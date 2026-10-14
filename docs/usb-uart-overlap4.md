# Receive the next UART report while USB retains the current report

This optional program pair serves four-byte reports through low-speed USB EP0
while the second core receives the next four UART bytes. It uses the existing
instruction set and two cores. READY throttles the UART sender when both the
retained report and pending group are full.

The finite model regression passed with the two-core tapeout profile. It
observes UART samples during an active USB DATA reply and checks partial,
bad-byte rollback and fully pending groups at deliberately chosen ACK crossings.
Sustained forwarding and native RTL behavior remain unmeasured for this pair.

## Load and communicate

The [cold-load helper](../tools/pe/usb_uart_overlap4.py) provides `assemble()`,
`load_writes()` and `vendor_request()`. Start with both cores stopped and their
FIFOs empty, then apply the helper's 374 register/memory writes. The plan loads
both programs, sets exclusive pin/flag ownership and starts both cores. This
count is host operations; serial loading latency has not been measured.

Send UART at nominal 115,200 baud, 8N1, with a 48 MHz system clock. Admit one
complete frame for each new active-high READY grant. Complete its STOP interval
even if the next READY grant appears early. A bad STOP discards that byte and
sets sticky ERROR while preserving earlier good bytes. Partial groups have no
idle timeout or short-report flush.

The USB host issues the vendor/device IN request `c0 01 00 00 00 00 04 00`.
Exactly four good bytes form a report. Polling and valid ACKs remain the USB
host's responsibility. There is no active loading-host FIFO relay, refill or
rearm API, and no claim of an autonomous external host.

## Resource and state contract

| Resource | Reservation |
|---|---|
| Core 0 / bank 0 | 256 words / 1,024 bytes; low-speed USB EP0; zero spare words |
| Core 1 / bank 1 | 93 words / 372 bytes; cooperative UART receiver and byte supplier |
| Physical pins | USB 0/1 on core 0; RX 16, READY 8 and ERROR 9 on core 1 |
| Virtual pins and flags | Pins 24–31, F0–F7 reserved by this pair |
| Profile | Two cores, two 256-word banks, depth-8 FIFOs; no active FIFO relay |
| Cadence | Both encoded dividers 256: one instruction tick per system clock |
| Line configuration | 32 clocks per low-speed bit, TURN 63, SYNC_BYPASS 0 |
| Producer CRC | CRCCTRL `0x000f0000`: 16-bit scratch, both taps off |
| Producer shifts | SHIFTCTRL 3: right shifts, no autopush or autopull |

The images total 1,396 bytes. Relative to the separate 59-word serial supplier,
this producer adds 34 words and one cold CRC configuration write, and executes
more often. No additional RTL or SRAM is needed. Power and physical effects of
the higher activity are unmeasured.

R1/R2 retain the current report. ISR accumulates up to four subsequent good
bytes. The service subroutine temporarily uses CRC to preserve the UART delay
register; CRC cannot also hold a useful checksum. Its default one-bit width
would corrupt that saved register, so the supplied configuration is mandatory.

F6 announces one internal byte. Core 0 clears it before capture; core 1 waits
for that clear before replacing the pins. The internal order is reversed bits
of report bytes 1, 0, 3, 2; USB reconstructs their normal external order. F7 is
set only after the promoted report and first internal byte are ready. A valid
fixed-length DATA ACK releases the old report before status completion. This
is not an application exactly-once delivery guarantee.

The producer preserves a partial next group across old-report ACKs and removes
only a bad-STOP byte. A fully pending report is promoted after the retained one
is released. It does not reset on USB detector reset. Global reset, reload,
preemption, runtime configuration writes and arbitrary aborted internal reads
are outside this finite contract. These shared-pin transactions are firmware
coordination, not an atomic hardware mailbox.

## Measured finite model result

On October 14, 2026, the sole C32 invocation passed in **310,000 model steps**,
including 374 cold writes, in 27.97 seconds wall time. The same ordinary test
node passed its saved-trace audit; three saved-only negative controls rejected
a changed payload with valid CRC, missing phase coverage and late publication.
These negatives are checks of the saved-data oracle, not additional DUT runs.

The fixed external calendar contains 17 UART frames: 16 good bytes and one
bad-STOP `a6`. It delivers the four exact USB reports `00000000`, `39d3815e`,
`12345678` and `9abcdef0`, retaining earlier reports through the declared
lost/malformed-ACK retries. An empty final four-byte request returns STALL.
The trace records 99 USB transmissions: 98 complete packets and one deliberate
ACK with a one-bit tail. Independent review checked 33 DATA CRCs and all 136
UART data-sample windows. Loading-host activity and USB driver overlap are zero
after startup.

| Required crossing | Observed model behavior |
|---|---|
| Consumer clear and producer old-high poll at clock 150592 | Next publication at 150658: 66 clocks; next payload OUT meets the frozen queue deadline |
| USB DATA versus UART receive | Actual UART IN1 samples at 150636, 151053 and 151470 occur during the first USB DATA reply |
| Old report ACK during UART delay at 158241 | PC stays 280, delay count 19→18, ISR stays `0xa6720000` |
| ACK during bad-byte rollback at 203989 | Earlier `12 34` prefix survives; bad `a6` is discarded and ERROR stays set |
| ACK while the next full group is pending | Clear at 260541; guarded promotion sets F7 at 260566; READY rises at 260594 |

The four valid report ACKs start 64 clocks after the DATA packet's first EOP J,
or 32 clocks after that complete J cell. The distinction is explicit in the
raw timing checks. The maximum observed clear-to-publication delay is a finite
66-clock witness, not a universal timing proof.

**282 line-RX overflow events remain recorded**, observed during device
transmission. Their cause and general recovery behavior are unproven. This
passing case does not establish that overflows are harmless.

The previous C30 run reached the same final clock but failed in its observer:
only executable instructions were logged, so a required ACK edge during RET
delay was absent. Its later pin expectation also selected the next internal
byte instead of the first. C32 adds a passive edge snapshot and corrects that
expectation from `0x59` to `0x3d`. All 31 other original raw fields match C30;
firmware, helper, inputs and schedules are unchanged. The original failure is
preserved, and was not reclassified as a passing run.

## Complete host-service workload

The separate [complete-service regression](../test/model/test_usb_uart_overlap4_service.py)
passed on October 14 in one 270,000-step model invocation, with the same two-core
profile and unchanged firmware. It admits 32 complete UART frames at fixed
4,167-clock intervals and delivers eight four-byte reports. Each USB read pays
for all nine packets of its control transfer. One read deliberately loses its
first DATA ACK, receives the identical report again, then completes status;
that read uses eleven packets. No loading-host service follows the cold load.

| Measurement | Model result at 48 MHz |
|---|---|
| UART source window | 32 bytes in 133,344 clocks: 11,519.08 B/s offered |
| Ordinary complete read | 12,111–12,303 clocks |
| Read with the lost-ACK retry | 15,476 clocks; retry adds 3,365 clocks |
| Smallest margin before the next service deadline | 1,192 clocks / 24.83 microseconds |
| First UART START through final status service | 145,519 clocks: 10,555.32 B/s for this finite burst |
| Maximum queued good data | Seven bytes; no two-full-group READY hold occurred |

Service duration includes the final status ACK's complete EOP and a further
64-clock idle gap. The final ACK ends at clock 265,455 and service ends at
265,519; the test drains to its fixed final clock 270,000. Cold setup and
enumeration precede the UART source window. The 374 direct-model loading
writes still do not measure serial loading latency.

Independent saved-trace review decoded all 125 packets, including 74 report
packets, checked 42 DATA CRCs and 42 token CRCs, and sampled all 32 UART frames.
Three saved-only negative controls reject a changed report with valid CRC,
a missing READY grant and a missing retry. The trace preserves 310 line-RX
overflow observations. This test does not prove their general recovery safe.

This is a finite, scheduled external-host result. It does not establish
unbounded streaming, arbitrary host pauses or keepalive handling. UART STARTs
must receive fresh READY grants; the measured absence of full-buffer pauses
is not a test of host blackout tolerance. The external USB host's CPU cost and
native RTL or board throughput remain unmeasured.

## Remaining validation

This model pass does not establish sustained 115,200-baud forwarding.
The overlap schedule has planned idle periods; the separate complete-service
case measures one finite near-contiguous source burst with scheduled USB service.
Longer streams, host blackouts and idle partial groups remain separate work.
Native RTL, board/electrical USB behavior,
broad baud/jitter tolerance, power and general reset recovery remain unqualified.
The combined software/model suite passed all 1,194 tests across 52 modules at
`85991cdd`. The new complete-service node passed separately on its pinned inputs;
these are distinct qualifications, not a combined 1,195-test run.

## Reproduce the focused test

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tools /usr/bin/python3 -B -m pytest \
  -q -x -p no:cacheprovider \
  test/model/test_usb_uart_overlap4.py::test_overlap4_retained_and_next
```

The test uses pytest's fresh `tmp_path`, or a fresh explicit `WIRELINGO_EVIDENCE`
directory, and refuses to overwrite `overlap4-raw.json`. This command does not
itself impose external wall or address-space caps. The coordinated C32 run is
limited to one invocation, 120 seconds, 2 GiB, 400,000 aggregate model steps and
five seconds of termination grace. Collection alone is not a behavioral result.

Run the separate complete-service case with the same command prefix and node
`test/model/test_usb_uart_overlap4_service.py::test_overlap4_complete_service`.
Its coordinated C33 invocation used the same caps and no additional model run
for the saved-trace checks.
