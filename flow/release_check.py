#!/usr/bin/env python3
"""Release check: stricter than flow/signoff.py, for choosing what to submit.

flow/signoff.py applies Tiny Tapeout's criteria (DRC/LVS/antenna clean, setup
at the typical corner, hold at every corner, the right SRAM view per corner)
and only reports the rest. This adds what a release candidate must also show:

* setup met at every corner at the clock constraint, or at the slow corner at
  a stated lower frequency (--slow-mhz 48: the slow corner's slack is
  recomputed for that period);
* no max-slew, max-capacitance or max-fanout violation at any corner, except
  ones waived explicitly (--waive fanout=1: up to one fanout violation per
  corner, with the reason recorded in the release notes, not here);
* gate-level receipts: every functional gate-level suite in
  <run worktree>/test/sim_build/gl.*/ (flow/gl_test.sh) ran on this run's
  netlist and passed, and optionally SDF and precheck logs given with
  --sdf-log / --precheck-log reported a pass.

    python3 flow/release_check.py <run worktree> [--slow-mhz 48] [--waive fanout=1]
        [--sdf-log FILE] [--precheck-log FILE]       # exit 1 if it fails
"""
import argparse
import glob
import hashlib
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import signoff  # noqa: E402

PERIOD_NS = 20.0
ELECTRICAL = {"slew": "design__max_slew_violation__count",
              "cap": "design__max_cap_violation__count",
              "fanout": "design__max_fanout_violation__count"}


def sha16(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()[:16]


def gl_receipts(worktree, netlist):
    """(ok, lines): every gl.* directory's suites passed on this netlist."""
    dirs = sorted(glob.glob(os.path.join(worktree, "test", "sim_build", "gl.*")))
    if not dirs:
        return False, ["no gate-level results (run flow/gl_test.sh on this run)"]
    want = sha16(netlist)
    lines, ok_any = [], False
    for d in dirs:
        nl = os.path.join(d, "netlist.v")
        xmls = sorted(glob.glob(os.path.join(d, "*.xml")))
        if not os.path.exists(nl) or sha16(nl) != want or not xmls:
            lines.append(f"{os.path.basename(d)}: other netlist or incomplete, ignored")
            continue
        total = bad = 0
        for x in xmls:
            for case in ET.parse(x).getroot().iter("testcase"):
                total += 1
                bad += any(c.tag in ("failure", "error", "skipped") for c in case)
        lines.append(f"{os.path.basename(d)}: {total - bad}/{total} passed on netlist {want}")
        ok_any |= total > 0 and bad == 0
    return ok_any, lines


def log_passed(path, pattern):
    return os.path.exists(path) and re.search(pattern, open(path, errors="replace").read()) is not None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("worktree")
    ap.add_argument("--slow-mhz", type=float, default=None,
                    help="judge the slow corner at this frequency instead of the constraint")
    ap.add_argument("--waive", action="append", default=[], metavar="KIND=N",
                    help="allow up to N violations of KIND (slew, cap, fanout) per corner")
    ap.add_argument("--sdf-log")
    ap.add_argument("--precheck-log")
    a = ap.parse_args(argv)
    run = os.path.join(a.worktree, "runs", "wokwi")
    failures = []
    print("== flow/signoff.py (Tiny Tapeout criteria)")
    if signoff.main(run) != 0:
        failures.append("flow/signoff.py fails")
    m = json.load(open(os.path.join(run, "final", "metrics.json")))
    waive = {}
    for w in a.waive:
        kind, n = w.split("=")
        if kind not in ELECTRICAL:
            ap.error(f"--waive kind must be one of {sorted(ELECTRICAL)}")
        waive[kind] = int(n)
    print("== release criteria")
    for c in signoff.CORNERS:
        ws = m.get(f"timing__setup__ws__corner:{c}")
        slow = c.startswith("nom_slow") and a.slow_mhz
        target = 1000.0 / a.slow_mhz if slow else PERIOD_NS
        slack = None if ws is None else ws + (target - PERIOD_NS)
        print(f"{c:22s} setup slack {slack:+.3f} ns at {1000 / target:.2f} MHz" if slack is not None
              else f"{c:22s} setup slack missing")
        if slack is None or slack < 0:
            failures.append(f"{c}: setup slack {slack} at {1000 / target:.2f} MHz")
        for kind, key in ELECTRICAL.items():
            n = m.get(f"{key}__corner:{c}")
            if n is None or n > waive.get(kind, 0):
                failures.append(f"{c}: {n} max-{kind} violations" + (f" (waived up to {waive[kind]})" if kind in waive else ""))
        print(f"{'':22s} slew/cap/fanout violations "
              + "/".join(str(m.get(f'{k}__corner:{c}')) for k in ELECTRICAL.values()))
    netlist = glob.glob(os.path.join(run, "final", "nl", "*.nl.v"))[0]
    ok, lines = gl_receipts(a.worktree, netlist)
    for line in lines:
        print("GL " + line)
    if not ok:
        failures.append("no passing gate-level receipt for this netlist")
    for name, path, pattern in (("SDF gate level", a.sdf_log, r"all cases as required"),
                                ("precheck", a.precheck_log, r"Precheck passed")):
        if path is None:
            print(f"{name}: no log given")
            failures.append(f"{name} log not given")
        elif log_passed(path, pattern):
            print(f"{name}: passed ({path})")
        else:
            failures.append(f"{name}: {path} doesn't report a pass")
    if waive:
        print("waived: " + ", ".join(f"{k} up to {n} per corner" for k, n in waive.items()))
    print("RELEASE " + ("FAILED: " + "; ".join(failures) if failures else "PASSED"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
