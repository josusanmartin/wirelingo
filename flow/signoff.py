#!/usr/bin/env python3
"""Signoff summary of a LibreLane run: DRC/LVS/antenna, timing and electrical
exceptions per corner, utilization, the SRAM timing view each STA corner
loaded, and the netlist/GDS hashes. Ends with a verdict on what Tiny Tapeout
signs off: no DRC/LVS/antenna errors, setup met at the typical corner, hold
met at every corner, and each corner timed with its own SRAM view. The slow
corner's setup slack is reported, not required (docs/physical.md).

    python3 flow/signoff.py <run dir, e.g. runs/wokwi>     # exit 1 if it fails
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
MUST_BE_ZERO = KEYS[:4]
CORNERS = {"nom_typ_1p20V_25C": "typ_1p20V_25C",
           "nom_slow_1p08V_125C": "slow_1p08V_125C",
           "nom_fast_1p32V_m40C": "fast_1p32V_m55C"}     # the SRAM has no -40C view


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()[:16] if os.path.exists(path) else "-"


def main(run):
    failures = []
    m = json.load(open(os.path.join(run, "final", "metrics.json")))
    for label, k in KEYS:
        print(f"{label:28s} {m.get(k)}")
    for label, k in MUST_BE_ZERO:
        if m.get(k) != 0:
            failures.append(f"{label}: {m.get(k)}")
    for c in CORNERS:
        g = lambda k: m.get(f"{k}__corner:{c}")
        print(f"{c:28s} setup WS {g('timing__setup__ws')}, hold WS {g('timing__hold__ws')}, "
              f"setup/hold violators {g('timing__setup_vio__count')}/{g('timing__hold_vio__count')}, "
              f"max slew/cap violations {g('design__max_slew_violation__count')}/"
              f"{g('design__max_cap_violation__count')}")
        if g("timing__hold__ws") is None or g("timing__hold__ws") < 0:
            failures.append(f"{c}: hold WS {g('timing__hold__ws')}")
        if c.startswith("nom_typ") and (g("timing__setup__ws") is None or g("timing__setup__ws") < 0):
            failures.append(f"{c}: setup WS {g('timing__setup__ws')}")
    sta = sorted(glob.glob(os.path.join(run, "*-openroad-stapostpnr")))
    for c, view in CORNERS.items():
        log = os.path.join(sta[-1], c, "sta.log") if sta else ""
        libs = sorted(set(re.findall(r"RM_IHPSG13_1P_256x32_c2_bm_bist_(\w+?)\.lib",
                                     open(log).read()))) if os.path.exists(log) else []
        print(f"SRAM timing view, {c:20s} {', '.join(libs) or '-'}")
        if libs != [view]:
            failures.append(f"{c}: SRAM timing view {libs or 'missing'}")
    top = os.path.basename(glob.glob(os.path.join(run, "final", "nl", "*.nl.v"))[0])[:-5]
    gds = os.path.join(run, "final", "gds", top + ".gds")
    print(f"sha256 nl {sha(os.path.join(run, 'final', 'nl', top + '.nl.v'))}  gds {sha(gds)}")
    if not os.path.exists(gds):
        failures.append("no final GDS")
    print("SIGNOFF " + ("FAILED: " + "; ".join(failures) if failures else "PASSED"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
