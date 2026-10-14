# Receive the next UART report while USB retains the current report

This optional program pair serves four-byte reports through low-speed USB EP0
while the second core receives the next four UART bytes. It uses the existing
instruction set and two cores. READY throttles the UART sender when both the
retained report and pending group are full.

Everything below is model evidence on the two-core tapeout profile. Native RTL,
FPGA and board behavior of this pair are unmeasured.

## Load and communicate

The [cold-load helper](../tools/pe/usb_uart_overlap4.py) provides `assemble()`,
`load_writes()` and `vendor_request()`. Start with both cores stopped and their
FIFOs empty, then apply the helper's 374 register/memory writes. The plan loads
both programs, sets exclusive pin/flag ownership and starts both cores. This
count is host operations; serial loading latency has not been measured.

Send UART 8N1 at 115,200 baud (supported range in the
[envelope](#supported-envelope)) with a 48 MHz system clock. Admit one
complete frame for each new active-high READY grant. Complete its STOP interval
even if the next READY grant appears early. A bad STOP discards that byte and
sets sticky ERROR while preserving earlier good bytes. Partial groups have no
idle timeout or short-report flush.

The USB host issues the vendor/device IN request `c0 01 00 00 00 00 04 00`.
Exactly four good bytes form a report; a request with no report ready is
answered with STALL, and the host must issue a fresh SETUP. Polling and valid
ACKs remain the USB host's responsibility. There is no loading-host FIFO relay,
refill or rearm API.

### Reading the reports on a PC

[`pe.usb_uart_reports`](../tools/pe/usb_uart_reports.py) issues this request
with pyusb, treats STALL as "no report yet" and decodes the replies. The
decoders and the polling loop take any object with pyusb's `ctrl_transfer`,
so pyusb is optional for tests and only needed to open the device.

```sh
PYTHONPATH=tools python3 -m pe.usb_uart_reports overlap4 --bytes 64
```

```python
from pe import usb_uart_reports as r
dev = r.open_device()                      # VID 0x1209, PID 0x5634
data = r.read_bytes(dev, "overlap4", 16)   # whole four-byte reports
```

[`test_usb_uart_reports.py`](../test/model/test_usb_uart_reports.py) answers
`ctrl_transfer` with the model's control transfers to this pair. No PC has
read a report from silicon.

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
more often. No additional RTL or SRAM is needed.

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
are outside this contract. These shared-pin transactions are firmware
coordination, not an atomic hardware mailbox.

## Supported envelope

[`test_usb_uart_overlap4_envelope.py`](../test/model/test_usb_uart_overlap4_envelope.py)
measures these limits with an ideal READY-gated sender (edges at whole clocks
nearest k·T) and the `ControlHost` model at address 0 with two-bit packet gaps.
Each limit is tested just inside (passes) and just outside (fails).

| Quantity | Supported in the model | Just outside |
|---|---|---|
| Sender baud | 109,050–121,059 (−5.34 % / +5.09 %), every START phase | 109,049: STOP read low, ERROR, byte dropped. 121,060: bit 7 read as 1, no ERROR |
| Forwarding rate | 115,200-baud line rate: a frame every 4,167 clocks, 11,519 B/s | — |
| Report release | Device accepts the DATA ACK at most 16,602 clocks (345.9 µs) after the report's last STOP ends | READY holds the sender: slower, nothing lost |

**Baud.** START is polled every 25 clocks, so the sample that sees it lies
d = 0–24 clocks after the edge. Data bit j and STOP (j = 8) are sampled
d + 625 + 417·j clocks after the edge. The range above holds for every d. The
slow side fails at d = 0 and the fast side at d = 24; the test runs exactly
those phases. It is a clock-error budget for a clean edge. Jitter, slow edges
and a real sender's divider rounding are not included and reduce it. Overspeed
fails silently.

**Rate.** The producer holds READY only while the retained report is
unreleased and the next four bytes are complete. After the release READY rises
41–65 clocks later (41–61 observed; the spread is one 25-clock poll). Line rate is therefore kept if
the device accepts each report's DATA ACK at least 66 clocks before the end of
the eighth following STOP. With back-to-back frames that is 16,602 clocks
after the report's own last STOP. A full nine-packet read takes 12,015–12,079
clocks, and the release comes 8,903–8,967 clocks after its SETUP starts (each
stuffed bit in the DATA packet adds 32). A STALLed attempt takes 6,826–6,986
clocks.

| Host in the test | Result |
|---|---|
| Repeats the request at once after each STALL, keep-alives on | 12 reports (48 bytes) at line rate, no READY hold |
| Starts each read 7,653 clocks after the report's last STOP | 26 reports (104 bytes) at line rate, no hold |
| Starts each read 7,654 clocks after it | 11 reports: one 19-clock hold after report 10's late release; all 44 bytes exact |

The lag edge depends on payload stuffing and phase; the release deadline does
not. At line rate the model host keeps the bus busy at least 72 % of the time
(12,015 of every 16,668 clocks). Real host controllers schedule control
transfers per 1 ms frame and were not modeled. At one report per millisecond
READY would throttle the sender to 4,000 B/s. That figure is arithmetic, not a
measurement.

## Line-RX overflow is the device's own echo

The line receiver listens on the pads its own transmitter drives, so it decodes
every packet the device sends. The consumer queues a whole reply with OUT LINE
before its `echo:` loop drains the receiver until the frame ends, as `usb_enum`
and `usb_ls_dev` do. The RX queue holds 32 bits. In every trace a reply of N
line bits drops N − 50 of its echo bits: 14 for a four-byte report (64 bits),
46 for an eight-byte descriptor packet (96 bits), none for handshakes or
replies up to two bytes. The sticky overflow bit is never read. The 282 events
of the regression are 7 × 14 + 4 × 46; the 310 of the service run are
9 × 14 + 4 × 46.

All six UART/USB pair tests (this pair, its service run, batch4, byte,
envelope3 and the peer-capture coexistence) now assert this through
[`line_echo.py`](../test/model/line_echo.py). Every dropped bit must belong to a
receiver frame that the device's own transmitter started. Every peer drive
must begin with an empty RX queue, no pending frame end and an idle receiver.
Peer frames peak at 16 queued bits. [`test_line_echo.py`](../test/model/test_line_echo.py)
shows the check is not vacuous: a receiver that is never drained loses peer
bits, and 16 undrained echo bits cost the next token its last 16 bits. Both
fail the check. The traces show no peer bit lost; this is not a proof over
all paths.

## Finite regressions

[`test_overlap4_retained_and_next`](../test/model/test_usb_uart_overlap4.py)
runs 310,000 clocks. Its fixed calendar sends 17 UART frames: 16 good bytes and
one bad-STOP `a6`. It delivers the reports `00000000`, `39d3815e`, `12345678`
and `9abcdef0`, retaining each through the declared lost or malformed ACK
retries. An empty final request returns STALL. The audit decodes every wire
packet and checks DATA CRCs and every UART sample window. Three saved-trace
mutations must be rejected: a changed payload with valid CRC, missing phase
coverage and a late publication.

| Required crossing | Observed model behavior |
|---|---|
| Consumer clear and producer old-high poll at clock 150592 | Next publication at 150658: 66 clocks; next payload OUT meets the queue deadline |
| USB DATA versus UART receive | UART samples at 150636, 151053 and 151470 fall inside the first USB DATA reply |
| Old report ACK during UART delay at 158241 | PC stays 280, delay count 19→18, ISR stays `0xa6720000` |
| ACK during bad-byte rollback at 203989 | Earlier `12 34` prefix survives; bad `a6` is discarded and ERROR stays set |
| ACK while the next full group is pending | Clear at 260541; guarded promotion sets F7 at 260566; READY rises at 260594 |

The largest F6 clear-to-publication delay observed in these regressions is 66
clocks. That is an observed maximum, not a timing proof.

[`test_overlap4_complete_service`](../test/model/test_usb_uart_overlap4_service.py)
enumerates, then admits 32 frames at fixed 4,167-clock intervals and reads
eight reports with complete control transfers. One read loses its first DATA
ACK and receives the identical report again. Ordinary reads take 12,111–12,303
clocks; the retry adds 3,365 clocks. The smallest slack before the next
scheduled read is 1,192 clocks. At most seven good bytes were queued. Three
saved-trace mutations must be rejected: a changed report with valid CRC, a
missing READY grant and a missing retry.

## Remaining validation

Native RTL, board and electrical USB behavior, real host scheduling, UART
jitter, host blackouts, power and general reset recovery remain unqualified.
The rate results above are finite runs of 12 to 26 reports.

## Reproduce

```sh
PYTHONPATH=tools python3 -m pytest -q test/model/test_usb_uart_overlap4.py \
  test/model/test_usb_uart_overlap4_service.py \
  test/model/test_usb_uart_overlap4_envelope.py test/model/test_line_echo.py
```

The two finite regressions write their raw trace to pytest's `tmp_path`, or to
a fresh `WIRELINGO_EVIDENCE` directory, and refuse to overwrite one.
