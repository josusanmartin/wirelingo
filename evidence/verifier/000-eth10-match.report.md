VERDICT: FAIL

Commit 61af9b2, base 5149c77. The firmware works: its match decisions were right in every case at 50 MHz, and reactions stayed at 10-11 clocks. It fails on proof and on the doc. Claim 1's numbers are not guarded by any test and its upper bound is a small-sample figure, claim 3 has no test assertion, and claim 4 (limits complete) is false.

| Claim | Result |
|---|---|
| 1. 10.0-10.9 clocks, 201-218 ns | FAIL: reproduced for those 8 frames, but the real range is 10.0-11.0 clocks and no test asserts it (findings 1, 2) |
| 2. Below threshold or other EtherType raises nothing; equal raises | PASS: tests catch the mutations; also holds across the full unsigned range |
| 3. About 38 us before a 64-byte frame ends | True as measured, but no test asserts it (finding 3) |
| 4. Limits complete, no over-claiming | FAIL: limits are missing (finding 4). KV260 row is labelled properly but has no log (finding 5) |

## Findings

### Blocking

1. `test/model/test_eth10_match.py:116`: the numbers in claim 1 are not asserted. The only timing bound is `assert max(ns) < 500`, and the jitter test (`:119-128`) has no bound at all, it only prints.
   - Shown by mutation: adding N `mov r1, r1` before `pin HIT, hi` in `firmware/eth10_match.pasm`.
   - N = 1, 2, 5, 6, 7, 8 and 9 all give 5 passed. N = 9 reacts in 19.0-19.9 clocks, 381-398 ns at 50 MHz, about double the claim.
   - N = 10 is caught only by the 40 MHz case. The 50 MHz case first fails at N = 15. N = 14 gives 481-498 ns at 50 MHz and that case still passes.
   - Fix: assert the clock range itself, at both clocks and in the jitter test.

2. `docs/eth10-cut-through.md:18`, and claim 1 as worded: 10.0-10.9 clocks (201-218 ns) is the spread of 8 frames, not a bound.
   - Over 600 frames at 50 MHz, no jitter, seeds 0-149: 10.000-10.999 clocks, so 200-220 ns.
   - Same at 40 MHz: 10.002-10.999 clocks, about 250-275 ns against the doc's 250-271 ns.
   - The doc's row says "8 frames", so that row is accurate as a sample. Claim 1 as stated is not. The range to state is 10-11 clocks: one clock of sampling phase.

3. `test/model/test_eth10_match.py:109-111`: claim 3 has no assertion. The test name and docstring say "before the frame ends", but the test never refers to the frame's end.
   - Shown by changing the test's frames to `payload=2`, which leaves only the FCS after the field: 5 passed.
   - `legacy_demo.py:1347` only checks `mid < rise < edges[-1]`, which any rise before the last edge satisfies.
   - The claim itself is true. I measured 38.44-38.45 us from rise to last edge on 8 frames, and `legacy_demo.py --model eth10_cut` prints 38.2-38.5 us. The difference is whether the last edge is the TP_IDL edge.
   - The number has printed output behind it, not an assertion.

4. `docs/eth10-cut-through.md:31-40`: the limits are not complete. Measured on the model, in a clean copy, unless noted:
   - **Records can be lost silently.** `push noblock` (`firmware/eth10_match.pasm:55`) drops a record when the FIFO is full. With 12 hits and no host pops, 12 HIT rises occurred but only the first 8 records survived. The pin and the records then disagree, and the firmware comment tells the host to check "from the records".
   - **The threshold is truncated to 16 bits** (`firmware/eth10_match.pasm:21`, R registers are 16 bit). Threshold 0x10005 behaves as 5: field 5 hits, field 4 does not. Threshold 0x12345 behaves as 0x2345, so field 100 does not hit. The doc doesn't say a threshold above 16 bits wraps.
   - **Short preambles are missed.** With 31 preamble bits before the SFD the frame hits. With 30, 24 or 16 it is missed with no record, because `in line, 32` (`:26`) swallows the SFD. 802.3's normal preamble is well clear of this, but it is an unstated limit.
   - **40 MHz has no jitter margin.** The doc's table shows 40 MHz without jitter and does not warn. At +-13.5 ns, 1 of 160 frames lost its HIT and its record (159 records). `docs/guide.md:93` already records this limit for the line unit, so the fix is one line in this doc. My larger 40 MHz jitter sweeps were cut off by my 540 s limit, so 1/160 is a single sample, not a rate.

### Should fix

5. `docs/eth10-cut-through.md:21,23`: the KV260 row is labelled correctly but no log backs it. "KV260, the chip's RTL in fabric" states the evidence level, and the limits at `:33-35` say it is generator frames at digital levels, with chip pad and comparator delays unmeasured. That labelling is fine.
   - RULES.md requires a test or log behind a number. The commit has a command but no log, date or bitstream revision, and `fpga/kv260/README.md` has no entry for this run, unlike the other `legacy_demo.py` rows there.
   - The model half reproduces exactly: 10 clocks on all 5 hit frames, 38.2-38.5 us, field 999 no HIT.
   - I could not confirm the FPGA half, so "Model and FPGA agree to the clock" is unverified here. The orchestrator should supply the log and revision.

6. `firmware/eth10_match.pasm:9`: the comment "HIT stays high until the next frame" is false. HIT falls at the end of the same frame, 0.03 us after the frame's last edge, so the output is a pulse about 38 us long on a 64-byte frame. Anyone wiring this as a latch gets the wrong behaviour.

### Minor

7. `firmware/eth10_match.pasm:14`: "at most 24 bits" should be 32 for a standard 64-bit preamble and SFD, since `in line, 32` leaves 32 bits to hunt. Harmless: the 32-bit queue absorbs the backlog (about 16 bits at 40 MHz, by arithmetic).

8. `README.md:174`: "a pin rises 200 ns after" states the bottom of the range as a flat figure. It should match the corrected 200-220 ns.

9. `test/model/test_eth10_match.py:47-48`: the `run()` docstring describes a pin-level return value that does not exist. It returns `(reactions, records)`.

## What held

- **Claim 2:** field equal to threshold hits, below does not, other EtherType does not. Mutations removing the EtherType check, using the wrong constant, removing the threshold check, or changing `>=` to `>` are all caught. Unsigned edges hold: 0xFFFF against 0x8000 hits, 0x7FFF against 0x8000 does not, 0 against 0 hits, 0xFFFF against 0xFFFF hits, 0xFFFE against 0xFFFF does not.
- **Timing against the ISA:** 6 instructions run from the field's `in line, 16` to `pin` at 1 clock each. Each added instruction added exactly 1 clock, and removing `jmp c` gives 9.0-9.9. That leaves about 4 clocks for the synchroniser, bit recovery and output, which is consistent with `docs/isa.md` §4.7 but not cycle-counted through the RTL.
- **Robustness:** 0 of 1,800 frames lost at 50 MHz across no jitter, +-13.5 ns at +100 ppm, and +-13.5 ns at +-200 ppm. Also correct: 20 frames at 9.6 us IFG, a link pulse before a frame, a runt or a frame cut inside the field followed by a good frame, and a 1518-byte frame.
- **By design, as the doc says:** a bad FCS still raises HIT and a VLAN-tagged frame does not match.
- **Over-claiming:** no first, only or fastest language. README evidence reads "model; KV260 (generator frames)", with no RTL simulation claimed.
- **Rules:** nothing under `src/` or `flow/` changed.

## What I ran

All on runner1 at 61af9b2. Throwaway copies were removed afterwards.

- `remote_test.sh model`: `model rc=0`, `1395 passed, 23 skipped in 159.81s`. Sigrok tests skip on runner1 (per `remote_test.sh`); I did not run them locally. No lockstep, RTL or formal run: the commit touches no RTL.
- `pytest -s test/model/test_eth10_match.py`: `5 passed`. Printed: `40 MHz, no jitter: 10.0-10.8 clocks, 250-271 ns`; `50 MHz, no jitter: 10.0-10.9 clocks, 201-218 ns`; `50 MHz, +-13.5 ns: 200-219 ns over 24 frames`.
- `legacy_demo.py --model eth10_cut`: `summary: eth10_cut PASS`, `ALL PASS`.
- 24 firmware and input mutations, one at a time. Caught: delete `pin HIT, hi`, no EtherType check, wrong ETHERTYPE, no threshold check, `>` for `>=`, never lower HIT, no push, wrong field offset, no SFD, big-endian field, delay N = 10, 14, 15, 16, 19, 20. Survived: delay N = 1, 2, 5, 6, 7, 8, 9, and `payload=2`. The unmodified control passed 5 of 5.
- Reaction and robustness probes on the model: 7 sweeps planned, 5 completed. The two 40 MHz jitter sweeps (+-13.5 ns, +-8 ns) timed out, so the 40 MHz jitter figure above comes from an earlier 160-frame run.

Discarded: my first probe run shared a directory with the running mutator and loaded mutated firmware. None of its numbers are used. Every probe figure here is from a clean copy at 61af9b2 with no modified tracked files.
