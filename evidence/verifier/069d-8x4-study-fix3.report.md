VERDICT: PASS

069d 8x4-study fix3 (branch `study-8x4`, `976fed1..d2ed344`, commits `cc59c17` and `d2ed344`, `docs/8x4-study.md` only, backlog AN): verifier report.
Kria: verifier does not touch it. No SWD.

069c's findings 1–3 are fixed. I reread the three edited paragraphs whole at `d2ed344`: they have no fragments and make no claim beyond the record. The attribution split is right:
- `docs/v3-study.md`'s "Timing" covers CMPW, `!t` and the CRC register as an endpoint;
- the CRC widening is this study's prototype.

## Findings

1. **Notes only, no change required.**
   - **Commit message.** `cc59c17`'s message still credits the CRC widening to "v3-study.md, Timing" ("the any-width CRC widens an endpoint register (v3-study.md, Timing)"). That is the misattribution `d2ed344` fixes in the doc. The message is history, not a doc claim, and the next commit's message corrects it. Rewriting it is not worth a history change.
   - **Line width.** `docs/8x4-study.md:40` is now 81 columns. Cosmetic.

## 069c's findings against the tip

1. **The fragment** (069c finding 1). The gate paragraph, `:643-664`, read whole:
   - "The untuned 2-core" is gone;
   - "closed, so week 5 is not spent redoing gate level, SDF, equivalence and precheck" is one rewrapped sentence;
   - every sentence is complete.

   `grep` finds no line ending in "untuned 2-core", and no "addition to drop", "on that path", "worst case" or "keep the rest" anywhere in the doc. ✓
2. **The table read** (069c finding 2). Both the gate paragraph (`:655-664`) and the fallback section (`:580-586`) now say:
   - "drop the table read first: it is the addition that loads the SRAM outputs directly". This matches `docs/v3-study.md:249-250`: "The table read gives every DOUT bit one more load (the OSR mux)". ✓
   - "CMPW and `!t` also add a gate on decode paths (v3-study.md, "Timing", which also names the CRC register as an endpoint of those paths)". `v3-study.md:248-253` says "The slow-corner critical paths start at the SRAM outputs (decode → pin registers and R0–R3/CRC/PC)" and "CMPW and `!t` add a gate in front of the ALU immediate and the condition mux." The ALU immediate and the condition mux sit on those decode paths, so the paraphrase is fair. ✓
   - "the any-width CRC widens that register (this study's prototype)". `syn/8x4-study/pe_core_x.sv:446-457` has a 32-bit `crc_step` and a 32-bit `crc_top` select under `V3_CRC32` / `V8_CRCW`, and the doc's own `:446` says "the CRC register and its readback widen to 32 bits". It is attributed to this study. ✓
   - "So whether dropping the table read is enough is for a harden to measure." No unmeasured outcome is claimed. ✓
   - The word counts are unchanged and, as I checked in 069c, match `v3-study.md:124`, `:169`, `:179-181`. "Probably no longer fits a bank" stays hedged, with the uncounted case named. ✓
3. **The intro bullet** (069c note 3, `:37-41`). It now reads "so it should be ready at the gate. Whether it keeps the base's 48 MHz margin … is for that search to measure." That is consistent with the gate paragraph's "if its search closed". ✓

**The three paragraphs, whole.**
- The **intro bullet** (`:37-41`) says only what the plan does and what the search will measure.
- The **fallback section** (`:570-586`) cites the measured numbers it gives:
  - 308,777 µm² (`results.txt`, the 2-core package row);
  - +13.6K;
  - estimate 35.5 %, labelled as an estimate;
  - Metal3 45.7 % with no overflow;
  - −0.643 ns and +0.19 ns slow (base, my 069 rerun).

  The rest is plan or hedged.
- The **gate paragraph** (`:645-664`) adds no new number.
- No first, only or fastest language.
- The new link `[v3-study.md](v3-study.md)` resolves to `docs/v3-study.md`.

## Privacy and branch

- **`record_check.py`.** `record_check.py --repo . --tree HEAD --commits refs/remotes/public/main..HEAD` at `d2ed344`: "tree HEAD: 719 files checked", "commit messages checked: 12", "TOTAL flagged: 0".
- **My private-string check.** v067/mycheck.py over the doc and the delta (the diff plus both messages): "TOTAL 0".
- **Pattern grep.** Over the delta for home paths, tailnet, IPv4 and IPv6 forms, emails, SSID and password: the only hits are the two trailers' noreply address.
- **Commits.** All 12 commits on `public/main..HEAD` are by the GitHub noreply identity, as author and committer. Both new commits carry the trailer.
- **Ancestry and files.** The parent chain is `976fed1` → `cc59c17` → `d2ed344`. `git merge-base --is-ancestor b2fa7f3 HEAD` exits 1. `git diff --name-only 976fed1 d2ed344` lists only `docs/8x4-study.md` (+21 −13).
- **ISA timing check.** Not applicable: no RTL or firmware changed.

## What I ran

- **Locally.** Detached my verifier checkout at `d2ed344`. I read the diff and both messages, then the three paragraphs whole. I compared the wording with `docs/v3-study.md:248-253` and `syn/8x4-study/pe_core_x.sv`, and ran the greps and privacy tools above.
- **`remote_test.sh model` at `d2ed344`.** "model rc=0"; "2916 passed, 29 skipped in 437.76s (0:07:17)"; SIGROK checks skipped: 28, and a pass says nothing about them. The doc-link test is in that suite. The runner1 checkout `~/protoemu-flow/agent-runs/protoemu-verify-d2ed344-2093099` has been removed.

Summary: the fragment is gone, the table-read wording now claims only what the record and this study's prototype show, and the intro matches the gate. The study doc is consistent for the author's pick.
