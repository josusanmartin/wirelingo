# 062 usb-config-prestage

Branch `usb-config-prestage`, commits `f3b51a6`, `541f806`, `988746a` and `ab2db8e` on `f8eed52` (improve-2026-11 after the tidy merge). 18 files, +1016 −43. It merges cleanly onto the current tip, `088d703` (`git merge-tree`).

Backlog item AH, a mitigation for F, the SET_CONFIGURATION stall (`notes/F-usb-stall-20261103.md`). The status stage of SET_CONFIGURATION is now answered by firmware, with no software round trip.

The board A/B script is written but not run. The evidence level is model and RTL simulation only.

The builder's claims:

1. **The old path** (f8eed52). The SETUP token's `got_setup` flushes the TX FIFO and clears F_STAGED, so the status IN is NAKed until the software has polled, parsed and staged.
   - The software already stages in the same `service()` pass that reads the SETUP, so it has no delay left to cut.
   - It can't stage before the SETUP arrives, because the token flushes the FIFO.
   - Hence a firmware change.
2. **The new path** (`firmware/usb_fs_dev.pasm`).
   - While the SETUP's DATA arrives, the firmware reads its first 16 bits through the CRC tap.
   - If bmRequestType is 0x00 and bRequest 0x09, it sets F_CFGZLP (flag 7, unused before).
   - A status IN that finds nothing staged then gets a zero-length DATA1 from the new `cfg_zlp` block, including on a repeated IN.
   - The next SETUP token clears the flag.
   - `DeviceSoftware` sees the `cfg_zlp` label and stages nothing for SET_CONFIGURATION, so firmware and software agree.
   - SET_ADDRESS and the vendor and other no-data requests are unchanged; a test checks vendor requests are still NAKed while the software is held.
3. **Firmware size:** 142 → 159 words (of 256).
4. **Costs in RTL:**
   - IN → NAK goes from 13.4 to 14.4 clocks.
   - The firmware ZLP is answered in 14.1 clocks.
   - ACK and the staged DATA1 are unchanged at 10.5 and 15.9.
   - During a SETUP's DATA, the line unit holds at most 4 bits, against 3 before, and none by the end of the packet, so the ACK path is unaffected.
5. **The double-stage bug** (2026-11-03 19:10). It was already fixed on f8eed52 (6dc8c93). A new test counts `stage()` calls, and its new mutant is killed.
6. **The shared helper `staged_over_empty`** now waits for the software to finish before checking for leftover staged state. With the instant status, it had looked too early.
7. **The race tests** (`988746a`). They used SET_CONFIGURATION as the request that abandons a transfer. With the firmware answering it, mutants `usbdev-restage-after-setup` and `usbdev-dirty-not-cleared` survived. The FS race tests now use a zero-length vendor write, and both mutants are killed again.
8. **Tests on runner1.**
   - Model at ab2db8e: 2914 passed, 29 skipped (2878 + 36 new).
   - cocotb `test` at 988746a: 28/28, including the new `test_usb_fs_config_zlp`.
   - `test_usb_uart_rtl`: 3/3.
   - Mutants `usb*`, `fw-usb*` and `hw-*`: 116 of 116 killed (7 new; 241 in all).
   - The `pe.timing` and `--plan`/`--model` paths pass.
   - sigrok-cli isn't available, so the new RTL check is its own test, and the sigrok expectations are untouched.
9. **The A/B script** `fpga/kv260/host/usb_prestage_ab.py --a A --b B [--pairs 300]`.
   - It runs single attaches of each arm's own demo in ABBA order, with `usb_guard_run.py`'s capture, stop-on-signal and no-device rules.
   - It counts SET_CONFIGURATION stalls per arm and reports a one-sided Fisher p.
   - It refuses if the checkouts are the wrong way round.
   - It is proved on the model and with stand-in demos (`test/model/test_usb_prestage_ab.py`). One optional argument was added to `usb_guard_run.py`.
   - Power at a 3% base rate, from `--plan`, to detect a B that never stalls: 100 attaches per arm 0.18, 200 0.72, 300 0.95.
10. **Docs** with evidence levels: `usb-full-speed.md`, `timing-check.md`, `fpga/kv260/README.md`, `README.md` and `explain-wirelingo.md`. The board result is marked pending.

The builder's caveats:
- In all 8 stalls sampled on 2026-11-03, the ZLP was already staged and no IN had arrived, which points at the host or hub side. This change removes the NAKed status IN through the TT; only the A/B can say whether that matters.
- The gate-level and SDF runs have not been redone with the new firmware.
- If the new firmware is loaded with the old software, a spare ZLP sits in the TX FIFO until the next SETUP.

Asked checks:
- **Read the firmware change against the USB 2.0 spec.**
  - Is answering SET_CONFIGURATION's status before the software has applied the configuration correct? The device has bNumEndpoints 0, so there is nothing to configure. Check the descriptors.
  - Is DATA1 right, including on a repeated IN?
  - Is the flag cleared in every path a new SETUP or a bus reset can take?
  - Can the 16-bit peek misread a different request, for example a SETUP with a CRC error, or a short packet?
- **Recheck the cycle counts** against the ISA (one instruction per tick) for the new path, and the IN → NAK +1.
- **Confirm the two race-test changes don't weaken anything else.** Does any mutant now survive that died before?
- **Read `usb_prestage_ab.py` for board safety**, since only the orchestrator runs it:
  - every PMOD and board rule in RULES.md;
  - its stop paths;
  - ABBA order, the checkout guard and the statistics.
- **Rerun** the model suite at ab2db8e and the cocotb `test` suite.
- **Privacy grep** of the changed files and commit messages.

The usual rules: `--ignore=test/model/test_swd.py` on any model run, and no Kria.
