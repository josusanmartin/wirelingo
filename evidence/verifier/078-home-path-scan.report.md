VERDICT: FAIL

078 home-path-scan (branch `as-home-path`, `4190ee8` and `2f63d7f` on `next-8x4` `36c6c26`; backlog AS): verifier report.
Kria: verifier does not touch it. No SWD.

Every stated claim reproduces:
- `hits()` at `36c6c26` is exactly `test/tb.gtkw:5`, `:8`;
- claim 2's pre-allowlist list is every match, file by file and count by count;
- 7 of 7 mutants are killed;
- the model suite gives 3097 passed;
- `tb.gtkw` still opens in GTKWave;
- the 077 nits are fixed.

The verdict is FAIL on the check's purpose. I planted the author's real local user name in a scratch copy of the tree, in 26 forms. The scan misses it in the one form that started this item: the home directory itself as a dashed directory name, `-home-<user>` followed by `/` or the end. That is how Claude names a project directory for a session run in the home directory (`…/projects/-home-<user>/…`). It is also how 077's raw log spelled the path (`/tmp/claude-<uid>/-home-<user>/…`). The scan caught that log line only through its `/tmp/claude-` prefix.

The dashed class requires a `-` after the name, so the item's own self-test plants only `-home-<name>-proj`. A one-line change to the class catches it with no false positive in the tree (finding 1).

Evidence levels: model suite and mutants (runner1); the plant and `hits()` runs are local Python on scratch copies. No board.

## Findings

1. **The dashed class misses the home directory's own dashed name** (`test/model/test_home_paths.py:35`).
   - The pattern `(?<![A-Za-z0-9])-home-([A-Za-z0-9_.]+)-` needs a `-` after the name. It matches `-home-<user>-dev-protoemu/`, a path below the home directory, but not the home directory itself:
     - `~/.claude/projects/-home-<user>/memory` (form 5 below);
     - `-home-<user>` at the end of a line (form 6);
     - 077's raw-log form without its `/tmp/claude-` prefix.
   - So a redaction that removes the `/tmp/claude-<uid>` prefix but keeps the rest would pass. The docstring's "the form a home path takes as one directory's name" (`:12-13`) holds only for paths below the home directory.
   - **Fix:** end the class at the name, not at a `-`: `(?<![A-Za-z0-9-])-ho` `me-([A-Za-z0-9_.]+)(?![A-Za-z0-9_.])`. The `-` in the lookbehind keeps `--home-dir` out.
     - **On the tree** (`cand.py`, tracked files at both `36c6c26` and `2f63d7f`): 0 matches, so 0 new false positives. The current class also gives 0.
     - **On my plants:** it catches forms 4–8.
   - Add a planted case with the name followed by `/` (and one at the end of a line) to `test_a_planted_home_path_is_found`, and a mutant that restores the trailing `-`.
2. **Other forms the scan misses.** I planted the real name, read from `$USER` at run time and never printed, in a scratch `git archive` of `2f63d7f` with its own fresh repository (so no blob with the name entered any repo of the project). Results:

   | # | form | result |
   |---|---|---|
   | 1 | `/home/<user>/dev/protoemu/README.md` | caught |
   | 2 | `HOME=/home/<user>` (end of line) | caught |
   | 3 | `"/home/<user>"` in JSON | caught |
   | 4 | `/tmp/x/-home-<user>-dev-protoemu/out` | caught |
   | 5 | `~/.claude/projects/-home-<user>/memory` | **missed** (finding 1) |
   | 6 | `-home-<user>` at the end of a line | **missed** (finding 1) |
   | 7 | `/tmp/claude-<uid>/-home-<user>/…/out3c` | caught (by `/tmp/claude-`) |
   | 8 | `~/.claude/projects/-home-<user>-dev-protoemu/memory` | caught |
   | 9 | `/Users/<user>/src` | caught |
   | 10 | `/HOME/<user>/x` (upper-case directory) | missed |
   | 11 | `/home/<USER>/x` (upper-case name) | caught |
   | 12 | `"/ho" "me/<user>/x"` (adjacent Python literals) | caught |
   | 13 | `"/home/" + "<user>/x"` (Python `+`) | missed (by design: only adjacent literals are joined) |
   | 14 | `"\/home\/<user>\/x"` (JSON-escaped slashes) | missed |
   | 15 | `%2Fhome%2F<user>%2Fx` (URL-encoded) | missed (caveat) |
   | 16 | `C:\Users\<user>\x` | missed (caveat) |
   | 17 | `file:///home/<user>/x` | caught |
   | 18 | `~<user>/dev` | missed |
   | 19 | `home/<user>/dev/x.txt` (relative, as tar and rsync print it) | missed |
   | 20 | `/var/home/<user>/x` | caught (contains `/home/`; the caveat lists it as not searched) |
   | 21 | `<user>@build` (the bare name) | missed (out of class) |
   | 22 | `/home/<user>/x` inside a `.gz` file | missed |
   | 23 | a tracked symlink to `/home/<user>/dev/protoemu` | missed: `hits()` skips symlinks (`:65`), but git publishes the target |
   | 24 | `/home/<user>/x` in UTF-16 | missed |
   | 25 | `a-home-<user>-dev` (after a letter) | missed (by design: the lookbehind) |
   | 26 | `/home/<user>.old/x` | caught |

   - The tree has no tracked symlink, compressed file or UTF-16 file today, so forms 22–24 are latent.
   - Worth adding, cheaply:
     - form 23: read a symlink's target with `os.readlink` and scan it as a line;
     - form 19: `(?<![A-Za-z0-9/])home/<name>/`, if it adds no false positive.
   - Forms 10, 14, 15, 16 and 18 are unlikely in this repo, and two of them are already in the caveats. I would note them in the docstring rather than match them.
3. **The allowlist: keep the names, but scope `runner1` (and preferably `ubuntu`) to `evidence/`.**
   - **Where each name is today**, from my pre-allowlist listing at `36c6c26`:
     - `runner1`: only under `evidence/` (the fabric-ping `model.log` ×3, `043-fabric-ping3.report.md`, `evidence/verifier/README.md`);
     - `ubuntu`: only under `evidence/`;
     - `pi`: `hw_day.py`, `test_hw_day.py` and `evidence/verifier/`;
     - `vscode`: `.devcontainer/Dockerfile`;
     - `x`: `test_flow_tools.py`;
     - `someone`: `evidence/verifier/`.
   - Each reason is correct. None of the six is the author's user, and all are generic or made up.
   - **`runner1`:** scope it. It is the build host's account, and the repo's convention for that host is paths relative to the remote home (`REMOTE_DIR`, `~/protoemu-flow/…`). So a new `/home/runner1/…` outside `evidence/` would be a script or doc hardcoding the private build host's layout, which the scan should stop. Today, scoping costs nothing.
   - **`ubuntu`:** global is acceptable for privacy. It is Ubuntu's default account and identifies no one, as the builder's caveat says. But every current hit is a verbatim board record under `evidence/`, and the host tools use `~`. Scoping it too would catch a hardcoded board path in new code. I recommend it; it is not required.
   - **`pi`, `vscode`, `x`, `someone`:** fine as names. If the allowlist becomes per-path, scope them to their current files.
   - A per-path allowlist (`name: (reason, path prefixes)`) costs a few lines. A test can show that `/home/runner1/x` in a file outside `evidence/` fails.
4. **Note on GTKWave.** `test/README.md:23` tells readers to open `tb.fst`, but `make` always passes `+DUMP_FILE=$(SIM_BUILD)/wave.fst` (`test/Makefile:104`), so no `tb.fst` is written. That is pre-existing, not this item's.

## Asked checks

- **`hits()` at `36c6c26` by hand** (`scan.py`: `2f63d7f`'s `test_home_paths.py` run on a scratch worktree of `36c6c26`): `['test/tb.gtkw:5', 'test/tb.gtkw:8']`, the TT template's author's home. ✓
- **Claim 2's list against every pre-allowlist match:**
  - `HOME.finditer` over all tracked files gives 52 matches in 21 files. File by file and name by name, the counts equal claim 2's list:
    - `vscode` 1, `runner1` 3 + 1 + 1, `ubuntu` in the kv260-usbfs evidence 7 + 3 + 1 + 1;
    - the verifier reports and READMEs as listed;
    - `pi` 1 + 1 in `hw_day.py` and `test_hw_day.py`, `x` 2, `uri` 2.
  - There is no `-home-`, `/Users/` or `/tmp/claude-` match.
  - Six more bare `/home/` mentions are prose with no name (`041`/`042`/`043` reports, `evidence/verifier/README.md`, the test's docstring). ✓
  - At `2f63d7f`, on a fresh scratch copy, `hits()` is `[]`. ✓
- **The allowlist:** finding 3.
- **Mutants** (runner1, my clone at `2f63d7f`, `python test/mutants.py "privacy-home-*" "privacy-scan-*" privacy-kria-default-host`): "7 of 7 mutants killed".
  - `privacy-home-path-default` and `privacy-home-scratch-in-readme`: killed by `test_no_home_path_in_tracked_files`.
  - `privacy-home-scan-no-dashed` and `privacy-home-scan-any-name`: killed by `test_a_planted_home_path_is_found`.
  - The three re-run privacy mutants: killed by their tests.
  - The planted user in `test/mutants.py` is made up (`jdoe`), split with `+`. ✓
- **Model suite** at `2f63d7f` (my runner1 clone; `remote_test.sh`'s pytest flags but `-n 8`, with `--ignore=test/model/test_swd.py`): rc 0, "3097 passed, 29 skipped in 726.71s (0:12:06)", as claimed. SIGROK checks skipped: 28, and a pass says nothing about them.
- **`tb.gtkw` in GTKWave** (runner1, OSS CAD Suite GTKWave v4.0.0-prealpha, headless under `xvfb-run`):
  - `make COCOTB_TEST_MODULES=test_pio WAVES=1` at `2f63d7f`: "TESTS=2 PASS=2", writing `sim_build/pio/wave.fst`. I copied it to `test/tb.fst`, the save file's relative dump name.
  - `gtkwave tb.fst tb.gtkw` and `gtkwave tb.gtkw` alone, from `test/`: each loads the dump ("FSTLOAD | Built 1905 signals and 333 aliases.") and runs for the full 15 s, with no save-file error. The control `gtkwave tb.fst /nonexistent.gtkw` prints "Error opening save file … skipping".
  - So the save file opens, and its relative `[dumpfile]` finds `tb.fst`.
  - This build has no Tcl `-S` option, so I could not list the displayed signals: the evidence is "opens without error", not a visual check.
- **077 nits.**
  - `fpga/kv260/README.md:328-329`: the ring line now says the log shows the three pairs and their messages, and that the rate, 115200, is `step_loopback`'s. The UART line says the log gives 9600 for uart_tx_pin (`board_smoke.log:188`, "at 9600 baud"), and that uart_rx_pin's 9600 is the code's (`step_uart_rx_pin(…, baud=9600)`). ✓
  - The doc test's `leaves_cores_to_default` flags all four of 076's bad lines, passes the three good ones, and finds nothing in either doc (in the suite). ✓
- **Privacy.**
  - `record_check.py --repo . --tree HEAD --commits next-8x4..HEAD` at `2f63d7f`: "tree HEAD: 747 files checked", "commit messages checked: 2", "TOTAL flagged: 0".
  - The author's user name is in none of the branch's added lines or messages, checked by a count against `$USER` without printing it.
  - The one home-form line in the messages is the class description `-home-<name>-`.
  - Both commits are by the noreply identity with the trailer. `git merge-base --is-ancestor b2fa7f3 HEAD` exits 1.
  - No `src/`, `flow/` or `docs/methodology.md` change.

## What I ran

- **Locally:**
  - `scan.py` (`hits()` and the raw listing at `36c6c26`);
  - `plant.py`: 26 forms in a scratch `git archive` with its own `git init`, since deleted;
  - `cand.py` (the candidate dashed class on both trees);
  - `record_check.py` and the name count.
- **runner1** (`~/protoemu-flow/verifier-078/`; no harden, no Vivado):
  - the model suite;
  - the mutants;
  - one `make` with waves, and GTKWave under `xvfb-run`.
- **No Kria.**

Summary: the scan is honestly described, and its tree audit is exact. But it misses the author's user name written as the home directory's dashed name (`-home-<user>/`, `-home-<user>` at the end of a line), the form that prompted the item. End the dashed class at the name (0 new false positives), plant that case, and scope `runner1` to `evidence/`. Reading symlink targets and relative `home/<user>/` paths would be worth adding too.
