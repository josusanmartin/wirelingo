# Physical design notes (IHP SG13CMOS5L, Tiny Tapeout 6x4)

A running log of what the flow taught us. The numbers come from local LibreLane
3.1.0.dev3 runs. `flow/harden.sh NAME` hardens HEAD in its own worktree and
writes a signoff summary (`flow/signoff.py`); `flow/gl_test.sh` runs the
gate-level tests on the result. The routability experiments below used a
flop-IMEM script stopped after global routing (`flow/exp.sh`, removed after
bd07aa5; see git history).

**Status:** run **g4** (2026-10-05, the current RTL: fixed banks, latch FIFOs,
the dual-edge line receiver and the host-port reset guard) passes signoff; see
the table at the end. A run is a commit:
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

## Routability is the constraint (not cell area)

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
| **g4** | a907d4a: fixed banks, latch FIFOs, dual-edge line receiver, host-port reset guard; PnR latch SDC, hold uncertainty 0.1 ns, all three corners repaired, `GRT_ADJUSTMENT` 0.10, density 44 | 0 | 0 | clean | 0 | **0 (met) / −2.88 ns** | +0.15 ns (slow +0.30, fast +0.05) | 2 / 0 |

g4 is the current candidate (netlist sha256 `4db31a01877b1977…`, GDS
`91ebc9ce59147f04…`; 44 % stdcell utilization, 50 % with the macros). The
build is reproducible on a GitHub runner's budget: a fresh `flow/harden.sh` of
the same tree with its container capped to 4 CPUs and 16 GB (a public-repo
runner; the official action uses the same tt-support-tools, PDK and LibreLane
revisions) finished in 1 h 56 min, passed signoff, and produced the same
netlist and, with GDS timestamps zeroed, a byte-identical GDS. Tiny
Tapeout's precheck (`tt/precheck`) passes on it. Its typ
setup slack is exactly 0 because the FIFO latches borrow time; every flop path
meets. The slow corner allows about 44 MHz. t5 is historical hardware at `ae7d59d`, with its own configuration. Its retained
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
refuses a run whose `src/` differs from the checkout's. The standard cases
run in parallel:

| Corner | Clock | Tests | Required | g4 |
|--------|-------|-------|----------|----|
| typ | 20 ns (50 MHz) | test_id, test_uart_tx, test_spi_master, test_host_port_fast, test_fetch_to_pin | pass | 5/5 pass |
| slow | 23 ns (43.5 MHz; STA needs 22.88) | same | pass | 5/5 pass |
| slow | 12.5 ns (80 MHz) | test_fetch_to_pin | fail | fails |
| slow | 20 ns (single case, not in the standard set) | the five | — | 5/5 pass (see below) |
| fast | 20 ns (single case) | the five | — | 5/5 pass |

```
$ flow/gl_sdf_test.sh ~/dev/protoemu-exp-g4
[typ_20ns_pass] PASS as required: ** TESTS=5 PASS=5 FAIL=0 SKIP=0   933950.00   498.61   1873.10 ** (537 s)
[slow_23ns_pass] PASS as required: ** TESTS=5 PASS=5 FAIL=0 SKIP=0  1074042.50   455.90   2355.88 ** (495 s)
[slow_12.5ns_fail] FAILS as required: ** TESTS=1 PASS=0 FAIL=1 SKIP=0  110918.75   342.31   324.03 ** (382 s)
gl_sdf_test: all cases as required        (9 min wall on a loaded 16-core box)
```

One case is `flow/gl_sdf_test.sh <run> CORNER CLK_NS [fail]`; `TESTS=` picks
other tests and `MODULE=test_sigrok` the sigrok oracle (sigrok_uart_tx,
sigrok_spi_master and sigrok_i2c_master pass at typ, 50 MHz). Most of a
case's ~4 minutes is Icarus reading the SDF; the simulation itself runs at
roughly 2 µs of chip time per second.

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
* The testbench drives inputs at clock edges (the host port's SCK/MOSI/CS_n
  go through synchronizers, so any phase is legal there); the board-level
  input/output delays STA constrains aren't modelled.

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
