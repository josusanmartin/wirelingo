"""WS2812 driver: decode the waveform from pulse widths and check the timing."""
import pathlib

from pe import asm, host
from pe.modelhost import ModelHost

FW = pathlib.Path(__file__).resolve().parents[2] / "firmware"
DOUT = 8  # uo0


def test_ws2812_waveform():
    mh = ModelHost()
    prog = asm.assemble((FW / "ws2812.pasm").read_text())
    mh.apply(host.load_program(0, prog, side_pins=DOUT, clkdiv=6 << 8))  # 48 MHz / 6 = 8 MHz
    mh.start(1)
    colours = [0xFF0000, 0x00A55A, 0x123456]
    for c in colours:
        mh.push(0, c << 8)
    level, t_edge, highs, lows = 0, 0, [], []
    for t in range(40 * 60 * 6):
        mh.step()
        v = (mh.chip.pin_out >> DOUT) & 1
        if v != level:
            (highs if level else lows).append(t - t_edge)
            level, t_edge = v, t
    bits = []
    for h in highs:
        ns = h * 1000 / 48
        assert 250 <= ns <= 550 or 700 <= ns <= 1000, ns   # T0H / T1H windows (datasheet +-150 ns)
        bits.append(1 if ns > 600 else 0)
    period = [(h + l) * 1000 / 48 for h, l in zip(highs, lows[1:])]
    assert all(1100 <= p <= 1400 for p in period), period[:5]
    got = [int("".join(map(str, bits[24 * i:24 * i + 24])), 2) for i in range(len(colours))]
    assert got == colours, [hex(g) for g in got]
