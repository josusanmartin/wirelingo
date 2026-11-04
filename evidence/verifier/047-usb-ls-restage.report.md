VERDICT: PASS

Commit 6e1da04 on edc9042 (usb-guard, report 046; an ancestor), 9 commits, 26 files. The change is:
- `firmware/usb_ls_tx.pasm`;
- `tools/pe/usbdev.py` and its MicroPython copy;
- the regenerated bundle;
- two new test modules and additions to two others;
- mutants, docs and `evidence/model-usb-ls-restage-20261104/`.

`usb_ls_rx`, `usb_fs_dev`, `usb_ls_dev`, `src/` and `flow/` are unchanged.

Both races are reproduced on the code before the fix, closed after it, and the harness that shows it does so at the bus's legal minimum gap too.

| Claim | Result |
|---|---|
| 1. LS two-core and stage-abort harnesses, SPI cost a parameter; two harness fixes | PASS. The fixes are sound: the results hold at a 2-bit-time gap (below) |
| 2. Claim 8 reproduced at 50 µs (stale at n = 8 token first, n = 9 both); 104/104 after | PASS: my rerun on c887e28 gives the committed table line for line |
| 3. Note 3 reproduced at both speeds (128 cases); all right after | PASS: same |
| 4. `pe.usbdev` checks every stage; `usb_ls_tx` flushes while F_SETUP and NAKs F_STAGED over empty; 49→55; turnaround 2.312 before and after; one mutant per firmware change | PASS |
| 5. Every-access tests permanent; `test_nothing_left_staged_after_a_transfer` kills the two revived mutants | PASS |
| 6. Docs: kv260 README, verification-story, word counts in guide / pio-comparison / test; evidence with sha256s and the commit map | PASS |
| 7. Model 2842 passed, 29 skipped; RTL `test` 27/27; test_sigrok 8/8; lockstep 6/6; 16/16 mutants | PASS |

## Checked

**The asked check: do the "before" tables reproduce on the pre-fix code?**
- **How.** On runner1, `git archive` copies of c887e28 (before) and 699e430 (the fix). The evidence's own `ls_case.py` gives the re-stage table: n = 0-25 × both resumes × 48 and 2400 clocks per access. The abort module's `__main__` gives the abort table: n = 0-15 × full and low speed × both resumes × both costs. They ran under `xargs -P 12`, as `jobs*.sh` do.
- **c887e28:** both outputs equal the committed `table-restage-dc51cc8.txt` and `table-abort-dc51cc8.txt` as sets of lines. Only the order differs, from `-P`.
  - **The re-stage table:** stale at overhead 2400 for `n=8` token first and `n=9` both resumes; 101 right.
  - **The abort table:** full speed stale at n = 0-1 and F_UNDERRUN at n = 2-4, at both costs; low speed stale at n = 0-5 at 2400, and the CRC errors at n = 0-2 at 48.
- **699e430:** both tables together equal `table-8fdda85.txt`: 232 of 232 right.
- **The commit map.** The README maps dc51cc8 → c887e28 and 8fdda85 → 699e430. The equal outputs bear out its statement that the rebase changed nothing these tables run.

**The asked check: are the harness fixes realistic?**
- **The gap fix.** `GAP = 8` bit times between a handshake's EOP and the host's next token is `UsbHost.data_in`'s own retry gap. USB 2.0 §7.1.18.1 requires at least 2 bit times; the old harness sent the token at 0, which no host may do.
- **My attack: the gap set to the legal minimum.** I set `GAP = 2`, and also `4`, in copies of 6e1da04:
  - with the fix, `test_restage_held_at_every_access_ls` gives `104 passed` at both gaps;
  - with c887e28's `usb_ls_tx` in the same copies, `3 failed, 101 passed` at both gaps, the claim-8 cases.

  So neither the pass nor the failure depends on the 8-bit choice.
- **The second fix** (`at_decision`, `test_usb_ls_restage.py:159-171`) arms the hold on the elapsed time the software will compute, one SPI read later. It moves the hold onto the access that really decides, and doesn't touch the host.

**The asked check: what does the full-speed software check cost on IN → DATA?** Nothing at the bus. The firmware's IN path is unchanged at full speed, and the new FSET checks are SPI accesses in `stage()`, before F_STAGED is set. `test_usb_fs.py -s` at edc9042 and at 6e1da04 prints the same lines:
- turnarounds 10.9 / 12.7 / 15.7 clocks;
- 8-byte 407 / 597 kB/s, 16-byte 561 / 854, 24-byte 709 / 998, 64-byte 1056 / 1260 kB/s.

Only the run time differs. (`usb-full-speed.md`'s table has 708 / 997 and 1259 for the 24- and 64-byte rows. That predates this branch, and edc9042 gives the same as 6e1da04.)

**The firmware, read against the ISA.**
- **The new `idle` order** is F_RESET, F_ACK, **F_SETUP**, F_IN. While F_SETUP is set, `setup_flush` pulls words until `txne` is false, then goes back to `idle`.
- **The IN path** is `jmp !f F_STAGED, send_nak` then `jmp txne, staged`. That is the one-core guard's form.
- **When F_SETUP is cleared.** `usb_ls_rx` sets it in `got_setup` and clears it in `trailer`, before the record's last `push`. So the software only sees the SETUP's record, and only stages the reply, after F_SETUP is clear: the flush can't eat the reply.
- **The ACK is checked before F_SETUP.** A drain in progress delays it by at most the FIFO's 8 words × 3 instructions = 24 clocks, under one low-speed bit. `measure_ls_ta.py` on c887e28 and 699e430 gives the committed `turnaround-*.txt` exactly: 2.312 bit times for ACK, NAK and DATA, before and after.
- **Word counts** (assembled): `usb_ls_tx` 55, `usb_ls_rx` 143, `usb_ls_dev` 130, `usb_fs_dev` 142. `guide.md`, `pio-comparison.md` (4.1× is 130 / 32) and `test_pio_comparison.py` match.

**Mutants.**
- **The builder's set.** `test/mutants.py "usbfs-*" "usbdev-*" "fw-usb-*" "hw-guard-run-*"`: `16 of 16 mutants killed`:
  - `fw-usb-ls-tx-no-setup-flush` by `test_two_core_setup_drops_what_was_staged`;
  - `fw-usb-ls-tx-no-in-guard` by `test_two_core_in_with_f_staged_over_an_empty_fifo_is_naked`;
  - `usbfs-zlp-staged-twice` and `usbdev-zlp-at-wlength` by `test_nothing_left_staged_after_a_transfer`.
- **My three, in copies of 6e1da04,** run against the LS re-stage, stage-abort, in-guard, `test_usb_enum` and `test_usb_ack` modules:
  - the `jmp f F_SETUP, setup_flush` line removed: `7 failed`;
  - `jmp txne, staged` made unconditional: `7 failed`;
  - edc9042's `usbdev.py` with the new firmware: `31 failed`. This is the stage-abort full-speed cases, so the software check is what closes note 3 at full speed, as claimed.

**Claim 7.**
- **The model suite.** `remote_test.sh model` at 6e1da04: `2842 passed, 29 skipped`. My first run timed out (`rc=124`, at about 98% with no failure) while my own table jobs loaded runner1. The rerun alone passed.
- **`remote_test.sh` RTL and lockstep:** `test rc=0`, `TESTS=27 PASS=27`; `lockstep rc=0`, `TESTS=6 PASS=6`.
- **sigrok.** RTL `test_sigrok` with `SIGROK_CLI` in my clone: `TESTS=8 PASS=8`.
- **The bundle.** `pe.mpy_bundle --check`: `firmware bundle matches validated sources`.
- **The permanent per-access tests** with the guard and FS data tests: `322 passed` in 352 s under `-n 12` on a loaded runner. The item's "under a minute" is for the 232 per-access cases alone on an idle one, so I can't confirm or refute it from this.

**Docs.**
- The kv260 README's new paragraph says model and RTL tests only, never on the board, and that the two-core device has not run on this board. The verification-story row says the gate-level runs predate this `usb_ls_tx`.
- The evidence README's sha256 values check, and the tables match what I reran.

## Notes

1. **An IN while F_SETUP is set is now left unanswered, from reading.** `idle` tests F_SETUP before F_IN, so an IN token arriving between a SETUP token and the end of its DATA is not served then. Its F_IN stays set, and would be served after F_SETUP clears: right after the ACK of a later SETUP's DATA, unasked.
   - **How it could arise.** It needs a host that sends an IN while a SETUP's DATA is outstanding. A correct host doesn't: SETUP is token plus DATA0 back to back, and a lost DATA makes the host retry the SETUP, which clears F_SETUP at its DATA, or reset the port.
   - **The cheap guard.** `flag clr F_IN` in `setup_flush`, or in `usb_ls_rx`'s `got_setup`, would remove the question.
   - I have not tested it.
2. **Run time of the per-access tests.** On a busy runner the 322 USB tests took 352 s under `-n 12`, and running them alongside the whole suite pushed the suite past `remote_test.sh`'s 1800 s limit once. On an idle runner the suite takes 582 s. This is worth knowing for CI, not a defect.

## What I ran

**On runner1:**
- `remote_test.sh model test lockstep` at 6e1da04: the model timed out under my load (above); `test` 27/27, lockstep 6/6. Then `remote_test.sh model` alone: `model rc=0`, `2842 passed, 29 skipped in 582.49s`. Checkouts removed.
- In `~/verifier-047/`:
  - both tables at c887e28 and 699e430, compared with the committed ones;
  - `measure_ls_ta.py` at both;
  - the permanent tests at 6e1da04;
  - the GAP 2 and 4 copies, with and without c887e28's `usb_ls_tx`;
  - `test_usb_fs.py -s` at edc9042 and 6e1da04;
  - my three mutant copies;
  - `test/mutants.py` (16);
  - `pe.mpy_bundle --check`;
  - RTL `test_sigrok`.

**Locally:**
- the diff;
- `usb_ls_tx`/`usb_ls_rx` against `docs/isa.md`;
- word counts;
- the doc changes.

My runner1 scratch and checkouts are removed. No SWD test ran (the whole suite only through `remote_test.sh`), I did not touch the Kria, and nothing was pushed.
