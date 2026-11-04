#!/bin/bash
# Turnaround figures before (bc98a97) and after (c4003a2) the got_in guard.
set -u
M=$HOME/protoemu-flow/usb-guard/measure
PY=$HOME/protoemu-flow/test-venv/bin/python
export PATH=$HOME/tools/oss-cad-suite/bin:$PATH PYTHONDONTWRITEBYTECODE=1
for tag in bc98a97 c4003a2; do
  if [ $tag = bc98a97 ]; then T=$HOME/protoemu-flow/usb-guard/bc98a97; else T=$HOME/protoemu-flow/agent-runs/agent-a158fcfcc32d11458-c4003a2-1943025; fi
  cd $T && PYTHONPATH=tools $PY -m pytest -s -q -p no:cacheprovider test/model/test_usb_fs.py > $M/usbfs-model-$tag.log 2>&1
  cd $T && PYTHONPATH=tools $PY -m pytest -s -q -p no:cacheprovider test/model/test_timing_check.py > $M/timing-check-$tag.log 2>&1
done
cd $HOME/protoemu-flow/usb-guard/bc98a97/test && timeout 3600 make TESTCASE=test_usb_fs_device SIM_BUILD=sim_build/fsdev COCOTB_RESULTS_FILE=fsdev.xml > $M/rtl-fsdev-bc98a97.log 2>&1
echo DONE > $M/done
# Second step, run by hand after the above (verifier 044 note 2 asked for it here):
# the dev replays at both commits and their comparison, compare_dev_replay.py.
for tag in bc98a97 c4003a2; do
  if [ $tag = bc98a97 ]; then T=$HOME/protoemu-flow/usb-guard/bc98a97; else T=$HOME/protoemu-flow/agent-runs/agent-a158fcfcc32d11458-c4003a2-1943025; fi
  cd $T && $PY fpga/kv260/host/usb_replay_demo.py --plans $M/plans-$tag.json --plan-only ls-dev fs-dev > $M/plan-$tag.log 2>&1
  cd $T && $PY fpga/kv260/host/usb_replay_demo.py --model --plans $M/plans-$tag.json --save $M/replay-$tag.json ls-dev fs-dev > $M/replay-$tag.log 2>&1
done
$PY $M/compare_dev_replay.py $M/plans-bc98a97.json $M/plans-c4003a2.json > $M/compare-dev-replay.txt
