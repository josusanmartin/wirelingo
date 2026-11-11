# 068 public-followups2

A delta on 067 (PASS), applying the fixes in report 067's findings 1, 2 and 4 and
answering finding 3 in the private checker. Nothing has been pushed; no GitHub,
no Kria, no SWD. No change to `src/`, `flow/` or `docs/methodology.md`.

**Where.**
- Branch `public-followups`: one new commit,
  `aa958a7b91ee3dfba91defb5012e2b1c31550215`, on top of `c110c44` (no amend).
- The branch is now `f7923d4` → `c110c44` → `aa958a7`. Author and committer are
  the public noreply identity, and the message ends with the trailer.
- The checker is `~/dev/protoemu-agent-tools/record_check.py`, outside the repo.

## What changed, keyed to 067's findings

**Finding 1.** `evidence/history-map.txt:59`: `14375cabdaba` is now `14375ca`, its
shortest cited length (old versions of `docs/team-integration-checklist.md`).

**Finding 2.** The "Cited in" lists now name the citing path names under
`evidence/model-usb-ls-restage-20261104/`:
- `8fdda85`: `table-8fdda85.txt`, `turnaround-8fdda85.txt`;
- `aacdc4c`: `table-aacdc4c.txt`;
- `dc51cc8`: `table-abort-dc51cc8.txt`, `table-restage-dc51cc8.txt`, and also
  `turnaround-dc51cc8.txt`, which the report's list did not have.
  `turnaround-dc51cc8.txt` is tracked at the tip. `rev-list --objects` shows it
  under another path because its blob is shared with that path. `git log
  --name-only` over the public refs finds these 6 path names and no others for
  the 12 rows.

**Finding 3 (checker, private).** Of the 6 missed forms, all 6 are now caught,
and a "Known gaps" section in the docstring lists what still is not.
- *Glued tokens.* It scans maximal hex runs of 7-40, whatever the neighbours.
  That catches `-g<hex>`, `0x<hex>` and `abc<hex>`. A run longer than 40 is
  tested on its first 12 hex.
- *Split label.* The label hex is also caught when one separator (`- _ . :` or a
  space) sits anywhere inside it.
- *Digest prefixes.* It flags any hex run of 8 or more that is a prefix of the
  md5, sha1, sha256, sha512 or git-blob digest of a private string form. The
  forms are the label, its hex part, [tailnet name form redacted], the host name, the host FQDN
  and the host ident email, each in lower and upper case, with and without a
  trailing newline.
- *Planted test.* I ran 22 planted cases, built in memory with no value printed:
  - 18 positives, including the 6 forms from finding 3;
  - 4 negatives: a joint commit, a 6-hex H prefix, an unrelated sha256, and hex
    prose such as `defaced`/`0xdeadbeef`.

  All 22 behave as intended.
- *Still not caught:* other digest forms and slices, more than one separator or
  an encoded label, an old id at a nonzero offset inside a longer hex run, H
  propagation through messages and tree names, and private strings other than
  the three values. For those, run the privacy scan beside the checker.
- *Regression.* On inbox 051-067 it still gives exactly 066 finding 1's 11
  lines. On `067-public-followups.report.md`, this item and `aa958a7`'s message
  it gives 0.

**Finding 4.** Both docs now call `b7267f4f146a…` "the published (redacted)
version of that tree", so it is no longer implied that only the id changed.
- `docs/team-integration-checklist.md:43-45` adds that "its id and the content of
  a few files differ from the original's".
- `docs/usb-uart-coexist.md:202-205` is rewrapped. The 90-column line is gone,
  and the longest line in that paragraph is now one that was already there
  before the edit.

## Checks run

- `python3 test/model/test_doc_links.py`: "49 files: every relative link
  resolves".
- `python3 test/model/test_evidence_index.py`: "evidence/README.md: one row per
  folder".
- The orchestrator's privacy `git grep`: rc 1. A diff scan for home paths,
  `ts.net`, labels, `@`, CGNAT addresses, `ssid`, `password` and `[build host name class redacted]`: no hits.
- `record_check.py --tree HEAD --commits HEAD` at `aa958a7`: 708 files and 576
  messages, "TOTAL flagged: 0".
- The header still covers every unpublished old commit visible in the public
  history: 47 of 47, none missing and none extra.
- `merge-base --is-ancestor b2fa7f3 HEAD` exits 1.
- No model run, since only docs and the map changed.

## Please check (the delta only)

- `git diff c110c44 aa958a7` touches only `evidence/history-map.txt`,
  `docs/team-integration-checklist.md` and `docs/usb-uart-coexist.md`. The new
  commit's one parent is `c110c44`, and the branch still has no private-history
  commit.
- Row `14375ca` is at its shortest cited length. All 47 rows now satisfy the
  header's length rule.
- The six path names in the three rows match the tracked files, including
  `turnaround-dc51cc8.txt`.
- The finding 4 wording, and the line widths in the rewrapped paragraph.
- Rerun your `mutate.py` cases against the updated `record_check.py`. Confirm
  the 6 former gaps are flagged, no planted value appears in the output, and the
  inbox 051-068 run still gives the same 11 lines.
