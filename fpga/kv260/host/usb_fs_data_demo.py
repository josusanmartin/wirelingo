#!/usr/bin/env python3
"""Data at full speed on the Kria's own USB port: firmware/usb_fs_dev.pasm in the
chip's RTL, pe.usbfsdata's device software on the SPI port, and a Linux process
as the USB client. The client writes random data with vendor control transfers
through the kernel, reads it back and compares.

Run as root on the Kria with the 48 MHz app (protoemu48) loaded and the
full-speed wiring of fpga/kv260/README.md:

    sudo python3 protoemu48/fpga/kv260/host/usb_fs_data_demo.py [--sizes 8,64,1024] [--rounds 20]

Unlike usb_demo.py the chip is not autonomous here: Python answers every request
over the SPI port, so the throughput measures this loop, not the chip. Exit
status 0 = every byte read back equal.

--nodata N: before the round trips, the client sends N zero-length vendor
requests (--rt/--rq: 0x40 0x10 = REQ_WRITE, the default; 0xC0 0x11 = REQ_READ),
1 s timeout each, and counts how many time out or fail otherwise; the log line
names RT and RQ. (This replaces the orchestrator's nodata.py/nodata2.py.)

--model: the same device side (usb_fs_dev on core 0 with the line unit,
pe.usbfsdata as the host software) on the cycle model, no board: a pins-only
host (pe.usbfs.FsHost) does what Linux did on the board (usbmon, 2026-11-03):
the 64-byte device descriptor read ended by the first 8-byte packet, a 10 ms
port reset, SET_ADDRESS, the device and configuration descriptors and
SET_CONFIGURATION, then the client's requests, every stage checked (DATA
toggles, one zero-length DATA1 per status stage). The device software is
served between the host's transactions, not concurrently with them (that is
test/model/test_usb_fs_data.py). Same output lines as on the board, in model
time. fpga/kv260/host/usb_guard_run.py drives both.
"""
import argparse
import ctypes
import errno
import fcntl
import os
import pathlib
import random
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import usb_demo                              # noqa: E402  (also sets up the import paths)
import usb_stress                            # noqa: E402
from pe import asm, host, sim, usbdev, usbfsdata   # noqa: E402

FW = usb_demo.ROOT / "firmware" / "usb_fs_dev.pasm"
LINECFG = 0x49F3                             # as low speed (test_usb_fs.py)
SERVICE_CPU, CLIENT_CPU = 3, 2


def attach(board, spim, pe):
    """Wiring check, soft detach, load, attach, start. Returns (program, column order)."""
    spim.pmod({})
    board.reset()
    pe.stop(3)
    order, line = usb_demo.detect(spim, pe)
    if line != "D+":
        raise usb_demo.WiringError(f"the pull-up is on {line}; full speed needs it on D+")
    spim.pmod({usb_demo.WIRING[order]["D+"]: usb_demo.UO0})
    prog = asm.assemble(FW.read_text(), usbdev.defines())
    for addr, words in host.coalesce(host.load_program(0, prog, 0, clkdiv=1 << 8)):
        pe.write(addr, words)
    pe.write(sim.core_reg(0, sim.C_CRCCTRL), usbdev.CRCCTRL)
    pe.write(sim.A_LINEDIV, (4 << 16) | (4 << 8))
    pe.write(sim.A_LINEPINS, 0)
    pe.write(sim.A_LINECFG, LINECFG)
    time.sleep(0.5)
    spim.pmod(usb_demo.line_map("full", order))
    for addr, words in host.coalesce(host.start_writes(1)):
        pe.write(addr, words)
    print(f"  loaded usb_fs_dev ({len(prog.words)} words), attached, core 0 started")
    return prog, order


def control(fd, request_type, request, data=None, length=0):
    """One vendor control transfer: `data` goes out, or `length` bytes come in."""
    n = len(data) if data is not None else length
    buf = ctypes.create_string_buffer(data, max(n, 1)) if data else ctypes.create_string_buffer(max(n, 1))
    xfer = usb_stress.CtrlTransfer(request_type, request, 0, 0, n, 5000, ctypes.addressof(buf))
    got = fcntl.ioctl(fd, usb_stress.USBDEVFS_CONTROL, xfer)
    return buf.raw[:got]


def nodata_line(n, rt, rq, lat, timeouts, other):
    """The --nodata summary (board and model alike); lat in seconds."""
    head = f"  NODATA rt 0x{rt:02X} rq 0x{rq:02X} wLength 0: {n} sent: {len(lat)} ok, {timeouts} timed out, other {other}"
    lat = sorted(lat)
    if lat:
        head += (f"; latency ms min {lat[0] * 1e3:.2f} median {lat[len(lat) // 2] * 1e3:.2f} "
                 f"max {lat[-1] * 1e3:.2f}")
    return head


def nodata(fd, n, rt, rq):
    """n zero-length vendor requests through the kernel, 1 s timeout each. Returns the exit status."""
    buf = ctypes.create_string_buffer(1)
    timeouts, other, lat = 0, {}, []
    for i in range(n):
        xfer = usb_stress.CtrlTransfer(rt, rq, 0, 0, 0, 1000, ctypes.addressof(buf))
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
    print(nodata_line(n, rt, rq, lat, timeouts, other), flush=True)
    return int(timeouts > 0 or bool(other))


def client(sizes, rounds, seed, n_nodata=0, rt=0x40, rq=usbfsdata.REQ_WRITE):
    """The USB side: only the kernel's interface. Returns the exit status."""
    os.sched_setaffinity(0, {CLIENT_CPU})
    fd = os.open(usb_stress.find(), os.O_RDWR)
    rng = random.Random(seed)
    status = nodata(fd, n_nodata, rt, rq) if n_nodata else 0
    for size in sizes:
        bad = 0
        t0 = time.monotonic()
        for _ in range(rounds):
            data = rng.randbytes(size)
            control(fd, 0x40, usbfsdata.REQ_WRITE, data=data)
            bad += control(fd, 0xC0, usbfsdata.REQ_READ, length=size) != data
        dt = time.monotonic() - t0
        print(f"  {size:5d} B x {rounds}: {rounds - bad} of {rounds} equal, "
              f"{2 * size * rounds / dt / 1e3:.1f} kB/s both ways together", flush=True)
        status |= bad != 0
    os.close(fd)
    return status


def run(board, spim, pe, prog, a, sizes):
    """Service the device until the client is done. Returns (status, order, software);
    status None: Linux never configured it."""
    sw = usbfsdata.LoopbackDevice(pe, prog, usbdev.DESCRIPTORS, 0, 0)
    t0 = time.monotonic()
    child = None
    polls = 0
    print("[service] answering over the SPI port until Linux has configured the device", flush=True)
    while True:
        sw.service()
        polls += 1
        if polls & 255:
            continue
        now = time.monotonic()
        if child is None:
            d = usb_demo.sysfs_device()
            if d is not None and pathlib.Path(d, "bConfigurationValue").read_text().strip() == "1":
                print(f"  configured after {now - t0:.2f} s, {len(sw.requests)} requests, address "
                      f"{sw.address}\n[data] Linux client, random bytes out and back", flush=True)
                t0 = now
                child = os.fork()
                if child == 0:
                    os._exit(client(sizes, a.rounds, a.seed, a.nodata, a.rt, a.rq))
            elif now - t0 > a.timeout:
                return None, None, sw
        else:
            pid, st = os.waitpid(child, os.WNOHANG)
            if pid:
                return os.waitstatus_to_exitcode(st), None, sw
            if now - t0 > a.limit:
                os.kill(child, 9)
                os.waitpid(child, 0)
                print(f"  client still running after {a.limit:.0f} s: killed")
                return 1, None, sw


# ------------------------------------------------------------------ --model
class ModelBus:
    """--model: the chip on the cycle model, the device software served at each
    of the host's idle points, and a pins-only host's transfers with every stage
    checked (DATA toggles; a status stage is one zero-length DATA1)."""

    def __init__(self, seed):
        from pe.modelhost import ModelHost
        from pe.usbfs import FsHost
        from pe import usb
        self.usb = usb
        self.prog = asm.assemble(FW.read_text(), usbdev.defines())
        mh = self.mh = ModelHost()
        mh.apply(host.load_program(0, self.prog, 0, clkdiv=1 << 8))
        mh.write(sim.core_reg(0, sim.C_CRCCTRL), usbdev.CRCCTRL)
        mh.write(sim.A_LINEDIV, (4 << 16) | (4 << 8))
        mh.write(sim.A_LINEPINS, 0)
        mh.write(sim.A_LINECFG, LINECFG)
        mh.pull_up = 0b10
        mh.start(1)
        mh.step(50)
        self.sw = usbfsdata.LoopbackDevice(mh, self.prog, usbdev.DESCRIPTORS, 0, 0)
        self.h = FsHost(mh, idle=self.idle, seed=seed, jitter_ns=5)

    def idle(self, mh):
        mh.step(300)
        self.sw.service(mh)

    def ms(self):
        return self.h.now() / 1e6

    def wait_us(self, us):
        end = self.h.now() + us * 1e3
        while self.h.now() < end:
            self.h.wait_bits(64)
            self.idle(self.mh)

    def reset(self, ms=10):
        for _ in range(int(ms * 48000)):
            self.h._edge(0b00)
        self.h.last_eop_j = None
        self.wait_us(200)

    def status_in(self, addr, what):
        h, usb = self.h, self.usb
        pid, payload = h.data_in(addr, 0)
        if (pid, payload) != (usb.pid_byte(usb.PID_DATA1), b""):
            raise AssertionError(f"{what}: status stage got {pid:#04x} {payload.hex()}")

    def read(self, addr, req, what, mps=8):
        h, usb = self.h, self.usb
        n = req[6] | req[7] << 8
        h.setup(addr, req)
        if not n:
            self.status_in(addr, what)
            return b""
        out, toggle = b"", 1
        while len(out) < n:
            pid, payload = h.data_in(addr, 0)
            if pid != usb.pid_byte(usb.PID_DATA1 if toggle else usb.PID_DATA0):
                raise AssertionError(f"{what}: DATA toggle")
            toggle ^= 1
            out += payload
            if len(payload) < mps:
                break
        h.data_out(addr, 0, usb.PID_DATA1, b"")
        return out

    def write(self, addr, req, data, what):
        h, usb = self.h, self.usb
        h.setup(addr, req)
        for i in range(0, len(data), 8):
            h.data_out(addr, 0, usb.PID_DATA0 if i // 8 % 2 else usb.PID_DATA1, data[i:i + 8])
        self.status_in(addr, what)

    def enumerate(self, addr=31):
        """Linux's sequence on the board (usbmon, 2026-11-03)."""
        dev64 = bytes([0x80, 6, 0, 1, 0, 0, 64, 0])
        if self.read(0, dev64, "GET_DESCRIPTOR(device, 64)", mps=64) != usbdev.DEVICE_DESC[:8]:
            raise AssertionError("device descriptor")
        self.wait_us(100)
        self.reset()
        self.write(0, bytes([0, 5, addr, 0, 0, 0, 0, 0]), b"", "SET_ADDRESS")
        self.wait_us(2000)
        for req, want, what in ((bytes([0x80, 6, 0, 1, 0, 0, 18, 0]), usbdev.DEVICE_DESC, "device, 18"),
                                (bytes([0x80, 6, 0, 2, 0, 0, 9, 0]), usbdev.CONFIG_DESC[:9], "config, 9"),
                                (bytes([0x80, 6, 0, 2, 0, 0, 18, 0]), usbdev.CONFIG_DESC, "config, 18")):
            if self.read(addr, req, f"GET_DESCRIPTOR({what})") != want:
                raise AssertionError(f"GET_DESCRIPTOR({what})")
        self.wait_us(569)
        self.write(addr, bytes([0, 9, 1, 0, 0, 0, 0, 0]), b"", "SET_CONFIGURATION")

    def client(self, addr, sizes, rounds, seed, n_nodata, rt, rq):
        """The board client's requests, in the same order and with the same output lines."""
        status = 0
        if n_nodata:
            timeouts, other, lat = 0, {}, []
            req = bytes([rt, rq, 0, 0, 0, 0, 0, 0])
            for i in range(n_nodata):
                t0 = self.ms()
                try:
                    if rt & 0x80:
                        self.read(addr, req, f"no-data {i}")
                    else:
                        self.write(addr, req, b"", f"no-data {i}")
                    lat.append((self.ms() - t0) / 1e3)
                except TimeoutError:
                    timeouts += 1
                    print(f"  no-data {i}: TIMEOUT after {self.ms() - t0:.2f} ms of bus time", flush=True)
                except AssertionError as e:
                    other["protocol"] = other.get("protocol", 0) + 1
                    print(f"  no-data {i}: {e}", flush=True)
            print(nodata_line(n_nodata, rt, rq, lat, timeouts, other), flush=True)
            status |= int(timeouts > 0 or bool(other))
        rng = random.Random(seed)
        for size in sizes:
            bad = 0
            t0 = self.ms()
            for _ in range(rounds):
                data = rng.randbytes(size)
                self.write(addr, bytes([0x40, usbfsdata.REQ_WRITE, 0, 0, 0, 0, size & 255, size >> 8]), data, "write")
                got = self.read(addr, bytes([0xC0, usbfsdata.REQ_READ, 0, 0, 0, 0, size & 255, size >> 8]), "read")
                bad += got != data
            dt = (self.ms() - t0) / 1e3
            print(f"  {size:5d} B x {rounds}: {rounds - bad} of {rounds} equal, "
                  f"{2 * size * rounds / dt / 1e3:.1f} kB/s both ways together (model bus time)", flush=True)
            status |= bad != 0
        return status


def model_main(a, sizes):
    import hashlib
    words = len(asm.assemble(FW.read_text(), usbdev.defines()).words)
    print(f"[identity] cycle model (no board); usb_fs_dev {words} words, "
          f"sha256 {hashlib.sha256(FW.read_bytes()).hexdigest()[:16]}")
    print("[wiring]\n  model pads: D- on uio0, D+ on uio1, 1.5k pull-up on D+")
    status, sw = None, None
    for attempt in range(1, a.attaches + 1):
        bus = ModelBus(a.seed + attempt)
        sw = bus.sw
        print("[service] answering between the model host's transactions until it has configured the device",
              flush=True)
        try:
            bus.enumerate()
        except (AssertionError, TimeoutError) as e:
            st = bus.mh.read(sim.core_reg(0, sim.C_STATUS))
            print(f"  attach {attempt} of {a.attaches}: not configured ({e}); requests seen: {sw.requests[-3:]}; "
                  f"F_SET 0x{bus.mh.read(sim.A_FSET):02X}; core 0 TX FIFO {st & 15} words, RX FIFO {(st >> 4) & 15}, "
                  f"PC {bus.mh.read(sim.core_reg(0, sim.C_PC))}; software staged {sw.unacked}, pending {sw.pending}")
            continue
        print(f"  configured after {bus.ms():.2f} ms of bus time, {len(sw.requests)} requests, address {sw.address}\n"
              f"[data] model host, the client's requests", flush=True)
        status = bus.client(sw.address, sizes, a.rounds, a.seed, a.nodata, a.rt, a.rq)
        break
    print(f"[device software] attach {attempt}: {len(sw.requests)} requests, {sw.restaged} packets re-staged")
    print("PASS (model)" if status == 0 else "FAIL (model)")
    sys.exit(0 if status == 0 else 1)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--sizes", default="8,64,256,1024,4096")
    ap.add_argument("--rounds", type=int, default=20)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--timeout", type=float, default=7, help="seconds to wait for configuration")
    ap.add_argument("--attaches", type=int, default=3, help="attaches to try (docs: the SET_CONFIGURATION stall)")
    ap.add_argument("--limit", type=float, default=300, help="seconds the client may take")
    ap.add_argument("--nodata", type=int, default=0, help="zero-length requests before the round trips")
    ap.add_argument("--rt", type=lambda s: int(s, 0), default=0x40, help="their bmRequestType (0x40 or 0xC0)")
    ap.add_argument("--rq", type=lambda s: int(s, 0), default=usbfsdata.REQ_WRITE, help="their bRequest")
    ap.add_argument("--model", action="store_true", help="the cycle model, no board")
    a = ap.parse_args()
    sizes = [int(s) for s in a.sizes.split(",")]
    if a.model:
        model_main(a, sizes)

    from pe_kria import Board
    from pe_lab import Lab
    from pe_spim import Spim
    lab = Lab()
    ident = lab.identity()
    lab.mode(Lab.GPIO)
    if ident["clk_div"] != 25:
        sys.exit("FAIL: this needs the 48 MHz build. Load it one command at a time, reading what each prints:\n"
                 "  sudo xmutil unloadapp\n  sudo xmutil loadapp protoemu48\n  sudo xmutil listapps\n"
                 "and run nothing unless the table shows protoemu48 as its one active row")
    board = Board()
    board.reset()
    spim = Spim()
    spim.div, spim.quad = 1, False
    spim.select(fabric=True)
    pe = board.protoemu()
    pe.transport = spim
    if pe.read(sim.A_ID) != sim.ID_VALUE:
        sys.exit("FAIL: chip ID over the fabric master")
    print(f"[identity] RTL revision {ident['rtl_rev'][:12]}, clock {board.clk_hz() / 1e6:.4f} MHz")

    print("[wiring]")
    try:
        prog, order = attach(board, spim, pe)
    except usb_demo.WiringError as e:
        spim.pmod({})
        print(f"  {e}\nFAIL (wiring): every PMOD pin disabled, the core not started")
        sys.exit(2)

    os.sched_setaffinity(0, {SERVICE_CPU})
    status = None
    for attempt in range(1, a.attaches + 1):
        status, _, sw = run(board, spim, pe, prog, a, sizes)
        if status is not None:
            break
        st = pe.read(sim.core_reg(0, sim.C_STATUS))     # reads only: TX/RX FIFO levels, PC
        print(f"  attach {attempt} of {a.attaches}: not configured after {a.timeout:.0f} s; "
              f"requests seen: {sw.requests[-3:]}; F_SET 0x{pe.read(sim.A_FSET):02X}; "
              f"core 0 TX FIFO {st & 15} words, RX FIFO {(st >> 4) & 15}, PC {pe.read(sim.core_reg(0, sim.C_PC))}; "
              f"software staged {sw.unacked}, pending {sw.pending}")
        if attempt < a.attaches:
            prog, order = attach(board, spim, pe)
    usb_demo.detach(board, spim, pe, "full", order)
    print(f"[device software] attach {attempt}: {len(sw.requests)} requests, {sw.restaged} packets re-staged; detached")
    print("PASS" if status == 0 else "FAIL")
    sys.exit(0 if status == 0 else 1)


if __name__ == "__main__":
    main()
