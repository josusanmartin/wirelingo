# 075 merge-ao-ap

Branch `merge-ao-ap`, tip **`d973462`**: the merge commit **`5ff5e08`**
and one test fix on it (`d973462`, claim 1). The merge has parents `eb63aa8`
(`ao-pkg3`'s tip, 074 PASS) and `a395f5b` (`next-8x4`: AP's flow-8x4, 073
PASS). Made with `git checkout -b merge-ao-ap eb63aa8 && git merge --no-ff
a395f5b`, stopped before committing to regenerate the config. The merge
itself had no conflict. No harden (AP holds the queue). Nothing pushed.

What the merge commit adds beyond the two parents: `src/config.json` only,
regenerated with `python3 flow/gen_config.py` (no options). With AO's
`src/project.v` (`PE_NCORES` 3) the submission preset writes 3 banks at
x = 4.32 / 1302.72 / 653.28 µm (y 3.78, FS; layout `spread`) and 6
`PDN_MACRO_CONNECTIONS` (verifier 073, finding 3). `python3
flow/gen_config.py --preset submission --check`: "configuration matches
submission preset" (it failed on AP's committed 2-bank config, as 073 said).
Every other file is as in one parent or the other.

The builder's claims (runner1 unless noted):

1. **Model suite** (incl. `test/model/test_flow_8x4.py`):
   - at `5ff5e08`: **5 failed**, 3014 passed, 29 skipped. All five are AP's
     `test_flow_8x4` cases that assumed `src/project.v`'s PE_NCORES 2: the
     fo6 byte-for-byte config, the right-hand bank, spread = corners-swap
     (6x4, 8x4), and the submission bank count. The generator's bank count
     follows project.v, which is 3 since AO. gen_config.py is right; the
     tests' premise moved.
   - fixed in `d973462` (test only): those cases run a copy of
     `gen_config.py` beside a `src/project.v` of 2 cores (`generate(...,
     cores=2)`), and the bank-count test runs for this repository's
     project.v and for 2 and 3 cores. At `d973462`: **3021 passed, 29
     skipped**.
2. **test.py at 3 cores** (the default, SRAM model) at `5ff5e08`:
   **TESTS=30 PASS=30** (`d973462` changes only a model test).
3. **Lockstep** at `5ff5e08`, 3/3/8 and 2/2/8 (40 seeds × 1000 cycles each,
   plus the directed tests): **rc 0 both** (7 of 7 each).
4. **Privacy**: `record_check.py --repo . --tree HEAD --commits d2ed344..HEAD`
   at `d973462`: "TOTAL flagged: 0" (local).
5. **Ancestry**: `git merge-base --is-ancestor b2fa7f3 HEAD` fails (rc 1).

Caveats:
- This is the first tree with both the 3-core RTL and the 3-bank flow, so it
  is the first one a harden of the package can run from. None has run.
- The config's bank order (bank 2 in the middle) is gen_config's `spread`,
  as the study's `pkg3` harden had it.

Asked checks:
- `git diff eb63aa8 5ff5e08` (AP's 6 files: gen_config.py, harden.sh,
  info.yaml, config.json, harden8x4.sh, test_flow_8x4.py) and `git diff
  a395f5b 5ff5e08 -- flow info.yaml src/config.json` (config.json only):
  the other side's changes, plus `src/config.json` against the generator.
- `d973462`'s test change: does it keep the 2-core claims AP's tests made
  (fo6 byte for byte) while testing the real 3-core default?
- `python3 flow/gen_config.py --preset submission --check` at `5ff5e08`.
- The model suite at `d973462`; test.py at 3 cores and lockstep 3/2 at the merge.
- `record_check.py --repo . --commits d2ed344..HEAD` and the ancestry check.
