#!/usr/bin/env python3
"""Check every declared WAIT theorem's transitive Lean axiom dependencies."""
from pathlib import Path
import argparse
import re
import subprocess

HERE = Path(__file__).resolve().parent
ALLOWED = {"propext", "Classical.choice", "Quot.sound"}  # Standard Lean foundations.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="Wirelingo/Wait.lean")
    parser.add_argument("--audit", default="Audit.lean")
    args = parser.parse_args()
    result = subprocess.run(["lake", "env", "lean", "-DwarningAsError=true", args.audit],
                            cwd=HERE, text=True, capture_output=True, check=True, timeout=60)
    declared = {"Wirelingo." + name for name in
                re.findall(r"^theorem (\w+)", (HERE / args.source).read_text(), re.M)}
    audited = {}
    for name, dependencies in re.findall(
            r"'([^']+)' depends on axioms: \[([^\]]*)\]", result.stdout):
        axioms = set(dependencies.split(", ")) if dependencies else set()
        unexpected = axioms - ALLOWED
        if unexpected:
            raise AssertionError(f"{name} uses unapproved axioms: {sorted(unexpected)}")
        audited[name] = axioms
    for name in re.findall(r"'([^']+)' does not depend on any axioms", result.stdout):
        audited[name] = set()
    if not declared or set(audited) != declared:
        raise AssertionError(f"audit coverage differs: declared={declared}, audited={set(audited)}")
    print(result.stdout, end="")
    print(f"PASS: all {len(audited)} theorems audited; only standard Lean foundations")


if __name__ == "__main__":
    main()
