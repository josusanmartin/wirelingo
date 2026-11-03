#!/usr/bin/env python3
"""Stage 2 gate: the OCaml assembler against tools/pe/asm.py.

    python3 ocaml/test/asm_parity.py [--corpus FILE...] [--fuzz N] [--seed S]

Needs ocaml/_build/default/bin/wl_asm.exe. Every case is assembled by both and
the results compared byte for byte: the canonical dump (program name, origin,
wrap, side-set, config, labels, words, relocation marks, source lines), and the
relocated words as little-endian bytes. Errors compare by message; where
Python's eval() supplied the end of the message (its parser's wording), only
the assembler's part is compared. An exception asm.py does not catch compares by
type. firmware/swd_probe.pasm is excluded and never opened.

Case sets:
  firmware   every firmware/*.pasm except swd_probe, under several define sets
  directed   a list of error and edge cases below
  fuzz       seeded random edits of firmware sources
  corpus     assemble_all() calls recorded from the model test suite
             (capture_plugin.py)
Prints a count per set; exit status 0 only if all of them agree.

SPDX-License-Identifier: Apache-2.0
"""
import argparse
import hashlib
import json
import os
import pathlib
import random
import re
import struct
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))
from pe import asm  # noqa: E402

WL_ASM = REPO / "ocaml/_build/default/bin/wl_asm.exe"


def firmware_files():
    return [p for p in sorted((REPO / "firmware").glob("*.pasm")) if p.name != "swd_probe.pasm"]


def dump(progs):
    out = []
    for p in progs:
        out.append(f"program {p.name}")
        out.append(f"origin {p.origin}")
        out.append(f"wrap {p.wrap_bottom} {p.wrap_top}")
        out.append(f"side {p.side_count} {p.side_opt} {p.side_pindir}")
        for k, v in sorted(p.config.items()):
            out.append(f"config {k} {v!r}")
        for k, v in sorted(p.labels.items()):
            out.append(f"label {k} {v}")
        for i, w in enumerate(p.words):
            out.append(f"word {i} {w:08x} {int(i in p.reloc)}")
        for i, raw in p.source_lines:
            out.append(f"src {i} {raw}")
    return "".join(s + "\n" for s in out)


def py_result(text, defines):
    """(kind, dump or message, progs)"""
    try:
        progs = asm.assemble_all(text, defines)
    except asm.AsmError as e:
        return "error", f"error: {e}\n", None
    except Exception as e:
        return "crash", f"crash: {type(e).__name__}\n", None
    return "ok", dump(progs), progs


def norm(s):
    return re.sub(r"(bad expression '.*?')(?:: .*)?$", r"\1", s, flags=re.M)


class Parity:
    def __init__(self):
        self.n = {}
        self.bad = []
        self.bin_bytes = 0
        self.dir = tempfile.mkdtemp()

    def run_ml(self, text, defines, extra=()):
        args = [str(WL_ASM)]
        for k, v in defines.items():
            args += ["-D", f"{k}={v}"]
        args += list(extra) + ["-"]
        r = subprocess.run(args, input=text.encode(), capture_output=True)
        return r.stdout.decode(errors="surrogateescape"), r.returncode, r.stderr.decode(errors="replace")

    def case(self, group, text, defines=None, bases=()):
        """Compare one case; returns Python's kind."""
        defines = dict(defines or {})
        kind, want, progs = py_result(text, defines)
        out, rc, err = self.run_ml(text, defines)
        c = self.n.setdefault(group, {"ok": 0, "error": 0, "crash": 0, "fail": 0})
        same = norm(out) == norm(want) and rc == {"ok": 0, "error": 1, "crash": 2}[kind]
        if same and kind == "ok":
            for base in bases:
                try:
                    words = [w for p in progs for w in p.relocate(base)]
                except ValueError:
                    continue
                f = os.path.join(self.dir, "o.bin")
                out2, rc2, _ = self.run_ml(text, defines, ("--bin", f, "--base", str(base)))
                data = open(f, "rb").read()
                self.bin_bytes += len(data)
                same = rc2 == 0 and data == b"".join(struct.pack("<I", w) for w in words)
                if not same:
                    break
        c["fail" if not same else kind] += 1
        if not same:
            self.bad.append((group, text[:400], defines, want[:300], out[:300], err[:300]))
        return kind

    def report(self):
        for g, c in self.n.items():
            print(f"{g:10s} agree ok={c['ok']} error={c['error']} crash={c['crash']} DISAGREE={c['fail']}")
        print(f"bytes compared via --bin: {self.bin_bytes}")
        for b in self.bad[:12]:
            print("DISAGREE", json.dumps(b, indent=0)[:1400])
        return not self.bad


# ---- firmware: find what each file needs by asking the Python assembler
KNOWN = {"RX": 16, "TX": 8, "SDA": 0, "SCL": 1, "CLK": 2, "DAT": 3, "DQ": 0, "CEC": 2, "IR": 8, "MASK": 255,
         "TB": 128, "FOV": 0, "RS": 2, "GAP": 40, "LINEDIV": 32, "TURN": 63, "MTBS": 4, "TE": 3, "FERR": 1,
         "FLOST": 2, "F_GO": 0, "A": 0, "CPOL": 1, "ESC": 2}


def define_sets(text):
    """Define sets under which the file assembles or fails only on values."""
    from pe import postfreeze, postfreeze2
    sets = [postfreeze.dshot_bidir_defines(48e6), postfreeze2.modbus_defines(),
            {"D0": 0, "D1": 1, "PW": 50, "IV": 2000, "FG": 20000}]   # the tests' wiegand_tx
    for fill in (None, 1, 2, 3, 5, 8, 16, 40, 100, 1000):
        d = {}
        for _ in range(64):
            try:
                asm.assemble_all(text, d)
                break
            except asm.AsmError as e:
                m = re.search(r"unknown symbol '(\w+)'", str(e))
                if not m:
                    break
                d[m.group(1)] = fill if fill is not None else KNOWN.get(m.group(1), 3)
        if d not in sets:
            sets.append(d)
    return sets


def run_firmware(p):
    files = firmware_files()
    for f in files:
        text = f.read_text()
        good = 0
        for d in define_sets(text):
            good += p.case("firmware", text, d, bases=(0, 256)) == "ok"
        if not good:
            p.bad.append(("firmware", f.name, "no define set assembled", "", "", ""))
    print(f"firmware files: {len(files)} (swd_probe excluded)")


# ---- directed cases
DIRECTED = """
nop
.word
.word 0x1FFFFFFFF
.word -1
.define
.define X
.define X 1 +
.define X 3\nldi r0, X*2
.origin
.origin 5\nnop
.side_set
.side_set 5
.side_set 3 opt pindirs\nnop side 1 [3]\nnop [3] side 1
.side_set 1\nnop
.side_set 1 opt\nnop
.side_set -1\nnop side 0
.side_set -1\nnop [300]
.side_set 2\nnop side 4
.side_set 2\nnop side 1 [64]
nop side 1
nop [256]
nop [255]
nop [-1]
[3]
side 1
.wrap_target\nnop\n.wrap\nnop
.wrap
.clkdiv
.clkdiv abc
.clkdiv 54.25
.clkdiv 1.999
.clkdiv 0.5
.clkdiv 65536
.clkdiv 65535.999
.clkdiv nan
.clkdiv inf
.clkdiv 1e3
.clkdiv 1_0.5
.out_pins
.out_pins 1
.out_pins 1 2
.set_pins 1 2 3
.side_pins
.in_pins 4
.fifo
.fifo join_tx
.fifo join_rx
.shift_out
.shift_out up
.shift_out left autopull 8
.shift_in right autopush
.shift_in right 33
.shift_in right 1 2
.shift_in right 0
.bogus
.program
.program a\nnop\n.program b\nhalt
x: x: nop
a:\nb:\nc: nop\njmp a\njmp b\njmp c
foo
jmp
jmp 1024
jmp 1023
jmp -1
jmp , 1
jmp a
jmp !r0--, 1
jmp r9, 1
jmp pin 32, 1
jmp pin -1, 1
jmp !pin ui0, 1
jmp f 8, 1
jmp !flag 7, 1
jmp x--, 1
jmp !y, 1
jmp !osre, 1
jmp z, 1, 2
call 1
call !z, 5
ret
wait
wait 1
wait 1 pin
wait 1 pin 32
wait 0 pin uio3
wait 1 flag 8
wait 1 flag 3 clear
wait 0 flag 3 clear
wait 1 irq 1
wait rise 3 timeout
wait rise 3 timeout r1
wait rise 3 timeout r1x
wait fall ui0 if pin 3 == 1
wait fall ui0 if pin 3 == 2
wait edge 1 if pin 40 == 1
wait edge 1 IF PIN 2 == 0 TIMEOUT R2
wait 1 pin 3 if pin  == 1
wait pins != r0
wait pins != r1 mask 0x0F timeout r2
wait pins != r1 mask 256
wait pins != r1 mask
wait pins != r1 clear
wait pins != r1 if pin 1 == 1
wait pins != r5
in
in pins
in pins, 0
in pins, 33
in pins, 32
in bogus, 1
out pins, 1
out osr, 1
out status, 1
out lr, 1
out line, 8
push iffull block
push block noblock
push block block
push iffull iffull
push bogus
pull ifempty noblock
pull iffull
mov r0, ~r1
mov r0, !r1
mov r0, ::r1
mov r0, ::
mov lr, r1
mov bogus, r1
mov r0
set pins, 31
set pins, 32
set pindirs, 0
set bogus, 1
ldi r0, 65535
ldi r0, 65536
ldi r9, 1
ldi x, 1
add r0, r1
add r0, 8191
add r0, 8192
add r0, #5
add r0, ##5
cmp y, x
shl r0
pin 3, lo
pin 3, bad
pin 32, hi
PIN 3, HI
flag set 7
flag set 8
flag clear 3
flag clr
flag
halt
halt 1
ldi r0, 1+2*3
ldi r0, (1+2)*3
ldi r0, 7/2
ldi r0, 7//2
ldi r0, 7///2
ldi r0, -7/2+10
ldi r0, 7%4
ldi r0, -7%4+10
ldi r0, 1<<4
ldi r0, 256>>4
ldi r0, 1<<-1
ldi r0, 0xFF&0x0F
ldi r0, 0xF0|0x0F
ldi r0, ~0&255
ldi r0, 2**8
ldi r0, 2**-1
ldi r0, 2**0.5
ldi r0, 1e3
ldi r0, 1E2+1
ldi r0, 1e-1
ldi r0, 1<2
ldi r0, 3>2>1
ldi r0, 1<2<1
ldi r0, 1_000
ldi r0, 1__0
ldi r0, 0x_FF
ldi r0, 0b101
ldi r0, 0b102
ldi r0, 010
ldi r0, 00
ldi r0, 0xG
ldi r0, 1a
ldi r0, ()
ldi r0, (
ldi r0, )
ldi r0, 1 2
ldi r0, 1/0
ldi r0, 1%0
ldi r0, 1e400
ldi r0, 1e400-1e400
ldi r0, nosuch
ldi r0, uio3+ui0
ldi r0, p31
ldi r0, v7
ldi r0, uo6
ldi r0, p32
ldi r0, 1@2
ldi r0, 1+
ldi r0, 'a'
.define A 5\n.define B A*2\nldi r0, B-A
.define A -1\nldi r0, 5*A+10
.define A 1\n.program x\nldi r0, A
L: .define Z L\nnop\nldi r0, Z
.word 1 ; comment\n.word 2 // comment
; only comment
\n\n
   nop   ;   spaced
NOP
Jmp Z, 0
"""


def run_directed(p):
    for case in DIRECTED.strip("\n").split("\n"):
        p.case("directed", case.replace("\\n", "\n"))
    # defines given by the caller, as text
    for text, d in [("ldi r0, X", {"X": 7}), ("ldi r0, X", {"X": -1}), ("ldi r0, p3", {"p3": 9}), ("L: ldi r0, L", {"L": 9}),
                    ("ldi r0, X", {"X": 1.5}), ("ldi r0, X", {"X": "1+1"})]:
        p.case("directed", text, d)
    # line endings and empty input
    for text in ["", "\n", "nop\r\nhalt\r\n", "nop\rhalt", "nop\x0chalt", "nop\x0bhalt", "nop\x1chalt", "nop\u2028halt",
                 "nop\u0085halt", "nop ; \u00a7 comment\nhalt", "\ufeffnop"]:
        p.case("directed", text)


# ---- fuzz
JUNK = list("[]();,#:!~+-*/%<>&|=.0123456789abxyz_ ") + ["side", "if", "pin", "r0", "r9", "timeout", "clear", "==", "--",
                                                          "::", ".wrap", ".define", "0x", "1<<", "opt"]


def fuzz_line(rng, line):
    k = rng.randrange(5)
    if k == 0 and line:                        # delete a character
        i = rng.randrange(len(line))
        return line[:i] + line[i + 1:]
    if k == 1:                                 # insert junk
        i = rng.randrange(len(line) + 1)
        return line[:i] + rng.choice(JUNK) + line[i:]
    if k == 2:                                 # replace a number
        return re.sub(r"\b\d+\b", lambda m: rng.choice(["0", "1", "32", "33", "256", "1024", "-1", "1<<8", "1/0", "0x"]) if rng.random() < 0.5 else m.group(0), line)
    if k == 3:                                 # swap two tokens
        t = line.split()
        if len(t) >= 2:
            i, j = rng.randrange(len(t)), rng.randrange(len(t))
            t[i], t[j] = t[j], t[i]
        return " ".join(t)
    return ""                                  # drop the line


def run_fuzz(p, n, seed):
    rng = random.Random(seed)
    texts = [(f, f.read_text()) for f in firmware_files()]
    for i in range(n):
        f, text = rng.choice(texts)
        d = rng.choice(define_sets_cache.setdefault(f.name, define_sets(text)))
        lines = text.split("\n")
        code = [j for j, l in enumerate(lines) if l.strip() and not l.strip().startswith((";", "//"))]
        for _ in range(rng.randrange(1, 4)):
            j = rng.choice(code)
            lines[j] = fuzz_line(rng, lines[j])
        p.case("fuzz", "\n".join(lines), d)


define_sets_cache = {}


def run_corpus(p, files):
    seen = set()
    total = 0
    for name in files:
        with open(name) as f:
            for line in f:
                total += 1
                h = hashlib.sha256(line.encode()).digest()
                if h in seen:
                    continue
                seen.add(h)
                c = json.loads(line)
                p.case("corpus", c["text"], c["defines"], bases=(0, 256))
    print(f"corpus: {total} recorded calls, {len(seen)} distinct")


# A drop below these means coverage was lost, even with nothing disagreeing.
FLOORS = {"firmware": {"ok": 300}, "directed": {"ok": 130, "error": 110, "crash": 8}, "corpus": {"ok": 250, "error": 20}}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", nargs="*", default=[])
    ap.add_argument("--fuzz", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    p = Parity()
    run_firmware(p)
    run_directed(p)
    run_fuzz(p, a.fuzz, a.seed)
    if a.corpus:
        run_corpus(p, a.corpus)
    ok = p.report()
    low = [(g, k, p.n[g][k], v) for g, floors in FLOORS.items() if g in p.n for k, v in floors.items() if p.n[g][k] < v]
    if a.fuzz >= 5000 and a.corpus and (p.bin_bytes < 270000 or len(p.n) < 4):
        low.append(("bytes", p.bin_bytes))
    if low:
        print("BELOW FLOOR:", low)
    print("PARITY" if ok and not low else "DISAGREE")
    sys.exit(0 if ok and not low else 1)


if __name__ == "__main__":
    main()
