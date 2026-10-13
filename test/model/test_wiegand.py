"""Wiegand transmitter and receiver (firmware/wiegand_tx.pasm,
wiegand_rx.pasm, post-freeze) on the cycle model.

Judges: pe.postfreeze.wiegand_decode (pins only), the parity rule computed
independently (pe.postfreeze.wiegand_bits), an independent card-reader model
driving the lines for the receiver, and sigrok's wiegand decoder.

The 1 us tick runs at 1 MHz (CLKDIV 1) for the long runs: tick for tick the
waveform of 50 MHz / CLKDIV 50; test_wiegand_50mhz checks that at 50 MHz."""
import pathlib
import random

import pytest

from pe import asm, host, postfreeze as pf, sigrok, sim
from pe.modelhost import ModelHost

FW = pathlib.Path(__file__).resolve().parents[2] / "firmware"
D0, D1 = 0, 1              # uio0, uio1: open drain, pulled up
TX_DEFS = {"D0": D0, "D1": D1, "PW": 50, "IV": 1000, "FG": 12000}
RX_DEFS = {"D0": D0, "FTO": 10000, "PMAX": 500}
PARITY_CRC = 1 | (0 << 16)            # POLY 1, width 1


def setup(f_clk=1e6, tx_defs=None, rx_defs=None, tx=True, rx=True):
    mh = ModelHost()
    div = host.clkdiv_for(f_clk, 1e6)
    if tx:
        prog = asm.assemble((FW / "wiegand_tx.pasm").read_text(), tx_defs or TX_DEFS)
        mh.apply(host.load_program(0, prog, clkdiv=div))
        mh.write(sim.core_reg(0, sim.C_CRCCTRL), PARITY_CRC | (1 << 21))     # OUT tap
    if rx:
        prog = asm.assemble((FW / "wiegand_rx.pasm").read_text(), rx_defs or RX_DEFS)
        mh.apply(host.load_program(1, prog, in_pins=D0, clkdiv=div))
        mh.write(sim.core_reg(1, sim.C_CRCCTRL), PARITY_CRC | (1 << 20))     # IN tap
    mh.write(sim.A_POUT, (1 << D0) | (1 << D1))
    mh.write(sim.A_OD, (1 << D0) | (1 << D1))
    mh.pull_up = (1 << D0) | (1 << D1)
    mh.start((1 if tx else 0) | (2 if rx else 0))
    return mh


FRAMES = [(24, pf.h10301(17, 12345), False), (32, 0xDEADBEEF, False), (24, pf.h10301(255, 0), False),
          (24, 0, False), (32, 0xFFFFFFFF, False), (8, 0xA5, True), (30, 0x2AAAAAAA, False)]


def want_rx(frames):
    out = []
    for n, d, raw in frames:
        bits = pf.wiegand_bits(n, d, raw)
        out.append({"bits": bits, "valid": len(bits) in (26, 34) and not raw})
    return out


def test_wiegand_tx_rx_loopback():
    """wiegand_tx (core 0) -> the open-drain lines -> wiegand_rx (core 1):
    26-bit, 34-bit, raw and odd sizes; the receiver's parity verdicts; the
    lines judged by the pins-only decoder."""
    mh = setup()
    log = pf.PinLog(mh, lambda m: m.bus_uio() & 3, 1000.0)
    words = []
    for n, d, raw in FRAMES:
        for w in pf.wiegand_words(n, d, raw):
            log.push(0, w)
        while mh.tx_level(0) >= 7:          # the host keeps draining the receiver meanwhile
            log.step(1000)
            words += log.pop_all(1)
    total = sum((len(pf.wiegand_bits(n, d, r)) * 1000 + 14000) for n, d, r in FRAMES)
    for _ in range(total // 1000 + 20):
        log.step(1000)
        words += log.pop_all(1)
    got = pf.wiegand_rx_frames(words)
    assert [{"bits": g["bits"], "valid": g["valid"]} for g in got] == want_rx(FRAMES), got
    assert not any(g["error"] for g in got)
    dec = pf.wiegand_decode(log.log, gap_ns=4e6)
    assert [b for _, b, _, _ in dec] == [pf.wiegand_bits(n, d, r) for n, d, r in FRAMES]
    widths = {w for _, _, ws, _ in dec for w in ws}
    ivs = {i for _, _, _, iv in dec for i in iv}
    assert widths == {50000.0} and ivs == {1000000.0}, (widths, ivs)


def test_wiegand_50mhz():
    """At 50 MHz (CLKDIV 50) with IV = 200 us: pulse widths and intervals exact."""
    mh = setup(50e6, tx_defs=dict(TX_DEFS, IV=200, FG=2000), rx=False)
    log = pf.PinLog(mh, lambda m: m.bus_uio() & 3, 20.0)
    for w in pf.wiegand_words(24, pf.h10301(1, 2)):
        log.push(0, w)
    log.step(26 * 200 * 50 + 5000)
    [(t, bits, widths, ivs)] = pf.wiegand_decode(log.log, gap_ns=1e6)
    print(f"\nWiegand at 50 MHz: {bits}, pulses {sorted(set(widths))} ns, intervals {sorted(set(ivs))} ns")
    assert bits == pf.wiegand_bits(24, pf.h10301(1, 2))
    assert set(widths) == {50000.0} and set(ivs) == {200000.0}


class Reader:
    """An independent card reader driving D0/D1 (open drain) from the
    outside: pulse width, interval, glitches, both lines low."""

    def __init__(self, mh):
        self.mh = mh
        mh.ext_oe = (1 << D0) | (1 << D1)
        mh.uio = 3
        self.words = []

    def hold(self, ns):
        self.mh.step(int(ns / 1000))
        self.words += self.mh.pop_all(1)

    def pulse(self, lines, width):
        self.mh.uio = 3 & ~lines
        self.hold(width)
        self.mh.uio = 3

    def frame(self, bits, pw, iv):
        for b in bits:
            self.pulse(1 << (D1 if b == "1" else D0), pw)
            self.hold(iv - pw)


@pytest.mark.parametrize("pw,iv", [(20e3, 200e3), (50e3, 1e6), (100e3, 2e6), (100e3, 5e6)])
def test_wiegand_rx_reader(pw, iv):
    """wiegand_rx against the reader model across the usual pulse widths and
    intervals (FTO = 10 ms covers up to 5 ms intervals)."""
    mh = setup(tx=False)
    rd = Reader(mh)
    rng = random.Random(int(pw + iv))
    frames = [(24, rng.getrandbits(24), False), (32, rng.getrandbits(32), False), (35, rng.getrandbits(35), True)]
    for n, d, raw in frames:
        bits = pf.wiegand_bits(n, d, raw) if n <= 32 else format(d, f"0{n}b")
        rd.frame(bits, pw, iv)
        rd.hold(12e6)
    got = pf.wiegand_rx_frames(rd.words)
    want = [pf.wiegand_bits(n, d, raw) if n <= 32 else format(d, f"0{n}b") for n, d, raw in frames]
    assert [g["bits"] for g in got] == want
    assert [g["valid"] for g in got] == [True, True, False]


def test_wiegand_rx_noise_and_errors():
    """2 us glitches are ignored; both lines low and a stuck line mark the
    frame invalid; a 26-bit frame with a wrong parity bit is not valid; a
    70-bit frame keeps its first 32 and last 32 bits."""
    mh = setup(tx=False)
    rd = Reader(mh)
    good = pf.wiegand_bits(24, pf.h10301(10, 20))
    # glitches between bits
    for i, b in enumerate(good):
        rd.pulse(1 << (D1 if b == "1" else D0), 50e3)
        rd.hold(500e3)
        rd.pulse(1 << D0, 2e3)
        rd.hold(1450e3)
    rd.hold(12e6)
    bad = good[:-1] + ("0" if good[-1] == "1" else "1")
    rd.frame(bad, 50e3, 2e6)
    rd.hold(12e6)
    rd.frame(good[:5], 50e3, 2e6)
    rd.pulse(3, 50e3)                          # both lines low
    rd.hold(2e6)
    rd.frame(good[5:], 50e3, 2e6)
    rd.hold(12e6)
    long = "".join(random.Random(1).choice("01") for _ in range(70))
    rd.frame(long, 50e3, 1e6)
    rd.hold(12e6)
    got = pf.wiegand_rx_frames(rd.words)
    assert got[0]["bits"] == good and got[0]["valid"] and not got[0]["error"]
    assert got[1]["bits"] == bad and not got[1]["valid"]
    assert got[2]["error"] and not got[2]["valid"]
    assert got[3]["n"] == 70 and got[3]["bits"] == long[:32] + long[-32:]


@pytest.mark.skipif(sigrok.cli() is None, reason=sigrok.INSTALL_HINT)
def test_sigrok_wiegand():
    """sigrok's wiegand decoder on the TX lines (frames end after 4 ms idle)."""
    mh = setup(rx=False)
    log = pf.PinLog(mh, lambda m: m.bus_uio() & 3, 1000.0)
    frames = FRAMES[:3] + FRAMES[5:6]
    for n, d, raw in frames:
        for w in pf.wiegand_words(n, d, raw):
            log.push(0, w)
    log.step(sum(len(pf.wiegand_bits(n, d, r)) * 1000 + 13000 for n, d, r in frames))
    tr = sigrok.Trace()
    pf.to_trace(tr, pf.bit_edges(log.log, 0), "d0")
    pf.to_trace(tr, pf.bit_edges(log.log, 1), "d1")
    tr.end(round(log.now() * 1000))
    got, bad = sigrok.wiegand(tr, "d0", "d1", bitwidth_ms=4, res_ps=1_000_000 // 10)
    print(f"\nsigrok wiegand: {[b for _, b in got]} invalid={bad}")
    assert [b for _, b in got] == [pf.wiegand_bits(n, d, r) for n, d, r in frames] and not bad
