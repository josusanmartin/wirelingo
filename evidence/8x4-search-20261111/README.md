# 8x4 settings searches (2026-11-11)

The small records of the place-and-route settings searches for the 8x4
release netlist (backlog item AQ), one folder per harden. The table and what
it says are in [docs/physical.md](../../docs/physical.md#8x4-settings-searches-2026-11-11).
Evidence level: harden (LibreLane 3.1.0.dev3 in its docker image, on the
compute runner), plus static timing of the finished runs. No gate-level or
silicon result is here; the release receipts are separate.

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
* `tools/`: the queue that ran them (`queue.sh`, one harden at a time) and
  the scripts that read the results (`table.py`, `mdtable.py`) and pruned
  run directories to the best two (`prune.py`).

On the compute runner, `~/protoemu-flow/aq-search/results/<run>/` also holds
each run's `metrics.json`, `resolved.json`, the full `exp.log` (gzipped), the
slow corner's max report head and the fast corner's min report head; the
best two run worktrees are kept in `~/protoemu-flow/aq-search/runs/`.

Redactions: the runner's home directory is written `~` (in `sta48.out`).
Searched for tailnet hostnames and addresses, LAN addresses, email
addresses, the build machine's provider hostname and credentials: none.
