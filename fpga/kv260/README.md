# Wirelingo (protoemu) on the Kria KV260

Hardware validation of the chip RTL (tapeout configuration: 2 cores,
256-word bank each, 8-deep FIFOs) on a Kria KV260, before silicon. Since
branch `kv260-v2` the design is built from the v2 RTL (NRZI half-cycle
sample point, SYNC_BYPASS, WAIT kind 7, CALL/RET), and every bitstream says
which RTL revision it is (below).

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

### Build identity and demo fabric (`pe_lab.v`, AXI4-Lite at `0xA0030000`)

**Revision identification.** `build.sh` runs `build_id.py`, which writes
`build_id.vh` into the staged sources; `pe_lab` returns it as read-only
registers: the *RTL revision* (the last commit that touched the RTL inputs:
`src/`, `flow/fpga_profile.py`, `fpga/kv260/{build.tcl,kv260_top.v,pe_spim.v,pe_lab.v,kv260.xdc}`),
the checkout's HEAD, a dirty flag, the build time and the first 32 bits of a
SHA-256 over the exact staged RTL and fabric files. `build.sh` refuses to
build from uncommitted RTL inputs (`ALLOW_DIRTY=1` builds one marked dirty).
`install.sh` writes `host/expected_build.json` on the Kria from the checkout
it installs, and `bringup_kria.py`'s first step (`build`) prints the
identity and fails unless the bitstream's RTL revision equals it (or
`--expect REV`) and is clean. Commits that change only docs, tests or host
software keep the RTL revision, so they don't force a rebuild; one that
changes RTL does. `host/pe_lab.py` reads it: `Lab().identity()`.

**Demo fabric.** `MODE` chooses what drives the uio pads protoemu leaves
undriven: the GPIO path (reset), a **pattern generator** (1024 entries of
value + delay, played on protoemu's clock or the 45.93 MHz alternate MMCM
clock; every change is one register, so its times are exact in its clock),
an **SPI device** (mode 0, MISO on uio4 for SCK = uio2 / MOSI = uio3, on a
400 MHz clock from the same MMCM, 8 cycles per protoemu clock and
phase-aligned; it registers SCK/MOSI at every 2.5 ns edge and puts the next
MISO bit out DELAY + 2 cycles after the protoemu clock edge that dropped SCK,
DELAY = 0..31; it logs MOSI and the SCK period), or an **I2C bus** joining
uio0/uio2 (SDA) and uio1/uio3 (SCL) as wired-AND lines with pull-ups, so the
chip's two cores can talk I2C to each other. Register map in `pe_lab.v`.
The SPI device's two crossings (protoemu's SCK/MOSI into its first flops,
its MISO flop into protoemu's first synchroniser flop) are timed as ordinary
2.5 ns synchronous paths and meet timing, so its behaviour is a fixed number
of 2.5 ns steps, not a race; with clk_div 25 (48 MHz) its clock is not 8x.

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
    fpga/kv260/build.sh 25 && APP=protoemu48 fpga/kv260/install.sh   # the 48 MHz build (USB) as a second app
    ssh ubuntu@kria.[tailnet domain redacted] sudo python3 protoemu/fpga/kv260/host/bringup_kria.py
    ssh ubuntu@kria.[tailnet domain redacted] sudo python3 protoemu/fpga/kv260/host/spim_test.py
    ssh ubuntu@kria.[tailnet domain redacted] sudo python3 protoemu/fpga/kv260/host/v2_demo.py call la spi i2c --save la.json
    scp ubuntu@kria.[tailnet domain redacted]:la.json /tmp/ && python3 fpga/kv260/host/v2_demo.py la-model /tmp/la.json  # off the board

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

## Status (2026-10-07): v2 RTL, branch `kv260-v2`

### Build

RTL revision `d1aa46f` (v2 7db7d61 + `pe_lab`), digest `3adc796c`,
Vivado 2026.1 BASIC on runner1 (4 threads), clk_div 24. Timing met, 0
critical warnings (`out/timing_summary.rpt`): design WNS +0.196 ns, WHS
+0.017 ns, both on the SPI device's 2.5 ns crossings.

| Clock / crossing | WNS | WHS |
|---|---|---|
| protoemu clk (50 MHz) | +4.608 ns | +0.027 ns |
| mclk_pe / mclk_alt (fabric master) | +3.977 / +4.861 ns | +0.040 ns |
| SPI device clock (400 MHz) | +0.520 ns | +0.017 ns |
| protoemu clk -> SPI device (SCK, MOSI; 2.5 ns) | +0.276 ns | +0.157 ns |
| SPI device -> protoemu (MISO into sync1 / the line unit's falling-edge flop) | +0.474 / +0.196 ns | +0.219 ns |
| pattern generator on protoemu's clock -> protoemu | +7.279 ns | +0.381 ns |
| pattern generator on the alt clock -> protoemu (max delay 5 ns) | +2.288 ns | (cut) |
| GPIO SCK / pl_clk0 | +7.523 / +4.467 ns | +0.085 / +0.021 ns |

Utilization: 9996 LUTs (protoemu 6534, pe_lab 1233 of which 640 LUTRAM for
the generator's table, pe_spim 550), 6046 FFs (protoemu 2545), 4 RAMB36
(pe_spim's buffer) + 2 RAMB18 (protoemu's IMEM banks), 9 IOBs, 0 DSP.
`flow/fpga_profile.py` from v2 (its global configuration conversion now
includes SYNC_BYPASS) staged the v2 RTL unchanged.

### Hardware (2026-10-07, after a power cycle)

`install.sh` + `xmutil loadapp protoemu`: loaded (slot 0). Identity:

    [build]
      bitstream RTL revision d1aa46f7ed7e94adc36b895a591338be51da2b7b
      built 2026-10-06 22:07:40 UTC from HEAD d1aa46f7ed7e, RTL digest 3adc796c, clk_div 24
      expected RTL revision d1aa46f7ed7e94adc36b895a591338be51da2b7b (expected_build.json)
      PASS

and with `--expect 7db7d61`: `FAIL: loaded bitstream is RTL revision
d1aa46f7ed7e, expected 7db7d61`. `bringup_kria.py`: ALL PASS (clock
49.9983 MHz; id, regs, pins, loopback, uart_tx_pin, uart_rx_pin, quad).
`spim_test.py`: ALL PASS, all 12 configurations, the same throughput as the
g4 build below (quad at SCK = clk 199.9/199.8 Mbit/s, 64-word quad write
193.6 Mbit/s in 529 clk; alt clock 183.6 Mbit/s, 64-word write 177.8 Mbit/s in 576 clk).

### v2 features on hardware (`host/v2_demo.py`, all through protoemu's pins)

Host traffic goes over pe_spim at SCK = clk, 1-bit. Everything below is
FPGA fabric timing; the IHP pads, the TT mux and board traces are not in
the loop.

**CALL/RET, directed** (`call`): a program with a conditional CALL not taken,
a CALL into a subroutine that saves LR in R3, calls another and restores it:
hardware R0..R3 = `0x65 0x6 0x2 0x2`, the cycle model's values, core halted.

**WAIT kind 7 / la_capture** (`la`): core 0 runs `la_capture` (MASK 0x0F) on
uio0-7 driven by the pattern generator; the host drains RXF with blind
8-word READ_FIXED bursts. Synchronous run (generator on protoemu's clock,
435 entries, 2.35 M clocks, 422 records, no gap):

    timestamps: 160 edges on 4 channels, 7-430 ticks apart (unwatched channels toggling):
                capture tick - drive tick = [58860] for every edge
    close edges (ch1 d ticks after ch0; lateness of ch1's timestamp, ticks):
                d=1: [-1], d=2: [5], d=3: [4], d=4: [3], d=5: [2], d=6: [1], d=7..10: [0]
    square waves on ch0: edge every 7 ticks: 35 of 35 edges captured, every 6 ticks: 30 of 40

So: **1-tick resolution** (one constant offset for every edge; its value is
the arbitrary start offset), **7-tick dead time** with exactly the firmware
header's rule (an edge 1 tick after a record is folded into it, 1 tick
early; 2-6 ticks after, stamped at W+7; from 7 on, exact; nothing lost but
pulses), and the **7-tick edge-rate bound**. `v2_demo.py la-model` replays the
same schedule on the cycle model: its 419 records after the first change are
**identical, word for word**, to the hardware's. Asynchronous run (generator
on the 45.93 MHz alternate clock, 240 edges on channel 0, >= 9 alt cycles
apart): all captured; capture tick - ideal time (alt cycle x 26.125/24)
spans **0.995 ticks**, spread evenly over the tick:

    {0.0: 14, 0.1: 20, 0.2: 26, 0.3: 23, 0.4: 27, 0.5: 29, 0.6: 24, 0.7: 17, 0.8: 21, 0.9: 24, 1.0: 15}

That is a sampled input resolved to the tick. Limits: the alternate clock
comes from the same 1200 MHz VCO as protoemu's (its phase walks; it is not
an independent oscillator); 8 records fit in RXF, so the schedule pauses
30000 ticks between bursts for the Python host (the sustained rate is the
host's, see docs/reverse-engineering.md).

**SYNC_BYPASS / SPI at 4 clocks per SCK** (`spi`): core 1 runs
`spi_master_fast` (CLKDIV 1) against pe_lab's SPI device, 8 rounds of 8
random bytes per cell; MOSI was right in every round (the device's log), SCK
rise to rise was 32 device cycles = 4 clocks (12.5 MHz) throughout.

| DELAY | device MISO after the chip's SCK-fall clock edge | no bypass | SYNC_BYPASS |
|---|---|---|---|
| 0-5 | 5.0-17.5 ns | ok | ok |
| 6-13 | 20.0-37.5 ns | 1 bit late | ok |
| 14-19 | 40.0-52.5 ns | 1 bit late | 1 bit late |

The window is less than 1 clock (17.5 ns of 20) without the bypass and less
than 2 clocks (37.5 ns of 40) with it, as `test/model/test_spi_fast.py` and
docs/pio-comparison.md state; the step is 2.5 ns, and both crossings meet
timing, so the boundaries are exact for this placement. **Fabric timing is
not the TT pads**: on silicon, the 40 ns must also cover the output pad and
mux (clock to SCK at the pin), board flight both ways and the input pad to
the first flop, none of which is modelled here.

**CALL/RET in a real program** (`i2c`): core 1 runs `i2c_device` (88 words, 6
CALLs: the bit loop, the event push with its stretch and the START/STOP
check are subroutines) at 0x50 with the host as its register model
(`pe.i2cdev`, a 256-byte image); core 0 runs `i2c_master` at 100 kHz;
pe_lab's I2C bus mode joins their pins.

    write 0x10, read 8   master replies ['0x0', '0x0', '0x0', '0xf1', '0x3f', '0xa6', '0xd', '0xe6', '0x28', '0x1c', '0x5f']
    write 0x20: CA FE    master replies ['0x0', '0x0', '0x0', '0x0']
    write 0x20, read 2   master replies ['0x0', '0x0', '0x0', '0xca', '0xfe']
    address 0x51         master replies ['0x1']

The answers are the image's bytes, the write lands, 0x51 is NACKed, no
stretch timeout (F1). Both ends are this chip, so this checks the CALL/RET
firmware and the two cores' bus behaviour against each other, not against
an external I2C device or I2C's electrical timing.

`make -C fpga/kv260/sim` runs the same demos (shortened) on the RTL:
`test_lab` (plus `test_bringup_steps`, `test_spim`; 3/3 pass).

## Status (2026-10-05, g4 RTL, Vivado 2026.1 BASIC, clk_div 24)

### Fabric master + PMOD build

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

## PMOD J2

**Hardware result (2026-10-07, v2 build): `pmod_test.py` PASSES.** All four
loopback pairs connect (each pin toggled six times, the partner must follow
every time), UART at 115200 from core 0 on pmod0 reaches core 1 on pmod4
over the jumper, and the open-drain pair reads low when driven low:

    J2 pin 1 driven: follows on pmod[4] (expected [4], J2 pin 7); PMODIN low 0xEE high 0xFF (6 toggles)
    J2 pin 2 driven: follows on pmod[5] (expected [5], J2 pin 8); PMODIN low 0xDD high 0xFF (6 toggles)
    J2 pin 3 driven: follows on pmod[6] (expected [6], J2 pin 9); PMODIN low 0x22 high 0xFF (6 toggles)
    J2 pin 4 driven: follows on pmod[7] (expected [7], J2 pin 10); PMODIN low 0x44 high 0xEE (6 toggles)
    UART 115200 J2 pin 1 -> pin 7: sent b'PMOD ok' got b'PMOD ok'
    uio2 open drain 0/release/0 on J2 pin 2 -> uio3 on pin 8 reads [0, 1, 0] (released level checked only with --pullup ...)
    PASS

**Header numbering.** The KV260's silkscreen numbers J2 in columns, not the
Digilent way: odd pins on the top row, even below, pin 1 at the top right,
11/12 at the left; the two left columns (9/10, 11/12) are GND and 3.3 V.
"J2-n" in the scripts and below uses the Digilent numbering (1-4 top signal
row, 7-10 bottom); the silkscreen equivalents are J2-1 = 1, J2-2 = 3,
J2-3 = 5, J2-4 = 7, J2-7 = 2, J2-8 = 4, J2-9 = 6, J2-10 = 8. The first
attempt put the jumpers in the four left columns by the Digilent picture,
so two of them joined GND to GND and 3.3 V to 3.3 V (harmless) and only two
pairs connected. Wiring diagram with the silkscreen numbers:
[img/pmod_loopback.png](img/pmod_loopback.png). The pairs are verified on
hardware; which signal column is pmod0..3 follows the Digilent order (pin 1
farthest from the power pins), which the loopback can't distinguish.

`pmod_test.py`'s first version checked each pairing with one toggle; an
undriven pair floats (no pull-ups), so a floating pad once looked like a
partner. It now requires six consistent toggles, and the open-drain released
level is only checked with `--pullup` and a resistor from J2-8 (silkscreen 4)
to 3.3 V.

J2 pin numbers are inferred from AMD's `kv260_ispMipiRx_vcu_DP` platform,
which puts a Digilent Pmod I2S2 on these pins (H12/E10/D10/C11 = its TX
MCLK/LRCK/SCLK/SDIN on pins 1-4, B10/E12/D11/B11 = RX on pins 7-10); the
jumper self-test below checks the row pairing.

**(i) Jumper loopback self-test.** Four jumpers: J2 1-7, 2-8, 3-9, 4-10 (silkscreen 1-2, 3-4, 5-6, 7-8).
Then `sudo python3 protoemu/fpga/kv260/host/pmod_test.py`: drives each
top-row pin from uo0 and checks that only its partner follows (PMODIN),
runs UART at 115200 from core 0 on uo0 (J2-1) to core 1 on ui0 (J2-7), and
pulls uio2 (open drain, J2-2) low and reads it on uio3 (J2-8).

**(ii) USB device on the Kria's own USB-A port**: the autonomous USB
firmware with three connections; see the next section.

**(iii) I2C device** (3.3 V breakout, e.g. an EEPROM or sensor):
* J2-1 (pmod0 = uio0) = SDA, J2-2 (pmod1 = uio1) = SCL
* 4.7 kohm pull-ups from SDA and SCL to 3.3 V (J2-6), unless the breakout has them
* device VCC to J2-6 (3.3 V), GND to J2-5

Software: `spim.pmod({0: 0, 1: 1})`, `OD_MASK` = 0x03 and `PIN_OUT` bits 0-1 = 1
(release the lines) before starting `firmware/i2c_master.pasm` (`I2C_MASTER`
in `tools/micropython/protoemu_fw.py`, 100 kHz at 48 MHz; recompute CLKDIV for 50 MHz).

(iii) has not been wired or run.

## USB device on the Kria's own USB port (`host/usb_demo.py`)

The chip's autonomous USB firmware (`firmware/usb_enum.pasm`,
docs/usb-enumeration.md: one core and the line unit answer every control
request themselves) runs in the FPGA and is wired through PMOD J2 to one of
the Kria's own USB-A ports. After the host software has loaded the firmware
and started the core, nothing touches the chip's host port: the Kria's Linux
USB stack (xHCI, through the carrier's USB5744 hub) enumerates and configures
the device, and `lsusb`/`dmesg` show it. Low speed (1.5 Mbit/s) first. The
build must run protoemu at exactly 48 MHz: `build.sh 25`, installed as the
separate app `protoemu48` (the 50 MHz app `protoemu` stays installed).

### Wiring (three connections; silkscreen numbers)

| What | Wire | J2 silkscreen | Scripts' J2-n (Digilent) | pmod[] (package pin) if the columns run as kv260.xdc assumes / the other way | Chip pin, low speed | Chip pin, full speed |
|---|---|---|---|---|---|---|
| USB D+ (usually green) | through a 27 ohm resistor | **1** (top row, right end) | J2-1 | pmod[0] (H12) / pmod[3] (C11) | uio0 (line unit TXP/RXP) | uio1 |
| USB D- (usually white) | through a 27 ohm resistor | **3** (top row, next to 1) | J2-2 | pmod[1] (E10) / pmod[2] (D10) | uio1 (line unit TXN/RXN) | uio0 |
| 1.5 kohm pull-up | from D- (cable side of its 27 ohm) to 3.3 V | **11** (top row, left end), expected 3.3 V | J2-6 | - | marks a low-speed device | (moves to D+) |
| USB GND (black), VBUS (red) | **not connected**: tape both off | - | - | - | - | - |

```
   J2 (socket) seen from above, board edge to the right:

      11    9    7    5    3    1        <- top row
      12   10    8    6    4    2        <- bottom row
      |                     |    |
    3.3 V (expected)       27R  27R      (27 ohm in series, one per line)
      |                     |    |
      +-------- 1k5 --------+    |       (1.5 kohm: D- to 3.3 V)
                            |    |
                           D-   D+   -> cut USB-A cable: white (pin 2), green (pin 3);
                                        red VBUS (pin 1) and black GND (pin 4) taped off
```

The 1.5 kohm goes from the cable side of D-'s 27 ohm to silkscreen 11 (the
top pin of the outermost left column; 12 below it should be the same rail).
That the left column is 3.3 V and the one next to it GND follows the
Digilent Pmod convention (pins 6/12 VCC, 5/11 GND) mapped to the silkscreen;
it is not verified on this board. If it is the other way round, the resistor
pulls D- to GND: the hub sees nothing and the device never attaches, and
`usb_demo.py` stops with "no pulled-up pin" before enabling anything; move
the resistor's end to silkscreen 9 and rerun.

**Why these pins.** Silkscreen 1 and 3 are the two signal pins at the right
end of the top row, farthest from the power columns. The line unit needs
its two pins adjacent (TXP = LINEPINS, TXN = the next pin) and J (idle) to be
the second pin high: at low speed D+ is uio0 and D- (pulled up) uio1, at full
speed the other way round (docs/usb-full-speed.md). The PMOD routing in
`kv260_top.v` maps any uio pin to any PMOD pin, so the same two wires serve
both speeds; only the pull-up moves. The jumper loopback verified which pins
pair vertically but not which signal column is pmod[0] (README above): either
way, `usb_demo.py` finds the pulled-up pin and maps the chip's pins to match.

**Parts.** Two 27 ohm resistors (22-33 ohm is fine), one 1.5 kohm (5 %), a
USB cable with an A plug to cut (or an A-plug breakout), male-to-male jumper
wires (J2 is a socket), optionally a small breadboard for the resistors.
Wire colours are a convention, not a guarantee: check with a meter (A plug,
contacts facing you, from the left: 1 VBUS, 2 D-, 3 D+, 4 GND).

**Safety.**
* No GND wire: the USB port and J2 are on the same board, so the grounds are
  already common. No VBUS wire: the port's 5 V must never reach a 3.3 V FPGA
  pin, so tape the red wire's end (and the black one's) individually.
* The 27 ohm resistors limit the current if the FPGA and the host ever drive
  a line at the same time; the FPGA pins are LVCMOS33, 4 mA, slow slew.
* Wire with the Kria powered off if convenient. With the pull-up in place
  the hub sees a device whenever the board is on, even before any app is
  loaded: Linux then logs a failed enumeration (`device descriptor read/64,
  error -71`, `unable to enumerate USB device`). That is harmless; the demo
  makes the kernel try again (soft detach and attach, below). Loading or
  unloading an app may also cause such lines (FPGA pins during
  reconfiguration).
* Every PMOD pin is disabled (high impedance) after an app loads, and stays
  so unless a script enables it: `bringup_kria.py` never does. Don't run
  `pmod_test.py` with this wiring (it drives the top-row pins as a loopback).
* What `usb_demo.py` drives: each top-row signal pin low for a few
  microseconds (the wiring check), then the pulled-up line low (2.2 mA through
  the 1.5 kohm: a soft detach) while it loads the firmware, then only the line
  unit, which drives D+/D- just for its answers to host packets.
* Low speed specifies 75-300 ns edges; the FPGA's are a few ns. Keep the
  wires short.

### Run

On the Kria (one process on the board at a time):

    sudo xmutil unloadapp; sudo xmutil loadapp protoemu48
    sudo python3 protoemu48/fpga/kv260/host/bringup_kria.py --mhz 48       # optional: build identity + chip checks
    sudo python3 protoemu48/fpga/kv260/host/usb_demo.py                     # the demo
    lsusb -d 1209:5634; sudo dmesg | tail
    sudo python3 protoemu48/fpga/kv260/host/usb_demo.py --stop              # again, detaching at the end
    sudo xmutil unloadapp; sudo xmutil loadapp protoemu                     # back to the 50 MHz app

`usb_demo.py` checks the bitstream is the 48 MHz build and measures the clock
(within 0.1 %), reads the wiring (each top-row pin pulsed low and read back
at once: only a pulled-up pin is high again; that gives the column order and
whether the pull-up is on D- or D+), holds the pulled-up line low (the hub
sees a disconnect), loads `usb_enum`, CRCCTRL and the line unit over the
fabric SPI master, maps the two PMOD pins to uio0/uio1, which releases the
line (the hub sees a new device), starts core 0, and from then on only reads
the Kria's kernel log and sysfs until the device 1209:5634 is configured
(`bConfigurationValue` 1) or 20 s pass (`--timeout`). Then it prints the
verdict with the kernel's lines, `lsusb -d 1209:5634` and `lsusb -t`, and
only then reads the chip's state over the host port (address, Configured
flag). The device stays attached and running; `--stop` detaches it (core
stopped, pulled-up line held low until the app is reloaded). Exit status 0 =
PASS, 1 = FAIL, 2 = wiring refused (nothing enabled, core not started). Other
options: `--speed full`, `--order normal|reversed` (assert the column order),
`--dry-run` (identity, clock, load and start with every PMOD pin disabled),
`--model` (no hardware, below).

VID:PID 1209:5634 is pid.codes' VID with the repository's placeholder PID
(`pe.usbdev.DEVICE_DESC`, bcdDevice 0.01, no strings, one configuration with
one vendor-specific interface and no endpoints besides 0, 100 mA); a real
product needs its own PID. Linux binds no driver to it, which is expected.

Expected output (the kernel lines are the format Linux 6.8 prints; the port
`1-1.N` is the hub port of the USB-A socket used, the device number Linux's):

    [identity] RTL revision 60b06926cb1d, clk_div 25, built 2026-10-07 03:15:05 UTC
    [clock] protoemu clk measured 47.9993 MHz
    [wiring]
      pins pulsed low then read (of 5): pmod[0] 0, pmod[1] 5, pmod[2] 0, pmod[3] 0
      pull-up found on pmod[1] = silkscreen 3: columns normal, it is D- (low-speed wiring)
      holding D- (pmod[1]) low: detached
      loaded usb_enum (241 words, low speed) into core 0, CRCCTRL, line unit
      attached: pmod[0] (silkscreen 1) = uio0, pmod[1] (silkscreen 3) = uio1; core 0 started; host port idle from here on
    [kernel] waiting up to 20 s for 1209:5634 (no host-port access meanwhile)
      [  ...] usb 1-1.2: new low-speed USB device number 4 using xhci-hcd
      [  ...] usb 1-1.2: New USB device found, idVendor=1209, idProduct=5634, bcdDevice= 0.01
      [  ...] usb 1-1.2: New USB device strings: Mfr=0, Product=0, SerialNumber=0
    PASS: Linux enumerated 1209:5634 at low speed and configured it (1-1.2: devnum 4, speed 1.5 Mbit/s,
      bConfigurationValue 1, bMaxPacketSize0 8, bcdDevice 0001)
      $ lsusb -d 1209:5634
        Bus 001 Device 004: ID 1209:5634 Generic ...
    [chip] after the verdict, over the host port: address N, configured True, pending address False, stalled False
      left running and attached (--stop detaches)

With the columns the other way round the wiring lines say `pmod[2] 5` and
`columns reversed` and the pins are mapped to pmod[3]/pmod[2]. On failure the
verdict names the kernel's error and a likely cause (no "new low-speed USB
device" line: the hub never saw the attach, so check the pull-up; `device
descriptor read/64, error -71`: answers missing or garbled, so check D+/D-
and the clock; `device not accepting address`; `can't set config`).

**Full speed later**: move the 1.5 kohm from D- to D+ (the cable side of D+'s
27 ohm, to the same 3.3 V pin) and run `usb_demo.py --speed full`: the same
two wires, the chip's pins swapped by the PMOD map (D- = uio0, D+ = uio1),
4 clocks per bit. The 48 MHz clock is the PS's 100 MHz (99.999 MHz from its
IOPLL) x 12 / 25, about -10 ppm, inside full speed's 0.25 %. Not simulated
on this design (the chip RTL test `test_usb_autonomous_fs` covers the
firmware at full speed); an FPGA pin with 27 ohm is probably also
outside full speed's driver-impedance window (28-44 ohm); keep the wires short.

### Build and staging (2026-10-07, branch `kv260-usb`)

On `main` since 2026-10-13, with the USB bus-reset fix (usb_enum waits for
D- low as a level; 241 words, one more than the transcripts recorded on
2026-10-07). The bitstream is unchanged (no `src/` change); the Kria's
`~/protoemu48/{tools,firmware,fpga/kv260/host}` were refreshed from `main`
the same day (nothing loaded), and `make -C fpga/kv260/sim` passes 5/5 with it.

Branch `kv260-usb` = `v2` (this FPGA flow) + the `usb-enum` commit
(firmware/usb_enum.pasm, pe.usbenum, pe.usbhost, docs/usb-enumeration.md;
no `src/` change). `build.tcl` cuts the SPI device's crossings when its
clock is not 8x protoemu's (clk_div 25: 400 MHz against 48 MHz, edges as
close as 0.83 ns), so the SPI device demo is untimed in the 48 MHz build;
the 50 MHz build is unaffected.

`REMOTE_DIR=protoemu-flow/fpga-kusb fpga/kv260/build.sh 25`: RTL revision
`60b0692`, digest `036faa21`, Vivado 2026.1 BASIC on runner1, about 15 min.
Timing met, 0 critical warnings in the log (methodology: the existing
TIMING-4, and TIMING-47 for the deliberate clk_d/clk cut): design WNS
+0.479 ns (inside the SPI device's own 400 MHz domain), WHS +0.010 ns.

| Clock | WNS | WHS |
|---|---|---|
| protoemu clk (48 MHz) | +4.807 ns | +0.010 ns |
| mclk_pe / mclk_alt (fabric master) | +3.901 / +4.370 ns | +0.043 ns |
| SPI device clock (400 MHz, internal) | +0.479 ns | +0.057 ns |
| mclk_pe <-> protoemu clk | +6.179 / +11.663 ns | +0.349 / +0.389 ns |
| GPIO SCK / pl_clk0 | +7.671 / +4.656 ns | +0.101 / +0.017 ns |

Utilization: 10015 LUTs (protoemu 6536, pe_lab 1223, pe_spim 581), 6052 FFs
(protoemu 2545), 4 RAMB36 + 2 RAMB18, 9 IOBs, 0 DSP.

Staged on the Kria (no PMOD pin driven; the loopback jumpers may still be
in J2: remove them before wiring USB): `APP=protoemu48 LOAD=0
fpga/kv260/install.sh` (app in /lib/firmware/xilinx/protoemu48 with
`protoemu48.bit.bin`, host software in ~/protoemu48; ~/protoemu and the
`protoemu` app untouched), `xmutil loadapp protoemu48`, then:

    $ sudo python3 protoemu48/fpga/kv260/host/bringup_kria.py --mhz 48
    [build]   bitstream RTL revision 60b06926cb1df7c3c4a75ddcfe8f7329d4603dc0
              built 2026-10-07 03:15:05 UTC from HEAD 60b06926cb1d, RTL digest 036faa21, clk_div 25
              expected RTL revision 60b06926... (expected_build.json)  PASS
    [clock]   protoemu clk measured 48.0004 MHz
    [id] [regs] [pins] [loopback] [uart_tx_pin] [uart_rx_pin] [quad]: PASS
    ALL PASS
    clock over 2 / 5 / 10 s: 47.99929 / 47.99943 / 47.99953 MHz (pl0_ref 99999999 Hz; Linux's clock as reference)
    $ sudo python3 protoemu48/fpga/kv260/host/usb_demo.py --dry-run
    [identity] RTL revision 60b06926cb1d, clk_div 25, built 2026-10-07 03:15:05 UTC
    [clock] protoemu clk measured 47.9993 MHz
    [dry run] usb_enum loaded (240 words) and started with every PMOD pin disabled: CTRL 0x001, uio_oe 0x00
      (idle bus: the device never drives), state {'address': 0, 'configured': False, ...}; PMOD 00000000 00000000; core stopped
    PASS (dry run)

The clock is 48 MHz within 10 ppm against Linux's clock (the 0.5 s figure of
`bringup_kria.py` carries Python's timing noise). Then the 50 MHz app was
loaded again (`xmutil loadapp protoemu`; `bringup_kria.py --only build clock
id`: RTL revision d1aa46f, 49.9981 MHz, PASS) and left loaded, PMOD pins
disabled. The USB wiring itself and `usb_demo.py` without `--dry-run` have
not run on hardware.

### Validation (no hardware)

    python3 fpga/kv260/host/usb_demo.py --model
    make -C fpga/kv260/sim COCOTB_TEST_FILTER=test_usb_demo

`--model`: the cycle model loads the firmware exactly as the script does
(`pe.usbenum.load_writes`), then the independent host controller model
`pe.usbhost.ControlHost` (pins only; toggles, retries, STALL, keep-alive
EOPs) runs Linux's sequence (`usb_demo.linux_flow`: 64-byte device
descriptor read at address 0, 10 ms bus reset, SET_ADDRESS 7, device
descriptor, configuration header, whole configuration, SET_CONFIGURATION 1,
GET_CONFIGURATION) with every host-transport operation refused after the
start, at 0 and +-1.5 % clock offset:

    [model, clock offset +0.0 %] 26 transactions, 1070913 cycles (22.31 ms at 48 MHz), turnaround 2.16-2.16 bit times; chip state {'address': 7, 'configured': True, 'pending_address': False, 'stalled': False}: PASS
    [model, clock offset +1.5 %] 26 transactions, 1071965 cycles (22.33 ms at 48 MHz), turnaround 2.12-2.16 bit times; ... PASS
    [model, clock offset -1.5 %] 26 transactions, 1069862 cycles (22.29 ms at 48 MHz), turnaround 2.16-2.19 bit times; ... PASS

RTL (`fpga/kv260/sim`, the whole KV260 design: `kv260_top`, `pe_spim`,
`pe_lab` and the staged chip RTL, protoemu clock 20.834 ns = 48 MHz). The
testbench models what is wired to the PMOD pads: a strong driver per pin
(the USB host), pull-ups/pull-downs (the 1.5 kohm; the host's 15 kohm), and a
weak keeper per pin, so an open pin keeps the level it was last driven to.
`test_usb_demo` runs the script's own `prepare()` (wiring check, soft
detach, load over the fabric master, attach, start) unchanged through the
simulated AXI ports, then `ControlHost` runs `linux_flow` on the PMOD pads
(bus reset shortened to 100 us); CS_n/SCK/MOSI are watched from the start
to the end of enumeration:

    pins pulsed low then read (of 5): pmod[0] 0, pmod[1] 5, pmod[2] 0, pmod[3] 0
    pull-up found on pmod[1] = silkscreen 3: columns normal, it is D- (low-speed wiring)
    holding D- (pmod[1]) low: detached
    loaded usb_enum (241 words, low speed) into core 0, CRCCTRL, line unit
    attached: pmod[0] (silkscreen 1) = uio0, pmod[1] (silkscreen 3) = uio1; core 0 started; host port idle from here on
    usb_demo on the KV260 RTL at 48 MHz: device descriptor 120110010000000809123456010000000001; 27 transactions,
      168540 cycles (3.51 ms), turnaround 2.16-2.16 bit times, host port idle after the start;
      chip state {'address': 7, 'configured': True, 'pending_address': False, 'stalled': False}

It also checks that D- was low (detached) during the load and J (attached)
before the first host packet. `test_usb_demo_wiring`: (a) the columns the
other way round (D+ on pmod[3], D- on pmod[2]): detected (`pmod[2] 5`,
`columns reversed`), mapped to pmod[3] = uio0 / pmod[2] = uio1, and the
device answers the first GET_DESCRIPTOR; (b) the pull-up to GND (the power
columns swapped): `no pulled-up pin`, refused; (c) the pull-up on D+ for a
low-speed run: refused. In (b) and (c) both PMOD map registers end at 0, the
core is not started, and no PMOD pin is ever driven high. Both tests pass
(77 s of Icarus); the whole `make -C fpga/kv260/sim` is 5/5 (test_bringup_steps,
test_spim, test_lab unchanged by the new pad model).

What simulation can't show: the pads' analogue behaviour (edge rates,
ringing, the hub's receivers), the hub and the Linux host stack themselves.

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
* The demo fabric (pattern generator, SPI device, I2C bus wiring) replaces
  the outside world inside the FPGA: edges are clock-exact or come from a
  clock of the same VCO, and the SPI device's delays are fabric routing plus
  2.5 ns steps, not pad and trace delays.
* The host must keep CS_n high for 5 clk after reset release (docs/isa.md §6.1);
  `Board.reset()` does. In simulation, starting a read 2-3 clk after reset
  release returns a wrong ID, as specified.
