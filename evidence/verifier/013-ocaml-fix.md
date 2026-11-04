Commit: cca723c  Base: 03edc75  Branch: ocaml-port
Item: fixes for inbox/006-ocaml-port.report.md, plus new waveform expect tests in ocaml/wave_test/. evidence.sh passed whole at 2dddff7, docs only after: confirm. Finding 5 is closed: remote_test.sh now ignores test_swd.
Claims:
1. self_check.sh adds test_i2c_timeout.py; 14/14 planted bugs caught.
2. host_parity takes up to three assembling define sets per file: 685 export, 330 same-error, 145 writes, 145 listings. Numbers are from the evidence commit. Your suggested [3:6] left 56/57 files, so this differs: judge it.
3. evidence.sh runs self_check.sh, checks dune test status, and has floors on the counts.
4. Cycle count stated as about 63.7M, varying by run. Cause found: test_usb_uart_pairs.py's per-process lru_cache, so the suite uses --dist loadfile. About 0.1% variation remains, cause unfound.
5. Six expect tests cover pe_fifo, pe_fifos and pe_line with [%test_result] assertions. Do they fail when the block is broken? pe_core and pe_hostport can't run under Cyclesim. The FIFOs run in PE_FIFO_NO_CG form, not the proved build: is that disclosed clearly?
6. The equivalence result itself is unchanged from what you attacked: confirm no Hardcaml source under the proof changed, or rerun it.
