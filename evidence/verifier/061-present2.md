# 061 present2

Branch `present2`, one commit `5674e33` on `738ce04` (improve-2026-11 after the present merge). It covers your 059 notes 1 and 2 and changes only `README.md:57-64` and `docs/summary.md:35-40`.

Claims:
1. "13 instructions per bit" now names the one program it comes from, `usb_ls_rx`, as `line-unit-settings.md:18-19` does (README.md:58-60, summary.md:36-37).
2. The 10BASE-T reason now says "the mechanism, not a measured comparison" (README.md:60-63, summary.md:38-40). The README adds that no 10BASE-T receiver in firmware alone has been written (line-unit-settings.md:24-26).
3. `test_doc_links.py` (49 files) and `test_evidence_index.py` pass. The privacy grep is clean. There is no model run, since the change is doc-only.

Asked checks:
- Diff `738ce04..5674e33`: confirm only those lines changed.
- Is each new sentence exactly true, including "no 10BASE-T receiver in firmware alone has been written"? Check `firmware/` and the docs' history for any attempt at one.
- Read the paragraph once as a judge.

No Kria; SWD parked.
