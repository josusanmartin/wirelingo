"""ata_pio firmware on the cycle model against a spec-derived ATA device
(ata_device.py), which sees only pad levels. The subset is what fits: 8 data
pads, PIO mode 0, polled; docs/blind-test.md."""
import pathlib
import random
import sys

import pytest

from pe import asm, host, sim, timing
from pe.modelhost import ModelHost

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import ata_device as ata
from ata_host import *

FW = pathlib.Path(__file__).resolve().parents[2] / "firmware"
CLK_NS = 20
IORDY = 16
FIO = 7
DEFS = {"IORDY": IORDY, "ITO": 70, "FIO": FIO, **pio_defines(0)}
SIDE = 13

def load(**over):
    return asm.assemble((FW / "ata_pio.pasm").read_text(), {**DEFS, **over})


class Rig:
    def __init__(self, dev=None, clkdiv=1 << 8, clk_ns=CLK_NS, **over):
        self.mh = ModelHost()
        self.dev = dev or ata.AtaDevice()
        self.clk_ns = clk_ns
        self.clkdiv = clkdiv
        self.mh.apply(host.load_program(0, load(**over), out_pins=(0, 13), in_pins=0, side_pins=SIDE,
                                        clkdiv=clkdiv))
        self.mh.write(sim.A_POUT, 0x7F00)     # strobes and chip selects inactive
        self.tr = timing.Tracer(self.mh.chip, 0, 0)
        self.mh.start(1)
        self.rx = []
        self.strobe_while_rx_full = 0
        self.hold_rx = False
        self.pending = None

    def t(self):
        return self.mh.chip.cycle * self.clk_ns

    def use_mode(self, mode):
        """The device stays in the mode it was told; the host loads the build for it."""
        self.mh.write(sim.A_CTRL, 0)
        self.mh.apply(host.load_program(0, load(**pio_defines(mode)), out_pins=(0, 13), in_pins=0,
                                        side_pins=SIDE, clkdiv=self.clkdiv))
        self.mh.start(1)

    def step(self):
        m = self.mh
        t = self.t()
        uo, _, oe = m.chip.outputs()
        bus = m.bus_uio()
        self.dev.feed(t, uo & 7, (uo >> 3) & 1, (uo >> 4) & 1, (uo >> 5) & 1, (uo >> 6) & 1, bus & 0xFF, oe & 0xFF)
        # What the far end shows just before this cycle's closing edge, the one that
        # samples the pads. A change exactly on that edge goes to the next, as on the RTL.
        edge = t + self.clk_ns - 1
        v, doe = self.dev.drive(edge)
        m.uio, m.ext_oe = v, doe
        m.ui = (m.ui & ~1) | self.dev.iordy(edge)
        if self.pending is None:
            m.step()
        else:                           # a host write is a cycle too: the far end must see it
            m.chip.step(m.ui, m.bus_uio(), we=True, addr=sim.core_reg(0, sim.C_FIFO), wdata=self.pending)
            self.pending = None
        c = m.chip.cores[0]
        if not self.hold_rx:
            while c.rxf:                # not over the host bus: that would step the chip unseen
                self.rx.append(c.rxf.pop(0))

    def push(self, words, limit=4_000_000):
        words = list(words)
        for _ in range(limit):
            if not words:
                return
            if self.mh.tx_level(0) < 8:
                self.pending = words.pop(0)
            self.step()
        raise AssertionError("TX stuck")

    def expect(self, n, limit=4_000_000):
        for _ in range(limit):
            if len(self.rx) >= n:
                out, self.rx = self.rx[:n], self.rx[n:]
                return out
            self.step()
        raise AssertionError(f"have {len(self.rx)} replies, want {n}")

    def idle(self, ns):
        for _ in range(int(ns / self.clk_ns)):
            self.step()

    def status(self, units=20):
        self.push([wait(units)])
        return self.expect(1)[0]

    def reset(self):
        self.push(SRST)
        s = self.status(300)
        assert s & 0x1FF == 0x50, hex(s)

    def enable_8bit(self):
        self.push([wr(R_ERR, 0x01), wr(R_CMD, 0xEF)])
        s = self.status()
        return s

    def read_sectors(self, n, count=1):
        self.push(lba(n, count) + [wr(R_CMD, 0x20)])
        out = b""
        for _ in range(count):
            s = self.status()
            assert s & 0x1FF == 0x58, hex(s)
            self.push([bread(128)])
            got = self.expect(129)
            assert got[0] & 0x3FF == 0x58, hex(got[0])
            out += b"".join(w.to_bytes(4, "big") for w in got[1:])
        s = self.status()
        assert s & 0x1FF == 0x50, hex(s)
        return out

    def write_sectors(self, n, data):
        count = len(data) // 512
        self.push(lba(n, count) + [wr(R_CMD, 0x30)])
        for i in range(count):
            s = self.status()
            assert s & 0x1FF == 0x58, hex(s)
            self.push([bwrite(128)] + be_words(data[512 * i:512 * (i + 1)]))
            got = self.expect(1)
            assert got[0] & 0x3FF == 0x58, hex(got[0])
        s = self.status()
        assert s & 0x1FF == 0x50, hex(s)


def ready():
    r = Rig()
    r.reset()
    assert r.enable_8bit() & 0x1FF == 0x50
    return r


def test_register_round_trip():
    r = Rig()
    r.push([wr(R_SN, 0x5A), wr(R_CL, 0xA5), rd(R_SN), rd(R_CL)])
    assert r.expect(2) == [0x5A, 0xA5]
    assert not r.dev.errors, r.dev.errors


def test_software_reset_signature():
    r = Rig()
    r.push([wr(R_SN, 0x77)])
    r.reset()
    r.push([rd(R_ERR), rd(R_SC), rd(R_SN), rd(R_CL), rd(R_CH), rd(R_DH)])
    assert r.expect(6) == [1, 1, 1, 0, 0, 0]
    assert not r.dev.errors, r.dev.errors


def test_set_features_8bit_then_identify():
    r = ready()
    assert r.dev.bus8
    r.push([wr(R_DH, 0xE0), wr(R_CMD, 0xEC)])
    assert r.status() & 0x1FF == 0x58
    r.push([bread(128)])
    got = r.expect(129)
    data = b"".join(w.to_bytes(4, "big") for w in got[1:])
    assert len(data) == 512 and int.from_bytes(data[0:2], "little") == 0x848A
    model = bytes(data[54 + i + (1 if i % 2 == 0 else -1)] for i in range(40))
    assert model.rstrip() == r.dev.model.encode()
    assert int.from_bytes(data[120:124], "little") == len(r.dev.sectors)
    assert r.status() & 0x1FF == 0x50
    assert not r.dev.errors, r.dev.errors


def test_read_sector():
    r = ready()
    assert r.read_sectors(5) == r.dev.sectors[5]
    assert not r.dev.errors, r.dev.errors


def test_read_two_sectors_with_bsy_between():
    r = ready()
    assert r.read_sectors(7, 2) == r.dev.sectors[7] + r.dev.sectors[8]
    assert not r.dev.errors, r.dev.errors


def test_write_sector_then_read_it_back():
    r = ready()
    data = bytes(random.Random(5).getrandbits(8) for _ in range(1024))
    r.write_sectors(9, data)
    assert r.dev.written[9] == data[:512] and r.dev.written[10] == data[512:]
    assert r.read_sectors(9, 2) == data
    assert not r.dev.errors, r.dev.errors


def test_exact_cycle_figures():
    """From the far end's record of the pads, in ns, for each kind of cycle."""
    r = ready()
    r.dev.cycles.clear()
    r.read_sectors(1)
    cyc = r.dev.cycles
    low = {b - a for a, b, *_ in cyc}
    assert low == {320}
    data = [c for c in cyc if c[2] == "r" and c[3] == 0 and c[5] == 0]
    assert len(data) == 512
    falls = [c[0] for c in data]
    gaps = sorted({b - a for a, b in zip(falls, falls[1:]) if b - a < 1000})
    assert min(gaps) >= 600
    assert gaps == [620, 680], gaps     # inside a 4-byte word; across a PUSH
    assert not r.dev.errors, r.dev.errors


def test_iordy_extends_a_cycle_and_data_arrives_at_its_rise():
    d = ata.AtaDevice(iordy_low_ns=1000)
    r = Rig(d)
    r.reset()
    assert r.enable_8bit() & 0x1FF == 0x50
    d.cycles.clear()
    got = r.read_sectors(2)
    assert got == d.sectors[2]
    assert not d.errors, d.errors
    assert {b - a for a, b, *_ in d.cycles} == {1100}   # 1000, + pad 1, sync 2, wait, jmp, in less 2
    assert not r.mh.chip.flags & (1 << FIO)


def test_iordy_write_cycles_extend_too():
    d = ata.AtaDevice(iordy_low_ns=1200)
    r = Rig(d)
    r.reset()
    assert r.enable_8bit() & 0x1FF == 0x50
    data = bytes(range(256)) * 2
    r.write_sectors(4, data)
    assert d.written[4] == data and not d.errors, d.errors
    assert not r.mh.chip.flags & (1 << FIO)


def test_iordy_stuck_low_cannot_hang_the_core():
    d = ata.AtaDevice(iordy_stuck=True)
    r = Rig(d)
    r.push([rd(R_SN), rd(R_SN)])
    assert len(r.expect(2)) == 2
    assert r.mh.chip.flags & (1 << FIO)
    assert {b - a for a, b, *_ in d.cycles} == {1760}    # nop 13, ldi 1, wait 71, jmp, flag, jmp


def test_bsy_stuck_times_out_exactly():
    d = ata.AtaDevice(stuck_bsy=True)
    r = Rig(d)
    t0 = r.mh.chip.cycle
    r.push([wait(1)])                   # (1 + 1) * 1024 polls
    s = r.expect(1)[0]
    assert s == (TIMED_OUT | 0xD0)
    assert (r.mh.chip.cycle - t0) * CLK_NS == pytest.approx(2 * 1024 * 620, abs=1000)
    assert sum(c[2] == "r" for c in d.cycles) == 2048


def test_first_poll_is_past_the_400_ns_window():
    """The device shows the old status for the full 400 ns the spec allows: a
    poll inside the window would see BSY = 0, DRQ = 0, and report a finished
    command that has not started."""
    r = ready()
    r.dev.cycles.clear()
    r.push(lba(1) + [wr(R_CMD, 0x20), wait()])
    s = r.expect(1)[0]
    assert s & 0x1FF == 0x58, hex(s)
    cmd_rise = next(c[1] for c in r.dev.cycles if c[2] == "w" and c[5] == 7)
    first_poll = next(c[0] for c in r.dev.cycles if c[0] > cmd_rise and c[2] == "r")
    assert first_poll - cmd_rise == 540           # the shortest path: 27 ticks, > 400 ns
    r.push([bread(128)])
    r.expect(129)
    assert not r.dev.errors, r.dev.errors


def test_a_device_outside_the_window_fools_the_poll_negative():
    """The same test's teeth: a device that keeps the old status for 1000 ns,
    beyond the 400 ns it may, is read as finished before it has started."""
    d = ata.AtaDevice(status_window_ns=1000)
    r = Rig(d)
    r.reset()
    assert r.enable_8bit() & 0x1FF == 0x50
    r.push(lba(1) + [wr(R_CMD, 0x20), wait()])
    assert r.expect(1)[0] & 0x1FF == 0x50         # no DRQ seen: wrong, and the reason for the rule


def test_rx_full_mid_block_loses_nothing_and_no_strobe_runs():
    """The host stops reading for a while in the middle of a sector. The bus
    must go idle with no strobe active: nothing may be sampled after its strobe."""
    r = ready()
    r.push(lba(3) + [wr(R_CMD, 0x20)])
    assert r.status() & 0x1FF == 0x58
    r.hold_rx = True
    r.push([bread(128)])
    r.dev.cycles.clear()
    r.idle(100_000)
    uo, _, _ = r.mh.chip.outputs()
    assert (uo >> 5) & 3 == 3                     # both strobes idle while stalled
    n = sum(c[3] == 0 for c in r.dev.cycles)
    assert n % 4 == 3 or n % 4 == 0
    assert n <= 8 * 4 + 4
    t_stall = r.dev.cycles[-1][1]
    assert r.t() - t_stall > 70_000               # the last strobe was long ago: stalled, not slow
    r.hold_rx = False
    got = r.expect(129)
    assert b"".join(w.to_bytes(4, "big") for w in got[1:]) == r.dev.sectors[3]
    assert not r.dev.errors, r.dev.errors


def test_slow_tx_refill_mid_block_write():
    """The host dribbles words: the bus idles between them, none is lost."""
    r = ready()
    data = bytes(random.Random(6).getrandbits(8) for _ in range(512))
    r.push(lba(11) + [wr(R_CMD, 0x30)])
    assert r.status() & 0x1FF == 0x58
    r.push([bwrite(128)])
    for w in be_words(data):
        r.idle(random.Random(w).randrange(0, 3000))
        r.push([w])
    r.expect(1)
    assert r.status() & 0x1FF == 0x50
    assert r.dev.written[11] == data and not r.dev.errors, r.dev.errors


def test_error_status_refuses_the_block():
    """An out-of-range LBA ends in ERR, no DRQ: the block op reads no data."""
    r = ready()
    r.push(lba(1000) + [wr(R_CMD, 0x20)])
    s = r.status()
    assert s & 0x1FF == 0x51, hex(s)
    r.dev.cycles.clear()
    r.push([bread(128)])
    got = r.expect(1)
    assert got[0] == (REFUSED | 0x51)
    assert [c for c in r.dev.cycles if c[3] == 0] == []    # no Data cycle at all
    r.push([rd(R_ERR)])
    assert r.expect(1) == [0x10]


def test_refused_write_consumes_its_words():
    r = ready()
    r.push([wait()])
    r.expect(1)
    r.push([bwrite(4)] + [1, 2, 3, 4])
    got = r.expect(1)
    assert got[0] == (REFUSED | 0x50)
    r.push([rd(R_DH)])                    # the next op is still understood
    assert r.expect(1) == [0]
    assert not r.dev.errors, r.dev.errors


def test_device_without_cfa_rejects_8bit_mode():
    """The no-fit, shown: a 16-bit-only device aborts SET FEATURES 01h, so
    sectors cannot be moved; IDENTIFY gets the low byte of each word only."""
    d = ata.AtaDevice(cfa=False)
    r = Rig(d)
    r.reset()
    s = r.enable_8bit()
    assert s & 0x1FF == 0x51, hex(s)
    r.push([rd(R_ERR)])
    assert r.expect(1) == [ata.ABRT]
    assert not d.bus8
    r.push([wr(R_DH, 0xE0), wr(R_CMD, 0xEC)])
    assert r.status() & 0x1FF == 0x58
    r.push([bread(64)])                   # 256 word cycles, 4 per FIFO word
    got = r.expect(65)
    lows = b"".join(w.to_bytes(4, "big") for w in got[1:])
    full = bytes(d.buf)
    assert lows == full[0::2]             # half the data, and it is not recoverable
    assert d.highbyte_lost == 256
    assert not d.errors, d.errors


def test_writing_a_16bit_device_is_flagged_by_the_far_end():
    d = ata.AtaDevice(cfa=False)
    r = Rig(d)
    r.reset()
    r.push(lba(1) + [wr(R_CMD, 0x30)])
    assert r.status() & 0x1FF == 0x58
    r.push([bwrite(1)] + [0x01020304])
    r.expect(1)
    r.idle(5000)
    assert any("DD15:8" in e for e in d.errors)


def test_far_end_rejects_short_cycles_negative():
    """The device model must fail a too-fast host, or its silence means nothing."""
    d = ata.AtaDevice()
    d.feed(0, 7, 1, 1, 1, 1, 0, 0)
    d.feed(1000, 3, 0, 1, 1, 1, 0, 0)
    d.feed(1030, 3, 0, 1, 0, 1, 0, 0)     # t1 = 30
    d.feed(1130, 3, 0, 1, 1, 1, 0, 0)     # t2 = 100
    d.feed(1140, 2, 0, 1, 1, 1, 0, 0)     # t9 = 10
    d.feed(1400, 2, 0, 1, 0, 1, 0, 0)     # t0 = 370
    text = " ".join(d.errors)
    assert all(k in text for k in ("t1:", "t2:", "t9:", "t0:")), d.errors


# ------------------------------------------------------------- static timing

def check(**kw):
    return timing.check_source((FW / "ata_pio.pasm").read_text(), DEFS, **kw)


def test_annotations_leave_words_unchanged():
    import test_timing_check as TC
    text = (FW / "ata_pio.pasm").read_text()
    a, b = asm.assemble(text, DEFS), asm.assemble(TC.strip_annotations(text), DEFS)
    assert a.words == b.words


def test_static_deadlines_pass():
    chk, results = check()
    for r in results:
        print(r.report())
    assert not chk.errors
    bad = [(r.deadline.name, r.status) for r in results if not r.status.startswith("PASS")]
    assert results and not bad, bad


def test_static_bounds_are_sound_on_the_model():
    import test_timing_check as TC
    r = ready()
    r.read_sectors(1)
    r.write_sectors(2, bytes(512))
    r.push([rd(R_SN), wr(R_SN, 1)])
    r.expect(1)
    chk, results = check()
    TC.soundness(chk, results, r.tr, label="ata_pio")


# ------------------------------------------------------------- exact figures

ATA_SEEN = {"t0": 620, "t1": 100, "t2": 320, "t2i": 300, "t3": 360, "t4": 100, "t9": 160}


def test_smallest_figures_seen_at_the_pads():
    """Every cycle kind once. Margins against mode 0: t0 20, t1 30, t2 30 (8-bit),
    t3 300, t4 70, t9 140 ns."""
    r = ready()
    r.dev.seen.clear()
    data = bytes(range(256)) * 2
    r.write_sectors(12, data)
    assert r.read_sectors(12) == data
    r.push([rd(R_SN), wr(R_SN, 1), rd(R_SN)])
    r.expect(2)
    r.idle(2000)
    assert r.dev.seen == ATA_SEEN
    assert not r.dev.errors, r.dev.errors


@pytest.mark.parametrize("valid_ns,ok", [(270, True), (290, False)])
def test_read_sample_point_from_both_sides(valid_ns, ok):
    """DD is sampled 280 ns after DIOR- falls, as ata_read_sample_point finds on
    the RTL. Mode 0 gives 240 ns (t2 - t5)."""
    d = ata.AtaDevice(tm=ata.MODE0._replace(t5=290 - valid_ns))
    r = Rig(d)
    r.push([wr(R_SN, 0x5A), rd(R_SN)])
    assert r.expect(1)[0] == (0x5A if ok else ~0x5A & 0xFF)


class RandomIordy(ata.AtaDevice):
    """IORDY negated for a random time on each strobe, up to tB."""

    def __init__(self, seed, **kw):
        super().__init__(**kw)
        self.rng = random.Random(seed)

    def _fall(self, t, kind):
        self.iordy_low_ns = self.rng.choice((0, 0, 0, 100, 300, 700, 1250))
        super()._fall(t, kind)


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_random_stress(seed):
    """Random IORDY, execution times, sectors and host pauses, seeded."""
    rng = random.Random(seed)
    d = RandomIordy(seed, exec_ns=rng.randrange(1000, 40000), between_ns=rng.randrange(500, 20000))
    r = Rig(d)
    r.reset()
    assert r.enable_8bit() & 0x1FF == 0x50
    want = dict(d.sectors)
    for _ in range(6):
        n, count = rng.randrange(0, 60), rng.randrange(1, 4)
        if rng.random() < 0.5:
            data = bytes(rng.getrandbits(8) for _ in range(512 * count))
            r.write_sectors(n, data)
            for i in range(count):
                want[n + i] = data[512 * i:512 * (i + 1)]
        else:
            assert r.read_sectors(n, count) == b"".join(want[n + i] for i in range(count))
        r.idle(rng.randrange(0, 5000))
    assert not d.errors, d.errors[:5]
    assert not r.mh.chip.flags & (1 << FIO)


# ------------------------------------------------------------- the status path

def test_err_with_drq_refuses_the_block():
    """The check is BSY = 0, DRQ = 1, ERR = 0. Here DRQ is up and only ERR says no."""
    r = ready()
    r.dev.err_with_drq = True
    r.push(lba(1) + [wr(R_CMD, 0x20)])
    assert r.status() & 0x1FF == 0x59
    r.dev.cycles.clear()
    r.push([bread(128)])
    assert r.expect(1)[0] == (REFUSED | 0x59)
    assert [c for c in r.dev.cycles if c[3] == 0] == []
    assert not r.dev.errors, r.dev.errors


def test_idle_bus_is_deselected():
    """CS0- and CS1- high and DD released after every kind of op."""
    r = ready()
    for words, n in (([rd(R_SN)], 1), ([wr(R_SN, 1)], 0), ([wait()], 1), ([delay(1)], 0)):
        r.push(words)
        r.expect(n)
        r.idle(3000)
        uo, _, oe = r.mh.chip.outputs()
        assert (uo >> 3) & 3 == 3 and (uo >> 5) & 3 == 3 and oe == 0, (words, bin(uo), oe)


def test_polls_use_alternate_status():
    """A device clears a pending interrupt when Status is read, so neither WAIT
    nor the block check may read it: every status read is CS1-, DA = 6."""
    r = ready()
    r.dev.status_reads = 0
    r.dev.cycles.clear()
    r.read_sectors(2)
    assert r.dev.status_reads == 0
    alt = [c for c in r.dev.cycles if c[3] == 1 and c[4] == 0]
    assert alt and all(c[2] == "r" and c[5] == 6 for c in alt)


# ------------------------------------------------------------- faster modes

MODE_SEEN = {
    1: {"t0": 420, "t1": 100, "t2": 340, "t2i": 80, "t3": 380, "t4": 40, "t9": 100},
    2: {"t0": 420, "t1": 100, "t2": 340, "t2i": 80, "t3": 380, "t4": 40, "t9": 100},
    3: {"t0": 220, "t1": 100, "t2": 140, "t2i": 80, "t3": 180, "t4": 40, "t9": 100},
    4: {"t0": 220, "t1": 100, "t2": 140, "t2i": 80, "t3": 180, "t4": 40, "t9": 100},
}


def at_mode(mode, **kw):
    """Mode 0 until the device is told, then the build for that mode."""
    d = ata.AtaDevice(**kw)
    r = Rig(d)
    r.reset()
    assert r.enable_8bit() & 0x1FF == 0x50
    r.push(set_mode(mode))
    assert r.status() & 0x1FF == 0x50
    assert d.tm == ata.MODES[mode]
    r.use_mode(mode)
    d.seen.clear()
    d.cycles.clear()
    return r


@pytest.mark.parametrize("mode", [1, 2, 3, 4])
def test_faster_mode_read_write(mode):
    r = at_mode(mode)
    data = bytes(random.Random(mode).getrandbits(8) for _ in range(1024))
    r.write_sectors(20, data)
    assert r.read_sectors(20, 2) == data
    r.push([rd(R_SN), wr(R_SN, 1), rd(R_SN)])
    r.expect(2)
    r.idle(2000)
    assert not r.dev.errors, r.dev.errors[:4]
    assert r.dev.seen == MODE_SEEN[mode]


@pytest.mark.parametrize("mode", [1, 2, 3, 4])
def test_faster_mode_follows_iordy(mode):
    """The device takes all of tA to negate IORDY, the firmware must still see it."""
    r = at_mode(mode)
    r.dev.iordy_low_ns = 700
    data = bytes(range(256)) * 2
    r.write_sectors(30, data)
    assert r.read_sectors(30) == data
    assert not r.dev.errors, r.dev.errors[:4]
    assert not r.mh.chip.flags & (1 << FIO)
    assert min(b - a for a, b, *_ in r.dev.cycles) >= 700     # every strobe stretched


@pytest.mark.parametrize("mode", [1, 3])
def test_fast_build_on_a_mode0_device_is_caught_negative(mode):
    """The control: the far end does enforce each mode. Skip SET FEATURES 03h."""
    d = ata.AtaDevice()
    r = Rig(d)
    r.reset()
    assert r.enable_8bit() & 0x1FF == 0x50
    r.push(lba(1) + [wr(R_CMD, 0x20)])
    assert r.status() & 0x1FF == 0x58
    r.use_mode(mode)
    r.push([bread(4)])
    r.expect(1, limit=20000)
    r.idle(5000)
    assert any(k in e for e in d.errors for k in ("t0:", "t2:")), d.errors[:4]


def test_set_features_refuses_a_mode_the_device_lacks():
    d = ata.AtaDevice(max_mode=2)
    r = Rig(d)
    r.reset()
    r.push(set_mode(3))
    assert r.status() & 0x1FF == 0x51
    assert d.tm == ata.MODE0
