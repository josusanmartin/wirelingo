# protoemu assembly (`.pasm`)

The assembler lives in `tools/pe/asm.py`. The instruction semantics are in
[isa.md](isa.md).

```python
from pe import asm, host
prog = asm.assemble(open("firmware/uart_rx.pasm").read(), {"RX": 16})
writes = host.load_program(core=1, prog=prog, in_pins=16, clkdiv=host.clkdiv_for(50e6, 8 * 115200))
```

## Lexical rules

* **Comments:** `;` or `//` to end of line.
* **Labels:** `name:`, either on their own line or before an instruction.
* **Numbers:** decimal, `0x…` or `0b…`. Operand fields accept simple
  expressions (`+ - * / % << >> | & ~`).
* **Symbols:** from `.define`, labels, the `defines` argument of `assemble()`,
  and built-in pin names:

  | Pin name  | Logical pins |
  |-----------|--------------|
  | `uio0–7`  | P0–P7        |
  | `uo0–6`   | P8–P14       |
  | `ui0–4`   | P16–P20      |
  | `v0–7`    | P24–P31 (virtual) |
  | `p0–31`   | P0–P31       |

* **Case:** mnemonics and keywords are case-insensitive; symbols are
  case-sensitive.

## Directives

| Directive | Effect |
|-----------|--------|
| `.program name` | start a program (a file may contain several) |
| `.define NAME expr` | constant |
| `.origin addr` | load address (otherwise the host picks one) |
| `.side_set N [opt] [pindirs]` | N side-set pins (0–4); `opt` makes side-set optional per instruction; `pindirs` drives OE instead of the value |
| `.wrap_target` / `.wrap` | loop bounds: execution falls from the instruction before `.wrap` back to `.wrap_target` for free |
| `.clkdiv f` | default clock divider (e.g. `.clkdiv 54.25`) |
| `.out_pins base count`, `.set_pins base count`, `.side_pins base`, `.in_pins base` | default pin mapping |
| `.shift_out right\|left [autopull] [thresh]`, `.shift_in right\|left [autopush] [thresh]` | default shift configuration |
| `.fifo join_tx` | set SHIFTCTRL.FJOIN_TX: RXF becomes a second TX stage (2·D words of TX buffering) |
| `.word value` | raw 32-bit instruction word |

Pin and shift directives are defaults. `host.load_program(..., out_pins=…)`
and friends override them.

## Instruction suffixes

Every instruction can end with `side v` (side-set value) and/or `[d]` (delay
in ticks), in either order: `out pins, 1 side 0 [7]`. The number of delay
bits is `8 − side_count − opt`. Without `opt`, `side` is mandatory on every
instruction.

## Instructions

```
jmp [cond,] target
    cond: !rN  rN  rN--  pin p  !pin p  z  !z  c  !c  t  !osre  txne  rxf  f k  !f k
wait 1|0 pin p   [if pin q == v] [timeout rN]
wait rise|fall|edge p   [if pin q == v] [timeout rN]
wait 1 flag k [clear] [timeout rN]      wait 0 flag k
wait pins != rN [mask m] [timeout rT]   ; the 8 pins from IN_BASE, masked (default 0xFF), differ from rN's low byte
in  src, n        ; src: r0-r3 pins isr osr null status flags pc crc
out dst, n        ; dst: r0-r3 pins pindirs isr pc crc null
push [iffull] [block|noblock]
pull [ifempty] [block|noblock]
mov dst, [~|::]src        ; ~ invert, :: bit-reverse; `mov crc, ~null` presets the CRC tap
set pins|pindirs, v       ; v = 0..31
ldi rN, imm16
add|sub|and|or|xor|shl|shr|cmp rN, rM|imm13
pin p, lo|hi|toggle|hiz
flag set|clr k
halt
nop
```

`x`/`y` are accepted as aliases of `r0`/`r1`. JMP targets are relative to
the program start and are relocated by `Program.relocate(base)`. Computed
jumps (`out pc`, `mov pc`) are not relocated.

## Example

```
; UART receiver, 8N1, 8 ticks per bit
.program uart_rx
.shift_in right
start:
    wait fall RX                    ; start bit edge, caught at clock resolution
    ldi r0, 7                  [10] ; to the middle of data bit 0
bitloop:
    in pins, 1
    jmp r0--, bitloop          [6]
    jmp pin RX, good                ; stop bit must be high
    flag set 0                      ; framing error
    wait 1 pin RX
    jmp start
good:
    in null, 24                     ; right-align the byte
    push block
```
