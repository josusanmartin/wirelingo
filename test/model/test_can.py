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


def records(words):
    """can_rx records: data words, then a status word ([31] = 1). Returns
    [(status, data words)] and checks nothing is left over."""
    out, start = [], 0
    for i, w in enumerate(words):
        if w >> 31:
            out.append((w & 0xFFFF, words[start:i]))
            start = i + 1
    assert start == len(words), [hex(w) for w in words]
    return out


def unpack(words, nbits):
    """Destuffed bits from data words: 24 per word in [23:0], MSB first; the last
    word holds the rest, right-aligned."""
    bits = []
    full, rem = divmod(nbits, 24)
    for w in words[:full]:
        assert w >> 24 == 0
        bits += [(w >> (23 - i)) & 1 for i in range(24)]
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
        [(status, data)] = records(words)
        assert status == 1 and unpack(data, len(bits)) == bits
    # a corrupted frame: no ACK, CRC error status
    bits = frame_bits(0x321, bytes([1, 2, 3]))
    assert send(mh, bits, corrupt=30) is False
    mh.step(3000)
    words = RXQ + mh.pop_all(0)
    RXQ.clear()
    assert [st for st, _ in records(words)] == [0]


def test_can_rx_stuff_error():
    """A stuff bit equal to the five before it: status 2, an error flag on TX,
    and the next frame is received normally."""
    RXQ.clear()
    mh = setup()
    bits = frame_bits(0x0F0, bytes([0x00, 0xFF, 0x0F]))
    wire = stuff(bits)
    k = next(i for i in range(5, len(wire)) if len(set(wire[i - 5:i])) == 1 and wire[i] != wire[i - 1])
    flag = []
    step0 = mh.step

    def step(n=1):
        for _ in range(n):
            step0()
            flag.append((mh.chip.pin_out >> TX) & 1)
    mh.step = step
    send(mh, bits, corrupt=k)                       # the stuff bit made equal: 6 in a row
    mh.step(3000)
    words = RXQ + mh.pop_all(0)
    RXQ.clear()
    assert [st for st, _ in records(words)] == [2]
    lows = max(len(r) for r in "".join(map(str, flag)).split("1"))
    assert lows >= 6 * TPB - 2, lows                # error flag: 6 dominant bits
    bits = frame_bits(0x123, bytes([0xDE, 0xAD]))
    assert send(mh, bits) is True
    mh.step(3000)
    words = RXQ + mh.pop_all(0)
    RXQ.clear()
    [(status, data)] = records(words)
    assert status == 1 and unpack(data, len(bits)) == bits


# ---------------------------------------------------------------- transmitter

def tx_words(bits):
    words = [len(bits)]
    for i in range(0, len(bits), 32):
        chunk = bits[i:i + 32] + [0] * (32 - len(bits[i:i + 32]))
        words.append(int("".join(map(str, chunk)), 2))
    return words


TXD0, TXD1 = 8, 9  # uo0 (can_tx), uo1 (can_rx's ACK)


def loop_setup():
    """can_tx on core 0 and can_rx on core 1 on one wired-AND bus (RXD = ui0)."""
    mh = ModelHost()
    tx = asm.assemble((FW / "can_tx.pasm").read_text(), {"RX": RX, "TX": TXD0})
    rx = asm.assemble((FW / "can_rx.pasm").read_text(), {"RX": RX, "TX": TXD1})
    mh.apply(host.pin_owner_writes({TXD0: 0, TXD1: 1}))
    mh.apply(host.load_program(0, tx, out_pins=(TXD0, 1), in_pins=RX, clkdiv=2 << 8))
    mh.apply(host.load_program(1, rx, in_pins=RX, clkdiv=2 << 8))
    mh.write(sim.core_reg(0, sim.C_CRCCTRL), 0x4599 | (14 << 16) | (1 << 21))   # OUT tap
    mh.write(sim.core_reg(1, sim.C_CRCCTRL), 0x4599 | (14 << 16) | (1 << 20))   # IN tap
    mh.write(sim.A_POUT, (1 << TXD0) | (1 << TXD1))
    mh.ui = 1
    mh.start(0b11)
    return mh


def run_bus(mh, cycles, other=None):
    """Step with RXD = TXD0 & TXD1 (& another node's bit, from other(t))."""
    rx_words = []
    for t in range(cycles):
        po = mh.chip.pin_out
        bus = ((po >> TXD0) & 1) & ((po >> TXD1) & 1) & (other(t) if other else 1)
        mh.ui = (mh.ui & ~1) | bus
        mh.step()
        rx_words += mh.pop_all(1)
        if mh.chip.cores[0].rxf:
            return mh.pop_all(0), rx_words
    return [], rx_words


def test_can_loopback_tx_to_rx():
    mh = loop_setup()
    for ident, data in [(0x123, bytes([1, 2, 3, 4])), (0x7F0, bytes([0xFF] * 8)), (0x001, b"")]:
        bits = frame_bits(ident, data)
        for w in tx_words(bits[:-15]):         # the TX appends the CRC itself
            mh.push(0, w)
        status, rx_words = run_bus(mh, 400 * TPB)
        assert status == [0], f"TX status {status} for {ident:#x}"      # ACKed by core 1
        rx_words += mh.pop_all(1)
        [(st, data)] = records(rx_words)
        assert st == 1 and unpack(data, len(bits)) == bits               # core 1: CRC ok


def test_can_arbitration_lost():
    mh = loop_setup()
    ours = frame_bits(0x123, bytes([0xAA]))[:-15]
    theirs = stuff(frame_bits(0x100, bytes([0x55]))) + [1] * 20           # lower ID wins
    for w in tx_words(ours):
        mh.push(0, w)
    # the other node starts its SOF when ours does: watch our TXD for the SOF edge
    start = []

    def other(t):
        if not start and (mh.chip.pin_out >> TXD0) & 1 == 0:
            start.append(t)
        if not start:
            return 1
        i = (t - start[0]) // TPB
        return theirs[i] if i < len(theirs) else 1

    status, _ = run_bus(mh, 400 * TPB, other)
    assert status == [2], status


# Frames whose CRC ends in five equal bits: a stuff bit follows the last CRC bit
# (stuffing covers SOF..CRC), so the CRC delimiter and the ACK slot come a bit later.
TRAILING_STUFF = [(0x054, bytes([1, 2, 3])), (0x07E, bytes([0x7E])), (0x10A, b"")]


def test_trailing_stuff_frames_have_a_stuff_bit_after_the_crc():
    for ident, data in TRAILING_STUFF:
        bits = frame_bits(ident, data)
        assert len(stuff(bits)) == len(stuff(bits[:-1])) + 2, hex(ident)


def test_can_rx_acks_after_a_stuff_bit_that_follows_the_crc():
    """can_rx used to drive its ACK one bit early (in the CRC delimiter) when
    a stuff bit followed the CRC: the transmitter saw no ACK in the slot.
    Found by fpga/kv260/host/legacy_demo.py's model run (can_rx_gen), before the KV260 run."""
    for ident, data in TRAILING_STUFF:
        RXQ.clear()
        mh = setup()
        bits = frame_bits(ident, data)
        assert send(mh, bits) is True, f"no ACK in the ACK slot for {ident:#x}"
        mh.step(3000)
        words = RXQ + mh.pop_all(0)
        RXQ.clear()
        [(status, got)] = records(words)
        assert status == 1 and unpack(got, len(bits)) == bits


def test_can_loopback_with_a_stuff_bit_after_the_crc():
    mh = loop_setup()
    for ident, data in TRAILING_STUFF:
        bits = frame_bits(ident, data)
        for w in tx_words(bits[:-15]):
            mh.push(0, w)
        status, rx_words = run_bus(mh, 400 * TPB)
        assert status == [0], f"TX status {status} for {ident:#x}"
        rx_words += mh.pop_all(1)
        [(st, got)] = records(rx_words)
        assert st == 1 and unpack(got, len(bits)) == bits
