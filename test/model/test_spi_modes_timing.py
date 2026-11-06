"""SPI schedule measurements and timing assumptions on the tapeout model.

SPI_METRICS_OUT optionally records measured rows; no log is needed to run
the tests. Timing comes from actual independent-peer edges and model traces.
"""
import copy
import hashlib
import json
import os
from pathlib import Path

import pytest

from pe import asm, isa, timing
from test_spi_modes import FW, TX, RESP, SCK, setup, step, action, pop


def variant(mode, schedule):
    name = f"spi_master_{'unrolled_' if schedule == 'unrolled' else ''}cpha{mode & 1}"
    return next(p for p in asm.assemble_all((FW / "spi_master_modes.pasm").read_text(),
                                          {"CPOL": mode >> 1}) if p.name == name)


def record(row, environment="SPI_METRICS_OUT"):
    if path := os.environ.get(environment):
        file = Path(path)
        rows = json.loads(file.read_text()) if file.exists() else []
        rows.append(row)
        file.write_text(json.dumps(rows, indent=2) + "\n")


def measure_frame(mode, divider, delay, prog):
    tx = (TX * 2)[:8]
    response = (RESP * 2)[:8]
    mh, peer = setup(mode, divider << 8, delay=delay, prog=prog, response=response)
    assert (mh.chip.ncores, mh.chip.nbanks, mh.chip.fifo_depth) == (2, 2, 8)
    assert mh.chip.sync_bypass == 0
    for byte in tx:
        mh.push(0, byte << 24)
    trace = timing.Tracer(mh.chip, 0, 0)
    mh.start(1)
    step(mh, peer, 2200)
    assert peer.rx == tx and mh.chip.cores[0].rxf == response
    assert len(peer.edges) == 16 * len(tx)
    assert ((mh.chip.pin_out >> SCK) & 1) == mode >> 1
    pull_pc = next(i for i, word in enumerate(prog.words) if isa.fields(word)[0] == isa.OP_PULL)
    pulls, previous = [], None
    for pc, tick, cycle, completed in trace.attempts:
        if pc == pull_pc and previous != pull_pc:
            pulls.append(tick)
        previous = pc
    assert len(pulls) == 9  # eight sent bytes, then an idle empty-PULL attempt
    frame_ticks = [b - a for a, b in zip(pulls, pulls[1:])]
    active = [peer.edges[i + 1][0] - peer.edges[i][0]
              for i in range(0, len(peer.edges), 2)]
    idle = [peer.edges[i + 1][0] - peer.edges[i][0]
            for i in range(1, len(peer.edges) - 1, 2)]
    row = dict(program=prog.name, mode=mode, divider=divider, launch_delay_clocks=delay,
               profile=dict(cores=2, banks=2, bank_words=256, fifo_depth=8, sync_bypass=0),
               words=len(prog.words), program_bytes=4 * len(prog.words),
               byte_ticks=sorted(set(frame_ticks)), batch_ticks=pulls[-1] - pulls[0],
               batch_clocks=(pulls[-1] - pulls[0]) * divider,
               wire_span_clocks=peer.edges[-1][0] - peer.edges[0][0],
               active_clocks=sorted(set(active)), idle_clocks=sorted(set(idle)),
               active_ticks=sorted(set(n // divider for n in active)),
               idle_ticks=sorted(set(n // divider for n in idle)),
               transmitted=tx, received=response, edges=len(peer.edges),
               host_io_during_batch=0,
               instruction_sha256=hashlib.sha256(b"".join(w.to_bytes(4, "little") for w in prog.words)).hexdigest())
    return row, mh, peer, trace


@pytest.mark.parametrize("mode", range(4))
@pytest.mark.parametrize("divider", [3, 4])
@pytest.mark.parametrize("delay", [1, 2])
@pytest.mark.parametrize("schedule", ["compact", "unrolled"])
def test_tapeout_frame_measurements(mode, divider, delay, schedule):
    row, _, _, _ = measure_frame(mode, divider, delay, variant(mode, schedule))
    byte_ticks = 32 if schedule == "unrolled" else 34
    idle = 3 if mode & 1 else 2
    assert row["byte_ticks"] == [byte_ticks]
    assert row["batch_ticks"] == 8 * byte_ticks
    assert row["active_ticks"] == [1 if mode & 1 else 2]
    assert row["idle_ticks"] == ([idle] if schedule == "unrolled" else [idle, idle + 2])
    record(row)


@pytest.mark.parametrize("mode", range(4))
@pytest.mark.parametrize("schedule", ["compact", "unrolled"])
def test_variant_tx_starvation(mode, schedule):
    mh, peer = setup(mode, 3 << 8, delay=2, prog=variant(mode, schedule))
    mh.start(1)
    step(mh, peer, 101)
    assert not peer.edges
    for index, byte in enumerate(TX):
        action(mh, peer, mh.push, 0, byte << 24)
        step(mh, peer, 200 + 7 * index)
        assert pop(mh, peer) == RESP[index]
        assert peer.rx == TX[:index + 1]
        assert len(peer.edges) == 16 * (index + 1)
        assert ((mh.chip.pin_out >> SCK) & 1) == mode >> 1
        step(mh, peer, 53 + 11 * index)
        assert len(peer.edges) == 16 * (index + 1)


def backpressure(mode, divider, delay, prog, pause_level=None):
    tx, response = (TX * 2)[:9], [0xA5] * 9  # final MISO bit differs from following idle 0
    mh, peer = setup(mode, divider << 8, delay=delay, prog=prog, response=response)
    for byte in tx[:8]:
        mh.push(0, byte << 24)
    trace = timing.Tracer(mh.chip, 0, 0)
    mh.start(1)
    step(mh, peer, 100)
    action(mh, peer, mh.push, 0, tx[-1] << 24)
    step(mh, peer, 1800)
    assert mh.chip.cores[0].rxf == response[:8] and peer.rx == tx
    assert mh.chip.cores[0].stalled
    expected = mode >> 1 if mode & 1 else 1 - (mode >> 1)
    assert ((mh.chip.pin_out >> SCK) & 1) == (expected if pause_level is None else pause_level)
    paused = list(peer.edges)
    step(mh, peer, 137)
    assert peer.edges == paused
    got = [pop(mh, peer)]
    step(mh, peer, 100)
    while mh.chip.cores[0].rxf:
        got.append(pop(mh, peer))
    assert len(peer.edges) == 16 * len(tx)
    return got, response, trace


@pytest.mark.parametrize("mode", range(4))
@pytest.mark.parametrize("schedule", ["compact", "unrolled"])
@pytest.mark.parametrize("divider", [3, 4])
@pytest.mark.parametrize("delay", [1, 2])
def test_tapeout_rx_full_sample_phase(mode, schedule, divider, delay):
    got, response, trace = backpressure(mode, divider, delay, variant(mode, schedule))
    assert got == response
    checker, results = static_check(mode, schedule, divider)
    result = next(r for r in results if r.deadline.name == "byte_no_rx_pause")
    measurements = timing.measure(checker, result.deadline, trace)
    clean = [m for m in measurements if not m.violations]
    broken = [m for m in measurements if m.violations]
    assert clean and all(result.contains(m.ticks, m.clocks) for m in clean)
    assert broken and any(m.ticks > result.hi for m in broken)
    record(dict(kind="runtime_rx_pause", mode=mode, schedule=schedule, divider=divider,
                launch_delay_clocks=delay, clean_intervals=len(clean),
                assumption_violations=[dict(ticks=m.ticks, violations=m.violations) for m in broken]),
           "SPI_TIMING_OUT")


def static_check(mode, schedule, divider=3):
    source = (FW / "spi_master_modes.pasm").read_text()
    return timing.check_source(source, {"CPOL": mode >> 1}, variant(mode, schedule).name,
                               (FW / "spi_master_modes.timing").read_text(), clkdiv=divider)


@pytest.mark.parametrize("mode", range(4))
@pytest.mark.parametrize("schedule", ["compact", "unrolled"])
def test_static_host_assumptions_are_explicit(mode, schedule):
    checker, results = static_check(mode, schedule)
    assert not checker.errors
    by_name = {r.deadline.name: r for r in results}
    assert by_name["byte_unserviced"].status == "FAIL (UNBOUNDED)"
    expected = 32 if schedule == "unrolled" else 34
    for name in ("byte_no_rx_pause", "byte_no_fifo_pause"):
        result = by_name[name]
        assert result.status == "PASS" and (result.lo, result.hi) == (expected, expected)
        assert "conditional" in result.deadline.why and result.assumptions
    record(dict(kind="static", mode=mode, schedule=schedule,
                reports=[r.report() for r in results]), "SPI_TIMING_OUT")


@pytest.mark.parametrize("mode", [0, 2])
@pytest.mark.parametrize("divider", [3, 4])
@pytest.mark.parametrize("delay", [1, 2])
def test_negative_trailing_edge_on_stalled_in_corrupts_final_bit(mode, divider, delay):
    # Negative control, not a third optimization candidate: move CPHA0's
    # trailing edge onto IN. Side-set acts even if RX-full prevents sampling.
    # The peer then launches the next MISO bit during the stall; retrying IN
    # eventually captures that next bit instead of the byte's last bit.
    mutant = copy.deepcopy(variant(mode, "compact"))
    pc = mutant.labels["sample"]
    op, sd, operand = isa.fields(mutant.words[pc])
    mutant.words[pc] = isa.enc(op, (sd & 0x7F) | ((mode >> 1) << 7), operand)
    got, response, _ = backpressure(mode, divider, delay, mutant, pause_level=mode >> 1)
    assert got[:8] == response[:8] and got[-1] == 0xA4 and response[-1] == 0xA5
    record(dict(kind="negative_stall_order", mode=mode, divider=divider,
                launch_delay_clocks=delay, expected=response, received=got,
                reason="IN issued trailing edge before RX-full sample completed"), "SPI_NEGATIVE_OUT")
