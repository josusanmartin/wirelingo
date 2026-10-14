#!/usr/bin/env python3
"""Build the yosys-readable cell library for the equivalence check.

Input: the PDK's Verilog functional models (sg13cmos5l_stdcell.v and its UDP
file sg13cmos5l_udp.v). Output:

  comb.v   every combinational cell model, as in the PDK (gate primitives),
           with the `specify` timing blocks removed and each combinational UDP
           (ihp_mux2, ihp_mux4, ...) replaced by an equivalent module that
           this script derives from the UDP's truth table;
  seq.v    blackbox stubs (ports only) for the state-holding cells: the check
           cuts every state element, so only their pin directions matter;
  cells.json  the cell inventory (which cells went where, and why).

A UDP is translated by enumerating all 0/1 input combinations and finding
the table rows that match ('?' and 'b' match 0 and 1; rows with 'x' in an
input column never match binary inputs). Every combination must match at
least one row and all matching rows must agree, else the script stops.

SPDX-License-Identifier: Apache-2.0
"""
import argparse
import itertools
import json
import re
import sys

SEQ_UDP = re.compile(r"\bihp_(dff|latch)\w*")
# tristate drivers and the bus holder: not supported by this check (the
# netlist must not contain them; hierarchy -check then fails if it does)
UNSUPPORTED = re.compile(r"\b(bufif[01]|notif[01])\b")


def strip_comments(text):
    # line comments first: the PDK's banner lines (//*****) contain "/*"
    text = re.sub(r"//[^\n]*", "", text)
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


def parse_udps(text):
    """Return {name: (ports_in_header_order, output, rows, sequential)}."""
    udps = {}
    for m in re.finditer(r"\bprimitive\s+(\w+)\s*\(([^)]*)\)\s*;(.*?)\bendprimitive", text, re.S):
        name, ports, body = m.group(1), [p.strip() for p in m.group(2).split(",")], m.group(3)
        out = re.search(r"\boutput\s+(?:reg\s+)?(\w+)", body).group(1)
        table = re.search(r"\btable\b(.*?)\bendtable", body, re.S).group(1)
        rows = []
        for line in table.split(";"):
            line = line.strip()
            if not line:
                continue
            fields = [f.split() for f in line.split(":")]
            rows.append([tok for f in fields for tok in f])
        seq = bool(re.search(r"\boutput\s+reg\b|\breg\s+" + out + r"\b", body)) or any(len(r) != len(ports) for r in rows)
        udps[name] = (ports, out, rows, seq)
    return udps


def udp_truth(name, ports, out, rows):
    if ports[0] != out:
        sys.exit(f"cells.py: UDP {name}: output is not the first port")
    ins = ports[1:]
    truth = {}
    for vals in itertools.product("01", repeat=len(ins)):
        hits = set()
        for r in rows:
            if all(c in ("?", "b") or c == v for c, v in zip(r[:-1], vals)):
                if r[-1] not in "01":
                    sys.exit(f"cells.py: UDP {name}: binary inputs {vals} give {r[-1]}")
                hits.add(r[-1])
        if len(hits) != 1:
            sys.exit(f"cells.py: UDP {name}: inputs {vals} match outputs {sorted(hits)}")
        truth[vals] = hits.pop()
    return ins, truth


def udp_module(name, ports, out, rows):
    ins, truth = udp_truth(name, ports, out, rows)
    terms = []
    for vals, z in truth.items():
        if z == "1":
            terms.append("(" + " & ".join(i if v == "1" else "~" + i for i, v in zip(ins, vals)) + ")")
    expr = " | ".join(terms) if terms else "1'b0"
    return (f"// translated from UDP {name} ({len(truth)} binary rows, {len(terms)} minterms)\n"
            f"module {name} (output {out}, input {', '.join(ins)});\n"
            f"  assign {out} = {expr};\nendmodule\n")


def split_modules(text):
    return [(m.group(1), m.group(0)) for m in
            re.finditer(r"\bmodule\s+(\w+)\b.*?\bendmodule\b", text, re.S)]


def ports_of(body):
    hdr = re.match(r"module\s+\w+\s*\(([^)]*)\)", body).group(1)
    names = [p.strip() for p in hdr.split(",") if p.strip()]
    dirs = {}
    for d, lst in re.findall(r"\b(input|output|inout)\s+([^;]*);", body):
        for n in lst.split(","):
            dirs[n.strip()] = d
    return [(n, dirs[n]) for n in names]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cells", required=True)
    ap.add_argument("--udp", required=True)
    ap.add_argument("--out", required=True, help="output directory")
    a = ap.parse_args()

    udps = parse_udps(strip_comments(open(a.udp).read()))
    text = strip_comments(open(a.cells).read())
    comb, seq, inv, used_udps, outputs = [], [], {}, set(), {}
    for name, body in split_modules(text):
        body = re.sub(r"\bspecify\b.*?\bendspecify\b", "", body, flags=re.S)
        outputs[name] = [n for n, d in ports_of(body) if d == "output"]
        if UNSUPPORTED.search(body):
            inv[name] = "unsupported (tristate)"
            continue
        if SEQ_UDP.search(body):
            ps = ports_of(body)
            seq.append(f"(* blackbox *)\nmodule {name} ({', '.join(f'{d} {n}' for n, d in ps)});\nendmodule\n")
            inv[name] = "seq-stub"
            continue
        for u in udps:
            if re.search(r"\b" + u + r"\b", body):
                if udps[u][3]:
                    sys.exit(f"cells.py: {name} uses sequential UDP {u} but is not classified sequential")
                used_udps.add(u)
                # UDP instances may be unnamed; module instances may not
                body = re.sub(r"\b(" + u + r")\s*\(", r"\1 u_" + u + " (", body)
        comb.append(body + "\n")
        inv[name] = "comb"
    with open(f"{a.out}/comb.v", "w") as f:
        f.write("// generated by formal/equiv/cells.py from the PDK functional models\n")
        for u in sorted(used_udps):
            f.write(udp_module(u, *udps[u][:3]))
        f.writelines(comb)
    with open(f"{a.out}/seq.v", "w") as f:
        f.write("// generated by formal/equiv/cells.py: state-holding cells as blackboxes\n")
        f.writelines(seq)
    json.dump({"cells": inv, "outputs": outputs, "translated_udps": sorted(used_udps)}, open(f"{a.out}/cells.json", "w"), indent=1)
    print(f"cells.py: {sum(v == 'comb' for v in inv.values())} combinational, "
          f"{sum(v == 'seq-stub' for v in inv.values())} sequential stubs, UDPs translated: {sorted(used_udps)}")


if __name__ == "__main__":
    main()
