"""The 10BASE-T stack on the RTL: ARP and ping through the actual host port and pads.

pe.ethhost and pe.eth run unmodified over cocotb SPI on the real pins, as a
thread beside the simulator. The far end drives ui0 with the frames
test/model/test_eth_stack.py builds independently, with 802.3's edge jitter and a
-200 ppm clock; replies are decoded off uo[1:0]. The rate tests repeat the model's
pass/fail edges on the real port, which is the check on pe.ethsim.TimedPort.
"""
import pathlib
import random
import sys
import threading

import cocotb
from cocotb.task import bridge, resume
from cocotb.triggers import ClockCycles, Edge, FallingEdge, Timer
from cocotb.utils import get_sim_time

from pe import eth, ethhost, ethsim, host, sim
import test as base

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "model"))
import test_eth_stack as model


class Port:
    """The ProtoEmu transport calls over quad or 1-bit SPI."""

    def __init__(self, spi, quad):
        self.spi, self.quad = spi, quad

    @resume
    async def read(self, addr, n=1, fixed=False):
        if self.quad:
            w = await self.spi.qxfer(0x4B if fixed else 0x43, addr, nread=n)
        else:
            w = await self.spi.read(addr, n, fixed=fixed)
        return w[0] if n == 1 else w

    @resume
    async def write(self, addr, words, fixed=False):
        words = [words] if isinstance(words, int) else list(words)
        if self.quad:
            await self.spi.qxfer(0x4A if fixed else 0x42, addr, words=words)
        else:
            await self.spi.write(addr, words, fixed=fixed)

    @resume
    async def now_us(self):
        return get_sim_time(unit="ns") / 1000


class TdLog:
    """Bursts of (ns, level) off the pads. A burst is over a microsecond after idle.
    Sampled at the falling clock edge after a change: on a netlist with no delays
    the two TD flops' outputs don't change in the same delta, so an L to H flip
    passes through both-high inside one timestep. A level that lasts to mid-clock
    is what the pads hold, and that is what must never be both high. `flips`
    counts those transients by the level they settle on: RTL has none."""

    def __init__(self, dut):
        self.dut = dut
        self.bursts = []
        self.flips = {}
        cocotb.start_soon(self.watch())

    async def watch(self):
        td = 0
        while True:
            await Edge(self.dut.uo_out)
            first = int(self.dut.uo_out.value) & 3
            await FallingEdge(self.dut.clk)
            v = int(self.dut.uo_out.value) & 3
            assert v != 3, "TD+ and TD- both high"
            lv = (0, 1, -1, 0)[v]
            if first == 3:
                self.flips[lv] = self.flips.get(lv, 0) + 1
            if lv == td:
                continue
            td = lv
            t = get_sim_time(unit="ns")
            if self.bursts and t - self.bursts[-1].t1 < ethsim.QUIET_NS:
                self.bursts[-1].edges.append((t, lv))
            else:
                self.bursts.append(ethsim.Burst(t, lv))

    def check_flips(self):
        """Log the transients; every one of them must end at H (+1)."""
        self.dut._log.info("TD both-high transients by settled level: %s", self.flips)
        assert set(self.flips) <= {1}, self.flips

    def frames(self):
        now = get_sim_time(unit="ns")
        return [b for b in self.bursts if not b.pulse and not b.edges[-1][1]
                and now - b.t1 >= ethsim.QUIET_NS]


async def rig(dut, clock_ns=20, quad=True, sck_div=2):
    """A chip with the stack loaded, over pe_spim's CS timing: (spi, Eth10Host, TdLog)."""
    await base.setup(dut, clock_ns)
    spi = base.QuadSpi(dut, period=clock_ns * sck_div, cs_setup=2 * clock_ns, cs_hold=3 * clock_ns,
                       cs_gap=8 * clock_ns)
    if quad:
        await spi.write(sim.A_HOSTCFG, [1])
    port = Port(spi, quad)
    h = ethhost.Eth10Host(port, round(1e9 / clock_ns))
    for a, d in host.coalesce(ethhost.load_writes(1e9 / clock_ns)):
        if quad:
            await spi.qxfer(0x42, a, words=d)
        else:
            await spi.write(a, d)
    return spi, h, TdLog(dut)


class Service:
    """pe.ethhost's loop with a Station, on its own thread, until stop."""

    def __init__(self, h, reply=True):
        self.h, self.reply = h, reply
        self.station = eth.Station(model.CHIP_MAC, model.CHIP_IP)
        self.received = []
        self.stop_flag = threading.Event()

    def loop(self):
        while not self.stop_flag.is_set():
            for raw in self.h.service():
                self.received.append(raw)
                if self.reply:
                    for r in self.station.handle(raw):
                        self.h.send(r)


async def wait_for(cond, us):
    for _ in range(int(us)):
        if cond():
            return True
        await Timer(1, unit="us")
    return cond()


async def run_exchange(spi, td, frame, n=1, us=800, jitter=True, seed=7):
    """Send a frame, wait for n frames on TD, decode them twice."""
    before = len(td.frames())
    await base.eth_send(spi, frame, random.Random(seed), jitter=jitter)
    assert await wait_for(lambda: len(td.frames()) >= before + n, us), "no reply on TD"
    out = []
    for b in td.frames()[before:]:
        d = ethsim.manchester_decode(b.edges)
        assert d, "TD did not decode"
        raw = ethsim.frame_from_bits(d[0])
        assert raw is not None, "preamble"
        assert 250 <= d[1] <= 350, f"TP_IDL {d[1]}"
        assert model.sampled_decode(b) == d[0]
        out.append(raw)
    return out


@cocotb.test()
async def test_ping_rtl_50mhz(dut):
    """ARP, pings, UDP echo, and two frames that get no reply: quad SPI at 25 MHz
    SCK, the far end with +-13.5 ns of edge jitter and -200 ppm."""
    spi, h, td = await rig(dut, 20, True, 2)
    s = Service(h)
    task = cocotb.start_soon(bridge(s.loop)())
    try:
        await ClockCycles(dut.clk, 200)
        r = await run_exchange(spi, td, model.peer_arp_request())
        assert len(r) == 1
        model.check_arp_reply(r[0])
        for seq in (1, 2):
            data = model.ping_payload(seq=seq)
            r = await run_exchange(spi, td, model.peer_ping(seq, data), seed=seq)
            assert len(r) == 1
            model.check_ping_reply(r[0], seq, data)
        data = bytes(i & 255 for i in range(300))
        r = await run_exchange(spi, td, model.peer_ping(3, data), us=1500)
        model.check_ping_reply(r[0], 3, data)
        r = await run_exchange(spi, td, model.peer_udp(b"wirelingo"))
        assert r[0][14 + 20 + 8:14 + 20 + 8 + 9] == b"wirelingo"
        data = bytes((i * 7 + 3) & 255 for i in range(1472))
        fr = model.peer_ping(5, data)
        assert len(fr) == 1518
        r = await run_exchange(spi, td, fr, us=3000)
        assert len(r[0]) == 1518
        model.check_ping_reply(r[0], 5, data)
        n = len(td.frames())
        bad = model.peer_ping(4)
        await base.eth_send(spi, bad[:-1] + bytes([bad[-1] ^ 0x40]), random.Random(1))
        await base.eth_send(spi, model.peer_ping(4, dst_mac=bytes.fromhex("02574c000003")), random.Random(2))
        await Timer(300, unit="us")
        assert len(td.frames()) == n, "answered a frame it should have dropped"
        assert s.station.stats.get("bad_fcs") == 1 and s.station.stats.get("other_dst") == 1
        dut._log.info("host %s station %s", h.stats, s.station.stats)
        td.check_flips()
    finally:
        s.stop_flag.set()
        await task


@cocotb.test()
async def test_ping_rtl_40mhz(dut):
    """Quad SPI at 20 MHz SCK. No far-end jitter here: base.eth_send's is +-13.5 ns,
    past what 40 MHz receive tolerates; test_eth_stack.py runs +-12 on the model."""
    spi, h, td = await rig(dut, 25, True, 2)
    s = Service(h)
    task = cocotb.start_soon(bridge(s.loop)())
    try:
        await ClockCycles(dut.clk, 200)
        r = await run_exchange(spi, td, model.peer_arp_request(), jitter=False)
        model.check_arp_reply(r[0])
        data = model.ping_payload(seq=1)
        r = await run_exchange(spi, td, model.peer_ping(1, data), jitter=False)
        model.check_ping_reply(r[0], 1, data)
        td.check_flips()
    finally:
        s.stop_flag.set()
        await task


async def rx_rate(dut, sck_div, quad=True):
    """test_rate_table's RX run on the real pins: (received, stats)."""
    spi, h, _ = await rig(dut, 20, quad, sck_div)
    s = Service(h, reply=False)
    task = cocotb.start_soon(bridge(s.loop)())
    fr = model.test_frame(1518)
    try:
        await ClockCycles(dut.clk, 200)
        await base.eth_send(spi, fr, random.Random(3), jitter=False)
        await Timer(100, unit="us")
    finally:
        s.stop_flag.set()
        await task
    return fr in s.received, h.stats


@cocotb.test()
async def test_rx_keeps_up_quad_clk_7(dut):
    ok, stats = await rx_rate(dut, 7)
    assert ok, stats


@cocotb.test()
async def test_rx_overflows_quad_clk_8(dut):
    ok, stats = await rx_rate(dut, 8)
    assert not ok and stats.get("rx_overflows"), stats


@cocotb.test()
async def test_rx_keeps_up_1bit_clk_2(dut):
    ok, stats = await rx_rate(dut, 2, quad=False)
    assert ok, stats


@cocotb.test()
async def test_rx_overflows_1bit_clk_3(dut):
    ok, stats = await rx_rate(dut, 3, quad=False)
    assert not ok and stats.get("rx_overflows"), stats


# ns, the far end's last edge to our first: the model's 9800 from the pad, and this
# monitor reads a level at the mid-clock after it
RTL_GAP_NS = 9810


@cocotb.test()
async def test_carrier_sense_rtl(dut):
    """The core's own deferral. F_GO is set first, then the far end starts inside the
    96-bit window, so nothing but the firmware holds the frame back. It waits for the
    whole arrival and starts the stated time after the far end's last edge."""
    spi, h, td = await rig(dut, 20, True, 2)
    s = Service(h, reply=False)
    task = cocotb.start_soon(bridge(s.loop)())
    try:
        await ClockCycles(dut.clk, 500)
        rx, tx = model.test_frame(200), model.test_frame(200, seed=2)
        h.send(tx)
        assert await wait_for(lambda: h.go, 50), "F_GO never set"
        ended = []

        async def send():
            await base.eth_send(spi, rx, random.Random(5), jitter=False)
            ended.append(get_sim_time(unit="ns") - ethsim.IFG_NS)   # eth_send ends with the 96-bit gap

        sender = cocotb.start_soon(send())
        assert await wait_for(sender.done, 400)
        assert not [b for b in td.bursts if not b.pulse and b.t0 < ended[0]], "transmitted into the frame"
        assert await wait_for(td.frames, 600), "no frame on TD"
        b = td.frames()[0]
        dut._log.info("far end's last edge to our first: %s ns", b.t0 - ended[0])
        assert b.t0 - ended[0] == RTL_GAP_NS
        assert ethsim.frame_from_bits(ethsim.manchester_decode(b.edges)[0]) == tx
        assert rx in s.received
        td.check_flips()
    finally:
        s.stop_flag.set()
        await task
