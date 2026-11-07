# The Hardcaml and OCaml port

`ocaml/` is a second implementation of Wirelingo: the RTL in Hardcaml, and the
assembler, cycle model and main host tools in OCaml. The frozen chip stays the
Verilog in `src/`, which this work does not edit.

**Evidence level.** Everything below is a statement about the port. "Equivalent" means
the generated Verilog equals `src/` by a formal check; "lockstep" means simulation. None
of it is silicon or FPGA evidence, and it adds nothing to what is known about the chip
itself. Its use is narrower: anything already established for `src/` at RTL level carries
over to the generated Verilog through the proof, and the OCaml tools can stand in for the
Python ones where they were compared.

All numbers are from one run of `ocaml/scripts/evidence.sh` on runner1 at commit a167455,
base 8c9599c, except where Stage 3 compares it with a second run. The commit after it only
changes this file and `evidence/README.md`, and adds the run's `summary.txt` and `rc.txt` in
[evidence/ocaml-parity-20261104/](../evidence/ocaml-parity-20261104/README.md). The generated
tapeout Verilog has sha256 `3b9a573559b1a1c1…`, as at the earlier run (9220637, base af2c097).

Since that run `src/`, `asm.py`, `isa.py`, `sim.py` and `line.py` have not changed; firmware
and tests have. The same script, unchanged, passed every gate on a clean checkout of 8c9599c:
the 10 new firmware files assemble byte for byte, and the new model tests show no model
difference. Three things had lost coverage, and a167455 restores them, in `ocaml/` only:
CI's test job had gained `test_bench_fw` and `test_pps`, which the cocotb gate did not run;
108 new model tests write into their chips, which were then compared only up to the write
(Stage 3); and the coverage floors were still the old counts.

| stage | gate | result |
|---|---|---|
| 1 | cocotb `test`, `test_post_freeze`, `test_bench_fw`, `test_pps`, CI tapeout lockstep on generated Verilog | 27/27, 16/16, 5/5, 4/4, 6/6 |
| 1 | formal equivalence with `src/`, tapeout build | proved: 7444 of 7444 points by `equiv_simple`, ABC `&cec` equivalent |
| 2 | assembler, every firmware but `swd_probe` | 67 files byte-identical, no disagreement in 6371 cases |
| 3 | cycle model against `sim.py` and RTL | no difference in 102,738 + 227,254 cycles three-way, nor in about 155 million cycles of the model suite and 7.1 million more on the chips its tests write into |
| 4 | host tools | `host`, `profile`, `export`, `disasm`, `listing`, `modelhost` ported, parity holds |

## Stage 1: RTL

`ocaml/rtl/` has one file per Verilog module: `project`, `pe_chip`, `pe_core`, `pe_line`,
`pe_hostport`, `pe_fifos`, `pe_fifo`, `pe_imem` (with `pe_imem_bank`). It uses `Interface`
for ports, `Hierarchy.In_scope` for the module tree, `Always` for the execute, line and
host-port blocks, and `Reg_spec` for the clock domains: rising and falling `clk`, rising
and falling `sck`, gated clocks, and asynchronous resets from `rst_n` and from `cs_n`.

Stays Verilog: `src/pe_cg.sv` (`pe_cg`, `pe_latch`), instantiated with `Instantiation` and
read unchanged, and the SRAM macro, instantiated with the same tie-offs. So the clock gates
and latches are the same source on both sides, and their cells are cut points.

Build parameters are OCaml values (`Config`): `tapeout` (2 cores, 2 SRAM banks, 8-deep
FIFOs) and `flop4` (CI's 4-core flop build). The `ifdef FORMAL` blocks are not ported.

The Verilog next-state block in `pe_line` uses blocking assignments, so a later statement
reads what an earlier one wrote; an `Always` variable's value is always its final value.
The transmitter is therefore two blocks, and the task `deliver` is a pure function.

### Gate (a): the cocotb suites

`ocaml/scripts/run_cocotb.sh` copies `test/ tools/ firmware/ src/` at HEAD to a throwaway
directory and runs make with `SRC_DIR` and `PROJECT_SOURCES` overridden. `test/` and its
Makefile are not edited. Each suite also passes `check_results.py` with its full test count.

| suite | configuration | result |
|---|---|---|
| `test` | tapeout, `tb.v` | 27/27 |
| `test_post_freeze` | tapeout, `tb.v` | 16/16 |
| lockstep | CI tapeout: 2 cores, 2 banks, FIFO_LOG2 3, 40 seeds x 1000 | 6/6 |
| lockstep | CI 4-core flop build, 30 seeds x 1000, `LOCKSTEP_GOALS=core` | 6/6 |
| `test_pio`, `test_eth_service` | tapeout | 2/2, 2/2 |
| `test_spi_packed_host`, `test_spi_preloaded_host` | tapeout | 1/1, 1/1 |
| `test_uart_capture_host`, `test_usb_uart_rtl` | tapeout | 1/1, 3/3 |
| `test_bench_fw`, `test_pps` | tapeout, `tb.v` | 5/5, 4/4 |

That is every cocotb step of CI's test job. RTL simulation in Icarus. `test_bench_fw` and
`test_pps` each have one test that skips by design, and the check takes CI's list of the tests
that must pass: `bench_sigrok_available` needs sigrok-cli, which runner1 does not have on its
path, as CI does not, and `pps_one_second` runs only with `PPS_LONG=1`.

`tb.v` instantiates the generated top level directly. `tb_chip.v` needs one disclosed
adapter. It instantiates `pe_chip` with parameters, and `test_lockstep.py` reads state
through generate blocks (`u_chip.g_core[0].g_on.u_core.pc`). Generated Verilog has neither,
so for `TB=chip` the generator also writes `pe_chip_lockstep.v`: a `pe_chip` with those
parameters that checks them against the build, instantiates the Hardcaml chip as
`pe_chip_hc`, and mirrors the state as read-only wires in blocks of those names. It is test
scaffolding (`ocaml/rtl/lockstep_shim.ml`), outside the proof, and it only reads.

### Gate (b): formal equivalence

`ocaml/formal/equiv_port.sh` uses the method of [equivalence.md](equivalence.md), and imports
`formal/equiv/`'s ABC and `equiv_simple` steps unchanged, with the generated Verilog in the
netlist's place. Gold is `src/` with the flow's defines, tapeout parameters by default in
`project.v`. Gate is `wirelingo_hc.v` plus `src/pe_cg.sv`. Both are elaborated by the same
yosys (`proc`, `flatten`). Then:

1. Ports of all modules, by name, direction and width.
2. Every flop is matched by name, and clock edge, reset edge and reset value must be
   identical. Latches, clock gates and SRAMs are matched by instance name.
3. Both designs are cut at those state elements. Next state, clock and reset of each flop,
   each black-box input and each top-level output must be equal for all inputs and all
   states, reachable or not.
4. Two engines prove it: ABC `&cec` on the whole miter, with every `x` of `src/` a free
   input, and yosys `equiv_simple` per point in parallel shards.
5. Planted bugs in the generated Verilog must each be caught.

Result for the tapeout build, yosys 0.69+158, 418 s with `-j 4` on runner1:

| check | count |
|---|---|
| modules with equal ports | 11 of 11 |
| flops matched | 1652 of 1652 on both sides |
| instances matched (1024 latches, 54 clock gates, 2 SRAMs) | 1080 of 1080 |
| next state (D) | 1652 |
| clock | 1652 |
| asynchronous reset | 1652 |
| latch D and GATE | 2048 |
| clock-gate CLK and GATE | 108 |
| SRAM inputs | 308 |
| outputs `uo_out`, `uio_out`, `uio_oe` | 24 |
| **total compared points** | **7444** |
| `equiv_simple` | 7444 proven, 0 unproven |
| ABC `&cec` | equivalent, with `src/`'s `x` values free |

All 8 planted bugs were caught: an AND made an OR, swapped mux legs, swapped clock-gate
enables, swapped SRAM address legs (these four by the proof), a flop's clock edge, a flop's
reset polarity (by the proof and the state-element check), a stuck register that left its
neighbours unmatched, and a changed reset value (by the state-element check alone, since a
reset value is a property of the flop rather than a compared point).

No section fails to prove, so there is no counterexample to analyse.

Limits of the claim:

* It is about the generated Verilog, for the tapeout configuration. The OCaml source reaches
  it through Hardcaml's generator, which needs no trust for this claim, since the Verilog is
  what was compared. The `flop4` build and `PE_FIFO_NO_CG` are not proved: `flop4` has the
  lockstep result above, and `PE_FIFO_NO_CG` runs only in the waveform tests below, at
  `pe_fifo` and `pe_fifos` level.
* yosys elaborates both sides. A front-end misreading common to both would not show here;
  the cocotb runs read both with Icarus.
* The proof is combinational at cut points, so the two designs are sequentially equivalent
  from corresponding states. Reset brings flops there. Elements with no reset (the 128 FIFO
  write-data flops, FIFO latches, clock-gate latches, SRAM contents) start from the same
  unknown content, being the same cells. Delays, glitches and metastability are out of scope,
  as in [equivalence.md](equivalence.md).
* `src/`'s physical netlist equals `src/` by the separate run in equivalence.md. The port
  was not hardened and has no netlist of its own.

## Stage 2: assembler

`ocaml/asm/` ports `asm.py` and `isa.py`. The regular expressions are `asm.py`'s, copied
verbatim and run by `pyre.ml`, a small backtracking matcher with Python's semantics.
`pyexpr.ml` evaluates what `asm.py` hands to `eval()`, with Python's integers (Zarith),
floor division and modulo, chained comparison and float literals.

`ocaml/test/asm_parity.py` compares every case byte for byte: a canonical dump (name,
origin, wrap, side-set, config, labels, words, relocation marks, source lines) and the
relocated words as little-endian bytes at bases 0 and 256. Error messages compare exactly,
except the tail that Python's own parser writes after `bad expression '...'`, whose wording
is not reproduced. An exception `asm.py` does not catch compares by type.
`firmware/swd_probe.pasm` is filtered out by name before anything opens it.

| case set | assembled, equal | error, equal | uncaught exception, equal | disagree |
|---|---|---|---|---|
| firmware: 67 files, several define sets each | 350 | 393 | 0 | 0 |
| directed error and edge cases | 137 | 117 | 8 | 0 |
| fuzz: 5000 seeded edits of firmware | 986 | 4007 | 7 | 0 |
| corpus: `assemble_all()` calls of the model suite, 366 distinct of 9350 | 342 | 24 | 0 | 0 |

Every one of the 67 files assembled under at least one define set. 367,348 relocated bytes
were compared. The 10 files added since the 9220637 run are `ata_pio`, `eth10_reply`,
`eth10_tx_cs`, `hd44780_4bit`, `mdio_master`, `mdio_phy`, `pps_capture`, `rmii_match`,
`rmii_rx` and `rmii_tx`; the changed ones include `usb_fs_dev` (142 words), `usb_ls_dev`
(130) and `usb_ls_tx` (56). The assembler needed no change for any of them. The error row of the firmware set is mostly arbitrary define values that do
not fit a field. That is a test of the errors: the working define sets are in the other
column and in the corpus, which holds the tests' real defines.

`test/self_check.sh`, part of `evidence.sh`, plants bugs to show the check can fail: 4 in the
assembler (a field shift, an error message, truncating division, a greedy quantifier made
lazy), all caught.

## Stage 3: cycle model

`ocaml/sim/` ports `sim.py` and `line.py`, including the coverage counters. `wl_simcheck`
runs it from a stimulus stream and compares with expected values; `ocaml/test/lock_chip.py`
wraps `sim.Chip` so that any chip feeds such a stream, without changing `sim.py`.

Three comparisons, each at a stated level:

* **RTL, sim.py and OCaml at `test_lockstep.py`'s seeds.** `test_lockstep_ocaml.py` runs its
  tests unedited with the wrapper on. RTL equals `sim.py` every cycle by that test, and
  `sim.py` equals OCaml every cycle (complete state, pins, bus read) by the wrapper. RTL
  simulation in Icarus.
* **The model test suite.** All of `test/model` except `test_swd.py`, every `Chip` mirrored.
  This is the firmware run: the tests drive it against their pin-level host and device
  models. Pins and bus reads are compared every cycle, the whole state every 64 cycles and at
  the end of each chip, and coverage counters at the end. Tests with a mismatch run again at
  stride 1, which separates a test reaching into a chip from a real difference, and there the
  comparison goes on from the state the test wrote.
* **Hardcaml's own simulator.** `line_cyclesim` runs `Pe_line` under Cyclesim against
  `Line` on random traffic, in OCaml alone. `pe_line` is the one module with a single clock
  and no blackboxes; the others need the Verilog path.

| comparison | scope | cycles | result |
|---|---|---|---|
| RTL = `sim.py` = OCaml, generated Verilog | seeds 1000-1039, plus the directed lockstep tests | 102,738 | 7/7 tests, 0 mismatches |
| RTL = `sim.py` = OCaml, `src/` itself | the same | 102,738 | 7/7, 0 mismatches |
| RTL = `sim.py` = OCaml, generated Verilog | seeds 5000-5099, plus the directed tests | 227,254 | 7/7, 0 mismatches |
| `sim.py` = OCaml | model suite: 2872 tests passed, 29 skipped, 19,602 chips | 155,099,397 | see below |
| `sim.py` = OCaml, stride 1 | the 108 tests with a state difference (below): 357 chips, 342 written into by the test | 7,135,772 | 0 mismatches |
| `Pe_line` under Cyclesim = OCaml `Line` | seed 7, random traffic | 2,000,000 | 0 mismatches |

In the model suite's first pass the state was compared 2,500,279 times, and at stride 1
7,136,021 times, plus pins and bus read every cycle.

The first-pass figure varies by run: treat it as about 155.1M. The run at 8c9599c, whose
model and wrapper are the same at stride 64, gave 155,135,208, with the same 19,602 chips.
(At 9220637, before the new tests, it was about 63.7M on 1262 chips.) One cause is found:
`test_usb_uart_pairs.py` keeps runs in a per-process `lru_cache`, so under plain `-n 8` a run
repeats in each worker that needs it; the script uses `--dist loadfile` for that. Some
variation remains, with the same chip count and different coverage counts, so some test's run
length differs between runs. Its cause is not found. Cycles on chips that hit a mismatch in
the first pass (5,258,220) are not counted there; those chips are the stride-1 row.

108 tests showed a state difference: 43 in `test_pps.py`, 40 in `test_ata_pio.py`, 21 in
`test_faults.py` (its `i2c_master` cases), 2 in `test_reverse_engineering.py`, and one each in
`test_call.py` and `test_rmii.py`. At stride 1 every one of them is the test writing into a
chip: popping `core.rxf` directly (the ATA, PPS and fault harnesses, the capture tests),
setting `core.rxf` by hand (`test_rmii.py`), fast-forwarding a wait by setting registers and
`chip.cycle` (`test_pps.py`), and setting `lr` (`test_lr_survives_restart`). At the 9220637
run such a chip was compared only up to the write; there were 10. Now `lock_chip.py` sends
the written state to `wl_simcheck` as an `L` line, `Chip.load_state` loads it, `wl_simcheck`
checks that it prints back as the same line, and the comparison goes on. Up to the write the
two models were equal every cycle, so a load hides nothing the OCaml model computed; what it
does not check is the written values themselves, which the test chose. So the 342
written-into chips are compared to their end, coverage counters included, with 9050 loads:
7,135,772 cycles, 0 mismatches. Under the old rule (`WL_LOCK_RESYNC=0`; the run at 8c9599c)
the same pass compared 3,833,185 cycles. 12 deep copies of chips, made by USB and SPI tests,
are not compared after the copy. `test_swd.py` is excluded. Of the 29 skips, the suite's own
summary line says 28 are sigrok checks (runner1 has no sigrok-cli on its path); the skips
were not itemised further.

`test/self_check.sh` plants 5 bugs in the model (a saturation limit, a delay, the link
value, a loop gain, a coverage counter's name), all caught. The verifier found the last one
missed by the first version of the script, whose test files never touched that counter;
`test_i2c_timeout.py` does. It plants 2 more that only a chip written into reaches, on the
`test_rmii.py` test that fills `core.rxf` by hand: the FIFO read taking the wrong end, and
`load_state` swapping the two FIFOs. Each is caught with the load and missed with
`WL_LOCK_RESYNC=0`, as the script requires.

### Waveform tests

`ocaml/wave_test/` holds Hardcaml expect tests: a block runs under Cyclesim, its waveform is
printed into the source with `[%expect]`, and `dune test` fails if it changes. Each test also
asserts the behaviour with `[%test_result]`, on values worked out from docs/isa.md rather than
copied from a run. A failed assertion fails the test. `dune --auto-promote` would write that
failure into the source as an exception record, which alone would make the test pass, so a
dune rule in `wave_test/` fails `dune test` on any such record. A waveform change that no
assertion covers can still be promoted; the diff is the only guard there. There are 7 tests,
2 in `test_fifo.ml` and 5 in `test_line.ml`. The FIFOs are built with `log2:2`, 4 deep, where
the tapeout's are 8 deep.

| block | what is asserted |
|---|---|
| `pe_fifo` | full and empty judged on start-of-cycle state: push at full dropped even with a pop, pop at empty ignored, push kept at empty, clear beats push |
| `pe_fifos`, join | TXF to RXF to core, 8 words in order, the ninth dropped (`log2:2`; 16 words at the tapeout's depth, not tested here) |
| `pe_line` TX | NRZ bit by bit, then release; Manchester halves and the END symbol; equal-bit stuffing, where the stuff bit starts the next run |
| `pe_line` RX | Manchester bits and the frame end; an edge's position in the clock moves the frame end by one clock, 13 against 14 |

Cyclesim runs one clock and cannot open a blackbox. So the FIFOs run in the `PE_FIFO_NO_CG`
form, with enable flops in place of the latches and clock gates that the proved build uses.
`pe_line` is the proved logic as it stands. `pe_core` has clock-gated registers and
`pe_hostport` has four clock domains, so neither has a waveform test.

`self_check.sh` plants 5 RTL bugs for these tests, all caught. The first run missed one, a
swap of the two edge ages; the frame-end test above was added for it. It also checks that
promoting a failed assertion leaves `dune test` failing, and that the dune rule is what makes
it fail; at 9220637 that case was run alone, and at the commit above it ran in the whole
`evidence.sh`: failing with the rule, passing with it removed.

## Stage 4: host tools

Done, in value order: `host.py` and `profile.py` (`ocaml/asm/host.ml`), `export.py`
(`wl_export`), `isa.disasm` and `Program.listing`, `modelhost.py` (`ocaml/sim/modelhost.ml`),
and `wl_run`, which goes from source to pin changes with no Python.

| check | compared | disagree |
|---|---|---|
| `wl_export` against `python -m pe.export`, success | 796 runs | 0 |
| the same, both fail with the same message | 373 runs | 0 |
| `load_program` writes | 167 programs | 0 |
| `Program.listing` | 167 programs | 0 |
| `isa.disasm` | 200,000 words | 0 |
| `wl_run` against the same run through `pe.modelhost` | 8 firmware, 3714 lines of pin changes | 0 |

`host_parity.py` covers all 67 firmware files. An earlier figure here was smaller: a change to
the define sets in `asm_parity.py` had silently cut this check's coverage by more than half,
with 0 disagreements. It now takes up to three define sets per file that really assemble, and
the parity scripts, the model suite and lockstep fail below floors on their counts.

## Reproduce

On runner1, from a checkout of commit a167455 or later:

```
ocaml/scripts/setup_runner.sh ~/protoemu-flow/ocaml-port/sysroot    # once
export OPAMROOT=$HOME/.opam PATH=$HOME/.local/bin:$HOME/tools/oss-cad-suite/bin:$PATH \
       LIBRARY_PATH=$HOME/protoemu-flow/ocaml-port/sysroot/usr/lib/x86_64-linux-gnu
eval "$(opam env --switch=wl --set-switch)"
cd ocaml && dune build
scripts/evidence.sh OUT         # every gate above, including self_check.sh; OUT/summary.txt
```

`evidence.sh` took 32 minutes (1899 s) at the commit above, with runner1's load at about 10 to 16
when it started, and 41 minutes (2450 s) at 8c9599c; at 9220637 it took 66 minutes with
runner1 busy (load 20 to 50). One gate at a time:
`formal/equiv_port.sh -j 4 --mutants OUT`,
`scripts/run_cocotb.sh WORK test test_post_freeze test_bench_fw test_pps lockstep`,
`scripts/stage3_model_suite.sh OUT`, `python3 test/asm_parity.py --fuzz 5000`.

Toolchain: OCaml 5.3.0, dune 3.24.2, hardcaml and hardcaml_waveterm v0.17 from opam, Yosys 0.69+158, Icarus and
cocotb from OSS CAD Suite. No sudo: runner1 lacks libgmp-dev, which zarith needs, so
`setup_runner.sh` unpacks it under a private sysroot. The switch takes 1.6 GB in `~/.opam`.

## Not done

* `pe_core` and `pe_hostport` have no waveform test, for the reason above.
* `hardcaml_verify` is not used. The proof runs in yosys on the generated Verilog, as gate (b)
  asked. No Hardcaml-level proof exists.
* The `ifdef FORMAL` assertions are not ported; they remain proved on `src/`.
* Host tools not ported: `timing.py` (the static deadline checker, next in value), `la.py`,
  `sigrok.py`, the protocol helpers (`postfreeze*.py`, `usb*.py`, `i2c*.py`, `uartcapture*.py`,
  `spipacked.py`, `line`-level host models). SWD tools are excluded by rule.
* `spi_frame`, `coalesce`, `start_writes` and `pin_owner_writes` in `host.ml` have no parity
  test of their own.
* Model suite tests that deep-copy a chip are compared up to the copy, not on the copy. Tests
  that write into a chip are compared on from the written state, at stride 1 only; the
  written values are the test's own and are not checked. See the counts above.
* The Python assembler's non-ASCII whitespace handling is not reproduced, outside line
  endings. No firmware instruction line depends on it.
