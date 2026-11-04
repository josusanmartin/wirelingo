"""10BASE-T frames to and from the chip RTL in the KV260's fabric, with no cable:
eth_tap.FpgaBackend's two halves. Every line is a uio pad inside the FPGA; no
PMOD pin is enabled.

What the loaded bitstream offers, read from the RTL of the commit it was built
from (d1aa46f: fpga/kv260/kv260_top.v, pe_lab.v, pe_spim.v, unchanged since):

* pe_lab's pattern generator: 1024 entries of (value, delay) on the protoemu
  clock, driving the 8 uio pads protoemu doesn't drive (MODE 1). Not ui0, the
  stack's RXP: ui_in[0] comes from the PS GPIO bit or a PMOD pin only.
* No capture block and no logic analyser. pe_lab's SPI device keeps the last
  128 MOSI bits; the AXI GPIO reads uo_out when Linux asks. Neither can follow
  50 ns half cells.
* pe_spim: one SPI transaction at a time, at most 4096 words, quad at SCK = clk
  (8 clocks a word). While a burst runs, nothing else reaches the chip.

So one frame goes through in two phases, each with the cores loaded for it:

RX: the stack's own load (ethhost.load_writes: eth10_rx on core 0 with the line
    unit, eth10_tx_cs on core 1), with RXP = uio0 and TD+/TD- = uio3/uio4
    instead of ui0 and uo0/uo1. The generator plays the frame's Manchester
    edges into uio0, at clock-exact times, and one quad READ_FIXED burst drains
    core 0's RX FIFO. A blind burst reads an empty FIFO as 0, and a data word of
    24 zero bits is 0 too (ARP padding makes them). The burst reads every 8
    clocks and the line unit fills a word every 24 bits, so each data word has
    its place in the burst: restore_rx() puts the zero words back by that
    cadence. The record's bit count and the FCS check the result.
TX: eth10_tx_cs on core 1, as the stack loads it but behind a trigger, with the
    reply preloaded (its FIFOs hold 16 words: a 64-byte frame) and F_GO set;
    usb_sampler on core 0 reads uio2..uio4 every clock (uio2 is a marker the
    generator holds high, so no sample word is 0); the generator raises the
    trigger and one burst drains the samples. pe.ethsim's decoder, the model
    tests' far end, reads the frame off them.

The host in between is pe.eth.Station, the stack's own. pe.ethhost.Eth10Host
is not used: it services the FIFOs with passes, and here bursts do.

Limits, all from the above: a frame Linux sends must need at most 1024
generator entries (a 64-byte frame does; a 102-byte ping doesn't), and a reply
must be at most 64 bytes. Carrier sense is exercised only against a quiet line,
and RX and TX never overlap.

fabric_guard runs before anything maps the fabric (board_rig): it refuses unless
the app for this clock is the one active row. Nothing here loads an app.
"""
import math
import pathlib
import signal
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import fabric_guard  # noqa: E402
import pe_spim  # noqa: E402
import postfreeze_demo as pfd  # noqa: E402  (sets up sys.path for tools/)
import postfreeze2_demo as p2d  # noqa: E402
import usb_replay_demo as urd  # noqa: E402  (imported here: a first import inside a capture takes ms on the Kria)
from pe import asm, eth, ethhost, ethsim, host, sim  # noqa: E402

RXP = 0                        # uio0: the generator's line into the line unit
TD = 3                         # uio3 = TD+, uio4 = TD-
MARK = 2                       # uio2: held high by the generator from the trigger on
TD_POE = (1 << TD) | (1 << (TD + 1))
PG_DEPTH = 1024
BURST_MAX = 4096               # pe_spim's buffer
TX_MAX = 2 * ethhost.DEPTH * 4  # bytes the TX FIFOs hold (join_tx): 64
LEAD_S = 30e-6                 # generator start -> first edge or trigger; the burst starts within a few us
TAIL_S = 40e-6                 # burst time after the last thing it must see
READ_CLOCKS = 8                # one word of a quad burst at SCK = clk
TX_PHASES = 5                  # CLKDIV 1.25: the tick pattern repeats every 5 clocks
FW = HERE.parents[2] / "firmware"


class TooBig(ValueError):
    """The frame doesn't fit this method (generator entries or TX FIFOs)."""


class RxGap(RuntimeError):
    """The burst can't be read back into whole records."""


# ---------------------------------------------------------------- RX
def pg_schedule(raw, f_clk, lead_s=LEAD_S):
    """Generator entries [(clocks from the start, uio value)] for one frame,
    destination..FCS, as 802.3 puts it on the line: preamble and SFD, Manchester,
    TP_IDL, then idle low (pe.ethsim.line_edges), times rounded half up to the
    clock: at 50 MHz every bit is 5 clocks and every half cell 3 or 2 (a mid-bit
    edge 10 ns late, the same in every bit); at 40 MHz they are exact."""
    lead = round(lead_s * f_clk)
    out = []
    for t_ns, lv in ethsim.line_edges(ethsim.wire_bits(raw), 0.0):
        out.append((lead + math.floor(t_ns * f_clk / 1e9 + 0.5), lv << RXP))
    if len(out) > PG_DEPTH:
        raise TooBig(f"{len(raw)}-byte frame: {len(out)} edges, the generator holds {PG_DEPTH}")
    assert all(b[0] > a[0] for a, b in zip(out, out[1:]))
    return out


def restore_rx(burst, reads_per_word, tol=2.0):
    """eth10_rx's FIFO words, in order, from one blind burst (a word per read, 0
    when the FIFO was empty), with the data words that read as 0 put back.

    Per record, ending at its trailer (the only words with a non-zero low byte):
    * the word just before the trailer is the record's last word, 0 or not. The
      firmware pushes the trailer 4 instructions after it and reads are 8 clocks
      apart, so no empty read falls between them;
    * the full data words before it come one every reads_per_word reads (24 bits
      of line, one read per 8 clocks), starting from the first non-zero word,
      which is preamble. Each non-zero word must sit within tol reads of its
      place; the spacing is re-estimated from each word placed. Places with no
      word were 0.
    Raises RxGap when a record doesn't fit that, e.g. a burst that started after
    the first word or a burst that ended inside a frame."""
    out, seg, restored = [], [], 0
    for i, w in enumerate(burst):
        if not w:
            continue
        if not w & 0xFF:
            seg.append((i, w))
            continue
        count = w >> 16
        full = count // 24
        last = burst[i - 1] if i else 0
        data = [(j, v) for j, v in seg if j < i - 1]
        words = [0] * full
        if full:
            if not data:
                raise RxGap(f"a {count}-bit record with no data word before its last one")
            i0, r, top = data[0][0], float(reads_per_word), 0
            words[0] = data[0][1]
            for j, v in data[1:]:
                x = (j - i0) / r
                k = round(x)
                if not top < k < full or abs(j - i0 - k * r) > tol:
                    raise RxGap(f"a {count}-bit record: the word read at {j} is not on the "
                                f"{r:.2f}-read cadence from {i0} (place {x:.2f} of {full})")
                words[k], top = v, k
                r = (j - i0) / k
        elif data:
            raise RxGap(f"a {count}-bit record with {len(data)} words before its last one")
        restored += words.count(0)
        out += words + [last, w]
        seg = []
    if seg:
        raise RxGap("data words after the last trailer: the burst ended inside a record")
    return out, restored


def records(words):
    """(count, words) of every record in restored FIFO words (pe.eth.Records)."""
    rec, got = eth.Records(), []
    for w in words:
        r = rec.feed(w)
        if r is not None:
            got.append(r)
    if rec.malformed:
        raise RxGap(f"{rec.malformed} malformed record(s)")
    return got


# ---------------------------------------------------------------- TX
def samples(words):
    """usb_sampler words -> 3-bit samples, one per clock (uio2 bit 0, uio3 bit 1,
    uio4 bit 2). Zero words are empty reads: after the trigger uio2 is high."""
    return [(w >> (3 * (9 - i))) & 7 for w in words if w for i in range(10)]


def td_bursts(smp, clk_ns):
    """(bursts in clocks, both-high samples). A burst is pe.ethsim.Burst's: (sample,
    level) edges, level +1 for TD+ high, -1 for TD- high, 0 idle; it closes after
    QUIET_NS of idle. The last burst may still be open (None at its end)."""
    out, cur, lv, last, both = [], None, 0, -1, 0
    quiet = ethsim.QUIET_NS / clk_ns
    for i, s in enumerate(smp):
        v = (s >> 1) & 3
        both += v == 3
        x = (0, 1, -1, 0)[v]
        if x != lv:
            lv, last = x, i
            if cur is None:
                cur = [(i, x)]
            else:
                cur.append((i, x))
        elif cur is not None and not x and (i - last) >= quiet:
            out.append(cur)
            cur = None
    if cur is not None:
        out.append(cur + [None])
    return out, both


def decode_td(smp, clk_ns):
    """Frames and facts off TD+/TD- sampled every clock. The frames are pe.ethsim's
    decode (frame_from_bits(manchester_decode)), with or without a good FCS."""
    bursts, both = td_bursts(smp, clk_ns)
    info = dict(both_high=both, pulses=[], frames=[], open=False)
    frames = []
    for b in bursts:
        if b[-1] is None:
            info["open"] = True
            continue
        edges = [(i * clk_ns, x) for i, x in b]
        if edges[-1][0] - edges[0][0] <= ethsim.PULSE_MAX_NS:
            info["pulses"].append((b[0][0], b[-1][0] - b[0][0]))
            continue
        d = ethsim.manchester_decode(edges)
        f = ethsim.frame_from_bits(d[0]) if d else None
        runs = sorted({b2[0] - b1[0] for b1, b2 in zip(b, b[1:-1]) if b1[1]})
        info["frames"].append(dict(start=b[0][0], end=b[-1][0], tp_idl_ns=round(d[1]) if d else None,
                                   driven_runs=runs, decoded=f is not None, edges=b))
        frames.append(f)
    return frames, info


# ---------------------------------------------------------------- the two phases
class FabricEth:
    """One frame in, one frame out, on a rig: postfreeze_demo.HwRig (the board,
    through board_rig) or ModelRig (the cycle model, pe_lab modelled)."""

    def __init__(self, rig, clock_hz=50e6):
        self.rig, self.clock_hz = rig, clock_hz
        self.lead_s = LEAD_S
        self.clkdiv, self.period = ethhost.clock_settings(clock_hz)
        if rig.f_clk != clock_hz:
            raise RuntimeError(f"the rig runs at {rig.f_clk / 1e6:.4f} MHz, not {clock_hz / 1e6:g}")
        self.clk_ns = 1e9 / clock_hz
        self.reads_per_word = 24 * self.period / READ_CLOCKS
        self.tx_prog = asm.assemble((FW / "eth10_tx_cs.pasm").read_text(), {"F_GO": ethhost.F_GO, "RXP": RXP})
        self.sampler = asm.assemble((HERE / "usb_sampler.pasm").read_text(), {"TRIG": MARK})

    def _words_for(self, t_clocks):
        n = math.ceil((t_clocks + TAIL_S * self.clock_hz) / READ_CLOCKS)
        if n > BURST_MAX:
            raise TooBig(f"a {n}-word burst; pe_spim holds {BURST_MAX}")
        return n

    def receive(self, raw):
        """Play raw (destination..FCS) into RXP; returns (the frame eth10_rx
        recorded, destination..FCS, FCS unchecked, or None; facts)."""
        rig = self.rig
        sched = pg_schedule(raw, self.clock_hz, self.lead_s)
        n = self._words_for(sched[-1][0])
        t = time.monotonic()
        pfd.setup(rig, mode=pfd.PG, poe=TD_POE, pg_init=0)
        for a, w in host.coalesce(ethhost.load_writes(self.clock_hz, True, td_pin=TD, rxp=RXP)):
            rig.write(a, w)
        rig.pg_load(sched, init=0)
        burst = urd.go_burst(rig, ethhost.RX_CORE, n, 1, True)
        rest = p2d.rd(rig, pfd.FIFO(0), p2d.rxn(rig.read(pfd.STATUS(0))))
        linestat = rig.read(sim.A_LINESTAT)
        busy = rig.pg_busy()
        st = pfd.finish(rig)
        rig.write(sim.A_LINECFG, 0)
        info = dict(entries=len(sched), burst_words=n, status=st, wall_ms=round((time.monotonic() - t) * 1e3, 1))
        if busy:
            raise RxGap("the generator was still playing when the burst ended")
        if linestat & ethhost.LS_RX_OVERFLOW:
            raise RxGap("eth10_rx overflowed (LINESTAT)")
        words, info["zero_words_restored"] = restore_rx(burst + rest, self.reads_per_word)
        info["fifo_words"] = words
        recs = records(words)
        info["records"] = [c for c, _ in recs]
        frames = [eth.decode_record(c, w) for c, w in recs if c > ethhost.PULSE_BITS]
        if len(frames) != 1:
            info["why"] = f"{len(frames)} frame records"
            return None, info
        return frames[0], info

    def transmit(self, raw, phase=0):
        """eth10_tx_cs sends raw (destination..FCS) on TD; returns (the frame read
        off TD, destination..FCS, or None; facts).

        Core 1 runs at CLKDIV 1.25, 4 ticks in 5 clocks, so where its edges fall
        after the trigger depends on its divider's phase then, which the host's
        timing since core 1's restart sets: on the board, any of 5. On the model,
        phase = k (0..4) adds k clocks there, so a run per phase gives them all."""
        if not eth.MIN_FRAME <= len(raw) <= TX_MAX:
            raise TooBig(f"a {len(raw)}-byte reply; the TX FIFOs hold {TX_MAX} bytes")
        rig = self.rig
        lead = round(self.lead_s * self.clock_hz)
        # pulse, 32 ticks, the 96-bit window, the frame and TP_IDL, in clocks
        n = self._words_for(lead + (self.period * (8 + 1 + 96 + 8 * (8 + len(raw)) + 4)))
        t = time.monotonic()
        tx_core, base = ethhost.TX_CORE, 256 * ethhost.TX_CORE
        pfd.setup(rig, mode=pfd.PG, owners={TD: tx_core, TD + 1: tx_core}, poe=TD_POE, pg_init=0)
        pfd.load(rig, tx_core, self.tx_prog, side_pins=TD, clkdiv=self.clkdiv)
        rig.write(base + pfd.PRE, pfd.trigger_preamble(base))
        rig.write(sim.core_reg(tx_core, sim.C_ENTRY), base + pfd.PRE)
        pfd.load(rig, ethhost.RX_CORE, self.sampler, in_pins=MARK, clkdiv=1 << 8)
        rig.write(sim.core_reg(tx_core, sim.C_R0), 8 * len(raw) - 1)
        p2d.ctrl(rig, 1 << tx_core)                     # waits at its trigger
        w = [int.from_bytes(raw[i:i + 4].ljust(4, b"\0"), "little") for i in range(0, len(raw), 4)]
        d = ethhost.DEPTH
        rig.write(pfd.FIFO(tx_core), w[:d], fixed=True)
        for _ in range(100):                            # join_tx moves them to the second stage
            if not rig.read(pfd.STATUS(tx_core)) & 15:
                break
            rig.wait(1e-6)
        if w[d:]:
            rig.write(pfd.FIFO(tx_core), w[d:], fixed=True)
        st = rig.read(pfd.STATUS(tx_core))
        loaded = (st & 15) + ((st >> 4) & 15)
        rig.write(sim.A_FSET, 1 << ethhost.F_GO)
        rig.write(sim.A_CTRL, 0b11 | (1 << (8 + ethhost.RX_CORE)))   # the sampler restarted; core 1 waits on
        if rig.model:
            rig.wait((round(1e-6 * self.clock_hz) + phase + 0.5) / self.clock_hz)    # whole clocks
        rig.pg_load([(lead, (1 << MARK) | (1 << pfd.TRIG))], init=0)
        words = urd.go_burst(rig, ethhost.RX_CORE, n, 1, True)
        fset = rig.read(sim.A_FSET)
        st = pfd.finish(rig)
        smp = samples(words)
        frames, info = decode_td(smp, self.clk_ns)
        info.update(loaded_words=loaded, burst_words=n, samples=len(smp), status=st,
                    f_go_cleared=not fset & (1 << ethhost.F_GO), wall_ms=round((time.monotonic() - t) * 1e3, 1))
        if loaded != len(w):
            info["why"] = f"{loaded} of {len(w)} words in the TX FIFOs"
            return None, info
        if len(frames) != 1 or frames[0] is None:
            info["why"] = f"{len(frames)} frame bursts, decoded {[f is not None for f in frames]}"
            return None, info
        if info["both_high"] or not info["f_go_cleared"] or info["open"]:
            info["why"] = "TD+ and TD- both high, F_GO still set or a burst still open"
            return None, info
        return frames[0], info

    def close(self):
        self.rig.close()


def board_parts():
    """pe_kria.Board, pe_spim.Spim, pe_lab.Lab on /dev/mem. Building them reads
    VERSION and MAGIC and drives the GPIO path's idle levels; it moves no select."""
    from pe_kria import Board
    from pe_lab import Lab
    from pe_spim import Spim
    return Board(), Spim(), Lab()


def give_back(board, spim, lab):
    """What HwRig.close does, for a rig that may be half built: quad off while the
    host port is still on the fabric master, PMOD pins disabled, pe_lab in GPIO
    mode, the host port back on the GPIO path. Every step is tried; no signal can
    cut it short (they are ignored meanwhile). The first error is raised after."""
    errors = []

    def step(fn):
        try:
            fn()
        except Exception as e:                   # noqa: BLE001 - every step must still run
            errors.append(e)

    def quad_off():
        if spim.mem.rd(pe_spim.SEL) & 1:         # only the fabric master can reach HOSTCFG
            spim.quad = False
            spim.write(0x80B, 0)                 # HOSTCFG: QUAD_EN off, a 1-bit write

    def steps():
        step(quad_off)
        step(lambda: lab.mode(pfd.GPIO))
        step(lambda: board.set_inputs(uio=0xFF))
        step(lambda: spim.pmod({}))
        step(lambda: spim.select(fabric=False))
    shielded(steps)
    if errors:
        raise errors[0]


def shielded(fn):
    """fn() with SIGINT, SIGTERM and SIGHUP ignored, so a second signal can't cut a
    give-back short; the handlers are put back after."""
    names = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
    old = [signal.signal(n, signal.SIG_IGN) for n in names]
    try:
        return fn()
    finally:
        for n, h in zip(names, old):
            signal.signal(n, h)


def board_rig(clock_hz, parts=None):
    """The KV260, after fabric_guard: the app for this clock must be the one active
    row, or nothing maps the fabric. Never loads an app. What v2_demo.Hw and HwRig do
    to the board while they are built (host port to the fabric master, ID read,
    quad on, 2 s of clock measurement) is given back if that ends in any way but a
    rig: an exception, Ctrl-C, or a signal eth_tap.run_closing turned into one."""
    if clock_hz not in (40e6, 50e6):
        raise ValueError("10BASE-T needs 40 or 50 MHz")
    fabric_guard.require_app(expect=fabric_guard.APP_FOR_MHZ[round(clock_hz / 1e6)])
    from v2_demo import Hw
    board, spim, lab = (parts or board_parts)()
    try:
        board.reset()                            # what Hw() does when it builds its own board
        rig = pfd.HwRig(hw=Hw(board=board, spim=spim, lab=lab))
        if rig.f_clk != clock_hz:
            raise RuntimeError(f"the board's clock is {rig.f_meas / 1e6:.5f} MHz, not {clock_hz / 1e6:g}")
    except BaseException:
        try:
            give_back(board, spim, lab)
        except Exception as e:                   # noqa: BLE001 - the first failure is the one to report
            print(f"giving the board back failed too: {e!r}", file=sys.stderr)
        raise
    return rig
