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
          │ lockstep: every register, every cycle
   ┌──────┴──────────────┐
   │ RTL (pe_chip)       │  formal safety proofs on the pieces (SymbiYosys)
   └──────┬──────────────┘
          │ same firmware, same device models, through the real SPI port
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
* **Checking:** every cycle, every architectural register of every core (PC,
  R0–R3, ISR/OSR and counts, flags, delay, divider, WAIT state, FIFO levels,
  CRC), plus pin registers, global flags, pad outputs, and the bus read data
  for a random address.

The first mismatch stops the run with a register diff, recent bus traffic and
the disassembly of every core's current instruction. Coverage counters (per
opcode: attempted, completed, stalled) are printed so a regression that
quietly stops exercising something is visible. The regression runs on both
the full 4-core build and the reduced builds (`PE_NCORES`/`PE_NBANKS`).

**Mutation checks.** A deliberate one-line bug is put into the model and the
lockstep must catch it. It did so within about 1K cycles for an ALU mutation
and about 3.9K cycles for a CRC-tap mutation. A mutation it does *not* catch
means a coverage gap (for example, a CRC-width-specific mutation showed that
width 5 was rare in random configs, so a directed test was added).

## 3. Formal proofs (`formal/`, SymbiYosys)

* **`pe_fifo`** is proven equal to a reference queue for all input sequences
  of any length (k-induction), including the clock-gated storage (multiclock
  mode). Covers show full + simultaneous push/pop is reachable.
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

## 4. Firmware on the model, then on the RTL

`tools/pe/modelhost.py` runs firmware on the model with pin-level device
models. It's about 100× faster than RTL simulation, which makes firmware
development interactive. Because the model is lockstep-equivalent to the RTL,
the same tests are then repeated on the RTL through the **real SPI host port**
(`test/test.py`):

* UART TX/RX, SPI master, and an I2C master against an EEPROM model with clock
  stretching, NACKs and repeated STARTs.
* A **USB low-speed device**:
  * a reference host encoder/decoder (NRZI, bit stuffing, CRC5/CRC16, checked
    against known vectors);
  * packets at ±2.5% bit-rate error, beyond the ±1.5% the USB spec requires;
  * SETUP→ACK within the 7.5-bit turnaround, IN→NAK and IN→DATA1;
  * a corrupted DATA packet that must *not* be ACKed.
* The CRC tap is checked against the USB reference CRCs, both the transmitted
  fields and the residues.

## 5. Gate level

The hardened netlist runs the same top-level cocotb tests with the PDK cell and
SRAM models (`make GATES=yes`, as in Tiny Tapeout's CI).

## Bugs found by this flow (selection)

| Where | Bug | Caught by |
|-------|-----|-----------|
| assembler | `nop` encoded with the pre-32-bit field layout (`mov r0, ~r0`) | firmware test on the model (USB RX bit count off) |
| USB RX firmware | edge missed after a mid-cell decision with a 1.5 %-fast host | ±ppm model test; fixed by moving the decision point |
| USB RX firmware | response flags raised after the (possibly blocking) FIFO push | trace review; flags now precede pushes |
| test harness | EEPROM model lost bus updates (cocotb deferred writes) | I2C test; drivers now shadowed in Python |
| flow | LibreLane passes `-threads None` to OpenROAD | investigation of slow runs |
