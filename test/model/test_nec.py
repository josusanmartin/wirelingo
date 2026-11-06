"""NEC infrared transmitter and receiver (firmware/nec_tx.pasm, nec_rx.pasm,
post-freeze) on the cycle model.

Judges: pins-only models (pe.postfreeze: the carrier's bursts, an NEC decoder
of the envelope, a TSOP-style receiver module between the two cores, an
independent remote sending demodulated codes) and sigrok's ir_nec decoder,
both on the raw 38 kHz carrier (its carrier detection) and on the receiver
module's output.

Most runs clock the model at 456 kHz (TX CLKDIV 4, RX 4.56): the programs
count ticks, so the waveform is the same in ticks as at 50 MHz, at 1/110 of
the simulation cost (and the receiver sees coarser, 2.2 us, input timing). test_nec_tx_50mhz runs a repeat code at 50 MHz."""
import pathlib
import random

import pytest

from pe import asm, host, postfreeze as pf, sigrok
from pe.modelhost import ModelHost

FW = pathlib.Path(__file__).resolve().parents[2] / "firmware"
LED = 8                    # uo0: core 0's IR LED
RXP = 16                   # ui0: the receiver module's output into core 1
TICK_NS = 1e9 / 114e3      # nec_tx: 1/3 carrier period
F_SLOW = 456e3             # model clock for the long runs: TX CLKDIV 4, RX 4.56


def setup(f_clk=None, tx=True, rx=True):
    f_clk = f_clk or F_SLOW
    mh = ModelHost()
    if tx:
        prog = asm.assemble((FW / "nec_tx.pasm").read_text(), {"IR": LED})
        mh.apply(host.load_program(0, prog, clkdiv=host.clkdiv_for(f_clk, 114e3)))
    if rx:
        prog = asm.assemble((FW / "nec_rx.pasm").read_text(), {"IR": RXP})
        mh.apply(host.load_program(1, prog, in_pins=RXP, clkdiv=host.clkdiv_for(f_clk, 100e3)))
    mh.ui = 1
    mh.start((1 if tx else 0) | (2 if rx else 0))
    return mh


CODES = [(0x00, 0x45, "standard"), (0xA5, 0x5A, "standard"), (0x1234, 0x81, "extended"),
         (0, 0, "repeat"), (0xFF, 0x00, "standard"), (0, 0, "repeat")]


def want_records(codes):
    out = []
    for a, c, m in codes:
        if m == "repeat":
            out.append({"kind": "repeat"})
        else:
            addr = (a & 0xFF) | ((~a & 0xFF) << 8) if m == "standard" else a
            std = ((addr & 0xFF) ^ (addr >> 8)) == 0xFF
            out.append({"kind": "frame", "address": addr, "command": c, "command_ok": True, "standard": std})
    return out


def test_nec_tx_rx_through_receiver_module():
    """nec_tx (core 0) -> 38 kHz LED -> receiver module model -> nec_rx (core 1).
    Also judges the LED waveform: carrier frequency/duty, burst and frame
    times, decoded independently from the carrier's envelope."""
    f_clk = F_SLOW
    period = 1e9 / f_clk
    mh = setup(f_clk)
    tsop = pf.IrReceiverModule()
    led = pf.PinLog(mh, pf.pin_probe(LED), period)
    for a, c, m in CODES:
        mh.push(0, pf.nec_word(a, c, m))
    recs, tsop_out = [], [(0, 1)]
    for _ in range(int(len(CODES) * 0.110 * f_clk)):
        t = mh.chip.cycle * period
        o = tsop.step(t, (mh.chip.pin_out >> LED) & 1)
        if o != tsop_out[-1][1]:
            tsop_out.append((t, o))
        mh.ui = o
        led.step()
        for w in mh.pop_all(1):
            recs.append(pf.nec_rx_record(w))
    assert recs == want_records(CODES), recs
    # the LED: bursts of a 38 kHz, 1/3-duty carrier
    edges = led.edges()
    rises = [t for t, v in edges if v == 1]
    per = [b - a for a, b in zip(rises, rises[1:]) if b - a < 40000]
    highs = [b[0] - a[0] for a, b in zip(edges, edges[1:]) if a[1] == 1]
    f_car = 1e9 / (sum(per) / len(per))
    print(f"\nNEC TX carrier at {f_clk / 1e6} MHz clock: {f_car:.1f} Hz, high {min(highs):.0f}..{max(highs):.0f} ns "
          f"(period {min(per):.0f}..{max(per):.0f} ns)")
    assert abs(f_car - 38000) < 40 and all(abs(h - TICK_NS) <= period for h in highs)
    bursts = pf.carrier_bursts(edges, 40000)
    rs = []
    for (a, b, n), nxt in zip(bursts, bursts[1:] + [None]):
        rs.append((0, a, b - a + 2 * TICK_NS))      # a burst lasts to the end of its last period
        if nxt:
            rs.append((1, a + rs[-1][2], nxt[0] - a - rs[-1][2]))
    codes = pf.nec_decode_envelope(rs, tol=0.02)
    assert [k for _, k, _ in codes] == ["repeat" if m == "repeat" else "frame" for _, _, m in CODES]
    starts = [t for t, _, _ in codes]
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    print(f"code-to-code period {[round(g / 1e6, 3) for g in gaps]} ms (192 u = 12288 ticks = "
          f"{12288 * TICK_NS / 1e6:.3f} ms)")
    assert all(abs(g - 12288 * TICK_NS) <= 2 * period for g in gaps)
    n_lead = [n for _, _, n in bursts if n > 300]
    n_bit = {n for _, _, n in bursts if n < 30}
    assert set(n_lead) == {341} and n_bit == {21}, (set(n_lead), n_bit)


@pytest.mark.parametrize("scale,stretch", [(1.0, 0), (0.9, 200e3), (1.1, -200e3), (0.9, -200e3), (1.1, 200e3)])
def test_nec_rx_tolerance(scale, stretch):
    """nec_rx against an independent remote model (demodulated, active low):
    +-10 % timing and a receiver module lengthening bursts by up to 200 us
    (or shortening them). Random codes, back to back at the remote's period."""
    f_clk = F_SLOW
    period = 1e9 / f_clk
    mh = setup(f_clk, tx=False)
    rng = random.Random(int(scale * 1000 + stretch))
    codes = [(rng.getrandbits(8), rng.getrandbits(8), "standard") for _ in range(3)]
    codes += [(0, 0, "repeat"), (rng.getrandbits(16), rng.getrandbits(8), "extended"), (0, 0, "repeat")]
    recs = []
    for a, c, m in codes:
        env = pf.nec_envelope(a, c, m, scale, stretch)
        total = sum(d for _, d in env)
        env.append((1, 108e6 * scale - total))
        for lvl, d in env:
            mh.ui = lvl
            mh.step(int(d / period))
            for w in mh.pop_all(1):
                recs.append(pf.nec_rx_record(w))
    assert recs == want_records(codes), recs


def test_nec_rx_rejects_noise():
    """Short glitches, a truncated frame and a stuck-low line: no false codes,
    the truncated frame is reported as an error, and decoding resumes."""
    f_clk = F_SLOW
    period = 1e9 / f_clk
    mh = setup(f_clk, tx=False)
    recs = []

    def drive(seq):
        for lvl, d in seq:
            mh.ui = lvl
            mh.step(int(d / period))
            for w in mh.pop_all(1):
                recs.append(pf.nec_rx_record(w))
    drive([(0, 300e3), (1, 2e6), (0, 3e6), (1, 10e6)])                 # glitch, short "leader"
    env = pf.nec_envelope(0x10, 0x20)
    drive(env[:30] + [(1, 20e6)])                     # truncated: 13 bits and a space that never ends
    drive([(0, 30e6), (1, 5e6)])                                        # stuck low 30 ms
    drive(pf.nec_envelope(0x10, 0x20) + [(1, 50e6)])
    assert [r["kind"] for r in recs] == ["error", "frame"], recs
    assert recs[0]["bits"] == 13 and recs[1]["address"] == 0xEF10 and recs[1]["command"] == 0x20


def test_nec_tx_50mhz():
    """A repeat code at 50 MHz (CLKDIV 438.6): carrier 38 kHz to 1 ppm of the
    divider, 1/3 duty to the clock, 16 u + 4 u + 1 u."""
    f_clk = 50e6
    period = 1e9 / f_clk
    mh = setup(f_clk, rx=False)
    led = pf.PinLog(mh, pf.pin_probe(LED), period)
    led.push(0, pf.nec_word(0, 0, "repeat"))
    led.step(int(12.5e-3 * f_clk))
    edges = led.edges()
    rises = [t for t, v in edges if v == 1]
    per = [b - a for a, b in zip(rises, rises[1:]) if b - a < 40000]
    highs = [b[0] - a[0] for a, b in zip(edges, edges[1:]) if a[1] == 1]
    f_car = 1e9 / (sum(per) / len(per))
    bursts = pf.carrier_bursts(edges, 40000)
    print(f"\nNEC TX at 50 MHz: carrier {f_car:.2f} Hz (period {min(per):.0f}..{max(per):.0f} ns), "
          f"high {min(highs):.0f}..{max(highs):.0f} ns, bursts {[(n, round((b - a) / 1e3, 2)) for a, b, n in bursts]} "
          f"(periods, us first rise to last fall), leader to stop {(bursts[1][0] - bursts[0][0]) / 1e6:.4f} ms")
    assert abs(f_car - 38000) < 1 and max(per) - min(per) <= 2 * period
    assert all(abs(h - 1e9 / 114e3) <= period for h in highs)
    assert [n for _, _, n in bursts] == [341, 21]
    assert abs(bursts[1][0] - bursts[0][0] - 1280 * 1e9 / 114e3) <= 2 * period


@pytest.mark.skipif(sigrok.cli() is None, reason=sigrok.INSTALL_HINT)
def test_sigrok_nec():
    """sigrok's ir_nec on the LED's raw carrier (cd_freq = 38 kHz, active high)
    and on the receiver module's output (active low): the standard frames and
    repeat codes, as nec_rx decoded them."""
    f_clk = F_SLOW
    period = 1e9 / f_clk
    mh = setup(f_clk)
    tsop = pf.IrReceiverModule()
    log = pf.PinLog(mh, lambda m: ((m.chip.pin_out >> LED) & 1) | (m.ui << 1), period)
    codes = [(0x00, 0x45, "standard"), (0, 0, "repeat"), (0xA5, 0x5A, "standard"), (0x3C, 0xC3, "standard")]
    for a, c, m in codes:
        mh.push(0, pf.nec_word(a, c, m))
    recs = []
    for _ in range(int(len(codes) * 0.110 * f_clk)):
        mh.ui = tsop.step(mh.chip.cycle * period, (mh.chip.pin_out >> LED) & 1)
        log.step()
        recs += [pf.nec_rx_record(w) for w in mh.pop_all(1)]
    assert recs == want_records(codes)
    tr = sigrok.Trace()
    pf.to_trace(tr, pf.bit_edges(log.log, 0), "led")
    pf.to_trace(tr, pf.bit_edges(log.log, 1), "rx")
    tr.end(round(log.now() * 1000))
    want = [("repeat", None, None) if m == "repeat" else ("frame", a, c) for a, c, m in codes]
    raw, w1 = sigrok.ir_nec(tr, "led", "active-high", 38000, res_ps=100_000)
    dem, w2 = sigrok.ir_nec(tr, "rx", "active-low", res_ps=100_000)
    print(f"\nsigrok ir_nec, raw carrier: {[(k, a, c) for _, k, a, c in raw]} {w1}"
          f"\nsigrok ir_nec, receiver module output: {[(k, a, c) for _, k, a, c in dem]} {w2}")
    assert [(k, a, c) for _, k, a, c in raw] == want and not w1
    assert [(k, a, c) for _, k, a, c in dem] == want and not w2
