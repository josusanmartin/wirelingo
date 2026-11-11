VERDICT: PASS

Item 064 `privacy-needles2`: commit 251b323 on 088d703, checked out detached. I read the diff (2 files, +57 −11: `test/model/test_private_hosts.py` and `test/mutants.py`) and nothing of the builder's reasoning. Kria: verifier does not touch it. No SWD.

060's notes 1, 2 and 4 are fixed.
- The new Python pass catches the old label in every Python form the parser joins, including the old files copied back from history.
- The forms it can't see need deliberate code to build the label, and no realistic copy-back has them.
- The IPv6 allowlist admits exactly the three class forms and no address.
- The six privacy mutants are killed.
- The tree is clean, alone and merged with 062 and 063.

## Findings

None blocking.

### Notes

1. **Still blind, by design: a label built at run time.** In a throwaway repo with the real old label (read from e449e40 at run time, as in 060), the scan does not see:
   - `"tail" + "<hex>"`;
   - `'%s%s' % ("tail", "<hex>")`;
   - `''.join([...])`;
   - an f-string whose placeholder splits the label (`f"tail{''}<hex>"`);
   - a `.py` file that doesn't parse (its line scan still runs).

   Each needs code written to assemble the name, which is what the test itself and `mutants.py` now do on purpose (with `+`). The realistic risk, old files copied back, is caught (claim 1). All 252 tracked `.py` files parse under runner1's Python 3.12, so none falls back to the line scan alone.
2. **A /64 inside the tailnet's range is flagged** (`<prefix>:1::/64`), and so is an all-zero host form other than the three allowlisted ones. This is fail-safe: a writer who needs such a form gets a hit and rewords. Nothing in the tree has one.

## The builder's claims

1. **The Python pass: holds.**
   - `python_constant_lines` walks `ast.Constant` str and bytes nodes, and adjacent literals arrive as one constant. It reports the constant's first line, and returns nothing for a file that doesn't parse.
   - Plants (real label, `hits()` on a scratch repo). Caught:
     - adjacent str literals on one line;
     - adjacent literals across lines in parentheses;
     - adjacent bytes literals;
     - a backslash line continuation inside one literal;
     - a plain literal adjacent to an f-string;
     - a placeholder-free run inside an f-string;
     - a comment, by the line scan.
   - Not caught: the run-time forms of note 1.
   - Old files: e449e40's `test_private_hosts.py` and `mutants.py` placed in the scratch repo are flagged at exactly 058's six lines, `test_private_hosts.py` 22, 58, 60, 61, 62 and `mutants.py` 519. The line scan alone finds 58, 60, 61 and 519, because their `<hex>.ts.net` tail matches the MagicDNS branch. The AST pass adds 22 and 62, the two split bare-label constants.
   - The planted test's new `j.py` (one line) and `k.py` (across lines) are in the diff, and `privacy-scan-no-python-pass` is killed by that test (below).
   - The three privacy mutants' made-up names now use `+` (`mutants.py:521-527`), so neither the line scan nor the AST pass sees them in the source. Their applied text still trips the tree scan, which kills each.
2. **IPv6: holds, and the allowlist is no wider than the class.**
   - The branch is now `<prefix>[:][0-9a-f:]*(?:/[0-9]+)?`, which takes the whole address. `CLASS_ONLY` adds exactly `<prefix>:`, `<prefix>::` and `<prefix>::/48`.
   - Not flagged: the three class forms, the `::/48` range, `::` in either case, and a trailing colon.
   - Flagged:
     - a full address;
     - `::53`;
     - `::1/128`;
     - a `/64`;
     - an all-zero host written out;
     - an IPv4-mapped address;
     - an address with a zone (`%eth0`).
   - The only allowlisted strings are the network base itself, which is never a host's address.
3. **The made-up MagicDNS name: `example-example`.** It is in the test (`e.md`) and in `privacy-kria-magicdns-host`.
4. **The checks at 251b323: hold.**
   - `hits(ROOT)` is `[]`.
   - My 41-form encoding scan (060's, plus the joined-constant and separator-free views) finds none of the forms in any of the 704 tracked files. It opened one compressed member, the PNG `zTXt` chunk of `docs/fo6-layout.png`, which is clean.
   - The commit message has 0 private-pattern hits and ends with the Co-Authored-By line.
5. **runner1: holds.** Model: 2880 passed and 29 skipped, rc 0, the builder's figure and 088d703's own count. `mutants.py "privacy-*"`: 6 of 6 killed. The four tree-scan mutants are killed by `test_no_tailnet_name_or_address_in_tracked_files`. The two new scan mutants are killed by `test_a_planted_name_in_a_tracked_file_is_found`. `privacy-kria-other-default` is killed by `test_install_refuses_with_no_host`.

## Asked checks

- **My note 1 plants, rerun:** caught (claim 1).
- **Bytes literals, f-strings and `+`:**
  - bytes: caught;
  - an f-string with a constant run holding the label: caught;
  - a label split by a placeholder: blind;
  - `+`: blind.

  It matters little for the realistic risk (note 1).
- **IPv6 allowlist:** exactly the class (claim 2).
- **The 6 privacy mutants:** 6 of 6 killed.
- **Merges.** In a throwaway runner1 clone, 251b323 merged with ab2db8e (062) and 82bf76f (063), in either order, is conflict-free and gives the same tree, `[host-only tree id redacted]`. On the merged tree, `hits()` gives `[]`, so 062's additions to `test/mutants.py` don't trip the new Python pass.

## What I ran

- **Model suite.** `remote_test.sh model` at 251b323 (runner1; `--ignore=test/model/test_swd.py` is built in): rc=0, "2880 passed, 29 skipped in 443.06s (0:07:23)", with "SIGROK CHECKS SKIPPED: 28 tests".
- **Plants.** `plant2.py` on runner1, a throwaway repo with the real old label read from e449e40 at run time. Result: "mismatches 0 of 24", the two old files' lines as above, "tree hits: []", "py files 252 unparsable: []".
- **Privacy mutants.** `python test/mutants.py "privacy-*"` in the clone: "6 of 6 mutants killed".
- **Encoding scan.** 060's `needles.py` at 251b323: "files 704 … hits by form: none".
- **Merges.** Both orders with ab2db8e and 82bf76f, then `hits()` on the result.
