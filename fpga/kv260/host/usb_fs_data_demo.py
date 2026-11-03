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
"""
import argparse
import ctypes
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


def client(sizes, rounds, seed):
    """The USB side: only the kernel's interface. Returns the exit status."""
    os.sched_setaffinity(0, {CLIENT_CPU})
    fd = os.open(usb_stress.find(), os.O_RDWR)
    rng = random.Random(seed)
    status = 0
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
                    os._exit(client(sizes, a.rounds, a.seed))
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


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--sizes", default="8,64,256,1024,4096")
    ap.add_argument("--rounds", type=int, default=20)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--timeout", type=float, default=7, help="seconds to wait for configuration")
    ap.add_argument("--attaches", type=int, default=3, help="attaches to try (docs: the SET_CONFIGURATION stall)")
    ap.add_argument("--limit", type=float, default=300, help="seconds the client may take")
    a = ap.parse_args()
    sizes = [int(s) for s in a.sizes.split(",")]

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
        print(f"  attach {attempt} of {a.attaches}: not configured after {a.timeout:.0f} s; "
              f"requests seen: {sw.requests[-3:]}; F_SET 0x{pe.read(sim.A_FSET):02X}")
        if attempt < a.attaches:
            prog, order = attach(board, spim, pe)
    usb_demo.detach(board, spim, pe, "full", order)
    print(f"[device software] attach {attempt}: {len(sw.requests)} requests, {sw.restaged} packets re-staged; detached")
    print("PASS" if status == 0 else "FAIL")
    sys.exit(0 if status == 0 else 1)


if __name__ == "__main__":
    main()
