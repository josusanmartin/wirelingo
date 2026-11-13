#!/bin/bash
# Run from a committed scratch clone. Uses existing runner1 tools.
# Includes a diagnostic of the unresolved EP1 retry behavior: its assertion
# deliberately checks that the current firmware exhibits the reported defect.
set -eu
case "$PWD" in
  "$HOME"/protoemu-flow/astra-review/*) ;;
  *) echo 'Run inside the assigned review scratch directory.' >&2; exit 2 ;;
esac
mkdir -p .tmp
export TMPDIR="$PWD/.tmp" PYTHONDONTWRITEBYTECODE=1
export PATH="$HOME/tools/oss-cad-suite/bin:$PATH"
root=$PWD
nice -n 10 timeout 1800 make -C formal > formal-all.log 2>&1
printf 'default formal gate: '
grep -c 'DONE (PASS' formal-all.log
grep 'DONE (' formal-all.log
! grep -E 'DONE \((FAIL|ERROR|UNKNOWN)' formal-all.log
# Prove the new properties distinguish faults the prior harness missed.
for handshake in push take; do
  d="$root/.tmp/line-$handshake"
  mkdir -p "$d/src" "$d/formal"
  cp src/*.sv "$d/src/"
  cp formal/core.sby "$d/formal/"
  python3 - "$d/src/pe_core.sv" "$handshake" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1]); s=p.read_text(); which=sys.argv[2]
a=f'= exec && line_{which}_d;'
assert s.count(a)==1
p.write_text(s.replace(a,f'= line_{which}_d;'))
PY
  git show fb4228d:formal/core_props.sv > "$d/formal/core_props.sv"
  (cd "$d/formal" && nice -n 10 timeout 300 sby -f core.sby prove0 > before.log 2>&1)
  printf 'old harness line-%s: ' "$handshake"
  grep 'DONE (' "$d/formal/before.log"
  cp formal/core_props.sv "$d/formal/core_props.sv"
  set +e
  (cd "$d/formal" && nice -n 10 timeout 300 sby -f core.sby prove0 > after.log 2>&1)
  rc=$?
  set -e
  printf 'new harness line-%s: rc=%s ' "$handshake" "$rc"
  grep 'DONE (' "$d/formal/after.log"
  grep -q 'DONE (FAIL' "$d/formal/after.log"
done
# A wire-level observation of an unresolved payload retry, and divider timing.
PYTHONPATH=tools:test/model nice -n 10 "$HOME/protoemu-flow/test-venv/bin/python" - <<'PY'
from pe import usb, sim
from test_usb_enum_ep1 import device,configure,set_inputs
from test_usb_autonomous import TransportIdle
for speed in ('low','full'):
 mh,h=device(speed)
 with TransportIdle(mh):
  configure(h,3)
  set_inputs(mh,5,0xa5); mh.step(20)
  h._send(usb.token(usb.PID_IN,3,1)); a=h._response(); h.gap()
  set_inputs(mh,6,0x5a); mh.step(20)
  h._send(usb.token(usb.PID_IN,3,1)); b=h._response(); h.gap()
  print(f'EP1 {speed}: no ACK; DATA {a.hex()} -> {b.hex()}; same PID={a[0]==b[0]}, same packet={a==b}')
  assert a[0]==b[0] and a != b
c=sim.Core(0); c.div_int=0; c.div_frac=128
edges=[]
for cycle in range(131075):
 if c._divider(): edges.append(cycle)
print('INT=0 FRAC=128 tick clocks:',edges)
assert edges[:5]==[0,65536,65537,131073,131074]
PY
# Both SRAM builds, with fresh random seeds in addition to directed cases.
cd test
for cores in 3 2; do
  set +e
  LOCKSTEP_SEED0=20261112 LOCKSTEP_SEEDS=40 LOCKSTEP_CYCLES=1000 PE_NCORES="$cores" PE_NBANKS="$cores" PE_FIFO_LOG2=3 nice -n 10 timeout 3600 make TB=chip SIM_BUILD="sim_build/astra-lock$cores" COCOTB_RESULTS_FILE="astra-lock$cores.xml" > "astra-lock$cores.log" 2>&1
  rc=$?
  set -e
  printf 'lockstep cores=%s rc=%s\n' "$cores" "$rc"
  grep -E 'TESTS=| FAIL ' "astra-lock$cores.log"
  python3 check_results.py "astra-lock$cores.xml" "$(grep -c '^@cocotb.test()' test_lockstep.py)"
  [ "$rc" = 0 ]
  set +e
  PE_NCORES="$cores" PE_NBANKS="$cores" PE_FIFO_LOG2=3 nice -n 10 timeout 3600 make SIM_BUILD="sim_build/astra-top$cores" COCOTB_RESULTS_FILE="astra-top$cores.xml" > "astra-top$cores.log" 2>&1
  rc=$?
  set -e
  printf 'top cores=%s rc=%s\n' "$cores" "$rc"
  grep -E 'TESTS=| FAIL ' "astra-top$cores.log"
  python3 check_results.py "astra-top$cores.xml" "$(grep -c '^@cocotb.test()' test.py)"
  [ "$rc" = 0 ]
done
for module in test_blind test_rmii_rtl test_eth_stack_rtl test_eth_reply_rtl; do
  nice -n 10 timeout 1800 make PE_NCORES=3 PE_NBANKS=3 PE_FIFO_LOG2=3 COCOTB_TEST_MODULES="$module" SIM_BUILD="sim_build/astra-$module" COCOTB_RESULTS_FILE="astra-$module.xml" > "astra-$module.log" 2>&1
  grep -E 'TESTS=| FAIL ' "astra-$module.log"
  python3 check_results.py "astra-$module.xml" "$(grep -c '^@cocotb.test()' "$module.py")"
done
cd ..
PYTHONPATH=tools nice -n 10 timeout 600 "$HOME/protoemu-flow/test-venv/bin/python" -m pytest -q -p no:cacheprovider --ignore=test/model/test_swd.py test/model/test_doc_links.py test/model/test_doc_guard_labels.py test/model/test_evidence_index.py test/model/test_home_paths.py test/model/test_private_hosts.py
PYTHONPATH=tools nice -n 10 python3 -m pe.mpy_bundle --check
