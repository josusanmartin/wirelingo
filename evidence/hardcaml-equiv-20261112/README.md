# The Hardcaml port proved equal to the retimed RTL (2026-11-12)

The independent verifier's rerun of `ocaml/formal/equiv_port.sh -j 4 --mutants`
(after `dune build`) on commit `7a243eb`, the CRC retime of `src/pe_core.sv`
(docs/physical.md, "The CRC step off the decode path"), as reported in its
item 088 (verdict PASS). That report is not in this repository; this file
copies its figures and a short summary. `src/*.sv` and `ocaml/rtl/` are
unchanged from `7a243eb` to the 3-core release candidate's shipping commit
(`git diff --stat 7a243eb <commit> -- src/ ocaml/rtl/` lists only
`src/config.json` and `src/protoemu.sdc`, the hardening config and the SDC),
so the proof is of today's RTL.

Evidence level: formal (yosys `equiv_simple` and ABC `&cec` on the
generated Verilog against `src/`). Not gate level, FPGA or silicon.

| Build | Result | Flops matched | Instances matched (latches, clock gates, SRAMs) | Compared points | Planted bugs caught | Time |
|---|---|---|---|---|---|---|
| `tapeout`: 3 cores, the 8x4 release build | EQUIVALENT, ABC `&cec` equivalent | 2349 of 2349 | 1618 of 1618 | 10763 of 10763 | 10 of 10 | 305 s |
| `--build fallback`: 2 cores | EQUIVALENT, ABC equivalent | 1830 of 1830 | 1080 of 1080 | 7978 of 7978 | 10 of 10 | 186 s |

Both equal the figures of the item it checked. They differ from
[docs/hardcaml-port.md](../../docs/hardcaml-port.md)'s 10,412 and 7,744
points, which are of the RTL before the retime: the retime adds 39 flops per
core.

Summary of the rest of that report, for context: the retime was found
cycle-exact by its reading of the diff, the repository's unbounded core
proof (rerun, PASS), 14 of 14 formal and 9 of 9 lockstep CRC mutants caught,
9 mutants of its own (7 caught; the other 2 were equivalent rewrites that
must still pass, and did), and lockstep against the model at 3 and 2 cores
(8 of 8 each). The model suite passed (3151 passed, 29 skipped). Its
own old-against-new miter of `pe_core` did not finish, and it ran no
placement, routing, harden or gate-level simulation.

Redactions: none were needed; the figures and the summary hold no path,
host name or address.
