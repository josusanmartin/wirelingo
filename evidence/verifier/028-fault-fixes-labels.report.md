VERDICT: PASS

Commit b2560de, parent 7f7a70f, base 575362a. The commit changes five files: README.md, fault-testing.md, physical.md, verification.md and test_faults.py. No firmware, no `src/`, no `flow/`. All three claims hold. There are two minor notes below; neither blocks.

| Claim | Result |
|---|---|
| 1. The three sentences and physical.md:647 labelled; nothing else unlabelled | PASS (note 2 on review.md) |
| 2. 2.4 us SE0; real firmware 0 failures; each timeout at 100 caught (21, 16, 3); 2.4 us below the 2.52 us threshold with margin | PASS, every number reproduced (note 1 on what is still uncaught) |
| 3. Model 2146 passed, 0 skipped, SIGROK_CLI set | PASS: `2146 passed in 369.21s` |

## Checked

**Claim 1.**
- `README.md:75-77`: the KV260 bullet now says it was the 2026-10-14 replay with the 246-word `usb_enum`, and that the 253-word one needs a KV260 rerun. This matches `usb-enumeration.md` and `fpga/kv260/README.md:1478`.
- `README.md:289-291`: "These receipts ran software `7685aba`, with the firmware of that time; `uart_rx`, `i2c_master`, `can_rx` and the 253-word `usb_enum` … need a gate-level rerun."
  - `7685aba` is the recorded release (`release-evidence.md:182`).
  - It names all four changed programs. That is right, because the 29 GL tests include all four, not just `usb_enum`.
- `docs/verification.md:361-363`: the same label, "receipts for software `7685aba`, with the 246-word `usb_enum` … needs a gate-level rerun".
- `docs/physical.md:647-648`: "with the firmware of that run".
- The sentence after the README label, "the 16 post-freeze protocol tests pass on the netlist", is not affected: `post-freeze-protocols.md` uses none of the four programs.

**The grep, re-run.** Tracked files that mention KV260, Kria, FPGA, gate level, netlist or SDF and also one of the four programs: 37 in all (23 docs, 8 under `fpga/kv260/`, 6 test files).
- My 027 report said 29. This run counted the non-doc files too.
- In the 23 docs I listed every paragraph or table row that has an evidence word and a program, but no label (fault-fixes, 246-word, 253-word, rerun, 7685aba, "that run"). That gave 34 hits.
- I read all 34. They are:
  - dated run logs (`fpga/kv260/README.md`, under its page banner at `:11`, "Firmware changed since these runs");
  - receipt tables (`release-evidence.md`);
  - claims-audit rows;
  - `uart_rx_*` capture variants, which `fault-fixes` did not change;
  - the `i2c_sniff` row (`README.md:188`), whose evidence is about `i2c_sniff` running beside `i2c_master`. The `i2c_master` row itself (`:172`) is labelled.
- None claims gate-level or FPGA results for the changed programs in the present tense, apart from note 2.

**Claim 2.** `test_faults.py:1356` sets `USB_SHORT_SE0 = 115` (2.396 us at 48 MHz). I ran `check_short_se0` over all 75 start points:

| Firmware | SE0 (clocks) | Bad start points |
|---|---|---|
| real | 115, 118, 120 | 0 |
| real | 121 | 2 |
| real | 122 | 5 |
| real | 123 | 7 |
| real | 124 | 10 |
| real | 126 | 22 |
| `got_in` timeout 100 / 110 / 112 / 114 / 116 / 119 | 115 | **21** / 6 / 3 / 0 / 0 / 0 |
| `handshake` timeout 100 / 110 / 112 / 114 / 116 / 119 | 115 | **16** / 6 / 4 / 2 / 0 / 0 |
| `echo` timeout 100 / 110 / 112 / 114 / 116 / 119 | 115 | **3** / 1 / 0 / 0 / 0 / 0 |

- **Threshold.** The real firmware's threshold is 121 clocks, 2.52 us.
  - This agrees with the ISA (`docs/isa.md:549`: `timeout` gives up after N + 1 unsuccessful ticks, so 120 gives 121).
  - So 2.52 us is not a paper figure: 120 clocks never resets and 121 does.
  - It is just above the 2.5 us at which USB lets a device treat SE0 as a reset.
- **Margin.**
  - 115 clocks sits 6 clocks (5 %) under the threshold.
  - A device clock would have to run 5 % fast for the test's SE0 to become a reset. That is far outside the 0.25 % USB allows at full speed, and the 1.5 % at low speed.
  - So the test does not demand that the firmware ignore something it should treat as a reset.
- **Timeout 100.** The stated 21, 16 and 3 reproduce exactly.

Mutants, each in its own `git archive` copy, each edit checked to apply once, run against the six files that load `usb_enum` (`-k "not fix_mutant"`):

| Mutant | Result | Failing tests |
|---|---|---|
| `gotin-100` | killed, 3 failed | `short_se0[1-3]` |
| `hs-100` | killed, 2 failed | `short_se0[0]`, `[4]` |
| `echo-100` | killed, 1 failed | `short_se0[4]` |
| `gotin-40` | killed, 3 failed | `short_se0[1-3]` |
| `hs-40` | killed, 2 failed | `short_se0[0]`, `[4]` |
| `echo-1` | killed, 2 failed | `short_se0[0]`, `[4]` |
| `echo-no-3` | killed, 2 failed | `test_usb_enum_overflow_is_only_its_own_echo`, `reset_anywhere[4]` |
| `jlate0` | killed, 1 failed | `test_usb_enum_answers_on_the_same_clock_as_before` |

`docs/fault-testing.md:331-333` says what the test now catches ("a 2.4 us SE0 … it also catches each timeout lowered to 100 ticks"). That is accurate.

**Claim 3.** See "What I ran".

## Notes

1. **Timeouts close to 120 are still uncaught.** With the 115-clock SE0, a lowered timeout fails the suite only up to:
   - `got_in`: 112 ticks;
   - `handshake`: 114;
   - `echo`: 110. At `echo` the test rests on one start point at 110 and three at 100.

   So `got_in` at 114-119, `handshake` at 116-119 and `echo` at 112-119 would pass. Such a timeout treats an SE0 of about 2.35-2.5 us as a reset. That is a much smaller band than the 90-119 that report 027 found. The 5 % margin above means the test cannot move much closer to the threshold, so I accept it as it stands.

2. **`docs/review.md:102-108`** ("Still open") lists the KV260 USB enumeration against a replayed host waveform and against Linux "on 2026-10-30", without saying it was the 246-word `usb_enum`.
   - It is dated and in the past tense, so it is true as a record. The README carries the label for the same results.
   - My 027 report put review.md down as historical bug notes only and missed this paragraph.
   - A clause would make it consistent; I do not count it.

## What I ran

On runner1 at b2560de: my clone `~/verifier-028/` (from a bundle) and the `remote_test.sh` checkout `agent-runs/protoemu-verify-b2560de-4161752`. Both are now removed.

- `remote_test.sh model test test_sigrok`:
  - `model rc=0`, `2118 passed, 28 skipped in 639.25s` ("SIGROK CHECKS SKIPPED: 28 tests");
  - `test`: `TESTS=27 PASS=27 FAIL=0 SKIP=0`;
  - `test_sigrok`: `TESTS=8 PASS=0 FAIL=0 SKIP=8`.
- In my clone, `SIGROK_CLI=~/protoemu-flow/sigrok/sigrok-cli PYTHONPATH=tools pytest -q -n 16 --ignore=test/model/test_swd.py test/model`: **`2146 passed in 369.21s`**, rc 0.
- The SE0 sweeps and the 8 mutants above.
- The doc grep and reads above.

Nothing touched the Kria or SWD, and nothing was pushed.
