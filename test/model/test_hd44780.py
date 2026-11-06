"""hd44780_4bit firmware on the cycle model against a datasheet far end
(hd44780_device.py), which sees only pad levels."""
import pathlib
import random
import sys

import pytest

from pe import asm, host, timing
from pe.modelhost import ModelHost

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hd44780_device as lcd

FW = pathlib.Path(__file__).resolve().parents[2] / "firmware"
CLK_NS = 100        # 10 MHz, CLKDIV 1: the same 100 ns tick as CLKDIV 5 at 50 MHz, faster to simulate
DB, RS, RW, E = 0, 8, 9, 10
DEFS = {"RS": RS, "RW": RW, "BFTO": 6000}
INIT, RW_BIT, RS_BIT = 0x400, 0x200, 0x100
BF_STUCK = 0x100
STUCK_OVERHEAD = 23  # clocks outside the polls: 19 instructions, 4 of FIFO and host stepping


def load(**over):
    return asm.assemble((FW / "hd44780_4bit.pasm").read_text(), {**DEFS, **over})


class Rig:
    def __init__(self, dev=None, clkdiv=1 << 8, clk_ns=CLK_NS, **over):
        self.mh = ModelHost()
        self.dev = dev or lcd.Hd44780()
        self.clk_ns = clk_ns
        prog = load(**over)
        self.prog = prog
        self.mh.apply(host.load_program(0, prog, out_pins=(DB, 4), in_pins=DB, side_pins=E, clkdiv=clkdiv))
        self.oe_rises = []
        self.tr = timing.Tracer(self.mh.chip, 0, 0)
        self.mh.start(1)
        self.last_oe = 0

    def step(self):
        m = self.mh
        t = m.chip.cycle * self.clk_ns
        uo, _, oe = m.chip.outputs()
        bus = m.bus_uio()
        chip_oe = oe & 0xF
        if chip_oe and not self.last_oe:
            self.oe_rises.append(t)
        self.last_oe = chip_oe
        self.dev.feed(t, (uo >> (RS - 8)) & 1, (uo >> (RW - 8)) & 1, (uo >> (E - 8)) & 1, (bus >> DB) & 0xF, chip_oe)
        v, doe = self.dev.drive(t)
        m.uio = (m.uio & 0xF0) | v
        m.ext_oe = (m.ext_oe & 0xF0) | doe
        m.pull_up = 0xF                 # pull-up MOS on DB (memory: see hd44780_device.py)
        m.step()

    def cmd(self, word, limit=12_000_000):
        self.mh.push(0, word)
        for _ in range(limit):
            self.step()
            if self.mh.chip.cores[0].rxf:
                return self.mh.pop_all(0)[0]
        raise AssertionError(f"no reply to {word:#x}")

    def init(self):
        assert self.cmd(INIT) == 0

    def wr_ir(self, b):
        return self.cmd(b)

    def wr_dr(self, b):
        return self.cmd(RS_BIT | b)

    def setup_display(self):
        self.init()
        for b in (0x28, 0x08, 0x01, 0x06, 0x0C):
            assert self.wr_ir(b) == 0


def test_init_and_write():
    r = Rig()
    r.setup_display()
    text = b"Wirelingo 4-bit"
    r.wr_ir(0x80 | 0x40)
    for c in text:
        assert r.wr_dr(c) == 0
    assert r.dev.line(0x40, len(text)) == text
    assert not r.dev.errors, r.dev.errors
    assert r.dev.lines2 and r.dev.display and not r.dev.dl8
    assert [(k, b) for _, k, b in r.dev.log[:8]][4:] == [("ir", 0x28), ("ir", 0x08), ("ir", 0x01), ("ir", 0x06)]


def test_init_exact_gaps():
    """Measured between the E falls of the four init writes, in ns."""
    r = Rig()
    r.init()
    f = r.dev.e_falls
    gaps = [b - a for a, b in zip(f, f[1:])]
    assert len(f) == 4
    assert f[0] > 40e6
    assert gaps[0] > 4.1e6 and gaps[1] > 100e3 and gaps[2] > 100e3
    assert not r.dev.errors, r.dev.errors


def test_exact_bus_figures_write():
    d = lcd.Hd44780(power_on_busy=False, t_power=None)
    d.dl8, d.fs_count = False, 3
    r = Rig(d, clkdiv=5 << 8, clk_ns=20)     # 50 MHz, as the header says
    r.wr_ir(0x28)
    # a BF poll (2 E pulses), then the 2 write pulses
    er, ef = r.dev.e_rises, r.dev.e_falls
    assert len(er) == 4
    assert [b - a for a, b in zip(er[:2], ef[:2])] == [800, 800]
    assert er[1] - er[0] == 1600
    assert [b - a for a, b in zip(er[2:], ef[2:])] == [600, 600]
    assert er[3] - er[2] == 1200


def test_busy_flag_is_polled_through_clear():
    """Clear takes 1.52 ms; the next write must land after BF falls, and the
    only way the firmware can know is the poll."""
    r = Rig()
    r.setup_display()
    r.wr_ir(0x01)
    t_clear = r.dev.log[-1][0]
    r.wr_dr(0x41)
    t_data = r.dev.log[-1][0]
    assert 1.52e6 < t_data - t_clear < 1.52e6 + 30_000
    assert not r.dev.errors, r.dev.errors


@pytest.mark.parametrize("scale", [1.0, 1.42, 3.0])
def test_slow_oscillator_is_followed(scale):
    """1.42: fOSC 190 kHz, nominal 270 less 30 %. Fixed delays would fail; BF does not."""
    r = Rig(lcd.Hd44780(scale=scale))
    r.setup_display()
    for c in b"abcd":
        assert r.wr_dr(c) == 0
    assert r.dev.line(0, 4) == b"abcd" and not r.dev.errors, r.dev.errors


def test_bf_ac_read_both_nibbles():
    r = Rig(lcd.Hd44780(power_on_busy=False, t_power=None))
    r.setup_display()
    r.wr_ir(0x80 | 0x25)
    v = r.cmd(RW_BIT)                 # the set-address instruction is still executing
    assert v == 0x80 | 0x25
    for _ in range(50_000 // CLK_NS):
        r.step()
    assert r.cmd(RW_BIT) == 0x25
    assert not r.dev.errors, r.dev.errors


def test_ac_is_read_after_tadd():
    """After a DR write BF falls first; AC moves tADD (5.6 us) later."""
    r = Rig(lcd.Hd44780(power_on_busy=False, t_power=None))
    r.setup_display()
    r.wr_ir(0x80 | 0x10)
    r.wr_dr(0x55)
    t0 = r.dev.busy_until
    while r.mh.chip.cycle * CLK_NS < t0 + 100:
        r.step()
    early = r.cmd(RW_BIT)
    assert not early & 0x80
    while r.mh.chip.cycle * CLK_NS < t0 + 7000:
        r.step()
    late = r.cmd(RW_BIT)
    assert early == 0x10 and late == 0x11


def test_read_ddram_back():
    r = Rig()
    r.setup_display()
    data = bytes(random.Random(4).getrandbits(8) for _ in range(16))
    r.wr_ir(0x80)
    for b in data:
        r.wr_dr(b)
    r.wr_ir(0x80)
    got = bytes(r.cmd(RW_BIT | RS_BIT) & 0xFF for _ in data)
    assert got == data and not r.dev.errors, r.dev.errors


def test_cgram_write_and_read():
    r = Rig()
    r.setup_display()
    r.wr_ir(0x40 | 8)
    glyph = bytes([0x1F, 0x11, 0x0A, 0x04, 0x0A, 0x11, 0x1F, 0])
    for b in glyph:
        r.wr_dr(b)
    assert bytes(r.dev.cgram[8:16]) == glyph and not r.dev.errors


@pytest.mark.parametrize("state", ["8bit", "4bit", "4bit-mid"])
def test_init_recovers_from_any_interface_state(state):
    """The point of the three 0011s: a warm restart of the host finds the LCD
    in 8-bit mode, in 4-bit mode, or half way through a byte."""
    d = lcd.Hd44780(power_on_busy=False, t_power=None)
    if state != "8bit":
        d.dl8 = False
        d.fs_count = 3
    if state == "4bit-mid":
        d.pending_hi = 0x0                 # the first 0011 ends as instruction 0x03: return home
    r = Rig(d)
    r.setup_display()
    r.wr_ir(0x80)
    r.wr_dr(0x5A)
    assert d.ddram[0] == 0x5A and not d.dl8 and d.pending_hi is None
    assert not d.errors, d.errors


def test_stuck_busy_flag_times_out_and_writes_nothing():
    r = Rig(lcd.Hd44780(stuck_busy=True, t_power=None), BFTO=200)
    assert r.cmd(INIT) == 0
    n = len(r.dev.log)
    t0 = r.mh.chip.cycle
    assert r.cmd(0x41 | RS_BIT) == BF_STUCK
    assert len(r.dev.log) == n
    assert (r.mh.chip.cycle - t0) == 201 * 38 + STUCK_OVERHEAD
    assert r.cmd(0x42 | RS_BIT) == BF_STUCK       # and the core still answers


def test_no_write_without_poll_negative():
    """The far end must catch an unpolled write: drive it by hand without firmware."""
    d = lcd.Hd44780(power_on_busy=False, t_power=None)
    d.fs_count = 3
    d.dl8 = False
    d.feed(0, 0, 0, 0, 0x2, 0xF)
    t = 1000
    for nib in (0x2, 0x8, 0x0, 0x1):      # 0x28 then 0x01 with no wait
        d.feed(t, 0, 0, 1, nib, 0xF)
        d.feed(t + 500, 0, 0, 0, nib, 0xF)
        t += 1200
    assert any("while busy" in e for e in d.errors)


@pytest.mark.parametrize("field,value,needle", [
    ("pweh", 100, "PWEH"), ("tas", 400, "tAS"),
])
def test_far_end_rejects_short_timing_negative(field, value, needle):
    d = lcd.Hd44780(power_on_busy=False, t_power=None)
    d.dl8, d.fs_count = False, 3
    d.feed(0, 0, 0, 0, 0, 0xF)
    d.feed(1000, 1, 0, 0, 0, 0xF)
    d.feed(1000 + (10 if field == "tas" else 200), 1, 0, 1, 3, 0xF)
    d.feed(1000 + 200 + value, 1, 0, 0, 3, 0xF)
    assert any(needle in e for e in d.errors), d.errors


def test_lcd_never_contended():
    """The chip's DB drivers come up at least 200 ns after E falls on a read."""
    r = Rig()
    r.setup_display()
    falls = sorted(r.dev.e_falls)
    for t in r.oe_rises:
        prev = [x for x in falls if x < t]
        if prev:
            assert t - prev[-1] >= 200
    assert not [e for e in r.dev.errors if "contention" in e]


# ------------------------------------------------------------- static timing

def check(**kw):
    return timing.check_source((FW / "hd44780_4bit.pasm").read_text(), DEFS, **kw)


def test_annotations_leave_words_unchanged():
    import test_timing_check as TC
    text = (FW / "hd44780_4bit.pasm").read_text()
    a, b = asm.assemble(text, DEFS), asm.assemble(TC.strip_annotations(text), DEFS)
    assert a.words == b.words


def test_static_deadlines_pass():
    chk, results = check()
    for r in results:
        print(r.report())
    assert not chk.errors
    assert len(results) == 8 and all(r.status == "PASS" for r in results), [(r.deadline.name, r.status) for r in results]


def test_static_bounds_are_sound_on_the_model():
    import test_timing_check as TC
    r = Rig()
    r.setup_display()
    for c in b"xy":
        r.wr_dr(c)
    r.cmd(RW_BIT)
    r.wr_ir(0x80)
    r.cmd(RW_BIT | RS_BIT)
    chk, results = check(clkdiv=(1, 0))
    s = TC.soundness(chk, results, r.tr, label="hd44780")
    assert all(s[x.deadline.name][0] > 0 for x in results), s


# ------------------------------------------------------------- exact figures

HD_SEEN = {"tAS": 300, "tAH": 300, "tH": 400, "PWEH": 600, "tcycE": 1200, "tDSW": 800}


def fifty(**kw):
    d = lcd.Hd44780(power_on_busy=False, t_power=None, **kw)
    d.dl8, d.fs_count = False, 3
    return Rig(d, clkdiv=5 << 8, clk_ns=20)


def test_smallest_figures_seen_at_the_pads():
    """Margins at the VCC 2.7-4.5 V table: tAS 240, tAH 280, tH 390, PWEH 150,
    tcycE 200, tDSW 605 ns."""
    r = fifty()
    r.wr_ir(0x80)
    r.wr_dr(0x41)
    r.wr_ir(0x80)
    assert r.cmd(RW_BIT | RS_BIT) & 0xFF == 0x41
    assert r.dev.seen == HD_SEEN
    assert not r.dev.errors, r.dev.errors


@pytest.mark.parametrize("tddr,ok", [(640, True), (680, False)])
def test_read_sample_point_from_both_sides(tddr, ok):
    """At 50 MHz DB is sampled 660 ns after E rises (700 less 2 clocks).
    tDDR is at most 360 ns."""
    r = fifty(timing=lcd.V3._replace(tddr=tddr))
    r.wr_ir(0x80 | 0x25)
    for _ in range(3000):
        r.step()
    got = r.cmd(RW_BIT)
    assert (got == 0x25) == ok, hex(got)


def test_every_byte_value_round_trips():
    """All 256 codes through CGRAM and DDRAM, so no nibble or bit is special.
    DDRAM holds 80 display positions; this model keeps 128 addressable bytes."""
    r = fifty()
    r.wr_ir(0x28)
    for start in range(0, 256, 64):
        r.wr_ir(0x80)
        vals = bytes(range(start, start + 64))
        for b in vals:
            assert r.wr_dr(b) == 0
        r.wr_ir(0x80)
        assert bytes(r.cmd(RW_BIT | RS_BIT) & 0xFF for _ in vals) == vals
    assert not r.dev.errors, r.dev.errors


def test_tadd_is_respected_at_any_poll_phase():
    """BF falls at some phase of the 3.8 us poll. Oscillator speeds across the
    datasheet's range move that phase; the access after BF = 0 must clear tADD
    at all of them. A fixed phase hid this: the mutant without the wait
    survived test_read_ddram_back."""
    for k in range(24):
        d = lcd.Hd44780(power_on_busy=False, t_power=None, scale=1 + 0.42 * k / 24)
        d.dl8, d.fs_count = False, 3
        d.ddram[:8] = bytes(range(0x41, 0x49))
        r = Rig(d)
        r.wr_ir(0x80)
        assert bytes(r.cmd(RW_BIT | RS_BIT) & 0xFF for _ in range(8)) == bytes(range(0x41, 0x49))
        assert not d.errors, (k, d.errors)
