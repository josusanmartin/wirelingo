VERDICT: PASS

078b home-path-fix (branch `as-home-path`, `965a520` on 078's tip `2f63d7f`, base `next-8x4` `36c6c26`; backlog AS): verifier report.
Kria: verifier does not touch it. No SWD.

078's four findings are fixed, and every claim reproduces:
- the dashed class is the proposed one, and its two planted cases are in;
- tracked symlink targets are scanned;
- the new relative class adds no false positive;
- the per-path allowlist covers every current match;
- `test/README.md` names `wave.fst`;
- 11 of 11 mutants are killed;
- the model suite gives 3097 passed, 29 skipped.

I re-ran my 26-form plant of the author's real local user name. It misses exactly the 10 forms the docstring now lists as unmatched: 10, 13, 14, 15, 16, 18, 21, 22, 24 and 25.

Ten further forms I tried are in finding 1. Three of them are missed, and the docstring doesn't list them. They are coverage notes, not a false claim: the docstring says what is read and matched, and that holds.

Evidence levels: model suite and mutants (runner1); the plants and scans are local Python on scratch copies. No board.

## Findings

1. **Minor: three forms are missed that the docstring doesn't list.** They are from a second plant of the real name, forms 27–36 (scratch `git archive` of `965a520`, fresh repository, deleted after):

   | # | form | result |
   |---|---|---|
   | 27 | `rsync: home/<user>` (relative, no `/` after the name) | **missed** |
   | 28 | symlink -> `../../home/<user>/x` | caught |
   | 29 | symlink -> `-home-<user>/memory` | caught |
   | 30 | `./home/<user>/dev/x` | caught (the `/home/` class) |
   | 31 | a tracked file at `qhome/<user>/notes.md` (the name in the path, clean content) | **missed** |
   | 32 | a tracked file at `x27/-home-<user>-dev.md` | **missed** |
   | 33 | `user-home/<user>/x` | caught |
   | 34 | adjacent `.py` literals across two lines | caught |
   | 35 | `/home/<user>` before CRLF | caught |
   | 36 | `/home/<user>/x` in an `.ipynb` | caught |

   - **27:** the relative class requires a `/` after the name (`test_home_paths.py:52`), by design: the clean file's `home/x` must not match. `tar` and `rsync` print a directory entry with its trailing `/` (`home/<user>/`), so the common case is caught. The docstring's class line (`:14`) already says `home/<name>/`; listing the bare form under "Not matched" would make that explicit.
   - **31, 32: tracked paths are not scanned.** `hits()` reads contents and symlink targets but never the path that `git ls-files` returns (`:91-103`). Git publishes paths too, the same reason the symlink targets are read. No tracked path has a home form today: `git ls-files | grep -E 'home|Users|claude-[0-9]'` gives only `test/model/test_home_paths.py`, and the name count over the paths is 0.
     - **Suggestion:** also run `home_paths(os.fsencode(name), name)` on each path, reported as `name:0`. Commit messages are also outside this test and outside record_check's classes. The 077 finding 1 caveat still holds for them, and here they are clean by my count (below).
2. **Minor: two parts of the patterns are redundant (mutants survive, as equivalent mutants).**
   - **The dashed class's lookahead `(?![A-Za-z0-9_.])`** (`:53`) never changes a match: the greedy `[A-Za-z0-9_.]+` before it always stops at a non-name character or the end. Removing it: 3 passed.
   - **The relative class's `/` in its lookbehind** (`:52`) only keeps `/home/<name>/` from matching twice. The `/home/` class already reports that line, under the same allowlist, so `hits()` is unchanged. Removing it: 3 passed.
   - Both are harmless and can stay. The `/` is worth a comment, since without it the relative class re-flags every allowed `/home/` path.
3. **Note: allowlist prefixes are plain `startswith`.** For example, `fpga/kv260/host/hw_day.py` also admits a `hw_day.py.orig`, and `.devcontainer/Dockerfile` admits `Dockerfile.dev`. With the current entries that only admits the same generic names in sibling files, so it is harmless. The builder's caveat on `evidence/` is accurate.
4. **Heads-up for the orchestrator: three records would fail the new scan if copied into `evidence/verifier/`.** I ran `home_paths()` over my inbox, using each file's would-be `evidence/verifier/` path:
   (Below, each path is written as `/home/` + name, so this report doesn't match.)
   - **`077-kv260-3core-fix.report.md:94`:** `/home/` + `uri`, `vscode` and `x`;
   - **`078-home-path-scan.md`:**
     - `:47`, `/home/` + `runner` (a partial name in a mutant description);
     - `:58`, `/Users/` + `pi`;
     - `:61`, `/home/` + `uri`;
     - `:67`, `/home/` + `vscode`;
     - `:76`, `/home/` + `x`;
   - **`078b-home-path-fix.md:79`:** `/Users/` + `pi`.

   075–076, my 078 report and this report are clean. This is the scan working (fail-closed). `uri` is a third party's name from the template's old `tb.gtkw`. Redact these when copying, for example to `/home/<other>`.

## Asked checks

- **The 26-form plant at `965a520`** (`plant.py`, real name from `$USER`, never printed; `git archive` of `965a520` into a fresh `git init`, deleted after):
  - caught: 1–9, 11, 12, 17, 19, 20, 23, 26;
  - missed: 10, 13, 14, 15, 16, 18, 21, 22, 24, 25.

  These are exactly the expected misses, and they match the docstring's list (`:31-35`). I also planted 22 (gzip) and 24 (UTF-16); both are missed, as documented. There were 0 hits outside the planted files.
  - Against 078: forms 5 and 6 (dashed), 19 (relative) and 23 (symlink) are now caught.
- **Finding 1's class** (`:53`): it is the proposed pattern.
  - The planted cases are in: `i.md` (`-home-<made-up>/memory`) and `j.md` (end of line).
  - `clean.md` holds `--home-dir` and `privacy-home-path-default`.
  - The new mutant is killed (below). My mutant that drops the `-` from the lookbehind is killed too: 2 failed.
- **Tree counts at `965a520`** (`scan2.py`, every class's raw matches with the allow decision):
  - class 0 (`/home/`): 50 matches in 20 files;
  - class 1 (relative): 0;
  - class 2 (dashed): 0;
  - `/Users/`: 0;
  - `/tmp/claude-`: 0;
  - tracked symlinks: none.

  This is 078's 52 in 21 files minus `tb.gtkw`'s two, which `2f63d7f` fixed. The only relative-looking text in the tree is `test_home_paths.py:140`'s `home/x `, which has no `/` after it, so it is not matched. `hits()` is `[]`.
- **`ALLOWED`'s prefixes against those 50 matches:** every one is inside its name's prefixes.
  - `ubuntu` (23) and `runner1` (5) are all under `evidence/`;
  - `pi` (14) is in `hw_day.py`, `test_hw_day.py` and `evidence/verifier/`;
  - `someone` (5) is in `evidence/verifier/`;
  - `vscode` (1) is in `.devcontainer/Dockerfile`;
  - `x` (2) is in `test_flow_tools.py`.

  `test_the_allowlist_is_only_generic_names_in_their_files` asserts what the item says (`:161-177`). The planted `m.md` (allowed names outside their prefixes) is reported. My mutant that makes the prefix check a top-directory substring is killed: 1 failed.
- **Symlinks** (`:93-96`): the target is read with `os.readlink` and scanned under the link's own path.
  - `n.lnk` is reported; `ok.lnk` -> `../README.md` is not.
  - My mutant that drops the symlink branch, so links fall through to a plain read, is killed: 1 failed.
  - So is the builder's `-scan-no-symlink`.
- **`.py` constants** now pass the path to the matcher (`:102`). My mutant without the path is killed: 2 failed. It flags `test_flow_tools.py`'s allowed made-up HOME (name `x`) in the tree, and the planted copy too.
- **Finding 4** (`test/README.md:23`), checked against the code:
  - `test/Makefile:104` adds `+DUMP_FILE=$(SIM_BUILD)/wave.fst` unconditionally;
  - `SIM_BUILD` is `sim_build/rtl_…` or `sim_build/gl` (`:47`, `:57`);
  - `tb.v:12` falls back to `tb.fst` only without the plusarg;
  - `tb.gtkw` names `tb.fst` and `tb.gtkw` relatively (`:5`, `:8`). Passing the dump on the command line, as the README now says, is what I showed opens in 078.
- **Mutants** (runner1 clone at `965a520`, `python test/mutants.py 'privacy-home-*' 'privacy-scan-*' privacy-kria-default-host` with the test venv): "11 of 11 mutants killed", each by the test the item names. The clone was clean after the run (`git status --short` empty).
  - My 7 extra mutants (`mymut.py`): 5 killed, plus the 2 equivalent survivors of finding 2.
  - One of the 5, the relative class without its trailing `/`, is killed (2 failed) by the clean file's `home/x`.
  - Unmutated: 3 passed.
- **Model suite** (runner1 clone at `965a520`; `remote_test.sh`'s pytest flags with `-n 8` and `--ignore=test/model/test_swd.py`): rc 0, "3097 passed, 29 skipped in 591.28s (0:09:51)", as claimed. SIGROK checks skipped: 28, and a pass says nothing about them.
- **Privacy:**
  - `record_check.py --repo . --tree HEAD --commits next-8x4..HEAD` at `965a520`: "tree HEAD: 747 files checked", "commit messages checked: 3", "TOTAL flagged: 0", as claimed.
  - `test_private_hosts.hits()` on the tree: `[]`.
  - Case-insensitive name count, 0 in each: the commit's added lines, the messages `36c6c26..965a520`, and the tracked paths.
  - All 3 commits are author and committer noreply, with the trailer.
  - `git merge-base --is-ancestor b2fa7f3 HEAD` exits 1.
- **Scope:** only `test/model/test_home_paths.py`, `test/mutants.py` and `test/README.md` change. No `src/`, `flow/`, methodology or board file.

## What I ran

- **runner1** (`~/protoemu-flow/verifier-078b/`, two clones of `965a520` from a bundle; no harden, no Vivado; AP's harden was running and was not touched):
  - the model suite;
  - `mutants.py`, then `mymut.py`.

  After the runs I removed the clones and my run's `/tmp/protoemu-mutants-rbc_1xhk` scratch. Logs and scripts remain (32K).
- **Locally:**
  - `scan2.py` on the tree;
  - `plant.py` (078's 26 forms) and `plant_extra.py` (forms 27–36) on scratch copies with fresh repositories, deleted after;
  - `record_check.py`, `test_private_hosts.hits()`, the name counts and ancestry;
  - `home_paths()` over my inbox.
- **No Kria.**

Summary: PASS. The dashed class now catches the home directory's own dashed name. Relative paths and symlink targets are scanned, and the allowlist is scoped per path, with every current match inside its prefixes. The real name's misses are exactly the documented list. Remaining gaps: tracked path names and a bare relative `home/<user>` (finding 1). Also, three inbox records need redacting before they are copied into `evidence/verifier/` (finding 4).
