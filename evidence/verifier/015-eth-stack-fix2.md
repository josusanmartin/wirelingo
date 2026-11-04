Commit: 87a1b70  Base: d2d89b8  Branch: eth-stack
Item: fixes for inbox/010-eth-stack-fix.report.md, all findings. Logs: runner1 ~/protoemu-flow/eth-stack/logs/363fc2e.
Claims:
1. SEL is restored even if clearing QUAD_EN fails; a stuck master leaves QUAD_EN set and reports it. SIGINT, SIGTERM and SIGHUP raise for the session; further signals are ignored. Fake register file only. Rerun your own stuck-master and signal tables. Check what the code leaves behind when a signal lands inside the restore itself.
2. Sustained traffic row: 16.0 us at 50 MHz, 15.5 at 40 MHz, asserted both sides. Go/no-go stays 15.5.
3. An unmatched mutant pattern now errors. 21/21 mutants killed.
4. Model 1527 passed; RTL 7/7, TAP and gate level 3/3 at 400b294: confirm same code as 87a1b70.
