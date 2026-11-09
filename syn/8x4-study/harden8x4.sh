#!/bin/bash
# Harden a committed HEAD, RTL unchanged, on Tiny Tapeout's 8x4 IHP tile
# (1724.16 x 710.64 um) without editing any committed flow/ file.
#
#   syn/8x4-study/harden8x4.sh NAME [NCORES] [-- gen_config.py options]
#
# Same steps as flow/harden.sh, except, in the run's own worktree only:
#   * info.yaml  tiles: "6x4" -> "8x4" (tt_tool then uses tt_block_8x4_pgvdd.def);
#   * src/config.json is written by a copy of flow/gen_config.py whose core
#     corner is the 8x4 die's (CORE_X1 1286.40 -> 1721.28, the same 2.88 um
#     margin), so its SRAM layouts use the whole 8x4 width.
# NCORES (2-4, default 2) sets PE_NCORES and the SRAM banks, as in
# flow/harden.sh; 2 cores use the submission preset, else the experiment one.
# STOP=grt stops after global routing (result.txt: the congestion report),
# FIFO_LOG2=2|3 overrides the FIFO depth. Options after -- go to
# gen_config.py, e.g. --layout row --density 52 or --set CLOCK_PERIOD=16.667.
# Environment: PROTOEMU_EXP_DIR (parent of the run worktree), FLOW_VENV (the
# LibreLane venv, default <repo>/.venv-flow), TT_SRC (tt-support-tools with the
# 8x4 IHP tile, default <repo>/tt), PDK_ROOT (default ~/ttpdk).
# Writes exp.log and result.txt (flow status, commit, signoff summary).
set -euo pipefail
NAME=${1:?usage: syn/8x4-study/harden8x4.sh NAME [NCORES] [-- gen_config options]}
shift
NC=2
if [[ ${1:-} =~ ^[0-9]+$ ]]; then NC=$1; shift; fi
[[ $NC =~ ^[2-4]$ ]] || { echo "NCORES must be 2..4"; exit 2; }
[[ $NAME =~ ^[A-Za-z0-9][A-Za-z0-9_-]*$ ]] || { echo "unsafe run name"; exit 2; }
[[ ${FIFO_LOG2:-3} =~ ^[23]$ ]] || { echo "FIFO_LOG2 must be 2 or 3"; exit 2; }
[[ ${STOP:-grt} == grt ]] || { echo "STOP=grt only"; exit 2; }
[ "${1:-}" = "--" ] && shift

REPO=$(git -C "$(dirname "$0")" rev-parse --show-toplevel)
W=${PROTOEMU_EXP_DIR:-$(dirname "$REPO")}/protoemu-exp-$NAME
VENV=${FLOW_VENV:-$REPO/.venv-flow}
TT=${TT_SRC:-$REPO/tt}
[ -e "$W" ] && { echo "$W exists; select a new run name"; exit 2; }
grep -q '^8x4: "0 0 1724.16 710.64"' "$TT/tech/ihp-sg13cmos5l/tile_sizes.yaml" \
  || { echo "$TT has no 8x4 IHP tile"; exit 2; }
[ -f "$TT/tech/ihp-sg13cmos5l/def/tt_block_8x4_pgvdd.def" ] || { echo "no 8x4 DEF template"; exit 2; }
if [ -n "$(git -C "$REPO" status --porcelain)" ]; then
  echo "source tree is dirty: commit what you want hardened (a run is a commit)"; exit 2
fi

git -C "$REPO" worktree add -q --detach "$W" HEAD
cd "$W"
cp -r "$TT" tt
sed -i 's/^  tiles: "6x4"$/  tiles: "8x4"/' info.yaml
grep -q '^  tiles: "8x4"$' info.yaml || { echo "could not set tiles in info.yaml"; exit 2; }
G=$(mktemp -d)/gen_config_8x4.py
sed 's/^CORE_X1, CORE_Y1 = 1286.40, 706.86  # core area top-right (6x4 tile)$/CORE_X1, CORE_Y1 = 1721.28, 706.86  # 8x4 (study copy)/' \
  flow/gen_config.py > "$G"
grep -q '^CORE_X1, CORE_Y1 = 1721.28, 706.86' "$G" || { echo "could not patch the core corner"; exit 2; }
# --layout spread (this copy only): the banks evenly along the bottom edge,
# mirrored (pins up); bank 0 leftmost (core 0's line unit and the I/O pins
# pull its logic left), bank 1 rightmost, banks 2 and 3 in between.
python3 - "$G" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read()
anchor = '    if layout == "row":\n'
assert s.count(anchor) == 1 and s.count('"corners-swap", "row"]') == 1
s = s.replace(anchor, '''    if layout == "spread":
        gap = (CORE_X1 - 2.88 - 6 * SITE - nbanks * W) / max(nbanks - 1, 1)
        order = {1: [0], 2: [0, 1], 3: [0, 2, 1], 4: [0, 2, 3, 1]}[nbanks]
        return {b: (snap(2.88 + 3 * SITE + order.index(b) * (W + gap), SITE), 3.78, "FS") for b in range(nbanks)}
''' + anchor)
s = s.replace('"corners-swap", "row"]', '"corners-swap", "row", "spread"]')
open(p, "w").write(s)
PY
PRESET=submission
if [ "$NC" != 2 ] || [ -n "${STOP:-}" ]; then PRESET=experiment; fi
python3 "$G" --preset "$PRESET" --banks "$NC" --output src/config.json "$@"
sed -i "s/^\`define PE_NCORES [0-9]\$/\`define PE_NCORES $NC/" src/project.v
grep -q "^\`define PE_NCORES $NC\$" src/project.v || { echo "could not set PE_NCORES in src/project.v"; exit 2; }
if [ -n "${FIFO_LOG2:-}" ]; then
  sed -i "s/^\`define PE_FIFO_LOG2 [0-9]\$/\`define PE_FIFO_LOG2 $FIFO_LOG2/" src/project.v
  grep -q "^\`define PE_FIFO_LOG2 $FIFO_LOG2\$" src/project.v || { echo "could not set PE_FIFO_LOG2"; exit 2; }
fi

export PATH=$VENV/bin:$PATH PDK_ROOT=${PDK_ROOT:-$HOME/ttpdk} PDK=ihp-sg13cmos5l
python ./tt/tt_tool.py --create-user-config --ihp > /dev/null
rc=0
if [ -n "${STOP:-}" ]; then
  mkdir -p runs/wokwi
  python -m librelane --pdk-root "$PDK_ROOT" --docker-no-tty --dockerized --pdk ihp-sg13cmos5l --manual-pdk \
    --run-tag wokwi --force-run-dir runs/wokwi --to OpenROAD.GlobalRouting src/config_merged.json > exp.log 2>&1 || rc=$?
else
  python ./tt/tt_tool.py --harden --ihp > exp.log 2>&1 || rc=$?
fi
{
  echo "flow exit status: $rc"
  git log --oneline -1
  echo "tiles: $(grep '^  tiles:' info.yaml), cores $NC, FIFO_LOG2 ${FIFO_LOG2:-3}, STOP ${STOP:-none}"
  python3 -c 'import json; d=json.load(open("src/config.json")); print("macros", {k[18:24]: v["location"] for k, v in d["MACROS"]["RM_IHPSG13_1P_256x32_c2_bm_bist"]["instances"].items()}, "period", d["CLOCK_PERIOD"], "density", d["PL_TARGET_DENSITY_PCT"])'
  grep -E "Flow complete|flow failed|ERROR" exp.log | grep -v PSM-0069 | tail -3 || true
  grep -E "GPL-0019|design__instance__utilization" exp.log | tail -2 || true
  grep -A10 "Final congestion report" exp.log | grep -E "Metal[234] |Total " | tail -4 || true
  if [ -z "${STOP:-}" ]; then
    python3 flow/signoff.py runs/wokwi || { [ "$rc" = 0 ] && rc=1; }
  fi
} > result.txt
exit $rc
