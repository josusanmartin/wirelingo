# protoemu on the Kria KV260

Hardware validation of the protoemu RTL (tapeout configuration: 2 cores,
256-word bank each, 8-deep FIFOs) on a Kria KV260, before silicon.

## Design

`kv260_top.v` wraps `tt_um_josusanmartin_protoemu`, staged for FPGA by
`flow/fpga_profile.py` (SRAM banks -> inferred RAM, latch FIFOs -> flops, gated
configuration clocks -> enables; `src/` is not modified). The Zynq PS reaches
the Tiny Tapeout pins through AXI GPIO on HPM0_FPD, so Linux bit-bangs the SPI
host port exactly like the TT demo board's RP2350:

| Register | Bits |
|---|---|
| gpio0 ch1 out `0xA0000000` | `[7:0]` ui_in, `[8]` rst_n, `[23:16]` uio pad value where protoemu doesn't drive |
| gpio0 ch2 in `0xA0000008` | `[7:0]` uo_out, `[15:8]` uio_out, `[23:16]` uio_oe, `[24]` MMCM locked |
| gpio1 ch1 in `0xA0010000` | Gray-coded count of protoemu clk cycles |

protoemu's clock is an MMCM output from pl_clk0 (100 MHz): 1200 MHz VCO / 24 =
50 MHz (`build.sh 25` for 48 MHz). It is asynchronous to the AXI side, as the
chip is to its host. SCK (ui[6]) goes through a BUFG and is constrained as
its own clock, mirroring `src/protoemu.sdc` (bundled-data max delay of one clk
period between SCK and clk). The only package pin is the carrier fan enable
(A12), held on.

## Build, install, test

    fpga/kv260/build.sh              # stage, rsync to runner1, Vivado batch, bootgen; fetch to out/
    fpga/kv260/install.sh            # copy to the Kria, dtc the overlay, xmutil loadapp protoemu
    ssh ubuntu@kria.[tailnet domain redacted] sudo python3 protoemu/fpga/kv260/host/bringup_kria.py

`build.tcl` is a non-project batch flow (in-memory project for the block
design): PS + AXI interconnect + 2 AXI GPIO, then synth/place/route of
`kv260_top`, reports (`utilization.rpt`, `timing_summary.rpt`, ...) and
`protoemu.bit`; `build.sh` converts it with bootgen to `protoemu.bit.bin`.
`protoemu.dtsi` is the overlay (bitstream, pl_clk0 = 100 MHz, HPM0_FPD 128-bit
via config-afi); `shell.json` makes it an XRT_FLAT app for dfx-mgr.

`host/pe_kria.py` maps the GPIO through `/dev/mem` and presents it as the demo
board's `tt` object, so `tools/micropython/protoemu.py` runs unmodified;
`QuadTransport` adds the quad host port. `host/bringup_kria.py` steps:

* `clock`: protoemu clk measured from the cycle counter
* `id`: reset, read 0x800 = 0x50450002
* `regs`: 200 rounds of random R0..R3 writes/readbacks on both cores
* `pins`: ui[4:0]/uio pads -> PINS; PIN_OUT/PIN_OE -> uo_out/uio_out/uio_oe; open drain
* `loopback`: firmware/uart_tx + uart_rx (assembled by tools/pe, loaded over SPI),
  core 0 -> virtual pin 24 -> core 1 at 115200, FIFO readback
* `uart_tx_pin`: uart_tx on uo[0] at 9600, decoded by polling the GPIO
* `uart_rx_pin`: Python bit-bangs 9600 8N1 on ui[0], core 1 receives
* `quad`: quad ID read and burst write/read, cross-checked over 1-bit

Offline checks of the same steps (no hardware):

    python3 fpga/kv260/host/bringup_kria.py --model    # cycle model + SPI port model
    make -C fpga/kv260/sim                             # RTL of kv260_top + staged src/ (cocotb, icarus)

## Status (2026-10-05, Vivado 2026.1 BASIC, clk_div 24)

Build (`out/*.rpt`): timing met with protoemu at 50 MHz (pl_clk0 constrained at
100 MHz, as Linux sets it): WNS +6.200 ns / WHS +0.028 ns on the protoemu clock,
+7.606 / +0.074 ns on SCK, +5.862 / +0.015 ns on pl_clk0 (design WNS +5.862,
WHS +0.015). Utilization: 8019 LUTs (protoemu 6439), 4836 FFs (protoemu 2515),
2 RAMB18 (one per IMEM bank: the banks inferred block RAM), 42 LUTRAM (all in
the AXI interconnect; protoemu uses none), 0 DSP. Methodology: TIMING-2/4
critical warnings for the SCK clock defined on the BUFG output (a
software-toggled clock with no proper source; its latency is not used by the
datapath-only cross-clock checks); TIMING-9/10 for protoemu's synchronisers
without ASYNC_REG.

Hardware (KV260, Ubuntu 24.04, `xmutil loadapp protoemu`): pl0_ref reads
99999999 Hz; the cycle counter measured protoemu clk = 49.9996 MHz. All
`bringup_kria.py` steps pass (four runs, plus one with `--settle 0`):

    [clock]       protoemu clk measured 49.9996 MHz
    [id]          ID (0x800) = 0x50450002
    [regs]        200 rounds: R0..R3 of both cores written and read back, single and burst
    [pins]        inputs: ui[4:0] and undriven uio pads reach PINS through the synchronisers
                  outputs: PIN_OUT/PIN_OE drive uo_out/uio_out/uio_oe; open drain on P0 works
    [loopback]    loaded uart_tx (4 words, core 0) and uart_rx (13 words, core 1)
                  sent/got b'Hello from protoemu on a KV260!'
    [uart_tx_pin] 53 edges on uo[0], decoded b'protoemu' at 9600 baud
    [uart_rx_pin] drove b'KV260 ok' on ui[0]; core 1 received b'KV260 ok'; framing error flag 0
    [quad]        quad: ID = 0x50450002; 50 burst write/read rounds cross-checked over 1-bit

The fan pin (A12, `fan_en_b`, active low per AMD's starter-kit platform and
the Linux pwm-fan node's inverted polarity) is held low; with the app loaded
the AMS temperatures fell from about 31 to 28 C over two minutes.

Offline: `--model` passes id/regs/pins/loopback; the RTL simulation passes
id/regs/pins/loopback/quad.

## FPGA vs ASIC differences

* IMEM is inferred RAM/flops instead of the IHP SRAM macros; FIFOs are enable
  flops instead of latches; configuration registers use clock enables instead
  of gated clocks (all from `flow/fpga_profile.py`).
* The uio pads are modelled in the wrapper: `uio_in = uio_oe ? uio_out : PS value`
  (the PS value plays the external driver or pull-up; no contention model).
* Host timing: the PS writes GPIO at roughly microsecond intervals, so the
  host port runs far below its SCK = clk limit; pad and board timing of the
  real chip are not exercised.
* The host must keep CS_n high for 5 clk after reset release (docs/isa.md §6.1);
  `Board.reset()` does. In simulation, starting a read 2-3 clk after reset
  release returns a wrong ID, as specified.
