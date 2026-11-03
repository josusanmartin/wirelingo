VERDICT: PASS

Commit ceb84a4, base d811228. All six findings from 002 are resolved. The new stall budgets are right, not merely lower: they survived an independent sweep well beyond the builder's own, and each one is tight. Firmware changes are comments and `@deadline` lines only; code lines are identical to d811228.

| Claim | Result |
|---|---|
| 1. Budgets 69, 66, 63, 60, 57; 43 for bursts; 28 each for two stalls at 12 clocks | PASS: independently confirmed |
| 2. Figures are exact asserts; rxf mutant fails 22 of 22 | PASS: 22 failed in my run |
| 3. 189 exact; TX cushion 0/46/94/142; turnaround 12/11/10 | PASS |
| 4. `rmii_match` limits stated and tested | PASS |
| 5. `STATUS.stalled` documented and tested | PASS |
| 6. RP2350 rated 150 MHz, 160 flagged as an overclock | PASS, source note below |

## Why my 71, 59 and 45 were too high

My 002 figures held the frame's start fixed against the reader, so they sampled too few phases. The builder's test sweeps 12 start offsets against the reader as well as the stall's place in the reader's cycle, and the worst phase is 2 clocks lower in each case. My report's point stands (the budget depends on the host), but those three numbers were optimistic. So were my "70 in the other SCK phase" and "71 at 9 clocks per read", and the builder's "same in both SCK phases" replaces them.

## The attack on claim 1

The builder's test could not check itself, so I looked for its blind spots.

| Possible weakness in the test | My check | Result |
|---|---|---|
| Stalls only near read 60, assuming steady state. Burst hosts drift slowly against the word clock | The claimed budget at reads 1-124 and all 12 offsets, 1488 places for each of 8 hosts | **0 of 11,904 fail** |
| Is each budget tight? | Budget + 1, reads 40-99, all offsets | Fails somewhere for all 8 hosts: 180 of 720 at 8 to 11 clocks per read, 60 at 12, and 27, 23 and 15 for bursts of 7, 8 and 12 |
| Searches downward and rechecks only the final value, so a smaller stall could fail | Every stall from budget - 30 to budget, reads 60-71, all offsets, for 3 hosts | **0 of 13,392 fail** |
| Stalls only with SCK held inside a burst, never with CS high between bursts | A new script extending the gap, 6 burst positions, 12 offsets, stalls 20-89, bursts of 7, 8, 12 | **43 for all three**, the same figure |
| 400-byte frames only | Budget at 8 places and 12 offsets on 1518-byte frames, 3 hosts | **0 of 288 fail** |

My sweep ran in the tested SCK phase only. The other phase rests on the builder's exact asserts, which pass in my run.

The catch-up formula at `docs/rmii-study.md` follows from the rates: a backlog of S/12 words cleared at 1/p - 1/12 gives S p / (12 - p). "57/2 = 28" matches the measured two-stall figure.

## 002's findings

| # | Status | Evidence |
|---|---|---|
| 1 Budget wrong for the doc's hosts | Resolved | Verdict row, table and step 4 now give per-host figures with catch-up |
| 2 71 not asserted | Resolved | `STALL_BUDGET` exact. My rxf mutant, which fell through in 002, fails 22 of 22 |
| 3 Other numbers unpinned | Resolved | 189 exact in both phases; `TX_CUSHION` exact to the clock; deadlines `min=max` at 12, 11, 10 and also measured. A one-tick-longer end path fails the static test and the bounds test |
| 4 `rmii_match` limits | Resolved | Three new tests. `push block` and lowering HIT at `quiet` each fail the matching test |
| 5 `STATUS.stalled` | Resolved | Doc and test, 0x1000 for both |
| 6 RP2350 | Resolved | Doc flags the overclock; 150 / 4 = 37.5 MHz, enough for RX at 33.3, not TX at 40 |

## Notes

1. An 8-bit mask on the threshold passes `test_match_threshold_wraps_at_16_bits`, because that test only uses 5 and 4. It is caught by `test_reaction_hits_and_misses`, so nothing is unguarded.
2. I confirmed 150 MHz from distributor listings (Pimoroni, Farnell), not the RP2350 datasheet the doc links, which I did not open.
3. `docs/rmii-study.md` says the budgets are model figures and that RTL confirms only the burst limit. That is the right label.
4. From 002, still open: the LAN8720A figures have not been compared with the datasheet.

## What I ran

On runner1 at ceb84a4. Throwaway copies were removed afterwards.

- `remote_test.sh model test_rmii_rtl`: `1533 passed, 23 skipped in 212.56s`, `test_rmii_rtl rc=0`, `TESTS=6 PASS=6 FAIL=0 SKIP=0`. Model tests in `test_rmii.py`: 138 collected, as the doc says.
- The sweep above: 41,448 model runs in total, about 10 minutes.
- Mutations: control 29 passed. rxf moved: 22 failed. Match `push block`: caught. Match HIT lowered: caught. One tick longer: caught. 8-bit threshold mask: 1 failed, 137 passed on the whole file.
- `git diff`: firmware code lines unchanged in both files, and nothing under `src/` or `flow/` changed.
