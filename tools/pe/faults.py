"""Seeded faults on the wire, between a pins-only protocol model and the chip.

Wire sits on the model's chip.step: whatever a device model drives (mh.ui,
mh.uio, or the pads passed straight to chip.step, as the USB and Ethernet
senders do) goes through the faults before the input synchronizers see it, at
both clock edges when the sender gives a half-cycle value.

    wire = Wire(mh, [Glitch(16, start=1000, width=20)])
    ...drive the model as usual...
    wire.clear()                     # the wire is clean again

Times are the chip's clocks (chip.cycle; half clocks at the falling edge).
Nothing else in the model changes: this is a fault on the chip's input pads and
on what a wired-AND peer reads through wire.bus(), never inside the chip.

    plan(kind, seed, span)           # one fault of KINDS, from the seed alone

The same seed gives the same faults on any machine: random.Random seeded with a
string is stable across Python versions and independent of the hash seed. See
docs/fault-testing.md.
"""
from dataclasses import dataclass
import random

from . import host

KINDS = ("glitch", "stuck", "truncate", "flip", "stretch", "jitter")
FOREVER = float("inf")


def _bit(pin):
    """(0, bit) for uio, (1, bit) for ui."""
    host.input_pad(pin)
    return (0, pin) if pin < 8 else (1, pin - 16)


@dataclass
class Fault:
    pin: int

    def __post_init__(self):
        self.where = _bit(self.pin)

    def apply(self, t, v):
        raise NotImplementedError


@dataclass
class Glitch(Fault):
    """The line is forced for `width` clocks from `start`: to `level`, or to
    its inverse (level None). With `bit`, width must be shorter than one bit."""
    start: float
    width: float
    level: int | None = None
    bit: float | None = None

    def __post_init__(self):
        super().__post_init__()
        if self.width <= 0:
            raise ValueError("a glitch has a width")
        if self.bit is not None and self.width >= self.bit:
            raise ValueError("a glitch is shorter than one bit")

    def apply(self, t, v):
        if self.start <= t < self.start + self.width:
            return 1 - v if self.level is None else self.level
        return v


@dataclass
class BitFlip(Fault):
    """The line inverted for one bit period from `start`."""
    start: float
    bit: float

    def apply(self, t, v):
        return 1 - v if self.start <= t < self.start + self.bit else v


@dataclass
class Stuck(Fault):
    """The line held at `level` from `start` until `end` (FOREVER: until
    wire.clear())."""
    level: int
    start: float = 0
    end: float = FOREVER

    def apply(self, t, v):
        return self.level if self.start <= t < self.end else v


@dataclass
class Truncate(Stuck):
    """The sender goes away: the line idles from `start` until `end`, whatever
    the sender was sending. A Stuck at the idle level, named for what it does
    to a frame."""


class _Delay(Fault):
    """Edges of the line leave later than they arrive: edge i by delay(i)
    clocks, and at least a clock after the edge ahead of it, so no edge is
    lost or reordered. Crowded edges can be later than delay(i) for that."""

    def _init_delay(self):
        self.seen = self.out = None
        self.n = 0
        self.pending = []
        self.last_t = self.last_v = None
        self.free = float("-inf")

    def delay(self, i):
        raise NotImplementedError

    def apply(self, t, v):
        if t == self.last_t:           # wire.bus() and chip.step at the same clock
            return self.last_v
        if self.seen is None:
            self.seen = self.out = v
        if v != self.seen:
            self.seen = v
            self.n += 1
            self.free = max(t + self.delay(self.n), self.free + 1)
            self.pending.append((self.free, v))
        while self.pending and self.pending[0][0] <= t:
            self.out = self.pending.pop(0)[1]
        self.last_t, self.last_v = t, self.out
        return self.out


class EdgeJitter(_Delay):
    """Each edge of the line late by a uniform whole number of clocks in
    0..amp, drawn from `seed` (a constant delay of amp / 2 plus +-amp / 2 of
    jitter). Whole clocks: the model's pads are sampled at clock edges."""

    def __init__(self, pin, amp, seed=0, start=0, end=FOREVER):
        Fault.__init__(self, pin)
        if amp < 0:
            raise ValueError("amp must not be negative")
        self.amp, self.start, self.end = amp, start, end
        self.rng = random.Random(f"jitter:{seed}")
        self._init_delay()

    def apply(self, t, v):
        self.t = t
        return super().apply(t, v)

    def delay(self, i):
        return self.rng.randint(0, self.amp) if self.start <= self.t < self.end else 0

    def __repr__(self):
        return f"EdgeJitter(pin={self.pin}, amp={self.amp}, start={self.start}, end={self.end})"


class Stretch(_Delay):
    """One bit of a sender runs long: from the line's nth edge on, every edge
    leaves `extra` clocks late (a one-way line: the sender's own clock
    stalled once and went on from there)."""

    def __init__(self, pin, nth, extra, start=0):
        Fault.__init__(self, pin)
        if nth < 1 or extra < 0:
            raise ValueError("nth >= 1, extra >= 0")
        self.nth, self.extra, self.start = nth, extra, start
        self.count = 0
        self._init_delay()

    def apply(self, t, v):
        if t >= self.start and self.seen is not None and v != self.seen:
            self.count += 1
        return super().apply(t, v)

    def delay(self, i):
        return self.extra if self.count >= self.nth else 0

    def __repr__(self):
        return f"Stretch(pin={self.pin}, nth={self.nth}, extra={self.extra}, start={self.start})"


class Hold(Fault):
    """A peer stretches a shared line: `extra` clocks of `level` starting at
    the line's nth change to `level` after `start` (I2C clock stretching: the
    nth SCL fall is held low)."""

    def __init__(self, pin, nth, extra, level=0, start=0):
        Fault.__init__(self, pin)
        if nth < 1 or extra < 0:
            raise ValueError("nth >= 1, extra >= 0")
        self.nth, self.extra, self.level, self.start = nth, extra, level, start
        self.prev, self.count, self.until, self.last_t = None, 0, None, None

    def apply(self, t, v):
        if t != self.last_t:
            self.last_t = t
            if t >= self.start and self.prev is not None and self.prev != v and v == self.level:
                self.count += 1
                if self.count == self.nth:
                    self.until = t + self.extra
            self.prev = v
        return self.level if self.until is not None and t < self.until else v

    def __repr__(self):
        return f"Hold(pin={self.pin}, nth={self.nth}, extra={self.extra}, level={self.level}, start={self.start})"


class Wire:
    """Faults on the pads between the model's devices and the chip."""

    def __init__(self, mh, faults=(), record=False):
        self.mh = mh
        self.faults = list(faults)
        self.real_step = mh.chip.step
        mh.chip.step = self.step
        self.trace = [] if record else None       # (cycle, raw pads, faulted pads)

    def add(self, *faults):
        self.faults += faults

    def clear(self):
        self.faults = []

    def filter(self, t, ui, uio):
        """(ui, uio) with the faults applied at time t."""
        v = [uio, ui]
        for f in self.faults:
            side, bit = f.where
            old = (v[side] >> bit) & 1
            if f.apply(t, old) != old:
                v[side] ^= 1 << bit
        return v[1], v[0]

    def bus(self):
        """The resolved uio bus as a wired-AND peer reads it, faults included."""
        return self.filter(self.mh.chip.cycle, self.mh.ui, self.mh.bus_uio())[1]

    def step(self, ui_in=0, uio_in=0, *a, ui_mid=None, uio_mid=None, **kw):
        t = self.mh.chip.cycle
        if self.faults:
            if ui_mid is not None or uio_mid is not None:     # earlier in time: first, for the stateful ones
                ui_mid, uio_mid = self.filter(t - 0.5, ui_in if ui_mid is None else ui_mid,
                                              uio_in if uio_mid is None else uio_mid)
            ui, uio = self.filter(t, ui_in, uio_in)
        else:
            ui, uio = ui_in, uio_in
        if self.trace is not None:
            self.trace.append((t, (ui_in, uio_in), (ui, uio)))
        self.real_step(ui, uio, *a, ui_mid=ui_mid, uio_mid=uio_mid, **kw)


# ------------------------------------------------------------------ seeded plans

@dataclass(frozen=True)
class Span:
    """A frame on the wire: where a fault may land. t0 and bit are clocks.
    idle: {pin: its idle level}; several pins move together (differential)."""
    t0: float
    bit: float
    nbits: int
    idle: dict

    @property
    def end(self):
        return self.t0 + self.bit * self.nbits

    @property
    def pins(self):
        return tuple(self.idle)


def plan(kind, seed, span):
    """The faults of one case: `kind` on the span's pins, drawn from seed."""
    rng = random.Random(f"{kind}:{seed}")
    bit, n = span.bit, span.nbits
    at = span.t0 + rng.randrange(max(1, int(bit * n)))        # anywhere in the frame
    edge = span.t0 + bit * rng.randrange(n)                   # a bit boundary
    if kind == "glitch":
        width = max(1, rng.randrange(1, max(2, int(bit))))
        return [Glitch(p, at, width, bit=bit if bit > 1 else None) for p in span.pins]
    if kind == "flip":
        return [BitFlip(p, edge, bit) for p in span.pins]
    if kind == "stuck":
        lengths = int(bit * n)
        end = at + rng.randrange(lengths // 8 + 1, lengths + 1)
        return [Stuck(p, rng.getrandbits(1), at, end) for p in span.pins]
    if kind == "truncate":
        cut = span.t0 + bit * rng.randrange(1, n)
        return [Truncate(p, lv, cut, span.end + bit) for p, lv in span.idle.items()]
    if kind == "stretch":
        nth, extra = rng.randrange(1, max(2, n // 2)), rng.randrange(1, max(2, int(bit) + 1))
        return [Stretch(p, nth, extra, span.t0) for p in span.pins]
    if kind == "jitter":
        amp, sub = rng.randrange(1, max(2, int(bit) // 2 + 1)), rng.getrandbits(32)
        return [EdgeJitter(p, amp, sub + i, span.t0, span.end) for i, p in enumerate(span.pins)]
    raise ValueError(f"unknown fault kind {kind!r}")
