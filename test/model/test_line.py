"""The line unit (docs/isa.md §4.7) on the model: core 0 transmits through it
onto virtual pins and receives its own transmission back, in USB-style
(NRZI, stuffing, SE0 end), 10BASE-T-style (Manchester) and CAN-style (NRZ,
stuffing) configurations. The received bits must equal the sent ones, the
frame end must be reported, and the USB-style waveform must decode with the
independent USB reference decoder."""
import random

import pytest

from pe import asm, host, sim, usb
from pe.modelhost import ModelHost

TXP = 24                      # virtual pins 24/25: wires between the unit's TX and RX
PROG = """
    pull block
    out line, 32
    pull block
    out line, 32
rx:
    in line, 8
    jmp t, done
    push block
    jmp rx
done:
    push block              ; the partial byte at the frame end
    halt
"""


def cfg(code, stuff_k=0, ones=0, diff=1, end=0, end_bits=0, idle=0):
    return (1 | (code << 1) | (stuff_k << 3) | (ones << 6) | (diff << 7) | (end << 8)
            | (end_bits << 10) | (idle << 13) | (1 << 14))


def run(linecfg, tx_div, rx_div, data_bits, trace=None):
    """Send 64 data bits (two OUT LINE entries) and return the received bits."""
    mh = ModelHost()
    prog = asm.assemble(".shift_out right\n.shift_in right\n" + PROG)
    mh.apply(host.load_program(0, prog, 0, clkdiv=1 << 8))
    mh.write(sim.A_LINEDIV, tx_div | (rx_div << 16))
    mh.write(sim.A_LINEPINS, TXP | (TXP << 8))
    idle, diff = (linecfg >> 13) & 1, (linecfg >> 7) & 1
    mh.write(sim.A_POUT, (idle << TXP) | ((1 - idle) * diff << (TXP + 1)))   # the line rests at IDLE
    mh.write(sim.A_LINECFG, linecfg)
    words = [sum(b << i for i, b in enumerate(data_bits[32 * w:32 * w + 32])) for w in range(2)]
    for w in words:
        mh.push(0, w)
    mh.start(1)
    out = []
    for _ in range(64 * (tx_div >> 8) * 4 + 4000):
        mh.step()
        if trace is not None:
            trace.append((mh.chip.eff_pins()[0] >> TXP) & 3)
        if mh.chip.cores[0].halted:
            break
    words = mh.pop_all(0)
    assert mh.chip.cores[0].halted, "no frame end"
    bits = []
    for w in words[:-1]:
        bits += [(w >> (24 + i)) & 1 for i in range(8)]     # right shift: byte in [31:24]
    last = words[-1]
    tail = (len(data_bits) - len(bits))
    bits += [(last >> (32 - tail + i)) & 1 for i in range(tail)]
    return bits, mh


@pytest.mark.parametrize("seed", range(4))
def test_usb_style_nrzi_stuffing_se0(seed):
    rng = random.Random(seed)
    data = [0] + [rng.getrandbits(1) for _ in range(31)] + [1] * 20 + [rng.getrandbits(1) for _ in range(12)]
    trace = []
    got, mh = run(cfg(1, stuff_k=6, ones=1, end=1, end_bits=2, idle=0), 32 << 8, 32, data, trace)
    assert got == data
    assert mh.chip.read(sim.A_LINESTAT) >> 16 & 1 == 0          # transmitter released
    # the waveform, sampled mid-bit, decodes with the USB reference (J = D- high)
    k = next(i for i, v in enumerate(trace) if v != 0b10)
    states = [{0b10: usb.J, 0b01: usb.K, 0b00: usb.SE0}[trace[j]] for j in range(k + 16, len(trace), 32)]
    states = states[:states.index(usb.SE0) + 3]
    bits = usb.nrzi_destuff([usb.J] + states)
    assert bits[:len(data)] == data


@pytest.mark.parametrize("half,seed", [(2, 0), (2, 1), (5, 2)])
def test_manchester(half, seed):
    rng = random.Random(seed)
    data = [1] + [rng.getrandbits(1) for _ in range(63)]     # first half = idle (low): a preamble-like start
    got, _ = run(cfg(2, end=2, end_bits=3), half << 8, 2 * half, data)
    assert got == data


def test_can_style_nrz_stuffing():
    rng = random.Random(3)
    data = [0] + [0] * 9 + [1] * 11 + [rng.getrandbits(1) for _ in range(43)]
    got, _ = run(cfg(0, stuff_k=5, ones=0, diff=0, idle=1), 24 << 8, 24, data)
    assert got == data
