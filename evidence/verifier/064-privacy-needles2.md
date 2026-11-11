# 064 privacy-needles2

Branch `privacy-needles2`, one commit `251b323` on `088d703`. It covers your 060 notes 1, 2 and 4. Only `test/model/test_private_hosts.py` and `test/mutants.py` change (+57 −11).

It merges cleanly with `usb-config-prestage` (062), which also edits `test/mutants.py` (`git merge-tree`).

Claims:
1. **Note 1, the Python pass.**
   - Every tracked `.py` file is also parsed with `ast`, and each string constant is searched whole, so adjacent literals are caught. The hit is reported at the constant's first line.
   - A file that doesn't parse skips this pass; the line scan still runs.
   - The planted test adds `j.py` (`X = "tail" "<random hex>"` on one line) and `k.py` (the same split across two lines in parentheses).
   - New mutant `privacy-scan-no-python-pass`, killed by the planted test.
   - The privacy mutants' made-up names were adjacent literals, so they would now fail the clean-tree scan. They use `+` instead.
   - Run over e449e40's two files in scratch, the scan flags exactly your 058 six constants.
2. **Note 4, IPv6.**
   - The pattern takes in the whole address after the prefix.
   - The /48 written as a class is allowlisted: prefix + `::`, `::/48`, or a trailing colon.
   - Planted real addresses (`::53`, a full bracketed address) are still flagged, and the class forms are added to the near misses.
   - New mutant `privacy-scan-v6-prefix-only`, killed.
3. **Note 2.** The made-up MagicDNS name is now `example-example`.
4. **Checks at 251b323.**
   - `git grep -inIE "[tailnet label's hex part redacted]"` finds nothing, in files or in the message.
   - The 26-encoding reconstruction scan over 704 files is clean.
   - The AST joined-literal check over 252 `.py` files is clean.
   - The clean-tree scan, with the new pass, reports no hits.
5. **runner1.**
   - Model: rc 0, 2880 passed, 29 skipped. The same as 088d703's own run (2880/29), so no tests were added or lost.
   - `mutants.py "privacy-*"`: 6 of 6 killed.

Asked checks:
- Rerun your note 1 plants (single-line and multi-line split literals) against the new scan. Try bytes literals, f-strings with a constant part, and concatenation with `+` (which the parser doesn't fuse). Say which forms are still blind, and whether that matters for the realistic risk: old files copied back from history.
- Check the IPv6 allowlist isn't wider than the class forms.
- Rerun the 6 privacy mutants.

The usual rules: `--ignore=test/model/test_swd.py` on any model run, and no Kria.
