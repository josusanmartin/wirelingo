"""Bench firmware on the RTL, through the real SPI host port
(docs/bench-firmware.md): MDIO master and PHY stand-in, the QSFP-style
stand-in on i2c_device, PMBus reads on smbus_host. Each test loads firmware
over the bit-banged SPI port of test.py, works the FIFOs through it and judges
the pads with the pins-only models and, where sigrok-cli exists, sigrok's
decoders. Short, directed runs; the sweeps are on the model
(test/model/test_{mdio,qsfpdev,pmbus}.py).

    make COCOTB_TEST_MODULES=test_bench_fw

The MDIO tests run at 50 MHz. The testbench's wire between two pads takes one
clock: cocotb reads the pads at a clock edge and its writes reach the chip
for the next. QSFP at 50 MHz, PMBus at 3.571 MHz with CLKDIV 4, tick for
tick 50 MHz / 56.
"""
import importlib.util
import pathlib
import re
import subprocess

import cocotb
from cocotb.triggers import ClockCycles, RisingEdge
from cocotb.utils import get_sim_time

from pe import asm, host, i2cbus, mdio, pmbus, postfreeze as pf, postfreeze3 as p3, qsfpdev, sigrok, sim

HERE = pathlib.Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("pe_top_tests", HERE / "test.py")
T = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(T)

FW = HERE.parent / "firmware"
HAVE_SIGROK = sigrok.cli() is not None
if not HAVE_SIGROK:
    cocotb.log.warning(sigrok.INSTALL_HINT + " -- the sigrok checks in test_bench_fw are skipped")

M_MDIO, M_MDC, P_MDIO, P_MDC = T.UIO(0), T.UO(0), T.UIO(1), T.UI(0)
PHY = 5
REGS = {mdio.BMCR: 0x1140, mdio.BMSR: 0x7949, mdio.PHYID1: 0x0143, mdio.PHYID2: 0xBC30, mdio.ANAR: 0x01E1, 31: 0xABCD}


def sigrok_skipped(dut, test, check):
    """A decoder check that did not run must say so: its test still passes."""
    dut._log.warning(f"SIGROK SKIPPED: {test}: {check}")


def load(name, defines):
    return asm.assemble((FW / f"{name}.pasm").read_text(), defines)


async def push_all(spi, core, words):
    fifo = sim.core_reg(core, sim.C_FIFO)
    while words:
        st = (await spi.read(sim.core_reg(core, sim.C_STATUS)))[0]
        room = 8 - (st & 15)
        if room:
            await spi.write(fifo, words[:room], fixed=True)
            words = words[room:]


async def drain(spi, core):
    st = (await spi.read(sim.core_reg(core, sim.C_STATUS)))[0]
    n = (st >> 4) & 15
    return await spi.read(sim.core_reg(core, sim.C_FIFO), n, fixed=True) if n else []


class MdioBus:
    """The board around the chip: the pads of MDIO tied together with a pull-up,
    MDC from uo0 to ui0 (or from a model). Logs at the pads, each clock."""

    def __init__(self, dut, spi, phy=None, sta=None):
        self.dut, self.spi, self.phy, self.sta = dut, spi, phy, sta
        self.wire = mdio.Wire()
        self.mdc_log, self.mdio_log, self.who_log = [(0.0, 0)], [(0.0, 1)], [(0.0, ())]
        self.task = cocotb.start_soon(self.run())

    async def run(self):
        dut, d_model, mdc_in = self.dut, None, 0
        while True:
            await RisingEdge(dut.clk)
            t = get_sim_time(unit="ps") / 1000
            oe, out = dut.uio_oe.value, dut.uio_out.value
            oe = int(oe) if oe.is_resolvable else 0
            out = int(out) if out.is_resolvable else 0
            uo = dut.uo_out.value
            uo = int(uo) if uo.is_resolvable else 0
            d_m = (out >> M_MDIO & 1) if oe >> M_MDIO & 1 else None
            d_p = (out >> P_MDIO & 1) if oe >> P_MDIO & 1 else None
            mdc = uo >> (M_MDC - 8) & 1
            level = min([x for x in (d_m, d_p, d_model) if x is not None], default=1)
            if self.sta:
                mdc, d_model = self.sta.step(t, level)
            if self.phy:
                d_model = self.phy.step(t, mdc, level)
            level = self.wire.resolve(d_m, d_p, d_model)
            who = tuple(n for n, x in (("master", d_m), ("phyfw", d_p), ("model", d_model)) if x is not None)
            T.EXT.set(M_MDIO, True, level)
            T.EXT.set(P_MDIO, True, level)
            if mdc != mdc_in:
                self.spi.set_low_inputs(mdc)
                mdc_in = mdc
            if mdc != self.mdc_log[-1][1]:
                self.mdc_log.append((t, mdc))
            if level != self.mdio_log[-1][1]:
                self.mdio_log.append((t, level))
            if who != self.who_log[-1][1]:
                self.who_log.append((t, who))

    def trace(self, t_end):
        tr = sigrok.Trace()
        for t, v in self.mdc_log:
            tr.set(round(t * 1000), "mdc", v)
        for t, v in self.mdio_log:
            tr.set(round(t * 1000), "mdio", v)
        tr.end(round(t_end * 1000))
        return tr


async def frames(dut, spi, words):
    out = []
    for w in words:
        await push_all(spi, 0, [w])
        for _ in range(30):
            await ClockCycles(dut.clk, 200)
            got = await drain(spi, 0)
            if got:
                out += got
                break
        else:
            raise AssertionError("no record")
    return [mdio.parse_record(w) for w in out]


async def start_master(spi):
    prog = load("mdio_master", mdio.master_defines(M_MDIO, M_MDC))
    await T.run_program(spi, 0, prog, side_pins=M_MDC, clkdiv=1 << 8)


async def start_phy(spi):
    await spi.apply(host.pin_owner_writes({P_MDIO: 1}))
    prog = load("mdio_phy", mdio.phy_defines(P_MDIO, P_MDC, PHY, 256, REGS))
    await spi.apply(mdio.phy_setup(1, prog, REGS))
    ctrl = (await spi.read(sim.A_CTRL))[0]
    await spi.apply(host.start_writes(2, ctrl & 15))


@cocotb.test()
async def bench_mdio_master_and_phy(dut):
    """mdio_master (core 0) and mdio_phy (core 1) at 50 MHz, joined outside the
    chip: PHY ID reads, control and advertising written and read back, a
    silent address. Every record exact; no contention; every frame well
    formed; MDC exactly 400 ns; sigrok's mdio decodes the same frames."""
    spi = await T.setup(dut)
    await start_phy(spi)
    bus = MdioBus(dut, spi)
    await start_master(spi)
    await ClockCycles(dut.clk, 50)
    w, r = lambda reg, v: mdio.frame_word(False, PHY, reg, v), lambda reg: mdio.frame_word(True, PHY, reg)
    plan = [r(2), r(3), w(0, 0x9200), r(0), w(4, 0x0DE1), r(4), r(31), mdio.frame_word(True, PHY + 1, 2)]
    recs = await frames(dut, spi, plan)
    await ClockCycles(dut.clk, 100)
    bus.task.cancel()
    t_end = get_sim_time(unit="ps") / 1000
    want = [{"read": True, "ack": True, "data": 0x0143}, {"read": True, "ack": True, "data": 0xBC30},
            {"read": False, "ack": None, "data": None}, {"read": True, "ack": True, "data": 0x1000},
            {"read": False, "ack": None, "data": None}, {"read": True, "ack": True, "data": 0x0DE1},
            {"read": True, "ack": True, "data": 0xABCD}, {"read": True, "ack": False, "data": 0xFFFF}]
    assert recs == want, recs
    assert bus.wire.contention == 0
    assert [mdio.parse_write(x) for x in await drain(spi, 1)] == [{"reg": 0, "ta": 2, "data": 0x9200},
                                                                  {"reg": 4, "ta": 2, "data": 0x0DE1}]
    fs = mdio.decode_frames(bus.mdc_log, bus.mdio_log)
    assert len(fs) == 8 and all(f["preamble"] >= 32 and f["st"] == 1 and f["ta"] == ((1, 0) if i < 7 else (1, 1))
                                for i, f in enumerate(fs)), fs
    assert fs[0]["preamble"] == 32
    rises = [t for t, v in bus.mdc_log if v]
    assert len(rises) == 8 * 65
    per = {b - a for fr in (rises[i:i + 65] for i in range(0, len(rises), 65)) for a, b in zip(fr, fr[1:])}
    assert per == {400.0}, per
    dut._log.info(f"MDIO: {len(rises)} rising edges, period {per} ns, contention {bus.wire.contention}")
    # clock to output: the stand-in's first drive after TA1's rising edge
    k = rises.index(fs[0]["rise"][0])
    ta1 = rises[k + 14]
    drv = next(t for t, wh in bus.who_log if t > ta1 and wh == ("phyfw",))
    dut._log.info(f"stand-in drives {drv - ta1} ns after MDC rises at uo0")
    assert drv - ta1 == 100.0                      # 4 clocks, plus the testbench's 1-clock wire
    if HAVE_SIGROK:
        got, errs = sigrok.mdio(bus.trace(t_end), "mdc", "mdio")
        dut._log.info(f"sigrok mdio: {got} {errs}")
        assert [f[1:] for f in got] == [
            ("READ", PHY, 2, 0x0143, False), ("READ", PHY, 3, 0xBC30, False), ("WRITE", PHY, 0, 0x9200, False),
            ("READ", PHY, 0, 0x1000, False), ("WRITE", PHY, 4, 0x0DE1, False), ("READ", PHY, 4, 0x0DE1, False),
            ("READ", PHY, 31, 0xABCD, False), ("READ", PHY + 1, 2, 0xFFFF, True)]
        assert [e[1] for e in errs] == ["TA invalid (bit2)"]
    else:
        sigrok_skipped(dut, "bench_mdio_master_and_phy", "mdio decoder")


@cocotb.test()
async def bench_mdio_master_phy_model(dut):
    """mdio_master against the pins-only PHY with the model's delay at 0 and
    280 ns: 20 and 300 ns from MDC's rise at the pad, since the testbench sees
    MDC one clock late. The data comes back, a write lands, the released
    turnaround leaves the line to the model, no contention."""
    spi = await T.setup(dut)
    await start_master(spi)
    for delay in (0.0, 280.0):
        phy = mdio.PhyModel(PHY, dict(REGS), delay_ns=delay)
        bus = MdioBus(dut, spi, phy=phy)
        recs = await frames(dut, spi, [mdio.frame_word(True, PHY, 2), mdio.frame_word(False, PHY, 4, 0x5AA5),
                                       mdio.frame_word(True, PHY, 4)])
        bus.task.cancel()
        await ClockCycles(dut.clk, 20)
        assert [x["data"] for x in recs] == [0x0143, None, 0x5AA5] and recs[0]["ack"], recs
        assert phy.writes == [(4, 0b10, 0x5AA5)] and bus.wire.contention == 0
        rises = [t for t, v in bus.mdc_log if v]
        ta1 = rises[32 + 14]
        drv = next(t for t, wh in bus.who_log if t >= ta1 and wh == ("model",))
        assert drv - ta1 == delay
        dut._log.info(f"PHY model delay {delay} ns: {recs}")


@cocotb.test()
async def bench_mdio_phy_sta_model(dut):
    """mdio_phy against the pins-only STA (setup and hold 200 ns): ID read, a
    31-one preamble and a Clause 45 start ignored, MDIO never driven for
    them; a write with a bad turnaround not applied but reported."""
    spi = await T.setup(dut)
    await start_phy(spi)
    S = dict(setup_ns=200.0, hold_ns=200.0, t0=5000.0)
    ops = [("r", PHY, 2), ("r", PHY, 2, {"pre": 31, "prefix": [0]}), ("r", PHY, 3, {"st": 0}), ("r", PHY, 3),
           ("w", PHY, 4, 0x1111, {"ta": 0b01}), ("r", PHY, 4)]
    sta = mdio.Sta(ops, **S)
    bus = MdioBus(dut, spi, sta=sta)
    t0 = get_sim_time(unit="ps") / 1000
    sta.events = [(t + t0, k, v) for t, k, v in sta.events]
    while not sta.done(get_sim_time(unit="ps") / 1000):
        await ClockCycles(dut.clk, 500)
    await ClockCycles(dut.clk, 50)
    bus.task.cancel()
    res = sta.results
    dut._log.info(f"STA: {res}")
    assert [r["data"] for r in res if r] == [0x0143, 0xFFFF, 0xFFFF, 0xBC30, REGS[4]]
    assert [r["ta2"] for r in res if r] == [0, 1, 1, 0, 0]
    assert bus.wire.contention == 0
    assert [mdio.parse_write(x) for x in await drain(spi, 1)] == [{"reg": 4, "ta": 1, "data": 0x1111}]


@cocotb.test()
async def bench_qsfp(dut):
    """pe.qsfpdev on i2c_device (core 1), every byte a round trip through the
    SPI port (SCK = clk/8) under clock stretching; the pins-only master at
    396.8 kHz: identifier, temperature, vendor name, page 03h threshold, an
    unsupported page reverting, roll-over at 127. The longest SCL hold with
    this polling loop is exactly 36.18 us (SFF-8636: at most 500 us)."""
    SDA, SCL = T.UIO(0), T.UIO(1)
    spi = await T.setup(dut)
    dut.pull_up.value = 3
    await spi.write(sim.A_POUT, [3])
    await spi.write(sim.A_OD, [3])
    await spi.apply(host.pin_owner_writes({SDA: 1, SCL: 1}))
    dev = qsfpdev.QsfpDevice(vendor="WIRELINGO", temperature=47.25)
    prog = load("i2c_device", {"SDA": SDA, "SCL": SCL})
    await spi.apply(dev.setup_writes(1, prog, SDA, SCL, 256, profile=T.PROFILE))
    ctrl = (await spi.read(sim.A_CTRL))[0]
    await spi.apply(host.start_writes(2, ctrl & 15))
    m = i2cbus.Master(lambda: int(dut.uio_in.value), SDA, SCL, 63)
    A = qsfpdev.ADDR
    ops = [("wr", A, [0], 1), ("wr", A, [22], 2), ("wr", A, [148], 16), ("w", A, [127, 3]), ("wr", A, [127], 1),
           ("wr", A, [128], 2), ("w", A, [127, 0x21]), ("wr", A, [127], 2)]

    async def master():
        for n in m.run(ops):
            T.EXT.set(SDA, bool(m.low >> SDA & 1), 0)
            T.EXT.set(SCL, bool(m.low >> SCL & 1), 0)
            await ClockCycles(dut.clk, n)
        T.EXT.set(SDA, False)
        T.EXT.set(SCL, False)
    holds, log = [], [(0.0, 3)]

    async def watch():
        t0 = None
        while True:
            await RisingEdge(dut.clk)
            pull = int(dut.uio_oe.value) >> SCL & 1
            c = get_sim_time(unit="ps") / 1000
            if pull and t0 is None:
                t0 = c
            elif not pull and t0 is not None:
                holds.append(c - t0)
                t0 = None
            v = int(dut.uio_in.value) & 3
            if v != log[-1][1]:
                log.append((c, v))
    w = cocotb.start_soon(watch())
    task = cocotb.start_soon(master())
    fifo = sim.core_reg(1, sim.C_FIFO)
    while not task.done():
        for ev in [x for x in await spi.read(fifo, 2, fixed=True) if x]:
            for b in dev.handle(ev):
                await spi.write(fifo, [b], fixed=True)
    await ClockCycles(dut.clk, 200)
    for ev in [x for x in await spi.read(fifo, 2, fixed=True) if x]:
        dev.handle(ev)
    w.cancel()
    t_end = get_sim_time(unit="ps") / 1000
    res = m.results
    dut._log.info(f"QSFP: {res}; longest SCL hold {max(holds) / 1000:.2f} us over {len(holds)} holds")
    assert res[0] == [0x11] and res[1] == [47, 0x40] and bytes(res[2]) == b"WIRELINGO       "
    assert res[4] == [3] and res[5] == [75, 0] and res[7] == [0, 0x11]
    assert max(holds) == 36180.0 < qsfpdev.T_CLOCK_HOLD_US * 1000
    if HAVE_SIGROK:
        tr = sigrok.Trace()
        pf.to_trace(tr, pf.bit_edges(log, SCL), "scl")
        pf.to_trace(tr, pf.bit_edges(log, SDA), "sda")
        tr.end(round(t_end * 1000))
        toks = [k for _, k in sigrok.i2c(tr, "scl", "sda", res_ps=10_000)]
        dut._log.info(f"sigrok i2c: {toks[:12]} ...")
        assert not [k for k in toks if isinstance(k, tuple) and k[0] == "WARN"]
        reads = [k[1] for k in toks if isinstance(k, tuple) and k[0] == "DR"]
        assert bytes(reads[3:19]) == b"WIRELINGO       "
    else:
        sigrok_skipped(dut, "bench_qsfp", "i2c decoder")


@cocotb.test()
async def bench_pmbus(dut):
    """smbus_host (core 0) and the pins-only PMBus device, the chip at 3.571 MHz
    with CLKDIV 4: READ_TEMPERATURE_1 and READ_VIN decoded as LINEAR11, with
    PEC; a bit flipped on the wire is rejected and yields no value; the next
    read is right. sigrok's i2c decodes the bus and the PEC is recomputed
    over its bytes."""
    PERIOD = 280.0
    SDA, SCL = T.UIO(0), T.UIO(1)
    ADDR = 0x40
    spi = await T.setup(dut, clock_ns=PERIOD)
    dut.pull_up.value = 3
    await spi.write(sim.A_POUT, [3])
    await spi.write(sim.A_OD, [3])
    await spi.write(sim.core_reg(0, sim.C_CRCCTRL), [p3.SMBUS_CRCCTRL])
    await T.run_program(spi, 0, load("smbus_host", p3.smbus_defines(SDA, SCL, 7)), clkdiv=4 << 8)
    dev = pmbus.PmbusDevice(ADDR, {pmbus.READ_TEMPERATURE_1: 0xE32A, pmbus.READ_VIN: 0xDB12})

    async def bus():
        while True:
            await RisingEdge(dut.clk)
            v = int(dut.uio_in.value)
            sc, sd = dev.step(get_sim_time(unit="ps") / 1000, v >> SCL & 1, v >> SDA & 1)
            T.EXT.set(SCL, not sc, 0)
            T.EXT.set(SDA, not sd, 0)
    task = cocotb.start_soon(bus())
    log, t0 = [], None

    async def watch():
        while True:
            await RisingEdge(dut.clk)
            v = int(dut.uio_in.value) & 3
            if not log or v != log[-1][1]:
                log.append((get_sim_time(unit="ps") / 1000, v))
    w = cocotb.start_soon(watch())

    async def read(cmd):
        n = len(words)
        await push_all(spi, 0, pmbus.read_word_words(ADDR, cmd))
        for _ in range(40):
            await ClockCycles(dut.clk, 400)
            words.extend(await drain(spi, 0))
            if p3.smbus_records(words[n:]):
                return p3.smbus_records(words[n:])[-1]
        raise AssertionError("no record")
    words = []
    t = pmbus.linear11(pmbus.parse_word(await read(pmbus.READ_TEMPERATURE_1)))
    v = pmbus.linear11(pmbus.parse_word(await read(pmbus.READ_VIN)))
    dev.flip = 12                                  # a data bit of the next read
    bad = await read(pmbus.READ_TEMPERATURE_1)
    good = await read(pmbus.READ_TEMPERATURE_1)
    task.cancel()
    w.cancel()
    t_end = get_sim_time(unit="ps") / 1000
    dut._log.info(f"PMBus: temperature {float(t)} C, VIN {float(v)} V; flipped read {bad}")
    assert float(t) == 50.625 and float(v) == 24.5625
    assert bad["result"] == "pec_bad"
    rejected = False
    try:
        pmbus.parse_word(bad)
    except pmbus.BadPec:
        rejected = True
    assert rejected and pmbus.parse_word(good) == 0xE32A
    if HAVE_SIGROK:
        tr = sigrok.Trace()
        pf.to_trace(tr, pf.bit_edges(log, SCL), "scl")
        pf.to_trace(tr, pf.bit_edges(log, SDA), "sda")
        tr.end(round(t_end * 1000))
        frames_, cur = [], None
        for _, k in sigrok.i2c(tr, "scl", "sda", res_ps=10_000):
            if k == "S":
                cur = []
                frames_.append(cur)
            elif isinstance(k, tuple) and k[0] in ("AW", "AR", "DW", "DR"):
                cur.append((k[1] << 1 | (k[0] == "AR")) if k[0][0] == "A" else k[1])
        dut._log.info(f"sigrok i2c: {[bytes(f).hex() for f in frames_]}")
        assert len(frames_) == 4
        assert [p3.smbus_pec(bytes(f[:-1])) == f[-1] for f in frames_] == [True, True, False, True]
    else:
        sigrok_skipped(dut, "bench_pmbus", "i2c decoder and PEC recomputed over its bytes")


@cocotb.test(skip=not HAVE_SIGROK)
async def bench_sigrok_available(dut):
    """Passes only where sigrok-cli exists, and is skipped elsewhere, so that a
    run without the decoder checks shows SKIP=1 in its summary rather than a
    clean pass. It checks that both decoders the tests need are there."""
    listed = subprocess.run([sigrok.cli(), "-L"], capture_output=True, text=True, timeout=60).stdout
    assert re.search(r"^\s*mdio\s", listed, re.M) and re.search(r"^\s*i2c\s", listed, re.M)
