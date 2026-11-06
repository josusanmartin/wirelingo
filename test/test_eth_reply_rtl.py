"""Match, then answer (firmware/eth10_reply.pasm, docs/eth10-reaction.md) on the RTL.

The program and the line unit are loaded through the real host port (quad SPI),
then the host stays away: the far end drives ui0 with frames built here with struct
and zlib, and replies are read off uo[1:0] by test_eth_stack_rtl's TdLog, which reads
TD at the falling clock edge after each change (half a clock after the pad), and
decoded by pe.ethsim's edge tracker and test_eth_stack's cell sampler. Only at the end
does the host read the records the core pushed.

    make COCOTB_TEST_MODULES=test_eth_reply_rtl SIM_BUILD=sim_build/test_eth_reply_rtl
"""
import pathlib
import random
import struct
import sys
import zlib

import cocotb
from cocotb.triggers import ClockCycles, Edge, Timer
from cocotb.utils import get_sim_time

from pe import ethreply, ethsim, host, sim
import test as base
from test_eth_stack_rtl import TdLog

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "model"))
import test_eth_stack as stack  # noqa: E402

PEER = bytes.fromhex("0200deadbe01")
THRESHOLD = 1000
IFG = ethsim.IFG_NS
TP_IDL = 300.0


def far_frame(field, size=64, ethertype=0x88B5, seed=0):
    rng = random.Random(seed)
    body = b"\xff" * 6 + PEER + struct.pack(">H", ethertype) + struct.pack("<H", field & 0xFFFF)
    body += bytes(rng.getrandbits(8) for _ in range(size - 4 - len(body)))
    return body + struct.pack("<I", zlib.crc32(body))


async def load(dut, clock_ns):
    """Reset, quad SPI at SCK = clk/2, the program and the line unit: then hands off."""
    await base.setup(dut, clock_ns)
    spi = base.QuadSpi(dut, period=clock_ns * 2, cs_setup=2 * clock_ns, cs_hold=3 * clock_ns,
                       cs_gap=8 * clock_ns)
    await spi.write(sim.A_HOSTCFG, [1])
    for a, d in host.coalesce(ethreply.load_writes(1e9 / clock_ns, THRESHOLD)):
        await spi.qxfer(0x42, a, words=d)
    await ClockCycles(dut.clk, 50)
    return spi


async def drive(spi, frames, gap_ns=IFG, jitter=0.0, ppm=-200.0, seed=1):
    """Far-end frames on ui0, each starting gap_ns after the end of the previous one's
    last bit cell (802.3 counts the gap from there); each ends with TP_IDL high, then
    idle low. Returns (first edge, last edge) per frame, in ns."""
    rng = random.Random(seed)
    half = 50.0 * (1 + ppm * 1e-6)
    t = get_sim_time(unit="ns") + 100.0
    edges, spans = [], []
    for raw in frames:
        bits = ethsim.wire_bits(raw)
        first, lv = None, 0
        for b in bits:
            for v in (1 - b, b):
                if v != lv:
                    te = t + (rng.uniform(-jitter, jitter) if jitter else 0.0)
                    edges.append((te, v))
                    first = te if first is None else first
                    lv = v
                t += half
        if not lv:
            edges.append((t, 1))
        edges.append((t + TP_IDL, 0))
        spans.append((first, t + TP_IDL))
        t += gap_ns
    for te, v in edges:
        dt = round((te - get_sim_time(unit="ns")) * 1000)
        if dt > 0:
            await Timer(dt, unit="ps")
        spi._set(0, v)
    return spans


class Hit:
    """Rises of HIT (uo2)."""

    def __init__(self, dut):
        self.dut, self.rises = dut, []
        cocotb.start_soon(self.watch())

    async def watch(self):
        prev = 0
        while True:
            await Edge(self.dut.uo_out)
            h = (int(self.dut.uo_out.value) >> (ethreply.HIT - 8)) & 1
            if h and not prev:
                self.rises.append(get_sim_time(unit="ns"))
            prev = h


def replies(td):
    """Every finished burst on TD decoded twice, as test_eth_stack does."""
    out = []
    for b in td.frames():
        bits, tp_idl = ethsim.manchester_decode(b.edges)
        assert stack.sampled_decode(b) == bits
        assert 250 <= tp_idl <= 350, tp_idl
        raw = ethsim.frame_from_bits(bits)
        assert raw is not None, "preamble and SFD"
        out.append((b, raw))
    return out


async def records(spi, n):
    return await spi.qxfer(0x4B, sim.core_reg(0, sim.C_FIFO), nread=n)


@cocotb.test()
async def test_reply_rtl_50mhz(dut):
    """Matching and other frames with 802.3's +-13.5 ns edge jitter at -200 ppm: one
    reply per matching frame, valid, starting N clocks after 96 bit times from the far
    end's last edge, with N in the model's [0, 1) plus the monitor's half clock."""
    clk = 20.0
    spi = await load(dut, clk)
    td, hit = TdLog(dut), Hit(dut)
    cases = [(2000, 0x88B5, True), (999, 0x88B5, False), (5000, 0x0800, False), (THRESHOLD, 0x88B5, True),
             (0xFFFF, 0x88B5, True)]
    spans = []
    for i, (field, et, _) in enumerate(cases):
        spans += await drive(spi, [far_frame(field, 64 + 17 * i, et, seed=i)], jitter=13.5, seed=i)
        await Timer(IFG + 57.6e3 + 3000, unit="ns")
    got = replies(td)
    assert [r for _, r in got] == [ethreply.reply_frame()] * 3
    want = [s for s, (_, _, m) in zip(spans, cases) if m]
    for (b, _), (_, last) in zip(got, want):
        n = (b.t0 - last - IFG) / clk
        dut._log.info("far end's last edge to our first: %.1f ns, N = %.2f clocks", b.t0 - last, n)
        assert 0.5 <= n < 1.5, n
    assert len(hit.rises) == 3
    assert await records(spi, 4) == [2000, THRESHOLD, 0xFFFF, 0]
    td.check_flips()


@cocotb.test()
async def test_back_to_back_rtl_40mhz(dut):
    """Two matching frames at the 802.3 minimum gap and a third that doesn't match:
    the core hears all three and answers twice after them, our two replies 96 bit
    times apart and more (the model's 10175 ns from TP_IDL's end)."""
    clk = 25.0
    spi = await load(dut, clk)
    td, hit = TdLog(dut), Hit(dut)
    spans = await drive(spi, [far_frame(2000, seed=1), far_frame(3000, seed=2), far_frame(7, seed=3)], ppm=0.0)
    await Timer(2 * (IFG + 57.6e3) + 12000, unit="ns")
    got = replies(td)
    assert [r for _, r in got] == [ethreply.reply_frame()] * 2
    (a, _), (b, _) = got
    assert a.t0 > spans[-1][1]
    n = (a.t0 - spans[-1][1] - IFG) / clk
    dut._log.info("N = %.2f clocks; our gap %.0f ns", n, b.t0 - a.t1)
    assert 0.5 <= n < 1.5, n
    assert b.t0 - a.t1 == 10175.0
    assert len(hit.rises) == 2
    assert await records(spi, 3) == [2000, 3000, 0]
    td.check_flips()
