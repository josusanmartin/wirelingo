# A guide for reviewers

protoemu is a protocol emulator built as **firmware on small bit-banging
cores**, not as fixed peripherals. This page maps each claim to its evidence
and the command that reproduces it. Setup is in the README (Python venv, OSS
CAD Suite).

## What is new

* **WAITs that can't hang and don't miss edges.** Any WAIT can time out
  (`i2c_master`: every SCL release, so a stuck bus can't hang the core). Edge
  WAITs are tracked every clock even when the core ticks slowly. A WAIT can be
  qualified by a second pin: `wait fall SDA if pin SCL == 1` is an I2C START
  detector in one instruction (`i2c_sniff`).
* **A CRC tap** fed by the shift registers checks USB CRC16, CAN CRC15 and PS/2
  parity in firmware.
* **FIFO join** (from PIO) plus in-core preamble generation make 10BASE-T TX
  fit through a 5 Mbit/s SPI host port.
* **Verification as part of the design:**
  * a cycle-accurate model is checked against the RTL every cycle on random
    programs, with required coverage goals;
  * formal proofs of the FIFOs;
  * mutation checks;
  * firmware tested against independent device models.

## Claims and evidence

| Claim | Evidence | Reproduce |
|---|---|---|
| The RTL matches the spec's model cycle for cycle | random programs, pins and host traffic; registers, WAIT trackers, FIFO levels and heads compared every cycle; coverage goals required | `cd test && LOCKSTEP_SEEDS=60 PE_NCORES=2 PE_NBANKS=2 PE_FIFO_LOG2=3 make TB=chip` |
| The tests catch bugs | 12 one-line bugs in the model, RTL, firmware and assembler, each killed | `python3 test/mutants.py` |
| FIFOs (incl. join) are correct for all input sequences | k-induction against reference queues, depths 4 and 8 | `make -C formal fifos` |
| Protocols work through the real host port | top-level cocotb: UART, SPI, I2C, USB device, 10BASE-T, FIFO read race, SPI timing sweep | `cd test && make` |
| … and on the routed netlist | the same tests on `final/nl` with the PDK cell and SRAM models | `flow/gl_test.sh <run>` |
| USB low-speed device enumerates | cycle-level host + device software (also with random service stalls); address filter, lost ACK, bad packets | `pytest test/model/test_usb_enum.py test/model/test_usb_model.py` |
| I2C can't hang | SCL stuck low at every one of 19 releases, plus before START | `pytest test/model/test_i2c_timeout.py` |
| 10BASE-T TX, with its limit | waveform decoded (Manchester, FCS, TP_IDL, link pulses); 128-byte streaming bound | `pytest test/model/test_eth10.py` |
| Host software works | the MicroPython driver and bring-up script, unmodified, against the model via an SPI port model | `pytest test/model/test_micropython_driver.py` |
| It fits and closes timing | DRC/LVS/antenna clean, timing per corner, SRAM view per corner | `python3 flow/signoff.py <run>/runs/wokwi` (docs/physical.md) |

(`pytest` = `PYTHONPATH=tools python -m pytest -q` from the repo root.)

## Known limits

* Two cores on silicon (four don't route on this tile). 50 MHz at the typical
  corner; about 37 MHz at the slow corner.
* 10BASE-T: TX only, frames ≤ 128 bytes (64 without streaming); no RX
  (README explains why).
* USB: endpoint 0 in firmware; requests and data toggles are host software.
* CAN: frames with stuffing, CRC, arbitration, ACK and stuff-error detection;
  not a full controller (no error counters or bus-off).
* Protocol timing is checked in clock cycles against behavioural device models,
  not against electrical specifications.
* Gate-level simulation is functional (no SDF); timing is covered by STA.

## Where things are

* [isa.md](isa.md): the spec.
* [verification.md](verification.md): the method, its limits, bugs found.
* [physical.md](physical.md): floorplan, routability, signoff.
* `firmware/`: one `.pasm` per protocol; each header documents its timing and
  host protocol.
