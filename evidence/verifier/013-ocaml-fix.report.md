VERDICT: PASS

Commit cca723c, base 03edc75. All of 006's findings are resolved, and I reran the whole of `evidence.sh` at this commit: every step passed. The equivalence result is unchanged. The wave tests' assertions are real. One doc sentence about them is wrong and should be fixed (note 1), but no item claim is false, so I am passing it with that note. If you hold a wrong sentence in a doc to the same line as a false claim, treat note 1 as the blocker.

| Claim | Result |
|---|---|
| 1. `self_check.sh` adds `test_i2c_timeout.py`; 14/14 | PASS: 14 caught, `missed: 0` |
| 2. Host parity 685/330/145/145 | PASS, reproduced exactly, 57 of 57 files |
| 3. `evidence.sh` runs `self_check.sh`, checks `dune test`, has floors | PASS: I broke coverage and the floor fired |
| 4. About 63.7M cycles; `lru_cache` cause; `--dist loadfile` | PASS: cause reproduced to the cycle |
| 5. Expect tests assert, and fail when the block is broken | PASS, notes 1 to 3 |
| 6. Equivalence unchanged | PASS: no source changed, same Verilog hash, proof rerun |

## Claim 5: do the wave tests fail when the block is broken?

Yes. I planted 11 bugs and looked at whether `[%test_result]` itself fired, not just whether the printed waveform changed.

| Planted in | `[%test_result]` fired |
|---|---|
| `pe_fifo`: pop not guarded at empty | yes |
| `pe_fifo`: read pointer steps by 2 | yes |
| `pe_fifo`: clear ignored | yes |
| `pe_fifo`, NO_CG path: always writes entry 0 | yes |
| `pe_fifos`: join moves from an empty TXF | yes |
| `pe_line`: TX level inverted | yes |
| `pe_line`: END symbol low | yes |
| `pe_line`: END symbol half length | yes |
| `pe_line`: frame end never pending | yes |
| `pe_line`: `quiet` saturates at 14 | no: NRZ RX has no wave test, and the doc's table claims none |
| `pe_fifo`, **proved path only**: always writes entry 0 | no: Cyclesim never runs that path |

The last row is the disclosure working as stated. I ran that bug through the proof, and it gives ABC not equivalent and `NOT PROVEN`. So the wave tests and the proof cover different ground, as the doc says.

The `PE_FIFO_NO_CG` disclosure is clear: `docs/hardcaml-port.md` and `ocaml/wave_test/bench.ml` both say the FIFOs run with enable flops, not the proved latches, and that form is not proved.

## Notes

1. `docs/hardcaml-port.md:220`: "so a promoted waveform cannot bless a wrong result" is false as written. When `[%test_result]` raises, `dune --auto-promote` records it in the source as `[@@expect.uncaught_exn]`. In 5 of my 9 visible bugs the test then passed. In the other 4 it still failed. It would show plainly in a diff as an exception in the test file, so the risk is small. Fix: say the assertion fails the test and that promoting would have to write the exception into the source.

   My first classification of these mutants was wrong because of this: I read "passes after promote" as "waveform only". Recording whether the assertion fired, as above, is the correct measure.

2. `docs/hardcaml-port.md:225`: "8 words in order, the ninth dropped" is the joined depth of a test build with `log2:2`. The tapeout's FIFOs are 8 deep, so 16 joined. Neither the table nor the test's name says so.

3. The item says six expect tests. There are 7: 2 in `test_fifo.ml` and 5 in `test_line.ml`.

4. `docs/hardcaml-port.md:151`: corpus "287 distinct of 1412" is from an earlier run. This run gave 1410, which matches the `--dist loadfile` chip count of 1262 and the doc's other figures from 2dddff7.

## Other claims

- **Claim 2, your question about `[3:6]`:** the builder's choice is better than my suggestion. It filters to sets that assemble, takes discovered sets first, and falls back to fixed ones, so all 57 files are covered. My fix gave 56 of 57 and fewer programs. I reproduced `{'files': 57, 'export': 685, 'export_err': 330, 'writes': 145, 'listing': 145, 'bad': 0}`.
- **Claim 3, floors really fire:** I changed `host_parity.py` to take 1 define set per file. It reported `BELOW FLOOR` with 295/132/61/61, rc 1, and 0 disagreements. That is exactly the kind of silent loss from 006. `dune test` status now reaches `rc.txt`, and `self_check.sh` runs as a step.
- **Claim 4:**

  | `test_usb_uart_pairs.py` | Cycles |
  |---|---|
  | Serial | 408,741 |
  | `-n 8` | 573,340 |
  | `-n 8 --dist loadfile` | 408,741 |

  The whole suite gave 1262 chips and 63,703,566 cycles here, within the doc's "about 63.7M". The lock wrapper's change, which stops counting cycles after a mismatch, is reasonable and stated.
- **Claim 6:** `git diff 03edc75 cca723c -- ocaml/rtl ocaml/bin/gen_rtl.ml ocaml/formal src formal` is empty. Adding `hardcaml_waveterm` to the switch did not change the output: generated Verilog is `3b9a573559b1a1c1...`, the hash I attacked in 006. The proof reran anyway: `EQUIVALENT`, 7444 points, 8 of 8 mutants.
- **"Docs only after":** `2dddff7` to `cca723c` touches only `docs/hardcaml-port.md` and `ocaml/README.md`.

## 006's findings

| # | Status |
|---|---|
| 1 `self_check.sh` exit 1 | Resolved: 14 of 14 |
| 2 Host parity figures | Resolved, and floors now guard them |
| 3 `evidence.sh` could not notice | Resolved |
| 4 Exact cycle count | Resolved: about 63.7M, cause found, residual 0.1% stated as unfound |
| 5 SWD conflict | Closed by `remote_test.sh`; `RULES.md` updated |
| 6 Minor: duration | Resolved |

## What I ran

All on runner1 at cca723c, in `verifier-013/`, since removed.

- `setup_runner.sh`: rc 0. `dune build`: rc 0.
- `ocaml/scripts/evidence.sh`: rc 0, wall time 2346 s, all 12 steps including `dune_test` and `self`.
  - `equiv`: `EQUIVALENT`, 7444/7444 and ABC equivalent, 8 mutants caught
  - `test` 25/25, `test_post_freeze` 16/16, lockstep 6/6, flop4 6/6, the other six suites pass
  - lockstep3 7/7 at 102,738 cycles on generated Verilog and on `src/`, 7/7 at 227,254
  - assembler `DISAGREE=0` in all four sets; `PARITY`
  - Cyclesim 2,000,000 cycles; `wl_run` 3714 lines
  - model suite 1394 passed, 23 skipped; 0 mismatches at stride 1
  - `self_check.sh`: 14 `caught=yes`, `missed: 0`
- 11 wave mutants, run twice (see note 1), the proof on the proved-path bug, the floor test and the `lru_cache` runs above.
