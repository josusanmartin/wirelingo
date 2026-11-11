# 078b home-path-fix

Branch `as-home-path`, one new commit **`965a520`** on 078's tip `2f63d7f`
(base `next-8x4` `36c6c26`; `git merge-base --is-ancestor b2fa7f3 HEAD`
exits 1). It fixes 078's findings 1 to 4.

Evidence levels:
- **Model suite** and **mutants**, on runner1.
- The $USER plant: local plain Python on scratch repositories, never
  printed. pytest was not run locally.

No board, no SWD, no `src/`, `flow/` or methodology change, no push.

Files: `test/model/test_home_paths.py`, `test/mutants.py`, `test/README.md`.

`record_check.py --repo . --tree HEAD --commits next-8x4..HEAD`: 747
files, 3 messages, 0 flagged. A count of $USER in the commit's added lines
gives 0. Both scans (`test_home_paths.hits()`, `test_private_hosts.hits()`)
are empty on the tree.

The builder's claims:

1. **Finding 1: the dashed class.**
   - It is now your pattern: lookbehind `(?<![A-Za-z0-9-])`, then `-home-`
     and a name `[A-Za-z0-9_.]+`, then `(?![A-Za-z0-9_.])`.
   - New planted cases in `test_a_planted_home_path_is_found`:
     - `i.md`: `~/.claude/projects/-home-<made-up>/memory/MEMORY.md` (the
       name followed by `/`);
     - `j.md`: `-home-<made-up>` at the end of a line.
   - The clean file now also holds `--home-dir` and
     `privacy-home-path-default`, which must not match.
   - New mutant `privacy-home-scan-dashed-needs-dash` restores the trailing
     `-`, and is killed.
   - The docstring now describes the class as it is: alone, before `/`, at
     the end of a line, or before `-<more>`.
2. **Finding 2: other forms.**
   - **Form 23, symlinks:** a tracked symlink's `os.readlink` target is
     scanned as line 1.
     - The planted symlink `n.lnk` points to a made-up home and is
       reported. `ok.lnk` -> `../README.md` is not.
     - Mutant `privacy-home-scan-no-symlink` is killed.
   - **Form 19, relative paths: added.** The class `(?<![A-Za-z0-9/])home/`
     + name + `/` gives 0 matches on the tracked tree (`grep -P`, and
     `hits()` stays empty), so it adds no false positive.
     - Planted: `k.txt` (`tar: home/<made-up>/dev/x.txt: file changed`) and
       `l.md` (at the line start).
     - The clean file holds `a home/ directory` and `home/x (no slash ...`,
       which must not match.
     - Mutant `privacy-home-scan-no-relative` is killed.
   - **Not matched, now listed in the docstring:** forms 10 (`/HOME/`), 14
     (JSON-escaped), 15 (URL-encoded), 16 (`C:\Users`), 18 (`~<name>/`), 22
     (gzip) and 24 (UTF-16). Also 13 (a Python `+` string), 21 (the bare
     name) and 25 (after a letter), by design.
   - The `/var/home` caveat is corrected: the docstring now says
     `/var/home/<name>` (and `file:///home/<name>`) is caught.
   - **The real name** (`plant_user.py` in my scratch: `$USER`, one fresh
     `git init` per form, deleted after):
     - caught: 1–9, 11, 12, 17, 19, 20, 23 and 26;
     - missed: 10, 13, 14, 15, 16, 18, 21 and 25, which are exactly the
       documented ones.

     Forms 22 and 24 were not planted; they are documented.
3. **Finding 3: a per-path allowlist.** `ALLOWED` is now `name: (reason,
   path prefixes)`.
   - Where each name may appear:
     - `ubuntu` and `runner1`: `evidence/` only;
     - `pi`: `fpga/kv260/host/hw_day.py`, `test/model/test_hw_day.py` and
       `evidence/verifier/`;
     - `vscode`: `.devcontainer/Dockerfile`;
     - `someone`: `evidence/verifier/`;
     - `x`: `test/model/test_flow_tools.py`.
   - These cover every current hit (078's listing), and the tree scan is
     empty.
   - `test_the_allowlist_is_only_generic_names_in_their_files` shows:
     - `/home/runner1/x` fails in `fpga/kv260/build.sh`, in
       `docs/hardware-day.md` and with no path;
     - `/home/ubuntu/x` fails in `fpga/kv260/host/pe_kria.py`;
     - `/home/pi/x` passes in `hw_day.py` and fails in `pe_kria.py`;
     - `ubuntu2` and `piotr` fail, and so does `/Users/<other>`.
   - The planted test also plants allowed names outside their prefixes
     (`m.md`), reported, and inside them (`evidence/...`,
     `.devcontainer/Dockerfile`, `test/model/test_flow_tools.py`), not
     reported.
   - Mutant `privacy-home-allow-anywhere` (the prefixes ignored) is killed.
4. **Finding 4.** `test/README.md:23` now says that `make` writes
   `wave.fst` in its `$(SIM_BUILD)` directory (`+DUMP_FILE`), that `tb.v`
   writes `tb.fst` only without the plusarg, and how to open it with
   `tb.gtkw`.
5. **Mutants** (runner1 clone at `965a520`, test venv: `python
   test/mutants.py 'privacy-home-*' 'privacy-scan-*'
   privacy-kria-default-host`): **11 of 11 killed**. The 8 home-path
   mutants:
   - `-path-default` and `-scratch-in-readme`, killed by the tree scan;
   - `-scan-no-dashed`, `-scan-dashed-needs-dash`, `-scan-no-relative`,
     `-scan-any-name`, `-allow-anywhere` and `-scan-no-symlink`, killed by
     the planted test.

   The 3 re-run privacy mutants are killed too.
6. **Model suite** on runner1 at `965a520`: **3097 passed, 29 skipped**,
   the same count as `2f63d7f`: the tests were changed, not added.

Caveats:
- The allowlist's prefixes are file or directory prefixes. `evidence/`
  admits `ubuntu` and `runner1` in any new evidence file, which is the
  verbatim-record case they are for.
- Forms 22 and 24 (gzip, UTF-16) are documented, not tested: no such
  tracked file exists.

Asked checks:
- Your 26-form plant against `965a520`. Expect the misses to be exactly
  10, 13, 14, 15, 16, 18, 21, 22, 24 and 25 (the docstring's list); I did
  not plant 22 and 24.
- `cand.py`'s tree count for the new dashed and relative classes: 0.
- Mutants and the model suite as above.
- Read `ALLOWED`'s prefixes against the tree's hits.
- Privacy: `record_check.py`, and the $USER count over the commit.

No Kria, no SWD; use `--ignore=test/model/test_swd.py` on model runs.
