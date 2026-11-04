Commit: 8a6dd8f  Base: 80cc0b9  Branch: hw-kit
Item: fixes for inbox/016-hw-kit.report.md, all findings and notes, on top of the guard in item 019. If 019 is not yet started, review both at 8a6dd8f together. Do not touch the Kria.
Claims: each of 016's findings 1-6 and notes is fixed as follows. Check each, then re-attack the whole launcher and runbook as a beginner would use them.
1. Step 16 removes the USB wiring, plug first; usb-pi clears usb-off.
2. root() is computed on use; tested from /home/pi/hw_day.py.
3. mkdir -p before rsync; tested against a local directory.
4. The whole prerequisite chain is checked; confirmations expire after 12 h.
5. Both rules stated with no exceptions; the Kria cable move is replaced by pi-net.
6. Mutants are in test/mutants.py: `python3 test/mutants.py 'hw-*'`, 56/56.
7. --active-app can't override a readable table; what xmutil shows after a failed load is unverified.
8-12. hd off reports when nothing was written; Auto-MDIX reworked for disabled pins; no two parts share a row; -0.57 V compared with the -0.55 V minimum, cable out first; cut and ping print wiring; corrupt state refuses in words.
Model 1883 passed, 28 sigrok skipped. Fakes only.
