#!/usr/bin/env python3
"""Markdown rows for the AQ search table (run on runner1): mdtable.py NAME..."""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import table  # noqa: E402

S, T, F = "nom_slow_1p08V_125C", "nom_typ_1p20V_25C", "nom_fast_1p32V_m40C"
for n in sys.argv[1:]:
    r = table.load(n)
    d = f"{table.B}/{n}"
    spec = r["spec"].split(" -- ", 1)
    opts = spec[1] if len(spec) > 1 else "none"
    res = open(f"{d}/result.txt").read()
    commit = res.splitlines()[1].split()[0]
    hpwl = re.search(r"HPWL after\s+([\d.]+)", open(f"{d}/placement.txt").read())
    s = r["corners"][S]
    s48 = r["sta48"].get(S)
    mins = re.search(r"minutes=(\d+)", r["time"])[1]
    print(f"| {n} | `{opts}` | {s['setup']:+.3f} ({s['svio']}, {s['tns']:.2f}) | {r['flop20'].get('slow_1p08V_125C'):+.3f} | "
          f"{s48[0]:.3f} ({s48[1]}) | {r['corners'][T]['hold']:+.3f} / {s['hold']:+.3f} / {r['corners'][F]['hold']:+.3f} | "
          f"{r['elec']} | {r['metal3']} % / {r['ovf']} | {r['wire']:,} | {float(hpwl[1]):,.0f} | {mins} | {commit} | `{r['nl']}` |")
