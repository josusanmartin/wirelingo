# Packed SPI on the existing protocol engine

## Summary

**What it does.** Two firmware programs drive an SPI device with the payload
packed four bytes per FIFO word, on the existing instruction set and RTL:

* `spi_master_packed.pasm` takes reusable queued messages: one FIFO header
  word gives the bit count and tail count, followed by packed payload words.
  The CPHA0 image is 60 bytes.
* `spi_master_preloaded_once.pasm` takes its setup in R0/R1 instead of a
  header, so all 32 payload bytes fit the eight-word TX FIFO before the
  start. It releases CS and halts after one frame. The image is 44 bytes; it
  supports core 0, mode 0 and 1–32 bytes, with synchronized MISO and 32-bit
  shift thresholds. It does not provide arbitrary-length unattended
  transfers.

**How to use it.** `pe.spipacked` prepares and decodes messages on the PC:
`preload_plan(payload, enabled_before=…)` for the one-shot, `encode` and
`decode`. Load the program once with `pe.export` (command below), apply a
plan for each message and drain the RX words. Pins in the RTL test: MOSI 8,
SCK 9, CS 10, MISO 16 (`ui[0]`). On the demo board,
`tools/micropython/bringup.py` wraps both programs on the `uio` pins
([below](#on-the-demo-board-micropython)).

**Envelope.** The one-shot's RTL qualification is two consecutive 32-byte
messages on core 0, mode 0, divisor 3, a 20 ns system clock, host SCK at 8
system clocks and a fixed 3120-clock wait: 11712 paid resident clocks per
message, about 288 under a 12000-clock deadline. The final CS hold is 3
system clocks. The one-shot's other lengths (1, 3, 4, 5 and 31 bytes) and
the queued program's four modes at divisors 3 and 4 are tested on the cycle
model only; no other clock configuration is qualified at RTL. CPU cost, pad
timing, Fmax and PPA are unknown.

**Evidence.**

* Cycle model: `test/model/test_spi_preloaded.py` and
  `test/model/test_spi_packed.py` (27 and 139 tests).
* RTL simulation (Icarus Verilog, the top level with behavioral SRAM):
  `test/test_spi_preloaded_host.py`, two one-shot messages through the real
  serial host port; `test/test_spi_packed_host.py`, the queued comparison.
* The MicroPython board code: cycle model only.
* FPGA: the packed (modes 0–3), preloaded (mode 0) and all-mode byte
  firmware ran on the KV260 build against the fabric's SPI device
  ([below](#on-the-fpga-build-2026-10-14)).
* Not run: gate level, a demo board, IHP pads or silicon.

## Preparation and execution

PC-side preparation is transport independent:

```python
from pe import spipacked

payload = bytes(range(32))
plan = spipacked.preload_plan(payload, enabled_before=0)
# plan has eight high-aligned payload words and no FIFO header.
# R0 = 255; R1 = 0; RX count = 8.
```

Zero or 33-byte lengths, non-bytes inputs, unsupported modes, cores or FIFO
depths and invalid enable masks are rejected before any plan is returned.
Payload bytes are copied. `enabled_before` is the caller-known two-core
enable mask (core 0's bit is ignored during disable and rearm); disable and
restart actions preserve the unrelated core's enable. Shared pins must
already belong to core 0. The plan neither reads model state nor drives
protocol pins.

Load and configure the one-shot program **once**, while core 0 is disabled.
Use MOSI 8, SCK 9, CS 10 and MISO 16 (MISO 16 is `ui[0]`), mode 0, divisor 3,
synchronizers on and depth 8. A normal export command produces the
`pe.load(...)` image and configuration:

```sh
PYTHONPATH=tools python -m pe.export firmware/spi_master_preloaded_once.pasm \
  --define CPOL=0 --define WORD_BITS=32 --define CS=10 \
  --out-pins 8,1 --side-pins 9 --in-pins 16 --clkdiv 3
```

The RTL example uses a 20 ns system period and a host SCK period of 8 system
clocks. Its external peer launches MISO two clocks after the falling SCK.

The following synchronous transport outline uses normal register reads and
writes (`read` returns an integer for one word and a list for multiple):

```python
# Precondition: previous frame completed, CS high, no fault, both FIFOs empty.
# Do not clear unexpected RX leftovers to manufacture an empty precondition.
for address, value in plan['disable_writes']:
    transport.write(address, [value])
transport.write(plan['register_writes'][0][0],
                [value for _, value in plan['register_writes']])
transport.write(plan['fifo_address'], plan['tx_words'], fixed=True)
for address, value in plan['start_writes']:
    transport.write(address, [value])

# Caller implements this fixed time from the declared system clock.
# For the exact 32-byte/div3 benchmark: wait 3120 clocks AFTER start returns.
# This timer is not a qualification for other lengths or clock configurations.
wait_system_clocks(3120)
status = transport.read(0x409)
assert status & 255 == plan['rx_words'] << 4
assert status & (1 << 13) and not status & (1 << 14)  # HALT, no fault
rx_words = transport.read(plan['fifo_address'], plan['rx_words'], fixed=True)
if isinstance(rx_words, int):  # Single-word transports may return a scalar.
    rx_words = [rx_words]
response = spipacked.decode(rx_words, plan['length'])
status = transport.read(0x409)
assert status & 255 == 0 and status & (1 << 13) and not status & (1 << 14)
pout = transport.read(0x809)
assert pout & ((1 << 10) | (1 << 9)) == 1 << 10  # CS high, SCK idle
```

Apply a new plan to rearm the next message; no reset or reload is needed.
Restart clears the shift state and keeps the newly written R0/R1. The partial
last RX word is low aligned, and `decode` rejects wrong word counts and
phantom high tail bits. These checks cannot replace the caller's empty-FIFO
and completed-frame preconditions.

The RTL test additionally reads R0/R1 back before preloading, and charges
that traffic. It starts resident timing at the disable and ends it at the
returned final POUT, including the fixed wait, the drain and all STATUS
operations. An initial reset and FIFO clear are charged to the cold load;
the per-message rearm never clears FIFO contents.

**Startup guard.** The `[2]` delay on the ISR-clear instruction keeps CS high
until the host's start call has returned. It adds six startup clocks at
divisor 3 without adding a word. Both the physical host pins and the whole
start-call interval must be outside CS low; the RTL test checks both.

## Running the tests

With pytest installed, from the checkout root:

```sh
PYTHONPATH=tools python -m pytest -q test/model/test_spi_preloaded.py test/model/test_spi_packed.py
```

With cocotb and Icarus already installed in the selected Python environment:

```sh
python test/test_spi_preloaded_host.py --output /tmp/spi-preloaded-fresh
# If Icarus is not on PATH, add --icarus-bin /path/to/icarus/bin.
```

The output directory must be fresh or empty; omitting `--output` uses a new
temporary folder. The runner selects one exact test, the actual top-level
RTL plus behavioral 256-word SRAM, two cores, two banks, depth 8, `make -j1`,
the installed libpython and the normal pygpi delegates. It handles the
existing OSS suite's child-only loader environment without editing that
suite. `-none` disables dumps; the log must confirm the suppression, and no
waveform file may appear. No historical evidence path or hash manifest is
needed. Missing simulator dependencies do not prevent the API and model
tests; the runner installs nothing. The queued comparison runs the same way:
`python test/test_spi_packed_host.py --output /tmp/spi-queued-fresh`.

## Measured costs (RTL simulation)

**The one-shot, current test.** One run passed two consecutive distinct
32-byte messages after one cold load, without reset or reload, at
11711.9999 and 11711.99995 paid resident clocks: each about 288 clocks under
the 12000-clock deadline. Each message pays 8592 serialized transaction
clocks plus the fixed 3120-clock wait. Each has nine real host transactions,
eight TX payload words, eight RX words, eight CSR setup data bytes and no
FIFO header. Both pass the strict host-silence check, exactly 256 samples
and 512 edges, full-duplex data, HALT, RX 8 / TX 0, an exact drain and the
rearm. The first host service begins 35.99995 clocks after the physical CS
release. The cold load is separate: 8734.5 clocks, 134 serial bytes and 44
program bytes.

The deadline assertion runs only after the complete paid timing and protocol
results are saved. The register readback, STATUS reads, final POUT, stimuli,
startup guard and the physical and API silence checks are all kept. The
timer is fixed from the start call's return and never uses peer or internal
completion.

**The same messages with a 4096-clock wait.** An earlier run of the same
test, with the wait at 4096 clocks, cost about **12688 paid resident clocks**
per message, including 784 clocks of R0/R1 readback. Its timer expired about
1012 clocks after the wire ended (the post-wire STATUS, drain and POUT
costs are recorded separately in that run's artifacts), and both messages
missed the 12000-clock deadline by about 688 clocks. The 3120-clock wait removes 976
waiting-policy clocks per message at the same hardware and wire rate.

**Scope.** The qualification is limited to two consecutive 32-byte messages
on core 0, mode 0, divisor 3, synchronized MISO 16 with a two-clock peer
launch, depth 8, two cores and two 256-word banks, a 20 ns system period and
host SCK at 8 system clocks. Other modes, lengths, clock configurations, CPU
cost, pad timing, Fmax and PPA are not qualified by this run.

**Against the queued program.** The earlier queued 32-byte result was 9216
paid clocks with the 60-byte image, a header plus eight TX payload words,
only 28 payload bytes before the start, and active service. That is a
different service policy, not a matched speed comparison: the one-shot is
slower under its deliberately fixed wait and readback. This run shows
bounded capacity and host silence, not a causal speed advantage.

**CS hold.** The final CS hold is 3 system clocks. A peer that needs 4
clocks, as the peer model of a separate UART-to-SPI bridge experiment did,
is not served. Peers must tolerate finite pauses inside a frame; other host
speeds and divisors need their own startup, sample, hold and wait
qualification.

## On the demo board (MicroPython)

`tools/micropython/bringup.py` wraps both programs, and the all-mode byte
firmware, for the Tiny Tapeout demo board. Pins are `uio` (SCK 2, MOSI 3,
MISO 4, CS 5, with a pull-up on CS); the default divider is 12 (4 ticks per bit,
1 MHz SCK at 48 MHz; the packed CPHA1 body takes 5), and `div=` takes any
integer from 3.

```python
import bringup
pe = bringup.setup()
rx = bringup.spi_transfer(pe, b"\x9f\0\0\0", mode=1)     # spi_master_modes, host CS
bringup.spi_packed_setup(pe, mode=2)                      # resident queued firmware
rx1, rx2 = bringup.spi_packed_transfer(pe, [b"\x05\0", bytes(200)])
bringup.spi_preloaded_setup(pe)                           # load once, core 0 stays disabled
rx = bringup.spi_preloaded_transfer(pe, bytes(range(32)))  # plan, start, wait HALT, drain
```

The board copies of `encode`, `decode` and `preload_plan`
(`spi_packed_encode`, `spi_packed_decode`, `spi_preload_plan`) must equal
`pe.spipacked`'s, rejections included; a model test checks this.
`spi_preloaded_transfer` checks the preconditions above and refuses, without
clearing anything, if they fail; it waits for HALT by polling STATUS rather
than a fixed timer. `test/model/test_micropython_spi_uart.py` runs all of
this unmodified on the cycle model through the host-port model, against an
exact-frame SPI device on the `uio` pins. It has not run on a demo board.

## On the FPGA build (2026-10-14)

The packed (modes 0-3), preloaded (mode 0) and all-mode byte firmware also
ran on the KV260 FPGA build of the chip RTL against the fabric's SPI device
and under `la_capture`: see fpga/kv260/README.md, "SPI and UART firmware on
hardware" (`fpga/kv260/host/spi_uart_demo.py`). All runs passed; the results
equal the cycle model's. That is fabric timing, not the IHP pads or silicon.

## Run history

The one-shot was developed on 2026-10-13 by a team of agents in numbered
tickets (R13, C13). Its firmware, helper and RTL test on `main` match the
worker commit `8f4df47`, except for the test's wait change below.

* **R13, first RTL run.** The original 11-word program started CS about 3
  clocks before the host's start call returned. The retained RTL trace
  caught this although the host pins were physically idle during the API's
  trailing gap. The `[2]` startup guard fixed it; the oracle was not
  weakened.
* **R13, second RTL run**, with the 4096-clock wait: the protocol checks
  passed, and both messages missed the 12000-clock deadline by about 688
  clocks (the 12688-clock figures above). That miss and the startup failure
  stay on record.
* **C13** changed only the wait to 3120 clocks (for this exact 32-byte,
  divisor-3 schedule) and added the 12000-clock assertion, which rejects
  R13's saved late row. One RTL run then passed with 288 clocks of slack
  (the 11712-clock figures above).
