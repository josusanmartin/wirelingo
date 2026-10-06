#!/bin/bash
# Timing-annotated gate-level tests of a hardened run: the final netlist
# (final/nl, as flow/gl_test.sh) back-annotated with the run's extracted SDF
# (final/sdf/<corner>) through flow/sdf_prep.py, the PDK models' timing views,
# Icarus Verilog.
#
#   flow/gl_sdf_test.sh <run worktree>                        # the standard cases
#   flow/gl_sdf_test.sh <run worktree> CORNER CLK_NS [fail]   # one case
#
# Standard cases (run in parallel, ~6 minutes):
#   typ  20 ns (50 MHz)     $TESTS              must pass  (STA: setup met at typ)
#   slow 23 ns (43.5 MHz)   $TESTS              must pass  (STA: -2.88 ns at 20 ns)
#   slow 12.5 ns (80 MHz)   test_fetch_to_pin   must FAIL  (the annotation is in effect)
# CORNER is typ, slow or fast (final/sdf/nom_<corner>_*). A "fail" case passes
# only if the simulation ran with a clean annotation and at least one test
# failed an assertion.
#
# Tests: this checkout's test/test.py (PE_CLK_NS sets its clock) on the run's
# netlist, so the run's src/ must equal this checkout's (configs aside).
# TESTS="test_id test_uart_tx" overrides the subset; MODULE=test_sigrok runs
# another cocotb module (e.g. TESTS="sigrok_uart_tx sigrok_spi_master").
# Every case builds in a fresh directory under test/sim_build/gl_sdf.XXXXXX
# (the run's directory is only read). The script fails on any case that is
# not as required, on any SDF or VPI error in a simulator log, or on a
# missing result. docs/physical.md (Gate-level simulation) says what this
# checks and what it doesn't.
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
echo "tests ($MODULE): $TESTS"

# one case: corner, clock period (ns), expect (pass|fail), tests
run_case() {
  local corner=$1 clk=$2 expect=$3 tests=$4 sdf name dir t0 rc=0 count
  sdf=("$W"/runs/wokwi/final/sdf/nom_"$corner"_*/*.sdf)
  [[ ${#sdf[@]} == 1 ]] || { echo "[$corner] expected one SDF for corner $corner"; return 2; }
  name=${corner}_${clk}ns_$expect
  dir=$OUT/$name
  mkdir -p "$dir"
  t0=$(date +%s)
  python3 "$REPO/flow/sdf_prep.py" "$NL" "${sdf[0]}" "$dir/netlist.v" "$dir/annotate.sdf" > "$dir/sdf_prep.log"
  # COCOTB_TESTCASE: comma-separated names (a COCOTB_TEST_FILTER alternation
  # would need | and $, which cocotb's makefile passes to the shell unquoted)
  local testcase
  testcase=$(echo "$tests" | xargs | tr ' ' ',')
  count=$(echo "$tests" | wc -w)
  (cd "$REPO/test" && PE_CLK_NS=$clk PE_NCORES=$NC PE_NBANKS=$NB PE_FIFO_LOG2=$FL make GATES=yes \
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
  run_case "$2" "$3" "$([[ ${4:-} == fail ]] && echo fail || echo pass)" "$TESTS"
  exit
fi
pids=()
run_case typ 20 pass "$TESTS"                > "$OUT/typ.txt"  2>&1 & pids+=($!)
run_case slow 23 pass "$TESTS"               > "$OUT/slow.txt" 2>&1 & pids+=($!)
run_case slow 12.5 fail "test_fetch_to_pin"  > "$OUT/fail.txt" 2>&1 & pids+=($!)
status=0
for p in "${pids[@]}"; do wait "$p" || status=1; done
cat "$OUT/typ.txt" "$OUT/slow.txt" "$OUT/fail.txt"
[[ $status == 0 ]] && echo "gl_sdf_test: all cases as required" || echo "gl_sdf_test: FAILED"
exit $status
