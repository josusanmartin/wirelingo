# OCaml and Hardcaml port parity run, 2026-11-04

The two result files of one run of `ocaml/scripts/evidence.sh` on runner1, at
commit 89675b6 (branch `ocaml-parity2`, base 9dd256e), as the script wrote
them. [docs/hardcaml-port.md](../../docs/hardcaml-port.md) takes its numbers
from them.

Level: **simulation and formal only**. The formal equivalence of the generated
Verilog with `src/` (yosys, ABC), RTL simulation in Icarus (cocotb), and the
OCaml cycle model against `tools/pe/sim.py`. Not gate level, not the board, not
silicon.

| file | what |
|---|---|
| `summary.txt` | the commit and `src/` tree, the toolchain, wall time (1899 s), every step's exit status, the equivalence verdict, each cocotb suite's count, the model suite's totals at stride 64 and at stride 1, the assembler, host and run parity counts, `line_cyclesim`, and `self_check.sh`'s missed count |
| `rc.txt` | each step's exit status in the order the steps finished |

Every step exited 0: `dune_test`, `equiv`, `cocotb_a` to `cocotb_e`, `model`,
`asm`, `host`, `run`, `cyclesim`, `self`.

The rest of the output directory (step logs, cocotb work trees, the
equivalence work files, 558 MB) is kept on runner1 at
`~/protoemu-flow/ocaml-port/ev-89675b6`, and the earlier run of the unchanged
port on a clean checkout of 9dd256e, which also passed every step, at
`~/protoemu-flow/ocaml-port/ev-9dd256e`. Neither is in git.

## Redactions

None. Both files were searched for tailnet names and addresses, private LAN
addresses, email addresses, the build machine's provider hostname, home
directory paths and anything like a key or token: nothing was found and nothing
was replaced.

## sha256

```
646bba95fd82d683a8204a66a88e44d194164d290a9ab315db3f4c9fcb66f67e  summary.txt
e788d1fafe92be50343041c79affd139e94ee6551e4c8be187f6f14c8cfa2412  rc.txt
```
