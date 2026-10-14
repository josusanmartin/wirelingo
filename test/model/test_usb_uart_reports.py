"""pe.usb_uart_reports, the PC-side reader of the UART-over-USB reports, without
pyusb: a stand-in device answers ctrl_transfer() with the cycle model's own
control transfers (pe.usbhost.ControlHost at address 0, two-bit packet gaps)
to the overlap4 and envelope3 pairs, cold-loaded by their helpers, with an
ideal READY-gated 115200-baud 8N1 sender on RX (ui[0]). STALL comes back as a
pyusb-like error (errno EPIPE, libusb PIPE). Model evidence only.
"""
import array
import errno
import sys

import pytest

from pe import sim, usb_uart_envelope3, usb_uart_overlap4, usb_uart_reports as rep
from pe.modelhost import ModelHost
from pe.usbhost import ControlHost, Stall

CLOCK = 48_000_000


class _Host(ModelHost):
    def __init__(self):
        self.chip = sim.Chip(ncores=2, nbanks=2, bank_words=256, fifo_depth=8)
        self.ui, self.uio, self.ext_oe, self.pull_up = 1, 0, 0, 2


class PairRig:
    """One pair on the model; the sender starts a frame one clock after each
    fresh READY rise (never before its previous STOP ends, never while READY
    is low), edges at round(k * T)."""

    def __init__(self, helper, data):
        self.mh = mh = _Host()
        c = mh.chip
        T = CLOCK / 115200
        queue = list(data)
        st = dict(ready=0, grant=None, used=None, frame=None, prev_end=0)
        original = c.step

        def step(ui_in=0, uio_in=0, **kw):
            cycle, ready = c.cycle, c.outputs()[0] & 1
            if ready and not st["ready"]:
                st["grant"] = cycle
            st["ready"] = ready
            f = st["frame"]
            if f is not None and cycle >= f["end"]:
                st["prev_end"], st["frame"] = f["end"], None
            if (st["frame"] is None and queue and ready and st["grant"] not in (None, st["used"])
                    and cycle >= max(st["grant"] + 1, st["prev_end"])):
                st["frame"] = dict(byte=queue.pop(0), start=cycle, end=cycle + round(10 * T))
                st["used"] = st["grant"]
            level, f = 1, st["frame"]
            if f is not None:
                t, k = cycle - f["start"], 0
                while k < 9 and round((k + 1) * T) <= t:
                    k += 1
                level = 0 if k == 0 else 1 if k == 9 else (f["byte"] >> (k - 1)) & 1
            return original((ui_in & ~1) | level, uio_in, **kw)
        c.step = step
        self.queue = queue
        mh.apply(helper.load_writes())
        self.host = ControlHost(mh, bit=32, gap_bits=2, retries=0)


class FakeUsbError(Exception):
    """What pyusb raises: usb.core.USBError carries errno and backend_error_code."""

    def __init__(self, err, backend):
        super().__init__(err)
        self.errno, self.backend_error_code = err, backend


class ModelDevice:
    """pyusb's ctrl_transfer() on the model; sleep() and clock() are model time."""

    def __init__(self, rig):
        self.rig, self.raw, self.stalls = rig, [], 0

    def ctrl_transfer(self, bmRequestType, bRequest, wValue=0, wIndex=0, data_or_wLength=None, timeout=None):
        n = data_or_wLength
        setup = bytes([bmRequestType, bRequest, wValue & 255, wValue >> 8, wIndex & 255, wIndex >> 8,
                       n & 255, n >> 8])
        try:
            data = self.rig.host.control_read(0, setup)
        except Stall:
            self.stalls += 1
            raise FakeUsbError(errno.EPIPE, rep.LIBUSB_ERROR_PIPE)
        self.raw.append(bytes(data))
        return array.array("B", data)

    def sleep(self, s):
        self.rig.mh.step(round(s * CLOCK))

    def clock(self):
        return self.rig.mh.chip.cycle / CLOCK


def model_read(dev, mode, n):
    return rep.read_bytes(dev, mode, n, poll_s=200e-6, timeout_s=0.05, sleep=dev.sleep, clock=dev.clock)


def test_setup_packets_match_the_loaders():
    assert rep.setup_packet("overlap4") == usb_uart_overlap4.vendor_request()
    assert rep.setup_packet("envelope3") == usb_uart_envelope3.vendor_request()
    with pytest.raises(ValueError):
        rep.setup_packet("batch4")


def test_decoders():
    """The documented report bytes (docs/usb-uart-*.md) and what is refused."""
    for hexa in ("00000000", "39d3815e", "12345678", "9abcdef0"):
        assert rep.decode_overlap4(bytes.fromhex(hexa)) == bytes.fromhex(hexa)
    for hexa, payload in (("00000001", "00"), ("00a50002", "00a5"), ("39d38103", "39d381"),
                          ("12340002", "1234")):
        assert rep.decode_envelope3(array.array("B", bytes.fromhex(hexa))) == bytes.fromhex(payload)
    for bad in (b"", b"\x01\x02\x03", bytes(5)):
        with pytest.raises(ValueError):
            rep.decode_overlap4(bad)
    for bad in ("00000000", "00000004", "00ff0001", "0000ff02", "000000", "0000000101"):
        with pytest.raises(ValueError):
            rep.decode_envelope3(bytes.fromhex(bad))


def test_overlap4_reports_from_the_model():
    """STALL before any byte, then two whole reports in arrival order."""
    data = bytes.fromhex("39d3815e12345678")
    rig = PairRig(usb_uart_overlap4, data)
    dev = ModelDevice(rig)
    assert rep.read_report(dev, "overlap4") is None and dev.stalls == 1
    assert model_read(dev, "overlap4", 8) == data
    assert dev.raw == [data[:4], data[4:]]


def test_envelope3_reports_from_the_model():
    """Three bytes publish at once; the last two (a zero byte first) after the
    idle timer: the documented reports 39d38103 and 00a50002."""
    rig = PairRig(usb_uart_envelope3, bytes.fromhex("39d38100a5"))
    dev = ModelDevice(rig)
    assert model_read(dev, "envelope3", 5) == bytes.fromhex("39d38100a5")
    assert dev.raw == [bytes.fromhex("39d38103"), bytes.fromhex("00a50002")]
    assert not rig.queue


def test_errors_other_than_stall_propagate_and_timeout_reports_progress():
    class Dev:
        def __init__(self, err):
            self.err, self.calls = err, 0

        def ctrl_transfer(self, *args):
            self.calls += 1
            raise FakeUsbError(self.err, -7)          # LIBUSB_ERROR_TIMEOUT
    with pytest.raises(FakeUsbError):
        rep.read_report(Dev(errno.ETIMEDOUT), "overlap4")
    t = [0.0]

    def sleep(s):
        t[0] += s
    stalling = Dev(errno.EPIPE)
    with pytest.raises(TimeoutError) as e:
        rep.read_bytes(stalling, "envelope3", 1, poll_s=0.01, timeout_s=0.1, sleep=sleep, clock=lambda: t[0])
    assert e.value.args[1] == b"" and stalling.calls > 5


def test_pyusb_is_optional(monkeypatch):
    """The module imports and decodes without pyusb; only open_device needs it."""
    monkeypatch.setitem(sys.modules, "usb", None)
    monkeypatch.setitem(sys.modules, "usb.core", None)
    import importlib
    fresh = importlib.reload(rep)
    assert fresh.decode_overlap4(b"abcd") == b"abcd"
    with pytest.raises(RuntimeError, match="pip install pyusb"):
        fresh.open_device()
