# Wirelingo (protoemu) on the Kria KV260

The chip RTL (tapeout configuration: 2 cores, 256-word bank each, 8-deep
FIFOs) running on a Kria KV260 FPGA, before silicon. This validates the RTL
in FPGA fabric (inferred RAM, FPGA timing and pads), not the IHP netlist,
its pads or the TT mux ([FPGA vs ASIC differences](#fpga-vs-asic-differences)). Since
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

## Post-freeze protocols on hardware (2026-10-13, `host/postfreeze_demo.py`)

The ten post-freeze firmwares (docs/post-freeze-protocols.md) on the FPGA
build of the chip RTL: their first run outside simulation. What ran is the
RTL on an FPGA (fabric timing, inferred RAM, no IHP pads), not silicon.
Nothing left the FPGA: every line is a virtual pin (P24+) or a uio pad driven
by pe_lab; no PMOD pin was enabled (the J2 wiring was not known, so no
protocol went over real pins).

    ssh ubuntu@kria.[tailnet domain redacted] sudo python3 -u protoemu/fpga/kv260/host/postfreeze_demo.py --save pf50.json
    # 48 MHz (DShot): sudo xmutil unloadapp; sudo xmutil loadapp protoemu48, then
    ssh ubuntu@kria.[tailnet domain redacted] sudo python3 -u protoemu48/fpga/kv260/host/postfreeze_demo.py dshot bidir --save pf48.json
    scp ubuntu@kria.[tailnet domain redacted]:pf50.json /tmp/ && python3 fpga/kv260/host/postfreeze_demo.py sigrok /tmp/pf50.json
    python3 fpga/kv260/host/postfreeze_demo.py --model          # the same runs on the cycle model (about 5 min)

**Board state and builds.** Run 2026-10-13 19:14-19:17 UTC. The Kria had
rebooted and had `k26-starter-kits` in slot 0; `xmutil loadapp protoemu`
loaded the 50 MHz app: `bringup_kria.py --only build clock id` PASS, RTL
revision `d1aa46f` (digest `3adc796c`, clk_div 24), clock 49.9966 MHz. The
script measured 49.9991 MHz over 2 s against Linux's clock and, within
200 ppm of the MMCM's 1200 MHz / 24, derived every CLKDIV and define for
50.000 MHz. DShot was repeated on `protoemu48` (RTL revision `60b0692`,
digest `036faa21`, clk_div 25; measured 47.9992 MHz, defines for 48.000 MHz),
then `protoemu` was loaded again (build/clock/id PASS, 49.9989 MHz) and left
loaded with PMOD0/PMOD1 = 0, pe_lab MODE 0 and the host port on the GPIO
path. The chip Verilog (`src/*.v`) is identical in d1aa46f, 60b0692 and
`main` (0ebe6ca); between them only harden configuration (config.json, SDC)
and build.tcl changed. Host software (`tools/`, `firmware/`,
`fpga/kv260/host/`) was refreshed in `~/protoemu` and `~/protoemu48` by rsync
as install.sh does, keeping each `expected_build.json`.

**Method.** Transmitters: core 0 runs the firmware with its pins on virtual
pins; core 1 runs `la_capture` on them (1 tick = 1 clock, unless stated);
the host drains the records with one 4096-word READ_FIXED burst over
pe_spim, with the SCK divider chosen so the burst outlasts the transmission
and drains faster than the edges arrive (quad at SCK = clk for I2S). The
transmitter waits behind a 2-word preamble (`wait 1 pin 7; jmp <firmware>` at
word 254) until pe_lab's pattern generator raises uio7, after the burst has
started. A capture with a gap record fails the run; none had one. The
pins-only decoders and models of `pe.postfreeze` judge the edges.
Receivers: core to core where both ends speak the same line (Wiegand on V0/V1;
LIN on pe_lab's I2C-bus mode, uio0 and uio2 wired-AND with a pull-up, both
nodes open drain; bidirectional DShot against `host/esc_emu.pasm`, a 16-word
ESC stand-in on the second core, on the same wired-AND line). Otherwise the
pattern generator plays a pins-only model's waveform at clock-exact times
(NEC envelopes, Wiegand reader timings, a LIN commander at +-10 %). `nec_rx`
cannot take `nec_tx`'s output (raw 38 kHz carrier vs a receiver module's
demodulated envelope; nec_tx has no envelope mode), so NEC TX and RX were
tested separately.

**Results at 50 MHz** (15 of 16 runs PASS; times from the captures, one
clock = 20 ns):

| Protocol | How | Result |
|---|---|---|
| DMX512 TX | V0 -> la_capture | PASS. 3 packets (9, 2, 1 slots, MTBS 0) and 2 (20, 1 slots, MTBS 8 us): every slot right; BREAK 176.0 us, MAB 12.0 us, every bit edge on the 4 us grid (0 ns off), mark between slots 0 / 8.0 us, BREAK to BREAK 1204.0 us (padded) and 1234.0 us (20 slots x 52 us + 194). sigrok `dmx512` on the MTBS-8 capture: the 20 slots equal, then it reads the next BREAK as slots (its known 512-slot limit) |
| I2S TX | V0-V2 -> la_capture, quad drain | PASS. 48 kHz: 14 samples exact, then 35 silent words with BCLK and WS running (underrun); BCLK period 32/33 clocks (CLKDIV 16+71/256), frame 1041/1042 clocks, fs 47996.2 Hz (-0.008 %) over 24 frames. sigrok `i2s`: same samples, no warnings |
| DShot600 | V0 -> la_capture | PASS. 20 frames 0x7d0a, 0x617, 0xffee then repeated; T1H 1240-1260, T0H 620-640, bit 1660-1680 ns (+-1 clock, CLKDIV 10.42), frame period 125.0 us. At 48 MHz: T1H 1250.0, T0H 625.0, bit 1666.7 ns exactly. sigrok `pwm` -> the same frames |
| NEC TX | V0 -> la_capture | PASS. Repeat code at clock resolution: bursts of 341 and 21 carrier periods, carrier 37999.86 Hz (1315/1316 clocks), on 8.76-8.78 us (33.3 %), leader to stop burst 1280 ticks. Standard (0x04/0x08) and extended (0xBEEF/0x11) frames captured at 5 us ticks (one record per carrier period: burst starts exact to 5 us, ends within ~35 us), decoded from the envelope; unit from burst starts 560.0-562.5 us (firmware 561.4 us, +-5 us tick); repeat to repeat 107.790 ms (firmware 107.7898). sigrok `ir_nec`: repeat on the raw carrier, the standard frame on the rebuilt envelope (the extended one is its known "Address# error") |
| NEC RX | generator -> uio0 | PASS. 525 entries, 1.2 s: senders at x1.0, x0.9 (+200 us bursts) and x1.1 (-200 us bursts), standard + repeat + extended each, 9 records right; a glitch, a 3 ms leader and 30 ms stuck low ignored; a truncated frame -> error after 13 bits; the next frame decoded. Waveforms from `pe.postfreeze.nec_envelope` (our model, not a real remote or receiver module) |
| Wiegand TX | V0/V1 -> la_capture | PASS. 26-bit H10301 (42/31337) and 34-bit frames bit-exact, pulses 50.0 us, intervals 2000.0 us. sigrok `wiegand`: same bits, both valid |
| Wiegand TX -> RX | core 0 -> V0/V1 -> core 1 | PASS. PW 50 / IV 200 us: 26-bit (valid), 34-bit (valid), raw 30-bit (not valid: not a validated format), 26-bit with a wrong parity bit (not valid), all bit for bit |
| Wiegand RX | generator -> uio0/uio1 | PASS. 26-bit at 20 us / 200 us and 34-bit at 100 us / 5 ms valid; 2 us glitches between bits ignored; wrong parity not valid; both lines low -> error |
| LIN commander | V0 -> la_capture | PASS. Bus bytes 55 50 01020304 a5, 55 3c 30..37 62 (classic), 55 61 (header, no responder: timeout record); break 12.9992-12.9996 bits, delimiter 0.9999, edges inside bytes -30..+13 ns from the 19200 bit/s grid. sigrok `lin`: IDs 0x10, 0x3C, 0x21, parity and checksums ok |
| LIN commander <-> responder | wired-AND bus (pe_lab I2C mode) | PASS. Responder clock nominal, x1.1, x0.9: sync-field T = 104 / 115 / 94 ticks (ideal 104.2 / 114.6 / 93.8); publish 0x10 heard (enhanced ok), subscribe 0x21 answered C0 FF EE and checked by the commander, publish 0x3C heard (classic ok); no dropped record (F2) |
| LIN responder rate | generator commander -> uio0, response on uio2 -> la_capture | PASS. Commander at 19200 bit/s, x1.1, x0.9: response C0 FF EE + checksum, response bit -0.16 / +0.32 / +0.22 % from the commander's (LIN: +-2 %), spaces between bytes 0.07-0.16 bit |
| Bidir DShot frames | uio0 -> la_capture (no ESC) | **FAIL at 50 MHz**, PASS at 48 MHz. Frames right (0x640d, 0x197, 0xf9e7, inverted checksums), but low 1220 / 600 ns and bit 1640 ns: one clock short of the defines (T1 62, T0 31, BIT 83). Cause, in the firmware, not the FPGA: with T0 odd, `dshot_bidir.pasm`'s delays `[(T0 - 2) / 2]` and `[T0 - 2 - (T0 - 2) / 2]` each lose half a clock (the assembler divides exactly, then truncates). The cycle model shows the same. At 48 MHz (T0 = 30): low 1250.0 / 625.0 ns, bit 1666.7 ns exactly |
| Bidir DShot frames, after the fix (2026-10-13, same bitstream) | uio0 -> la_capture (no ESC) | PASS at 50 MHz: the assembler's `/` is now integer division, so the two halves add up to T0 - 2: low 1240.0 / 620.0 ns, bit 1660.0 ns, exactly 62 / 31 / 83 clocks; the ESC exchange below passes again |
| Bidir DShot telemetry | dshot_bidir <-> esc_emu, wired-AND line | PASS at 50 and 48 MHz. The ESC side sampled the 8 frames right; 8 replies (21 levels, 750 kbit/s, 30 us turnaround) decoded by the firmware + host to eRPM periods 333, 4096, 64896, 100, 1000, 2000, 29952, 512 us (what the encoding keeps of 65000 and 30000), GCR and checksums ok. The ESC is our fixture (`esc_emu.pasm`), not a real ESC |

**Not run / limits.** The 513-slot DMX universe (the host cannot feed the TX
FIFO and drain the capture at once with one SPI port; packets here are at
most 20 slots, each pre-loaded in the 8-word FIFO). Full NEC frames at carrier
resolution (a 67 ms frame produces more records than one 4096-word burst can
drain fast enough), hence the 5 us capture for frames. No protocol went over
FPGA package pins. Every "other end" is either this chip's other core or one
of our pins-only models played by the generator; sigrok's third-party
decoders checked the captured transmitter waveforms off the board.

## SPI and UART firmware on hardware (2026-10-14, `host/spi_uart_demo.py`)

The SPI masters (`spi_master_modes`, `spi_master_packed`,
`spi_master_preloaded_once`) and the UART receivers (`uart_rx_capture`,
`uart_rx_packed`, `uart_rx_capture_peer`, `uart_rx_mailbox`), which had only
run in simulation, on the same FPGA build of the chip RTL. What ran is the RTL
on an FPGA (fabric timing, inferred RAM, no IHP pads), not silicon. Every line
is a uio pad driven by a core or by pe_lab; no PMOD pin was enabled.

    ssh ubuntu@kria.[tailnet domain redacted] sudo python3 -u protoemu/fpga/kv260/host/spi_uart_demo.py --save su50.json
    python3 fpga/kv260/host/spi_uart_demo.py --model --save m.json      # the same runs on the cycle model (about 4 min)
    scp ubuntu@kria.[tailnet domain redacted]:su50.json /tmp/ && python3 fpga/kv260/host/spi_uart_demo.py compare /tmp/su50.json m.json

**Board state and build.** Run 2026-10-14 03:14-03:15 UTC (the three groups
had also run separately at 03:10-03:12 with the same results). `protoemu`
(50 MHz) was already loaded: `bringup_kria.py --only build clock id` PASS,
RTL revision `d1aa46f` (digest `3adc796c`, clk_div 24), clock 49.9988 MHz.
The script measured 49.9979 MHz over 2 s and, within 200 ppm of
1200 MHz / 24, derived every CLKDIV for 50.000 MHz. `src/*.v` is identical in
d1aa46f and this branch's base `main` (2d08f14). Host software (`tools/`,
`firmware/`, `fpga/kv260/host/`) was refreshed in `~/protoemu` by rsync as
`install.sh` does, keeping `expected_build.json`.

**Method.** Pins (uio): SCK 2, MOSI 3, MISO 4, CS 5, trigger 7; UART RX
uio0 (core 0) and uio1 (core 1).
* *SPI against pe_lab's SPI device* (MODE 2): a reactive peer in the fabric.
  It samples MOSI on each SCK rise and drives the next MISO bit
  (DELAY + 2) x 2.5 ns after the clock edge that dropped SCK, and it counts
  SCK rises and logs the last 16 MOSI bytes. That is a mode-0 peer and, with
  its 128-bit response register rotated by one bit, a mode-3 one (mode 3 also
  samples on the rise and launches on the fall; its first fall must present
  bit 7). Modes 1 and 2 have no peer in the fabric.
* *SPI with la_capture* (all four modes): core 0 runs the master at CLKDIV 8
  behind the trigger preamble (with the program's side-set holding SCK at
  CPOL), core 1 runs `la_capture` on SCK, MOSI, CS and the trigger (1 tick =
  1 clock; every edge is >= 8 clocks from the previous one, so every edge is
  timestamped exactly), and the pattern generator plays MISO: each bit 4
  clocks after the peer's launch edge (CPHA0: CS fall, then each trailing
  edge; CPHA1: each leading edge) as the cycle model predicts for the same
  set-up. MISO is therefore pre-timed, not a reactive peer: the master's tick
  phase against the trigger differs from the model's by a start offset
  (measured from the capture: -1..+6 clocks), so the bits landed 2 clocks
  before to 5 clocks after the launch edge; the master reads MISO 14 or more
  clocks after that edge. The captured wire is checked with
  `test_spi_packed.FramedPeer`'s rules (CS changes only with SCK idle, no SCK
  edge outside CS, MOSI never changes at a sample edge, exact sample count)
  and compared edge for edge with the model's capture.
* *UART*: the generator plays precomputed 8N1 lines at exact clocks, built
  like `test_uart._PinTimeline` (true baud offset, start phase, alternating
  jitter, low STOP bits, breaks); arming, completion checks and decoding use
  `pe.uartcapture` / `pe.uartcapture_peer` as docs/uart-capture-host.md
  describes (STATUS/CTRL preconditions, the 5-write plan, HALT/no fault/exact
  count/flag, then exactly ceil(N/4) words).

**Results at 50 MHz** (11 of 11 runs PASS; one clock = 20 ns; `compare`
against the `--model` run: every run's DELAY tables, waveforms in clocks
after the start offset, flags and data are equal):

| Firmware | How | Result |
|---|---|---|
| spi_master_modes | SPI device, modes 0 and 3, compact and unrolled, CLKDIV 3 and 4, DELAY 0..31, 2 x 8 random bytes per DELAY | PASS. Every round: 64 SCK rises, MOSI right (device log), SCK idle at CPOL after. Mode 0: right at every DELAY (MISO up to 82.5 ns after the edge, the device's maximum). Mode 3 at CLKDIV 3: right up to DELAY 29 (77.5 ns), 1 bit late at 30-31: the master reads MISO 2 ticks after the launching fall, from a pad 2 clocks old, so 4 clocks = 80 ns; at CLKDIV 4 right throughout. SCK rise to rise 12 clocks (CLKDIV 3) / 16 (CLKDIV 4) within a byte; compact 18 / 24 across bytes, unrolled 12 / 16 (no byte gap) |
| spi_master_modes | la_capture, all four modes, compact and unrolled, CLKDIV 8, 8 bytes | PASS. MOSI at the sample edges = TX, master RX = the generator's MISO, 128 SCK edges alternating leading/trailing, idle at CPOL. CPHA0: SCK active 2, idle 2 ticks (4 across bytes); CPHA1: active 1, idle 3 (5); bytes every 34 ticks compact, 32 unrolled, as the firmware header and test_spi_modes state. MOSI changes 8 clocks (1 tick) before the sample edge and 24 after it |
| spi_master_packed | SPI device, modes 0 and 3, CLKDIV 3 | PASS. 10 queued messages of 1, 2, 3, 4, 5, 7, 8, 15, 16, 28 bytes (header + payload preloaded, core started once): exactly 8 N SCK rises, MOSI and RX right (`spipacked.decode`), CS high and SCK idle after each. 8-byte message right up to DELAY 29 (77.5 ns), 1 bit late at 30-31, both modes. A 200-byte message fed and drained by the host while it ran (the master paused with TX empty / RX full): 1600 rises, all 200 RX bytes right |
| spi_master_packed | la_capture, all four modes, CLKDIV 8, a 5- and a 3-byte message queued | PASS. Two CS frames with exactly 40 and 24 sample edges, wire = TX, RX right. CPHA0: CS low to first edge 3 ticks, last edge to CS high 1 tick, 4 ticks per bit (32 per byte); CPHA1: 2 and 5 ticks, 5 ticks per bit (40 per byte); CS high 8 ticks between the messages |
| spi_master_preloaded_once | SPI device, mode 0, CLKDIV 3 | PASS. 1, 3, 4, 5, 31, 32 and 32 bytes, each armed by `spipacked.preload_plan` (disable, R0/R1, FIFO burst, start) without reset or reload, completion polled: HALT, no fault, exact RX count, decode right, STATUS empty after the drain, CS high and SCK low; exactly 8 N rises and MOSI right |
| spi_master_preloaded_once | la_capture, CLKDIV 8, 32 and 5 bytes | PASS. One CS frame each, 256 / 40 sample edges, wire = TX, RX right, HALT; CS low to first edge 3 ticks, final CS hold 1 tick (the header's "1 tick, not 4 clocks") |
| uart_rx_capture | generator -> uio0, core 0, 115200 | PASS. The native test's pair on one load: 32 bytes with a low final STOP -> HALT, all 32 bytes kept, F0 = 1, the extra frame not taken; FCLR/rearm, `39 d3 81` -> `0x81d33900`, F0 = 0. Lengths 1, 4, 5, 31 right; 32 bytes at the model test's corners (-3 % / +3 % baud, 51-clock phase, 2-clock jitter) right; 3 and 4 bytes with a low final STOP and a break -> HALT, F0 = 1; under-supply (5 declared, 4 sent): no HALT, 1 word, R1 = 1. Outside the qualified +-3 %: -4 % and -5 % right; -6 %, -7 % HALT with wrong data and F0; +4 % to +7 % no HALT (6-7 words) and F0, identical to the model |
| uart_rx_packed | generator -> uio0, 115200 | PASS. 32 bytes unattended: 8 words = the bytes; 30 bytes: 7 words (the last 2 stay in ISR, no flush, as documented); a low STOP on byte 5 of 16: F0 = 1, all 4 words kept; 160 random bytes back to back (14.9 ms) drained by the host while arriving: all right, F0 = 0 |
| uart_rx_capture_peer | generator -> uio1, core 1 (F6), alongside uart_rx_capture on core 0 | PASS. Overlapping: core 0 32 bytes at 115200 (F0 = 0) and core 1 7 bytes at 57600 with a low final STOP (F6 = 1, data kept). Then core 0 capturing 32 bytes at 19200 (17.7 ms) while core 1 took a 4-byte message at 115200, was drained and rearmed by `uartcapture_peer.capture_plan` (core 0's enable kept, CTRL read 0x3) and took a second: all right, F0 = F6 = 0. Live two-core operation, which docs/uart-capture-host.md lists as not qualified by its native test |
| uart_rx_mailbox | generator -> uio1, core 1, READY uo0, byte on V24..V31; the host clears F7 as usb_uart_byte's core 0 does after a valid DATA ACK | PASS. 6 frames 4 ms apart, frame 3 with a low STOP: the 5 good bytes published with F7, READY low whenever F7 was seen and high again after FCLR F7; the bad frame discarded with F6 = 1 |

**Not run.** `usb_uart_byte`, `usb_uart_batch4`, `usb_uart_overlap4` and
the USB side of `uart_rx_capture_peer` / `uart_rx_mailbox` /
`uart_rx_batch4` / `uart_rx_overlap4`: they need a USB host on wired PMOD
pins. `uart_rx_batch4` and `uart_rx_overlap4` were not run standalone either
(their consumer is the USB core). SPI modes 1 and 2 against a reactive peer
(only the pre-timed generator MISO). Nothing went over package pins, so the
SPI DELAY windows are fabric numbers: on silicon the same clock budget must
also cover the output pad, board flight both ways and the input pad. The
UART sources are our models played by the generator, not an external UART.

**Bring-up at the end, and a logging artefact.** Nine runs of the full
`bringup_kria.py` (03:16-03:18 UTC) through `~/wlrun.sh`, which syncs the disk
after every output line while the script runs, gave five ALL PASS and four
with a failure in the Python-timed 9600-baud steps (`uart_rx_pin`: a byte
with a flipped bit or a truncated message, e.g. `KV260 \xefk`, `KV\xff`;
`uart_tx_pin`: `prot\x7femu`); one of the four was at real-time priority
(`chrt -f 50`). Six further runs (03:18-03:20) with the output written to a
file and synced after the run all passed: clock 49.9951-49.9982 MHz (0.5 s, Python-timed), worst
`uart_tx_pin` edge 3-32 % of a bit off the grid. Those two steps bit-bang and
poll the GPIO from Python; every other step, including the core-to-core
`loopback`, passed in all fifteen runs. Left loaded: `protoemu`, PMOD0/PMOD1
= 0, pe_lab MODE 0, host port on the GPIO path.

## Post-freeze protocols, second round, on hardware (2026-10-14, `host/postfreeze2_demo.py`)

The five second-round firmwares (docs/post-freeze-protocols.md: `dali_ctrl`,
`modbus_rtu`, `midi_rx`, `midi_tx`, `quad_enc`), which had only run in
simulation, on the same FPGA build of the chip RTL. What ran is the RTL on an
FPGA (fabric timing, inferred RAM, no IHP pads), not silicon. Every line is a
virtual pin or a uio pad driven by a core or by pe_lab; no PMOD pin was
enabled; no wiring was changed.

    python3 fpga/kv260/host/postfreeze2_demo.py --model --save m2.json --plans plans.json   # cycle model first (about 6 min)
    scp plans.json ubuntu@kria.[tailnet domain redacted]:pf2_logs/
    ssh ubuntu@kria.[tailnet domain redacted] sudo python3 -u protoemu/fpga/kv260/host/postfreeze2_demo.py --save pf2.json --plans pf2_logs/plans.json
    python3 fpga/kv260/host/postfreeze2_demo.py compare pf2.json m2.json
    python3 fpga/kv260/host/postfreeze2_demo.py sigrok pf2.json

**Board state and build.** Run 2026-10-14 04:33:04-04:33:15 UTC (the four
groups had also run separately at 04:21-04:32, all passing apart from the
first DALI attempt, below). `protoemu` (50 MHz) was loaded: `bringup_kria.py
--only build clock id` PASS, RTL revision `d1aa46f` (digest `3adc796c`,
clk_div 24), clock 49.9980 MHz. The script measured 49.99920 MHz over 2 s
and, within 200 ppm of 1200 MHz / 24, derived every CLKDIV and define for
50.000 MHz. `src/*.v` is identical in d1aa46f and this branch's base `main`
(35da4ed). Host software (`tools/`, `firmware/`, `fpga/kv260/host/`) was
refreshed in `~/protoemu` by rsync as `install.sh` does, keeping
`expected_build.json`. Each run's output went to a file, synced after the run
(`~/pf2_logs/`).

**Method.**
* *Wired-AND lines in the generator mode.* kv260_top gives a uio pad
  `uio_oe ? uio_out : generator`, so a firmware pin set open drain and
  released reads what the pattern generator drives: the pad is the
  wired-AND of the core and the generator. DALI's bus is uio0 (`dali_ctrl`
  TX = RX = uio0, open drain); the generator is the rest of the bus (idle
  high, the gear's backward frames, another controller's frame). Modbus's
  RS-485 line is uio0 (TX open drain, RX), DE uio2; the server answers on the
  same line, so the client also hears its own request, as on a half-duplex
  bus.
* *Pre-timed far ends.* The generator cannot react, so the DALI gear and
  the Modbus server are pre-timed: each scenario first runs on the cycle
  model with the test's pins-only model in the loop on the model's pad
  (`pe.postfreeze2.DaliGear` at 307.2 kHz, `ModbusServer` at 104 x baud,
  CLKDIV 1, tick for tick the 50 MHz firmware), and the far end's own
  waveform from that run (its exact edges, relative to the trigger) is
  what the generator plays on the board, after the 2-word trigger preamble
  of `postfreeze_demo` releases the firmware. The firmware's start differs
  from the model's by under a tick, so the answers land within about a
  tick of the model's times; the captures and the firmware's records say
  where they landed. `--plans` keeps these model runs in a file (33 runs,
  keyed by everything they depend on), so the Kria reuses them.
* *Transmitters* are recorded by `la_capture` on core 1 (1 tick = 1 clock;
  uio0-7 with the trigger, or V0) and drained by one READ_FIXED burst, as in
  postfreeze_demo; a gap record fails the run. *Receivers'* records are
  polled. DALI and Modbus captures are also compared edge by edge with the
  model's pads (the "board - model" spans below: one constant start offset
  for edges the core times from its start, up to about 2 ticks after the
  firmware re-synchronises on a generator edge).
* *Judges*: the decoders and models of test/model/test_{dali,modbus,midi,quad_enc}.py
  (`dali_decode` with IEC 62386-101's receiver ranges, `dali_records`,
  `uart_decode`, `modbus_crc` (the specification's algorithm),
  `ModbusServer`, `modbus_response`, `midi_parse`, `midi_encode`,
  `midi_rx_events`, `quad_count`), with the tests' scenarios and seeds
  where they fit; `compare` against the `--model` run; sigrok off the board.

**Results at 50 MHz** (25 of 25 runs PASS; `compare` with the `--model`
run: every run's deterministic results (values, records, flags, the MIDI
waveform in us, the quadrature tables) equal; one clock = 20 ns):

| Item | How | Result |
|---|---|---|
| DALI forward frames | bus uio0 -> la_capture | PASS. 0xFE80, 0x0105, 0xA300, 0xFFFF, 0x0000 (16 bits), 0xC10203 (24), 0x5A (8): all legal for the pins-only decoder. Every run between edges 20833/20834 clocks (416.66/416.68 us) or 41666/41667 (833.32/833.34 us), CLKDIV 651.043; idle from a frame's last edge to the next 14.154..14.570 ms (>= 14.0). Board - model: -7.7..-7.3 us over 214 edges (one start offset, under a tick of 13.02 us) |
| DALI queries | generator gear, captured | PASS. 0x0190, 0xFF91, 0x0399 answered 0xAB, 0x00, 0xFF (9 bits, decoded legal on the bus), 0x0590 'no answer'. Settling time on the bus 8007.2..8020.2 us (the gear's 8000 us, pre-timed from the model's frames; the board's frames came 7..20 us earlier); the firmware reported 8007.8 / 8020.8 / 8020.8 us, 0.6..0.8 us after the bus (its stated detection: 0..2 ticks) |
| DALI answer tolerance | generator gear, polled | PASS. Half bits 336, 366.7, 416.67, 466.7, 498 us, low phases +-75 us (416.67) and +40 / -40 us (380 / 450), each at 5.5 ms and 10.4 ms settling time: 72 answers right (0x00, 0xFF, 0x55, 0xAA, 0x81, 0x7E); every gear waveform legal for the reference decoder; reported settling times = the model's +-1 tick |
| DALI negative controls | generator gear, captured | PASS. 250 us half bits: violation (n 0); a 1300 us double half bit: violation; no mid-bit edge: violation; a 5-bit frame: n = 6, no violation; an 11.2 ms reply: 'no answer', and the next forward frame started 14.11 ms after the late frame's last edge; bus low 3 ms: violation; then 0x3C answered |
| DALI collisions | two generator gear, wired-AND, captured | PASS. 0x0F&0xF0, 0x80&0x01, 0xFF&0x00, 0x01&0x03: the pins-only decoder finds an illegal run on the bus (6666.7 / 1250 / 6666.7 / 1666.7 us) and the firmware reports a violation; 0x12&0x12: both 0x12 |
| DALI bus busy | generator plays another controller | PASS. Another controller's 0xFE05 3 ms after our 0xFE80 was queued: ours started 14.114 ms after its last edge (>= 14.0) |
| Modbus published CRCs | line + DE -> la_capture | PASS. Broadcasts `01 03 00 00 00 0A` -> `C5 CD`, `11 03 00 6B 00 03` -> `76 87` on the bus, even parity right; the server model heard both, CRC ok; no record (broadcast). Edges -33..+17 ns from each character's 19200 baud grid; characters 0.46..3.48 us apart (t1.5 = 859 us); DE high 1.50 us before the first start bit, low 20-40 ns before the end of the last stop bit (by the nominal bit time) |
| Modbus request/response | generator server, captured | PASS. Requests `1103006b00037687`, `11060001beefeab6`; responses `1103067d9f7ea07fa18b06` and the echo received with CRC ok, no flag; response start 3001.5..3002.3 us after the request's last stop bit (the model's 3000 us); request edges -33..+13 ns off the grid. Board - model: -2.3..-0.8 us (1 tick = 0.5 us) |
| Modbus lengths | generator server, polled | PASS. Functions 3 (1, 2, 3, 4, 10 registers), 6, 8, an exception: responses of 7, 9, 11, 13, 25, 8, 8, 5 bytes (every length mod 3) right, CRC ok, no flag |
| Modbus 255-byte response | generator server, polled | PASS. 125 registers (byte values with few edges, so the waveform fits the generator: 879 entries), 85 records over about 146 ms drained while arriving: right, CRC ok, no overflow |
| Modbus tolerance | generator server, polled | PASS. Server x0.98 and x1.02, 0 and 1.3 characters between its characters: 7- and 23-byte responses right, CRC ok, no flag |
| Modbus negative controls | generator server, polled | PASS. Wrong CRC: not ok; parity error: flagged, CRC ok; low stop bit: framing (and parity) flagged; 2.5-character gap: gap flagged, CRC ok; 4-character gap: the frame ends after 3 bytes, CRC not ok; no response: an end word with no bytes after the 100 ms timeout; then a good one. The same flags as the model |
| Modbus odd parity, 38400 | generator server, polled | PASS. 3 responses right; a 1.0 ms gap flagged (t1.5 = 750 us), none and a 0.6 ms gap not |
| MIDI TX | midi_tx -> V0 -> la_capture | PASS. Running status on: 8 events -> 17 bytes (20 without); off: 6 events with a SysEx -> 18 bytes; the reference encoder's bytes; every run inside a byte a multiple of 32.000 us (CLKDIV 50, exact); stop bit + space 40..61 us |
| MIDI RX | generator -> uio0 -> midi_rx (core 1), polled | PASS. Two random streams of every kind (121 and 108 bytes, 20 real-time bytes inserted anywhere, back to back) and two at x0.98 / x1.02 (88, 79 bytes): 60, 60, 41, 41 events equal to the reference parser's, FOV 0. Negative controls: stray data bytes dropped as the reference drops them; a low stop bit -> a framing-error record, the stream resumes. Host not reading: the first 8 messages kept, FOV set; the next 53 bytes right |
| MIDI loopback | midi_tx (core 0) -> V0 -> midi_rx (core 1) | PASS. 40 events of every kind (92 bytes with running status), host feeding and draining: the same 40 events out |
| MIDI full port | generator -> midi_rx (core 1) while midi_tx (core 0) sends on uio1 | PASS. RX: 18 bytes -> the 8 events sent. TX at the same time: 19 bytes right, read by the host polling the GPIO (uio_out[1]; host-timed, runs 29.9..1011.2 us: bytes only, the bit timing is the MIDI TX row) |
| Quadrature, random walk | generator -> uio0/uio1, also la_capture | PASS. 1001 steps 15..60 clocks apart: R3 -41 = the encoder's position = the pins-only X4 decoder on the capture; every step captured at the generator's time + one constant; FERR 0. 5 x 1000 steps (5 plays): R3 after each = the encoder's |
| Quadrature, rate | generator, 400 steps per cell | PASS. A step every n clocks: exact for n >= 14 forward and n >= 15 backward (3.57 / 3.33 M steps/s); at 10..13 (14 backward) steps missed and FERR set every time |
| Quadrature, glitches, wrap | generator | PASS. 1- and 3-clock pulses move nothing; both lines at once: FERR, not counted; 7 steps back counted. 40000 steps at 14 clocks: R3 = -25536 = 40000 - 65536 |
| Quadrature, asynchronous | generator on the 45.93 MHz alternate clock | PASS. A step every n alternate cycles (n x 1.0885 clocks, phase walking): exact forward from n = 13 (14.2 clocks), backward from 14 (15.2); at 11-12 (and 13 backward) missed and flagged: exact while the mean step is at least the loop. A 1000-step walk 15..60 alternate cycles apart: R3 316 = the encoder's |

sigrok (`postfreeze2_demo.py sigrok pf2.json`, the board's captures):
`dali` decodes every 16-bit forward frame and the backward frames 0xAB,
0x00, 0xFF, 0x99, 0x3C (it lists the 8-bit forward frame 0x5A as backward
and skips the 24-bit one: its known 16/8-bit limit); on the collision
captures it reads bytes (0x00, 0x01) where the pins-only decoder and the
firmware find illegal runs (it does not check run lengths); `uart` (even
parity) + `modbus`: the client's bytes, "Function 3 / Function 6" and "CRC
correct" in both directions; `midi`: the same messages as sent, both
captures; `graycode`: last count -42, the firmware's R3 before the final
step.

**One failure on the way, a script bug.** The first DALI run (04:21) failed
`dali_query`: the capture burst ended 4 ms after the last bus edge, before
the firmware's 'no answer' record for the last query (it comes after the
10.5 ms window), so 3 of 4 records were read. The capture now runs 14 ms
past the last edge and checks the record count; on the model the burst now
lasts as long as on the board (before, the model's minimum burst ran 0.4 s
and hid it). No firmware or tool bug was found.

**Not run / limits.** No protocol went over package pins (no PMOD pin
enabled), so no real DALI gear or bus power supply, RS-485 transceiver,
Modbus server, MIDI instrument or encoder: every far end is our pins-only
model, played by the generator at clock-exact times (or the chip's other
core). The DALI gear and Modbus server are pre-timed from a model run, not
reactive. midi_tx's bit timing was measured alone (la_capture); in the
full-port run both cores were busy, so its output was read by the host from
the GPIO (bytes, not timing). Only up to 8 TX FIFO words are queued before a
captured run (the host can't feed the FIFO during the burst), so captured
runs are short (at most 4 DALI queries, 2 Modbus requests, 8 MIDI words).
The asynchronous quadrature clock comes from the same VCO as protoemu's (its
phase walks; not an independent oscillator). The `--model` run models the
alternate clock as rounded-up protoemu clocks.

**At the end** (04:34 UTC): `bringup_kria.py` (all steps) ALL PASS, clock
49.9991 MHz; left loaded: `protoemu`, PMOD0/PMOD1 = 0, pe_lab MODE 0, host
port on the GPIO path (pe_spim SEL 0).

## Post-freeze protocols, third round, on hardware (2026-10-14, `host/postfreeze3_demo.py`)

The third-round firmwares (docs/post-freeze-protocols.md: `cec`, `iso7816` +
`iso7816_clk`, `smbus_host`, `rc5_tx`, `rc5_rx`) had only run in
simulation. Here they run on the same FPGA build of the chip RTL. What ran
is the RTL on an FPGA (fabric timing, inferred RAM, no IHP pads), not
silicon. Every line is a virtual pin or a uio pad driven by a core or by
pe_lab. No PMOD pin was enabled and no wiring was changed.

    python3 fpga/kv260/host/postfreeze3_demo.py --model --save m3.json --plans plans3.json   # cycle model first (about 9 min)
    scp plans3.json ubuntu@kria.[tailnet domain redacted]:pf3_logs/
    ssh ubuntu@kria.[tailnet domain redacted] sudo python3 -u protoemu/fpga/kv260/host/postfreeze3_demo.py --save pf3_logs/pf3.json --plans pf3_logs/plans3.json
    python3 fpga/kv260/host/postfreeze3_demo.py compare pf3.json m3.json
    python3 fpga/kv260/host/postfreeze3_demo.py sigrok pf3.json

**Board state and build.** The final run was 2026-10-14 07:15:34-07:15:49
UTC. The four groups had also run separately at 07:10-07:13, and once
before that at 07:13:32, all passing apart from one host-timing failure
described below. `protoemu` (50 MHz) was loaded: `bringup_kria.py --only
build clock id` PASS, RTL revision `d1aa46f` (digest `3adc796c`, clk_div
24), clock 49.9981 MHz. The script measured 49.99908 MHz over 2 s and,
within 200 ppm of 1200 MHz / 24, derived every CLKDIV and define for
50.000 MHz. `src/*.v` is identical in d1aa46f and this branch's base `main`
(51662db). Host software (`tools/`, `firmware/`, `fpga/kv260/host/`) was
refreshed in `~/protoemu` by rsync as `install.sh` does, keeping
`expected_build.json`. Each run's output went to a file in `~/pf3_logs/`,
synced after the run. All 49 pre-timing runs came from the plans file made
on the workstation, so none was recomputed on the Kria.

**Method.** This follows the second round; differences are noted.
* *Wired-AND lines.* The CEC bus is uio0 and SMBus is SDA uio0 / SCL uio1.
  The ISO 7816 I/O line is uio0, with RST on uio1. Each is driven open drain
  by the firmware and played by pe_lab's pattern generator for the far end,
  so each pad is the wired-AND of both (`uio_in = uio_oe ? uio_out :
  generator`).
* *Pre-timed far ends.* Each scenario first runs on the cycle model with the
  model tests' pins-only far end in the loop on the model's pads:
  `pe.postfreeze3.CecNode`, `SmbusDevice` (and test_smbus's second master),
  and `IsoCard`, which counts the CLK the clock core gives it. The models run
  tick for tick at the 50 MHz configuration: CEC at 200 kHz CLKDIV 2, SMBus
  at 3.571 MHz CLKDIV 4, ISO 7816 at 8.333 MHz with clock core CLKDIV 1 and
  reader CLKDIV 6. The generator then plays the far end's own edges,
  relative to the trigger, on the board. The far-end models act a little
  after what they see, so that a pre-timed answer never precedes the board's
  edge it answers when the firmware's start differs from the model's by up
  to a tick:
  * a CEC device after 30 us;
  * an SMBus device's SDA and SCL-stretch outputs after 2 us (SMBus allows
    data valid up to 3.45 us).
* *Judges.* On the board the firmware's records must equal the model run's
  word for word. The CEC, SMBus and ISO 7816 captures are compared edge by
  edge with the model's pads. Each run is also judged by:
  * pins-only decoders (`cec_decode`, an I2C reader with the PEC
    recomputed, `t0_parse`, `rc5_decode` behind the 36 kHz receiver-module
    model);
  * the model tests' assertions and scenarios;
  * `compare` with the `--model` run;
  * sigrok, off the board.
* *Captures.* Transmitters are recorded by la_capture on core 1 (1 tick = 1
  clock, uio0-7 with the trigger) and drained in one READ_FIXED burst. The
  host can't drain core 0 during the burst. When a scenario makes more than
  the 8 records its RX FIFO holds, the capture is pre-timed from a model run
  that leaves the FIFO full too. The firmwares' "FIFO full" path in `put`
  takes one more tick in `smbus_host` and `iso7816`, so the two timings
  differ. All of the records then come from a polled run of the same
  scenario. Records are polled while the TX FIFO is fed in whole commands.
* *ISO 7816 takes both cores*, so la_capture can't run beside it. In the
  two-core runs the reader's records are polled and its I/O drive and RST
  are read by the host polling the GPIO (`uio_oe`/`uio_out`, host-timed).
  For exact timing, separate runs put la_capture on core 1 in place of the
  clock core. The reader never reads CLK, and the card's answers were
  pre-timed in a model run with the clock core running. CLK is measured on
  its own: the clock core drives uio2 into pe_lab's SPI device, which
  times SCK rise to rise in 2.5 ns steps.

**Results at 50 MHz** (31 of 31 runs PASS; `compare` with the `--model` run:
the deterministic results of all 36 entries (records, decoded frames, ACK
bits, results) equal; one clock = 20 ns):

| Item | How | Result |
|---|---|---|
| CEC, our frames | TV follower pre-timed, captured | PASS. A poll, a directed command and a broadcast: results ok, ok, ok. The bus (pins-only decoder) carries 40, 4036 and 4f821000 with ACK bits [0], [0, 0] and [1, 1, 1, 1]. Our drive is exact to the clock: start bit low 185000 and next fall 225000 clocks, data lows 30000 / 75000, bits 120000 apart (3.7 / 4.5 / 0.6 / 1.5 / 2.4 ms). The follower's ACK slots are low 1.544 ms. Frame to frame is 12.100 ms after the previous end (5 bit periods: 12.0). |
| CEC, 16 blocks | captured | PASS. 4047 + "Wirelingo dev." (388.5 ms): every block acknowledged, EOM on the last only, the same exact intervals. |
| CEC, follower | TV initiator pre-timed, captured | PASS. Records 048f [0, 0] to us; 05 [1] (nobody acknowledged, the TV stopped after the header); 0f36 [1, 1]; 044441 [0, 0, 0] to us. The same frames and ACK bits on the bus. Our ACK holds the line 1504.7..1509.7 us from the TV's fall (1.5 ms + detection, 0..1 tick). |
| CEC, receiver ranges | polled | PASS. Initiators at start 3.5/4.3 and 3.9/4.7 ms, lows 0.4/1.3 and 0.8/1.7 ms, bits 2.05 and 2.75 ms, and both corners: 8 x 2 frames received, the directed ones acknowledged. |
| CEC, negative controls | generator, polled | PASS. A 3.0 ms start bit and 5 ms low ignored. Errors short (bits left 3), partial (4) and low (6), then frames 04, 04, 048f, the last acknowledged. |
| CEC, arbitration lost | TV starts 10 us after our fall, captured | PASS. Ours (0x40, 10 blocks) lost at the header. The TV's 048f was received and acknowledged by us, and our 2 unsent data words dropped. The retry (3 bit periods) went through 7.549 ms after the TV's frame, every block acknowledged. |
| CEC, arbitration won | device 8 starts with us, captured | PASS. Ours 4036 ok and acknowledged. Bus start bit low 3.751 ms (the two overlapped). |
| CEC, signal free time | TV broadcast 2 ms after ours was queued, captured | PASS. Ours went 12.249 ms after the TV's frame (5 bit periods), the next 16.900 ms after ours (7). CEC board - model on every captured run: -20.3..-5.0 us (1 tick = 10 us; the model's synchroniser is two 5 us clocks). |
| SMBus, every protocol with PEC | device pre-timed, 4 captured runs | PASS. Write byte/word, read byte/word, block read, receive byte, block write, quick write/read, process call, send byte, empty block: the expected results and data. On the bus every PEC = CRC-8 of its bytes, address bytes included (e.g. 16201707576972656c696e97). SCL high 4.48..11.20 us, low 5.60..17.92, bit periods 10.08..17.92 (55.8..99.2 kHz). START hold 4.48, STOP setup 6.72, bus free 29.1..30.2 us. SCL edges board - model -720..-20 ns, one constant per run. |
| SMBus, without PEC | captured | PASS. Results ok x3, the last byte read not acknowledged, written 160d99. |
| SMBus, PEC errors | captured | PASS. A flipped bit gives pec_bad (read 2412b8). A device without PEC gives pec_bad. Then pec_ok. The pins-only reader finds the same PECs wrong. |
| SMBus, NACKs | captured | PASS. No device at 0x50: addr_nack with 4 words dropped. The 2nd data byte refused: data_nack with 3 dropped. The next read is pec_ok each time. |
| SMBus, clock stretching | 3 and 50 us captured, 2 ms polled | PASS. Block read pec_ok and block write ok at all three. Stretched SCL lows on the bus 8.96..53.38 us. |
| SMBus, timeout | device holds SCL 40 ms, polled | PASS. Timeout, then pec_ok after the device's 35 ms reset. The timeout record reached the host 25.16 ms after the chip released SCL (host-timed: 0.1 ms polls; firmware 25.003 ms + 9 ticks). |
| SMBus, busy and arbitration | polled / captured | PASS. SDA held low 40 ms: busy with 3 words dropped, then a read once the bus is free. Another master addressing 0x08: lost at our 6th address bit. The bus carried 000100 at our SCL rises, and our drive of both lines is off. Over all the other captured SMBus runs, SCL board - model -1260..+300 ns, one constant per run except the 50 us stretch (re-synchronised on the device's releases). |
| ISO 7816, T=0 | reader + clock core, card pre-timed, polled | PASS. ATR 3bb4110000574c474f, direct convention (FINV 0). READ BINARY 16: INS, 20..2f, 9000. UPDATE BINARY "HELLO": INS, 9000. INS 12: 6D00. READ 5 with N = 1: "HELLO", 9000. 52 records = the model run's. |
| ISO 7816, inverse convention, NULLs | polled | PASS. 3F ATR: FINV = 1, READ 8 and UPDATE 3 in the inverse convention. A card sending 60 60 before INS: 2 NULLs, the data, 9000. |
| ISO 7816, error signals and repetition | polled | PASS. A card character with a wrong parity: our 2 error signals, then the right byte. Corrupted 9 times: 4 signals, the 5th copy kept and flagged. Our bytes rejected once and twice: SEND records (0 unsent, 1 / 2 repetitions), 9000. Rejected 6 times: aborted with 4 bytes unsent after 5 repetitions. Bad TS, a mute card (timeout) and DEACT (I/O driven low, RST low) as the model. |
| ISO 7816, reader timing | la_capture in place of the clock core | PASS. Our error signals 10.498 etu after the start of the card's character (10.5 +- 0.2) and 6696 clocks long (1.5 etu). Header characters 53568 clocks apart (12 etu, exact); 75888 (17 etu) with N = 5. Repetitions 14.040..14.048 etu after the previous start (2 etu after the card's error signal ends). RST rose 2.139..2.144 etu after the trigger. I/O board - model -440..+280 ns (1 reader tick = 720 ns). |
| ISO 7816, two cores, host-read | reader + clock core, GPIO polled | PASS. The reader's drive edge for edge as the model's (18 edges, -0.3..+10.4 us after RST's rise, host-timed). Read as characters: 00b0000004 with parity right, and 2 error signals of 1.5 etu. Reads at most 80 us apart; each edge seen within 4.1..14.5 us. |
| ISO 7816, CLK | clock core on uio2 -> SPI device | PASS. Rise to rise 96 x 2.5 ns = 240.0 ns every time: 4.1667 MHz = f_clk / 12, 1 etu = 372 periods = 89.28 us. 33789 rises in 407706 clocks (host-timed window). |
| RC-5 TX, carrier | V0 -> la_capture, 1 clock | PASS. (5, 53) and RC-5X (20, 70): carrier periods 1388/1389 clocks (35999.7 / 35999.9 Hz), on 462/463. Bursts of 32 and 64 periods, every burst start on the 96-tick half-bit grid. Decoded through the receiver-module model. |
| RC-5 TX, frames | V0 -> la_capture, 25 us ticks | PASS. New, repeat, repeat, new, new (RC-5X): toggle bits 1, 1, 1, 0, 1. Frame starts 113.775..113.800 ms apart (12288 ticks = 113.78 ms, +-1 capture tick). |
| RC-5 RX | generator -> uio0, polled | PASS. A remote at x0.9 / x1.0 / x1.1 with bursts 0 / +150 / -150 us (7 combinations, 4 frames each): every address, command and toggle bit right. Negative controls: a 200 us glitch ignored, a cut frame and a bit without its mid-bit edge reported, then (7, 33). |
| RC-5 TX -> RX | board captures -> receiver-module model (host) -> generator -> rc5_rx | PASS. (5, 53, 1) and (20, 70, 1) as sent. A hybrid: the demodulation is our model on the host, not a receiver module. |

sigrok (`postfreeze3_demo.py sigrok pf3.json`, the board's captures):
* `cec` decodes every frame as the firmware and the pins-only decoder do:
  "40 PING R: ACK", "40:36 STANDBY R: ACK", "4f:82:10:00 ACTIVE_SOURCE R:
  ACK", the 16-block SET_OSD_NAME, the TV's 04:8f, "05 ... R: NACK", 0f:36,
  04:44:41, the lost-then-retried pair and the free-time triple. It gives no
  warnings.
* `i2c`, with the PEC recomputed over sigrok's bytes, gives every PEC
  transaction right. The three flipped or missing PECs are wrong, as
  intended.
* `uart` (even parity, f_CLK/372 baud) on I/O reads the ATR, the header and
  the card's answers. It reports a frame error at each character our error
  signal rejected, as in test_iso7816.
* `ir_rc5` on the receiver-module output gives (5, 53, 1), (20, 6, 1)
  (extended: (20, 70, 1)), and the frames' toggle bits 1, 1, 1, 0, 1.

**One failure on the way, a host-timing artefact.** The first `iso` run
(07:11) failed `iso_host`. The bytes were right, but the first character's
parity sample came out wrong because the host saw its start edge 110 us
late. The poll loop stored every read, and Python's collector paused it.
Now the loop stores only changes, runs with `gc` off, and reports its
largest gap between reads. It passed 3 of 3 times on its own and in both
full runs, with gaps of up to 193 us that fell between edges.

No firmware or tool bug was found. One observation: `smbus_host`'s and
`iso7816`'s `put` take one tick more when the RX FIFO is full, a path where
records are being dropped anyway; `cec` pads its `put` to equal lengths.
This is why the captures that overflow core 0's FIFO are pre-timed from a
model run that overflows the same way.

**Not run / limits.**
* No protocol went over package pins (no PMOD pin enabled). There was no
  HDMI sink, smart card, SMBus device, IR LED or receiver module. Every far
  end is our pins-only model played by the generator, pre-timed from a
  model run (not reactive), or the chip's other core.
* The CEC and SMBus far ends react 30 us / 2 us after what they see; this
  is our choice, within the standards' allowances.
* In ISO 7816's two-core configuration, the I/O line's exact timing and
  CLK's edges were not captured: the I/O drive was host-read (bytes and
  order, us-scale timing). The exact reader timing comes from runs with
  la_capture in place of the clock core. CLK was measured separately by
  the SPI device.
* SMBus with 2 ms stretching and the 25 ms timeout were polled, not
  captured: one burst can't drain SMBus's edge rate for that long.
* RC-5's TX-to-RX loop used our receiver-module model on the host in place
  of a real demodulator (`rc5_rx` takes a demodulated envelope; `rc5_tx`
  makes the carrier).
* Captured runs have at most 8 TX FIFO words (the host can't feed during
  the burst).

**At the end** (07:22 UTC): `bringup_kria.py` (all steps) ALL PASS, clock
49.9983 MHz. Left loaded: `protoemu`, PMOD0/PMOD1 = 0, pe_lab MODE 0, host
port on the GPIO path (pe_spim SEL 0).

## USB enumeration with a replayed host, no PC (2026-10-14, `host/usb_replay_demo.py`)

The autonomous USB device (`firmware/usb_enum.pasm`,
[docs/usb-enumeration.md](../../docs/usb-enumeration.md)) and the one-core
device firmwares `usb_ls_dev` / `usb_fs_dev`, run on the 48 MHz build
(`protoemu48`) against a USB host that pe_lab's pattern generator replays on
the D+/D- pads. No PC, cable or PMOD pin is involved. What ran is the RTL on
an FPGA with digital levels on fabric pads, not silicon and not a real host
controller.

    python3 fpga/kv260/host/usb_replay_demo.py --model --plans up.json --save m.json      # plans + replay on the model (~15 min)
    scp up.json ubuntu@kria.[tailnet domain redacted]:usb_logs/
    ssh ubuntu@kria.[tailnet domain redacted] sudo python3 -u protoemu48/fpga/kv260/host/usb_replay_demo.py \
        --expect 60b0692 --plans usb_logs/up.json --save usb_logs/hw.json
    python3 fpga/kv260/host/usb_replay_demo.py compare hw.json m.json
    python3 fpga/kv260/host/usb_replay_demo.py sigrok hw.json --plans up.json

**Board state and build.** Final run 2026-10-14 08:55:09-08:55:29 UTC
(earlier full runs at 08:04:59 and 08:19:11 gave the same results with the
14 enumeration scenarios). `xmutil loadapp protoemu48`; `bringup_kria.py --mhz
48 --expect 60b0692 --only build clock id` PASS: RTL revision `60b0692`
(digest `036faa21`, clk_div 25), clock 47.9982 MHz (the script: 47.99924 MHz
over 2 s). `src/*.v` is identical in 60b0692 and this branch. Host software
(`tools/`, `firmware/`, `fpga/kv260/host/`) was refreshed in `~/protoemu48`
and `~/protoemu` as `install.sh` does, keeping `expected_build.json` (there is
none in `~/protoemu48`, hence `--expect`). Each run's output went to a file,
synced after the run (`~/usb_logs/`).

**Method.**
* *Plan.* Each scenario first runs on the cycle model with an independent
  host model in the loop: `pe.usbhost.ControlHost` at low speed (32 clocks per
  bit), the same transaction logic at 4 clocks per bit for full speed
  (`FsClockHost`: edges on the clock grid, a SOF every ms;
  `pe.usbfs.FsControlHost` places edges in continuous time, which the
  generator can't play). The host's own line levels (its drive, or the idle J
  of the pull-up when nobody drives) are recorded clock by clock, and cut at
  idle points into segments of at most 1000 generator entries that the
  observer can cover. `--plans` stores these runs (16 scenarios, 2.3 MB).
* *Replay.* The device firmware is loaded and started on core 0 once and never
  touched again (usb_enum), or, for `usb_ls_dev`/`usb_fs_dev`, served by
  `pe.usbdev.DeviceSoftware` over the host port between segments, exactly
  where and with the clock the plan served it. For each segment, core 1 is
  restarted with the observer, the generator plays the host's levels on uio0
  (D+ at low speed, D- at full speed) and uio1, relative to a trigger on uio2,
  and one READ_FIXED burst, started a few microseconds after the generator,
  drains the observer.
* *Observers.* Low speed: `la_capture` (1 tick = 1 clock; USB edges are at
  least 32 clocks apart, so every edge is timestamped exactly; quad burst at
  SCK = clk/div, div chosen so the burst outlasts the segment and drains
  faster than the edges come). Full speed has edges 4 clocks apart, under
  `la_capture`'s 7-tick dead time, so core 1 runs `host/usb_sampler.pasm`:
  `in pins, 3` every clock, 10 samples per word, drained at 8 clocks per word
  (quad, SCK = clk) from the trigger on: 0.55 ms of every clock per segment.
  Every edge of a full-speed segment must fall in that window, except the end
  of a 10 ms bus reset.
* *Pads and electrical simplification.* kv260_top gives `uio_in = uio_oe ?
  uio_out : generator`: while the line unit drives, the pads are the device's;
  otherwise the generator's, which plays the host's drive or the idle J. There
  is no USB transceiver or PHY: no differential receiver, no single-ended
  thresholds, no 1.5 kohm pull-up or 15 kohm pull-downs (J is a generator
  value), no edge rates, no contention model (the device's drive wins). All
  pad changes are launched by clock edges, so the line unit's falling-edge
  sample sees the next rising edge's value; the model runs with `uio_mid =
  uio_in` (`sync_mid`). With the model's default (an asynchronous edge, seen
  half a clock later) the full-speed plan's device answers are one clock
  later than the board's (turnaround 3.50-4.25 instead of 3.25-4.00 bit
  times), so the comparison below resolves one clock.
* *Judge.* Every pad edge of every segment, relative to the trigger, against
  the plan: the host's edges (the generator: a check of the replay) and the
  device's (the chip). Then the packets decoded from the board's edges
  (`pe.usb`, run lengths at the nominal bit time) against the plan's, bytes
  and owner; the device's end state (address and Configured from the host
  port after the last segment; for the `*-dev` scenarios the device software's
  requests, address and re-stage count). `compare` checks the board's bus
  events with their times in clocks against the `--model` replay; `sigrok`
  decodes the board's edges with usb_signalling + usb_packet.

**Results at 48 MHz** (16 of 16 scenarios PASS; every device edge of every
scenario at the clock the model gives, `compare`: all 16 equal):

| Scenario | Low speed (1.5 Mbit/s) | Full speed (12 Mbit/s) |
|---|---|---|
| `enum`: device descriptor (8), SET_ADDRESS 5, device (18), configuration (9, 18), string STALLed, SET_CONFIGURATION 1, GET_STATUS | PASS: 2 segments, 51 host / 26 device packets, 834 device edges board - model 0 clocks, turnaround 2.16 bit times, address 5 Configured | PASS: same packets, 834 edges 0 clocks, turnaround 3.25-4.00 |
| `linux`: 64-byte read at address 0, 10 ms bus reset, SET_ADDRESS 7, ..., GET_CONFIGURATION | PASS: 4 segments, 52/26 packets, 913 edges 0 clocks, address 7 Configured | PASS: 3 segments, 53/26, 913 edges 0 |
| `faults`: per step 0-2 lost device ACKs (host retries), host ACKs not sent, DATA treated as damaged (13 injected) | PASS: 61/33 packets, 1168 edges 0 clocks, address 9 Configured | PASS: same |
| `reset`: resets in the data stage, after the address, configured (50 us SE0), at 6 clock phases, a 3 us reset unconfiguring | PASS: 6 segments, 155/73 packets, 2220 edges 0 clocks; address 5 ignored after each reset; end address 0, not Configured | PASS: 10 segments, 164/73, 2220 edges 0 |
| `ack`: after a GET_DESCRIPTOR's first DATA1, the host's ACK nominal, dribbled 1-3 bits, with 1 or 3 extra bytes, or cut short (7 partial patterns) | PASS: nominal and dribbled accepted (next IN: DATA0 with bytes 8-15), the 11 malformed ones rejected (the same DATA1 again, then a nominal ACK advances it); 112/56 packets, 3404 edges 0 clocks | PASS: same verdicts, 3404 edges 0 |
| `stall`: 9 unsupported requests STALLed and cleared by the next SETUP; other addresses/endpoints, bad CRC5, bad CRC16 ignored; wLength 0-255 on both descriptors | PASS: 15 segments, 347/179 packets, 6292 edges 0 clocks | PASS: 348/179, 6292 edges 0 |
| `addr`: SET_ADDRESS's status ACK lost, then a SETUP first; then an IN first to the new address | PASS: 33/17 packets, 587 edges 0 clocks; the IN-first answered 2.16 bit times after the EOP; address 78 Configured | PASS: the IN-first answered 4.75 bit times (19 clocks; limit 6.5 = 26) |
| `dev`: `usb_ls_dev` / `usb_fs_dev` + DeviceSoftware: 64-byte read, SET_ADDRESS 5, descriptors, SET_CONFIGURATION, then a DATA1 not ACKed, re-staged after 100 us | PASS: 27 segments, 49/25 packets, 1022 edges 0 clocks, turnaround 2.16-2.22; requests and re-stage count = the model's | PASS: same, turnaround 2.75-3.75 |

Off the board, sigrok's `usb_signalling` + `usb_packet` on the board's edges
give exactly the plan's packets for every scenario, with no errors other than
the two deliberate ones (`stall`: "CRC5 ERROR", "CRC16 ERROR"). sigrok reads
each of the 14 malformed ACKs as a plain ACK; the firmware rejects the 11 that
USB 2.0 §8.4.5 excludes (extra bytes, short) and takes the dribbles (§7.1.9.1).

**Not run / limits.** No real host: the host is our model's waveform,
pre-timed, so it can't react to an answer that differs from the model's (any
difference shows as a mismatch, and none happened). No PHY or analogue
behaviour (above). Full speed is observed only in the sampler's 0.55 ms
window per segment: the last 9.5 ms of a 10 ms bus reset (no edges) are
not observed. `usb_ls_rx` + `usb_ls_tx` (the two-core device) and the
USB-UART pairs (`usb_uart_*` with `uart_rx_batch4` / `overlap4` /
`envelope3` / `capture_peer` / `mailbox`) use both cores, leaving none to
observe the pads: not run. The real-host demo (`host/usb_demo.py`, below) ran
at low speed on 2026-10-30.

## Firmwares that had never run on the FPGA (2026-10-14, `host/legacy_demo.py`)

`ws2812`, `spi_master`, `i2c_sniff`, `ps2_device`, `onewire`, `jtag`,
`can_tx` + `can_rx` and `eth10_tx` + `eth10_rx` on the 50 MHz build. What ran
is the RTL on an FPGA, not silicon; every line is a virtual pin or a uio pad
driven by a core or pe_lab; no PMOD pin was enabled.

    python3 fpga/kv260/host/legacy_demo.py --model --save ml.json --plans pl.json     # ~10 min
    scp pl.json ubuntu@kria.[tailnet domain redacted]:legacy_logs/
    ssh ubuntu@kria.[tailnet domain redacted] sudo python3 -u protoemu/fpga/kv260/host/legacy_demo.py \
        --save legacy_logs/hl.json --plans legacy_logs/pl.json
    python3 fpga/kv260/host/legacy_demo.py compare hl.json ml.json
    python3 fpga/kv260/host/legacy_demo.py sigrok hl.json

**Board state and build.** Final run 2026-10-14 08:58:59-08:59:14 UTC.
`protoemu` loaded: `bringup_kria.py --only build clock id` PASS, RTL
revision `d1aa46f` (digest `3adc796c`, clk_div 24), clock 49.9982 MHz; the
script measured 49.99917 MHz over 2 s and derived every CLKDIV for 50.000 MHz.
Same host-software refresh as above. Earlier runs (08:51:59, 08:54:18) gave
the same results except `ws2812` at 08:51:59 (below).

**Method.** The post-freeze rounds' toolkit. Transmitters on core 0 behind the
trigger preamble, `la_capture` on core 1 (1 tick = 1 clock), drained by one
quad READ_FIXED burst started a few microseconds after the generator. Far ends
that answer are pre-timed: the scenario first runs on the cycle model with a
pins-only model of the far end that sees every uio pad (1-Wire slave, JTAG
TAP, I2C EEPROM, CAN nodes; from the model tests), and the generator replays
that far end's edges relative to the trigger. Receivers get the generator's
waveforms; CAN's two cores share pe_lab's wired-AND line (I2C mode). Judges:
the model tests' decoders and models; `compare` with the `--model` run (every
run's deterministic results equal); sigrok off the board.

**Results at 50 MHz** (13 of 13 runs PASS; one clock = 20 ns):

| Firmware | How | Result |
|---|---|---|
| `ws2812` | V0 -> la_capture, CLKDIV 6.25 (8 MHz ticks), 2 x 8 colours | PASS. All 16 colours right. T0H 380 ns, T1H 880 ns, bit 1240-1260 ns (WS2812B: 400 / 800 +-150 ns, 1.25 us +-600 ns), then low for 3.9 ms. **Bug found and fixed** (below): before, a 0 was 2 ticks high |
| `spi_master` | pe_lab's SPI device, 2 x 8 random bytes per DELAY 0..31 | PASS. CLKDIV 3 (4.17 MHz SCK): right up to DELAY 29 (MISO 77.5 ns after the clock edge that dropped SCK); CLKDIV 2 (6.25 MHz): up to DELAY 13 (37.5 ns); CLKDIV 1 (12.5 MHz): every DELAY reads MISO a bit late (two from DELAY 30), as test_spi_fast predicts; CLKDIV 1 with SYNC_BYPASS on MISO: right up to DELAY 5 (17.5 ns). MOSI right in all 256 rounds, SCK rise to rise exact (32/64/96 x 2.5 ns) |
| `i2c_sniff` | generator plays an I2C bus at 100 kHz, 400 kHz, 1 MHz | PASS. 21 events each = the waveform's: 5 STARTs (2 repeated), 4 STOPs, 16 bytes with their ACK/NACK bits (reads driven by the "device", the master's NACK) |
| `i2c_sniff` + `i2c_master` | master on core 0 (open drain), sniffer on core 1, EEPROM pre-timed (2 us after SCL falls) | PASS. Master: write 0x50 [10 42] ACKed, 0x51 NACKed (replies 0 0 0 1). Sniffer: START A0 10 42 STOP START A2(NACK) STOP |
| `ps2_device` | CLK uio0, DAT uio1 open drain; the generator is the pull-ups / the host | PASS. 8 frames = the codes, odd parity, start/stop right; CLK low 40 us, fall to fall 80-90 us (12.2 kHz; PS/2: 10-16.7 kHz). Host inhibit (CLK held low 3 ms before the device starts): the first CLK fall 44.5 us after the release. Not covered: an inhibit during a frame (the firmware checks CLK only before a frame) |
| `onewire` | DQ uio0 open drain, 1 us ticks, slave pre-timed | PASS. Reset/presence 1, write 0x33 done, READ ROM 28 ff 4c 60 91 16 (6 bytes, as queued); no slave: presence 0, a read 0xff. Reset low 480.0 us; DQ edges within a tick (1 us) of the model's |
| `jtag` | TDI/TMS/TCK uio4-6, TDO uio0 from a pre-timed TAP, CLKDIV 4 and 8 | PASS. IDCODE 0x4ba00477; IR = 1111 (BYPASS), 8 bits through: TDO = TDI one TCK later. TCK/TMS/TDI 134 edges, each the model's + one start offset (0 / -140 ns, under a tick); 20 records closer than la_capture's 7 ticks not compared. At CLKDIV 2 (6.25 MHz) the model needs TDO within 1 clock (20 ns) of TCK's fall at the pad, 3 at CLKDIV 3: a pre-timed TAP can't promise that against the core's start offset, so those ran only on the model |
| `can_tx` + `can_rx` | both cores, open drain on pe_lab's wired-AND line, 500 kbit/s (CLKDIV 2.08) | PASS. 5 frames (IDs 0x123, 0x7f0, 0x001, 0x555, 0x2aa; DLC 0-8): TX status 0 (ACKed), RX CRC ok, every destuffed bit right |
| `can_rx` | generator frames at 0, +-0.5 %, +-1 %; CRC corrupted; stuff error | PASS. Status 1 and every bit right, ACK slot driven 2.0 us; corrupted CRC: status 0, no ACK; stuff error: status 2, error flag (11.7 bits dominant on the bus, with the transmitter's dominant bits). **Bug found and fixed** (below) |
| `can_tx` | pre-timed ACKing node, nobody, a node with ID 0x100 | PASS. Status 0 / 1 / 2 (lost arbitration); the bus carries 0x123's 58 destuffed bits with CRC15 and the ACK slot dominant / recessive, or the winner's frame bit for bit; bus edges within 20 ns (a clock) of the model's |
| `eth10_rx` | generator Manchester frames (64 bytes) on uio0, on protoemu's clock (half bits 2 or 3 clocks: +-10 ns) and on the 45.93 MHz alternate clock (phase walking) | PASS. 4 frames received, FCS ok |
| `eth10_tx` -> `eth10_rx` | core 1 on V0/V1 at CLKDIV 1.25 (40/60 ns half cells, test_eth10's 50 MHz case) -> core 0, RXP = V0 | PASS. 2 x 64-byte frames, FCS ok |
| `eth10_tx` | core 0 on uio3/uio4, sampled every clock by `usb_sampler` | PASS. 576 wire bits (preamble, SFD, frame, FCS ok), every bit 5 clocks with its mid-bit transition, runs 2/3/5 clocks; a 100 ns link pulse first; TP_IDL high 280 ns |

sigrok on the board's captures: `can` decodes every frame with the bus's ACK
bit as above (the stuff-error capture with EOF warnings, as expected);
`onewire_network` "Reset/presence: true/false", "ROM command 0x33 'Read
ROM'"; `jtag` "DR TDO 0x4ba00477", "IR TDO 0001", the bypass bits. sigrok's
`ps2` decodes the first frame and then loses the framing on both captures,
the board's and the model's: its decoder takes 12 falling edges per frame
(`handle_bits` emits at bitcount 11), so it swallows the next frame's start
bit; our falling-edge decoder and the model test's agree on all 8 frames.

**Two firmware bugs, found while preparing these runs and fixed** (both by
this script's `--model` pass at 50 MHz, before the board; failing model tests
first, then the fixes; the board ran the fixed firmware):
* `ws2812`: a 0 bit was 2 ticks high and 8 low (250 ns at 48 MHz, the bottom
  edge of WS2812B's T0H window), while the header says 3 / 7 (375 / 875 ns).
  At 50 MHz (CLKDIV 6.25) T0H came out 240-260 ns, outside the window. Fixed
  by moving one delay (same 4 words); `test_ws2812_bit_timing_is_the_documented_one`.
* `can_rx`: when a frame's CRC ends in five equal bits, the transmitter sends
  a stuff bit before the CRC delimiter (`can_tx` does); `can_rx` didn't skip it
  and drove its ACK one bit early, in the delimiter, so the transmitter saw no
  ACK. About one random frame in 31 (20,000 sampled); the generator's +0.5 %
  frame here was one. The model tests' frames happened to avoid it. Fixed:
  the stuff bit is sampled (six equal bits: stuff error) and the delimiter and
  ACK come a bit later; three tests (`test_can.py`).

**One failure on the way, a script bug.** `ws2812` failed at 08:51:59 (three
times in a row) with a gap record: the first `import usb_replay_demo` happened
between starting `la_capture` and the burst and took long enough on the Kria
for eight idle records to fill the FIFO. The import is now at the top.

**Not run.** `swd_probe` (parked). The USB-UART pairs and `uart_rx_batch4` /
`uart_rx_overlap4` / `uart_rx_envelope3` (their only consumer is the USB core;
see above). `usb_ls_rx` + `usb_ls_tx` (above). Nothing over package pins: no
LEDs, SPI chip, PS/2 host, 1-Wire device, JTAG target, CAN transceiver or
Ethernet PHY; every far end is our pins-only model or the chip's other core.
10BASE-T ran at 50 MHz with CLKDIV 1.25 (the test suite's 50 MHz case), not
the 40 MHz its header specifies; the FPGA builds are 50 and 48 MHz only.

**At the end**: after the last 48 MHz run, `xmutil loadapp protoemu` and
`bringup_kria.py` (all steps) ALL PASS at 08:55:39 UTC (49.9983 MHz); after
the last legacy run, `bringup_kria.py` (all steps) ALL PASS again at
09:07:15 UTC (49.9980 MHz). Left loaded: `protoemu`, PMOD0/PMOD1 = 0, pe_lab
MODE 0, host port on the GPIO path (pe_spim SEL 0).

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
      loaded usb_enum (246 words, low speed) into core 0, CRCCTRL, line unit
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

### Result against Linux (low speed, 2026-10-30)

The first real host for this firmware, on the first attempt: the wiring above
on a breadboard, port 1-1.4 of the board's USB5744 hub, and the Kria's Linux
6.8 xHCI as host. RTL revision 60b0692 (no Verilog change in `src/` since);
`firmware/usb_enum.pasm`, `tools/pe/usbenum.py` and `usb_demo.py` on the board
byte-identical to `main`.

    [clock] protoemu clk measured 47.9990 MHz
      pins pulsed low then read (of 5): pmod[0] 0, pmod[1] 5, pmod[2] 0, pmod[3] 0
      pull-up found on pmod[1] = silkscreen 3: columns normal, it is D- (low-speed wiring)
      loaded usb_enum (246 words, low speed) into core 0, CRCCTRL, line unit
      [  111.964734] usb 1-1.4: new low-speed USB device number 8 using xhci-hcd
      [  112.054482] usb 1-1.4: New USB device found, idVendor=1209, idProduct=5634, bcdDevice= 0.01
    PASS: Linux enumerated 1209:5634 at low speed and configured it (1-1.4: devnum 8, speed 1.5 Mbit/s, bConfigurationValue 1, bMaxPacketSize0 8, bcdDevice 0001)
    [chip] after the verdict, over the host port: address 8, configured True, pending address False, stalled False

| Test | Result |
|---|---|
| Attach and enumerate, each after a fresh firmware load | 18 of 18 to configured, each on the first attempt after the core started |
| Live `GET_DESCRIPTOR` (device, configuration) through usbfs, `host/usb_stress.py` | 10,000 of 10,000 right bytes, 0.66 ms each |
| Bus resets from the kernel, each re-enumerated | 58 of 58 |
| `GET_STATUS` / `GET_CONFIGURATION` | `0000` / `01` |
| String 0, device qualifier | STALL, as designed: no strings, not high speed |

The 18 attaches include the ones `--stop` makes. The bus resets are the first
real-host test of the reset fix of 2026-10-13. The chip's address differs from
Linux's `devnum` because the xHCI controller picks the address, a new one for
each reset.

Reloading the app with the wiring in place logs a burst in `dmesg`, and these
are not device failures: `device descriptor read/64, error -32`, then `device
not accepting address N, error -71`. The pull-up attaches the line while no
firmware runs, so the hub tries a silent device, as it does at every boot. In
the runs above each burst ended before the core could have started
(`usb_demo.py` holds the line for 0.5 s first).

**Not shown.** This is the RTL on FPGA fabric, 3.3 V LVCMOS pins, not the chip
and not silicon. One host and one hub: no Windows, no macOS, no USB-IF
compliance test. Nothing was measured with a scope, so edge rates and eye
diagrams are unknown (the pads' edges are faster than low speed's 75-300 ns).
The device has no data endpoint, so this covers enumeration and control
transfers only. Full speed against a real host is a separate test, below.

**Full speed later**: move the 1.5 kohm from D- to D+ (the cable side of D+'s
27 ohm, to the same 3.3 V pin) and run `usb_demo.py --speed full`: the same
two wires, the chip's pins swapped by the PMOD map (D- = uio0, D+ = uio1),
4 clocks per bit. The 48 MHz clock is the PS's 100 MHz (99.999 MHz from its
IOPLL) x 12 / 25, about -10 ppm, inside full speed's 0.25 %. Not simulated
on this design (the chip RTL test `test_usb_autonomous_fs` covers the
firmware at full speed; on the board, full speed has run only against the
replayed host (above), not a real one); an FPGA pin with 27 ohm is probably also
outside full speed's driver-impedance window (28-44 ohm); keep the wires short.

### Build and staging (2026-10-07, branch `kv260-usb`)

On `main` since 2026-10-13, with the USB bus-reset fix (usb_enum waits for
D- low as a level; 241 words, one more than the transcripts recorded on
2026-10-07; 246 since the ACK tail guard of 2026-10-13). The bitstream is unchanged (no `src/` change); the Kria's
`~/protoemu48/{tools,firmware,fpga/kv260/host}` were refreshed from `main`
the same day (nothing loaded), and `make -C fpga/kv260/sim` passed 5/5 with
the 241-word build. Rerun 2026-10-14 on runner1 with `main` a1561d6's
firmware (246-word `usb_enum`; checkout `beddbe2`, which adds only release
tooling): 5/5 (test_bringup_steps, test_spim, test_lab, test_usb_demo,
test_usb_demo_wiring), 321 s of Icarus, `test_usb_demo` again 27
transactions in 168540 cycles with the device at address 7, configured.

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
disabled. The USB wiring and `usb_demo.py` without `--dry-run` first ran on
2026-10-30: see "Result against Linux" above.

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
    loaded usb_enum (246 words, low speed) into core 0, CRCCTRL, line unit
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
test_spim, test_lab unchanged by the new pad model). The excerpt above is
from the 2026-10-14 rerun with the 246-word `usb_enum` (5/5, [above](#build-and-staging-2026-10-07-branch-kv260-usb)).

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
