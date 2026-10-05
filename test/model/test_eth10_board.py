"""Finite board capture: independent wire records, FCS, bounds and failure cleanup."""
import binascii
import pathlib
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "tools/micropython"))
import eth10_rx
import protoemu


def frame(length):
    body = bytes((i * 37 + 9) & 255 for i in range(length - 4))
    return body + binascii.crc32(body).to_bytes(4, "little")


def records(data, lost=0):
    wire = bytes([0x55] * 7 + [0xD5]) + data
    bits = [(byte >> k) & 1 for byte in wire for k in range(8)][lost:]
    full, rem = divmod(len(bits), 24)
    words = [sum(b << (8 + k) for k, b in enumerate(bits[i*24:i*24+24])) for i in range(full)]
    words.append(sum(b << (32-rem+k) for k, b in enumerate(bits[24*full:])))
    return words + [(len(bits) << 16) | 0x80]


@pytest.mark.parametrize("size,lost", [(64, 0), (64, 1), (128, 7), (1518, 16)])
def test_wire_records_and_fcs(size, lost):
    fr = frame(size)
    collector = eth10_rx.Records()
    result = None
    for word in records(fr, lost):
        result = collector.feed(word)
    assert eth10_rx.decode(result) == fr
    assert not collector.malformed


@pytest.mark.parametrize("fault", ["crc", "prefix", "runt", "oversize", "unaligned", "padding", "count"])
def test_reject_corruption(fault):
    fr = frame(64)
    words = records(fr)
    if fault == "crc":
        words = records(fr[:-1] + bytes([fr[-1] ^ 1]))
    elif fault == "prefix":
        words[0] ^= 1 << 12
    elif fault == "runt":
        words = records(frame(63))
    elif fault == "oversize":
        words = records(frame(1519))
    elif fault == "unaligned":
        words = records(fr, 1)
        words[-1] += 1 << 16
    elif fault == "padding":
        words[-2] |= 1 << 8  # Mandatory empty final word in this exact-multiple case.
    else:
        words[-1] += 24 << 16
    collector = eth10_rx.Records()
    result = None
    for word in words:
        result = collector.feed(word)
    if result is not None:
        with pytest.raises(ValueError):
            eth10_rx.decode(result)


def test_missing_trailer_bounded_and_resynchronizes():
    collector = eth10_rx.Records()
    for _ in range(eth10_rx.MAX_WORDS + 100):
        assert collector.feed(0) is None
        assert len(collector.words) <= eth10_rx.MAX_WORDS
    assert collector.feed(0x80) is None
    assert collector.malformed == 1
    # A short link pulse is not an Ethernet frame.
    assert collector.feed(1 << 31) is None
    assert collector.feed((1 << 16) | 0x80) is None
    assert collector.short == 1
    result = None
    for word in records(frame(64)):
        result = collector.feed(word)
    assert eth10_rx.decode(result) == frame(64)


class Port:
    """Board API fake for cleanup/clock/error policy; not a hardware timing model."""
    def __init__(self, words=(), fault=None):
        self.tt = SimpleNamespace(auto_clocking_freq=50_000_000)
        self.words = list(words)
        self.calls = []
        self.fault = fault
        self.popped = False
        self.ticks = (1 << 30) - 5

    def stop(self, mask):
        self.calls.append(("stop", mask))
        if self.fault == "cleanup" and self.popped:
            raise OSError("cleanup failed")

    def start(self, mask): self.calls.append(("start", mask))
    def clear_fifos(self, mask): self.calls.append(("clear", mask))
    def load(self, **image): self.calls.append(("load", image))
    def write(self, address, value): self.calls.append(("write", address, value))

    def read(self, address):
        assert address == protoemu.A_LINESTAT
        return 1 << 19 if self.fault == "overflow" and self.popped else 0

    def status(self, core):
        assert core == 0
        return protoemu.ST_FAULT if self.fault == "core" else 0

    def pop_all(self, core):
        assert core == 0
        self.popped = True
        if self.fault == "cleanup":
            raise OSError("capture read failed")
        if self.fault == "clock":
            self.tt.auto_clocking_freq = 48_000_000
        result, self.words = self.words[:8], self.words[8:]
        return result

    def now_us(self):
        self.ticks = (self.ticks + 1) & ((1 << 30) - 1)
        return self.ticks

    @staticmethod
    def elapsed_us(now, before):
        return (now - before) & ((1 << 30) - 1)


def test_bounded_capture_stops_before_decoding_and_preserves_other_core(monkeypatch):
    port = Port(records(frame(64)) + records(frame(1518)))
    original = eth10_rx.decode

    def decode_after_stop(record):
        assert port.calls[-1] == ("stop", 1)
        return original(record)

    monkeypatch.setattr(eth10_rx, "decode", decode_after_stop)
    capture = eth10_rx.Capture(port)
    assert capture.capture(max_frames=2) == [frame(64), frame(1518)]
    assert len(capture.raw_frames) == 2
    assert ("write", protoemu.A_LINEDIV, 5 << 16) in port.calls
    assert ("write", protoemu.A_LINEPINS, 16 << 8) in port.calls
    assert all(call[1] == 1 for call in port.calls if call[0] in ("stop", "start", "clear"))


@pytest.mark.parametrize("fault,match", [("overflow", "overflow"), ("core", "faulted"),
                                         ("clock", "50-MHz"), ("timeout", "timed out"),
                                         ("cleanup", "capture read failed")])
def test_capture_failure_cleanup(fault, match):
    port = Port(records(frame(64)) if fault != "timeout" else (), fault)
    capture = eth10_rx.Capture(port)
    with pytest.raises((RuntimeError, OSError), match=match):
        capture.capture(timeout_us=100)
    assert port.calls[-1] == ("stop", 1)


def test_clock_and_parameters_rejected_before_writes():
    port = Port()
    port.tt.auto_clocking_freq = 48_000_000
    with pytest.raises(RuntimeError, match="50-MHz"):
        eth10_rx.Capture(port)
    assert not port.calls
    port.tt.auto_clocking_freq = 50_000_000
    capture = eth10_rx.Capture(port)
    for args in ({"timeout_us": 0}, {"timeout_us": 1_000_001}, {"max_frames": 0}, {"max_frames": 17}):
        with pytest.raises(ValueError):
            capture.capture(**args)
        assert not port.calls
