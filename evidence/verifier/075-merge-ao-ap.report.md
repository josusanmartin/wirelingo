VERDICT: PASS

075 merge-ao-ap (branch `merge-ao-ap`, merge `5ff5e08` of `eb63aa8` and `a395f5b`, plus `d973462`; backlog AO/AP integration): verifier report.
Kria: verifier does not touch it. No SWD.

Every claim reproduces:
- the merge is clean, and `src/config.json` is its only content of its own;
- the regenerated config is the generator's 3-bank submission output and equals the study's `grt3` run config in every setting;
- `d973462` keeps AP's 2-core claims, including fo6 byte for byte, and also tests the real 3-core default. Two generator mutants show that both sides are tested;
- the model suite, the top-level tests at 3 cores and lockstep at 3 and 2 cores all pass.

My notes are minor.

## Findings

1. **Minor: two docstrings in `test/model/test_flow_8x4.py` now say more than holds.**
   - `:3-4` says "A 6x4 info.yaml must still give the signed-off run fo6's configuration byte for byte; 8x4 moves only the right-hand bank." Since AO, both hold only with a 2-core `project.v`, which is how the tests now run them.
   - `:35` says "(3 on next-8x4)", but `next-8x4` is still `a395f5b`, with 2 cores, until this branch lands.
   - Fix, when next touched: "with a 2-core project.v" and "3 since AO".
2. **For the orchestrator: this merge has `next-8x4` at `a395f5b` only.**
   - `ap-flow8x4`'s later commits are not in it: `285dca8`, `e491847`, `72e2ccb` (073's docs), `a40c3cf` and `3cb9f46`.
   - `git merge-tree --write-tree d973462 ap-flow8x4` is clean (rc 0), so they can follow without a conflict.
3. **Process (mine):** at the start I ran `git fetch -q origin` in my local checkout by habit. It printed nothing and left `FETCH_HEAD` at its earlier 13:42 UTC time, so it brought nothing in. I made no other outside contact.

## Asked checks

- **The merge is clean.**
  - Merge base `d2ed344`. `5ff5e08`'s parents are `eb63aa8` and `a395f5b`.
  - AO's 44 changed files and AP's 6 (`flow/gen_config.py`, `flow/harden.sh`, `info.yaml`, `src/config.json`, `syn/8x4-study/harden8x4.sh`, `test/model/test_flow_8x4.py`) do not overlap.
  - My own `git merge-tree --write-tree eb63aa8 a395f5b` gives rc 0 with no conflict. Its tree differs from `5ff5e08`'s in `src/config.json` only.
  - `git diff eb63aa8 5ff5e08` has exactly AP's 6 files. `git diff a395f5b 5ff5e08 -- flow info.yaml src/config.json` has `src/config.json` only (+12/−3).
  - AO changed `src/pe_chip.sv`, `pe_core.sv` and `project.v`, and added no source file, so `info.yaml`'s 9 `source_files` are still complete. ✓
- **`src/config.json`** (`5ff5e08` = `d973462`):
  - The diff against AP's config is:
    - `"banks": 3`;
    - `"layout": "spread"`;
    - a third instance `u_chip.u_imem.g_bank[2].g_on.u_bank.sram` at (653.28, 3.78), FS;
    - two more `PDN_MACRO_CONNECTIONS` for bank 2 (6 in all).
  - The instance path matches the RTL (`pe_imem.sv:23-26`: `g_bank` / `g_on` / `u_bank`; `gen_config.py:57`, `:61`).
  - `project.v:25` is `` `define PE_NCORES 3 `` and `:28` is `PE_NBANKS = PE_NCORES`.
  - Rerun on a `git archive` of `5ff5e08`:
    - `python3 flow/gen_config.py --preset submission --check` gives "configuration matches submission preset", rc 0;
    - `python3 flow/gen_config.py` (no options) rewrites a byte-identical file (`cmp`). ✓
  - Against the study's 3-bank runs on runner1 (read-only, key by key):
    - **`grt3`:** equal except the `//` comment (the study's generator wrote "6x4") and `//generator`'s preset label (`experiment`);
    - **`pkg3` and `grt3pkg`:** the same, plus `RUN_LINTER` (0 there, 1 here) and their study-only `VERILOG_DEFINES`.

    So the placement, PDN and every physical setting are the ones the study hardened with 3 banks.
- **Lint (extra check).** The study's `pkg3` ran with the linter off, so no LibreLane lint had seen AO's RTL. The submission preset runs one (`RUN_LINTER` 1).
  - I reran fo6's exact Verilator command from its `01-verilator-lint/COMMANDS` in the same LibreLane image (Verilator 5.046), with fo6's blackboxes and `_deps.vlt` waivers (copied, read-only), on two trees.
  - **`d2ed344`, the control:** 0 errors and 24 warnings, fo6's own count (`design__lint_warning__count` 24, errors 0).
  - **`d973462`:** 0 errors and 24 warnings (22 UNUSEDSIGNAL, 1 SYNCASYNCNET, 1 WIDTHEXPAND). The only differences are four unused-bit ranges that shrink with the third core: `bank_rdata`, `fclear`, `restart` and `fetch_addr`.
  - So no lint error stands in the way of the first harden.
- **`d973462` keeps AP's intent.**
  - Only `test/model/test_flow_8x4.py` changes.
  - `generate(..., cores=2)` copies `gen_config.py` unchanged into a scratch root, with `info.yaml` and a `project.v` whose single `PE_NCORES` line is set to 2 (asserted to be exactly one line). The generator reads `project.v` and `info.yaml` relative to its own file (`gen_config.py:17`, `:49`, `:124`), so the copy runs the same code on 2 cores.
  - The four 2-core cases are kept as they were, assertions unchanged. They are fo6 byte for byte (same `FO6_CONFIG` sha256), the right-hand bank, and spread = corners-swap on 6x4 and 8x4.
  - The bank-count test now runs for this repository's `project.v`, 2 and 3. Each is refused with the other count and gives n banks by default, so it keeps AP's refusal check in both directions.
  - `test_committed_config_is_the_8x4_submission` runs against the real 3-core `project.v`. With `test_three_and_four_banks_spread_along_the_bottom` (the study's `grt3`/`pkg3` positions), it tests the real default.
  - Only side effect: a copied root has no `tt/`, so `core_corner`'s `tile_sizes.yaml` cross-check is skipped there. It returns the same literal corner, and `test_tile_size_is_checked_against_tt_support_tools` still covers the check.
  - **Mutants** (`genmut.sh`, runner1, `test_flow_8x4.py` at `d973462`, `project_cores()` pinned):
    - unmutated: "19 passed";
    - pinned to 2: 3 failed (`test_committed_config_is_the_8x4_submission`, `test_submission_bank_count_follows_project_cores[None]` and `[3]`);
    - pinned to 3: 5 failed (the four 2-core cases and `[2]`).

    So the 2-core cases depend on the copy's `project.v`, and the 3-core default is really tested.
- **The 5 failures at the merge.** `test/model/test_flow_8x4.py` at `5ff5e08` (runner1): "5 failed, 12 passed". They are the five the builder names:
  - `test_6x4_info_gives_fo6_config_byte_for_byte`;
  - `test_8x4_moves_only_the_right_bank`;
  - `test_spread_with_two_banks_is_corners_swap[6x4]` and `[8x4]`;
  - `test_submission_bank_count_follows_project_cores`, where the generator wrote "3 banks at {0: (4.32, 3.78), 1: (1302.72, 3.78), 2: (653.28, 3.78)}".
- **Model suite at `d973462`** (my runner1 clone; `remote_test.sh`'s pytest flags but `-n 8`, with `--ignore=test/model/test_swd.py`): rc 0, "3021 passed, 29 skipped in 1103.68s (0:18:23)", as claimed. That is the merge's 3014 + 5, plus 2 new parametrized cases. SIGROK checks skipped: 28, and a pass says nothing about them.
- **`test.py` at 3 cores** (the default `project.v`, SRAM model) at `5ff5e08`: rc 0, "TESTS=30 PASS=30 FAIL=0 SKIP=0" (1298 s). `d973462` changes only a model test, so this holds at the tip.
- **Lockstep at `5ff5e08`** (`make TB=chip`, `LOCKSTEP_SEEDS=40 LOCKSTEP_CYCLES=1000`, `PE_FIFO_LOG2=3`):
  - 3/3: rc 0, "TESTS=7 PASS=7";
  - 2/2: rc 0, "TESTS=7 PASS=7".
- **Privacy.**
  - `record_check.py --repo . --tree HEAD --commits d2ed344..HEAD` at `d973462`: "tree HEAD: 728 files checked", "commit messages checked: 20", "TOTAL flagged: 0".
  - All 20 commits are by the noreply identity and carry the trailer.
- **Ancestry.** `git merge-base --is-ancestor b2fa7f3 HEAD` exits 1. ✓
- **No harden.** AP's density-sweep harden was running on runner1 throughout. I started none.
- **ISA timing check.** Not applicable: no RTL, model or firmware changed beyond the two parents, both already verified (073, 074).

## What I ran

- **Locally:**
  - reading the diffs;
  - `git merge-tree` twice;
  - the `--check` run and regeneration on a `git archive` of `5ff5e08`;
  - the key-by-key config comparison;
  - `record_check.py` and the ancestry check.
- **runner1** (`~/protoemu-flow/verifier-075/`, my clone of `merge-ao-ap` at `d973462`, one worktree per job; no harden):
  - the model suite at `d973462`;
  - `test_flow_8x4.py` at `5ff5e08`;
  - `test.py` at 3 cores and lockstep 3/3 and 2/2 at `5ff5e08`;
  - `genmut.sh`;
  - `lint.sh` (fo6's lint command in the LibreLane image);
  - read-only reads of the study configs and fo6's lint step.

Summary: the merge is clean and the 3-bank config is the generator's and the study's. `d973462` keeps AP's 2-core claims and also tests the 3-core default. The suite, the top-level tests and lockstep pass, and the RTL lints clean under fo6's lint. This tree is ready for the package's first harden when AP's queue frees.
