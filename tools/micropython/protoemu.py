"""protoemu host driver for the Tiny Tapeout demo board (MicroPython on the RP2350).

The chip's host port is SPI mode 0 on ui[5]=MOSI, ui[6]=SCK, ui[7]=CS_n, uo[7]=MISO
(docs/isa.md §6.1). This driver bit-bangs it through the demo board's `tt` object,
so it needs no particular pin mux (and is slow: a PIO host gets the port's full rate, docs/isa.md §6.1).

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
A_ID, A_CTRL, A_FSET, A_FCLR, A_BANK, A_POWN_LO, A_POWN_HI, A_OD, A_PINS, A_POUT, A_POE, A_HOSTCFG = range(0x800, 0x80C)
CORE_BASE, CORE_STRIDE = 0x400, 0x20
C_CLKDIV, C_PINCTRL0, C_PINCTRL1, C_SHIFTCTRL, C_ENTRY, C_WRAP, C_CRCCTRL, C_CRC = range(8)
C_PC, C_STATUS, C_R0, C_ISR, C_OSR, C_FIFO = 8, 9, 0xA, 0xE, 0xF, 0x10
ST_HALTED, ST_FAULT = 1 << 13, 1 << 14   # STATUS bits

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
        r = self.xfer(bytes([CMD_READ_FIXED if fixed else CMD_READ, addr >> 8, addr & 0xFF, 0]), 4 * n)[4:]  # 0: dummy byte
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
        """Restart the cores in mask (PC = ENTRY) and enable them."""
        en = self.read(A_CTRL) & 15
        self.write(A_CTRL, en | mask | (mask << 8))

    def stop(self, mask):
        """Disable the cores in mask; their state is frozen (enable() resumes)."""
        en = self.read(A_CTRL) & 15
        self.write(A_CTRL, en & ~mask)

    def enable(self, mask):
        """Resume the cores in mask where they stopped (no restart)."""
        self.write(A_CTRL, (self.read(A_CTRL) & 15) | mask)

    def clear_fifos(self, mask):
        """Empty both FIFOs of the cores in mask."""
        self.write(A_CTRL, (self.read(A_CTRL) & 15) | (mask << 16))

    def own_pins(self, core, pins):
        """Give logical pins (docs/isa.md §2) to core: only the owner drives them."""
        lo, hi = self.read(A_POWN_LO, 2)
        for p in pins:
            if p < 16:
                lo = (lo & ~(3 << 2 * p)) | (core << 2 * p)
            else:
                hi = (hi & ~(3 << 2 * (p - 16))) | (core << 2 * (p - 16))
        self.write(A_POWN_LO, [lo, hi])

    def status(self, core):
        return self.read(self.core_reg(core, C_STATUS))

    def push(self, core, word, tries=10000):
        """Push one TX FIFO word, waiting for room. Raises if the core halts or
        faults, or if the FIFO stays full for `tries` polls."""
        for _ in range(tries):
            st = self.status(core)
            if st & 15 < self.depth:
                self.write(self.core_reg(core, C_FIFO), word, fixed=True)
                return
            if st & (ST_HALTED | ST_FAULT):
                raise RuntimeError("core %d %s, TX FIFO full" % (core, "faulted" if st & ST_FAULT else "halted"))
        raise RuntimeError("core %d TX FIFO still full" % core)

    def pop(self, core):
        """Return one RX FIFO word, or None if empty."""
        if (self.status(core) >> 4) & 15 == 0:
            return None
        return self.read(self.core_reg(core, C_FIFO), fixed=True)

    # -- 10BASE-T (firmware/eth10_tx.pasm, 40 MHz clock)
    def eth10_send(self, core, frame, flag=0):
        """Send one Ethernet frame: destination MAC .. payload, at most 60 bytes
        (padded to 60; the FCS is appended here). 64 bytes fit the joined FIFO,
        so this needs no streaming. Returns at once; see eth10_busy()."""
        frame = bytes(frame)
        assert len(frame) <= 60
        frame += bytes(60 - len(frame))
        frame += _crc32(frame).to_bytes(4, "little")
        self.write(self.core_reg(core, C_R0), 8 * len(frame) - 1)
        for i in range(0, len(frame), 4):
            self.push(core, int.from_bytes(frame[i:i + 4], "little"))
        self.write(A_FSET, 1 << flag)

    def eth10_busy(self, flag=0):
        return (self.read(A_FSET) >> flag) & 1


def _crc32(data):
    try:
        import binascii
        return binascii.crc32(data) & 0xFFFFFFFF
    except (ImportError, AttributeError):
        c = 0xFFFFFFFF
        for b in data:
            c ^= b
            for _ in range(8):
                c = (c >> 1) ^ (0xEDB88320 & -(c & 1))
        return c ^ 0xFFFFFFFF
