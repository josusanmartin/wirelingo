# Post-freeze protocol challenge

The competition asks for a chip "reprogrammable enough to support new
protocols after fabrication". This is a rehearsal of that moment: with the
RTL frozen at **tag `rc-93c2137-t48ba`** (the release candidate, run t48ba),
implement protocols the chip was never designed for, in firmware only, and
judge each one with oracles that do not share our assumptions.

Rules kept: no change under `src/` (`git diff rc-93c2137-t48ba -- src/` was
empty on this branch; since then `main` has changed only the harden
configuration `src/config.json`, for run fo6's antenna repair: the chip
Verilog `src/*.v` is still the tag's), and none to the golden model,
assembler or timing checker (`tools/pe/{sim,isa,asm,line,timing}.py` were as
on `main`). One tool fix came out of the first round's FPGA run afterwards:
the assembler's `/` became integer division (bidirectional DShot was a clock
short at 50 MHz, [below](#results)); `timing.py`'s command line also changed
to exit 0 on a declared conditional PASS. Added:
firmware, host-side helpers and pins-only models (`tools/pe/postfreeze.py`),
sigrok wrappers (`tools/pe/sigrok.py`), tests and this document. None of
these protocols appears in the RTL, the ISA spec or any earlier firmware;
nothing in them needed a feature the chip lacks, though several needed
workarounds (section [ISA limitations](#isa-limitations-found)).

## Results

| Protocol | Firmware (words) | sigrok decoder | Timing, measured | Spec |
|---|---|---|---|---|
| DMX512 transmitter | `dmx512_tx` (20) | `dmx512`: 3 full universes (513 slots) | at 50 MHz: BREAK 176.000 us, MAB 12.000 us, every bit edge on the 4 us grid to the clock; BREAK to BREAK 1204 us for 1..22 slots, 194 + 44 n us above | E1.11 TX: BREAK >= 92 us, MAB >= 12 us, bit 3.92..4.08 us, BREAK to BREAK >= 1204 us |
| LIN commander | `lin_commander` (142) | `uart` + `lin`: PID parity, data, enhanced and classic checksums | at 50 MHz: break 12.9992 bits, delimiter 1.0003, edges inside a byte -30..+3 ns from the 19200 bit/s grid (-0.003 % rate) | commander clock +-0.5 %, break >= 13 bits, delimiter >= 1 bit |
| LIN responder (sync-field bit-rate detection) | `lin_responder` (191) | same | locks to a commander 10 % fast or slow and to its own clock 14 % off; its response's bit within 0.44 % of the commander's | after synchronisation +-2 %; before +-14 % |
| NEC infrared transmitter, 38 kHz carrier | `nec_tx` (60) | `ir_nec`, on the raw carrier (`cd_freq=38000`) and on the receiver module's output | at 50 MHz: carrier 37999.94 Hz, 1/3 duty (8.77 us +- 1 clock); unit 561.4 us (-0.2 %); codes exactly 192 units (107.789 ms) apart | sigrok accepts +-5 %; NEC receivers typically far more |
| NEC infrared receiver | `nec_rx` (75) | same (it decodes what we decoded) | decodes senders +-10 % in rate with bursts stretched or shrunk by 200 us, rejects glitches and truncated frames | |
| I2S transmitter, 16-bit stereo | `i2s_tx` (14) | `i2s` | 48 kHz at 50 MHz: BCLK period 32 or 33 clocks (fractional divider), fs 47996.5 Hz (-0.007 %); exact 64 ticks per frame, also on underrun | receivers are clocked by BCLK |
| Wiegand transmitter | `wiegand_tx` (48) | `wiegand` | at 50 MHz: pulses 50.000 us, intervals exactly IV (2 ms or 200 us as configured) | SIA AC-01: pulse 20..100 us, interval 200 us..20 ms |
| Wiegand receiver | `wiegand_rx` (63) | same | 20..100 us pulses, 0.2..5 ms intervals, 2 us glitches ignored; parity of 26- and 34-bit formats checked | |
| DShot600 | `dshot600` (25) | `pwm` (no DShot decoder exists): frames rebuilt from duty cycles | 48 MHz: T1H 1250.0, T0H 625.0, bit 1666.7 ns, exact; 50 MHz: +-10 ns (one clock) | Betaflight's 1250 / 625 / 1667 ns |
| Bidirectional DShot600 (eRPM telemetry) | `dshot_bidir` (59) | `pwm` (active low) for the frames; the telemetry has no sigrok decoder | frames as DShot600; telemetry recovered for an ESC clock 15 % fast or slow, turnarounds 20..60 us | |
| DALI application controller | `dali_ctrl` (69) | `dali`: forward and backward frames (16-bit forward only: the decoder has no 24-bit frames) | at 50 MHz: every half bit 20833 or 20834 clocks (416.66 / 416.68 us); forward frames 14.15..14.57 ms apart; backward frames decoded from gear at 336..498 us half bits, asymmetric by up to 75 us, 5.5..10.4 ms after the forward frame; settling time reported to 0..2 ticks (26 us) | IEC 62386-101 ed. 2: receiver half bit 333.3..500 us, double 666.7..1000; backward frame 5.5..10.5 ms after; 13.5 ms between forward frames |
| Modbus RTU client (request out, response in) | `modbus_rtu` (119) | `uart` (even parity) + `modbus`: function and "CRC correct" both ways | 19200 baud at 50 MHz: edges -33..+17 ns from the bit grid (-0.004 %); characters of a request 0.5..3.5 us apart; responses from a server 2 % fast or slow with 1.3-character gaps received; a character after t1.5 flagged; t3.5 ends a frame | Modbus over Serial Line V1.02: 11-bit characters, gaps < t1.5 inside a frame, >= t3.5 between |
| MIDI receiver and parser | `midi_rx` (132) | `uart` + `midi` on the stream played in: the same messages | 31250 baud: every byte sampled 1600 clocks per bit at 50 MHz; the parser takes 14..39 ticks (checker: <= 48) of the 64 in half a stop bit; streams from a sender 2 % fast or slow parsed | MIDI 1.0: 31250 baud +-1 % |
| MIDI transmitter (running status) | `midi_tx` (58) | `uart` + `midi`: notes, program change, pressure, pitch bend, clock | every bit 32.000 us (CLKDIV 50 at 50 MHz, exact); stop bit and space 40..61 us | |
| Quadrature encoder counter (X4) | `quad_enc` (20) | `graycode`: the same count | CLKDIV 1: a step every 14 (forward) or 15 (backward) clocks or slower counted exactly: 3.3 M steps/s at 50 MHz; faster, missed steps are flagged | (no standard: A/B in quadrature) |
| HDMI-CEC device: initiator and follower | `cec` (244) | `cec`: headers, opcodes, operands, ACK/NACK | at 50 MHz: start bit low 185000 clocks (3.7 ms), next fall 225000 (4.5 ms), a 1 low 30000 (0.6 ms), a 0 75000 (1.5 ms), bits 120000 (2.4 ms) apart, exact; frames from initiators at the ends of the receiver's ranges received and acknowledged; arbitration lost (then received as a follower) and won; signal free time 3 / 5 / 7 bit periods + 0.1..0.3 ms | HDMI 1.3a Supplement 1: start 3.7 / 4.5 ms +-0.2, a bit 2.4 ms (receiver 2.05..2.75), low 0.6 / 1.5 +-0.2, sampled at 1.05 +-0.2 |
| ISO 7816-3 T=0 reader, with the card clock | `iso7816` (218) + `iso7816_clk` (1): both cores | `uart` (even parity): both directions | 1 etu = 372 CLK exactly (4464 clocks at 50 MHz, every edge on that grid); characters 12 + N etu apart; our error signal 10.5 etu after the start, 1.5 etu long; a rejected character sent again 2 etu after the card's error signal ends; card to reader >= 16 etu | ISO/IEC 7816-3: 12 etu (+ N) start to start, error signal at 10.5 +-0.2 etu for 1..2 etu, repetition >= 2 etu after it, >= 16 etu between directions |
| SMBus host with PEC | `smbus_host` (189) | `i2c`: addresses, data, ACK/NACK; the PEC recomputed over its bytes | at 50 MHz (CLKDIV 56): SCL low 5.6 us, high 4.5..5.6 us, 89..99 kHz; START hold 4.5 us, STOP setup 6.7 us; a device holding SCL low: given up 25.003 ms after we release it | SMBus 3.2: 10..100 kHz, tLOW >= 4.7 us, tHIGH >= 4.0 us, T_TIMEOUT 25..35 ms; PEC = CRC-8 |
| RC-5 infrared transmitter, 36 kHz carrier | `rc5_tx` (49) | `ir_rc5` on the receiver module's output | every carrier period 3 ticks, on for 1, across half bits too (at 50 MHz 1388 or 1389 clocks, 35999.8 Hz on average); half bits of 32 periods (888.9 us), frames 113.78 ms apart, exact in ticks | RC-5: 36 kHz, half bit 889 us, a frame every 64 bits |
| RC-5 infrared receiver | `rc5_rx` (33) | same | decodes a remote 10 % fast or slow with bursts 150 us longer or shorter; truncated frames and missing transitions reported | |

The first round: ten programs, 697 instruction words; the largest,
`lin_responder`, uses 191 of a bank's 256. Every one has an RTL test through
the real SPI host port (`test/test_post_freeze.py`: 8 tests, all passing, 148
s of icarus) and model tests (`test/model/test_{dmx512,lin,nec,i2s,wiegand,dshot,dshot_bidir}.py`,
`test_post_freeze_timing.py`: 78 tests, 79 since the assembler fix made
`test_bidir_no_reply_and_timing` run at 48 and 50 MHz; the whole model
suite, 517 tests, passed on that branch).

The second round ([below](#second-round-dali-modbus-rtu-midi-quadrature)):
five more programs, 398 words (the largest, `midi_rx`, 132 of 256), each in
one core's bank; a complete MIDI port (in and out at once) takes both cores
of the IHP chip. Four more RTL tests through the SPI host port (`pf_dali`,
`pf_modbus`, `pf_midi`, `pf_quad_enc`: test_post_freeze has 12, all passing
on runner1) and 61 more model tests (`test/model/test_{dali,modbus,midi,quad_enc}.py`
and 14 in `test_post_freeze_timing.py`). None of these five has run on
silicon; on the FPGA, see below.

The third round ([below](#third-round-hdmi-cec-iso-7816-3-smbus-with-pec-rc-5)):
six more programs for four protocols, 734 words (the largest, `cec`, 244 of
256: initiator and follower in one bank); ISO 7816-3 takes both cores of the
IHP chip, because the card's clock needs a core of its own. Four more RTL
tests through the SPI host port (`pf_cec`, `pf_iso7816`, `pf_smbus`,
`pf_rc5`: test_post_freeze has 16, all passing on runner1, 168 s of icarus;
locally with sigrok as well) and 72 more model tests
(`test/model/test_{cec,iso7816,smbus,rc5}.py`: 56, and 16 in
`test_post_freeze_timing.py`). None of these six has run on silicon; on the
FPGA, see below.

On hardware (the RTL on the KV260 FPGA, 2026-10-13): see fpga/kv260/README.md, "Post-freeze protocols on hardware" (`fpga/kv260/host/postfreeze_demo.py`): 15 of 16 runs passed at 50 MHz; bidirectional DShot's bit timing was a clock short at 50 MHz (odd T0: the assembler divided exactly and truncated each half of a split delay). With `/` now integer division it measures exactly 62 / 31 / 83 clocks at 50 MHz on the same bitstream, and exact at 48 MHz.

Second round on hardware (the RTL on the KV260 FPGA, 2026-10-14): see fpga/kv260/README.md, "Post-freeze protocols, second round, on hardware" (`fpga/kv260/host/postfreeze2_demo.py`): 25 of 25 runs passed at 50 MHz (DALI, Modbus RTU, MIDI in/out and both at once, quadrature), every far end a pins-only model played by pe_lab's pattern generator or the other core; results equal to the cycle model's.

Third round on hardware (the RTL on the KV260 FPGA, 2026-10-14): see fpga/kv260/README.md, "Post-freeze protocols, third round, on hardware" (`fpga/kv260/host/postfreeze3_demo.py`): 31 of 31 runs passed at 50 MHz (HDMI-CEC, ISO 7816-3 T=0 with its clock core, SMBus with PEC, RC-5 TX and RX), every far end a pins-only model pre-timed and played by pe_lab's pattern generator; the firmware's records equal to the cycle model's.

## How they are judged

* **Pins-only models** (`tools/pe/postfreeze.py`), the other end of each
  line, seeing nothing but pin levels and times: a DMX512 receiver with
  E1.11's receiver rules, a LIN bus decoder, an independent LIN commander
  (waveforms at any bit rate, with wrong PIDs and checksums on demand) and
  LIN responder node (its own bit rate), an NEC envelope decoder, a
  TSOP-style IR receiver module (output low 7 carrier periods after the
  carrier starts until 9 after it stops), an independent NEC remote with
  timing scale and burst distortion, an I2S receiver, a Wiegand decoder and
  card-reader model, a DShot decoder and a bidirectional-DShot ESC that
  decodes the frames, checks the inverted checksum, answers with GCR
  telemetry and flags bus contention. Checksums and parities (LIN PID and
  checksum, Wiegand parity, DShot CRCs, GCR) are recomputed there from the
  protocol definitions, independently of the firmware. Second round
  (`tools/pe/postfreeze2.py`): a DALI decoder with the standard's receiver
  ranges and a control-gear model answering at its own timing, a UART
  decoder with parity, a Modbus RTU server model (CRC by the specification's
  algorithm), a MIDI reference receiver and running-status encoder, and a pins-only
  X4 quadrature decoder (with the tests' encoder model, which knows the
  position it moved to). Third round (`tools/pe/postfreeze3.py`): a CEC
  decoder with the receiver's ranges and a CEC device model (a follower
  that acknowledges its address, an initiator with its own timing that
  arbitrates and checks ACKs); an ISO 7816 T=0 card that times everything
  by counting the CLK it is given, checks parity, signals errors and repeats
  what the reader rejects; an SMBus device that computes its own PEC over
  what it saw, stretches the clock and resets itself after 35 ms of SCL low;
  an RC-5 decoder written from the code's definition and an independent
  remote's envelope (into round one's receiver-module model, at 36 kHz).
* **sigrok** (`sigrok-cli` 0.7.2, libsigrokdecode 0.5.3, installed in user
  space by `flow/install_sigrok.sh`): third-party decoders on the recorded
  waveform (model pins, or the RTL's pads). Their outputs are quoted below.
  The tests skip the sigrok checks without `sigrok-cli`; on this machine all
  of them ran.
* **The static timing checker** (`tools/pe/timing.py`, docs/timing-check.md):
  every program carries `@deadline` annotations for its critical timing; all
  pass, and `test_post_freeze_timing.py` measures the same intervals on the
  model (every measurement inside the checker's bound, as
  test_timing_check.py requires for the older firmware).
* **The RTL** through the SPI host port, the pads recorded and judged by the
  same models and decoders. Protocols with millisecond frames run the chip at
  a slower clock (DMX512's universes and Wiegand at 1 MHz, LIN at 5 MHz, NEC
  at 456 kHz): the programs count ticks of their divider, so the waveform in
  ticks is the same as at 50 MHz (`test_dmx512_clock_scaling` checks it edge
  for edge), and icarus simulates about 5000 clocks per second. The timing
  numbers above come from 50 MHz (or 48 MHz) model runs and the checker's
  clock bounds; DMX512's timing run, I2S, DShot600 and bidirectional DShot
  run at 50/48 MHz on the RTL as well. Second round: DALI at 307.2 kHz
  (CLKDIV 4), Modbus at 1.9968 MHz and MIDI at 4 MHz (both CLKDIV 1 for the
  receiver), the quadrature counter at 50 MHz; on the model DALI and Modbus
  also run a frame at 50 MHz. Third round: CEC at 100 kHz (CLKDIV 1),
  ISO 7816 at 4 MHz (the card's CLK at 2 MHz), SMBus at 3.571 MHz (CLKDIV
  4), RC-5 at 108 kHz (CLKDIV 1 and 1.5), each tick for tick its 50 MHz
  configuration; on the model each also runs at 50 MHz.

```
PYTHONPATH=tools .venv/bin/python -m pytest test/model/test_dmx512.py test/model/test_lin.py \
    test/model/test_nec.py test/model/test_i2s.py test/model/test_wiegand.py \
    test/model/test_dshot.py test/model/test_dshot_bidir.py test/model/test_post_freeze_timing.py \
    test/model/test_dali.py test/model/test_modbus.py test/model/test_midi.py test/model/test_quad_enc.py \
    test/model/test_cec.py test/model/test_iso7816.py test/model/test_smbus.py test/model/test_rc5.py
cd test && make COCOTB_TEST_MODULES=test_post_freeze          # RTL, about 6 minutes
PYTHONPATH=tools python -m pe.timing firmware/dmx512_tx.pasm -D TX=8 -D MTBS=0   # each header has its command
```

## DMX512 transmitter (`dmx512_tx`)

**Implemented.** ANSI E1.11 (DMX512-A) packets: BREAK 176 us, MARK AFTER
BREAK 12 us, then n = 1..513 slots (START code first), 8N2 at 250 kbit/s, an
optional mark between slots (`MTBS` define, 0 = back to back). 1 tick = 1 us
(CLKDIV 50 at 50 MHz, exact). The host sends a header word (n) and 4 slots
per word; a `pull ifempty` before each slot means a late host stretches the
mark between slots (legal up to 1 s) instead of breaking a slot. Short
packets are padded with mark so that BREAK to BREAK is never under E1.11's
1204 us: the firmware keeps a budget (`r2 = 1008 - 44 n`) and pads what is
left.

**Timing.** The checker proves BREAK = 176 ticks, MAB = 12, every bit 4,
slot to slot 44 (host keeping up), stop bits >= 8. BREAK to BREAK cannot be a
checker deadline: it rests on the relation between two registers (r2 and the
slot count), which the checker does not track, so it is measured instead for
n = 1..40, 512 and 513: exactly 1204 us up to 22 slots, 194 + 44 n above.
At 50 MHz on the model and on the RTL: break 176.0 us, MAB 12.0 us, the
largest deviation of any bit edge from the 4 us grid 0 ns.

**sigrok** (`dmx512`, RTL at 1 MHz, MTBS = 1):
`sigrok dmx512: [513, 513, 1] slots, break [176.0, 176.0, 176.0] us, MAB [12.0, 76.0, 76.0] us, errors []`.
The 76 us MABs are real: at a 1 MHz chip clock one 32-bit SPI word takes
256 us, so the host's data arrived after the MAB had started and the line
marked a little longer (the test pre-loads the first packet; E1.11 allows a
MAB up to 1 s). On the model at 1 MHz: three universes, 176.0 / 12.0 us, no
errors.

**Limits.** Transmit only (no receiver, no RDM). The host must refresh within
1 s (receivers drop a stale universe). libsigrokdecode 0.5.3's `dmx512` has
three limits that our receiver does not: it reads a slot's second stop bit at
the first sample of the next slot, so it misframes back-to-back slots (MTBS =
0, which E1.11 allows: tested with our receiver, sigrok runs use MTBS = 1);
it looks for the next BREAK only after 512 channels, so it cannot frame a
shorter packet; and it takes a mark of more than 88 us before the first
BREAK for a BREAK (the RTL capture starts 20 us before the first one).

## LIN commander (`lin_commander`) and responder (`lin_responder`)

**Implemented.** LIN 2.x frames on a transceiver's TXD/RXD (the tests wire
the bus as the AND of the nodes' TXD, fed back as RXD).

* Commander: break of 13 bits and a 1-bit delimiter, sync 0x55, the PID with
  its parity bits computed in firmware (P0 = ID0^ID1^ID2^ID4,
  P1 = !(ID1^ID3^ID4^ID5)), then either publishes 1..8 bytes with the
  checksum computed in firmware (classic or enhanced, the 8-bit sum with
  end-around carry, inverted) or subscribes: receives n bytes + checksum at
  mid-bit, each re-synchronised on its start bit, and checks the checksum;
  an absent or late responder is a timeout record. TB = 104 ticks per bit
  (CLKDIV 25.04 at 50 MHz for 19200 bit/s).
* Responder: a node without a crystal. Detects a break (dominant for 11 bits
  of its own clock), then measures the commander's bit time on the sync
  field: four `wait fall ... timeout r3` in a row share one timeout counter,
  which becomes the timestamp of the fifth falling edge; T = (elapsed + 4) / 8
  ticks, accepted between TMIN and TMAX. Everything after (PID, data, its own
  response) uses that T. Checks the PID parity (reports a bad one and never
  answers it), publishes a response the host staged for that ID (the staged
  response waits in the OSR through other headers, and the checksum is
  computed in firmware), or else listens: records every byte until an
  inter-byte timeout and checks the checksum both ways (classic and
  enhanced), since a listener does not know the length. A dominant field read
  as 0x00 with a framing error is taken as the next break.

**Timing.** Commander, checker: break 1352 ticks (13 bits), delimiter 104,
every sent and received bit 104, spaces between header/published bytes 0 to
32 ticks (0.3 bit, the PID computation). At 50 MHz on the model: break
12.9992 bits, delimiter 1.0003, edges inside each byte within -30..+3 ns of
the 19200 bit/s grid (the divider is 25 + 10/256 for 25.0401: -0.003 %);
LIN asks +-0.5 % of a commander. Responder: its bit timing is a measured
value, so the checker cannot bound it (it reports register-valued loops as
unbounded); it does bound the header record (<= 39 ticks after the PID's
stop bit) and the model measures the rest. Against the independent
commander model:

| commander rate | responder clock | T measured (ideal) | response bit vs commander |
|---|---|---|---|
| nominal | nominal | 104 (104.2) | -0.16 % |
| +10 % | nominal | 95 (94.7) | +0.32 % |
| -10 % | nominal | 116 (115.7) | +0.22 % |
| nominal | +14 % | 119 (118.8) | +0.14 % |
| nominal | -14 % | 90 (89.6) | +0.44 % |
| +5 % | -10 % | 89 (89.3) | -0.34 % |

LIN 2.x allows a responder +-2 % after synchronisation; the error is T's
rounding to a whole tick (0.5 us ticks, so 0.5 % at T = 90). The space
between the responder's bytes is 0.07..0.13 bit.

**sigrok** (`uart` + `lin`, RTL at 5 MHz):
`sigrok lin: [(16, True, '01020304', True), (33, True, 'c0ffee', True)] []`
(ID, parity ok, data, checksum ok): a published frame and the responder's
staged answer to a subscribed one. On the model,
`sigrok lin (v2): [(16, True, '0102030405', True), (34, True, 'cafe', True), (60, True, '0001020304050607', True)] []`,
and decoding the same bus as LIN 1 (classic checksums) fails the enhanced
frames and passes ID 0x3C's classic one, as it should.

**Limits.** Frames, not a full LIN stack: no schedule table (the host sends
each frame), no sleep/wake-up, no diagnostic transport layer, no
response_error bit. The commander does not read back its own bits (no bit
error detection), nor does the responder while publishing. Each byte of a
response must start within BTMO ticks (a define; the tests use 4 bits) of
the previous one: LIN lets a whole response take 40 % longer than nominal,
so a slow responder needs a larger BTMO, and a listening responder takes a
gap longer than BTMO as the end of the frame. One staged
response at a time. The host must drain the responder's records (one per
byte; at least one read per 520 us at 19200 bit/s), else F2 flags a dropped
record.

## NEC infrared transmitter (`nec_tx`) and receiver (`nec_rx`)

**Implemented.** Transmitter: the 38 kHz carrier itself (1 tick on, 2 off at
114 kHz ticks: 1/3 duty), NEC's leader (16 u burst, 8 u space), 32 bits LSB
first (address, ~address, command, ~command, the inverses computed in
firmware; or a 16-bit extended address), the stop burst, repeat codes, and
every code padded to start 192 u (108 ms) after the previous one: all paths
from the `pull` to the leader take the same 23 ticks, so the period does not
depend on the code. Receiver: the output of an IR receiver module
(demodulated, active low); every interval is a level WAIT with a timeout
(nothing hangs, no edge is missed), with windows for the leader (7..11 ms),
frame/repeat spaces (3.37..5.67 / 1.5..3.37 ms), data bursts (<= 1 ms) and
spaces (0: <= 1.12 ms, 1: <= 2.52 ms). It checks the command's inverse and
reports whether the address is standard or extended, repeat codes, and
errors with the number of bits received.

**Timing.** Checker: carrier period 3 ticks, on for 1; bursts of 21 and 341
carrier periods; leader + space 1536 ticks (24 u), bit 0 128, bit 1 256,
repeat 1280, repeat to repeat and repeat to frame 12288 (192 u). At 50 MHz
(CLKDIV 438.6): carrier 37999.94 Hz (period 26.30..26.32 us), on 8.76..8.78
us, a repeat code's bursts 341 and 21 periods. NEC's unit is 562.5 us; ours
is 64 ticks = 561.4 us (-0.2 %), and codes start 107.789 ms apart (108 ms
nominal). Receiver: the record is in the FIFO 24..25 ticks (250 us) after the
stop burst (checker bound <= 40). Tolerance, against the independent remote:
+-10 % rate with bursts lengthened or shortened by 200 us (what receiver
modules do), all codes decoded; glitches and a 3 ms "leader" ignored, a
truncated frame reported as an error with 13 bits, a 30 ms stuck-low line
survived.

**sigrok** (`ir_nec`, RTL at 456 kHz): on the raw LED carrier
`[('frame', 4, 8), ('repeat', None, None)]` and on the receiver module's
output the same, plus `Address# error: 0xBEEF` for the extended frame, which
this decoder does not know (nec_rx decoded it: address 0xBEEF, command 0x11).
On the model: `[('frame', 0, 69), ('repeat', None, None), ('frame', 165, 90), ('frame', 60, 195)] []`
both ways.

**Limits.** NEC only (no RC-5, RC-6, Sony, ...: each is another program).
The receiver needs a demodulating receiver module, as almost all IR receivers
do; the model of that module is ours. One code per FIFO word, pushed with
`push block`: a host that never reads would stall the receiver.

## I2S transmitter (`i2s_tx`)

**Implemented.** Philips I2S, 16-bit stereo, 32 BCLK per frame: SD changes on
BCLK's falling edge, WS one BCLK before the MSB. One FIFO word per frame (left
in the high half). 12 instructions in the loop, side-set driving BCLK and WS.
On underrun, `pull noblock` loads R0 (= 0): the frame is silence and BCLK/WS
keep running, where an autopull design (such as pico-extras' `audio_i2s.pio`)
stalls with the clocks frozen mid-frame.

**Timing.** Checker: every bit 2 ticks, every frame 64 (underrun included),
WS 32 ticks each way. 48 kHz at 50 MHz needs CLKDIV 16.28: a BCLK period is
32 or 33 clocks and the frame rate 47996.5 Hz (-0.007 %, the divider's 8-bit
fraction); at 49.152 MHz it is exactly 16. On the RTL, 40 samples then 44
silent words with the clocks running.

**sigrok** (`i2s`, RTL at 50 MHz): `sigrok i2s: 86 words, first [(21380.0, 'L', 40180), (31800.0, 'R', 44772)], warnings []`,
every sample equal to what was sent.

**Limits.** Transmit only, 16-bit samples in 16-bit slots, no MCLK output.
Other formats (left-justified, 32-bit slots, TDM) are small changes of this
program, not tested.

## Wiegand transmitter (`wiegand_tx`) and receiver (`wiegand_rx`)

**Implemented.** Transmitter: open-drain D0/D1 with pull-ups, a 0 is a pulse
on D0 and a 1 on D1, MSB first, pulse width PW, interval IV and frame gap FG
as defines (1 us ticks). n = 1..32 data bits with an even parity bit over the
first n/2 and an odd one over the rest, computed in firmware with the CRC tap
as a 1-bit LFSR (n = 24: the 26-bit H10301 format; n = 32: 34-bit), or raw.
Receiver: WAIT kind 7 (the two lines differ from idle) catches a pulse on
either line at clock resolution; a pulse must still be there 8 us later and
end within PMAX; both lines low is an error; a frame ends after FTO without a
pulse. It keeps the parity of all bits in the CRC tap and copies it after bit
13 and bit 17, so at the end it can validate the 26- and 34-bit formats.
Records: the first 32 bits, the rest, and a status word (n, parities, valid,
error).

**Timing.** Checker: pulses exactly PW, intervals exactly IV (all paths
equalised, parity bits included), the receiver samples 8 ticks after the
edge. Measured: pulses 50.000 us and intervals 2.000 ms (1 MHz model), 200 us
at 50 MHz. Receiver against the card-reader model: 20 us / 200 us, 50 us /
1 ms, 100 us / 2 ms and 100 us / 5 ms, each with 26-bit, 34-bit and 35-bit raw
frames; glitches of 2 us between bits ignored; a wrong parity bit, both lines
low, and a 70-bit frame (first and last 32 bits kept) handled.

**sigrok** (`wiegand`, RTL at 1 MHz, 200 us intervals):
`sigrok wiegand: ['00010101001111010011010011', '1000100100011010001010110011110001'] invalid=[]`,
the same bits the receiver decoded, both marked valid.

**Limits.** The transmitter sends at most 32 data bits (34 with parity); the
receiver validates only the 26- and 34-bit layouts (the parity split of other
formats is at a bit it does not record; the bits are reported anyway). Its
records use `push block`: a host that lets the RX FIFO fill (8 words, under 3
frames) stalls it, and it loses the start of the next frame (a test did,
before it drained).

## DShot600 (`dshot600`) and bidirectional DShot600 (`dshot_bidir`)

**Implemented.** DShot600: 16-bit frames (11-bit throttle, telemetry bit,
4-bit checksum computed in firmware as Betaflight does), 8 ticks per bit; the
last value repeats every FRAME ticks when the FIFO is empty (`pull noblock`
loads R0, which holds it), so an ESC sees a steady stream without the host.
Bidirectional: the inverted form (idle high, inverted checksum), then the pin
is released and the ESC's reply received on the same wire: 21 line levels at
750 kbit/s (5/4 of the frame rate), 20-60 us later. The receiver times every
edge with WAIT kind 7 ("the pin differs from its last level") and its timeout
counter, at CLKDIV 1, and turns each interval of 1, 2 or 3 bits into that
many levels: it re-synchronises on every edge, so the ESC's clock may be off.
The host turns the levels into telemetry (GCR 5b to 4b, eRPM exponent and
mantissa, checksum).

**Timing.** DShot600 checker: bit 8 ticks, T1H 6, T0H 3, frame period 600;
measured at 48 MHz: T1H 1250.0, T0H 625.0, bit 1666.7 ns exactly; at 50 MHz
(CLKDIV 10.42): 1240..1260, 620..640 and 1660..1680 ns, one clock either
way. Bidirectional at 48 MHz, CLKDIV 1: low 1250 / 625 ns, bit 1666.7 ns,
pin released 1298 clocks after the frame starts (checker and model). The
telemetry decodes for ESC clocks from 15 % slow to 15 % fast (fails from about
20 %: the thresholds sit at 1.5 and 2.5 bits and a run is at most 3), and
for 20, 30 and 60 us turnarounds; the ESC model never saw the chip driving
while it answered.

**sigrok.** It has no DShot decoder; its `pwm` decoder gives each bit's duty
cycle, from which the tests rebuild the frames and check their checksums:
`['0xbb88', '0x259e', '0xffff', '0x0']` (model), and on the RTL
`sigrok pwm -> DShot frames ['0x0', '0x7d0a', '0x617', '0xffee']`. For the
bidirectional frames, `pwm` with active-low polarity. The telemetry's only
judges are our ESC model and our GCR decoder, which share our reading of the
encoding (GCR table, `gcr = l ^ l >> 1`, checksum) from the Betaflight and
BLHeli_32 sources; it has not been checked against a real ESC.

**Limits.** At 50 MHz the bidirectional bit counts round to whole clocks
(83 / 31 / 63, +-0.8 %). The frame period of the bidirectional program
depends on when the ESC answers. DShot150/300/1200 are other CLKDIVs (or
defines); DShot1200 at 50 MHz would have one-clock steps of 6 % of T0H.

## Second round: DALI, Modbus RTU, MIDI, quadrature

Same rules (no change under `src/`, `flow/`, or to `tools/pe/{sim,isa,asm,line,timing}.py`),
same standard of evidence: pins-only models (`tools/pe/postfreeze2.py`),
sigrok, the static checker with a soundness run on the model for every
deadline (`test_post_freeze_timing.py`: every one of the 5 programs' 19
deadlines measured, none outside its bound), and an RTL test through the SPI
host port.

**Why these.** Each is widely deployed, has a public specification (or, for
DALI, a standard whose timing tables are widely reproduced), has a sigrok
decoder for an independent check, and leans on a different part of the
chip than the first round did:

* **DALI** (IEC 62386-101, building lighting): bi-phase coding in firmware
  (the line unit is core 0's and does 802.3-style Manchester), a half-duplex
  turnaround with a settling-time window, and a receiver that measures every
  interval between edges against the standard's tables, with a negative
  result (a violated frame) that the application must act on.
* **Modbus RTU** (industrial RS-485): the CRC tap on a reflected CRC
  (CRC-16/MODBUS), parity computed per byte, framing purely by silence (t1.5
  inside a frame, t3.5 between), an RS-485 driver-enable line, and a
  response of up to 256 bytes streamed to the host.
* **MIDI 1.0** (musical instruments): a byte-stream parser with state
  (running status, real-time bytes that may interrupt anything, SysEx of any
  length), branch-heavy code that must finish inside half a stop bit; and a
  transmitter that compresses with running status.
* **Quadrature encoder** (motors, knobs): no standard body, but the most
  common position sensor; it measures how fast the chip can follow two
  asynchronous inputs (WAIT kind 7 at full clock rate).

Not chosen: SENT (SAE J2716: the standard is not public, and sigrok has no
decoder), SDI-12 (no sigrok decoder), HDMI-CEC (sigrok has one; left for a
later round).

Sources of the numbers. Modbus: *Modbus over Serial Line Specification and
Implementation Guide V1.02* (modbus.org). MIDI: *MIDI 1.0 Detailed
Specification*. DALI: IEC 62386-101 ed. 2 is not freely available; its
transmitter and receiver timing tables (18, 19) are taken as reproduced in
Renesas application note R01AN4653 (RL78/G13 DALI-2 driver) and its
settling times (5.5..10.5 ms forward to backward, 13.5 ms between forward
frames at the highest priority) from public application notes. A DALI
certification lab would test against the standard itself.

### DALI application controller (`dali_ctrl`)

**Implemented.** A controller on one core: forward frames of 1..24 bits (16
for control gear, 24 for ed. 2 control devices), MSB first after a start bit,
bi-phase (a 1 is low then high), Te = 416.67 us per half bit; before each,
the bus must have been idle for 14.0 ms (33.6 Te; a bus that goes low
meanwhile, such as another controller, restarts the wait once it is idle).
If the host asks, it then receives a backward frame: the start bit's fall
must come within 10.5 ms of the forward frame's end; from there every
interval between edges is measured (a kind-7 WAIT against the last level,
with its timeout counter as the stopwatch, so an edge during the bookkeeping
is not lost) and classified as tables 18/19 ask, the limits in the grey
areas: a half bit 22..44 ticks (286..573 us at Te / 32; the standard accepts
333.3..500), a double half bit 45..83 ticks (586..1081 us; 666.7..1000), only a
half bit after a bit boundary or the start bit's fall. A frame ends with 2.6
Te of high line and must stay idle until 2400 us after its last edge (the
receiver's stop condition); anything else is a violation, reported with the
bits received so far, after which the firmware waits for 2400 us of idle
bus. Records: the bits (start bit included) and a status word with the bit
count, violation, no answer, and the settling time in ticks. 1 tick = Te / 32
(CLKDIV 651.04 at 50 MHz). 69 words.

**Timing.** Checker: every half bit 32 ticks, the last one too; at least
1087 ticks (14.16 ms) from the end of a forward frame to the next one when no
answer is expected; the receiver's bookkeeping between two interval WAITs is
exactly 8 or 11 ticks on every path (the constants its thresholds are built
on: P_B and P_M in the source; the paths were padded to make them equal, and
the checker proves it). Measured: at 50 MHz every run between edges 20833 or
20834 clocks (Te) or 41666..41667 (2 Te); forward frames 14.15..14.57 ms
apart (idle line between the last edge of one and the start of the next); a
controller's frame already on the bus delays ours to 14.14 ms after its last
edge. The settling time is reported 0..2 ticks (0..26 us) late (edge
detection: synchroniser plus the tick).

Against the gear model (`DaliGear`: decodes our frames with the standard's
receiver ranges, answers at its own half bit and settling time): answers
decoded with half bits of 336, 366.7, 416.67, 466.7 and 498 us, with low
phases 75 us longer or shorter than high ones (and 40 us at 380 and 450 us),
each at 5.5 and 10.4 ms settling time, 0x00/0xFF/0x55/0xAA/0x81/0x7E. (336
and 498 rather than 333.3 and 500: the model samples the bus every 3.3 us,
and its reference decoder must find the waveform legal.) Negative controls,
none giving an answer: 250 us half bits (violation, 0 bits), a 1300 us double
half bit (violation: table 19's 1200..1400 band), a bit without its mid-bit
transition (violation), a well-formed 5-bit backward frame (n = 6 reported,
not 9: the host rejects it), a reply 11.2 ms after the forward frame (no
answer; our next frame then waits for 14 ms of idle after it), the bus held
low for 3 ms (violation). Two gear answering at once (wired-AND): for 0x0F &
0xF0, 0x80 & 0x01, 0xFF & 0x00 and 0x01 & 0x03 the bus carries an illegal
waveform and the firmware reports a violation, as the reference decoder does;
two equal answers decode as that answer.

**sigrok** (`dali`, RTL at 307.2 kHz, CLKDIV 4: tick for tick 50 MHz):
`sigrok dali: [('forward', 'fe40'), ('forward', '0390'), ('backward', '5a'), ('forward', '0399'), ('backward', 'c3'), ('forward', '0590')]`;
the firmware: answers 0x5A and 0xC3 with 8021 us settling time (the gear's
8000 us + 20.8 us of detection), and "no answer" for 0x0590. On the model the
same with 0xFE7F, 0x0190 -> 0x42, 0x03A0 -> 0xFE, 0x0105.

**Limits.** A controller, not control gear or a bus power supply; no
multi-master collision detection while transmitting (ed. 2 controllers
must back off); no forward-frame priorities other than the fixed 14 ms
(priority 1 is 13.5..14.7 ms); 24-bit forward frames are sent but the
received frames are expected to be backward (8-bit) ones, any length up to
31 bits is reported; the stuck-bus wait has no timeout (the record of a
violation comes when the bus has been idle for 2400 us). The timing tables
were taken from secondary sources (above).

### Modbus RTU client (`modbus_rtu`)

**Implemented.** The serial-line layer of a Modbus client (master) on one
core: it waits for t3.5 of silence, enables the RS-485 driver (DE), sends n
bytes from the host (11-bit characters: start, 8 data bits LSB first, even
parity computed here, stop), then the CRC computed by the CRC tap, low byte
first, and releases DE after the last stop bit. The tap is set to POLY
0x8005, width 16, preset 0xFFFF, and fed by every data bit in wire order
(`out pins, 1` sending, `in r, 1` receiving; parity, start and stop bits are
read with `mov`/`jmp pin`, so they do not feed it). CRC-16/MODBUS is the
reflected CRC, and that order gives the bit-reversed register:
`mov osr, ::crc` (the bit-reverse MOV) and `out null, 16` put it in the
order the wire wants. Then, if the host asks (not for a broadcast), it
receives the response: the first start bit within RTMO * 4099 ticks
(100 ms; the 16-bit timeout counter needs the outer loop), every
character re-synchronised on its start bit and sampled at mid-bit, parity
checked, until t3.5 of silence after a stop bit; a character that starts
after t1.5 but before t3.5 marks the frame broken (the specification says
such a frame is discarded; the firmware keeps the bytes and flags it). The
CRC tap is preset again and fed the response; over a correct frame and its
CRC it ends at 0. Records: 3 bytes per word while receiving, then an end
word with the last 0..2 bytes and flags (parity, framing, t1.5 gap, CRC ok,
RX FIFO overflow). t1.5/t3.5 are defines in ticks: 16.5 and 38.5 bit times up
to 19200 baud, 750 and 1750 us above (the specification's fixed values).
Odd parity is a define; no parity with 2 stop bits is not supported. 119
words.

**Timing.** Checker, 19200 baud (TB = 104 ticks per bit, CLKDIV 25.04 at
50 MHz): every sent start, data and parity bit 104 ticks; a stop bit and the
space after it 105..111 ticks inside a request (+0.01..0.07 bit, the
parity computation of the next character falls in its start bit); the last
stop bit complete (104 ticks) when DE is released; received bits sampled 104
ticks apart, the first 52 after the start bit is seen; from a stop bit's
middle to watching for the next start bit 14..23 ticks (under half a bit).
Measured: at 50 MHz every edge of a request within -33..+17 ns of its
character's 19200 baud grid (the divider: 2604.06 clocks per bit for
2604.17, -0.004 %); the server model saw characters 0.5..3.5 us apart
(t1.5 is 859 us).

Against the server model (`ModbusServer`: decodes 11-bit characters, frames
by t3.5, checks the CRC with the specification's algorithm, answers):
requests of functions 3, 6 and 8 and an exception response, with responses
of every length mod 3 (the packing), a 255-byte response (125 registers)
drained while it arrives; the server 2 % slow or fast, with 0 or 1.3
characters of idle between its characters: all received, CRC ok, no flag.
Two requests whose CRCs are published in Modbus literature come out with
exactly those bytes: `01 03 00 00 00 0A` -> `C5 CD`, `11 03 00 6B 00 03` ->
`76 87`. Negative controls: a wrong CRC (not ok), a parity error (flagged,
CRC still ok: the data was right), a low stop bit (framing flagged), a
2.5-character gap inside the response (gap flagged, bytes and CRC otherwise
right), a 4-character gap (the frame ends at t3.5 after 3 bytes, CRC not ok),
no response (an end word with no bytes). Odd parity at 38400 baud: t1.5 = 750
us; a 1.0 ms gap is flagged, a 0.6 ms one is not.

**sigrok** (`uart` with even parity + `modbus`, the client's characters on
uart TX, the server's on RX, RTL at 1.9968 MHz, CLKDIV 1: tick for tick
50 MHz): `client->server ['Function 3: Read Holding Registers', 'CRC correct', 'Function 6: Write Single Register', 'CRC correct']; server->client` the same.
The firmware's records: `110306ae415652434049ad` and `1106000100039a9b`, CRC
ok; the server decoded `1103006b00037687` (the published example) and
`1106000100039a9b`.

**Limits.** The framing layer only: the host builds requests and parses
responses (function codes, exception responses, matching the server
address). One request at a time; 255 bytes before the CRC. The receiver does
not check the server's turnaround (the specification's t3.5 before a
response) and keeps a frame broken by a t1.5 gap rather than dropping it.
The response timeout is RTMO loops of 4099 ticks.

### MIDI receiver (`midi_rx`) and transmitter (`midi_tx`)

**Implemented.** Receiver: 8N1 at 31250 baud, every byte re-synchronised on
its start bit (a level WAIT, so a start bit that comes while the parser is
busy is not lost) and sampled at mid-bit, a low stop bit reported and the
byte dropped. The parser follows MIDI 1.0's rules for a receiver: running
status (data bytes after a complete channel message start another one with
the same status; 1 data byte for program change and channel pressure, 2 for
the others); system common clears running status (0xF1 and 0xF3 take 1 byte,
0xF2 2, 0xF4..0xF7 none); real-time bytes (0xF8..0xFF) may come anywhere,
inside another message or a SysEx, are passed on at once and change nothing;
a SysEx is 0xF0, data, and 0xF7 or any other status, which is then parsed;
data bytes with no status are dropped. One word per message (status, data,
count), SysEx data 3 bytes per word. A full RX FIFO drops the item and sets a
flag, so the receiver never stalls. TB = 128 ticks per bit (CLKDIV 12.5 at
50 MHz). 132 words. Transmitter: takes the same words and leaves out the
status byte of a channel message whose status equals the last one sent
(running status; system common and SysEx cancel it, real-time leaves it; a
define turns it off). TB = 32 (CLKDIV 50, exact). 58 words. In and out at
the same time take two cores: both cores of the IHP chip.

**Timing.** Receiver, checker: the start bit's middle 64 ticks after it is
seen, every bit 128 ticks after the last; the parser, from the stop bit's
sample to watching for the next start bit, at most 48 ticks of the 64 that
half a stop bit leaves (measured 14..39 over 105 bytes). The worst path is a
status byte that ends a SysEx holding 2 bytes and starts a channel message.
At TB = 64 (CLKDIV 25, exact) it would not fit (48 > 32), which is why TB is
128. Transmitter: every start and data bit 32 ticks (32.000 us at 50 MHz);
stop bit and space 40..61 ticks with the host keeping up.

Against the reference receiver (`midi_parse`, written from the
specification and checked against the generator: parse(encode(events)) =
events with and without running status, and the specification's example
`90 3C 40 3E 40` is two note-ons): two random streams of every message kind
with running status and 25 real-time bytes inserted anywhere, bytes back to
back (153 bytes -> 76 messages, 3 SysEx and 28 real-time; 139 -> 76), equal;
a sender 2 % slow or fast (MIDI allows 1 %), equal. Negative controls: data
with no status, data after a system common message, a message cut short by a
new status: dropped as the reference drops them; a low stop bit: the byte
reported as a framing error, the stream resumes (running status then pairs
the data bytes that remain, as the reference does with the byte removed);
a host that stops reading: the first 8 messages kept, the rest dropped, flag
set, and the receiver keeps going (everything after the host drains
correct). Transmitter: the bytes on the wire equal the reference encoder's
with running status (a random mix: 92 bytes for 109 without) and without
(105). Loopback (midi_tx -> virtual pin -> midi_rx): 40 messages of every
kind, the same 40 out.

**sigrok** (`uart` + `midi`). Transmit, RTL at 4 MHz (midi_rx CLKDIV 1,
midi_tx CLKDIV 4) with midi_rx receiving the same wire: `sigrok midi:
["Channel 1: note on (note = 60 'C4', velocity = 100)", "Channel 1: note on
(note = 64 'E4', velocity = 100)", 'System Realtime: timing clock', "Channel
1: note on (note = 67 'G4', velocity = 100)", 'Channel 2: program change to
instrument 13 (assuming Marimba)', 'Channel 1: pitch bend change 0x00 0x48
(9216)', 'System Common: song position pointer 0x10 0x02 (272)', "system
exclusive: for 'Universal Non-Realtime' with payload 0x7f 0x09 0x01 ",
'System Realtime: start', ...]`: 28 bytes on the wire for 31 without running
status, and midi_rx's 11 messages equal the 11 sent. Receive, on the model:
sigrok decodes the stream played into midi_rx (67 note and pitch-bend messages with
running status, 12 timing clocks inside messages); its messages equal the
firmware's, in the same order apart from the clocks (sigrok places a message
at its first byte, before a clock that came inside it; the firmware reports
it when complete).

**Limits.** No active-sensing timeout (a receiver may treat 300 ms without
any byte as a lost connection: the host's job). midi_tx does not insert
real-time bytes into a message (the host orders them between messages) and
never refreshes running status. Undefined real-time bytes (0xF9, 0xFD) are
passed on.

### Quadrature encoder counter (`quad_enc`)

**Implemented.** X4 decoding of A and B on one core at CLKDIV 1: every edge
of either line is a step, forward when A leads B; the direction is the old B
XOR the new A. WAIT kind 7 compares the two lines with the last state read,
on every clock, so an edge that comes while the previous step is being
counted is caught by the next WAIT; a pulse that is over before the state is
read counts nothing; both lines changed between two reads (a missed step)
sets a flag and does not move the position. The position is R3, which the
host reads through the core's register (16 bits, wrapping). 20 words.

**Timing.** Checker: 14 clocks per forward step, 15 backward (from the WAIT
completing to the next WAIT, 8..15 ticks; measured 8..15 over 809 steps).
Measured at CLKDIV 1, steady rotation with a step every n clocks: exact for
n >= 14 forward and n >= 15 backward; at 10..13 (14 backward) steps are
missed and the flag is set every time (the negative control). At 50 MHz:
3.3 M steps/s, an 830 kHz quadrature signal. A 5000-step random walk at
15..60 clocks per step, 1- and 3-clock glitches, a double step, and 40000
steps (the 16-bit wrap): all as the encoder model moved.

**sigrok** (`graycode`): RTL at 50 MHz, a 1500-step random walk: the
firmware's position 76, the encoder's 76, sigrok's count 76 (1501 count
changes, the double step counted as 0 by both); on the model a 600-step walk
-42 = -42.

**Limits.** No index (Z) input, no edge filter beyond "the state must have
changed when it is read", no count capture: the host polls R3. Steps closer
than 14/15 clocks on average are lost (and flagged).

## Third round: HDMI-CEC, ISO 7816-3, SMBus with PEC, RC-5

Same rules (no change under `src/`, `flow/`, or to `tools/pe/{sim,isa,asm,line,timing}.py`),
same standard of evidence: pins-only models (`tools/pe/postfreeze3.py`),
sigrok, the static checker with a soundness run on the model for every
deadline (`test_post_freeze_timing.py`: every one of the 6 programs' 43
deadlines measured, none outside its bound), and an RTL test through the SPI
host port for each protocol.

**Why these.** Each is widely deployed, has a public specification (or, for
ISO 7816-3, a standard whose character timing is reproduced in public
documents), has a sigrok decoder, and leans on parts of the chip the first
two rounds did not:

* **HDMI-CEC** (televisions, players and receivers on HDMI): a multi-master
  open-drain line where every device is initiator and follower. Bit-level
  arbitration (an initiator that sees a 0 where it sent a 1 stops and goes
  on as a follower, inside that bit), a follower that writes into the
  initiator's bit (its ACK holds the line low past the initiator's own
  pulse), a signal free time that depends on who sent last. It needs both
  roles in one program: 244 of a bank's 256 words.
* **ISO 7816-3 T=0** (SIM, bank and identity cards): half-duplex characters
  with an error signal inside the guard time and repetition both ways, two
  bit orders and polarities (direct and inverse convention, decided by the
  first character), and a clock the reader gives the card, with every bit a
  fixed number of its periods. It exercises the bit-reverse MOV, two cores
  whose dividers must stay in an exact ratio, and reading back our own line
  while sending.
* **SMBus with PEC** (battery packs, power supplies, PMBus, temperature and
  memory sensors): I2C transactions plus a CRC-8 over every byte, the
  address bytes included, a length the device decides (block reads), and a
  25..35 ms clock-low timeout. It uses the CRC tap as a non-reflected CRC-8
  fed by OUT and IN in one transaction, with a residue check.
* **RC-5** (Philips infrared remote controls): bi-phase on a 36 kHz carrier
  with a toggle bit that is state kept from frame to frame. The carrier must
  keep its period across half bits (no instruction to spare between them),
  and the receiver re-synchronises on every bit's own transition.

Not chosen: PS/2 host and an SPI flash command layer (mostly host work on
top of round one's PS/2 device and SPI masters), RC-6 (the same machinery as
RC-5 plus a leader and a double-length trailer bit: another program), S/PDIF
(bi-phase mark at 2.8 Mbit/s and more), SDLC/HDLC (the line unit stuffs a 0
after five 1s, as HDLC does, but it stuffs everything queued, a 0x7E flag
included, and only the host can rewrite LINECFG between flags and data: a
reading of isa.md section 4.7, not tried).

Sources of the numbers. CEC: *High-Definition Multimedia Interface
Specification 1.3a*, Supplement 1 (CEC), bit timing and arbitration; the same
values are in sigrok's `cec` decoder (quoted in pe.postfreeze3). ISO 7816-3:
ISO/IEC 7816-3:2006 is not free; the character timing used here (12 etu,
error signal 10.5 +-0.2 etu for 1..2 etu, repetition >= 2 etu after it, 16
etu between characters in opposite directions, answer to reset within
400..40000 clock cycles) is as in EMV Book 1 (free from EMVCo) and smart-card
reader application notes. SMBus: *System Management Bus Specification 3.2*
(sbs-forum.org); the CRC-8 check value from the CRC catalogue (CRC-8/SMBUS,
"123456789" -> 0xF4). RC-5: Philips' definition as widely reproduced (36 kHz,
889 us half bit, 14 bits, 113.778 ms repetition) and sigrok's `ir_rc5`. A
certification lab would test against the documents themselves.

### HDMI-CEC device (`cec`)

**Implemented.** One program is a whole CEC device on one core. Follower,
always: a start bit must be low 3.5..3.9 ms with the next fall 4.3..4.7 ms
after its fall (2 ticks of margin each way); every bit is sampled 1.05 ms
after its fall, the line must be high again by 1.85 ms, the next fall must
not come before 2.0 ms and, missing at 2.8 ms, ends the frame. The header's
destination is compared with our logical address, kept in the CRC register
(the taps off: the fifth register); frames to us are acknowledged by
holding the ACK slot low from the fall to 1.5 ms; every block on the line is
reported. Initiator, when the host queues a frame: it waits until the line
has been idle for the frame's signal free time (the host gives it: 3, 5 or 7
bit periods, for a retry, a new initiator, the same initiator again), sends
the start bit and the blocks, checks at each 1 it sends that the line is
high at 1.05 ms (else another initiator sent a 0: it stops driving, reports
the loss at the end of the frame, and from that very bit goes on as a
follower: it receives the winner's frame and acknowledges it if it is for
us), and ends the frame on a NACK (directed) or a rejection (broadcast). The
data words of a frame that was lost or refused are pulled from the TX FIFO
(a 29-tick constant path, as it runs between two bit samples). No retries
in firmware: the host resubmits (CEC allows up to 5 for a NACK). 1 tick =
10 us (CLKDIV 500 at 50 MHz, exact). 244 words.

**Timing.** Checker: start bit low 370 ticks, next fall 450; every bit we
send 240 ticks apart, a 1 low 60, a 0 low 150, our sample at 105; the end of
our last bit 240 ticks after its fall (the signal free time counts from
there). Follower: the sample 105 ticks after the fall is seen (the WAIT's
completion: 0..1 tick and the synchroniser's 2 clocks after the edge), our
ACK driven 6 ticks after it and released at 150, every bookkeeping path
exactly 19 ticks (the windows are measured by the WAIT's counter from that
point: the paths were padded, the checker proves them equal), a lost
arbitration joins the follower 151 ticks after the fall, as it would after
its own release; the idle loop 31..33 ticks (it counts 31 per turn).
Measured at 50 MHz: 185000 / 225000 / 30000 / 75000 / 120000 clocks, exact;
on the RTL at 100 kHz, 3.7 / 4.5 / 0.6 / 1.5 / 2.4 ms. Signal free time,
from the nominal end of the previous frame: 7.30 ms for 3 bit periods (7.2),
12.28 ms for 5 after another initiator's frame (12.0; the end of a received
frame is known only when no fall came by 2.8 ms, and the idle count starts
conservatively), 16.90 ms for 7 (16.8).

Against the device model (`CecNode`: a follower with the receiver's ranges
that acknowledges its own address; an initiator with its own timing that
samples its 1s and checks ACKs): our frames (a poll, a directed command, a
broadcast, a 16-block frame) decoded and acknowledged by the TV model, our
results ok; the TV's frames to us acknowledged by the firmware, to address 5
not (the TV stops after its header, as CEC asks), broadcasts received;
initiators at the ends of the receiver's ranges (start low 3.5 and 3.9 ms
with the next fall at 4.3 and 4.7, a 1 low 0.4 and 0.8, a 0 low 1.3 and
1.7, bits 2.05 and 2.75 ms apart, and the two corners): every frame received
and acknowledged. Arbitration: the TV starts with us (0x04 against our
0x40): we see its 0 at our second header bit, stop driving, receive its
frame and acknowledge it, drop the 2 unsent data words of our 10-block frame
and report the loss; the host's retry (3 bit periods) goes through. The
other way (an initiator at address 8, 0x80 against 0x40, loses at the first
bit): ours goes through. Negative controls: a 3.0 ms "start bit" and the line
held low for 5 ms ignored; a bit 1.9 ms after the last one ("short"), a frame
that stops 5 bits into a block ("partial"), a bit held low for 3.6 ms (a
follower's error notification, "low"), then a good frame; a poll to an
address nobody has: NACK.

**sigrok** (`cec`, RTL at 100 kHz):
`sigrok cec: ['40:04' 'HDR: Playback_1, TV | OPC: IMAGE_VIEW_ON | R: ACK', '4f:82:10:00' 'HDR: Playback_1, Broadcast | OPC: ACTIVE_SOURCE | OPS: 0x10, 0x00 | R: ACK', '4b' 'HDR: Playback_1, Playback_3 | OPC: PING | R: NACK', '04:8f' 'HDR: TV, Playback_1 | OPC: GIVE_DEVICE_POWER_STATUS | R: ACK']`;
the firmware: ok, ok, NACK for its own three, and the TV's frame received
with "to us". On the model the same frames and a 2-block retry after a NACK.

**Limits.** One logical address (a device with several would compare a
mask: more words, and there are 12 left); the signal free time is the
host's number; as a follower it reports a timing error but does not send an
error notification (the 3.6 ms low), and a notification during our own frame
looks like a lost arbitration (the follower path then reports the long
low); the host must push a frame's words together and only when the FIFO
has room for them (the firmware does not wait for data in mid-frame), 16
blocks at most (CEC's own limit); the follower's windows are a little wider
than the receiver's ranges (2.0..2.8 ms for 2.05..2.75), for the detection's
tick. No CEC application layer (opcodes, physical addresses, logical
address allocation by polling): that is the host's.

### ISO 7816-3 T=0 reader (`iso7816`) and card clock (`iso7816_clk`)

**Implemented.** The reader's character layer on core 0; the card's clock
on core 1, one instruction (`pin CLK, toggle` every tick): a core cannot
make a continuous clock while it does anything else, so ISO 7816 takes both
cores of the IHP chip. The dividers are locked: the clock core at CLKDIV D
makes CLK = f / 2D; the reader at 6D with 124 ticks per etu, so one etu is
exactly 372 CLK periods (Fi = 372, Di = 1; one CTRL write starts both).
Commands from the host: RESET (RST low for 2 etu = 744 CLK, >= 400; I/O
released; the convention cleared), RECV m characters (the first within a
timeout in units of 8 etu, 112 etu for an answer to reset, which must start
within 40000 CLK; then WT between characters; optionally the first is TS:
3B is the direct convention, 3F the inverse, kept in flag FINV, anything
else an error), SEND n bytes with N extra guard etu (after TURN = 6 etu of
idle line, which with the end of a RECV gives >= 16 etu after the card's
last character), DEACT (RST and I/O low). Every character's even parity is
computed or checked; in the inverse convention the levels are complemented
and MSB first: `in r, 8` + `mov r, ::isr` + `xor r, 0xFF` (the bit-reverse
MOV through the ISR). Receiving, a parity error is signalled (I/O low from
10.5 etu for 1.5 etu) and the repetition awaited, up to RMAX = 4 times; then
the character is kept and flagged. Sending, I/O is sampled at 11 etu, and on
an error signal the character is sent again 2 etu after the signal ends, up
to RMAX times, then the command is aborted. Records: each character with its
error count, and an end word per command (counts, timeout, bad TS,
convention, repetitions, aborted). The T=0 procedure bytes (INS, NULL 60,
SW1 SW2) are the host's (`pe.postfreeze3.t0_parse`): for a command that
reads data it queues SEND header + RECV Le + 3 at once, since the card may
answer its procedure byte with data at once. 218 + 1 words.

**Timing.** Checker: every bit we send 124 ticks (1 etu); I/O released at
10 etu and sampled at 11; the next character >= 12 etu after the last (+ N,
a register: only the minimum is bounded); a repetition >= 2 etu after the
error signal ends. Receiving: the start bit's middle 62 ticks after it is
seen, every bit 124 ticks after the last; our error signal 123..131 ticks
after the parity sample (10.49..10.56 etu after the start; the standard's
10.5 +-0.2), 1.5 etu long; the next start bit watched for 30..46 ticks after
the parity sample (before 9.9 etu). CLK: one tick per half period. Measured
on the card model's own clock: the reader's characters exactly 12.000 etu
apart (13 and 17 with N = 1 and 5); its error signals from 10.500 etu for
1.500 etu; repetitions 14.05 etu after the previous start (2 etu after the
card's 1.5-etu error signal ends; the standard asks >= 2 etu after the
error is detected, which allows 13); our first character 18.2 etu or more
after the card's last (the standard: >= 16; the card model answers 20 etu
after our last). At 50 MHz (clock core CLKDIV 6: CLK 4.1667 MHz;
reader 36): every edge of a SEND on the 4464-clock etu grid.

Against the card model (`IsoCard`: times everything by counting the CLK it
is given, samples at mid-etu, checks parity and signals errors, repeats
what the reader rejects, runs READ and UPDATE BINARY): the answer to reset
in both conventions (`3B B4 11 00 00 57 4C 47 4F` and its 3F form), READ
BINARY (case 2) and UPDATE BINARY (case 3) through the procedure bytes, two
NULL procedure bytes, an unknown instruction (6D 00), the inverse convention
through a whole exchange; the card corrupting a character twice (two error
signals, the right byte), or more often than RMAX (four signals, the fifth
copy kept and flagged); the card rejecting our bytes once and twice
(repetitions counted, the right bytes stored) and six times (the command
aborted after five sends); a mute card (the first-character timeout); a
first character that is not TS (reported, receiving stops); deactivation.

**sigrok** (`uart` with even parity at f_CLK / 372 baud, I/O on the RTL at
4 MHz, CLK 2 MHz): `sigrok uart: 3bb4110000574c474f 00b0001002 b0 10 10 11 9000 [Frame error]`
(the answer to reset; the READ BINARY header; INS; the data byte the card
sent with a wrong parity bit, our error signal, which makes the decoder's
stop-bit sample low: the one frame error; its repetition; the second byte;
SW1 SW2). The firmware's records: the same bytes, 1 error signal on the
first data byte. On the model the same with 3 data bytes.

**Limits.** T=0 characters only: procedure bytes, GET RESPONSE, PPS (another
F/D is another TB and CLKDIV pair) and T=1 (blocks, LRC/CRC) are the
host's or not done; no parsing of the answer to reset (the host knows its
length from T0 and TDi, `pe.postfreeze3.atr_length`; a RECV ends after WT);
WT is a define (9600 etu for a real card; the tests use 32 so that the end of
an answer is found quickly, and the checker unrolls that loop, slowly at
9600); VCC and stopping the clock are the host's; one card; both cores. The
repetition comes 2 etu after the card's error signal ends rather than after
it starts (later than needed).

### SMBus host with PEC (`smbus_host`)

**Implemented.** One transaction per host header: START, address + W, nw
bytes (one per FIFO word), then, if nr: a repeated START, address + R, the
bytes read (a block read's count is the device's first byte), the PEC
appended after the last byte written or checked after the last byte read;
the last byte read is not acknowledged. That covers quick command, send and
receive byte, read and write byte and word, block read and write, process
call and block process call. The PEC is the CRC tap (POLY 0x07, width 8,
both taps): only data bits go through 1-bit shifts (`out r1, 1`, then `mov
pins, r1` drives SDA; `in pins, 1`), ACK bits are driven and read with
`mov`, so the register is CRC-8 over every byte, address bytes included, and
0 after a correct received PEC. Every SCL release waits for SCL high (clock
stretching) for at most TTO = 25 ms; longer, the transaction is abandoned
with both lines released ("timeout"; SMBus devices reset at T_TIMEOUT).
Every 1 we send is checked at SCL high: a 0 there is another master's, and
we let go of both lines ("lost"). The bus must be idle before the START, or
after TTO "busy". The data words of a failed transaction are drained. 189
words.

**Timing.** Checker (CLKDIV 56 at 50 MHz: 1.12 us ticks): SCL low 5 ticks
(5.6 us) inside a byte, up to 22 between bytes; high 4..5 ticks without
stretching (4.48..5.6 us), 10 around a repeated START; SDA changes >= 1 tick
after SCL falls; START hold 4 ticks (4.48 us), STOP setup 6 (6.72 us), bus
free >= 26 ticks; the timeout gives up 22324 ticks (25.003 ms) after
releasing SCL. Measured on the model: SCL high >= 4.48 us, low >= 5.60 us,
bit periods >= 10.08 us (99.2 kHz); the timeout's record 25.013 ms after SCL
was released (pushed 9 ticks after giving up); the device's own reset after
35.000 ms of SCL low.

Against the device model (`SmbusDevice`: computes the PEC of what it saw,
appends it to reads, checks it on writes, stretches the clock, resets after
35 ms of SCL low): every protocol above with PEC (writes: the device found
the PEC right every time; reads: the firmware did), and without; a PEC made
wrong by one flipped bit, and a device without PEC answering a read that
expects one: "pec_bad"; an absent address (NACK, the 4 data words dropped);
a device that refuses a data byte (NACK, the rest dropped, the next
transaction right); stretching of 3 us, 50 us and 2 ms after every ACK; a
40 ms hold (the firmware gives up at 25 ms, the device resets at 35); a bus
held busy; another master that wins the arbitration at our 6th address bit.
The PEC function itself gives the CRC catalogue's check value.

**sigrok** (`i2c`, RTL at 3.571 MHz with the device stretching 20 us after
every ACK): `sigrok i2c: ['1609785686', '1609173412b8', '16201705576972656c91', 'a0']`
(each transaction's bytes from the address on: a write word, a read word, a
block read, a poll); the PEC recomputed over sigrok's bytes equals the last
byte of each of the first three. The firmware: ok, pec_ok, pec_ok, NACK.

**Limits.** A host only (no SMBus device side); no SMBALERT#, address
resolution protocol or host notify; T_LOW:SEXT and T_LOW:MEXT (cumulative
stretching per message and per byte) are not checked, only each wait's
25 ms; the block count is not bounded (up to 255, as SMBus 3 allows); one
byte per FIFO word (four times the SPI traffic of packed words, so that the
words of a failed transaction are easy to drop); the host must keep the FIFO
fed during a write (meanwhile the firmware holds SCL low: master clock
stretching, which SMBus limits to 10 ms per byte); the 100 kHz class only.

### RC-5 infrared transmitter (`rc5_tx`) and receiver (`rc5_rx`)

**Implemented.** Transmitter: 14-bit frames (S1, S2 = the complement of
command bit 6 for RC-5X, the toggle bit, 5 address and 6 command bits), the
toggle bit kept in firmware (it flips for a new key and stays for a repeat
word), 28 half bits on the 36 kHz carrier, frames 64 bits apart while the
host keeps the FIFO fed. Each carrier period is three instructions, the
same in a burst and in a space: `mov pins, r1` (on for a burst, off for a
space) and two that hold the pin low by side-set (SIDE_OPT); the bookkeeping
between half bits (the next level, the counter) takes the two off ticks of
each half's last period, so the carrier keeps its period across half bits
and a 1 followed by a 0 is one burst of 64 periods. The end of the frame:
with PULL_THRESH = 29, `jmp !osre` sees the OSR empty in the 29th half (a
space). Receiver: from S1's mid-bit edge (the first fall of the receiver
module's output), every bit is sampled 3/4 bit after the last mid-bit edge
(in its first half: its value), and its own mid-bit edge awaited by an edge
WAIT started there, which ignores the boundary edge before it and accepts
the mid-bit edge 3/4..5/4 bit after the last one: each bit re-synchronises.
49 and 33 words.

**Timing.** Checker: the carrier's period 3 ticks everywhere, across half
bits too, on for 1 (108 kHz ticks: CLKDIV 462.96 at 50 MHz); every half bit
96 ticks (32 periods, 888.9 us); frame to frame 12288 ticks (64 bits,
113.78 ms). Receiver (128 ticks per bit): each sample 96 ticks after the
mid-bit edge, the edge WAIT armed 3 ticks after the sample, the record 3..4
ticks after the last edge. Measured: on the model at 432 kHz the same, and
bursts of exactly 32 and 64 periods; at 50 MHz carrier periods of 1388 or
1389 clocks (35999.8 Hz on average), on for 462 or 463.

Against the remote model (`rc5_envelope`: its own half bit, bursts stretched
or shrunk as receiver modules do) into rc5_rx: 10 % fast and slow, bursts
150 us longer and shorter, and both together: every frame with its toggle
bit; negative controls: a 200 us glitch ignored, a frame cut after 8 bits
and a bit without its mid-bit transition reported as errors, then a good
frame. Loopback (rc5_tx -> LED -> the 36 kHz receiver-module model ->
rc5_rx, on one chip): random keys and repeats, every address, command and
toggle bit as sent.

**sigrok** (`ir_rc5` on the receiver module's output, RTL at 108 kHz):
`sigrok ir_rc5: [(0, 12, 1), (0, 12, 1), (5, 53, 0), (20, 6, 1)]` (address,
command, toggle; the decoder's "standard" protocol has 6 command bits: the
RC-5X command 70 is its 6, with S2 = 0); rc5_rx: (0, 12), its repeat with
the same toggle, (5, 53) toggled, (20, 70). On the model (0, 12, 1), (0, 12,
1), (5, 53, 0), (31, 63, 1).

**Limits.** RC-5 only (RC-6's leader, mode bits and double-length trailer
are another program); the receiver needs 2 bit times of idle line before a
frame (a frame that begins sooner after the core starts is missed: the
loopback test sends its first key 10 ms in); each bit re-synchronises, so a
sender may drift +-25 % per bit (the tests check +-10 % with 150 us of burst
distortion); every repeat frame is reported (the host compares the toggle
bit); the transmitter's frame begins 139 ticks (1.29 ms) after its word is
pulled (the half bits are built first), then every 113.78 ms.

## ISA limitations found

What the challenge ran into, for a future revision. None stopped a protocol;
each cost instructions, registers or generality. (Items 11..16 come from the
second round, 17..25 from the third.)

1. **No `jmp !t`.** T (the WAIT timeout flag) has a positive JMP condition
   only, unlike Z and C. `nec_rx` was first written with `jmp !t` and had to
   branch the other way; every "timed out vs not" decision is laid out around
   `jmp t`.
2. **ALU immediates are 13 bits** (0..8191). LIN's mask 0xF300 and the
   record tag 0x2000 need an `ldi` into a scratch register first.
3. **Four general registers.** The LIN responder needs a bit time, a sum, the
   PID, a byte count, a byte, a delay counter and a bit counter: the bit time
   lives in the CRC register (taps off, width 16: a fifth register, but only
   when the CRC tap is not needed), the byte count shares the PID's register
   (its high byte), and the bit counter is a marker bit shifting through the
   byte register. The host's staged response sits in the OSR between frames.
4. **No data memory or table lookup.** The LIN PID parity takes about 25 ALU
   instructions where a 64-entry table would take one lookup; the
   bidirectional DShot GCR decoding is left to the host (a computed-jump table
   would cost about 64 words).
5. **Shift counts are immediates.** Wiegand's transmitter needs the host to
   left-align its data; the receiver cannot split the parity halves at n/2
   after the fact, so it snapshots the parity at bits 13 and 17 (the 26- and
   34-bit formats) and validates only those.
6. **16-bit registers, 32-bit shift registers.** `mov osr, r` zero-extends:
   DShot needs `out null, 16` to left-align its frame, NEC builds its 32 bits
   through the ISR (`in r1, 16; in r2, 16; mov osr, isr`).
7. **One tick rate per core, set by the host.** Bidirectional DShot sends at
   600 kbit/s and receives at 750: it runs at CLKDIV 1 with every time in
   clocks, so its defines depend on the clock frequency.
8. **Fractional-divider steps.** A tick is INT or INT + 1 clocks: I2S's BCLK is
   32 or 33 clocks at 50 MHz, DShot's edges move by 20 ns. Harmless here,
   visible in every measurement.
9. **Blocking PUSH in receivers.** A receiver whose RX FIFO is full stalls and
   loses the next frame's start (`wiegand_rx`, `nec_rx`); `lin_responder` uses
   `jmp rxf` + `push noblock` + a flag instead. The FIFOs are 8 words.
10. **One link register.** No problem in practice (`lin_responder` tail-calls
    with `jmp` to avoid a nested CALL).
11. **The WAIT timeout counter is the only stopwatch, and it runs only while
    waiting.** `dali_ctrl` measures intervals between edges with it, so the
    instructions between two WAITs must be counted by hand and every path
    padded to the same length (7 or 10 instructions; the checker proves
    them). A free-running tick counter readable by MOV would remove the
    padding and the constants.
12. **16-bit timeout counters.** Modbus's 100 ms response timeout is 200000
    ticks at 19200 baud with 104 ticks per bit: an outer loop around a level
    WAIT (a level WAIT, so no edge falls between two of them).
13. **No parity or population count.** Even parity takes 11 ALU instructions
    per sent byte (a fold of `mov`/`shr`/`xor`), placed inside the start bit;
    received bytes accumulate it with an `xor` per bit. Only 1-bit shifts feed
    the CRC tap, which helped here (the parity bit is read with `mov`, so it
    stays out of the CRC).
14. **Registers again.** `modbus_rtu` keeps its receive status in the OSR,
    `midi_tx` its running status in the ISR and the message's tag in the
    OSR, `dali_ctrl` the window counter in r0 for the whole backward frame
    (the settling time is reported from it).
15. **No table lookup (again).** MIDI's data-byte count per status is a
    chain of compares; with the SysEx bookkeeping the worst parser path is
    48 ticks, which forced 128 ticks per bit (CLKDIV 12.5 at 50 MHz) so that
    it fits in half a stop bit; a lookup would allow 64.
16. **Two cores in the tapeout.** A MIDI port in and out at once, or any
    full-duplex pair of these programs, uses both cores; nothing is left for a
    third protocol. Each program alone fits one core's 256 words (at most
    132).
17. **A clock output costs a core.** ISO 7816's card clock must run while
    the reader does everything else; a core can only make it with a
    one-instruction loop, so the reader takes both cores of the IHP chip. A
    divider output on a pin (or a PWM) would leave a core free.
18. **The line unit cannot do infrared bi-phase.** Its Manchester receiver
    tracks bits of at most 4095 clocks (an RC-5 bit is 88900 at 50 MHz), its
    transmitter's symbol divider is 8 bits of clocks, it has no carrier, and
    it is core 0's: RC-5 (like DALI) is all firmware. Its HDLC-style bit
    stuffing would stuff a 0x7E flag as well, and only the host can rewrite
    LINECFG between flags and data (a reading of isa.md, not tried).
19. **No way for a core to drop queued words.** A CEC frame that loses
    arbitration or is refused leaves its unsent data words in the TX FIFO;
    the firmware pulls them itself, so it computes how many (a fixed
    29-tick path, as it runs between two bit samples); `smbus_host` takes one
    byte per FIFO word so that the count is the bytes left. A core-side TX
    FIFO clear would do it in one instruction.
20. **The bit-reverse MOV reverses 32 bits.** `mov r, ::r` reverses a
    register's zero-extended 32 bits, so a byte ends outside the 16-bit
    register; the inverse convention's reverse goes through the ISR (`in r,
    8` puts the byte at the top, `mov r, ::isr` brings it back reversed):
    three instructions and the ISR.
21. **No parity (again)**, now for every character in both directions of
    ISO 7816: a three-step fold (11 instructions) to send, an XOR per bit to
    receive.
22. **Registers (again).** The CRC register holds CEC's logical address and
    ISO 7816's N or m and its error count (the taps off); `smbus_host` keeps
    the header in the ISR and its flags in the OSR in the read phase; CEC's
    r1 packs four flags, the block index and the blocks left; RC-5's toggle
    bit has a register to itself for the program's life.
23. **The stopwatch (again).** The CEC follower measures the next bit's
    window with the WAIT's counter from a known point, so its bookkeeping is
    padded to 18 ticks on every path, as DALI's was (item 11).
24. **No `jmp osre`.** Only `!osre` exists; RC-5's carrier loop, three
    instructions per period with none to spare, finds the end of the frame
    with `jmp !osre` to the next-but-one instruction and a `jmp` out as its
    fall-through, at the cost of a word; as with T (item 1), the negated
    form would save it.
25. **ALU immediates (again).** CEC's record marker (0x4000) and RC-5's S1
    (0x2000) are above 8191: a different record layout, an `ldi` first.

What helped in the third round: the CRC tap fed by OUT and IN alike (the
SMBus PEC over bytes sent and received in one register, checked by its 0
residue), edge WAITs that count only edges after they start (RC-5's
receiver ignores the boundary edges by starting its WAIT at 3/4 bit),
side-set with SIDE_OPT next to `mov pins` on the same pin (RC-5's carrier:
on from a register, off from side-set, room for bookkeeping in the off
ticks), open-drain pins read back at any time (CEC's ACK and arbitration,
SMBus's arbitration and clock stretching, ISO 7816's error signals both
ways), two cores' dividers in an integer ratio started by one CTRL write
(ISO 7816's etu locked to its own CLK), `pull noblock` to discard words, the
bit-reverse MOV again (ISO 7816's inverse convention), global flags as state
the host can see (CEC's lost arbitration across a frame, ISO 7816's
convention, which the host may also set), 256 words per core (CEC's 244).

What helped in the second round: the bit-reverse MOV (`mov osr, ::crc`
turns the tap's register into the reflected Modbus CRC in one instruction),
the CRC residue (0 over a correct frame and its CRC: one compare), WAIT kind 7
(the DALI receiver's edge timing without a missed edge, the encoder's two
lines at clock resolution), level WAITs with timeouts for start bits (a late
parser delays the samples a little instead of losing a byte), the host's
direct access to R3 (the encoder needs no FIFO protocol), global flags as
sticky error bits.

What helped: timeouts on every WAIT (no receiver here can hang on a dead
line), level WAITs with timeouts as interval timers, WAIT kind 7 (Wiegand's
two lines in one instruction, bidirectional DShot's edge timing), the timeout
counter as a timestamp (LIN's sync measurement across four WAITs), `pull
noblock` loading R0 (I2S silence, DShot's repeated value), the CRC tap as a
1-bit parity LFSR (Wiegand), 256 words per core (the LIN responder's 191).

## Tool limitations found

* The **static checker** cannot bound a loop whose count is a run-time
  measurement (the LIN responder's T) or follow a relation between registers
  (DMX512's BREAK-to-BREAK budget against the slot count), and does not track
  the CRC register's value. Those timings are measured on the model instead,
  and said so in the firmware headers.
* **sigrok 0.5.3**: `dmx512` (above), `ir_nec` knows only standard NEC
  (extended addresses are an "Address# error"), there is no DShot decoder,
  `wiegand` frames bits by a fixed idle time (`bitwidth_ms`), and `lin`
  closes a frame after two idle UART frames. Second round: `dali` decodes
  only 16-bit forward and 8-bit backward frames (not ed. 2's 24-bit ones);
  `modbus` on a single line tries every frame both as a request and as a
  response, so the tests give it the client's and the server's characters
  on separate channels; `midi` places a message at its first byte, so a
  real-time byte that came inside it is listed after it; `graycode`
  annotates a count only when it changes (the last value shows after one
  more step), and sigrok-cli aborts at exit after it ("free(): invalid
  pointer", its output complete: `pe.sigrok.decode(abort_at_exit_ok=True)`
  accepts exactly that); the capture must start at the encoder's idle state,
  or the decoder takes its first sample as a step.
* The **static checker** proved the DALI receiver's bookkeeping constants
  and the MIDI parser's worst path, but cannot follow the receiver's
  decisions (the thresholds compare a measured value), nor `midi_tx`'s
  "every word sends a byte", which is an `@loop max=1` assumption (a channel
  message with no data bytes would break it).
* Third round. The **static checker** bounds only the minimum of ISO 7816's
  guard time (N is a register) and unrolls loops with a known count, so
  ISO 7816's WT loop at 9600 etu takes it minutes: the annotations and the
  soundness runs use WT = 32 or 64, which only changes that loop's count.
  **sigrok**: `uart` takes at most 1.5 stop bits and samples one, so an ISO
  7816 error signal shows as a frame error on the rejected character (the
  tests expect exactly one); `ir_rc5`'s "standard" protocol has 6 command
  bits (an RC-5X command's bit 6 is its "start bit 2"); `i2c` has no SMBus
  layer, so the tests recompute the PEC over its bytes; `cec` names opcodes
  and logical addresses (quoted above), and checks bit timing against its
  own table, the same as the specification's.
