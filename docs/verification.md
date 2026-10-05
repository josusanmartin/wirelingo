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
  settings, pin ownership, open-drain masks and CRC polynomials.
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
  outputs, and the bus read data for a random address. Configuration registers
  are write-only and their internal RTL values are compared directly each
  cycle, including pin and shift settings. FIFO entries behind the head are
  checked as they reach it; read reservations and the full line state are also
  compared. Finite traces establish agreement for the exercised stimuli.

The first mismatch stops the run with a register diff, recent bus traffic and
the disassembly of every core's current instruction. Coverage counters (per
opcode: attempted, completed, stalled) are printed. The regression runs on the
tapeout configuration (2 cores, 2 SRAM banks, 8-deep FIFOs) and on the full
4-core flop build.

**The line unit** (§4.7) is in the lockstep too: random LINECFG/LINEDIV/
LINEPINS (pins chosen so its TX often loops back to its RX), random IN/OUT LINE
on core 0, and its whole state compared every cycle. The tapeout-config run
must reach its coverage goals as well: bits sent and received, stuff bits both
ways, Manchester halves, frame ends, full and partial IN LINE, RX overflow.

**Mutation checks.** A deliberate bug in the model or the RTL must make a test
fail. Recorded cases: an ALU mutation (lockstep, ~1K cycles), a CRC-tap
mutation (~3.9K cycles; it showed CRC width 5 was rare, so a directed test was
added), an unregistered CTRL write in the model (lockstep, immediately), and
removing the FIFO read-snapshot gate in the RTL (`test_fifo_read_race`, word
lost when pushed 210 clocks into the read).

## 3. Formal proofs (`formal/`, SymbiYosys)

* **`pe_fifos`** (both FIFOs and the FJOIN_TX join) against reference queues
  for all input sequences, by k-induction, at depths 4 and 8, including the
  clock-gated latch storage and its staging register (multiclock mode). Unjoined: two independent queues.
  Joined: one queue in order across RXF then TXF, with the stage levels the
  spec defines. Covers reach 2·D words buffered, with a push and a pop in the
  same cycle.
* **`pe_hostport`**, with SCK, CS_n, the IO lines and the clock relationship
  unconstrained (multiclock): at most one bus operation per cycle; at most two
  read words outstanding; a snapshot only with room, a commit only of a word
  that was snapped, a cancel clears them; uo[6:4] taken only inside a
  transaction. Covers reach a write, a commit with two outstanding, a cancel.
  Data integrity at speed is checked by `test_host_port_fast` and
  `test_spi_port_timing`.
* **`pe_core`**, with a completely unconstrained instruction stream, pins,
  flags and host:
  * no side effects (pins, flags, FIFOs) unless executing on a tick;
  * none while disabled or restarting;
  * halted/fault are sticky until restart;
  * the PC changes only on a completed instruction, a restart or a host write;
  * a disabled core's state is frozen;
  * shift counts never exceed 32.
* **`pe_line`**, with every input free except core 0's contract (pushes only
  with room, takes only what `IN LINE` may, counts 1–32), including LINECFG,
  LINEDIV and LINEPINS rewritten at any time: TX queue ≤ 2 entries, RX queue
  ≤ 32 bits, and the Manchester receiver's phase stays in [−720720, 262208]
  for any period up to 4095, with every 21-bit operation equal to the same
  computation at 23 bits (it never wraps). Tightening either bound breaks the
  induction. For any legal RX period (P ≥ 4) the NRZ/NRZI sample counter's
  load after a transition (P/2 − 1, or P/2 − 2 with the half-cycle sample
  point) doesn't wrap.

These are safety properties of the pieces. Functional equivalence of the
whole core is covered by lockstep, not by formal.

## 4. Firmware on the model, then on the RTL

`tools/pe/modelhost.py` runs firmware on the model with pin-level device
models, about 100× faster than RTL simulation. The model is lockstep-checked
against the RTL on tested traces; model-only results remain model evidence.
The key cases are repeated on
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
* SPI reset with CS low at command, address and data phases, SCK stopped during
  reset, and read-ahead state discarded; skewed duty cycles at fast rates.
* 10BASE-T RX: comparator-pad jitter and full 64..1518-byte frames with FCS,
  96-bit inter-frame gaps, concurrent quad draining, bounded 4-us stalls,
  deliberate overflow and explicit queue/firmware recovery.
* USB: both variants enumerate through concurrent SPI service at a quantized
  48 MHz, reject an invalid PID, retry lost ACKs, abort a transfer on SETUP,
  and restore address zero after bus reset. Functional GL can run these same
  tests, but requires a new candidate netlist.

**Additional model coverage (beyond the RTL cases above):**

The separate `test_eth_service` suite executes the MicroPython finite RX
capture through real quad SPI, with independent 64/1518-byte input waveforms,
jitter, bad FCS and overflow. Its two tests also pass on the retained global-
routing checkpoint netlist without SDF. `test_eth10_board.py` checks bounded
records, independent FCS, preamble/padding and failure cleanup; its board API
tests use fakes. The proposed [receiver circuit](eth10-receiver.md) is unbuilt.
The final GL runner requires the main, compiled-PIO and receive-service suites;
checkpoint tests do not satisfy final physical release.

* USB: packets at ±2.5 % bit rate (the spec requires ±1.5 %) with every
  payload length; tokens to other addresses/endpoints or with a bad CRC5
  ignored; a bad DATA packet gives a bad record and the retry parses; a
  half-staged packet is NAKed; **enumeration** (`test_usb_enum.py`) by a
  cycle-level host (`tools/pe/usbhost.py`) while "device software" services
  the chip between transactions, also with random 100–3000-cycle stalls;
  the address filter follows SET_ADDRESS; a lost ACK makes the software
  re-stage the same packet with the same toggle.
* Line unit: USB-style (NRZI, stuffing, SE0 end; the waveform also decoded
  by the USB reference), Manchester and CAN-style (NRZ stuffing) loopbacks.
* USB full speed (`usb_fs_dev`, 48 MHz, 4 clocks per bit): ±9.25 ns edge
  jitter at ±500/±2500 ppm, and 28 ns of duty-cycle distortion between
  consecutive edges at 0/±2500 ppm (`test_fs_rx_duty_cycle_margin`, which
  needs the half-cycle NRZI sample point; the RTL test `test_usb_fs_device`
  checks the same 28 ns case: 24/24 packets, against 11/24 on g4).
* USB on one core (`usb_ls_dev`, line unit): enumeration with fixed and random
  service stalls, the address filter, the lost ACK, the same tests as the
  two-core firmware.
* 10BASE-T RX (`eth10_rx`, line unit): `eth10_tx` on core 1 to `eth10_rx` on
  core 0 at 40 MHz; an external sender at 50 MHz with random phase, 100 ppm
  offset and 802.3's ±13.5 ns of jitter, uniform and with every edge at
  ±13.5 ns (±200 ppm); the pad is sampled at both clock edges. A sweep: 30/30
  frames at ±15 ns, 9/30 at ±18 ns. `test_line_rx_jitter` (lockstep) runs a
  jittered frame through the RTL and the model, compared every cycle, through
  the receiver's lock.
* UART: 64 back-to-back bytes from a sender at ±3 % with the fractional
  115200-baud divider; the on-chip TX→RX pair over a virtual pin.
* I2C: SCL held low at every one of the 19 releases (and before START) times
  out, replies 0x100 and recovers; the SCL waveform is 4 + 4 ticks.
* 10BASE-T: a 1518-byte frame streamed through the host port; link pulses
  100 ns wide, 16 ms apart.
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

**Independent protocol oracle (sigrok).** The firmware and the device models
above come from the same hands, so a shared misreading of a protocol would
pass both. `test/test_sigrok.py` re-runs test.py's UART TX and RX, SPI master,
I2C master and both USB device tests unchanged (their own assertions included)
while recording the pads, then decodes the recording with sigrok-cli's
protocol decoders (libsigrokdecode: third-party code sharing nothing with our
models, `tools/pe/sigrok.py`). The decode must equal the data the test meant
to move, with no decoder warnings, and in every test the host port (SPI mode
0: CS_n ui7, SCK ui6, MOSI ui5, MISO uo7) must decode byte for byte to what
test.py's master sent and sampled. `test/model/test_sigrok_can.py` does the
same for the CAN firmware on the cycle model (`ModelRecorder` records the
model's pins) with sigrok's CAN decoder.

```
make -C test COCOTB_TEST_MODULES=test_sigrok                         # 6 tests, ~1 min
PYTHONPATH=tools python -m pytest test/model/test_sigrok_can.py      # 2 tests, ~5 s
```

sigrok-cli comes from `sudo apt install sigrok-cli`, or `flow/install_sigrok.sh`
(no root: unpacks the Ubuntu packages, ~1 MB, under `~/tools/sigrok`; tested
on Ubuntu 22.04 with sigrok-cli 0.7.2 and libsigrokdecode 0.5.3), or
`SIGROK_CLI=<path>`. Without it every sigrok test is skipped and the log says
how to install it. `SIGROK_KEEP=<dir>` keeps each test's VCD and sigrok
output. What sigrok disagreed with:

* `test_usb_ls_device` clocked the chip at 50 MHz with 32 clocks per bit:
  1.5625 Mbit/s, +4.2 %, outside low speed's ±1.5 %. Our USB helpers scaled
  the same way, so nothing had noticed; sigrok's `usb_signalling` rejected the
  bus. A test bug (the firmware is right at 48 MHz); the test now runs at
  48 MHz as its docstring said.
* CAN identifiers 0x7F0 and 0x7FF (test_can.py's all-ones corner cases):
  sigrok warns "Identifier bits 10..4 must not be all recessive", Bosch CAN
  2.0's reserved range. The firmware treats them as any identifier, which is
  what a receiver should do; the test vectors are deliberate, so that one
  warning is expected for those frames and no other.
* Decoder limits, not disagreements: libsigrokdecode 0.5.3's CAN decoder
  never checks the CRC (`is_valid_crc()` is a TODO returning true), so the
  test compares the CRC field sigrok read with the CRC of the fields sigrok
  decoded (the corrupted frame fails that and is not ACKed). The decoder also
  needs the bus idle at the start of the capture: recorded from reset (ui0
  low while the program loads) it misdecoded the first frames, so the capture
  starts after setup.

**What these runs don't show.** Most top-level tests run at 50 MHz; the
concurrent USB service and generated UART loopback run at 48 MHz (rounded
to 20.834 ns by the simulator). Protocol timing is checked against behavioural
device models. USB uses 32 clocks per bit at 48 MHz; 10BASE-T TX uses four at
40 MHz, while RX tests use 50 MHz. Nothing here establishes board performance:
nothing here checks analog levels, USB or Ethernet electrical compliance, or
the CAN error-confinement state machine (the CAN programs send and receive
frames; they are not a full controller).

## 5. Gate level

The hardened netlist (`final/nl`, as Tiny Tapeout's CI uses for IHP) must run the
top-level cocotb suites (`test.py`, `test_pio.py`, `test_eth_service.py`) with
the PDK cell and SRAM models (`flow/gl_test.sh <run>`, fresh build and result
directories per run). The models are functional and no SDF is annotated, so
this checks the mapped logic, not delays; timing is STA's job. Besides
setup/hold, every STA corner reports the SCK↔clk crossings against their
one-period bound and the clock-gating, pulse-width and recovery/removal checks
(`src/timing_checks.tcl`).

**With delays** (`flow/gl_sdf_test.sh <run>`): the same netlist back-annotated
with the run's extracted SDF (Icarus; `flow/sdf_prep.py` works around its SDF
reader), five top-level tests at typ/50 MHz and slow/43.5 MHz must pass, and
`test_fetch_to_pin` at slow/80 MHz must fail, which shows the delays are in
effect. g4 passes all three cases. This checks that the exercised
transitions meet the clock with real cell, wire, clock-tree and SRAM delays.
It does not check setup/hold windows (Icarus has no timing checks), and it
covers only the paths the tests sensitize. g4's slow corner also passes the
five tests at 50 MHz, although STA says −2.88 ns, because no test reaches
STA's worst path. Details and limits:
[physical.md](physical.md#timing-annotated-gate-level-sdf).

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
| USB RX firmware (two-core) | the device's own ACK of SETUP/OUT data, heard by the RX core, set F_GOTACK; host software fast enough to stage the first IN chunk before that ACK ended saw the chunk as acknowledged, and a lost host ACK skipped a chunk | concurrent RTL service test, once the service loop got faster; the TX core now sets F_SENT when it sends DATA and only an ACK after it counts |
