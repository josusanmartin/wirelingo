# UART RX timing and recovery regression

The standalone `firmware/uart_rx.pasm` receives the selected back-to-back
8N1 pin streams without a firmware change. It already returns to the start
level-WAIT one tick after the STOP check. Atlas's historical D13 bridge
experiment shortened a four-instruction handoff after STOP; that handoff
is absent here. The result justifies a maintained regression, not a bridge
rewrite, changed divider API or hardware upgrade.

Run the maintained tests from the checkout:

```sh
PYTHONPATH=tools python3 -m pytest -q test/model/test_uart.py
```

## Transmitter and service contract

The new pin-timeline tests use a 48 MHz model clock, nominal 115200 baud,
and the existing eight-ticks-per-bit receiver divider: encoded `13333`, or
`52 + 21/256` clocks per tick. Each case offers `00 ff 55 aa 5a c3 19 e7`
as eight contiguous frames. True transmitter baud offsets are -3%, nominal
and +3%, with bit period `48000000 / (115200 * (1 + offset))` clocks.
Initial phase offsets are 0, 13 and 51 clocks after a fixed 1664-clock idle
interval measured from completed loading. These are three selected phases,
not an exhaustive divider-phase sweep.

All nine rate/phase combinations run both ideal bits and deterministic
alternating +2/-2-clock duration jitter. Bit boundaries accumulate as exact
rational numbers, then quantize down to integer clocks. The jitter changes
individual bit durations while cancelling over each two-bit pair; it does
not represent arbitrary noise, clock wander or accumulated jitter.
The source is precomputed before enabling the core and advances solely by
actual model clock. It runs during host operations too. DUT PCs/registers
are observed only to record execution; they never schedule input edges.

The fixture advances 64 clocks, then drains every available RX word through
ModelHost's snap/commit/cancel register-read operations. Those operations
also advance the source and are included in the clock count. Checking the
model queue for availability is an observation, not an exercised SPI STATUS
transaction. This is an explicit promptly serviced RX policy, not a real
loading-host bandwidth benchmark or host-free capture claim. Every new case
requires peak RX FIFO occupancy of one word and zero blocked PUSH attempts.
No guarantee is made for a full FIFO or an indefinitely stalled host.

The historical random-phase test remains, renamed internally to
`period_ppm`: it multiplies **bit period** by `1 + period_ppm/1e6`, rather
than applying a true baud offset. Its per-bit host drains also add time to
that historical sender schedule. Its bytes/seed/period algorithm remain
unchanged. The independent pin-timeline cases make the new baud claim.
The existing on-chip TX-to-RX virtual-pin loopback is also retained.

## Sample windows and rearm

The driver defines each frame's START, eight data-bit and STOP intervals
without reference to receiver state. The passive observer records completed
START WAITs, each IN-pins attempt, STOP checks, PUSH attempts and the first
START-WAIT attempt after STOP. Input synchronization is enabled throughout.
An instruction at model cycle `S` consumes the external value from `S-2`.
Every data/STOP sample must lie strictly inside its offered bit interval;
START detection must lie within START. Byte equality alone is insufficient:
equal adjacent bits could conceal a late sampling error.

Let `D` be the tick at which the start-bit level WAIT completes. Current
source samples data at `D+12, D+20, ..., D+68`, right-aligns at `D+70`,
pushes at `D+71`, checks STOP at `D+76`, and attempts the next START WAIT
at `D+77` after a good STOP. In these runs STOP-to-rearm takes one fractional
divider interval of 52 or 53 clocks. This includes the last byte; final
rearm is observed during idle rather than inferred from the received data.

Observed minima/ranges across the three phases and eight bytes per case:

| True baud offset | Alternating jitter | Minimum data-window margin | Minimum STOP-window margin | Rearm slack before next physical START |
|---|---:|---:|---:|---:|
| -3% | 0 clocks | 108 clocks | 95 clocks | 234..281 clocks |
| -3% | +/-2 clocks | 108 clocks | 93 clocks | 234..281 clocks |
| nominal | 0 clocks | 166 clocks | 167 clocks | 112..153 clocks |
| nominal | +/-2 clocks | 166 clocks | 167 clocks | 112..153 clocks |
| +3% | 0 clocks | 57 clocks | 46 clocks | -9..32 clocks |
| +3% | +/-2 clocks | 59 clocks | 46 clocks | -9..32 clocks |

A window margin is the minimum distance of the synchronized sample's
external source cycle to either offered boundary. Rearm slack compares the
actual WAIT attempt with the next physical falling edge; it is deliberately
not adjusted by the two-clock input age. At +3%, an attempt can occur nine
clocks after that edge. This still works because WAIT tests the START
**level**, rather than requiring an edge after it is armed. The regression
checks the synchronized source time of rearm before the next START interval
ends and verifies the subsequent
START/data/STOP windows. The latest STOP samples remain at least 46 clocks
before the next frame; no late-byte or end-phase window is silently omitted.
These finite digital-model margins are not electrical setup/hold guarantees
or proof of all phases/payloads within a continuous baud range.

## Framing error and break recovery

A separate pin stream sends `a5` with a low STOP, continues low for twelve
additional nominal bit periods, then idles high for two bits before offering
`5a ff 00`. The receiver returns all four bytes and sets sticky F0 once.
The erroneous `a5` was already pushed before the STOP check; it is not
filtered out. A host must treat bytes around F0 as suspect. The low break
holds recovery in the high-level WAIT; valid frames resume after high idle.
The test leaves F0 set rather than clearing it to make recovery pass.
This is firmware recovery on the declared waveform, not a physical reset,
a general break-duration specification or noise rejection proof.

## Scope and diagnostics

The receiver uses one core, 13 program words (52 bytes), R0/ISR, F0 and its
RX FIFO. No TX, bundle, model/helper, ISA, USB, hardware profile or RTL
change is required. The accepted finite contract is 18 eight-byte timing
cases plus the four-byte error/recovery case, alongside the historical
64-byte streams and virtual-pin loopback. Historical Atlas +/-6% failures
are outside this selected standalone +/-3% test set and were not replayed.

The file's fixture counts every actual Chip.step, including loading and
host helpers, and records per-test `model_clocks` in JUnit properties.
Optional `UART_TRACE_DIR` saves external boundaries, samples, STOP/rearm,
FIFO/error observations and per-test clock totals before assertions; normal
runs create no trace artifacts. `UART_MODEL_CLOCK_LIMIT` can impose a lower
aggregate invocation cap (default 2500000). There are no native, board,
metastability, RTL/pad, arbitrary-jitter or FIFO-full claims.

The R13 final full-file run passed **23 tests**, receiving 377 bytes in
1637941 counted model clocks (54.12 seconds pytest time). The prior focused
19-case run used 674266 clocks; total ticket model clocks are
2312207 / 2500000. The final run was performed once at the unchanged test
source; the 13-word receiver firmware is byte-identical to the ticket
baseline. These are local model results pending host commit/integration.

## Optional packed UART mode: 32-byte finite capture

`firmware/uart_rx_packed.pasm` is a separate optional receiver, not a
replacement for the byte-per-word program or its bundle. It uses the same
8N1 wire format, RX define/IN_BASE, eight-ticks-per-bit divider and one core.
Both programs occupy **13 words/52 bytes**. Packed mode enables right-shift
`autopush 32` and substitutes two NOPs for per-byte alignment/manual PUSH.
Data samples remain `D+12+8k`, STOP remains `D+76`, and the level WAIT
rearms at `D+77`. No ISA, model, FIFO depth, host API or bundle changes.
The original receiver's stale comment that rearm always precedes a +3%
START is corrected; its instructions are identical.

Run only the maintained packed regression (prerequisite: R13 UART commit
`4519e2f`, which supplies the independent pin-timeline helper):

```sh
PYTHONPATH=tools python3 -m pytest -q test/model/test_uart_packed.py
```

Four received bytes form one FIFO word in little-endian order:
`b0 | (b1 << 8) | (b2 << 16) | (b3 << 24)`. Host code can decode complete
words without another general-purpose API:

```python
received = b"".join(int(word).to_bytes(4, "little") for word in rx_words)
```

Eight physical RX FIFO words therefore retain **32 payload bytes** in this
mode, using the existing ISR for the current partial group. Packing reduces
word usage for complete groups; it does not increase the UART wire rate,
provide a fourfold throughput guarantee or create larger SRAM. Resource
use is one core/private program bank, R0/ISR and shift count, one synchronized
logical input RX (UI0/P16 in tests), F0 and the eight-word RX FIFO. No second
engine, shared virtual pins or additional hardware buffer is consumed.
The tested initial state is a fresh two-core/two-bank/256-word/depth8 model,
empty FIFO and ordinary restart/enable; live reload with stale FIFO/flags
is outside these tests. Restart clears partial ISR state; idle does not.

### Same-stimulus, no-service comparison

The comparison offers exactly these 32 consecutive bytes to both programs:

```text
00 ff 55 aa 5a c3 19 e7 01 02 04 08 10 20 40 80
7e 81 3c c1 96 69 12 34 56 78 9a bc de f0 27 d8
```

At nominal 115200 baud, physical START is cycle 4096. Both runs use the same
precomputed bit boundaries and service deadline, cycle 138453: final offered
STOP end 137429 plus 1024 clocks. **No loading-host register/FIFO operation**
is allowed after the normal start call returns until that fixed deadline.
PC/ISR/FIFO observations are passive and never schedule edges or service.
All loader/start/read/helper Chip.step calls are counted; synchronized
input age is two clocks, with no bypass. After the deadline, the host reads
STATUS and drains that many words through normal snap/commit/cancel reads,
waits a fixed 1024 clocks, then performs a second STATUS/drain. No privileged
FIFO removal, state seeding or serialized SPI-host throughput is involved.

| Nominal identical 32-byte stream | Byte receiver | Packed receiver |
|---|---|---|
| RX FIFO occupancy at deadline | 8 words, first 8 byte values | 8 words, all 32 byte values |
| Pending ISR at deadline | Ninth byte `01`, shift count 32, blocked PUSH | Empty, shift count 0 |
| Returned after the fixed drain/grace | First 9 byte values | All 32 exact bytes |
| First missing START | Frame 9 (tenth offered byte) | None |
| First STOP not validated | Frame 8 (ninth offered byte) | None |
| Stalls recorded across capture/drain | 1869 manual PUSH attempts | Zero PUSH/autopush stalls |
| Active loading-host operations | 0 | 0 |

Baseline's first blocked PUSH is cycle 41165 at PC 8. Its ninth byte had
already been sampled into ISR; it becomes readable only after post-deadline
drain frees a FIFO slot. Its STOP was never checked at the intended time,
and subsequent input frames are lost. F0 stays clear here: the framing flag
is not a general loss/overflow indicator. This distinguishes eight FIFO
words from incidental pending ISR storage; neither mode promises lossless
input once a full FIFO blocks acquisition.

### Selected packed timing contract

The eight 32-byte candidate streams cover true baud -3%/nominal/+3%, phases
0/51 clocks, ideal bits, plus +3% at both phases with alternating +/-2-clock
bit-duration jitter. The nominal phase0 stream is the candidate half of
the single comparison and is not repeated. Rate uses
`48000000/(115200*(1+offset))`; phases shift the fixed physical START origin.
Jitter and rational-to-clock quantization follow the R13 source. This is a
finite selected matrix, not all phases/payloads or arbitrary jitter.
All 256 offered candidate bytes return exactly in 64 words, with no active
host service or blocked autopush. Every START/data/STOP observation is in
its offered slot, including the last byte; data and STOP source samples
are strictly inside their independently defined windows.

| True baud | Phase | Alternating jitter | Minimum data margin | Minimum STOP margin |
|---|---:|---:|---:|---:|
| -3% | 0 | 0 clocks | 106 clocks | 92 clocks |
| -3% | 51 | 0 clocks | 107 clocks | 93 clocks |
| nominal | 0 | 0 clocks | 171 clocks | 171 clocks |
| nominal | 51 | 0 clocks | 170 clocks | 170 clocks |
| +3% | 0 | 0 clocks | 48 clocks | 37 clocks |
| +3% | 51 | 0 clocks | 48 clocks | 37 clocks |
| +3% | 0 | +/-2 clocks | 50 clocks | 37 clocks |
| +3% | 51 | +/-2 clocks | 50 clocks | 37 clocks |

Margins use the external source cycle `S-2`, not a pad/electrical timing
model. First packed-word publication is 16080 clocks/335us after first START
in the nominal comparison; across the eight cases it is 15716..16446 clocks.
Publication occurs on the fourth byte's last data sample, **before its STOP
is validated**. Those are publication latencies; host reads remain postponed
until after the fixed deadline. No native/RTL/board throughput is measured.

### Partial group and framing/break limits

With `00 ff 55`, followed by eight nominal bit periods of idle, a passive
checkpoint at cycle 17620 sees **zero FIFO words**, ISR `55ff0000` and shift
count 24. Idle does not flush those three bytes. Fourth byte `aa` starts at
cycle 19929; publication at 23509 yields word `aa55ff00` and exact bytes
`00 ff 55 aa`. First START-to-publication latency is 19413 clocks in this
explicit idle-gap case. Up to three bytes wait in ISR for the next byte;
there is no timeout flush or arbitrary packet-length guarantee. Restart
would discard a partial group rather than deliver it.

The R13-shaped fault stream offers `a5` with bad STOP, twelve extra low
bit periods, two high idle bits, then `5a ff 00`. Sticky F0 is set once and
not cleared. The group returns word `00ff5aa5`, retaining the suspect `a5`;
partial ISR state survives the break/recovery wait. F0 is global, not
per-byte or per-word validity metadata. Consumers must treat words around
F0 as suspect and account for publication before fourth-byte STOP checking.
This is the declared finite recovery waveform, not noise rejection or reset
qualification. Full-FIFO stalls can lose input; no lossless FIFO-full claim.

The new file's ten tests passed in two focused invocations: 3 tests in 13.12s
and 7 in 37.47s. Total **1303266 / 3000000** actual model clocks includes the
single baseline comparison, setup and post-deadline reads; zero native/EDA/
runner jobs. One candidate/implementation iteration sufficed. The unchanged
23-test byte-UART suite was not rerun. These source/model results are pending
host commit and Claude integration; the new tests/docs require R13 first.
