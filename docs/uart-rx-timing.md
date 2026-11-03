# UART RX timing and recovery regression

## Summary

**What it does.** Three optional one-core UART receivers, 8N1 at eight
execution ticks per bit, with a sticky F0 for a bad STOP:

* `firmware/uart_rx.pasm`: one byte per RX FIFO word, continuous, 14 words
  (13 before `fault-fixes`, which added a mid-bit recheck of the start bit:
  [fault-testing.md](fault-testing.md), finding 1).
* `firmware/uart_rx_packed.pasm`: four bytes per RX word, so the eight-word
  FIFO holds 32 bytes without service; 13 words.
* `firmware/uart_rx_capture.pasm`: a declared length of 1 to 32 bytes, then
  HALT; 19 words.

This page is the timing and recovery record of all three on the cycle model.

**How to use them.** Define RX (pin 16, `ui[0]`, in the tests) and set the
divider to `host.clkdiv_for(48e6, 8*115200)`, encoded `13333`. Drain words
from the RX FIFO: one byte each for `uart_rx`, four little-endian bytes each
for `uart_rx_packed`. For `uart_rx_capture`, use `pe.uartcapture`'s
`capture_plan` and `decode_capture` ([below](#paid-setup-and-completion-api),
and [uart-capture-host.md](uart-capture-host.md) for the serial host).

**Envelope.** On the cycle model at 48 MHz: true baud offsets of −3 %, 0 and
+3 % from 115200, at selected start phases, with ideal bits and with an
alternating ±2-clock bit jitter. The smallest data-sample margins, at +3 %,
are 57 clocks (`uart_rx`), 48 (`uart_rx_packed`) and 47 (`uart_rx_capture`).
These are selected points, not a continuous baud range, all phases, or
arbitrary jitter. `uart_rx` needs prompt service: in the tests the RX FIFO
never held more than one word. Once a full FIFO blocks the receiver, input
can be lost; no mode promises lossless input then. A bad STOP keeps its byte
and sets F0.

**Evidence.**

* Cycle model: `test/model/test_uart.py` (23 tests), `test_uart_packed.py`
  (10) and `test_uart_capture.py` (208), all in the model suite.
* `uart_rx` is also in the top-level RTL suite (`test_uart_rx` in
  `test/test.py`, nominal timing at 24 clocks per bit), which also runs on
  the gate-level netlist. The gate-level runs on record (fo6's receipts) used
  the 13-word firmware; the 14-word one has not run at gate level.
* KV260 FPGA build: `uart_rx` through a loopback from `uart_tx` and from a
  9600-baud line bit-banged on `ui[0]`; `uart_rx_packed` and
  `uart_rx_capture` with generator-played lines, the capture also at the
  model's ±3 % corners (fpga/kv260/README.md, "SPI and UART firmware on
  hardware"). Those runs used the 13-word `uart_rx` from before
  `fault-fixes`. The 14-word one has not run on the FPGA: the KV260 result
  applies to it only after a rerun there.
* Not covered: a real external UART, electrical timing, metastability, or
  the baud and jitter cases on RTL or at gate level.

## Byte receiver: `uart_rx`

The standalone `firmware/uart_rx.pasm` receives the selected back-to-back
8N1 pin streams without a firmware change. It already returns to the start
level-WAIT one tick after the STOP check. (An earlier, separate UART-to-SPI
bridge experiment had to shorten a four-instruction handoff after STOP; that
handoff is absent here.) The result justifies a maintained regression, not a
rewrite, a changed divider API or a hardware upgrade.

Run the tests from the checkout:

```sh
PYTHONPATH=tools python3 -m pytest -q test/model/test_uart.py
```

### Transmitter and service contract

The pin-timeline tests use a 48 MHz model clock, nominal 115200 baud,
and the existing eight-ticks-per-bit receiver divider: encoded `13333`, or
`52 + 21/256` clocks per tick. Each case offers `00 ff 55 aa 5a c3 19 e7`
as eight contiguous frames. True transmitter baud offsets are −3 %, nominal
and +3 %, with a bit period of `48000000 / (115200 * (1 + offset))` clocks.
Initial phase offsets are 0, 13 and 51 clocks after a fixed 1664-clock idle
interval measured from completed loading. These are three selected phases,
not an exhaustive divider-phase sweep.

All nine rate and phase combinations run both ideal bits and a deterministic
alternating +2/−2-clock duration jitter. Bit boundaries accumulate as exact
rational numbers, then quantize down to integer clocks. The jitter changes
individual bit durations while cancelling over each two-bit pair; it does
not represent arbitrary noise, clock wander or accumulated jitter.
The source is precomputed before enabling the core and advances solely by
actual model clock. It runs during host operations too. DUT PCs and
registers are observed only to record execution; they never schedule input
edges.

The fixture advances 64 clocks, then drains every available RX word through
the model host's snap/commit/cancel register-read operations. Those
operations also advance the source and are included in the clock count.
Checking the model queue for availability is an observation, not an
exercised SPI STATUS transaction. This is an explicit promptly-serviced RX
policy, not a real loading-host bandwidth benchmark or a host-free capture
claim. Every case requires a peak RX FIFO occupancy of one word and zero
blocked PUSH attempts. No guarantee is made for a full FIFO or an
indefinitely stalled host.

The older random-phase test remains, renamed internally to `period_ppm`: it
multiplies the **bit period** by `1 + period_ppm/1e6`, rather than applying a
true baud offset. Its per-bit host drains also add time to that sender
schedule. Its bytes, seed and period algorithm are unchanged. The
independent pin-timeline cases make the baud claim. The existing on-chip
TX-to-RX virtual-pin loopback is also kept.

### Sample windows and rearm

The driver defines each frame's START, eight data-bit and STOP intervals
without reference to receiver state. The passive observer records completed
START WAITs, each IN-pins attempt, STOP checks, PUSH attempts and the first
START-WAIT attempt after STOP. Input synchronization is enabled throughout.
An instruction at model cycle `S` consumes the external value from `S-2`.
Every data and STOP sample must lie strictly inside its offered bit
interval; START detection must lie within START. Byte equality alone is
insufficient: equal adjacent bits could hide a late sampling error.

Let `D` be the tick at which the start-bit level WAIT completes. The current
source samples data at `D+12, D+20, ..., D+68`, right-aligns at `D+70`,
pushes at `D+71`, checks STOP at `D+76`, and attempts the next START WAIT
at `D+77` after a good STOP. (Since `fault-fixes` it also reads the start
bit again at `D+4` and goes back to the WAIT if it is high; the wait's
`[10]` became `[3]` + the check + `[6]`, so every time above is unchanged.)
In these runs STOP-to-rearm takes one fractional
divider interval of 52 or 53 clocks. This includes the last byte; the final
rearm is observed during idle rather than inferred from the received data.

Observed minima and ranges across the three phases and eight bytes per case:

| True baud offset | Alternating jitter | Minimum data-window margin | Minimum STOP-window margin | Rearm slack before next physical START |
|---|---:|---:|---:|---:|
| −3 % | 0 clocks | 108 clocks | 95 clocks | 234..281 clocks |
| −3 % | ±2 clocks | 108 clocks | 93 clocks | 234..281 clocks |
| nominal | 0 clocks | 166 clocks | 167 clocks | 112..153 clocks |
| nominal | ±2 clocks | 166 clocks | 167 clocks | 112..153 clocks |
| +3 % | 0 clocks | 57 clocks | 46 clocks | −9..32 clocks |
| +3 % | ±2 clocks | 59 clocks | 46 clocks | −9..32 clocks |

A window margin is the minimum distance of the synchronized sample's
external source cycle to either offered boundary. Rearm slack compares the
actual WAIT attempt with the next physical falling edge; it is deliberately
not adjusted by the two-clock input age. At +3 %, an attempt can occur nine
clocks after that edge. This still works because WAIT tests the START
**level**, rather than requiring an edge after it is armed. The regression
checks that the synchronized source time of the rearm comes before the next
START interval ends, and verifies the following START, data and STOP
windows. The latest STOP samples remain at least 46 clocks before the next
frame; no late-byte or end-phase window is silently omitted. These finite
digital-model margins are not electrical setup or hold guarantees, or proof
of all phases and payloads within a continuous baud range.

### Framing error and break recovery

A separate pin stream sends `a5` with a low STOP, continues low for twelve
more nominal bit periods, then idles high for two bits before offering
`5a ff 00`. The receiver returns all four bytes and sets the sticky F0 once.
The erroneous `a5` was already pushed before the STOP check; it is not
filtered out. A host must treat bytes around F0 as suspect. The low break
holds recovery in the high-level WAIT; valid frames resume after high idle.
The test leaves F0 set rather than clearing it to make recovery pass.
This is firmware recovery on the declared waveform, not a physical reset,
a general break-duration specification or a noise-rejection proof.

### Scope and diagnostics

The receiver uses one core, 14 program words (56 bytes; 13 and 52 before
`fault-fixes`), R0/ISR, F0 and its
RX FIFO. No TX, bundle, model or helper, ISA, USB, hardware profile or RTL
change is required. The supported finite contract is 18 eight-byte timing
cases plus the four-byte error and recovery case, alongside the older 64-byte
streams and the virtual-pin loopback. The ±6 % offsets at which the earlier
bridge experiment failed are outside this ±3 % test set and were not
replayed.

The file's fixture counts every actual `Chip.step`, including loading and
host helpers, and records per-test `model_clocks` in JUnit properties.
The optional `UART_TRACE_DIR` saves external boundaries, samples, STOP and
rearm, FIFO and error observations and per-test clock totals before the
assertions; normal runs create no trace files. `UART_MODEL_CLOCK_LIMIT` can
impose a lower aggregate cap per invocation (default 2500000). These tests
make no native, board, metastability, RTL or pad, arbitrary-jitter or
FIFO-full claim. (`uart_rx_packed` and `uart_rx_capture` later ran on the
KV260 FPGA build of the RTL with generator-played lines: fpga/kv260/README.md,
"SPI and UART firmware on hardware".)

One recorded run of the full file passed **23 tests**, receiving 377 bytes in
1637941 counted model clocks (54.12 seconds of pytest time); see
[Run history](#run-history).

## Optional packed UART mode: 32-byte finite capture

`firmware/uart_rx_packed.pasm` is a separate optional receiver, not a
replacement for the byte-per-word program or its bundle. It uses the same
8N1 wire format, RX define and IN_BASE, eight-ticks-per-bit divider and one
core. It occupies **13 words / 52 bytes**, as the byte receiver did until
`fault-fixes` gave that one a mid-bit start check (14 words); the packed
receiver has no such check. Packed mode enables
right-shift `autopush 32` and substitutes two NOPs for the per-byte alignment
and manual PUSH. Data samples remain `D+12+8k`, STOP remains `D+76`, and the
level WAIT rearms at `D+77`. No ISA, model, FIFO depth, host API or bundle
changes. The original receiver's stale comment that rearm always precedes a
+3 % START is corrected; its instructions are identical.

Run the packed regression:

```sh
PYTHONPATH=tools python3 -m pytest -q test/model/test_uart_packed.py
```

Four received bytes form one FIFO word in little-endian order:
`b0 | (b1 << 8) | (b2 << 16) | (b3 << 24)`. Host code can decode complete
words without another general-purpose API:

```python
received = b"".join(int(word).to_bytes(4, "little") for word in rx_words)
```

Eight physical RX FIFO words therefore hold **32 payload bytes** in this
mode, using the existing ISR for the current partial group. Packing reduces
word usage for complete groups; it does not increase the UART wire rate,
provide a fourfold throughput guarantee or create larger SRAM. Resource use
is one core and its private program bank, R0, ISR and shift count, one
synchronized logical input RX (UI0/P16 in the tests), F0 and the eight-word
RX FIFO. No second engine, shared virtual pins or additional hardware buffer
is used. The tested initial state is a fresh two-core, two-bank, 256-word,
depth-8 model, an empty FIFO and an ordinary restart and enable; a live
reload with a stale FIFO or flags is outside these tests. Restart clears the
partial ISR state; idle does not.

### Same-stimulus, no-service comparison

The comparison offers exactly these 32 consecutive bytes to both programs:

```text
00 ff 55 aa 5a c3 19 e7 01 02 04 08 10 20 40 80
7e 81 3c c1 96 69 12 34 56 78 9a bc de f0 27 d8
```

At nominal 115200 baud, the physical START is cycle 4096. Both runs use the
same precomputed bit boundaries and service deadline, cycle 138453: the final
offered STOP end at 137429 plus 1024 clocks. **No loading-host register or
FIFO operation** is allowed after the normal start call returns until that
fixed deadline. PC, ISR and FIFO observations are passive and never schedule
edges or service. All loader, start, read and helper `Chip.step` calls are
counted; the synchronized input age is two clocks, with no bypass. After the
deadline, the host reads STATUS and drains that many words through normal
snap/commit/cancel reads, waits a fixed 1024 clocks, then performs a second
STATUS and drain. No privileged FIFO removal, state seeding or serialized
SPI-host throughput is involved.

| Nominal identical 32-byte stream | Byte receiver | Packed receiver |
|---|---|---|
| RX FIFO occupancy at deadline | 8 words, first 8 byte values | 8 words, all 32 byte values |
| Pending ISR at deadline | Ninth byte `01`, shift count 32, blocked PUSH | Empty, shift count 0 |
| Returned after the fixed drain/grace | First 9 byte values | All 32 exact bytes |
| First missing START | Frame 9 (tenth offered byte) | None |
| First STOP not validated | Frame 8 (ninth offered byte) | None |
| Stalls recorded across capture/drain | 1869 manual PUSH attempts | Zero PUSH/autopush stalls |
| Active loading-host operations | 0 | 0 |

The byte receiver's first blocked PUSH is at cycle 41165, PC 8. Its ninth
byte had already been sampled into ISR; it becomes readable only after the
post-deadline drain frees a FIFO slot. Its STOP was never checked at the
intended time, and the following input frames are lost. F0 stays clear here:
the framing flag is not a general loss or overflow indicator. This
distinguishes eight FIFO words from incidental pending ISR storage; neither
mode promises lossless input once a full FIFO blocks acquisition.

### Selected packed timing contract

The eight 32-byte candidate streams cover true baud −3 %, nominal and +3 %,
phases 0 and 51 clocks, ideal bits, plus +3 % at both phases with an
alternating ±2-clock bit-duration jitter. The nominal phase-0 stream is the
packed half of the single comparison and is not repeated. Rate uses
`48000000/(115200*(1+offset))`; phases shift the fixed physical START origin.
Jitter and rational-to-clock quantization follow the byte receiver's pin
timeline above. This is a finite selected matrix, not all phases or payloads
or arbitrary jitter. All 256 offered bytes return exactly in 64 words, with
no active host service or blocked autopush. Every START, data and STOP
observation is in its offered slot, including the last byte; data and STOP
source samples are strictly inside their independently defined windows.

| True baud | Phase | Alternating jitter | Minimum data margin | Minimum STOP margin |
|---|---:|---:|---:|---:|
| −3 % | 0 | 0 clocks | 106 clocks | 92 clocks |
| −3 % | 51 | 0 clocks | 107 clocks | 93 clocks |
| nominal | 0 | 0 clocks | 171 clocks | 171 clocks |
| nominal | 51 | 0 clocks | 170 clocks | 170 clocks |
| +3 % | 0 | 0 clocks | 48 clocks | 37 clocks |
| +3 % | 51 | 0 clocks | 48 clocks | 37 clocks |
| +3 % | 0 | ±2 clocks | 50 clocks | 37 clocks |
| +3 % | 51 | ±2 clocks | 50 clocks | 37 clocks |

Margins use the external source cycle `S-2`, not a pad or electrical timing
model. The first packed-word publication is 16080 clocks (335 µs) after the
first START in the nominal comparison; across the eight cases it is
15716..16446 clocks. Publication occurs on the fourth byte's last data
sample, **before its STOP is validated**. Those are publication latencies;
host reads remain postponed until after the fixed deadline. No native, RTL
or board throughput is measured.

### Partial group and framing/break limits

With `00 ff 55`, followed by eight nominal bit periods of idle, a passive
checkpoint at cycle 17620 sees **zero FIFO words**, ISR `55ff0000` and shift
count 24. Idle does not flush those three bytes. The fourth byte `aa` starts
at cycle 19929; publication at 23509 yields the word `aa55ff00` and the exact
bytes `00 ff 55 aa`. The first START-to-publication latency is 19413 clocks
in this explicit idle-gap case. Up to three bytes wait in ISR for the next
byte; there is no timeout flush or arbitrary packet-length guarantee. A
restart would discard a partial group rather than deliver it.

The fault stream is the byte receiver's: `a5` with a bad STOP, twelve extra
low bit periods, two high idle bits, then `5a ff 00`. The sticky F0 is set
once and not cleared. The group returns the word `00ff5aa5`, keeping the
suspect `a5`; the partial ISR state survives the break and recovery wait. F0
is global, not per-byte or per-word validity metadata. Consumers must treat
words around F0 as suspect and account for publication before the fourth
byte's STOP check. This is the declared finite recovery waveform, not noise
rejection or reset qualification. Full-FIFO stalls can lose input; there is
no lossless FIFO-full claim.

The file's ten tests passed in the recorded runs, using **1303266 / 3000000**
counted model clocks; see [Run history](#run-history).

## Declared-length capture: complete 1..32-byte results

`firmware/uart_rx_capture.pasm` adds an optional **declared-length** mode.
The application supplies N in 1..32 and arranges the wire START after
arming; this is not idle-delimited packet inference or unconditional
acquisition of an already running stream. The image uses **19 words / 76
bytes**, six words more than continuous packed RX. One core, one RX pin, F0
and the existing RX FIFO are used, with R1 and R2 also holding N and N mod 4.
No extra SRAM, metadata FIFO word, ISA, bundle or hardware change is needed.

Nonfinal frames keep the eight-tick interface and the data, STOP and rearm
schedule. The old D70/D71 padding slots now decrement R1 and test whether
this is the final frame. After the final D76 STOP **sample**, the program
pushes a partial group if required and executes HALT. Exactly ceil(N/4)
words are emitted: complete groups remain little endian; a final 1..3-byte
group is **high aligned** with zero low padding. N = 32 still occupies
exactly 8 FIFO words. Earlier complete words may appear before STOP
validation; word availability alone is not whole-capture completion.

### Paid setup and completion API

`tools/pe/uartcapture.py` provides the pure `capture_plan` and
`decode_capture`. Load and configure the image separately while core 0 is
disabled, using the ordinary assembler and loader with RX defined and the
same divider and IN_BASE. Before each arm or rearm, read core 0's STATUS and
the current enable mask: **both target FIFOs must be empty and core 0 must be
stopped (HALT) or disabled**. Do not silently clear leftovers. F0 belongs to
this workload; an independently running peer must not use it. Keep the
peer's enable bit when supplying the current `enabled_before` mask.

```python
from pe.uartcapture import capture_plan, decode_capture

# After ordinary stopped/disabled + empty-FIFO checks and cold image load:
writes = capture_plan(N, core=0, enabled_before=current_enable_mask)
# Send each (address, value) through the ordinary bus transport.
# Arrange the known N-byte input only after that start API has returned.
# Later read STATUS: require HALT, no fault, exactly ceil(N/4) RX words.
# Read F0 before deciding whether framing is acceptable, then drain normally.
raw_bytes = decode_capture(N, rx_words)
```

All numeric arguments are validated before a plan is returned: core 0 only,
a plain integer N in 1..32 and an enable mask in 0..3. Its five writes
disable core 0 while preserving the peer's enable, set R1 = N and
R2 = N mod 4, write **FCLR 1 only**, and restart and start core 0. No peer
registers, other flags, program or configuration, or FIFO words are written.
This costs five register data words (20 data bytes), excluding transport
framing. The test caller also pays two ordinary precondition reads; an arm
costs 9 direct-model clocks. Cold loading costs 25 writes (19 instruction +
6 configuration). These are bus operations, not a serialized SPI or native
performance measurement.

The strict decoder requires exactly ceil(N/4) uint32 words, and rejects an
invalid length, count, type or range and nonzero low padding in the final
partial word. It keeps complete little-endian groups and extracts only the
high-aligned tail. For N = 3, `55ff0000` decodes to `00 ff 55`; for N = 5,
the complete word `aa55ff00` plus the tail `5a000000` decodes to
`00 ff 55 aa 5a`. Detecting corruption that still has a valid format needs
a payload oracle; decoder success alone is neither completion nor framing
acceptance.

After the fixed test deadline, ordinary reads obtain STATUS, F0, the expected
FIFO words and an empty STATUS. A completed capture pays ceil(N/4)+3 final
read transactions: 9 model clocks for one word, 30 for eight. HALT, no fault
and the exact occupancy gate the drain and decoding. F0 is read after HALT.
Core 1 stays disabled in these model flows; all four enable masks are covered
by pure plan tests. Live peer execution is not claimed, and no peer register
or flag setup is used to manufacture preservation.

### Independent pin cases and measured boundaries

Run the capture tests:

```sh
PYTHONPATH=tools python3 -m pytest -q test/model/test_uart_capture.py
```

Every timeline is precomputed before enabling: 48 MHz, 115200 8N1, an
absolute START at 4096 + phase, rational true-baud offsets and a two-flop
input age. Inputs advance during setup and later reads. No PC, ISR, FIFO or
flag observation drives edges or schedules service. No loading-host API call
or write/snap/commit/cancel strobe occurs from the ordinary start return to
the offered source end + 1024 clocks. That deadline includes extra offered
frames and breaks and is fixed before arming. Normal host reads after it are
paid and logged.

Nominal N = 1, 3, 4, 5, 31 and 32 use the packed comparison's 32-byte pattern
truncated to N, followed by an extra complete `aa` frame. All return the
exact N bytes in ceil(N/4) words, with HALT, no fault, F0 clear and no
residual FIFO words after the ordinary drain. The extra frames are physically
offered but generate no START, data or STOP acquisition, or words. N = 32
also covers the packed matrix's other seven true-baud, phase and jitter
settings: ideal −3 %, nominal and +3 % at phases 0 and 51 (nominal phase 0
already counted), plus +3 % at both phases with an alternating ±2-clock
duration jitter.

| N = 32, true baud | Phase | Jitter | Minimum data-source margin | Minimum STOP-source margin |
|---|---:|---:|---:|---:|
| −3 % | 0 | ideal | 107 clocks | 94 clocks |
| −3 % | 51 | ideal | 106 clocks | 92 clocks |
| nominal | 0 | ideal | 157 clocks | 157 clocks |
| nominal | 51 | ideal | 156 clocks | 156 clocks |
| +3 % | 0 | ideal | 47 clocks | 36 clocks |
| +3 % | 51 | ideal | 48 clocks | 36 clocks |
| +3 % | 0 | ±2 clocks | 49 clocks | 36 clocks |
| +3 % | 51 | ±2 clocks | 50 clocks | 36 clocks |

All declared START, data and STOP observations stay in their offered windows,
including the last byte; data and STOP source times are strictly interior.
HALT follows the final STOP sample by 104 clocks for nominal complete groups,
156..157 for good partial groups, 156 for the bad N = 4 and 209 for the bad
N = 3. Nominal first-START-to-HALT is 4167 clocks for N = 1, 12501 for
N = 3, 16615 for N = 4, 20834 for N = 5, 129164 for N = 31 and 133279 for
N = 32. These are local model observations, not all-phase,
arbitrary-jitter or electrical timing guarantees.

**Completion refers to the final STOP sample, not its physical end.** In the
selected good N = 32 cases, HALT occurs between 220 clocks before and 29
clocks after the physical STOP end. The final partial PUSH is after the final
STOP sample; the final full-group autopush is 416..417 clocks before it.
Earlier words must therefore wait for whole-capture HALT and the framing
checks before being treated as an accepted result.

### Errors, rearm and under-supply

A bad final STOP at N = 3 and N = 4 keeps the exact suspect bytes. Twelve
continued low bit periods, two high idle bits and an extra complete frame
produce no extra words. The sticky F0 is set **before HALT** and not cleared
to pass. F0 is capture error information, not per-byte validity metadata;
raw decoding still returns suspect data for the caller's policy.

A normally rearmed N = 32 → N = 3 pair uses one cold capture-image load, no
reset or reload, and complete ordinary draining between arms. The first
N = 32 capture ends with a bad STOP to set F0. The second, the distinct
`39 d3 81`, starts at the declared cycle 200000 and returns `81d33900`. F0
rises at 137323, before the first HALT at 137427, is cleared only by the paid
FCLR 1 write at 142657, and stays clear at the second HALT at 212449. Both
captures use fixed deadlines and ignore their extra complete frames. No stale
words are silently discarded.

An under-supply test declares N = 5 but offers only four bytes. At the fixed
idle deadline the receiver is still waiting, R1 = 1, HALT and fault are both
clear, and one early full word is available. The completion gate does not
drain or decode it as an N = 5 result. There is no timeout, idle flush or
fabricated partial completion. The application must arrange enough input or
explicitly manage an abandoned capture; a cancellation API is outside this
mode.

The file has 208 tests: 191 pure controls and 17 model tests (17 instances,
18 capture episodes including the under-supply case). In the recorded runs
they used **1662635 / 3000000** counted `Chip.step` calls, including load,
setup, final reads and rearm. The declared lengths and start arrangement,
empty target FIFOs, F0 ownership and these finite wire cases are the
supported contract. Full-FIFO losslessness, spontaneous packet acquisition,
serialized host throughput and physical or pad qualification remain outside
it.

## Run history

The three receivers' tests were developed by a team of agents on 2026-10-13,
in numbered tickets, and later integrated on `main` with the model suite.

* **Byte receiver (ticket R13, worker commit `4519e2f`).** The final
  full-file run passed 23 tests, receiving 377 bytes in 1637941 counted model
  clocks (54.12 s of pytest time). An earlier focused 19-case run used
  674266 clocks; the ticket's total was 2312207 of its 2500000-clock budget.
  The final run was made once on the unchanged test source; the 13-word
  receiver firmware is byte-identical to the ticket's baseline (it is 14
  words since `fault-fixes`, with the same sample times; the file's tests run
  on it in the model suite). An earlier,
  separate UART-to-SPI bridge experiment (its "D13" variant, divider 13, not
  part of `main`) had shortened a four-instruction handoff after STOP, and
  failed at ±6 %; this receiver has no such handoff, and ±6 % was not
  replayed here.
* **Packed receiver (ticket C11, worker commit `80a28e5`, built on R13's
  pin-timeline helper).** The ten tests passed in two focused invocations:
  3 tests in 13.12 s and 7 in 37.47 s, a total of 1303266 of 3000000 model
  clocks, including the single baseline comparison, setup and post-deadline
  reads, with no simulator or EDA jobs. One implementation iteration
  sufficed. The 23-test byte suite was not re-run at that point.
* **Declared-length capture (worker commit `82ba9b2`, built on C11).** All
  208 tests passed across two focused selections: 197 in 16.34 s and 11 in
  54.59 s, 1662635 of 3000000 counted `Chip.step` calls. One source
  iteration sufficed; no correction, simulator, EDA or runner work was
  needed. The byte and packed suites were not re-run at that point.

On `main` all three files run in the model suite.
