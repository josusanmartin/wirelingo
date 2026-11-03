VERDICT: PASS

Commit f497922, base 6d5bdd5. `eth10_reply` does what the doc says on the model and the RTL, and every number I reran matches. The bound N in [0, 1) is derived from the static checker and the chip's fixed latencies, not fitted. The RTL check does bound N ≥ 0, and it catches a reply one clock early and one clock late. The doc's limits are honest, and I found no hardware implication. The `test_hw_day.py` change was ignored as asked.

| Claim | Result |
|---|---|
| 1, 2. N at 50 and 40 MHz | PASS: `N = 0.003-0.994` over 31, `0.024-0.926` over 38 |
| 3. Bound derived | PASS (below) |
| 4. From the last bit | PASS: `9900.1-9919.9 ns`, `9900.6-9923.2 ns` |
| 5. Jitter | PASS: `N = 0.025-0.779 clocks over 12 replies` |
| 6. 60 frames, two decoders | PASS; note 3 on independence |
| 7. RTL N and the half-clock offset | PASS (below) |
| 8. Back to back | PASS: `10140-10140`, `10175-10175`; RTL `b.t0 - a.t1 == 10175.0` asserted |
| 9. Blind window | PASS: 95/100 ns at 50 MHz, 120/125 ns at 40 |
| 10. Arriving while replying | PASS |
| 11. Just after | PASS: 480/485 ns, 500/505 ns |
| 12. Link pulses; short TP_IDL | PASS: N 2.98 / 3.99; 60 ns TP_IDL gives 11.55-11.96 and 10.20-10.52, never below 0.55 |
| 13. `WIRE_STAMP` | PASS for this firmware. The old 9800 ns figure is answered in note 1 |
| 14. Limits | PASS; note 2 |
| 15. Model, RTL, mutants | PASS, all reproduced |

## Claims 3 and 7, checked

**The bound (claim 3):**
- `bounds()` takes the `ifg` deadline from `pe.timing` on the committed firmware: exact, 477 clocks at 50 MHz and 381 at 40, which is `96p - WINDOW_TRIM + PATH_TICKS`.
- It adds the synchronizer's [2, 3) and the 1-clock output, giving [0, 1). `test_deadlines_from_the_firmware` asserts all of that.
- `assert_timing` checks each piece per reply: sync in [2, 3), firmware interval == the deadline, output == 1, and N in the bound.
- `WINDOW_TRIM = 8` is chosen so the sum lands on the legal minimum; it is not read off the observations.
- At exact phases (my probe, `ppm 0`):
  - an edge on a clock edge gives N = 0.0, sync 2.0;
  - 0.001 ns after it gives N = 1.0, sync 3.0 (the open end);
  - mid-clock gives 0.5.

  So the bound is tight at both ends, as derived.

**The RTL check (claim 7):**
- `TdLog` stamps a TD change at the next falling clock edge. In zero-delay RTL, TD changes at a rising edge, so a reading is exactly N + 0.5.
- The far end's edges are driven at exact times, and the last one, the TP_IDL fall, has no jitter.
- So `0.5 <= n < 1.5` is `0 <= N < 1`, and does bound N ≥ 0.
- The 40 MHz 0.00 is the aligned case (sync 2.0), which agrees with the model.
- Mutants on RTL: `WINDOW_TRIM = 9` (a clock early) gives `N = 0.08` and `N = -0.50`; `WINDOW_TRIM = 7` (a clock late) gives `2.08` and `1.50`. Both fail both tests: `TESTS=2 PASS=0 FAIL=2` each.

## Notes

1. **Claim 13, the 9800 ns in `docs/eth10-network.md:94-97`.** It is not wrong for the phase it was measured at, but it is one point in a 20 ns range, and "asserted exactly" holds only for that phase.

   I ran `test_carrier_sense_rtl` with the far end shifted by sub-clock offsets (RTL truth = reading − 10 ns), and the same case on the model through the Wire's new `offset_ns`:

   | Far-end last edge after a clock edge | RTL truth | Model stamp | Model − 1 clock |
   |---|---|---|---|
   | 0 ns (the committed test's phase) | **9800** (9810 read) | 9800 | 9780 |
   | 4 ns | 9816 | 9836 | 9816 |
   | 8 ns | 9812 | 9832 | 9812 |
   | 12 ns | 9808 | 9828 | 9808 |
   | 16 ns | 9804 | 9824 | 9804 |

   What this shows:
   - At every off-grid phase, the model stamp is exactly one clock late against the RTL, as 025 says.
   - At exact alignment, the uncorrected stamp equals the RTL: for `eth10_tx_cs` the model also sees a clock-coincident edge one clock early, and the two errors cancel. I didn't find why; for `eth10_reply` the aligned case is right (above).
   - The model's sweep starts the far end at whole microseconds, so it only ever measured the aligned phase.
   - So at the pads, the gap from the far end's last edge is 9800 to 9820 ns at 50 MHz. At 40 MHz the model gives 9800-9825 corrected (not run on RTL).
   - Every value is far above 9600 ns, so the deferral claim stands. The doc should give the range rather than one exact figure, and say that subtracting `WIRE_STAMP` there would be wrong at that phase.

2. Wording:
   - The README row's limits ("fixed reply; no link pulses; no collision handling") leave out that the decision comes before the FCS, so a corrupted frame can be answered. The doc says it; the one-line row should too.
   - guide.md's row lists "collisions" among the tested cases next to features. Say "collisions (not handled: our reply goes out whole)".
   - The bound is half-open only in simulation: a real synchronizer at an edge that coincides with a clock can resolve either way, giving [0, 1]. N ≥ 0 is unaffected.

3. Claim 6: the decoders are independent algorithms. One tracks mid-bit edges; the other samples the level at 25 and 75 ns of each 100 ns cell, laid from the first edge. But they read the same `Burst` edge list: the Wire's on the model, `TdLog`'s on RTL. A capture or stamping error would reach both. Call them two decoders of one capture.

4. Some ordering checks in `test_eth10_reply.py` use raw Wire stamps, which are a clock late, so they are one clock looser than stated: `check_train`'s window `sent[i][1] < b.t0` at `:249`, and `:348`, `:375`, `:436`. The N bound itself goes through `td()`, and our own gaps (`:354`) cancel the stamp.

5. My 8 extra firmware mutants against `test_eth10_reply.py`: **7 killed.**
   - Killed: EtherType unchecked, threshold + 1, HIT never raised, HIT left high, no record push, chunks 5 and 6 swapped, SFD matched on one 1.
   - Survivor: `wait 1 pin RXP` for `wait edge RXP` in the window. It is equivalent here, because the core has just waited for RXP low, so the only possible change is a rise.

## What I ran

On runner1 at f497922, in `verifier-025/`, since removed.

- `remote_test.sh model test_eth_reply_rtl`: `2299 passed, 28 skipped in 284.10s`; `TESTS=2 PASS=2 FAIL=0 SKIP=0`; rc 0.
- `python3 test/mutants.py 'reply-*'` (git clone, venv on PATH): `15 of 15 mutants killed`, rc 0.
- `pytest -s test/model/test_eth10_reply.py`: `39 passed`, with every figure above. Program size `161 words of 256`.
- The RTL `WINDOW_TRIM` 7/9 mutants, the RTL and model phase probes of `eth10_tx_cs`, the `eth10_reply` exact-phase probe, and my 8 mutants, each in its own copy.
