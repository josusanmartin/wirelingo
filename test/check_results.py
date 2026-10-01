#!/usr/bin/env python3
"""Fail unless a cocotb results file exists and holds exactly the expected
number of passing tests (no failures, errors or skips).

    python3 check_results.py top.xml 8
"""
import sys
import xml.etree.ElementTree as ET


def main(path, expected):
    try:
        cases = ET.parse(path).getroot().iter("testcase")
    except (OSError, ET.ParseError) as e:
        sys.exit(f"{path}: {e}")
    cases = list(cases)
    bad = [c.get("name") for c in cases if any(c.find(t) is not None for t in ("failure", "error", "skipped"))]
    print(f"{path}: {len(cases)} tests, {len(bad)} not passing")
    if bad or len(cases) != expected:
        sys.exit(f"{path}: expected {expected} passing tests; not passing: {bad}")


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]))
