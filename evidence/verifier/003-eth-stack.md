Commit: e71ab1c  Base: 61af9b2  Branch: eth-stack
Item: tools/pe/eth.py, firmware/eth10_tx_cs.pasm, test_eth_stack tests, test_eth_stack_rtl, fpga/kv260/host/eth_tap.py, docs/eth10-network.md.
Reproduce: `remote_test.sh model test_eth_stack_rtl`. Scapy and MicroPython live in runner1 ~/protoemu-flow/eth-venv and eth-tools, otherwise those tests skip: run them, do not accept skips. Do not use sudo or a TAP yourself; read the TAP code and judge it.
Claims:
1. Stack runs on the model and on MicroPython's Unix port with a fake port. 15/15 ad hoc mutants were caught, but that script is not in the repo: judge whether that claim is reproducible.
2. eth10_tx_cs senses carrier on the core, leaving 130 ns unheard against 3910 ns for eth10_tx with host gating. No collision handling.
3. Link pulses run idle and across frames, 16.000 ms apart. eth10_tx can pulse 3.3 us after a frame; eth10_tx_cs restarts the timer.
4. At 50 MHz half duplex needs quad SCK >= 7.14 MHz, a collision 8.33 MHz; 1-bit needs 25 MHz for both. Software budget per pass is 16 us RX, 24 us TX.
5. RTL 7/7; gate level fo6 without SDF 3/3 at 1999a39, docs only after; a real Linux TAP gave 5/5 pings at both clocks.
6. The gate-level monitor change (reading TD at mid-clock) did not hide a real fault: decide this one carefully.
7. Docs state nothing ran on FPGA, silicon or a cable.
