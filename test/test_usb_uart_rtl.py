"""UART -> USB pair modes on the RTL top level, pins only, cross-checked on the model.

Each test loads one optional pair (docs/usb-uart-overlap4.md,
docs/usb-uart-envelope3.md, docs/usb-uart-byte.md) through the SPI host port,
exactly the writes of its tools/pe helper's load_writes(), into tb.v's
tt_um_josusanmartin_protoemu in the tapeout configuration (2 cores, 2 SRAM
banks of 256 words, 8-deep FIFOs) at 48 MHz. The loading host then stays off
the bus (CS_n high, SCK and MOSI still: watched on the pads) while two
pins-only peers run the mode's scenario (test/model/usb_uart_pairs.py):

* a READY-gated ideal 8N1 sender at 115,200 baud on RX (ui0): one frame per
  fresh READY rise (uo0), started only while READY is high, finished always;
* pe.usbhost.ControlHost, the model tests' low-speed host controller, on
  D+/D- (uio0/uio1, pull-up on D-) through tb.v's external drivers.

usb_uart_pairs.check() then judges the RTL run against expectations computed
from the sender's byte list alone: every report's bytes and order, no lost or
duplicated byte, READY admission and throttling, STALL when empty (first and
last request, and never while a report is due), the bad-STOP byte dropped
(and the ERROR pin where the mode has one), an in-transfer IN retry answered
with the identical DATA packet, device turnarounds. The same scenario runs on
the cycle model (tools/pe/sim.py) in-process, and usb_uart_pairs.compare()
requires the two runs to agree on everything recorded: reports, request
outcomes, every USB packet with its first and last clock, each UART frame's
START and READY grant clock, and every edge of RX, READY, ERROR, D+ and D-,
in clocks after the anchor (the first clock READY is high after the load).
The comparison and both records are saved to sim_build/usb_uart_rtl/ (or
$USB_UART_RTL_OUT).

This is RTL simulation with ideal edges at one clock's resolution: no gate
delays, jitter, baud error, electrical USB or real host scheduling.

    make COCOTB_TEST_MODULES=test_usb_uart_rtl SIM_BUILD=sim_build/usb_uart_rtl
"""
import json
import os
import sys
import time
import types
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.task import bridge, resume
from cocotb.triggers import ClockCycles, FallingEdge, Timer, ValueChange
from cocotb.utils import get_sim_time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "model"))
sys.path.insert(0, str(HERE.parent / "tools"))
from pe import host, sim  # noqa: E402
import usb_uart_pairs as pairs  # noqa: E402

PERIOD_PS = 20834          # 47.9985 MHz: 48 MHz within the simulator's 1 ps precision
LOAD_HALF = 1              # load at SCK = clk/2 (the port allows SCK up to clk, docs/isa.md)
READ_HALF = 4              # reads at clk/8, as test/test.py
OUT = Path(os.environ.get("USB_UART_RTL_OUT") or HERE / "sim_build" / "usb_uart_rtl")


class Spi:
    """Bit-banged mode-0 SPI master on ui[7:5] (CS_n, SCK, MOSI), MISO uo[7].
    Between transactions CS_n is high and SCK and MOSI are low."""
    CS, SCK, MOSI = 7, 6, 5

    def __init__(self, dut, low=1):
        self.dut = dut
        self.ui = 0x80 | (low & 0x1F)
        dut.ui_in.value = self.ui
        self.transactions = 0

    def _set(self, bit, val):
        self.ui = (self.ui & ~(1 << bit)) | (int(val) << bit)
        self.dut.ui_in.value = self.ui

    async def xfer(self, data, half):
        clk, out = self.dut.clk, bytearray()
        self._set(self.CS, 0)
        await ClockCycles(clk, half)
        for byte in data:
            r = 0
            for i in range(7, -1, -1):
                self._set(self.MOSI, (byte >> i) & 1)
                await ClockCycles(clk, half)
                r = (r << 1) | ((int(self.dut.uo_out.value) >> 7) & 1)
                self._set(self.SCK, 1)
                await ClockCycles(clk, half)
                self._set(self.SCK, 0)
            out.append(r)
        self._set(self.MOSI, 0)
        await ClockCycles(clk, half)
        self._set(self.CS, 1)
        await ClockCycles(clk, 6)                       # CS_n high >= 4 clk between transactions
        self.transactions += 1
        return bytes(out)

    async def load(self, writes):
        for addr, words in host.coalesce(writes):
            await self.xfer(host.spi_frame(host.CMD_WRITE, addr, words), LOAD_HALF)

    async def read(self, addr):
        r = await self.xfer(host.spi_frame(host.CMD_READ, addr, nread=1), READ_HALF)
        return int.from_bytes(r[3 + host.READ_DUMMY:3 + host.READ_DUMMY + 4], "big")


class RtlBench(pairs.Bench):
    """ModelHost's interface on the RTL pads, for ControlHost and the sender.

    The bench advances one clock per Timer, one picosecond after each falling
    edge: there it reads what the chip drives (settled after the rising edge),
    runs Bench.observe() and writes ui_in/ext_oe/ext_val, which the next rising
    edge samples. The pads thus change between a falling and a rising edge,
    the model's default for its falling-edge line sample. ControlHost runs in a
    bridge() thread; step(n) resumes the simulator for n clocks."""

    def __init__(self, dut, frames):
        self.dut = dut
        self.ui, self.uio, self.ext_oe, self.pull_up = 1, 0, 0, 0b10
        self.host_bits = 0x80                          # CS_n high, SCK and MOSI low
        self.chip = types.SimpleNamespace(cycle=0)     # ControlHost's clock count
        self.init_bench(frames)
        self.written = None

    def sample(self):
        d = self.dut
        self.uo = int(d.uo_out.value) & 0x7F
        self.uio_out, self.uio_oe = int(d.uio_out.value), int(d.uio_oe.value)

    def bus_uio(self):
        """tb.v's resolution: wired-AND where both drive, else the driver, else the pull-up."""
        v = 0
        for i in range(8):
            c, e = (self.uio_oe >> i) & 1, (self.ext_oe >> i) & 1
            if c and e:
                b = (self.uio_out >> i) & (self.uio >> i) & 1
            elif c:
                b = (self.uio_out >> i) & 1
            elif e:
                b = (self.uio >> i) & 1
            else:
                b = (self.pull_up >> i) & 1
            v |= b << i
        return v

    async def clocks(self, n, observe=True):
        d = self.dut
        for _ in range(n):
            if observe:
                rx = self.observe(self.uo, self.bus_uio())
                self.ui = (self.ui & ~1) | rx
            w = (self.host_bits | (self.ui & 0x1F), self.ext_oe & 0xFF, self.uio & self.ext_oe & 0xFF)
            if w != self.written:
                d.ui_in.value, d.ext_oe.value, d.ext_val.value = w
                self.written = w
            await Timer(PERIOD_PS, unit="ps")
            self.sample()
            self.chip.cycle += 1
            if self.t is not None:
                self.t += 1

    @resume
    async def _resume_clocks(self, n):
        await self.clocks(n)

    def step(self, n=1):
        if n > 0:
            self._resume_clocks(n)


async def run(dut, mode):
    assert [int(os.environ.get(k, v)) for k, v in zip(
        ("PE_NCORES", "PE_NBANKS", "PE_BANK_AW", "PE_FIFO_LOG2"), ("2", "2", "8", "3"))] == [2, 2, 8, 3], \
        "tapeout profile only (2 cores, 2 banks of 256 words, depth-8 FIFOs)"
    spec = pairs.MODES[mode]
    wall = time.time()
    cocotb.start_soon(Clock(dut.clk, PERIOD_PS, unit="ps").start())
    spi = Spi(dut, low=1)                              # RX (ui0) idle high from reset on
    dut.ena.value = 1
    dut.ext_oe.value = dut.ext_val.value = 0
    dut.pull_up.value = 0b10                           # the device's 1.5k on D-: J when idle
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 10)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 5)
    assert await spi.read(sim.A_ID) == sim.ID_VALUE
    t_load = get_sim_time(unit="ps")
    writes = spec.helper.load_writes()
    await spi.load(writes)
    load_clocks = round((get_sim_time(unit="ps") - t_load) / PERIOD_PS)
    assert spi.ui == 0x81, hex(spi.ui)

    host_moves = []                                    # any CS_n/SCK/MOSI change after the load

    async def watch():
        while True:
            await ValueChange(dut.ui_in)
            v = int(dut.ui_in.value) >> 5
            if v != 0b100:
                host_moves.append((get_sim_time(unit="ns"), v))
    watcher = cocotb.start_soon(watch())

    bench = RtlBench(dut, spec.frames())
    await FallingEdge(dut.clk)
    await Timer(1, unit="ps")
    bench.sample()
    assert not bench.uo & 1, "READY already high when the load returned"
    for _ in range(5000):
        if bench.uo & 1:
            break
        await bench.clocks(1, observe=False)
    else:
        raise AssertionError("READY never rose after the load")
    anchor_after_load = bench.chip.cycle
    bench.t = 0
    rec = await bridge(spec.scenario)(bench)
    watcher.cancel()
    rec.update(load_clocks=load_clocks, load_writes=len(writes), spi_transactions=spi.transactions,
               anchor_after_load=anchor_after_load, host_moves_after_load=host_moves)
    rec["flags"] = await spi.read(sim.A_FSET)          # after the scenario: the host may read again
    rtl_wall = time.time() - wall

    OUT.mkdir(parents=True, exist_ok=True)
    result = dict(mode=mode, rtl=rec)
    try:
        assert not host_moves, ("loading host moved after the load", host_moves[:4])
        result["rtl_check"] = summary = pairs.check(mode, rec)
        model, _ = pairs.run_model(mode)
        result["model"] = model
        result["model_check"] = pairs.check(mode, model)
        result["differences"] = diffs = pairs.compare(rec, model)
        assert not diffs, f"RTL and model differ: {json.dumps(diffs)[:2000]}"
    finally:
        (OUT / f"{mode}.json").write_text(json.dumps(result, indent=1, default=str) + "\n")
    dut._log.info(
        f"{mode}: SPI load {len(writes)} writes / {spi.transactions} transactions / {load_clocks} clocks, "
        f"READY {anchor_after_load} clocks after it; then {summary['simulated_clocks']} clocks "
        f"({summary['simulated_ms']} ms) with the host port idle: {summary['frames']} UART frames "
        f"({summary['good_bytes']} good), {summary['requests']} requests ({summary['stalls']} STALLed), "
        f"{summary['usb_packets']} USB packets, reports {summary['reports']}; READY fell "
        f"{summary['ready_fall_after_start']} clocks after START, holds {summary['holds']}, READY "
        f"{summary['ack_to_ready_rise']} clocks after the releasing ACK began, ERROR at {summary['error_rise']}; "
        f"turnaround {summary['turnaround_bits']} bit times; identical to the cycle model "
        f"(all packets, frames and pin edges); RTL wall {rtl_wall:.0f} s")


@cocotb.test()
async def test_overlap4_rtl(dut):
    """usb_uart_overlap4 + uart_rx_overlap4: 4-byte reports, the next group
    received while USB retains the current one (pairs.overlap4_scenario)."""
    await run(dut, "overlap4")


@cocotb.test()
async def test_envelope3_rtl(dut):
    """usb_uart_envelope3 + uart_rx_envelope3: [b0, b1, b2, n] reports, partial
    groups sealed by the idle timer (pairs.envelope3_scenario)."""
    await run(dut, "envelope3")


@cocotb.test()
async def test_byte_rtl(dut):
    """usb_uart_byte + uart_rx_mailbox: one retained byte per report
    (pairs.byte_scenario)."""
    await run(dut, "byte")
