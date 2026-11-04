VERDICT: PASS

Commit f47e875, base 5582d7a. All four notes from report 008 are handled, and the numbers reproduce. The firmware change is comments only. The new limits go further than my note did: the abandoned-write case is worse than anything I reported, and the builder found and stated it.

| Claim | Result |
|---|---|
| 1. After an aborted read the next read is lost and the one after is right; an abandoned write applies wrong data | PASS: held between the test's points too |
| 2. 40 ns hold stated as more than 40 ns, no margin, arithmetic | PASS |
| 3. A skipped sigrok check shows SKIP=1 and SIGROK SKIPPED lines; CI's gap stated | PASS: seen working, by accident as well as on purpose |
| 4. REV_COMPLIANCE comment; 1579 passed, sigrok RTL 6/6, mutants 28/28 | PASS |

## Claim 1 checked between the test's points

The tests abort every 1 us. I aborted every 60 ns, 467 positions per frame type, and read 4 times after each.

| Frame abandoned | Most contention, clocks | Reads after the abort |
|---|---|---|
| Read | 340 | all right, or the first lost and the rest right |
| Write to register 4 | 0 | same |
| Write to register 0 | 0 | same |
| Read to another PHYAD | 0 | same |
| Write to another PHYAD | 0 | same |

340 clocks is the true worst case at this resolution, and no abort point loses two reads. `test_abandoned_write_applies_partial_data` pins the wrong-data result: 0x3FFF for an abandoned 0x1234. The test's name, the doc and the firmware comment agree, and the doc says this is model only, not RTL. What two drivers do to real pads is stated as not examined.

## Claim 3 worked in a way I did not plan

The item's sigrok path, `~/protoemu-flow/agent-runs/sg-bench-fw/sigrok-cli`, no longer exists. My first run set it, and the result was exactly what this change promises:

```
SIGROK SKIPPED: bench_mdio_master_and_phy: mdio decoder
SIGROK SKIPPED: bench_qsfp: i2c decoder
SIGROK SKIPPED: bench_pmbus: i2c decoder and PEC recomputed over its bytes
TESTS=6 PASS=5 FAIL=0 SKIP=1
```

Before this commit that run would have looked like a clean pass. I reran with `~/protoemu-flow/sigrok/sigrok-cli`, version 0.7.2.

| Run | Model | RTL | `check_results.py`, the 5 named tests |
|---|---|---|---|
| Without sigrok | `SIGROK CHECKS SKIPPED: 28 tests` printed | `TESTS=6 PASS=5 FAIL=0 SKIP=1` | passes, so CI still works |
| With sigrok | `190 passed`, no skip lines | `TESTS=6 PASS=6 FAIL=0 SKIP=0` | passes; the check with all 6 also passes |

The `conftest.py` hook matches on the text "sigrok-cli not found", which is the real skip reason, so it fires. It lists 28 tests, all those on the model that need sigrok, not only these. `bench_sigrok_available` runs and passes with sigrok. A check that reads every `@cocotb.test()` would miss `@cocotb.test(skip=...)`, which is why CI now names the 5 tests.

## Other checks

- **Claim 2:** the doc, the firmware comment and the test's docstring all say more than 40 ns, and that it is arithmetic, not measured.
- **Claim 4:** `tools/pe/qsfpdev.py` now says Rev 2.12 narrows the code to 2.8-2.10, as my 008 report read it. The doc credits that reading to the verifier and does not claim to have read Rev 2.12 itself. Model counts 87 + 40 + 63 = 190 match the doc's table.
- **Firmware:** `firmware/mdio_phy.pasm`'s change is comments only.
- **Rules:** nothing under `src/` or `flow/` changed.

## Note

1. `docs/bench-firmware.md` itself does not name the old `sg-bench-fw` path, so nothing needs changing in the repo. Item files should use `~/protoemu-flow/sigrok/sigrok-cli` from now on.

## What I ran

All on runner1 at f47e875. Throwaway copies were removed afterwards.

- `remote_test.sh model test_bench_fw`: `1579 passed, 28 skipped in 739.49s`, `test_bench_fw rc=0`, `TESTS=6 PASS=5 FAIL=0 SKIP=1`, as expected without sigrok. The tool now ignores `test_swd.py`.
- With sigrok: as in the table.
- `python3 test/mutants.py` with all 28 names: `28 of 28 mutants killed`, rc 0.
- The abort sweep: 2335 model runs.
