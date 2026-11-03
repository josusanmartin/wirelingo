Commit: d2d89b8  Base: e71ab1c  Branch: eth-stack
Item: fixes for inbox/003-eth-stack.report.md, all findings. Check each, then re-attack what changed. Logs: runner1 ~/protoemu-flow/eth-logs/aed4c8f. Do not use sudo or a TAP yourself.
Claims:
1. test_service_budget at 1518 bytes, asserted on both sides: RX 16.5/15.5 us, TX 22.5/21.5 us. Go/no-go uses 15.5 us. A real loss was fixed: a late core adds a bit, and dribble bits are now dropped. Attack that fix: can it drop a real bit?
2. 18 mutants in test/mutants.py, 18/18 killed.
3. Flip direction corrected to L to H; all 15,497 transients settle on H.
4. --measure-spim restores the select and clears QUAD_EN, tested with a fake only. This will run on the Kria, so check it cannot leave the port in a bad state.
5. 3930/3950 ns asserted exactly. Gaps exact: 9800/9825 ns model, 9810 RTL and gate level; the 95-bit mutant is killed; the RTL test now reaches core deferral.
6. Model 1506 passed, RTL 7/7, gate level 3/3. TAP passed at 7503c14, same code: confirm "same code".
