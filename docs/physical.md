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

## Area reductions applied

* Clock-gated FIFO storage (one `lgcp` per entry instead of a hold mux per bit):
  −4.9K µm² per core.
* Clock-gated host-only configuration registers (core and chip).
* Shared datapath: one funnel shifter for IN/OUT, and one rotator for all pin writes.
