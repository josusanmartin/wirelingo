#!/usr/bin/env python3
"""RTL-vs-netlist equivalence check of a LibreLane run (driver).

    equiv.py [-j N] [--mutants] [--skip-simple] RUN OUT

RUN is a hardening worktree (with runs/wokwi) or its runs/<tag> directory;
OUT a fresh work directory (never inside RUN: the run is read-only here).
See docs/equivalence.md for the method and what a pass means.

Steps (each timed in OUT/result.json):
  inputs     resolve RTL files, defines, cell models, macro stubs, netlists
             from the run's resolved.json and synthesis step; hash them
  cells      PDK Verilog models -> yosys library (cells.py); every
             combinational cell the netlist uses is checked against the
             Liberty function the synthesis used, and the flop's Liberty
             ff group against the semantics the check assumes
  gold       RTL elaborated by yosys exactly as the flow reads it (same
             files, defines, parameters), flattened; flops bit-blasted
  gate       final netlist with the cell models, flattened
  match      state-element correspondence (cut.py, pair.py)
  abc        ABC &cec on the whole miter, every RTL x a free input
  simple     yosys equiv_simple per compared point (sharded, -j N)
  mutants    optional self-test: mutated netlists must fail

Exit status 0 only if every point is proven by both engines, nothing in the
netlist is unmatched, and (with --mutants) every mutant is caught.

SPDX-License-Identifier: Apache-2.0
"""
import argparse
import concurrent.futures as cf
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TIMES = {}


def log(msg):
    print(f"[equiv {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def sh(cmd, logf=None, check=True):
    with open(logf, "w") if logf else open(os.devnull, "w") as f:
        r = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT)
    if check and r.returncode:
        sys.exit(f"equiv: command failed ({r.returncode}): {' '.join(cmd)[:300]}" + (f"\n  see {logf}" if logf else ""))
    return r.returncode


def yosys(script, logf, check=True):
    return sh(["yosys", "-q", "-l", logf, "-p", script], None, check)


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def timed(name):
    def deco(fn):
        def run(*a, **k):
            t = time.time()
            log(f"{name} ...")
            r = fn(*a, **k)
            TIMES[name] = round(time.time() - t, 1)
            log(f"{name} done in {TIMES[name]} s")
            return r
        return run
    return deco


@timed("inputs")
def resolve(run, out):
    rd = run
    if not os.path.exists(os.path.join(rd, "resolved.json")):
        tags = sorted(glob.glob(os.path.join(run, "runs", "*", "resolved.json")))
        if len(tags) != 1:
            sys.exit(f"equiv: expected one runs/*/resolved.json under {run}, found {len(tags)}")
        rd = os.path.dirname(tags[0])
    rd = os.path.realpath(rd)
    if os.path.realpath(out).startswith(os.path.dirname(os.path.dirname(rd)) + os.sep):
        sys.exit("equiv: OUT must not be inside the run directory")
    synth_dirs = sorted(glob.glob(os.path.join(rd, "*-yosys-synthesis")))
    if len(synth_dirs) != 1:
        sys.exit(f"equiv: expected one *-yosys-synthesis step, found {synth_dirs}")
    sd = synth_dirs[0]
    # the configuration the synthesis step itself was given (same keys as
    # resolved.json; the read below mirrors LibreLane's synthesize.py)
    cfg = json.load(open(os.path.join(sd, "config.json")))
    meta = json.load(open(os.path.join(rd, "resolved.json"))).get("meta") or {}
    design = cfg["DESIGN_NAME"]
    slog = os.path.join(sd, "yosys-synthesis.log")
    libs = re.findall(r"Executing Liberty frontend: (\S+)", open(slog, errors="replace").read(4 << 20))
    stdlib = [l for l in libs if cfg["STD_CELL_LIBRARY"] in os.path.basename(l)]
    models = cfg["CELL_VERILOG_MODELS"]
    if len(models) != 1:
        sys.exit(f"equiv: expected one CELL_VERILOG_MODELS file, got {models}")
    udp = glob.glob(os.path.join(os.path.dirname(models[0]), "*udp*.v"))
    macros = {name: m["nl"] for name, m in (cfg.get("MACROS") or {}).items()}
    defines = list(cfg.get("VERILOG_DEFINES") or []) + [
        f"PDK_{cfg['PDK'].replace('-', '_')}", f"SCL_{cfg['STD_CELL_LIBRARY']}", "__librelane__", "__pnr__"]
    if "PAD_CELL_LIBRARY" in cfg:
        defines.append(f"PAD_{cfg['PAD_CELL_LIBRARY']}")
    inp = {
        "run": rd, "design": design, "librelane": meta.get("librelane_version"),
        "rtl": cfg["VERILOG_FILES"], "defines": defines,
        "synth_parameters": cfg.get("SYNTH_PARAMETERS") or [],
        "include_dirs": cfg.get("VERILOG_INCLUDE_DIRS") or [],
        "cell_models": models[0], "udp": udp[0] if len(udp) == 1 else None,
        "liberty": stdlib[0] if stdlib else None, "macros": macros,
        "final_netlist": os.path.join(rd, "final", "nl", f"{design}.nl.v"),
        "synth_netlist": os.path.join(sd, f"{design}.nl.v"), "synth_log": slog,
    }
    if not inp["udp"] or not inp["liberty"]:
        sys.exit(f"equiv: could not find the UDP file or the synthesis Liberty file: {inp}")
    files = inp["rtl"] + [inp["cell_models"], inp["udp"], inp["liberty"], inp["final_netlist"],
                          inp["synth_netlist"]] + [f for v in macros.values() for f in v]
    inp["sha256"] = {f: sha(f) for f in files}
    json.dump(inp, open(os.path.join(out, "inputs.json"), "w"), indent=1)
    return inp


def macro_reads(inp):
    return "".join(f"read_verilog -lib {f}; " for v in inp["macros"].values() for f in v)


@timed("cells")
def cells(inp, out):
    lib = os.path.join(out, "lib")
    os.makedirs(lib, exist_ok=True)
    sh([sys.executable, os.path.join(HERE, "cells.py"), "--cells", inp["cell_models"], "--udp", inp["udp"],
        "--out", lib], os.path.join(out, "cells.log"))
    # cells the final netlist uses
    used = sorted(set(re.findall(r"^\s*(\w+)\s+\\?\S+\s*\(", open(inp["final_netlist"]).read(), re.M)) - {"module"})
    inv = json.load(open(os.path.join(lib, "cells.json")))["cells"]
    outs = json.load(open(os.path.join(lib, "cells.json")))["outputs"]
    comb = [c for c in used if inv.get(c) == "comb" and outs.get(c)]   # fill, decap, antenna: no outputs
    # Verilog model vs Liberty function, per combinational cell used
    script = [f"read_verilog {lib}/comb.v"]
    for c in comb:
        script.append(f"rename {c} v_{c}")
    script.append(f"read_liberty -overwrite -ignore_miss_func {inp['liberty']}")
    for c in comb:
        script.append(f"miter -equiv -flatten -make_assert v_{c} {c} m_{c}; sat -verify -prove-asserts -show-ports m_{c}")
    yosys("; ".join(script), os.path.join(out, "cells_vs_liberty.log"))
    # the flop's Liberty ff group must say what cut.py assumes
    text = open(inp["liberty"]).read()
    ffs = {}
    for c in used:
        if inv.get(c) == "seq-stub":
            m = re.search(r"cell\s*\(\s*\"?" + c + r"\"?\s*\)(.*?)\n\s*cell\s*\(", text, re.S)
            body = m.group(1) if m else ""
            ff = re.search(r"\bff\s*\([^)]*\)\s*\{([^}]*)\}", body)
            la = re.search(r"\blatch\s*\([^)]*\)\s*\{([^}]*)\}", body)
            cg = re.search(r"clock_gating_integrated_cell\s*:\s*\"?([\w]+)", body)
            ffs[c] = {"ff": " ".join(ff.group(1).split()) if ff else None,
                      "latch": " ".join(la.group(1).split()) if la else None,
                      "icg": cg.group(1) if cg else None}
    for c, v in ffs.items():
        if c.startswith("sg13cmos5l_dfrbpq"):
            want = ['clear : "!RESET_B";', 'clocked_on : "CLK";', 'next_state : "D";']
            if not v["ff"] or any(w not in v["ff"] for w in want):
                sys.exit(f"equiv: Liberty ff group of {c} is not a rising-edge D flop with RESET_B clear: {v}")
    res = {"used_cells": used, "combinational_checked": comb, "sequential": ffs,
           "unknown": [c for c in used if c not in inv and not c.startswith("RM_")],
           "unsupported": [c for c in used if inv.get(c, "").startswith("unsupported")]}
    if res["unknown"] or res["unsupported"]:
        sys.exit(f"equiv: netlist uses unknown/unsupported cells: {res['unknown'] + res['unsupported']}")
    json.dump(res, open(os.path.join(out, "cells_check.json"), "w"), indent=1)
    return res


@timed("gold")
def gold(inp, out):
    lib = os.path.join(out, "lib")
    d = " ".join(f"-D{x}" for x in inp["defines"]) + " " + " ".join(f"-I{x}" for x in inp["include_dirs"])
    reads = "".join(f"read_verilog -defer -noautowire -sv {d} {f}; " for f in inp["rtl"])
    chp = "".join(f"chparam -set {p.split('=', 1)[0]} {p.split('=', 1)[1]} {inp['design']}; "
                  for p in inp["synth_parameters"])
    yosys(f"read_verilog -lib {lib}/seq.v; {macro_reads(inp)}{reads}{chp}"
          f"hierarchy -check -top {inp['design']}; proc; flatten; delete t:$scopeinfo; opt_clean; "
          f"tee -o {out}/gold_stat.txt stat; "
          "select -assert-none t:$dlatch t:$adlatch t:$dlatchsr t:$_DLATCH* t:$sr t:$_SR_* t:$mem* t:$dffsr t:$aldff t:$_ALDFF* t:$_DFFSR*; "
          f"dffunmap; simplemap t:$dff t:$adff; write_json {out}/gold_raw.json", os.path.join(out, "gold.log"))
    # the parameter values the elaboration used, for the record
    params = sorted(set(re.findall(r"^Parameter \\(\w+) = (\S+)", open(os.path.join(out, "gold.log")).read(), re.M)))
    return params


def _gate(inp, out, netlist=None, tag="gate"):
    lib = os.path.join(out, "lib")
    netlist = netlist or inp["final_netlist"]
    yosys(f"read_verilog {lib}/comb.v; read_verilog -lib {lib}/seq.v; {macro_reads(inp)}"
          f"read_verilog {netlist}; hierarchy -check -top {inp['design']}; flatten; delete t:$scopeinfo; "
          f"opt_clean; write_json {out}/{tag}_raw.json", os.path.join(out, f"{tag}.log"))


gate = timed("gate")(_gate)


@timed("synth-netlist")
def synth(inp, out):
    lib = os.path.join(out, "lib")
    yosys(f"read_verilog -lib {lib}/comb.v; read_verilog -lib {lib}/seq.v; {macro_reads(inp)}"
          f"read_verilog {inp['synth_netlist']}; hierarchy -top {inp['design']}; write_json {out}/synth_raw.json",
          os.path.join(out, "synth.log"))


def cut(inp, out, gate_json, dst, pairing=False, pairs=None):
    os.makedirs(dst, exist_ok=True)
    cmd = [sys.executable, os.path.join(HERE, "cut.py"), "--gold", f"{out}/gold_raw.json", "--gate", gate_json,
           "--synth", f"{out}/synth_raw.json", "--synth-log", inp["synth_log"], "--out", dst]
    if pairing:
        cmd.append("--pairing")
    if pairs:
        cmd += ["--pairs", pairs]
    sh(cmd, os.path.join(dst, "cut.log"))
    return json.load(open(os.path.join(dst, "match.json")))


@timed("match")
def match(inp, out):
    p = os.path.join(out, "pairing")
    cut(inp, out, f"{out}/gate_raw.json", p, pairing=True)
    yosys(f"read_json {p}/gold_cut.json; techmap; opt_clean; write_json {p}/gold_fine.json; design -reset; "
          f"read_json {p}/gate_cut.json; techmap; opt_clean; write_json {p}/gate_fine.json", f"{p}/techmap.log")
    sh([sys.executable, os.path.join(HERE, "pair.py"), "--gold", f"{p}/gold_fine.json", "--gate",
        f"{p}/gate_fine.json", "--aliases", f"{p}/aliases.json", "--out", f"{out}/pairs.json"], f"{p}/pair.log")
    m = cut(inp, out, f"{out}/gate_raw.json", os.path.join(out, "cut"), pairs=f"{out}/pairs.json")
    return m


def abc_check(cutdir, tag):
    """ABC &cec on the miter; every x of the RTL is a free input (setundef -anyseq)."""
    aig = os.path.join(cutdir, f"miter_{tag}.aig")
    yosys(f"read_json {cutdir}/gold_cut.json; read_json {cutdir}/gate_cut.json; "
          f"miter -equiv -flatten gold gate miter; hierarchy -top miter; techmap; opt_clean; "
          f"setundef -anyseq; aigmap; write_aiger {aig}", os.path.join(cutdir, f"miter_{tag}.log"))
    lf = os.path.join(cutdir, f"abc_{tag}.log")
    sh(["yosys-abc", "-c", f"read_aiger {aig}; print_stats; &get; &cec -m"], lf, check=False)
    text = open(lf).read()
    if "Networks are equivalent" in text:
        return "equivalent"
    if "NOT EQUIVALENT" in text:
        return "not equivalent"
    return "undecided"


@timed("abc")
def abc(out):
    return abc_check(os.path.join(out, "cut"), "final")


def shard_ports(cutdir, dst, outputs):
    os.makedirs(dst, exist_ok=True)
    for side in ("gold", "gate"):
        d = json.load(open(f"{cutdir}/{side}_cut.json"))
        m = d["modules"][side]
        drop = [p for p, v in m["ports"].items() if v["direction"] == "output" and p not in outputs]
        for p in drop:
            del m["ports"][p]
            del m["netnames"][p]
        json.dump(d, open(f"{dst}/{side}_cut.json", "w"))


def simple_shard(dst):
    lf = os.path.join(dst, "equiv.log")
    yosys(f"read_json {dst}/gold_cut.json; read_json {dst}/gate_cut.json; equiv_make gold gate equiv; "
          f"hierarchy -top equiv; opt_clean; equiv_simple; tee -o {dst}/status.txt equiv_status", lf, check=False)
    status =open(f"{dst}/status.txt").read() if os.path.exists(f"{dst}/status.txt") else ""
    m = re.search(r"Found (\d+) \$equiv cells.*?Of those cells (\d+) are proven and (\d+) are unproven", status, re.S)
    unproven = re.findall(r"Unproven \$equiv \S+ \\(\S+?)_gold(?: \[(\d+)\])?", status)
    return {"equiv_cells": int(m.group(1)) if m else None, "proven": int(m.group(2)) if m else None,
            "unproven": [f"{n}[{i}]" if i else n for n, i in unproven]}


@timed("simple")
def simple(out, jobs, only=None):
    cutdir = os.path.join(out, "cut")
    m = json.load(open(f"{cutdir}/gold_cut.json"))["modules"]["gold"]
    outs = sorted(p for p, v in m["ports"].items() if v["direction"] == "output" and (not only or re.search(only, p)))
    # interleave so every shard gets a mix of flops, latches and pins
    shards = [outs[i::jobs] for i in range(jobs)]
    dirs = [os.path.join(out, "simple", f"shard{i}") for i in range(jobs)]
    for d, s in zip(dirs, shards):
        shard_ports(cutdir, d, set(s))
    with cf.ThreadPoolExecutor(jobs) as ex:
        results = list(ex.map(simple_shard, dirs))
    total = sum(r["equiv_cells"] or 0 for r in results)
    proven = sum(r["proven"] or 0 for r in results)
    unproven = [u for r in results for u in r["unproven"]]
    bad = [i for i, r in enumerate(results) if r["equiv_cells"] is None]
    # an unproven bit of a multi-bit port is reported as port[i]
    ports = sorted({u if u in m["ports"] else re.sub(r"\[\d+\]$", "", u) for u in unproven})
    cexs = [counterexample(cutdir, os.path.join(out, "cex", f"cex{i}"), p)
            for i, p in enumerate(p for p in ports if p in m["ports"]) if i < 5]
    return {"equiv_cells": total, "proven": proven, "unproven": unproven, "failed_shards": bad,
            "ports": len(outs), "counterexamples": cexs}


def counterexample(cutdir, dst, port):
    """A SAT counterexample for one unproven output port: the values of every
    cut-module input (design inputs, flop states S:, black-box outputs O:)
    for which gold and gate differ, written to dst/cex.log."""
    shard_ports(cutdir, dst, {port})
    lf = os.path.join(dst, "cex.log")
    yosys(f"read_json {dst}/gold_cut.json; read_json {dst}/gate_cut.json; "
          f"miter -equiv -flatten -make_assert -make_outputs gold gate miter; hierarchy -top miter; opt_clean; "
          f"sat -prove-asserts -show-inputs -show-outputs miter", lf, check=False)
    return {"port": port, "log": lf}


def categorize(out):
    m = json.load(open(f"{out}/cut/gold_cut.json"))["modules"]["gold"]
    cats = {}
    for p, v in m["ports"].items():
        if v["direction"] != "output":
            continue
        k = p.split(":", 1)[0] if ":" in p else "port"
        if k == "I":
            inst = p[2:].rsplit(".", 1)[0]
            k = "I:" + ("latch" if inst.endswith(".l") else "icg" if inst.endswith(".cg") else "sram")
        cats.setdefault(k, 0)
        cats[k] += len(v["bits"])
    return cats


MUTANTS = [
    ("hold-fix delay cell on a data path made an inverter",
     r"(sg13cmos5l_)dlygate4sd3_1(\s+hold\d+\s*\(\.A\([^)]*\)\s*,\s*\.)X\(", r"\1inv_1\2Y("),
    ("clock-tree buffer made an inverter",
     r"(sg13cmos5l_)buf_(\d+)(\s+clkbuf_\S+\s*\(\.A\([^)]*\)\s*,\s*\.)X\(", r"\1inv_\2\3Y("),
]


def mutate_text(text, kind):
    """Return (mutated text, description) or None."""
    if kind < len(MUTANTS):
        desc, pat, rep = MUTANTS[kind]
        new, n = re.subn(pat, rep, text, count=1)
        return (new, desc) if n else None
    if kind == len(MUTANTS):        # swap the D nets of two flops
        ms = list(re.finditer(r"sg13cmos5l_dfrbpq_1 (\S+) \((?:[^;]*?)\.D\(([^)]+)\)", text))
        a, b = ms[100], ms[900]
        t = text[:a.start(2)] + b.group(2) + text[a.end(2):b.start(2)] + a.group(2) + text[b.end(2):]
        return t, f"D nets of flops {a.group(1)} and {b.group(1)} swapped"
    if kind == len(MUTANTS) + 1:    # swap the GATE nets of two clock gates
        ms = list(re.finditer(r"sg13cmos5l_lgcp_1 (\S+)\s+\(\.GATE\(([^)]+)\)", text))
        a, b = ms[3], ms[40]
        t = text[:a.start(2)] + b.group(2) + text[a.end(2):b.start(2)] + a.group(2) + text[b.end(2):]
        return t, f"GATE nets of clock gates {a.group(1)} and {b.group(1)} swapped"
    if kind == len(MUTANTS) + 2:    # swap two address bits of the first SRAM
        m = re.search(r"\.A_ADDR\(\{([^}]*)\}\)", text)
        bits = [x.strip() for x in m.group(1).split(",")]
        bits[1], bits[6] = bits[6], bits[1]
        t = text[:m.start(1)] + ",\n    ".join(bits) + text[m.end(1):]
        return t, "two address bits of an SRAM macro swapped"
    return None


@timed("mutants")
def mutants(inp, out):
    res = []
    text = open(inp["final_netlist"]).read()
    for k in range(len(MUTANTS) + 3):
        r = mutate_text(text, k)
        if not r:
            res.append({"mutant": k, "status": "not applicable"})
            continue
        mt, desc = r
        d = os.path.join(out, "mutants", f"m{k}")
        os.makedirs(d, exist_ok=True)
        nl = os.path.join(d, "netlist.v")
        open(nl, "w").write(mt)
        _gate(inp, out, netlist=nl, tag=f"mutants/m{k}/gate")
        cut(inp, out, f"{d}/gate_raw.json", d, pairs=f"{out}/pairs.json")
        st = abc_check(d, "mut")
        res.append({"mutant": k, "description": desc, "abc": st, "caught": st == "not equivalent"})
        log(f"  mutant {k}: {desc}: {st}")
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run")
    ap.add_argument("out")
    ap.add_argument("-j", "--jobs", type=int, default=4)
    ap.add_argument("--skip-simple", action="store_true")
    ap.add_argument("--mutants", action="store_true")
    a = ap.parse_args()
    if os.path.exists(a.out) and os.listdir(a.out):
        sys.exit(f"equiv: {a.out} exists and is not empty")
    os.makedirs(a.out, exist_ok=True)
    out = os.path.realpath(a.out)
    for tool in ("yosys", "yosys-abc"):
        if not shutil.which(tool):
            sys.exit(f"equiv: {tool} not on PATH")
    ver = subprocess.run(["yosys", "-V"], capture_output=True, text=True).stdout.strip()
    t0 = time.time()
    inp = resolve(a.run, out)
    cres = cells(inp, out)
    params = gold(inp, out)
    gate(inp, out)
    synth(inp, out)
    m = match(inp, out)
    abc_res = abc(out)
    log(f"ABC: {abc_res}")
    sres = None if a.skip_simple else simple(out, a.jobs)
    if sres:
        log(f"equiv_simple: {sres['proven']}/{sres['equiv_cells']} proven, {len(sres['unproven'])} unproven")
    mres = mutants(inp, out) if a.mutants else None
    pairs = json.load(open(f"{out}/pairs.json"))
    unobs = [e for e in m["unmatched_gold_ff"] if e["type"] != "fsm-encoded bit"]
    res = {
        "yosys": ver, "rtl_parameters": [f"{k}={v}" for k, v in params], "inputs": {k: inp[k] for k in ("run", "design", "librelane", "final_netlist")},
        "netlist_sha256": inp["sha256"][inp["final_netlist"]],
        "cells": {"used": len(cres["used_cells"]), "combinational_checked_vs_liberty": len(cres["combinational_checked"]),
                  "sequential": cres["sequential"]},
        "match": m["summary"], "fsms": m["fsms"],
        "pairs_by_simulation": pairs["pairs"],
        "gold_only_flops": unobs,
        "fsm_bits_removed_by_synthesis": [e["cell"] for e in m["unmatched_gold_ff"] if e["type"] == "fsm-encoded bit"],
        "unmatched_gate_flops": m["unmatched_gate_ff"], "unmatched_gold_instances": m["unmatched_gold_bb"],
        "unmatched_gate_instances": m["unmatched_gate_bb"], "polarity_conflicts": m["polarity_conflicts"],
        "compared_points": categorize(out),
        "abc": abc_res, "equiv_simple": sres, "mutants": mres,
        "times_s": dict(TIMES, total=round(time.time() - t0, 1)),
    }
    ok = (abc_res == "equivalent" and (sres is None or (not sres["unproven"] and not sres["failed_shards"]
                                                          and sres["proven"] == sres["equiv_cells"]))
          and not m["unmatched_gate_ff"] and not m["unmatched_gold_bb"] and not m["unmatched_gate_bb"]
          and not m["polarity_conflicts"] and not m["clock_reset_mismatch_in_fsm"]
          and (mres is None or all(x.get("caught") for x in mres if x.get("status") != "not applicable")))
    res["verdict"] = "EQUIVALENT" if ok else "NOT PROVEN"
    json.dump(res, open(f"{out}/result.json", "w"), indent=1)
    s = res["match"]
    print(f"""
==== equivalence: {inp['design']} ({inp['run']})
netlist sha256      {res['netlist_sha256']}
checker             {ver}; LibreLane {inp['librelane']}
cells               {res['cells']['used']} types used; {res['cells']['combinational_checked_vs_liberty']} combinational models = Liberty functions
rtl parameters      {', '.join(res['rtl_parameters'])}
state elements      flops gold {s['gold_ff']} / gate {s['gate_ff']}: matched {s['matched_ff']} {s['match_by']} ({s['inverted_ff']} inverted)
                    instances (latches, clock gates, SRAMs) {s['matched_bb']}/{s['gold_bb']} by name; FSMs re-encoded: {s['fsm_registers']}
gold-only flops     {len(unobs)} (proven unobservable): {', '.join(e['names'][0] for e in unobs)}
compared points     {res['compared_points']}
ABC &cec            {abc_res} (RTL x values free)
equiv_simple        {'skipped' if not sres else f"{sres['proven']}/{sres['equiv_cells']} proven, unproven: {sres['unproven'][:20]}"}
mutants             {'not run' if mres is None else ', '.join(f"m{x['mutant']}:{'caught' if x.get('caught') else x.get('abc', x.get('status'))}" for x in mres)}
time                {res['times_s']}
VERDICT             {res['verdict']}
""")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
