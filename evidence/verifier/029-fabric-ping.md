Commit: da82a51  Base: 97e442d  Branch: fabric-ping
Item: backlog D. fpga/kv260/host/fabric_eth.py (FpgaBackend for eth_tap.py on the loaded d1aa46f bitstream: RXP from pe_lab's generator, one 64-byte frame at a time; reply from eth10_tx_cs sampled by usb_sampler on core 1; host port through pe_spim), fabric_guard.py parser rewrite (real listapps rows with merged columns; fixture test/model/listapps-20261103.txt, verbatim from the board), hw_day.py preflight reads rows not substrings, install.sh grep, test/model/test_fabric_ping.py, mutants fab-* and hw-*, docs/eth10-network.md "The FPGA backend".

Builder's claims (model): full model suite 2360 passed, 28 skipped at da82a51; `python3 test/mutants.py 'hw-*' 'fab-*'` 94/94 killed; the fake-KV260 tests run the board code path against the cycle model to the clock, PMOD registers only ever 0, the board handed back on every exit path (errors, Ctrl-C, SIGTERM, SIGHUP).

Orchestrator's board run (2026-11-03 20:04-20:06 UTC, Kria, protoemu active alone, RTL d1aa46f, 49.99916 MHz), runbook in the builder's report, archive of da82a51 (tools firmware fpga/kv260/host, sha256 prefix shown in evidence/029):
1. `eth_tap.py --backend fpga --probe`: arp, ping, udp each 64 bytes in, reply 64 bytes off TD, FCS ok; bad_fcs and other_mac no reply; probe PASS. Model probe (runner1) also PASS.
2. `--compare b.json m.json`: arp/bad_fcs/other_mac same (947 TD edges, divider phase 1), ping same (939, phase 4), udp same (917, phase 0): SAME.
3. `unshare --net -- eth_tap.py --backend fpga --selftest`: real Linux in a namespace, 5/5 pings (rtt avg 52.3 ms), UDP echo "wirelingo", no reply for an unknown IP, ARP-ignored count 1, "pass": true.
Evidence: ~/dev/protoemu-verify-reports/evidence/029/ (selftest.log, fabric-ping-b.json board, fabric-ping-m.json model). The --pcap file came out 0 bytes: flag whether the doc or the code claims a pcap.

Check especially: that the evidence level written in docs is "RTL in FPGA fabric (KV260), one frame at a time, 64-byte frames, generator into RXP, no cable", not more; that the zero-word restoration ("6 zero words restored") cannot hide a real receive error (bit count and FCS check it?); that the guard parser still fails closed on unknown lines; that "SAME" really compares every TD edge. Kria: verifier does not touch it. No SWD.
