# Sparse UART messages over USB EP0

This optional firmware pair delivers one to three UART bytes in each four-byte
USB control response. It uses both existing engines and adds no RTL. A finite
model scenario passed on the two-core tapeout profile. A second, pins-only
scenario passed on the RTL top level and matched the model clock for clock
([RTL simulation](#rtl-simulation)). This pair has no FPGA or silicon
qualification.

Use the [fixed four-byte example](usb-uart-overlap4.md) when partial groups are
unnecessary. This version spends one wire byte on a payload count and adds an
idle timer, so a short message can become available without filling a group.

## Wire format and sender contract

Vendor/device IN request 2 (`c002000000000400`) requests exactly four bytes:
`[b0, b1, b2, n]`, where `n` is 1, 2 or 3. The first `n` bytes are payload;
unused positions through byte 2 are zero. A zero payload byte is meaningful.
Other requested lengths are refused, and request 1 is not implemented by this
image. The device has EP0 only and retains its 18-byte device and configuration
descriptors.

Core 1 publishes three good UART bytes immediately, or seals a nonempty partial
group after its idle timer. Empty timeouts close and reopen READY without
publishing. UART RX is pin 16, READY is pin 8 (`uo[0]`, active high), and ERROR
is pin 9 (`uo[1]`, sticky after a bad STOP). The tested source is nominal
115200 baud, 8N1 at a 48 MHz engine clock, using complete 4167-clock frames.
The sender must obtain a fresh physical READY grant and finish the whole frame;
it must not start while READY is low. READY closes before a final synchronized
START recheck, allowing an already admitted frame to finish before sealing.
The test covers five specific closing phases, not all baud, jitter or phase
combinations.

The producer retains payload and count until a valid full-report DATA ACK.
It can hold three further good bytes while the old report waits for ACK. A bad
STOP discards only that incoming byte. A sealed pending group keeps READY low
until the old report releases, then becomes the next retained report. The
tested USB detector resets, configuration changes and SETUP traffic preserve
this ownership. Cold reload is destructive.

A request received before a report is available selects STALL. Repeated IN
tokens do not reconsider availability; the host must issue a fresh SETUP.
Publication latency therefore does not bound USB delivery latency. This
availability rule is source-derived and is not an additional tested polling
policy. A completed status handshake alone does not establish that the earlier
report DATA ACK was accepted. The count byte is not a sequence number; this
example does not provide an application-level exactly-once delivery guarantee
across fresh requests or resets.

On a PC, [`pe.usb_uart_reports`](../tools/pe/usb_uart_reports.py) polls this
request through pyusb and returns the first `n` bytes of each report; it
rejects a count outside 1–3 or nonzero padding. pyusb is needed only to open
the device:

```sh
PYTHONPATH=tools python3 -m pe.usb_uart_reports envelope3
```

[`test_usb_uart_reports.py`](../test/model/test_usb_uart_reports.py) reads
`39d38103` and, after the idle timer, `00a50002` from the model through the
same code. No PC has read a report from silicon.

## Loading and resource cost

The [cold-loading helper](../tools/pe/usb_uart_envelope3.py) provides
`assemble()`, `load_writes()` and `vendor_request()`. Apply the entire ordinary
host-write sequence before starting protocol traffic. It stops both engines,
sets ownership and state, loads both banks, and starts both engines together.
There is no runtime loading-host relay or FIFO service in the demonstrated
scenario; the external USB host still performs the control transfers.

The [USB consumer](../firmware/usb_uart_envelope3.pasm) uses 256 words and the
[UART producer](../firmware/uart_rx_envelope3.pasm) uses 112 words: 1472 bytes
and 393 cold writes in total. The consumer fills its bank. Both cores use
divider 256; USB low speed uses 32 clocks per bit. Producer CRCCTRL must be
`0x000f0000` for 16-bit scratch with both taps off, and SHIFTCTRL must be 3
with autopush/autopull off. The helper configures these values and synchronized
inputs on the two-core, two-bank, 256-word, eight-entry-FIFO profile.

The pair reserves both engines, all eight flags, virtual pins 24–31, USB pins
0/1, and the RX/READY/ERROR pins. It also uses the line unit and CRC resources.
Capacity is at most three retained plus three pending payload bytes. No SRAM
or additional engine is added. Each four-byte report carries at most three
payload bytes; the count and any padding remain charged transport overhead.

## Verification and limits

Run the [maintained model test](../test/model/test_usb_uart_envelope3.py) from
the repository root:

```sh
PYTHONPATH=tools python3 -B -m pytest -q test/model/test_usb_uart_envelope3.py
```

Ordinary pytest uses a fresh `tmp_path` for the raw trace. An optional nonempty
`WIRELINGO_EVIDENCE` selects a fresh output directory instead. The test fixes
every USB and UART input time before constructing the model; observed replies,
READY, flags and registers cannot move stimulus dates or introduce a retry.

The October 14 scenario passed 620,000 model clocks in 59.23 seconds of pytest
time (60.15 seconds including the launcher), using the two-core tapeout profile.
It recorded 179 USB packets and nine UART frames, including one bad STOP, and
delivered four reports: `00000001`, `00a50002`, `39d38103`, `12340002`. These
contain eight payload bytes, four padding bytes and four count bytes. Deliberate
idle and retry intervals make this a correctness test, not a throughput result.

The trace checks lost and malformed ACK retention, partial and full pending
groups, detector reset and re-enumeration, sticky ERROR, empty timeouts, and
five admitted START phases near READY closure. It observes the selected
66-clock supplier delay and verifies that the next word arrives before the
declared queue-empty bound. It also separates an ordinary descriptor ACK at
cursor 2083 from an adversarial extra empty-DATA ACK at cursor 37: neither
releases the UART report. Four genuine report releases use the full cursor
4133. This extra transaction is a discriminating test, not a required host
transaction or a general USB conformance claim.

The saved-trace checker rejected three deliberate corruptions: a wrong count
with recomputed CRC, a removed legal late-START observation, and the alias ACK
incorrectly clearing report ownership. These are oracle controls, not mutated
RTL tests. The run recorded no active loading-host calls or bus strobes and no
USB driver overlap. Its 558 line-RX overflow events are the device's
[own echo](usb-uart-overlap4.md#line-rx-overflow-is-the-devices-own-echo): 10 descriptor replies drop 46 bits each and 7 reports 14 each. The test
asserts that every dropped bit belongs to a device-started receiver frame and
that every peer packet starts on an empty, idle receiver.

Detector reset uses 144 SE0 clocks plus 4800 recovery clocks; it does not qualify
normative or electrical reset timing. Deliberately delayed retry/ACK intervals
stress retention rather than establish general ACK timing compliance. Arbitrary
core gating, global reset, preemption, faults, active loader service, aborted
internal supplier traversals, sustained rate and physical I/O timing remain
outside this result. The baud and rate envelope measured for the fixed
four-byte pair does not transfer: this producer polls START every 26 clocks.

## RTL simulation

[`test_envelope3_rtl`](../test/test_usb_uart_rtl.py) runs the harness
described in the [fixed four-byte guide](usb-uart-overlap4.md#rtl-simulation)
on the RTL top level: the helper's 393 writes through the SPI port (15
transactions, 25,880 clocks at SCK = clk/2), then a host port checked idle
on the pads. A READY-gated ideal 115,200-baud sender and the `ControlHost`
low-speed model run the [scenario](../test/model/usb_uart_pairs.py) at
address 0 with request 2. The time origin is the first clock READY is high,
30 clocks after the load.

In 172,739 clocks (3.60 ms) the scenario does the following:

* An empty request is STALLed.
* Two full three-byte groups arrive while the host is idle. The retained
  first group and the pending second one hold the sender's next frame for
  13,033 clocks.
* Greedy reads fetch both groups. The second is fetched with an
  in-transfer IN retry that returns the identical DATA packet.
* A group of `12`, a bad-STOP `99` and `34` is sealed as two bytes by the
  idle timer. The host's repeated requests are STALLed until then.
* One byte `00` is sealed the same way.
* A final request is STALLed.

10 frames carry 9 good bytes. The host makes 18 requests (14 STALLed) in 108
USB packets. The test checks the following against expectations computed
from the sender's byte list:

* The reports are exactly `a55aff03`, `7e810103`, `12340002` and `00000001`,
  in order, with zero padding and the right counts.
* No request is STALLed once a report is due: 100 clocks after a full
  group's last STOP ends, or 48,500 clocks after a partial group's (the
  timer is 1,846 polls of 26 clocks).
* Every frame starts while READY is high, on a fresh rise. READY falls 7–29
  clocks after each START.
* ERROR rises once, at clock 47,170, inside the bad frame's STOP.
* READY ends the hold 608 clocks after the releasing ACK begins.
* F7 is clear at the end.

The same scenario on the cycle model gives identical records: request
outcomes, reports, every packet with its first and last clock, each frame's
START and grant clock, and every edge of RX, READY, ERROR, D+ and D-.
sigrok's decoders on the saved RTL pins agree: 10 bytes with one framing
error, and 108 packets without errors. This is RTL simulation with ideal
edges at one-clock resolution. It does not cover detector reset,
malformed ACKs, START phases near READY closure, baud error, gate-level
timing or electrical USB. Reproduce with
`make COCOTB_TEST_MODULES=test_usb_uart_rtl COCOTB_TEST_FILTER=test_envelope3_rtl`
in `test/`.
