Commit: 7f7a70f  Parent: b5496aa  Base: 575362a  Branch: fault-fixes
Item: fixes for inbox/023-fault-fixes.report.md.
Reproduce: remote_test.sh model test test_sigrok with SIGROK_CLI=runner1 ~/protoemu-flow/fault-fixes/sigrok/sigrok-cli passed through (stock remote_test.sh does not pass it, so sigrok tests skip there; say what you used).
Claims:
1. Blocking 1 fixed: docs/info.md (both the bringup.usb_enum section and the FPGA summary paragraph) and docs/usb-full-speed.md say the KV260, Linux and gate-level results are for the 246-word usb_enum and need an FPGA and gate-level rerun. Also labelled: usb-enumeration.md gate-level and replayed-host sections, guide.md netlist/hardware/fault rows, uart-rx-timing.md gate-level line. Left on purpose: historical notes in methodology.md, explain-wirelingo.md, test_can.py, and KV260 demo docstrings. Grep the tree yourself for anything still tying uart_rx, i2c_master, can_rx or usb_enum (as changed here) to KV260 or gate-level evidence without a label.
2. Should-fix 2: test_usb_enum_answers_on_the_same_clock_as_before (five sessions, both speeds, against figures recorded from the old firmware) catches J_LATE 0; test_usb_enum_short_se0_is_not_a_reset (1.9 us SE0 at each of the three timed waits, address stays set) catches the three lowered timeouts. FIX_MUTANTS now 25 (your 4 survivors + 5 killed). Rerun your 9 mutants.
3. usb_enum.timing gained deadlines in_reach and hs_reach (>= 7 ticks frame end to the timed wait; pass at 8 and 10) proving the stall=0 assumptions; the timing test asserts no measurement broke one. Check that they are meaningful, not vacuous.
4. Notes fixed: timing-check.md START explanation; claims-audit.md "246 words now".
5. Results: model 2146 passed 0 skipped, test 27/27, test_sigrok 8/8 at 7f7a70f.
Kria off limits; no SWD.
