# 066 public-history2

Backlog item AI, second round, answering report 065's six findings and the
orchestrator's rulings on them. The input is unchanged: `improve-2026-11` at
`b2fa7f3`, to be published as a new repo with the private history rewritten.
Nothing has been pushed; no GitHub, no Kria. The build is reproducible:
`./run.sh b2fa7f3 <new dir> --date 2026-11-07T12:00:00+0000`, and two runs give
identical refs.

**Where.** The builder's directory is
`[scratch path redacted]/ai-rewrite/`:
- the scripts `run.sh`, `rewrite.py`, `verify.py`, `histlib.py`;
- the output repo `final3-b2fa7f3/public/` (only the published refs, no remote),
  with `verify.out` and `remote-model.out`;
- `final3-b2fa7f3/commit-map.txt`, the full old→new map, kept beside the repo and
  **not** published; the in-repo `evidence/history-map.txt` no longer carries it;
- `final3-b2fa7f3/rewrite-summary.json`, `verify-report.json`, `file-classes.json`.

**Published refs:**

| Ref | Object | Old → new |
|---|---|---|
| `main` | `f7923d42f6f9a2caaf4b64b89ec7561d8cb66fcc` | b2fa7f3 → `a52fe01` (rewritten tip), plus one remap commit |
| `archive/kv260-usb` (lightweight) | `a3918c8f5284a23d0263eb1c44a57461999b596b` | old b1ccd3e, 5 commits not on main; holds `60b0692` |
| `rc-01ce16d-t48ba` | tag `3ef2f599df9d15e44bf8af5a985a50b5b0ea68cc` | 93c2137 → `01ce16d` |
| `rc-00dfa75-fo6` | tag `ab9d785206622fb57e5a4afeff06ee92ac345b45` | d916d40 → `00dfa75` |
| `rc-4f5179d-fo6` | tag `e70a9497afe159fc481a6bbb564e1de9f5564af1` | f1cb7c9 → `4f5179d` |
| `signoff-g4` | tag `6b60dfacd03b9d69092ea7e4f720d4a7ff30fef3` | [old id of a907d4a, redacted] → `a907d4a` |

Every ref differs from round 1, including the three rc tag names. The
single-value pass (finding 1) rewrites citations in early history, so the hashes
of those commits and of everything after them change.

## What changed, keyed to 065's findings

**Finding 1 (blocking): no old hash that tests one private value alone is public.**
1. *The map adds nothing.* `evidence/history-map.txt` lists only old tokens that
   already appear in a published file or message: 129 published and 35 unpublished
   commits. Each is given at the length it is cited, never a full hash; the longest
   is 12 hex. Remapped living citations are not listed. The full old→new table is
   gone from the repo; it lives in `commit-map.txt`, unpublished. The header keeps
   the rules, the renamed tags (old name → new name and new target) and the archive
   tag (new target only).
2. *The single-value commits are translated across all history.* The set **H** is
   the published commits whose hash depends on only one private value.
   - It is computed over every commit object in the input. Host dependence comes
     from a commit's own idents or any ancestor's; label dependence from a label
     blob in its tree or any ancestor's.
   - **H has 145 commits, all host-only.** No label-only commit exists anywhere:
     3 commit objects have idents without the host, all unpublished; 2 depend on
     the host through ancestors, and 1 depends on neither value.
   - Over every commit object, the kinds are: host-only/published 145,
     joint/published 433, joint/unpublished 143, none/unpublished 1.
   - 16 of the 145 are cited, 483 times, all in living files and in the
     since-removed `flow/exp.sh`. **No verbatim file, in any version, cites a
     commit in H**, so "never edit verbatim" is not in conflict. No frozen file at
     the tip cites one.
   - In every blob and message of the history, each cited token of a commit in H
     is replaced by its new hash at the same length.
   - Each new token is a unique prefix among the published objects.
3. *Deviation from the ruling's assertion, and why.* One second pass with
   map1 does not hold: 137 of the 145 moved (map2 ≠ map1). The cause is not a
   commit cited from its own ancestry. Translating any citation changes that
   commit and all later ones, which moves every H commit after the first
   translated blob. The script therefore iterates: pass k uses map(k−1) for H,
   until a pass moves none.
   - It converged on the 9th iteration; the commits moved per iteration were
     137, 96, 94, 87, 75, 38, 19, 3 and 0.
   - The script stops if no fixed point is reached. A fixed point exists only if
     no H commit is cited from its own ancestry, so convergence proves that
     condition.
4. *Oracle, across the whole history.* verify.py now scans every reachable blob
   (all versions) and every commit and tag message, not just the tip. Secrets are
   compared by sha256 digest and never printed.
   - **202 old commits** have a token visible somewhere public: 164 published and
     38 unpublished (whose objects are not public, so they cannot be
     reconstructed).
   - **All 202 are joint**, needing host and label together.
   - **Single-value commits with a visible token anywhere in the published history: 0.**
   - Tip tree: 164 cited, 0 single-value. Verbatim records: 129 cited, 0
     single-value, so 065's measure still holds.

**Finding 2: commits that exist only in the object store are classified and listed.**
Citations are classified against every commit object (`cat-file
--batch-all-objects`). `d1aa46f`, `e96cb69`, `7c3a039`, `8fdda85`, `aacdc4c`,
`d5b8944`, `dc51cc8`, `a85fc57` and `e158fe0` are now in the map's "cited but not
published" header with the reason "exists only in the object store (a rebased or
deleted branch)". The header has 35 commits in all, and all 81 unresolvable
citations are covered: 0 missing.

**Finding 3: the AI-use figure for the public history.** No v2 line is published.
The remap commit adds a sentence to `docs/claims-audit.md`; `methodology.md`
stays frozen. It says `19ebe98` is on unpublished branches, and that at the public
`main` tip, remap commit included, **496 of 574** commits carry a Claude
`Co-Authored-By` trailer. That is methodology.md's own method, which reproduces
164 of 184 at 19ebe98.

**Finding 4: `--expect` after the rewrite.** A note under the USB-replay section
of `fpga/kv260/README.md`:
- the loaded bitstreams report their pre-rewrite RTL revision;
- `60b0692` is `dbd6172` on `archive/kv260-usb`;
- `build_id.py` on a public checkout computes the new hash, so keep passing
  `--expect 60b0692` until the board is rebuilt from the public history.

**Finding 5: the provider word is redacted, and the digests are updated.**
- *Redaction.* The bare provider word is replaced by `[build host provider
  redacted]`, case-insensitive, as a whole word. It has no false positives: 8 hits
  in the tip, all the word itself. It is redacted 14 times across history.
  runner1's home path is kept.
- *Digests.* Per the ruling, the remap commit sets the "sha256 committed" cell in
  `evidence/verifier/README.md` to the published digest for the 5 changed verbatim
  files: `030-docs-fix.md`, `035-hostname-fix.md`, `035-hostname-fix.report.md`,
  `041-verifier-reports2.md`, `042-verifier-reports2-fix.report.md`. One sentence
  above the tables says these digests are of the files as published and differ
  from the originals', and the history-map header says the same. verify.py checks
  each cell against `sha256sum` of the published file.
- *Nothing else pins them.* Grepping the old digests (inbox and committed) across
  the b2fa7f3 tree finds them only in `evidence/verifier/README.md`; no test or
  receipt elsewhere. `test_evidence.py` covers `evidence/kv260-usb-20261030/`,
  where the word does not occur.

**Finding 6: no unreachable objects.** After `reflog expire` and `gc
--prune=now`, `git fsck --unreachable` reports 0. verify.py fails if any appear.
The push uses explicit refs in any case.

**Consequence to note.** main^ (the rewritten tip) no longer equals b2fa7f3's tree
byte for byte. It differs in the 5 provider-redacted verbatim files and the
verifier README, and in 5 living files where the single-value pass translated
citations: `README.md`, `docs/claims-audit.md`, `docs/physical.md`,
`docs/verification-story.md` and `docs/verification.md`. verify.py checks that
every changed blob equals the old blob after redaction and translation.

## The asked checks

All run by `verify.py`; `final3-b2fa7f3/verify.out` ends "RESULT: PASS".
- **Private scan.** Every reachable object: 2352 blobs, 2285 trees, 579 commits
  with their idents, 4 tag objects and 782 path names. 0 hits for:
  - the tailnet label and its split hex part;
  - `*.ts.net`;
  - 100.64/10 (class base aside) and the Tailscale IPv6 range (class forms aside);
  - `[build host name class redacted]…`/`[build host provider redacted]server`, and the provider word;
  - `[home directory redacted]`;
  - keys and tokens, and `password/psk/ssid=`;
  - universal MACs, and non-allowlisted emails.
- **Map adds nothing.** Every one of the 129 + 35 old tokens appears byte for byte
  in a public file other than the map.
- **Oracle.** 0 single-value tokens anywhere in the published history; all 202
  visible old commits are joint.
- **History.** Every rewritten commit equals its old commit after redaction and
  translation: trees, messages, parents, dates, modes. The H fixed point holds,
  and the H tokens are unique prefixes.
- **Remap commit.**
  - `evidence/history-map.txt` added.
  - 5 notes added: the evidence/README pointer, the fabric-ping sentence, the
    claims-audit sentence, the fpga `--expect` note, and the verifier-README
    digest sentence.
  - 5 sha256 cells updated.
  - 336 citations remapped in 65 living files.
  - Nothing else changed.
- **Tags.** Each points at the rewritten counterpart of its old commit, with the
  tree equal and the noreply tagger at the original date. `archive/kv260-usb`
  resolves `60b0692`.
- **Final-tree citations.**
  - 314 resolve to new commits.
  - 57 kept names and 21 bitstream revisions, all resolvable through the map.
  - 871 old hashes in verbatim files, all resolvable.
  - 81 citations of 35 unpublished commits, all in the map header.
  - 0 ambiguous, and 0 old tag names in living files.
- **Reproducibility.** A second run with the same `--date` gives identical refs.
- **Tests on `main`.** `test/model/test_evidence_index.py` and
  `test/model/test_doc_links.py` pass. The remote model suite is below.

## Remote model suite

`remote_test.sh model` at `f7923d4` (runner1 checkout
`~/protoemu-flow/agent-runs/public-f7923d4-1339382`): **model rc=0, 2916 passed, 29
skipped** (SIGROK checks skipped: 28), the same as b2fa7f3 and as rounds 1 and 2.

## Please check

- **Rerun your oracle across the full history** of `final3-b2fa7f3/public`:
  every reachable blob, all versions, and every commit and tag message, not just
  the tip. Confirm that no old token anywhere lets either private value be checked
  alone. Also confirm that every old commit whose token is visible needs both
  values together (the joint-only count, 202 by my count).
- **Recompute H yourself** (single-value commits over every commit object) and
  confirm its size and kinds: 145 host-only, 0 label-only. Confirm that no
  verbatim file in any version cites a commit in H.
- **Check the fixed point.** Every H commit's new hash is the one its translated
  citations name. The iteration replaced the single map2 = map1 assertion, for the
  reason under finding 1.3. Judge whether that is acceptable.
- **Check the digests and the private side file.** The 5 updated "sha256
  committed" cells equal `sha256sum` of the published files. `commit-map.txt` is
  not reachable from any published ref.
- **Reproducibility.** Rerun `run.sh` into a new directory with the same date and
  confirm the refs are identical.
