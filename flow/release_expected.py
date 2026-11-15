#!/usr/bin/env python3
"""The identity a release build must reproduce, and its check.

    flow/release_expected.py WORKTREE EXPECTED [--late-clock]

WORKTREE is a hardened checkout (its runs/wokwi/final), on the compute runner
or in GitHub's gds job after "Build GDS". EXPECTED is a tracked JSON file under
evidence/ (for the 3-core 8x4 release: evidence/8x4-release-expected.json):

    netlist_sha256          sha256 of runs/wokwi/final/nl/*.nl.v
    gds_normalized_sha256   sha256 of runs/wokwi/final/gds/*.gds with its
                            BGNLIB/BGNSTR dates zeroed (flow/build_manifest.py's
                            normalized_gds_digest, the normalization under which
                            a clean rebuild reproduces the GDS)
    late_clock              {"registers": N, "branches": M}: the LATE_CLOCK_COMPLETE
                            line that flow/sram_clock_advance.tcl prints in the
                            SRAM clock advance step's log
    nonlatch_floor_ns       the least worst non-latch setup slack at the slow
                            corner that the release accepts (flow/release_check.py
                            --require-nonlatch-floor must be at least this)
    slow_mhz                the slow-corner rate the release claims

This prints the expected and the actual netlist and GDS hashes (and, with
--late-clock, the late-clock counts), each on its own line, and exits 1 on any
difference or missing file. flow/release_check.py --expect runs the same
comparison, late clock included.
"""
import argparse
import json
import math
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_manifest import digest, normalized_gds_digest, one  # noqa: E402

SCHEMA = "protoemu-release-expected-1"
HASHES = ("netlist_sha256", "gds_normalized_sha256")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
LATE_LOG = "runs/wokwi/*-project-sramclockadvance/*-project-sramclockadvancetcl/project-sramclockadvancetcl.log"
LATE_RE = re.compile(r"^LATE_CLOCK_COMPLETE (\d+) registers (\d+) branches\s*$", re.M)


def count(value):
    return type(value) is int and value >= 0


def load(path):
    """The expected file, or a ValueError naming what is wrong with it."""
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise ValueError(f"{path}: not a {SCHEMA} file")
    for key in HASHES:
        if not isinstance(data.get(key), str) or not HEX64.match(data[key]):
            raise ValueError(f"{path}: {key} is not a full lower-case sha256")
    late = data.get("late_clock")
    if not isinstance(late, dict) or set(late) != {"registers", "branches"} or not all(map(count, late.values())):
        raise ValueError(f"{path}: late_clock must be {{\"registers\": N, \"branches\": M}}")
    floor = data.get("nonlatch_floor_ns")
    if type(floor) not in (int, float) or not math.isfinite(floor):
        raise ValueError(f"{path}: nonlatch_floor_ns is not a finite number")
    mhz = data.get("slow_mhz")
    if type(mhz) not in (int, float) or not 0 < mhz <= 50:
        raise ValueError(f"{path}: slow_mhz must be in (0, 50]")
    return data


def actual_hashes(worktree):
    final = Path(worktree) / "runs/wokwi/final"
    return {"netlist_sha256": digest(one((final / "nl").glob("*.nl.v"), "final netlist")),
            "gds_normalized_sha256": normalized_gds_digest(one((final / "gds").glob("*.gds"), "final GDS"))}


def actual_late_clock(worktree):
    """{"registers", "branches"} from the one LATE_CLOCK_COMPLETE line of the run's
    SRAM clock advance step, or a ValueError."""
    logs = sorted(Path(worktree).glob(LATE_LOG))
    if len(logs) != 1:
        raise ValueError(f"expected one SRAM clock advance step log ({LATE_LOG}), found {len(logs)}")
    lines = LATE_RE.findall(logs[0].read_text(errors="replace"))
    if len(lines) != 1:
        raise ValueError(f"{logs[0]}: expected one LATE_CLOCK_COMPLETE line, found {len(lines)}")
    return {"registers": int(lines[0][0]), "branches": int(lines[0][1])}


def compare(expected, worktree, late_clock=True):
    """(problems, lines): the comparison of a run with the expected file's data."""
    problems, lines = [], []
    got = actual_hashes(worktree)
    for key in HASHES:
        same = got[key] == expected[key]
        lines += [f"{key} expected {expected[key]}", f"{key} actual   {got[key]} ({'match' if same else 'MISMATCH'})"]
        if not same:
            problems.append(f"{key} {got[key]} is not the expected {expected[key]}")
    if late_clock:
        want = expected["late_clock"]
        try:
            have = actual_late_clock(worktree)
        except (OSError, ValueError) as e:
            problems.append(f"late clock: {e}")
            lines.append(f"late clock expected {want['registers']} registers on {want['branches']} branches; "
                         f"actual unreadable")
        else:
            same = have == want
            lines.append(f"late clock expected {want['registers']} registers on {want['branches']} branches; "
                         f"actual {have['registers']} on {have['branches']} ({'match' if same else 'MISMATCH'})")
            if not same:
                problems.append(f"late clock: {have['registers']} registers on {have['branches']} branches, "
                                f"expected {want['registers']} on {want['branches']}")
    return problems, lines


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("worktree", help="hardened checkout (runs/wokwi/final)")
    ap.add_argument("expected", help="expected-release JSON file (evidence/)")
    ap.add_argument("--late-clock", action="store_true", help="also compare the late-clock register count")
    a = ap.parse_args(argv)
    try:
        expected = load(a.expected)
        problems, lines = compare(expected, a.worktree, a.late_clock)
    except (OSError, ValueError, KeyError, TypeError) as e:
        print(f"RELEASE IDENTITY UNREADABLE: {e}")
        return 1
    for line in lines:
        print(line)
    if problems:
        print("RELEASE IDENTITY MISMATCH:\n" + "\n".join(f"- {p}" for p in problems))
        return 1
    print(f"RELEASE IDENTITY MATCHES {a.expected}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
