Commit: b5496aa  Base: 575362a  Branch: fault-fixes
Item: firmware fixes for four of the fault findings judged real in report 014 (uart_rx glitch start bit; i2c_master stuck SDA read as ACK; can_rx one-clock idle glitch; usb_enum missing a reset that starts inside a token). Findings 3 (I2C after a truncated read) and 4 (CAN form check) are left as documented.
Reproduce: `remote_test.sh model test test_sigrok`, with SIGROK_CLI pointing at runner1 `~/protoemu-flow/fault-fixes/sigrok/sigrok-cli` (the builder installed it there). Long reset sweeps: `test/model/usb_reset_sweep.py`.

Claims:
1. uart_rx rechecks the start bit at mid-bit and drops a glitch up to 4 ticks. 13 -> 14 words, every sample time unchanged.
2. i2c_master reads SDA back wherever it releases it. A low read-back sets F1 and replies 0x200. 71 -> 90 words. The SCL waveform inside a command is unchanged; a WRITE is 9 ticks longer (11 %), a READ 3. Check every doc and table that quotes I2C timing or word counts (pio-comparison, README, timing-check) now gives the new figures, or says which firmware it measured.
3. can_rx confirms the SOF at its sample point. 97 -> 100 words, no tick added.
4. usb_enum sees a bus reset starting at any clock and returns to address 0. 246 -> 253 words of 256. Every answer leaves on the same clock as before at both speeds. Turnaround deadlines are now timed from the frame end because the checker cannot time from a WAIT with a timeout; the bounds are unchanged. Check that this change of reference does not loosen what the timing checker proves; it must not have.
5. Report 014 note 1 (a full 10 ms reset at bits 30, 32, 35 was missed) is closed: low-speed sweep 9000 start points, 0 missed, at most 2702 clocks from SE0 start to reset; the old firmware gives 11 of 563 missed through the same committed script. Check the full-speed sweep too, and that the sweep can fail (run it on the old firmware).
6. test_finding_* now assert the fixes. FIX_MUTANTS (16) each take one new check out, and its test must fail. New RTL tests test_uart_rx_glitch and test_i2c_master_bus_fault.
7. Fault sweep: 576 cases, 0 needs-reset, 0 false-good, 1 second-frame (i2c_master stuck, seed 13; the host is told by F1). Before: 5 false-good, 2 second-frame. Check that "told by F1" is what the host really sees.
8. Results: model 2131 passed, RTL `test` 27/27, test_sigrok 8/8, at b5496aa on runner1.
9. Honesty: none of the four changed programs has run on the KV260 or at gate level. Docs, README and docs/verification-story.md must say the earlier KV260 results were for the old firmware and need an FPGA rerun, especially usb_enum's hardware enumeration. Flag any sentence that implies otherwise.
10. Nothing outside firmware, tests, tools/test helpers and docs changed: no `src/`, assembler, or model semantics. The MicroPython bundle was regenerated; check it matches the new firmware.

Not for you to touch: the Kria (off limits), anything SWD.
