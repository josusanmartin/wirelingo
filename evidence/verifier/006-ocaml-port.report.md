VERDICT: FAIL

Commit 03edc75, base 61af9b2. The important result holds: the generated Verilog is equal to `src/`, and I could not break that proof. `evidence.sh` passes as a whole at the final commit, which it had not done before. It fails on two things outside the proof. `ocaml/test/self_check.sh` exits 1, while the doc says all planted bugs were caught, and the host parity figures in the doc do not reproduce at this commit, because a harness change cut that check's coverage by more than half.

| Claim | Result |
|---|---|
| 1. Cocotb 25/25, 16/16, 6/6; shim cannot mask a difference | PASS |
| 2. Equivalent to `src/`: 7444/7444, ABC, 1652/1652, 1080/1080, 8/8 | PASS, attacked hard (below) |
| 3. 57 firmwares, 6202 cases, 0 disagreements | PASS |
| 4. 102,738 and 227,254 cycles, 0 mismatches | PASS, exact |
| 5. Model suite 63.9M cycles, 0 real mismatches | PASS on "0 real"; the cycle count varies by run (finding 4) |
| 6. Host tools ported with parity | Parity PASS, doc figures FAIL (finding 2) |
| 7. Docs honest about levels and gaps | PASS, except findings 1 and 2 |

## Findings

### Blocking

1. `docs/hardcaml-port.md:199-200`, `ocaml/test/self_check.sh:49`: "planted 5 bugs in the model ... all caught" is false for the committed script. It exits 1 with `missed: 1`.
   - `"wait:timeout"` changed to `"wait:timed_out"` reports `caught=no`. Repeating that mutation by hand on the script's three files gave 40 passed and 0 fail files.
   - The cause is the script's test choice, `test_eth10.py`, `test_call.py` and `test_uart.py`: none of them produces a difference on that counter. On `test/model/test_i2c_timeout.py` the same mutation gives 4 fail files, so the coverage comparison itself works.
   - Fix: add that test file to the script.

2. `docs/hardcaml-port.md:210-213`: the host parity figures do not reproduce, and `eff2b0f` caused it.

   | Check | Doc (0027302) | At 03edc75 | With the one-line fix |
   |---|---|---|---|
   | `wl_export` success | 408 | **182** | 630 |
   | Both fail, same message | 166 | **56** | 308 |
   | `load_program` writes | 82 | **34** | 134 |
   | `Program.listing` | 82 | **34** | 134 |
   | Disagreements | 0 | 0 | 0 |

   - `eff2b0f` added a third fixed define set to `define_sets()` in `ocaml/test/asm_parity.py`. `ocaml/test/host_parity.py:39` takes `define_sets(text)[:3]`, so those slots now hold only the three fixed sets, which most firmware cannot assemble with.
   - The doc is open that these figures are from 0027302, but `:15-16` says the two commits "differ only in `ocaml/test/`", as if that were harmless. Here it is what changed the result.
   - Changing the slice to `[3:6]` restores wide coverage with 0 disagreements, so parity itself holds.
   - Fix: repair the slice and quote numbers from the final commit.

### Should fix

3. `ocaml/scripts/evidence.sh`: it does not run `self_check.sh`, so its all-clear does not cover finding 1. It also checks only exit codes for `host_parity.py`, so it cannot notice a drop like finding 2. A floor on those counts would have caught it.

4. `docs/hardcaml-port.md:24,188,191`: an exact cycle count for a figure that varies between runs.

   | Run | Chips | Cycles | State compares | Corpus calls |
   |---|---|---|---|---|
   | Builder, 0027302 | 1263 | 63,897,940 | 998,858 | 1412 |
   | Builder, eff2b0f (doc) | 1263 | 63,876,142 | 998,520 | 1412 |
   | Mine, evidence run | 1262 | 63,744,240 | 996,458 | 1410 |
   | Mine, rerun | 1263 | 63,901,054 | 998,909 | not read |

   All four runs show 10 flagged chips and 12 copies. My two show 1394 passed, 23 skipped, and 0 real mismatches at stride 1. Claim 5's "63.9M" is not the figure of every run. Say "about 63.8M, varying by run". I did not find the cause of the variation.

5. **Rule conflict, for the orchestrator.** `remote_test.sh model` runs all of `test/model` with no ignore, so it includes `test/model/test_swd.py`. The base count of 1395 passed against 1394 here, with that file excluded, shows it contributes a test. My runs for items 000-005 therefore ran it. RULES.md prescribes that command and also says no SWD work of any kind, and this port's scripts exclude the file. I did not open it, and I ran no SWD file on purpose. Decide whether `remote_test.sh` should ignore it.

### Minor

6. `docs/hardcaml-port.md:227`: "about an hour" is generous. The evidence run and `self_check.sh` together finished in under 25 minutes on a quiet runner1.

## Claim 2: how I attacked the proof

**Gold is the unmodified `src/`.**
- `git diff 61af9b2 03edc75 -- src formal test tools firmware flow` is empty. The only file outside `ocaml/` is the doc.
- All 10 files the proof reads, including `pe_config.vh`, match `~/protoemu-flow/protoemu-exp-fo6/src` byte for byte.
- Gold really is read: a bug planted in `src/pe_core.sv` gives NOT PROVEN.

**Nothing makes it vacuous.**
- The script has no assume, and `FORMAL` is not defined. `FORMAL` blocks are not compiled on either side.
- The point count adds up with nothing missing: 1652 x 3 + 2048 + 108 + 308 + 24 = 7444. The SRAM's 308 is exactly 2 x 154 pins, tie-offs and BIST included. 7444 is also `docs/equivalence.md`'s 7429 plus 3 x 5, for the five flops synthesis merged.
- Name matching cannot hide a bug. Each flop's D, clock and reset are compared under a complete one-to-one pairing, so swapped names would fail, and type and reset value are checked separately.
- Unexpected cell types stop the run.
- Blackboxes hide no logic. Only three cell types are allowed, every one of their inputs is a compared point, and both sides use the same `src/pe_cg.sv`. The cell contents are out of scope, as the doc says.

**My planted bugs, made in the Hardcaml source and regenerated.** The first run used ABC only. The reachable rare bug and its unreachable twin also ran with `equiv_simple`.

| Bug | Result |
|---|---|
| Control, no change | EQUIVALENT, hash `3b9a573559b1a1c1` as in the doc |
| Rare, reachable: wrap also taken at `pc = 200`, `lr = 777` | **NOT PROVEN**: ABC not equivalent, `equiv_simple` 7417/7444 |
| Host output enable loses `cs_n` | NOT PROVEN |
| SRAM `A_DLY` tied to gnd | NOT PROVEN |
| SRAM BIST data tied high | NOT PROVEN |
| `cs_n` reset edge flipped | NOT PROVEN, 53 type mismatches |
| Rare: line `quiet` saturates at 14 | NOT PROVEN |
| HOSTCFG at the wrong address | NOT PROVEN |
| Gold: `src/pe_core.sv`, pc + 2 | NOT PROVEN |
| Same rare bug at `pc = 1023` | EQUIVALENT by both engines, **correctly** |

The last row looked like a hole at first, and I chased it. `pc = 1023` has `pc[9:8] = 3`, and with 2 banks `bank_ok` (`src/pe_core.sv:212`) is false, so that instruction never commits and `pc_seq` is unused. The change has no observable effect, and the same bug at `pc = 200` is caught. The builder's 8 of 8 also reproduced.

## Claim 1: the shim

It cannot mask a difference.
- The lockstep file `pe_chip_impl.v` is generated separately from the proved `wirelingo_hc.v`, so I compared them: all 8 modules are identical after the rename. Only `pe_hostport` and the top level are absent, which is lockstep's normal scope.
- The test's only writes go to `pe_chip` ports. It writes nothing through the mirrors.
- All 87 mirrors match their signals' widths, so nothing is truncated.
- A bug visible only through a mirror, `lr` off by one, fails lockstep on `test_random_lockstep` and `test_call_ret`.
- The doc's claim that these are all of CI's cocotb steps is consistent with `.github/workflows/test.yaml`.

## Claim 5: "real"

Agreed, with the limits the doc states.
- The 3 tests with a stride-64 mismatch are clean at stride 1: 10 chips flagged as poked, 0 mismatches. Those chips are compared only up to the write. The 12 deep copies are not compared after the copy.
- A stride of 64 could in principle miss a difference that heals within 63 cycles. So I ran five files at stride 1, `test_eth10`, `test_can`, `test_i2c_timeout`, `test_uart` and `test_call`: 4,927,424 cycles, 4,927,497 state compares, 0 mismatches.

## Claim 7

Mostly right. The doc says at `:7-12` that this is not silicon or FPGA evidence, and that the evidence run was not whole at the final commit, which I have now done. Listed as unproved or unported, all accurate: `flop4` lockstep only, `PE_FIFO_NO_CG` never run, `timing.py` unported, `ifdef FORMAL` not ported, no Hardcaml-level proof. The port's firmware and test parity scripts exclude `swd_probe` and `test_swd.py`. No first or only language.

## What I ran

All on runner1, in a clean clone of 03edc75. The throwaway copies were removed afterwards.

- `ocaml/scripts/setup_runner.sh`: rc 0. It reused the shared `~/.opam` switch `wl`, with its own sysroot.
- `ocaml/scripts/evidence.sh`: rc 0, all 10 steps.
  - `equiv`: `EQUIVALENT`, 7444/7444, `equivalent`, 1652/1652, 1080/1080, 8 of 8, 392.8 s
  - `test` 25/25, `test_post_freeze` 16/16, lockstep 6/6, flop4 lockstep 6/6
  - the other six cocotb suites as in the doc
  - lockstep3 7/7 at 102,738 cycles on generated Verilog and on `src/`, 7/7 at 227,254 at new seeds, 0 mismatches
  - assembler: 57 firmware files, ok/error/crash 301/352/0, 137/117/8, 941/4052/7, 263/24/0, which is 6202 cases, `DISAGREE=0`, 278,828 bytes
  - Cyclesim 2,000,000 cycles agree; `wl_run` 8 firmware, 3714 lines, 0 disagree; disasm 200,000 words agree
- `ocaml/test/self_check.sh`: rc 1, `missed: 1`. The other 8 were caught.
- Planted bugs, shim bug, mirror widths and module comparison as above. A second model suite run, the stride-1 subset, and host parity with the slice fixed.

Not done: gate level and SDF on the port, which has no netlist. A full-suite stride-1 run, which was too slow to justify.
