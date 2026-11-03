VERDICT: FAIL

Commit b5496aa, base 575362a. The four firmware fixes are correct and every number I could rerun matches:
- the word counts, tick costs and turnarounds;
- the reset sweeps on both firmware;
- the fault sweep, the 16 fix mutants, and the RTL and sigrok runs.

The timing checker's change of reference does not loosen what it proves.

It fails on claim 9. Two docs still give the hardware and gate-level results for the `usb_enum` a user loads today, with no label: one is the chip's datasheet page, `docs/info.md`. Claim 9 asks for exactly this to be flagged, and RULES.md requires the evidence level to be stated.

| Claim | Result |
|---|---|
| 1. `uart_rx` mid-bit recheck; 13 to 14 words; sample times unchanged | PASS |
| 2. `i2c_master` read-back; 71 to 90; WRITE +9, READ +3; docs give the new figures | PASS, notes 4 and 5 |
| 3. `can_rx` SOF confirm; 97 to 100; no tick added | PASS |
| 4. `usb_enum` reset anywhere; 246 to 253; same clocks; reference change doesn't loosen the checker | PASS, should-fix 2 and 3 |
| 5. Sweeps, and that they can fail | PASS, all reproduced |
| 6. Finding tests, 16 `FIX_MUTANTS`, two RTL tests | PASS. My own mutants: should-fix 2 |
| 7. 576 cases, 1 second-frame, "told by F1" | PASS |
| 8. Model 2131, `test` 27/27, `test_sigrok` 8/8 | PASS, but not by the stated recipe (note 6) |
| 9. Docs say the KV260 results were for the old firmware | **FAIL** (finding 1) |
| 10. Only firmware, tests, tools and docs changed; bundle regenerated | PASS |

## Blocking

1. Two docs give old-firmware evidence as current.

   **`docs/info.md:126-135`, the autonomous USB device:**
   - It says "Call `bringup.usb_enum(pe, speed)` … Tested in RTL and timing-annotated gate-level simulation, and on the KV260 FPGA build … Linux enumerated and configured it at low and full speed (2026-10-30)".
   - `bringup.usb_enum` loads `protoemu_fw.USB_ENUM_LOW/FULL`. This commit regenerated those as the 253-word firmware (claim 10, checked).
   - So the page tells a user that the program they load ran at gate level and against Linux. Neither is true of it: claim 9 itself says none of the four changed programs has run on the KV260 or at gate level.
   - The file has no `fault-fixes` label anywhere.

   **`docs/usb-full-speed.md:395-399`:**
   - Under "Real hosts" it gives `usb_enum` on the KV260 on 2026-10-14 and against Linux xHCI on 2026-10-30, with the 14/14, 56,000 and 130 figures.
   - It has no label either.

   These are the sentences claim 9 says to flag ("especially usb_enum's hardware enumeration"). README, verification-story, verification, usb-enumeration, uart-rx-timing, pio-comparison, usb-uart-coexist and `fpga/kv260/README.md` are all labelled correctly.

   How I found them: every doc that mentions the KV260, the FPGA or the Kria, and also one of the four programs, sorted by whether it mentions `fault-fixes`. The rest of the unlabelled ones are historical bug notes or generic sentences (note 7).

## Should fix

2. My 9 mutants: **5 killed.** The 4 survivors are all in `usb_enum`, and show two gaps:

   | Survivor | What it shows |
   |---|---|
   | `.define J_LATE 0` | Every low-speed answer moves 1 or 2 clocks; the whole USB suite passes. "The same clock as before at both speeds" is held only by `usb_reset_sweep.py turnarounds`, which is outside the suite. That script does catch it: low speed `turnarounds equal False` |
   | `got_in` timeout 120 → 40 | No test checks that an SE0 **shorter** than 2.5 µs is not a reset. 40 ticks is 0.83 µs |
   | `handshake` timeout 120 → 40 | The same |
   | `echo` timeout 120 → 1 | The same: an SE0 of 2 ticks in the turnaround would be taken as a reset |

   The firmware's values are right: 121 ticks is 2.52 µs at 48 MHz. But every test drives SE0s of 4.2 µs or more. Add:
   - a test that a 1.5 to 2 µs SE0 at each of the three WAITs leaves the address set;
   - a low-speed turnaround assertion in the suite, for example the script's five-session comparison against pinned turnaround figures.

   Killed:
   - the `uart_rx` check moved a tick late;
   - the `i2c_master` compare that drops the LSB (`cmp r0, 4`);
   - `start_fault` without its SCL low;
   - `can_rx` routing the SOF through `sample`;
   - `echo` without its `[3]`.

3. `soundness()` at `test/model/test_timing_check.py:45` excuses any measurement that breaks an assumption, and the USB test asserts only that clean ones exist. So `stall=in_tmo:0` and `stall=hs_tmo:0` are counted by the soundness runs, not checked by them, which is what `docs/timing-check.md:325` says they are. They do hold:
   - In the ten soundness sessions I counted 0 broken measurements: `in_ta` 35/0, `in_ta_any` 40/0, `data_ta` 40/0, `rx_bit` 1600/0.
   - Statically, two deadlines I added, `tok_end.enter → in_tmo` and `d_end.enter → hs_tmo` with `min=7`, pass at 8..134 and 10..134 ticks. So the WAIT is always reached after the J (2..6), and the assumption follows from the program.

   Put those two deadlines in `usb_enum.timing`, or assert that the broken count is 0.

## Notes

4. `docs/timing-check.md:247-249` still says START's high phase is 6 ticks because "its `ldi r3, TMO` sits between the SCL release and the WAIT". This commit moved that `ldi` before the release. The 6 now comes from the `jmp !pin SDA` after the WAIT. The figure is right; the reason is stale.

5. `docs/claims-audit.md:29` says "`usb_enum` … is 246 words now". It is a dated audit, but "now" is wrong.

6. Reproducing claim 8:
   - `remote_test.sh` does not pass `SIGROK_CLI` over ssh. The item's recipe gives `test_sigrok` `TESTS=8 PASS=0 FAIL=0 SKIP=8`, and the model run `2103 passed, 28 skipped`.
   - With `SIGROK_CLI` set in my own clone: `test_sigrok` `TESTS=8 PASS=8`, and every model file that uses sigrok gives `484 passed` with no skips.
   - So 2103 + 28 = 2131 holds, but only with sigrok, run outside `remote_test.sh`.
   - I used my usual `~/protoemu-flow/sigrok/sigrok-cli` (0.7.2). The builder's copy is a shell wrapper reporting the same version.

7. Not flagged: `docs/info.md:46-48` ("Most of it … has also run on an FPGA build") is generic. `docs/methodology.md`, `explain-wirelingo.md` and `review.md` name these programs only for historical bugs. `docs/release-evidence.md` is about the receipts at `7685aba`, where 246 words was right.

## What held

- **Claim 1:**
  - `wait 0 [3]` (D..D+3), `jmp pin RX, start` (D+4), `ldi [6]` (D+5..11): the first sample is still D+12.
  - The two RTL tests fail on the old firmware:
    - `test_uart_rx_glitch`: `AssertionError: {1: [255], 3: [255], 5: [255], 'byte': [180]}`.
    - `test_i2c_master_bus_fault`: `'stuck': [0]`.

    Both pass on the new firmware in `test` 27/27.
- **Claim 2:**
  - The added instructions give a WRITE 4 + 5 ticks and a READ +1 +3 −1. START and STOP keep their tick counts.
  - `pio-comparison.md`'s 575/601 µs and 89 ticks/111 µs follow: a register write is 5 written bytes × 9 ticks × 1.25 µs = 56.25 µs, and a read is 3 × 9 + 2 × 3 = 33 ticks. Both are asserted in `test_pio_comparison.py`, which passed.
  - README, `fpga/kv260/README.md` and pio-comparison have 90 words; timing-check's table is unchanged and correct.
- **Claim 3:** the SOF path reaches `sof:` at S+4 as `jmp sample` did; 3 words are added.
- **Claim 4:**
  - `got_in` +2, `echo` +3, `handshake` +2 words.
  - The checker's bounds equal the old ones: `in_ta` (10, 15), `in_ta_any` (10, 21), `data_ta` (11, 20), the same at 575362a with the old sidecar. Algebra: from J, (Δ − 2..6) + 3..4 = Δ − 3..2, which is the new `adjust`.
  - It still fails a late answer: `[TURN + 4 + J_LATE]` gives `in_ta_any FAIL (16, 27)`.
  - `usb_reset_sweep.py turnarounds`, old against new: all five sessions `turnarounds equal True, packet bytes equal True`. It can fail: one tick of delay gives `False` in all five, and `J_LATE 0` gives `False` at low speed only.
- **Claim 5:** every sweep in the doc reproduced; table below.
- **Claim 6:** the 16 `FIX_MUTANTS` are in the model run and pass, so each one fails its test. Each check passes the real firmware first.
- **Claim 7:** I traced `i2c_master` stuck seed 13:
  - the faulted WRITE replies `0x0, 0x0, 0x200`, F1 set;
  - the next WRITE replies `0x0, 0x0, 0x0`, and F1, cleared just before, is set again by the START that found SDA low;
  - the transfer after that is clean, with F1 clear.

  So the host is told by F1 only, never by a reply, as the doc says. A host that checks only replies would miss it.
- **Claim 10:**
  - `git diff --stat` has no `src/`, `tools/pe/` or model file.
  - `python -m pe.mpy_bundle --check`: "firmware bundle matches validated sources". With `cmp r0, 4` planted, it reports "protoemu_fw.py is stale".

## What I ran

On runner1, at b5496aa and at 575362a, in `verifier-023/`, since removed.

- `remote_test.sh model test test_sigrok`: `2103 passed, 28 skipped in 1430.53s`; `test` `TESTS=27 PASS=27 FAIL=0 SKIP=0`; `test_sigrok` `TESTS=8 PASS=0 FAIL=0 SKIP=8`; rc 0.
- `make COCOTB_TEST_MODULES=test_sigrok` with `SIGROK_CLI`: `TESTS=8 PASS=8 FAIL=0 SKIP=0`. The model's sigrok files: `484 passed`.
- The new RTL tests against the old `uart_rx` and `i2c_master`: each `FAIL`, output above.
- `usb_reset_sweep.py`:

  | Run | Result |
  |---|---|
  | `full 3000 1` | 4400 starts, 0 missed, at most 461 clocks |
  | `full 3000 1 --firmware` old | 4400 starts, **125 missed** (runs at 535-561, 777-800, 1422-1445, 2068-2091, 2673-2698) |
  | `full 480000 37` | 119 starts, 0 missed, at most 455 |
  | `low 20000 4` | 9000 starts, 0 missed, at most 2702 (4299 s) |
  | `low 20000 64 --firmware` old | 563 starts, **11 missed** |
  | `turnarounds` old | 5 of 5 equal |

- The timing probe for claim 4, and the `i2c_master` seed-13 trace for claim 7.
- My 9 mutants, each in a fresh copy, with each edit checked to apply once. Run against the test files that load the program, with `-k "not fix_mutant"`.
