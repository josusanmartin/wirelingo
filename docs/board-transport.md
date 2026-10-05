# Optional RP2350 PIO/DMA host transport

`tools/micropython/quad_spi.py` supplies an opt-in mode-0 quad transport for
the existing `ProtoEmu` interface. `pio_quad.py` supplies its 14-instruction PIO
program; `transport_bench.py` records bounded, read-only ID-burst measurements.
The default driver remains the portable bit-banged implementation.

The implementation was checked against the
[MicroPython v1.26.0 assembler](https://github.com/micropython/micropython/blob/v1.26.0/ports/rp2/modules/rp2.py),
[DMA implementation](https://github.com/micropython/micropython/blob/v1.26.0/ports/rp2/rp2_dma.c)
and [PIO implementation](https://github.com/micropython/micropython/blob/v1.26.0/ports/rp2/rp2_pio.c).
The SDK adapter reads named pins' `gpio_num` fields and `auto_clocking_freq`,
as exposed by the [pinned Tiny Tapeout SDK reference](https://github.com/TinyTapeout/tt-micropython-firmware/tree/d485c7a9986336024e35d790568fca33be1c5665/src/ttboard).
No connected board has been tested. Validate the installed MicroPython/SDK
revision and retain the versions with measurements.

## Resource ownership and attachment

The board owner must reserve one unused state machine in **0..7**, two DMA
channels, and ten distinct GPIOs. Construction claims unused DMA
channels; it checks whether the selected state machine is active but cannot
establish ownership of another user's inactive machine. The PIO program uses
14 slots in that machine's shared 32-slot instruction memory. `close()` does
not remove shared PIO instruction memory or change the block's GPIO window.

RP2350B can expose GPIOs through 47; one PIO block addresses either GPIO0–31
or GPIO16–47. All nine PIO pins (data groups and SCK) must fit its already
configured window. Software CS may be outside that window. The transport
queries `PIO.gpio_base()` and rejects a mismatch before allocating DMA or
changing GPIOs. It never changes a shared block's base. Builds without that
API support the legacy GPIO0–29 range.

The referenced SDK's [TTDBv3 map](https://github.com/TinyTapeout/tt-micropython-firmware/blob/d485c7a9986336024e35d790568fca33be1c5665/src/ttboard/pins/gpio_map_dbv3.py)
places write IO0..IO3 on GPIO19..22 and read IO0..IO3 on GPIO37..40, with SCK23
and CS24. That layout requires GPIO base 16. The block owner must configure
an otherwise unused PIO block before creating machines or loading programs,
as required by [MicroPython's GPIO-base API](https://docs.micropython.org/en/v1.26.0/library/rp2.PIO.html).
Select the actual installed SDK map rather than assuming these numbers.

Logical write IO0..IO3 connect to `ui[2]..ui[5]`; logical read IO0..IO3 connect
to `uo[4]..uo[7]`. Each four-pin group must be consecutive physical GPIOs,
in ascending or descending order. The two groups may have different orders.
SCK is `ui[6]`, CS is `ui[7]`. `from_tt()` resolves the selected SDK map and
rejects unsupported layouts before changing GPIOs. A different layout needs
an explicit adapter or a different PIO program; do not assume a board pin map.
Quad operation reserves these ASIC pins from other protocol uses.

Copy `protoemu.py`, `bringup.py`, `protoemu_fw.py`, `quad_spi.py`, `pio_quad.py`
and `transport_bench.py` to the board. Select an unused machine according to
the team's resource allocation, then attach after normal reset/ID bring-up:

```python
import bringup
from quad_spi import QuadSPI

pe = bringup.setup()  # Checks the SDK's actual 48-MHz clock setting.
import rp2
# TTDBv3 example: caller must own the entire unused PIO1 block for this step.
rp2.PIO(1).gpio_base(16)  # Before any PIO1 state machines/programs exist.
bus = QuadSPI.from_tt(pe.tt, sm_id=5)  # Example only: caller must own SM5.
try:
    bus.enable_quad()  # Serial HOSTCFG write, followed by a quad ID check.
except BaseException:
    bus.close()
    raise
pe.transport = bus
assert pe.read(0x800) == 0x50450002
assert pe.read(0x800, 8, fixed=True) == [0x50450002] * 8
```

While attached, use a single host/configuration writer. Do not write
`tt.ui_in`, remux the reserved GPIOs, reuse the state machine, or change
project/system clocks. The adapter checks the SDK project clock and system
clock before each transfer. Keep real clock measurements separate from these
settings. Constructing another bit-banged `ProtoEmu` while PIO owns the pins
also violates ownership.

After a global chip reset, keep CS high for the ISA's five-clock interval,
call `bus.enable_quad()` again, and resynchronise the driver's ownership
shadow with the reset map, for example `pe.set_pin_owners({})`.
`bus.close()` stops the machine, aborts both DMA channels, holds CS high and
SCK low, and releases DMA resources. Explicitly restore the SDK pin mux/mode
before returning to bit banging; simply setting `pe.transport = None` does
not restore GPIO muxes.

## Transfer and failure behavior

The default requested SCK is half the supplied project clock: 24 MHz for
48 MHz. Four PIO instructions generate each SCK period. Divider quantization
rounds toward a slower average clock, and validation checks the shortest
fractional-divider half-cycle against the ISA minimum. Prologue/epilogue
instructions and an idle-CS gap preserve setup, hold and reset-idle intervals.
RP2 input synchronizers, ASIC pad delays, wiring and actual duty cycles still
require measurements.

The serial command is followed by a quad address/data phase. Reads retain
eight dummy SCK. DMA samples include the header, a padded final half-word,
and a completion marker emitted after the final falling edge and hold time.
CS is released after receiving that marker and checking both DMA channels.
TX padding is discarded before the next control word, including consecutive
partial transfers. An empty TX FIFO can stretch SCK low; a full RX FIFO can
stretch it high. Thus the configured rate is an upper bound, not a sustained
throughput result.

`read()` preserves the driver's scalar-for-one/list-for-many convention;
`write()` accepts a word, sequence or generator. Bursts contain 1..4096 words.
Address overflow, invalid words and invalid counts fail before selection.
FIFO callers must still respect availability; DMA does not prevent a write
to a full chip FIFO. `xfer()` supports the legacy serial protocol for bootstrap
and recovery. USB service and other higher-level driver methods use the
attached transport through their existing read/write interface.

Timeouts and DMA/completion errors abort both channels while buffers remain
alive, deselect the chip and reinitialise the state machine before reporting
failure. **A partial write may already have landed.** Do not automatically
retry side-effecting transfers; inspect/reset/resynchronise according to the
application. The transport is synchronous and rejects overlapping calls.

## Digital reproduction and board evidence

Host tests check independent wire packing, consecutive compiled PIO transfers,
four-word FIFO starvation, quantized-clock guards, channel allocation/abort
paths, SDK mapping and driver delegation. The DMA API tests use fakes and do
not validate DMA hardware. Cocotb decodes the compiled instructions and drives
the real ASIC SPI pins with quantized PIO intervals. It executes loaded
instruction images, checks 16-bit registers and 32-bit FIFO words, sweeps pin
order, tests burst reads and resets. Its PIO test machine excludes RP2 input
synchronizer and pad delay.

```sh
PYTHONPATH=tools python -m pytest -q test/model/test_pio_transport.py
make -C test COCOTB_TEST_MODULES=test_pio SIM_BUILD=sim_build/my-pio-run \
  COCOTB_RESULTS_FILE=my-pio-run.xml
python test/check_results.py test/my-pio-run.xml 2
```

`test/pio_program.json` binds the instruction fixture to the exact PIO source
and pinned assembler hashes. To change the program, download the linked
v1.26.0 `rp2.py` to a private directory and regenerate explicitly:

```sh
python test/compile_pio.py --rp2-source /path/to/pinned/rp2.py \
  --output test/pio_program.json
```

On the board, record declared board and SDK revisions with the bounded probe:

```python
import transport_bench
result = transport_bench.run(pe, board_revision="actual PCB revision",
                             sdk_revision=pe.tt.revision)
print(result)
```

The result retains every transaction latency plus median, p99, maximum and
payload bytes/second. It labels clock settings and leaves measured clocks and
sampling margin unset. It reads only the fixed ID address and does not touch
FIFOs or programs. Add oscilloscope/logic-analyzer SCK/project frequency,
high/low duty, input margin and CS interval evidence. Then measure the actual
concurrent USB/Ethernet service loop, including tail latency, host pauses,
FIFO levels/loss and reset recovery. ID-burst measurements alone cannot
establish those service bounds.
