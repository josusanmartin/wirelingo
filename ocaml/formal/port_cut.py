#!/usr/bin/env python3
"""Match the state elements of src/ (gold) and the generated Verilog (gate) and
cut both at them, giving two combinational modules with identical ports. The
cut modules have the shape formal/equiv/cut.py gives them, so formal/equiv's ABC
and equiv_simple steps run on them unchanged.

Inputs (yosys JSON, after proc, flatten, dffunmap, simplemap of flops):
  gold.json  src/ as the flow reads it
  gate.json  ocaml/ generated Verilog, read the same way

Correspondence (all of it is checked, none of it assumed):
  flops      by name: the gate's flop-output net, translated by gate_to_gold(),
             carries the name of the gold register bit. Clock edge, reset edge
             and reset value must be identical.
  instances  latches (dlhq), clock gates (lgcp), SRAM macros: by instance name,
             translated the same way. Both sides read the same src/pe_cg.sv.

The module's outputs are compared with S:/O: inputs shared:
  D:<key> C:<key> R:<key>   next state, clock, reset of a flop pair
  I:<inst>.<port>           black-box inputs
  the design's own ports
Reset is compared in active-low form (constant 1 when a flop has none).

SPDX-License-Identifier: Apache-2.0
"""
import argparse
import json
import os
import re
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "formal", "equiv"))
from cut import Cutter, DFF_RE, bit_names, load  # noqa: E402  (formal/equiv/cut.py, unmodified)

TOP = "tt_um_josusanmartin_protoemu"
BB_TYPES = ("sg13cmos5l_dlhq_1", "sg13cmos5l_lgcp_1", "RM_IHPSG13_1P_256x32_c2_bm_bist")

# Generated Verilog has no generate blocks, so ocaml/rtl names instances
# g_ent_3_u_cg where the Verilog has g_ent[3].u_cg
RENAMES = [
    (re.compile(r"\bu_core_(\d)\b"), r"g_core[\1].g_on.u_core"),
    (re.compile(r"\bg_cfg_cg_(\d)_u_cg\b"), r"g_cfg_cg[\1].u_cg"),
    (re.compile(r"\bg_gcfg_cg_(\d)_u_cg\b"), r"g_gcfg_cg[\1].u_cg"),
    (re.compile(r"\bg_ent_(\d)_u_cg\b"), r"g_ent[\1].u_cg"),
    (re.compile(r"\bg_ent_(\d)_u_lat\b"), r"g_ent[\1].u_lat"),
    (re.compile(r"\bg_bank_(\d)_u_bank\b"), r"g_bank[\1].g_on.u_bank"),
    (re.compile(r"\bu_host\.bus_(addr|we|wdata|snap|commit|cancel)_r\b"), r"u_host.bus_\1"),
]


def gate_to_gold(name):
    for pat, rep in RENAMES:
        name = pat.sub(rep, name)
    return name


def flops(mod):
    """cell -> (clk pol, rst pol or None, rst value or None); rejects other state."""
    out = {}
    for cname, c in mod["cells"].items():
        t = c["type"]
        if t.startswith("$") and re.search(r"dff|DFF|latch|LATCH|mem|MEM|ff\b|FF_", t):
            m = DFF_RE.match(t)
            if not m:
                sys.exit(f"port_cut: unsupported state cell {cname} of type {t}")
            out[cname] = (m.group(1), m.group(2), m.group(3))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", required=True)
    ap.add_argument("--gate", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    gold, gate = load(a.gold, TOP), load(a.gate, TOP)
    gold_ff, gate_ff = flops(gold), flops(gate)
    gnames, tnames = bit_names(gold), bit_names(gate)

    by_name = defaultdict(set)
    for g in gold_ff:
        for n in gnames.get(gold["cells"][g]["connections"]["Q"][0], ()):
            by_name[n].add(g)

    report = {"unmatched_gold_ff": [], "unmatched_gate_ff": [], "type_mismatch": [],
              "unmatched_gold_bb": [], "unmatched_gate_bb": []}
    match = {}
    for t in gate_ff:
        q = gate["cells"][t]["connections"]["Q"][0]
        hits = set()
        for n in tnames.get(q, ()):
            hits |= by_name.get(gate_to_gold(n), set())
        if len(hits) != 1 or next(iter(hits)) in match:
            report["unmatched_gate_ff"].append({"cell": t, "names": sorted(tnames.get(q, ()))[:6],
                                                "hits": sorted(hits)[:6]})
            continue
        match[hits.pop()] = t
    for g in gold_ff:
        if g not in match:
            report["unmatched_gold_ff"].append(
                {"cell": g, "names": sorted(gnames.get(gold["cells"][g]["connections"]["Q"][0], ()))[:6]})

    gcut, ncut = Cutter(gold), Cutter(gate)

    def key_of(g):
        names = gnames.get(gold["cells"][g]["connections"]["Q"][0], ())
        names = sorted(names, key=lambda n: (len(n), n))
        return names[0] if names else g

    def norm_r(cut, conn, rpol):
        if rpol is None:
            return "1"
        return conn["R"][0] if rpol == "N" else cut.inv(conn["R"][0])

    keys = {}
    for g, t in sorted(match.items(), key=lambda kv: kv[1]):
        if gold_ff[g] != gate_ff[t]:
            report["type_mismatch"].append({"gold": g, "gate": t, "gold_type": gold["cells"][g]["type"],
                                            "gate_type": gate["cells"][t]["type"]})
        key = key_of(g)
        for cut, mod, cell, typ in ((gcut, gold, g, gold_ff[g]), (ncut, gate, t, gate_ff[t])):
            conn = mod["cells"][cell]["connections"]
            del cut.cells[cell]
            cut.add_port("S:" + key, "input", conn["Q"])
            cut.add_port("D:" + key, "output", conn["D"])
            c = conn["C"][0] if typ[0] == "P" else cut.inv(conn["C"][0])
            cut.add_port("C:" + key, "output", [c])
            cut.add_port("R:" + key, "output", [norm_r(cut, conn, typ[1])])
        keys[key] = {"gold": g, "gate": t}

    gold_bb = {c: v for c, v in gold["cells"].items() if v["type"] in BB_TYPES}
    gate_bb = {gate_to_gold(c): v for c, v in gate["cells"].items() if v["type"] in BB_TYPES}
    gate_bb_raw = {gate_to_gold(c): c for c, v in gate["cells"].items() if v["type"] in BB_TYPES}
    report["unmatched_gold_bb"] = sorted(c for c in gold_bb if c not in gate_bb or gate_bb[c]["type"] != gold_bb[c]["type"])
    report["unmatched_gate_bb"] = sorted(c for c in gate_bb if c not in gold_bb)
    other = [c for c, v in list(gold["cells"].items()) + list(gate["cells"].items())
             if not v["type"].startswith("$") and v["type"] not in BB_TYPES]
    if other:
        sys.exit(f"port_cut: unexpected cells: {other[:10]}")
    for c in sorted(set(gold_bb) & set(gate_bb)):
        for cut, cell, raw in ((gcut, gold_bb[c], c), (ncut, gate_bb[c], gate_bb_raw[c])):
            del cut.cells[raw]
            for p, bits in sorted(cell["connections"].items()):
                out = cell["port_directions"][p] == "output"
                cut.add_port(("O:" if out else "I:") + c + "." + p, "input" if out else "output", bits)

    json.dump({"creator": "port_cut.py", "modules": {"gold": gcut.module()}}, open(f"{a.out}/gold_cut.json", "w"))
    json.dump({"creator": "port_cut.py", "modules": {"gate": ncut.module()}}, open(f"{a.out}/gate_cut.json", "w"))
    report["summary"] = {"gold_ff": len(gold_ff), "gate_ff": len(gate_ff), "matched_ff": len(match),
                         "gold_bb": len(gold_bb), "gate_bb": len(gate_bb),
                         "matched_bb": len(set(gold_bb) & set(gate_bb))}
    report["flops"] = keys
    json.dump(report, open(f"{a.out}/match.json", "w"), indent=1)
    s = report["summary"]
    print(f"port_cut: flops gold {s['gold_ff']} gate {s['gate_ff']} matched {s['matched_ff']}; "
          f"instances gold {s['gold_bb']} gate {s['gate_bb']} matched {s['matched_bb']}")
    for k in ("unmatched_gold_ff", "unmatched_gate_ff", "type_mismatch", "unmatched_gold_bb", "unmatched_gate_bb"):
        if report[k]:
            print(f"port_cut: {k}: {len(report[k])}")
            for e in report[k][:40]:
                print("   ", e)


if __name__ == "__main__":
    main()
