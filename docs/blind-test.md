# Blind protocol test

Can an agent add protocols it was not prepared for, on the frozen chip?

**Conditions.** The agent used the public docs (`docs/isa.md`, `docs/asm.md`,
`docs/guide.md`), public datasheets and specifications, and the existing firmware,
tools and tests as examples. It did not change the assembler, model, tools or RTL.
"Blind" here means *not prepared for this protocol*. It does not mean a stranger:
the agent is the same model that has worked on this kind of task, and it knew
what kind of chip and tests to expect. What it read is listed under "What the docs
did not say".

Evidence level throughout: **cycle model and RTL simulation**. Nothing here ran
at gate level, on an FPGA or against a real LCD or disk, and no electrical
property was examined.

## The draw

The rule: sort the candidates, take sha256("wirelingo blind test 61af9b2") as a
number, and index the list by the value mod 19 and by (value >> 16) mod 19. The
hash is `fe43bc640eb56e8caf084802074f23fc1cb58e754f8027188a74f3874274bbff`.

| # | Candidate | # | Candidate |
|---|---|---|---|
| 0 | AS-Interface | 10 | MicroWire EEPROM |
| **1** | **ATA PIO** | 11 | ModBus ASCII |
| 2 | BiSS-C | 12 | PSI5 |
| 3 | DMX RDM | 13 | Profibus DP frame |
| 4 | HART | 14 | SDI-12 |
| **5** | **HD44780 4-bit** | 15 | SENT SAE J2716 |
| 6 | Hall-sensor SPC | 16 | Sony SIRC IR |
| 7 | KNX TP1 | 17 | TM1637 display |
| 8 | M-Bus EN 13757-2 | 18 | nRF24 SPI framing |
| 9 | MAX6675 thermocouple read | | |

The value mod 19 is 5 and (value >> 16) mod 19 is 1: HD44780 4-bit (the LCD write
and busy-flag read) and ATA PIO.

**What the draw does and does not show.** The list was written by the orchestrator,
an AI agent, not an outsider. It started with 20 entries; SWIM (the STM8 debug
link) was removed before the draw because the project keeps debug-link work
parked. The list was made and drawn in one step on 2026-11-02 with no independent
timestamp, so **that it was fixed in advance cannot be proved**. The arithmetic
can be checked; the list's independence cannot.

## Verdicts

| Protocol | Verdict | Program | Reasoned limit |
|---|---|---|---|
| HD44780 4-bit, write and busy-flag read | **Fits** | `firmware/hd44780_4bit.pasm`, 99 words, one core | Assumes a 3.3 V LCD; see Open |
| ATA PIO | **Does not fit as specified. A working subset exists**: an 8-bit-wide host, polled, PIO mode 0 and faster builds for modes 1 to 4 | `firmware/ata_pio.pasm`, 100 words, one core | No 16-bit Data register, so no ordinary ATA disk's sectors |

### Why ATA PIO does not fit

ATA's bus needs DD15:0 bidirectional (16), DA2:0 (3), CS0-, CS1-, DIOR-, DIOW-
(4), RESET- (1), and inputs IORDY and INTRQ: at least 24 lines before DMA. This
chip has 8 bidirectional pads (uio), 7 output-only (uo[6:0]) and 5 input-only
(ui[4:0]) once the SPI port has its 4 (isa.md §2). Sixteen bidirectional lines
cannot be had: the output-only pads cannot be released, and there are 8 fewer
bidirectional pads than ATA's data bus alone needs.

What this firmware uses: all 8 bidirectional pads on DD7:0, all 7 outputs on
DA2:0, CS0-, CS1-, DIOR-, DIOW-, and IORDY on an input.

* **Do not fit:** DD15:8, and RESET-, since the output pads are full (software
  reset instead).
* **Left out by choice:** INTRQ. This firmware polls. 4 input pads stay free, so a
  design could wire it. DMARQ/DMACK- are unused.
* **Cost to the host port:** taking all of uo[6:0] includes uo[6:4], the quad
  port's data pads (isa.md §6.1), so with this firmware the host port runs 1-bit.
  That limits sector rate. No rate was measured; the tests run SPI at clk/8.

That gives a working subset, because ATA's task-file registers are 8 bits on DD7:0
(ATA-3, DD signal description) and the CFA feature set has an 8-bit PIO mode:

* **CFA device (CompactFlash in True IDE mode):** after SET FEATURES 01h, full
  512-byte sector reads and writes, IDENTIFY, status, software reset.
* **16-bit-only device:** register access and non-data commands work. SET
  FEATURES 01h is aborted. IDENTIFY returns the low byte of each word only, and
  the high bytes are gone (`ata_16bit_device_is_the_no_fit`). Sector data cannot
  be moved, and a write would leave DD15:8 undriven.

External latches on the 8-bit bus, as XT-IDE adapters use, are a possible way to
16 bits. That is glue logic on a board, untested and not claimed here.

### Faster PIO modes: what was tried

An earlier version of this report called mode 0 the "largest" subset. That was
wrong: an independent check got sector transfers at PIO mode 1 timing by removing
two padding delays. So each mode was then tried properly. The far end holds each
mode's table (ATA-3 Table 21, with t2 and mode 2's t0 as noted below) and enforces
it, including t2i. A device starts at mode 0, so the host runs the mode 0 build,
sends SET FEATURES 03h (ATA-3 Table 16, `00001nnn`), then loads the build for
that mode. The far end negates IORDY as late as tA allows.

| Mode | Build (`pio_defines`) | t0 | t2 | t2i | t3 | t4 | t9 | Spec t0 / t2 / t2i |
|---|---|---|---|---|---|---|---|---|
| 0 | TS 12 | 620 | 320 | 300 | 360 | 100 | 160 | 600 / 290 / - |
| 1 | TS 13 | 420 | 340 | 80 | 380 | 40 | 100 | 383 / 290 / - |
| 2 | TS 13, same build | 420 | 340 | 80 | 380 | 40 | 100 | 330 (memory) / 290 / - |
| 3 | TS 3 | 220 | 140 | 80 | 180 | 40 | 100 | 180 / 80 / 70 |
| 4 | TS 3, same build | 220 | 140 | 80 | 180 | 40 | 100 | 120 / 70 / 25 |

Figures are the smallest seen at the pads, in ns. t1 is 100 in all. Each mode
wrote a sector and read it back, and stayed correct with IORDY held 700 ns, on the
model (2 sectors) and on the RTL through SPI (1 sector).

What this does not say:

* **Mode 4's timing is met; its rate is not.** One fast build serves modes 3 and
  4. Its byte cycle is 220 ns reading and, by instruction count, 280 ns writing,
  against mode 4's 120 ns. The instruction overhead of a strobe sets that.
* **No maximum is claimed.** These builds were chosen to meet each table with
  margin, not to find the fastest possible. Mode 1's and 2's t2 at 290 ns is the
  secondary figure below, and it decides those builds.
* Static deadlines cover mode 0 only; the faster builds' figures are measured.
* WAIT's unit shrinks with the build, since it counts polls.
* That a real CF card accepts these modes in 8-bit mode is unverified.

## Pins and host protocol

Each firmware's header is the reference. HD44780: DB7..4 on four uio pads, RS,
RW and E on uo, tick 100 ns (CLKDIV 5 at 50 MHz). ATA: `MOV pins` sets 13 pins
at once, because P0-P7 (DD) and P8-P12 (DA, CS) are contiguous: one register
holds the whole bus image. DIOR-/DIOW- are side-set on P13/P14. Tick 20 ns.

## Timing figures and their sources

Measured values are the smallest seen at the pads by the far end. The same
exact figures are asserted on the model (`test_smallest_figures_seen_at_the_pads`)
and on the RTL through SPI (`hd_bus_figures_50mhz`, `ata_identify_read_write`).

HD44780U datasheet (Hitachi, copy at soldered.com, see
`test/model/hd44780_device.py`), Bus Timing tables, VCC 2.7-4.5 V, p.49. That
column is used because the pads are 3.3 V; it is the stricter one:

| Figure | Datasheet (ns) | Measured (ns) |
|---|---|---|
| tcycE | >= 1000 | 1200 |
| PWEH | >= 450 | 600 (write), 800 (read) |
| tAS | >= 60 | 300 |
| tAH | >= 20 | 300 |
| tDSW | >= 195 | 800 |
| tH | >= 10 | 400 |
| tDDR | <= 360 | sampled 660 at 50 MHz; 650 reads right, 670 does not (`hd_read_sample_point`) |

Figure 24 (p.46): > 40 ms, > 4.1 ms, > 100 us. Firmware: 44.5 ms measured to
the first write, then 15.0 ms, 411.7 us, 213.3 us, deliberately long: the 4.1 ms
and 100 us are stretched for a slow oscillator. Execution times 37 us and
1.52 ms: Table 6. tADD = 1.5 / fOSC: Figure 10.

ATA, [ATA-3 draft revision 6](https://www.pjrc.com/docs/specs/disk/ata3r6_10.html)
Table 21, PIO mode 0:

| Figure | Spec (ns) | Measured (ns) |
|---|---|---|
| t0 | >= 600 | 620 |
| t1 | >= 70 | 100 |
| t2, 8-bit | >= 290 (*) | 320 |
| t3 | >= 60 | 360 |
| t4 | >= 30 | 100 |
| t9 | >= 20 | 160 |
| t5, t2 - t5 = 240 | data valid by 240 | sampled 280; 270 reads right, 290 does not (`ata_read_sample_point`) |
| tB | <= 1250 | strobe follows IORDY to 1100 for 1000 held; gives up at 1760 and sets a flag |

(*) ATA-3's table has only the 16-bit t2 (165). 290 ns for 8-bit is from Linux's
`ataspec_t2_8`, seen as a source excerpt: **secondary**. Using the larger figure
is the safe direction.

Other sources: BSY within 400 ns of the Command write: ATA-3 6.2 and clause 8.
Software reset sequence and register signature: ATA-3 clause 9.2; SRST held 5 us:
its note 25. Invalid address with both chip selects: ATA-3 Table 6.

**8-bit mode rests on secondary sources.** SET FEATURES 01h/81h, CFA only: XTIDE
Universal BIOS `IdeRegisters.inc`. Only DD7:0, n*512 accesses: Hale Landis on the
T13 reflector, 2006-05-23. The CF specification itself could not be opened: the
copy found returned 404.

**From memory, not checked:** the 2 ms a host waits after clearing SRST; SRST
returning 8-bit mode to default (the conservative choice); DDRAM read needing an
address set first; pull-up MOS on DB pins; the 10 ms internal-reset busy period.
The first and last two are labelled in the far-end sources too.

## Evidence

* **Far ends** (`test/model/hd44780_device.py`, `ata_device.py`): pins-only,
  written before their firmware, from the sources above. On the RTL they react
  to pad changes and drive at the exact nanosecond of their table. During a read
  they drive the complement until data is valid, so an early sample fails.
* **Model tests**: `test/model/test_hd44780.py`, `test_ata_pio.py`, 56 tests:
  every byte value, warm restart in 8-bit, 4-bit and mid-nibble states, slow
  oscillators, BF stuck, BSY stuck, IORDY extended and stuck, RX full mid-sector,
  slow TX refill, refused blocks, seeded random stress.
* **RTL**: `test/test_blind.py`, 13 tests through the SPI port.
* **Static timing**: 8 HD44780 and 10 ATA (mode 0) deadlines pass (3 of ATA's are
  conditional, meaning they bound only the paths that reach the end point) and
  both firmwares' bounds are checked against model measurements.
* **Mutants**: `test/blind_mutants.py`, 29 one-line firmware bugs, 29 killed
  after a passing baseline. They run on the model tests, not on RTL.
* **Far-end teeth**: tests that a too-fast host is rejected, that an unpolled
  write is caught, and that a device outside the 400 ns window does fool the
  poll, so that test can fail.

### sigrok

sigrok-cli 0.7.2 on runner1 has **no hd44780 decoder and no ATA decoder**
(`--list-supported`). The generic `parallel` decoder was used as a weaker
check: it knows no protocol, so it only confirms the bus value at a strobe edge.
It read all HD44780 nibbles at E's fall, and every ATA write's byte and address at
DIOW-'s rise. ATA reads were not checked this way: at DIOR-'s fall the data is
not yet valid, and at its rise this decoder would need the sample before the edge.

## Wrong turns

1. Decoded `chip.outputs()` as logical pin words. It returns
   (uo, uio_out, uio_oe). No E pulses, all HD44780 tests failed. Harness error.
2. Scaled Figure 24's waits with fOSC in the far end, then found the firmware's
   minimum waits failed at a slow oscillator. Kept the model, lengthened the
   firmware.
3. Wrote "about 3.5 us" per poll. It is 38 ticks, 3.8 us, by count and by test.
4. Soundness test compared a CLKDIV 1 run with the CLKDIV 5 assumption.
5. **Shared scratchpad.** My `rt.sh` was overwritten by another agent working in
   the same scratch directory, so I ran its script once. It stopped at its failed
   `mv` and its files were intact afterwards; its own test run was going. My work
   moved to a private directory.
6. `parallel` emits an item at the next clock edge, so a capture's last word never
   appears. First read as a test bug.
7. ATA far end: `left` unset for IDENTIFY, so a finished IDENTIFY looked like a
   read with more sectors.
8. Predicted figures wrongly (poll 30 ticks, really 31; IORDY give-up 1720 ns,
   really 1760; word gap 640 ns, really 620 and 680). The tests were corrected to
   figures derived by counting and confirmed on both model and RTL, not simply to
   whatever was measured.
9. Wrote a 500 ns delay in ATA WAIT for the status window. Counting showed the
   path alone takes 540 ns. Removed the delay; a static deadline now holds it.
10. Concluded the timing checker "exploded" on one change. Wrong: timing each
    deadline showed one taking 87 s by wandering through the DELAY and drain
    loops. `avoid=` fixed it. Stopped jobs on a false diagnosis along the way.
11. Rig pushed FIFO words in a cycle the far end did not see, so t3 read 340 ns.
    Suspected the rig instead of accepting the number; RTL gave the 360 predicted.
12. Guessed `sim.A_FLAGS_SET`. It is `A_FSET`.
13. **Mutant survivor, real hole:** the ATA far end never checked that a strobe
    stayed low while IORDY was negated, so firmware ignoring IORDY on writes
    passed. Fixed in the far end.
14. **Mutant survivor, hidden by phase:** removing the tADD wait passed because
    BF always fell at the same poll phase. A sweep across oscillator speeds kills
    it. That review also showed tADD must scale with fOSC, and the firmware's 8 us
    wait was too short at 90 kHz: now 17 us.
15. An RTL test raced SPI latency against the 37 us execution time and failed
    after an unrelated change. The two commands now go in one burst.
16. The reset window (Open, first item) was invisible because the far end started
    after the host's first write. Found while writing the limits, not by a test.
17. The far end drove DD on an invalid address. Found by that new test. Fixed from
    Table 6.
18. t0 was exactly 600 ns on block writes, zero margin. One delay tick added.
19. The sigrok run at the code of that time failed in `sigrok.decode`, not in a check:
    the `sg-bench-fw` copy named in the brief was deleted from runner1 during the
    run, by something other than this work. Rerun with `bench-fw/sigrok`, version
    0.7.2 as well. The plain `remote_test.sh` run had skipped those branches
    silently, so 13/13 there did not show them.
20. **Found by the verifier, not by me.** "Largest subset" was false; the doc's
    first sentence oversold "blind"; the list was unpublished; the header said no
    pad was left for INTRQ; the times were called log points; the quad-port cost
    was missing; and three test gaps let mutants through: ERR ignored when DRQ is
    up, idling with CS0- low, polling Status instead of Alternate Status.
21. The model rig showed the far end's output at the start of a cycle, a clock
    before the edge that samples it. That made the read sample look like 260 ns
    against the RTL's 280, and it read a mode 2 device's status as garbage here (likely
    the mode 2 stall the verifier reported, not confirmed). Fixed in the rig;
    model and RTL now agree. Earlier, a separate window had been written down for
    the model instead of finding this.
22. Mode 3/4 first used TS 2, which sampled IORDY 5 ns past tA. Moved to TS 3.

## What the docs did not say

* asm.md points at `docs/timing-check.md`, which was outside this test's allowed
  list. The annotation syntax came from `tools/pe/timing.py`'s docstring.
* No allowed doc says how to write a far end or an RTL test. `ModelHost`,
  `chip.outputs()`, `T.setup`, `Spi.set_low_inputs`, `Ext` and the `Pads`
  recorder were learned from `tools/` and `test/`. I read `test/test.py`,
  `test/test_post_freeze.py`, `test/test_sigrok.py`, `test/mutants.py`,
  `test/model/test_onewire.py` and `test_post_freeze_timing.py` as examples: the
  brief named firmware as examples, so this is a reading of "existing tools".
  `docs/post-freeze-protocols.md` was not opened.
* **A stalled autopush IN samples late.** §4.2 says side-set applies on every
  attempt and §4.4 says a stalled IN shifts nothing. Together: `in pins, 8 side
  idle` with autopush ends the strobe, then samples when RX has room, after the
  data has gone. Nothing warns of it. Found by reading, so `ata_pio` pushes
  explicitly.
* **Reset output state.** isa.md gives reset values for core state but its pin
  section does not spell out that uo pads sit at 0 until written, which matters
  for active-low control lines.
* The register names in isa.md (`FLAGS_SET`) are not the names in `sim.py`.
* `avoid=` is documented as taking points, but labels work.
* guide.md's reproduce commands assume local pytest; RULES says there is none.

## Tool and ISA pain points (not fixed)

* The timing checker has no progress output or budget: a slow deadline looks like
  a hang. Timing deadlines one by one needed a throwaway script.
* `sigrok.py` has no `parallel` wrapper, so `sigrok.decode` was called directly.
* `remote_test.sh` cannot pass SIGROK_CLI, so sigrok branches skip silently. The
  log line added here shows they ran.
* `COCOTB_TEST_FILTER` goes through a shell unquoted: `a|b` fails.
* One link register: `busy_wait` saves LR by hand.
* ALU immediates stop at 13 bits; `or r1, 0x1000` fits, by luck of the pin map.
* No way to preset the uo pads before reset releases.

## Times

**These are the agent's own record, with no log behind them.** They come from the
agent's shell timestamps during the session. No log was published or can be
checked, and none has been reconstructed. Treat them as a statement, not a
measurement. Git has one firmware commit, so it cannot show the order of work
either: that the far ends came before their firmware is also the agent's own
account.

| Protocol | First read to all its model and RTL tests passing, as recorded |
|---|---|
| HD44780 4-bit | 60 min 55 s |
| ATA PIO subset, mode 0 | 86 min 32 s |

Reading was shared and work overlapped, so the two do not add up. Mutants, review
and the verifier's findings continued after those points and changed firmware.

## Open

* **ATA reset window.** From reset until the host's first PIN_OUT write, DIOR-,
  DIOW-, CS0- and CS1- are all low, and RESET- is not available to hold the
  device off. ATA-3 calls that address invalid, and the far end shows no register
  touched (`ata_reset_window_is_a_real_limit`), but that is one model's reading,
  and it breaks the bus rules. A board needs a fix, such as inverters on the
  strobes with the polarity changed in firmware. Unresolved.
* HD44780 at 5 V needs level shifting on DB reads. Only a 3.3 V module was
  assumed. The HD44780S and clones have other tables, not checked.
* The list's independence: see The draw.
* The far ends are one reader's interpretation of the sources, with the
  from-memory items above. No real device has disagreed with them because none
  has been tried.
* A real CF card's 8-bit mode is unverified. One forum report describes a card
  that neither entered the mode nor reported an error.
* Only device 0, LBA, READ/WRITE SECTORS, IDENTIFY and SET FEATURES are modelled.
* Block cycle bounds with a host-side stall are unbounded by design; the static
  t0 and t1 bounds are minimums.

## Reproduce

```
remote_test.sh model test_blind       # [home directory redacted]/dev/protoemu-agent-tools/
# with sigrok, in the runner1 checkout it prints:
cd test && PATH=$HOME/tools/oss-cad-suite/bin:$PATH \
  SIGROK_CLI=$HOME/protoemu-flow/sigrok/sigrok-cli \
  make COCOTB_TEST_MODULES=test_blind
# mutants, from the checkout root:
PYTHONPATH=tools ~/protoemu-flow/test-venv/bin/python test/blind_mutants.py
```
