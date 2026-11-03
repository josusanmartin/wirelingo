VERDICT: FAIL

Commit 7f7a70f, parent b5496aa, base 575362a. No firmware source changed; the commit touches only `usb_enum.timing`, two test files, the sweep script and docs. Claims 2-5 hold:
- the new tests kill all 9 of my mutants;
- the pinned turnarounds are the old firmware's;
- `in_reach` and `hs_reach` are real checks, not vacuous;
- every count reproduces.

It fails on claim 1. The two docs named in report 023 are fixed. But the grep that claim 1 asks for finds three more sentences that give fo6's gate-level results, or the KV260 replay result, for the `usb_enum` loaded today, with no label. Two are in the README.

That is my miss as well: report 023 called README and verification.md "labelled correctly". It checked their KV260 lines and missed these. They are the same claims this commit labels in `guide.md`.

| Claim | Result |
|---|---|
| 1. Old-firmware evidence labelled | **FAIL** (finding 1). info.md, usb-full-speed.md, usb-enumeration.md, guide.md and uart-rx-timing.md are now right |
| 2. New tests catch the four survivors; `FIX_MUTANTS` 25 | PASS. My 9: **9 killed**. Note 2 on how much the short-SE0 test catches |
| 3. `in_reach`, `hs_reach` | PASS, and not vacuous (below) |
| 4. timing-check.md, claims-audit.md | PASS |
| 5. Model 2146 / 0 skipped, `test` 27/27, `test_sigrok` 8/8 | PASS, with sigrok-cli set as described (below) |

## Blocking

1. **Gate-level and FPGA claims for `usb_enum` still unlabelled.** Each sentence below says, in the present tense, that the autonomous USB firmware ran somewhere the 253-word version has not:

   - **`README.md:283-287`**, under "Where it stands": "Its netlist passes all 29 top-level tests at gate level … autonomous full-speed USB enumeration runs on it with real delays."
     - Those receipts used software `7685aba` and the 246-word `usb_enum`.
     - `guide.md`'s "The routed netlist does the same" row says exactly this. This commit labels that row ("have not run at gate level"), but not the README sentence.
   - **`README.md:69-75`**, the KV260 bullet: "At 48 MHz the autonomous USB device enumerates, at low and full speed, against a host model's waveform replayed on its pads by the FPGA's pattern generator".
     - That is the 2026-10-14 replay, which `usb-enumeration.md` now labels "It ran the 246-word firmware … needs a rerun on the KV260".
     - The Linux bullet just above (`README:36-42`) is labelled; this one is not.
   - **`docs/verification.md:358-361`**: "fo6 passes all of it, and the release adds the autonomous USB enumeration tests (low and full speed) with SDF at all three corners".
     - The same receipts and the same firmware. The label at `:253-256` covers the KV260 only ("have not run on the KV260 since").

   The fix is one clause each, as in `guide.md`: ran with the 246-word `usb_enum`; the 253-word one needs a gate-level rerun (README:286, verification.md:360) or a KV260 rerun (README:72).

   How I found them:
   - I listed every tracked file that mentions the KV260, Kria, FPGA, gate level, netlist or SDF and also one of the four programs: 29 files.
   - I read each hit in the files with no `fault-fixes` mention.
   - In the files that have one, I read every line about USB enumeration or the netlist.

   Correctly left alone:
   - the historical notes the item names;
   - `release-evidence.md`, which is about the receipts at `7685aba` and says so;
   - `physical.md:31` and `:647`, run records for named runs;
   - `v3-study.md` and `usb-uart-overlap4.md`, which give no evidence level for these programs.

   `physical.md:647` ("sigrok_spi_master and sigrok_i2c_master pass at typ, 50 MHz", with SDF) is an `i2c_master` gate-level result in a dated run log. It would read better with "(firmware of that run)", but I do not count it.

## Checked

**Claim 1, what is now right:**
- `info.md:46-51` says the four changed programs need an FPGA rerun.
- `info.md:130-142` says the current `usb_enum` is tested in RTL simulation and on the model only, and that the gate-level, KV260 and Linux results were for the 246-word one.
- "RTL simulation" is true: `test/test.py:1231` loads `usbenum.assemble(speed)`, the current source.
- `usb-full-speed.md:17-23` and `:403-407`, `usb-enumeration.md:305-311` and `:334-336`, and `guide.md`'s three rows are labelled.
- `uart-rx-timing.md:41-42` is labelled.

**Claim 2:**
- Pinned turnarounds: `usb_session` run on 575362a's `usb_enum.pasm` gives exactly `USB_TURNAROUNDS` in all five sessions (`old firmware equals USB_TURNAROUNDS: True`).
- With `J_LATE 0`:
  - the four full-speed sessions are unchanged;
  - low speed gives 2.09375 and 2.125 bit times against the pinned 2.15625, which is 1 and 2 clocks of 32 early.
- So the test holds what the doc says, at the resolution it says.
- `FIX_MUTANTS` has 25 entries: 16 plus 9.

**Claim 3, non-vacuity.** I ran the checker on four edited sources, removing instructions on the path from the frame end to the WAIT:

| Source | `in_reach` | `hs_reach` |
|---|---|---|
| real | PASS, lo 8 | PASS, lo 10 |
| `shr r0, 8` removed | FAIL (may never reach), lo 7 | PASS, 10 |
| `mov r0, isr` and `shr` removed | **FAIL, lo 6** | PASS, 10 |
| 3 instructions of `d_crc` removed | PASS, 8 | PASS, lo 7 |
| 4 removed | PASS, 8 | **FAIL, lo 6** |

So `min=7` is enforced at its boundary, and each deadline sees only its own path.
- The test pins `lo == (8, 10)`, so it also notices a slack change.
- The new soundness assertion (`s[n][1] == 0` for all four deadlines) replaces the excuse report 023 objected to.
- `timing-check.md:325-331` now says what the program proves.

**Claim 4.**
- `timing-check.md:247-250` gives the right reason now: the `ldi` is before the release, and the 6 ticks come from the `jmp !pin SDA` after the WAIT.
- `claims-audit.md:29-30` says "was 246 words at this audit (253 since `fault-fixes`)".

**Claim 5.** I ran the stock recipe, then the same suites with sigrok-cli, in my own clone:

| Run | Result |
|---|---|
| Stock `remote_test.sh`, model | `2118 passed, 28 skipped` |
| Stock, `test` | `TESTS=27 PASS=27` |
| Stock, `test_sigrok` | `SKIP=8` |
| My clone, model, `SIGROK_CLI=~/protoemu-flow/sigrok/sigrok-cli` (0.7.2) | **`2146 passed in 299.78s`**, 0 skipped |
| My clone, `make COCOTB_TEST_MODULES=test_sigrok`, same `SIGROK_CLI` | `TESTS=8 PASS=8 FAIL=0 SKIP=0` |

- 2146 = 2131 + 15. The 15 are 5 short-SE0 cases, 1 turnaround test and 9 new fix mutants.
- The new `fault-testing.md:25-28` paragraph about `SIGROK_CLI` matches what I found in report 023.

## Notes

2. **The short-SE0 test catches a lowered timeout only up to about 85 ticks.** I set each of the three timeouts to T and ran `check_short_se0` over all five packets (75 offsets):

   | T (ticks) | `got_in` | `handshake` | `echo` |
   |---|---|---|---|
   | 60 | 21 bad | 19 | 4 |
   | 80 | 15 | 10 | 3 |
   | 90, 100, 110, 119 | 0 | 0 | 0 |

   So a timeout of 90-119 ticks would pass the suite. 90 ticks is 1.88 µs, and 119 ticks would treat a 2.48 µs SE0 as a reset.
   - The doc's "a 1.9 us SE0 … must leave the address set" is accurate. But a 1.9 µs SE0 leaves 0.6 µs below the 2.5 µs threshold unchecked.
   - An SE0 of about 115 clocks (2.4 µs) would cover most of that band.
   - At `echo` only 3-4 of the 75 offsets land in the WAIT, so that site rests on few cases.
   - The firmware's 120 is right, so this does not affect the verdict.

3. The changed `usb_reset_sweep.py` rounds full-speed turnarounds to 0.01 ns instead of 1e-6. One clock is 20.8 ns, so nothing is lost.

## What I ran

On runner1 at 7f7a70f, in `verifier-027/` (now removed) and the `remote_test.sh` checkout (removed).

- `remote_test.sh model test test_sigrok`: summary lines as in the table above; rc 0.
- The same model suite and the RTL `test_sigrok` with `SIGROK_CLI` set: summary lines as in the table above.
- My 9 mutants, each in its own `git archive` copy, each edit checked to apply exactly once. Each was run against the files that load its program, with `-k "not fix_mutant"`:
  - `uart-late`: killed, 2 failed (`test_finding_uart_rx_rejects_a_glitch_as_a_start_bit`, …).
  - `i2c-lsb`: killed, 1 failed (`test_finding_i2c_master_reports_a_stuck_sda`).
  - `i2c-start-scl`: killed, 2 failed (`test_sweep[stuck-i2c_master]`, …).
  - `can-sof-sample`: killed, 6 failed (`test_finding_can_rx_ignores_a_glitch_on_the_idle_bus[...]`).
  - `echo-no-3`: killed, 2 failed (`test_usb_enum_overflow_is_only_its_own_echo`, `test_usb_enum_sees_a_reset_that_starts_anywhere[4]`).
  - `jlate0`: killed, 1 failed (`test_usb_enum_answers_on_the_same_clock_as_before`).
  - `gotin-40`: killed, 3 failed (`test_usb_enum_short_se0_is_not_a_reset[1-3]`).
  - `hs-40`: killed, 2 failed (`…short_se0…[0]`, `[4]`).
  - `echo-1`: killed, 2 failed (`…short_se0…[0]`, `[4]`).
- Probes: the turnaround comparison against 575362a's source, the checker on the four edited sources, and the 18 timeout runs.
- The doc grep and reads above.

Nothing touched the Kria or SWD, and nothing was pushed.
