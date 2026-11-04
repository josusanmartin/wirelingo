Commit: 0bdcbe5  Base: 1fddeef  Branch: improve-2026-11 (also carries merged explain-docs and evidence commits; review only test/model/test_eth10_match.py, docs/eth10-cut-through.md and fpga/kv260/README.md)
Item: fixes for inbox/004-eth10-match-fix2.report.md findings 1-3.
Claims:
1. The rise is now bounded directly: last field mid-bit to last edge, minus 10 to 11 clocks. Orchestrator ran delay N = 1, 3, 100: the test fails at each; unmodified passes.
2. The log's line range and omissions are stated. "250 ns" is labelled as the test sender's.
