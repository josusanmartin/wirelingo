Commit: d811228  Base: 61af9b2  Branch: rmii-study
Item: firmware for RMII RX, TX and match, test/model/test_rmii.py, the RTL test, docs/rmii-study.md. Reproduce: `remote_test.sh model test_rmii_rtl test lockstep`.
Claims:
1. RX with each RXD on two pads and SYNC_BYPASS 0x0C takes exactly 12 clocks per word; 1518-byte and back-to-back frames are exact.
2. The host needs one read per 12 clocks, or bursts of at least 7 words (6 fails). Longest stall 71 clocks. Overrun is flagged in the record and F0.
3. TX with `out pins, 3`, one word per 10 clocks, no gap. Underrun is recorded nowhere on the chip; the far-end FCS fails.
4. Simultaneous RX and TX needs 147 % of the port; none of 16 burst mixes worked.
5. Reaction is 10 clocks, 9 with bypass, model; RTL matches at 10.
6. Pad budget is an estimate: positive with REF_CLK in, TX hold -0.43 ns at the fast corner with REF_CLK out.
7. Doc verdict: RX go, TX go, each on model and RTL simulation; both at wire rate on one port no-go; hardware unproved. Check the wording cannot be read as hardware evidence, and that RMII behaviour taken from a datasheet is labelled.
