# 052 demo-notes

Branch `demo-notes`, commit `2a5677d` on `d7f177d` (improve-2026-11 after the usb-in-setup merge). Backlog item AA: notes 1 and 2 of your report 049. Files: `fpga/kv260/host/usb_fs_data_demo.py`, `test/model/test_usb_fs_data_demo_board.py`, `test/mutants.py`.

Builder claims to check:

1. **049 note 1 fixed.**
   - **The re-attach moved.** It used to run at the end of loop iteration k; it now runs at the start of iteration k+1 (`if attempt > 1: prog, order = attach(...)`, `:470-472`), so `attempt` is the number of the attach being set up. The board operations run in the same order as before.
   - **One assignment.** `run()` takes `n` and stores `LIVE["sw"] = n, sw` in one assignment (`:177-182`). `except Stop` drops the stored software when its number isn't `attempt` (`:483-485`).
   - **New report line.** `[device software] attach N: stopped before its device software started; <left>` (`:498-499`).
   - **One reported line changed from 049.** A stop during the first wiring check now says `attach 1: stopped before its device software started; every PMOD pin disabled, the cores stopped` instead of `no attach; …`. A stop before `[wiring]` still says `no attach; the board not touched`. Board end states are unchanged.
2. **049 note 2 pinned.** `test_a_client_that_raises_exits_1_and_leaves_the_board_alone[no-device,stop]` (`:224`). In `no-device`, the real `client()` runs with `usb_stress.find` returning None, so it raises TypeError; in `stop`, it raises `d.Stop`. The test asserts:
   - the child exits 1;
   - the demo exits 1 with FAIL last, and no traceback or STOPPED;
   - only the demo's pid on the board;
   - the board ends with the normal detach.
3. **New wiring-check test.** `test_a_stop_during_a_wiring_check_names_that_attach[1,2]` (`:202`): SIGTERM during attach n's wiring check, with attach 1 never configured. It asserts:
   - rc 143, STOPPED, and no traceback;
   - exactly one `[device software]` line, naming attach n;
   - only the demo's pid on the board;
   - the end state: `pmod {}`/`stop 3` for n=1, the normal detach for n=2.

   Against d7f177d's demo file, `2 failed`; the n=2 case prints `attach 1: … detached`.
4. **Mutants.** The new mutant `hw-demo-client-exception-escapes` (`mutants.py:232`) removes the child's try/finally and keeps the signal reset. `"hw-demo-*" "hw-guard-run-*"`: 8 of 8 killed. With the mutant applied by hand, the `stop` case shows the mutated child itself writing the cleanup (`0x809 0; pmod {0: 8}; stop 1`) and printing STOPPED.
5. **Results on runner1.**
   - The board test: 9 passed, three times out of three.
   - `test_usb_guard_run.py`: 11 passed.
   - `remote_test.sh model` at 2a5677d: 2871 passed, 29 skipped, exit 0.

Builder's own caveats:
- **The `no-device` case kills the mutant only through the traceback.** It writes nothing to the board even mutated, because `main()` catches only `Stop`.
- **A WiringError during a re-attach still escapes as a traceback.** This was left out of scope. Say whether that path can leave the board in a bad state, and whether that is a defect or a note.

Asked checks:
- Rerun the d7f177d-vs-2a5677d comparison.
- Rerun your 049 `v048_fakeboard.py` five scenarios and `group_runs.py` at 2a5677d.
- Check that the move of the re-attach leaves the operation order on the board the same, on a normal two-attach run, by comparing the fake board's logs at d7f177d and 2a5677d.

As always, add `--ignore=test/model/test_swd.py` to any model run.
