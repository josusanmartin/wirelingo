#!/usr/bin/env python3
"""C1-EUCLID-WAIT: bounded clock-cycle differential traces and targeted mutants.

Only WAIT RISE/FALL/EDGE/DIFF at PC0 wrapping to PC0. No host writes except
PC0 cancellation, and registered CTRL restart (never simultaneous with PC).
Input pins come from actual Chip synchronizer stepping; execution gates/tick
are sampled from the model, not proved by Lean. Seeded halt/fault/delay states
exercise source gates without asserting reachability. Cold reset has no model
rst_n API: compare its projection against a new Core, separately from traces.
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

PROBE = HERE / ".lake/build/bin/event_probe"


def projection(c):
    return (c.in_wait, c.wprev, c.wevent, *c.r, c.t)


def configs():
    for kind, qen, qpol, timed, treg, budget, div, scenario in itertools.product(
            (2, 3, 4), range(2), range(2), range(2), range(4),
            (0, 2, 257), (1, 2, 4), range(8)):
        x = (kind << 16) | (timed << 10) | (treg << 8) | (qen << 6) | (qpol << 5) | 1
        yield (x, budget, div, scenario, 0, 0)
    for ref, treg, mask, timed, budget, div, scenario in itertools.product(
            range(4), range(4), (0, 1, 255), range(2),
            (0, 2, 257), (1, 2, 4), range(8)):
        x = (7 << 16) | (ref << 14) | (timed << 10) | (treg << 8) | mask
        yield (x, budget, div, scenario, (0, 16, 31)[ref % 3], ref)


def trace(config, stale=True):
    x, budget, div, scenario, base, ref = config
    chip = sim.Chip()
    c = chip.cores[0]
    c.wrap_top = c.wrap_bottom = 0
    c.div_int = div
    c.in_base = base
    c.r = [0, 0x55, 0xAA, 0xFF]
    c.r[(x >> 8) & 3] = budget
    c.wevent = int(stale)
    chip.imem[0] = isa.enc(isa.OP_WAIT, 0, x)
    chip.enable = 1
    chip.sync1 = chip.sync2 = 2  # Known initial qualifier pin, signal pin low.
    rows, specs = [], []
    for clock in range(12):
        chip.enable = int(not (scenario == 2 and clock in (2, 3, 4)))
        if scenario == 4 and clock == 3:
            c.dcnt = 2
        if scenario == 6 and clock == 3:
            c.halted = 1
        if scenario == 7 and clock == 3:
            c.fault = 1
        restart = scenario in (3, 6, 7) and clock == 6
        if restart:
            chip.ctrl_pend = 0x101  # Actual already-registered CTRL restart+enable.
        pcw = scenario == 5 and clock == 4
        pins = chip.pins_in()
        tick = c.cnt == 0
        execute = tick and not (c.halted or c.fault or c.dcnt) and c.pc >> 8 == c.idx
        inputs = (*projection(c), x, pins, c.in_base, chip.enable,
                  int(execute), 0, int(restart), int(pcw))
        signal = int(clock in (1, 7))
        # A blocked edge and a later qualified edge; qualifier is sampled on the edge.
        qualifier = int(not (scenario == 1 and clock <= 4))
        pads = signal | (qualifier << 1)
        chip.step(uio_in=pads, ui_in=pads, we=pcw,
                  addr=sim.core_reg(0, sim.C_PC), wdata=0)
        rows.append((inputs, projection(c)))
        specs.append({"clock": clock, "pads": pads, "restart": restart, "pc_write": pcw})
    return rows, specs, chip.stats


def compare(rows, label):
    result = subprocess.run([str(PROBE)], input="".join(
        " ".join(map(str, inputs)) + "\n" for inputs, _ in rows),
        text=True, capture_output=True, check=True, timeout=25)
    lines = result.stdout.splitlines()
    if len(lines) != len(rows):
        raise AssertionError(f"probe row count: {label}")
    for n, (line, (inputs, expected)) in enumerate(zip(lines, rows)):
        actual = tuple(map(int, line.split()))
        if actual != expected:
            raise AssertionError(json.dumps({"label": label, "row": n, "input": inputs,
                                             "Lean": actual, "model": expected}))


@contextmanager
def mutation(name):
    divider, track, wait = sim.Core._divider, sim.Core._track_wait, sim.Core._op_wait
    old_dispatch = sim._OPS[isa.OP_WAIT]
    if name == "tick-only-sampling":
        def tick(core):
            core._c1_tick = divider(core)
            return core._c1_tick
        def tick_track(core, e):
            if core._c1_tick:
                track(core, e)
        sim.Core._divider, sim.Core._track_wait = tick, tick_track
    else:
        def stale_wait(core, e):
            first, stale = not core.in_wait, core.wevent
            wait(core, e)
            if first and not e.complete:
                core.wevent = stale
        sim.Core._op_wait = sim._OPS[isa.OP_WAIT] = stale_wait
    try:
        yield
    finally:
        sim.Core._divider, sim.Core._track_wait, sim.Core._op_wait = divider, track, wait
        sim._OPS[isa.OP_WAIT] = old_dispatch


def main():
    traces = cycles = captured = alias_traces = 0
    batch = []
    for config in configs():
        rows, _, stats = trace(config)
        batch.extend(rows)
        traces += 1
        cycles += len(rows)
        captured += stats.get("wait:edge_caught_between_ticks", 0)
        captured += stats.get("wait:diff_caught_between_ticks", 0)
        if (config[0] >> 16) == 7 and ((config[0] >> 14) & 3) == ((config[0] >> 8) & 3):
            alias_traces += 1
        if len(batch) >= 4096:
            compare(batch, f"trace-prefix-{traces}")
            batch.clear()
    if batch:
        compare(batch, "final-batch")
    if not captured or not alias_traces or traces > 200000:
        raise AssertionError("missing non-tick retention/alias coverage or exceeded trace budget")
    # Cold-reset projection only: the Python model has no rst_n step argument.
    resets = []
    for iw, prev, ev, t in itertools.product(range(2), repeat=4):
        inputs = (iw, prev, ev, 1, 2, 3, 4, t, 2 << 16, 0, 0, 1, 1, 1, 0, 1)
        resets.append((inputs, projection(sim.Core(0))))
    compare(resets, "cold-reset-projection")
    rejected = []
    for name in ("tick-only-sampling", "stale-first-entry"):
        config = ((2 << 16) | (1 << 10) | (3 << 8), 257, 4, 0, 0, 0)
        with mutation(name):
            rows, specs, _ = trace(config, stale=name == "stale-first-entry")
        try:
            compare(rows, name)
        except AssertionError as error:
            # Retain the mismatch and the concrete chronological input trace for review.
            rejected.append({"mutation": name, "counterexample": json.loads(str(error)),
                             "configuration": config, "drive": specs})
        else:
            raise AssertionError(f"mutation accepted: {name}")
    sources = {Path(module.__file__).resolve() for module in list(sys.modules.values())
               if getattr(module, "__file__", None) and
               Path(module.__file__).resolve().is_relative_to(ROOT / "tools")}
    sources.update(HERE.glob("*.lean"))
    sources.update((HERE / "Wirelingo").glob("*.lean"))
    sources.update([Path(__file__).resolve(), HERE / "audit_axioms.py",
                    HERE / "lakefile.toml", HERE / "lean-toolchain", PROBE,
                    ROOT / "src/pe_core.sv", ROOT / "docs/isa.md"])
    print(json.dumps({"result": "pass", "ticket": "C1-EUCLID-WAIT",
                      "model_traces": traces, "model_cycles": cycles,
                      "cold_reset_projections": len(resets), "non_tick_capture_events": captured,
                      "diff_timeout_alias_traces": alias_traces, "mutations_rejected": rejected,
                      "source_sha256": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                                        for p in sorted(sources)}}, indent=2))


if __name__ == "__main__":
    main()
