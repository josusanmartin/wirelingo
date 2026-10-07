"""Static firmware timing checker: cycle bounds between labelled points of a
protoemu program, checked against declared deadlines (docs/timing-check.md).

    from pe import asm, timing
    src = open("firmware/uart_tx.pasm").read()
    chk = timing.Checker(asm.assemble(src), timing.parse_annotations(src))
    for r in chk.check():
        print(r.report())

The bounds come from the cycle semantics of docs/isa.md (§4.1 divider, §4.2
execution, §5.2 instructions) and the assumptions written next to the code
(`; @stall`, `; @loop`, `; @assume`). They are compared with the golden model
by test/model/test_timing_check.py (`Tracer` + `measure` below); that is a
check against the cycle model, not a proof.

Annotations live in comments, so the assembled words do not change:

    ; @point NAME                 this instruction (or the next one) is point NAME
    ; @point NAME at=LABEL+k      anywhere: the instruction k words after LABEL
    ; @stall max=N [min=N]        this instruction stalls at most N ticks per execution
    ; @loop max=N                 this instruction executes at most N times inside a deadline interval
    ; @assume clkdiv=D fifo_stall=N line_stall=N core=C
    ; @deadline NAME from=P[.enter|.done|.cond] to=P[.enter|.done] [max=N] [min=N]
    ;     [unit=clk|tick] [adjust=A[..B]] [avoid=P,P] [stall=P:LO..HI,...]
    ;     [after=P[.enter|.done]:LO..HI] [reach=required|conditional] ["why"]

Times are in ticks inside (one tick = one divider period), reported in ticks
and clocks. A point's `enter` is the tick of the instruction's first attempt,
`done` the tick it completes; `P.cond` is the tick at which the condition of
the untimed level WAIT at P becomes true, given LO..HI ticks after the `after`
event (an assumption about the outside world).
"""
import math
import re
import shlex
import sys
from collections import namedtuple
from dataclasses import dataclass, field

from . import asm, isa
from .isa import (OP_JMP, OP_WAIT, OP_IN, OP_OUT, OP_PUSH, OP_PULL, OP_MOV, OP_SET,
                  OP_LDI, OP_ALU, OP_PIN, OP_FLAG, OP_HALT, OP_CALL, bits)

INF = math.inf
KSET = 6            # a register value is a set of at most KSET constants, else unknown (None)
MAX_KEYS = 200000   # exploration limit per deadline
M16 = 0xFFFF


class TimingError(Exception):
    pass


# ------------------------------------------------------------------ abstract values
# A value is None (unknown) or a frozenset of ints and ('ret', pc) link addresses.

def _const(v):
    return frozenset([v])


def _join(a, b):
    if a is None or b is None:
        return None
    u = a | b           # link addresses are few (one per CALL site): never widened
    return u if len(u) <= KSET or all(isinstance(v, tuple) for v in u) else None


def _ints(a):
    return a is not None and all(isinstance(v, int) for v in a)


def _map(a, f):
    if not _ints(a):
        return None
    out = frozenset(f(v) for v in a)
    return out if len(out) <= KSET else None


def _truth(a, f):
    """Possible truth values of f over value a."""
    if not _ints(a):
        return {True, False}
    return {bool(f(v)) for v in a}


BOTH = frozenset([0, 1])

State = namedtuple("State", "r lr z c t isr_cnt osr_cnt txf_ne rxf_nf")
# r: 4 values; lr: value; z/c/t: subsets of {0,1}; isr_cnt/osr_cnt: values;
# txf_ne: the core's pull source is known non-empty (only the core pops it);
# rxf_nf: RXF is known not full (only the core pushes it).

INITIAL = State((None,) * 4, None, _const(0), _const(0), _const(0), _const(0), _const(32), False, False)


def _join_state(a, b):
    return State(tuple(_join(x, y) for x, y in zip(a.r, b.r)), _join(a.lr, b.lr),
                 a.z | b.z, a.c | b.c, a.t | b.t, _join(a.isr_cnt, b.isr_cnt),
                 _join(a.osr_cnt, b.osr_cnt), a.txf_ne and b.txf_ne, a.rxf_nf and b.rxf_nf)


def _setr(st, i, v):
    r = list(st.r)
    r[i] = v
    return st._replace(r=tuple(r))


# ------------------------------------------------------------------ annotations
@dataclass
class Event:
    point: str
    kind: str          # enter | done | cond


@dataclass
class Deadline:
    name: str
    frm: Event
    to: Event
    max: float = None
    min: float = None
    unit: str = "clk"
    adjust: tuple = (0, 0)
    avoid: list = field(default_factory=list)
    after: Event = None
    window: tuple = None   # (LO, HI) ticks from `after` to the cond
    stalls: list = field(default_factory=list)   # [(point, lo, hi)]: @stall for this deadline only
    why: str = ""
    line: int = 0
    reach: str = "required"   # required: a reachable path that never gets to `to` fails the deadline


@dataclass
class Annotations:
    points: dict = field(default_factory=dict)       # name -> [(pc or (label, k), line)]: a set
    stalls: dict = field(default_factory=dict)       # pc-ref -> (lo, hi, line)
    loops: dict = field(default_factory=dict)        # pc-ref -> (N, line)
    assume: dict = field(default_factory=dict)
    deadlines: list = field(default_factory=list)


def _instr_lines(text, program=None):
    """[(lineno, raw)] of the lines that hold instructions, in word order, for
    the selected .program (mirrors pass 1 of asm._assemble_block)."""
    out, cur, seen_prog = [], None, False
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = re.split(r";|//", raw, maxsplit=1)[0].strip()
        if line.startswith(".program"):
            name = line.split()[1] if len(line.split()) > 1 else ""
            if seen_prog and program is None:
                break                         # first program only
            seen_prog = True
            cur = program is None or name == program
            continue
        if cur is None:
            cur = program is None or program == ""
        if not cur:
            continue
        while True:
            m = re.match(r"^([A-Za-z_][A-Za-z_0-9]*):\s*(.*)$", line)
            if not m:
                break
            line = m.group(2)
        if not line or (line.startswith(".") and not line.startswith(".word")):
            out.append((lineno, None))
            continue
        out.append((lineno, raw))
    return out


def _num(s):
    s = s.strip()
    if s in ("inf", "unbounded"):
        return INF
    return float(s) if "." in s else int(s, 0)


def _range(s):
    if ".." in s:
        a, b = s.split("..")
        return _num(a), _num(b)
    v = _num(s)
    return v, v


def _event(s, default):
    name, _, kind = s.partition(".")
    kind = kind or default
    if kind not in ("enter", "done", "cond"):
        raise TimingError(f"bad event kind '{kind}' in '{s}'")
    return Event(name, kind)


def parse_annotations(text, program=None, ann=None):
    """Collect `@` annotations from the comments of a .pasm source (or the
    lines of a sidecar file). A point/stall/loop annotation applies to the
    instruction on its line, or else to the next instruction."""
    ann = ann or Annotations()
    lines = _instr_lines(text, program)
    instr_at = {}            # lineno -> word index (instruction lines)
    idx = 0
    for lineno, raw in lines:
        if raw is not None:
            instr_at[lineno] = idx
            idx += 1
    pending = []             # annotations waiting for the next instruction
    for lineno, raw in enumerate(text.splitlines(), 1):
        if raw.lstrip().startswith("@"):        # a sidecar line: annotation first, then a comment
            body = raw.strip()
        else:                                   # a source line: annotations live in the comment
            m = re.search(r"(?:;|//)(.*)$", raw)
            body = m.group(1).strip() if m else ""
        if lineno in instr_at:
            here = instr_at[lineno]
            for fn in pending:
                fn(here)
            pending = []
        for kw, rest in _ann_items(body):
            _parse_one(ann, kw, rest, lineno, instr_at, pending)
    if pending:
        raise TimingError("an annotation at the end of the source applies to no instruction")
    return ann


_KW = ("point", "stall", "wait", "loop", "assume", "deadline")


def _ann_items(body):
    """[(keyword, rest)] of the `@keyword ...` items in a comment; each runs
    to the next item or the next `;`."""
    ms = list(re.finditer(r"(?:^|(?<=\s))@(%s)\b" % "|".join(_KW), body))
    out = []
    for i, m in enumerate(ms):
        end = ms[i + 1].start() if i + 1 < len(ms) else len(body)
        out.append((m.group(1), body[m.end():end].split(";")[0].strip()))
    return out


def _parse_one(ann, kw, rest, lineno, instr_at, pending):
    toks = shlex.split(rest, comments=False)
    iskv = lambda t: re.match(r"^[A-Za-z_]+=", t) is not None
    kv = dict(t.split("=", 1) for t in toks if iskv(t))
    bare = [t for t in toks if not iskv(t)]

    def attach(fn):
        if "at" in kv:
            lab, _, k = kv["at"].partition("+")
            fn((lab, int(k or 0)))
        elif lineno in instr_at:
            fn(instr_at[lineno])
        else:
            pending.append(fn)
    if kw == "point":
        if not bare:
            raise TimingError(f"line {lineno}: @point NAME")
        attach(lambda pc, n=bare[0], ln=lineno: ann.points.setdefault(n, []).append((pc, ln)))
    elif kw in ("stall", "wait"):
        lo, hi = _num(kv.get("min", "0")), _num(kv.get("max", "inf"))
        attach(lambda pc, lo=lo, hi=hi, ln=lineno: ann.stalls.__setitem__(pc, (lo, hi, ln)))
    elif kw == "loop":
        n = _num(kv["max"])
        attach(lambda pc, n=n, ln=lineno: ann.loops.__setitem__(pc, (n, ln)))
    elif kw == "assume":
        for k, v in kv.items():
            if k not in ("clkdiv", "fifo_stall", "line_stall", "core"):
                raise TimingError(f"line {lineno}: unknown assumption '{k}'")
            ann.assume[k] = (v, lineno)
    elif kw == "deadline":
        if not bare or "from" not in kv or "to" not in kv:
            raise TimingError(f"line {lineno}: @deadline NAME from=P to=P ...")
        d = Deadline(bare[0], _event(kv["from"], "done"), _event(kv["to"], "done"), line=lineno)
        d.why = " ".join(bare[1:])
        unknown = set(kv) - {"from", "to", "max", "min", "unit", "adjust", "avoid", "after", "stall", "reach"}
        if unknown:
            raise TimingError(f"line {lineno}: unknown deadline option(s) {sorted(unknown)}")
        if "max" in kv:
            d.max = _num(kv["max"])
        if "min" in kv:
            d.min = _num(kv["min"])
        d.unit = {"ticks": "tick", "clks": "clk", "clocks": "clk"}.get(kv.get("unit", "clk"), kv.get("unit", "clk"))
        if d.unit not in ("clk", "tick"):
            raise TimingError(f"line {lineno}: unit is clk or tick")
        if "adjust" in kv:
            d.adjust = _range(kv["adjust"].lstrip("+"))
        if "avoid" in kv:
            d.avoid = [a for a in kv["avoid"].split(",") if a]
        if "reach" in kv:
            d.reach = kv["reach"]
            if d.reach not in ("required", "conditional"):
                raise TimingError(f"line {lineno}: reach is required or conditional")
        if "stall" in kv:
            for item in kv["stall"].split(","):
                p, _, rng = item.rpartition(":")
                lo, hi = _range(rng)
                d.stalls.append((p, lo, hi))
        if "after" in kv:
            ev, _, win = kv["after"].rpartition(":")
            d.after = _event(ev, "enter")
            d.window = _range(win)
        if d.frm.kind == "cond" and d.after is None:
            raise TimingError(f"line {lineno}: from=P.cond needs after=ANCHOR:LO..HI")
        if d.to.kind == "cond":
            raise TimingError(f"line {lineno}: .cond is a start event")
        ann.deadlines.append(d)
    else:
        raise TimingError(f"line {lineno}: unknown annotation @{kw}")


# ------------------------------------------------------------------ results
@dataclass
class Step:
    pc: int
    lo: float
    hi: float
    note: str = ""


@dataclass
class Result:
    deadline: Deadline
    lo: float = INF                 # ticks
    hi: float = INF
    lo_clk: float = INF
    hi_clk: float = INF
    status: str = ""
    why_unbounded: str = ""
    worst: list = field(default_factory=list)     # [Step]
    best: list = field(default_factory=list)
    assumptions: list = field(default_factory=list)
    escapes: list = field(default_factory=list)
    nonreaching: list = field(default_factory=list)   # reachable paths that never get to `to`
    errors: list = field(default_factory=list)
    checker: object = None

    def total(self):
        """(lo, hi) in the deadline's unit, with the adjustment."""
        a, b = self.deadline.adjust
        if self.deadline.unit == "tick":
            return self.lo + a, self.hi + b
        return self.lo_clk + a, self.hi_clk + b

    def contains(self, ticks, clocks):
        return self.lo <= ticks <= self.hi and self.lo_clk <= clocks <= self.hi_clk

    def report(self, verbose=False):
        d, c = self.deadline, self.checker
        f = lambda v: "inf" if v == INF else f"{v:g}"
        frm = f"{d.frm.point}.{d.frm.kind}"
        if d.after:
            frm += f" ({f(d.window[0])}..{f(d.window[1])} ticks after {d.after.point}.{d.after.kind})"
        out = [f"deadline {d.name} (line {d.line}): {frm} -> {d.to.point}.{d.to.kind}"
               + (f"  -- {d.why}" if d.why else "")]
        if self.errors:
            out += [f"  ERROR: {e}" for e in self.errors]
        if self.lo == INF and not self.errors:
            out.append(f"  NO PATH from {frm} to {d.to.point}")
        if self.lo != INF:
            s = f"  bound: [{f(self.lo)}, {f(self.hi)}] ticks = [{f(self.lo_clk)}, {f(self.hi_clk)}] clk"
            if d.adjust != (0, 0):
                lo, hi = self.total()
                s += f"; with adjust +{f(d.adjust[0])}..{f(d.adjust[1])}: [{f(lo)}, {f(hi)}] {d.unit}"
            out.append(s)
        req = []
        if d.min is not None:
            req.append(f">= {f(d.min)}")
        if d.max is not None:
            req.append(f"<= {f(d.max)}")
        out.append(f"  required: {' and '.join(req) or 'nothing'} {d.unit}  ->  {self.status}")
        if self.nonreaching:
            if d.reach == "conditional":
                out.append(f"  CONDITIONAL: bounds only paths that reach {d.to.point}; these never do:")
            else:
                out.append(f"  some reachable paths never reach {d.to.point} (fails; use avoid= or reach=conditional "
                           "if that is intended):")
            out += [f"    {e}" for e in self.nonreaching]
        if self.hi == INF and self.why_unbounded:
            out.append(f"  UNBOUNDED because {self.why_unbounded}")
        if self.worst:
            out.append(f"  worst path ({f(self.hi)} ticks):")
            out += c.fmt_path(self.worst)
        if verbose and self.best:
            out.append(f"  best path ({f(self.lo)} ticks):")
            out += c.fmt_path(self.best)
        if self.assumptions:
            out.append("  assumes:")
            groups = {}
            for text, pc in self.assumptions:
                groups.setdefault(text, []).append(pc)
            for text, pcs in groups.items():
                if len(pcs) == 1:
                    out.append(f"    {text} at {c.where(pcs[0])}")
                else:
                    lines = [c._line_of(p) for p in sorted(pcs)]
                    at = ", ".join(str(ln) if ln else f"pc {p}" for ln, p in zip(lines, sorted(pcs)))
                    out.append(f"    {text} at lines {at}")
        if self.escapes and verbose:
            out.append("  paths not counted:")
            out += [f"    {e}" for e in self.escapes]
        return "\n".join(out)


# ------------------------------------------------------------------ the checker
class Checker:
    def __init__(self, prog, ann=None, *, clkdiv=None, core=None, fifo_stall=None, line_stall=None,
                 shift_out=None, shift_in=None, source=None, name=None):
        """prog: asm.Program. ann: Annotations (parse_annotations). Keyword
        arguments override the annotations' @assume values and the program's
        .clkdiv/.shift_* directives (as host.load_program's do)."""
        self.prog, self.ann = prog, ann or Annotations()
        self.name = name or prog.name
        self.words = prog.words
        self.n = len(prog.words)
        self.source = source
        self.src_text = {}
        for i, raw in prog.source_lines:
            self.src_text[i] = raw
        a = self.ann.assume
        div = clkdiv if clkdiv is not None else a["clkdiv"][0] if "clkdiv" in a else None
        if div is None:
            self.clkdiv = prog.config.get("clkdiv", (1, 0))
        elif isinstance(div, tuple):
            self.clkdiv = div
        else:
            self.clkdiv = isa.clkdiv_fields(float(div))
        self.core = int(core if core is not None else a["core"][0] if "core" in a else 0)
        fs = fifo_stall if fifo_stall is not None else a["fifo_stall"][0] if "fifo_stall" in a else "inf"
        ls = line_stall if line_stall is not None else a["line_stall"][0] if "line_stall" in a else "inf"
        self.fifo_stall = _num(str(fs))
        self.line_stall = _num(str(ls))
        cfg = prog.config
        so = shift_out if shift_out is not None else cfg.get("shift_out", (False, False, 32))
        si = shift_in if shift_in is not None else cfg.get("shift_in", (False, False, 32))
        self.autopull, self.pull_th = bool(so[1]), (so[2] & 31) or 32
        self.autopush, self.push_th = bool(si[1]), (si[2] & 31) or 32
        sc = min(prog.side_count, 4)
        self.dmask = (1 << (8 - sc - prog.side_opt)) - 1
        self.wrap_bottom, self.wrap_top = prog.wrap_bottom, prog.wrap_top
        self.points = {lab: frozenset([pc]) for lab, pc in prog.labels.items() if pc < self.n}
        for nm, refs in self.ann.points.items():
            self.points[nm] = frozenset(self._pc(ref) for ref, _ in refs)
        self.stalls = {self._pc(r): v for r, v in self.ann.stalls.items()}
        self.overlay = {}           # @stall of the deadline being checked
        self.loops = {self._pc(r): v for r, v in self.ann.loops.items()}
        if source is not None:          # the annotation line -> word map must match the assembler's
            raws = [raw.strip() for _, raw in _instr_lines(source, prog.name or None) if raw is not None]
            if raws != [raw for _, raw in prog.source_lines]:
                raise TimingError("cannot map source lines to instruction words (multi-program file? "
                                  "pass program=)")
        self.errors = []
        self.entry_state = self._dataflow()
        self.errors += self._check_calls()

    # -- helpers
    def _pc(self, ref):
        if isinstance(ref, tuple):
            lab, k = ref
            if lab not in self.prog.labels:
                raise TimingError(f"unknown label '{lab}'")
            ref = self.prog.labels[lab] + k
        if not 0 <= ref < self.n:
            raise TimingError(f"point outside the program ({ref})")
        return ref

    def point(self, name):
        """The set of instructions a point (or label, or `label+k`) names."""
        if "+" in name:
            base, _, k = name.partition("+")
            return frozenset(self._pc(p + int(k)) for p in self.point(base))
        if name not in self.points:
            raise TimingError(f"unknown point or label '{name}'")
        return self.points[name]

    def point1(self, name):
        p = self.point(name)
        if len(p) != 1:
            raise TimingError(f"point '{name}' names {len(p)} instructions; one is needed here")
        return next(iter(p))

    def set_overlay(self, d):
        """Make deadline d's own stall= assumptions current (None: clear)."""
        self.overlay = {}
        for p, lo, hi in (d.stalls if d else []):
            for pc in self.point(p):
                self.overlay[pc] = (lo, hi, d.line)

    def where(self, pc):
        """'line N `instr`' for messages."""
        txt = self.src_text.get(pc, isa.disasm(self.words[pc]))
        code = re.split(r";|//", txt, maxsplit=1)[0].strip()
        ln = self._line_of(pc)
        return f"{'line ' + str(ln) if ln else 'pc ' + str(pc)} `{code}`"

    def _line_of(self, pc):
        if self.source is None:
            return None
        if not hasattr(self, "_lines"):
            self._lines = [ln for ln, raw in _instr_lines(self.source, self.prog.name or None)
                           if raw is not None]
        return self._lines[pc] if pc < len(self._lines) else None

    def fmt_step(self, s):
        f = lambda v: "inf" if v == INF else f"{v:g}"
        cost = f(s.lo) if s.lo == s.hi else f"{f(s.lo)}..{f(s.hi)}"
        return f"{self.where(s.pc):48s} +{cost}{'  ' + s.note if s.note else ''}"

    def fmt_path(self, steps):
        """Path lines, with repeated loop bodies folded ("... x7")."""
        keys = [(s.pc, s.lo, s.hi) for s in steps]
        out, i = [], 0
        while i < len(steps):
            best = (1, 1)
            for L in range(1, 9):
                k = 1
                while keys[i + k * L:i + (k + 1) * L] == keys[i:i + L]:
                    k += 1
                if k > 1 and k * L > best[0] * best[1]:
                    best = (L, k)
            L, k = best
            for j, s in enumerate(steps[i:i + L]):
                end = "  (end)" if i + j == len(steps) - 1 else ""
                out.append("    " + self.fmt_step(s) + end)
            if k > 1:
                out[-1] += f"   (last {L} lines x{k})" if L > 1 else f"   (x{k})"
            i += L * k
        return out

    def seq(self, pc):
        return self.wrap_bottom if pc == self.wrap_top else pc + 1

    def delay(self, pc):
        return isa.fields(self.words[pc])[1] & self.dmask

    def ticks_to_clk(self, n, up):
        if n == INF:
            return INF
        i, f = self.clkdiv
        nf = n * f
        return n * (i or 65536) + (-(-nf // 256) if up else nf // 256)

    def _stall(self, pc, lo, hi, notes, why):
        """Stall ticks [lo, hi] intersected with this instruction's @stall."""
        a = self.overlay.get(pc) or self.stalls.get(pc)
        if a:
            alo, ahi, line = a
            if ahi < hi or alo > lo:
                src = "stall= of the deadline" if pc in self.overlay else "@stall"
                rng = f"max={ahi:g}" if alo == 0 else f"min={alo:g} max={ahi:g}"
                notes.append((f"{src} {rng} (line {line})", pc))
            lo, hi = max(lo, alo), min(hi, ahi)
        if hi == INF:
            return lo, hi, f"{self.where(pc)}: {why}"
        return lo, hi, None

    def assumed_stall_max(self, pc):
        """The stall bound an assumption (not the ISA) gives pc, or None."""
        st = self.entry_state.get(pc)
        if st is None:
            return None
        outs = self.step(pc, st)
        if any(o.notes for o in outs):
            return max(o.whi for o in outs)
        return None

    # -- the transfer function: outcomes of one execution of pc in state st
    Out = namedtuple("Out", "npc st wlo whi why notes sink")

    def step(self, pc, st):
        op, sd, x = isa.fields(self.words[pc])
        seq = self.seq(pc)
        outs = []

        def out(npc, nst, wlo=0, whi=0, why=None, notes=(), sink=None):
            if sink is None and not 0 <= npc < self.n:
                sink = f"runs off the program at pc {npc} after {self.where(pc)}"
            outs.append(self.Out(npc, nst, wlo, whi, why, list(notes), sink))

        if op in (OP_JMP, OP_CALL):
            cond, sel, addr = bits(x, 18, 15), bits(x, 14, 10), bits(x, 9, 0)
            rs = sel & 3
            rv = st.r[rs]
            if cond == isa.C_ALWAYS:
                poss = {True}
            elif cond == isa.C_ZERO:
                poss = _truth(rv, lambda v: v == 0)
            elif cond in (isa.C_NZERO, isa.C_DEC):
                poss = _truth(rv, lambda v: v != 0)
            elif cond in (isa.C_Z, isa.C_NZ):
                poss = {(z == 1) == (cond == isa.C_Z) for z in st.z}
            elif cond in (isa.C_C, isa.C_NC):
                poss = {(c == 1) == (cond == isa.C_C) for c in st.c}
            elif cond == isa.C_T:
                poss = {t == 1 for t in st.t}
            elif cond == isa.C_NOSRE:
                poss = _truth(st.osr_cnt, lambda v: v < self.pull_th)
            elif cond == isa.C_TXNE:
                poss = {True} if st.txf_ne else {True, False}
            elif cond == isa.C_RXF:
                poss = {False} if st.rxf_nf else {True, False}
            else:                                # pins and flags: the outside world
                poss = {True, False}
            for taken in (True, False):
                if taken not in poss:
                    continue
                nst = st
                if cond in (isa.C_ZERO, isa.C_NZERO, isa.C_DEC) and _ints(rv):
                    keep = frozenset(v for v in rv if (v != 0) == (taken != (cond == isa.C_ZERO)))
                    nst = _setr(nst, rs, keep)
                if cond == isa.C_DEC:
                    nst = _setr(nst, rs, _map(nst.r[rs], lambda v: (v - 1) & M16) if nst.r[rs] is not None
                                else (None if taken else _const(M16)))
                elif cond in (isa.C_Z, isa.C_NZ):
                    nst = nst._replace(z=_const(int(taken == (cond == isa.C_Z))))
                elif cond in (isa.C_C, isa.C_NC):
                    nst = nst._replace(c=_const(int(taken == (cond == isa.C_C))))
                elif cond == isa.C_T:
                    nst = nst._replace(t=_const(int(taken)))
                elif cond == isa.C_TXNE and taken:
                    nst = nst._replace(txf_ne=True)
                elif cond == isa.C_RXF and not taken:
                    nst = nst._replace(rxf_nf=True)
                if taken and op == OP_CALL:
                    nst = nst._replace(lr=_const(("ret", seq)))
                out(addr if taken else seq, nst)
            return outs

        if op == OP_WAIT:
            kind, to, treg = bits(x, 18, 16), bits(x, 10, 10), bits(x, 9, 8)
            level = kind not in (isa.W_RISE, isa.W_FALL, isa.W_EDGE)
            wmin = 0 if level else 1
            if not to:
                notes = []
                lo, hi, why = self._stall(pc, wmin, INF, notes, "untimed WAIT and no @stall bound")
                if lo > hi:
                    raise TimingError(f"@stall at {self.where(pc)} contradicts the ISA")
                out(seq, st, lo, hi, why, notes)
                return outs
            nv = st.r[treg]
            nlo, nhi = (min(nv), max(nv)) if _ints(nv) else (0, M16)
            notes = []
            lo, hi, _ = self._stall(pc, wmin, nhi, notes, "")
            if lo <= hi and (level or nhi >= 1):     # met before the budget ran out: T = 0
                out(seq, _setr(st, treg, None)._replace(t=_const(0)), lo, hi, None, notes)
            notes = []
            lo, hi, _ = self._stall(pc, nlo, nhi, notes, "")
            if lo <= hi:                              # timed out after nlo..nhi ticks: T = 1
                out(seq, _setr(st, treg, _const(0))._replace(t=_const(1)), lo, hi, None, notes)
            if not outs:
                raise TimingError(f"@stall at {self.where(pc)} contradicts the ISA")
            return outs

        if op == OP_IN:
            src, n = bits(x, 18, 15), bits(x, 4, 0) or 32
            line = src == isa.LINE and self.core == 0
            cnt = st.isr_cnt
            notes, whys = [], []
            lo = hi = 0
            if line:
                l_lo, l_hi, why = self._line(pc, notes, "IN LINE waits for received bits")
                hi += l_hi
                whys.append(why)
                cases = [(0, _map(cnt, lambda v: min(32, v + n)))]
                t1 = None if not _ints(cnt) else frozenset(min(32, v + k) for v in cnt for k in range(n))
                cases.append((1, t1 if t1 is not None and len(t1) <= KSET else None))
            else:
                cases = [(None, _map(cnt, lambda v: min(32, v + n)))]
            for t, ncnt in cases:
                nst = st if t is None else st._replace(t=_const(t))
                fhi, fwhy, fnotes = 0, None, []
                if self.autopush:
                    may = _truth(ncnt, lambda v: v >= self.push_th)
                    if True in may:
                        if not st.rxf_nf:
                            fhi, fwhy = self._fifo(pc, fnotes, "autopush may find RXF full")
                        ncnt = _const(0) if may == {True} else _join(ncnt, _const(0)) if ncnt is not None else None
                        nst = nst._replace(rxf_nf=False)
                nst = nst._replace(isr_cnt=ncnt)
                ws = [w for w in whys + [fwhy] if w]
                nn = notes + fnotes
                lo2, hi2, why2 = self._stall(pc, lo, hi + fhi, nn, "; ".join(ws))
                out(seq, nst, lo2, hi2, why2 if hi2 == INF else None, nn)
            return outs

        if op == OP_OUT:
            dst, n = bits(x, 18, 15), bits(x, 4, 0) or 32
            line = dst == isa.LINE and self.core == 0
            notes, whys = [], []
            hi = 0
            if line:
                _, l_hi, why = self._line(pc, notes, "OUT LINE may find the TX queue full")
                hi += l_hi
                whys.append(why)
            cnt = st.osr_cnt
            nst = st
            if self.autopull:
                may = _truth(cnt, lambda v: v >= self.pull_th)
                if True in may:
                    if not st.txf_ne:
                        fhi, fwhy = self._fifo(pc, notes, "autopull may find TXF empty")
                        hi += fhi
                        whys.append(fwhy)
                    pulled = _const(min(32, n))
                    if may == {True}:
                        cnt = pulled
                    else:
                        cnt = _join(_map(cnt, lambda v: min(32, v + n)), pulled)
                    nst = nst._replace(txf_ne=False, osr_cnt=cnt)
                else:
                    nst = nst._replace(osr_cnt=_map(cnt, lambda v: min(32, v + n)))
            else:
                nst = nst._replace(osr_cnt=_map(cnt, lambda v: min(32, v + n)))
            if dst < 4:
                nst = _setr(nst, dst, None)
            elif dst == isa.ISR:
                nst = nst._replace(isr_cnt=_const(n))
            lo2, hi2, why2 = self._stall(pc, 0, hi, notes, "; ".join(w for w in whys if w))
            if dst == isa.PC:
                out(None, nst, lo2, hi2, sink=f"computed jump (out pc) at {self.where(pc)} is not supported")
            else:
                out(seq, nst, lo2, hi2, why2, notes)
            return outs

        if op == OP_PUSH:
            iff, blk = bits(x, 18, 18), bits(x, 17, 17)
            notes = []
            may = _truth(st.isr_cnt, lambda v: v >= self.push_th) if iff else {True}
            hi, why = 0, None
            nst = st
            if True in may:
                if blk and not st.rxf_nf:
                    hi, why = self._fifo(pc, notes, "PUSH may find RXF full")
                nst = nst._replace(rxf_nf=False,
                                   isr_cnt=_const(0) if may == {True} else _join(st.isr_cnt, _const(0)))
            lo2, hi2, why2 = self._stall(pc, 0, hi, notes, why or "")
            out(seq, nst, lo2, hi2, why2, notes)
            return outs

        if op == OP_PULL:
            ife, blk = bits(x, 18, 18), bits(x, 17, 17)
            notes = []
            may = _truth(st.osr_cnt, lambda v: v >= self.pull_th) if ife else {True}
            hi, why = 0, None
            nst = st
            if True in may:
                if blk and not st.txf_ne:
                    hi, why = self._fifo(pc, notes, "PULL may find TXF empty")
                nst = nst._replace(txf_ne=False,
                                   osr_cnt=_const(0) if may == {True} else _join(st.osr_cnt, _const(0)))
            lo2, hi2, why2 = self._stall(pc, 0, hi, notes, why or "")
            out(seq, nst, lo2, hi2, why2, notes)
            return outs

        if op == OP_MOV:
            dst, mop, src = bits(x, 18, 15), bits(x, 14, 13), bits(x, 12, 9)
            if src < 4:
                v = st.r[src]
            elif src == isa.LR:
                v = st.lr
            elif src in (isa.NULL, isa.PINDIRS, 15) or (src == isa.LINE):
                v = _const(0)
            else:
                v = None
            if mop == isa.M_INV:
                v = _map(v, lambda a: ~a & 0xFFFFFFFF)
            elif mop == isa.M_REV:
                v = _map(v, lambda a: int(f"{a:032b}"[::-1], 2))
            nst = st
            if dst < 4:
                nst = _setr(st, dst, _map(v, lambda a: a & M16) if _ints(v) else v)
            elif dst == isa.ISR:
                nst = st._replace(isr_cnt=_const(0))
            elif dst == isa.OSR:
                nst = st._replace(osr_cnt=_const(0))
            elif dst == isa.LR:
                nst = st._replace(lr=v)
            elif dst == isa.PC:
                if v is not None and v and all(isinstance(a, tuple) for a in v):
                    for a in sorted(v):
                        out(a[1], st)
                else:
                    what = "RET with an unknown link register" if src == isa.LR else "computed jump (mov pc)"
                    out(None, st, sink=f"{what} at {self.where(pc)} is not supported")
                return outs
            out(seq, nst)
            return outs

        if op == OP_LDI:
            out(seq, _setr(st, bits(x, 17, 16), _const(bits(x, 15, 0))))
            return outs

        if op == OP_ALU:
            aop, rd, imm = bits(x, 18, 16), bits(x, 15, 14), bits(x, 13, 13)
            a = st.r[rd]
            s = _const(bits(x, 12, 0)) if imm else st.r[bits(x, 1, 0)]
            if _ints(a) and _ints(s):
                res = set()
                for av in a:
                    for sv in s:
                        res.add(_alu(aop, av, sv))
                vals = frozenset(r for r, _, _ in res)
                nst = st._replace(z=frozenset(z for _, z, _ in res), c=frozenset(c for _, _, c in res))
                if aop != isa.A_CMP:
                    nst = _setr(nst, rd, vals if len(vals) <= KSET else None)
            else:
                logic = aop in (isa.A_AND, isa.A_OR, isa.A_XOR, isa.A_SHL, isa.A_SHR)
                nst = st._replace(z=BOTH, c=_const(0) if logic else BOTH)
                if aop != isa.A_CMP:
                    nst = _setr(nst, rd, None)
            out(seq, nst)
            return outs

        if op == OP_HALT:
            out(None, st, sink=f"HALT at {self.where(pc)}")
            return outs

        # SET, PIN, FLAG and the reserved opcodes complete in one tick
        out(seq, st)
        return outs

    def _fifo(self, pc, notes, why):
        if self.fifo_stall < INF:
            notes.append((f"fifo_stall <= {self.fifo_stall:g} (the host keeps up)", pc))
            return self.fifo_stall, None
        return INF, why + " and no fifo_stall/@stall bound"

    def _line(self, pc, notes, why):
        if self.line_stall < INF:
            notes.append((f"line_stall <= {self.line_stall:g}", pc))
            return 0, self.line_stall, None
        return 0, INF, why + " and no line_stall/@stall bound"

    # -- whole-program dataflow (join per pc) from the entry
    def _dataflow(self):
        states = {0: INITIAL}
        work = [0]
        self.reach_sinks = []
        while work:
            pc = work.pop()
            for o in self.step(pc, states[pc]):
                if o.sink:
                    if not o.sink.startswith("HALT"):
                        self.reach_sinks.append(o.sink)
                    continue
                old = states.get(o.npc)
                new = o.st if old is None else _join_state(old, o.st)
                if new != old:
                    states[o.npc] = new
                    work.append(o.npc)
        return states

    # -- CALL/RET: one link register (docs/isa.md §5.2)
    def _check_calls(self):
        """From each CALL target, follow the subroutine (CALLs inside it are
        taken as returning) and report a RET that runs after a CALL inside it
        overwrote LR, unless LR was saved in a register and restored. A
        subroutine may leave by a jump instead of a RET (the return is then
        abandoned, and later CALLs are fresh)."""
        errs = []
        targets = sorted({bits(isa.fields(w)[2], 9, 0) for pc, w in enumerate(self.words)
                          if isa.fields(w)[0] == OP_CALL and pc in self.entry_state})
        for tgt in targets:
            seen = set()
            stack = [(tgt, frozenset(), None)]
            while stack:
                key = stack.pop()
                if key in seen:
                    continue
                seen.add(key)
                pc, saved, clob = key       # clob: the CALL that overwrote LR, or None
                if not 0 <= pc < self.n:
                    continue
                op, _, x = isa.fields(self.words[pc])
                nxt = [self.seq(pc)]
                if op == OP_CALL:
                    if clob is None:
                        clob = pc
                elif op == OP_JMP:
                    cond = bits(x, 18, 15)
                    nxt = [bits(x, 9, 0)] + ([] if cond == isa.C_ALWAYS else nxt)
                elif op == OP_MOV:
                    dst, src = bits(x, 18, 15), bits(x, 12, 9)
                    if dst == isa.PC:
                        if src == isa.LR and clob is not None:
                            errs.append(f"RET at {self.where(pc)} returns through LR overwritten by the "
                                        f"nested CALL at {self.where(clob)} (subroutine at {self.where(tgt)}): "
                                        f"one link register, save it (`mov r3, lr` ... `mov lr, r3`)")
                        continue
                    if dst < 4:
                        saved = saved | {dst} if src == isa.LR and clob is None else saved - {dst}
                    if dst == isa.LR:
                        clob = None if (src < 4 and src in saved) else pc
                elif op in (OP_LDI, OP_ALU):
                    rd = bits(x, 17, 16) if op == OP_LDI else bits(x, 15, 14)
                    if not (op == OP_ALU and bits(x, 18, 16) == isa.A_CMP):
                        saved = saved - {rd}
                elif op == OP_OUT and bits(x, 18, 15) < 4:
                    saved = saved - {bits(x, 18, 15)}
                elif op == OP_HALT:
                    continue
                for npc in nxt:
                    stack.append((npc, saved, clob))
        return sorted(set(errs))

    # -- one deadline
    def check(self):
        return [self.check_deadline(d) for d in self.ann.deadlines]

    def check_deadline(self, d):
        r = Result(d, checker=self)
        r.errors = list(self.errors)
        try:
            self.set_overlay(d)
            self._explore(d, r)
        except TimingError as e:
            r.errors.append(str(e))
        finally:
            self.set_overlay(None)
        r.errors = list(dict.fromkeys(r.errors))
        lo, hi = r.total()
        if r.errors:
            r.status = "ERROR"
        elif r.lo == INF:
            r.status = "NO PATH"
        elif (d.max is not None and hi > d.max) or (d.min is not None and lo < d.min):
            r.status = "FAIL"
        elif r.nonreaching and d.reach == "required":
            r.status = "FAIL (MAY NEVER REACH)"
        else:
            r.status = "PASS (conditional)" if r.nonreaching else "PASS"
        if r.status == "FAIL" and hi == INF:
            r.status = "FAIL (UNBOUNDED)"
        return r

    def _explore(self, d, r):
        A, B = self.point(d.frm.point), self.point(d.to.point)
        avoid = frozenset().union(*[self.point(p) for p in d.avoid])
        loops = sorted(self.loops)
        cond = d.frm.kind == "cond"
        X = self.point1(d.frm.point) if cond else None
        if cond:
            op, _, x = isa.fields(self.words[X])
            if op != OP_WAIT or bits(x, 18, 16) not in (isa.W_PIN1, isa.W_PIN0, isa.W_FLAG1, isa.W_FLAG0) \
                    or bits(x, 10, 10):
                raise TimingError(f"{d.frm.point}.cond needs an untimed level WAIT, not {self.where(X)}")
            S, skind = self.point(d.after.point), d.after.kind
        else:
            S, skind = A, d.frm.kind
        S = sorted(S)
        if not any(s in self.entry_state for s in S):
            raise TimingError(f"{d.frm.point}: unreachable from the program entry")
        notes_all = set()
        escapes = set()
        edges = {}          # node -> [(node2, kind, a, b, pc, note)]: kind 'add' (+a..b) or 'reset'
        START, END = ("START",), ("END",)
        edges[START] = []
        nodes = {}
        work = []

        def node(npc, st, cnt, phase, frm_node, kind, a, b, pc_from, note):
            """Arrive at npc; add the edge from frm_node."""
            if not cond or phase == 1:
                if npc in B:
                    key = ("B", npc, st, cnt, phase)
                    if key not in edges:
                        edges[key] = []
                        if d.to.kind == "done":
                            outs = self.step(npc, st)
                            halts = [o.sink for o in outs if o.sink and o.sink.startswith("HALT")]
                            if halts:
                                raise TimingError(f"{d.to.point}.done: {halts[0]} never completes "
                                                  f"(use {d.to.point}.enter)")
                            wlo = min(o.wlo for o in outs)
                            whi = max(o.whi for o in outs)
                            why = next((o.why for o in outs if o.whi == INF), None)
                            for o in outs:
                                notes_all.update(o.notes)
                            edges[key].append((END, "add", wlo, whi, npc, why))
                        else:
                            edges[key].append((END, "add", 0, 0, npc, None))
                    edges[frm_node].append((key, kind, a, b, pc_from, note))
                    return
            elif npc in B:
                escapes.add(f"reaches {self.where(npc)} before the condition WAIT")
                return
            if npc in avoid:
                escapes.add(f"avoid: {self.where(npc)}")
                return
            if npc in self.loops:
                i = loops.index(npc)
                cnt = cnt[:i] + (cnt[i] + 1,) + cnt[i + 1:]
                if cnt[i] > self.loops[npc][0]:
                    escapes.add(f"@loop max={self.loops[npc][0]:g} at {self.where(npc)}")
                    notes_all.add((f"@loop max={self.loops[npc][0]:g} (line {self.loops[npc][1]})", npc))
                    return
            if cond and phase == 0 and npc == X:
                key = ("X", npc, st, cnt, 0)
            else:
                key = (npc, st, cnt, phase)
            if key not in nodes:
                nodes[key] = True
                edges[key] = []
                work.append(key)
                if len(nodes) > MAX_KEYS:
                    raise TimingError("analysis limit: too many states (a loop whose registers keep changing?); "
                                      "add `; @loop max=N`")
            edges[frm_node].append((key, kind, a, b, pc_from, note))

        cnt0 = (0,) * len(loops)
        if cond and X in S:
            raise TimingError("after= must name an earlier point than the WAIT")
        for s0 in S:
            if s0 not in self.entry_state:
                continue
            dS = self.delay(s0)
            if not cond and skind == "enter" and d.to.kind == "done" and s0 in B:
                # the interval ends when this same instruction completes: its stall
                outs = self.step(s0, self.entry_state[s0])
                halts = [o.sink for o in outs if o.sink and o.sink.startswith("HALT")]
                if halts:
                    raise TimingError(f"{d.to.point}.done: {halts[0]} never completes (use {d.to.point}.enter)")
                for o in outs:
                    notes_all.update(o.notes)
                    edges[START].append((END, "add", o.wlo, o.whi, s0, o.why))
                continue
            for o in self.step(s0, self.entry_state[s0]):
                notes_all.update(o.notes)
                if o.sink:
                    escapes.add(o.sink)
                    if "not supported" in o.sink or o.sink.startswith("runs off"):
                        r.errors.append(o.sink)
                    continue
                if skind == "done":
                    a, b, note = 1 + dS, 1 + dS, None
                else:
                    a, b, note = o.wlo + 1 + dS, o.whi + 1 + dS, o.why
                node(o.npc, o.st, cnt0, 0, START, "add", a, b, s0, note)
        while work:
            key = work.pop()
            if key[0] == "X":
                _, pc, st, cnt, _ = key
                for o in self.step(pc, st):         # its completion comes from after=, not a stall
                    dX = self.delay(pc)
                    node(o.npc, o.st, cnt, 1, key, "reset", 1 + dX, 1 + dX, pc, None)
                continue
            pc, st, cnt, phase = key
            for o in self.step(pc, st):
                notes_all.update(o.notes)
                if o.sink:
                    escapes.add(o.sink)
                    if "not supported" in o.sink or o.sink.startswith("runs off"):
                        r.errors.append(o.sink)
                    continue
                dp = self.delay(pc)
                node(o.npc, o.st, cnt, phase, key, "add", o.wlo + 1 + dp, o.whi + 1 + dp, pc, o.why)

        # nodes that reach END
        rev = {}
        for u, es in edges.items():
            for v, *_ in es:
                rev.setdefault(v, []).append(u)
        live, stack = {END}, [END]
        while stack:
            v = stack.pop()
            for u in rev.get(v, []):
                if u not in live:
                    live.add(u)
                    stack.append(u)
        for u, es in edges.items():               # where paths leave for good
            if u in live:
                for v, *_ in es:
                    if v not in live and isinstance(v[0], int):
                        escapes.add(f"never reaches {d.to.point} from {self.where(v[0])}")
        r.escapes = sorted(escapes)
        # Paths that stop for good without reaching `to`: an endless loop, or a HALT.
        # Explicit exclusions (avoid=, @loop bounds, the cond-mode early arrival) are not counted.
        r.nonreaching = sorted(e for e in escapes
                               if e.startswith("never reaches") or e.startswith("HALT"))
        if START not in live:
            return
        lo_win, hi_win = d.window if cond else (0, 0)

        def apply(kind, a, b, lo, hi):
            if kind == "add":
                return lo + a, hi + b
            return max(lo - hi_win, 0) + a, max(hi - lo_win, 0) + b   # completion - cond, then the delay

        # cycle check among live nodes (iterative DFS)
        cyc = None
        color = {}
        order = []
        for root in [START]:
            stack = [(root, iter([e for e in edges[root] if e[0] in live]))]
            color[root] = 1
            while stack:
                u, it = stack[-1]
                nxt = next(it, None)
                if nxt is None:
                    color[u] = 2
                    order.append(u)
                    stack.pop()
                    continue
                v = nxt[0]
                c = color.get(v, 0)
                if c == 0:
                    color[v] = 1
                    stack.append((v, iter([e for e in edges.get(v, []) if e[0] in live])))
                elif c == 1 and cyc is None:
                    path = [s[0] for s in stack]
                    cyc = path[path.index(v):]
        # lo: label-correcting (costs are positive inside a phase)
        lo = {START: 0}
        pred_lo = {}
        wl = [START]
        while wl:
            u = wl.pop()
            for v, kind, a, b, pcf, note in edges.get(u, []):
                if v not in live:
                    continue
                nlo, _ = apply(kind, a, b, lo[u], lo[u])
                if nlo < lo.get(v, INF):
                    lo[v] = nlo
                    pred_lo[v] = (u, a, b, pcf, kind)
                    wl.append(v)
        r.lo = lo.get(END, INF)
        r.assumptions = sorted(notes_all)
        if cyc is not None:
            pcs = sorted({k[0] if isinstance(k[0], int) else k[1] for k in cyc if k not in (START, END)})
            r.hi = INF
            r.why_unbounded = ("a loop through " + ", ".join(self.where(p) for p in pcs)
                               + " has no bound (add `; @loop max=N` on one of its instructions)")
        else:
            hi = {START: 0}
            why = {START: None}
            pred = {}
            for u in reversed(order):
                if u not in hi:
                    continue
                for v, kind, a, b, pcf, note in edges.get(u, []):
                    if v not in live:
                        continue
                    _, nhi = apply(kind, a, b, hi[u], hi[u])
                    if v not in hi or nhi > hi[v]:
                        hi[v] = nhi
                        pred[v] = (u, a, b, pcf, kind)
                        why[v] = why[u] or (note if b == INF else None)
            r.hi = hi.get(END, INF)
            r.why_unbounded = why.get(END) or ""
            r.worst = self._path(pred, END, hi, lo_win)
        r.best = self._path(pred_lo, END, lo, hi_win)
        r.lo_clk = self.ticks_to_clk(r.lo, False)
        r.hi_clk = self.ticks_to_clk(r.hi, True)

    def _path(self, pred, end, val, win):
        """The path into `end` along `pred`, each step with what it added to
        `val` (the worst or best total); win: the cond time used on it."""
        steps = []
        v = end
        while v in pred:
            u, a, b, pcf, kind = pred[v]
            inc = val[v] - val[u]
            note = ""
            if kind == "reset":
                inc = val[v]
                note = (f"<- the interval starts at the condition ({win:g} ticks after the anchor; "
                        f"the WAIT was reached at {val[u]:g}, the lines above count from the anchor)")
            steps.append(Step(pcf, inc, inc, note))
            v = u
        steps.reverse()
        return steps


def _alu(aop, a, s):
    c = 0
    if aop == isa.A_ADD:
        res = a + s
        c = res >> 16
    elif aop in (isa.A_SUB, isa.A_CMP):
        res = a - s
        c = int(a < s)
    elif aop == isa.A_AND:
        res = a & s
    elif aop == isa.A_OR:
        res = a | s
    elif aop == isa.A_XOR:
        res = a ^ s
    elif aop == isa.A_SHL:
        res = a << (s & 15)
    else:
        res = a >> (s & 15)
    res &= M16
    return res, int(res == 0), c


# ------------------------------------------------------------------ measuring on the model
class Tracer:
    """Records one core's instruction attempts on a sim.Chip, for comparing
    measured intervals with the checker's bounds. Hooks the instance only
    (core.step, core._divider, chip.cover); the model itself is unchanged."""

    def __init__(self, chip, core, base):
        self.chip, self.core, self.base = chip, chip.cores[core], base
        self.attempts = []          # (pc_rel, tick, cycle, done)
        self.cycles = []            # (cycle, tick or None, pins_in, flags) per enabled cycle
        self.ticks = 0
        self._active = False
        c = self.core
        orig_step, orig_div, orig_cover = c.step, c._divider, chip.cover

        def step(chip_, pins_in, flags):
            self._active = True
            self._pins = (pins_in, flags)
            self._tick = None
            try:
                return orig_step(chip_, pins_in, flags)
            finally:
                self._active = False
                self.cycles.append((chip.cycle, self._tick, pins_in, flags))

        def div():
            t = orig_div()
            if t:
                self.ticks += 1
                self._tick = self.ticks
            return t

        def cover(ev):
            if self._active:
                if ev.startswith("attempt:"):
                    self._cur = (c.pc - self.base, self.ticks, chip.cycle)
                elif ev.startswith("done:") or ev.startswith("stall:"):
                    self.attempts.append(self._cur + (ev.startswith("done:"),))
            return orig_cover(ev)

        c.step, c._divider, chip.cover = step, div, cover

    def instances(self):
        """[(pc, enter_tick, enter_cycle, done_tick, done_cycle, attempts)];
        done_* is None for an instruction that never completed."""
        out, cur = [], None
        for pc, tick, cyc, done in self.attempts:
            if cur is not None and cur[0] != pc:
                out.append((cur[0], cur[1], cur[2], None, None, cur[3]))
                cur = None
            if cur is None:
                cur = [pc, tick, cyc, 0]
            cur[3] += 1
            if done:
                out.append((pc, cur[1], cur[2], tick, cyc, cur[3]))
                cur = None
        return out


@dataclass
class Measurement:
    ticks: int
    clocks: int
    start_cycle: int
    violations: list
    cond_offset: int = None         # .cond deadlines: ticks from the `after` event to the condition


def _cond_holds(word, pins, flags):
    x = isa.fields(word)[2]
    kind, idx = bits(x, 18, 16), bits(x, 15, 11)
    qen, qpol, qpin = bits(x, 6, 6), bits(x, 5, 5), bits(x, 4, 0)
    q = (not qen) or ((pins >> qpin) & 1) == qpol
    base = {isa.W_PIN1: (pins >> idx) & 1 == 1, isa.W_PIN0: (pins >> idx) & 1 == 0,
            isa.W_FLAG1: (flags >> (idx & 7)) & 1 == 1, isa.W_FLAG0: (flags >> (idx & 7)) & 1 == 0}[kind]
    return base and q


def measure(checker, deadline, tracer):
    """Measured intervals of `deadline` in a Tracer's record, with the
    assumptions each one broke (empty list: the checker's bound must hold)."""
    d = deadline
    A, B = checker.point(d.frm.point), checker.point(d.to.point)
    avoid = frozenset().union(*[checker.point(p) for p in d.avoid])
    cond = d.frm.kind == "cond"
    S, skind = (checker.point(d.after.point), d.after.kind) if cond else (A, d.frm.kind)
    checker.set_overlay(d)
    try:
        smax = {pc: checker.assumed_stall_max(pc) for pc in range(checker.n)}
    finally:
        checker.set_overlay(None)
    tick_cycles = [(cyc, t, pins, fl) for cyc, t, pins, fl in tracer.cycles if t is not None]
    tick_index = {cyc: i for i, (cyc, *_rest) in enumerate(tick_cycles)}
    events = []
    for inst in tracer.instances():
        pc, et, ec, dt, dc, natt = inst
        events.append(("enter", pc, et, ec, inst))
        if dt is not None:
            events.append(("done", pc, dt, dc, inst))
    out = []
    open_ = []      # dicts: start tick/cycle, phase, counts, viol
    for kind, pc, tick, cyc, inst in events:
        still = []
        for iv in open_:
            if kind == "done" and smax.get(pc) is not None and inst[5] - 1 > smax[pc] \
                    and (iv["phase"] == 1 or pc not in A):
                iv["viol"].append(f"stall {inst[5] - 1} > assumed {smax[pc]:g} at pc {pc}")
            if iv["phase"] == 1 and kind == d.to.kind and pc in B:
                out.append(Measurement(tick - iv["t"], cyc - iv["c"], iv["c"], iv["viol"], iv.get("off")))
                continue
            if kind == "enter":
                if iv["phase"] == 0 and pc in B:
                    continue                      # the analysis drops these paths too
                if pc in avoid:
                    continue
                if pc in checker.loops:
                    iv["cnt"][pc] = iv["cnt"].get(pc, 0) + 1
                    if iv["cnt"][pc] > checker.loops[pc][0]:
                        iv["viol"].append(f"@loop at pc {pc}")
                if iv["phase"] == 0 and pc in A:
                    # the condition: first tick at/after the anchor where it holds
                    w = checker.words[pc]
                    c_ = None
                    for i in range(tick_index.get(iv["c0"], 0), len(tick_cycles)):
                        cc, tt, pins, fl = tick_cycles[i]
                        if cc >= iv["c0"] and _cond_holds(w, pins, fl):
                            c_ = (tt, cc)
                            break
                    if c_ is None:
                        continue
                    lo_w, hi_w = d.window
                    iv["off"] = c_[0] - iv["t0"]
                    if not lo_w <= c_[0] - iv["t0"] <= hi_w:
                        iv["viol"].append(f"cond at {c_[0] - iv['t0']} ticks, assumed {lo_w}..{hi_w}")
                    if inst[3] is not None and inst[3] != max(inst[1], c_[0]):
                        iv["viol"].append("the condition did not hold until the WAIT saw it")
                    iv["phase"], iv["t"], iv["c"] = 1, c_[0], c_[1]
            still.append(iv)
        open_ = still
        if kind == skind and pc in S:
            open_.append(dict(phase=0 if cond else 1, t=tick, c=cyc, t0=tick, c0=cyc, cnt={}, viol=[]))
    return out


# ------------------------------------------------------------------ CLI
def check_source(text, defines=None, program=None, sidecar=None, **kw):
    """(Checker, [Result]) for a .pasm source text and optional sidecar text."""
    progs = asm.assemble_all(text, defines)
    prog = progs[0] if program is None else next(p for p in progs if p.name == program)
    ann = parse_annotations(text, prog.name if program else None)
    if sidecar:
        ann = parse_annotations(sidecar, ann=ann)
    chk = Checker(prog, ann, source=text, **kw)
    return chk, chk.check()


def main(argv=None):
    import argparse
    import importlib
    ap = argparse.ArgumentParser(prog="python -m pe.timing", description=__doc__.split("\n")[0])
    ap.add_argument("file")
    ap.add_argument("-D", action="append", default=[], help="NAME=VALUE assembler define")
    ap.add_argument("--defs", help="MODULE:FUNC returning a defines dict (e.g. pe.usbdev:defines)")
    ap.add_argument("--timing", help="sidecar annotation file")
    ap.add_argument("--program")
    ap.add_argument("--clkdiv", type=float)
    ap.add_argument("--fifo-stall")
    ap.add_argument("--line-stall")
    ap.add_argument("-v", action="store_true", help="also the best path and the paths not counted")
    a = ap.parse_args(argv)
    defs = {}
    if a.defs:
        mod, _, fn = a.defs.partition(":")
        defs.update(getattr(importlib.import_module(mod), fn)())
    for kv in a.D:
        k, _, v = kv.partition("=")
        defs[k] = int(v, 0)
    text = open(a.file).read()
    side = open(a.timing).read() if a.timing else None
    chk, results = check_source(text, defs, a.program, side, clkdiv=a.clkdiv,
                                fifo_stall=a.fifo_stall, line_stall=a.line_stall)
    i, f = chk.clkdiv
    print(f"{chk.name}: {chk.n} words, CLKDIV {i}+{f}/256 (1 tick = {i + f / 256:g} clk)")
    for e in chk.errors:
        print(f"ERROR: {e}")
    bad = bool(chk.errors)
    for r in results:
        print(r.report(a.v))
        bad |= r.status != "PASS"
    if not results:
        print("no @deadline annotations")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
