"""Paid serial-host completion of accepted counted SPI firmware, finite RTL.

Deadline misses are measured outcomes; PASS checks protocol and accounting.
This invocation runs exactly six paired cases; the old optional pause is excluded.
Only pins and returned registers/words are observed. The peer permits finite
intra-frame pauses; no CPU, pad/Fmax, other mode or indefinite deadline claim.
Artifact paths and the aggregate actual-clock ledger are explicitly supplied.
"""
import json
import os
from pathlib import Path

import cocotb
from cocotb.triggers import ClockCycles, RisingEdge, Timer
from cocotb.utils import get_sim_time

from pe import asm, host, sim, spipacked
from pe.profile import TAPEOUT
from test import TimedSpi

CLK_NS = 20
MOSI, SCK, CS, MISO = 8, 9, 10, 16
FW = Path(__file__).resolve().parents[1] / "firmware/spi_master_packed.pasm"
ORDER = [(n, width) for n in (1, 5, 32) for width in (1, 4)]


def now_ps():
    return int(get_sim_time(unit="ps"))


def pattern(length, salt):
    return bytes((((i * 73) ^ salt ^ (i >> 1)) & 254) | 1
                 for i in range(length))


def verify_observation(row):
    """Replay saved external observations, independent of a success flag.

    Boundary: this finite, reset-isolated mode0 message, after idle setup.
    Samples and both SCK edge polarities must match the declared byte count.
    """
    expected = bytes(row["payload"])
    response = bytes(row["response"])
    trace = row["pin_trace"]
    assert trace and trace[0][1:3] == [1, 0], "initial idle pins"
    active, frames, edges, samples, rx_bits = False, 0, [], [], []
    previous = trace[0]
    last_mosi_change = previous[0]
    wire_end = None
    for point in trace[1:]:
        cycle, cs, sck, mosi, miso = point
        old_cycle, old_cs, old_sck, old_mosi, _ = previous
        assert cycle >= old_cycle
        if mosi != old_mosi:
            last_mosi_change = cycle
        if cs != old_cs:
            assert sck == 0, "CS transition at non-idle SCK"
            if not cs:
                assert not active and frames == 0, "extra CS frame"
                active = True
            else:
                assert active, "unmatched CS release"
                active, frames, wire_end = False, frames + 1, cycle
        if cs:
            assert sck == 0, "SCK non-idle outside frame"
        if sck != old_sck:
            assert active and not cs, "clock outside CS"
            edges.append([cycle, sck])
            if sck:
                assert cycle > last_mosi_change, "MOSI lacks sample setup"
                samples.append([cycle, mosi])
                rx_bits.append(miso)
        previous = point
    assert not active and frames == 1, "incomplete/extra CS frame"
    assert len(samples) == 8 * len(expected), "wrong exact sample count"
    assert len(edges) == 16 * len(expected), "wrong exact edge count"
    assert row["sample_trace"] == samples, "saved sample trace disagrees with pins"
    assert row["edge_trace"] == edges, "saved edge trace disagrees with pins"
    wire = bytes(sum(samples[8 * j + i][1] << (7 - i) for i in range(8))
                 for j in range(len(expected)))
    sampled_rx = bytes(sum(rx_bits[8 * j + i] << (7 - i) for i in range(8))
                       for j in range(len(response)))
    assert wire == expected, "MOSI content"
    assert sampled_rx == response, "external sampled MISO content"
    assert spipacked.decode(row["received_words"], len(expected),
                            word_bytes=row["word_bytes"]) == response, "RX content"
    assert row["wire_end_cycle"] == wire_end
    return dict(samples=len(samples), edges=len(edges), wire=list(wire),
                sampled_miso=list(sampled_rx))


class ObservedSpi(TimedSpi):
    """Unchanged real-pin transport; subclass adds timestamps/counts only."""
    def __init__(self, dut):
        super().__init__(dut, period=8 * CLK_NS)
        self.phase_label = "cold"
        self.transactions, self.host_edges = [], []

    def _set(self, bit, value):
        old = (self.ui >> bit) & 1
        super()._set(bit, value)
        if bit in (self.CS, self.SCK) and old != int(value):
            self.host_edges.append(dict(time_ps=now_ps(), pin=bit,
                                        value=int(value)))

    async def xfer(self, data):
        start, first_edge = now_ps(), len(self.host_edges)
        row = dict(phase=self.phase_label, start_ps=start, frame=list(data),
                   command=data[0], address=int.from_bytes(data[1:3], "big"))
        self.transactions.append(row)
        result = await super().xfer(data)
        row.update(end_ps=now_ps(), returned_frame=list(result),
                   edges=self.host_edges[first_edge:])
        read = bool(data[0] & 1)
        offset = 3 + (host.READ_DUMMY if read else 0)
        nwords = (len(data) - offset) // 4
        sck = [e for e in row["edges"] if e["pin"] == self.SCK]
        assert sum(e["value"] for e in sck) == 8 * len(data)
        assert len(sck) == 16 * len(data)
        row.update(read=read, fixed=bool(data[0] & 8), words=nwords,
                   command_bits=8, address_bits=16,
                   dummy_bits=8 * host.READ_DUMMY if read else 0,
                   data_bits=32 * nwords, sck_rising_edges=8 * len(data),
                   system_clocks=(row["end_ps"] - start) / (CLK_NS * 1000),
                   cs_setup_clocks=self.cs_setup / CLK_NS,
                   cs_hold_clocks=self.cs_hold / CLK_NS,
                   cs_gap_clocks=self.cs_gap / CLK_NS)
        return result


class PinPeer:
    """Uses only CS/SCK/MOSI pins, time and predeclared response bits."""
    def __init__(self, dut, spi, response, budget):
        self.dut, self.spi, self.budget = dut, spi, budget
        self.bits = [(byte >> bit) & 1 for byte in response
                     for bit in range(7, -1, -1)]
        self.index, self.miso = 0, self.bits[0]
        self.pending, self.trace, self.edges, self.samples = [], [], [], []
        self.wire_end, self.previous = None, None
        spi.set_low_inputs(self.miso)  # logical16 = ui[0], preserve ui[7:5]

    def observe(self):
        cycle = self.budget["cycles"]
        pins = int(self.dut.uo_out.value)
        cs, sck, mosi = (pins >> (p - 8) & 1 for p in (CS, SCK, MOSI))
        if self.previous is not None:
            old_cs, old_sck, _ = self.previous
            if old_cs != cs:
                assert sck == 0, "CS at non-idle SCK"
                if cs:
                    self.wire_end = cycle
            if old_sck != sck:
                assert not cs, "protocol clock outside CS"
                self.edges.append([cycle, sck])
                if sck:
                    assert self.index < len(self.bits), "extra protocol sample"
                    self.samples.append([cycle, mosi])
                    self.index += 1
                elif self.index < len(self.bits):
                    self.pending.append((cycle + 2, self.bits[self.index]))
        while self.pending and self.pending[0][0] <= cycle:
            _, self.miso = self.pending.pop(0)
            self.spi.set_low_inputs(self.miso)
        point = [cycle, cs, sck, mosi, self.miso]
        if not self.trace or point[1:] != self.trace[-1][1:]:
            self.trace.append(point)
        self.previous = (cs, sck, mosi)

    async def run(self):
        self.observe()
        while True:
            await RisingEdge(self.dut.clk)
            # Observe settled RTL outputs and launch inputs just after an edge,
            # avoiding an artificial same-edge synchronizer capture.
            await Timer(1, unit="ps")
            self.observe()


async def bounded_clock(dut, ledger, path):
    """Count every actual top clock edge, including resets and failures."""
    dut.clk.value = 0
    while True:
        await Timer(CLK_NS / 2, unit="ns")
        assert ledger["cycles"] < ledger["limit"], "aggregate RTL clock cap"
        ledger["cycles"] += 1
        if ledger["cycles"] % 1024 == 0:
            path.write_text(json.dumps(ledger) + "\n")
        dut.clk.value = 1
        await Timer(CLK_NS / 2, unit="ns")
        dut.clk.value = 0


async def load_bursts(spi, writes):
    for address, words in host.coalesce(writes):
        for offset in range(0, len(words), 8):
            await spi.write(address + offset, words[offset:offset + 8])


async def run_case(dut, ledger, row, save, *, length, width, pause=0):
    spi = ObservedSpi(dut)
    row.update(length=length, word_bytes=width, deliberate_pause_clocks=pause,
               transactions=spi.transactions, payload=list(pattern(length, 0xA5)),
               response=list(pattern(length, 0x6D)))
    payload, response = bytes(row["payload"]), bytes(row["response"])
    words = spipacked.encode(payload, word_bytes=width)
    program = asm.assemble_all(FW.read_text(),
                               dict(CPOL=0, WORD_BITS=8 * width, CS=CS))[0]
    row.update(command_words=words, program_words=program.words,
               program_bytes=4 * len(program.words), received_words=[])
    cold_begin = now_ps()
    dut.ena.value = 1
    dut.rst_n.value = 0
    dut.ui_in.value = spi.ui
    dut.ext_oe.value = 0
    dut.ext_val.value = 0
    dut.pull_up.value = 0
    await ClockCycles(dut.clk, 10)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 5)
    assert (await spi.read(sim.A_ID))[0] == sim.ID_VALUE
    await spi.write(sim.A_CTRL, [1 << 16])  # disabled + FIFO clear
    await load_bursts(spi, [(sim.A_POUT, 1 << CS),
                           (sim.A_POE, (1 << MOSI) | (1 << SCK) | (1 << CS))])
    await load_bursts(spi, [(sim.A_SYNCBYP, 0)] +
                      host.pin_owner_writes({MOSI: 0, SCK: 0, CS: 0, MISO: 0}))
    await load_bursts(spi, host.load_program(
        0, program, profile=TAPEOUT, out_pins=(MOSI, 1), side_pins=SCK,
        in_pins=MISO, clkdiv=3 << 8))
    assert (await spi.read(sim.A_SYNCBYP))[0] == 0
    assert (await spi.read(sim.core_reg(0, sim.C_STATUS)))[0] & 255 == 0
    row.update(cold_system_clocks=(now_ps() - cold_begin) / (CLK_NS * 1000),
               cold_transactions=len(spi.transactions),
               cold_serial_bytes=sum(len(t["frame"]) for t in spi.transactions),
               loaded_program_bytes=4 * len(program.words),
               cold_configuration_data_bytes=4 * sum(
                   t["words"] for t in spi.transactions
                   if not t["read"] and t["address"] >= 0x400))
    peer = PinPeer(dut, spi, response, ledger)
    task = cocotb.start_soon(peer.run())
    row.update(pin_trace=peer.trace, sample_trace=peer.samples, edge_trace=peer.edges)
    try:
        await Timer(1, unit="ps")
        resident_begin = now_ps()
        row["resident_begin_ps"] = resident_begin
        row["resident_begin_cycle"] = ledger["cycles"]
        fifo, status_addr = sim.core_reg(0, sim.C_FIFO), sim.core_reg(0, sim.C_STATUS)
        sent, target_rx = min(8, len(words)), (length + width - 1) // width
        # Stage uses the unchanged helper; transport performs the fixed burst.
        assert [a for a, _ in spipacked.stage(0, words)] == [fifo] * len(words)
        spi.phase_label = "prefill"
        await spi.write(fifo, words[:sent], fixed=True)
        spi.phase_label = "start"
        await spi.apply(host.start_writes(1))
        paused = False
        for round_index in range(128):
            spi.phase_label = "service_status"
            status = (await spi.read(status_addr))[0]
            assert not status & ((1 << 13) | (1 << 14)), "halt/fault"
            tx_level, rx_level = status & 15, (status >> 4) & 15
            assert tx_level <= 8 and rx_level <= 8
            remaining = target_rx - len(row["received_words"])
            assert rx_level <= remaining, "phantom RX entries"
            n = min(8 - tx_level, len(words) - sent, 8)
            if n:
                spi.phase_label = "service_tx"
                await spi.write(fifo, words[sent:sent + n], fixed=True)
                sent += n
            if pause and sent == len(words) and not paused:
                row["pause_begin_cycle"] = ledger["cycles"]
                await ClockCycles(dut.clk, pause)
                row["pause_end_cycle"] = ledger["cycles"]
                # Oracle only, never a host service/completion decision.
                assert peer.index == 8 * length
                assert peer.previous[:2] == (0, 1), "RX-full final sample phase"
                boundary = row["pause_end_cycle"] - 1000
                assert not [e for e in peer.edges if e[0] >= boundary]
                assert not [p for p in peer.trace if p[0] >= boundary]
                paused = True
            n = min(rx_level, remaining, 8)
            if n:
                spi.phase_label = "service_rx"
                row["received_words"].extend(await spi.read(fifo, n, fixed=True))
                row["last_rx_return_ps"] = now_ps()
            if sent == len(words) and len(row["received_words"]) == target_rx:
                break
        else:
            raise AssertionError("finite host service-round limit")
        assert spipacked.decode(row["received_words"], length,
                                word_bytes=width) == response
        spi.phase_label = "final_status"
        status = (await spi.read(status_addr))[0]
        assert status & 255 == 0, "FIFO leftovers/phantom RX"
        spi.phase_label = "final_pout"
        for _ in range(32):
            pout = (await spi.read(sim.A_POUT))[0]
            if pout & (1 << CS):
                break
        else:
            raise AssertionError("finite charged POUT completion limit")
        row.update(final_pout=pout, completion_return_ps=now_ps(),
                   wire_end_cycle=peer.wire_end,
                   resident_system_clocks=(now_ps() - resident_begin) / (CLK_NS * 1000),
                   transmitted_words=sent, target_rx_words=target_rx,
                   prefill_payload_capacity_bytes=7 * width,
                   deadline_system_clocks=12000 if length == 32 else None)
        row["deadline_pass"] = (row["resident_system_clocks"] <= 12000
                                if length == 32 else None)
        row["oracle"] = verify_observation(row)
        assert not pause or paused
        row["complete"] = True
    finally:
        row["wire_end_cycle"] = peer.wire_end
        task.cancel()
        save()


@cocotb.test()
async def test_serial_host_paid_completion(dut):
    """Six fixed pairs; 12000-clock deadline misses are performance outcomes."""
    assert float(os.environ["PE_CLK_NS"]) == CLK_NS
    assert [int(os.environ[k]) for k in ("PE_NCORES", "PE_NBANKS", "PE_BANK_AW",
                                       "PE_FIFO_LOG2")] == [2, 2, 8, 3]
    budget_path = Path(os.environ["PACKED_HOST_BUDGET"])
    output_path = Path(os.environ["PACKED_HOST_RESULTS"])
    ledger = json.loads(budget_path.read_text())
    initial = ledger["cycles"]
    result = dict(clock_ns=CLK_NS, host_sck_period_system_clocks=8,
                  profile="2cores/2banks256/depth8/synchronized", cases=[],
                  optional_pause="excluded: six-case paired contract",
                  deadline_contract=dict(system_clocks=12000,
                                         miss_is_measured_outcome=True,
                                         pass_means="protocol/identity/accounting correctness"),
                  finite_pause_tolerance=True)

    def save():
        ledger["cycles_this_invocation"] = ledger["cycles"] - initial
        budget_path.write_text(json.dumps(ledger) + "\n")
        result["cycles_this_invocation"] = ledger["cycles"] - initial
        result["aggregate_actual_rtl_clocks"] = ledger["cycles"]
        output_path.write_text(json.dumps(result, indent=2) + "\n")

    clock = cocotb.start_soon(bounded_clock(dut, ledger, budget_path))
    try:
        for length, width in ORDER:
            row = dict(label=f"N{length}-w{width}")
            result["cases"].append(row)
            await run_case(dut, ledger, row, save, length=length, width=width)
        result["deadline_outcomes"] = [
            dict(label=row["label"], paid_system_clocks=row["resident_system_clocks"],
                 deadline_system_clocks=12000,
                 slack_system_clocks=12000 - row["resident_system_clocks"],
                 meets_deadline=row["deadline_pass"])
            for row in result["cases"] if row["length"] == 32
        ]
    finally:
        clock.cancel()
        save()
