# Wirelingo — a programmable protocol emulator ASIC

*(Formerly protoemu; code identifiers such as the Tiny Tapeout module
`tt_um_josusanmartin_protoemu` and `tools/micropython/protoemu.py` keep the old
name until the next re-harden.)*

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

* **Release candidate: run fo6** (software `d916d40`), passing the strict
  release gate with no waiver. 50 MHz is met at all three corners, slow
  included; hold is met everywhere (fast corner +0.095 ns); DRC, LVS and
  antenna are clean, with no slew, capacitance or fanout violation at any
  corner. A clean checkout rebuilt on a 4-CPU machine reproduces its netlist
  and GDS bit for bit. Hash-bound receipts: Tiny Tapeout precheck, all 29
  top-level tests at gate level, and timing-annotated (SDF) gate level at all
  three corners ([docs/release-evidence.md](docs/release-evidence.md)). The
  last exception of the previous candidate, t48ba (antenna diodes taking a
  net to 11 loads against the IHP cells' fanout limit of 8), is removed by a
  flow step that buffers such nets after routing ([docs/physical.md](docs/physical.md)).
* Running on a Kria KV260 FPGA at 50 MHz: firmware loads and runs, UART
  crosses real pins in both directions, and the quad host port moves
  193.6 Mbit/s, matching simulation ([fpga/kv260](fpga/kv260/README.md)).
  At 48 MHz the autonomous USB device enumerates, at low and full speed,
  against a host model's waveform replayed on its pads by the FPGA's pattern
  generator, with every device edge at the clock the cycle model gives (no PC
  and no USB PHY in the loop).
* A **full-speed USB (12 Mbit/s) device runs on one 48 MHz core**, with
  handshakes 10.5 clocks after the host's packet (spec: 8–26; g4: 11.5), in RTL and
  gate-level simulation of the signed-off netlist
  ([docs/usb-full-speed.md](docs/usb-full-speed.md)); the chip meets 50 MHz,
  so 48 MHz, at every corner. With no host software after loading, one core
  also enumerates as a USB device on its own, low or full speed
  ([docs/usb-enumeration.md](docs/usb-enumeration.md)).
* Measured against the RP2040 PIO's reference programs, including where PIO
  is better ([docs/pio-comparison.md](docs/pio-comparison.md)).
* Not yet on the Tiny Tapeout demo board or silicon.
* **Post-freeze protocol challenge:** with the RTL frozen (tag
  `rc-93c2137-t48ba`; `src/` unchanged since), three rounds of protocols were
  added in firmware alone. Round one: DMX512, LIN (commander and a
  self-synchronising responder), NEC infrared (38 kHz carrier and receiver),
  I2S, Wiegand, DShot600 (bidirectional too). Round two: DALI, Modbus RTU,
  MIDI (receive and transmit), a quadrature encoder counter. Round three:
  HDMI-CEC (multi-master arbitration), ISO 7816-3 T=0 smart card, SMBus with
  PEC, RC-5 infrared. Each is checked by the static timing checker, pins-only
  models, sigrok's decoders where one exists and an RTL test through the SPI
  port. All three rounds also ran on the KV260 FPGA build of the RTL,
  against the other core or the FPGA's pattern generator, with results equal
  to the cycle model's ([docs/post-freeze-protocols.md](docs/post-freeze-protocols.md),
  with the 25 ISA limitations they ran into).
* **v2** (signed off as run v2c): four small additions over g4, each in the spec, RTL, model, assembler, lockstep, formal where an
  invariant applies, and a firmware that uses it. The line unit's NRZI sample
  point moves half a clock earlier (full-speed USB in RTL simulation: 24/24
  packets at 28 ns edge-to-edge distortion, g4 11/24; v2c meets 48 MHz at the
  typical corner, and its slow corner reaches about 46 MHz); `SYNC_BYPASS` reads chosen pad inputs through one
  synchroniser flop (SPI master at 4 clocks per SCK, like PIO, if the pads and
  the device answer within 2 clocks); WAIT kind 7 waits until any of 8 pins
  differs from a register (logic analyzer timestamps to the tick instead of
  5 ticks); CALL/RET with one link register (`i2c_device` 98 → 88
  instructions). docs/isa.md has the details.

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
  while SCL is high" is one instruction). v2: or wait until any of 8 pins
  differs from a register, with the timeout counter as the timestamp.
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
| `spi_master` | mode 0, MSB first, 8-bit frames, f_SCK = f_tick / 4 | chip select by the host or a wrapper | RTL; KV260 against pe_lab's SPI device (DELAY sweeps at CLKDIV 1-3) |
| `spi_master_fast` (v2) | the same at 4 clocks per SCK (12.5 MHz at 50 MHz): MISO's pad in `SYNC_BYPASS` (one synchroniser flop), sampled at the SCK rise | the device must answer within 2 clocks of SCK falling, pad and board delays included | RTL against a device model; delay sweep on the model |
| `i2c_master` | open drain, clock stretching, repeated START; **every SCL release times out** (F1, reply 0x100, bus released) | 4 + 4 ticks per SCL period | RTL; stuck-SCL in every phase on the model |
| `usb_ls_rx` + `usb_ls_tx` (2 cores) | low-speed device: NRZI, stuffing, CRC16 check via the CRC tap, **token filter** (address, endpoint, CRC5), ACK/NAK within the turnaround, framed DATA records (good or bad), staged IN data | vendor endpoint 0; host software handles requests/toggles | Concurrent RTL service: descriptors, address changes, lost ACK, malformed packets, duplicate OUT and reset; ±2.5 % bit rate on the model |
| `usb_ls_dev` (**1 core**, line unit) | the same device on one core: the line unit does NRZI, stuffing, bit timing and the EOP; the core decodes packets, filters tokens, checks CRC16 bit by bit through the CRC tap, answers | same host protocol (pe.usbdev) | Same concurrent RTL service/error/reset cases; model enumeration and lockstep line unit; KV260: enumeration against a replayed host, every pad edge equal to the model's |
| `usb_fs_dev` (**1 core**, line unit) | **full-speed USB (12 Mbit/s)** at 48 MHz, 4 clocks per bit: the line unit does NRZI, stuffing and clock recovery; the core does PID, CRC16, address filtering and the handshakes within the bus turnaround ([docs/usb-full-speed.md](docs/usb-full-speed.md)) | exactly 48 MHz; packets up to 16 (OUT) / 24 (IN) bytes from the FIFOs, 64 with the host streaming over quad SPI; receive jitter tolerance about 31 ns edge to edge, 12 ns past the spec's 18.5 (g4: about 21 ns) | RTL and gate-level (signed-off netlist): SETUP/DATA0, ACK at 10.5 clocks (spec 8–26; g4 11.5), NAK, staged DATA1, bad CRC/PID/stuffing ignored; enumeration on the model; KV260 at 48 MHz against a replayed host, every pad edge equal to the model's |
| `usb_enum` (**1 core**, line unit) | **autonomous USB enumeration**, low or full speed ([docs/usb-enumeration.md](docs/usb-enumeration.md)): descriptors in instruction memory, standard requests, DATA toggles, lost-ACK retries, SET_ADDRESS after its status stage, bus reset, STALL for the rest; the host only loads it | endpoint 0 only; 246 of 256 words; full speed at exactly 48 MHz | model (both speeds: offsets, jitter, faults, resets, STALLs) and RTL with the host port idle; sigrok decodes every packet; KV260 at 48 MHz, both speeds, against a host replayed by pe_lab's pattern generator: every device pad edge at the model's clock (no PC, no PHY) |
| `eth10_rx` (core 0) | **10BASE-T RX** through the line unit: Manchester clock recovery, frames with preamble/SFD to the host (24 bits per FIFO word + a bit-count trailer) | 802.3's ±13.5 ns edge jitter at 50 MHz (see below) | RTL: 64/128/1518-byte frames, jitter, quad-SPI draining, overflow/recovery; model: TX→RX and external sender; KV260: generator frames (sync and 45.93 MHz clock) and from eth10_tx |
| `eth10_tx` | **10BASE-T TX** at 40 MHz (4 clocks per bit): Manchester on a TD+/TD− pair, preamble/SFD in-core, TP_IDL, 16 ms link pulses | up to 1518-byte frames (64 bytes fit the FIFOs; longer ones stream through the host port) | RTL (64-byte frame, FCS decoded); streaming bound and link pulses on the model; KV260 at 50 MHz (CLKDIV 1.25), sampled every clock |
| `can_tx`, `can_rx` | CAN 2.0A: stuffing, CRC15 via the CRC tap, arbitration, ACK; RX edge resync, stuff errors detected (error flag sent) | frames, not a full controller (no error counters or bus-off) | model, incl. a TX→RX link between the two cores; KV260 (both cores on a wired-AND line; generator frames and pre-timed nodes); sigrok `can` |
| `swd_probe` | ARM SWD engine: request parity, ACK, DPIDR, register writes | | model |
| `jtag` | IEEE 1149.1 shift engine (IDCODE via Shift-DR) | | model; KV260 (pre-timed TAP: IDCODE, BYPASS); sigrok `jtag` |
| `onewire` | 1-Wire master: reset/presence via WAIT timeout, READ ROM | | model; KV260 (pre-timed slave); sigrok `onewire_network` |
| `ps2_device` | PS/2 device, odd parity from the CRC tap | | model; KV260 (incl. a host inhibit before a frame) |
| `ws2812` | NeoPixel driver, datasheet timing windows | | model; KV260 |
| `i2c_sniff` | passive I2C monitor (START/STOP/bytes), beside the master on the other core | | model; KV260 (generator bus at 0.1-1 MHz; beside i2c_master) |
| `la_capture` + `i2c_device` (2 cores) | **reverse engineering** ([docs/reverse-engineering.md](docs/reverse-engineering.md)): an 8-channel logic analyzer (timestamped change records; `pe.la` makes a VCD, an I2C transcript and the device's register image), then an I2C target at the decoded address whose registers are host software: a clone of the captured EEPROM, or a man in the middle | capture (v2, WAIT kind 7): timestamps to the tick, a record per ≥ 7 ticks, sustained rate bounded by the host's draining (1-bit SPI at 25 MHz: 625 k edges/s; quad, 32-word bursts: 5.6 M); device: every read byte is a host round trip under clock stretching | RTL: capture → decode → clone → man in the middle through the SPI port, rate sweep; capture timing and device response on the model |
| `dmx512_tx` (post-freeze) | **DMX512** (E1.11) lighting: BREAK 176 us, MAB 12 us, up to 513 slots at 250 kbit/s streamed from the FIFO, short packets padded to the 1204 us minimum | transmit only; the host refreshes within 1 s | RTL (timing at 50 MHz: every edge on the 4 us grid; full universes); sigrok `dmx512` |
| `lin_commander` + `lin_responder` (post-freeze, 2 cores) | **LIN** 2.x: break/sync/PID (parity computed in firmware), publish or subscribe, classic/enhanced checksums; the responder measures the bit rate on every sync field | no schedule table, sleep or bit-error check | RTL (both on one bus); model: commander +-10 %, responder clock +-14 %, response within 0.44 % of the commander; sigrok `lin` |
| `nec_tx` + `nec_rx` (post-freeze) | **NEC infrared**: 38 kHz carrier generated in firmware, frames/extended/repeat codes 108 ms apart; receiver decodes a receiver module's output | NEC only | RTL (TX -> receiver-module model -> RX); model: +-10 % senders, +-200 us burst distortion; sigrok `ir_nec` on the carrier and the module output |
| `i2s_tx` (post-freeze) | **I2S** 16-bit stereo; on underrun the clocks keep running and the frame is silence | transmit only, no MCLK | RTL; sigrok `i2s` |
| `wiegand_tx` + `wiegand_rx` (post-freeze) | **Wiegand** card-reader interface: parity computed (TX) and checked (RX, 26/34-bit) with the CRC tap; RX catches both lines with one WAIT | TX <= 32 data bits | RTL (TX -> RX); model: 20-100 us pulses, 0.2-5 ms intervals, glitches; sigrok `wiegand` |
| `dshot600`, `dshot_bidir` (post-freeze) | **DShot600** ESC protocol, checksum in firmware, last value repeated; **bidirectional**: the ESC's GCR telemetry received on the same wire at 750 kbit/s, every edge timed | telemetry GCR decoded by the host | RTL; model: edges exact at 48 MHz, +-1 clock at 50 MHz; telemetry with ESC clock +-15 %; sigrok `pwm` (no DShot decoder exists) |
| `dali_ctrl` (post-freeze, round 2) | **DALI** (IEC 62386) controller: Manchester forward frames, backward frames decoded against the standard's timing tables, collisions flagged | controller only | RTL; sigrok `dali`; KV260 |
| `modbus_rtu` (post-freeze, round 2) | **Modbus RTU** client: CRC-16 through the CRC tap, even/odd parity, 3.5-character framing, RS-485 driver enable | client only | RTL; sigrok `modbus`; KV260 |
| `midi_rx`, `midi_tx` (post-freeze, round 2) | **MIDI**: parser with running status and real-time bytes anywhere; transmitter with running status | a full port uses both cores | RTL; sigrok `midi`; KV260 |
| `quad_enc` (post-freeze, round 2) | **quadrature encoder** counter, 20 words | exact to a step every 14 (forward) / 15 (back) clocks; faster steps flagged | RTL; sigrok `graycode`; KV260 |
| `cec` (post-freeze, round 3) | **HDMI-CEC**: open-drain multi-master, arbitration loss turns the sender into a listener within the bit, ACKs | one logical address; host resubmits | RTL; sigrok `cec`; KV260 |
| `iso7816` + `iso7816_clk` (post-freeze, round 3, 2 cores) | **ISO 7816-3 T=0** reader: ATR conventions, parity, error signalling and repetition both ways; card clock on the second core | no PPS/T=1; WT fixed by define | RTL; sigrok `uart`; KV260 |
| `smbus_host` (post-freeze, round 3) | **SMBus** host, all protocols, PEC (CRC-8) over sent and received bytes, 25 ms clock-low timeout | host side only | RTL; sigrok `i2c` + PEC; KV260 |
| `rc5_tx`, `rc5_rx` (post-freeze, round 3) | **RC-5** infrared: 36 kHz carrier under bi-phase, toggle bit; receiver tolerates a remote ±10 % | no RC-6 | RTL; sigrok `ir_rc5`; KV260 |

10BASE-T RX (`eth10_rx`, with the line unit) decodes frames from an external
sender with 802.3's ±13.5 ns of edge jitter at 50 MHz and ±200 ppm, including
the worst case where every edge sits at ±13.5 ns (test_eth10's sweep: 30/30
frames at ±15 ns, 9/30 at ±18 ns). The unit samples the RX pin at both clock
edges and tracks the sender's phase with a digital loop (docs/isa.md §4.7).
The first version measured each edge against the previous one at one sample
per clock and managed ±5 ns.

## Additional firmware and host workflows

These optional workloads use the existing RTL. Each guide supplies its loading
contract, pin/flag allocation and reproduction commands. Choose a complete
workload before loading; the resource allocations are not interchangeable.
Evidence below applies to the named cases and configurations.

| Workload | Use | Evidence and limits |
|---|---|---|
| [Packed SPI and one-shot preload](docs/spi-packed-host.md) | Pack FIFO words; preload a 1–32-byte mode-0 transfer, then drain and rearm through the host | Model tests and documented serialized-host RTL cases; one-shot mode is core 0 only; demo-board helpers in `bringup.py`, model-tested only |
| [Declared UART capture](docs/uart-capture-host.md) | Capture 1–32 bytes with framing status and a partial final word | Model tests and a serialized-host RTL regression with paid drain/rearm; length must be known before arming; `bringup.uart_capture()` for the demo board, model-tested only |
| [USB plus UART peer capture](docs/usb-uart-coexist.md) | Enumerate on core 0 while core 1 captures UART to its host FIFO | Two finite model cases; this mode does not forward the captured bytes through USB |
| [One-byte USB/UART mailbox](docs/usb-uart-byte.md) | Return one retained UART byte through a vendor USB EP0 request | Finite low-speed model test; one pins-only scenario also RTL-tested (5 frames with a bad STOP, retry, STALL when empty), identical to the model; READY admits each frame and the USB host supplies polling/ACK service; `bringup.usb_uart()` loads it from the demo board with RX on uio6, model-tested only |
| [Four-byte USB/UART mailbox](docs/usb-uart-batch4.md) | Retain four UART bytes through USB retries before admitting the next group | Finite low-speed model test with a measured publication deadline; UART pauses until ACK, sustained forwarding unmeasured |
| [Overlapping UART/USB reports](docs/usb-uart-overlap4.md) | Receive the next four UART bytes while USB retains the current report | Model: 109,050–121,059 baud at every START phase; 115,200-baud line rate kept when the host releases each report within 346 µs of its last STOP (tested over 12–26 reports); slower hosts throttle via READY without loss. RTL-tested on one pins-only scenario (6 reports, bad STOP, READY holds, retry, STALL when empty), identical to the model clock for clock; `bringup.usb_uart()` loads it from the demo board with RX on uio6, model-tested only |
| [Sparse UART/USB reports](docs/usb-uart-envelope3.md) | Send 1–3 UART bytes per four-byte report, sealing partial groups after an idle timer | Finite model scenario; RTL-tested on one pins-only scenario (4 reports incl. timer-sealed partials, bad STOP, READY hold, retry, STALL when empty), identical to the model clock for clock; `bringup.usb_uart()` loads it from the demo board with RX on uio6, model-tested only |

The [current Lean guide](docs/lean-verification.md) documents manual proofs and
finite comparisons against actual RTL, including normally fetched pin ownership,
RX autopush and UART IN1/partial PUSH. Each check names its assumptions and scope;
these results do not establish universal equivalence of the whole chip.

## Verification in one paragraph

A cycle-accurate Python model and the RTL run constrained-random programs,
pin stimulus and host traffic side by side, compared every cycle (registers,
WAIT trackers, FIFO levels and heads, pins, flags, bus reads), in the tapeout
configuration and the 4-core build. SymbiYosys proves the FIFO pair (with
join) against reference queues at depths 4 and 8, SPI-slave bus safety, and
core safety invariants. Firmware is developed against device models on the
model, and the key cases repeat on the RTL through the SPI port. The
signed-off netlist passes the same top-level tests at gate level
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
* **Run fo6 passes signoff** (`flow/signoff.py`): 0 routing violations,
  0 KLayout DRC, LVS clean, 0 antenna violations. Setup is met at 50 MHz at
  all three corners, hold everywhere (+0.19 / +0.36 / +0.095 ns typ / slow /
  fast), and no slew or capacitance violation at any corner. 47 % standard-cell
  utilization, 53 % with the macros; Metal3 at 64 %. What closed the slow
  corner (from about 46 MHz in run v2c) was the clock tree, not the RTL: the
  SRAMs are clocked from the clock root so their reads launch earlier, the
  pin registers' clocks are delayed, clock branches respect the cells' fanout
  limit, and repair runs with electrical margins.
* The stricter `flow/release_check.py` (zero electrical exceptions, actual
  50 MHz timing at every corner, receipts for this exact netlist) passes with
  no waiver. Detailed routing's antenna repair runs after every resizer step
  and its diodes count as loads; the project's routing step re-routes nets
  they take over the fanout limit behind one buffer (t48ba, the previous
  candidate, had one such net).
* Its netlist passes all 29 top-level tests at gate level (main suite 25,
  compiled PIO 2, Ethernet capture 2), and the SDF gate-level sets pass at
  typ, slow and fast at the claimed clocks, with an overclocked control that
  must fail; autonomous full-speed USB enumeration runs on it with real
  delays. The GDS passes Tiny Tapeout's precheck.
* Routing is the constraint: one horizontal layer (Metal3) at ≈ 63 %. Global
  routing's reserve for detailed routing (`GRT_ADJUSTMENT` 0.10) was what made
  the current RTL routable ([docs/physical.md](docs/physical.md)).
* [External review](docs/review.md): the bugs an independent review found and
  how they were fixed and tested. `flow/gen_config.py --check` verifies that
  `src/config.json` is the generator's (fo6's settings).
* [Optional board transport](docs/board-transport.md): RP2350 PIO/DMA quad
  SPI; board throughput and sampling margin remain unmeasured.
* [Ethernet receive bench](docs/eth10-receiver.md): a bounded capture service
  and a proposed comparator/squelch circuit, not yet built.

## Layout

| Path | What |
|------|------|
| `docs/` | `isa.md` (spec), `asm.md` (assembler), `methodology.md` (AI-assisted design and verification), `verification.md`, `physical.md`, `info.md` (TT datasheet) |
| `src/` | RTL: `pe_core`, `pe_fifos`/`pe_fifo`, `pe_chip` (cores + IMEM + pins), `pe_imem`, `pe_hostport` (SPI, 1-bit or quad), TT top `project.v` |
| `tools/pe/` | `isa`, `asm` (assembler), `sim` (golden model), `host` (register writes), `modelhost`, `randprog`, USB/CAN helpers, `postfreeze` (host side and pins-only models of the post-freeze protocols) |
| `tools/micropython/` | demo-board driver and generated firmware bundle |
| `firmware/` | `.pasm` programs |
| `test/` | cocotb top-level and lockstep tests; `test/model/` pytest firmware tests |
| `formal/` | SymbiYosys proofs |
| `fpga/kv260/` | Kria KV260 build, app packaging, fabric SPI master and hardware bring-up tests |
| `flow/`, `librelane_plugin_protoemu.py` | hardening config generator, SRAM power stripes, experiment and GL scripts |
