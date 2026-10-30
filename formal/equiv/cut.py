#!/usr/bin/env python3
"""Match the state elements of the RTL (gold) and the netlist (gate) and cut
both designs at them, giving two combinational modules with identical ports.

Inputs (yosys JSON, written by equiv_check.sh):
  gold_raw.json   RTL after proc/flatten/dffunmap/simplemap: flops are
                  $_DFF_<clk><rst><val>_ cells; latches, clock gates and the
                  SRAMs are library/macro instances (the RTL instantiates them)
  gate_raw.json   final netlist, cells replaced by the PDK functional models
                  (flatten), state-holding cells and SRAMs kept as instances
  synth_raw.json  the synthesis netlist (06-yosys-synthesis), cells as
                  instances: used only to name a flop whose output net the
                  P&R tools renamed (instance names survive P&R)
  synthesis log   for FSM re-encodings (yosys fsm_recode)
  pairs.json      optional: flop pairs proposed by pair.py (by simulation)
                  for flops whose names synthesis dropped

Correspondence (the state map; all of it is checked, none of it assumed):
  flops      by name: the gate flop's output net (or, failing that, the net
             its synthesis-netlist instance drives, directly or through one
             inverter) carries the name of the gold register bit; else a
             pair from pairs.json.
  instances  latches (dlhq), clock gates (lgcp) and SRAM macros: by instance
             name, which the RTL fixes (it instantiates the cells).
  FSMs       a register that synthesis re-encoded (fsm_recode, logged) maps
             to its new state flops through the logged code table: gate
             flop i holds ENC_i(gold state) XOR pol.

Normalisation. Every gate flop is a dfrbpq: rising-edge CLK, active-low
RESET_B to 0. A gold flop paired with polarity p has gate state = gold state
XOR p, and its D is compared XOR p; p must equal its reset value. A gold
flop's clock is compared as the rising-edge clock (inverted for a
falling-edge flop), its reset as active-low (inverted for active-high,
constant 1 when it has none).

The cut modules (gold, gate) have
  inputs   S:<key>               flop state (gate polarity)
           O:<inst>.<port>       black-box outputs (latch Q, GCLK, SRAM DOUT)
           U:<name> / V:<cell>   state of an unmatched gold / gate flop:
                                 present in both modules, used by one only
  outputs  D:<key> C:<key> R:<key>   next state, clock, reset of a flop pair
           I:<inst>.<port>       black-box inputs
           the design's own ports
With --pairing, the modules also export the unmatched flops' pins (UD/UC/UR/UQ
in gold, VD/VC/VR in gate) for pair.py; those modules are not for proving.

SPDX-License-Identifier: Apache-2.0
"""
import argparse
import json
import re
import sys
from collections import defaultdict

TOP = "tt_um_josusanmartin_protoemu"
GATE_FF = {"sg13cmos5l_dfrbpq_1", "sg13cmos5l_dfrbpq_2"}
BB_TYPES = ("sg13cmos5l_dlhq_1", "sg13cmos5l_lgcp_1", "RM_IHPSG13_1P_256x32_c2_bm_bist")
INV_TYPES = re.compile(r"^sg13cmos5l_inv_\d+$")
DFF_RE = re.compile(r"^\$_DFF_([PN])(?:([PN])([01]))?_$")


def load(path, top):
    return json.load(open(path))["modules"][top]


def bit_names(mod):
    """bit id -> set of names ('wire[idx]' and, for 1-bit wires, 'wire')."""
    names = defaultdict(set)
    for name, n in mod["netnames"].items():
        bits = n["bits"]
        off = n.get("offset", 0)
        upto = n.get("upto", 0)
        for i, b in enumerate(bits):
            if isinstance(b, str):
                continue
            idx = off + (len(bits) - 1 - i if upto else i)
            names[b].add(f"{name}[{idx}]")
            if len(bits) == 1:
                names[b].add(name)
    return names


def readers(mod):
    rd = defaultdict(list)
    for cname, c in mod["cells"].items():
        for p, bits in c["connections"].items():
            if c["port_directions"].get(p) == "input":
                for b in bits:
                    if not isinstance(b, str):
                        rd[b].append((cname, p))
    return rd


def parse_fsms(log_text):
    """FSM re-encodings from a yosys synthesis log.

    Returns [{reg, width, reset, table: {old_code_int: new_code_str}}];
    codes are printed MSB first, new codes may contain '-' (one-hot: 0)."""
    fsms = []
    for m in re.finditer(r"Extracting FSM `\\(\S+)' from module.*?found reset state: (\d+)'([01]+)", log_text, re.S):
        reg, width, reset = m.group(1), int(m.group(2)), int(m.group(3), 2)
        rec = re.search(r"Recoding FSM `\$fsm\$\\" + re.escape(reg) + r"\$\d+'.*?\n((?:\s+mapping[^\n]*\n)?)((?:\s+[01]+ -> [01-]+\n)+)",
                        log_text[m.end():], re.S)
        if not rec:
            sys.exit(f"cut.py: FSM {reg} extracted but no recoding table found in the log")
        table = {}
        for old, new in re.findall(r"([01]+) -> ([01-]+)", rec.group(2)):
            table[int(old, 2)] = new
        fsms.append({"reg": reg, "width": width, "reset": reset, "table": table})
    return fsms


class Cutter:
    """Builds a cut module from a raw module."""

    def __init__(self, mod):
        self.next_bit = 1 + max([b for n in mod["netnames"].values() for b in n["bits"] if not isinstance(b, str)]
                                + [b for c in mod["cells"].values() for bits in c["connections"].values()
                                   for b in bits if not isinstance(b, str)])
        self.ports = dict(mod["ports"])
        self.cells = dict(mod["cells"])
        self.extra = 0

    def new_bit(self):
        self.next_bit += 1
        return self.next_bit

    def cell(self, typ, conns, y=None):
        y = self.new_bit() if y is None else y
        self.extra += 1
        dirs = {p: "input" for p in conns}
        dirs["Y"] = "output"
        conns = {p: [b] for p, b in conns.items()}
        conns["Y"] = [y]
        self.cells[f"$eqcut${self.extra}"] = {"hide_name": 1, "type": typ, "parameters": {}, "attributes": {},
                                              "port_directions": dirs, "connections": conns}
        return y

    def inv(self, a, y=None):
        if isinstance(a, str) and y is None:
            return {"0": "1", "1": "0"}.get(a, a)
        return self.cell("$_NOT_", {"A": a}, y)

    def xor_const(self, a, p):
        return self.inv(a) if p else a

    def and_all(self, bits):
        acc = "1"
        for b in bits:
            acc = b if acc == "1" else self.cell("$_AND_", {"A": acc, "B": b})
        return acc

    def or_all(self, bits):
        acc = "0"
        for b in bits:
            acc = b if acc == "0" else self.cell("$_OR_", {"A": acc, "B": b})
        return acc

    def equals(self, bits, value):
        """bits (LSB first) == value"""
        return self.and_all([b if (value >> i) & 1 else self.inv(b) for i, b in enumerate(bits)])

    def add_port(self, name, direction, bits):
        if name in self.ports:
            sys.exit(f"cut.py: duplicate port {name}")
        self.ports[name] = {"direction": direction, "bits": list(bits)}

    def module(self):
        # every output bit gets its own net (a buffer when it is a constant,
        # an input bit or another output's bit), so no port aliases another
        seen = {b for v in self.ports.values() if v["direction"] == "input" for b in v["bits"]}
        for p, v in self.ports.items():
            if v["direction"] != "output":
                continue
            bits = []
            for b in v["bits"]:
                if isinstance(b, str) or b in seen:
                    b = self.cell("$_BUF_", {"A": b})
                seen.add(b)
                bits.append(b)
            v["bits"] = bits
        # only port names survive: equiv_make must match ports and nothing else
        netnames = {p: {"hide_name": 0, "bits": v["bits"], "attributes": {}} for p, v in self.ports.items()}
        return {"attributes": {}, "ports": self.ports, "cells": self.cells, "netnames": netnames}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", required=True)
    ap.add_argument("--gate", required=True)
    ap.add_argument("--synth", required=True)
    ap.add_argument("--synth-log", required=True)
    ap.add_argument("--pairs")
    ap.add_argument("--pairing", action="store_true", help="export unmatched flop pins for pair.py")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    gold, gate, synth = load(a.gold, TOP), load(a.gate, TOP), load(a.synth, TOP)
    fsms = parse_fsms(open(a.synth_log, errors="replace").read())
    report = {"fsms": fsms, "unmatched_gold_ff": [], "unmatched_gate_ff": [], "unmatched_gold_bb": [],
              "unmatched_gate_bb": [], "polarity_conflicts": [], "clock_reset_mismatch_in_fsm": []}

    # ---- gold flops, indexed by every name of their Q bit
    gnames = bit_names(gold)
    gold_ff = {}            # cell -> (clk_pol, rst_pol or None, rst_val or None)
    gold_by_name = defaultdict(set)
    for cname, c in gold["cells"].items():
        t = c["type"]
        if t.startswith("$") and not t.startswith(("$_AND", "$_OR", "$_NOT", "$_XOR", "$_MUX")) and \
                re.search(r"dff|DFF|latch|LATCH|mem|MEM|ff\b|FF_", t):
            m = DFF_RE.match(t)
            if not m:
                sys.exit(f"cut.py: unsupported gold state cell {cname} of type {t}")
            gold_ff[cname] = (m.group(1), m.group(2), m.group(3))
            for n in gnames.get(c["connections"]["Q"][0], ()):
                gold_by_name[n].add(cname)

    def gold_name(g):
        names = gnames.get(gold["cells"][g]["connections"]["Q"][0], ())
        names = sorted(names, key=lambda n: (len(n), n))
        return names[0] if names else g

    # FSM registers: their gold flops and the gate flops named after them
    # are excluded from name matching
    fsm_gold = {}           # reg -> [gold cell per bit, LSB first]
    fsm_names = set()
    for f in fsms:
        bits = []
        for i in range(f["width"]):
            hits = gold_by_name.get(f"{f['reg']}[{i}]", set()) if f["width"] > 1 else gold_by_name.get(f["reg"], set())
            if len(hits) != 1:
                sys.exit(f"cut.py: FSM {f['reg']} bit {i}: {len(hits)} gold flops")
            bits.append(next(iter(hits)))
        fsm_gold[f["reg"]] = bits
        n_new = len(next(iter(f["table"].values())))
        fsm_names |= {f"{f['reg']}[{i}]" for i in range(max(n_new, f["width"]))}
    fsm_gold_cells = {g for bits in fsm_gold.values() for g in bits}

    def lookup(names):
        hits = set()
        for n in names:
            if n not in fsm_names:
                hits |= gold_by_name.get(n, set())
        return hits - fsm_gold_cells

    # ---- gate flops: name of the Q net, else the synthesis netlist's
    gtnames = bit_names(gate)
    snames = bit_names(synth)
    s_rd = readers(synth)
    match = {}              # gold key (cell or fsm:<reg>#i) -> (gate cell, pol, how)
    gate_ff = [c for c, v in gate["cells"].items() if v["type"] in GATE_FF]
    other = [(c, v["type"]) for c, v in gate["cells"].items()
             if not v["type"].startswith("$") and v["type"] not in GATE_FF and v["type"] not in BB_TYPES]
    if other:
        sys.exit(f"cut.py: unsupported gate cells: {other[:10]}")
    gate_unmatched = []
    for gc in gate_ff:
        q = gate["cells"][gc]["connections"]["Q"][0]
        hits, pol, how = lookup(gtnames.get(q, ())), 0, "final-net"
        if not hits and gc in synth["cells"]:
            sq = synth["cells"][gc]["connections"]["Q"][0]
            hits, how = lookup(snames.get(sq, ())), "synth-net"
            if not hits:
                for rc, rp in s_rd.get(sq, ()):
                    if INV_TYPES.match(synth["cells"][rc]["type"]):
                        h = lookup(snames.get(synth["cells"][rc]["connections"]["Y"][0], ()))
                        if h:
                            hits, pol, how = h, 1, "synth-net-via-inverter"
                            break
        if len(hits) != 1 or next(iter(hits)) in match:
            gate_unmatched.append(gc)
            continue
        match[hits.pop()] = (gc, pol, how)

    # FSM state flops: synthesis names the re-encoded state register after the
    # original one, so a gate flop whose output is <reg>[i] (directly, or
    # through one inverter in the synthesis netlist) holds encoded bit i
    fsm_bitname = {}
    for f in fsms:
        n_new = len(next(iter(f["table"].values())))
        for i in range(n_new):
            fsm_bitname[f"{f['reg']}[{i}]"] = f"fsm:{f['reg']}#{i}"
    for gc in list(gate_unmatched):
        q = gate["cells"][gc]["connections"]["Q"][0]
        found = [(fsm_bitname[n], 0) for n in gtnames.get(q, ()) if n in fsm_bitname]
        if not found and gc in synth["cells"]:
            sq = synth["cells"][gc]["connections"]["Q"][0]
            found = [(fsm_bitname[n], 0) for n in snames.get(sq, ()) if n in fsm_bitname]
            for rc, rp in s_rd.get(sq, ()):
                if INV_TYPES.match(synth["cells"][rc]["type"]):
                    found += [(fsm_bitname[n], 1) for n in snames.get(synth["cells"][rc]["connections"]["Y"][0], ())
                              if n in fsm_bitname]
        if len(found) == 1 and found[0][0] not in match:
            match[found[0][0]] = (gc, found[0][1], "fsm-name")
            gate_unmatched.remove(gc)

    if a.pairs:
        for p in json.load(open(a.pairs))["pairs"]:
            if p["gate"] not in gate_unmatched or p["gold"] in match:
                sys.exit(f"cut.py: pair {p} conflicts with a name match")
            match[p["gold"]] = (p["gate"], p["pol"], "simulation-pair")
            gate_unmatched.remove(p["gate"])

    # FSM virtual flops: one per bit of the new encoding
    fsm_virtual = {}        # key -> (fsm, bit index)
    for f in fsms:
        n_new = len(next(iter(f["table"].values())))
        for i in range(n_new):
            fsm_virtual[f"fsm:{f['reg']}#{i}"] = (f, i)

    for g in list(gold_ff) + list(fsm_virtual):
        if g not in match and g not in fsm_gold_cells:
            if g in fsm_virtual:
                report["unmatched_gold_ff"].append({"cell": g, "type": "fsm-encoded bit", "names": [g]})
            else:
                report["unmatched_gold_ff"].append({"cell": g, "type": gold["cells"][g]["type"],
                                                    "names": [gold_name(g)],
                                                    "src": gold["cells"][g]["attributes"].get("src", "")})
    for gc in gate_unmatched:
        q = gate["cells"][gc]["connections"]["Q"][0]
        report["unmatched_gate_ff"].append({"cell": gc, "names": sorted(
            n for n in gtnames.get(q, ()) if not re.match(r"^_\d+_\.|^(hold|fanout|wire|max_cap|clkbuf)\d+\.", n))})

    # ---- black-box instances by name
    gold_bb = {c: v for c, v in gold["cells"].items() if v["type"] in BB_TYPES}
    gate_bb = {c: v for c, v in gate["cells"].items() if v["type"] in BB_TYPES}
    report["unmatched_gold_bb"] = sorted(c for c in gold_bb if c not in gate_bb or gate_bb[c]["type"] != gold_bb[c]["type"])
    report["unmatched_gate_bb"] = sorted(c for c in gate_bb if c not in gold_bb)

    # ---- build the cut modules
    gcut, ncut = Cutter(gold), Cutter(gate)

    def norm_c(cut, conn, cpol):
        return conn["C"][0] if cpol == "P" else cut.inv(conn["C"][0])

    def norm_r(cut, conn, rpol):
        if rpol is None:
            return "1"
        return conn["R"][0] if rpol == "N" else cut.inv(conn["R"][0])

    # FSM registers: gold state bits become shared inputs S:<reg>[j]; the gate
    # flops paired with the encoded bits read ENC_i(S) XOR pol
    fsm_in = {}             # reg -> (gold bits, gate bits) of the shared state inputs
    fsm_d = {}              # reg -> gold D bits
    for f in fsms:
        reg, cells = f["reg"], fsm_gold[f["reg"]]
        c0 = gold["cells"][cells[0]]
        for g in cells:
            cg = gold["cells"][g]
            if gold_ff[g] != gold_ff[cells[0]] or cg["connections"]["C"] != c0["connections"]["C"] or \
                    cg["connections"].get("R") != c0["connections"].get("R"):
                report["clock_reset_mismatch_in_fsm"].append(g)
        gbits, nbits = [], []
        for j, g in enumerate(cells):
            name = f"S:{reg}[{j}]"
            q = gold["cells"][g]["connections"]["Q"][0]
            del gcut.cells[g]
            gcut.add_port(name, "input", [q])
            nb = ncut.new_bit()
            ncut.add_port(name, "input", [nb])
            gbits.append(q)
            nbits.append(nb)
        fsm_in[reg] = (gbits, nbits)
        fsm_d[reg] = [gold["cells"][g]["connections"]["D"][0] for g in cells]

    def enc(cut, f, i, state_bits):
        """bit i of the new encoding of the gold state (0 for unlisted codes)"""
        return cut.or_all([cut.equals(state_bits, old) for old, new in sorted(f["table"].items()) if new[-1 - i] == "1"])

    keys = {}
    for g, (gc, pol, how) in sorted(match.items(), key=lambda kv: kv[1][0]):
        nconn = gate["cells"][gc]["connections"]
        if g in fsm_virtual:
            f, i = fsm_virtual[g]
            key = g
            cells = fsm_gold[f["reg"]]
            cpol, rpol, rval = gold_ff[cells[0]]
            reset_enc = int(any(new[-1 - i] == "1" for old, new in f["table"].items() if old == f["reset"]))
            if reset_enc != pol:
                report["polarity_conflicts"].append({"gold": g, "gate": gc, "encoded_reset": reset_enc, "pol": pol})
            conn = gold["cells"][cells[0]]["connections"]
            gcut.add_port("D:" + key, "output", [gcut.xor_const(enc(gcut, f, i, fsm_d[f["reg"]]), pol)])
            gcut.add_port("C:" + key, "output", [norm_c(gcut, conn, cpol)])
            gcut.add_port("R:" + key, "output", [norm_r(gcut, conn, rpol)])
            del ncut.cells[gc]
            v = enc(ncut, f, i, fsm_in[f["reg"]][1])
            if pol:
                ncut.inv(v, y=nconn["Q"][0])
            else:
                ncut.cell("$_BUF_", {"A": v}, y=nconn["Q"][0])
        else:
            cpol, rpol, rval = gold_ff[g]
            if rval is not None and int(rval) != pol:
                report["polarity_conflicts"].append({"gold": g, "gate": gc, "reset_value": rval, "pol": pol, "how": how})
            key = gold_name(g)
            conn = gold["cells"][g]["connections"]
            q = conn["Q"][0]
            del gcut.cells[g]
            if pol:
                s = gcut.new_bit()
                gcut.inv(s, y=q)
                gcut.add_port("S:" + key, "input", [s])
            else:
                gcut.add_port("S:" + key, "input", [q])
            gcut.add_port("D:" + key, "output", [gcut.xor_const(conn["D"][0], pol)])
            gcut.add_port("C:" + key, "output", [norm_c(gcut, conn, cpol)])
            gcut.add_port("R:" + key, "output", [norm_r(gcut, conn, rpol)])
            del ncut.cells[gc]
            ncut.add_port("S:" + key, "input", nconn["Q"])
        ncut.add_port("D:" + key, "output", nconn["D"])
        ncut.add_port("C:" + key, "output", nconn["CLK"])
        ncut.add_port("R:" + key, "output", nconn["RESET_B"])
        keys[key] = {"gold": g, "gate": gc, "pol": pol, "how": how}

    for c in sorted(set(gold_bb) & set(gate_bb)):
        for cut, cell in ((gcut, gold_bb[c]), (ncut, gate_bb[c])):
            del cut.cells[c]
            for p, bits in sorted(cell["connections"].items()):
                out = cell["port_directions"][p] == "output"
                cut.add_port(("O:" if out else "I:") + c + "." + p, "input" if out else "output", bits)

    # Unmatched flops: their outputs become free inputs that exist in both
    # modules but drive logic in one only. Everything they influence then
    # fails to prove unless it does not depend on them: this is how an
    # unobservable gold register (one synthesis removed) is shown to be
    # unobservable. Their own D/C/R are not compared.
    for e in report["unmatched_gold_ff"]:
        g = e["cell"]
        if g in fsm_virtual:
            f, i = fsm_virtual[g]
            if a.pairing:
                conn = gold["cells"][fsm_gold[f["reg"]][0]]["connections"]
                cpol, rpol, _ = gold_ff[fsm_gold[f["reg"]][0]]
                gcut.add_port("UQ:" + g, "output", [enc(gcut, f, i, fsm_in[f["reg"]][0])])
                gcut.add_port("UD:" + g, "output", [enc(gcut, f, i, fsm_d[f["reg"]])])
                gcut.add_port("UC:" + g, "output", [norm_c(gcut, conn, cpol)])
                gcut.add_port("UR:" + g, "output", [norm_r(gcut, conn, rpol)])
            continue
        conn = gold["cells"][g]["connections"]
        cpol, rpol, _ = gold_ff[g]
        del gcut.cells[g]
        name = e["names"][0]
        gcut.add_port("U:" + name, "input", conn["Q"])
        ncut.add_port("U:" + name, "input", [ncut.new_bit()])
        if a.pairing:
            gcut.add_port("UQ:" + g, "output", conn["Q"])
            gcut.add_port("UD:" + g, "output", conn["D"])
            gcut.add_port("UC:" + g, "output", [norm_c(gcut, conn, cpol)])
            gcut.add_port("UR:" + g, "output", [norm_r(gcut, conn, rpol)])
    for e in report["unmatched_gate_ff"]:
        f = e["cell"]
        conn = gate["cells"][f]["connections"]
        del ncut.cells[f]
        ncut.add_port("V:" + f, "input", conn["Q"])
        gcut.add_port("V:" + f, "input", [gcut.new_bit()])
        if a.pairing:
            ncut.add_port("VD:" + f, "output", conn["D"])
            ncut.add_port("VC:" + f, "output", conn["CLK"])
            ncut.add_port("VR:" + f, "output", conn["RESET_B"])

    if a.pairing:
        # names the unmatched gate flops' outputs carry in the netlists, and
        # the gold signals of the same names, for pair.py's alias pairing
        aliases = {}
        gold_bits_by_name = {}
        for b, ns in gnames.items():
            for n in ns:
                gold_bits_by_name[n] = b
        for gc in gate_unmatched:
            cand = []
            q = gate["cells"][gc]["connections"]["Q"][0]
            cand += [(n, 0) for n in gtnames.get(q, ())]
            if gc in synth["cells"]:
                sq = synth["cells"][gc]["connections"]["Q"][0]
                cand += [(n, 0) for n in snames.get(sq, ())]
                for rc, rp in s_rd.get(sq, ()):
                    if INV_TYPES.match(synth["cells"][rc]["type"]):
                        cand += [(n, 1) for n in snames.get(synth["cells"][rc]["connections"]["Y"][0], ())]
            cand = sorted({(n, p) for n, p in cand if n in gold_bits_by_name and n not in fsm_names})
            for n, p in cand:
                if "UN:" + n not in gcut.ports:
                    gcut.add_port("UN:" + n, "output", [gold_bits_by_name[n]])
            if cand:
                aliases[gc] = cand
        json.dump(aliases, open(f"{a.out}/aliases.json", "w"), indent=1)

    json.dump({"creator": "cut.py", "modules": {"gold": gcut.module()}}, open(f"{a.out}/gold_cut.json", "w"))
    json.dump({"creator": "cut.py", "modules": {"gate": ncut.module()}}, open(f"{a.out}/gate_cut.json", "w"))
    hows = defaultdict(int)
    for v in keys.values():
        hows[v["how"]] += 1
    report["summary"] = {"gold_ff": len(gold_ff), "gate_ff": len(gate_ff), "matched_ff": len(match),
                         "match_by": dict(hows), "inverted_ff": sum(v["pol"] for v in keys.values()),
                         "fsm_registers": [f["reg"] for f in fsms],
                         "gold_bb": len(gold_bb), "gate_bb": len(gate_bb),
                         "matched_bb": len(set(gold_bb) & set(gate_bb))}
    report["flops"] = keys
    json.dump(report, open(f"{a.out}/match.json", "w"), indent=1)
    s = report["summary"]
    print(f"cut.py: flops gold {s['gold_ff']} gate {s['gate_ff']} matched {s['matched_ff']} "
          f"(by {s['match_by']}, {s['inverted_ff']} inverted); FSMs {s['fsm_registers']}; "
          f"instances gold {s['gold_bb']} gate {s['gate_bb']} matched {s['matched_bb']}")
    for k in ("unmatched_gold_ff", "unmatched_gate_ff", "unmatched_gold_bb", "unmatched_gate_bb",
              "polarity_conflicts", "clock_reset_mismatch_in_fsm"):
        if report[k]:
            print(f"cut.py: {k}: {len(report[k])}")
            for e in report[k][:50]:
                print("   ", e)


if __name__ == "__main__":
    main()
