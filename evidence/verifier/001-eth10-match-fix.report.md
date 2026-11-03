VERDICT: FAIL

Commit aedf991, base 61af9b2. All nine findings from 000 are addressed, and the tests are now much stronger. It fails on one false number: claim 3's 38.2 us lower bound does not hold for frames whose last wire bit is 0, and the test only uses a frame ending in 1. The fix is small: correct the range and test both last-bit values.

| Claim | Result |
|---|---|
| 1. Firmware change is comments only | PASS: assembled `Program` identical at 61af9b2 and aedf991 |
| 2. Tests fail for delay N >= 1 at both clocks, and for payload=2 | PASS: N = 1, 2, 3, 5, 9, 14 fail at 40 and 50 MHz; payload=2 fails |
| 3. Reaction 10-11 clocks; rise 38.2-38.6 us before the last edge | FAIL: reaction holds, the 38.2 bound is false (finding 1) |
| 4. Limits stated and, except 40 MHz, tested | PASS: threshold wrap, FIFO loss, short preamble and HIT pulse are stated and tested; 40 MHz stated and reproduced |
| 5. KV260 section quotes the real log | Format fits; authenticity not checkable from here (finding 3) |

## Findings

### Blocking

1. `docs/eth10-cut-through.md:31`, `test/model/test_eth10_match.py:148`: the rise is not always 38.2-38.6 us before a 64-byte frame's last edge.
   - The range depends on the frame's last wire bit. A frame ending in 1 has a 200 ns TP_IDL edge after it; a frame ending in 0 has its last edge at that bit's mid-bit.
   - Using the commit's own `run()` over 80 frames per clock (fields 2000-2039, 2 frames each):

   | Clock | Last bit 0 | Last bit 1 |
   |---|---|---|
   | 50 MHz | 38.184-38.204 us (34 of 40 below 38.2) | 38.434-38.453 us |
   | 40 MHz | 38.130-38.153 us (40 of 40 below 38.2) | 38.380-38.403 us |

   - The test passes because `wire_frame(2000)` ends in 1, so it never sees the lower group. The 38.6 upper bound is also never approached.
   - This is the same fault as 000's finding 2: a sample's range stated as a bound.
   - Fix: test both last-bit values at both clocks, and state the range that results (38.18-38.46 us at 50 MHz on these frames). The README's "about 38 us" stays true.

### Should fix

2. `docs/eth10-cut-through.md:24`: "On the 5 FPGA frames the phase happened to give 10 clocks each time" is false. It is not chance.
   - The sampler sees the edge on a clock, so the sub-clock spread cannot appear in that measurement. On the model, the rise cycle minus the first clock at or after the mid-bit edge was exactly 10 on 80 of 80 frames at 50 MHz and 80 of 80 at 40 MHz, over 20 seeds.
   - So 10 clocks every time is the expected result, and the KV260 row says nothing about the 10-11 spread. The doc should say that.

3. `fpga/kv260/README.md:925-942`: the log fits the demo's format, but that cannot show it is a board log.
   - Fits: the six `field ...` lines and `summary: eth10_cut PASS` match the `legacy_demo.py` print statements, apart from `run_all`'s two-space indent, which is stripped. The header lines (`bitstream RTL revision ...`, `protoemu clk measured ...`) and the `PASS (n s)` line are omitted from the quote, though the values appear in the prose with the right precision.
   - Consistent: the run ended at 20:14:05, 24 s before 61af9b2 was committed at 20:14:29. RTL `d1aa46f` and digest `3adc796c` match the other 50 MHz sections, clk_div 24 is what the demo requires, and `git diff d1aa46f aedf991 -- 'src/*.v'` is empty. The host code is unchanged since 61af9b2.
   - Cannot tell: the six lines are byte-identical to my `legacy_demo.py --model eth10_cut` output at 61af9b2. That is expected if model and board agree, but it means a format check cannot separate a board log from a model transcript. The demo also never writes `/var/log/wl_steps.log`, so a wrapper must have produced it.
   - I did not touch the Kria (RULES.md). The orchestrator should compare the quoted lines with that file.

### Minor

4. `docs/eth10-cut-through.md:26`: the link anchor `#cut-through-match-on-hardware-2026-10-30` looks broken. The heading at `fpga/kv260/README.md:925` also contains ``, `host/legacy_demo.py eth10_cut` ``, so by GitHub's slug rules the anchor ends `-hostlegacy_demopy-eth10_cut`. Inferred from the rules, not tested on GitHub. `guide.md#known-limits` is fine.

## 000's findings

| # | Status | Evidence |
|---|---|---|
| 1 Timing not asserted | Resolved | N = 1 now fails at both clocks plus the jitter test |
| 2 Range was a sample | Resolved | Doc and test say 10-11 clocks. My 80-frame runs: 10.002-10.996 at 50 MHz, 10.028-10.972 at 40 MHz |
| 3 No frame-end assertion | Resolved, wrong bound | payload=2 and payload=60 both fail, see finding 1 |
| 4 Missing limits | Resolved | All four stated. Threshold wrap, FIFO loss and preamble tested. `in line, 16` and one extra `in line, 1` both fail the preamble test. 40 MHz figure reproduced: seed 24, frame 4 lost, 3 records |
| 5 No KV260 log | Addressed | See finding 3 |
| 6 HIT comment | Resolved | Comment corrected. Moving the fall to the next frame fails the pulse test |
| 7 "24 bits" | Resolved | Comment says 32 |
| 8 README flat 200 ns | Resolved | Says 200-220 ns |
| 9 Stale docstring | Resolved | Matches the 4-tuple return |

## What held on re-attack

- Mutations: pin before the compare, EtherType check deleted and `>` for `>=` are all caught.
- FIFO depth: the doc's 8 words matches `ModelHost` and `src/project.v`. The bare `sim.Chip` default of 4 is not the tapeout's.
- Over-claiming: none found. Evidence levels are unchanged and correct.
- Rules: nothing under `src/` or `flow/` changed.

## What I ran

All on runner1 at aedf991. Throwaway copies were removed afterwards.

- `remote_test.sh model`: `1399 passed, 23 skipped in 569.07s`. Sigrok tests skip on runner1, and I did not run them locally. No lockstep, RTL or formal run: no RTL changed.
- Assembling both revisions: `equal repr: True`.
- Mutations: 14 of 15 caught, and the control passed 9 of 9.
  - The first run was stopped at its time limit after the delay cases, with runner1 at load 16-25. The other nine ran in a fresh copy against only the tests each one targets (`-k`), so their results say nothing about the other tests.
  - Caught: delay N = 1, 2, 3, 5, 9, 14; pin before compare; HIT falls at next frame; `in line, 16`; extra `in line, 1`; EtherType check deleted; `>` for `>=`; payload=2; payload=60.
  - Not a valid run: `push block`. It showed as surviving because my replace edited the new comment at `firmware/eth10_match.pasm:11` and not the instruction. Run directly with the instruction changed (one word differs), it gives 9 records and no HIT on frames 10-12, which `test_full_fifo_loses_records_but_hit_still_rises` rejects by its assertions. I did not rerun pytest on that mutation.
- Probes in a separate clean copy: the frame-end sweep, the quantised reaction, and 40 MHz +-13.5 ns over seeds 0-39.
