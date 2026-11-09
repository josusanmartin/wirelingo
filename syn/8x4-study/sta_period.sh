#!/bin/sh
# STA of a finished run at another clock period, at the three signoff corners:
#   syn/8x4-study/sta_period.sh RUN_WORKTREE PERIOD_NS
# Mounts the run read-only in the LibreLane image and runs sta_period.tcl with
# the run's own SDC variables (resolved.json) except CLOCK_PERIOD. The I/O
# delays scale with the period, as in signoff (IO_DELAY_CONSTRAINT is a %).
set -e
RUN=$(cd "${1:?usage: sta_period.sh RUN_WORKTREE PERIOD_NS}" && pwd)
P=${2:?period in ns}
HERE=$(cd "$(dirname "$0")" && pwd)
ENV=$(python3 - "$RUN/runs/wokwi/resolved.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
print(" ".join(f"-e {k}={d[k]}" for k in ("IO_DELAY_CONSTRAINT", "MAX_FANOUT_CONSTRAINT", "SYNTH_DRIVING_CELL",
      "OUTPUT_CAP_LOAD", "CLOCK_UNCERTAINTY_CONSTRAINT", "CLOCK_TRANSITION_CONSTRAINT", "TIME_DERATING_CONSTRAINT")))
PY
)
for c in "typ_1p20V_25C typ_1p20V_25C" "slow_1p08V_125C slow_1p08V_125C" "fast_1p32V_m40C fast_1p32V_m55C"; do
  set -- $c
  echo "=== $1 (SRAM view $2), period $P ns"
  docker run --rm -v "$RUN/runs/wokwi:/work/run:ro" -v "$RUN/src:/work/src:ro" -v "${PDK_ROOT:-$HOME/ttpdk}:/pdk:ro" \
    -v "$HERE:/x:ro" $ENV -e CLOCK_PERIOD="$P" \
    -e STDLIB="sg13cmos5l_stdcell_$1.lib" -e SRAMLIB="RM_IHPSG13_1P_256x32_c2_bm_bist_$2.lib" \
    ghcr.io/librelane/librelane:3.1.0.dev3 sta -no_splash -exit /x/sta_period.tcl 2>&1 \
    | grep -E "^(period|worst slack|tns|setup_)|^ *(Endpoint|-----|[^ ].* -?[0-9]+\.[0-9]+ *\(VIOLATED\)|[^ ].* [0-9]+\.[0-9]+ *\(MET\))" | head -12
done
