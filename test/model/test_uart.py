"""UART RX/TX on the model: back-to-back bytes with a fractional divider (the
115200-baud bundle divider at 48 MHz is 52.08), from a transmitter model at
+-3 % baud and random phase, and from the on-chip TX over a virtual pin. Back-to-back bytes
put the receiver's return to its start-bit WAIT at the very end of the stop
bit, which an edge WAIT used to miss."""
import pathlib
import random

import pytest

from pe import asm, host, sim
from pe.modelhost import ModelHost

FW = pathlib.Path(__file__).resolve().parents[2] / "firmware"
F_CLK, BAUD = 48e6, 115200
RX = 16          # ui0
V24 = 24         # virtual pin: a wire between cores
DIV = host.clkdiv_for(F_CLK, 8 * BAUD)


def rx_core(mh, core, pin):
    prog = asm.assemble((FW / "uart_rx.pasm").read_text(), {"RX": pin})
    mh.apply(host.load_program(core, prog, in_pins=pin, clkdiv=DIV))


@pytest.mark.parametrize("ppm", [0, 30000, -30000])
def test_uart_rx_back_to_back(ppm):
    mh = ModelHost()
    rx_core(mh, 0, RX)
    mh.ui = 1                                      # idle high
    mh.start(1)
    rng = random.Random(ppm)
    data = bytes(rng.getrandbits(8) for _ in range(64))
    bit = F_CLK / BAUD * (1 + ppm / 1e6)
    t = rng.random() * bit
    got = []
    for b in data:                                 # start, 8 data bits LSB first, stop
        for level in [0] + [(b >> i) & 1 for i in range(8)] + [1]:
            mh.ui = level
            t += bit
            n = int(t)
            t -= n
            mh.step(n)
            got += mh.pop_all(0)
    mh.step(2000)
    got += mh.pop_all(0)
    assert bytes(got) == data and mh.chip.flags & 1 == 0


def test_uart_tx_to_rx_over_virtual_pin():
    mh = ModelHost()
    tx = asm.assemble((FW / "uart_tx.pasm").read_text())
    mh.apply(host.load_program(0, tx, out_pins=(V24, 1), side_pins=V24, clkdiv=DIV))
    rx_core(mh, 1, V24)
    mh.start(0b11)
    data = bytes(range(0, 256, 7))
    got = []
    for b in data:
        while mh.tx_level(0) >= 8:
            mh.step(50)
            got += mh.pop_all(1)
        mh.push(0, b)
    for _ in range(400):
        mh.step(100)
        got += mh.pop_all(1)
    assert bytes(got) == data
