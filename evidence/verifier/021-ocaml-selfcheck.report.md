VERDICT: PASS

Commit 88ab957, base 7dba86e. 017's finding is fixed. The `promote` case now uses the `pe_fifos` join bug and requires both halves: fail with the rule, pass with it cut out. With the rule deleted from the commit, `self_check.sh` reports that case missed and exits 1.

| Run | Promote case | Result |
|---|---|---|
| As committed | `with the rule: fail; rule removed: pass`, `caught=yes` | 15 caught, `missed: 0`, rc 0 |
| Rule deleted from `ocaml/wave_test/dune` and committed in a throwaway clone, since `self_check.sh` archives HEAD | `with the rule: pass; rule removed: pass`, `caught=no` | 14 caught, `missed: 1`, rc 1 |

## Note

1. `ocaml/test/self_check.sh:42-46`: with no `(rule` in the dune file, the embedded Python raises `ValueError: substring not found` and prints a traceback. The case still ends `caught=no`, which is the right answer, but a reader sees a crash. Have the script print "no rule in wave_test/dune" and count the case as missed explicitly.

## What I ran

On runner1 at 88ab957, in `verifier-021/`, since removed. Toolchain by `ocaml/scripts/setup_runner.sh`, rc 0.

- `ocaml/test/self_check.sh`, as committed: rc 0, 15 `caught=yes`, `missed: 0`.
- The same with the rule deleted (local commit `f1cad46` in the throwaway clone, not in the repo): rc 1, `missed: 1`, and only the promote case is `caught=no`.
