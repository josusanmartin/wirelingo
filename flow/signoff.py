#!/usr/bin/env python3
"""Signoff summary of a LibreLane run: DRC/LVS/antenna, timing per corner,
electrical exceptions, utilization, the SRAM timing view each STA corner
loaded, and the netlist/GDS hashes.

    python3 flow/signoff.py <run dir, e.g. runs/wokwi>
"""
import glob
import hashlib
import json
import os
import re
import sys

KEYS = [
    ("routing DRC", "route__drc_errors"),
    ("KLayout DRC", "klayout__drc_error__count"),
    ("LVS errors", "design__lvs_error__count"),
    ("antenna violations", "route__antenna_violation__count"),
    ("stdcell utilization", "design__instance__utilization__stdcell"),
    ("utilization incl. macros", "design__instance__utilization"),
]
CORNERS = ["nom_typ_1p20V_25C", "nom_slow_1p08V_125C", "nom_fast_1p32V_m40C"]


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()[:16] if os.path.exists(path) else "-"


def main(run):
    m = json.load(open(os.path.join(run, "final", "metrics.json")))
    for label, k in KEYS:
        print(f"{label:28s} {m.get(k)}")
    for c in CORNERS:
        g = lambda k: m.get(f"{k}__corner:{c}")
        print(f"{c:28s} setup WS {g('timing__setup__ws')}, hold WS {g('timing__hold__ws')}, "
              f"setup/hold violators {g('timing__setup_vio__count')}/{g('timing__hold_vio__count')}, "
              f"max slew/cap violations {g('design__max_slew_violation__count')}/"
              f"{g('design__max_cap_violation__count')}")
    sta = sorted(glob.glob(os.path.join(run, "*-openroad-stapostpnr")))
    if sta:
        for c in CORNERS:
            log = os.path.join(sta[-1], c, "sta.log")
            libs = sorted(set(re.findall(r"RM_IHPSG13_1P_256x32_c2_bm_bist_(\w+?)\.lib", open(log).read()))) if os.path.exists(log) else []
            print(f"SRAM timing view, {c:20s} {', '.join(libs) or '-'}")
    top = os.path.basename(glob.glob(os.path.join(run, "final", "nl", "*.nl.v"))[0])[:-5]
    print(f"sha256 nl {sha(os.path.join(run, 'final', 'nl', top + '.nl.v'))}  "
          f"gds {sha(os.path.join(run, 'final', 'gds', top + '.gds'))}")


if __name__ == "__main__":
    main(sys.argv[1])
