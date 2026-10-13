"""Optional packed UART: finite no-service capture, timing and partial groups.

Uses R13's precomputed independent transmitter, not a DUT-driven loopback.
Only this file is needed: PYTHONPATH=tools python3 -m pytest -q
 test/model/test_uart_packed.py. It does not execute the byte UART test suite.
"""
import json
import os
import pathlib

import pytest

from pe import asm, host, isa, sim
from pe.modelhost import ModelHost
from pe.profile import TAPEOUT
from test_uart import _PinTimeline

FW = pathlib.Path(__file__).resolve().parents[2] / "firmware"
RX = 16
DIV = host.clkdiv_for(48000000, 8 * 115200)
BURST = bytes.fromhex("00ff55aa5ac319e701020408102040807e813cc19669123456789abcdef027d8")
_CLOCKS = 0


def _save(name, result):
    folder = os.environ.get("UART_PACK_TRACE_DIR")
    if folder:
        path = pathlib.Path(folder)
        path.mkdir(parents=True, exist_ok=True)
        (path / (name + ".json")).write_text(json.dumps(result, indent=2) + "\n")


@pytest.fixture(autouse=True)
def _count_clocks(monkeypatch, request):
    """Every Chip.step, including load/start/drain calls, costs one clock."""
    global _CLOCKS
    before = _CLOCKS
    original = sim.Chip.step
    limit = int(os.environ.get("UART_PACK_MODEL_CLOCK_LIMIT", "3000000"))

    def counted(chip, *args, **kwargs):
        global _CLOCKS
        assert _CLOCKS < limit, "packed UART model-clock budget exhausted"
        _CLOCKS += 1
        return original(chip, *args, **kwargs)

    monkeypatch.setattr(sim.Chip, "step", counted)
    yield
    clocks = _CLOCKS - before
    request.node.user_properties.append(("model_clocks", clocks))
    _save("clocks-" + request.node.name, {"test": request.node.name,
          "model_clocks": clocks, "invocation_total": _CLOCKS})


class _TapeoutHost(ModelHost):
    """Normal host operations on a fresh explicit tapeout-sized model."""
    def __init__(self):
        self.chip = sim.Chip(ncores=TAPEOUT.ncores, nbanks=TAPEOUT.nbanks,
                             bank_words=TAPEOUT.bank_words, fifo_depth=TAPEOUT.fifo_depth)
        self.ui = 1
        self.uio = self.ext_oe = self.pull_up = 0


def _drain(mh):
    # Real register snapshots/commits, never deque.popleft or direct FIFO edits.
    status = mh.read(sim.core_reg(0, sim.C_STATUS))
    level = (status >> 4) & 15
    return [mh.read(sim.core_reg(0, sim.C_FIFO), pop=True) for _ in range(level)]


def _capture(program_name, baud_ppm=0, phase=0, jitter=0, mode="burst"):
    # Entire external timeline and service deadline exist before any enabling.
    source = _PinTimeline(4096 + phase, baud_ppm, jitter)
    checkpoint = None
    if mode == "partial":
        data = bytes([0x00, 0xFF, 0x55, 0xAA])
        for byte in data[:3]:
            source.frame(byte)
        checkpoint = int(source.cursor) + 1024
        source.gap(8, 1)
        source.frame(data[3])
    elif mode == "broken":
        data = bytes([0xA5, 0x5A, 0xFF, 0x00])
        source.frame(data[0], stop=0)
        source.gap(12, 0)
        source.gap(2, 1)
        for byte in data[1:]:
            source.frame(byte)
    else:
        data = BURST
        for byte in data:
            source.frame(byte)
    source.finish()
    deadline = int(source.cursor) + 1024
    mh = _TapeoutHost()
    prog = asm.assemble((FW / (program_name + ".pasm")).read_text(), {"RX": RX})
    mh.apply(host.load_program(0, prog, in_pins=RX, clkdiv=DIV, profile=TAPEOUT))
    chip, core = mh.chip, mh.chip.cores[0]
    active_begin = chip.cycle + 1  # the ordinary start-write call returns here
    assert active_begin < source.frames[0]["start"]
    events, publications, flags, checkpoints, stalls = [], [], [], [], []
    original = chip.step
    counts = {"active_host_operations": 0, "post_deadline_host_strobes": 0,
              "PUSH_stalls": 0, "autopush_stalls": 0, "max_rx_words": 0}
    stop_pc = prog.labels["last"] + 3
    pending_rearm = None
    rearm = []

    def snapshot(cycle):
        return {"cycle": cycle, "rx_words": len(core.rxf), "isr": core.isr,
                "isr_bits": core.isr_cnt, "pc": core.pc, "flags": chip.flags}

    def observed(ui_in=0, uio_in=0, **kwargs):
        nonlocal pending_rearm
        cycle = chip.cycle
        strobes = any(kwargs.get(k, False) for k in ("we", "snap", "commit", "cancel"))
        if active_begin <= cycle < deadline and strobes:
            counts["active_host_operations"] += 1
            raise AssertionError("loading-host service inside the declared capture gap")
        if cycle >= deadline and strobes:
            counts["post_deadline_host_strobes"] += 1
        if cycle == checkpoint:
            checkpoints.append(snapshot(cycle))
        raw = source.level(cycle)
        logical = (chip.pins_in() >> RX) & 1
        assert chip.sync_bypass == 0 and logical == source.level(cycle - 2)
        pc = core.pc
        active = chip.enable & 1 and core.cnt == 0 and core.dcnt == 0 and not core.halted
        op, _, operand = isa.fields(chip.fetch(pc))
        kind = None
        before_isr, before_bits, before_flag = core.isr, core.isr_cnt, chip.flags & 1
        if active:
            if pc == prog.labels["start"]:
                kind = "start"
                if pending_rearm is not None:
                    rearm.append({"stop_cycle": pending_rearm, "attempt_cycle": cycle})
                    pending_rearm = None
            elif op == isa.OP_IN and isa.bits(operand, 18, 15) == isa.PINS:
                kind = "data"
            elif pc == stop_pc:
                kind = "stop"
                pending_rearm = cycle
            elif op == isa.OP_PUSH:
                kind = "push"
        eligible_auto = kind == "data" and core.autopush and before_bits == 31
        original(ui_in=raw, uio_in=uio_in, **kwargs)
        counts["max_rx_words"] = max(counts["max_rx_words"], len(core.rxf))
        if kind and (kind != "start" or core.pc != pc):
            event = {"kind": kind, "cycle": cycle, "source_cycle": cycle - 2,
                     "window": source.window(cycle - 2), "logical": logical,
                     "pc": pc, "stalled": bool(core.stalled)}
            if kind == "push" and core.stalled or eligible_auto and core.stalled:
                key = "PUSH_stalls" if kind == "push" else "autopush_stalls"
                counts[key] += 1
                if not stalls:
                    stalls.append(event)
            else:
                events.append(event)
            if (kind == "push" or eligible_auto) and not core.stalled:
                word = before_isr if kind == "push" else (before_isr >> 1) | (logical << 31)
                publications.append({"cycle": cycle, "word": word, "autopush": bool(eligible_auto)})
        if not before_flag and chip.flags & 1:
            flags.append({"cycle": cycle, "window": source.window(cycle - 2)})

    chip.step = observed
    mh.start(1)
    mh.step(deadline - chip.cycle)       # fixed endpoint; no reads or FIFO service
    at_deadline = snapshot(chip.cycle)
    words = _drain(mh)
    mh.step(1024)                        # fixed grace exposes a baseline pending ISR byte
    words.extend(_drain(mh))
    packed = program_name == "uart_rx_packed"
    got = b"".join(word.to_bytes(4, "little") for word in words) if packed else bytes(words)
    acquired = {e["window"]["frame"] for e in events if e["kind"] == "start"
                and e["cycle"] < deadline and e["window"] and e["window"]["slot"] == 0}
    validated = {e["window"]["frame"] for e in events if e["kind"] == "stop"
                 and e["cycle"] < deadline and e["window"] and e["window"]["slot"] == 9}
    result = {"program": program_name, "program_words": len(prog.words), "program_bytes": 4*len(prog.words),
              "geometry": {"ncores": chip.ncores, "nbanks": chip.nbanks,
                           "bank_words": chip.bank_words, "fifo_depth": chip.fifo_depth},
              "baud_ppm": baud_ppm, "phase_clocks": phase, "jitter_clocks": jitter, "mode": mode,
              "expected": list(data), "received": list(got), "words": words, "counts": counts,
              "active_begin": active_begin, "deadline": deadline, "at_deadline": at_deadline,
              "external_edges": source.edges, "frames": source.frames, "windows": source.windows,
              "events": events, "publications": publications, "framing_flags": flags,
              "rearm": rearm, "checkpoints": checkpoints, "first_stall": stalls,
              "first_unacquired_frame": next((i for i in range(len(data)) if i not in acquired), None),
              "first_unvalidated_STOP_frame": next((i for i in range(len(data)) if i not in validated), None),
              "model_clocks": chip.cycle, "final_isr_bits": core.isr_cnt,
              "final_rx_words": len(core.rxf), "final_flags": chip.flags}
    _save("trace-%s-%d-%d-%d-%s" % (program_name, baud_ppm, phase, jitter, mode), result)
    return result


def _assert_windows(result):
    for frame in result["frames"]:
        samples = [e for e in result["events"] if e["kind"] in ("start", "data", "stop")
                   and e["cycle"] < result["deadline"] and e["window"]
                   and e["window"]["frame"] == frame["index"]]
        assert [e["window"]["slot"] for e in samples] == list(range(10)), (frame, samples)
        for sample in samples:
            window = sample["window"]
            assert not sample["stalled"] and sample["logical"] == window["level"]
            assert window["begin"] <= sample["source_cycle"] < window["end"]
            if sample["kind"] != "start":
                assert window["begin"] < sample["source_cycle"]
    assert result["first_unacquired_frame"] is None
    assert result["first_unvalidated_STOP_frame"] is None
    assert result["counts"]["autopush_stalls"] == 0
    assert result["counts"]["active_host_operations"] == 0
    assert result["final_isr_bits"] == 0 and result["final_rx_words"] == 0


def _assert_burst(result):
    assert bytes(result["received"]) == BURST
    assert len(result["words"]) == result["at_deadline"]["rx_words"] == 8
    assert result["final_flags"] & 1 == 0
    assert len(result["publications"]) == 8
    assert result["program_words"] <= 17
    _assert_windows(result)


def test_same_burst_without_host_service():
    baseline = _capture("uart_rx")
    packed = _capture("uart_rx_packed")
    for field in ("external_edges", "frames", "windows", "deadline"):
        assert baseline[field] == packed[field]      # identical ordinary offered stream
    assert baseline["counts"]["active_host_operations"] == 0
    assert baseline["at_deadline"]["rx_words"] == 8
    assert baseline["counts"]["PUSH_stalls"] > 0
    # FIFO words and the pending ISR byte are distinct. After deadline and the
    # fixed grace/drain, the ninth captured byte is returned too; later frames lost.
    assert baseline["at_deadline"]["isr_bits"] == 32
    assert baseline["at_deadline"]["isr"] == BURST[8]
    assert bytes(baseline["received"]) == BURST[:9]
    assert baseline["first_unacquired_frame"] == 9
    assert baseline["first_unvalidated_STOP_frame"] == 8
    _assert_burst(packed)


# Nominal phase0 is already the candidate half of the single baseline comparison.
@pytest.mark.parametrize("baud_ppm,phase,jitter", [(-30000, 0, 0), (-30000, 51, 0),
    (0, 51, 0), (30000, 0, 0), (30000, 51, 0), (30000, 0, 2), (30000, 51, 2)])
def test_packed_baud_phase_windows(baud_ppm, phase, jitter):
    _assert_burst(_capture("uart_rx_packed", baud_ppm, phase, jitter))


def test_partial_group_waits_for_fourth_byte():
    result = _capture("uart_rx_packed", mode="partial")
    assert len(result["checkpoints"]) == 1
    checkpoint = result["checkpoints"][0]
    assert checkpoint["rx_words"] == 0 and checkpoint["isr_bits"] == 24
    assert checkpoint["isr"] == 0x55FF0000
    assert result["received"] == result["expected"] == [0x00, 0xFF, 0x55, 0xAA]
    assert result["words"] == [0xAA55FF00]
    assert len(result["publications"]) == 1
    assert result["publications"][0]["cycle"] > result["frames"][3]["start"]
    _assert_windows(result)


def test_bad_stop_break_retains_suspect_byte():
    result = _capture("uart_rx_packed", mode="broken")
    assert result["received"] == result["expected"] == [0xA5, 0x5A, 0xFF, 0x00]
    assert result["words"] == [0x00FF5AA5]
    assert result["final_flags"] & 1 == 1
    assert len(result["framing_flags"]) == 1
    _assert_windows(result)
