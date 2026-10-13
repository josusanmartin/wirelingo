"""Run tools/micropython/protoemu.py (the demo-board driver) unmodified against the
cycle model through a Python model of the SPI host port (docs/isa.md §6.1)."""
import sys
import pathlib

from pe import sim
from pe.modelhost import ModelHost

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "tools" / "micropython"))
import protoemu  # noqa: E402
import protoemu_fw  # noqa: E402
import bringup  # noqa: E402

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))


class SpiPortModel:
    """Byte-level model of pe_hostport (1-bit) on top of ModelHost: writes land
    per word; reads snapshot two words ahead once the address is in, send them
    after the dummy byte, commit each fully sent word, cancel the rest at CS high."""

    def __init__(self, mh):
        self.mh = mh
        self.cs, self.sck = 1, 0
        self.bits = 0
        self.bitcount = 0
        self.miso = 0
        self.tx = []            # bits to shift out
        self.bytes_in = []
        self.ahead = []         # snapshotted read words not yet sent
        self.reading = False
        self.cmd = None

    def pins(self, ui):
        cs, sck, mosi = (ui >> 7) & 1, (ui >> 6) & 1, (ui >> 5) & 1
        if cs:
            if not self.cs and self.reading:
                self.mh.cancel()
            self.cs, self.bytes_in, self.bitcount, self.tx, self.ahead, self.reading = 1, [], 0, [], [], False
            self.cmd = None
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

    def snap(self):
        self.ahead.append(self.mh.snap(self.snap_addr))
        if not self.cmd & 0x08:
            self.snap_addr += 1

    def load_word(self):
        w = self.ahead[0]
        self.tx = [(w >> (31 - i)) & 1 for i in range(32)]

    def byte(self, b):
        self.bytes_in.append(b)
        n = len(self.bytes_in)
        if n == 3:
            self.cmd, self.addr = self.bytes_in[0], (self.bytes_in[1] << 8) | self.bytes_in[2]
            if self.cmd in (0x03, 0x0B):
                self.reading, self.snap_addr = True, self.addr
                self.snap()
                self.snap()
        elif self.cmd in (0x02, 0x0A) and n > 3 and (n - 3) % 4 == 0:
            self.mh.write(self.addr, int.from_bytes(bytes(self.bytes_in[-4:]), "big"))
            if not self.cmd & 0x08:
                self.addr += 1
        elif self.reading and n == 4:       # dummy byte done: first word out
            self.load_word()
        elif self.reading and n > 4 and (n - 4) % 4 == 0:
            self.mh.commit(self.addr)       # the oldest word is fully sent
            if not self.cmd & 0x08:
                self.addr += 1
            self.ahead.pop(0)
            self.snap()
            self.load_word()


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


def test_bringup_uart_loopback_and_errors():
    """bringup.py's board-independent steps on the model: the internal UART
    loopback over virtual pin 24, and push() reporting a halted core instead of
    spinning on a full FIFO."""
    import pytest
    mh = ModelHost()
    pe = protoemu.ProtoEmu(FakeTT(mh))
    assert pe.read(protoemu.A_ID) == bringup.ID
    assert bringup.uart_loopback(pe, b"Hi!") == b"Hi!"
    halt = {"core": 0, "base": 0, "words": [12 << 27], "clkdiv": 1 << 8, "pinctrl0": 0,
            "pinctrl1": 0, "shiftctrl": 0, "wrap": 0}
    bringup.load(pe, halt)
    for i in range(8):
        pe.push(0, i)
    with pytest.raises(RuntimeError, match="halted"):
        pe.push(0, 8)


def test_bringup_uart_loopback_streams_beyond_fifo_capacity():
    import pytest
    mh = ModelHost()
    pe = protoemu.ProtoEmu(FakeTT(mh))
    msg = bytes(range(32)) + b"protoemu: two cores\0\xff"
    assert bringup.uart_loopback(pe, msg) == msg
    assert pe.read(protoemu.A_CTRL) & 3 == 0
    with pytest.raises(RuntimeError, match="loopback got"):
        bringup.uart_loopback(pe, msg, timeout=0)
    assert pe.read(protoemu.A_CTRL) & 3 == 0


def test_driver_register_map_matches_the_model():
    """The MicroPython driver keeps its own copy of the register map (it can't
    import tools/pe on the board); every constant must match the model's."""
    from pe import host
    names = [n for n in dir(protoemu) if n.startswith(("A_", "C_", "CORE_"))]
    assert len(names) > 20
    for n in names:
        assert getattr(protoemu, n) == getattr(sim, n), n
    for n in ("CMD_WRITE", "CMD_READ", "CMD_WRITE_FIXED", "CMD_READ_FIXED"):
        assert getattr(protoemu, n) == getattr(host, n), n
    assert bringup.ID == sim.ID_VALUE


def test_board_modules_avoid_cpython_only_features():
    """tools/micropython runs on MicroPython: no step slices on str/bytes, no
    str/bytes padding methods, no dataclasses/typing (none of them exist there)."""
    import re
    root = pathlib.Path(__file__).resolve().parents[2] / "tools/micropython"
    bad = re.compile(r"\[[^\]]*::-1\]|\.(ljust|rjust|zfill)\(|^\s*(from|import) (dataclasses|typing)\b", re.M)
    for f in sorted(root.glob("*.py")):
        hits = [m.group(0) for m in bad.finditer(f.read_text())]
        assert not hits, (f.name, hits)


def test_bringup_usb_enum_enumerates_alone():
    """bringup.usb_enum() loads the autonomous USB device through the driver
    (bundle bursts over the SPI port model); the chip then enumerates with the
    host transport idle, at both speeds, and usb_enum_state() reads the same
    outcome as pe.usbenum.state."""
    import pytest
    from pe import usbenum
    from pe.usbfs import FsControlHost
    from pe.usbhost import ControlHost
    import test_usb_autonomous as ua
    for speed in ("low", "full"):
        mh = ModelHost()
        pe = protoemu.ProtoEmu(FakeTT(mh))
        bringup.usb_enum(pe, speed)
        mh.pull_up = 0b10
        mh.step(100)
        h = ControlHost(mh) if speed == "low" else FsControlHost(mh, ipd_bits=2.0, ack_bits=2.0)
        with ua.TransportIdle(mh):
            ua.enumerate_flow(h, addr=5)
        got = bringup.usb_enum_state(pe)
        assert got == ua.CONFIGURED(5), (speed, got)
        assert got == usbenum.state(pe.read)
    with pytest.raises(ValueError):
        bringup.usb_enum(protoemu.ProtoEmu(FakeTT(ModelHost())), "high")


def test_bundle_usb_enum_matches_the_loader():
    """The bundle's bursts replay exactly pe.usbenum.load_writes (no start)."""
    from pe import usbenum
    for speed, image in (("low", protoemu_fw.USB_ENUM_LOW), ("full", protoemu_fw.USB_ENUM_FULL)):
        flat = [(a + i, w) for a, ws in image["writes"] for i, w in enumerate(ws)]
        assert flat == usbenum.load_writes(usbenum.assemble(speed), speed, start=False)
