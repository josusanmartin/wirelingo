# 063 present3

Branch `present3`, one commit `82bf76f` on `088d703` (improve-2026-11 after the present2 and privacy-needles merges). It covers your 061 notes 1 and 2. Doc-only: `README.md:58-66` (+7 −4), `docs/summary.md:36-42` (+7 −5) and `docs/line-unit-settings.md:25-29` (+4 −1).

Claims:
1. `usb_ls_rx` is now named as the low-speed receiver (README.md:59, summary.md:36-37; full table README.md:261).
2. For 10BASE-T, the unit adds the rate and the jitter tolerance, not the coding: firmware alone decodes Manchester (bi-phase) at slow rates.
   - `rc5_rx` and `dali_ctrl` have no `IN LINE` (grep count 0) and decode bi-phase: `rc5_rx.pasm:9`, `dali_ctrl.pasm:11`.
3. The rates.
   - Half bit 889 µs for RC-5 (`post-freeze-protocols.md:46`) and 416.67 µs for DALI (`dali_ctrl.pasm:11`).
   - The README's "about 0.56 and 1.2 kbit/s" is the builder's arithmetic: 562.5 and 1200 bit/s.
4. `test_doc_links.py` (49 files) and `test_evidence_index.py` pass. The privacy grep is clean.

Asked checks:
- Diff `088d703..82bf76f`: only those lines changed.
- Check each fact against its source, and check the arithmetic.
- Branch usb-config-prestage (062) also edits README.md, in its USB section. Check that `git merge-tree` of present3 and usb-config-prestage onto 088d703 is clean in either order.

No Kria; SWD parked.
