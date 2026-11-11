VERDICT: PASS

068 public-followups2 (delta `aa958a7` on `c110c44`, plus `record_check.py`): verifier report.
Kria: verifier does not touch it. No SWD.

The commit applies 067 findings 1, 2 and 4 correctly. The branch still holds no private-history commit, no single-value old token and no private string. The updated checker catches all 6 forms that 067 found missed, and it still gives exactly the 11 lines of 066 finding 1. My extra cases found one more miss and one wrong docstring example (finding 1). Neither form occurs in any record today.

## Findings

1. **`record_check.py`: one undocumented miss, and one example the tool does not catch.** I reran my 067 cases plus 16 new ones, 42 in all. 41 behave as intended.
   - **The undocumented miss:** a token at offset 0 of a hex run of 9 to 40 chars that is longer than the token. For example, a 12-hex single-value prefix followed directly by 8 more hex makes a 20-hex run. `check_text` tests the whole run (`record_check.py:289`), and the run is not a prefix of the id. Runs over 40 are tested on their first 12 hex, so a 13–40 run is the only length band that misses.
   - **Fix suggestion:** for any run, also test `run[:12]`, or `run[:7]`, when the full run gives no hit. The "Known gaps" list (`:39-49`) covers only nonzero offsets.
   - **The example:** the docstring (`:12`) lists `abc<hex>` as caught. `a`, `b` and `c` are hex digits, so that form is a token at offset 3 inside a longer run, the documented gap at `:45`, and the tool does not flag it (my case "glued after HEX letters abc"). The form with non-hex letters (`xyz<hex>`) is caught. Change the example to `xyz<hex>`.
   - **Exposure today:** 0. In inbox 051–068 (35 files) and the 708 files of `aa958a7`, no hex run of 8–40 has a 7-hex prefix of a single-value old id without the whole run being one, and no run has such a hit at a nonzero offset (my v068/prefixgap.py).

2. **My 067 finding 2 missed one path name.** I listed 5 path names. The builder correctly adds a sixth, `turnaround-dc51cc8.txt`. My 067 scan took path names from `rev-list --objects`, which names each blob only once, and that blob is shared with another path. With path names taken from `ls-tree -r` of every public commit, the set is exactly the builder's 6, and all 6 are tracked at `aa958a7`.

3. **Cosmetic.** The rewrapped `dc51cc8` continuation line at `evidence/history-map.txt:98` is 109 columns. The header's worker-branch rows are already up to 195, so this is a note only.

## Checks of the delta

- **Branch.**
  - `aa958a7` has one parent, `c110c44`.
  - `rev-list public/main..public-followups` lists `aa958a7` and `c110c44`.
  - `merge-base --is-ancestor` on the rewrite's old input tip exits 1. 0 of the branch's 576 commits are in `commit-map.txt`'s left column.
  - Author and committer are the noreply identity, and the trailer is present.
  - `git diff c110c44 aa958a7` touches only the three named files (+15 −12).
- **067 finding 1.** `evidence/history-map.txt:59` is now `14375ca`, cited at 7, 12 and 40 hex. My v068/header2.py reports "rows failing length or ref check: []" for all 47 rows. It also finds 47 cited, 47 rows, none missing or extra, with ref lists matching `for-each-ref --contains` and commit-time order kept.
- **067 finding 2.** The three rows now name exactly the path names my corrected scan finds: `8fdda85` two, `aacdc4c` one, `dc51cc8` three. Their other citing files and commit-message counts are unchanged.
- **067 finding 4.**
  - Both docs now say "the published (redacted) version of that tree". The checklist adds "its id and the content of a few files differ from the original's", which is accurate: 4 files differ (067 finding 4).
  - The tree id is unchanged and correct.
  - Line widths in `docs/usb-uart-coexist.md:199-212`: the longest is now 83, a line that was already 83 at `f7923d4`. The 90-column line is gone.
- **The whole public history plus the branch.**
  - My 066 `dep.py`: SINGLE-SECRET visible old ids 0. Visible ids: 164 published commits, 47 unpublished commits and 1 tree, all h+l. Partial-prefix matches 0.
  - My 066 `secrets.py` (146 forms): "FORM HITS: none". Class counts are identical to 067. The deltas are +1 commit, +3 trees, +3 blobs and the noreply and trailer counts.
- **The checker's regression.**
  - Inbox 051–068: "TOTAL flagged: 11", line-for-line the same as my 067 run. `067-public-followups.report.md` and `068-public-followups2.md` each flag 0.
  - `--tree aa958a7 --commits aa958a7`: 708 files, 576 messages, "TOTAL flagged: 0". `aa958a7`'s message alone flags 0.
- **The checker's mutation run** (v068/mutate2.py, in memory, temp file deleted).
  - The 6 former gaps (`-g<hex>`, `0x<hex>`, label hex split by a dash, sha256 of the label at 12 and 64 hex, sha256 of the host email at 8) are all flagged now.
  - Also flagged:
    - `xyz<hex>`;
    - an id followed by more hex past 40;
    - the label hex split by an underscore, a colon or a space;
    - sha256 of the label with a trailing newline;
    - md5 of the host FQDN;
    - sha1 of the upper-case label hex;
    - the git-blob digest of the label.
  - Correctly not flagged:
    - the documented gaps (two separators, a digest prefix at an offset, `abc<hex>`);
    - a 7-hex digest prefix, which is below 8;
    - a 6-hex H prefix;
    - a joint commit;
    - an unrelated sha256;
    - hex prose (`defaced 0xdeadbeef cafebabe`).
  - The one miss is finding 1. No planted value appears in the tool's output.
- **Timing check against the ISA:** not applicable. No RTL, model or test code changed.

## What I ran

- **Checkout.** Detached at `aa958a7` in my verifier checkout. The scratch bare clone of the published repo got `public-followups` refetched. The private repo, the rewrite output and the reports dir were read only.
- **Scripts.** v066/dep.py, v068/header2.py (header.py with path names from `ls-tree -r`), v068/mutate2.py, v068/prefixgap.py, v066/secrets.py, and `record_check.py` itself. Outputs are quoted above.
- **`remote_test.sh model` at `aa958a7`.**
  - "rc=0"
  - "2916 passed, 29 skipped in 387.03s (0:06:27)"
  - SIGROK checks skipped: 28. A pass says nothing about them.
- **Cleanup.** The runner1 checkout `~/protoemu-flow/agent-runs/protoemu-verify-aa958a7-1704907` has been removed.

Summary: the delta is correct and adds no private value. The header is complete and exact at 47 rows. The checker now catches the 6 forms from 067. For AL, fix or document finding 1 (a 13–40 hex run that extends a token, and the `abc<hex>` example).
