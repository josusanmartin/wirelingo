"""10BASE-T transmitter: decode the TD+/TD- waveform independently and check
Manchester timing, preamble/SFD, frame bits (with a zlib CRC-32 FCS), TP_IDL
and the link pulses. clk = 40 MHz, so 4 ticks per bit and 25 ns per tick."""
import pathlib
import struct
import zlib

from pe import asm, host, sim
from pe.modelhost import ModelHost

FW = pathlib.Path(__file__).resolve().parents[2] / "firmware"
TDP = 8           # uo0 = TD+, uo1 = TD-
F_GO = 0
IDLE, H, L = 0, 1, 2
SPI_WORD_TICKS = 256  # one 32-bit FIFO word per 6.4 us: WRITE_FIXED burst at SCK = clk/8


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
