# The 3-core 8x4 release candidate: reproductions, a perturbation and a release rehearsal (2026-11-14/15)

Small records of the runs behind the 3-core 8x4 release candidate described in
[docs/physical.md](../../docs/physical.md#the-3-core-release-candidate-2026-11-15)
and [docs/release-evidence.md](../../docs/release-evidence.md#the-3-core-8x4-release-candidate).
The candidate's own harden, aqp-rt10, is in
[8x4-search-20261114/](../8x4-search-20261114/README.md). All runs were on the
compute runner (runner1), LibreLane 3.1.0.dev3 in its docker image, one
harden at a time.

Evidence levels: harden and static timing (STA); gate-level simulation,
functional and with SDF delays (Icarus); Tiny Tapeout's precheck; formal
equivalence of the RTL and the final netlist under the checker's model
(`formal/equiv/`, [docs/equivalence.md](../../docs/equivalence.md)). Nothing
here is silicon, and nothing here ran on an FPGA.

| Folder or file | Run | What it holds |
|---|---|---|
| `rt10s/` | aqp-rt10s: a clean checkout of `1ea5cb7` (the commit whose submission preset is rt10's options), no options | `spec.txt`, `time.txt` (exit status and minutes), `result.txt` (`flow/harden.sh`'s summary, with the netlist and raw GDS sha256 prefixes) |
| `rt10s4/` | aqp-rt10s4: the same clean `1ea5cb7`, every container confined to 4 CPUs and 16 GB with no extra swap, LibreLane's core count 4 | the same three files; `container-limits.sh`, the container-engine wrapper that set the limits; `sta48.out`, the fresh STA at 20.832 ns |
| `rt10s5-pre/` | aqp-rt10s5-pre: a clean harden of `77c3936` through `flow/harden.sh`'s clean-tree path, then the full release receipts (a rehearsal of the release path) | `spec.txt`, `time.txt`, `harden.out` (the clean-tree verdicts at the start and after config generation), `result.txt`; `sta48.out`; `sta-nonlatch.txt` (`flow/sta_nonlatch.py`); `equiv.txt` (`formal/equiv/equiv_check.sh --mutants`); `gl-sdf-counts.txt` (test cases per JUnit file of the GL and SDF receipts, from `tools/junit_counts.py`); `release-evidence.log` and `verdict.json` (`flow/release_evidence.py`'s run); `release-check.log` (the release check rerun, below) |
| `rt10d48/` | aqp-rt10d48: the clean `1ea5cb7` with `--density 48`, a real placement change | `spec.txt`, `time.txt`, `result.txt`, `sta48.out`, `worktree.diff` (the generated config's two changed lines) |
| `gds-normalized.txt` | rt10, rt10s, rt10s5-pre | each final GDS's sha256 with its BGNLIB/BGNSTR dates zeroed, and its raw sha256 prefix, from `tools/gds_normalized.py` (the normalization of `flow/build_manifest.py`), run read-only on the kept run directories on 2026-11-15 |
| `late-clock.txt` | rt10, rt10s, rt10s5-pre | the `LATE_CLOCK_COMPLETE` line that `flow/sram_clock_advance.tcl` prints in each run's SRAM clock advance step log (64 registers on 23 branches each), taken read-only on 2026-11-15; the count `evidence/8x4-release-expected.json` holds |
| `density.txt` | rt10, rt10s5-pre | global placement's `GPL-0302` and `GPL-0023` lines: the configured target density 0.34 is below the free-area minimum, so placement runs at 0.47 (taken read-only on 2026-11-15) |
| `tools/` | | the two read-only scripts above |

What the records show:

* **Reproduction.** rt10, rt10s, rt10s4 and rt10s5-pre give the same netlist
  sha256 (`453d0af7bea77dd5…`, each `result.txt`; in full in
  `rt10s5-pre/release-check.log`) and the same `flow/signoff.py` summary
  (HPWL, utilization, DRC/LVS/antenna, per-corner STA lines). The normalized
  GDS of rt10, rt10s and rt10s5-pre is `357ea15b4546ef28…` (`gds-normalized.txt`;
  the raw GDS hashes differ by their dates only). rt10s4's run directory was
  pruned before its normalized GDS was recorded, so for rt10s4 only the
  netlist and the summary are recorded here. rt10s4's harden took 240 minutes
  (`rt10s4/time.txt`).
* **The rehearsal's release receipts** (`rt10s5-pre/`): functional GL 30/30
  (`test`), 2/2 (`test_pio`) and 2/2 (`test_eth_service`); SDF `standard` 5/5
  at typ 20 ns, slow 20.832 ns and fast 20 ns, with the slow 10 ns control
  failing as required; SDF `v2` 3/3 at the same three, plus USB full speed
  1/1 at 20.834 ns at each corner; autonomous USB (low and full speed) 2/2 at
  20.834 ns at each corner (`gl-sdf-counts.txt`). The fresh slow-corner STA at
  20.832 ns, the clock and reset checks and the precheck are verified in
  `release-check.log`. Equivalence: `EQUIVALENT`, equiv_simple 10754/10754
  proven, ABC equivalent, 9 of 9 planted netlist mutants caught (`equiv.txt`).
* **The release check.** `flow/release_evidence.py`'s own run of
  `release_check.py` failed on one line only: regenerating the configs from
  the shipping tree needs Tiny Tapeout's tool environment, which that run did
  not pass on (`release-evidence.log`, last lines; `verdict.json`; verifier
  item 100, finding 1, addressed by `d6dbd38`). Rerun from the prepared
  environment with the same receipts plus the non-latch STA receipt, it
  printed `RELEASE PASSED (SDF profile v2, slow target 48 MHz)`
  (`release-check.log`). This rehearsal's receipt format predates `d6dbd38`'s
  gate, and its tree is not the shipping tree, so it cannot release the
  shipping commit; the release needs its own clean harden
  ([docs/release-evidence.md](../../docs/release-evidence.md#the-3-core-8x4-release-candidate)).
* **Timing at 20.832 ns** (`rt10s5-pre/sta-nonlatch.txt`): worst non-latch
  setup slack +0.4304 ns at `_53246_/D` (slow), +3.3180 ns (typ) and +3.9191 ns
  (fast); the worst latch at slack 0, borrowing 0.937 ns (slow). Hold
  (`result.txt`): +0.212 / +0.395 / +0.105 ns typ / slow / fast. At 20 ns the
  slow corner's setup WS is −0.4016 ns with 13 violators (`result.txt`).
* **The perturbation** (`rt10d48/`): density 48 instead of 34 moves the
  placement (HPWL 2,182,471 against 2,185,983) and gives a new netlist; it
  passes `flow/signoff.py` with 0 DRC/LVS/antenna, but at 20.832 ns the slow
  corner's setup WS is −0.815 ns with 15 violators (`sta48.out`), and at 20 ns
  −1.647 ns with 71. The candidate's margin is specific to its placement.

`late-clock.txt` and `density.txt` name each log by its path inside its run
directory, so they hold no home directory.

Redactions: the runner's home directory in paths was replaced by `~`,
`<flow-root>` or `<aq-search>` (occurrences: 60 in `release-evidence.log`, 27 in
`verdict.json`, 12 in `release-check.log` and 1-2 in each of `rt10s4/spec.txt`,
`rt10s4/sta48.out`, `rt10s5-pre/sta48.out`, `rt10s5-pre/sta-nonlatch.txt`,
`rt10s5-pre/equiv.txt`, `rt10s5-pre/harden.out` and `rt10d48/sta48.out`). The
files were searched for tailnet names and addresses, private LAN addresses,
email addresses, other host names and credentials; none was found. Nothing
else was changed.
