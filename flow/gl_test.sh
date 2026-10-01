#!/bin/bash
# Gate-level test of a hardened run, as Tiny Tapeout's CI does for IHP: the
# unpowered netlist (final/nl) with the PDK cell and SRAM models.
#   flow/gl_test.sh <worktree with runs/wokwi>   (default: current directory)
# Uses the run's PE_NCORES / PE_NBANKS / PE_FIFO_LOG2, checks that every
# top-level test passes, and prints the netlist hash.
set -euo pipefail
W=${1:-$PWD}
NL=$(ls "$W"/runs/wokwi/final/nl/*.nl.v 2>/dev/null | head -1)
[ -n "$NL" ] || { echo "no netlist in $W/runs/wokwi/final/nl"; exit 2; }
REPO=$(git -C "$(dirname "$0")" rev-parse --show-toplevel)
def() { { grep -o "define $1 [0-9]" "$W/src/project.v" || true; } | awk '{print $3}' | head -1; }
NC=$(def PE_NCORES); FL=$(def PE_FIFO_LOG2); FL=${FL:-2}   # runs before 8-deep FIFOs had no define
NB=$(grep -q 'define PE_NBANKS `PE_NCORES' "$W/src/project.v" && echo "$NC" || def PE_NBANKS)
echo "netlist $(sha256sum "$NL" | cut -c1-16)  cores=$NC banks=$NB fifo_log2=$FL"
cd "$REPO/test"
cp "$NL" gate_level_netlist.v
export PATH=$HOME/tools/oss-cad-suite/bin:$REPO/.venv/bin:$PATH PDK_ROOT=${PDK_ROOT:-$HOME/ttpdk}
rm -rf sim_build/gl
PE_NCORES=$NC PE_NBANKS=$NB PE_FIFO_LOG2=$FL make GATES=yes COCOTB_RESULTS_FILE=gl.xml > gl.log 2>&1 || true
grep -E "^\s+\*\* test\." gl.log | cut -c1-100 || true
python3 check_results.py gl.xml "$(grep -c '^@cocotb.test()' test.py)"
