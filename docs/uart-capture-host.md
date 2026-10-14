# Declared UART capture through the serial host

## Summary

**What it does.** The optional core-0 firmware `uart_rx_capture.pasm`
(19 words / 76 bytes) captures a declared 1–32-byte 8N1 message. It packs
four bytes per RX word, keeps suspect bytes and sets a sticky F0 for a bad
STOP, and halts after the final STOP sample. This page is about driving it
through the chip's serial host port; the firmware's timing on the cycle
model is in [uart-rx-timing.md](uart-rx-timing.md#declared-length-capture-complete-132-byte-results).

**How to use it.**

* Load the image once with core 0 disabled, RX 16 mapped to `ui[0]`,
  synchronized inputs and the divider
  `host.clkdiv_for(50_000_000, 8 * 115200)`.
* Before each capture apply `pe.uartcapture.capture_plan(length,
  enabled_before=ctrl & 3)`: five ordinary writes. After the message, read
  STATUS and F0, drain `ceil(length/4)` words and decode them with
  `decode_capture`.
* Demo board: `bringup.uart_capture(pe, n)` does all of this with RX on
  `uio[6]` ([below](#on-the-demo-board-micropython)).

**Envelope.** At RTL: one run at a 50 MHz system clock and nominal 115200
baud, with two messages (32 bytes ending in a bad STOP, then 3 bytes) on a
fixed service schedule, core 1 disabled. Arming cost 3168 clocks; completion
reads 3904 and 2112 clocks; first drain plus second arm 7072 clocks. Other
lengths (1, 3, 4, 5 and 31 bytes) and baud offsets of ±3 % (for 32 bytes)
are covered on the cycle model at 48 MHz
([uart-rx-timing.md](uart-rx-timing.md#independent-pin-cases-and-measured-boundaries)),
and the KV260 build ran the same corners ([below](#on-the-fpga-build-2026-10-14)).

**Evidence.**

* RTL simulation (Icarus Verilog, top level with behavioral SRAM):
  `test/test_uart_capture_host.py`, one test, through the real serial host
  port.
* Cycle model: `test/model/test_uart_capture.py` (208 tests).
* The MicroPython board code: cycle model only.
* FPGA: `uart_rx_capture`, `uart_rx_packed`, `uart_rx_capture_peer` and
  `uart_rx_mailbox` ran on the KV260 build with generator-played lines
  ([below](#on-the-fpga-build-2026-10-14)).
* Not run: gate level, a demo board, a real external UART, silicon.

## Contract

F0 belongs to this capture workload. A partial final word is high aligned;
`decode_capture` rejects wrong counts or nonzero low padding. Decoder success
alone does not validate framing or data. The firmware halts after the final
STOP sample, possibly before the physical STOP ends. No generic host, model,
RTL or runtime change is needed.

Load once with core 0 disabled, RX 16 mapped to `ui[0]`, synchronized inputs,
normal pin ownership and `host.clkdiv_for(50_000_000, 8 * 115200)`. This is a
50 MHz system clock (20 ns), not 48 MHz. The test uses two cores, two
256-word banks, depth 8 and core 1 disabled. The helper preserves an
unrelated enable bit, but concurrent-core operation is not qualified by this
test.

Before every arm or rearm, pay ordinary STATUS and CTRL reads. Require both
FIFOs empty, no fault, and core 0 disabled or HALTed. Do not clear unexpected
leftovers to manufacture success. Apply `uartcapture.capture_plan(length,
enabled_before=ctrl & 3)` through normal serialized writes: disable, an
adjacent R1/R2 count burst, FCLR 1, restart and enable, in that order. No
FIFO clear, reset or reload belongs in a normal rearm. The host must know
the declared length and arm before the independent source starts.

After the caller's fixed service time, read STATUS and F0, then one
fixed-address FIFO burst of exactly `ceil(length/4)` words, and an empty
STATUS. Require HALT, no fault, the exact RX count, TX 0, the expected raw
data and the framing flag. Read F0 before the next ordinary FCLR 1: a bad
final STOP must survive as data plus a sticky F0. The three-byte example
`39 d3 81` produces exactly `0x81d33900`, with no phantom byte.

## The RTL test

With cocotb and Icarus already installed:

```sh
python test/test_uart_capture_host.py --output /tmp/uart-capture-fresh
# Optional: --icarus-bin /path/to/icarus/bin
```

The output directory must be fresh or empty; the default is a fresh
temporary directory. The unchanged SPI runner selects only
`test_uart_capture_host.test_capture_rearm`, and uses the actual top-level
RTL with behavioral SRAM, host SCK at 8 system clocks, a 230000-clock ledger
and the existing 108-second inner timeout. It keeps the time-0 runtime maps,
normal pygpi delegates, exact argv and test guards and `-none` waveform
suppression. Nothing is installed or bootstrapped.

The one test cold-loads once, then captures 32 bytes with a low final STOP
and three distinct bytes with valid STOPs, without reset or reload. Its
rational 115200-baud source begins at absolute cycles 24000 and 190000; all
input slots are declared in advance and advance independently of host
readiness. The physical source shares the host's UI shadow through
`set_low_inputs`. Host service begins at fixed cycles 164000 and 204100,
never from DUT state, FIFO occupancy, flags or peer completion. Both the API
calls and the physical host CS/SCK must stay idle from arm return through
each service time.

The first completion and drain plus the second arm must return by 180000;
the second full completion must return by 210000. Arming must precede each
fixed source START. All precondition, count, FCLR and start costs, STATUS,
F0 and drain costs, readback, pin transitions, raw words, flags and failures
are saved. Cold load is charged separately. Deadline failures keep complete
rows.

Core configuration offsets 0x00–0x06 are write-only and read as zero. The
test pays a six-word read of them and checks for the six zeros, records the
intended configuration values separately, and validates the six-word write
frame it submitted. That submitted frame is not an internal configuration
read or a separate proof of delivery to the pads.

This is a finite qualification of a nominal source, a fixed schedule and
core 0, not a general baud envelope, an indefinite stream, idle packet
inference, a timeout or flush, live core-1 operation, CPU overhead, pad,
Fmax or PPA, silicon or a board. Earlier full words can appear before STOP
validation; wait for HALT of the whole capture and read F0 before treating
data as framed.

### Measured RTL run

One run passed both captures. The 32-byte message with a bad final STOP kept
every byte and set F0 = 1; after an ordinary paid FCLR 1 and rearm,
`39 d3 81` returned exactly `0x81d33900` with F0 = 0. Both had HALT, no fault
and exactly RX 8 / RX 1 with TX 0 before draining, empty final FIFOs, and no
host API transactions or physical host CS/SCK activity inside their quiet
windows.

| Paid measurement | First, 32 B | Second, 3 B |
|---|---:|---:|
| Arm, including fresh STATUS/CTRL reads | 3168 clocks | 3168 clocks |
| Completion: STATUS, F0, drain, empty STATUS | 3904 clocks | 2112 clocks |
| Arm return, absolute cycle | 15183 | 171072 |
| Margin before the fixed source START | 8817 clocks | 18928 clocks |
| Scheduled service, absolute cycle | 164000 | 204100 |
| Final returned empty STATUS, absolute cycle | 167904 | 206212 |

The first drain plus the second arm cost 7072 clocks, returning at 171072
with 8928 clocks of slack against 180000. The final completion has 3788
clocks of slack against 210000. Cold load is separate: 12015 clocks (12000
serialized host transaction clocks plus 15 reset and setup-wait clocks), ten
frames and 185 serial bytes, including 76 program bytes. The whole run uses
30 host frames, 2984 host SCK bits, 24352 serialized transaction clocks and
206212 total RTL clocks, including the declared idle waits, below the 230000
cap. The measured 3168/3904/2112/7072 costs match the predictions made from
the source before the run.

This applies only to the declared 50 MHz / 115200-baud two-message source and
fixed service schedule on core 0, with core 1 disabled and behavioral SRAM.
The source timing never follows host or DUT readiness. It does not extend the
baud envelope or show indefinite-stream capture, live core-1 concurrency,
CPU cost, pad, Fmax or PPA, silicon or board operation.

## On the demo board (MicroPython)

`bringup.uart_capture(pe, n, baud=115200, clock_hz=48_000_000, timeout_ms=2000,
after_arm=None)` loads the capture on core 0 with RX on `uio[6]`, applies the
same five-write plan, then calls `after_arm()` (if given: make the device send
there). It returns `(data, framing_error)`, where `framing_error` is F0 read
before any FCLR: a low STOP keeps its byte in `data` and sets the flag.

```python
data, framing_error = bringup.uart_capture(pe, 3)    # e.g. (b"\x39\xd3\x81", False)
data, framing_error = bringup.uart_capture(pe, 64)   # above 32: uart_rx_packed
```

For 1–32 bytes it waits for HALT, then requires no fault, the exact RX count
and TX 0, decodes with the board copy of `decode_capture`, and checks the FIFOs
are empty after the drain. Above 32 bytes, `n` must be a multiple of 4: it
runs `uart_rx_packed` and drains words while they arrive, then raises if the
RX FIFO was ever seen full. On a timeout, fewer bytes than declared for
example, it stops core 0, clears its FIFOs and F0, and raises. The firmware is
loaded on every call; this is not the paid-rearm schedule measured above.
`test/model/test_micropython_spi_uart.py` runs it unmodified on the cycle
model through the host-port model (lengths 3, 4, 5, 32, 36 and 40, ±3 %
baud, bad STOPs, a break, under-supply). It has not run on a demo board.

## On the FPGA build (2026-10-14)

`uart_rx_capture`, `uart_rx_packed`, `uart_rx_capture_peer` (live beside a
capturing core 0, rearmed while it ran) and `uart_rx_mailbox` (host standing
in for the USB core's F7 release) also ran on the KV260 FPGA build of the chip
RTL with generator-played 8N1 lines: see fpga/kv260/README.md, "SPI and UART
firmware on hardware" (`fpga/kv260/host/spi_uart_demo.py`). All runs passed;
the results equal the cycle model's. That is fabric timing, not the IHP pads
or silicon, and the sources are our models, not an external UART.

## Run history

The firmware and `tools/pe/uartcapture.py` were developed by a team of
agents in worker commit `82ba9b24f88d7df04b68419aab956208e2e67bfd`; the
copies on `main` are byte-identical to it. The RTL test was then developed
in three numbered tickets on 2026-10-13, each allowed one RTL run and none
retried:

* **C14** failed at time 0, before any clock or host transaction: the
  initial pin observer read an unresolved UI value before its queued write
  settled.
* **C15** added only a 1 ps wait before that first observation (pins first
  appear at 1 ps, before the first 10000 ps clock edge); every later strict
  integer conversion, source time, service time and oracle was unchanged.
  The run then failed during cold configuration readback at cycle 11487
  (229740 ns): it read six zero words where it expected the written values,
  because those registers are write-only. No assertion was removed or
  changed. Nine cold host frames had paid 11472 serial clocks plus 15
  reset and setup-wait clocks. Neither capture was armed and no UART frame
  started, so the arm and completion costs stayed unmeasured predictions.
  This was a test setup failure, not a UART or RTL counterexample. The
  runtime identity, exact single-test collection and waveform suppression
  worked.
* **C16** kept the paid six-word read and checked for the documented six
  zeros (see [The RTL test](#the-rtl-test)); no timed transaction or wait
  was removed. Its single run is the [measured run](#measured-rtl-run)
  above.
