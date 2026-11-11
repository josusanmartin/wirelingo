# 055 reap-stop

Branch `reap-stop`, commit `0d84715` on `9dd256e` (improve-2026-11 after the reattach-wiring merge). Backlog item AE: the defect in your 054 (a Stop between `run()`'s reap and the clearing of `LIVE["client"]`).

Files changed:
- `fpga/kv260/host/usb_fs_data_demo.py`
- `fpga/kv260/host/usb_guard_run.py`
- `test/model/test_usb_fs_data_demo_board.py`
- `test/model/test_usb_guard_run.py`
- `test/mutants.py`

Builder claims to check:

1. **`stops_held()`** (`:154`) blocks SIGINT, SIGTERM and SIGHUP with `pthread_sigmask`, and unblocks them at the end.
   - The block call is inside the `try`, because CPython runs a pending handler right after `pthread_sigmask` returns.
   - The end is `SIG_UNBLOCK`, not a restore of the saved mask. The builder says that is safe because the blocks never nest. Check that they really never nest, including `kill_client` reached from inside a held block.
2. **Three demo sites, each one held step.**
   - The WNOHANG reap and its clear (`:227`).
   - The limit path's kill, reap and clear (`:234`).
   - The whole body of `kill_client()` (`:168-181`). It also tolerates `ProcessLookupError` and `ChildProcessError` (`:180`).

   The old `kill_client` cleared the record first and killed second. A Stop between the two, when it was reached by a non-Stop exception, left the client running with nothing recorded.
3. **The guard.** `Stopper.held()` (`:155`) now wraps `proc.wait(timeout=0.2)` (`:184`) and `proc.kill(); proc.wait()` (`:192`).
   - The case: the handler runs inside `Popen.wait` after its reap but before `returncode` is set. `poll()` then returns None and the SIGTERM goes to the reaped pid.
   - The stdlib swallows the ESRCH, so the harm is only to a reused pid. That is outside 054, but it is the same class of window.
4. **Sites left alone:**
   - `fork_client`: the pid is recorded before the unblock;
   - `Tracer.stop`;
   - `eth_tap.py:417-420` and `:498-499`;
   - `hw_day.py:1087-1088`.

   The builder says none of their handlers signal a recorded pid. Check that list for completeness.
5. **Tests.**
   - `test_a_stop_just_after_the_client_is_reaped` (`:316`), for {done, limit} × {signal, raise}. Each asserts:
     - 130 and STOPPED, with no traceback;
     - the normal detach as the last three operations;
     - only the demo's pid on the board;
     - exactly one reap;
     - for "signal", no kill to a reaped pid.
   - `test_a_stop_inside_kill_client_still_kills_the_client` (`:342`).
   - `test_usb_guard_run.py` `:180` and `:202`.
   - The new tests at 0d84715: 27 passed. Against 9dd256e's files: 6 failed, 21 passed, with the 054 `ProcessLookupError` signature.
6. **Mutants.** Five new ones: `hw-demo-reap-unheld`, `-limit-reap-unheld`, `-kill-client-unheld`, `-kill-client-intolerant` and `hw-guard-run-wait-unheld`. The run over `"hw-demo-*" "hw-guard-run-*"` kills 14 of 14.

   Each of the mask and tolerance mutants survives on rc and STOPPED alone, because the other fix covers it:
   - the mask mutant dies only on the no-kill-to-a-reaped-pid assertion;
   - the tolerance mutant dies only on the "raise" cases.
7. **Stress.** The builder's own harness gives 0 of 2400 failures at both commits with an immediate SIGINT. With a random 0-1 s delay, it gives 2 of 5200 at 9dd256e and 0 of 5200 at 0d84715. Your `group_runs.py` hit the window far more often (23 of 400). The builder calls its own counts weak evidence.
8. **Model suite.** `remote_test.sh model` at 0d84715: 2878 passed, 29 skipped.

Builder's caveat: the guard's second `held()`, at `:192`, has no test or mutant.

Asked checks:
- Rerun your `group_runs.py` on runner1 at 64 at a time (400 or more runs) at 9dd256e and at 0d84715.
- Rerun `v054_race.py` at 0d84715.
- Rerun the old-file comparison.
- Say whether the untested `:192` path needs a test before merge.
- Check that a Stop held by the mask is still delivered promptly once the block ends, so that Ctrl-C isn't lost.

As always, run every model test with `--ignore=test/model/test_swd.py`.
