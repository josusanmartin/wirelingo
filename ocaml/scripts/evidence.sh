#!/usr/bin/env bash
# Every gate of docs/hardcaml-port.md, from a clean build of the committed tree.
#
#   ocaml/scripts/evidence.sh OUT
#
# OUT: a new directory. Each step's log lands there, summary.txt collects the
# result lines. Exit status 0 only if all passed; the parity and model scripts
# also fail below their coverage floors.
#
# Three phases, each at about 10 processes, so that the run takes about half of a
# 20-core host: the proofs (3-core tapeout and 2-core fallback) with the cocotb
# suites; the model suite with the host-tool parity checks; the assembler parity
# (which reads the model suite's corpus) with self_check.sh.
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
} > "$out/build.txt" 2>&1
start=$(date +%s)
fail=0
if ! dune build -j 8 >> "$out/build.txt" 2>&1; then echo "dune build failed"; exit 1; fi
dune test -j 8 >> "$out/build.txt" 2>&1
echo "dune_test rc=$?" >> "$out/rc.txt"
step() {                      # step NAME COMMAND...: in the background, log to OUT/NAME.log
  local name=$1; shift
  ( "$@" > "$out/$name.log" 2>&1; echo "$name rc=$?" >> "$out/rc.txt" ) &
}
phase() { wait; echo "phase $1 done at $(($(date +%s) - start)) s" >> "$out/build.txt"; }
# phase 1: 2 + 2 + 6 processes
step equiv formal/equiv_port.sh -j 2 --mutants "$out/equiv"
step equiv2 formal/equiv_port.sh -j 2 --mutants --build fallback "$out/equiv2"
step cocotb_a scripts/run_cocotb.sh "$out/wa" test test_post_freeze test2
step cocotb_b scripts/run_cocotb.sh "$out/wb" lockstep lockstep2 test_pio test_eth_service \
  test_spi_packed_host test_spi_preloaded_host test_uart_capture_host test_usb_uart_rtl
step cocotb_f scripts/run_cocotb.sh "$out/wf" lockstep4
step cocotb_c scripts/run_cocotb.sh "$out/wc" lockstep3 lockstep3fb lockstep3src
step cocotb_e scripts/run_cocotb.sh "$out/we" test_bench_fw test_pps
step cocotb_d env LOCKSTEP_SEED0=5000 LOCKSTEP_SEEDS=100 scripts/run_cocotb.sh "$out/wd" lockstep3
phase 1
# phase 2: 7 + 3
step model env WL_JOBS=7 scripts/stage3_model_suite.sh "$out/model" 64
step host python3 test/host_parity.py
step run python3 test/run_parity.py
step cyclesim _build/default/test/line_cyclesim.exe 2000000 7
phase 2
# phase 3: 1 + up to 4
step asm python3 test/asm_parity.py --fuzz 5000 --corpus "$out"/model/asm.*.jsonl
step self test/self_check.sh "$out/self"
phase 3
echo "wall time $(($(date +%s) - start)) s" >> "$out/build.txt"
cat "$out/rc.txt"
grep -v ' rc=0$' "$out/rc.txt" && fail=1
{
  cat "$out/build.txt"
  sort "$out/rc.txt"
  sed -n '/^==== port equivalence/,/^VERDICT/p' "$out/equiv.log" "$out/equiv2.log"
  cat "$out"/cocotb_?.log
  grep -E 'passed|skipped|lockstep totals|tests with a mismatch|stride 1|BELOW' "$out/model.log"
  tail -9 "$out/asm.log"
  tail -3 "$out/host.log"
  tail -1 "$out/run.log"
  tail -1 "$out/cyclesim.log"
  tail -1 "$out/self.log"
} > "$out/summary.txt"
exit $fail
