#!/usr/bin/env python3
"""Stage 4 check: wl_export against tools/pe/host.py, export.py and isa.disasm.

    python3 ocaml/test/host_parity.py

For every firmware/*.pasm (swd_probe excluded, never opened) that assembles:
the listing, the load_program writes, and the text of `python -m pe.export` on
both cores with a few pin and clock options; the same for two v0.3 programs
written here (table reads, `.data` words that read as out-of-bank jumps, CMPW,
`!t`, `flag tog`, ticks); and isa.disasm on 200000 words (all opcodes and random
operands). Errors compare by the last stderr line. Prints
counts; exit status 0 only if all agree and every count reaches its floor.

SPDX-License-Identifier: Apache-2.0
"""
import pathlib
import random
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO / "ocaml/test"))
from pe import asm, host, isa  # noqa: E402
from asm_parity import define_sets, firmware_files  # noqa: E402

WL = str(REPO / "ocaml/_build/default/bin/wl_export.exe")
ENV = {"PYTHONPATH": str(REPO / "tools"), "PYTHONDONTWRITEBYTECODE": "1", "PATH": "/usr/bin:/bin"}


# A drop below these means coverage was lost, even with nothing disagreeing.
FLOORS = {"files": 70, "v03_files": 2, "export": 800, "export_err": 380, "writes": 170, "listing": 170}

# v0.3 syntax (docs/isa.md section 5.2), which no firmware file used when this was
# written. A `.data` word is a table entry, not a jump, so load_program does not
# check its address field; the same word as `.word` must be refused.
V03 = {
    "v03_table.pasm": """
.program v03_table
top:
    ldi r1, t
    mov osr, [r1]
    mov r0, osr
    cmpw r0, 0xBEEF
    jmp !z, top
    mov osr, [r1] [2]
    mov r2, ticks
    in ticks, 16
    flag tog 2
    jmp !t, top
    call !t, top
    cmp r2, 40000
    mov pc, r2
t:  .data 0xBEEF
    .data 0x000003FF
""",
    "v03_word.pasm": """
.program v03_word
    mov osr, [r0]
    jmp 0
    .word 0x000003FF
""",
}


def assembles(text, d):
    try:
        asm.assemble_all(text, d)
        return True
    except asm.AsmError:
        return False


def last_err(s):
    t = s.strip().split("\n")
    return t[-1] if t else ""


def main():
    n = {"files": 0, "v03_files": 0, "export": 0, "export_err": 0, "writes": 0, "listing": 0, "bad": 0}
    bad = []
    tmp = pathlib.Path(tempfile.mkdtemp())
    for name, text in V03.items():
        (tmp / name).write_text(text)
    v03 = [tmp / name for name in V03]
    for f in firmware_files() + v03:
        text = f.read_text()
        sets = define_sets(text)
        # sets[:3] are fixed sets for three firmwares, the rest discovered for this file;
        # up to three that assemble, the discovered ones first
        use = [d for d in sets[3:] if assembles(text, d)][:3] or [d for d in sets[:3] if assembles(text, d)]
        covered = False
        for d in use:
            try:
                progs = asm.assemble_all(text, d)
            except asm.AsmError:
                continue
            covered = True
            if len(progs) != 1:
                sel = [["--program", p.name] for p in progs if p.name]
            else:
                sel = [[]]
            defs = [x for k, v in d.items() for x in ("--define", f"{k}={v}")]
            opts = [[], ["--core", "1"], ["--out-pins", "8,3", "--side-pins", "10", "--in-pins", "16", "--clkdiv", "54.25"],
                    ["--core", "1", "--base", "300", "--set-pins", "1,2"], ["--profile", "full", "--core", "3"],
                    ["--base", "250"], ["--core", "2"]]
            for s in sel:
                for o in opts:
                    args = [str(f), *defs, *s, *o]
                    py = subprocess.run([sys.executable, "-m", "pe.export", *args], capture_output=True, text=True, env=ENV)
                    ml = subprocess.run([WL, *args], capture_output=True, text=True)
                    if py.returncode:
                        same = bool(ml.returncode) and last_err(py.stderr) == last_err(ml.stderr)
                        n["export_err"] += same
                    else:
                        same = py.stdout == ml.stdout and ml.returncode == 0
                        n["export"] += same
                    if not same:
                        n["bad"] += 1
                        bad.append((args, py.stdout[:300], py.stderr[-200:], ml.stdout[:300], ml.stderr[-200:]))
                p = progs[0] if not s else [q for q in progs if q.name == s[1]][0]
                base = p.origin if p.origin is not None else 0
                want = p.listing(base) + "\n"
                ml = subprocess.run([WL, "--listing", str(f), *defs, *s, "--base", str(base)], capture_output=True, text=True)
                n["listing"] += want == ml.stdout
                if want != ml.stdout:
                    n["bad"] += 1
                    bad.append(("listing", f.name, want[:400], ml.stdout[:400], ml.stderr[-200:]))
                try:
                    w = host.load_program(0, p)
                except ValueError:
                    continue
                want = "".join(f"{a} {b}\n" for a, b in w)
                ml = subprocess.run([WL, "--writes", str(f), *defs, *s], capture_output=True, text=True)
                n["writes"] += want == ml.stdout
                if want != ml.stdout:
                    n["bad"] += 1
                    bad.append(("writes", f.name, want[:300], ml.stdout[:300], ml.stderr[-200:]))
        n["v03_files" if f in v03 else "files"] += covered
    rng = random.Random(1)
    words = [(op << 27) | rng.getrandbits(27) for op in range(32) for _ in range(1000)] + \
        [rng.getrandbits(32) for _ in range(168000)]
    py = "".join(isa.disasm(w) + "\n" for w in words)
    ml = subprocess.run([WL, "--disasm"], input="".join(f"{w:08x}\n" for w in words), capture_output=True, text=True)
    same = py == ml.stdout
    print(f"disasm words {len(words)} {'agree' if same else 'DISAGREE'}")
    if not same:
        n["bad"] += 1
        for a, b, w in zip(py.split("\n"), ml.stdout.split("\n"), words):
            if a != b:
                print(f"{w:08x}: py {a!r} ml {b!r}")
                break
    print(n)
    for b in bad[:6]:
        print("DISAGREE", str(b)[:900])
    low = {k: (n[k], v) for k, v in FLOORS.items() if n[k] < v}
    if low:
        print("BELOW FLOOR (got, floor):", low)
    sys.exit(1 if n["bad"] or low else 0)


if __name__ == "__main__":
    main()
