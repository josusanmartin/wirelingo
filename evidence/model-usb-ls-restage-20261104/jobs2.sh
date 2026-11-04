#!/bin/bash
# The re-stage table at dc51cc8, after jobs.sh (whose re-stage step named ls_case.py by a wrong path).
H=$HOME/protoemu-flow/usb-ls-restage; T=$H/dc51cc8; P=$HOME/protoemu-flow/test-venv/bin/python
while [ ! -f $H/done-dc51cc8 ]; do sleep 20; done
{ for o in 48 2400; do for r in 0 1; do for n in $(seq 0 25); do echo "$H/ls_case.py $T $r $n test_usb_ls_restage restage_race_ls $o"; done; done; done; } > $H/restage2.jobs
cat $H/restage2.jobs | xargs -P 8 -L 1 sh -c "cd $T && PYTHONPATH=tools:test/model $P \$0 \"\$@\"" > $H/table-restage-dc51cc8.txt 2>&1
echo DONE > $H/done2-dc51cc8
