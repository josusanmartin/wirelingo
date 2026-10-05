"""Unmodified board capture software over actual quad SPI, tapeout geometry."""
import pathlib
import random
import sys
from types import SimpleNamespace

import cocotb
from cocotb.task import bridge, resume
from cocotb.triggers import ClockCycles, Timer
from cocotb.utils import get_sim_time

from pe import sim
import test as base

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tools/micropython"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "model"))
import eth10_rx
import protoemu
import test_eth10 as golden


async def host(dut, stall=False):
    await base.setup(dut)
    port = base.QuadSpi(dut, period=40, cs_setup=20, cs_hold=20, cs_gap=80)
    await port.write(sim.A_HOSTCFG, [1])

    class Backend:
        armed = delayed = False
        line_states = []

        @resume
        async def read(self, addr, n=1, fixed=False):
            if stall and self.armed and not self.delayed and addr == sim.A_LINESTAT:
                self.delayed = True
                await ClockCycles(dut.clk, 2000)  # 40 us exceeds RX buffering.
            words = await port.qxfer(0x4B if fixed else 0x43, addr, nread=n)
            if addr == sim.A_LINESTAT:
                state = words[0] & 0xE0000
                if not self.line_states or self.line_states[-1][1] != state:
                    self.line_states.append((get_sim_time(unit="ns"), state))
            return words[0] if n == 1 else words

        @resume
        async def write(self, addr, words, fixed=False):
            values = [words] if isinstance(words, int) else list(words)
            await port.qxfer(0x4A if fixed else 0x42, addr, words=values)
            if addr == sim.A_CTRL:
                self.armed = bool(values[0] & 1)

    tt = SimpleNamespace(auto_clocking_freq=50_000_000,
                         ui_in=SimpleNamespace(value=0), uo_out=SimpleNamespace(value=0))
    pe = protoemu.ProtoEmu(tt)
    pe.transport = Backend()

    @resume
    async def now():
        return get_sim_time(unit="ns") / 1000

    pe.now_us = now
    pe.elapsed_us = lambda current, previous: current - previous
    return port, pe


async def send(dut, port, frame, seed=19):
    rng = random.Random(seed)
    wire = bytes([0x55] * 7 + [0xD5]) + frame
    bits = [(byte >> k) & 1 for byte in wire for k in range(8)]
    levels = [v for bit in bits for v in (1-bit, bit)] + [1] * 4
    before = get_sim_time(unit="ns") + 120
    previous = 0
    for k, level in enumerate(levels):
        if level != previous:
            when = before + k * 50 * (1-200e-6) + rng.uniform(-13.5, 13.5)
            delay = round((when - get_sim_time(unit="ns")) * 1000)
            if delay > 0:
                await Timer(delay, unit="ps")
            port._set(0, level)
        previous = level
    delay = round((before + len(levels) * 50 - get_sim_time(unit="ns")) * 1000)
    if delay > 0:
        await Timer(delay, unit="ps")
    port._set(0, 0)
    await Timer(9600, unit="ns")


def capture_result(capture, **kwargs):
    # Expected failure cases must be caught inside the background service task;
    # otherwise cocotb aborts the test while the source is still transmitting.
    try:
        return capture.capture(**kwargs), None
    except Exception as error:
        return None, error


async def ready(dut, pe):
    for _ in range(10000):
        if pe.transport.armed:
            await ClockCycles(dut.clk, 100)
            return
        await ClockCycles(dut.clk, 10)
    assert False, "capture did not arm"


@cocotb.test()
async def test_eth10_board_capture(dut):
    port, pe = await host(dut)
    capture = eth10_rx.Capture(pe)
    frames = [golden.frame(46), golden.frame(1500, seed=5)]
    # 64+1518-byte frames with preambles/IFG need >1.28 ms on the wire.
    task = cocotb.start_soon(bridge(capture_result)(capture, timeout_us=2000, max_frames=2))
    try:
        await ready(dut, pe)
        for frame in frames:
            await send(dut, port, frame)
        result, error = await task
        dut._log.info("Raw counts=%s short=%s malformed=%s pending=%s discarding=%s line=%s",
                      [r[0] for r in capture.raw_frames], capture.records.short, capture.records.malformed,
                      len(capture.records.words), capture.records.discarding, pe.transport.line_states)
        assert error is None, repr(error)
        assert result == frames
    finally:
        if not task.done():
            await task  # Join the synchronous service thread before simulator teardown.
    assert len(capture.raw_frames) == 2
    assert (await port.read(sim.A_CTRL))[0] & 1 == 0
    assert (await port.read(sim.A_LINECFG))[0] == 0
    # Full-looking data with a bad FCS must raise after RX has stopped.
    task = cocotb.start_soon(bridge(capture_result)(capture, timeout_us=1000))
    await ready(dut, pe)
    bad = frames[0][:-1] + bytes([frames[0][-1] ^ 1])
    await send(dut, port, bad)
    result, error = await task
    assert result is None and isinstance(error, ValueError) and "FCS" in str(error)
    assert (await port.read(sim.A_CTRL))[0] & 1 == 0


@cocotb.test()
async def test_eth10_board_capture_overflow(dut):
    port, pe = await host(dut, stall=True)
    capture = eth10_rx.Capture(pe)
    task = cocotb.start_soon(bridge(capture_result)(capture, timeout_us=1000))
    await ready(dut, pe)
    await send(dut, port, golden.frame(1500))
    result, error = await task
    assert result is None and isinstance(error, RuntimeError) and "overflow" in str(error)
    assert not capture.raw_frames
    assert (await port.read(sim.A_CTRL))[0] & 1 == 0
