"""Run fpga/kv260/host/bringup_kria.py's steps on the RTL of the KV260 design
(kv260_top.v + the flow/fpga_profile.py staging of src/), through the same
GPIO words the Kria writes. pl_clk 100 MHz, protoemu clk 50 MHz, asynchronous.
Wall-clock steps (clock, UART on pins) are hardware-only and skipped."""
import pathlib
import sys

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles
from cocotb.task import bridge, resume

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "host"))
import bringup_kria  # noqa: E402
from pe_kria import Board  # noqa: E402

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


@cocotb.test()
async def test_bringup_steps(dut):
    cocotb.start_soon(Clock(dut.pl_clk, 10, unit="ns").start())
    cocotb.start_soon(Clock(dut.pe_clk, 20, unit="ns").start(start_high=False))
    dut.gpio0_o.value = 0x00FF0080          # AXI GPIO reset value: CS_n high, rst_n low
    await ClockCycles(dut.pl_clk, 10)

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
