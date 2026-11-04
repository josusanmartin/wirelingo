VERDICT: PASS

Commit 05f86ec on base 97e442d. It follows f88fb31 (report 036) with two commits:
- **9f6f9ea:** `test/model/test_usb_fs_data.py`, `fpga/kv260/README.md` and the new `test/model/usb_fs_enum_sweep.py`;
- **05f86ec:** `evidence/model-usb-fs-enum-sweep-20261104/` and 9 README lines.

No software under test changed. Against 64b59c2, `tools/pe/usbdev.py` and `firmware/usb_fs_dev.pasm` are byte-identical, and `tools/pe/usbfsdata.py` differs only in fd650ee's docstring. Nothing in `src/` or `flow/` changed.

Both of 036's blocking findings are dealt with. The window is now stated as wide as it is, with exact counts. My hold at every SPI access of the re-stage finds unanswered INs at those three accesses only. The sweep figures now have a script and outputs, and my rerun at two commits reproduces them line for line.

| Claim | Result |
|---|---|
| 1. 6 hold points × 2 resumes; counts 0/0, 0/0, 0/2, 8/0, 1/0, 0/0; no stale DATA; allowances exact, each fails at one less | PASS: `12 passed`; each allowance minus one fails exactly its own case; every access checked, not just the six (below) |
| 2. README: window from the check's flags read to the FSET; 2/8/1; xHCI CErr labelled from the spec, untested; "never stale DATA", "narrowed, not closed" | PASS: matches what the model does at every access |
| 3. Sweep committed; prints the software's sha256; reruns 160/160 and 6/40 at all three commits, the same six runs; outputs and sha256s in evidence | PASS: rerun at 64b59c2 and c184dd6 identical to the committed outputs apart from the wall-time line; all hashes match |
| 4. Model suite 2318 passed, 28 skipped; mutants 10/10 | PASS: same |

## Checked

**Claim 1, and the asked check that every allowance fails at one less.**
- `test_no_restage_after_a_setup` at 05f86ec: `12 passed`, twice.
- **One copy per allowance, decremented** (2→1, 8→7, 1→0). Each gives `1 failed, 11 passed`, and the failure is the decremented case itself:
  - `[inside the check before FSET-after its token]`;
  - `[at FSET-after the SETUP]`;
  - `[after FSET-after the SETUP]`.
- **Zero everywhere else.** The other nine cases are held to 0 by `UNANSWERED.get(..., 0)` and `len(silent) <= unanswered`. My run below shows they are 0.

**Beyond the six named points: every SPI access** (`test_v040_every_access`).
- **How.** The same scenario as the builder's test (an 8-byte read whose ACK is lost, OUT status, then SET_CONFIGURATION). The software is held at the n-th SPI access from the re-stage decision on, for n = 0-25, and resumed after the whole SETUP or after its token: 52 cases.
- **What is checked at each.** The status-stage log, the device state (`staged_over_empty`), and a write and read round trip after.
- **Result: `52 passed`.**
  - Unanswered INs occur at exactly three accesses, with the README's counts:

    | Access | Resume | Unanswered INs |
    |---|---|---|
    | 8, the pre-FSET check's RX-level read | after the token | 2 |
    | 9, the FSET write | after the whole SETUP | 8 |
    | 10, the flags read just after it | after the whole SETUP | 1 |

  - The other 49 cases give 0. Those include the decision's RX-level read (access 1), each of the four TX pushes (3-6), the pre-check's flags read (7), the post-FSET check (11-12), and the service loops after it.
  - Stale DATA was never sent, the status stage always ended in one zero-length DATA1, and every round trip after was right.
- So "the window runs from the check's flags read to the FSET" is accurate, and no seventh point opens it.

**Claim 3, the sweep: the asked rerun.**
- **What I ran.** On runner1, the script from 05f86ec, with the README's command (`--runs 40 --procs 8`, default combos), against `git archive` copies of 64b59c2 and c184dd6.
- **Result.** Both outputs are identical to the committed `out-64b59c2.txt` and `out-c184dd6.txt`, apart from the last line (the wall time: 1584 s and 1580 s, against the committed 896 s). That covers the three sha256 lines, 0 of 40 bad and re-staged in 0 for the four 20-100 µs combinations, and `6 of 40 bad` at 9600 clocks (200 µs) for runs 4, 15, 22, 28, 31 and 37, each `SETUP not acknowledged` after only `8006000100004000`.
- **The committed files.** sha256 of the three outputs: `0449b19c…`, `fa826302…`, `fa0fb999…`, as in the evidence README. The result lines of `out-c184dd6.txt` and `out-8c71177.txt` are equal too.
- **The hashes inside the outputs** match each commit's own `usbdev.py` (`023ae80e…`, `05ee7c23…`, `78353275…`); `usbfsdata.py` and the firmware are the same at all three.
- **The README** now says six runs, the same at all three commits, and cites the script and the evidence folder.

**Claim 4.**
- `remote_test.sh model` at 05f86ec: `2318 passed, 28 skipped`.
- `test/mutants.py "usbfs-*" "usbdev-*"` in my clone at 05f86ec: `10 of 10 mutants killed`. `usbdev-fset-unchecked-after` is killed by the new `[inside the check before FSET-after its token]` case.

**Timing check against the ISA.**
- No firmware or RTL changed.
- The counts are the model's own: FIFO levels as of a cycle's start, and a restart that keeps the FIFOs (`docs/isa.md`, as in 036).
- The README gives them as model counts, with the CErr consequence marked as taken from the xHCI spec and not tested.

**Over-claiming.**
- "Narrowed, not closed", "never stale DATA" and "evidence level: model" are all accurate.
- The board claim ("the board's stalled runs re-staged nothing") is unchanged from 036.

## Notes

1. **The sweep never reaches the re-stage.** "Re-staged in 0" holds for all 200 runs at each commit, so it cannot tell the three commits' re-stage code apart. That is consistent with the README, which uses it only to show that SET_CONFIGURATION is answered and that the 200 µs failures sit at SET_ADDRESS. The re-stage evidence is `test_no_restage_after_a_setup`, not the sweep.
2. **The bridge comes from the tree.** With `--tree`, the script loads `SpiBridge` from that tree's `test/model/test_usb_fs_data.py`. So each commit ran with its own test harness, not only its own software. The output's hashes cover the software, not the harness. It made no difference here, since c184dd6 and 64b59c2 give the same result lines, but a hash of the test file in the output would make the run fully identified.
3. **My every-access test could join the suite.** It is a stronger form of the six-point table, and it runs in about 40 s under `-n 12` (52 cases). It would catch a future change that opens a seventh point.

## What I ran

**On runner1:**
- `remote_test.sh model` at 05f86ec: `model rc=0`, `2318 passed, 28 skipped in 1009.57s (0:16:49)` (it shared the runner with the sweep). Checkout `agent-runs/protoemu-verify-05f86ec-1760830`, removed.
- In `~/verifier-040/`:
  - the sweep at 64b59c2 and c184dd6 (above);
  - `test_v040_every_access` under `-n 12`: `52 passed in 40.61s`;
  - the three decremented copies: `1 failed, 11 passed` each;
  - `test_no_restage_after_a_setup` twice: `12 passed`;
  - `test/mutants.py "usbfs-*" "usbdev-*"`: `10 of 10 mutants killed`.

**Locally:**
- the diff;
- `usbdev.py`'s `stage()` and `service()`, read;
- the software hashes at the three commits;
- the sha256 of the committed outputs.

My runner1 scratch and the `remote_test.sh` checkout are removed. I did not touch the Kria or SWD, and nothing was pushed.
