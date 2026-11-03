Commit: 03edc75  Base: 61af9b2  Branch: ocaml-port
Item: ocaml/ (Hardcaml port of src/, assembler, model, some host tools), docs/hardcaml-port.md. Numbers came from 0027302 and eff2b0f; evidence.sh has not run whole at the final commit, so run it on runner1 at 03edc75: `ocaml/scripts/setup_runner.sh SYSROOT`, `ocaml/scripts/evidence.sh OUT`, `ocaml/test/self_check.sh WORK`. Do not rely on any figure you did not reproduce.
Claims:
1. Generated Verilog passes cocotb: test 25/25, test_post_freeze 16/16, tapeout lockstep 6/6. Lockstep needs a read-only shim outside the proof: judge whether the shim could mask a difference.
2. Equivalent to src/ in the tapeout build: 7444/7444 points by equiv_simple, ABC equivalent, 1652/1652 flops, 1080/1080 instances, 8/8 planted bugs caught. Attack this one hard: confirm the gold side really is the unmodified src/, that blackboxes do not hide logic, that assumptions or matched pairs cannot make it vacuous. Plant your own bug in the Hardcaml source and show the proof fails.
3. 57 firmwares byte-identical to the Python assembler; 6202 cases including error parity and fuzz, 0 disagreements. swd_probe excluded.
4. RTL = sim.py = OCaml on 102,738 cycles at CI seeds and 227,254 at new seeds, 0 mismatches.
5. Model suite except test_swd: 63.9M cycles, 0 real mismatches; 10 poked chips and 12 deep copies compared only up to the poke or copy. Judge "real".
6. host, profile, export, disasm, listing and modelhost ported with parity.
7. Docs state evidence levels honestly and list what is unported and unproved (flop4 lockstep only, PE_FIFO_NO_CG never run, timing.py unported).
Note: this one is long. Use runner1 only.
