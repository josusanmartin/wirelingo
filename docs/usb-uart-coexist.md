# Autonomous USB plus declared UART peer capture

## Summary

**What it does.** Core 1 captures a declared-length UART message (1 to 32
bytes) while core 0 runs autonomous low-speed USB enumeration (`usb_enum`).
The two run side by side; UART bytes are not forwarded over USB. Core 1 can
be rearmed between captures without stopping or disturbing the USB core.

**How to use it.**

* Load both images (`usb_enum` on core 0, `uart_rx_capture_peer` on core 1)
  with both cores disabled, using the normal tapeout loaders.
* Arm or rearm core 1 with the five writes from
  `pe.uartcapture_peer.capture_plan(N, enabled_before=current_mask)`; after
  HALT, decode the RX words with the module's `decode_capture`.
* Pins: USB on pads 0/1; UART RX on logical pin 16 (`ui[0]`), input only.
  Flags: F0..F5 USB, F6 the capture's sticky bad-STOP flag.

**Envelope.** 48 MHz system clock, low-speed USB, UART 115200 baud 8N1 with
the unchanged eight-tick divider, N from 1 to 32. The two tested cases use
nominal baud only. The peer image matches the core-0 capture's UART timing
except for two flag immediates; the ±3 % cases in
[uart-rx-timing.md](uart-rx-timing.md) ran on the core-0 image, not on this
pair.

**Evidence.**

* Cycle model: two fixed pin-level cases in
  `test/model/test_usb_uart_coexist.py`: enumeration during a 32-byte
  capture, and a USB STALL, a UART bad STOP, a clear and a rearm without
  either core disturbing the other. On `main` (`d61f548`) the file passes,
  3 tests including both cases, in the model suite.
* FPGA: `uart_rx_capture_peer` ran on the KV260 build live beside a
  capturing core 0 and was rearmed while core 0 ran
  ([uart-capture-host.md](uart-capture-host.md#on-the-fpga-build-2026-10-14)).
  It has not run there beside USB.
* Not run: RTL or gate-level simulation of this pair, a board, full-speed
  USB, electrical USB or a real SPI loading host.

## Allocation

This optional program gives core 1, bank 1 and F6 to a known-length UART
capture beside autonomous `usb_enum` on core 0, bank 0 and F0..F5. The
existing core-0/F0 UART API is unchanged. This allocation does not apply to
FIFO-based USB, which uses all eight flags.

At 48 MHz USB uses the low-speed line unit on logical pads 0/1; UART
receives 115200 baud, 8N1, on logical RX 16 (ui0), with the unchanged
eight-tick divider. The peer image uses 19 words / 76 B; the selected USB
image uses 253 words / 1012 B (246 / 984 B before `fault-fixes`). Together the 1088 B occupy both engines and
banks (3 and 237 words spare), one line unit, flags 0..6 and three physical
inputs (pads 0/1/16). UART uses no virtual pins, mailbox, shared data
registers or line unit. F7 is unallocated here; no spare engine is
demonstrated. RX 16 remains input only.

## Arm, rearm and decode

`pe.uartcapture_peer.capture_plan(N, enabled_before=current_mask)` supports
only core 1 and integer N from 1 to 32. Before each arm or rearm, ordinary
reads must establish that core 1 is stopped or disabled and that its RX and
TX FIFOs are empty. The plan's five writes disable only core 1, set R1 = N
and R2 = N mod 4, clear only F6 with FCLR 64, and restart and enable only
core 1, preserving core 0's current enable. A running USB core must not be
disabled or restarted, or have its flags or FIFO cleared. Use the unchanged
strict `decode_capture` exported by this module after observing HALT, no
fault, the expected RX occupancy and F6 through ordinary host reads.
Complete four-byte words are little endian; a final 1..3-byte word is high
aligned. There is no implicit drain or flush and no inference of packets
from idle time.

A bad STOP keeps the suspect bytes and sets the sticky F6 before HALT.
Completion is after the final synchronized STOP sample, not necessarily the
physical end of STOP. Earlier complete words can be published before STOP
checking. The application must know N and arrange the start; N = 32 uses all
eight RX FIFO words without metadata. This finite mode is not an indefinite
stream or a FIFO-full guarantee.

## The two model cases

Run `PYTHONPATH=tools python3 -m pytest -q test/model/test_usb_uart_coexist.py`.
The model is explicitly two cores, two banks, 256 words and depth 8; inputs
use normal two-clock synchronization. The external USB controller is a pin
peer, distinct from the loading host. Model-host bus service is paid in model
clocks and does not establish serialized SPI-host latency. Set
`USB_UART_TRACE_DIR` to save each case's raw record.

Both cases are declared before the run:

* **Case 1.** A 32-byte UART burst starts at clock 4096 and receives no
  loading-host service until 150000, while the selected first-attempt
  low-speed USB enumeration reaches address 5 and the configured state.
* **Case 2.** A fresh case requests a missing string (USB STALL expected),
  captures a bad-final-STOP `39 d3 81` starting at 24000, services and
  drains at 40000, and rearms core 1 while preserving USB's F0. A second
  capture, `a6 5c 27`, starts at 65000; at 66000 a normal USB
  device-descriptor request clears the STALL through SETUP. No loading-host
  service follows the rearm until 100000.

Raw resolved USB pads, packet bytes, CRCs and toggles, UART source, samples,
STOPs, publication and HALT, flags and enables, and paid bus operations are
kept. The UART transmitter and service times never depend on the DUT's PC
or readiness.

### Results

| Measured outcome | Case 1: enumeration plus 32 B | Case 2: error, clear and rearm |
| --- | --- | --- |
| USB completion | 109449, configured, address 5, no STALL | 88137, address 0, no STALL |
| UART HALT clocks | 137370 | 36551 bad / 77471 good |
| Delivered words | 8 words / 32 exact bytes | 0x81d33900 / 0x275ca600 |
| UART RX peak | 8 words | 1 word |
| USB wire inventory | 77 packets (51 peer / 26 device) | 20 packets (13 peer / 7 device) |
| Data/STOP source margins | 161/161 clocks | 158/158 clocks |
| Loading-host quiet clocks | 291..150000 | 24000..40000 and 40024..100000 |

The recorded run used 150034 clocks for case 1 and 100013 for case 2:
250047 counted `Chip.step` calls in total, against a cap of 500000,
including cold loads, setup, reads and drains.

**Case 1.** The UART source spans clocks 4096 through 137429. 76 USB
packets, including all 26 device responses, overlap that interval. Both
cores stay enabled through the exchange; core 1 HALTs after its final STOP
sample and keeps all 32 exact bytes without loading-host refill or service.
The fixed drain at 150000 returns the eight little-endian words and leaves
the target FIFO empty. USB has exactly its standard one intentional
missing-string STALL; retries 0, first-response DATA toggles and CRCs, and
the 2..6.5-bit turnaround oracle all pass. All measured response
turnarounds are 2.15625 low-speed bit times.

**Case 2.** The normal unsupported string sets USB's F0 at 4815. The UART
bad final STOP sets F6 at 36395 (flags 1 → 65), before HALT at 36551. The
ordinary service at 40000 returns `39 d3 81`, keeping the suspect byte. The
rearm's FCLR 64 at 40018 changes flags 65 → 1, preserving USB's STALL and
core 0's enable; the five plan writes and paid inspection reads finish at
40024, before the fixed source at 65000. The request started at 66000 clears
F0 through a normal SETUP at 70390. The second capture returns `a6 5c 27`
with F6 clear, HALT, no fault and no residual words. Core 0 is never disabled
or restarted after its cold start, and neither image is reloaded, reset or
has its FIFO cleared. All 380 UART START, data and STOP observations across
38 bytes fall within the independent source windows.

**Costs.** Each case pays 281 cold image and configuration writes (USB 256 +
UART 25). Case 1's arm pays 9 model clocks (two precondition reads, five
writes), then one shared start write. Case 2's inspected arms each pay 15
clocks; its first drain is 9 and drain plus rearm is 24. Case 1's
completion, drain and state reads take 34 clocks; case 2's last ones take 13.
These are direct model-host bus costs, not serialized SPI-host timings. Raw
paid operations and strobes, flag and enable changes, per-core FIFO peaks,
pin windows, publications and HALTs, resolved USB pad edges and complete
direction-tagged packet and CRC inventories are saved losslessly. There were
zero active loading-host API calls or strobes, UART PUSH or autopush stalls,
host FIFO drops and nonempty clears. Normal WAIT and line blocking remain
observable (core stall clocks 141916/8645 and 97630/42185); these include
idle time, not loss of captured bytes.

**Line-RX overflow.** The line unit reports 276 and 92 RX overflow events in
the two cases: 46 echo bits from each of six and two eight-byte descriptor
replies. They are the device's
[own echo](usb-uart-overlap4.md#line-rx-overflow-is-the-devices-own-echo);
both cases assert that every dropped bit belongs to a receiver frame the
device started and that every peer packet starts on an empty, idle receiver.

### Limits

No full-speed envelope, live serialized-host concurrency, UART-to-USB
forwarding, indefinite input stream, CPU, PPA or electrical result, and no
concurrent native or RTL qualification is provided. Both engines are
occupied; this result uses the existing 1088 B program allocation (1060 B before `fault-fixes`) and the
8-word UART FIFO, without extra SRAM, cores or flags. A spare F7 does not
imply a spare engine.

### Reading a saved case

Saved nested bytes use explicit tagged hex, including empty bytes and
bytearray; unsupported object types still raise TypeError. For example,
`b"\xd2"` becomes `{"__bytes_type__": "bytes", "hex": "d2"}`.
To restore these values when reading a saved case:

```python
import json

def restore_bytes(value):
    if set(value) == {"__bytes_type__", "hex"}:
        kind = value["__bytes_type__"]
        if kind == "bytes":
            return bytes.fromhex(value["hex"])
        if kind == "bytearray":
            return bytearray.fromhex(value["hex"])
    return value

case = json.loads(saved_text, object_hook=restore_bytes)
```

This keeps raw arrays, original exception strings and USB ACK, empty and
DATA bytes instead of dropping or stringifying them.

## Run history

The test was developed by a team of agents in two numbered tickets on
2026-10-13. Both ran in a disposable Git tree,
`b7267f4f146a5899e6a3ad7463cb5f6ad324c1c8` (the tree of commit `67443d9`,
the published (redacted) version of that tree), with only the four new
files added, while the worker's branch stayed at `82ba9b2`. Each ticket
allowed one model invocation. Their evidence (exact commands and exits, source
hashes, the raw cases of C16) is in `evidence/sol-kepler/C15-KEPLER-USB-UART-PEER/`
and `evidence/sol-kepler/C16-KEPLER-START-SAVE/` under the team's directory,
which is not part of this repository.

**C15: failed harness, no result.** A zero-clock preflight passed: 19 / 246
words, identical UART timing, shifts and wrap, with only the flag immediates
at indices 11 and 15 changed from 0 to 6, and relocated branches at
256..274; 128 valid plans, 16 invalid inputs and three rejecting mocks
(payload corruption, unauthorized bus strobe, nominal USB error) passed
(exit 0, 0.292 s). The single dynamic invocation then failed case 1 at the
150000 service deadline: core 1's STATUS read returned 0 (HALT 0, fault 0,
RX 0, TX 0), so no drain or byte comparison ran. The enumeration helper had
returned before the deadline, but the final configured-state read, the
saved-wire oracle and the overlap checks were not reached; case 2 did not
run (exit 1, 11.495 s, 150004 counted `Chip.step` calls). This did not
qualify the peer image or its isolation, and does not show that the
allocation fails.

The cause was a race in the test harness. CTRL is registered and applies on
the next model clock. Right after the peer plan's final write 514 (enable 2,
restart 2), core enable still read 0, so `ModelHost.start(1)` wrote 257
(enable 1, restart 1) and replaced the peer's enable on the following clock
(writes at clocks 289/290, both-start return at 291; the UART wire starts at
4096). This is a reconstruction from the fixed write sequence and the
model's documented semantics, not a recovered trace. A second harness bug
kept the failure record from being saved: the USB transaction log holds
bytes, and `json.dumps` raised TypeError on an ACK byte, so no case-1 raw
trace, overlap count, flag history or overflow count exists for C15. No
firmware, extra engine or SRAM, clock or synchronizer change, or hardware
change was justified by this failure.

**C16: two-fix correction, passed.** Case 1's immediate start became
`mh.apply(host.start_writes(1, enabled_before=2))`: one paid CTRL write
0x103 that enables both cores while restarting only core 0. The preceding
peer CTRL 0x202 restart is unchanged, with no extra delay or read, no peek
at the pending enable, no shared helper change and no change to stimulus,
oracles or case 2's cold start and absolute times. Saved bytes became tagged
hex ([above](#reading-a-saved-case)). A zero-clock preflight (exit 0,
0.356 s) checked the narrow delta, 22 unchanged dependencies, the
deferred-mask arithmetic and lossless round trips, with the negative oracles
kept. The single invocation then passed both cases (exit 0, 20.948 s, pytest
20.52 s, 2 passed, the pure test deselected); the [results](#results) are
from this run. There was no second invocation, firmware change, default API
edit or timing retune.

The test was integrated on `main` afterwards; at `d61f548` (2026-10-14)
`test/model/test_usb_uart_coexist.py` passes, 3 tests including both cases.
