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
firmware.** `firmware/usb_enum.pasm` is 246 of core 0's 256 instruction words
(36 of them the descriptors), uses the line unit, and leaves core 1 free.
`pe.usbenum.load_writes()` is the whole host side: the program, CRCCTRL, the
line unit's three registers and the start. From then on the host transport is
idle (the tests fail on any host bus access, and the RTL test watches CS_n,
SCK and MOSI on the pads) until the host chooses to read the outcome: the
address is in core 0's r3, and flag F5 means Configured.

The binding constraint is **instruction memory** (10 words to spare since the ACK tail guard; 15 before), then
**registers** (four 16-bit registers hold the token filter, the transfer state
and the prepared answer; every other bit of state is a global flag). Cycles
are not the limit at low speed (32 clocks per bit). At full speed (4 clocks
per bit) they shaped the design in three places, all with margin now: the IN
turnaround (the answer's PID is prepared before the token arrives, and
nothing else sits on the token's path, not even a pending address's commit:
see [the turnaround fix](#the-lost-status-ack-and-an-in-first) below), the
receive loop (3 instructions per bit) and the transmit loop (3 per bit plus 4
between table entries, which the line unit's 2-entry queue absorbs).

| Check | Low speed (1.5 Mbit/s) | Full speed (12 Mbit/s) |
|---|---|---|
| Model: full enumeration to Configured, transport idle | 109,397 cycles (2.28 ms at 48 MHz), 26 transactions, every one through first time | 14,169 cycles (0.30 ms), same 26 |
| Clock offset / edges | ±1.5 % (the low-speed tolerance) | ±0.25 %, ±3 ns jitter, 2 ns duty-cycle distortion, the host's shortest gaps (2 bit times) |
| Turnaround (spec 2-6.5 bit times) | 2.12-2.19 bit times | 3.10-4.16 bit times |
| SET_ADDRESS's status ACK lost, then an IN first (the address is committed on it) | 2.16 bit times | 4.67 bit times = 18.7 clocks (limit 26; was 27.4-28.2 before `enum-ta`); static checker: 10-21 clocks, PASS |
| Lost ACKs (both directions), damaged DATA, bus resets mid-enumeration, 9 unsupported requests STALLed, wLength 0-255, bad CRC5/CRC16, other addresses | model | model |
| RTL (cocotb, Icarus): the flow with faults and a mid-enumeration reset | `test_usb_autonomous`: PASS, turnaround 2.16 bit times | `test_usb_autonomous_fs`: PASS, 3.25-4.75 (4.75: the IN after a lost status ACK) |
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
answer again). That commit is three instructions inline on the token path
and needs no `prep`: r1 is DATA1 while the address is pending and DATA1
after it (no transfer), so an IN that is that first token is answered within
the turnaround ([below](#the-lost-status-ack-and-an-in-first)).

### The lost status ACK and an IN first

The static timing checker ([timing-check.md](timing-check.md)) found a real
full-speed bug here. Until branch `enum-ta`, the commit on the first token
to the new address was `call commit`, which fell into `prep` and returned:
11 instructions between the token's end and the IN's WAIT. If that token is
an IN (SET_ADDRESS's status ACK lost, and the host's next transaction to the
new address an IN rather than a SETUP), the answer started up to 30 clocks
after the host's EOP (checker, pads) and 27.4–28.2 clocks on the model's
bus, over the 26 clocks (6.5 bit times) USB 2.0 §7.1.18.1 allows. Low speed
has 8 times the slack and was fine.

Two fixes were considered: NAK that IN at once, commit, and serve the retry
(the host retries a NAKed IN; it costs a NAK path and a flag), or take the
commit off the path. The second is smaller and answers the IN itself:

* `prep` was redundant on the commit: while F_ADDR is set r1 is always
  DATA1 (written by `prep` after SET_ADDRESS's ACK and after the status
  ZLP; `addr_filter` borrows r1 but the `prep` after it restores it before
  any token is handled), and after the commit the answer is the
  no-transfer zero-length DATA1 (r2 = dev, bit 3 clear): the same word. So
  `commit` no longer falls into `prep`; at `tok_end` it is inline
  (`mov r3, r2`, `flag clr F_ADDR`, `ldi r2, dev`), and `ack_addr` is the
  same three plus `jmp idle`.
* The one way r1 could change while F_ADDR was set was a SETUP's DATA
  (`d_cap` overwrites r1 and r2) that then failed its CRC or length check:
  F_ADDR stayed set until a good SETUP. A SETUP token now clears F_ADDR
  itself (a SETUP abandons any control transfer, SET_ADDRESS's included),
  and the clear in the request decoder, now redundant, is gone.

Toggles and the status stage are unchanged: the status ZLP still goes out
as DATA1 with the old address in force, the commit still happens on the
host's ACK or on the first token to the new address, and that token is
served exactly as before, only sooner: a SETUP as a SETUP, an IN with a
zero-length DATA1 (what the device answers any IN with no transfer under
way). Net cost: one word (241; 246 since the ACK tail guards of
[usb-full-speed.md](usb-full-speed.md#partial-ack-data-and-immediate-retry)).

| Path (full speed, pads) | Before | After |
|---|---|---|
| IN → answer (`in_ta`) | 10–15 clk | 10–15 clk |
| IN → answer incl. the lost-ACK commit (`in_ta_any`) | 10–30 clk, FAIL | 10–21 clk, PASS |
| the forced case on the model's bus (5 sessions, jitter, ±0.25 %) | 27.4–28.2 clk | 18.4–19.0 clk |

### Cycle budget

Measured on the model (clocks at 48 MHz, `test/model` profiling of core 0's
PC over a full enumeration with a lost ACK):

| Path | Clocks | Limit at full speed |
|---|---|---|
| token's end → the IN's answer queued (incl. the 4-tick turnaround delay) | 9 | 26 (6.5 bit times) |
| the same, the first token to the new address after a lost status ACK (inline commit) | 15 | 26 |
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
* **One link register**: a subroutine can't call another; `prep` and
  `addr_filter` are leaves, and `commit` is inline where it is timed.
* **The line unit's receive queue has no frame boundaries**, and the firmware
  can't flush it (LINECFG is host-only). A frame's end must be consumed before
  the next frame starts, or the next frame's bits are read as the old one's.
  Every path consumes to the frame's end; the host-ACK path does so before its
  processing.

## Tests

`test/model/test_usb_autonomous.py` (model, both speeds; 65 tests in the
results below, 71 now):
enumeration with clock offsets and jitter (every transaction through first
time), the Linux flow with a 10 ms reset, wLength 0-255 on both descriptors,
each fault kind sprinkled randomly over the flow (3 seeds × 3 kinds × 2
speeds), SET_ADDRESS's status ACK lost (then a SETUP first, or an IN first:
`test_set_address_status_ack_lost_then_in_first` asserts the IN's answer within
6.5 bit times, ≤ 26 clocks at full speed, and enumeration to Configured
afterwards), a repeated and an abandoned
SET_ADDRESS, bus resets in the data stage / after the address / after
configuration (then enumeration at a new address), a 3 µs reset (just over
the 2.5 µs threshold) unconfiguring the device, 9 unsupported requests
STALLed and cleared by the next SETUP (including OUT data of an unsupported
control write), tokens to other addresses/endpoints, a bad CRC5 and a bad
CRC16 ignored, and sigrok's decode of a whole enumeration with faults.

`test/test.py::test_usb_autonomous` and `test_usb_autonomous_fs` (RTL):
load over SPI, then the whole flow with a lost SETUP ACK, SET_ADDRESS's
status ACK lost followed by an IN to the new address, a lost host ACK,
damaged DATA, a STALLed string descriptor and SET_FEATURE, a bus reset in the
middle of a configuration read, enumeration again at a new address with
SET_ADDRESS's status ACK lost; the pads show no host port activity from the
load to the end; then the host reads address 9 and Configured.
`test/test_sigrok.py::sigrok_usb_autonomous` and `_fs` run those tests and
check that sigrok decodes exactly the packets the host model recorded.

Results (2026-10-07, branch `enum-ta`, after the turnaround fix):

```
pytest test/model/test_usb_autonomous.py          65 passed
  low {'ppm': 0}: configured in 109414 cycles (2.28 ms at 48 MHz), 26 transactions, turnaround 2.16-2.16 bit times
  low {'ppm': 15000}: ... 110493 cycles, turnaround 2.12-2.16;  {'ppm': -15000}: 108332 cycles, 2.16-2.19
  full {'ppm': 0}: configured in 14169 cycles (0.30 ms at 48 MHz), 26 transactions, turnaround 3.17-4.09 bit times
  full {'ppm': 2500, 'jitter_ns': 3}: 14138 cycles, 3.12-4.10;  {'ppm': -2500, 'jitter_ns': 3, 'dcd_ns': 2}: 14227 cycles, 3.10-4.16
  low: lost status ACK + IN first: answered 2.16 bit times after the EOP; configured at 77
  full: lost status ACK + IN first: answered 4.67 bit times (18.7 clocks) after the EOP; configured at 77
    (the v2 firmware fails this test: 6.92 bit times, 27.7 clocks)
  sigrok (low speed): 81 packets, identical to the host model's, no errors
  sigrok (full speed): 81 packets, identical to the host model's, no errors
pytest test/model/test_timing_check.py            usb_enum: in_ta_any PASS, 10-21 clk (was FAIL, 10-30);
  the forced lost-ACK IN on the bus: 18.65, 18.77, 18.36, 19.02, 18.93 clocks (5 sessions); none later than 26
pytest test/model                                  355 passed (whole suite, on v2 c49f973)

make COCOTB_TEST_FILTER=test_usb_autonomous        (RTL, Icarus; run before the rebase onto c49f973,
                                                    which changed no firmware words and no RTL)
  autonomous USB (low speed): 47 transactions, 256640 cycles (5.3 ms), turnaround 2.16-2.16 bit times
    (the IN after a lost status ACK: 2.16), host port idle throughout; state {'address': 9, 'configured': True, ...}
  autonomous USB (full speed): 47 transactions, 78487 cycles (1.6 ms), turnaround 3.25-4.75 bit times
    (the IN after a lost status ACK: 4.75 = 19 clocks), host port idle throughout; state {'address': 9, 'configured': True, ...}
  TESTS=2 PASS=2 FAIL=0
make COCOTB_TEST_MODULES=test_sigrok COCOTB_TEST_FILTER=sigrok_usb_autonomous
  sigrok usb autonomous (low-speed): 139 packets, identical to the host model's, no errors
  sigrok usb autonomous (full-speed): 139 packets, identical to the host model's, no errors
  TESTS=2 PASS=2 FAIL=0
```

Gate level was not run on this branch. It has run since: run fo6's release
receipts include `test_usb_autonomous` and `test_usb_autonomous_fs` on the
netlist with SDF at typ, slow and fast at 20.834 ns (48 MHz), validation root
`f1cb7c9` and again `7685aba`, whose `usb_enum.pasm` is the current one
([release-evidence.md](release-evidence.md)).

## On the KV260, with a replayed host (2026-10-14)

`fpga/kv260/host/usb_replay_demo.py` ([fpga/kv260/README.md](../fpga/kv260/README.md#usb-enumeration-with-a-replayed-host-no-pc-2026-10-14-hostusb_replay_demopy))
runs usb_enum on the FPGA build of the chip RTL at 48 MHz with no PC: each
scenario above first runs on the cycle model against the host model, and
pe_lab's pattern generator then plays that host's line levels on the D+/D-
pads at the exact clocks, segment by segment, while core 1 records the pads
(la_capture at low speed; a sampler taking the pads every clock at full
speed). Results (2026-10-14 08:55 UTC): 7 scenarios at each speed (the
standard sequence, Linux's with a 10 ms reset, lost and damaged handshakes,
bus resets at six phases and a 3 µs one, 15 ACK variants incl. dribble and
malformed, 9 STALLed requests, bad CRCs and wLength 0-255, SET_ADDRESS's lost
status ACK with an IN first) plus `usb_ls_dev` / `usb_fs_dev` with their
device software: **every device edge at the clock the model gives** (0 clocks
difference on all 32,880 device edges, 16,440 per speed), every device packet byte for byte,
the end states equal, and sigrok's decode of the board's edges equal to the
host model's packet record. (The model runs here with its falling-edge pad
sample equal to the clock-edge-launched pads, as on the FPGA; with its default
asynchronous edge the full-speed answers come one clock later.) Turnaround on the pads: 2.16 bit times (low
speed), 3.25-4.00 (full speed; 4.75 for the IN that commits a new address).
This is the RTL on FPGA fabric with digital pad levels: no transceiver, no
analogue receiver, no real host controller.

## What remains

* **Data endpoints.** An interrupt IN endpoint (HID, or vendor) needs an
  endpoint descriptor (7 bytes: 4 table words, wTotalLength 25), a second
  token filter (the endpoint field and so the CRC5 differ; no register is
  free to hold it, so it would be recomputed per token or kept elsewhere), its
  own toggle (a flag), and a data source: core 1 through virtual pins, or the
  host through the TX FIFO, which brings the host back for data, though not
  for enumeration. The descriptor (8 table words) fits in the 10 spare words; the token
  dispatch and data path need room made first (candidates: GET_CONFIGURATION,
  6 words; the interface/endpoint GET_STATUS range, 2; the SETUP length check,
  3).
* **String descriptors** cost one word per byte (UTF-16LE: two bytes per
  character), so a product name of 8 characters is ~20 words: over budget
  today.
* **Suspend/resume** (3 ms idle): not detected; the device keeps its state.
* **Real hosts.** Both speeds passed against Linux on the KV260 (2026-10-30,
  Linux xHCI through the board's USB5744 hub; the chip's RTL in fabric, not
  silicon: [fpga/kv260/README.md](../fpga/kv260/README.md)). Low speed: 18 of
  18 attaches, 10,000 of 10,000 control transfers, 58 of 58 bus resets. Full
  speed: 14 of 14 attaches, 56,000 of 56,000, 130 of 130. Windows and macOS
  have not been tried. Requests outside the list above are STALLed,
  which hosts accept (Windows' 0xEE string, the device qualifier); SET_INTERFACE
  is STALLed too, which Linux tolerates for single-setting interfaces.
* **Full speed on silicon** needs the chip at exactly 48 MHz and the line
  unit's receive jitter margin (docs/usb-full-speed.md: v2's half-cycle NRZI
  sample point); none of that changes here.
