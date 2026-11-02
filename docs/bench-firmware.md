# Bench firmware: MDIO, a QSFP-style stand-in, PMBus

Three additions that use the chip as a tool on a bring-up bench, with the RTL
frozen: nothing under `src/` or `flow/` changed, and neither did
`tools/pe/{sim,isa,asm,line,timing}.py`, `firmware/i2c_device.pasm` or
`firmware/smbus_host.pasm`. They follow the method of
[post-freeze-protocols.md](post-freeze-protocols.md): static timing checker,
pins-only models, sigrok where a decoder exists, an RTL test through the SPI
host port.

**Evidence level of everything here: cycle model and RTL simulation.** None
of it has run at gate level, on an FPGA, on silicon, or against a real PHY,
module or power device. The models and the firmware have one author; sigrok's
decoders are the only third-party judge, and they check frames, not limits.

| Addition | Files | Size | Evidence |
|---|---|---|---|
| MDIO master and PHY stand-in | `firmware/mdio_master.pasm`, `firmware/mdio_phy.pasm`, `tools/pe/mdio.py` | 32 and 124 of 256 words | model; RTL through SPI at 50 MHz; sigrok `mdio` on both |
| QSFP-style stand-in | `tools/pe/qsfpdev.py` on `i2c_device`, `firmware/i2c_device.timing` | no new program | model; RTL through SPI at 50 MHz; sigrok `i2c` on both |
| PMBus reads with PEC | `tools/pe/pmbus.py` on `smbus_host` | no new program | model; RTL through SPI at 3.571 MHz, tick for tick 50 MHz; sigrok `i2c` on both, PEC recomputed |

## Why these are bench uses

A board with an Ethernet PHY, a pluggable cage or a digitally managed power
rail is brought up over slow management buses, usually before the software
that normally drives them exists. These programs do one of two things there.
As a master, the chip gets at a device from a host over SPI: read a PHY's ID
registers to find out whether it is alive and strapped to the expected
address, read a rail's voltage and temperature with a checksum that says the
reading arrived intact. As a stand-in, it takes the place of a device that is
absent, expensive or not yet working, so that the other end's management code
can be exercised: a MAC's MDIO driver against the PHY stand-in, a host's
module-management path against the QSFP stand-in.

The fit with any company's needs is inference. Nobody has asked for these,
and no user, lab or vendor has tried them.

## MDIO

**What it does.** `mdio_master` runs IEEE 802.3 Clause 22 frames: 32 preamble
ones, ST 01, OP, PHYAD, REGAD, TA, 16 data bits, then one idle MDC clock with
MDIO released. The host pushes the 32 bits after the preamble as one word
(`pe.mdio.frame_word`) and gets one record back. On a read the firmware lets
go of MDIO on the falling MDC edge after A0, and the record carries the second
turnaround bit, which tells a PHY that answered (0) from an empty address (1).

`mdio_phy` stands in for a PHY's register set. MDIO has no clock stretching: a
read must be on the wire two MDC periods after REGAD, too soon for a host
round trip, so the registers live in the core. 29 are constants in a jump table
in instruction memory, fixed when the program is assembled. Three are live: R2
is control (register 0, read/write), R3 is status (register 1, read-only to
the STA, written by the host), and the CRC register with no taps is
advertising (register 4, read/write). The host is told of every write frame
addressed to it through the RX FIFO.

**The turnaround.** Both directions are checked on a wire model that counts
every instant two drivers overlap, and all the tests below end with that count
at 0, over 200 random frames between the two firmwares too.
`test_broken_master_keeps_driving_through_turnaround` shows the count rising
when the release is removed.

| Moment, read frame | Master | PHY | Tested at 50 MHz |
|---|---|---|---|
| A0 rising edge | drives | off | `test_master_turnaround_who_drives` |
| falling edge after it | releases, 200 ns before TA1 | off | exactly 200 ns |
| TA1 rising edge | off | off | nobody on the wire, the pull-up reads 1 |
| 80 ns after it | off | stand-in drives 0 | `test_phy_turnaround_who_drives`, exactly 4 clocks on the synchronous model; 100 ns on the RTL test, which has a 1-clock wire |
| TA2 to D0 rising edges | off | drives | every edge |
| 80 ns after D0's rising edge | off | stand-in releases | exactly 4 clocks |

On a write the master drives TA as 1, 0 and all the data, and releases on the
falling edge after D0.

**Timing against the standard.** IEEE 802.3 itself was not available when this
was written. The Clause 22 numbers are from public secondary sources:
Wikipedia's *Management Data Input/Output* article and web search summaries of
an IEEE 802.3 ad hoc slide deck (`perezaranda_NGAUTOah_190417`, citing
22.2.2.13 and 22.3.4) and vendor documents. *From memory, not checked against
any source:* "a PHY shall observe 32 contiguous ones before it responds", MDC
having no maximum high or low time beyond what the summaries say, and the
meaning of control bits 15 (reset) and 9 (restart auto-negotiation).

| Parameter | 802.3 (as above) | `mdio_master`, T = 20, CLKDIV 1, 50 MHz | Test |
|---|---|---|---|
| MDC high, low | >= 160 ns | exactly 200 ns each, every bit of every frame | `test_master_read_and_write_exact_waveform`; checker 10 ticks |
| MDC period | >= 400 ns | exactly 400 ns, **no margin**: a clock above 50 MHz breaks the minimum | same; RTL `bench_mdio_master_and_phy`; checker 20 ticks |
| MDIO setup, hold from the STA | >= 10 ns | exactly 200 ns each | checker 10 ticks each; `test_master_preamble_and_setup_hold` |
| PHY clock to output | 0..300 ns | reads correctly with a PHY at 0..340 ns on the 20 ns grid; wrong at 360 ns | `test_master_phy_clock_to_output`; RTL at 20 and 300 ns |

The master samples MDIO 2 clocks (40 ns) before MDC rises at the pad: `in pins,
1 side 1` raises MDC in the instruction that reads a pin through a two-flop
synchroniser. So 60 ns remain beyond the standard's 300 ns for the board and
the pads, whose delays no test here includes.

| Parameter | 802.3 | `mdio_phy`, CLKDIV 1, 50 MHz | Test |
|---|---|---|---|
| Clock to output | 0..300 ns | 80 ns on the synchronous model, every driven edge; at most 100 ns with an asynchronous MDC (checker's `adjust`, an assumption, not a measurement) | `test_phy_clock_to_output_exact` |
| STA hold | >= 10 ns | **needs 40 ns**: 40 ns works, 20 ns does not | `test_phy_sta_hold_limit` |
| STA setup | >= 10 ns | none needed; the bit may appear up to 20 ns after MDC rises | `test_phy_sta_setup_limit` |
| MDC period, read | >= 400 ns | 400, 380 and 360 ns work, 340 and 320 ns do not: REGAD to the wait on TA1 is 17 ticks | `test_phy_fastest_mdc`; 17 measured on 31 registers, 14 on register 0, `test_phy_sound` |

The hold is the limit that matters. The stand-in reads MDIO in the instruction
after the WAIT on MDC, 1 to 2 clocks after MDC's edge at the pad, so an STA
that really holds MDIO for only the standard's 10 ns after the rising edge is
**not** served at 50 MHz. An STA that changes MDIO on the falling edge, as
`mdio_master` does, is. 802.3's 20 ns window around the edge is as wide as one
clock at 50 MHz, so a sampler behind a synchroniser cannot be certain of
hitting it whichever instruction it uses.

**Static checker.** `mdc_hi`, `mdc_lo`, `mdc_per`, `su`, `hd`, `turn` and `ta1`
on the master, `c2o`, `wc2o`, `dec`, `sendup` and `wrup` on the stand-in, with
exact bounds asserted by `test_deadlines_pass_with_exact_bounds` and each
measured on the model inside its bound. The checker does not follow the
stand-in's table jump, so the budget is split around it,
`test_phy_table_entries_are_uniform` checks the 32 entries' form, and the sum
is measured. A redundant `jmp !r1, table` exists only so that the checker
reaches `send`; register 0 takes that path, which is why it is 3 ticks shorter.

**sigrok** (`mdio` from libsigrokdecode 0.5.3). On the RTL at 50 MHz, master
and stand-in: all 8 frames with PHYAD, REGAD and data equal to what was sent,
and the frame to an empty address flagged `TA invalid (bit2)`, as it should be.
The decoder is a frame check only: it looks at MDIO on rising MDC edges and
measures no timing. It also runs in `test_sigrok_mdio_master`,
`test_sigrok_mdio_master_and_phy` and `test_sigrok_flags_broken_frames`.

**Negative tests.** Master: no PHY (TA2 reads 1, data 0xFFFF), the release
removed, a 31-bit preamble refused by the PHY model. Stand-in: 31 ones (no
answer, MDIO never driven), a 0 in a run of ones, ST 00, ST 11, OP 00, OP 11,
every other PHYAD (31 addresses), writes with TA 00, 01 and 11 (not applied,
reported), writes to read-only registers (ignored, reported), STA hold and
setup past the limits, MDC too fast, 10 unread write records.

**Limits.**

* Clause 22 only. `pe.mdio` builds no Clause 45 word and the stand-in ignores
  ST 00; no Clause 45 frame was tested on the master.
* `T` must be even. Tested: T = 20, 24, 40 at CLKDIV 1 and T = 20 at CLKDIV 2
  and 25 (800 ns and 10 us). Below 400 ns is outside 802.3 and untested.
* A frame is one host word and one record: no scanning and no polling in
  firmware. A full RX FIFO holds MDC low between frames, losing nothing
  (`test_master_stalled_host_only_delays_frames`).
* A write has no acknowledgement on the wire; only a read shows a PHY exists.
* MDIO is driven push-pull, not open drain, and needs a pull-up on the board.
  Pad levels, drive and speed are not examined here.
* Stand-in, one PHY address, not address 0's broadcast role; no preamble
  suppression.
* Stand-in registers: no latching status bits, no reset behaviour beyond bits
  15 and 9 of control reading back 0, no auto-negotiation, no link. It has no
  line side. Status changes are host writes to R3, tested between frames.
* Stand-in, constants: changing one means reassembling and reloading, with the
  core stopped.
* The stand-in has no timeout: if an STA stops in the middle of a read, MDIO
  stays driven until the frame's remaining MDC edges arrive (from the code;
  not tested).
* Its write records are pushed without blocking: more than 8 unread are lost,
  and the writes still apply (`test_phy_record_fifo_full_drops_records_not_writes`).
* `BASE` must equal the load address: tested on core 1 at 256 only. The two
  programs cannot share a pad (pin ownership), so together they need two MDIO
  pads joined outside the chip, as the tests do. MDC tested on `uo0` and
  `uio2` as an output, on `ui0` as an input.

## QSFP-style stand-in

**What it is, and is not.** `pe.qsfpdev.QsfpDevice` is a stand-in, not a
compliant module. It is a host library behind the existing `i2c_device`
firmware, which was not edited: the core runs the I2C bus, the host holds an
SFF-8636-style map, and an I2C master reads it at 0x50. It exists so that a
host's module-management code can be brought up without a module.

Layout, formats and behaviour are from SFF-8636 Rev 2.9 (2017, the published
text was read for this; section numbers below are its). The identifier codes
0Dh and 11h are SFF-8024's, taken from a search summary of Linux's
`include/linux/sfp.h`; SFF-8024 was not read.

| Page | Bytes | Field | Behaviour |
|---|---|---|---|
| lower | 0 | Identifier | 11h QSFP28 by default, or as given |
| lower | 1 | Revision compliance | 08h |
| lower | 2 | Status | IntL bit 1, Flat_mem 0, Data_Not_Ready as the host sets it |
| lower | 22-23 | Temperature | signed 1/256 C, MSB first, set by the host |
| lower | 26-27 | Supply voltage | unsigned 100 uV, MSB first, set by the host |
| lower | 86 | Tx disable | bits 3-0 read/write, bits 7-4 read 0 |
| lower | 127 | Page select | read/write; 00h and 03h supported |
| 00h | 128 | Identifier | same as byte 0 |
| 00h | 148-163 | Vendor name | ASCII, space padded |
| 00h | 165-167 | Vendor OUI | as given |
| 00h | 168-183, 184-185 | Part number, revision | ASCII |
| 00h | 191 | CC_BASE | low 8 bits of the sum of 128..190 |
| 00h | 196-211, 212-219 | Serial number, date code | ASCII |
| 00h | 223 | CC_EXT | low 8 bits of the sum of 192..222 |
| 03h | 128-135 | Temperature alarms and warnings | 75, -5, 70, 0 C |
| 03h | 144-151 | Supply alarms and warnings | 3.63, 2.97, 3.465, 3.135 V |

**Every other byte reads 0 and ignores writes**
(`test_everything_else_reads_zero`, on the lower page and upper page 00h, with
the list typed separately from the library's). That alone is non-compliant:
SFF-8636 (6.1) does not allow 0 for "unspecified" in informative ID fields.

**Behaviour taken from the specification, each tested on the model:**
device address 1010000x, others NACKed; the address counter rolls over inside
its 128-byte page, 127 to 0 and 255 to 128 (5.3.1), and persists between
operations; an unsupported page value (01h, 02h, 04h, 20h, 21h, 22h, 7Fh, 80h,
FFh) reverts byte 127 to 00h and operations go to upper page 00h (6.1, 6.2.11);
a write takes effect at STOP, and a repeated START in its place discards it
(5.3.2); a two-byte read is coherent (6.2.4): a temperature change between
MSB and LSB, across a carry, gives the old value whole and then the new one.

**Clock stretching.** SFF-8636's limit is 500 us (Table 5-1, T_clock_hold).
Each read byte is a host round trip under stretching, so the hold is the
host's business, not the firmware's. On the model the hold is exactly the
host's latency + 23 clocks (`test_clock_hold_follows_the_host`: 100, 1000, 10000
and 24900 clocks, the last 24923 clocks = 498.46 us at 50 MHz); with a
same-clock host exactly 26 clocks. The firmware cannot enforce the limit:
`test_slow_host_breaks_the_clock_hold_limit` holds SCL 600.46 us. On the RTL test
through the SPI port at SCK = clk/8, with one polling loop, the longest
hold was under 500 us (`bench_qsfp` asserts it and logs the figure). A real
host's latency is unmeasured.

**Reader.** The tests read with the pins-only `pe.i2cbus.Master` at 396.8 kHz
(half period 63 clocks), with offsets typed as literals from the
specification's tables, not taken from the library. sigrok `i2c` decodes the
identifier, vendor-name and temperature reads with no warning.

**Static checker.** `firmware/i2c_device.timing`, a sidecar, adds two deadlines
without touching the program: a read byte's MSB is on SDA exactly 16 ticks
before SCL is released, and the stretch lasts at least 22 ticks.

**Limits.** No ModSelL, ResetL, LPMode, IntL or ModPrsL pin. No clear-on-read
flags (bytes 3-21) and no channel monitors. Monitors are what the host set;
nothing is measured. No write cycle: tWR and the NACK while it runs are not
modelled. Sequential writes longer than the 4 bytes the specification requires
are accepted. Writes to upper pages are ignored, including page 03h's
read/write bytes. tBUF (20 us) is the master's to keep; the stand-in does not
check it. Pages 01h and 02h are unsupported, which SFF-8636 allows. `i2c_device`
ACKs every byte, and pushes STOP without blocking: with its RX FIFO full a
STOP is dropped, and the write it would have completed is lost. Page select
and Tx disable are the only writable bytes. fSCL was tested at 396.8 kHz only.

## PMBus reads with PEC

**What it does.** `pe.pmbus` builds Read Word and Read Byte transactions with
PEC for the existing `smbus_host` firmware, which was not edited, and decodes
the results: LINEAR11 (`linear11`, exact, as a `Fraction`), and READ_VOUT as
unsigned LINEAR16 with VOUT_MODE's exponent (`linear16`). The firmware's CRC
tap checks the PEC on the wire. `parse_word` and `parse_byte` raise `BadPec`
instead of returning a value that failed it. They raise `PmbusError` for
anything else that is not a good read.

The specification was not read. LINEAR11 and LINEAR16 are from vendor
application notes found by web search; command codes 88h, 8Bh, 8Dh and 20h
from a search summary of Linux's `drivers/hwmon/pmbus/pmbus.h`, 8Ch from one
more search result. *From memory:* linear mode being VOUT_MODE's top 3 bits
000.

**Tests against a pins-only device** (`pmbus.PmbusDevice`, which extends the
post-freeze `SmbusDevice`: it sees SCL and SDA and computes its own PEC):

* READ_TEMPERATURE_1 0xE32A = 50.625, READ_VIN 0xDB12 = 24.5625, VOUT_MODE
  0x17 and READ_VOUT 0x3333 = 6.3994... V (exactly 0x3333 / 512), each with PEC,
  on the model. Temperature and VIN also on the RTL.
* Bad PEC: each of the 24 bits of the two data bytes and the PEC flipped on the
  wire in turn: the firmware reports `pec_bad`, the library raises `BadPec`,
  and the next read is right. One data bit also on the RTL. The unflipped PEC
  is compared with a CRC-8 worked out in the test. A device whose PEC leaves
  out the address bytes, and a device with no PEC (the pull-up's 0xFF read as
  the PEC): rejected.
* LINEAR11 on boundary values: both limits of the 11-bit mantissa at exponents
  0, 15, -16, -1, the smallest positive value 2^-16, the extremes -33554432
  and 33521664, zero with several exponents, 0xFFFF = -0.5, and a published
  vendor example 0xDB12 = 24.5625. All 65536 words against a second integer
  computation, and through `to_linear11` and back. The corners also go over the
  wire with PEC.

**sigrok** (`i2c`): S, 0x40 W, the command, Sr, 0x40 R, low byte, high byte,
PEC with NACK, P. The PEC recomputed over sigrok's bytes matches on good
reads and fails on the flipped one, on the model and the RTL. sigrok has no
PMBus decoder.

**Timing.** `smbus_host`'s, as in
[post-freeze-protocols.md](post-freeze-protocols.md): SCL 89..99 kHz at 50 MHz
and its checker deadlines, unchanged.

**Limits.** Reads only: no writes, PAGE, QUERY, block commands or
manufacturer commands, and no ALERT# or CONTROL pin. No DIRECT format, and
LINEAR16 only in VOUT_MODE's linear mode. The value is decoded as sent;
calibration and units are the device's. PEC is a CRC-8: a corruption can still
pass it. Only single-bit flips were tried, and a device with no PEC gets
through whenever 0xFF happens to be the right PEC, about 1 read in 256 for
random data.

## Tests

| What | Where | Count |
|---|---|---|
| MDIO, model | `test/model/test_mdio.py` | TEST_MDIO tests |
| QSFP-style stand-in, model | `test/model/test_qsfpdev.py` | TEST_QSFP tests |
| PMBus, model | `test/model/test_pmbus.py` | TEST_PMBUS tests |
| RTL through the SPI port | `test/test_bench_fw.py` | 5 tests |
| Mutants, one bug each | `test/mutants.py`, names `fw-mdio-*`, `qsfp-*`, `pmbus-*`, `fw-smbus-pec-not-checked` | 28, all killed |

```
PYTHONPATH=tools python -m pe.timing firmware/mdio_master.pasm -D MDIO=0 -D MDC=8 -D T=20 -D PRE=32
PYTHONPATH=tools python -m pe.timing firmware/mdio_phy.pasm --defs pe.mdio:phy_timing_defines
PYTHONPATH=tools python -m pe.timing firmware/i2c_device.pasm -D SDA=0 -D SCL=1 --timing firmware/i2c_device.timing
PYTHONPATH=tools python -m pytest test/model/test_mdio.py test/model/test_qsfpdev.py test/model/test_pmbus.py
cd test && make COCOTB_TEST_MODULES=test_bench_fw
python3 test/mutants.py fw-mdio-phy-31-ones qsfp-tears-a-read pmbus-byte-order   # any names; none = all
```

The sigrok tests need `sigrok-cli` and skip without it.
