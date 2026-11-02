"""PMBus reads with PEC on firmware/smbus_host.pasm (pe.pmbus,
docs/bench-firmware.md), on the cycle model, tick for tick 50 MHz / CLKDIV 56.

The firmware under test is the existing smbus_host, unchanged; the PEC is
checked on the wire by its CRC tap. Judges: the pins-only device, which
computes its own PEC over what it saw; sigrok's i2c decoder, with the PEC
recomputed over its bytes; LINEAR11 against literal values and against an
independent integer computation over all 65536 words."""
import math
from fractions import Fraction

import pytest

from pe import pmbus, postfreeze as pf, postfreeze3 as p3, sigrok

import test_smbus as TS

ADDR = 0x40
WORDS = {pmbus.READ_VIN: 0xDB12, pmbus.READ_TEMPERATURE_1: 0xE32A, pmbus.READ_VOUT: 0x3333, pmbus.READ_IOUT: 0x0000}


def rig(**kw):
    dev = pmbus.PmbusDevice(ADDR, WORDS, {pmbus.VOUT_MODE: 0x17}, **kw)
    r = TS.Rig([dev])
    r.run(50e3)
    return r, dev


def read_word(r, command):
    return r.transact(pmbus.read_word_words(ADDR, command))


# ---------------------------------------------------------------- LINEAR11

@pytest.mark.parametrize("word,value", [
    (0x0000, Fraction(0)),                       # N = 0, Y = 0
    (0x0001, Fraction(1)),                       # the smallest positive mantissa
    (0x03FF, Fraction(1023)),                    # Y's largest, N = 0
    (0x0400, Fraction(-1024)),                   # Y's smallest, N = 0
    (0x07FF, Fraction(-1)),                      # Y = -1, N = 0
    (0x7BFF, Fraction(1023 * 2 ** 15)),          # the largest value of the format
    (0x7C00, Fraction(-1024 * 2 ** 15)),         # the smallest
    (0x7800, Fraction(0)),                       # zero with N = 15
    (0x8000, Fraction(0)),                       # zero with N = -16
    (0x8001, Fraction(1, 2 ** 16)),              # the smallest positive value
    (0x83FF, Fraction(1023, 2 ** 16)),           # Y's largest, N = -16
    (0x8400, Fraction(-1024, 2 ** 16)),          # Y's smallest, N = -16
    (0x87FF, Fraction(-1, 2 ** 16)),             # the negative nearest 0
    (0xF800, Fraction(0)),                       # N = -1
    (0xFBFF, Fraction(1023, 2)),                 # N = -1, Y largest
    (0xFC00, Fraction(-512)),                    # N = -1, Y smallest
    (0xFFFF, Fraction(-1, 2)),                   # all ones: N = -1, Y = -1
    (0x0800, Fraction(0)),                       # N = 1, Y = 0
    (0x0801, Fraction(2)),                       # N = 1
    (0xDB12, Fraction(786, 32)),                 # a vendor's published example: N = -5, Y = 786
])
def test_linear11_boundaries(word, value):
    assert pmbus.linear11(word) == value


def test_linear11_extremes_as_numbers():
    assert pmbus.linear11(0x7BFF) == 33521664
    assert pmbus.linear11(0x7C00) == -33554432
    assert pmbus.linear11(0xDB12) == Fraction("24.5625")
    assert float(pmbus.linear11(0x8001)) == math.ldexp(1, -16)


def test_linear11_every_word():
    """All 65536 words against a second computation (the sign by subtraction
    of the field's weight, the scaling by shifting integers), and every value
    survives to_linear11 back to the same value."""
    seen = 0
    for w in range(65536):
        y = (w & 0x3FF) - (w & 0x400)
        n = (w >> 11 & 15) - (w >> 11 & 16)
        v = pmbus.linear11(w)
        assert v * 2 ** 16 == y * 2 ** (n + 16)
        assert pmbus.linear11(pmbus.to_linear11(v)) == v
        seen += 1
    assert seen == 65536


def test_linear11_rejects():
    for w in (-1, 65536):
        with pytest.raises(ValueError):
            pmbus.linear11(w)
    for v in (33521664 + 32768, -33554432 - 32768):
        with pytest.raises(ValueError):
            pmbus.to_linear11(v)
    assert pmbus.to_linear11(24.5625) == 0xDB12


def test_linear16():
    """VOUT_MODE 0x17: linear, exponent -9: 0x3333 / 512 = 25.599..., 0x0200 = 1
    exactly; exponent 0x10 = -16 and 0x0F = +15 at the ends of the
    5-bit field; non-linear modes refused."""
    assert pmbus.linear16(0x0200, 0x17) == 1
    assert pmbus.linear16(0x3333, 0x17) == Fraction(0x3333, 512)
    assert pmbus.linear16(0xFFFF, 0x10) == Fraction(0xFFFF, 65536)
    assert pmbus.linear16(1, 0x0F) == 32768
    for mode in (0x20, 0x40, 0x60, 0xFF):
        with pytest.raises(ValueError):
            pmbus.linear16(1, mode)


# ---------------------------------------------------------------- through the firmware

def test_read_temperature_and_voltage_with_pec():
    """READ_TEMPERATURE_1, READ_VIN (LINEAR11) and VOUT_MODE + READ_VOUT
    (LINEAR16) through smbus_host: exact values; the device saw command code,
    repeated START, and the master's NACK on the PEC only."""
    r, dev = rig()
    t = pmbus.linear11(pmbus.parse_word(read_word(r, pmbus.READ_TEMPERATURE_1)))
    v = pmbus.linear11(pmbus.parse_word(read_word(r, pmbus.READ_VIN)))
    mode = pmbus.parse_byte(r.transact(pmbus.read_byte_words(ADDR, pmbus.VOUT_MODE)))
    vout = pmbus.linear16(pmbus.parse_word(read_word(r, pmbus.READ_VOUT)), mode)
    assert t == Fraction("50.625") and v == Fraction("24.5625") and mode == 0x17 and vout == Fraction(0x3333, 512)
    trs = dev.transactions
    assert [x["write"] for x in trs] == [bytes([c]) for c in (0x8D, 0x88, 0x20, 0x8B)]
    assert all(x["sr"] for x in trs)
    assert [x["acks"] for x in trs] == [[0, 0, 1], [0, 0, 1], [0, 1], [0, 0, 1]]


@pytest.mark.parametrize("word", [0x0000, 0x03FF, 0x0400, 0x7BFF, 0x7C00, 0x8000, 0x83FF, 0x8400, 0xFFFF])
def test_boundary_words_through_the_firmware(word):
    """The format's corners, over the wire with PEC."""
    dev = pmbus.PmbusDevice(ADDR, {pmbus.READ_TEMPERATURE_1: word})
    r = TS.Rig([dev])
    r.run(50e3)
    assert pmbus.parse_word(read_word(r, pmbus.READ_TEMPERATURE_1)) == word


@pytest.mark.parametrize("bit", range(24))
def test_bad_pec_every_single_bit(bit):
    """Each of the 24 bits of the two data bytes and the PEC, flipped on the
    wire: the firmware reports pec_bad, the library raises BadPec and returns
    no value. The next read is correct."""
    r, dev = rig(flip=bit)
    rec = read_word(r, pmbus.READ_TEMPERATURE_1)
    assert rec["result"] == "pec_bad"
    with pytest.raises(pmbus.BadPec):
        pmbus.parse_word(rec)
    assert dev.sent != [0x2A, 0xE3, p3.smbus_pec(bytes([ADDR << 1, 0x8D, ADDR << 1 | 1, 0x2A, 0xE3]))]
    assert pmbus.parse_word(read_word(r, pmbus.READ_TEMPERATURE_1)) == 0xE32A


def test_good_pec_is_what_we_expect():
    """The other half of the control: unflipped, the PEC byte on the wire is
    the literal CRC-8 of address, command, address, data, computed here by
    hand from the catalogue's definition."""
    r, dev = rig()
    assert read_word(r, pmbus.READ_TEMPERATURE_1)["result"] == "pec_ok"
    crc = 0
    for b in (ADDR << 1, 0x8D, ADDR << 1 | 1, 0x2A, 0xE3):
        crc ^= b
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else crc << 1 & 0xFF
    assert dev.sent == [0x2A, 0xE3, crc]


def test_pec_over_data_only_is_rejected():
    """A device that leaves the address bytes out of its PEC: rejected."""
    r, dev = rig(pec_data_only=True)
    with pytest.raises(pmbus.BadPec):
        pmbus.parse_word(read_word(r, pmbus.READ_TEMPERATURE_1))


def test_device_without_pec_is_rejected():
    """A device with no PEC: the byte after the data is 0xFF (the pull-up),
    which is not the PEC of this data: rejected."""
    dev = pmbus.PmbusDevice(ADDR, WORDS, pec=False)
    r = TS.Rig([dev])
    r.run(50e3)
    with pytest.raises(pmbus.BadPec):
        pmbus.parse_word(read_word(r, pmbus.READ_TEMPERATURE_1))


def test_absent_device_and_short_records():
    """No device: PmbusError, no value. A record of the wrong length: the
    same, even with a good PEC result."""
    r, dev = rig()
    rec = r.transact(pmbus.read_word_words(0x41, pmbus.READ_VIN))
    assert rec["result"] == "addr_nack"
    with pytest.raises(pmbus.PmbusError):
        pmbus.parse_word(rec)
    with pytest.raises(pmbus.PmbusError):
        pmbus.parse_word({"read": b"\x01\x02", "result": "pec_ok", "unsent": 0})


@pytest.mark.skipif(sigrok.cli() is None, reason=sigrok.INSTALL_HINT)
def test_sigrok_i2c_and_pec():
    """sigrok's i2c decoder: S, 0x40 W, the command, Sr, 0x40 R, data low
    byte, high byte, PEC with NACK, P; the PEC recomputed over sigrok's bytes.
    The bad-PEC read's bytes fail that recomputation."""
    r, dev = rig()
    read_word(r, pmbus.READ_TEMPERATURE_1)
    dev.flip = 3
    read_word(r, pmbus.READ_TEMPERATURE_1)
    r.run(50e3)
    tr = sigrok.Trace()
    pf.to_trace(tr, pf.bit_edges(r.log, 1), "scl")
    pf.to_trace(tr, pf.bit_edges(r.log, 0), "sda")
    tr.end(round(r.now() * 1000))
    toks = [k for _, k in sigrok.i2c(tr, "scl", "sda", res_ps=10_000)]
    print(toks)
    assert toks[:15] == ["S", ("AW", ADDR), "ACK", ("DW", 0x8D), "ACK", "Sr", ("AR", ADDR), "ACK", ("DR", 0x2A), "ACK",
                         ("DR", 0xE3), "ACK", ("DR", dev.sent[2]), "NACK", "P"]
    good = [ADDR << 1, 0x8D, ADDR << 1 | 1, 0x2A, 0xE3]
    assert p3.smbus_pec(bytes(good)) == dev.sent[2]
    bad = [k[1] for k in toks[15:] if isinstance(k, tuple) and k[0] == "DR"]
    assert len(bad) == 3 and p3.smbus_pec(bytes(good[:3] + bad[:2])) != bad[2]
