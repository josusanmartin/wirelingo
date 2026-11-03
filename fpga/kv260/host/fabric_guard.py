"""The one check every /dev/mem opener makes before it maps anything.

On 2026-11-03 `xmutil loadapp protoemu` returned Load Error -1, the next command
ran a host script anyway, and the Kria stopped answering. The likely cause, not
proved: pe_spim.Spim reads VERSION as soon as it is built, and on a Zynq MPSoC a read
of a fabric address with no working bitstream behind it can hang the bus. So a "not
found" check on a fabric register comes too late: it is the read that hangs.

pe_kria.DevMem, pe_spim.SpimMem and pe_lab.LabMem call require_app() first. Injected
`mem`/`regs` objects (the simulation, the cycle model, fakes) never reach it.

What is read, and how we know it doesn't touch the fabric:

1. `xmutil listapps`, a subprocess. This module maps nothing, and test_fabric_guard.py
   proves it doesn't open /dev/mem. xmutil itself asks the dfx-mgr daemon for its
   bookkeeping. That is from its documented design, not from reading its source, and
   it has not been observed on a board with no app. If that is wrong, the guard can't
   make it worse: the one failing case was a mapped read.
2. /sys/class/fpga_manager/fpga0/state, a kernel attribute (the kernel's
   sysfs-class-fpga-manager ABI). Present and not "operating": refused.
3. /proc/device-tree/fpga-full/firmware-name, the kernel's in-memory device tree.
   protoemu.dtsi's overlay sets it to "<app>.bit.bin", and an overlay whose bitstream
   failed to program is reverted. Present and naming something else: refused.

2 and 3 cover a daemon listing an app that isn't really there. Their paths are
expected, not seen on this board, so an absent file is no evidence either way and
only (1) decides. The table's format is from memory of dfx-mgr. A table this can't
parse refuses, unless a person who read it sets PE_ACTIVE_APP=<app>; a table it can
parse is never overruled, and (2) and (3) still apply.
"""
import os
import pathlib
import subprocess

APP_PREFIX = "protoemu"                  # protoemu, protoemu40, protoemu48: each is one of our bitstreams
OVERRIDE = "PE_ACTIVE_APP"
FPGA_STATE = "/sys/class/fpga_manager/fpga0/state"
FIRMWARE_NAME = "/proc/device-tree/fpga-full/firmware-name"
STOP = ("No tool may run on this board now. See docs/hardware-day.md, step 0: "
        "read-only diagnosis only (dmesg, xmutil listapps, journalctl for dfx-mgr)")


class NoActiveApp(RuntimeError):
    """Nothing was mapped: the fabric can't be shown to hold our app."""


def parse(text):
    """(active app or None, whether any row of the table was understood).
    An active row ends in a slot number with a comma; an inactive one in -1."""
    understood = False
    for line in text.splitlines():
        t = line.split()
        if len(t) < 2 or t[0] == "Accelerator":
            continue
        if t[-1] == "-1":
            understood = True
        elif t[-1].rstrip(",").isdigit():
            return t[0], True
    return None, understood


def listapps():
    try:
        r = subprocess.run(["xmutil", "listapps"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise NoActiveApp(f"`xmutil listapps` didn't run ({e}). {STOP}")
    if r.returncode:
        raise NoActiveApp(f"`xmutil listapps` failed (status {r.returncode}): {r.stderr.strip()}. {STOP}")
    return r.stdout


def text_of(path):
    """A file's stripped text, or None if it isn't there."""
    try:
        return pathlib.Path(path).read_bytes().replace(b"\0", b"").decode(errors="replace").strip()
    except OSError:
        return None


def check(read=listapps, state=FPGA_STATE, firmware=FIRMWARE_NAME, env=os.environ):
    """The active protoemu app's name, or NoActiveApp. Touches no fabric address."""
    text = read()
    app, understood = parse(text)
    if app is None and understood:
        raise NoActiveApp(f"`xmutil listapps` shows no active app. {STOP}")
    if app is None:
        app = env.get(OVERRIDE)
        if not app:
            raise NoActiveApp(f"can't read the active app from `xmutil listapps`:\n{text}\n"
                              f"Read it yourself; only if it shows an active protoemu app, set {OVERRIDE}=<app>. {STOP}")
    if not app.startswith(APP_PREFIX):
        raise NoActiveApp(f"active app is {app!r}, not a protoemu app, so these addresses aren't ours. {STOP}")
    s = text_of(state)
    if s is not None and s != "operating":
        raise NoActiveApp(f"the FPGA manager's state is {s!r}, not 'operating': a load failed. {STOP}")
    f = text_of(firmware)
    if f is not None and f != app + ".bit.bin":
        raise NoActiveApp(f"the device tree's firmware-name is {f!r}, not {app}.bit.bin. {STOP}")
    return app


_checked = None


def require_app(**kw):
    """Every opener's first line. Once per process: a script doesn't load apps."""
    global _checked
    if _checked is None:
        _checked = check(**kw)
    return _checked
