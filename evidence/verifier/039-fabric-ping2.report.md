VERDICT: PASS

Commit 651513e on base e28a26b (an ancestor; the merge that holds 037's 1b3ff6b). Three commits: c108e09 (the abandoned `pthread_sigmask` try), baed5c8 (the code) and 651513e (docs only, 7 lines of `docs/eth10-network.md`).
- **Changed:** `fpga/kv260/host/eth_tap.py` (`run_closing`, 19 lines), `test/model/test_fabric_ping.py`, `test/mutants.py`, `docs/eth10-network.md`.
- **Unchanged:** nothing in `src/`, `flow/` or `firmware/`.
- **Result:** 037's note 1 is closed in a way that doesn't depend on where the interpreter checks for signals. On the base, my end-to-end test leaves the board on the fabric path (SEL writes `[1]`); at 651513e it is given back at every point, with every signal and for every way the body ends.

| Claim | Result |
|---|---|
| 1. The try clears `live` as its last statement; each except, including a new `except BaseException`, clears it first; so the finally is always entered with `live` False; a signal while a body exception is leaving becomes RunStopped, after the close | PASS by reading (below) and by test |
| 2. New way-out test: SIGTERM at every opcode, 14 / 31 / 31; one close each time; passes under xdist and alone; the two new mutants are killed by it | PASS: same counts, both modes. The c108e09 history sentence is not reproduced (note 1) |
| 3. Exhaustive test at opcode level too: 4186 opcode + 621 line events, 32 parts, coverage stated exactly | PASS: `fabric-ping window: 621 line events, 4186 opcode events` |
| 4. Kria Python 3.12.3 on Ubuntu 24.04.2, orchestrator's reading | No log behind it in the repo (note 2); the design no longer depends on it |
| 5. Model suite 2522 passed, 29 skipped; 107/107 mutants; selftests and probes at 50/40 MHz | PASS: suite and mutants reproduced; probe and selftest logs read, `probe PASS`, `"pass": true` |
| Asked: no flakiness, not much slower under `-n 12` | PASS (below) |

## Checked

**Claim 1, read (`eth_tap.py:681-705`).**
- **Every way into the finally has `live` False.**
  - A normal end stores it before `return status`.
  - Every exception from `make` or the body is caught by one of the three excepts, and each stores `live` as its first statement. The new `except BaseException` re-raises with a bare `raise`, so the traceback is kept.
  - A first signal handled anywhere before one of those stores goes through `stop`, which clears `live` before it raises. That holds whether the signal lands in the try or in an except's matching opcodes; from the matching opcodes, 3.12's exception table takes it to the finally.
  - A second signal is ignored from then on.
- **`SessionSignal`** is raised only by `measure_spim_session`, which is not run under `run_closing` (`eth_tap.py:735-748`). `NoActiveApp` comes from `board_parts`, before anything is held.

**My end-to-end test of the way out (`test_v039_wayout.py`).**
- **Why a second test.** The builder's way-out test uses a counting closer and a synthetic `make`. Mine goes through `eth_tap.main(["--backend","fpga","--probe"])` on the builder's fake KV260, and checks `board_end_state` after every run.
- **How.** It traces `run_closing`'s opcodes from the probe call until `Held.close` is entered, and sends real signals with `os.kill` at each opcode:
  - SIGTERM, SIGINT and SIGHUP alone;
  - SIGTERM then SIGHUP, and SIGINT then SIGTERM, in the same instant.
- **Body endings.** Four: a normal return, `OSError`, `KeyboardInterrupt`, and a real SIGTERM inside the body (`stop`'s path). In that last case the injected signal is the second one.
- **Result at 651513e: 20 of 20 cases pass, and the board is given back at every point.** Per case (run without xdist):

| Body ends | Opcodes | Outcomes seen |
|---|---|---|
| return | 14 | `0` or `143` (SIGTERM) / `129` (SIGHUP) |
| OSError | 31 | `OSError` or `RunStopped` (the accepted replacement) |
| KeyboardInterrupt | 31 | `KeyboardInterrupt` |
| SIGTERM in the body, second signal on the way out | 45 | `143` every time |

  At no point is a board left on the fabric path.

- **On the base's `eth_tap.py`** (all else 651513e), the same test fails 15 of 20 cases (all but the SIGTERM-in-body ones), with rows reading "NOT given back (SEL writes [1])". That is 037's note 1 shown end to end. The builder's three way-out cases fail there too.

**Mutants: theirs and mine.**
- **The builder's set.** `test/mutants.py "hw-*" "fab-*" "privacy-*"` in my clone at 651513e gives `107 of 107 mutants killed`. `fab-way-out-live-on-return` and `-on-exception` are killed by `…way_out…[return]` and `…[ctrl-c]`.
- **My own, against `test_fabric_ping.py` plus my test, each run under `-n 12`:**

| Mutant of `run_closing` | Result |
|---|---|
| m1: `eth_tap.py` as at e28a26b | 18 failed (3 theirs, 15 mine) |
| m2: no store in `except (RunStopped, SessionSignal)` | survives: equivalent (RunStopped already cleared `live` in `stop`; SessionSignal is not raised here) |
| m3: no store in `except NoActiveApp` | survives: equivalent for the board (nothing is held yet) |
| m4: no store at the top of the finally | survives: equivalent (no path enters it with `live` True) |
| m5: no `except BaseException` clause | 12 failed (theirs `[ctrl-c]`, `[exception]`; mine for OSError and KeyboardInterrupt) |
| m6: the try's store moved before `body()` (signals ignored for the whole run) | 8 failed, including `test_run_closing_gives_the_board_back_on_a_signal[1,2,15]` |
| m7: `stop` raises without clearing `live` | 5 failed, **all mine** (note 3) |

**The timing and flakiness ask.**
- **Model suite, `remote_test.sh model`, run back to back on runner1** (load average about 11 at the start):
  - 651513e: `2522 passed, 29 skipped in 479.57s`;
  - e28a26b: `2455 passed, 29 skipped in 528.89s`.

  The 67 new tests are the 64 opcode shards plus the 3 way-out cases. Their cost is lost in the runner's load noise: e28a26b was slower in this pair, and the item's 634 s for e28a26b was another moment.
- **`test_fabric_ping.py` alone, under `-n 12`:**
  - e28a26b: 6.3 s and 9.2 s (78 tests);
  - 651513e: 22.6 s, 22.9 s, 23.2 s and 24.6 s (145 tests).

  So about +16 s of wall time. The slowest calls are opcode shards of about 6-7 s each.
- **No flaky result.** Over 5 runs of the file, the suite run and my combined run, there was no failure and no difference between runs.

**Over-claiming.**
- Evidence level is stated as model and fake board, and no board claim is made.
- "Within the traced code an injected point can be any opcode, where the interpreter itself handles signals at fewer places" is accurate. That makes the injection a superset of real delivery points within the traced code, and the doc says plainly that code outside it is not injected into.
- **Timing check against the ISA:** not applicable. No firmware, RTL or PIO timing changed, only host Python.

## Notes

1. **The c108e09 sentence has no evidence, and I could not reproduce it.** The doc says "A first try with `pthread_sigmask` failed this test under pytest-xdist, whose worker threads take a process signal the main thread has masked."
   - **What I saw.** At c108e09, `test_fabric_ping.py` under `-n 12` gave `145 passed` 7 times out of 7, and the way-out and `on_a_signal` tests pass alone too. What it does show is 3 `PytestUnraisableExceptionWarning`s from `eth_tap.py:688` in the way-out cases: an exception raised where Python could only discard it.
   - **The logs.** The builder's `logs/c108e09/mutants.log` ends in an infrastructure error (`FileNotFoundError` in its tmp dir), not a test failure.
   - **What it means.** The mechanism described is plausible (a process-directed signal goes to a thread that hasn't masked it, and CPython runs the handler in the main thread), and the shipped design doesn't depend on it. As a statement of fact it is unbacked: keep a log of the failure or say "did not hold up" without the specific claim.
2. **No log backs "On the Kria, Python is 3.12.3, on Ubuntu 24.04.2".** The repo has kernel logs with a 24.04 toolchain string (`evidence/kv260-usb-20261030/fs/dmesg.txt:2`), but no Python reading. The new design is independent of the interpreter, so nothing rests on it. A saved `python3 -VV; cat /etc/os-release` from the orchestrator under `evidence/` would give the line a source. runner1's venv is `Python 3.12.3`, as the doc says.
3. **A test gap: "later signals are ignored" on the way out.** Mutant m7 (`stop` raises without clearing `live`) survives the builder's whole fabric-ping file, and only my "SIGTERM in the body, second signal on the way out" case kills it.
   - The board is still given back under m7; the outcome changes from 143 to an escaping `RunStopped`/`KeyboardInterrupt`.
   - That case (45 opcodes) would be a cheap addition to `test_no_opcode_on_the_way_out_…`. The test also sends only SIGTERM, and accepts `RunStopped` after a normal return, which never happens: the outcomes there were only `7` and `143`.
4. **Three of the new stores are defensive, not load-bearing.** That is m2, m3 and m4 above: removing any of them changes nothing a test or my reading can see. That is fine. The two that carry the guarantee are the try's last statement and the `except BaseException` store, and the builder's mutants cover exactly those.

## What I ran

**On runner1:**
- `remote_test.sh model` at 651513e: `model rc=0`, `2522 passed, 29 skipped in 479.57s (0:07:59)`. Then at e28a26b: `model rc=0`, `2455 passed, 29 skipped in 528.89s (0:08:48)`. Checkouts `agent-runs/protoemu-verify-651513e-1646351` and `-e28a26b-1666958`, removed.
- In `~/verifier-039/`:
  - stock and m1-m7 copies: `test_fabric_ping.py` plus `test_v039_wayout.py` under `-n 12`. Stock `165 passed`; failures as in the table;
  - the counts, without xdist: `621 line events, 4186 opcode events`, way out `14 / 31 / 31`, `4 passed`;
  - my test without xdist on 9 cases: `9 passed`, with the per-case lines above;
  - `test_fabric_ping.py` 3 more times at 651513e, twice at e28a26b, and with `--durations`;
  - c108e09: `test_fabric_ping.py` 7 times under `-n 12`, and the way-out tests alone;
  - `test/mutants.py "hw-*" "fab-*" "privacy-*"`: `107 of 107 mutants killed`.
- Read: the builder's logs in `agent-runs/fabric-ping2/logs/baed5c8/` (`probe PASS` at 50 and 40, selftests `"pass": true`, `107 of 107`) and `logs/c108e09/`.

**Locally:**
- the diff and `run_closing`, read;
- the six mutants dry-applied and parsed.

My runner1 scratch and the `remote_test.sh` checkouts are removed. I did not touch the Kria or SWD, and nothing was pushed.
