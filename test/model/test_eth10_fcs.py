"""The 10BASE-T FCS in the engine (v0.3 ISA: the 32-bit CRC tap, CMPW).

Receive (firmware/eth10_rx_fcs.pasm, ported from the v3 study): the sender is
external (random phase, jitter, frequency offset, as test_eth10) or core 1;
frames are good or have one bit flipped. Transmit (firmware/eth10_tx_fcs.pasm,
new): the host gives DA .. payload and the core appends the FCS; the wire is
decoded as in test_eth10 and must equal eth10_tx's with the host's FCS. Then
core 1 sends to core 0 with neither FCS computed by the host."""
import pathlib
import random
import zlib

import pytest

from pe import asm, host, isa, sim
from pe.modelhost import ModelHost
import test_eth10
from test_eth10 import F_GO, frame, wire_bits, words

FW = pathlib.Path(__file__).resolve().parents[2] / "firmware"
RX_CRC = isa.crcctrl(0x04C11DB7, 32, in_tap=1)     # (CRCCTRL, POLY_HI)
TX_CRC = isa.crcctrl(0x04C11DB7, 32, out_tap=1)


def crc_writes(core, ctrl):
    return [(sim.core_reg(core, sim.C_CRCCTRL), ctrl[0]), (sim.core_reg(core, sim.C_POLY_HI), ctrl[1])]


def test_constants_in_the_headers():
    assert RX_CRC == (0x009F1DB7, 0x04C1) and TX_CRC == (0x00AF1DB7, 0x04C1)
    assert "0x009F1DB7" in (FW / "eth10_rx_fcs.pasm").read_text()
    assert "0x00AF1DB7" in (FW / "eth10_tx_fcs.pasm").read_text()


def setup(mh, clk_per_bit, rxp):
    prog = asm.assemble((FW / "eth10_rx_fcs.pasm").read_text())
    mh.apply(host.load_program(0, prog, 0, clkdiv=1 << 8))
    mh.apply(crc_writes(0, RX_CRC))
    mh.write(sim.A_LINEDIV, clk_per_bit << 16)
    mh.write(sim.A_LINEPINS, rxp << 8)
    mh.write(sim.A_LINECFG, 1 | (2 << 1) | (1 << 14))
    return prog


def records(words_):
    """eth10_rx_fcs records -> [(frame bytes, fcs_good)]."""
    out, data = [], []
    for w in words_:
        if w & 0xFF:
            n = w >> 16
            bits = []
            for d in data:
                bits += [(d >> (8 + i)) & 1 for i in range(24)]
            full, rem = divmod(n, 24)
            bits = bits[:24 * full]
            if rem:
                bits += [(data[full] >> (32 - rem + i)) & 1 for i in range(rem)]
            fr = bytes(sum(b << j for j, b in enumerate(bits[8 * k:8 * k + 8])) for k in range(len(bits) // 8))
            out.append((fr, bool(w & 1), n))
            data = []
        else:
            data.append(w)
    return out


def receive(seed, jitter, flip=None, ppm=100, nbytes=60, extreme=False):
    rng = random.Random(seed)
    mh = ModelHost()
    setup(mh, 5, 16)                               # RD on ui0, 50 MHz
    mh.start(1)
    fr = frame(nbytes, seed=seed)
    bits = wire_bits(fr)
    if flip is not None:
        bits[64 + flip] ^= 1                       # a bit error after the SFD
    line = [b for x in bits for b in (1 - x, x)]
    clk, half = 20e-9, 50e-9 * (1 + ppm * 1e-6)
    t, edges, prev = rng.random() * clk + 200e-9, [], 0
    for lv in line:
        if lv != prev:
            j = (jitter if rng.random() < 0.5 else -jitter) if extreme else rng.uniform(-jitter, jitter)
            edges.append((t + j, lv))
        prev = lv
        t += half
    if prev:
        edges.append((t + 4 * half, 0))

    def level(time):
        lv = 0
        for te, v in edges:
            if te > time:
                break
            lv = v
        return lv
    step0 = mh.chip.step

    def step(ui_in=0, *a, **kw):
        c = mh.chip.cycle
        step0(level(c * clk), *a, ui_mid=level((c - 0.5) * clk), **kw)
    mh.chip.step = step
    got, maxq = [], 0
    while mh.chip.cycle * clk < t + 2e-6:
        mh.step()
        maxq = max(maxq, len(mh.chip.line.rq))
        got += mh.pop_all(0)
    assert not mh.chip.line.status() >> 19 & 1, "line unit overflow"
    print(f"seed {seed}: most bits queued in the line unit {maxq} (of 32)")
    return records(got), fr


@pytest.mark.parametrize("seed", range(3))
def test_good_frames_pass_with_jitter(seed):
    recs, fr = receive(seed, 13.5e-9)
    assert recs == [(fr, True, 8 * len(fr))]


@pytest.mark.parametrize("seed", range(2))
def test_good_frames_extreme_jitter(seed):
    recs, fr = receive(seed + 10, 13.5e-9, extreme=True, ppm=-200)
    assert recs == [(fr, True, 8 * len(fr))]


@pytest.mark.parametrize("flip", [0, 100, 300, 8 * 64 - 1])
def test_a_flipped_bit_fails_the_fcs(flip):
    recs, fr = receive(3, 5e-9, flip=flip)
    assert len(recs) == 1 and recs[0][1] is False and recs[0][2] == 8 * len(fr)


def test_long_frame():
    recs, fr = receive(4, 10e-9, nbytes=1500)
    assert recs == [(fr, True, 8 * len(fr))]


@pytest.mark.parametrize("fcs_in_engine", [False, True])
def test_core1_tx_to_core0_rx_with_link_pulses(fcs_in_engine):
    """eth10_tx (the host's FCS) or eth10_tx_fcs (the engine's) on core 1, virtual
    pins 24/25, link pulses between frames, to eth10_rx_fcs on core 0 at 50 MHz:
    the link pulses leave no records, every frame checks."""
    mh = ModelHost()
    name = "eth10_tx_fcs" if fcs_in_engine else "eth10_tx"
    tx = asm.assemble((FW / f"{name}.pasm").read_text(), {"F_GO": F_GO})
    mh.apply(host.pin_owner_writes({24: 1, 25: 1}))
    mh.apply(host.load_program(1, tx, 256, side_pins=24, clkdiv=320))
    if fcs_in_engine:
        mh.apply(crc_writes(1, TX_CRC))
    setup(mh, 5, 24)
    mh.start(0b11)
    got, sent = [], [frame(46), frame(100, seed=5), frame(47, seed=6)]
    for fr in sent:
        body = fr[:-4] if fcs_in_engine else fr
        w = words(body)
        mh.write(sim.core_reg(1, sim.C_R0), 8 * len(body) - 1)
        for x in w[:16]:
            mh.push(1, x)
        mh.step(16)
        mh.write(sim.A_FSET, 1 << F_GO)
        for x in w[16:]:
            while mh.tx_level(1) >= 8:
                mh.step()
                got += mh.pop_all(0)
            mh.push(1, x)
        while mh.chip.flags >> F_GO & 1:
            mh.step()
            got += mh.pop_all(0)
        for _ in range(400):
            mh.step()
            got += mh.pop_all(0)
    assert [(r[0], r[1]) for r in records(got)] == [(fr, True) for fr in sent]


# ---------------------------------------------------------------- transmit with the FCS


class FcsLine(test_eth10.Line):
    """test_eth10's line, with eth10_tx_fcs and its CRC settings on core 0."""

    def __init__(self):
        self.mh = ModelHost()
        prog = asm.assemble((FW / "eth10_tx_fcs.pasm").read_text(), {"F_GO": F_GO})
        self.mh.apply(host.load_program(0, prog, side_pins=test_eth10.TDP, clkdiv=1 << 8))
        self.mh.apply(crc_writes(0, TX_CRC))
        self.mh.start(1)
        self.trace = []


def test_tx_fcs_min_frame_from_fifo():
    ln = FcsLine()
    ln.step(100)
    fr = frame(46)                                   # 64 bytes with the FCS
    test_eth10.check(ln.trace, ln.send(fr[:-4]), fr)


def test_tx_fcs_streamed_and_back_to_back():
    """Long frames streamed at SPI speed, part-used last words (60 + 1, 2, 3 bytes),
    and frames back to back: the FCS always comes from the engine."""
    ln = FcsLine()
    ln.step(100)
    for n, seed in ((110, 1), (51, 2), (52, 3), (53, 4), (46, 5)):
        fr = frame(n, seed=seed)
        test_eth10.check(ln.trace, ln.send(fr[:-4], stream=len(fr) > 64), fr)


def test_tx_fcs_max_frame_streamed():
    ln = FcsLine()
    ln.step(100)
    fr = frame(1500)
    assert len(fr) == 1518
    test_eth10.check(ln.trace, ln.send(fr[:-4], stream=True), fr)


def test_tx_fcs_same_wire_as_the_host_fcs():
    """The same frame from eth10_tx (FCS from the host) and eth10_tx_fcs: the same
    bits on the wire and the same TP_IDL; only the start latency differs."""
    fr = frame(41, seed=9)                       # 59 bytes: preloaded, a part-used word
    a, b = test_eth10.Line(), FcsLine()
    for ln in (a, b):
        ln.step(100)
    da = test_eth10.decode(a.trace, a.send(fr))
    db = test_eth10.decode(b.trace, b.send(fr[:-4]))
    assert da[:2] == db[:2] and da[0] == wire_bits(fr)
    assert zlib.crc32(fr[:-4]).to_bytes(4, "little") == fr[-4:]


def test_tx_fcs_word_count():
    """Report the sizes (docs/isa.md's 256-word bank): both fit with room."""
    rx = asm.assemble((FW / "eth10_rx_fcs.pasm").read_text())
    tx = asm.assemble((FW / "eth10_tx_fcs.pasm").read_text(), {"F_GO": F_GO})
    base = asm.assemble((FW / "eth10_tx.pasm").read_text(), {"F_GO": F_GO})
    print(f"eth10_rx_fcs {len(rx.words)} words, eth10_tx_fcs {len(tx.words)} (eth10_tx {len(base.words)})")
    assert len(rx.words) == 27 and len(tx.words) == 53


def _next_link_pulse_ms(ln, since, limit_ms=30):
    """Step the line until the next link pulse (idle, then H) after trace index
    `since`; its time from `since` in ms (25 ns ticks: 40 MHz, CLKDIV 1), or None."""
    ticks_per_ms = 40_000
    i = since
    while True:
        while i + 1 < len(ln.trace):
            if ln.trace[i] == test_eth10.IDLE and ln.trace[i + 1] == test_eth10.H:
                return (i + 1 - since) / ticks_per_ms
            i += 1
        if len(ln.trace) - since > limit_ms * ticks_per_ms:
            return None
        ln.step(256)


@pytest.mark.parametrize("fcs_in_engine", [False, True])
def test_link_pulse_within_8_to_24_ms_after_a_frame(fcs_in_engine):
    """802.3 clause 14: while idle, normal link pulses every 16 +- 8 ms. After a
    frame the first one must come 8..24 ms after its end. eth10_tx_fcs reuses
    r1, the pulse count, for the FCS bits and reloads it in TP_IDL (without the
    reload the next pulse came 104.86 ms later). eth10_tx keeps its count
    across the frame, so it is checked only against 24 ms."""
    ln = FcsLine() if fcs_in_engine else test_eth10.Line()
    ln.step(100)
    fr = frame(46)
    start = ln.send(fr[:-4] if fcs_in_engine else fr)
    _, _, end = test_eth10.decode(ln.trace, start)       # the end of TP_IDL
    ms = _next_link_pulse_ms(ln, end)
    assert ms is not None, "no link pulse within 30 ms of the frame"
    assert ms <= 24 and (ms >= 8 or not fcs_in_engine), ms
    print(f"{'eth10_tx_fcs' if fcs_in_engine else 'eth10_tx'}: frame end -> link pulse {ms:.2f} ms")
