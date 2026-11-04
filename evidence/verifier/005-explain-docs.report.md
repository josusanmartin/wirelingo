VERDICT: PASS

Commit 7eaaf9c, base 61af9b2. Docs only: `docs/explain-wirelingo.md` and `docs/verification-story.md`. The clock-by-clock trace reproduces exactly on the RTL, the bug story matches the commit, and no number I checked was wrong. The notes below are one imprecise reference, a list of undefined terms, and text in the story that has since gone stale.

| Claim | Result |
|---|---|
| 1. File:line references and clock counts right | PASS, one imprecise reference (note 1) |
| 2. `uart_tx` walkthrough and bug story accurate | PASS |
| 3. Q&A true, hides no weakness; AUTHOR TO ANSWER items invent nothing | PASS |
| 4. Evidence levels right, numbers trace, no first/only/fastest, SWD only as parked | PASS |
| 5. A reader with no hardware background could follow it | PASS, terms flagged (note 2) |

## Notes

1. `docs/explain-wirelingo.md:311`: "DMX512 allows that up to 1 s (`:51`)". Line 51 only says a late host stretches the mark. The 1 s is at `firmware/dmx512_tx.pasm:14` and `:21`. Separately, `:549-550` cites `README.md:50-53`, which has the rule, while `7685aba` itself is at `README.md:45`.

2. `docs/explain-wirelingo.md:3-4` says each term is defined the first time it appears. Most are, and a newcomer can follow the sections in order. These are not defined, or are defined later than first use:
   - **Defined late:** tick (`:45`, defined `:216`), UART (`:49`, defined `:205`).
   - **Section 1:** behavioural (`:63`), hold register (`:101`), bus (`:122`), mask and rotation (`:164`).
   - **Sections 2 to 4:** logic-analyzer (`:289`), `pwm` (`:373`), pins-only device models (`:439`).
   - **Q&A:** tile and overflow (`:484`), 10BASE-T and USB low speed (`:489`), Ethernet (`:541`), half-period read margin (`:558`), latch (`:560`), inferred RAM and clock enables (`:568`), clock gates (`:574`), comparator (`:620`), control transfers (`:630`), scope (`:631`), `SET_CONFIGURATION` (`:632`).
   - **Appendix A:** Verilog and Icarus (`:654`).
   - An engineer is likely to ask about latch, clock gate, 10BASE-T and tile, so those four matter most.

3. `docs/verification-story.md:101-103` was true when written and is now stale. Item 004 has a report and it is FAIL, and item 003 is not listed. Update it before the page goes anywhere. Nothing there says PASS, so nothing is overstated.

4. `docs/verification-story.md:90` gives an absolute path under a home directory. Readers outside this machine cannot follow it, and the line says the reports are outside the repo. Decide whether to publish the reports or drop the path.

## What I checked

- **Trace (claim 1):** I extracted the testbench from Appendix A and ran it on runner1 with the stated command.
  - `SCK_HALF=4 PH=0.1` matches every row of the table: edges 467-473 and 1207-1214. S = 467 and T = 1211 fit the tables in sections 1.3 and 1.5.
  - `SCK_HALF=1 PH=19.9` gives `bus_we` at 332 and the pin at 335, so 6 clocks and 0.1 ns, as stated.
  - Two extra settings also give `bus_we` to pin in 3 edges.
- **Encodings:** I assembled the files. `eth10_match` word 31 is `0x50020008`, word 30 is a `jmp`, `HIT` is at line 18, the pin at line 53, `halt` is `0x60000000`, and the four `uart_tx` words match. `pe.timing` gives 5 of 5 PASS. `dmx512_tx` has 20 instructions.
- **References:** a script printed the target of about 170 `file:line` references, and I read each. Apart from note 1, each lands on what it is cited for, at this commit.
- **`uart_tx` (claim 2):** line numbers, tick counts (80 per byte), the 52 and 21/256 divider, and 72 ticks at 3749-3750 clocks all hold. The side-set on a stalled instruction matches `src/pe_core.sv:222`.
- **Bug story (claim 2):**
  - `git show 2673606` shows the `/` change, and the parent's test ran timing only at 48 MHz.
  - The figures agree: T0 is 30 and 31; lows 600 and 1220 ns; bit 1640 ns; defines 620, 1240 and 1660 ns.
  - The commit message says what the doc says it says. `1369790` is the rerun.
  - "The ESC model tolerated one clock" holds: `BidirEsc` splits bits at half a bit.
- **Q&A (claim 3):** I traced the figures in Q2, Q5, Q11, Q12, Q14, Q16, Q17, Q23 and Q25. The answers name their limits: Q2 the host effort, Q3 that PIO is faster, Q6 that the line unit breaks "protocols are firmware", Q10 that the two implementations are not independent, Q11 what mutation does not prove, Q23 the stall.
- **AUTHOR TO ANSWER (claim 3):** all eight items are questions. The only facts beside them cite `docs/methodology.md`.
- **Story (claim 4):**
  - All 11 cited commits exist and say what the rows say.
  - 29 of 29, 350 of 350, 2 MB, 17.6 kB/s, 32, 66,000, 188, 16 RTL tests, 0.095 ns, -7.6 ns, +4.2 %, one frame in 30, depths 4 and 8, and the 5/6/21/12/2 audit counts all trace.
  - The `eth10_match` account matches my reports 000 and 001, and the 002 row matches report 002.
  - Evidence levels are right, including row 34, which says no RTL simulation.
  - The "Limits of this process" section admits that `61af9b2` sits on `main` with no PASS.
- **Language (claim 4):** the only hits for first, only and similar words are ordinary uses, such as "the first seed". SWD appears twice, both as parked.
- **Rules:** only the two new files changed, and `docs/methodology.md` is untouched.

## What I ran

- `remote_test.sh model` at 7eaaf9c: `1395 passed, 23 skipped in 127.61s`. The count equals base, as expected for a docs-only commit.
- Appendix A on runner1 with Icarus, four settings. Throwaway copy removed.
- Reference script, assembler and `pe.timing` locally. All light.
