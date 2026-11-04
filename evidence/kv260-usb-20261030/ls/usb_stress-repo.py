#!/usr/bin/env python3
"""Live control transfers and bus resets against the attached USB demo device.

Run after usb_demo.py, with the device attached, as root:
    sudo python3 protoemu48/fpga/kv260/host/usb_stress.py [TRANSFERS] [RESETS]
Each transfer goes to the device through usbfs (Linux caches nothing here), so
a wrong byte, a timeout or a stall counts. Each reset is a real bus reset: the
kernel re-enumerates and the device is expected back with the same descriptor.
Reads only the kernel's USB interface; the chip's host port is not touched.
Exit status 0 = every transfer and reset good.
"""
import collections
import ctypes
import errno
import fcntl
import glob
import os
import sys
import time

USBDEVFS_CONTROL, USBDEVFS_RESET = 0xC0185500, 0x5514
DEVICE = bytes.fromhex("120110010000000809123456010000000001")
CONFIG = bytes.fromhex("0902120001010080320904000000ff000000")


class CtrlTransfer(ctypes.Structure):
    _fields_ = [("bRequestType", ctypes.c_uint8), ("bRequest", ctypes.c_uint8),
                ("wValue", ctypes.c_uint16), ("wIndex", ctypes.c_uint16),
                ("wLength", ctypes.c_uint16), ("timeout", ctypes.c_uint32),
                ("data", ctypes.c_void_p)]


def find():
    """The usbfs node of 1209:5634."""
    for d in glob.glob("/sys/bus/usb/devices/*"):
        try:
            with open(d + "/idVendor") as f, open(d + "/idProduct") as g:
                if (f.read().strip(), g.read().strip()) != ("1209", "5634"):
                    continue
            with open(d + "/busnum") as f, open(d + "/devnum") as g:
                return "/dev/bus/usb/%03d/%03d" % (int(f.read()), int(g.read()))
        except OSError:
            pass
    return None


def control(fd, request_type, request, value, index, length):
    buf = ctypes.create_string_buffer(max(length, 1))
    xfer = CtrlTransfer(request_type, request, value, index, length, 1000, ctypes.addressof(buf))
    n = fcntl.ioctl(fd, USBDEVFS_CONTROL, xfer)
    return buf.raw[:n]


def main():
    transfers = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
    resets = int(sys.argv[2]) if len(sys.argv) > 2 else 30
    path = find()
    if path is None:
        print("FAIL: 1209:5634 is not attached (run usb_demo.py first)")
        return 1
    fd = os.open(path, os.O_RDWR)
    for name, setup in (("GET_STATUS", (0x80, 0, 0, 0, 2)), ("GET_CONFIGURATION", (0x80, 8, 0, 0, 1))):
        print(name, control(fd, *setup).hex())
    # A low-speed device with no strings stalls these two: the expected answer.
    for name, setup in (("string 0", (0x80, 6, 0x0300, 0, 4)), ("device qualifier", (0x80, 6, 0x0600, 0, 10))):
        try:
            print(name, "answered", control(fd, *setup).hex())
        except OSError as e:
            print(name, errno.errorcode[e.errno])
    tests = (("device descriptor", (0x80, 6, 0x0100, 0, 18), DEVICE),
             ("configuration descriptor", (0x80, 6, 0x0200, 0, len(CONFIG)), CONFIG))
    counts = collections.Counter()
    t0 = time.monotonic()
    for i in range(transfers):
        name, setup, want = tests[i % 2]
        try:
            counts[name, "ok" if control(fd, *setup) == want else "WRONG BYTES"] += 1
        except OSError as e:
            counts[name, errno.errorcode[e.errno]] += 1
    dt = time.monotonic() - t0
    print(f"{transfers} transfers in {dt:.1f} s ({1e3 * dt / max(transfers, 1):.2f} ms each)")
    for (name, result), n in sorted(counts.items()):
        print(f"  {name}: {result} {n}")
    good = 0
    for i in range(resets):
        try:
            fcntl.ioctl(fd, USBDEVFS_RESET, 0)
            time.sleep(0.05)
            good += control(fd, 0x80, 6, 0x0100, 0, 18) == DEVICE
        except OSError as e:
            print(f"reset {i}: {errno.errorcode[e.errno]}")
            time.sleep(0.2)
    print(f"bus resets: {good} of {resets} good")
    os.close(fd)
    ok = good == resets and all(result == "ok" for _, result in counts)
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
