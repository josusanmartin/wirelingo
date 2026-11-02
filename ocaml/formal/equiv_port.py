#!/usr/bin/env python3
"""Formal equivalence of the Hardcaml port's generated Verilog against src/.

    equiv_port.py [-j N] [--mutants] [--skip-simple] GEN_DIR OUT

GEN_DIR holds wirelingo_hc.v (ocaml/bin/gen_rtl tapeout GEN_DIR). OUT is a new
work directory. The method is formal/equiv's (docs/equivalence.md), with the
generated Verilog in the netlist's place: both designs are elaborated by yosys
(proc, flatten), every state element is matched and cut, and the two
combinational modules are proved equal by ABC &cec (every x of src/ a free
input) and by yosys equiv_simple per point, in parallel shards. With every
compared point proved for all inputs and all states, the two designs are
sequentially equivalent from corresponding states. See docs/hardcaml-port.md.

Exit status 0 only if every point is proved by both engines, nothing is
unmatched, and (with --mutants) every planted bug is caught.

SPDX-License-Identifier: Apache-2.0
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, "formal", "equiv"))
import equiv  # noqa: E402  (formal/equiv/equiv.py, unmodified: abc_check, simple, categorize, yosys, sh)

SRC = [f"{REPO}/src/{f}" for f in ("project.v", "pe_chip.sv", "pe_core.sv", "pe_line.sv", "pe_fifos.sv",
                                   "pe_fifo.sv", "pe_cg.sv", "pe_imem.sv", "pe_hostport.sv")]
# what LibreLane defines for the tapeout, formal/equiv/equiv.py resolve(); only
# the first selects anything in src/ (checked in main)
DEFINES = ["SCL_sg13cmos5l_stdcell", "PDK_ihp_sg13cmos5l", "__librelane__", "__pnr__"]
TOP = "tt_um_josusanmartin_protoemu"


def elaborate(sources, out, tag):
    d = " ".join(f"-D{x}" for x in DEFINES) + f" -I{REPO}/src"
    reads = "".join(f"read_verilog -defer -noautowire -sv {d} {f}; " for f in sources)
    equiv.yosys(
        f"read_verilog -lib {HERE}/cells_stub.v; {reads}"
        f"hierarchy -check -top {TOP}; proc; flatten; delete t:$scopeinfo; opt_clean; "
        f"tee -o {out}/{tag}_stat.txt stat; "
        "select -assert-none t:$dlatch t:$adlatch t:$dlatchsr t:$_DLATCH* t:$sr t:$_SR_* t:$mem* t:$dffsr t:$aldff t:$_ALDFF* t:$_DFFSR*; "
        f"dffunmap; simplemap t:$dff t:$adff; write_json {out}/{tag}.json",
        f"{out}/{tag}.log")


def module_ports(sources, out, tag):
    """{module: {port: (direction, width)}} of every module, as elaborated."""
    d = " ".join(f"-D{x}" for x in DEFINES) + f" -I{REPO}/src"
    reads = "".join(f"read_verilog -defer -noautowire -sv {d} {f}; " for f in sources)
    equiv.yosys(f"read_verilog -lib {HERE}/cells_stub.v; {reads}hierarchy -top {TOP}; proc; write_json {out}/{tag}_ports.json",
                f"{out}/{tag}_ports.log")
    mods = {}
    for name, m in json.load(open(f"{out}/{tag}_ports.json"))["modules"].items():
        if m.get("attributes", {}).get("blackbox"):
            continue
        m2 = re.match(r"^\$paramod(?:\$[0-9a-f]+)?\\([^\\]+)", name)    # pe_core with IDX=1, ...
        base = m2.group(1) if m2 else name
        base = re.sub(r"_\d+$", "", base)                # Hardcaml's pe_core_0
        mods.setdefault(base, {}).update(
            {f"{p}": (v["direction"], len(v["bits"])) for p, v in m["ports"].items()})
    return mods


def cut(out, gate_json, dst):
    os.makedirs(dst, exist_ok=True)
    equiv.sh([sys.executable, f"{HERE}/port_cut.py", "--gold", f"{out}/gold.json", "--gate", gate_json, "--out", dst],
             f"{dst}/cut.log")
    return json.load(open(f"{dst}/match.json"))


# Planted bugs: one small edit of the generated Verilog each. A check that
# cannot see these would not be evidence. Each must match exactly once.
def first_flop_of(name):
    return re.compile(r"always @\(posedge (\S+) or negedge (\S+)\) begin\n\s+if \(\S+ == 0\)\n\s+" + name + r" <= (\S+);")


def mutate(text, kind):
    if kind == "wrong-gate":             # AND becomes OR, first such assign in pe_line
        i = text.index("module pe_line (")
        m = re.compile(r"assign (\S+) = (\S+) & (\S+);").search(text, i)
        return text[:m.start()] + f"assign {m.group(1)} = {m.group(2)} | {m.group(3)};" + text[m.end():]
    if kind == "swapped-mux-legs":       # first two-way mux of pe_hostport
        i = text.index("module pe_hostport (")
        m = re.compile(r"assign (\S+) = (\S+) \? (\S+) : (\S+);").search(text, i)
        return text[:m.start()] + f"assign {m.group(1)} = {m.group(2)} ? {m.group(4)} : {m.group(3)};" + text[m.end():]
    if kind == "falling-edge":           # the negedge rx_n1 flop made a posedge flop
        m = re.search(r"always @\(negedge (\S+) or negedge (\S+)\) begin\n\s+if \(\S+ == 0\)\n\s+rx_n1 <=", text)
        return text[:m.start()] + m.group(0).replace("negedge " + m.group(1), "posedge " + m.group(1), 1) + text[m.end():]
    if kind == "reset-to-set":           # r0 of core 1 loses its reset polarity: active-high reset
        i = text.index("module pe_core (")
        m = first_flop_of("r0").search(text, i)
        return text[:m.start()] + m.group(0).replace("negedge " + m.group(2), "posedge " + m.group(2)).replace("== 0", "== 1") + text[m.end():]
    if kind == "swapped-clock-gate-enables":
        i = text.index("module pe_chip (")
        ms = list(re.compile(r"\.en\((\S+)\),\s*\.clk\((\S+)\)|\.clk\((\S+)\),\s*\.en\((\S+)\)").finditer(text, i))[:2]
        a, b = ms[0], ms[1]
        ae, be = (a.group(1) or a.group(4)), (b.group(1) or b.group(4))
        ta = a.group(0).replace(f".en({ae})", f".en({be})")
        tb = b.group(0).replace(f".en({be})", f".en({ae})")
        return text[:a.start()] + ta + text[a.end():b.start()] + tb + text[b.end():]
    if kind == "sram-address-swapped":   # raddr and waddr legs exchanged at the macro
        m = re.search(r"\.A_ADDR\((\S+)\)", text)
        sig = m.group(1)
        d = re.search(r"assign " + re.escape(sig) + r" = (\S+) \? (\S+) : (\S+);", text)
        return text[:d.start()] + f"assign {sig} = {d.group(1)} ? {d.group(3)} : {d.group(2)};" + text[d.end():]
    if kind == "dropped-register":       # a flop's D tied to its Q (a stuck register)
        i = text.index("module pe_line (")
        m2 = re.compile(r"(\n\s+else\n\s+)tcnt <= (\S+);").search(text, i)
        return text[:m2.start()] + m2.group(1) + "tcnt <= tcnt;" + text[m2.end():]
    if kind == "reset-value":            # osr_cnt resets to 32: make the reset value 0 on one core
        i = text.index("module pe_core (")
        m = first_flop_of("osr_cnt").search(text, i)
        return text[:m.start()] + m.group(0).replace(f"osr_cnt <= {m.group(3)}", "osr_cnt <= 6'b0") + text[m.end():]
    raise ValueError(kind)


MUTANTS = ["wrong-gate", "swapped-mux-legs", "falling-edge", "reset-to-set", "swapped-clock-gate-enables",
           "sram-address-swapped", "dropped-register", "reset-value"]


def mutants(gen, out):
    res = []
    text = open(gen).read()
    for k, kind in enumerate(MUTANTS):
        d = f"{out}/mutants/m{k}"
        os.makedirs(d, exist_ok=True)
        new = mutate(text, kind)
        if new == text:
            sys.exit(f"mutant {kind} did not change the file")
        v = f"{d}/wirelingo_hc.v"
        open(v, "w").write(new)
        elaborate([v, f"{REPO}/src/pe_cg.sv"], d, "gate")
        m = cut(out, f"{d}/gate.json", d)
        # a flop whose edge or reset changed, or that synthesis of the mutant
        # dropped, is caught by the state-element check, the rest by the proof;
        # either stops the run. Unmatched flops stay in the cut modules, where
        # ABC cannot read them.
        s = m["summary"]
        unmatched = bool(m["unmatched_gold_ff"] or m["unmatched_gate_ff"])
        structural = bool(m["type_mismatch"]) or unmatched
        st = "not run: flops unmatched" if unmatched else equiv.abc_check(d, "mut")
        caught = st == "not equivalent" or structural
        res.append({"mutant": kind, "abc": st, "state_check": structural, "caught": caught,
                    "matched_ff": s["matched_ff"]})
        equiv.log(f"  mutant {kind}: abc {st}, state check {'failed' if structural else 'clean'}")
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("gen")
    ap.add_argument("out")
    ap.add_argument("-j", "--jobs", type=int, default=4)
    ap.add_argument("--skip-simple", action="store_true")
    ap.add_argument("--mutants", action="store_true")
    a = ap.parse_args()
    if os.path.exists(a.out) and os.listdir(a.out):
        sys.exit(f"equiv_port: {a.out} exists and is not empty")
    os.makedirs(a.out, exist_ok=True)
    out = os.path.realpath(a.out)
    for tool in ("yosys", "yosys-abc"):
        if not shutil.which(tool):
            sys.exit(f"equiv_port: {tool} not on PATH")
    t0 = time.time()
    gen = os.path.join(os.path.realpath(a.gen), "wirelingo_hc.v")
    src_text = "".join(open(f).read() for f in SRC)
    for x in DEFINES[1:]:
        if x in src_text:
            sys.exit(f"equiv_port: {x} is used in src/: review DEFINES")
    gold_mods = module_ports(SRC, out, "gold")
    gate_mods = module_ports([gen, f"{REPO}/src/pe_cg.sv"], out, "gate")
    port_diff = {k: "only one side" for k in set(gold_mods) ^ set(gate_mods)}
    for k in set(gold_mods) & set(gate_mods):
        d = set(gold_mods[k].items()) ^ set(gate_mods[k].items())
        if d:
            port_diff[k] = sorted(d)
    equiv.log(f"module ports: {len(gold_mods)} modules, differing: {sorted(port_diff)}")
    elaborate(SRC, out, "gold")
    equiv.log("gold elaborated")
    elaborate([gen, f"{REPO}/src/pe_cg.sv"], out, "gate")
    equiv.log("gate elaborated")
    m = cut(out, f"{out}/gate.json", f"{out}/cut")
    print(open(f"{out}/cut/cut.log").read(), end="")
    abc_res = equiv.abc_check(f"{out}/cut", "port")
    equiv.log(f"ABC: {abc_res}")
    sres = None if a.skip_simple else equiv.simple(out, a.jobs)
    if sres:
        equiv.log(f"equiv_simple: {sres['proven']}/{sres['equiv_cells']} proven, {len(sres['unproven'])} unproven")
    mres = mutants(gen, out) if a.mutants else None
    s = m["summary"]
    cats = equiv.categorize(out)
    res = {
        "yosys": subprocess.run(["yosys", "-V"], capture_output=True, text=True).stdout.strip(),
        "gate_sha256": hashlib.sha256(open(gen, "rb").read()).hexdigest(),
        "src_sha256": {os.path.basename(f): hashlib.sha256(open(f, "rb").read()).hexdigest() for f in SRC},
        "modules": sorted(gold_mods), "port_differences": port_diff,
        "match": s, "compared_points": cats, "abc": abc_res, "equiv_simple": sres, "mutants": mres,
        "time_s": round(time.time() - t0, 1),
    }
    ok = (not port_diff and abc_res == "equivalent" and (sres is None or (not sres["unproven"] and not sres["failed_shards"]
                                                          and sres["proven"] == sres["equiv_cells"]))
          and not any(m[k] for k in ("unmatched_gold_ff", "unmatched_gate_ff", "type_mismatch",
                                     "unmatched_gold_bb", "unmatched_gate_bb"))
          and (mres is None or all(x["caught"] for x in mres)))
    res["verdict"] = "EQUIVALENT" if ok else "NOT PROVEN"
    json.dump(res, open(f"{out}/result.json", "w"), indent=1)
    print(f"""
==== port equivalence: {TOP}
generated Verilog   {res['gate_sha256']}
checker             {res['yosys']}
module ports        {len(gold_mods)} modules: {'all equal' if not port_diff else port_diff}
state elements      flops gold {s['gold_ff']} / gate {s['gate_ff']}: matched {s['matched_ff']}
                    instances (latches, clock gates, SRAMs) {s['matched_bb']}/{s['gold_bb']} by name
compared points     {cats} (total {sum(cats.values())})
ABC &cec            {abc_res} (src/ x values free)
equiv_simple        {'skipped' if not sres else f"{sres['proven']}/{sres['equiv_cells']} proven, unproven: {sres['unproven'][:20]}"}
mutants             {'not run' if mres is None else ', '.join(f"{x['mutant']}:{'caught' if x['caught'] else 'MISSED'}" for x in mres)}
time                {res['time_s']} s
VERDICT             {res['verdict']}
""")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
