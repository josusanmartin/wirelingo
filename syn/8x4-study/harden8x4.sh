#!/bin/bash
# Harden a committed HEAD, RTL unchanged, on Tiny Tapeout's 8x4 IHP tile
# (1724.16 x 710.64 um) without editing any committed flow/ file.
#
#   syn/8x4-study/harden8x4.sh NAME [-- gen_config.py options]
#
# Same steps as flow/harden.sh (2 cores, submission preset), except, in the
# run's own worktree only:
#   * info.yaml  tiles: "6x4" -> "8x4" (tt_tool then uses tt_block_8x4_pgvdd.def);
#   * src/config.json is written by a copy of flow/gen_config.py whose core
#     corner is the 8x4 die's (CORE_X1 1286.40 -> 1721.28, the same 2.88 um
#     margin), so the corners-swap SRAM layout puts bank 1 in the 8x4 die's
#     bottom-right corner instead of mid-die.
# Options after -- go to gen_config.py, e.g. --set CLOCK_PERIOD=16.667.
# Environment: PROTOEMU_EXP_DIR (parent of the run worktree), FLOW_VENV (the
# LibreLane venv, default <repo>/.venv-flow), TT_SRC (tt-support-tools with the
# 8x4 IHP tile, default <repo>/tt), PDK_ROOT (default ~/ttpdk).
# Writes exp.log and result.txt (flow status, commit, signoff summary).
set -euo pipefail
NAME=${1:?usage: syn/8x4-study/harden8x4.sh NAME [-- gen_config options]}
shift
[[ $NAME =~ ^[A-Za-z0-9][A-Za-z0-9_-]*$ ]] || { echo "unsafe run name"; exit 2; }
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
python3 "$G" --preset submission --banks 2 --output src/config.json "$@"
grep -q "^\`define PE_NCORES 2\$" src/project.v || { echo "PE_NCORES is not 2"; exit 2; }

export PATH=$VENV/bin:$PATH PDK_ROOT=${PDK_ROOT:-$HOME/ttpdk} PDK=ihp-sg13cmos5l
python ./tt/tt_tool.py --create-user-config --ihp > /dev/null
rc=0
python ./tt/tt_tool.py --harden --ihp > exp.log 2>&1 || rc=$?
{
  echo "flow exit status: $rc"
  git log --oneline -1
  echo "tiles: $(grep '^  tiles:' info.yaml)"
  python3 -c 'import json; d=json.load(open("src/config.json")); print("macros", {k: v["location"] for k, v in d["MACROS"]["RM_IHPSG13_1P_256x32_c2_bm_bist"]["instances"].items()}, "period", d["CLOCK_PERIOD"])'
  grep -E "Flow complete|flow failed|ERROR" exp.log | grep -v PSM-0069 | tail -3 || true
  grep -E "GPL-0019|design__instance__utilization" exp.log | tail -2 || true
  grep -A10 "Final congestion report" exp.log | grep -E "Metal[234] |Total " | tail -4 || true
  python3 flow/signoff.py runs/wokwi || { [ "$rc" = 0 ] && rc=1; }
} > result.txt
exit $rc
