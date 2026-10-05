"""Run fpga/kv260/host/bringup_kria.py's steps on the RTL of the KV260 design
(kv260_top.v + the flow/fpga_profile.py staging of src/), through the same
GPIO words the Kria writes. pl_clk 100 MHz, protoemu clk 50 MHz, asynchronous.
Wall-clock steps (clock, UART on pins) are hardware-only and skipped."""
import pathlib
import sys

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, ReadOnly, RisingEdge
from cocotb.task import bridge, resume

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "host"))
import bringup_kria  # noqa: E402
import spim_test  # noqa: E402
from pe_kria import Board  # noqa: E402
from pe_spim import Spim  # noqa: E402

import os  # noqa: E402
CLOCKS = os.environ.get("SPIM_CLOCKS", "shared alt").split()
DIVS = [int(d) for d in os.environ.get("SPIM_DIVS", "4 2 1").split()]
GPIO_CLKS = 3   # pl_clk cycles per GPIO access: SCK half period 30 ns, 1.5 protoemu clk


class SimRegs:
    def __init__(self, dut):
        self.dut = dut

    @resume
    async def out(self, v):
        self.dut.gpio0_o.value = v
        await ClockCycles(self.dut.pl_clk, GPIO_CLKS)

    @resume
    async def inp(self):
        await ClockCycles(self.dut.pl_clk, 1)
        return int(self.dut.gpio0_i.value)

    @resume
    async def cycles_gray(self):
        return int(self.dut.gpio1_i.value)


class SimSpimMem:
    """pe_spim.SpimMem over the testbench's AXI4-Lite port."""

    def __init__(self, dut):
        self.dut = dut

    async def _handshake(self, ready):
        while True:
            await ReadOnly()
            ok = ready.value == 1
            await RisingEdge(self.dut.pl_clk)
            if ok:
                return

    @resume
    async def wr(self, off, v):
        d = self.dut
        d.s_awaddr.value, d.s_wdata.value = off, v & 0xFFFFFFFF
        d.s_awvalid.value = d.s_wvalid.value = d.s_bready.value = 1
        await self._handshake(d.s_awready)
        d.s_awvalid.value = d.s_wvalid.value = 0
        await self._handshake(d.s_bvalid)

    @resume
    async def rd(self, off):
        if off == 0x8010:                    # STATUS: poll here until idle (no thread switch per poll)
            while await self._rd(off) & 1:
                pass
            return 0
        return await self._rd(off)

    async def _rd(self, off):
        d = self.dut
        d.s_araddr.value, d.s_arvalid.value, d.s_rready.value = off, 1, 1
        await self._handshake(d.s_arready)
        d.s_arvalid.value = 0
        while True:
            await ReadOnly()
            if d.s_rvalid.value == 1:
                v = int(d.s_rdata.value)
                await RisingEdge(d.pl_clk)
                return v
            await RisingEdge(d.pl_clk)

    def buf_write(self, words):
        for i, w in enumerate(words):
            self.wr(4 * i, w)

    def buf_read(self, n):
        return [self.rd(4 * i) for i in range(n)]


async def start(dut, alt_ns=21.77):
    cocotb.start_soon(Clock(dut.pl_clk, 10, unit="ns").start())
    cocotb.start_soon(Clock(dut.pe_clk, 20, unit="ns").start(start_high=False))
    cocotb.start_soon(Clock(dut.alt_clk, alt_ns, unit="ns").start())
    dut.gpio0_o.value = 0x00FF0080          # AXI GPIO reset value: CS_n high, rst_n low
    dut.s_awvalid.value = dut.s_wvalid.value = dut.s_arvalid.value = 0
    dut.s_bready.value = dut.s_rready.value = 0
    dut.aresetn.value = 0
    await ClockCycles(dut.pl_clk, 10)
    dut.aresetn.value = 1
    await ClockCycles(dut.pl_clk, 2)


@cocotb.test()
async def test_bringup_steps(dut):
    await start(dut)

    def run():
        ctx = bringup_kria.Ctx()
        ctx.board = Board(SimRegs(dut))
        ctx.board.reset(hold_s=0)
        ctx.pe = ctx.board.protoemu()
        ctx.f_nominal = ctx.f_clk = 50e6
        for name, fn in bringup_kria.STEPS:
            if name in ("clock", "uart_tx_pin", "uart_rx_pin"):
                continue
            if name == "regs":
                fn(ctx, n=3)
            elif name == "quad":
                fn(ctx, n=5)
            else:
                fn(ctx)
            dut._log.info(f"{name}: PASS")
    await bridge(run)()


@cocotb.test()
async def test_spim(dut):
    """spim_test.py's checks through the fabric master, shortened: SCK =
    mclk/4, /2, /1, 1-bit and quad, master on protoemu's clock and on an
    unrelated 45.9 MHz clock."""
    await start(dut)

    def run():
        board = Board(SimRegs(dut))
        board.reset(hold_s=0)
        spim = Spim(SimSpimMem(dut))
        ctx = spim_test.Ctx(board, spim, 50e6, log=dut._log.info)
        dut._log.info(f"pe_spim version 0x{spim.mem.rd(0x802C):08X}")
        spim.select(fabric=True)
        for div in DIVS:
            spim.div = div
            got = ctx.fab.read(0x800)
            dut._log.info(f"fabric 1-bit ID at SCK = mclk/{div}: 0x{got:08X}, CS low {spim.last_pcyc} clk")
            assert got == 0x50450002
        ok = spim_test.run(ctx, CLOCKS, DIVS, 16, 1, [16, 1, 5])
        assert ok, [n for n, good in ctx.results if not good]
    await bridge(run)()
