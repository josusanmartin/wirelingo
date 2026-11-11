# 060 privacy-needles

Branch `privacy-needles`, one commit `6b78b89` on `e449e40`. Backlog item AJ: your 058 finding 1. The real tailnet label is held in six joinable string constants: `test/model/test_private_hosts.py:22,58,60,61,62` and `test/mutants.py:519`.

The commit changes only those two files (+73 −24). It merges cleanly onto improve-2026-11's current tip, `f8eed52` (`git merge-tree`).

The builder's claims:

1. **The tree scan is now a class search,** `test_no_tailnet_name_or_address_in_tracked_files`. It flags, in any case:
   - `tail` followed by 4 or more hex digits;
   - any `<label>.ts.net` name;
   - any address in 100.64.0.0/10;
   - any address under `fd7a:115c:a1e0:`.

   One allowlist entry, `100.64.0.0`, the base address that evidence READMEs quote as a class. `*.ts.net` and `TAIL…TS.NET` don't match, so the existing redaction notes pass. Each pattern is written so the test file doesn't match itself.
2. **Why not a hash or an environment variable.**
   - A salted hash of "tail" plus 6 hex digits falls to brute force, since there are only about 16.7 million labels.
   - An environment variable would skip the check in public CI.
3. **The planted-name test** plants nine files with labels generated at random on each run:
   - a full host name, its upper-case copy, and an admin-URL bare label;
   - a `.ts.net` name with no `tail` label;
   - addresses in 100.64.0.0/10, including both ends of the range, and an IPv6 one.

   It also plants a clean file of near misses, which must not match: `tail -f`, "tailored", "detail", 100.63.x and 100.128.x, an address inside a longer dotted number, 192.168.x, and the class mentions. Untracked files are still ignored.
4. **Mutants.**
   - `privacy-kria-default-host` now uses a made-up label.
   - New: `privacy-kria-magicdns-host` and `privacy-kria-tailnet-address`.
   - These three run only the tree scan (`-k no_tailnet`), so the scan itself is what kills them.
   - `privacy-kria-other-default` is unchanged.
5. **The label is not recoverable.**
   - `git grep -inIE "[tailnet label's hex part redacted]"` finds nothing.
   - A scan of all 700 tracked files found none of 26 forms of the label and its 6-digit part: plain, reversed, upper case, hex, base64 at every alignment, base32, rot13, sha256/md5/sha1, `\x` escapes and byte lists. The scan also tried each file with every separator removed, and with a Python file's literals joined.
   - It was shown to detect the label: on a scratch set of planted encodings it caught every one.
   - An AST check that joins literals finds your six constants at e449e40 and none at 6b78b89.
6. **Tests on runner1 at 6b78b89.**
   - Model suite: rc 0, 2878 passed, 29 skipped (a test was renamed, not added).
   - `mutants.py "privacy-*"`: 4 of 4 killed.
   - `"hw-*" "fab-*" "privacy-*"`: 124 of 124 killed.

Asked checks:
- **Rerun your AST and joined-literal scan at 6b78b89.** Also try any encoding of the label you can think of that the builder didn't.
- **Read the class patterns for false negatives against the real label's shape.** Would the old private label be caught if it were reintroduced: in a doc, a log, a URL, any case?
- **Read them for false positives** on today's tree, and confirm the allowlist is minimal.
- **Check that the merge onto `f8eed52` passes the scan.** The tidy merge removed `test/gl.log`.
- **Rerun the four `privacy-*` mutants.**

Out of scope: the label is also in old commits' history, which is item AI, the history rewrite. The builder also notes `/home/runner1` in 3 evidence lines and the `/home/<other>` template path. Say whether either matters for a public repo; the author has already decided to keep runner names.

The usual rules: `--ignore=test/model/test_swd.py` on any model run, and no Kria.
