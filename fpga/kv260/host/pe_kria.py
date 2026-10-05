"""protoemu host transport for the KV260 bring-up design (fpga/kv260).

The PL design (kv260_top.v) puts the Tiny Tapeout pins on AXI GPIO:

    gpio0 ch1 out  0xA0000000  [7:0] ui_in, [8] rst_n, [23:16] uio pad drive
    gpio0 ch2 in   0xA0000008  [7:0] uo_out, [15:8] uio_out, [23:16] uio_oe, [24] locked
    gpio1 ch1 in   0xA0010000  Gray-coded protoemu clk cycle count

`Board` maps them through /dev/mem (run as root) and looks like the TT demo
board's `tt` object (ui_in.value / uo_out.value), so the demo-board driver
tools/micropython/protoemu.py bit-bangs the SPI host port unmodified:

    board = Board(); board.reset()
    pe = board.protoemu()                 # tools/micropython/protoemu.ProtoEmu
    assert pe.read(0x800) == 0x50450002

`QuadTransport` adds the quad host port (docs/isa.md §6.1) as a ProtoEmu
transport. ui_in[4:0] are protocol inputs: the board drives them from
`Board.ui_low`, so SPI traffic doesn't disturb them (except ui[4:2] during
quad transactions, which the quad port borrows).
"""
import mmap
import os
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[3]       # repo root
for sub in ("tools", "tools/micropython"):
    if str(ROOT / sub) not in sys.path:
        sys.path.insert(0, str(ROOT / sub))

GPIO0, GPIO1 = 0xA0000000, 0xA0010000
CS, SCK, MOSI = 7, 6, 5
RST_N = 8
UIO = 16
LOCKED = 24
ID = 0x50450002


class DevMem:
    """32-bit register access to the two AXI GPIO blocks through /dev/mem."""

    def __init__(self):
        fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
        try:
            self._maps = [mmap.mmap(fd, 0x1000, offset=a) for a in (GPIO0, GPIO1)]
        finally:
            os.close(fd)
        # 'I' memoryview items are single aligned 32-bit loads/stores
        self.g0, self.g1 = (memoryview(m).cast("I") for m in self._maps)

    def out(self, v):
        self.g0[0] = v

    def inp(self):
        return self.g0[2]

    def cycles_gray(self):
        return self.g1[0]


def gray_to_bin(g):
    b = g
    s = 1
    while s < 32:
        b ^= b >> s
        s <<= 1
    return b


class Board:
    """The KV260 design as a TT demo board `tt` object, plus reset/clock helpers.

    settle: extra reads of the input register before using its value. A
    posted GPIO write (the SCK fall that launches MISO) can still be in flight
    when the next read is issued; the input channel also adds two pl_clk0
    synchroniser cycles. One extra read covers both."""

    def __init__(self, regs=None, settle=1):
        self.regs = DevMem() if regs is None else regs
        self.settle = settle
        self.ui_low = 0              # ui_in[4:0]: protocol inputs P16..P20
        self.uio_drive = 0xFF        # uio pad value where protoemu doesn't drive (pull-ups)
        self.rst_n = 1
        self._ui = 1 << CS
        self.ui_in = _UiIn(self)
        self.uo_out = _UoOut(self)
        self._apply()

    # -- pins
    def _word(self):
        return (self._ui & 0xFF) | (self.rst_n << RST_N) | ((self.uio_drive & 0xFF) << UIO)

    def _apply(self):
        self.regs.out(self._word())

    def set_ui(self, v):
        """ui_in from the driver: [7:5] are the SPI lines; [4:0] come from ui_low."""
        self._ui = (v & 0xE0) | (self.ui_low & 0x1F)
        self._apply()

    def set_inputs(self, ui_low=None, uio=None):
        if ui_low is not None:
            self.ui_low = ui_low & 0x1F
            self._ui = (self._ui & 0xE0) | self.ui_low
        if uio is not None:
            self.uio_drive = uio & 0xFF
        self._apply()

    def pins(self):
        """(uo_out, uio_out, uio_oe, locked) as the PS sees them now."""
        for _ in range(self.settle):
            self.regs.inp()
        v = self.regs.inp()
        return v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF, (v >> LOCKED) & 1

    # -- chip control
    def reset(self, hold_s=0.001):
        """Pulse rst_n with CS_n high, then keep CS_n high well over 5 clk (§6.1)."""
        self._ui = (1 << CS) | self.ui_low
        self.rst_n = 0
        for _ in range(8):
            self._apply()
        time.sleep(hold_s)
        if not self.pins()[3]:
            raise RuntimeError("MMCM not locked: is the protoemu app loaded?")
        self.rst_n = 1
        for _ in range(8):      # CS_n high for >= 5 clk even if hold_s is 0 (simulation)
            self._apply()
        time.sleep(hold_s)

    def cycles(self):
        return gray_to_bin(self.regs.cycles_gray())

    def clk_hz(self, interval=0.5):
        c0, t0 = self.cycles(), time.monotonic()
        time.sleep(interval)
        c1, t1 = self.cycles(), time.monotonic()
        return ((c1 - c0) & 0xFFFFFFFF) / (t1 - t0)

    def protoemu(self, **kw):
        import protoemu
        return protoemu.ProtoEmu(self, **kw)


class _UiIn:
    def __init__(self, board):
        self.board = board

    @property
    def value(self):
        return self.board._ui

    @value.setter
    def value(self, v):
        self.board.set_ui(v)


class _UoOut:
    def __init__(self, board):
        self.board = board

    @property
    def value(self):
        return self.board.pins()[0]


class QuadTransport:
    """Quad host port: command byte 1-bit on IO3 (ui[5]), then address and data
    4 bits per SCK on ui[5:2] in and uo[7:4] out; reads have 8 dummy SCK.
    Requires HOSTCFG.QUAD_EN = 1 (enable() sets it with a 1-bit write).
    Install with pe.transport = QuadTransport(board)."""

    def __init__(self, board):
        self.b = board

    def enable(self, pe):
        pe.transport = None
        pe.write(0x80B, 1)
        pe.transport = self

    def _drive(self, cs, sck, nib):
        b = self.b
        b._ui = (cs << CS) | (sck << SCK) | ((nib & 0xF) << 2) | (b.ui_low & 0x03)
        b._apply()

    def _frame(self, cmd, addr, words=(), nread=0):
        nibs = [((cmd >> i) & 1) << 3 for i in range(7, -1, -1)]
        nibs += [(addr >> (4 * i)) & 0xF for i in range(3, -1, -1)]
        for w in words:
            nibs += [(w >> (4 * i)) & 0xF for i in range(7, -1, -1)]
        nibs += [0] * (8 + 8 * nread) if nread else []
        got = []
        self._drive(0, 0, nibs[0])
        for n in nibs:
            self._drive(0, 0, n)                   # outputs change on the SCK fall
            got.append(self.b.pins()[0] >> 4)      # sample before the rise
            self._drive(0, 1, n)
        self._drive(0, 0, 0)
        self._drive(1, 0, 0)
        self.b.set_ui(1 << CS)
        data = got[len(nibs) - 8 * nread:]
        out = []
        for i in range(nread):
            w = 0
            for n in data[8 * i:8 * i + 8]:
                w = (w << 4) | n
            out.append(w)
        return out

    def write(self, addr, words, fixed=False):
        if isinstance(words, int):
            words = [words]
        self._frame(0x4A if fixed else 0x42, addr, words)

    def read(self, addr, n=1, fixed=False):
        words = self._frame(0x4B if fixed else 0x43, addr, nread=n)
        return words[0] if n == 1 else words

    def xfer(self, data, nread=0):
        raise NotImplementedError("quad transport has no raw 1-bit transfers")
