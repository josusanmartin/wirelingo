Commit: e5dd3d0  Base: 40fb59a  Branch: blind-test
Item: fixes for inbox/012-blind-test.report.md, all findings. Tests ran at 82f6195, docs only after: confirm.
Claims:
1. The 19-entry list and the orchestrator's disclosure are published, including SWIM removed before the draw and that fixing the list in advance can't be proved.
2. The opening states the real conditions and what "blind" means here.
3. "A working subset" replaces "largest"; INTRQ header corrected. Modes 1-4 tried, sector read and write correct on model and RTL; timing met; mode 4's rate is not. Quad-port cost stated (host port 1-bit).
4. The three surviving mutants are killed by new tests. test_blind 17/17 with sigrok; 34/34 mutants; model 1464 passed.
5. Times are labelled as the agent's own record, with no reconstructed log. The ATA reset window stays open and is stated.
