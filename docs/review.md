# External review: findings and fixes

In October 2026 a second agent (Codex) reviewed the design, reproduced several
bugs, and fixed them. This page records what it found, what changed, and which
tests now cover each fix. Finding codes are the review's.

## Bugs found and fixed

| Code | Finding | Fix | Covered by |
|---|---|---|---|
| R1 | The advertised physical signoff belonged to older RTL. | Release claims now identify the physical run and software validation root; the release checker binds GL, SDF and precheck receipts to their inputs. Later firmware results remain separate. | [Recorded fo6 release and receipt contract](release-evidence.md) |
| R3 | Global reset during an active SPI transaction left the host port's SCK-side parser half-way through a command. | After reset the clk side ignores host-port events until it has seen CS high for three clocks (`host_ready`, pe_hostport.sv), so the interrupted command has no effect; the SCK side resets at that CS rise as before. (The review's version also held the SCK-side parser and the quad drivers in reset and dropped writes outside an active write transaction. Its netlists all placed with about 5 % more wire and congested routing; this smaller fix doesn't.) | `test.py` reset tests (stopped SCK, read-ahead abort, every core afterwards); `formal/hostport.sby` |
| R2 | The SDC cut every SCK↔clk path (`set_clock_groups`), so the held buses between the domains were never timed. | `set_max_delay -ignore_clock_latency` bounds both directions to one clock period; only cross-clock hold is cut. Synchronizer flops carry `async_reg`. | STA at every corner (`src/timing_checks.tcl` adds a CDC report) |
| R4 | USB firmware accepted a DATA packet with a corrupt PID. | Both variants compare the full SYNC+PID byte, complement bits included; SETUP data must be DATA0; bad packets are not ACKed. | `test_usb_model.py`, concurrent USB service on the RTL (`test.py`) |
| R5 | A USB bus reset didn't restore the default address. | Sustained SE0 raises `F_RESET`; the device software stops the cores, clears state, restores address 0 and restarts on J. | `test_usb_enum.py` (reset at address 5, with transfers pending), RTL service test |
| R6 | The MicroPython driver's pin-owner helper overwrote earlier assignments (PIN_OWNER is write-only). | The driver keeps a shadow of the owner map. | `test_micropython_driver.py`, `test_host_validation.py` |
| R7 | Loaders accepted programs that overran their bank or jumped out of it, which faults at run time with fixed banks. | Loaders, exporters and the MicroPython bundle check bank bounds, bases, wrap ranges and jump targets before writing anything. | `test_host_validation.py` |
| V1 | `flow/gl_test.sh` could report an old results file after a failed build. | Each GL run uses fresh private output directories and fails if any suite fails or produces no results. | `test_flow_tools.py` (compile, startup, missing-XML and per-suite failures) |
| C1 | `src/config.json` and `flow/gen_config.py` had drifted apart. | `gen_config.py --check` fails if the committed config isn't exactly the generator's; CI runs it. | `test_flow_tools.py`, GDS workflow |
| C2 | Verification descriptions and implementation comments had drifted. | The docs describe direct internal comparison of write-only configuration registers, distinguish mutation definitions from recorded kills, and identify model-only results. The FIFO comment now matches the multiclock proof. | [Verification scope](verification.md), [reviewer guide](guide.md), `src/pe_fifo.sv` |
| C3 | Two push workflows ran the formal suite. | Formal jobs are consolidated in `test.yaml`; the duplicate `formal.yaml` was removed. | [Test workflow](../.github/workflows/test.yaml); this is orchestration cleanup, not new proof coverage. |
| V4 | A failed mutation command could be counted as a killed mutant; board bring-up did not check the generated clock. | The mutation runner requires a passing baseline and fresh executed-test results, and separates infrastructure errors. Bring-up checks the SDK-reported clock before releasing reset. | `test_flow_tools.py::test_mutation_infrastructure_is_not_a_kill`; `test_host_validation.py::test_bringup_checks_generated_clock_before_releasing_reset` (mocked SDK, not a board measurement) |

## Follow-up fixes and verification — October 14, 2026

The later review found two USB ACK parsing bugs and a timing-observer bug.
These fixes use the existing ISA and tools; they do not change RTL.

| Finding | Fix | Regression and scope |
|---|---|---|
| An ACK followed by extra data could advance the autonomous transfer or publish the FIFO firmware's completion flag. | Check the handshake tail before publishing completion. Accept the supported final-level stretch, reject extra bytes, and clear discarded ISR bits before they can enter RX records. | [ACK regression](../test/model/test_usb_ack.py): low/full-speed autonomous and FIFO variants, next DATA packet and toggle checked externally. |
| After an invalid short tail had already ended, draining again could consume the first retry token. | Return to idle when the end has already been consumed; drain only a still-open invalid packet. | The same regression checks the first retry explicitly. Saved autonomous gaps are 64 clocks at low speed and 9 at full speed (2 and 2.25 bit times); this is finite model evidence, not every phase or minimum-spacing throughput. |
| The timing tracer omitted an instruction still pending at the end of observation, losing its entry and stall evidence. | Retain the final pending instance, with completion unset. | [Tracer regressions](../test/model/test_timing_tracer.py) distinguish entry from completion and reject invented completion. Finite observation does not prove an instruction can never finish. |

The ACK corpus has 125 passing cases using the two-core, two-bank,
256-word, depth-eight model profile. The two maintained MicroPython USB
driver tests separately check the corresponding generated images in that
profile. See [ACK behavior and remaining limits](usb-full-speed.md) and
[enumeration](usb-enumeration.md). These results do not qualify the updated
ACK paths on native RTL, a board or silicon.

The UART follow-up added baud/phase, framing, rearm and packed-capture tests;
it did not require rewriting the standalone receiver. Its later
[serialized-host RTL test](uart-capture-host.md) captures 32 bytes with a bad
final STOP, then three good bytes after paid drain/rearm. That finite test
uses core 0 with core 1 disabled and behavioral SRAM. The separate
[UART IN1 and partial-PUSH checks](lean-verification.md) combine manual Lean
lemmas with finite RTL comparisons; they are not whole-core equivalence.

Combined software/model regression passed **1,194 tests across 52 modules**
at `85991cddf99d8bffa0bc8208ded123e263e23e16`, with no failures or skips.
The later [complete UART-to-USB service test](usb-uart-overlap4.md) passed
separately on the same firmware/runtime inputs: 32 UART bytes, eight complete
USB reads and one lost-ACK retry. Its test and measured documentation are
`6052028` and `2d08f141`. A combined 1,195-test run is not claimed.
Physical receipts retain their own named validation roots; neither model
result extends them to later firmware or grants release approval.

## Verification added

* **USB:** concurrent device service over the real SPI port on the RTL for both
  firmware variants: descriptors in chunks, data toggles, lost ACK, a new
  SETUP mid-transfer, invalid PIDs and bus reset.
* **10BASE-T RX:** 64-, 128- and 1518-byte frames with FCS through the RTL
  while quad SPI drains the FIFO, with jitter, 96-bit gaps, service stalls,
  and a forced overflow followed by a clean frame.
* **Host link:** SCK duty cycles from 25 to 75 % at SCK = clk / 2.
* **CI** runs the formal proofs and the mutation check with the simulation
  suites, and checks the generated configuration and MicroPython bundle.

## Host software added

* `tools/micropython/quad_spi.py`, `pio_quad.py`: an optional RP2350 PIO/DMA
  transport for the quad host port. Tested against the RTL with the compiled
  PIO program driving the chip's SPI pins (`test_pio.py`); not yet measured on
  a board ([board-transport.md](board-transport.md)).
* `tools/micropython/usb_device.py`, `usb_bits.py`: the USB device software
  (endpoint 0) in portable form for the board.
* `tools/micropython/eth10_rx.py`: bounded 10BASE-T frame capture on the board
  ([eth10-receiver.md](eth10-receiver.md)); tested on the RTL
  (`test_eth_service.py`), with a proposed receive circuit that hasn't been
  built.

## Review recommendations not adopted

* The original hand-assembled release evidence file was not adopted.
  `flow/signoff.py` remains the minimum physical check. A separate
  [release check](release-evidence.md) now uses receipts generated automatically
  by the verification runners, including setup at the claimed clock and
  explicit electrical limits. It preserves the distinction between minimum
  acceptance and the stronger release claim.
* Real latch arcs during placement and routing. The FIFO latches receive data
  while open, and STA's time borrowing through them made each repair step
  take hours. The PnR SDC (`src/protoemu_pnr.sdc`) times them like flops; the
  same repair edits resulted in 4 instead of 79 minutes after CTS and 8
  instead of 114 after global routing. Signoff always times the real latch
  arcs.

## Still open

* More on hardware. The RTL runs on a Kria KV260 at 50 MHz (bring-up, the v2
  features, quad SPI at 193.6 Mbit/s, UART across real PMOD pins:
  [fpga/kv260](../fpga/kv260/README.md)); still open are the RP2350 transport,
  USB enumeration against a real host (prepared on the KV260, not yet
  wired), and the 10BASE-T analog front end.
* A formal equivalence check of the RTL against the synthesized netlist. LVS
  compares layout with netlist; gate-level simulation covers only the cases
  the tests exercise.
* V2's stronger host-port proof remains open: address/data integrity and
  ordering across abort/reset boundaries are checked by finite RTL tests,
  not by the [host-port formal harness](../formal/hostport_props.sv). That
  harness proves operation/count safety and uses initial reset only.
  `test_host_reset_aborts_selected_transaction` covers reasserted reset in
  the selected parser phases; it is not a proof over all reset sequences.
* Closed 2026-10-13: the last electrical exception (a fanout count of 11
  against 8, of which 4 were antenna diodes) was removed, not waived. Run fo6
  passes `flow/release_check.py` with no waiver.
