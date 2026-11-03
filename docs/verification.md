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

## 1. A single normative spec, two implementations

`docs/isa.md` defines every instruction and every cycle-level interaction:

* what happens when the host writes a register while the core writes it;
* FIFO full/empty evaluated at start of cycle;
* how WAIT edge trackers behave between ticks;
* ...and similar edge cases.

The Python model (`sim.py`) and the RTL were written separately from it
(by the same agents, so a shared misreading of the spec passes both; the
device models and sigrok decoders below are the check on that). A
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
  settings, pin ownership, open-drain masks, synchroniser bypass masks (also
  rewritten mid-run, so the switch between one and two flops is covered) and
  CRC polynomials.
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

**CALL / RET**: random programs contain CALLs (conditions and targets as
JMP), RETs, and MOVs to and from LR, with coverage goals for a taken CALL, a
RET and a write to LR; LR is compared every cycle. `test_call_ret` (directed)
adds nesting through a saved LR, a CALL on the wrap's last word (it links to
the wrap bottom), side-set and delays on CALL/RET, a fractional divider, and
host restarts and PC writes.

**WAIT kind 7** (`wait pins != rN mask m`) is a quarter of the random WAITs,
with coverage goals for completing on the first attempt, while in progress and
by timeout; `test_wait_diff` (directed, compared every cycle) adds slow and
fractional dividers, single-clock pin pulses caught between ticks, host PC
writes and restarts landing mid-WAIT.

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
* **`pe_hostport` against a reference model** of the §6.1 protocol decoded
  from the pins, under the host timing contract, with reset reasserted at
  any step (k-induction, [formal-hostport.md](formal-hostport.md)): every
  write goes to the right address (incrementing or fixed) with the host's
  data, once and in order; each word read out equals its own snapshot of the
  right address; a word is committed only after its last bit was clocked out;
  a read is cancelled once, after CS_n rises; a word cut by CS_n is neither
  written nor committed; after reset nothing happens until a legally re-armed
  transaction, which then works; the clock-domain crossings keep their
  margins. Covers reach each case; planted RTL bugs are caught
  (`make -C formal hostport_mutants`).
* **`pe_core`**, with a completely unconstrained instruction stream, pins,
  flags and host:
  * no side effects (pins, flags, FIFOs) unless executing on a tick;
  * none while disabled or restarting;
  * halted/fault are sticky until restart;
  * the PC changes only on a completed instruction, a restart or a host write;
  * a disabled core's state is frozen;
  * LR changes only on a completed instruction, and a taken CALL leaves it
    equal to the PC the core would have gone to without the jump;
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

* UART TX/RX; SPI master, also at 4 clocks per SCK with MISO in
  `SYNC_BYPASS` (`test_spi_master_fast`: correct with the bypass, one bit late
  without it); I2C master against an EEPROM model with clock stretching, NACKs
  and repeated STARTs.
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
  and restore address zero after bus reset. These are in the main suite,
  which passes on run fo6's netlist at gate level (25/25).

**Additional model coverage (beyond the RTL cases above):**

The separate `test_eth_service` suite executes the MicroPython finite RX
capture through real quad SPI, with independent 64/1518-byte input waveforms,
jitter, bad FCS and overflow. Its two tests also pass at gate level on run
fo6's final netlist (functional, no SDF). `test_eth10_board.py` checks bounded
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
  ±13.5 ns (±200 ppm); the pad is sampled at both clock edges. A one-off model sweep
when the loop was added (commit 19707ef, not a regression test): 30/30
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
* CALL / RET (`test_call.py`): linking, conditional calls (LR untouched when
  not taken), nesting through a register, a CALL at the wrap top, LR across a
  restart. `i2c_device` with its three subroutines runs the whole reverse
  engineering flow; its response-time test keeps its bounds (a written byte's
  ACK moved from 4 to 5 clocks after the SCL fall, the rest is unchanged).
* WAIT kind 7 (`test_wait_diff.py`): the mask and IN_BASE, completion on
  the first attempt, a one-clock pulse between ticks, the timeout as a tick
  counter. The logic-analyzer capture (`test_reverse_engineering.py`):
  160 random edges timestamped with one constant offset, edges 1–6 ticks apart
  on two channels never lost, the 7-tick square-wave bound, idle records and
  the gap word.
* SPI master against a device model whose answer delay is swept
  (`test_spi_fast.py`): clocks per SCK and the delay budget for each firmware,
  divider and bypass setting.
* Host software: the demo-board MicroPython driver runs unmodified against the
  model through a Python model of the SPI port (UART output, a 10BASE-T frame).

* Wire faults on existing firmware ([fault-testing.md](fault-testing.md)):
  seeded glitches, stuck lines, truncated frames, bit flips, clock stretch and
  edge jitter against `uart_rx`, `i2c_master`, `can_rx`, `usb_enum`, `eth10_rx`
  and `eth10_match` (`test_faults.py`), six findings. Four are fixed in the
  firmware on `fault-fixes` (`uart_rx` start glitch, `i2c_master` SDA read-back,
  `can_rx` SOF check, `usb_enum` reset anywhere), with RTL tests
  `test_uart_rx_glitch` and `test_i2c_master_bus_fault`; those four firmware
  have not run on the KV260 since, and its results are for the earlier ones.
* Pulse timestamps ([pps-capture.md](pps-capture.md)): `test_pps.py` on the
  model, `test/test_pps.py` on the RTL.

**Independent protocol oracle (sigrok).** The firmware and the device models
above come from the same hands, so a shared misreading of a protocol would
pass both. `test/test_sigrok.py` re-runs test.py's UART TX and RX, SPI master,
I2C master, both USB device tests and autonomous USB enumeration at low and
full speed unchanged (their own assertions included)
while recording the pads, then decodes the recording with sigrok-cli's
protocol decoders (libsigrokdecode: third-party code sharing nothing with our
models, `tools/pe/sigrok.py`). The decode must equal the data the test meant
to move, with no decoder warnings, and in every test the host port (SPI mode
0: CS_n ui7, SCK ui6, MOSI ui5, MISO uo7) must decode byte for byte to what
test.py's master sent and sampled. `test/model/test_sigrok_can.py` does the
same for the CAN firmware on the cycle model (`ModelRecorder` records the
model's pins) with sigrok's CAN decoder.

```
make -C test COCOTB_TEST_MODULES=test_sigrok                         # 8 tests
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

**Formal equivalence** (`formal/equiv/equiv_check.sh <run>`,
[equivalence.md](equivalence.md)). Gate-level simulation covers only what the
tests exercise; this covers every input and state. The final netlist,
with the PDK's cell models, is proved equivalent to the RTL as synthesised.
All 1647 flops, 1024 latches, 54 clock gates and both SRAMs correspond one to
one with the RTL's: by name, by simulation-proposed pairs (each proved), or,
for the host port's `phase`, through its logged one-hot re-encoding. Each
flop's next state, clock and reset, the inputs of each latch, clock gate and
SRAM, and the outputs are proved equal: 7429 points, by ABC `&cec` and by
yosys `equiv_simple`. Six RTL flops with no netlist flop are proved
unobservable. Run fo6 and t48ba both pass, and five planted netlist bugs are
each caught. It is zero-delay and functional: timing stays with STA and SDF
simulation. The latch, clock-gate and SRAM cells are the same black boxes on
both sides.

**With delays** (`flow/gl_sdf_test.sh <run>`): the same netlist back-annotated
with the run's extracted SDF (Icarus; `flow/sdf_prep.py` works around its SDF
reader), five top-level tests at typ, slow and fast at 50 MHz must pass, and
`test_fetch_to_pin` at slow/100 MHz must fail, which shows the delays are in
effect. Run fo6 passes all of it (release receipts,
[release-evidence.md](release-evidence.md)). Historically g4 and v2c passed
the older set (typ/50 MHz, slow/43.5 MHz, control at slow/80 MHz). This checks that the exercised
transitions meet the clock with real cell, wire, clock-tree and SRAM delays.
It does not check setup/hold windows (Icarus has no timing checks), and it
covers only the paths the tests sensitize. g4's slow corner also passes the
five tests at 50 MHz, although STA says −2.88 ns, because no test reaches
STA's worst path. Details and limits:
[physical.md](physical.md#timing-annotated-gate-level-sdf).

The v2 features have their own set (`flow/gl_sdf_test.sh <run> v2`): the
fast SPI master with MISO in `SYNC_BYPASS` (test_spi_master_fast), WAIT kind 7
at 1-tick resolution (test_la_capture_ticks, new: decoded edges and pulses
must equal the cycle model's), CALL/RET (test_call_ret, new: side-set edges,
pushed links and registers must equal the model's) at 20 ns, and the
full-speed USB device with the half-cycle NRZI sample point
(test_usb_fs_device) at 48 MHz (20.834 ns), each at typ, slow and fast. Run
fo6 passes all of it, and the release adds the autonomous USB enumeration
tests (low and full speed) with SDF at all three corners. Historically v2c
passed the older version of this set (typ 20 ns, slow 23 ns, and full-speed
USB at slow/48 MHz, which its STA didn't meet). `v2long` runs the full-length
test_la_capture_rate and test_reverse_engineering (v2c: pass at typ and slow, ~50 minutes each). Under SDF the
external uio devices drive 4 ns after the clock edge, STA's input delay:
driven at the edge, a pad synchronizer whose clock arrives 0.85 ns late
took the value a clock early, which first failed test_spi_master_fast's
negative check (a testbench race, not the chip; physical.md has the
numbers). Kind 7 on fractional dividers, restarts mid-WAIT or mid-CALL, and
the bypass on other pins stay with RTL lockstep, which can't run on the
netlist.

## v2 regression (2026-10-05, RTL not yet hardened)

On branch `v2` with the RTL as committed (kind 7's reference on bits [15:14]):

* model suite: 251 passed;
* top-level cocotb: `test.py` 20/20, `test_pio.py` 2/2, `test_eth_service.py` 2/2;
* lockstep, tapeout configuration (`LOCKSTEP_SEEDS=60`, 2 cores, 2 SRAM banks,
  8-deep FIFOs): 6/6, every coverage goal reached; 4-core flop build
  (`LOCKSTEP_GOALS=core`, 60 seeds × 1000 cycles): 6/6; plus 400 seeds × 1000
  cycles in four other seed windows (tapeout) and 200 in two (4-core), all
  passing. Two earlier windows found the two golden-model bugs in the table
  below; the regression's own windows had not reached them;
* formal: all seven jobs pass (fifos 4/8 + cover, hostport + cover, core, line);
* mutation runner: 34 of 34 mutants killed (17 of them new for the v2 features).

Gate level and STA: run v2c's netlist passes all 24 top-level tests (functional
GL) and signoff STA at every corner ([physical.md](physical.md)). The current
candidate, run fo6, passes 29/29 and meets 50 MHz at every corner (§5).

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
| golden model (v2) | the line unit's falling-edge RX sample was chosen with a `SYNC_BYPASS` value written in the same cycle (the RTL uses it from the next cycle) | lockstep, a seed window outside the regression's (`LOCKSTEP_SEED0=5000`): Manchester phase off by one sample after a mid-frame bypass write |
| golden model | the stuffing run bit was "none" at a frame start where the RTL keeps the last bit and clears a valid flag; switching STUFF_ONES off mid-frame crashed the model | lockstep, seed window 9000 (TypeError); run bits and flags are now compared every cycle |
| MicroPython driver (v2) | its image check ignored CALL (opcode 13), so a CALL out of the core's bank loaded and faulted at run time | review; `test_board_load_validates_before_any_bus_write` has a CALL case and a mutant |
| WS2812 firmware | a 0 bit was 2 ticks high, not the header's 3: 240-260 ns at 50 MHz, outside WS2812B's T0H window | the KV260 run script's model pass at 50 MHz, before the board; `test_ws2812_bit_timing_is_the_documented_one` |
| CAN RX firmware | a stuff bit after a CRC ending in five equal bits was not skipped, so the ACK came a bit early (about one random frame in 31; the model tests' frames avoided it) | the KV260 run script's model pass on a generator frame, before the board; three tests in `test_can.py` |
| assembler | `/` truncated each half of a split delay, so bidirectional DShot ran a clock short per level at 50 MHz | the KV260 board (pins captured by `la_capture`), then the cycle model; `/` is now integer division |
