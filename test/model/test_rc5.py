"""Philips RC-5 infrared transmitter and receiver (firmware/rc5_tx.pasm,
rc5_rx.pasm, post-freeze) on the cycle model.

Judges: pe.postfreeze3.rc5_decode (pins only, from the definition of the
code), a 36 kHz receiver-module model between the LED and the receiver
(pe.postfreeze.IrReceiverModule), an independent remote
(pe.postfreeze3.rc5_envelope: its own rate, bursts stretched as receiver
modules do), the LED's carrier measured edge by edge, and sigrok's ir_rc5
decoder on the demodulated line.

rc5_tx: 1 tick = 1/3 carrier period (108 kHz ticks), run at CLKDIV 4 with
the chip at 432 kHz (tick for tick 50 MHz / 462.96); rc5_rx: 128 ticks per
bit, CLKDIV 4 at 288 kHz (tick for tick 50 MHz / 694.4); both together at
864 kHz (CLKDIV 8 and 12)."""
import pathlib
import random

import pytest

from pe import asm, host, postfreeze as pf, postfreeze3 as p3, sigrok, sim
from pe.modelhost import ModelHost
from pe.postfreeze2 import Player

FW = pathlib.Path(__file__).resolve().parents[2] / "firmware"
LED, RX, FOV = 8, 16, 7            # uo0, ui0


def tx_prog():
    p = asm.assemble((FW / "rc5_tx.pasm").read_text(), {"IR": LED})
    assert len(p.words) <= 256
    return p


def rx_prog():
    p = asm.assemble((FW / "rc5_rx.pasm").read_text(), {"IR": RX, "TB": 128, "FOV": FOV})
    assert len(p.words) <= 256
    return p


class TxRig:
    """rc5_tx on core 0; the LED through the receiver-module model."""

    def __init__(self, f_clk=432e3, clkdiv=4 << 8):
        self.mh = mh = ModelHost()
        mh.apply(host.load_program(0, tx_prog(), side_pins=LED, clkdiv=clkdiv))
        self.period = 1e9 / f_clk
        self.tsop = pf.IrReceiverModule(f_carrier=p3.RC5_F)
        self.led, self.out = [(0.0, 0)], [(0.0, 1)]
        mh.start(1)

    def run(self, ns):
        mh = self.mh
        for _ in range(int(ns / self.period)):
            t = mh.chip.cycle * self.period
            led = (mh.chip.pin_out >> LED) & 1
            o = self.tsop.step(t, led)
            if led != self.led[-1][1]:
                self.led.append((t, led))
            if o != self.out[-1][1]:
                self.out.append((t, o))
            mh.step()

    def now(self):
        return self.mh.chip.cycle * self.period


def test_rc5_tx():
    """Frames out: a new key, two repeats (same toggle bit), a new key
    (toggle flips), an RC-5X command (bit 6 in S2); decoded pins-only from
    the receiver module's output; on the LED a 36 kHz carrier (every period
    3 ticks, on for 1), bursts of 32 or 64 periods, frames 64 bits apart."""
    rig = TxRig()
    keys = [(0, 12, False), (0, 12, True), (0, 12, True), (5, 53, False), (20, 70, False)]
    for a, c, rep in keys:
        rig.mh.push(0, p3.rc5_word(a, c, rep))
    rig.run(len(keys) * 114e6 + 10e6)
    frames, errs = p3.rc5_decode(rig.out, rig.now())
    got = [p3.rc5_fields(b) for _, b in frames]
    print(f"\ndecoded {got} {errs}")
    assert not errs and [g[:2] for g in got] == [(a, c) for a, c, _ in keys]
    tog = [g[2] for g in got]
    assert tog[0] == tog[1] == tog[2] != tog[3] != tog[4]
    tick = rig.period * 4
    rises = [t for t, v in rig.led if v]
    per = {round((b - a) / tick, 6) for a, b in zip(rises, rises[1:]) if b - a < 10 * tick}
    on = {round((b[0] - a[0]) / tick, 6) for a, b in zip(rig.led, rig.led[1:]) if a[1] == 1}
    bursts = pf.carrier_bursts(rig.led, 2 * 3 * tick)
    nper = sorted({n for _, _, n in bursts})
    starts = [frames[k][0] for k in range(len(frames))]
    gaps = {round((b - a) / tick) for a, b in zip(starts, starts[1:])}
    print(f"carrier period {per} ticks, on {on}; bursts of {nper} periods; frames {gaps} ticks apart")
    assert per == {3.0} and on == {1.0} and nper == [32, 64] and gaps == {12288}


class RxRig:
    """rc5_rx on core 0, its input played from a waveform."""

    def __init__(self, f_clk=288e3, div=4):
        self.mh = mh = ModelHost()
        mh.apply(host.load_program(0, rx_prog(), in_pins=RX, clkdiv=div << 8))
        mh.ui = 1
        mh.start(1)
        self.period = 1e9 / f_clk
        self.player = Player()
        self.words = []

    def run(self, ns):
        mh = self.mh
        for _ in range(int(ns / self.period)):
            mh.ui = self.player.level(mh.chip.cycle * self.period)
            mh.step()
            if mh.chip.cores[0].rxf:
                self.words += mh.pop_all(0)
        return [p3.rc5_record(w) for w in self.words]


@pytest.mark.parametrize("scale,stretch_us", [(1.0, 0), (0.9, 0), (1.1, 0), (1.0, 150), (1.0, -150),
                                              (0.9, 150), (1.1, -150)])
def test_rc5_rx_tolerance(scale, stretch_us):
    """An independent remote 10 % fast or slow, its bursts lengthened or
    shortened by 150 us (receiver modules): every frame decoded with its
    toggle bit."""
    rig = RxRig()
    rng = random.Random(int(scale * 10) + stretch_us)
    sent, t = [], 20e6
    for k in range(6):
        a, c, tg = rng.randrange(32), rng.randrange(128), k & 1
        sent.append((a, c, tg))
        t = rig.player.schedule(t, p3.rc5_envelope(a, c, tg, scale, stretch_us * 1e3)) + 90e6
    recs = rig.run(t)
    assert [(r["address"], r["command"], r["toggle"]) for r in recs] == sent


def test_rc5_rx_errors():
    """Negative controls: a 200 us glitch is ignored; a frame cut after 8
    bits and one with a bit that has no mid-bit transition are errors; a
    good frame after them is received."""
    rig = RxRig()
    t = 20e6
    t = rig.player.schedule(t, [(0, 200e3)]) + 20e6
    cut = p3.rc5_envelope(1, 2, 0)
    tt = 0
    for i, (lv, d) in enumerate(cut):                 # the waveform up to bit 8
        tt += d
        if tt > 8 * p3.RC5_BIT:
            cut = cut[:i]
            break
    t = rig.player.schedule(t, cut) + 30e6
    bits = p3.rc5_bits(3, 4, 1)
    # bit 6 without its mid-bit transition: its two halves at one level
    halves = []
    for b in bits:
        halves += [1 - b, b]
    halves[13] = halves[12]
    flat = []
    for x in halves:
        lvl = 0 if x else 1
        if flat and flat[-1][0] == lvl:
            flat[-1] = (lvl, flat[-1][1] + p3.RC5_HALF)
        else:
            flat.append((lvl, p3.RC5_HALF))
    t = rig.player.schedule(t, flat[1:] if flat[0][0] == 1 else flat) + 30e6
    t = rig.player.schedule(t, p3.rc5_envelope(7, 33, 0)) + 30e6
    recs = rig.run(t)
    print(f"\n{recs}")
    assert [r["kind"] for r in recs] == ["error", "error", "frame"]
    assert recs[-1]["address"] == 7 and recs[-1]["command"] == 33


def test_rc5_loopback():
    """rc5_tx (core 0) -> LED -> the 36 kHz receiver module -> rc5_rx (core
    1) on one chip (864 kHz: CLKDIV 8 and 12): random keys and repeats, every
    address, command and toggle bit as sent."""
    mh = ModelHost()
    mh.apply(host.load_program(0, tx_prog(), side_pins=LED, clkdiv=8 << 8))
    mh.apply(host.load_program(1, rx_prog(), in_pins=RX, clkdiv=12 << 8))
    tsop = pf.IrReceiverModule(f_carrier=p3.RC5_F)
    mh.ui = 1
    mh.start(3)
    rng = random.Random(9)
    keys = []
    for _ in range(5):
        a, c = rng.randrange(32), rng.randrange(128)
        keys.append((a, c, False))
        if rng.random() < 0.5:
            keys.append((a, c, True))
    todo = [p3.rc5_word(a, c, r) for a, c, r in keys]
    period = 1e9 / 864e3
    words = []
    for _ in range(int((len(keys) * 114e6 + 20e6) / period)):
        if todo and mh.tx_level(0) < 8 and mh.chip.cycle * period > 10e6:   # (the receiver wants 2 idle bits first)
            mh.push(0, todo.pop(0))
        mh.ui = tsop.step(mh.chip.cycle * period, (mh.chip.pin_out >> LED) & 1)
        mh.step()
        if mh.chip.cores[1].rxf:
            words += mh.pop_all(1)
    recs = [p3.rc5_record(w) for w in words]
    got = [(r["address"], r["command"]) for r in recs]
    assert got == [(a, c) for a, c, _ in keys]
    tog = [r["toggle"] for r in recs]
    for k in range(1, len(keys)):
        assert (tog[k] == tog[k - 1]) == keys[k][2]


def test_rc5_50mhz():
    """At 50 MHz (CLKDIV 462.96): the carrier's period 1388 or 1389 clocks
    (36000 Hz on average), on for 462 or 463."""
    rig = TxRig(f_clk=50e6, clkdiv=host.clkdiv_for(50e6, 108e3))
    rig.mh.push(0, p3.rc5_word(0, 0))
    rig.run(1.0e6 + 4 * p3.RC5_BIT)
    rises = [t for t, v in rig.led if v]
    per = [round((b - a) / 20) for a, b in zip(rises, rises[1:]) if b - a < 60e3]
    on = {round((b[0] - a[0]) / 20) for a, b in zip(rig.led, rig.led[1:]) if a[1] == 1}
    f = 50e6 / (sum(per) / len(per))
    print(f"\nRC-5 carrier at 50 MHz: periods {sorted(set(per))} clocks, on {sorted(on)}, mean {f:.1f} Hz")
    assert set(per) <= {1388, 1389} and on <= {462, 463} and abs(f - 36000) < 2


@pytest.mark.skipif(sigrok.cli() is None, reason=sigrok.INSTALL_HINT)
def test_sigrok_rc5():
    """sigrok's ir_rc5 on the receiver module's output (active low): the
    same addresses, commands and toggle bits; RC-5X with protocol=extended."""
    rig = TxRig()
    keys = [(0, 12, False), (0, 12, True), (5, 53, False), (31, 63, False)]
    for a, c, r in keys:
        rig.mh.push(0, p3.rc5_word(a, c, r))
    rig.run(len(keys) * 114e6 + 10e6)
    tr = sigrok.Trace()
    pf.to_trace(tr, rig.out, "ir", rig.now())
    got = sigrok.ir_rc5(tr, "ir", res_ps=100_000)
    print(f"\nsigrok ir_rc5: {got}")
    assert [(g[1], g[2]) for g in got] == [(a, c) for a, c, _ in keys]
    assert got[0][3] == got[1][3] != got[2][3] != got[3][3]
