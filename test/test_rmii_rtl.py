"""RMII on the RTL through the real quad SPI port and the pins (docs/rmii-study.md):
the same far end and the same frames as test/model/test_rmii.py, tapeout geometry.
Run alone: remote_test.sh test_rmii_rtl.

A light test: the PHY changes its pins on the falling clock edge, so with zero wire
delay each value is sampled at the next rising edge, as the model's are."""
import cocotb
from cocotb.triggers import ClockCycles, FallingEdge, RisingEdge

from pe import host, rmii, sim
import test as base

T = base.CLK_NS
FIFO0, FIFO1 = sim.core_reg(0, sim.C_FIFO), sim.core_reg(1, sim.C_FIFO)


def quad(dut):
    return base.QuadSpi(dut, period=T, cs_setup=T, cs_hold=T, cs_gap=4 * T)


async def setup(dut):
    await base.setup(dut)
    dut.ext_oe.value = 0x1F
    dut.ext_val.value = 0
    spi = quad(dut)
    slow = base.Spi(dut)
    await slow.write(sim.A_HOSTCFG, [1])
    return slow, spi


async def load(spi, writes):
    for a, words in host.coalesce(writes):
        await spi.qxfer(0x42, a, words=list(words))


async def phy(dut, pins):
    for rxd, crs in pins:
        await FallingEdge(dut.clk)
        dut.ext_val.value = rmii.rx_pads(rxd, crs)
    await FallingEdge(dut.clk)
    dut.ext_val.value = 0


async def reader(spi, nwords, out):
    while len(out) < nwords:
        out.extend(await spi.qxfer(0x4B, FIFO0, nread=min(256, nwords - len(out))))


@cocotb.test()
async def test_rmii_rx_rtl(dut):
    """A 1518-byte frame, then a 64-byte one of zeros at the minimum gap, read at
    SCK = clk with quad READ_FIXED bursts of 256 words and their real headers."""
    slow, spi = await setup(dut)
    await load(spi, host.load_program(0, rmii.firmware("rmii_rx.pasm"), profile=base.PROFILE))
    await slow.write(sim.A_SYNCBYP, [rmii.SYNC_BYPASS])
    await slow.write(sim.A_CTRL, [1 | (1 << 8)])
    frames = [rmii.make_frame(1518, 5), rmii.make_frame(64, 6, fill=0)]
    p = rmii.PhyRx()
    p.idle(100)
    p.frame(frames[0], toggle=2)
    p.idle(rmii.MIN_GAP)
    p.frame(frames[1])
    got = []
    cocotb.start_soon(phy(dut, p.pins))
    await reader(spi, (len(p.pins) + 200) // 8, got)
    recs, rest = rmii.parse_rx(got)
    assert [r.data for r in recs] == frames, [len(r.data) for r in recs]
    assert all(r.good and r.flags == 0 for r in recs) and rest == []
    assert (await spi.qxfer(0x43, sim.A_FSET, nread=1))[0] & rmii.OVF_FLAG_BIT == 0


async def rx_bursts(dut, n):
    slow, spi = await setup(dut)
    await load(spi, host.load_program(0, rmii.firmware("rmii_rx.pasm"), profile=base.PROFILE))
    await slow.write(sim.A_SYNCBYP, [rmii.SYNC_BYPASS])
    await slow.write(sim.A_CTRL, [1 | (1 << 8)])
    f = rmii.make_frame(1518, 5)
    p = rmii.PhyRx()
    p.idle(100)
    p.frame(f)
    task = cocotb.start_soon(phy(dut, p.pins))
    got = []
    while not task.done():
        got += await spi.qxfer(0x4B, FIFO0, nread=n)
    got += await spi.qxfer(0x4B, FIFO0, nread=16)
    recs, _ = rmii.parse_rx(got)
    flag = (await spi.qxfer(0x43, sim.A_FSET, nread=1))[0] & rmii.OVF_FLAG_BIT
    return recs, f, flag


@cocotb.test()
async def test_rmii_rx_seven_word_bursts_rtl(dut):
    """The port's real headers and CS gap: bursts of 7 sustain, as the model says."""
    recs, f, flag = await rx_bursts(dut, 7)
    assert len(recs) == 1 and recs[0].data == f and recs[0].good and not flag


@cocotb.test()
async def test_rmii_rx_six_word_bursts_rtl(dut):
    """Bursts of 6 do not, as the model says, and the record and flag show it."""
    recs, f, flag = await rx_bursts(dut, 6)
    assert flag and recs[0].flags & rmii.F_OVERFLOW and not recs[0].good


@cocotb.test()
async def test_rmii_rx_overflow_rtl(dut):
    """The host reads nothing during a frame: overflow flagged in the record and in
    F_OVF, and the next frame arrives whole."""
    slow, spi = await setup(dut)
    await load(spi, host.load_program(0, rmii.firmware("rmii_rx.pasm"), profile=base.PROFILE))
    await slow.write(sim.A_SYNCBYP, [rmii.SYNC_BYPASS])
    await slow.write(sim.A_CTRL, [1 | (1 << 8)])
    frames = [rmii.make_frame(128, 1), rmii.make_frame(64, 2)]
    p = rmii.PhyRx()
    p.idle(40)
    p.frame(frames[0])
    await phy(dut, p.pins)
    await ClockCycles(dut.clk, 20)
    assert (await spi.qxfer(0x43, sim.A_FSET, nread=1))[0] & rmii.OVF_FLAG_BIT
    got = []
    await reader(spi, 12, got)
    await slow.write(sim.A_FCLR, [rmii.OVF_FLAG_BIT])
    p = rmii.PhyRx()
    p.idle(40)
    p.frame(frames[1])
    task = cocotb.start_soon(phy(dut, p.pins))
    await reader(spi, 12 + len(p.pins) // 8 + 8, got)
    await task
    recs, _ = rmii.parse_rx(got)
    assert recs[0].flags & rmii.F_OVERFLOW and not recs[0].good
    assert recs[1].data == frames[1] and recs[1].good


@cocotb.test()
async def test_rmii_tx_rtl(dut):
    """A 1518-byte frame from WRITE_FIXED bursts, 9 words per 90 clocks after a
    longer first burst: sampled on every clock, no gap, FCS checked."""
    slow, spi = await setup(dut)
    await load(spi, host.load_program(1, rmii.firmware("rmii_tx.pasm"), profile=base.PROFILE)
               + host.pin_owner_writes({rmii.TX_PINS + i: 1 for i in range(3)}))
    await slow.write(sim.A_CTRL, [2 | (2 << 8)])
    f = rmii.make_frame(1518, 9)
    w = rmii.tx_words(rmii.tx_samples([f]))
    sink = rmii.PhyTx()

    async def sampler():
        while True:
            await RisingEdge(dut.clk)
            sink.sample(int(dut.uo_out.value) & 0x7F)
    task = cocotb.start_soon(sampler())
    ramp = 40
    await spi.qxfer(0x4A, FIFO1, words=w[:ramp])
    for i in range(ramp, len(w), 9):
        await spi.qxfer(0x4A, FIFO1, words=w[i:i + 9])
    await ClockCycles(dut.clk, 300)
    task.kill()
    fr = sink.frames()
    assert len(fr) == 1, len(fr)
    assert fr[0][2] and fr[0][1] == f and fr[0][3] == 4 * (8 + 1518)


@cocotb.test()
async def test_rmii_match_rtl(dut):
    """The cut-through pin: the same 10 clocks as the model, three frames."""
    slow, spi = await setup(dut)
    await load(spi, host.load_program(0, rmii.firmware("rmii_match.pasm"), profile=base.PROFILE))
    await slow.write(FIFO0, [1000])
    await slow.write(sim.A_CTRL, [1 | (1 << 8)])

    def frame(v):
        head = bytes.fromhex("ffffffffffff020000c0ffee") + (0x88B5).to_bytes(2, "big")
        return rmii.with_fcs(head + v.to_bytes(2, "little") + bytes(range(44)))
    p = rmii.PhyRx()
    p.idle(40)
    marks = []
    for v in (2000, 999, 3000):
        marks.append((len(p.pins) + 4 + 32 + 4 * 16 - 1, v >= 1000))
        p.frame(frame(v))
        p.idle(rmii.MIN_GAP)
    rises, prev = [], 0
    for i, (rxd, crs) in enumerate(p.pins):
        await FallingEdge(dut.clk)
        # the pin as it stands after the edge that took p.pins[i - 1], as in the model's trace
        v = (int(dut.uo_out.value) >> (rmii.HIT_PIN - 8)) & 1
        if v and not prev:
            rises.append(i - 1)
        prev = v
        dut.ext_val.value = rmii.rx_pads(rxd, crs)
    assert rises == [m + 10 for m, hit in marks if hit], (rises, marks)
    got = await spi.qxfer(0x4B, FIFO0, nread=2)
    assert got == [2000, 3000]
