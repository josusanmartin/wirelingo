"""PMBus reads on firmware/smbus_host.pasm (docs/bench-firmware.md): the host
builds the SMBus transactions, the firmware checks the PEC on the wire, this
library decodes the value and refuses one whose PEC was wrong.

    words = read_word_words(0x40, READ_TEMPERATURE_1)   # smbus_host TX words
    ... run them, smbus_records() the RX words ...
    linear11(parse_word(record))                        # Fraction, degrees C

Formats, from the public descriptions of PMBus Part II (section 7 and 8; the
specification itself was not read): LINEAR11 is a 16-bit word, the high 5 bits a
two's complement exponent N and the low 11 bits a two's complement mantissa
Y, value Y * 2^N. READ_VOUT is unsigned LINEAR16, with the exponent in the low
5 bits of VOUT_MODE when VOUT_MODE's top 3 bits are 000. Command codes as in
Linux's drivers/hwmon/pmbus/pmbus.h. Words are low byte first, as SMBus reads
them.

PmbusDevice is the pins-only device: pe.postfreeze3.SmbusDevice, which sees
only SCL and SDA and computes its own PEC, plus the ways to get that PEC
wrong and the data bits to flip on demand.
"""
from fractions import Fraction

from . import postfreeze3 as p3

VOUT_MODE, READ_VIN, READ_IOUT, READ_VOUT, READ_TEMPERATURE_1 = 0x20, 0x88, 0x8C, 0x8B, 0x8D


class PmbusError(Exception):
    """The transaction failed; `record` is smbus_host's record."""

    def __init__(self, record):
        super().__init__(record["result"])
        self.record = record


class BadPec(PmbusError):
    """The PEC on the wire did not check: no value is returned."""


def linear11(word):
    """The value of a LINEAR11 word, exactly."""
    if not 0 <= word < 65536:
        raise ValueError("LINEAR11 is 16 bits")
    n, y = word >> 11, word & 0x7FF
    n = n - 32 if n & 0x10 else n
    y = y - 2048 if y & 0x400 else y
    return Fraction(y) * Fraction(2) ** n


def to_linear11(value):
    """The LINEAR11 word nearest to value with the smallest exponent that
    fits (most mantissa bits). ValueError outside what the format holds."""
    value = Fraction(value)
    for n in range(-16, 16):
        y = round(value / Fraction(2) ** n)
        if -1024 <= y <= 1023:
            return (n & 31) << 11 | (y & 0x7FF)
    raise ValueError(f"{value} is outside LINEAR11")


def linear16(word, vout_mode):
    """READ_VOUT's value in volts with VOUT_MODE's exponent, exactly."""
    if vout_mode >> 5:
        raise ValueError(f"VOUT_MODE {vout_mode:#x}: not linear mode")
    n = vout_mode & 31
    n = n - 32 if n & 0x10 else n
    return Fraction(word) * Fraction(2) ** n


def read_word_words(addr, command):
    """smbus_host TX words: Read Word with PEC."""
    return p3.smbus_words(addr, [command], 2, pec=True)


def read_byte_words(addr, command):
    """smbus_host TX words: Read Byte with PEC."""
    return p3.smbus_words(addr, [command], 1, pec=True)


def _checked(record, n):
    if record["result"] == "pec_bad":
        raise BadPec(record)
    if record["result"] != "pec_ok":
        raise PmbusError(record)
    data = record["read"]
    if len(data) != n + 1:
        raise PmbusError(record)
    return data[:n]


def parse_word(record):
    """A Read Word's record -> the 16-bit word. BadPec or PmbusError rather
    than a value that did not arrive intact."""
    low, high = _checked(record, 2)
    return high << 8 | low


def parse_byte(record):
    return _checked(record, 1)[0]


class PmbusDevice(p3.SmbusDevice):
    """words: {command: 16-bit word}, bytes_: {command: byte}, with PEC.
    flip: bit index into the read's data + PEC bytes, MSB of the first byte
    first, flipped on the wire (once). pec_data_only: compute the PEC over
    the data bytes alone, a real device bug (the address bytes belong in
    it). The read's bytes as sent are in .sent."""

    def __init__(self, addr, words=None, bytes_=None, flip=None, pec_data_only=False, **kw):
        regs = {c: bytes([w & 255, w >> 8]) for c, w in (words or {}).items()}
        regs.update({c: bytes([b]) for c, b in (bytes_ or {}).items()})
        super().__init__(addr, regs, **kw)
        self.flip, self.pec_data_only = flip, pec_data_only
        self.sent = []

    def _next_read_byte(self):
        tr = self.tr
        if tr["out"] and not tr.get("mangled"):
            tr["mangled"] = True
            out = tr["out"]
            if self.pec_data_only:
                out[-1] = p3.smbus_pec(bytes(out[:-1]))
            if self.flip is not None:
                byte, bit = self.flip // 8, 7 - self.flip % 8
                out[byte] ^= 1 << bit
                self.flip = None
        b = super()._next_read_byte()
        self.sent.append(b)
        return b
