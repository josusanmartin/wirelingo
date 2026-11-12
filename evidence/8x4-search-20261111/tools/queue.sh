#!/bin/bash
# AQ search queue (runner1): one harden at a time. Reads queue.txt, one run per
# line, "NAME [NCORES] [-- gen_config options]" (the arguments of flow/harden.sh);
# blank lines and # comments are skipped. A line runs when results/NAME/done is
# absent, so lines can be appended while the queue runs. Per run: wait for no
# other harden and >= MIN_FREE_GB free, harden, copy the small results, fresh STA
# at 20.832 ns (flow/sta_rerun.py, the 48 MHz release period), worst flop/SRAM
# endpoint at 20 ns (syn/8x4-study/sta_period.sh), then keep only the top 2 run
# worktrees of the run's search (prune.py; aqf* = fallback, aqp* = package).
set -u
B=$HOME/protoemu-flow/aq-search
REPO=$B/repo
MIN_FREE_GB=${MIN_FREE_GB:-15}
export PROTOEMU_EXP_DIR=$B/runs PDK_ROOT=$HOME/ttpdk
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$B/queue.log"; }
next() {
  while read -r line; do
    line=${line%%#*}
    [ -z "${line// }" ] && continue
    n=${line%% *}
    [ -e "$B/results/$n/done" ] && continue
    [ -e "$B/results/$n/skip" ] && continue
    echo "$line"; return 0
  done < "$B/queue.txt"
  return 1
}
while spec=$(next); do
  name=${spec%% *}
  R=$B/runs/protoemu-exp-$name
  D=$B/results/$name
  mkdir -p "$D"
  while pgrep -f "flow/harden[.]sh" > /dev/null; do sleep 60; done
  warned=0
  while [ "$(df --output=avail -BG "$HOME" | tail -1 | tr -dc 0-9)" -lt "$MIN_FREE_GB" ]; do
    [ $warned = 0 ] && log "$name waiting: under $MIN_FREE_GB GB free"
    warned=1; sleep 600
  done
  df -h "$HOME" | tail -1 > "$D/df-before.txt"
  echo "$spec" > "$D/spec.txt"
  log "$name start: $spec"
  t0=$(date +%s)
  # shellcheck disable=SC2086
  (cd "$REPO" && flow/harden.sh $spec) > "$D/harden.out" 2>&1
  rc=$?
  log "$name harden rc=$rc ($(( ($(date +%s) - t0) / 60 )) min)"
  echo "harden rc=$rc minutes=$(( ($(date +%s) - t0) / 60 ))" > "$D/time.txt"
  cp "$R/result.txt" "$R/src/config.json" "$D/" 2>/dev/null
  cp "$R/runs/wokwi/final/metrics.json" "$R/runs/wokwi/resolved.json" "$D/" 2>/dev/null
  cp "$R"/runs/wokwi/*-openroad-stapostpnr/summary.rpt "$D/sta-summary.rpt" 2>/dev/null
  head -400 "$R"/runs/wokwi/*-openroad-stapostpnr/nom_slow_1p08V_125C/max.rpt > "$D/slow-max-head.rpt" 2>/dev/null
  head -200 "$R"/runs/wokwi/*-openroad-stapostpnr/nom_fast_1p32V_m40C/min.rpt > "$D/fast-min-head.rpt" 2>/dev/null
  grep -h "DPL-0022\|GPL-0019\|LATE_CLOCK" "$R/exp.log" > "$D/placement.txt" 2>/dev/null
  grep -c "GRT-0101\|Start routing overflow\|overflow iterations" "$R/exp.log" > "$D/grt-rounds.txt" 2>/dev/null
  ls -1 "$R/runs/wokwi" 2>/dev/null | grep -E "^[0-9]+-" > "$D/steps.txt"
  tail -c 3000 "$R/exp.log" > "$D/exp-tail.log" 2>/dev/null
  gzip -c "$R/exp.log" > "$D/exp.log.gz" 2>/dev/null
  if [ -d "$R/runs/wokwi/final/nl" ]; then
    (cd "$REPO" && python3 flow/sta_rerun.py "$R" 20.832 --out-parent "$D/sta48") > "$D/sta48.out" 2>&1
    for f in "$D"/sta48/sta.*/sta/*/sta.log; do grep "^%OL_METRIC" "$f" > "${f%.log}.metrics"; done
    find "$D/sta48" \( -name sta.log -o -name "*.sdf" -o -name "*.lib" -o -name "max.rpt" -o -name "min.rpt" \
      -o -name "checks.rpt" -o -name "*.odb" -o -name "*.spef" -o -name "*.nl.v" \) -delete
    for try in 1 2 3; do
      sh "$REPO/syn/8x4-study/sta_period.sh" "$R" 20 > "$D/sta20-flop.txt" 2> "$D/sta20-flop.err"
      [ "$(grep -c setup_violating_endpoints "$D/sta20-flop.txt")" = 3 ] && break
      log "$name sta_period try $try incomplete"
    done
  fi
  touch "$D/done"
  python3 "$B/prune.py" "$name" 2>&1 | tee -a "$B/queue.log"
  df -h "$HOME" | tail -1 > "$D/df-after.txt"
done
log "queue empty"
