# Physical design notes (IHP SG13CMOS5L, Tiny Tapeout 6x4)

A running log of what the flow taught us. The numbers come from local LibreLane
3.1.0.dev3 runs. `flow/harden.sh NAME` hardens HEAD in its own worktree and
writes a signoff summary (`flow/signoff.py`); `flow/gl_test.sh` runs the
gate-level tests on the result. The routability experiments below used a
flop-IMEM script stopped after global routing (`flow/exp.sh`, removed after
bd07aa5; see git history).

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
* Hold repair across all corners inserted about 3200 buffers and broke detailed
  placement. TT signs off at typ, so `PNR_CORNERS = [typ, slow]` is used.
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

The core's own instruction → next-fetch-address logic is 13.9 ns at the slow
corner (standalone, before repair). Computed jumps (MOV/OUT to PC) put the
source mux and the funnel shifter on it; fetching their target a cycle later
(docs/isa.md §4.2) makes it 11.6 ns.


* 50 MHz at the typical corner is comfortable. The flop build has +8.9 ns setup
  slack after CTS, the SRAM build +8.4 ns, and the SRAM build also meets 50 MHz
  at the slow corner (+1.9 ns).

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

## Signoff runs

| Run | RTL | DRT (final) | KLayout DRC | LVS | Antenna | Setup WS typ / slow | Hold WS typ | Max slew / cap viol. (typ) |
|-----|-----|-------------|-------------|-----|---------|---------------------|-------------|----------------------------|
| s2c | 248eae0, 4-deep FIFOs | 0 | 0 | clean | 0 | +2.79 / −7.64 ns* | +0.29 ns | 50 / 44 |
| final4 | 6a563dc: 8-deep FIFOs, registered CTRL, FIFO join; post-GRT repair, 400 µm max wire | 0 (19 iterations) | 0 | clean | 0 | +3.19 / −4.93 ns* (−7.12 ns with the slow SRAM view) | +0.19 ns | 9 / 10 |
| **final5** | bd2d6d0: + FIFO read snapshot, pe_fifos; PVT-matched SRAM views | 0 | 0 | clean | 0 | **+3.12 / −6.90 ns** | +0.21 ns (fast +0.05) | 3 / 11 |

final5 is the current hardware. Its gate-level simulation passes all 9
top-level tests. The slow corner allows about 37 MHz. The capacitance
exceptions are mostly SRAM DOUT pins slightly over the macro's 0.064 pF
output-load limit.

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
* Results: s2c 6/6 and final4 **7/7** (ID, UART TX/RX, SPI, I2C, USB
  low-speed device, 10BASE-T TX with FIFO join) on the routed netlist.

## Area reductions applied

* Clock-gated FIFO storage (one `lgcp` per entry instead of a hold mux per bit):
  −4.9K µm² per core.
* Clock-gated host-only configuration registers (core and chip).
* Shared datapath: one funnel shifter for IN/OUT, and one rotator for all pin writes.
