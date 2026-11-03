"""The bus-reset sweeps of docs/fault-testing.md (finding 6), too long for the
suite: SE0 on usb_enum's inputs from every STEP-th clock of a control read, with
test_faults.py's helpers. Also the clock-for-clock turnaround comparison
against another usb_enum source (the firmware before fault-fixes).

    cd test/model && PYTHONPATH=../../tools python usb_reset_sweep.py full 3000 1
    PYTHONPATH=../../tools python usb_reset_sweep.py full 480000 37
    PYTHONPATH=../../tools python usb_reset_sweep.py low 20000 4
    PYTHONPATH=../../tools python usb_reset_sweep.py full 3000 1 --firmware OLD.pasm
    PYTHONPATH=../../tools python usb_reset_sweep.py turnarounds OLD.pasm

(run with a pytest installed, as the suite is; -j sets the processes)."""
import argparse
import multiprocessing as mp

import test_faults as T
from pe import usb, usbdev, usbenum
from pe.modelhost import ModelHost
from pe.usbfs import FsControlHost
from pe.usbhost import ControlHost


def one(args):
    speed, off, cycles, text = args
    f = T.usb_reset_anywhere if speed == "full" else T.usb_ls_reset_anywhere
    return (off,) + f(off, cycles, text)


def sweep(speed, cycles, step, text, jobs):
    end = {"full": 4400, "low": 36000}[speed]          # the whole control read and the idle after it
    with mp.Pool(jobs) as p:
        res = p.map(one, [(speed, o, cycles, text) for o in range(0, end, step)])
    bad = [r for r in res if r[1] is None or r[2] != 0 or not r[3] or r[4]]
    lat = [r[1] for r in res if r[1] is not None]
    print(f"{speed} speed, SE0 of {cycles} clocks from every {step}th clock of 0..{end}: "
          f"{len(res)} starts, {len(bad)} where the reset was missed or did not recover; "
          f"SE0 start to `reset` at most {max(lat) if lat else None} clocks")
    runs = []
    for r in bad:
        if runs and r[0] - runs[-1][1] <= step:
            runs[-1][1] = r[0]
        else:
            runs.append([r[0], r[0]])
    for a, b in runs:
        print(f"  missed from {a} to {b}")


def session(speed, text, seed=1, jitter=0.0, ppm=0.0, lost=False):
    mh = ModelHost()
    mh.apply(usbenum.load_writes(usbenum.assemble(speed, text), speed))
    mh.pull_up = 0b10
    mh.step(100)
    if speed == "full":
        h = FsControlHost(mh, seed=seed, jitter_ns=jitter, ppm=ppm, ipd_bits=2.0, ack_bits=2.0)
    else:
        h = ControlHost(mh, bit=32)
    if lost:
        h.faults["lose_host_ack"] = 1
        h.control_write(0, bytes([0x00, 5, 77, 0, 0, 0, 0, 0]))
        h._send(usb.token(usb.PID_IN, 77, 0))
        h._response()
    else:
        assert h.get_descriptor(0, 1, 18) == usbdev.DEVICE_DESC
        h.control_write(0, bytes([0x00, 5, 9, 0, 0, 0, 0, 0]))
        assert h.get_descriptor(9, 2, 18) == usbdev.CONFIG_DESC
        h.control_write(9, bytes([0x00, 9, 1, 0, 0, 0, 0, 0]))
    # in ns at full speed (continuous host time: rounded below float noise), bit times at low speed
    turn = [round(t[1], 6) for t in h.fs.turnarounds] if speed == "full" else h.turnarounds
    return turn, h.wire


def turnarounds(old):
    for args in [("full", None, 1, 0, 0, False), ("full", None, 2, 9.25, 2500, False),
                 ("full", None, 3, 5, -2500, True), ("full", None, 9, 9.25, 2500, True), ("low", None)]:
        a = session(args[0], old, *args[2:])
        b = session(*args)
        print(f"{args[0]} speed {args[2:]}: {len(a[0])} answers; turnarounds equal {a[0] == b[0]}, "
              f"packet bytes equal {a[1] == b[1]}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["full", "low", "turnarounds"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--firmware", help="another usb_enum source to sweep")
    ap.add_argument("-j", type=int, default=12)
    a = ap.parse_args()
    if a.mode == "turnarounds":
        turnarounds(open(a.args[0]).read())
    else:
        sweep(a.mode, int(a.args[0]), int(a.args[1]), open(a.firmware).read() if a.firmware else None, a.j)
