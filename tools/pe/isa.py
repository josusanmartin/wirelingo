"""Instruction encodings for protoemu (see docs/isa.md §5)."""

# Opcodes
OP_JMP, OP_WAIT, OP_IN, OP_OUT, OP_PUSH, OP_PULL, OP_MOV, OP_SET = range(8)
OP_LDI, OP_ALU, OP_PIN, OP_FLAG, OP_HALT = range(8, 13)

OPNAMES = {
    OP_JMP: "jmp", OP_WAIT: "wait", OP_IN: "in", OP_OUT: "out",
    OP_PUSH: "push", OP_PULL: "pull", OP_MOV: "mov", OP_SET: "set",
    OP_LDI: "ldi", OP_ALU: "alu", OP_PIN: "pin", OP_FLAG: "flag", OP_HALT: "halt",
}

# src/dst operand codes (§5.1)
R0, R1, R2, R3, PINS, PINDIRS, ISR, OSR, NULL, STATUS, FLAGS, PC, CRC = range(13)
LOC_NAMES = ["r0", "r1", "r2", "r3", "pins", "pindirs", "isr", "osr",
             "null", "status", "flags", "pc", "crc"]

# JMP conditions
(C_ALWAYS, C_ZERO, C_NZERO, C_DEC, C_PIN, C_NPIN, C_Z, C_NZ, C_C, C_NC,
 C_T, C_NOSRE, C_TXNE, C_RXF, C_F, C_NF) = range(16)

# WAIT kinds
W_PIN1, W_PIN0, W_RISE, W_FALL, W_EDGE, W_FLAG1, W_FLAG0 = range(7)

# ALU ops
A_ADD, A_SUB, A_AND, A_OR, A_XOR, A_SHL, A_SHR, A_CMP = range(8)
ALU_NAMES = ["add", "sub", "and", "or", "xor", "shl", "shr", "cmp"]

# PIN actions
P_LO, P_HI, P_TOGGLE, P_HIZ = range(4)
PIN_ACTIONS = ["lo", "hi", "toggle", "hiz"]

# MOV ops
M_NONE, M_INV, M_REV = range(3)

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
    if op == OP_JMP:
        cond, sel, addr = bits(x, 18, 15), bits(x, 14, 10), bits(x, 9, 0)
        cs = ["", "!r{r}", "r{r}", "r{r}--", "pin {s}", "!pin {s}", "z", "!z",
              "c", "!c", "t", "!osre", "txne", "rxf", "f {f}", "!f {f}"][cond]
        cs = cs.format(r=sel & 3, s=sel, f=sel & 7)
        return f"jmp {cs + ', ' if cs else ''}{addr}{sfx}"
    if op == OP_WAIT:
        kind, idx = bits(x, 18, 16), bits(x, 15, 11)
        to, treg, clr = bits(x, 10, 10), bits(x, 9, 8), bits(x, 7, 7)
        qen, qpol, qpin = bits(x, 6, 6), bits(x, 5, 5), bits(x, 4, 0)
        k = ["1 pin {i}", "0 pin {i}", "rise {i}", "fall {i}", "edge {i}",
             "1 flag {f}", "0 flag {f}", "reserved"][kind].format(i=idx, f=idx & 7)
        q = f" if pin {qpin} == {qpol}" if qen else ""
        return f"wait {k}{q}{' clear' if clr else ''}{f' timeout r{treg}' if to else ''}{sfx}"
    if op in (OP_IN, OP_OUT):
        loc, cnt = bits(x, 18, 15), bits(x, 4, 0) or 32
        name = LOC_NAMES[loc] if loc < len(LOC_NAMES) else f"loc{loc}"
        return f"{OPNAMES[op]} {name}, {cnt}{sfx}"
    if op in (OP_PUSH, OP_PULL):
        a, b = bits(x, 18, 18), bits(x, 17, 17)
        q = (("iffull " if op == OP_PUSH else "ifempty ") if a else "")
        return f"{OPNAMES[op]} {q}{'block' if b else 'noblock'}{sfx}"
    if op == OP_MOV:
        dst, mop, src = bits(x, 18, 15), bits(x, 14, 13), bits(x, 12, 9)
        n = lambda l: LOC_NAMES[l] if l < len(LOC_NAMES) else f"loc{l}"
        return f"mov {n(dst)}, {['', '~', '::', '?'][mop]}{n(src)}{sfx}"
    if op == OP_SET:
        return f"set {'pindirs' if bits(x, 18, 18) else 'pins'}, {bits(x, 4, 0)}{sfx}"
    if op == OP_LDI:
        return f"ldi r{bits(x, 17, 16)}, {bits(x, 15, 0)}{sfx}"
    if op == OP_ALU:
        aop, rd, imm = bits(x, 18, 16), bits(x, 15, 14), bits(x, 13, 13)
        s = f"{bits(x, 12, 0)}" if imm else f"r{bits(x, 1, 0)}"
        return f"{ALU_NAMES[aop]} r{rd}, {s}{sfx}"
    if op == OP_PIN:
        return f"pin {bits(x, 4, 0)}, {PIN_ACTIONS[bits(x, 18, 17)]}{sfx}"
    if op == OP_FLAG:
        return f"flag {'clr' if bits(x, 18, 18) else 'set'} {bits(x, 2, 0)}{sfx}"
    if op == OP_HALT:
        return f"halt{sfx}"
    return f".word {word:#010x}"
