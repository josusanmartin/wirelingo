VERDICT: PASS

Item 066 `public-history2`: round 2 of the rewritten history, in the orchestrator's `ai-rewrite/final3-b2fa7f3/public`, built from b2fa7f3. It has 6 refs, 579 commits (574 on `main`, 5 more on `archive/kv260-usb`), 2285 trees, 2352 blobs and 4 tag objects. I read `run.sh`, `rewrite.py` and `histlib.py` as the artifact under test, and `verify.py` only for its checks. Nothing was pushed, no GitHub, nothing was written to the private repo. Kria: verifier does not touch it. No SWD.

**The history passes the gate's test.** From the 6 published refs alone, no old object and no digest lets either private value be tested alone. I checked this independently of `verify.py`:
- **Scope:** dependence on the build host and the tailnet label for every object in the private repo's object store (862 commits, not the builder's 722-commit mirror);
- **what I scanned:** every hex run in every reachable public object (all versions, messages, tags, paths, ref names), digests, and raw binary;
- **result:** 0 single-value tokens, and 211 visible old commits, all needing both values.

The rewrite reproduces exactly. Every rewritten commit equals my own redaction plus translation of its original.

**Before going public, finding 1 must be handled.** It is not in the history but in the records queued to follow it: six inbox files that will be copied into `evidence/verifier/` cite old hashes of host-only commits or host-only trees. Copying any of them verbatim would bring back 065's oracle. The rest are notes. One is a claimed fix that isn't there (finding 2): the 9 commits from 065 finding 2 are still not in the map header.

This report cites no host-only token. Every old hash in it needs both values.

## Findings

1. **Must be handled before any of inbox 051-066 enters the public repo: six records carry host-only old ids.**
   - **What:** each item below needs only the build host to be tested, the class of 065 finding 1. My check:
     - `tokcheck.py`, over the 31 inbox files not in the public tree;
     - each hex token is classed by `dep.py`'s dependence.
   - **Old hashes of commits in H:**
     - `060-privacy-needles.report.md:111` (1);
     - `061-present2.report.md:26,27,53` (3);
     - `065-public-history.md:23` (1);
     - `066-public-history2.md:28`, the signoff-g4 row's old target (1).
   - **Old tree ids that are host-only:**
     - b2fa7f3's root tree, at `064-privacy-needles2.report.md:75` and in my own `065-public-history.report.md:86`. Since this round, main^'s tree differs from b2fa7f3's. b2fa7f3's tree holds 5 living files that cite H commits by their old hashes, so it can be rebuilt from public data plus a host guess.
     - A tree at `063-present3.report.md:39`.
   - **Also in plain text:** the label's hex part, in `060-privacy-needles.md:31` and `064-privacy-needles2.md:22`. The repo's privacy test should stop these.
   - **Fix (the orchestrator's decision):** when copying the records, translate or redact these tokens, as the H pass did for history. For example:
     - translate them to the new hashes, with a note, as for the 5 provider-word files;
     - or redact them with a bracketed note.

     Then run a check like `tokcheck.py` or `verify.py`'s K check on every record before it is committed, for as long as the old hashes matter.
2. **The claim on 065 finding 2 is not met: the 9 object-store commits are still missing from the map header.**
   - **The item says:** "`d1aa46f`, `e96cb69`, `7c3a039`, `8fdda85`, `aacdc4c`, `d5b8944`, `dc51cc8`, `a85fc57` and `e158fe0` are now in the map's 'cited but not published' header".
   - **What the map holds:** none of them (`evidence/history-map.txt:29-65`). The header's 35 commits are the same set as round 1's, now at cited length.
   - **The cause:** `rewrite.py:170` snapshots the source with `git clone --mirror --no-local`, which copies only reachable objects. The input has 722 commits; the private store has 862. So `OldCommits`' `--batch-all-objects` (`histlib.py:161`) can't see them, and `verify.py`'s "missing from it: 0" is blind to the same gap.
   - **The same scope applies to the item's whole-store figures.** "Kinds over every commit object", "3 commit objects have idents without the host", "none/unpublished 1" and "202 visible = 164 + 38" are all counted over the mirror. Over the full store:
     - unpublished: 280 joint (6 of them with a second private ident email), 3 with only that email, and 1 host-only;
     - visible: 211 = 164 published + 47 unpublished. That is the 38, the 9, and 3 cited only in old versions (`987e2d5`, `aa0820a`, `f9c7ebd`).
   - **What does not change:** H covers published commits only, so it is unaffected (claim 2). All 47 visible unpublished commits are joint.
   - **Fix:** classify against the source's own object store, read-only, and add the 9. That can be a later commit, with no history change.
3. **Note: the oracle check covers only commits, and one full old tree id is cited at the tip.**
   - **Where:** `docs/team-integration-checklist.md:43` and `docs/usb-uart-coexist.md:202` cite `f8772208d86e21ca1cec573bd33b353b8dbb5b72`, the root tree of old `6ffefca` (published as `67443d9`).
   - **Why it stays safe:** an old tree has no idents, so its only private input is the label (two domain-redacted blobs), plus the old text of any blob the H pass changed. This tree's `docs/physical.md` cites 10 H commits by their old hashes, which makes the tree joint.
   - **My check:** I rebuilt the old tree from public data with the true label (`tree_oracle.py`, check mode, label read from the private store, nothing printed). It does not match, and it fails exactly at `docs/physical.md`.
   - **The risk:** a tree like this one, but without an H-citing blob, would be a label-only oracle of the 065 kind, and `verify.py`'s K check would pass it.
   - **Also:**
     - the citation is dangling: the tree is not in the public repo, and the map lists only commits;
     - `verify.py`'s K check and the "final-tree citations" count classify trees, blobs and digests as "not a commit".
   - **Fix:** extend the K check to every old object id and to digests of old content (as `dep.py` does). Cite `67443d9^{tree}`, or add a note.
4. **Note: the provider-word redaction can be undone with one guess.**
   - **How:** replace the note in each of the 5 published files with the word, in some case, and hash the result. The sha256 then matches a digest in `evidence/verifier/README.md`'s public history, the old "sha256 committed" cells. I tested this with the true word: all 5 match.
   - **The current cells:** the kept "sha256 inbox" cells for `030-docs-fix.md` and `041-verifier-reports2.md` confirm it at the tip too.
   - **What the redaction does:** it keeps the word out of every string (my scan: 0 objects, 6 in round 1). Its digests let anyone who guesses the provider confirm it.
   - **Impact:** it bears only on the joint search's guess at the host's shape. The provider is not one of the two values, and RULES' list doesn't name it.
   - **The author's call:** accept that, or drop the word's redaction and keep those 5 records byte for byte.
5. **Judgment: the joint search, re-estimated (a paper estimate, not measured).** It corrects 065 finding 1.
   - **What 065 said:** each joint guess recomputes "a chain of hundreds of commits".
   - **What is true:**
     - **the cheapest visible joint commit** (`277944a`) needs 20 label-dependent commits rehashed per (host, label) guess;
     - **the cited tree in finding 3** needs about one root-tree hash per guess, since its host-only and label-only subtrees are computed once per value.
   - **Estimate:** about 1.7 × 10^14 guesses × 10-100 SHA-1 compressions ≈ 2 × 10^15 to 2 × 10^16. At a paper figure of about 5 × 10^10 SHA-1/s for one current high-end GPU, that is about half a day to four days.
   - **Meaning:** costly, but within reach of a determined attacker with GPUs. 065's option (c) is to rename the tailnet's DNS name, which makes the label worthless whatever is published. That is the author's decision. The ruling's criterion, no single-value test, is met.
6. **Note: the builder's side files hold oracles; keep them private.**
   - `verify.out` and `final3.log` print 8- and 12-hex sha256 prefixes of the label and the host email ("secrets used (sha256, first 12)"). Each prefix tests a guess of one value alone.
   - Neither file is in the repo (my scan of the public objects, and no `commit-map`, `verify.out` or log path in any tree). The 066 item doesn't quote them.
   - `commit-map.txt`, `verify.out`, `final3.log` and `rewrite-summary.json` must stay unpublished, and that line must never be pasted into a record.
7. **Minor wording.**
   - **`evidence/history-map.txt:16`** says the map "reveals nothing a public file does not already". It adds the old→new pairing, which spares a joint attacker the step of finding which public commit to rebuild: at most a factor of 579.
   - **The map omits 35 published old commits** whose tokens are visible only in old versions of living files. The item says so ("Remapped living citations are not listed").

## The builder's claims

1. **The map adds nothing: holds as to tokens** (wording: finding 7).
   - 164 tokens, 129 published and 35 unpublished, the longest 12 hex.
   - Each appears as a token in some public object other than the map: 0 missing.
   - The full table is gone from the repo, and the header holds the rules, the renamed tags and the archive tag.
2. **H: holds.**
   - **My count** (`dep.py`):
     - **scope:** every object of the private store;
     - **host dependence:** idents, messages, blob and path text, and any ancestor;
     - **label dependence:** blob, message and path text, and any ancestor;
     - **result:** 145 published commits with exactly one value, all host-only, and 0 label-only.
   - **The kinds match the item's** for published commits: host-only 145 and joint 433.
   - **Translation:** my H, used in my own translation, reproduces every rewritten blob byte for byte: 483 tokens, the builder's count, on the builder's 7 paths.
   - **Verbatim files:** none touched, in any version. No path under `evidence/`, and none in `file-classes.json`'s 167 verbatim paths.
3. **The fixed point: holds, and the iteration is acceptable.**
   - Every translated token names the final new hash of its commit at the same length, and is a unique prefix among the public objects: 0 not unique.
   - No old token of an H commit is visible anywhere.
   - On the method: a single second pass can't hold, because translating a citation changes its commit and every later one. Iterating map(k-1) → map(k) until nothing moves is the right construction. A fixed point is exactly the self-consistency wanted. `rewrite.py:280` stops if none is reached. My rerun shows the same 9 iterations (137 … 3, 0).
4. **The oracle across the full history: holds, with finding 2's scope.**
   - **Scanned:** every reachable object, every version, and every commit and tag message, path and ref name, reading hex runs case-folded at every offset. That came to 33897 runs.
   - **Visible old ids:** 164 published commits, 47 unpublished commits and 1 tree, all joint.
   - **Single-value:** 0, partial-prefix matches 0.
   - **Digests:** md5, sha1, sha256 and sha512 of all 361 old blobs that hold a private value, a guessable one, or an H citation, and of every reports-dir file that holds one. They show only:
     - the provider-word ones (finding 4);
     - two joint ones, the 035 inbox digests.
   - **Archive digests:** the two `archive.sha256` digests reproduce exactly as `git archive --format=tgz` of the old commits, whose tar header holds the old commit id, so they are joint. Every other cited `git archive` is of a commit.
   - **Binary:** 6-byte old-id prefixes 0, and 4-byte H prefixes 0 (0.11 expected by chance).
5. **Digests and the side file: hold.**
   - The 5 "sha256 committed" cells equal `sha256sum` of the published files. The inbox cells equal the inbox files.
   - `commit-map.txt`'s blob is not in the public store, and no path in any version names it.
6. **Redaction: holds.** My 065 scan (`secrets.py`), with the bare provider word added, read every object reachable from the 6 refs: 146 forms, "FORM HITS: none". Every class count is the same as round 1, except the provider word, which went from 6 objects to 0.
7. **Every rewritten commit: holds.** `pairs2.py` checked all 578 pairs:
   - dates, names and parents;
   - noreply idents;
   - paths and modes;
   - every one of the 234 changed blob pairs, against my redaction followed by H translation.

   Messages equal my redaction followed by translation, except 11 tokens kept on purpose, all joint and all in the map: the bitstream revision `60b0692` (6), names (`7685aba`, `c108e09`) and two prose tokens in old messages.
8. **The remap commit: holds** (`remapdiff2.py`).
   - It touches 65 files plus the added map, none of them frozen or verbatim.
   - It remaps 295 hash tokens and 41 tag names, the builder's 336, each checked against `commit-map.txt` and unique.
   - Every other change:
     - the 5 notes;
     - the 5 sha256 cells: 2 replace "same" and 3 replace the old digest.
9. **Tags and archive: hold.**
   - Each tag points at map(old target), with its new name in the `tag` header and its tagger date unchanged, under the noreply tagger.
   - Each message differs only in the translated hash. `rc-4f5179d-fo6` keeps the archive file name.
   - `archive/kv260-usb` = map(kv260-usb): 5 commits not on main, holding `60b0692` as `dbd6172`.
10. **The finding 3 and finding 4 texts: hold.**
    - `docs/claims-audit.md:51-54`: 496 of 574 at the public `main` tip, by the trailer count that gives 164 of 184 at `19ebe98` in the private repo.
    - `fpga/kv260/README.md:751-756`: `dbd6172` is on `archive/kv260-usb` and not on `main`.
11. **No unreachable objects: holds.** `fsck --unreachable` reports 0, and there are 0 loose objects.
12. **main^ vs b2fa7f3: holds.** Exactly the 11 listed files differ, each by redaction or translation (claim 7).

## What I ran

- **`dep.py`:**
  - "commit kinds over all 862 commit objects: {('e:0', 'unpublished'): 3, ('e:0+h+l', 'unpublished'): 6, ('h', 'published'): 145, ('h', 'unpublished'): 1, ('h+l', 'published'): 433, ('h+l', 'unpublished'): 274}";
  - "H (published, exactly one secret): 145 Counter({'h': 145})";
  - "phase 2 (H-citing blobs host-dependent): H unchanged: True";
  - visible "('commit', 'published', 'h+l') 164", "('commit', 'unpublished', 'h+l') 47", "('tree', 'tree', 'h+l') 1";
  - "SINGLE-SECRET visible old ids: 0", "H commits with any visible old token: 0";
  - "6-byte old-id prefixes found: 0 | 4-byte H prefixes found (expected by chance 0.11): 0".

  Phase 1, before blobs citing H commits counted as host-dependent, found the cited tree as label-only, which is what led to finding 3.
- **`tree_oracle.py`** (check mode): "true label rebuilds the cited old tree from public data: False". The only blob I can't rebuild is `docs/physical.md`, which "tokens differ: 10 old tokens in H: 10".
- **`pairs2.py`:** "pairs 578 | problems: none" for blobs ("translated blob: 483", "changed blob pairs: 234", "translated tokens not unique among public objects: 0", "H-translated paths that are verbatim or under evidence/: []"), and "message mismatch tokens: {('kept', 'joint', 'other'): 10, ('kept', 'joint', 'name-like'): 1}".
- **`remapdiff2.py`:** "changed files 65 | files with remapped tokens 40 | tokens 298". 298 = 295 hashes + 3 digest cells, and those 3 cells are its only "bad" entries.
- **`secrets.py`** (146 forms): "FORM HITS: none".
- **`tokcheck.py`** over the inbox: finding 1's lines. On this report: 0 single-value tokens.
- **The provider-word test** (finding 4): "provider guess confirmed by a digest in the README's public history: True" for all 5.
- **Rerun:** `./run.sh b2fa7f3 <my dir> --date 2026-11-07T12:00:00+0000`, "REFS-IDENTICAL", "MAP-IDENTICAL", "RESULT: PASS", passes 2-10 moving 137, 96, 94, 87, 75, 38, 19, 3 and 0. The private repo's refs equal the input mirror's (0 differences).
- **Tests on the output's `main`** (a clone of `f7923d4` with no remote): `remote_test.sh model test`, with `--ignore=test/model/test_swd.py` built in:
  - model: "model rc=0", "2916 passed, 29 skipped in 422.28s (0:07:02)" with "SIGROK CHECKS SKIPPED: 28 tests", the same as b2fa7f3 and round 1;
  - test: "test rc=0", "TESTS=28 PASS=28 FAIL=0 SKIP=0".

  The runner1 checkout has been removed.
