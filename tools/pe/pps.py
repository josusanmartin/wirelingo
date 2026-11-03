"""Host side of firmware/pps_capture.pasm: load it, decode its records, report
intervals and drift against the chip's own clock (docs/pps-capture.md).

    mh.apply(pps.load_writes(pps.assemble(), pin=16))
    dec = pps.Decoder()
    edges = dec.feed(words)             # words read from core 0's RX FIFO
    rep = pps.analyse(edges, nominal=50_000_000)   # a 1 Hz input at 50 MHz
    print(rep.summary(ns_per_tick=20))

Read the FIFO by its level (STATUS[7:4]), not blindly: a read of an empty FIFO
returns 0, and word 1 of a record can be 0 as well, so zeros can't be told from
data. Decoder raises on a word 0 that isn't one. Everything here is relative to
the chip's own clock: the drift of a pulse train against that clock says which
one moved only if the other is trusted, and the chip has no notion of UTC.
"""
from dataclasses import dataclass, field
from fractions import Fraction
import math
import pathlib

from . import asm, host, sim
from .profile import TAPEOUT

FW = pathlib.Path(__file__).resolve().parents[2] / "firmware" / "pps_capture.pasm"

BITS = 48
WRAP = 1 << BITS
DROP_WRAP = 1 << 13
CAPACITY = 4                  # records the 8-word RX FIFO holds
START = 4                     # first instruction -> first WAIT attempt, ticks
PAD_TO_WAIT = 2               # pad value at clock edge c -> the WAIT at c + 2 (isa §2)
ROLL, ROLL_CARRY = 4, 8       # WAIT -> next attempt after a rollover, r3 ran out too
RECORD, BORROW, CARRY = 13, 16, 19      # ... after a record, r2 went through 0, r3 too
ROLL_R2, CARRY_R2 = 0xFFFC, 0xFFF8      # r2 at the first attempt after each rollover


def assemble():
    return asm.assemble(FW.read_text())


def load_writes(prog, pin=16, core=0, start=WRAP - 1, run=True, profile=TAPEOUT):
    """Register writes: the program, CLKDIV 1, the counter's start value (the
    counter at the first WAIT attempt), then the start. `pin`: any input a
    core can read as a pad, or any logical pin."""
    if not 0 <= start < WRAP:
        raise ValueError("start must fit in 48 bits")
    w = host.load_program(core, prog, in_pins=pin, clkdiv=1 << 8, profile=profile)
    for reg, v in ((1, start >> 32), (2, start & 0xFFFF), (3, (start >> 16) & 0xFFFF)):
        w.append((sim.core_reg(core, sim.C_R0 + reg), v))
    if run:
        w += host.start_writes(1 << core)
    return w


@dataclass(frozen=True)
class Edge:
    tick: int           # chip clocks after the first WAIT attempt, extended past 2**48
    level: int          # the input's level after the edge: 1 = rising
    late: int = 0       # the edge happened 0..late ticks before `tick`
    drops: int = 0      # records dropped just before this one
    counter: int = 0    # the raw 48-bit counter


def record_path(counter):
    """Ticks from a record's WAIT to the next attempt, from its stamp."""
    if counter & 0xFFFF >= RECORD:
        return RECORD
    return CARRY if (counter >> 16) & 0xFFFF == 0 else BORROW


class Decoder:
    """Words -> Edges, keeping state across host passes (a pass may end
    between the two words of a record)."""

    def __init__(self, start=WRAP - 1):
        self.start = start
        self.partial = None
        self.last = None           # the last Edge
        self.base = 0              # wraps of the 48-bit counter so far, in ticks
        self.drops = 0             # the firmware's cumulative count
        self.dropped = 0           # records dropped, seen so far
        self.records = 0

    def feed(self, words):
        out = []
        for w in words:
            if self.partial is None:
                if (w >> 17) & 3 != 1:
                    raise ValueError(f"not word 0 of a pps_capture record: {w:#010x}")
                self.partial = w
            else:
                out.append(self._record(self.partial, w))
                self.partial = None
        return out

    def _record(self, w0, w1):
        counter = ((w0 & 0xFFFF) << 32) | w1
        drops = (w0 >> 19) & (DROP_WRAP - 1)
        delta = (drops - self.drops) % DROP_WRAP
        self.drops = drops
        self.dropped += delta
        raw = (self.start - counter) % WRAP
        last = self.last
        if last is not None and raw < last.tick % WRAP:
            self.base += WRAP
        tick = self.base + raw
        late = 0
        low, mid = counter & 0xFFFF, (counter >> 16) & 0xFFFF
        if low == ROLL_R2:
            late = ROLL - 1
        elif low == CARRY_R2 and mid == 0xFFFF:
            late = ROLL_CARRY - 1
        if delta:                  # the record before this one is unknown
            late = max(late, CARRY - 1)
        elif last is None:
            if tick == 0:          # between the start sample and the first attempt
                late = max(late, START - 2)
        else:
            path = record_path(last.counter)
            if tick - last.tick == path:
                late = max(late, path - 1)
        self.last = Edge(tick, (w0 >> 16) & 1, late, delta, counter)
        self.records += 1
        return self.last


@dataclass(frozen=True)
class Pulse:
    rise: int
    width: int | None   # None: no fall recorded (the capture ended, or it was dropped)
    late: int           # the rise happened 0..late ticks before `rise`
    fall_late: int      # the true width is in [width - fall_late, width + late]


def pulses(edges):
    """Rising edges with their widths, from a list of Edges. A fall whose rise
    wasn't recorded is skipped."""
    out, rise = [], None
    for e in edges:
        if e.level:
            if rise is not None:
                out.append(Pulse(rise.tick, None, rise.late, 0))
            rise = e
        else:
            if rise is not None:
                width = None if e.drops else e.tick - rise.tick
                out.append(Pulse(rise.tick, width, rise.late, e.late if width is not None else 0))
            rise = None
    if rise is not None:
        out.append(Pulse(rise.tick, None, rise.late, 0))
    return out


@dataclass
class Report:
    nominal: int
    rises: list = field(default_factory=list)      # (pulse number, tick) of the accepted pulses
    intervals: list = field(default_factory=list)  # ticks between consecutive accepted pulses
    missed: list = field(default_factory=list)     # pulse numbers with no pulse
    runts: list = field(default_factory=list)      # Pulses narrower than min_width
    offgrid: list = field(default_factory=list)    # Pulses too far from the grid
    dropped: int = 0                               # records the host was too slow for
    period: Fraction | None = None                 # fitted ticks per pulse number
    drift_ppm: Fraction | None = None              # period / nominal - 1, in ppm
    tie_pp: Fraction | None = None                 # peak-to-peak residual of the fit, ticks
    tie_rms: float | None = None                   # rms residual of the fit, ticks
    max_late: int = 0                              # worst `late` among the accepted pulses

    def summary(self, ns_per_tick=20.0):
        lines = [f"{len(self.rises)} pulses, {len(self.missed)} missed, {len(self.runts)} runts, "
                 f"{len(self.offgrid)} off grid, {self.dropped} records dropped"]
        if self.period is not None:
            lines.append(f"period {float(self.period):.3f} ticks = {float(self.period) * ns_per_tick:.1f} ns, "
                         f"drift {float(self.drift_ppm):+.3f} ppm against the chip's clock")
            lines.append(f"residual {float(self.tie_pp) * ns_per_tick:.1f} ns peak-to-peak, "
                         f"{self.tie_rms * ns_per_tick:.1f} ns rms")
        return "\n".join(lines)


def analyse(edges, nominal, *, min_width=1, tol=None, dropped=None):
    """Sort a pulse train into accepted pulses, runts, off-grid pulses and gaps,
    and fit the accepted ones. nominal: ticks per pulse (50_000_000 for 1 Hz at
    50 MHz). min_width: narrower pulses are runts (decided on the narrowest
    width the stamps allow), and neither count as pulses nor move the grid.
    tol: an accepted pulse is within tol ticks of a whole number of nominal
    periods after the previous one (default nominal / 1000, i.e. 1000 ppm).
    The first wide pulse anchors the grid: if that one is off grid, so is every
    other, and they land in `offgrid` rather than in the fit."""
    if nominal <= 0:
        raise ValueError("nominal must be positive")
    if min_width < 1:
        raise ValueError("min_width must be at least 1")
    tol = nominal // 1000 if tol is None else tol
    if tol < 0 or 2 * tol >= nominal:
        raise ValueError("tol must be in [0, nominal / 2)")
    rep = Report(nominal)
    rep.dropped = sum(e.drops for e in edges) if dropped is None else dropped
    n = 0
    prev = None
    late = []
    for p in pulses(edges):
        if p.width is not None and p.width - p.fall_late < min_width:
            rep.runts.append(p)
            continue
        if prev is None:
            prev = p.rise
            rep.rises.append((0, p.rise))
            late.append(p.late)
            continue
        dt = p.rise - prev
        k = (2 * dt + nominal) // (2 * nominal)        # nearest whole number of periods
        if k < 1 or abs(dt - k * nominal) > tol:
            rep.offgrid.append(p)
            continue
        rep.missed += range(n + 1, n + k)
        n += k
        rep.intervals.append(dt)
        rep.rises.append((n, p.rise))
        late.append(p.late)
        prev = p.rise
    rep.max_late = max(late, default=0)
    if len(rep.rises) >= 2:
        xs, ys = zip(*rep.rises)
        m = len(xs)
        xbar, ybar = Fraction(sum(xs), m), Fraction(sum(ys), m)
        sxx = sum((x - xbar) ** 2 for x in xs)
        rep.period = sum((x - xbar) * (y - ybar) for x, y in rep.rises) / sxx
        rep.drift_ppm = (rep.period / nominal - 1) * 1_000_000
        res = [y - ybar - rep.period * (x - xbar) for x, y in rep.rises]
        rep.tie_pp = max(res) - min(res)
        rep.tie_rms = math.sqrt(sum(float(r) ** 2 for r in res) / m)
    return rep
