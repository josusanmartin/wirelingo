Commit: 40fb59a  Base: 61af9b2  Branch: blind-test
Item: HD44780 and ATA PIO firmware, test_blind, test/blind_mutants.py, docs/blind-test.md. Reproduce: `remote_test.sh model test_blind`; test/blind_mutants.py. Tests ran at 3218e99, docs only after: confirm.
Claims:
1. Conditions: the draw followed the stated rule (sorted list, sha256("wirelingo blind test 61af9b2"), modulo 19, and shifted right 16 bits modulo 19), and no assembler, model, tool or RTL changed. Note the agent could read existing firmware: check the doc says so, and that the doc does not oversell "blind".
2. HD44780 4-bit fits: 99 words, first pass 60m55s. Far-end models come from public timing, not the firmware.
3. ATA PIO does not fit as specified, because 8 bidirectional pads can't carry DD15:0. Largest subset: 8-bit host, PIO mode 0, polled, 100 words, first pass 86m32s. Sector transfer needs a CFA device. Check that "largest" is true.
4. test_blind 13/13 through SPI; model suite 1450 passed; 29/29 mutants killed.
5. Limits are stated: the ATA reset window (strobes and selects low until the first host write, RESET- unwired), secondary sources for 8-bit mode, 290 ns t2 from Linux source, 3.3 V LCD assumed, sigrok's generic parallel decoder only.
