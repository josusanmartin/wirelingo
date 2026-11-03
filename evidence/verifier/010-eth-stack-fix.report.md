VERDICT: FAIL

Commit d2d89b8, base e71ab1c. All 8 findings from 003 are addressed, and everything I reran reproduces, including gate level at this commit. It fails on the one thing the item asked me to clear for the board: `--measure-spim` can leave the host port on the fabric master with QUAD_EN set. That only needs a small change. The dribble fix passed every attack.

| Claim | Result |
|---|---|
| 1. Budgets at 1518 bytes; go/no-go 15.5 us; dribble fix | PASS, 50 MHz RX row note (finding 2) |
| 2. 18 mutants, 18/18 killed | PASS |
| 3. L to H; 15,497 transients settle on H | PASS |
| 4. `--measure-spim` restores the port | **FAIL** (finding 1) |
| 5. 3930/3950 ns, 9800/9825 ns, 9810 ns; 95-bit mutant; RTL reaches core deferral | PASS |
| 6. Model 1506, RTL 7/7, gate level 3/3; TAP "same code" | PASS, same code confirmed |

## Findings

### Blocking

1. `fpga/kv260/host/eth_tap.py:238-252`: `measure_spim_session` can leave the port in a bad state, in two ways. Both shown with a fake register file and no hardware.

   - **The restore is skipped when cleanup itself fails.** The `finally` runs `enable_quad(False)`, then `wait_idle()`, then the `SEL` write. If the master is stuck busy, the first line raises and `SEL` is never written.

     | Master stuck from | Exception | Final `SEL` |
     |---|---|---|
     | never | none | 0, restored |
     | first GO, setting QUAD_EN | `pe_spim stuck busy` | **1, left on the fabric master** |
     | second GO | same | **1** |
     | third GO | same | **1** |

   - **A signal skips the `finally`.**

     | Signal | `SEL` writes seen |
     |---|---|
     | SIGINT | 1, then 0: restored |
     | SIGTERM | 1 only: **left set** |
     | SIGHUP | 1 only: **left set** |

     SIGHUP is what a dropped ssh session sends, and SIGTERM is what `timeout` sends. QUAD_EN stays set in those cases too.

   - The test uses a fake that never sticks, and it only covers the measurement loop raising, so neither path is reached.
   - Fix: nest the restore so `SEL` is written even if the QUAD_EN write fails, and turn SIGTERM and SIGHUP into an exception for the session's length. Add a stuck-master case to `test_measure_spim_gives_the_port_back`.

   What is right: `SEL` is readable (`fpga/kv260/pe_spim.v:110`), so restoring it is sound, including the alternate-clock bit. QUAD_EN really is write-only (`docs/isa.md:736`), so 0 is the honest choice. The 1-bit clear goes out through the fabric master before the select moves back, and 1-bit commands are accepted with QUAD_EN set (`src/pe_hostport.sv:58`). I did not run any of this on hardware.

### Should fix

2. `docs/eth10-network.md:141`: the 50 MHz RX figure of 16.5 us is for one frame. Back-to-back frames at the minimum gap need 16.0 us. Longest software time per pass with every frame arriving, 4 phases, 0.5 us steps:

   | Traffic at the minimum gap | 50 MHz | 40 MHz |
   |---|---|---|
   | 1518 bytes, 2, 3 or 6 frames | **16.0** | 15.5 |
   | 1000 bytes, 6 frames | **16.0** | 15.5 |
   | 600 or 300 bytes, 6 frames | 16.5 | 15.5 |
   | 64 bytes, 12 frames | **16.0** | 15.5 |

   At 16.5 us, the second of two 1518-byte frames is lost with `rx_overflows` in all 6 phases I tried. **The 15.5 us go/no-go at `:147` and `:180` holds in every case on both clocks**, so the decision is sound, which is why this is not blocking. The table row should say 16.0 for sustained traffic or be labelled as a single frame.

3. `test/mutants.py:233`: a mistyped name now runs everything. `fnmatch.filter` returns nothing for a name that matches no mutant, and `names or MUTANTS` then treats that as "all", so a typo runs all 52 and reports on them. Before this change, an unknown name was an error. Fix: fail when a pattern matches nothing. Found by reading the code; I did not run it, since that would run all 52.

## Claim 1: can the dribble fix drop a real bit?

No, not in a way that matters. Frames are whole bytes, so what it drops is never frame data unless alignment is already lost, and then the FCS rejects the frame.

- **Bit-level fuzz:** 300 frames cut or extended by 1 to 40 bits, with zero, one and random tails: 0 wrong frames passed the FCS.
- **Could the extra bit land in the next frame's record?** That was my main worry: a leading bit would break preamble detection and lose a good frame. It never happened in 208 frame pairs at the minimum gap with a host at budget. The bit is always in its own frame's record, and the count is never above 1. The only frames lost were the 16.5 us overflows in finding 2.
- **TP_IDL 200 to 500 ns:** 72 runs, 0 lost.
- **Jitter:** +-13.5 ns at 50 MHz and +-12 ns at 40 MHz, 16 runs, 0 lost.
- **Budget phases:** the test uses 2 phases a few clocks apart and the sweep used 4, while a host's pass is about 800 clocks. So I ran all 6 cases at phases 0 to 999 in 37-clock steps, at 1518 and 1200 bytes: 336 runs, 0 failures. The budgets are not phase-dependent.
- **Tightness:** `test_service_budget` asserts both sides, and the figures match my own search from 003.

## 003's findings

| # | Status | Evidence |
|---|---|---|
| 1 Budget on 300-byte frames | Resolved | Asserted at 1518 bytes, both sides. See finding 2 here for the sustained case |
| 2 15/15 not reproducible | Resolved | 18 mutants in `test/mutants.py`, `'eth-*'` gives 18 of 18 |
| 3 Flip direction | Resolved | Doc and test say L to H, and `check_flips` asserts it. 1203 + 13164 + 1130 = 15,497 in the logs and in my own gate-level run |
| 4 `--measure-spim` | **Partly** | Restores on a normal exit, an exception in the loop, and Ctrl-C. See finding 1 |
| 5 3910 ns | Resolved | 3930/3950 exact, matching my 10 ns sweep |
| 6 96 bit times not pinned | Resolved | 9800 and 9825 exact. RTL and gate level 9810. A 95-bit window gives 9710 ns on RTL and fails |
| 7 TAP logs not cited | Resolved | The doc cites the path. 5 of 5, 2 of 2, UDP, all no-reply checks and `pass: true` at both clocks. Wall and simulated round-trip times match the doc |
| 8 Two untested IP checks | Resolved | `ip_source_*` and `ip_length_past_the_frame` added, each with a mutant |

## Other checks

- **RTL reaches core deferral (claim 5):** removing the window from the firmware fails `test_carrier_sense_rtl`, and so does the 95-bit window.
- **Same code (claim 6):** `git diff 7503c14 d2d89b8 -- tools firmware fpga src` is empty, so the TAP run's code is this commit's. `aed4c8f` to `d2d89b8` is docs only.
- **Go/no-go arithmetic:** a STATUS read plus an 8-word read is about 138 clocks, or 2.8 us at 50 MHz, so "about 18 us" follows. It is labelled an estimate.
- **Doc citation:** I checked `docs/eth10-network.md`'s use of my 003 report, and it matches what that report says, including "not a measured overlap".

## What I ran

All on runner1 at d2d89b8. Throwaway copies were removed afterwards, and scratch stayed in `verifier-010/`, per the new rule.

- `remote_test.sh model test_eth_stack_rtl`: `model rc=0`, `1506 passed, 23 skipped in 1107.55s`, `test_eth_stack_rtl rc=0`, `TESTS=7 PASS=7 FAIL=0 SKIP=0`.
- `flow/gl_module_test.py` on fo6 (read only) at d2d89b8: `gl_module_test: 3/3 passed`.
- `python3 test/mutants.py 'eth-*'`: `18 of 18 mutants killed`.
- Probes as above, including 1064 runs for finding 2.
- `measure_spim_session` failure paths locally, with a fake register file. No Kria, no TAP, no sudo.
- My first RTL 95-bit mutant did not apply because of a shell escape in my script. The rerun, confirmed by `git diff --stat`, is the result above.
