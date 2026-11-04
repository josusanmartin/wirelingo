"""usb_fs_data_demo, but once configured the client sends NODATA_N no-data vendor
OUT requests (0x40, REQ_WRITE, wLength 0; status stage = an IN the device first
NAKs while Python stages the ZLP) with a 1 s timeout each, and counts how many
time out, how many fail otherwise, and the latency spread. Then one 8-byte round
trip, so the usual PASS line still means the device works afterwards.
"""
import ctypes, errno, fcntl, os, sys, time
sys.path.insert(0, "/home/ubuntu/protoemu48/fpga/kv260/host")
N = int(os.environ.get("NODATA_N", "1000"))
import usb_fs_data_demo as d
import usb_stress
from pe import usbfsdata
orig_client = d.client
C = usbfsdata.LoopbackDevice
orig_handle = C.handle
def handle(self, req):
    # pe.usbfsdata stages the status ZLP of a zero-length REQ_WRITE twice (handle and
    # service); stage it once here so the test measures the bus, not that bug
    if req[0] == 0x40 and req[1] == usbfsdata.REQ_WRITE and not (req[6] | req[7] << 8):
        usbfsdata.usbdev.DeviceSoftware.handle(self, bytes(8))
        self.out_records, self.incoming, self.pending = [], None, [b""]
        return
    return orig_handle(self, req)
C.handle = handle

def client(sizes, rounds, seed):
    os.sched_setaffinity(0, {d.CLIENT_CPU})
    fd = os.open(usb_stress.find(), os.O_RDWR)
    buf = ctypes.create_string_buffer(1)
    timeouts, other, lat = 0, {}, []
    for i in range(N):
        xfer = usb_stress.CtrlTransfer(0x40, usbfsdata.REQ_WRITE, 0, 0, 0, 1000, ctypes.addressof(buf))
        t0 = time.monotonic()
        try:
            fcntl.ioctl(fd, usb_stress.USBDEVFS_CONTROL, xfer)
            lat.append(time.monotonic() - t0)
        except OSError as e:
            if e.errno == errno.ETIMEDOUT:
                timeouts += 1
                print(f"  no-data {i}: TIMEOUT after {time.monotonic() - t0:.2f} s", flush=True)
            else:
                other[e.errno] = other.get(e.errno, 0) + 1
                print(f"  no-data {i}: errno {e.errno} ({os.strerror(e.errno)})", flush=True)
    os.close(fd)
    lat.sort()
    if lat:
        print(f"  NODATA {N} sent: {len(lat)} ok, {timeouts} timed out, other {other}; "
              f"latency ms min {lat[0]*1e3:.2f} median {lat[len(lat)//2]*1e3:.2f} max {lat[-1]*1e3:.2f}", flush=True)
    else:
        print(f"  NODATA {N} sent: 0 ok, {timeouts} timed out, other {other}", flush=True)
    return orig_client(sizes, rounds, seed) | (timeouts > 0) | bool(other)

d.client = client
d.main()
