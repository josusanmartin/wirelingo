"""I2S transmitter (firmware/i2s_tx.pasm, post-freeze) on the cycle model.

Judges: pe.postfreeze.I2sReceiver (pins only) and sigrok's i2s decoder. At
50 MHz the 48 kHz bit clock needs a fractional divider (16.28 clocks per
tick): the test measures what that does to BCLK and the frame rate."""
import pathlib
import random

import pytest

from pe import asm, host, postfreeze as pf, sigrok
from pe.modelhost import ModelHost

FW = pathlib.Path(__file__).resolve().parents[2] / "firmware"
BCLK, WS, SD = 8, 9, 10        # uo0..uo2
F_CLK, FS = 50e6, 48000
PERIOD = 1e9 / F_CLK


def setup():
    mh = ModelHost()
    prog = asm.assemble((FW / "i2s_tx.pasm").read_text())
    mh.apply(host.load_program(0, prog, out_pins=(SD, 1), side_pins=BCLK,
                               clkdiv=host.clkdiv_for(F_CLK, 64 * FS)))
    mh.write(0x400 + 0xA, 0x1234)          # R0: the firmware must zero it itself
    mh.start(1)
    return mh, pf.PinLog(mh, pf.pin_probe(BCLK, WS, SD), PERIOD)


def play(log, frames, gap_after=None, gap_frames=0):
    log.step(int(3 * F_CLK / FS))          # 3 frames of silence first (R0 = 0, not the 0x1234 preloaded)
    q = [pf.i2s_word(l, r) for l, r in frames]
    sent = 0
    while q:
        if log.mh.tx_level(0) < 8 and not (gap_after is not None and sent == gap_after and gap_frames):
            log.push(0, q.pop(0))
            sent += 1
        else:
            log.step(64)
            if gap_after is not None and sent == gap_after and gap_frames:
                while log.mh.tx_level(0):                 # the queued frames play out
                    log.step(64)
                log.step(int(gap_frames * F_CLK / FS))
                gap_frames = 0
    while log.mh.tx_level(0):
        log.step(64)
    log.step(int(3 * F_CLK / FS))


def test_i2s_samples_and_timing():
    mh, log = setup()
    rng = random.Random(1)
    frames = [(rng.randrange(-32768, 32768), rng.randrange(-32768, 32768)) for _ in range(40)]
    frames[:3] = [(0x7FFF, -0x8000), (1, -1), (0, 0x5555)]
    play(log, frames)
    words, rises = pf.I2sReceiver().decode(log.log)
    assert all(n == 16 for _, _, n, _ in words), {n for _, _, n, _ in words}
    lr = [(c, v) for c, v, _, _ in words]
    want = sum([[("L", l & 0xFFFF), ("R", r & 0xFFFF)] for l, r in frames], [])
    i = lr.index(want[0])                  # the frames before ours are the silence (R0 = 0)
    assert all(x == ("L", 0) or x == ("R", 0) for x in lr[:i]), lr[:i]
    assert lr[i:i + len(want)] == want
    # BCLK: 2 ticks per period; each tick is 16 or 17 clocks (CLKDIV 16 + 71/256)
    per = [round((b - a) / PERIOD) for a, b in zip(rises, rises[1:])]
    assert set(per) <= {32, 33, 34}, set(per)
    fs = (len(rises) - 1) / 32 / ((rises[-1] - rises[0]) * 1e-9)
    print(f"\nI2S at 50 MHz: BCLK periods {sorted(set(per))} clk, fs = {fs:.1f} Hz "
          f"(divider {host.clkdiv_for(F_CLK, 64 * FS) / 256:.4f} for {F_CLK / 64 / FS:.4f})")
    assert abs(fs - FS) / FS < 1e-4


def test_i2s_underrun_keeps_clocks():
    """The host stops for 10 frames: BCLK and WS keep going, the frames in the
    gap are silence, and playback resumes on a frame boundary."""
    mh, log = setup()
    frames = [(1000 + i, -1000 - i) for i in range(16)]
    play(log, frames, gap_after=8, gap_frames=10)
    words, rises = pf.I2sReceiver().decode(log.log)
    per = [round((b - a) / PERIOD) for a, b in zip(rises, rises[1:])]
    assert max(per) <= 34
    vals = [(c, v) for c, v, _, _ in words]
    want = sum([[("L", l & 0xFFFF), ("R", r & 0xFFFF)] for l, r in frames], [])
    first = vals.index(want[0])
    got = vals[first:]
    nz = [x for x in got if x[1] != 0]
    assert nz[:len(want)] == want
    zeros_between = got.index(want[16]) - 16
    assert zeros_between >= 18, zeros_between          # >= 9 silent frames in the gap


@pytest.mark.skipif(sigrok.cli() is None, reason=sigrok.INSTALL_HINT)
def test_sigrok_i2s():
    mh, log = setup()
    rng = random.Random(2)
    frames = [(rng.randrange(-32768, 32768), rng.randrange(-32768, 32768)) for _ in range(24)]
    play(log, frames)
    tr = sigrok.Trace()
    for i, name in enumerate(("bclk", "ws", "sd")):
        pf.to_trace(tr, pf.bit_edges(log.log, i), name)
    tr.end(round(log.now() * 1000))
    got, warn = sigrok.i2s(tr, "bclk", "ws", "sd", res_ps=10_000)
    vals = [(c, v) for _, c, v in got]
    want = sum([[("L", l & 0xFFFF), ("R", r & 0xFFFF)] for l, r in frames], [])
    i = vals.index(want[0])
    print(f"\nsigrok i2s: {len(got)} words, first {got[i:i + 4]}, warnings {warn}")
    assert vals[i:i + len(want)] == want and not warn
