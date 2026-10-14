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
admission still requires physical READY. There is no RTL, board, physical
or continuous-stream qualification.

C23's saved offline oracle also rejected three mutations: retained retry
payload changed with recomputed CRC, early READY release and an extra
nominal error hidden by later success. Its evidence is in
`evidence/sol-atlas/C23-ATLAS-NOACK-GAP/` (source-frozen.json, dynamic.json,
saved-audit.json and saved-gaps.json). Historical assembly hypotheses are
not measured artifact identities.

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
