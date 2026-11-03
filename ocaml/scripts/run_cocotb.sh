#!/usr/bin/env bash
# Run the repo's cocotb suites on the Hardcaml-generated Verilog (docs/hardcaml-port.md).
#
#   ocaml/scripts/run_cocotb.sh WORK SUITE...
#
# WORK: a new directory, a throwaway copy of test/ tools/ firmware/ src/ at HEAD.
# SUITE: a cocotb module (test, test_post_freeze, test_pio, ...), or
#        lockstep        RTL-vs-model lockstep, CI's tapeout configuration
#        lockstep4       RTL-vs-model lockstep, CI's 4-core flop configuration
#        lockstep3       as lockstep, with the OCaml model in the loop every cycle
#        lockstep3src    the same on src/ itself instead of the generated Verilog
# Tests and test/Makefile are not edited: SRC_DIR and PROJECT_SOURCES are
# overridden on the make command line. Each suite's summary goes to stdout and
# its log to WORK/<suite>.log. Exit status 0 only if every suite passed.
#
# Needs gen_rtl built (dune build in ocaml/), and OSS CAD Suite at $OSS_CAD
# (default ~/tools/oss-cad-suite).
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../.." && pwd)
gen_rtl=$repo/ocaml/_build/default/bin/gen_rtl.exe
work=$1; shift
export PATH="${OSS_CAD:-$HOME/tools/oss-cad-suite}/bin:$PATH"
[ -x "$gen_rtl" ] || { echo "build first: cd ocaml && dune build" >&2; exit 2; }
mkdir -p "$work"
git -C "$repo" archive HEAD test tools firmware src | tar -x -C "$work"
for build in tapeout flop4; do
  mkdir -p "$work/gen/$build"
  "$gen_rtl" $build "$work/gen/$build"
  cp "$repo/src/pe_cg.sv" "$repo/src/pe_config.vh" "$work/gen/$build/"
done
cd "$work/test" || exit 2
fail=0
top_args=(SRC_DIR="$work/gen/tapeout" PROJECT_SOURCES="wirelingo_hc.v pe_cg.sv")
for s in "$@"; do
  case $s in
    lockstep)
      LOCKSTEP_SEEDS=${LOCKSTEP_SEEDS:-40} LOCKSTEP_CYCLES=${LOCKSTEP_CYCLES:-1000} \
        PE_NCORES=2 PE_NBANKS=2 PE_FIFO_LOG2=3 timeout 7200 make TB=chip \
        SRC_DIR="$work/gen/tapeout" PROJECT_SOURCES="pe_chip_lockstep.v pe_chip_impl.v pe_cg.sv" \
        SIM_BUILD=sim_build/lk COCOTB_RESULTS_FILE=lk.xml > "$work/$s.log" 2>&1
      rc=$? ;;
    lockstep4)
      LOCKSTEP_GOALS=core LOCKSTEP_SEEDS=${LOCKSTEP_SEEDS:-30} LOCKSTEP_CYCLES=${LOCKSTEP_CYCLES:-1000} \
        timeout 7200 make TB=chip IMEM=flop \
        SRC_DIR="$work/gen/flop4" PROJECT_SOURCES="pe_chip_lockstep.v pe_chip_impl.v pe_cg.sv" \
        SIM_BUILD=sim_build/lk4 COCOTB_RESULTS_FILE=lk4.xml > "$work/$s.log" 2>&1
      rc=$? ;;
    lockstep3|lockstep3src)
      chip_args=(SRC_DIR="$work/gen/tapeout" PROJECT_SOURCES="pe_chip_lockstep.v pe_chip_impl.v pe_cg.sv")
      [ "$s" = lockstep3src ] && chip_args=()
      PYTHONPATH="$repo/ocaml/test:$work/test" WL_LOCK_OUT="$work/$s" \
        LOCKSTEP_SEEDS=${LOCKSTEP_SEEDS:-40} LOCKSTEP_CYCLES=${LOCKSTEP_CYCLES:-1000} \
        PE_NCORES=2 PE_NBANKS=2 PE_FIFO_LOG2=3 timeout 7200 make TB=chip "${chip_args[@]}" \
        COCOTB_TEST_MODULES=test_lockstep_ocaml \
        SIM_BUILD="sim_build/$s" COCOTB_RESULTS_FILE="$s.xml" > "$work/$s.log" 2>&1
      rc=$? ;;
    *)
      timeout 7200 make "${top_args[@]}" COCOTB_TEST_MODULES="$s" SIM_BUILD="sim_build/$s" \
        COCOTB_RESULTS_FILE="$s.xml" > "$work/$s.log" 2>&1
      rc=$? ;;
  esac
  echo "$s rc=$rc"
  grep -E 'TESTS=| FAIL ' "$work/$s.log"
  # CI's own check: the results file exists, has every test, none failed or skipped
  case $s in
    lockstep) xml=lk.xml; mod=test_lockstep ;;
    lockstep4) xml=lk4.xml; mod=test_lockstep ;;
    lockstep3|lockstep3src) xml=$s.xml; mod=$repo/ocaml/test/test_lockstep_ocaml ;;
    *) xml=$s.xml; mod=$s ;;
  esac
  n=$(grep -c '^@cocotb.test()' "$mod.py")
  [ "$s" = lockstep3 ] || [ "$s" = lockstep3src ] && n=$((n + 6))
  python3 check_results.py "$xml" "$n" || rc=1
  [ $rc -eq 0 ] || fail=1
done
exit $fail
