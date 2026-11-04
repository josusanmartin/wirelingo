VERDICT: FAIL

Commit 80cc0b9, base 49a2d89. The guard sits in the right places. Every `/dev/mem` opener in the repo calls it first, the tests can fail, and the runbook now stops on a failed load. But claim 2, "any doubt refuses", is false. Missing kernel files, a stray message line from `xmutil`, and a leftover `PE_ACTIVE_APP` each get a pass. The stated mutant count has no artifact again. Everything here is against fakes: the Kria is off limits and I did not touch it.

| Claim | Result |
|---|---|
| 1. No route in `host/` reaches `/dev/mem` without the guard | PASS in the repo. Gap on the board (finding 3). Format attack got through (finding 1) |
| 2. Fails closed | **FAIL** (findings 1, 2) |
| 3. Checks touch no fabric; is `xmutil` good enough | Touches none: PASS. A stronger kernel-only check exists (finding 2) |
| 4. A failed load stops the runbook; step 0 recovers read-only | PASS, note 5 |
| 5. 37/37 mutants; 1836 passed | 1836 PASS. Mutants **unproved** (finding 4) |

## Blocking

1. `fpga/kv260/host/fabric_guard.py:57`, `:91`: the parser accepts any line whose last word is a number. Then only a `protoemu` prefix is required.

   I fed `check()` invented outputs, with the sysfs and device-tree files absent:

   | `xmutil listapps` stdout, rc 0 | Result |
   |---|---|
   | `protoemu: load failed, error code 1` | **passes, as app `protoemu:`** |
   | a row for `protoemu_old_build` or `protoemu-broken`, active | **passes** |
   | two active rows, `protoemu` first | passes |
   | `Load Error: -1`, or every row `-1` | refused |
   | `k26-starter-kits` active | refused |
   | `Error: Failed to connect to dfx-mgr` | refused |

   The first row is the shape of an error message, the case this guard exists for. I don't know what `xmutil` prints on errors, and the guard doesn't either: its format is from memory. Fix:
   - Match whole names against a known set, or against `/lib/firmware/xilinx/<app>/`, which is fabric-free.
   - Require the row to have the table's column count.
   - Refuse two active rows.

2. `fabric_guard.py:94`, `:97`: an absent `fpga0/state` or `firmware-name` counts as no evidence, so the check passes. Neither path has been seen on this board, which the docstring says. If either path is wrong, those checks never run, and nothing reports it. The guard is then the listing alone.

   That is not failing closed. I also think the device tree check is the stronger one, and could be made required:
   - The overlay sets `firmware-name` on `fpga-full`.
   - The kernel removes that property when the overlay is removed or reverted after a failed program. That is from memory of how overlays work, not checked on this board.
   - "Present and equal to `<app>.bit.bin`" therefore says our overlay is applied now, with no daemon involved.
   - `state` alone is weak: as I recall, it stays `operating` from the last good program after an unload.

   Fix: require both files, as `operating` and the exact name. Treat absent as a refusal that says which path was missing, so the first real run on the board shows whether the paths are right. For claim 3, `xmutil` reading dfx-mgr's bookkeeping is a reasonable first check, but it should not be the only one.

3. `PE_ACTIVE_APP`, `fabric_guard.py:87`: the override applies whenever the table can't be parsed, and the guard reads it from the environment.
   - Exported once (the doc at `docs/hardware-day.md:146` says "set `PE_ACTIVE_APP=<app>`"), it then passes **empty stdout**, `Failed to connect to dfx-mgr`, and a table with an extra column, for every later command in that shell, including after a failed load.
   - `hd run --active-app` sets it for one process only, which is fine.
   - Fix: take the override only as a command-line flag, or have it name the exact table text it vouches for. And tell people never to export it.

4. Mutants: "37/37" has no artifact. `test/mutants.py` has no entry for the guard or `hw_day`, and no other mutant file is in the commit. That is the same problem as 016 finding 6.

   My 15 mutants against `test_fabric_guard.py` and `test_hw_day.py`: **14 killed.** They covered:
   - each of the three checks removed;
   - the env override overruling a table;
   - `rc` ignored, timeout uncaught;
   - each opener unguarded, and the guard after `os.open`;
   - a cached refusal turned into a pass;
   - `hd off` unguarded, and `recover` not required.

   The survivor makes `check_app` return `--active-app` without calling the guard. It is still caught in practice, at `SpimMem`'s own call, so defence in depth works there, but no test sees it.

## Should fix

5. The Kria's existing copies of the host scripts are unguarded. `install.sh` puts `tools`, `firmware` and `host` in `~/$APP` on the board (README), and those predate this commit. The item does not say which copy ran `eth_tap.py` in the incident. The runbook's setup refreshes only `~/hwday`. Say in step 0 that the old copies must be replaced or removed before any tool runs, or the guard protects only the new one.

6. `docs/hardware-day.md:66`: "only a power cycle brings it back", and step 0's "Nothing remote can bring it back", are stated as fact. They are probably right for a hung bus with no remote power control, but the cause itself is "likely, not proved". Label them the same way. Step 0's power cycle by the author is consistent with RULES.md's "until the author says so", since it is addressed to the author.

7. 016's findings are still open on this branch, which is expected since this item doesn't claim them. They include:
   - `hw_day.py:50` `parents[2]` crashing on the Pi;
   - the setup `rsync`;
   - step 16 without USB removal;
   - one-level prerequisites;
   - `hd off` untested.

   The new `turn_off` message, "with no app there are no PMOD pins on", is still a reassurance when the real reason is an unreadable table (`hw_day.py:943`).

## What held

- **Claim 1 search:** `open(` with `/dev/mem`, `mmap(` and `devmem` across the repo (`.py`, `.sh`, `.c`) found only `pe_kria.DevMem`, `pe_spim.SpimMem` and `pe_lab.LabMem`, each with `require_app()` first. `test_every_dev_mem_opener_in_the_tree_is_guarded` checks the same set. `/dev/hidraw` users in the USB scripts are not fabric.
- **The refusals:** a failed or timed-out `xmutil` refused, as did a non-`operating` state (`write error`, `unknown`), a wrong `firmware-name`, a table with no active row, and a non-protoemu active app. A table it can parse is never overruled by the override. Nothing is mapped on refusal (test).
- **Per-app overlays:** `install.sh` rewrites `firmware-name` to `$APP.bit.bin` per app, so the device tree check is right for `protoemu40` and `protoemu48`. I checked this because a shared `protoemu.bit.bin` would have refused them.
- **Claim 4:**
  - Rule 13 is plain.
  - `load_line` now chains `&& sudo xmutil listapps` and says to stop on any error. The incident's `;` chain is named as the cause to avoid.
  - Step 0's commands are read-only: `dmesg`, `journalctl`, `xmutil listapps`, `cat`, `ls`.
  - The retry is done by hand with the result read before continuing.
  - `preflight` now needs `recover` (test, and my mutant was killed).
- **Evidence level:** "not proved", "has not run on a board" and "fakes only" are all stated.

## What I ran

- `remote_test.sh model` at 80cc0b9: `1836 passed, 28 skipped in 233.19s`, rc 0.
- My 15 mutants on runner1 in `verifier-019/`, since removed.
- `fabric_guard.check()` locally with the 20 inputs above. No file outside my scratch directory was read or written, and no `/dev/mem`.
