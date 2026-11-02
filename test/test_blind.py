"""Blind protocol test (docs/blind-test.md): firmware added on the frozen RTL
for two protocols drawn by rule, on the RTL through the real SPI host port.

Each test loads the firmware over SPI, feeds its FIFO over SPI, and runs a
pins-only far end written from the public timing (test/model/hd44780_device.py,
test/model/ata_device.py). The far end reacts to value changes of the pads and
drives back at the exact nanosecond its table says, not at a clock edge.

No sigrok decoder for either protocol exists in sigrok-cli 0.7.2. The generic
`parallel` decoder is used where it helps: it knows nothing of the protocol, so
it is an independent reading of the bus values at the strobe edges, no more.

    make COCOTB_TEST_MODULES=test_blind
    make COCOTB_TEST_MODULES=test_blind COCOTB_TEST_FILTER=hd_
"""
import importlib.util
import pathlib
import sys

import cocotb
from cocotb.triggers import First, Timer, ValueChange
from cocotb.utils import get_sim_time

from pe import asm, host, sigrok, sim

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "model"))
import ata_device as ata
import ata_host as ah
import hd44780_device as lcd

_spec = importlib.util.spec_from_file_location("pe_post_freeze", HERE / "test_post_freeze.py")
PF = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(PF)
T = PF.T
FW = HERE.parent / "firmware"
HAVE_SIGROK = sigrok.cli() is not None


def bits(v):
    """An integer from a logic value, with x/z read as 0."""
    s = str(v)
    return sum(1 << i for i, c in enumerate(reversed(s)) if c == "1")


class FarEnd:
    """Runs a device against the pads. feed(t_ns, uo, uio_out, uio_oe, ui)
    hands it the levels; device.drive(t) gives (value, oe) on the uio bits in
    `mask`; device.next_change(t) tells when to look again."""

    def __init__(self, dut, dev, feed, mask, shift=0, ui=None):
        self.dut, self.dev, self.feed, self.mask, self.shift = dut, dev, feed, mask, shift
        self.ui = ui          # optional: called with t after each update, to drive ui_in bits
        self.task = cocotb.start_soon(self.run())

    def now(self):
        return get_sim_time(unit="ps") / 1000

    def apply(self, t):
        v, oe = self.dev.drive(t)
        e = T.EXT
        e.oe = (e.oe & ~self.mask) | ((oe << self.shift) & self.mask)
        e.val = (e.val & ~self.mask) | ((v << self.shift) & self.mask)
        self.dut.ext_oe.value = e.oe
        self.dut.ext_val.value = e.val

    async def run(self):
        d = self.dut
        while True:
            t = self.now()
            self.feed(t, bits(d.uo_out.value), bits(d.uio_out.value), bits(d.uio_oe.value))
            self.apply(t)
            if self.ui:
                self.ui(t)
            trig = [ValueChange(d.uo_out), ValueChange(d.uio_out), ValueChange(d.uio_oe)]
            nc = self.dev.next_change(t)
            if nc is not None:
                trig.append(Timer(max(1, round((nc - t) * 1000)), unit="ps"))
            await First(*trig)
            await Timer(1, unit="ps")   # let every pad of this edge settle

    def stop(self):
        self.task.cancel()


def parallel(trace, clk, data, edge):
    """Words sigrok's generic parallel decoder reads at each `edge` of clk."""
    pins = ":".join(f"d{i}={n}" for i, n in enumerate(data))
    anns = sigrok.decode(trace, f"parallel:clk={clk}:{pins}:clock_edge={edge}", "parallel=items", res_ps=1000)
    return [int(a.text.split()[-1], 16) for a in anns]


async def reply(dut, spi, core=0, limit=20_000):
    """One word from a core's RX FIFO, polling STATUS over SPI."""
    st_addr, fifo = sim.core_reg(core, sim.C_STATUS), sim.core_reg(core, sim.C_FIFO)
    for _ in range(limit):
        st = (await spi.read(st_addr))[0]
        if (st >> 4) & 15:
            return (await spi.read(fifo, 1, fixed=True))[0]
    raise AssertionError("no reply over SPI")


# ------------------------------------------------------------------ HD44780

HD_DB, HD_RS, HD_RW, HD_E = 0, 8, 9, 10
HD_INIT, HD_RDBIT, HD_RSBIT, HD_STUCK = 0x400, 0x200, 0x100, 0x100


def hd_prog(bfto=6000):
    return asm.assemble((FW / "hd44780_4bit.pasm").read_text(), {"RS": HD_RS, "RW": HD_RW, "BFTO": bfto})


def hd_farend(dut, dev):
    def feed(t, uo, uio_out, uio_oe):
        oe = uio_oe & 0xF
        db = (uio_out & oe) | (0xF & ~oe)        # undriven: the LCD's pull-ups
        dev.feed(t, uo & 1, (uo >> 1) & 1, (uo >> 2) & 1, db, oe)
    return FarEnd(dut, dev, feed, 0x0F)


async def hd_start(dut, dev, clock_ns, div, bfto=6000):
    spi = await T.setup(dut, clock_ns=clock_ns)
    fe = hd_farend(dut, dev)
    await T.run_program(spi, 0, hd_prog(bfto), out_pins=(HD_DB, 4), in_pins=HD_DB, side_pins=HD_E, clkdiv=div)
    return spi, fe


async def hd_cmd(dut, spi, word):
    await spi.write(sim.core_reg(0, sim.C_FIFO), [word], fixed=True)
    return await reply(dut, spi)


def hd_nibbles(trace):
    """(RW, RS, nibble) at each E fall, from sigrok alone."""
    w = parallel(trace, "e", ["db4", "db5", "db6", "db7", "rs", "rw"], "falling")
    return [((x >> 5) & 1, (x >> 4) & 1, x & 15) for x in w]


def hd_trace(pads):
    return pads.trace({"e": ("uo", 2), "rs": ("uo", 0), "rw": ("uo", 1), "db4": ("uio", 0),
                       "db5": ("uio", 1), "db6": ("uio", 2), "db7": ("uio", 3)})


@cocotb.test()
async def hd_init_text_readback(dut):
    """Power on, Figure 24, a line of text and its read-back, at a 10 MHz chip
    clock with CLKDIV 1 (57 ms of waits at 100 ns per tick, the same ticks as
    CLKDIV 5 at 50 MHz). sigrok's parallel decoder reads the nibbles at E's fall."""
    dev = lcd.Hd44780()
    spi, fe = await hd_start(dut, dev, 100, 1 << 8)
    pads = PF.Pads(dut)
    assert await hd_cmd(dut, spi, HD_INIT) == 0
    for b in (0x28, 0x08, 0x01, 0x06, 0x0C):
        assert await hd_cmd(dut, spi, b) == 0
    text = b"Wirelingo blind"
    assert await hd_cmd(dut, spi, 0x80 | 0x40) == 0
    for c in text:
        assert await hd_cmd(dut, spi, HD_RSBIT | c) == 0
    assert await hd_cmd(dut, spi, 0x80 | 0x40) == 0
    got = bytes([(await hd_cmd(dut, spi, HD_RDBIT | HD_RSBIT | 0)) & 0xFF for _ in text])
    await PF.wait_ns(dut, 100e3, 100)
    pads.stop()
    fe.stop()
    assert dev.line(0x40, len(text)) == text and got == text, (dev.line(0x40, len(text)), got)
    assert not dev.dl8 and dev.lines2 and dev.display
    assert not dev.errors, dev.errors
    dut._log.info(f"HD44780 init: first write {dev.e_falls[0] / 1e6:.3f} ms after power on, "
                  f"gaps {[round((b - a) / 1e3, 1) for a, b in zip(dev.e_falls[:4], dev.e_falls[1:4])]} us")
    assert dev.e_falls[0] > 40e6
    if HAVE_SIGROK:
        dut._log.info("HD44780: sigrok parallel decoder ran")
        ns = hd_nibbles(hd_trace(pads))
        assert ns[:4] == [(0, 0, 3), (0, 0, 3), (0, 0, 3), (0, 0, 2)], ns[:4]
        # pair the rest: writes as the far end logged them, reads with their RW bit
        writes = [n for rw, _, n in ns[4:] if not rw]
        rs_w = [rs for rw, rs, _ in ns[4:] if not rw]
        pairs = [(rs_w[i], writes[i] << 4 | writes[i + 1]) for i in range(0, len(writes), 2)]
        want = [(0, b) for b in (0x28, 0x08, 0x01, 0x06, 0x0C, 0xC0)] + [(1, c) for c in text] + [(0, 0xC0)]
        assert pairs == want, pairs
        reads = [n for rw, rs, n in ns[4:] if rw and rs]
        # the decoder emits an item at the next clock edge, so a capture's last nibble never appears
        assert reads == [n for c in text for n in (c >> 4, c & 15)][:-1], reads


@cocotb.test()
async def hd_bus_figures_50mhz(dut):
    """At 50 MHz (CLKDIV 5), on an LCD already in 4-bit mode: exact E timing,
    and a write that has to follow BF through a 1.52 ms clear."""
    dev = lcd.Hd44780(power_on_busy=False, t_power=None)
    dev.dl8, dev.fs_count = False, 3
    spi, fe = await hd_start(dut, dev, 20, 5 << 8)
    assert await hd_cmd(dut, spi, 0x28) == 0
    er, ef = dev.e_rises, dev.e_falls
    highs = [round(b - a, 3) for a, b in zip(er, ef)]
    assert highs == [800, 800, 600, 600], highs
    assert [round(b - a, 3) for a, b in zip(er, er[1:])][0] == 1600
    assert round(er[3] - er[2], 3) == 1200
    assert await hd_cmd(dut, spi, 0x01) == 0
    t_clear = dev.log[-1][0]
    assert await hd_cmd(dut, spi, HD_RSBIT | 0x41) == 0
    t_data = dev.log[-1][0]
    assert await hd_cmd(dut, spi, 0x80) == 0
    assert (await hd_cmd(dut, spi, HD_RDBIT | HD_RSBIT)) & 0xFF == 0x41
    fe.stop()
    assert dev.ddram[0] == 0x41 and not dev.errors, dev.errors
    wait = t_data - t_clear
    dut._log.info(f"HD44780: write landed {wait / 1e3:.1f} us after clear")
    assert 1520e3 < wait < 1550e3
    dut._log.info(f"HD44780: smallest figures at the pads {dev.seen}")
    assert dev.seen == {"tAS": 300, "tAH": 300, "tH": 400, "PWEH": 600, "tcycE": 1200, "tDSW": 800}, dev.seen


@cocotb.test()
async def hd_bf_ac_read_50mhz(dut):
    """The busy-flag read itself: raw BF and AC, while busy and after."""
    dev = lcd.Hd44780(power_on_busy=False, t_power=None)
    dev.dl8, dev.fs_count = False, 3
    spi, fe = await hd_start(dut, dev, 20, 5 << 8)
    # one burst: the read must run right behind the write, not an SPI round trip later
    await spi.write(sim.core_reg(0, sim.C_FIFO), [0x80 | 0x35, HD_RDBIT], fixed=True)
    assert await reply(dut, spi) == 0
    assert await reply(dut, spi) == 0x80 | 0x35
    await PF.wait_ns(dut, 50e3, 20)
    assert (await hd_cmd(dut, spi, HD_RDBIT)) == 0x35
    fe.stop()
    assert not dev.errors, dev.errors


@cocotb.test()
async def hd_stuck_busy_times_out(dut):
    """BF stuck at 1: no byte is written, the reply says so, the core lives."""
    dev = lcd.Hd44780(power_on_busy=False, stuck_busy=True, t_power=None)
    dev.dl8, dev.fs_count = False, 3
    spi, fe = await hd_start(dut, dev, 20, 5 << 8, bfto=50)
    n = len(dev.log)
    assert await hd_cmd(dut, spi, HD_RSBIT | 0x41) == HD_STUCK
    assert await hd_cmd(dut, spi, 0x01) == HD_STUCK
    assert len(dev.log) == n
    assert len(dev.e_rises) == 2 * 2 * 51       # 2 E pulses per poll, 51 polls, 2 commands
    fe.stop()


@cocotb.test()
async def hd_warm_restart_mid_nibble(dut):
    """The host restarted with the LCD half way through a 4-bit byte."""
    dev = lcd.Hd44780(power_on_busy=False, t_power=None)
    dev.dl8, dev.fs_count = False, 3
    dev.pending_hi = 0
    spi, fe = await hd_start(dut, dev, 100, 1 << 8)
    assert await hd_cmd(dut, spi, HD_INIT) == 0
    for b in (0x28, 0x80):
        assert await hd_cmd(dut, spi, b) == 0
    assert await hd_cmd(dut, spi, HD_RSBIT | 0x5A) == 0
    fe.stop()
    assert dev.ddram[0] == 0x5A and not dev.dl8 and not dev.errors, dev.errors


# ------------------------------------------------------------------ ATA PIO

ATA_IORDY, ATA_ITO, ATA_FIO = 16, 70, 7


def ata_prog(ito=ATA_ITO):
    return asm.assemble((FW / "ata_pio.pasm").read_text(), {"IORDY": ATA_IORDY, "ITO": ito, "FIO": ATA_FIO})


def ata_farend(dut, spi, dev):
    def feed(t, uo, uio_out, uio_oe):
        dev.feed(t, uo & 7, (uo >> 3) & 1, (uo >> 4) & 1, (uo >> 5) & 1, (uo >> 6) & 1, uio_out & uio_oe, uio_oe)

    state = [1]

    def ui(t):
        v = dev.iordy(t)
        if v != state[0]:
            state[0] = v
            spi.set_low_inputs(v)

    spi.set_low_inputs(1)                 # IORDY's pull-up
    return FarEnd(dut, dev, feed, 0xFF, ui=ui)


async def ata_start(dut, dev):
    """50 MHz, CLKDIV 1. The strobes and selects go high before the core runs."""
    spi = await T.setup(dut)
    await spi.write(sim.A_POUT, [0x7F00])
    fe = ata_farend(dut, spi, dev)
    await T.run_program(spi, 0, ata_prog(), out_pins=(0, 13), in_pins=0, side_pins=13, clkdiv=1 << 8)
    return spi, fe


async def ata_push(spi, words):
    await PF.push_all(spi, 0, list(words))


async def ata_replies(dut, spi, n):
    out = []
    while len(out) < n:
        out += await PF.drain(spi, 0)
    assert len(out) == n, out
    return out


async def ata_status(dut, spi, units=20):
    await ata_push(spi, [ah.wait(units)])
    return (await ata_replies(dut, spi, 1))[0]


async def ata_ready(dut, spi):
    await ata_push(spi, ah.SRST)
    s = await ata_status(dut, spi, 300)
    assert s == 0x50, hex(s)
    await ata_push(spi, [ah.wr(ah.R_ERR, 0x01), ah.wr(ah.R_CMD, 0xEF)])
    s = await ata_status(dut, spi)
    assert s == 0x50, hex(s)


def ata_trace(pads):
    ch = {"dior": ("uo", 5), "diow": ("uo", 6), "a0": ("uo", 0), "a1": ("uo", 1), "a2": ("uo", 2),
          "cs0": ("uo", 3), "cs1": ("uo", 4)}
    ch.update({f"d{i}": ("uio", i) for i in range(8)})
    return pads.trace(ch)


@cocotb.test()
async def ata_identify_read_write(dut):
    """SRST, 8-bit mode, IDENTIFY, a sector read, a sector write, through the
    SPI port at 50 MHz. The port at clk/8 is far slower than the bus, so the
    FIFO-stall paths run all the time. sigrok's parallel decoder reads every
    write strobe's byte and address independently."""
    dev = ata.AtaDevice()
    spi, fe = await ata_start(dut, dev)
    pads = PF.Pads(dut)
    await ata_ready(dut, spi)
    assert dev.bus8

    await ata_push(spi, [ah.wr(ah.R_DH, 0xE0), ah.wr(ah.R_CMD, 0xEC)])
    assert await ata_status(dut, spi) == 0x58
    await ata_push(spi, [ah.bread(128)])
    got = await ata_replies(dut, spi, 129)
    assert got[0] == 0x58
    ident = b"".join(w.to_bytes(4, "big") for w in got[1:])
    assert int.from_bytes(ident[:2], "little") == 0x848A
    assert await ata_status(dut, spi) == 0x50

    await ata_push(spi, ah.lba(5) + [ah.wr(ah.R_CMD, 0x20)])
    assert await ata_status(dut, spi) == 0x58
    await ata_push(spi, [ah.bread(128)])
    got = await ata_replies(dut, spi, 129)
    assert b"".join(w.to_bytes(4, "big") for w in got[1:]) == dev.sectors[5]
    assert await ata_status(dut, spi) == 0x50

    data = bytes((i * 11 + 3) & 255 for i in range(512))
    await ata_push(spi, ah.lba(9) + [ah.wr(ah.R_CMD, 0x30)])
    assert await ata_status(dut, spi) == 0x58
    await ata_push(spi, [ah.bwrite(128)] + ah.be_words(data))
    assert (await ata_replies(dut, spi, 1))[0] == 0x58
    assert await ata_status(dut, spi) == 0x50
    assert dev.written[9] == data

    await ata_push(spi, [ah.rd(ah.R_SN), ah.wr(ah.R_SN, 1), ah.rd(ah.R_SN)])
    await ata_replies(dut, spi, 2)
    await PF.wait_ns(dut, 2000, 20)
    pads.stop()
    fe.stop()
    assert not dev.errors, dev.errors[:5]

    lows = {round(b - a, 3) for a, b, *_ in dev.cycles}
    falls = [c[0] for c in dev.cycles]
    gap = min(b - a for a, b in zip(falls, falls[1:]))
    dut._log.info(f"ATA: {len(dev.cycles)} cycles, strobe low {sorted(lows)} ns, min cycle {gap:.1f} ns")
    assert lows == {320.0}
    assert gap == 620
    dut._log.info(f"ATA: smallest figures at the pads {dev.seen}")
    assert dev.seen == {"t0": 620, "t1": 100, "t2": 320, "t3": 360, "t4": 100, "t9": 160}, dev.seen

    if HAVE_SIGROK:
        dut._log.info("ATA: sigrok parallel decoder ran")
        tr = ata_trace(pads)
        wvals = parallel(tr, "diow", [f"d{i}" for i in range(8)], "rising")
        waddr = parallel(tr, "diow", ["a0", "a1", "a2", "cs0", "cs1"], "rising")
        dev_w = [c for c in dev.cycles if c[2] == "w"]
        assert wvals == [c[6] for c in dev_w][:-1]              # all but the last, see parallel()
        assert waddr == [c[5] | c[3] << 3 | c[4] << 4 for c in dev_w][:-1]
        # the Data register by sigrok's own address reading: CS0- low, CS1- high, DA 0
        assert bytes(v for v, x in zip(wvals, waddr) if x == 0x10) == data


@cocotb.test()
async def ata_iordy_extends(dut):
    """IORDY held low 1000 ns on every cycle: the strobe stretches to fit and
    the data, invalid until IORDY returns, still reads right."""
    dev = ata.AtaDevice()
    spi, fe = await ata_start(dut, dev)
    await ata_ready(dut, spi)
    dev.iordy_low_ns = 1000
    dev.cycles.clear()
    await ata_push(spi, ah.lba(2) + [ah.wr(ah.R_CMD, 0x20)])
    assert await ata_status(dut, spi) == 0x58
    await ata_push(spi, [ah.bread(128)])
    got = await ata_replies(dut, spi, 129)
    fe.stop()
    assert b"".join(w.to_bytes(4, "big") for w in got[1:]) == dev.sectors[2]
    assert not dev.errors, dev.errors[:5]
    lows = {round(b - a, 3) for a, b, *_ in dev.cycles}
    dut._log.info(f"ATA: strobe low with IORDY held 1000 ns: {sorted(lows)} ns")
    assert lows == {1100.0}
    assert not (await spi.read(sim.A_FSET))[0] & (1 << ATA_FIO)


@cocotb.test()
async def ata_iordy_stuck_and_bsy_stuck(dut):
    """Two dead devices: neither can hang the core, both are reported."""
    dev = ata.AtaDevice(iordy_stuck=True)
    spi, fe = await ata_start(dut, dev)
    await ata_push(spi, [ah.rd(ah.R_SN)])
    await ata_replies(dut, spi, 1)
    assert {round(b - a, 3) for a, b, *_ in dev.cycles} == {1760.0}
    assert (await spi.read(sim.A_FSET))[0] & (1 << ATA_FIO)
    fe.stop()

    dev2 = ata.AtaDevice(stuck_bsy=True)
    fe2 = ata_farend(dut, spi, dev2)
    await ata_push(spi, [ah.wait(0)])
    s = (await ata_replies(dut, spi, 1))[0]
    fe2.stop()
    assert s == (ah.TIMED_OUT | 0xD0)
    assert sum(c[2] == "r" for c in dev2.cycles) == 1024


@cocotb.test()
async def ata_status_window_and_refusal(dut):
    """The device keeps the old status for the 400 ns it may. The firmware's
    first poll is past that; then an out-of-range LBA ends in ERR and the
    block op moves no data."""
    dev = ata.AtaDevice()
    spi, fe = await ata_start(dut, dev)
    await ata_ready(dut, spi)
    dev.cycles.clear()
    await ata_push(spi, ah.lba(1000) + [ah.wr(ah.R_CMD, 0x20), ah.wait()])
    s = (await ata_replies(dut, spi, 1))[0]
    assert s == 0x51, hex(s)
    rise = next(c[1] for c in dev.cycles if c[2] == "w" and c[5] == 7)
    first = next(c[0] for c in dev.cycles if c[0] > rise and c[2] == "r")
    dut._log.info(f"ATA: first poll {first - rise:.1f} ns after the command write")
    assert first - rise >= 400
    n = len(dev.cycles)
    await ata_push(spi, [ah.bread(128)])
    assert (await ata_replies(dut, spi, 1))[0] == (ah.REFUSED | 0x51)
    await ata_push(spi, [ah.bwrite(2), 1, 2])
    assert (await ata_replies(dut, spi, 1))[0] == (ah.REFUSED | 0x51)
    assert [c for c in dev.cycles[n:] if c[3] == 0] == []
    fe.stop()
    assert not dev.errors, dev.errors[:5]


@cocotb.test()
async def ata_16bit_device_is_the_no_fit(dut):
    """A device without the CFA feature set aborts 8-bit mode; IDENTIFY then
    gives the low byte of each word and the high bytes are gone."""
    dev = ata.AtaDevice(cfa=False)
    spi, fe = await ata_start(dut, dev)
    await ata_push(spi, ah.SRST)
    assert await ata_status(dut, spi, 300) == 0x50
    await ata_push(spi, [ah.wr(ah.R_ERR, 0x01), ah.wr(ah.R_CMD, 0xEF)])
    assert await ata_status(dut, spi) == 0x51
    await ata_push(spi, [ah.rd(ah.R_ERR)])
    assert (await ata_replies(dut, spi, 1))[0] == ata.ABRT
    await ata_push(spi, [ah.wr(ah.R_DH, 0xE0), ah.wr(ah.R_CMD, 0xEC)])
    assert await ata_status(dut, spi) == 0x58
    await ata_push(spi, [ah.bread(64)])
    got = await ata_replies(dut, spi, 65)
    fe.stop()
    assert b"".join(w.to_bytes(4, "big") for w in got[1:]) == bytes(dev.buf)[0::2]
    assert dev.highbyte_lost == 256 and not dev.errors, dev.errors[:5]


@cocotb.test()
async def ata_read_sample_point(dut):
    """DD is sampled 280 ns after DIOR- falls: data valid at 270 ns reads right,
    at 290 ns it does not. Mode 0 promises 240 ns (t2 - t5)."""
    dev = ata.AtaDevice()
    spi, fe = await ata_start(dut, dev)
    await ata_push(spi, [ah.wr(ah.R_SN, 0x5A), ah.rd(ah.R_SN)])
    assert (await ata_replies(dut, spi, 1))[0] == 0x5A
    dev.tm = dev.tm._replace(t5=20)       # valid at 290 - 20 = 270 ns
    await ata_push(spi, [ah.rd(ah.R_SN)])
    assert (await ata_replies(dut, spi, 1))[0] == 0x5A
    dev.tm = dev.tm._replace(t5=0)        # valid at 290 ns
    await ata_push(spi, [ah.rd(ah.R_SN)])
    assert (await ata_replies(dut, spi, 1))[0] == (~0x5A & 0xFF)
    fe.stop()


@cocotb.test()
async def hd_read_sample_point(dut):
    """At 50 MHz DB is sampled 660 ns after E rises: tDDR 650 reads right, 670
    does not. The datasheet's maximum is 360 ns."""
    dev = lcd.Hd44780(power_on_busy=False, t_power=None)
    dev.dl8, dev.fs_count = False, 3
    spi, fe = await hd_start(dut, dev, 20, 5 << 8)
    assert await hd_cmd(dut, spi, 0x80 | 0x25) == 0
    await PF.wait_ns(dut, 50e3, 20)
    dev.tm = dev.tm._replace(tddr=650)
    assert await hd_cmd(dut, spi, HD_RDBIT) == 0x25
    dev.tm = dev.tm._replace(tddr=670)
    assert await hd_cmd(dut, spi, HD_RDBIT) == (~0x25 & 0xFF)
    fe.stop()


@cocotb.test()
async def ata_reset_window_is_a_real_limit(dut):
    """pin_out resets to 0 and P8-P14 always drive, so from reset until the
    host's first PIN_OUT write both strobes and both chip selects are active.
    ATA-3 Table 6 calls CS0- = CS1- = 0 an invalid address, and no register is
    touched here, but the pins do break the bus rules: this test records that
    instead of hiding it. docs/blind-test.md lists it as open."""
    dev = ata.AtaDevice()
    spi = await T.setup(dut)
    fe = ata_farend(dut, spi, dev)
    await PF.wait_ns(dut, 2000, 20)
    assert bits(dut.uo_out.value) & 0x7F == 0
    await spi.write(sim.A_POUT, [0x7F00])
    await PF.wait_ns(dut, 200, 20)
    assert (bits(dut.uo_out.value) >> 3) & 15 == 15
    assert any("both active" in e for e in dev.errors) and any("no chip select" in e for e in dev.errors)
    assert dev.sn == 1 and dev.commands == [], (dev.sn, dev.commands)
    assert [c for c in dev.cycles if c[3] + c[4] == 1] == []       # no valid-address cycle happened
    dev.errors.clear()
    await T.run_program(spi, 0, ata_prog(), out_pins=(0, 13), in_pins=0, side_pins=13, clkdiv=1 << 8)
    await ata_push(spi, [ah.wr(ah.R_SN, 0x5A), ah.rd(ah.R_SN)])
    assert (await ata_replies(dut, spi, 1))[0] == 0x5A
    fe.stop()
    assert not dev.errors, dev.errors[:5]
