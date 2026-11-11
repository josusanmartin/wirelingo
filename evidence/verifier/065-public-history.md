# 065 public-history

Backlog item AI. The author decided on 2026-11-06 to make the repo public, with the history rewritten so that nothing private is published and every cited commit hash is remapped.

It is published as a NEW GitHub repo. The old private repo is renamed and kept private, so no old object is reachable from the public one. This item is the rewritten history that will be pushed. Nothing has been pushed yet. Verify it before anything is published.

**Where.** The builder's directory is `[scratch path redacted]/ai-rewrite/`:
- the scripts `run.sh`, `rewrite.py`, `verify.py` and `histlib.py`;
- the final output repo `final-b2fa7f3/public/` (a bare-style repo with only the published refs, no remote), with `verify.out` and `remote-model.out`;
- the earlier dry run `dryrun-088d703/`.

The input is improve-2026-11 at `b2fa7f3`, whose full regression is green: model 2916/29, test 28/28, and every RTL suite rc 0, usb_uart_rtl 3/3 included. The command is `./run.sh b2fa7f3 <new dir> --date 2026-11-07T12:00:00+0000`, which is reproducible: two runs give identical refs.

**Published refs:**

| Ref | Object | Old → new |
|---|---|---|
| `main` | `[round-1 id redacted]…` | b2fa7f3 → `[round-1 id redacted]`, plus one remap commit |
| `archive/kv260-usb` (lightweight) | `[round-1 id redacted]…` | old b1ccd3e, 5 commits not on main; holds 60b0692, the bitstream's RTL revision |
| `rc-[round-1 id redacted]-t48ba` | tag `[round-1 id redacted]…` | 93c2137 → `[round-1 id redacted]` |
| `rc-[round-1 id redacted]-fo6` | tag `[round-1 id redacted]…` | d916d40 → `[round-1 id redacted]` |
| `rc-[round-1 id redacted]-fo6` | tag `[round-1 id redacted]…` | f1cb7c9 → `[round-1 id redacted]` |
| `signoff-g4` | tag `[round-1 id redacted]…` | [old id of a907d4a, redacted] → `[round-1 id redacted]` |

The builder's claims:

1. **Redaction.** Every blob, path name, commit message, tag message and ident reachable from the published refs is scanned (579 commits, 2285 trees, 2352 blobs, 4 tag objects, 782 path names), with zero hits for:
   - the tailnet label, any `tail` + 4 hex, and the hex part alone;
   - `*.ts.net` names;
   - 100.64/10 addresses other than the class base, and the Tailscale IPv6 range (class forms allowed);
   - `[build host name class redacted]` + digits and `[build host provider redacted]server`;
   - `[home directory redacted]` and `[home directory redacted]`;
   - keys, tokens, and `password=`/`psk=`/`ssid=` assignments;
   - universally administered MACs;
   - emails outside an allowlist of public and example addresses.
2. **Redaction counts.** In blobs: tailnet domain 539, label split 30, home path 205. In messages: 1 home path. 1160 idents were rewritten to `Josu <5554649+josusanmartin@users.noreply.github.com>`. The redaction strings reuse the repo's own conventions.
3. **Trees.** The b2fa7f3 tree is unchanged by redaction. Each of the 503 rewritten commits differs from its old tree only by redaction. Parents, dates and modes are preserved.
4. **The remap commit,** one commit on the rewritten tip, by the noreply ident, with a Co-Authored-By trailer:
   - `evidence/history-map.txt`, 578 entries (old → new). Its header explains the rules, and lists the renamed tags, the archive tag, and 35 commits "cited but not published", each with its refs and a reason.
   - 354 citations remapped in 65 living files.
   - A pointer line in `evidence/README.md`.
   - A sentence in `evidence/fabric-ping-20261103/README.md`: its `git archive` sha256 values predate the rewrite, and can't be reproduced, because the archived trees held the tailnet name.
5. **The citation rule.**
   - A citation is a token of 7-40 lowercase hex, bounded by non-alphanumerics, that uniquely prefixes an old commit.
   - It is remapped only in living files, at the same length; none needed lengthening.
   - Kept as written:
     - tokens inside file, directory or archive names;
     - the blind-test draw input `sha256("wirelingo blind test 61af9b2")`;
     - bitstream RTL revisions (`60b0692`, `7db7d61`), since the hardware reports them and `--expect 60b0692` must match the board.
   - Link fragments follow their headings.
   - Verbatim files are never edited: `evidence/verifier/NNN-*`, files inside `evidence/<folder>/` other than its README, `docs/lean-verification-history.md`, and `*.log`.
   - Frozen files are never edited: `src/`, `flow/`, and `docs/methodology.md`.
6. **Final-tree citations.**
   - 313 resolve to new commits.
   - 57 are kept names and 19 are bitstream revisions, all in the map.
   - 871 old hashes in verbatim files, all in the map.
   - 80 citations of 35 unpublished commits, all listed in the map header.
   - 0 ambiguous, and 0 old tag names left.
7. **Messages.** 86 hashes translated; kept: 26 unpublished, 6 names, 6 bitstream revisions; 62 tokens that aren't commits.
8. **The kv260-usb archive tag.** It adds no SWD content: two SWD files are in its tree, but they are byte-identical to blobs main already publishes. It held the tailnet name, which is now redacted.
9. **Tests on the output's `main`.**
   - Remote model suite at `[round-1 id redacted]`: 2916 passed, 29 skipped, rc 0, the same as b2fa7f3.
   - `test_doc_links.py` and `test_evidence_index.py` pass.

Asked checks:
- **Write your own private-string scan** over `git rev-list --objects --all` of `final-b2fa7f3/public` (blobs, trees' path names, commit and tag messages, idents). Do this independently of `verify.py`.
  - Find the old label and hostname in the input history yourself (from `[home directory redacted]/dev/protoemu`), and search for their exact strings and for the encodings your 060 and 064 work covered.
  - Never print them in the report.
- **Compare the trees.** Diff `final-b2fa7f3/public` main^ (the rewritten tip, before the remap) against b2fa7f3's tree: they should be identical. Diff the remap commit: only hash tokens, tag names, the map, the pointer and the one sentence.
- **Sample at least 20 remapped citations,** across docs, firmware headers, tools and the README. Confirm each new hash names the commit with the same subject and tree change as the old one.
- **Sample kept tokens** of each kind, and confirm the reason for each.
- **Check the tags.** Each of the four points at the rewritten counterpart of its old commit, with an equal tree after redaction. `archive/kv260-usb` resolves `60b0692`.
- **Reproducibility.** Rerun `run.sh` into a new directory with the same date, and confirm identical refs.
- **Read `rewrite.py` and `histlib.py`** for any path that could publish an unscanned object, or drop a commit.
- **Judge the decisions:**
  - Is keeping the bitstream revisions unmapped right?
  - Is the verbatim/living split right?
  - Is anything in "cited but not published" something a public reader actually needs?

No push and no GitHub access: nothing in this item touches the remote. No Kria.
