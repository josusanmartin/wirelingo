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
