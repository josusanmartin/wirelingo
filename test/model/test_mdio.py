"""MDIO bench firmware (firmware/mdio_master.pasm, firmware/mdio_phy.pasm,
docs/bench-firmware.md) on the cycle model, 50 MHz, 1 clock = 20 ns.

Judges: pe.mdio's pins-only PHY and STA (their logic shares nothing with the
firmware), decode_frames on the recorded edges, a tri-state wire that counts
every instant two drivers fight, the static checker with a soundness run,
and sigrok's mdio decoder.

Wiring: the master on core 0 (MDC uo0, MDIO uio0), the PHY stand-in on core
1 (MDC ui0, MDIO uio1). Pin ownership keeps two cores off one pad, so the
two MDIO pads are tied together outside the chip, as they would be on a
board with two chips, with a pull-up."""
import pathlib
import random

import pytest

from pe import asm, host, mdio, sigrok, sim, timing
from pe.modelhost import ModelHost

import test_timing_check as TC

FW = pathlib.Path(__file__).resolve().parents[2] / "firmware"
CLK = 20.0
M_MDIO, M_MDC, P_MDIO, P_MDC = 0, 8, 1, 16
PHY = 5
ID1, ID2 = 0x0143, 0xBC30
REGS = {mdio.BMCR: 0x1140, mdio.BMSR: 0x7949, mdio.PHYID1: ID1, mdio.PHYID2: ID2, mdio.ANAR: 0x01E1, 5: 0xC1E1, 31: 0xABCD}


def master_prog(text=None, **kw):
    d = mdio.master_defines(M_MDIO, M_MDC, **kw)
    return asm.assemble(text if text is not None else (FW / "mdio_master.pasm").read_text(), d)


def phy_prog(regs=REGS, phyad=PHY, text=None):
    d = mdio.phy_defines(P_MDIO, P_MDC, phyad, 256, regs)
    return asm.assemble(text if text is not None else (FW / "mdio_phy.pasm").read_text(), d)


class Rig:
    """Any of: master firmware or `sta` (a pins-only STA); PHY firmware or
    `phy` (a pins-only PHY); the wire between them. Logs edges at the pads."""

    def __init__(self, master=None, phy_fw=None, phy=None, sta=None, regs=REGS, clkdiv=1 << 8, tracer_core=None):
        self.mh = mh = ModelHost()
        self.phy, self.sta = phy, sta
        self.has_master, self.has_phy = master is not None, phy_fw is not None
        if master is not None:
            mh.apply(mdio.master_load(0, master, M_MDIO, M_MDC, clkdiv))
        if phy_fw is not None:
            mh.apply(host.pin_owner_writes({P_MDIO: 1}))
            mh.apply(mdio.phy_setup(1, phy_fw, regs))
        self.wire = mdio.Wire()
        self.mdc_log, self.mdio_log, self.who_log = [(0.0, 0)], [(0.0, 1)], [(0.0, ())]
        self.d_model = None
        self.tracer = timing.Tracer(mh.chip, tracer_core, 256 * tracer_core) if tracer_core is not None else None
        orig = mh.chip.step

        def step(ui, uio, *a, **kw):
            self._observe()
            return orig(mh.ui, mh.bus_uio(), *a, **kw)
        mh.chip.step = step
        self.rx = {0: [], 1: []}
        mh.start((1 if self.has_master else 0) | (2 if self.has_phy else 0))

    def t(self):
        return self.mh.chip.cycle * CLK

    def _observe(self):
        mh = self.mh
        t = self.t()
        uo, out, oe = mh.chip.outputs()
        d_m = (out >> M_MDIO & 1) if oe >> M_MDIO & 1 else None
        d_p = (out >> P_MDIO & 1) if oe >> P_MDIO & 1 else None
        mdc = uo & 1
        if self.sta:
            level = min([x for x in (d_m, d_p, self.d_model) if x is not None], default=1)
            mdc, self.d_model = self.sta.step(t, level)
        if self.phy:
            level = min([x for x in (d_m, d_p, self.d_model) if x is not None], default=1)
            self.d_model = self.phy.step(t, mdc, level)
        level = self.wire.resolve(d_m, d_p, self.d_model)
        who = tuple(n for n, x in (("master", d_m), ("phyfw", d_p), ("model", self.d_model)) if x is not None)
        mh.ui = mdc
        mh.ext_oe, mh.uio = 3, level * 3
        if mdc != self.mdc_log[-1][1]:
            self.mdc_log.append((t, mdc))
        if level != self.mdio_log[-1][1]:
            self.mdio_log.append((t, level))
        if who != self.who_log[-1][1]:
            self.who_log.append((t, who))

    def run(self, cycles, until=None):
        mh = self.mh
        for _ in range(int(cycles)):
            mh.step()
            for c in (0, 1):
                if mh.chip.cores[c].rxf:
                    self.rx[c] += mh.pop_all(c)
            if until and until():
                return True
        return False

    def frames(self, *words):
        """Push master words one at a time; their records."""
        n0 = len(self.rx[0])
        for k, w in enumerate(words):
            self.mh.push(0, w)
            assert self.run(4000, lambda: len(self.rx[0]) > n0 + k)
        self.run(50)
        return [mdio.parse_record(w) for w in self.rx[0][n0:]]

    def sta_run(self):
        assert self.run((self.sta.t_end + 2000) / CLK, lambda: self.sta.done(self.t()))
        self.run(100)
        return self.sta.results

    def who(self, t):
        v = ()
        for tt, w in self.who_log:
            if tt > t:
                break
            v = w
        return v

    def decoded(self):
        return mdio.decode_frames(self.mdc_log, self.mdio_log)

    def rises(self):
        return [t for t, v in self.mdc_log if v]

    def trace(self):
        tr = sigrok.Trace()
        for t, v in self.mdc_log:
            tr.set(round(t * 1000), "mdc", v)
        for t, v in self.mdio_log:
            tr.set(round(t * 1000), "mdio", v)
        tr.end(round((self.t() + 2000) * 1000))
        return tr


R = mdio.frame_word(True, PHY, mdio.PHYID1)
W = mdio.frame_word(False, PHY, mdio.BMCR, 0x8000)


# ---------------------------------------------------------------- host words

def test_frame_words_exact():
    """The 32 bits after the preamble, bit for bit, from Clause 22's frame."""
    assert mdio.frame_word(True, 1, 2) == 0b01_10_00001_00010_00 << 16 == 0x60880000
    assert mdio.frame_word(False, 31, 31, 0xFFFF) == 0b01_01_11111_11111_10 << 16 | 0xFFFF == 0x5FFEFFFF
    assert mdio.frame_word(False, 0, 0, 0) == 0b01_01_00000_00000_10 << 16 == 0x50020000
    for bad in ((32, 0, 0), (0, 32, 0), (0, 0, 0x10000)):
        with pytest.raises(ValueError):
            mdio.frame_word(False, *bad)
    with pytest.raises(ValueError):
        mdio.frame_word(True, 0, 0, 1)
    for w in (0, 1 << 19, 3 << 17):
        with pytest.raises(ValueError):
            mdio.parse_record(w)
    with pytest.raises(ValueError):
        mdio.parse_write(0x7FFFFF)
    with pytest.raises(ValueError):
        mdio.master_defines(t=21)


# ---------------------------------------------------------------- master against a pins-only PHY

def test_master_read_and_write_exact_waveform():
    """A read and a write against the PHY model. Every frame: 32 preamble ones,
    ST 01, OP, PHYAD, REGAD, 16 data bits, 65 MDC rising edges with the idle
    clock; MDC high exactly 200 ns, low exactly 200 ns, period exactly 400 ns
    (802.3: >= 160, >= 160, >= 400) over all edges of a frame; no contention."""
    phy = mdio.PhyModel(PHY, dict(REGS), delay_ns=100.0)
    rig = Rig(master_prog(), phy=phy)
    rig.run(200)
    recs = rig.frames(R, W, mdio.frame_word(True, PHY, mdio.BMCR))
    assert recs == [{"read": True, "ack": True, "data": ID1}, {"read": False, "ack": None, "data": None},
                    {"read": True, "ack": True, "data": 0x8000}]
    assert phy.regs[mdio.BMCR] == 0x8000 and phy.writes == [(mdio.BMCR, 0b10, 0x8000)]
    f = rig.decoded()
    assert [(x["preamble"], x["st"], x["op"], x["phy"], x["reg"], x["ta"], x["data"]) for x in f] == [
        (32, 1, 2, PHY, 2, (1, 0), ID1), (33, 1, 1, PHY, 0, (1, 0), 0x8000), (33, 1, 2, PHY, 0, (1, 0), 0x8000)]
    rises = rig.rises()
    assert len(rises) == 3 * 65
    frame = [rises[i:i + 65] for i in range(0, len(rises), 65)]
    assert {b - a for fr in frame for a, b in zip(fr, fr[1:])} == {400.0}
    fall = [t for t, v in rig.mdc_log[1:] if not v]
    assert len(fall) == len(rises) and {f - r for r, f in zip(rises, fall)} == {200.0}
    assert {r - f for fr, fl in zip(frame, [fall[i:i + 65] for i in range(0, len(fall), 65)])
            for f, r in zip(fl, fr[1:])} == {200.0}
    assert rig.wire.contention == 0


def test_master_turnaround_who_drives():
    """The turnaround, on the driver log. Read: the master drives through A0
    and lets go on the falling edge after it, 200 ns before TA1's rising edge;
    nobody drives at TA1's rising edge; the PHY alone at TA2's; the master
    never drives again in the frame. Write: the master alone, TA 1 then 0."""
    phy = mdio.PhyModel(PHY, dict(REGS), delay_ns=100.0)
    rig = Rig(master_prog(), phy=phy)
    rig.run(200)
    rig.frames(R, W)
    rd, wr = rig.decoded()
    rise = rig.rises()
    k = rise.index(rd["rise"][0])                 # ST0's rising edge
    a0, ta1, ta2 = rise[k + 13], rise[k + 14], rise[k + 15]
    assert rig.who(a0 - 1) == ("master",) and rig.who(a0 + 1) == ("master",)
    released = next(t for t, w in rig.who_log if t > a0 and w == ())
    assert ta1 - released == 200.0
    assert rig.who(ta1 - 1) == () and rig.who(ta1) == ()
    assert rig.who(ta2 - 1) == ("model",)
    for t in rd["rise"][16:]:
        assert rig.who(t - 1) == ("model",)
    assert rig.who(rd["rise"][-1] + 500) == ()    # the model let go after D0
    j = rise.index(wr["rise"][0])
    for t in rise[j:j + 32]:
        assert rig.who(t - 1) == ("master",) == rig.who(t + 1)
    assert wr["ta"] == (1, 0)
    assert rig.who(rise[j + 31] + 200) == ()      # released on the falling edge after D0
    assert rig.wire.contention == 0


def test_master_preamble_and_setup_hold():
    """MDIO from the master changes only with MDC's falling edge: setup and
    hold around the rising edge are exactly 200 ns on every bit it drives
    (802.3: >= 10 ns); all 32 preamble bits are ones."""
    phy = mdio.PhyModel(PHY, dict(REGS))
    rig = Rig(master_prog(), phy=phy)
    rig.run(200)
    rig.frames(W, mdio.frame_word(False, PHY, 4, 0xAAAA), mdio.frame_word(False, PHY, 4, 0x5555))
    falls = {t for t, v in rig.mdc_log if not v}
    moved = [t for t, _ in rig.mdio_log[1:]]
    driven = [t for t in moved if rig.who(t - 1) == ("master",) or rig.who(t) == ("master",)]
    assert driven and set(driven) <= falls
    assert len(driven) > 60                       # 0xAAAA and 0x5555 toggle on every bit


@pytest.mark.parametrize("delay,ok", [(0.0, True), (20.0, True), (300.0, True), (320.0, True), (340.0, True),
                                      (360.0, False), (380.0, False)])
def test_master_phy_clock_to_output(delay, ok):
    """802.3 allows a PHY 0..300 ns from the rising edge. At 400 ns the master
    reads correct data for every delay on the 20 ns grid from 0 to 340 ns and
    wrong data from 360 ns: it samples 2 clocks (40 ns) before MDC rises at
    the pad, so 60 ns remain beyond the standard's 300 for the board."""
    phy = mdio.PhyModel(PHY, dict(REGS), delay_ns=delay)
    rig = Rig(master_prog(), phy=phy)
    rig.run(200)
    r = rig.frames(mdio.frame_word(True, PHY, 31), R)
    if ok:
        assert [x["data"] for x in r] == [0xABCD, ID1] and all(x["ack"] for x in r)
    else:
        assert [x["data"] for x in r] != [0xABCD, ID1]
    assert rig.wire.contention == 0


def test_master_no_phy():
    """Nobody at the address: the line stays high, TA2 reads 1, the record
    says so, the data is the pull-up's 0xFFFF; a write has no such check on
    the wire and completes."""
    rig = Rig(master_prog(), phy=mdio.PhyModel(PHY + 1, dict(REGS)))
    rig.run(200)
    r = rig.frames(R, W)
    assert r[0] == {"read": True, "ack": False, "data": 0xFFFF} and r[1]["read"] is False
    assert rig.decoded()[1]["ta"] == (1, 0)


@pytest.mark.parametrize("t,div,per", [(20, 1, 400.0), (24, 1, 480.0), (40, 1, 800.0), (20, 2, 800.0), (20, 25, 10000.0)])
def test_master_slower_clocks(t, div, per):
    """T and CLKDIV scale MDC: exact periods; the read still comes back."""
    phy = mdio.PhyModel(PHY, dict(REGS), delay_ns=300.0)
    rig = Rig(master_prog(t=t), phy=phy, clkdiv=div << 8)
    rig.run(200)
    n0 = 0
    rig.mh.push(0, R)
    assert rig.run(2000 * div * t, lambda: len(rig.rx[0]) > n0)
    assert mdio.parse_record(rig.rx[0][0])["data"] == ID1
    rises = rig.rises()
    assert {b - a for a, b in zip(rises, rises[1:])} == {per}
    assert len(rises) == 65


def test_master_mdc_on_a_uio_pin():
    """MDC may be a uio pin: the first instruction makes it an output, low."""
    mdc = 2
    prog = asm.assemble((FW / "mdio_master.pasm").read_text(), mdio.master_defines(M_MDIO, mdc))
    mh = ModelHost()
    mh.apply(mdio.master_load(0, prog, M_MDIO, mdc))
    mh.pull_up = 1
    mh.start(1)
    mh.step(5)
    assert mh.chip.outputs()[2] >> mdc & 1
    mh.push(0, R)
    prev, rises = 0, 0
    for _ in range(1400):
        v = mh.bus_uio() >> mdc & 1
        rises += v and not prev
        prev = v
        mh.step()
    assert rises == 65 and mdio.parse_record(mh.pop_all(0)[0]) == {"read": True, "ack": False, "data": 0xFFFF}


def test_master_short_preamble_define():
    """PRE is a define; 31 is non-compliant, and the 32-one PHY model refuses
    it (no answer, TA2 = 1): the model does hold the master to 32."""
    rig = Rig(master_prog(pre=31), phy=mdio.PhyModel(PHY, dict(REGS)))
    rig.run(200)
    assert rig.frames(R)[0] == {"read": True, "ack": False, "data": 0xFFFF}
    assert rig.decoded()[0]["preamble"] == 31


def test_master_stalled_host_only_delays_frames():
    """Records unread: after 8 the FIFO is full and the master holds MDC low
    between frames, losing none (9 sent; the 9th's record waits)."""
    phy = mdio.PhyModel(PHY, dict(REGS))
    rig = Rig(master_prog(), phy=phy)
    mh = rig.mh
    rig.run(200)
    for i in range(9):
        while mh.tx_level(0) >= 8:
            mh.step(100)
        mh.push(0, mdio.frame_word(False, PHY, 4, i))
    mh.step(1400 * 11)
    assert len(mh.chip.cores[0].rxf) == 8 and len(phy.writes) == 9
    assert [d for _, _, d in phy.writes] == list(range(9))


def test_broken_master_keeps_driving_through_turnaround():
    """Negative: without the release the wire judge sees the fight and the
    data is wrong."""
    text = (FW / "mdio_master.pasm").read_text()
    bad = text.replace("    pin MDIO, hiz           side 0 [H-1]  ; turnaround: ours ends here   @point f @point rel",
                       "    nop                     side 0 [H-1]")
    assert bad != text
    phy = mdio.PhyModel(PHY, dict(REGS))
    rig = Rig(master_prog(bad), phy=phy)
    rig.run(200)
    r = rig.frames(R)                             # REGAD 2 ends in 0: the master holds the line low
    assert rig.wire.contention > 0 and r[0]["data"] == 0


# ---------------------------------------------------------------- PHY stand-in against a pins-only STA

def sta_rig(ops, regs=REGS, phyad=PHY, **kw):
    sta = mdio.Sta(ops, **kw)
    return Rig(phy_fw=phy_prog(regs, phyad), sta=sta, regs=regs), sta


def test_phy_reads_every_register():
    """All 32 registers answer exactly: the three in core registers and the
    29 in the table. Contention 0; TA2 low every time."""
    regs = {r: (0x1000 * (r % 16) + 0x0101 * r) & 0xFFFF for r in range(32)}
    regs[mdio.BMCR] &= mdio.BMCR_READ_MASK
    rig, sta = sta_rig([("r", PHY, r) for r in range(32)], regs, setup_ns=200.0, hold_ns=200.0)
    res = rig.sta_run()
    assert [r["data"] for r in res] == [regs[r] for r in range(32)]
    assert all(r["ta2"] == 0 for r in res) and rig.wire.contention == 0


def test_phy_turnaround_who_drives():
    """Read: the stand-in drives nothing at TA1's rising edge, starts 80 ns
    after it, is alone on the wire at TA2's and through D0's, and lets go
    80 ns after D0's rising edge."""
    rig, sta = sta_rig([("r", PHY, mdio.PHYID1), ("r", PHY, mdio.PHYID2)], period_ns=400.0, setup_ns=200.0, hold_ns=200.0)
    res = rig.sta_run()
    assert [r["data"] for r in res] == [ID1, ID2]
    f0 = rig.decoded()[0]
    rise = rig.rises()
    k = rise.index(f0["rise"][0])
    ta1, ta2 = rise[k + 14], rise[k + 15]
    assert rig.who(ta1 - 1) == () and rig.who(ta1) == ()
    drv = next(t for t, w in rig.who_log if t > ta1 and w == ("phyfw",))
    assert drv - ta1 == 80.0                       # 4 clocks
    for t in rise[k + 15:k + 32]:
        assert rig.who(t - 1) == ("phyfw",)
    d0 = rise[k + 31]
    gone = next(t for t, w in rig.who_log if t > d0 and w == ())
    assert gone - d0 == 80.0
    assert rig.wire.contention == 0


def test_phy_clock_to_output_exact():
    """Every edge the stand-in drives is exactly 4 clocks (80 ns) after MDC's
    rising edge on the synchronous model (802.3: <= 300 ns)."""
    ops = [("r", PHY, 31, {}) for _ in range(1)] + [("r", PHY, mdio.PHYID2), ("r", PHY, 5)]
    rig, sta = sta_rig(ops, setup_ns=200.0, hold_ns=200.0)
    rig.sta_run()
    rises = set(rig.rises())
    moves = [t for t, _ in rig.mdio_log[1:] if rig.who(t) == ("phyfw",) or rig.who(t - CLK) == ("phyfw",)]
    assert len(moves) > 20
    assert {min(t - r for r in rises if t - r >= 0) for t in moves} == {80.0}


def test_phy_write_control_and_advertising():
    """BMCR and ANAR read back what was written, except that BMCR's reset and
    restart bits read 0; a write of all ones and of all zeros, exact; records
    of each write."""
    ops = [("w", PHY, 0, 0xFFFF), ("r", PHY, 0), ("w", PHY, 0, 0x0000), ("r", PHY, 0), ("w", PHY, 0, 0x9200), ("r", PHY, 0),
           ("w", PHY, 4, 0xFFFF), ("r", PHY, 4), ("w", PHY, 4, 0x0000), ("r", PHY, 4), ("w", PHY, 4, 0x0DE1), ("r", PHY, 4)]
    rig, sta = sta_rig(ops, setup_ns=200.0, hold_ns=200.0)
    res = rig.sta_run()
    assert [r["data"] for r in res if r] == [0x7DFF, 0, 0x1000, 0xFFFF, 0, 0x0DE1]
    assert [mdio.parse_write(w) for w in rig.rx[1]] == [
        {"reg": r, "ta": 2, "data": d} for r, d in ((0, 0xFFFF), (0, 0), (0, 0x9200), (4, 0xFFFF), (4, 0), (4, 0x0DE1))]
    assert rig.mh.read(sim.core_reg(1, sim.C_R0 + 2)) == 0x1000 and rig.mh.read(sim.core_reg(1, sim.C_CRC)) == 0x0DE1


def test_phy_read_only_writes_ignored_but_reported():
    """Writes to status, ID and any constant register change nothing and are
    reported to the host."""
    ops = [("w", PHY, 1, 0x0000), ("r", PHY, 1), ("w", PHY, 2, 0x1234), ("r", PHY, 2), ("w", PHY, 31, 0), ("r", PHY, 31)]
    rig, sta = sta_rig(ops, setup_ns=200.0, hold_ns=200.0)
    res = rig.sta_run()
    assert [r["data"] for r in res if r] == [REGS[1], REGS[2], REGS[31]]
    assert [mdio.parse_write(w)["reg"] for w in rig.rx[1]] == [1, 2, 31]


def test_phy_host_updates_status_live():
    """The host writes R3 between frames (a link change); the next read shows
    it."""
    rig, sta = sta_rig([("r", PHY, 1), ("r", PHY, 1, {})], setup_ns=200.0, hold_ns=200.0)
    assert rig.run(5000, lambda: len(rig.rises()) == 65)         # the first frame and its idle clock
    rig.mh.write(sim.core_reg(1, sim.C_R0 + 3), REGS[1] | 4)
    res = rig.sta_run()
    assert [r["data"] for r in res] == [REGS[1], REGS[1] | 4]


@pytest.mark.parametrize("pre,answers", [(31, False), (32, True), (33, True), (60, True)])
def test_phy_preamble_needs_32_ones(pre, answers):
    """802.3: a PHY observes 32 ones before it responds. 31: no answer and MDIO
    never driven; 32 and more: answered."""
    rig, sta = sta_rig([("r", PHY, 2, {"pre": pre})], setup_ns=200.0, hold_ns=200.0)
    res = rig.sta_run()
    assert res[0]["data"] == (ID1 if answers else 0xFFFF)
    assert (("phyfw",) in [w for _, w in rig.who_log]) == answers


def test_phy_zero_in_preamble_restarts_count():
    """Contiguous means contiguous: 20 ones, a 0, 20 ones (after a frame that
    ends low) is no preamble; a real one after it is."""
    ops = [("w", PHY + 1, 4, 0, {"idle": 0}), ("r", PHY, 2, {"prefix": [1] * 20 + [0], "pre": 20}), ("r", PHY, 2)]
    rig, sta = sta_rig(ops, setup_ns=200.0, hold_ns=200.0)
    res = rig.sta_run()
    assert [r["data"] for r in res[1:]] == [0xFFFF, ID1]


@pytest.mark.parametrize("kw", [{"st": 0b00}, {"op": 0b00}, {"op": 0b11}, {"st": 0b11}])
def test_phy_ignores_non_clause22(kw):
    """ST 00 (Clause 45), OP 00 or 11, ST 11: MDIO never driven; the next
    frame is answered."""
    rig, sta = sta_rig([("r", PHY, 2, kw), ("r", PHY, 3)], setup_ns=200.0, hold_ns=200.0)
    res = rig.sta_run()
    assert res[0]["data"] == 0xFFFF and res[1]["data"] == ID2


def test_phy_other_address_stays_off_the_wire():
    """Every other PHYAD: no drive, no write applied, no record; ours next."""
    ops = [("r", a, 2) for a in range(32) if a != PHY] + [("w", PHY + 1, 4, 0x1234), ("r", PHY, 4)]
    rig, sta = sta_rig(ops, setup_ns=200.0, hold_ns=200.0)
    res = rig.sta_run()
    assert all(r["data"] == 0xFFFF and r["ta2"] == 1 for r in res[:31])
    assert res[-1]["data"] == REGS[4] and rig.rx[1] == []


@pytest.mark.parametrize("ta", [0b00, 0b01, 0b11])
def test_phy_bad_turnaround_on_a_write(ta):
    """A write whose TA is not 1, 0 is not applied; the host's record shows
    the TA bits."""
    rig, sta = sta_rig([("w", PHY, 4, 0x1234, {"ta": ta}), ("r", PHY, 4)], setup_ns=200.0, hold_ns=200.0)
    res = rig.sta_run()
    assert res[1]["data"] == REGS[4]
    assert mdio.parse_write(rig.rx[1][0]) == {"reg": 4, "ta": ta, "data": 0x1234}


@pytest.mark.parametrize("hold,ok", [(0.0, False), (20.0, False), (40.0, True), (60.0, True), (200.0, True)])
def test_phy_sta_hold_limit(hold, ok):
    """The STA drives the inverse of each bit except from 10 ns before to
    `hold` after the rising edge. 40 ns is enough, 20 ns is not: the
    stand-in needs more than 802.3's 10 ns minimum at 50 MHz (a limit)."""
    rig, sta = sta_rig([("w", PHY, 4, 0xA55A), ("r", PHY, 4)], setup_ns=10.0, hold_ns=hold)
    res = rig.sta_run()
    assert (res[1]["data"] == 0xA55A) == ok


@pytest.mark.parametrize("setup,ok", [(-40.0, False), (-20.0, True), (0.0, True), (10.0, True)])
def test_phy_sta_setup_limit(setup, ok):
    """No setup time is needed: the bit may appear as late as 20 ns after MDC
    rises (with 60 ns of hold) on the synchronous model."""
    rig, sta = sta_rig([("w", PHY, 4, 0xA55A), ("r", PHY, 4)], setup_ns=setup, hold_ns=60.0)
    res = rig.sta_run()
    assert (res[1]["data"] == 0xA55A) == ok


@pytest.mark.parametrize("period,ok", [(400.0, True), (380.0, True), (360.0, True), (340.0, False), (320.0, False)])
def test_phy_fastest_mdc(period, ok):
    """A read needs MDC no faster than 360 ns (2.78 MHz, 18 clocks at 50 MHz:
    REGAD to the TA1 WAIT takes 17 ticks); at 340 ns it misses TA1 and the
    frame is lost. 802.3's 400 ns has 40 ns to spare."""
    rig, sta = sta_rig([("r", PHY, 31), ("r", PHY, 2)], period_ns=period, setup_ns=period / 2, hold_ns=period / 2)
    res = rig.sta_run()
    assert ([r["data"] for r in res] == [REGS[31], ID1]) == ok
    assert rig.wire.contention == 0


def test_phy_back_to_back_write_without_idle():
    """A write then 32 ones with no idle clock between: the stand-in is back
    hunting before the first preamble edge (worst path 14 ticks after D0)."""
    ops = [("w", PHY, 4, 0xFFFF, {"idle": 0}), ("r", PHY, 4)]
    rig, sta = sta_rig(ops, setup_ns=200.0, hold_ns=200.0)
    res = rig.sta_run()
    assert res[1]["data"] == 0xFFFF


def test_phy_record_fifo_full_drops_records_not_writes():
    """10 writes and a host that reads nothing: 8 records kept, the rest
    dropped, every write applied, reads still answered."""
    ops = [("w", PHY, 4, i) for i in range(1, 11)] + [("r", PHY, 4)]
    sta = mdio.Sta(ops, setup_ns=200.0, hold_ns=200.0)
    rig = Rig(phy_fw=phy_prog(), sta=sta)
    mh = rig.mh
    while not sta.done(rig.t()):
        mh.step(100)
    mh.step(200)
    assert sta.results[-1]["data"] == 10
    assert [mdio.parse_write(w)["data"] for w in mh.pop_all(1)] == list(range(1, 9))


# ---------------------------------------------------------------- both firmwares

def test_master_and_phy_random_traffic():
    """200 random frames between the two firmwares, a shadow register file
    predicting every answer; no contention over the whole run; every frame
    on the wire well formed. Seed fixed."""
    rng = random.Random(22)
    rig = Rig(master_prog(), phy_fw=phy_prog())
    rig.run(50)
    shadow = dict(REGS)
    for _ in range(200):
        reg = rng.choice([0, 1, 2, 3, 4, 5, 31, rng.randrange(32)])
        if rng.random() < 0.4:
            v = rng.getrandbits(16)
            assert rig.frames(mdio.frame_word(False, PHY, reg, v))[0]["read"] is False
            if reg == 0:
                shadow[0] = v & mdio.BMCR_READ_MASK
            elif reg == 4:
                shadow[4] = v
        else:
            r = rig.frames(mdio.frame_word(True, PHY, reg))[0]
            assert r == {"read": True, "ack": True, "data": shadow.get(reg, 0)}
    fs = rig.decoded()
    assert len(fs) == 200 and all(f["preamble"] >= 32 and f["ta"] == (1, 0) and f["st"] == 1 for f in fs)
    assert rig.wire.contention == 0


def test_master_and_phy_no_answer_at_other_address():
    rig = Rig(master_prog(), phy_fw=phy_prog())
    rig.run(50)
    r = rig.frames(mdio.frame_word(True, PHY + 1, 2), mdio.frame_word(True, PHY, 2))
    assert r == [{"read": True, "ack": False, "data": 0xFFFF}, {"read": True, "ack": True, "data": ID1}]


# ---------------------------------------------------------------- static checker

DEFS = {"mdio_master": mdio.master_defines(M_MDIO, M_MDC), "mdio_phy": mdio.phy_defines(P_MDIO, P_MDC, PHY, 256, REGS)}


def check(name, text=None, **kw):
    return timing.check_source(text if text is not None else (FW / f"{name}.pasm").read_text(), DEFS[name], **kw)


@pytest.mark.parametrize("name", sorted(DEFS))
def test_annotations_leave_the_words_unchanged(name):
    text = (FW / f"{name}.pasm").read_text()
    a, b = asm.assemble(text, DEFS[name]), asm.assemble(TC.strip_annotations(text), DEFS[name])
    assert a.words == b.words


def test_sizes():
    assert len(master_prog().words) == 32
    assert len(phy_prog().words) == 124


@pytest.mark.parametrize("name,want", [
    ("mdio_master", {"mdc_hi": (10, 10), "mdc_lo": (10, 10), "mdc_per": (20, 20), "su": (10, 10), "hd": (10, 10),
                     "turn": (30, 30), "ta1": (10, 10)}),
    ("mdio_phy", {"c2o": (1, 1), "wc2o": (1, 1), "dec": (10, 10), "sendup": (2, 2), "wrup": (8, 14)})])
def test_deadlines_pass_with_exact_bounds(name, want):
    chk, results = check(name)
    assert not chk.errors
    for r in results:
        print(r.report())
        assert r.status == ("PASS (conditional)" if r.deadline.reach == "conditional" else "PASS"), r.deadline.name
    assert {r.deadline.name: (r.lo, r.hi) for r in results} == want


def test_broken_variants_fail_the_checker():
    """One tick off, and the checker says so."""
    text = (FW / "mdio_master.pasm").read_text()
    bad = text.replace("    nop                     side 1 [H-1]  ; @point r\n    jmp r0--, pre           side 0 [H-1]",
                       "    nop                     side 1 [H-2]  ; @point r\n    jmp r0--, pre           side 0 [H-1]")
    assert bad != text
    _, res = check("mdio_master", bad)
    assert {r.deadline.name: r.status for r in res}["mdc_hi"].startswith("FAIL")
    text = (FW / "mdio_phy.pasm").read_text()
    bad = text.replace("    pin MDIO, lo                      ; TA2 is ours: 0   @point drv",
                       "    pin MDIO, lo              [20]    ; TA2 is ours: 0   @point drv")
    assert bad != text
    bad = bad.replace("    wait rise MDC                     ; @point w @point sb\n",
                      "    wait rise MDC                     ; @point w @point sb\n    nop\n")
    _, res = check("mdio_phy", bad)
    assert {r.deadline.name: r.status for r in res}["wc2o"].startswith("FAIL")


def test_phy_table_entries_are_uniform():
    """The checker does not follow the table jump, so its entries are checked
    here: 32 entries of 2 words, a value then `jmp send`, in register order."""
    text = (FW / "mdio_phy.pasm").read_text()
    src = [l.split(";")[0].strip() for l in text.splitlines()]
    a, b = src.index("table:"), src.index("send:")
    body = [l for l in src[a + 1:b] if l]
    assert len(body) == 64
    assert body[1::2] == ["jmp send"] * 32
    want = {0: "mov r1, r2", 1: "mov r1, r3", 4: "mov r1, crc"}
    assert body[0::2] == [want.get(r, f"ldi r1, V{r}") for r in range(32)]


def test_master_sound():
    rig = Rig(master_prog(), phy=mdio.PhyModel(PHY, dict(REGS)), tracer_core=0)
    rig.run(200)
    rng = random.Random(3)
    for _ in range(12):
        rig.frames(rng.choice([R, W, mdio.frame_word(False, PHY, 4, rng.getrandbits(16))]))
    chk, results = check("mdio_master")
    s = TC.soundness(chk, results, rig.tracer, label="mdio_master")
    TC.show(s, results)
    assert all(s[r.deadline.name][0] > 0 for r in results), s
    assert all(s[r.deadline.name][1] == 0 for r in results), s


def test_phy_sound():
    """The bounds against the model; and the whole REGAD-to-TA1 budget, which
    crosses the table jump, measured: 17 ticks on every table entry but
    register 0's (14)."""
    ops = [("r", PHY, r) for r in range(32)] + [("w", PHY, r, 0x5A5A) for r in (0, 1, 4)] + [("r", PHY + 1, 2)]
    sta = mdio.Sta(ops, setup_ns=200.0, hold_ns=200.0)
    rig = Rig(phy_fw=phy_prog(), sta=sta, tracer_core=1)
    rig.sta_run()
    chk, results = check("mdio_phy")
    s = TC.soundness(chk, results, rig.tracer, label="mdio_phy")
    TC.show(s, results)
    assert all(s[r.deadline.name][0] > 0 for r in results), s
    prog = phy_prog()
    hb, ta1 = prog.labels["hb"], prog.labels["send"] + 2         # the last header WAIT; the WAIT on TA1
    budget, last = [], None
    for pc, tick, cycle, done in rig.tracer.attempts:
        if pc == hb and done:
            last = tick
        elif pc == ta1 and last is not None:
            budget.append(tick - last)
            last = None
    assert budget == [14] + [17] * 31                            # register 0 takes the checker's short path


# ---------------------------------------------------------------- sigrok

@pytest.mark.skipif(sigrok.cli() is None, reason=sigrok.INSTALL_HINT)
def test_sigrok_mdio_master():
    """sigrok's mdio decoder, on master + PHY model: the same frames, no
    frame errors, no ERROR."""
    rig = Rig(master_prog(), phy=mdio.PhyModel(PHY, dict(REGS)))
    rig.run(200)
    rig.frames(R, W, mdio.frame_word(True, PHY, 31), mdio.frame_word(False, 31, 4, 0xFFFF), mdio.frame_word(False, PHY, 5, 0))
    frames, errors = sigrok.mdio(rig.trace(), "mdc", "mdio")
    print(frames, errors)
    assert [f[1:] for f in frames] == [("READ", PHY, 2, ID1, False), ("WRITE", PHY, 0, 0x8000, False),
                                       ("READ", PHY, 31, 0xABCD, False), ("WRITE", 31, 4, 0xFFFF, False),
                                       ("WRITE", PHY, 5, 0, False)]
    assert errors == []


@pytest.mark.skipif(sigrok.cli() is None, reason=sigrok.INSTALL_HINT)
def test_sigrok_mdio_master_and_phy():
    """sigrok on the two firmwares together."""
    rig = Rig(master_prog(), phy_fw=phy_prog())
    rig.run(50)
    rig.frames(R, mdio.frame_word(False, PHY, 4, 0x0DE1), mdio.frame_word(True, PHY, 4), mdio.frame_word(True, PHY, 0))
    frames, errors = sigrok.mdio(rig.trace(), "mdc", "mdio")
    print(frames, errors)
    assert [f[1:] for f in frames] == [("READ", PHY, 2, ID1, False), ("WRITE", PHY, 4, 0x0DE1, False),
                                       ("READ", PHY, 4, 0x0DE1, False), ("READ", PHY, 0, REGS[0], False)]
    assert errors == []


@pytest.mark.skipif(sigrok.cli() is None, reason=sigrok.INSTALL_HINT)
def test_sigrok_flags_broken_frames():
    """Negative: the decoder is not a rubber stamp. No PHY: 'TA invalid' and
    ERROR on the read."""
    rig = Rig(master_prog(), phy=mdio.PhyModel(PHY + 1, dict(REGS)))
    rig.run(200)
    rig.frames(R)
    frames, errors = sigrok.mdio(rig.trace(), "mdc", "mdio")
    print(frames, errors)
    assert [f[1:] for f in frames] == [("READ", PHY, 2, 0xFFFF, True)]
    assert any("TA" in e[1] for e in errors)
