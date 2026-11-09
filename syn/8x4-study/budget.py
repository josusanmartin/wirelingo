#!/usr/bin/env python3
"""Area budget table for docs/8x4-study.md from syn/8x4-study/results.txt.

    python3 syn/8x4-study/budget.py [results.txt]

For each synthesis row: the area delta against the 2-core row, an ESTIMATE of
the hardened standard-cell area (synthesis area x fo6's ratio of hardened to
synthesized area for the taped-out RTL, which folds in buffering, clock tree,
hold and antenna cells), and the standard-cell utilization that estimate
would give on the 8x4 core (core area minus one 256x32 SRAM outline per bank).
The ratio comes from one design; a new netlist can differ, so the utilization
column is an estimate, not a harden.
"""
import pathlib
import re
import sys

FO6_STDCELL = 376827        # fo6 final/metrics.json design__instance__area__stdcell (um^2)
FO6_SYNTH = 295218          # results.txt row [2]: the same RTL, this script's synthesis
CORE_8X4 = (1721.28 - 2.88) * (706.86 - 3.78)   # 8x4 core bbox (um^2)
SRAM = 416.64 * 118.78      # RM_IHPSG13_1P_256x32 LEF outline (um^2)
K = FO6_STDCELL / FO6_SYNTH

path = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else pathlib.Path(__file__).with_name("results.txt"))
rows = re.findall(r"^\[(\d)([^\]]*)\] cells \d+ flops (\d+) latches (\d+) area (\d+)", path.read_text(), re.M)
base = next(int(a) for n, d, f, l, a in rows if n == "2" and not d.strip())
print(f"hardened/synth ratio (fo6) {K:.4f}; 8x4 core {CORE_8X4:,.0f} um^2; SRAM {SRAM:,.1f} um^2")
print(f"{'cores':>5} {'defines':<60} {'flops':>5} {'synth':>8} {'delta':>8} {'hardened est.':>13} {'util est.':>9}")
BUNDLE = "V3_CMPW V3_CMPW_SPLIT V3_NT V3_TBL V3_TBL_OSR V3_BANKREL V3_TOG"


def label(d):
    d = " ".join(d.split()).replace(BUNDLE, "BUNDLE").replace("V3_CRC32 V8_CRCW", "CRC_ANY")
    return d.replace("X_LINE1 V8_LINE1", "LINE1").replace("PE_FIFO_LOG2=2", "FIFO4")


for n, d, f, l, a in rows:
    d = label(d)
    nc, a = int(n), int(a)
    nb = nc
    hard = K * a
    util = hard / (CORE_8X4 - nb * SRAM)
    print(f"{nc:>5} {d.strip()[:60]:<60} {f:>5} {a:>8,} {a - base:>+8,} {hard:>13,.0f} {util:>8.1%}")
