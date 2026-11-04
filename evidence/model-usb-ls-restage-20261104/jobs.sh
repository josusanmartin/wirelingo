#!/bin/bash
# The per-access tables at dc51cc8: re-stage (low speed) and stage abort (both speeds), SPI 1 us and 50 us.
T=$HOME/protoemu-flow/usb-ls-restage/dc51cc8; P=$HOME/protoemu-flow/test-venv/bin/python; cd $HOME/protoemu-flow/usb-ls-restage
{ for o in 48 2400; do for r in 0 1; do for n in $(seq 0 25); do echo "$HOME/protoemu-flow/usb-ls-restage/ls_case.py $T $r $n test_usb_ls_restage restage_race_ls $o"; done; done; done; } > restage.jobs
{ for s in full low; do for o in 48 2400; do for r in 0 1; do for n in $(seq 0 15); do echo "$T/test/model/test_usb_stage_abort.py $s $r $n $o"; done; done; done; done; } > abort.jobs
cat restage.jobs | xargs -P 8 -L 1 sh -c "cd $T && PYTHONPATH=tools:test/model $P \$0 \"\$@\"" > table-restage-dc51cc8.txt 2>&1
cat abort.jobs | xargs -P 8 -L 1 sh -c "cd $T && PYTHONPATH=tools:test/model $P \$0 \"\$@\"" > table-abort-dc51cc8.txt 2>&1
echo DONE > done-dc51cc8
