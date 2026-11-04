Commit: b2560de  Parent: 7f7a70f  Base: 575362a  Branch: fault-fixes
Item: fixes for inbox/027-fault-fixes-fix.report.md.
Claims:
1. The three sentences are labelled: README.md KV260 bullet (2026-10-14 replay was the 246-word usb_enum; 253-word needs a KV260 rerun); README.md "Where it stands" netlist sentence (receipts ran software 7685aba; the four changed programs need a gate-level rerun); docs/verification.md SDF sentence (same). physical.md:647 says "with the firmware of that run". Re-run your 29-file grep: nothing else unlabelled.
2. From your note 2 (a test change): test_usb_enum_short_se0_is_not_a_reset now drives a 2.4 us SE0 instead of 1.9 us; the real firmware gives 0 failures, and each timeout lowered to 100 ticks is caught at all three waits (21, 16 and 3 failing start points). Check 2.4 us is still below the firmware's 2.52 us threshold with margin for clock tolerance, so the test is not asserting something the firmware should reject.
3. Model suite at b2560de: 2146 passed, 0 skipped, SIGROK_CLI set.
If this passes, the whole fault-fixes branch (b5496aa..b2560de) is accepted and will be merged into improve-2026-11.
Kria off limits; no SWD.
