"""PC side of the UART-over-USB pairs: read their four-byte EP0 reports.

usb_uart_overlap4 (docs/usb-uart-overlap4.md) answers vendor/device IN
request 1 with four UART bytes; usb_uart_envelope3 (docs/usb-uart-envelope3.md)
answers request 2 with [b0, b1, b2, n], n = 1..3 payload bytes and zero
padding. Either answers STALL while no report is ready; the next request is a
fresh control transfer. The device is VID 0x1209, PID 0x5634.

The decoders and the polling loop are pure Python and take any object with
pyusb's `ctrl_transfer(bmRequestType, bRequest, wValue, wIndex, length,
timeout)`. pyusb itself is optional: only open_device() and the command line
import it (`pip install pyusb`, plus a libusb backend and, on Linux, access
to the device node).

    PYTHONPATH=tools python3 -m pe.usb_uart_reports overlap4 --bytes 64

Tested against the cycle model's USB packets only; no PC has read a report
from silicon. A report carries no sequence number: a DATA ACK the device
misses makes it send the same report again (not exactly-once delivery).
"""
import argparse
import errno
import sys
import time

VENDOR_ID, PRODUCT_ID = 0x1209, 0x5634
REQUEST_TYPE = 0xC0                 # device to host, vendor, device recipient
REPORT_LENGTH = 4
REQUESTS = {"overlap4": 1, "envelope3": 2}
LIBUSB_ERROR_PIPE = -9


def decode_overlap4(report):
    """Four UART bytes, in arrival order."""
    report = bytes(report)
    if len(report) != REPORT_LENGTH:
        raise ValueError("overlap4 report must be exactly 4 bytes, got %d" % len(report))
    return report


def decode_envelope3(report):
    """The first n of [b0, b1, b2, n]; n = 1..3 and the unused bytes are zero.
    A zero payload byte is data."""
    report = bytes(report)
    if len(report) != REPORT_LENGTH:
        raise ValueError("envelope3 report must be exactly 4 bytes, got %d" % len(report))
    n = report[3]
    if not 1 <= n <= 3:
        raise ValueError("envelope3 count must be 1..3, got %d" % n)
    if any(report[n:3]):
        raise ValueError("envelope3 padding must be zero: %s" % report.hex())
    return report[:n]


DECODERS = {"overlap4": decode_overlap4, "envelope3": decode_envelope3}


def _mode(mode):
    if mode not in REQUESTS:
        raise ValueError("mode must be overlap4 or envelope3")
    return mode


def setup_packet(mode):
    """The 8-byte SETUP of a report request (pe.usb_uart_*.vendor_request())."""
    return bytes([REQUEST_TYPE, REQUESTS[_mode(mode)], 0, 0, 0, 0, REPORT_LENGTH, 0])


def is_stall(exc):
    """True for pyusb's STALL error (libusb PIPE, errno EPIPE)."""
    return (getattr(exc, "backend_error_code", None) == LIBUSB_ERROR_PIPE
            or getattr(exc, "errno", None) == errno.EPIPE)


def read_report(dev, mode, timeout_ms=100):
    """One report's payload bytes, or None if the device STALLed (none ready)."""
    try:
        data = dev.ctrl_transfer(REQUEST_TYPE, REQUESTS[_mode(mode)], 0, 0, REPORT_LENGTH, timeout_ms)
    except Exception as exc:
        if is_stall(exc):
            return None
        raise
    return DECODERS[mode](data)


def read_bytes(dev, mode, n, *, poll_s=0.001, timeout_s=10.0, timeout_ms=100,
               sleep=time.sleep, clock=time.monotonic):
    """Poll until at least n bytes arrived; returns all of them (whole reports,
    so possibly more than n). TimeoutError with the bytes so far in .args[1]."""
    _mode(mode)
    out = bytearray()
    t0 = clock()
    while len(out) < n:
        report = read_report(dev, mode, timeout_ms)
        if report is not None:
            out += report
            continue
        if clock() - t0 > timeout_s:
            raise TimeoutError("%d of %d bytes after %.1f s" % (len(out), n, timeout_s), bytes(out))
        sleep(poll_s)
    return bytes(out)


def open_device(vid=VENDOR_ID, pid=PRODUCT_ID):
    """The first matching device, through pyusb (imported here only)."""
    try:
        import usb.core
    except ImportError as exc:
        raise RuntimeError("pyusb is needed to talk to the device: pip install pyusb") from exc
    dev = usb.core.find(idVendor=vid, idProduct=pid)
    if dev is None:
        raise RuntimeError("no USB device %04x:%04x found" % (vid, pid))
    return dev


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("mode", choices=sorted(REQUESTS))
    ap.add_argument("--bytes", type=int, default=0, help="stop after this many bytes (0: run until Ctrl-C)")
    ap.add_argument("--timeout", type=float, default=10.0, help="seconds without a report before giving up")
    ap.add_argument("--raw", action="store_true", help="write the bytes to stdout instead of hex lines")
    a = ap.parse_args(argv)
    dev = open_device()
    got = 0
    while not a.bytes or got < a.bytes:
        data = read_bytes(dev, a.mode, 1, timeout_s=a.timeout)
        got += len(data)
        if a.raw:
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()
        else:
            print(data.hex(" "), flush=True)


if __name__ == "__main__":
    main()
