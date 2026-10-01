# protoemu — a programmable protocol emulator ASIC

An entry for the [Jane Street protocol emulator ASIC competition](https://blog.janestreet.com/protocol-emulator-asic-competition/),
targeting IHP SG13CMOS5L through Tiny Tapeout (6x4 tiles).

Small cores specialised for bit-banging run protocols in firmware: UART, SPI,
I2C, low-speed USB, 10BASE-T TX, CAN, SWD, JTAG, 1-Wire, PS/2, WS2812 and an
I2C sniffer (two cores on the IHP tapeout; the RTL scales to four). The ISA is
PIO-inspired but removes its main limits:

- **Up to 1024 instructions** in four banks that can be reassigned between cores.
  PIO has 32.
- **Registers and arithmetic:** 16-bit R0–R3, a 16-bit immediate load, and a real
  ALU with Z/C flags.
- **Richer WAITs:** a WAIT can time out, can wait on a rise, fall or any edge
  (caught at full clock resolution), and can be qualified by a second pin.
  "SDA falls while SCL is high" is one instruction.
- **Pin conveniences:** per-pin open drain, direct `PIN p, hi/lo/toggle/hiz` on
  any pin, and 8 virtual pins that act as wires between cores.
- **A CRC tap** (any polynomial up to 16 bits) fed by the shift registers, and
  **FIFO join** (RXF as a second TX stage) for line rates above what the SPI
  host port can stream.

The full spec is in [docs/isa.md](docs/isa.md) and is normative. The RTL, the
golden model and the assembler all implement it.

## Layout

| Path | What |
|------|------|
| `docs/isa.md` | architecture, ISA, register map, host SPI protocol |
| `src/` | RTL: `pe_core` (one core), `pe_chip` (4 cores + IMEM + pins), `pe_spi_slave`, TT top in `project.v` |
| `tools/pe/` | Python: `isa` encodings, `asm` assembler, `sim` cycle-accurate golden model, `host` register-write helpers, `randprog` random program generator |
| `firmware/` | `.pasm` programs, one per protocol (table below) |
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
* **Firmware** (`firmware/`), each with a device model to talk to:

  | Protocol | Notes | Verified on |
  |---|---|---|
  | UART TX / RX | 8N1; RX start edge caught at clock resolution | RTL (SPI port) |
  | SPI master | mode 0, 2 instructions | RTL |
  | I2C master | open drain, clock stretching with timeouts, repeated START | RTL |
  | **USB low-speed device** | NRZI, bit stuffing, on-chip token recognition, ACK/NAK in the turnaround window, CRC16 check via the CRC tap; **full enumeration** | RTL + model |
  | SWD probe | ARM Serial Wire Debug engine: DPIDR read, register writes | model |
  | JTAG | IEEE 1149.1 shift engine: IDCODE via Shift-DR | model |
  | CAN 2.0A TX + RX | TX: stuffing, CRC15 from the CRC tap, arbitration, ACK check. RX: edge resync, destuffing, CRC15 check, drives ACK (±1 % bit rate). Two cores form a working CAN link on one chip | model |
  | 1-Wire master | reset/presence with WAIT timeout, READ ROM | model |
  | PS/2 device | self-clocked, odd parity from the CRC tap as a 1-bit LFSR | model |
  | WS2812 | NeoPixel timing, checked against the datasheet windows | model |
  | **10BASE-T TX** | 40 MHz, 4 clocks per bit: Manchester on a TD+/TD− side-set pair, preamble/SFD generated in-core, TP_IDL, 16 ms link pulses. FIFO join gives 16 words, so a minimum 64-byte frame needs no streaming; longer frames stream at SPI speed | RTL + model (frames with a zlib CRC-32 FCS decoded from the waveform) |
  | I2C sniffer | passive; START = `wait fall SDA if pin SCL == 1` | model (runs alongside the I2C master on another core) |

  "model" means the cycle-accurate model, which is lockstep-verified against the RTL.
  10BASE-T RX is not attempted. At 40–50 MHz with a 2-flop input synchronizer,
  edge-timed Manchester sampling leaves 0–1 clocks between the bit-boundary
  edge and the sample point, below the ±13.5 ns jitter 802.3 allows. A robust
  receiver needs oversampling hardware.
* **Physical design (IHP SG13CMOS5L, 6x4 tiles):**
  * The tapeout configuration is **2 cores, 2 × 256-word SRAM banks, 8-deep
    FIFOs and the CRC tap**, at 50 MHz typical (48 MHz for USB).
  * SRAM macros sit in the bottom corners, mirrored with pins up. Placement
    density is matched to utilization (about 30 %).
  * A full run (2 cores, 4-deep FIFOs) signed off clean: 0 DRT, 0 KLayout DRC,
    LVS clean, 0 antenna, typ timing met, and the gate-level simulation passes.
    The current RTL is being hardened (docs/physical.md).
  * Four cores don't route on this tile's single horizontal routing layer.
    [docs/physical.md](docs/physical.md) has the routability experiments that
    led here.
* **Next:**
  * Signoff and gate-level simulation of the current RTL (CTRL pipelining, FIFO join).
  * Bring-up scripts for the demo board.
  * A public repository with the TT GDS action.
