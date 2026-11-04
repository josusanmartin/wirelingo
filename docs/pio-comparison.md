# protoemu against RP2040 PIO on standard protocols

This page compares protoemu's firmware with the official Raspberry Pi PIO
programs for the same protocols: UART TX/RX, SPI, I2C and low-speed USB.
Sources and measurements are kept apart, and the page says where PIO is better.
For the ISA-level differences see [isa.md §1](isa.md). For the program
occupancy table see [guide.md](guide.md).

**Short version.** protoemu is not faster than PIO, and its programs are not
smaller. Per state-machine cycle the two match on UART and SPI. PIO runs on a
clock 2.7× faster, spends less I2C bus time per byte, and its instructions are
half the width. On the signed-off g4 chip PIO also needed half the system
clocks per SPI bit; v2's input-synchroniser bypass (`SYNC_BYPASS`) closes that
gap (4 clocks per SCK on both).

protoemu's advantage is how much the host has to do, and what happens when the
bus misbehaves:

* an I2C master that can't hang;
* START/STOP and per-byte ACK handled in the engine;
* USB-LS handshakes in the engine;
* a UART receiver that tolerates faster senders;
* room for programs that don't fit in PIO's 32 shared slots.

"More efficient than PIO" is defensible only in that sense, as host involvement
and fault handling per protocol. It is not true of speed or code density.

## How the numbers were obtained

* **PIO programs:** [raspberrypi/pico-examples](https://github.com/raspberrypi/pico-examples)
  at commit `0d62f75b` (`pio/uart_tx`, `pio/uart_rx`, `pio/spi`, `pio/i2c`),
  and [sekigon-gonnoc/Pico-PIO-USB](https://github.com/sekigon-gonnoc/Pico-PIO-USB)
  at `5a37a66d`. They were assembled with the official `pioasm`, built from
  [raspberrypi/pico-sdk](https://github.com/raspberrypi/pico-sdk) `079c6f39`.
  [test/model/pio_reference.json](../test/model/pio_reference.json) holds the
  resulting words, each source's pinned URL and its sha256.
* **protoemu programs:** `firmware/*.pasm` assembled by `tools/pe/asm.py`.
  Counts come from the assembled words.
* **protoemu behaviour:** the cycle-accurate model (`tools/pe/sim.py`), which is
  checked against the RTL cycle by cycle ([verification.md](verification.md)).
* **PIO behaviour:** [test/model/rp2040_pio.py](../test/model/rp2040_pio.py), a
  small instruction-level model of one state machine. It follows the RP2040
  datasheet[^ds] §3.2.4 (stalling), §3.4 (instructions), §3.5.1 (side-set),
  §3.5.4 (autopush and autopull) and §3.5.7 (OUT EXEC). It runs the `pioasm`
  words.
  * The I2C host side is pico-examples' `pio_i2c.c`, transcribed function by
    function into Python. The CPU is treated as infinitely fast between SM
    clocks, which favours PIO.
  * **Validation:** with the same I2C target model and an independent bus
    decoder, both engines produce the expected bus transcripts for a register
    write and a repeated-START register read: START/STOP, every byte and ACK
    bit. UART TX takes 80 cycles per byte, as in datasheet §3.6.3.
    UART RX and SPI loopback return the sent bytes.
  * **Not modelled:** the PIO input synchroniser (two system clocks; pio_spi
    bypasses it), DMA, and electrical behaviour.
* **Everything is simulation.** Neither chip's pads, boards or analog behaviour
  were measured. RP2040 silicon was not used.

Reproduce (repo root, venv active):

```
PYTHONPATH=tools python -m pytest -q test/model/test_pio_comparison.py   # 9 tests, ~1 min
PYTHONPATH=tools:test/model python test/model/test_pio_comparison.py      # prints every measurement
```

## Summary

Unless a row says "datasheet" or "source", PIO behaviour comes from the PIO
model and protoemu behaviour from the protoemu model. 100 kHz I2C uses each
side's own divider: pico-examples runs 32 SM clocks per bit at 3.2 MHz,
protoemu 8 ticks per bit at 800 kHz.

| | RP2040 PIO (pico-examples) | protoemu (firmware/) | Better |
|---|---|---|---|
| **Clock, process** | 133 MHz max (200 MHz at 1.15 V), 40 nm (datasheet) | 50 MHz at all three corners (run fo6; earlier runs reached about 44-46 MHz at the slow corner); IHP 130 nm ([physical.md](physical.md)) | PIO |
| **Engines** | 2 blocks × 4 SMs, 32 instruction slots per block, shared by its 4 SMs | 2 cores, 256-word private bank each | PIO on engine count; protoemu on per-engine program space |
| **UART TX** program | 4 instr = 8 B | 4 instr = 16 B | PIO (bytes) |
| UART TX timing | 8 SM clocks per bit; max 16.6 Mbaud at 133 MHz | 8 ticks per bit; max 6.25 Mbaud at 50 MHz | equal per cycle; PIO in absolute rate |
| **UART RX** program | 9 instr = 18 B | 14 instr = 56 B | PIO |
| UART RX framing error / break | byte dropped, sticky IRQ 4 | byte pushed, sticky flag F0 | PIO (bad byte not delivered) |
| UART RX, back-to-back frames from a fast sender | fails at −3 % (needs frames ≥ 78 SM clocks) | passes −3.5 % (needs ≥ 77 ticks) | protoemu |
| **SPI** program | 2 instr = 4 B | 2 instr = 8 B | PIO (bytes) |
| SPI fastest correct loopback | 4 system clocks per SCK (synchroniser bypassed): 33 MHz at 133 MHz | g4: 8 per SCK (at 4 the synchroniser makes MISO a bit late), 6.25 MHz. v2 with `SYNC_BYPASS`: 4 per SCK, 12.5 MHz | g4: PIO; v2: equal per clock, PIO in absolute rate |
| **I2C** program | 18 instr in PIO + START/STOP/RSTART injected by the CPU as instructions in the FIFO | 90 instr, all in the engine | PIO on size; protoemu on autonomy |
| I2C register read (repeated START, 2 bytes): host accesses | 19 FIFO writes + 3 FIFO reads + 3 control-register writes | 8 FIFO writes + 5 FIFO reads | protoemu (count); bytes similar, see below |
| I2C bus time, START to STOP | 478 µs; 89.7 µs per byte | 601 µs; 111 µs per byte (560 and 105 before the SDA read-back) | PIO |
| I2C SCL low between bytes at 100 kHz | 4.69 µs (UM10204 minimum 4.7) | 5.00 µs | protoemu (marginal) |
| **I2C SCL held low** | SM waits forever; `pio_i2c_write_blocking` doesn't return (100 ms simulated) | error reply after 2.5 ms per command; idle after 7.6 ms; works again when SCL is freed | protoemu |
| I2C address NACK | SM halts on `irq wait`; CPU drains, forces a JMP, clears the IRQ, sends STOP; no further bytes clocked | per-byte ACK bit to host; keeps clocking bytes already queued | PIO (stops by itself) |
| I2C SDA held low | false ACK, "success" | bus fault: reply 0x200, F1 (false ACK and data 0x00 before `fault-fixes`) | protoemu |
| **USB** | Pico-PIO-USB: 32 instr, 3 SMs; CPU does NRZI/stuff encoding, CRC and handshake timing (source); FS device, FS/LS host | LS device only: 130 instr on one core + line unit (or 143 + 56 on two cores); handshakes, CRC check and stuff errors in the engine | different scope; protoemu on CPU offload |
| **Area** | not published; "each SM ≈ a standard SPI/I2C controller" (datasheet §3.1) | 0.354 mm² std cells + 0.099 mm² SRAM, whole chip incl. host port and line unit (run g4) | not comparable |
| **Host link** | on-chip bus, DMA up to 1 word per clock (datasheet §3.1) | external SPI: 193 Mbit/s quad (simulated, 193.6 on FPGA) | PIO |

## UART TX

Same algorithm, same length. protoemu's `uart_tx.pasm` is a transliteration of
`uart_tx.pio`, with `ldi r0, 7` for `set x, 7`.

| | PIO `uart_tx` | protoemu `uart_tx` |
|---|---|---|
| Instructions / bytes | 4 / 8 | 4 / 16 |
| Ticks per bit, per byte (measured, back to back) | 8, 80 | 8, 80 |
| Max baud (program-limited, divider = 1) | 125/8 = 15.6 M; 133/8 = 16.6 M | 50/8 = 6.25 M (fo6 meets 50 MHz at every corner) |
| Divider | 16.8 fractional (§3.5.5) | 16.8 fractional ([isa.md §4.1](isa.md)) |
| Edge resolution with a fractional divider | 1 system clock: 7.5 ns at 133 MHz | 1 clock: 20 ns |

PIO is better on everything here except code width, which neither side needs.
Both programs pull one 32-bit FIFO word per byte.

## UART RX

| | PIO `uart_rx` | protoemu `uart_rx` |
|---|---|---|
| Instructions / bytes | 9 / 18 | 14 / 56 |
| Stop bit low (framing error) | not pushed; `irq 4 rel` sets a sticky flag; waits for idle | pushed; flag F0; waits for idle |
| Break (line low for 25 bits) | nothing pushed; flag | 0x00 pushed; flag |
| Recovery | next byte after idle (measured) | next byte after idle (measured) |
| Back-to-back frames, sender off by −4 / −3.5 / −3 / −2.5 % (shorter bits), 0, +4 % | fail / fail / fail / pass / pass / pass | fail / pass / pass / pass / pass / pass |

**Why the tolerance differs.** Both programs sample bit *k* at D + 12 + 8k,
where D is the tick the start-bit WAIT completes. They differ in when they get
back to that WAIT:

* **PIO** checks the stop bit at D + 76 (`jmp pin`), pushes at D + 77 and is
  back at the WAIT at D + 78.
* **protoemu** pushes during the stop bit and is back at D + 77.

When frames are shorter than the return time, the lateness adds up frame after
frame until a bit is sampled in the wrong place. Frames are nominally 80 ticks.
PIO needs frames of at least 78 ticks (−2.5 %), protoemu at least 77
(−3.75 %). The sweep agrees: PIO passes at −2.5 % and fails at −3 %;
protoemu passes at −3.5 % and fails at −4 %. PIO's own comment notes that slack matters "in case the
TX clock is slightly too fast". The test sends 32 random bytes back to back at
each offset.

**The size gap is a firmware choice, not the ISA.** The test transliterates
`uart_rx.pio` instruction for instruction into protoemu assembly (`set x` →
`ldi r0`, `jmp x--` → `jmp r0--`, `irq` → `flag`). That gives 9 instructions,
and its results on the protoemu model match the PIO model exactly in every case
above (`test_uart_rx_pio_transliteration`, `test_uart_rx_back_to_back_tolerance`).
The shipped 14-instruction firmware spends 5 extra instructions on three things:

* `in null, 24` to right-align the byte, which pico-examples does instead by
  reading byte lane 3 on the RP2040 bus;
* pushing before the stop-bit check, which is what buys the tolerance and also
  why a bad byte is delivered;
* since `fault-fixes`, one `jmp pin` that reads the start bit again at mid-bit
  and drops a glitch ([fault-testing.md](fault-testing.md), finding 1).
  pico-examples' `uart_rx` has no such check. It costs no tick: the sample
  times and the D + 77 return are unchanged, so the tolerance row above holds.

## SPI (mode 0, full duplex)

| | PIO `spi_cpha0` | protoemu `spi_master` |
|---|---|---|
| Instructions / bytes | 2 / 4 | 2 / 8 |
| Ticks per SCK | 4 | 4 |
| Gap between back-to-back bytes (autopull/autopush) | none (16 bits in 64 ticks) | none (16 bits in 64 ticks) |
| Fastest setting with correct loopback, no external delay | clkdiv 1: 4 system clocks per SCK, input synchroniser bypassed (`pio_spi_init` does this) | g4: CLKDIV 2, 8 clocks per SCK; at CLKDIV 1 MISO is sampled one bit late (0xA5 → 0x52). v2: CLKDIV 1 with MISO in `SYNC_BYPASS`, 4 clocks per SCK |
| Against a device that answers a clock after SCK falls | MISO sampled 2 clocks after SCK falls | v2: `spi_master_fast` (3 instructions), 4 clocks per SCK, MISO sampled 2 clocks after SCK falls |
| Max SCK | 31.25 MHz at 125 MHz, 33 MHz at 133 MHz | g4: 6.25 MHz; v2: 12.5 MHz |

On g4, protoemu's pad inputs always pass a 2-flop synchroniser
([isa.md §2](isa.md)), and there is no bypass. PIO has a per-GPIO bypass
(`INPUT_SYNC_BYPASS`, datasheet §3.5.6.3). That was a real PIO advantage for
fast synchronous interfaces: 2× in clocks per bit, about 5× in absolute SCK.

v2 adds a per-pin bypass (`SYNC_BYPASS`, 0x0804): the pad is read from the
first flop. In the loopback above `spi_master` then runs at CLKDIV 1, 4 clocks
per SCK, like PIO (`test_uart_tx_and_spi_cycles`). PIO's IN samples at the
clock edge where it raises SCK, two clocks after SCK fell. protoemu's IN reads
the pad as it was a clock before the instruction, so the 2-instruction program
gives a device only one clock to answer. `spi_master_fast` spends a third
instruction to sample at the SCK rise, which restores the two clocks.
`test/model/test_spi_fast.py` sweeps the device's answer delay:

| Firmware, setting | Clocks per SCK | Device must answer within (after the SCK fall) |
|---|---|---|
| `spi_master`, CLKDIV 2 (g4) | 8 | 2 clocks |
| `spi_master`, CLKDIV 1 (g4) | 4 | never correct (one bit late) |
| `spi_master`, CLKDIV 1, bypass | 4 | 1 clock |
| `spi_master_fast`, CLKDIV 1 | 4 | 1 clock |
| `spi_master_fast`, CLKDIV 1, bypass | 4 | **2 clocks** (40 ns at 50 MHz, less pad and board delays, below) |

The RTL test `test_spi_master_fast` runs the last row through the real host
port against `test_spi_master`'s device model, which answers a clock after it
sees SCK fall: 5 bytes back to back at 4 clocks per SCK, all correct. Without
the bypass, or with `spi_master` at CLKDIV 1 even with it, every MISO bit
arrives one bit late (0xC3 read as 0xE1). PIO keeps the absolute-rate
advantage of its faster clock (33 against 12.5 MHz).

The "2 clocks" are a budget, not a margin: t_out (clock to SCK at the pad) +
SCK board flight + the device's SCK-fall-to-MISO delay + MISO board flight +
t_in (pad to the first synchroniser flop) + setup must stay under 40 ns at
50 MHz. Hold is safe (MISO changes only at the next SCK fall). The simulation
has zero pad and board delays, and the Tiny Tapeout I/O path (pad plus the
shared input/output multiplexers) is not characterised here, so 12.5 MHz with
a real device is unmeasured; a device with a 10 ns clock-to-out leaves about
30 ns for the chip's pads and the board. PIO's own budget is the same two
clocks, at 133 MHz: 15 ns.

## I2C master

### Size and division of labour

| | PIO `i2c` + `pio_i2c.c` | protoemu `i2c_master` |
|---|---|---|
| Engine instructions / bytes | 18 / 36 (56 % of a PIO block's 32 shared slots) | 90 / 360 (35 % of one core's bank) |
| START / STOP / RSTART | the CPU sends an escape word plus 3, 3 or 5 instruction words, run by `out exec` | one command word each, run in the engine |
| ACK of written bytes | NACK on a non-final byte halts the SM | ACK bit returned for every byte |
| Clock stretching | `wait 1 pin, 1`, no limit | every SCL release waits with a timeout (R3, default 2000 ticks) |
| What the timeouts cost | — | 20 instructions: `ldi r3` + `jmp t` at each of the 6 SCL-release WAITs, plus an 8-instruction handler |
| SDA read back where the master releases it | — | 19 instructions (71 to 90, `fault-fixes`): before START, after STOP, each written bit, a READ's NACK; a low read-back replies 0x200 and sets F1 ([fault-testing.md](fault-testing.md), finding 2) |

The 90 instructions would not fit in a PIO block. That is the point of 256
private words: this I2C master and the 130-instruction USB device both need
more than 32.

### Host traffic and bus time

Register write is 3 data bytes. Register read is 2 bytes after a repeated
START. Both engines run against the same target model at 100 kHz.

| | PIO: FIFO writes / reads / control writes | protoemu: FIFO writes / reads | PIO bus time | protoemu bus time |
|---|---|---|---|---|
| Register write | 13 / 0 / 2 | 7 / 5 | 463 µs | 575 µs (519 before the SDA read-back) |
| Register read | 19 / 3 / 3 | 8 / 5 | 478 µs | 601 µs (560 before) |

* **Host accesses.** protoemu needs about half as many host FIFO accesses and
  no control-register writes. pio_i2c toggles autopush and clears FDEBUG.
* **Bytes.** PIO's FIFO writes are 16 bits and protoemu's words are 32, so
  bytes moved are similar: 50 against 52 for the read. protoemu's words also
  cross an external SPI link, while PIO's use the on-chip bus. The defensible
  claim is fewer host interactions and no instruction-level choreography by the
  CPU, not less data.
* **Bus time.** PIO is faster on the bus: 287 SM clocks (89.7 µs) per byte
  against protoemu's 89 ticks (111 µs; 84 and 105 before `fault-fixes` added
  the SDA read-back, which costs a WRITE 9 ticks and a READ 3). protoemu decodes a command word before
  each byte (`pull`, `mov`, `and`, compares), and that costs about 1.5 bit
  times.
* **SCL timing.** Measured inside the transfers: protoemu holds SCL 5.00 µs
  high and at least 5.00 µs low. pico-examples holds it 5.00 µs high, but low
  for only 15 SM clocks = 4.69 µs between bytes (`jmp pin [2]`, `out`, `out`,
  `jmp`, `set`, `out [7]`). UM10204's Standard-mode minimum is t_LOW = 4.7
  µs[^i2c]. The shortfall is 0.3 %, harmless in practice, but it is a measured
  difference.

### Faults

**SCL held low (a stuck or long-stretching target).** The target holds SCL low
from the falling edge before the 14th SCL pulse, which is in the register byte
of a register write.

* **protoemu.** The master releases SCL, and the WAIT gives up after
  (TMO + 1) ticks. The host gets 0x100 instead of an ACK, 2.51 ms after the
  release, and F1 is set. The data byte and STOP behind it time out the same
  way, and the core is back at `pull block` with an empty FIFO 7.56 ms after
  the release. When the target lets go, the next transaction works (measured).
  [test_i2c_timeout.py](../test/model/test_i2c_timeout.py) repeats this at
  every one of the 19 SCL releases of a START/WRITE/READ/STOP transaction.
* **PIO.** After 100 ms of simulated busy-waiting:
  * the SM is stalled at `wait 1 pin, 1 [4]`, with no IRQ flag and FDEBUG.TXSTALL clear;
  * the CPU is still inside `pio_i2c_write_blocking`, spinning in
    `pio_i2c_stop`'s FIFO-full loop;
  * `pio_i2c_wait_idle` exits only on TXSTALL or the IRQ, so it would not exit
    either.

  When the target releases SCL, the transfer simply continues and completes
  (measured). This is legal I2C. Recovery from a target that never lets go
  needs a CPU-side timer, which `pio_i2c.c` doesn't have. The CPU then has to
  drain the FIFO, force a JMP through `SMx_INSTR` (datasheet §3.5.7: a forced
  instruction executes immediately, replacing the stalled one) and clear the
  IRQ.
* **Why the difference is architectural.** PIO's WAIT is "stall until some
  condition is met", with only polarity, source and index fields (datasheet
  §3.4.3). Stalled instructions retry every cycle with no limit (§3.2.4). A
  bounded wait therefore can't be one PIO instruction. A polling loop with a
  counter is possible, but it gives up X or Y and the 32 shared slots, and that
  cost is an inference, not measured here. protoemu's WAIT carries the timeout
  in its encoding ([isa.md §5.2](isa.md)).
* **The trade-off, honestly.** I2C itself allows a target to stretch SCL for
  any time. protoemu's default TMO of 2000 ticks is 2.5 ms at 100 kHz, so a
  legitimately longer stretch is reported as an error where PIO would wait it
  out. SMBus defines t_TIMEOUT as 25–35 ms[^smbus]. TMO is a `.define` and R3
  is 16 bits: up to 65 535 ticks, 82 ms at 100 kHz. TMO = 20 000 gives 25 ms.

**Address NACK** (nobody at 0x51; three data bytes queued behind it).

* **PIO.** The SM sees NAK on a non-final byte and stops at `irq wait 0 rel`.
  `write_blocking` notices the IRQ, runs `pio_i2c_resume_after_error` and sends
  STOP: 5 control writes in all. Only the address byte reaches the bus, and the
  call returns −1.
* **protoemu.** The host receives NACK for the address byte and for each
  following byte, because the master clocks every WRITE it was given. All 4
  bytes reach the bus.
* PIO's behaviour is the safer default. On protoemu the host must check a
  reply before queuing more bytes, or accept that bytes go to an absent target.

**SDA held low.** pico-examples reads the stuck line as ACK and returns
success. protoemu did the same (ACK, data 0x00) until `fault-fixes`; it now
reads SDA back wherever it releases it and replies 0x200 with F1 set
([fault-testing.md](fault-testing.md), finding 2). It compares a written byte
after the ACK, so it reports a lost arbitration but does not stop early on
one: it is still not a multi-master.
On both, the host can clear a target stuck mid-read the standard way, by
clocking 9 bits with SDA released and then STOP (protoemu: READ with NACK;
pio_i2c: a 0xFF read word with NAK set). That was not measured here.

## USB low speed

No like-for-like pair exists, so this section compares sources rather than
simulations.

* **Pico-PIO-USB.** The README lists "1 PIO, 3 state machines, 32
  instructions": `usb_tx_dpdm` 5, `usb_edge_detector` 17, `usb_nrzi_decoder` 10
  (pioasm counts in pio_reference.json). It supports a full-speed device and
  full- and low-speed hosts, but no low-speed device.
* **protoemu.** It implements a low-speed device only.

| | Pico-PIO-USB | protoemu `usb_ls_dev` (or `usb_ls_rx` + `usb_ls_tx`) |
|---|---|---|
| Engine instructions | 32 (3 SMs) | 130 on core 0 with the line unit; 143 + 56 on two cores without it |
| NRZI + bit stuffing, TX | CPU, `pio_usb_ll_encode_tx_data` (`pio_usb.c:459`) | line unit (or firmware) |
| Stuff-bit errors on RX | dropped "without error check" (`usb_rx.pio:103`) | detected; malformed stuffing tested |
| CRC | CPU (`update_usb_crc16`, `pio_usb.c:230`) | RX: CRC tap checks the residue in the engine; TX: CRC bytes staged by host software |
| Handshake turnaround | CPU busy-wait, "Timing critical" (`pio_usb.c:237-239`); device mode busy-waits inside the RX IRQ handler (`pio_usb_device.c:124`, `:303`) | engine sends ACK/NAK; host software over SPI tolerates 100–3000-clock service gaps (`test_usb_enum.py`) |
| Clock constraint | system clock a multiple of 12 MHz (examples set 120 MHz) | tested at a 48 MHz core clock, 32 clocks per bit |

protoemu's split moves all bit-time-critical work into the engine. It costs
4.1× the instructions of Pico-PIO-USB (130 against 32), but its device role also does more in the
engine. Pico-PIO-USB keeps the engine small and spends CPU time instead. Full
speed is out of reach for protoemu at 50 MHz with this firmware.

guide.md had listed the two-core variant as 138 + 48 instructions. The current
sources assemble to 143 + 56 (49 for usb_ls_tx until its SETUP flush and
got_in-style guard, branch `usb-ls-restage`; 56 since that flush also drops
an IN inside a SETUP, branch `usb-in-setup`); guide.md is corrected and the
tests check the counts (`test_pio_comparison.py`, `test_guide_sizes.py`).

## TI Sitara PRU, for reference

Nothing here is measured, so these are vendor facts only (AM335x data
sheet[^pru]):

* two 32-bit load/store RISC PRUs at 200 MHz, each with 8 KB of instruction
  RAM, which is 2048 32-bit instructions;
* 8 KB of data RAM each and 12 KB shared;
* a hardware UART up to 12 Mbps inside the subsystem.

A PRU is a general-purpose core, so a timeout is an ordinary counter in a
polling loop. It has far more program space and clock than
protoemu or PIO, and it lives inside a Cortex-A8 SoC. Against PRU, protoemu's
case is cost and size: two cores, FIFOs and SRAM in 0.45 mm² of 130 nm cells
and macros. The hardware edge capture (edges caught every clock even at a slow
tick) and the one-instruction qualified WAIT are ISA features, and they were not
benchmarked against PRU code.

## Area and hardware cost

* **protoemu, signed-off run g4** (`metrics.json`):
  * die 0.916 mm² (Tiny Tapeout 6×4, IHP SG13 130 nm);
  * 26 551 standard cells covering 0.354 mm²;
  * two 256×32 SRAM macros covering 0.099 mm²;
  * 44 % standard-cell utilization.

  This is the whole chip: two cores, FIFOs, host port, line unit and fill
  excluded. A core alone (pe_core + FIFOs) was about 116K µm² in the
  block-removal experiment in [physical.md](physical.md).
* **PIO.** No area figure is published. The datasheet says only that each
  state machine "occupies approximately the same silicon area as a standard
  serial interface block, such as an SPI or I2C controller" (§3.1), on a 40 nm
  process. No per-mm² comparison is made here.

## Where PIO wins

1. **Speed.** 133 MHz (200 MHz overvolted) on 40 nm against 50 MHz on 130 nm:
   2.7× the absolute rate for the same cycles per bit (UART), and finer edge
   placement (7.5 ns against 20 ns).
2. **Fast synchronous inputs.** On g4, PIO's input-synchroniser bypass gave
   4 clocks per SPI bit against protoemu's 8, about 33 MHz against 6.25 MHz
   SCK. v2's `SYNC_BYPASS` matches the 4 clocks (12.5 MHz at 50 MHz); the
   absolute rate is the clock's.
3. **Code density.** 16-bit instructions, and smaller programs where the PIO
   design pushes work to the CPU: I2C uses 36 B against 284 B, UART RX 18 B
   against 52 B.
4. **I2C bus efficiency.** 15 % less bus time per byte, and no command decode
   between bytes.
5. **Host link.** An on-chip bus and DMA against an external SPI link (193
   Mbit/s, simulated and FPGA-measured, not yet on a board).
6. **Engine count.** 8 state machines against 2 cores.
7. **Safer defaults in two cases.** UART RX drops a badly framed byte instead
   of delivering it, and I2C stops by itself on an unexpected NACK.
8. **No false timeouts.** PIO waits out any legal clock stretch. protoemu's
   default 2.5 ms limit is shorter than SMBus's 25 ms.
9. **Scope and maturity.** Shipping silicon, and full-speed USB on it
   (Pico-PIO-USB). protoemu's full-speed USB device (`usb_fs_dev`, `usb_enum`)
   runs in RTL and gate-level simulation and in FPGA fabric, not on silicon.

## Defensible claims for protoemu

1. **The I2C master can't hang, without host help.** Measured: with SCL held
   low, every queued command ends within (TMO + 1) ticks, which is 2.5 ms per
   command at 100 kHz. The host sees an error reply and the core returns to
   idle. Under the same fault, pico-examples' SM stalls indefinitely and its
   blocking call doesn't return (100 ms simulated). PIO's WAIT has no timeout
   field (datasheet §3.4.3), so only the CPU can bound the wait.
2. **Protocol control stays in the engine.**
   * I2C START/STOP/repeated START and per-byte ACK need one command word each.
     A register read takes 13 host FIFO accesses against 25 FIFO and control
     accesses for pico-examples.
   * USB low-speed handshakes, CRC checking and stuff-error detection run in
     the engine. Pico-PIO-USB does these on the CPU with timing-critical busy
     waits.
3. **Room for the program that does this.** 256 private words per core: the
   90-instruction I2C master and the 130-instruction USB device each exceed a
   PIO block's 32 shared slots.

A smaller, measured point: protoemu's UART receiver takes back-to-back frames
from a sender 3.5 % fast (expected limit 3.75 %), where pico-examples fails at 3 %
(limit 2.5 %).
That comes from firmware structure, not the ISA (the transliterated PIO
program behaves like PIO).

## Sources

[^ds]: Raspberry Pi Ltd, *RP2040 Datasheet*, build-date 2025-02-20 (build
    3184e62), <https://datasheets.raspberrypi.com/rp2040/rp2040-datasheet.pdf>.
    §2.15.3 (clk_sys 133 MHz, 200 MHz at 1.15 V), §3.1, §3.2.1 ("32-slot
    instruction memory"), §3.2.4, §3.4.3, §3.5.4, §3.5.5, §3.5.6.3, §3.5.7,
    §3.6.7, FDEBUG.TXSTALL and CTRL.SM_RESTART register descriptions.
[^i2c]: NXP, *UM10204 I2C-bus specification and user manual*, Rev. 7.0
    (2021), Table 10: Standard-mode t_LOW ≥ 4.7 µs, t_HIGH ≥ 4.0 µs.
[^smbus]: SBS-IF, *System Management Bus Specification* v3.1 (2018),
    <https://smbus.org/specs/SMBus_3_1_20180319.pdf>: t_TIMEOUT 25–35 ms.
[^pru]: Texas Instruments, *AM335x Sitara Processors* data sheet, SPRS717L
    (rev. March 2020), <https://www.ti.com/lit/ds/symlink/am3358.pdf>, PRU-ICSS
    feature list.

PIO program sources, at the commits recorded with their sha256 in
`test/model/pio_reference.json`:

* <https://github.com/raspberrypi/pico-examples/blob/0d62f75bafc2c8120d3276c3343d1a9195e909e9/pio/uart_tx/uart_tx.pio>
* <https://github.com/raspberrypi/pico-examples/blob/0d62f75bafc2c8120d3276c3343d1a9195e909e9/pio/uart_rx/uart_rx.pio>
* <https://github.com/raspberrypi/pico-examples/blob/0d62f75bafc2c8120d3276c3343d1a9195e909e9/pio/spi/spi.pio>
  (and `pio_spi.c`, `spi_loopback.c`: clkdiv 1)
* <https://github.com/raspberrypi/pico-examples/blob/0d62f75bafc2c8120d3276c3343d1a9195e909e9/pio/i2c/i2c.pio>
  and `pio/i2c/pio_i2c.c`
* <https://github.com/sekigon-gonnoc/Pico-PIO-USB/blob/5a37a66dc5d3fbe0ef3cdbeda923a757440f984f/src/usb_rx.pio>,
  `src/usb_tx.pio`, `src/pio_usb.c`, `README.md`
* pioasm: <https://github.com/raspberrypi/pico-sdk/tree/079c6f39023649b154152db30f1d781e884879bc/tools/pioasm>
