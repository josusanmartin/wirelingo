VERDICT: PASS

Commit 7d53740 on base bc98a97 (an ancestor). Three commits:
- **763e0af:** a85fc57 rebased;
- **ead2d02:** 039-041 copied, and 041's finding fixed;
- **7d53740:** evidence index rows and `test/model/test_evidence_index.py`.

Against bc98a97 the branch touches only `README.md`, `docs/eth10-network.md`, `evidence/` and the new test. Nothing in `src/`, `flow/`, `tools/` or `firmware/` changed.

041's finding is fixed. The 039-041 copies are exact, the rebase conflict was resolved without changing a word of the base's text, and the new index test fails when it should.

| Claim | Result |
|---|---|
| 1. "17 replacements on 15 lines of 4 files", naming the two double lines; the per-kind table says it counts kinds; item 041's claim 1 left verbatim, with the reason given | PASS |
| 2. Rebase: one conflict in `docs/eth10-network.md`; the base's paragraph kept; 029/031/034 links re-added; 037 note 1 linked | PASS: text word-for-word the base's once links are stripped |
| 3. 039-041 verbatim, cmp-checked, no new redactions; counts 42/42, 21 PASS, 21 FAIL; top-level counts updated; copy-time caveat; 000-029 concatenation sha256; a85fc57 the only commit not in this branch's history | PASS |
| 4. Index rows for the two new folders; both searched, only `/home/ubuntu`; index test with a scratch-tree check | PASS: not vacuous (below) |
| 5. Model suite 2543 passed, 29 skipped; links 0 broken; no tailnet label, build hostname or Kria MAC | PASS. My link count differs from theirs (note 1) |

## Checked

**Claim 1.**
- The sentence now reads "17 replacements on 15 lines of 4 files (one row per line; lines 9 of the 035 item and 14 of the 035 report carry two each)". That matches the 15-row table, `diff` against my inbox (15 changed lines) and the 17 bracketed notes.
- A line before the per-kind table says it counts kinds and that `035-hostname-fix.md:9` counts under two.
- The 039-041 section says item 041's claim 1 keeps "16 lines" as a verbatim record. That is the right choice for a copy.

**The asked check: the rebase conflict in `docs/eth10-network.md`.**
- **Compared word for word.** With every `[text](link)` reduced to its text, the file at 7d53740 is word-for-word the base's (5118 words each, `difflib` finds no difference).
- **What the diff adds.** Five links and nothing else: 029 (1), 031 (1), 034 (2) and 037 (1, at "verifier report 037, note 1"). All resolve.
- **`git range-diff e28a26b..a85fc57 bc98a97..763e0af`** shows the same: only the eth10-network hunk changed, onto the base's new paragraph.

**The asked check: the 039-041 copies and hashes.**
- **The copies.** `cmp` of all 84 committed `NNN-*.md` against my inbox gives 80 identical. The 4 that differ are the four listed redacted files of 030-038, unchanged since a85fc57. All 6 files of 039-041 are identical.
- **The 039-041 hashes.** The sha256 of each in my inbox, as committed and as written in the README agree for all 6 (`2d1a8e27…`, `80aa7f1b…`, `8734c99b…`, `7d6a1459…`, `5a7b9b36…`, `69982302…`).
- **The 000-029 concatenation.** `cat 0[0-2]?-*.md | sha256sum` in the C locale over the 60 files gives `f8293aa094267dad4f46266223bc03f0350f269abb1966b655f68721a568c561`, both in my inbox and in a `git archive` copy of 7d53740. That is the README's value.
- **Line references.** In the copies, `041-verifier-reports2.report.md:41` holds the kept emails, `:42` the locally administered MAC, and `041-verifier-reports2.md:5` the kept emails and the words `[build host provider redacted]` and `fastmail`. So the README's line numbers are right.

**Redactions: 039-041 and the two newly indexed folders.**
- **039-041:** only kept items, as the README lists:
  - emails only at report 041 `:41` and item 041 `:5`;
  - MAC only `02:00:de:ad:be:01`;
  - `100.64.0.0`, `[tailnet domain redacted]`, `ssid` and `psk` only as classes searched for;
  - `/home/` only `/home/ubuntu`, plus the `/home/pi` and `/home/someone` that report 041 quotes from 000-029.
- **`kv260-usbfs-zlp-20261103/` and `model-usb-fs-enum-sweep-20261104/`:** no email, no MAC, no private or tailnet address, no SSID, `psk` or provider name. `/home/ubuntu` appears 12 times. As the evidence README says.
- **The whole tree at 7d53740** (`git grep -i -F`, binaries included, counts only):
  - the Kria's MAC with colons, dashes, bare, and its NIC half: none;
  - the tailnet label, build hostname, provider domain and author address: none;
  - the local home directory: only `test/gl.log`, the known unfixed item.

**Counts.**
- **The table.** 42 rows. Each verdict equals the first line of my report, and the count is 21 PASS, 21 FAIL.
- **History.** Every commit in the table is an ancestor of 7d53740 except `a85fc57`, as the README says.
- **Elsewhere.** `README.md:50` and `evidence/README.md:9` say 000-041, 21 PASS and 21 FAIL.

**The asked check: is the index test vacuous?**
- **How.** In a `git archive` copy of 7d53740, run as `python3 -I test/model/test_evidence_index.py` (no pytest needed).
- **Stock:** it passes.
- **Each of these fails it:**
  - one row removed;
  - a row duplicated;
  - a row linking another folder's README;
  - a new folder with no row;
  - a listed folder's README deleted;
  - a row written `[name]` without the slash.
- **Restored:** it passes again.
- **The scratch-tree test** also checks a missing row and a wrong link name, and passes in the suite.
- **The new rows.** `kv260-usbfs-zlp-20261103/` says RTL in FPGA fabric, KV260, protoemu48 `60b0692`, as its own README does (`:3`). `model-usb-fs-enum-sweep-20261104/` says cycle model, 40 enumerations at five timings, as 040 found.

**Claim 5.**
- `remote_test.sh model` at 7d53740: `2543 passed, 29 skipped`.
- **My link check** over `README.md`, `docs/eth10-network.md`, `evidence/README.md`, `evidence/fabric-ping-20261103/README.md` and `evidence/verifier/README.md` (relative targets, with `#anchors` checked against headings): `169 relative links, 0 broken`, plus 2 web links not followed.

## Notes

1. **The link counts don't agree.** The item gives "166 links in 5 changed .md files + 7 on evidence/README.md". I count 169 relative links (171 with the two web links) over the same five files, evidence/README's 7 included. Probably a different counting rule. Nothing is broken under any of them, and the figure appears in the item, not in a tracked doc.
2. **The index regex has edge cases.** It reads any line in `evidence/README.md` that starts `| [name/](...)`, so such a line outside the table, or inside a code block, would also count as a row. Nothing like that is there today.

## What I ran

**On runner1:** `remote_test.sh model` at 7d53740: `model rc=0`, `2543 passed, 29 skipped in 1050.52s (0:17:30)` (checkout `agent-runs/protoemu-verify-7d53740-2146321`, removed).

**Locally** (on a `git archive` copy in my scratchpad):
- `cmp` of the 84 copies;
- the sha256s and the 000-029 concatenation;
- the link-stripped word comparison of `docs/eth10-network.md` and the `range-diff`;
- the pattern sweeps and the whole-tree value search, printing counts only;
- the table checks;
- the six index-test mutations and the restore;
- the link check.

I did not touch the Kria or SWD, and nothing was pushed.
