VERDICT: FAIL

Commit e158fe0 on ff9fecb (improve-2026-11, the usb-guard merge), one commit, 5 files:
- `fpga/kv260/host/usb_guard_run.py`;
- `fpga/kv260/host/usb_fs_data_demo.py`;
- `test/model/test_usb_guard_run.py`;
- `test/mutants.py`;
- the kv260 README.

No firmware, `tools/`, `src/` or `flow/` change.

For a signal sent to the demo's pid, the stop behaves as claimed. The guard's handler only sets a flag, the demo detaches as a normal end does, and a stop during the wiring check leaves every PMOD pin disabled.

The FAIL is a signal sent to the whole process group: a Ctrl-C at a terminal, or an ssh hangup of a run in the foreground. That reaches the demo's forked Linux client too, and the client then runs the demo's own cleanup on the board, a second process writing to it.

| Claim | Result |
|---|---|
| 1. Cause: SIGINT starts ignored in a background job from a non-interactive shell | Plausible, not something I can check from here; both scripts now install handlers regardless |
| 2. The guard's handlers: the first signal ends the loop, SIGTERMs the demo (60 s, then SIGKILL), restores tracing, writes STOPPED, exits 128+n; a second does nothing | PASS (read; the builder's tests) |
| 3. The demo on a signal: kills its client, detaches as a normal end, or disables every PMOD pin if before the wiring check finished; STOPPED, 143 | **FAIL for a process-group signal** (finding 1); PASS for a signal to its pid (fake-board comparison below) |
| 4. Test: a real signal with a demo child running, SIGTERM and SIGINT-ignored-at-start | PASS (`11 passed`) |
| 5. Give-up after 5 empty attaches, NO DEVICE, exit 3 | PASS (read; `test_no_device_stops_early`) |
| 6. Read-only port report | PASS (read: `journalctl` and a sysfs read, nothing written) |
| 7. 5/5 mutants; README; model 2619/29 | PASS |

## Findings

1. **A group-wide signal makes the Linux client child run the demo's cleanup on the board.**
   - **Why.** `usb_fs_data_demo.py` installs its handlers at `:386` (`catch_signals()`), then forks the client at `:181`. The child inherits the handler and runs `os._exit(client(...))` (`:183`).
     - On a SIGINT, SIGTERM or SIGHUP delivered to the child, `Stop` is raised inside `client()`. It escapes before `os._exit` runs.
     - It goes up through `run()`'s `except BaseException` (`:197`), where `child` is 0 in the child, so nothing is killed, and into `main()`'s `except Stop`.
     - So the **child** calls `usb_demo.detach(board, spim, pe, …)` through its inherited mapping of the fabric, prints `STOPPED` and exits 130. Meanwhile the parent is SIGKILLing it.
   - **Who receives the signal.** The guard starts the demo with a plain `subprocess.Popen` (`usb_guard_run.py:158`), in its own process group, and the client is in it too. So a terminal Ctrl-C, an ssh hangup of a foreground run, or `kill -- -PGID` reaches all three processes. A SIGTERM sent by the guard to the demo's pid does not reach the client.
   - **Shown on a fake board.** `v048_fakeboard.py` runs the demo's real board path (`main`, `attach`, `run`, `usb_demo.detach`) with fake `Board`, `Lab` and `Spim` that log every operation with the writing pid. A real `os.fork` runs a 4 s fake client. The test does `os.setpgrp()` and then `os.killpg(0, SIGINT)` while the client runs.
     - **Locally, 20 runs:** in 10, the client's pid logged board operations (`pe.write 0x809 0`, `spim.pmod {0: 8}`, `pe.stop 1`) before the parent's SIGKILL reached it.
     - **On runner1, 20 runs:** 2.
     - Every run ended rc 130 with the parent's own full detach last, so the final pin state is right.
   - **Why it blocks.** Two processes wrote to the board within the same moments, and the child can be killed in the middle of an SPI transaction to the fabric master, just before the parent's detach starts its own. The Kria rule is one process at a time, and claim 3 says the demo "kills its Linux client, detaches exactly as a normal end". The ssh-hangup case is exactly the one the stop handling exists for.
   - **Fix.** In the child, right after `fork`, put SIGINT, SIGTERM and SIGHUP back to `SIG_DFL` (or `SIG_IGN`, since the parent kills it), and wrap the call so the child never leaves it: `try: os._exit(client(...)) finally: os._exit(1)`. Optionally, `start_new_session=True` in the guard's `Popen`, so only the guard sees a terminal or hangup signal and passes its SIGTERM on. A test can fork a client and signal the process group, as above.

## Checked

**The asked check: does detach-on-signal leave the pins as a normal end does?** The fake board above, with the board operations from the last attach on, per scenario:

| Scenario | Last board operations | rc / prints |
|---|---|---|
| normal end (client returns 0) | `pe.write 0x809 0`, `spim.pmod {0: 8}`, `pe.stop 1` | 0, PASS |
| SIGTERM to the demo's pid while the client runs | the same three; the client killed by the parent first | 143, STOPPED |
| SIGTERM during the first wiring check (`detect`, after it drove a pin) | `spim.pmod {}`, `pe.stop 3` | 143, STOPPED |
| SIGTERM during the second attach's wiring check | the same three as a normal end (`order` from attach 1) | 143, STOPPED |
| SIGTERM before `[wiring]` (in `Lab.identity`) | none; the board untouched | 1, a traceback (note 1) |

- **The normal-end and signal-end tails are identical:** `POUT` 0, the pulled-up D+ (pmod 0) held low from uo0, core 0 stopped.
- **During the wiring check** the stop leaves every PMOD pin disabled and both cores stopped. That is the `WiringError` path's `spim.pmod({})` plus a `pe.stop(3)`, as claimed.
- **What `main()` does.** It catches `Stop` around attach and run, and runs the same `usb_demo.detach` when an attach finished, or `pmod({})` + `pe.stop(3)` when none had.

**The asked check: does a signal during Tracer setup still restore?** Yes, by reading.
- `Stopper.handler` only records the signal and SIGTERMs a running demo; it raises nothing. So `Tracer.__init__` (`modprobe`, then the saved reads, then the writes) always completes.
- The loop's first test (`if stopper.signal is not None: break`) is inside the `try` whose `finally` runs `tracer.close()`. A signal any time after `Tracer(out)` returns is restored.
- **The one exception: a group-wide signal while `modprobe usbmon` runs.** It kills `modprobe`, and `check=True` raises before any tracefs write. Nothing to restore, but the run ends in a traceback with no `summary.txt` (note 2).

**Claims 2, 4, 5 and 7, on runner1 in a git clone of e158fe0.**
- `test_usb_guard_run.py`: `11 passed`, including `test_a_signal_stops_the_run_cleanly[SIGTERM-False]`, the SIGINT-ignored case and `test_no_device_stops_early`.
- `test/mutants.py "hw-guard-run-*" "hw-demo-*"`: `5 of 5 mutants killed`:
  - `signals-ignored`, `child-not-told` and `hw-demo-dies-on-sigterm` by `…stops_the_run_cleanly[SIGTERM-False]`;
  - `no-give-up` by `test_no_device_stops_early`;
  - `raw-buffer-size` by `test_full_run_with_a_fake_tracefs[7 (expanded: 1408)\n]`.
- `remote_test.sh model` at e158fe0 (`--ignore=test/model/test_swd.py`): `2619 passed, 29 skipped`.
- **The guard's `demo()`.** It SIGTERMs the running demo once (handler or loop), SIGKILLs it after 60 s, and returns its rc. A stop before attach N writes `[stopped] by SIGx before attach N`. Both branches write `summary.txt` and return 128+n or 3.

**Claim 6.** `port_report` runs `journalctl -k -b` and reads one sysfs `state` file; it writes nothing.

**Timing check against the ISA:** not applicable; host scripts only.

## Notes

1. **A stop before `[wiring]` exits 1 with a traceback**, not STOPPED / 143. The `try` starts at `:416`, after `Lab()`, `board.reset()`, `spim.select(fabric=True)` and the ID read. The board is left as a normal end leaves it (no PMOD change, the chip reset), so nothing is unsafe; but the exit and print differ from claim 3's.
2. **A group signal during the guard's own `modprobe`/`journalctl` calls** kills those children too, and `modprobe`'s `check=True` turns that into a traceback before the tracer exists. Running the demo, and these, in their own session would avoid it.
3. **The demo's `[device software]` line says "no attach"** when the stop came inside `run()` of attach 1, since `sw` is only set when `run()` returns. It is cosmetic: the line still says "detached".

## What I ran

**Locally:**
- the diff;
- `v048_fakeboard.py` (the demo's board path on a logging fake board, real signals, real fork): the five scenarios in the table, and the group SIGINT 20 times.

**On runner1:**
- in `~/verifier-048/` (a git clone): `test_usb_guard_run.py`; `test/mutants.py "hw-guard-run-*" "hw-demo-*"`; the group SIGINT 20 times;
- `remote_test.sh model` at e158fe0: `model rc=0`, `2619 passed, 29 skipped in 408.70s` (checkout `agent-runs/protoemu-verify-e158fe0-2744963`, removed).

My runner1 scratch is removed. No SWD test ran, I did not touch the Kria, and nothing was pushed.
