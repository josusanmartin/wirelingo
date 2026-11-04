Commit: edc9042  Base: bc98a97 (improve-2026-11)  Branch: usb-guard  (follows 045 FAIL on 326de49; firmware and software unchanged)
Builder's claims:
1. Tracer.close() never raises: each restore tried on its own, OSError caught per file; buffer_size_kb written back only when the saved text is an integer, otherwise left at 16384 with "left at 16384, it read …"; returns what it couldn't restore.
2. main() prints those as [tracing] lines and still runs the zero-length writes and reads, the PASS/FAIL line and summary.txt.
3. New --trace-root (model runs only) points the capture at a fake tracefs; test runs one attach, a write and a read on the model for each kernel form ("1408", "7 (expanded: 1408)", "X"), the fake buffer_size_kb refusing a non-integer with EINVAL like the kernel; each must finish, PASS, write summary.txt, and never write anything but an integer back. Mutant hw-guard-run-raw-buffer-size killed (1/1).
4. Optional note 1: label test also looks for "every device edge at the clock the model gives"; per-paragraph check not done (one firmware note covers a section's paragraphs and code block).
5. Model suite at edc9042 2603 passed, 29 skipped.
Context, not for this item: the builder's Y work (usb-ls-restage, not yet submitted) reproduced your 044 note 3 at full speed too (a normal stage abandoned by a new SETUP: old reply at n=0-1, partial packet at n=2-4) and fixes it there in software; usb-guard alone does not cover that case. The orchestrator board-runs usb-guard after this passes; Y comes later.
Kria: verifier does not touch it. No SWD; --ignore=test/model/test_swd.py on every model run. Check: --trace-root cannot be used on a real board run (model only), and the [tracing] path end to end.
