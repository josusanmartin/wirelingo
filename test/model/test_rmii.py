"""RMII at 100 Mbit/s on the cycle model, tapeout configuration (2 cores, 8-deep
FIFOs): firmware/rmii_rx.pasm, rmii_tx.pasm, rmii_match.pasm, against a far end that
sees only the pads and a host that sees only what the SPI port returns. The port is
pe.rmii.QuadPort, the clk side of src/pe_hostport.sv register for register.

Evidence level: model. Run with -s to see the numbers docs/rmii-study.md quotes."""
import random

import pytest

from pe import asm, host, rmii, sim, timing
from pe.rmii import Bench, MIN_GAP, make_frame, parse_rx

FULL, MIN = 1518, 64
MIXED_FILLS = [None, 0x00, 0xFF, 0x55, 0xD5]


def rx_run(frames, ifg=MIN_GAP, lead=4, toggle=0, script=None, tail=400, late=False, prog=None, syncbyp=True, **drain_kw):
    """Frames on the wire, a host draining. Returns (records, leftover dibits, bench)."""
    b = Bench(late=late)
    b.load_rx(prog)
    if not syncbyp:
        b.write(sim.A_SYNCBYP, 0)
    b.start(1)
    b.phy_rx.idle(40)
    for i, f in enumerate(frames):
        if i:
            b.phy_rx.idle(ifg)
        b.phy_rx.frame(f, lead=lead, toggle=toggle)
    b.port.script = script(b) if script else rmii.drain(b.port, **drain_kw)
    b.run_all(tail)
    recs, rest = parse_rx(b.words())
    return recs, rest, b


def assert_delivered(recs, frames):
    assert [r.data for r in recs] == frames
    assert all(r.good and r.flags == 0 for r in recs), [hex(r.flags) for r in recs]


# ---------------------------------------------------------------- RX
@pytest.mark.parametrize("fill", MIXED_FILLS)
def test_rx_full_frame_every_dibit(fill):
    """A 1518-byte frame whatever its bytes: zeros (where a word could look like an
    empty FIFO), ones, and bytes that look like the preamble and the SFD."""
    f = make_frame(FULL, 7, fill)
    recs, rest, b = rx_run([f])
    assert_delivered(recs, [f])
    assert rest == [] and b.flags() == 0


@pytest.mark.parametrize("size", [64, 65, 66, 67, 68, 69, 127, 1517, 1518])
def test_rx_every_length_phase(size):
    """Word and sample alignment of the frame's end: all three exits of the loop."""
    f = make_frame(size, size)
    recs, _, _ = rx_run([f])
    assert_delivered(recs, [f])


def test_rx_back_to_back_at_the_minimum_gap():
    frames = [make_frame(s, i, fill=rmii_fill) for i, (s, rmii_fill) in enumerate(
        [(FULL, None), (MIN, 0), (MIN, None), (FULL, 0xFF), (MIN, 0xFF), (MIN, None), (FULL, 0), (MIN, 0x55)])]
    recs, _, b = rx_run(frames, ifg=MIN_GAP)
    assert_delivered(recs, frames)
    assert b.flags() == 0


@pytest.mark.parametrize("toggle", [1, 2, 3, 8])
def test_rx_crs_dv_toggling_at_the_end(toggle):
    """Carrier ends before the data: CRS_DV toggles at 25 MHz for the last nibbles."""
    frames = [make_frame(s, s) for s in (64, 65, 66, 67, 1518)]
    recs, _, _ = rx_run(frames, toggle=toggle)
    assert_delivered(recs, frames)


@pytest.mark.parametrize("lead", [0, 1, 3, 20])
def test_rx_carrier_lead(lead):
    frames = [make_frame(64, 1), make_frame(100, 2)]
    recs, _, _ = rx_run(frames, lead=lead)
    assert_delivered(recs, frames)


@pytest.mark.parametrize("late", [False, True])
def test_rx_word_rate_and_read_rate(late):
    """The rates a host has to meet: measured on the words it received."""
    f = make_frame(FULL, 3)
    recs, _, b = rx_run([f], late=late)
    assert_delivered(recs, [f])
    real = [t for t, w in b.port.rx if w]
    reads = [t for t, _ in b.port.rx]
    dt = sorted({y - x for x, y in zip(reads, reads[1:])})
    mean = (real[-2] - real[0]) / (len(real) - 2)
    print(f"line {FULL} B frame: {len(real)} real words, one per {mean:.2f} clocks; reads every {dt} clocks")
    assert dt == [8]
    assert 11.5 < mean < 12.1
    assert len(real) == 508       # 1518 bytes and two dibits at 3 bytes a word, and the trailer


def _burst_ok(burst, gap=4, div=1, pause=None, late=False, one=False):
    f1, f2 = make_frame(FULL, 5), make_frame(MIN, 6)
    frames = [f1] if one else [f1, f2, f1]
    recs, _, b = rx_run(frames, burst=burst, gap=gap, div=div, pause=pause, late=late)
    return [r.data for r in recs] == frames and all(r.good and r.flags == 0 for r in recs), b, recs


@pytest.mark.parametrize("late", [False, True])
def test_rx_shortest_read_burst_that_sustains(late):
    """READ_FIXED bursts with their header and CS gap (26 clocks between bursts at
    SCK = clk), the pause a real host pays. Short bursts must fail loudly."""
    results = {}
    for n in (1, 2, 3, 4, 5, 6, 7, 8, 12, 16, 64, 256):
        ok, b, recs = _burst_ok(n, late=late)
        results[n] = ok
        if not ok:
            assert b.flags() & rmii.OVF_FLAG_BIT, f"burst {n} lost data with no flag"
            assert not all(r.good for r in recs) or len(recs) < 3
    print(f"late={late}: burst -> sustained {results}")
    assert results[7] and results[8] and results[64] and results[256]
    assert not results[6] and not results[1]


def test_rx_slowest_word_period_that_sustains():
    """One long burst with SCK held between words: 8 + p clocks per read."""
    ok = {}
    for p in range(0, 7):
        ok[8 + p] = _burst_ok(4096, pause=lambda p=p: p)[0]
    print(f"clocks per read -> sustained: {ok}")
    assert ok[8] and ok[11] and ok[12]
    assert not ok[13] and not ok[14]


def test_rx_sck_half_clock_fails_with_a_flag():
    """SCK = clk/2 reads a word per 16 clocks: slower than the 12 the firmware makes."""
    ok, b, recs = _burst_ok(4096, div=2)
    assert not ok
    assert b.flags() & rmii.OVF_FLAG_BIT
    assert any(r.flags & rmii.F_OVERFLOW for r in recs)


def _stalled(start, clocks):
    """A pause(): the host holds SCK for `clocks` after the word at `start`."""
    n = [0]

    def pause():
        n[0] += 1
        return clocks if n[0] == start else 0
    return pause


def _reader(period=8, burst=4096, stalls=(), late=False, offset=0, size=400):
    """One frame, a reader at `period` clocks per read in bursts of `burst`, SCK held
    for each (word index, clocks) of `stalls`. Returns (delivered whole, bench)."""
    stalls = dict(stalls)
    n = [0]

    def pause():
        n[0] += 1
        return period - 8 + stalls.get(n[0], 0)
    f = make_frame(size, 5)
    b = Bench(late=late)
    b.load_rx()
    b.start(1)
    b.phy_rx.idle(40 + offset)
    b.phy_rx.frame(f)
    b.port.script = rmii.drain(b.port, burst=burst, pause=pause)
    b.run_all(300)
    recs, _ = parse_rx(b.words())
    return len(recs) == 1 and recs[0].data == f and recs[0].good and recs[0].flags == 0, b


def _phases(period, burst):
    """Every phase of a stall that repeats: the frame's start against the reader
    over the 12-clock word, and the stall's place in the reader's own cycle."""
    if burst < 4096:
        n = burst
    else:
        n = {8: 3, 9: 4, 10: 6, 11: 12, 12: 1}[period]
    first = 60 - 60 % n                # well inside the frame, steady state
    return [(o, first + i) for o in range(12) for i in range(n)]


# Longest stall, clocks, worst phase, for (clocks per read, burst, late SCK phase).
# A host's budget is what its FIFO has left, not what the FIFO holds: 8 words of 12
# clocks less the backlog it already runs with.
STALL_BUDGET = {}
for _period, _clocks in ((8, 69), (9, 66), (10, 63), (11, 60), (12, 57)):
    STALL_BUDGET[_period, 4096, False] = STALL_BUDGET[_period, 4096, True] = _clocks
for _burst in (7, 8, 12):
    STALL_BUDGET[8, _burst, False] = STALL_BUDGET[8, _burst, True] = 43


@pytest.mark.parametrize("period,burst,late", sorted(STALL_BUDGET))
def test_rx_stall_budget_per_host(period, burst, late):
    """Search down from 110 over every phase: the budget must hold at all of them,
    and one clock more must fail at one of them, with the flag up."""
    budget, failed = 110, None
    for o, at in _phases(period, burst):
        while True:
            ok, b = _reader(period, burst, [(at, budget)], late, o)
            if ok:
                break
            assert b.flags() & rmii.OVF_FLAG_BIT, "a stall lost data with no flag"
            failed = (o, at)
            budget -= 1
    assert failed is not None
    assert all(_reader(period, burst, [(at, budget)], late, o)[0] for o, at in _phases(period, burst))
    print(f"period {period} burst {burst} late {late}: longest stall {budget} clocks, worst phase {failed}")
    assert budget == STALL_BUDGET[period, burst, late]


# Two stalls of `each` clocks, `apart` reads apart, worst phase over the reader's cycle.
TWO_STALLS = {(12, 150): 28, (8, 150): 69, (8, 10): 50}


@pytest.mark.parametrize("period,apart", sorted(TWO_STALLS))
@pytest.mark.parametrize("late", [False, True])
def test_rx_a_second_stall_needs_catch_up(period, apart, late):
    """A stall's backlog stays until the host reads faster than 12 clocks per word.
    At exactly 12 it never goes, and two stalls share one budget."""
    each = 110
    n = _phases(period, 4096)
    for o, at in n:
        while not _reader(period, 4096, [(at, each), (at + apart, each)], late, o, size=1000)[0]:
            each -= 1
    print(f"period {period}, {apart} reads apart, late {late}: {each} clocks each")
    assert each == TWO_STALLS[period, apart]


def test_rx_stall_mid_frame_is_flagged_and_recovers():
    f1, f2, f3 = make_frame(FULL, 1), make_frame(MIN, 2), make_frame(FULL, 3)
    recs, _, b = rx_run([f1, f2, f3], ifg=800, pause=_stalled(100, 400))
    assert recs[0].flags & rmii.F_OVERFLOW and not recs[0].good
    assert b.flags() & rmii.OVF_FLAG_BIT
    assert [r.data for r in recs[1:]] == [f2, f3] and all(r.good for r in recs[1:])


def test_rx_host_never_reads_then_returns():
    """Nobody reads for three frames. FIFO full, the rest lost, the host told."""
    frames = [make_frame(MIN, i) for i in range(6)]

    def late_host(b):
        for _ in range(4 * (288 + 60) + 40):
            yield (1, ())
        yield from rmii.drain(b.port)
    recs, _, b = rx_run(frames, script=late_host, tail=600, ifg=60)
    assert b.flags() & rmii.OVF_FLAG_BIT
    assert any(r.flags for r in recs)
    good = [r.data for r in recs if r.good]
    assert all(g in frames for g in good) and len(good) < len(frames)


@pytest.mark.parametrize("seed", range(12))
def test_rx_no_silent_loss_under_random_stalls(seed):
    """Whatever the host does: delivered records are exact and in order, and each
    run of lost frames has a flag on a record beside it or in the sticky flag."""
    rng = random.Random(seed)
    frames = [make_frame(rng.choice([64, 65, 66, 100, 300, 600, 1518]), 900 + i,
                         fill=rng.choice(MIXED_FILLS)) for i in range(12)]

    def pause():
        return rng.choice([0] * 300 + [rng.randrange(20, 2500)])
    recs, _, b = rx_run(frames, ifg=rng.choice([48, 48, 100, 500]), burst=rng.choice([8, 16, 64]),
                        pause=pause, tail=6000)
    i, last_good, lost = 0, -1, 0
    for k, r in enumerate(recs):
        if not r.good:
            continue
        assert r.data in frames[i:], "a good record that was never sent, or out of order"
        j = frames.index(r.data, i)
        if j > i:
            lost += j - i
            assert any(x.flags for x in recs[last_good + 1 if last_good >= 0 else 0:k + 1]) or \
                (last_good >= 0 and recs[last_good].flags), "frames lost with no flag"
        i, last_good = j + 1, k
    if i < len(frames):
        assert b.flags() & rmii.OVF_FLAG_BIT or any(r.flags for r in recs[last_good + 1:])
    assert not any(r.flags & rmii.F_OVERFLOW and r.good for r in recs)


def test_rx_no_loss_flag_on_a_clean_run():
    """The flag is not stuck: 20 frames, no flag, F_OVF clear."""
    frames = [make_frame(64 + 7 * i, i) for i in range(20)]
    recs, _, b = rx_run(frames)
    assert_delivered(recs, frames)
    assert b.flags() & rmii.OVF_FLAG_BIT == 0


def test_rx_too_long_is_flagged():
    f = make_frame(2000, 1)
    g = make_frame(MIN, 2)
    recs, _, _ = rx_run([f, g])
    assert recs[0].flags & rmii.F_TOO_LONG and not recs[0].good
    assert recs[1].data == g and recs[1].good


def test_rx_false_carrier_and_missing_sfd_make_no_record():
    b = Bench()
    b.load_rx()
    b.start(1)
    b.phy_rx.idle(30)
    b.phy_rx.pins += [(2, 1)] * 40                      # false carrier: RXD = 10 (LAN8720A: CRS high)
    b.phy_rx.idle(60)
    b.phy_rx.pins += [(3, 1)] * 10                      # carrier and 11 with no preamble before it
    b.phy_rx.idle(60)
    b.phy_rx.pins += [(0, 1)] * 4 + [(1, 1)] * 60       # preamble, no SFD
    b.phy_rx.idle(60)
    f = make_frame(MIN, 3)
    b.phy_rx.frame(f)
    b.port.script = rmii.drain(b.port)
    b.run_all(300)
    recs, _ = parse_rx(b.words())
    assert_delivered(recs, [f])


def test_rx_started_mid_frame_finds_the_next_one():
    f, g = make_frame(FULL, 1), make_frame(MIN, 2)
    b = Bench()
    b.load_rx()
    b.phy_rx.frame(f)
    b.phy_rx.idle(MIN_GAP)
    b.phy_rx.frame(g)
    b.port.script = rmii.drain(b.port)
    b.run(3000)                                         # the core is not enabled yet
    b.start(1)
    b.run(len(b.phy_rx.pins) - 3000 + 300)
    recs, _ = parse_rx(b.words())
    assert recs[-1].data == g and recs[-1].good


@pytest.mark.parametrize("gap", [MIN_GAP, 24, 16, 12, 8])
def test_rx_gap_margin(gap):
    """Below the standard's gap: how much of it the firmware uses. 96 bit times is
    48 clocks; the record's turnaround comes out of the preamble as well."""
    frames = [make_frame(64, i) for i in range(6)]
    recs, _, _ = rx_run(frames, ifg=gap, lead=0)
    assert_delivered(recs, frames)


def test_rx_dribble_nibble():
    """Two dibits past the last byte: the frame is all there, with one byte more,
    so the FCS check over the whole record fails. A stated limit."""
    f = make_frame(MIN, 3)
    b = Bench()
    b.load_rx()
    b.start(1)
    b.phy_rx.idle(40)
    b.phy_rx.frame(f)
    b.phy_rx.pins += [(1, 1), (2, 1)]
    b.port.script = rmii.drain(b.port)
    b.run_all(200)
    recs, _ = parse_rx(b.words())
    assert len(recs) == 1 and recs[0].data[:MIN] == f and len(recs[0].data) == MIN + 1
    assert not recs[0].good


def test_rx_trimmed_preamble_at_the_minimum_gap():
    """A PHY that hands over only the SFD and one preamble byte."""
    frames = [make_frame(64, i) for i in range(6)]
    b = Bench()
    b.load_rx()
    b.start(1)
    b.phy_rx.idle(40)
    for f in frames:
        b.phy_rx.frame(f, lead=0, preamble=rmii.PREAMBLE[-2:])
        b.phy_rx.idle(MIN_GAP)
    b.port.script = rmii.drain(b.port)
    b.run_all(200)
    assert_delivered(parse_rx(b.words())[0], frames)


# ---------------------------------------------------------------- why the firmware is built this way
NAIVE = """.program naive
.clkdiv 1
.in_pins 0
.shift_in right autopush 32
.wrap_target
loop:
    in pins, 2
.wrap
"""


def test_naive_one_instruction_loop_loses_silently():
    """`in pins, 2` on every clock with three pads: it keeps up, but has no
    instruction left to see the frame end or the FIFO fill. A host stall shortens
    the stream by the stalled clocks, with nothing to tell."""
    def run(stall):
        b = Bench()
        b.apply(host.load_program(0, asm.assemble(NAIVE)))
        b.start(1)
        f = make_frame(FULL, 4)
        b.phy_rx.idle(40)
        b.phy_rx.frame(f)
        b.port.script = rmii.drain(b.port, pause=_stalled(200, stall))
        b.run_all(40)
        return sum(1 for _, w in b.port.rx if w), b
    clean, _ = run(0)
    lossy, b = run(400)
    assert lossy < clean - 10
    assert b.flags() == 0 and b.chip.cores[0].pc == 0       # still in the loop, no flag


def test_second_flop_only_would_repeat_dibits():
    """Without SYNC_BYPASS both pads show the same age: the check below sees it."""
    f = make_frame(MIN, 2)
    recs, _, _ = rx_run([f], syncbyp=False)
    assert not recs or not recs[0].good


# ---------------------------------------------------------------- negative tests
@pytest.mark.parametrize("seed", range(4))
def test_corrupted_dibit_fails_the_check(seed):
    """One dibit wrong on the wire, anywhere after the SFD: the check must fail.
    All of a 64-byte frame, every XOR pattern in turn by position."""
    f = make_frame(MIN, seed)
    start = 32
    for pos in range(start, start + 256):
        x = 1 + (pos + seed) % 3
        b = Bench()
        b.load_rx()
        b.start(1)
        b.phy_rx.idle(40)
        b.phy_rx.frame(f, corrupt={pos: x})
        b.port.script = rmii.drain(b.port)
        b.run_all(100)
        recs, _ = parse_rx(b.words())
        assert len(recs) == 1 and recs[0].data != f and not recs[0].good, pos


def test_clean_control_for_the_corruption_test():
    f = make_frame(MIN, 1)
    b = Bench()
    b.load_rx()
    b.start(1)
    b.phy_rx.idle(40)
    b.phy_rx.frame(f, corrupt={})
    b.port.script = rmii.drain(b.port)
    b.run_all(100)
    recs, _ = parse_rx(b.words())
    assert_delivered(recs, [f])


def test_corrupted_sfd_gets_no_good_record():
    f = make_frame(MIN, 1)
    b = Bench()
    b.load_rx()
    b.start(1)
    b.phy_rx.idle(40)
    b.phy_rx.frame(f, corrupt={31: 2})                  # 11 -> 01: the SFD is now preamble
    b.port.script = rmii.drain(b.port)
    b.run_all(100)
    recs, _ = parse_rx(b.words())
    assert not any(r.good for r in recs)


def test_broken_firmware_fails():
    """One clock off in the loop."""
    src = (rmii.ROOT / "firmware/rmii_rx.pasm").read_text()
    broken = src.replace("s1: in pins, 5                [1]", "s1: in pins, 5                [2]")
    assert broken != src
    f = make_frame(MIN, 2)
    recs, _, _ = rx_run([f], prog=asm.assemble(broken))
    assert not recs or not recs[0].good
    _, results = timing.check_source(broken)
    assert [r.deadline.name for r in results if not r.status.startswith("PASS")] == ["word", "s12"]


# ---------------------------------------------------------------- the static checker
@pytest.mark.parametrize("name", ["rmii_rx.pasm", "rmii_tx.pasm", "rmii_match.pasm"])
def test_static_deadlines_pass(name):
    chk, results = timing.check_source((rmii.ROOT / "firmware" / name).read_text())
    assert not chk.errors
    assert results and all(r.status.startswith("PASS") for r in results), [r.report() for r in results]
    if name == "rmii_rx.pasm":
        # no assumption about the host: the loop's pushes are proved not to stall
        word = [r for r in results if r.deadline.name == "word"][0]
        assert (word.lo, word.hi) == (12, 12)
        assert not [a for a in word.assumptions if "fifo" in str(a) or "stall" in str(a)]


def test_checker_bounds_hold_on_the_model():
    """Each deadline measured on a run, with and without stalls: every interval
    inside the checker's bounds, and the checker's exact numbers are reached."""
    chk, results = timing.check_source((rmii.ROOT / "firmware/rmii_rx.pasm").read_text())
    b = Bench()
    b.load_rx()
    t = timing.Tracer(b.chip, 0, 0)
    b.start(1)
    b.phy_rx.idle(40)
    for s in (64, 65, 66, 67, 1518):
        b.phy_rx.frame(make_frame(s, s))
        b.phy_rx.idle(MIN_GAP)
    b.port.script = rmii.drain(b.port)
    b.run_all(100)
    seen = {}
    for r in results:
        ms = [m for m in timing.measure(chk, r.deadline, t) if not m.violations]
        assert ms, r.deadline.name
        assert all(r.contains(m.ticks, m.clocks) for m in ms), r.deadline.name
        seen[r.deadline.name] = (len(ms), min(m.ticks for m in ms), max(m.ticks for m in ms))
    print(seen)
    assert seen["word"][1:] == (12, 12) and seen["word"][0] > 400
    assert seen["turnaround0"][1:] == (12, 12)
    assert seen["turnaround2"][1:] == (11, 11)
    assert seen["turnaround4"][1:] == (10, 10)


# ---------------------------------------------------------------- TX
def tx_run(frames, gap=MIN_GAP, mode="paced", ramp=24, stop_after=None, corrupt_word=None, late=False, tail=200):
    """Feed the frames and sample the pins. mode: 'paced' one burst, SCK held 2 clocks
    per word after `ramp` words at full speed; 'bursts' 9-word WRITE_FIXED bursts
    (8 SCK per word + 18 clocks of command, address and CS: 10 clocks per word)."""
    b = Bench(late=late)
    b.load_tx()
    b.start(2)
    samples = rmii.tx_samples(frames, gap=gap)
    w = rmii.tx_words(samples)
    if corrupt_word is not None:
        w[corrupt_word[0]] ^= corrupt_word[1]
    if stop_after is not None:
        w = w[:stop_after]
    fifo = rmii.fifo_addr(1)

    def script(b):
        if mode == "paced":
            n = [0]

            def pause():
                n[0] += 1
                return 2 if n[0] > ramp else 0
            yield from b.port.write_fixed(fifo, w, pause=pause)
        else:
            yield from b.port.write_fixed(fifo, w[:ramp])
            for i in range(ramp, len(w), 9):
                yield from b.port.write_fixed(fifo, w[i:i + 9])
    b.port.script = script(b)
    b.run(len(w) * 10 + 200 + tail)
    return b


def assert_sent(b, frames, gap=MIN_GAP):
    fr = b.phy_tx.frames()
    assert len(fr) == len(frames), (len(fr), len(frames))
    for (t, data, pre, n), f in zip(fr, frames):
        assert pre, "preamble"
        assert data == f and rmii.fcs_ok(data)
        assert n == 4 * (8 + len(f)), "a dibit too many or too few: a gap in the stream"
    starts = [x[0] for x in b.phy_tx.bursts]
    for e, s in zip(b.phy_tx.ends, starts[1:]):
        assert s - e == gap


@pytest.mark.parametrize("mode", ["paced", "bursts"])
@pytest.mark.parametrize("late", [False, True])
@pytest.mark.parametrize("fill", [None, 0, 0xFF])
def test_tx_full_frame_no_gap(mode, late, fill):
    f = make_frame(FULL, 9, fill)
    b = tx_run([f], mode=mode, late=late)
    assert_sent(b, [f])


@pytest.mark.parametrize("mode", ["paced", "bursts"])
def test_tx_min_frame(mode):
    f = make_frame(MIN, 2)
    assert_sent(tx_run([f], mode=mode), [f])


@pytest.mark.parametrize("mode", ["paced", "bursts"])
def test_tx_back_to_back_at_the_minimum_gap(mode):
    frames = [make_frame(s, i) for i, s in enumerate([64, 64, 1518, 64, 1518, 64])]
    assert_sent(tx_run(frames, mode=mode), frames)


def test_tx_host_rate_limits():
    """A plain burst at 8 clocks per word overruns the FIFOs on a full frame and a
    host at 11 clocks per word runs dry. Both break the frame: the rate is real."""
    f = make_frame(FULL, 1)
    w = rmii.tx_words(rmii.tx_samples([f]))
    fifo = rmii.fifo_addr(1)

    def run(period):
        b = Bench()
        b.load_tx()
        b.start(2)
        b.port.script = b.port.write_fixed(fifo, w, pause=lambda: period - 8)
        b.run(len(w) * period + 400)
        fr = b.phy_tx.frames()
        return len(fr) == 1 and fr[0][1] == f and fr[0][3] == 4 * (8 + FULL)
    assert not run(8)
    assert not run(11)


@pytest.mark.parametrize("late", [False, True])
def test_tx_longest_frame_from_a_plain_burst(late):
    """No pacing at all: one WRITE_FIXED at SCK = clk, words every 8 clocks, a host
    that can't hold SCK. The FIFOs absorb the difference up to a size, found here."""
    fifo = rmii.fifo_addr(1)

    def ok(size):
        f = make_frame(size, 1)
        b = Bench(late=late)
        b.load_tx()
        b.start(2)
        w = rmii.tx_words(rmii.tx_samples([f], tail=1))
        b.port.script = b.port.write_fixed(fifo, w)
        b.run(len(w) * 10 + 200)
        fr = b.phy_tx.frames()
        return len(fr) == 1 and fr[0][1] == f and fr[0][3] == 4 * (8 + size)
    size = 64
    while ok(size + 1):
        size += 1
    print(f"longest frame from an unpaced burst, late {late}: {size} bytes")
    assert size == 189 and not ok(1518)


TX_CUSHION = {(0, False): 0, (24, False): 46, (48, False): 94, (72, False): 142,
              (0, True): 0, (24, True): 46, (48, True): 94, (72, True): 142}


@pytest.mark.parametrize("ramp,late", sorted(TX_CUSHION))
def test_tx_cushion(ramp, late):
    """The longest host stall mid-frame after `ramp` words at full speed, by search
    to the clock. Once paced at 10 clocks a word the level never recovers."""
    f = make_frame(FULL, 1)
    w = rmii.tx_words(rmii.tx_samples([f]))
    fifo = rmii.fifo_addr(1)

    def ok(stall):
        b = Bench(late=late)
        b.load_tx()
        b.start(2)
        n = [0]

        def pause():
            n[0] += 1
            return (2 if n[0] > ramp else 0) + (stall if n[0] == 300 else 0)
        b.port.script = b.port.write_fixed(fifo, w, pause=pause)
        b.run(len(w) * 10 + 600 + stall)
        fr = b.phy_tx.frames()
        return len(fr) == 1 and fr[0][1] == f and fr[0][3] == 4 * (8 + FULL)
    lo, hi = -1, 400                   # lo works, hi does not
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if ok(mid):
            lo = mid
        else:
            hi = mid
    print(f"ramp {ramp} words, late {late}: longest stall mid-frame {lo} clocks")
    assert lo == TX_CUSHION[ramp, late]


def test_tx_underrun_breaks_the_frame_visibly_at_the_far_end():
    """The FIFO runs dry mid-frame: TX_EN stays up and a dibit repeats. The far
    end's FCS fails. The chip records nothing: this is a stated limit."""
    f = make_frame(FULL, 1)
    b = tx_run([f], stop_after=100, tail=500)
    uo = (b.chip.outputs()[0] >> 2) & 1
    assert uo == 1, "TX_EN stuck high until the host acts"
    w = rmii.tx_words(rmii.tx_samples([f]))
    fifo = rmii.fifo_addr(1)
    b.port.script = b.port.write_fixed(fifo, w[100:], pause=lambda: 2)
    b.run(len(w) * 10 + 400)
    fr = b.phy_tx.frames()
    assert len(fr) == 1 and not rmii.fcs_ok(fr[0][1][:FULL])
    assert fr[0][3] > 4 * (8 + FULL)


def test_tx_status_stalled_cannot_tell_underrun_from_idle():
    """STATUS.stalled is 1 in an underrun and 1 idling after a good frame: same
    register value, so it can't be the missing underrun flag."""
    f = make_frame(MIN, 1)
    status = sim.core_reg(1, sim.C_STATUS)
    under = tx_run([f], stop_after=10, tail=100)
    idle = tx_run([f], tail=100)
    assert (under.chip.outputs()[0] >> 2) & 1 == 1 and (idle.chip.outputs()[0] >> 2) & 1 == 0
    assert under.chip.read(status) == idle.chip.read(status) == 0x1000


def test_corrupted_tx_dibit_fails_the_check():
    f = make_frame(MIN, 1)
    b = tx_run([f], corrupt_word=(10, 1 << 9))         # sample 3: TXD1
    with pytest.raises(AssertionError):
        assert_sent(b, [f])
    assert not rmii.fcs_ok(b.phy_tx.frames()[0][1])


def test_broken_tx_firmware_has_gaps():
    prog = asm.assemble(""".program t
.clkdiv 1
.out_pins 8 3
.shift_out right autopull 30
.fifo join_tx
.wrap_target
    out pins, 3
    nop
.wrap
""")
    f = make_frame(MIN, 1)
    b = Bench()
    b.load_tx(prog)
    b.start(2)
    w = rmii.tx_words(rmii.tx_samples([f]))
    b.port.script = b.port.write_fixed(rmii.fifo_addr(1), w, pause=lambda: 12)
    b.run(len(w) * 22 + 200)
    with pytest.raises(AssertionError):
        assert_sent(b, [f])


# ---------------------------------------------------------------- both directions
def test_full_duplex_at_line_rate_does_not_fit_the_port():
    """Reads at one word per 12 clocks and writes at one per 10 share one port at 8
    clocks per word, one direction at a time: 8/12 + 8/10 = 147 % before headers.
    No alternation of bursts delivers both 1518-byte frames."""
    rx, tx = make_frame(FULL, 1), make_frame(FULL, 2)
    w = rmii.tx_words(rmii.tx_samples([tx]))
    results = {}
    for r_n in (2, 4, 8, 16):
        for w_n in (2, 4, 9, 16):
            b = Bench()
            b.load_rx()
            b.load_tx()
            b.start(3)
            b.phy_rx.idle(40)
            b.phy_rx.frame(rx)

            def script(b=b, r_n=r_n, w_n=w_n):
                i = 0
                while True:
                    yield from b.port.write_fixed(rmii.fifo_addr(1), w[i:i + w_n] or [0])
                    i += w_n
                    yield from b.port.read_fixed(rmii.fifo_addr(0), r_n)
            b.port.script = script()
            b.run_all(2000)
            recs, _ = parse_rx(b.words())
            r_ok = len(recs) >= 1 and recs[0].good and recs[0].data == rx
            fr = b.phy_tx.frames()
            t_ok = len(fr) == 1 and fr[0][1] == tx and fr[0][3] == 4 * (8 + FULL)
            results[r_n, w_n] = (r_ok, t_ok)
            if not r_ok:
                assert b.flags() & rmii.OVF_FLAG_BIT, "RX lost data with no flag"
    print(f"(read burst, write burst) -> (rx ok, tx ok): {results}")
    assert not any(a and b for a, b in results.values())


def test_half_duplex_rx_then_tx_on_one_chip():
    """Both firmwares loaded, core 0 and core 1: a frame in, then a frame out, on
    one port."""
    rx, tx = make_frame(MIN, 1), make_frame(MIN, 2)
    w = rmii.tx_words(rmii.tx_samples([tx]))
    b = Bench()
    b.load_rx()
    b.load_tx()
    b.start(3)
    b.phy_rx.idle(40)
    b.phy_rx.frame(rx)
    end = len(b.phy_rx.pins)
    b.port.script = b.port.read_fixed(rmii.fifo_addr(0), 50)
    b.run(end + 80)
    recs, _ = parse_rx(b.words())
    assert_delivered(recs, [rx])
    b.port.script = b.port.write_fixed(rmii.fifo_addr(1), w, pause=lambda: 1)
    b.run(len(w) * 10 + 100)
    fr = b.phy_tx.frames()
    assert len(fr) == 1 and fr[0][1] == tx


# ---------------------------------------------------------------- reaction
FIELD_END = 32 + 4 * 16 - 1       # preamble + SFD dibits, then 14 header bytes and the field


def field_frame(value, size=64, ethertype=0x88B5, seed=1):
    head = bytes.fromhex("ffffffffffff020000c0ffee") + ethertype.to_bytes(2, "big")
    body = head + value.to_bytes(2, "little") + bytes((seed + i) & 255 for i in range(size - 20))
    return rmii.with_fcs(body)


def match_run(frames, threshold=1000, bypass=False, gap=MIN_GAP, lead=4, toggle=0):
    b = Bench()
    b.apply(host.load_program(0, rmii.firmware("rmii_match.pasm")))
    if bypass:
        b.write(sim.A_SYNCBYP, 0x03)
    b.write(rmii.fifo_addr(0), threshold)
    b.start(1)
    b.phy_rx.idle(70)
    marks = []
    for i, f in enumerate(frames):
        if i:
            b.phy_rx.idle(gap)
        marks.append(len(b.phy_rx.pins) + lead + FIELD_END)
        b.phy_rx.frame(f, lead=lead, toggle=toggle)
    b.trace = []
    b.port.script = rmii.drain(b.port)
    b.run_all(100)
    hit = rmii.HIT_PIN - 8
    rises = [t for (t, uo), (_, prev) in zip(b.trace[1:], b.trace) if (uo >> hit) & 1 and not (prev >> hit) & 1]
    out = []
    for m in marks:
        r = [x for x in rises if m <= x < m + 100]
        out.append(r[0] - m if r else None)
    return out, [w for _, w in b.port.rx if w], b


@pytest.mark.parametrize("bypass,clocks", [(False, 10), (True, 9)])  # 0.2 us, 0.18 us
def test_reaction_clocks(bypass, clocks):
    """From the REF_CLK edge that latches the field's last dibit into the first
    synchroniser flop to the edge that changes the pin's output register: exact."""
    frames = [field_frame(2000 + i, seed=i) for i in range(8)]
    r, got, _ = match_run(frames, bypass=bypass)
    print(f"bypass={bypass}: reaction {sorted(set(r))} clocks")
    assert r == [clocks] * 8
    assert got == [2000 + i for i in range(8)]


def test_reaction_hits_and_misses():
    frames = [field_frame(1000), field_frame(999), field_frame(1001), field_frame(65535),
              field_frame(5000, ethertype=0x0800), field_frame(0)]
    r, got, _ = match_run(frames, threshold=1000)
    assert [x is not None for x in r] == [True, False, True, True, False, False]
    assert got == [1000, 1001, 65535]


def test_reaction_full_size_and_toggling_tail():
    frames = [field_frame(2000, size=1518), field_frame(3000), field_frame(4000, size=1518)]
    r, got, _ = match_run(frames, toggle=3)
    assert r == [10, 10, 10] and got == [2000, 3000, 4000]


def test_reaction_on_a_corrupted_field_changes():
    f = field_frame(2000)
    b = Bench()
    b.apply(host.load_program(0, rmii.firmware("rmii_match.pasm")))
    b.write(sim.core_reg(0, sim.C_FIFO), 1000)
    b.start(1)
    b.phy_rx.idle(40)
    b.phy_rx.frame(f, corrupt={32 + 4 * 15 + 3: 2})    # top dibit of the field's high byte
    b.port.script = rmii.drain(b.port)
    b.run_all(100)
    words = [w for _, w in b.port.rx if w]
    assert words != [2000]


def test_match_threshold_wraps_at_16_bits():
    """The threshold word is kept as its low 16 bits: 0x10005 behaves as 5."""
    r, got, _ = match_run([field_frame(5), field_frame(4)], threshold=0x10005)
    assert [x is not None for x in r] == [True, False] and got == [5]


def test_match_fifo_loses_records_while_the_pin_still_rises():
    """Nobody reads: every hit raises HIT, RXF keeps 8 records and the rest go, with
    no flag. The pin is the result; the FIFO is a best-effort log."""
    frames = [field_frame(2000 + i) for i in range(12)]
    b = Bench()
    b.apply(host.load_program(0, rmii.firmware("rmii_match.pasm")))
    b.write(rmii.fifo_addr(0), 1000)
    b.start(1)
    b.phy_rx.idle(70)
    for f in frames:
        b.phy_rx.frame(f)
        b.phy_rx.idle(MIN_GAP)
    b.trace = []
    b.run_all(100)
    hit = rmii.HIT_PIN - 8
    rises = sum(1 for (_, u), (_, v) in zip(b.trace[1:], b.trace) if (u >> hit) & 1 and not (v >> hit) & 1)
    b.port.script = b.port.read_fixed(rmii.fifo_addr(0), 12)
    b.run(200)
    got = [w for w in b.words() if w]
    assert rises == 12 and got == [2000 + i for i in range(8)] and b.flags() == 0


def test_match_pin_is_a_latch():
    """HIT stays up past the frame's end and through idle, until the next carrier."""
    b = None
    r, _, b = match_run([field_frame(2000)])
    hit = rmii.HIT_PIN - 8
    assert (b.chip.outputs()[0] >> hit) & 1 == 1      # 100 clocks after the frame
    r, _, b = match_run([field_frame(2000), field_frame(5)], gap=2000)
    assert (b.chip.outputs()[0] >> hit) & 1 == 0      # the next frame's carrier cleared it


def test_reaction_precedes_the_end_of_a_min_frame():
    r, _, _ = match_run([field_frame(2000)])
    left = 4 * (64 - 16) - r[0]
    print(f"pin up {left} clocks ({left * 20} ns) before a 64-byte frame's last dibit is latched")
    assert left == 182


# ---------------------------------------------------------------- pad timing
def eye_ok(offset_ns, seed=1):
    """A 64-byte frame through pads sampled offset_ns after the nominal edge: the
    chip's edge t falls at 20 t + offset_ns on the PHY's time, and the PHY launches
    pins[n + 1] at 20 n, valid from tov after it to toh after the next edge. Anywhere
    else the pads read random: the model has no X."""
    tm = rmii.LAN8720A["ref_clk_in"]
    rng = random.Random(seed)
    f = make_frame(MIN, 1)
    b = Bench()
    b.load_rx()
    b.start(1)
    b.phy_rx.idle(40)
    b.phy_rx.frame(f)
    pins = b.phy_rx.pins

    def at(t):
        now = 20 * t + offset_ns
        n = (now - int(tm["tov"])) // 20
        if now <= 20 * (n + 1) + tm["toh"]:
            return pins[n + 1] if 0 <= n + 1 < len(pins) else (0, 0)
        return (rng.randrange(4), rng.randrange(2))
    b.phy_rx.at = at
    b.port.script = rmii.drain(b.port)
    b.run(len(pins) + 200)
    recs, _ = parse_rx(b.words())
    return len(recs) == 1 and recs[0].data == f and recs[0].good


def test_eye_on_the_model():
    """The sample phase sweep: frames pass where the PHY's output is valid. The model
    has no setup time: this shows the shape, and that a whole clock doesn't matter."""
    ok = [o for o in range(0, 40) if all(eye_ok(o, s) for s in range(3))]
    width = len([o for o in ok if o < 20])
    print(f"sample offsets after REF_CLK rise that pass (0..39 ns): {ok}")
    assert all((o % 20 >= 14 or o % 20 <= 3) for o in ok)
    assert set(o % 20 for o in ok) == set(range(14, 20)) | set(range(0, 4))
    assert width == 10


def test_pad_budget():
    """Budget at the macro's boundary, in-phase REF_CLK (skew 0): the estimate the
    study quotes. STA of the macro only; harness, pads and board are not in it."""
    rows = {}
    for mode, phy in rmii.LAN8720A.items():
        for corner, chip in rmii.STA_FO6.items():
            rows[mode, corner] = rmii.rx_slack(phy, chip) + rmii.tx_slack(phy, chip)
    for k, v in rows.items():
        print(k, "rx setup/hold, tx setup/hold ns:", " ".join(f"{x:.2f}" for x in v))
    assert all(min(rows["ref_clk_in", c]) > 0 for c in rmii.STA_FO6)
    assert rows["ref_clk_out", "fast"][3] < 0                # TX hold, before any added delay
    assert min(rows["ref_clk_out", "slow"][:2]) < 0.5        # RX hold, the thin one
    # 10 ns of insertion delay, the TT clock page's figure, in one clock path only
    bad = rmii.rx_slack(rmii.LAN8720A["ref_clk_in"], rmii.STA_FO6["typ"], skew=10.0)
    assert bad[1] < 0


def test_port_model_write_path_counts_every_word():
    """The port model itself: a WRITE_FIXED of n words lands n words in order."""
    b = Bench()
    words = [0x1000 + i for i in range(6)]
    b.port.script = b.port.write_fixed(sim.core_reg(0, sim.C_FIFO), words)
    b.run(200)
    assert list(b.chip.cores[0].txf) == words


def test_port_model_read_path_counts_every_word():
    """A READ_FIXED pops what it delivered and no more, zeros when empty."""
    b = Bench()
    core = b.chip.cores[0]
    core.rxf = [11, 22, 33]
    b.port.script = b.port.read_fixed(sim.core_reg(0, sim.C_FIFO), 2)
    b.run(120)
    assert [w for _, w in b.port.rx] == [11, 22] and core.rxf == [33]
    b.port.script = b.port.read_fixed(sim.core_reg(0, sim.C_FIFO), 3)
    b.run(120)
    assert [w for _, w in b.port.rx] == [11, 22, 33, 0, 0] and core.rxf == []


def test_tx_frame_needs_an_idle_sample_after_it():
    """(8 + 67) * 4 samples fill exactly 30 words. With nothing after them the core
    stalls with TX_EN up; one idle sample more drops it on the next clock."""
    f = make_frame(67, 1)

    def tx_en_left_high(tail):
        b = Bench()
        b.load_tx()
        b.start(2)
        w = rmii.tx_words(rmii.tx_samples([f], tail=tail))
        b.port.script = b.port.write_fixed(rmii.fifo_addr(1), w, pause=lambda: 2)
        b.run(len(w) * 10 + 200)
        return (b.chip.outputs()[0] >> 2) & 1
    assert tx_en_left_high(0) == 1
    assert tx_en_left_high(1) == 0


def test_rx_carrier_during_the_quiet_check_is_flagged():
    """Back from a miss or just started, the core wants 4 quiet clocks. A carrier that
    breaks them is a frame it can't catch: the flag and the next record say so."""
    f = make_frame(MIN, 1)
    b = Bench()
    b.load_rx()
    b.start(1)
    b.phy_rx.pins += [(0, 1)] * 20 + [(0, 0)] * 2 + [(0, 1)] * 20
    b.phy_rx.idle(60)
    b.phy_rx.frame(f)
    b.port.script = rmii.drain(b.port)
    b.run_all(200)
    recs, _ = parse_rx(b.words())
    assert b.flags() & rmii.OVF_FLAG_BIT
    assert len(recs) == 1 and recs[0].data == f and recs[0].good and recs[0].flags & rmii.F_MISSED


def test_rx_sfd_needs_both_bits():
    """After a preamble dibit, RXD = 10 is not the SFD's 11: only the frame comes out."""
    f = make_frame(MIN, 1)
    b = Bench()
    b.load_rx()
    b.start(1)
    b.phy_rx.idle(30)
    b.phy_rx.pins += [(1, 1)] * 8 + [(2, 1)] * 8
    b.phy_rx.idle(60)
    b.phy_rx.frame(f)
    b.port.script = rmii.drain(b.port)
    b.run_all(200)
    recs, _ = parse_rx(b.words())
    assert_delivered(recs, [f])
