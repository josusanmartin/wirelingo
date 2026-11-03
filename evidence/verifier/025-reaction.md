Commit: f497922  Base: 6d5bdd5  Branch: reaction
Item: firmware/eth10_reply.pasm: core 0 alone matches a 10BASE-T frame and sends a fixed 64-byte reply (FCS precomputed on the PC as ldi immediates) with no host after loading. tools/pe/ethreply.py, tools/pe/ethsim.py (Wire.send gained offset_ns, tp_idl_ns), test/model/test_eth10_reply.py, test/test_eth_reply_rtl.py, mutants reply-*, docs/eth10-reaction.md plus pointer rows in README, guide, eth10-cut-through. Also touches test_hw_day.py (the same ping-step stand-in bug the orchestrator fixed at f3a9db2 on improve-2026-11; ignore that part, it will be resolved at merge).
Reproduce: remote_test.sh model at f497922; `pytest -s test/model/test_eth10_reply.py -k ...` as below; `make COCOTB_TEST_MODULES=test_eth_reply_rtl SIM_BUILD=sim_build/test_eth_reply_rtl`; `python3 test/mutants.py 'reply-*'` (needs a git checkout). Builder logs in runner1:~/protoemu-flow/reaction/logs/.

Claims (N = first TD edge - (far end's last RXP edge + 9600 ns), in clocks):
1. 50 MHz: N in [0, 1), measured 0.003-0.994 over 31 replies, far-end TP_IDL 250-350 ns, +-100 ppm, random phase. `-k "match_then_reply_timing or other_tp_idl"`.
2. 40 MHz: same bound, 0.024-0.926 over 38 replies.
3. The bound: pe.timing `ifg` deadline exact (477 clocks at 50 MHz, 381 at 40) + pad synchronizer 2-3 + 1 clock out-to-TD; all asserted per reply. `-k deadlines`. Check the bound is derived, not fitted to observations.
4. From the far end's last bit (300 ns TP_IDL): 9900.1-9919.9 ns at 50 MHz, 9900.6-9923.2 at 40.
5. Jitter +-13.5 ns at 50 MHz: N 0.025-0.779 over 12. `-k jitter`. No 40 MHz jitter runs (stated).
6. 60 mixed frames per clock: every matching frame exactly one reply, none otherwise; replies decode through two independent decoders that must agree, exact preamble/SFD, TP_IDL 250-350 ns, TD+ and TD- never both high, FCS checked with zlib. Check the two decoders really are independent.
7. RTL at the pads: N 0.12, 0.24, 0.58 at 50 MHz (with jitter, -200 ppm); 40 MHz N = 0.00, the test asserting 0.5 <= reading < 1.5 because the monitor reads half a clock later. Scrutinise this: is the half-clock offset justified, and does the RTL check actually bound N >= 0 (the reply never before the gap ends)?
8. Back-to-back trains of 2-5 frames at the 802.3 minimum gap: all heard, every match answered after the train; replies 10140 ns apart (50 MHz), 10175 (40). RTL 40 MHz: 3 frames, 2 replies, 10175 ns.
9. Blind window: a far-end first edge up to 95 ns (50 MHz) / 120 ns (40) before ours is unheard and collides; 5 ns more is heard. `-k blind_window`.
10. A frame arriving 1, 25, 50 or 56 us into our reply leaves the reply whole and is not heard; the next one is. `-k arriving_while_replying`.
11. A frame up to 480 ns (50) / 500 ns (40) after our TP_IDL's end is unheard; 5 ns later it is. `-k just_after`.
12. Far-end link pulses in the window restart it (N 2.98 / 3.99 after the last pulse); far-end TP_IDL 60-200 ns delays the reply by up to 11.96 clocks, never earlier.
13. A measurement bug fixed: pe.ethsim.Wire stamps a TD change one clock after its clock edge; model tests now subtract WIRE_STAMP. Check this correction is right and applied consistently; the builder says the same stamping may bias docs/eth10-network.md's model 9800 ns (its RTL check, 9810 ns, used one phase). Say whether that older figure is wrong.
14. Limits stated in docs/eth10-reaction.md: no link pulses (a real NIC sees link down), fixed reply, decision before FCS so a corrupt frame can be answered, no collision detect/jam/backoff, model and RTL only, no gate level/FPGA/cable/peer; pad delays only make N larger. Flag any sentence that overclaims or implies hardware.
15. Full model suite at 660615a: 2299 passed, 28 skipped; RTL module 2/2; mutants reply-* 15/15 killed.

Kria off limits; no SWD.
