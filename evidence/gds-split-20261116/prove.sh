#!/bin/bash
# ci-split proof: resume copies of the release run tree with the LibreLane
# arguments of the split gds.yaml and compare the identity hashes.
#   prove.sh quick   part 3 only (steps 52-79) on a copy cut after step 51
#   prove.sh full    part 1's --to (step 50), part 2 (51), part 3 (52-79)
set -euo pipefail
MODE=$1
ORIG=$HOME/protoemu-flow/aq-search/runs/protoemu-exp-aqp-rel-2c97c15   # read-only
S=$HOME/protoemu-flow/ci-split
IMG=ghcr.io/librelane/librelane:3.1.0.dev3
PDK_ROOT=$HOME/ttpdk
log(){ echo "[$(date -u +%FT%TZ)] $*"; }

# The container command tt_tool.py --harden's --dockerized LibreLane runs (as in
# the release run's exp.log, plus --hide-progress-bar as under CI), with the
# copy mounted at the original's path so the run directory's absolute paths
# resolve to the copy; nice 19 inside the container.
ll() {
  local copy=$1; shift
  log "librelane $*"
  docker run --rm -i --user "$(id -u):$(id -g)" -v "$HOME:$HOME" -v "$PDK_ROOT:$PDK_ROOT" -e "PDK_ROOT=$PDK_ROOT" \
    -v "$copy:$ORIG" -w "$ORIG" "$IMG" nice -n 19 python3 -m librelane --pdk-root "$PDK_ROOT" --pdk ihp-sg13cmos5l \
    --manual-pdk --run-tag wokwi --force-run-dir runs/wokwi --hide-progress-bar "$@" src/config_merged.json
  log "librelane exit $?"
}
# The artifact hand-off as gds.yaml does it: pack, then unpack in place and drop final/.
handoff() {
  local copy=$1
  ( cd "$copy" && nice -n 19 tar --zstd --format=posix -cf "$S/handoff-$MODE.tar.zst" runs/wokwi && ls -l "$S/handoff-$MODE.tar.zst" \
    && rm -rf runs/wokwi && nice -n 19 tar --zstd -xf "$S/handoff-$MODE.tar.zst" && rm "$S/handoff-$MODE.tar.zst" && rm -rf runs/wokwi/final )
  log "handoff done"
}
check() {
  local copy=$1
  ( cd "$copy" && python3 flow/release_expected.py . evidence/8x4-release-expected.json ) || log "release_expected FAILED"
  diff <(ls "$ORIG/runs/wokwi") <(ls "$copy/runs/wokwi") && log "step directory names identical"
  diff "$ORIG/runs/wokwi/final/metrics.csv" "$copy/runs/wokwi/final/metrics.csv" > "$S/metrics-$MODE.diff" && log "final/metrics.csv identical" || log "final/metrics.csv differs ($(wc -l < "$S/metrics-$MODE.diff") diff lines)"
  ( cd "$ORIG/runs/wokwi/final" && find . -type f ! -name commit_id.json | sort | xargs sha256sum ) > "$S/final-orig.sha"
  ( cd "$copy/runs/wokwi/final" && find . -type f | sort | xargs sha256sum ) > "$S/final-$MODE.sha"
  diff "$S/final-orig.sha" "$S/final-$MODE.sha" > "$S/final-$MODE.diff" && log "every final/ file identical" || { log "final/ files that differ:"; cat "$S/final-$MODE.diff"; }
}

C=$S/$MODE
log "copy $ORIG -> $C"
nice -n 19 cp -a "$ORIG" "$C"
cd "$C/runs/wokwi"
if [ "$MODE" = quick ]; then
  rm -rf 5[2-9]-* 6[0-9]-* 7[0-9]-* final pdk.json
  handoff "$C"; cd "$C"
  ll "$C" --from Odb.RemoveRoutingObstructions --with-initial-state runs/wokwi/51-project-detailedrouting/state_out.json
else
  rm -rf 5[0-9]-* 6[0-9]-* 7[0-9]-* final pdk.json
  # part 1's stop: run step 50 alone (steps 1-49 kept) with part 1's --to
  ll "$C" --from OpenROAD.STAMidPNR-3 --to OpenROAD.STAMidPNR-3 --with-initial-state runs/wokwi/49-project-repairdesignthenhold2/state_out.json
  test -f "$C/runs/wokwi/50-openroad-stamidpnr-3/state_out.json" && test ! -e "$C/runs/wokwi/51-project-detailedrouting" && log "part 1 stopped after 50-openroad-stamidpnr-3"
  handoff "$C"; cd "$C"
  ll "$C" --from Project.DetailedRouting --to Project.DetailedRouting --with-initial-state runs/wokwi/50-openroad-stamidpnr-3/state_out.json
  test -f "$C/runs/wokwi/51-project-detailedrouting/state_out.json" && test ! -e "$C/runs/wokwi/52-odb-removeroutingobstructions" && log "part 2 stopped after 51-project-detailedrouting"
  handoff "$C"; cd "$C"
  ll "$C" --from Odb.RemoveRoutingObstructions --with-initial-state runs/wokwi/51-project-detailedrouting/state_out.json
fi
check "$C"
log "done"
