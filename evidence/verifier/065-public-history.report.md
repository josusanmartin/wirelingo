VERDICT: FAIL

Item 065 `public-history`: the rewritten history in the orchestrator's `ai-rewrite/final-b2fa7f3/public`, built from b2fa7f3. It has 6 refs, 579 commits, 2285 trees, 2352 blobs and 4 tag objects. I read `run.sh`, `rewrite.py` and `histlib.py` as the artifact under test, and the builder's `verify.py` only for side effects. Nothing was pushed, no GitHub access, nothing written to the private repo. Kria: verifier does not touch it. No SWD.

The redaction itself holds. No published object holds the private strings in any encoding I tried. Every rewritten commit differs from its original only by redaction. The tags, the archive tag and the remap commit are right, and the rewrite reproduces exactly.

Until finding 1 is resolved, this report should not be copied into `evidence/verifier/`.

It fails on one point: **`evidence/history-map.txt` publishes the old commits' full hashes. Those hashes depend on the private strings, and both are short enough to guess.** From the public repo alone, I recovered the build machine's ident email in 1.3 s and the tailnet label in 17 s (finding 1). Everything else is a note.

## Findings

1. **Blocking: the private strings are recoverable from `history-map.txt`'s old hashes.**
   - **Where:** `evidence/history-map.txt:63-640` in the output, written by `rewrite.py:350`.
   - **Why it works:**
     - An old commit's hash covers its idents, which held the build host's name, and its tree, which held the tailnet name.
     - Everything else in the old commit object is public, since the rewrite changed only what the notes say. So each old hash is a check of a guess at the private values.
     - Both values come from small, enumerable spaces: about 10^7 for the host, 2^24 for the label.
   - **What I ran:** the method is in my scratch scripts (`oracle.py`, `label_oracle.py`), not here. They used only a public clone of `main`, plus sha256 digests of the true values to report a match. Nothing private was printed or sent.
     - **The host:** "recovered: True | equals the private build-host email: True", 2.7 s locally and 1.3 s on runner1.
     - **The label,** given the host: "label recovered True | matches the true one: True | 17 s with 12 workers" (runner1).
   - **Not the cause: the verbatim records.** Without the map, no public file cites an old commit that needs only one value guessed ("their old abbreviations cited in public files, map aside: 0"). Every old hash they do cite needs both values guessed together. As a paper estimate, that is about 10^7 × 2^24 ≈ 1.7 × 10^14 guesses, each recomputing a chain of hundreds of commits. The full map is what makes the attack cheap.
   - **Fix options (the author's decision):**
     - (a) Publish map entries only for old hashes that a public file actually cites, at the cited length. Then rerun my check: no entry should let one value be checked alone.
     - (b) Publish no map.
     - (c) Treat both values as exposed: rename the tailnet's DNS name and accept the VPS name.

     The old hashes in the verbatim records (871) and the kept names stay under every option, behind the joint search above.
2. **Not blocking: 9 cited commits are not listed, though the map header says it lists what is cited but not published.** These commits exist only in the private object store, on no ref, for example rebased or deleted worker branches:
   - `d1aa46f`, the pe_lab bitstream's RTL revision: `fpga/kv260/README.md:313`, `docs/eth10-network.md:299`, `evidence/fabric-ping-20261103/README.md:26`;
   - `e96cb69`: `docs/physical.md:322`;
   - `7c3a039`, `8fdda85`, `aacdc4c`, `d5b8944` and `dc51cc8`: `evidence/model-usb-ls-restage-20261104/README.md:19-27`;
   - `a85fc57` and `e158fe0`: `evidence/verifier/README.md:47-48,104,111`.

   The cause is that the classifier knows only commits on refs (`histlib.py:141`), so these count as "not a commit". Claim 6's "0 unaccounted" is true only within that scope. The fix is to classify against every commit object in the input (`cat-file --batch-all-objects`) and add the 9 to the header. That can be a later commit, with no history change.
3. **Judgment: the author's statement cites an unpublished commit.** `docs/methodology.md:61` says "At commit 19ebe98, 164 of the repository's 184 commits are co-authored by Claude", echoed in `docs/claims-audit.md:49` and `docs/rules-check.md:148`.
   - The count is right in the private repo (184 commits, 164 Claude trailers).
   - But 19ebe98 is the v2 line, 18 commits not on main, on swd, t48-margin and t48-rtl. So no public reader can check the AI-use figure, and the file is frozen.
   - Its tree's two SWD files are byte-identical to blobs main already publishes.
   - Publishing 19ebe98 as a second archive tag would make the statement checkable. Finding 1 applies to it as to any old hash.
   - The rest of the cited-but-unpublished list is fine for a public reader:
     - the team/* worker commits, whose integrated results are on main;
     - the Lean checkers' `BASELINE`s, labels inside hash-bound receipts that no git command reads;
     - `cbeae96`, v2c's RTL, a documented experiment;
     - `7db7d61`, an old bitstream's revision.
4. **Note: after the rewrite, board identity checks fail on a public checkout until a rebuild.**
   - `fpga/kv260/install.sh:13,40` writes `expected_build.json` from `build_id.py`, which is `git log -1 -- <RTL inputs>` of the checkout.
   - From the public history that gives a new hash, while the loaded bitstreams report the old ones (`60b0692`).
   - So `bringup_kria.py` without `--expect` would fail until the board is rebuilt from the public history. After such a rebuild, the README's `--expect 60b0692` (`fpga/kv260/README.md:763`) no longer matches.
   - For the orchestrator, who alone touches the board.
5. **Note: things outside the builder's needle list.**
   - runner1's home directory path is published: `evidence/fabric-ping-20261103/c108e09-suite/model.log`, and the verifier README's line about report 043.
   - The provider's and the mail provider's names appear as words in sweep lists (`evidence/verifier/README.md`, items 030, 035, 041, 042).

   Earlier reports accepted both, but the provider's name narrows finding 1's guess. Everything else my classes found is a test fixture, documentation or a false positive:
   - **Emails:** only the GitHub noreply, Anthropic's noreply, `example.*`, the competition's and T13's public addresses, systemd unit names and made-up test hosts.
   - **MACs:** all locally administered (`02:…`, broadcast), or false positives (time ranges, a dune list). No universally administered MAC exists anywhere in the input history.
   - **100.64/10:** only the class base.
   - **The tailnet IPv6 prefix:** only the allowlisted class forms in `test/model/test_private_hosts.py`.
   - **Home paths:** only board, container and template defaults, plus runner1's.
6. **Note: the output directory holds 4 unreachable loose objects.** They are an alternative remap commit (`17671684`), apparently left by `verify.py` after `gc`. A push of the 6 refs doesn't send them. Push by explicit refs, never from a copy of this directory's object store.

## The builder's claims

1. **Redaction: holds.**
   - **My scan** (`secrets.py`) read every object reachable from the 6 refs as raw bytes: commit and tag headers and messages, tree entry names, blobs, and 5 compressed members (gzip, zip, tar, PNG text chunks).
   - **What it searched for:** 119 forms of the tailnet label, the host, the full host name, the provider and the home path:
     - plain, reversed, hex, rot13;
     - percent and HTML escapes, decimal lists, `\x`/octal/`\u` escapes, UTF-16;
     - base32/85, base64 at 3 alignments;
     - md5/sha1/sha256/sha512;
     - the label's hex part, bounded;
     - separator-stripped and Python-constant-joined views.

     It also counted 13 classes (finding 5).
   - **Where the strings came from:** I read the label from e449e40's `NEEDLE` and the host from the input's idents. The input has exactly one `tail`+6-hex token, equal to the label.
   - **Result:** "FORM HITS: none".
   - **Sanity check:** the same scan on the input mirror finds them: "FORM HITS: {'hostname': 6, 'fqdn': 6, 'provider': 6, 'home-path': 105, 'other-ident-email-0': 1, 'label': 8, 'label-hexpart': 2}".
2. **Counts: hold exactly.** My own count over the old objects reachable from b2fa7f3 and kv260-usb:
   - blobs: domain 539, label-split 30, home 205;
   - messages: home 1;
   - 0 binary blobs touched.

   All 30 split hits sit beside the label's prefix across a quote: 9 in `test_private_hosts.py`, 21 in `mutants.py`. So no unrelated 6-hex token was redacted. Idents: every author, committer and tagger is the noreply ident (1158 + 4). The names are unchanged.
3. **Trees: hold.**
   - main^ (`[round-1 id redacted]`) has tree `[host-only tree id redacted]`, the same as b2fa7f3's.
   - `pairs.py` checked all 578 map pairs:
     - dates, names and parents mapped;
     - the same paths and modes;
     - each of the 104 changed blob pairs byte-equal to my own redaction of the old blob;
     - each message equal to my redaction of the old one, except hash tokens that the map translates.

     Result: "problems none".
   - The map is exactly `rev-list b2fa7f3 kv260-usb` (578), injective, and its new side is every published commit except the remap commit.
   - No `gpgsig` or `mergetag` headers, and 0 signed commits.
4. **The remap commit: holds.**
   - `remapdiff.py`: 65 changed files plus the added map. No frozen or verbatim file is touched. With the two notes and the three tag renames taken out, old and new differ only at hex tokens.
   - 313 tokens, each the same length, each uniquely prefixing an old commit X, with the new token prefixing map[X] and unique among published objects: "bad 0".
   - Firmware and tools change only in tag names inside comments.
   - The fabric-ping sentence is accurate. At the cited commits, the archived `fpga/kv260/host` subtrees differ in 2 redacted files, while `tools` and `firmware` are unchanged.
5. **The citation rule: holds.** The kept tokens in living files, each of the right kind:
   - archive and directory names (`wirelingo-release-evidence-7685aba-fo6.tar.gz`, `rel-7685aba/`, `c108e09-suite/`, `out-c184dd6.txt`, `smoke-1b3ff6b/`);
   - the draw input `sha256("wirelingo blind test 61af9b2")` (`docs/blind-test.md`);
   - the bitstream revision `60b0692` (`fpga/kv260/README.md`, `usb_replay_demo.py`).

   The one changed link fragment follows its heading (`release-evidence.md#recorded-release-fo6-with-software-[round-1 id redacted]-2026-10-13`). No old tag name is left in a living file.
6. **Final-tree citations: hold, with finding 2.** `keep.py` gives:
   - living: 354 new commit citations, 76 kept, 48 unpublished;
   - verbatim: 871 old, all in the map;
   - 35 unpublished commits, all in the header;
   - 0 old tokens that also prefix a published object, so no old hash silently resolves to a wrong new one.
7. **Messages: hold.** Commits give 82 translated tokens, 26 unpublished, 11 kept names and revisions, and 52 that aren't commits. The 4 tag messages add 4, 1 and 10. Each translated token resolves through the map.
8. **Archive tag: holds.**
   - `archive/kv260-usb` holds the rewritten 60b0692 (`[round-1 id redacted]`), 5 commits not on main.
   - Its two SWD blobs (`f06fa78`, `fcb234e`) are reachable from main.
   - None of its 44 archive-only objects has an SWD path.
9. **Tests: hold** (below).

## Asked checks

- **Own scan: clean** (claim 1). It is independent of `verify.py` and covers every reachable object, including names, messages and idents.
- **Trees: identical** (claim 3). The remap diff is limited as stated (claim 4).
- **20 sampled citations.**
  - 19 seeded across README, docs, evidence, formal, fpga and test: same subject and same changed files, old vs new. The one subject difference is `ceb84a4`'s, which cites a hash the rewrite translated.
  - Firmware and tools have no hash citations, only tag names.
  - All 313 are checked mechanically (claim 4).
- **Kept tokens:** each kind sampled, each reason right (claim 5).
- **Tags.** For each of the four: the tag object points at map(old target) and the `tag` header carries the new name, with tagger and date unchanged. Each message differs only in the translated hash; `rc-[round-1 id redacted]-fo6` keeps the archive file name. `archive/kv260-usb` contains map(60b0692).
- **Reproducibility.** `./run.sh b2fa7f3 <my new dir> --date 2026-11-07T12:00:00+0000` gives refs identical to the builder's, all 6, and the verify line "RESULT: PASS". The private repo's refs equal the input mirror's (0 differences).
- **Code reading.**
  - Every published object passes through filter-repo's blob and message callbacks.
  - The commit count is asserted against the commit map (`rewrite.py:234-236`), with `--prune-empty never` and `--prune-degenerate never`.
  - Only explicit refspecs are fetched into the output.
  - The remap commit's `git add -A` (`rewrite.py:352`) runs on a clean `reset --hard` tree; it could pick up an untracked file but didn't (1 A, 65 M).
  - The redaction rules are derived at run time and miss nothing in this input. `derive_labels` (`histlib.py:62`) assumes a 6-hex label, so a different label shape would be missed, but verify's class scan would then fail.
  - The path the scripts don't guard is finding 1, which no scan of strings can see.
- **Decisions.**
  - **Bitstream revisions unmapped: right.** The hardware prints them and `--expect` must match. The map resolves them, and none collides with a published object. Finding 4 is the operational side.
  - **Verbatim/living split: right.** Records stay as written, and READMEs and docs move to the new hashes.
  - **Cited but not published:** finding 3 (19ebe98) is the one a public reader needs.

## What I ran

- **`secrets.py`** on the output and on the input mirror (above).
- **`ctx.py`:** distinct class matches.
- **`git grep`** for emails on the ref tips.
- **The input history,** for universal MACs and the Xilinx OUI: none.
- **`pairs.py`:** "map entries 578 old commits (b2fa7f3 + kv260-usb) 578 map == that set: True new side == published minus remap: True injective: True", then "distinct changed blob pairs 104 problems none".
- **`remapdiff.py`:** "changed files 65 files with remapped tokens 40 tokens 313", then "bad 0".
- **`keep.py`:** "unpublished cited commits 35 all listed in map header: True", "old tokens that ALSO prefix a published object: 0". Then the object-store check for finding 2: "private commits cited, not published: 49", 9 on no ref.
- **Redaction counts:** "blobs: {'domain': 539, 'label': 0, 'label-split': 30, 'home': 205, 'host': 0} messages: {… 'home': 1 …} binary blobs redacted: 0".
- **`oracle.py`** (local) and **`label_oracle.py`** (runner1 `~/verifier-065/`, public clone only, now removed): finding 1's lines.
- **Rerun:** `run.sh` into my scratchpad, "REFS-IDENTICAL".
- **Tests on the output's `main`** (a clone of `[round-1 id redacted]` with no remote): `remote_test.sh model test` (`--ignore=test/model/test_swd.py` built in):
  - model: "model rc=0", "2916 passed, 29 skipped in 472.86s (0:07:52)" with "SIGROK CHECKS SKIPPED: 28 tests", the same as b2fa7f3;
  - test: "test rc=0", "TESTS=28 PASS=28 FAIL=0 SKIP=0".
