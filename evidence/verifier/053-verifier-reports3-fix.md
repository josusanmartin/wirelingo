# 053 verifier-reports3-fix

Branch `verifier-reports3`, new commit `f28e573` on `fff2b3f` (your 051, FAIL). It changes `evidence/verifier/README.md` only. The branch base is still `d7f177d`. improve-2026-11 has since moved to `9ca49ef` with the demo-notes merge, which doesn't touch `evidence/`.

The builder claims:

1. **Finding 1, the false sentence (`:267-271`).** The false parenthesis is removed. The text now says:
   - the 18 files 042-050, as committed, pass the search;
   - the tailnet label and the provider hostname are not written in this README;
   - the bare tailnet domain is, but only as a class searched for, as in 030-041.
2. **Finding 1, the inconsistency sentence (`:274-281`).** It now lists every place the class mention is kept unredacted:
   - 030 (item and report), 035 (item and report), 038 (report), 041 (report);
   - this README at `:130`, `:157` and `:235`;
   - `evidence/README.md:24`.
3. **Note 2 (`:56-59`).** `1550601` is now described with `afe09457da93fe81d` as a builder's id and checkout number, in the path report 043 quotes. The verifier's own checkout suffixes are named separately.
4. **Note 1 (`:295`).** The `/home/ubuntu` kept entry also cites item 042 `:6` and report 042 `:17`.
5. **Searches and checks.**
   - `git grep -inIc "ts\.net"` gives 3 lines in `evidence/verifier/README.md` and 1 in `evidence/README.md`, which matches the text.
   - The tailnet label and the provider hostname appear in neither README. The private-string grep over the 18 files still finds nothing.
   - The index test passes. No test changed, so the builder ran no model suite.

Asked checks:
- Verify that every statement in the README about the privacy search is now true, including any you didn't flag in 051.
- Check that the diff `fff2b3f..f28e573` touches only the lines claimed.
- Check that the branch still merges cleanly onto `9ca49ef`: run `git merge-tree` and the index test on the result.

As always, add `--ignore=test/model/test_swd.py` to any model run.
