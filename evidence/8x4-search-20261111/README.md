# 8x4 settings searches (2026-11-11)

The small records of the place-and-route settings searches for the 8x4
release netlist (backlog item AQ), one folder per harden. The table and what
it says are in [docs/physical.md](../../docs/physical.md#8x4-settings-searches-2026-11-11).
Evidence level: harden (LibreLane 3.1.0.dev3 in its docker image, on the
compute runner), plus static timing of the finished runs. No gate-level or
silicon result is in `fallback/`; `fallback-release/` holds the fallback
candidate's release receipts.

* `fallback/<run>/`: the 2-core fallback (the package RTL with `PE_NCORES` 2,
  `flow/harden.sh <run> 2 -- <options>`), one folder per run:
  * `spec.txt`: the run's `flow/harden.sh` arguments; `time.txt`: its exit status and minutes;
  * `result.txt`: `flow/harden.sh`'s summary (commit, tile, banks, density,
    congestion, `flow/signoff.py` verdict, netlist and GDS sha256 prefixes);
  * `sta-summary.rpt`: the final STA step's summary table, all corners;
  * `sta20-flop.txt`: worst flop/SRAM endpoint per corner at 20 ns
    (`syn/8x4-study/sta_period.sh`; the summary's setup WS is 0 when only
    the FIFO latches' borrowing is left);
  * `sta48.out`, `sta20.832-<corner>.metrics`: a fresh STA at 20.832 ns
    (`flow/sta_rerun.py`, the 48 MHz release period), its `%OL_METRIC` lines;
  * `placement.txt`: global placement utilization, detailed placement HPWL
    after each legalization, the late-clock register count;
  * `table.txt`: `tools/table.py`'s reading of the above and of the run's
    `final/metrics.json` (kept on the runner with the full log);
  * `config.json` (the two candidates only): the run's generated `src/config.json`.
* `fallback-release/`: the fallback candidate `aqf-d44l8lcch15`'s release
  receipts and equivalence, run from a clean clone of `aq-fallback-val`
  (`15dd367`): `release-evidence.out` (the jobs and their exit status),
  `release-check.log`, `verdict.json` (without its candidate digest table),
  `archive.sha256` (the evidence archive, kept on the runner, not in the repository),
  `equiv.out` and `equiv.rc` (`formal/equiv/equiv_check.sh -j 4 --mutants`),
  and `negctl-3core-root.log`: the same receipts checked against the 3-core
  `aq-search` checkout (`998fccf`) as validation root, refused (it differs
  in `src/project.v` and in `test/`). Evidence level: gate-level and SDF
  simulation, Tiny Tapeout precheck, formal equivalence.
* `tools/`: the queue that ran them (`queue.sh`, one harden at a time) and
  the scripts that read the results (`table.py`, `mdtable.py`) and pruned
  run directories to the best two (`prune.py`).

On the compute runner, `~/protoemu-flow/aq-search/results/<run>/` also holds
each run's `metrics.json`, `resolved.json`, the full `exp.log` (gzipped), the
slow corner's max report head and the fast corner's min report head; the
best two run worktrees are kept in `~/protoemu-flow/aq-search/runs/`.

Redactions: the runner's home directory is written `~` (in `sta48.out` and
the `fallback-release/` logs). The committed `placement.txt` files keep the
`GPL-0019`, `DPL-0022` and `LATE_CLOCK_COMPLETE` lines and drop the
per-branch `LATE_CLOCK <net>: N registers behind S buffers` lines (one per
clock branch, 46 in the candidate; they are in the runner's
`results/<run>/placement.txt`).
Searched for tailnet hostnames and addresses, LAN addresses, email
addresses, the build machine's provider hostname and credentials: none.
