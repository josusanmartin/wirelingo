"""Compiled RP2 PIO SPI instruction traces against the ASIC RTL/netlist.

This tests its digital wire protocol with quantized PIO cycle intervals and
FIFO starvation; it does not emulate RP2350 DMA, pin pads or input synchronizers.
"""
import pathlib
import random
import sys
from array import array

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, Timer
from cocotb.utils import get_sim_time

from pe import asm, host, sim
from pio_support import PioMachine

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'tools/micropython'))
import quad_spi as quad


def reverse_four(value):
    return sum(((value >> i) & 1) << (3 - i) for i in range(4))


class PioSpi:
    def __init__(self, dut, out_reverse=False, in_reverse=False, stalls=False):
        self.dut, self.machine = dut, PioMachine()
        self.out_reverse, self.in_reverse, self.stalls = out_reverse, in_reverse, stalls
        self.clock_phase, self.cycle = 0, 0
        # 150-MHz RP2 system clock, divider 1.5625 => 96-MHz PIO, 24-MHz SCK.
        self.divider = quad.clock_config(150000000, 48000000, 24000000)[1]
        self.system_cycles = 0
        self.last_edge, self.edge_level = None, 0
        self.dut.ui_in.value = 0x80

    async def transfer(self, payload, count):
        tx = [((count - 1) << 16) | int(bool(count % 8))]
        tx += [quad.reverse_nibbles(w) if self.out_reverse else w for w in payload]
        received = []
        await Timer(1000, unit='ns')
        selected = get_sim_time(unit='ps')
        self.dut.ui_in.value = 0
        first_rise = last_fall = None
        self.last_edge, self.edge_level = None, 0
        for _ in range(count * 30 + 10000):
            if tx and len(self.machine.tx) < 4 and (not self.stalls or self.cycle % 13 == 0):
                self.machine.tx.append(tx.pop(0))
            if self.machine.rx and (not self.stalls or self.cycle % 101 == 0):
                received.append(self.machine.rx.popleft())
            incoming = (int(self.dut.uo_out.value) >> 4) & 15
            if self.in_reverse:
                incoming = reverse_four(incoming)
            self.machine.step(incoming)
            outgoing = reverse_four(self.machine.output) if self.out_reverse else self.machine.output
            self.dut.ui_in.value = (outgoing << 2) | (self.machine.sck << 6)
            now = get_sim_time(unit='ps')
            if self.machine.sck != self.edge_level:
                if self.last_edge is not None:
                    assert now - self.last_edge >= 10417, 'shorter than half a project clock'
                self.last_edge, self.edge_level = now, self.machine.sck
                if self.machine.sck and first_rise is None:
                    first_rise = now
                elif not self.machine.sck:
                    last_fall = now
            self.clock_phase += self.divider
            clocks, self.clock_phase = divmod(self.clock_phase, 256)
            old_cycles = self.system_cycles
            self.system_cycles += clocks
            elapsed_ps = round(self.system_cycles * 1000000 / 150) - round(old_cycles * 1000000 / 150)
            await Timer(elapsed_ps, unit='ps')
            self.cycle += 1
            if len(received) == (count + 7) // 8 + 1:
                break
        else:
            assert False, 'compiled PIO SPI program timed out'
        assert received[-1] == 0xFFFFFFFF and not tx
        assert not self.machine.sck
        assert first_rise - selected >= 20834
        assert get_sim_time(unit='ps') - last_fall >= 20834
        self.dut.ui_in.value = 0x80
        await Timer(1000, unit='ns')
        return array('I', received[:-1])

    async def legacy(self, data):
        samples = await self.transfer(array('I', (quad.serial_byte(b) for b in data)), 8 * len(data))
        result = bytearray()
        for word in samples:
            if self.in_reverse:
                word = quad.reverse_nibbles(word)
            byte = 0
            for shift in range(28, -1, -4):
                byte = (byte << 1) | ((word >> (shift + 3)) & 1)
            result.append(byte)
        return bytes(result)

    async def enable_quad(self):
        await self.legacy(host.spi_frame(host.CMD_WRITE, sim.A_HOSTCFG, [1]))
        assert (await self.read(sim.A_ID))[0] == sim.ID_VALUE

    async def write(self, addr, words, fixed=False):
        await self.transfer(*quad.write_frame(addr, words, fixed))

    async def read(self, addr, n=1, fixed=False):
        samples = await self.transfer(*quad.read_frame(addr, n, fixed))
        return quad.read_words(samples, n, self.in_reverse)


async def setup(dut):
    cocotb.start_soon(Clock(dut.clk, 20.834, unit='ns').start())
    dut.ena.value, dut.ui_in.value = 1, 0x80
    dut.ext_oe.value, dut.ext_val.value, dut.pull_up.value = 0, 0, 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 10)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 5)


@cocotb.test()
async def test_compiled_pio_quad_bursts(dut):
    await setup(dut)
    for out_reverse, in_reverse in ((False, False), (True, False), (False, True), (True, True)):
        spi = PioSpi(dut, out_reverse, in_reverse)
        await spi.enable_quad()
        legacy = await spi.legacy(host.spi_frame(host.CMD_READ, sim.A_ID, nread=1))
        assert int.from_bytes(legacy[4:], 'big') == sim.ID_VALUE
        rng = random.Random(27)
        for n in (1, 2, 3, 8, 31, 256):
            words = [rng.getrandbits(32) for _ in range(n)]
            # IMEM is write-only. Execute the loaded image instead of treating
            # its defined zero-valued host reads as a memory readback.
            program = asm.assemble('nop\n' * (n - 1) + 'halt')
            for addr, data in host.coalesce(host.load_program(1, program, 256)):
                await spi.write(addr, data)
            await spi.write(sim.A_CTRL, 2 | (2 << 8))
            await ClockCycles(dut.clk, 3000)
            status = (await spi.read(sim.core_reg(1, sim.C_STATUS)))[0]
            assert status & ((1 << 13) | (1 << 14)) == 1 << 13, 'image must HALT without a bank fault'
            assert await spi.read(sim.core_reg(1, sim.C_PC)) == [256 + n - 1]
            await spi.write(sim.A_CTRL, 0)
            await spi.write(sim.A_POUT, words, fixed=True)
            assert await spi.read(sim.A_POUT, n, fixed=True) == [words[-1]] * n
            regs = [rng.getrandbits(16) for _ in range(4)]
            await spi.write(sim.core_reg(1, sim.C_R0), regs)
            assert await spi.read(sim.core_reg(1, sim.C_R0), 4) == regs
            assert await spi.read(sim.A_ID, n, fixed=True) == [sim.ID_VALUE] * n


@cocotb.test()
async def test_compiled_pio_stalls_fifo_commit_and_reset(dut):
    await setup(dut)
    spi = PioSpi(dut, True, True, stalls=True)
    await spi.enable_quad()
    program = asm.assemble('loop:\npull block\nmov isr, osr\npush block\njmp loop')
    for addr, words in host.coalesce(host.load_program(0, program, 0)):
        await spi.write(addr, words)
    markers = [0xDEAD0000 + i for i in range(8)]
    await spi.write(sim.A_CTRL, 1 | (1 << 8))
    await spi.write(sim.core_reg(0, sim.C_FIFO), markers, fixed=True)
    await ClockCycles(dut.clk, 100)
    assert (await spi.read(sim.core_reg(0, sim.C_STATUS)))[0] >> 4 & 15 == 8
    assert await spi.read(sim.core_reg(0, sim.C_FIFO), 3, fixed=True) == markers[:3]
    assert (await spi.read(sim.core_reg(0, sim.C_STATUS)))[0] >> 4 & 15 == 5
    assert await spi.read(sim.core_reg(0, sim.C_FIFO), 5, fixed=True) == markers[3:]
    assert (await spi.read(sim.core_reg(0, sim.C_STATUS)))[0] >> 4 & 15 == 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 10)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 5)
    assert await spi.read(sim.A_ID) == [0]  # QUAD_EN was cleared by reset.
    await spi.enable_quad()
