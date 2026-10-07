#!/usr/bin/env python3
"""Release gate using actual timing constraints and automatic verification receipts.

The minimum Tiny Tapeout check remains flow/signoff.py. This additionally
requires finite, clean timing, explicit electrical waivers, complete functional
GL, SDF coverage, and a precheck of the exact GDS. Free-form logs cannot release.
"""
import argparse
import math
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent))
import signoff
import release_receipts as receipts

ELECTRICAL = {"slew": "design__max_slew_violation__count",
              "cap": "design__max_cap_violation__count",
              "fanout": "design__max_fanout_violation__count"}
EVIDENCE_ERRORS = (OSError, ValueError, KeyError, TypeError, IndexError, ET.ParseError)


def finite_number(value):
    return type(value) in (int, float) and math.isfinite(value)


def nonnegative_count(value):
    return type(value) is int and value >= 0


def sdf_coverage(records, slow_mhz, profile):
    failures = []
    cases = [case for record in records for case in record["plan"]]
    for corner in ("typ", "slow", "fast"):
        period = 1000 / (slow_mhz if corner == "slow" else 50)
        required = set(receipts.BASIC_TESTS)
        if profile == "v2":
            required.update(receipts.V2_TESTS)
        covered = {test for case in cases if case["corner"] == corner and case["expect"] == "pass"
                   and case["module"] == "test" and case["period_ns"] <= period + 1e-6
                   for test in case["tests"]}
        if required - covered:
            failures.append(f"SDF {corner}: missing passing tests at <= {period:g} ns: {sorted(required - covered)}")
        if profile == "v2" and not any(case["corner"] == corner and case["expect"] == "pass"
                                      and case["module"] == "test" and abs(case["period_ns"] - 20.834) < 1e-6
                                      and "test_usb_fs_device" in case["tests"] for case in cases):
            failures.append(f"SDF {corner}: missing USB full-speed test at 20.834 ns")
    if not any(case["expect"] == "fail" and case["module"] == "test"
               and "test_fetch_to_pin" in case["tests"] and case["period_ns"] < 20 for case in cases):
        failures.append("SDF: missing completed overclock functional-failure control")
    return failures


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("worktree")
    ap.add_argument("--slow-mhz", type=float, default=50,
                    help="required slow-corner frequency (default 50); never extrapolates slack")
    ap.add_argument("--waive", action="append", default=[], metavar="KIND=N")
    ap.add_argument("--waiver-reason", help="documented justification for all explicit electrical waivers")
    ap.add_argument("--gl-receipt", help="otherwise discover completed gl.*/receipt.json in this run")
    ap.add_argument("--sdf-receipt", action="append", default=[], help="repeat for separate SDF sets")
    ap.add_argument("--precheck-receipt")
    ap.add_argument("--sdf-profile", choices=("standard", "v2"), default="v2")
    ap.add_argument("--sdf-log", help=argparse.SUPPRESS)
    ap.add_argument("--precheck-log", help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    failures = []
    if not finite_number(a.slow_mhz) or not 0 < a.slow_mhz <= 50:
        ap.error("--slow-mhz must be finite, positive and no greater than 50")
    waive = {}
    for item in a.waive:
        try:
            kind, number = item.split("=")
            if kind not in ELECTRICAL or kind in waive or not number.isdecimal():
                raise ValueError()
            waive[kind] = int(number)
        except ValueError:
            ap.error("--waive requires each kind once: slew=N, cap=N or fanout=N, N >= 0")
    if waive and not (a.waiver_reason or "").strip():
        ap.error("--waive requires --waiver-reason")
    wt = Path(a.worktree).resolve()
    run = wt / "runs/wokwi"
    print("== minimum signoff")
    try:
        if signoff.main(str(run)) != 0:
            failures.append("flow/signoff.py fails")
    except EVIDENCE_ERRORS as e:
        failures.append(f"incomplete minimum signoff evidence: {e}")
    print("== release criteria")
    try:
        m = receipts.read_json(run / "final/metrics.json")
        period = receipts.read_json(run / "resolved.json")["CLOCK_PERIOD"]
        if not finite_number(period) or period <= 0:
            raise ValueError("invalid CLOCK_PERIOD in resolved.json")
        for corner in signoff.CORNERS:
            target = a.slow_mhz if corner.startswith("nom_slow") else 50
            sta = receipts.one(run.glob("*-openroad-stapostpnr"), "final STA directory")
            report = (sta / corner / "clock.rpt").read_text()
            clock = re.search(r"^Clock: clk\n(?:(?!^Clock:).)*?^Period: ([0-9.]+)\s*$", report, re.M | re.S)
            if not clock or abs(float(clock[1]) - period) > 1e-6:
                failures.append(f"{corner}: actual STA clock report missing or disagrees with CLOCK_PERIOD")
            if period > 1000 / target + 1e-6:
                failures.append(f"{corner}: STA constrained at {period:g} ns, does not establish {target:g} MHz")
            for check in ("setup", "hold"):
                ws = m.get(f"timing__{check}__ws__corner:{corner}")
                count = m.get(f"timing__{check}_vio__count__corner:{corner}")
                if not finite_number(ws) or ws < 0 or not nonnegative_count(count) or count != 0:
                    failures.append(f"{corner}: {check} slack {ws}, violators {count}; fresh STA required, no slack extrapolation")
            for kind, key in ELECTRICAL.items():
                n = m.get(f"{key}__corner:{corner}")
                if not nonnegative_count(n) or n > waive.get(kind, 0):
                    failures.append(f"{corner}: {n} max-{kind} violations (allowed {waive.get(kind, 0)})")
            print(f"{corner}: actual constraint {period:g} ns; setup {m.get(f'timing__setup__ws__corner:{corner}')} ns; "
                  f"hold {m.get(f'timing__hold__ws__corner:{corner}')} ns")
        receipts.candidate_files(wt)
    except EVIDENCE_ERRORS as e:
        failures.append(f"invalid final run evidence: {e}")

    gl_paths = [Path(a.gl_receipt)] if a.gl_receipt else sorted((wt / "test/sim_build").glob("gl.*/receipt.json"))
    valid_gl = False
    for path in gl_paths:
        try:
            receipts.verify(path, wt, "gl")
            print(f"GL verified: {path}")
            valid_gl = True
        except EVIDENCE_ERRORS as e:
            print(f"GL receipt rejected: {e}")
    if not valid_gl:
        failures.append("no complete GL receipt for the candidate; rerun flow/gl_test.sh")
    sdf = []
    for path in a.sdf_receipt:
        try:
            sdf.append(receipts.verify(path, wt, "sdf"))
            print(f"SDF verified: {path}")
        except EVIDENCE_ERRORS as e:
            failures.append(f"SDF receipt rejected: {e}")
    failures.extend(sdf_coverage(sdf, a.slow_mhz, a.sdf_profile))
    if a.precheck_receipt:
        try:
            receipts.verify(a.precheck_receipt, wt, "precheck")
            print(f"precheck verified: {a.precheck_receipt}")
        except EVIDENCE_ERRORS as e:
            failures.append(f"precheck receipt rejected: {e}")
    else:
        failures.append("no precheck receipt; run flow/precheck_test.py")
    if a.sdf_log or a.precheck_log:
        failures.append("free-form logs cannot release a candidate; supply automatic --sdf-receipt / --precheck-receipt")
    if waive:
        print(f"electrical waivers: {waive}; reason: {a.waiver_reason}")
    if failures:
        print("RELEASE FAILED:\n" + "\n".join(f"- {failure}" for failure in failures))
    else:
        print(f"RELEASE PASSED (SDF profile {a.sdf_profile}, slow target {a.slow_mhz:g} MHz"
              + (", WITH ELECTRICAL WAIVERS" if waive else "") + ")")
    return int(bool(failures))


if __name__ == "__main__":
    sys.exit(main())
