#!/usr/bin/env python3
"""Closed C2 traces: one init, then external logical pins/enable/restart/PC cancel.

No later model state is sent to Lean. Pin inputs are independently derived from
the declared two-clock pad history, not Chip.pins_in(). Actual target state and
attempt/completion coverage counters are read only as comparison outputs.
"""
from contextlib import contextmanager
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

PROBE = HERE / ".lake/build/bin/gating_probe"
PROFILE = {"ncores": 2, "nbanks": 2, "bank_words": 256, "fifo_depth": 8}
DIVIDERS = ((1, 0), (2, 0), (4, 0), (1, 1), (2, 128), (4, 255),
            (0, 0), (0, 128), (0, 255))
INITIALS = ((0, 0, 0, 0, 0), (0, 255, 1, 0, 0),
            (1, 128, 3, 1, 0), (65535, 254, 255, 0, 1))


def projection(c):
    return (c.in_wait, c.wprev, c.wevent, *c.r, c.t,
            c.cnt, c.facc, c.dcnt, c.halted, c.fault)


def configs():
    yield from itertools.product((2, 3, 4, 7), DIVIDERS, range(4), range(3))


def make_trace(config):
    kind, (di, df), initial, scenario = config
    target = (kind + initial + scenario) % 2
    address, mask = target * 256, 1 << target
    cnt, facc, dcnt, halted, fault = INITIALS[initial]
    treg = (initial + scenario) % 4
    ref = (di + df) % 4
    operand = (kind << 16) | (1 << 10) | (treg << 8)
    operand |= (ref << 14) | 255 if kind == 7 else (1 << 6) | (1 << 5) | 1
    base, delay = target * 16, (0, 1, 7)[scenario]
    regs = [0, 0x55, 0xAA, 0xFF]
    regs[treg] = (0, 2, 257, 65535)[initial]
    state = (initial % 2, 0, int(initial >= 2), *regs, initial % 2,
             cnt, facc, dcnt, halted, fault)
    start = (operand, base, di, df, delay, *state)
    chip = sim.Chip(**PROFILE)
    c = chip.cores[target]
    c.entry = c.pc = c.wrap_top = c.wrap_bottom = address
    c.div_int, c.div_frac, c.in_base = di, df, base
    c.r = regs.copy()
    c.in_wait, c.wprev, c.wevent, c.t = state[0], state[1], state[2], state[7]
    c.cnt, c.facc, c.dcnt, c.halted, c.fault = cnt, facc, dcnt, halted, fault
    chip.imem[address] = isa.enc(isa.OP_WAIT, delay, operand)
    chip.sync1 = chip.sync2 = 2 | (2 << 8)
    history = [2, 2]
    commands = ["0 " + " ".join(map(str, start))]
    expected, drives = [], []
    for clock in range(24):
        enabled = not (scenario == 1 and 3 <= clock <= 8 or scenario == 2 and 5 <= clock <= 6)
        restart = scenario == 1 and clock == 6 or scenario == 2 and clock == 12
        pcw = scenario == 1 and clock == 7 or scenario == 2 and clock == 4
        pins = (history[0] & 255) | ((history[0] & 31) << 16)
        commands.append("1 " + " ".join(map(str, (pins, int(enabled), int(restart), int(pcw)))))
        chip.enable = mask if enabled else 0
        if restart:
            chip.ctrl_pend = (mask << 8) | (mask if enabled else 0)
        before_attempt = chip.stats.get("attempt:wait", 0)
        before_done = chip.stats.get("done:wait", 0)
        pad = int(clock in (1, 9, 17)) | (int(not (scenario == 2 and clock < 7)) << 1)
        chip.step(uio_in=pad, ui_in=pad, we=pcw,
                  addr=sim.core_reg(target, sim.C_PC), wdata=address)
        if c.pc != address:
            raise AssertionError("fixed own-bank/self-wrap contract violated")
        expected.append((*projection(c), chip.stats.get("attempt:wait", 0) - before_attempt,
                         chip.stats.get("done:wait", 0) - before_done))
        drives.append({"clock": clock, "pad": pad, "logical_pins": pins,
                       "enabled": enabled, "restart": restart, "pc_cancel": pcw})
        history = [history[1], pad]
    captures = chip.stats.get("wait:edge_caught_between_ticks", 0) + chip.stats.get("wait:diff_caught_between_ticks", 0)
    return {"configuration": config, "target": target, "initial_command": start,
            "commands": commands, "expected": expected, "drive": drives, "captures": captures}


def compare(traces):
    result = subprocess.run([str(PROBE)], input="\n".join(
        command for trace in traces for command in trace["commands"]) + "\n",
        text=True, capture_output=True, check=True, timeout=25)
    rows = result.stdout.splitlines()
    if len(rows) != sum(len(t["expected"]) for t in traces):
        raise AssertionError("unexpected probe output count")
    index = 0
    for trace in traces:
        for clock, expected in enumerate(trace["expected"]):
            actual = tuple(map(int, rows[index].split()))
            index += 1
            if actual != expected:
                raise AssertionError(json.dumps({"configuration": trace["configuration"],
                    "initial_command": trace["initial_command"], "clock": clock,
                    "drive_prefix": trace["drive"][:clock + 1], "Lean": actual, "model": expected}))


@contextmanager
def mutant(name):
    divider, chip_step = sim.Core._divider, sim.Chip.step
    if name == "dropped-fractional-carry":
        def no_carry(core):
            if core.cnt == 0:
                core.facc = (core.facc + core.div_frac) & 255
                core.cnt = (core.div_int - 1) & 65535
                return True
            return divider(core)
        sim.Core._divider = no_carry
    elif name == "disabled-counter-advance":
        def disabled_advance(chip, *args, **kwargs):
            for index in range(chip.ncores):
                if not ((chip.enable >> index) & 1) and not (((chip.ctrl_pend or 0) >> (8 + index)) & 1):
                    chip.cores[index]._divider()
            return chip_step(chip, *args, **kwargs)
        sim.Chip.step = disabled_advance
    else:
        def no_disabled_restart(chip, *args, **kwargs):
            if chip.ctrl_pend is not None:
                for index in range(chip.ncores):
                    if not ((chip.enable >> index) & 1):
                        chip.ctrl_pend &= ~(1 << (8 + index))
            return chip_step(chip, *args, **kwargs)
        sim.Chip.step = no_disabled_restart
    try:
        yield
    finally:
        sim.Core._divider, sim.Chip.step = divider, chip_step


def main():
    batch, traces, cycles, captures = [], 0, 0, 0
    for config in configs():
        trace = make_trace(config)
        batch.append(trace)
        traces += 1
        cycles += len(trace["expected"])
        captures += trace["captures"]
        if len(batch) == 48:
            compare(batch)
            batch.clear()
    if batch:
        compare(batch)
    if not captures:
        raise AssertionError("no non-tick pulse captured")
    rejected = []
    cases = [("dropped-fractional-carry", (2, (2, 128), 1, 0)),
             ("disabled-counter-advance", (2, (4, 0), 0, 1)),
             ("suppressed-disabled-restart", (2, (4, 255), 3, 1))]
    for name, config in cases:
        with mutant(name):
            trace = make_trace(config)
        cycles += len(trace["expected"])
        try:
            compare([trace])
        except AssertionError as error:
            rejected.append({"mutation": name, "counterexample": json.loads(str(error))})
        else:
            raise AssertionError(f"mutant accepted: {name}")
    sources = {Path(m.__file__).resolve() for m in list(sys.modules.values())
               if getattr(m, "__file__", None) and Path(m.__file__).resolve().is_relative_to(ROOT / "tools")}
    sources.update(HERE.glob("*.lean"))
    sources.update((HERE / "Wirelingo").glob("*.lean"))
    sources.update([Path(__file__).resolve(), HERE / "validate_gating.py", HERE / "audit_axioms.py",
                    HERE / "lakefile.toml", HERE / "lean-toolchain", PROBE,
                    ROOT / "src/pe_core.sv", ROOT / "docs/isa.md"])
    print(json.dumps({"result": "pass", "ticket": "C2-EUCLID-GATING", "profile": PROFILE,
        "closed_input_contract": "one initial state then only logical pins, enable, restart, PC-cancel",
        "oracle_model_fields_fed_back": [], "traces": traces, "comparison_model_cycles": cycles,
        "between_tick_capture_events": captures, "mutants_rejected": rejected,
        "source_sha256": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(sources)}}, indent=2))


if __name__ == "__main__":
    main()
