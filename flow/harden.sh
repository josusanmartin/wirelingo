#!/bin/bash
# Harden HEAD in its own git worktree with Tiny Tapeout's flow (LibreLane, ~2 h).
#
#   flow/harden.sh NAME [NCORES] [-- gen_config.py options]
#
# The worktree is <parent of this repo>/protoemu-exp-NAME (PROTOEMU_EXP_DIR
# overrides the parent); an existing run is never overwritten. NCORES (1-4,
# default 2) sets PE_NCORES and the number of SRAM banks. Writes exp.log and
# result.txt (exit status, commit, signoff summary) in the worktree and exits
# with the flow's status, or 1 if flow/signoff.py fails it. Needs .venv-flow and
# PDK_ROOT (default ~/ttpdk). FIFO_LOG2=2|3 overrides the FIFO depth.
# STOP=grt stops after global routing (~45 min), STOP=cts after clock-tree synthesis,
# STOP=dpl after detailed placement (~10 min), for routability experiments;
# result.txt then has the congestion report or the placement wirelength.
set -euo pipefail
NAME=${1:?usage: flow/harden.sh NAME [NCORES] [-- gen_config options]}
shift
NC=2
if [[ ${1:-} =~ ^[0-9]+$ ]]; then NC=$1; shift; fi
[[ $NC =~ ^[1-4]$ ]] || { echo "NCORES must be 1..4"; exit 2; }
[[ $NAME =~ ^[A-Za-z0-9][A-Za-z0-9_-]*$ ]] || { echo "unsafe run name"; exit 2; }
[[ ${FIFO_LOG2:-3} =~ ^[23]$ ]] || { echo "FIFO_LOG2 must be 2 or 3"; exit 2; }
[ "${1:-}" = "--" ] && shift

REPO=$(git -C "$(dirname "$0")" rev-parse --show-toplevel)
W=${PROTOEMU_EXP_DIR:-$(dirname "$REPO")}/protoemu-exp-$NAME
if [ -e "$W" ]; then
  echo "$W exists; select a new run name"
  exit 2
fi
[ -d "$REPO/tt" ] || { echo "$REPO/tt (tt-support-tools) is missing"; exit 2; }
if [ -n "$(git -C "$REPO" status --porcelain)" ]; then
  echo "source tree is dirty: commit what you want hardened (a run is a commit)"
  exit 2
fi

git -C "$REPO" worktree add -q --detach "$W" HEAD
cd "$W"
cp -r "$REPO/tt" .
PRESET=submission
if [ "$NC" != 2 ] || [ -n "${STOP:-}" ]; then PRESET=experiment; fi
python3 flow/gen_config.py --preset "$PRESET" --banks "$NC" "$@"
sed -i "s/^\`define PE_NCORES [0-9]\$/\`define PE_NCORES $NC/" src/project.v
grep -q "^\`define PE_NCORES $NC\$" src/project.v || { echo "could not set PE_NCORES in src/project.v"; exit 2; }
if [ -n "${FIFO_LOG2:-}" ]; then                 # optional: FIFO depth 2**FIFO_LOG2
  sed -i "s/^\`define PE_FIFO_LOG2 [0-9]\$/\`define PE_FIFO_LOG2 $FIFO_LOG2/" src/project.v
  grep -q "^\`define PE_FIFO_LOG2 $FIFO_LOG2\$" src/project.v || { echo "could not set PE_FIFO_LOG2"; exit 2; }
fi

export PATH=$REPO/.venv-flow/bin:$PATH PDK_ROOT=${PDK_ROOT:-$HOME/ttpdk} PDK=ihp-sg13cmos5l
python ./tt/tt_tool.py --create-user-config --ihp > /dev/null
rc=0
if [ -n "${STOP:-}" ]; then
  case $STOP in grt) TO=OpenROAD.GlobalRouting ;; cts) TO=OpenROAD.CTS ;; dpl) TO=OpenROAD.DetailedPlacement ;;
    *) echo "STOP=grt|cts|dpl"; exit 2 ;; esac
  mkdir -p runs/wokwi
  python -m librelane --pdk-root "$PDK_ROOT" --docker-no-tty --dockerized --pdk ihp-sg13cmos5l --manual-pdk \
    --run-tag wokwi --force-run-dir runs/wokwi --to $TO src/config_merged.json > exp.log 2>&1 || rc=$?
else
  python ./tt/tt_tool.py --harden --ihp > exp.log 2>&1 || rc=$?
fi
{
  echo "flow exit status: $rc"
  git log --oneline -1
  grep -E "Flow complete|flow failed|ERROR" exp.log | grep -v PSM-0069 | tail -3 || true
  grep -E "GPL-0019|design__instance__utilization" exp.log | tail -2 || true
  grep -A10 "Final congestion report" exp.log | grep -E "Metal[234] |Total " | tail -4 || true
  grep -h "DPL-0022" runs/wokwi/*-openroad-detailedplacement/*.log 2>/dev/null | tail -1 || true
  if [ -z "${STOP:-}" ]; then
    python3 flow/signoff.py runs/wokwi || { [ "$rc" = 0 ] && rc=1; }
  fi
} > result.txt
exit $rc
