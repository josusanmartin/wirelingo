VERDICT: PASS

Commit 0135ca2 on 9baebb7 (improve-2026-11, the usb-ls-restage merge). Two commits:
- **ac3e72e:** e158fe0 rebased. `git range-diff ff9fecb..e158fe0 9baebb7..ac3e72e` gives `1: e158fe0 = 1: ac3e72e`, so it is unchanged.
- **0135ca2:** `usb_fs_data_demo.py`, `usb_guard_run.py`, the new `test_usb_fs_data_demo_board.py`, two mutants and the kv260 README.

No firmware, `tools/`, `src/` or `flow/` change.

048's finding is fixed. In 260 group-signal runs (60 locally and 200 on runner1), no process but the demo touched the fake board. In 048 the same harness found the client writing in 10 of 20 runs locally and 2 of 20 on runner1.

| Claim | Result |
|---|---|
| 1. Signals blocked across the fork; the child resets them to SIG_DFL, unblocks, leaves only by `os._exit`; the guard starts the demo, modprobe, the usbmon cat and both journalctl calls in their own session | PASS |
| 2. The pid recorded before the unblock; `run()` and `main()` kill a recorded client; mutant `hw-demo-client-recorded-late` | PASS |
| 3. `test_usb_fs_data_demo_board.py`: only the demo's pid on the board, for five cases; two mutants | PASS (note 2 on what it logs) |
| 4. 048 notes 1 and 3: any stop gives STOPPED and 128+n; the stopped attach is named | PASS, with one naming slip (note 1) |
| 5. Model 2863 passed, 29 skipped; 7/7 mutants | PASS |

## Checked

**The fork, read** (`usb_fs_data_demo.py:220-241`).
- **`fork_client`** blocks SIGINT, SIGTERM and SIGHUP with `pthread_sigmask` and forks.
  - The **child** sets the three back to `SIG_DFL` and restores the mask. A pending signal then kills it with the default action, and no Python handler runs. It calls `client()` inside `try: os._exit(client(...)) finally: os._exit(1)`.
  - The **parent** stores the pid in `LIVE["client"]` (`:239`) and then unblocks, so a `Stop` raised by a pending signal at the unblock still finds the client. `run()`'s `except BaseException` and `main()`'s `except Stop` both call `kill_client()`.
- **Why the mask holds.** It is only reliable with one thread: 039 found a process signal reaching an unmasked thread under pytest-xdist. No module on this path starts a thread (no `threading` or `Thread(` in `usb_fs_data_demo`, `usb_demo`, `usb_stress`, `pe_spim`, `pe_kria`, `pe_lab`, `pe.usbfsdata` or `pe.usbdev`), so the demo's only thread is the masked one.
- **The guard.** The demo `Popen` (`usb_guard_run.py:162`), `modprobe` (`:72`), the usbmon `cat` (`:85`) and both `journalctl` calls (`:95`, `:195`) pass `start_new_session=True`.

**The asked check: my `v048_fakeboard.py`, rerun at 0135ca2.** It is the demo's real board path on a fake board that logs each operation with its pid, with real signals and a real fork.
- **The process-group SIGINT** (`os.setpgrp()`, then `os.killpg(0, SIGINT)` while the client runs), counted by `group_runs.py`:
  - locally, 60 runs, 8 at a time: `60 runs: rc 130, board ops by another pid 0, STOPPED printed True`;
  - on runner1, 200 runs, 12 at a time: `200 runs: rc 130, board ops by another pid 0, STOPPED printed True`.

  In every run the demo's last board operations are the normal end's: `pe.write 0x809 0; spim.pmod {0: 8}; pe.stop 1`.
- **The other five scenarios, locally:**

| Scenario | Last board operations | rc, report line |
|---|---|---|
| normal end | `0x809 0`, `pmod {0: 8}`, `stop 1` | 0, `attach 1 … detached`, PASS |
| SIGTERM to the demo during the client | the same | 143, `attach 1 … detached`, STOPPED |
| SIGTERM during the first wiring check | `pmod {}`, `stop 3` | 143, `no attach; every PMOD pin disabled, the cores stopped`, STOPPED |
| SIGTERM during the second attach's wiring check | `0x809 0`, `pmod {0: 8}`, `stop 1` | 143, `attach 1 … detached` (note 1), STOPPED |
| SIGTERM in `Lab.identity` (before `[wiring]`) | none | 143, `no attach; the board not touched`, STOPPED, no traceback |

**The asked check: do the pid assertions cover every board-writing path?**
- **What the test logs.** `test_usb_fs_data_demo_board.py`'s fake board logs `pe.write`, `pe.push`, `pe.stop`, `spim.pmod` and `board.reset`. Those are every write the cleanup paths make: `usb_demo.detach` is `POUT`, `pmod` and `stop`; the early stop is `pmod({})` and `stop(3)`. They are also every write in `attach()` and in the device software's service.
- **What it doesn't log:** `spim.select`, `Lab.mode` and reads. Those happen only before the first attach in `main()`, which the child no longer reaches.
- **The cases** (group SIGINT; a stop before the wiring check; SIGTERM and SIGHUP during an attach; a SIGTERM pending across the fork) each assert that the set of pids on the board is exactly the demo's, and that the detach is the last three operations.
- **Results on runner1:** `5 passed` five times out of five, and `test_usb_guard_run.py` `11 passed`.

**Mutants.**
- **The builder's set.** `test/mutants.py "hw-guard-run-*" "hw-demo-*"`: `7 of 7 mutants killed`. `hw-demo-client-keeps-handlers` is killed by `test_a_group_signal_reaches_only_the_demo_on_the_board`, and `hw-demo-client-recorded-late` by `test_a_stop_raised_by_the_fork_unblock_still_kills_the_client`, in 30 s, its timeout.
- **My mutant,** the child's `try/finally` removed and the reset kept: `5 passed` (note 2).

**Claim 5.** `remote_test.sh model` at 0135ca2 (`--ignore=test/model/test_swd.py`): `2863 passed, 29 skipped`.

**Timing check against the ISA:** not applicable; host scripts only.

## Notes

1. **A stop during a re-attach is reported as the previous attach.** The re-attach for attempt 2 runs inside loop iteration 1 (`if attempt < a.attaches: prog, order = attach(...)`, `:476`), so `attempt` is still 1 when its wiring check is stopped. The report says `attach 1 … detached`. The board end state is right (the normal-end detach); only the number is one behind.
2. **The child's `try/finally` is untested.** It only matters if `client()` raises, for example when `usb_stress.find()` finds no device. Without it, such an exception would climb into the demo's code in the child. Today that ends in a traceback without reaching the cleanup, because `main()` catches only `Stop`, but nothing pins that. A test with a raising fake client that asserts the child exits 1 and writes nothing would pin it.

## What I ran

**Locally:**
- the diff and the range-diff;
- `v048_fakeboard.py`'s five scenarios;
- `group_runs.py` (60 group-signal runs).

**On runner1:**
- in `~/verifier-049/` (a git clone):
  - `group_runs.py` (200 runs);
  - `test_usb_fs_data_demo_board.py` ×5;
  - `test_usb_guard_run.py`;
  - `test/mutants.py "hw-guard-run-*" "hw-demo-*"`;
  - my `try/finally` mutant (restored after);
- `remote_test.sh model` at 0135ca2: `model rc=0`, `2863 passed, 29 skipped in 442.49s` (checkout removed).

My runner1 scratch is removed. No SWD test ran, I did not touch the Kria, and nothing was pushed.
