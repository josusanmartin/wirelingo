#!/usr/bin/env bash
# Run the repo's cocotb suites on the Hardcaml-generated Verilog (docs/hardcaml-port.md).
#
#   ocaml/scripts/run_cocotb.sh WORK SUITE...
#
# WORK: a new directory, a throwaway copy of test/ tools/ firmware/ src/ at HEAD.
# SUITE: a cocotb module (test, test_post_freeze, test_pio, ...), run on the build
#        CI runs it on (the 8x4 tapeout's 3 cores, or the 2-core fallback for the
#        four host-transport suites), or
#        test2           test.py on the 2-core fallback (CI's second top-level run)
#        lockstep        RTL-vs-model lockstep, CI's tapeout configuration (3 cores)
#        lockstep2       the same, CI's fallback configuration (2 cores)
#        lockstep4       RTL-vs-model lockstep, CI's 4-core flop configuration
#        lockstep3       as lockstep, with the OCaml model in the loop every cycle
#        lockstep3fb     as lockstep2, with the OCaml model in the loop every cycle
#        lockstep3src    lockstep3 on src/ itself instead of the generated Verilog
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
for build in tapeout fallback flop4; do
  mkdir -p "$work/gen/$build"
  "$gen_rtl" $build "$work/gen/$build"
  cp "$repo/src/pe_cg.sv" "$repo/src/pe_config.vh" "$work/gen/$build/"
done
cd "$work/test" || exit 2
fail=0
chip_srcs="pe_chip_lockstep.v pe_chip_impl.v pe_cg.sv"
for s in "$@"; do
  case $s in
    lockstep|lockstep2)
      if [ "$s" = lockstep ]; then b=tapeout nc=3; else b=fallback nc=2; fi
      LOCKSTEP_SEEDS=${LOCKSTEP_SEEDS:-40} LOCKSTEP_CYCLES=${LOCKSTEP_CYCLES:-1000} \
        PE_NCORES=$nc PE_NBANKS=$nc PE_FIFO_LOG2=3 timeout 7200 make TB=chip \
        SRC_DIR="$work/gen/$b" PROJECT_SOURCES="$chip_srcs" \
        SIM_BUILD="sim_build/$s" COCOTB_RESULTS_FILE="$s.xml" > "$work/$s.log" 2>&1
      rc=$? ;;
    lockstep4)
      LOCKSTEP_GOALS=core LOCKSTEP_SEEDS=${LOCKSTEP_SEEDS:-30} LOCKSTEP_CYCLES=${LOCKSTEP_CYCLES:-1000} \
        timeout 7200 make TB=chip IMEM=flop \
        SRC_DIR="$work/gen/flop4" PROJECT_SOURCES="$chip_srcs" \
        SIM_BUILD=sim_build/lk4 COCOTB_RESULTS_FILE=lockstep4.xml > "$work/$s.log" 2>&1
      rc=$? ;;
    lockstep3|lockstep3fb|lockstep3src)
      if [ "$s" = lockstep3fb ]; then b=fallback nc=2; else b=tapeout nc=3; fi
      chip_args=(SRC_DIR="$work/gen/$b" PROJECT_SOURCES="$chip_srcs")
      [ "$s" = lockstep3src ] && chip_args=()
      PYTHONPATH="$repo/ocaml/test:$work/test" WL_LOCK_OUT="$work/$s" \
        LOCKSTEP_SEEDS=${LOCKSTEP_SEEDS:-40} LOCKSTEP_CYCLES=${LOCKSTEP_CYCLES:-1000} \
        PE_NCORES=$nc PE_NBANKS=$nc PE_FIFO_LOG2=3 timeout 7200 make TB=chip "${chip_args[@]}" \
        COCOTB_TEST_MODULES=test_lockstep_ocaml \
        SIM_BUILD="sim_build/$s" COCOTB_RESULTS_FILE="$s.xml" > "$work/$s.log" 2>&1
      rc=$? ;;
    test2|test_spi_packed_host|test_spi_preloaded_host|test_uart_capture_host|test_usb_uart_rtl)
      # CI runs these on the 2-core profile (test2: CI's top2 step)
      mod=$s; [ "$s" = test2 ] && mod=test
      timeout 7200 make PE_NCORES=2 PE_NBANKS=2 SRC_DIR="$work/gen/fallback" \
        PROJECT_SOURCES="wirelingo_hc.v pe_cg.sv" COCOTB_TEST_MODULES="$mod" SIM_BUILD="sim_build/$s" \
        COCOTB_RESULTS_FILE="$s.xml" > "$work/$s.log" 2>&1
      rc=$? ;;
    *)
      timeout 7200 make SRC_DIR="$work/gen/tapeout" PROJECT_SOURCES="wirelingo_hc.v pe_cg.sv" \
        COCOTB_TEST_MODULES="$s" SIM_BUILD="sim_build/$s" \
        COCOTB_RESULTS_FILE="$s.xml" > "$work/$s.log" 2>&1
      rc=$? ;;
  esac
  echo "$s rc=$rc"
  grep -E 'TESTS=| FAIL ' "$work/$s.log"
  # CI's own check: the results file exists, has every test, none failed or skipped
  xml=$s.xml
  case $s in
    lockstep|lockstep2|lockstep4) mod=test_lockstep ;;
    lockstep3*) mod=$repo/ocaml/test/test_lockstep_ocaml ;;
    test2) mod=test ;;
    *) mod=$s ;;
  esac
  n=$(grep -c '^@cocotb.test()' "$mod.py")
  # test_lockstep_ocaml.py imports test_lockstep.py's tests and adds its own
  case $s in lockstep3*) n=$((n + $(grep -c '^@cocotb.test()' test_lockstep.py))) ;; esac
  # Two suites have a test that skips by design; CI's test.yaml names the ones
  # that must pass, and so does this (bench_sigrok_available needs sigrok-cli,
  # pps_one_second needs PPS_LONG=1).
  names=()
  case $s in
    test_bench_fw) names=(bench_mdio_master_and_phy bench_mdio_master_phy_model bench_mdio_phy_sta_model
                          bench_qsfp bench_pmbus) ;;
    test_pps) names=(pps_record_paths pps_rollover_phases pps_train pps_fifo_full) ;;
  esac
  python3 check_results.py "$xml" "$n" "${names[@]}" || rc=1
  [ $rc -eq 0 ] || fail=1
done
exit $fail
