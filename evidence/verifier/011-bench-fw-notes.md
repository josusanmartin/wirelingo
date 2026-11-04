Commit: f47e875  Base: 5582d7a  Branch: bench-fw
Item: notes from inbox/008-bench-fw.report.md. Short review of what changed. Sigrok: SIGROK_CLI=the path shown by `find ~/protoemu-flow/agent-runs/bench-fw -name sigrok-cli`.
Claims:
1. After an aborted read the next read is lost and the one after is right, on the model. An abandoned write applies wrong data: stated and tested.
2. The 40 ns hold is stated as more than 40 ns with no margin; arithmetic, not measured.
3. A skipped sigrok check now shows SKIP=1 and SIGROK SKIPPED lines; CI's gap is stated.
4. REV_COMPLIANCE comment corrected. 1579 passed, sigrok RTL 6/6, mutants 28/28.
