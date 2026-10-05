# Capture, understand, impersonate

Jane Street's competition post says a protocol emulator "is a useful tool for
hardware debugging and reverse engineering". This demo walks through that
workflow on two I2C lines. The two cores and pins stay the same throughout; the
firmware loaded on them changes:

```
 1. capture       master <──I2C──> EEPROM           core 0: la_capture (logic analyzer)
                                     │ SDA, SCL ──> RX records ──> host: pe.la
 2. understand    host: records -> edges -> VCD, I2C transcript, register image
 3. impersonate   master <──I2C──> core 1: i2c_device at the decoded address,
                  (EEPROM removed)          host serves the decoded image (pe.i2cdev)
                                   core 0 keeps capturing: the clone decodes the same
 4. man in the    as 3, but the host changes register 0x12 on its way to the master
    middle
```

The same pins work as a timestamping logic analyzer, a protocol decoder's front
end, and a device whose behaviour is host software. A fixed I2C
controller does one of those jobs, on its own pins.

## What runs where

| Part | File | Size |
|---|---|---|
| Capture firmware: up to 8 channels, timestamped change records | `firmware/la_capture.pasm` | 25 instructions |
| I2C target firmware: address match, ACKs, bits, clock stretching | `firmware/i2c_device.pasm` | 98 instructions |
| Records → edges, VCD, I2C events/transcript/register image; command line | `tools/pe/la.py` | host |
| Device register model (EEPROM-style pointer device, `on_read` hook) | `tools/pe/i2cdev.py` | host |
| Bit-banged I2C master and EEPROM pin models (tests) | `tools/pe/i2cbus.py` | host |

**Capture records** (one RX word per change): `[31:24]` marker, `[23:16]` the
masked channel state, `[15:0]` the sample counter. Markers: `F0` first, `FF`
change, `FE` idle (65,536 samples without a change), `FC`/`F8` change/idle
after a gap (RXF was full and the core waited for the host). Bit 31 is always
set, so the host drains with blind `READ_FIXED` bursts and drops the zeros an
empty FIFO returns. The header of `la_capture.pasm` gives the timing rules;
`pe.la.decode_records` applies them.

**Device events** (RX): `0x100|byte` address matched (`[0]` R/W), `0x200|byte`
written byte, `0x300` read request (the host pushes the next byte), `0x400`
STOP, `0x500` master NACK. The host's `Device.handle(event)` returns the bytes
to push. The firmware holds SCL low until each read byte arrives, so a slow
host only slows the bus down.

## Reproduce

```sh
# model: the whole flow; -s prints the transcript the host decoded
PYTHONPATH=tools .venv/bin/python -m pytest -q -s test/model/test_reverse_engineering.py

# keep the captured records and decode them from the command line
RE_DUMP=/tmp/re PYTHONPATH=tools .venv/bin/python -m pytest -q test/model/test_reverse_engineering.py -k impersonate
PYTHONPATH=tools .venv/bin/python -m pe.la /tmp/re/capture.txt --vcd /tmp/re/bus.vcd --i2c SDA=0,SCL=1

# RTL, everything through the SPI host port at SCK = clk/2 (1-bit)
export PATH=$HOME/tools/oss-cad-suite/bin:$PWD/.venv/bin:$PATH
cd test && make COCOTB_TEST_FILTER=test_reverse_engineering SIM_BUILD=sim_build/re COCOTB_RESULTS_FILE=re.xml
make COCOTB_TEST_FILTER=test_la_capture_rate SIM_BUILD=sim_build/re COCOTB_RESULTS_FILE=rate.xml
```

Both RTL tests also pass at gate level on run g4's signed-off netlist
(functional, no SDF; `make GATES=yes GATE_LEVEL_NETLIST=<run>/runs/wokwi/final/nl/*.nl.v
COCOTB_TEST_FILTER=…`), with the same transcript, stretching and rate figures.
The README's "20 gate-level tests" count predates them.

The RTL run (100 kHz master, the `I2cEeprom` model from `test.py`) logs:

```
captured 480 records over 2019 us:
  write to 0x50: 10
  read 8 bytes from 0x50: F1 3F A6 0D E6 28 1C 5F
  write to 0x50: 20 CA FE
  write to 0x50: 20
  read 2 bytes from 0x50: CA FE
  write to 0x51: address NACKed
clone: same answers and transcript; SCL stretched 2936 clocks waiting for the host
```

The register image the host learns from that transcript is registers
0x10–0x17 (read) and 0x20–0x21 (written). The clone serves those values and
returns 0xFF for every register the capture never showed. The model test runs
the master at 400 kHz against an independent EEPROM model (`pe.i2cbus.Eeprom`).
On the RTL the man in the middle replaces register 0x12 with 0x00. The master
reads `F1 3F 00 0D …` and core 0's capture shows the change.

## Measured limits

At 50 MHz, CLKDIV 1 (a tick is a clock; CLKDIV *n* scales every time by *n*):

| Quantity | Value | Evidence |
|---|---|---|
| Sample period | 5 ticks = 100 ns | the `la_capture.pasm` loop |
| Timestamp accuracy | within one sample: over 120 random edges on 4 channels, capture time − edge time varies by at most 4 ticks | `test_capture_timestamps` |
| Dead time after a record | 12 ticks: an edge 5–16 ticks after the previous one is recorded up to 11 ticks late; edges < 5 ticks apart can share a record, and a pulse < 5 ticks can be missed | firmware timing, same test (edges ≥ 17 apart) |
| Firmware edge-rate bound | periodic edges ≥ 12 ticks apart are all captured (4.17 M edges/s); at 11 edges are lost | model sweep |
| Burst | 8 records in RXF (+1 waiting in the ISR) | `test_capture_gap_when_host_stops_draining` |
| Sustained rate, 1-bit SPI, SCK = 25 MHz | gap-free with an edge every 80 clocks (625 k edges/s); gaps at 70 | `test_la_capture_rate` (RTL) |
| Sustained rate, quad SPI, SCK = 50 MHz | gap-free with an edge every 12 clocks, edges lost at 11: the firmware bound, not the port | `test_la_capture_rate` (RTL) |
| Idle cost | one record per 65,536 samples (6.55 ms): about 610 bytes/s | `test_capture_timestamps` (a 6.8 ms gap) |
| I2C at 100 kHz | 480 records in 2.02 ms ≈ 240 k records/s average, 40 % of the 1-bit drain bound | `test_reverse_engineering` |
| Device response | SDA changes 3–10 clocks after an SCL fall (10 = the address ACK); read stretch starts after 4 clocks, lasts ≥ 22 | `test_device_response_times` |
| Device host latency (RTL) | 2936 clocks of stretching over 10 read bytes, ≈ 6 µs per byte with the 1-bit polling loop | `test_reverse_engineering` |

So I2C at 400 kHz (about 1 M records/s) needs the quad port or a host that
drains faster than 1-bit SPI at 25 MHz. The model test runs 400 kHz with a host
that drains every cycle.

## Limitations

* **ISA: a WAIT watches one pin.** The capture cannot wait for "any change on
  these pins" with a timeout, which would timestamp at clock resolution and
  get the timestamp from the timeout counter. It polls instead (`mov`, `and`,
  `cmp`, `jmp`, `jmp r2--`): 5-tick samples and a 12-tick handler. A
  single-pin capture could use `wait 0/1 pin p timeout r2` at one-tick
  resolution. A "wait until `(pins & mask) != Rn`" kind would fix this for any
  pin group, and WAIT kind 7 is still reserved (docs/isa.md §5.2). The chip is
  signed off, so it stays a note for a later version.
* **ISA: no data memory.** The device cannot hold a register table on chip
  (four 16-bit registers, FIFOs, no loads from IMEM). Every read byte is a host
  round trip covered by clock stretching. A master that does not allow
  stretching, or times out sooner than the host answers, cannot be served. The
  host may queue bytes ahead (the core then does not wait), but it cannot
  withdraw them when the master stops early: the CTRL FIFO clear empties both
  directions.
* **ISA: no CALL/RET**, so the address and data byte loops are separate copies
  (98 words of 256).
* **Four registers**: the capture keeps the next record's marker in the ISR
  (the masked state, last state, counter and channel mask take R0–R3).
* The device emulates one 7-bit address. It has no 10-bit addressing, general
  call or spike filter (I2C fast mode asks for 50 ns suppression; the capture
  and the device both see any glitch that lasts a sample). STOP and NACK events
  are pushed without blocking and dropped if RXF is full (the bus can't be
  stretched there); data events stretch instead.
* The clone only knows what the capture showed. The host's register model is
  EEPROM-style (pointer, auto-increment); other devices need their own
  `Device` subclass.
* The I2C decoder orders SDA and SCL changes that land in one record as a
  well-formed bus would (SCL falls before SDA changes, SDA changes before SCL
  rises). After a gap marker the times are lower bounds.
* Timing is checked in clock cycles against behavioural pin models on the
  RTL and the model, not against I2C's electrical specification. There is no
  MicroPython service for the demo board yet; the host code (`pe.la`,
  `pe.i2cdev`) is CPython, and the RTL tests drive it through a bit-banged SPI
  port model.
