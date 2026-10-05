# External review: findings and fixes

In October 2026 a second agent (Codex) reviewed the design, reproduced several
bugs, and fixed them. This page records what it found, what changed, and which
tests now cover each fix. Finding codes are the review's.

## Bugs found and fixed

| Code | Finding | Fix | Covered by |
|---|---|---|---|
| R3 | Global reset during an active SPI transaction left the host port's SCK-side parser half-way through a command. | After reset the clk side ignores host-port events, and the quad drivers stay off, until it has seen CS high (`host_ready`, pe_hostport.sv), so the interrupted command has no effect; the SCK side resets at that CS rise as before. Writes need an active write transaction. (The review's version also held the SCK-side parser in reset; that tied the global reset into the SCK flops' resets and cost about 10 % more wire, enough to congest routing.) | `test.py` reset tests (stopped SCK, read-ahead abort, every core afterwards); `formal/hostport.sby` |
| R2 | The SDC cut every SCK↔clk path (`set_clock_groups`), so the held buses between the domains were never timed. | `set_max_delay -ignore_clock_latency` bounds both directions to one clock period; only cross-clock hold is cut. Synchronizer flops carry `async_reg`. | STA at every corner (`src/timing_checks.tcl` adds a CDC report) |
| R4 | USB firmware accepted a DATA packet with a corrupt PID. | Both variants compare the full SYNC+PID byte, complement bits included; SETUP data must be DATA0; bad packets are not ACKed. | `test_usb_model.py`, concurrent USB service on the RTL (`test.py`) |
| R5 | A USB bus reset didn't restore the default address. | Sustained SE0 raises `F_RESET`; the device software stops the cores, clears state, restores address 0 and restarts on J. | `test_usb_enum.py` (reset at address 5, with transfers pending), RTL service test |
| R6 | The MicroPython driver's pin-owner helper overwrote earlier assignments (PIN_OWNER is write-only). | The driver keeps a shadow of the owner map. | `test_micropython_driver.py`, `test_host_validation.py` |
| R7 | Loaders accepted programs that overran their bank or jumped out of it, which faults at run time with fixed banks. | Loaders, exporters and the MicroPython bundle check bank bounds, bases, wrap ranges and jump targets before writing anything. | `test_host_validation.py` |
| V1 | `flow/gl_test.sh` could report an old results file after a failed build. | Each GL run uses fresh private output directories and fails if any suite fails or produces no results. | `test_flow_tools.py` (compile, startup, missing-XML and per-suite failures) |
| C1 | `src/config.json` and `flow/gen_config.py` had drifted apart. | `gen_config.py --check` fails if the committed config isn't exactly the generator's; CI runs it. | `test_flow_tools.py`, GDS workflow |

## Verification added

* **USB:** concurrent device service over the real SPI port on the RTL for both
  firmware variants: descriptors in chunks, data toggles, lost ACK, a new
  SETUP mid-transfer, invalid PIDs and bus reset.
* **10BASE-T RX:** 64-, 128- and 1518-byte frames with FCS through the RTL
  while quad SPI drains the FIFO, with jitter, 96-bit gaps, service stalls,
  and a forced overflow followed by a clean frame.
* **Host link:** SCK duty cycles from 25 to 75 % at SCK = clk / 2.
* **CI** runs the formal proofs and the mutation check with the simulation
  suites, and checks the generated configuration and MicroPython bundle.

## Host software added

* `tools/micropython/quad_spi.py`, `pio_quad.py`: an optional RP2350 PIO/DMA
  transport for the quad host port. Tested against the RTL with the compiled
  PIO program driving the chip's SPI pins (`test_pio.py`); not yet measured on
  a board ([board-transport.md](board-transport.md)).
* `tools/micropython/usb_device.py`, `usb_bits.py`: the USB device software
  (endpoint 0) in portable form for the board.
* `tools/micropython/eth10_rx.py`: bounded 10BASE-T frame capture on the board
  ([eth10-receiver.md](eth10-receiver.md)); tested on the RTL
  (`test_eth_service.py`), with a proposed receive circuit that hasn't been
  built.

## Review recommendations not adopted

* A hash-bound "release gate" requiring setup slack at the slow corner and a
  hand-assembled evidence file. Tiny Tapeout signs off at the typical corner;
  `flow/signoff.py` checks DRC/LVS/antenna, typical setup, hold at every
  corner and the SRAM timing view per corner, and reports the slow corner.
* Real latch arcs during placement and routing. The FIFO latches receive data
  while open, and STA's time borrowing through them made each repair step
  take hours. The PnR SDC (`src/protoemu_pnr.sdc`) times them like flops; the
  same repair edits resulted in 4 instead of 79 minutes after CTS and 8
  instead of 114 after global routing. Signoff always times the real latch
  arcs.

## Still open

* Physical signoff of the current RTL, and gate-level tests on its netlist.
* Anything on hardware: RP2350 transport throughput, USB enumeration on a real
  host, the 10BASE-T analog front end.
* An opt-in clock-fanout repair step (`gen_config.py --clock-fanout-fix`) is
  in the flow but unused until a signoff run shows the clock-fanout limits it
  addresses.
