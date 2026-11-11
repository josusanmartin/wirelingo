VERDICT: PASS

067 public-followups (`c110c44` on public `main` `f7923d4`): verifier report.
Kria: verifier does not touch it. No SWD.

I checked the branch, the 47-row map header, the tree swap, the line-16 wording and `record_check.py`. Each check was independent and covered the full private object store. The branch holds no private-history commit. It adds no single-value old token and no private string. The orchestrator accepted two deviations (the worker-branch reason, and the shortest cited length with 12 hex for a full-hash-only citation), and both are applied correctly. `record_check.py` reproduces 066 finding 1 exactly, and its dependence data matches mine for every old object. Three things in the record need fixing (findings 1, 2 and 4), and finding 3 lists the checker's blind spots for AL. None of them leaks anything.

## Findings

1. **One header row is longer than its shortest citation, so claim 2 and the new header text are false for 1 of 47 rows.**
   - Where: `evidence/history-map.txt:59` gives `14375cabdaba` (12 hex). That row was in the header before this commit.
   - The citations: the same commit is cited at 7 hex (`` `14375ca` ``) in two old versions of `docs/team-integration-checklist.md`, the ones changed by public commits `6e85949` and `7e511cb`. At the tip it is cited in full, at `docs/team-integration-checklist.md:115` and elsewhere in that file.
   - Why it matters: the header now says "Each at its shortest cited length (12 hex where only a full hash is cited)", and claim 2 says "None is longer than its shortest citation". By that rule this row should be `14375ca`.
   - Privacy: no effect. The full 40-hex id is already public, and the commit is joint (host and label).
   - Fix: change the token to `14375ca`. The other 46 rows are each exactly at their shortest cited length (my v067/header.py, below).

2. **Three new rows leave out the file names that cite their commit.**
   - `8fdda85` (`:99`), `aacdc4c` (`:92`) and `dc51cc8` (`:94`) are also cited in path names under `evidence/model-usb-ls-restage-20261104/`:
     - `table-8fdda85.txt` and `turnaround-8fdda85.txt`;
     - `table-aacdc4c.txt`;
     - `table-abort-dc51cc8.txt` and `table-restage-dc51cc8.txt`.
   - Their "Cited in" lists name only the README, scripts and tests of that folder.
   - This is minor. The folder is named, and all three are joint. Every other count in the 12 rows is exact: citing paths, old-version-only status and commit-message counts (8, 2, 1, 2, 4, 4, 1, 1, 0).

3. **What `record_check.py` does not catch (for AL).** I ran 26 planted cases in a temporary file, built in memory and deleted afterwards.
   - **20 behave as intended:**
     - single-value commit tokens at 7 (lower and upper case), 12 and 40 hex, in backticks and in path-like names;
     - label-only and host-only trees, host-only and label-only blobs, an email-only commit and a single-value tag;
     - the label in full, in upper case, as a bare hex part, and glued to a letter;
     - the host name in either case, and inside the ident email;
     - correctly not flagged: a 6-hex prefix and a joint commit.
   - **6 are missed:**
     - a token glued to a letter, such as a `git describe` suffix (`-g<hex>`) or `0x<hex>`;
     - the label's hex part split by a dash;
     - sha256 prefixes of the label or the host email, at 8, 12 or 64 hex. This is the verify.out style that RULES line 25 forbids quoting.
   - **Exposure today:** none of these forms occurs in inbox 051–067 (33 files) or in the 708 files of `c110c44` (my v067/gaps.py: 0 glued single-value tokens, 0 digest prefixes of 12 private-string forms).
   - **For AL:** run the privacy scan alongside the checker, and either flag digest prefixes or keep relying on RULES line 25.
   - **One shared modelling limit:** like my 066 `dep.py`, the checker propagates H citations only through blob bytes, not through old commit or tag messages or tree entry names. Today only two such objects exist (1 commit message, 1 tag message), and both already depend on the host, so nothing changes.

4. **The tree note says only the id changed, but the content changed too.**
   - Where: `docs/team-integration-checklist.md:43` and `docs/usb-uart-coexist.md:202`.
   - The id is right: `b7267f4f146a…` is `67443d9^{tree}`. `commit-map.txt` maps the old commit whose root tree was cited to `67443d9`, and the tree is reachable from `public/main`.
   - The content: the published tree differs from the old one in 4 files (91 lines changed each way), all redactions or translated hashes:
     - `docs/physical.md`
     - `fpga/kv260/README.md`
     - `fpga/kv260/install.sh`
     - `test/gl.log`
   - Suggested wording: "the published (redacted) version of that tree". This is minor.
   - Cosmetic: the reflow at `docs/usb-uart-coexist.md:205` leaves a 90-column line among lines of about 75.

5. **The two deviations, judged.**
   - **Worker-branch reason.** For each of the 3, `for-each-ref --contains` in the private repo gives exactly the refs named in its row: `team/20261011-sol-forge`; `team/20261011-sol-kepler` and `team/20261013-coexist-docs`; `team/20261011-sol-atlas`. So "exists only in the object store" would be false for them, and the worker-branch reason is the right one.
   - **The 9 object-store rows.** All 9 are on no ref.
   - **12 hex for `f9c7ebd995a6`.** It is cited only in full, in one old blob version, which matches the header's "never a full 40-hex hash" rule.
   - **The other rows.** For all 47 rows, the ref lists match `--contains` exactly, and the rows are in commit-time order (0 inversions).

6. **Not in AK's scope, still open from 066.** Finding 4 (the README's sha256 history cells match the old files after one guess of the provider word) and finding 6 (the rewrite's side files hold secret-digest prefixes) are unchanged. AL covers finding 1.

## Claims

1. **Holds.**
   - `rev-list public/main..public-followups` lists exactly `c110c44`. Its one parent is `f7923d4`, which equals `main` of the published repo.
   - `merge-base --is-ancestor` on the rewrite's old input tip exits 1. 0 of the branch's 575 commits are in `commit-map.txt`'s left column.
   - No upstream is configured. Author and committer are the noreply identity, and the trailer is present.
   - Only the three named files changed.
2. **Holds except for finding 1.**
   - My independent scan read every blob version, every commit and tag message, and every path name reachable from the 6 public refs plus the branch, leaving out the branch's own map blob.
   - It finds exactly 47 cited unpublished commits. The header has 47 rows, which resolve one-to-one to them. None is missing, none is extra, and none is 40 hex.
   - 46 rows are at their shortest cited length; `14375cabdaba` is not.
3. **Holds.**
   - `dep.py` (066) on the public repo plus the branch: 47 unpublished, 164 published and 1 tree visible, all `h+l`.
   - SINGLE-SECRET visible old ids: 0. Partial-prefix matches: 0. H commits with a visible token: 0.
   - Old-object classes are unchanged from 066: 3730 old ids, H = 145, all host-only.
4. **Holds.**
   - The new id is verified as in finding 4.
   - `git grep` for the old tree id at `c110c44` exits 1. At `f7923d4` it hits the two lines this commit changed.
   - The wording caveat is finding 4.
5. **Holds.**
   - `record_check.py` on inbox 051–067 (33 files) flags exactly 066 finding 1's 11 lines, with the same kinds, and nothing else. It flags 0 on `066-public-history2.report.md` and 0 on `067-public-followups.md`.
   - My v067/mycheck.py, built from my own `dep.json`, gives the identical 11 lines.
   - The tool's published set (578, from the old tips) equals `commit-map.txt`'s left column. Its H equals mine, and its kind for each of the 3730 old ids (746 blobs, 862 commits, 2117 trees, 5 tags) equals mine.
   - All 5227 public objects are class `none`.
   - Its output prints only paths, line numbers, kinds and counts. The planted run confirmed that no planted value appears in its output.

The line-16 wording is as stated in the item, at `evidence/history-map.txt:16-18`.

Timing check against the ISA: not applicable. No RTL, model or test code changed.

## What I ran

- **Checkout.** Detached at `c110c44` in my verifier checkout. I made a scratch bare clone of the published repo with `public-followups` fetched into it, and changed nothing else. The private repo, the rewrite output and the reports dir were read only.
- **Diff.** `git diff f7923d4 c110c44`, read in full: 3 files, +51 −9.
- **v066/dep.py PRIVATE PUB+branch commit-map reports-dir.** "SINGLE-SECRET visible old ids: 0"; visible: commit published h+l 164, commit unpublished h+l 47, tree h+l 1.
- **v067/header.py.** 47 cited, 47 rows, "cited but not in header: [] | in header but not cited: []", "rows failing length or ref check: ['14375cabdaba']", commit-time order True.
- **record_check.py.**
  - On inbox 051–067: "TOTAL flagged: 11", the lines of 066 finding 1.
  - `--tree public-followups --commits public-followups`: 708 files, 575 messages, "TOTAL flagged: 0".
- **v067/mycheck.py** (independent, all object types): "TOTAL 11", the same lines.
- **v067/rc_compare.py.** "published: tool 578 | map left 578 | equal: True"; kinds equal for all 3730 old ids; H equal.
- **v067/mutate.py.** 26 cases, 6 gaps (finding 3); no planted value in the tool's output.
- **v067/gaps.py.** Inbox 051–067 and the `c110c44` tree: 0 glued single-value tokens, 0 private-string digest prefixes.
- **v066/secrets.py** on public plus branch (146 forms): "FORM HITS: none". Class counts are identical to 066's. The deltas are +1 commit, +3 trees, +3 blobs and the noreply and trailer counts.
- **`remote_test.sh model` at `c110c44`.**
  - "model rc=0"
  - "2916 passed, 29 skipped in 378.73s (0:06:18)"
  - SIGROK checks skipped: 28. A pass says nothing about them.
  - The model suite includes `test_doc_links.py` and `test_evidence_index.py`.
- **Cleanup.** The runner1 checkout `~/protoemu-flow/agent-runs/protoemu-verify-c110c44-1435115` has been removed. 24 older `protoemu-verify-*` checkouts from earlier items remain there; I did not remove them.

Summary: no private-history commit, no new single-value token and no private string. The header is complete at 47. Fix the one over-length row (finding 1) before push; findings 2 and 4 are wording. The checker is sound for AK's scope; finding 3 lists what AL must cover beside it.
