#!/bin/sh
# Exact setup slack per family of a finished run at a period, three corners,
# nice 19, read-only: sta832.sh RUN_WORKTREE PERIOD_NS OUTDIR
# (syn/8x4-study/sta_period.sh's environment and mounts; sta832.tcl's reports)
set -e
RUN=$(cd "$1" && pwd); P=$2; OUT=$3; T=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$OUT"
ENV=$(python3 - "$RUN/runs/wokwi/resolved.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
print(" ".join(f"-e {k}={d[k]}" for k in ("IO_DELAY_CONSTRAINT", "MAX_FANOUT_CONSTRAINT", "SYNTH_DRIVING_CELL",
      "OUTPUT_CAP_LOAD", "CLOCK_UNCERTAINTY_CONSTRAINT", "CLOCK_TRANSITION_CONSTRAINT", "TIME_DERATING_CONSTRAINT")))
PY
)
OE=$(python3 - "$RUN"/runs/wokwi/final/nl/*.nl.v <<'PY'
import re, sys
nl = open(sys.argv[1]).read()
insts = [m.group(1) for m in re.finditer(r"sg13cmos5l_\w+\s+(\S+)\s*\(([^;]*?)\);", nl, re.S)
         if re.search(r"\.Q\(\s*\\u_chip\.pin_oe\[\d+\]\s*\)", m.group(2))]
print(" ".join(insts))
PY
)
echo "pin_oe registers: $(echo $OE | wc -w)" > "$OUT/oe.txt"
for c in "typ_1p20V_25C typ_1p20V_25C" "slow_1p08V_125C slow_1p08V_125C" "fast_1p32V_m40C fast_1p32V_m55C"; do
  set -- $c
  nice -n 19 docker run --rm --network none -v "$RUN/runs/wokwi:/work/run:ro" -v "$RUN/src:/work/src:ro" \
    -v "${PDK_ROOT:-$HOME/ttpdk}:/pdk:ro" -v "$T:/x:ro" $ENV -e CLOCK_PERIOD="$P" -e "OE_INSTS=$OE" \
    -e STDLIB="sg13cmos5l_stdcell_$1.lib" -e SRAMLIB="RM_IHPSG13_1P_256x32_c2_bm_bist_$2.lib" \
    ghcr.io/librelane/librelane:3.1.0.dev3 sta -no_splash -exit /x/sta832.tcl > "$OUT/$1.rpt" 2>&1
done
echo done > "$OUT/finished"
