VERDICT: FAIL

Commit 1fddeef, base aedf991. All four findings from 001 are addressed and the doc's numbers are right. It fails on one point: the new "exact" assertion cannot see the chip. It still passes with the pin 2 us late, so claim 1's assertion and the doc's description of it are wrong, even though the ranges themselves hold. The fix is two lines.

| Claim | Result |
|---|---|
| 1. Rise-to-last-edge relation asserted exactly; doc ranges follow | FAIL: ranges right, but the assertion is an identity (finding 1) |
| 2. Doc says the FPGA row can't show the 10-11 spread | PASS |
| 3. Orchestrator compared the log with the Kria | Not checkable from here; format fits (finding 2) |
| 4. Heading anchor fixed | PASS |

## Findings

### Blocking

1. `test/model/test_eth10_match.py:151-154`, `docs/eth10-cut-through.md:34-35`: the assertion cannot fail on the rise.
   - `run()` returns `lead = end - rise` and `reaction = (rise - mark) / clk`. The test asserts `lead == geometry - reaction`, so `rise` cancels. What is left is `end - mark == geometry`, which is a property of the sender in `edges_of`, not of the firmware.
   - Shown by mutation, adding N instructions before `pin HIT, hi`, with `-k rise_to_last`:

   | N | Result | Printed rise to last edge, 50 MHz |
   |---|---|---|
   | 0 | 2 passed | 38.19-38.45 us |
   | 1 | 2 passed | 38.17-38.43 us |
   | 5 | 2 passed | 38.09-38.35 us |
   | 100 | **2 passed** | **36.19-36.45 us** |

   - Nothing is left unguarded. The whole file fails at N = 5 through `test_reaction_is_10_to_11_clocks` and the jitter test. Rise = mark + reaction, and that reaction is asserted on the real firmware. That is why the ranges stand.
   - But the doc's "It checks the rise against the frame's end exactly" says something the test cannot do.
   - Fix: bound the lead directly, `384 * bit + tail - 11 * clk <= x <= 384 * bit + tail - 10 * clk`. Then a delay fails this test as well.

### Minor

2. `fpga/kv260/README.md:925-945`, claim 3: I cannot read the Kria (RULES.md), so the comparison stays the orchestrator's. What I can check fits.
   - The two header lines, `PASS (0.4 s)` and `summary:` match the demo's print statements, including field precision. `built 2026-10-06 22:07:40 UTC` matches `fpga/kv260/README.md:174`. A 3 s run is consistent with the 2 s clock measurement plus 0.4 s.
   - The item says lines 1691-1702 and the README says 1690-1702. One of them is off by one.
   - The README range is 13 lines and the quote has 10. The demo's `[eth10_cut]` line, `pre-timing runs` line and `ALL PASS` are the likely 3, but the README only says timestamps were removed. Say lines were omitted too.

3. `docs/eth10-cut-through.md:35`: "250 ns of TP_IDL" is this test's sender, 200 ns high after the last cell. `eth10_tx_cs` itself holds 275-300 ns (`firmware/eth10_tx_cs.pasm:71,74`). The doc's sentence does scope it to a sender, but it should say the figure is the test's.

## 001's findings

| # | Status | Evidence |
|---|---|---|
| 1 Wrong 38.2 bound | Numbers resolved, test not (finding 1) | My 001 measurements were 38.184-38.204 and 38.434-38.453 us at 50 MHz, 38.130-38.153 and 38.380-38.403 us at 40 MHz. All sit inside the doc's four ranges, which are within 0.01 us of the formula. Frames now end in both 0 and 1, and the test requires it |
| 2 "Phase happened" | Resolved | The doc now says the sampler sees the edge on a clock, and that the row confirms the path's length, not the range. This matches 80 of 80 frames at each clock in 001 |
| 3 Log | Addressed | See finding 2 |
| 4 Anchor | Resolved | The heading's slug is `cut-through-match-on-hardware-2026-10-30`, matching the link. The dropped text moved into the paragraph |

Nothing outside `docs/eth10-cut-through.md`, `fpga/kv260/README.md` and the test changed, and the firmware is untouched.

## What I ran

On runner1 at 1fddeef. The throwaway copy was removed afterwards.

- `remote_test.sh model`: `1400 passed, 23 skipped in 173.36s`. No RTL changed, so nothing else was needed.
- Control, `-k rise_to_last`: `2 passed`; printed 38.14-38.39 us at 40 MHz and 38.19-38.45 us at 50 MHz.
- Delay mutations as above. Whole file at N = 5: `3 failed, 7 passed`.
- `payload=2` and frames all ending in 1: both `2 failed`. These failed in 0.04 s, at the test's `{0, 1}` check, before the relation ran, so they prove that check rather than the relation.
