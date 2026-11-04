#!/bin/bash
# Both tables with the fix: re-stage (low speed) and stage abort (both speeds), SPI 1 us and 50 us.
H=$HOME/protoemu-flow/usb-ls-restage; T=$H/fix; P=$HOME/protoemu-flow/test-venv/bin/python
{ for o in 48 2400; do for r in 0 1; do for n in $(seq 0 25); do echo "$H/ls_case.py $T $r $n test_usb_ls_restage restage_race_ls $o"; done; done; done; } > $H/restage3.jobs
{ for s in full low; do for o in 48 2400; do for r in 0 1; do for n in $(seq 0 15); do echo "$T/test/model/test_usb_stage_abort.py $s $r $n $o"; done; done; done; done; } > $H/abort3.jobs
cat $H/abort3.jobs $H/restage3.jobs | xargs -P 10 -L 1 sh -c "cd $T && PYTHONPATH=tools:test/model $P \$0 \"\$@\"" > $H/table-fix.txt 2>&1
echo DONE > $H/done3
