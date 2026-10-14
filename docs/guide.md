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
* **A line unit** does line coding (NRZ, NRZI, Manchester), bit stuffing and
  clock recovery for core 0: USB low-speed fits on one core, and 10BASE-T
  receive works.
* **FIFO join** (from PIO) plus in-core preamble generation make 10BASE-T TX
  fit through the host port; the port itself is source-synchronous (SCK up to
  the core clock) with a quad mode: 193 Mbit/s payload in RTL simulation.
* **Verification as part of the design:**
  * a cycle-accurate model is checked against the RTL every cycle on random
    programs, with required coverage goals;
  * formal proofs of the FIFOs, the host port (bus safety, and its protocol
    against a pin-level reference model), core invariants and the line unit;
  * mutation checks;
* firmware tested against independent device models.

The comparison is with the RP2040 PIO instruction engine, not with the whole
microcontroller or its dedicated USB peripheral. RP2040 has two PIO blocks,
each with four state machines and 32 shared instruction slots. Its WAIT
encoding selects one GPIO, mapped pin or IRQ level. Implementing a timeout
or second-pin qualification therefore needs additional program or host logic;
this is an inference from the encoding. See the
[RP2040 datasheet, chapter 3](https://datasheets.raspberrypi.com/rp2040/rp2040-datasheet.pdf).
No head-to-head hardware throughput benchmark has been performed.
A simulated head-to-head against the pico-examples PIO programs (UART, SPI,
I2C, with fault injection) and Pico-PIO-USB is in
[pio-comparison.md](pio-comparison.md), including where PIO is better.

| Dimension | RP2040 PIO engine | protoemu tapeout configuration |
|---|---|---|
| Program storage | 32 slots shared by four state machines per block | Two private 256-word banks |
| WAIT | GPIO/pin/IRQ level | Level/edge, optional second-pin qualifier and register timeout |
| FIFO transport | On-chip bus and DMA | External SPI; sustained board transport remains unmeasured |
| Protocol assistance | Shift registers and instruction sequencing | CRC tap and one shared line unit for core 0 |

Program sizes below come from the generated board bundle. They describe
occupancy, not a speed comparison:

| Demonstration | Instructions / cores | Evidence |
|---|---|---|
| UART over virtual pin 24 | TX 4 + RX 13 / two cores | Generated bundle loaded via RTL SPI; 53 bytes in 4.639 ms simulated, including a 187.5-us service stall |
| USB without line unit | RX 143 + TX 49 / two cores | Concurrent SPI service, descriptors, errors, retries and bus reset |
| USB with line unit | 129 / core 0 | Same software service and end-to-end RTL checks; core 1 remains available |
| I2C master | 71 / one core | Device-model transactions and stuck-clock recovery |

The UART test demonstrates two independent programs communicating internally.
The USB variants demonstrate moving bit coding between firmware and the line
unit while preserving the host application interface. Physical timing and
board measurements still need their own evidence.

## Claims and evidence

| Claim | Evidence | Reproduce |
|---|---|---|
| The RTL matches the spec's model cycle for cycle | random programs, pins and host traffic; registers, WAIT trackers, FIFO levels and heads compared every cycle; coverage goals required | `cd test && LOCKSTEP_SEEDS=60 PE_NCORES=2 PE_NBANKS=2 PE_FIFO_LOG2=3 make TB=chip` |
| The tests catch bugs | 34 mutation definitions in model, RTL, firmware, assembler and host driver; the runner requires a passing baseline and an executed target-test failure | `python3 test/mutants.py` (use its recorded results for killed counts) |
| FIFOs (incl. join) are correct for all input sequences | k-induction against reference queues, depths 4 and 8 | `make -C formal fifos` |
| The host port is fast and safe | 193 Mbit/s simulated quad payload at SCK = clk; phase/duty sweeps and reset aborts; formal bus safety | `cd test && make COCOTB_TEST_FILTER=test_host_port_fast`; `make -C formal hostport` |
| Protocols work through the real host port | RTL UART, SPI, I2C, concurrent USB service, full Ethernet RX frames and overflow recovery | `cd test && make` |
| The routed netlist does the same | Run fo6's final netlist passes all 29 top-level tests at gate level (functional; main suite 25, compiled PIO 2, Ethernet capture 2); with its SDF, a five-test subset passes at typ, slow and fast at 50 MHz and a control case fails at slow/100 MHz; the v2-feature set (fast SPI with `SYNC_BYPASS`, WAIT kind 7, CALL/RET at 50 MHz, full-speed USB at 48 MHz) and autonomous USB enumeration (low and full speed, 48 MHz) pass at all three corners. Hash-bound receipts for validation roots `d916d40`, `f1cb7c9` and `7685aba` (the certified software) ([release-evidence.md](release-evidence.md)) | `flow/release_evidence.py <run> --validation-commit <rev>`, or `flow/gl_test.sh <run>`; `flow/gl_sdf_test.sh <run> [v2\|v2long]` |
| USB device service, on two cores or one | Concurrent RTL service at 48 MHz; address changes, lost ACK, malformed PID/CRC/stuffing, duplicate OUT, interrupted SETUP and reset | `cd test && make COCOTB_TEST_FILTER=test_usb_concurrent_service`; model USB tests |
| Capture, understand, impersonate (reverse engineering) | core 0 captures a master and an EEPROM; the host decodes the transcript and register image; core 1 clones the EEPROM at the decoded address: same answers, same transcript; then a man in the middle changes a register. Capture timestamps within one 5-tick sample; sustained edge rate measured through the port | [reverse-engineering.md](reverse-engineering.md); `cd test && make COCOTB_TEST_FILTER=test_reverse_engineering`; `pytest test/model/test_reverse_engineering.py` |
| I2C can't hang | SCL stuck low at every one of 19 releases, plus before START | `pytest test/model/test_i2c_timeout.py` |
| 10BASE-T TX and RX | TX waveform decoded (Manchester, FCS, TP_IDL, link pulses), a 1518-byte frame streamed; RX from core 1's TX and from an external sender with jitter | `pytest test/model/test_eth10.py` |
| Host software works | the MicroPython driver and bring-up script, unmodified, against the model via an SPI port model, including the SPI master (modes 0-3, packed, preloaded) and UART capture helpers against pin-level devices; the PC reader of the UART-over-USB reports against the model's control transfers; the UART-over-USB pairs loaded by `bringup.usb_uart()` (RX on uio6) and read end to end by that reader with the host port idle. Not yet run on a demo board or a PC | `pytest test/model/test_micropython_driver.py test/model/test_micropython_spi_uart.py test/model/test_usb_uart_reports.py test/model/test_micropython_usb_uart.py` |
| Optional RP2 PIO/DMA transport | Compiled PIO instruction traces drive the ASIC SPI port; API/error-path tests use fakes; board rate and pad margin remain unmeasured | [board-transport.md](board-transport.md); `make -C test COCOTB_TEST_MODULES=test_pio SIM_BUILD=sim_build/pio COCOTB_RESULTS_FILE=pio.xml` |
| Finite Ethernet board capture | Actual capture driver over RTL/checkpoint GL SPI: 64/1518-byte frames, FCS failure and overflow cleanup; analog circuit remains unbuilt | [eth10-receiver.md](eth10-receiver.md); `make -C test COCOTB_TEST_MODULES=test_eth_service SIM_BUILD=sim_build/eth_service COCOTB_RESULTS_FILE=eth_service.xml` |
| Physical signoff | Run fo6: DRC/LVS/antenna clean, setup met at 50 MHz and hold met at all three corners, no slew, capacitance or fanout violation at any corner; `flow/release_check.py` passes with no waiver | `flow/harden.sh NAME`, then `python3 flow/signoff.py <run>/runs/wokwi` and `flow/release_check.py` ([release-evidence.md](release-evidence.md)) |
| The RTL runs on hardware | The RTL built for a Kria KV260 FPGA at 50 MHz (48 MHz for USB): firmware loading, UART across real pins, quad host port at 193.6 Mbit/s, most firmware including all post-freeze protocols against the other core or the FPGA's pattern generator, autonomous USB enumeration against a replayed host waveform. FPGA fabric, not the IHP netlist or its pads | [fpga/kv260](../fpga/kv260/README.md) (needs the board) |
| Protocols added with the RTL frozen | 14 protocols in firmware after the freeze tag: static timing checker, pins-only models, sigrok decoders, RTL through the SPI port; the same 16 tests on fo6's gate-level netlist (functional 16/16; with SDF at typ 14/16, `pf_dmx512` and `pf_wiegand` failing on exact-picosecond timing assertions), KV260 | [post-freeze-protocols.md](post-freeze-protocols.md); `make -C test COCOTB_TEST_MODULES=test_post_freeze`; `flow/gl_module_test.py run <run> --validation-root .` |

(`pytest` = `PYTHONPATH=tools python -m pytest -q` from the repo root.)

## Known limits

* Two cores are selected for tapeout. The release candidate (run fo6) meets
  50 MHz at all three corners; fast-corner hold passes by 0.095 ns, the
  thinnest margin (docs/physical.md).
* 10BASE-T RX needs the 50 MHz clock for 802.3's ±13.5 ns of jitter; at 40 MHz (12.5 ns half-cycles) it tolerates ±12 ns (a one-off model sweep, not a regression test: 0/100 frames failed at ±12, 27/100 at ±13.5 with every edge at the extreme).
* USB: endpoint 0 in firmware; requests and data toggles are host software.
* Host link: 193 Mbit/s simulated payload (quad SPI, SDR, SCK = clk = 50 MHz,
  96.5 % of the wire rate); no sustained board measurement. DDR was evaluated
  and left out: words would arrive every 4 SCK,
  but the clock side needs about 6 cycles to commit a sent word and fetch the
  next, and it reads 2 words ahead, so full-rate DDR reads need a third read
  reservation per core (another FIFO read port: the kind of mux that cost 4 %
  of a core's wiring). Without it DDR reads run at about SCK = 33 MHz, 264
  Mbit/s, with half the timing window on TT's pads.
* CAN: frames with stuffing, CRC, arbitration, ACK and stuff-error detection;
  not a full controller (no error counters or bus-off).
* Protocol timing is checked in clock cycles against behavioural device models,
  not against electrical specifications.
* Gate-level simulation is functional (no SDF) in TT's CI. The SDF-annotated
  runs (Icarus, on the compute runner) have no setup/hold checks and cover only exercised paths, so
  delay validation needs STA.

## Where things are

* [isa.md](isa.md): the spec.
* [verification.md](verification.md): the method, its limits, bugs found.
* [physical.md](physical.md): floorplan, routability, signoff.
* `firmware/`: one `.pasm` per protocol; each header documents its timing and
  host protocol.
