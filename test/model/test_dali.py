"""DALI application controller (firmware/dali_ctrl.pasm, post-freeze) on the
cycle model.

Judges: pe.postfreeze2.dali_decode (pins only, IEC 62386-101 ed. 2 receiver
ranges), an independent control-gear model (pe.postfreeze2.DaliGear) that
decodes the forward frames and answers with backward frames at its own bit
rate and settling time, and sigrok's dali decoder.

The chip runs at 2400 * TE * CLKDIV Hz (76.8 kHz with CLKDIV 1, 307.2 kHz with
CLKDIV 4: the bus is then sampled every 3.3 us), tick for tick the waveform of
50 MHz / CLKDIV 651.04; test_dali_50mhz runs one frame at 50 MHz."""
import pathlib

import pytest

from pe import asm, host, postfreeze as pf, postfreeze2 as p2, sigrok, sim
from pe.modelhost import ModelHost

FW = pathlib.Path(__file__).resolve().parents[2] / "firmware"
TX, RX = 8, 16          # uo0, ui0
TE = 32
TICK = p2.DALI_TE / TE  # ns


class Rig:
    """dali_ctrl on core 0; the bus = TX AND every gear's drive AND an
    optional other controller (a Player)."""

    def __init__(self, gears=(), f_clk=2400 * TE, other=None, start=True):
        self.mh = mh = ModelHost()
        prog = asm.assemble((FW / "dali_ctrl.pasm").read_text(), p2.dali_defines(TX, RX, TE))
        assert len(prog.words) <= 256
        mh.apply(host.load_program(0, prog, clkdiv=p2.dali_clkdiv(f_clk, TE)))
        mh.write(sim.A_POUT, 1 << TX)
        mh.ui = 1
        if start:
            mh.start(1)
        self.period = 1e9 / f_clk
        self.gears, self.other = list(gears), other
        self.bus, self.tx, self.words = [(0.0, 1)], [(0.0, 1)], []

    def run(self, ns):
        mh, chip = self.mh, self.mh.chip
        for _ in range(int(ns / self.period)):
            t = chip.cycle * self.period
            tx = (chip.pin_out >> TX) & 1
            bus = tx
            for g in self.gears:
                bus &= g.player.level(t)
            if self.other is not None:
                bus &= self.other.level(t)
            for g in self.gears:
                g.step(t, bus)
            if bus != self.bus[-1][1]:
                self.bus.append((t, bus))
            if tx != self.tx[-1][1]:
                self.tx.append((t, tx))
            mh.ui = bus
            mh.step()
            if chip.cores[0].rxf:
                self.words += mh.pop_all(0)

    def now(self):
        return self.mh.chip.cycle * self.period

    def command(self, *words, wait=60e6):
        for w in words:
            self.mh.push(0, w)
        self.run(wait)
        return p2.dali_records(self.words)


def query(answers):
    """An answer() for DaliGear: a dict {forward value: byte}."""
    return lambda value, n: answers.get(value)


def test_dali_forward_frames():
    """16- and 24-bit forward frames: decoded by the pins-only decoder, every
    run between edges exactly Te or 2 Te (32 or 64 ticks), and forward frames
    at least 14.0 ms apart (DALI-2: 13.5 ms at the highest priority)."""
    rig = Rig()
    frames = [(0xFE80, 16), (0x0105, 16), (0xA300, 16), (0xFFFF, 16), (0x0000, 16), (0xC1_0203, 24), (0x5A, 8)]
    for v, n in frames:
        rig.mh.push(0, p2.dali_word(v, n))
    rig.run(len(frames) * 40e6)
    got = p2.dali_decode(rig.tx, rig.now())
    assert [(f["value"], f["n"], f["error"]) for f in got] == [(v, n, None) for v, n in frames]
    ticks = {round(d / TICK) for f in got for d in f["runs"]}
    assert ticks <= {32, 64}, ticks
    assert all(abs(d / TICK - round(d / TICK)) < 1e-6 for f in got for d in f["runs"])
    gaps = [b["t"] - (a["t"] + sum(a["runs"])) for a, b in zip(got, got[1:])]
    print(f"\nDALI forward frames: {[hex(f['value']) for f in got]}, runs {sorted(ticks)} ticks, "
          f"idle between frames {min(gaps) / 1e6:.3f}..{max(gaps) / 1e6:.3f} ms")
    assert min(gaps) >= 14.0e6 and not rig.words


@pytest.mark.parametrize("settle", [5.5e6, 8e6, 10.4e6])
def test_dali_query_answer(settle):
    """Query frames answered by the gear model: the answer byte, 9 bits
    (start bit included), and the reported settling time 0..2 ticks above the
    gear's; an unanswered query is 'no answer'."""
    gear = p2.DaliGear(query({0x0190: 0xAB, 0xFF91: 0x00, 0x0399: 0xFF}), settle=settle)
    rig = Rig([gear], f_clk=2400 * TE * 4)
    recs = rig.command(p2.dali_word(0x0190, reply=True), p2.dali_word(0xFF91, reply=True),
                       p2.dali_word(0x0399, reply=True), p2.dali_word(0x0590, reply=True), wait=240e6)
    print(f"\nsettle {settle / 1e6} ms: {[(r['answer'], r['n'], r['settle_ticks'] * TICK / 1e6) for r in recs]}")
    assert [r["answer"] for r in recs] == [0xAB, 0x00, 0xFF, None]
    assert [r["n"] for r in recs[:3]] == [9, 9, 9] and not any(r["violation"] for r in recs)
    assert recs[3]["no_answer"] and not any(r["no_answer"] for r in recs[:3])
    assert all(0 <= r["settle_ticks"] * TICK - settle <= 2 * TICK for r in recs[:3])   # detection: up to 2 ticks late
    assert [f[1] for f in gear.frames] == [0x0190, 0xFF91, 0x0399, 0x0590]


@pytest.mark.parametrize("te_us,low_extra_us", [(336, 0), (366.7, 0), (416.67, 0), (466.7, 0), (498, 0),
                                                (416.67, 75), (416.67, -75), (380, 40), (450, -40)])
def test_dali_answer_tolerance(te_us, low_extra_us):
    """Backward frames from gear at the ends of the receiver ranges (IEC
    62386-101 ed. 2: half bit 333.3..500 us, double 666.7..1000 us), also
    with asymmetric edges (low phases longer or shorter, every run still in
    range), at the earliest and latest settling times. Every answer is
    decoded. (336 and 498 us, not 333.3 and 500: the bus is sampled every
    3.3 us, and the reference decoder must find the waveform legal.)"""
    answers = [0x00, 0xFF, 0x55, 0xAA, 0x81, 0x7E]
    out = []
    for settle in (5.5e6, 10.4e6):
        gear = p2.DaliGear(lambda v, n: v & 0xFF, settle=settle, te=te_us * 1e3, low_extra=low_extra_us * 1e3)
        rig = Rig([gear], f_clk=2400 * TE * 4)
        recs = rig.command(*[p2.dali_word(0x0100 | a, reply=True) for a in answers], wait=len(answers) * 55e6)
        # the waveform the gear made is legal by the standard's own ranges
        bw = [f for f in p2.dali_decode(rig.bus, rig.now()) if f["n"] == 8]
        assert [f["value"] for f in bw] == answers and not any(f["error"] for f in bw)
        out.append([r["answer"] for r in recs])
    assert out == [answers, answers], out


def test_dali_answer_violations():
    """Negative controls. Each must not produce an answer: half bits of
    250 us; one 'double half bit' of 1300 us (a violation in IEC 62386-101
    ed. 2 table 19); a bit without its mid-bit transition; a backward frame
    of 5 bits (well formed, but not 8: the host sees n = 6); a reply after
    11 ms (outside the window: 'no answer', and the next frame waits for the
    bus); the line held low for 3 ms (no stop condition). Then a good answer."""
    te = p2.DALI_TE
    long_double = p2.dali_wave(0x00, 8)
    assert long_double[1] == (1, 2 * te)
    long_double[1] = (1, 1300e3)
    waves = {
        0x0101: p2.dali_wave(0x5A, 8, te=250e3),
        0x0102: long_double,
        0x0103: [(0, te), (1, 3 * te), (0, te), (1, te)] + [(0, te), (1, te)] * 7,
        0x0104: p2.dali_wave(0b10110, 5),
        0x0105: None,
        0x0106: [(0, 3e6)],
        0x0107: p2.dali_wave(0x3C, 8),
    }
    cases = list(waves)
    gear = p2.DaliGear(lambda v, n: waves.get(v), settle=7e6)
    late = p2.DaliGear(lambda v, n: 0x99 if v == 0x0105 else None, settle=11.2e6)
    rig = Rig([gear, late], f_clk=2400 * TE * 4)
    recs = rig.command(*[p2.dali_word(v, reply=True) for v in cases], wait=len(cases) * 60e6)
    got = {v: r for v, r in zip(cases, recs)}
    for v, r in got.items():
        print(f"  {v:#06x}: n {r['n']}, violation {r['violation']}, no answer {r['no_answer']}, "
              f"answer {r['answer']}")
    assert all(got[v]["answer"] is None for v in list(cases)[:-1])
    assert got[0x0101]["violation"] and got[0x0102]["violation"] and got[0x0103]["violation"]
    assert got[0x0104]["n"] == 6 and not got[0x0104]["violation"]
    assert got[0x0105]["no_answer"] and late.sent
    assert got[0x0106]["violation"]
    assert got[0x0107]["answer"] == 0x3C
    # the frame after the late answer started >= 14 ms after the bus went idle
    fw = [f for f in p2.dali_decode(rig.tx, rig.now()) if f["n"] == 16]
    late_end = late.sent[0][0] + 9 * 2 * p2.DALI_TE
    nxt = next(f for f in fw if f["t"] > late.sent[0][0])
    assert nxt["t"] - late_end >= 14e6 - 2 * p2.DALI_TE


def test_dali_collision():
    """Two gear answer a query at once (wired-AND bus). Our decoder of the
    bus decides what the waveform is; the firmware must agree with it: a
    violation where the decoder finds one, else the same byte. DALI-2
    controllers take a violated backward frame as 'several answers'."""
    outcomes = []
    for a, b in [(0x0F, 0xF0), (0x80, 0x01), (0xFF, 0x00), (0x01, 0x03), (0x12, 0x12)]:
        g1 = p2.DaliGear(lambda v, n, a=a: a, settle=7e6)
        g2 = p2.DaliGear(lambda v, n, b=b: b, settle=7e6)
        rig = Rig([g1, g2], f_clk=2400 * TE * 4)
        [r] = rig.command(p2.dali_word(0xFF99, reply=True), wait=50e6)
        bw = [f for f in p2.dali_decode(rig.bus, rig.now()) if f["t"] > 20e6]
        ref = bw[0]
        outcomes.append((a, b, ref["error"], ref["value"] if not ref["error"] else None, r["violation"], r["answer"]))
        if ref["error"] or ref["n"] != 8:
            assert r["answer"] is None
        else:
            assert r["answer"] == ref["value"]
    print("\n" + "\n".join(f"  {a:#04x} & {b:#04x}: decoder {e or v}, firmware violation {viol} answer {ans}"
                           for a, b, e, v, viol, ans in outcomes))
    assert any(o[4] for o in outcomes) and outcomes[-1][5] == 0x12


def test_dali_bus_busy():
    """Another controller starts a frame on the bus; ours, queued meanwhile,
    waits until the bus has been idle for 14 ms after it."""
    other = p2.Player()
    other.schedule(20e6, p2.dali_wave(0xFE05, 16))
    rig = Rig(other=other)
    rig.run(22e6)
    rig.mh.push(0, p2.dali_word(0xFE80))
    rig.run(60e6)
    fr = p2.dali_decode(rig.bus, rig.now())
    assert [f["value"] for f in fr] == [0xFE05, 0xFE80] and not any(f["error"] for f in fr)
    gap = fr[1]["t"] - (fr[0]["t"] + sum(fr[0]["runs"]))
    print(f"\nours started {gap / 1e6:.2f} ms after the other controller's last edge")
    assert gap >= 14.0e6


def test_dali_50mhz():
    """At 50 MHz (CLKDIV 651.04): every half bit 20833 or 20834 clocks
    (416.66 / 416.68 us), a run of two 41666..41668."""
    rig = Rig(f_clk=50e6)
    rig.mh.push(0, p2.dali_word(0xA5C3))
    rig.run(14.1e6 + 14.2e6 + 2.6e6)     # SETTLE, the frame, the stop condition
    [f] = p2.dali_decode(rig.tx, rig.now())
    clk = sorted({round(d / 20) for d in f["runs"]})
    print(f"\nDALI at 50 MHz: {hex(f['value'])}, runs {clk} clocks")
    assert f["value"] == 0xA5C3 and not f["error"]
    assert set(clk) <= {20833, 20834, 41666, 41667, 41668}


@pytest.mark.skipif(sigrok.cli() is None, reason=sigrok.INSTALL_HINT)
def test_sigrok_dali():
    """sigrok's dali decoder on the bus: our forward frames and the gear's
    backward frames."""
    gear = p2.DaliGear(query({0x0190: 0x42, 0x03A0: 0xFE}), settle=8e6)
    rig = Rig([gear], f_clk=2400 * TE * 4)
    rig.command(p2.dali_word(0xFE7F), p2.dali_word(0x0190, reply=True), p2.dali_word(0x03A0, reply=True),
                p2.dali_word(0x0105), wait=200e6)
    tr = sigrok.Trace()
    pf.to_trace(tr, rig.bus, "dali", rig.now())
    got = sigrok.dali(tr, "dali", res_ps=100_000)
    s = [(k, b.hex()) for _, k, b in got]
    print(f"\nsigrok dali: {s}")
    assert s == [("forward", "fe7f"), ("forward", "0190"), ("backward", "42"), ("forward", "03a0"),
                 ("backward", "fe"), ("forward", "0105")]
