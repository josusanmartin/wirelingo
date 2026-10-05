"""sigrok's CAN decoder as an independent oracle for the CAN firmware, on the
cycle model's pins (tools/pe/sigrok.py ModelRecorder): test_can.py's tests run
unchanged while the pins are recorded, then sigrok decodes the bus (ui0, the
wired-AND of every node's TXD).

What sigrok checks here: SOF/arbitration/control/data fields, bit stuffing,
the CRC/ACK delimiters, the ACK slot (the receiving firmware's dominant ACK)
and EOF. libsigrokdecode 0.5.3 does not verify the CRC itself (its
is_valid_crc() is a TODO), so the CRC-15 field sigrok read off the wire is
compared with the CRC of the fields sigrok decoded.

One rule sigrok enforces that our test vectors break on purpose: Bosch CAN 2.0
forbids identifiers whose 7 most significant bits are all recessive (0x7F0 to
0x7FF); sigrok warns "Identifier bits 10..4 must not be all recessive" and
decodes the frame anyway. test_can.py uses 0x7F0 and 0x7FF as all-ones
corner cases; the firmware handles them like any identifier, so that one
warning is expected for those frames and no other.

Skipped without sigrok-cli (tools/pe/sigrok.py INSTALL_HINT).
"""
import random

import pytest

from pe import sigrok

import test_can as C

pytestmark = pytest.mark.skipif(sigrok.cli() is None, reason=sigrok.INSTALL_HINT)

PERIOD_NS = 1000 / 48          # test_can's TPB = 96 clocks per bit: 500 kbit/s at 48 MHz
ID_RULE = "Identifier bits 10..4 must not be all recessive"


def recorded(monkeypatch, setup_name):
    """A ModelRecorder that starts capturing once test_can's setup function
    has returned (program loaded, idle levels applied): the bus idles
    recessive from the first sample, as sigrok's decoder needs."""
    rec = sigrok.ModelRecorder(PERIOD_NS)
    setup = getattr(C, setup_name)

    def setup_then_capture():
        mh = setup()
        rec.restart()
        return mh
    monkeypatch.setattr(C, setup_name, setup_then_capture)
    return rec


def crc_matches(f):
    """sigrok's CRC-15 field == CRC-15 of sigrok's own id/dlc/data."""
    want = C.frame_bits(f["id"], f["data"], f["rtr"])[-15:]
    return f["crc"] == int("".join(map(str, want)), 2)


def check_frame(f, ident, data, acked=True):
    assert (f["id"], f["dlc"], f["data"], f["rtr"]) == (ident, len(data), data, False), f
    assert f["eof"] and f["ack"] is acked and f["crc_ok"] is not False and crc_matches(f), f
    assert f["warnings"] == ([ID_RULE] if ident & 0x7F0 == 0x7F0 else []), f


def test_sigrok_can_loopback(monkeypatch):
    """can_tx (core 0) -> can_rx (core 1) on one wired-AND bus: sigrok sees
    each frame intact, with a correct CRC and core 1's ACK."""
    with recorded(monkeypatch, "loop_setup") as rec:
        C.test_can_loopback_tx_to_rx()
    frames, _ = sigrok.can(rec.trace, "ui0", 500_000)
    want = [(0x123, bytes([1, 2, 3, 4])), (0x7F0, bytes([0xFF] * 8)), (0x001, b"")]
    assert len(frames) == len(want), frames
    for f, (ident, data) in zip(frames, want):
        check_frame(f, ident, data)


def test_sigrok_can_rx(monkeypatch):
    """can_rx against test_can's transmitter model (nominal bit rate): sigrok
    agrees on the five frames and sees can_rx's ACK; the deliberately
    corrupted sixth frame has a CRC that doesn't match its fields and no ACK."""
    with recorded(monkeypatch, "setup") as rec:
        C.test_can_rx_frames(0)
    frames, _ = sigrok.can(rec.trace, "ui0", 500_000)
    assert len(frames) == 6, frames
    rng = random.Random(0)                     # test_can_rx_frames(ppm=0)'s random payload
    want = [(0x123, bytes([0xDE, 0xAD, 0xBE, 0xEF])), (0x7FF, bytes(8)), (0x000, b""),
            (0x2AA, bytes([0xFF] * 8)), (0x155, bytes(rng.getrandbits(8) for _ in range(3)))]
    for f, (ident, data) in zip(frames, want):
        check_frame(f, ident, data)
    bad = frames[5]
    assert bad["id"] == 0x321 and bad["crc"] is not None and not crc_matches(bad) and bad["ack"] is False, bad
