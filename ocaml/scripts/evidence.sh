#!/usr/bin/env bash
# Every gate of docs/hardcaml-port.md, from a clean build of the committed tree.
#
#   ocaml/scripts/evidence.sh OUT
#
# OUT: a new directory. Each step's log lands there, summary.txt collects the
# result lines. About an hour on runner1. Exit status 0 only if all passed.
# SPDX-License-Identifier: Apache-2.0
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
ocaml=$(cd "$here/.." && pwd)
out=$(realpath -m "$1")
mkdir -p "$out"
cd "$ocaml" || exit 2
{
  echo "commit $(git rev-parse HEAD)"
  echo "src tree $(git rev-parse HEAD:src)"
  yosys_bin=${OSS_CAD:-$HOME/tools/oss-cad-suite}/bin/yosys
  "$yosys_bin" -V
  ocamlfind ocamlopt -version 2>/dev/null
  rm -rf _build
  dune build 2>&1 | tail -5
  dune test 2>&1 | tail -2
} > "$out/build.txt" 2>&1
fail=0
step() {                      # step NAME COMMAND...: in the background, log to OUT/NAME.log
  local name=$1; shift
  ( "$@" > "$out/$name.log" 2>&1; echo "$name rc=$?" >> "$out/rc.txt" ) &
}
step equiv formal/equiv_port.sh -j 4 --mutants "$out/equiv"
step cocotb_a scripts/run_cocotb.sh "$out/wa" test test_post_freeze
step cocotb_b scripts/run_cocotb.sh "$out/wb" lockstep lockstep4 test_pio test_eth_service \
  test_spi_packed_host test_spi_preloaded_host test_uart_capture_host test_usb_uart_rtl
step cocotb_c scripts/run_cocotb.sh "$out/wc" lockstep3 lockstep3src
step cocotb_d env LOCKSTEP_SEED0=5000 LOCKSTEP_SEEDS=100 scripts/run_cocotb.sh "$out/wd" lockstep3
step model scripts/stage3_model_suite.sh "$out/model" 64
wait
step asm python3 test/asm_parity.py --fuzz 5000 --corpus "$out"/model/asm.*.jsonl
step host python3 test/host_parity.py
step run python3 test/run_parity.py
step cyclesim _build/default/test/line_cyclesim.exe 2000000 7
wait
cat "$out/rc.txt"
grep -v ' rc=0$' "$out/rc.txt" && fail=1
{
  cat "$out/build.txt"
  sort "$out/rc.txt"
  tail -13 "$out/equiv.log"
  cat "$out"/cocotb_?.log
  tail -5 "$out/model.log"
  tail -9 "$out/asm.log"
  tail -3 "$out/host.log"
  tail -1 "$out/run.log"
  tail -1 "$out/cyclesim.log"
} > "$out/summary.txt"
exit $fail
