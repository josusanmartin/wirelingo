#!/usr/bin/env python3
"""Live control transfers and bus resets against 1209:5634 through usbfs."""
import collections, ctypes, errno, fcntl, glob, os, sys, time

CTRL, RESET = 0xC0185500, 0x5514
DEVICE = bytes.fromhex("120110010000000809123456010000000001")

class Ctrl(ctypes.Structure):
    _fields_ = [("bRequestType", ctypes.c_uint8), ("bRequest", ctypes.c_uint8),
                ("wValue", ctypes.c_uint16), ("wIndex", ctypes.c_uint16),
                ("wLength", ctypes.c_uint16), ("timeout", ctypes.c_uint32),
                ("data", ctypes.c_void_p)]

def find():
    for d in glob.glob("/sys/bus/usb/devices/*"):
        try:
            if open(d + "/idVendor").read().strip() == "1209" and open(d + "/idProduct").read().strip() == "5634":
                bus, dev = int(open(d + "/busnum").read()), int(open(d + "/devnum").read())
                return d, "/dev/bus/usb/%03d/%03d" % (bus, dev)
        except OSError:
            pass
    return None, None

def ctrl(fd, rt, req, val, idx, length, timeout=1000):
    buf = ctypes.create_string_buffer(max(length, 1))
    c = Ctrl(rt, req, val, idx, length, timeout, ctypes.addressof(buf))
    n = fcntl.ioctl(fd, CTRL, c)
    return buf.raw[:n]

def main():
    n = int(sys.argv[1]); resets = int(sys.argv[2])
    sysd, path = find()
    print("device", sysd, path)
    fd = os.open(path, os.O_RDWR)
    cfg = ctrl(fd, 0x80, 6, 0x0200, 0, 9)
    total = cfg[2] | cfg[3] << 8
    full = ctrl(fd, 0x80, 6, 0x0200, 0, total)
    print("config descriptor", full.hex())
    tests = [("GET_DESCRIPTOR device", (0x80, 6, 0x0100, 0, 18), DEVICE),
             ("GET_DESCRIPTOR config", (0x80, 6, 0x0200, 0, total), full)]
    extra = [("GET_STATUS device", (0x80, 0, 0, 0, 2)), ("GET_CONFIGURATION", (0x80, 8, 0, 0, 1)),
             ("GET_DESCRIPTOR string0", (0x80, 6, 0x0300, 0, 4)), ("GET_DESCRIPTOR qualifier", (0x80, 6, 0x0600, 0, 10))]
    for name, a in extra:
        try:
            print("probe", name, ctrl(fd, *a).hex())
        except OSError as e:
            print("probe", name, errno.errorcode[e.errno])
    res = collections.Counter()
    t0 = time.time()
    for i in range(n):
        name, a, want = tests[i % len(tests)]
        try:
            got = ctrl(fd, *a)
            res[name + (" ok" if got == want else " MISMATCH")] += 1
            if got != want and res[name + " MISMATCH"] < 4:
                print("mismatch", i, got.hex())
        except OSError as e:
            res[name + " " + errno.errorcode[e.errno]] += 1
    dt = time.time() - t0
    print("%d transfers in %.1f s (%.2f ms each)" % (n, dt, 1e3 * dt / n))
    for k, v in sorted(res.items()):
        print(" ", k, v)
    ok = bad = 0
    for i in range(resets):
        try:
            fcntl.ioctl(fd, RESET, 0)
            time.sleep(0.05)
            got = ctrl(fd, 0x80, 6, 0x0100, 0, 18)
            conf = ctrl(fd, 0x80, 8, 0, 0, 1)
            if got == DEVICE:
                ok += 1
            else:
                bad += 1
                print("reset", i, "descriptor", got.hex())
        except OSError as e:
            bad += 1
            print("reset", i, errno.errorcode[e.errno])
            time.sleep(0.2)
    print("bus resets: %d good, %d bad of %d" % (ok, bad, resets))
    os.close(fd)
    return 1 if bad or any(("ok" not in k) for k in res) else 0

sys.exit(main())
