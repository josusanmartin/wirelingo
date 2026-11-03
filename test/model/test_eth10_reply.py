"""Match, then answer: firmware/eth10_reply.pasm on the cycle model, at pin level
(docs/eth10-reaction.md). Core 0 alone hears a frame, compares a field, and after
the frame and a 96-bit quiet line sends a fixed reply; no host after loading.

The far end is pe.ethsim's Wire: a Manchester sender on RXP and a receiver on
TD+/TD- that knows nothing of the chip. Far-end frames are built here with struct
and zlib. Replies are read off the pads by test_eth_stack's two decoders (an edge
tracker and a cell sampler), which must agree, with the exact 802.3 preamble and
SFD, a TP_IDL of 250-350 ns and never both TD pads high; zlib checks the FCS.

The reply's start is measured from the far end's last edge at the pad (the end of
its TP_IDL), against 96 bit times: N = first TD edge - last RXP edge - 9600 ns, in
clocks. The Wire stamps a TD change with the end of the model cycle that made it,
one clock after the clock edge at which the pads change (the Wire samples RXP at
that edge); TD times here are moved back by that clock (WIRE_STAMP), and
test_eth_reply_rtl.py, which reads the RTL's pads, agrees. The bound N in [0, 1) is derived from the firmware by pe.timing (the `ifg`
deadline, exact) plus the chip's fixed latencies, and each piece is checked on its
own per reply. Run with -s for the numbers the doc quotes."""
import math
import pathlib
import random
import struct
import sys
import zlib

import pytest

from pe import ethreply, ethsim, timing
from pe.modelhost import ModelHost

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import test_eth_stack as stack  # noqa: E402

CLOCKS = [50e6, 40e6]
PEER = bytes.fromhex("0200deadbe01")
ETHERTYPE = 0x88B5
THRESHOLD = 1000
IFG = ethsim.IFG_NS


def far_frame(field, size=64, ethertype=ETHERTYPE, seed=0):
    """A frame from the far end, destination through FCS: the field is the first
    two payload bytes, little-endian."""
    rng = random.Random(seed)
    body = b"\xff" * 6 + PEER + struct.pack(">H", ethertype) + struct.pack("<H", field & 0xFFFF)
    body += bytes(rng.getrandbits(8) for _ in range(size - 4 - len(body)))
    return body + struct.pack("<I", zlib.crc32(body))


def on_wire_ns(raw):
    """Preamble, SFD and frame at 100 ns a bit."""
    return (64 + 8 * len(raw)) * 100.0


REPLY_NS = on_wire_ns(bytes(64))
WIRE_STAMP = 1          # clocks: pe.ethsim.Wire's TD times are this late (see the docstring)


def bounds(clock_hz):
    """The checker's exact ifg interval in clocks, and N's bound [lo, hi) in clocks."""
    chk, res = timing.check_source(ethreply.FW.read_text(), ethreply.defines(clock_hz))
    assert not chk.errors
    r = {x.deadline.name: x for x in res}
    d = r["ifg"]
    assert d.lo == d.hi
    p = ethreply.clocks_per_bit(clock_hz)
    lo = ethreply.SYNC_CLOCKS[0] + d.lo + ethreply.OUT_CLOCKS - 96 * p
    hi = ethreply.SYNC_CLOCKS[1] + d.hi + ethreply.OUT_CLOCKS - 96 * p
    return d.lo, lo, hi


class Bench:
    """Chip with eth10_reply loaded, the Wire, a HIT monitor and a Tracer on core 0.
    Nothing touches the host port after loading."""

    def __init__(self, clock_hz, threshold=THRESHOLD, text=None):
        self.clock_hz = clock_hz
        self.clk = 1e9 / clock_hz
        self.mh = ModelHost()
        self.wire = ethsim.Wire(self.mh, clock_hz)
        self.prog = ethreply.program(clock_hz, text=text)
        self.tracer = timing.Tracer(self.mh.chip, 0, 0)
        self.mh.apply(ethreply.load_writes(clock_hz, threshold, text=text))
        self.hits, self._h = [], 0
        inner = self.mh.chip.step

        def step(*a, **kw):
            inner(*a, **kw)
            h = (self.mh.chip.outputs()[0] >> (ethreply.HIT - 8)) & 1
            if h and not self._h:
                self.hits.append(self.mh.chip.cycle * self.clk)
            self._h = h
        self.mh.chip.step = step

    def now(self):
        return self.mh.chip.cycle * self.clk

    def send(self, raw, at_ns=None, **kw):
        """at_ns: a far end that starts then whatever TD does; else one that defers."""
        self.wire.send(raw, at_ns=at_ns, force=at_ns is not None, **kw)

    def run_to(self, t_ns):
        n = int((t_ns - self.now()) / self.clk) + 1
        if n > 0:
            self.mh.step(n)

    def replies(self):
        return [b for b in self.wire.bursts if not b.pulse]

    def td(self, t):
        """A Wire TD time moved to the clock edge where the pad changes."""
        return t - WIRE_STAMP * self.clk

    def frames(self):
        """Every reply decoded twice, checked as test_eth_stack checks the stack's frames."""
        return stack.wire_frames(self.wire)

    def records(self):
        return list(self.mh.chip.cores[0].rxf)

    def last_rx_edge(self, t):
        """The far end's last edge before t: a frame's TP_IDL fall, or a link pulse's."""
        ends = [e for _, e in self.wire.sent if e < t]
        ends += [te for te, lv in self.wire.edges if lv == 0 and te < t]
        return max(ends)

    def done_cycles(self, label):
        pc = self.prog.labels[label]
        return [i[4] for i in self.tracer.instances() if i[0] == pc and i[4] is not None]

    def check_reply_timing(self):
        """Per reply: (N, the synchronizer piece, the firmware piece, the output piece),
        N in clocks after 96 bit times from the far end's last edge."""
        lows, firsts = self.done_cycles("defer"), self.done_cycles("first")
        out = []
        reps = self.replies()
        assert len(firsts) == len(reps)
        for b, cf in zip(reps, firsts):
            t0 = self.td(b.t0)
            prior = self.last_rx_edge(t0)
            cl = max(c for c in lows if c < cf)
            out.append(((t0 - prior - IFG) / self.clk, (cl * self.clk - prior) / self.clk, cf - cl,
                        (t0 - cf * self.clk) / self.clk))
        return out


def overlap(bench):
    """Our frames that share air time with a far-end frame (a collision)."""
    return [b for b in bench.replies()
            if any(bench.td(b.t0) < e and s < bench.td(b.t1) for s, e in bench.wire.sent)]


def assert_timing(bench, label="", first_only=False):
    d, lo, hi = bounds(bench.clock_hz)
    t = bench.check_reply_timing()
    assert t
    if first_only:
        t = t[:1]
    for n, sync, fw, outp in t:
        assert ethreply.SYNC_CLOCKS[0] - 1e-9 <= sync < ethreply.SYNC_CLOCKS[1], (label, sync)
        assert fw == d, (label, fw)
        assert abs(outp - ethreply.OUT_CLOCKS) < 1e-9, (label, outp)
        assert lo - 1e-9 <= n < hi, (label, n, lo, hi)
    return [x[0] for x in t]


# ---------------------------------------------------------------- the reply frame and the program
def test_reply_frame_is_valid_and_fits():
    raw = ethreply.reply_frame()
    assert len(raw) == 64 and struct.pack("<I", zlib.crc32(raw[:-4])) == raw[-4:]
    assert raw[:6] == b"\xff" * 6 and raw[6:12] == ethreply.CHIP_MAC and raw[12:14] == b"\x08\x00"
    ip = raw[14:34]
    assert ip[0] == 0x45 and ip[9] == 17 and stack.cksum(ip) == 0
    assert struct.unpack(">HHH", raw[34:40]) == (ethreply.PORT, ethreply.PORT, 8 + 18)
    pseudo = ip[12:20] + b"\x00\x11" + raw[38:40]
    assert stack.cksum(pseudo + raw[34:60]) == 0
    assert raw[42:60] == b"wirelingo matched!"
    assert not raw[-1] >> 7                       # the last bit on the wire is 0
    for clock in CLOCKS:
        prog = ethreply.program(clock)
        print(f"\neth10_reply: {len(prog.words)} words of 256")
        assert len(prog.words) <= 256
    ch = ethreply.chunks(raw)
    assert bytes(b for c in ch for b in (c & 255, c >> 8)) == raw


def test_chunks_refuse_what_the_firmware_cant_send():
    raw = ethreply.reply_frame()
    with pytest.raises(ValueError):
        ethreply.chunks(raw[:-1])
    with pytest.raises(ValueError):
        ethreply.chunks(raw[:-1] + bytes([raw[-1] ^ 1]))          # bad FCS
    for ident in range(64):                                       # an FCS that ends in 1
        body = bytearray(raw[:-4])
        body[18:20] = ident.to_bytes(2, "big")
        bad = bytes(body) + struct.pack("<I", zlib.crc32(bytes(body)))
        if bad[-1] >> 7:
            break
    with pytest.raises(ValueError):
        ethreply.chunks(bad)


@pytest.mark.parametrize("clock", CLOCKS)
def test_deadlines_from_the_firmware(clock):
    """pe.timing on eth10_reply: the window path is exact, and so is the path from a
    frame's end to it. The first is what N's bound is built from."""
    chk, res = timing.check_source(ethreply.FW.read_text(), ethreply.defines(clock))
    print()
    for r in res:
        print(r.report())
    assert not chk.errors
    r = {x.deadline.name: x for x in res}
    assert all(x.status == "PASS (conditional)" for x in res)
    p = ethreply.clocks_per_bit(clock)
    assert (r["ifg"].lo, r["ifg"].hi) == (96 * p - ethreply.WINDOW_TRIM + ethreply.PATH_TICKS,) * 2
    assert (r["frame_end"].lo, r["frame_end"].hi) == (2, 2)
    _, lo, hi = bounds(clock)
    assert (lo, hi) == (0, 1)


# ---------------------------------------------------------------- match, then answer
def train(bench, rng, n, sizes=(64, 65, 100, 300), jitter=0.0, tp_idl=ethsim.TP_IDL_NS, at=10000.0):
    """n far-end frames, each far enough from the last that the reply goes out: a
    random field above or below the threshold, sometimes another EtherType, random
    size, clock (+-100 ppm) and sampling phase. Returns the expected per frame: the
    field if it matches, else None; and the end time."""
    exp = []
    for i in range(n):
        size = rng.choice(sizes)
        kind = rng.random()
        if kind < 0.15:
            raw, m = far_frame(THRESHOLD + 5, size, ethertype=0x0800, seed=i), None
        else:
            field = rng.choice([THRESHOLD, THRESHOLD - 1, 0, 0xFFFF, rng.randrange(1 << 16)])
            raw, m = far_frame(field, size, seed=i), (field if field >= THRESHOLD else None)
        bench.send(raw, at_ns=at, ppm=rng.uniform(-100, 100), offset_ns=rng.uniform(0, bench.clk),
                   jitter_ns=jitter, tp_idl_ns=tp_idl)
        exp.append(m)
        at += on_wire_ns(raw) + tp_idl + IFG + REPLY_NS + IFG + rng.uniform(1000, 5000)
    return exp, at


def check_train(bench, exp):
    reps = bench.replies()
    starts = [s for s, _ in bench.wire.sent]
    # one reply after each matching frame, before the next far-end frame, none otherwise
    for i, m in enumerate(exp):
        nxt = starts[i + 1] if i + 1 < len(starts) else float("inf")
        mine = [b for b in reps if bench.wire.sent[i][1] < b.t0 < nxt]
        assert len(mine) == (m is not None), (i, m, len(mine))
    assert bench.frames() == [ethreply.reply_frame()] * len(reps)
    assert len(bench.hits) == len(reps)
    matched = [m for m in exp if m is not None]
    assert bench.records() == matched[:8]                          # nobody pops: the first 8 stay
    assert not overlap(bench)


@pytest.mark.parametrize("clock", CLOCKS)
def test_match_then_reply_timing(clock):
    """60 frames, no host: every matching frame gets one valid reply, and its first
    edge comes N in [0, 1) clocks after 96 bit times from the far end's last edge."""
    b = Bench(clock)
    exp, end = train(b, random.Random(int(clock)), 60)
    b.run_to(end)
    assert sum(m is not None for m in exp) >= 20
    check_train(b, exp)
    ns = assert_timing(b)
    # the same from the end of the far end's last bit: its 300 ns TP_IDL comes first
    raw = [b.td(r.t0) - (b.last_rx_edge(b.td(r.t0)) - ethsim.TP_IDL_NS) for r in b.replies()]
    print(f"\n{clock / 1e6:.0f} MHz, {len(ns)} replies: N = {min(ns):.3f}-{max(ns):.3f} clocks after 96 bit "
          f"times from the far end's last edge; last bit's end to our first edge {min(raw):.1f}-{max(raw):.1f} ns")
    assert all(ethsim.TP_IDL_NS + IFG <= x < ethsim.TP_IDL_NS + IFG + b.clk for x in raw)
    assert max(ns) - min(ns) > 0.8                 # the sampling phases were covered
    # the checker's intervals against the same run (pe.timing.measure)
    chk, res = timing.check_source(ethreply.FW.read_text(), ethreply.defines(clock))
    for r in res:
        ms = [m for m in timing.measure(chk, r.deadline, b.tracer) if not m.violations]
        assert ms and all(r.contains(m.ticks, m.clocks) for m in ms), r.deadline.name


def test_reply_with_8023_jitter():
    """50 MHz, 802.3's +-13.5 ns of edge jitter on the far end. N is measured from
    the actual (jittered) last edge, so the bound is the same."""
    b = Bench(50e6)
    exp, end = train(b, random.Random(3), 30, jitter=13.5)
    b.run_to(end)
    check_train(b, exp)
    ns = assert_timing(b, "jitter")
    print(f"\n+-13.5 ns: N = {min(ns):.3f}-{max(ns):.3f} clocks over {len(ns)} replies")


@pytest.mark.parametrize("clock", CLOCKS)
@pytest.mark.parametrize("tp_idl", [250.0, 350.0])
def test_other_tp_idl_lengths(clock, tp_idl):
    """The window starts when RXP is low. With a TP_IDL of 250 ns or more the core
    is waiting for that before the fall, so the bound is unchanged."""
    b = Bench(clock)
    exp, end = train(b, random.Random(int(tp_idl)), 12, sizes=(64, 65), tp_idl=tp_idl)
    b.run_to(end)
    check_train(b, exp)
    assert_timing(b, tp_idl)


@pytest.mark.parametrize("clock", CLOCKS)
@pytest.mark.parametrize("tp_idl", [60.0, 120.0, 200.0])
def test_short_tp_idl_only_delays_the_reply(clock, tp_idl):
    """A sender whose last edge comes before the core reaches the low wait (the line
    unit ends a frame 1.5 bit times after its last mid-bit edge): the window then
    starts at the wait, later than the edge, never earlier."""
    b = Bench(clock)
    exp, end = train(b, random.Random(9), 8, sizes=(64, 65), tp_idl=tp_idl)
    b.run_to(end)
    check_train(b, exp)
    _, lo, _ = bounds(clock)
    late = [n for n, *_ in b.check_reply_timing()]
    print(f"\n{clock / 1e6:.0f} MHz, {tp_idl:.0f} ns TP_IDL: N = {min(late):.2f}-{max(late):.2f} clocks")
    assert min(late) >= lo and max(late) < 13


# ---------------------------------------------------------------- the line busy
def back_to_back(bench, fields, at=10000.0, gap=IFG):
    """A far-end train: each frame starts `gap` after the end of the previous one's
    last bit cell (802.3 counts the gap from there; TP_IDL is inside it)."""
    exp = []
    for i, f in enumerate(fields):
        raw = far_frame(f, 64, seed=i)
        bench.send(raw, at_ns=at)
        exp.append(f if f >= THRESHOLD else None)
        at += on_wire_ns(raw) + gap
    return exp, at


@pytest.mark.parametrize("clock", CLOCKS)
@pytest.mark.parametrize("fields", [[2000, 5], [2000, 3000], [5, 2000], [2000, 5, 3000, 7, 4000]])
def test_back_to_back_frames_are_all_heard(clock, fields):
    """Frames at the 802.3 minimum gap: the reply owed for one waits, the core hears
    the next, and every matching frame gets its reply after the train. Our own
    replies keep 96 bit times between them too."""
    b = Bench(clock)
    exp, end = back_to_back(b, fields)
    n = sum(m is not None for m in exp)
    b.run_to(end + n * (REPLY_NS + 2 * IFG) + 5000)
    sent = b.wire.sent
    # a far-end frame's first edge is half a bit into its first cell (a 1 is low, then high)
    assert all(abs(y[0] - 50 - (x[1] - ethsim.TP_IDL_NS) - IFG) < 1e-6 for x, y in zip(sent, sent[1:]))
    reps = b.replies()
    assert len(reps) == n
    assert reps[0].t0 > sent[-1][1]                                # after the whole train
    assert b.frames() == [ethreply.reply_frame()] * n
    assert b.records() == [m for m in exp if m is not None]
    assert len(b.hits) == n
    assert not overlap(b)
    assert_timing(b, "first reply", first_only=True)
    gaps = [y.t0 - x.t1 for x, y in zip(reps, reps[1:])]
    if gaps:
        print(f"\n{clock / 1e6:.0f} MHz: our TP_IDL's end to our next preamble {min(gaps):.0f}-{max(gaps):.0f} ns")
        assert all(g == OWN_GAP_NS[clock] for g in gaps), gaps


OWN_GAP_NS = {50e6: 10140.0, 40e6: 10175.0}


@pytest.mark.parametrize("clock", CLOCKS)
def test_link_pulses_restart_the_window(clock):
    """Far-end link pulses during the window hold the reply back: it starts N clocks
    after 96 bit times from the last pulse. Pulses alone get no reply."""
    b = Bench(clock)
    raw = far_frame(2000)
    b.send(raw, at_ns=10000.0)
    end = 10000.0 + on_wire_ns(raw) + ethsim.TP_IDL_NS
    b.run_to(end + 100)
    b.wire.pulses([end + 3000.0 + 0.37 * b.clk, end + 9000.0, end + 15000.3])
    b.run_to(end + 15000 + IFG + REPLY_NS + 2000)
    assert len(b.replies()) == 1 and b.frames() == [ethreply.reply_frame()]
    assert b.replies()[0].t0 > end + 15100 + IFG
    # a pulse is a short frame to the line unit: the window restarts when the core
    # has consumed its end, 1.5 bit times after its fall (isa.md 4.7) and the
    # frame_end path, so later than after a frame's TP_IDL, never earlier
    (n, *_), = b.check_reply_timing()
    p = ethreply.clocks_per_bit(clock)
    print(f"\n{clock / 1e6:.0f} MHz: after link pulses, N = {n:.2f} clocks")
    assert bounds(clock)[1] <= n < 1.5 * p + 3
    t = b.now()
    b.wire.pulses([t + 2000.0, t + 20000.0])
    b.run_to(t + 60000)
    assert len(b.replies()) == 1


# ---------------------------------------------------------------- collisions
def solo_reply_start(clock):
    b = Bench(clock)
    raw = far_frame(2000)
    b.send(raw, at_ns=10000.0)
    b.run_to(10000.0 + on_wire_ns(raw) + IFG + REPLY_NS + 3000)
    return b.td(b.replies()[0].t0), raw


def first_edge_at(bench, raw, t_edge, **kw):
    """Force a far-end frame whose first edge (half a bit into its first cell) is at t_edge."""
    x = t_edge - 50 - 2 * bench.clk                  # the Wire starts 2 clocks after it decides
    k = math.floor(x / bench.clk)
    bench.send(raw, at_ns=k * bench.clk, offset_ns=x - k * bench.clk, **kw)


def blind_run(clock, t_ours, lead):
    """A matching frame, then a far-end frame forced to have its first edge `lead` ns
    before our reply's would; then a third, matching, from a far end that defers."""
    b = Bench(clock)
    b.send(far_frame(2000), at_ns=10000.0)
    first_edge_at(b, far_frame(3000, seed=2), t_ours - lead)
    b.run_to(t_ours + 2 * REPLY_NS)
    assert abs(b.wire.sent[1][0] - (t_ours - lead)) < 1e-6
    b.send(far_frame(4000, seed=3))
    b.run_to(b.now() + 4 * REPLY_NS + 6 * IFG)
    return b


@pytest.mark.parametrize("clock", CLOCKS)
def test_blind_window(clock):
    """How late can a far-end frame's first edge come and still hold our reply back?
    5 ns steps. What's left is the synchronizer and the path to the first chunk; a
    frame inside it collides with the reply: nothing here jams or retries, the
    colliding frame is not heard, and the next frame is heard and answered."""
    t0, _ = solo_reply_start(clock)
    hit, heard = [], []
    for lead in range(60, 200, 5):
        b = blind_run(clock, t0, lead)
        first = b.replies()[0]
        if abs(b.td(first.t0) - t0) < 1e-6:
            hit.append(lead)
            assert overlap(b)
            assert b.records() == [2000, 4000], (lead, b.records())    # 3000 collided: not heard
            assert len(b.replies()) == 2 and b.frames() == [ethreply.reply_frame()] * 2
        else:
            heard.append(lead)
            assert first.t0 > b.wire.sent[1][1] and not overlap(b)
            assert b.records() == [2000, 3000, 4000]
            assert len(b.replies()) == 3 and b.frames() == [ethreply.reply_frame()] * 3
    blind, first_heard = max(hit), min(heard)
    print(f"\n{clock / 1e6:.0f} MHz: a far-end first edge up to {blind} ns before ours goes unheard; "
          f"{first_heard} ns is heard")
    assert all(x < first_heard for x in hit)
    assert (blind, first_heard) == BLIND_NS[clock]


BLIND_NS = {50e6: (95, 100), 40e6: (120, 125)}      # 5 clocks: synchronizer 2, jmp t, out line, line unit 1


@pytest.mark.parametrize("clock", CLOCKS)
@pytest.mark.parametrize("after_us", [1.0, 25.0, 50.0, 56.0])
def test_frame_arriving_while_replying(clock, after_us):
    """A far end that ignores carrier starts a matching frame this long after our
    reply's first edge. Our reply stays whole; the colliding frame is not heard (no
    HIT, no record, no reply); a frame after it, from a far end that defers, is."""
    t0, raw0 = solo_reply_start(clock)
    b = Bench(clock)
    b.send(raw0, at_ns=10000.0)
    coll = far_frame(3000, 100, seed=5)
    b.send(coll, at_ns=t0 + after_us * 1000)
    b.run_to(t0 + after_us * 1000 + on_wire_ns(coll) + 1000)
    assert overlap(b)
    b.send(far_frame(4000, seed=6))
    b.run_to(b.now() + 3 * REPLY_NS + 4 * IFG)
    assert b.frames() == [ethreply.reply_frame()] * 2
    assert b.records() == [2000, 4000]
    assert len(b.hits) == 2


@pytest.mark.parametrize("clock", CLOCKS)
def test_frame_just_after_our_reply(clock):
    """Where the quiet wait after our reply ends: a far-end frame whose first edge
    comes before that is not heard, one after it is. 5 ns steps around the boundary,
    measured from our TP_IDL's end (our last edge). A far end that keeps the 96-bit
    gap is far past it."""
    t0, _ = solo_reply_start(clock)
    t_end = t0 + REPLY_NS + 300.0                 # our TP_IDL ends here: 3 bit times after a 0
    dropped, heard = [], []
    for off in [-30000, -2000, -400, 0, 200] + list(range(400, 561, 5)) + [1000, 9600]:
        b = Bench(clock)
        b.send(far_frame(2000), at_ns=10000.0)
        first_edge_at(b, far_frame(3000, seed=2), t_end + off)
        b.run_to(t_end + off + 3 * REPLY_NS + 3 * IFG)
        assert abs(b.td(b.replies()[0].t1) - t_end) < 1e-6
        got = 3000 in b.records()
        (heard if got else dropped).append(off)
        assert len(b.replies()) == 1 + got and b.frames() == [ethreply.reply_frame()] * (1 + got)
    print(f"\n{clock / 1e6:.0f} MHz: a frame whose first edge comes up to {max(dropped)} ns after our "
          f"TP_IDL's end is not heard; {min(heard)} ns is")
    assert all(x < min(heard) for x in dropped)
    assert (max(dropped), min(heard)) == CUTOFF_NS[clock]


CUTOFF_NS = {50e6: (480, 485), 40e6: (500, 505)}
