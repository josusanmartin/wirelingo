# Packed SPI on the existing protocol engine

There are two interfaces. `spi_master_packed.pasm` accepts reusable queued
messages: one FIFO header supplies the bit count and tail count, followed by
packed payload words. `spi_master_preloaded_once.pasm` uses paid R0/R1 setup
instead of a header, allowing all 32 payload bytes to fit an eight-word TX
FIFO before starting. It releases CS and halts after one frame.

These are existing-ISA firmware, not RTL changes. The queued CPHA0 image is
60 bytes; the mode0 one-shot image is 44 bytes. The one-shot supports core0,
mode0 and 1–32 bytes. It uses synchronized MISO and 32-bit shift thresholds.
It does not provide arbitrary-length unattended transfers.

## Preparation and execution

PC-side preparation is transport independent:

```python
from pe import spipacked

payload = bytes(range(32))
plan = spipacked.preload_plan(payload, enabled_before=0)
# plan has eight high-aligned payload words and no FIFO header.
# R0 = 255; R1 = 0; RX count = 8.
```

Zero/33-byte lengths, non-bytes inputs, unsupported modes/cores/FIFO depths
and invalid enable masks are rejected before any plan is returned. Payload
bytes are copied. `enabled_before` is the caller-known two-core enable mask (core0's bit is
ignored during disable/rearm);
disable/restart actions preserve the unrelated core's enable. Shared pins
must already belong to core0. The plan neither reads model state nor drives
protocol pins.

Load/configure the one-shot program **once**, while core0 is disabled. Use
MOSI8/SCK9/CS10/MISO16 (MISO16 is `ui[0]`), mode0, divisor3, synchronizers on
and depth8. A normal export command produces the `pe.load(...)` image/configuration:

```sh
PYTHONPATH=tools python -m pe.export firmware/spi_master_preloaded_once.pasm \
  --define CPOL=0 --define WORD_BITS=32 --define CS=10 \
  --out-pins 8,1 --side-pins 9 --in-pins 16 --clkdiv 3
```

The native example uses system period20ns and host SCK period8
system clocks. Its external peer launches MISO two clocks after falling SCK.

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
# For the exact 32-byte/div3 benchmark: wait3120 clocks AFTER start returns.
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

Apply a new plan to rearm the next message; no reset/reload is needed.
Restart clears shift state and retains the newly written R0/R1. The partial
last RX word is low aligned and `decode` rejects wrong word counts or
phantom high tail bits. These checks cannot replace the caller's empty-FIFO
and completed-frame preconditions.

The regression additionally reads R0/R1 back before preloading, and charges
that traffic. It starts resident timing at disable and ends at the returned
final POUT, including the fixed wait, drain and all STATUS operations. An
initial reset/FIFO-clear is charged to cold load; per-message rearm never
clears FIFO contents.

## Regression commands

With pytest installed, from the checkout root:

```sh
PYTHONPATH=tools python -m pytest -q test/model/test_spi_preloaded.py test/model/test_spi_packed.py
```

With cocotb/Icarus already installed in the selected Python environment:

```sh
python test/test_spi_preloaded_host.py --output /tmp/spi-preloaded-fresh
# If Icarus is not on PATH, add --icarus-bin /path/to/icarus/bin.
```

Output must be fresh/empty. Omitting `--output` uses a new temporary
folder. The runner selects one exact native test, actual top RTL plus
behavioral SRAM256, two cores/two banks/depth8, `make -j1`, the installed
libpython and normal pygpi delegates. It handles the existing OSS suite's
child-only loader environment without editing that suite. `-none` disables
dumps; the log must confirm suppression and no waveform file may appear.
No historical evidence path or hash manifest is required. Missing native
dependencies do not prevent API/model tests; nothing is installed by the
runner. The queued comparison is likewise runnable with
`python test/test_spi_packed_host.py --output /tmp/spi-queued-fresh`.

## Qualified boundary and measured costs

The original 11-word proposal started CS about3 clocks before the host start
call returned. The retained native trace caught this despite physically idle
host pins during the API's trailing gap. The ISR-clear instruction now has
`[2]`, adding six startup clocks at divisor3 without adding a word. Both the
physical host pins and the complete start-call interval must be outside CS
low; the oracle was not weakened.

In the retained R13 run, two consecutive distinct32-byte messages pass that strict silence check,
exact256samples/512edges, full-duplex data, HALT, RX8/TX0, exact drain and
rearm. Both cost about **12688 paid resident clocks**, including784 clocks
of R0/R1 readback. Cold load is8734.5clocks and134serial bytes, with44program
bytes. Each resident message has nine real host transactions, eight TX
payload words, eight RX words, eight CSR setup data bytes, and no FIFO
header. The fixed4096-clock timer expires about1012 clocks after wire end;
post-wire status/drain/POUT costs are separately recorded in the artifacts.
Both messages miss the12000-clock comparison deadline by about688 clocks.

The saved C13 native run passed both consecutive32-byte messages at
11711.9999 and11711.99995 paid resident clocks, each with approximately288
clocks of slack against the unchanged12000-clock deadline. Each message
pays8592 serialized transaction clocks plus the fixed3120-clock wait;
first host service begins35.99995 clocks after physical CS release. Cold
load is separate:8734.5 clocks,134serial bytes and44program bytes. The
deadline assertion runs only after complete paid timing and protocol
results have been saved. Register readback, STATUS reads, final POUT,
stimuli, startup guard and physical/API silence checks are retained.
The timer is fixed from start return and never uses peer or internal
completion. This removes976 waiting-policy clocks per message relative to
R13 at the same hardware/wire rate; its MISS688 and original startup
failure remain preserved. Qualification is limited to two consecutive32B
messages on core0, mode0/div3, synchronized MISO16 with two-clock peer
launch, depth8, two cores/two banks256,20ns system period and host SCK8
system clocks. Final hold remains3 clocks; other modes, lengths, clock
configurations, CPU cost, pad timing, Fmax and PPA are not qualified by
this run. The queued9216-clock result uses a different active-service
policy and is not a matched speed comparison.

The historical queued32-byte result was9216 paid clocks with a60-byte image,
header plus eight TX payload words, only28 prestart payload bytes and active
service. The one-shot is slower under its deliberately fixed wait/readback
policy. This proves bounded capacity/silence, not a causal speed advantage.
The native final CS hold is3system clocks, which does **not** meet Atlas's
distinct4-clock peer requirement. Peers must tolerate finite intra-frame
pauses; other host speeds/divisors need their own startup, sample, hold and
wait qualification. CPU overhead, physical rates, Fmax and PPA are unknown.

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
