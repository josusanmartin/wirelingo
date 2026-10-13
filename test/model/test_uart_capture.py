"""Declared 1..32-byte UART capture: fixed wire input, paid host setup, STOP/HALT.

Existing UART/packed helpers are imported read-only; their old tests are not run.
"""
import json
import os
import pathlib

import pytest

from pe import asm, host, isa, sim, uartcapture
from pe.profile import TAPEOUT
from test_uart import _PinTimeline
from test_uart_packed import BURST, DIV, RX, _TapeoutHost

FW = pathlib.Path(__file__).resolve().parents[2] / "firmware"
_CLOCKS = 0


def _save(name, result):
    directory = os.environ.get("UART_CAPTURE_TRACE_DIR")
    if directory:
        p = pathlib.Path(directory)
        p.mkdir(parents=True, exist_ok=True)
        (p / (name + ".json")).write_text(json.dumps(result, indent=2) + "\n")


@pytest.fixture(autouse=True)
def _count_clocks(monkeypatch, request):
    global _CLOCKS
    before = _CLOCKS
    original = sim.Chip.step
    limit = int(os.environ.get("UART_CAPTURE_CLOCK_LIMIT", "3000000"))

    def counted(chip, *args, **kwargs):
        global _CLOCKS
        assert _CLOCKS < limit, "declared UART capture clock cap exhausted"
        _CLOCKS += 1
        return original(chip, *args, **kwargs)

    monkeypatch.setattr(sim.Chip, "step", counted)
    yield
    clocks = _CLOCKS - before
    request.node.user_properties.append(("model_clocks", clocks))
    _save("clocks-" + request.node.name, {"test": request.node.name,
          "model_clocks": clocks, "invocation_total": _CLOCKS})


class _ObservedHost(_TapeoutHost):
    def __init__(self):
        super().__init__()
        self.operations = []
        self.gaps = []

    def _guard(self, kind, addr):
        cycle = self.chip.cycle
        self.operations.append({"cycle": cycle, "kind": kind, "addr": addr})
        assert not any(a <= cycle < b for a, b in self.gaps), "active loading-host API call"

    def write(self, addr, data):
        self._guard("write", addr)
        self.operations[-1]["data"] = data
        return super().write(addr, data)

    def read(self, addr, pop=False):
        self._guard("read", addr)
        self.operations[-1]["pop"] = pop
        return super().read(addr, pop)


def _wire(length, origin=4096, baud_ppm=0, phase=0, jitter=0, *,
          data=None, bad_final=False, break_after=False, undersupply=False, extra=0xAA):
    data = BURST[:length] if data is None else bytes(data)
    offered = data[:4] if undersupply else data
    source = _PinTimeline(origin + phase, baud_ppm, jitter)
    for i, byte in enumerate(offered):
        source.frame(byte, stop=0 if bad_final and i == length-1 else 1)
    if break_after:
        source.gap(12, 0)
        source.gap(2, 1)
    if not undersupply:
        source.frame(extra)                # independent extra complete frame, must be ignored
    source.finish()
    return {"source": source, "length": length, "data": data,
            "baud_ppm": baud_ppm, "phase": phase, "jitter": jitter,
            "bad_final": bad_final, "undersupply": undersupply,
            "deadline": int(source.cursor) + 1024}


def _new_host(segments):
    """One cold normal load; all timelines/deadlines precomputed before enable."""
    mh = _ObservedHost()
    prog = asm.assemble((FW / "uart_rx_capture.pasm").read_text(), {"RX": RX})
    mh.apply(host.load_program(0, prog, in_pins=RX, clkdiv=DIV, profile=TAPEOUT))
    chip, core = mh.chip, mh.chip.cores[0]
    original = chip.step
    trace = {"program_words": len(prog.words), "cold_load_writes": len(mh.operations),
             "events": [], "publications": [], "flag_events": [], "halts": [],
             "active_bus_strobes": 0, "bus_strobes": 0, "stalls": 0, "peak_rx": 0}

    def wire_at(cycle):
        # Selection is fixed time, never PC, flags, registers, enable or FIFO state.
        index = max(i for i, segment in enumerate(segments)
                    if i == 0 or cycle >= segment["source"].frames[0]["start"])
        source = segments[index]["source"]
        return source, index

    def observed(ui_in=0, uio_in=0, **kwargs):
        cycle = chip.cycle
        source, _ = wire_at(cycle)
        sampled_source, index = wire_at(cycle - 2)
        raw = source.level(cycle)
        logical = (chip.pins_in() >> RX) & 1
        assert chip.sync_bypass == 0 and logical == sampled_source.level(cycle - 2)
        strobes = any(kwargs.get(k, False) for k in ("we", "snap", "commit", "cancel"))
        trace["bus_strobes"] += int(strobes)
        if strobes and any(a <= cycle < b for a, b in mh.gaps):
            trace["active_bus_strobes"] += 1
            raise AssertionError("active loading-host bus strobe")
        pc = core.pc
        active = chip.enable & 1 and core.cnt == 0 and core.dcnt == 0 and not core.halted
        op, _, operand = isa.fields(chip.fetch(pc))
        kind = None
        if active:
            if pc == prog.labels["start"]:
                kind = "start"
            elif op == isa.OP_IN and isa.bits(operand, 18, 15) == isa.PINS:
                kind = "data"
            elif pc in (prog.labels["last"] + 3, prog.labels["final_stop"] + 1):
                kind = "stop"
            elif op == isa.OP_PUSH:
                kind = "push"
        before_isr, before_bits = core.isr, core.isr_cnt
        before_flag, before_halt = chip.flags & 1, core.halted
        auto = kind == "data" and core.autopush and before_bits == 31
        original(ui_in=raw, uio_in=uio_in, **kwargs)
        trace["peak_rx"] = max(trace["peak_rx"], len(core.rxf))
        if kind and (kind != "start" or core.pc != pc):
            event = {"kind": kind, "cycle": cycle, "source_cycle": cycle-2, "segment": index,
                     "pc": pc, "logical": logical, "window": sampled_source.window(cycle-2),
                     "stalled": bool(core.stalled)}
            trace["events"].append(event)
            trace["stalls"] += int(core.stalled)
            if (auto or kind == "push") and not core.stalled:
                word = (before_isr >> 1) | (logical << 31) if auto else before_isr
                trace["publications"].append({"cycle": cycle, "word": word,
                                              "autopush": bool(auto), "segment": index})
        if chip.flags & 1 != before_flag:
            trace["flag_events"].append({"cycle": cycle, "value": chip.flags & 1})
        if not before_halt and core.halted:
            trace["halts"].append({"cycle": cycle, "segment": index, "fault": core.fault})

    chip.step = observed
    return mh, trace


def _episode(mh, trace, segment, index):
    chip, core = mh.chip, mh.chip.cores[0]
    # Ordinary precondition reads: no silent FIFO cleanup or register injection.
    begin = chip.cycle
    before_status = mh.read(sim.core_reg(0, sim.C_STATUS))
    enabled = mh.read(sim.A_CTRL)
    assert before_status & 0xFF == 0, "target FIFOs must be empty before rearm"
    assert not enabled & 1 or before_status & (1 << 13), "target must be stopped/disabled"
    writes = uartcapture.capture_plan(segment["length"], enabled_before=enabled)
    mh.apply(writes)
    armed = chip.cycle
    assert armed < segment["source"].frames[0]["start"]
    mh.gaps.append((armed, segment["deadline"]))
    mh.step(segment["deadline"] - chip.cycle)
    passive = {"cycle": chip.cycle, "pc": core.pc, "isr": core.isr, "isr_bits": core.isr_cnt,
               "r1": core.r[1], "r2": core.r[2], "halted": core.halted, "fault": core.fault}
    status = mh.read(sim.core_reg(0, sim.C_STATUS))
    flags = mh.read(sim.A_FCLR)
    count = (status >> 4) & 15
    wanted = (segment["length"] + 3) // 4
    words, decoded, after, decode_error = [], None, None, None
    # HALT/status gates precede decoder or drain, even if an early word exists.
    if status & (1 << 13) and not status & (1 << 14) and count == wanted:
        words = [mh.read(sim.core_reg(0, sim.C_FIFO), pop=True) for _ in range(count)]
        after = mh.read(sim.core_reg(0, sim.C_STATUS))
        try:
            decoded = list(uartcapture.decode_capture(segment["length"], words))
        except ValueError as exc:
            decode_error = str(exc)
    source = segment["source"]
    result = {k: v for k, v in segment.items() if k not in ("source", "data")}
    result.update(expected=list(segment["data"]), frames=source.frames, windows=source.windows,
                  external_edges=source.edges, status=status, flags=flags, words=words,
                  decoded=decoded, after_status=after, decode_error=decode_error,
                  passive=passive, arm_writes=writes, setup_begin=begin, arm_return=armed,
                  paid_arm_clocks=armed-begin, model_clocks=chip.cycle, segment=index,
                  geometry=dict(ncores=chip.ncores, nbanks=chip.nbanks,
                                bank_words=chip.bank_words, fifo_depth=chip.fifo_depth),
                  trace=json.loads(json.dumps(trace)), host_operations=list(mh.operations),
                  service_gaps=list(mh.gaps))
    _save("capture-%d-%d-%d-%d-%s-%s-seg%d" % (segment["length"], segment["baud_ppm"],
          segment["phase"], segment["jitter"], segment["bad_final"], segment["undersupply"], index), result)
    return result


def _assert_completed(result):
    n = result["length"]
    assert result["trace"]["program_words"] <= 24
    assert result["status"] & (1 << 13) and not result["status"] & (1 << 14)
    assert (result["status"] >> 4) & 15 == (n+3)//4
    assert result["decoded"] == result["expected"]
    assert result["decode_error"] is None and result["after_status"] & 0xFF == 0
    assert result["flags"] & 1 == int(result["bad_final"])
    assert result["trace"]["active_bus_strobes"] == result["trace"]["stalls"] == 0
    events = [e for e in result["trace"]["events"] if e["segment"] == result["segment"]
              and e["kind"] in ("start", "data", "stop")]
    assert len(events) == n*10          # offered extra frames must not be acquired
    for frame in range(n):
        samples = [e for e in events if e["window"] and e["window"]["frame"] == frame]
        assert [e["kind"] for e in samples] == ["start"] + ["data"]*8 + ["stop"]
        assert [e["window"]["slot"] for e in samples] == list(range(10))
        for sample in samples:
            w = sample["window"]
            assert not sample["stalled"] and sample["logical"] == w["level"]
            assert w["begin"] <= sample["source_cycle"] < w["end"]
            if sample["kind"] != "start":
                assert w["begin"] < sample["source_cycle"]
    final_stop = next(e for e in events if e["kind"] == "stop" and e["window"]["frame"] == n-1)
    halt = [h for h in result["trace"]["halts"] if h["segment"] == result["segment"]]
    assert len(halt) == 1 and halt[0]["cycle"] > final_stop["cycle"] and halt[0]["fault"] == 0
    if result["bad_final"]:
        assert any(final_stop["cycle"] < f["cycle"] < halt[0]["cycle"] and f["value"] == 1
                   for f in result["trace"]["flag_events"])
    assert result["passive"]["isr_bits"] == 0


def _run(segment):
    mh, trace = _new_host([segment])
    return _episode(mh, trace, segment, 0)


@pytest.mark.parametrize("length", [1, 3, 4, 5, 31, 32])
def test_nominal_declared_length(length):
    _assert_completed(_run(_wire(length)))


@pytest.mark.parametrize("baud_ppm,phase,jitter", [(-30000,0,0),(-30000,51,0),(0,51,0),
    (30000,0,0),(30000,51,0),(30000,0,2),(30000,51,2)])
def test_corner_length32(baud_ppm, phase, jitter):
    _assert_completed(_run(_wire(32, baud_ppm=baud_ppm, phase=phase, jitter=jitter)))


@pytest.mark.parametrize("length", [3, 4])
def test_final_bad_stop_halts_through_break(length):
    _assert_completed(_run(_wire(length, bad_final=True, break_after=True)))


def test_normally_rearmed_pair():
    segments = [_wire(32, bad_final=True, extra=0xF6),
                _wire(3, origin=200000, data=b"\x39\xd3\x81", extra=0x69)]
    mh, trace = _new_host(segments)       # one cold capture image load, no reset/reload
    first = _episode(mh, trace, segments[0], 0)
    _assert_completed(first)
    second = _episode(mh, trace, segments[1], 1)
    _assert_completed(second)
    assert first["flags"] & 1 and second["flags"] & 1 == 0
    assert [data for addr, data in second["arm_writes"] if addr == sim.A_FCLR] == [1]
    assert second["trace"]["cold_load_writes"] == first["trace"]["cold_load_writes"]


def test_under_supply_waits_without_partial_completion():
    result = _run(_wire(5, undersupply=True))
    assert not result["status"] & ((1 << 13) | (1 << 14))
    assert (result["status"] >> 4) & 15 == 1  # early full word is not completed capture
    assert result["decoded"] is None and result["words"] == []
    assert result["passive"]["r1"] == 1 and result["trace"]["halts"] == []
    assert result["flags"] & 1 == 0 and result["trace"]["active_bus_strobes"] == 0


@pytest.mark.parametrize("length", range(1,33))
@pytest.mark.parametrize("enabled", range(4))
def test_capture_plan_pure(length, enabled):
    peer = enabled & 2
    assert uartcapture.capture_plan(length, enabled_before=enabled) == [
        (sim.A_CTRL, peer), (sim.core_reg(0,sim.C_R0+1),length),
        (sim.core_reg(0,sim.C_R0+2),length%4), (sim.A_FCLR,1),
        (sim.A_CTRL,peer|1|256)]


@pytest.mark.parametrize("kwargs", [{"length":v} for v in (0,33,-1,True,1.5,"3",None)] +
    [{"length":3,"core":v} for v in (1,-1,False,"0")] +
    [{"length":3,"enabled_before":v} for v in (-1,4,True,"2",None)])
def test_invalid_plan_rejected_before_writes(kwargs):
    with pytest.raises(ValueError):
        uartcapture.capture_plan(**kwargs)


@pytest.mark.parametrize("length", range(1,33))
def test_decoder_pure_roundtrip(length):
    data = BURST[:length]
    full,tail = divmod(length,4)
    words = [int.from_bytes(data[i:i+4],"little") for i in range(0,full*4,4)]
    if tail:
        words.append(int.from_bytes(data[full*4:],"little") << (8*(4-tail)))
    assert uartcapture.decode_capture(length,words) == data
    changed = list(words)
    changed[-1] ^= 1 << (8*(4-tail) if tail else 0)
    assert uartcapture.decode_capture(length,changed) != data  # payload oracle catches valid-format wrong data
    with pytest.raises(ValueError):
        uartcapture.decode_capture(length,words+[0])
    with pytest.raises(ValueError):
        uartcapture.decode_capture(length,words[:-1])
    if tail:
        malformed = list(words)
        malformed[-1] |= 1
        with pytest.raises(ValueError):
            uartcapture.decode_capture(length,malformed)


@pytest.mark.parametrize("length,words", [(n,[0]) for n in (0,33,True,1.5,"1",None)] +
    [(1,w) for w in (None,[-1],[2**32],[True],[1.5],["0"],[],[0,0],[1])])
def test_decoder_malformed_inputs(length,words):
    with pytest.raises(ValueError):
        uartcapture.decode_capture(length,words)
