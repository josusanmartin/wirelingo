# 067 public-followups

Backlog item AK, answering findings 2, 3 and 7 of report 066 on the published
history, and adding the private-side record checker that item AL needs. Nothing
has been pushed; no GitHub, no Kria, no SWD. No change to `src/`, `flow/` or
`docs/methodology.md`.

**Where.**
- Branch `public-followups` in the private clone, created with
  `git switch -c public-followups refs/remotes/public/main`. Its base is the
  public `main`, `f7923d42f6f9a2caaf4b64b89ec7561d8cb66fcc`. The upstream setting
  was removed afterwards, so a bare `git push` has no target.
- One commit on top: `c110c445c4c4646f3a62e82e6ef94653d7362fda` ("history-map: list
  all 47 cited unpublished commits; cite the published tree"). Author and
  committer are the public noreply identity, and the message ends with the
  Co-Authored-By trailer.
- `git rev-list refs/remotes/public/main..public-followups` lists only that
  commit, and its one parent is `f7923d4`. `git merge-base --is-ancestor b2fa7f3
  public-followups` exits 1.
- The checker is outside the repo, at
  `~/dev/protoemu-agent-tools/record_check.py`.

## What changed, keyed to 066's findings

**Finding 2: the map header now lists every cited unpublished commit, 47 of 47.**
1. *Classified against the full private store.* I classified against the
   private repo's own object store (`cat-file --batch-all-objects`, read-only),
   not a mirror: 862 old commit objects.
   - Scope: every object reachable from the 6 public refs, scanned for hex tokens.
     That is every blob version, every commit and tag message, and every path
     name.
   - Visible old objects: 212. They are 164 published commits, 47 unpublished
     commits and 1 tree.
   - The header listed 35 of the 47. The 12 missing are exactly the 9 from 065
     finding 2 and 066 finding 2, plus `987e2d5`, `aa0820a` and `f9c7ebd`.
2. *The three are cited.* `987e2d5`, `aa0820a` and `f9c7ebd` are cited only in
   old versions of `docs/team-integration-checklist.md`, not at the tip. All three
   are added.
3. *The 12 rows.* Each row gives the token, its citing files (and how many commit
   messages cite it) and the reason. The rows go into the header in commit-time
   order, like the builder's.
   - **The 9** (`d1aa46f`, `e96cb69`, `7c3a039`, `8fdda85`, `aacdc4c`, `d5b8944`,
     `dc51cc8`, `a85fc57`, `e158fe0`) are on no ref. Their reason is "exists only
     in the object store (a rebased or deleted branch)".
   - **The 3** are not object-store-only, so they do not get that reason. Each is
     still on a private worker branch (`team/20261011-sol-kepler` and
     `team/20261013-coexist-docs`; `team/20261011-sol-forge`;
     `team/20261011-sol-atlas`). They get the builder's reason for worker
     branches, with the refs named as in the other rows.
4. *Lengths: never longer than cited.* Each token is at its shortest cited
   length. The one exception follows the header's "never a full 40-hex hash"
   rule, the same `outlen` rule `rewrite.py` uses:
   - 11 tokens are at 7 hex. `d1aa46f` is cited at 7, 12 and 40 hex, so it is
     given at 7.
   - `f9c7ebd` is cited only in full, so it is given at 12 hex (`f9c7ebd995a6`).
5. *The header text.* It now says "Each at its shortest cited length (12 hex where
   only a full hash is cited)". It also says the 12 were added after publication,
   and why the first list missed them: it read only reachable commits, and only
   the tip's files.
6. *All 12 are joint.* By the dependence method of the verifier's `dep.py`
   (reimplemented in `record_check.py`, below): 12 of 12 depend on host and label
   together. 0 are host-only, 0 label-only and 0 email-only. All 212 visible old
   objects are joint as well, and 0 single-value tokens are visible.

**Finding 3: the full old tree id is replaced by the published tree.**
- **The change.** `docs/team-integration-checklist.md:43` and
  `docs/usb-uart-coexist.md:202` now cite
  `b7267f4f146a5899e6a3ad7463cb5f6ad324c1c8`.
- **What that id is.** It is `67443d9^{tree}`. `67443d9` is the new commit that
  the private `commit-map.txt` gives for the old commit whose root tree was cited.
- **Checks on the old id.** I confirmed it equals that old commit's root tree. No
  file at the new tip contains it.
- **Checks on the new id.** It is reachable from `refs/remotes/public/main`
  (`rev-list --objects`).
- **The note.** Both sentences need the tree itself, so each adds a short note:
  "the tree of commit `67443d9`; its id changed when the history was rewritten
  for publication".
- **What stays.** The old id stays in old versions of the two files, since the
  history is not rewritten again. It is joint, as 066's `tree_oracle.py` check
  showed, and my dependence run agrees.

**Finding 7: `evidence/history-map.txt:16` is softened.** It no longer says the
map "reveals nothing a public file does not already". It now says the map "shows
no old token that a public file does not already show. What it adds is the
pairing of each old hash with its new one."

**For item AL: `record_check.py`, the private-side record checker.**
- **Usage:** `record_check.py [--repo DIR] [--tree REV] [--commits RANGE] [--quiet] [FILE...]`.
  `--repo` defaults to `~/dev/protoemu` and is only read.
- **What it flags.** Every hex token of 7 to 40 chars, any case, bounded by
  non-alphanumerics, that prefixes an object of the private store whose id
  depends on exactly one private value. Kinds:
  - commit, tree, blob or tag;
  - host-only, label-only, or email-only. Email-only covers the one other
    private ident email in the store; no record cites such an object.

  It also flags the label's hex part as a bare token, and the build host's
  name.
- **The private values** are derived in memory from the store at run time.
- **Output:** only `path:line: <kind>`, or `commit <sha>:line: <kind>`, then a
  total. Exit 1 if anything is flagged.
- **Dependence** is `dep.py`'s method:
  - blob = its bytes;
  - tree = entry names and children;
  - commit = ident lines, message, tree and parents;
  - tag = tagger, message and target.

  Then a fixed point. An old blob that cites a published single-value commit
  gains that commit's value, because the public history translated every such
  citation.
- **Published old commits** are the ancestors of the rewrite's input tips:
  `b2fa7f3`, the kv260-usb tip and the 4 old tags. That makes 578, equal to
  `commit-map.txt`'s left column, so the tool doesn't need the map, which lives
  in /tmp. `--commit-map` overrides it.
- **Its counts match 066's `dep.py` line for line:**
  - 862 old commits: host-only/published 145, joint/published 433,
    joint/unpublished 274, host-only/unpublished 1, email-only/unpublished 3,
    email+host+label/unpublished 6;
  - H = 145, all host-only;
  - every one of the 5220 objects reachable from the public refs has no private
    dependence.
- **On inbox 060-066 it reproduces 066 finding 1 exactly, and nothing else:**
  - commit host-only at `060-privacy-needles.report.md:111`,
    `061-present2.report.md:26,27,53`, `065-public-history.md:23` and
    `066-public-history2.md:28`;
  - tree host-only at `064-privacy-needles2.report.md:75`,
    `065-public-history.report.md:86` and `063-present3.report.md:39`;
  - label hex part at `060-privacy-needles.md:31` and `064-privacy-needles2.md:22`.

  11 flags in all. On `066-public-history2.report.md`: 0. On inbox 051-059: 0.

## Claims

1. `public-followups` is `f7923d4` plus one commit, `c110c44`. No commit of the
   private history is in it.
2. The map header lists all 47 unpublished old commits that any public file
   version or message cites. None is longer than its shortest citation, and none
   is a full hash.
3. All 12 added commits are joint (host and label). Over the whole public history,
   0 single-value old tokens are visible.
4. The two docs cite a tree that is in the public repo, with a note. No file at
   the tip holds the old tree id.
5. `record_check.py` flags exactly 066 finding 1's 11 lines on inbox 060-066, and
   0 on the 066 report.

## Checks run

- `python3 test/model/test_doc_links.py`: "49 files: every relative link
  resolves".
- `python3 test/model/test_evidence_index.py`: "evidence/README.md: one row per
  folder". The local pytest is broken, so both ran as scripts, which run each
  file's check.
- The orchestrator's `git grep -inIcE` for the label's hex part, the host pattern and
  the provider word (pattern not quoted here, since it holds the label hex): rc 1.
- A diff scan for home paths, `ts.net`, tailnet labels, `@`, CGNAT addresses,
  `ssid`, `password` and `vmi`: no hits.
- `record_check.py --tree HEAD --commits HEAD`: 708 files and 575 commit messages,
  "TOTAL flagged: 0". The tree of `refs/remotes/public/main` also gives 0.
- `record_check.py` on this item: 0.
- `remote_test.sh model` at `c110c44`: see the end of this item.

**Side effect to note.** To scan `archive/kv260-usb`, which the local store
lacked, I fetched that public tag from `ai-rewrite/final3-b2fa7f3/public` into
FETCH_HEAD. It created no ref, and it added only public objects to the local
store.

## Please check

- **The branch.** Confirm that `public-followups` has `public/main` (`f7923d4`)
  as its base and holds no private-history commit:
  - `rev-list public/main..public-followups` is the one commit;
  - `merge-base --is-ancestor b2fa7f3 public-followups` fails;
  - no commit on the branch is in `commit-map.txt`'s left column.
- **The map header.** Recompute, with your `dep.py`, the unpublished old commits
  visible anywhere in the public history plus this commit. Confirm that the
  header holds all 47 and that each is joint. Check each token's length against
  its shortest citation.
- **Judge two choices:**
  - the 3 worker-branch commits get the worker-branch reason, not the
    object-store one;
  - `f9c7ebd` is given at 12 hex.
- **The new tree.** Confirm that `b7267f4f146a5899e6a3ad7463cb5f6ad324c1c8` is
  `67443d9^{tree}` and is reachable from `public/main`.
- **The checker.** Run `record_check.py` against your `tokcheck.py` on inbox
  051-067 and compare. Confirm that it prints no value, digest or matched token.
  Also check its published-set derivation against `commit-map.txt`.
- **The rest.** Confirm the line-16 wording, and that nothing outside the three
  files changed.

## Remote model suite

`remote_test.sh model` at `c110c44` (runner1 checkout
`~/protoemu-flow/agent-runs/agent-af3010357afb9f6db-c110c44-1427633`): exit 0,
**2916 passed, 29 skipped** (SIGROK checks skipped: 28). That is the same as
`f7923d4` in 066.
