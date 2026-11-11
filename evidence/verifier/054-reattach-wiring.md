# 054 reattach-wiring

Branch `reattach-wiring`, commit `cb93530` on `9ca49ef` (improve-2026-11 after the demo-notes merge). Backlog item AC: your 052 note 1. The commit changes three files: `fpga/kv260/host/usb_fs_data_demo.py`, `test/model/test_usb_fs_data_demo_board.py` and `test/mutants.py`.

Builder claims to check:

1. **The handler** (`usb_fs_data_demo.py:470-478`). The re-attach's `attach()` now sits in a `try` with `except usb_demo.WiringError` (`:473`). The handler mirrors attach 1's (`:463-466`):
   - `spim.pmod({})`;
   - the error, then `FAIL (wiring): every PMOD pin disabled, the core not started`;
   - `sys.exit(2)`.

   Before the FAIL it adds `[device software] attach N: its wiring check failed before its device software started; every PMOD pin disabled, the cores stopped`.
2. **Board end state unchanged.** The board is reset, the cores stopped and every pin disabled, with no detach afterwards. The one extra operation is a redundant `spim.pmod {}`, kept so both handlers match.
3. **Asymmetry kept.** Attach 1's wiring path still prints no `[device software]` line. Say whether that asymmetry matters.
4. **`usb_guard_run.py` needs no change.** It classifies by the demo's rc and the NODATA line only (`:281-283`, `:321-323`), not by wiring. Re-attaches happen only in its zero-length steps (`--attaches 3`).

   An ad hoc check, not committed: the demo's real output for a wiring failure at attach 1 and at attach 2 was fed into `usb_guard_run.main`. At cb93530 the two summaries are identical (`rc=2`, FAIL, status 1); at 9ca49ef, attach 2 gave `rc=1`.
5. **New test** `test_a_wiring_failure_on_a_re_attach_reports_as_on_the_first` (`:228`). It runs with `NO_PULLUP=2 NOT_CONFIGURED=1 ATTACHES=2 TIMEOUT=1`, using a new `NO_PULLUP` switch (`:77-85`) and `FakeSpim.pmod_in() = 0` (`:64`). It asserts:
   - rc 2, no traceback, no STOPPED;
   - FAIL (wiring) last;
   - exactly one `[device software]` line, naming attach 2;
   - only the demo's pid on the board;
   - the exact last 46 board operations.

   Against 9ca49ef's demo it gives `1 failed, 9 passed`, the traceback ending in `WiringError` at `:471`.
6. **Mutant** `hw-demo-reattach-wiring-escapes` (`mutants.py:245`) takes the handler out. `"hw-demo-*" "hw-guard-run-*"`: 9 of 9 killed.
7. **Test results.** The board test gives 10 passed. `remote_test.sh model` at cb93530 gives 2872 passed, 29 skipped, exit 0.

Asked checks:
- Rerun the old-vs-new comparison.
- Rerun your `v052_order.py` re-attach WiringError case at cb93530. Confirm the operation log is 9ca49ef's plus exactly one trailing `spim.pmod {}`.
- Rerun `group_runs.py` and the `v048_fakeboard.py` scenarios, to check nothing else moved.
- Read whether the new `pmod_in() = 0` default in `FakeSpim` changes what any existing test checks.

As always, add `--ignore=test/model/test_swd.py` to any model run.
