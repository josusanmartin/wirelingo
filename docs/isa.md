# protoemu — architecture & ISA specification (v0.2)

This document is normative: the RTL (`src/`), the golden model
(`tools/pe/sim.py`) and the assembler (`tools/pe/asm.py`) implement exactly
what is written here. When they disagree, this document wins and the other
two get fixed.

## 1. Overview

protoemu is a programmable protocol engine: up to four small cores ("lanes"),
each a single-issue, one-instruction-per-tick processor specialised for
bit-banging. A build has `NCORES` cores and `NBANKS` instruction-memory banks
(1–4 each). The architecture, register map and encodings always describe four;
see §7 for what a smaller build does with the missing ones.
Each core has its own clock divider, registers, shift registers and FIFOs to
the host. The instruction set is PIO-inspired but addresses PIO's main
limitations:

| PIO limitation                           | protoemu                                             |
|------------------------------------------|------------------------------------------------------|
| 32 instructions shared by 4 SMs          | 4 banks of up to 256 words, re-assignable among cores |
| only X/Y, no arithmetic, 5-bit immediates| R0–R3 (16 bit), 16-bit LDI, ADD/SUB/AND/OR/XOR/SHL/SHR/CMP, Z/C flags |
| WAIT has no timeout                      | any WAIT can time out (Rn tick budget), sets T flag  |
| WAIT only on levels                      | WAIT on rise / fall / any edge, sampled every clock  |
| no compound conditions                   | WAIT qualifier: "SDA falls while SCL is high" in one instruction |
| open drain needs pindir tricks           | per-pin open-drain mode                              |
| no direct single-pin control             | `PIN p, hi/lo/toggle/hiz` on any absolute pin        |
| SMs sync only via IRQ flags              | flags + 8 virtual pins (internal wires between cores)|

Beyond PIO there is also a CRC tap fed by the shift registers (§4.5) and
FIFO join (§4.4). Bit stuffing and NRZI/Manchester line coding stay in
firmware (the USB, CAN and 10BASE-T programs); a hardware line-coding stage is
not planned for this tapeout.

## 2. Pins

Tiny Tapeout provides `ui_in[7:0]` (input), `uo_out[7:0]` (output) and
`uio[7:0]` (bidirectional). Four are reserved for the host SPI port:

| Pad      | Use          |
|----------|--------------|
| ui[5]    | host MOSI    |
| ui[6]    | host SCK     |
| ui[7]    | host CS_n    |
| uo[7]    | host MISO    |

Cores see a 32-entry **logical pin space**:

| Logical  | Pad          | Direction          | Read value                          |
|----------|--------------|--------------------|-------------------------------------|
| P0–P7    | uio[0..7]    | bidirectional      | synchronised pad input              |
| P8–P14   | uo[0..6]     | output only        | current output register             |
| P15      | —            | —                  | 0                                   |
| P16–P20  | ui[0..4]     | input only         | synchronised pad input              |
| P21–P23  | —            | —                  | 0                                   |
| P24–P31  | V0–V7        | virtual (internal) | current output register             |

* **Synchroniser:** pad inputs pass through a 2-flop synchroniser. A core
  reading a pad input in cycle *t* sees the pad value that was present at the
  clock edge ending cycle *t − 2*.
* **Output registers:** one global `pin_out[31:0]` and `pin_oe[31:0]`.
  Writes take effect at the end of the cycle in which the writing instruction
  executes, so the pad changes one cycle later.
* **Ownership:** each logical pin has a 2-bit owner (`PIN_OWNER`). A write by
  core *c* to a pin whose owner is not *c* is silently discarded. Reset: every
  pin is owned by core 0.
* **Pad drive:** for P0–P7 `uio_oe = pin_oe`, `uio_out = pin_out`, unless the
  pin is in **open-drain mode** (`OD_MASK[i] = 1`), in which case
  `uio_out = 0` and `uio_oe = ~pin_out`. Writing 1 releases the line; writing
  0 pulls it low. `pin_oe` is ignored for OD pins. For P8–P14 the pad always
  drives `pin_out`.
* **Priority** inside one core in one cycle: side-set beats any other write to
  the same pin. Different pins combine.

## 3. Instruction memory

Four banks of `BANK_WORDS` 32-bit words (256 in the SRAM build; the flop
build may have fewer, in which case addresses alias within a bank). An
instruction address is 10 bits: `bank = addr[9:8]`, word = `addr[7:0]`.

Each bank has one fetch port, assigned to a core by `BANK_OWNER` (reset:
bank *b* → core *b*). Every core has a 10-bit PC covering all banks. A core
can fetch only from banks it owns. If a core is about to execute from a bank
it doesn't own, it **faults** and halts (`STATUS.fault = 1`).

So one core can run a 768-word USB program across banks 0–2 while core 3 runs
a UART from bank 3, at no cost to the other cores.

IMEM is loaded by the host and is write-only from the host's side. Fetches
are registered (SRAM-like): the core presents its next PC every cycle, so
there's no branch penalty. Two host-side rules follow from this:

* Write a bank only while its owning core is disabled.
* Change `BANK_OWNER` only while the affected cores are disabled.

## 4. Core programming model

Per-core architectural state:

| State        | Width | Reset / restart value            |
|--------------|-------|----------------------------------|
| PC           | 10    | `ENTRY`                          |
| R0–R3        | 16    | 0 at reset; kept on restart      |
| ISR          | 32    | 0                                |
| ISR_CNT      | 6     | 0 (bits shifted in, saturates at 32) |
| OSR          | 32    | 0                                |
| OSR_CNT      | 6     | 32 (bits shifted out; 32 = empty) |
| Z, C, T      | 1 each| 0                                |
| TXF          | D×32  | empty at reset; kept on restart (host → core) |
| RXF          | D×32  | empty at reset; kept on restart (core → host) |

Restart keeps both FIFOs, so the host can queue data before starting a core.
A `CTRL` FIFO-clear bit (`[19:16]`, §6.2) empties them.

### 4.1 Ticks, clock divider

Each core runs on its own tick, generated by a 16.8 fractional divider
(`CLKDIV.INT`, `CLKDIV.FRAC`). Divider state is `cnt` (16 bits) and `facc`
(8 bits). Each clock cycle while the core is enabled:

```
if cnt == 0:  tick = 1; {carry, facc} = facc + FRAC; cnt = INT - 1 + carry   (mod 2^16)
else:         tick = 0; cnt = cnt - 1
```

`INT=1, FRAC=0` ticks every cycle (reset value). `INT=0` means 65536.
Restart clears `cnt` and `facc`, so the first enabled cycle after a restart
is a tick.

### 4.2 Execution

On each tick, a core is in one of these states:

* **DELAY** (`dcnt > 0`): `dcnt -= 1`, and nothing else happens.
* **RUN**: attempt the instruction at PC. Side-set (if enabled) is applied on
  every attempt. If the instruction **completes**, its effects are committed,
  the PC advances (below) and `dcnt = delay`. If it **stalls** (WAIT not
  satisfied, blocking PUSH/PULL, autopush/autopull blocked), no state
  changes except side-set, the WAIT trackers and a timeout counter. The
  instruction is retried on the next tick.
* **HALTED / FAULT**: nothing happens until the host restarts the core.

An instruction with delay *d* takes at least 1 + *d* ticks.

**Next PC** after a completed instruction is the jump target if a jump was
taken. Otherwise it's `WRAP_BOTTOM` if `PC == WRAP_TOP`, else
`PC + 1` (mod 1024).

Disabling a core (`CTRL.enable = 0`) freezes all of its state, including the
divider and the WAIT trackers. Re-enabling continues where it left off.
**Restart** sets PC = ENTRY and resets the state marked above. R0–R3 are kept,
so the host can preload them.

### 4.3 Side-set and delay

Bits [26:19] of every instruction are shared between side-set and delay,
configured per core by `SIDE_COUNT` (0–4 pins; 5–7 behave as 4) and
`SIDE_OPT`:

```
[26:19] = { en (only if SIDE_OPT), side[SIDE_COUNT-1:0], delay[...] }
```

`delay` gets the remaining `8 − SIDE_COUNT − SIDE_OPT` low bits (at least 3).
Without `SIDE_OPT`, every instruction drives its side-set pins. With
`SIDE_OPT`, it drives them only when `en = 1`. Side-set drives `SIDE_COUNT`
pins starting at `SIDE_BASE` (mod 32). If `SIDE_PINDIR` is set, it writes
`pin_oe` instead of `pin_out`.

### 4.4 Shifting

`OUT_SHIFT_RIGHT` and `IN_SHIFT_RIGHT` choose the shift direction.

* **OUT** *n* bits (1 ≤ *n* ≤ 32):
  - Right: `data = OSR[n-1:0]; OSR >>= n`.
  - Left: `data = OSR[31:32-n]; OSR <<= n`.
  - Either way, `OSR_CNT = min(32, OSR_CNT + n)`.
* **IN** *n* bits from `src`:
  - Right: `ISR = (ISR >> n) | (src[n-1:0] << (32-n))`.
  - Left: `ISR = (ISR << n) | src[n-1:0]`.
  - Either way, `ISR_CNT = min(32, ISR_CNT + n)`.
* **Autopull** (if enabled): an OUT that finds `OSR_CNT ≥ PULL_THRESH` first
  refills OSR from TXF (`OSR_CNT = 0`), then shifts from the refilled value in
  the same tick. If TXF is empty, the OUT stalls.
* **Autopush** (if enabled): an IN whose result has `ISR_CNT ≥ PUSH_THRESH`
  pushes the new ISR to RXF, then clears ISR and ISR_CNT. If RXF is full, the
  IN stalls and nothing is shifted.
* **FIFO join** (`FJOIN_TX`): RXF becomes a second TX stage, giving 2·D
  words of TX buffering for protocols the host can't keep up with
  (10BASE-T). The host pushes into TXF as usual. Each cycle, if TXF is not
  empty and RXF is not full (start-of-cycle levels), the TXF head moves to
  RXF. PULL, autopull and `txne` use RXF. PUSH and autopush act as if RXF
  were full, and so does the `rxf` condition. Host FIFO reads return 0 and
  pop nothing. STATUS still reports both levels, so the host sees
  `TXF lvl < D` as "room to push". The move runs whether or not the core is
  enabled. Change `FJOIN_TX` only with both FIFOs empty (or clear them).
* Thresholds are 5-bit fields where 0 means 32. `OUT_COUNT` > 32 behaves as
  32, and `SET_COUNT` 6–7 behave as 5.

### 4.5 CRC tap

Each core has a 16-bit CRC register fed by its shift registers, for protocols
that append a CRC (USB CRC5/CRC16, CAN CRC15, CRC-CCITT, Modbus, ...).
`CRCCTRL` sets the polynomial `POLY` (without the top term), the width
`W = CRC_W + 1` (1–16), and which shifts feed it:

* `IN_TAP`: every `IN src, 1` feeds the bit shifted into the ISR.
* `OUT_TAP`: every `OUT dst, 1` feeds the bit shifted out of the OSR.

Only 1-bit shifts update the CRC. Each fed bit `b` does:

```
fb  = CRC[W-1] ^ b
CRC = ((CRC << 1) & CRC_MASK) ^ (fb ? POLY & CRC_MASK : 0)      CRC_MASK = 2^W - 1
```

This is the non-reflected LFSR over bits in time order (for USB, the order
they appear on the wire). `MOV crc, ~null` presets all ones, and `MOV r, crc`
reads the CRC back for a compare. Over a correct USB DATA packet (payload plus
its inverted CRC16) the register ends at the residue 0x800D; over a correct
token it ends at 0b01100. The CRC is not changed by restart; the host can
write it through `CRC` (core block 0x07).

### 4.6 Flags

* **Z, C** are set only by ALU instructions.
* **T** is set by a WAIT with timeout: 1 if it timed out, 0 if the condition
  was met. Other instructions leave it unchanged.
* **Global flags F0–F7** are shared by all cores and the host. Any core can set
  or clear them (`FLAG`), wait on them (`WAIT`) and branch on them (`JMP`).

## 5. Instruction encoding

All instructions are 32 bits:

```
 31      27 26            19 18                           0
+----------+----------------+------------------------------+
|  opcode  |  side / delay  |           operand            |
+----------+----------------+------------------------------+
```

Reserved operand bits should be 0. The assembler emits 0 and the hardware
ignores them. Reserved opcodes execute as NOP and complete.

| Op | Mnemonic | Operand layout                                                             |
|----|----------|----------------------------------------------------------------------------|
| 0  | `JMP`    | `[18:15] cond  [14:10] sel  [9:0] addr`                                    |
| 1  | `WAIT`   | `[18:16] kind  [15:11] idx  [10] timeout  [9:8] treg  [7] clear  [6] qen  [5] qpol  [4:0] qpin` |
| 2  | `IN`     | `[18:15] src  [4:0] count (0 = 32)`                                        |
| 3  | `OUT`    | `[18:15] dst  [4:0] count (0 = 32)`                                        |
| 4  | `PUSH`   | `[18] iffull  [17] block`                                                  |
| 5  | `PULL`   | `[18] ifempty  [17] block`                                                 |
| 6  | `MOV`    | `[18:15] dst  [14:13] op  [12:9] src`                                      |
| 7  | `SET`    | `[18] pindirs  [4:0] value`                                                |
| 8  | `LDI`    | `[17:16] rd  [15:0] imm`                                                   |
| 9  | `ALU`    | `[18:16] aop  [15:14] rd  [13] imm  [12:0] imm13 / [1:0] rs`               |
| 10 | `PIN`    | `[18:17] action  [4:0] pin`                                                |
| 11 | `FLAG`   | `[18] clear  [2:0] idx`                                                    |
| 12 | `HALT`   | —                                                                          |
| 13–31 | —     | reserved (v0.3 serial pipeline)                                            |

### 5.1 Operand codes (`src` / `dst`)

| Code | Name     | as source                                  | as destination                               |
|------|----------|--------------------------------------------|----------------------------------------------|
| 0–3  | R0–R3    | zero-extended to 32 bits                   | low 16 bits                                  |
| 4    | PINS     | `rotr(pins_in, IN_BASE)`                   | `OUT_COUNT` pins from `OUT_BASE` → `pin_out` |
| 5    | PINDIRS  | 0                                          | `OUT_COUNT` pins from `OUT_BASE` → `pin_oe`  |
| 6    | ISR      | ISR                                        | ISR (and `ISR_CNT = 0`)                      |
| 7    | OSR      | OSR                                        | OSR (and `OSR_CNT = 0`); not an OUT destination |
| 8    | NULL     | 0                                          | discard                                      |
| 9    | STATUS   | `[3:0]` TXF level, `[7:4]` RXF level, `[8]` Z, `[9]` C, `[10]` T | discard            |
| 10   | FLAGS    | `{24'b0, F7..F0}`                          | discard                                      |
| 11   | PC       | `{22'b0, PC}`                              | jump to `value[9:0]`                         |
| 12   | CRC      | `{16'b0, CRC}`                             | `CRC = value[15:0] & CRC_MASK` (§4.5)        |
| 13–15| —        | 0                                          | discard                                      |

For a PINS/PINDIRS destination, pin `(OUT_BASE + i) mod 32` gets `value[i]`
for `0 ≤ i < OUT_COUNT`.

### 5.2 Instruction semantics

**JMP cond, sel, addr.** If `cond` holds, the next PC is `addr`.

| cond | Syntax     | Holds when                                                  |
|------|------------|-------------------------------------------------------------|
| 0    | (none)     | always                                                      |
| 1    | `!rN`      | `R[sel[1:0]] == 0`                                          |
| 2    | `rN`       | `R[sel[1:0]] != 0`                                          |
| 3    | `rN--`     | `R[sel[1:0]] != 0`; the register is decremented either way (0 → 0xFFFF) |
| 4    | `pin p`    | `pins_in[sel] == 1` (absolute pin)                          |
| 5    | `!pin p`   | `pins_in[sel] == 0`                                         |
| 6    | `z`        | Z == 1                                                      |
| 7    | `!z`       | Z == 0                                                      |
| 8    | `c`        | C == 1                                                      |
| 9    | `!c`       | C == 0                                                      |
| 10   | `t`        | T == 1                                                      |
| 11   | `!osre`    | `OSR_CNT < PULL_THRESH`                                     |
| 12   | `txne`     | TXF not empty                                               |
| 13   | `rxf`      | RXF full                                                    |
| 14   | `f k`      | `F[sel[2:0]] == 1`                                          |
| 15   | `!f k`     | `F[sel[2:0]] == 0`                                          |

**WAIT kind, idx [qualifier] [clear] [timeout].** Stalls until the condition
holds. `p = idx` is an absolute pin; `k = idx[2:0]` is a flag.

| kind | Syntax            | Base condition                                        |
|------|-------------------|-------------------------------------------------------|
| 0    | `wait 1 pin p`    | `pins_in[p] == 1`                                     |
| 1    | `wait 0 pin p`    | `pins_in[p] == 0`                                     |
| 2    | `wait rise p`     | a 0→1 transition of `p` has been observed             |
| 3    | `wait fall p`     | a 1→0 transition has been observed                    |
| 4    | `wait edge p`     | any transition has been observed                      |
| 5    | `wait 1 flag k`   | `F[k] == 1`; with `clear`, the flag is cleared when the WAIT completes |
| 6    | `wait 0 flag k`   | `F[k] == 0`                                           |
| 7    | —                 | reserved (completes immediately)                      |

**Qualifier** (`qen = 1`, syntax `if pin q == v`): `Q = (pins_in[qpin] == qpol)`.
For level kinds (0, 1, 5, 6) the condition is `base && Q`, evaluated on the tick.
For edge kinds, a transition counts only if `Q` holds in the same clock cycle
as the transition.

**Edge tracking.** A WAIT is *in progress* from its first stalled attempt
until it completes, or until the host writes PC or restarts the core. The
first attempt samples `prev = pins_in[p]`, clears the sticky `event` bit, and
never completes for edge kinds. After that, every enabled clock cycle (tick
or not):

```
ev    = transition(prev, pins_in[p]) && (!qen || Q)     // rise: !prev && cur, fall: prev && !cur, edge: prev != cur
done  = event || ev                                     // used if this cycle is a tick
event = event || ev;  prev = pins_in[p]
```

So edges are caught at full clock resolution even when the core ticks slowly.
Only edges that happen after the WAIT began count.

**Timeout** (`timeout = 1`) uses `R[treg]` as a tick budget. On each tick
where the WAIT attempts but its condition doesn't hold:

* If `R[treg] == 0`, the WAIT completes with `T = 1`.
* Otherwise `R[treg] -= 1` and the WAIT stalls.

If the condition holds, the WAIT completes with `T = 0`. So `ldi r3, N`
followed by `wait … timeout r3` gives up after N + 1 unsuccessful ticks.

**IN src, n.** Shift *n* bits of `src` into ISR (§4.4).

**OUT dst, n.** Shift *n* bits out of OSR (§4.4) and write `data` to `dst`
(§5.1). A PC destination jumps. An ISR destination sets ISR and
`ISR_CNT = n`. OSR, STATUS and FLAGS are not OUT destinations: the shift
happens and `data` is discarded, as for NULL. The assembler rejects them.

**PUSH [iffull] [block|noblock]** (default `block`):

1. If `iffull` and `ISR_CNT < PUSH_THRESH`, do nothing.
2. Otherwise, if RXF is full: stall if `block`; else complete and clear ISR,
   dropping the data.
3. Otherwise push ISR, then clear ISR and ISR_CNT.

**PULL [ifempty] [block|noblock]**:

1. If `ifempty` and `OSR_CNT < PULL_THRESH`, do nothing.
2. Otherwise, if TXF is empty: stall if `block`; else `OSR = R0` and
   `OSR_CNT = 0`.
3. Otherwise `OSR = pop(TXF)` and `OSR_CNT = 0`.

**MOV dst, op src.** `value = op(src)`, where op 0 = none, 1 = invert (`~`,
32-bit), 2 = bit-reverse (`::`, 32-bit) and 3 = reserved (behaves as none).
The value is then written to `dst` (§5.1). MOV does not touch flags.

**SET pins|pindirs, v.** Write `v` to `SET_COUNT` pins from `SET_BASE`.

**LDI rd, imm16.** `R[rd] = imm16`.

**ALU aop, rd, (rs | #imm13).** Compute on `a = R[rd]` and `s`, where
`s = imm ? zext(imm13) : R[rs]`:

| aop | Mnemonic | Result written to rd     | Z            | C                         |
|-----|----------|--------------------------|--------------|---------------------------|
| 0   | `add`    | `a + s` (16 bit)         | result == 0  | carry out of bit 15       |
| 1   | `sub`    | `a − s`                  | result == 0  | borrow (`a < s` unsigned) |
| 2   | `and`    | `a & s`                  | result == 0  | 0                         |
| 3   | `or`     | `a \| s`                 | result == 0  | 0                         |
| 4   | `xor`    | `a ^ s`                  | result == 0  | 0                         |
| 5   | `shl`    | `a << s[3:0]`            | result == 0  | 0                         |
| 6   | `shr`    | `a >> s[3:0]` (logical)  | result == 0  | 0                         |
| 7   | `cmp`    | — (like sub, no write)   | `a == s`     | `a < s`                   |

**PIN p, action.** Direct control of one absolute pin:

* 0 = `lo`: `pin_out = 0`, `pin_oe = 1`
* 1 = `hi`: `pin_out = 1`, `pin_oe = 1`
* 2 = `toggle`: `pin_out ^= 1`
* 3 = `hiz`: `pin_oe = 0`

**FLAG set|clr k.** Set or clear global flag `F[k]`.

**HALT.** The core stops with `STATUS.halted = 1` until it is restarted.

### 5.3 Cycle semantics and conflicts

The whole chip is one synchronous state machine. Every core, the host bus and
the pin/flag logic read the state at the start of cycle *t*, and their
effects combine into the state for cycle *t + 1*.

* Host register writes in cycle *t* take effect for cycle *t + 1*. The core
  uses the old configuration during *t*.
* A host write to R0–R3 or PC overrides the core's own write to that register
  in the same cycle. A host PC write also cancels any pending delay and any
  in-progress WAIT.
* A `CTRL` write is the exception: it is registered first, so a write in
  cycle *t* acts in cycle *t + 1*. Its restart or FIFO clear overrides
  everything that core (or that FIFO) would do in *t + 1*, and its enable
  takes effect for *t + 2*. (This keeps the host address decode out of the
  cores' execute path. The SPI host cannot observe the extra cycle.)
* FIFO full/empty are evaluated at the start of the cycle. A host push to a
  full TXF is dropped even if the core pops in the same cycle, and a core
  can't pull a word the host pushes in the same cycle.
* Global flags: `F(t+1) = (F(t) & ~clears) | sets`, over all cores and the
  host. When a set and a clear land in the same cycle, the set wins.

## 6. Host interface

### 6.1 Host port (SPI, 1-bit or quad)

SPI mode 0 (CPOL = 0, CPHA = 0), MSB first, `CS_n` active low.
**Source-synchronous:** SCK clocks the port's shift registers directly, so SCK
can run at up to the system clock: about 50 Mbit/s in 1-bit mode and
200 Mbit/s in quad mode at 50 MHz (`test_host_port_fast` measures 193 Mbit/s
for a quad burst).

| Line | 1-bit | Quad |
|---|---|---|
| `ui[7]` | CS_n | CS_n |
| `ui[6]` | SCK | SCK |
| `ui[5]` | MOSI | IO3 (MSB of each nibble, and the command bit) |
| `ui[4:2]` | protocol inputs | IO2..IO0 (host to chip) |
| `uo[7]` | MISO | IO3 |
| `uo[6:4]` | protocol outputs | IO2..IO0 during a quad read's data, else the cores' |

The host samples on SCK rises and changes its outputs on SCK falls; so does
the chip. Each transaction (CS_n low → high) is:

```
cmd[7:0]  addr[15:0]  [8 dummy SCK, reads only]  32-bit words...
```

The command byte is always sent 1-bit on IO3. After a quad command, the
address and data move 4 bits per SCK (IO3 = MSB).

| cmd  | Quad cmd | Name          | Data                                              |
|------|----------|---------------|---------------------------------------------------|
| 0x02 | 0x42 | WRITE         | 32-bit words; addr increments after each              |
| 0x0A | 0x4A | WRITE_FIXED   | 32-bit words, all to addr (FIFO bursts)               |
| 0x03 | 0x43 | READ          | after 8 dummy SCK, 32-bit words; addr increments      |
| 0x0B | 0x4B | READ_FIXED    | after 8 dummy SCK, 32-bit words, all from addr        |

* **Quad commands** are accepted only while `HOSTCFG.QUAD_EN` = 1, which a
  1-bit write sets; otherwise they are ignored like unknown commands. Change
  `QUAD_EN` only between transactions. The pins it borrows
  (`ui[4:2]`, `uo[6:4]`) are the board designer's to give up.
* **Writes** happen when the last bit of each word has been received.
* **Reads:** the 8 dummy SCK let the port fetch the first word, and it keeps
  one word ahead after that. Read data is captured (a *snapshot*) before the
  word is sent. A word's side effect (a FIFO pop) happens only once its last
  bit has been clocked out. Ending the transaction (or aborting mid-word)
  cancels the words fetched ahead. So FIFO reads lose nothing. On the
  internal host bus this is three operations, at most one per cycle and at
  most two words outstanding:
  * `snap` captures the data at an address. At a core's FIFO address it
    reserves the next unread RXF entry (the head, or the one after a real
    reservation) and records whether it was a real entry; an empty or joined
    FIFO reads 0.
  * `commit` marks the oldest snapped word as sent, and pops it if it was real.
  * `cancel` (the transaction ended) drops all reservations; so does a FIFO
    clear.

  A word the core pushes while the host reads an empty RXF (and gets 0)
  stays for the next read.
* **Unknown commands** are ignored until CS_n goes high.

Timing, in system clocks (clk), at any phase between SCK and clk:

| Parameter | Min |
|---|---|
| SCK period | 1 (SCK ≤ clk) |
| CS_n low to the first SCK rise | 1 |
| last SCK fall to CS_n high | 1 |
| CS_n high between transactions | 4 |

Words cross from the SCK domain to clk through 2-flop synchronizers, and their
data is held for at least 8 SCK, longer than the crossing. MISO/IO outputs
change on the SCK fall, so at SCK = clk the host gets half a period, less the
pad and board delays, to sample them. `test_host_port_fast` runs both modes at
SCK = clk and clk/2 at random phases. `test_spi_port_timing` sweeps slower SCK,
duty cycles and CS timing.

### 6.2 Register map (32-bit word addresses)

| Address            | Name         | Access | Description                                  |
|--------------------|--------------|--------|----------------------------------------------|
| 0x0000–0x03FF      | IMEM         | W      | instruction words                            |
| 0x0400 + 0x20·c    | core *c* block | —    | see below                                    |
| 0x0800             | ID           | R      | `0x50450002`                                 |
| 0x0801             | CTRL         | RW     | `[3:0]` enable; write-only, self-clearing: `[11:8]` restart, `[19:16]` FIFO clear |
| 0x0802             | FLAGS_SET    | RW     | R: flags; W: set bits where 1                |
| 0x0803             | FLAGS_CLR    | RW     | R: flags; W: clear bits where 1              |
| 0x0804             | BANK_OWNER   | RW     | `[2b+1:2b]` owner core of bank b (reset 0xE4) |
| 0x0805             | PIN_OWNER_LO | RW     | `[2i+1:2i]` owner of pin i (0–15)            |
| 0x0806             | PIN_OWNER_HI | RW     | owner of pin 16+i                            |
| 0x0807             | OD_MASK      | RW     | `[7:0]` open-drain enable for P0–P7          |
| 0x0808             | PINS         | R      | `pins_in[31:0]`                              |
| 0x0809             | PIN_OUT      | RW     | `pin_out[31:0]`; a write replaces it (see below) |
| 0x080A             | PIN_OE       | RW     | `pin_oe[31:0]`; a write replaces it          |
| 0x080B             | HOSTCFG      | RW     | `[0]` QUAD_EN: accept quad commands (§6.1)   |

Core block (offset from `0x0400 + 0x20·c`):

| Off  | Name      | Access | Fields                                                                 |
|------|-----------|--------|------------------------------------------------------------------------|
| 0x00 | CLKDIV    | RW     | `[23:8]` INT, `[7:0]` FRAC (reset INT = 1)                              |
| 0x01 | PINCTRL0  | RW     | `[4:0]` OUT_BASE, `[13:8]` OUT_COUNT, `[20:16]` SET_BASE, `[26:24]` SET_COUNT |
| 0x02 | PINCTRL1  | RW     | `[4:0]` SIDE_BASE, `[10:8]` SIDE_COUNT, `[12]` SIDE_OPT, `[13]` SIDE_PINDIR, `[20:16]` IN_BASE |
| 0x03 | SHIFTCTRL | RW     | `[0]` OUT_SHIFT_RIGHT, `[1]` IN_SHIFT_RIGHT, `[2]` AUTOPULL, `[3]` AUTOPUSH, `[4]` FJOIN_TX (§4.4), `[12:8]` PULL_THRESH, `[20:16]` PUSH_THRESH |
| 0x04 | ENTRY     | RW     | `[9:0]` restart PC (reset 256·c)                                       |
| 0x05 | WRAP      | RW     | `[9:0]` WRAP_BOTTOM (reset 0), `[25:16]` WRAP_TOP (reset 1023)         |
| 0x06 | CRCCTRL   | RW     | `[15:0]` POLY, `[19:16]` CRC_W (width − 1), `[20]` IN_TAP, `[21]` OUT_TAP |
| 0x07 | CRC       | RW     | `[15:0]` CRC register                                                  |
| 0x08 | PC        | RW     | current PC; a write sets it                                            |
| 0x09 | STATUS    | R      | `[3:0]` TXF lvl, `[7:4]` RXF lvl, `[8]` Z, `[9]` C, `[10]` T, `[12]` stalled, `[13]` halted, `[14]` fault |
| 0x0A–0x0D | R0–R3 | RW   | registers                                                              |
| 0x0E | ISR       | R      |                                                                        |
| 0x0F | OSR       | R      |                                                                        |
| 0x10 | FIFO      | RW     | W: push TXF (dropped if full); R: pop RXF (0 if empty)                 |

A host write to PIN_OUT or PIN_OE replaces the whole register, ignoring pin
ownership and overriding every core's writes in that cycle. It is meant for
putting pins into a known state (for example releasing open-drain lines)
before starting programs, and for host-driven GPIO.

`STATUS.stalled` is 1 when the last attempted instruction stalled. Unlisted
addresses read as 0 and ignore writes.

## 7. Reduced builds

A build may implement fewer than four cores (`NCORES`) and IMEM banks
(`NBANKS`), and chooses the FIFO depth `D` (4 or 8 words per direction;
"full" in this document means D entries). The IHP tapeout's values (2 cores,
2 banks, D = 8) are the defaults in `src/project.v`. For the parts that don't
exist:

* **Missing core *c*:** its register block reads as 0 and ignores writes
  (including FIFO pushes). `CTRL.enable[c]` and `CTRL.restart[c]` are stored or
  accepted but have no effect. Pins and flags are never written by it. A pin
  owned by a missing core keeps its value unless the host writes `PIN_OUT` or
  `PIN_OE`.
* **Missing bank *b*:** host writes to it are ignored and fetches from it read
  instruction 0 (`jmp 0`). `BANK_OWNER` still resets to bank *b* → core *b*, so
  a present core that runs into a missing bank faults (§3), unless the host has
  explicitly given it that bank.
