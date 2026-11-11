#!/usr/bin/env bash
# Do the parity checks notice a broken port? Plant one bug at a time in a throwaway
# copy of ocaml/, rebuild, and require the matching check to report it.
#
#   ocaml/test/self_check.sh WORK
#
# Assembler bugs go to asm_parity.py, model bugs to the lockstep plugin on four
# model test files (and, for the state load after a test writes into a chip, on
# one test that does), RTL bugs to the waveform tests. Needs the same environment as dune build and stage3_model_suite.sh.
# First the unmutated tree runs every check once and must come out clean (no lockstep
# failure, every test passing, parity and waveform tests passing); otherwise a planted
# bug would look caught by a failure that was already there.
# Exit status 0 only if the baseline is clean and every planted bug is caught.
# SPDX-License-Identifier: Apache-2.0
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../.." && pwd)
work=$(realpath -m "$1")
py=${WL_PYTHON:-$HOME/protoemu-flow/test-venv/bin/python}
mkdir -p "$work"
git -C "$repo" archive HEAD | tar -x -C "$work"
cd "$work/ocaml" || exit 2
missed=0
sim_check() {                  # the model files every sim case runs; rc of pytest
  (cd .. && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tools:ocaml/test WL_LOCK_EVERY=1 WL_LOCK_OUT="$work/lock" \
    "$py" -m pytest -q -p no:cacheprovider -p capture_plugin -n 4 \
    test/model/test_eth10.py test/model/test_call.py test/model/test_uart.py test/model/test_i2c_timeout.py \
    test/model/test_v03_isa.py \
    > "$1" 2>&1)
}
resync_check() {               # resync_check R LOG: the written-into test, state load on (1) or off (0)
  (cd .. && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tools:ocaml/test WL_LOCK_EVERY=1 WL_LOCK_RESYNC=$1 \
    WL_LOCK_OUT="$work/lock" "$py" -m pytest -q -p no:cacheprovider -p capture_plugin \
    test/model/test_rmii.py::test_port_model_read_path_counts_every_word > "$2" 2>&1)
}
# baseline: the unmutated tree, every check once
dune build bin/wl_asm.exe bin/wl_simcheck.exe 2>"$work/build.err" || { echo "baseline does not build"; cat "$work/build.err"; exit 1; }
base_bad=0
rm -f "$work"/lock.*
sim_check "$work/base_sim.log"; rc=$?
ls "$work"/lock.fail.* >/dev/null 2>&1 && { echo "baseline sim: lockstep failure"; base_bad=1; }
echo "baseline sim: pytest rc=$rc, $(tail -n 1 "$work/base_sim.log")"; [ $rc -eq 0 ] || base_bad=1
for r in 1 0; do
  rm -f "$work"/lock.*
  resync_check $r "$work/base_resync$r.log"; rc=$?
  ls "$work"/lock.fail.* >/dev/null 2>&1 && { echo "baseline resync=$r: lockstep failure"; base_bad=1; }
  [ $rc -eq 0 ] || { echo "baseline resync=$r: pytest rc=$rc"; base_bad=1; }
done
python3 test/asm_parity.py --fuzz 300 > "$work/base_asm.log" 2>&1 || { echo "baseline asm: parity fails"; base_bad=1; }
dune build @wave_test/runtest > "$work/base_wave.log" 2>&1 || { echo "baseline wave: tests fail"; base_bad=1; }
rm -f "$work"/lock.*
if [ $base_bad -ne 0 ]; then echo "baseline: NOT CLEAN, no case can show anything"; exit 1; fi
echo "baseline: clean"
try() {                        # try FILE PATTERN REPLACEMENT KIND
  local f=$1 pat=$2 rep=$3 kind=$4
  cp "$f" "$f.orig"
  python3 - "$f" "$pat" "$rep" <<'EOF' || { echo "pattern not found: $pat"; missed=$((missed + 1)); return; }
import sys
f, pat, rep = sys.argv[1:4]
s = open(f).read()
if pat not in s:
    sys.exit(1)
open(f, "w").write(s.replace(pat, rep, 1))
EOF
  dune build bin/wl_asm.exe bin/wl_simcheck.exe 2>"$work/build.err" || { echo "does not build: $pat"; cat "$work/build.err"; missed=$((missed + 1)); }
  rm -f "$work"/lock.*
  if [ "$kind" = wave ]; then
    dune build @wave_test/runtest > "$work/check.log" 2>&1 && caught=no || caught=yes
  elif [ "$kind" = promote ]; then
    # accepting the failure with --auto-promote must not make the test pass. The bug is
    # one whose promoted records alone would pass: so the build must fail with the
    # wave_test rule and pass with the rule cut out, or the case shows nothing.
    mkdir -p "$work/keep" && cp wave_test/test_*.ml wave_test/dune "$work/keep/"
    dune build @wave_test/runtest --auto-promote > "$work/check.log" 2>&1 || true
    dune build @wave_test/runtest > "$work/check.log" 2>&1 && with=pass || with=fail
    python3 - wave_test/dune <<'EOF'
import sys
f = sys.argv[1]
s = open(f).read()
open(f, "w").write(s[:s.index("(rule")])
EOF
    dune build @wave_test/runtest > "$work/check2.log" 2>&1 && without=pass || without=fail
    cp "$work"/keep/test_*.ml "$work"/keep/dune wave_test/
    echo "  with the rule: $with; rule removed: $without"
    [ $with = fail ] && [ $without = pass ] && caught=yes || caught=no
  elif [ "$kind" = resync ]; then
    # a bug that only a test which writes into its chip reaches: it must be caught
    # with the state load after the write, and missed without it, or the case shows nothing
    for r in 1 0; do
      rm -f "$work"/lock.*
      resync_check $r "$work/check$r.log"
      ls "$work"/lock.fail.* >/dev/null 2>&1 && got[$r]=caught || got[$r]=missed
    done
    echo "  with the load: ${got[1]}; without: ${got[0]}"
    [ ${got[1]} = caught ] && [ ${got[0]} = missed ] && caught=yes || caught=no
  elif [ "$kind" = asm ]; then
    python3 test/asm_parity.py --fuzz 300 > "$work/check.log" 2>&1 && caught=no || caught=yes
  else
    sim_check "$work/check.log"
    ls "$work"/lock.fail.* >/dev/null 2>&1 && caught=yes || caught=no
  fi
  echo "$kind [$pat] -> [$rep]: caught=$caught"
  [ $caught = yes ] || missed=$((missed + 1))
  mv "$f.orig" "$f"
}
try sim/chip.ml 'let ncnt = min 32 (c.isr_cnt + n) in' 'let ncnt = min 31 (c.isr_cnt + n) in' sim
try sim/chip.ml 'c.dcnt <- delay;' 'c.dcnt <- delay + (delay land 1);' sim
try sim/chip.ml 'c.lr <- (if c.pc = c.wrap_top then c.wrap_bottom else (c.pc + 1) land 1023);' 'c.lr <- (c.pc + 1) land 1023 lor 1;' sim
try sim/line.ml 'let g = min 5 (1 + (l.n / 6)) in' 'let g = min 4 (1 + (l.n / 6)) in' sim
try sim/chip.ml '"wait:timeout"' '"wait:timed_out"' sim
# v0.3 (docs/isa.md section 5.2): `!t`, CMPW's immediate, the table word, the ticks, CRC width 17..32
try sim/chip.ml '| 0 -> sel <> Isa.xc_nt || c.t = 0' '| 0 -> sel <> Isa.xc_nt || c.t = 1' sim
try sim/chip.ml 'let s = Isa.cmpw_imm e.x in' 'let s = e.x land 0x3FFF in' sim
try sim/chip.ml '          core.osr_cnt <- 0;
          core.tbl <- None' '          core.osr_cnt <- 32;
          core.tbl <- None' sim
try sim/chip.ml 'if tick then c.ticks <- (c.ticks + 1) land m16;' 'if tick then c.ticks <- (c.ticks + 2) land m16;' sim
try sim/chip.ml 'lor (((v lsr 23) land 1) lsl 4)' 'lor (((v lsr 22) land 1) lsl 4)' sim
try sim/chip.ml 'then List.nth c.rxf n else 0)' 'then List.nth c.rxf (List.length c.rxf - 1 - n) else 0)' resync
try sim/chip.ml '       c.txf <- list ();
       c.rxf <- list ();' '       c.rxf <- list ();
       c.txf <- list ();' resync
try asm/asm.ml '(cond lsl 15) lor (z_int sel lsl 10) lor addr' '(cond lsl 15) lor (z_int sel lsl 11) lor addr' asm
try asm/asm.ml '"pin out of range"' '"pin outside range"' asm
try asm/asm.ml '| "!t" -> Isa.c_always, Z.of_int Isa.xc_nt' '| "!t" -> Isa.c_t, Z.zero' asm
try asm/asm.ml 'if Pyre.starts_with ~prefix:".data" line then prog.data <- idx :: prog.data;' '' asm
try asm/asm.ml 'if v >= 8192 || l.mn = "cmpw"' 'if v >= 8191 || l.mn = "cmpw"' asm
try asm/pyexpr.ml 'I (Z.fdiv x y)' 'I (Z.div x y)' asm
try asm/pyre.ml "if greedy
        then more () || k pos
        else k pos || more ()" "if greedy
        then k pos || more ()
        else k pos || more ()" asm
try rtl/pe_fifo.ml 'let do_push = i.push &: ~:(bit cnt_w log2) in' 'let do_push = i.push in' wave
try rtl/pe_fifos.ml 'let xfer = i.fjoin &: ~:txf_empty &: ~:rxf_full in' 'let xfer = i.fjoin &: ~:txf_empty in' wave
try rtl/pe_line.ml 'let spb = mux2 manch (of_int_trunc ~width:4 2) (of_int_trunc ~width:4 1) in' 'let spb = of_int_trunc ~width:4 1 in' wave
try rtl/pe_line.ml 'n_trunbit <-- ~:(trunbit.value)' 'n_trunbit <-- trunbit.value' wave
try rtl/pe_line.ml 'let m_age = mux2 edge1 (of_int_trunc ~width:21 96) (of_int_trunc ~width:21 32) in' 'let m_age = mux2 edge1 (of_int_trunc ~width:21 32) (of_int_trunc ~width:21 96) in' wave
try rtl/pe_fifos.ml 'let xfer = i.fjoin &: ~:txf_empty &: ~:rxf_full in' 'let xfer = i.fjoin &: ~:txf_empty in' promote
echo "missed: $missed"
exit $((missed > 0))
