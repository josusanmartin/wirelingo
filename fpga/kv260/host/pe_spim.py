"""Host driver for pe_spim, the fabric SPI master of the KV260 design
(fpga/kv260/pe_spim.v, AXI4-Lite at 0xA0020000; register map there).

    spim = Spim()                       # /dev/mem (root)
    spim.select(fabric=True)            # host-port pins from the master
    spim.div, spim.quad = 1, True       # SCK = master clock, quad commands
    pe.transport = spim                 # tools/micropython/protoemu.ProtoEmu
    pe.read(0x800)

`Spim` is a ProtoEmu transport (write/read). Quad commands need
HOSTCFG.QUAD_EN = 1, which `enable_quad()` sets with a 1-bit write.
"""
import mmap
import os
import time

BASE = 0xA0020000
CMD, LEN, CFG, SEL, GO, PCYC, MCYC = 0x8000, 0x8004, 0x8008, 0x800C, 0x8010, 0x8018, 0x801C
PMOD0, PMOD1, PMODIN, VERSION, TIMING = 0x8020, 0x8024, 0x8028, 0x802C, 0x8030
BUF_WORDS = 4096
VERSION_VALUE = 0x53504D01


class SpimMem:
    """32-bit accesses to pe_spim's 64 KB window through /dev/mem."""

    def __init__(self):
        fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
        try:
            self._map = mmap.mmap(fd, 0x10000, offset=BASE)
        finally:
            os.close(fd)
        self.mv = memoryview(self._map).cast("I")

    def rd(self, off):
        return self.mv[off >> 2]

    def wr(self, off, v):
        self.mv[off >> 2] = v & 0xFFFFFFFF

    def buf_write(self, words):
        mv = self.mv
        for i, w in enumerate(words):    # one 32-bit store each: no memcpy on device memory
            mv[i] = w

    def buf_read(self, n):
        mv = self.mv
        return [mv[i] for i in range(n)]


class Spim:
    def __init__(self, mem=None, div=1, quad=False):
        self.mem = SpimMem() if mem is None else mem
        if self.mem.rd(VERSION) != VERSION_VALUE:
            raise RuntimeError("pe_spim not found: is the protoemu app loaded?")
        self.div, self.quad = div, quad
        self.last_pcyc = self.last_mcyc = None

    def select(self, fabric, alt=False):
        """Host-port pins from the master (fabric) or the GPIO path; master clock
        protoemu's (alt=False) or the alternate MMCM clock."""
        self.wait_idle()
        self.mem.wr(SEL, int(fabric) | (int(alt) << 1))

    def timing(self, setup=2, hold=3, gap=8):
        self.mem.wr(TIMING, (setup & 15) | ((hold & 15) << 4) | ((gap & 15) << 8))

    def wait_idle(self, timeout=1.0):
        t = time.monotonic()
        while self.mem.rd(GO) & 1:
            if time.monotonic() - t > timeout:
                raise RuntimeError("pe_spim stuck busy")

    def transfer(self, cmd, addr, words=(), nread=0, quad=None, div=None):
        """One host-port transaction. Returns the read words (reads) or []."""
        quad = self.quad if quad is None else quad
        div = self.div if div is None else div
        rd = cmd & 1
        n = nread if rd else len(words)
        if not 0 <= n <= BUF_WORDS:
            raise ValueError("at most %d words per transaction" % BUF_WORDS)
        self.wait_idle()
        if not rd:
            self.mem.buf_write(words)
        self.mem.wr(CMD, (cmd & 0xFF) | ((addr & 0xFFFF) << 8))
        self.mem.wr(LEN, n)
        self.mem.wr(CFG, (div & 0xFF) | (int(quad) << 8) | (rd << 9))
        self.mem.wr(GO, 1)
        self.wait_idle()
        self.last_pcyc, self.last_mcyc = self.mem.rd(PCYC), self.mem.rd(MCYC)
        return self.mem.buf_read(n) if rd else []

    # -- ProtoEmu transport
    def _cmd(self, base, fixed):
        return (base | (0x08 if fixed else 0)) | (0x40 if self.quad else 0)

    def write(self, addr, words, fixed=False):
        if isinstance(words, int):
            words = [words]
        words = list(words)
        for i in range(0, len(words), BUF_WORDS):
            self.transfer(self._cmd(0x02, fixed), addr if fixed else addr + i, words[i:i + BUF_WORDS])

    def read(self, addr, n=1, fixed=False):
        out = []
        while len(out) < n:
            k = min(BUF_WORDS, n - len(out))
            out += self.transfer(self._cmd(0x03, fixed), addr if fixed else addr + len(out), nread=k)
        return out[0] if n == 1 else out

    def xfer(self, data, nread=0):
        raise NotImplementedError("use write()/read()")

    def enable_quad(self, on=True):
        """Set HOSTCFG.QUAD_EN with a 1-bit write; later transfers use quad commands."""
        self.quad = False
        self.write(0x80B, int(on))
        self.quad = bool(on)

    # -- PMOD (kv260_top.v): pin -> TT pin index (0-7 uio, 8-14 uo0-6, 16-20 ui0-4) or None
    def pmod(self, mapping):
        f = [0] * 8
        for pin, tt in mapping.items():
            if tt is not None:
                f[pin] = 0x80 | (tt & 31)
        self.mem.wr(PMOD0, f[0] | f[1] << 8 | f[2] << 16 | f[3] << 24)
        self.mem.wr(PMOD1, f[4] | f[5] << 8 | f[6] << 16 | f[7] << 24)

    def pmod_in(self):
        return self.mem.rd(PMODIN) & 0xFF
