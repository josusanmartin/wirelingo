# protoemu — a programmable protocol emulator ASIC

An entry for the [Jane Street protocol emulator ASIC competition](https://blog.janestreet.com/protocol-emulator-asic-competition/),
targeting IHP SG13CMOS5L through Tiny Tapeout (6x4 tiles).

Protocols are programs, not peripherals. The chip has **two small cores built
for bit-banging**, each with its own SRAM bank of 256 32-bit instructions,
FIFOs to the host and a clock divider. A host (the TT demo board's RP2350)
loads firmware over SPI and exchanges data through the FIFOs. The RTL is
parameterised up to four cores and four banks. Two is what routes on this
tile's single horizontal routing layer ([docs/physical.md](docs/physical.md)).

The ISA is inspired by the RP2040's PIO, with the additions that real
protocols kept asking for:

- **Room for real programs:** 256 instructions per bank (PIO: 32 shared), and
  banks can be reassigned between cores.
- **Registers and arithmetic:** 16-bit R0–R3, a 16-bit immediate load, an ALU
  with Z/C flags.
- **WAITs that can't hang and don't miss edges:** any WAIT can time out; it can
  wait on a rise, fall or any edge, caught at full clock resolution even when
  the core ticks slowly; and it can be qualified by a second pin ("SDA falls
  while SCL is high" is one instruction).
- **Pins:** per-pin open drain, `PIN p, hi/lo/toggle/hiz` on any pin, 8 virtual
  pins as wires between cores, per-pin ownership.
- **A CRC tap** (any polynomial up to 16 bits) fed by the shift registers, and
  **FIFO join** (RXF as a second TX stage) for line rates above what the SPI
  host port can stream.

**Reviewers:** [docs/guide.md](docs/guide.md) maps each claim to its evidence
and the command that reproduces it.

[docs/isa.md](docs/isa.md) is the normative spec. The RTL, the golden model
and the assembler implement it, and are checked against each other
([docs/verification.md](docs/verification.md)).

## What runs on it

Each program talks to an independent device model that sees only the pins.
"RTL" = loaded and exercised through the real SPI host port on the RTL (and at
gate level); "model" = on the cycle-accurate model, which is lockstep-checked
against the RTL.

| Firmware | What it does | Limits | Verified on |
|---|---|---|---|
| `uart_tx`, `uart_rx` | 8N1, 8 ticks per bit (fractional divider) | RX tolerates a ±3 % sender, back to back; F0 on a framing error | RTL; ±3 % and back-to-back on the model |
| `spi_master` | mode 0, MSB first, 8-bit frames, f_SCK = f_tick / 4 | chip select by the host or a wrapper | RTL |
| `i2c_master` | open drain, clock stretching, repeated START; **every SCL release times out** (F1, reply 0x100, bus released) | 4 + 4 ticks per SCL period | RTL; stuck-SCL in every phase on the model |
| `usb_ls_rx` + `usb_ls_tx` (2 cores) | low-speed device: NRZI, stuffing, CRC16 check via the CRC tap, **token filter** (address, endpoint, CRC5), ACK/NAK within the turnaround, framed DATA records (good or bad), staged IN data | endpoint 0; the host software does the requests and data toggles | RTL (SETUP/ACK, foreign address, NAK, IN data); **enumeration**, lost ACK, ±2.5 % bit rate on the model |
| `eth10_tx` | **10BASE-T TX** at 40 MHz (4 clocks per bit): Manchester on a TD+/TD− pair, preamble/SFD in-core, TP_IDL, 16 ms link pulses | up to 1518-byte frames (64 bytes fit the FIFOs; longer ones stream through the host port) | RTL (64-byte frame, FCS decoded); streaming bound and link pulses on the model |
| `can_tx`, `can_rx` | CAN 2.0A: stuffing, CRC15 via the CRC tap, arbitration, ACK; RX edge resync, stuff errors detected (error flag sent) | frames, not a full controller (no error counters or bus-off) | model, incl. a TX→RX link between the two cores |
| `swd_probe` | ARM SWD engine: request parity, ACK, DPIDR, register writes | | model |
| `jtag` | IEEE 1149.1 shift engine (IDCODE via Shift-DR) | | model |
| `onewire` | 1-Wire master: reset/presence via WAIT timeout, READ ROM | | model |
| `ps2_device` | PS/2 device, odd parity from the CRC tap | | model |
| `ws2812` | NeoPixel driver, datasheet timing windows | | model |
| `i2c_sniff` | passive I2C monitor (START/STOP/bytes), beside the master on the other core | | model |

10BASE-T RX is not attempted: at 40–50 MHz with a 2-flop input synchronizer,
edge-timed Manchester sampling leaves 0–1 clocks between the bit-boundary edge
and the sample point, below the ±13.5 ns jitter 802.3 allows.

## Verification in one paragraph

A cycle-accurate Python model and the RTL run constrained-random programs,
pin stimulus and host traffic side by side, compared every cycle (registers,
WAIT trackers, FIFO levels and heads, pins, flags, bus reads), in the tapeout
configuration and the 4-core build. SymbiYosys proves the FIFO pair (with
join) against reference queues at depths 4 and 8, SPI-slave bus safety, and
core safety invariants. Firmware is developed against device models on the
model, and the key cases repeat on the RTL through the SPI port and on the
routed netlist. Deliberately injected bugs are caught. Details, limits and
the bugs this found: [docs/verification.md](docs/verification.md).

```
python3 -m venv .venv && .venv/bin/pip install -r test/requirements.txt
export PATH=$HOME/tools/oss-cad-suite/bin:$PWD/.venv/bin:$PATH   # iverilog, yosys, sby
cd test && make                                                  # top level through the SPI port
LOCKSTEP_SEEDS=200 PE_NCORES=2 PE_NBANKS=2 PE_FIFO_LOG2=3 make TB=chip   # lockstep, tapeout config
cd .. && PYTHONPATH=tools .venv/bin/python -m pytest test/model  # firmware on the model
make -C formal                                                   # proofs
```

## Physical design (IHP SG13CMOS5L, 6x4 tiles)

* 2 cores, 2 × 256x32 SRAM macros (bottom corners, pins facing the logic),
  8-deep FIFOs; 40 % standard-cell utilization.
* **Run final5 (the current RTL) signs off clean:** 0 routing violations,
  0 KLayout DRC, LVS clean, 0 antenna violations. With PVT-matched SRAM
  timing views, 50 MHz is met at the typical corner (+3.12 ns setup,
  +0.21 ns hold) and hold is met at the fast corner (+0.05 ns). The slow
  corner reaches about 37 MHz (−6.9 ns at 50 MHz).
* All 9 top-level tests pass on the routed netlist (`flow/gl_test.sh`).
* Remaining electrical exceptions at typ (3 slew, 11 capacitance, mostly the
  SRAM output pins) are listed in [docs/physical.md](docs/physical.md).

## Layout

| Path | What |
|------|------|
| `docs/` | `isa.md` (spec), `asm.md` (assembler), `verification.md`, `physical.md`, `info.md` (TT datasheet) |
| `src/` | RTL: `pe_core`, `pe_fifos`/`pe_fifo`, `pe_chip` (cores + IMEM + pins), `pe_imem`, `pe_hostport` (SPI, 1-bit or quad), TT top `project.v` |
| `tools/pe/` | `isa`, `asm` (assembler), `sim` (golden model), `host` (register writes), `modelhost`, `randprog`, USB/CAN helpers |
| `tools/micropython/` | demo-board driver and generated firmware bundle |
| `firmware/` | `.pasm` programs |
| `test/` | cocotb top-level and lockstep tests; `test/model/` pytest firmware tests |
| `formal/` | SymbiYosys proofs |
| `flow/`, `librelane_plugin_protoemu.py` | hardening config generator, SRAM power stripes, experiment and GL scripts |
