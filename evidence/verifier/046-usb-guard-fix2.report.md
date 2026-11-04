VERDICT: PASS

Commit edc9042 on 326de49 (report 045), one commit, 4 files:
- `fpga/kv260/host/usb_guard_run.py`;
- `test/model/test_usb_guard_run.py`;
- `test/model/test_doc_guard_labels.py`;
- `test/mutants.py`.

No firmware or software under test changed.

045's finding is fixed. `Tracer.close()` no longer raises in either kernel form, and the run still does its zero-length steps, the PASS/FAIL line and `summary.txt`. `--trace-root` is refused outside a model run before anything is made.

| Claim | Result |
|---|---|
| 1. `close()` never raises; each restore on its own; `buffer_size_kb` written back only from an integer, else left at 16384 and said; returns what it couldn't restore | PASS |
| 2. `main()` prints them as `[tracing]` lines and still runs the zero-length steps, PASS/FAIL and `summary.txt` | PASS, end to end through the CLI |
| 3. `--trace-root` (model only); the test runs each kernel form with an EINVAL-refusing fake; mutant killed | PASS |
| 4. The label test also knows "every device edge at the clock the model gives" | PASS: removing `usb-enumeration.md`'s label now fails it |
| 5. Model suite 2603 passed, 29 skipped | PASS |

## Checked

**`close()`, read** (`usb_guard_run.py:88-117`).
- **Every write goes through `put()`,** which opens and writes, and turns an `OSError` into a line in `problems`.
- **`buffer_size_kb` is written only when the saved text `isdigit()`.** Otherwise the problem line says "left at 16384, it read …".
- **The order** is set_event (truncate, then one event per append), the xHCI enable unless it read `X`, the buffer size, then `tracing_on`.
- **The backstop.** `main()` also wraps `close()` in `except Exception` (`:196-201`). The problem lines are printed after the `finally`, so they reach `summary.txt` ahead of `[attaches]`.

**My 045 check against the new code.** The real `Tracer` against the fake tracefs, starting from each form:
- `"7 (expanded: 1408)"` and `"X"`: `close()` returns, and `buffer_size_kb` stays at `16384`;
- `"1408"`: restored.

**The asked check: the `[tracing]` path end to end.** The real command line: `usb_guard_run.py --model --attaches 1 --writes 2 --reads 1 --trace-root T` on runner1. Each fake tracefs started with `tracing_on` 1, the xHCI enable 0 and `set_event` `sched:sched_switch`.

| `buffer_size_kb` at start | rc | `summary.txt` | after |
|---|---|---|---|
| `1408` | 0 | no `[tracing]` line; writes `2 of 2 ok`, reads `1 of 1 ok`, `PASS` | 1408, tracing_on 1, xhci 0, `sched:sched_switch` |
| `7 (expanded: 1408)` | 0 | `[tracing] buffer_size_kb: left at 16384, it read '7 (expanded: 1408)', which it does not take back`, then the same steps and `PASS` | 16384, the rest restored |
| `X` | 0 | the same line with `'X'`, the steps, `PASS` | 16384, the rest restored |

(Plain files there, so no EINVAL. The builder's test supplies that with a patched `open`, and my 045 fake did too.)

**The asked check: can `--trace-root` reach a real board run?**
- `usb_guard_run.py --trace-root DIR --out O` without `--model` gives rc 2, `error: --trace-root is for a model run against a fake tracefs`, and `O` is not created. The check is `ap.error` right after parsing, before the root check and before anything is opened.
- The board branch (`if not a.model: tracer = Tracer(out)`) never reads `a.trace_root`. A `--trace-root` run is a model run, with `modprobe` and `journalctl` replaced by a stub.

**Tests and mutants, in my git clone of edc9042.**
- **The tests.** `test_usb_guard_run.py` and `test_doc_guard_labels.py`: `7 passed`. That includes `test_full_run_with_a_fake_tracefs` for all three forms.
- **The builder's mutant.** `test/mutants.py "hw-guard-run-*"`: `1 of 1 mutants killed`. `hw-guard-run-raw-buffer-size` is killed by `…[7 (expanded: 1408)\n]`.
- **My two mutants, in copies:**
  - the `[tracing]` printing removed: `2 failed, 4 passed`;
  - `put()` catching `ZeroDivisionError` instead of `OSError`: `6 passed` (note 1).
- **The label test.** With `usb-enumeration.md`'s "before the got_in guard" removed, it fails with `['docs/usb-enumeration.md:313']`. In 045 that removal passed.

**Claim 5.** `remote_test.sh model` at edc9042 (`--ignore=test/model/test_swd.py`, as always): `2603 passed, 29 skipped`.

**Timing check against the ISA:** not applicable; no firmware or RTL change.

## Notes

1. **`put()`'s `OSError` catch is a second line of defence.** With the integer check in front of it, no test write raises, so catching the wrong exception goes unnoticed. If an `OSError` did arise, `main()`'s `except Exception` would still catch it, and the run would still finish. But then `close()` would stop at that file, and the later restores (`tracing_on`, for one) would be skipped. A fake that refuses some other file, `tracing_on` say, would pin it.
2. **On the board, an unexpanded buffer stays at 16 MB per CPU.** When `buffer_size_kb` read `N (expanded: M)`, the run leaves the ring buffer at 16384 KB per CPU after it ends: about 64 MB on the KV260's four cores, until reboot or a manual write. That is what `hunt.sh` does too. The `[tracing]` line says so, so the orchestrator can reset it by hand if wanted.
3. **The item's context (Y).** That a normal stage abandoned by a new SETUP can also send an old or partial packet at full speed is outside this item, and I have not checked it. It agrees with 044's note 3 by reading.

## What I ran

**On runner1:**
- `remote_test.sh model` at edc9042: `model rc=0`, `2603 passed, 29 skipped in 1051.14s (0:17:31)` (checkout `agent-runs/protoemu-verify-edc9042-2682111`, removed).
- In `~/verifier-046/` (a git clone):
  - the two test files;
  - my 045 Tracer check;
  - the three CLI runs with `--trace-root`, and the one without `--model`;
  - `test/mutants.py "hw-guard-run-*"`;
  - my two mutant copies;
  - the label test with `usb-enumeration.md`'s label removed (restored with `git checkout`).

**Locally:** the diff, read.

My runner1 scratch and the `remote_test.sh` checkout are removed. No SWD test ran, I did not touch the Kria, and nothing was pushed.
