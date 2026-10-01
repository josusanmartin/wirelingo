#!/bin/bash
# Harden HEAD in its own git worktree with Tiny Tapeout's flow (LibreLane, ~3-4 h).
#
#   flow/harden.sh NAME [NCORES] [-- gen_config.py options]
#
# The worktree is <parent of this repo>/protoemu-exp-NAME (PROTOEMU_EXP_DIR
# overrides the parent); an existing one is kept unless FORCE=1. NCORES (1-4,
# default 2) sets PE_NCORES and the number of SRAM banks. Writes exp.log and
# result.txt (exit status, commit, signoff summary) in the worktree and exits
# with the flow's status. Needs .venv-flow (tt-support-tools requirements) and
# PDK_ROOT (default ~/ttpdk). FIFO_LOG2=2|3 overrides the FIFO depth. STOP=grt stops after global routing (~45 min) for
# routability experiments; result.txt then has the congestion report.
set -euo pipefail
NAME=${1:?usage: flow/harden.sh NAME [NCORES] [-- gen_config options]}
shift
NC=2
if [[ ${1:-} =~ ^[0-9]+$ ]]; then NC=$1; shift; fi
[[ $NC =~ ^[1-4]$ ]] || { echo "NCORES must be 1..4"; exit 2; }
[ "${1:-}" = "--" ] && shift

REPO=$(git -C "$(dirname "$0")" rev-parse --show-toplevel)
W=${PROTOEMU_EXP_DIR:-$(dirname "$REPO")}/protoemu-exp-$NAME
if [ -e "$W" ]; then
  [ "${FORCE:-0}" = 1 ] || { echo "$W exists (FORCE=1 to replace it)"; exit 2; }
  git -C "$REPO" worktree remove --force "$W"
fi
[ -d "$REPO/tt" ] || { echo "$REPO/tt (tt-support-tools) is missing"; exit 2; }

git -C "$REPO" worktree add -q --detach "$W" HEAD
cd "$W"
cp -r "$REPO/tt" .
python3 flow/gen_config.py --banks "$NC" "$@"
sed -i "s/^\`define PE_NCORES [0-9]\$/\`define PE_NCORES $NC/" src/project.v
grep -q "^\`define PE_NCORES $NC\$" src/project.v || { echo "could not set PE_NCORES in src/project.v"; exit 2; }
if [ -n "${FIFO_LOG2:-}" ]; then                 # optional: FIFO depth 2**FIFO_LOG2
  sed -i "s/^\`define PE_FIFO_LOG2 [0-9]\$/\`define PE_FIFO_LOG2 $FIFO_LOG2/" src/project.v
  grep -q "^\`define PE_FIFO_LOG2 $FIFO_LOG2\$" src/project.v || { echo "could not set PE_FIFO_LOG2"; exit 2; }
fi

export PATH=$REPO/.venv-flow/bin:$PATH PDK_ROOT=${PDK_ROOT:-$HOME/ttpdk} PDK=ihp-sg13cmos5l
python ./tt/tt_tool.py --create-user-config --ihp > /dev/null
rc=0
if [ "${STOP:-}" = grt ]; then
  mkdir -p runs/wokwi
  python -m librelane --pdk-root "$PDK_ROOT" --docker-no-tty --dockerized --pdk ihp-sg13cmos5l --manual-pdk \
    --run-tag wokwi --force-run-dir runs/wokwi --to OpenROAD.GlobalRouting src/config_merged.json > exp.log 2>&1 || rc=$?
else
  python ./tt/tt_tool.py --harden --ihp > exp.log 2>&1 || rc=$?
fi
{
  echo "exit status: $rc"
  git log --oneline -1
  grep -E "Flow complete|flow failed|ERROR" exp.log | grep -v PSM-0069 | tail -3 || true
  grep -E "GPL-0019|design__instance__utilization" exp.log | tail -2 || true
  grep -A10 "Final congestion report" exp.log | grep -E "Metal[234] |Total " | tail -4 || true
  [ "${STOP:-}" = grt ] || python3 flow/signoff.py runs/wokwi || true
} > result.txt
exit $rc
