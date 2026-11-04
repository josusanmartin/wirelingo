Commit: 75eed67  Base: 61af9b2  Branch: faults-pulse
Item: pps_capture firmware and helper, tools/pe/faults.py, test/model/test_faults.py, test_pps, docs/fault-testing.md. Reproduce: `remote_test.sh model test_pps`; `PPS_LONG=1 make -C test COCOTB_TEST_MODULES=test_pps COCOTB_TEST_FILTER=pps_one_second`.
Claims:
1. Stamps are exact to the clock, except 3 blind ticks in 65536 (7 at the carry) and edges inside the 13-19 tick record path. Records flag these; bounds come from pe.timing.
2. A slow host loses edges, never time: the FIFO holds 4 edges. Check against the tapeout's FIFO depth, since earlier reports found bare sim.Chip's default of 4 is not the tapeout's 8.
3. The counter wraps after 65.2 days (48 bits). One 1 s interval on RTL gave 50,000,003 clocks in 6831 s at 04cfbf6.
4. Sweep of 576 seeded cases, model only: 0 hangs, 7 not ok. Check the sweep can really detect a hang, and that "ok" is not vacuous.
5. Six findings each reproduce from the stated seed through test_finding_*: uart_rx glitch reads as start bit (0xFF, no flag); i2c_master stuck SDA reads as ACK; i2c_master next transfer lost after a truncated read; can_rx has no form check; can_rx one-clock idle glitch triggers an error flag; usb_enum misses a reset starting in an EOP. Decide for each: real firmware weakness, expected protocol behaviour, or a fault of the harness.
6. Limits stated: sweep not on RTL, no UTC reference, usb_enum swept at full speed only.
