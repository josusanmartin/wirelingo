# Static timing checker

`tools/pe/timing.py` computes cycle bounds between labelled points of a
firmware program and checks them against declared deadlines: "from the end
of the host's EOP to our SYNC ≤ 26 clocks", "every UART bit is 8 ticks",
"SCL low ≥ 4.7 µs". It reads the assembled words, the cycle semantics of
[isa.md](isa.md) and assumptions written next to the code. It reports, for
each deadline, the minimum and maximum over all paths, PASS or FAIL, the
worst path, the assumptions the result depends on, and, when there is no
maximum, why.

The bounds are **checked against the cycle model**: for every annotated
firmware, `test/model/test_timing_check.py` runs the golden model
(`tools/pe/sim.py`, which is lockstep-checked against the RTL every cycle)
with randomized stimulus, measures the same intervals, and requires every
measurement made while the assumptions held to lie inside the checker's
[min, max]. Random programs do the same for the whole instruction set. This
is evidence, not a proof: no formal argument connects the checker to the
RTL, and an interval the stimulus never produced is not covered by it.

```
$ python -m pe.timing firmware/usb_fs_dev.pasm --defs pe.usbdev:defines
usb_fs_dev: 159 words, CLKDIV 1+0/256 (1 tick = 1 clk)
deadline ack_ta (line 57): ack_wait.cond (2..6 ticks after dend.enter) -> ack_out.done  -- DATA0/1 -> ACK
  bound: [5, 12] ticks = [5, 12] clk; with adjust +3..4: [8, 16] clk
  required: >= 8 and <= 26 clk  ->  PASS
  worst path (12 ticks):
    line 118 `ldi r2, 0x800D`                        +1
    line 119 `mov r1, crc`                           +1
    line 120 `cmp r1, r2`                            +1
    line 121 `jmp !z, data_bad`                      +1
    line 122 `cmp r3, 0x1C3`                         +1
    line 123 `jmp z, data_ok`                        +1
    line 124 `jmp f F_SETUP, data_bad`               +1
    line 125 `cmp r3, 0x1D2`                         +1
    line 126 `jmp !z, data_bad`                      +1
    line 128 `wait 1 pin DM`                         +8  <- the interval starts at the condition (2 ticks after the anchor; the WAIT was reached at 9, the lines above count from the anchor)
    line 129 `ldi r2, 0xD280            [2]`         +3
    line 130 `mov osr, r2`                           +1
    line 131 `out line, 16`                          +0  (end)
  assumes:
    stall= of the deadline max=0 (line 57) at line 131 `out line, 16`
...
```

The WAIT is reached 9 ticks after `dend` on this path and J may be visible
from tick 2, so the interval starts 7 ticks before the WAIT completes; the
other lines add their own ticks. (Run from the repository with
`PYTHONPATH=tools`. `-D NAME=VALUE` passes
assembler defines, `--defs MODULE:FUNC` takes them from a function,
`--timing FILE` adds a sidecar annotation file, `--clkdiv`, `--fifo-stall`
and `--line-stall` override the annotations, `-v` adds the best path and the
paths that were not counted. The exit status is 1 if any deadline fails.)

## 1. What is measured

Times are counted in **ticks** (one period of the core's divider, §4.1) and
converted to clocks at the end. An event is one of:

| Event | Tick |
|---|---|
| `P.enter` | the first attempt of the instruction at point P (side-set acts here, §4.2) |
| `P.done` (default) | the tick on which it completes (its effects, e.g. a pin write, commit here) |
| `P.cond` | the tick at which the condition of the untimed level WAIT at P becomes true: an event of the outside world, placed by an assumption (`after=`, §3) |

A deadline `from=A to=B` covers every occurrence of A and the next
occurrence of B after it. A point names one or more instructions: `@point`
can be given to several (all `pin SCL, hi` are `scl_hi`), and every label is
also a point (`label+k` is the k-th instruction after it).

**Per instruction**, from its first attempt, an instruction with delay *d*
(the low bits of the side/delay field, §4.3) that stalls *w* ticks completes
*w* ticks later and the next instruction is attempted *w* + 1 + *d* ticks
after its own first attempt. *w* is:

| Instruction | Stall *w* (ticks) |
|---|---|
| JMP, CALL, MOV, SET, LDI, ALU, PIN, FLAG, reserved opcodes | 0 |
| WAIT level kinds (0, 1, 5, 6) and kind 7, untimed | 0 .. ∞ (outside world) |
| WAIT edge kinds (2, 3, 4), untimed | 1 .. ∞ (the first attempt never completes, §5.2) |
| WAIT with `timeout rN`, N known | condition met (T = 0): 0 (edge: 1) .. N; timed out (T = 1): exactly N |
| WAIT with `timeout rN`, N unknown | as above with N ≤ 65535 (16-bit register): bounded, but loosely |
| IN with autopush reaching the threshold, PUSH (block) | 0 .. ∞ while RXF may be full |
| OUT with autopull at the threshold, PULL (block) | 0 .. ∞ while TXF may be empty |
| `in line` / `out line` (core 0) | 0 .. ∞: bits not yet received / TX queue full |
| PUSH `iffull`, PULL `ifempty` below the threshold; `noblock` | 0 |
| HALT | never completes: the path ends |

The ∞ entries are where assumptions come in (§3). A FIFO stall is ruled out
without an assumption when the checker can show it: it tracks ISR_CNT and
OSR_CNT (so it knows when autopush/autopull trigger), and it knows TXF is not
empty after a taken `jmp txne` until the core next pops it (only the core
pops it) and RXF is not full after an untaken `jmp rxf` until the core next
pushes.

**Clock conversion.** A span of *n* ticks of the 16.8 divider lasts
*n*·INT + ⌊*n*·FRAC/256⌋ to *n*·INT + ⌈*n*·FRAC/256⌉ clocks from any divider
phase (INT = 0 counts as 65536); `test_fractional_divider_conversion` checks
it from every phase. The divider comes from `.clkdiv`, `@assume clkdiv=` or
`--clkdiv`.

**Paths.** The checker first runs a dataflow over the whole program from its
entry (registers unknown, as the host may preload them; Z = C = T = 0,
ISR_CNT = 0, OSR_CNT = 32, as after a restart), so it knows the state at
every instruction. From the start event it then explores the paths
explicitly, carrying an abstract state along each one: register values as
small sets of constants, the link register as a set of return addresses,
Z/C/T, ISR_CNT/OSR_CNT and the two FIFO facts above. So:

* a branch whose condition the state decides is followed one way only, which
  unrolls a counted loop (`ldi r0, 7` … `jmp r0--`) exactly, with no
  annotation; pins, flags and the T after an `in line` are the outside
  world: both ways;
* a timed WAIT forks into "met" and "timed out", so a `jmp t` after it
  follows the right one;
* CALL/RET are followed with the link register on each path, so a
  subroutine called from several places returns to the right one;
* a loop the state does not decide (the path comes back to the same
  instruction in the same state) makes the maximum unbounded, unless one of
  its instructions has `@loop max=N`.

The minimum is the shortest path (it ignores loops' forced iterations
unless the state decides them, which is safe: a lower bound). The maximum
is the longest path of the explored state graph (exact over that graph).

**Program checks.** A RET that runs after a CALL inside the same subroutine
overwrote the only link register (§5.2), without `mov rN, lr` … `mov lr, rN`
around it, is an error. A subroutine may leave by a jump instead of a RET
(`i2c_device`'s `bit1` jumps to `start`), and CALLs after that are fresh.
A computed jump (`mov pc, rN` with an unknown value, `out pc`), a RET with an
unknown link register, or running off the end of the program inside a
deadline's paths is an error for that deadline.

## 2. Annotations

Annotations live in comments, so the assembled words do not change
(`test_annotations_leave_the_words_unchanged` assembles each annotated file
with and without them). An item starts with `@keyword` anywhere in a
comment and runs to the next item or `;`. Items that belong to an
instruction apply to the instruction on their line, else to the next one.

```
; @point NAME [at=LABEL+k]           this instruction is (one of the instructions of) point NAME
; @stall max=N [min=N]                this instruction stalls at most N ticks each time (an assumption)
; @loop max=N                         this instruction runs at most N times inside one interval
; @assume clkdiv=D fifo_stall=N line_stall=N core=C
; @deadline NAME from=P[.enter|.done|.cond] to=P[.enter|.done] [min=N] [max=N]
;     [unit=clk|tick] [adjust=A..B] [avoid=P,P] [stall=P:LO..HI,...] [after=P[.enter|.done]:LO..HI] ["why"]
```

* `min` / `max`: the requirement, in `unit` (clocks by default).
* `adjust=A..B`: a constant latency added to the bound, for a deadline that
  is about pads rather than instructions (§4).
* `avoid=P,...`: paths through these points are not counted (the interval
  is not of this kind: `avoid=top` in i2c_master leaves out the gap between
  host commands). They are listed with `-v`.
* `stall=P:LO..HI`: a stall assumption for this deadline only.
* `after=ANCHOR:LO..HI` (with `from=P.cond`): the WAIT at P sees its
  condition become true LO..HI ticks after the ANCHOR event. The interval
  starts at that moment; if the core reaches the WAIT later, the late
  arrival is part of the interval, which is exactly the turnaround question.

A **sidecar file** holds the same items, one per line without the leading
`;`, for firmware that should not be edited: `firmware/usb_enum.timing` has
`@point in_tmo at=got_in+1` and its deadlines.

### Paths that never arrive, and HALT

A deadline is a promise about every occurrence of its start: each one must be
followed by the end within the bound. So by default a reachable path that
never gets to the end (an endless loop, or a HALT on the way) fails the
deadline as `FAIL (MAY NEVER REACH)`, and the report lists those paths
without `-v`. Where that exclusion is intended (a phase deadline that a
timeout or an end of packet may leave, a response deadline for one token type
that other tokens never reach), say so: `avoid=P,...` names the exits, or
`reach=conditional` passes the deadline as `PASS (conditional)` and still
lists every excluded path. The firmware deadlines that use it are the I2C
phase lengths (the clock-stretch timeouts leave them), usb_fs_dev's IN
responses (other tokens take other paths) and its bit/byte loops (the end of
the packet leaves them), and usb_enum's `in_ta` and bit loop. usb_enum's
`in_ta_any` names its exits instead (`avoid=idle,not_in`: the token is not
for us, or not an IN) and passes outright.

`P.done` on a HALT is an error: HALT never completes, so there is no such
moment. Use `P.enter`.

(Both rules came from a review by Codex on 2026-10-07: before them, a HALT's
"completion" counted as a finite end and a branch into a forever-loop was
silently left out of a PASS.)

## 3. Assumptions

**Always assumed** (not checkable in an annotation; the soundness runs
respect them): the host does not write the core's PC or registers, restart
it or clear its FIFOs inside an interval; the program is loaded with its
entry at its first word (as `host.load_program` does) and runs from its own
bank; the side-set configuration is the program's `.side_set`.

**Declared**, and listed under "assumes:" whenever a result depends on them:

| Assumption | Meaning | Checked in the soundness runs by |
|---|---|---|
| `@stall max=N`, `stall=P:..` | the instruction's stall per execution | the measured attempts of each instance |
| `fifo_stall=N` | every FIFO stall (the host keeps up) | the same, for every FIFO instruction |
| `line_stall=N` | every line unit stall | the same |
| `@loop max=N` | executions per interval | counting them |
| `after=A:LO..HI` | when the outside world makes the WAIT's condition true | evaluating the condition on the model's pins every tick |
| `adjust=A..B` | pad latency outside the program | comparing with the bus-level measurement (USB) |
| `clkdiv` | the divider the host loads | running the model with it |

A measurement taken while an assumption was broken is reported apart and
excused; every other measurement must be inside the bound or the test
reports a checker bug. `test_i2c_master_no_stretch_assumption_needs_clkdiv_3`
breaks one on purpose: at CLKDIV 2 the synchroniser delays the SCL read-back
by a tick, so `stall=scl_wait:0` is false and every affected high phase is
flagged.

## 4. Firmware results

All measured on the model by `test_timing_check.py`; "measured" is the range
over the soundness runs.

**uart_tx** (`@assume clkdiv=52.083`, 115200 baud at 48 MHz; also run at
CLKDIV 1 and 3.5). No assumptions needed.

| Deadline | Bound (ticks) | Clocks at 52.083 | Measured (ticks) |
|---|---|---|---|
| start bit (`ldi side 0` → first `out pins`) | 8 | 416–417 | 8 |
| data bit → next bit, inside a byte | 8 | 416–417 | 8 |
| bit 7 → stop bit (`pull`'s first attempt: side-set) | 8 | 416–417 | 8 |
| start + 8 bits (the r0 loop unrolled) | 72 | 3749–3750 | 72 |
| stop bit | ≥ 8 (no maximum: idle while the FIFO is empty) | ≥ 416 | 8–86 |

**i2c_master** (`@assume clkdiv=60`: 100 kHz at 48 MHz, 1 tick = 1.25 µs;
the soundness runs use CLKDIV 4 with a peer that stretches SCL at random).
The phases are the master's drive, from each `pin SCL` to the next inside a
command.

| Deadline | Bound (ticks) | Requirement | Measured (ticks) |
|---|---|---|---|
| SCL high, stretching allowed | 4–2006 (bounded by the WAIT timeout, TMO = 2000) | — | 4–13 |
| SCL high, no stretching (`stall=scl_wait:0`) | 4–6 | — | 4–6 |
| SCL low inside a command | 4–8 | — | 4–8 |
| tHIGH at 100 kHz | ≥ 240 clk | ≥ 192 (4.0 µs) | |
| tLOW at 100 kHz | ≥ 240 clk | ≥ 226 (4.7 µs) | |

The checker corrected the declared numbers: the high phase is 6 ticks, not
5, for a START, whose `ldi r3, TMO` sat between the SCL release and the
WAIT. Since `fault-fixes` that `ldi` comes before the release, and the 6
ticks come from the `jmp !pin SDA` (the START's SDA read-back) after the WAIT.

**usb_fs_dev** at 48 MHz. Turnaround (USB 2.0 §7.1.18.1) is from the host's
SE0 → J at the pads to our first J → K at the pads, 8 to 26 clocks. Two
constants connect pads and program: the core sees J 1 to 2 clocks after the
pad edge (the synchroniser, §2), and our K reaches the pads 2 clocks after
`out line` completes (the line unit starts on the next cycle, §4.7, and its
output register drives the pad the cycle after); `adjust=3..4`. When the
core sees J is an assumption: 2..6 ticks after the frame end reaches the
firmware (`dend`, `tok_end`, `nak_send`, each 2 ticks after the `in line`
that returned T = 1). The receiver samples the SE0 1.5–2.5 clocks after its
edge and J follows 8 clocks later, which gives 3..4 for clean edges; the
window adds a clock each way for host jitter. The responses also assume the
line transmitter is idle when they start, and an IN's staged packet is in
the TX FIFO (the host sets F_STAGED after pushing it).

| Response | Checker, pads (clk) | Bus, measured (clk) | Program, bound / measured (ticks) |
|---|---|---|---|
| DATA0/1 → ACK | 8–16 | 10.29–14.33 | 5–12 / 7–11 |
| IN → NAK | 10–16 | 12.77–13.37 (before the guard; that path is unchanged) | 7–12 / 9–10 |
| IN → staged DATA | 13–18 | 15.73–16.45 | 10–14 / 12–13 |
| IN → the firmware's zero-length DATA1 (`in_zlp_ta`, branch `usb-config-prestage`) | 11–17 | 13.77–14.53 | 8–13 / 10–11 |
| OUT without room → NAK | 9–10 | 9.25–9.70 | 6 / 6 |
| DATA receive loop (per bit, ≤ 4) | 3 | | 3 |
| DATA transmit loop (per byte, ≤ 32) | 3–4 | | 3–4 |

With the got_in guard (branch `usb-guard`, 2026-11-04: an IN that finds
F_STAGED over an empty TX FIFO is NAKed), re-derived on the model:
`pe.timing` and `test_timing_check.py` at ac382d2 (logs:
`evidence/model-usb-guard-20261104/`). The guard costs IN →
staged DATA one clock (before it, at c5ee980: checker 12–17, bus
14.73–15.45, program 9–13 / 11–12), and IN → NAK one clock in its bound's
maximum only, on the new F_STAGED-over-empty path (checker 10–15, program
7–11 before); the other rows are unchanged.

With SET_CONFIGURATION's status stage answered by the firmware (branch
`usb-config-prestage`, 2026-11-06; 159 words), `pe.timing` and
`test_timing_check.py` (whose sessions now start with a SET_CONFIGURATION
and its status IN) give: IN → NAK one clock longer, checker 11–17, program
8–13 / 10–12, bus 13.42–15.03 (the `jmp f F_CFGZLP` in `nak_in`); IN → the
firmware's ZLP as in the row above; DATA0/1 → ACK bus 10.58–14.19, IN →
staged DATA 14.94–16.35, OUT → NAK 9.38–9.98, with the checker and program
bounds of those three unchanged. Evidence: model.

Measured over 8 sessions (SETUP/DATA0 → ACK, IN → NAK, IN → DATA1, OUT →
ACK, OUT → NAK) with host jitter up to ±9.25 ns and −2500 to +2500 ppm; the
J window measured 3..4 ticks every time, and the bus-minus-program
difference was always within 3..4 clocks. The measured maximum has at least
9.5 clocks of margin (IN → DATA, 16.45 of 26; 10.5 before the guard), the
checker's at least 8. The minimum has none in the worst case: if J were
visible 6 ticks after `dend`, a DATA0 would be ACKed exactly 8 clocks after
the EOP; with the measured window it is 10 or more.

**usb_enum** (full speed, `TURN = 4`; annotations in
`firmware/usb_enum.timing`):

| Deadline | Checker, pads (clk) | Bus, measured (clk) | |
|---|---|---|---|
| IN → DATA/STALL, ordinary path (`in_ta`) | 10–15 | | PASS (conditional) |
| DATA0/1 → ACK/STALL | 11–20 | | PASS |
| IN → answer, including the lost-ACK commit path (`in_ta_any`) | 10–21 (was 10–30) | the forced case: 18.4–19.0 (was 27.4–28.2) | PASS (was **FAIL**) |

**A real bug, found and fixed.** The checker's first run on `usb_enum` failed
`in_ta_any`: if SET_ADDRESS's status-stage ACK is lost, the device commits
the new address on the first token to it, and the commit (`call commit`,
which fell into `prep` and its RET: 11 instructions) sat between the token's
end and the IN's WAIT. If that token is an IN, the core reached the WAIT 22
ticks after `tok_end` instead of 7, and the answer could start 30 clocks
after the host's EOP, past full speed's 26 (6.5 bit times, USB 2.0
§7.1.18.1). Forcing that sequence on the model (the host loses its status
ACK, then sends an IN to the new address) gave bus turnarounds of
27.4–28.2 clocks; the host's timeout (16–18 bit times) still caught the
packet, so no test had noticed. A host starts every control transfer with
SETUP, which is why `in_ta` excludes the path (`avoid=lost_ack`), but a
device should not depend on it. Low speed, with 8 times the slack, was never
affected; the firmware is the same.

The fix (branch `enum-ta`, docs/usb-enumeration.md) removes the work rather
than racing it: `prep` was redundant there, because r1 is DATA1 while the
address is pending and DATA1 again after the commit (no transfer). The
commit is now three instructions inline at `tok_end` (`mov r3, r2`, `flag
clr F_ADDR`, `ldi r2, dev`), 6 ticks more than the ordinary IN path. To keep
"r1 is DATA1 while F_ADDR is set" true, a SETUP token now clears F_ADDR
before its DATA can overwrite r1 (previously a damaged SETUP DATA to the old
address could leave F_ADDR set with r1 and r2 overwritten). Now `in_ta_any`
is 10–21 clocks and PASSes with 5 clocks of margin, unconditionally: it
excludes paths only by name (`idle`, a token not for us; `not_in`, a token
that is not an IN), so it is the required deadline; `in_ta` is kept as the
ordinary path's bound (`PASS (conditional)`). `test_usb_enum_sound_and_pad_turnaround` measures the
forced case in all 5 sessions at 18.4–19.0 clocks on the bus, inside the
checker's 10–21, and no response anywhere is later than 26.

**From the frame end, since `fault-fixes`.** usb_enum's WAITs for the J
before an answer now have a timeout, so that a bus reset running on from the
host's EOP is seen ([fault-testing.md](fault-testing.md), finding 6). `.cond`
takes an untimed level WAIT only, so the three turnaround deadlines start at
the frame end (`tok_end`, `d_end`) instead: the WAIT is reached 7 ticks or
more after it, after the J (2..6, the old `after=`), so it completes at its
first attempt (`stall=in_tmo:0`, `hs_tmo:0`: the deadlines `in_reach` and
`hs_reach`, `min=7` ticks from the frame end to the WAIT, pass at 8 and 10, so
the assumptions follow from the program; the soundness runs, which skip a
measurement that breaks one, also assert that none does),
and the J-to-answer time is the interval less 2..6 ticks plus the pads' 3..4:
`adjust=-3..2`. The bounds are the same as above (10–15, 11–20, 10–21), and
the test now finds the J in the trace and still requires the bus turnaround
to be 3..4 clocks more than the core's. The firmware's delays keep the
answer on the same clock as before at both speeds (`J_LATE` in the source:
at low speed the J comes after the WAIT is reached). An untimed WAIT kept
only for the checker would have left a 2-clock window in which an SE0
starting just after the J is waited through, which a per-clock sweep found.

**Broken variants fail** (`test_broken_*`): `uart_tx` with `[5]` instead of
`[6]` in the bit loop gives 7-tick bits (FAIL); `usb_fs_dev` with seven
more ticks before the ACK gives 20–28 clocks (FAIL, > 26); without the
`[4]` before the NAK, an OUT's NAK comes 5–6 clocks after the EOP (FAIL,
< 8: the delay is what keeps it off the 2-bit minimum).

## 5. Soundness check against the model

`timing.Tracer` hooks one core of a `sim.Chip` (the instance only: its
`step`, its divider and the chip's coverage callback; the model is
unchanged) and records every instruction attempt with its tick and clock
cycle, and the pins and flags every tick. `timing.measure` turns a record
into the deadline's intervals, using the same rules as the analysis (next
occurrence, `avoid`, `@loop` counts, the `.cond` tick evaluated on the
pins), and lists the assumptions each interval broke.

`test_timing_check.py` runs:

* uart_tx at three dividers (one fractional) with bytes pushed at random
  moments;
* i2c_master with random commands and random clock stretching, three seeds;
* usb_fs_dev against the pins-only full-speed host (`pe.usbfs.FsHost`), 8
  sessions, comparing the bus turnaround with bound + adjust;
* usb_enum through enumeration with a lost handshake, and the forced
  lost-ACK IN (status ACK lost, then an IN first), 5 seeds;
* 40 random programs (every instruction kind, side-set and delay fields,
  autopush/autopull, timed and untimed WAITs on toggling pins, flags and
  FIFO traffic from the host, dividers with fractions) with random
  deadlines; no assumptions, so every measured interval must be inside.
  The last run: 131 deadlines with a path, 66 with a finite maximum, 10047
  intervals, all inside. An offline run of the same generator over 300
  programs (seeds 1000-1299, 6 deadlines each, some with `avoid`): 1230
  deadlines with a path, 640 with a finite maximum, 137477 intervals, none
  outside; for 337 deadlines the measured maximum equals the bound.

The random programs found one checker bug while this was written: for
`from=P.enter to=P.done` the analysis looked for the next execution of P
instead of the end of the same one.

## 6. Limits

* Not a proof. The checker implements its own reading of isa.md, and the
  only evidence that it matches the RTL is the model comparison above,
  which covers the stimulus it was given.
* The pad constants (`adjust`) and the USB J window are derived from the
  spec and confirmed on the model; they depend on the line unit and the
  board, which the checker does not model. Rise times, stretching on the
  wire and analog effects are outside it.
* The line unit's TX queue drain is not modelled: a stall on `out line` is
  an assumption (`stall=`), not a computed bound.
* Pins, flags and other cores are the outside world: every branch on them
  is taken both ways, so correlations the protocol guarantees (a flag that
  cannot be set here) need `avoid=` to exclude them, which is an
  assumption of its own and not checked except by the measurements.
* With CLKDIV > 1, a `.cond` event is placed at a tick, which can be up to
  one tick after the pad change; add it to `adjust`.
* Computed jumps other than RET are not followed (`usb_enum`'s
  `mov pc, r2` into its descriptor table): deadlines must not cross them.
* The path search is per abstract state; a loop whose registers keep
  changing hits an exploration limit and is reported with a request for
  `@loop`.
