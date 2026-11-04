#!/usr/bin/env python3
"""The board run for the got_in guard (branch usb-guard): N single-attach runs
of usb_fs_data_demo (the SET_CONFIGURATION stall), then zero-length vendor
writes, then zero-length reads (10, then 200), with the firmware and software
identified by sha256 and word count first. For the orchestrator; run as root on
the Kria from a checkout of this branch, with protoemu48 the one active app:

    sudo python3 fpga/kv260/host/usb_guard_run.py [--attaches 200] [--writes 1000] [--reads 10,200] [--out DIR]

and on the cycle model, anywhere (the same steps, the demo's --model path in
place of the board and Linux; no tracing):

    python3 fpga/kv260/host/usb_guard_run.py --model --attaches 3 --writes 20 --reads 2,5

On the board it refuses unless fabric_guard finds protoemu48 active (it maps
nothing itself), and like the orchestrator's hunt.sh it records xHCI trace
events and usbmon (bus 1) per attach, keeping the trace, usbmon text, kernel
log and demo log of every attach that fails. One process at a time: it runs
each demo to completion before the next. Every log goes in --out; the summary
lines (one per step) go to stdout and to summary.txt. Exit status 0 = every
step passed.
"""
import argparse
import hashlib
import os
import pathlib
import re
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
DEMO = HERE / "usb_fs_data_demo.py"
TRACE = pathlib.Path("/sys/kernel/tracing")
USBMON = pathlib.Path("/sys/kernel/debug/usb/usbmon/1u")
IDENTIFIED = ("firmware/usb_fs_dev.pasm", "tools/pe/usbdev.py", "tools/pe/usbfsdata.py",
              "fpga/kv260/host/usb_fs_data_demo.py", "fpga/kv260/host/usb_guard_run.py")


def identity(say):
    sys.path.insert(0, str(ROOT / "tools"))
    from pe import asm, usbdev
    words = len(asm.assemble((ROOT / IDENTIFIED[0]).read_text(), usbdev.defines()).words)
    guarded = "jmp txne, send_data" in (ROOT / IDENTIFIED[0]).read_text()
    say(f"[identity] usb_fs_dev {words} words, got_in guard {'present' if guarded else 'ABSENT'}")
    for f in IDENTIFIED:
        say(f"  {hashlib.sha256((ROOT / f).read_bytes()).hexdigest()}  {f}")
    return guarded


class Tracer:
    """hunt.sh's capture: xHCI trace events and usbmon text around each attach
    (board only). It saves the tracing state it finds (buffer size, tracing on
    or off, the enabled events) and puts it back on close(). trace, usbmon,
    run and popen are the kernel's paths and subprocess's, replaceable for a
    test."""

    def __init__(self, out, trace=TRACE, usbmon=USBMON, run=subprocess.run, popen=subprocess.Popen):
        self.out, self.trace, self.usbmon, self.popen = out, trace, usbmon, popen
        self.run = run
        run(["modprobe", "usbmon"], check=True)
        self.saved = {f: (trace / f).read_text() for f in ("buffer_size_kb", "tracing_on", "events/xhci-hcd/enable")}
        self.saved_events = (trace / "set_event").read_text().split()
        (trace / "buffer_size_kb").write_text("16384")
        (trace / "tracing_on").write_text("0")
        (trace / "set_event").write_text("")
        (trace / "events/xhci-hcd/enable").write_text("1")
        self.mon = None

    def start(self, i):
        (self.trace / "trace").write_text("")
        (self.trace / "tracing_on").write_text("1")
        self.mon = self.popen(["timeout", "20", "cat", str(self.usbmon)], stdout=open(self.out / f"mon-{i}.txt", "w"))
        time.sleep(0.2)

    def stop(self, i, failed):
        (self.trace / "tracing_on").write_text("0")
        self.mon.terminate()
        self.mon.wait()
        if failed:
            (self.out / f"trace-{i}.txt").write_text((self.trace / "trace").read_text())
            log = self.run(["journalctl", "-k", "--since", "-40s", "--no-pager"], capture_output=True, text=True)
            (self.out / f"dmesg-{i}.txt").write_text(log.stdout)
        else:
            (self.out / f"mon-{i}.txt").unlink(missing_ok=True)

    def close(self):
        """Back to what it found: the events enabled (set_event takes one per
        write; a write that truncates clears the rest), then the buffer size,
        the xHCI enable and tracing on or off."""
        (self.trace / "set_event").write_text("")
        for e in self.saved_events:
            with open(self.trace / "set_event", "a") as f:
                f.write(e + "\n")
        if self.saved["events/xhci-hcd/enable"].strip() in ("0", "1"):   # "X" (some): set_event did it
            (self.trace / "events/xhci-hcd/enable").write_text(self.saved["events/xhci-hcd/enable"])
        for f in ("buffer_size_kb", "tracing_on"):
            (self.trace / f).write_text(self.saved[f])


def demo(args, log, model, timeout):
    """One usb_fs_data_demo run, its output in `log`. Returns (rc, text)."""
    cmd = [sys.executable, str(DEMO)] + args + (["--model"] if model else [])
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=timeout)
        rc, text = p.returncode, p.stdout
    except subprocess.TimeoutExpired as e:
        rc, text = 124, (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
    log.write_text(text)
    return rc, text


def nodata_summary(text):
    m = re.search(r"NODATA rt (0x\w+) rq (0x\w+) wLength 0: (\d+) sent: (\d+) ok, (\d+) timed out, other (\{.*?\})"
                  r"(?:; latency ms min ([\d.]+) median ([\d.]+) max ([\d.]+))?", text)
    if not m:
        return None
    rt, rq, n, ok, to, other, lo, med, hi = m.groups()
    lat = f", median {med} ms, max {hi} ms" if med else ""
    return f"rt {rt} rq {rq}: {ok} of {n} ok, {to} timed out, other {other}{lat}"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--attaches", type=int, default=200)
    ap.add_argument("--writes", type=int, default=1000, help="zero-length vendor writes (0x40 0x10)")
    ap.add_argument("--reads", default="10,200", help="zero-length vendor reads (0xC0 0x11), runs of these counts")
    ap.add_argument("--out", type=pathlib.Path, help="log folder (default ~/usbguard/run-<UTC>)")
    ap.add_argument("--model", action="store_true", help="the cycle model, no board")
    a = ap.parse_args()
    if not a.model:                    # refuse before anything is written or opened
        if os.geteuid() != 0:
            sys.exit("run as root (sudo): the demo maps the fabric and the capture writes /sys/kernel/tracing")
        sys.path.insert(0, str(HERE))
        import fabric_guard
        try:
            fabric_guard.require_app("protoemu48")
        except fabric_guard.NoActiveApp as e:
            sys.exit(f"STOP: {e}")
    out = a.out or pathlib.Path.home() / "usbguard" / time.strftime("run-%Y%m%dT%H%M%SZ", time.gmtime())
    out.mkdir(parents=True, exist_ok=True)
    lines = []

    def say(s):
        print(s, flush=True)
        lines.append(s)

    tracer = None
    say(f"[run] {'cycle model' if a.model else 'KV260, protoemu48'}; logs in {out}")
    if not identity(say):
        sys.exit("STOP: firmware/usb_fs_dev.pasm has no got_in guard: this is not the usb-guard checkout")
    if not a.model:
        tracer = Tracer(out)
    fails = []
    t0 = time.time()
    try:
        for i in range(1, a.attaches + 1):
            if tracer:
                tracer.start(i)
            rc, _ = demo(["--sizes", "8", "--rounds", "1", "--attaches", "1", "--timeout", "7"],
                         out / f"demo-{i}.log", a.model, 600 if a.model else 120)
            if tracer:
                tracer.stop(i, rc != 0)
            print(f"run {i} {'ok' if rc == 0 else f'FAIL rc={rc}'}", flush=True)
            if rc:
                fails.append(i)
    finally:
        if tracer:
            tracer.close()
    say(f"[attaches] {a.attaches} runs, {len(fails)} not configured or failed{': runs ' + ' '.join(map(str, fails)) if fails else ''}"
        f" ({time.time() - t0:.0f} s)")
    status = int(bool(fails))
    steps = [("writes", a.writes, "0x40", "0x10")] + [("reads", int(n), "0xC0", "0x11") for n in a.reads.split(",") if n]
    for k, (what, n, rt, rq) in enumerate(steps):
        if not n:
            continue
        rc, text = demo(["--sizes", "8", "--rounds", "1", "--attaches", "3", "--nodata", str(n), "--rt", rt, "--rq", rq],
                        out / f"nodata-{k}-{what}-{n}.log", a.model, 3600)
        summary = nodata_summary(text)
        say(f"[zero-length {what}] {summary or 'no NODATA line (not configured?)'}; demo rc {rc}")
        status |= int(rc != 0 or summary is None)
    say("PASS" if status == 0 else "FAIL")
    (out / "summary.txt").write_text("\n".join(lines) + "\n")
    return status


if __name__ == "__main__":
    sys.exit(main())
