# RMII study: 100 Mbit/s Ethernet through an external PHY

Can the frozen chip (no RTL change), at 50 MHz, do 100 Mbit/s Ethernet through an RMII
PHY such as a LAN8720? RMII carries a dibit per 50 MHz REF_CLK clock, shared by the
chip and the PHY. Native 100BASE-TX is not in scope.

## Verdict

| Direction | Verdict | Evidence | Conditions |
|---|---|---|---|
| RX | **Go** | Cycle model and RTL simulation, through the SPI port | Two extra pads; a host that reads a word per 12 clocks with no stall over 71 clocks |
| TX | **Go** | Cycle model and RTL simulation, through the SPI port | A host that writes a word per 10 clocks, and always finishes the frame |
| RX and TX at once, at wire rate | **No-go** | Model | One port, one direction at a time: 147 % of it before headers. Half duplex only |
| Hardware | **Unproved** | Paper budget only | Nothing has run on a PHY, a fabric pad or silicon. The clock path outside the macro is open |

No hardware was touched for this study. The one FPGA figure below measures the port,
not RMII.

## RX: `firmware/rmii_rx.pasm`

**The problem.** `in pins, 2` on every clock uses every instruction. It keeps up, but
nothing is left to see the frame end or the FIFO fill.
`test_naive_one_instruction_loop_loses_silently` shows that loop: a host stall makes
the stream shorter, with no flag, and the core never leaves the loop. There is no RX
FIFO join either: `FJOIN_TX` is the only one, so RX has 8 words.

**The way out, with no RTL change.** Each RXD line goes to two pads, and `SYNC_BYPASS`
reads one pad of each pair from the first synchroniser flop, one clock newer:

| Signal | Pads | Stage |
|---|---|---|
| RXD0, RXD1 | uio0, uio1 | two flops, the older dibit |
| RXD0, RXD1 | uio2, uio3 | one flop (`SYNC_BYPASS` = 0x0C), the newer dibit |
| CRS_DV | uio4 | two flops: it rises asynchronously (LAN8720A rev 1.4, 3.4.1.1) |

One `in pins, 5` takes two dibits, so a pair costs two clocks and one instruction is
free. Those free instructions check CRS_DV, check the FIFO and count words. No pad of
the quad port (ui[4:2], uo[6:4]) is used.

**Words.** Five bits per sample, six samples, 30-bit autopush: 3 frame bytes per word.
The fifth bit is V7, a virtual pin held high. It costs a fifth of each word, and it
is needed: a read of an empty FIFO returns 0, and a frame of zeros would
otherwise look the same. With the tag, a host can read without knowing the FIFO level,
and discard zeros. A trailer ends each record, with flags and a word count.

**Frame boundaries.** The firmware waits for carrier, one preamble dibit (01), then the
first dibit 11. The next instruction samples the first data dibit. The preamble is not
counted, so a trimmed one works, and the FCS stays the host's job: a damaged SFD gives a
record whose FCS fails. At the end, CRS_DV is read only on a nibble's second dibit,
because when carrier ends first the PHY toggles it at 25 MHz (same section). The cost is
two dibits past the FCS in every record; the host keeps whole bytes.

### Measured

All on the cycle model with the tapeout's 2 cores and 8-deep FIFOs. The host is
`pe.rmii.QuadPort`, the clk side of `src/pe_hostport.sv` register by register, so its
latency, read-ahead, headers and CS gaps are paid. The rate tests run both phases of SCK
against clk.

| Quantity | Result | Test |
|---|---|---|
| Word period | exactly 12 clocks; the checker's bound is [12, 12] with no host assumption, 587 intervals measured | `test_static_deadlines_pass`, `test_checker_bounds_hold_on_the_model` |
| 1518-byte frame | every byte, FCS checked, for random, 0x00, 0xFF, 0x55, 0xD5 payloads: 508 words | `test_rx_full_frame_every_dibit`, `test_rx_word_rate_and_read_rate` |
| Sizes | 64-69, 127, 1517, 1518 bytes: all three exits of the loop | `test_rx_every_length_phase` |
| Minimum gap | 96 bit times, 48 clocks, 8 mixed frames | `test_rx_back_to_back_at_the_minimum_gap` |
| Gap margin | 8 clocks still works; 10 to 12 ticks from end of frame seen to hunting | `test_rx_gap_margin`, `turnaround*` deadlines |
| CRS_DV toggling | 1, 2, 3 and 8 nibbles before the end | `test_rx_crs_dv_toggling_at_the_end` |
| Read rate needed | one word per 12 clocks: 12 sustains, 13 fails | `test_rx_slowest_word_period_that_sustains` |
| Port rate used | 133 Mbit/s of the port's 200 wire rate (32 bits per 240 ns) | derived from the two above |
| SCK | at least 33.3 MHz with no pauses (derived). SCK = clk/2 fails, with the flag | `test_rx_sck_half_clock_fails_with_a_flag` |
| Burst length | READ_FIXED bursts with 26 clocks of header and gap: 7 words sustain, 6 fail. Model and **RTL** agree | `test_rx_shortest_read_burst_that_sustains`, `test_rmii_rx_seven_word_bursts_rtl`, `test_rmii_rx_six_word_bursts_rtl` |
| Longest host stall | 71 clocks, 1.42 us, worst phase | `test_rx_pause_budget` |

The FPGA figure for the port (193.6 Mbit/s for a 64-word quad write, 199.9 for 4096-word
bursts, fpga/kv260/README.md) is above the 133 needed, but it is another workload.

### If the host falls behind

The FIFO is checked before the push, so the loop never stalls and never drops a dibit
quietly. If RXF is full the frame is dropped. The record's trailer has the overflow bit,
global flag F0 stays set until the host clears it, and the core waits for the frame's
end. If carrier arrives while the trailer waits for room, the `missed` bit and F0 are set.

`test_rx_no_silent_loss_under_random_stalls` runs 12 seeds of random frames and stalls.
Every record taken as good is exact and in order, and each run of lost frames has a flag
beside it. Also `test_rx_stall_mid_frame_is_flagged_and_recovers`,
`test_rx_host_never_reads_then_returns`, `test_rx_too_long_is_flagged`, and on RTL
`test_rmii_rx_overflow_rtl`. Recovery needs no restart.

## TX: `firmware/rmii_tx.pasm`

One instruction: `out pins, 3`, 30-bit autopull. There is no spare clock to end a frame,
so TX_EN is data: 3 bits per clock, 10 samples per word, one word per 10 clocks, 160
Mbit/s on the port. `FJOIN_TX` gives 16 words. The host supplies preamble, SFD, payload,
FCS, gap and TX_EN. `pe.rmii.tx_words` packs them.

| Quantity | Result | Test |
|---|---|---|
| 1518-byte frame, 64-byte frame | every dibit, TX_EN unbroken, exact length, FCS checked by a pins-only sink | `test_tx_full_frame_no_gap`, `test_tx_min_frame`, `test_rmii_tx_rtl` (**RTL**) |
| Minimum gap | exactly 48 clocks between frames | `test_tx_back_to_back_at_the_minimum_gap` |
| Host pacing that works | 8 SCK at clk + 2 held clocks per word; or WRITE_FIXED bursts of 9 words, 90 clocks each with 18 of overhead (after a 24-word first burst, 40 on RTL) | same tests |
| Host rates that fail | 8 clocks per word overruns on a full frame, 11 runs dry | `test_tx_host_rate_limits` |
| Unpaced burst | one WRITE_FIXED at SCK = clk works up to 189 bytes | `test_tx_longest_frame_from_a_plain_burst` |
| Stall mid-frame | 40, 90, 140 clocks after a ramp of 24, 48, 72 words at full speed, to 10 clocks | `test_tx_cushion` |

**Limits of this firmware, on purpose.** An underrun holds the pins: mid-frame, TX_EN
stays up and a dibit repeats until data returns. Nothing on the chip records it, and if
the host dies TX_EN stays high. The far end's FCS fails
(`test_tx_underrun_breaks_the_frame_visibly_at_the_far_end`). By isa.md a host write to
`PIN_OUT` overrides the pin; no test runs that here. A frame must be followed by an idle sample, or TX_EN stays up
(`test_tx_frame_needs_an_idle_sample_after_it`).

## Both directions

Both firmwares fit together, RX on core 0 and TX on core 1, and `test_half_duplex_rx_then_tx_on_one_chip`
runs a frame in, then a frame out. At wire rate at once they do not fit: reads take 8/12
of the port and writes 8/10, 147 % before headers.
`test_full_duplex_at_line_rate_does_not_fit_the_port` tries 16 alternations of bursts and
none delivers both 1518-byte frames; RX failures all raise the flag. A frame arriving
while a long one is sent is lost, and flagged. A PHY link in full duplex can do that.

## Clocking

**Relation.** Chip and PHY share one 50 MHz. The chip samples on its rising edge what the
PHY launched on the previous rising edge. It launches on its rising edge what the PHY
samples on the next one. A whole clock either way changes nothing:
`test_eye_on_the_model` shows that and the shape of the window.

**Required**, as offsets of the first flop's sample from the PHY's REF_CLK rise, modulo
20 ns. From LAN8720A rev 1.4 tables 5.10 and 5.9 (10 to 25 pF load):

| PHY clock mode | Output valid / hold after rise | RX: sample must fall | TX: setup / hold at PHY | TX: arrival after launch edge must fall |
|---|---|---|---|---|
| REF_CLK in | 14.0 / 3.0 ns | +14 to +23 ns, less flop setup and hold | 4.0 / 1.5 ns | +1.5 to +16 ns |
| REF_CLK out | 5.0 / 1.4 ns | +5 to +21.4 ns, same | 7.0 / 2.0 ns | +2 to +13 ns |

Table 5.11 also asks for CLKIN ±50 ppm, 40-60 % duty and 150 ps peak-to-peak jitter.

**Estimate: the macro alone.** OpenSTA on the signed-off netlist and SPEF of release
candidate fo6, boundary to boundary, 6 fF load, in-phase clock at the macro's own `clk`
port (`pe.rmii.STA_FO6`, `test_pad_budget`). Slack in ns, each a worst path. **These are
estimates: no TT harness, no pad, no package, no board is in them**, and docs/physical.md
holds no measured pad delay.

| Mode | Corner | RX setup | RX hold | TX setup | TX hold |
|---|---|---|---|---|---|
| REF_CLK in | fast | 6.33 | 2.50 | 13.51 | 0.07 |
| REF_CLK in | typ | 6.45 | 2.29 | 12.28 | 0.81 |
| REF_CLK in | slow | 6.64 | 1.93 | 10.15 | 2.13 |
| REF_CLK out | fast | 15.33 | 0.90 | 10.51 | **-0.43** |
| REF_CLK out | typ | 15.45 | 0.69 | 9.28 | 0.31 |
| REF_CLK out | slow | 15.64 | 0.33 | 7.15 | 1.63 |

So at the macro's boundary REF_CLK in is positive everywhere, and REF_CLK out is thin or
negative on hold. Output delay outside the macro helps TX hold and hurts TX setup; clock
insertion outside the macro moves RX the wrong way.

The Tiny Tapeout clock page expects up to 10 ns between the chip's pad and a project's
clock ([tinytapeout.com/specs/clock](https://www.tinytapeout.com/specs/clock/)). It does
not say this applies to the IHP chip. If it does, and it is not matched on the data
inputs, 10 ns in the clock path alone fails RX hold (`test_pad_budget`): the sample falls
where the PHY's outputs are changing. Inverting the PHY's clock would move it 10 ns, an
untested idea. The real figure decides this and it is **open**.

**TT demo board: what it needs.** The chip's `clk` and the PHY's clock from one 50 MHz
source with a known phase, meeting table 5.11, and the three signal groups on free pads.
**Open, not found:** whether the demo board lets an external clock drive `clk`, or
brings its own clock to a header for a PHY; the RP2350 clock's ppm and jitter; board
delays; the IHP harness's clock insertion and input and output delays. The clock page
describes an RP2040-generated clock, 1 Hz to 66.5 MHz.

**Board host.** docs/board-transport.md's default SCK is half the project clock: 16
clocks per word, too slow for both directions. RX needs 33.3 MHz and TX 40 MHz. With its
four PIO instructions per SCK that means a 133 and 160 MHz RP2350 system clock: an
estimate, not run. Its sampling margin is unmeasured.

**KV260: what it needs.** From this repo:

* PMOD J2 has 8 signals. 5 RX pads, 3 TX pads and REF_CLK make 9, and one PMOD pin
  carries one chip pin (`kv260_top.v`), so the bitstream must join uio0/uio2 and
  uio1/uio3, or RX needs its pads on two wires and REF_CLK has no pin. MDIO has no pin:
  the PHY's mode must be strapped.
* No clock is forwarded to PMOD today. protoemu's clock is an MMCM output, so a forwarded
  REF_CLK with a phase setting is plausible, in a rebuilt bitstream. Its ppm and jitter
  against table 5.11 are **open**, as is using a PMOD pin as a clock input instead.
* PMOD pins have false paths, SLOW slew and 4 mA by default (`kv260.xdc`): pad and
  fabric-route delays are unconstrained, so the window above is not met by construction.
* `pe_spim`: SCK = mclk reads at 8 clocks per word, 4096 words per burst, 32793 clocks
  (656 us, fpga/kv260/README.md), then software. So RX is limited to one buffer. Its integer DIV
  has no 10-clock word: TX over 189 bytes needs a change there, such as a word pause.
* These are fpga/ changes. The chip's RTL stays as it is.

**Next step that needs no wiring (not done; orchestrator only).** pe_lab's pattern
generator drives all uio pads on protoemu's own clock, 1024 entries, so a 64-byte frame
(288 dibits) fits, with uio0 = uio2 and uio1 = uio3. That would be RX on RTL in fabric,
with a fabric master reading it.

## Reaction: `firmware/rmii_match.pasm`

The exact-delay route of `eth10_match`: wait for the SFD, `nop [47]` for 12 bytes, 16
`in pins, 2` on consecutive clocks (straight-line, as there is no time for a loop),
compare the EtherType, compare the field with a threshold, raise a pin. Three pads, no
bypass needed.

| Measure | Result | Test |
|---|---|---|
| Edge latching the field's last dibit into the first flop, to the edge that changes the pin's output register | **10 clocks**, 200 ns, on 8 of 8 frames. Model and **RTL** equal | `test_reaction_clocks`, `test_rmii_match_rtl` |
| Same with `SYNC_BYPASS` on uio0, uio1 | 9 clocks, 180 ns | `test_reaction_clocks` |
| Composition | 2 flops, then 8 instructions (checker: [8, 8]) | `last_to_pin` deadline |
| Before a 64-byte frame's last dibit is latched | 182 clocks, 3.64 us | `test_reaction_precedes_the_end_of_a_min_frame` |

The dibit was launched one clock before that first edge, so from the PHY's launch edge
add 1 clock. Add also the pad and output delays, unmeasured in silicon.

**PHY latency is open.** The LAN8720A datasheet rev 1.4 has no latency figure (the word
does not occur in its text, and the RMII timing section has none). Wire-to-RXD time is not in any number here.

**Speculative**, as `eth10_match`: the decision precedes the FCS. Offsets are fixed, so a
VLAN tag moves the field and it is missed. No reply frame.

## Tests and how to reproduce

`test/model/test_rmii.py`, 105 tests: pins-only far end; full-size, minimum-size,
back-to-back; drain failure; and negatives. `test_corrupted_dibit_fails_the_check` wrongs
every one of a 64-byte frame's 256 dibits in turn, with `test_clean_control_for_the_corruption_test`
as the control. `test_corrupted_sfd_gets_no_good_record`, `test_corrupted_tx_dibit_fails_the_check`,
`test_broken_firmware_fails` (one clock off: model and checker both fail),
`test_broken_tx_firmware_has_gaps` and `test_second_flop_only_would_repeat_dibits`.

`test/test_rmii_rtl.py`, 6 cocotb tests on the RTL (tb.v, tapeout geometry) through real
quad SPI at SCK = clk: RX of 1518 + 64 bytes at the minimum gap, 7-word and 6-word bursts,
overflow and recovery, TX of 1518 bytes, and the match pin.

```
[home directory redacted]/dev/protoemu-agent-tools/remote_test.sh model test_rmii_rtl
PYTHONPATH=tools pytest -s test/model/test_rmii.py      # prints the numbers above
PYTHONPATH=tools python -m pe.timing firmware/rmii_rx.pasm
```

The STA numbers come from `flow/rmii_sta.tcl` (its header has the command) on runner1,
against `~/protoemu-flow/protoemu-exp-fo6/runs/wokwi`, at the three corners. Reports:
runner1 `~/protoemu-flow/agent-runs/rmii-sta/`.

## What is unproved

* **Everything physical.** No PHY, no pads, no real REF_CLK. The tests' pins change
  between clock edges, with no skew and no noise. Gate level was not run.
* The RMII behaviour comes from the LAN8720A datasheet. The RMII specification's own
  text could not be read here. Real PHY behaviour around the frame's start and end is
  unobserved.
* RXER is not wired and is ignored. A symbol error reaches the host as data for the FCS
  to catch. A frame ending on a nibble (dribble) keeps an extra byte, so the host's FCS
  check fails it where a MAC might accept it (`test_rx_dribble_nibble`).
* SYNC_BYPASS gives the newer dibits one flop. That is sound only with setup and hold
  met; outside the window a metastable sample reaches the core.
* QuadPort is a model. Its burst limit is confirmed on RTL at 6 and 7 words, one frame,
  and at SCK = clk only. FPGA pad timing at SCK = clk with a real reader is not this test.
* The model's host stops at the port: nothing here stores or processes a frame.
* The eye sweep has no setup time and no metastability; its random values stand in for X.
* Not built: a second core copying RXD to virtual pins might replace the two extra pads
  at the cost of the TX core. An idea only.

## What hardware would have to do

1. One 50 MHz for chip and PHY, within table 5.11, phase set so both windows above hold
   at the flops: measure or sweep it first.
2. Wire five RX pads (RXD doubled) and three TX pads; strap the PHY.
3. A host with SCK of at least 33.3 MHz for RX, or 40 MHz for TX with word pacing, on a
   clock tied to the chip's, with a frame's worth of real-time storage. The ability to
   sample quad reads at that SCK is itself unmeasured.
4. Keep stalls under 71 clocks on RX, and never starve TX mid-frame.
5. Run half duplex, or accept flagged RX loss while transmitting.
