#!/bin/bash
# Full SRAM-build hardening experiment. Usage: flow/exp_sram.sh NAME NCORES DENSITY [GRT_ADJ]
set -e
NAME=$1; NC=$2; DEN=$3; ADJ=${4:-0.15}
W=$HOME/dev/protoemu-exp-$NAME
cd $HOME/dev
rm -rf $W; git -C protoemu worktree prune
git -C protoemu worktree add -q --detach $W HEAD
cd $W
cp -r $HOME/dev/protoemu/tt .
cp $HOME/dev/protoemu/flow/gen_config.py flow/
python3 flow/gen_config.py --banks $NC --density $DEN --grt-adjust $ADJ
python3 - "$NC" <<'PY'
import sys
nc = sys.argv[1]
p = 'src/project.v'; s = open(p).read()
s = s.replace('`define PE_NCORES 4', f'`define PE_NCORES {nc}')
open(p, 'w').write(s)
PY
grep -q "define PE_NCORES $NC" src/project.v
export PATH=$HOME/dev/protoemu/.venv-flow/bin:$PATH PDK_ROOT=$HOME/ttpdk PDK=ihp-sg13cmos5l
python ./tt/tt_tool.py --create-user-config --ihp > /dev/null 2>&1
python ./tt/tt_tool.py --harden --ihp > exp.log 2>&1 || true
{ grep -E "GPL-0019" exp.log | tail -1; grep -A10 "Final congestion report" exp.log | grep -E "Metal[234] |Total " | tail -4; grep -E "DRT-0199|Number of violations|flow failed|Flow complete|ERROR" exp.log | tail -6; } > result.txt
echo DONE >> result.txt
