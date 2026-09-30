# protoemu — a programmable protocol emulator ASIC

An entry for the [Jane Street protocol emulator ASIC competition](https://blog.janestreet.com/protocol-emulator-asic-competition/),
targeting IHP SG13CMOS5L through Tiny Tapeout (6x4 tiles).

Four small cores, each specialised for bit-banging, run protocols in firmware:
UART, SPI and I2C today, with low-speed USB, 10BASE-T and CAN planned. The ISA
is PIO-inspired but removes its main limits:

- **Up to 1024 instructions** in four banks that can be reassigned between cores.
  PIO has 32.
- **Registers and arithmetic:** 16-bit R0–R3, a 16-bit immediate load, and a real
  ALU with Z/C flags.
- **Richer WAITs:** a WAIT can time out, can wait on a rise, fall or any edge
  (caught at full clock resolution), and can be qualified by a second pin.
  "SDA falls while SCL is high" is one instruction.
- **Pin conveniences:** per-pin open drain, direct `PIN p, hi/lo/toggle/hiz` on
  any pin, and 8 virtual pins that act as wires between cores.

The full spec is in [docs/isa.md](docs/isa.md) and is normative. The RTL, the
golden model and the assembler all implement it.

## Layout

| Path | What |
|------|------|
| `docs/isa.md` | architecture, ISA, register map, host SPI protocol |
| `src/` | RTL: `pe_core` (one core), `pe_chip` (4 cores + IMEM + pins), `pe_spi_slave`, TT top in `project.v` |
| `tools/pe/` | Python: `isa` encodings, `asm` assembler, `sim` cycle-accurate golden model, `host` register-write helpers, `randprog` random program generator |
| `firmware/` | `.pasm` programs: `uart_tx`, `uart_rx`, `spi_master`, `i2c_master`, `usb_ls_rx`, `usb_ls_tx` |
| `formal/` | SymbiYosys proofs (`make -C formal`) |
| `flow/`, `librelane_plugin_protoemu.py` | hardening helpers: SRAM power stripes, routability experiments |
| `tools/micropython/` | host driver for the TT demo board (RP2350) |
| `test/` | cocotb: `test.py` (top level, over the real SPI port, with device models), `test_lockstep.py` (RTL vs model, every register every cycle) |

## Verification

- **Lockstep:** the RTL and the Python model run the same constrained-random
  programs, random pin stimulus and random host bus traffic. Every architectural
  register, FIFO level, flag and pin is compared every cycle. Mutating the model
  is caught within about 1K cycles.
- **Protocol tests:** firmware is loaded through the SPI host port and talks to
  Python device models (UART, SPI slave, I2C EEPROM with clock stretching) that
  see only the pins.

```
python3 -m venv .venv && .venv/bin/pip install -r test/requirements.txt
export PATH=$HOME/tools/oss-cad-suite/bin:$PWD/.venv/bin:$PATH   # iverilog, yosys, verilator
cd test
make                                   # top-level tests through the SPI port (SRAM build, behavioral SRAM)
LOCKSTEP_SEEDS=200 make TB=chip        # random RTL-vs-model lockstep regression
make TB=chip IMEM=flop                 # same on the small flop IMEM (faster)
cd .. && PYTHONPATH=tools .venv/bin/python -m pytest test/model   # firmware on the model, assembler tests
make -C formal                         # SymbiYosys proofs
```

## Status

* **ISA v0.2:** the RTL, the golden model and the assembler are done.
* **Verification:**
  * RTL-vs-model lockstep across thousands of random programs, with zero mismatches.
  * SymbiYosys proofs: the FIFO against a reference queue by k-induction; SPI
    slave bus safety; core safety invariants.
* **Firmware**, all verified on the RTL through the SPI host port:
  * UART TX and RX.
  * SPI master.
  * I2C master with clock stretching and timeouts.
  * **USB low-speed device transactions** (SETUP→ACK, IN→NAK/DATA). NRZI,
    bit stuffing and CRC-carrying packets are handled in firmware, and tokens
    are recognized on-chip so the device answers within the bus turnaround.
* **Physical design:** SRAM integration and timing work, but routability is the
  constraint (a single horizontal routing layer). See
  [docs/physical.md](docs/physical.md). The architecture is being sized from
  routability experiments.
* **Next:**
  * Size the architecture to route.
  * The serial pipeline / CRC assist.
  * Demo board bring-up tooling.
