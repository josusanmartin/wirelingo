"""Instruction encodings for protoemu (see docs/isa.md §5)."""

# Opcodes
OP_JMP, OP_WAIT, OP_IN, OP_OUT, OP_PUSH, OP_PULL, OP_MOV, OP_SET = range(8)
OP_LDI, OP_ALU, OP_PIN, OP_FLAG, OP_HALT, OP_CALL = range(8, 14)

OPNAMES = {
    OP_JMP: "jmp", OP_WAIT: "wait", OP_IN: "in", OP_OUT: "out",
    OP_PUSH: "push", OP_PULL: "pull", OP_MOV: "mov", OP_SET: "set",
    OP_LDI: "ldi", OP_ALU: "alu", OP_PIN: "pin", OP_FLAG: "flag", OP_HALT: "halt", OP_CALL: "call",
}

# src/dst operand codes (§5.1)
R0, R1, R2, R3, PINS, PINDIRS, ISR, OSR, NULL, STATUS, FLAGS, PC, CRC, LINE, LR, TICKS = range(16)
LOC_NAMES = ["r0", "r1", "r2", "r3", "pins", "pindirs", "isr", "osr",
             "null", "status", "flags", "pc", "crc", "line", "lr", "ticks"]

# OUT can write these; OUT to OSR/STATUS/FLAGS/LR would only discard, so the assembler rejects them
OUT_DSTS = {R0, R1, R2, R3, PINS, PINDIRS, ISR, NULL, PC, CRC, LINE}


def clkdiv_fields(d):
    """CLKDIV (INT, FRAC) for a divider d (ticks per tick of clk / d), FRAC in 1/256."""
    i = int(d)
    f = int(round((d - i) * 256))
    if f == 256:                     # 1.999 -> 2.0, not 1 + 0/256
        i, f = i + 1, 0
    if not 1 <= i < 65536:
        raise ValueError(f"clock divider {d} out of range (1 .. 65535.996)")
    return i, f


# JMP conditions
(C_ALWAYS, C_ZERO, C_NZERO, C_DEC, C_PIN, C_NPIN, C_Z, C_NZ, C_C, C_NC,
 C_T, C_NOSRE, C_TXNE, C_RXF, C_F, C_NF) = range(16)

# WAIT kinds
W_PIN1, W_PIN0, W_RISE, W_FALL, W_EDGE, W_FLAG1, W_FLAG0, W_DIFF = range(8)

# ALU ops
A_ADD, A_SUB, A_AND, A_OR, A_XOR, A_SHL, A_SHR, A_CMP = range(8)
ALU_NAMES = ["add", "sub", "and", "or", "xor", "shl", "shr", "cmp"]

# PIN actions
P_LO, P_HI, P_TOGGLE, P_HIZ = range(4)
PIN_ACTIONS = ["lo", "hi", "toggle", "hiz"]

# MOV ops (v0.3: 3 = table read, `mov osr, [src]`)
M_NONE, M_INV, M_REV, M_TBL = range(4)

# v0.3 additions (docs/isa.md §5, §8), each in encoding space v0.2 reserved or ignored
XC_NT = 1            # JMP/CALL cond 0 (always) with sel 1: `!t` (T == 0)
LDI_CMPW = 1 << 18   # LDI with bit 18 set: `cmp rd, #imm16` (CMPW); rd at the ALU's
                     # [15:14] (its selector is shared with WAIT kind 7), imm16 = {[17:16], [13:0]}
CMPW_IMM_MASK = (3 << 16) | 0x3FFF
FLAG_TOG = 1 << 17   # FLAG with bit 17 set: toggle F[k] (as a set or clear: no new chip wires)
CRC_W_HI = 1 << 23   # CRCCTRL: CRC_W[4], so widths 17..32 (POLY_HI holds the polynomial's top half)


def cmpw_fields(rd, imm):
    """Operand bits of `cmp rd, #imm16` (without opcode)."""
    return LDI_CMPW | (((imm >> 14) & 3) << 16) | (rd << 14) | (imm & 0x3FFF)


def cmpw_imm(x):
    return (((x >> 16) & 3) << 14) | (x & 0x3FFF)


def with_imm16(word, imm):
    """An LDI or CMPW word with its 16-bit immediate replaced (token filters)."""
    if word & LDI_CMPW and (word >> 27) == OP_LDI:
        return (word & ~CMPW_IMM_MASK) | (cmpw_fields(0, imm) & CMPW_IMM_MASK)
    return (word & ~0xFFFF) | (imm & 0xFFFF)


def v03_feature(word):
    """The v0.3 addition a word uses ('cmpw', 'not_t', 'table', 'tog', 'ticks'), or None.
    (A v0.2 program, assembled, never uses one: docs/isa.md §8.)"""
    op, _, x = fields(word)
    if op == OP_LDI and x & LDI_CMPW:
        return "cmpw"
    if op in (OP_JMP, OP_CALL) and bits(x, 18, 15) == C_ALWAYS and bits(x, 14, 10) == XC_NT:
        return "not_t"
    if op == OP_MOV and bits(x, 14, 13) == M_TBL:
        return "table"
    if op == OP_FLAG and x & FLAG_TOG:
        return "tog"
    if (op == OP_MOV and bits(x, 12, 9) == TICKS) or (op == OP_IN and bits(x, 18, 15) == TICKS):
        return "ticks"
    return None


def crcctrl(poly, width, in_tap=0, out_tap=0):
    """(CRCCTRL, POLY_HI) for a CRC of `width` 1..32 bits with polynomial `poly`
    (without the top term)."""
    if not 1 <= width <= 32 or not 0 <= poly < (1 << width):
        raise ValueError("CRC width 1..32 and a polynomial below 2**width")
    w = width - 1
    ctrl = (poly & 0xFFFF) | ((w & 15) << 16) | (in_tap << 20) | (out_tap << 21) | ((w >> 4) << 23)
    return ctrl, poly >> 16


ADDR_BITS = 10
SD_BITS = 8
OPERAND_BITS = 19


def enc(op, sd, operand):
    assert 0 <= op < 32 and 0 <= sd < 256 and 0 <= operand < (1 << OPERAND_BITS)
    return (op << 27) | (sd << 19) | operand


def fields(word):
    return (word >> 27) & 31, (word >> 19) & 0xFF, word & 0x7FFFF


def bits(v, hi, lo):
    return (v >> lo) & ((1 << (hi - lo + 1)) - 1)


def disasm(word):
    """Human-readable form (side/delay field shown raw)."""
    op, sd, x = fields(word)
    sfx = f"  ; sd={sd}" if sd else ""
    if op in (OP_JMP, OP_CALL):
        cond, sel, addr = bits(x, 18, 15), bits(x, 14, 10), bits(x, 9, 0)
        if cond == C_ALWAYS and sel == XC_NT:
            return f"{OPNAMES[op]} !t, {addr}{sfx}"
        cs = ["", "!r{r}", "r{r}", "r{r}--", "pin {s}", "!pin {s}", "z", "!z",
              "c", "!c", "t", "!osre", "txne", "rxf", "f {f}", "!f {f}"][cond]
        cs = cs.format(r=sel & 3, s=sel, f=sel & 7)
        return f"{OPNAMES[op]} {cs + ', ' if cs else ''}{addr}{sfx}"
    if op == OP_WAIT:
        kind, idx = bits(x, 18, 16), bits(x, 15, 11)
        to, treg, clr = bits(x, 10, 10), bits(x, 9, 8), bits(x, 7, 7)
        qen, qpol, qpin = bits(x, 6, 6), bits(x, 5, 5), bits(x, 4, 0)
        if kind == W_DIFF:
            return f"wait pins != r{bits(x, 15, 14)} mask {bits(x, 7, 0):#04x}{f' timeout r{treg}' if to else ''}{sfx}"
        k = ["1 pin {i}", "0 pin {i}", "rise {i}", "fall {i}", "edge {i}",
             "1 flag {f}", "0 flag {f}"][kind].format(i=idx, f=idx & 7)
        q = f" if pin {qpin} == {qpol}" if qen else ""
        return f"wait {k}{q}{' clear' if clr else ''}{f' timeout r{treg}' if to else ''}{sfx}"
    if op in (OP_IN, OP_OUT):
        loc, cnt = bits(x, 18, 15), bits(x, 4, 0) or 32
        name = LOC_NAMES[loc]
        return f"{OPNAMES[op]} {name}, {cnt}{sfx}"
    if op in (OP_PUSH, OP_PULL):
        a, b = bits(x, 18, 18), bits(x, 17, 17)
        q = (("iffull " if op == OP_PUSH else "ifempty ") if a else "")
        return f"{OPNAMES[op]} {q}{'block' if b else 'noblock'}{sfx}"
    if op == OP_MOV:
        dst, mop, src = bits(x, 18, 15), bits(x, 14, 13), bits(x, 12, 9)
        n = lambda l: LOC_NAMES[l] if l < len(LOC_NAMES) else f"loc{l}"
        if mop == M_TBL:
            return f"mov {n(dst)}, [{n(src)}]{sfx}"
        return f"mov {n(dst)}, {['', '~', '::', '?'][mop]}{n(src)}{sfx}"
    if op == OP_SET:
        return f"set {'pindirs' if bits(x, 18, 18) else 'pins'}, {bits(x, 4, 0)}{sfx}"
    if op == OP_LDI and bits(x, 18, 18):
        return f"cmpw r{bits(x, 15, 14)}, {cmpw_imm(x)}{sfx}"
    if op == OP_LDI:
        return f"ldi r{bits(x, 17, 16)}, {bits(x, 15, 0)}{sfx}"
    if op == OP_ALU:
        aop, rd, imm = bits(x, 18, 16), bits(x, 15, 14), bits(x, 13, 13)
        s = f"{bits(x, 12, 0)}" if imm else f"r{bits(x, 1, 0)}"
        return f"{ALU_NAMES[aop]} r{rd}, {s}{sfx}"
    if op == OP_PIN:
        return f"pin {bits(x, 4, 0)}, {PIN_ACTIONS[bits(x, 18, 17)]}{sfx}"
    if op == OP_FLAG:
        if bits(x, 17, 17):
            return f"flag tog {bits(x, 2, 0)}{sfx}"
        return f"flag {'clr' if bits(x, 18, 18) else 'set'} {bits(x, 2, 0)}{sfx}"
    if op == OP_HALT:
        return f"halt{sfx}"
    return f".word {word:#010x}"
