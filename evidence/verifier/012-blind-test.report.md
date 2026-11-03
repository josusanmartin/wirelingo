VERDICT: FAIL

Commit 40fb59a, base 61af9b2. The engineering is good: both firmwares work, the tests are real, every test figure reproduces, and the limits are stated honestly. It fails on what a blind test depends on: its conditions and its headline. The draw cannot be checked because the candidate list is not published, the doc's first sentence oversells "blind", and "largest subset" is false, as a two-line firmware change shows.

| Claim | Result |
|---|---|
| 1. Draw followed the rule; no tool changed; doc does not oversell "blind" | **FAIL**: arithmetic right, list missing, opening oversells (findings 1, 2). No tool changed: confirmed |
| 2. HD44780 fits, 99 words, 60m55s; far ends from public timing | Fit and word count PASS. Time has no log (finding 5) |
| 3. ATA does not fit; largest subset is 8-bit, mode 0, polled, 100 words | No-fit PASS. **"Largest" FAIL** (finding 3). Word count PASS |
| 4. `test_blind` 13/13; model 1450; 29/29 mutants | PASS |
| 5. Limits stated | PASS, one addition in finding 6 |

## Findings

### Blocking

1. `docs/blind-test.md:10`: the draw cannot be checked. "The candidate list has 19 sorted entries", but the list is not in the doc, the repo, the backlog or this reports folder.
   - What I confirmed: sha256("wirelingo blind test 61af9b2") is `fe43bc64...4274bbff`, value mod 19 = 5, (value >> 16) mod 19 = 1.
   - What I could not: that entries 5 and 1 are HD44780 and ATA PIO, or that the list was fixed first. A rule "fixed in advance" (BACKLOG item G) means nothing unless readers can see what it was applied to.
   - Fix: publish the 19 entries in the doc, ideally with evidence of the date they were fixed.

2. `docs/blind-test.md:3-4`: "using only the public docs" is not what happened.
   - The doc's own later section, `:194-199`, says the agent read `tools/`, `test/test.py`, `test/test_post_freeze.py`, `test/test_sigrok.py`, `test/mutants.py`, `test/model/test_onewire.py` and `test_post_freeze_timing.py`, plus firmware named as examples in the brief. It also got annotation syntax from `tools/pe/timing.py`.
   - So the disclosure exists, but it is in section "What the docs did not say", 190 lines after a first sentence that says the opposite. BACKLOG item G says "datasheet only".
   - Nothing about the reading was improper. The fix is to state the conditions at the top: public docs, datasheets, existing firmware, tools and tests as examples. Say also that a same-model agent is not a stranger to this kind of task, so "blind" means "not prepared for this protocol".

3. `docs/blind-test.md:19`, `firmware/ata_pio.pasm:10`: "largest honest subset" is false.
   - **Faster PIO works.** Removing two padding delays, `[8]` in `bbyte` and `[5]` in `bwbyte`, gives a 460 ns byte cycle. I ran reset, SET FEATURES, a two-sector read, and a write read back against the commit's own far end with PIO mode 1 timing:

     | Far end | Sector read | Sector write | Far-end errors |
     |---|---|---|---|
     | Mode 1 | right | right | **0** |
     | Mode 0, same firmware, as the control | right | right | 2044, `t0: 460 ns` |

     The control shows the far end enforces the timing. This is on the model, with mode 1 figures from my memory of ATA-3 Table 21 and the file's own 290 ns t2. My mode 2 run stalled at reset and I did not find out why, so I claim nothing there.
   - The doc half-knows this: `:46` says mode 0 is "not a measured ceiling", which contradicts "Largest" in the verdict table at `:19`. Claim 3 repeats "Largest".
   - **INTRQ can be wired.** The firmware header says it is unwired "because no pad is left". The doc's own count at `:25-26` leaves 5 inputs and IORDY uses one, so 4 are free. Polling is a choice. The output side is really full, so RESET- stays out.
   - Fix: call it "a working subset", give mode 0 as what was tried, and correct the header.

### Should fix

4. Three of my 16 extra mutants survived, all test gaps rather than firmware bugs:
   - ERR ignored in the block check (`and r3, 0x89` to `0x88`). The header promises a refusal on ERR, but the test's error status has DRQ = 0, so the check never decides it.
   - Idle with CS0- low, which the header says must be high.
   - Polling Status instead of Alternate Status. Harmless when polled, but a real device clears INTRQ on it.

5. `docs/blind-test.md`, "Times": 60m55s and 86m32s have no log behind them. The doc calls them log points, and I found no such log in the repo or in runner1's `blind-dev`, and `git log` has one firmware commit, so it cannot show the far ends were written first either. RULES.md wants a log behind a number. Publish it, or state that the times are the agent's own record.

6. `docs/blind-test.md`: ATA takes all of `uo[6:0]`, and `uo[6:4]` are the quad port's pads, so the host port runs 1-bit only with this firmware. That is a real limit on sector rate, and the doc does not state it. `:25-26` only says "once the SPI port has its 4".

## What held

- **No-fit reasoning (claim 3):** 8 bidirectional pads against 16 data lines holds. The output-only pads cannot release, which is why extra outputs do not help. The CFA dependence is stated, and a 16-bit device is tested as the no-fit.
- **Conditions (claim 1):** `git diff 61af9b2 40fb59a` adds only new files: the doc, two firmwares, tests and `test/blind_mutants.py`. No assembler, model, tool or RTL change. `3218e99` to `40fb59a` is docs only, confirmed.
- **Far ends (claim 2):** the headers cite their sources and mark memory items. The HD44780 and ATA figures agree with what I know of those tables. The far ends can fail: the mode 0 control above, and the doc's "far-end teeth" tests. The doc's list of 19 wrong turns is unusually candid.
- **Test strength:** my 16 mutants cover both firmwares. 13 were caught, including every HD44780 one: a short power-on wait, never switching to 4-bit, wrong BF bit, swapped nibbles, RW left high, short tH.
- **Limits (claim 5):** the reset window, secondary sources for 8-bit mode, the Linux source for 290 ns, the 3.3 V assumption and sigrok's generic decoder are all stated, plainly and in the right places.
- **Evidence level:** the opening says model and RTL simulation only, and that nothing ran on a real LCD or disk. No first or only language.

## What I ran

On runner1 at 40fb59a. Throwaway copies were removed afterwards.

- `remote_test.sh model test_blind`: `1450 passed, 23 skipped in 843.30s`, `test_blind rc=0`, `TESTS=13 PASS=13 FAIL=0 SKIP=0`.
- With sigrok, using `~/protoemu-flow/sigrok/sigrok-cli`: `TESTS=13 PASS=13`, and both "sigrok parallel decoder ran" lines appear.
- `test/blind_mutants.py`: `29/29 killed`, rc 0, after baselines passed.
- My 16 mutants. 9 ran against both test files. The other 7 hit my time limit under load and were rerun against `test_ata_pio.py` alone.
- The PIO mode experiment. Two early attempts failed on faults in my script: the wrong method name, then a leftover edited file. The table is from a clean rerun.
- Word counts assembled: 99 and 100.
