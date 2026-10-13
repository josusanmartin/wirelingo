"""UART model regressions with synchronized pin inputs.

The historical random-phase test changes *bit period* by +/-3%, not true
baud, and drains between bits. The pin-timeline tests use true baud offsets,
fixed initial phases and bounded alternating jitter. Host FIFO reads consume
model clocks without pausing their independent transmitter timeline.
"""
import bisect
from fractions import Fraction
import json
import os
import pathlib
import random

import pytest

from pe import asm, host, isa, sim
from pe.modelhost import ModelHost

FW = pathlib.Path(__file__).resolve().parents[2] / "firmware"
F_CLK, BAUD = 48e6, 115200
RX = 16          # ui0
V24 = 24         # virtual pin: a wire between cores
DIV = host.clkdiv_for(F_CLK, 8 * BAUD)


def rx_core(mh, core, pin):
    prog = asm.assemble((FW / "uart_rx.pasm").read_text(), {"RX": pin})
    mh.apply(host.load_program(core, prog, in_pins=pin, clkdiv=DIV))


@pytest.mark.parametrize("period_ppm", [0, 30000, -30000])
def test_uart_rx_back_to_back(period_ppm):
    mh = ModelHost()
    rx_core(mh, 0, RX)
    mh.ui = 1                                      # idle high
    mh.start(1)
    rng = random.Random(period_ppm)
    data = bytes(rng.getrandbits(8) for _ in range(64))
    bit = F_CLK / BAUD * (1 + period_ppm / 1e6)
    t = rng.random() * bit
    got = []
    for b in data:                                 # start, 8 data bits LSB first, stop
        for level in [0] + [(b >> i) & 1 for i in range(8)] + [1]:
            mh.ui = level
            t += bit
            n = int(t)
            t -= n
            mh.step(n)
            got += mh.pop_all(0)
    mh.step(2000)
    got += mh.pop_all(0)
    assert bytes(got) == data and mh.chip.flags & 1 == 0


def test_uart_tx_to_rx_over_virtual_pin():
    mh = ModelHost()
    tx = asm.assemble((FW / "uart_tx.pasm").read_text())
    mh.apply(host.load_program(0, tx, out_pins=(V24, 1), side_pins=V24, clkdiv=DIV))
    rx_core(mh, 1, V24)
    mh.start(0b11)
    data = bytes(range(0, 256, 7))
    got = []
    for b in data:
        while mh.tx_level(0) >= 8:
            mh.step(50)
            got += mh.pop_all(1)
        mh.push(0, b)
    for _ in range(400):
        mh.step(100)
        got += mh.pop_all(1)
    assert bytes(got) == data


_MODEL_CLOCKS = 0
_PATTERN = bytes([0x00, 0xFF, 0x55, 0xAA, 0x5A, 0xC3, 0x19, 0xE7])


def _artifact(name, result):
    """Optional diagnostics; normal pytest runs do not write trace artifacts."""
    folder = os.environ.get("UART_TRACE_DIR")
    if folder:
        path = pathlib.Path(folder)
        path.mkdir(parents=True, exist_ok=True)
        (path / (name + ".json")).write_text(json.dumps(result, indent=2) + "\n")


@pytest.fixture(autouse=True)
def _count_uart_clocks(monkeypatch, request):
    """Count every Chip.step, including loader, read/pop and helper calls."""
    global _MODEL_CLOCKS
    before = _MODEL_CLOCKS
    original = sim.Chip.step
    limit = int(os.environ.get("UART_MODEL_CLOCK_LIMIT", "2500000"))

    def counted(chip, *args, **kwargs):
        global _MODEL_CLOCKS
        assert _MODEL_CLOCKS < limit, "UART model-clock budget exhausted"
        _MODEL_CLOCKS += 1
        return original(chip, *args, **kwargs)

    monkeypatch.setattr(sim.Chip, "step", counted)
    yield
    clocks = _MODEL_CLOCKS - before
    request.node.user_properties.append(("model_clocks", clocks))
    _artifact("clocks-" + request.node.name, {"test": request.node.name,
              "model_clocks": clocks, "invocation_total": _MODEL_CLOCKS})


class _PinTimeline:
    """Precomputed, cycle-only transmitter; never consults DUT execution state."""
    def __init__(self, origin, baud_ppm, jitter):
        self.period = Fraction(48000000 * 1000000, 115200 * (1000000 + baud_ppm))
        self.cursor = Fraction(origin)
        self.jitter = jitter
        self.bit_number = 0
        self.edges = [(0, 1)]
        self.windows = []
        self.frames = []

    def frame(self, byte, stop=1):
        index = len(self.frames)
        frame = {"index": index, "byte": byte, "start": int(self.cursor), "stop": stop}
        for bit, level in enumerate([0] + [(byte >> b) & 1 for b in range(8)] + [stop]):
            begin = int(self.cursor)
            delta = self.jitter if self.bit_number % 2 == 0 else -self.jitter
            self.cursor += self.period + delta
            self.bit_number += 1
            end = int(self.cursor)
            self.edges.append((begin, level))
            self.windows.append({"begin": begin, "end": end, "frame": index,
                                 "slot": bit, "level": level})
        frame["end"] = int(self.cursor)
        self.frames.append(frame)

    def gap(self, bits, level):
        self.edges.append((int(self.cursor), level))
        self.cursor += self.period * bits

    def finish(self):
        self.edges.append((int(self.cursor), 1))
        self.edge_cycles = [c for c, _ in self.edges]
        self.window_cycles = [w["begin"] for w in self.windows]

    def level(self, cycle):
        return self.edges[bisect.bisect_right(self.edge_cycles, cycle) - 1][1]

    def window(self, cycle):
        index = bisect.bisect_right(self.window_cycles, cycle) - 1
        if index >= 0 and cycle < self.windows[index]["end"]:
            return self.windows[index]
        return None


def _receive_timeline(baud_ppm, phase, jitter, broken=False):
    mh = ModelHost()
    mh.ui = 1
    prog = asm.assemble((FW / "uart_rx.pasm").read_text(), {"RX": RX})
    mh.apply(host.load_program(0, prog, in_pins=RX, clkdiv=DIV))
    # Fixed idle interval relative to completed loading, plus the declared phase.
    source = _PinTimeline(mh.chip.cycle + 1664 + phase, baud_ppm, jitter)
    if broken:
        source.frame(0xA5, stop=0)
        source.gap(12, 0)             # extended low break following the bad STOP
        source.gap(2, 1)              # recovery idle; no host clears the sticky F0
        data = bytes([0xA5, 0x5A, 0xFF, 0x00])
        for byte in data[1:]:
            source.frame(byte)
    else:
        data = _PATTERN
        for byte in data:
            source.frame(byte)
    source.finish()
    events, rearmed, got = [], [], []
    core = mh.chip.cores[0]
    original = mh.chip.step
    stop_pc = prog.labels["last"] + 3
    pending_rearm = None
    max_fifo = 0
    blocked_push = 0

    def observed(ui_in=0, uio_in=0, **kwargs):
        nonlocal pending_rearm, max_fifo, blocked_push
        chip = mh.chip
        cycle = chip.cycle
        raw = source.level(cycle)
        logical = (chip.pins_in() >> RX) & 1
        # Two-clock source age includes every host read/loader helper step.
        assert chip.sync_bypass == 0
        assert logical == source.level(cycle - 2)
        active = chip.enable & 1 and core.cnt == 0 and core.dcnt == 0 and not core.halted
        pc = core.pc
        op, _, operand = isa.fields(chip.fetch(pc))
        event = None
        if active:
            kind = None
            if pc == prog.labels["start"]:
                kind = "start_wait"
                if pending_rearm is not None:
                    rearmed.append({"stop_cycle": pending_rearm, "attempt_cycle": cycle})
                    pending_rearm = None
            elif op == isa.OP_IN and isa.bits(operand, 18, 15) == isa.PINS:
                kind = "data"
            elif pc == stop_pc:
                kind = "stop"
                pending_rearm = cycle
            elif op == isa.OP_PUSH:
                kind = "push"
                blocked_push += int(len(core.rxf) == chip.fifo_depth)
            if kind:
                event = {"kind": kind, "cycle": cycle, "source_cycle": cycle - 2,
                         "pc": pc, "logical": logical, "raw": raw,
                         "window": source.window(cycle - 2)}
        before_flag = chip.flags & 1
        original(ui_in=raw, uio_in=uio_in, **kwargs)
        max_fifo = max(max_fifo, len(core.rxf))
        if event and (event["kind"] != "start_wait" or core.pc != pc):
            events.append(event)
        if not before_flag and chip.flags & 1:
            events.append({"kind": "framing_flag", "cycle": cycle})

    mh.chip.step = observed
    mh.start(1)
    # Poll/drain on a fixed 64-clock cadence, while the independent source runs
    # during the snap/commit/cancel calls too. Never choose stimulus from FIFO/PC.
    end = int(source.cursor) + 1000
    while mh.chip.cycle < end:
        mh.step(min(64, end - mh.chip.cycle))
        got.extend(mh.pop_all(0))
    result = {"baud_ppm": baud_ppm, "phase_clocks": phase, "jitter_clocks": jitter,
              "period_rational": [source.period.numerator, source.period.denominator],
              "expected": list(data), "received": got, "flags": mh.chip.flags,
              "frames": source.frames, "windows": source.windows, "external_edges": source.edges,
              "events": events, "rearm": rearmed, "max_fifo_words": max_fifo,
              "blocked_push_attempts": blocked_push, "model_clocks": mh.chip.cycle,
              "program_words": len(prog.words), "divider_256": DIV, "broken": broken}
    name = "timeline-%d-%d-%d-%s" % (baud_ppm, phase, jitter, broken)
    _artifact(name, result)          # retained before assertions, including failures
    return result


def _check_windows(result):
    """Pin-defined windows catch late apertures even if equal bits hide errors."""
    for index, frame in enumerate(result["frames"]):
        samples = [e for e in result["events"] if e["kind"] in ("start_wait", "data", "stop")
                   and e["window"] and e["window"]["frame"] == index]
        assert [e["window"]["slot"] for e in samples] == list(range(10)), (index, samples)
        assert [e["kind"] for e in samples] == ["start_wait"] + ["data"] * 8 + ["stop"]
        for event in samples:
            window = event["window"]
            assert window["begin"] <= event["source_cycle"] < window["end"], event
            assert event["logical"] == window["level"], event
            if event["kind"] != "start_wait":
                assert window["begin"] < event["source_cycle"], event
    assert result["blocked_push_attempts"] == 0
    assert result["max_fifo_words"] == 1


@pytest.mark.parametrize("baud_ppm", [-30000, 0, 30000])
@pytest.mark.parametrize("phase", [0, 13, 51])
@pytest.mark.parametrize("jitter", [0, 2], ids=["ideal", "alternating-2clk"])
def test_uart_rx_pin_timeline(baud_ppm, phase, jitter):
    result = _receive_timeline(baud_ppm, phase, jitter)
    assert bytes(result["received"]) == _PATTERN
    assert result["flags"] & 1 == 0
    _check_windows(result)
    assert len(result["rearm"]) == 8
    # STOP -> level-WAIT attempt takes exactly one fractional divider tick.
    assert {r["attempt_cycle"] - r["stop_cycle"] for r in result["rearm"]} <= {52, 53}
    for frame, rearm in zip(result["frames"][:-1], result["rearm"]):
        next_frame = result["frames"][frame["index"] + 1]
        # Rearm may be after the physical falling edge: level WAIT still acquires
        # within START. Do not incorrectly require an edge detector or early rearm.
        start_end = result["windows"][(frame["index"] + 1) * 10]["end"]
        assert rearm["attempt_cycle"] - 2 < start_end
        assert rearm["stop_cycle"] - 2 < next_frame["start"]


def test_uart_rx_bad_stop_break_recovery():
    result = _receive_timeline(0, 51, 0, broken=True)
    assert result["received"] == result["expected"]  # bad byte was pushed before STOP
    assert result["flags"] & 1 == 1                  # sticky; no automatic filtering
    assert len([e for e in result["events"] if e["kind"] == "framing_flag"]) == 1
    _check_windows(result)
    assert result["rearm"][0]["attempt_cycle"] > result["frames"][0]["end"] + 12 * float(F_CLK / BAUD)
