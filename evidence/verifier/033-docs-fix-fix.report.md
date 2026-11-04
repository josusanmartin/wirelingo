VERDICT: PASS

Commit f3b066b on base 3410bc9 (an ancestor), three commits. The changes are docs, `evidence/`, `.gitattributes` and one new test. Nothing in `src/` or `flow/` changed. `docs/methodology.md` lines 1-36 are byte-identical to `2d05073`. Report 030's three findings are fixed.

| Claim | Result |
|---|---|
| 1. methodology: the sentence's date, before the team; the records don't say what it meant; roles as `assignments.json` | PASS |
| 2. dmesg CR bytes restored; `.gitattributes`; manifest; `test_evidence.py` | PASS: rebuilt from the archive myself; the check catches a stripped CR and an unlisted address |
| 3. Every citation 030 listed is linked, plus two more | PASS: no unlinked "report NNN" left in docs or evidence READMEs |
| 4. Model suite 2351 passed, 29 skipped | PASS: the new skip is the archive rebuild |

## Checked

**Claim 1 (`docs/methodology.md:37-43`).**
- The text now reads: "The author worked with Claude Opus 5.5 and Astra. That sentence was written on 2026-10-06, before the team below existed, and the records do not say what it referred to then. The project's records name `gpt-6-astra` …".
- So it no longer defines the author's word.
- The author's first sentence is unchanged.
- The roles match `~/wirelingo-team-20261011/control/assignments.json`: Euclid (Lean), Pulse (protocol firmware), Forge (flow tooling, release tests), Atlas (architecture, README and documentation), Kepler (expansion study).

**Claim 2.**
- **My own comparison.** I compared the committed blobs (`git cat-file blob f3b066b:…`, so no checkout conversion is involved) with the archive's files, extracted for report 030. The archive is sha256 `7d3a6160…01f2`, matching the manifest.
  - Six files are identical.
  - `fs/dmesg.txt` differs only at lines 149, 240 and 319, and `ls/dmesg-final.txt` only at 149, 239 and 319. Those are exactly the manifest's six redactions.
  - The carriage return count is 1 and 1 in both versions of each file.
- **The builder's check.** `python3 test/model/test_evidence.py ~/wirelingo-usb-real-host-20261030.tar.gz`, plain python, local: "committed files: manifest sha256 equal", "archive: equal apart from the listed redactions", rc 0.
- **The check is not vacuous.** In a `git archive` copy:
  - with the CR stripped from `ls/dmesg-final.txt` (backup kept outside the folder), `test_usb_logs_are_the_manifest_bytes` fails on `ls/dmesg-final.txt`, and the archive rebuild fails too;
  - with `root@example.org` and a MAC appended to `fs/wl_steps.log`, `test_no_unlisted_email_or_mac_is_left` fails ("email"), and so do the other two.
- **Other parts.**
  - `.gitattributes` is `evidence/** -text`.
  - The manifest lists each redaction by file, line and kind, and never the removed value.
  - The README states the CR line and the check.

**Claim 3.** I scanned every `.md` in the tree except the verifier's own items and reports for "report(s) NNN".
- 21 citations; every one links its report file.
- Two lines link the range "Reports 000-023" to `evidence/verifier/README.md`, which is right for a range.
- The one other hit, `usb-uart-coexist.md:157` "reports 276 and 92 RX overflow events", is not a citation.
- The links 030 listed:
  - `verification-story.md:35` and `:105` (000, 001, 004, 009);
  - `rules-check.md:145` and `:154`;
  - `evidence/fabric-ping-20261103/README.md:36`;
  - plus `verification-story.md`'s "Report 001" and the "report 009 passed" line.
- Every relative link in the changed files resolves.

**Claim 4.** The skip is `test/model/test_evidence.py:87: archive not here: set WIRELINGO_USB_ARCHIVE`, on runner1, where the archive is absent. The other two evidence tests pass there.

## Notes

1. The rebuild check runs only where the archive is present: here, not on runner1. The manifest check, which catches a line-ending change on checkout or copy, runs everywhere.

## What I ran

- `remote_test.sh model` at f3b066b: `model rc=0`, `2351 passed, 29 skipped in 409.74s (0:06:49)` (checkout `agent-runs/protoemu-verify-f3b066b-980668`, then `pytest -rs test/model/test_evidence.py` there: `2 passed, 1 skipped`; checkout removed).
- **Locally, no pytest:**
  - the blob-versus-archive comparison;
  - `test_evidence.py` with the archive;
  - the two mutations in a scratch copy, since removed;
  - the citation scan and link check.

I did not touch the Kria or SWD, and nothing was pushed.
