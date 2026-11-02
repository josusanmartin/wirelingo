"""10BASE-T cut-through match (firmware/eth10_match.pasm) on the model: a frame
from an external sender with 802.3's edge jitter, and the reaction measured
from the wire: the field's last mid-bit transition at the pad to the HIT pin.

Run with -s to see the measured numbers."""
import pathlib
import random
import struct
import zlib

import pytest

from pe import asm, host, sim
from pe.modelhost import ModelHost

FW = pathlib.Path(__file__).resolve().parents[2] / "firmware"
RXP = 16                          # ui0
FIELD_LAST_BIT = 64 + 8 * 16 - 1  # preamble + SFD, 14 header bytes, the 2-byte field


def wire_frame(field, ethertype=0x88B5, payload=46, preamble=62):
    body = bytes(6 * [0xFF]) + bytes.fromhex("020000c0ffee") + struct.pack(">H", ethertype)
    body += struct.pack("<H", field) + bytes(i & 255 for i in range(payload - 2))
    fr = body + struct.pack("<I", zlib.crc32(body))
    return ([1, 0] * 31)[-preamble:] + [1, 1] + [(b >> i) & 1 for b in fr for i in range(8)]


def edges_of(bits, t, jitter, rng, ppm=100):
    """(edges, time of each bit's mid-bit transition, end time), 802.3 polarity."""
    half = 50e-9 * (1 + ppm * 1e-6)
    edges, mids, prev = [], [], 0
    for x in bits:
        for i, lv in enumerate((1 - x, x)):
            te = t + rng.uniform(-jitter, jitter)
            if lv != prev:
                edges.append((te, lv))
            if i == 1:
                mids.append(te)
            prev = lv
            t += half
    if prev:
        edges.append((t + 2 * half * 2, 0))     # TP_IDL: 200 ns high, then silence
    return edges, mids, t + 4 * half


def run(frames, threshold, clock_mhz=50, jitter=0.0, seed=1, gap=20e-6, pop=True):
    """Feed frames (bit lists). Returns (reaction per frame in clocks, or None;
    RX FIFO words; per frame, seconds from the rise to the frame's last edge, or
    None; per frame, seconds from the frame's last edge to the fall, or None).
    With pop=False the host pops only after the last frame."""
    rng = random.Random(seed)
    clk = 1e-6 / clock_mhz
    mh = ModelHost()
    prog = asm.assemble((FW / "eth10_match.pasm").read_text())
    mh.apply(host.load_program(0, prog, 0, clkdiv=1 << 8))
    mh.write(sim.A_LINEDIV, round(100 / (1000 / clock_mhz)) << 16)
    mh.write(sim.A_LINEPINS, RXP << 8)
    mh.write(sim.A_LINECFG, 1 | (2 << 1) | (1 << 14))
    mh.push(0, threshold)
    mh.start(1)
    mh.step(10)
    t0 = (mh.chip.cycle + 10 + rng.random()) * clk
    t = t0
    all_edges, marks, ends = [], [], []
    for bits in frames:
        edges, mids, end = edges_of(bits, t, jitter, rng)
        all_edges += edges
        marks.append(mids[FIELD_LAST_BIT])
        ends.append(edges[-1][0])
        t = end + gap

    def level(time):
        lv = 0
        for te, v in all_edges:
            if te > time:
                break
            lv = v
        return lv
    step0 = mh.chip.step

    def step(ui_in=0, *a, **kw):
        c = mh.chip.cycle
        step0(level(c * clk), *a, ui_mid=level((c - 0.5) * clk), **kw)
    mh.chip.step = step
    rises, falls, got, prev = [], [], [], 0
    while mh.chip.cycle * clk < t:
        mh.step()
        uo = mh.chip.outputs()[0] & 1
        if uo and not prev:
            rises.append(mh.chip.cycle * clk)
        if prev and not uo:
            falls.append(mh.chip.cycle * clk)
        prev = uo
        if pop:
            got += mh.pop_all(0)
    got += mh.pop_all(0)
    out, lead, tail = [], [], []
    for m, e in zip(marks, ends):
        r = [x for x in rises if m < x < m + 5e-6]
        f = [x for x in falls if x > m]
        out.append((r[0] - m) / clk if r else None)
        lead.append(e - r[0] if r else None)
        tail.append(f[0] - e if r and f else None)
    return out, got, lead, tail


def test_hit_and_miss():
    r, got, *_ = run([wire_frame(1000), wire_frame(999), wire_frame(1001)], 1000)
    assert r[0] is not None and r[1] is None and r[2] is not None
    assert got == [1000, 1001]


def test_other_ethertype_never_hits():
    r, got, *_ = run([wire_frame(5000, ethertype=0x0800)], 1000)
    assert r == [None] and got == []


@pytest.mark.parametrize("clock_mhz", [40, 50])
def test_reaction_is_10_to_11_clocks(clock_mhz):
    """One clock of sampling phase on 60 frames, no jitter: docs/eth10-cut-through.md."""
    r = []
    for seed in range(15):
        x, *_ = run([wire_frame(2000)] * 4, 1000, clock_mhz, seed=seed)
        r += x
    assert all(x is not None for x in r)
    ns = [x * 1000 / clock_mhz for x in r]
    print(f"{clock_mhz} MHz, no jitter: {min(r):.3f}-{max(r):.3f} clocks, {min(ns):.0f}-{max(ns):.0f} ns over {len(r)} frames")
    assert all(10 <= x <= 11 for x in r), (min(r), max(r))


def test_reaction_with_8023_jitter():
    r, got = [], []
    for seed in range(6):
        x, g, *_ = run([wire_frame(2000 + seed)] * 4, 1000, 50, jitter=13.5e-9, seed=seed)
        r += x
        got += g
    assert all(x is not None for x in r), r
    assert len(got) == len(r)
    ns = [x * 20 for x in r]
    print(f"50 MHz, +-13.5 ns: {min(ns):.0f}-{max(ns):.0f} ns over {len(r)} frames")
    assert all(10 - 0.675 <= x <= 11 + 0.675 for x in r), (min(r), max(r))


@pytest.mark.parametrize("clock_mhz", [40, 50])
def test_rise_to_last_edge_and_hit_is_a_pulse(clock_mhz):
    """44 payload bytes and the FCS follow the field: 384 bits at 100 ns (+100 ppm).
    A frame ending in 1 then has this test sender's TP_IDL edge, 250 ns after that
    bit's mid-bit. The rise is 10-11 clocks after the field, so that much earlier."""
    frames = [wire_frame(2000 + i) for i in range(8)]
    assert {f[-1] for f in frames} == {0, 1}
    r, _, lead, tail = run(frames, 1000, clock_mhz)
    bit, clk = 100e-9 * (1 + 100e-6), 1e-6 / clock_mhz
    for f, x in zip(frames, lead):
        after = 384 * bit + (2.5 * bit if f[-1] else 0)      # last field mid-bit to last edge
        assert after - 11 * clk - 1e-12 <= x <= after - 10 * clk + 1e-12
    print(f"{clock_mhz} MHz: rise to last edge {min(lead) * 1e6:.2f}-{max(lead) * 1e6:.2f} us")
    assert all(x is not None and 0 < x < 1e-6 for x in tail), tail


def test_threshold_wraps_at_16_bits():
    assert run([wire_frame(5), wire_frame(4)], 0x10005)[1] == [5]


def test_preamble_shorter_than_31_bits_is_missed():
    assert run([wire_frame(2000, preamble=31)], 1000)[1] == [2000]
    assert run([wire_frame(2000, preamble=30)], 1000)[1] == []


def test_full_fifo_loses_records_but_hit_still_rises():
    r, got, *_ = run([wire_frame(2000 + i) for i in range(12)], 1000, pop=False)
    assert all(x is not None for x in r)
    assert got == [2000 + i for i in range(8)]
