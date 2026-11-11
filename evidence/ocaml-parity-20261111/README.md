# OCaml and Hardcaml port parity run, v0.3, 2026-11-11

The result files of one run of `ocaml/scripts/evidence.sh` on runner1, at commit 0f98335
(branch `au-ocaml-v03`, base `next-8x4` 25214f2), as the script wrote them, and of the
reruns after it. [docs/hardcaml-port.md](../../docs/hardcaml-port.md) takes its numbers from
them. The port is of the v0.3 RTL, at 3 cores (the 8x4 tapeout build) and at 2 (its
fallback). The generated Verilog is the same at 0f98335 and 567bff6 (sha256
`26af0b599ca74cd2…` and `3c95ce7b28834149…`).

Level: **simulation and formal only**. The formal equivalence of the generated Verilog with
`src/` (yosys, ABC), RTL simulation in Icarus (cocotb), and the OCaml cycle model against
`tools/pe/sim.py`. Not gate level, not the board, not silicon.

| file | what |
|---|---|
| `summary.txt` | evidence.sh at 0f98335: the commit and `src/` tree, the toolchain, the three phases and wall time (5490 s), every step's exit status, both equivalence verdicts (3 and 2 cores), each cocotb suite's count, the model suite's totals at stride 64 and at stride 1, the assembler, host and run parity counts, `line_cyclesim`, and `self_check.sh`'s missed count |
| `rc.txt` | each step's exit status in the order the steps finished |
| `kv260-856194f.txt` | the lockstep totals of `test/model/test_kv260_3core.py` alone at strides 1 and 64 at 856194f, the commit where `lock_chip.py` compares the model's own bus read. They were printed by the same summing as the gate's, from the run's JSON totals. pytest's line, `73 passed`, was read on the console and is not in the file |
| `model-567bff6.txt` | `ocaml/scripts/stage3_model_suite.sh` (the model gate, `WL_JOBS=5`, stride 64, then stride 1) at 567bff6 on `next-8x4` 27ac3f7, with `capture_plugin.py` passing SWD source through unrecorded: exit status 0 |
| `self-check-567bff6.txt` | `ocaml/test/self_check.sh` at 567bff6, with its new clean baseline first: exit status 0 |
| `cocotb-id-567bff6.txt` | `ocaml/scripts/run_cocotb.sh` at 567bff6 for `test_spi_packed_host` and `test_uart_capture_host`, the two suites d68c7ec fixed, on the generated 2-core Verilog: exit status 0 |

The three `567bff6` runs were made from a clone of a commit with the same tree as 567bff6,
`1ea4cc7926921da9f12463890b317e038f2e58b5` (the tree id of 567bff6). That commit differed
only in its committer line, which was corrected before publication, so the checked-out
files were these.

Two steps of `summary.txt` exit 1. Both are explained in docs/hardcaml-port.md and both
pass at 567bff6:

* `cocotb_b`: `test_spi_packed_host` and `test_uart_capture_host` checked the ID register
  against v0.2's value, and failed the same way on `src/`. d68c7ec fixes the two tests;
  `cocotb-id-567bff6.txt` has 1/1 each.
* `model`: 2 stride-1 mismatches, both a test's stubbed bus read in `test_kv260_3core.py`.
  `kv260-856194f.txt` is that file after the fix. `model-567bff6.txt` is the whole gate:
  3149 passed, 29 skipped, 0 mismatches at stride 1.

Every other step exited 0. The rest of the output (step logs, cocotb work trees, the
equivalence work files, the corpus) is kept on runner1 in the branch's work directory. It
is not in git.

## Redactions

None needed. All six text files were searched for runner and home directory paths, tailnet
names and addresses, private LAN addresses, email addresses, the build machine's provider
hostname, and anything like a key or token. Nothing was found and nothing was replaced. The
scripts print result lines, test names and totals, not paths.

## sha256

```
5e93e861c45c1f8392fd994289323ab02d55b5f773fd6e7172be26d553e02457  summary.txt
e5d1c63753f2aabd984fab006580be5caa33a46bfc9dfd308575d7c364736b04  rc.txt
dc3c4114a75b827322676fe71b742329477e0ce9d593450f073278a6e719da06  kv260-856194f.txt
1737325d462e2432012e69091f8170e5b4b7839d16ba6fa3fa08c524ada67f87  model-567bff6.txt
266a75d431db2082be03bf4f6896f10f18b5fa604a148e73306887975cf658e9  self-check-567bff6.txt
7a39672d96a9d713570ce7d73dd688dbdcd2b42385d3e80bb3179d66db597af1  cocotb-id-567bff6.txt
```
