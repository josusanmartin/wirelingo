# 078 home-path-scan

Branch `as-home-path`, made with `git checkout -b as-home-path next-8x4`
(`next-8x4` = `36c6c26`); `git merge-base --is-ancestor b2fa7f3 HEAD`
exits 1. This is backlog item AS, from 077 finding 1: no automated check
covered home paths. It also carries 077's two nits.

Evidence level: **model suite** (pytest on runner1) and **mutants**. No
board, no SWD, no `src/`, `flow/` or methodology change, no push.

Commits on `36c6c26`:
- `4190ee8`:
  - new `test/model/test_home_paths.py`;
  - `test_private_hosts.python_constant_lines` takes a matcher;
  - 4 mutants in `test/mutants.py`;
  - `test/tb.gtkw`'s two paths made relative.
- `2f63d7f`, the 077 nits: `fpga/kv260/README.md`, and the doc test in
  `test/model/test_kv260_3core.py`.

Tip **`2f63d7f`**. `record_check.py --repo . --tree HEAD --commits
next-8x4..HEAD`: 747 files, 2 messages, 0 flagged.

The builder's claims:

1. **The check.** `test_home_paths.py`, a sibling of `test_private_hosts.py`.
   - **Inputs:** it reads the same tracked files (`tracked()`, evidence/
     included) and, for each .py file, its string constants joined
     (`python_constant_lines` with a matcher argument).
   - **Classes**, case-sensitive:
     - `/home/<name>`, with or without a trailing slash;
     - the scratch-directory form `-home-<name>-`, which must not follow a
       letter or digit, so mutant names like `privacy-home-path-default`
       and words like `home-made` don't match;
     - `/Users/<name>`;
     - `/tmp/claude-<uid>`.
   - **Allowlist:** a `/home` or `-home-` name passes only if it is in
     `ALLOWED`. Each entry has its reason, and each is a generic name public
     main already uses:
     - `ubuntu`: the Kria's user;
     - `pi`: the Raspberry Pi's default user;
     - `vscode`: the devcontainer;
     - `runner1`: the build host's account, in verbatim model logs and the
       verifier reports quoting them;
     - `someone` and `x`: made-up examples.
   - `/Users/` and `/tmp/claude-` always fail. `~/...` and `$HOME/...` don't
     match.
   - `/home/<other>/` (CI) is not in the tree, so it is not allowed.
   - **Not self-matching:** the patterns are written split (`rb"/ho"
     rb"me/" + NAME`), and the planted paths are built with `+` from random
     names. The file passes its own scan, and so does `test/mutants.py`.
   - **Self-tests:**
     - `test_a_planted_home_path_is_found`, 8 planted files: a plain path; a
       full scratch path with its dashed directory; the dashed form alone;
       `/Users`; `/tmp/claude-<uid>`; no trailing slash; adjacent Python
       literals; an upper-case name. Plus a clean file of allowed and
       near-miss forms, and an untracked file;
     - `test_the_allowlist_is_only_generic_names`: `ubuntu2` and `piotr`
       are not allowed; `/Users/<other>` fails.
2. **The whole tree at `next-8x4` `36c6c26`.** Every match, before the
   allowlist:
   - **Real, fixed:** `test/tb.gtkw:5`, `:8`, `/home/<other>/p/tt-new-template-proto/...`.
     This is the TT template's GTKWave save file, carrying its author's home
     directory; it has been in the tree since the first commit. It is now
     `"tb.fst"` and `"tb.gtkw"`, relative. GTKWave is given the dump file on
     the command line anyway.
   - **Allowed, generic.** Files outside evidence/verifier:
     - `.devcontainer/Dockerfile:5`, `/home/<other>` (1);
     - `evidence/fabric-ping-20261103/c108e09-suite/model.log`,
       `/home/runner1` (3);
     - `evidence/kv260-usbfs-zlp-20261103/`: `fix200.log` (7),
       `quirkm200.log` (3), `nodata.py` (1) and `nodata2.py` (1), all
       `/home/ubuntu`;
     - `evidence/README.md`, `/home/ubuntu` (1);
     - `fpga/kv260/host/hw_day.py:85`, `/home/pi` (1);
     - `test/model/test_hw_day.py:1212`, `/home/pi` (1);
     - `test/model/test_flow_tools.py:276`, `:291`, `/home/<other>` (2).
   - **evidence/verifier/ (verbatim, listed, not edited):**
     - `016-hw-kit.report.md`: `/home/pi` 4, `/home/someone` 1;
     - `020-hw-kit-fix.md`: `/home/pi` 1;
     - `020-hw-kit-fix.report.md`: `/home/pi` 3;
     - `036-usbfs-zlp-fix.report.md`: `/home/ubuntu` 1;
     - `041-verifier-reports2.md`: `/home/ubuntu` 1;
     - `041-verifier-reports2.report.md`: `/home/pi`, `/home/someone`,
       `/home/ubuntu`, 1 each;
     - `042-verifier-reports2-fix.md`: `/home/ubuntu` 1;
     - `042-verifier-reports2-fix.report.md`: `/home/pi` 1, `/home/someone`
       1, `/home/ubuntu` 3;
     - `043-fabric-ping3.report.md`: `/home/runner1` 1;
     - `README.md`: `/home/pi` 2, `/home/someone` 2, `/home/ubuntu` 3,
       `/home/runner1` 1.

     All are allowed names, so none fails the scan.
   - **Never matched:** `-home-<name>-`, `/Users/` and `/tmp/claude-`.
     That includes `fpga/kv260/reports/v03-20261110-3c/board_smoke.log`,
     whose scratch path was redacted in 077.
   - At `2f63d7f`, `hits()` is empty, and `test_private_hosts.hits()` is
     empty too.
3. **Mutants** (`python test/mutants.py 'privacy-home-*' 'privacy-scan-*'
   privacy-kria-default-host`, runner1 clone at `2f63d7f`, test venv):
   **7 of 7 killed**.
   - **New:**
     - `privacy-home-path-default`: install.sh's `OUT_DIR` default set to a
       made-up home path; killed by `test_no_home_path_in_tracked_files`;
     - `privacy-home-scratch-in-readme`: a Claude scratch path with the
       dashed directory, in place of README's `<scratch>/out3c`; killed by
       the same test;
     - `privacy-home-scan-no-dashed` (the dashed alternative dropped) and
       `privacy-home-scan-any-name` (any `/home` name allowed); both killed
       by `test_a_planted_home_path_is_found`.
   - **Existing:** the three re-run privacy mutants are still killed, since
     `python_constant_lines` changed.
4. **077 nits.**
   - README's board section no longer puts "115200" under "From the log".
     The ring line says the log shows the three pairs and their messages,
     and that the rate is `bringup_kria.py`'s `step_loopback`. Likewise,
     uart_rx_pin's 9600 is cited to the code; only uart_tx_pin's 9600 is in
     the log.
   - The doc test's `leaves_cores_to_default(line)` now also flags a
     `build.sh` command line without `--cores` that starts with the command
     (a code block, env prefixes allowed) or that also runs `install.sh`.
     It checks itself against 076's four cases (bare `build.sh`,
     `build.sh 25 && ... install.sh`, `PMOD_DRIVE=12 ... build.sh 24`,
     `build.sh && APP=... install.sh`) and three good lines. Prose that names
     the tool in backticks is not flagged. Both docs pass, with the
     2026-10-07 record exempt as before.
5. **Model suite** on runner1 at `2f63d7f`: **3097 passed, 29 skipped**
   (3094 at `36c6c26`; +3: test_home_paths.py).

Caveats:
- The allowlist is by name, not by file. `/home/ubuntu/...` is accepted
  anywhere, which is right for the board's user but would also pass a
  checkout path on someone's Ubuntu box with that user name.
- Other forms are not searched: Windows `C:\Users\...`, `/root/`,
  `/var/home/`, a URL-encoded `%2Fhome%2F`. None is in the tree (077's
  manual search). They are easy to add if wanted.
- The new scan doesn't apply to commit messages; record_check reads those
  but not for this class. The two new messages name only the forms, no
  user.

Asked checks:
- Model suite; and `python test/mutants.py 'privacy-home-*'`.
- Run `hits()` (`test_home_paths.py`) at `36c6c26` by hand: expect exactly
  `test/tb.gtkw:5` and `:8`. Then check that the list in claim 2 is every
  pre-allowlist match.
- Read the allowlist and its reasons, and say whether any entry should be
  per-file instead (for example `runner1`, only under evidence/).
- Check that `tb.gtkw` still opens in GTKWave, if you have it, with
  `gtkwave test/tb.fst test/tb.gtkw` after a `make` that writes `tb.fst`.
- The README lines and the doc test (claim 4).
- Privacy: `record_check.py` as above.

No Kria, no SWD; use `--ignore=test/model/test_swd.py` on model runs.
