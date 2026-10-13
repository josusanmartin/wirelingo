# Post-freeze protocol challenge

The competition asks for a chip "reprogrammable enough to support new
protocols after fabrication". This is a rehearsal of that moment: with the
RTL frozen at **tag `rc-93c2137-t48ba`** (the release candidate, run t48ba),
implement protocols the chip was never designed for, in firmware only, and
judge each one with oracles that do not share our assumptions.

Rules kept: no change under `src/` (`git diff rc-93c2137-t48ba -- src/` is
empty on this branch), and none to the golden model, assembler or timing
checker (`tools/pe/{sim,isa,asm,line,timing}.py` are as on `main`). Added:
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

Ten programs, 697 instruction words; the largest, `lin_responder`, uses 191
of a bank's 256. Every one has an RTL test through the real SPI host port
(`test/test_post_freeze.py`: 8 tests, all passing, 148 s of icarus) and
model tests (`test/model/test_{dmx512,lin,nec,i2s,wiegand,dshot,dshot_bidir}.py`,
`test_post_freeze_timing.py`: 78 tests; the whole model suite, 517 tests,
passes on this branch).

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
  protocol definitions, independently of the firmware.
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
  run at 50/48 MHz on the RTL as well.

```
PYTHONPATH=tools .venv/bin/python -m pytest test/model/test_dmx512.py test/model/test_lin.py \
    test/model/test_nec.py test/model/test_i2s.py test/model/test_wiegand.py \
    test/model/test_dshot.py test/model/test_dshot_bidir.py test/model/test_post_freeze_timing.py
cd test && make COCOTB_TEST_MODULES=test_post_freeze          # RTL, about 3 minutes
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

## ISA limitations found

What the challenge ran into, for a future revision. None stopped a protocol;
each cost instructions, registers or generality.

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
  closes a frame after two idle UART frames.
