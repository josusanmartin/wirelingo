"""Run fpga/kv260/host/bringup_kria.py's steps on the RTL of the KV260 design
(kv260_top.v + the flow/fpga_profile.py staging of src/), through the same
GPIO words the Kria writes. pl_clk 100 MHz, protoemu clk 50 MHz, asynchronous.
Wall-clock steps (clock, UART on pins) are hardware-only and skipped."""
import pathlib
import sys

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, ReadOnly, RisingEdge, Timer
from cocotb.utils import get_sim_time
from cocotb.task import bridge, resume

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "host"))
import bringup_kria  # noqa: E402
import spim_test  # noqa: E402
import v2_demo  # noqa: E402
import usb_demo  # noqa: E402
from pe_kria import Board  # noqa: E402
from pe_spim import Spim  # noqa: E402
from pe_lab import Lab  # noqa: E402

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
    """pe_spim.SpimMem over the testbench's AXI4-Lite port (prefix "l_": pe_lab's)."""

    def __init__(self, dut, prefix="s_"):
        self.dut = dut
        self.p = {n: getattr(dut, prefix + n) for n in
                  ("awaddr", "wdata", "awvalid", "wvalid", "bready", "awready", "bvalid",
                   "araddr", "arvalid", "rready", "arready", "rvalid", "rdata")}
        self.status = 0x8010 if prefix == "s_" else None

    async def _handshake(self, ready):
        while True:
            await ReadOnly()
            ok = ready.value == 1
            await RisingEdge(self.dut.pl_clk)
            if ok:
                return

    @resume
    async def wr(self, off, v):
        p = self.p
        p["awaddr"].value, p["wdata"].value = off, v & 0xFFFFFFFF
        p["awvalid"].value = p["wvalid"].value = p["bready"].value = 1
        await self._handshake(p["awready"])
        p["awvalid"].value = p["wvalid"].value = 0
        await self._handshake(p["bvalid"])

    @resume
    async def rd(self, off):
        if off == self.status:               # STATUS: poll here until idle (no thread switch per poll)
            while await self._rd(off) & 1:
                pass
            return 0
        return await self._rd(off)

    async def _rd(self, off):
        p, d = self.p, self.dut
        p["araddr"].value, p["arvalid"].value, p["rready"].value = off, 1, 1
        await self._handshake(p["arready"])
        p["arvalid"].value = 0
        while True:
            await ReadOnly()
            if p["rvalid"].value == 1:
                v = int(p["rdata"].value)
                await RisingEdge(d.pl_clk)
                return v
            await RisingEdge(d.pl_clk)

    def buf_write(self, words):
        for i, w in enumerate(words):
            self.wr(4 * i, w)

    def buf_read(self, n):
        return [self.rd(4 * i) for i in range(n)]


async def start(dut, alt_ns=21.77, pe_ns=20, dclk=True):
    """pe_ns: protoemu's clock period (20 = 50 MHz; 20.834 = the 48 MHz
    build). dclk: run the SPI device's 400 MHz clock (8 per 50 MHz clock)."""
    cocotb.start_soon(Clock(dut.pl_clk, 10, unit="ns").start())
    cocotb.start_soon(Clock(dut.pe_clk, pe_ns, unit="ns").start(start_high=False))
    cocotb.start_soon(Clock(dut.alt_clk, alt_ns, unit="ns").start())

    async def run_dclk():                   # 400 MHz, rising edges on pe_clk's (10 ns, 30 ns, ...)
        await Timer(8750, unit="ps")
        await Clock(dut.d_clk, 2500, unit="ps").start(start_high=False)
    if dclk:
        cocotb.start_soon(run_dclk())
    else:
        dut.d_clk.value = 0
    dut.gpio0_o.value = 0x00FF0080          # AXI GPIO reset value: CS_n high, rst_n low
    for pre in ("s_", "l_"):
        for n in ("awvalid", "wvalid", "arvalid", "bready", "rready"):
            getattr(dut, pre + n).value = 0
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
            if name in ("build", "clock", "uart_tx_pin", "uart_rx_pin"):
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


@cocotb.test()
async def test_lab(dut):
    """pe_lab: the identity registers (the Makefile's placeholder), the
    pattern generator's edge times as protoemu's pins see them, and short
    runs of v2_demo.py's demos (call, la, spi, i2c) through the fabric."""
    await start(dut)
    seen, done = [], False

    async def watch_uio():                  # uio_in as protoemu's synchroniser samples it
        prev, n = None, 0
        while not done:
            await RisingEdge(dut.pe_clk)
            n += 1
            if not dut.dut.u_pe.uio_in.value.is_resolvable:
                continue
            v = int(dut.dut.u_pe.uio_in.value)
            if v != prev:
                seen.append((n, v))
                prev = v

    def run():
        board = Board(SimRegs(dut))
        board.reset(hold_s=0)
        lab = Lab(SimSpimMem(dut, "l_"))
        ident = lab.identity()
        dut._log.info(f"identity: {ident}")
        assert ident["rtl_rev"] == "0" * 40 and ident["dirty"] and ident["clk_div"] == 24
        lab.mode(Lab.PG)
        lab.pg_set(0)
        sched = [(5, 0x01), (6, 0x03), (13, 0x83), (400, 0x00)]
        n0 = len(seen)
        lab.pg_load(sched, init=0)
        lab.pg_start()
        lab.pg_wait()
        assert lab.pg_cycles() == 400, lab.pg_cycles()
        got = seen[n0:]
        rel = [(c - got[0][0] + 5, v) for c, v in got]
        dut._log.info(f"pattern generator -> uio_in: {rel}")
        assert rel == sched, rel
        nonlocal done
        done = True
        lab.mode(Lab.GPIO)
        spim = Spim(SimSpimMem(dut))
        hw = v2_demo.Hw(board, spim, lab, f_clk=50e6, timeout=1e9)
        v2_demo.demo_call(hw)
        v2_demo.demo_la(hw, sched=v2_demo.la_sync_schedule(n_ts=18, close=(1, 2, 6, 7), reps=3, bursts=1,
                                                            pause=4000), run_async=False)
        v2_demo.demo_spi(hw, delays=[0, 5, 6, 13, 14], rounds=1)
        v2_demo.demo_i2c(hw, khz=1000)
    cocotb.start_soon(watch_uio())
    await bridge(run)()


# ---------------------------------------------------------------- USB on the PMOD pads (usb_demo.py)



def pmod_host(dut, dp, dm, **kw):
    """pe.usbhost.ControlHost (toggles, retries, STALL, keep-alives) driving the
    PMOD pads dp/dm (D+/D-) through the testbench's external drivers and
    reading the resolved pads, clocked by protoemu's clock (48 MHz). Its
    transaction logic runs in the bridge() thread; each bus operation is one
    resume() into the simulator (as test/test.py's rtl_control_host)."""
    import types
    from pe import usb
    from pe.usbhost import ControlHost
    pads = {usb.J: (0, 1), usb.K: (1, 0), usb.SE0: (0, 0)}     # (D+, D-) at low speed
    ext = {"oe": 0, "val": 0}

    def drive(pin, on, v=0):
        ext["oe"] = ext["oe"] | (1 << pin) if on else ext["oe"] & ~(1 << pin)
        ext["val"] = (ext["val"] & ~(1 << pin)) | (v << pin)
        dut.ext_oe.value, dut.ext_val.value = ext["oe"], ext["val"]

    def bus():
        s = str(dut.pmod.value)                       # MSB first
        return {"01": usb.J, "10": usb.K, "00": usb.SE0}.get(s[7 - dp] + s[7 - dm], "?")

    class PmodControlHost(ControlHost):
        def __init__(self):
            super().__init__(types.SimpleNamespace(pull_up=0, chip=types.SimpleNamespace(cycle=0)), **kw)

        def _lengths(self, bits):
            out = []
            for _ in range(bits):
                self.frac += self.tpb
                out.append(int(self.frac))
                self.frac -= out[-1]
            return out

        @resume
        async def _clocks(self, n):
            await ClockCycles(dut.pe_clk, n)
            self.mh.chip.cycle += n

        @resume
        async def _drive_pads(self, states, lengths):
            for st, n in zip(states, lengths):
                p, m = pads[st]
                drive(dp, True, p)
                drive(dm, True, m)
                await ClockCycles(dut.pe_clk, n)
            drive(dp, False)
            drive(dm, False)
            self.mh.chip.cycle += sum(lengths)

        def _steps(self, bits):
            n = sum(self._lengths(bits))
            if n:
                self._clocks(n)

        def _drive(self, states):
            self._drive_pads(states, self._lengths(len(states)))

        @resume
        async def _response(self):
            trace, start, i = [], None, 0
            while True:
                await RisingEdge(dut.pe_clk)
                s = bus()
                i += 1
                if start is None:
                    if s == usb.K:
                        start = i
                        self.turnarounds.append(1 + (i - 1) / self.bit)
                    elif i >= self.TIMEOUT_BITS * self.bit:
                        self.mh.chip.cycle += i
                        return None
                if start is not None:
                    trace.append(s)
                    if len(trace) > 2 and trace[-1] == usb.J and trace[-2] == usb.SE0:
                        break
            self.mh.chip.cycle += i
            return self._decode(trace)

        @resume
        async def _reset_pads(self, cycles, recovery):
            drive(dp, True, 0)
            drive(dm, True, 0)
            await ClockCycles(dut.pe_clk, cycles)
            drive(dp, False)
            drive(dm, False)
            await ClockCycles(dut.pe_clk, recovery)
            self.mh.chip.cycle += cycles + recovery

        def bus_reset(self, cycles=48 * 100, recovery=48 * 1000):
            """100 us of SE0 (the device acts on 2.5 us; Linux sends 10-50 ms)."""
            self._reset_pads(cycles, recovery)
            self.next_keepalive = self.mh.chip.cycle

    return PmodControlHost()


class PadWatch:
    """Every change of the PMOD pads' (D+, D-) levels and of what the FPGA
    drives on any PMOD pin (pad_oe & pad_o: high levels), per protoemu clock."""

    def __init__(self, dut, dp, dm):
        self.dut, self.dp, self.dm = dut, dp, dm
        self.levels, self.driven_high, self.done = [], [], False

    async def run(self):
        d, last, n = self.dut, None, 0
        while not self.done:
            await RisingEdge(d.pe_clk)
            n += 1
            s = str(d.pmod.value)
            v = s[7 - self.dp] + s[7 - self.dm]
            if v != last:
                self.levels.append((n, v))
                last = v
            oe, o = d.dut.pad_oe.value, d.dut.pad_o.value
            if oe.is_resolvable and o.is_resolvable and int(oe) & int(o):
                self.driven_high.append((n, int(oe) & int(o)))


async def usb_demo_setup(dut, dp, dm, pull_up=0, pull_dn=0):
    await start(dut, pe_ns=20.834, dclk=False)                  # the 48 MHz build
    dut.pull_up.value, dut.pull_dn.value = pull_up, pull_dn
    dut.ext_oe.value = 0
    watch = PadWatch(dut, dp, dm)
    cocotb.start_soon(watch.run())
    return watch


def usb_demo_board(dut, out):
    board = Board(SimRegs(dut))
    board.reset(hold_s=0)
    spim = Spim(SimSpimMem(dut))
    spim.div, spim.quad = 1, False
    spim.select(fabric=True)
    pe = board.protoemu()
    pe.transport = spim
    return board, spim, pe, lambda msg: (out.append(msg), dut._log.info(msg))


@cocotb.test()
async def test_usb_demo(dut):
    """usb_demo.py on the RTL of the KV260 design at 48 MHz, wired as the
    README says with the columns as kv260.xdc assumes: D+ on pmod[0]
    (silkscreen 1), D- on pmod[1] (silkscreen 3) with the 1.5k pull-up, the
    host's 15k pull-down on D+. The script's own steps (prepare: wiring check,
    soft detach, load over the fabric master, attach, start) run unchanged;
    then nothing touches the chip's host port (CS_n/SCK/MOSI watched) while
    pe.usbhost.ControlHost runs Linux's enumeration (usb_demo.linux_flow) on the
    PMOD pads, to the Configured state. Only then is the state read."""
    from cocotb.triggers import ValueChange
    dp, dm = 0, 1
    watch = await usb_demo_setup(dut, dp, dm, pull_up=1 << dm, pull_dn=1 << dp)
    log, host_port = [], []
    marks = {}

    async def watch_port():
        last = int(dut.dut.ui_in.value) >> 5
        while True:
            await ValueChange(dut.dut.ui_in)
            v = int(dut.dut.ui_in.value) >> 5
            if v != last:
                host_port.append((get_sim_time(unit="ns"), v))
                last = v

    def run():
        board, spim, pe, say = usb_demo_board(dut, log)
        order = usb_demo.prepare(board, spim, pe, "low", hold_s=0, log=say)
        assert order == "normal", order
        marks["started"] = len(watch.levels)
        return board, spim, pe
    board, spim, pe = await bridge(run)()
    port_task = cocotb.start_soon(watch_port())
    h = pmod_host(dut, dp, dm)

    def flow():
        dev = usb_demo.linux_flow(h)
        return dev
    dev = await bridge(flow)()
    port_task.cancel()
    watch.done = True
    assert not host_port, host_port[:4]

    def readback():
        from pe import usbenum
        return usbenum.state(pe.read), spim.mem.rd(0x8020)
    st, pmod0 = await bridge(readback)()
    assert st == dict(address=7, configured=True, pending_address=False, stalled=False), st
    assert all(2.0 <= t <= 6.5 for t in h.turnarounds), (min(h.turnarounds), max(h.turnarounds))
    # pads: D- low (detached) during the load, then J (attached) before any host traffic
    pre = [v for _, v in watch.levels[:marks["started"]]]
    assert "00" in pre and pre[-1] == "01", watch.levels[:marks["started"]]
    assert "".join(log).count("pull-up found on pmod[1] = silkscreen 3") == 1, log
    assert pmod0 & 0xFFFF == 0x8180, hex(pmod0)                # pmod[0] = uio0, pmod[1] = uio1
    dut._log.info(f"usb_demo on the KV260 RTL at 48 MHz: device descriptor {dev.hex()}; {len(h.log)} "
                  f"transactions, {h.mh.chip.cycle} cycles ({h.mh.chip.cycle / 48e3:.2f} ms), turnaround "
                  f"{min(h.turnarounds):.2f}-{max(h.turnarounds):.2f} bit times, host port idle after the start; "
                  f"chip state {st}")


@cocotb.test()
async def test_usb_demo_wiring(dut):
    """usb_demo.py's wiring check against the other cases: (a) the header's
    columns the other way round (D+ on pmod[3], D- on pmod[2]): detected, the
    pins mapped accordingly, and the device answers the first GET_DESCRIPTOR;
    (b) the 3.3 V and GND columns swapped (the 1.5k goes to GND): no pull-up
    found, the script stops before enabling any pin for the chip, and no PMOD
    pin is ever driven high; (c) the pull-up on D+ (full-speed wiring) with a
    low-speed run: refused the same way."""
    from pe import sim, usbdev
    # (a) reversed columns
    dp, dm = 3, 2
    watch = await usb_demo_setup(dut, dp, dm, pull_up=1 << dm, pull_dn=1 << dp)
    log = []

    def run_a():
        board, spim, pe, say = usb_demo_board(dut, log)
        order = usb_demo.prepare(board, spim, pe, "low", hold_s=0, log=say)
        assert order == "reversed", order
        return spim.mem.rd(0x8020)
    pmod0 = await bridge(run_a)()
    assert pmod0 == 0x80810000, hex(pmod0)                     # pmod[3] = uio0 (D+), pmod[2] = uio1 (D-)
    h = pmod_host(dut, dp, dm)

    def flow_a():
        return h.get_descriptor(0, 1, 64)
    assert await bridge(flow_a)() == usbdev.DEVICE_DESC
    dut._log.info("(a) columns reversed: " + " / ".join(log))

    # (b), (c): the script must stop with every pin disabled, the core stopped
    for case, pu, pd, msg in (("b", 0, (1 << 1) | (1 << 0), "no pulled-up pin"),
                              ("c", 1 << 0, 1 << 1, "the pull-up is on D+")):
        watch.done = True
        dut.pull_up.value, dut.pull_dn.value = pu, pd
        await ClockCycles(dut.pe_clk, 10)
        watch = PadWatch(dut, 0, 1)
        cocotb.start_soon(watch.run())
        log = []

        def run_bc():
            board, spim, pe, say = usb_demo_board(dut, log)
            try:
                usb_demo.prepare(board, spim, pe, "low", hold_s=0, log=say)
            except usb_demo.WiringError as e:
                spim.pmod({})
                return str(e), spim.mem.rd(0x8020), spim.mem.rd(0x8024), pe.read(sim.A_CTRL)
            raise AssertionError("prepare accepted the wiring")
        err, p0, p1, ctrl = await bridge(run_bc)()
        watch.done = True
        assert msg in err, err
        assert p0 == p1 == 0 and ctrl & 1 == 0, (hex(p0), hex(p1), hex(ctrl))
        assert not watch.driven_high, watch.driven_high[:4]
        dut._log.info(f"({case}) {' / '.join(log)} -> refused: {err}")
