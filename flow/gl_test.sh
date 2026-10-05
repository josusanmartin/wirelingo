#!/bin/bash
# Gate-level tests of a hardened run, as Tiny Tapeout's CI does for IHP: the
# unpowered netlist (final/nl) with the PDK cell and SRAM models, no SDF.
#   flow/gl_test.sh <run worktree>   (default: current directory)
# Runs every top-level suite (test.py, test_pio.py, test_eth_service.py) of the
# run's own commit on its netlist, with its PE_NCORES / PE_NBANKS /
# PE_FIFO_LOG2, each build in its own directory under <run>/test/sim_build/gl.*
# (never reusing old results), and fails if any test fails.
set -euo pipefail
W=$(realpath "${1:-$PWD}")
shopt -s nullglob
NETLISTS=("$W"/runs/wokwi/final/nl/*.nl.v)
[[ ${#NETLISTS[@]} == 1 ]] || { echo "expected exactly one final netlist in $W"; exit 2; }
NL=${NETLISTS[0]}
REPO=$(git -C "$(dirname "$0")" rev-parse --show-toplevel)     # for its .venv
def() { { grep -o "define $1 [0-9]" "$W/src/project.v" || true; } | awk '{print $3}' | head -1; }
NC=$(def PE_NCORES); FL=$(def PE_FIFO_LOG2); FL=${FL:-2}
NB=$(grep -q 'define PE_NBANKS `PE_NCORES' "$W/src/project.v" && echo "$NC" || def PE_NBANKS)
mkdir -p "$W/test/sim_build"
OUT=$(mktemp -d "$W/test/sim_build/gl.XXXXXX")
cp "$NL" "$OUT/netlist.v"
echo "netlist $(sha256sum "$NL" | cut -c1-16)  cores=$NC banks=$NB fifo_log2=$FL  outputs $OUT"
cd "$W/test"
export PATH=$HOME/tools/oss-cad-suite/bin:$REPO/.venv/bin:$PATH PDK_ROOT=${PDK_ROOT:-$HOME/ttpdk}
for module in test test_pio test_eth_service; do
  count=$(grep -c '^@cocotb.test()' "$module.py")
  if ! PE_NCORES=$NC PE_NBANKS=$NB PE_FIFO_LOG2=$FL make GATES=yes \
      GATE_LEVEL_NETLIST="$OUT/netlist.v" COCOTB_TEST_MODULES="$module" \
      SIM_BUILD="$OUT/$module" COCOTB_RESULTS_FILE="$OUT/$module.xml" > "$OUT/$module.log" 2>&1; then
    tail -30 "$OUT/$module.log"; echo "GL suite $module failed (log: $OUT/$module.log)"; exit 1
  fi
  python3 check_results.py "$OUT/$module.xml" "$count"
done
