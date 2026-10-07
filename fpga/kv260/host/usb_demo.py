#!/usr/bin/env python3
"""Wirelingo's autonomous USB device (firmware/usb_enum.pasm) on the Kria's own
USB-A port, through PMOD J2 (wiring and safety: fpga/kv260/README.md, "USB
device on the Kria's own USB port").

    sudo python3 fpga/kv260/host/usb_demo.py                # on the Kria, protoemu48 app loaded
    sudo python3 fpga/kv260/host/usb_demo.py --dry-run      # no PMOD pin is touched
    python3 fpga/kv260/host/usb_demo.py --model             # cycle model, anywhere

Wiring (three connections, no GND, no VBUS): D+ through 27 ohm to J2
silkscreen 1, D- through 27 ohm to silkscreen 3, 1.5 kohm from D- to 3.3 V
(low speed). In the scripts' Digilent numbering those are J2-1 and J2-2, i.e.
pmod[0] and pmod[1] if the header's columns run the way kv260.xdc assumes, or
pmod[3] and pmod[2] if they run the other way (the jumper loopback could not
tell): `detect` finds out from which pin carries the pull-up.

Steps on the board:
 1. identity and clock: the bitstream must be a 48 MHz build (clk_div 25), the
    measured clock within 0.1 % of 48 MHz;
 2. wiring check (`detect`): each top-row pin (silkscreen 1, 3, 5, 7) in turn is
    driven low for a few microseconds and released; the one that reads high
    right after is the pulled-up line (an open pin keeps its low charge). That
    tells the column order and whether the pull-up is on D- (low speed) or D+
    (full speed). Nothing else is driven; no pull-up found = stop, with a hint
    (if the 3.3 V and GND columns are the other way round, the resistor goes to
    GND and the device simply never attaches);
 3. soft detach: the pulled-up line is held low (SE0: the hub sees a
    disconnect) while core 0 is loaded with usb_enum, CRCCTRL and the line unit
    (pe.usbenum.load_writes) over the fabric SPI master;
 4. attach and start: the two PMOD pins are switched to uio0/uio1 (low speed:
    D+ = uio0, the line unit's pin, D- = uio1; full speed the other way round),
    which releases the pulled-up line (J: the hub sees a device), and core 0
    is started. That is the last access to the chip's host port;
 5. watch: the Kria's own kernel log (dmesg) and sysfs until the device with
    VID:PID 1209:5634 (pid.codes' test VID and the repository's placeholder PID;
    pe.usbdev.DEVICE_DESC) is configured (bConfigurationValue 1), or a timeout;
    verdict, the kernel's lines, `lsusb`;
 6. only then the chip's state over the host port (address in r3, F5 =
    configured). The device stays attached and running; --stop detaches it
    (holds the pulled-up line low with the core stopped) instead.

--model runs the host flow Linux uses (64-byte device descriptor read at
address 0, bus reset, SET_ADDRESS, descriptors, SET_CONFIGURATION) with the
independent host controller model pe.usbhost.ControlHost against the cycle
model, with the host transport refused after the start. The RTL of the whole
KV260 design runs this script's board steps 2-4 and the same flow on the PMOD
pads (fpga/kv260/sim, test_usb_demo).
"""
import argparse
import glob
import os
import re
import subprocess
import sys
import time

import pathlib
HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
for sub in ("tools", "tools/micropython", str(HERE)):
    p = sub if sub == str(HERE) else str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

from pe import sim, usbdev, usbenum   # noqa: E402

VID, PID = 0x1209, 0x5634                  # pe.usbdev.DEVICE_DESC (placeholder PID)
F_NOMINAL = 48e6
UO0 = 8                                    # TT pin index of uo0 (kv260_top.v PMOD routing)
UIO0, UIO1 = 0, 1
# Physical wiring: D+ on silkscreen 1, D- on silkscreen 3 (J2-1, J2-2). Which
# pmod[] index those are depends on the column order.
WIRING = {"normal": {"D+": 0, "D-": 1}, "reversed": {"D+": 3, "D-": 2}}
SILK = {"normal": {0: 1, 1: 3, 2: 5, 3: 7}, "reversed": {3: 1, 2: 3, 1: 5, 0: 7}}
PULLUP = {"low": "D-", "full": "D+"}


class WiringError(Exception):
    pass


def line_map(speed, order):
    """PMOD pin -> TT pin. The line unit's pin (uio0) is D+ at low speed and D-
    at full speed; uio1 the other (so J, the idle state, is uio1 high)."""
    w = WIRING[order]
    if speed == "low":
        return {w["D+"]: UIO0, w["D-"]: UIO1}
    return {w["D-"]: UIO0, w["D+"]: UIO1}


def detect(spim, pe, reps=5, log=print):
    """Find the pulled-up line among the four top-row pins: pulse each low from
    uo0 (PIN_OUT 0) and read it right after release, `reps` times. Returns
    (order, line) with line 'D-' (low-speed wiring) or 'D+' (full speed)."""
    pe.write(sim.A_POUT, 0)
    high = {}
    for p in range(4):
        n = 0
        for _ in range(reps):
            spim.pmod({p: UO0})
            spim.pmod({})
            n += spim.pmod_in() >> p & 1
        high[p] = n
    log("  pins pulsed low then read (of %d): " % reps
        + ", ".join(f"pmod[{p}] {high[p]}" for p in range(4)))
    up = [p for p in range(4) if high[p] == reps]
    odd = [p for p in range(4) if 0 < high[p] < reps]
    if odd:
        raise WiringError(f"pmod{odd} read high only sometimes: a loose wire or an unexpected load")
    if not up:
        raise WiringError("no pulled-up pin: the 1.5k pull-up is missing, or its end is on GND "
                          "(the 3.3 V and GND columns would then be the other way round: move it to "
                          "the other left column), or D+/D- are not on silkscreen 1 and 3")
    if len(up) > 1:
        raise WiringError(f"several pulled-up pins {up}: only one line may have the 1.5k")
    p = up[0]
    for order, w in WIRING.items():
        for line, q in w.items():
            if q == p:
                log(f"  pull-up found on pmod[{p}] = silkscreen {SILK[order][p]}: columns {order}, "
                    f"it is {line} ({'low' if line == 'D-' else 'full'}-speed wiring)")
                return order, line
    raise WiringError(f"pull-up on pmod[{p}] = silkscreen {SILK['normal'][p]} or {SILK['reversed'][p]}, "
                      "which is neither D+ nor D- (expected on silkscreen 1 or 3)")


def prepare(board, spim, pe, speed="low", order=None, hold_s=0.5, log=print):
    """Board steps 2-4 (module docstring): wiring check, soft detach, load,
    attach, start. The start is the last host-port access. Returns the order."""
    spim.pmod({})
    board.reset()
    pe.stop(3)
    found, line = detect(spim, pe, log=log)
    if line != PULLUP[speed]:
        raise WiringError(f"the pull-up is on {line}, which is {('full' if line == 'D+' else 'low')}-speed "
                          f"wiring; for {speed} speed it belongs on {PULLUP[speed]} (or pass --speed)")
    if order and order != found:
        raise WiringError(f"--order {order}, but the pull-up says {found}")
    order = found
    up = WIRING[order][PULLUP[speed]]
    spim.pmod({up: UO0})                              # SE0: the hub sees a disconnect
    log(f"  holding {PULLUP[speed]} (pmod[{up}]) low: detached")
    t0 = time.monotonic()
    prog = usbenum.assemble(speed)
    from pe import host
    for addr, words in host.coalesce(usbenum.load_writes(prog, speed, start=False)):
        pe.write(addr, words)
    log(f"  loaded usb_enum ({len(prog.words)} words, {speed} speed) into core 0, CRCCTRL, line unit")
    oe = board.pins()[2]
    if oe & 3:
        raise RuntimeError(f"uio_oe = 0x{oe:02X} before the start: the chip drives uio0/1")
    time.sleep(max(0.0, hold_s - (time.monotonic() - t0)))
    m = line_map(speed, order)
    spim.pmod(m)                                       # releases the pulled-up line: attach
    for addr, words in host.coalesce(host.start_writes(1)):
        pe.write(addr, words)                          # the last host-port access
    log("  attached: " + ", ".join(f"pmod[{p}] (silkscreen {SILK[order][p]}) = uio{t}" for p, t in sorted(m.items()))
        + "; core 0 started; host port idle from here on")
    return order


def detach(board, spim, pe, speed, order):
    """Stop the core and hold the pulled-up line low (the hub sees a disconnect)."""
    up = WIRING[order][PULLUP[speed]]
    pe.write(sim.A_POUT, 0)
    spim.pmod({up: UO0})
    pe.stop(1)


# ---------------------------------------------------------------- the host side (Linux)

USB_LINE = re.compile(r"^\[\s*([0-9.]+)\]\s+(.*)$")
INTERESTING = re.compile(r"\busb\b|hub|xhci|USB")


def kernel_log():
    """[(timestamp, text)] from dmesg."""
    out = subprocess.run(["dmesg"], capture_output=True, text=True, check=True).stdout
    lines = []
    for ln in out.splitlines():
        m = USB_LINE.match(ln)
        if m:
            lines.append((float(m.group(1)), m.group(2)))
    return lines


def sysfs_device(vid=VID, pid=PID):
    """The sysfs directory of the device, or None."""
    for d in glob.glob("/sys/bus/usb/devices/*"):
        try:
            v = open(os.path.join(d, "idVendor")).read().strip()
            p = open(os.path.join(d, "idProduct")).read().strip()
        except OSError:
            continue
        if int(v, 16) == vid and int(p, 16) == pid:
            return d
    return None


def sysfs_attr(d, name):
    try:
        return open(os.path.join(d, name)).read().strip()
    except OSError:
        return None


HINTS = [
    ("device descriptor read", "the device's answers don't arrive or don't decode: D+/D- swapped, the 27 ohm "
                               "resistors on the wrong lines, or the clock is not 48 MHz"),
    ("device not accepting address", "the device answered address 0 but not SET_ADDRESS's new address"),
    ("can't set config", "SET_CONFIGURATION failed"),
    ("Cannot enable", "the port reset failed: check the D+/D- wiring"),
    ("unable to enumerate", "the hub saw a device but enumeration failed (see the lines above)"),
]


def watch(t_start, timeout, speed, log=print):
    """Kernel log and sysfs until the device is configured or `timeout` s.
    Returns (ok, lines after t_start, sysfs dir)."""
    deadline = time.monotonic() + timeout
    seen = 0
    lines = []
    found_new = False
    while True:
        lines = [(t, s) for t, s in kernel_log() if t > t_start]
        for t, s in lines[seen:]:
            if INTERESTING.search(s):
                log(f"  [{t:12.6f}] {s}")
            if f"new {speed}-speed USB device" in s:
                found_new = True
        seen = len(lines)
        d = sysfs_device()
        if d and found_new and sysfs_attr(d, "bConfigurationValue") == "1":
            return True, lines, d
        if time.monotonic() > deadline:
            return False, lines, d
        time.sleep(0.2)


def verdict(ok, lines, d, speed):
    text = [s for _, s in lines]
    if ok:
        print(f"PASS: Linux enumerated {VID:04x}:{PID:04x} at {speed} speed and configured it "
              f"({os.path.basename(d)}: devnum {sysfs_attr(d, 'devnum')}, speed {sysfs_attr(d, 'speed')} Mbit/s, "
              f"bConfigurationValue {sysfs_attr(d, 'bConfigurationValue')}, bMaxPacketSize0 "
              f"{sysfs_attr(d, 'bMaxPacketSize0')}, bcdDevice {sysfs_attr(d, 'bcdDevice')})")
        for cmd in (["lsusb", "-d", f"{VID:04x}:{PID:04x}"], ["lsusb", "-t"]):
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                print(f"  $ {' '.join(cmd)}\n" + "".join(f"    {ln}\n" for ln in r.stdout.splitlines()), end="")
            except (OSError, subprocess.TimeoutExpired) as e:
                print(f"  {cmd[0]}: {e}")
        return
    print("FAIL: no configured %04x:%04x" % (VID, PID))
    if not any("USB device" in s for s in text):
        print("  the kernel logged no new USB device: the hub never saw the attach. Check the pull-up "
              "(on D-, to 3.3 V) and that the cable's D+/D- go to the Kria's USB-A port")
    for key, hint in HINTS:
        if any(key in s for s in text):
            print(f"  '{key}': {hint}")


# ---------------------------------------------------------------- simulation paths

def linux_flow(h, addr=7):
    """The control transfers Linux's hub driver makes for a new device ("new
    scheme"), as h (pe.usbhost.ControlHost or a subclass) runs them: the 64-byte
    device descriptor read at address 0, a bus reset, SET_ADDRESS, the device
    descriptor, the configuration's header, all of it, SET_CONFIGURATION 1.
    Returns the device descriptor."""
    dev = h.get_descriptor(0, 1, 64)
    assert dev == usbdev.DEVICE_DESC, dev.hex()
    h.bus_reset()
    h.control_write(0, bytes([0x00, 5, addr, 0, 0, 0, 0, 0]))
    assert h.get_descriptor(addr, 1, 18) == usbdev.DEVICE_DESC
    cfg = h.get_descriptor(addr, 2, 9)
    assert cfg == usbdev.CONFIG_DESC[:9], cfg.hex()
    assert h.get_descriptor(addr, 2, cfg[2]) == usbdev.CONFIG_DESC
    h.control_write(addr, bytes([0x00, 9, 1, 0, 0, 0, 0, 0]))
    assert h.control_read(addr, bytes([0x80, 8, 0, 0, 0, 0, 1, 0])) == b"\x01"   # GET_CONFIGURATION
    return dev


def run_model(ppm=0.0):
    """The cycle model: load, start, then linux_flow with every host-transport
    operation refused."""
    from pe.modelhost import ModelHost
    from pe.usbhost import ControlHost
    mh = ModelHost()
    mh.apply(usbenum.load_writes(usbenum.assemble("low"), "low"))
    mh.pull_up = 0b10                                   # 1.5k on D- (uio1)
    h = ControlHost(mh, ppm=ppm)
    saved = {op: getattr(mh, op) for op in ("write", "read", "apply", "snap", "commit")}

    def refuse(op):
        def f(*a, **kw):
            raise AssertionError(f"host transport used after the start: {op}")
        return f
    for op in saved:
        setattr(mh, op, refuse(op))
    linux_flow(h)
    for op, f in saved.items():
        setattr(mh, op, f)
    st = usbenum.state(mh.read)
    return h, st, mh.chip.cycle


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--speed", choices=("low", "full"), default="low")
    ap.add_argument("--order", choices=("normal", "reversed"), help="column order (default: detected)")
    ap.add_argument("--timeout", type=float, default=20.0, help="seconds to wait for the kernel")
    ap.add_argument("--stop", action="store_true", help="detach (hold the pulled-up line low) and stop at the end")
    ap.add_argument("--dry-run", action="store_true",
                    help="identity, clock, load and start with NO PMOD pin enabled, then stop")
    ap.add_argument("--model", action="store_true", help="cycle model only (no hardware)")
    a = ap.parse_args()

    if a.model:
        for ppm in (0, 15000, -15000):
            h, st, cyc = run_model(ppm)
            ok = st == dict(address=7, configured=True, pending_address=False, stalled=False)
            print(f"[model, clock offset {ppm / 1e4:+.1f} %] {len(h.log)} transactions, {cyc} cycles "
                  f"({cyc / 48e3:.2f} ms at 48 MHz), turnaround {min(h.turnarounds):.2f}-{max(h.turnarounds):.2f} "
                  f"bit times; chip state {st}: {'PASS' if ok else 'FAIL'}")
            if not ok:
                sys.exit(1)
        print("PASS (model)")
        return

    from pe_kria import Board
    from pe_spim import Spim
    from pe_lab import Lab
    lab = Lab()
    ident = lab.identity()
    lab.mode(Lab.GPIO)
    print(f"[identity] RTL revision {ident['rtl_rev'][:12]}{' DIRTY' if ident['dirty'] else ''}, "
          f"clk_div {ident['clk_div']}, built {ident['build_utc']} UTC")
    if ident["clk_div"] != 25:
        sys.exit(f"FAIL: this bitstream runs protoemu at 1200/{ident['clk_div']} MHz; USB needs the 48 MHz "
                 "build (clk_div 25): sudo xmutil unloadapp; sudo xmutil loadapp protoemu48")
    board = Board()
    board.reset()
    f = board.clk_hz()
    print(f"[clock] protoemu clk measured {f / 1e6:.4f} MHz")
    if abs(f - F_NOMINAL) > 1e-3 * F_NOMINAL:
        sys.exit("FAIL: clock more than 0.1 % from 48 MHz")
    spim = Spim()
    spim.div, spim.quad = 1, False
    spim.select(fabric=True)
    pe = board.protoemu()
    pe.transport = spim
    if pe.read(sim.A_ID) != sim.ID_VALUE:
        sys.exit("FAIL: chip ID over the fabric master")

    if a.dry_run:
        spim.pmod({})
        board.reset()
        board.set_inputs(uio=0x02)                     # undriven uio pads: J (uio1 high, uio0 low)
        prog = usbenum.assemble(a.speed)
        from pe import host
        for addr, words in host.coalesce(usbenum.load_writes(prog, a.speed)):
            pe.write(addr, words)
        time.sleep(0.2)
        _, _, oe, _ = board.pins()
        ctrl = pe.read(sim.A_CTRL)
        st = usbenum.state(pe.read)
        pe.stop(3)
        board.set_inputs(uio=0xFF)
        ok = ctrl & 1 and not oe & 3 and not st["configured"] and st["address"] == 0
        print(f"[dry run] usb_enum loaded ({len(prog.words)} words) and started with every PMOD pin disabled: "
              f"CTRL 0x{ctrl:03X}, uio_oe 0x{oe:02X} (idle bus: the device never drives), state {st}; "
              f"PMOD {spim.mem.rd(0x8020):08X} {spim.mem.rd(0x8024):08X}; core stopped")
        print("PASS (dry run)" if ok else "FAIL (dry run)")
        sys.exit(0 if ok else 1)

    print("[wiring]")
    try:
        before = kernel_log()
        t_before = before[-1][0] if before else 0.0
        order = prepare(board, spim, pe, a.speed, a.order)
    except WiringError as e:
        spim.pmod({})
        print(f"  {e}\nFAIL (wiring): every PMOD pin disabled, the core not started")
        sys.exit(2)
    print(f"[kernel] waiting up to {a.timeout:.0f} s for {VID:04x}:{PID:04x} (no host-port access meanwhile)")
    ok, lines, d = watch(t_before, a.timeout, a.speed)
    verdict(ok, lines, d, a.speed)
    st = usbenum.state(pe.read)                        # after the verdict: the chip's view
    print(f"[chip] after the verdict, over the host port: address {st['address']}, configured {st['configured']}, "
          f"pending address {st['pending_address']}, stalled {st['stalled']}")
    if a.stop:
        detach(board, spim, pe, a.speed, order)
        print(f"  --stop: core 0 stopped, {PULLUP[a.speed]} held low (detached) until the app is reloaded")
    else:
        print("  left running and attached (--stop detaches)")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
