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
