# 069d 8x4-study fix3

Branch `study-8x4`, two new commits on `976fed1`, answering 069c's findings
1 and 2 and its optional note 3. Only `docs/8x4-study.md` changes; no
`src/`, `flow/`, `firmware/` or `docs/methodology.md`; no hardens.

- `cc59c17`: the gate paragraph's fragment and the table-read wording.
- `d2ed344`: splits that wording's attribution. The coordinator caught that
  the first version credited v3-study.md with the CRC widening, which is
  this study's.

Tip **`d2ed344`**. `record_check.py --repo . --tree HEAD --commits
refs/remotes/public/main..HEAD`: 0 flagged. `git merge-base --is-ancestor
b2fa7f3 HEAD` exits 1. `test_doc_links.py` passes. Model suite at the tip
on runner1: 2916 passed, 29 skipped.

What changed:

1. **Finding 1, the fragment.**
   - The gate paragraph (`docs/8x4-study.md:643-664`) is rewritten as one
     block.
   - The leftover "The untuned 2-core" is gone, and the short lines are
     rewrapped.
2. **Finding 2, the table read.** In the gate paragraph and in the fallback
   section (`:579-586`), the claim is now "drop the table read first: it is
   the addition that loads the SRAM outputs directly".
   - **The attribution is split.** CMPW and `!t` also add a gate on decode
     paths (v3-study.md, "Timing", which also names the CRC register as an
     endpoint of those paths). The any-width CRC widens that register (this
     study's prototype).
   - So whether dropping the table read is enough is left for a harden to
     measure.
   - The USB-endpoint word counts stay, as the consequence of dropping it:
     243 / 250 / 280, and the bundle without the table read was not counted.
3. **Note 3.** The intro bullet now says "should be ready at the gate".

Before each commit I reread the three edited paragraphs whole: the intro
bullet `:37-41`, the fallback section `:572-586` and the gate `:643-664`.
I also grepped for "addition to drop", "on that path", "worst case" and a
line ending "untuned 2-core"; none remain.

Asked checks:
- Read the diff `976fed1..d2ed344` against 069c's findings.
- Read the three paragraphs in full for fragments or claims beyond the
  record. In particular: v3-study.md "Timing" says CMPW and `!t` add a gate
  in front of the ALU immediate and the condition mux, and lists
  R0–R3/CRC/PC as endpoints.
- Privacy as above.

The usual rules: `--ignore=test/model/test_swd.py` on any model run; no
Kria.
