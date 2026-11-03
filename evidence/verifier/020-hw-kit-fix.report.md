VERDICT: PASS

Commit 8a6dd8f, base 80cc0b9. Every finding and note from 016 is fixed as claimed, and I checked each one by running it, not by reading. Re-attacked as a beginner would use it, the launcher refused or turned the pins off in every case I tried. One new trap must be fixed before the day (note 1). 019's guard findings are still open on this branch (note 2). Fakes only; the Kria was not touched.

| 016 finding | Claimed fix | Checked by | Result |
|---|---|---|---|
| 1. Step 16 left USB on | Plug first, all jumpers; `usb-pi` clears `usb-off` | `usb-pi` then `rx`: refused, "needs usb-off". 3 removal mutants killed | Fixed |
| 2. Pi import crash | `root()` on use | Source run as `/home/pi/hw_day.py`: loads | Fixed |
| 3. `rsync` setup | `mkdir -p` first | The four lines against a local directory: all succeed | Fixed |
| 4. One-level prerequisites | Whole chain, 12 h expiry | Probes below. My `closure-direct`, `no-expiry`, `future-ok` mutants killed | Fixed |
| 5. Two rules weakened | No exceptions; `pi-net` | Rules 5 and 6 now say "No exceptions". The Kria's cable stays put; `tx-switch` takes the router off the switch | Fixed |
| 6. Mutants | `test/mutants.py 'hw-*'` | 56 of 56 killed, rerun | Fixed |
| 7. `--active-app` over a table | Refused when it disagrees | Told `protoemu` against a k26 or `protoemu40` table: refused. Garbled table: accepted | Fixed |
| 8-12. Notes | As listed | Each read and probed | Fixed |

## Should fix

1. Confirmations expire, `recover` with them, and `recover` is step 0's power cycle. `MAX_AGE_S` is 12 h (`fpga/kv260/host/hw_day.py:64`), and every step's chain starts at `recover` (`:148`).
   - My probe with `recover` forgotten, or stale, gives `tx-pi needs recover confirmed first (hd run recover)`.
   - Step 0 (`docs/hardware-day.md:346`) begins: "Take every wire out of J2. Unplug the Kria's power".
   - So on any later day, or after 12 hours, the launcher sends a beginner to power-cycle a healthy board. Rule 8 calls that "the one power cycle".
   - Fix: split step 0 into the one-time recovery and an "app loads cleanly" check, and have only the second expire. Or have step 0 say plainly: if the Kria answers and an app shows active, don't power it off, just confirm.

2. 019's findings are not claimed here and are still in this tree's `fabric_guard.py`:
   - a line like `protoemu: load failed, error code 1` parses as an active app;
   - an absent `fpga0/state` or `firmware-name` passes;
   - an exported `PE_ACTIVE_APP` passes empty or error output.

   This commit adds the disagreement refusal only. Fix those before this runbook is used on the board.

## Notes

3. `docs/hardware-day.md:657`: the reworked Auto-MDIX paragraph is right, and it is now worked out for disabled pins, labelled as an estimate. "Small against the 20 mA pad figure" gives no source for 20 mA. Name it, or drop the comparison.

4. `usb-pi` takes back `usb-off` as soon as its phrase is typed, even if the step then refuses for the wrong app. My probe showed that. It is the safe choice, because the wiring is on by then.

## Re-attack

Each probe used the commit's own fakes, apart from the `/home/pi` load and the `rsync` lines.

| What I tried | Result |
|---|---|
| `tx-pi` with its chain fresh | Runs; pins `{0, 1}` on, then off |
| `rails` 13 h old, in the future, old string timestamp, `true`, `NaN` | Refused, "needs rails", no rig opened |
| `usb-off` or `recover` forgotten | Refused, naming it |
| State file `{`, `[]`, `null`, a JSON string | Refused in words: "not a confirmations file. Delete it" |
| `usb-pi`'s phrase, then `rx` | `rx` refused until `usb-off` is repeated |
| SIGTERM during the turn-off, with a fake whose `close()` does not turn pins off | rc 0, pins off: `sig.live` fixes it |
| `hd off` with an unreadable table | "NOTHING WAS WRITTEN: don't assume the pins are off. Pull the wires" |
| Wiring nets of every step (my 016 script) | One net per column; **no two parts share a row** |

The new `rx` layout gives 3.3 V on column 17, the pad on 21, RJ45 1 with the termination on 25, and GND with RJ45 2 on 29. The divider is unchanged. `cut` and `ping` now print the full wiring.

## What I ran

On runner1 at 8a6dd8f, in `verifier-020/`, since removed.

- `remote_test.sh model`: `1883 passed, 28 skipped in 255.55s`, rc 0.
- `python test/mutants.py 'hw-*'`, with the test venv on PATH: `56 of 56 mutants killed`, rc 0. A first run without the venv on PATH failed every baseline as "INFRASTRUCTURE": the script calls `python` from PATH. Its docstring says "needs the test env", so this is a note, not a finding.
- My own 43 mutants against `test_hw_day.py` and `test_fabric_guard.py`: 43 killed. They are my 016 set, which had 6 survivors there, plus 11 new ones on the chain, expiry, clearing, the signal fix, state parsing, step 16's removal and the guard's disagreement check.
- 17 probes in a separate copy, plus the local `rsync` and `/home/pi` checks above.
