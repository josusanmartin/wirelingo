"""Run tools/micropython/protoemu.py (the demo-board driver) unmodified against the
cycle model through a Python model of the SPI host port (docs/isa.md §6.1)."""
import sys
import pathlib

from pe import sim
from pe.modelhost import ModelHost

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "tools" / "micropython"))
import protoemu  # noqa: E402
import protoemu_fw  # noqa: E402

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))


class SpiPortModel:
    """Byte-accurate model of pe_spi_slave on top of ModelHost (one bus op per SPI word)."""

    def __init__(self, mh):
        self.mh = mh
        self.cs, self.sck = 1, 0
        self.bits = 0
        self.bitcount = 0
        self.miso = 0
        self.tx = []            # bits to shift out
        self.bytes_in = []

    def pins(self, ui):
        cs, sck, mosi = (ui >> 7) & 1, (ui >> 6) & 1, (ui >> 5) & 1
        if cs:
            self.cs, self.bytes_in, self.bitcount, self.tx = 1, [], 0, []
        elif self.cs:
            self.cs = 0
        elif sck and not self.sck:          # rising edge: sample, then shift MISO
            self.bits = ((self.bits << 1) | mosi) & 0xFF
            self.bitcount += 1
            if self.tx:
                self.tx.pop(0)
            if self.bitcount % 8 == 0:
                self.byte(self.bits)
        self.sck = sck
        self.miso = self.tx[0] if self.tx and not self.cs else 0
        self.mh.step(8)                     # the SPI clock is slow compared to clk

    def byte(self, b):
        self.bytes_in.append(b)
        n = len(self.bytes_in)
        if n == 3:
            self.cmd, self.addr = self.bytes_in[0], (self.bytes_in[1] << 8) | self.bytes_in[2]
            if self.cmd in (0x03, 0x0B):
                self.load_word()
        elif n > 3 and (n - 3) % 4 == 0:
            if self.cmd in (0x02, 0x0A):
                w = int.from_bytes(bytes(self.bytes_in[-4:]), "big")
                self.mh.write(self.addr, w)
            else:
                self.mh.read(self.addr, pop=True)          # commit (FIFO pop)
            if not self.cmd & 0x08:
                self.addr += 1
            if self.cmd in (0x03, 0x0B):
                self.load_word()

    def load_word(self):
        w = self.mh.chip.read(self.addr)
        self.tx = [(w >> (31 - i)) & 1 for i in range(32)]


class FakeTT:
    """Just enough of the demo board's `tt` object: ui_in.value / uo_out.value."""

    class _In:
        def __init__(self, port):
            self.port, self._v = port, 0

        @property
        def value(self):
            return self._v

        @value.setter
        def value(self, v):
            self._v = v
            self.port.pins(v)

    class _Out:
        def __init__(self, port, mh):
            self.port, self.mh = port, mh

        @property
        def value(self):
            uo, _, _ = self.mh.chip.outputs()
            return uo | (self.port.miso << 7)

    def __init__(self, mh):
        self.port = SpiPortModel(mh)
        self.ui_in = FakeTT._In(self.port)
        self.uo_out = FakeTT._Out(self.port, mh)


def test_driver_uart_tx_bundle():
    mh = ModelHost()
    samples = []
    step0 = mh.step

    def step(n=1):                      # record uo0 on every cycle, SPI traffic included
        for _ in range(n):
            step0()
            samples.append((mh.chip.pin_out >> 8) & 1)
    mh.step = step
    tt = FakeTT(mh)
    pe = protoemu.ProtoEmu(tt, fifo_depth=4)
    assert pe.read(protoemu.A_ID) == sim.ID_VALUE
    pe.load(**protoemu_fw.UART_TX)
    pe.start(1)
    msg = b"Hi!"
    level, bits, t_edge = 1, [], None
    for ch in msg:
        pe.push(0, ch)
    # decode 115200 baud 8N1 from uo0 at 48 MHz: 416.7 clocks per bit
    mh.step(12 * 417 * len(msg) + 2000)
    out = []
    i = samples.index(1)                # the program idles the line high first
    while i < len(samples) and len(out) < len(msg):
        if samples[i] == 0:
            b = sum(samples[int(i + 417 * (k + 1.5))] << k for k in range(8))
            out.append(b)
            i += int(417 * 9.5)
        else:
            i += 1
    assert bytes(out) == msg, out


def test_driver_eth10_frame():
    """eth10_send(): FCS, R0 and the 16-word FIFO fill done by the driver; the
    waveform is decoded by the 10BASE-T model test's decoder."""
    import zlib
    import test_eth10 as eth
    mh = ModelHost()
    trace = []
    chip_step = mh.chip.step

    def step(*args, **kw):              # every clock, including the host's bus cycles
        chip_step(*args, **kw)
        trace.append((mh.chip.pin_out >> 8) & 3)
    mh.chip.step = step
    tt = FakeTT(mh)
    pe = protoemu.ProtoEmu(tt)
    pe.load(**protoemu_fw.ETH10_TX)
    pe.start(1)
    body = bytes.fromhex("ffffffffffff020000c0ffee88b5") + b"hello from protoemu"
    assert protoemu._crc32(body) == zlib.crc32(body)
    mark = len(trace)
    pe.eth10_send(0, body)
    while pe.eth10_busy():
        mh.step(100)
    sent = body + bytes(60 - len(body))
    sent += zlib.crc32(sent).to_bytes(4, "little")
    eth.check(trace, mark, sent)
