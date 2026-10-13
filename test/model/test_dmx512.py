"""DMX512 transmitter (firmware/dmx512_tx.pasm, post-freeze) on the cycle model.

Judges: pe.postfreeze.DmxReceiver (pins only: E1.11 receiver rules, the bit
grid) and sigrok's dmx512 decoder. Runs at 50 MHz (CLKDIV 50, 1 tick = 1 us)
for the timing, and with the model clocked at 1 MHz and CLKDIV 1 for long
packets: the program counts ticks, so the waveform is the same tick for tick
(test_dmx512_clock_scaling checks it), at 1/50 of the simulation cost.
"""
import pathlib
import random

import pytest

from pe import asm, host, postfreeze as pf, sigrok
from pe.modelhost import ModelHost

FW = pathlib.Path(__file__).resolve().parents[2] / "firmware"
TX = 8  # uo0


def setup(div=50, mtbs=0):
    mh = ModelHost()
    prog = asm.assemble((FW / "dmx512_tx.pasm").read_text(), {"TX": TX, "MTBS": mtbs})
    mh.apply(host.load_program(0, prog, out_pins=(TX, 1), clkdiv=div << 8))
    mh.write(0x809, 1 << TX)            # PIN_OUT: mark (high) before the first BREAK
    mh.start(1)
    return mh, prog


def send(mh, packets, period_ns, host_delay=None, max_cycles=10_000_000):
    """Feed packets through the TX FIFO as a host would (pushing whenever there
    is room, host_delay(rng) cycles between pushes), record the TX pin."""
    words = []
    for p in packets:
        words += pf.dmx_words(p)
    log = pf.PinLog(mh, pf.pin_probe(TX), period_ns)
    rng = random.Random(1)
    wait = 0
    while words or mh.tx_level(0) or mh.chip.cores[0].pc not in (0, 1):
        if words and mh.tx_level(0) < 8 and wait <= 0:
            log.push(0, words.pop(0))
            wait = host_delay(rng) if host_delay else 0
            continue
        log.step()
        wait -= 1
        assert mh.chip.cycle < max_cycles
    log.step(int(1300e3 / period_ns))
    return log.edges(), log.now()


def check_packets(got, want, exact=True):
    assert len(got) >= len(want), (len(got), len(want))
    for p, slots in zip(got, want):
        assert bytes(p["slots"]) == bytes(slots), (bytes(p["slots"])[:16], bytes(slots)[:16])
        assert not p["errors"], p["errors"]
        assert 92000 <= p["brk"] <= 1e9 and 12000 <= p["mab"] < 1e9, (p["brk"], p["mab"])
        if exact:
            assert p["brk"] == 176000 and p["mab"] == 12000, (p["brk"], p["mab"])
            assert max(abs(e) for e in p["grid_err"]) == 0, max(p["grid_err"])


def test_dmx512_50mhz_timing():
    """At 50 MHz: BREAK 176 us, MAB 12 us, every bit edge on the 4 us grid to
    the clock, slots back to back, and a 3-slot packet padded to 1204 us."""
    mh, _ = setup(50)
    pkts = [bytes([0]) + bytes(range(1, 24)), bytes([0, 0xFF, 0x80])]
    edges, t_end = send(mh, pkts + [bytes([0x55])], 20.0)
    got = pf.DmxReceiver().decode(edges, t_end)
    check_packets(got, pkts + [bytes([0x55])])
    assert all(g == 0 for g in got[0]["mark_gaps"]), got[0]["mark_gaps"][:4]
    assert got[0]["break_to_break"] == (194 + 44 * 24) * 1000
    assert got[1]["break_to_break"] == 1204 * 1000


def test_dmx512_clock_scaling():
    """The same packets at 1 MHz / CLKDIV 1 give the same waveform in ticks as
    50 MHz / CLKDIV 50 (edges 50 clocks apart become 1 clock apart)."""
    pkts = [bytes([0, 1, 2, 3, 250]), bytes([0xCC] * 9)]
    mh, _ = setup(50)
    e50, _ = send(mh, pkts, 20.0)
    mh, _ = setup(1)
    e1, _ = send(mh, pkts, 1000.0)
    rel = lambda e: [(round(t - e[1][0]), v) for t, v in e[1:]]
    assert rel(e50) == rel(e1)


def test_dmx512_packet_period():
    """BREAK to BREAK >= 1204 us for every packet length: = 1204 us (padded)
    up to 22 slots, 194 + 44 n us from 23 on. The static checker cannot show
    this (it rests on the r1/r2 relation), so it is measured for n = 1..40,
    512 and 513."""
    mh, _ = setup(1)
    lens = list(range(1, 41)) + [512, 513]
    rng = random.Random(7)
    pkts = [bytes(rng.getrandbits(8) for _ in range(n)) for n in lens] + [b"\0"]
    edges, t_end = send(mh, pkts, 1000.0)
    got = pf.DmxReceiver().decode(edges, t_end)
    check_packets(got, pkts)
    for n, p in zip(lens, got):
        want = 1204 if n <= 22 else 194 + 44 * n
        assert p["break_to_break"] == want * 1000, (n, p["break_to_break"])


def test_dmx512_late_host():
    """A host that pushes slowly: the line marks between slots (and after the
    MAB) instead of breaking a slot; every slot still decodes."""
    mh, _ = setup(1)
    rng = random.Random(3)
    pkts = [bytes(rng.getrandbits(8) for _ in range(n)) for n in (9, 30)]
    edges, t_end = send(mh, pkts, 1000.0, host_delay=lambda r: r.randrange(0, 400))
    got = pf.DmxReceiver().decode(edges, t_end)
    check_packets(got, pkts, exact=False)
    assert any(g > 0 for p in got for g in p["mark_gaps"])


@pytest.mark.skipif(sigrok.cli() is None, reason=sigrok.INSTALL_HINT)
def test_sigrok_dmx512_universe():
    """sigrok decodes three full packets (START code + 512 channels), with
    the break and MAB lengths it measured. (sigrok 0.5.3's decoder looks for a
    BREAK only after 512 channels, so it cannot frame a shorter packet; our
    receiver checks those above.)
    With MTBS = 1 us: libsigrokdecode 0.5.3's dmx512 decoder reads the second
    stop bit at the sample where the next start bit already begins, so it
    misframes back-to-back slots (MTBS = 0, which E1.11 allows and our own
    receiver decodes above)."""
    mh, _ = setup(1, mtbs=1)
    rng = random.Random(5)
    full = bytes([0]) + bytes(rng.getrandbits(8) for _ in range(512))
    alt = bytes([0x17]) + bytes(rng.getrandbits(8) for _ in range(512))
    full2 = bytes([0]) + bytes(range(256)) * 2
    edges, t_end = send(mh, [full, alt, full2], 1000.0)
    got = pf.DmxReceiver().decode(edges, t_end)
    check_packets(got, [full, alt, full2])
    assert all(g == 1000 for p in got for g in p["mark_gaps"])
    tr = pf.to_trace(sigrok.Trace(), edges, "dmx", t_end)
    pk, err = sigrok.dmx512(tr, "dmx", res_ps=100_000)
    print(f"\nsigrok dmx512: {len(pk)} packets, slots {[len(p['slots']) for p in pk]}, "
          f"break {[p['brk_ns'] / 1000 for p in pk]} us, MAB {[p['mab_ns'] / 1000 for p in pk]} us, "
          f"errors {err}")
    assert [p["slots"] for p in pk] == [full, alt, full2], [len(p["slots"]) for p in pk]
    assert not err
    assert all(abs(p["brk_ns"] - 176000) <= 200 and abs(p["mab_ns"] - 12000) <= 200 for p in pk)
