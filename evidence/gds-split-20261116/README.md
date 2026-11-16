# Resuming the release run as the split GDS workflow does (2026-11-16)

The GDS workflow (`.github/workflows/gds.yaml`) runs the build as three jobs,
`gds_place` (LibreLane steps 1 to 50), `gds_route` (step 51, detailed routing)
and `gds` (steps 52 to 79 and the rest of the GDS action), handing the run
directory on as an artifact
([docs/release-evidence.md](../../docs/release-evidence.md#the-3-core-8x4-release-candidate),
"Why GitHub builds in three jobs"). These records check, on the compute runner
(runner1), that LibreLane resumed that way gives the release build.

Evidence level: harden (LibreLane steps rerun in the release run's image,
`ghcr.io/librelane/librelane:3.1.0.dev3`, image id `d109140b8f17`, with the
PDK at IHP-Open-PDK `2bbec755`). Not GitHub, not silicon.

## What ran

`prove.sh` (this folder, as run), at nice 19, on copies of the release run
aqp-rel-2c97c15 (the harden of the shipping commit, read-only; its records
are in [8x4-release-20261116/](../8x4-release-20261116/README.md)). For each
check the script:

1. copies the release run's worktree, its run directory included;
2. removes the step directories from the part's first step on, and `final/`
   and `pdk.json`;
3. packs `runs/wokwi` and unpacks it in place as the workflow's jobs do
   (`tar --zstd --format=posix`, then `final/` removed);
4. runs LibreLane's container command as `tt_tool.py --harden` builds it under
   CI (`--pdk-root`, `--pdk ihp-sg13cmos5l --manual-pdk --run-tag wokwi
   --force-run-dir runs/wokwi --hide-progress-bar`, the part's arguments,
   `src/config_merged.json`), with the copy mounted at the original's path, so
   that the absolute paths in the run directory's state files resolve to the
   copy. On GitHub no mount is needed: every job checks out at the same path;
5. runs `flow/release_expected.py . evidence/8x4-release-expected.json` and
   compares the step directory names, `final/metrics.csv` and the sha256 of
   every file in `final/` with the release run's.

The wrapper that adds the part's arguments to tt-support-tools' own
`--harden` is checked separately by `test/model/test_release_expected.py`
(with a stand-in for `tt_tool.py`) and was run once against tt-support-tools
at `d66cf17` with a stand-in for LibreLane: it produced the three commands
above and the tools' `commit_id.json` and `pdk.json`.

Paths in the records: the runner's home is written `~`.

## Results

* **`quick.out`, the `gds` job's part:** steps 52 to 79 resumed from step 51's
  state (`--from Odb.RemoveRoutingObstructions --with-initial-state
  runs/wokwi/51-project-detailedrouting/state_out.json`), 18:29 to 18:40 UTC:
  LibreLane exit 0, netlist `453d0af7…` and dates-zeroed GDS `357ea15b…` both
  `(match)`, `RELEASE IDENTITY MATCHES evidence/8x4-release-expected.json`.
  The step directories are the release run's (the only name in the listing
  diff is `pdk.json`, which tt-support-tools writes after LibreLane and the
  check ran without). The `final/` files differ from the release run's only
  in dates: the GDS files' BGNLIB/BGNSTR dates (the digest above zeroes
  them), the SDF `DATE` line, the SPEF `*DATE` line and the magic
  `timestamp`, each a one-line `diff`, read before the copy was deleted:

  ```
  sdf nom_slow:  (DATE "Mon Nov 16 01:32:31 2026")  ->  (DATE "Mon Nov 16 18:30:25 2026")
  sdf nom_fast:  (DATE "Mon Nov 16 01:32:31 2026")  ->  (DATE "Mon Nov 16 18:30:24 2026")
  sdf nom_typ:   (DATE "Mon Nov 16 01:32:29 2026")  ->  (DATE "Mon Nov 16 18:30:25 2026")
  spef nom:      *DATE "01:31:33 Monday November 16, 2026"  ->  *DATE "18:29:30 Monday November 16, 2026"
  mag:           timestamp 1794792830  ->  timestamp 1794853905
  ```

  and `metrics.csv`, where `route__wirelength__estimated` reads `2519140`
  instead of the one-job run's `2.51914E+6`: the same number, written after a
  round trip through a state file.
* **`full.out`, all three parts:** on a second copy with steps 50 to 79
  removed, step 50 run alone with `gds_place`'s stop (`--from
  OpenROAD.STAMidPNR-3 --to OpenROAD.STAMidPNR-3`, steps 1 to 49 kept from the
  release run: it stopped with `50-openroad-stamidpnr-3` and no step 51), then
  `gds_route`'s arguments (18:41 to 20:43 UTC, 2 h 02 min of detailed routing
  at nice 19 beside other work; it stopped with `51-project-detailedrouting`
  and no step 52), then the `gds` job's (20:43 to 20:55), each after a pack
  and unpack: LibreLane exit 0 each time, netlist `453d0af7…` and
  dates-zeroed GDS `357ea15b…` both `(match)`, `RELEASE IDENTITY MATCHES`.
  Step 51's routed database, DEF and netlist are byte-identical to the
  release run's. The step directories and the `final/` differences are as in
  `quick.out`: the same files, dates only (the diffs are in `full.out`), and
  the same `metrics.csv` line.

Not checked here: steps 1 to 49 under `gds_place`'s command (only its stop
was run), and anything on GitHub's runners. The `gds` job's hash step is the
check for those.

Both copies were deleted after these records were taken.
