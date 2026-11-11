VERDICT: FAIL

Commit fff2b3f on d7f177d (improve-2026-11, after the usb-in-setup merge). Two commits:
- **8f7fad1:** the 18 files 042-050 and the `evidence/verifier/README.md` update;
- **fff2b3f:** the coverage lines in `README.md` and `evidence/README.md`.

Only those three READMEs and the 18 new files change. No test, `tools/`, `firmware/`, `src/` or `flow/` change.

The copies, hashes, counts, table rows and the list of unreachable commits all check exactly. The FAIL is one false sentence in the new section about the privacy search (finding 1).

| Claim | Result |
|---|---|
| 1. 18 files, byte-identical to the inbox apart from the redaction lines; sha256 of each, in the inbox and as committed | PASS |
| 2. Exactly 2 redactions, 042 report `:42` and 043 report `:24`; 030, 035, 038 and 041 keep the same class mention, and the README says so; nothing else needed redacting, and the README lists what was kept | **FAIL** (finding 1). The redactions and the kept list check |
| 3. No Markdown links outside code spans in 042-050 | PASS |
| 4. 51 items, 51 reports, 27 PASS, 24 FAIL; the `grep -v report` trap | PASS |
| 5. Six unreachable commits, what each became, complete for 000-050 | PASS |
| 6. Coverage lines updated; no other stale count; `docs/rules-check.md:154` left as a dated quote | PASS |
| 7. Index test passes; the private-string grep finds nothing in the added lines; model 2867/29 at 8f7fad1 | PASS (the suite run at fff2b3f, below) |

## Findings

1. **The README says it passes the privacy search, and it doesn't.**
   - **The sentence.** `evidence/verifier/README.md:265-268` says 042-050 were searched "with a case-insensitive `git grep` for the tailnet label, the build machine's provider hostname and the tailnet domain (none of the three is written here, so that this README passes the same search)".
   - **What the README contains.** The tailnet domain is in it three times: `:129` and `:156` (the 000-029 and 030-038 tables, as `*.ts.net`) and `:234` (the 039-041 kept list, bare). `git grep -c -i` for the bare domain at fff2b3f gives 3 for this README, and 1 for `evidence/README.md` (`:24`).
   - **It is the same needle.** The section's own reason for the two redactions (`:272-273`) is that this `git grep` must find nothing in the added files. Those two lines quote the bare domain, so the search includes it.
   - **Why it blocks.** The sentence states the result of a privacy check on a file meant for publication, and that result is wrong. The added lines themselves are clean (below), and the three mentions are old text naming a class, not a host. So nothing private leaks; the record of the search is what's wrong.
   - **Fix.** Say what is true. Either: the label and the hostname are not written here, and the bare domain appears only as a class searched for, as in 030-041. Or: the added files pass the search. Claim 2's "deliberate inconsistency" sentence (`:270-272`) could also name this README and `evidence/README.md:24`.

## Checked

**The asked check: `cmp` of every copy, and the diff of the two redacted ones.**
- `cmp` of the 18 committed files against my inbox: 16 identical.
- The 2 that differ, each one line:
  - `042-verifier-reports2-fix.report.md:42`;
  - `043-fabric-ping3.report.md:24`.
- In each, `diff` shows only the bare tailnet domain inside its backticks replaced by `[tailnet domain redacted]`. Nothing else on the line changed.
- **Hashes.** All 18 README rows: the sha256 of my inbox file equals the left column, and the committed file equals the right (or "same"). 0 mismatches.
- **Committed bytes.** The blob at fff2b3f equals the checked-out file for all 18 (`text` unset under `evidence/`).
- 103 files in the folder: 51 × 2 plus the README.

**The asked check: the verdicts, recounted.**
- 51 `NNN-*.md` that are not `.report.md`, and 51 `.report.md`.
- `head -qn1 0*.report.md | sort | uniq -c`: `27 VERDICT: PASS`, `24 VERDICT: FAIL`, nothing else.
- `ls 0*.md | grep -c report` gives 53, from `041-verifier-reports2.md` and `042-verifier-reports2-fix.md`. So the README's warning about `grep -v report` is right.
- **The table.** 51 rows, 000-050, none missing. For each row, the item and report links name the files, the verdict equals the report's first line, and the commit appears in the item. My 042-050 reports' first lines agree with the 9 new rows.
- **Elsewhere.** `README.md:50` and `evidence/README.md:9` say 000-050, 27 PASS and 24 FAIL.

**The asked check: every hex string against `git rev-list fff2b3f`.**
- **How.** I took every string of 7 to 40 hex digits in all 102 files 000-050, ran each through `git cat-file --batch-check`, and checked the commits against `git rev-list fff2b3f` (535 commits).
- **Result.**
  - 124 strings are commits in this branch's history.
  - Exactly six are commits outside it: `a85fc57`, `e158fe0`, `dc51cc8`, `8fdda85`, `60b0692` and `d1aa46f`. The last two also appear in 12-digit forms.
  - None is ambiguous. The rest are not objects.
- **The README's "Named in" column** matches the files for all six.
- **What each became.**
  - `dc51cc8` → `c887e28`, `8fdda85` → `699e430` and `e158fe0` → `ac3e72e` have identical `git patch-id --stable`, and the targets are ancestors of fff2b3f.
  - `a85fc57` → `763e0af` differs only by the eth10-network conflict resolution I checked in 042.
  - `60b0692` is on `kv260-usb` only, and `d1aa46f` is on no branch. Their dates and subjects are as the README says.
- **The non-commits in 042-050.** They are exactly the README's list:
  - the sha256 prefixes;
  - `afe09457da93fe81d`;
  - the dates `20261030`, `20261103` and `20261104`;
  - eight checkout suffixes: seven of mine (`2146321`, `2471574`, `2626905`, `2656770`, `2682111`, `2744963`, `2842899`) and `1550601`. That one is a builder's, in the path that report 043 quotes, not "the verifier's checkouts" as the README says (note 2).

**Redactions, and what was kept.** These sweeps cover the 1033 added lines and print counts only.
- **None found** of:
  - the Kria's eth0 MAC (colons, dashes, bare, NIC half), taken from my own 030 report;
  - the tailnet label, the bare tailnet domain, the provider hostname or domain;
  - the local home directory, also in its dashed form;
  - the user name (other than the public `josusanmartin`, which isn't in 042-050);
  - the author's or the user's email address, or any email address;
  - `fastmail.com`;
  - `claude-1000`, the scratchpad path's start.
- **Found and kept**, as the README lists:
  - IPv4-like: `100.64.0.0` (a class) and `7.1.18.1` (a USB section number);
  - MAC: `02:00:de:ad:be:01` only;
  - `/home/`: `/home/ubuntu`, `/home/pi`, `/home/someone` and `/home/runner1`;
  - `ssid` and `psk` only as classes;
  - "token" only in the USB sense.
- **The kept list's line numbers** check: report 042 `:36`, `:41`-`:44`; report 043 `:22`, `:69`; report 047 `:35`. `/home/ubuntu` also appears at item 042 `:6` and report 042 `:17`. The README doesn't cite those lines, but its claim "no other `/home/` paths" still holds.
- **Claim 2's deliberate inconsistency.**
  - A case-insensitive search finds the bare domain in 030 (item and report), 035 (item and report), 038's report (upper case, `TAIL…TS.NET`) and 041's report.
  - The README says so at `:270-272`, apart from finding 1.
- **Links.** The only `[…](…)` in 042-050 are report 042 `:28` and `:76`, both inside code spans.

**The asked check: my scratchpad paths, and anything else that should have been redacted.**
- **The item's premise is wrong.** No file 042-050 quotes a scratchpad path. There is no `/tmp/`, `claude-1000` or session id. The word "scratchpad" appears once, in report 042 `:82` ("on a `git archive` copy in my scratchpad"). My reports name my scripts by bare file name only (`v045_tracer.py`, `v048_fakeboard.py`, `group_runs.py`, …).
- **Had a path been there,** it would have needed redacting. My scratchpad path contains the local home directory in dashed form, which is one of the redacted classes.
- **Nothing else needed redacting.** I read the 18 files for anything else private:
  - runner1 paths (`~/verifier-NNN/`, `agent-runs/…`, `/home/runner1/…`);
  - a builder agent's id;
  - the Kria's Python build string (`Aug 31 2026, 10:18:26`).

  Each names no host, address or person, and each falls under the kept rules of 000-041.

**Coverage lines (claim 6).**
- `README.md:50` and `evidence/README.md:9`, `:14`, `:15` and `:30` read as claimed.
- **Other stale counts.** A tree-wide `git grep` for coverage patterns found only `docs/rules-check.md:154` ("Reports 000-023", in the dated audit table). The patterns were `000-0NN`, "after 0NN", "N PASS and N FAIL", "N items, N reports" and "not yet copied", plus every reference to `evidence/verifier`.
- Inside the verifier README, the only remaining "041" mentions are the 039-041 section and the table. Its last line now says "Item 051 and later items and reports are not here."

**Claim 7.**
- `test/model/test_evidence_index.py` at fff2b3f, run as `python3 -I`: `evidence/README.md: one row per folder`, rc 0.
- `test_private_hosts.py` searches only for the tailnet label, which is absent; it passes in the suite.
- The suite is below.

**Mutation.** Nothing here is code. No test reads the verifier README's table, counts or hashes, so they hold only because they were checked by hand, as above.

**Timing check against the ISA:** not applicable; docs and evidence copies only.

## Notes

1. **The 042-050 kept list cites some, not all, lines of a kept path.** `/home/ubuntu` is also at item 042 `:6` and report 042 `:17`. It is the same path, so nothing is hidden.
2. **One checkout suffix is not the verifier's.** `1550601` is the suffix of a builder's checkout, `agent-afe09457da93fe81d-c108e09-1550601`, which report 043 quotes. The README puts it with "the numeric suffixes of the verifier's checkouts". It is still not a commit, so the list is right.

## What I ran

**Locally** (my worktree, detached at fff2b3f):
- the diff and the commit messages;
- `cmp` and `diff` of the 18 copies against my inbox;
- the sha256 table check, against both the files and the blobs;
- the counts and the table check;
- the hex-string lookup (`git cat-file --batch-check`, `git rev-list fff2b3f`) and `git patch-id` for the rebased pairs;
- the private-string sweeps of the added lines (counts only) and the tree-wide `ts.net` count;
- the coverage `git grep`;
- `python3 -I test/model/test_evidence_index.py`.

**On runner1:** `remote_test.sh model` at fff2b3f (`--ignore=test/model/test_swd.py`): `model rc=0`, `2867 passed, 29 skipped in 703.65s (0:11:43)`. This is the item's count, at the later commit, which differs from 8f7fad1 only in `README.md` and `evidence/README.md` (the latter read by the index test). Checkout `agent-runs/protoemu-verify-fff2b3f-2942384`, removed.

No SWD test ran, I did not touch the Kria, and nothing was pushed.
