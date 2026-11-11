# 074 pkg3-fix

Branch `ao-pkg3`, tip `eb63aa8`, three new commits on `3a7feab` (the tip
070–072 were verified at). `next-8x4` = public/main `d2ed344` is the base;
`git merge-base --is-ancestor b2fa7f3 HEAD` fails, as it must.

- `843160b` docs/isa.md: RET after a CALL in a bank's last word (070, finding 1);
- `18c180b` eth10_tx_fcs: reload the link-pulse count in TP_IDL (072, finding 1),
  the link-pulse test, ep1_ta pinned (072, finding 2), two mutants;
- `eb63aa8` formal/lean: the fetch-ownership testbench at 3 cores with the
  values masked by their enables (071, finding 2).

Backlog item AO, fixes for 070 / 071 / 072. Evidence levels: model, RTL
simulation, formal (Lean-backed finite check, on runner1). No RTL change.

The builder's claims:

1. **072 finding 1, fixed.** `eth10_tx_fcs` used r1, idle_wait's link-pulse
   count, for its 32 FCS bits and left it at 0xFFFF. TP_IDL's 10 ticks are
   now `ldi r1, 9999 side 1` + `nop side 1 [8]` (was `nop side 1 [9]`): the
   same H for the same 10 ticks, and the first link pulse comes 16 ms after
   the frame's end. 53 words (was 52). I chose the reload over moving the
   count to r2: with the count reloaded the first pulse is 16 ms after any
   frame, so the 8 ms lower bound holds too, which a count carried across
   the frame (as eth10_tx does) doesn't promise.
   - **New model test** `test_link_pulse_within_8_to_24_ms_after_a_frame`
     (test_eth10_fcs, test_eth10's line at 40 MHz): frame end → next link
     pulse, eth10_tx_fcs within 8..24 ms, eth10_tx within 24 ms. Both
     measure **16.00 ms** on the model (a local smoke run; then in the
     runner1 model suite, claim 5).
   - **Sibling mutant** `fw-eth10-tx-fcs-no-link-reload` (the old behaviour:
     the `ldi` back to a `nop`): **killed** by
     `test_link_pulse_within_8_to_24_ms_after_a_frame[True]` (runner1, 63 s).
   - **RTL check:** not added. 16 ms is 640,000 clocks, several minutes of
     cocotb per run under runner1's load; the firmware change is checked on
     the model, the model equals the RTL by lockstep, and the FCS loopback
     through the SPI port reruns on the RTL with the new program (claim 4).
   - Word count re-checked: `eth10_tx_fcs` 53 (pinned in
     `test_tx_fcs_word_count`), `eth10_rx_fcs` 27, `usb_enum_ep1` 255.
2. **072 finding 2, applied.** `test_deadlines_static_and_sound` now pins
   `ep1_ta` at [19, 25] and `in_ta_any` at [10, 25] clocks (max 26), so one
   clock more on endpoint 1's path fails the model suite (CI) rather than
   eating the margin. Mutant `fw-usb-ep1-turnaround-one-tick-more` (a delay
   of 1 on `flag set F_SENT1`, still under 26 statically): **killed** by
   `test_deadlines_static_and_sound`.
   `ep1_ta` stays `reach=conditional` in the sidecar: a required deadline
   would need every token path to reach the IN answer, which by design it
   doesn't (not ours, not configured, not an IN).
3. **070 finding 1, fixed.** docs/isa.md §5.1: RET is unchanged whenever the
   CALL's link is in its bank, i.e. for every CALL except one in the bank's
   last word with no wrap there (core 3's word 1023: word 0); v0.2's RET
   faulted there, v0.3's goes to word 0 of the own bank. §8's lead sentence
   now excepts a PC write (MOV/OUT PC, RET) that leaves the bank, and §8's
   table lists the CALL-in-the-last-word case. (070 finding 2, `known_id`
   accepting any NBANKS ≤ NCORES: no change, as the report says.)
4. **071 finding 2, applied.** `rtl_fetch_ownership_tb.sv` instantiates the
   3-core build and masks cores 2–3's write values by their enables in
   `inactive_writes`. On runner1 (Lean 4.28.0 under
   `~/protoemu-flow/ao-pkg3/lean-4.28.0`, outputs in
   `~/protoemu-flow/ao-pkg3/lean-out-eb63aa8/`): fetch ownership
   `"result": "pass"` at 3 cores, its planted-mux control diverging at
   clock 29 and the shifted trace at 31, as before; RX autopush (3 cores)
   `"pass"`. Core 2 is still not observed by name (the testbench comment
   says so). 071 finding 1 (Lean locally): every Lean run since is on
   runner1. 071 finding 3 (CRC and R0–R3 behind the pins in logic-only STA):
   no change here; for AP's first harden of this RTL, report the worst slow
   path per endpoint class (pins, CRC, R0–R3, PC, OSR).
5. **Regression at `eb63aa8`** (runner1): model suite **3002 passed, 29 skipped**
   (+2: the link-pulse test); `fw-*` mutants (the library's and the v0.3
   ones) **40 of 40 killed**; test.py at 3 cores **TESTS=30 PASS=30** (it
   holds `test_eth10_fcs_in_engine_loopback` with the new program). The RTL
   is unchanged since `8c4c635`, so 071's lockstep results stand.
6. **Privacy**: `record_check.py --repo . --tree HEAD --commits next-8x4..HEAD`
   at `eb63aa8`: 0 flagged.

Asked checks:
- Read `eth10_tx_fcs.pasm`'s new TP_IDL (10 ticks H, r1 = 9999 on the way to
  idle_wait) and rerun the verifier's `nlp_after_frame.py`.
- `remote_test.sh model` (or just `test/model/test_eth10_fcs.py -k link_pulse`).
- Mutants `fw-eth10-tx-fcs-no-link-reload`, `fw-usb-ep1-turnaround-one-tick-more`.
- docs/isa.md §5.1 and §8 against finding 1's case.
- The Lean fetch-ownership check at 3 cores on runner1.
