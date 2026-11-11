# 069c 8x4-study fix2

Branch `study-8x4`, one new commit on `a8b6e2a`, answering 069b's finding 1
and its finding 2 notes. Only `docs/8x4-study.md` changes; no `src/`,
`flow/`, `firmware/` or `docs/methodology.md`; no hardens.

- `976fed1`: the fallback's 48 MHz margin is unmeasured; the cosmetics.

Tip **`976fed1`**. `record_check.py --repo . --tree HEAD --commits
refs/remotes/public/main..HEAD`: 0 flagged. `git merge-base --is-ancestor
b2fa7f3 HEAD` exits 1. `test_doc_links.py` passes. Model suite at the tip on runner1:
2916 passed, 29 skipped.

What changed:

1. **Finding 1, the gate paragraph.**
   - "The untuned 2-core layout already meets 48 MHz at every corner
     (base), so the fallback's worst case is a 48 MHz release" is gone.
   - In its place: the base meets 48 MHz at every corner (slow +0.19 ns).
     The fallback adds the bundle's load on the SRAM output paths (the table
     read's OSR mux, on the paths that set the slow corner), so its own
     search measures whether it keeps that margin.
   - The branch if it misses 48 MHz too: drop the table read, the addition
     on that path, and keep the rest (CMPW, `jmp !t`, `flag tog`,
     bank-relative PC, the any-width CRC, the tick counter).
   - The cost is stated from the v3 study's counts only. The USB device
     with a data endpoint then probably no longer fits a bank: 243 words
     with the whole bundle, 250 with the table read alone, 280 with none.
     The bundle without the table read was never counted.
   - The same paragraph's "it is already through `release_check.py` at the
     gate" now reads "should be ... if its search closed".
   - The bottom-line fallback bullet and the fallback section each gain a
     sentence saying the fallback's 48 MHz margin is unmeasured, and that
     the table read is the addition to drop.
2. **Finding 2.**
   - **The release-clock paragraph's +0.19 ns** is now "(the slow corner by
     +0.19 ns)".
   - **The fallback's search** is now days 1–2 (25–30 runs at 43–49 min,
     18–25 h), and the package's days 3–7 (25–30 runs at about 3.1 h,
     78–93 h, inside the 120 h left). The intro says "about a day", and so
     does the bottom line.
   - **The low-speed drift note** now reads: 2.3 clocks from the period
     error alone; about 2.7–3.7 with the P/2 reload's rounding; and about
     3.5 more for a device at the edge of low speed's ±1.5 % rate. All of
     this is against a half-bit margin of about 16.
   - **Line widths** were not touched: they are cosmetic and were already
     there at `65d0e8e`.

Asked checks:
- Read the diff `a8b6e2a..976fed1` against 069b's findings 1 and 2.
- Check that the new gate text claims nothing unmeasured about the
  fallback.
- Check the v3 word counts quoted (243 / 250 / 280) against
  `docs/v3-study.md`.
- Privacy as above.

The usual rules: `--ignore=test/model/test_swd.py` on any model run; no
Kria.
