# Physical design notes (IHP SG13CMOS5L, Tiny Tapeout 6x4)

A running log of what the flow taught us. The numbers come from local LibreLane
3.1.0.dev3 runs (`flow/exp.sh` for routability experiments).

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

## Signoff runs

| Run | RTL | DRT (final) | KLayout DRC | LVS | Antenna | Setup WS typ / slow | Hold WS typ | Max slew / cap viol. (typ) |
|-----|-----|-------------|-------------|-----|---------|---------------------|-------------|----------------------------|
| s2c | 248eae0, 4-deep FIFOs | 0 | 0 | clean | 0 | +2.79 / −7.64 ns | +0.29 ns | 50 / 44 |
| **final4** | 6a563dc: 8-deep FIFOs, registered CTRL, FIFO join; post-GRT repair, 400 µm max wire | 0 (19 iterations) | 0 | clean | 0 | **+3.19 / −4.93 ns** | +0.19 ns | 9 / 10 |

* The slow corner misses 50 MHz by 7.6 ns (444 endpoints). TT signs off at typ,
  so 50 MHz at typ (or about 35 MHz at the slow corner) is the stated target.
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

* final4 (`--post-grt-repair --max-wire 400`): repair after global routing
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

## Area reductions applied

* Clock-gated FIFO storage (one `lgcp` per entry instead of a hold mux per bit):
  −4.9K µm² per core.
* Clock-gated host-only configuration registers (core and chip).
* Shared datapath: one funnel shifter for IN/OUT, and one rotator for all pin writes.
