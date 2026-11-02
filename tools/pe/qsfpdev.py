"""A QSFP-style management interface stand-in: an SFF-8636-style memory map on
the existing firmware/i2c_device.pasm (docs/bench-firmware.md).

A stand-in, not a compliant module. It answers an I2C master at 0x50 with the
fields below so a host's module-management path can be brought up without a
module. It has no module pins (ModSelL, ResetL, LPMode, IntL, ModPrsL), no
laser, no measurements and no write cycle (tWR, the busy NACK), and most ID
bytes are zero where SFF-8636 forbids that.

Numbers and layout from SFF-8636 Rev 2.9 (2017), the published text:
device address 1010000x (5.2.3.1); the map of Table 6-1, Table 6-16, Table
6-30; temperature signed 1/256 C, supply 100 uV (6.2.4); page select at byte
127, an unsupported value reverts to 00h (6.1, 6.2.11); the address counter
rolls over inside its 128-byte page (5.3.1); a write takes effect at STOP and a
repeated START in its place aborts it (5.3.2); clock stretching at most 500 us
(Table 5-1). Identifier codes 0Dh and 11h are SFF-8024's, as Linux's
include/linux/sfp.h has them (SFF-8024 itself was not read).

Modelled, page 00h lower: byte 0 identifier, 1 revision compliance (08h), 2
status (Data_Not_Ready, IntL bit, Flat_mem 0), 22-23 temperature, 26-27 supply
voltage, 86 Tx disable (bits 3-0, read/write), 127 page select (read/write).
Upper page 00h: 128 identifier, 148-163 vendor name, 165-167 OUI, 168-183
part number, 184-185 revision, 191 CC_BASE, 196-211 serial number, 212-219
date code, 223 CC_EXT. Upper page 03h: 128-135 temperature and 144-151 supply
thresholds. Every other byte reads 0 and ignores writes; pages other than 00h
and 03h are unsupported.

    dev = QsfpDevice(vendor="ACME", part="QSFP-X", serial="S1")
    writes = dev.setup_writes(core=1, prog=..., sda=0, scl=1)
    for word in drained RX words:
        for tx in dev.handle(word):
            push(tx)
    dev.set_temperature(47.25)
"""
from . import i2cdev

ADDR = 0x50                     # 1010000x
ID_QSFP_PLUS, ID_QSFP28 = 0x0D, 0x11
REV_COMPLIANCE = 0x08           # "SFF-8636 Rev 2.8 or later" (Table 6-3)
PAGES = (0x00, 0x03)
STATUS_DATA_NOT_READY, STATUS_INTL_HIGH = 0x01, 0x02
T_CLOCK_HOLD_US = 500.0         # SFF-8636 Table 5-1: longest SCL hold
# byte -> what: the fields above, for the tests and the document
LOWER_MODELLED = (0, 1, 2, 22, 23, 26, 27, 86, 127)
UPPER00_MODELLED = (128, *range(148, 164), *range(165, 186), 191, *range(196, 220), 223)
UPPER03_MODELLED = (*range(128, 136), *range(144, 152))
THRESHOLDS = {128: 75.0, 130: -5.0, 132: 70.0, 134: 0.0}         # high/low alarm, high/low warning, C
VCC_THRESHOLDS = {144: 3.63, 146: 2.97, 148: 3.465, 150: 3.135}  # V
TX_DISABLE, PAGE_SELECT = 86, 127


def encode_temperature(celsius):
    raw = round(celsius * 256)
    if not -32768 <= raw <= 32767:
        raise ValueError("temperature is a signed 1/256 C field: -128 .. 127.996 C")
    return (raw & 0xFFFF).to_bytes(2, "big")


def decode_temperature(msb, lsb):
    raw = msb << 8 | lsb
    return (raw - 65536 if raw & 0x8000 else raw) / 256


def encode_vcc(volts):
    raw = round(volts * 10000)
    if not 0 <= raw <= 65535:
        raise ValueError("supply voltage is an unsigned 100 uV field: 0 .. 6.5535 V")
    return raw.to_bytes(2, "big")


def decode_vcc(msb, lsb):
    return (msb << 8 | lsb) / 10000


def check_code(data):
    """Low 8 bits of the sum (6.3.22, 6.3.29)."""
    return sum(data) & 0xFF


def _ascii(s, n):
    b = s.encode("ascii")
    if len(b) > n:
        raise ValueError(f"at most {n} characters: {s!r}")
    return b.ljust(n, b" ")


def parse_upper00(page):
    """The 128 bytes of upper page 00h (bytes 128-255) as a reader sees them.
    Raises ValueError on a wrong check code."""
    p = bytes(page)
    if len(p) != 128:
        raise ValueError("upper page 00h is 128 bytes")
    if check_code(p[:63]) != p[63] or check_code(p[64:95]) != p[95]:
        raise ValueError("bad check code")
    text = lambda a, n: p[a - 128:a - 128 + n].decode("ascii").rstrip()
    return {"identifier": p[0], "vendor": text(148, 16), "oui": p[165 - 128:168 - 128], "part": text(168, 16),
            "rev": text(184, 2), "serial": text(196, 16), "date": text(212, 8)}


class QsfpDevice(i2cdev.Device):
    """The memory map behind i2c_device's events. handle() and setup_writes()
    as i2cdev.Device; ptr is the SFF-8636 address counter. `ignored` lists
    (byte, value) writes that hit nothing writable."""

    def __init__(self, vendor="WIRELINGO", part="BENCH-STANDIN", rev="A0", serial="000001", date="26110200",
                 oui=b"\x00\x00\x00", identifier=ID_QSFP28, temperature=25.0, vcc=3.3, addr=ADDR):
        super().__init__(addr, fill=0)
        self.lower = bytearray(128)
        self.upper = {0: bytearray(128), 3: bytearray(128)}
        self.lower[0] = identifier
        self.lower[1] = REV_COMPLIANCE
        self.lower[2] = STATUS_INTL_HIGH
        self.set_temperature(temperature)
        self.set_vcc(vcc)
        u = self.upper[0]
        put = lambda a, b: u.__setitem__(slice(a - 128, a - 128 + len(b)), b)
        u[0] = identifier
        put(148, _ascii(vendor, 16))
        put(165, bytes(oui))
        put(168, _ascii(part, 16))
        put(184, _ascii(rev, 2))
        put(196, _ascii(serial, 16))
        put(212, _ascii(date, 8))
        u[191 - 128] = check_code(u[:63])
        u[223 - 128] = check_code(u[64:95])
        p3 = self.upper[3]
        for a, c in THRESHOLDS.items():
            p3[a - 128:a - 126] = encode_temperature(c)
        for a, v in VCC_THRESHOLDS.items():
            p3[a - 128:a - 126] = encode_vcc(v)
        self.pending, self.ignored = [], []
        self.view = bytes(256)

    @property
    def page(self):
        return self.lower[PAGE_SELECT]

    def set_temperature(self, celsius):
        self.lower[22:24] = encode_temperature(celsius)

    def set_vcc(self, volts):
        self.lower[26:28] = encode_vcc(volts)

    def set_data_not_ready(self, flag):
        self.lower[2] = (self.lower[2] & ~STATUS_DATA_NOT_READY) | (STATUS_DATA_NOT_READY if flag else 0)

    def image(self):
        return bytes(self.lower) + bytes(self.upper[self.page])

    @staticmethod
    def _next(a):
        """Roll-over inside the 128-byte page (5.3.1)."""
        return (a & 0x80) | ((a + 1) & 0x7F)

    def _store(self, a, b):
        if a == PAGE_SELECT:
            self.lower[a] = b if b in PAGES else 0
        elif a == TX_DISABLE:
            self.lower[a] = b & 0x0F
        else:
            self.ignored.append((a, b))

    def handle(self, word):
        kind, b = (word >> 8) & 0xF, word & 0xFF
        if word == 0:
            return []
        if kind == i2cdev.EV_ADDR:
            self.pending = []            # a repeated START instead of a STOP: the write is aborted
            self.first = not (b & 1)
            self.log.append(("R" if b & 1 else "W", []))
            self.view = self.image()     # a read is one coherent view (6.2.4)
        elif kind == i2cdev.EV_WRITE:
            if self.first:
                self.ptr, self.first = b, False
            else:
                self.pending.append((self.ptr, b))
                self.ptr = self._next(self.ptr)
            self.log[-1][1].append(b)
        elif kind == i2cdev.EV_READ:
            v = self.view[self.ptr]
            self.ptr = self._next(self.ptr)
            self.log[-1][1].append(v)
            return [v]
        elif kind == i2cdev.EV_STOP:
            for a, v in self.pending:
                self._store(a, v)
            self.pending = []
        elif kind == i2cdev.EV_NACK:
            pass
        else:
            raise ValueError(f"not an i2c_device event: {word:#x}")
        return []
