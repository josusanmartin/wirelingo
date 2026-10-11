"""Exact-CS packed SPI, independent pad peer, finite service/cycle evidence.

Only firmware drives protocol pins after start. ModelHost calls are bus clocks,
not measured host-port/CPU throughput. The scheduler observes FIFO occupancy
without a physical status transaction; host wire bounds explicitly exclude it.
Peers here tolerate arbitrary intra-CS idle/sample-phase pauses. No reset,
aborted packet, noncooperating slave, pad or RTL qualification is claimed.
"""
import json
import os
from pathlib import Path

import pytest

from pe import asm, host, sim, spipacked, timing
from pe.modelhost import ModelHost

FW = Path(__file__).resolve().parents[2] / "firmware"
MOSI, SCK, CS, MISO = 8, 9, 10, 16
RUNNERS, RESULTS = [], []


@pytest.fixture(scope="module", autouse=True)
def bounded_cycles():
    """Budget/evidence cover this module only; restore Chip.step on module exit.

    Count every Chip.step here, including load/setup and active host bus calls.
    """
    target = os.environ.get("PACKED_BUDGET_PATH")
    ledger = Path(target) if target else None
    state = json.loads(ledger.read_text()) if ledger else dict(cycles=0, limit=700000)
    initial = state["cycles"]
    original = sim.Chip.step

    def counted(chip, *args, **kwargs):
        assert state["cycles"] < state["limit"], "aggregate model budget exhausted"
        state["cycles"] += 1
        if ledger and state["cycles"] % 1024 == 0:
            ledger.write_text(json.dumps(state) + "\n")
        return original(chip, *args, **kwargs)

    sim.Chip.step = counted
    try:
        yield
    finally:
        sim.Chip.step = original
        if ledger:
            ledger.write_text(json.dumps(state) + "\n")
        target = os.environ.get("PACKED_RESULTS_OUT")
        if target:
            Path(target).write_text(json.dumps(dict(
                cycles_this_invocation=state["cycles"] - initial,
                aggregate_cycles=state["cycles"], results=RESULTS,
                runs=[r.evidence() for r in RUNNERS]), indent=2) + "\n")


def pattern(length, salt):
    return bytes((((i * 73) ^ salt ^ (i >> 1)) & 254) | 1 for i in range(length))


class FramedPeer:
    """Input is physical CS/SCK/MOSI only; expectations never enter the core."""
    def __init__(self, mode, messages, delay):
        self.cpol, self.cpha, self.delay = mode >> 1, mode & 1, delay
        self.messages = messages
        self.prev_cs, self.prev_sck, self.prev_mosi = 1, self.cpol, 0
        self.miso, self.pending, self.frames, self.edges = 0, [], [], []
        self.current = None

    def tick(self, cycle, cs, sck, mosi):
        if cs != self.prev_cs:
            assert sck == self.cpol, "CS transition while SCK non-idle"
            if not cs:
                assert self.current is None and len(self.frames) < len(self.messages)
                tx, response = self.messages[len(self.frames)]
                self.bits = [(b >> i) & 1 for b in response for i in range(7, -1, -1)]
                self.current = dict(start=cycle, samples=[], wire=[], sample_cycles=[], byte=0)
                self.index = 0
                self.pending = []
                self.miso = self.bits[0] if not self.cpha else 0
            else:
                tx, _ = self.messages[len(self.frames)]
                assert self.current is not None
                assert self.index == 8 * len(tx), "wrong exact wire length"
                assert bytes(self.current["wire"]) == tx, "wire data mismatch"
                self.current["end"] = cycle
                self.frames.append(self.current)
                self.current, self.pending, self.miso = None, [], 0
        if cs:
            assert sck == self.cpol, "SCK non-idle outside CS"
        if sck != self.prev_sck:
            assert not cs and self.current is not None, "clock outside CS"
            leading = sck != self.cpol
            self.edges.append([cycle, leading])
            if leading != bool(self.cpha):
                assert self.index < len(self.bits), "extra wire sample edge"
                assert mosi == self.prev_mosi, "MOSI changed at sample edge"
                self.current["samples"].append(mosi)
                self.current["sample_cycles"].append(cycle)
                self.current["byte"] = (self.current["byte"] << 1) | mosi
                self.index += 1
                if self.index % 8 == 0:
                    self.current["wire"].append(self.current["byte"] & 255)
            else:
                bit = self.bits[self.index] if self.index < len(self.bits) else 0
                self.pending.append((cycle + self.delay, bit))
        self.prev_cs, self.prev_sck, self.prev_mosi = cs, sck, mosi
        while self.pending and self.pending[0][0] <= cycle:
            _, self.miso = self.pending.pop(0)
        return self.miso


class Run:
    def __init__(self, mode, divider, delay, lengths, *, word_bytes=4, label="matrix"):
        self.mode, self.divider, self.delay = mode, divider, delay
        self.lengths, self.width, self.label = lengths, word_bytes, label
        self.messages = [(pattern(n, 0xA5 + i * 2), pattern(n, 0x6D + i * 2))
                         for i, n in enumerate(lengths)]
        self.commands = [spipacked.encode(tx, word_bytes=word_bytes) for tx, _ in self.messages]
        self.words = [w for command in self.commands for w in command]
        self.program = asm.assemble_all((FW / "spi_master_packed.pasm").read_text(),
                                       {"CPOL": mode >> 1, "WORD_BITS": 8 * word_bytes,
                                        "CS": CS})[mode & 1]
        self.mh = ModelHost()
        self.mh.chip = sim.Chip(ncores=2, nbanks=2, bank_words=256, fifo_depth=8)
        self.mh.write(sim.A_POUT, (1 << CS) | ((mode >> 1) << SCK))
        self.mh.write(sim.A_POE, (1 << MOSI) | (1 << SCK) | (1 << CS))
        self.mh.apply(host.pin_owner_writes({MOSI: 0, SCK: 0, CS: 0, MISO: 0}))
        self.mh.apply(host.load_program(0, self.program, out_pins=(MOSI, 1),
                                       side_pins=SCK, in_pins=MISO, clkdiv=divider << 8))
        self.setup_bus_operations = self.mh.chip.cycle
        self.peer = FramedPeer(mode, self.messages, delay)
        self.trace = timing.Tracer(self.mh.chip, 0, 0)
        self.tx_index, self.commits, self.produced = 0, 0, 0
        self.got, self.rx_available, self.bus = [], [], []
        self.started = False
        RUNNERS.append(self)

    @property
    def core(self):
        return self.mh.chip.cores[0]

    def pins(self):
        out = self.mh.chip.pin_out
        return ((out >> CS) & 1, (out >> SCK) & 1, (out >> MOSI) & 1)

    def call(self, method, *args, operation=None, word=None):
        cs, sck, mosi = self.pins()
        self.mh.ui = self.peer.tick(self.mh.chip.cycle, cs, sck, mosi)
        if operation:
            self.bus.append(dict(cycle=self.mh.chip.cycle, operation=operation,
                                 active=bool(self.started and not cs), word=word))
        value = method(*args)
        produced = len(self.core.rxf) + self.commits
        if produced > self.produced:
            assert produced == self.produced + 1
            self.rx_available.append(dict(cycle=self.mh.chip.cycle,
                                          word=self.core.rxf[-1]))
        self.produced = produced
        return value

    def step(self, count=1):
        for _ in range(count):
            self.call(self.mh.step)

    def push(self):
        assert self.tx_index < len(self.words) and len(self.core.txf) < 8
        addr, word = spipacked.stage(0, [self.words[self.tx_index]])[0]
        self.call(self.mh.write, addr, word, operation="tx_write", word=word)
        self.tx_index += 1

    def pop(self):
        assert self.core.rxf
        addr = sim.core_reg(0, sim.C_FIFO)
        word = self.call(self.mh.snap, addr, operation="rx_snap")
        self.commits += 1
        self.call(self.mh.commit, addr, operation="rx_commit", word=word)
        self.call(self.mh.cancel, operation="rx_cancel")
        self.got.append(word)

    def start(self, prefill=8):
        for _ in range(min(prefill, len(self.words))):
            self.push()
        self.call(self.mh.start, 1, operation="start_ctrl")
        self.started = True
        self.start_cycle = self.mh.chip.cycle

    def service(self, *, drain=True, limit=8):
        for _ in range(limit):
            if self.tx_index == len(self.words) or len(self.core.txf) == 8:
                break
            self.push()
        if drain:
            for _ in range(limit):
                if not self.core.rxf:
                    break
                self.pop()

    def finish(self, *, period=64, limit=8, drain=True, deadline=None):
        target = self.mh.chip.cycle + 20000
        service_at = self.start_cycle + period
        while len(self.peer.frames) < len(self.messages) and self.mh.chip.cycle < target:
            if self.mh.chip.cycle >= service_at:
                self.service(drain=drain, limit=limit)
                service_at += period
            self.step()
        assert len(self.peer.frames) == len(self.messages), "packet did not finish within finite trace"
        self.step(20)
        while self.core.rxf:
            self.pop()
        self.check_rx()
        assert len(self.peer.edges) == 16 * sum(self.lengths)
        assert not self.core.fault and self.mh.chip.sync_bypass == 0
        assert self.pins()[:2] == (1, self.mode >> 1)
        if deadline is not None:
            return self.peer.frames[-1]["end"] - self.start_cycle <= deadline

    def check_rx(self):
        offset = 0
        for length, (_, response) in zip(self.lengths, self.messages):
            count = (length + self.width - 1) // self.width
            assert spipacked.decode(self.got[offset:offset + count], length,
                                    word_bytes=self.width) == response
            offset += count
        assert offset == len(self.got), "phantom RX word"

    def evidence(self):
        bit_pc = self.program.labels["bit"]
        frame_pc = self.program.labels["frame"]
        bit_ticks = [e[1] for e in self.trace.instances() if e[0] == bit_pc]
        frame_ticks = [e[1] for e in self.trace.instances() if e[0] == frame_pc]
        logical_active = [e for e in self.bus if e["active"] and
                          e["operation"] in ("tx_write", "rx_commit")]
        gaps = [b["cycle"] - a["cycle"] for a, b in zip(logical_active, logical_active[1:])]
        return dict(label=self.label, mode=self.mode, divider=self.divider, delay=self.delay,
                    lengths=self.lengths, word_bytes=self.width, program_words=len(self.program.words),
                    program_bytes=4 * len(self.program.words), setup_bus_operations=self.setup_bus_operations,
                    commands=self.commands, bus_operations=self.bus, received_words=self.got,
                    rx_available=self.rx_available, frames=self.peer.frames, edges=self.peer.edges,
                    pending_frame=self.peer.current, next_tx_word_index=self.tx_index,
                    bit_tick_deltas=sorted({b - a for a, b in zip(bit_ticks, bit_ticks[1:])}),
                    frame_tick_spans=[b - a for a, b in zip(frame_ticks, frame_ticks[1:])],
                    active_tx_words=sum(e["operation"] == "tx_write" for e in logical_active),
                    active_rx_words=sum(e["operation"] == "rx_commit" for e in logical_active),
                    active_host_bus_operations=sum(e["active"] for e in self.bus),
                    worst_observed_active_fifo_operation_gap=max(gaps, default=0))


@pytest.mark.parametrize("mode", range(4))
@pytest.mark.parametrize("divider", [3, 4])
@pytest.mark.parametrize("delay", [1, 2])
@pytest.mark.parametrize("length", [1, 3, 4, 5, 31, 32, 33])
def test_exact_matrix(length, mode, divider, delay):
    r = Run(mode, divider, delay, [length])
    r.start()
    r.finish(drain=length > 32)
    row = r.evidence()
    body = 5 if mode & 1 else 4
    assert row["bit_tick_deltas"] == [body]
    assert row["frame_tick_spans"] == [8 * length * body + 11 + bool(length % 4)]
    assert len(r.rx_available) == (length + 3) // 4
    if len(r.words) <= 8:
        assert row["active_host_bus_operations"] == 0
    frame = r.peer.frames[0]
    row = dict(kind="matrix", mode=mode, divider=divider, delay=delay, length=length,
               body_ticks_per_bit=body, message_overhead_ticks=11 + bool(length % 4),
               cs_active_clocks=frame["end"] - frame["start"],
               first_rx_from_cs_clocks=r.rx_available[0]["cycle"] - frame["start"],
               last_rx_from_cs_clocks=r.rx_available[-1]["cycle"] - frame["start"],
               first_rx_after_first_byte_sample_clocks=r.rx_available[0]["cycle"] - frame["sample_cycles"][7],
               last_rx_after_last_sample_clocks=r.rx_available[-1]["cycle"] - frame["sample_cycles"][-1],
               costs=spipacked.host_sck_cost(length))
    RESULTS.append(row)


@pytest.mark.parametrize("mode", range(4))
def test_starvation_before_first_payload_and_inside_cs(mode):
    r = Run(mode, 3, 2, [5], label="tx-starvation")
    r.start(prefill=0)
    r.step(101)
    assert r.pins()[:2] == (1, mode >> 1) and not r.peer.edges
    r.push()  # Header alone must not assert CS.
    r.step(101)
    assert r.pins()[:2] == (1, mode >> 1) and not r.peer.edges
    r.push()
    r.step(700)
    assert r.peer.current["wire"] == list(r.messages[0][0][:4])
    assert r.pins()[:2] == (0, mode >> 1) and r.core.stalled
    before = list(r.peer.edges)
    r.step(137)
    assert r.peer.edges == before
    r.push()
    r.finish(drain=False)


@pytest.mark.parametrize("mode", range(4))
@pytest.mark.parametrize("divider", [3, 4])
@pytest.mark.parametrize("length", [33, 36])
def test_rx_full_final_sample_and_partial_push(length, mode, divider):
    r = Run(mode, divider, 2, [length], label="rx-full")
    r.start()
    for _ in range(64):
        r.service(drain=False)
        r.step(1)
    r.step(8 * length * 5 * divider + 80)
    assert len(r.core.rxf) == 8 and r.core.stalled
    expected_sck = (mode >> 1) if mode & 1 else 1 - (mode >> 1)
    assert r.pins()[:2] == (0, expected_sck)
    assert r.peer.current["wire"] == list(r.messages[0][0])
    before, miso = list(r.peer.edges), r.peer.miso
    r.step(137)
    assert r.peer.edges == before and r.peer.miso == miso
    r.pop()
    r.finish(drain=False)


@pytest.mark.parametrize("mode", range(4))
def test_consecutive_different_lengths(mode):
    r = Run(mode, 3, 2, [1, 3, 4, 5], label="consecutive")
    r.start()
    r.finish(drain=False)
    assert len(r.peer.frames) == 4 and len(r.got) == 5


def test_exact_wire_length_negative_retains_nonzero_padding():
    r = Run(0, 3, 2, [5], label="extra-tail-negative")
    assert r.words[-1] & 0xFFFFFF == 0xD3D3D3
    r.words[0] = ((8 * 6 - 1) << 16) | 2  # Corrupt length without changing peer.
    r.start()
    with pytest.raises(AssertionError, match="extra wire sample edge"):
        r.finish(drain=False)
    RESULTS.append(dict(kind="negative", result="rejected_extra_tail_sample",
                        intended_bytes=5, corrupt_header_bytes=6,
                        samples=len(r.peer.current["samples"])))


def test_helper_rejects_invalid_lengths_and_phantom_rx():
    for length in [0, -1, 8193, True, 1.5]:
        with pytest.raises(ValueError):
            spipacked.decode([], length)
    with pytest.raises(ValueError):
        spipacked.encode(b"")
    assert len(spipacked.encode(bytes(8192))) == 2049
    for words in [[], [1, 2], [0x100]]:
        with pytest.raises(ValueError):
            spipacked.decode(words, 1)
    for width in [0, 2, True]:
        with pytest.raises(ValueError):
            spipacked.encode(b"a", word_bytes=width)


def test_deterministic_service_same_framed_contract():
    rows = []
    for width in (1, 4):
        r = Run(0, 3, 2, [32], word_bytes=width, label="offered-load")
        r.start()
        met = r.finish(period=400, limit=1, deadline=4000)
        rows.append(dict(kind="service", word_bytes=width, payload_bytes=32,
                         service_period_clocks=400, max_tx_rx_words_per_service=1,
                         deadline_clocks=4000, met_deadline=met,
                         delivered_bytes=len(r.peer.frames[0]["wire"]),
                         finish_clocks=r.peer.frames[0]["end"] - r.start_cycle,
                         costs=spipacked.host_sck_cost(32, word_bytes=width)))
        RESULTS.append(rows[-1])
    assert rows[0]["delivered_bytes"] == rows[1]["delivered_bytes"] == 32
    assert not rows[0]["met_deadline"] and rows[1]["met_deadline"]
    assert rows[1]["costs"]["single_sck"] == 624 < rows[0]["costs"]["single_sck"] == 2328
    assert spipacked.host_sck_cost(1) == spipacked.host_sck_cost(1, word_bytes=1) | {
        "prestart_payload_capacity_bytes": 28}
