# 8x4 settings searches: the 3-core candidate (2026-11-14)

The small records of the 3-core 8x4 harden that the submission preset now
reproduces (`flow/gen_config.py`'s `SUBMISSION_TUNED[("8x4", 3)]`, the tuned
set `rt10-8x4`). Evidence level: harden (LibreLane 3.1.0.dev3 in its docker
image, on the compute runner), plus static timing of the finished run. No
gate-level or silicon result is here.

* `3core/aqp-rt10/`:
  * `spec.txt`: the run's `flow/harden.sh` arguments (the commit's own core
    count, so the submission preset); `time.txt`: its exit status and minutes;
  * `config.json`: the run's generated `src/config.json`, byte for byte what
    `flow/gen_config.py --preset submission` now writes for 3 cores at 8x4;
  * `plugin.sha256`: the sha256 of the `librelane_plugin_protoemu.py` it ran;
  * `result.txt`: `flow/harden.sh`'s summary plus the per-run extras (SRAM
    address pin slews, largest stage, `flow/release_check.py`'s timing lines);
  * `sta-summary.rpt`: the final STA step's summary table, all corners;
  * `sta20-flop.txt`: worst flop/SRAM endpoint per corner at 20 ns
    (`syn/8x4-study/sta_period.sh`);
  * `sta-exact.txt`: the exact setup slack at 20.832 ns (48 MHz) and 20 ns per
    endpoint family (all endpoints, the FIFO latches, non-latch endpoints,
    SRAM `A_ADDR`, `pin_oe`), all corners (`tools/sta832.sh`). The summary's
    setup WS reads 0 because the FIFO latches borrow time and show slack 0.
* `tools/`: `sta832.sh` and `sta832.tcl`, the read-only STA that wrote the
  reports `sta-exact.txt` summarizes.
