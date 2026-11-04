VERDICT: PASS

Commit e5dd3d0, base 40fb59a. Every finding from 012 is dealt with, and each claim holds. The faster PIO modes are tested on both model and RTL, and they are enforced by a far end that can fail. One small wording point about IORDY coverage is in note 1.

| Claim | Result |
|---|---|
| 1. List and disclosure published, SWIM, "can't be proved" | PASS |
| 2. Opening states the real conditions and what "blind" means | PASS |
| 3. "A working subset"; INTRQ header; modes 1-4; mode 4 rate; quad-port cost | PASS, note 1 |
| 4. Three survivors killed; `test_blind` 17/17 with sigrok; 34/34; model 1464 | PASS, all reproduced |
| 5. Times labelled as the agent's own; reset window stated | PASS |
| Docs only after 82f6195 | PASS: `git diff --stat 82f6195 e5dd3d0` is `docs/blind-test.md` alone |

## Notes

1. `docs/blind-test.md`, under "Faster PIO modes": "stayed correct with IORDY held 700 ns, on the model (2 sectors) and on the RTL through SPI (1 sector)" says a little more than the tests do.
   - The model's IORDY test moves 1 sector. The 2 sectors are the plain read/write test.
   - On the RTL, `ata_mode_run` holds IORDY on the read only. Writes with IORDY held are tested on the model alone.

   Say that, or add the write to the RTL run.

2. My extra mutants for the new modes, run with the exact-figure assertion removed so that only the far end could catch them:

   | Mutant | Result |
   |---|---|
   | Far end: mode 3 t2 to 150, t2i to 90, t0 to 230; mode 1 t0 to 430, t2 to 350; mode 4 t1 to 120 | 6 killed: the far end enforces each new figure |
   | Build: modes 1 and 2 at TS 10 (t2 280, under 290) | killed |
   | Build: modes 3 and 4 at TS 0 | killed |
   | Build: modes 3 and 4 at TS 2 | survived |

   The TS 2 survival is expected. "Wrong turns" 22 says TS 2 sampled IORDY 5 ns *past* tA, which is legal, and TS 3 is margin. The exact figures in `MODE_SEEN` would catch this mutant in the real test anyway.

## What held

- **The draw (claim 1):** the 19 entries are in ASCII order. I recomputed the sha256, `fe43bc64...4274bbff`: mod 19 is 5, (value >> 16) mod 19 is 1, and those entries are HD44780 4-bit and ATA PIO. The doc says the list came from an AI orchestrator, began with 20 entries with SWIM removed before the draw, and that fixing it in advance "cannot be proved".
- **Conditions (claim 2):** the first paragraph states public docs, datasheets, and existing firmware, tools and tests as examples. It says "blind" means "not prepared for this protocol", not a stranger.
- **Claim 3:**
  - "A working subset" replaces "largest". "No maximum is claimed."
  - The firmware header now says INTRQ is left out by choice, with 4 input pads free. The quad-port cost, the host port at 1 bit, is in the doc and the header.
  - The mode table matches the far end's `MODES`, which match my memory of ATA-3 Table 21, mode 2's 8-bit t0 marked as memory. The measured figures equal `MODE_SEEN` and `ATA_MODE_SEEN`, asserted exactly on model and RTL.
  - Mode 4's 220 ns against 120 ns is stated as timing met, rate not. A control test shows a fast build on a mode 0 device is caught.
- **Claim 4:** `test/blind_mutants.py` gained the three survivors from 012, written as I wrote them (`and r3, 0x89` to `0x88`, CS0- low at idle, Status for Alternate Status), plus the second Status site and early IORDY.
- **Claim 5:** "Times" says "the agent's own record, with no log behind them", and that git cannot show the order of work. The reset window is the first item under Open, unresolved.

## What I ran

On runner1 at e5dd3d0. Scratch was in `verifier-018/`, since removed.

- `remote_test.sh model test_blind`: `1464 passed, 23 skipped in 402.92s`, `test_blind rc=0`, `TESTS=17 PASS=17 FAIL=0 SKIP=0`.
- With sigrok (`SIGROK_CLI=~/protoemu-flow/sigrok/sigrok-cli`, `make COCOTB_TEST_MODULES=test_blind`): `TESTS=17 PASS=17`, with both "sigrok parallel decoder ran" lines.
- `test/blind_mutants.py`: `34/34 killed`, rc 0.
- My 9 mutants in note 2, in a separate copy.
