VERDICT: FAIL

Commit d811228, base 61af9b2. The firmware and the RX, TX and no-go conclusions hold, and the tests that protect them are sensitive on both model and RTL. It fails on claim 2: 71 clocks is not the longest host stall. It is the figure for one kind of host in one SCK phase, and the doc's verdict and hardware list apply it to hosts that survive far less. A host built to those conditions would lose frames.

| Claim | Result |
|---|---|
| 1. RX 12 clocks per word; 1518-byte and back-to-back exact | PASS |
| 2. One read per 12 clocks or bursts >= 7; longest stall 71; overrun flagged | FAIL: rates and flagging hold, the 71 does not (findings 1, 2) |
| 3. TX 10 clocks per word, no gap; underrun recorded nowhere; far-end FCS fails | PASS (note in finding 5) |
| 4. RX and TX at once needs 147 %; none of 16 mixes worked | PASS |
| 5. Reaction 10 clocks, 9 with bypass; RTL matches at 10 | PASS |
| 6. Pad budget an estimate; positive with REF_CLK in; TX hold -0.43 ns fast with REF_CLK out | PASS: STA reproduced. LAN8720A figures not compared with the datasheet |
| 7. Verdict wording, datasheet labels | PASS |

## Findings

### Blocking

1. `docs/rmii-study.md:11,72,271`: the 71-clock stall budget is wrong for the hosts the doc describes.
   - `:11` gives the RX condition as "a host that reads a word per 12 clocks with no stall over 71 clocks". `:72` says "71 clocks, 1.42 us, worst phase". `:271` tells hardware to "keep stalls under 71 clocks".
   - `test_rx_pause_budget` measures something narrower: one stall, inside a reader running flat out at 8 clocks per word, with `late=False` only. The doc says at `:57` that rate tests run both phases of SCK; this one does not.
   - Measured with the commit's `_burst_ok` on a 1518-byte frame, searching for the longest stall that delivers it:

   | Host | Longest stall, clocks |
   |---|---|
   | 8 or 9 clocks per read, SCK phase as tested | 71 |
   | 8 clocks per read, other SCK phase (`late=True`) | **70**, at all 12 stall positions tried |
   | 10 clocks per read | 67 |
   | 11 clocks per read | 63 |
   | **12 clocks per read, the verdict's host** | **59**, at all 3 stall positions tried |
   | 12 clocks per read, two stalls 150 words apart | **29** each |
   | 7-word bursts, the doc's minimum burst | **45**, at all 4 positions tried |
   | 8-word bursts | 45-57 |
   | 12-word bursts | 59-69 |

   - So a host meeting `:11` exactly, with one 60 to 71 clock stall, loses the frame. The reason is simple: at 12 clocks per read nothing drains the backlog, so a stall's backlog stays in the FIFO for the rest of the frame.
   - Overruns stay flagged, so this is loud rather than silent. It is still a false condition on a Go verdict and in the hardware list.
   - Fix: state the budget per host rate, say a stall needs catch-up headroom, take 70 as the worst SCK phase, and run the test at `late=True` too.

2. `test/model/test_rmii.py:165`: the 71 is not asserted. The bound is `assert 60 <= worst <= 96`.
   - Shown by mutation: moving the `jmp rxf, overflow` check in `firmware/rmii_rx.pasm` from s3 to s1 (timing kept) drops the budget to 67 clocks. The doc still says 71 and the budget test passes.
   - Across the whole file that mutation gives 104 passed, 1 failed. The failure is `test_broken_firmware_fails`, only because that test text-replaces the same source line I moved (`assert broken != src`). It is not a detection.
   - This is the same fault as item 000's finding 1. Fix: assert the exact figure.

### Should fix

3. Other doc numbers are printed but not pinned. Each value is right today; I reproduced all of them.
   - `docs/rmii-study.md:103`, 189 bytes: `test/model/test_rmii.py:548` accepts 100 to 400.
   - `docs/rmii-study.md:104`, 40/90/140 clocks: `test/model/test_rmii.py:577` only asserts `best[72] >= 100` and `best[0] < best[72]`.
   - `docs/rmii-study.md:66`, "10 to 12 ticks": the `turnaround*` deadlines at `firmware/rmii_rx.pasm:52-54` say `max=20`.

4. `docs/rmii-study.md:218-219`: `rmii_match` has limits the doc omits, the ones item 000 had to add for `eth10_match`.
   - The threshold wraps at 16 bits: 0x10005 behaves as 5 (field 5 hits, field 4 does not).
   - `push noblock` drops records while HIT still rises: 12 hits with no reads gave 12 rises and 8 records.
   - Unlike `eth10_match`, HIT here is a latch, as its comment says: it was still high 100 clocks after the last frame ended.

### Minor

5. `docs/rmii-study.md:106-108`: "Nothing on the chip records it" holds, but `STATUS.stalled` (`docs/isa.md:760`) deserves a mention, because someone will propose it as the fix. On the model it reads 1 during an underrun and also 1 idling after a good frame (STATUS 0x1000 both times), so it cannot tell them apart.

6. `docs/rmii-study.md:174`: a 160 MHz RP2350 system clock for TX is, from my memory of the part, above its 150 MHz rating. It is labelled an estimate, but the doc does not say it would be an overclock. Not checked against the RP2350 datasheet.

## What held

- **Claim 1:** checker bound [12, 12], 587 intervals measured. All sizes and the minimum gap pass on the model. RTL passes 1518 + 64 bytes at the minimum gap.
- **Claim 2, rates and flags:** 12 clocks per read sustains and 13 fails; 7-word bursts sustain and 6 fail, on model in both SCK phases and on RTL. Removing the flag set or the record's flag bit fails the tests.
- **Claim 3:** TX firmware is one instruction. RTL sends 1518 bytes with no gap, and the far-end FCS failure test passes.
- **Claim 4:** 8/12 + 8/10 = 146.7 %. All 16 mixes fail. I also tried simultaneous 64, 100, 200 and 400-byte frames, and no mix delivered both, so "half duplex only" stands.
- **Claim 5:** 10 clocks on 8 of 8 model frames, 9 with bypass, 10 on RTL. The 182 clocks before the last dibit is arithmetically right.
- **Claim 6:**
  - I reran `flow/rmii_sta.tcl` at the three corners with fo6 mounted read-only. Output arrivals, input delays and flop setup and hold match `STA_FO6`, and the slack table reproduces.
  - The TX hold of 0.07 ns at the fast corner is positive but thin, and the doc shows it.
  - The script traces a `g_core[0]` net while TX runs on core 1. That is not a fault: `pin_out` is one chip-level register (`src/pe_chip.sv:43`) and the port only aliases it.
- **Claim 7:** every verdict row names its evidence, `:14` and `:16` say no hardware ran, and the README entry says the same. Datasheet behaviour is labelled where used, with a blanket statement at `:248`. No first or only language.
- **External source:** the Tiny Tapeout clock page matches the doc: up to 10 ns insertion delay, RP2040-generated, 1 Hz to 66.5 MHz. It says "Tiny Tapeout 4 and beyond" and names no IHP chip, so the doc's caution is right.
- **Rules:** nothing under `src/` changed. `flow/rmii_sta.tcl` is a new file, so no existing `flow/` file was edited.

## Not checked

- LAN8720A timing tables and section 3.4.1.1 were not compared with the source. I downloaded the datasheet, but it is encrypted and no PDF tool is available on either machine. The doc labels them as datasheet figures, which is what claim 7 asks.
- Gate level was not run. The doc says so.

## What I ran

All on runner1. Throwaway copies were removed afterwards.

- `remote_test.sh model test_rmii_rtl test lockstep` at d811228:
  - `model rc=0`, `1500 passed, 23 skipped in 640.04s`
  - `test_rmii_rtl rc=0`, `TESTS=6 PASS=6 FAIL=0 SKIP=0`
  - `test rc=0`, `TESTS=25 PASS=25 FAIL=0 SKIP=0`
  - `lockstep rc=0`, `TESTS=6 PASS=6 FAIL=0 SKIP=0`
- Model mutations, control 105 passed. Caught: no flag set, no record flag bit, no rxf check, match one instruction later, `>` for `>=`, bypass off, CRS toggling polarity swapped, one extra SCK per word in the port model. Survived: rxf check moved, as finding 2. Each caught mutation ran against the tests it targets (`-k`); the survivor was then rerun on the whole file.
- RTL mutations, in two broken copies:
  - Bypass off, TX delayed and match one instruction later: 5 of 6 fail, the six-word test passes, which is expected since it asserts failure.
  - Overflow reporting removed: exactly the six-word and overflow tests fail.
- Stall, burst and simultaneous-frame probes in a clean copy: the figures in finding 1 and claim 4.
- My core 1 STA run exited with an error because that net does not exist; that is how I found the chip-level register above. No core 1 numbers are used.
