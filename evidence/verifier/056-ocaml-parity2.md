# 056 ocaml-parity2

Branch `ocaml-parity2`, commits `89675b6` (fixes to `ocaml/`) and `6219a50` (`docs/hardcaml-port.md` numbers, plus `evidence/ocaml-parity-20261104/` with its row in `evidence/README.md`), on `9dd256e`. Backlog item AD: rerun the OCaml port's `ocaml/scripts/evidence.sh`. Its last run was at 2dddff7, and the firmware has changed since.

The builder says the unchanged port passed every gate at 9dd256e, with no assembler or model difference, but that three things had lost coverage. It restored them in `ocaml/` only. No Python, firmware, `src/` or `flow/` file changed.

Claims to check:

1. **The full run at 89675b6** (`evidence/ocaml-parity-20261104/summary.txt` and `rc.txt`): all 13 steps rc 0.
   - **Formal:** 7444/7444 proven, ABC `&cec` equivalent. Generated Verilog sha `3b9a5735…f69646`, unchanged. Mutants 8/8.
2. **cocotb on the generated Verilog:**
   - test 27/27, post_freeze 16/16, lockstep 6/6, lockstep4 6/6;
   - lockstep3 7/7 three times (including seeds 5000+);
   - new: `test_bench_fw` 5/5 and `test_pps` 4/4, each with one test skipped by design.
3. **Fix 1-2: CI's two new test-job steps.** A new `cocotb_e` step (`evidence.sh:38`) runs them. Per-suite lists of tests that must pass (`run_cocotb.sh:74-83`) are copied from CI's `test.yaml`. Check that the lists match test.yaml exactly, and that the doc's claim "every cocotb step of CI's test job" is now true.
4. **Fix 3-5, the state load.** This is the main one: decide whether the resync is sound.
   - `Chip.load_state` (`ocaml/sim/chip.ml:1239-1374`) is the exact inverse of `state_line`, and raises on a line that is too short or too long. `wl_simcheck` has a new `L` command that round-trips it.
   - At stride 1, `ocaml/test/lock_chip.py` now loads the state a test wrote into the chip and keeps comparing; before, it dropped the chip at that write. 108 tests write into chip state: pops of `core.rxf`, or `chip.cycle` and register fast-forwards.
   - The builder argues it is sound because the two models agreed on every cycle up to the write.
   - Check:
     - whether loading Python's state into OCaml can ever hide a real OCaml divergence, for example if the load happens at a cycle where the states already differ, or if the load copies state that OCaml should have computed itself;
     - whether every write is detected, or a test can write into the chip unnoticed;
     - whether `WL_LOCK_RESYNC=0` restores the old rule exactly.
5. **Fix 6, self-check.** Two planted bugs only the load reaches: a FIFO read from the wrong end, and `load_state` swapping txf and rxf. Each is caught with the load and missed without it. In all, 17 planted bugs are caught and 0 missed.
6. **Fix 7, coverage floors.** They are raised to the new counts:
   - `host_parity.py:30`: files 57→67;
   - `asm_parity.py:458`;
   - `stage3_model_suite.sh:19,50-52`: 19000 chips / 150M cycles.

   Check that each floor is below the measured value with a sensible margin. Given the ~0.03% run-to-run variation the builder reports, check that the cycle floor won't flake.
7. **Fix 8.** `evidence.sh:56` keeps the model suite's first-pass totals in the summary.
8. **Numbers, old (2dddff7) → new.**
   - Assembler: 57→67 files, 6371 cases, 0 disagree.
   - Model, first pass: 1394 → 2872 passed; 1262 → 19,602 chips; ~63.7M → 155,099,397 cycles.
   - Stride 1: 342 written-into chips, 9050 loads, 7,135,772 cycles, 0 mismatches.
   - Host parity: 67 files, 0 bad.
   - Unchanged: run_parity (8 runs, 3714 lines) and cyclesim (2M cycles).
9. **Docs.**
   - `docs/hardcaml-port.md` is rebased to 89675b6, base 9dd256e. Its evidence-level paragraph is unchanged: simulation and formal only, not silicon or FPGA.
   - The evidence folder is indexed: `test_evidence_index.py` passes.

Asked checks:
- Read the `lock_chip.py` resync and judge its soundness (claim 4). This decides the item.
- Plant your own divergence in a throwaway copy of `ocaml/sim/chip.ml`. Make it one that only shows after a test's write, for example in rxf handling, and run the stride-1 model gate on runner1. Confirm it is caught with the load, and missed with `WL_LOCK_RESYNC=0`.
- Compare the doc's numbers against `summary.txt`.
- Check that the copied `summary.txt` and `rc.txt` match runner1's `~/protoemu-flow/ocaml-port/ev-89675b6/`, which the builder kept. You may rerun single steps there. You need not rerun the whole `evidence.sh`, which takes about 32 min, unless you find a reason to.

The usual rules:
- SWD stays parked: `swd_probe` and `test_swd.py` are excluded, and every model run uses `--ignore=test/model/test_swd.py`.
- No Kria.
