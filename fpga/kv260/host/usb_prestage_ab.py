#!/usr/bin/env python3
"""Board A/B for SET_CONFIGURATION's status stage (branch usb-config-prestage,
backlog item F): single attaches of usb_fs_data_demo alternating between two
checkouts, A without the pre-stage (the baseline, e.g. improve-2026-11 at
fe3c7ab) and B with it (this branch: usb_fs_dev answers SET_CONFIGURATION's
status IN itself), counting the attaches that stall at SET_CONFIGURATION in
each. For the orchestrator; run as root on the Kria with protoemu48 the one
active app, each checkout copied whole (tools, firmware, fpga/kv260/host):

    sudo python3 B/fpga/kv260/host/usb_prestage_ab.py --a A --b B [--pairs 300] [--out DIR]

and on the cycle model, anywhere (the demo's --model path; the model host
always sends the status IN, so it never stalls: this proves the control flow):

    python3 fpga/kv260/host/usb_prestage_ab.py --model --a DIR --b DIR --pairs 1

--plan prints how many pairs give what power (no board, no model).

Each arm runs its own checkout's usb_fs_data_demo.py (`--sizes 8 --rounds 1
--attaches 1 --timeout 7`: one attach, no retry), with the capture, the
signal handling and the no-device stop of usb_guard_run.py (one process at a
time; a SIGINT, SIGTERM or SIGHUP stops the running demo, which detaches, and
then the run; summary.txt marked STOPPED). Order ABBA ABBA ..., so a linear drift
over the run falls on both arms alike. It refuses unless A's usb_fs_dev has
no `cfg_zlp` and B's has. An attach is "stall" when the demo was not
configured and the last request its software saw was SET_CONFIGURATION
(00 09 01 00 00 00 00 00), "ok" when the demo passed, and "other" otherwise.

Exit status 0 = every attach ran (the verdict is in the summary), 1 = some
attach failed other than by the stall, 3 = no device seen, 128 + the signal
when stopped by one.
"""
import argparse
import ast
import hashlib
import math
import os
import pathlib
import re
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
import usb_guard_run as g                   # noqa: E402

SET_CONFIG = "0009010000000000"
DEMO_ARGS = ["--sizes", "8", "--rounds", "1", "--attaches", "1", "--timeout", "7"]
BASE_RATE = 0.03                            # 14 of 436 attaches (2026-10-30 and -11-03), docs/usb-full-speed.md


def arm_identity(tree):
    """(words, pre-stage present, sha256 lines) of a checkout's usb_fs_dev, assembled with this checkout's
    assembler and defines; words None if it has no usb_fs_dev.pasm."""
    sys.path.insert(0, str(ROOT / "tools"))
    from pe import asm, usbdev
    fw = tree / g.IDENTIFIED[0]
    lines = []
    for f in g.IDENTIFIED:
        p = tree / f
        lines.append(f"  {hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else 'missing':64s}  {f}")
    if not fw.exists():
        return None, False, lines
    prog = asm.assemble(fw.read_text(), usbdev.defines())
    return len(prog.words), "cfg_zlp" in prog.labels, lines


def classify(rc, text):
    """"ok", "stall" (not configured, SET_CONFIGURATION the last request seen) or "other"."""
    if rc == 0:
        return "ok"
    m = re.search(r"not configured[^\n]*?requests seen: (\[[^\]]*\])", text)
    if m:
        seen = ast.literal_eval(m.group(1))
        if seen and seen[-1] == SET_CONFIG:
            return "stall"
    return "other"


def order(pairs):
    """ABBA ABBA ...: arm names for 2 * pairs attaches."""
    out = []
    for i in range(pairs):
        out += ["A", "B"] if i % 2 == 0 else ["B", "A"]
    return out


def fisher_less(stalls_a, n_a, stalls_b, n_b):
    """One-sided Fisher exact p: the chance, with both arms at one rate, that B
    has stalls_b or fewer of the stalls_a + stalls_b."""
    k, total = stalls_a + stalls_b, n_a + n_b
    return sum(math.comb(n_b, x) * math.comb(n_a, k - x) for x in range(stalls_b + 1) if k - x <= n_a) \
        / math.comb(total, k)


def power(n, p_a, p_b, alpha=0.05):
    """The chance that n attaches per arm give fisher_less < alpha, A stalling at p_a and B at p_b."""
    def pmf(p, k):
        return math.comb(n, k) * p ** k * (1 - p) ** (n - k)
    top_a = min(n, int(n * p_a + 10 * math.sqrt(n * p_a + 1)) + 10)
    top_b = min(n, int(n * p_b + 10 * math.sqrt(n * p_b + 1)) + 10) if p_b else 0
    return sum(pmf(p_a, a) * pmf(p_b, b) for a in range(top_a + 1) for b in range(top_b + 1)
               if fisher_less(a, n, b, n) < alpha)


def plan():
    lines = [f"[plan] one-sided Fisher exact test at 0.05; A at {BASE_RATE:.1%}"]
    for n in (100, 150, 200, 300, 400):
        lines.append(f"  {n} pairs ({2 * n} attaches): power {power(n, BASE_RATE, 0):.2f} if B never stalls, "
                     f"{power(n, BASE_RATE, BASE_RATE / 3):.2f} if B stalls at a third of A's rate; "
                     f"B with 0 stalls in {n}: {(1 - BASE_RATE) ** n:.4f} at A's rate")
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--a", type=pathlib.Path, help="checkout without the pre-stage (the baseline)")
    ap.add_argument("--b", type=pathlib.Path, default=ROOT, help="checkout with it (default: this one)")
    ap.add_argument("--pairs", type=int, default=300, help="attaches per arm (default 300: see --plan)")
    ap.add_argument("--out", type=pathlib.Path, help="log folder (default ~/usbab/run-<UTC>)")
    ap.add_argument("--model", action="store_true", help="the cycle model, no board")
    ap.add_argument("--plan", action="store_true", help="print the sample-size table and exit")
    ap.add_argument("--give-up", type=int, default=5,
                    help="stop if the first this-many attaches all see no request and no kernel line for --port")
    ap.add_argument("--port", default="1-1.4", help="the hub port the device sits on (kernel name)")
    ap.add_argument("--model-no-device", action="store_true",
                    help="with --model: no device on the bus (tests the --give-up stop)")
    ap.add_argument("--trace-root", type=pathlib.Path,
                    help="with --model: capture with this fake tracefs (tests; modprobe and journalctl skipped)")
    a = ap.parse_args(argv)
    if a.plan:
        print("\n".join(plan()))
        return 0
    if a.a is None:
        ap.error("--a (the checkout without the pre-stage) is required")
    if a.trace_root and not a.model:
        ap.error("--trace-root is for a model run against a fake tracefs")
    if not a.model:                    # refuse before anything is written or opened
        if os.geteuid() != 0:
            sys.exit("run as root (sudo): the demo maps the fabric and the capture writes /sys/kernel/tracing")
        import fabric_guard
        try:
            fabric_guard.require_app("protoemu48")
        except fabric_guard.NoActiveApp as e:
            sys.exit(f"STOP: {e}")
    trees = {"A": a.a.resolve(), "B": a.b.resolve()}
    idents = {k: arm_identity(t) for k, t in trees.items()}
    for k, want in (("A", False), ("B", True)):
        words, pre, _ = idents[k]
        if words is None or pre != want:
            sys.exit(f"STOP: arm {k} ({trees[k]}): usb_fs_dev "
                     f"{'missing' if words is None else ('has' if pre else 'has no') + ' cfg_zlp'}; "
                     f"A must be a checkout without the pre-stage and B one with it")
    out = a.out or pathlib.Path.home() / "usbab" / time.strftime("run-%Y%m%dT%H%M%SZ", time.gmtime())
    out.mkdir(parents=True, exist_ok=True)
    lines = []

    def say(s):
        print(s, flush=True)
        lines.append(s)

    say(f"[run] {'cycle model' if a.model else 'KV260, protoemu48'}; {a.pairs} attaches per arm, order ABBA; "
        f"logs in {out}")
    for k in "AB":
        words, pre, sums = idents[k]
        say(f"[arm {k}] {trees[k]}: usb_fs_dev {words} words, pre-stage {'present' if pre else 'absent'}")
        for line in sums:
            say(line)
    fake_run = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout=(a.trace_root / "journal.txt").read_text()
                                                             if (a.trace_root / "journal.txt").exists() else "")
    tracer = None
    stopper = g.Stopper()
    extra = ["--model-no-device"] if a.model_no_device else []
    if not a.model:
        for line in g.port_report(a.port):
            say(line)
        tracer = g.Tracer(out)
    elif a.trace_root:
        for line in g.port_report(a.port, run=fake_run, sysfs=a.trace_root / "usb-devices"):
            say(line)
        tracer = g.Tracer(out, trace=a.trace_root, usbmon=a.trace_root / "usbmon-1u", run=fake_run)
    results = {"A": [], "B": []}           # (attach number, outcome, kernel -110 seen)
    empty, stop, ran = 0, None, 0
    t0 = time.time()
    try:
        for i, arm in enumerate(order(a.pairs), 1):
            if stopper.signal is not None:
                break
            if tracer:
                tracer.start(i)
            rc, text = g.demo(DEMO_ARGS, out / f"demo-{i}-{arm}.log", a.model, 600 if a.model else 120, stopper,
                              extra, script=trees[arm] / "fpga/kv260/host/usb_fs_data_demo.py")
            if tracer:
                tracer.stop(i, rc != 0)
            ran = i
            if stopper.signal is not None:
                stop = f"[stopped] by {stopper.signal.name} during attach {i} (arm {arm}; the demo stopped: rc {rc})"
                break
            kernel = (out / f"dmesg-{i}.txt").read_text() if (out / f"dmesg-{i}.txt").exists() else ""
            outcome = classify(rc, text)
            results[arm].append((i, outcome, "can't set config #1, error -110" in kernel))
            print(f"run {i} arm {arm} {outcome}{'' if rc == 0 else f' rc={rc}'}", flush=True)
            if i <= a.give_up and g.nothing_seen(text, kernel, a.port):
                empty += 1
                if empty == a.give_up:
                    stop = f"no device seen on the hub port in {a.give_up} attaches: check the USB wiring"
                    break
    finally:
        problems = []
        if tracer:
            try:
                problems = tracer.close()
            except Exception as e:          # never lose the summary to it
                problems = [f"close() failed: {e!r}"]
    for p in problems:
        say(f"[tracing] {p}")
    if stopper.signal is not None and stop is None:
        stop = f"[stopped] by {stopper.signal.name} before attach {ran + 1}"
    counts = {}
    for k in "AB":
        rs = results[k]
        by = {o: [i for i, oo, _ in rs if oo == o] for o in ("ok", "stall", "other")}
        counts[k] = (len(rs), len(by["stall"]))
        say(f"[arm {k}] {len(rs)} attaches: {len(by['ok'])} ok, {len(by['stall'])} stalled at SET_CONFIGURATION"
            f"{' (runs ' + ' '.join(map(str, by['stall'])) + ')' if by['stall'] else ''}, "
            f"{len(by['other'])} other failures{' (runs ' + ' '.join(map(str, by['other'])) + ')' if by['other'] else ''}"
            f"; kernel -110 on {sum(1 for r in rs if r[2])}")
    (n_a, s_a), (n_b, s_b) = counts["A"], counts["B"]
    if n_a and n_b:
        say(f"[verdict] stall rate A {s_a}/{n_a} = {s_a / n_a:.1%}, B {s_b}/{n_b} = {s_b / n_b:.1%}; "
            f"one-sided Fisher p (B lower than A) = {fisher_less(s_a, n_a, s_b, n_b):.4f}; "
            f"B against the earlier {BASE_RATE:.0%}: P(<= {s_b} of {n_b}) = "
            f"{sum(math.comb(n_b, x) * BASE_RATE ** x * (1 - BASE_RATE) ** (n_b - x) for x in range(s_b + 1)):.4f}"
            f" ({time.time() - t0:.0f} s)")
    if stop is not None:
        say(stop)
        signalled = stopper.signal is not None
        say("STOPPED" if signalled else "NO DEVICE")
        (out / "summary.txt").write_text("\n".join(lines) + "\n")
        return 128 + stopper.signal.value if signalled else 3
    other = any(o == "other" for k in "AB" for _, o, _ in results[k])
    say("DONE, with other failures" if other else "DONE")
    (out / "summary.txt").write_text("\n".join(lines) + "\n")
    return int(other)


if __name__ == "__main__":
    sys.exit(main())
