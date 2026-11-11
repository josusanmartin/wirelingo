VERDICT: PASS

Commit cb93530 on 9ca49ef (improve-2026-11 after the demo-notes merge), one commit. It changes three files:
- `fpga/kv260/host/usb_fs_data_demo.py` (+8/−1);
- `test/model/test_usb_fs_data_demo_board.py`;
- `test/mutants.py`.

No firmware, `tools/`, `src/`, `flow/` or doc change.

052 note 1 is fixed. A WiringError at a re-attach now ends as one at attach 1 does: rc 2 and `FAIL (wiring)`. The board operations are 9ca49ef's plus one `spim.pmod {}`. Every claim checks.

Rerunning the group-signal runs, as asked, found **a defect in the stop path that is older than this commit** (below). It is in code cb93530 does not touch. It reproduces the same way at 9ca49ef, and the merge does not make it worse. So it does not block this commit, but it needs its own item before the next board run with Ctrl-C or hangup stops.

| Claim | Result |
|---|---|
| 1. The handler at `:470-478`, `except usb_demo.WiringError` at `:473`, mirroring `:463-466`, plus a `[device software]` line naming the attach | PASS |
| 2. Board end state unchanged; the one extra op a redundant `spim.pmod {}` | PASS (operation logs below) |
| 3. Attach 1's wiring path still has no `[device software]` line | True; it doesn't matter (below) |
| 4. `usb_guard_run.py` needs no change; the two summaries now identical, attach 2 gave rc 1 before | PASS, reproduced |
| 5. New test (`:228`), `NO_PULLUP` (`:77-85`), `pmod_in() = 0` (`:64`); `1 failed, 9 passed` against 9ca49ef's demo | PASS, reproduced |
| 6. Mutant `hw-demo-reattach-wiring-escapes` (`mutants.py:245`); 9/9 | PASS |
| 7. Board test 10 passed; model 2872/29 | PASS (model below) |

## Defect found (older than this commit)

1. **A stop that lands just after `run()`'s `waitpid` has reaped the client exits rc 1 with a traceback, and the board is not detached.**
   - **Where.** `usb_fs_data_demo.py:204-206`:
     - `pid, st = os.waitpid(child, os.WNOHANG)` reaps the client;
     - only then does `child = LIVE["client"] = None` run.
   - **What goes wrong.**
     - A Stop raised between the two leaves the reaped pid in `LIVE["client"]`.
     - `run()`'s `except BaseException` calls `kill_client()`, and `os.kill(child, 9)` (`:156`) raises `ProcessLookupError`.
     - That replaces the Stop, and `main()` catches only `Stop`. So the demo exits rc 1 with a traceback and no STOPPED, without killing anything and without `usb_demo.detach`.
     - The board is left as attached: core 0 running and the USB lines mapped. The last operations are `spim.pmod {0: 1, 1: 0}` and `pe.write 0x801 [257]`.
   - **When it happens.** A group signal kills the client, whose handlers are back at SIG_DFL, at the same moment it reaches the parent. The parent's poll then reaps the dead child before Python runs the handler. That is exactly the terminal Ctrl-C or ssh hangup case 048/049 were about.
   - **Shown three ways.**
     - **Load-dependent:** `group_runs.py` (my 049 harness, the real fork and signals, a fake board):
       - locally, under load average 19: 9 of 60 at cb93530; then 4 of 48 at 9ca49ef and 1 of 48 at cb93530 (16 at a time);
       - on runner1: 0 of 200 at 12 at a time, but 23 of 400 at 64 at a time.
       - Every failure is `ProcessLookupError`, with the Stop at `:204`. No other pid touched the board in any run.
     - **Deterministic:** `v054_race.py` wraps `os.waitpid` to reap the client and then raise `Stop("SIGINT")`. It gives rc 1, `ProcessLookupError` and no detach at both 9ca49ef and cb93530.
   - **The same window elsewhere.** It is also in the limit path (`:209-211`: kill, reap, then clear).
   - **Pid reuse.** If the reaped pid were reused before `kill_client` ran, the SIGKILL would hit an unrelated process.
   - **Why 049 and 052 missed it.** Their 260 runs each were at 8 and 12 at a time, on a lightly loaded machine, where the window was never hit.
   - **Fix.**
     - Block `STOP_SIGNALS` with `pthread_sigmask` around `waitpid` and the clearing of `LIVE["client"]` (both paths). Then a reaped pid is never left recorded.
     - Also, or instead, have `kill_client` treat `ProcessLookupError` and `ChildProcessError` as already gone. That alone leaves the pid-reuse risk.
   - **The test.** A test can use the same reap-then-Stop wrapper and assert STOPPED, 130 and the normal detach.

## Checked

**The handler, read.**
- `:470-478` puts the re-attach's `attach()` in `try`.
- `except usb_demo.WiringError` (`:473`) does `spim.pmod({})`. It prints the error, the `[device software] attach N: its wiring check failed …` line and `FAIL (wiring): …`, then calls `sys.exit(2)`, as `:463-466` does.
- `SystemExit` passes through `except Stop`, so the post-try detach is not run. That is right here: `detect` (`usb_demo.py:95-123`) has just ended with every pin off, after `attach()`'s `pmod({})`, `board.reset()` and `pe.stop(3)`.

**The asked check: `v052_order.py` at 9ca49ef and cb93530.** It logs every board operation, with the real `detect` and `attach()`.

| Run | 9ca49ef | cb93530 |
|---|---|---|
| two attaches (1 unconfigured, 2 configured) | 160 ops, rc 0, PASS | identical (`cmp`) |
| three attaches, none configured | 239 ops, rc 1, FAIL | identical |
| no pull-up at attach 1 | 70 ops, rc 2, FAIL (wiring) | identical |
| no pull-up at attach 2 (the re-attach) | 145 ops, rc 1, traceback | the same 145, then `spim.pmod {}` and rc 2 |

- In the re-attach run, `diff` shows only the added `spim.pmod {}`, plus the harness's own `exit 2` record of the `SystemExit`.
- The stdout ends with the error, `[device software] attach 2: its wiring check failed before its device software started; every PMOD pin disabled, the cores stopped` and `FAIL (wiring): every PMOD pin disabled, the core not started`.

**Claim 4, reproduced.**
- **Harness.** `v054_guard.py` runs `usb_guard_run.main(["--model", ...])` with `demo()` replaced. The attach run passes, and the zero-length step returns the recorded demo output and rc.
- **At cb93530,** the attach-1 and attach-2 wiring failures give identical summaries apart from the log folder: `[zero-length writes] no NODATA line (not configured?); demo rc 2`, then FAIL.
- **At 9ca49ef,** attach 2 gives `demo rc 1`.
- **By reading,** the guard classifies only by rc (`:281-282`) and `nodata_summary` (`:321-323`). `nothing_seen` (`:186-188`) uses "not configured" and "requests seen: []", and nothing reads `[device software]` or `FAIL (wiring)`.

**Claim 3: does the asymmetry matter?**
- No program reads the line, so classification is the same either way.
- A reader of the log sees `FAIL (wiring)` with the error in both cases.
- The only difference: attach 1's wiring failure has no `[device software]` line, while a stop during attach 1's wiring check does have one (`attach 1: stopped before …`).
- It is cosmetic, and the attach number is unambiguous there, since only attach 1 can fail before `[service]`.

**The asked check: the old-vs-new comparison** (runner1).
- **Result:** cb93530's test file with 9ca49ef's demo gives `1 failed, 9 passed`.
- **The failure:** `test_a_wiring_failure_on_a_re_attach_reports_as_on_the_first`, ending in `usb_demo.WiringError: no pulled-up pin …`, raised from the re-attach at 9ca49ef's `:471`.

**The asked check: does the `pmod_in() = 0` default change an existing test?**
- No. In `test_usb_fs_data_demo_board.py`, `pmod_in` is reached only through the real `usb_demo.detect` (`usb_demo.py:102`). The driver replaces `detect` and calls the real one only when `NO_PULLUP` names that check (`:84-85`), and no other test sets `NO_PULLUP`.
- Before this commit, `FakeSpim` had no `pmod_in`, so any call would have been an AttributeError. No existing test could have been relying on one.
- `test_hw_day.py` has its own fake.

**The test, read.** The 46-operation tail is:
- `attach()`'s `pmod {}`, `reset` and `stop 3`;
- the `detect 2` note and `POUT` 0;
- 4 pins × 5 pulses × (`{q: 8}`, `{}`);
- the handler's `pmod {}`.

That matches my operation log at cb93530. It also asserts:
- rc 2, no traceback or STOPPED;
- `FAIL (wiring)` last;
- "attach 1 of 2: not configured";
- one `[device software]` line;
- only the demo's pid on the board.

**The asked check: `v048_fakeboard.py`'s five scenarios at cb93530** (locally). They are unchanged from 052:

| Scenario | Result |
|---|---|
| normal | 0, `attach 1 … detached`, PASS |
| run-term | 143, `attach 1 … detached`, STOPPED |
| wiring-term | 143, `attach 1: stopped before its device software started; every PMOD pin disabled, the cores stopped`; ops `pmod {}`, `stop 3` |
| reattach-term | 143, `attach 2: stopped before its device software started; detached` |
| identity-term | 143, `no attach; the board not touched` |

There were 0 board operations by another pid. The group runs are in the defect above.

**Mutants and tests** (runner1, a git clone of cb93530).
- `test/mutants.py "hw-demo-*" "hw-guard-run-*"`: `9 of 9 mutants killed`. `hw-demo-reattach-wiring-escapes` is killed by the new test in 2 s.
- `test_usb_fs_data_demo_board.py`: `10 passed` three times out of three.
- `test_usb_guard_run.py`: `11 passed`.

**Timing check against the ISA:** not applicable; host scripts only.

## Notes

1. **A stop landing inside the new handler** (between `except` and `sys.exit(2)`, a few statements) would reach `except Stop` with attach 1's `order`. It would then run the normal detach, driving pmod 0 from uo0 after a wiring check that just failed. The window is a few statements wide and I didn't hit it.

   The same is true by design of a stop during a re-attach's wiring check (052's `reattach-term`). There, the detach uses the wiring attach 1 verified, which a failed or interrupted second check no longer vouches for. Within one run the wiring doesn't change, so this is a note.

## What I ran

**Locally** (`git archive` trees of 9ca49ef and cb93530 in my scratchpad):
- the diff;
- `v052_order.py`'s four runs at both commits;
- `v054_guard.py` on the four recorded outputs;
- `v048_fakeboard.py`'s five scenarios at cb93530;
- `group_runs.py`, 60 runs at cb93530;
- 48 group runs at each commit, 16 at a time;
- `v054_race.py` at both.

**On runner1:**
- in `~/verifier-054/` (a git clone):
  - the new test file with 9ca49ef's demo;
  - the board test ×3;
  - `test_usb_guard_run.py`;
  - the mutants;
  - `group_runs.py` 200 runs at 12 at a time and 400 at 64 at a time;
- `remote_test.sh model` at cb93530 (`--ignore=test/model/test_swd.py`): `model rc=0`, `2872 passed, 29 skipped in 453.87s (0:07:33)` (checkout `agent-runs/protoemu-verify-cb93530-3601847`, removed).

My runner1 scratch is removed. No SWD test ran, I did not touch the Kria, and nothing was pushed.
