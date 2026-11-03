"""Host driver for pe_lab, the build identity and v2 demo fabric of the KV260
design (fpga/kv260/pe_lab.v, AXI4-Lite at 0xA0030000; register map there).

    lab = Lab()                          # /dev/mem (root)
    print(lab.identity())                # RTL revision etc. the bitstream was built from
    lab.mode(Lab.PG)                     # the pattern generator drives the uio pads
    lab.pg_play([(100, 0x01), (107, 0x03)])   # (gclk cycle from the start, value)
"""
import mmap
import os
import time

import fabric_guard

BASE = 0xA0030000
MAGIC = 0x4C414231
MODE, PG_LEN, PG_CTRL, PG_GO, PG_INIT, PG_D0, PG_CYC = 0x100, 0x110, 0x114, 0x118, 0x11C, 0x120, 0x124
PG_MEM, PG_DEPTH = 0x4000, 1024
SPID_DELAY, SPID_ARM, SPID_PER, SPID_RESP, SPID_RX = 0x200, 0x204, 0x208, 0x210, 0x220


class LabMem:
    """32-bit accesses to pe_lab's 64 KB window through /dev/mem."""

    def __init__(self):
        fabric_guard.require_app()           # before anything maps the fabric: see that file
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


def _hex160(words):
    return "".join(f"{w:08x}" for w in words)


class Lab:
    GPIO, PG, SPI, I2C = 0, 1, 2, 3

    def __init__(self, mem=None):
        self.mem = LabMem() if mem is None else mem
        if self.mem.rd(0) != MAGIC:
            raise RuntimeError("pe_lab not found (MAGIC 0x%08X): is a protoemu app with "
                               "pe_lab (fpga/kv260 >= kv260-v2) loaded?" % self.mem.rd(0))

    # -- identity
    def identity(self):
        rd = self.mem.rd
        flags = rd(0x04)
        t = rd(0x08)
        return {
            "rtl_rev": _hex160([rd(0x10 + 4 * i) for i in range(5)]),
            "head": _hex160([rd(0x30 + 4 * i) for i in range(5)]),
            "dirty": bool(flags & 1),
            "clk_div": (flags >> 8) & 0xFF,
            "build_time": t,
            "build_utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(t)),
            "digest": f"{rd(0x0C):08x}",
        }

    def mode(self, m):
        self.mem.wr(MODE, m)

    # -- pattern generator
    def pg_busy(self):
        return self.mem.rd(PG_GO) & 1

    def pg_wait(self, timeout=5.0):
        t = time.monotonic()
        while self.pg_busy():
            if time.monotonic() - t > timeout:
                raise RuntimeError("pattern generator stuck busy")

    def pg_set(self, value, alt=None):
        """Drive a constant value now (a start with no entries)."""
        self.pg_wait()
        if alt is not None:
            self.mem.wr(PG_CTRL, int(alt))
        self.mem.wr(PG_INIT, value & 0xFF)
        self.mem.wr(PG_LEN, 0)
        self.mem.wr(PG_GO, 1)
        self.pg_wait()

    def pg_load(self, sched, init=None, alt=False):
        """sched: [(t, value)], t = gclk cycles from the start, strictly
        increasing, t[0] >= 1, at most 1024 entries; init: the value driven at
        the start (default: keep the current one)."""
        if not 0 < len(sched) <= PG_DEPTH:
            raise ValueError("1..%d entries" % PG_DEPTH)
        ts = [t for t, _ in sched]
        if ts[0] < 1 or any(b <= a for a, b in zip(ts, ts[1:])) or any(b - a >= 1 << 24 for a, b in zip(ts, ts[1:])):
            raise ValueError("times must start >= 1 and increase by 1 .. 2**24-1")
        self.pg_wait()
        wr = self.mem.wr
        for i, (t, v) in enumerate(sched):
            d = sched[i + 1][0] - t if i + 1 < len(sched) else 1
            wr(PG_MEM + 4 * i, (d << 8) | (v & 0xFF))
        wr(PG_CTRL, int(alt))
        wr(PG_D0, ts[0])
        wr(PG_LEN, len(sched))
        if init is not None:
            wr(PG_INIT, init & 0xFF)
        else:
            wr(PG_INIT, self.mem.rd(PG_INIT))

    def pg_start(self):
        self.mem.wr(PG_GO, 1)

    def pg_cycles(self):
        return self.mem.rd(PG_CYC)

    # -- SPI device
    def spid_arm(self, resp, delay):
        """Load the 16 response bytes, set DELAY (0..31) and arm: byte 0's MSB goes on MISO."""
        resp = bytes(resp).ljust(16, b"\0")
        wr = self.mem.wr
        wr(SPID_DELAY, delay)
        for i in range(4):
            wr(SPID_RESP + 4 * i, int.from_bytes(resp[4 * i:4 * i + 4], "big"))
        wr(SPID_ARM, 1)

    def spid_status(self):
        """(SCK rises since the arm, shortest and longest rise-to-rise interval in dclk cycles)."""
        per = self.mem.rd(SPID_PER)
        return self.mem.rd(SPID_ARM) & 0xFFFF, per & 0xFF, (per >> 8) & 0xFF

    def spid_rx(self):
        """The last 16 bytes the device took from MOSI, oldest first."""
        return b"".join(self.mem.rd(SPID_RX + 4 * i).to_bytes(4, "big") for i in range(4))
