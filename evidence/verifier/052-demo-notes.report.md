VERDICT: PASS

Commit 2a5677d on d7f177d (improve-2026-11 after the usb-in-setup merge), one commit. It changes three files:
- `fpga/kv260/host/usb_fs_data_demo.py`;
- `test/model/test_usb_fs_data_demo_board.py`;
- `test/mutants.py`.

No firmware, `tools/`, `src/`, `flow/` or doc change.

Both of 049's notes are dealt with.
- **Note 1.** A stop during a re-attach now names that attach. The board operations on a normal two-attach run are byte-for-byte the same as before.
- **Note 2.** The child's `try/finally` is now pinned by a test that kills its removal.
- **Group signals.** None reaches the board from another pid in 260 runs.

| Claim | Result |
|---|---|
| 1. Re-attach moved to the start of iteration k+1, same board order; `LIVE["sw"] = n, sw` in one assignment, dropped when `n != attempt`; new report line; one 049 line changed, end states unchanged | PASS (fake-board logs identical; the five scenarios below) |
| 2. `test_a_client_that_raises_exits_1_and_leaves_the_board_alone[no-device,stop]` (`:224`) | PASS |
| 3. `test_a_stop_during_a_wiring_check_names_that_attach[1,2]` (`:202`); `2 failed` against d7f177d's demo, n=2 printing `attach 1: … detached` | PASS, reproduced |
| 4. Mutant `hw-demo-client-exception-escapes` (`mutants.py:232`); 8/8; by hand, the mutated child writes the cleanup and prints STOPPED | PASS, reproduced |
| 5. Board test 9 passed ×3; `test_usb_guard_run.py` 11 passed; model 2871/29 | PASS (model below) |

## Checked

**The code, read** (`usb_fs_data_demo.py`).
- **The re-attach.** It runs at `:470-471`, `if attempt > 1: prog, order = attach(...)`, before `run(..., attempt)` (`:472`). The old code ran it at the end of iteration k under `if attempt < a.attaches`. Nothing runs between the end of one iteration and the start of the next, and both forms skip it after the last attach and after a `break`. So the sequence is the same.
- **The stored software.** `run()` builds the `LoopbackDevice` and then stores `LIVE["sw"] = n, sw` (`:181-182`).
  - `except Stop` takes `LIVE["sw"] or (0, None)` and drops `sw` when its number is not `attempt` (`:483-485`).
  - `attempt = 1` is set just before the first wiring check (`:460`), and it is 0 before that.
  - The three report lines (`:496-501`):
    - a stop inside `run()` of attach k reports k's software;
    - a stop during attach k's set-up reports "attach k: stopped before its device software started";
    - a stop before `[wiring]` reports "no attach".
- **`LIVE["sw"]`** has no other reader, and `run()` has no other caller (`git grep`).

**The asked check: does the move keep the board's operation order?**
- **The harness.** `v052_order.py` is my fake KV260. It logs every board operation in order: every `pe.read` and `pe.write` with its words, `pmod`, `pmod_in`, `reset`, `stop` and `select`. It runs the real `usb_demo.detect` against a fake pull-up on D+, and the real `attach()`, firmware assembly and load.
- **The trees.** I ran it on `git archive` trees of d7f177d and 2a5677d.

| Run | d7f177d | 2a5677d | Logs |
|---|---|---|---|
| two attaches: attach 1 never configured, attach 2 configured, client 0 | 160 operations, rc 0, PASS | 160, rc 0, PASS | `cmp` identical; stdout identical |
| three attaches, none configured | 239 operations, rc 1, FAIL | 239, rc 1, FAIL | `cmp` identical; stdout identical |

**The asked check: my 049 `v048_fakeboard.py` scenarios, at d7f177d and 2a5677d (locally).**

| Scenario | d7f177d | 2a5677d (rc, `[device software]` line, last board operations) |
|---|---|---|
| normal end | 0, `attach 1 … detached`, PASS | the same |
| SIGTERM during the client | 143, `attach 1 … detached`, STOPPED | the same |
| SIGTERM in the first wiring check | 143, `no attach; every PMOD pin disabled, the cores stopped` | 143, `attach 1: stopped before its device software started; every PMOD pin disabled, the cores stopped`; ops `pmod {}`, `stop 3` as before |
| SIGTERM in the second attach's wiring check | 143, `attach 1: 0 requests … detached` (049 note 1) | 143, `attach 2: stopped before its device software started; detached`; ops the normal detach, as before |
| SIGTERM in `Lab.identity` | 143, `no attach; the board not touched` | the same, no traceback |

Board operations by another pid: 0 in every scenario at both commits. So the only changed lines are the two the item names.

**The asked check: `group_runs.py` at 2a5677d.**
- Locally, 60 runs, 8 at a time: `60 runs: rc 130, board ops by another pid 0, STOPPED printed True`.
- On runner1, 200 runs, 12 at a time: `200 runs: rc 130, board ops by another pid 0, STOPPED printed True`.
- In all 260, the demo's last board operations are `pe.write 0x809 0; spim.pmod {0: 8}; pe.stop 1`.

**The asked check: d7f177d vs 2a5677d** (runner1). This is 2a5677d's test file with d7f177d's `usb_fs_data_demo.py`.
- **Result:** `2 failed, 7 passed`.
- **The failures:** `test_a_stop_during_a_wiring_check_names_that_attach[1]` and `[2]`.
  - n=1 printed `no attach; every PMOD pin disabled, the cores stopped`;
  - n=2 printed `[device software] attach 1: 0 requests, 0 packets re-staged; detached`.

**The tests, read.**
- **Output capture.** `start()` merges stderr into stdout (`stderr=subprocess.STDOUT`), so `"Traceback" not in out` is a real check.
- **The wiring-check test** asserts:
  - rc 143, STOPPED, no traceback;
  - exactly one `[device software]` line, naming attach n;
  - "attach 1 of 2: not configured" printed only for n=2;
  - only the demo's pid on the board;
  - the last three operations.
- **The raising-client test.**
  - `no-device` runs the real `client()` with `usb_stress.find` returning None, as the real `find()` (`usb_stress.py:33-44`) does when no 1209:5634 is present. `os.open(None)` then raises TypeError.
  - It asserts:
    - the child's exit 1, through a patched `waitpid` in the parent;
    - FAIL last, with no traceback or STOPPED;
    - one `client start`, from a pid other than the demo's;
    - only the demo's pid on the board;
    - the normal detach.

**Mutants** (runner1).
- **The builder's set.** `test/mutants.py "hw-demo-*" "hw-guard-run-*"`: `8 of 8 mutants killed`. `hw-demo-client-exception-escapes` is killed by `test_a_client_that_raises_exits_1_and_leaves_the_board_alone[no-device]`.
- **The same mutation by hand,** in a copy, with both cases run.
  - **`no-device`** fails on the child's traceback only, as the builder's caveat says.
  - **`stop`** fails on STOPPED. Its board log has two pids: the mutated child (4 operations) writes `pe.write 0x809 0; spim.pmod {0: 8}; pe.stop 1`, and then the demo writes the same. That is claim 4's description, and the pid assertion would also catch it.

**Claim 5** (runner1, in a git clone of 2a5677d).
- `test_usb_fs_data_demo_board.py`: `9 passed` three times out of three.
- `test_usb_guard_run.py`: `11 passed`.
- `remote_test.sh model`: below.

**The builder's caveat: a WiringError during a re-attach.** Shown with `v052_order.py`, with the pull-up gone at the second wiring check.
- **The path.** The re-attach's `attach()` starts with `spim.pmod({})`, `board.reset()` and `pe.stop(3)` (`:56-58`). `usb_demo.detect` then writes `POUT` 0 and pulses each pin, each pulse followed by `pmod({})`, and raises `WiringError` (`usb_demo.py:95-123`). It is not caught: the `except usb_demo.WiringError` at `:463` covers only the first attach, and `main()` catches only `Stop`.
- **What it leaves.**
  - The board is left reset, with both cores stopped and every PMOD pin disabled. The last operation is `spim.pmod {}`. That is the end state of the first attach's `WiringError` path (`pmod({})`, core not started).
  - No Linux client is alive: `run()` returns on a timeout only when no client was forked (`:200-202`).
- **What differs** from the first attach's path is the report: exit 1 with a traceback, instead of `FAIL (wiring)` and exit 2, and no `[device software]` line.
- **It is older than this commit.** d7f177d does exactly the same: its operation log is `cmp`-identical to 2a5677d's.
- **So it is a note, not a defect** (note 1).

**Timing check against the ISA:** not applicable; host scripts only.

## Notes

1. **A WiringError during a re-attach ends in a traceback.** The board is left safe: reset, cores stopped, every PMOD pin disabled. But the run ends with exit 1 and no `FAIL (wiring)` or `[device software]` line, so the guard records it as an ordinary failure. Catching `WiringError` around the re-attach the same way as around the first attach would make the two the same. The same behaviour is at d7f177d.
2. **One of the two raising-client cases kills the new mutant only through the traceback.** That is `no-device`, as the builder says. The `stop` case also kills it, with the child's own board writes, which is the property that matters.

## What I ran

**Locally** (`git archive` trees of d7f177d and 2a5677d in my scratchpad):
- the diff;
- `v052_order.py`: the two-attach and three-attach logs, and the re-attach `WiringError`, at both commits;
- `v048_fakeboard.py`'s five scenarios at both;
- `group_runs.py`, 60 runs at 2a5677d.

**On runner1:**
- in `~/verifier-052/` (a git clone):
  - the new test file with d7f177d's demo;
  - `test_usb_fs_data_demo_board.py` ×3;
  - `test_usb_guard_run.py`;
  - `test/mutants.py "hw-demo-*" "hw-guard-run-*"`;
  - the hand-applied mutant's two cases;
  - `group_runs.py`, 200 runs;
- `remote_test.sh model` at 2a5677d (`--ignore=test/model/test_swd.py`): `model rc=0`, `2871 passed, 29 skipped in 442.84s (0:07:22)` (checkout `agent-runs/protoemu-verify-2a5677d-2957216`, removed).

My runner1 scratch is removed. No SWD test ran, I did not touch the Kria, and nothing was pushed.
