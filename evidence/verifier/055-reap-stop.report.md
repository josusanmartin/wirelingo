VERDICT: PASS

Commit 0d84715 on 9dd256e (improve-2026-11 after the reattach-wiring merge), one commit. It changes five files:
- `fpga/kv260/host/usb_fs_data_demo.py` (+37/−11);
- `fpga/kv260/host/usb_guard_run.py` (+20/−2);
- `test/model/test_usb_fs_data_demo_board.py`;
- `test/model/test_usb_guard_run.py`;
- `test/mutants.py`.

No firmware, `tools/`, `src/`, `flow/` or doc change.

The defect from my 054 is fixed. A Stop landing just after `run()` reaps the client now ends in STOPPED, rc 130 and the normal detach. In my group-signal runs, 9dd256e failed 53 of 800 and 0d84715 failed 0 of 800, interleaved under the same load. Every claim checks. The untested `:192` path works under my harness, and I don't think it needs a test before merge (note 1).

| Claim | Result |
|---|---|
| 1. `stops_held()` (`:154`): the block inside the `try`, `SIG_UNBLOCK` at the end; never nested | PASS (below; note 4 on the unblock) |
| 2. The three demo sites (`:227`, `:234`, `:168-181`) held; the tolerance at `:180` | PASS |
| 3. `Stopper.held()` (`:155`) around `:184` and `:192`; the reaped-pid SIGTERM window | PASS, reproduced at both sites |
| 4. Sites left alone, and none of their handlers signals a recorded pid | PASS for `fpga/kv260/host` (note 5 on `flow/`) |
| 5. New tests: 27 passed; against 9dd256e's code 6 failed, 21 passed, the `ProcessLookupError` signature | PASS, reproduced |
| 6. Five new mutants; 14 of 14 | PASS, reproduced; plus 7 of my own (below) |
| 7. Builder's stress counts, called weak evidence | Not rerun; my stronger runs below agree |
| 8. Model 2878 passed, 29 skipped | PASS, reproduced |

## Checked

**Claim 1, read.**
- `stops_held()` (`usb_fs_data_demo.py:154-165`) does `pthread_sigmask(SIG_BLOCK, STOP_SIGNALS)` inside `try`, and `SIG_UNBLOCK` in `finally`.
- **Why the block is inside the `try`.** CPython's `signal.pthread_sigmask` calls `PyErr_CheckSignals()` after changing the mask, so a pending handler's Stop can be raised by the call itself. When that happens the mask change has already been applied.
  - Inside the `try`, the `finally` undoes it.
  - Outside, the signals would stay blocked. In this program that happens to do no harm (my `block-outside-try` mutant, below), but the placement is right.
- **Never nested.** I checked every path into a held step:
  - The three demo sites are sequential statements. None calls another held step or `fork_client`.
  - `kill_client()` is reached only from `run()`'s `except BaseException` (`:240-241`) and `main()`'s `except Stop` (`:515`). Both run after any `with` has exited, because a `with` block's `finally` runs before the exception reaches the `except`. So `kill_client` is never entered from inside a held step.
  - `fork_client()`'s own block/SETMASK (`:257-268`) is reached only from `run()` outside any held step.
  - The child process never calls either one.
  - The guard's two `held()` steps are sequential (the second is in the `except TimeoutExpired` branch).
  - Neither file starts a thread. That matters because a mask holds off a process-directed signal only when every thread has it blocked.

**Claim 2, read.**
- At `:227-232`, the WNOHANG reap and `child = LIVE["client"] = None` are one held step. A Stop pending during it is raised at the unblock, after the pid is cleared. `kill_client` then finds nothing.
- The limit path (`:234-237`): the kill, the reap and the clear are one held step.
- `kill_client` (`:168-181`): the clear, kill and reap are held. `ProcessLookupError` and `ChildProcessError` are passed over (`:180`).
- **The old-order defect the builder names is real.** At 9dd256e, `kill_client` cleared the record first and killed second. So a Stop between the two, with `kill_client` reached from a non-Stop exception, left the client running and unrecorded. The new `test_a_stop_inside_kill_client_still_kills_the_client` fails at 9dd256e on exactly that: "the Linux client killed" is missing.

**The asked check: `v054_race.py` at 0d84715.**
- **The 054 harness as it was** (reap, then `raise Stop`, on run()'s poll):
  - 9dd256e: rc 1, `ProcessLookupError`, no detach; the last operations are `spim.pmod {0: 1, 1: 0}` and `pe.write 0x801 [257]`.
  - 0d84715: rc 130, `[stop] SIGINT: stopping`, `attach 1 … detached`, STOPPED; the last three operations are the normal detach.
- **Six variants in `v055_race.py`.**
  - **The variants.** Both reaps: run()'s poll, and the `--limit 1` kill. Each with either `raise` (a Stop raised in place) or `signal` (a real SIGINT to itself). `signal` is also run with `:hold`, which sleeps 0.3 s inside the held step.
  - **Logged.** Every kill, marked REAPED or live.

  | Variant | 9dd256e | 0d84715 |
  |---|---|---|
  | done:raise | rc 1, `ProcessLookupError`, a kill to the REAPED pid | rc 130, STOPPED, detached; one kill to the REAPED pid (tolerated: the raise path) |
  | done:signal, done:signal:hold | rc 1, `ProcessLookupError`, a kill to the REAPED pid | rc 130, STOPPED, detached; **no kill** |
  | limit:raise | rc 1, `ProcessLookupError` | rc 130, STOPPED, detached; the live kill, then one to the REAPED pid (tolerated) |
  | limit:signal, limit:signal:hold | rc 1, `ProcessLookupError` | rc 130, STOPPED, detached; only the live kill |

  - **What the `raise` rows show.** A Stop raised in place, which the mask cannot hold, still sends one SIGKILL to the reaped pid. A real Stop comes only from the handler, which the mask now holds off, so only the `signal` rows describe real runs. The `raise` rows show the tolerance working as a second line.

**The asked check: does a held Stop still arrive promptly?**
- **The demo.** In the four `signal` variants, the Stop was raised 0.04-0.09 ms after the held step's last statement. That covers the clear and the unblock, and holds even after the 0.3 s hold.
  - Every demo step is short: a WNOHANG `waitpid`, or a SIGKILL and its reap.
  - By reading, the one step that can take long is a SIGKILL'd client still in a usbfs control transfer. A Stop then arrives once the kernel lets the client die. Before this commit, `kill_client`'s wait behind such a Stop was just as long.
- **The guard.**
  - **Harness.** `v055_guard_latency.py` runs `demo()` 40 times on a fake demo. Each time another process sends the guard a SIGINT at a random moment. The fake demo records when its SIGTERM arrives.
  - **At 9dd256e:** min 0.3, median 0.4, max 0.6 ms.
  - **At 0d84715:** min 8.1, median 112.4, max 198.7 ms.
  - So the Ctrl-C is held up to the 0.2 s `wait` timeout, then handled. None was lost: every run ended rc 143, with `stopper.signal` SIGINT.

**The asked check: group runs on runner1.** This is my 049 harness (`group_runs.py`: the real fork and signals, a fake board, a SIGINT to the whole process group while the client runs). It ran 64 at a time, in four rounds of 200 per commit, alternating 9dd256e and 0d84715.

| Round | 9dd256e | 0d84715 |
|---|---|---|
| 1 | 14 rc 1 (no STOPPED), 186 rc 130 | 200 rc 130 |
| 2 | 13 rc 1, 187 rc 130 | 200 rc 130 |
| 3 | 13 rc 1, 187 rc 130 | 200 rc 130 |
| 4 | 13 rc 1, 187 rc 130 | 200 rc 130 |
| total | **53 of 800 failed** | **0 of 800** |

- At 9dd256e, every rc 1 run ended on the attached state (`spim.pmod {0: 1, 1: 0}`, `pe.write 0x801 [257]`). Every rc 130 run ended on the normal detach.
- No other pid touched the board in any of the 1600 runs.
- If 0d84715 failed at 9dd256e's rate, 0 failures in 800 would have probability about e^−53.
- The runner1 load average went from 4.7 to 39 during the runs.

**The asked check: the old-file comparison** (runner1).
- **At 0d84715:** both test files, `27 passed`.
- **With 0d84715's two test files on 9dd256e's code:** `6 failed, 21 passed`.
  - The four `test_a_stop_just_after_the_client_is_reaped` cases fail with `ProcessLookupError: [Errno 3] No such process`.
  - `test_a_stop_inside_kill_client_still_kills_the_client` fails.
  - `test_a_signal_as_the_demo_is_reaped_signals_no_reaped_pid` fails.

**Claim 3, reproduced at both sites.**
- **`:184`** is covered by the new guard test, which fails at 9dd256e.
- **`:192`** has no test. My `v055_guard_kill.py` calls `demo()` with a 0.6 s timeout on a demo that sleeps 30 s. That reaches `proc.kill(); proc.wait()`, and a SIGINT is sent just after that wait's reap.

  | Code | Kills seen after `reaped <pid>` | rc |
  |---|---|---|
  | 9dd256e | `kill <pid> 15` to the reaped pid | 124 |
  | 0d84715 | none | 124 |
  | 0d84715 with that `held()` removed | `kill <pid> 15` | 124 |

- By reading Python 3.12's `Popen`:
  - `poll()` returns None while `wait()` holds `_waitpid_lock`, and `send_signal` swallows `ProcessLookupError`. So the claim's description of the window, and of the harm being only to a reused pid, is right.

**Claim 4, completeness.**
- **Every signal handler in `fpga/kv260/host` either raises or is ignored, and none signals a pid:**
  - `eth_tap.py:384-387` and `:672-675`;
  - `hw_day.py:466-469`;
  - `board_hold.py:23` (SIG_IGN);
  - and the demo's and guard's own handlers.
- **Every pid kill outside tests,** from `git grep` for `os.kill`, `os.killpg`, `os.waitpid`, `.send_signal`, `.terminate()` and `.kill()`:
  - in `fpga/kv260/host`, either the builder's list or one of the changed sites;
  - the Popen ones (`eth_tap.py:420`, `:498`, `hw_day.py:1087`, `Tracer.stop` `:91`) go through `send_signal`, which polls first and swallows ESRCH;
  - the two in `flow/` are in note 5.

**Mutants** (runner1, a git clone of 0d84715).
- **The package mutants:** `test/mutants.py "hw-demo-*" "hw-guard-run-*"` gives `14 of 14 mutants killed`. As claimed:
  - `hw-demo-reap-unheld` dies on `[signal-done]`;
  - `-limit-reap-unheld` on `[signal-limit]`;
  - `-kill-client-unheld` on `inside_kill_client`;
  - `-kill-client-intolerant` on `[raise-done]`;
  - `hw-guard-run-wait-unheld` on the new guard test.
- **My own** (`r1_mut.py`, each in a fresh copy, both test files):

  | Mutant | Result |
  |---|---|
  | `reap-clear-outside`: only the WNOHANG `waitpid` held, the clear after the step | KILLED, `[signal-done]` |
  | `limit-clear-outside`: the limit path's clear after the step | KILLED, `[signal-limit]` |
  | `never-unblock`: the demo's `finally` blocks again | KILLED, `test_a_group_signal_reaches_only_the_demo_on_the_board` |
  | `guard-never-unblock`: the guard's `finally` blocks again | KILLED, `test_a_signal_stops_the_run_cleanly[SIGTERM-False]` |
  | `block-outside-try`: the block before `try` | SURVIVED (note 3) |
  | `kill-client-clear-after`: `kill_client` clears after its kill and reap, still held | SURVIVED (note 3) |
  | `guard-kill-unheld`: `:192`'s `held()` removed | SURVIVED, as the builder says (note 1) |

**Regression, locally.**
- `v048_fakeboard.py`'s five scenarios at 0d84715 are unchanged from 052 and 054:
  - normal: 0, PASS;
  - run-term: 143, `attach 1 … detached`;
  - wiring-term: 143, `attach 1: stopped before its device software started; every PMOD pin disabled, the cores stopped`;
  - reattach-term: 143, `attach 2: stopped before …; detached`;
  - identity-term: 143, `no attach; the board not touched`.
  - There were 0 board operations by another pid.
- `v052_order.py`'s board-operation logs (two attaches 160 ops, three attaches 239, no pull-up at attach 1 70) are identical (`cmp`) at 9dd256e and 0d84715.

**Timing check against the ISA:** not applicable; host scripts only.

## Notes

1. **The untested `:192` path doesn't need a test before merge, in my view.**
   - **Shown working.** The hold there is shown working by `v055_guard_kill.py`, and removing it shows the reaped-pid SIGTERM again.
   - **The window is narrower than at `:184`.** In the 60 s give-up branch, `told` is set, so the handler has already run once and does nothing more. The window exists only in the timeout branch (rc 124), and only for a Ctrl-C landing within microseconds of that reap.
   - **The harm** is a SIGTERM to a reused pid, the same as the tested site.
   - **A test is cheap.** The guard test's `REAP_DRIVER` with `timeout` 0.6 and a demo that sleeps takes about 1 s. A mutant like `guard-kill-unheld` would then die. Worth adding with the next guard change.

2. **The guard now takes up to 0.2 s to pass a Ctrl-C on** (median 112 ms, previously under 1 ms). That is the cost of holding the signals across `proc.wait(timeout=0.2)`. It is prompt for a person at a terminal, and nothing is lost. The demo's held steps add under 0.1 ms.

3. **Two of my mutants survive, and both are harmless while the mask holds.**
   - **`block-outside-try`.** A Stop raised by the block call leaves the signals blocked. But every path after it goes through another `stops_held()`, whose `finally` unblocks: the Stop from `kill_client` reaches `main()`'s `kill_client`, and the Stop from run()'s step reaches run()'s `kill_client`. So no test can see it.
   - **`kill-client-clear-after`.** The order inside a held step makes no difference to a signal. It matters only for a Stop raised in place, which only the test wrapper can do.
   - The builder's placements are the safer ones. No test change is needed.

4. **The end unblocks rather than restores.** That is safe because the steps never nest. But it also means that if the demo were started with these signals already blocked in its inherited mask, the first held step would unblock them. That would change things for the better: a stop would then work.
   - None of the launchers does this. The guard's `Popen` (`usb_guard_run.py:176`) is outside `held()`.
   - The docstring's "never nested, so the end unblocks rather than restoring" is accurate.

5. **Claim 4's list covers `fpga/kv260/host`. `flow/` has two more sites of the same class.**
   - **The sites.** `flow/gl_module_test.py:184-192` and `flow/release_evidence.py:244-259` call `os.killpg(proc.pid, …)` on Popen pids from the exception path. Their SIGTERM handlers raise KeyboardInterrupt.
   - **The window.** A KeyboardInterrupt just after `poll()` has reaped a job leaves it in `running`. Then `killpg` goes to that pid's group.
   - **Why it matters little.** ESRCH is tolerated, and the harm needs a reused pgid. `flow/` files can't be edited under RULES.md.
   - **Why it's outside this item.** These are not board or demo paths.

## What I ran

**Locally** (`git archive` trees of 9dd256e and 0d84715, and a mutated copy, in my scratchpad):
- the diff;
- `v054_race.py` at both commits;
- `v055_race.py`'s six variants at both;
- `v055_guard_latency.py`, 40 runs at each;
- `v055_guard_kill.py` at both and with `:192`'s hold removed;
- `v048_fakeboard.py`'s five scenarios at 0d84715;
- `v052_order.py` at both;
- the `git grep` for signal handlers and pid kills.

**On runner1:**
- in `~/verifier-055/` (git clones of 0d84715 and 9dd256e):
  - both test files at 0d84715;
  - both new test files on 9dd256e's code;
  - `test/mutants.py "hw-demo-*" "hw-guard-run-*"`;
  - `r1_mut.py`'s seven mutants;
  - `group_runs.py`, 4 × 200 per commit at 64 at a time.
- `remote_test.sh model` at 0d84715 (`--ignore=test/model/test_swd.py`): `model rc=0`, `2878 passed, 29 skipped in 572.64s (0:09:32)` (checkout `agent-runs/protoemu-verify-0d84715-487244`, removed).

My runner1 scratch is removed. No SWD test ran, I did not touch the Kria, and nothing was pushed.
