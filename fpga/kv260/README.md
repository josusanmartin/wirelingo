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
chip is to its host. Package pins: the carrier fan enable (A12, held on) and
the PMOD header (below, disabled by default).

### Fabric SPI master (`pe_spim.v`, AXI4-Lite at `0xA0020000`)

To run the host port near its specified speed, `pe_spim` drives CS_n, SCK and
IO[3:0] when `SEL.FABRIC` = 1 (the GPIO path is selected at load, so
`bringup_kria.py` is unchanged). Linux fills a 4096-word buffer and writes
CMD/LEN/CFG/GO; the master sends the command byte 1-bit on IO3, the address
and data 1-bit or quad, 8 dummy SCK before read data, and stores read words in
the buffer (register map in `pe_spim.v`, driver `host/pe_spim.py`, which is a
ProtoEmu transport).

Clocking: the master runs on `mclk`, selected by a BUFGMUX_CTRL (`SEL.ALT`):
* **shared**: protoemu's own 50 MHz clock (same MMCM output, separate global
  buffers). SCK is then phase-locked to clk, which is easier than the ASIC's
  arbitrary SCK phase; Vivado times the port's SCK domain and clk paths as
  synchronous.
* **alt**: a 45.93 MHz MMCM output (1200 MHz / 26.125). Same reference, but
  the phase against clk walks through fine steps (fractional divider); the
  constraints treat it like the ASIC's asynchronous SCK (bundled-data max
  delay, `src/protoemu.sdc`). Not a truly independent oscillator.

SCK is `mclk` gated by a BUFGCE: one high pulse of half an mclk period every
`CFG.DIV` mclk cycles (DIV = 1: SCK = mclk). A BUFGCTRL then selects it or the
GPIO SCK; the selection changes only while CS_n is high. The master changes IO
right after the SCK rise that sampled the previous beat and samples the port's
IO on each SCK rise; CS_n setup/hold/gap are 2/3/8 mclk by default (`TIMING`
register), above the spec's 1/1/4 clk minimums. `PCYC` counts protoemu clocks
while ui_in[7] (CS_n) is low, through a 2-flop synchroniser: the throughput
numbers below are payload bits / (PCYC / measured clk), i.e. the transfer
itself, excluding Linux's buffer fill and register accesses.

## Build, install, test

    fpga/kv260/build.sh              # stage, rsync to runner1, Vivado batch, bootgen; fetch to out/
    fpga/kv260/install.sh            # copy to the Kria, dtc the overlay, xmutil loadapp protoemu
    ssh ubuntu@kria.[tailnet domain redacted] sudo python3 protoemu/fpga/kv260/host/bringup_kria.py
    ssh ubuntu@kria.[tailnet domain redacted] sudo python3 protoemu/fpga/kv260/host/spim_test.py

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

`host/spim_test.py`, for each master clock (shared, alt), SCK = mclk/4, /2,
/1 and 1-bit/quad: 50 rounds of R0..R3 writes/readbacks on both cores with
GPIO-path cross-checks both ways and 64 READ_FIXED of the ID; a 4096-word
WRITE_FIXED into core 0's TX FIFO consumed by `host/sink.pasm` (sum of
16-bit halves and word count, read back over the GPIO path; a dropped or
corrupted word changes them); READ_FIXED bursts of 4096, 1, 7, 1000 and 64
words from core 1's RX FIFO filled by `host/source.pasm` (every word checked
against the known sequence, continuing across bursts, and the next word read
over the GPIO path); at SCK = mclk quad also the spec's 64-word quad
WRITE_FIXED. The firmware is loaded through the fabric master.

Offline checks of the same steps (no hardware):

    python3 fpga/kv260/host/bringup_kria.py --model    # cycle model + SPI port model
    make -C fpga/kv260/sim                             # RTL of kv260_top + staged src/ (cocotb, icarus)

The simulation's `test_spim` runs `spim_test.py` (16-word bursts) through the
fabric master at SCK = mclk/4, /2, /1, 1-bit and quad, on both clocks.

## Status (2026-10-05, Vivado 2026.1 BASIC, clk_div 24)

### Current build (fabric master + PMOD)

Timing met (`out/timing_summary.rpt`): design WNS +2.078 ns, WHS +0.013 ns.

| Clock | WNS | WHS |
|---|---|---|
| protoemu clk (50 MHz) | +4.280 ns | +0.013 ns |
| mclk_pe (master on protoemu's clock) | +2.078 ns | +0.051 ns |
| mclk_alt (45.93 MHz) | +2.962 ns | +0.051 ns |
| GPIO SCK | +6.676 ns | +0.098 ns |
| pl_clk0 (100 MHz) | +5.059 ns | +0.022 ns |
| mclk_pe <-> protoemu clk (synchronous) | +5.708 / +10.785 ns | +0.233 / +0.187 ns |
| mclk_alt <-> protoemu clk (max delay 20 ns) | +15.299 / +14.278 ns | (cut) |

The worst mclk_pe path is the port's IO3 output (launched on the SCK fall)
into the master's capture register on the next rise: a half-period (10 ns)
path with -4.49 ns clock skew, because SCK goes through two cascaded global
buffers (BUFGCE, BUFGCTRL) after mclk's BUFGMUX. Utilization: 8506 LUTs
(protoemu 6330, pe_spim 576), 5338 FFs (protoemu 2513, pe_spim 541), 4 RAMB36
(the master's buffer) + 2 RAMB18 (protoemu's IMEM banks, one each), 38 LUTRAM
(AXI interconnect), 9 bonded IOBs (fan + 8 PMOD), 0 DSP. Methodology:
TIMING-4 critical warning for the GPIO SCK clock defined on the BUFGCTRL input
(a software-toggled clock with no proper source).

Hardware: protoemu clk measured 49.9979 MHz (cycle counter). `bringup_kria.py`
still passes (GPIO path). `spim_test.py` passed in four runs; one run's output:

| Master clock | SCK | Mode | 4096-word write | 4096-word read |
|---|---|---|---|---|
| shared 50 MHz | mclk/4 | 1-bit | 12.5 Mbit/s (524386 clk) | 12.5 Mbit/s (524418 clk) |
| shared | mclk/4 | quad | 50.0 (131122) | 50.0 (131154) |
| shared | mclk/2 | 1-bit | 25.0 (262196) | 25.0 (262212) |
| shared | mclk/2 | quad | 100.0 (65564) | 99.9 (65580) |
| shared | mclk/1 | 1-bit | 50.0 (131101) | 50.0 (131109) |
| shared | mclk/1 | quad | 199.9 (32785) | 199.8 (32793) |
| alt 45.93 MHz | mclk/4 | 1-bit | 11.5 (570816) | 11.5 (570851) |
| alt | mclk/4 | quad | 45.9 (142732) | 45.9 (142766) |
| alt | mclk/2 | 1-bit | 23.0 (285411) | 23.0 (285429) |
| alt | mclk/2 | quad | 91.8 (71369) | 91.8 (71387) |
| alt | mclk/1 | 1-bit | 45.9 (142709) | 45.9 (142718) |
| alt | mclk/1 | quad | 183.6 (35688) | 183.6 (35696) |

Every configuration passed all its checks: register round trips and GPIO
cross-checks, the sink's count (4096) and sum, and 5168 RX words in sequence
plus the next word over GPIO. The 64-word quad WRITE_FIXED at SCK = clk took
529 protoemu clocks: 193.6 Mbit/s, the same as `test_host_port_fast`'s
simulated 193 Mbit/s (the simulation of this design also gives 529 clk).
With the alt clock it took 576 clocks (177.8 Mbit/s; SCK is 45.93 MHz there).
Clock counts are of CS_n low, through a 2-flop synchroniser (about +-1 clk).
The pin-level timing margins are the FPGA's, not the IHP pads'.

Temperatures with this bitstream (fan held on): 27.4-29.8 C over two minutes.

### Milestone 1 build (GPIO path only)

Build: timing met with protoemu at 50 MHz (pl_clk0 constrained at
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

## PMOD J2 (prepared, not tested on hardware)

Each of the 8 PMOD signal pins can carry one TT pin, chosen by `pe_spim`'s
PMOD0/PMOD1 registers (`Spim.pmod({pmod_index: tt_pin})`, `tt_pin` 0-7 =
uio[0..7], 8-14 = uo[0..6], 16-20 = ui[0..4]). Nothing drives a PMOD pin
unless it is enabled: at load every pin is an input (high impedance) and the
TT pins keep the GPIO-modelled behaviour. An enabled uio pin is driven while
protoemu's uio_oe is 1 and protoemu reads the pad's level (open drain works
with an external pull-up); a uo pin is always driven; a ui pin replaces the
GPIO bit. `Spim.pmod_in()` reads the 8 pad levels. LVCMOS33, 4 mA, slow slew,
no internal pull-ups.

| pmod index | package pin | J2 pin |
|---|---|---|
| 0 | H12 | 1 |
| 1 | E10 | 2 |
| 2 | D10 | 3 |
| 3 | C11 | 4 |
| - | GND | 5 |
| - | 3.3 V | 6 |
| 4 | B10 | 7 |
| 5 | E12 | 8 |
| 6 | D11 | 9 |
| 7 | B11 | 10 |
| - | GND | 11 |
| - | 3.3 V | 12 |

Package pins and IOSTANDARD are AMD's (Kria-Vitis-Platforms
`kv260/platforms/kv260_bist/xdc/pin.xdc`, PMOD_GPIO[0..7]; the XilinxBoardStore
KV260 carrier board files describe only the SOM connector, not the PMOD). The
J2 pin numbers are inferred from AMD's `kv260_ispMipiRx_vcu_DP` platform,
which puts a Digilent Pmod I2S2 on these pins (H12/E10/D10/C11 = its TX
MCLK/LRCK/SCLK/SDIN on pins 1-4, B10/E12/D11/B11 = RX on pins 7-10); the
jumper self-test below checks the row pairing.

**(i) Jumper loopback self-test.** Four jumpers: J2 1-7, 2-8, 3-9, 4-10.
Then `sudo python3 protoemu/fpga/kv260/host/pmod_test.py`: drives each
top-row pin from uo0 and checks that only its partner follows (PMODIN),
runs UART at 115200 from core 0 on uo0 (J2-1) to core 1 on ui0 (J2-7), and
pulls uio2 (open drain, J2-2) low and reads it on uio3 (J2-8).

**(ii) USB low-speed device on the Kria's own USB-A port.** Rebuild at 48 MHz
(`build.sh 25`: the USB firmware assumes 48 MHz; at 50 MHz it would signal
4 % fast, outside low speed's 1.5 % tolerance). Cut a USB-A cable or use a
USB-A breakout:
* J2-1 (pmod0 = uio0) -> 27 ohm -> D+ (green)
* J2-2 (pmod1 = uio1) -> 27 ohm -> D- (white)
* 1.5 kohm from D- (connector side of its resistor) to 3.3 V (J2-6): marks a low-speed device
* J2-5 (GND) -> GND (black); leave VBUS (red) unconnected.

Software: `spim.pmod({0: 0, 1: 1})`, then `tools/micropython/bringup.usb_setup(pe)`
with `pe = board.protoemu()` (preferably with `pe.transport = spim` on the
fabric path) and call `sw.service()` in a loop; watch `dmesg` on the Kria.
Caveat: the FPGA's edges are a few ns, far faster than low speed's
75-300 ns rise/fall specification; short wires help.

**(iii) I2C device** (3.3 V breakout, e.g. an EEPROM or sensor):
* J2-1 (pmod0 = uio0) = SDA, J2-2 (pmod1 = uio1) = SCL
* 4.7 kohm pull-ups from SDA and SCL to 3.3 V (J2-6), unless the breakout has them
* device VCC to J2-6 (3.3 V), GND to J2-5

Software: `spim.pmod({0: 0, 1: 1})`, `OD_MASK` = 0x03 and `PIN_OUT` bits 0-1 = 1
(release the lines) before starting `firmware/i2c_master.pasm` (`I2C_MASTER`
in `tools/micropython/protoemu_fw.py`, 100 kHz at 48 MHz; recompute CLKDIV for 50 MHz).

None of (i)-(iii) has been wired or run; `pmod_test.py` itself has not been executed.

## FPGA vs ASIC differences

* IMEM is inferred RAM/flops instead of the IHP SRAM macros; FIFOs are enable
  flops instead of latches; configuration registers use clock enables instead
  of gated clocks (all from `flow/fpga_profile.py`).
* The uio pads are modelled in the wrapper: `uio_in = uio_oe ? uio_out : PS value`
  (the PS value plays the external driver or pull-up; no contention model).
* Host timing: the GPIO path toggles pins at roughly microsecond intervals.
  The fabric master reaches SCK = clk, but with SCK phase-locked to clk
  (shared) or derived from the same reference (alt), on FPGA routing; the IHP
  pads, board traces and a truly asynchronous host are not exercised.
* The host must keep CS_n high for 5 clk after reset release (docs/isa.md §6.1);
  `Board.reset()` does. In simulation, starting a read 2-3 clk after reset
  release returns a wrong ID, as specified.
