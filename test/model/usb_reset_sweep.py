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


def turnarounds(old):
    for s in T.USB_SESSIONS:
        a = T.usb_session(s[0], old, *s[1:])
        b = T.usb_session(s[0], None, *s[1:])
        print(f"{s[0]} speed {s[1:]}: {len(a[0])} answers; turnarounds equal {a[0] == b[0]}, "
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
