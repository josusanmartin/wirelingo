# Fault injection on existing firmware

`tools/pe/faults.py` puts seeded faults on the wire between a pins-only protocol
model and the chip. `test/model/test_faults.py` runs firmware against them as
it is, with no test-only change. Every result here is from the cycle model at a
fixed seed sweep, deterministic on any machine. The sweep was not run on the
RTL, at gate level, or on hardware. The model is lockstep-checked against the
RTL on tested traces, and that is all that connects these results to the RTL
(two of the fixes below also have their own RTL tests).

The sweep found six weaknesses. Branch `fault-fixes` fixed four of them in the
firmware (1, 2, 5 and 6 below; base `575362a`) and left two (3: expected I2C
behaviour; 4: a CAN check outside the README's scope). **The KV260 runs,
including Linux's enumeration of `usb_enum`, were made with the firmware
before these fixes.** `uart_rx`, `i2c_master`, `can_rx` and `usb_enum` as
they are now have not run on the FPGA, and those results apply to them only
after a rerun there.

```
PYTHONPATH=tools python -m pytest -q test/model/test_faults.py
remote_test.sh model                # on the compute runner, with the whole suite
remote_test.sh test                 # the RTL suite: test_uart_rx_glitch, test_i2c_master_bus_fault
```

The sigrok tests skip unless sigrok-cli is found: `remote_test.sh` does not pass
`SIGROK_CLI` over ssh, so on the runner set it in the remote command (the
`fault-fixes` runs did; at `b5496aa`: model 2131 passed, `test_sigrok` 8/8,
and without it 2103 passed, 28 skipped).

## What was injected

| Kind | On the wire | Size |
|---|---|---|
| `glitch` | a line inverted for less than one bit | 1 clock to one bit less a clock, anywhere in the frame |
| `stuck` | a line held at a random level | an eighth of a frame to a frame, then released |
| `truncate` | the sender goes away: the line at idle to the end of the frame | from a random bit |
| `flip` | a line inverted for exactly one bit, on the bit grid | one bit |
| `stretch` | from one edge on, all edges late (a sender's bit runs long); on I2C, a peer holds SCL low up to twice the firmware's timeout | up to one bit |
| `jitter` | each edge late by a random whole number of clocks, order kept and none lost | up to half a bit, more for an edge crowded behind another |

`pe.faults.plan(kind, seed, span)` draws a fault from the seed alone. Faults
apply to what the chip's input pads see, and at both clock edges where the sender
gives a half-cycle value. A wired-AND peer (the I2C EEPROM) reads the same
faulted bus. On USB the host model hears the device's drive unfaulted: faults
reach only the chip's inputs, including its own echo. The opposite direction has
its own injected faults in `test_usb_autonomous.py`. Glitches and flips hit
both lines of a pair together; a stuck pair draws each line's level separately,
so it covers J, K, SE0 and both high.

## What each case checks

Each case is a clean frame (the control), a frame under one fault, then clean
frames. It ends in one verdict:

| Verdict | Meaning |
|---|---|
| `ok` | nothing detectably corrupt was reported as good, and the next clean frame worked |
| `false-good` | a frame the protocol can show is bad was reported as good |
| `second-frame` | the next clean frame failed, the one after it worked |
| `bus-clear` | it took the protocol's own recovery (I2C: 9 clocks and a STOP) |
| `needs-reset` | it took a host restart of the core |
| `hang` | a documented timeout was missed, or a restart did not clear it |

"Detectably corrupt" is per protocol, and this is where the claims stop:

* `uart_rx`: a stop bit low must set F0, and a start bit a mid-bit check would
  reject must not give a byte with no flag. An independent strict 8N1 receiver
  decides, from the line as the chip saw it. A data bit flip in 8N1 is not
  detectable, and is not counted.
* `i2c_master`: on a write, every byte ACKed with no timeout must mean the memory
  holds what was written. On a read nothing can be checked.
* `can_rx`: status 1 must mean the destuffed bits equal the bits sent.
* `usb_enum`, full speed: a control read must return the descriptor, or fail;
  a STALL or wrong data means the device acted on a damaged packet. Half the
  seeds aim at the SETUP stage, half at the whole transfer.
* `eth10_rx`: no damaged frame may pass the FCS, which the host checks.
* `eth10_match`: only recovery. It decides before the FCS by design, so a damaged
  frame can raise HIT, as its header says.

The hang property also has its own runs, a line stuck through a whole frame
(`test_a_line_stuck_for_a_whole_frame_and_more`, and J, K, SE0 and both-high on
USB), because the sweep's `stuck` is bounded by a frame.

## Results

16 seeds per firmware and fault kind: 576 cases, 1 not `ok`: a `second-frame`
(`i2c_master` under `stuck`, seed 13, below). No `hang`, no `needs-reset`, no
`bus-clear`, no `false-good`. Before `fault-fixes` (at `575362a`) the same sweep
gave 7 not `ok`: 5 `false-good` (findings 1 and 2) and 2 `second-frame`
(finding 3). "Changed" counts the cases where the fault changed what the
receiver reported at all: where it is low, the fault mostly didn't matter to
that firmware, and that row is weak evidence (`uart_rx` and `can_rx` under
`jitter` changed nothing at these sizes; `eth10_match` looks at only 16 bits
of a frame). The table is pinned to the sweep by `test_doc_table_is_the_sweep`.

| Firmware | Fault | Cases | Changed | Not ok (seed: verdict) |
|---|---|---|---|---|
| uart_rx | glitch | 16 | 9 | - |
| uart_rx | stuck | 16 | 15 | - |
| uart_rx | truncate | 16 | 10 | - |
| uart_rx | flip | 16 | 16 | - |
| uart_rx | stretch | 16 | 6 | - |
| uart_rx | jitter | 16 | 0 | - |
| i2c_master | glitch | 16 | 15 | - |
| i2c_master | stuck | 16 | 13 | 13: second-frame |
| i2c_master | truncate | 16 | 16 | - |
| i2c_master | flip | 16 | 13 | - |
| i2c_master | stretch | 16 | 8 | - |
| i2c_master | jitter | 16 | 6 | - |
| can_rx | glitch | 16 | 5 | - |
| can_rx | stuck | 16 | 12 | - |
| can_rx | truncate | 16 | 14 | - |
| can_rx | flip | 16 | 11 | - |
| can_rx | stretch | 16 | 3 | - |
| can_rx | jitter | 16 | 0 | - |
| usb_enum | glitch | 16 | 10 | - |
| usb_enum | stuck | 16 | 14 | - |
| usb_enum | truncate | 16 | 13 | - |
| usb_enum | flip | 16 | 10 | - |
| usb_enum | stretch | 16 | 6 | - |
| usb_enum | jitter | 16 | 7 | - |
| eth10_rx | glitch | 16 | 4 | - |
| eth10_rx | stuck | 16 | 16 | - |
| eth10_rx | truncate | 16 | 16 | - |
| eth10_rx | flip | 16 | 13 | - |
| eth10_rx | stretch | 16 | 9 | - |
| eth10_rx | jitter | 16 | 3 | - |
| eth10_match | glitch | 16 | 0 | - |
| eth10_match | stuck | 16 | 13 | - |
| eth10_match | truncate | 16 | 10 | - |
| eth10_match | flip | 16 | 1 | - |
| eth10_match | stretch | 16 | 10 | - |
| eth10_match | jitter | 16 | 2 | - |

The rows that moved: `uart_rx` glitch (seed 3 is now `ok`; a glitch the
receiver drops changes nothing, so Changed went 10 to 9); `i2c_master` stuck,
truncate and flip (findings 2 and 3; its WRITE command is now 9 ticks longer,
so a fault fixed in clocks lands on other bits, which also moves glitch and
flip's Changed); `can_rx` glitch (a dropped idle glitch changes nothing).

**The new `second-frame`, `i2c_master` stuck seed 13.** SCL is held high
for about 21 bit times during a WRITE. The EEPROM misses clocks, and is left
driving its ACK on SDA after the transfer. The faulted transfer is reported:
its last byte replies 0x200 and F1 is set. The next transfer's START finds
SDA low, which sets F1; its bytes go to an EEPROM that saw no START and are
ACKed, so the harness counts it failed (F1 set, data elsewhere), but the host
is told by F1. The transfer after it works. Before `fault-fixes` this seed's
fault fell on other bits and the case was `ok`.

## Failures found

Each has a test named `test_finding_*`. For the four fixed, the test asserts
the fix, and `test_fix_mutant_fails_its_test` takes each new check out of the
firmware (or moves it a tick) and requires that test, run on the mutant, to
fail, and the real firmware to pass it: 25 mutants, all caught. Costs are
exact, from the assembler, the static timing checker and the model.

**1. `uart_rx` took a glitch for a start bit. Fixed.** Sweep: `glitch`, seed 3
was `false-good`. Then one tick low on the idle line delivered 0xFF with no
error flag, and so did any pulse up to 12 ticks of a bit's 8; a frame
starting 3 bits after the glitch was read from its middle. Now the start bit
is read again at D+4, its middle (`jmp pin RX, start`): a pulse of up to 4
ticks is no frame, at every tick phase; from 5 ticks the line is low at
mid-bit, a start bit by the book, and the firmware agrees with the strict
receiver for every width from 1 to 15 ticks. A frame 0 to 24 ticks after a
dropped glitch is received exactly. Cost: 1 word (13 to 14). No time: the
WAIT's `[10]` became `[3]`, the check and `[6]`, so the data samples
(D+12+8k), the STOP check (D+76) and the return to the WAIT (D+77) are where
they were, and `test_uart.py`'s ±3 % timing windows and
`test_pio_comparison.py`'s back-to-back tolerance pass unchanged. RTL:
`test_uart_rx_glitch`. Not changed: `uart_rx_packed`, `uart_rx_capture` and
the USB-UART pairs' receivers have no such check.

**2. `i2c_master` reported an ACK from a stuck SDA. Fixed.** Sweep: `stuck`
seeds 0, 4 and `flip` seeds 8, 12 were `false-good`. Then SDA held low with no
device at the address made every WRITE read ACK, a READ returned 0x00 and F1
stayed clear. Now SDA is read back wherever the master releases it: before a
START (SCL high, SDA must be high), after a STOP (SDA must have risen), every
bit of a written byte (sampled while SCL is high and compared with the byte
after the ACK) and a READ's NACK. A low read-back is a bus fault: F1, and a
WRITE or READ replies 0x200 (bit 9; an SCL timeout is 0x100) instead of its
ACK or data. START and STOP have no reply, so F1 alone. A START that finds
SDA low is not made, but SCL still goes low once, as after a START, so a
target holding SDA for a clock edge gets one. The same minimal case now
replies 0x200 to every byte with F1 set. Cost: 19 words (71 to 90). The SCL
waveform inside a command is unchanged (4 ticks low, 4 high, 6 for START's
high phase; the timing checker's five deadlines pass with the same bounds). A
WRITE command takes 88 ticks instead of 79 (4 more before its first bit, 5
after its ACK), a READ 3 more (after its ACK); START and STOP none. So
back-to-back written bytes run 11 % slower. Below the documented minimum of
CLKDIV 3, START's high phase is now 7 ticks at CLKDIV 2 (it was 6): its
`ldi r3` moved before the SCL release to make room for the check. RTL:
`test_i2c_master_bus_fault`. The master still drives all 8 bits of a byte
before comparing, so it does not stop early on a lost arbitration; it is not
a multi-master.

**3. `i2c_master`'s next transfer is lost after a truncated read. Left: the
usual result without a bus clear.** The sweep's `truncate` seeds 3 and 15
were `second-frame` and are now `ok`, because the faults moved (finding 2's
cost), not because of a change for this. The minimal case still holds: SCL
held high over the end of a READ leaves the EEPROM model driving SDA low. The
read-back now sees it at the NACK, so that READ replies 0x200 with F1. The
firmware has no bus clear, so the next transfer is NACKed (the host is told)
and clocks the target free; the one after works. The host can do the bus
clear itself with the existing commands: READ with NACK, then STOP.

**4. `can_rx` has no form check. Left.** Not visible to the sweep's oracle,
so it is directed. A dominant bit in the CRC delimiter, ACK delimiter or EOF
is a form error; `can_rx` stops looking after the last CRC bit, reports
status 1 and ACKs. The payload is intact. A check of the CRC delimiter alone
would be a few words, but the ACK delimiter and EOF come after the status
word is pushed, so checking them means pushing it about 8 bits later, a
change to when the host learns of a frame. That is beyond "frames, not a full
controller" in the README, so it is left.

**5. `can_rx` answered a glitch on the idle bus with an error flag. Fixed.**
Then one clock low on the idle bus (up to 47 tested) started a frame, and the
recessive bits after it were a stuff error: status 2 and 288 ticks of
dominant on TX, destroying a frame that started during the flag. Now the SOF
is read again at its sample point, D+24, by a copy of the sample
instructions on the same tick as every later bit's (`jmp r2, idle` if it is
recessive). A low pulse of up to 24 clocks is no frame, no flag, no status;
from 25 clocks the SOF is dominant at the sample point, which is a SOF by the
book, and the stuff error follows as before. A frame 2 bits after a dropped
glitch is received and ACKed. Cost: 3 words (97 to 100); no tick on any path
(the SOF path reaches the shared code at S+4, as before). Not changed:
`can_tx`'s receive side.

**6. `usb_enum` missed a bus reset that started in an EOP. Fixed.** Then SE0
from the end of a valid token addressed to the device (start bits 30 to 35)
found the firmware at `got_in: wait 1 pin DM`, with no timeout and no SE0
check, and the verifier showed no reset length got out: a 10 ms reset there
left the address set, and a host that resets and enumerates at address 0 then
gets no answer. A per-clock sweep found the same for every SE0 starting in
the 24 to 27 clocks before each of the device's five packets in a control
read (the host's EOP and the turnaround), 125 of 4400 start clocks. Three
changes:

* `got_in` and `handshake` wait for the J with a timeout, 121 ticks (2.5 us,
  as `idle` does), and go to `se0` on it: an SE0 that runs on from the EOP is
  a reset.
* `echo`, which drains the device's own packet as it comes back through the
  receiver, first waits up to 121 ticks for J or K on its input. An SE0 that
  starts in the turnaround hides the device's own SYNC from it, so its echo
  never begins; that is now a reset too.
* The turnaround delays absorb the new instructions, so every answer leaves
  on the same clock as before, at both speeds (`J_LATE`: at low speed the
  2-bit EOP outlasts the instructions before the WAIT; at full speed the J is
  already there). In five model sessions compared clock for clock (four at full
  speed with offsets, jitter and the lost-ACK IN, one low-speed enumeration;
  `usb_reset_sweep.py turnarounds`, below) the device's turnarounds and the
  bytes of every packet on the bus are identical to the old firmware's. The
  suite holds this too: `test_usb_enum_answers_on_the_same_clock_as_before`
  pins the five sessions' turnarounds as the old firmware gave them (to
  0.01 ns at full speed, exactly at low speed, where `J_LATE 0` moves them by
  1 or 2 clocks). (The
  longer program takes 7 more host writes to load, so everything after the
  load is 7 clocks later.)

Results (model, full speed unless stated):

* SE0 of 4.2 us from each of 22 bit positions of an IN token, and of 10 ms
  from bits 30, 32 and 35: a reset every time, address 0, and the device
  enumerates again (`test_finding_usb_enum_sees_a_reset_that_starts_in_an_eop`
  and `..._a_10ms_reset_...`).
* SE0 of 3000 clocks (63 us, longer than any of the device's packets plus
  2.5 us) starting at every clock of a GET_DESCRIPTOR control read, 4400
  start clocks: the core reached `reset` while the SE0 lasted at every one,
  at most 461 clocks (9.6 us) after the SE0 began (the longest is inside the
  device's own 8-byte DATA packet, which it finishes first), and enumerated
  again at address 0 every time. The old firmware failed 125 of the 4400.
  The sweep is too long for the suite (`usb_reset_sweep.py`, below); the suite's
  `test_usb_enum_sees_a_reset_that_starts_anywhere` runs every clock of the
  five turnaround windows (24 before to 8 after each packet's first clock)
  and a 29-clock grid over the rest (262 starts),
  with the 461 as its bound, and requires that the device starts no packet
  of its own more than 16 clocks into the SE0.
* A 10 ms SE0 (480,000 clocks) from every 37th clock of the same read, 119
  starts: a reset at every one, at most 455 clocks after the SE0 began, and
  enumerated again.
* Low speed: SE0 of 20,000 clocks (417 us) from every 4th clock of a
  low-speed control read, 9000 start clocks: a reset at every one, at most
  2702 clocks (56 us) after the SE0 began, and enumerated again. The suite's
  `test_usb_enum_sees_a_reset_that_starts_anywhere_low_speed` runs 93 of those
  starts (every 16th clock of the 200 before each of the device's packets,
  and a 997-clock grid) with the 2702 as its bound. The old firmware, from
  every 64th clock: 11 of 563 starts missed, each in the 150 clocks before one
  of the device's packets.

The sweeps run on the compute runner (`--firmware` takes another `usb_enum`
source, such as the one at `575362a`):

```
cd test/model && export PYTHONPATH=../../tools
python usb_reset_sweep.py full 3000 1          # 4400 starts, 0 missed, at most 461 clocks
python usb_reset_sweep.py full 3000 1 --firmware OLD.pasm      # the old firmware: 125 missed
python usb_reset_sweep.py full 480000 37       # 10 ms: 119 starts, 0 missed, at most 455
python usb_reset_sweep.py low 20000 4          # 9000 starts, 0 missed, at most 2702
python usb_reset_sweep.py turnarounds OLD.pasm # the five sessions, clock for clock
```

A longer reset only keeps the device longer in `reset`'s WAIT for J, so
"any length" here means any SE0 that lasts until the core reaches `reset`:
at most 461 clocks at full speed (2702 at low speed). A shorter SE0 that
starts inside the device's own packet can be over before the device has
finished sending and looks, with the old firmware and the new: it does not
watch the line while it sends. Cost: 7 words (246 to 253 of 256,
3 left); the timing checker's three turnaround deadlines pass with the same
bounds (10-15, 11-20 and 10-21 clocks), now measured from the frame end
because its `.cond` takes only an untimed WAIT ([timing-check.md](timing-check.md)).
The echo check adds 4 ticks before the device drains its own packet, which
no deadline covers; at low speed it drops one more bit of its own 96-bit
DATA echo in one packet of an enumeration (`test_line_echo.py`: 93 echo
bits dropped, 92 before, and never a bit of the host's).

## The tests of the tests

* Firmware mutants must change a verdict: `uart_rx` without its stop-bit check,
  `can_rx` without its CRC check, `usb_enum` without its CRC16 check (each
  `false-good`), `eth10_match` that never leaves `drain` (`needs-reset`),
  `i2c_master` with no timeout on the data bits (misses the bound). Two more
  break the clean frame, which aborts the case rather than passing it.
* The fixes' own 16 mutants (`FIX_MUTANTS`): the mid-bit check removed or a
  tick early; the SOF check removed, made unconditional, a tick early or
  late; each of the four SDA read-backs removed, the read-back replaced by
  zeros, the byte compared against the wrong bit order; `got_in`'s timeout
  removed or the WAIT made untimed; `handshake`'s timeout removed; the echo
  check removed. And, from report 023, nine more: `J_LATE 0` (low-speed
  answers 1 or 2 clocks early: caught by the pinned turnarounds); the
  `got_in`, `handshake` and `echo` timeouts lowered (to 40, 40 and 1 ticks:
  caught by `test_usb_enum_short_se0_is_not_a_reset`, a 1.9 us SE0 at each of
  those WAITs, which must leave the address set); `echo` without its `[3]`;
  the mid-bit check a tick late; the read-back compare ignoring the LSB;
  `start_fault` without its SCL low (stuck seed 13 then hangs); the SOF
  routed back through `sample`. Each fails the test of its check, which the
  real firmware passes.
* The UART strict receiver and the FCS check have their own tests, including
  that they reject what they should.
* Every fault class is checked clock for clock on a recorded line: exact
  window, edge count and order, delay bound, the seed deciding it, bad
  arguments rejected, and an empty Wire leaving the chip's state unchanged.

## Limits

* This is a sample: 16 seeds per cell, one fault per case, one frame shape per
  firmware. "No hang in 576 cases" is that, not a proof. The firmware WAITs without
  a timeout (`uart_rx`'s, `can_rx`'s, `usb_enum`'s `reset`) are cleared by the line
  returning, shown only in the stuck-line runs above.
* Faults are digital and whole-clock on the model's pads, apart from Ethernet's
  half-clock sender. No analog levels, no metastability, no ringing, no bus
  capacitance. On USB the fault reaches only the chip's input, so an SE0
  during the device's own packet hides that packet from it; on a real bus the
  two drivers would fight.
* The peer models are behavioural and our own. A real I2C target or CAN node
  would differ, and other nodes on the bus are not modelled: no retransmission,
  no error confinement.
* `usb_enum` was swept at full speed only (the reset sweeps also at low
  speed); `eth10` at 50 MHz, short frames; `can_rx` at CLKDIV 1, `uart_rx` at
  CLKDIV 4, `i2c_master` at CLKDIV 4.
* A host that stops reading is not part of this. FIFO overflow is covered
  elsewhere.
* The fixes are model and RTL-simulation results. They were not run at gate
  level or on the KV260, and the KV260 results elsewhere in this repository
  were made with the firmware before them.
