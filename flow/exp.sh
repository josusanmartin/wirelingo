#!/bin/bash
# Routability experiment: flop IMEM (4 words/bank), no SRAM macros, flow stops after
# global routing. Usage: flow/exp.sh NAME NCORES [extra python edit for src/]
set -e
NAME=$1; NC=$2
W=$HOME/dev/protoemu-exp-$NAME
cd $HOME/dev
rm -rf $W; git -C protoemu worktree prune
git -C protoemu worktree add -q --detach $W HEAD
cd $W
cp -r $HOME/dev/protoemu/tt .
cp $HOME/dev/protoemu-flow/src/config.json src/config.json 2>/dev/null || git show cd4d3d2:src/config.json > src/config.json
python3 - "$NC" <<'PY'
import json, sys, collections
nc = sys.argv[1]
p = 'src/project.v'; s = open(p).read()
s = s.replace('`ifdef PE_FLOP_IMEM', '`define PE_FLOP_IMEM\n`define PE_BANK_AW 2\n`ifdef PE_FLOP_IMEM', 1)
s = s.replace('pe_chip #(.BANK_AW(`PE_BANK_AW)) u_chip (', f'pe_chip #(.BANK_AW(`PE_BANK_AW), .NCORES({nc})) u_chip (')
open(p, 'w').write(s)
c = json.load(open('src/config.json'), object_pairs_hook=collections.OrderedDict)
c["PNR_CORNERS"] = ["nom_typ_1p20V_25C", "nom_slow_1p08V_125C"]
json.dump(c, open('src/config.json', 'w'), indent=2)
PY
if [ -n "$3" ]; then python3 -c "$3"; fi
export PATH=$HOME/dev/protoemu/.venv-flow/bin:$PATH PDK_ROOT=$HOME/ttpdk PDK=ihp-sg13cmos5l
python ./tt/tt_tool.py --create-user-config --ihp > /dev/null 2>&1
mkdir -p runs/exp
python -m librelane --pdk-root $PDK_ROOT --docker-no-tty --dockerized --pdk ihp-sg13cmos5l --manual-pdk \
  --run-tag exp --force-run-dir runs/exp --to OpenROAD.GlobalRouting src/config_merged.json > exp.log 2>&1 || true
{ grep -E "GPL-0019" exp.log | tail -1; grep -A10 "Final congestion report" exp.log | grep -E "Metal[234] |Total "; } > result.txt
echo DONE >> result.txt
