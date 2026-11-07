# Wirelingo in two pages

Wirelingo (repository name protoemu) is an entry for the
[Jane Street protocol emulator ASIC competition](https://blog.janestreet.com/protocol-emulator-asic-competition/):
a Tiny Tapeout design on IHP SG13CMOS5L, 6x4 tiles. It has not been
fabricated. Every result below names its evidence level: model, RTL
simulation, formal, gate level, RTL in FPGA fabric. FPGA is not silicon.

## What it is

Two small processor cores built for bit-banging. Each has its own SRAM bank
of 256 32-bit instructions, 8-word FIFOs to the host, a fractional clock
divider, four 16-bit registers with an ALU, and a CRC tap fed by its shift
registers. Beside core 0 sits a line unit: a configurable line coder (NRZ,
NRZI or Manchester, bit stuffing, end of packet, receive clock recovery). A
host, such as the Tiny Tapeout demo board's RP2350, loads programs over SPI
(1-bit or quad) and exchanges data through the FIFOs. The instruction set
starts from the RP2040's PIO and adds what protocols kept asking for: WAITs
that time out, catch edges at full clock resolution and can be qualified by a
second pin; registers and arithmetic; per-pin open drain; CALL/RET
([isa.md](isa.md)).

## What it does after fabrication

**A protocol is a program.** No protocol is fixed in the silicon. UART, SPI,
I2C, CAN, USB, 10BASE-T and the rest are files in `firmware/` (68 firmware
files), assembled on a PC and loaded over SPI. A new protocol needs a new
program, not a new chip.

**The line unit is a fixed menu that software picks from.** It offers line
codings (NRZ, NRZI, Manchester), stuffing rules, frame endings and receive
clock recovery. The menu was built with USB and 10BASE-T in mind, and
CAN-style stuffing also fits; software chooses from it but cannot add a
coding. The host picks with three register writes (LINECFG, LINEDIV,
LINEPINS) when it loads the program. Two protocols use it: full-speed USB
(4 clocks per bit at 48 MHz; the pure-firmware receiver, `usb_ls_rx`, the
low-speed one, takes at least 13 instructions per bit, so it cannot keep up)
and 10BASE-T receive (what the unit adds is the rate and the jitter
tolerance, not the coding: firmware alone decodes Manchester at slow rates,
in `rc5_rx` and `dali_ctrl`. The unit samples at both clock edges and tracks
the sender's phase, while a core sees one sample per clock; this is the
mechanism, not a measured comparison). [line-unit-settings.md](line-unit-settings.md) lists
each setting a test uses, the protocols it serves and the firmware.

**This was rehearsed.** With the chip's Verilog frozen at tag
`rc-01ce16d-t48ba` (unchanged since), three rounds of protocols the chip was
not designed for were added in firmware only: DMX512, LIN, NEC and RC-5
infrared, I2S, Wiegand, DShot600 (also bidirectional), DALI, Modbus RTU, MIDI,
a quadrature encoder counter, HDMI-CEC, ISO 7816-3 T=0 and SMBus with PEC:
21 programs. Each is checked against pins-only device models and against
sigrok's decoder where one exists. Their 16 RTL tests through the SPI port
also pass on the signed-off gate-level netlist (functional 16/16; with SDF
delays at the typical corner 16/16, two after their timing assertions got a
1 ns tolerance). All three rounds also ran on the RTL in FPGA fabric (Kria
KV260) against the other core or a pattern generator playing our own device
models ([post-freeze-protocols.md](post-freeze-protocols.md), with the 25
ISA limitations they ran into). Later, a fresh agent was given two protocols
drawn by a rule, with chip and tools frozen: HD44780 4-bit fits; ATA PIO
does not fit as specified, and an 8-bit subset does (model and RTL
simulation, [blind-test.md](blind-test.md)).

## Results that show what the chip can do

| Result | Level | Where |
|---|---|---|
| A full-speed USB device on one core at 48 MHz, handshake 10.5 clocks after the host's packet (spec 8-26) | RTL simulation; gate level on the signed-off netlist (141-word firmware, before the got_in guard) | [usb-full-speed.md](usb-full-speed.md) |
| USB enumeration with no host software after loading: Linux enumerated and configured it at low and full speed, 32 of 32 attaches, 66,000 of 66,000 descriptor reads, 188 of 188 bus resets | RTL in FPGA fabric, with the 246-word `usb_enum` from before `fault-fixes` (the current 253-word one has not run there) | [fpga/kv260](../fpga/kv260/README.md#result-against-linux-low-speed-2026-10-30) |
| 10BASE-T receive with 802.3's ±13.5 ns edge jitter at 50 MHz | RTL simulation | [isa.md §4.7](isa.md#47-line-unit-core-0) |
| 10BASE-T cut-through match: a pin rises 200-220 ns after a frame field's last mid-bit edge, before the frame ends | Model; RTL in FPGA fabric on generator frames | [eth10-cut-through.md](eth10-cut-through.md) |
| Logic analyzer, then an I2C target that clones what it captured | RTL simulation; RTL in FPGA fabric | [reverse-engineering.md](reverse-engineering.md) |
| Quad host port: 193 Mbit/s of payload | RTL simulation; 193.6 Mbit/s in FPGA fabric against its own SPI master | [fpga/kv260](../fpga/kv260/README.md) |

## How it is verified

The design was built by one person with no prior chip-design experience,
with AI agents writing the spec, RTL, firmware and tests
([methodology.md](methodology.md)). So each claim needs a check that does not
rest on an agent's word:

* **Two implementations of the spec**, a Python cycle model and the RTL, run
  random programs, pin stimulus and host traffic side by side, compared
  every cycle ([verification.md](verification.md)). A third, a Hardcaml and
  OCaml port, generates Verilog that is formally proved equal to the RTL
  ([hardcaml-port.md](hardcaml-port.md)).
* **Formal proofs** of the FIFOs, the host port against a pin-level
  reference, core invariants and line-unit bounds; **mutation testing**
  (34 of 34 mutants killed, recorded 2026-10-05).
* **The signed-off netlist is formally equivalent to the RTL** (7429 of 7429
  points, two engines, 5 of 5 planted bugs caught) and passes all 29
  top-level tests at gate level ([equivalence.md](equivalence.md),
  [release-evidence.md](release-evidence.md)).
* **Oracles that don't share our assumptions:** sigrok's protocol decoders,
  a real Linux USB stack on the FPGA build, and seeded wire faults (six real
  failures found, four fixed: [fault-testing.md](fault-testing.md)).
* **An independent verifier agent** that reruns and attacks the claims of each item sent to it: 51
  reports (000-050), 27 PASS and 24 FAIL, kept verbatim
  ([evidence/verifier](../evidence/verifier/README.md)).

## Physical status (run fo6)

Signoff passes with no waiver: 0 routing violations, KLayout DRC clean, LVS
clean, no antenna violation, and no slew, capacitance or fanout violation at
any corner; the Tiny Tapeout precheck passes. **Clock: the demos run at
48 MHz, with margin; 50 MHz is met at signoff** at all three corners (worst
flop or SRAM setup slack at the slow corner +0.010 ns at 20 ns; hold
+0.19 / +0.36 / +0.095 ns typ / slow / fast; [physical.md](physical.md)).
The 48 MHz margin is derived, not measured on fo6: the period is 0.833 ns
longer, and in every run of physical.md's settings table with STA at both
clocks the 48 MHz slack is the 50 MHz slack plus 0.833 ns. 10BASE-T receive
meets 802.3's ±13.5 ns jitter at 50 MHz; at the demo board's 40 MHz it
tolerates ±12 ns in a one-off model sweep ([guide.md](guide.md#known-limits)).

## Limits

Nothing has been fabricated. Pad timing, the host port's speed through the
pads and every analogue interface are unmeasured. FPGA runs used 3.3 V FPGA
pins and, apart from the Linux USB host, far ends that are our own models or
replayed waveforms. Seven programs have changed since software `5457d8e`,
the software of the gate-level receipts (`uart_rx`, `i2c_master`, `can_rx`,
`usb_enum`, `usb_fs_dev`, `usb_ls_dev`, `usb_ls_tx`), and their gate-level
and FPGA results apply to them only after a rerun; the
[README's firmware table](../README.md#what-runs-on-it) and
[verification-story.md](verification-story.md) say which run used which
version. SWD exists on the model only and is parked.

**Read next:** the [README](../README.md) for the full tables;
[guide.md](guide.md) maps each claim to the command that reproduces it;
[explain-wirelingo.md](explain-wirelingo.md) walks through the design for a
reader with no hardware background.
