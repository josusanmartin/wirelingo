#!/usr/bin/env python3
"""One <=90s C2 focused invocation, never the C1/legacy comparison corpora."""
import argparse
import json
from pathlib import Path
import subprocess
import time

HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    args.evidence.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    commands = [["lake", "build"], ["python3", "audit_axioms.py"],
        ["python3", "audit_axioms.py", "--source", "Wirelingo/Event.lean", "--audit", "EventAudit.lean"],
        ["python3", "audit_axioms.py", "--source", "Wirelingo/Gating.lean", "--audit", "GatingAudit.lean"],
        ["python3", "check_gating.py"]]
    checks = []
    for index, argv in enumerate(commands):
        remaining = 90 - (time.monotonic() - start)
        if remaining <= 0:
            raise RuntimeError("90-second invocation cap reached")
        before = time.monotonic()
        result = subprocess.run(argv, cwd=HERE, text=True, capture_output=True, timeout=remaining)
        (args.evidence / f"{index + 1}.log").write_text(result.stdout + result.stderr)
        checks.append({"argv": argv, "exit": result.returncode, "elapsed_seconds": time.monotonic() - before})
        (args.evidence / "validation.json").write_text(json.dumps({"checks": checks,
            "elapsed_seconds": time.monotonic() - start}, indent=2) + "\n")
        print(f"check {index + 1}: exit {result.returncode}", flush=True)
        if result.returncode:
            raise SystemExit(result.returncode)
    print("PASS C2-EUCLID-GATING focused invocation", flush=True)


if __name__ == "__main__":
    main()
