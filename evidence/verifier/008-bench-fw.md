Commit: 5582d7a  Base: 61af9b2  Branch: bench-fw
Item: mdio_master, mdio_phy, pe.qsfpdev, pe.pmbus, test_bench_fw, docs/bench-firmware.md. Sigrok on runner1: SIGROK_CLI=~/protoemu-flow/agent-runs/sg-bench-fw/sigrok-cli.
Reproduce: `remote_test.sh model test_bench_fw`, then with sigrok; `python3 test/mutants.py <names>`.
Claims:
1. mdio_master (32 words): MDC exactly 400 ns, zero margin at 50 MHz. PHY delay 0-340 ns reads correctly, 360 ns fails.
2. mdio_phy (124 words): all 32 registers read back, no bus contention over 200 random frames. Needs 40 ns STA hold against 802.3's 10 ns, and MDC >= 360 ns. Decide whether "no contention" is a real check.
3. pe.qsfpdev is a stand-in with its fields listed; roll-over, page revert and write abort follow SFF-8636 Rev 2.9. SCL hold is host latency + 23 clocks.
4. pe.pmbus: all 24 single-bit flips rejected; all 65536 LINEAR11 words checked.
5. 176 bench model tests and 5 RTL tests pass with sigrok; 28/28 mutants killed.
6. Figures from secondary sources or memory are labelled; 802.3 and PMBus specs were not read. Nothing claims FPGA or real-device evidence.
7. The CI workflow edit is sound, and no flow/ file changed.
