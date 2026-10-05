"""Assembler for protoemu programs (.pasm). Syntax documented in docs/asm.md.

    prog = assemble(open("uart_tx.pasm").read())
    prog.words            # list of 32-bit instruction words, relative to address 0
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
        if not isinstance(base, int) or not 0 <= base < 1024:
            raise ValueError("relocation base must be in 0..1023")
        out = []
        for i, w in enumerate(self.words):
            if i in self.reloc:
                target = (w & 0x3FF) + base
                if target >= 1024:
                    raise ValueError("relocated JMP exceeds address space")
                w = (w & ~0x3FF) | target
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
                try:
                    prog.config["clkdiv"] = isa.clkdiv_fields(float(args[0]))
                except ValueError as e:
                    raise AsmError(f"line {lineno}: {e}") from None
            elif d in (".out_pins", ".set_pins"):
                prog.config[d[1:]] = (_eval(args[0], defs, lineno), _eval(args[1], defs, lineno))
            elif d in (".side_pins", ".in_pins"):
                prog.config[d[1:]] = _eval(args[0], defs, lineno)
            elif d == ".fifo":
                if args != ["join_tx"]:
                    raise AsmError(f"line {lineno}: .fifo takes join_tx")
                prog.config["fjoin"] = True
            elif d in (".shift_out", ".shift_in"):
                auto_kw = "autopull" if d == ".shift_out" else "autopush"
                if not args or args[0] not in ("left", "right"):
                    raise AsmError(f"line {lineno}: {d} needs left or right")
                auto = auto_kw in args[1:]
                th = [a for a in args[1:] if a != auto_kw]
                if len(th) > 1:
                    raise AsmError(f"line {lineno}: {d} right|left [{auto_kw}] [threshold]")
                n = _eval(th[0], defs, lineno) if th else 32
                if not 1 <= n <= 32:
                    raise AsmError(f"line {lineno}: threshold must be 1..32")
                prog.config[d[1:]] = (args[0] == "right", auto, n)
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


class _Line:
    """One instruction being encoded: its mnemonic, operands and helpers."""

    def __init__(self, line, defs, lineno):
        self.line, self.defs, self.lineno = line, defs, lineno
        parts = line.replace(",", " , ").split()
        self.mn, self.rest = parts[0].lower(), " ".join(parts[1:])
        self.args = [a.strip() for a in self.rest.split(",")] if self.rest else []

    def err(self, msg):
        return AsmError(f"line {self.lineno}: {msg}: '{self.line}'")

    def value(self, s):
        return _eval(s, self.defs, self.lineno)

    def need(self, n, usage):
        if len(self.args) != n:
            raise self.err(usage)
        return self.args

    def loc(self, s):
        s = s.strip().lower()
        if s not in LOCS:
            raise self.err(f"unknown operand {s}")
        return LOCS[s]

    def count(self, s):
        n = self.value(s)
        if not 1 <= n <= 32:
            raise self.err("bit count must be 1..32")
        return n & 31

    def reg(self, s):
        s = s.strip().lower()
        if s in ("r0", "r1", "r2", "r3", "x", "y"):
            return LOCS[s]
        raise self.err(f"expected register, got {s}")

    def ranged(self, s, lo, hi, what):
        v = self.value(s)
        if not lo <= v < hi:
            raise self.err(f"{what} {lo}..{hi - 1}")
        return v


def _side_delay(prog, side, delay, ln):
    """The 8-bit side-set/delay field (docs/isa.md §4.3)."""
    dbits = 8 - prog.side_count - prog.side_opt
    sd = 0
    if delay is not None:
        d = ln.value(delay)
        if not 0 <= d < (1 << dbits):
            raise ln.err(f"delay {d} doesn't fit in {dbits} bits")
        sd |= d
    if side is not None:
        if prog.side_count == 0:
            raise ln.err("side-set used without .side_set")
        v = ln.value(side)
        if not 0 <= v < (1 << prog.side_count):
            raise ln.err("side value too large")
        sd |= v << dbits
        if prog.side_opt:
            sd |= 1 << 7
    elif prog.side_count and not prog.side_opt:
        raise ln.err("side-set is mandatory (declare .side_set N opt to make it optional)")
    return sd


_JMP_SIMPLE = {"z": isa.C_Z, "!z": isa.C_NZ, "c": isa.C_C, "!c": isa.C_NC, "t": isa.C_T,
               "!osre": isa.C_NOSRE, "txne": isa.C_TXNE, "rxf": isa.C_RXF}


def _jmp(ln):
    if len(ln.args) == 1:
        cond, sel, tgt = isa.C_ALWAYS, 0, ln.args[0]
    elif len(ln.args) == 2:
        c_orig, tgt = ln.args[0].split(), ln.args[1]
        c = [t.lower() for t in c_orig]
        cs = " ".join(c)
        m = re.fullmatch(r"(!?)(r[0-3]|x|y)(--)?", cs)
        if m:
            if m.group(1) and m.group(3):
                raise ln.err("can't combine ! and --")
            sel = ln.reg(m.group(2))
            cond = isa.C_DEC if m.group(3) else (isa.C_ZERO if m.group(1) else isa.C_NZERO)
        elif c[0] in ("pin", "!pin"):
            cond, sel = (isa.C_NPIN if c[0][0] == "!" else isa.C_PIN), ln.value(" ".join(c_orig[1:]))
        elif c[0] in ("f", "!f", "flag", "!flag"):
            cond, sel = (isa.C_NF if c[0][0] == "!" else isa.C_F), ln.value(" ".join(c_orig[1:]))
            if sel > 7:
                raise ln.err("flag index 0..7")
        elif cs in _JMP_SIMPLE:
            cond, sel = _JMP_SIMPLE[cs], 0
        else:
            raise ln.err("unknown jmp condition")
        if not 0 <= sel < 32:
            raise ln.err("jmp selector out of range")
    else:
        raise ln.err("jmp takes [cond,] target")
    addr = ln.ranged(tgt, 0, 1024, "jmp target")
    return isa.OP_JMP, (cond << 15) | (sel << 10) | addr


def _wait(ln):
    # wait <1|0> pin p | <1|0> flag k | rise|fall|edge p  [if pin q == v] [clear] [timeout rN]
    body = ln.rest.replace(",", " ")
    to, treg = 0, 3
    m = re.search(r"\btimeout(?:\s+(r[0-3]|x|y))?\b", body, re.I)
    if m:
        to = 1
        treg = ln.reg(m.group(1)) if m.group(1) else 3
        body = body[: m.start()] + body[m.end():]
    clr = 0
    if re.search(r"\bclear\b", body, re.I):
        clr = 1
        body = re.sub(r"\bclear\b", "", body, flags=re.I)
    qen = qpol = qpin = 0
    m = re.search(r"\bif\s+pin\s+(.+?)\s*==\s*([01])\s*$", body.strip(), re.I)
    if m:
        qen, qpin, qpol = 1, ln.value(m.group(1)), int(m.group(2))
        body = body.strip()[: m.start()]
        if not 0 <= qpin < 32:
            raise ln.err("qualifier pin out of range")
    toks = body.split()
    kw = [t.lower() for t in toks]
    if len(toks) >= 3 and kw[0] in ("1", "0") and kw[1] == "pin":
        kind, idx = (isa.W_PIN1 if toks[0] == "1" else isa.W_PIN0), ln.value(" ".join(toks[2:]))
    elif len(toks) >= 3 and kw[0] in ("1", "0") and kw[1] in ("flag", "irq"):
        kind, idx = (isa.W_FLAG1 if toks[0] == "1" else isa.W_FLAG0), ln.value(" ".join(toks[2:]))
        if idx > 7:
            raise ln.err("flag index 0..7")
    elif len(toks) >= 2 and kw[0] in ("rise", "fall", "edge"):
        kind = {"rise": isa.W_RISE, "fall": isa.W_FALL, "edge": isa.W_EDGE}[kw[0]]
        idx = ln.value(" ".join(toks[1:]))
    else:
        raise ln.err("bad wait")
    if clr and kind != isa.W_FLAG1:
        raise ln.err("'clear' only valid with 'wait 1 flag'")
    if not 0 <= idx < 32:
        raise ln.err("pin out of range")
    return isa.OP_WAIT, ((kind << 16) | (idx << 11) | (to << 10) | (treg << 8) | (clr << 7)
                         | (qen << 6) | (qpol << 5) | qpin)


def _in_out(ln):
    where, n = ln.need(2, f"{ln.mn} takes loc, count")
    where = ln.loc(where)
    if ln.mn == "out" and where not in isa.OUT_DSTS:
        raise ln.err(f"out can't write {ln.args[0]}")
    return (isa.OP_IN if ln.mn == "in" else isa.OP_OUT), (where << 15) | ln.count(n)


def _push_pull(ln):
    toks = ln.rest.lower().split()
    cond_kw = "iffull" if ln.mn == "push" else "ifempty"
    bad = [t for t in toks if t not in (cond_kw, "block", "noblock")]
    if bad or ("block" in toks and "noblock" in toks) or len(set(toks)) != len(toks):
        raise ln.err(f"{ln.mn} [{cond_kw}] [block|noblock]")
    x = (int(cond_kw in toks) << 18) | (int("noblock" not in toks) << 17)
    return (isa.OP_PUSH if ln.mn == "push" else isa.OP_PULL), x


def _mov(ln):
    dst, src = ln.need(2, "mov takes dst, src")
    mop = isa.M_NONE
    if src.startswith("::"):
        mop, src = isa.M_REV, src[2:]
    elif src[:1] in ("!", "~"):
        mop, src = isa.M_INV, src[1:]
    return isa.OP_MOV, (ln.loc(dst) << 15) | (mop << 13) | (ln.loc(src) << 9)


def _set(ln):
    dst, v = ln.need(2, "set pins|pindirs, value")
    if dst.lower() not in ("pins", "pindirs"):
        raise ln.err("set pins|pindirs, value")
    return isa.OP_SET, (int(dst.lower() == "pindirs") << 18) | ln.ranged(v, 0, 32, "set value")


def _ldi(ln):
    rd, v = ln.need(2, "ldi rN, value")
    return isa.OP_LDI, (ln.reg(rd) << 16) | ln.ranged(v, 0, 65536, "ldi immediate")


def _alu(ln):
    rd, src = ln.need(2, f"{ln.mn} rN, rM|value")
    x = (isa.ALU_NAMES.index(ln.mn) << 16) | (ln.reg(rd) << 14)
    if src.lower() in ("r0", "r1", "r2", "r3", "x", "y"):
        return isa.OP_ALU, x | ln.reg(src)
    return isa.OP_ALU, x | (1 << 13) | ln.ranged(src.lstrip("#"), 0, 8192, "alu immediate")


def _pin(ln):
    p, action = ln.need(2, "pin p, lo|hi|toggle|hiz")
    if action.lower() not in isa.PIN_ACTIONS:
        raise ln.err("pin p, lo|hi|toggle|hiz")
    return isa.OP_PIN, (isa.PIN_ACTIONS.index(action.lower()) << 17) | ln.ranged(p, 0, 32, "pin")


def _flag(ln):
    toks = ln.rest.split()
    if len(toks) < 2 or toks[0].lower() not in ("set", "clr", "clear"):
        raise ln.err("flag set|clr k")
    return isa.OP_FLAG, (int(toks[0].lower() != "set") << 18) | ln.ranged(" ".join(toks[1:]), 0, 8, "flag index")


_ENCODERS = {
    "nop": lambda ln: (isa.OP_MOV, (isa.NULL << 15) | (isa.NULL << 9)),
    "jmp": _jmp, "wait": _wait, "in": _in_out, "out": _in_out, "push": _push_pull, "pull": _push_pull,
    "mov": _mov, "set": _set, "ldi": _ldi, "pin": _pin, "flag": _flag,
    "halt": lambda ln: (isa.OP_HALT, 0),
    **{name: _alu for name in isa.ALU_NAMES},
}


def _encode(line, prog, defs, lineno):
    """(word, is_jump) for one source line; jumps are relocated later."""
    if line.startswith(".word"):
        return _eval(line[5:], defs, lineno) & 0xFFFFFFFF, False
    line, side, delay = _split_suffix(line, lineno)
    ln = _Line(line, defs, lineno)
    sd = _side_delay(prog, side, delay, ln)
    if ln.mn not in _ENCODERS:
        raise ln.err("unknown mnemonic")
    op, x = _ENCODERS[ln.mn](ln)
    return isa.enc(op, sd, x), op == isa.OP_JMP
