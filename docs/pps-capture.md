# Pulse timestamps (`pps_capture`)

`firmware/pps_capture.pasm` and `tools/pe/pps.py` stamp the edges of one input
with a free-running 48-bit count of chip clocks, and report interval and drift
of a pulse-per-second style signal against that count. Written after the RTL
freeze; it uses one core, 34 of its 256 words, and no RTL change.

```python
mh.apply(pps.load_writes(pps.assemble(), pin=16))
edges = pps.Decoder().feed(words)                     # words from core 0's RX FIFO
print(pps.analyse(edges, nominal=50_000_000, min_width=50).summary())
```

Read the FIFO by its level (`STATUS[7:4]`), never blindly: an empty read gives
0, and word 1 of a record may be 0, so `Decoder` raises on a word that can't
be a record.

**Evidence level:** the cycle model and RTL simulation through the SPI port.
Not gate level, not FPGA, not silicon. All clocks below are simulated clock
edges; there is no analog input and no real oscillator anywhere in these tests.

## What it measures, and what it can't

* **It measures against the local clock.** One tick is one chip clock, 20 ns at
  50 MHz. Drift is `fitted period / nominal - 1`; it says the two clocks differ,
  not which one moved. For example, 500 extra clocks per 50 MHz second read as
  +10 ppm, whether the chip's oscillator is fast or the input is slow.
* **There is no UTC reference.** A stamp is a count since the core started.
  The chip has no time of day, does not discipline anything, and holds no
  calibration of its own clock.
* **The host pass rate limits it.** The RX FIFO holds 4 records (2 words each),
  and a pulse is 2 records. If a fifth edge arrives before the host reads, its
  record is dropped and counted: at 1 Hz, read at least once in every 2 pulses.
  A faster input needs passes at the same 4 edges per pass
  (`test_host_pass_rate_bound`: a pass every 4 edges loses none, every 5 loses
  exactly one per pass). The chip never waits: a slow host loses edges, not
  time (`test_a_slow_host_loses_edges_never_time`, RTL `pps_fifo_full`).
* **Resolution is not accuracy.** The stamp is exact to the clock for what the
  core read. A real asynchronous edge also carries one clock of sampling
  uncertainty, and an edge near the synchroniser's setup window may take a
  clock more. That is a paper estimate: the tests drive whole clocks.
* **A dead input says nothing.** No edge, no record
  (`test_no_pulse_at_all_gives_no_records`). "The input stopped" is the host's
  own timeout; only the next pulse shows up as missed pulses.
* **One input, CLKDIV 1, one core.** It leaves no spare register or OSR on
  that core.

## How it works

The counter is `OSR[15:0]:r3:r2`, counting down. The WAIT (`wait pins != r0
timeout r2`) decrements r2 each tick and doubles as the edge detector. Every path
out of the WAIT takes its own length out of the counter on the way back, so the
counter advances one per clock regardless of what happened. Each record is a
complete absolute stamp, which is what lets a dropped record cost nothing else.

Word 0: `[31:19]` drops (mod 8192), `[18]` 0, `[17]` 1, `[16]` level after the
edge, `[15:0]` counter `[47:32]`. Word 1: counter `[31:0]`. The firmware checks
for two free words first (`level <= 6`) and pushes both or neither.

## Bounds, from the design

These are the checker's bounds from the assembled words
(`python -m pe.timing firmware/pps_capture.pasm`), and
`test_static_bounds_are_the_constants_the_decoder_uses` holds `pe.pps` to them.
A tick here is a clock.

| Path out of the WAIT at W | Next attempt | Edge there is late by, at most |
|---|---|---|
| rollover: r2 ran out | W + 4 | 3 ticks |
| rollover: r3 ran out too, every 2^32 ticks | W + 8 | 7 ticks |
| record | W + 13 | 12 ticks |
| record, the `sub` borrows (r2 < 13) | W + 16 | 15 ticks |
| record, borrow and r3 = 0 | W + 19 | 18 ticks |

An edge on a tick where the WAIT is attempted is stamped exactly. An edge in a
path is seen at the next attempt, and never lost as long as the level has not
returned by then. So:

* An isolated edge is exact unless it lands in the blind ticks of a rollover: 3
  ticks in every 65536, and 7 at the carry every 2^32. **There the stated
  resolution does not hold**, and the record says so: `Edge.late` is 3 or 7,
  from the stamp (r2 = 0xFFFC, or 0xFFF8 with r3 = 0xFFFF).
  `test_every_phase_through_a_rollover` puts an edge on every
  tick around a rollover and a carry, and asserts the true tick is in
  `[tick - late, tick]` with the bound reached.
* The second edge of a pulse narrower than the record path is stamped at the
  path's end. `Pulse.width` is then the path's length and `fall_late` makes the
  true width's range explicit (`test_every_width_through_the_record_path`, every
  width from 1 tick, on all three paths).
* A change that reverts between two attempts is not seen at all
  (`test_a_dip_inside_the_record_path_is_not_seen`, at the exact boundary: pad
  back at c + 13 unseen, at c + 14 seen, for a rise at c).
* After drops, the next record's `late` is 18, the worst case: the record
  before it is unknown.

The pad is read 2 clocks late (isa §2). That is a constant, not an error:
`test_stamps_equal_the_wire_clock_for_clock` predicts each stamp in absolute
ticks from the drive clock and the CTRL write.

## Wrap

48 bits of 20 ns is 65.2 days. A stamp needs no history inside that: two pulses
100 s apart, past the 32-bit point with no record in between, give the interval
to the clock (`test_interval_longer_than_the_32_bit_counter`). A 1 Hz train
through all three rollovers at once, the counter crossing zero, gives every
interval exact (`test_interval_across_every_counter_digit_at_one_hertz`).

The host needs one record in every 2^48 ticks: a whole wrap between records can't
be seen, and any backward step counts as one wrap
(`test_an_interval_of_2_48_ticks_aliases`). `drops` wraps at 8192.

The 1 Hz runs use a skip over quiet WAIT ticks and whole rollover periods, in
the test bench only; `test_fast_forward_equals_stepping` requires the same final
state, register for register, with and without it, and the firmware mutants run
without the whole-period part.

## Pulse trains

`pe.pps.analyse` sorts rising edges into accepted pulses, runts, off-grid pulses
and gaps, on `nominal` ticks per pulse. Runts are narrower than `min_width`
(decided on the narrowest width the stamps allow); they don't count and don't
move the grid. An accepted pulse is within `tol` of a whole number of periods
after the previous one (default 1000 ppm). The numbers in the report are exact
fractions: `period` is the least-squares fit of tick on pulse number.

Tested on the model at 1 Hz, 50 MHz, with every expected value computed from
the drive clocks: offset to the tick; drifts of 0, +-37, +500 and +-2500 clocks per
second (`period` and `drift_ppm` exact); jitter of +-5000 clocks on a drift of
123 (the same fit as an independent one); pulses 3, 6 and 7 missing (numbered,
grid held); a runt of 1 to 100 ticks between pulses (reported with its stamp, the
intervals around it unharmed).

Tested on the RTL through the SPI port (`make COCOTB_TEST_MODULES=test_pps`):
every record path, every phase of both rollovers, and a pulse train, each with
the words equal to the model's for the same stimulus and every interval equal to
the drive clocks; and a FIFO overflow, with the drop count and the next stamp
exact. The train is 70000 nominal clocks with a drift of 7, jitter of +-60, a
missed pulse and a 3-clock runt.

## Limits of the evidence and the analysis

* **One real 1 s interval ran on the RTL**, once: `pps_one_second`, 50,000,003
  clocks between two pulses with the host port idle, reported to the clock
  (+0.060 ppm, as driven). It took 6831 s on the compute runner, so it runs only
  with `PPS_LONG=1`, not in CI; the run was on commit 04cfbf6. The regression's
  other RTL intervals are up to 70000 clocks. The carry and the counter's top
  were reached on the RTL by loading the counter near them, not by counting there,
  and a train of 1 s pulses was run on the model only.
* `analyse` anchors on the first wide pulse. If that one is off grid, every real
  pulse lands in `offgrid` and nothing is fitted
  (`test_an_off_grid_first_pulse_is_reported_not_hidden`): it fails visibly, it
  doesn't recover. A phase step in the input does the same from there on.
* `analyse` includes stamps with `late` > 0 in the fit; `Report.max_late` says
  how far they can be off.
* A drift larger than `tol` is outside the grid. The caller sets `nominal` and
  `tol`; nothing estimates them.
