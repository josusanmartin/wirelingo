"""A pins-only ATA PIO device, written from public sources, not from any firmware.

Sources, by figure:
  [ATA3]  AT Attachment-3 Interface, working draft revision 6, Table 21 "PIO data
          transfer timing" (t0, t1, t2 16-bit, t3, t4, t5, t6, t6Z, t9, tRd, tA,
          tB for modes 0-4) and its notes; clause 6.2 registers; clause 8
          commands; clause 9.2 software reset (BSY within 400 ns, register
          signature); https://www.pjrc.com/docs/specs/disk/ata3r6_10.html and
          its sibling pages ata3r6_5/6/8/9.html
  [LINUX] t2 for 8-bit register access, 290 ns in modes 0-2: `ataspec_t2_8` in
          the Linux pata_mpc52xx driver (seen as a search-result excerpt; the
          ATA-3 table lists only the 16-bit t2). Secondary source.
  [XTIDE] SET FEATURES subcommands 01h enable and 81h disable 8-bit PIO, CFA
          feature set only: XTIDE Universal BIOS IdeRegisters.inc.
  [LANDIS] Hale Landis on the T13 reflector, 2006-05-23: in CF 8-bit mode all PIO
          data transfers use only DD7:0, n*512 accesses per DRQ block.
          https://mail-archive.com/forum@t13.org/msg02889.html
From memory, not checked against a source: the 2 ms a host waits after clearing
SRST; that SRST returns 8-bit mode to its default (the conservative choice:
the host must re-enable it); the status value used when the device is absent.

The CompactFlash specification itself could not be opened (the copy found
returned 404), so 8-bit behaviour rests on [XTIDE] and [LANDIS].

The class sees pad levels only. feed(t_ns, ...) gives it DA, CS0-, CS1-, DIOR-,
DIOW- and DD7:0 with the chip's OE. drive(t) returns what it puts on DD7:0;
iordy(t) its IORDY level. DD15:8 are not wired to the chip, which it records.
"""
from collections import namedtuple

Pio = namedtuple("Pio", "t0 t1 t2 t2_16 t3 t4 t5 t6 t6z t9 ta tb t2i")
# [ATA3] Table 21, except t2 here: 8-bit, [LINUX] `ataspec_t2_8`; and mode 2's t0,
# 330 ns, the 8-bit register figure, from memory (the table's 240 is 16-bit).
MODES = [
    Pio(t0=600, t1=70, t2=290, t2_16=165, t3=60, t4=30, t5=50, t6=5, t6z=30, t9=20, ta=35, tb=1250, t2i=0),
    Pio(t0=383, t1=50, t2=290, t2_16=125, t3=45, t4=20, t5=35, t6=5, t6z=30, t9=15, ta=35, tb=1250, t2i=0),
    Pio(t0=330, t1=30, t2=290, t2_16=100, t3=30, t4=15, t5=20, t6=5, t6z=30, t9=10, ta=35, tb=1250, t2i=0),
    Pio(t0=180, t1=30, t2=80, t2_16=80, t3=30, t4=10, t5=20, t6=5, t6z=30, t9=10, ta=35, tb=1250, t2i=70),
    Pio(t0=120, t1=25, t2=70, t2_16=70, t3=20, t4=10, t5=20, t6=5, t6z=30, t9=10, ta=35, tb=1250, t2i=25),
]
MODE0 = MODES[0]

BSY, DRDY, DF, DSC, DRQ, CORR, IDX, ERR = 0x80, 0x40, 0x20, 0x10, 0x08, 0x04, 0x02, 0x01
ABRT = 0x04
STATUS_WINDOW_NS = 400      # [ATA3] 6.2: BSY or DRQ set within 400 ns of the Command write


class AtaDevice:
    def __init__(self, cfa=True, nsectors=64, exec_ns=20_000, between_ns=5_000, power_busy_ns=0,
                 iordy_low_ns=0, iordy_stuck=False, stuck_bsy=False, reset_busy_ns=100_000,
                 tm=MODE0, model="WIRELINGO BLIND TEST ATA", status_window_ns=STATUS_WINDOW_NS,
                 max_mode=4, err_with_drq=False):
        self.tm = tm
        self.window = status_window_ns
        self.max_mode = max_mode
        self.err_with_drq = err_with_drq    # ERR set while DRQ is up: a block must be refused
        self.status_reads = 0       # Status, which clears a pending interrupt; Alternate Status does not
        self.cfa = cfa
        self.exec_ns, self.between_ns, self.reset_busy_ns = exec_ns, between_ns, reset_busy_ns
        self.iordy_low_ns, self.iordy_stuck, self.stuck_bsy = iordy_low_ns, iordy_stuck, stuck_bsy
        self.model = model
        self.errors = []
        self.seen = {}              # smallest t0, t1, t2, t3, t4, t9 seen, ns
        self.cycles = []            # (t_fall, t_rise, "r"|"w", cs0_n, cs1_n, da, value)
        self.commands = []          # (t, code)
        self.sectors = {i: bytes((i * 7 + j * 3) & 255 for j in range(512)) for i in range(nsectors)}
        self.written = {}
        self.bus8 = False
        self.highbyte_lost = 0      # 16-bit Data cycles: DD15:8 did not reach the chip
        # task file
        self.err = 1
        self.feat = 0
        self.sc = self.sn = 1
        self.cl = self.ch = 0
        self.dh = 0
        self.ctl = 0
        self.status = DRDY | DSC
        self.busy_until = power_busy_ns
        self.after = None           # state applied when BSY falls
        self.stale_until = 0        # Status still shows the old value until here
        self.stale = 0
        self.buf = bytearray()
        self.pos = 0
        self.dir = None
        self.lba = 0
        self.left = 0
        self.srst_t = None
        # pads
        self.addr = (1, 1, 0)
        self.t_addr = -1e12
        self.dd = 0
        self.oe = 0
        self.t_dd = -1e12
        self.dior = self.diow = 1
        self.t_fall = None
        self.t_prev_fall = None
        self.t_rise = -1e12
        self.kind = None
        self.out_on = False
        self.out_val = 0
        self.out_valid_from = 0
        self.out_end = None         # valid until here
        self.out_off = None         # released at this time
        self.iordy_rise = None      # IORDY active again at this time (None = active)
        self.iordy_from = 0         # and negated only from here: tA after the strobe, the latest allowed
        self.max_low = 0

    # ---------------------------------------------------------------- helpers
    def _see(self, name, ns):
        """Keep the smallest value of a figure seen at the pads, in ns."""
        if ns < 1e9:
            self.seen[name] = min(self.seen.get(name, ns), round(ns, 3))

    def _e(self, t, msg):
        self.errors.append(f"{t:.0f} ns: {msg}")

    def selected(self):
        return not self.dh & 0x10       # this model is device 0 alone

    def _tick(self, t):
        if self.after and t >= self.busy_until and not self.stuck_bsy:
            fn, self.after = self.after, None
            fn(t)

    def _shown_status(self, t):
        self._tick(t)
        if t < self.stale_until:
            return self.stale
        if self.stuck_bsy or t < self.busy_until:
            return (self.status | BSY) & ~DRQ
        return self.status

    def _decode(self):
        cs0, cs1, da = self.addr
        if cs0 == 0 and cs1 == 1:
            return "cmd", da
        if cs0 == 1 and cs1 == 0:
            return "ctl", da
        return None, da

    # ---------------------------------------------------------------- inputs
    def feed(self, t, da, cs0_n, cs1_n, dior_n, diow_n, dd, chip_oe):
        self._tick(t)
        addr = (cs0_n, cs1_n, da)
        if addr != self.addr:
            if self.kind:
                self._e(t, f"address changed during a strobe: {self.addr} -> {addr}")
            self._see("t9", t - self.t_rise)
            if t - self.t_rise < self.tm.t9:
                self._e(t, f"t9: address changed {t - self.t_rise:.0f} ns after the strobe rose")
            self.addr, self.t_addr = addr, t
        if self.out_on and chip_oe and (self.out_off is None or t < self.out_off):
            self._e(t, f"contention: chip drives DD {chip_oe:02x} while the device does")
        if (chip_oe, dd if chip_oe else None) != (self.oe, self.dd if self.oe else None):
            if self.oe and self.last_kind == "w":
                self._see("t4", t - self.t_rise)
            if self.oe and t - self.t_rise < self.tm.t4 and self.last_kind == "w":
                self._e(t, f"t4: DD changed {t - self.t_rise:.0f} ns after DIOW- rose")
            self.t_dd = t
        self.dd, self.oe = dd, chip_oe
        if not dior_n and not diow_n:
            self._e(t, "DIOR- and DIOW- both active")
        if self.dior and not dior_n:
            self._fall(t, "r")
        if self.diow and not diow_n:
            self._fall(t, "w")
        if not self.dior and dior_n:
            self._rise(t)
        if not self.diow and diow_n:
            self._rise(t)
        self.dior, self.diow = dior_n, diow_n

    last_kind = None

    def _fall(self, t, kind):
        self.kind, self.t_fall = kind, t
        block, reg = self._decode()
        if block is None:
            self._e(t, f"strobe with no chip select, or both: {self.addr}")
        self._see("t1", t - self.t_addr)
        if t - self.t_addr < self.tm.t1:
            self._e(t, f"t1: {t - self.t_addr:.0f} ns")
        if self.t_prev_fall is not None:
            self._see("t0", t - self.t_prev_fall)
        if self.t_prev_fall is not None and t - self.t_prev_fall < self.tm.t0:
            self._e(t, f"t0: {t - self.t_prev_fall:.0f} ns")
        self.t_prev_fall = t
        self._see("t2i", t - self.t_rise)
        if t - self.t_rise < self.tm.t2i:
            self._e(t, f"t2i: {t - self.t_rise:.0f} ns")
        self.iordy_from = t + self.tm.ta
        if self.iordy_stuck:
            self.iordy_rise = float("inf")
        elif self.iordy_low_ns:
            self.iordy_rise = t + self.iordy_low_ns
            if self.iordy_low_ns > self.tm.tb:
                self._e(t, "test bug: IORDY low longer than tB")
        else:
            self.iordy_rise = None
        if kind == "r":
            if self.oe:
                self._e(t, f"contention: chip drives DD {self.oe:02x} into a read")
            self.out_val = self._read_reg(t, block, reg) if block else 0xFF
            self.out_on = block is not None     # [ATA3] Table 6: no valid address, no drive
            # [ATA3] t5: valid t5 before the end of a t2-minimum strobe, or
            # at IORDY's return (tRd = 0). Before that the bus is garbage.
            self.out_valid_from = t + self.tm.t2 - self.tm.t5
            if self.iordy_rise is not None:
                self.out_valid_from = max(self.out_valid_from, self.iordy_rise)
            self.out_end = self.out_off = None

    def _rise(self, t):
        kind, tf = self.kind, self.t_fall
        if kind is None:                # the second of two strobes that rose together
            return
        if self.iordy_rise is not None and t < self.iordy_rise and not self.iordy_stuck:
            self._e(t, "strobe ended with IORDY negated")
        self.t_rise = t
        self.last_kind = kind
        self.kind = None
        self._see("t2", t - tf)
        if t - tf < self.tm.t2:
            self._e(t, f"t2: {t - tf:.0f} ns")
        self.max_low = max(self.max_low, t - tf)
        block, reg = self._decode()
        value = None
        if kind == "r":
            value = self.out_val
            self.out_end = t + self.tm.t6
            self.out_off = t + self.tm.t6z
        else:
            self._see("t3", t - self.t_dd)
            if t - self.t_dd < self.tm.t3:
                self._e(t, f"t3: {t - self.t_dd:.0f} ns")
            if self.oe != 0xFF:
                self._e(t, f"write with DD undriven, chip OE {self.oe:02x}")
            value = self.dd
            if block:
                self._write_reg(t, block, reg, value)
        self.cycles.append((tf, t, kind, self.addr[0], self.addr[1], self.addr[2], value))

    # ---------------------------------------------------------------- registers
    def _read_reg(self, t, block, reg):
        if not self.selected():
            return 0x00
        if block == "ctl":
            return self._shown_status(t) if reg == 6 else 0xFF
        if reg == 0:
            return self._data_read(t)
        if reg == 7:
            self.status_reads += 1
            return self._shown_status(t)
        return {1: self.err, 2: self.sc, 3: self.sn, 4: self.cl, 5: self.ch, 6: self.dh}[reg]

    def _write_reg(self, t, block, reg, v):
        if block == "ctl":
            if reg == 6:
                self._control(t, v)
            return
        if reg == 0:
            self._data_write(t, v)
            return
        s = self._shown_status(t)
        if (t < self.busy_until or self.stuck_bsy) or self.status & DRQ:
            self._e(t, f"command block register {reg} written while BSY or DRQ (status {s:02x})")
        if reg == 7:
            self._command(t, v)
            return
        if reg == 1:
            self.feat = v
        elif reg == 2:
            self.sc = v
        elif reg == 3:
            self.sn = v
        elif reg == 4:
            self.cl = v
        elif reg == 5:
            self.ch = v
        elif reg == 6:
            self.dh = v

    def _control(self, t, v):
        old, self.ctl = self.ctl, v
        if v & 4 and not old & 4:
            self.srst_t = t
            self.busy_until = float("inf")
            self.after = None
            self.status = 0
            self.stale_until = t + self.window
            self.stale = DRDY | DSC
        elif old & 4 and not v & 4:
            if t - self.srst_t < 5000:
                self._e(t, f"SRST held only {t - self.srst_t:.0f} ns (ATA-3 note 25: 5 us)")
            self.bus8 = False
            self.tm = MODE0
            self.buf, self.pos, self.dir = bytearray(), 0, None
            self.busy_until = t + self.reset_busy_ns

            def done(t2):
                self.status = DRDY | DSC
                self.err, self.sc, self.sn, self.cl, self.ch, self.dh = 1, 1, 1, 0, 0, 0
            self.after = done

    def _begin(self, t, fn, ns=None):
        """BSY for ns, the status stale for the first 400 ns, the worst case allowed."""
        self.stale = self.status
        self.stale_until = t + self.window
        self.status = DRDY | DSC
        self.busy_until = t + (self.exec_ns if ns is None else ns)
        self.after = fn

    def _abort(self, t):
        self.status = DRDY | DSC | ERR
        self.err = ABRT

    def _command(self, t, c):
        self.commands.append((t, c))
        if not self.selected():
            return
        self.err = 0
        if c == 0xEC:
            self._begin(t, self._identify)
        elif c == 0xEF:
            self._begin(t, self._set_features)
        elif c in (0x20, 0x21):
            self._begin(t, self._rd_setup)
        elif c in (0x30, 0x31):
            self._begin(t, self._wr_setup)
        else:
            self._begin(t, self._abort)

    def _identify(self, t):
        w = [0] * 256
        w[0] = 0x848A if self.cfa else 0x0040
        w[1], w[3], w[6] = 16, 4, 32
        m = self.model.ljust(40)[:40].encode()
        for i in range(20):
            w[27 + i] = m[2 * i] << 8 | m[2 * i + 1]
        n = len(self.sectors)
        w[60], w[61] = n & 0xFFFF, n >> 16
        w[49] = 0x0200                  # LBA supported
        self.left = 1
        self._load(b"".join(x.to_bytes(2, "little") for x in w), "in")

    def _load(self, data, d):
        self.buf, self.pos, self.dir = bytearray(data), 0, d
        self.status = DRDY | DSC | DRQ | (ERR if self.err_with_drq else 0)

    def _set_features(self, t):
        if self.feat == 0x01 and self.cfa:
            self.bus8 = True
        elif self.feat == 0x81 and self.cfa:
            self.bus8 = False
        elif self.feat == 0x03 and self.sc & 0xF8 == 0x08 and self.sc & 7 <= self.max_mode:
            self.tm = MODES[self.sc & 7]    # [ATA3] 8.x Set transfer mode, Table 16
        else:
            self._abort(t)

    def _lba(self):
        return (self.dh & 15) << 24 | self.ch << 16 | self.cl << 8 | self.sn

    def _rd_setup(self, t):
        if not self.dh & 0x40:
            return self._abort(t)       # this model does LBA only
        self.lba, self.left = self._lba(), self.sc or 256
        self._rd_next(t)

    def _rd_next(self, t):
        if self.lba not in self.sectors:
            self.status = DRDY | DSC | ERR
            self.err = 0x10             # IDNF
            return
        self._load(self.sectors[self.lba], "in")

    def _wr_setup(self, t):
        if not self.dh & 0x40:
            return self._abort(t)
        self.lba, self.left = self._lba(), self.sc or 256
        self._load(bytes(512), "out")

    # ---------------------------------------------------------------- data register
    def _data_read(self, t):
        self._tick(t)
        if self.dir != "in" or not self.status & DRQ or t < self.busy_until:
            self._e(t, "Data read without DRQ")
            return 0xFF
        v = self.buf[self.pos]
        if self.bus8:
            self.pos += 1
        else:                           # a 16-bit cycle: the high byte goes nowhere
            self.pos += 2
            self.highbyte_lost += 1
        if self.pos >= len(self.buf):
            self._rd_block_done(t)
        return v

    def _rd_block_done(self, t):
        self.dir = None
        self.left -= 1
        self.lba += 1
        if self.left:
            self.status = DRDY | DSC
            self.busy_until = t + self.between_ns
            self.after = self._rd_next
        else:
            self.status = DRDY | DSC

    def _data_write(self, t, v):
        self._tick(t)
        if self.dir != "out" or not self.status & DRQ or t < self.busy_until:
            self._e(t, "Data write without DRQ")
            return
        self.buf[self.pos] = v
        if self.bus8:
            self.pos += 1
        else:
            self._e(t, "16-bit Data write: DD15:8 are not driven")
            self.pos += 2
        if self.pos >= len(self.buf):
            self.written[self.lba] = bytes(self.buf)
            self.sectors[self.lba] = bytes(self.buf)
            self.left -= 1
            self.lba += 1
            self.dir = None
            self.status = DRDY | DSC
            self.busy_until = t + self.between_ns
            if self.left:
                self.after = lambda t2: self._load(bytes(512), "out")

    # ---------------------------------------------------------------- outputs
    def next_change(self, t):
        c = [self.out_valid_from if self.out_on else None, self.out_end, self.out_off, self.iordy_rise,
             self.iordy_from if self.iordy_rise is not None else None,
             self.busy_until if self.after else None]
        c = [x for x in c if x is not None and x > t and x != float("inf")]
        return min(c) if c else None

    def drive(self, t):
        """(value, oe) on DD7:0: garbage until valid, valid to t6, garbage to t6Z."""
        self._tick(t)
        if self.out_on and self.out_off is not None and t >= self.out_off:
            self.out_on = False
        if not self.out_on:
            return 0, 0
        valid = t >= self.out_valid_from and (self.out_end is None or t < self.out_end)
        return (self.out_val if valid else ~self.out_val & 0xFF), 0xFF

    def iordy(self, t):
        """IORDY as the pad sees it. Active (1) when no strobe is extended.
        The device has tA = 35 ns from the strobe to negate it, and this model
        takes all of it: a host that looks earlier misses the extension."""
        if self.iordy_rise is None or t < self.iordy_from:
            return 1
        if t >= self.iordy_rise:
            self.iordy_rise = None
            return 1
        return 0
