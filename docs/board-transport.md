# RP2350 PIO quad transport (optional)

`tools/micropython/quad_spi.py` is an optional quad-SPI transport for the
`ProtoEmu` driver, using one PIO state machine (`pio_quad.py`, 14
instructions) and two DMA channels. The default transport is still the
portable bit-banged one. It was written against MicroPython v1.26.0's
[rp2 module](https://github.com/micropython/micropython/blob/v1.26.0/ports/rp2/modules/rp2.py)
and the [Tiny Tapeout SDK](https://github.com/TinyTapeout/tt-micropython-firmware/tree/d485c7a9986336024e35d790568fca33be1c5665/src/ttboard);
**it has not run on a board yet.**

## Pins and resources

| Signal | ASIC pin | TTDBv3 GPIO |
|---|---|---|
| write IO0..IO3 | `ui[2]..ui[5]` | 19..22 |
| read IO0..IO3 | `uo[4]..uo[7]` | 37..40 |
| SCK | `ui[6]` | 23 |
| CS | `ui[7]` | 24 (software) |

Each four-pin group must be consecutive GPIOs, in either order. The nine PIO
pins must fit the PIO block's GPIO window (GPIO0–31 or 16–47); on TTDBv3 that
means base 16, which must be set before anything else uses that block.
`from_tt()` reads the installed SDK's pin map and rejects layouts it can't
drive before touching any GPIO. The caller owns the choice of state machine;
the transport claims two free DMA channels and checks that the machine is
idle.

```python
import bringup, rp2
from quad_spi import QuadSPI

pe = bringup.setup()
rp2.PIO(1).gpio_base(16)                 # TTDBv3; PIO1 must be otherwise unused
bus = QuadSPI.from_tt(pe.tt, sm_id=5)
try:
    bus.enable_quad()                    # serial HOSTCFG write, then a quad ID check
except BaseException:
    bus.close()
    raise
pe.transport = bus
assert pe.read(0x800) == 0x50450002
```

While attached, nothing else may drive `ui_in`, remux these GPIOs or change
the clocks (checked before each transfer). After a chip reset, keep CS high
for five clocks, call `enable_quad()` again and reset the driver's pin-owner
shadow (`pe.set_pin_owners({})`). `close()` stops the machine and frees the
DMA channels; restore the SDK pin muxes before bit-banging again.

## Transfers

SCK defaults to half the project clock (24 MHz at 48 MHz), four PIO
instructions per period; divider rounding only ever slows it. Reads keep the
eight dummy clocks. Bursts are 1–4096 words. A completion marker after the
last falling edge tells the transport when to release CS. The PIO stalls SCK
when its FIFOs run empty or full, so the configured rate is an upper bound.

On a timeout or DMA error the transport aborts both channels, deselects the
chip and reinitializes the machine. **A partial write may already have
landed**, so side-effecting transfers are not retried automatically.

## Tests

* `test/model/test_pio_transport.py`: wire packing, clock-divider limits,
  DMA allocation and abort paths, SDK pin mapping (DMA and PIO are fakes).
* `test_pio.py` (cocotb): the compiled PIO program, instruction by
  instruction, drives the chip's real SPI pins: registers, FIFO words, burst
  reads, every pin order, and reset. RP2 input synchronizers and pad delays
  are not modeled.

`test/pio_program.json` pins the compiled program to the PIO source and the
assembler's hash; `test/compile_pio.py --rp2-source <v1.26.0 rp2.py>`
regenerates it.

Still to measure on a board: throughput and sampling margin.
