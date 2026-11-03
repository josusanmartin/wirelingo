VERDICT: PASS

Commit 5582d7a, base 61af9b2. Every claim holds, and every figure I checked reproduces, including with sigrok on. The notes below are one limit the doc understates and a few things worth knowing, none of which makes a claim false.

| Claim | Result |
|---|---|
| 1. `mdio_master` 32 words, MDC exactly 400 ns; PHY delay 0-340 ns reads, 360 ns fails | PASS: boundary also confirmed on RTL |
| 2. `mdio_phy` 124 words, all 32 registers, no contention over 200 frames; needs 40 ns hold and MDC >= 360 ns | PASS: "no contention" is a real check, scoped to whole frames |
| 3. `pe.qsfpdev` a stand-in; roll-over, page revert, write abort per SFF-8636; hold = latency + 23 clocks | PASS: checked against the spec text, Rev 2.12 |
| 4. PMBus: 24 single-bit flips rejected, 65536 LINEAR11 words | PASS |
| 5. 176 model and 5 RTL tests with sigrok; 28/28 mutants | PASS: 0 skips |
| 6. Secondary sources and memory labelled; no FPGA or device evidence claimed | PASS |
| 7. CI edit sound, no `flow/` change | PASS |

## Claim 2: is "no contention" a real check?

Yes, at clock resolution, for frames that complete.

- **It sees the real outputs.** `Rig._observe` reads the chip's actual output-enable bits every clock, and `Wire.resolve` counts any two drivers, whatever their values. It is not derived from the firmware's intent.
- **It can fire.** The no-release mutants trip it or the who-drives tests. `test_broken_master_keeps_driving_through_turnaround` shows the count rise, and `Rig.run` asserts 0 after every model run. My `phy: drives TA2 high` mutant was caught too.
- **The margin is wide.** The master releases 200 ns before TA1 and the stand-in drives 80 ns after it.
- **Limit 1, resolution.** It has clock resolution on one chip clock. Sub-clock overlap and pad delay are not visible, and the doc says pad behaviour is not examined.
- **Limit 2, aborted frames.** See note 1.

## Notes

1. `docs/bench-firmware.md:153-155` understates an abort. It says MDIO "stays driven" if an STA stops mid-read, from the code and not tested. I tested it on the model by restarting the master at four points during a read, then sending a new read:

   | Abort after | Contention | Next read |
   |---|---|---|
   | 2 us (preamble) | 0 | correct |
   | 19 us (data) | 334 | lost: no ack, 0xFFFF |
   | 20 us | 274 | lost |
   | 25 us | 34 | lost |

   So an aborted read causes real two-driver contention against the next frame's preamble, and that next read is lost. The stand-in then has too few ones left to see a preamble. I did not test the read after that. Add this to the limit.

2. `docs/bench-firmware.md:99`: "needs 40 ns" has no margin with an asynchronous MDC. The stand-in reads 1 to 2 clocks after the edge, so a 40 ns hold can end exactly at the worst-case sample. This is arithmetic from the doc's own figures, because the model and RTL tests run MDC on the chip's clock grid. Worth saying "more than 40 ns" for a real STA.

3. CI runs no sigrok: `.github/workflows/test.yaml` does not install it, and `HAVE_SIGROK` then quietly drops those checks inside passing tests. So a CI pass on `test_bench_fw` does not show the sigrok checks ran. The doc says they need `sigrok-cli`. This is not new to this item. The workflow step itself is sound: it matches its neighbours, and `check_results.py` requires all 5 tests to pass.

4. `tools/pe/qsfpdev.py`, comment on `REV_COMPLIANCE`: "Rev 2.8 or later". In Rev 2.12, code 08h means Rev 2.8, 2.9 and 2.10, and a later code exists. Correct for a Rev 2.9 stand-in, but the comment overstates it.

## What I checked beyond the tests

- **SFF-8636 (claim 3):** I fetched the published spec, which is Rev 2.12, not the Rev 2.9 the doc cites, and read the passages. All of these match:
  - Roll-over "to the first byte of the same page".
  - A non-supported page "shall not be accepted", reverting to 0h and operations on Upper Page 00h.
  - A repeated START in place of STOP aborts and discards the write.
  - Up to four sequential bytes, and a sequence not ended by STOP is aborted.
  - T_clock_hold 500 us.
  - 2-byte reads must not give partially updated data.
  - IntL default 1, and address 1010000.
  - The stated limits are real ones (tWR, no 0 for "unspecified").
- **RTL read boundary (claim 1):** the RTL test only covers 20 and 300 ns at the pad, so I ran 320, 340 and 360. 320 and 340 read correctly. 360 fails with no ack and wrong data, exactly the model's boundary.
- **PMBus arithmetic (claim 4):** I worked the doc's examples by hand. 0xE32A = 810/16 = 50.625, 0xDB12 = 786/32 = 24.5625, 0xFFFF = -0.5, and the extremes are -1024 x 2^15 and 1023 x 2^15.
- **My own 18 mutants (on top of the 28):** 17 caught across the MDIO firmwares, `qsfpdev` and `pmbus`. The survivor, `CC_EXT` summing one byte fewer, is equivalent here, because byte 222 is always 0 in this image.
- **Word counts:** assembled, 32 and 124.
- **Scope:** the diff touches no `src/` or `flow/` file, and none of `tools/pe/{sim,isa,asm,line,timing}.py`, `firmware/i2c_device.pasm` or `firmware/smbus_host.pasm`, as the doc says. `tools/pe/sigrok.py` gained an MDIO helper.
- **Claim 6:** the doc names what was not read (802.3, PMBus, SFF-8024) and marks memory-sourced statements, including "32 contiguous ones". The evidence line says model and RTL only.
- **Language:** no first or only claims. The hits are ordinary words.

## What I ran

All on runner1. Throwaway copies were removed afterwards.

- `remote_test.sh model test_bench_fw`: `1566 passed, 28 skipped in 547.30s`, `test_bench_fw rc=0`, `TESTS=5 PASS=5 FAIL=0 SKIP=0`.
- With `SIGROK_CLI` set: the three model files `176 passed in 57.42s`, 0 skip lines with `-rs`. RTL `TESTS=5 PASS=5 FAIL=0 SKIP=0`, with sigrok `mdio` and `i2c` output in the log.
- `python3 test/mutants.py` with all 28 names: `28 of 28 mutants killed`, rc 0.
- My extra mutants, the abort probe and the RTL delay run. My first delay run failed on a quoting error in my own script; the rerun gave the result above.
