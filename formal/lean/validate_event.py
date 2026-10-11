#!/usr/bin/env python3
"""One <=90-second C1 focused validation invocation; excludes legacy 541k corpus."""
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
    started = time.monotonic()
    commands = [["lake", "build"], ["python3", "audit_axioms.py"],
                ["python3", "audit_axioms.py", "--source", "Wirelingo/Event.lean", "--audit", "EventAudit.lean"],
                ["python3", "check_event.py"]]
    statuses = []
    for index, argv in enumerate(commands):
        remaining = 90 - (time.monotonic() - started)
        if remaining <= 0:
            raise RuntimeError("90-second invocation cap exhausted")
        before = time.monotonic()
        result = subprocess.run(argv, cwd=HERE, text=True, capture_output=True, timeout=remaining)
        (args.evidence / f"{index + 1}.log").write_text(result.stdout + result.stderr)
        statuses.append({"argv": argv, "exit": result.returncode,
                         "elapsed_seconds": time.monotonic() - before})
        (args.evidence / "validation.json").write_text(json.dumps(
            {"checks": statuses, "elapsed_seconds": time.monotonic() - started}, indent=2) + "\n")
        print(f"check {index + 1}: exit {result.returncode}", flush=True)
        if result.returncode:
            raise SystemExit(result.returncode)
    print("PASS C1-EUCLID-WAIT focused invocation", flush=True)


if __name__ == "__main__":
    main()
