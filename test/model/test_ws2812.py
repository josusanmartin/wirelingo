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


def waveform(clkdiv, colours, clocks):
    """(highs, lows) in clocks of DOUT for the colours."""
    mh = ModelHost()
    prog = asm.assemble((FW / "ws2812.pasm").read_text())
    mh.apply(host.load_program(0, prog, side_pins=DOUT, clkdiv=clkdiv))
    mh.start(1)
    for c in colours:
        mh.push(0, c << 8)
    level, t_edge, highs, lows = 0, 0, [], []
    for t in range(clocks):
        mh.step()
        v = (mh.chip.pin_out >> DOUT) & 1
        if v != level:
            (highs if level else lows).append(t - t_edge)
            level, t_edge = v, t
    return highs, lows[1:]


def test_ws2812_bit_timing_is_the_documented_one():
    """The header's timing, exactly, at 48 MHz (CLKDIV 6, 8 MHz ticks): a 0 is
    3 ticks high and 7 low (375 / 875 ns), a 1 is 7 high and 3 low. The code
    used to drive a 0 for 2 ticks (250 ns, the bottom of the datasheet window),
    which a fractional divider (6.25 at 50 MHz) pushed out of it (240 ns; found
    by fpga/kv260/host/legacy_demo.py's model run at 50 MHz, before the KV260 run)."""
    highs, lows = waveform(6 << 8, [0x0F0F0F, 0xF0F0F0], 2 * 24 * 60 + 200)
    assert sorted(set(highs)) == [18, 42], sorted(set(highs))
    assert all(h + lo == 60 for h, lo in zip(highs, lows)), "a bit is 10 ticks"
    # 50 MHz, CLKDIV 6.25: every high inside WS2812B's T0H / T1H windows (400 / 800 +-150 ns)
    highs, lows = waveform((6 << 8) | 64, [0x0F0F0F, 0xF0F0F0, 0x123456], 3 * 24 * 63 + 200)
    ns = [h * 20 for h in highs]
    assert all(250 <= x <= 550 or 650 <= x <= 950 for x in ns), sorted(set(ns))
