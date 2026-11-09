#!/usr/bin/env python3
"""One-line summary of a yosys `stat -liberty` report (syn/8x4-study scripts).

    stat_summary.py STAT_FILE LABEL
"""
import re
import sys

txt = open(sys.argv[1]).read()
own = {}
for name, a in re.findall(r"Chip area for module '(.*)':\s*([\d.]+)\s*$", txt, re.M):
    m = re.search(r"\\(pe_\w+|tt_um_\w+)(?:\\IDX=2'(\d+))?", name)
    key = (m.group(1) + (f"[{int(m.group(2), 2)}]" if m.group(2) else "")) if m else name
    own[key] = float(a)
body = txt.split("=== design hierarchy ===")[-1]
area = re.findall(r"Chip area for (?:top )?module '[^']+':\s*([\d.]+)", body)[-1]
cells = re.search(r"^\s+(\d+)\s+\S+\s+cells$", body, re.M).group(1)
flops = sum(int(n) for n in re.findall(r"^\s+(\d+)\s+\S+\s+sg13cmos5l_df\w+$", body, re.M))
lat = sum(int(n) for n in re.findall(r"^\s+(\d+)\s+\S+\s+sg13cmos5l_dl\w+$", body, re.M))
print(f"[{sys.argv[2].strip()}] cells {cells} flops {flops} latches {lat} area {float(area):.0f}")
mods = {k: v for k, v in own.items() if k.startswith(("pe_core", "pe_chip", "pe_line", "pe_hostport", "pe_x"))}
if mods:
    print("   own area:", ", ".join(f"{k} {v:.0f}" for k, v in sorted(mods.items())))
