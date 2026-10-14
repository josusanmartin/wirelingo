# One retained UART byte over USB EP0

## Summary

**What it does.** Core 1 receives one 115200-baud 8N1 UART byte for each
active-high READY grant. Core 0 returns that byte to a USB host through a
vendor control read on low-speed EP0, and keeps it until the host's DATA ACK.
It is a one-byte mailbox with an admission wire, not a continuous stream or
a USB class driver. It occupies both cores and banks; the existing
enumeration and UART capture programs are separate modes and are unchanged.

**How to use it.**

* Load: `pe.usb_uart_byte.assemble()` builds the two images and
  `load_writes()` gives the 300 cold host writes that load them and start
  both cores. Load with both cores disabled.
* Read: the host sends `vendor_request()`, `c0 01 00 00 00 00 01 00`. An
  empty slot answers STALL; send a fresh SETUP after the next UART frame.
* Pins: USB D+ on `uio[0]`, D− on `uio[1]`; UART RX on pin 16 (`ui[0]`) by
  default, or any other pad input with `rx_pin=`; READY on pin 8 (`uo[0]`),
  active high.
* Demo board: `bringup.usb_uart(pe, "byte")` loads a build with RX on
  `uio[6]`. On the PC, `PYTHONPATH=tools python3 -m pe.usb_uart_reports byte`
  prints each byte. Wiring is in [info.md](info.md).

**Envelope.** Tested at a 48 MHz system clock and nominal 115200 baud only:
no baud tolerance, jitter or continuous rate is tested. Capacity is one
byte, and the sender must wait for READY. In the tests READY rose again
89–123 clocks after the end of the releasing ACK (cycle model) or 651–689
clocks after the releasing ACK began (RTL). These are observations from
finite scenarios, not bounds or a sustained rate. A full slot may stall the
sender indefinitely.

**Evidence.**

* Cycle model: one fault and recovery scenario
  (`test/model/test_usb_uart_byte.py`): three good bytes delivered in order
  through lost and malformed ACKs, a descriptor interleave, deconfigure and
  reconfigure, a USB detector reset and a bad STOP, judged by an oracle that
  decodes the raw pads.
* RTL simulation: one pins-only scenario on the top level (`test_byte_rtl`,
  Icarus Verilog, behavioral SRAM): four bytes and one bad STOP, identical
  to the cycle model clock for clock; sigrok's decoders agree. It does not
  include the model scenario's fault cases.
* The demo-board build (RX on `uio[6]`): cycle model only.
* FPGA: the UART half (`uart_rx_mailbox`) ran on the KV260 build with the
  host standing in for the USB core
  ([uart-capture-host.md](uart-capture-host.md#on-the-fpga-build-2026-10-14)).
  The pair has not run there.
* Not run: gate level, a demo board, a PC, electrical USB or a real UART
  sender.

## Contract and resources

| Resource | Reservation |
|---|---|
| Core 0 / bank 0 | `usb_uart_byte.pasm`: 256 words / 1024 B, no spare words |
| Core 1 / bank 1 | `uart_rx_mailbox.pasm`: 20 words / 80 B, within a 32-word budget |
| USB | low speed, D+ uio0 / D− uio1; line period 32, TURN 63, core divider 1 |
| UART | RX logical 16 / ui0; nominal 115200 baud, eight execution ticks per bit |
| READY | logical 8 / uo0, **active high**; one frame per observed grant |
| Communication | virtual pins 24..31 owned by core 1; core 0 IN_BASE 24 |
| Flags | F0..5 USB; F6 sticky discarded bad STOP; F7 occupied |
| FIFOs | no runtime queue or service; the depth-8 profile is still charged |

The UART divider is `52 + 21/256` clocks per tick (encoded 13333), from
`host.clkdiv_for(48e6, 8*115200)`. This differs slightly from the exact
nominal source period of 1250/3 clocks per bit. The finite nominal scenario
below passed at this divider. There is no tolerance, continuous-rate, USB
throughput, CPU, PPA or physical claim.

## Cold load and request

`pe.usb_uart_byte.assemble()` returns the two complete images.
`load_writes()` uses the supported tapeout loaders, owner and configuration
writes, cold-clears flags and outputs, and starts both cores. Use a cold or
disabled workload; do not apply it to a running capture or enumeration mode.
There is no runtime loading-host API, FIFO relay or rearm on the device side.
Normal external USB host transactions perform the reads and are a real
service cost.

`vendor_request()` is `c0 01 00 00 00 00 01 00`: vendor/device IN request 1,
wValue 0, wIndex 0, wLength 1. The firmware ignores wValue and wIndex, clamps
wLength to 0..1 and accepts this optional request in the default, addressed
and configured states. An empty slot STALLs; issue a fresh SETUP after a new
UART grant and frame. The original 18-byte device and configuration
descriptors keep zero non-control endpoints; this is not HID, CDC, bulk or
interrupt USB.

### From the demo board and a PC

`assemble(rx_pin=…)` and `load_writes(rx_pin=…)` move RX to another pad input
(not the USB pins 0/1); the default stays pin 16 (`ui[0]`) and its image is
byte-identical. The Tiny Tapeout board driver holds `ui[0]` low, so
`bringup.usb_uart(pe, "byte")` loads `protoemu_fw.USB_UART_BYTE`, built with
RX on `uio[6]` (pin 6); READY stays on `uo[0]` and USB on `uio[0]`/`uio[1]`.
Only core 1's words and the pin owners differ. On the PC,
`PYTHONPATH=tools python3 -m pe.usb_uart_reports byte` issues this request
(wLength 1) through pyusb and prints each byte; wiring is in
[info.md](info.md).
[`test_micropython_usb_uart.py`](../test/model/test_micropython_usb_uart.py)
runs the board code on the cycle model: a pins-only sender on `uio[6]` sends
`a5`, `00`, a bad-STOP `3c` and `ff`; after enumeration the PC reader returns
`a5 00 ff` and F6 is set, with the host port idle after the load. Model
evidence only; not yet run on RTL with RX on `uio[6]`, on a board or on a PC.

The mailbox table entry is at 37, whose bit 3 is clear for the initial DATA1.
Only its low byte is sent; rotated physical USB inputs in the upper byte
are not a second stable payload byte. The current single-LR call/return flow
is preserved; there is no nested supplier call or hardware return stack.

## Admission, release and recovery

The UART peer must observe READY high, then send at most one complete 8N1
frame. Idle-high RX is established before READY is granted, followed directly
by START detection. READY falls after START and stays low while data is
pending. A good STOP publishes the virtual data before setting F7. A bad STOP
discards the frame, sets F6, waits for idle and offers a new grant. F6 stays
set until cold setup. A peer that transmits while READY is low is outside the
lossless contract: that byte can be lost, but must not overwrite a pending
byte. READY is explicitly active high; there is no unspecified conventional
RTS polarity and no implicit in-flight buffer.

A valid ACK (F_SENT) at the exact one-byte vendor cursor 1061 releases F7. A
lost or malformed ACK, another EP0 packet, an abandoned SETUP and wLength 0
cannot release it. A fresh vendor SETUP re-reads the pending byte.
SET_CONFIGURATION 0/1 and USB reset keep the producer's byte and F7; after a
reset, a new default-address SETUP starts a new transfer. Cold load starts
empty. Release happens on the DATA ACK, **before** status completion, so
exactly-once delivery to the application across USB host failures is not
promised. The USB side inherits the final-bit dribble guard and its shared
EOP and error-indication limits.

## The model scenario

`test/model/test_usb_uart_byte.py` declares one serial scenario on two
cores, two banks, 256 words and depth 8, with synchronizers enabled. It
enumerates once at address 5 and drives good 0x00, 0xa5 and 0xff, a bad-STOP
0x3c, and a separate READY-violating 0x5a. The nominal UART sender uses
rational edge lists and only the physical READY pin for admission, with 8
clocks of observation and reaction, and finishes any frame it started.

Before enumeration, a default-address empty-slot probe precedes the bad-STOP
0x3c; the next compliant 0x00 START comes exactly 8 system clocks after the
new physical READY rising edge. No USB work or settling is inserted before
that witness. Enumeration then runs while 0x00 is retained. The scenario
checks:

* empty-slot STALL and recovery by a fresh SETUP;
* lost and malformed ACK retries;
* a descriptor interleave;
* a deliberate zero-length IN/ZLP ACK probe;
* one maximum valid final-level hold;
* retention across deconfigure and reconfigure;
* a short USB reset while data is pending. Its 144-clock SE0 and
  4800-clock recovery test the model's detector; they are not a normative
  10 ms or electrical reset qualification.

All nominal USB attempts are accounted for; retries are disabled. Complete
resolved pad windows, direction-tagged packets, READY and UART edges,
observations of zero active loading-host API calls and strobes, and line-RX
overflow and own-TX overlap are saved. Internal flags, mailbox and PC
snapshots only corroborate; they never decide UART drives or substitute for
decoded DATA, PID and CRC. Logical consumption means the three matching DATA
ACK releases in source order, not one physical transmission per byte.

The independent oracle decodes the saved pads and CRCs, counts every expected
response (errors included, not only eventual success), and must reject three
corrupted copies of the saved trace: a retried payload changed with a
recomputed CRC, an early READY release, and an extra nominal error repaired
by a later success. The test runs these three controls after its audit.
This scenario does not qualify concurrent third work, electrical USB or
UART, every malformed ending, or a continuous rate or phase envelope. No
generated bundle is included.

### Recorded model run

One recorded run of this scenario (2026-10-13; see
[Run history](#run-history)) made 237290 `Chip.step` calls including loading
and helpers, in 19.852919 s wall time, on two cores, two banks, 256 words,
depth 8 and SYNC_BYPASS 0. The programs occupy 256 + 20 words / 1104 B and
need 300 cold host writes. The raw-pad oracle decoded 155 packets from 101
external USB host transactions and saw 0x00, 0xa5 and 0xff consumed in
order. The post-error 0x00 START followed the new physical READY rising edge
by 8 system clocks. Three fixed 64-clock gaps after an unacknowledged
transfer separated the retained DATA EOP-J from the descriptor and
deconfigure SETUPs and from the detector-reset SE0. Active loading APIs,
observed loading bus strobes and USB driver overlap were all 0.

Observed latencies from the consuming ACK's EOP-J to the next READY were
89, 111 and 123 clocks for 0x00, 0xa5 and 0xff. They are observations from
that one fault and recovery scenario, not maximum bounds or a sustained
service rate. The 276 line-RX overflow events are the device's
[own echo](usb-uart-overlap4.md#line-rx-overflow-is-the-devices-own-echo):
six descriptor replies drop 46 bits each. The test asserts that every
dropped bit belongs to a receiver frame the device started and that every
peer packet starts on an empty, idle receiver. External USB host work
remains a service cost. Capacity is one byte, core 0's 256-word bank is
full, and sender admission still requires the physical READY pin. Beyond the
[RTL scenario](#rtl-simulation) below, there is no RTL, board, physical or
continuous-stream qualification.

That run's evidence is outside this repository; its location is in the
[Run history](#run-history). Historical assembly hypotheses are not measured
artifact identities.

## RTL simulation

[`test_byte_rtl`](../test/test_usb_uart_rtl.py) runs the harness described in
the [overlap4 guide](usb-uart-overlap4.md#rtl-simulation) on the RTL top
level. The helper's 300 writes go through the SPI port (14 transactions,
19,872 clocks at SCK = clk/2), and the host port is then checked idle on
the pads. The [scenario](../test/model/usb_uart_pairs.py) uses address 0
and `vendor_request()`. The time origin is the first clock READY is high,
155 clocks after the load.

In 70,519 clocks (1.47 ms) the sender offers `00`, `ff`, a bad-STOP `5a`,
`a5` and `81`. The host first makes an empty request, which is STALLed. It
then reads greedily, and the third byte is fetched with an in-transfer IN
retry that returns the identical DATA packet. A final empty request is
STALLed. There are 7 requests (3 STALLed) in 53 USB packets.

The test checks the following against the sender's byte list:

* The reports are exactly `00`, `ff`, `a5` and `81`, in order. The bad-STOP
  byte is absent.
* Every frame starts on a fresh READY rise while READY is high. READY falls
  93–103 clocks after START, about two ticks of the 52+21/256-clock divider.
* READY holds three frames until the previous byte's read: for 10,146, 7,135
  and 12,082 clocks. It rises 651–689 clocks after the releasing ACK begins.
* The frame after the bad STOP waited 260 clocks for READY.
* The ERROR pin (not used by this mode) stays low.
* F6 is set and F7 is clear at the end, read through the SPI port after the
  scenario.

The model run of the same scenario is identical in reports, packets with
their clocks, frame and grant clocks, and every pin edge. sigrok decodes the
saved RTL pins as the same 5 bytes (one framing error) and 53 packets without
errors. This is RTL simulation with ideal edges. It does not cover the model
scenario's fault cases (malformed ACK, reset, deconfigure), gate-level timing
or electrical USB.

## Running the tests

Ordinary pytest needs no `WIRELINGO_EVIDENCE` setting. Output goes to
pytest's isolated `tmp_path`; an unset or empty variable selects that
default. From the repository root, with bytecode suppressed:

    PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tools /usr/bin/python3 -B -m pytest \
      -q -x -p no:cacheprovider test/model/test_usb_uart_byte.py

A nonempty value selects an explicit directory, which the test creates if
needed. Use a fresh path to keep each run's raw scenario separately:

    PYTHONDONTWRITEBYTECODE=1 WIRELINGO_EVIDENCE=/path/to/new/evidence \
      PYTHONPATH=tools /usr/bin/python3 -B -m pytest -q -x -p no:cacheprovider \
      test/model/test_usb_uart_byte.py

In the recorded runs, the override's handling of an explicit or empty value
was reviewed in source, not run. A passing run is not a release approval or
a full-suite result.

The RTL scenario: `make COCOTB_TEST_MODULES=test_usb_uart_rtl
COCOTB_TEST_FILTER=test_byte_rtl` in `test/`.

## Run history

This feature was developed on 2026-10-13 by a team of agents working in
numbered tickets (C18, C21, C23, C24) and integrated on `main` as `5131faa`.
Each ticket ran the scenario at most once, with fixed firmware and oracles.

* **C18**: the first attempt failed before the model was built (a Python
  import error in the launcher's environment). No model result.
* **C21**: the scenario ran and failed at the descriptor interleave. The test
  fixture sent the next SETUP with no gap after an unacknowledged transfer,
  overlapping the device's own transmission by 32 clocks. That was a test
  scheduling fault; no firmware defect was established.
* **C23**: the test schedule gained fixed 64-clock gaps at the three such
  handoffs, with the same firmware, expected packets and assertions. The
  scenario passed; the figures in [Recorded model run](#recorded-model-run)
  are from this run, and its saved trace rejected the three corrupted copies.
  Its evidence (`source-frozen.json`, `dynamic.json`, `saved-audit.json` and
  `saved-gaps.json`) is in `evidence/sol-atlas/C23-ATLAS-NOACK-GAP/` under the
  team's directory, which is not part of this repository. The test then
  required `WIRELINGO_EVIDENCE` to be set.
* **C24** made `WIRELINGO_EVIDENCE` optional (default `tmp_path`) and ran the
  same scenario once without it, on 2026-10-13: 1 passed, 237290 `Chip.step`
  calls, 19.35 s wall. The raw trace it wrote was byte-identical to C23's
  (both SHA-256 `ad5baab5…`). The team lead accepted it; the five files of
  that accepted commit are the ones in `5131faa`. An earlier version of this
  page, written before C24 ran, called its result pending. The team's
  records (`evidence/sol-atlas/C24-ATLAS-PYTEST-DEFAULT/` and the lead's
  acceptance, not part of this repository) show it ran and passed.

Since then the test has gained the line-echo attribution, runs the three
corruption controls itself, and accepts `rx_pin=` for the board build
(`a2b79fd`, `708a64a`, `8c889f1`); it runs in the model suite.
