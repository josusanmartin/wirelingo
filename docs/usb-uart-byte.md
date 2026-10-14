# One retained UART byte over USB EP0

This optional workload occupies both tapeout cores/banks and the existing
line/CRC resources. Core1 receives one nominal115200 8N1 UART frame per
active-high READY grant. Core0 returns that byte through a vendor/device
control read, retaining it until a matching valid DATA ACK. It is a one-byte
mailbox with an external admission wire, not a continuous stream or a USB
class driver. Existing enumeration and UART capture programs are separate
modes and remain unchanged.

| Resource | Reservation |
|---|---|
| Core0 / bank0 | `usb_uart_byte.pasm`:256words/1024B, no spare words |
| Core1 / bank1 | `uart_rx_mailbox.pasm`:20words/80B, <=32word budget |
| USB | LS D+ uio0 / D− uio1; line period32, TURN63, core divider1 |
| UART | RX logical16 / ui0; nominal115200, eight execution ticks/bit |
| READY | logical8 / uo0, **active high**; one frame per observed grant |
| Communication | virtual24..31 owned by core1; core0 IN_BASE24 |
| Flags | F0..5 USB; F6 sticky discarded bad-STOP; F7 occupied |
| FIFOs | no runtime queue/service; depth8 profile still charged |

The UART divider is `52 + 21/256` clocks/tick (encoded13333), from
`host.clkdiv_for(48e6, 8*115200)`. This differs slightly from the exact nominal
source period1250/3 clocks/bit. The finite C23 nominal scenario passed at
this divider. There is no tolerance, continuous-rate, USB throughput, CPU,
PPA or physical claim. A full slot may stall the producer indefinitely.

## Cold load and request

`pe.usb_uart_byte.assemble()` returns the two complete images.
`load_writes()` uses supported tapeout loaders/owner/config writes, cold
clears flags/outputs, and starts both cores. Use a cold/disabled workload;
do not apply it to a running capture or enumeration mode. Runtime device-side
loading-host APIs, FIFO relay and rearm are absent. Normal external USB host
transactions perform the reads and are a real service cost.

`vendor_request()` is `c0 01 00 00 00 00 01 00`: vendor/device IN request1,
wValue0/wIndex0/wLength1. Firmware ignores wValue/wIndex, clamps wLength to
0..1 and accepts this optional request in default/addressed/configured state.
An empty slot STALLs; issue a fresh SETUP after a new UART grant/frame.
The original18/18-byte descriptors retain zero noncontrol endpoints; this
is not HID, CDC, bulk or interrupt USB.

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

The mailbox table entry is at37, whose bit3 is clear for initial DATA1.
Only its low byte is sent; rotated physical USB inputs in the upper byte
are not a second stable payload byte. Current single-LR call/return flow is
preserved; there is no nested supplier call or hardware return stack.

## Admission, release and recovery

The UART peer must observe READY high, then emit at most one complete8N1
frame. Idle-high RX is established before READY is granted, followed directly by
START detection. READY falls after START and stays low while data is pending.
Good STOP publishes virtual data before setting F7. Bad STOP discards the
frame, sets F6, waits for idle and offers a new grant. F6 remains sticky until
cold setup. A peer that transmits while READY is low is outside the lossless
contract: that byte can be lost, but must not overwrite a pending byte.
READY is explicitly active high, without an unspecified conventional RTS
polarity or an implicit in-flight buffer.

Valid ACK/F_SENT and exact one-byte vendor cursor1061 release F7. Lost or
malformed ACK, another EP0 packet, an abandoned SETUP and wLength0 cannot
release it. A fresh vendor SETUP re-reads the pending byte. SET_CONFIGURATION
0/1 and USB reset retain the producer byte/F7; after reset a new default-
address SETUP starts a new transfer. Cold load starts empty. Release is on
DATA ACK **before** status completion; application exactly-once delivery
across USB host failures is not promised. The USB source inherits the final-
bit dribble guard and its shared EOP/error-indication limitations.

## Maintained finite scenario

The new `test/model/test_usb_uart_byte.py` declares one serial scenario on
actual2cores/2banks/256words/depth8 with enabled synchronizers. It enumerates
once at address5 and drives good0x00/0xa5/0xff, bad-STOP0x3c, and a distinct
READY-violating0x5a. The nominal UART sender uses rational edge lists and
only physical READY for admission, with8 clocks observation/reaction and
completion of any started frame.

Before enumeration, a default-address empty-slot probe precedes bad STOP0x3c;
the next compliant0x00 START is exactly8 system clocks after the NEW physical
READY rising edge. No USB work or settling is inserted before that witness.
Enumeration then runs while0x00 is retained. The scenario checks empty-slot
STALL/fresh-SETUP recovery, lost and malformed
ACK retry, a descriptor interleave, a deliberate zero-length IN/ZLP ACK probe,
one maximum valid final-level hold, deconfigure/reconfigure retention, and
a short USB reset while data is pending. Its144-clock SE0/4800-clock recovery
is a model detector test, not a normative10ms or electrical reset qualification.
All nominal USB attempts are accounted; retries are disabled. Complete
resolved pad windows, direction-tagged packets, READY/UART edges, zero-active-
API/strobe observations and line RX overflow/ownTX overlap are saved. Internal
flags/mailbox/PC snapshots corroborate, never decide UART drives or substitute
for decoded DATA/PID/CRC. Logical consumption means the three matching DATA
ACK releases in source order, not one physical transmission per byte.

The independent oracle decodes saved pads and CRC, counts all expected
responses (including errors rather than eventual success), and rejects
recomputed-CRC retry corruption, early READY/release and an extra nominal
error repaired by later success. Model evidence for this scenario does not
qualify concurrent third work, electrical USB/UART, every malformed ending,
or a continuous rate/phase envelope. No generated bundle is included.

## Measured model scope

C23 executed this one fault/recovery scenario once:237290 `Chip.step` calls
including loading and helpers,19.852919s wall, actual2cores/2banks/256words/
depth8 and SYNC_BYPASS0. The programs occupy256+20words/1104B and require
300 cold host writes. Its independent raw-pad oracle decoded155 packets
from101 external USB-host transactions and consumed0x00/0xa5/0xff in order.
The post-error0x00 START followed the new physical READY rising edge by8
system clocks. Three fixed64-clock no-ACK handoffs separated the retained
DATA EOP-J from descriptor/deconfigure SETUP or detector-reset SE0. Active
loading APIs, observed loading bus strobes and USB driver overlap were all0.

Observed consuming-ACK EOP-J to next READY latencies were89/111/123clocks for
0x00/0xa5/0xff. These are observations from that one fault/recovery scenario,
not maximum bounds or a sustained service rate. The 276 line-RX overflow
events are the device's [own echo](usb-uart-overlap4.md#line-rx-overflow-is-the-devices-own-echo):
six descriptor replies drop 46 bits each. The test asserts that every dropped
bit belongs to a device-started receiver frame and that every peer packet
starts on an empty, idle receiver. External USB-host work remains a
service cost. Capacity is one byte, core0's256-word bank is full, and sender
admission still requires physical READY. Beyond the
[RTL scenario](#rtl-simulation) below, there is no RTL, board, physical
or continuous-stream qualification.

C23's saved offline oracle also rejected three mutations: retained retry
payload changed with recomputed CRC, early READY release and an extra
nominal error hidden by later success. Its evidence is in
`evidence/sol-atlas/C23-ATLAS-NOACK-GAP/` (source-frozen.json, dynamic.json,
saved-audit.json and saved-gaps.json), a team directory that is not part of
this repository. Historical assembly hypotheses are
not measured artifact identities.

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
errors. This is RTL simulation with ideal edges. It does not cover the C23
fault cases (malformed ACK, reset, deconfigure), gate-level timing or
electrical USB.

## Running the maintained test

Ordinary pytest needs no WIRELINGO_EVIDENCE setting. Output uses pytest's
isolated `tmp_path`; an unset or empty variable selects that default.
From the repository root, with bytecode suppressed:

    PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tools /usr/bin/python3 -B -m pytest \
      -q -x -p no:cacheprovider test/model/test_usb_uart_byte.py

A nonempty override selects an explicit directory, which the test creates
if needed. Use a fresh path to retain each run's raw scenario separately:

    PYTHONDONTWRITEBYTECODE=1 WIRELINGO_EVIDENCE=/path/to/new/evidence \
      PYTHONPATH=tools /usr/bin/python3 -B -m pytest -q -x -p no:cacheprovider \
      test/model/test_usb_uart_byte.py

C24 separately qualifies the default-output adapter with this same scenario
and WIRELINGO_EVIDENCE absent; it compares the newly generated raw trace
byte-for-byte with C23. The C23 measurements above remain the accepted
protocol evidence and do not imply a C24 result before that check completes.
The optional override selection is reviewed in source, without another live
scenario. Team runs require explicit ticket/resource acceptance and caps;
ordinary invocation does not grant release approval or a full-suite claim.

The RTL scenario: `make COCOTB_TEST_MODULES=test_usb_uart_rtl
COCOTB_TEST_FILTER=test_byte_rtl` in `test/`.
