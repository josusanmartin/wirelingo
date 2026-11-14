#!/usr/bin/env python3
"""Release gate using actual timing constraints and automatic verification receipts.

The minimum Tiny Tapeout check remains flow/signoff.py. This additionally
requires finite, clean timing, explicit electrical waivers, complete functional
GL, SDF coverage, and a precheck of the exact GDS. Free-form logs cannot release.
A slow-corner rate below the run's constraint (--slow-mhz 48 on a 50 MHz run)
needs a fresh STA of the run at that period (--slow-sta-receipt, from
flow/sta_rerun.py); typical and fast stay at 50 MHz.

It also gates clock/reset checks (pulse width, recovery/removal, gating, from
a checks receipt of flow/check_rerun.py), the SRAM power hookup (the final DEF
and the ExtendPowerStripes log; flow/release_gates.py), and, with
--shipping-root, that the run was built from the shipping commit's build
inputs (flow/build_manifest.py).
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
import release_gates as gates
import build_manifest
import clean_tree

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
    ap.add_argument("--slow-sta-receipt",
                    help="fresh STA of this run at <= 1000/slow-mhz ns (flow/sta_rerun.py): "
                         "the slow corner's timing is taken from it instead of the run's own STA")
    ap.add_argument("--require-sta-inputs", action="store_true",
                    help="a live run: refuse an STA receipt whose run-side checks would be skipped, "
                         "and a release gate without its inputs "
                         "(flow/release_evidence.py passes it; an archive check leaves it out)")
    ap.add_argument("--checks-receipt", help="clock/reset/pulse-width check STA of this run (flow/check_rerun.py)")
    ap.add_argument("--flow-python", default=str(Path.home() / "protoemu-flow/protoemu/.venv-flow/bin/python"),
                    help="python with tt-support-tools' requirements, to regenerate the shipping tree's configs")
    ap.add_argument("--nonlatch-receipt", help="worst setup slack without the latches (flow/sta_nonlatch.py); printed")
    ap.add_argument("--shipping-root",
                    help="checkout of the shipping commit: the run's build inputs must equal its "
                         "(flow/build_manifest.py); flow/release_evidence.py passes the validation root")
    ap.add_argument("--waive", action="append", default=[], metavar="KIND=N")
    ap.add_argument("--waiver-reason", help="documented justification for all explicit electrical waivers")
    ap.add_argument("--gl-receipt", help="otherwise discover completed gl.*/receipt.json in this run")
    ap.add_argument("--validation-root", help="shipping firmware/test checkout; requires identical candidate RTL/profile")
    ap.add_argument("--sdf-receipt", action="append", default=[], help="repeat for separate SDF sets")
    ap.add_argument("--precheck-receipt")
    ap.add_argument("--sdf-profile", choices=("standard", "v2"), default="v2")
    ap.add_argument("--sdf-log", help=argparse.SUPPRESS)
    ap.add_argument("--precheck-log", help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    failures, skipped = [], []
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
        fresh = None
        nonlatch = None
        if a.nonlatch_receipt:
            try:
                import sta_nonlatch     # only with a receipt: archives made before it do not hold the tool
                nonlatch = sta_nonlatch.verify(a.nonlatch_receipt, wt)
                print(f"non-latch STA verified: {a.nonlatch_receipt} ({nonlatch['plan']['period_ns']:g} ns, "
                      f"image {nonlatch['plan']['image_id']})")
            except (OSError, ValueError, KeyError, TypeError) as e:
                failures.append(f"invalid non-latch STA evidence: {e}")
        else:
            print("worst non-latch setup slack: not measured (flow/sta_nonlatch.py, --nonlatch-receipt)")
        if a.slow_sta_receipt:
            fresh = receipts.verify(a.slow_sta_receipt, wt, "sta")
            print(f"slow-corner STA verified: {a.slow_sta_receipt} ({fresh['plan']['period_ns']:g} ns, "
                  f"image {fresh['plan']['image_id']}, LibreLane {fresh['plan']['librelane_version']})")
            skipped += fresh["skipped"]
            for check in fresh["skipped"]:
                print(f"slow-corner STA receipt, SKIPPED: {check}")
            if a.require_sta_inputs and fresh["skipped"]:
                failures.append("--require-sta-inputs: the run lacks the STA inputs its receipt records "
                                f"({len(fresh['skipped'])} checks would be skipped)")
        for corner in signoff.CORNERS:
            target = a.slow_mhz if corner.startswith("nom_slow") else 50
            sta = receipts.one(run.glob("*-openroad-stapostpnr"), "final STA directory")
            report = (sta / corner / "clock.rpt").read_text()
            clock = re.search(receipts.CLOCK_PERIOD_RE, report, re.M | re.S)
            if not clock or abs(float(clock[1]) - period) > receipts.CLOCK_TOLERANCE_NS:
                failures.append(f"{corner}: actual STA clock report missing or disagrees with CLOCK_PERIOD")
            timed, source = period, "actual constraint"
            timing = {k: m.get(f"{k}__corner:{corner}") for k in receipts.STA_METRICS}
            if fresh is not None and corner.startswith("nom_slow"):
                # the run's own STA stays checked above; the slow corner's timing
                # is the fresh STA's at its own (receipt-checked) period
                timed, source = fresh["plan"]["period_ns"], "fresh STA"
                timing = receipts.sta_metrics(Path(a.slow_sta_receipt).parent, corner)
            if timed > 1000 / target + 1e-6:
                failures.append(f"{corner}: STA constrained at {timed:g} ns, does not establish {target:g} MHz")
            for check in ("setup", "hold"):
                ws = timing[f"timing__{check}__ws"]
                count = timing[f"timing__{check}_vio__count"]
                if not finite_number(ws) or ws < 0 or not nonnegative_count(count) or count != 0:
                    failures.append(f"{corner}: {check} slack {ws}, violators {count} at {timed:g} ns; "
                                    "fresh STA required, no slack extrapolation")
            for kind, key in ELECTRICAL.items():
                n = m.get(f"{key}__corner:{corner}")
                if not nonnegative_count(n) or n > waive.get(kind, 0):
                    failures.append(f"{corner}: {n} max-{kind} violations (allowed {waive.get(kind, 0)})")
            print(f"{corner}: {source} {timed:g} ns; setup {timing['timing__setup__ws']} ns "
                  f"(all endpoints, latches included); hold {timing['timing__hold__ws']} ns")
            if nonlatch is not None and corner in nonlatch["results"]:
                r = nonlatch["results"][corner]
                print(f"{corner}: worst non-latch setup slack {r['nonlatch']:+.4f} ns at {r['nonlatch_endpoint']} "
                      f"({nonlatch['plan']['period_ns']:g} ns, flow/sta_nonlatch.py)")
        receipts.candidate_files(wt)
    except EVIDENCE_ERRORS as e:
        failures.append(f"invalid final run evidence: {e}")

    gate_skipped = []
    print("== clock and reset checks")
    try:
        checks_dir = None
        if a.checks_receipt:
            data = receipts.verify(a.checks_receipt, wt, "checks")
            checks_dir = Path(a.checks_receipt).parent
            print(f"checks receipt verified: {a.checks_receipt} (image {data['plan']['image_id']}; the pulse pass "
                  f"disabled the path exceptions of {len(data['plan']['sdc_removed'])} SDC lines)")
            # the report Tcl that ran must be this checkout's: an older receipt's reports may lack a section
            stale = [name for name, tcl in receipts.CHECK_PASSES.items()
                     if data["plan"]["tcl"].get(name) != receipts.digest(Path(__file__).resolve().parent / tcl)]
            if stale:
                failures.append(f"checks receipt ran other report Tcl than this checkout's flow/: {stale}")
            skipped += data["skipped"]
            for check in data["skipped"]:
                print(f"checks receipt, SKIPPED: {check}")
            if a.require_sta_inputs and data["skipped"]:
                failures.append("--require-sta-inputs: the run lacks the STA inputs its checks receipt records")
        problems, notes = gates.timing_check_failures(wt, checks_dir)
        for line in notes:
            print(line)
        failures += problems
    except (*EVIDENCE_ERRORS, AttributeError) as e:
        failures.append(f"invalid clock/reset check evidence: {e}")
    try:
        archive = gates.archive_shaped(wt)
    except EVIDENCE_ERRORS as e:
        archive = False
        failures.append(f"invalid final run evidence: {e}")
    resolved = {}
    try:
        resolved = receipts.read_json(wt / "runs/wokwi/resolved.json")
    except EVIDENCE_ERRORS:
        pass        # reported above
    sram = sorted(pin.rsplit("/", 1)[0] for pin in gates.sram_clock_pins(resolved))
    for title, gate in (("SRAM power stripes (ExtendPowerStripes log)", lambda: gates.stripes_failures(wt, sram)),
                        ("macro power pins (final DEF)", lambda: gates.macro_power_failures(wt))):
        print(f"== {title}")
        if archive:
            gate_skipped.append(f"{title}: an evidence archive keeps neither the step logs nor the final DEF")
            print(f"release gate, SKIPPED: {gate_skipped[-1]}")
            continue
        try:
            problems, notes = gate()
            for line in notes:
                print(line)
            failures += problems
        except (*EVIDENCE_ERRORS, AttributeError) as e:
            failures.append(f"invalid {title} evidence: {e}")
    print("== shipping build")
    if a.shipping_root and archive:
        gate_skipped.append("shipping build: an evidence archive keeps neither flow/, the plugin nor tt/")
        print(f"release gate, SKIPPED: {gate_skipped[-1]}")
    elif a.shipping_root:
        # the release identity (flow/clean_tree.py): the run hardened a clean
        # checkout of exactly the shipping tree, with the pinned tt, image and PDK
        try:
            problems, notes = clean_tree.check_release(wt, a.shipping_root, a.flow_python)
            for line in notes:
                print(f"clean tree: {line}")
            failures += [f"clean tree: {p}" for p in problems]
            if not problems:
                print(f"clean tree: the run hardened a clean checkout of {a.shipping_root}'s tree")
        except (*EVIDENCE_ERRORS, AttributeError) as e:
            failures.append(f"invalid clean-tree evidence: {e}")
        # the file-set manifest: an additional check, and the diff that explains a mismatch
        try:
            problems, notes = build_manifest.compare(build_manifest.run_manifest(wt),
                                                     build_manifest.checkout_manifest(a.shipping_root))
            for line in notes:
                print(f"shipping build: {line}")
            failures += [f"shipping build: {p}" for p in problems]
            if not problems:
                print(f"shipping build: the run's build inputs equal {a.shipping_root}'s")
            # what CI checks: the commit's config.json is its generator's submission preset
            rc, said = build_manifest.generator_check(a.shipping_root)
            print(f"shipping build: gen_config.py --preset submission --check: {said} (rc {rc})")
            if rc != 0:
                failures.append(f"shipping build: the checkout's gen_config.py --preset submission --check fails "
                                f"(rc {rc}), as CI's would: {said}")
        except (*EVIDENCE_ERRORS, AttributeError) as e:
            failures.append(f"invalid shipping build evidence: {e}")
    else:
        gate_skipped.append("shipping build: no --shipping-root, so the run is not bound to a shipping commit")
        print(f"release gate, SKIPPED: {gate_skipped[-1]}")
    if a.require_sta_inputs and gate_skipped:
        failures.append(f"--require-sta-inputs: {len(gate_skipped)} release gates would be skipped")

    gl_paths = [Path(a.gl_receipt)] if a.gl_receipt else sorted((wt / "test/sim_build").glob("gl.*/receipt.json"))
    valid_gl = False
    for path in gl_paths:
        try:
            receipts.verify(path, wt, "gl", a.validation_root)
            print(f"GL verified: {path}")
            valid_gl = True
        except EVIDENCE_ERRORS as e:
            print(f"GL receipt rejected: {e}")
    if not valid_gl:
        failures.append("no complete GL receipt for the candidate; rerun flow/gl_test.sh")
    sdf = []
    for path in a.sdf_receipt:
        try:
            sdf.append(receipts.verify(path, wt, "sdf", a.validation_root))
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
              + (", WITH ELECTRICAL WAIVERS" if waive else "")
              + (f", {len(skipped)} {receipts.SKIPPED_VERDICT} (the run's STA inputs are absent)" if skipped else "")
              + (f", {len(gate_skipped)} {receipts.GATES_SKIPPED_VERDICT}" if gate_skipped else "")
              + ")")
    return int(bool(failures))


if __name__ == "__main__":
    sys.exit(main())
