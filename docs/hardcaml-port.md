# The Hardcaml and OCaml port

`ocaml/` is a second implementation of Wirelingo: the RTL in Hardcaml, and the
assembler, cycle model and main host tools in OCaml. The chip stays the Verilog in
`src/`, which this work does not edit.

**Version.** The port is of the v0.3 RTL and ISA (docs/isa.md §8) on the 8x4 branch:
the 3-core package (the third core with its own bank, CRC widths to 32 bits with
POLY_HI, the tick counter, the table read, CMPW, `jmp !t`, `flag tog`, the
bank-relative PC), at 3 cores and 3 banks (the 3-core 8x4 configuration, `project.v`'s default) and at
2 and 2 (its Dec 6 fallback, the same RTL with `PE_NCORES=2 PE_NBANKS=2`). The v0.2
port's numbers, at a167455 for the 6x4 build, are in
[evidence/ocaml-parity-20261104/](../evidence/ocaml-parity-20261104/README.md); they say
nothing about v0.3, and this page replaces them. This page's own run is in
[evidence/ocaml-parity-20261111/](../evidence/ocaml-parity-20261111/README.md).

**Evidence level.** Everything below is a statement about the port. "Equivalent" means
the generated Verilog equals `src/` by a formal check; "lockstep" means simulation. None
of it is silicon or FPGA evidence, and it adds nothing to what is known about the chip
itself. Its use is narrower: anything already established for `src/` at RTL level carries
over to the generated Verilog through the proof, and the OCaml tools can stand in for the
Python ones where they were compared.

All numbers are from one run of `ocaml/scripts/evidence.sh` on runner1 at commit 0f98335,
base `next-8x4` 25214f2 (`src/` as there), except four later reruns. `src/` has not changed
since; `next-8x4` 27ac3f7 adds d68c7ec, a fix to two of `test/`'s cocotb suites, below.

* `test_kv260_3core.py` in the model gate, at 856194f (Stage 3); that commit changes only
  `ocaml/test/lock_chip.py`.
* The whole model gate, `self_check.sh` and the two fixed suites, at 567bff6 on 27ac3f7.
  Since 856194f, `ocaml/` has changed only in `ocaml/README.md` (c9264ed, docs only),
  `ocaml/test/capture_plugin.py` (9a7a63a), `ocaml/test/self_check.sh` and comments in
  `ocaml/formal/equiv_port.py` (567bff6). The
  commit after 567bff6 changes only this file and `evidence/`. (The 567bff6 runs used a
  checkout of the same tree, `1ea4cc79…`; see the evidence folder's README.)

The generated Verilog is the same at all of them: it has sha256 `26af0b599ca74cd2…`
(tapeout) and `3c95ce7b28834149…` (fallback). The run's `summary.txt` and `rc.txt`, and the
reruns' output, are in
[evidence/ocaml-parity-20261111/](../evidence/ocaml-parity-20261111/README.md). The step
logs stay on runner1, in the branch's work directory.

Two gates of the run at 0f98335 did not pass. Both are resolved, as follows.

* **The model gate** failed on two mismatches that were the wrapper comparing a test's
  stubbed bus read, not the model (Stage 3). 856194f fixes the wrapper, and the whole gate,
  rerun at 567bff6, passes with no mismatch.
* **Two of CI's host-transport suites**, `test_spi_packed_host` and
  `test_uart_capture_host`, failed on `src/` too. They checked the ID register against
  `sim.ID_VALUE`, which `tools/pe/sim.py` keeps at v0.2's `0x50450002` on purpose, for the
  board tools. The 2-core v0.3 chip reads `0x50452203` (docs/isa.md §6.2). Both failed at that
  assertion, before any traffic, with the same value on the generated Verilog and on `src/`
  itself (run by hand at 25214f2). d68c7ec changes both to `sim.id_value(2, 2)`. At 567bff6,
  on 27ac3f7, both pass on the generated Verilog: 1/1 and 1/1.

| stage | gate | result |
|---|---|---|
| 1 | formal equivalence with `src/`, 3-core 8x4 build (`tapeout` config) | proved: 10,412 of 10,412 points by `equiv_simple`, ABC `&cec` equivalent, 10 of 10 planted bugs caught |
| 1 | the same, 2-core fallback build | proved: 7,744 of 7,744 points, ABC equivalent, 10 of 10 planted bugs caught |
| 1 | CI's cocotb suites on the generated Verilog | all pass (the two above at 567bff6): `test` 30/30 at 3 cores and 30/30 at 2, `test_post_freeze` 16/16, `test_bench_fw` 5/5, `test_pps` 4/4, `test_pio` 2/2, `test_eth_service` 2/2, `test_spi_preloaded_host` 1/1, `test_usb_uart_rtl` 3/3, `test_spi_packed_host` 1/1, `test_uart_capture_host` 1/1, lockstep 7/7 at 3, 2 and 4 cores |
| 2 | assembler, every firmware but `swd_probe`, and the v0.3 syntax | 70 files byte-identical, no disagreement in 6643 cases |
| 3 | cycle model against `sim.py` and RTL | no difference in 113,825 + 238,671 cycles three-way at 3 cores, 113,551 at 2, nor in about 162 million cycles of the model suite and 8.1 million more at stride 1 on the chips its tests write into |
| 4 | host tools | `host`, `profile`, `export`, `disasm`, `listing`, `modelhost` ported, parity holds, two v0.3 programs included |

## Stage 1: RTL

`ocaml/rtl/` has one file per Verilog module: `project`, `pe_chip`, `pe_core`, `pe_line`,
`pe_hostport`, `pe_fifos`, `pe_fifo`, `pe_imem` (with `pe_imem_bank`). It uses `Interface`
for ports, `Hierarchy.In_scope` for the module tree, `Always` for the execute, line and
host-port blocks, and `Reg_spec` for the clock domains: rising and falling `clk`, rising
and falling `sck`, gated clocks, and asynchronous resets from `rst_n` and from `cs_n`.

Stays Verilog: `src/pe_cg.sv` (`pe_cg`, `pe_latch`), instantiated with `Instantiation` and
read unchanged, and the SRAM macro, instantiated with the same tie-offs. So the clock gates
and latches are the same source on both sides, and their cells are cut points.

Build parameters are OCaml values (`Config`): `tapeout` (3 cores, 3 SRAM banks, 8-deep
FIFOs), `fallback` (2 cores, 2 banks, 8-deep FIFOs) and `flop4` (CI's 4-core flop build).
`gen_rtl tapeout|fallback|flop4 DIR` writes each. `tapeout` is only the name of the
3-core 8x4 configuration, here and in the tables below: nothing has been taped out. The `ifdef FORMAL` blocks are not ported.

The v0.3 changes are all in `pe_core` and `pe_chip`'s ID, as in `src/`: CMPW decoded from
LDI's bit 18 into the ALU's compare, `!t` as condition 0 with selector 1, the table read on
MOV PC's jump path with a `tbl_pend` flop for its extra clock (the word lands in the OSR
from the fetch port; a host PC write in the MOV's clock cancels it), `flag tog` as a set or
clear of the flag as the core sees it, MOV and OUT to PC keeping the PC's bank bits, the
16-bit `tcnt` (source 15, cleared by restart), and the CRC at any width to 32 (`crc_w` five
bits from CRCCTRL {[23], [19:16]}, `crc_poly_hi` an enable flop on `clk` at offset 0x11,
not a clock gate, as in `src/`). The ID is {`"PE"`, NBANKS, NCORES, 3}: `0x50453303` at 3
cores, `0x50452203` at 2.

The Verilog next-state block in `pe_line` uses blocking assignments, so a later statement
reads what an earlier one wrote; an `Always` variable's value is always its final value.
The transmitter is therefore two blocks, and the task `deliver` is a pure function.

### Gate (a): the cocotb suites

`ocaml/scripts/run_cocotb.sh` copies `test/ tools/ firmware/ src/` at HEAD to a throwaway
directory and runs make with `SRC_DIR` and `PROJECT_SOURCES` overridden. `test/` and its
Makefile are not edited. Each suite also passes `check_results.py` with its full test count.
Each runs on the build CI runs it on.

| suite | configuration | result |
|---|---|---|
| `test` | tapeout, `tb.v` | 30/30 |
| `test` (CI's `top2` step) | fallback, `tb.v` | 30/30 |
| `test_post_freeze` | tapeout, `tb.v` | 16/16 |
| lockstep | CI tapeout: 3 cores, 3 banks, FIFO_LOG2 3, 40 seeds x 1000 | 7/7 |
| lockstep | CI fallback: 2 cores, 2 banks, FIFO_LOG2 3, 40 seeds x 1000 | 7/7 |
| lockstep | CI 4-core flop build, 30 seeds x 1000, `LOCKSTEP_GOALS=core` | 7/7 |
| `test_pio`, `test_eth_service` | tapeout | 2/2, 2/2 |
| `test_bench_fw`, `test_pps` | tapeout, `tb.v` | 5/5, 4/4 |
| `test_spi_preloaded_host`, `test_usb_uart_rtl` | fallback | 1/1, 3/3 |
| `test_spi_packed_host`, `test_uart_capture_host` | fallback | 0/1, 0/1 at 0f98335 (the ID check above, the same on `src/`); 1/1, 1/1 at 567bff6, with d68c7ec |

That is every cocotb step of CI's test job. RTL simulation in Icarus. The lockstep suites
include `test_v03_corners`, which drives every v0.3 addition and the table read's corners
(restart wins, cancel by a host PC write, the word landing while the core is disabled) and
requires their coverage goals. `test_bench_fw` and `test_pps` each have one test that skips
by design, and the check takes CI's list of the tests that must pass: `bench_sigrok_available`
needs sigrok-cli, which runner1 does not have on its path, as CI does not, and
`pps_one_second` runs only with `PPS_LONG=1`.

`tb.v` instantiates the generated top level directly. `tb_chip.v` needs one disclosed
adapter. It instantiates `pe_chip` with parameters, and `test_lockstep.py` reads state
through generate blocks (`u_chip.g_core[0].g_on.u_core.pc`). Generated Verilog has neither,
so for `TB=chip` the generator also writes `pe_chip_lockstep.v`: a `pe_chip` with those
parameters that checks them against the build, instantiates the Hardcaml chip as
`pe_chip_hc`, and mirrors the state as read-only wires in blocks of those names (v0.3 adds
`crc_poly_hi`, `tcnt`, `tbl_pend` and the wider `crc` and `crc_w`). It is test scaffolding
(`ocaml/rtl/lockstep_shim.ml`), outside the proof, and it only reads.

### Gate (b): formal equivalence

`ocaml/formal/equiv_port.sh` uses the method of [equivalence.md](equivalence.md), and imports
`formal/equiv/`'s ABC and `equiv_simple` steps unchanged, with the generated Verilog in the
netlist's place. Gold is `src/` with the flow's defines: for `--build tapeout`,
`project.v`'s own parameters (3 cores, 3 banks); for `--build fallback`, `src/` read with
`-DPE_NCORES=2 -DPE_NBANKS=2`. Gate is `wirelingo_hc.v` of the same build plus
`src/pe_cg.sv`. Both are elaborated by the same yosys (`proc`, `flatten`). Then:

1. Ports of all modules, by name, direction and width.
2. Every flop is matched by name, and clock edge, reset edge and reset value must be
   identical. Latches, clock gates and SRAMs are matched by instance name.
3. Both designs are cut at those state elements. Next state, clock and reset of each flop,
   each black-box input and each top-level output must be equal for all inputs and all
   states, reachable or not.
4. Two engines prove it: ABC `&cec` on the whole miter, with every `x` of `src/` a free
   input, and yosys `equiv_simple` per point in parallel shards.
5. Planted bugs in the generated Verilog must each be caught.

Results, yosys 0.69+158, `-j 2` each on runner1 while both ran beside the cocotb suites
and another job's harden: 1012 s (tapeout) and 698 s (fallback). A run before the commit,
the same port with 8 planted bugs, one build after the other with `-j 4` on a quieter
runner1, took 277 s and 333 s and gave the same counts.

| check | tapeout (3 cores) | fallback (2 cores) |
|---|---|---|
| modules with equal ports | 11 of 11 | 11 of 11 |
| flops matched, both sides | 2232 of 2232 | 1752 of 1752 |
| instances matched (latches, clock gates, SRAMs) | 1618 of 1618 (1536, 79, 3) | 1080 of 1080 (1024, 54, 2) |
| next state (D), clock, asynchronous reset | 2232 each | 1752 each |
| latch D and GATE | 3072 | 2048 |
| clock-gate CLK and GATE | 158 | 108 |
| SRAM inputs | 462 | 308 |
| outputs `uo_out`, `uio_out`, `uio_oe` | 24 | 24 |
| **total compared points** | **10,412** | **7,744** |
| `equiv_simple` | 10,412 proven, 0 unproven | 7,744 proven, 0 unproven |
| ABC `&cec` | equivalent, `src/`'s `x` values free | equivalent |

On each build all 10 planted bugs were caught: an AND made an OR, swapped mux legs, swapped
clock-gate enables, swapped SRAM address legs (these four by the proof), a flop's clock
edge, a flop's reset polarity (by the proof and the state-element check), a stuck register
that left its neighbours unmatched, a changed reset value (by the state-element check alone,
since a reset value is a property of the flop rather than a compared point), and two in
v0.3 logic, both by the proof: a tick counter counting down, and a table read's word never
reaching the OSR. The planted bugs in `pe_core` (reset polarity, reset value and the two v0.3
ones) edit the first `module pe_core (` in the generated file. Hardcaml names one core's
module `pe_core` and the others `pe_core_N`, and that one is `u_core_2`'s at 3 cores and
`u_core_1`'s at 2.

No section fails to prove, so there is no counterexample to analyse.

Limits of the claim:

* It is about the generated Verilog, for the 3-core 8x4 (`tapeout`) and fallback configurations. The OCaml
  source reaches it through Hardcaml's generator, which needs no trust for this claim, since
  the Verilog is what was compared. The `flop4` build and `PE_FIFO_NO_CG` are not proved:
  `flop4` has the lockstep result above, and `PE_FIFO_NO_CG` runs only in the waveform tests
  below, at `pe_fifo` and `pe_fifos` level.
* yosys elaborates both sides. A front-end misreading common to both would not show here;
  the cocotb runs read both with Icarus.
* The proof is combinational at cut points, so the two designs are sequentially equivalent
  from corresponding states. Reset brings flops there. Elements with no reset (the FIFO
  write-data flops, 64 per core, FIFO latches, clock-gate latches, SRAM contents) start from
  the same unknown content, being the same cells. Delays, glitches and metastability are out
  of scope, as in [equivalence.md](equivalence.md).
* No netlist is involved. [equivalence.md](equivalence.md)'s netlist run is for the 6x4 fo6
  netlist against the v0.2 `src/`; this page cites no 8x4 netlist. The port was not
  hardened and has no netlist of its own.

## Stage 2: assembler

`ocaml/asm/` ports `asm.py` and `isa.py`. The regular expressions are `asm.py`'s, copied
verbatim and run by `pyre.ml`, a small backtracking matcher with Python's semantics.
`pyexpr.ml` evaluates what `asm.py` hands to `eval()`, with Python's integers (Zarith),
floor division and modulo, chained comparison and float literals.

The v0.3 syntax is ported as `asm.py` has it: `.data` (a word marked as a table entry),
`mov osr, [src]` (a bracketed last operand is an operand, not a delay, when it names a
location), `cmpw rN, imm16` and `cmp rN, imm` above 8191 as CMPW, `jmp/call !t`,
`flag tog k`, and `ticks` as a source; `isa.disasm` prints the v0.3 forms.

`ocaml/test/asm_parity.py` compares every case byte for byte: a canonical dump (name,
origin, wrap, side-set, config, labels, words, relocation and `.data` marks, source lines)
and the relocated words as little-endian bytes at bases 0 and 256. Error messages compare
exactly, except the tail that Python's own parser writes after `bad expression '...'`,
whose wording is not reproduced. An exception `asm.py` does not catch compares by type.
`firmware/swd_probe.pasm` is filtered out by name before anything opens it.

| case set | assembled, equal | error, equal | uncaught exception, equal | disagree |
|---|---|---|---|---|
| firmware: 70 files, several define sets each | 365 | 408 | 0 | 0 |
| directed error and edge cases | 179 | 148 | 8 | 0 |
| fuzz: 5000 seeded edits of firmware | 997 | 3993 | 10 | 0 |
| corpus: `assemble_all()` calls of the model suite, 535 distinct of 9709 | 498 | 37 | 0 | 0 |

Every one of the 70 files assembled under at least one define set, and 409,700 relocated
bytes were compared. The three files added since the v0.2 run are `eth10_rx_fcs`,
`eth10_tx_fcs` and `usb_enum_ep1`; two of them use v0.3 syntax (`usb_enum_ep1`: `!t`, table
reads, `flag tog`, `.data` descriptors; `eth10_rx_fcs`: CMPW), and the firmware rows include
them. The directed set has 49 v0.3 cases more than at v0.2: table reads with delays and
side-set in both orders, bad table operands and destinations, `.data` alone, with labels and
with words that read as jumps, CMPW at its range ends and with a register, `cmp` around
8192, `!t` spellings, `flag tog` ranges, `ticks` as MOV and IN source and as a refused OUT
destination. The fuzz edits draw v0.3 tokens too (`[r0]`, `ticks`, `cmpw`, `!t`, `tog`,
`.data`), so its stream differs from the v0.2 run's. The error row of the firmware set is
mostly arbitrary define values that do not fit a field. That is a test of the errors: the
working define sets are in the other column and in the corpus, which holds the tests' real
defines.

`ocaml/test/self_check.sh`, part of `evidence.sh`, plants bugs to show the check can fail: 7 in the
assembler (a field shift, an error message, truncating division, a greedy quantifier made
lazy, and in v0.3 syntax `!t` assembled as `t`, `.data` marks dropped, CMPW chosen from 8191),
all caught.

## Stage 3: cycle model

`ocaml/sim/` ports `sim.py` and `line.py`, including the coverage counters. `wl_simcheck`
runs it from a stimulus stream and compares with expected values; `ocaml/test/lock_chip.py`
wraps `sim.Chip` so that any chip feeds such a stream, without changing `sim.py`. The state
line both sides print now carries POLY_HI, the tick counter and a pending table word.

One `sim.py` detail is copied for parity, not because the chip has it: `Core._src` builds
its table of sources as a dict literal, so `_ticks()` runs, and `ticks:read` is counted, on
every read of a non-register source, whichever source it is. The OCaml model counts it the
same way, and coverage counters agree. A consequence for `test_lockstep.py`'s coverage goals
(not for this port): reaching `ticks:read` does not show that a program read the ticks.

Three comparisons, each at a stated level:

* **RTL, sim.py and OCaml at `test_lockstep.py`'s seeds.** `test_lockstep_ocaml.py` runs its
  7 tests unedited, `test_v03_corners` included, with the wrapper on. RTL equals `sim.py`
  every cycle by that test, and `sim.py` equals OCaml every cycle (complete state, pins, bus
  read) by the wrapper. RTL simulation in Icarus.
* **The model test suite.** All of `test/model` except `test_swd.py`, every `Chip` mirrored.
  This is the firmware run: the tests drive it against their pin-level host and device
  models, `test_v03_isa.py` among them. Pins and bus reads are compared every cycle, the
  whole state every 64 cycles and at the end of each chip, and coverage counters at the end.
  Tests with a mismatch run again at stride 1, which separates a test reaching into a chip
  from a real difference, and there the comparison goes on from the state the test wrote.
* **Hardcaml's own simulator.** `line_cyclesim` runs `Pe_line` under Cyclesim against
  `Line` on random traffic, in OCaml alone. `pe_line` is the one module with a single clock
  and no blackboxes; the others need the Verilog path.

| comparison | scope | chips | cycles | result |
|---|---|---|---|---|
| RTL = `sim.py` = OCaml, generated Verilog, 3 cores | seeds 1000-1039, plus the directed lockstep tests | 54 | 113,825 | 8/8 tests, 0 mismatches |
| the same, `src/` itself | the same | 54 | 113,825 | 8/8, 0 mismatches |
| the same, generated Verilog, 2 cores | seeds 1000-1039, plus the directed tests | 54 | 113,551 | 8/8, 0 mismatches |
| RTL = `sim.py` = OCaml, generated Verilog, 3 cores | seeds 5000-5099, plus the directed tests | 114 | 238,671 | 8/8, 0 mismatches |
| `sim.py` = OCaml | model suite: 3148 tests passed, 1 failed (below), 29 skipped | 20,180 | 162,304,809 | see below |
| `sim.py` = OCaml, stride 1 | the 120 tests with a difference (below): 352 chips written into by the test | 369 | 8,073,393 | 2 mismatches, both a stubbed read (below); 0 after the fix |
| `sim.py` = OCaml, after the fix, strides 1 and 64 | `test_kv260_3core.py`, 73 tests | 8 | 1,361,901 | 0 mismatches |
| `sim.py` = OCaml, the whole gate at 567bff6 | model suite: 3149 passed, 0 failed, 29 skipped | 20,180 | 162,627,479 | see below |
| the same, stride 1 | the 118 tests with a difference: 352 chips written into | 367 | 8,073,393 | 0 mismatches |
| `Pe_line` under Cyclesim = OCaml `Line` | seed 7, random traffic | | 2,000,000 | 0 mismatches |

(8 tests: `test_lockstep.py`'s 7 and the final check that every chip agreed with OCaml.)

In the model suite's first pass the state was compared 2,622,941 times, and at stride 1
8,076,830 times, plus pins and bus read every cycle. Cycles on chips that hit a mismatch in
the first pass (5,900,645) are not counted there; those chips are the stride-1 row. The
first-pass figure varies a little by run, as at v0.2, where one cause was found
(`test_usb_uart_pairs.py`'s per-process cache, hence `--dist loadfile`) and some variation
was left unexplained. At v0.2 it was about 155.1M on 19,602 chips; the suite has grown since
(`test_v03_isa.py`, `test_eth10_fcs.py`, `test_usb_enum_ep1.py`, `test_kv260_3core.py` and
others).

**The one failed test** at 0f98335 was the port's own guard:
`test_v03_isa.py::test_v02_firmware_uses_no_v03_encoding` assembles every file in
`firmware/`, `swd_probe.pasm` included, and `ocaml/test/capture_plugin.py`, which records
`assemble_all()` calls for the corpus, then refused SWD source by raising. Since 9a7a63a the
plugin passes an SWD call through to the assembler unrecorded: it never enters the corpus,
so it is in no assembler parity set and no parity evidence on this page. The count of such
calls goes to a `.swd-skipped` file beside the corpus, which `asm_parity.py` does not read;
the 567bff6 run had 1, and no corpus file there holds SWD text. The test reads no chip, so
the model comparison is the same either way, and with the plugin loaded it passes
(`model-567bff6.txt`: 3149 passed). So does `self_check.sh`'s run of `test_v03_isa.py`,
which includes that test (its clean baseline, below).

120 tests showed a difference at stride 64: 43 in `test_pps.py`, 40 in `test_ata_pio.py`, 21
in `test_faults.py` (its `i2c_master` cases), 10 in `test_usb_enum_ep1.py`, 2 in
`test_reverse_engineering.py`, 2 in `test_kv260_3core.py`, and one each in `test_call.py` and
`test_rmii.py`. At stride 1, 118 of them are the test writing into a chip (popping or
setting `core.rxf`, fast-forwarding a wait, setting `lr`, as at v0.2): `lock_chip.py` sends
the written state to `wl_simcheck` as an `L` line, `Chip.load_state` loads it, `wl_simcheck`
checks that it prints back as the same line, and the comparison goes on, with 9106 loads
here. Up to the write the two models were equal every cycle, so a load hides nothing the
OCaml model computed; what it does not check is the written values themselves, which the
test chose.

At 567bff6, 118 tests showed a difference at stride 64, the same files without
`test_kv260_3core.py`, and all were written into, with 9106 loads and 0 mismatches.

The other 2 at 0f98335, both in `test_kv260_3core.py`, replace a chip's `read` with a stub
that returns a made-up ID register, and the wrapper compared the stub's value with the OCaml
model's read: the mismatch was at the ID address only (the model's `0x50452203` against the
stub's v0.2 ID, and the model's `0x50453303` against the stub's 4-core ID). The chip itself
runs on `sim.py`. 856194f makes the wrapper compare `sim.Chip.read`, the model's own, and
count such chips (`read_stubbed_chips`). Rerun at 856194f, the whole file has 0 mismatches at
strides 1 and 64, with 2 stubbed chips, and so it has in the whole gate at 567bff6. 12 deep
copies of chips, made by USB and SPI tests, are not compared after the copy. `test_swd.py`
is excluded. Of the 29 skips, the suite's own summary line says 28 are sigrok checks
(runner1 has no sigrok-cli on its path).

`ocaml/test/self_check.sh` plants 10 bugs in the model: a saturation limit, a delay, the link
value, a loop gain, a coverage counter's name, and five in v0.3 behaviour (`!t` taken on
T = 1, CMPW's immediate cut to 14 bits, a table word leaving the OSR count at 32, the tick
counter stepping by 2, CRC_W[4] read from bit 22), each run against five model test files,
`test_v03_isa.py` now one of them. It plants 2 more that only a chip written into reaches, on
the `test_rmii.py` test that fills `core.rxf` by hand: the FIFO read taking the wrong end, and
`load_state` swapping the two FIFOs. Each must be caught with the load and missed with
`WL_LOCK_RESYNC=0`. Since 567bff6 the script first runs every check once on the unmutated
tree, and exits 1 unless that baseline is clean: no lockstep failure on the five files and
pytest passing, the written-into test clean with and without the load, `asm_parity.py` and
the waveform tests passing. Otherwise a failure already there would make every case look
caught. All 12 were caught, the 2 written-into cases as required (caught with
the load, missed without). At 567bff6 the whole script, baseline first, again caught all 25
of its planted bugs (`self-check-567bff6.txt`); the baseline's five files gave 105 passed.

### Waveform tests

`ocaml/wave_test/` holds Hardcaml expect tests: a block runs under Cyclesim, its waveform is
printed into the source with `[%expect]`, and `dune test` fails if it changes. Each test also
asserts the behaviour with `[%test_result]`, on values worked out from docs/isa.md rather than
copied from a run. A failed assertion fails the test. `dune --auto-promote` would write that
failure into the source as an exception record, which alone would make the test pass, so a
dune rule in `wave_test/` fails `dune test` on any such record. A waveform change that no
assertion covers can still be promoted; the diff is the only guard there. There are 7 tests,
2 in `test_fifo.ml` and 5 in `test_line.ml`. The FIFOs are built with `log2:2`, 4 deep, where
the 3-core 8x4 build's are 8 deep.

| block | what is asserted |
|---|---|
| `pe_fifo` | full and empty judged on start-of-cycle state: push at full dropped even with a pop, pop at empty ignored, push kept at empty, clear beats push |
| `pe_fifos`, join | TXF to RXF to core, 8 words in order, the ninth dropped (`log2:2`; 16 words at the 8x4 build's depth, not tested here) |
| `pe_line` TX | NRZ bit by bit, then release; Manchester halves and the END symbol; equal-bit stuffing, where the stuff bit starts the next run |
| `pe_line` RX | Manchester bits and the frame end; an edge's position in the clock moves the frame end by one clock, 13 against 14 |

Cyclesim runs one clock and cannot open a blackbox. So the FIFOs run in the `PE_FIFO_NO_CG`
form, with enable flops in place of the latches and clock gates that the proved build uses.
`pe_line` is the proved logic as it stands. `pe_core` has clock-gated registers and
`pe_hostport` has four clock domains, so neither has a waveform test.

`self_check.sh` plants 5 RTL bugs for these tests, all caught. The first run missed one, a
swap of the two edge ages; the frame-end test above was added for it. It also checks that
promoting a failed assertion leaves `dune test` failing, and that the dune rule is what makes
it fail; at 9220637 that case was run alone, and at 0f98335 it ran in the whole
`evidence.sh`: failing with the rule, passing with it removed.

## Stage 4: host tools

Done, in value order: `host.py` and `profile.py` (`ocaml/asm/host.ml`, with the v0.3
`pkg3` profile and `load_program` not checking `.data` words as jumps), `export.py`
(`wl_export`), `isa.disasm` and `Program.listing`, `modelhost.py` (`ocaml/sim/modelhost.ml`,
now with the core and bank counts as options), and `wl_run`, which goes from source to pin
changes with no Python.

| check | compared | disagree |
|---|---|---|
| `wl_export` against `python -m pe.export`, success | 828 runs | 0 |
| the same, both fail with the same message | 404 runs | 0 |
| `load_program` writes | 175 programs (the `.word` program is refused, as by `host.py`) | 0 |
| `Program.listing` | 176 programs | 0 |
| `isa.disasm` | 200,000 words | 0 |
| `wl_run` against the same run through `pe.modelhost` | 8 firmware, 3714 lines of pin changes | 0 |

`host_parity.py` covers all 70 firmware files, among them the two that use v0.3 syntax
(`usb_enum_ep1`: `!t`, table reads, `flag tog`, `.data` descriptors; `eth10_rx_fcs`: CMPW),
and two v0.3 programs it writes itself: one with table reads, CMPW, `!t`, `flag tog`, ticks
and a `.data` word that reads as a jump out of every bank (which `load_program` must accept),
and one with the same word as `.word` (which it must refuse, with the same message as
`host.py`). The parity scripts, the model suite and lockstep fail below floors on their
counts.

## Reproduce

On runner1, from a checkout of commit 567bff6 or later:

```
ocaml/scripts/setup_runner.sh SYSROOT    # once
export OPAMROOT=$HOME/.opam PATH=$HOME/.local/bin:$HOME/tools/oss-cad-suite/bin:$PATH \
       LIBRARY_PATH=SYSROOT/usr/lib/x86_64-linux-gnu
eval "$(opam env --switch=wl --set-switch)"
cd ocaml && dune build
scripts/evidence.sh OUT         # every gate above, including self_check.sh; OUT/summary.txt
```

`evidence.sh` took 92 minutes (5490 s) at 0f98335, in three phases of about 10 processes:
the cocotb suites and both proofs 3038 s, the model suite and host checks 1351 s, the
assembler parity and `self_check.sh` 1101 s. Another job's harden ran beside it on runner1
(load about 8 to 16). The v0.2 run, everything at once, took 32 minutes. One gate at a time:
`formal/equiv_port.sh -j 4 --mutants [--build fallback] OUT`,
`scripts/run_cocotb.sh WORK test test2 test_post_freeze test_bench_fw test_pps lockstep lockstep2`,
`scripts/stage3_model_suite.sh OUT`, `python3 test/asm_parity.py --fuzz 5000`.

Toolchain: OCaml 5.3.0, dune 3.24.2, hardcaml and hardcaml_waveterm v0.17 from opam, Yosys
0.69+158, Icarus and cocotb from OSS CAD Suite. No sudo: runner1 lacks libgmp-dev, which zarith needs, so
`setup_runner.sh` unpacks it under a private sysroot. The switch takes 1.6 GB in `~/.opam`.

## Not done

* `pe_core` and `pe_hostport` have no waveform test, for the reason above; the v0.3 logic is
  in `pe_core`, so it has the proof, the lockstep and the planted bugs, but no waveform test.
* `hardcaml_verify` is not used. The proof runs in yosys on the generated Verilog, as gate (b)
  asked. No Hardcaml-level proof exists.
* The `ifdef FORMAL` assertions are not ported, v0.3's new ones included; they remain proved
  on `src/`.
* Host tools not ported: `timing.py` (the static deadline checker, next in value, which v0.3
  also changed for the table read's clock), `la.py`, `sigrok.py`, `randprog.py`, the protocol
  helpers (`postfreeze*.py`, `usb*.py`, `i2c*.py`, `uartcapture*.py`, `spipacked.py`,
  `line`-level host models). `isa.py`'s `with_imm16`, `v03_feature` and `crcctrl` and
  `sim.py`'s `known_id` are not ported. SWD tools are excluded by rule.
* `spi_frame`, `coalesce`, `start_writes` and `pin_owner_writes` in `host.ml` have no parity
  test of their own; nor has `pkg3`, which `wl_export` does not offer (`export.py` has no
  such option either).
* Model suite tests that deep-copy a chip are compared up to the copy, not on the copy. Tests
  that write into a chip are compared on from the written state, at stride 1 only; the
  written values are the test's own and are not checked. See the counts above.
* The Python assembler's non-ASCII whitespace handling is not reproduced, outside line
  endings. No firmware instruction line depends on it.
