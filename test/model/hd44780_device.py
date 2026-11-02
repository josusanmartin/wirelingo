"""A pins-only HD44780U far end, written from the datasheet, not from any firmware.

Source for every figure below unless labelled otherwise: Hitachi HD44780U
datasheet (ADE-207-272(Z), rev 0.0), copy at
https://soldered.com/cdn/shop/files/Soldered_HD44780_datasheet.pdf
  * bus timing: "Bus Timing Characteristics" tables, p.49 (VCC 2.7-4.5 V) and
    p.52 (VCC 4.5-5.5 V); figures 25, 26
  * RS/RW meaning: Table 1, p.9; execution times: Table 6, p.24-25
  * tADD = 1.5 / fOSC after BF falls: Figure 10, p.25
  * init by instruction, 4 bit: Figure 24, p.46 (BF cannot be checked before
    the third 0011; wait > 40 ms from VCC = 2.7 V, > 4.1 ms, > 100 us)
  * 4-bit transfer high nibble first, BF read needs both nibbles: Figure 9,
    p.22; Figure 17, p.33
From memory, not checked against the text extraction (the PDF's text layer
lost these sentences): DDRAM read needs an address set or cursor shift first;
DB pins have pull-up MOS; the internal-reset busy period is 10 ms.

The class sees only pad levels. feed(t_ns, ...) gives it RS, RW, E, the DB7..4
levels and whether the chip drives them. drive(t_ns) says what it drives.
next_change(t_ns) says when that may change next. It never looks at a register
or at a program.
"""
from collections import namedtuple

Timing = namedtuple("Timing", "tcyc pweh tas tah tdsw th tddr tdhr")
# HD44780U minimums (tddr is a maximum), in ns
V3 = Timing(tcyc=1000, pweh=450, tas=60, tah=20, tdsw=195, th=10, tddr=360, tdhr=5)    # VCC 2.7-4.5 V
V5 = Timing(tcyc=500, pweh=230, tas=40, tah=10, tdsw=80, th=10, tddr=160, tdhr=5)      # VCC 4.5-5.5 V

FOSC_KHZ = 270          # Table 6 quotes execution times at fcp/fOSC = 270 kHz
T_EXEC_NS = 37_000
T_CLEAR_NS = 1_520_000  # clear display and return home
T_RESET_BUSY_NS = 10_000_000   # from memory: internal reset, BF = 1 for 10 ms
T_POWER_NS = 40_000_000 # Figure 24: > 40 ms after VCC rises to 2.7 V
T_INIT1_NS = 4_100_000  # Figure 24: > 4.1 ms after the first 0011
T_INIT2_NS = 100_000    # Figure 24: > 100 us after the second 0011


def tadd_ns(fosc_khz=FOSC_KHZ):
    return round(1.5e6 / fosc_khz)


class Hd44780:
    """Power-on state is the internal-reset state: 8-bit interface, display off."""

    def __init__(self, timing=V3, scale=1.0, power_on_busy=True, stuck_busy=False, t_power=0):
        self.tm = timing
        self.scale = scale              # > 1: a slower oscillator stretches execution times
        self.stuck_busy = stuck_busy
        self.t_power = t_power          # None: already powered, skip the 40 ms check
        self.fs_count = 0               # 8-bit function sets seen, for Figure 24
        self.errors = []                # protocol and timing violations, as text
        self.seen = {}                  # smallest tAS, tAH, tH, PWEH, tcycE, tDSW seen, ns
        self.log = []                   # (t_ns, "ir"|"dr", byte) accepted writes
        self.reads = []                 # (t_ns, "bf"|"dr", byte, nibble-index) reads started
        self.e_rises = []
        self.e_falls = []
        self.busy_until = T_RESET_BUSY_NS if power_on_busy else 0
        self.ac_due = None              # (t_ns, new AC): AC updates tADD after BF falls
        self.dl8 = True                 # interface data length
        self.pending_hi = None          # high nibble waiting for its low nibble
        self.read_low = False           # next read E pulse gives the low nibble
        self.lines2 = self.font10 = False
        self.display = self.cursor = self.blink = False
        self.inc, self.shift = True, False
        self.ac, self.ac_cg = 0, False
        self.ddram = bytearray(128)
        self.cgram = bytearray(64)
        self.dr = 0                     # output data register, loaded by an address set
        self.dr_valid = False
        # pad state
        self.rs = self.rw = self.e = 0
        self.db = 0
        self.oe = 0
        self.t_rs = self.t_rw = self.t_db = -1e12
        self.t_e_r = self.t_e_f = -1e12
        self.t_e_prev_r = None
        self.out_byte = 0               # latched at E rise for a read
        self.out_on = False
        self.out_from = self.out_to = None

    # ---------------------------------------------------------------- helpers
    def _busy(self, t):
        return self.stuck_busy or t < self.busy_until

    def _settle(self, t):
        if self.ac_due and t >= self.ac_due[0]:
            self.ac = self.ac_due[1]
            self.ac_due = None

    def _see(self, name, ns):
        """Keep the smallest value of a figure seen at the pads, in ns."""
        if ns < 1e9:
            self.seen[name] = min(self.seen.get(name, ns), round(ns, 3))

    def _err(self, t, msg):
        self.errors.append(f"{t:.0f} ns: {msg}")

    def _start(self, t, ns):
        self.busy_until = t + ns * self.scale

    # ---------------------------------------------------------------- inputs
    def feed(self, t, rs, rw, e, db, chip_oe):
        """Pad levels at t_ns. db: DB7..4 as a 4-bit value as seen on the bus;
        chip_oe: 4-bit mask of the pads the chip drives."""
        self._settle(t)
        lcd_on = self.out_on and (self.out_to is None or t < self.out_to)
        if lcd_on and chip_oe:
            self._err(t, f"contention: chip drives DB {chip_oe:04b} while the LCD does")
        if (rs, rw) != (self.rs, self.rw):
            if e:
                self._err(t, "RS or R/W changed with E high")
            self._see("tAH", t - self.t_e_f)
            if t - self.t_e_f < self.tm.tah:
                self._err(t, f"tAH: RS or R/W changed {t - self.t_e_f:.0f} ns after E fell")
            if rs != self.rs:
                self.t_rs = t
            if rw != self.rw:
                self.t_rw = t
            self.rs, self.rw = rs, rw
        if db != self.db and not self.rw:
            self._see("tH", t - self.t_e_f)
            if t - self.t_e_f < self.tm.th:
                self._err(t, f"tH: DB changed {t - self.t_e_f:.0f} ns after E fell")
            self.t_db = t
        self.db = db
        self.oe = chip_oe
        if e and not self.e:
            self._e_rise(t)
        elif self.e and not e:
            self._e_fall(t)
        self.e = e

    def _e_rise(self, t):
        self.e_rises.append(t)
        self._see("tAS", t - max(self.t_rs, self.t_rw))
        if self.t_e_prev_r is not None:
            self._see("tcycE", t - self.t_e_prev_r)
        if t - max(self.t_rs, self.t_rw) < self.tm.tas:
            self._err(t, f"tAS: {t - max(self.t_rs, self.t_rw):.0f} ns")
        if self.t_e_prev_r is not None and t - self.t_e_prev_r < self.tm.tcyc:
            self._err(t, f"tcycE: {t - self.t_e_prev_r:.0f} ns")
        self.t_e_prev_r = t
        self.t_e_r = t
        if self.rw:
            self._read_begin(t)

    def _e_fall(self, t):
        self.e_falls.append(t)
        self.t_e_f = t
        self._see("PWEH", t - self.t_e_r)
        if t - self.t_e_r < self.tm.pweh:
            self._err(t, f"PWEH: {t - self.t_e_r:.0f} ns")
        if self.rw:
            if self.out_on:
                self.out_to = t + self.tm.tdhr
            return
        self._see("tDSW", t - self.t_db)
        if t - self.t_db < self.tm.tdsw:
            self._err(t, f"tDSW: {t - self.t_db:.0f} ns")
        if self.oe != 0xF:
            self._err(t, f"write latched with DB undriven (chip OE {self.oe:04b})")
        self._write_latched(t)

    # ---------------------------------------------------------------- reads
    def _read_byte(self, t):
        if not self.rs:
            return (0x80 if self._busy(t) else 0) | (self.ac & 0x7F)
        return self.dr

    def _read_begin(self, t):
        if self.oe:
            self._err(t, f"contention: chip drives DB {self.oe:04b} at a read's E rise")
        if self.rs and self._busy(t):
            self._err(t, "DR read while busy")
        if self.rs and self.ac_due:
            self._err(t, "DR read before the AC update (tADD)")
        if self.dl8 and self.fs_count < 3:
            self._err(t, "BF read before the third function set (Figure 24)")
        if self.dl8:
            v = self._read_byte(t)
            self._read_end_dr(t)
        else:
            if self.pending_hi is not None:
                self._err(t, "read in the middle of a 4-bit write")
                self.pending_hi = None
            if not self.read_low:
                self._rbyte = self._read_byte(t)
                v = self._rbyte
                self.reads.append((t, "dr" if self.rs else "bf", v, 0))
            else:
                v = (self._rbyte << 4) & 0xFF
                self.reads.append((t, "dr" if self.rs else "bf", v, 1))
                self._read_end_dr(t)
            self.read_low = not self.read_low
        self.out_byte = v
        self.out_on = True
        self.out_from = t + self.tm.tddr      # undefined until then
        self.out_to = None

    def _read_end_dr(self, t):
        if self.rs:
            self._next_ac()
            self.dr = self._ram(self.ac)
            self._start(t, T_EXEC_NS)
            self.ac_due = (self.busy_until + tadd_ns() * self.scale, self._pending_ac)
            self.ac = self._ac_before

    # ---------------------------------------------------------------- writes
    def _write_latched(self, t):
        nib = self.db & 0xF
        if self.read_low and not self.dl8:
            self._err(t, "write while a 4-bit read is half done")
            self.read_low = False
        if self.dl8:
            # DB3..0 are not wired: the pull-ups read 1 (memory, see above)
            self._byte(t, (nib << 4) | 0xF)
        elif self.pending_hi is None:
            self.pending_hi = nib
        else:
            b = (self.pending_hi << 4) | nib
            self.pending_hi = None
            self._byte(t, b)

    def _byte(self, t, b):
        if self._busy(t):
            self._err(t, f"{'DR' if self.rs else 'IR'} write {b:02x} while busy")
        if self.ac_due:
            # Strict reading of Figure 10: the datasheet only says when AC is
            # updated. This model refuses to guess what a write in that window does.
            self._err(t, f"{'DR' if self.rs else 'IR'} write {b:02x} before the AC update (tADD)")
            self._settle(t + 1e9)
        self.log.append((t, "dr" if self.rs else "ir", b))
        if self.rs:
            self._ram_write(b)
            self._next_ac()
            self._start(t, T_EXEC_NS)
            self.ac_due = (self.busy_until + tadd_ns() * self.scale, self._pending_ac)
            self.ac = self._ac_before
            return
        self._instr(t, b)

    def _ram(self, a):
        return self.cgram[a & 63] if self.ac_cg else self.ddram[a & 127]

    def _ram_write(self, b):
        if self.ac_cg:
            self.cgram[self.ac & 63] = b
        else:
            self.ddram[self.ac & 127] = b

    def _next_ac(self):
        self._ac_before = self.ac
        d = 1 if self.inc else -1
        self.ac = (self.ac + d) & (63 if self.ac_cg else 127)
        self._pending_ac = self.ac

    def _instr(self, t, b):
        ns = T_EXEC_NS
        if b == 0x01:                    # clear display
            self.ddram[:] = bytes([0x20]) * 128
            self.ac, self.ac_cg, self.inc = 0, False, True
            ns = T_CLEAR_NS
        elif b & 0xFE == 0x02:           # return home
            self.ac, self.ac_cg = 0, False
            ns = T_CLEAR_NS
        elif b & 0xFC == 0x04:           # entry mode set
            self.inc, self.shift = bool(b & 2), bool(b & 1)
        elif b & 0xF8 == 0x08:           # display on/off
            self.display, self.cursor, self.blink = bool(b & 4), bool(b & 2), bool(b & 1)
        elif b & 0xF0 == 0x10:           # cursor or display shift
            pass
        elif b & 0xE0 == 0x20:           # function set
            if self.dl8 and b & 0x10 and self.fs_count < 2:   # Figure 24's waits
                ns = (T_INIT1_NS, T_INIT2_NS)[self.fs_count]
                if self.fs_count == 0 and self.t_power is not None and t - self.t_power < T_POWER_NS:
                    self._err(t, f"first function set {t - self.t_power:.0f} ns after power on")
            if b & 0x10:
                self.fs_count += 1
            self.dl8 = bool(b & 0x10)
            self.lines2, self.font10 = bool(b & 8), bool(b & 4)
            self.pending_hi = None
            self.read_low = False
        elif b & 0xC0 == 0x40:           # set CGRAM address
            self.ac, self.ac_cg = b & 63, True
            self.dr = self._ram(self.ac)
        elif b & 0x80:                   # set DDRAM address
            self.ac, self.ac_cg = b & 127, False
            self.dr = self._ram(self.ac)
        self._start(t, ns)

    # ---------------------------------------------------------------- outputs
    def next_change(self, t):
        """The next time drive() may return something new, or None."""
        c = [x for x in (self.out_from, self.out_to, self.ac_due[0] if self.ac_due else None)
             if x is not None and x > t]
        return min(c) if c else None

    def drive(self, t):
        """(value, oe) on DB7..4 at t_ns. Between E rise and tDDR the LCD
        drives, but the value is not yet valid: complement it, the worst case."""
        self._settle(t)
        if self.out_on and self.out_to is not None and t >= self.out_to:
            self.out_on = False
        if not self.out_on:
            return 0, 0
        nib = (self.out_byte >> 4) & 0xF
        return (nib if t >= self.out_from else ~nib & 0xF), 0xF

    # ---------------------------------------------------------------- views
    def line(self, addr, n):
        return bytes(self.ddram[addr:addr + n])
