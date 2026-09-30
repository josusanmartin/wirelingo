"""protoemu host driver for the Tiny Tapeout demo board (MicroPython on the RP2350).

The chip's host port is SPI mode 0 on ui[5]=MOSI, ui[6]=SCK, ui[7]=CS_n, uo[7]=MISO
(docs/isa.md §6.1). This driver bit-bangs it through the demo board's `tt` object,
so it needs no particular pin mux; SCK must stay <= clk/8.

    from protoemu import ProtoEmu
    pe = ProtoEmu(tt)
    assert pe.read(0x800) == 0x50450002
    pe.load(core=0, words=[...], base=0, clkdiv=..., pinctrl0=..., pinctrl1=..., shiftctrl=..., wrap=...)
    pe.start(0b0001)
    pe.push(0, ord('A'))

Programs are assembled on a PC (tools/pe/asm.py); `python -m pe.export file.pasm`
prints the words and register values to paste here.
"""

CMD_WRITE, CMD_READ, CMD_WRITE_FIXED, CMD_READ_FIXED = 0x02, 0x03, 0x0A, 0x0B
A_ID, A_CTRL, A_FSET, A_FCLR, A_BANK, A_POWN_LO, A_POWN_HI, A_OD, A_PINS, A_POUT, A_POE = range(0x800, 0x80B)
CORE_BASE, CORE_STRIDE = 0x400, 0x20
C_CLKDIV, C_PINCTRL0, C_PINCTRL1, C_SHIFTCTRL, C_ENTRY, C_WRAP = 0, 1, 2, 3, 4, 5
C_PC, C_STATUS, C_R0, C_ISR, C_OSR, C_FIFO = 8, 9, 0xA, 0xE, 0xF, 0x10

MOSI, SCK, CS = 5, 6, 7


class ProtoEmu:
    def __init__(self, tt, fifo_depth=8):
        self.tt = tt
        self.depth = fifo_depth
        self.ui = 1 << CS
        self._set_ui()

    # -- low level
    def _set_ui(self):
        self.tt.ui_in.value = self.ui

    def _bit(self, b):
        self.ui = (self.ui & ~(1 << MOSI)) | (b << MOSI)
        self._set_ui()
        r = (self.tt.uo_out.value >> 7) & 1
        self.ui |= 1 << SCK
        self._set_ui()
        self.ui &= ~(1 << SCK)
        self._set_ui()
        return r

    def xfer(self, data, nread=0):
        self.ui &= ~(1 << CS)
        self._set_ui()
        out = bytearray()
        for byte in bytes(data) + bytes(nread):
            r = 0
            for i in range(7, -1, -1):
                r = (r << 1) | self._bit((byte >> i) & 1)
            out.append(r)
        self.ui |= 1 << CS
        self._set_ui()
        return bytes(out)

    # -- bus
    def write(self, addr, words, fixed=False):
        if isinstance(words, int):
            words = [words]
        frame = bytearray([CMD_WRITE_FIXED if fixed else CMD_WRITE, addr >> 8, addr & 0xFF])
        for w in words:
            frame += w.to_bytes(4, "big")
        self.xfer(frame)

    def read(self, addr, n=1, fixed=False):
        r = self.xfer(bytes([CMD_READ_FIXED if fixed else CMD_READ, addr >> 8, addr & 0xFF]), 4 * n)[3:]
        words = [int.from_bytes(r[4 * i:4 * i + 4], "big") for i in range(n)]
        return words[0] if n == 1 else words

    # -- convenience
    @staticmethod
    def core_reg(core, off):
        return CORE_BASE + CORE_STRIDE * core + off

    def load(self, core, words, base, clkdiv=1 << 8, pinctrl0=0, pinctrl1=0, shiftctrl=0, wrap=None):
        """Load relocated words at `base` and configure `core` (which must be stopped)."""
        self.write(base, list(words))
        r = lambda o: self.core_reg(core, o)
        if wrap is None:
            wrap = base | ((base + len(words) - 1) << 16)
        self.write(r(C_CLKDIV), [clkdiv, pinctrl0, pinctrl1, shiftctrl, base, wrap])

    def start(self, mask):
        en = self.read(A_CTRL) & 15
        self.write(A_CTRL, en | mask | (mask << 8))

    def stop(self, mask):
        en = self.read(A_CTRL) & 15
        self.write(A_CTRL, en & ~mask)

    def status(self, core):
        return self.read(self.core_reg(core, C_STATUS))

    def push(self, core, word):
        while self.status(core) & 15 >= self.depth:
            pass
        self.write(self.core_reg(core, C_FIFO), word, fixed=True)

    def pop(self, core):
        """Return one RX FIFO word, or None if empty."""
        if (self.status(core) >> 4) & 15 == 0:
            return None
        return self.read(self.core_reg(core, C_FIFO), fixed=True)
