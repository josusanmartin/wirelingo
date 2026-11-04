VERDICT: PASS

Commit b665baf on 0135ca2 (usb-guard2, report 049), two commits:
- **1c24c05:** the firmware fix and its test;
- **b665baf:** the test-cost change.

The change touches `firmware/usb_ls_tx.pasm` (one instruction), the regenerated bundle, a new `test_usb_in_setup.py`, one-line changes to the LS re-stage, stage-abort and FS-data harnesses, one mutant, and docs. `usb_ls_rx`, `usb_ls_dev`, `usb_fs_dev`, `tools/pe`, `src/` and `flow/` are unchanged.

Both 047 notes are dealt with. The pending IN is reproduced and fixed, and the faster check loses none of the failures the tests find on the pre-047 code.

| Claim | Result |
|---|---|
| 1. 047 note 1 reproduced: an IN inside a SETUP was answered after its ACK, with and without a packet staged; fixed by `flag clr F_IN` in `setup_flush`; one-core firmware has no pending IN; the test checks both speeds | PASS |
| 2. 55→56 words, docs and test updated, bundle regenerated; turnarounds 2.312 before and after; mutant | PASS |
| 3. 047 note 2: the final descriptor read at 48 clocks per access; 318 tests 93.2 → 58.8 s; on d01db69 old and new tests fail the same 38 | PASS: 89.0 → 57.0 s here; the same 38 |
| 4. RTL 27/27, lockstep 6/6, sigrok 8/8; model 2867/29; 13/13 mutants | PASS |

## Checked

**The asked check: can the F_IN clear drop a legitimate IN?** No, by reading and in the tests.
- **When the clear runs.** `flag clr F_IN` is the first instruction of `setup_flush`, which `idle` enters only while F_SETUP is set.
  - `usb_ls_rx` sets F_SETUP in `got_setup` (the SETUP token) and clears it in `trailer`, when the SETUP's DATA ends, before that record's last `push`.
  - F_ACK is set before the trailer runs, and `idle` tests F_ACK first, so the SETUP's ACK is never held back by the flush.
- **Why a real IN can't be lost.** A legitimate IN can only come after the SETUP's ACK, by which time F_SETUP is long clear: the host must wait at least 2 bit times after the handshake, and the trailer is a few clocks. After a lost or bad DATA, a host retries the SETUP rather than sending an IN.
- **The tests.**
  - With `GAP` set to the legal minimum of 2 bit times (`test_usb_ls_restage.py`) and the new firmware, `test_usb_ls_restage.py` and `test_usb_in_setup.py` give `108 passed`. A real IN 2 bit times after the ACK is answered every time.
  - The two-core enumeration and every other LS test pass in the model suite.
  - The new test's last step sends a real IN after the SETUP and requires its NAK.

**Claim 1.**
- `test_usb_in_setup.py` at b665baf: `4 passed`.
- With 0135ca2's `usb_ls_tx` in the same tree: `2 failed`. These are `test_two_core_in_inside_a_setup_is_never_answered_later[nothing-staged]` and `[staged]`.
- The one-core cases pass on both: the IN gets its NAK at once, and nothing follows the ACK.

**Claim 2.**
- **Word counts** (assembled): `usb_ls_tx` 56, `usb_ls_rx` 143. `guide.md`, `pio-comparison.md` (×3) and `test_pio_comparison.py` say 143 + 56.
- **The bundle.** `pe.mpy_bundle --check`: `firmware bundle matches validated sources`.
- **Turnarounds.** `measure_ls_ta.py` at 0135ca2 and b665baf: ACK after SETUP, NAK after IN and DATA after IN all `2.312-2.312 bit times over 8`.
- **The kv260 README paragraph** says model tests only.

**The asked check: can the faster final read hide a device the race left broken?**
- **What happens before the switch.** In each harness, the race's outcome is decided before `b.service.overhead = 48`, all at the case's own pace:
  - the status stage is classified (right, silent, stale, wrong);
  - the full-speed cases check `staged_over_empty`;
  - the low-speed abort records the TX FIFO level.

  Only the closing 18-byte descriptor read, a liveness check, runs at 1 µs per access.
- **Measured on code with the bugs.** Both 0135ca2's and b665baf's `test_usb_ls_restage.py` and `test_usb_stage_abort.py`, run on trees with d01db69's `usb_ls_tx.pasm` and `tools/pe/usbdev.py` (before the 047 fix), give `38 failed, 194 passed`. The two failure lists are identical (`cmp`). No case the slow read caught is lost.
- **What is given up** is noted below.

**Claim 3's cost.**
- The three USB files (`test_usb_ls_restage`, `test_usb_stage_abort`, `test_usb_fs_data`) under `-n 12` on an otherwise idle runner1: `318 passed`, in 89.0 s at 0135ca2 and 57.0 s at b665baf.
- On the pre-fix trees the two runs took 60.6 s and 42.4 s.

**Mutants.** `test/mutants.py "fw-usb-ls-*" "usbfs-*" "usbdev-*"`: `13 of 13 mutants killed`. `fw-usb-ls-tx-in-kept-in-setup` is killed by `test_two_core_in_inside_a_setup_is_never_answered_later[nothing-staged]`.

**Claim 4.**
- `remote_test.sh model test lockstep` at b665baf: `model rc=0`, `2867 passed, 29 skipped` (`--ignore=test/model/test_swd.py`); `test rc=0`, `TESTS=27 PASS=27`; `lockstep rc=0`, `TESTS=6 PASS=6`.
- RTL `test_sigrok` with `SIGROK_CLI`: `TESTS=8 PASS=8`.

**Timing check against the ISA.** The one new instruction is on the `setup_flush` path only, not on the IN, ACK or NAK paths. The model's turnarounds above are unchanged.

## Notes

1. **What the faster read gives up.** At 2400 clocks per access, the closing descriptor read also ran the software slowly enough for the 100 µs re-stage to fire during it, in some cases. At 48 clocks it rarely does. The re-stage is tested on its own (the every-access tables), and the 38-case comparison shows nothing lost against known bugs. But that incidental coverage of a re-stage during the closing read is gone. This is not a defect.
2. **The one-core firmware answers an IN inside a SETUP with a NAK at once.** The test now pins that the NAK is immediate and that nothing follows the ACK. A host never sends that IN; this is recorded behaviour, not a finding.

## What I ran

**On runner1:**
- in `~/verifier-050/`:
  - `test_usb_in_setup.py` at b665baf and with 0135ca2's `usb_ls_tx`;
  - the three USB files at 0135ca2 and b665baf, timed;
  - the d01db69-firmware/software trees with old and new tests, and their failure lists compared;
  - the GAP 2 copy;
  - `measure_ls_ta.py` at both;
  - `test/mutants.py "fw-usb-ls-*" "usbfs-*" "usbdev-*"`;
  - `pe.mpy_bundle --check`;
  - RTL `test_sigrok`;
- `remote_test.sh model test lockstep` at b665baf (checkout `agent-runs/protoemu-verify-b665baf-2842899`, removed).

**Locally:**
- the diff;
- `usb_ls_tx`/`usb_ls_rx` read for the F_SETUP window;
- the word counts.

My runner1 scratch is removed. No SWD test ran, I did not touch the Kria, and nothing was pushed.
