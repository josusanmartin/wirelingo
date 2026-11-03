# Fault injection on existing firmware

`tools/pe/faults.py` puts seeded faults on the wire between a pins-only protocol
model and the chip. `test/model/test_faults.py` runs firmware against them with
the firmware unchanged. Every result here is from the cycle model at a fixed
seed sweep, deterministic on any machine. The sweep was not run on the RTL, at
gate level, or on hardware. The model is lockstep-checked against the RTL on
tested traces, and that is all that connects these results to the RTL.

```
PYTHONPATH=tools python -m pytest -q test/model/test_faults.py
remote_test.sh model                # on the compute runner, with the whole suite
```

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

16 seeds per firmware and fault kind: 576 cases, 7 not `ok`: 5 `false-good`
(findings 1 and 2 below) and 2 `second-frame` (finding 3). No `hang`, no
`needs-reset`, no `bus-clear`. "Changed" counts the cases where the fault
changed what the receiver reported at all: where it is low, the fault mostly
didn't matter to that firmware, and that row is weak evidence (`uart_rx` and
`can_rx` under `jitter` changed nothing at these sizes; `eth10_match` looks at
only 16 bits of a frame). The table is pinned to the sweep by
`test_doc_table_is_the_sweep`.

| Firmware | Fault | Cases | Changed | Not ok (seed: verdict) |
|---|---|---|---|---|
| uart_rx | glitch | 16 | 10 | 3: false-good |
| uart_rx | stuck | 16 | 15 | - |
| uart_rx | truncate | 16 | 10 | - |
| uart_rx | flip | 16 | 16 | - |
| uart_rx | stretch | 16 | 6 | - |
| uart_rx | jitter | 16 | 0 | - |
| i2c_master | glitch | 16 | 14 | - |
| i2c_master | stuck | 16 | 13 | 0: false-good, 4: false-good |
| i2c_master | truncate | 16 | 16 | 3: second-frame, 15: second-frame |
| i2c_master | flip | 16 | 16 | 8: false-good, 12: false-good |
| i2c_master | stretch | 16 | 8 | - |
| i2c_master | jitter | 16 | 6 | - |
| can_rx | glitch | 16 | 6 | - |
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

## Failures found

None was patched: each fix costs timing or words in firmware other things rest on
(see each). Each has a test named `test_finding_*` that pins today's behaviour
with the minimal case, so a fix must change the test.

**1. `uart_rx` takes a glitch for a start bit.** Sweep: `glitch`, seed 3. Minimal:
one tick low on the idle line delivers 0xFF with no error flag; so does any
pulse up to 12 ticks, of 8 ticks per bit (13 reaches the first data sample: 0xFE).
Below one tick it depends on a tick falling in the pulse (1 clock at CLKDIV 4: 1
phase in 4). The start bit is never re-read at mid-bit. A frame that starts 3 bits
after the glitch is read from its middle and its byte isn't delivered, with F0 set
or not depending on its data bit 5; the next frame after an idle line is fine. The check is one `jmp pin`, but `uart_rx`'s timing is fixed by
[uart-rx-timing.md](uart-rx-timing.md), so it isn't a one-line change.

**2. `i2c_master` reports an ACK from a stuck SDA.** Sweep: `stuck` seeds 0, 4,
`flip` seeds 8, 12. Minimal: SDA held low with no device at the address: every
WRITE reads ACK, a READ returns 0x00, F1 stays clear; the same transfer on a clean
bus is NACKed. The firmware never reads back what it drives. The same root lets a
flipped data bit be stored and ACKed. A stuck SCL, by contrast, is caught: SCL has
a timeout, SDA has none.

**3. `i2c_master`'s next transfer is lost after a truncated read.** Sweep:
`truncate` seeds 3, 15. Minimal: SCL held high over the end of a READ leaves the
EEPROM model driving SDA low. The firmware has no bus clear, so its STOP can't
raise SDA and the next START doesn't happen. That transfer comes back NACKed, so
the host is told, and it clocks the target free; the next one works. The host can
do the bus clear itself with the existing commands: READ with NACK, then STOP.

**4. `can_rx` has no form check.** Not visible to the sweep's oracle, so it is
directed. A dominant bit in the CRC delimiter, ACK delimiter or EOF is a form
error; `can_rx` stops looking after the last CRC bit, reports status 1 and ACKs.
The payload is intact.

**5. `can_rx` answers a glitch on the idle bus with an error flag.** Directed.
One clock low on the idle bus (up to 47 tested) starts a frame, because the SOF
isn't re-read at its sample point. The recessive bits after it are a stuff error:
status 2 and 288 ticks of dominant on TX. It recovers by itself, but a frame that
starts during the flag is destroyed.

**6. `usb_enum` can miss a bus reset that starts in an EOP.** Directed. SE0 for
4.2 us (the firmware's threshold is 2.5 us) starting at each of 22 bit positions
around an IN token: from the end of a valid token addressed to the device (start
bits 30 to 35) the firmware is at `got_in: wait 1 pin DM`, with no timeout and no
SE0 check, so the reset is not seen. The device keeps its address and answers when
the line returns; the next transfer works. A host doesn't start a reset inside a
token, so this takes a fault. The firmware uses 246 of 256 words and the
path is on the turnaround budget, so it isn't a small fix.

## The tests of the tests

* Firmware mutants must change a verdict: `uart_rx` without its stop-bit check,
  `can_rx` without its CRC check, `usb_enum` without its CRC16 check (each
  `false-good`), `eth10_match` that never leaves `drain` (`needs-reset`),
  `i2c_master` with no timeout on the data bits (misses the bound). Two more
  break the clean frame, which aborts the case rather than passing it.
* The UART strict receiver and the FCS check have their own tests, including
  that they reject what they should.
* Every fault class is checked clock for clock on a recorded line: exact
  window, edge count and order, delay bound, the seed deciding it, bad
  arguments rejected, and an empty Wire leaving the chip's state unchanged.

## Limits

* This is a sample: 16 seeds per cell, one fault per case, one frame shape per
  firmware. "No hang in 576 cases" is that, not a proof. The firmware WAITs without
  a timeout (`uart_rx`'s, `can_rx`'s, `usb_enum`'s) are cleared by the line
  returning, shown only in the stuck-line runs above.
* Faults are digital and whole-clock on the model's pads, apart from Ethernet's
  half-clock sender. No analog levels, no metastability, no ringing, no bus
  capacitance.
* The peer models are behavioural and our own. A real I2C target or CAN node
  would differ, and other nodes on the bus are not modelled: no retransmission,
  no error confinement.
* `usb_enum` was swept at full speed only; `eth10` at 50 MHz, short frames; `can_rx`
  at CLKDIV 1, `uart_rx` at CLKDIV 4, `i2c_master` at CLKDIV 4.
* A host that stops reading is not part of this. FIFO overflow is covered
  elsewhere.
