VERDICT: FAIL

Commit 326de49 on 5554b88 (report 044), one commit, 16 files.
- **Unchanged since 5554b88:** firmware, `tools/`, `src/` and `flow/` (`git diff --stat 5554b88 326de49 -- firmware tools src flow`: empty).
- **Fixed:** both 044 findings. My grep at 326de49 finds no pre-guard board or gate-level figure without its label, and `guide.md` matches the bundle.
- **Reproduced:** the replay comparison, byte for byte.

The FAIL is the new `Tracer.close()`, which the orchestrator will run on the board next. In a state the Linux 6.8 tracefs documents, it raises instead of restoring, and the run then loses its zero-length steps and its summary.

| Claim | Result |
|---|---|
| 1. Labels on every pre-guard board and gate-level figure; `test_doc_guard_labels.py` flags 5554b88's docs | PASS: my grep is clean. The test is not vacuous, but it is coarser than the claim (note 1) |
| 2. `guide.md` 130 / 14 / 90; `test_guide_sizes.py` | PASS; the open UART question is settled (note 2) |
| 3. `compare_dev_replay.py` committed; `run.sh` step 2; reruns byte-identical | PASS |
| 4. Tracer saves and restores the tracing state | **FAIL** (finding 1) |
| 5. Model suite 2600 passed, 29 skipped | PASS |

## Findings

1. **`Tracer.close()` can't write back what `Tracer.__init__` read from `buffer_size_kb`** (`fpga/kv260/host/usb_guard_run.py:63` reads it, `:98-99` writes it back).
   - **What the kernel does.** In Linux 6.8 (`kernel/trace/trace.c`; the Kria runs `6.8.0-1036-xilinx`), `tracing_entries_read` returns `"%lu (expanded: %lu)\n"` while the ring buffer has never been expanded, and `"X\n"` when the per-CPU sizes differ. `tracing_entries_write` parses with `kstrtoul_from_user(ubuf, cnt, 10, &val)`, so writing either string back fails with EINVAL.
   - **What I ran** (`v045_tracer.py`, the real `Tracer` against a fake tracefs whose `buffer_size_kb` follows those two rules):
     - start `"7 (expanded: 1408)"`: `close()` raised `OSError(22, 'Invalid argument …')`;
     - start `"X"`: the same;
     - start `"1408"`: `close()` ok, restored.
   - **What it costs.** `close()` runs in `main()`'s `finally` after the attach loop. The exception stops the run there:
     - the 1000 zero-length writes and the 10 + 200 reads never run;
     - no PASS/FAIL line is printed;
     - `summary.txt` is not written;
     - tracing is left off, with `buffer_size_kb` at 16384.

     That happens after 200 attaches on the board.
   - **How likely it is.** Only the orchestrator can say. The buffer is unexpanded if nothing has used tracing since the Kria's last boot; an earlier `hunt.sh` run in this boot would have expanded it.
   - **The builder's test can't see it.** `test_usb_guard_run.py`'s fake starts at `"1408\n"`.
   - **Fix.** Restore `buffer_size_kb` only when the saved text is an integer. An unexpanded buffer can't be shrunk back anyway. Alternatively, catch `OSError` per file in `close()` so the steps and the summary still run, and add the two kernel forms to the fake.

## Checked

**The asked check: my grep at 326de49.**
- `git grep -i -E "usb_fs_dev|usb_ls_dev|test_usb_fs_device|test_usb_ls_device|usb_fs_data_demo|17\.6 kB|kB/s"` over all tracked `.md` outside `evidence/`, each hit read in context.
- **My four 044 sections are labelled:**
  - `README.md:209` ("the 129-word firmware before the got_in guard; not re-measured on the board with the 130-word one yet");
  - `README.md:210` ("The gate-level and KV260 runs used the 141-word firmware before the got_in guard …");
  - `verification-story.md:25, 28, 29`;
  - `verification.md:359-361`.
- **So are** `physical.md:736-738`, `explain-wirelingo.md:678-681`, `usb-enumeration.md:325-327`, `usb-full-speed.md:427-431`, and the kv260 README's replay intro (`:753`).
- **The rest** are model or RTL figures that still hold (the RTL tests rerun with the guard in 044), study counts with their own note (`v3-study.md:136-140`), the report covered by its guard box (`usb-full-speed.md:25-41`), or references without figures.

**The asked check: is the label test vacuous?** Run in a git clone of 326de49 on runner1, since the test uses `git ls-files`.
- **Stock:** `2 passed` (with `test_guide_sizes.py`).
- **5554b88's `README.md`, `docs/` and kv260 README with 326de49's tests:** it fails with `['README.md:186', 'docs/explain-wirelingo.md:545', 'docs/usb-full-speed.md:396', 'docs/verification-story.md:15']`. `test_guide_sizes.py` fails with `'UART over virtual pin 24': [4, 13], the bundle {'UART_TX_V24': 4, 'UART_RX_V24': 14}`.
- **Labels removed one section at a time:**

| Removed | Result |
|---|---|
| both row labels in `README.md` (209, 210) | fails, `['README.md:186']` |
| `explain-wirelingo.md`'s "before the got_in guard" | fails, `[…:545]` |
| `usb-full-speed.md`'s board-paragraph label | fails, `[…:396]` |
| `verification-story.md`'s "Data moves" row label | **passes**: the same section has another "before the got_in guard" (note 1) |
| `usb-enumeration.md`'s label | **passes**: its figure isn't one of the four `FIGURES` (note 1) |

- **`guide.md`** with `| USB with line unit | 131 |`: `1 failed`.

**Claim 3, the asked rerun.**
- **The trees.** `git archive` copies of bc98a97 and c4003a2 on runner1, with the two `usb_replay_demo.py` commands of `run.sh`'s step 2 for each (plan, then `--model` replay): all four rc 0.
- **The comparison.** `compare_dev_replay.py` on the two new plan files gives output **byte-identical** (`cmp`) to the committed `compare-dev-replay.txt`. Both `ls-dev` and `fs-dev` have equal requests, restaged 1 and 1, 74 equal wire packets, and "pads equal False".
- **The logs.** `replay-bc98a97.log` and `replay-c4003a2.log` equal the committed ones apart from the wall times and the two lines naming the plan and save paths.

**Claim 2.** Against `tools/micropython/protoemu_fw.py` at 326de49, `guide.md:55-58` now gives UART 4 + 14, USB 143 + 49, USB-line 130 and I2C 90, which is what the bundle holds.

**Claim 4, the rest.**
- **What `close()` gets right:** it restores `set_event` one event per write (correct for that file), leaves `events/xhci-hcd/enable` alone when it read `X`, and writes `tracing_on` back.
- **The builder's test:** `test_usb_guard_run.py` passes, `3 passed`.

**Claim 5.** `remote_test.sh model` at 326de49: `2600 passed, 29 skipped`. That is 044's 2597 plus the three new tests.

**Timing check against the ISA:** not applicable, since no firmware or RTL changed since 044.

## Notes

1. **The label test checks sections, not figures.** It passes a section if any "before the (got_in) guard" appears anywhere in it, and it only knows four figure strings. So:
   - `verification-story.md`'s "Data moves" row can lose its label unnoticed while the "Firmware runs on hardware" row two lines down keeps one;
   - `usb-enumeration.md`'s "every device edge at the clock the model gives" is outside `FIGURES` and is guarded by hand only, like `verification.md` and `physical.md:736`.

   The item's "against 5554b88's docs it flags exactly your four sections" is also not quite right. It flags README, verification-story, `explain-wirelingo.md:545` and `usb-full-speed.md:396`, but not `verification.md`, which was one of my four (the item says that one is by hand). All of them are labelled now, so this is only about what the test will catch next time.
2. **The open UART question is answered.** `evidence/model-usb-guard-20261104/rtl-test-module-c4003a2.log` logs `generated UART loopback: 53 bytes, 4.639 ms simulated, TX/RX 4/14 words`. So the guide's "53 bytes in 4.639 ms" is the 14-word RX's figure, and needs no change.

## What I ran

**On runner1:**
- `remote_test.sh model` at 326de49: `model rc=0`, `2600 passed, 29 skipped in 542.63s (0:09:02)` (checkout `agent-runs/protoemu-verify-326de49-2656770`, removed).
- In `~/verifier-045/` (a git clone):
  - the two new tests stock, against 5554b88's docs, and with each label removed or a size changed (always restored with `git checkout`);
  - `run.sh` step 2 at fresh bc98a97 and c4003a2 trees, and `compare_dev_replay.py`;
  - `test_usb_guard_run.py`.

**Locally:**
- the diff;
- my grep at 326de49, read in context;
- `v045_tracer.py` (the real `Tracer`, a fake tracefs with the kernel's read and write rules);
- `kernel/trace/trace.c` at v6.8, read (`tracing_entries_read` and `tracing_entries_write`).

My runner1 scratch and the `remote_test.sh` checkout are removed. No SWD test ran: the whole model suite ran only through `remote_test.sh`, which passes `--ignore=test/model/test_swd.py`, and my other runs named their test files. I did not touch the Kria, and nothing was pushed.
