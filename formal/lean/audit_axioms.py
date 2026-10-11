#!/usr/bin/env python3
"""Check every declared WAIT theorem's transitive Lean axiom dependencies."""
from pathlib import Path
import re
import subprocess

HERE = Path(__file__).resolve().parent
ALLOWED = {"propext", "Classical.choice", "Quot.sound"}  # Standard Lean foundations.


def main():
    result = subprocess.run(["lake", "env", "lean", "-DwarningAsError=true", "Audit.lean"],
                            cwd=HERE, text=True, capture_output=True, check=True, timeout=60)
    declared = {"Wirelingo." + name for name in
                re.findall(r"^theorem (\w+)", (HERE / "Wirelingo/Wait.lean").read_text(), re.M)}
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
