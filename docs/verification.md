# Verification

protoemu is verified at four levels. Each one catches a different class of
bug, and the lower levels make the higher ones cheap.

```
        spec (docs/isa.md) ── normative
          │
   ┌──────┴──────────────┐
   │ golden model        │  tools/pe/sim.py: cycle-accurate, pure Python
   │ assembler           │  tools/pe/asm.py (+ round-trip self-test)
   └──────┬──────────────┘
          │ lockstep: architectural state, every cycle
   ┌──────┴──────────────┐
   │ RTL (pe_chip)       │  formal safety proofs on the pieces (SymbiYosys)
   └──────┬──────────────┘
          │ firmware + device models: model first, key cases through the SPI port
   ┌──────┴──────────────┐
   │ top level / GL      │  cocotb device models (UART, SPI, I2C EEPROM, USB host)
   └─────────────────────┘
```

## 1. A single normative spec, two independent implementations

`docs/isa.md` defines every instruction and every cycle-level interaction:

* what happens when the host writes a register while the core writes it;
* FIFO full/empty evaluated at start of cycle;
* how WAIT edge trackers behave between ticks;
* ...and similar edge cases.

The Python model (`sim.py`) and the RTL were written separately from it. A
disagreement is resolved by the spec, never by making one implementation copy
the other.

## 2. Constrained-random lockstep (`test/test_lockstep.py`)

The RTL `pe_chip` and the model run side by side in cocotb:

* **Programs:** random, from `tools/pe/randprog.py`. That means weighted
  opcodes, jump targets mostly inside owned banks, occasional fully random
  words (reserved encodings must behave as specified), small delays, and
  timeouts so programs keep moving. Some jump targets deliberately cross into
  unowned banks, to exercise faults.
* **Configuration:** random clock dividers, pin maps, shift and threshold
  settings, bank and pin ownership, open-drain masks and CRC polynomials.
* **Stimulus:** random pin toggling at several rates, plus random host bus
  traffic every cycle:
  * FIFO pushes and pops;
  * flag set/clear;
  * register and PC overrides;
  * restarts and FIFO clears;
  * configuration and pin-override writes.
* **Checking**, every cycle, for every core: PC, R0–R3, ISR/OSR and their
  counts, Z/C/T, delay counter, divider state, WAIT state (in-progress bit,
  edge tracker `prev`/`event`), halted/fault/stalled, CRC, FIFO levels, the
  word at the head of each FIFO (so every entry is compared as it reaches the
  head) and the FIFO read-snapshot bit; plus pin registers, global flags, pad
  outputs, and the bus read data for a random address. Not compared directly
  each cycle: configuration registers (they are read back through random bus
  reads and act on everything above), FIFO entries behind the head, and a CTRL
  write in flight.

The first mismatch stops the run with a register diff, recent bus traffic and
the disassembly of every core's current instruction. Coverage counters (per
opcode: attempted, completed, stalled) are printed. The regression runs on the
tapeout configuration (2 cores, 2 SRAM banks, 8-deep FIFOs) and on the full
4-core flop build.

**Mutation checks.** A deliberate bug in the model or the RTL must make a test
fail. Recorded cases: an ALU mutation (lockstep, ~1K cycles), a CRC-tap
mutation (~3.9K cycles; it showed CRC width 5 was rare, so a directed test was
added), an unregistered CTRL write in the model (lockstep, immediately), and
removing the FIFO read-snapshot gate in the RTL (`test_fifo_read_race`, word
lost when pushed 210 clocks into the read).

## 3. Formal proofs (`formal/`, SymbiYosys)

* **`pe_fifos`** (both FIFOs and the FJOIN_TX join) against reference queues
  for all input sequences, by k-induction, at depths 4 and 8, including the
  clock-gated storage (multiclock mode). Unjoined: two independent queues.
  Joined: one queue in order across RXF then TXF, with the stage levels the
  spec defines. Covers reach 2·D words buffered, with a push and a pop in the
  same cycle.
* **`pe_spi_slave`:**
  * at most one bus operation per cycle, each a single-cycle pulse;
  * MISO idle while deselected;
  * any aborted transaction returns to idle.

  Covers reach a complete write and a complete read.
* **`pe_core`**, with a completely unconstrained instruction stream, pins,
  flags and host:
  * no side effects (pins, flags, FIFOs) unless executing on a tick;
  * none while disabled or restarting;
  * halted/fault are sticky until restart;
  * the PC changes only on a completed instruction, a restart or a host write;
  * a disabled core's state is frozen;
  * shift counts never exceed 32.

These are safety properties of the pieces. Functional equivalence of the
whole core is covered by lockstep, not by formal.

## 4. Firmware on the model, then on the RTL

`tools/pe/modelhost.py` runs firmware on the model with pin-level device
models, about 100× faster than RTL simulation. The model is lockstep-checked
against the RTL, so model results carry over; the key cases are repeated on
the RTL through the **real SPI host port** (`test/test.py`).

**On the RTL, through the SPI port** (also run at gate level):

* UART TX/RX; SPI master; I2C master against an EEPROM model with clock
  stretching, NACKs and repeated STARTs.
* USB low-speed device: SETUP + DATA0 → ACK and a good record; an IN to
  another address gets no answer; IN → NAK; IN → the staged DATA1, each within
  the 7.5-bit turnaround.
* 10BASE-T TX: a 64-byte frame from one 16-word burst into the joined FIFO,
  decoded from the waveform (Manchester cells, preamble/SFD, FCS, TP_IDL).
* The FIFO read race: a word pushed anywhere during a read of an empty RXF
  must be read exactly once.

**On the model only:**

* USB: packets at ±2.5 % bit rate (the spec requires ±1.5 %) with every
  payload length; tokens to other addresses/endpoints or with a bad CRC5
  ignored; a bad DATA packet gives a bad record and the retry parses; a
  half-staged packet is NAKed; **enumeration** (`test_usb_enum.py`) by a
  cycle-level host (`tools/pe/usbhost.py`) while "device software" services
  the chip between transactions, also with random 100–3000-cycle stalls;
  the address filter follows SET_ADDRESS; a lost ACK makes the software
  re-stage the same packet with the same toggle.
* UART: 64 back-to-back bytes from a sender at ±3 % with the fractional
  115200-baud divider; the on-chip TX→RX pair over a virtual pin.
* I2C: SCL held low at every one of the 19 releases (and before START) times
  out, replies 0x100 and recovers; the SCL waveform is 4 + 4 ticks.
* 10BASE-T: frames streamed at the SPI port's maximum rate up to the 128-byte
  bound (132 bytes must underrun); link pulses 100 ns wide, 16 ms apart.
* CAN 2.0A: a transmitter model at ±1 % bit rate, a corrupted frame (no ACK),
  a stuff error (status 2, error flag, the next frame received), an on-chip
  TX→RX loopback between two cores on a wired-AND bus, and arbitration loss
  against a higher-priority node.
* SWD (ARM DP model), JTAG (IEEE 1149.1 TAP), 1-Wire (slave model), PS/2
  (host model checking parity), WS2812 (datasheet timing windows), and the I2C
  sniffer running beside the I2C master.
* The CRC tap against the USB reference CRCs (fields and residues).
* Host software: the demo-board MicroPython driver runs unmodified against the
  model through a Python model of the SPI port (UART output, a 10BASE-T frame).

**What these runs don't show.** The top-level testbench runs at 50 MHz, and
protocol timing is checked in clock cycles (for example 32 clocks per USB bit,
4 per 10BASE-T bit); at the documented clocks (48 and 40 MHz) the same cycle
counts give the specified bit rates. The device models are behavioural:
nothing here checks analog levels, USB or Ethernet electrical compliance, or
the CAN error-confinement state machine (the CAN programs send and receive
frames; they are not a full controller).

## 5. Gate level

The hardened netlist (`final/nl`, as Tiny Tapeout's CI uses for IHP) runs the
top-level cocotb tests with the PDK cell and SRAM models (`make GATES=yes`;
`flow/gl_test.sh`). The models are functional and no SDF is annotated, so this
checks the mapped logic, not delays; timing is covered by STA.

## Bugs found by this flow (selection)

| Where | Bug | Caught by |
|-------|-----|-----------|
| assembler | `nop` encoded with the pre-32-bit field layout (`mov r0, ~r0`) | firmware test on the model (USB RX bit count off) |
| USB RX firmware | edge missed after a mid-cell decision with a 1.5 %-fast host | ±ppm model test; fixed by moving the decision point |
| USB RX firmware | response flags raised after the (possibly blocking) FIFO push | trace review; flags now precede pushes |
| test harness | EEPROM model lost bus updates (cocotb deferred writes) | I2C test; drivers now shadowed in Python |
| flow | LibreLane passes `-threads None` to OpenROAD | investigation of slow runs |
| USB RX firmware | autopushed words of skipped packets (our own IN responses) leaked into the FIFO | enumeration test (host software desync) |
| USB RX firmware | a 12-byte packet plus its token overflowed the 4-word FIFO | enumeration test; now only DATA payloads are pushed, without SYNC/PID |
| golden model | FIFO join: the TXF→RXF move used the join bit after a same-cycle host SHIFTCTRL write (the RTL uses the old value, as §5 says) | lockstep (random FJOIN_TX), first seed with a mid-run SHIFTCTRL write |
| RTL timing | a CTRL write decoded `restart` combinationally, putting the host address decode in front of every core's execute stage (−7.6 ns at the slow corner) | post-route STA path review; CTRL is now registered (lockstep confirms the model and RTL agree on the new timing; an unregistered-CTRL model mutant fails at once) |
| test harness | gate-level test used the powered netlist and connected VPWR/VGND; TT simulates `final/nl` for IHP and the cell models need `sg13cmos5l_udp.v` | first GL run |
| test harness | driver test traced only `ModelHost.step`, missing the clocks of the host's own bus cycles (a 1-tick gap breaks Manchester decoding) | 10BASE-T driver test; trace now hooks `Chip.step` |
| UART RX firmware | back-to-back bytes: the program returned to its start-bit edge WAIT exactly at the end of the stop bit, and with a fractional divider the next start edge could fall on that tick and be missed; also too little margin for a 3 %-fast sender | the board bring-up loopback (driver on the model); now a level WAIT, the byte pushed during the stop bit, and test_uart (±3 %, back to back) |
