"""firmware/pps_capture.pasm and pe.pps on the cycle model (docs/pps-capture.md).

Every expected number is computed from the clock at which the test drives the
pad, never from what the firmware reported: a pad value at clock edge c is read
by a WAIT at c + 2 (isa §2), the CTRL write at w starts the core at w + 2 (§5.3)
and the first WAIT attempt is START ticks later, so an edge driven at c must be
stamped c + 2 - (w + 2 + START), unless the firmware says it may be late.

One second is 50 million model steps, so Bench skips the ticks of a WAIT while
nothing can happen; test_fast_forward_equals_stepping pins that to plain
stepping, and test/test_pps.py runs without it on the RTL."""
from fractions import Fraction
import random

import pytest

from pe import asm, host, pps, sim, timing
from pe.modelhost import ModelHost

PIN = 16                 # ui0
PROG = pps.assemble()
FIFO = sim.core_reg(0, sim.C_FIFO)
SEC = 50_000_000         # clocks per second at 50 MHz


class Bench:
    """The firmware on the model, an input driven at given clocks."""

    def __init__(self, start=pps.WRAP - 1, prog=PROG, fast=True, whole=True):
        self.mh = ModelHost()
        self.mh.apply(pps.load_writes(prog, pin=PIN, start=start, run=False))
        self.ctrl = self.mh.chip.cycle                 # the CTRL write's cycle
        self.mh.apply(host.start_writes(1))
        self.zero = self.ctrl + 2 + pps.START          # tick 0
        self.dec = pps.Decoder(start)
        self.fast, self.whole = fast, whole
        self.edges = []
        self.level = 0

    @property
    def cycle(self):
        return self.mh.chip.cycle

    def advance(self, target):
        mh, chip = self.mh, self.mh.chip
        core = chip.cores[0]
        while chip.cycle < target:
            quiet = (self.fast and core.in_wait and not (chip.pins_in() >> PIN ^ core.r[0]) & 1
                     and chip.sync1 == chip.sync2 == chip.pads_prev == mh.ui << 8)
            k = min(core.r[3] - 1, (target - chip.cycle - 8) // 65536)     # whole rollover periods
            n = min(target - chip.cycle, core.r[2] - 2) - 1                # ticks of this WAIT
            if quiet and self.whole and k > 0:           # the state repeats, but for r3
                core.r[3] -= k
                core.t = 1
                chip.cycle += k * 65536
            elif quiet and n > 8:
                core.r[2] -= n
                chip.cycle += n
            else:
                mh.step()

    def drive(self, level, cycle):
        """The pad takes `level` at the clock edge of `cycle`."""
        assert cycle >= self.cycle, (cycle, self.cycle)
        self.advance(cycle)
        self.mh.ui = level
        self.level = level

    def want(self, cycle):
        """The tick an edge driven at `cycle` must get."""
        return cycle + pps.PAD_TO_WAIT - self.zero

    def drain(self):
        self.edges += self.dec.feed(self.mh.pop_all(0))
        return self.edges

    def settle(self, n=40):
        self.advance(self.cycle + n)
        return self.drain()


def state(mh):
    c = mh.chip.cores[0]
    return (mh.chip.cycle, c.pc, tuple(c.r), c.isr, c.isr_cnt, c.osr, c.osr_cnt, c.t, c.stalled, c.in_wait,
            tuple(c.rxf), mh.chip.sync1, mh.chip.sync2)


def test_fast_forward_equals_stepping():
    """Everything below that spans more than a few thousand clocks uses
    Bench's skip, so the skip itself has to be invisible: the same input, over
    two rollovers, a carry and edges in and out of the skips, ends in the same
    state, register for register, with and without it."""
    rng = random.Random(4)
    t = [rng.randrange(100, 30000) for _ in range(6)]
    runs = []
    for fast in (False, True):
        b = Bench(start=(5 << 32) | 0x0000_3000, fast=fast)
        c = b.cycle + 10
        for i, dt in enumerate(t):
            c += dt
            b.drive((i + 1) & 1, c)
            b.advance(c + 60)
            b.drain()
        b.advance(c + 80000)
        runs.append((state(b.mh), b.drain()))
    assert runs[0] == runs[1]
    assert len(runs[0][1]) == 6
    assert runs[0][0][5] >> 16 == 0 and runs[0][0][5] & 0xFFFF == 4   # the carry was in the run


def test_static_bounds_are_the_constants_the_decoder_uses():
    """pe.timing's bounds from the assembled words: the paths the firmware
    header states and pe.pps relies on."""
    chk, res = timing.check_source(pps.FW.read_text(), {})
    got = {r.deadline.name: (r.lo, r.hi) for r in res}
    assert got == {"record": (pps.RECORD, pps.CARRY), "rollover": (pps.ROLL, pps.ROLL_CARRY)}
    assert all(r.status.startswith("PASS") for r in res), [r.status for r in res]


def test_program_size():
    assert len(PROG.words) == 34


def test_stamps_equal_the_wire_clock_for_clock():
    """Random edges at least a record path apart: every stamp is exact, in
    absolute ticks, and none claims to be late."""
    rng = random.Random(1)
    b = Bench()
    c, want = b.cycle + 20, []
    for i in range(120):
        c = max(b.cycle, c + rng.choice([pps.CARRY + 1, 21, 22, 33, 64, 257, 1000, rng.randrange(20, 4000)]))
        b.drive((i + 1) & 1, c)
        want.append((b.want(c), (i + 1) & 1))
        if i % 3 == 2:
            b.advance(c + pps.CARRY + 1)
            b.drain()
    b.settle()
    assert [(e.tick, e.level) for e in b.edges] == want
    assert [e.late for e in b.edges] == [0] * 120 and b.dec.dropped == 0


def test_the_level_at_the_start_is_not_an_edge():
    mh = ModelHost()
    mh.ui = 1
    mh.apply(pps.load_writes(PROG, pin=PIN))
    mh.step(60)
    assert mh.pop_all(0) == []
    mh.ui = 0
    mh.step(60)
    [e] = pps.Decoder().feed(mh.pop_all(0))
    assert e.level == 0


def test_an_edge_at_the_first_attempt_says_it_may_be_late():
    """The start sample is 3 ticks before the first attempt; the level changed
    between them, so the stamp is 0 and the edge up to 2 ticks older."""
    mh = ModelHost()
    mh.apply(pps.load_writes(PROG, pin=PIN, run=False))
    w = mh.chip.cycle
    mh.apply(host.start_writes(1))
    zero = w + 2 + pps.START
    for c in (zero - 4, zero - 3, zero - 2):          # read at c + 2: -2, -1, 0
        m = ModelHost()
        m.apply(pps.load_writes(PROG, pin=PIN, run=False))
        m.apply(host.start_writes(1))
        while m.chip.cycle < c:
            m.step()
        m.ui = 1
        m.step(40)
        [e] = pps.Decoder().feed(m.pop_all(0))
        assert (e.tick, e.late) == (0, 2)
    for c, tick in ((zero - 5, None), (zero - 1, 1)):
        m = ModelHost()
        m.apply(pps.load_writes(PROG, pin=PIN, run=False))
        m.apply(host.start_writes(1))
        while m.chip.cycle < c:
            m.step()
        m.ui = 1
        m.step(40)
        got = pps.Decoder().feed(m.pop_all(0))
        if tick is None:                              # sampled by `mov r0, pins`: no edge at all
            assert got == []
        else:
            assert [(e.tick, e.late) for e in got] == [(tick, 0)]


@pytest.mark.parametrize("name,start,gap", [("rollover", (7 << 32) | (9 << 16) | 200, pps.ROLL),
                                            ("carry", (7 << 32) | 200, pps.ROLL_CARRY)])
def test_every_phase_through_a_rollover(name, start, gap):
    """An edge at every tick from before r2 runs out (W) to after the next
    attempt: exact on the attempts, on the blind ticks stamped at the next
    attempt with `late` covering it. The next edge, far away, is exact: no
    path lost time."""
    late_seen = []
    # the counter reaches r2 = 0 at tick 200: W
    w = 200
    for off in range(-3, gap + 3):
        b = Bench(start=start)
        c = b.zero + w + off - pps.PAD_TO_WAIT
        b.drive(1, c)
        b.drive(0, c + 400)
        b.drive(1, c + 900)
        b.settle()
        rise, fall, rise2 = b.edges
        true = w + off
        blind = 0 < off < gap
        assert rise.tick == (w + gap if blind else true), off
        assert rise.late == (gap - 1 if off > 0 and blind or off == gap else 0), off
        assert rise.tick - rise.late <= true <= rise.tick
        assert fall.tick == true + 400 and fall.late == 0
        assert rise2.tick == true + 900 and rise2.late == 0
        late_seen.append(rise.tick - true)
    assert max(late_seen) == gap - 1                 # the bound is reached


def test_rollover_with_no_edge_keeps_time():
    """Rollovers alone, 3 of them and a carry, with an edge after each span."""
    start = (2 << 32) | 0x0002_0100                  # 2 rollovers then r3 = 0
    b = Bench(start=start)
    c = b.zero + 3 * 65536 + 5000
    b.drive(1, c)
    b.settle()
    assert [(e.tick, e.late) for e in b.edges] == [(b.want(c), 0)]
    assert b.mh.chip.cores[0].r[3] != 0


@pytest.mark.parametrize("name,start,path", [("plain", pps.WRAP - 1, pps.RECORD),
                                             ("borrow", (1 << 32) | (3 << 16) | (500 + 5), pps.BORROW),
                                             ("carry", (1 << 32) | (500 + 5), pps.CARRY)])
def test_every_width_through_the_record_path(name, start, path):
    """A pulse of each width, 1 tick up to past the record path (with r2 about
    to pass through zero in the `borrow` and `carry` runs, so the path is the
    long one): a fall inside the path is stamped at its end, `late` covers it,
    a wider pulse is exact, and the next rise shows no time lost."""
    for width in range(1, path + 3):
        b = Bench(start=start)
        c = b.zero + 500 - pps.PAD_TO_WAIT           # rise at tick 500: r2 = 5 in the last two runs
        b.drive(1, c)
        b.drive(0, c + width)
        b.drive(1, c + 2000)
        b.settle()
        rise, fall, rise2 = b.edges
        assert rise.tick == 500 and rise.late == 0
        assert fall.tick == 500 + max(width, path), width
        assert fall.late == (path - 1 if width <= path else 0), width
        assert fall.tick - fall.late <= 500 + width <= fall.tick
        assert rise2.tick == 2500 and rise2.late == 0
        [p, _] = pps.pulses(b.edges)
        assert p.width - p.fall_late <= width <= p.width + p.late


@pytest.mark.parametrize("r2,path", [(14, pps.RECORD), (13, pps.RECORD), (12, pps.BORROW), (0, pps.BORROW)])
def test_the_borrow_boundary(r2, path):
    """r2 at the edge decides the path: `sub r2, 13` borrows below 13 and not
    at 13. A 1-tick pulse shows the path's length, and `late` must match it."""
    b = Bench(start=(1 << 32) | (3 << 16) | (500 + r2))
    c = b.zero + 500 - pps.PAD_TO_WAIT
    b.drive(1, c)
    b.drive(0, c + 1)
    b.drive(1, c + 2000)
    b.settle()
    rise, fall, rise2 = b.edges
    assert rise.counter & 0xFFFF == r2
    assert pps.record_path(rise.counter) == path
    assert (fall.tick - rise.tick, fall.late) == (path, path - 1)
    assert rise2.tick == 2500 and rise2.late == 0


def test_a_dip_inside_the_record_path_is_not_seen():
    """The limit stated in the firmware header: the level is looked at again
    only at the next attempt, so a change that reverts before then is gone."""
    for back, seen in ((pps.RECORD, [1]), (pps.RECORD + 1, [1, 0, 1])):
        b = Bench()
        c = b.cycle + 20                             # W = c + 2, next attempt c + 15, reading the pad of c + 13
        b.drive(1, c)
        b.drive(0, c + 3)
        b.drive(1, c + back)
        b.settle()
        assert [e.level for e in b.edges] == seen, back


def test_interval_across_every_counter_digit_at_one_hertz():
    """Real 1 Hz at 50 MHz through 16-, 32- and 48-bit rollovers: a train
    started so that r3 runs out and then the whole counter wraps inside it."""
    start = 2 * SEC + 12345                          # wraps 2 s in
    b = Bench(start=start)
    rises = [b.cycle + 1000 + k * SEC for k in range(5)]
    for r in rises:
        b.drive(1, r)
        b.drive(0, r + SEC // 10)
        b.settle()
    assert [e.tick for e in b.edges if e.level] == [b.want(r) for r in rises]
    assert [e.level for e in b.edges] == [1, 0] * 5
    assert b.edges[-1].counter > b.edges[0].counter            # the 48-bit counter went through 0
    rep = pps.analyse(b.edges, SEC, min_width=50)
    assert rep.intervals == [SEC] * 4 and rep.drift_ppm == 0 and rep.tie_pp == 0
    # the decoder's own extension, with a start that makes its ticks wrap inside the train
    d = pps.Decoder(start=start - 2 * SEC)
    edges = d.feed(sum(([(1 << 17) | (e.level << 16) | (e.counter >> 32), e.counter & 0xFFFFFFFF]
                        for e in b.edges), []))
    assert d.base == pps.WRAP
    assert [e.tick - edges[0].tick for e in edges] == [e.tick - b.edges[0].tick for e in b.edges]


def test_interval_longer_than_the_32_bit_counter():
    """Two pulses 100 s apart (5e9 ticks > 2**32): no record in between, and
    the interval comes out to the clock."""
    b = Bench()
    r0 = b.cycle + 1000
    b.drive(1, r0)
    b.drive(0, r0 + 500)
    b.settle()
    r1 = r0 + 100 * SEC
    b.drive(1, r1)
    b.drive(0, r1 + 500)
    b.settle()
    assert 100 * SEC > 1 << 32
    assert [e.tick for e in b.edges if e.level] == [b.want(r0), b.want(r1)]
    rep = pps.analyse(b.edges, 100 * SEC)
    assert rep.intervals == [100 * SEC]


def test_an_interval_of_2_48_ticks_aliases():
    """The stated limit, at the decoder: the host needs a record in every 2**48
    ticks (65.2 days at 50 MHz), because one wrap between records can't be seen."""
    d = pps.Decoder()
    rec = lambda t, lv: [((pps.WRAP - 1 - t) >> 32) | (1 << 17) | (lv << 16), (pps.WRAP - 1 - t) & 0xFFFFFFFF]
    a = d.feed(rec(1000, 1))
    b = d.feed(rec((1000 + pps.WRAP - 7) % pps.WRAP, 0))   # 7 short of a wrap: seen
    c = d.feed(rec((1000 + 2 * pps.WRAP - 7 + 20) % pps.WRAP, 1))
    assert b[0].tick - a[0].tick == pps.WRAP - 7
    assert c[0].tick - b[0].tick == 20                     # a whole wrap between records is lost
    assert round(pps.WRAP * 20e-9 / 86400, 1) == 65.2      # days, at 20 ns a tick
    d2 = pps.Decoder()
    d2.feed(rec(1000, 1))
    assert d2.feed(rec(990, 0))[0].tick == pps.WRAP + 990  # any backward step counts as one wrap


# Pulse trains with known offset, jitter and drift ------------------------------

def train(rises, width=SEC // 10, start=pps.WRAP - 1):
    b = Bench(start=start)
    for r in rises:
        b.drive(1, b.cycle * 0 + r)
        b.drive(0, r + width)
        b.settle()
    return b


def lsq(pts):
    m = len(pts)
    xb, yb = Fraction(sum(x for x, _ in pts), m), Fraction(sum(y for _, y in pts), m)
    return sum((x - xb) * (y - yb) for x, y in pts) / sum((x - xb) ** 2 for x, _ in pts)


@pytest.mark.parametrize("drift", [0, 37, -37, 500, 2500, -2500])
def test_drift_of_a_one_hertz_train(drift):
    """A train whose period is SEC + drift clocks: each interval exact, the
    drift exactly drift / SEC, whatever the clock offset of the first pulse."""
    r0 = 12345 + abs(drift)
    rises = [r0 + k * (SEC + drift) for k in range(6)]
    b = train(rises)
    want = [b.want(r) for r in rises]
    rep = pps.analyse(b.edges, SEC, min_width=50)
    assert [t for _, t in rep.rises] == want
    assert rep.rises[0][1] == r0 + 2 - b.zero                  # the known offset
    assert rep.intervals == [SEC + drift] * 5
    assert rep.period == SEC + drift
    assert rep.drift_ppm == Fraction(drift * 1_000_000, SEC)
    assert rep.tie_pp == 0 and rep.tie_rms == 0
    assert not (rep.missed or rep.runts or rep.offgrid or rep.dropped)


def test_jitter_and_drift_together():
    """Known jitter on known drift: the same fit as an independent one over
    the true pulse times, residuals to the tick."""
    rng = random.Random(7)
    drift = 123
    jit = [rng.randrange(-5000, 5001) for _ in range(8)]
    rises = [10000 + k * (SEC + drift) + j for k, j in enumerate(jit)]
    b = train(rises)
    rep = pps.analyse(b.edges, SEC, min_width=50)
    true = [(k, b.want(r)) for k, r in enumerate(rises)]
    assert rep.rises == true
    assert rep.intervals == [b - a for a, b in zip(rises, rises[1:])]
    slope = lsq(true)
    assert rep.period == slope
    assert rep.drift_ppm == (slope / SEC - 1) * 1_000_000
    xb = Fraction(sum(x for x, _ in true), 8)
    yb = Fraction(sum(y for _, y in true), 8)
    res = [y - yb - slope * (x - xb) for x, y in true]
    assert rep.tie_pp == max(res) - min(res)
    assert rep.tie_pp > 0


def test_a_missed_pulse_is_counted_and_the_grid_holds():
    """Pulse 3 and pulses 6-7 never come: gaps of 2 and 3 periods, the numbers
    of the missing pulses, and a fit that is still exact."""
    drift = 40
    numbers = [0, 1, 2, 4, 5, 8, 9]
    rises = [5000 + k * (SEC + drift) for k in numbers]
    rep = pps.analyse(train(rises).edges, SEC, min_width=50)
    assert rep.missed == [3, 6, 7]
    assert [n for n, _ in rep.rises] == numbers
    assert rep.period == SEC + drift and rep.tie_pp == 0
    assert rep.intervals == [(b - a) * (SEC + drift) for a, b in zip(numbers, numbers[1:])]


@pytest.mark.parametrize("width", [1, 2, 13, 14, 100])
def test_a_runt_between_pulses_is_not_a_pulse(width):
    """A runt half way between two pulses, width 1 tick to 2 us: reported with
    its stamp, left out of the grid, and the intervals around it unharmed."""
    b = Bench()
    rises = [5000 + k * (SEC + 11) for k in range(4)]
    runt = rises[1] + SEC // 2
    for r in rises[:2]:
        b.drive(1, r)
        b.drive(0, r + SEC // 10)
        b.settle()
    b.drive(1, runt)
    b.drive(0, runt + width)
    b.settle()
    for r in rises[2:]:
        b.drive(1, r)
        b.drive(0, r + SEC // 10)
        b.settle()
    rep = pps.analyse(b.edges, SEC, min_width=200)
    assert [p.rise for p in rep.runts] == [b.want(runt)]
    p = rep.runts[0]
    assert p.width == max(width, pps.RECORD)
    assert p.width - p.fall_late <= width <= p.width + p.late
    assert rep.intervals == [SEC + 11] * 3 and rep.period == SEC + 11
    assert not rep.missed and not rep.offgrid
    # with runts treated as pulses, the same stamps must look wrong
    naive = pps.analyse(b.edges, SEC, min_width=1)
    assert naive.offgrid and [p.rise for p in naive.offgrid] == [b.want(runt)] and width >= 1


def test_an_off_grid_wide_pulse_is_not_fitted():
    rises = [5000 + k * SEC for k in range(4)]
    b = Bench()
    for i, r in enumerate(rises):
        b.drive(1, r)
        b.drive(0, r + 500)
        b.settle()
        if i == 1:
            b.drive(1, r + SEC // 3)
            b.drive(0, r + SEC // 3 + 500)
            b.settle()
    rep = pps.analyse(b.edges, SEC, min_width=50)
    assert [p.rise for p in rep.offgrid] == [b.want(rises[1] + SEC // 3)]
    assert rep.intervals == [SEC] * 3 and rep.drift_ppm == 0


def test_an_off_grid_first_pulse_is_reported_not_hidden():
    """The stated limit of the anchor: a wide glitch first leaves every real
    pulse off grid, in `offgrid`, and nothing fitted."""
    b = Bench()
    b.drive(1, 2000)
    b.drive(0, 3000)
    b.settle()
    for k in range(1, 4):
        b.drive(1, SEC // 3 + k * SEC)
        b.drive(0, SEC // 3 + k * SEC + 1000)
        b.settle()
    rep = pps.analyse(b.edges, SEC, min_width=50)
    assert len(rep.rises) == 1 and len(rep.offgrid) == 3 and rep.period is None


# The host --------------------------------------------------------------------

def test_a_slow_host_loses_edges_never_time():
    """No read for 9 edges: the first CAPACITY stay, the rest are dropped and
    counted, the core is never stalled by the FIFO, and the first record after
    the drain is exact and says what happened."""
    b = Bench()
    c = b.cycle + 30
    drive = []
    for i in range(9):
        b.drive((i + 1) & 1, c)
        drive.append(c)
        c += 200
        # a WAIT is the only stall: the FIFO never holds the core
        b.advance(c - 150)
        core = b.mh.chip.cores[0]
        assert core.stalled and core.in_wait
    b.advance(c)
    assert len(b.mh.chip.cores[0].rxf) == 2 * pps.CAPACITY
    got = b.drain()
    assert [e.tick for e in got] == [b.want(x) for x in drive[:pps.CAPACITY]]
    assert [e.drops for e in got] == [0] * pps.CAPACITY
    c = b.cycle + 30
    b.drive(0, c)
    b.drive(1, c + 100)
    b.settle()
    next_edge = b.edges[pps.CAPACITY]
    assert next_edge.tick == b.want(c) and next_edge.level == 0
    assert next_edge.drops == 9 - pps.CAPACITY and b.dec.dropped == 5
    assert next_edge.late == pps.CARRY - 1            # the record before it is unknown
    assert b.edges[pps.CAPACITY + 1].tick == b.want(c + 100) and b.edges[pps.CAPACITY + 1].late == 0


def test_a_lost_fall_leaves_no_false_width():
    """Records dropped between a rise and its fall: no width is claimed for
    the pulse the drop split."""
    d = pps.Decoder()
    rec = lambda t, lv, drops: [(drops << 19) | (1 << 17) | (lv << 16) | 0xFFFF,
                                (0xFFFFFFFF - t) & 0xFFFFFFFF]
    edges = d.feed(rec(100, 1, 0) + rec(5000, 0, 2) + rec(9000, 1, 2) + rec(9500, 1, 3))
    assert [e.drops for e in edges] == [0, 2, 0, 1]
    ps = pps.pulses(edges)
    assert [(p.rise, p.width) for p in ps] == [(100, None), (9000, None), (9500, None)]
    assert d.dropped == 3


def test_drop_count_wraps_at_8192():
    d = pps.Decoder()
    rec = lambda t, drops: [(drops << 19) | (1 << 17) | (1 << 16) | 0xFFFF, (0xFFFFFFFF - t) & 0xFFFFFFFF]
    edges = d.feed(rec(100, 8190) + rec(200, 3) + rec(300, 3))
    assert [e.drops for e in edges] == [8190, 5, 0] and d.dropped == 8195


def test_runt_is_decided_on_the_narrowest_width_the_stamps_allow():
    """A 1-tick pulse reads as 13 wide, with `fall_late` 12: below min_width 5
    on what it may really be, although 13 is above it."""
    b = Bench()
    c = b.cycle + 20
    b.drive(1, c)
    b.drive(0, c + 1)
    b.settle()
    b.drive(1, b.cycle + 200)
    b.drive(0, b.cycle + 30)
    b.settle()
    rep = pps.analyse(b.edges, 1000, min_width=5, tol=999 // 2)
    assert [p.width for p in rep.runts] == [pps.RECORD]
    assert len(rep.rises) == 1


def test_host_pass_rate_bound():
    """Edges 200 clocks apart. A pass after every 4 edges loses none; after
    every 5 it loses exactly the fifth each time: the FIFO holds CAPACITY."""
    for every, lost in ((pps.CAPACITY, 0), (pps.CAPACITY + 1, 1)):
        b = Bench()
        c = b.cycle + 40
        passes = 8
        for i in range(every * passes + 1):
            b.drive((i + 1) & 1, c)
            c += 200
            if (i + 1) % every == 0:
                b.advance(c - 100)
                b.drain()
                c = max(c, b.cycle + 1)
        b.settle()
        assert b.dec.dropped == lost * passes
        assert len(b.edges) == every * passes + 1 - lost * passes


def test_word_order_and_split_reads():
    """A host pass may end between a record's two words."""
    b = Bench()
    c = b.cycle + 20
    b.drive(1, c)
    b.drive(0, c + 100)
    b.advance(c + 200)
    words = b.mh.pop_all(0)
    assert len(words) == 4 and words[0] >> 16 == 0b11 and words[2] >> 16 == 0b10
    assert (words[0] >> 17) & 3 == 1
    d1, d2 = pps.Decoder(), pps.Decoder()
    whole = d1.feed(words)
    split = d2.feed(words[:1]) + d2.feed(words[1:3]) + d2.feed(words[3:])
    assert whole == split and [e.tick for e in whole] == [b.want(c), b.want(c + 100)]


@pytest.mark.parametrize("word", [0, 0x00010000, 0x00040000, 0x00060000])
def test_decoder_rejects_a_word_that_is_not_a_record(word):
    with pytest.raises(ValueError):
        pps.Decoder().feed([word, 0])


def test_load_and_analyse_reject_bad_arguments():
    with pytest.raises(ValueError):
        pps.load_writes(PROG, start=pps.WRAP)
    with pytest.raises(ValueError):
        pps.load_writes(PROG, start=-1)
    with pytest.raises(ValueError):
        pps.analyse([], 0)
    with pytest.raises(ValueError):
        pps.analyse([], 1000, min_width=0)
    with pytest.raises(ValueError):
        pps.analyse([], 1000, tol=500)
    assert pps.analyse([], 1000).period is None


def test_no_pulse_at_all_gives_no_records():
    """The stated limit: a dead input produces nothing, so "the input stopped"
    is the host's own timeout, not something the chip reports."""
    b = Bench()
    b.advance(b.cycle + 300000)
    assert b.drain() == []


def test_summary_states_the_reference():
    b = train([5000 + k * (SEC + 50) for k in range(3)])
    s = pps.analyse(b.edges, SEC).summary()
    assert "3 pulses, 0 missed" in s and "+1.000 ppm against the chip's clock" in s
    assert "UTC" not in s


# Firmware mutants: each must fail a check above ---------------------------------

TEXT = pps.FW.read_text()


def exact_run(prog):
    """Edges placed on every path, in two runs: True if every stamp is right.
    With start = 1 << 32 | 200, r2 reaches 0 at tick 200 + 65536 k, r3 ran out
    at tick 200. Run A puts a record on the carry and on a borrow; run B lets
    the WAIT time out through the carry and the plain rollover. No whole-period
    skip here: it would step over the rollover path under test."""
    for ticks in ([80, 195, 400, 65731, 66000], [80, 400, 66000]):
        b = Bench(start=(1 << 32) | 200, prog=prog, whole=False)
        want = []
        for i, t in enumerate(ticks):
            c = b.zero + t - pps.PAD_TO_WAIT
            b.drive((i + 1) & 1, c)
            want.append(t)
            b.advance(c + 45)
            b.drain()
        b.settle()
        if [e.tick for e in b.edges] != want:
            return False
    return True


@pytest.mark.parametrize("old,new", [
    ("sub r2, 13 ", "sub r2, 12 "),                     # the record path's time
    ("sub r2, 3 ", "sub r2, 2 "),                       # the borrow path's
    ("sub r2, 6 ", "sub r2, 5 "),                       # the carry path's
    ("ldi r2, 0xFFFC", "ldi r2, 0xFFFF"),               # the rollover's
    ("ldi r2, 0xFFF8", "ldi r2, 0xFFFC"),               # the carry's
    ("jmp r3--, over1", "jmp over1"),                   # no carry at all
    ("xor r0, 1 ", "nop "),                             # never looks for the other level
])
def test_firmware_mutants_change_the_stamps(old, new):
    assert old in TEXT
    assert exact_run(PROG)
    assert not exact_run(asm.assemble(TEXT.replace(old, new)))


def slow_host(prog):
    """(records kept, the drop count seen, ticks of the first edge after them):
    9 edges unread, a drain, one more edge."""
    b = Bench(prog=prog, fast=False)
    c = b.cycle + 30
    for i in range(9):
        b.drive((i + 1) & 1, c)
        c += 200
    b.advance(c)
    kept = len(b.drain())
    c = b.cycle + 30
    b.drive(0, c)
    b.settle()
    return kept, b.dec.dropped, b.edges[-1].tick == b.want(c)


@pytest.mark.parametrize("old,new", [
    ("cmp r1, 0x70 ", "cmp r1, 0x60 "),                 # one record short
    ("add r0, 8 ", "add r0, 16 "),                      # drops miscounted
    ("add r0, 8                 [2]", "add r0, 8                 [1]"),   # the drop path's time
])
def test_drop_path_mutants(old, new):
    assert slow_host(PROG) == (pps.CAPACITY, 5, True)
    assert old in TEXT
    assert slow_host(asm.assemble(TEXT.replace(old, new))) != (pps.CAPACITY, 5, True)


def test_mutant_blocking_push_loses_time():
    """Without the room check a full FIFO stops the counter: the check in
    test_a_slow_host_loses_edges_never_time is what catches it."""
    text = TEXT.replace("jmp !c, drop ", "nop ")
    prog = asm.assemble(text)
    b = Bench(prog=prog, fast=False)
    c = b.cycle + 30
    for i in range(6):
        b.drive((i + 1) & 1, c)
        c += 200
    b.advance(c)
    assert b.mh.chip.cores[0].stalled and not b.mh.chip.cores[0].in_wait   # stuck in a push
