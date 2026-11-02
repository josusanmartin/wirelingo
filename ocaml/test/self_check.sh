#!/usr/bin/env bash
# Do the parity checks notice a broken port? Plant one bug at a time in a throwaway
# copy of ocaml/, rebuild, and require the matching check to report it.
#
#   ocaml/test/self_check.sh WORK
#
# Assembler bugs go to asm_parity.py, model bugs to the lockstep plugin on two model
# test files. Needs the same environment as dune build and stage3_model_suite.sh.
# Exit status 0 only if every planted bug is caught.
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
  if [ "$kind" = asm ]; then
    python3 test/asm_parity.py --fuzz 300 > "$work/check.log" 2>&1 && caught=no || caught=yes
  else
    (cd .. && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tools:ocaml/test WL_LOCK_EVERY=1 WL_LOCK_OUT="$work/lock" \
      "$py" -m pytest -q -p no:cacheprovider -p capture_plugin -n 4 \
      test/model/test_eth10.py test/model/test_call.py test/model/test_uart.py > "$work/check.log" 2>&1)
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
try asm/asm.ml '(cond lsl 15) lor (z_int sel lsl 10) lor addr' '(cond lsl 15) lor (z_int sel lsl 11) lor addr' asm
try asm/asm.ml '"pin out of range"' '"pin outside range"' asm
try asm/pyexpr.ml 'I (Z.fdiv x y)' 'I (Z.div x y)' asm
try asm/pyre.ml "if greedy
        then more () || k pos
        else k pos || more ()" "if greedy
        then k pos || more ()
        else k pos || more ()" asm
echo "missed: $missed"
exit $((missed > 0))
