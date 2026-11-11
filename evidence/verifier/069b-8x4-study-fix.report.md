VERDICT: FAIL

069b 8x4-study fix (branch `study-8x4`, `65d0e8e..a8b6e2a`, `docs/8x4-study.md` only, backlog AN): verifier report.
Kria: verifier does not touch it. No SWD.

All five 069 findings are fixed correctly, and the builder's two count corrections are right: pkg3 18 distinct nets, grt4f2d58 20. My 069 "about 19" came from `exp.log`, which wraps net names, so 18 is the correct figure. The low-speed paragraph's arithmetic matches `docs/isa.md` §4.7, and the week 3–4 order fits one harden at a time.

The verdict is FAIL on one new sentence in the finding-5 fix (finding 1). It turns "the base meets 48 MHz" into a guarantee for a netlist that has never been hardened, and that contradicts the doc's own timing caveat. The fix is one sentence. Everything else in this delta passes.

## Findings

1. **New unproved claim: "the fallback's worst case is a 48 MHz release"** (`docs/8x4-study.md:639-641`).
   - The fallback is two cores *plus* the four additions. No run hardened that netlist. The evidence cited, "the untuned 2-core layout already meets 48 MHz at every corner (base)", is for today's RTL, with a slow-corner margin of only +0.190 ns.
   - The doc itself says the bundle's table read "gives each SRAM output bit one more load (the OSR mux), on the paths that set the slow corner … a timing risk that only a harden measures" (`:409-413`). The worst slow path starts at bank 0's `A_DOUT[3]` (`:111-112`, and my 069 check).
   - The only hardened netlist with these additions, pkg3 (on three cores), is −0.738 ns at 48 MHz on the slow corner (`:272`, `:626`).
   - So whether the fallback meets 48 MHz is unmeasured, and the gate has no stated branch for the case where it misses. The 65d0e8e wording ("starts from a layout that already meets 48 MHz") was accurate. The new sentence over-claims.
   - Fix: drop the sentence, or replace it with something like "The base meets 48 MHz at every corner (slow +0.19 ns); the fallback adds the bundle's load on the SRAM output paths, so its own search measures whether it keeps that margin." Optionally state what happens if the fallback misses 48 MHz too.

2. **Minor notes, no change required.**
   - **One unlabelled +0.19 ns.** `:44` and `:127` now name the slow corner. `:625-626` ("meets 48 MHz at every corner (+0.19 ns)") still doesn't. Cosmetic.
   - **"Day 1" for the fallback's search is tight.** 25–30 runs at "about 45 minutes" is 19–22.5 h. The measured 2-core hardens took 43 min (base), 45 (baselc8) and 49 (my v069base), and the fallback netlist is 13.6K µm² larger. At 49 min, 30 runs is 24.5 h, so the search may spill into day 2. The package's 25–30 runs at about 3.1 h (78–93 h) still fit days 2–7 (144 h), so the week holds. The figure is a paper estimate, which "about" and "under a day" already signal.
   - **Low-speed drift.** "About 2.3 clocks of drift over 7 bits" counts the period error only (7 × 0.33). Adding the P/2 reload's rounding (16 or 15 against 16.67) brings the offset at the 7th sample to about 2.7–3.7 clocks. A low-speed device's own ±1.5 % rate tolerance would add about 3.5 more. That is still well inside the ~16-clock half bit, so "looks feasible on paper" stands. Precision only.
   - **Line width.** The edited line `:44` is now 108 columns, beside a 143-column line at `:45` that was already there at `65d0e8e`. Cosmetic.

## The 069 findings against the diff

1. **Clock NDRs.** On runner1, using the item's command, plus warning counts in the step logs and in `exp.log`. All reads were read-only.

   | run | step-log warnings | distinct nets (step logs) | `exp.log` warnings |
   |---|---|---|---|
   | base | 0 | 0 | 0 |
   | baselc8 | 0 | 0 | 0 |
   | grt3 | 6 | **6** | 7 |
   | grt3pkg | 14 | **14** | 15 |
   | grt4f2 | 0 | 0 | 0 |
   | grt4f2d58 | 20 | **20** | 26 |
   | pkg3 | 65 | **18** | 66 |
   | fo6 (read-only) | 0 | — | — |

   - **pkg3.** Its 65 step-log warnings are spread over steps 38 (global routing, 7), 40 (18), 43 (19) and 45 (21). The 18 names are distinct nets, for example `clknet_0_…rxf.g_ent[3].gclk` and the unbuffered `…rxf.g_ent[3].gclk`, plus `clknet_sram_adv_*`.
   - **`exp.log`.** It carries one more warning than the step logs: a `flow.py` echo of the last warning. It also wraps net names onto the next line, which is why my 069 count from it was off by one. The doc's "18 distinct … (65 … step logs, 66 in `exp.log`)" is exact, and so are grt3 6, grt3pkg 14, and "base and fo6 none" (`:279-282`).
   - **grt4f2d58.** "20 nets" (`:252`) matches its step log. The old 25 was an `exp.log` count; the finished log now has 26. "1000 extra overflow iterations" (`:249`, `:581`) matches my 069 count.

2. **Attributions.**
   - **+1.5 % core wire.** Now cited as physical.md's figure (`:446-447`, ranking table `:510`). `docs/physical.md:432` is "What the v2 additions cost", and its 32-bit CRC row at `:449` is "+1.5 % over CALL/RET". ✓
   - **+2.2K per core.** Now labelled as this study's synthesis, "the `2 V3_CRC32` row, 4,394 over two cores; the v3 study reported +2.06K" (`:436-438`). `results.txt:30` gives 299,612, and 299,612 − 295,218 = 4,394. ✓
   - **Synthesis noise.** "±0.5K here (the v3 study gave ±0.4K)" (`:186`). ✓
   - **v3's `tools/pe` diff.** "9 files in `tools/pe/`, +152 −37" (`:415-416`), as I measured in 069. ✓
   - **KV260 crossings.** "+0.196 to +0.474 ns at 400 MHz (the domain itself +0.520 ns)" (`:672-673`), as `fpga/kv260/README.md` gives. ✓
   - **The file count.** I reran `git grep -l -i -E "6x4|two cores"` at `a8b6e2a`, leaving out the doc and `syn/8x4-study/`. It gives 41 files, 38 outside `evidence/`, and 23 in `docs/`, README and info.yaml, as `:620` says. ✓
   - **Lean testbenches.** `grep -l "NCORES(2)" formal/lean/*_tb.sv` gives exactly `rtl_fetch_ownership_tb.sv` and `rtl_rx_autopush_tb.sv`, 2 of 4 (`:299-301`). ✓

3. **Item numbers.**
   - "`jmp !t` fixes item 1; CMPW covers the compares of items 2 and 25" (`:403-404`). CMPW is the v3 compare (`docs/v3-study.md:52`, `:73`). ✓
   - "(both item 4)" for the lookups (`:403`). ✓
   - Item 15 is gone from the lookup line and from the package list (`:549`, now "items 1, 2, 4, 11, 23, 25"). No "item 15" or "15)" is left in the doc. ✓
   - Item 5 now reads "Wiegand's formats (the item); by extension other variable-length fields" (`:488`). ✓

4. **Low speed** (`:360-367`), checked against `docs/isa.md`:
   - `:271`: LINEDIV `[31:16]` gives the RX clocks per bit, at least 4.
   - `:329-330`: "a transition sets the sample counter to P/2, or to P/2 - 1".
   - `:333`: the frame ends with no transition for STUFF_K + 2 bits.

   With USB's stuff-after-6, a run is at most 7 bit times between transitions. 50 / 1.5 = 33.33, and P = 33 gives −1.0 %. 7 × 0.33 ≈ 2.3 clocks, against P/2 = 16 (finding 2 has a precision note). 10BASE-T is 50 / 10 = 5. At 48 MHz, 10BASE-T is 4.8. The arithmetic is right, and the paragraph is marked "by a reading of the spec (not a test)".

   The headline, the ranking row `:513`, the left-out row `:582` and the recommendation bullet `:560-564` now lead with grt3pkg's 518 overflows and keep the full-speed 60 MHz argument. "Though that run carries the whole ISA set too" is stated. The full-speed text is unchanged and correct. ✓

5. **The plan** (`:600-641`).
   - **The intro** states that both layouts miss 50 MHz untuned (−0.643, −1.571), so each needs its own search and receipts. It names the receipts and says runner1 runs one harden at a time. ✓
   - **Week 3** runs the fallback search on day 1, then the package search on days 2–7. Hardens run strictly one after another, so the order fits the rule.
   - **The receipts** (gate-level functional, equivalence, SDF, precheck and `release_check.py`, which reads receipts and runs no flow) are not hardens. Running them beside the one harden is allowed, since RULES limits hardens, not other heavy compute on runner1. They will slow the concurrent harden's wall clock somewhat (finding 2's day-1 note).
   - **Week 4** puts the package's receipts and the fallback's finish before the Dec 6 gate.
   - **Week 5** builds the KV260 bitstream for whichever was chosen.
   - **Dependencies.** "The fallback's RTL is the package's with `PE_NCORES` 2, so it exists at the end of week 2" is consistent with week 2.
   - **The intro bullet** (`:38-40`) and the fallback section (`:572-575`) say the same thing.
   - **The exception** is the sentence in finding 1.

   "48 MHz's +0.19 ns is now labelled as the slow corner": done at `:44`, but not at `:626` (finding 2).

**Not changed, as stated.** The three FAQ points stay attributed to the FAQ as paraphrased. I still have no local copy, so I left them unchecked, as in 069.

## Privacy and branch

- **`record_check.py`.** `record_check.py --repo . --tree HEAD --commits refs/remotes/public/main..HEAD` at `a8b6e2a`: "tree HEAD: 719 files checked", "commit messages checked: 9", "TOTAL flagged: 0".
- **My private-string check.** v067/mycheck.py over the doc and the delta (the diff plus both messages): "TOTAL 0".
- **Pattern grep.** Over the delta for home paths, tailnet, IPv4 and IPv6 forms, emails, SSID and password: the only hits are the two `Co-Authored-By` trailers' noreply address.
- **Commits.** All 9 commits on `public/main..HEAD` are by the GitHub noreply identity, as author and committer. Both new commits carry the trailer.
- **Ancestry.** `git merge-base --is-ancestor b2fa7f3 HEAD` exits 1.
- **Files.** `git diff --name-only 65d0e8e a8b6e2a` lists only `docs/8x4-study.md` (+77 −42). No `src/`, `flow/`, `firmware/` or `docs/methodology.md` change.
- **ISA timing check.** Not applicable: no RTL or firmware changed.

## What I ran

- **Locally.** Detached my verifier checkout at `a8b6e2a`. I read the full diff and both messages, then ran the greps and the two privacy tools above.
- **runner1, read-only.** Ran the item's NDR count command and warning counts on the seven study runs and on fo6, and read the grt4f2d58 global-routing log. I created no scratch.
- **`remote_test.sh model` at `a8b6e2a`.** "model rc=0"; "2916 passed, 29 skipped in 517.76s (0:08:37)"; SIGROK checks skipped: 28, and a pass says nothing about them. The runner1 checkout `~/protoemu-flow/agent-runs/protoemu-verify-a8b6e2a-1941166` has been removed.

Summary: findings 1–4 of 069 are fixed exactly, and the builder's 18- and 20-net counts are correct. Finding 5's budgeting is right and fits one harden at a time. But the new closing sentence at `:639-641` claims the untested fallback is guaranteed 48 MHz, which the doc's own timing caveat contradicts. Reword it (finding 1) and this passes; no new runs are needed.
