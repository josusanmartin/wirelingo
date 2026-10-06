#!/bin/bash
# Timing-annotated gate-level tests of a hardened run: the final netlist
# (final/nl, as flow/gl_test.sh) back-annotated with the run's extracted SDF
# (final/sdf/<corner>) through flow/sdf_prep.py, the PDK models' timing views,
# Icarus Verilog.
#
#   flow/gl_sdf_test.sh <run worktree>                        # the standard (fast) set
#   flow/gl_sdf_test.sh <run worktree> v2                     # the v2-feature set
#   flow/gl_sdf_test.sh <run worktree> v2long                 # the v2 long tests
#   flow/gl_sdf_test.sh <run worktree> CORNER CLK_NS [fail|report]   # one case
#
# Standard set (run in parallel, ~6-15 minutes):
#   typ  20 ns (50 MHz)     $TESTS              must pass  (STA: setup met at typ)
#   slow 23 ns (43.5 MHz)   $TESTS              must pass  (STA, v2c: -1.57 ns at 20 ns)
#   slow 12.5 ns (80 MHz)   test_fetch_to_pin   must FAIL  (the annotation is in effect)
# v2 set (the v2 features; in parallel, ~35 minutes, the USB cases set it):
#   typ  20 ns       test_spi_master_fast test_la_capture_ticks test_call_ret   must pass
#   typ  20.834 ns   test_usb_fs_device (48 MHz, USB's own clock)              must pass
#   slow 23 ns       test_spi_master_fast test_la_capture_ticks test_call_ret   must pass
#   slow 23 ns       test_usb_fs_device, bus scaled to the clock               must pass
#   slow 20.834 ns   test_usb_fs_device at 48 MHz        report only (STA, v2c: needs 21.57 ns)
# v2long (the full-length originals the directed tests shorten; ~45-60 minutes):
#   typ 20 ns and slow 23 ns, each of test_la_capture_rate, test_reverse_engineering   must pass
# CORNER is typ, slow or fast (final/sdf/nom_<corner>_*). A "fail" case passes
# only if the simulation ran with a clean annotation and at least one test
# failed an assertion; a "report" case prints its result and never fails the
# script (a clean annotation is still required).
#
# Tests: this checkout's test/test.py on the run's netlist, so the run's src/
# must equal this checkout's (configs aside). CLK_NS sets every test's clock
# (PE_CLK_NS), test_usb_fs_device's too (PE_USB_FS_CLK_NS: its bus keeps 4
# clocks per bit, so away from 20.834 ns it is not 12 Mbit/s).
# TESTS="test_id test_uart_tx" overrides the standard subset (and a single
# case's); MODULE=test_sigrok runs another cocotb module (e.g.
# TESTS="sigrok_uart_tx sigrok_spi_master").
# Every case builds in a fresh directory under test/sim_build/gl_sdf.XXXXXX
# (the run's directory is only read). The script fails on any case that is
# not as required, on any SDF or VPI error in a simulator log, or on a
# missing result. docs/physical.md (Timing-annotated gate level) says what
# this checks and what it doesn't.
set -euo pipefail
W=$(realpath "${1:-$PWD}")
REPO=$(git -C "$(dirname "$0")" rev-parse --show-toplevel)
shopt -s nullglob
NETLISTS=("$W"/runs/wokwi/final/nl/*.nl.v)
[[ ${#NETLISTS[@]} == 1 ]] || { echo "expected exactly one final netlist in $W"; exit 2; }
NL=${NETLISTS[0]}
if ! diff -rq -x 'config*.json' -x user_config.json "$W/src" "$REPO/src" > /dev/null; then
  echo "$W/src differs from $REPO/src: the tests here would not describe that netlist"; exit 2
fi
def() { { grep -o "define $1 [0-9]" "$W/src/project.v" || true; } | awk '{print $3}' | head -1; }
NC=$(def PE_NCORES); FL=$(def PE_FIFO_LOG2); FL=${FL:-2}
NB=$(grep -q 'define PE_NBANKS `PE_NCORES' "$W/src/project.v" && echo "$NC" || def PE_NBANKS)
TESTS=${TESTS:-test_id test_uart_tx test_spi_master test_host_port_fast test_fetch_to_pin}
MODULE=${MODULE:-test}
VENV=$REPO/.venv
[[ -d $VENV ]] || VENV=$(git -C "$REPO" worktree list --porcelain | awk 'NR==1{print $2}')/.venv   # worktrees: main's
export PATH=$HOME/tools/oss-cad-suite/bin:$VENV/bin:$PATH PDK_ROOT=${PDK_ROOT:-$HOME/ttpdk}
mkdir -p "$REPO/test/sim_build"
OUT=$(mktemp -d "$REPO/test/sim_build/gl_sdf.XXXXXX")
echo "netlist $(sha256sum "$NL" | cut -c1-16)  cores=$NC banks=$NB fifo_log2=$FL  outputs $OUT"
[[ $# -ge 3 || ${2:-standard} == standard ]] && echo "tests ($MODULE): $TESTS"

# one case: corner, clock period (ns), expect (pass|fail|report), tests
run_case() {
  local corner=$1 clk=$2 expect=$3 tests=$4 sdf name dir t0 rc=0 count
  sdf=("$W"/runs/wokwi/final/sdf/nom_"$corner"_*/*.sdf)
  [[ ${#sdf[@]} == 1 ]] || { echo "[$corner] expected one SDF for corner $corner"; return 2; }
  name=${corner}_${clk}ns_$expect
  [[ $tests == *" "* ]] || name=${name}_${tests#test_}
  dir=$OUT/$name
  mkdir -p "$dir"
  t0=$(date +%s)
  python3 "$REPO/flow/sdf_prep.py" "$NL" "${sdf[0]}" "$dir/netlist.v" "$dir/annotate.sdf" > "$dir/sdf_prep.log"
  # COCOTB_TESTCASE: comma-separated names (a COCOTB_TEST_FILTER alternation
  # would need | and $, which cocotb's makefile passes to the shell unquoted)
  local testcase
  testcase=$(echo "$tests" | xargs | tr ' ' ',')
  count=$(echo "$tests" | wc -w)
  (cd "$REPO/test" && PE_CLK_NS=$clk PE_USB_FS_CLK_NS=$clk PE_NCORES=$NC PE_NBANKS=$NB PE_FIFO_LOG2=$FL make GATES=yes \
      SDF="$dir/annotate.sdf" GATE_LEVEL_NETLIST="$dir/netlist.v" COCOTB_TEST_MODULES="$MODULE" \
      COCOTB_TESTCASE="$testcase" SIM_BUILD="$dir/build" COCOTB_RESULTS_FILE="$dir/results.xml" \
      > "$dir/sim.log" 2>&1) || rc=$?
  local secs=$(( $(date +%s) - t0 ))
  # a clean annotation: no SDF errors (the header's empty typ VOLTAGE and
  # TEMPERATURE fields aside) and no VPI errors (e.g. an unplaceable net delay)
  local bad
  bad=$(grep -E "SDF ERROR|VPI error|Could not insert|Cannot find" "$dir/sim.log" | grep -vE "annotate.sdf:(9|11): Chosen value not defined" | head -5 || true)
  if [[ -n $bad ]]; then echo "[$name] annotation errors (log $dir/sim.log):"; echo "$bad"; return 1; fi
  grep -q "^sdf_annotate: $dir/annotate.sdf" "$dir/sim.log" || { echo "[$name] \$sdf_annotate did not run (log $dir/sim.log)"; return 1; }
  local summary
  summary=$(grep -E "^\s*\*\* TESTS=" "$dir/sim.log" | sed 's/^ *//' || true)
  grep -E "cocotb.regression +\S+\.\S+ (passed|failed)" "$dir/sim.log" | awk -v n="$name" '{print "["n"] "$(NF-1)" "$NF}' || true
  if [[ $expect == report ]]; then
    local verdict=FAILED
    [[ $rc == 0 ]] && python3 "$REPO/test/check_results.py" "$dir/results.xml" "$count" $tests > /dev/null && verdict=PASSES
    echo "[$name] $verdict (report only): ${summary:-no results} (${secs} s, log $dir/sim.log)"; return 0
  fi
  if [[ $expect == pass ]]; then
    if [[ $rc == 0 ]] && python3 "$REPO/test/check_results.py" "$dir/results.xml" "$count" $tests > /dev/null; then
      echo "[$name] PASS as required: $summary (${secs} s)"; return 0
    fi
    echo "[$name] FAILED, required to pass: ${summary:-no results} (${secs} s, log $dir/sim.log)"; return 1
  fi
  # expect fail: the simulation must have completed with assertion failures
  if [[ -f $dir/results.xml ]] && grep -q "<failure" "$dir/results.xml"; then
    echo "[$name] FAILS as required: $summary (${secs} s)"; return 0
  fi
  echo "[$name] required to fail, but: ${summary:-no results} (${secs} s, log $dir/sim.log)"; return 1
}

if [[ $# -ge 3 ]]; then
  expect=pass
  [[ ${4:-} == fail || ${4:-} == report ]] && expect=$4
  run_case "$2" "$3" "$expect" "$TESTS"
  exit
fi
# the cases of a set: "corner clk expect tests...", one per line
SET=${2:-standard}
case $SET in
  standard) CASES="typ 20 pass $TESTS
slow 23 pass $TESTS
slow 12.5 fail test_fetch_to_pin" ;;
  v2) V2="test_spi_master_fast test_la_capture_ticks test_call_ret"
      CASES="typ 20 pass $V2
typ 20.834 pass test_usb_fs_device
slow 23 pass $V2
slow 23 pass test_usb_fs_device
slow 20.834 report test_usb_fs_device" ;;
  v2long) CASES="typ 20 pass test_la_capture_rate
typ 20 pass test_reverse_engineering
slow 23 pass test_la_capture_rate
slow 23 pass test_reverse_engineering" ;;
  *) echo "unknown set $SET (standard, v2, v2long; or CORNER CLK_NS [fail|report])"; exit 2 ;;
esac
[[ $SET == standard ]] || echo "set $SET"
pids=() outs=() i=0
while read -r corner clk expect tests; do
  outs+=("$OUT/case$i.txt")
  run_case "$corner" "$clk" "$expect" "$tests" > "$OUT/case$i.txt" 2>&1 & pids+=($!)
  i=$((i + 1))
done <<< "$CASES"
status=0
for p in "${pids[@]}"; do wait "$p" || status=1; done
cat "${outs[@]}"
[[ $status == 0 ]] && echo "gl_sdf_test ($SET): all cases as required" || echo "gl_sdf_test ($SET): FAILED"
exit $status
