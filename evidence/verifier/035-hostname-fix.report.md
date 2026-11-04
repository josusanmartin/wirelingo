VERDICT: PASS

Commit 2650f98 on base dbde7f7 (an ancestor, `improve-2026-11`), one commit.
- **Changed:** 12 files (docs, `fpga/kv260/install.sh`, two docstrings, one new test, one mutant).
- **Unchanged:** nothing in `src/` or `flow/`; `docs/methodology.md` is not in the diff.
- **Result:** the tailnet name is gone from every tracked file, and every claim checks out.

**Before any history goes public (note 1):** the name is still in the git history (14 commits reachable from 2650f98 add or remove it). Every commit's author and committer address is `[email redacted]`.

| Claim | Result |
|---|---|
| 1. `git grep [tailnet label redacted]` empty; hardware-day 7, README 17 replaced; README says once what `<kria-host>` stands for | PASS. Base counts: hardware-day 7, README 17, install.sh 2, legacy_demo 2, usb_replay_demo 2; at 2650f98, 0 (binary files included) |
| 2. install.sh reads `${WL_KRIA:-${KRIA:-}}`, exits 1 first if neither is set, otherwise unchanged; demo `--help` unchanged | PASS: exercised (below) |
| 3. `[home directory redacted]` gone from the five docs; three broken absolute links now plain `~/` paths | PASS: `git grep [home directory redacted]` gives only `test/gl.log` (75) |
| 4. `test_private_hosts.py` reads every tracked file, with a walk without `.git`; plant test; mutant `privacy-kria-default-host` | PASS |
| 5. A planted hit in `docs/blind-test.md`: 1 failed at `:368`; reverted: 2 passed | PASS: same line reported |
| 6. Sweep: `test/gl.log` not fixed; the other classes clean | PASS for tracked files (note 1 for history) |
| 7. Model suite 2353 passed, 29 skipped | PASS: the extra skip is `test_evidence.py:87`, archive not here |

## Checked

**Claim 1.**
- `git grep -c [tailnet label redacted] 2650f98` (no `-I`, so binaries are read too): no file.
- Every removed line was replaced by `ubuntu@<kria-host>` in the same command, and no other text on those lines changed.
- `fpga/kv260/README.md` defines `<kria-host>` once ("here and in every command in this repository … a LAN IP or a tailnet name") and names `WL_KRIA`.
- The two PNGs in `fpga/kv260/img/` carry only a Matplotlib `Software` text chunk.

**Claim 2.** I copied `install.sh` from the commit into a scratch folder and ran it only as far as the host line:
- with WL_KRIA and KRIA both unset, and again with both set empty: `set WL_KRIA to the board's ssh address, e.g. WL_KRIA=ubuntu@kria.example`, rc 1. Nothing ran before it (the check precedes `here=`, the bit-file check and any ssh).
- in a copy cut off just after the check: `KRIA=a@old` gives `a@old`; `WL_KRIA=b@new` with or without KRIA gives `b@new`.
- `bash -n` passes.
- Leaving out the host lines and the comment lines that name the host, the old and new scripts are identical.
- **Demos:** `legacy_demo.py --help` and `usb_replay_demo.py --help` are byte-identical at dbde7f7 and 2650f98 (18 and 24 lines; `description=__doc__.split("\n\n")[0]`, so only the first paragraph is used). Both files are identical from line 20 on.

**Claim 4.**
- **The test has teeth.** On runner1, with my own plants:

  | Where | Result |
  |---|---|
  | in my clone at 2650f98 (the `git ls-files` path) | `2 passed` |
  | in a `git archive` copy (the walk path) | `2 passed` |
  | the new test on a dbde7f7 archive copy | fails, `docs/hardware-day.md:116, :117, …` |
  | a name appended to `docs/blind-test.md` | fails, `['docs/blind-test.md:368']` |
  | a name appended to the tracked `evidence/README.md` | fails, `['evidence/README.md:27']` |
  | a name in an untracked file | `2 passed`, as the item says |

  The clone was clean after each test.
- **The mutant:** `test/mutants.py privacy-kria-default-host`: `1 of 1 mutants killed` (by `test_no_tailnet_hostname_in_tracked_files`).
- **The needles are split**, so neither `test_private_hosts.py` nor `mutants.py` matches itself.

**Claim 6, my own sweep of the tracked files at 2650f98:**
- **`*.ts.net`:** only the split needles in the test and the mutant.
- **100.64.0.0/10:** only the three READMEs that name the range as a class.
- **None found:** 192.168, 172.16-31, other 10.x (only `10.77.0.x`, the TAP's own); `[build machine hostname redacted]`, `[build host provider redacted]`, `fastmail`, the author's mail name; SSIDs or `psk=`.
- **Emails:** the systemd units (`modprobe@…`), `example.invalid`, `n@cocotb.test`, and two public organisational addresses (`forum@t13.org` in a cited URL, `asic-competition@janestreet.com` in `rules-check.md:279`).
- **MACs:** all locally administered (`02:57:4c:…`, `02:00:de:ad:be:01`) or broadcast.
- **`josusanmartin`:** the TT module name and the GitHub repo, public by design.

## Notes

1. **The history still carries the name and the build machine's hostname.**
   - `git log -S [tailnet label redacted] 2650f98` lists 14 commits.
   - All 946 author and committer addresses in the history are `[email redacted]`.
   - The item is scoped to tracked files, and that scope is met. But if the repository is ever published with its history, the name is still there, and every commit names the build machine.
   - Rewriting history or setting the author address is the author's decision. No agent may push or publish.
2. **The needles are case-sensitive and need a host part.**
   - `[upper-case tailnet name redacted]` appended to a tracked doc passes the test.
   - So would a bare `[tailnet label redacted]`, such as a Tailscale admin URL.
   - Unlikely, but one case-insensitive needle `[tailnet label redacted]` would cover both.
3. **Nothing tests install.sh's refusal itself.** The mutant puts the name back as text, and only the grep test kills it. A mutant that sets a different default host would survive. My run above shows the refusal works.
4. **The placeholders can't be pasted as written.** `docs/hardware-day.md` commands now read `ssh ubuntu@<kria-host> …`, which bash parses as a redirect from a file named `kria-host` if pasted as is, and `hardware-day.md` doesn't define the placeholder itself. Writing `"$WL_KRIA"` in those commands, with `export WL_KRIA=…` once, would keep the runbook copy-pastable. This is usability, not privacy.

## What I ran

**On runner1:**
- `remote_test.sh model` at 2650f98: `model rc=0`, `2353 passed, 29 skipped in 1302.27s (0:21:42)` (checkout `agent-runs/protoemu-verify-2650f98-1369729`; `pytest -rs` there on `test_evidence.py` and `test_private_hosts.py`: `4 passed, 1 skipped`, the skip `test_evidence.py:87: archive not here`; checkout removed).
- In `~/verifier-035/`:
  - the test in my clone, an archive copy and a dbde7f7 copy, plus the four plants;
  - `test/mutants.py privacy-kria-default-host`;
  - `--help` of both demos at both commits.

**Locally:**
- the `git grep` sweeps above;
- `install.sh` from the commit, run to the host check only, in a scratch folder (nothing ssh'd);
- PNG text chunks;
- `git log -S` and the address count.

My runner1 scratch and the `remote_test.sh` checkout are removed. I did not touch the Kria or SWD, and nothing was pushed.
