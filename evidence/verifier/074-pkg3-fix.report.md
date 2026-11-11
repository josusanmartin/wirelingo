VERDICT: PASS

074 pkg3-fix (branch `ao-pkg3`, `843160b`, `18c180b`, `eb63aa8` on `3a7feab`, backlog AO): verifier report.
Kria: verifier does not touch it. No SWD.

The three commits fix 072 finding 1, apply 072 finding 2, fix 070 finding 1 and apply 071 finding 2, as described:
- `eth10_tx_fcs` now sends its first link pulse 16.00 ms after a frame on the model, as `eth10_tx` does;
- the new link-pulse test and the two new mutants work;
- the pinned `ep1_ta` bounds hold;
- the Lean fetch-ownership check passes at 3 cores on runner1.

Nothing else changed: no `src/` and no `flow/`.

## Findings

None. One note: the builder chose to reload r1 in TP_IDL rather than move the FCS count to r2, which I had also offered. It is the better fix. It gives 16 ms after every frame, so it also meets the 8 ms lower bound, which a count carried across the frame doesn't promise.

## Asked checks

- **The new TP_IDL** (`firmware/eth10_tx_fcs.pasm:92-94`): `ldi r1, 9999 side 1` (1 tick H) plus `nop side 1 [8]` (9 ticks H) is 10 ticks H, the same as the old `nop side 1 [9]`. So TP_IDL is still 300 ns after a 1 and 275 ns after a 0. r1 is 9999 on the way to `idle_wait`, whose `jmp r1--` counts 10,000 × 64 ticks = 16 ms. Nothing between `tp_idl` and `idle_wait` touches r1.
- **`nlp_after_frame.py`** (mine, from 072, rerun on runner1 at `eb63aa8`, test_eth10's line at 40 MHz, one 64-byte frame):
  - "eth10_tx: frame end -> next link pulse 16.00 ms ; pulse-to-pulse 16.00 ms";
  - "eth10_tx_fcs: frame end -> next link pulse 16.00 ms ; pulse-to-pulse 16.00 ms". It was 104.86 ms at `3a7feab`.
- **The new test** `test_link_pulse_within_8_to_24_ms_after_a_frame` measures from the decoded end of TP_IDL to the first idle-to-H transition. It asserts 8–24 ms for `eth10_tx_fcs` and ≤ 24 ms for `eth10_tx`. It is in the suite below, and its mutant is killed (below).
- **Model suite** at `eb63aa8` (my runner1 clone; `remote_test.sh`'s pytest flags but `-n 8`, with `--ignore=test/model/test_swd.py`): rc 0, "3002 passed, 29 skipped in 1108.70s (0:18:28)". That is +2 over `3a7feab`'s 3000. SIGROK checks skipped: 28, and a pass says nothing about them.
- **Mutants.** `test/mutants.py "fw-*"` at `eb63aa8` on runner1: **"40 of 40 mutants killed"**, every baseline passing. Among them:
  - `fw-eth10-tx-fcs-no-link-reload` by `test_link_pulse_within_8_to_24_ms_after_a_frame[True]` (50 s);
  - `fw-usb-ep1-turnaround-one-tick-more` by `test_deadlines_static_and_sound`;
  - the 6 from 072, again.
- **The `ep1_ta` pin** (`test/model/test_usb_enum_ep1.py:193-197`) asserts `ep1_ta` at (19, 25), `in_ta_any` at (10, 25) and the max at 26, the values I got from `pe.timing` in 072. Keeping `ep1_ta` `reach=conditional` is right: not every token path reaches an IN answer, by design.
- **The RTL loopback with the new program.** `make TESTCASE=test_eth10_fcs_in_engine_loopback` at 3 cores (`-DPE_NCORES=3 -DPE_NBANKS=3`): "TESTS=1 PASS=1 FAIL=0". The builder's reason for no RTL link-pulse test (640,000 clocks) is fair: the model is lockstep-equal to the RTL, which is unchanged since `8c4c635`.
- **Word counts** (my assembly of `eb63aa8`): `eth10_tx_fcs` 53, `eth10_rx_fcs` 27, `usb_enum_ep1` 255. ✓
- **`docs/isa.md` against 070 finding 1:**
  - §5.1 now says RET is unchanged whenever the CALL's link is in its bank. The exception is a CALL in the bank's last word with no wrap there, whose link is the next bank's first word (core 3: word 0). v0.2 faulted there; v0.3 goes to word 0 of the own bank. That matches the RTL (`{pc[9:8], lr[7:0]}`) and the model.
  - §8's lead sentence now excepts a PC write that leaves the bank.
  - §8's table has the new row ("none in `firmware/`", which my 070 scan confirms: no assembled program has a CALL in word 255).
- **Lean at 3 cores on runner1.** I used the Lean 4.28.0 install already there, read-only, with OSS CAD Suite Icarus, on the committed testbench (`.NCORES(3), .NBANKS(3)`, values masked by enables, the same mask I tried in 071):
  - `run_fetch_ownership.py`: rc 0, "result": "pass", with "actual_mutant_first_mismatch": 29 and "offline_shift_first_mismatch": 31;
  - `check_rtl_rx_autopush.py --phase all`: rc 0, "pass".

  The new testbench comment says core 2 is not observed by name and that its fetch is covered by the 3-core lockstep. That is accurate.
- **Privacy.**
  - `record_check.py --repo . --tree HEAD --commits next-8x4..HEAD` at `eb63aa8`: "tree HEAD: 727 files checked", "commit messages checked: 17", "TOTAL flagged: 0".
  - My private-string check over the delta and its three messages: "TOTAL 0".
  - The pattern grep finds only the trailers' noreply address.
  - All three commits are by the noreply identity and carry the trailer.
  - `git merge-base --is-ancestor b2fa7f3 HEAD` exits 1.
- **ISA timing check.** TP_IDL's tick count is unchanged (above). No RTL changed.

## What I ran

- **runner1** (`~/protoemu-flow/verifier-074/`, my clone at `eb63aa8`, one worktree per job; no harden):
  - `nlp_after_frame.py`;
  - the model suite;
  - `test/mutants.py "fw-*"`;
  - the RTL loopback test;
  - the two Lean checks.
- **Locally:** reading the diff, assembling the three programs, and the privacy tools.

Summary: all four fixes are in and verified. 072's link-pulse bug is gone (16.00 ms), and it is now guarded by a test and a mutant.
