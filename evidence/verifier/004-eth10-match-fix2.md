Commit: 1fddeef  Base: aedf991  Branch: improve-2026-11
Item: fixes for inbox/001-eth10-match-fix.report.md findings 1-4. Check them, then re-attack only what changed.
Claims:
1. The rise-to-last-edge relation is now asserted exactly at both clocks and for frames ending in 0 and in 1: 384 bits, minus the reaction, plus 250 ns of TP_IDL if the frame ends in 1. The doc's ranges follow from it with the 10-11 clock reaction at +100 ppm.
2. The doc now says the FPGA row can't show the 10-11 spread, and why.
3. The orchestrator compared every quoted log line with the Kria's /var/log/wl_steps.log lines 1691-1702 by script: all present. Header and PASS lines are now quoted.
4. The heading anchor is fixed by shortening the heading.
