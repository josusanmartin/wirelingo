"""Finite observation traces: retain entry evidence without inventing completion.

These regressions consume recorded attempts directly and advance no model clocks.
A pending instance means incomplete at the observation boundary, not proof that
the instruction can never complete. Same-PC restart without an observed event
cannot be distinguished here; reset/trace epoch semantics are outside this test.
"""
import json
import os
from pathlib import Path

import pytest

from pe import timing


def observation(attempts):
    # Bypass hook installation: instances()/measure() need only these records.
    tracer = object.__new__(timing.Tracer)
    tracer.attempts = list(attempts)
    tracer.cycles = []  # No condition deadlines or fabricated model events.
    return tracer


@pytest.mark.parametrize("attempts,expected", [
    ([], []),
    ([(0, 1, 3, True), (0, 2, 6, True)],
     [(0, 1, 3, 1, 3, 1), (0, 2, 6, 2, 6, 1)]),
    ([(3, 2, 6, False), (3, 3, 9, False), (3, 4, 12, False)],
     [(3, 2, 6, None, None, 3)]),
    ([(3, 1, 3, True), (3, 2, 6, False), (3, 3, 9, False)],
     [(3, 1, 3, 1, 3, 1), (3, 2, 6, None, None, 2)]),
    ([(1, 4, 12, False), (1, 5, 15, False), (2, 6, 18, True)],
     [(1, 4, 12, None, None, 2), (2, 6, 18, 6, 18, 1)]),
], ids=["empty", "completed-same-pc", "terminal-stalled",
        "completed-then-pending-same-pc", "interrupted-pending"])
def test_observed_instruction_instances(attempts, expected):
    tracer = observation(attempts)
    assert tracer.instances() == expected
    # Reading the finite trace twice must neither consume nor extend evidence.
    assert tracer.instances() == expected and tracer.attempts == attempts


SOURCE = """
.shift_in left autopush 1
; @assume clkdiv=3
; @deadline enter from=start.done to=terminal.enter min=2 max=2 unit=tick stall=sample:0
; @deadline done from=start.done to=terminal.done min=2 max=2 unit=tick stall=sample:0,terminal:0
start: nop
sample: in pins, 1
terminal: pull block
halt
"""

# Concrete synthetic observation: the RX/sample IN stalls twice, completes,
# and the following empty TX PULL is still pending at the finite trace end.
ATTEMPTS = [(0, 5, 15, True), (1, 6, 18, False), (1, 7, 21, False),
            (1, 8, 24, True), (2, 9, 27, False), (2, 10, 30, False)]


def bounds():
    checker, results = timing.check_source(SOURCE)
    assert not checker.errors
    assert all(r.status == "PASS" and (r.lo, r.hi) == (2, 2) for r in results)
    return checker, {r.deadline.name: r for r in results}


def test_terminal_entry_is_observable_without_completion():
    checker, results = bounds()
    tracer = observation(ATTEMPTS)
    # No terminal.done event may be inferred from the last attempted tick.
    assert timing.measure(checker, results["done"].deadline, tracer) == []
    intervals = timing.measure(checker, results["enter"].deadline, tracer)
    assert len(intervals) == 1
    interval = intervals[0]
    assert (interval.ticks, interval.clocks, interval.start_cycle) == (4, 12, 15)
    assert tracer.instances()[-1] == (2, 9, 27, None, None, 2)


def test_completed_sample_stall_violates_zero_stall_before_terminal_entry():
    checker, results = bounds()
    tracer = observation(ATTEMPTS)
    intervals = timing.measure(checker, results["enter"].deadline, tracer)
    assert len(intervals) == 1
    interval = intervals[0]
    assert interval.violations == ["stall 2 > assumed 0 at pc 1"]
    assert interval.ticks > results["enter"].hi
    # Retain the attempts and measured witness from the focused run itself.
    target = os.environ.get("TRACE_OBSERVATIONS_OUT")
    if target:
        Path(target).write_text(json.dumps(dict(
            source=SOURCE, attempts=ATTEMPTS, instances=tracer.instances(),
            measured_enter=vars(interval), measured_done=[], model_cycles=0),
            indent=2) + "\n")


def test_trace_only_false_terminal_completion_is_rejected():
    checker, results = bounds()
    tracer = observation(ATTEMPTS)
    assert timing.measure(checker, results["done"].deadline, tracer) == []
    corrupted = list(ATTEMPTS)
    pc, tick, cycle, _ = corrupted[-1]
    corrupted[-1] = (pc, tick, cycle, True)
    # Negative control: falsely marking a stall done must fail the same
    # no-completion assertion. The tracer cannot authenticate supplied records.
    with pytest.raises(AssertionError):
        assert timing.measure(checker, results["done"].deadline,
                              observation(corrupted)) == []
