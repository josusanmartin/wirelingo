VERDICT: FAIL

069c 8x4-study fix2 (branch `study-8x4`, `a8b6e2a..976fed1`, `docs/8x4-study.md` only, backlog AN): verifier report.
Kria: verifier does not touch it. No SWD.

069b finding 1's sentence is gone, and every 069b finding-2 note is addressed with correct arithmetic. The v3 word counts quoted (243 / 250 / 280) match `docs/v3-study.md`.

The verdict is FAIL on two problems in the new gate text:
- a broken leftover fragment (finding 1);
- "drop the table read, the addition on that path", which the record contradicts (finding 2).

Both are wording fixes, and no runs are needed.

## Findings

1. **A leftover fragment breaks the gate paragraph.**
   - **Where:** `docs/8x4-study.md:647-648` reads "… redoing gate level, SDF, equivalence and precheck. The untuned 2-core / The base meets 48 MHz at every corner …".
   - **What it is:** "The untuned 2-core" is the start of the removed sentence. In the rendered paragraph it reads "The untuned 2-core The base meets 48 MHz".
   - **The rewrap:** `:646` ("closed, so week 5 is not spent") is left short.
   - **Fix:** delete the three words and rewrap.

2. **"Drop the table read, the addition on that path" claims more than the record shows** (`:651-652`, and the fallback section `:579-581`: "If it misses 48 MHz, the table read is the addition to drop").
   - **What the record says** (`docs/v3-study.md:248-253`):
     - the slow-corner critical paths "start at the SRAM outputs (decode → pin registers and R0–R3/CRC/PC)";
     - the table read adds a load on every DOUT bit;
     - "CMPW and `!t` add a gate in front of the ALU immediate and the condition mux. … Any of these changes reopens that."
   - **What else is on the path:**
     - this doc's own worst path runs through instruction decode (`:111-114`);
     - the any-width CRC widens the CRC register, one of those endpoints.
   - **What is right:** the table read is the one addition that loads the SRAM outputs directly.
   - **What is unmeasured:** it is not the only addition on paths that start there, and no run shows that dropping it alone recovers 48 MHz. That is the "nothing unmeasured about the fallback" check this item asks for.
   - **Fix:** say "drop the table read first: it is the addition that loads the SRAM outputs directly; CMPW and `!t` also add a gate on decode paths (v3-study.md, Timing), so whether that is enough is for a harden to measure". Make the same change at `:579-581`.
   - **Rest of the sentence:** the 243 / 250 / 280 counts are right (finding list below). "Probably no longer fits a bank" is suitably hedged, since the bundle without the table read was never counted.

3. **Minor, no change required.** The intro bullet (`:39-41`) still says "so it is ready at the gate" without the gate paragraph's "if its search closed". Its next sentence now says the 48 MHz margin is for that search to measure, so the bullet is not wrong. Matching "should be ready" would be tidier.

## 069b's findings against the diff

- **069b finding 1.** "The untuned 2-core layout already meets 48 MHz at every corner (base), so the fallback's worst case is a 48 MHz release" is gone. In its place:
  - base slow +0.19 ns (my 069 STA: +0.190);
  - the table read's OSR-mux load on the slow-corner paths (`:409-413`);
  - "its own search measures whether it keeps that margin". ✓

  "It should already be through `release_check.py` at the gate if its search closed" (`:644-646`) is conditional now. ✓

  The fallback section (`:579-581`) and the intro bullet (`:39-41`) each say the margin is unmeasured. ✓

  The fragment and the "that path" wording are findings 1 and 2.
- **069b finding 2, the notes.**
  - **The +0.19 ns label.** `:633` now reads "(the slow corner by +0.19 ns)". ✓
  - **Search length.** "Days 1–2", 25–30 runs at 43–49 min:
    - 25 × 43 = 17.9 h and 30 × 49 = 24.5 h, so "18–25 h" is right, and so is "about a day" (`:39`, `:613`).
    - The 43 / 45 / 49 min are base, baselc8 and my v069base.
  - **The package's search.** "Days 3–7, 25–30 runs at about 3.1 h, 78–93 h, inside the 120 h left": 25 × 3.1 = 77.5 h and 30 × 3.1 = 93 h, against 5 × 24 = 120 h. ✓ This still fits one harden at a time: the fallback's search and then the package's, strictly in sequence.
  - **Low-speed drift.** The arithmetic in `:365-369` checks:
    - period error alone 7 × 0.33 = 2.3;
    - with the P/2 reload's rounding (16 or 15 against 16.67), 2.7–3.7;
    - for a device at ±1.5 %, 33.33 × 0.015 × 7 = 3.5 more;
    - all against a half bit of about 16. ✓
  - **Line widths.** Left as they were, as I allowed (cosmetic). The edited `:633` is now about 95 columns; cosmetic.
- **The v3 word counts.** `docs/v3-study.md` gives:
  - 243 with the bundle (`:124`, `:169`, "243 of 256");
  - 250 "with the table read alone (no CMPW, `!t` or `flag tog`)" (`:181`, and the table row `:51`, "250/256");
  - 280 in v2 instructions (`:179`, `:124`).

  The three counts are quoted correctly, and "the bundle without the table read was not counted" is true: v3-study.md gives no such count.

## Privacy and branch

- **`record_check.py`.** `record_check.py --repo . --tree HEAD --commits refs/remotes/public/main..HEAD` at `976fed1`: "tree HEAD: 719 files checked", "commit messages checked: 10", "TOTAL flagged: 0".
- **My private-string check.** v067/mycheck.py over the doc and the delta (the diff plus the message): "TOTAL 0".
- **Pattern grep.** Over the delta for home paths, tailnet, IPv4 and IPv6 forms, emails, SSID and password: the only hit is the trailer's noreply address.
- **Commits.** All 10 commits on `public/main..HEAD` are by the GitHub noreply identity, as author and committer. `976fed1` carries the trailer.
- **Ancestry.** `976fed1`'s parent is `a8b6e2a`. `git merge-base --is-ancestor b2fa7f3 HEAD` exits 1.
- **Files.** `git diff --name-only a8b6e2a 976fed1` lists only `docs/8x4-study.md` (+27 −12).
- **ISA timing check.** Not applicable: no RTL or firmware changed.

## What I ran

- **Locally.** Detached my verifier checkout at `976fed1`. I read the diff and the message, checked the paragraph in context, and read `docs/v3-study.md` (the word counts and its Timing section). I also ran the privacy tools above.
- **`remote_test.sh model` at `976fed1`.** "model rc=0"; "2916 passed, 29 skipped in 445.19s (0:07:25)"; SIGROK checks skipped: 28, and a pass says nothing about them. The runner1 checkout `~/protoemu-flow/agent-runs/protoemu-verify-976fed1-1961984` has been removed.
- **069b.** Its model-suite line was already complete at `069b-8x4-study-fix.report.md:96`: "2916 passed, 29 skipped in 517.76s (0:08:37)", rc=0, 28 SIGROK checks skipped. No change was needed.

Summary: the unmeasured 48 MHz guarantee is gone, and the finding-2 numbers are right. Delete the leftover "The untuned 2-core" at `:647`. Reword "the table read, the addition on that path" (`:651-652`, `:579-581`): it is the addition that loads the SRAM outputs directly, not the only one on the slow paths. Then this passes.
