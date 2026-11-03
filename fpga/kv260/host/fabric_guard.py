"""The one check every /dev/mem opener makes before it maps anything. It fails closed.

On 2026-11-03 the Kria rebooted into its default app, k26-starter-kits, which held
the slot. `xmutil loadapp protoemu` printed `protoemu: Load Error: -1`, the next
command ran a host script anyway, the script read pe_spim's address inside the wrong
design, and the board froze: the previous boot's journal ends as that command started
(the orchestrator's reading of the board, read-only). A read of an address the loaded
design doesn't answer can hang a Zynq MPSoC's bus; that mechanism is the likely one,
not proved. So a "not found" check on a fabric register comes too late: it is the read
that hangs.

pe_kria.DevMem, pe_spim.SpimMem and pe_lab.LabMem call require_app() first. Injected
`mem`/`regs` objects (the simulation, the cycle model, fakes) never reach it.

Every check below must pass. Each reads something the kernel or the daemon keeps in
memory or on disk, never a fabric address, and this module maps nothing
(test_fabric_guard.py proves it doesn't open /dev/mem):

1. `xmutil listapps` (a subprocess), in exactly the table's format as read on the board
   on 2026-11-03: its header, seven columns a row, the slot count as `(n+n+n)`, the
   slot field `-1` or `n->n,`, exactly one active row, and that row's name one of
   KNOWN. Any other line, an error message included, refuses. xmutil asks the dfx-mgr
   daemon for its bookkeeping: from its documented design, not its source.
2. /proc/device-tree/fpga-region/firmware-name must be "<app>.bit.bin". The overlay
   (protoemu.dtsi, renamed per app by install.sh) sets it, and the board's boot log names
   that path (`OF: overlay: WARNING: memory leak will occur if overlay removed, property:
   /fpga-region/firmware-name`). /proc/device-tree is the kernel's live tree, which
   overlays change. That the property is removed when the overlay is removed, or reverted
   when programming fails, is from memory of how overlays work, not checked on this board.
   It is the check no daemon is involved in: in the incident it would have read
   k26-starter-kits.bit.bin.
3. /sys/class/fpga_manager/fpga0/state must be "operating" (the kernel's
   sysfs-class-fpga-manager ABI). Weak alone: from memory, it can stay "operating" from
   the last good programming.
4. /lib/firmware/xilinx/<app>/<app>.bit.bin must exist: install.sh puts it there, and the
   board has that directory.

An absent file refuses, naming the path, so the first real run shows whether the paths
are right. There is no override: a person can't vouch for a table this can't read.
"""
import pathlib
import re
import subprocess

KNOWN = ("protoemu", "protoemu40", "protoemu48")          # our apps, by exact name
HEADER = ["Accelerator", "Accel_type", "Base", "Pid", "Base_type", "#slots(RPU+PL+AIE)", "slot->handle"]
SLOTS = re.compile(r"\(\d+\+\d+\+\d+\)")
ACTIVE = re.compile(r"\d+->\d+,")
FPGA_STATE = "/sys/class/fpga_manager/fpga0/state"
FIRMWARE_NAME = "/proc/device-tree/fpga-region/firmware-name"
FIRMWARE_DIR = "/lib/firmware/xilinx"
STOP = ("No tool may run on this board now. See docs/hardware-day.md, step 0: "
        "read-only diagnosis only (dmesg, xmutil listapps, journalctl for dfx-mgr)")
SEQUENCE = ("The load, one command at a time, reading each: `sudo xmutil unloadapp`, then "
            "`sudo xmutil loadapp protoemu`, then `sudo xmutil listapps`, which must show protoemu as the "
            "one active row. Any error line: stop")


class NoActiveApp(RuntimeError):
    """Nothing was mapped: the fabric can't be shown to hold our app."""


def parse(text):
    """The one active row's name, or None when every row is inactive. Anything that
    isn't this table, exactly, raises."""
    rows, headers = [], 0
    for line in text.splitlines():
        t = line.split()
        if not t:
            continue
        if t == HEADER:
            headers += 1
            continue
        if len(t) != len(HEADER) or not SLOTS.fullmatch(t[5]) or not (t[6] == "-1" or ACTIVE.fullmatch(t[6])):
            raise NoActiveApp(f"`xmutil listapps` printed a line that isn't a row of its table: {line.strip()!r}. {STOP}")
        rows.append(t)
    if headers != 1 or not rows:
        raise NoActiveApp(f"`xmutil listapps` didn't print its table ({headers} headers, {len(rows)} rows). {STOP}")
    active = [t[0] for t in rows if t[6] != "-1"]
    if len(active) > 1:
        raise NoActiveApp(f"`xmutil listapps` shows {len(active)} active rows, {active}: one slot holds one. {STOP}")
    return active[0] if active else None


def listapps():
    try:
        r = subprocess.run(["xmutil", "listapps"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise NoActiveApp(f"`xmutil listapps` didn't run ({e}). {STOP}")
    if r.returncode:
        raise NoActiveApp(f"`xmutil listapps` failed (status {r.returncode}): {r.stderr.strip()}. {STOP}")
    return r.stdout


def text_of(path):
    """A file's text without NULs and outer whitespace, or None if it can't be read."""
    try:
        return pathlib.Path(path).read_bytes().replace(b"\0", b"").decode(errors="replace").strip()
    except OSError:
        return None


def check(read=None, state=None, firmware=None, firmware_dir=None):
    """The active app's name, one of KNOWN, or NoActiveApp. Touches no fabric address."""
    read, state, firmware = read or listapps, state or FPGA_STATE, firmware or FIRMWARE_NAME
    firmware_dir = pathlib.Path(firmware_dir or FIRMWARE_DIR)
    app = parse(read())
    if app is None:
        raise NoActiveApp(f"`xmutil listapps` shows no active app. {SEQUENCE}. {STOP}")
    if app not in KNOWN:
        raise NoActiveApp(f"the active app is {app!r}, not one of ours {list(KNOWN)}, so these addresses aren't "
                          f"what the tools expect. {SEQUENCE}. {STOP}")
    f = text_of(firmware)
    if f != app + ".bit.bin":
        raise NoActiveApp(f"{firmware} is {'missing' if f is None else repr(f)}, not {app}.bit.bin: the device tree "
                          f"doesn't show our overlay. {STOP}")
    s = text_of(state)
    if s != "operating":
        raise NoActiveApp(f"{state} is {'missing' if s is None else repr(s)}, not 'operating'. {STOP}")
    if not (firmware_dir / app / f"{app}.bit.bin").is_file():
        raise NoActiveApp(f"{firmware_dir / app / (app + '.bit.bin')} is missing: {app} isn't installed. {STOP}")
    return app


_checked = None


def require_app(**kw):
    """Every opener's first line. Once per process: a script doesn't load apps."""
    global _checked
    if _checked is None:
        _checked = check(**kw)
    return _checked
