Commit: c4ea377  Base: 97e442d  Branch: fabric-ping  (follows 031 FAIL on 48fcce0; f86eb34 is the code, c4ea377 adds doc counts only)
Item: fix for 031's blocking finding and notes 1-3.
Builder's claims:
1. Blocking: FpgaBackend.__init__ closes the rig on any BaseException (Ctrl-C included), with SIGINT/SIGTERM/SIGHUP ignored during the close. New test through eth_tap.main(["--backend","fpga","--probe"]) on the fake KV260 faults the first asm.assemble inside FabricEth.__init__ with exception, Ctrl-C, SIGTERM, SIGHUP; asserts quad on at the fault, and afterwards SEL on GPIO, quad off, pe_lab GPIO, PMOD writes all 0. Mutant fab-backend-ctrl-c-kept (Exception put back) killed.
2. Note 2: give_back reads SEL inside step(); a test makes the read raise and checks the other steps ran; mutant fab-give-back-sel-read-unguarded killed; fab-give-back-leaves-quad site moved.
3. Note 1: a Ctrl-C at the ID read (after select(fabric=True), before the clock measurement) is also given back.
4. Note 3: this branch already says the TD sampler is on core 0; the remaining "core 1" mentions are core 1 sending and its divider. The evidence/fabric-ping-20261103/README.md copy is on improve-2026-11 (not this branch); the orchestrator applies the builder's sentence there at merge.
5. docs/eth10-network.md covers the FabricEth window and cites reports 029 and 031.
6. Model suite at f86eb34 2376 passed, 28 skipped; mutants 'hw-*' 'fab-*' 101/101 killed (22 fab, 79 hw); model selftests (50 and 40 MHz) and probes pass. Logs runner1:~/protoemu-flow/agent-runs/fabric-ping/logs/f86eb34/.
Board: no new board run; the change is the error path only (031 addendum's smoke run covers the build path). Kria: verifier does not touch it. No SWD.
