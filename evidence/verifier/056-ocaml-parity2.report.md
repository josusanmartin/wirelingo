VERDICT: PASS

Commits 89675b6 and 6219a50 on 9dd256e (improve-2026-11 after the reattach-wiring merge).
- **89675b6** changes `ocaml/` only:
  - `bin/wl_simcheck.ml`;
  - `scripts/evidence.sh`, `run_cocotb.sh` and `stage3_model_suite.sh`;
  - `sim/chip.ml`;
  - `test/asm_parity.py`, `host_parity.py`, `lock_chip.py` and `self_check.sh`.
- **6219a50** changes `docs/hardcaml-port.md` and `evidence/README.md`, and adds `evidence/ocaml-parity-20261104/`. `ocaml/` is identical at the two commits.

No Python under `tools/` or `test/`, no firmware, and no `src/` or `flow/` change.

**Claim 4, the state load, is sound.** The load cannot hide an OCaml divergence that the old rule would have caught. Each load is processed only after the previous step's full-state compare passed, so it changes only what the test wrote. Every write in the 108 tests is detected. `WL_LOCK_RESYNC=0` gives the old wrapper's totals exactly. My own planted divergence fails the full stage-3 gate with the load and passes it without. Every other claim checks too.

| Claim | Result |
|---|---|
| 1. 13 steps rc 0; 7444/7444, ABC equivalent, sha `3b9a5735…f69646`, mutants 8/8 | PASS (`summary.txt` and `rc.txt` byte-identical to runner1's `ev-89675b6/`) |
| 2. cocotb counts, `test_bench_fw` 5/5, `test_pps` 4/4 | PASS |
| 3. `cocotb_e` (`evidence.sh:38`); the lists (`run_cocotb.sh:74-83`) equal to `test.yaml`'s; "every cocotb step of CI's test job" | PASS, the lists match word for word; all 12 CI cocotb steps are covered |
| 4. `load_state` (`chip.ml:1240-1374`), the `L` command, the resync at stride 1 | PASS, sound (below; notes 1-2) |
| 5. Self-check: 2 resync cases, 17 caught, 0 missed | PASS (`self.log`: 17 `caught=yes`, both resync cases "with the load: caught; without: missed", `missed: 0`) |
| 6. Floors (`host_parity.py:30`, `asm_parity.py:458`, `stage3_model_suite.sh:19,50-52`) | PASS, below the measured values; the cycle floor won't flake |
| 7. `evidence.sh:56` keeps the first-pass totals | PASS |
| 8. The numbers | PASS, each checked against the run logs |
| 9. Doc rebased; evidence-level paragraph unchanged; index test | PASS |

## Claim 4: the state load

**Why it can't hide a divergence, by reading.**
- **Order of the lines.** At stride 1, every step sends `S` (stimulus, with the bus read and pins before the edge) and then `=` (Python's full state after the edge). `wl_simcheck` handles lines in order and stops at the first difference.
- **When a write is detected.** A write is detected at the next step's start, as `state_line(chip) != link.last`, where `last` is the state just sent. The `L` line follows that step's `=`.
- **What the load changes.** By the time OCaml loads, it has been shown equal to `last` in every field the line holds. So loading the whole line changes OCaml only where the test wrote.
- **A divergence earlier than the load** has already failed the `=` before it.
- **After an earlier failure,** the `L` is never processed, and `finish()` records the mismatch: `wl_simcheck`'s output then doesn't start with `OK`, and a `BrokenPipeError` in `resync` goes to `finish(stats=False)`.
- **The load's own check.** `L` also checks that the loaded state prints back as the same line, so a `load_state` that sets a field wrongly fails at once. Memory and the coverage counters are not loaded, and coverage is still compared at the chip's end.

**What is loaded, measured** (`v056_instrument.py`: a throwaway `lock_chip.py` that logs each load's fields, on the 108 tests at stride 1 on runner1).

| Loads | Test file | Fields that changed |
|---|---|---|
| 5501 | `test_ata_pio.py` | `core0.rxf` |
| 2118 | `test_faults.py` | `core0.rxf` |
| 914 | `test_pps.py` | `core0.r`, `cycle` |
| 357 | `test_reverse_engineering.py` | `core0.rxf` |
| 158 | `test_pps.py` | `core0.r`, `core0.t`, `cycle` |
| 1 | `test_rmii.py` | `core0.rxf` (before the first step) |
| 1 | (the `lr` test) | `core0.lr` (before the first step) |

- That is 9050 in all, the run's count, and exactly the kinds the doc names.
- **Does the load copy state OCaml should have computed?** Only at test_pps's fast-forward, which sets `r3`, `t` and `chip.cycle` (up to 5,000,001,583) to skip whole WAIT rollovers. Python skips those cycles too. So neither model computes them, and the load takes the test's assumption, not a value Python worked out. The doc's "what it does not check is the written values themselves, which the test chose" covers this.

**Is every write detected?**
- **Method.** The same instrumentation fingerprinted every Python chip, core and line attribute that the state line doesn't print (`imem` among them), after each step and before the next.
- **Result.** In 7,135,772 steps there were 209 changes between steps. All were method monkeypatches, not data: test_faults' `Wire.step` set on the chip (169) and test_ata_pio's Tracer wrapping `core0.step` and `core0._divider` (40).
- **Elsewhere.** `git grep` finds no test or tool writing `imem` outside `sim.py`'s bus write (`sim.py:680`).
- **A reserved RXF entry removed by a test** (more True reservations than RXF entries) was never seen, in any chip.
- **The rest of the suite.** Tests outside the 108 aren't instrumented at stride 1. But a persistent write there would have shown as a stride-64 mismatch, at the latest in the chip's final compare, and sent the test to the 108.

**`WL_LOCK_RESYNC=0` restores the old rule.**
- **By reading.** The `not RESYNC` branches in `step()` and `finish()` are 9dd256e's code, moved inside the `try`.
- **Measured.** The stride-1 pass on the 108 tests, run twice with 9dd256e's wrapper and twice at 6219a50 with `WL_LOCK_RESYNC=0`, gave the same totals all four times: `chips 357, cycles 3,833,185, state_compares 3,833,194, poked_chips 342, coverage_events 308,440, mismatches 0`.
- 9dd256e's kept evidence run has 3,833,195 and 308,508. That is run-to-run variation (note 3).

**My planted divergence.**
- **The plant.** In a throwaway copy, `chip.ml`'s RX push (`| Some v -> rx := !rx @ [ v ]`) flips the pushed word's low bit when `chip.cycle >= 2^30`.
- **Why it shows only after a write.** No chip reaches 2^30 cycles by stepping: the whole suite is 155M. test_pps's fast-forward takes 20 loads past 2^28, the largest to 5,000,001,583.
- **The full `stage3_model_suite.sh … 64` on runner1, both ways:**

| `WL_LOCK_RESYNC` | First pass | Stride 1 | Gate |
|---|---|---|---|
| 1 | 2872 passed, 19,602 chips, 342 mismatches → 108 tests | 9050 loads, **1 mismatch**: `test_pps.py::test_interval_longer_than_the_32_bit_counter`, `MISMATCH … state` on the RXF word (OCaml 262143, Python 262142) at chip cycle 5,000,001,055 | **rc 1, caught** |
| 0 | the same | 0 loads, 3,833,185 cycles, 0 mismatches | **rc 0, missed** |

- The unmutated port at 6219a50, stride 1 on the 108 tests: 7,135,772 cycles, 7,136,021 state compares, 9050 loads, 0 mismatches. That is exactly the run's figures.

## Checked

**Claims 1, 2 and 8: the run and the doc's numbers.**
- `summary.txt` sha256 `646bba95…cb66f67e` and `rc.txt` `e788d1fa…6f2412` are identical in the commit and in runner1's `ev-89675b6/`.
- Every count the doc cites matches the summary:
  - 27/27, 16/16, 5/5 (SKIP=1), 4/4 (SKIP=1) and 6/6;
  - 7444 points and 418.3 s;
  - 67 files, and 6371 cases (743 firmware, 262 directed, 5000 fuzz, 366 corpus), 0 DISAGREE;
  - 367,348 bytes;
  - 2872 passed and 29 skipped; 19,602 chips and 155,099,397 cycles; 2,500,279 compares; 5,258,220 cycles ignored;
  - at stride 1, 357 chips, 342 poked, 9050 loads, 7,135,772 cycles and 7,136,021 compares;
  - host parity 796/373/167/167, 0 bad;
  - 8 runs and 3714 lines; 2,000,000 cyclesim cycles; 1899 s.
- **Checked in runner1's logs instead:**
  - the three-way 102,738 and 227,254 cycles, in `wc/`'s and `wd/`'s lockstep3 totals (also 102,738 for lockstep3src);
  - 155,135,208 cycles and 2450 s for the 9dd256e run;
  - the old rule's 3,833,185;
  - the split of the 108 tests (43 pps, 40 ata_pio, 21 faults, 2 reverse_engineering, 1 call, 1 rmii);
  - 28 of the 29 skips sigrok, from the suite's own line.

**Claim 3.**
- `run_cocotb.sh:74-83`'s names are CI's exactly (`test.yaml:59-60` and `:65`), with the same count of 5, since `grep -c '^@cocotb.test()'` doesn't count the `skip=` tests.
- `check_results.py` with names checks only those, so the skipped test can't hide a failure among the named ones.
- CI's test job has 12 cocotb steps: top, pio, eth_service, post_freeze, bench_fw, pps, the three serialized-host suites, usb_uart_rtl, and the two lockstep builds. `evidence.sh`'s `cocotb_a`, `_b` and `_e` run all 12.

**Claim 6.**

| Floor | Value | Margin |
|---|---|---|
| `files` 67 | 67 | none; a count of files, so it fails exactly when a file is lost |
| `export` 780 | 796 | 2% |
| `export_err` 360 | 373 | 3.5% |
| `writes` 160 | 167 | 4% |
| `listing` 160 | 167 | 4% |
| `firmware ok` 340 | 350 | 3% |
| `corpus ok` 330 | 342 | 3.5% |
| `corpus error` 20 | 24 | |
| chips 19000 | 19,602 | 3.1% |
| cycles 150M | 155.1M | 3.3% |

- **The assembler and host counts are deterministic.** They are identical in the 9dd256e and 89675b6 runs.
- **The cycle count does vary.** Four first passes gave 155,082,270 to 155,135,208: the two kept runs and my two mutant runs, whose first pass the plant cannot affect. That spread of 0.034% is about 100 times smaller than the 5M margin, so the floor won't flake.

**Claims 5, 7 and 9.**
- `self.log`: 17 `caught=yes`, `missed: 0`, and both resync cases say "with the load: caught; without: missed".
- `evidence.sh:56`'s `grep` is the one that put the first-pass totals in `summary.txt`.
- The evidence-level paragraph (`docs/hardcaml-port.md:7-9`) is not in the diff.
- `python3 -I test/model/test_evidence_index.py` gives `evidence/README.md: one row per folder`.
- **The privacy search of the new folder found nothing private.** The one name it holds is the project's top-module name from `info.yaml`, which is public. There are no addresses or paths.

**Timing check against the ISA:** not applicable. The change is in the port's harness and the model's state load, and it changes no cycle behaviour: the generated Verilog's sha is unchanged and equivalent.

## Notes

1. **A load before a chip's first step overwrites OCaml's reset state, which was never compared.**
   - This happened 2 times in 9050: test_rmii's `core.rxf = [11, 22, 33]` and the `lr` test.
   - On those 2 chips, the doc's "up to the write the two models were equal every cycle" is vacuous.
   - Nothing is lost. Under the old rule those chips weren't compared at all, and the reset state is compared at the first step of every other chip.
2. **The final compare after an end-of-life load is vacuous.** `finish()` loads, then sends `=` with the same line. That adds 1 to `state_compares` for each such chip. The coverage compare that follows it is real.
3. **Stride-1 `coverage_events` and `state_compares` vary between runs.** For example, 308,508 in the kept 9dd256e run against 308,440 in my four; 4,117,993 normally against 3,962,417 under my slower instrumented run.
   - A chip garbage-collected before `finish_all()` gets no final compare or coverage, so these totals depend on GC timing. This is older than this change.
   - Cycles and loads were the same in every run.
4. **There is no floor on the stride-1 pass.** If the resync silently went back to the old rule, `stage3_model_suite.sh` would still pass. `self_check.sh`'s two resync cases do catch that, since each requires "caught with the load", and `evidence.sh` runs them.
5. **One wording slip in the doc.** Table row 3's "7.1 million more on the chips its tests write into" counts all 357 stride-1 chips, 15 of them not written into. The Stage 3 row ("357 chips, 342 written into") is precise.

## What I ran

**Locally** (worktree detached at 6219a50):
- the diff;
- `test.yaml` against `run_cocotb.sh`;
- the doc's numbers against `summary.txt`;
- the index test;
- the privacy search of the new folder;
- `git grep` for test-side writes into chips.

**On runner1:**
- **in `~/verifier-056/`** (git clones of 6219a50 and 9dd256e, built with the documented opam switch):
  - the stride-1 pass on the 108 tests, with resync on, off ×3, and 9dd256e's wrapper ×2;
  - `v056_instrument.py`'s instrumented pass, on all 108 tests and on the 43 PPS tests;
  - the plant on the PPS tests both ways;
  - `stage3_model_suite.sh … 64` on the plant, with `WL_LOCK_RESYNC=1` (rc 1) and `=0` (rc 0).
- **read-only:** `~/protoemu-flow/ocaml-port/ev-89675b6/` and `ev-9dd256e/`, for the hashes, the logs, `lock.failed` and the lockstep3 totals.
- **`remote_test.sh model` at 6219a50** (`--ignore=test/model/test_swd.py`): `model rc=0`, `2872 passed, 29 skipped in 437.84s (0:07:17)` (checkout `agent-runs/protoemu-verify-6219a50-2027875`, removed).
- I did not rerun `evidence.sh` as a whole.

My runner1 scratch is removed, and the builder's `ev-*` folders are untouched. No SWD test ran, I did not touch the Kria, and nothing was pushed.
