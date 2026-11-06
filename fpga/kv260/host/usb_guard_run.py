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
step passed, 1 = a step failed, 3 = no device seen (below), 128 + the signal
when stopped by one.

Stopping: a SIGINT (Ctrl-C), SIGTERM or SIGHUP ends it at once. It passes a
SIGTERM to the running demo, which kills its Linux client and detaches as at
a normal end (core stopped, the pulled-up line held low), then it puts the
tracing state back and writes summary.txt, marked STOPPED. A second signal
does nothing more. If the first --give-up attaches (5) all see no request
and the kernel logs nothing for --port, it stops: "no device seen on the hub
port in N attaches: check the USB wiring" (NO DEVICE), summary.txt written.
"""
import argparse
import contextlib
import hashlib
import os
import pathlib
import re
import signal
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
        run(["modprobe", "usbmon"], check=True, start_new_session=True)
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
        self.mon = self.popen(["timeout", "20", "cat", str(self.usbmon)], stdout=open(self.out / f"mon-{i}.txt", "w"),
                              start_new_session=True)
        time.sleep(0.2)

    def stop(self, i, failed):
        (self.trace / "tracing_on").write_text("0")
        self.mon.terminate()
        self.mon.wait()
        if failed:
            (self.out / f"trace-{i}.txt").write_text((self.trace / "trace").read_text())
            log = self.run(["journalctl", "-k", "--since", "-40s", "--no-pager"], capture_output=True, text=True,
                           start_new_session=True)
            (self.out / f"dmesg-{i}.txt").write_text(log.stdout)
        else:
            (self.out / f"mon-{i}.txt").unlink(missing_ok=True)

    def close(self):
        """Back to what it found, as far as the kernel lets it: the events
        enabled (set_event takes one per write; a write that truncates clears
        the rest), the xHCI enable, the buffer size and tracing on or off. Each
        write is tried on its own and never raises; returns what it could not
        put back. buffer_size_kb reads "N (expanded: M)" before the ring buffer
        was first expanded and "X" when the per-CPU sizes differ (Linux 6.8,
        tracing_entries_read), and takes only an integer (kstrtoul): those two
        forms are left, and said so."""
        problems = []

        def put(name, text, mode="w"):
            try:
                with open(self.trace / name, mode) as f:
                    f.write(text)
            except OSError as e:
                problems.append(f"{name}: could not write {text.strip()!r} back ({e})")

        put("set_event", "")
        for e in self.saved_events:
            put("set_event", e + "\n", "a")
        if self.saved["events/xhci-hcd/enable"].strip() in ("0", "1"):   # "X" (some): set_event did it
            put("events/xhci-hcd/enable", self.saved["events/xhci-hcd/enable"])
        size = self.saved["buffer_size_kb"].strip()
        if size.isdigit():
            put("buffer_size_kb", size + "\n")
        else:
            problems.append(f"buffer_size_kb: left at 16384, it read {size!r}, which it does not take back")
        put("tracing_on", self.saved["tracing_on"])
        return problems


class Stopper:
    """SIGINT, SIGTERM and SIGHUP set `signal` (the first one only) and pass a
    SIGTERM to the demo running then, which stops itself cleanly. Installed
    explicitly: started in the background from a non-interactive shell, a
    process begins with SIGINT ignored, and Python leaves it so (the
    2026-11-04 board run went on to its next attach after a SIGINT)."""

    SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)

    def __init__(self):
        self.signal = None
        self.child = None
        for s in self.SIGNALS:
            signal.signal(s, self.handler)

    def handler(self, signum, frame):
        if self.signal is None:
            self.signal = signal.Signals(signum)
            if self.child is not None and self.child.poll() is None:
                self.child.send_signal(signal.SIGTERM)

    @contextlib.contextmanager
    def held(self):
        """The signals held off around a wait on the demo (one thread): run
        between Popen's reaping the demo and its noting the exit status, the
        handler's poll() would still say running and the SIGTERM would go to
        a reaped pid, or a reused one (verifier 054). A signal meanwhile is
        handled as the wait ends."""
        try:
            signal.pthread_sigmask(signal.SIG_BLOCK, self.SIGNALS)
            yield
        finally:
            signal.pthread_sigmask(signal.SIG_UNBLOCK, self.SIGNALS)


def demo(args, log, model, timeout, stopper, extra=(), script=None):
    """One usb_fs_data_demo run, its output in `log`. Returns (rc, text). A
    stop asked meanwhile reaches it as a SIGTERM; it gets 60 s to detach, then
    a SIGKILL. rc 124: it outlasted `timeout`. script: another checkout's
    usb_fs_data_demo.py (usb_prestage_ab.py), default this one's."""
    cmd = [sys.executable, str(script or DEMO)] + args + (["--model"] if model else []) + list(extra)
    with open(log, "w") as f:
        # its own session: a terminal Ctrl-C or an ssh hangup reaches only this
        # process, which passes a SIGTERM on (verifier 048)
        proc = subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT, start_new_session=True)
    stopper.child = proc
    t0, told = time.monotonic(), None
    if stopper.signal is not None:       # a stop that came as it started
        proc.send_signal(signal.SIGTERM)
        told = t0
    while True:
        try:
            with stopper.held():
                rc = proc.wait(timeout=0.2)
            break
        except subprocess.TimeoutExpired:
            now = time.monotonic()
            if stopper.signal is not None and told is None:
                told = now               # the handler sent it a SIGTERM already
            if (told is not None and now - told > 60) or now - t0 > timeout:
                with stopper.held():
                    proc.kill()
                    rc = proc.wait()
                if told is None:         # outlasted its timeout
                    rc = 124
                break
    stopper.child = None
    return rc, pathlib.Path(log).read_text()


def nothing_seen(text, kernel, port):
    """An attach that saw no request at all and no kernel line for the port."""
    return "not configured" in text and "requests seen: []" in text and f"usb {port}:" not in kernel


def port_report(port, run=subprocess.run, sysfs=pathlib.Path("/sys/bus/usb/devices")):
    """Read only: where this boot's kernel log last saw our device (1209:5634),
    and the hub port's state in sysfs (port "1-1.4": 1-1:1.0/1-1-port4/state)."""
    lines = []
    out = run(["journalctl", "-k", "-b", "--no-pager"], capture_output=True, text=True, start_new_session=True).stdout or ""
    seen = [m.group(1) for m in re.finditer(r"usb (\d+-[\d.]+): New USB device found, idVendor=1209, idProduct=5634", out)]
    lines.append(f"[port] device last seen on usb {seen[-1]} (kernel log, this boot)" if seen
                 else "[port] device not in this boot's kernel log")
    hub, _, num = port.rpartition(".")
    state = sysfs / f"{hub}:1.0" / f"{hub}-port{num}" / "state"
    try:
        lines.append(f"[port] {state.parent.name} state: {state.read_text().strip()}")
    except OSError as e:
        lines.append(f"[port] {state} not readable ({e.strerror})")
    return lines


def nodata_summary(text):
    m = re.search(r"NODATA rt (0x\w+) rq (0x\w+) wLength 0: (\d+) sent: (\d+) ok, (\d+) timed out, other (\{.*?\})"
                  r"(?:; latency ms min ([\d.]+) median ([\d.]+) max ([\d.]+))?", text)
    if not m:
        return None
    rt, rq, n, ok, to, other, lo, med, hi = m.groups()
    lat = f", median {med} ms, max {hi} ms" if med else ""
    return f"rt {rt} rq {rq}: {ok} of {n} ok, {to} timed out, other {other}{lat}"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--attaches", type=int, default=200)
    ap.add_argument("--writes", type=int, default=1000, help="zero-length vendor writes (0x40 0x10)")
    ap.add_argument("--reads", default="10,200", help="zero-length vendor reads (0xC0 0x11), runs of these counts")
    ap.add_argument("--out", type=pathlib.Path, help="log folder (default ~/usbguard/run-<UTC>)")
    ap.add_argument("--model", action="store_true", help="the cycle model, no board")
    ap.add_argument("--give-up", type=int, default=5,
                    help="stop if the first this-many attaches all see no request and no kernel line for --port")
    ap.add_argument("--port", default="1-1.4", help="the hub port the device sits on (kernel name)")
    ap.add_argument("--model-no-device", action="store_true",
                    help="with --model: no device on the bus (tests the --give-up stop)")
    ap.add_argument("--trace-root", type=pathlib.Path,
                    help="with --model: capture with this fake tracefs (test_usb_guard_run; modprobe and journalctl skipped)")
    a = ap.parse_args(argv)
    if a.trace_root and not a.model:
        ap.error("--trace-root is for a model run against a fake tracefs")
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
    stopper = Stopper()
    extra = ["--model-no-device"] if a.model_no_device else []
    say(f"[run] {'cycle model' if a.model else 'KV260, protoemu48'}; logs in {out}")
    if not identity(say):
        sys.exit("STOP: firmware/usb_fs_dev.pasm has no got_in guard: this is not the usb-guard checkout")
    fake_run = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout=(a.trace_root / "journal.txt").read_text()
                                                             if (a.trace_root / "journal.txt").exists() else "")
    if not a.model:
        for line in port_report(a.port):
            say(line)
        tracer = Tracer(out)
    elif a.trace_root:
        for line in port_report(a.port, run=fake_run, sysfs=a.trace_root / "usb-devices"):
            say(line)
        tracer = Tracer(out, trace=a.trace_root, usbmon=a.trace_root / "usbmon-1u", run=fake_run)
    fails, empty, stop, ran = [], 0, None, 0
    t0 = time.time()
    try:
        for i in range(1, a.attaches + 1):
            if stopper.signal is not None:
                break
            if tracer:
                tracer.start(i)
            rc, text = demo(["--sizes", "8", "--rounds", "1", "--attaches", "1", "--timeout", "7"],
                            out / f"demo-{i}.log", a.model, 600 if a.model else 120, stopper, extra)
            if tracer:
                tracer.stop(i, rc != 0)
            ran = i
            print(f"run {i} {'ok' if rc == 0 else f'FAIL rc={rc}'}", flush=True)
            if rc:
                fails.append(i)
            if stopper.signal is not None:
                stop = f"[stopped] by {stopper.signal.name} during attach {i} (the demo stopped: rc {rc})"
                break
            kernel = (out / f"dmesg-{i}.txt").read_text() if (out / f"dmesg-{i}.txt").exists() else ""
            if i <= a.give_up and nothing_seen(text, kernel, a.port):
                empty += 1
                if empty == a.give_up:
                    stop = f"no device seen on the hub port in {a.give_up} attaches: check the USB wiring"
                    break
    finally:
        problems = []
        if tracer:
            try:
                problems = tracer.close()
            except Exception as e:          # never lose the remaining steps and the summary to it
                problems = [f"close() failed: {e!r}"]
    for p in problems:
        say(f"[tracing] {p}")
    if stopper.signal is not None and stop is None:
        stop = f"[stopped] by {stopper.signal.name} before attach {ran + 1}"
    if stop is not None:
        say(stop)
        say(f"[attaches] {ran} of {a.attaches} run, {len(fails)} not configured or failed"
            f"{': runs ' + ' '.join(map(str, fails)) if fails else ''}; the zero-length steps not run")
        signalled = stopper.signal is not None
        say("STOPPED" if signalled else "NO DEVICE")
        (out / "summary.txt").write_text("\n".join(lines) + "\n")
        return 128 + stopper.signal.value if signalled else 3
    say(f"[attaches] {a.attaches} runs, {len(fails)} not configured or failed{': runs ' + ' '.join(map(str, fails)) if fails else ''}"
        f" ({time.time() - t0:.0f} s)")
    status = int(bool(fails))
    steps = [("writes", a.writes, "0x40", "0x10")] + [("reads", int(n), "0xC0", "0x11") for n in a.reads.split(",") if n]
    for k, (what, n, rt, rq) in enumerate(steps):
        if not n:
            continue
        rc, text = demo(["--sizes", "8", "--rounds", "1", "--attaches", "3", "--nodata", str(n), "--rt", rt, "--rq", rq],
                        out / f"nodata-{k}-{what}-{n}.log", a.model, 3600, stopper, extra)
        summary = nodata_summary(text)
        say(f"[zero-length {what}] {summary or 'no NODATA line (not configured?)'}; demo rc {rc}")
        status |= int(rc != 0 or summary is None)
        if stopper.signal is not None:
            say(f"[stopped] by {stopper.signal.name} during the zero-length {what} (the demo stopped: rc {rc});"
                " the steps after it not run")
            say("STOPPED")
            (out / "summary.txt").write_text("\n".join(lines) + "\n")
            return 128 + stopper.signal.value
    say("PASS" if status == 0 else "FAIL")
    (out / "summary.txt").write_text("\n".join(lines) + "\n")
    return status


if __name__ == "__main__":
    sys.exit(main())
