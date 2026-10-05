#!/usr/bin/env python3
"""Signoff summary of a LibreLane run: DRC/LVS/antenna, timing per corner,
electrical exceptions, utilization, the SRAM timing view each STA corner
loaded, and the netlist/GDS hashes.

    python3 flow/signoff.py <run dir, e.g. runs/wokwi>
"""
import glob
import hashlib
import json
import os
import re
import sys
import argparse
import xml.etree.ElementTree as ET
from pathlib import Path
from manifest import sources, identity, verify_physical_inputs

KEYS = [
    ("routing DRC", "route__drc_errors"),
    ("KLayout DRC", "klayout__drc_error__count"),
    ("LVS errors", "design__lvs_error__count"),
    ("antenna violations", "route__antenna_violation__count"),
    ("stdcell utilization", "design__instance__utilization__stdcell"),
    ("utilization incl. macros", "design__instance__utilization"),
]
CORNERS = ["nom_typ_1p20V_25C", "nom_slow_1p08V_125C", "nom_fast_1p32V_m40C"]


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()[:16] if os.path.exists(path) else "-"


def complete_gl_suites(gl, receipt_path, run):
    """Recheck all three fresh XML artifacts, not just the JSON pass label."""
    suites = gl.get("suites")
    if not isinstance(suites, list) or len(suites) != 3:
        return False
    required = {"test", "test_pio", "test_eth_service"}
    seen = set()
    for suite in suites:
        if not isinstance(suite, dict) or suite.get("module") not in required:
            return False
        seen.add(suite["module"])
        name, expected = suite.get("results_file"), suite.get("tests")
        if not isinstance(name, str) or not isinstance(expected, int) or expected < 1:
            return False
        result = (receipt_path.parent / name).resolve()
        if not result.is_relative_to(Path(run).resolve()) or not result.is_file():
            return False
        if hashlib.sha256(result.read_bytes()).hexdigest() != suite.get("results_sha256"):
            return False
        try:
            cases = list(ET.parse(result).getroot().iter("testcase"))
        except (OSError, ET.ParseError):
            return False
        if len(cases) != expected or any(c.find(t) is not None for c in cases
                                         for t in ("failure", "error", "skipped")):
            return False
    return seen == required


def main(run, manifest=None, summary_only=False):
    failures = []
    if not summary_only:
        if manifest is None:
            failures.append("candidate input manifest is required")
        else:
            candidate = json.load(open(manifest))
            current = {n: h for n, h in sources().items() if n.startswith("src/") or n == "info.yaml"}
            if current != candidate["hardware_files"] or identity(current) != candidate["hardware_sha256"]:
                failures.append("RTL/config/SDC hashes differ from hardened candidate")
            flow_error = verify_physical_inputs(candidate, Path(manifest).resolve().parent)
            if flow_error:
                failures.append(flow_error)
    m = json.load(open(os.path.join(run, "final", "metrics.json")))
    for label, k in KEYS:
        print(f"{label:28s} {m.get(k)}")
        if k not in ("design__instance__utilization__stdcell", "design__instance__utilization") and m.get(k) != 0:
            failures.append(f"{label}: missing or nonzero")
    for c in CORNERS:
        g = lambda k: m.get(f"{k}__corner:{c}")
        print(f"{c:28s} setup WS {g('timing__setup__ws')}, hold WS {g('timing__hold__ws')}, "
              f"setup/hold violators {g('timing__setup_vio__count')}/{g('timing__hold_vio__count')}, "
              f"max slew/cap violations {g('design__max_slew_violation__count')}/"
              f"{g('design__max_cap_violation__count')}")
        for k in ("timing__setup__ws", "timing__hold__ws"):
            if g(k) is None or g(k) < 0:
                failures.append(f"{c} {k}: missing or negative")
        for k in ("timing__setup_vio__count", "timing__hold_vio__count",
                  "design__max_slew_violation__count", "design__max_cap_violation__count"):
            if g(k) != 0:
                failures.append(f"{c} {k}: missing or nonzero")
    sta = sorted(glob.glob(os.path.join(run, "*-openroad-stapostpnr")))
    if sta:
        for c in CORNERS:
            log = os.path.join(sta[-1], c, "sta.log")
            libs = sorted(set(re.findall(r"RM_IHPSG13_1P_256x32_c2_bm_bist_(\w+?)\.lib", open(log).read()))) if os.path.exists(log) else []
            print(f"SRAM timing view, {c:20s} {', '.join(libs) or '-'}")
            expected = {CORNERS[0]: "typ_1p20V_25C", CORNERS[1]: "slow_1p08V_125C",
                        CORNERS[2]: "fast_1p32V_m55C"}[c]
            if libs != [expected]:
                failures.append(f"{c}: wrong or missing SRAM timing view")
    else:
        failures.append("post-route STA is missing")
    top = os.path.basename(glob.glob(os.path.join(run, "final", "nl", "*.nl.v"))[0])[:-5]
    print(f"sha256 nl {sha(os.path.join(run, 'final', 'nl', top + '.nl.v'))}  "
          f"gds {sha(os.path.join(run, 'final', 'gds', top + '.gds'))}")
    if not os.path.exists(os.path.join(run, 'final', 'gds', top + '.gds')):
        failures.append("final GDS is missing")
    # Detailed CDC/latch/gating/pulse checks and exact-netlist GL are recorded
    # separately; ordinary setup/hold metrics do not establish these checks.
    extra = os.path.join(run, "release_checks.json")
    if not summary_only:
        if not os.path.exists(extra):
            failures.append("detailed timing and exact-netlist GL receipt is missing")
        else:
            receipt = json.load(open(extra))
            expected_nl = hashlib.sha256(open(os.path.join(run, 'final', 'nl', top + '.nl.v'), 'rb').read()).hexdigest()
            if receipt.get("netlist_sha256") != expected_nl:
                failures.append("detailed check receipt refers to a different netlist")
            for check in ("cdc_max_delay", "synchronizer_resolution", "latch_timing", "clock_gating",
                          "minimum_pulse_width", "electrical_limits", "functional_gl"):
                entry = receipt.get(check, {})
                evidence = entry.get("evidence")
                if entry.get("status") != "pass" or not isinstance(evidence, list) or not evidence:
                    failures.append(f"missing successful {check} evidence")
                    continue
                for item in evidence:
                    if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                        failures.append(f"{check}: invalid evidence reference")
                        continue
                    path = (Path(run) / item["path"]).resolve()
                    if not path.is_relative_to(Path(run).resolve()) or not path.is_file():
                        failures.append(f"{check}: evidence file missing or outside run")
                    elif hashlib.sha256(path.read_bytes()).hexdigest() != item.get("sha256"):
                        failures.append(f"{check}: evidence hash differs")
                if check == "functional_gl":
                    valid_gl = False
                    for item in evidence:
                        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                            continue
                        path = (Path(run) / item["path"]).resolve()
                        if path.is_relative_to(Path(run).resolve()) and path.is_file() and path.suffix == ".json":
                            try:
                                gl = json.loads(path.read_text())
                            except (ValueError, UnicodeError):
                                continue
                            valid_gl |= (gl.get("status") == "pass"
                                         and gl.get("kind") == "functional GL without SDF"
                                         and gl.get("netlist_sha256") == expected_nl
                                         and gl.get("simulated_netlist_sha256") == expected_nl
                                         and gl.get("candidate_inputs_sha256") == hashlib.sha256(Path(manifest).read_bytes()).hexdigest()
                                         and complete_gl_suites(gl, path, run))
                    if not valid_gl:
                        failures.append("functional GL receipt needs this netlist/candidate and all three passing suites")
        if failures:
            print("RELEASE GATE FAILED:\n  " + "\n  ".join(failures))
            return 1
        print("release gate passed")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("--manifest")
    ap.add_argument("--summary-only", action="store_true", help="historical diagnostic, never a release gate")
    a = ap.parse_args()
    try:
        sys.exit(main(a.run, a.manifest, a.summary_only))
    except (OSError, ValueError, KeyError, IndexError) as e:
        sys.exit(f"incomplete or invalid signoff evidence: {e}")
