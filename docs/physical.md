# Physical design notes (IHP SG13CMOS5L, Tiny Tapeout 6x4)

A running log of what the flow taught us. The numbers come from local LibreLane
3.1.0.dev3 runs. `flow/harden.sh NAME` hardens HEAD in its own worktree and
writes a signoff summary (`flow/signoff.py`); `flow/gl_test.sh` runs the
gate-level tests on the result. The routability experiments below used a
flop-IMEM script stopped after global routing (`flow/exp.sh`, removed after
bd07aa5; see git history).

**Release check.** `flow/signoff.py` is the minimum check. A release candidate
must also pass `flow/release_check.py <run worktree>` with automatic GL, SDF
and precheck receipts. It requires finite, clean setup/hold at the actual
clock constraint, no slew/capacitance/fanout violations without explicit
documented waivers, complete functional suites and the required SDF cases.
Receipts bind the candidate and result files by full SHA-256. Unrelated pass
logs, partial suites and arithmetic estimates at another clock cannot pass.
[Release evidence](release-evidence.md) gives the commands, coverage and limits.
v2c fails at 50 MHz (slow setup −1.57 ns, 3 slew and 85 fanout violations).

**Status:** the physical candidate is run **fo6** (2026-10-13): run t48ba's
v2 RTL and settings plus `Project.DetailedRouting`, which keeps antenna
diodes within the fanout limit (see "Antenna diodes and the fanout limit"
below). It passes signoff and the physical part of
`flow/release_check.py` with no exception at any corner: 50 MHz setup and
hold everywhere, no slew, capacitance or fanout violation. Everything up to
detailed routing is t48ba's, bit for bit; its GL, SDF and precheck receipts
are still to be made. A run is a commit:
`flow/harden.sh NAME` refuses a dirty tree, `flow/gen_config.py --check`
confirms `src/config.json` is the generator's, and `flow/signoff.py` judges
the result.

`flow/gen_config.py --clock-fanout-fix` (off by default) clusters CTS sinks by
eight and adds `Project.FixClockFanout`, which splits clock branches above the
IHP buffers' fanout limit of eight and legalizes the added buffers.

## The tile

* The 6x4 die is 1289.28 × 710.64 µm. The core is about 1283.5 × 703.1 µm (0.90 mm²).
* All 43 signal pins sit on Metal4 along the top edge, between x ≈ 30 and 191 µm.
* **Routing layers: Metal2 (V), Metal3 (H), Metal4 (V).** Metal1 is taken by the
  cells, and TopMetal1 is reserved for the chip. That leaves **one horizontal
  layer**, and Metal4 also carries the power stripes.
* Cells: `dfrbpq_1` 49 µm², `mux2_1` 18, `mux4_1` 38, `dlhq_1` 31, `lgcp_1` 27 (ICG).
  There is no flop with an enable input, so every held flop costs a mux as well.

## SRAM macros

* `RM_IHPSG13_1P_256x32_c2_bm_bist`: 416.64 × 118.78 µm, all signal pins on the
  bottom edge. Clock→DOUT is 3.05 ns typical. Address hold is 0.52 ns, so short
  paths need hold buffers.
* The tile PDN is Metal4 stripes only. Stripes are redrawn over the macro's
  power columns by `Project.ExtendPowerStripes` (adapted from
  kdp1965/ihp-um-janestreet-prism). pdngen's PSM-0069 connectivity error before
  that step is expected.
* In historical runs, hold repair across all corners inserted about 3200 buffers
  and broke detailed placement. Those runs used `PNR_CORNERS = [typ, slow]`.
  The submission preset now includes fast as well after correcting hold uncertainty.
  About 1900 buffers remain, mostly for SRAM address hold.

## Timing

### Wire capacitance (2026-10-01)

final5's extracted SPEF has **0.19 fF/µm** of wire capacitance (least squares
over all 19.9K signal nets against their routed length); the tech LEF, which
PnR estimates use, says 0.092–0.097 fF/µm. Resistance agrees (0.515 Ω/µm, 20 Ω
per via). With the LEF values the slow corner showed +1.95 ns after CTS and
−6.9 ns at signoff, so setup repair never ran ("No setup violations found").
`flow/gen_config.py` now sets `LAYERS_RC` to the measured capacitance
(`--wire-cap`) and turns on post-GRT timing repair. First run (t1): repair
found 573 violating endpoints after CTS and brought the slow corner from
−7.9 to −0.48 ns (+0.8 % area); −1.18 ns after global routing.

The core's own instruction → next-fetch-address logic, standalone at the slow
corner with timing repair (`INSTR_DELAY=11 flow/core_route.sh`), is a wall of
near-equal paths of about 7.5 ns. Shortening the worst ones in RTL doesn't
move it: fetching computed jumps' targets a cycle later (13.9 → 11.6 ns before
repair) and a register-side autopush compare both end within 0.1 ns of the
original after repair. Unrepaired path delays mislead here, since the
resizer buffers the high-fanout decode nets that dominate them.


* 50 MHz at the typical corner is comfortable. The flop build has +8.9 ns setup
  slack after CTS, the SRAM build +8.4 ns, and the SRAM build also meets 50 MHz
  at the slow corner (+1.9 ns).

### Each core fetches from its own bank (2026-10-04)

With `BANK_OWNER`, every bank's 32-bit output reached every core's instruction
mux and every core's fetch address reached every bank: with the banks in
opposite corners, about 128 wires across the die on Metal3, and each core's
logic pulled between both corners. Same RTL otherwise (t7, which congested:
300+ overflow iterations in global routing) with bank *b* fixed to core *b*
(run fb):

| | t7 | fb |
|---|---|---|
| global routing | 300+ extra iterations, stopped | 51 (clean), 51 overflows |
| wire | — | 2.44M µm (t5: 2.50M) |
| slow setup after post-CTS repair | −1.14 ns | −0.21 ns |

`BANK_OWNER` is gone (docs/isa.md §3); 256 words per core.

### Global routing's reserve decides routability here (2026-10-05)

The host-port reset fix (docs/review.md, R3) changes a few dozen gates. Every
netlist with any version of it ended global routing with 80–240 overflows at
the default `GRT_ADJUSTMENT` of 0.15 (15 % of each layer held back for
detailed routing), over a dozen runs and every placement perturbation tried
(wire-length coefficient, density, resizer corners, synthesis strategy and
hierarchy). The one such run taken through detailed routing (t13) stalled at
8 violations after six hours.
The netlist without the fix ended with 1–11 overflows in every perturbation.

What does *not* explain it: cell count, area and total Metal3 demand are the
same within 1 % in clean and congested runs, and detailed-placement HPWL
doesn't separate them either (h5 placed at 1.442M µm, below every clean run,
and congested). At ≈ 68 % Metal3 the outcome depends on where local hotspots
land, and FastRoute's rip-up and reroute either untangles them or doesn't.
Nor is the cost in any one part of the fix: removing the write guard or the
CS-synchronizer change each brought placement HPWL back from 1.52M to 1.46M µm,
yet the leaner netlist (6e7a82b) still congested at 0.15.

What fixed it: `GRT_ADJUSTMENT` 0.10. Global routing then ends with 15–39
overflows, and detailed routing clears them: g3, g4 and r2 all reached 0
violations, antenna repair included. (0.05 was no better than 0.10.) The
setting also changes placement, since routability-driven global placement
estimates congestion with the same adjustment. Of the clean runs, g4 (density
44) met hold at every corner (fast +0.05 ns); r2 (wire-length coefficient 0.2)
and g3 (density 34) missed fast-corner hold by 8 and 11 ps. `gen_config.py`
defaults are g4's. Fast-corner hold is the thin margin: the PnR hold margin is
0.05 ns, and extraction after detailed routing moves it by about that much, so
a later RTL change may want a larger post-GRT hold margin.
`PL_TIMING_DRIVEN=false` changed nothing (identical placement).

### Hold buffers came from the constraint (2026-10-04)

About 1700 hold buffers (+6 % area) went in after CTS in every run. The
violators (t8, fast corner, before repair: 743 endpoints) were mostly flops
whose next state includes their own value: hold muxes (IHP's flops have no
enable), counters, shift registers. 618 of them start and end in the same
register. Such a path launches and captures on the same clock pin, yet failed
by up to 0.07 ns, because the SDC applied LibreLane's 0.25 ns clock
uncertainty to hold as well as setup. A hold check compares one edge with
itself: jitter cancels, skew is computed from the propagated clocks and OCV is
in the ±5 % derates. With 0.1 ns of hold uncertainty the same database has 20
fast-corner violators (the SRAM address/data pins and a few real skew paths).
So `src/protoemu.sdc` sets 0.1 ns for hold (0.25 for setup), and the fast
corner is repaired directly (`PNR_CORNERS` has all three) with 0.05 ns
margins, instead of a 0.25 ns typ margin standing in for it. (The earlier
"fast-corner hold took 3200 buffers" was the same uncertainty.)
`CTS_BALANCE_LEVELS` changed nothing (same tree, same violators).

### Slow corner: useful skew around the SRAMs (2026-10-06)

Full-speed USB needs exactly 48 MHz (20.833 ns), so the slow corner
(`nom_slow_1p08V_125C`) must reach it. v2c: −1.57 ns at 20 ns, **−0.74 ns at
20.833 ns** (91 violating endpoints). STA with `CLOCK_PERIOD` 20.833 on the
same netlist and SPEF (the I/O delays scale with it).

**What limits it.** Every violator starts at an SRAM output and ends in a
flop of the same cycle's execute stage: 64 of the 91 are the chip's pin
registers (`pin_out`, `pin_oe`), the rest core 0's R0–R3, CRC and PC. v2c's
worst path (bank 1 DOUT[29] → `pin_out[3]`): the SRAM's clock pin at 1.32 ns
(CTS balanced it to the register tree with five delay buffers), clock to
DOUT 5.44 ns (the slow view; 5.13–5.26 ns before the 5 % derate, set by the
macro), then 15.6 ns through 44 cells (0.2 ns of it net RC; 3.6 ns in ten
fanout/repeater buffers; one `a221oi_1`, the only drive strength the library
has, takes 1.39 ns into 0.09 pF), capture clock 1.19 ns, 0.25 ns uncertainty,
0.17 ns setup. The first 5.3 ns of that path is instruction decode alone
(nothing but SRAM bits in its fan-in), then the source select, the funnel and
pin rotators, the side-set merge and the pin-owner mux. The paths that don't
start at an SRAM had ≥ +1.6 ns; paths into the SRAM from flops ≥ +2.8 ns; the
SRAM → logic → SRAM fetch-address loop +0.33 ns (bank 0). Upsizing every
buf_1/buf_2 on the violating paths to buf_4 (STA what-if) gained 0.035 ns:
the weak complex gates before them are the limit. Post-GRT repair stalls
(v2c: −1.65 ns, 680 passes without progress).

**What helped** (`flow/gen_config.py`, all opt-in):

* `--sram-clock-advance` (`Project.SramClockAdvance`, after CTS): each SRAM's
  `A_CLK` is driven by one buf_8 on the macro tree's root net, and CTS's
  delay buffers for it are removed. The SRAMs launch at ≈ 0.4 ns instead of
  1.2–1.3 ns; paths into the SRAMs trade that setup slack for hold slack (the
  SRAM address hold buffers mostly disappear); the fetch loop starts and ends
  on the same pin and is unchanged.
* `--late-clock N` (same step): the 64 pin registers clock behind N extra
  buf_8 (≈ 0.09 ns each at slow). `pin_oe` only drives pads (+12 ns from its
  outputs), `pin_out` feeds the cores' pin inputs (+3.3 ns). Hold into them
  (from `bus_wdata` and the static `pin_owner`/side-set configuration flops)
  shrinks by the same amount; N = 12 left fast-corner hold at −0.016 ns (t48u).
* `--clock-fanout-fix` (CTS sinks in clusters of 8, overloaded branches split):
  clears the 85 clock-tree fanout violations; with the SRAM advance it also
  changed the tree enough to help timing (t48e vs t48f).
* `--pnr-setup-margin` (`src/protoemu_pnr_margin.sdc`): PnR repairs against
  1.0 ns setup uncertainty instead of 0.25 (signoff unchanged). Unlike a
  resizer setup margin it leaves the FIFO latches' borrowed slack at 0.

What didn't: a post-GRT hold margin of 0.15 ns (450–840 hold buffers, +2.6–3.3 %
area; two runs never finished detailed routing, one lost 1.2 ns of slow setup);
`PL_WIRE_LENGTH_COEF` 0.2 with the above (detailed routing stalled at 6–7
violations); densities ≤ 43 % (global placement clamps to 0.43: t48h placed
exactly as t48f, same netlist hash); density 46 with `--clock-fanout-fix`
(OpenROAD CTS left one FIFO clock gate's CLK unconnected: t48l/n/o/q; the SRAM
clock step now stops on that). An RTL precompute of IN's autopush compare and
the JMP/WAIT zero tests (branch `t48-rtl`, formally equivalent, full
regression passing) congested global routing (Metal3 70 %, 400–440
overflows, against 62–64 % and ~10): at this utilization any netlist change is
a routability gamble. Synthesis `DELAY` strategies: deeper unplaced paths and
+4–32 % area.

Slow-corner setup at 20 ns varies by ±0.4 ns between placements of the same
configuration, so the runs below are samples, not a ranking of settings.

All on v2c's RTL (cbeae96's `src/`), signoff by `flow/signoff.py` (all pass
unless noted). "Worst flop" is the slow corner's worst setup slack at a flop
or SRAM pin (the summary's WS is 0 when only the FIFO latches' borrowing is
left); 48 MHz is STA at 20.833 ns on the same netlist and SPEF. Exceptions
are max slew / max cap / max fanout violations per corner (typ, slow, fast).
Settings: A = `--sram-clock-advance`, F = `--clock-fanout-fix`, Ln = 
`--late-clock n`, M = `--pnr-setup-margin`, C = `MAX_FANOUT_CONSTRAINT` 8 and
30 % slew margins in design repair, h = post-GRT hold margin (ns), d = density.

| Run | Settings | Slow worst flop, 50 / 48 MHz | Fast hold | Exceptions typ · slow · fast | GRT Metal3 / overflow |
|-----|----------|------------------------------|-----------|------------------------------|-----------------------|
| v2c | — | −1.569 / −0.736 | +0.041 | 0/0/85 · 3/0/85 · 0/0/85 | 63.8 % / 8 |
| t48a | F | −1.627 / −0.794 | +0.048 | 0/0/1 · 8/0/1 · 0/0/1 | 63.8 % / 11 |
| t48e | A | −1.496 / −0.663 | +0.054 | 0/0/82 · 10/0/82 · 0/0/82 | 63.9 % / 7 |
| t48g | A M | −0.801 / +0.032 | +0.046 | 0/0/83 · 9/0/83 · 0/0/83 | 63.9 % / 8 |
| t48f | A F | −0.716 / +0.117 | +0.015 | 0/0/2 · 3/0/2 · 0/0/2 | 63.9 % / 11 |
| t48i | A F L5 | −0.673 / +0.160 | +0.055 | 0/0/3 · 9/0/3 · 0/0/3 | 64.0 % / 12 |
| t48m | A F L5 M | −0.597 / +0.236 | +0.046 | 0/0/5 · 7/0/5 · 0/0/5 | 64.1 % / 11 |
| t48s | A F L5 M d44 | −0.439 / +0.394 | +0.063 | 0/0/2 · 3/0/2 · 0/0/2 | 63.8 % / 9 |
| t48r | A F L8 M | −0.325 / +0.508 | +0.050 | 0/0/5 · 9/0/5 · 0/0/5 | 64.1 % / 12 |
| t48v | A F L12 M | −0.275 / +0.558 | +0.014 | 0/0/1 · 4/0/1 · 0/0/1 | 64.2 % / 12 |
| t48u | A F L12 M d44 | −0.112 / +0.721 | **−0.016** (fails) | 0/0/2 · 3/0/2 · 0/0/2 | 63.8 % / 9 |
| t48z | t48u, h 0.15 | −1.311 / −0.478 | +0.059 | 0/0/2 · 6/0/2 · 0/0/2 | 64.1 % / 10 |
| t48ad | A F L8 M C | −0.289 / +0.544 | +0.060 | 0/1/1 · 1/1/1 · 0/1/1 | 64.0 % / 8 |
| t48ag | A F L8 M C h0.12 | **+0.054 / +0.887** | +0.079 | 0/0/1 · 2/0/1 · 0/0/1 | 64.0 % / 8 |
| t48af | A F L8 M C d44 | +0.243 / +1.076 | +0.041 | 2/0/3 · 4/0/3 · 0/0/3 | 63.7 % / 10 |
| t48aj | t48ag + `--final-design-repair` | −0.021 / +0.812 | +0.060 | 0/0/2 · 3/0/2 · 0/0/2 | 64.0 % / 10 |
| t48ak | t48ag + `--sram-pin-buffers`, PnR fanout 6 | −0.882 / −0.049 | +0.043 | 0/0/2 · 0/0/2 · 0/0/2 | 66.9 % / 35 |
| t48al | t48ak, d44 | −0.748 / +0.085 | +0.062 | 0/1/2 · 0/1/2 · 0/1/2 | 66.4 % / 48 |
| t48aq | A F L5 M C h0.12, pin buffers (A_ADDR drivers upsized), GRT cap/antenna margins 30 % | −0.552 / +0.281 | +0.060 | 0/0/3 · 3/0/3 · 0/0/3 | 63.7 % / 19 |
| t48ao | t48aq with L8 | +0.077 / +0.910 | **−0.003** (fails) | 0/0/1 · 0/0/1 · 0/0/1 | 63.7 % / 20 |
| t48ar | t48aq with L6, DRT antenna margin 0 | −0.001 / +0.832 | +0.038 | 0/0/2 · 0/0/2 · 0/0/2 | 63.7 % / 19 |

The remaining exceptions are of two kinds. Slew: always pins of bank 1
(A_ADDR[5], A_ADDR[0], A_DIN[*]), driven by an `a21oi_1` or a `buf_1` fanout
buffer; global routing estimated them inside the 0.595 ns limit with margin,
extraction gave up to 0.8 ns. `--sram-pin-buffers` cleared them (t48ak/al:
0 slew violations at every corner) at the cost of congestion when combined
with the PnR fanout limit of 6. Fanout: 1–3 nets per run end with 9–13
loads, every one because detailed routing's antenna repair (after all resizer
steps) attached 6–11 antenna diodes to a net the resizer had left at ≤ 8 (or
≤ 6). Shorter repair wires (`GRT_DESIGN_REPAIR_MAX_WIRE_LENGTH` 250) to avoid
the antenna violations cost 0.5–1 ns of slow setup and 360–400 hold buffers
(t48am/an, stopped after global routing).
A post-GRT hold margin of 0.14 ns (t48as) and density 44 with the pin buffers
(t48ap) stalled detailed routing; both were stopped.

**Antenna diodes and the fanout limit (2026-10-13).** The fanout
violations above all come from the order of the flow: detailed routing's
antenna repair runs after the last resizer step, and each diode's pin is an
input that counts against the library's limit of eight. t48ba's net3483
(driven by the repair wire buffer `wire3483`) had 7 cell loads after
repair and 11 after routing: the router attached four diodes, two next to
`_37093_/D` and one each next to `_26873_/A1` and `_26505_/A1`. Limits
during placement and global routing can't see that: the diodes don't exist
yet, and a PnR fanout limit of 6 congested Metal3 (t48ak/al). So
`Project.DetailedRouting` (`flow/drt_antenna_fanout.tcl`, on by default;
`gen_config.py --no-antenna-fanout-repair` gives t48ba's configuration)
runs LibreLane's `drt.tcl` unchanged and then, in the same OpenROAD
process, fixes what the router added (`flow/antenna_fanout.tcl`):

* every net whose input pins, diodes included, exceed the limit is split:
  each cell sink is grouped with the diodes nearest to it, and the group
  farthest from the driver plus the groups nearest to it move behind a new
  `buf_4` placed on the free row sites at their centroid, until both nets
  are within the limit;
* the original net is re-created under its own name and guides, both nets
  get guides over the buffer and the moved pins, and detailed routing and
  its antenna loop run again (minutes; in fo6, 57 violations after the
  first iteration and 0 after the second, no antenna violation);
* a check that every pin of both nets lies on its routed wire, then the
  fanout check, which repeats up to three rounds. A design without such
  nets passes through unchanged.

Each mechanism was measured; the first versions failed:

| Run | Step version | Result |
|-----|--------------|--------|
| fo1 | separate step after routing, global routing incremental | stopped before routing: on a copy of t48ba's routed database, detailed routing in a new OpenROAD process started over (38k violations at the first iteration) |
| fo2 | inside detailed routing's process, global routing incremental | stopped: after antenna repair, global routing works from the detailed routes; its incremental update rewrote 22k guides and dropped the NDRs of 14 clock roots, and the router started over (30k violations) |
| fo3 | guides written by the step, `detailed_placement` legalizes the buffer | stopped: legalization touched every instance and the router started over (91k violations) |
| fo4 | buffer placed on free sites by the step, resizer's `insert_buffer` | **LVS: 2 errors**, net3483 open at the buffer input: the router did not route the pin added to a net it had already routed |
| fo5 | odb edit, net kept | stopped by the step's own pin check (same open) |
| **fo6** | net re-created | **signoff passes, no electrical exception** |

fo6 (commit f6b8ca0, the `gen_config.py` defaults): net3483 keeps 5 cells,
1 diode and the buffer (7 loads); `antfo_buf_1` drives `_37093_/D`,
`_26873_/A1` and their 3 diodes (5 loads), 0.8 µm from their centroid. The
final netlist differs from t48ba's in exactly that: one buffer, one net, five
pins (and the fillers around the buffer). Routed wire +105 µm (1 788 162
against 1 788 057 µm), no routing DRC, no antenna violation. Both moved
endpoints have about 16 ns of setup and 2 ns of hold slack at the slow
corner (net3483 carries a static configuration bit), and the reported slacks
are t48ba's: setup WS 0 at 20 ns at all three corners (the latch-borrowing
floor; worst flop/SRAM endpoint +0.010 ns slow, +4.06 typ, +4.65 fast), hold
+0.191 / +0.362 / +0.095 ns (typ / slow / fast), 0 setup and hold violators,
0 slew / 0 capacitance / **0 fanout** violations at typ, slow and fast.
Global routing as in t48ba: Metal3 63.9 %, 12 overflows (27 after the last
post-GRT repair), all cleared by detailed routing.

**Candidate: t48ag** (commit e96cb69, `flow/harden.sh NAME -- --sram-clock-advance
--clock-fanout-fix --late-clock 8 --pnr-setup-margin --set MAX_FANOUT_CONSTRAINT=8
--set GRT_DESIGN_REPAIR_MAX_SLEW_PCT=30 --set DESIGN_REPAIR_MAX_SLEW_PCT=30
--set GRT_RESIZER_HOLD_SLACK_MARGIN=0.12`): signoff passes; setup is met at
every corner at 50 MHz, the slow corner's worst flop/SRAM-pin slack is
+0.054 ns at 20 ns and +0.887 ns at 20.833 ns; hold +0.186 / +0.353 / +0.079 ns
(typ / slow / fast); no capacitance violations; 2 slow-corner slew violations
(bank 1 A_ADDR[5] 0.692 ns, A_ADDR[0] 0.597 ns, limit 0.595) and 1 fanout
violation at every corner (11 loads, antenna diodes included). Netlist sha256
`b2cb0a1a619ad1bf`, GDS `90040c26d13fb390`; Metal3 64.0 %, 8 overflows after
global routing; 52.5 % utilization with the macros. Functional gate level: all
25 top-level tests (test.py 21, test_pio 2, test_eth_service 2).


Numbers are quick global-routing runs (5 iterations) on placed databases.
Metal3 is the only horizontal layer.

| Build | Utilization | Metal3 (H) | Overflow | Notes |
|-------|-------------|------------|----------|-------|
| 4 cores, flop IMEM, placement density 60 % | 51 % | **146 %** | 95.8K | infeasible at any spreading |
| 3 cores, flop IMEM, density 60 % | 43 % | 100 % | 17.6K | |
| 4 cores without shifters/rotators | 44 % | 84 % | 2.2K | datapath ≈ 40 % of per-core wiring |
| 2 cores, flop IMEM, density 60 % | 32 % | 83 % | 2.5K | cells clustered in the middle, sides empty |
| 2 cores, flop IMEM, **density 38 %** | 32 % | **58 %** | **0** | spreading fixes it |
| 2 cores + 2 SRAMs, top-right pair, density 34 % | 30 % | 68 % | 794 | hotspot under the left macro's pin edge |
| **2 cores + 2 SRAMs, bottom corners (FS), density 34 %** | 30 % | 66 % | **34** | |
| reference: WilliamZhang20/protocol-emulator (passes) | 36 % | 51 % | — | |

Lessons:

* **Wiring is set by the datapath.** Barrel shifters and rotators cost about
  0.42 m of wire per core (40 %), far more than their area suggests.
* **Placement density must track utilization.** TT's default
  `PL_TARGET_DENSITY_PCT` of 60 packs a 30 %-utilized design into the middle
  of the die. Set it a few points above utilization.
* **SRAM floorplan matters.** Put the macros in the bottom corners, mirrored so
  the pins face the logic. That keeps the top band (where every TT I/O pin
  is) and the middle free. Rotated macros (E/W) would be better still, but
  `Project.ExtendPowerStripes` supports only N and FS.
* **LibreLane 3.1.0.dev3 runs OpenROAD single-threaded** unless
  `OPENROAD_THREADS` is set (it passes `-threads None`).

### Three cores don't fit (2026-10-01)

3 cores, 3 SRAM banks, 4-deep FIFOs, current RTL, 5 global-routing iterations
(`STOP=grt FIFO_LOG2=2 flow/harden.sh NAME 3 -- --grt-iters 5`):

| Build | Utilization | Metal3 (H) | Overflow |
|-------|-------------|------------|----------|
| 2 cores (final5, routes clean) | 47 % | 68 % | 9 |
| 3 cores | 57 % | **99.8 %** | 24.1K |
| 3 cores, no config readback, no second read-ahead word | 55 % | 95.9 % | 21.2K |

A third core needs about 30 % less horizontal wiring at chip level; the best
cuts found are worth 4 %.

### The line unit and host port: back under the limit (2026-10-01)

Adding the line unit and the quad host port (+54K µm² synthesized, +21 %)
grew global-route wire 44 % (2.31M → 3.33M µm) and left 1091 overflows after
global routing (t1); post-GRT repair then spent hours re-routing. Placement
density (46 %) and the wire-capacitance setting changed nothing. What did:

| Build (short GRT, 5 iterations) | Metal3 | Overflow |
|---------------------------------|--------|----------|
| as t1 | 77.6 % | 951 |
| 4-deep FIFOs | 76.0 % | 525 |
| configuration registers write-only | 72.8 % | 336 |
| + ISR/OSR not readable | 71.5 % | 57 |

Short-GRT overflow is noisy (write-only + 4-deep FIFOs gave 657), so the
verdict is a full run. **t3, configuration registers write-only:** 13
overflows after global routing (final5: 12), Metal3 71.3 %, 2.50M µm of wire
(−25 %), post-GRT repair in 4 minutes. Also hiding ISR/OSR (t4) gave 20 overflows and
2.55M µm: no better, so they stay readable. The read path of the configuration
registers, through each core's and the chip's read mux, was the congestion.

### Floorplan: bank placement (2026-10-03)

t5's slow-corner violators start at bank 0 (−4.10 ns) far more than bank 1
(−0.89 ns): core 0's logic sits on the left (the line unit and the I/O pins
pull it there) while bank 0 is in the bottom-right corner. Swapping the banks
(`--layout corners-swap`, run fsw) improved the mid-flow estimate (−0.41 vs
−1.2 ns after global routing) but not signoff: slow −4.72 ns, typ +4.46 ns,
with 58 overflows after global routing instead of 8 (it still routed clean).
Both banks side by side at the bottom centre (`row`) stayed congested in
global routing. This experiment retained the original corners layout. The
2026-10-04 submission preset selects corners-swap; its new routing/signoff
result must be established separately.

### Where a core's wiring goes

`flow/core_route.sh` places and globally routes one core (pe_core + FIFOs,
core 0's ports) alone in about two minutes. Removing one block at a time
(baseline 116K µm², 762K µm of wire):

| Removed | Wire | Area |
|---------|------|------|
| host bus read mux | −11 % | −6.5K µm² |
| IN/OUT funnel shifter | −8.5 % | −9.3K |
| side-set rotators | −4.5 % | −3.8K |
| pin-write rotators | −4 % | −7.1K |
| config register readback only | −5.7 % | −1.3K |
| second read-ahead word (`rxf_head2`) | −4 % | −2.6K |
| ALU shifts / MOV bit reverse / IN pin rotate | −2.4 / −1.6 / −1.3 % | |

Rewrites that keep the behaviour gained nothing: masks from thermometer codes
instead of rotators (−0.5 %), ALU shifts through the funnel (+1.8 %).
Synthesis already shares well; the wiring is in what the core does.

### What the v2 additions cost (2026-10-05, before re-hardening)

Measured on the RTL alone (not yet hardened). Per core: `flow/core_route.sh`
at placement densities 34/46/50 (a single placement moves wire by ~2 %, as
much as the differences measured), cumulative from main, LibreLane synthesis
area. pe_line: `TOP=pe_line` at 34/56/62 (its utilisation is 51 %, so lower
densities place identically). pe_chip glue: yosys with the IHP typ library
(it isn't a core_route target).

| Step | Block | Wire (k µm, per density) | Avg Δ wire | Area |
|---|---|---|---|---|
| main (g4) | core | 718.0 / 706.5 / 689.5 | — | 105.5K µm² |
| + half-cycle NRZI sample | pe_line | 187.2 / 183.9 / 180.8 vs 182.2 / 179.2 / 177.0 | +2.5 % of pe_line (+4.6K µm, one per chip) | −0.2K |
| + SYNC_BYPASS | pe_chip | — | 13 flops and muxes beside the synchroniser | +1.26K (yosys) |
| + WAIT kind 7 (own selector) | core | 737.7 / 722.8 / 705.9 | +2.5 % | +1.3K |
| + CALL / RET | core | 740.5 / 724.5 / 705.4 | +0.2 % | −0.5K |
| kind 7 on the ALU's selector (v2 as committed) | core | 732.6 / 715.5 / 700.6 | −1.0 %; **+1.6 % vs main** | +0.3K |
| (not done) 32-bit CRC, poly top half fixed at 0x04C1 | core | 746.6 / 735.0 / 721.2 | +1.5 % over CALL/RET | +1.2K |

So the committed v2 adds about 23K µm of wire across the two cores and 4.6K in
the line unit, about 1.2 % of g4's 2.29M µm global route, and the core's
Metal3 use rises by about 0.9 points (44.3 → 45.3 % in these runs). That's the
kind of change that moved g4-era routing a lot, so it needs the full harden.

The 32-bit CRC was left out: it costs about as much wire as the committed
kind 7 (+1.5 % per core), and the
firmware half doesn't fit. To check a 10BASE-T FCS in the engine, `eth10_rx`
must restart the CRC right after the SFD. The cheap way to find it (preset
the CRC to the fixed point of the preamble's `10` pairs, 0xFE9506DB, and wait
for it to change) needs pair alignment, but receivers can lose the first
preamble bits (test_eth10 allows it); a bit-by-bit SFD search costs about 7
instructions per bit against a 5-clock bit. The host already checks the FCS.

## Signoff runs

| Run | RTL | DRT (final) | KLayout DRC | LVS | Antenna | Setup WS typ / slow | Hold WS typ | Max slew / cap viol. (typ) |
|-----|-----|-------------|-------------|-----|---------|---------------------|-------------|----------------------------|
| s2c | 248eae0, 4-deep FIFOs | 0 | 0 | clean | 0 | +2.79 / −7.64 ns* | +0.29 ns | 50 / 44 |
| final4 | 6a563dc: 8-deep FIFOs, registered CTRL, FIFO join; post-GRT repair, 400 µm max wire | 0 (19 iterations) | 0 | clean | 0 | +3.19 / −4.93 ns* (−7.12 ns with the slow SRAM view) | +0.19 ns | 9 / 10 |
| final5 | bd2d6d0: + FIFO read snapshot, pe_fifos; PVT-matched SRAM views | 0 | 0 | clean | 0 | +3.12 / −6.90 ns | +0.21 ns (fast +0.05) | 3 / 11 |
| t3 | 0ceca69: + line unit, quad host port, write-only configuration; measured wire capacitance, post-GRT timing repair | 0 | 0 | clean | 0 | +4.56 / −4.64 ns | +0.15 ns (**fast −0.024**, 1 path) | 3 / 2 |
| t5 | t3 + post-GRT hold margin 0.25 ns | 0 | 0 | clean | 0 | +4.77 / −4.10 ns | +0.32 ns (fast +0.12) | 1 / 2 |
| g4 | a907d4a: fixed banks, latch FIFOs, dual-edge line receiver, host-port reset guard; PnR latch SDC, hold uncertainty 0.1 ns, all three corners repaired, `GRT_ADJUSTMENT` 0.10, density 44 | 0 | 0 | clean | 0 | 0 (met) / −2.88 ns | +0.15 ns (slow +0.30, fast +0.05) | 2 / 0 |
| m10 | g4's RTL, post-GRT hold margin 0.10 ns | 0 | 0 | clean | 0 | 0 (met) / −2.98 ns | +0.17 ns (slow +0.32, fast +0.08) | 0 / 0 |
| v2c | cbeae96: + NRZI half-cycle sample, `SYNC_BYPASS`, WAIT kind 7, CALL/RET; density 34, post-GRT hold margin 0.10 ns | 0 | 0 | clean | 0 | **0 (met) / −1.57 ns** | +0.10 ns (slow +0.20, fast +0.04) | 0 / 0 |
| **t48ba** | v2 RTL (digest cd25b1c1…), software 93c2137; SRAM clock advance, pin registers 6 clock stages late, clock-fanout fix, PnR setup margin, SRAM pin buffers, repair with electrical margins, post-GRT hold margin 0.13 ns | 0 | 0 | clean | 0 | **0 (met) / 0 (met)** | +0.19 ns (slow +0.36, fast +0.095) | **0 / 0** (1 fanout, see below) |
| **fo6** | t48ba + `Project.DetailedRouting` (antenna diodes within the fanout limit), commit f6b8ca0 | 0 | 0 | clean | 0 | **0 (met) / 0 (met)** | +0.19 ns (slow +0.36, fast +0.095) | **0 / 0**, 0 fanout at every corner |

**Physical candidate: fo6** (netlist sha256
`a94a948b70591b221be4e272ab4aaaa9a2f636e073676fa98657e12475330f81`, GDS
`e67697034da6cd34acb16c7ab6c0f85dc6d752fdc755439e6bdd21d872ac0707`;
runner1:~/protoemu-flow/protoemu-exp-fo6). t48ba plus one buffer that keeps
net3483 within the fanout limit; `flow/release_check.py` passes every
timing and electrical item at every corner and lacks only the GL, SDF and
precheck receipts for this netlist.

**Previous candidate: t48ba** (netlist sha256 `e400fa30c291f653…`, GDS
`8899bc154fc3975a…`; 47 % stdcell utilization, 53 % with the macros;
global routing ended with 12 overflows, Metal3 63.9 %). Setup is met at
20 ns at all three corners (the 0 is the FIFO latches' borrowing floor; every
flop and SRAM path has positive slack), hold at every corner, and no slew or
capacitance violation anywhere. A clean checkout of 93c2137 hardened with its
container capped to 4 CPUs and 16 GB (run ci6, 1 h 57 min) reproduced the
netlist exactly and the GDS exactly apart from its timestamps.
`flow/release_check.py` verifies the receipts for this netlist (precheck,
29/29 top-level tests at gate level, the SDF standard and v2 sets at
typ/slow/fast plus their overclocked controls, autonomous full-speed USB
enumeration with real delays) and fails on one item: net3483, driven by
`wire3483`, has 11 loads against the cells' fanout limit of 8 at every
corner: 7 cell inputs and 4 antenna diodes the router added. Its
capacitance and slew are within limits. The archive of all receipts and
inputs is described in [release-evidence.md](release-evidence.md).

v2c was the previous candidate (netlist sha256 `929524418e3d4227…`, GDS
`6d9787524c08a854…`; 44 % stdcell utilization, 50 % with the macros; global
routing ended with 8 overflows, Metal3 63.8 %). It passes Tiny Tapeout's
precheck and all 24 top-level tests at gate level. Two more placements of the
same RTL passed too: v2a (density 44: slow −1.82 ns, fast hold +0.03 ns) and
v2b (wire-length coefficient 0.2: slow −1.53 ns, fast hold +0.04 ns, 3/1
slew/cap exceptions). The v2 features cost about 1.6 % more wire per core
(table above), yet all three v2 runs routed with fewer global-routing
overflows than g4 (8–13 against 39): at this congestion the placement varies
more than the netlist does.

g4 was the previous candidate (netlist sha256 `4db31a01877b1977…`, GDS
`91ebc9ce59147f04…`; 44 % stdcell utilization, 50 % with the macros). Its
build is reproducible on a GitHub runner's budget: a fresh `flow/harden.sh` of
the same tree with its container capped to 4 CPUs and 16 GB (a public-repo
runner; the official action uses the same tt-support-tools, PDK and LibreLane
revisions) finished in 1 h 56 min, passed signoff, and produced the same
netlist and, with GDS timestamps zeroed, a byte-identical GDS. Tiny
Tapeout's precheck (`tt/precheck`) passes on it. Its typ
setup slack is exactly 0 because the FIFO latches borrow time; every flop path
meets. Its slow corner allows about 44 MHz. m10 is g4's RTL re-hardened with
the post-GRT hold margin raised from 0.05 to 0.10 ns: fast-corner hold +0.08 ns
and no typ slew exceptions, which is why v2 uses 0.10. t5 is historical hardware at `ae7d59d`, with its own configuration. Its retained
gate-level log passes 9/9 top-level tests. The slow corner allows
about 41 MHz. final5's DOUT capacitance exceptions are gone; t5 has one
slew and two capacitance exceptions at typ.

t3's one fast-corner hold violator (a core 0 configuration flop behind its
clock gate, to a flop on the main tree) had +0.150 ns at typ; fast-corner hold
isn't repaired (PNR_CORNERS), so post-GRT hold repair now keeps 0.25 ns at typ
(43 endpoints were below that).

\* The s2c and final4 runs gave every STA corner the typical SRAM timing view (the macro's
`lib` map was keyed `nom_*`/`min_*`/`max_*`, and every corner is `nom_*`), so
their slow and fast numbers are mixed-corner. Re-running STA on final4 with
PVT-matched views (`flow/signoff.py` now prints the view per corner): typ
unchanged; fast hold +0.034 ns, met (the worst hold paths are flop to flop);
slow setup −7.12 ns, i.e. about 37 MHz at the slow corner. Fixed in 32d4480.

### s2c (historical)

* The slow corner missed 50 MHz by 7.6 ns (444 endpoints). TT signs off at
  typ, so 50 MHz at typ is the target.
* **275 of the violators started at the host bus address.** A `CTRL` write
  decoded `restart` combinationally, and `restart` gates `active` → `exec`,
  i.e. the whole execute stage of every core, plus `fetch_addr` into the SRAM
  address pins. Since eb70fee the CTRL write goes through flops first
  (docs/isa.md §5; 13 flops, invisible to the SPI host). The remaining
  violators start at the SRAM outputs (fetch → decode → execute in one cycle,
  worst −5.5 ns, all from the far-left bank).
* The worst slow path is long logic plus **weak cells driving long wires**,
  e.g. a `nor2b_1` driving 0.33 pF with a 4.2 ns slew. Post-placement repair
  sizes cells from estimated wire loads, and the Classic flow does not repair
  again after global routing (`RUN_POST_GRT_DESIGN_REPAIR` is off). The same
  wires cause the slew/cap violations, including SRAM input pins above the
  macro's 0.476 ns transition limit.

### final4

* `--post-grt-repair --max-wire 400`: repair after global routing
  buffered 551 long wires (779 buffers) and cut the typ slew/cap violations
  from 50/44 to 9/10. Every slow-corner violator now starts at an SRAM output
  (fetch → execute; 346 of 369 from the far-left bank). The rest: one
  `nor4_1` decode net (4.8 ns slew, off the typ-critical path) and SRAM DOUT
  pins slightly over the macro's 0.064 pF output-load limit. Utilization is
  40 % stdcell, 47 % with the macros.

## Gate-level simulation

* TT simulates `runs/wokwi/final/nl/*.nl.v` for IHP (`tt/tech.py`:
  `netlist_type = "nl"`), with no power ports. The IHP cell models have no
  power pins either, so the testbench connects `VPWR`/`VGND` only under
  `USE_POWER_PINS`.
* `sg13cmos5l_stdcell.v` needs `sg13cmos5l_udp.v` (the `ihp_mux2`/`ihp_mux4`
  primitives) in the source list.
* `flow/gl_test.sh <run dir>` copies the netlist and runs `make GATES=yes`
  with the run's core count and FIFO depth.
* Historical logs report s2c 6/6 and final4 **7/7** (ID, UART TX/RX, SPI,
  I2C, USB low-speed device, 10BASE-T TX with FIFO join). They do not validate
  the current hardware or satisfy the fresh-result checks now required.
* Run g4's final netlist passes all 20 top-level tests (main 16, compiled
  PIO 2, Ethernet capture 2), functional, no SDF.

### Timing-annotated gate level (SDF)

`flow/gl_sdf_test.sh <run dir>` simulates the same final netlist
back-annotated with the run's own extracted SDF (`final/sdf/nom_<corner>_*`,
written by OpenSTA from the SPEF), using the timing views of the PDK cell
and SRAM models (no `FUNCTIONAL`), Icarus Verilog with `-gspecify
-ginterconnect`, and this checkout's `test/test.py` with its clock set by
`PE_CLK_NS`. Each case gets a fresh directory under
`test/sim_build/gl_sdf.*`; the run directory is only read, and the script
refuses a run whose `src/` differs from the checkout's. The external devices
on the uio pins drive 4 ns after the clock edge at which cocotb sets them
(`TB_EXT_DELAY`, the input delay STA assumes; see the limits below). The
standard cases run in parallel:

| Corner | Clock | Tests | Required | g4 | v2c |
|--------|-------|-------|----------|----|-----|
| typ | 20 ns (50 MHz) | test_id, test_uart_tx, test_spi_master, test_host_port_fast, test_fetch_to_pin | pass | 5/5 pass | 5/5 pass |
| slow | 23 ns (43.5 MHz; STA needs 22.88 for g4, 21.57 for v2c) | same | pass | 5/5 pass | 5/5 pass |
| slow | 12.5 ns (80 MHz) | test_fetch_to_pin | fail | fails | fails |
| slow | 20 ns (single case, not in the standard set) | the five | — | 5/5 pass (see below) | |
| fast | 20 ns (single case) | the five | — | 5/5 pass | |

```
$ flow/gl_sdf_test.sh ~/dev/protoemu-exp-g4        # before the 4 ns external-device delay
[typ_20ns_pass] PASS as required: ** TESTS=5 PASS=5 FAIL=0 SKIP=0   933950.00   498.61   1873.10 ** (537 s)
[slow_23ns_pass] PASS as required: ** TESTS=5 PASS=5 FAIL=0 SKIP=0  1074042.50   455.90   2355.88 ** (495 s)
[slow_12.5ns_fail] FAILS as required: ** TESTS=1 PASS=0 FAIL=1 SKIP=0  110918.75   342.31   324.03 ** (382 s)
gl_sdf_test: all cases as required        (9 min wall on a loaded 16-core box)
$ flow/gl_sdf_test.sh ~/dev/protoemu-exp-v2c
[typ_20ns_pass] PASS as required: ** TESTS=5 PASS=5 FAIL=0 SKIP=0  933950.00  780.44  1196.70 ** (851 s)
[slow_23ns_pass] PASS as required: ** TESTS=5 PASS=5 FAIL=0 SKIP=0  1074042.50  756.60  1419.57 ** (831 s)
[slow_12.5ns_fail_fetch_to_pin] FAILS as required: ** TESTS=1 PASS=0 FAIL=1 SKIP=0  109331.25  536.63  203.74 ** (609 s)
gl_sdf_test (standard): all cases as required        (14 min wall, load average ~28 on 16 cores)
```

One case is `flow/gl_sdf_test.sh <run> CORNER CLK_NS [fail|report]`
(`report` prints the result without requiring either); `TESTS=` picks
other tests and `MODULE=test_sigrok` the sigrok oracle (sigrok_uart_tx,
sigrok_spi_master and sigrok_i2c_master pass at typ, 50 MHz). Most of a
case's ~4 minutes is Icarus reading the SDF; the simulation itself runs at
roughly 2 µs of chip time per second.

**The v2 features** (`flow/gl_sdf_test.sh <run> v2`, five cases in
parallel, ~30 minutes, the USB cases set it). Two of the tests are short
directed ones written for this (their RTL runs take seconds; full-length
originals in `v2long` below):

| Feature | Test | What it checks |
|---------|------|----------------|
| `SYNC_BYPASS` | test_spi_master_fast | SPI master at 4 clocks per SCK reads every byte with MISO bypassed; without the bypass (or with spi_master) every bit one late |
| WAIT kind 7 | test_la_capture_ticks (new, 0.2 ms) | la_capture at CLKDIV 1 on three channels: 32 pin changes (spaced 7-30 ticks, the 7-tick square wave, two-channel pairs 1-6 ticks apart, 1- and 2-clock pulses); decoded edges and pulses equal the cycle model's, spaced edges exact to the tick |
| CALL/RET | test_call_ret (new, 0.26 ms) | conditional calls taken and not, nesting through a saved LR, a CALL on the wrap's last word, RET with side-set and delay, CLKDIV 1: side-set edges clock-exact, pushed links and final PC/status/R0-R3 equal the model's (kills `rtl-call-link-ignores-wrap` and `rtl-call-no-link` at RTL) |
| half-cycle NRZI sample | test_usb_fs_device (3.3 ms) | USB full-speed device: SETUP/IN/OUT handshakes, turnaround, 2 × 24 OUT packets at DCD 9.25 and 14 ns |

| Corner | Clock | Tests | Required | v2c |
|--------|-------|-------|----------|-----|
| typ | 20 ns | spi_master_fast, la_capture_ticks, call_ret | pass | 3/3 pass (690 s) |
| typ | 20.834 ns (48 MHz, USB's clock) | usb_fs_device | pass | pass (1823 s) |
| slow | 23 ns | spi_master_fast, la_capture_ticks, call_ret | pass | 3/3 pass (641 s) |
| slow | 23 ns, USB bus scaled to the clock | usb_fs_device | pass | pass (1797 s) |
| slow | 20.834 ns (48 MHz) | usb_fs_device | report only (STA needs 21.57 ns) | passes (1877 s) |
| slow | 12.5 ns (80 MHz, single cases) | each of the three short tests | report only | each fails (spi_master_fast: X on a pin; la_capture_ticks: gaps; call_ret: no side-set edges) |

```
$ flow/gl_sdf_test.sh ~/dev/protoemu-exp-v2c v2
[typ_20ns_pass] PASS as required: ** TESTS=3 PASS=3 FAIL=0 SKIP=0  1043820.00  633.88  1646.72 ** (690 s)
[typ_20.834ns_pass_usb_fs_device] PASS as required: ** TESTS=1 PASS=1 FAIL=0 SKIP=0  3264021.11  1770.75  1843.30 ** (1823 s)
[slow_23ns_pass] PASS as required: ** TESTS=3 PASS=3 FAIL=0 SKIP=0  1200393.00  583.22  2058.21 ** (641 s)
[slow_23ns_pass_usb_fs_device] PASS as required: ** TESTS=1 PASS=1 FAIL=0 SKIP=0  3602904.00  1741.99  2068.27 ** (1797 s)
[slow_20.834ns_report_usb_fs_device] PASSES (report only): ** TESTS=1 PASS=1 FAIL=0 SKIP=0  3264021.11  1823.60  1789.88 ** (1877 s)
gl_sdf_test (v2): all cases as required        (31 min wall, load average ~28 on 16 cores)
```

The 80 MHz cases show the annotation is in effect for these tests too, not
that they target the feature's own paths: at that clock much more breaks
(test_call_ret never saw a side-set edge).

USB full speed needs exactly 48 MHz, which the slow corner doesn't meet in
STA (−1.57 ns at 20 ns, so 21.57 ns); the slow/48 MHz case is reported, not
required. It passes, as g4's five passed at slow/50 MHz (below): a pass
shows only that the transitions the test exercises met timing, not that the
184 failing endpoints are unreachable. For a required slow
case, `PE_USB_FS_CLK_NS` (set to the case clock) runs the same test with the
host's bit time and DCD scaled to the clock, still 4 clocks per bit (10.9
Mbit/s at 23 ns: the same logic, not USB). At gate level the turnaround is
2.89 bit times for the ACK (RTL 2.64: the 4 ns input delay moves the EOP's
arrival past one more clock edge for that packet's phase); 24/24 OUT packets
are ACKed at each DCD, at every clock.

`flow/gl_sdf_test.sh <run> v2long` runs the full-length tests the directed
ones stand in for, each its own case, at typ 20 ns and slow 23 ns:
test_la_capture_rate (3.2 ms of chip time: the rate sweeps and their exact
burst-rate assertions) and test_reverse_engineering (5.4 ms: la_capture on
I2C, then i2c_device, which uses CALL/RET, cloning the EEPROM). v2c passes
all four (about 50 minutes each under the same load), with the RTL's
results: gap-free capture down to an edge every 80 clocks (1-bit SPI), 12
(quad, 8-word bursts) and 9 (32-word bursts) at both corners, and the clone
answers the same with SCL stretched 2935 clocks.

```
$ flow/gl_sdf_test.sh ~/dev/protoemu-exp-v2c v2long
[typ_20ns_pass_la_capture_rate] PASS as required: ** TESTS=1 PASS=1 FAIL=0 SKIP=0  3155480.00  2902.85  1087.03 ** (2958 s)
[typ_20ns_pass_reverse_engineering] PASS as required: ** TESTS=1 PASS=1 FAIL=0 SKIP=0  5431940.00  2900.97  1872.46 ** (2955 s)
[slow_23ns_pass_la_capture_rate] PASS as required: ** TESTS=1 PASS=1 FAIL=0 SKIP=0  3628802.00  2949.92  1230.14 ** (3006 s)
[slow_23ns_pass_reverse_engineering] PASS as required: ** TESTS=1 PASS=1 FAIL=0 SKIP=0  6246731.00  2944.59  2121.42 ** (3002 s)
gl_sdf_test (v2long): all cases as required
```

**A testbench race the v2 tests exposed.** test_spi_master_fast first
failed at typ/50 MHz: with MISO not bypassed it read every byte right
instead of one bit late. Its device model answers at the clock edge after it
sees SCK fall, and cocotb set MISO at that edge with no delay. STA (v2c,
typ) gives the pad-to-first-synchronizer path `uio_in[4]` → `input13` →
`_37309_/D` as 0.12 ns and that flop's clock latency as 0.85 ns (slow: 0.19
and 1.29 ns): a pin change made at the clock port's edge reaches D before
the delayed edge, so the synchronizer takes it at that edge, a clock earlier
than RTL. That is outside what STA signs off: the SDC sets 4 ns of input
delay (`IO_DELAY_CONSTRAINT` 20 %), and with it the path has 3.22 ns of hold
slack. The testbench now delays the external devices' outputs by 4 ns under
SDF (`tb.v`, `TB_EXT_DELAY`, a transport delay; RTL is unchanged with it:
the v2 tests and test_reverse_engineering pass with the delay compiled in),
and the test passes at typ and slow. No chip finding: the bypass itself (the
positive check) passed either way, and test_usb_fs_device, whose host
places edges at random phase, passed at typ without the delay as well.

**Getting Icarus to annotate this netlist.** Out of the box, Icarus 14
(devel) silently annotated only part of it: its SDF reader stops at the first
syntax error (the SRAM's bit-selected `IOPATH A_CLK A_DOUT[0]`, which left
both SRAMs and 832 buffers after them unannotated), cannot resolve the
netlist's escaped hierarchical names, cannot place a net delay on one bit of
a vector net or into an antenna diode, ignores `COND` entries, and needs over
15 minutes for 62k `INTERCONNECT`s. `flow/sdf_prep.py` writes private copies
of the netlist and SDF that avoid all of this (its docstring has the
details): renamed identifiers, split vector wires, dropped TIMINGCHECKs and
dead-end diode delays; net delays into cell inputs folded into the sink
cell's IOPATHs (adding the larger of the wire's rise/fall, at most 6 ps
apart in g4); each SRAM's 32 per-bit clock-to-output delays merged into one
(their max), and each arc's unconditional delay raised to the slowest of its
COND variants (up to +0.11 ns on mux selects), both pessimistic. The script
fails on any `SDF ERROR` (except the header's empty typ voltage and
temperature fields) or VPI error in the log. Verilator was not needed (it
does not read SDF).

**Is the annotation real?** In the slow-corner waveform (g4, test_fetch_to_pin
at 20 ns), arrival times along the start of STA's worst path follow the STA
report. Core 1's instruction bit 29 (bank 1 SRAM output) changes 6.60 ns
after the clock edge (STA: 6.91 ns), and the next two nets change at 6.91
and 7.21 ns (STA, at their sinks: 7.26 and 7.56 ns), so each stage adds the
same ~0.3 ns. The constant 0.3 ns offset is the SRAM's own arc: the STA
report gives 5.52 ns from A_CLK to DOUT[29], while the SDF OpenSTA wrote in
the same step gives 5.26 ns (we haven't found why; the simulation uses the
SDF). The clock-to-uio5 delay is 1.71/2.07 ns (rise/fall) at typ and
2.63/3.20 ns at slow. test_fetch_to_pin passes at slow down to 14.25 ns and fails at
13.5 ns; at typ it passes at 9 ns and fails at 8.5 ns: the ratio, ~1.56,
is the slow/typ delay ratio. Functional GL and RTL pass it at any period.

**Why the slow corner passes these tests at 50 MHz.** STA reports −2.88 ns
there (worst path: bank 1 SRAM DOUT[29] through ~35 gates to the pin output
register `pin_out_cur[5]`, arriving at 23.40 ns against 20.51 required). No
test sensitizes that path: test_fetch_to_pin writes that pin from that bank
every cycle with SET, PIN, MOV and OUT, yet the nets past the eighth gate
of the path toggle only during setup, and the endpoint's D input settles by
14.05 ns. So the five tests pass at slow corner and 50 MHz, and the
deliberate failure needs 12.5 ns. A passing timing simulation shows only that
the exercised transitions meet timing; STA remains the signoff, and the
slow-corner chip is specified at 43.5 MHz.

**What SDF GL with Icarus checks, and what it doesn't.**

* Checks: the mapped logic with every cell and net delay STA computed for
  that corner, including clock-tree skew, the SRAM's clock-to-output, the
  clock gates and latch FIFOs, and the SCK domain and host-port synchronizers
  at their real delays; a value that arrives after its capturing clock edge
  on an exercised transition gives a wrong result.
* Doesn't check timing windows: Icarus implements no timing checks
  (`$setuphold`, `$width`, recovery/removal; it warns "Timing checks are not
  supported"), so a flop samples D at its delayed clock edge with a zero-width
  window. Setup and hold violations smaller than the library's setup/hold
  times pass silently, and metastability isn't modelled (the 0.25 ns clock
  uncertainty STA adds isn't there either). Those stay STA's job, including
  the hold margins (fast corner +0.05 ns).
* Only the paths the tests exercise, with the data they use (above).
* Approximations, all small and mostly pessimistic: the merged SRAM bits and
  COND variants, the folded net delays, and Icarus dropping the cell models'
  `ifnone` edge-sensitive arcs (68 of them in 17 cell types; their
  conditional arcs, which get the unconditional SDF delay, still cover every
  0/1 input, so only X inputs are affected).
* Board timing, partly: the external devices on uio drive 4 ns after the
  clock edge (STA's input delay; above), but the host port's ui inputs are
  set at cocotb's own times (SCK/MOSI/CS_n go through synchronizers, so any
  phase is legal there), and no output delay is modelled (the testbench
  reads outputs at the next clock edge or at its own SPI timing).
* Features: the v2 set covers each v2 feature with one test (the
  half-cycle NRZI sample at the spec's DCD and beyond, `SYNC_BYPASS` on one
  pin at 4 clocks per SCK, WAIT kind 7 at CLKDIV 1 on three channels,
  CALL/RET in one program on one core). Kind 7 on fractional dividers, host
  restarts mid-WAIT or mid-CALL and the bypass on other pins are left to
  RTL lockstep (test_wait_diff, test_call_ret in `test_lockstep.py`, which
  probe internal state and so don't run on the netlist).

## Area reductions applied

* Clock-gated FIFO storage (one `lgcp` per entry instead of a hold mux per bit):
  −4.9K µm² per core.
* Latch FIFO storage (2026-10-04): each push lands in a clock-gated staging
  register, and the entry's clock gate opens its latches in the next half
  cycle. `dlhq_1` (31 µm², no reset) replaces a `dfrbpq_1` (49 µm², IHP's flops
  all have a reset) plus its tie cell (7 µm²). A first version used the
  resettable `dlhrq_1`: smaller, but its 1024 reset pins joined the reset net
  (1394 → 2432 loads) and the chip (t7) congested although 18K µm² smaller;
  entries are only read after being written, so they need no reset. STA reports the latch inputs as
  time borrowed with 0 slack, so the resizer's setup margin is 0 (any margin
  made it chase 512 unfixable endpoints per core). Instantiating the cell
  matters: an inferred latch with a reset folded the reset into D and the
  enable.
* Clock-gated host-only configuration registers (core and chip).
* Shared datapath: one funnel shifter for IN/OUT, and one rotator for all pin writes.
