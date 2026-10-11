#!/usr/bin/env python3
"""Ensure proof/build and differential checks reject deliberately wrong semantics.

Lean mutations live in disposable /tmp copies; the Python mutation is in-memory.
No RTL, source model, or committed Lean file is modified.
"""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

import check_model

HERE = Path(__file__).resolve().parent


def reject_lean_mutation(label, before, after):
    with tempfile.TemporaryDirectory(prefix=f"wirelingo-lean-{label}-") as tmp:
        root = Path(tmp) / "lean"
        shutil.copytree(HERE, root, ignore=shutil.ignore_patterns(".lake", "__pycache__"))
        source = root / "Wirelingo/Wait.lean"
        text = source.read_text()
        if text.count(before) != 1:
            raise AssertionError(f"mutation anchor is not unique: {label}")
        source.write_text(text.replace(before, after))
        result = subprocess.run(["lake", "build", "Wirelingo"], cwd=root,
                                text=True, capture_output=True, timeout=60)
        if result.returncode == 0 or "error:" not in result.stdout + result.stderr:
            raise AssertionError(f"proof build failed to reject mutation: {label}")


def main():
    reject_lean_mutation("timeout-priority", "if condition then",
                         "if condition && !(timeout && budget == 0) then")
    reject_lean_mutation("lost-decrement", "if timeout then budget - 1 else budget",
                         "if timeout then budget else budget")
    reject_lean_mutation("admitted-proof", "  simp [waitAttempt]\n\ntheorem expires_at_zero",
                         "  sorry\n\ntheorem expires_at_zero")
    # Model mutant: zero budget incorrectly wins over a satisfied pin condition.
    original = check_model.sim.Core._op_wait

    def bad_wait(core, e):
        if (e.x >> 10) & 1 and core.r[(e.x >> 8) & 3] == 0:
            e.wpin = 0
        return original(core, e)

    check_model.sim.Core._op_wait = bad_wait
    # Core.step's dispatch table stores function objects, so patch that as well.
    old_dispatch = check_model.sim._OPS[check_model.isa.OP_WAIT]
    check_model.sim._OPS[check_model.isa.OP_WAIT] = bad_wait
    try:
        try:
            check_model.check_batch(HERE / ".lake/build/bin/wait_probe", check_model.sim.Chip(),
                                    [(1, 1, 0, 0, 13, 255, 0, 0, 3, 0)])
        except AssertionError as error:
            if "WAIT mismatch" not in str(error):
                raise
        else:
            raise AssertionError("differential check accepted model timeout-priority mutation")
    finally:
        check_model.sim.Core._op_wait = original
        check_model.sim._OPS[check_model.isa.OP_WAIT] = old_dispatch
    print(json.dumps({"result": "pass", "negative_controls_rejected": [
        "Lean timeout-priority", "Lean lost-decrement", "Lean admitted-proof",
        "Python model timeout-priority"]}))


if __name__ == "__main__":
    main()
