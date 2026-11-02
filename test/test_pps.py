"""firmware/pps_capture.pasm on the RTL, through the real SPI host port
(docs/pps-capture.md; the sweeps are in test/model/test_pps.py).

The testbench drives uio0 at the falling clock edge of a known clock, so the
intervals the chip must report are the differences of those clocks, and the
same stimulus on the cycle model must give the same words.

    make COCOTB_TEST_MODULES=test_pps
    PPS_LONG=1 make COCOTB_TEST_MODULES=test_pps COCOTB_TEST_FILTER=pps_one_second    # 50M clocks
"""
from fractions import Fraction
import importlib.util
import os
import pathlib
import random

import cocotb
from cocotb.triggers import ClockCycles, Timer
from cocotb.utils import get_sim_time

from pe import host, pps, sim

HERE = pathlib.Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("pe_top_tests", HERE / "test.py")
T = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(T)

PIN = 0                 # uio0
FIFO = sim.core_reg(0, sim.C_FIFO)
STATUS = sim.core_reg(0, sim.C_STATUS)
PROG = pps.assemble()
TCLK_PS = round(T.CLK_NS * 1000)


class Rig:
    """The firmware running, uio0 driven at chosen clocks, the FIFO drained
    through the 1-bit SPI port."""

    def __init__(self, dut, spi):
        self.dut, self.spi = dut, spi
        self.words = []
        self.log = []               # (clock, level)
        self.level = 0

    @classmethod
    async def start(cls, dut, start=pps.WRAP - 1):
        spi = await T.setup(dut)
        spi = T.TimedSpi(dut, period=2 * T.CLK_NS, cs_setup=T.CLK_NS, cs_hold=T.CLK_NS, cs_gap=4 * T.CLK_NS)
        T.EXT.set(PIN, True, 0)
        await spi.apply(pps.load_writes(PROG, pin=PIN, start=start, run=False, profile=T.PROFILE))
        await spi.write(sim.A_CTRL, [1 | (1 << 8) | (1 << 16)])
        rig = cls(dut, spi)
        await ClockCycles(dut.clk, 30)
        return rig

    def now(self):
        """The clock number of the falling edge at or after this instant."""
        return int(get_sim_time(unit="ps") // TCLK_PS)

    async def edge(self, level, clock):
        """Drive the pad at the falling edge inside clock number `clock`."""
        t = clock * TCLK_PS + TCLK_PS // 2 - get_sim_time(unit="ps")
        assert t > 0, (clock, self.now())
        await Timer(t, unit="ps")
        T.EXT.set(PIN, True, level)
        self.log.append((clock, level))
        self.level = level

    async def drain(self):
        n = ((await self.spi.read(STATUS))[0] >> 4) & 15
        if n:
            self.words += await self.spi.read(FIFO, n, fixed=True)
        return n

    async def pulse(self, clock, width):
        await self.edge(1, clock)
        await self.edge(0, clock + width)
        await ClockCycles(self.dut.clk, 40)
        await self.drain()


def model_words(log, start, first_tick):
    """The same stimulus on the cycle model, with the first edge given the same
    tick as on the RTL. The host reads between edges, without pausing them."""
    from pe.modelhost import ModelHost
    mh = ModelHost()
    mh.apply(pps.load_writes(PROG, pin=PIN, start=start, run=False))
    w = mh.chip.cycle
    mh.apply(host.start_writes(1))
    mh.ext_oe = 1 << PIN
    zero = w + 2 + pps.START
    shift = zero + first_tick - pps.PAD_TO_WAIT - log[0][0]
    words = []
    for clock, level in log:
        c = clock + shift
        while mh.chip.cycle < c:
            mh.step()
            if mh.chip.cores[0].rxf and mh.chip.cycle + 30 < c:
                words += mh.pop_all(0)
        mh.uio = level
    for _ in range(40):
        mh.step()
    return words + mh.pop_all(0)


def expect_exact(edges, log):
    """Stamp differences equal drive-clock differences, for the edges not
    marked late; the late ones are bracketed."""
    t0, c0 = edges[0].tick, log[0][0]
    assert edges[0].late == 0
    for e, (c, level) in zip(edges, log):
        true = c - c0 + t0
        assert e.level == level
        assert e.tick - e.late <= true <= e.tick, (e, c)
        if not e.late:
            assert e.tick == true, (e, c)


@cocotb.test()
async def pps_record_paths(dut):
    """A pulse of each width through the record path, then records on its
    borrow paths: with r3 run out, and without. Words must equal the model's
    and intervals the drive clocks."""
    start = (3 << 32) | 12000                 # r3 ran out, r2 = 0 at tick 12000
    rig = await Rig.start(dut, start)
    await rig.drain()
    c = rig.now() + 40
    await rig.pulse(c, 200)
    first = pps.Decoder(start).feed(rig.words)
    assert len(first) == 2 and first[1].tick == first[0].tick + 200
    t = first[0].tick - c                     # an edge driven in clock c gets tick c + t
    tick = first[1].tick + 800
    for width in (1, 2, 12, 13, 14, 19, 20):
        await rig.pulse(tick - t, width)
        tick += 800
    await rig.pulse(11995 - t, 1)             # r2 = 5 and r3 = 0: the longest path, the fall inside it
    await rig.pulse(12000 + 65536 - 5 - t, 1)     # r2 = 5, r3 not 0: the borrow path
    await rig.drain()
    edges = pps.Decoder(start).feed(rig.words)
    assert len(edges) == len(rig.log) and edges[0].drops == 0
    expect_exact(edges, rig.log)
    late = {e.late for e in edges}
    assert {pps.RECORD - 1, pps.BORROW - 1, pps.CARRY - 1} <= late, late
    dut._log.info(f"{len(edges)} edges, late bounds used: {sorted(late)}")
    assert model_words(rig.log, start, edges[0].tick) == rig.words


@cocotb.test()
async def pps_rollover_phases(dut):
    """An edge on every tick around a rollover, one run per phase: r2 runs out
    at tick 1500, with r3 not run out (3 blind ticks) and run out (7), and an
    edge 900 ticks later that must be exact."""
    for mid, gap in ((7, pps.ROLL), (0, pps.ROLL_CARRY)):
        for off in range(-1, gap + 2):
            start = (3 << 32) | (mid << 16) | 1500
            rig = await Rig.start(dut, start)
            c = rig.now() + 40
            await rig.pulse(c, 100)
            t = pps.Decoder(start).feed(rig.words)[0].tick - c
            await rig.pulse(1500 + off - t, 400)
            await rig.pulse(1500 + off - t + 900, 400)
            await rig.drain()
            edges = pps.Decoder(start).feed(rig.words)
            assert len(edges) == 6
            expect_exact(edges, rig.log)
            assert edges[2].tick == 1500 + (gap if 0 < off < gap else off), (off, edges[2])
            assert edges[2].late == (gap - 1 if 0 < off <= gap else 0), (off, edges[2])
            assert edges[4].late == 0
            assert model_words(rig.log, start, edges[0].tick) == rig.words


@cocotb.test()
async def pps_train(dut):
    """A train with known offset, drift and jitter, a missed pulse and a runt,
    stamped through the SPI port: nominal 70000 clocks (1.4 ms), longer than
    the 16-bit rollover, so every interval has one in it. Period 70007,
    jitter up to +-60 clocks, pulse 2 missing, a 3-clock runt after pulse 1.
    tol is set for that jitter: the default, 70 clocks, can reject a good pulse
    across the missing one (two periods of drift plus the jitter of both)."""
    rng = random.Random(5)
    NOMINAL, DRIFT = 70000, 7
    rig = await Rig.start(dut)
    base = rig.now() + 500
    numbers = [0, 1, 3, 4]
    rises = [base + k * (NOMINAL + DRIFT) + rng.randrange(-60, 61) + 60 for k in numbers]
    runt_at = rises[1] + NOMINAL // 2
    for i, r in enumerate(rises):
        await rig.pulse(r, 5000)
        if i == 1:
            await rig.pulse(runt_at, 3)
    await rig.drain()
    edges = pps.Decoder().feed(rig.words)
    expect_exact(edges, rig.log)
    rep = pps.analyse(edges, NOMINAL, min_width=100, tol=300)
    dut._log.info(rep.summary())
    # all from the drive clocks
    zero = edges[0].tick - rises[0]
    assert rep.rises == [(k, r + zero) for k, r in zip(numbers, rises)]
    assert rep.intervals == [b - a for a, b in zip(rises, rises[1:])]
    assert rep.missed == [2]
    assert [p.rise for p in rep.runts] == [runt_at + zero]
    assert rep.runts[0].width == pps.RECORD and rep.runts[0].width - rep.runts[0].fall_late <= 3
    assert not rep.offgrid and rep.dropped == 0
    pts = list(zip(numbers, rises))
    xb = sum(numbers) / len(numbers)
    yb = sum(rises) / len(rises)
    slope = sum((x - xb) * (y - yb) for x, y in pts) / sum((x - xb) ** 2 for x in numbers)
    assert abs(float(rep.period) - slope) < 1e-3
    assert model_words(rig.log, pps.WRAP - 1, edges[0].tick) == rig.words


@cocotb.test()
async def pps_fifo_full(dut):
    """No read for 9 edges: four records stay, five are dropped and counted,
    and the next edge is stamped exactly: time did not stop."""
    rig = await Rig.start(dut)
    c = rig.now() + 40
    for i in range(9):
        await rig.edge((i + 1) & 1, c)
        c += 200
    await ClockCycles(dut.clk, 60)
    st = (await rig.spi.read(STATUS))[0]
    assert (st >> 4) & 15 == 2 * pps.CAPACITY
    await rig.drain()
    assert len(rig.words) == 2 * pps.CAPACITY
    next_clock = rig.now() + 40
    await rig.edge(0, next_clock)
    await ClockCycles(dut.clk, 40)
    await rig.drain()
    d = pps.Decoder()
    edges = d.feed(rig.words)
    assert len(edges) == pps.CAPACITY + 1 and d.dropped == 5 and edges[-1].drops == 5
    assert [e.tick - edges[0].tick for e in edges[:4]] == [0, 200, 400, 600]
    assert edges[-1].tick - edges[0].tick == next_clock - rig.log[0][0]
    assert edges[-1].level == 0


@cocotb.test(skip=not os.environ.get("PPS_LONG"))
async def pps_one_second(dut):
    """A real interval of 1 s plus 3 clocks at 50 MHz, nothing shortened: 50
    million clocks with the host port idle. About an hour on the compute
    runner, so it runs only with PPS_LONG=1."""
    SEC = 50_000_000
    assert TCLK_PS == 20000
    rig = await Rig.start(dut)
    c = rig.now() + 100
    await rig.pulse(c, 1000)
    await rig.pulse(c + SEC + 3, 1000)
    await rig.drain()
    edges = pps.Decoder().feed(rig.words)
    expect_exact(edges, rig.log)
    rep = pps.analyse(edges, SEC)
    dut._log.info(rep.summary())
    assert rep.intervals == [SEC + 3]
    assert rep.drift_ppm == Fraction(3_000_000, SEC)
