"""Assembler for protoemu programs (.pasm). Syntax documented in docs/asm.md.

    prog = assemble(open("uart_tx.pasm").read())
    prog.words            # list of 24-bit ints, relative to address 0
    prog.relocate(base)   # words with JMP targets offset by base
"""
import re
from dataclasses import dataclass, field

from . import isa

PIN_ALIASES = {}
for i in range(32):
    PIN_ALIASES[f"p{i}"] = i
for i in range(8):
    PIN_ALIASES[f"uio{i}"] = i
    PIN_ALIASES[f"v{i}"] = 24 + i
for i in range(7):
    PIN_ALIASES[f"uo{i}"] = 8 + i
for i in range(5):
    PIN_ALIASES[f"ui{i}"] = 16 + i

LOCS = {n: i for i, n in enumerate(isa.LOC_NAMES)}
LOCS["x"] = 0  # PIO-style aliases
LOCS["y"] = 1


class AsmError(Exception):
    pass


@dataclass
class Program:
    name: str = ""
    words: list = field(default_factory=list)
    reloc: set = field(default_factory=set)  # indices of JMP words
    labels: dict = field(default_factory=dict)
    origin: int = None
    wrap_bottom: int = 0
    wrap_top: int = None
    side_count: int = 0
    side_opt: int = 0
    side_pindir: int = 0
    config: dict = field(default_factory=dict)  # from .clkdiv/.out_pins/... directives
    source_lines: list = field(default_factory=list)  # (word index, line) for listings

    def relocate(self, base):
        out = []
        for i, w in enumerate(self.words):
            if i in self.reloc:
                w = (w & ~0x3FF) | ((w + base) & 0x3FF)
            out.append(w)
        return out

    def listing(self, base=0):
        return "\n".join(f"{base + i:4d}: {w:08x}  {isa.disasm(w):28s} {src}"
                         for (i, src), w in zip(self.source_lines, self.relocate(base)))


def assemble(text, defines=None):
    progs = assemble_all(text, defines)
    if len(progs) != 1:
        raise AsmError(f"expected one .program, got {len(progs)}")
    return progs[0]


def assemble_all(text, defines=None):
    """Assemble a source file containing one or more .program blocks."""
    blocks, cur = [], None
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = re.split(r";|//", raw, maxsplit=1)[0].strip()
        if line.startswith(".program"):
            cur = [line.split()[1] if len(line.split()) > 1 else "", []]
            blocks.append(cur)
            continue
        if cur is None:
            if line:
                cur = ["", []]
                blocks.append(cur)
            else:
                continue
        cur[1].append((lineno, line, raw.strip()))
    return [_assemble_block(name, lines, dict(defines or {})) for name, lines in blocks]


def _eval(expr, defs, lineno):
    expr = expr.strip()
    def sub(m):
        n = m.group(0)
        if n in defs:
            return str(defs[n])
        if n in PIN_ALIASES:
            return str(PIN_ALIASES[n])
        raise AsmError(f"line {lineno}: unknown symbol '{n}'")
    e = re.sub(r"(?<![0-9a-fA-FxXbB])[A-Za-z_][A-Za-z_0-9]*", sub, expr)
    if not re.fullmatch(r"[0-9a-fA-FxXbB_+\-*/%()<>|&~ ]*", e) or not e:
        raise AsmError(f"line {lineno}: bad expression '{expr}'")
    try:
        return int(eval(e, {"__builtins__": {}}, {}))
    except Exception as ex:
        raise AsmError(f"line {lineno}: bad expression '{expr}': {ex}") from None


def _assemble_block(name, lines, defs):
    prog = Program(name=name)
    # pass 1: labels & directives that affect encoding
    items = []  # (lineno, text, raw) of instructions
    pc = 0
    for lineno, line, raw in lines:
        while True:
            m = re.match(r"^([A-Za-z_][A-Za-z_0-9]*):\s*(.*)$", line)
            if not m:
                break
            if m.group(1) in prog.labels:
                raise AsmError(f"line {lineno}: duplicate label {m.group(1)}")
            prog.labels[m.group(1)] = pc
            line = m.group(2)
        if not line:
            continue
        if line.startswith("."):
            parts = line.split()
            d, args = parts[0], parts[1:]
            if d == ".define":
                defs[args[0]] = _eval(" ".join(args[1:]), defs, lineno)
            elif d == ".origin":
                prog.origin = _eval(" ".join(args), defs, lineno)
            elif d == ".side_set":
                prog.side_count = _eval(args[0], defs, lineno)
                prog.side_opt = int("opt" in args[1:])
                prog.side_pindir = int("pindirs" in args[1:])
                if prog.side_count > 4:
                    raise AsmError(f"line {lineno}: side_set too wide")
            elif d == ".wrap_target":
                prog.wrap_bottom = pc
            elif d == ".wrap":
                prog.wrap_top = pc - 1
            elif d == ".word":
                items.append((lineno, line, raw))
                pc += 1
            elif d == ".clkdiv":
                v = float(args[0])
                prog.config["clkdiv"] = (int(v), int(round((v - int(v)) * 256)) & 0xFF)
            elif d in (".out_pins", ".set_pins"):
                prog.config[d[1:]] = (_eval(args[0], defs, lineno), _eval(args[1], defs, lineno))
            elif d in (".side_pins", ".in_pins"):
                prog.config[d[1:]] = _eval(args[0], defs, lineno)
            elif d == ".fifo" and args == ["join_tx"]:
                prog.config["fjoin"] = True
            elif d in (".shift_out", ".shift_in"):
                right = args[0] == "right"
                auto = any(a in ("autopull", "autopush") for a in args[1:])
                th = [a for a in args[1:] if a not in ("autopull", "autopush")]
                prog.config[d[1:]] = (right, auto, _eval(th[0], defs, lineno) if th else 32)
            else:
                raise AsmError(f"line {lineno}: unknown directive {d}")
            continue
        items.append((lineno, line, raw))
        pc += 1
    if prog.wrap_top is None:
        prog.wrap_top = pc - 1
    defs = {**defs, **prog.labels}

    # pass 2: encode
    for idx, (lineno, line, raw) in enumerate(items):
        w, is_jmp = _encode(line, prog, defs, lineno)
        prog.words.append(w)
        if is_jmp:
            prog.reloc.add(idx)
        prog.source_lines.append((idx, raw))
    return prog


def _split_suffix(line, lineno):
    delay, side = None, None
    m = re.search(r"\[([^\]]+)\]\s*$", line)
    if m:
        delay, line = m.group(1), line[: m.start()].rstrip()
    m = re.search(r"\s+side\s+(\S+)\s*$", line)
    if m:
        side, line = m.group(1), line[: m.start()].rstrip()
    if delay is None:  # allow "[d] side x" order too
        m = re.search(r"\[([^\]]+)\]\s*$", line)
        if m:
            delay, line = m.group(1), line[: m.start()].rstrip()
    return line, side, delay


def _encode(line, prog, defs, lineno):
    if line.startswith(".word"):
        return _eval(line[5:], defs, lineno) & 0xFFFFFFFF, False
    line, side, delay = _split_suffix(line, lineno)
    E = lambda s: _eval(s, defs, lineno)
    err = lambda msg: AsmError(f"line {lineno}: {msg}: '{line}'")

    # side/delay field
    dbits = 8 - prog.side_count - prog.side_opt
    sd = 0
    if delay is not None:
        d = E(delay)
        if not 0 <= d < (1 << dbits):
            raise err(f"delay {d} doesn't fit in {dbits} bits")
        sd |= d
    if side is not None:
        if prog.side_count == 0:
            raise err("side-set used without .side_set")
        v = E(side)
        if not 0 <= v < (1 << prog.side_count):
            raise err("side value too large")
        sd |= v << dbits
        if prog.side_opt:
            sd |= 1 << 7
    elif prog.side_count and not prog.side_opt:
        raise err("side-set is mandatory (declare .side_set N opt to make it optional)")

    parts = line.replace(",", " , ").split()
    mn, rest = parts[0].lower(), " ".join(parts[1:])
    args = [a.strip() for a in rest.split(",")] if rest else []

    def loc(s):
        s = s.strip().lower()
        if s not in LOCS:
            raise err(f"unknown operand {s}")
        return LOCS[s]

    def cnt(s):
        n = E(s)
        if not 1 <= n <= 32:
            raise err("bit count must be 1..32")
        return n & 31

    def reg(s):
        s = s.strip().lower()
        if s in ("r0", "r1", "r2", "r3", "x", "y"):
            return LOCS[s]
        raise err(f"expected register, got {s}")

    op, x = None, 0
    if mn == "nop":
        op, x = isa.OP_MOV, (isa.NULL << 15) | (isa.NULL << 9)
    elif mn == "jmp":
        op = isa.OP_JMP
        if len(args) == 1:
            cond, sel, tgt = isa.C_ALWAYS, 0, args[0]
        elif len(args) == 2:
            c_orig, tgt = args[0].split(), args[1]
            c = [t.lower() for t in c_orig]
            cs = " ".join(c)
            m = re.fullmatch(r"(!?)(r[0-3]|x|y)(--)?", cs)
            if m:
                sel = reg(m.group(2))
                cond = isa.C_DEC if m.group(3) else (isa.C_ZERO if m.group(1) else isa.C_NZERO)
                if m.group(1) and m.group(3):
                    raise err("can't combine ! and --")
            elif c[0] in ("pin", "!pin"):
                cond, sel = (isa.C_NPIN if c[0][0] == "!" else isa.C_PIN), E(" ".join(c_orig[1:]))
            elif c[0] in ("f", "!f", "flag", "!flag"):
                cond, sel = (isa.C_NF if c[0][0] == "!" else isa.C_F), E(" ".join(c_orig[1:]))
                if sel > 7:
                    raise err("flag index 0..7")
            else:
                simple = {"z": isa.C_Z, "!z": isa.C_NZ, "c": isa.C_C, "!c": isa.C_NC, "t": isa.C_T,
                          "!osre": isa.C_NOSRE, "txne": isa.C_TXNE, "rxf": isa.C_RXF}
                if cs not in simple:
                    raise err("unknown jmp condition")
                cond, sel = simple[cs], 0
            if not 0 <= sel < 32:
                raise err("jmp selector out of range")
        else:
            raise err("jmp takes [cond,] target")
        addr = E(tgt)
        if not 0 <= addr < 1024:
            raise err("jmp target out of range")
        x = (cond << 15) | (sel << 10) | addr
        return isa.enc(op, sd, x), True
    elif mn == "wait":
        # wait <1|0> pin p | <1|0> flag k | rise|fall|edge p
        #      [if pin q == v] [clear] [timeout rN]
        op = isa.OP_WAIT
        body = rest.replace(",", " ")
        to, treg = 0, 3
        m = re.search(r"\btimeout(?:\s+(r[0-3]|x|y))?\b", body, re.I)
        if m:
            to = 1
            treg = reg(m.group(1)) if m.group(1) else 3
            body = body[: m.start()] + body[m.end():]
        clr = 0
        if re.search(r"\bclear\b", body, re.I):
            clr = 1
            body = re.sub(r"\bclear\b", "", body, flags=re.I)
        qen = qpol = qpin = 0
        m = re.search(r"\bif\s+pin\s+(.+?)\s*==\s*([01])\s*$", body.strip(), re.I)
        if m:
            qen, qpin, qpol = 1, E(m.group(1)), int(m.group(2))
            body = body.strip()[: m.start()]
            if not 0 <= qpin < 32:
                raise err("qualifier pin out of range")
        toks = body.split()
        kw = [t.lower() for t in toks]
        if len(toks) >= 3 and kw[0] in ("1", "0") and kw[1] == "pin":
            kind, idx = (isa.W_PIN1 if toks[0] == "1" else isa.W_PIN0), E(" ".join(toks[2:]))
        elif len(toks) >= 3 and kw[0] in ("1", "0") and kw[1] in ("flag", "irq"):
            kind, idx = (isa.W_FLAG1 if toks[0] == "1" else isa.W_FLAG0), E(" ".join(toks[2:]))
            if idx > 7:
                raise err("flag index 0..7")
        elif len(toks) >= 2 and kw[0] in ("rise", "fall", "edge"):
            kind = {"rise": isa.W_RISE, "fall": isa.W_FALL, "edge": isa.W_EDGE}[kw[0]]
            idx = E(" ".join(toks[1:]))
        else:
            raise err("bad wait")
        if clr and kind != isa.W_FLAG1:
            raise err("'clear' only valid with 'wait 1 flag'")
        if not 0 <= idx < 32:
            raise err("pin out of range")
        x = ((kind << 16) | (idx << 11) | (to << 10) | (treg << 8) | (clr << 7)
             | (qen << 6) | (qpol << 5) | qpin)
    elif mn in ("in", "out"):
        op = isa.OP_IN if mn == "in" else isa.OP_OUT
        if len(args) != 2:
            raise err(f"{mn} takes loc, count")
        x = (loc(args[0]) << 15) | cnt(args[1])
    elif mn in ("push", "pull"):
        op = isa.OP_PUSH if mn == "push" else isa.OP_PULL
        toks = rest.lower().split()
        q = ("iffull" if mn == "push" else "ifempty") in toks
        blk = "noblock" not in toks
        x = (int(q) << 18) | (int(blk) << 17)
    elif mn == "mov":
        op = isa.OP_MOV
        if len(args) != 2:
            raise err("mov takes dst, src")
        s = args[1].strip()
        mop = isa.M_NONE
        if s.startswith("::"):
            mop, s = isa.M_REV, s[2:]
        elif s[:1] in ("!", "~"):
            mop, s = isa.M_INV, s[1:]
        x = (loc(args[0]) << 15) | (mop << 13) | (loc(s) << 9)
    elif mn == "set":
        op = isa.OP_SET
        d = args[0].lower()
        if d not in ("pins", "pindirs"):
            raise err("set pins|pindirs, value")
        v = E(args[1])
        if not 0 <= v < 32:
            raise err("set value 0..31")
        x = (int(d == "pindirs") << 18) | v
    elif mn == "ldi":
        op = isa.OP_LDI
        v = E(args[1])
        if not 0 <= v < 65536:
            raise err("ldi immediate 0..65535")
        x = (reg(args[0]) << 16) | v
    elif mn in isa.ALU_NAMES:
        op = isa.OP_ALU
        aop = isa.ALU_NAMES.index(mn)
        rd = reg(args[0])
        s = args[1].strip()
        if s.lower() in ("r0", "r1", "r2", "r3", "x", "y"):
            x = (aop << 16) | (rd << 14) | reg(s)
        else:
            v = E(s.lstrip("#"))
            if not 0 <= v < 8192:
                raise err("alu immediate 0..8191")
            x = (aop << 16) | (rd << 14) | (1 << 13) | v
    elif mn == "pin":
        op = isa.OP_PIN
        if len(args) != 2 or args[1].lower() not in isa.PIN_ACTIONS:
            raise err("pin p, lo|hi|toggle|hiz")
        p = E(args[0])
        if not 0 <= p < 32:
            raise err("pin out of range")
        x = (isa.PIN_ACTIONS.index(args[1].lower()) << 17) | p
    elif mn == "flag":
        op = isa.OP_FLAG
        toks = rest.split()
        if toks[0].lower() not in ("set", "clr", "clear"):
            raise err("flag set|clr k")
        k = E(" ".join(toks[1:]))
        if not 0 <= k < 8:
            raise err("flag index 0..7")
        x = (int(toks[0].lower() != "set") << 18) | k
    elif mn == "halt":
        op = isa.OP_HALT
    else:
        raise err("unknown mnemonic")
    return isa.enc(op, sd, x), False
