VERDICT: PASS

Commit 5d5eb04 on base bc98a97 (an ancestor). Two commits:
- **be2f453:** the test, a mutant and the evidence;
- **5d5eb04:** docs only (7 lines of `docs/eth10-network.md`).

`fpga/kv260/host/` is untouched, so `eth_tap.py` is still 039's. Nothing in `src/`, `flow/` or `firmware/` changed. All four of 039's notes are dealt with, and each change checks out.

| Claim | Result |
|---|---|
| 1. The c108e09 failure was real in a full-suite log, now committed verbatim; doc says what it shows, that 039 couldn't reproduce it, and that the cause is not established; the thread explanation is gone | PASS |
| 2. The orchestrator's `kria-python-os.txt` copied byte for byte; doc cites it; both new files listed with sha256; redaction search clean | PASS |
| 3. Way-out test: SIGTERM, SIGHUP and SIGINT; the fourth ending; 14/31/31/45; exact outcomes; m7 killed by the second-signal cases; board-window counts unchanged | PASS |
| 4. Doc names the two load-bearing stores and calls the other three defensive | PASS: matches 039's m2-m4 and this run |
| 5. Suite 2550 passed, 29 skipped; 108/108; selftests and probes at 50/40 MHz | PASS |

## Checked

**The asked check: is the committed `model.log` verbatim, and is it clean?**
- **Verbatim.** `evidence/fabric-ping-20261103/c108e09-suite/model.log` has sha256 `f5d30a14…3053`. So does `runner1:~/protoemu-flow/agent-runs/agent-afe09457da93fe81d-c108e09-1550601/model.log`, and that checkout is at `c108e09`.
- **What it shows.** `3 failed, 2519 passed, 29 skipped`. The failures are `test_no_opcode_on_the_way_out_lets_a_signal_skip_the_close[ctrl-c]`, `[exception]` and `[return]`, with `opcode 31: closed 0 times, outcome RunStopped` (twice) and `opcode 14: closed 0 times, outcome RunStopped`. That is a signal escaping before the close, as the doc says.
- **Private strings.** The only paths are `/home/runner1/protoemu-flow/test-venv/bin/python`, three times: the runner's own home, kept under the evidence README's rule.
  - No other `/home/`, no email (the `@` hits are `@pytest…` decorators), no MAC, no private or tailnet address;
  - no `[tailnet domain redacted]`, provider name, build hostname or the local user name.

**Claim 2.**
- `kria-python-os.txt` is byte-identical (`cmp`) to `~/dev/protoemu-verify-reports/evidence/039/kria-python-os.txt` (sha256 `b966db0a…2c97`, as listed).
- It holds a 2026-11-04T06:35:29Z stamp, `Python 3.12.3 … [GCC 13.3.0]`, Ubuntu 24.04.2 LTS `os-release`, and `6.8.0-1036-xilinx`.
- The kernel release is the KV260's, the same as `evidence/kv260-usb-20261030/fs/dmesg.txt:2`. Nothing else in it is private.
- The doc and the folder README quote it correctly.

**Claim 3, run without xdist** (printed by the test):

| Body ends | Opcodes | SIGTERM | SIGHUP | SIGINT |
|---|---|---|---|---|
| return | 14 | `7`, `143` | `7`, `129` | `7`, `KeyboardInterrupt` |
| exception | 31 | `OSError`, `RunStopped` | `OSError`, `RunStopped` | `OSError`, `KeyboardInterrupt` |
| Ctrl-C | 31 | `KeyboardInterrupt`, `RunStopped` | same | `KeyboardInterrupt` |
| SIGTERM in the body | 45 | `143` | `143` | `143` |

- **The checks are exact.** `way_out_outcomes` gives exactly these sets. `RunStopped` is no longer accepted after a normal return. The board-window count is still `621 line events, 4186 opcode events`.

**The asked check: the m7 kill, and my 039 mutants against the builder's file alone.**
- Each mutant was applied to a `git archive` copy of 5d5eb04, and `test_fabric_ping.py` run under `-n 12`. Stock: `154 passed`.

| Mutant | Result |
|---|---|
| m1: `eth_tap.py` as at e28a26b | 9 failed (every way-out case but the `signal` ending) |
| m2, m3, m4: the three defensive stores removed | `154 passed` each: the doc's "defensive" is right |
| m5: no `except BaseException` | 6 failed (exception and Ctrl-C endings) |
| m6: the try's store moved before the body | 6 failed (`on_a_signal[1,2,15]` and `signal-*`) |
| m7: `stop` raises without clearing `live` | **3 failed**: `[signal-SIGINT]`, `[signal-SIGTERM]`, `[signal-SIGHUP]` |

  In 039, m7 survived the builder's file; now the builder's own tests kill it.
- **The builder's set.** `test/mutants.py "hw-*" "fab-*" "privacy-*"` in my clone: `108 of 108 mutants killed`. `fab-stop-keeps-live` is killed by `…[signal-SIGHUP]`, and the two `fab-way-out-*` mutants by `…[return-SIGHUP]` and `…[ctrl-c-SIGHUP]`.
- **My 039 end-to-end test** (`test_v039_wayout.py`, through `eth_tap.main` on the fake KV260) still passes at 5d5eb04: `20 passed`.

**Claim 5.**
- `remote_test.sh model` at 5d5eb04: `2550 passed, 29 skipped`. be2f453 → 5d5eb04 is 7 doc lines, so this is the item's count for the same code.
- **The builder's logs at `fabric-ping3/logs/be2f453/`:** `probe PASS` at 50 and 40 MHz, selftests `"pass": true` at both, and `108 of 108 mutants killed`.

**Timing check against the ISA:** not applicable; there is no firmware or RTL change.

**Over-claiming:** none found. The c108e09 sentence now says only what its log shows. "Why it failed was not established" is honest.

## Notes

1. **A pattern in the c108e09 log, for the record.** All three failures are at the last opcode before `Held.close` is called (31 of 31, 31 of 31, 14 of 14), and all three ran on worker `gw9`. That fits a signal held pending by the mask and delivered at the unmask, or at the call into `Held.close`, with `live` still True. I have not tested it, and it doesn't affect the shipped design.
2. **The Kria's build string matches runner1's.** `python3 -VV` in the Kria file reads `(main, Aug 31 2026, 10:18:26) [GCC 13.3.0]`, the same as runner1's venv Python. That is plausible for the same Ubuntu 24.04 package on two architectures, and the kernel line is the board's. I can't check from here that the reading was taken on the board; the file is the orchestrator's record.
3. **The way-out test doesn't require every allowed outcome to occur.** It collects `seen` but only prints it; requiring `seen == allowed` would pin, for example, that a SIGTERM before the try's store still ends the run with 143. The printed sets above show it does today.

## What I ran

**On runner1:**
- `remote_test.sh model` at 5d5eb04: `model rc=0`, `2550 passed, 29 skipped in 1170.22s (0:19:30)` (checkout `agent-runs/protoemu-verify-5d5eb04-2471574`, removed).
- In `~/verifier-043/`:
  - stock and m1-m7 copies with `test_fabric_ping.py` under `-n 12` (table above);
  - my `test_v039_wayout.py`: `20 passed`;
  - the counts without xdist: `13 passed`, lines above;
  - `test/mutants.py "hw-*" "fab-*" "privacy-*"`: `108 of 108 mutants killed`.
- sha256 of the source `model.log`.
- Read: the builder's `fabric-ping3/logs/be2f453/` logs.

**Locally:**
- the diff;
- `cmp` and sha256 of `kria-python-os.txt` against the orchestrator's file;
- the privacy search of `model.log`.

My runner1 scratch and the `remote_test.sh` checkout are removed. I did not touch the Kria or SWD, and nothing was pushed.
