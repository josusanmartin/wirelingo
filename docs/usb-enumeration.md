# Autonomous USB enumeration

Branch `usb-enum` (from `v2`; no RTL change). The question: after the host
loads the firmware, can Wirelingo enumerate as a USB device **with no host CPU
involvement**? Until now the chip did the bit-level work (NRZI, stuffing,
CRC16, token filter, handshakes within the turnaround) while host software
(`pe.usbdev.DeviceSoftware`) decoded every SETUP, staged every descriptor
packet, re-staged after lost ACKs and rewrote the token filter after
SET_ADDRESS.

## Bottom line

**It fits on one core, at low speed and at full speed, with the same
firmware.** `firmware/usb_enum.pasm` is 240 of core 0's 256 instruction words
(36 of them the descriptors), uses the line unit, and leaves core 1 free.
`pe.usbenum.load_writes()` is the whole host side: the program, CRCCTRL, the
line unit's three registers and the start. From then on the host transport is
idle (the tests fail on any host bus access, and the RTL test watches CS_n,
SCK and MOSI on the pads) until the host chooses to read the outcome: the
address is in core 0's r3, and flag F5 means Configured.

The binding constraint is **instruction memory** (16 words to spare), then
**registers** (four 16-bit registers hold the token filter, the transfer state
and the prepared answer; every other bit of state is a global flag). Cycles
are not the limit at low speed (32 clocks per bit). At full speed (4 clocks
per bit) they shaped the design in three places, all with margin now: the IN
turnaround (the answer's PID is prepared before the token arrives), the
receive loop (3 instructions per bit) and the transmit loop (3 per bit plus 4
between table entries, which the line unit's 2-entry queue absorbs).

| Check | Low speed (1.5 Mbit/s) | Full speed (12 Mbit/s) |
|---|---|---|
| Model: full enumeration to Configured, transport idle | 109,397 cycles (2.28 ms at 48 MHz), 26 transactions, every one through first time | 14,169 cycles (0.30 ms), same 26 |
| Clock offset / edges | ±1.5 % (the low-speed tolerance) | ±0.25 %, ±3 ns jitter, 2 ns duty-cycle distortion, the host's shortest gaps (2 bit times) |
| Turnaround (spec 2-6.5 bit times) | 2.12-2.19 bit times | 3.10-4.16 bit times |
| Lost ACKs (both directions), damaged DATA, bus resets mid-enumeration, 9 unsupported requests STALLed, wLength 0-255, bad CRC5/CRC16, other addresses | model | model |
| RTL (cocotb, Icarus): the flow with faults and a mid-enumeration reset | `test_usb_autonomous`: PASS, turnaround 2.16 bit times | `test_usb_autonomous_fs`: PASS, 3.25-4.00 |
| sigrok (usb_signalling + usb_packet) | model and RTL: every packet identical to the host model's record, no errors | model and RTL: same |

## The target

Enumeration to the Configured state against a standard host flow, with the
host controller model `pe.usbhost.ControlHost` (low speed; pins only) or
`pe.usbfs.FsControlHost` (full speed: the same transaction logic on
`pe.usbfs.FsHost`'s bus with edges in continuous time, jitter, frequency
offset and its own clock recovery). Neither knows anything about the
firmware; they implement the host side of USB 2.0 §8.5.3:

1. GET_DESCRIPTOR device, wLength 8 (or 64, Linux's "new scheme"), at address 0;
2. (Linux: bus reset);
3. SET_ADDRESS n; the device must keep answering address 0 until the status
   stage completes, then only n;
4. GET_DESCRIPTOR device (18), configuration (9, then wTotalLength or 255);
5. GET_DESCRIPTOR string: STALL (the descriptors have no string indices);
6. SET_CONFIGURATION 1; GET_STATUS; GET_CONFIGURATION.

With: DATA toggles (DATA1 first in every data stage; DATA1 status packets), a
host that ACKs and discards a retransmission with the old toggle, retries of
a transaction that got no answer or a damaged one, keep-alive EOPs (low
speed) or SOF tokens (full speed) every millisecond, bus resets at any point,
and STALL for anything the device doesn't support (in the data stage or, for
requests without data, the status stage), cleared by the next SETUP.

The descriptors are `pe.usbdev.DEVICE_DESC` and `CONFIG_DESC` (VID 0x1209,
PID 0x5634, one configuration with one vendor-class interface and no
endpoints besides endpoint 0, bMaxPacketSize0 8). The test
`test_table_is_the_reference_descriptors` decodes the firmware's table back
into bytes and compares. 0x1209/0x5634 is the placeholder the repository
already used; a real device needs its own PID (pid.codes or a VID owner).

## How it fits

### Descriptors live in instruction memory

There is no data memory, the FIFOs can't be re-read (and the host may not
refill them), and core 1's memory is reachable only through 8 virtual pins.
So the descriptors are code. Each table entry sends two bytes:

```
dev:    ldi r1, 0x0112      ; bLength 18, bDescriptorType 1
        call send2
        ldi r1, 0x0110      ; bcdUSB 1.10
        call send2
        ...
```

Two words per two bytes: byte offset *b* of a descriptor is at table address
`dev + b`, so a packet's start address is plain arithmetic, and the transfer
state is one register: `r2 = address | bytes_left << 10` (`mov pc, r2`
ignores the high bits). An IN computes n = min(8, bytes left), jumps into the
table, and `send2` sends `r1` bit by bit through the CRC tap (OUT_TAP) until
the packet's 8·n bits are out, then the inverted CRC16. The CRC of any
truncated packet (wLength 9 cuts the configuration descriptor's second packet
to one byte) comes for free; no CRC table.

The table also serves GET_STATUS (bytes 4-5 of the device descriptor are
00 00) and GET_CONFIGURATION (byte 4 of the device descriptor is 00, byte 4 of
the configuration descriptor is 01).

**DATA toggles need no state.** Every transfer's first packet starts at an
address whose bit 3 is clear (dev = 1, cfg = 19, dev+4, cfg+4), and packets
are 8 bytes = 8 addresses apart, so bit 3 alternates: clear = DATA1. The host's
ACK advances `r2` by n (address + n, bytes left − n); without it the IN is
answered again with the same packet and toggle, which is the whole lost-ACK
protocol on the device side.

### State

| Where | What |
|---|---|
| r3 | token filter: the 16 bits after a token's PID (address, endpoint 0, CRC5) as `mov r, ::isr` shows them |
| r2 | transfer: table address of the next packet, bytes left. During SET_ADDRESS: the new address, then its filter |
| r1 | SYNC + PID the next IN gets (STALL, DATA0, DATA1), prepared ahead by `prep` |
| r0 | scratch |
| F0-F5 | STALL, address pending, our DATA awaiting the host's ACK, DATA expected, it belongs to a SETUP, configured |
| ISR | the received SETUP's last 32 bits (wLength in the low half) |

SET_ADDRESS needs the CRC5 of the new address for the token filter. The CRC
tap's width is fixed (CRCCTRL is host configuration, set to CRC16), so
`addr_filter` computes it in software: long division, 11 iterations, done
while the status stage's zero-length packet goes out (68 clocks; the packet
and its EOP take 140 at full speed). The address takes effect when the host
ACKs that packet, or, if that ACK is lost, on the first token to the new
address (the host has moved on; a device waiting for the ACK would never
answer again).

### Cycle budget

Measured on the model (clocks at 48 MHz, `test/model` profiling of core 0's
PC over a full enumeration with a lost ACK):

| Path | Clocks | Limit at full speed |
|---|---|---|
| token's end → the IN's answer queued (incl. the 4-tick turnaround delay) | 9 | 26 (6.5 bit times) |
| DATA's end → handshake decision (CRC residue, length, flags) | 9-10 | 26 |
| SETUP request processing, while our ACK goes out | 16-32 | 76 (the ACK and its EOP) |
| status ZLP's CRC queued → the address filter computed | 68 | 140 |
| host ACK's end → back at idle (toggle advance, `prep`) | 16-24 | see below |
| receive, per bit / transmit, per bit (per 16 bits) | 3 / 3 (52) | 4 / 4 (64) |

At low speed every limit is 8 times larger. The one path that can exceed its
window at full speed is the host's ACK followed by its next token 2 bit times
later: the core may reach `wait fall DM` up to ~12 clocks into the next
packet's SYNC. That is harmless by construction: the line unit queues
received bits from a frame's first edge, so the WAIT only has to catch any
fall inside the SYNC (KJKJKJKK has four), and the ACK frame's end is consumed
*before* the processing starts. The rule behind it is an ISA property (below):
a frame's end must be consumed before the next frame begins.

### One core

Core 1 isn't needed. A two-core split (line protocol on core 0, requests on
core 1) would have to pass the SETUP fields and the descriptor stream over 8
virtual pins and flags, costing words on both sides and adding a handshake on
the turnaround path; the descriptors are the only large item and they have to
be next to the line unit anyway, since only core 0 can feed it. Core 1 stays
free for a second job (a data endpoint's data source, or another protocol).

## ISA limitations that bit

* **13-bit ALU immediates.** The CRC16 residue 0x800D can't be compared
  directly: `(crc ^ 0xD) + itself` is 0 with a carry only for 0x8000. The
  CRC5 polynomial at the top of a 16-bit register (0x2800) doesn't fit either;
  the division runs in a 13-bit window (first bit at bit 12, polynomial
  0x1280). PIDs are compared as `isr >> 8`.
* **No data memory**: descriptors as `ldi` + `call`, two words per two bytes
  (36 words for 36 bytes).
* **The CRC tap only sees 1-bit shifts**, so transmitted payload goes out one
  bit per `out line, 1`: 3 instructions per bit, inside full speed's 4 clocks
  by one.
* **One CRC configuration, set by the host**: CRC5 in software.
* **No `jmp !t`** (only `t`): two instructions where one would do.
* **No flag toggle**: the DATA toggle comes from the table address instead.
* **One link register**: `commit` falls into `prep` rather than calling it.
* **The line unit's receive queue has no frame boundaries**, and the firmware
  can't flush it (LINECFG is host-only). A frame's end must be consumed before
  the next frame starts, or the next frame's bits are read as the old one's.
  Every path consumes to the frame's end; the host-ACK path does so before its
  processing.

## Tests

`test/model/test_usb_autonomous.py` (model, both speeds; 63 tests):
enumeration with clock offsets and jitter (every transaction through first
time), the Linux flow with a 10 ms reset, wLength 0-255 on both descriptors,
each fault kind sprinkled randomly over the flow (3 seeds × 3 kinds × 2
speeds), SET_ADDRESS's status ACK lost, a repeated and an abandoned
SET_ADDRESS, bus resets in the data stage / after the address / after
configuration (then enumeration at a new address), a 3 µs reset (just over
the 2.5 µs threshold) unconfiguring the device, 9 unsupported requests
STALLed and cleared by the next SETUP (including OUT data of an unsupported
control write), tokens to other addresses/endpoints, a bad CRC5 and a bad
CRC16 ignored, and sigrok's decode of a whole enumeration with faults.

`test/test.py::test_usb_autonomous` and `test_usb_autonomous_fs` (RTL):
load over SPI, then the whole flow with a lost SETUP ACK, a lost host ACK,
damaged DATA, a STALLed string descriptor and SET_FEATURE, a bus reset in the
middle of a configuration read, enumeration again at a new address with
SET_ADDRESS's status ACK lost; the pads show no host port activity from the
load to the end; then the host reads address 9 and Configured.
`test/test_sigrok.py::sigrok_usb_autonomous` and `_fs` run those tests and
check that sigrok decodes exactly the packets the host model recorded.

Results (2026-10-06):

```
pytest test/model/test_usb_autonomous.py          63 passed in 168.93s
  low {'ppm': 0}: configured in 109413 cycles (2.28 ms at 48 MHz), 26 transactions, turnaround 2.16-2.16 bit times
  low {'ppm': 15000}: ... 110492 cycles, turnaround 2.12-2.16;  {'ppm': -15000}: 108331 cycles, 2.16-2.19
  full {'ppm': 0}: configured in 14169 cycles (0.30 ms at 48 MHz), 26 transactions, turnaround 3.17-4.09 bit times
  full {'ppm': 2500, 'jitter_ns': 3}: 14137 cycles, 3.12-4.10;  {'ppm': -2500, 'jitter_ns': 3, 'dcd_ns': 2}: 14226 cycles, 3.10-4.16
  sigrok (low speed): 81 packets, identical to the host model's, no errors
  sigrok (full speed): 81 packets, identical to the host model's, no errors
pytest test/model                                  300 passed (whole suite)

make COCOTB_TEST_FILTER=test_usb_autonomous        (RTL, Icarus)
  autonomous USB (low speed): 47 transactions, 254266 cycles (5.3 ms), turnaround 2.16-2.16 bit times,
    host port idle throughout; state {'address': 9, 'configured': True, 'pending_address': False, 'stalled': False}
  autonomous USB (full speed): 47 transactions, 78179 cycles (1.6 ms), turnaround 3.25-4.00 bit times,
    host port idle throughout; state {'address': 9, 'configured': True, ...}
  TESTS=2 PASS=2 FAIL=0
make COCOTB_TEST_MODULES=test_sigrok COCOTB_TEST_FILTER=sigrok_usb_autonomous
  sigrok usb autonomous (low-speed): 137 packets, identical to the host model's, no errors
  sigrok usb autonomous (full-speed): 137 packets, identical to the host model's, no errors
  TESTS=2 PASS=2 FAIL=0
```

Not run: gate level (the netlist is v2c's, unchanged by firmware; the existing
GL flow would run these tests as-is).

## What remains

* **Data endpoints.** An interrupt IN endpoint (HID, or vendor) needs an
  endpoint descriptor (7 bytes: 4 table words, wTotalLength 25), a second
  token filter (the endpoint field and so the CRC5 differ; no register is
  free to hold it, so it would be recomputed per token or kept elsewhere), its
  own toggle (a flag), and a data source: core 1 through virtual pins, or the
  host through the TX FIFO, which brings the host back for data, though not
  for enumeration. The descriptor fits in the 16 spare words; the token
  dispatch and data path need room made first (candidates: GET_CONFIGURATION,
  6 words; the interface/endpoint GET_STATUS range, 2; the SETUP length check,
  3).
* **String descriptors** cost one word per byte (UTF-16LE: two bytes per
  character), so a product name of 8 characters is ~20 words: over budget
  today.
* **Suspend/resume** (3 ms idle): not detected; the device keeps its state.
* **Real hosts.** Tested against independent host models and sigrok, not yet
  against a Linux/Windows host. Requests outside the list above are STALLed,
  which hosts accept (Windows' 0xEE string, the device qualifier); SET_INTERFACE
  is STALLed too, which Linux tolerates for single-setting interfaces.
* **Full speed on silicon** needs the chip at exactly 48 MHz and the line
  unit's receive jitter margin (docs/usb-full-speed.md: v2's half-cycle NRZI
  sample point); none of that changes here.
