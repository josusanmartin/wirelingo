Commit: ceb84a4  Base: d811228  Branch: rmii-study
Item: fixes for inbox/002-rmii-study.report.md findings 1-6. Check each, then re-attack what changed.
Claims:
1. Stall budget per host, worst of all phases, same in both SCK phases: 69, 66, 63, 60, 57 clocks at 8 to 12 clocks per read; 43 for 7, 8 or 12-word bursts. Two stalls at 12 clocks per read get 28 each. These sit below your own 71/59/45: confirm the wider sweep is right, not merely lower.
2. Figures are exact asserts; your rxf mutant now fails 22 of 22 budget tests.
3. 189 bytes exact. TX stall cushion 0/46/94/142 clocks replaces 40/90/140. Turnaround deadlines exact at 12/11/10.
4. rmii_match limits stated and tested: 16-bit wrap, 12 hits giving 8 records, HIT is a latch.
5. STATUS.stalled documented and tested: 0x1000 for both underrun and idle.
6. RP2350 rated 150 MHz, doc flags 160 MHz as an overclock.
