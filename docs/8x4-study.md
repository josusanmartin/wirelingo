# 8x4 study: what hardware to add with the extra area

Backlog item AN, branch `study-8x4` (from the public `main`), 2026-11-09.
On 2026-11-09 the organizers confirmed 8x4 tiles for the March 2027 IHP
SG13CMOS5L shuttle, and the author chose to spend the area on new hardware.
This is the study that comes before any RTL: it recommends what to add, and
the author picks. **No file in `src/`, `flow/` or `firmware/` changes here.**
Prototypes live in [`syn/8x4-study/`](../syn/8x4-study/).

Every number below names its evidence level:

* **harden**: a LibreLane run through Tiny Tapeout's flow on runner1, with
  the same settings as the signed-off run fo6 except the tile;
* **global route**: the same flow stopped after global routing, for
  routability only;
* **synth**: yosys + abc to the IHP typical library at chip level
  (`syn/8x4-study/synth_chip.sh`), SRAMs as black boxes, no placement;
* **estimate**: arithmetic on the above, labelled as such;
* **model**: the Python cycle model.

Prototype RTL (`pe_core_x.sv`, `pe_chip_x.sv`) was written to count cells
and to try routing. It was never simulated or verified, and none of it is
proposed as written.

## Bottom line

* **Recommended package: a third core with its own SRAM bank, plus four
  additions inside each core**: a CRC tap of any width to 32 bits with a
  programmable polynomial (on-chip 10BASE-T FCS, CAN FD and FlexRay CRCs),
  the v3 bundle (table read, CMPW, `jmp !t`, `flag tog`, bank-relative PC),
  and a free-running tick counter. Synthesized +125.5K µm² (+43 %);
  utilization **estimate** 50.7 % on 8x4. Hardened once as unverified
  prototype RTL (`pkg3`): it routes clean (51.1 %) and, untuned, fails
  signoff (slow setup −1.57 ns, hold −0.32 ns, 22 slew violations). It
  needs a settings search like the one that took v2c to fo6, at about
  three hours a run.
* **Fallback: the same four additions on two cores** (+13.6K µm²,
  **estimate** 35.5 %), decided at a Dec 6 gate.
* **Baseline (harden):** today's RTL on 8x4 routes with room to spare
  (Metal3 45.7 % at global routing, against fo6's 63.9 %), but with fo6's
  settings the slow corner misses 50 MHz by 0.643 ns; 48 MHz is met at
  every corner (+0.19 ns); 60 MHz misses by 3.98 ns at the slow corner and
  closes at typical and fast. One step of a settings search (pin clocks 8 buffers late) gained only 0.02 ns. The area relieves routing, not the
  critical path.
* **Left out:** a fourth core (global routing did not converge), a line
  unit for core 1 (its main use, USB and 10BASE-T at once, needs 60 MHz,
  which this chip does not close),
  512-word banks (+1.25 ns on the critical path's first cell), data RAM,
  the multi-bit CRC tap.
* Any 8x4 tapeout needs `flow/` edits (the generator hardcodes the 6x4
  core) that RULES.md does not allow today, and a fresh timing-closure
  search; the plan below has both.

## What changed: the tile

| | 6x4 (fo6, signed off) | 8x4 (Tiny Tapeout's tile, `tt_block_8x4_pgvdd.def`) |
|---|---|---|
| Die | 1289.28 × 710.64 µm | 1724.16 × 710.64 µm |
| Core area | 902,417 µm² | 1,208,170 µm² (+33.9 %) |
| Pins | 43 on Metal4, top edge, x ≈ 30–191 µm | the same 43, same place |
| Routing layers | Metal2 (V), Metal3 (H), Metal4 (V); one horizontal layer | the same |

The die only gets wider. The number of horizontal (Metal3) tracks is set by
the height, which does not change; each track gets 34 % longer. The pins stay
in the top-left corner, so logic placed on the new right-hand side is farther
from them.

## The baseline: today's RTL on 8x4 (harden)

Run `base`: the public `main` RTL (`src/` byte-identical to fo6's run
directory, checked by sha256), fo6's `gen_config.py` defaults, the 8x4 tile,
the two SRAMs in the 8x4 die's bottom corners (x = 4.32 and 1302.72 µm). One
harden on runner1, 43 minutes. Netlist sha256 `982444c9e60151fc…`, GDS
`2d398ea6d90d1fa5…`.

| | fo6 (6x4, signed off) | base (8x4, same RTL and settings) |
|---|---|---|
| Routing DRC / KLayout DRC / LVS / antenna | 0 / 0 / clean / 0 | 0 / 0 / clean / 0 |
| Slew / capacitance violations, every corner | 0 / 0 | 0 / 0 |
| Standard-cell area | 376,827 µm² | 378,074 µm² |
| Standard-cell utilization | 46.9 % | **34.1 %** (39.5 % with the macros) |
| Metal3 at the global-routing step (demand / capacity) | 63.9 % (143.4K / 224.6K) | **45.7 %** (142.7K / 312.4K) |
| Overflow at that step (all layers) | 12 | 0 |
| Routed wire | 1.788M µm | 1.884M µm (+5.3 %) |
| Hold buffers | 233 | 517 |
| Setup WS at 20 ns, typ / slow / fast | 0 / 0 / 0 (met) | 0 / **−0.643 ns** / 0 |
| Slow corner, worst flop or SRAM endpoint at 20 ns | +0.010 ns | −0.643 ns, 90 violating endpoints, TNS −33.8 ns |
| Hold WS, typ / slow / fast | +0.191 / +0.362 / +0.095 ns | +0.158 / +0.362 / +0.059 ns |
| `flow/signoff.py` | PASSED | PASSED (it gates typical setup and hold only) |

(WS 0 is the FIFO latches' borrowing floor, as in physical.md.) Other
clocks, by `syn/8x4-study/sta_period.sh` on the same netlist and extracted
SPEF (STA, not a harden at that clock):

| Clock | typ | slow | fast |
|---|---|---|---|
| 48 MHz (20.833 ns) | met | **+0.190 ns** | met |
| 50 MHz (20 ns) | met | −0.643 ns (90 endpoints) | met |
| 60 MHz (16.667 ns) | met (worst flop +3.75 ns) | **−3.976 ns** (509 endpoints, TNS −1130 ns) | met |

What this says:

* **Routing is no longer the limit.** The same netlist's Metal3 demand is
  within 1 % of fo6's and the capacity is 39 % larger: 45.7 % against
  63.9 % at global routing, no overflow against 12. That is the margin the v3 study did not
  have ("no respin", mainly congestion).
* **The critical path is not helped.** The worst slow-corner path is the one
  physical.md describes: bank 0's `A_DOUT[3]` (5.38 ns clock to output),
  instruction decode, the source select and pin rotators, the pin-owner mux,
  into a pin register (`pin_out_cur[20]`): 58 cells with the repair
  buffers, arriving at 21.86 ns against a required 21.22 ns (the pin
  registers' clock is already six buffers late). It is logic depth, not
  wire. fo6's settings were found over 25–30 placements to meet it by
  +0.010 ns; on the 8x4 placement the same settings miss by 0.64 ns. The
  settings table in physical.md spans −1.6 to +0.24 ns on this path. One
  step of a new search was tried (run `baselc8`: the pin registers' clock
  8 buffers late instead of 6, the change worth +0.27 ns between t48m and
  t48r at 6x4): slow −0.624 ns, 80 endpoints, hold +0.148 / +0.343 /
  +0.047 ns, no slew or capacitance violation, so **+0.02 ns**. 50 MHz at
  the slow corner on 8x4 is therefore not a given. **Any 8x4 tapeout, with
  or without new hardware, needs that search**, and its release receipts
  regenerated.
* **48 MHz has margin** (+0.19 ns at the slow corner): the demos' clock is
  met at every corner on this layout as it stands.
* **60 MHz is out of reach at the slow corner** without an RTL change: 4 ns
  is a fifth of the path. It closes at the typical and fast corners. The
  FAQ says a 60 MHz clock is fine to request; this design can't claim it
  at all corners, and this study does not recommend chasing it (it would
  mean pipelining fetch and decode, an ISA-visible timing change).

## The area budget

`syn/8x4-study/run_all.sh` synthesizes the chip with each candidate;
`syn/8x4-study/budget.py` turns [`results.txt`](../syn/8x4-study/results.txt)
into the table below. The "hardened" column is an **estimate**: synthesized
area × 1.2764, fo6's ratio of hardened standard-cell area (376,827 µm²) to
this script's synthesis of the same RTL (295,218 µm²); the ratio folds in
buffers, clock tree, hold and antenna cells and will differ for another
netlist. Utilization is that estimate over the 8x4 core minus one SRAM
outline (49,488 µm²) per bank. For scale, fo6 signed off at **46.9 %** on
6x4 with Metal3 at 63.9 % at global routing, and that was at the edge:
v2c to t48ba took 25–30 placements ([physical.md](physical.md)).

| Cores | Candidate | Flops | Synth (µm²) | Δ vs today | Hardened, **estimate** | Utilization on 8x4, **estimate** |
|---|---|---|---|---|---|---|
| 2 | today's RTL | 1647 | 295,218 | +0 | 376,827 | 34.0% |
| 3 | today's RTL | 2079 | 400,690 | +105,472 | 511,455 | 48.3% |
| 4 | today's RTL | 2511 | 509,008 | +213,790 | 649,716 | 64.3% |
| 2 | 4-deep FIFOs | 1635 | 269,495 | -25,723 | 343,993 | 31.0% |
| 3 | 4-deep FIFOs | 2061 | 361,390 | +66,172 | 461,291 | 43.5% |
| 4 | 4-deep FIFOs | 2487 | 456,139 | +160,921 | 582,232 | 57.6% |
| 2 | line unit for core 1 | 1927 | 338,021 | +42,803 | 431,462 | 38.9% |
| 3 | line unit for core 1 | 2359 | 445,704 | +150,486 | 568,913 | 53.7% |
| 2 | v3 bundle | 1649 | 297,127 | +1,909 | 379,264 | 34.2% |
| 2 | CRC-32 (v3, top half fixed) | 1681 | 299,612 | +4,394 | 382,436 | 34.5% |
| 2 | multi-bit CRC tap | 1649 | 312,598 | +17,380 | 399,011 | 36.0% |
| 2 | CRC-32 + multi-bit tap | 1683 | 323,487 | +28,269 | 412,911 | 37.2% |
| 2 | CRC any width to 32 | 1713 | 303,952 | +8,734 | 387,975 | 35.0% |
| 2 | 16×16 data RAM (flops) | 2159 | 333,780 | +38,562 | 426,049 | 38.4% |
| 2 | 4 shadow registers, xch | 1775 | 307,482 | +12,264 | 392,481 | 35.4% |
| 2 | tick counter | 1679 | 297,857 | +2,639 | 380,196 | 34.3% |
| 2 | parity | 1647 | 295,620 | +402 | 377,340 | 34.0% |
| 2 | TX FIFO clear | 1649 | 295,043 | -175 | 376,604 | 34.0% |
| 2 | register shift counts | 1647 | 296,109 | +891 | 377,964 | 34.1% |
| 2 | two PWM outputs | 1755 | 305,742 | +10,524 | 390,260 | 35.2% |
| 2 | bundle + CRC any + tick + parity + TX clear | 1749 | 309,055 | +13,837 | 394,489 | 35.6% |
| 3 | bundle + CRC any + tick + parity + TX clear | 2232 | 423,975 | +128,757 | 541,177 | 51.1% |
| 3 | … the same + line unit for core 1 | 2512 | 464,259 | +169,041 | 592,597 | 55.9% |
| 3 | … the same + line unit + PWM | 2620 | 475,935 | +180,717 | 607,501 | 57.3% |
| 3 | **bundle + CRC any + tick** | 2229 | 420,712 | +125,494 | 537,012 | 50.7% |
| 4 | bundle + CRC any + tick + parity + TX clear, 4-deep FIFOs | 2691 | 483,309 | +188,091 | 616,913 | 61.1% |
| 2 | **bundle + CRC any + tick** | 1747 | 308,777 | +13,559 | 394,134 | 35.5% |

Plus one SRAM macro per bank (49,488 µm², outside the standard-cell area).
Deltas are against the 2-core row, so rows with 3 or 4 cores include the
extra cores. One check of the ratio across tiles: for today's RTL it
predicts 376,827 µm², and the 8x4 harden measured 378,074 µm² (+0.3 %).

The control rows (`V8_NONE`, `X_NONE`: the prototype files with nothing
enabled) land within 0.2K µm² of the 2-core row, so the prototypes start from
the taped-out RTL; synthesis noise between equivalent netlists is about
±0.5K µm² (the v3 study measured the same).

## Candidates

The FAQ has no fixed weighting. Its main criterion is flexibility: can the
chip be reprogrammed for protocols it was not designed for? Then the
protocols demonstrated, what the architecture uniquely makes possible, and
how it was designed and verified; the write-up counts heavily. For USB and
Ethernet, more on-chip (line coding, CRC, clock recovery) scores better, and
running protocols at once or in both roles is a plus. Each candidate is read
against that, and against what the chip's own record says it ran into: the
25 ISA limitations the post-freeze rounds found
([post-freeze-protocols.md](post-freeze-protocols.md#isa-limitations-found),
cited below as "item N") and the v3 study's measurements
([v3-study.md](v3-study.md)).

Verification here means this project's own bar for an RTL change: the spec
(`docs/isa.md`), the Python model (`tools/pe/sim.py`), the assembler
(`tools/pe/asm.py`, `isa.py`), the static timing checker
(`tools/pe/timing.py`), the lockstep test with coverage goals
(`test/test_lockstep.py`, `tools/pe/randprog.py`), the formal properties
(`formal/`), mutants (`test/mutants.py`), the Hardcaml/OCaml port
(`ocaml/`, which [hardcaml-port.md](hardcaml-port.md) says is formally
equal to `src/`), and per netlist: gate-level and SDF suites, netlist-to-RTL
equivalence ([equivalence.md](equivalence.md)) and the release receipts
([release-evidence.md](release-evidence.md)).

### (a) More cores: 3 or 4

**Flexibility.** Today every two-core program pair takes the whole chip, and
the record names the cases: a full MIDI port in and out (item 16), the
ISO 7816 reader plus its card clock (item 17), low-speed USB without the
line unit (`usb_ls_rx` + `usb_ls_tx`), the 10BASE-T node with carrier sense
(`eth10_tx_cs` + `eth10_rx`), and the USB plus UART pairs
([usb-uart-coexist.md](usb-uart-coexist.md)). A third core leaves one free
next to each of them: a USB device plus a UART plus an I2C or SPI
controller at once, an I2C target and an I2C controller (both roles) next to
a USB device, or a 10BASE-T node with a management or console port. For an
unknown protocol that needs two engines (a clock and a data engine, a
receiver and a transmitter), a third core is what lets it run beside
anything else. This is the FAQ's "at the same time" directly.

**Area (synth).** +105.5K µm² for the third core (pe_core 73K, its two
latch FIFOs and clock gates about 31K, chip glue +1.6K) plus its SRAM
(49.5K µm² outline); +108.3K and another SRAM for the fourth. Utilization
**estimate**: 48.3 % for 3 cores, 64.3 % for 4; with 4-deep FIFOs (the
configuration CI's 4-core lockstep uses) 43.5 % and 57.6 %.

**Routing and timing (global route, then harden).** Run `grt3`: three
cores, 8-deep FIFOs, banks spread along the bottom edge (x = 4.32, 653.28,
1302.72 µm), fo6's settings. Placement utilization 43.8 %; at the
global-routing step Metal3 **65.4 %** (192.5K / 294.5K, the third macro
takes 6 % of the capacity) with **46 overflows** (40 on Metal3). That is a
little worse than fo6 at 6x4 (63.9 %, 12). physical.md's record at 6x4:
runs ending global routing with 15–39 overflows (at the `GRT_ADJUSTMENT`
of 0.10 these runs also use) were cleared by detailed routing; the one
netlist with 80–240 (at 0.15) taken through detailed routing stalled at 8
violations after six hours. So a third core is back at about the congestion
fo6 lived with, slightly above the range known to clear.
A fourth core (runs `grt4f2`, `grt4f2d58`: four cores, 4-deep FIFOs,
banks spread 16 µm apart) does not get that far. At fo6's density setting
detailed placement failed after post-placement repair added 6,478 buffers
(3 cells could not be legalized; placement utilization 52.5 %). At density
58 it placed, but global routing had not finished after 107 minutes: 1001
extra overflow iterations (`grt3` needed 344 to end at 46 overflows, the
`grt3pkg` superset 736 to end at 518), then the router began dropping the
non-default rules of clock nets to relieve congestion (25 nets). It was
stopped there; no final congestion report exists. 

**The recommended package, hardened (run `pkg3`).** Three cores with the
bundle, the any-width CRC and the tick counter, from the prototype files
(unverified RTL), spread banks, fo6's settings. Placement utilization
46.0 %; at global routing Metal3 **68.2 %** (200.9K / 294.5K) with **31
overflows** (23 on Metal3), inside the range detailed routing cleared at
6x4 (and fewer than plain `grt3`'s 46: one placement each, so this is
variance, not an effect of the additions). The harden then **routes clean**
(0 routing DRC, 0 KLayout DRC, LVS clean, 0 antenna) at **51.1 %**
standard-cell utilization (the estimate said 50.7 %), but **fails signoff**
with fo6's settings:

| pkg3 at 20 ns | typ | slow | fast |
|---|---|---|---|
| Setup WS | 0 (met) | **−1.571 ns**, 111 endpoints, TNS −60.0 ns | 0 (met) |
| Hold WS | **−0.188 ns**, 3 endpoints | +0.047 ns | **−0.318 ns**, 6 endpoints |
| Slew / capacitance violations | 7 / 1 | 22 / 1 | 2 / 1 |

By STA on the same netlist: 48 MHz slow −0.738 ns (21 endpoints); 60 MHz
slow −4.904 ns (648). Netlist sha256 `d35b190ce12de505…`, GDS
`65f302e3de1cb6b8…`. The worst setup path runs from bank 0's output into
core 0's RX FIFO input register; every failing hold path ends at bank 0's
SRAM inputs, where physical.md already notes a 0.52 ns address hold that
needs buffers. Signs of the congestion: post-GRT repair re-routed for
minutes where base took seconds, the router dropped the non-default rules
of 65 clock nets (base and fo6: none), and the harden took **3 h 06 min**
against base's 43 minutes.

How to read it: this is the state v2c was in at 6x4 (slow −1.57 ns, slew
and fanout exceptions), which 25–30 runs of settings (SRAM clock advance,
late pin clocks, PnR margins, hold margin) turned into fo6. Here the hold
failures are new, and each run costs about three hours. It is one
placement of unverified prototype RTL, not a verdict: nothing was tuned.

**Verification.** The cheapest of all the candidates per unit of
flexibility. The architecture, register map and encodings have always
described four cores (`docs/isa.md` §1, §7); `PE_NCORES` is a parameter of
the RTL, the model (`sim.Chip(ncores=...)`), the OCaml port
(`ocaml/rtl/config.ml`) and the FPGA profile (`flow/fpga_profile.py`); and
CI already runs the lockstep on a 4-core flop build next to the tapeout one
(`.github/workflows/test.yaml`, `lockstep_4core.xml`). What is new: a
3-core SRAM-build lockstep in the regression, the RTL tests' core spreading
(`test/test.py` takes `PE_NCORES`), the formal Lean testbenches fixed at
`NCORES(2)` (`formal/lean/*_tb.sv`), host tools and docs that assume two
cores, a new ID value so software can tell the builds apart, and the
per-netlist receipts. No ISA change.

**Schedule risk.** Low for the logic; **high for the layout**: the one
harden of the package (above) routes but needs a settings search, at about
three hours a run.

### (b) More instruction memory per core

**Flexibility.** Only USB is near the 256-word bank: `usb_enum` is 253 words,
and the v3 study's USB device with a data endpoint was 280 in v2
instructions. Everything else has room (the largest post-freeze program,
`cec`, is 244). A larger bank lets one core hold a bigger stack (USB with
endpoints and class descriptors, a CAN controller with filters), but the v3
table read alone brings the endpoint device to 250 words, and the bundle to
243 (model, [v3-study.md](v3-study.md)).

**Area.** The IHP 512×32 macro is 416.64 × 191.34 µm (79,720 µm², +30.2K per
bank over the 256×32); 1024×32 is 140,183 µm². Standard cells: one more
address bit per core, small (not synthesized; it needs the ISA change
below).

**Timing (liberty, measured).** The slow-corner critical paths start at the
SRAM's data output (physical.md, "Slow corner"). The 512×32's clock to
`A_DOUT` at `slow_1p08V_125C` is 6.38–6.69 ns against 5.13–5.44 ns for the
256×32 (the liberty `cell_rise` tables), **+1.25 ns** on the paths fo6 meets
by +0.010 ns at 20 ns. Two 256×32 macros per core instead cost an output mux
on the same paths and twice the macro area.

**Verification.** An ISA and tool change: a 10-bit address is four banks of
256 (`addr[9:8]` = bank), the JMP/CALL address field is 10 bits, and the
host's IMEM window (0x0000–0x03FF) sits under the core registers at 0x0400.
512 words per core needs core-relative PCs or a new address map, which
touches every loader, the assembler's origins and every test that places a
program on core 1.

**Verdict.** Leave out: little measured demand, an ISA break, and a slower
macro on the critical path.

### (c) A line unit for core 1, or more line-unit modes

**Flexibility.** Today the line unit is core 0's only, so the two protocols
that need it, full-speed USB and 10BASE-T receive, can't run at once, and
no second USB or Manchester line can run beside either. A second unit on
core 1 makes USB plus 10BASE-T on one chip possible, each with line coding
and clock recovery on-chip (the FAQ's "more on-chip" for both, at the same
time). The v3 study rejected it for routing (its ~80 signals from a shared
unit crossing the die); a second instance beside core 1 needs no shared
wiring, only the pin connections every core already has.

One catch, from the spec, not a test: the receiver's bit period is a whole
number of clocks (LINEDIV `[31:16]`). Full-speed USB needs 4 clocks per bit,
so 48 MHz; 10BASE-T needs 10 Mbit/s, so 5 clocks at 50 MHz or 4 at 40. No
clock gives both today. **60 MHz gives 5 and 6**, so USB FS plus 10BASE-T at
once needs the chip to close at 60 MHz (see the baseline), or a fractional
receive period (a line-unit change), or a test showing the Manchester
tracker tolerates 4.8 clocks per bit at 48 MHz (not tried). Low-speed USB
(32 clocks per bit at 48 MHz, 40 at 60) plus 10BASE-T has the same issue.

More modes for the same unit (infrared bi-phase with bit times above 4095
clocks and a carrier, item 18; HDLC flags; other stuffing rules): the record
asks for each once or not at all, and the firmware already decodes slow
Manchester (`rc5_rx`, `dali_ctrl`). Not prototyped.

**Area (synth).** +42.8K µm² for a second unit with its register window,
rx sampling flops and core 1's LINE path (pe_line itself is 41.1K), +45.0K
on a 3-core chip. Utilization estimate 38.9 % (2 cores), 53.7 % (3 cores).

**Routing (global route).** Run `grt3pkg`: three cores with the full ISA
set (bundle, any-width CRC, tick counter, parity, TX clear) **and** the
second line unit, spread banks, fo6's settings. Placement utilization
50.8 %; at global routing Metal3 **73.3 %** (215.7K / 294.5K) with **518
overflows** (489 on Metal3). That is past anything physical.md saw detailed
routing clear (the 80–240-overflow netlist stalled it at 6x4). The line
unit is not the only addition in that run, so its own share is not
separated; the package without it is measured in (a) (run `pkg3`).

**Verification.** `src/pe_line.sv` is reused unchanged, so its formal bounds
(`formal/line.sby`) carry over. New: the model's line unit per core
(`tools/pe/line.py` is one instance at core 0), lockstep with random line
traffic on core 1 and its coverage goals, the pin priority between two
units (a spec decision: which wins a pin both drive), the second register
window, the OCaml port, and RTL tests of USB and 10BASE-T on core 1 and at
once. Moderate.

### (d) The v3 "cheap bundle"

Table read (`mov osr, [rN]`), CMPW (`cmp rN, #imm16`), `jmp !t`, `flag tog`,
bank-relative PC writes ([v3-study.md](v3-study.md) has the encodings).

**Flexibility (model, v3 study).** −37 words across the library;
`usb_enum` 241 → 213 then; an autonomous USB device with an interrupt IN
endpoint fits one core (243 words; 280 without the bundle); lookups for
LIN's PID parity and DShot's GCR decoding (item 4, 15); items 1, 2 (compares)
and 25 (compares) addressed. The model, assembler, checker and 39 model
tests for it exist on the unmerged `v3-study` branch.

**Area (synth).** +1.9K µm² for two cores (+0.95K per core), 2 flops.

**Routing and timing.** Inside the core; no chip-level wire. The table read
gives each SRAM output bit one more load (the OSR mux), on the paths that
set the slow corner. The v3 verdict ("no respin") was about congestion at
6x4, which the baseline shows is gone; the DOUT load is a timing risk that
only a harden measures.

**Verification.** Port the v3 model/assembler/checker work (on that branch: about a
hundred changed lines in `tools/pe/`, and 39 model tests) as new commits on the public base;
RTL; lockstep coverage goals for each instruction; the formal "a disabled
core's state is frozen" assertion must allow the table read's write one
clock after its tick (the v3 study's note); mutants; the OCaml port.
Moderate, mostly already done once.

### (e) CRC-32, a multi-bit tap, on-chip Ethernet FCS

**Flexibility.** The 10BASE-T FCS is the host's job today in both directions
(`firmware/eth10_tx.pasm`: "the host computes the FCS"; the CRC-32 is
`tools/pe/eth.py`); the FAQ scores on-chip CRC for Ethernet. With a
32-bit CRC tap, which IN and OUT both feed, `eth10_rx` can check the FCS in
the engine (the v3 study's `eth10_rx_fcs`, 27 words, model tests with 802.3
jitter, ±200 ppm and single-bit errors) and `eth10_tx` can append it. Beyond
Ethernet: CAN FD's CRC-17 and CRC-21, FlexRay's CRC-24 and CRC-32C need more
than 16 bits, so a tap of **any width to 32 with a programmable polynomial**
covers more unknown protocols than v3's CRC-32 with the polynomial's top half
fixed at 0x04C1.

**Area (synth).** v3's CRC-32: +2.2K µm² per core, 17 flops. Any width with a
32-bit polynomial (`V8_CRCW`, a POLY_HI register at core offset 0x11):
+4.4K per core, 33 flops. Multi-bit tap (up to 8 bits per shift): +8.7K per
core; it speeds CRC loops that already meet their deadlines and unlocks no
measured protocol (the RMII receiver's `in pins, 5` carries a tag bit, so it
would not feed a tap directly).

**Routing and timing.** Inside the core; the CRC register and its readback
widen to 32 bits. The v3 study's core_route measurement: +1.5 % of core wire
for CRC-32.

**Verification.** Model and assembler (CRCCTRL fields), lockstep comparing
the 32-bit CRC, the CRC-tap mutant extended, firmware `eth10_rx_fcs` (port
from v3) and an FCS-appending `eth10_tx` (new), RTL tests through the SPI
port. Moderate. **Verdict:** any-width CRC in; multi-bit tap out.

### (f) Data RAM or more registers

**Flexibility.** Registers are the most-repeated limitation (items 3, 14,
22: LIN keeps its bit time in the CRC register, CEC packs four flags and two
counters in r1). Writable RAM would also hold a received frame to replay or
compare, CAN filter tables or a cloned device's registers.

**Area (synth).** A 16 × 16-bit data RAM in flops with `ld`/`st` on the
free opcode 14 (`V8_DRAM`): +19.3K µm² per core, 256 flops (a latch RAM like
the FIFOs' would be smaller; not prototyped). Four shadow registers with a
one-tick `xch rN` (`V8_XCH`): +6.1K per core, 64 flops. The IHP 64×16 SRAM
macro is 15,240 µm² but its clock to output (4.99–5.30 ns at slow) is in
series with an instruction that already starts at the IMEM's output: a
load would need a second cycle. Eight real registers need a 3-bit field in
every register operand, which the v2 encodings don't have: not small.

**Verification.** New instructions in the model, both assemblers, the
checker (the RAM's contents become state it doesn't track), lockstep
comparing RAM contents, formal frozen-state property extended, mutants.
Moderate to high.

**Verdict.** Valuable, but the per-core cost (6–19K) times three cores, and
the ISA work, put it behind (a), (d) and (e). `xch` is the cheap form if
there is room late; it is not in the recommendation.

### (g) Other additions for unknown protocols

Measured (synth, per core unless chip-level):

| Addition | Area | Record item | What it does for an unknown protocol |
|---|---|---|---|
| Free-running tick counter, MOV source 15 (`V8_TICK`) | +1.3K, 16 flops | 11, 23 | Time between any two events, not only inside a WAIT: pulse widths, inter-frame gaps, timestamps; removes DALI's and CEC's hand-padded paths |
| Parity of a register into C (`par rN`, `V8_PAR`) | +0.2K (noise) | 13, 21 | 1 instruction instead of an 11-instruction fold per character (UART with parity, ISO 7816) |
| Core-side TX FIFO clear (`pull clear`, `V8_TXCLR`) | noise, 1 flop | 19 | Drop a lost frame's queued words (CEC arbitration, any half-duplex bus) |
| Shift count from a register (`V8_SHV`) | +0.45K | 5 | Variable-length fields (Wiegand formats, length-prefixed frames) |
| Two PWM / clock outputs on any pin (`X_PWM`, chip-level) | +10.5K for the chip | 17 | A card clock, an I2S MCLK, a servo or LED PWM without spending a core |

Not prototyped: configurable sampling (a majority-of-N input filter per
pin, against glitches on long cables) and a fractional receive bit period
for the line unit (it would let USB and 10BASE-T share 48 MHz, see (c)).

The tick counter is the strongest of these per µm²: it is the one general
mechanism the post-freeze agents asked for twice and worked around by hand
each time. Parity and TX clear are almost free in area but each is one more
instruction to specify, model, port and test.

## Ranking

Flexibility is judged against the FAQ's order (reprogrammability for
protocols not designed for, then protocols shown, then what the architecture
uniquely allows); cost is synthesized area per chip (2 cores unless the row
says so) and the verification work above.

| Rank | Candidate | Flexibility gain (evidence) | Area, synth | Routing / timing at 8x4 | Verification cost | Schedule risk | Verdict |
|---|---|---|---|---|---|---|---|
| 1 | (a) third core and bank | A protocol next to every two-core pair; 3 at once; both roles plus USB (record) | +105.5K + SRAM | routes (grt3: 65.4 %, 46 overflows; pkg3 harden: routing clean) but fails signoff untuned (slow −1.57 ns, hold −0.32 ns) | Low: a parameter in spec, RTL, model, port; 4-core lockstep already in CI | High (layout) | **in**, with a Dec 6 gate |
| 2 | (e) CRC of any width to 32, programmable polynomial | 10BASE-T FCS in the engine both ways (model, v3); CAN FD, FlexRay CRCs | +8.7K | inside the core; +1.5 % core wire for CRC-32 (v3 core_route) | Moderate | Low | **in** |
| 3 | (d) the v3 bundle | −37 words; autonomous USB device with a data endpoint (model, v3); lookups | +1.9K | inside the core; one more load on each IMEM output bit | Moderate, done once on `v3-study` | Low | **in** |
| 4 | (g) tick counter | Interval timing anywhere (items 11, 23) | +2.6K | inside the core | Low | Low | **in** |
| 5 | (c) line unit for core 1 | USB and 10BASE-T at once with on-chip coding, **if** a common clock (60 MHz, not closed) or a fractional RX period | +42.8K (+45.0K on 3 cores) | with 3 cores and the ISA set: Metal3 73.3 %, 518 overflows (global route) | Moderate | High | out |
| 6 | (g) parity, TX clear, register shift counts | 1 instruction each where firmware spends 3–11 (items 5, 13, 19, 21) | +0.2K, ~0, +0.9K | inside the core | Low each, but each is ISA surface | Low | optional |
| 7 | (g) two PWM / clock outputs | A card clock or MCLK without a core (item 17) | +10.5K | chip level, near the pins | Low–moderate | Low | optional; a third core covers item 17 too |
| 8 | (f) shadow registers (`xch`) / 16×16 data RAM | The most-repeated limit (items 3, 14, 22) | +12.3K / +38.6K | inside the core | Moderate–high | Moderate | out (late extra at most) |
| 9 | 4th core | One more engine | +108.3K + SRAM | placement failed at fo6's density; at density 58 global routing did not converge in 107 min (stopped) | Low (as (a)) | High (layout) | **out** |
| 10 | (e) multi-bit CRC tap | Speed only; no protocol measured | +17.4K | inside the core | Moderate | Low | out |
| 11 | (b) 512-word banks | One program near the limit (`usb_enum`), which (d) relieves | +30.2K per bank (macro) | +1.25 ns on the critical path's first cell | High: address map and ISA | High | out |

## Recommendation

### The package: a third core, and four additions inside each core

1. **A third core with its own 256×32 SRAM bank** (`PE_NCORES` = 3,
   8-deep FIFOs as now).
2. **A CRC tap of any width from 1 to 32 bits with a programmable 32-bit
   polynomial**, so `eth10_rx` checks and `eth10_tx` appends the 10BASE-T
   FCS in the engine.
3. **The v3 bundle**: table read, CMPW, `jmp !t`, `flag tog`, bank-relative
   PC writes.
4. **A free-running tick counter** readable as MOV source 15.

Synthesized: 420,712 µm² against today's 295,218 (+125.5K, of which the
third core is 105.5K); utilization **estimate** 50.7 % on 8x4 (fo6: 46.9 %
on 6x4). Measured: the 3-core global route had 46 overflows (`grt3`); the
package itself (`pkg3`, prototype RTL) routed clean at 51.1 %, but with
fo6's settings misses slow setup by 1.57 ns and hold by up to 0.32 ns (see
(a)). It needs the settings search fo6 needed, at about three hours a run.

Why this and not more:

* **It buys the FAQ's first criterion at the lowest verification cost.** A
  third core is a new engine for any protocol, known or not, next to every
  pair that fills the chip today; it is a parameter the spec, model, RTL,
  OCaml port and FPGA profile already have, and a 4-core build is already
  in the lockstep regression. The four additions are each inside the core
  (no chip-level wire), each has a record item or a FAQ line behind it
  (on-chip Ethernet CRC; items 1, 2, 4, 11, 15, 23, 25), and two of them
  were already implemented once on the model with tests.
* **It is the largest addition that routes.** A third core put Metal3 back
  at fo6's level (65.4 % against 63.9 %), and the package routed clean in
  its one harden; the second line unit or a fourth core push past that
  (below). It is not free: untuned, the package misses slow setup by
  1.57 ns and hold by up to 0.32 ns, the kind of gap fo6's settings search
  closed at 6x4, but on the 2-core baseline the first setting tried
  recovered only 0.02 ns, and the package's runs take about three hours.
  That is the risk the release-clock rule (48 MHz if 50 is out of reach)
  and the Dec 6 gate exist for.
* **The second line unit's headline use, USB and 10BASE-T at once, has no
  common clock** in the range this chip closes (48 MHz yes, 60 MHz no at
  the slow corner). Without it, a second unit is a second USB or Manchester
  line, which nothing in the record asks for.

### The fallback: the same four additions on two cores

If the 3-core netlist is not through `release_check.py` by Dec 6, drop the
third bank and keep everything else: 308,777 µm² synthesized (+13.6K),
**estimate** 35.5 % utilization, with the baseline's routing margin (Metal3
45.7 %, no overflow). Every model, firmware and tool change carries over,
so the fallback loses one engine, not the work.

### Left out, and why

| Left out | Reason |
|---|---|
| A fourth core | Global routing did not converge (stopped after 107 minutes and 1001 extra iterations), and placement failed at fo6's density; estimate 57.6 % utilization even with 4-deep FIFOs |
| A line unit for core 1 | No clock that serves USB and 10BASE-T together; +45K on top of the third core, and the 3-core package with it did not route (518 overflows at global routing) |
| 512-word banks | +1.25 ns on the slow-corner critical path, an address-map and ISA change, one program near the limit |
| Data RAM, shadow registers | ISA and checker work at 6–19K per core, times three cores; the most-repeated limit, but not the cheapest win |
| Multi-bit CRC tap | Speed only, no protocol measured |
| Parity, TX FIFO clear, register shift counts, PWM outputs | Small and useful; each is one more instruction or register window to specify, verify and port. Add them only if weeks 1–2 finish early |
| 60 MHz | 4 ns short at the slow corner on today's RTL |

### Before any of it

The tile change alone costs a timing-closure search: today's RTL with fo6's
settings misses 50 MHz at the slow corner by 0.643 ns on 8x4, and the first
setting tried (later pin clocks) recovered 0.02 ns of it. Whatever the
author picks, the plan starts with that search, and the release receipts
(gate level, SDF, equivalence, precheck) are regenerated for the new
netlist.

## Plan to the freeze (2027-01-11)

Nine weeks, for the recommended package. A full harden at 8x4 took 43
minutes for the 2-core RTL and 3 h 06 min for the 3-core package (`pkg3`;
runner1, one at a time), so a search of the 25–30 runs t48 needed is three
to four days of continuous runner time for the package, and under a day for
the fallback. Weeks run Monday to Sunday.

| Week | Dates | Work | Gate at the end of the week |
|---|---|---|---|
| 1 | Nov 9–15 | Author picks. The orchestrator allows the `flow/` edits AN needs (`gen_config.py`'s 8x4 corner and 3-bank layout, `fpga_profile.py`'s gate conversion), which RULES.md forbids today. Spec text in `docs/isa.md` for every addition; model, assembler and checker (port the v3 work, add the any-width CRC and the tick counter); model tests | Model suite green; spec reviewed by the verifier |
| 2 | Nov 16–22 | RTL: `PE_NCORES` 3, the core additions, a new ID value. Lockstep coverage goals per instruction, the 3-core SRAM lockstep in the regression, formal properties (the table read's late write), mutants for each addition. Decide the OCaml port: port the additions, or date its equivalence claim to v2 | Lockstep (2-, 3-, 4-core), formal, mutants green |
| 3 | Nov 23–29 | Harden settings search at 8x4 (target: 50 MHz at every corner, no exception; else 48 MHz, below). Firmware: `eth10_rx_fcs`, an FCS-appending `eth10_tx`, `usb_enum` with table reads, `usb_enum_ep1`; host tools for three cores | A candidate netlist; firmware on the model and RTL |
| 4 | Nov 30–Dec 6 | Gate-level functional suite, netlist-to-RTL equivalence, SDF standard and v2 sets at three corners, autonomous USB with delays, precheck, `release_check.py` | **Go / fallback decision** (below) |
| 5 | Dec 7–13 | KV260 bitstream (3 cores), orchestrator board runs (smoke, USB with Linux, Ethernet); concurrency demo: three protocols at once on model, RTL and FPGA | Board smoke passes |
| 6 | Dec 14–20 | Write-up: README, summary, physical.md, isa.md §7 defaults, explain-wirelingo.md (27 files mention 6x4 or two cores today); verifier rounds | Docs verified |
| 7–8 | Dec 21–Jan 3 | Buffer (holidays), verifier FAILs, re-harden only if a bug is found | — |
| 9 | Jan 4–10 | Release candidate tag, receipts, record copies (item AL); **freeze Jan 11**; deadline Jan 18 | Freeze |

**The release clock.** Aim for 50 MHz at every corner; accept 48 MHz if
the search does not get there. The baseline already meets 48 MHz at every
corner (+0.19 ns); the package does not yet (−0.738 ns at the slow corner,
`pkg3`). At 48 MHz the USB demos are unchanged and 10BASE-T runs at 40 MHz
(4 clocks per bit), where it tolerates ±12 ns of edge jitter against
802.3's ±13.5 ns (a one-off model sweep, [guide.md](guide.md#known-limits)).
So what would be lost is the 50 MHz claim and, with it, full 802.3 jitter
tolerance. The orchestrator decides which clock the release claims.

**The go / fallback gate (Dec 6).** If the 3-core netlist has not passed
`release_check.py` at 48 MHz or better by then, take the fallback: two
cores on 8x4 with the same ISA additions, which keeps every firmware and
tool change and drops only the third bank, and starts from a layout that
already meets 48 MHz.

## The KV260 FPGA build

The FPGA build stages `src/` through `flow/fpga_profile.py` (SRAM banks to
inferred RAM, latch FIFOs and clock-gated configuration registers to
enables) and wraps it in `fpga/kv260/kv260_top.v`. For the recommended
package it needs:

* **Core count.** `fpga/kv260/build.sh` calls `fpga_profile.py --cores 2
  --fifo-log2 3`; make it 3. The profile already accepts 1–4 cores.
* **Clock-gate conversion.** `fpga_profile.py` rewrites exactly seven
  gated configuration registers per core (`wire [6:0] cfg_gclk`) and four
  global ones, and stops on any drift. Every new host-written register (the
  CRC's POLY_HI, a second line unit's window, PWM registers) must either
  join those arrays in the RTL or be added to the conversion. Folding them
  into the existing arrays keeps the converter's change to an index range.
* **Host software on the board.** `fpga/kv260/host/pe_kria.py` and
  `v2_demo.py` assert ID `0x50450002`; the new build should read a new ID
  (and the tools accept both), so a stale bitstream can't pass as the new
  one. The bring-up identity check (`expected_build.json`, `build_id.py`)
  binds the RTL digest as before.
* **Resources (estimate).** The v2 build used 6534 LUTs and 2545 flops for
  the chip logic, 2 RAMB18 for IMEM (fpga/kv260/README.md, 2026-10-07). A
  third core adds roughly half of that and one RAMB18: well inside the
  XCK26. No pins change: the logical pin space is the same 32 entries.
* **60 MHz, if the ASIC closes it.** `build.tcl` takes `clk_div` (1200 MHz
  / clk_div), so 20 gives 60 MHz, but `kv260_top.v` derives the SPI device
  clock as `CLKOUT2_DIVIDE(CLK_DIV / 8)` (8 per chip clock), which
  truncates to 2 (600 MHz) instead of 480. A 60 MHz build needs another VCO
  (for example 1440 MHz: 60 MHz is /24, 480 MHz is /3), and the device
  clock's crossings met with +0.28 to +0.52 ns at 400 MHz, so 480 MHz may
  not meet timing. Not tried.
* **Board runs** stay the orchestrator's: the smoke, USB and Ethernet runs
  of [fpga/kv260/README.md](../fpga/kv260/README.md) on the new bitstream,
  plus a concurrency demo (three cores on three protocols, the pattern
  generator and `la_capture` as far ends). Builders write the scripts and
  prove them on the model.

## Reproduce

On a machine with the IHP PDK (`~/ttpdk`), the LibreLane 3.1.0.dev3 image,
the flow venv, a tt-support-tools checkout with the 8x4 IHP tile (commit
`d66cf17`, "feat(ihp): 8x4 tile size for ihp-sg13cmos5l"), and yosys:

```sh
# synthesis rows and the budget table (about 3 minutes)
syn/8x4-study/run_all.sh > syn/8x4-study/results.txt
python3 syn/8x4-study/budget.py
# the baseline: today's RTL on 8x4 (about 1.5 h), then 60 MHz on its netlist
FLOW_VENV=... TT_SRC=... PROTOEMU_EXP_DIR=... syn/8x4-study/harden8x4.sh base
syn/8x4-study/sta_period.sh <run worktree> 16.667
syn/8x4-study/harden8x4.sh baselc8 -- --late-clock 8      # the same, pin clocks 8 buffers late
# routability (global routing only)
STOP=grt syn/8x4-study/harden8x4.sh grt3 3 -- --layout spread
STOP=grt FIFO_LOG2=2 syn/8x4-study/harden8x4.sh grt4f2 4 -- --layout spread      # fails in placement
STOP=grt FIFO_LOG2=2 syn/8x4-study/harden8x4.sh grt4f2d58 4 -- --layout spread --density 58
B="V3_CMPW V3_CMPW_SPLIT V3_NT V3_TBL V3_TBL_OSR V3_BANKREL V3_TOG"
STOP=grt PROTO="$B V3_CRC32 V8_CRCW V8_TICK V8_TXCLR V8_PAR X_LINE1 V8_LINE1" \
  syn/8x4-study/harden8x4.sh grt3pkg 3 -- --layout spread
# the recommended package, full harden (prototype RTL)
PROTO="$B V3_CRC32 V8_CRCW V8_TICK" syn/8x4-study/harden8x4.sh pkg3 3 -- --layout spread
```

| Run | What | Level | Wall time | Result |
|---|---|---|---|---|
| `base` | today's RTL, 2 cores, fo6's settings | harden | 43 min | signoff.py PASSED; slow setup −0.643 ns; netlist `982444c9e60151fc…` |
| `baselc8` | the same, `--late-clock 8` | harden | 45 min | signoff.py PASSED; slow setup −0.624 ns; netlist `73222029deedd20a…` |
| `grt3` | 3 cores | global route | 14 min | Metal3 65.4 %, 46 overflows |
| `grt4f2` | 4 cores, 4-deep FIFOs | (placement) | 3 min | detailed placement failed (DPL-0036) |
| `grt4f2d58` | the same, density 58 | global route | 107 min, stopped | not converged |
| `grt3pkg` | 3 cores + ISA set + second line unit (prototype) | global route | 45 min | Metal3 73.3 %, 518 overflows |
| `pkg3` | 3 cores + bundle + any-width CRC + tick (prototype) | harden | 3 h 06 min | routes clean; signoff FAILED (hold); slow setup −1.571 ns; netlist `d35b190ce12de505…` |

The runs above were made at commits `a91f85c` (base) and `6f8242f` (the
rest) of this branch; the later commits change only `budget.py`, a label in
`harden8x4.sh`'s summary, `run_all.sh`'s last row and this page.

`harden8x4.sh` is `flow/harden.sh`'s steps with two edits made only in the
run's own worktree: `info.yaml` says `tiles: "8x4"`, and `src/config.json`
comes from a copy of `flow/gen_config.py` whose core corner is the 8x4
die's (and, in that copy, a `spread` SRAM layout: banks evenly along the
bottom edge, bank 0 leftmost). With two banks `spread` gives exactly the
default `corners-swap` positions. `sta_period.sh` reproduces fo6's signoff
at 20 ns (hold +0.191 / +0.362 / +0.095 ns, slow worst flop +0.010 ns), which
is how it was checked.

## Limits

* The utilization column is an **estimate** from one ratio (fo6's hardened
  to synthesized area). The global-route and harden rows are measurements
  of one placement each; physical.md shows ±0.4 ns of slow-corner setup
  between placements of one configuration.
* A global route measures routability, not a routed chip; only detailed
  routing and signoff (a harden) settle it.
* The prototypes are unverified RTL, written to count cells and to route.
  A harden of a prototype measures area, wire and timing of something like
  the change, not the change the implementation will make.
* The 60 MHz and 48 MHz numbers are STA of netlists repaired for 50 MHz. A
  run repaired for 60 MHz would do somewhat better; 4 ns at the slow corner
  is more than repair has ever recovered here (physical.md).
* Flexibility gains are read from the record (the post-freeze limitations,
  the v3 study's model results), not from new tests. Nothing here ran on
  the KV260 or on silicon.
