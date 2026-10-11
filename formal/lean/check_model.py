#!/usr/bin/env python3
"""Compare compiled Lean WAIT transitions with actual encoded Python-model steps.

Standard library only. This is differential evidence, not an RTL refinement proof.
Core.step is used at its instruction-attempt boundary (tick, no delay/fault/host).
The resolved condition is represented by WAIT PIN1 with side-set disabled.
"""
import argparse
import hashlib
import itertools
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from pe import isa, sim  # noqa: E402


def cases():
    # Exhaust all budgets and all combinations of the three Boolean inputs.
    # Rotate register selection and prior WAIT state across budgets.
    for budget in range(65536):
        for timeout, condition, t in itertools.product(range(2), repeat=3):
            yield (timeout, condition, budget, t, 13, 255, 0, 0,
                   budget % 4, budget % 2)
    # Check every starting PC, both wrap branches, condition/zero-budget priority,
    # and representative delay fields. A matching logical bank owns each PC.
    for pc, wraps, condition, delay in itertools.product(
            range(1024), range(2), range(2), (0, 1, 127, 255)):
        yield (1, condition, 0, 1, pc, pc if wraps else (pc + 1) % 1024,
               23, delay, pc % 4, pc % 2)
    # Full register/WAIT-state cross product at budget and address boundaries.
    for budget, treg, in_wait, timeout, condition, t in itertools.product(
            (0, 1, 2, 255, 256, 32768, 65534, 65535), range(4),
            range(2), range(2), range(2), range(2)):
        yield (timeout, condition, budget, t, 255, 255, 3, 255, treg, in_wait)


def model_step(chip, case):
    timeout, condition, budget, t, pc, wt, wb, delay, treg, in_wait = case
    c = chip.cores[pc >> 8]
    c.pc, c.wrap_top, c.wrap_bottom = pc, wt, wb
    c.r = [0x1234, 0x4567, 0x89AB, 0xCDEF]
    c.r[treg] = budget
    old_r = c.r.copy()
    c.t, c.in_wait = t, in_wait
    c.wprev, c.wevent = 0, 0
    c.cnt = c.facc = c.dcnt = c.halted = c.fault = 0
    # Default divider 1.0 and side_count=0 make sd the exact delay byte.
    operand = (isa.W_PIN1 << 16) | (3 << 11) | (timeout << 10) | (treg << 8)
    chip.imem[pc] = isa.enc(isa.OP_WAIT, delay, operand)
    fx = c.step(chip, condition << 3, 0)
    # WAIT PIN1 cannot modify any other architectural register or flags.
    for i in range(4):
        if i != treg and c.r[i] != old_r[i]:
            raise AssertionError(f"unselected R{i} changed: {case}")
    if fx["fset"] or fx["fclr"]:
        raise AssertionError(f"unexpected flag effect: {case}")
    return (1 - c.stalled, c.r[treg], c.t, c.pc, c.dcnt, c.in_wait)


def check_batch(probe, chip, batch):
    inputs = "".join(" ".join(map(str, case[:8])) + "\n" for case in batch)
    completed = subprocess.run([str(probe)], input=inputs, text=True,
                               capture_output=True, check=True, timeout=30)
    lines = completed.stdout.splitlines()
    if len(lines) != len(batch):
        raise AssertionError(f"probe emitted {len(lines)} rows for {len(batch)} cases")
    for case, line in zip(batch, lines):
        actual = tuple(map(int, line.split()))
        expected = model_step(chip, case)
        if actual != expected:
            raise AssertionError(f"WAIT mismatch: input={case}, Lean={actual}, model={expected}")


def check_bad_inputs(probe):
    bad = ["", "x", "1 1 0", "2 1 0 0 0 0 0 0", "1 2 0 0 0 0 0 0",
           "1 0 65536 0 0 0 0 0", "1 0 0 2 0 0 0 0", "1 0 0 0 1024 0 0 0",
           "1 0 0 0 0 1024 0 0", "1 0 0 0 0 0 1024 0", "1 0 0 0 0 0 0 256",
           "1 0 -1 0 0 0 0 0"]
    for row in bad:
        result = subprocess.run([str(probe)], input=row + "\n", text=True,
                                capture_output=True, timeout=10)
        if result.returncode != 1 or result.stdout:
            raise AssertionError(f"invalid probe input accepted: {row!r}")
    return len(bad)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", type=Path, default=HERE / ".lake/build/bin/wait_probe")
    args = parser.parse_args()
    probe = args.probe.resolve()
    chip = sim.Chip()  # Logical ISA has four PC banks, independently of 6x4 configuration.
    count = 0
    batch = []
    for case in cases():
        batch.append(case)
        if len(batch) == 4096:
            check_batch(probe, chip, batch)
            count += len(batch)
            batch.clear()
    if batch:
        check_batch(probe, chip, batch)
        count += len(batch)
    invalid = check_bad_inputs(probe)
    sources = ["tools/pe/sim.py", "tools/pe/isa.py", "src/pe_core.sv", "docs/isa.md",
               "formal/lean/Wirelingo/Wait.lean", "formal/lean/Main.lean",
               "formal/lean/check_model.py", "formal/lean/lean-toolchain"]
    print(json.dumps({"result": "pass", "transition_cases": count,
                      "invalid_inputs_rejected": invalid,
                      "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                                        for name in sources}}, indent=2))


if __name__ == "__main__":
    main()
