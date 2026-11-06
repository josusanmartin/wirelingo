# Status in detail

This page holds the README's former opening: what is proven at each evidence
level, the failures found in our own work, the raw evidence in the
repository and the state of each line of work. The README now opens with a
shorter version; [summary.md](summary.md) is the two-page one. Nothing here
was removed from the repository, only moved.

## What is proven, and at what level

| Level | What has been shown | Limit |
|---|---|---|
| **Silicon, Tiny Tapeout demo board** | Nothing yet | The chip has not been fabricated |
| **FPGA against a real host** | The chip's RTL on a Kria KV260, wired to the board's own USB port: Linux enumerated and configured it at low and full speed, and a Linux process moved data through it at full speed ([results](../fpga/kv260/README.md#result-against-linux-low-speed-2026-10-30)) | USB only, one host and hub, no scope, 3.3 V FPGA pins. One attach in about 23 stalls in the data demo, cause not proved. Run with `usb_enum` as it was before `fault-fixes`; the current one needs a rerun |
| **FPGA against our own far ends** | Most of the firmware, with results equal to the cycle model: UART across real pins, SPI, I2C (master, sniffer, target), the logic analyzer, CAN, JTAG, 1-Wire, PS/2, WS2812, the one-core USB devices against a replayed host, 10BASE-T TX and RX and `eth10_match` on generator frames, all three post-freeze protocol rounds; quad host port at 193.6 Mbit/s ([fpga/kv260](../fpga/kv260/README.md); the "Verified on" column below says which) | The far end is a model or a replayed waveform, not a real device. Not run there: SWD, the two-core USB device, the USB-UART pairs, `eth10_reply`, `pps_capture`, the bench firmware (MDIO, QSFP-style, PMBus), the RMII study and the blind-test firmware; `eth10_tx_cs` only in the 2026-11-03 fabric ping, pending verification ([evidence](../evidence/fabric-ping-20261103/README.md)). `uart_rx`, `i2c_master`, `can_rx` and `usb_enum` ran there in their form before `fault-fixes` ([docs/fault-testing.md](fault-testing.md)) and need a rerun |
| **Signed-off layout (run fo6)** | Netlist formally equivalent to the RTL; 50 MHz at all three corners; DRC, LVS and antenna clean; Tiny Tapeout precheck passes; all 29 top-level tests at gate level | Timing-annotated gate level covers directed subsets, and analogue behaviour is not simulated |
| **Simulation and formal** | A Python model and the RTL compared cycle by cycle; formal proofs of the FIFOs, host port, core invariants and line unit; injected bugs caught | Proofs cover the listed properties, not the whole chip |

## Failures we found in our own work, and a test we did not prepare for

* [docs/fault-testing.md](fault-testing.md): seeded wire faults (glitches,
  stuck lines, truncation, bit flips, stretch, jitter) against six firmware on
  the cycle model found six real failures in `uart_rx`, `i2c_master`, `can_rx`
  and `usb_enum`, among them a glitch taken for a start bit, an ACK read from a
  stuck SDA and a missed bus reset inside a token. Four are fixed (model and
  RTL simulation only; not yet at gate level or on the FPGA); two are left,
  with the reason.
* [docs/blind-test.md](blind-test.md): a fresh agent given two protocols
  drawn by a rule (HD44780 4-bit and ATA PIO), with the chip and tools frozen.
  HD44780 fits in 99 words; ATA PIO does not fit as specified, and an 8-bit
  subset does. Model and RTL simulation only; the draw's list was written by
  an agent and its fixing in advance cannot be proved.
* [evidence/verifier/](../evidence/verifier/README.md): the independent verifier
  agent's items and reports 000-050, 27 PASS and 24 FAIL, verbatim apart from
  the redactions listed there.

## Raw evidence in the repository

[evidence/](../evidence/README.md) holds the
verifier's reports, the kernel and step logs of the 2026-10-30 real-host USB
runs, and the 2026-11-03 fabric ping (verified,
[verifier report 037](../evidence/verifier/037-fabric-ping-fix3.report.md)). The release
receipts' archive, `wirelingo-release-evidence-7685aba-fo6.tar.gz` (about
55 MB, sha256 `868c5d3ab8010cac27040617a869377076df6067e9fa1f28203ba5cf357409bf`), is not in git: it is to be attached as a release asset
when the repository is published ([docs/release-evidence.md](release-evidence.md)).

## Where it stands

* **Real USB host (2026-10-30):** on the KV260, Linux enumerated and configured
  the autonomous USB firmware at both speeds with no host software on the chip's
  side after loading: 32 of 32 attaches, 66,000 of 66,000 descriptor reads
  (`GET_DESCRIPTOR` control transfers) and 188 of 188 bus resets. (That was the 246-word `usb_enum`; the 253-word one
  from `fault-fixes`, which sees a reset wherever it starts, has not run on the
  FPGA, and these results apply to it only after a rerun there.) With
  `usb_fs_dev` and Python on the SPI port, a Linux
  process then wrote random data through USB and read it back, 350 of 350 round
  trips equal, over 2 MB at 17.6 kB/s, a rate set by the Python loop. (That was
  `usb_fs_dev` before the got_in guard of 2026-11-04; not re-run on the board
  with the 142-word one yet.) Details and
  limits: [fpga/kv260/README.md](../fpga/kv260/README.md); the kernel and step logs
  of the enumeration runs, which hold part of these counts, are in
  [evidence/kv260-usb-20261030/](../evidence/kv260-usb-20261030/README.md).
* **Release candidate: run fo6**, passing the strict release gate with no
  waiver. The certified software is **`7685aba`** (2026-10-14): `main`
  `a1561d6`'s firmware, tools and tests, so the third post-freeze round and
  the `ws2812` and `can_rx` timing fixes are covered. It adds one new,
  supplementary runner (`flow/gl_module_test.py`) and its model test.
  Earlier validation roots `d916d40` and `f1cb7c9` (tags `rc-d916d40-fo6`,
  `rc-f1cb7c9-fo6`) passed too. Receipts cover the firmware and tests of
  their own validation root; a later change to `firmware/`, `tools/`,
  `test/` or `src/` needs a new run, and `main` has moved since (test and
  tool changes, FPGA demos), so the final freeze reruns it.
  50 MHz is met at all three corners, slow included (+0.010 ns of setup
  slack there; the demos run at 48 MHz, with margin); hold is met everywhere
  (fast corner +0.095 ns); DRC, LVS and antenna are clean, with no slew,
  capacitance or fanout violation at any corner. A clean checkout rebuilt on
  a 4-CPU machine reproduces its netlist bit for bit, and its GDS apart from
  the timestamps in the GDS header. Hash-bound receipts: Tiny Tapeout
  precheck, all 29 top-level tests at gate level, and timing-annotated (SDF) gate level at all
  three corners ([docs/release-evidence.md](release-evidence.md)).
  Its netlist is formally proved equivalent to the RTL
  ([docs/equivalence.md](equivalence.md)). The
  last exception of the previous candidate, t48ba (antenna diodes taking a
  net to 11 loads against the IHP cells' fanout limit of 8), is removed by a
  flow step that buffers such nets after routing ([docs/physical.md](physical.md)).
* Running on a Kria KV260 FPGA at 50 MHz: firmware loads and runs, UART
  crosses real pins in both directions, and the quad host port moves
  193.6 Mbit/s, matching simulation ([fpga/kv260](../fpga/kv260/README.md)).
  That is against the FPGA's own fabric SPI master on the chip's clock; the
  demo board's MicroPython driver is far slower, and the port's speed through
  the Tiny Tapeout pads is unmeasured ([docs/info.md](info.md#how-to-test)).
  At 48 MHz the autonomous USB device enumerates, at low and full speed,
  against a host model's waveform replayed on its pads by the FPGA's pattern
  generator, with every device edge at the clock the cycle model gives when
  the model samples the pads the way the FPGA drives them. (That was the
  2026-10-14 replay with the 246-word `usb_enum` from before `fault-fixes`;
  the 253-word one needs a KV260 rerun before it applies.)
* A **full-speed USB (12 Mbit/s) device runs on one 48 MHz core**, with
  handshakes 10.5 clocks after the host's packet (spec: 8–26; g4: 11.5), in RTL and
  gate-level simulation of the signed-off netlist
  ([docs/usb-full-speed.md](usb-full-speed.md)); the chip meets 50 MHz,
  so 48 MHz, at every corner. With no host software after loading, one core
  also enumerates as a USB device on its own, low or full speed
  ([docs/usb-enumeration.md](usb-enumeration.md)).
* Measured in simulation against the RP2040 PIO's reference programs,
  including where PIO is better ([docs/pio-comparison.md](pio-comparison.md)).
* **Post-freeze protocol challenge:** with the RTL frozen (tag
  `rc-93c2137-t48ba`; the chip's Verilog `src/*.v` unchanged since, only the
  hardening config `src/config.json` changed for fo6), three rounds of
  protocols were added in firmware, plus one assembler fix (`/` became
  integer division, which bidirectional DShot needed). Round one: DMX512, LIN
  (commander and a self-synchronising responder), NEC infrared (38 kHz carrier and receiver),
  I2S, Wiegand, DShot600 (bidirectional too). Round two: DALI, Modbus RTU,
  MIDI (receive and transmit), a quadrature encoder counter. Round three:
  HDMI-CEC (multi-master arbitration), ISO 7816-3 T=0 smart card, SMBus with
  PEC, RC-5 infrared. Each is checked by the static timing checker, pins-only
  models, sigrok's decoders where one exists and an RTL test through the SPI
  port, which also passes on fo6's gate-level netlist (functional 16/16;
  with SDF at 20 ns 16/16 at the typical corner, two of them after their
  timing assertions got a 1 ns tolerance for sub-ns pad skew; 16/16 at the
  slow and fast corners in supplementary single-case runs of 2026-11-02,
  outside the release gate and with no verifier report). All three rounds also ran on the KV260 FPGA build of the RTL,
  against the other core or the FPGA's pattern generator playing our own
  (mostly pre-timed) device models, with results equal to the cycle model's
  ([docs/post-freeze-protocols.md](post-freeze-protocols.md), with the
  25 ISA limitations they ran into).
* **v2** (first signed off as run v2c; fo6 has the same RTL): four small
  additions over g4, each in the spec, RTL, model, assembler, lockstep, formal
  where an invariant applies, and a firmware that uses it. The line unit's NRZI sample
  point moves half a clock earlier (full-speed USB in RTL simulation: 24/24
  packets at 28 ns edge-to-edge distortion, g4 11/24; v2c met 48 MHz only at
  the typical corner, fo6 meets 50 MHz at all three); `SYNC_BYPASS` reads
  chosen pad inputs through one synchroniser flop (SPI master at 4 clocks
  per SCK, like PIO, if the pads and
  the device answer within 2 clocks); WAIT kind 7 waits until any of 8 pins
  differs from a register (logic analyzer timestamps to the tick instead of
  5 ticks); CALL/RET with one link register (`i2c_device` 98 → 88
  instructions). docs/isa.md has the details.
