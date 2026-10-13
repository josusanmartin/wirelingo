"""DShot600 transmitter (firmware/dshot600.pasm, post-freeze) on the cycle model.

Judges: pe.postfreeze.dshot_decode (pins only: bit = rising edge to rising
edge, 1 if high for more than half a bit; the checksum recomputed from
Betaflight's rule) and sigrok's pwm decoder (duty cycle per period; sigrok has
no DShot decoder, so the bits are read from its duty cycles). Timing at
48 MHz (CLKDIV 10, exact) and 50 MHz (10.42, fractional)."""
import pathlib
import random

import pytest

from pe import asm, host, postfreeze as pf, sigrok
from pe.modelhost import ModelHost

FW = pathlib.Path(__file__).resolve().parents[2] / "firmware"
ESC = 8                    # uo0
BIT_NS = 1e9 / 600e3       # 1666.7
GAP = 454                  # 600-tick frames: 125 us


def setup(f_clk, gap=GAP):
    mh = ModelHost()
    prog = asm.assemble((FW / "dshot600.pasm").read_text(), {"GAP": gap})
    mh.apply(host.load_program(0, prog, side_pins=ESC, clkdiv=host.clkdiv_for(f_clk, 8 * 600e3)))
    mh.write(0x400 + 0xA, 0)               # R0 = 0: motor stop until the host sends a value
    mh.start(1)
    return mh, pf.PinLog(mh, pf.pin_probe(ESC), 1e9 / f_clk)


def run(log, values, frames_each=1):
    """Push each value when the previous has been used for frames_each frames
    (16 rising edges per frame)."""
    for v in values:
        log.push(0, v)
        start = len(log.log)
        target = 16 * frames_each
        while sum(1 for t, b in log.log[start:] if b == 1) < target:
            log.step(100)
    log.step(int(130e3 / log.period))


@pytest.mark.parametrize("f_clk", [48e6, 50e6])
def test_dshot_frames(f_clk):
    mh, log = setup(f_clk)
    rng = random.Random(int(f_clk))
    vals = [(0, 0), (48, 0), (1047, 1), (2047, 0), (1, 1)] + [(rng.randrange(2048), rng.randrange(2)) for _ in range(10)]
    run(log, [(t << 1) | k for t, k in vals])
    frames = pf.dshot_decode(log.edges(), log.now(), BIT_NS)
    got = [pf.bits_value(b) for _, b, _, _ in frames]
    want = [pf.dshot_frame(t, k) for t, k in vals]
    assert all(len(b) == 16 for _, b, _, _ in frames)
    i = got.index(want[0])
    # each value appears (at least once, more if the frame repeated before the next push)
    dedup = [g for j, g in enumerate(got[i:]) if j == 0 or g != got[i + j - 1]]
    assert dedup[:len(want)] == want, [hex(x) for x in dedup[:len(want)]]
    clk = 1e9 / f_clk
    highs1 = [h for _, b, hs, _ in frames for x, h in zip(b, hs) if x]
    highs0 = [h for _, b, hs, _ in frames for x, h in zip(b, hs) if not x]
    pers = [p for _, _, _, ps in frames for p in ps]
    starts = [t for t, _, _, _ in frames]
    fper = [b - a for a, b in zip(starts, starts[1:])]
    print(f"\nDShot600 at {f_clk / 1e6:g} MHz: T1H {min(highs1):.1f}..{max(highs1):.1f} ns (1250), "
          f"T0H {min(highs0):.1f}..{max(highs0):.1f} ns (625), bit {min(pers):.1f}..{max(pers):.1f} ns "
          f"(1666.7), frame period {min(fper) / 1e3:.3f}..{max(fper) / 1e3:.3f} us")
    assert abs(min(highs1) - 1250) <= clk and abs(max(highs1) - 1250) <= clk
    assert abs(min(highs0) - 625) <= clk and abs(max(highs0) - 625) <= clk
    assert abs(min(pers) - BIT_NS) <= clk and abs(max(pers) - BIT_NS) <= clk
    assert all(abs(p - 125000) <= clk for p in fper)


def test_dshot_repeats_last_value():
    """With the FIFO empty the last value keeps going out, one frame per period."""
    mh, log = setup(48e6)
    log.push(0, (1000 << 1))
    log.step(48 * 1000)                      # 1 ms: 8 frames
    frames = pf.dshot_decode(log.edges(), log.now(), BIT_NS)
    vals = [pf.bits_value(b) for _, b, _, _ in frames]
    assert vals.count(pf.dshot_frame(1000)) >= 6 and set(vals) <= {pf.dshot_frame(0), pf.dshot_frame(1000)}


@pytest.mark.skipif(sigrok.cli() is None, reason=sigrok.INSTALL_HINT)
def test_sigrok_dshot_pwm():
    """sigrok's pwm decoder: each bit is a period (rise to rise) with a duty
    of 75 % (1) or 37.5 % (0); the last bit of a frame runs into the gap, so
    its duty is read from the high time alone. The frames are rebuilt from
    sigrok's numbers and their checksums recomputed."""
    mh, log = setup(48e6)
    vals = [(t << 1) | k for t, k in [(1500, 0), (300, 1), (2047, 1), (0, 0)]]
    run(log, vals)
    tr = pf.to_trace(sigrok.Trace(), log.edges(), "esc", log.now())
    bits, frames, pers = [], [], []
    # sigrok's periods: rise to rise; a period much longer than a bit is the frame gap
    for t0, t1, d in sigrok.pwm(tr, "esc", res_ps=10_000):
        per = t1 - t0
        if per < 2 * BIT_NS:
            pers.append(per)
            assert abs(d - 75) < 2 or abs(d - 37.5) < 2, d
            bits.append(1 if d > 50 else 0)
        else:                                 # last bit: high time = duty * period
            bits.append(1 if d / 100 * per > BIT_NS / 2 else 0)
            frames.append(pf.bits_value(bits))
            bits = []
    assert all(abs(p - BIT_NS) < 25 for p in pers), (min(pers), max(pers))
    want = [pf.dshot_frame(v >> 1, v & 1) for v in vals]
    dedup = [f for j, f in enumerate(frames) if j == 0 or f != frames[j - 1]]
    print(f"\nsigrok pwm -> DShot frames: {[hex(f) for f in dedup]}")
    i = dedup.index(want[0])
    assert dedup[i:i + len(want)] == want
    for f in dedup[i:]:
        v = f >> 4
        assert (v ^ (v >> 4) ^ (v >> 8)) & 0xF == f & 0xF
