#!/usr/bin/env python3
"""The post-freeze protocol firmwares (docs/post-freeze-protocols.md) on the
KV260 build of the chip RTL: run as root on the Kria with the protoemu app
(pe_lab) loaded. Nothing leaves the FPGA: every line is a virtual pin
(P24..P31, docs/isa.md §2) or a uio pad that pe_lab's fabric drives; no PMOD
pin is enabled.

    sudo python3 -u fpga/kv260/host/postfreeze_demo.py [dmx i2s dshot nec wiegand lin bidir] [--save pf.json]
    python3 fpga/kv260/host/postfreeze_demo.py --model [protocols]     # the cycle model, no hardware
    python3 fpga/kv260/host/postfreeze_demo.py sigrok pf.json          # off the board: sigrok on the captures

Transmitters: core 0 runs the firmware with its pins on virtual pins, core 1
runs firmware/la_capture on the same virtual pins (1 tick = 1 clock unless
stated), and the host drains the records with one long READ_FIXED burst of
4096 words over the fabric SPI master, its SCK divider chosen so that the
burst outlasts the transmission and drains faster than the edges come
(quad at SCK = clk for I2S). The transmitter waits behind a 2-word preamble
(`wait 1 pin 7; jmp <firmware>`, at word 254 of its bank) until pe_lab's
pattern generator raises uio7, after the burst has started, so the first
edge is already being drained; a capture with a gap record fails. The
pins-only decoders of pe.postfreeze judge the edges.

Receivers: core to core where the pair speaks the same line (Wiegand on two
virtual pins; LIN on pe_lab's I2C-bus mode, uio0 and uio2 wired-AND with a
pull-up, both nodes open drain; bidirectional DShot against esc_emu.pasm on
the same wired-AND line), else pe_lab's pattern generator plays a pins-only
model's waveform into the receiver at clock-exact times (NEC's demodulated
envelope, Wiegand card-reader timings, a LIN commander at +-10 %).

--model runs the same code on the cycle model (tools/pe/sim.py) with pe_lab's
modes modelled, at a slower clock for the long protocols (the firmware
counts ticks of its divider, so the waveforms in ticks are the same): it
checks this script, not the FPGA. --save keeps every capture (edges in ns);
`sigrok` decodes them with sigrok-cli's decoders, as test_post_freeze.py
does with the RTL's pads.
"""
import argparse
import bisect
import json
import math
import pathlib
import random
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
ROOT = HERE.parents[2]
for sub in ("tools", "tools/micropython"):
    sys.path.insert(0, str(ROOT / sub))

from pe import asm, host, la, sim  # noqa: E402
from pe import postfreeze as pf  # noqa: E402

FW = ROOT / "firmware"
FIFO = lambda c: sim.core_reg(c, sim.C_FIFO)
STATUS = lambda c: sim.core_reg(c, sim.C_STATUS)
GPIO, PG, SPI, I2C = 0, 1, 2, 3          # pe_lab MODE
TRIG = 7                                 # uio7: the pattern generator's start trigger
PRE = 254                                # trigger preamble, in core 0's bank
V0 = 24                                  # first virtual pin
HALTED, FAULT = 1 << 13, 1 << 14


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)


def fw(name, **defines):
    path = FW / f"{name}.pasm"
    if not path.exists():
        path = HERE / f"{name}.pasm"
    return asm.assemble(path.read_text(), defines)


# ================================================================ rigs

class HwRig:
    """The board: v2_demo.Hw (GPIO path for reset, pe_spim at SCK = clk for
    the host port, pe_lab), plus quad enabled for the I2S burst."""

    model = False

    def __init__(self):
        from v2_demo import Hw
        self.hw = Hw()
        self.pe, self.lab, self.spim, self.board = self.hw.pe, self.hw.lab, self.hw.spim, self.hw.board
        self.spim.enable_quad()
        self.spim.quad = False
        self.ident = self.lab.identity()
        self.f_nominal = 1200e6 / self.ident["clk_div"]
        self.f_meas = self.board.clk_hz(2.0)
        # The firmware's defines and CLKDIVs are derived from this. The cycle
        # counter measures against Linux's clock over 2 s (about 1e-5 of
        # Python timing noise); the MMCM's output is 1200 MHz / CLK_DIV from
        # the PS's 100 MHz, so within 200 ppm the nominal value is used.
        ok = abs(self.f_meas / self.f_nominal - 1) < 2e-4
        self.f_clk = self.f_nominal if ok else self.f_meas

    def model_clock(self, f):
        pass

    def write(self, addr, words, fixed=False):
        self.pe.write(addr, words, fixed=fixed)

    def read(self, addr, n=1, fixed=False):
        return self.pe.read(addr, n, fixed=fixed)

    def lab_mode(self, m):
        self.lab.mode(m)

    def pg_set(self, v):
        self.lab.pg_set(v, alt=False)

    def pg_load(self, sched, init):
        self.lab.pg_load(sched, init=init, alt=False)

    def pg_start(self):
        self.lab.pg_start()

    def pg_busy(self):
        return self.lab.pg_busy()

    def gpio_uio(self, v):
        self.board.set_inputs(uio=v)

    def spid_arm(self, resp, delay):
        self.lab.spid_arm(resp, delay)

    def spid_status(self):
        return self.lab.spid_status()

    def spid_rx(self):
        return self.lab.spid_rx()

    def burst(self, core, n, div, quad):
        s = self.spim
        s.div, s.quad = div, quad
        try:
            words = s.read(FIFO(core), n, fixed=True)
        finally:
            s.div, s.quad = 1, False
        return [words] if n == 1 else words

    def wait(self, sec):
        time.sleep(sec)

    def close(self):
        """Cores stopped, pins and pe_lab back to their load-time state, every
        PMOD pin disabled, host-port pins back on the GPIO path."""
        self.write(sim.A_CTRL, 0b11 << 16)
        for a in (sim.A_OD, sim.A_POUT, sim.A_POE, sim.A_SYNCBYP):
            self.write(a, 0)
        self.pe.set_pin_owners({})
        self.lab.pg_wait()
        self.lab.mode(GPIO)
        self.board.set_inputs(uio=0xFF)
        self.spim.write(0x80B, 0)               # HOSTCFG: quad off
        self.spim.pmod({})
        self.spim.select(fabric=False)


class FabricModel:
    """pe.modelhost.ModelHost with kv260_top's uio pads: uio_in = uio_oe ?
    uio_out : what pe_lab's MODE drives (GPIO value, pattern generator, SPI
    device's MISO on uio4, I2C bus of uio0/2 and uio1/3)."""

    def __new__(cls, *a, **kw):
        from pe.modelhost import ModelHost

        class _M(ModelHost):
            def __init__(self):
                super().__init__()
                self.mode, self.gpio, self.pg_init, self.pg = GPIO, 0xFF, 0, None
                self.spid = None

            def pg_value(self):
                if self.pg is None:
                    return self.pg_init
                t0, ts, vs = self.pg
                i = bisect.bisect_right(ts, self.chip.cycle - t0) - 1
                return vs[i] if i >= 0 else self.pg_init

            def spid_arm(self, resp, delay):
                """pe_lab's SPI device (MODE 2), modelled at protoemu clock
                edges: arm puts bit 127 of RESP on MISO; after each SCK fall
                the next bit reaches the pads (DELAY + 2) x 2.5 ns after the
                clock edge that dropped SCK, i.e. at the first protoemu edge
                more than that later; MOSI is taken with each SCK rise."""
                r = int.from_bytes(bytes(resp).ljust(16, b"\0"), "big")
                self.spid = dict(sh=r, miso=(r >> 127) & 1, delay=delay & 31, rises=0, rx=0,
                                 pmin=0xFF, pmax=0, seen=False, last=None, pend=[],
                                 sck=(self.chip.outputs()[1] >> 2) & 1)

            def spid_status(self):
                d = self.spid
                return d["rises"] & 0xFFFF, d["pmin"], d["pmax"]

            def spid_rx(self):
                return self.spid["rx"].to_bytes(16, "big")

            def spid_step(self, out):
                d = self.spid
                if d is None:
                    return 1
                cyc = self.chip.cycle          # the edge that ends this step
                sck, mosi = (out >> 2) & 1, (out >> 3) & 1
                if sck != d["sck"]:            # changed at the previous edge (cyc - 1)
                    if not sck:
                        d["pend"].append(cyc + (d["delay"] + 2) // 8)
                    else:
                        d["rx"] = ((d["rx"] << 1) | mosi) & ((1 << 128) - 1)
                        d["rises"] += 1
                        if d["seen"]:
                            iv = min(8 * (cyc - d["last"]) - 1, 255)   # per saturates at 0xFF
                            p1 = (iv + 1) & 0xFF                       # 8-bit per + 1
                            if p1 < d["pmin"]:
                                d["pmin"] = p1
                            if p1 > d["pmax"]:
                                d["pmax"] = p1
                        d["seen"], d["last"] = True, cyc
                    d["sck"] = sck
                while d["pend"] and d["pend"][0] <= cyc:
                    d["pend"].pop(0)
                    d["sh"] = ((d["sh"] << 1) | (d["sh"] >> 127)) & ((1 << 128) - 1)
                    d["miso"] = (d["sh"] >> 127) & 1
                return d["miso"]

            def bus_uio(self):
                _, out, oe = self.chip.outputs()
                if self.mode == PG:
                    ext = self.pg_value()
                elif self.mode == SPI:
                    ext = (self.gpio & ~0x10) | (self.spid_step(out) << 4)
                elif self.mode == I2C:
                    d = (~oe | out) & 0xFF
                    sda, scl = (d >> 0) & (d >> 2) & 1, (d >> 1) & (d >> 3) & 1
                    ext = (self.gpio & 0xF0) | (scl << 3) | (sda << 2) | (scl << 1) | sda
                else:
                    ext = self.gpio
                return ((oe & out) | (~oe & ext)) & 0xFF
        return _M()


class ModelRig:
    model = True

    def __init__(self, f_clk=50e6):
        self.f_nominal = self.f_meas = 50e6
        self.ident = {"rtl_rev": "model", "clk_div": 24, "build_utc": "-", "digest": "-", "dirty": False}
        self.model_clock(f_clk)

    def model_clock(self, f):
        self.f_clk = f
        self.mh = FabricModel()
        self.pg_sched = None

    def write(self, addr, words, fixed=False):
        if isinstance(words, int):
            words = [words]
        for i, w in enumerate(words):
            self.mh.write(addr if fixed else addr + i, w)

    def read(self, addr, n=1, fixed=False):
        out = [self.mh.read(addr if fixed else addr + i, pop=True) for i in range(n)]
        return out[0] if n == 1 else out

    def lab_mode(self, m):
        self.mh.mode = m

    def pg_set(self, v):
        self.mh.pg_init, self.mh.pg = v, None

    def pg_load(self, sched, init):
        self.pg_sched = ([t for t, _ in sched], [v for _, v in sched])
        self.mh.pg_init, self.mh.pg = init, None

    def pg_start(self):
        ts, vs = self.pg_sched
        self.mh.pg = (self.mh.chip.cycle, ts, vs)

    def pg_busy(self):
        return self.mh.pg is not None and self.mh.chip.cycle - self.mh.pg[0] <= self.mh.pg[1][-1]

    def gpio_uio(self, v):
        self.mh.gpio = v & 0xFF

    def spid_arm(self, resp, delay):
        self.mh.spid_arm(resp, delay)

    def spid_status(self):
        return self.mh.spid_status()

    def spid_rx(self):
        return self.mh.spid_rx()

    def burst(self, core, n, div, quad):
        per = (8 if quad else 32) * div
        words = []
        for _ in range(n):
            self.mh.step(per - 3)
            words.append(self.mh.read(FIFO(core), pop=True))
        return words

    def wait(self, sec):
        self.mh.step(max(1, int(sec * self.f_clk)))

    def close(self):
        pass


# ================================================================ helpers

def model_clock(f):
    """Clock for a run on the cycle model (--model); the board's is fixed."""
    def deco(fn):
        fn.model_f = f
        return fn
    return deco


def load(rig, core, prog, **cfg):
    for addr, words in host.coalesce(host.load_program(core, prog, core * 256, **cfg)):
        rig.write(addr, words)


def setup(rig, *, mode=GPIO, owners=None, od=0, pout=0, poe=0, pg_init=0, gpio=0xFF):
    """Both cores stopped with their FIFOs empty, pin registers set, pe_lab mode."""
    rig.write(sim.A_CTRL, 0b11 << 16)
    rig.gpio_uio(gpio)
    rig.lab_mode(mode)
    if mode == PG:
        rig.pg_set(pg_init)
    for addr, words in host.coalesce(host.pin_owner_writes(owners or {})):
        rig.write(addr, words)
    rig.write(sim.A_OD, od)
    rig.write(sim.A_POUT, pout)
    rig.write(sim.A_POE, poe)
    rig.write(sim.A_SYNCBYP, 0)


def finish(rig):
    """Stop both cores; report their STATUS words (a fault fails the run)."""
    st = [rig.read(STATUS(c)) for c in (0, 1)]
    rig.write(sim.A_CTRL, 0)
    rig.lab_mode(GPIO)
    rig.gpio_uio(0xFF)
    for a in (sim.A_OD, sim.A_POUT, sim.A_POE):
        rig.write(a, 0)
    check(not any(s & FAULT for s in st), f"a core faulted: STATUS {[hex(s) for s in st]}")
    return st


def trigger_preamble(base):
    p = asm.assemble(f".program trig\nx: wait 1 pin {TRIG}\njmp x")
    return [p.words[0], (p.words[1] & ~0x3FF) | base]


def burst_plan(rig, dur_s, quad=False, n=4096):
    """SCK divider for an n-word READ_FIXED lasting at least dur_s."""
    per = 8 if quad else 32
    div = max(1, math.ceil(dur_s * rig.f_clk / (n * per)))
    check(div <= 255, f"a {dur_s * 1e3:.0f} ms burst needs SCK divider {div} > 255")
    return n, div, quad


class Capture:
    """la_capture records -> edges in ns (t = 0 at the start sample)."""

    def __init__(self, words, mask, tick_ns):
        self.words, self.mask, self.tick_ns = words, mask, tick_ns
        self.cap = la.decode_records(words, mask)
        self.t_end = self.cap.end * tick_ns

    def edges(self, ch):
        return [(t * self.tick_ns, v) for t, v in la.channel_edges(self.cap, ch)]

    def log(self):
        return [(t * self.tick_ns, s) for t, s in self.cap.edges]

    def records(self):
        """Every change record's time (ticks) and state, pulses included."""
        out, a = [], None
        for w in self.words:
            if not w:
                continue
            m, s, c = w >> 24, (w >> 16) & self.mask, w & 0xFFFF
            if m == la.M_FIRST:
                a = la.START
            elif m == la.M_CHANGE:
                t = a + 0xFFFF - c
                out.append((t, s, c))
                a = t + la.LOOP
        return out


def capture(rig, tx_prog, tx_cfg, *, mask, prefill=(), extra=(), owners=None, pout=0, od=0,
            trig_s=1e-3, burst, la_div=1, in_base=V0, tx_core=0, la_core=1, pg_idle=0, trigger=True):
    """tx_prog on tx_core (behind the trigger preamble), la_capture on la_core,
    one READ_FIXED burst draining it. Returns a Capture; fails on a gap."""
    setup(rig, mode=PG, owners=owners, od=od, pout=pout, pg_init=pg_idle)
    load(rig, tx_core, tx_prog, **tx_cfg)
    if trigger:
        base = tx_core * 256
        rig.write(base + PRE, trigger_preamble(base))
        rig.write(sim.core_reg(tx_core, sim.C_ENTRY), base + PRE)
    for addr, v in extra:
        rig.write(addr, v)
    load(rig, la_core, fw("la_capture", MASK=mask), in_pins=in_base, clkdiv=la_div << 8)
    prefill = list(prefill)
    check(len(prefill) <= 8, "at most 8 TX FIFO words before the start")
    if prefill:
        rig.write(FIFO(tx_core), prefill, fixed=True)
    rig.pg_load([(max(1, round(trig_s * rig.f_clk)), pg_idle | (1 << TRIG))], init=pg_idle)
    rig.write(sim.A_CTRL, 0b11 | (0b11 << 8))
    rig.pg_start()
    words = rig.burst(la_core, *burst)
    left = (rig.read(STATUS(la_core)) >> 4) & 15
    if left:
        w = rig.read(FIFO(la_core), left, fixed=True)
        words += [w] if left == 1 else w
    tx_st = rig.read(STATUS(tx_core))
    tx_left, tx_rx = tx_st & 15, (tx_st >> 4) & 15
    tx_recs = []
    if tx_rx:
        w = rig.read(FIFO(tx_core), tx_rx, fixed=True)
        tx_recs = [w] if tx_rx == 1 else w
    st = finish(rig)
    c = Capture(words, mask, la_div * 1e9 / rig.f_clk)
    nrec = sum(1 for w in words if w)
    check(c.cap.exact, f"the capture has {len(c.cap.gaps)} gap(s): RXF filled while the host was not draining")
    check(tx_left == 0, f"the transmitter left {tx_left} TX FIFO words: the burst ended too soon?")
    c.info = {"records": nrec, "burst": list(burst), "status": st, "tx_records": tx_recs}
    return c


def runs_of(edges, t_end):
    return pf.runs(edges, t_end)


def clk(rig, ns):
    return ns * rig.f_clk / 1e9


def rng_bytes(rng, n):
    return bytes(rng.getrandbits(8) for _ in range(n))


def save_run(store, name, rig, **data):
    if store is not None:
        store[name] = dict(f_clk=rig.f_clk, **data)


def pg_from_levels(rig, seqs, other=0, bit=0, t0_s=1e-3):
    """Pattern-generator entries from [(level, duration_ns)] sequences played
    one after another on uio[bit] (other uio bits = `other`)."""
    out, t = [], round(t0_s * rig.f_clk)
    for seq in seqs:
        for lvl, d in seq:
            v = (other & ~(1 << bit)) | (lvl << bit)
            if not out or out[-1][1] != v:
                out.append((t, v))
            t += max(1, round(clk(rig, d)))
    return split_long(out, t)


def split_long(sched, t_end):
    """Insert repeats so that no step exceeds the generator's 24-bit delay."""
    out = []
    for i, (t, v) in enumerate(sched):
        out.append((t, v))
        nxt = sched[i + 1][0] if i + 1 < len(sched) else t_end
        while nxt - out[-1][0] >= (1 << 24) - 1:
            out.append((out[-1][0] + (1 << 23), v))
    if out[-1][0] < t_end:
        out.append((t_end, out[-1][1]))
    return out


def poll_records(rig, cores, until, step_s=0.002, timeout_s=5.0, after_s=0.0):
    """Drain the cores' RX FIFOs (STATUS, then that many words: records may
    be 0) until until(recs) is true (then `after_s` more), or timeout."""
    recs = {c: [] for c in cores}
    t_model = 0.0
    t0 = time.monotonic()
    done_at = None
    while True:
        for c in cores:
            n = (rig.read(STATUS(c)) >> 4) & 15
            if n:
                w = rig.read(FIFO(c), n, fixed=True)
                recs[c] += [w] if n == 1 else w
        if done_at is None and until(recs):
            done_at = t_model if rig.model else time.monotonic()
        now = t_model if rig.model else time.monotonic()
        if done_at is not None and now - done_at >= after_s:
            return recs
        check((t_model if rig.model else now - t0) < timeout_s, f"no end within {timeout_s} s: {recs}")
        rig.wait(step_s)
        t_model += step_s


# ================================================================ DMX512

def dmx_run(rig, store, name, mtbs, packets):
    f = rig.f_clk
    prog = fw("dmx512_tx", TX=V0, MTBS=mtbs)
    words = sum((pf.dmx_words(p) for p in packets), [])
    c = capture(rig, prog, dict(out_pins=(V0, 1), clkdiv=host.clkdiv_for(f, 1e6)), mask=1, prefill=words,
                owners={V0: 0}, pout=1 << V0, trig_s=1e-3, burst=burst_plan(rig, 1e-3 + 1.4e-3 * len(packets) + 1e-3))
    e = c.edges(0)
    save_run(store, name, rig, edges={"dmx": e}, t_end=c.t_end, packets=[p.hex() for p in packets], mtbs=mtbs)
    got = pf.DmxReceiver().decode(e, c.t_end)
    half = 0.5e9 / f
    check([bytes(p["slots"]) for p in got] == list(packets),
          f"slots {[bytes(p['slots']).hex() for p in got]} != sent {[p.hex() for p in packets]}")
    check(not any(p["errors"] for p in got), f"framing errors {[p['errors'] for p in got]}")
    brk, mab = [p["brk"] for p in got], [p["mab"] for p in got]
    grid = max(abs(x) for p in got for x in p["grid_err"])
    gaps = sorted({round(g) for p in got for g in p["mark_gaps"]})
    b2b = [p["break_to_break"] for p in got[:-1]]
    want_b2b = [max(1204e3, (194 + (44 + mtbs) * len(p)) * 1e3) for p in packets[:-1]]
    check(all(abs(b - 176e3) < half for b in brk) and all(abs(m - 12e3) < half for m in mab),
          f"BREAK {brk} / MAB {mab} ns, want 176 / 12 us")
    check(grid < half, f"a bit edge is {grid:.0f} ns off the 4 us grid")
    check(all(abs(g - mtbs * 1e3) < half for p in got for g in p["mark_gaps"]),
          f"mark between slots {gaps} ns, want {mtbs} us")
    check(all(abs(b - w) < half for b, w in zip(b2b, want_b2b)), f"BREAK to BREAK {b2b} ns, want {want_b2b}")
    return [f"{len(packets)} packets of {[len(p) for p in packets]} slots, MTBS {mtbs} us: all slots received; "
            f"{c.info['records']} records, burst {c.info['burst'][0]} words at SCK = clk/{c.info['burst'][1]}",
            f"BREAK {sorted({b / 1e3 for b in brk})} us, MAB {sorted({m / 1e3 for m in mab})} us, every bit edge "
            f"within {grid:.1f} ns of the 4 us grid, mark between slots {sorted({g / 1e3 for g in gaps})} us",
            f"BREAK to BREAK {[b / 1e3 for b in b2b]} us (E1.11 TX: >= 1204; firmware: max(1204, 194 + "
            f"{44 + mtbs} n))"]


@model_clock(50e6)
def dmx_short(rig, store):
    """dmx512_tx, MTBS 0: three short packets, padded to 1204 us."""
    return dmx_run(rig, store, "dmx_short", 0,
                   [bytes([0, 1, 2, 4, 8, 16, 32, 64, 128]), bytes([0, 0x99]), bytes([0])])


@model_clock(50e6)
def dmx_mtbs(rig, store):
    """dmx512_tx, MTBS 8 us: a 20-slot packet (unpadded) and a 1-slot one."""
    rng = random.Random(7)
    return dmx_run(rig, store, "dmx_mtbs", 8, [bytes([0]) + rng_bytes(rng, 19), bytes([0x55])])


# ================================================================ I2S

@model_clock(50e6)
def i2s(rig, store):
    """i2s_tx at 48 kHz: a silent frame, 7 stereo frames, then underrun silence."""
    f = rig.f_clk
    BCLK, WS, SD = V0, V0 + 1, V0 + 2
    cd = host.clkdiv_for(f, 64 * 48000)
    rng = random.Random(2)
    frames = [(rng.randrange(-32768, 32768), rng.randrange(-32768, 32768)) for _ in range(7)]
    # a silent frame first: a receiver can't use the first word (its start was not framed by WS)
    c = capture(rig, fw("i2s_tx"), dict(out_pins=(SD, 1), side_pins=BCLK, clkdiv=cd), mask=0x07,
                prefill=[0] + [pf.i2s_word(l, r) for l, r in frames], owners={BCLK: 0, WS: 0, SD: 0},
                trig_s=150e-6, burst=(4096, 1, True))
    log = c.log()
    save_run(store, "i2s", rig, edges={"bclk": c.edges(0), "ws": c.edges(1), "sd": c.edges(2)}, t_end=c.t_end,
             frames=frames)
    words, rises = pf.I2sReceiver().decode(log)
    vals = [(ch, v) for ch, v, _, _ in words]
    want = sum([[("L", l & 0xFFFF), ("R", r & 0xFFFF)] for l, r in frames], [])
    check(want[0] in vals, f"first sample not found in {vals[:6]}")
    i = vals.index(want[0])
    silent = vals[i + len(want):]
    check(vals[i:i + len(want)] == want, f"samples {vals[i:i + len(want)]} != {want}")
    check(len(silent) >= 8 and all(v == 0 for _, v in silent), f"after the data: {silent[:8]}")
    ck = 1e9 / f
    per = sorted({round((b - a) / ck) for a, b in zip(rises, rises[1:])})
    ws_fall = [t for t, v in c.edges(1)[1:] if v == 0]
    fp = [b - a for a, b in zip(ws_fall, ws_fall[1:])]
    fpc = sorted({round(x / ck) for x in fp})
    fs = 1e9 / (sum(fp) / len(fp))
    lo, hi = math.floor(2 * (cd / 256)), math.ceil(2 * (cd / 256))
    check(set(per) <= {lo, hi}, f"BCLK periods {per} clocks, want {lo}/{hi}")
    check(set(fpc) <= {math.floor(64 * cd / 256), math.ceil(64 * cd / 256)}, f"frame periods {fpc} clocks")
    return [f"{len(want)} samples exact, then {len(silent)} silent words with BCLK and WS running; "
            f"{c.info['records']} records, quad burst at SCK = clk",
            f"CLKDIV {cd >> 8}+{cd & 255}/256: BCLK period {per} clocks, frame (WS) {fpc} clocks, "
            f"fs {fs:.1f} Hz ({(fs / 48000 - 1) * 100:+.4f} % of 48 kHz) over {len(fp)} frames"]


# ================================================================ DShot600

@model_clock(50e6)
def dshot600(rig, store):
    """dshot600 at 600 kbit/s, an 8 kHz frame loop (GAP 454): three values, the last repeated."""
    f = rig.f_clk
    vals = [(1000 << 1), (48 << 1) | 1, (2047 << 1)]
    c = capture(rig, fw("dshot600", GAP=454), dict(side_pins=V0, clkdiv=host.clkdiv_for(f, 8 * 600e3)), mask=1,
                prefill=vals, owners={V0: 0}, extra=[(sim.core_reg(0, sim.C_R0), 0)], trig_s=150e-6,
                burst=(4096, 1, False))
    e = c.edges(0)
    save_run(store, "dshot600", rig, edges={"esc": e}, t_end=c.t_end, values=vals)
    bit = 1e9 / 600e3
    frames = [fr for fr in pf.dshot_decode(e, c.t_end, bit) if len(fr[1]) == 16 and fr[0] < c.t_end - 30e3]
    got = [pf.bits_value(b) for _, b, _, _ in frames]
    want = [pf.dshot_frame(v >> 1, v & 1) for v in vals]
    check(len(got) >= 6 and got[:3] == want and all(g == want[2] for g in got[3:]),
          f"frames {[hex(g) for g in got]}, want {[hex(w) for w in want]} then the last repeated")
    hi1 = [h for _, b, hs, _ in frames for x, h in zip(b, hs) if x]
    hi0 = [h for _, b, hs, _ in frames for x, h in zip(b, hs) if not x]
    per = [p for _, _, _, ps in frames for p in ps]
    starts = [t for t, _, _, _ in frames]
    fper = [b - a for a, b in zip(starts, starts[1:])]
    ck = 1e9 / f
    check(all(abs(h - 1250) <= ck + 0.5 for h in hi1) and all(abs(h - 625) <= ck + 0.5 for h in hi0)
          and all(abs(p - bit) <= ck + 0.5 for p in per), "a bit is more than one clock off 1250/625/1667 ns")
    rng_ = lambda xs: f"{min(xs):.1f}..{max(xs):.1f}"
    return [f"{len(got)} frames: {[hex(g) for g in got[:4]]}... (checksums Betaflight's, recomputed here); "
            f"{c.info['records']} records",
            f"T1H {rng_(hi1)} ns, T0H {rng_(hi0)} ns, bit {rng_(per)} ns (one clock = {ck:.0f} ns), "
            f"frame period {rng_([x / 1e3 for x in fper])} us (600 ticks = {600 * 1e6 / 4.8e6:.1f} us)"]


# ================================================================ NEC

def nec_cfg(rig):
    return dict(clkdiv=host.clkdiv_for(rig.f_clk, 114e3))


def nec_tick_ns(rig):
    cd = nec_cfg(rig)["clkdiv"]
    return (cd >> 8) + (cd & 255) / 256, (cd / 256) * 1e9 / rig.f_clk


@model_clock(5e6)
def nec_carrier(rig, store):
    """nec_tx: a repeat code at clock resolution (carrier frequency, duty, burst lengths)."""
    c = capture(rig, fw("nec_tx", IR=V0), nec_cfg(rig), mask=1, prefill=[pf.nec_word(0, 0, "repeat")],
                owners={V0: 0}, trig_s=0.5e-3, burst=burst_plan(rig, 14e-3))
    e = c.edges(0)
    save_run(store, "nec_carrier", rig, edges={"led": e}, t_end=c.t_end)
    _, tick = nec_tick_ns(rig)
    bursts = pf.carrier_bursts(e, 2 * 3 * tick)
    check([n for _, _, n in bursts] == [341, 21], f"bursts of {[n for _, _, n in bursts]} carrier periods")
    rises = [t for t, v in e if v == 1]
    per = [b - a for a, b in zip(rises, rises[1:]) if b - a < 2 * 3 * tick]
    highs = [b[0] - a[0] for a, b in zip(e, e[1:]) if a[1] == 1]
    ck = 1e9 / rig.f_clk
    fc = 1e9 / (sum(per) / len(per))
    lead_to_stop = (bursts[1][0] - bursts[0][0]) / tick
    check(abs(fc / 38000 - 1) < 1e-3, f"carrier {fc:.2f} Hz")
    check(abs(lead_to_stop - 1280) < 0.01, f"leader start to stop burst start {lead_to_stop:.2f} ticks, want 1280")
    return [f"repeat code: bursts of {[n for _, _, n in bursts]} carrier periods (16 u leader, 1 u stop); "
            f"{c.info['records']} records",
            f"carrier {fc:.2f} Hz (period {min(per) / ck:.0f}..{max(per) / ck:.0f} clocks), on "
            f"{min(highs) / 1e3:.3f}..{max(highs) / 1e3:.3f} us of {sum(per) / len(per) / 1e3:.3f} "
            f"(duty {sum(highs) / len(highs) / (sum(per) / len(per)) * 100:.1f} %); leader to stop burst "
            f"{lead_to_stop:.2f} ticks = 20 u ({(bursts[1][0] - bursts[0][0]) / 1e3:.1f} us)"]


def nec_envelope_from_records(c, max_gap_ticks=30):
    """Bursts (start, end) in ns from a coarse la_capture of the carrier: a
    burst starts at its first record (the first carrier rise, to the tick)
    and ends at its last (up to 7 ticks and a carrier period early)."""
    recs = [t for t, s, cnt in c.records() if not (cnt == 0)]
    bursts = []
    for t in recs:
        if bursts and t - bursts[-1][1] <= max_gap_ticks:
            bursts[-1][1] = t
        else:
            bursts.append([t, t])
    return [(a * c.tick_ns, b * c.tick_ns) for a, b in bursts]


def nec_frames_run(rig, store, name, codes, dur_s):
    la_div = max(1, round(5e-6 * rig.f_clk))           # 5 us ticks: 7 ticks > a carrier period
    c = capture(rig, fw("nec_tx", IR=V0), nec_cfg(rig), mask=1, prefill=[pf.nec_word(*x) for x in codes],
                owners={V0: 0}, trig_s=1e-3, la_div=la_div, burst=burst_plan(rig, dur_s))
    per_word = c.info["burst"][1] * 32
    check(per_word < 7 * la_div, "the burst drains slower than la_capture's records")
    bursts = nec_envelope_from_records(c)
    rs = [(1, 0.0, bursts[0][0])]
    for k, (a, b) in enumerate(bursts):
        nxt = bursts[k + 1][0] if k + 1 < len(bursts) else c.t_end + 50e6
        rs += [(0, a, b - a), (1, b, nxt - b)]
    save_run(store, name, rig, envelope=[(0, a, b) for a, b in bursts], t_end=c.t_end, tick_ns=c.tick_ns, codes=codes)
    dec = pf.nec_decode_envelope(rs)

    def want32(a, cm, m):
        a16 = (a & 0xFF) | ((~a & 0xFF) << 8) if m == "standard" else a & 0xFFFF
        return a16 | ((cm & 0xFF) << 16) | ((~cm & 0xFF) << 24)
    want = [("repeat", None) if m == "repeat" else ("frame", want32(a, cm, m)) for a, cm, m in codes]
    got = [(k, d) for _, k, d in dec]
    check(got == want, f"decoded {[(k, None if d is None else hex(d)) for k, d in got]}, want "
                       f"{[(k, None if d is None else hex(d)) for k, d in want]}")
    _, tick = nec_tick_ns(rig)
    starts = [a for a, _ in bursts]
    return c, starts, tick, la_div


@model_clock(1e6)
def nec_frames(rig, store):
    """nec_tx: a standard and an extended frame, the carrier captured at 5 us
    ticks (one record per carrier period: the envelope, burst starts to the tick)."""
    out = []
    for name, code in (("nec_standard", (0x04, 0x08, "standard")), ("nec_extended", (0xBEEF, 0x11, "extended"))):
        c, starts, tick, la_div = nec_frames_run(rig, store, name, [code], 85e-3)
        check(len(starts) == 34, f"{len(starts)} bursts, want 34")
        u = lambda d, n: d / n / 1e3
        lead = u(starts[1] - starts[0], 24)
        a, cm, m = code
        w = ((a & 0xFF) | ((~a & 0xFF) << 8) if m == "standard" else a) | ((cm & 0xFF) << 16) | ((~cm & 0xFF) << 24)
        bits = [(w >> i) & 1 for i in range(32)]
        b0 = [u(starts[i + 2] - starts[i + 1], 2) for i in range(32) if not bits[i]]
        b1 = [u(starts[i + 2] - starts[i + 1], 4) for i in range(32) if bits[i]]
        res = c.tick_ns / 1e3
        nom = 64 * tick / 1e3
        for v in [lead] + b0 + b1:
            check(abs(v - nom) <= res + 0.01, f"unit {v:.2f} us, firmware's {nom:.2f} us (+-{res:.0f} us tick)")
        out.append(f"{m} 0x{a:X}/0x{cm:02X}: decoded from the envelope; unit from burst starts: leader+space "
                   f"{lead:.2f}, bits 0 {min(b0):.2f}..{max(b0):.2f}, bits 1 {min(b1):.2f}..{max(b1):.2f} us "
                   f"(firmware {nom:.2f} us = 64 ticks, NEC 562.5; +-{res:.0f} us capture tick); "
                   f"{c.info['records']} records")
    return out


@model_clock(1e6)
def nec_period(rig, store):
    """nec_tx: two repeat codes, the code-to-code period (192 u)."""
    c, starts, tick, la_div = nec_frames_run(rig, store, "nec_period", [(0, 0, "repeat")] * 2, 123e-3)
    check(len(starts) == 4, f"{len(starts)} bursts, want 4")
    p = starts[2] - starts[0]
    want = 12288 * tick
    check(abs(p - want) <= c.tick_ns, f"code period {p / 1e6:.4f} ms, want {want / 1e6:.4f}")
    return [f"repeat to repeat {p / 1e6:.4f} ms (firmware 12288 ticks = {want / 1e6:.4f} ms; NEC 108 ms; "
            f"+-{c.tick_ns / 1e3:.0f} us capture tick)"]


def nec_want(codes):
    out = []
    for a, cm, m in codes:
        if m == "repeat":
            out.append({"kind": "repeat"})
        else:
            a16 = (a & 0xFF) | ((~a & 0xFF) << 8) if m == "standard" else a & 0xFFFF
            out.append({"kind": "frame", "address": a16, "command": cm, "command_ok": True,
                        "standard": ((a16 >> 8) ^ a16) & 0xFF == 0xFF})
    return out


@model_clock(500e3)
def nec_rx(rig, store):
    """nec_rx (uio0) fed by the pattern generator: an independent remote's
    envelope at +-10 % with 200 us burst distortion, then noise and a truncated frame."""
    f = rig.f_clk
    IR = 0
    rng = random.Random(9)
    seqs, want = [], []
    variants = [(1.0, 0.0), (0.9, 200e3), (1.1, -200e3)]
    for scale, stretch in variants:
        codes = [(rng.getrandbits(8), rng.getrandbits(8), "standard"), (0, 0, "repeat"),
                 (rng.getrandbits(16), rng.getrandbits(8), "extended")]
        for a, cm, m in codes:
            env = pf.nec_envelope(a, cm, m, scale, stretch)
            env.append((1, 108e6 * scale - sum(d for _, d in env)))
            seqs.append(env)
        want += nec_want(codes)
    seqs.append([(0, 300e3), (1, 2e6), (0, 3e6), (1, 10e6)])               # glitch, a 3 ms "leader"
    seqs.append(pf.nec_envelope(0x10, 0x20)[:30] + [(1, 20e6)])            # truncated after 13 bits
    seqs.append([(0, 30e6), (1, 5e6)])                                     # stuck low 30 ms
    seqs.append(pf.nec_envelope(0x10, 0x20) + [(1, 50e6)])
    want += [{"kind": "error", "bits": 13}, nec_want([(0x10, 0x20, "standard")])[0]]
    sched = pg_from_levels(rig, seqs, other=0, bit=IR)
    check(len(sched) <= 1024, f"{len(sched)} generator entries")
    setup(rig, mode=PG, pg_init=1 << IR)
    load(rig, 1, fw("nec_rx", IR=IR), in_pins=IR, clkdiv=host.clkdiv_for(f, 100e3))
    rig.pg_load(sched, init=1 << IR)
    rig.write(sim.A_CTRL, 0b10 | (0b10 << 8))
    rig.pg_start()
    recs = poll_records(rig, [1], lambda r: not rig.pg_busy(), step_s=0.02, timeout_s=10, after_s=0.2)[1]
    finish(rig)
    got = [pf.nec_rx_record(w) for w in recs]
    save_run(store, "nec_rx", rig, records=recs, sched_len=len(sched))
    check(got == want, f"nec_rx records {got}\n want {want}")
    return [f"{len(sched)} generator entries, {sum(d for s in seqs for _, d in s) / 1e9:.2f} s: "
            f"{len(want)} records exactly as expected",
            f"senders at x{[v[0] for v in variants]} timing with bursts {[v[1] / 1e3 for v in variants]} us "
            f"longer: standard, repeat, extended each; glitch, 3 ms leader and 30 ms stuck-low ignored; "
            f"truncated frame -> error after 13 bits; decoding resumed"]


# ================================================================ Wiegand

WG_TX = lambda **kw: fw("wiegand_tx", D0=V0, D1=V0 + 1, **kw)


@model_clock(1e6)
def wiegand_timing(rig, store):
    """wiegand_tx, PW 50 us, IV 2 ms: a 26-bit H10301 and a 34-bit frame captured."""
    f = rig.f_clk
    frames = [(24, pf.h10301(42, 31337)), (32, 0x12345678)]
    c = capture(rig, WG_TX(PW=50, IV=2000, FG=20000), dict(clkdiv=host.clkdiv_for(f, 1e6)), mask=3,
                prefill=sum([pf.wiegand_words(n, d) for n, d in frames], []), owners={V0: 0, V0 + 1: 0},
                pout=3 << V0, extra=[(sim.core_reg(0, sim.C_CRCCTRL), 1 | (1 << 21))], trig_s=1e-3,
                burst=burst_plan(rig, 0.166))
    log = c.log()
    save_run(store, "wiegand_timing", rig, edges={"d0": c.edges(0), "d1": c.edges(1)}, t_end=c.t_end)
    dec = pf.wiegand_decode(log, 0, 1, gap_ns=4e6)
    want = [pf.wiegand_bits(n, d) for n, d in frames]
    check([b for _, b, _, _ in dec] == want, f"bits {[b for _, b, _, _ in dec]} != {want}")
    ws = sorted({round(w, 1) for _, _, wl, _ in dec for w in wl})
    iv = sorted({round(i, 1) for _, _, _, il in dec for i in il})
    half = 0.5e9 / f
    check(all(abs(w - 50e3) < half for w in ws) and all(abs(i - 2e6) < half for i in iv),
          f"pulses {ws} ns, intervals {iv} ns")
    return [f"bits {want[0]} (26-bit H10301 42/31337) and {want[1]} (34-bit): exact; {c.info['records']} records",
            f"pulses {[w / 1e3 for w in ws]} us, intervals {[i / 1e3 for i in iv]} us (SIA: 20..100 / 200..20000)"]


@model_clock(1e6)
def wiegand_pair(rig, store):
    """wiegand_tx (core 0) -> V0/V1 -> wiegand_rx (core 1), PW 50 us, IV 200 us."""
    f = rig.f_clk
    rng = random.Random(4)
    good26 = pf.wiegand_bits(24, pf.h10301(42, 31337))
    bad26 = good26[:-1] + ("0" if good26[-1] == "1" else "1")
    raw30 = rng.getrandbits(30)
    frames = [(24, pf.h10301(42, 31337), False), (32, 0x89ABCDEF, False), (30, raw30, True),
              (26, int(bad26, 2), True)]
    words = sum([pf.wiegand_words(n, d, raw) for n, d, raw in frames], [])
    setup(rig, owners={V0: 0, V0 + 1: 0}, pout=3 << V0)
    load(rig, 0, WG_TX(PW=50, IV=200, FG=2000), clkdiv=host.clkdiv_for(f, 1e6))
    rig.write(sim.core_reg(0, sim.C_CRCCTRL), 1 | (1 << 21))
    load(rig, 1, fw("wiegand_rx", D0=V0, FTO=1000, PMAX=500), in_pins=V0, clkdiv=host.clkdiv_for(f, 1e6))
    rig.write(sim.core_reg(1, sim.C_CRCCTRL), 1 | (1 << 20))
    rig.write(FIFO(0), words, fixed=True)
    rig.write(sim.A_CTRL, 0b11 | (0b11 << 8))
    recs = poll_records(rig, [1], lambda r: len(r[1]) >= 3 * len(frames), step_s=0.002, timeout_s=2)[1]
    finish(rig)
    got = pf.wiegand_rx_frames(recs)
    want = [pf.wiegand_bits(n, d, raw) for n, d, raw in frames]
    save_run(store, "wiegand_pair", rig, records=recs)
    check([g["bits"] for g in got] == want, f"received {[g['bits'] for g in got]}\n sent {want}")
    check([g["valid"] for g in got] == [True, True, False, False] and not any(g["error"] for g in got),
          f"valid/error flags {[(g['valid'], g['error']) for g in got]}")
    return [f"core 0 -> V0/V1 -> core 1: {len(frames)} frames received bit for bit: 26-bit H10301 (valid), "
            f"34-bit (valid), raw 30-bit (not a validated format), 26-bit with a wrong parity bit (not valid)"]


@model_clock(1e6)
def wiegand_reader(rig, store):
    """wiegand_rx (uio0/uio1) fed by the pattern generator: reader timings
    20 us/200 us and 100 us/5 ms, 2 us glitches, a bad parity, both lines low."""
    f = rig.f_clk
    rng = random.Random(6)
    good = pf.wiegand_bits(24, pf.h10301(10, 20))
    f34 = pf.wiegand_bits(32, rng.getrandbits(32))
    f26 = pf.wiegand_bits(24, rng.getrandbits(24))
    bad = good[:-1] + ("0" if good[-1] == "1" else "1")
    sched, t = [], round(1e-3 * f)

    def put(v, d_ns):
        nonlocal t
        if not sched or sched[-1][1] != v:
            sched.append((t, v))
        t += max(1, round(clk(rig, d_ns)))

    def frame(bits, pw, iv, glitch=False):
        for b in bits:
            put(3 & ~(2 if b == "1" else 1), pw)
            if glitch:
                put(3, 500e3)
                put(2, 2e3)                     # a 2 us glitch on D0 between bits
                put(3, iv - pw - 502e3)
            else:
                put(3, iv - pw)
        put(3, 15e6)
    frame(f26, 20e3, 200e3)
    frame(f34, 100e3, 5e6)
    frame(good, 50e3, 2e6, glitch=True)
    frame(bad, 50e3, 2e6)
    for b in good[:5]:
        put(3 & ~(2 if b == "1" else 1), 50e3)
        put(3, 1950e3)
    put(0, 50e3)                                # both lines low
    put(3, 1950e3)
    frame(good[5:], 50e3, 2e6)
    sched = split_long(sched, t)
    setup(rig, mode=PG, pg_init=3)
    load(rig, 1, fw("wiegand_rx", D0=0, FTO=10000, PMAX=500), in_pins=0, clkdiv=host.clkdiv_for(f, 1e6))
    rig.write(sim.core_reg(1, sim.C_CRCCTRL), 1 | (1 << 20))
    rig.pg_load(sched, init=3)
    rig.write(sim.A_CTRL, 0b10 | (0b10 << 8))
    rig.pg_start()
    recs = poll_records(rig, [1], lambda r: not rig.pg_busy(), step_s=0.005, timeout_s=5, after_s=0.05)[1]
    finish(rig)
    got = pf.wiegand_rx_frames(recs)
    save_run(store, "wiegand_reader", rig, records=recs, sched_len=len(sched))
    check(len(got) == 5, f"{len(got)} frames: {got}")
    check(got[0]["bits"] == f26 and got[0]["valid"], f"20/200 us frame {got[0]}")
    check(got[1]["bits"] == f34 and got[1]["valid"], f"100 us/5 ms frame {got[1]}")
    check(got[2]["bits"] == good and got[2]["valid"] and not got[2]["error"], f"glitch frame {got[2]}")
    check(got[3]["bits"] == bad and not got[3]["valid"], f"bad parity frame {got[3]}")
    check(got[4]["error"] and not got[4]["valid"], f"both-low frame {got[4]}")
    return [f"{len(sched)} generator entries: 26-bit at 20 us/200 us and 34-bit at 100 us/5 ms valid; "
            f"2 us glitches ignored (valid); wrong parity bit not valid; both lines low -> error"]


# ================================================================ LIN

LIN_TB = 104


def lin_cfg(rig, tx, rx):
    return dict(out_pins=(tx, 1), in_pins=rx, clkdiv=host.clkdiv_for(rig.f_clk, 19200 * LIN_TB))


@model_clock(5e6)
def lin_timing(rig, store):
    """lin_commander alone: two published frames and a header (subscribe, no responder)."""
    f = rig.f_clk
    d8 = bytes(range(0x30, 0x38))
    cmds = pf.lin_command(0x10, data=bytes([1, 2, 3, 4])) + pf.lin_command(0x3C, data=d8, enhanced=False) + \
        pf.lin_command(0x21, n=2)
    c = capture(rig, fw("lin_commander", TX=V0, RX=V0, TB=LIN_TB, BTMO=4 * LIN_TB), lin_cfg(rig, V0, V0), mask=1,
                prefill=cmds, owners={V0: 0}, pout=1 << V0, trig_s=1e-3, burst=burst_plan(rig, 20e-3))
    e = c.edges(0)
    save_run(store, "lin_timing", rig, edges={"lin": e}, t_end=c.t_end)
    bit = 1e9 / 19200
    frames = pf.lin_decode(e, c.t_end, bit)
    want = [bytes([0x55, pf.lin_pid(0x10), 1, 2, 3, 4, pf.lin_checksum(pf.lin_pid(0x10), [1, 2, 3, 4])]),
            bytes([0x55, pf.lin_pid(0x3C)]) + d8 + bytes([pf.lin_checksum(pf.lin_pid(0x3C), d8, False)]),
            bytes([0x55, pf.lin_pid(0x21)])]
    check([fr["bytes"] for fr in frames] == want, f"bus bytes {[fr['bytes'].hex() for fr in frames]}")
    check(not any(fr["stop_errors"] for fr in frames), "stop-bit errors")
    # break and delimiter; every edge inside a byte against that byte's start
    brk = [fr["brk_ns"] / bit for fr in frames]
    delim = [(fr["starts"][0] - (fr["t"] + fr["brk_ns"])) / bit for fr in frames]
    err = []
    for fr in frames:
        for s0 in fr["starts"]:
            for t, _ in e:
                if s0 < t < s0 + 9.6 * bit:
                    err.append(t - s0 - round((t - s0) / bit) * bit)
    check(all(b >= 13 - 0.01 for b in brk) and all(d >= 1 - 0.01 for d in delim), f"break {brk}, delimiter {delim}")
    check(max(abs(x) for x in err) < 0.005 * 9 * bit, "an edge is more than 0.5 % off the bit grid")
    recs = pf.lin_records(c.info["tx_records"])
    check(len(recs) == 3 and recs[0]["published"] and recs[1]["published"] and recs[2]["flag1"]
          and not recs[2]["bytes"], f"commander records {recs}")
    return [f"bus: {[fr['bytes'].hex() for fr in frames]} (sync, PID with parity, data, enhanced/classic "
            f"checksum computed in firmware); {c.info['records']} records",
            f"break {min(brk):.4f}..{max(brk):.4f} bits, delimiter {min(delim):.4f}..{max(delim):.4f} bits, "
            f"edges inside bytes {min(err):+.0f}..{max(err):+.0f} ns from the 19200 bit/s grid "
            f"(TB {LIN_TB} ticks x CLKDIV {lin_cfg(rig, 0, 0)['clkdiv'] / 256:.4f})",
            "commander records: published, published, subscribe timed out (no responder on this run)"]


def lin_bus_run(rig, store, name, rsp_scale):
    """Commander (core 0, uio0) and responder (core 1, uio2), open drain on
    pe_lab's wired-AND bus; the responder's clock scaled by rsp_scale."""
    f = rig.f_clk
    d8 = bytes(range(0x40, 0x48))
    staged = bytes([0xC0, 0xFF, 0xEE])
    cmds = [pf.lin_command(0x10, data=bytes([1, 2, 3, 4])), pf.lin_command(0x21, n=3),
            pf.lin_command(0x3C, data=d8, enhanced=False)]
    setup(rig, mode=I2C, owners={0: 0, 2: 1}, od=0b101, pout=0b101)
    load(rig, 0, fw("lin_commander", TX=0, RX=0, TB=LIN_TB, BTMO=4 * LIN_TB), **lin_cfg(rig, 0, 0))
    rcd = host.clkdiv_for(f, 2e6 / rsp_scale)
    load(rig, 1, fw("lin_responder", TX=2, RX=2, BRK=1144, TMIN=89, TMAX=119, BTMO=4 * 104),
         out_pins=(2, 1), in_pins=2, clkdiv=rcd)
    rig.write(sim.core_reg(1, sim.C_CRCCTRL), 15 << 16)
    rig.write(FIFO(1), pf.lin_stage(0x21, staged), fixed=True)
    rig.write(sim.A_CTRL, 0b11 | (0b11 << 8))
    recs = {0: [], 1: []}
    for cmd in cmds:
        n_end = sum(1 for w in recs[0] if (w >> 12) & 0xF == 3) + 1
        rig.write(FIFO(0), cmd, fixed=True)
        r = poll_records(rig, [0, 1], lambda r: sum(1 for w in r[0] if (w >> 12) & 0xF == 3) >= 1,
                         step_s=0.0005, timeout_s=1, after_s=0.003)
        recs[0] += r[0]
        recs[1] += r[1]
        check(sum(1 for w in recs[0] if (w >> 12) & 0xF == 3) == n_end, "commander end records")
    fset = rig.read(sim.A_FSET)
    finish(rig)
    cmd, rsp = pf.lin_records(recs[0]), pf.lin_records(recs[1])
    save_run(store, name, rig, commander=recs[0], responder=recs[1], fset=fset)
    want1 = bytes([1, 2, 3, 4, pf.lin_checksum(pf.lin_pid(0x10), [1, 2, 3, 4])])
    want3 = d8 + bytes([pf.lin_checksum(pf.lin_pid(0x3C), d8, False)])
    check(len(cmd) == 3 and cmd[0]["published"] and cmd[2]["published"], f"commander {cmd}")
    check(cmd[1]["bytes"] == staged and cmd[1]["flag0"] and not cmd[1]["flag1"],
          f"subscribed frame at the commander {cmd[1]}")
    check(len(rsp) == 3, f"responder {rsp}")
    check([r["pid"] for r in rsp] == [pf.lin_pid(i) for i in (0x10, 0x21, 0x3C)] and
          not any(r["parity_error"] for r in rsp), f"responder headers {rsp}")
    check(rsp[0]["bytes"] == want1 and rsp[0]["flag1"], f"responder, frame 1 {rsp[0]}")
    check(rsp[1]["published"], f"responder, frame 2 {rsp[1]}")
    check(rsp[2]["bytes"] == want3 and rsp[2]["flag0"], f"responder, frame 3 {rsp[2]}")
    check(not fset & 4, "F2: the responder dropped a record")
    T = [r["T"] for r in rsp]
    ideal = 2e6 / rsp_scale / 19200
    check(all(abs(x - ideal) <= 1.5 for x in T), f"T {T}, ideal {ideal:.1f}")
    return [f"responder clock x{1 / rsp_scale:.2f} (CLKDIV {rcd / 256:.3f}): sync-field T = {sorted(set(T))} ticks "
            f"(ideal {ideal:.1f}); publish 0x10 heard (enhanced ok), subscribe 0x21 -> C0 FF EE answered and "
            f"checked by the commander, publish 0x3C heard (classic ok); no dropped record"]


@model_clock(5e6)
def lin_bus(rig, store):
    """lin_commander <-> lin_responder on pe_lab's wired-AND bus, responder clock nominal and +-10 %."""
    out = []
    for name, s in (("lin_bus", 1.0), ("lin_bus_fast", 1.0 / 1.1), ("lin_bus_slow", 1.0 / 0.9)):
        out += lin_bus_run(rig, store, name, s)
    return out


@model_clock(5e6)
def lin_rate(rig, store):
    """lin_responder answering a pattern-generator commander at 19200 bit/s
    and +-10 %, its response captured: response bit time vs the commander's."""
    f = rig.f_clk
    staged = bytes([0xC0, 0xFF, 0xEE])
    rates = [1.0, 1.1, 0.9]
    bit = 1e9 / 19200
    seqs = []
    for r in rates:
        seqs.append(pf.lin_waveform(0x21, bit_ns=bit / r))
        seqs.append([(1, 10e6)])
    sched = pg_from_levels(rig, seqs, other=1 << 2, bit=0, t0_s=1e-3)
    stage = sum([pf.lin_stage(0x21, staged) for _ in rates], [])
    # responder core 0 (RX uio0 = generator, TX uio2 open drain), la_capture core 1 on uio0..
    setup(rig, mode=PG, owners={2: 0}, od=0b100, pout=0b100, pg_init=0b101)
    load(rig, 0, fw("lin_responder", TX=2, RX=0, BRK=1144, TMIN=89, TMAX=119, BTMO=4 * 104),
         out_pins=(2, 1), in_pins=0, clkdiv=host.clkdiv_for(f, 2e6))
    rig.write(sim.core_reg(0, sim.C_CRCCTRL), 15 << 16)
    rig.write(FIFO(0), stage, fixed=True)
    load(rig, 1, fw("la_capture", MASK=0b101), in_pins=0, clkdiv=1 << 8)
    rig.pg_load(sched, init=0b101)
    rig.write(sim.A_CTRL, 0b11 | (0b11 << 8))
    rig.pg_start()
    words = rig.burst(1, *burst_plan(rig, sched[-1][0] / f + 2e-3))
    rsp_words = [w for w in rig.read(FIFO(0), 8, fixed=True) if w]
    finish(rig)
    c = Capture(words, 0b101, 1e9 / f)
    check(c.cap.exact, "capture has gaps")
    bus, tx = c.edges(0), c.edges(2)
    save_run(store, "lin_rate", rig, edges={"cmd": bus, "rsp": tx}, t_end=c.t_end, rates=rates)
    # each response, decoded at the commander's bit time; its bit time from
    # the edges inside its bytes (each against its byte's start bit)
    lines = []
    falls = [t for t, v in tx if v == 0]
    groups = []
    for t in falls:
        if groups and t - groups[-1][-1] < 15 * bit:
            groups[-1].append(t)
        else:
            groups.append([t])
    check(len(groups) == len(rates), f"{len(groups)} responses for {len(rates)} headers")
    want = staged + bytes([pf.lin_checksum(pf.lin_pid(0x21), staged)])
    for r, g in zip(rates, groups):
        b_cmd = bit / r
        seg = [(t, v) for t, v in tx if g[0] <= t <= g[-1] + 12 * b_cmd]
        e = [(g[0] - 20 * b_cmd, 0), (g[0] - 2 * b_cmd, 1)] + seg      # a dummy break before it
        fr = pf.lin_decode(e, seg[-1][0] + 12 * b_cmd, b_cmd, brk_bits=11)
        got = fr[0]["bytes"] if fr else b""
        check(got == want, f"rate x{r}: response {got.hex()} != {want.hex()}")
        starts = fr[0]["starts"]
        num = den = 0.0
        for t, _ in seg:
            s0 = max(s for s in starts if s <= t)
            k = round((t - s0) / b_cmd)
            if k >= 1:
                num, den = num + (t - s0), den + k
        b_rsp = num / den
        dev = (b_rsp / b_cmd - 1) * 100
        check(abs(dev) < 2.0, f"response bit {dev:+.2f} % off the commander's")
        sp = [(b - a) / b_cmd - 10 for a, b in zip(starts, starts[1:])]
        lines.append(f"commander x{r:.1f} ({b_cmd / 1e3:.2f} us bits): response {got.hex()}, response bit "
                     f"{b_rsp / 1e3:.3f} us = {dev:+.2f} % (LIN: +-2 % after sync), spaces between its bytes "
                     f"{min(sp):.2f}..{max(sp):.2f} bit")
    hdr = pf.lin_records(rsp_words)
    lines.append(f"responder header records: T = {[h.get('T') for h in hdr]} ticks; {len(sched)} generator entries")
    return lines


# ================================================================ bidirectional DShot

@model_clock(50e6)
def bidir_frames(rig, store):
    """dshot_bidir's frames on uio0 (generator = the pull-up), captured; no ESC: every reply times out."""
    f = rig.f_clk
    vals = [(800 << 1), (12 << 1) | 1, (1999 << 1)]
    d = pf.dshot_bidir_defines(f, 0)
    c = capture(rig, fw("dshot_bidir", **d), dict(side_pins=0, in_pins=0, clkdiv=1 << 8), mask=1, prefill=vals,
                owners={0: 0}, extra=[(sim.core_reg(0, sim.C_R0), 0)], trig_s=150e-6, burst=(4096, 1, False),
                in_base=0, pg_idle=0x01)
    e = [(t, 1 - v) for t, v in c.edges(0)]            # low pulses as highs
    save_run(store, "bidir_frames", rig, edges={"line": c.edges(0)}, t_end=c.t_end, values=vals, defines=d)
    bit = 1e9 / 600e3
    frames = [fr for fr in pf.dshot_decode(e, c.t_end, bit) if len(fr[1]) == 16 and fr[0] < c.t_end - 30e3]
    got = [pf.bits_value(b) for _, b, _, _ in frames]
    want = [pf.dshot_bidir_frame(v >> 1, v & 1) for v in vals]
    check(len(got) >= 5 and got[:3] == want and all(g == want[2] for g in got[3:]),
          f"frames {[hex(g) for g in got]}, want {[hex(w) for w in want]}")
    lo1 = [h for _, b, hs, _ in frames for x, h in zip(b, hs) if x]
    lo0 = [h for _, b, hs, _ in frames for x, h in zip(b, hs) if not x]
    per = [p for _, _, _, ps in frames for p in ps]
    starts = [t for t, _, _, _ in frames]
    fper = [b - a for a, b in zip(starts, starts[1:])]
    ck = 1e9 / f
    check(all(abs(h - d["T1"] * ck) < 1 for h in lo1) and all(abs(h - d["T0"] * ck) < 1 for h in lo0)
          and all(abs(p - d["BIT"] * ck) < 1 for p in per),
          f"lows {sorted(set(lo1))} / {sorted(set(lo0))} ns, bit {sorted(set(per))} ns, frames right; the defines "
          f"ask T1 {d['T1']}, T0 {d['T0']}, BIT {d['BIT']} clocks ({d['T1'] * ck:.0f} / {d['T0'] * ck:.0f} / "
          f"{d['BIT'] * ck:.0f} ns)" + (": with T0 odd, dshot_bidir.pasm's delays [(T0 - 2) / 2] and "
          "[T0 - 2 - (T0 - 2) / 2] each truncate a half clock (the assembler's / divides exactly, then "
          "int()), so every bit is a clock short" if d["T0"] % 2 else ""))
    r = lambda xs: f"{min(xs):.1f}..{max(xs):.1f}"
    return [f"{len(got)} inverted frames {[hex(g) for g in got[:4]]}... (inverted checksum); {c.info['records']} records",
            f"low {r(lo1)} ns for a 1, {r(lo0)} ns for a 0, bit {r(per)} ns (defines {d['T1']}/{d['T0']}/{d['BIT']} "
            f"clocks: {(d['T1'] * ck / 1250 - 1) * 100:+.1f}/{(d['T0'] * ck / 625 - 1) * 100:+.1f}/"
            f"{(d['BIT'] * ck / bit - 1) * 100:+.1f} % at {f / 1e6:.0f} MHz); frame period {r([x / 1e3 for x in fper])} us "
            f"(reply timeout {d['RTMO']} + rest {d['GAP']} clocks)"]


@model_clock(50e6)
def bidir_esc(rig, store):
    """dshot_bidir (core 0, uio0) against esc_emu (core 1, uio2 open drain) on
    pe_lab's wired-AND line: frames sampled by the ESC side, telemetry back."""
    f = rig.f_clk
    vals = [(800 << 1), (12 << 1) | 1, (1999 << 1)]
    periods = [333, 4096, 65000, 100, 1000, 2000, 30000, 512]
    replies = [pf.erpm_telemetry(p) for p in periods]

    def levels_word(v):
        lv = pf.telemetry_levels(v)
        return sum(b << (31 - i) for i, b in enumerate(lv))
    d = pf.dshot_bidir_defines(f, 0)
    turn_us = 30
    setup(rig, mode=I2C, owners={0: 0, 2: 1}, od=0b100, pout=0b101)
    load(rig, 0, fw("dshot_bidir", **d), side_pins=0, in_pins=0, clkdiv=1 << 8)
    rig.write(sim.core_reg(0, sim.C_R0), 0)
    rig.write(FIFO(0), vals, fixed=True)
    load(rig, 1, fw("esc_emu", E=2, TURN=round((turn_us * 48 + 35) / 4)), in_pins=2, out_pins=(2, 1),
         clkdiv=host.clkdiv_for(f, 48e6))
    rig.write(FIFO(1), [levels_word(v) for v in replies], fixed=True)
    rig.write(sim.A_CTRL, 0b11 | (0b11 << 8))
    rig.wait(0.005)
    fc = [w for w in rig.read(FIFO(0), 8, fixed=True)]
    esc = [w for w in rig.read(FIFO(1), 8, fixed=True)]
    finish(rig)
    save_run(store, "bidir_esc", rig, fc=fc, esc=esc, replies=replies, values=vals)
    tel = [pf.dshot_telemetry(r) for r in fc]
    seen = [(~w) & 0xFFFF for w in esc]
    want_f = [pf.dshot_bidir_frame(v >> 1, v & 1) for v in vals]
    want_f += [want_f[-1]] * (8 - len(want_f))
    check(seen == want_f, f"frames sampled by the ESC side {[hex(s) for s in seen]}, want {[hex(w) for w in want_f]}")
    check(all(t is not None and "value" in t for t in tel), f"telemetry records {[hex(r) for r in fc]}")
    check([t["value"] for t in tel] == replies and all(t["checksum_ok"] for t in tel),
          f"telemetry {[hex(t['value']) for t in tel]} != {[hex(r) for r in replies]}")
    want_p = [((v >> 4) & 0x1FF) << (v >> 13) for v in replies]          # m << e: what the encoding keeps
    check([t["period_us"] for t in tel] == want_p, f"eRPM periods {[t['period_us'] for t in tel]} != {want_p}")
    return [f"8 frames: the ESC side sampled {[hex(s) for s in seen[:3]]}, ... (inverted checksums ok); "
            f"8 replies, {turn_us} us turnaround, 21 levels at 750 kbit/s (esc_emu CLKDIV "
            f"{host.clkdiv_for(f, 48e6) / 256:.4f} x 64 clocks): the controller's records decode to eRPM periods "
            f"{[t['period_us'] for t in tel]} us, GCR and checksums ok"]


# ================================================================ main

PROTOCOLS = {
    "dmx": [dmx_short, dmx_mtbs],
    "i2s": [i2s],
    "dshot": [dshot600],
    "nec": [nec_carrier, nec_frames, nec_period, nec_rx],
    "wiegand": [wiegand_timing, wiegand_pair, wiegand_reader],
    "lin": [lin_timing, lin_bus, lin_rate],
    "bidir": [bidir_frames, bidir_esc],
}


def run_all(rig, names, store):
    results = []
    for p in names:
        for fn in PROTOCOLS[p]:
            rig.model_clock(fn.model_f)
            print(f"[{fn.__name__}] {fn.__doc__.split(chr(10))[0]}", flush=True)
            t = time.monotonic()
            try:
                for line in fn(rig, store):
                    print("  " + line, flush=True)
                print(f"  PASS ({time.monotonic() - t:.1f} s)", flush=True)
                results.append((p, fn.__name__, "PASS"))
            except Exception as e:  # report and continue
                print(f"  FAIL: {type(e).__name__}: {e}", flush=True)
                results.append((p, fn.__name__, "FAIL"))
                try:
                    finish(rig)
                except Exception as e2:
                    print(f"  (cleanup: {type(e2).__name__}: {e2})", flush=True)
    return results


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("protocols", nargs="*", default=list(PROTOCOLS))
    ap.add_argument("--model", action="store_true", help="the cycle model instead of the board")
    ap.add_argument("--save", help="keep the captures (JSON)")
    a = ap.parse_args()
    if a.protocols[:1] == ["sigrok"]:
        return sigrok_main(a.protocols[1])
    bad = [p for p in a.protocols if p not in PROTOCOLS]
    if bad:
        ap.error(f"unknown protocol(s) {bad}; choose from {list(PROTOCOLS)}")
    store = {} if a.save else None
    if a.model:
        rig = ModelRig()
        print("cycle model (pe.sim) with pe_lab's modes modelled; per-run model clocks as noted", flush=True)
    else:
        rig = HwRig()
        i = rig.ident
        print(f"bitstream RTL revision {i['rtl_rev'][:12]}{' (dirty)' if i['dirty'] else ''}, built {i['build_utc']} "
              f"UTC, digest {i['digest']}, clk_div {i['clk_div']}", flush=True)
        print(f"protoemu clk measured {rig.f_meas / 1e6:.5f} MHz over 2 s; defines and CLKDIVs derived for "
              f"{rig.f_clk / 1e6:.5f} MHz", flush=True)
    try:
        results = run_all(rig, a.protocols, store)
    finally:
        rig.close()
    if store is not None:
        store["_meta"] = {"model": rig.model, "f_clk": rig.f_clk, "f_meas": rig.f_meas, "ident": rig.ident,
                          "results": results, "utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())}
        pathlib.Path(a.save).write_text(json.dumps(store))
        print(f"saved {a.save}")
    print("summary: " + ", ".join(f"{n} {s}" for _, n, s in results))
    nfail = sum(1 for r in results if r[2] != "PASS")
    print("ALL PASS" if not nfail else f"{nfail} run(s) FAILED")
    sys.exit(1 if nfail else 0)


# ================================================================ sigrok (off the board)

def sigrok_main(path):
    from pe import sigrok
    if sigrok.cli() is None:
        sys.exit(sigrok.INSTALL_HINT)
    d = json.loads(pathlib.Path(path).read_text())
    meta = d.pop("_meta", {})
    print(f"captures from {path} ({'model' if meta.get('model') else 'hardware'}, {meta.get('utc')})")

    def trace(edges, t_end):
        tr = sigrok.Trace()
        for name, e in edges.items():
            pf.to_trace(tr, [tuple(x) for x in e], name)
        tr.end(round(t_end * 1000))
        return tr
    for name, r in d.items():
        try:
            if name.startswith("dmx") and r.get("mtbs", 0) >= 1:
                e = r["edges"]["dmx"]
                first = next(t for t, v in e if v == 0)
                tr = trace({"dmx": [[first - 20e3, 1]] + [x for x in e if x[0] >= first]}, r["t_end"])
                pk, err = sigrok.dmx512(tr, "dmx", res_ps=100_000)
                sent = bytes.fromhex(r["packets"][0])
                same = bytes(pk[0]["slots"][:len(sent)]) == sent if pk else False
                print(f"[{name}] sigrok dmx512: first packet's {len(sent)} slots {'equal' if same else 'DIFFER from'} "
                      f"what was sent; break {pk[0]['brk_ns'] / 1e3 if pk else None} us, MAB "
                      f"{pk[0]['mab_ns'] / 1e3 if pk else None} us; it then reads the next BREAK as slots "
                      f"(0.5.3 looks for a BREAK only after 512 slots): {len(err)} errors from "
                      f"{err[0][0] / 1e3 if err else None} us")
            elif name.startswith("dmx"):
                print(f"[{name}] (MTBS 0: libsigrokdecode 0.5.3's dmx512 misframes back-to-back slots; skipped)")
            elif name == "i2s":
                got, warn = sigrok.i2s(trace(r["edges"], r["t_end"]), "bclk", "ws", "sd", res_ps=10_000)
                print(f"[{name}] sigrok i2s: {len(got)} words, first {got[:4]}, warnings {warn}")
            elif name == "dshot600" or name == "bidir_frames":
                pol = "active-low" if name == "bidir_frames" else "active-high"
                ch = list(r["edges"])[0]
                out = sigrok.pwm(trace(r["edges"], r["t_end"]), ch, polarity=pol, res_ps=10_000)
                bit = 1e9 / 600e3
                bits, fr = [], []
                for t0, t1, duty in out:
                    if t1 - t0 < 2 * bit:
                        bits.append(1 if duty > 50 else 0)
                    else:
                        bits.append(1 if duty / 100 * (t1 - t0) > bit / 2 else 0)
                        fr.append(pf.bits_value(bits))
                        bits = []
                print(f"[{name}] sigrok pwm -> frames {[hex(x) for x in fr[:6]]}...")
            elif name == "nec_carrier":
                got, w = sigrok.ir_nec(trace(r["edges"], r["t_end"]), "led", "active-high", 38000, res_ps=100_000)
                print(f"[{name}] sigrok ir_nec on the raw carrier: {[(k, a, c) for _, k, a, c in got]} {w}")
            elif name in ("nec_standard", "nec_extended", "nec_period"):
                env = [[0, 1]]
                for _, a, b in r["envelope"]:
                    env += [[a, 0], [b, 1]]
                got, w = sigrok.ir_nec(trace({"rx": env}, r["t_end"]), "rx", "active-low", res_ps=100_000)
                print(f"[{name}] sigrok ir_nec on the envelope rebuilt from the capture: "
                      f"{[(k, a, c) for _, k, a, c in got]} {w}")
            elif name == "wiegand_timing":
                sg, bad = sigrok.wiegand(trace(r["edges"], r["t_end"]), "d0", "d1", bitwidth_ms=4, res_ps=100_000)
                print(f"[{name}] sigrok wiegand: {[b for _, b in sg]} invalid={bad}")
            elif name == "lin_timing":
                fr, warn = sigrok.lin(trace(r["edges"], r["t_end"]), "lin", 19200, version=2, res_ps=100_000)
                print(f"[{name}] sigrok lin: {[(x['id'], x['parity_ok'], x['data'].hex(), x['checksum_ok']) for x in fr]} "
                      f"{warn}")
        except Exception as e:
            print(f"[{name}] sigrok: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
