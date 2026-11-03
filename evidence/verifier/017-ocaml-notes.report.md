VERDICT: FAIL

Commit 7dba86e, base cca723c. The new dune rule works: I could not promote a failing `[%test_result]` past it. The doc notes from 013 are all fixed. But claim 1's second half is false. The case `self_check.sh` adds cannot tell whether the rule exists, because its planted bug still fails `dune test` after promotion with the rule deleted. The fix is one line.

| Claim | Result |
|---|---|
| 1a. A dune rule fails `dune test` on any promoted exception | PASS: 6 of 6 promoted bugs still fail |
| 1b. `self_check.sh` confirms it | **FAIL**: its case passes with the rule removed |
| 2. `log2:2` against the tapeout's 8; 7 tests; corpus 287 of 1410 | PASS |

## Finding

1. `ocaml/test/self_check.sh:72`: the `promote` case uses the `pe_fifo` bug `do_push = i.push`. After `--auto-promote` that one keeps failing for some other reason, rule or no rule.

   I took each RTL bug in `self_check.sh`, promoted it, then built `@wave_test/runtest` with the rule and again with the rule cut out of `wave_test/dune`:

   | Planted bug | Records written by promote | With the rule | Rule removed |
   |---|---|---|---|
   | `pe_fifo` `do_push = i.push` (the self-check's choice) | 4 | fails | **fails** |
   | `pe_fifos` join ignores RXF full | 2 | fails | passes |
   | `pe_line` `spb` fixed at 1 | 2 | fails | passes |
   | `pe_line` `n_trunbit` not inverted | 2 | fails | passes |
   | `pe_line` edge ages swapped | 2 | fails | passes |

   So `caught=yes` on that line would print even if someone deleted the rule. The last four rows show what the rule really protects. Fix: use one of those four in the `promote` case, for example the `pe_fifos` join bug. Better, also run the case once with the rule removed and require it to pass then.

## What held

- **Claim 1a:** in all six runs the promoted file held `[@@expect.uncaught_exn]` records. `dune build @wave_test/runtest` and `dune test` from `ocaml/` both failed with the rule, and both passed without it in the four rows above. The grep covers `uncaught_exn` and `expect.unreachable`. Expect tests live only in `test_*.ml`: `bench.ml` has none, so the glob misses nothing.
- **`self_check.sh` alone:** rc 0, 15 `caught=yes`, `missed: 0`, including `promote ... caught=yes`. It also restores the test files afterwards: a clean build after my runs passed.
- **Claim 2, against my 013 notes:**
  - `docs/hardcaml-port.md` now says the assertion fails the test and that promotion would write an exception record.
  - It says 7 tests, 2 and 5.
  - It says `log2:2`, 4 deep against the tapeout's 8, with 16 joined at the tapeout's depth not tested. The test's name was changed to match.
  - The corpus line reads "287 distinct of 1410".
  - The doc says the promote case "was run alone, not in a whole `evidence.sh`". That is true, and I ran it the same way.
- **The doc's limit is right:** a waveform change that no assertion covers can still be promoted, and the doc says so.

## What I ran

On runner1 at 7dba86e in `verifier-017/`, since removed. Toolchain by `ocaml/scripts/setup_runner.sh`, rc 0, dune 3.24.2.

- `dune build` rc 0. Clean `@wave_test/runtest` rc 0.
- `ocaml/test/self_check.sh`: rc 0, 15 caught, `missed: 0`.
- The table above: 6 promotions with and without the rule, in a separate copy from the self-check run.
