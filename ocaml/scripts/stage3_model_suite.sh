#!/usr/bin/env bash
# The repo's model test suite (test/model, test_swd.py excluded) with every Chip
# in lockstep with the OCaml model and every assemble_all() call recorded.
#
#   ocaml/scripts/stage3_model_suite.sh OUT [STRIDE]
#
# STRIDE: compare the whole state every STRIDE cycles (default 64); the pins and
# the bus read are compared every cycle, and the state and coverage counters at
# the end of each chip. A test that reaches into a chip shows as a mismatch at a
# stride above 1, so tests with a mismatch run again at stride 1, where such a
# reach is detected before the step and the chip is counted as poked: there, any
# mismatch is real. Exit status 0 only if the second pass has no mismatch.
#
# Needs pytest and pytest-xdist in $WL_PYTHON (default
# ~/protoemu-flow/test-venv/bin/python). pytest's own result is reported, not
# used: see docs/hardcaml-port.md for tests that need a tool runner1 lacks.
# SPDX-License-Identifier: Apache-2.0
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../.." && pwd)
out=$(realpath -m "$1"); stride=${2:-64}
py=${WL_PYTHON:-$HOME/protoemu-flow/test-venv/bin/python}
mkdir -p "$out"
cd "$repo" || exit 2
export PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tools:ocaml/test
WL_LOCK_EVERY=$stride WL_ASM_CORPUS=$out/asm WL_LOCK_OUT=$out/lock \
  timeout 7200 "$py" -m pytest -q -p no:cacheprovider -p capture_plugin \
  --ignore=test/model/test_swd.py -n 8 test/model > "$out/pytest.log" 2>&1
tail -8 "$out/pytest.log"
summary() {
  python3 - "$@" <<'EOF'
import glob, json, sys
prefix = sys.argv[1]
tot = {}
for f in glob.glob(prefix + ".[0-9]*.json"):
    for k, v in json.load(open(f))["totals"].items():
        if isinstance(v, int):
            tot[k] = tot.get(k, 0) + v
print("lockstep totals:", tot)
names = sorted({l.strip() for f in glob.glob(prefix + ".fail.*") for l in open(f) if l.startswith("test/")})
print("tests with a mismatch:", len(names))
open(prefix + ".failed", "w").write("".join(n + "\n" for n in names))
EOF
}
summary "$out/lock"
mapfile -t again < "$out/lock.failed"
rc=0
if [ ${#again[@]} -gt 0 ]; then
  printf '  %s\n' "${again[@]}"
  WL_LOCK_EVERY=1 WL_LOCK_OUT=$out/one timeout 3600 "$py" -m pytest -q -p no:cacheprovider \
    -p capture_plugin -n 4 "${again[@]}" > "$out/pytest1.log" 2>&1
  tail -1 "$out/pytest1.log"
  echo "stride 1:"
  summary "$out/one"
  [ -s "$out/one.failed" ] && rc=1
fi
exit $rc
