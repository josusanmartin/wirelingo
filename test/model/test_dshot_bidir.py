"""Bidirectional DShot600 (firmware/dshot_bidir.pasm, post-freeze) on the
cycle model: inverted frames out, the ESC's GCR telemetry back on the same
wire, 30 us later at 750 kbit/s.

Judges: an independent pins-only ESC (pe.postfreeze.BidirEsc: decodes the
frames, checks the inverted checksum, answers with telemetry encoded from
the eRPM period; it also counts contention), the host-side decoding of the
firmware's 21 recovered levels (GCR, checksum), and sigrok's pwm decoder
(active low) for the frames. sigrok has no DShot decoder, so the telemetry's
only judges are our own ESC model and decoder: they share the reading of the
encoding (Betaflight's and BLHeli_32's, GCR as in pe.postfreeze.GCR), which
was not checked against a real ESC."""
import pathlib

import pytest

from pe import asm, host, postfreeze as pf, sigrok
from pe.modelhost import ModelHost

FW = pathlib.Path(__file__).resolve().parents[2] / "firmware"
ESC = 0                    # uio0, pulled up


def setup(f_clk=48e6, **kw):
    mh = ModelHost()
    prog = asm.assemble((FW / "dshot_bidir.pasm").read_text(), pf.dshot_bidir_defines(f_clk, ESC, **kw))
    mh.apply(host.load_program(0, prog, side_pins=ESC, in_pins=ESC, clkdiv=1 << 8))
    mh.write(0x400 + 0xA, 0)              # R0 = 0: motor stop until the host sends a value
    mh.pull_up = 1 << ESC
    mh.start(1)
    return mh


def run(mh, esc, values, f_clk=48e6, frames_each=2, log=None):
    """Push each value, run frames_each frames per value; the ESC model on the wire."""
    period = 1e9 / f_clk
    recs = []
    log = log or pf.PinLog(mh, lambda m: m.bus_uio() & 1, period)
    for v in values:
        log.push(0, v)
        n0 = len(esc.frames) + frames_each
        while len(esc.frames) < n0:
            for _ in range(200):
                drive, lvl = esc.step(mh.chip.cycle * period, mh.bus_uio() & 1)
                mh.ext_oe, mh.uio = drive << ESC, lvl << ESC
                log.step()
            recs += log.pop_all(0)
    for _ in range(int(150e3 / period)):
        drive, lvl = esc.step(mh.chip.cycle * period, mh.bus_uio() & 1)
        mh.ext_oe, mh.uio = drive << ESC, lvl << ESC
        log.step()
    recs += log.pop_all(0)
    return recs, log


PERIODS = [100, 511, 512, 1000, 20000, 65000]


@pytest.mark.parametrize("rate,turn", [(1.0, 30e3), (0.92, 30e3), (1.08, 30e3), (0.85, 30e3), (1.15, 30e3),
                                       (1.0, 20e3), (1.0, 60e3)])
def test_bidir_telemetry(rate, turn):
    """Frames with the inverted checksum reach the ESC; its telemetry comes
    back decoded, for an ESC clock up to 15 % slow or fast (it fails from
    about 20 %: the thresholds sit at 1.5 and 2.5 bits and a run is at most
    3 bits) and turnarounds of 20 to 60 us; the chip never drives while the
    ESC does."""
    mh = setup()
    replies = [pf.erpm_telemetry(p) for p in PERIODS for _ in range(2)]
    esc = pf.BidirEsc(replies, turn_ns=turn, rate=rate)
    vals = [(t << 1) for t in (0, 100, 1000, 2047, 48, 1500)]
    recs, _ = run(mh, esc, vals)
    assert all(ok for _, _, ok in esc.frames), esc.frames
    got_frames = [f for _, f, _ in esc.frames]
    want_frames = [pf.dshot_bidir_frame(v >> 1) for v in vals]
    assert [f for f in want_frames if f in got_frames] == want_frames
    tel = [pf.dshot_telemetry(r) for r in recs]
    tel = [t for t in tel if t is not None]
    assert [t["value"] for t in tel] == esc.sent, ([hex(t["value"]) for t in tel], [hex(x) for x in esc.sent])
    assert all(t["checksum_ok"] for t in tel)
    assert [t["period_us"] for t in tel][::2] == [(p >> t["e"]) << t["e"] for p, t in zip(PERIODS, tel[::2])]
    assert esc.contention == 0


@pytest.mark.parametrize("f_clk", [48e6, 50e6])
def test_bidir_no_reply_and_timing(f_clk):
    """An ESC that does not answer: count-0 records, the line pulled up. And
    the frame's timing (CLKDIV 1): lows of exactly T1 / T0 clocks and bits of
    BIT clocks; at 48 MHz low 1250 / 625 ns, bit 1667 ns. At 50 MHz T0 is odd
    (31): the KV260 showed every bit a clock short while the assembler's `/`
    was float division (two truncated halves of T0 - 2)."""
    mh = setup(f_clk)
    esc = pf.BidirEsc([None] * 4)
    recs, log = run(mh, esc, [(500 << 1), (501 << 1)], f_clk=f_clk)
    assert recs and all(r == 0 for r in recs)
    edges = log.edges()
    lows = [b[0] - a[0] for a, b in zip(edges, edges[1:]) if a[1] == 0]
    falls = [t for t, v in edges if v == 0]
    per = [b - a for a, b in zip(falls, falls[1:]) if b - a < 2000]
    print(f"\nbidirectional DShot600 at {f_clk / 1e6:g} MHz: lows {sorted(set(round(x, 1) for x in lows))} ns, "
          f"bit {sorted(set(round(p, 1) for p in per))} ns")
    d, ns = pf.dshot_bidir_defines(f_clk), 1e9 / f_clk
    assert set(round(x, 1) for x in lows) == {round(d["T0"] * ns, 1), round(d["T1"] * ns, 1)}
    assert set(round(p, 1) for p in per) == {round(d["BIT"] * ns, 1)}
    if f_clk == 48e6:
        assert set(round(x, 1) for x in lows) == {625.0, 1250.0}
        assert set(round(p, 1) for p in per) == {1666.7}


def test_bidir_50mhz():
    """At 50 MHz the bit counts round (83 / 31 / 63 clocks): the ESC still
    decodes every frame and the telemetry still comes back."""
    mh = setup(50e6)
    esc = pf.BidirEsc([pf.erpm_telemetry(p) for p in (250, 4000, 300)] * 2)
    recs, _ = run(mh, esc, [(1234 << 1), (77 << 1) | 1, (2000 << 1)], f_clk=50e6)
    tel = [pf.dshot_telemetry(r) for r in recs if r]
    assert all(ok for _, _, ok in esc.frames) and [t["value"] for t in tel] == esc.sent


@pytest.mark.skipif(sigrok.cli() is None, reason=sigrok.INSTALL_HINT)
def test_sigrok_bidir_frames():
    """sigrok's pwm decoder (active low) reads the chip's frames off the wire
    (the ESC's replies in between are left out by time): bits from the duty
    cycle of each falling-edge-to-falling-edge period, checksums recomputed
    in the inverted form."""
    mh = setup()
    esc = pf.BidirEsc([pf.erpm_telemetry(700)] * 8)
    vals = [(1100 << 1), (5 << 1) | 1, (2047 << 1)]
    recs, log = run(mh, esc, vals)
    tr = pf.to_trace(sigrok.Trace(), log.edges(), "dshot", log.now())
    bit = 1e9 / 600e3
    bits, frames, last = [], [], None
    for t0, t1, d in sigrok.pwm(tr, "dshot", polarity="active-low", res_ps=10_000):
        if last is not None and t0 - last > 2 * bit:
            bits = []                           # a new frame (or a reply's edges)
        if abs((t1 - t0) - bit) < 30 and (abs(d - 75) < 2 or abs(d - 37.5) < 2):
            bits.append(1 if d > 50 else 0)
            if len(bits) == 15:                 # the 16th bit's period runs into the gap
                frames.append(bits)
            last = t1
        else:
            bits, last = [], None
    want = {pf.dshot_bidir_frame(v >> 1, v & 1) >> 1 for v in vals}
    got = {pf.bits_value(b) for b in frames}
    print(f"\nsigrok pwm (active low): first 15 bits of {len(frames)} frames: {sorted(hex(g) for g in got)}")
    assert want <= got
