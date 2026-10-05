# protoemu — a programmable protocol emulator ASIC

An entry for the [Jane Street protocol emulator ASIC competition](https://blog.janestreet.com/protocol-emulator-asic-competition/),
targeting IHP SG13CMOS5L through Tiny Tapeout (6x4 tiles).

Protocols are programs, not peripherals. The chip has **two small cores built
for bit-banging**, each with its own SRAM bank of 256 32-bit instructions,
FIFOs to the host and a clock divider. A host (the TT demo board's RP2350)
loads firmware over SPI and exchanges data through the FIFOs. UART, SPI, I2C,
low-speed USB, 10BASE-T, CAN, SWD, JTAG, 1-Wire, PS/2 and WS2812 are all
firmware ([below](#what-runs-on-it)).

**It was designed by one person with no prior chip-design experience, with AI
agents writing the spec, RTL, firmware and tests,** and every claim backed by
evidence an agent can't talk its way past: two independent implementations
of the spec compared cycle by cycle, formal proofs, mutation testing,
gate-level simulation, scripted physical signoff, an adversarial second agent,
and the RTL running on an FPGA. [docs/methodology.md](docs/methodology.md)
describes how, and what it caught.

**Where it stands:**

* Signed off (run g4): DRC, LVS and antenna clean, 50 MHz at the typical
  corner, hold met at every corner, Tiny Tapeout precheck passed; the netlist
  passes all 20 top-level tests at gate level ([docs/physical.md](docs/physical.md)).
* Running on a Kria KV260 FPGA at 50 MHz: firmware loads and runs, UART
  crosses real pins in both directions, and the quad host port moves
  193.6 Mbit/s, matching simulation ([fpga/kv260](fpga/kv260/README.md)).
* A **full-speed USB (12 Mbit/s) device runs on one 48 MHz core** of the
  signed-off chip, with handshakes 11.5 clocks after the host's packet
  (spec: 8–26), in RTL and gate-level simulation ([docs/usb-full-speed.md](docs/usb-full-speed.md)).
* Measured against the RP2040 PIO's reference programs, including where PIO
  is better ([docs/pio-comparison.md](docs/pio-comparison.md)).
* Not yet on the Tiny Tapeout demo board or silicon.

## What we'd do differently from PIO

The ISA starts from the RP2040's PIO and adds what real protocols kept asking
for:

- **Room for real programs:** 256 instructions per core, in its own bank (PIO: 32
  shared).
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
- **A line unit** beside core 0: NRZI, bit stuffing, end-of-packet detection
  and Manchester decoding with a phase-tracking loop, so one core runs a whole
  USB device, low or full speed, and 10BASE-T receive meets 802.3's ±13.5 ns
  edge jitter.
- **A host link that keeps up:** quad SPI at SCK = clk, 193 Mbit/s of payload
  in simulation and 193.6 Mbit/s measured on the FPGA.

**Reviewers:** [docs/guide.md](docs/guide.md) maps each claim to its evidence
and the command that reproduces it.

[docs/isa.md](docs/isa.md) is the normative spec. The RTL, the golden model
and the assembler implement it, and are checked against each other
([docs/verification.md](docs/verification.md)).

## What runs on it

Each program talks to an independent device model that sees only the pins.
"RTL" = loaded and exercised through the real SPI host port on the RTL (the
same suites pass on the signed-off gate-level netlist). "Model" = on the
cycle-accurate model, which is lockstep-checked against the RTL.

| Firmware | What it does | Limits | Verified on |
|---|---|---|---|
| `uart_tx`, `uart_rx` | 8N1, 8 ticks per bit (fractional divider) | RX tolerates a ±3 % sender, back to back; F0 on a framing error | RTL; ±3 % and back-to-back on the model |
| `spi_master` | mode 0, MSB first, 8-bit frames, f_SCK = f_tick / 4 | chip select by the host or a wrapper | RTL |
| `i2c_master` | open drain, clock stretching, repeated START; **every SCL release times out** (F1, reply 0x100, bus released) | 4 + 4 ticks per SCL period | RTL; stuck-SCL in every phase on the model |
| `usb_ls_rx` + `usb_ls_tx` (2 cores) | low-speed device: NRZI, stuffing, CRC16 check via the CRC tap, **token filter** (address, endpoint, CRC5), ACK/NAK within the turnaround, framed DATA records (good or bad), staged IN data | vendor endpoint 0; host software handles requests/toggles | Concurrent RTL service: descriptors, address changes, lost ACK, malformed packets, duplicate OUT and reset; ±2.5 % bit rate on the model |
| `usb_ls_dev` (**1 core**, line unit) | the same device on one core: the line unit does NRZI, stuffing, bit timing and the EOP; the core decodes packets, filters tokens, checks CRC16 bit by bit through the CRC tap, answers | same host protocol (pe.usbdev) | Same concurrent RTL service/error/reset cases; model enumeration and lockstep line unit |
| `usb_fs_dev` (**1 core**, line unit) | **full-speed USB (12 Mbit/s)** at 48 MHz, 4 clocks per bit: the line unit does NRZI, stuffing and clock recovery; the core does PID, CRC16, address filtering and the handshakes within the bus turnaround ([docs/usb-full-speed.md](docs/usb-full-speed.md)) | exactly 48 MHz; packets up to 16 (OUT) / 24 (IN) bytes from the FIFOs, 64 with the host streaming over quad SPI; receive jitter margin about 2 ns past the spec | RTL and gate-level (signed-off netlist): SETUP/DATA0, ACK at 11.5 clocks (spec 8–26), NAK, staged DATA1, bad CRC/PID/stuffing ignored; enumeration on the model |
| `eth10_rx` (core 0) | **10BASE-T RX** through the line unit: Manchester clock recovery, frames with preamble/SFD to the host (24 bits per FIFO word + a bit-count trailer) | 802.3's ±13.5 ns edge jitter at 50 MHz (see below) | RTL: 64/128/1518-byte frames, jitter, quad-SPI draining, overflow/recovery; model: TX→RX and external sender |
| `eth10_tx` | **10BASE-T TX** at 40 MHz (4 clocks per bit): Manchester on a TD+/TD− pair, preamble/SFD in-core, TP_IDL, 16 ms link pulses | up to 1518-byte frames (64 bytes fit the FIFOs; longer ones stream through the host port) | RTL (64-byte frame, FCS decoded); streaming bound and link pulses on the model |
| `can_tx`, `can_rx` | CAN 2.0A: stuffing, CRC15 via the CRC tap, arbitration, ACK; RX edge resync, stuff errors detected (error flag sent) | frames, not a full controller (no error counters or bus-off) | model, incl. a TX→RX link between the two cores |
| `swd_probe` | ARM SWD engine: request parity, ACK, DPIDR, register writes | | model |
| `jtag` | IEEE 1149.1 shift engine (IDCODE via Shift-DR) | | model |
| `onewire` | 1-Wire master: reset/presence via WAIT timeout, READ ROM | | model |
| `ps2_device` | PS/2 device, odd parity from the CRC tap | | model |
| `ws2812` | NeoPixel driver, datasheet timing windows | | model |
| `i2c_sniff` | passive I2C monitor (START/STOP/bytes), beside the master on the other core | | model |
| `la_capture` + `i2c_device` (2 cores) | **reverse engineering** ([docs/reverse-engineering.md](docs/reverse-engineering.md)): an 8-channel logic analyzer (timestamped change records; `pe.la` makes a VCD, an I2C transcript and the device's register image), then an I2C target at the decoded address whose registers are host software: a clone of the captured EEPROM, or a man in the middle | capture: 5-tick samples, edges ≥ 12 ticks apart, sustained rate bounded by the host's draining (1-bit SPI at 25 MHz: 625 k edges/s); device: every read byte is a host round trip under clock stretching | RTL: capture → decode → clone → man in the middle through the SPI port, rate sweep; capture timing and device response on the model |

10BASE-T RX (`eth10_rx`, with the line unit) decodes frames from an external
sender with 802.3's ±13.5 ns of edge jitter at 50 MHz and ±200 ppm, including
the worst case where every edge sits at ±13.5 ns (test_eth10's sweep: 30/30
frames at ±15 ns, 9/30 at ±18 ns). The unit samples the RX pin at both clock
edges and tracks the sender's phase with a digital loop (docs/isa.md §4.7).
The first version measured each edge against the previous one at one sample
per clock and managed ±5 ns.

## Verification in one paragraph

A cycle-accurate Python model and the RTL run constrained-random programs,
pin stimulus and host traffic side by side, compared every cycle (registers,
WAIT trackers, FIFO levels and heads, pins, flags, bus reads), in the tapeout
configuration and the 4-core build. SymbiYosys proves the FIFO pair (with
join) against reference queues at depths 4 and 8, SPI-slave bus safety, and
core safety invariants. Firmware is developed against device models on the
model, and the key cases repeat on the RTL through the SPI port. The
signed-off netlist passes the same 20 top-level tests at gate level
(functional, no SDF). Deliberately injected bugs are caught. Details, limits and
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
  8-deep FIFOs, the line unit and the quad host port; 44 % standard-cell
  utilization, 50 % with the macros.
* **Run g4 passes signoff** (`flow/signoff.py`): 0 routing violations,
  0 KLayout DRC, LVS clean, 0 antenna violations. 50 MHz is met at the typical
  corner, and hold is met at every corner (+0.15 / +0.30 / +0.05 ns typ / slow /
  fast). The slow corner reaches about 44 MHz (−2.88 ns at 50 MHz). Two slew
  exceptions remain at typ.
* The signed-off netlist passes all 20 top-level tests at gate level
  (`flow/gl_test.sh`: main suite 16, compiled PIO 2, Ethernet capture 2;
  functional, no SDF), and the GDS passes Tiny Tapeout's precheck
  (`tt/precheck`).
* Routing is the constraint: one horizontal layer (Metal3) at ≈ 63 %. Global
  routing's reserve for detailed routing (`GRT_ADJUSTMENT` 0.10) was what made
  the current RTL routable ([docs/physical.md](docs/physical.md)).
* [External review](docs/review.md): the bugs an independent review found and
  how they were fixed and tested. `flow/gen_config.py --check` verifies that
  `src/config.json` is the generator's (g4's settings).
* [Optional board transport](docs/board-transport.md): RP2350 PIO/DMA quad
  SPI; board throughput and sampling margin remain unmeasured.
* [Ethernet receive bench](docs/eth10-receiver.md): a bounded capture service
  and a proposed comparator/squelch circuit, not yet built.

## Layout

| Path | What |
|------|------|
| `docs/` | `isa.md` (spec), `asm.md` (assembler), `methodology.md` (AI-assisted design and verification), `verification.md`, `physical.md`, `info.md` (TT datasheet) |
| `src/` | RTL: `pe_core`, `pe_fifos`/`pe_fifo`, `pe_chip` (cores + IMEM + pins), `pe_imem`, `pe_hostport` (SPI, 1-bit or quad), TT top `project.v` |
| `tools/pe/` | `isa`, `asm` (assembler), `sim` (golden model), `host` (register writes), `modelhost`, `randprog`, USB/CAN helpers |
| `tools/micropython/` | demo-board driver and generated firmware bundle |
| `firmware/` | `.pasm` programs |
| `test/` | cocotb top-level and lockstep tests; `test/model/` pytest firmware tests |
| `formal/` | SymbiYosys proofs |
| `fpga/kv260/` | Kria KV260 build, app packaging, fabric SPI master and hardware bring-up tests |
| `flow/`, `librelane_plugin_protoemu.py` | hardening config generator, SRAM power stripes, experiment and GL scripts |
