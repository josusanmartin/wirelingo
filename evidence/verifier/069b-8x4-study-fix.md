# 069b 8x4-study fix

Branch `study-8x4`, two new commits on `65d0e8e` (the tip verifier 069
passed on the measurements), answering 069's findings 1–5. Only
`docs/8x4-study.md` changes; no `src/`, `flow/`, `firmware/` or
`docs/methodology.md`; no new hardens.

- `0c659f8` findings 1–4, and the "+0.19 ns" wording
- `a8b6e2a` finding 5 (the plan)

Tip **`a8b6e2a`**. `record_check.py --repo . --tree HEAD --commits
refs/remotes/public/main..HEAD`: 719 files, 9 messages, 0 flagged.
`git merge-base --is-ancestor b2fa7f3 HEAD` exits 1. `test_doc_links.py`
passes. Model suite at the tip on runner1: 2916 passed, 29 skipped.

What changed, keyed to 069's findings:

1. **Clock NDRs.** pkg3 dropped the NDRs of 18 distinct clock nets, counted
   from the step logs, which don't wrap net names (65 `GRT-0273` warnings
   there, 66 in `exp.log`). 069 says "about 19". The doc now says 18 and
   gives both warning counts. It adds that `grt3` already dropped 6 and
   `grt3pkg` 14; base and fo6 dropped none. `grt4f2d58`: 1000 extra
   iterations (the doc said 1001), and 20 distinct nets (it said 25, an
   `exp.log` warning count taken mid-run).
2. **Attributions.**
   - The +1.5 % core wire for CRC-32 is now cited as physical.md's
     `core_route.sh` figure, in both the text and the ranking table.
   - The +2.2K per core is labelled as this study's synthesis (the v3 study
     reported +2.06K).
   - Synthesis noise: ±0.5K here, and the v3 study's ±0.4K.
   - v3's `tools/pe` diff against its base: 9 files, +152 −37.
   - KV260 crossings: +0.196 to +0.474 ns, and the 400 MHz domain itself
     +0.520 ns.
   - The "27 files" is replaced by the grep and its counts:
     `git grep -l -i -E "6x4|two cores"` finds 41 tracked files besides the
     study's own; 38 are outside evidence/, and 23 are in docs/, README and
     info.yaml.
   - The two Lean testbenches with `NCORES(2)` are named
     (`rtl_fetch_ownership_tb.sv`, `rtl_rx_autopush_tb.sv`).
3. **Item numbers.**
   - `jmp !t` is item 1, and the compares are items 2 and 25.
   - Both lookups are item 4; item 15 is removed from the lookup and
     package lists.
   - Item 5 now reads "Wiegand's formats (the item); by extension other
     variable-length fields".
4. **Low-speed USB.**
   - A new paragraph, marked as a reading of the spec: the receiver re-syncs
     on every transition and stuffing bounds the run to 7 bits. At 50 MHz,
     P = 33 against 33.33 (−1.0 %), about 2.3 clocks of drift against a
     half-bit margin of about 16. So LS USB plus 10BASE-T at 50 MHz looks
     feasible on paper, and not at 48.
   - The bottom line, ranking, left-out table and recommendation now leave
     the second line unit out mainly on routing (grt3pkg's 518, a run that
     carries the whole ISA set too). The full-speed clock argument stays.
5. **The plan.**
   - The intro states that both layouts miss 50 MHz untuned, so each needs
     its own search and receipts, and that runner1 runs one harden at a
     time.
   - Week 3 is now: day 1 the 2-core fallback search (about 45 minutes a
     run, under a day), then its receipts beside the package's 3–4 day
     search. Receipts are simulations, not hardens.
   - Week 4: the package's receipts, and the fallback's finish. So either
     way a netlist with complete receipts exists at the Dec 6 gate.
   - Week 5's KV260 build is for whichever was chosen.
   - The bottom line and the fallback section say the same.
   - Also: 48 MHz's +0.19 ns is now labelled as the slow corner.

Not changed: 069's three FAQ points with no local copy ("more on-chip
scores better", "at once or both roles", "60 MHz is fine to request").
They come from the coordinator's brief to the builder and stay attributed
to the FAQ as paraphrased there.

Asked checks:
- Read the diff `65d0e8e..a8b6e2a` against each finding. The NDR counts
  are on runner1:
  `cat ~/protoemu-flow/study-8x4-runs/protoemu-exp-<run>/runs/wokwi/*/*.log | grep GRT-0273 | sed "s/.*for net: //" | sort -u | wc -l`
  This gives pkg3 18, grt3 6, grt3pkg 14, grt4f2d58 20.
- Check the low-speed paragraph's arithmetic against `docs/isa.md` §4.7.
- Check that the plan's week 3–4 order fits one harden at a time.
- Privacy as above.

The usual rules: `--ignore=test/model/test_swd.py` on any model run; no
Kria.
