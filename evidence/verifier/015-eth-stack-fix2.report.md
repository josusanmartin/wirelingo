VERDICT: PASS

Commit 87a1b70, base d2d89b8. All three findings from 010 are resolved. `--measure-spim` now gives the port back in every case I could construct, including a signal landing inside the restore. The eth-stack items are clear on this branch. This is all against a fake register file, as the item and the doc say, not the Kria.

| Claim | Result |
|---|---|
| 1. `SEL` restored if clearing QUAD_EN fails; signals handled | PASS, both tables rerun |
| 2. Sustained row 16.0/15.5 us, asserted both sides; go/no-go stays 15.5 | PASS |
| 3. Unmatched pattern errors; 21/21 mutants | PASS |
| 4. Model 1527, RTL 7/7, TAP and gate level 3/3 at 400b294, same code | PASS |

## Claim 1: my tables, rerun on this code

**Stuck master** (010's table).

| Master stuck from | Result | Final `SEL` | QUAD_EN |
|---|---|---|---|
| never | ok | 0 | 0 |
| first GO | reported | **0** | 0, never set |
| second GO, or later | reported | **0** | 1, left set, and the error says so |

In 010 the stuck rows ended with `SEL` at 1.

**Real signals from outside the process, sent twice.**

| Signal | Exit | `SEL` writes |
|---|---|---|
| SIGINT | 130 | 1, then 0 |
| SIGTERM | 143 | 1, then 0 |
| SIGHUP | 129 | 1, then 0 |

In 010, SIGTERM and SIGHUP left `SEL` at 1.

**A signal inside the restore itself**, as the item asked. A clean session makes 76 register accesses, so I delivered signals at each one:

| Case | Runs | `SEL` wrong, QUAD_EN left, or handlers not restored |
|---|---|---|
| One signal at every access, 3 signals, `SEL` starting at 0 and 2 | 456 | **0** |
| Two signals, every pair of accesses | 2926 | **0** |
| One signal at every access, master stuck from GO 1, 2, 3 or 5 | 116 | **0** in `SEL` and handlers (QUAD_EN can stay set here, and is reported) |

What the code leaves behind: a signal inside the restore is ignored, the restore finishes, the select is back, and the old handlers are put back last. The alternate-clock bit survives.

## Notes

1. There is a window of a bytecode or so at the entry to the `finally`, before `live[0] = False`. A first signal arriving exactly there would raise before the restore. On Python 3.12 this looks closed, since signals are only checked at certain instructions, and that is what I ran. I did not check the Kria's Python, and my timing test could not reach it: 60,000 randomly timed sessions all finished before the signal landed, so that test proves nothing and I am not counting it. Setting `live[0] = False` is the right design. A wider fix would block the signals around the restore with `signal.pthread_sigmask`.

2. `fpga/kv260/host/eth_tap.py`, `main`: `Spim(div=1)` is inside the `try`, so "pe_spim not found: is the protoemu app loaded?" also prints "pin select restored, but QUAD_EN may still be set", which is wrong for that case, since nothing was touched. Harmless, but the orchestrator will read this message on the board. From reading the code, not run.

3. SIGKILL cannot be caught. The doc says so. A power loss or `kill -9` during the short measurement leaves the port on the fabric master, so the board's state should be checked if a run dies that way.

## Other claims

- **Claim 2:** `test_service_budget_for_sustained_traffic` asserts 16.0 and 16.5 at 50 MHz and 15.5 and 16.0 at 40 MHz. The table row is now labelled, and it matches my 010 search. 15.5 us remains safe on both clocks.
- **Claim 3:** `python3 test/mutants.py nosuch-name` prints `no mutant matches 'nosuch-name'`. The three new mutants cover the nested restore, SIGHUP and the second signal, and `eth-*` gives 21 of 21.
- **Claim 4:** `git diff 400b294 87a1b70 -- tools firmware fpga src flow test/test_eth_stack_rtl.py` is empty, so the code the TAP and gate-level runs used is this commit's. The gate-level receipt shows `400b294`, `validation_dirty` false, 3/3. Both TAP logs show 5 of 5, 2 of 2, UDP echoed and `pass: true`. The round-trip figures match the doc: 9222 and 4221 ms, 208-1110 and 215-1129 us, 3.8-5.0 ms.
- **The tests can fail now:** the fake gained `stick_at`, so the stuck paths that 010 found untested are covered, and the mutants prove it.

## What I ran

On runner1 at 87a1b70, except the signal tests, which ran locally against the fake. Scratch was in `verifier-015/`, since removed. No Kria, no TAP, no sudo.

- `remote_test.sh model test_eth_stack_rtl`: `1527 passed, 23 skipped in 298.57s`, `test_eth_stack_rtl rc=0`, `TESTS=7 PASS=7 FAIL=0 SKIP=0`.
- `python3 test/mutants.py 'eth-*'`: `21 of 21 mutants killed`, rc 0.
- I did not rerun gate level or the TAP: gate level is covered by the receipt and the empty diff, and the TAP needs sudo. 010 reran gate level on the previous commit.
