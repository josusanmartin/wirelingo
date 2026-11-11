VERDICT: PASS

Commit f28e573 on fff2b3f (report 051), one commit. It changes `evidence/verifier/README.md` only, in four hunks: `:56-59`, `:268-271`, `:274-281` and `:295`. No copy, hash row, test or code changes.

051's finding is fixed. Every statement about the privacy search in the verifier README, and in `evidence/README.md`'s redaction paragraph, now matches the files. The branch merges onto `9ca49ef` without conflict.

| Claim | Result |
|---|---|
| 1. The false parenthesis removed; the 18 files pass the search; label and hostname not in this README; the bare domain only as a class | PASS |
| 2. The kept class mentions listed: 030, 035 (items and reports), 038 and 041 (reports), this README `:130`, `:157`, `:235`, `evidence/README.md:24` | PASS for the verifier files and the two READMEs (note 1 on one more folder) |
| 3. `1550601` described with `afe09457da93fe81d` as a builder's; the verifier's own suffixes named separately | PASS |
| 4. `/home/ubuntu` also cites item 042 `:6` and report 042 `:17` | PASS |
| 5. `git grep -inIc "ts\.net"` 3 and 1; label and hostname in neither README; private-string grep clean; index test passes | PASS |

## Checked

**The diff.**
- `git diff -U0 fff2b3f f28e573` has exactly the four hunks the item cites:
  - the non-commit sentence at `:56-59`;
  - the search sentence at `:267-271`;
  - the redaction sentence at `:274-281`;
  - the `/home/` kept entry at `:295`.
- Nothing else in the file moved apart from the line shift, and no other file changed.
- So the 18 copies and the sha256 table are as I checked them in 051.

**Claim 5's numbers.**
- `git grep -inIc "ts\.net"` at f28e573 gives 3 for `evidence/verifier/README.md`, at `:130`, `:157` and `:235`, as the new text says.
- It gives 1 for `evidence/README.md`, at `:24`.
- The README's description of each is right:
  - `:130` and `:157` are `*.ts.net` in the 000-029 and 030-038 tables;
  - `:235` is bare, in the 039-041 list;
  - `:24` is the redaction summary.

**The asked check: every privacy-search statement in the README is now true.**

I swept the 102 committed files, grouped by section, and the three READMEs. The sweep printed counts and kept values only, and covered:
- the Kria's eth0 MAC in five forms;
- the tailnet label, the provider hostname and domain;
- the local home directory, also in dashed form, and the user name other than `josusanmartin`;
- the user's email address and `claude-1000`;
- the bare domain;
- an email regex, IPv4 (with 100.64/10 and 192.168 split out), a MAC regex and `/home/` paths;
- `ssid`, `psk`, `password` and private-key headers.

- **000-029** (the README's first table, and "a later search … found none"):
  - nothing private;
  - no email (no `@` that forms one), no MAC, no private address;
  - `/home/` only `/home/pi` and `/home/someone`;
  - IPv4-like only `0.0.0.0` and `3.4.1.1`. Both are public, non-private values and not in either searched range.
- **030-038** (the second table):
  - after the 17 redactions, 0 hits for every private value;
  - the email set is exactly the seven kept ones the table names;
  - MAC only `02:00:de:ad:be:01` (plus the truncated `02:57:4c:…`);
  - `100.64.0.0` only as the class;
  - `/home/` only `/home/ubuntu`;
  - the domain only as `*.ts.net` classes, or in 035's lines where the label is redacted. 038's `KRIA.TAIL…TS.NET` is elided.
- **039-041** (`:221-239`), each bullet true:
  - emails only at item 041 `:5` and report 041 `:41`;
  - MACs only report 041 `:42`;
  - `/home/` only `/home/ubuntu` and report 041's quotes of `/home/pi` and `/home/someone`;
  - `josusanmartin`, `[build host provider redacted]` and `fastmail` only at item 041 `:5`;
  - `ssid`, `psk` and the domain only as classes.
- **042-050:**
  - 0 private hits, and 0 for the domain, so "the 18 files … as committed, pass that search" holds;
  - kept: `100.64.0.0`, `7.1.18.1`, `02:00:de:ad:be:01`;
  - `/home/` at exactly item 042 `:6`, report 042 `:17`, `:43`, `:44` and report 043 `:22`, `:23`, which is the entry at `:294-296`.
- **The READMEs:**
  - label and provider hostname: 0, as `:269-270` now says;
  - their emails, MACs and addresses are the kept values they quote.
- **`evidence/README.md:24-30`.** "17 in verifier files 030-038 (none in 039-041, and 2 in 042-050)" matches the three sections. "Runner names such as `runner1` and paths under `~/protoemu-flow` are kept" matches what is kept.

**Claim 3.**
- `1550601` and `afe09457da93fe81d` occur only in report 043's quoted path `agent-afe09457da93fe81d-c108e09-1550601`, the builder agent's checkout.
- The remaining suffixes in 042-050 are the verifier's own `agent-runs/protoemu-verify-<sha>-<n>` checkouts:
  - `2146321`, `2471574`, `2626905`, `2656770`, `2682111`, `2744963`, `2842899`.

**The asked check: does the branch still merge onto `9ca49ef`?**
- `9ca49ef` contains `d7f177d`. Its changes since then touch no file under `evidence/` and not `README.md` (0 of 3 files).
- `git merge-tree --write-tree 9ca49ef f28e573`: rc 0, no conflicts, tree `2869349a…`.
- That tree differs from `9ca49ef` only in the branch's 21 files. Its `evidence/` and `README.md` are identical to f28e573's.
- `python3 -I test/model/test_evidence_index.py` on a `git archive` of the merged tree: `evidence/README.md: one row per folder`, rc 0. It also passes at f28e573.

**Mutation:** nothing here is code, and no test reads the README's privacy statements. They hold because they were checked by hand, above.

**Timing check against the ISA:** not applicable; a README only.

## Notes

1. **One more folder keeps the class mention.** The item says claim 2 lists "every place" the class mention is kept. The tree also has `*.ts.net` at `evidence/kv260-usb-20261030/README.md:65`, that folder's own redaction note. `test/model/test_private_hosts.py` and `test/mutants.py` also have split needles. The README sentence doesn't say "every", and its scope is the verifier files and the READMEs that describe them, so the doc is right. Only the item's word is too wide. My 051 report didn't name that folder either.

## What I ran

**Locally** (worktree detached at f28e573):
- the diff and its hunks;
- `git grep -inIc "ts\.net"` and every match in context;
- the sweep of the 102 files and the three READMEs (counts and kept values only);
- the line checks of the 039-041 and 042-050 kept lists;
- `git merge-tree --write-tree 9ca49ef f28e573` and the index test on an archive of the result;
- the index test at f28e573.

**On runner1:** `remote_test.sh model` at f28e573 (`--ignore=test/model/test_swd.py`): `model rc=0`, `2867 passed, 29 skipped in 637.63s (0:10:37)`. That is the same as fff2b3f. `test_private_hosts` and the index test are included. The checkout, `agent-runs/protoemu-verify-f28e573-3430553`, is removed.

No SWD test ran, I did not touch the Kria, and nothing was pushed.
