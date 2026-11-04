VERDICT: PASS

Commit bc280ff on base 4ca4b0d (an ancestor; the merge of 035's 2650f98), one commit.
- **Changed:** six files (the two runbooks, two docstrings, `test/model/test_private_hosts.py`, one mutant).
- **Unchanged:** nothing in `src/`, `flow/` or `install.sh`; `test/gl.log` and the history untouched, as the item says.
- **Result:** 035's notes 2-4 are dealt with, and every pasted board command works with `WL_KRIA` exported.

| Claim | Result |
|---|---|
| 1. Any-case bare-label needle; plant test covers upper case and a bare label; runner1 plants fail at `blind-test.md:368` and `evidence/README.md:27`; reverted 3 passed | PASS: same lines |
| 2. `test_install_refuses_with_no_host`, ssh/scp/rsync stubbed; unset and both-empty exit 1, empty stdout, exactly the line on stderr; cut copy shows the fallback and precedence; `privacy-*` 2/2 | PASS |
| 3. hardware-day defines WL_KRIA once near the top; runbook commands use `"$WL_KRIA"`, prose keeps `<kria-host>`; docstrings likewise, `--help` unchanged | PASS: all 28 commands run as pasted (below) |
| 4. Model suite 2354 passed, 29 skipped | PASS: the skip is `test_evidence.py`'s archive rebuild, as before |

## Checked

**Claim 3: every pasted command, run as written.**
- **What I extracted** from `docs/hardware-day.md`, `fpga/kv260/README.md` and the two usage docstrings (via `ast`, so `\\` is the docstring's `\`): every command holding `$WL_KRIA`. That is code-block lines with their `\` continuations joined, and inline backtick commands in prose; 28 in all.
- **How I ran them:** each in `bash --norc -c` with `WL_KRIA=ubuntu@kria.example` exported, and `ssh`, `scp`, `rsync`, `git`, `python3` and `tar` stubbed as functions that print their argv.
- **Result:** all 28 return 0, and every board call gets the right words:
  - `ssh [ubuntu@kria.example] [sudo] [python3] [-u] [protoemu48/…/usb_replay_demo.py] [--expect] [60b0692] …`, with the continuation joined;
  - the scp downloads (`scp [ubuntu@kria.example:la.json] [/tmp/]`) and uploads (`scp [plans.json] [ubuntu@kria.example:pf2_logs/]`);
  - `rsync … [ubuntu@kria.example:hwday/fpga/kv260/]`;
  - `git archive … | ssh [ubuntu@kria.example] [tar -x -C hwday]`.

  The setup line's remote command arrives as one word, `echo "alias hd='sudo python3 -B ~/hwday/fpga/kv260/host/hw_day.py'" >> ~/.bashrc`, with `~` left for the Kria to expand, as before.
- **With `WL_KRIA` unset**, run for real in an empty scratch folder with no host reachable, each form fails loudly and leaves nothing behind:
  - `ssh "$WL_KRIA" uptime`: "Could not resolve hostname", rc 255;
  - `rsync … "$WL_KRIA":hwday/`: through ssh, rc 255;
  - `scp plans.json "$WL_KRIA":pf2_logs/`: scp takes `:pf2_logs/` as a local path and refuses ("Not a directory"), rc 1.

  Every upload in the runbooks ends in `/`, so none can quietly create a local file.
- **Placeholders left:** `<kria-host>` now appears only in prose and in the two `export WL_KRIA=ubuntu@<kria-host>` lines, plus the test's own docstring and message.
- **Other board commands:** no other ssh, scp or rsync command in the tracked docs or host scripts leaves out `"$WL_KRIA"`. `install.sh` and `build.sh` use their own variables.
- **`--help`:** `legacy_demo.py --help` and `usb_replay_demo.py --help` are byte-identical at 4ca4b0d and bc280ff (18 and 24 lines).

**Claim 1, on runner1 in my clone at bc280ff:**

| Case | Result |
|---|---|
| unchanged | `3 passed` |
| `KRIA.TAIL…TS.NET` appended to `docs/blind-test.md` | fails, `['docs/blind-test.md:368']` |
| `…admin/machines?tailnet=Tail…` (mixed-case bare label) appended to `evidence/README.md` | fails, `['evidence/README.md:27']` |
| reverted, clean tree | `3 passed` |

- It also passes in a `git archive` copy (the walk without `.git`), and on 4ca4b0d with the new test file.
- `git grep -il` on the label at bc280ff: no file.

**Claim 2.**
- The test copies `install.sh` into `tmp_path`, prepends stub ssh/scp/rsync that record a call and exit 97, and fails if any was called.
- The cut copy ends at the host check plus `printf "%s\n" "$kria"`.
- `test/mutants.py "privacy-*"`: `2 of 2 mutants killed`. `privacy-kria-other-default` is killed by `test_install_refuses_with_no_host`.
- Running the full copy is safe: with no host it exits at the check. With a mutated default it stops at the missing `out/protoemu.bit.bin` before any ssh, and the stubs are a second guard.

## Notes

1. **The needle reads raw bytes.** A tailnet name inside a compressed tracked file is not caught: a `gzip` of the FQDN, added to the index in my clone, gave `3 passed`. The tree has no such file today (the only binaries are two PNGs, checked in 035), so this is only a limit to know about.
2. **The stubs only cover PATH lookups.** A later `install.sh` that called `/usr/bin/ssh` or `command -p ssh` would get past them. Today the refusal comes first, so they are never reached.
3. **A clearer error when the variable is unset.** Without `WL_KRIA` the runbook commands fail with ssh's "Could not resolve hostname" rather than a message naming the variable. `"${WL_KRIA:?set WL_KRIA, see the top of this file}"` would say why. Optional; they already fail safe.

## What I ran

**On runner1:**
- `remote_test.sh model` at bc280ff: `model rc=0`, `2354 passed, 29 skipped in 352.95s (0:05:52)` (checkout `agent-runs/protoemu-verify-bc280ff-1465977`, removed).
- In `~/verifier-038/`:
  - `test_private_hosts.py` in the clone, an archive copy, and on 4ca4b0d;
  - the three plants and the revert;
  - `test/mutants.py "privacy-*"`;
  - `--help` of both demos at both commits.

**Locally:**
- the extraction and stubbed run of the 28 commands;
- the unset run of ssh, rsync and scp in an empty scratch folder (nothing reachable, nothing left);
- the `git grep` checks.

My runner1 scratch and the `remote_test.sh` checkout are removed. I did not touch the Kria or SWD, and nothing was pushed.
