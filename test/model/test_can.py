"""CAN receiver firmware vs a CAN transmitter model (bit stuffing, CRC15, ACK slot)."""
import pathlib
import random

import pytest

from pe import asm, host, sim
from pe.modelhost import ModelHost

FW = pathlib.Path(__file__).resolve().parents[2] / "firmware"
RX, TX = 16, 8          # ui0 (RXD), uo0 (TXD)
TPB = 96                # clocks per bit at 500 kbit/s and 48 MHz


def crc15(bits):
    crc = 0
    for b in bits:
        nxt = b ^ ((crc >> 14) & 1)
        crc = (crc << 1) & 0x7FFF
        if nxt:
            crc ^= 0x4599
    return crc


def frame_bits(ident, data, rtr=False):
    """Destuffed SOF..CRC bits of a standard frame."""
    b = [0] + [(ident >> (10 - i)) & 1 for i in range(11)] + [int(rtr), 0, 0]
    dlc = len(data)
    b += [(dlc >> (3 - i)) & 1 for i in range(4)]
    if not rtr:
        for byte in data:
            b += [(byte >> (7 - i)) & 1 for i in range(8)]
    c = crc15(b)
    return b + [(c >> (14 - i)) & 1 for i in range(15)]


def stuff(bits):
    out, run, last = [], 0, None
    for x in bits:
        out.append(x)
        run = run + 1 if x == last else 1
        last = x
        if run == 5:
            out.append(1 - x)
            last, run = 1 - x, 1
    return out


RXQ = []


def send(mh, bits, corrupt=None, ppm=0):
    """Drive the bus (RXD = transmitter AND our TXD); returns what the bus showed
    in the ACK slot."""
    wire = stuff(bits)
    if corrupt is not None:
        wire[corrupt] ^= 1
    wire += [1, 1, 1] + [1] * 7 + [1] * 3   # CRC delim, ACK slot (recessive), ACK delim, EOF, IFS
    ack_index = len(stuff(bits)) + 1
    tpb = TPB * (1 + ppm / 1e6)
    t, ack_seen = 0.0, None
    for i, b in enumerate(wire):
        t += tpb
        n = int(t)
        t -= n
        lows = 0
        for _ in range(n):
            ours = (mh.chip.pin_out >> TX) & 1
            mh.ui = (mh.ui & ~1) | (b & ours)
            lows += 1 - (b & ours)
            mh.step()
        if i == ack_index:
            ack_seen = lows > n // 2
        RXQ.extend(mh.pop_all(0))   # the host drains while the frame arrives (8 bytes = 5 words)
    return ack_seen


def setup():
    mh = ModelHost()
    prog = asm.assemble((FW / "can_rx.pasm").read_text(), {"RX": RX, "TX": TX})
    mh.apply(host.load_program(0, prog, in_pins=RX, clkdiv=2 << 8))
    mh.write(sim.core_reg(0, sim.C_CRCCTRL), 0x4599 | (14 << 16) | (1 << 20))
    mh.write(sim.A_POUT, 1 << TX)
    mh.ui = 1
    mh.start(1)
    mh.step(2000)
    return mh


def unpack(words, nbits):
    bits = []
    full, rem = divmod(nbits, 32)
    for w in words[:full]:
        bits += [(w >> (31 - i)) & 1 for i in range(32)]
    if rem:
        w = words[full]
        bits += [(w >> (rem - 1 - i)) & 1 for i in range(rem)]
    return bits


@pytest.mark.parametrize("ppm", [0, 5000, -5000, 10000, -10000])
def test_can_rx_frames(ppm):
    RXQ.clear()
    mh = setup()
    rng = random.Random(ppm)
    frames = [(0x123, bytes([0xDE, 0xAD, 0xBE, 0xEF])), (0x7FF, bytes(8)), (0x000, b""),
              (0x2AA, bytes([0xFF] * 8)), (0x155, bytes([rng.getrandbits(8) for _ in range(3)]))]
    for ident, data in frames:
        bits = frame_bits(ident, data)
        assert send(mh, bits, ppm=ppm) is True, f"no ACK for {ident:#x}"
        mh.step(3000)
        words = RXQ + mh.pop_all(0)
        RXQ.clear()
        assert words[-1] == 1, [hex(w) for w in words]
        assert unpack(words[:-1], len(bits)) == bits
    # a corrupted frame: no ACK, CRC error status
    bits = frame_bits(0x321, bytes([1, 2, 3]))
    assert send(mh, bits, corrupt=30) is False
    mh.step(3000)
    words = RXQ + mh.pop_all(0)
    RXQ.clear()
    assert words[-1] == 0
