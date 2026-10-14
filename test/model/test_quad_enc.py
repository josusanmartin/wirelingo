"""Quadrature encoder counter (firmware/quad_enc.pasm, post-freeze) on the
cycle model at CLKDIV 1 (one tick per clock; 50 MHz in the numbers).

Judges: an encoder model that knows the position it moved to (the steps it
made), the pins-only X4 decoder pe.postfreeze2.quad_count on the lines it
drove, and sigrok's graycode decoder."""
import pathlib
import random

import pytest

from pe import asm, host, postfreeze as pf, postfreeze2 as p2, sigrok, sim
from pe.modelhost import ModelHost

FW = pathlib.Path(__file__).resolve().parents[2] / "firmware"
A = 16                    # ui0, B = ui1
FERR = 6
SEQ = p2.QUAD_SEQ         # A | B << 1, A leading B: forward


def rig():
    mh = ModelHost()
    prog = asm.assemble((FW / "quad_enc.pasm").read_text(), {"A": A, "FERR": FERR})
    assert len(prog.words) <= 256
    mh.apply(host.load_program(0, prog, clkdiv=1 << 8))
    mh.write(sim.core_reg(0, sim.C_R0 + 3), 0)
    mh.ui = 0
    mh.start(1)
    mh.step(5)
    return mh


def position(mh):
    v = mh.read(sim.core_reg(0, sim.C_R0 + 3))
    return v - 0x10000 if v & 0x8000 else v


def ferr(mh):
    return (mh.chip.flags >> FERR) & 1


class Encoder:
    """Drives A/B: step(+1/-1) moves one quarter cycle; hold(n) waits n
    clocks. Keeps the true position and a log of the lines."""

    def __init__(self, mh):
        self.mh, self.phase, self.pos = mh, 0, 0
        self.log = [(0.0, 0)]

    def hold(self, n):
        for _ in range(n):
            self.mh.step()

    def set(self, state):
        self.mh.ui = state
        self.log.append((self.mh.chip.cycle * 20.0, state))

    def step(self, d):
        self.phase = (self.phase + d) % 4
        self.pos += d
        self.set(SEQ[self.phase])


def test_quad_random_walk():
    """5000 steps of a random walk (direction changes, bursts, pauses),
    steps 15 to 60 clocks apart: the position is exact, no FERR."""
    mh = rig()
    enc = Encoder(mh)
    rng = random.Random(1)
    d = 1
    for _ in range(5000):
        if rng.random() < 0.05:
            d = -d
        enc.step(d)
        enc.hold(rng.choice([15, 16, 20, 30, 60]))
    enc.hold(40)
    print(f"\nrandom walk: true {enc.pos}, counted {position(mh)}")
    assert position(mh) == enc.pos and not ferr(mh)


@pytest.mark.parametrize("d", [1, -1])
def test_quad_max_rate(d):
    """Steady rotation with steps every n clocks: exact for n >= 14 forward
    and n >= 15 backward (the loop's 14 / 15 clocks, as the checker bounds
    it); faster, steps are missed and FERR says so (the negative control)."""
    ok = {}
    for n in range(10, 18):
        mh = rig()
        enc = Encoder(mh)
        for _ in range(400):
            enc.step(d)
            enc.hold(n)
        enc.hold(40)
        ok[n] = (position(mh) == enc.pos, ferr(mh))
    print(f"\ndirection {d:+d}: steps every n clocks -> (exact, FERR): {ok}")
    lim = 14 if d > 0 else 15
    assert all(ok[n] == (True, 0) for n in ok if n >= lim)
    assert all(ok[n][0] is False and ok[n][1] == 1 for n in ok if n < lim)


def test_quad_glitches_and_errors():
    """A 1-clock pulse on A and a 3-clock pulse on B between steps move
    nothing; a jump of two states (both lines at once) sets FERR and does
    not move the position; the count goes on after it."""
    mh = rig()
    enc = Encoder(mh)
    for _ in range(10):
        enc.step(1)
        enc.hold(30)
    s = SEQ[enc.phase]
    enc.set(s ^ 1)
    enc.hold(1)
    enc.set(s)
    enc.hold(40)
    enc.set(s ^ 2)
    enc.hold(3)
    enc.set(s)
    enc.hold(40)
    assert position(mh) == 10 and not ferr(mh)
    enc.phase = (enc.phase + 2) % 4               # a missed step: both lines change
    enc.set(SEQ[enc.phase])
    enc.hold(40)
    assert position(mh) == 10 and ferr(mh) and p2.quad_count(enc.log) == (10, 1)
    for _ in range(7):
        enc.step(-1)
        enc.hold(30)
    assert position(mh) == 3


def test_quad_wrap():
    """The position is 16 bits: 40000 steps forward read as 40000 - 65536."""
    mh = rig()
    enc = Encoder(mh)
    for _ in range(40000):
        enc.step(1)
        enc.hold(14)
    enc.hold(40)
    assert position(mh) == 40000 - 65536


@pytest.mark.skipif(sigrok.cli() is None, reason=sigrok.INSTALL_HINT)
def test_sigrok_quad():
    """sigrok's graycode decoder on A/B (d0 = A, d1 = B): its count, at the
    last change before a final known step, equals the firmware's position
    read just before that step."""
    mh = rig()
    enc = Encoder(mh)
    rng = random.Random(4)
    d = 1
    for _ in range(600):
        if rng.random() < 0.1:
            d = -d
        enc.step(d)
        enc.hold(rng.choice([15, 25, 50]))
    enc.hold(40)
    ours = position(mh)
    enc.step(1)
    enc.hold(40)
    tr = sigrok.Trace()
    pf.to_trace(tr, pf.bit_edges(enc.log, 0), "a")
    pf.to_trace(tr, pf.bit_edges(enc.log, 1), "b")
    tr.end(round(mh.chip.cycle * 20.0 * 1000))
    counts = sigrok.graycode(tr, ["a", "b"], res_ps=10_000)
    print(f"\nsigrok graycode: {len(counts)} count changes, last {counts[-1]}; firmware {ours}, true {enc.pos - 1}")
    assert counts[-1] == ours == enc.pos - 1
