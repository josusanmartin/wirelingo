VERDICT: PASS

Commit 0bdcbe5, base 1fddeef. Reviewed `test/model/test_eth10_match.py`, `docs/eth10-cut-through.md` and `fpga/kv260/README.md`, as the item asks. All three findings from 004 are resolved. With this, the `eth10_match` fixes from 000, 001 and 004 are complete at this commit.

| Claim | Result |
|---|---|
| 1. Rise bounded directly; delay N = 1, 3, 100 fail; unmodified passes | PASS: I ran it myself |
| 2. Log's line range and omissions stated; "250 ns" labelled as the test sender's | PASS |

## 004's findings

1. **The assertion can now see the chip.** `test/model/test_eth10_match.py:145` no longer uses the measured reaction, so the rise does not cancel. It asserts `after - 11 clk <= lead <= after - 10 clk`, where `after` is the sender's own geometry.

   | Firmware | `-k rise_to_last` |
   |---|---|
   | Unmodified | 2 passed; 38.14-38.39 us at 40 MHz, 38.19-38.45 us at 50 MHz |
   | HIT 1 instruction late | 2 failed |
   | HIT 3 late | 2 failed |
   | HIT 100 late | 2 failed |
   | HIT 4 instructions early, my addition, for the other side of the bound | 2 failed |

   In 004 this same test passed at N = 100. The printed ranges sit inside the doc's four ranges, which are unchanged and correct. The doc now says "bounds" where it said "checks exactly".

2. **Log range.** `fpga/kv260/README.md` now gives lines 1691-1702, matching the item, and says the banner and the `pre-timing runs` line are left out. The count works: 12 lines in the range, 10 quoted, 2 named as omitted. The range stops at `summary:`, so the demo's final `ALL PASS` line is outside it. I still cannot read the Kria, so the comparison with the file remains the orchestrator's.

3. **250 ns.** `docs/eth10-cut-through.md` and the test's docstring both now call it the test sender's TP_IDL.

## Note

1. The first `remote_test.sh model` run on this commit gave `1399 passed, 23 skipped`, which adds up to 1422, while 1423 tests are collected at this commit and at the base. Its log has no failure, and its exit status was lost to my `tail`. A second run gave `1400 passed, 23 skipped`, rc 0, with 1423 distinct JUnit cases, 23 skipped and 0 failed. So nothing failed, but I cannot say what happened to that one test in the first run. runner1 was heavily loaded at the time. If that count drops again, look at `pytest-xdist` under load.

## What I ran

On runner1 at 0bdcbe5. Throwaway copies were removed afterwards. The commit carries other merged commits; per the item I did not review them, and `git diff 1fddeef 0bdcbe5 -- test tools firmware` touches only `test/model/test_eth10_match.py`.

- `remote_test.sh model`: `1399 passed, 23 skipped in 690.68s`, as in the note.
- Rerun with JUnit: `1400 passed, 23 skipped in 771.40s`, rc 0.
- Delay and early-pin mutations as above.
