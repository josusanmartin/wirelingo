Commit: 48fcce0  Base: 97e442d  Branch: fabric-ping  (follows 029 FAIL on da82a51)
Item: fixes for 029's two blocking findings and notes 2-4.
Builder's claims:
1. Finding 1: run_closing takes a builder, installs SIGTERM/SIGHUP handlers, then builds the backend; fabric_eth.board_rig builds Board, Spim and Lab itself and on any exception or interrupt gives back what it touched (quad off, PMOD disabled, pe_lab GPIO mode, host port on GPIO), signals ignored during the give-back, the original error re-raised; a guard refusal still exits 2. Fake-KV260 test through eth_tap.main with five faults (exception, wrong chip ID, Ctrl-C, SIGTERM, SIGHUP), asserting quad was on when each timed fault hit and the end state; HOSTCFG is write-only, so the earlier "quad off" check read 0 always: tests now read the model's quad state. Mutants: give-back skipped, quad left on, rig built before handlers.
2. Finding 2: a model probe now records link pulses and frame edges per divider phase; compare requires the board's pulses and edges together to equal the model at one phase; old model files are refused; the probe requires exactly one 100 ns link pulse before each frame. Mutants: compare ignores pulses, probe ignores pulses.
3. Notes: zero-word blind spot stated in docs/eth10-network.md; --pcap now reports tcpdump's exit status, output and file size and is never part of the pass; stale "07fef6e" wording fixed (doc now names 86fe42d, same eth_tap.py and fabric_eth.py as 48fcce0).
4. Model suite 2370 passed, 28 skipped; `mutants.py 'hw-*' 'fab-*'` 99/99 killed. The existing board file evidence/029/fabric-ping-b.json gives SAME against a fresh 48fcce0 model file (phases 1, 4, 0; link pulse [[5,5]] each).
Board: the orchestrator will run a short smoke run of steps 1-12 with 48fcce0 (new rig-construction code has only run on the fake board) and append the result here as an addendum. Verify the code claims now; the addendum follows. Kria: verifier does not touch it. No SWD.

## Addendum: board smoke run of 48fcce0 (orchestrator, 2026-11-03 ~21:55 UTC)
protoemu loaded alone (unload, load, listapps each read), guard printed `protoemu`.
`eth_tap.py --backend fpga --probe`: KV260 at 50 MHz, 49.99967 MHz, RTL d1aa46f7ed7e; arp, ping, udp
reply 64 bytes, FCS ok, one link pulse each ([(5,5)], [(4,5)], [(4,5)]); bad_fcs, other_mac no reply;
probe PASS, rc 0. `--compare` against a fresh 48fcce0 model file: arp/bad_fcs/other_mac link pulses
[[5,5]] + 947 frame edges = model phase 1; ping [[4,5]] + 939 = phase 2; udp [[4,5]] + 917 = phase 2;
SAME. protoemu still the one active row afterwards. Selftest (step 13) not repeated.
Evidence: ~/dev/protoemu-verify-reports/evidence/031/ (board and model JSON, archive sha256).
