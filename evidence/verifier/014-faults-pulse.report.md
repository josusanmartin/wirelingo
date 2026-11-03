VERDICT: PASS

Commit 75eed67, base 61af9b2. The pulse capture is exact as claimed, the tests can fail, and the fault sweep can see a stuck core. The six findings are honest. Finding 6 is worse than the doc says, and the doc should say so (note 1).

| Claim | Result |
|---|---|
| 1. Exact except blind ticks and the record path, flagged; bounds from `pe.timing` | PASS: 10 of 10 firmware mutants caught |
| 2. A slow host loses edges, never time; FIFO 4 edges; tapeout depth | PASS |
| 3. 65.2 days; one RTL second gave 50,000,003 clocks | PASS: rerun, same result |
| 4. 576 cases, 0 hangs, 7 not ok; sweep can detect a hang; "ok" not vacuous | PASS, note 2 |
| 5. Six findings judged | Judged below |
| 6. Limits stated | PASS |

## Claim 5: the six findings

| # | Finding | My judgement |
|---|---|---|
| 1 | `uart_rx` takes a glitch for a start bit | **Real firmware weakness.** Receivers commonly recheck the start bit at mid-bit; this one doesn't. The doc gives the reason it isn't a one-line fix |
| 2 | `i2c_master`, stuck SDA reads as ACK | **Real weakness, partly inherent to I2C.** A stuck-low SDA looks like an ACK on any master. A master that reads back the 1s it drives would see it. This one doesn't |
| 3 | Next transfer lost after a truncated read | **Expected protocol behaviour.** The target is left mid-byte, the master reports NACK, and the transfer after that works. That is the usual result without a bus-clear sequence |
| 4 | `can_rx` has no form check | **Real gap against the CAN specification**, within the README's "frames, not a full controller". It is a spec item, not a bug |
| 5 | `can_rx` raises an error flag on an idle glitch | **Real weakness.** As I recall, controllers reject a too-short SOF by sampling again at the sample point, which this one does not |
| 6 | `usb_enum` misses a reset that starts in an EOP | **Real firmware path, reached only by a fault.** Larger than stated: note 1 |

None is a harness fault. I checked each against the fault and the oracle code, not just the doc.

## Notes

1. `docs/fault-testing.md:159-166`: finding 6 was tested with a 4.2 us SE0. I ran a full USB reset, 10 ms (480,000 clocks):

   | SE0 of 10 ms starting at | Reset seen | Address after |
   |---|---|---|
   | bit 24, bit 36 | yes | cleared |
   | bits 30, 32, 35 | **no** | **kept** |

   So no reset length gets out of `got_in: wait 1 pin DM`. That wait has no timeout and no SE0 check. The doc's "the next transfer works" holds for a transfer to the old address.

   The step from there is my inference, not run: a host that resets and then enumerates at address 0 gets no answer from this device. The fault is still needed to get there, since a host does not start a reset inside a token. But the consequence is a device the host can no longer enumerate, not a lost transfer. Say so.

2. Claim 4, what "0 hangs" means: the `hang` verdict comes only from the I2C master's bound or from a restart that doesn't clear. A core wedged in a WAIT is reported as `needs-reset` instead. So "0 needs-reset" is the figure that shows nothing stuck. The doc does report both.

   To check that the sweep sees a stuck core at all, I planted wedges:

   | Firmware | Results |
   |---|---|
   | `uart_rx` | ok 85, needs-reset 10, false-good 1 |
   | `can_rx` | ok 66, needs-reset 30 |
   | `usb_enum` | ok 94, needs-reset 2 |

   "ok" is not vacuous:
   - the Changed column counts cases where the fault altered the output;
   - firmware mutants give `false-good`;
   - `test_doc_table_is_the_sweep` ties the table to the run.

   Two cells prove little and the doc says why: I2C reads have no expected value, and `can_rx` and `eth10_match` cells with Changed 0.

3. Claim 2: `ModelHost`'s default FIFO is 8, the tapeout's depth. The firmware holds 4 two-word records and refuses at level > 6, and the RTL testbench runs the tapeout's 8. Edges are dropped and counted, and the interval stays right.

4. Claim 3: 2^48 x 20 ns = 5,629,499 s = 65.2 days. 50,000,003 is by design: the test drives one second plus 3 clocks and asserts the exact count. That is a stronger test than one that only asks for a value near 50,000,000.

## What I ran

On runner1 at 75eed67, in `verifier-014/`, since removed.

- `remote_test.sh model test_pps`: `1537 passed, 23 skipped in 334.38s`, `test_pps rc=0`, `TESTS=5 PASS=4 FAIL=0 SKIP=1` (the long test is skipped there).
- `PPS_LONG=1`, `pps_one_second` alone: `period 50000003.000 ticks = 1000000060.0 ns, drift +0.060 ppm`, `TESTS=1 PASS=1 FAIL=0`, 4839.75 s, rc 0.
- 10 `pps_capture` mutants, each caught:
  - record path, both rollover constants, the borrow, the double borrow;
  - the FIFO check, the drop path, uncounted drops;
  - the OSR top bit, the level not tracked.
- The wedge mutants in note 2, and the USB reset sweep in note 1, in separate copies.
