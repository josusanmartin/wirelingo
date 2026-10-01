"""10BASE-T transmitter: decode the TD+/TD- waveform independently and check
Manchester timing, preamble/SFD, frame bits (with a zlib CRC-32 FCS), TP_IDL
and the link pulses. clk = 40 MHz, so 4 ticks per bit and 25 ns per tick."""
import pathlib
import random
import struct
import zlib

import pytest

from pe import asm, host, sim
from pe.modelhost import ModelHost

FW = pathlib.Path(__file__).resolve().parents[2] / "firmware"
TDP = 8           # uo0 = TD+, uo1 = TD-
F_GO = 0
IDLE, H, L = 0, 1, 2
SPI_WORD_TICKS = 64   # one 32-bit FIFO word per 64 clocks: 1-bit WRITE_FIXED burst at SCK = clk/2


def frame(payload_len, seed=1):
    dst, src = bytes.fromhex("ffffffffffff"), bytes.fromhex("020000c0ffee")
    body = dst + src + struct.pack(">H", 0x88B5) + bytes((seed * 37 + i * 11) & 0xFF for i in range(payload_len))
    return body + struct.pack("<I", zlib.crc32(body))


def words(b):
    b = b + bytes(-len(b) % 4)
    return [int.from_bytes(b[i:i + 4], "little") for i in range(0, len(b), 4)]


def wire_bits(fr):
    pre = [1, 0] * 31 + [1, 1]
    return pre + [(byte >> i) & 1 for byte in fr for i in range(8)]


class Line:
    def __init__(self):
        self.mh = ModelHost()
        prog = asm.assemble((FW / "eth10_tx.pasm").read_text(), {"F_GO": F_GO})
        self.mh.apply(host.load_program(0, prog, side_pins=TDP, clkdiv=1 << 8))
        self.mh.start(1)
        self.trace = []

    def level(self):
        v = (self.mh.chip.pin_out >> TDP) & 3
        assert v != 3, "TD+ and TD- both high"
        return v

    def step(self, n=1):
        for _ in range(n):
            self.mh.step()
            self.trace.append(self.level())

    def send(self, fr, stream=False):
        """Load a frame the way the host would and start it. With stream=True,
        only the first 2*D words are preloaded; the rest follow at SPI speed."""
        mh, w = self.mh, words(fr)
        mh.write(sim.core_reg(0, sim.C_R0), 8 * len(fr) - 1)
        pre = w if not stream else w[:16]
        assert len(pre) <= 16
        for x in pre:
            while mh.tx_level(0) >= 8:
                self.step()
            mh.push(0, x)
            self.trace.append(self.level())
        self.step(16)  # let the TXF -> RXF moves finish
        mh.write(sim.A_FSET, 1 << F_GO)
        self.trace.append(self.level())
        start = len(self.trace)
        last = -SPI_WORD_TICKS
        for x in w[len(pre):]:
            # a word as soon as there is room, but at most one per SPI word time
            while mh.tx_level(0) >= 8 or len(self.trace) - last < SPI_WORD_TICKS:
                self.step()
            last = len(self.trace)
            mh.push(0, x)
            self.trace.append(self.level())
        while mh.chip.flags >> F_GO & 1:
            self.step()
        self.step(40)
        return start


def decode(trace, start):
    """Bits from 4-tick Manchester cells, starting at the first non-idle tick
    at or after `start`; returns (bits, tp_idl_ticks, end index)."""
    t = start
    while trace[t] == IDLE:
        t += 1
    bits = []
    while True:
        cell = trace[t:t + 4]
        a, b = cell[0], cell[2]
        if not (cell[1] == a and cell[3] == b and a != b and IDLE not in cell):
            break
        bits.append(1 if b == H else 0)
        t += 4
    # TP_IDL: H from the last mid-bit transition (after a 1) or the bit boundary (after a 0)
    u = t - 2 if bits[-1] == 1 else t
    n = 0
    while trace[u + n] == H:
        n += 1
    assert all(v == IDLE for v in trace[u + n:u + n + 20]), trace[u + n - 4:u + n + 20]
    return bits, n, u + n


def check(trace, start, fr):
    bits, tp, _ = decode(trace, start)
    assert bits == wire_bits(fr), f"{len(bits)} bits decoded, {len(wire_bits(fr))} expected"
    assert 10 <= tp <= 14, f"TP_IDL {tp * 25} ns"   # 250..350 ns


def test_min_frame_from_fifo():
    ln = Line()
    ln.step(100)
    fr = frame(46)                       # 64 bytes: the minimum frame, 16 words
    assert len(fr) == 64
    check(ln.trace, ln.send(fr), fr)


def test_streamed_long_frame_and_back_to_back():
    ln = Line()
    ln.step(100)
    fr = frame(110)                      # 128 bytes, 32 words: host streams the second half
    check(ln.trace, ln.send(fr, stream=True), fr)
    fr2 = frame(51, seed=2)              # 69 bytes: the last word is part-used (OSR flush)
    check(ln.trace, ln.send(fr2, stream=True), fr2)
    fr3 = frame(46, seed=3)
    check(ln.trace, ln.send(fr3), fr3)


def test_max_frame_streamed():
    """A maximum-size (1518-byte) frame streamed through the host port: 1-bit at
    SCK = clk/2 gives a word every 64 clocks, the line takes one every 128."""
    ln = Line()
    ln.step(100)
    fr = frame(1500)
    assert len(fr) == 1518
    check(ln.trace, ln.send(fr, stream=True), fr)


def test_link_pulses():
    ln = Line()
    ln.step(2 * 640_000 + 2000)
    rises = [i for i in range(1, len(ln.trace)) if ln.trace[i] == H and ln.trace[i - 1] != H]
    assert len(rises) >= 2
    for r in rises:
        w = 0
        while ln.trace[r + w] == H:
            w += 1
        assert w == 4, f"NLP width {w * 25} ns"   # 100 ns
    gaps = [(b - a) * 25e-9 for a, b in zip(rises, rises[1:])]
    assert all(8e-3 <= g <= 24e-3 for g in gaps), gaps   # 16 +- 8 ms
    assert L not in ln.trace


# ---------------------------------------------------------------- receiver (line unit)

SFD = [1, 0, 1, 0, 1, 0, 1, 1]


def rx_bits(words):
    """eth10_rx records -> list of frames, each the list of received bits."""
    frames, data = [], []
    for w in words:
        if w & 0xFF:                              # trailer: count << 16 | 0x80
            n = w >> 16
            bits = []
            for d in data:
                bits += [(d >> (8 + i)) & 1 for i in range(24)]
            full, rem = divmod(n, 24)
            bits = bits[:24 * full]
            if rem:
                bits += [(data[full] >> (32 - rem + i)) & 1 for i in range(rem)]
            frames.append(bits)
            data = []
        else:
            data.append(w)
    return frames


def frame_from_bits(bits):
    """The frame bytes after the SFD (the preamble may have lost its first bits)."""
    for i in range(len(bits) - 8):
        if bits[i:i + 8] == SFD:
            body = bits[i + 8:]
            return bytes(sum(b << j for j, b in enumerate(body[8 * k:8 * k + 8])) for k in range(len(body) // 8))
    raise AssertionError("no SFD")


def rx_setup(mh, clk_per_bit, rxp):
    prog = asm.assemble((FW / "eth10_rx.pasm").read_text())
    mh.apply(host.load_program(0, prog, 0, clkdiv=1 << 8))
    mh.write(sim.A_LINEDIV, clk_per_bit << 16)
    mh.write(sim.A_LINEPINS, rxp << 8)
    mh.write(sim.A_LINECFG, 1 | (2 << 1) | (1 << 14))


def test_tx_core1_to_rx_core0():
    """eth10_tx on core 1 sends onto virtual pins 24/25; core 0 receives TD+
    through the line unit (40 MHz, 4 clocks per bit)."""
    mh = ModelHost()
    tx = asm.assemble((FW / "eth10_tx.pasm").read_text(), {"F_GO": F_GO})
    mh.apply(host.pin_owner_writes({24: 1, 25: 1}))
    mh.apply(host.load_program(1, tx, 256, side_pins=24, clkdiv=1 << 8))
    rx_setup(mh, 4, 24)
    mh.start(0b11)
    got = []
    for fr in (frame(46), frame(100, seed=5)):
        w = words(fr)
        mh.write(sim.core_reg(1, sim.C_R0), 8 * len(fr) - 1)
        for x in w[:16]:
            while mh.tx_level(1) >= 8:
                mh.step()
                got += mh.pop_all(0)
            mh.push(1, x)
        mh.step(16)
        mh.write(sim.A_FSET, 1 << F_GO)
        for x in w[16:]:
            while mh.tx_level(1) >= 8:
                mh.step()
                got += mh.pop_all(0)
            mh.push(1, x)
        while mh.chip.flags >> F_GO & 1:
            mh.step()
            got += mh.pop_all(0)
        for _ in range(400):
            mh.step()
            got += mh.pop_all(0)
    frames = [b for b in rx_bits(got) if len(b) > 64]   # link pulses arrive as 1-2 bit "frames"
    assert [frame_from_bits(b) for b in frames] == [frame(46), frame(100, seed=5)]


@pytest.mark.parametrize("seed", range(3))
def test_rx_external_50mhz_with_jitter(seed):
    """A Manchester frame from an external sender at 10 Mbit/s with a random
    phase, a 100 ppm frequency offset and +-4 ns of edge jitter, received at
    50 MHz (5 clocks per bit; the edges land between clock edges)."""
    rng = random.Random(seed)
    mh = ModelHost()
    rx_setup(mh, 5, 16)                            # RD on ui0
    mh.start(1)
    fr = frame(60, seed=seed)
    line = [b for x in wire_bits(fr) for b in (1 - x, x)]   # half-bit levels, 802.3 polarity
    clk = 20e-9
    half = 50e-9 * (1 + 100e-6)
    t, edges = rng.random() * clk + 200e-9, []
    for lv in line:
        edges.append((t + rng.uniform(-4e-9, 4e-9), lv))
        t += half
    edges.append((t, 0))                           # TP_IDL then silence (stays low)
    # drive RD from the chip's own cycle count, so the host's bus cycles (FIFO
    # reads) don't stall the waveform
    step0, k = mh.chip.step, [0]

    def step(ui_in=0, *a, **kw):
        while k[0] < len(edges) and edges[k[0]][0] <= mh.chip.cycle * clk:
            mh.ui = edges[k[0]][1]
            k[0] += 1
        step0(mh.ui, *a, **kw)
    mh.chip.step = step
    got = []
    while mh.chip.cycle * clk < t + 2e-6:
        mh.step()
        got += mh.pop_all(0)
    [bits] = [b for b in rx_bits(got) if len(b) > 64]
    assert frame_from_bits(bits) == fr
