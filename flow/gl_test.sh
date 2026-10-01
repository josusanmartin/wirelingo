#!/bin/bash
# Gate-level test of a hardened run, like TT's gl_test action.
# Usage: flow/gl_test.sh <worktree-with-runs/wokwi>   (default: current dir)
set -e
W=${1:-$PWD}
NL=$(ls $W/runs/wokwi/final/nl/*.nl.v 2>/dev/null | head -1)   # the IHP cell models have no power pins
[ -n "$NL" ] || { echo "no netlist in $W/runs/wokwi/final/nl"; exit 1; }
cd $HOME/dev/protoemu/test
cp "$NL" gate_level_netlist.v
NC=$(grep -o 'define PE_NCORES [0-9]' $W/src/project.v | awk '{print $3}')
FL=$(grep -o 'define PE_FIFO_LOG2 [0-9]' $W/src/project.v | awk '{print $3}')
export PATH=$HOME/tools/oss-cad-suite/bin:$HOME/dev/protoemu/.venv/bin:$PATH PDK_ROOT=$HOME/ttpdk
make clean >/dev/null 2>&1 || true
PE_NCORES=${NC:-4} PE_NBANKS=${NC:-4} PE_FIFO_LOG2=${FL:-2} make GATES=yes 2>&1 | grep -v "sorry: ifnone" | grep -E "TESTS=|PASS|FAIL|rror" | cut -c1-160
