# One retained four-byte UART report over USB EP0

This optional firmware receives four UART bytes and returns the retained report
through a vendor request on low-speed USB EP0. It uses two existing cores and
banks. The sender must honor READY; acquisition pauses until the report is
acknowledged. This is a model-tested workload, not continuous UART forwarding.
The [one-byte mode](usb-uart-byte.md) remains a separate program.

## Load and use

The [cold-load helper](../tools/pe/usb_uart_batch4.py) exposes `assemble()`,
`load_writes()` and `vendor_request()`. Apply its 339 ordinary register/memory
writes for this mode with both cores disabled. Loading replaces both programs
and reserves the resources below, then starts both cores once. The count is
host operations, not a measurement of serial loading time.

The default request is `c0 01 00 00 00 00 04 00`. Exactly four good bytes must
be available; other wLength values are refused, not clamped. Partial groups
have no idle timeout flush. The USB host sends normal control-transfer packets;
the loading host does no active API calls, FIFO service or rearming in the test.
There are no noncontrol endpoints or generated embedded bundles for this mode.

| Resource | Reservation |
|---|---|
| Core 0 / bank 0 | 256 words / 1,024 bytes; autonomous low-speed EP0; no headroom |
| Core 1 / bank 1 | 59 words / 236 bytes; partial group in ISR/OSR, retained halves in R1/R2 |
| Physical pins | USB 0/1 on core 0; RX 16, READY 8, ERROR 9 on core 1 |
| Virtual pins | 24–31 owned by core 1; core 0 reads from pin 24 |
| Flags | F0–F5 USB; F6 byte ready; F7 report valid |
| Profile | Two cores, two banks, 256 words per bank, FIFO depth 8; no active FIFO relay |
| Core cadence | Encoded dividers 256 and 3333: 1 and 3333/256 system clocks per tick |
| Tested source | Nominal 48 MHz system clock, 115,200 baud, 8N1; 1250/3 clocks per bit |
| USB line | 32 clocks per low-speed bit, TURN 63, synchronized inputs / SYNC_BYPASS 0 |

The images total 1,260 bytes. ERROR consumes one additional physical output
compared with the one-byte mode. RTL is unchanged; power, area and maximum
clock rate are not measured by this experiment.

## Ownership and lifetime

Active-high READY grants one complete UART frame. Finish the admitted frame
even if the next grant arrives during its STOP bit. The test waits eight clocks
after observing a new grant and completing the previous frame. Later groups
can wait for planned USB work before using an already granted high level.
Internal registers and flags do not control the external sender or its bytes.

A good STOP advances the group. A bad STOP discards the suspect byte, restores
the prior good bytes and sets sticky ERROR. ERROR is separate from the USB
payload and clears only on cold restart. Four good bytes are retained in R1/R2;
F7 is set after the first byte and F6 are published. Core 0 clears F6 with WAIT
before IN8; core 1 observes the clear before changing the virtual pins. This
requires uninterrupted core 0 execution at one clock per tick and exclusive
pin/flag ownership; it is not an atomic hardware mailbox operation.

The valid ACK/F_SENT check at exact cursor 4133 clears F7. The tested report
survives lost/malformed ACKs, retries, descriptor SETUP, deconfiguration, a
non-four-byte length request and USB detector reset. Every started report reads
two complete 16-bit words. The cyclic supplier checks F7 before F6 to escape a
speculative next byte after consumption; retries do not set F7 again. Release
occurs at DATA ACK, before status completion, so application exactly-once
delivery is not promised.

These rules require fixed configuration and eventual completion of all four
internal byte reads. Global reset, reload, gating, faults, preemption and abort
after only one to three supplier reads are outside this contract. The inherited
ACK guard does not distinguish every shared line-end/error cause or qualify all
malformed endings. Detector reset in the test is not electrical USB reset
qualification.

## Measured finite model result

On October 13, 2026, the single test passed on its sole invocation: **376,538
model steps**, including loading, in 33.63 seconds wall time. It used the
2/2/256/depth-8 profile above. Its raw trace contains 192 USB packets, 126
external USB-host transactions and 11 UART frames. Independent saved-trace
review decoded all packets, checked 63 DATA CRCs and checked 110 UART symbols.
No synthetic mutation controls were run for this experiment.

The test retained `00 00 00 00` through lost/malformed ACKs, descriptor and
configuration traffic, a refused length and reset/re-enumeration. A valid
final-bit dribble ACK consumed it. The second report, `39 d3 81 5e`, excluded a
bad-STOP `a6` inserted after the first two bytes; ERROR remained set through
subsequent descriptor/reset traffic. An ordinary ACK consumed that report.
The final partial group `12 34` remained unavailable after 5,000 idle clocks.

All three external IN times were fixed before execution. The trace observed
the targeted same-edge F6 clear and producer poll seeing the old high value,
at fractional divider state 236. No pilot run, phase search or retuning was
used. Let C be the final payload OUT of the first 16-bit word:

| Attempt | Clear to FLAG publication | Next payload OUT relative to C | Same-edge old-high poll |
|---|---:|---:|---|
| First all-zero reply | 53 clocks | C + 63 | Yes |
| Lost-ACK retry | 27 clocks | C + 37 | No |
| Malformed-ACK retry | 52 clocks | C + 62 | No |

In the first case, C=120270, F6 clear=120275, publication=120328 and the next
OUT=120333. This is 32 clocks before the conservative source-derived empty
queue decision at C+95. The raw packet remained complete with correct CRC.
This finite observation is distinct from the source arithmetic over all 256
fractional states and is not a general RTL timing proof.

Active loading API calls, loading bus strobes and external/device USB driver
overlap were all zero. The 636 line-RX overflow events are the device's
[own echo](usb-uart-overlap4.md#line-rx-overflow-is-the-devices-own-echo): 12 descriptor replies drop 46 bits each and 6 reports 14 each. The test
asserts that every dropped bit belongs to a device-started receiver frame and
that every peer packet starts on an empty, idle receiver.

Observed ACK EOP-J to next READY delays were 66 and 54 clocks. The scenario
includes enumeration, deliberate retries, resets and idle periods; its elapsed
clocks per byte are not sustained throughput. Single buffering still stops
UART admission while a report is retained. No native RTL, physical, board,
USB-conformance, broad baud/jitter or maximum-latency result is claimed.

## Reproduce

The maintained test uses pytest's `tmp_path` by default. A nonempty
`WIRELINGO_EVIDENCE` selects a fresh explicit output directory. From the repo:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tools /usr/bin/python3 -B -m pytest \
  -q -x -p no:cacheprovider test/model/test_usb_uart_batch4.py
```

The test saves raw pins and its oracle result, checks exact packet counts and
CRC/EOP, verifies READY/RX/ERROR behavior, and requires the 53-clock timing
witness. Collection alone does not validate behavior. The recorded run had a
120-second wall limit, 2 GiB address-space limit, one-million-step cap and
five-second termination grace; the plain command above does not impose the
external wall or memory limits.
