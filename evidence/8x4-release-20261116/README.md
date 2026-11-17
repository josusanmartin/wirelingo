# The 3-core 8x4 release: the release run of the shipping commit, and GitHub's builds (2026-11-15 to 17)

Records for the release of the 3-core 8x4 design described in
[docs/release-evidence.md](../../docs/release-evidence.md#the-3-core-8x4-release-candidate).
The shipping commit `S` is `2c97c15b5ab47d226798ed51d3d88f09eb548b21`
(tree `b4b79c79c94a70f0981f16f18855023218bcc62d`). This folder has two parts:

* `release-run/`: the release run, a clean harden of `S` on the compute
  runner (runner1) followed by `flow/release_evidence.py` and the
  equivalence check, the procedure of
  [docs/release-evidence.md](../../docs/release-evidence.md#the-3-core-8x4-release-candidate).
  It is the record that releases `S`: `RELEASE PASSED (SDF profile v2, slow target 48 MHz)`;
* `github/`: what GitHub Actions did with the pushed commits `a84401f`,
  `S`, `bc8882b` and `00cd1c6`, below the release run's section.

The release run's receipts are not in `S` itself: `flow/release_check.py`
refuses a run whose tree differs from the shipping tree, so they follow `S`
in commits that touch only `evidence/` and `docs/` (from 2026-11-16 also
README.md, `.github/workflows/gds.yaml` and its test file; "What may follow
`S`" in the same doc).

## The release run (`release-run/`)

Evidence levels: harden with LibreLane's DRC, LVS and antenna checks and
static timing (STA, OpenSTA); gate-level simulation of the final netlist,
functional and with SDF delays (cocotb on Icarus); Tiny Tapeout's precheck of
the GDS; formal equivalence of the RTL and the final netlist under the
checker's model (`formal/equiv/`, [docs/equivalence.md](../../docs/equivalence.md)).
None of it is silicon: the chip has not been fabricated. Nothing here ran on
an FPGA.

The run, all on runner1, all times UTC:

* **The harden,** `aqp-rel-2c97c15`: a clean clone of `S` (`rel-2c97c15`),
  `flow/harden.sh aqp-rel-2c97c15` with no options (the submission preset),
  at nice 19, with `PYTHONPATH`, `LIBRELANE_IMAGE_OVERRIDE`, `STOP` and
  `FIFO_LOG2` unset. It ran from 2026-11-15 23:20:23 to 2026-11-16 01:42:23,
  142 minutes, exit 0 (`queue.txt`, `time.txt`).
* **The receipts:** `flow/release_evidence.py` from a second clean clone of
  `S` (`val-2c97c15`, the validation root) with a third (`ship-2c97c15`) as
  the shipping root: `--slow-mhz 48 --jobs 4 --require-nonlatch-floor 0.3
  --expect …/evidence/8x4-release-expected.json`. It ran from 01:49:11 to
  03:19:30, ten jobs, at most four simulators at a time (`release-evidence.log`,
  `verdict.json`).
* **The equivalence check:** `formal/equiv/equiv_check.sh -j 4 --mutants`
  on the run, started at 01:49 beside the receipts, exit 0 (`equiv.out`,
  `equiv.rc`).

| File | What it holds |
|---|---|
| `release-run/identity.txt` | `S`'s full commit and tree ids; the final netlist's sha256, the GDS sha256 with its BGNLIB/BGNSTR dates zeroed and the raw one; the late-clock count; the release check's identity lines, each a match with [8x4-release-expected.json](../8x4-release-expected.json) |
| `release-run/archive-summary.txt` | the evidence archive: its name, sha256, size, payload files and verified receipts; the verdict; each of the ten jobs with its exit status, time, receipt path and receipt sha256, and the test cases of each GL and SDF receipt |
| `release-run/spec.txt`, `queue.txt`, `time.txt` | the harden's queue entry, its start, end and exit lines from the queue log, and its exit status and minutes |
| `release-run/harden.out` | `flow/harden.sh`'s clean-tree verdicts: `CLEAN (commit 2c97c15b5ab4, tree b4b79c79c94a)` at the start and again after config generation |
| `release-run/result.txt` | `flow/harden.sh`'s summary: flow exit status, the commit, the preset, the clean-tree verdict, HPWL, the minimum signoff (`flow/signoff.py`: DRC, LVS, antenna, utilization, setup and hold per corner at the 20 ns run constraint, the netlist and raw GDS sha256 prefixes, `SIGNOFF PASSED`) and the slow-corner extras |
| `release-run/step-times.txt` | each LibreLane step's time; 8515 s of steps in all |
| `release-run/sta48.out` | the harden's own fresh STA at 20.832 ns (48 MHz), three corners |
| `release-run/release-evidence.log` | `flow/release_evidence.py`'s log: each job's command, start and end, and the release check's full output. The run's console output (`rcpt-aqp-rel-2c97c15.out`) is byte-identical to it and is not copied twice |
| `release-run/verdict.json` | its verdict file: the candidate's file hashes, the jobs, the receipts, the release check's command line, the verdict |
| `release-run/release-check.log` | `flow/release_check.py`'s output alone: minimum signoff, the release criteria (non-latch floor, fresh slow STA), the clock and reset checks, the SRAM power stripes and pins, the shipping build and clean tree, the release identity, the receipts verified, and the verdict |
| `release-run/job-logs/` | the ten jobs' own logs: `gl`, `sdf_auto_slow`, `sdf_auto_typ`, `sdf_auto_fast`, `sdf_standard`, `sdf_v2`, `sta_slow`, `nonlatch`, `checks`, `precheck` |
| `release-run/gl-sdf-counts.txt` | the test cases in each JUnit file of the GL and SDF receipts (cases, failed, skipped, passed), from [tools/junit_counts.py](../8x4-release-candidate-20261115/tools/junit_counts.py) |
| `release-run/nonlatch/` | the non-latch STA receipt (`receipt.json`), its three per-corner reports and the Tcl script it ran (`flow/sta_nonlatch.py` at 20.832 ns) |
| `release-run/equiv.out`, `equiv.rc` | `formal/equiv/equiv_check.sh --mutants`'s output and exit status |

What the records show:

* **The release check passed.** `RELEASE PASSED (SDF profile v2, slow target 48 MHz)`
  at 2026-11-16T03:19:30Z (`release-evidence.log`; `release-check.log`), with
  the two options the release requires, `--require-nonlatch-floor 0.3` and
  `--expect`, and no waiver. All ten jobs exited 0.
* **Clean tree and shipping build.** The run hardened `S`'s tree
  (`b4b79c79…`, equal to the shipping commit's), with tt-support-tools
  `d66cf179…`, the LibreLane image `sha256:d109140b…` and the PDK `2bbec755…`,
  each as pinned; the generated configs regenerated from the shipping tree
  equal the record and the files LibreLane read; the run's build inputs equal
  the shipping clone's; and `gen_config.py --preset submission --check`
  passed (`release-check.log`, "== shipping build").
* **Release identity.** Netlist `453d0af7…`, dates-zeroed GDS `357ea15b…`
  and the late clock, 64 registers on 23 branches, each equal to
  [8x4-release-expected.json](../8x4-release-expected.json) (`identity.txt`).
  These are the netlist and the dates-zeroed GDS of the candidate's earlier
  hardens
  ([8x4-release-candidate-20261115/](../8x4-release-candidate-20261115/README.md)),
  so the numbers in docs/physical.md are the released netlist's.
* **Signoff** (`result.txt`, `release-check.log`): routing DRC 0, KLayout
  DRC 0, LVS errors 0, antenna violations 0; no slew or capacitance
  violation; `SIGNOFF PASSED`. At the run's 20 ns constraint: setup WS 0 at
  typ and fast and −0.4016 ns at slow with 13 violators (latches included),
  so 50 MHz is not claimed; hold +0.21215 / +0.39490 / +0.10504 ns typ / slow / fast.
* **48 MHz** (20.832 ns, the release target): the fresh slow STA has setup
  WS 0 (latches included) and 0 violators at each corner (`job-logs/sta_slow.log`);
  the worst non-latch setup slack is +0.4304 ns at `_53246_/D` (slow),
  +3.3180 ns and +3.9191 ns at `uo_out[4]` (typ, fast), and the slow figure
  meets the 0.3 ns floor (`nonlatch/`, `release-check.log`).
* **Clock and reset checks:** all 3885 register clock pins clocked and
  pulse-width checked at each corner; worst min pulse width, recovery and
  removal slacks positive at each corner (`job-logs/checks.log`,
  `release-check.log`).
* **Gate level** (`archive-summary.txt`, `gl-sdf-counts.txt`): functional
  GL `test` 30/30, `test_pio` 2/2, `test_eth_service` 2/2; SDF `standard`
  5/5 at typ 20 ns, slow 20.832 ns and fast 20 ns, with the slow 10 ns
  control failing as required (1 failed); SDF `v2` 3/3 at the same three and
  USB full speed 1/1 at 20.834 ns at each corner; autonomous USB (low and full
  speed) 2/2 at 20.834 ns at each corner. The SDF runs are directed subsets
  (the other cases of each JUnit file are skipped by design), and Icarus has
  no setup or hold timing checks, so STA remains the timing evidence.
* **Precheck:** the precheck receipt verified (`release-check.log`).
* **Equivalence** (`equiv.out`): `VERDICT EQUIVALENT`; ABC `&cec`
  equivalent; `equiv_simple` 10754/10754 proven; all 9 planted netlist
  mutants caught; exit 0. Its times are runner1's clock, one hour ahead of
  UTC (it started at 01:49 UTC).
* **The archive** (`archive-summary.txt`):
  `wirelingo-release-evidence-2c97c15-aqp-rel-2c97c15.tar.gz`, sha256
  `1ff19b7d7d673aeb37c88d941d13ae01750274f2cc838fbdb9283ef2e8bedb8b`,
  72,314,042 bytes, 568 payload files, 10 verified receipts. It is not in
  git; it stays on runner1 and is not yet published: the repository has no GitHub release yet (`gh api repos/josusanmartin/wirelingo/releases`, 2026-11-17).

Two lines that read otherwise: `result.txt` ends with `flow/harden.sh`'s
own preview of the release check, which says there is no clock and reset
receipt; the release check proper had one (`release-check.log`). And in
`release-check.log`, "shipping build: … not compared (…, shipping: None)"
lines are notes, not failures: the shipping clone was not built, so it records
no tools or outputs to compare (`flow/build_manifest.py`'s comparison). The
build-input comparison that follows, "the run's build inputs equal …
ship-2c97c15's", is the one that binds.

Redactions in `release-run/`: the runner's home directory in paths was
written as `~` (161 occurrences: 71 in `release-evidence.log`, 32 in
`verdict.json`, 13 in `release-check.log`, 9 in `nonlatch/receipt.json`,
3 in each of the three `nonlatch/*.rpt`, 5 in `job-logs/gl.log`, 2 in each
of the other nine job logs, 2 in `sta48.out`, 1 in `harden.out` and 1 in
`equiv.out`). So `nonlatch/receipt.json` is not byte-identical to the
receipt: its sha256 is no longer the `5ade898f…` that `verdict.json` and the
archive record; the archive holds the original. The files were searched for
tailnet names and addresses, private LAN addresses, email addresses, other
host names and credentials; none was found. `archive-summary.txt` and `identity.txt`, written for this folder, give
paths the same way. Nothing else was changed.

## GitHub (`github/`)

Evidence level of `github/`: run on GitHub's hosted runners (`ubuntu-24.04`),
in the public repository `josusanmartin/wirelingo`, on the test branch
`ci-8x4-test` and on `main`, and read from the public run logs and the API
with read-only `gh api` calls. The jobs are a harden with the flow's own DRC,
LVS and antenna checks, RTL simulation (cocotb on Icarus), functional
gate-level simulation of the netlist GitHub built (no SDF delays), Tiny
Tapeout's precheck (KLayout DRC and the tools' own checks of the GDS), the
cycle model (pytest), formal properties (SymbiYosys) and the mutation check.
Nothing here is silicon, and nothing here ran on an FPGA. Jobs that had not
finished when `github/runs.txt` was read are listed there as unfinished and
have no extract.

| File | What it holds |
|---|---|
| `github/runs.txt` | the fifteen runs of the five pushes below (workflows `gds`, `test`, `docs`), every attempt of each job with its result, start, end and duration, as the API gave them at the time the file states |
| `github/<commit>-gds.txt` (`a84401f`, `2c97c15`) and `github/bc8882b-gds-attempt3.txt` | a one-job `gds` job that succeeded: its steps and times, the preset check, the tool versions it used, the late-clock count, detailed routing's start and end, the flow's signoff summary, and the hash comparison's printed lines |
| `github/bc8882b-gds-attempt1.txt`, `-attempt2.txt` | the two `gds` jobs at `bc8882b` that GitHub cancelled at its 6 h limit: their steps and times, the preset check, the tools, the late-clock count, detailed routing's start and end, the last LibreLane step started (and, in attempt 2, the flow's end and the artifacts uploaded), and the cancellation |
| `github/00cd1c6-gds_place.txt`, `-gds_route.txt`, `-gds.txt`, and the same three `github/main-00cd1c6-*` | the three build jobs of the split workflow at `00cd1c6`, on the test branch and on `main`: for each its steps and times, the preset check, the run directory it received and its config hashes checked (`gds_route`, `gds`), the tools, the first and last LibreLane steps it ran and the flow's end, the boundary check, the artifacts it uploaded; the late-clock count in `gds_place`; the signoff summary and the hash comparison in `gds` |
| `github/<commit>-precheck.txt` | the `precheck` job (`a84401f`, `2c97c15`, `bc8882b`, `00cd1c6`): its steps and times, each KLayout check it ran with the error totals it printed, the start of the main DRC run's connectivity setup and of its rules, and the verdict |
| `github/<commit>-gl_test.txt` | the `gl_test` job (the same four, and `main-00cd1c6-gl_test.txt`): its steps and times, the netlist it tests, the suite's total |
| `github/<commit>-viewer.txt` | the `viewer` job (the same four, and `main-00cd1c6-viewer.txt`): the GitHub Pages deployment |
| `github/<commit>-test.txt` | the `test` job (`a84401f`, `2c97c15`, `bc8882b`, `00cd1c6`, and `main-00cd1c6-test.txt`): the model suite's total, the Tcl version, the 31 skips by reason, each cocotb suite's total |
| `github/<commit>-mutants.txt` | the `mutants` job (the same four, and `main-00cd1c6-mutants.txt`): at `a84401f` the one mutant that survived, the total and the exit; at the others the command, the total (none survived) and the line of the mutant that survived at `a84401f` |
| `github/<commit>-formal.txt` | the `formal` job (the same four, and `main-00cd1c6-formal.txt` for `main`'s run at `00cd1c6`): the 14 tasks' verdicts |
| `github/earlier-runs.txt` | the repository's six runs before the test branch's (all on `main`), with their mutants totals |
| `github/tt-submission-hashes.txt` | the sha256 of the `.oas`, `.lef`, netlist and GDS (raw and dates-zeroed) in six `tt_submission` artifacts, downloaded on 2026-11-17 (below) |

### The pushes

`a84401f` is the commit the independent verifier passed before `S`. `S` is
four commits later (`git log a84401f..2c97c15`): one redacts verifier
records, two edit docs and the evidence index, and one, `e610fcb`, fixes a
test (below). `bc8882b` is `S` plus the release run's evidence commits
(`evidence/`, `docs/`, README.md). `00cd1c6` is `bc8882b` plus one commit
that splits the `gds` workflow into three jobs (`.github/workflows/gds.yaml`
and its test file, docs and evidence). All four have the same build inputs:
`flow/build_manifest.py compare` prints `SAME BUILD` for `a84401f` against
`S` (run on 2026-11-16) and for `S` against `bc8882b` and against `00cd1c6`
(run on 2026-11-17 for this record, on exported trees of each commit). So
each GitHub build below is a build of the release's inputs.

| Commit | Branch | Pushed (the runs' creation time, UTC) | Runs: `gds`, `test`, `docs` |
|---|---|---|---|
| `a84401f` | `ci-8x4-test` | 2026-11-15 19:32 | `43753001925`, `43753002010`, `43753001926` |
| `2c97c15` (`S`) | `ci-8x4-test` | 2026-11-16 01:40 | `43787610261`, `43787610305`, `43787610191` |
| `bc8882b` | `main` | 2026-11-16 05:35 | `43811275309`, `43811275254`, `43811275355` |
| `00cd1c6` | `ci-8x4-test` | 2026-11-16 21:06 | `43956712707`, `43956712900`, `43956712530` |
| `00cd1c6` | `main` | 2026-11-17 06:56 | `44034950929`, `44034950943`, `44034950997` |

Before these, GitHub had run the repository's workflows on `main` only (six
runs, of `aa958a7` and `d2ed344`: the 6x4 design, a list of 245 mutants;
`github/earlier-runs.txt`).

### What the runs of `a84401f` show

* **The build reproduces the release identity.** The `gds` job succeeded.
  After "Build GDS", its step "Compare the build with the release's expected
  hashes" (`flow/release_expected.py . evidence/8x4-release-expected.json`)
  printed (`github/a84401f-gds.txt`, log lines 256423-256427):

  ```
  netlist_sha256 expected 453d0af7bea77dd59421b9391c737312aee8c10b9eb024d213f65e1e8e468b08
  netlist_sha256 actual   453d0af7bea77dd59421b9391c737312aee8c10b9eb024d213f65e1e8e468b08 (match)
  gds_normalized_sha256 expected 357ea15b4546ef28c7a9b35d62377c2702262da9396c2711c8654c4dea8c470a
  gds_normalized_sha256 actual   357ea15b4546ef28c7a9b35d62377c2702262da9396c2711c8654c4dea8c470a (match)
  RELEASE IDENTITY MATCHES evidence/8x4-release-expected.json
  ```

  So the netlist and the GDS with its dates zeroed that GitHub built are the
  ones the compute runner's clean reproductions gave
  ([8x4-release-candidate-20261115/](../8x4-release-candidate-20261115/README.md)).
  The step compares those two hashes. It runs without `--late-clock`, so it
  does not compare the late-clock count; the build's log has the line the
  reference runs have, `LATE_CLOCK_COMPLETE 64 registers 23 branches`.
* **The time.** The job ran from 19:32:36 to 00:46:09 UTC: 5 h 13 min 33 s,
  of which "Build GDS" took 5 h 13 min 22 s and detailed routing 3 h 45 min
  52 s (by the log's timestamps). GitHub's limit for a job on a hosted runner
  is 6 h, so this run finished with about 46 minutes to spare. That is one
  measurement, on one runner; the later one-job runs below took from 4 h 22
  min to more than 6 h.
* **The tools.** tt-support-tools at `d66cf17`, `librelane==3.1.0.dev3`, and
  the LibreLane image pulled by its version tag, which resolved to digest
  `sha256:d109140b…`, the digest the receipted runs on the compute runner
  used ([docs/release-evidence.md](../../docs/release-evidence.md#the-3-core-8x4-release-candidate)).
  The workflow does not pin that digest; this records what the tag gave on
  2026-11-15.
* **The tests.** The `test` job succeeded in 1 h 26 min 24 s. Its model
  suite printed `3714 passed, 31 skipped` with `Tcl 8.6.14`. The 31 skips, by
  reason: 28 need `sigrok-cli`, 1 needs a scapy environment, 1 needs
  MicroPython, and 1 needs a local archive that is not in git (the USB
  evidence archive, `WIRELINGO_USB_ARCHIVE`). No Tcl-backed test skipped. The
  cocotb suites' totals are in `github/a84401f-test.txt`: 18 summary lines
  with no failure, and two skips the workflow expects
  (`bench_sigrok_available`, and `pps_one_second`, which runs only with
  `PPS_LONG=1`).
* **Formal and docs.** `formal` succeeded: 14 tasks, each `DONE (PASS, rc=0)`.
  `docs` succeeded.
* **The viewer.** `viewer` succeeded. It deployed the layout viewer to the
  repository's GitHub Pages site from this branch's build
  (`github/a84401f-viewer.txt`), so the site then showed the build of
  `a84401f`, a commit that is not on `main`.
* **Gate level.** `gl_test` succeeded in 1 h 14 min 15 s: the action's
  `GATES=yes make`, the top-level cocotb module `test` on the netlist of the
  `gds` job's artifact, functional (no SDF delays), with the Icarus Verilog
  13.0 package the action downloads: `TESTS=30 PASS=30 FAIL=0 SKIP=0`
  (`github/a84401f-gl_test.txt`).
* **`precheck`** succeeded: `Precheck passed`, 00:46:12 to 05:25:41 UTC on
  2026-11-16 (4 h 39 min 29 s), of which its step "Run Tiny Tapeout
  Precheck" took 4 h 39 min 20 s (`github/a84401f-precheck.txt`; the
  precheck's time is discussed below). An earlier version of this folder
  recorded it as still running; it finished after that read.

### The mutant that survived on GitHub

**The `mutants` job at `a84401f` failed: `366 of 367 mutants killed`, and
`flow-sta-inputs-not-compared SURVIVED`** (`github/a84401f-mutants.txt`). The
`test` run is therefore red at `a84401f`.

This is a real finding, made by GitHub's run of the pushed branch: the
tests did not kill one of the planted faults.

* **The mutant** makes `flow/release_receipts.py`'s comparison of a run's STA
  inputs with the STA receipt's always pass.
* **The cause** was in the test, not in the gate. The test accepted any
  refusal containing "STA inputs differ from the receipt's". Another receipt,
  the checks receipt, binds the same inputs and prints a refusal with that
  substring, so with the STA receipt's comparison removed the checks receipt's
  refusal satisfied the test.
* **Reproduced** in a clean clone on runner1 (`e610fcb`'s message), and again
  by the independent verifier, which ran `a84401f`'s test with the mutation
  applied and saw it pass (its report on item 106; not in this repository).
* **The fix** is commit `e610fcb`, in the test only: the refusal must be the
  STA receipt's own, and each case then remakes the checks receipt on the
  changed run and requires the release to fail on the STA receipt alone. No
  check and no mutant entry changed; the list is still 367.
* **After the fix** the independent verifier ran the whole list at `S` in a
  clean clone on runner1: 367 of 367 killed (the same report). That run was
  on runner1, not on GitHub.

That is why the shipping commit is `2c97c15` and not `a84401f`.

### The runs of `2c97c15` (`S`)

All three runs succeeded (`github/runs.txt`):

* **The build reproduces the release identity.** The `gds` job (job
  `133038662531`) succeeded, 01:40:24 to 06:02:22 UTC on 2026-11-16: 4 h 21
  min 58 s, of which detailed routing took 3 h 05 min 25 s. Its hash step
  printed both pairs as `(match)`, netlist `453d0af7…` and dates-zeroed GDS
  `357ea15b…`, then `RELEASE IDENTITY MATCHES evidence/8x4-release-expected.json`
  (`github/2c97c15-gds.txt`, log lines 256425-256429). The image tag resolved
  to `sha256:d109140b…` again, and the late-clock line is there.
* **`precheck`** succeeded: `Precheck passed`, 06:02:25 to 10:30:56 UTC
  (job `133122030720`; its step "Run Tiny Tapeout Precheck" 4 h 28 min 24 s;
  `github/2c97c15-precheck.txt`).
* **`gl_test`** succeeded in 1 h 09 min 40 s with `TESTS=30 PASS=30 FAIL=0
  SKIP=0`, and `viewer` deployed the Pages site from this build.
* **`mutants` succeeded: `367 of 367 mutants killed`**, none survived, and
  `flow-sta-inputs-not-compared` is killed by
  `test_run_changed_after_the_sta_receipt_cannot_release[odb]`
  (`github/2c97c15-mutants.txt`). So GitHub's run of the fixed test agrees
  with the verifier's run on runner1.
* **`test`** succeeded in 2 h 21 min 34 s (`3714 passed, 31 skipped`, the
  same 31 skips as at `a84401f`; 18 cocotb summary lines with no failure);
  `formal` succeeded with its 14 tasks each `DONE (PASS, rc=0)`; `docs`
  succeeded.

### The runs of `bc8882b` on `main`

`main` was fast-forwarded from the 6x4 design to `bc8882b` on 2026-11-16.

* **The `gds` job ran three times** (run `43811275309`; GitHub's "re-run
  failed jobs" makes a new attempt of the run):
  - **Attempt 1** (job `133113260538`), 05:35:56 to 11:36:13 UTC, was
    cancelled at GitHub's 6 h job limit while LibreLane was still running:
    detailed routing took 4 h 40 min 34 s, and the last step started was
    `KLayout.DRC` at 11:27:38 (`github/bc8882b-gds-attempt1.txt`).
  - **Attempt 2** (job `133273103876`), 11:40:00 to 17:40:15, was cancelled
    at the limit too. Detailed routing took 4 h 27 min 01 s; LibreLane's
    flow completed at 17:30:11, 5 h 49 min 18 s after the step "Make GDS
    with LibreLane" began (11:40:53); the action then uploaded `GDS_logs` and
    `tt_submission` (17:31:40 and 17:31:46), and the job was cancelled at
    17:40:13 in the action's step "Render PNG from GDS", which had run for
    8 min 27 s (`github/bc8882b-gds-attempt2.txt`).
  - In neither attempt did the hash step run, so neither build is compared
    with the release identity here, and `precheck`, `gl_test` and `viewer`
    were skipped.
  - **Attempt 3** (job `133491890099`) succeeded, 17:48:00 to 22:17:53 UTC:
    4 h 29 min 53 s, detailed routing 3 h 11 min 06 s. Its hash step printed
    both pairs as `(match)` and `RELEASE IDENTITY MATCHES
    evidence/8x4-release-expected.json`, with the same image digest
    (`github/bc8882b-gds-attempt3.txt`, log lines 256424-256428).
* **`precheck`** (attempt 3, job `133654739439`) succeeded: `Precheck
  passed`, 22:17:56 on 2026-11-16 to 03:21:50 UTC on 2026-11-17 (its step
  5 h 03 min 47 s). `gl_test` succeeded (`TESTS=30 PASS=30 FAIL=0 SKIP=0`,
  1 h 11 min 11 s) and `viewer` deployed the Pages site from `main`.
* **The `test` run** (`43811275254`) succeeded: `test` (`3714 passed,
  31 skipped`), `mutants` (`367 of 367 mutants killed`) and `formal` (14
  tasks `DONE (PASS, rc=0)`). The `docs` run (`43811275355`) succeeded.

The three attempts built the same commit on hosted runners with the same
image label (`ubuntu-24.04`); detailed routing took 4 h 40 min, 4 h 27 min
and 3 h 11 min. The two cancelled attempts are why the build was split
(next).

### The split workflow at `00cd1c6` (test branch)

`00cd1c6` runs the build as three chained jobs, each under its own 6 h limit
([docs/release-evidence.md](../../docs/release-evidence.md#the-3-core-8x4-release-candidate),
"Why GitHub builds in three jobs"; the resume was first checked on runner1,
[gds-split-20261116/](../gds-split-20261116/README.md)). Pushed to
`ci-8x4-test`, run `43956712707` succeeded:

| Job | Job id | Start and end (UTC) | Duration | What the extract shows |
|---|---|---|---|---|
| `gds_place` | `133613065829` | 2026-11-16 21:06:52 to 21:51:30 | 44 min 38 s | LibreLane from `Verilator.Lint` to `OpenROAD.STAMidPNR-3`, the late-clock line `LATE_CLOCK_COMPLETE 64 registers 23 branches`, the boundary check, `gds_run_placed` uploaded |
| `gds_route` | `133639500221` | 21:51:33 to 2026-11-17 00:43:09 | 2 h 51 min 36 s | `gds_run_placed` received, both config hashes `OK`, `runs/wokwi kept`, `Project.DetailedRouting` alone, 2 h 48 min 44 s from its start to the flow's end, `gds_run_routed` uploaded |
| `gds` | `133727433238` | 2026-11-17 00:43:12 to 01:19:58 | 36 min 46 s | `gds_run_routed` received, both config hashes `OK`, `runs/wokwi kept`, LibreLane from `Odb.RemoveRoutingObstructions` to `Misc.ReportManufacturability`, the signoff summary (antenna, LVS, DRC passed), `GDS_logs`, `tt_submission` and `gds_render` uploaded, then the hash step |

* **The hash step** of the `gds` job printed both pairs as `(match)`,
  netlist `453d0af7…` and dates-zeroed GDS `357ea15b…`, and `RELEASE
  IDENTITY MATCHES evidence/8x4-release-expected.json`
  (`github/00cd1c6-gds.txt`, log lines 16435-16439). So the split workflow
  built the same netlist and the same dates-zeroed GDS as the one-job
  workflow and the release run.
* **`precheck`** (job `133743926009`) succeeded: `Precheck passed`, 01:20:00
  to 06:50:16 UTC on 2026-11-17 (its step 5 h 30 min 10 s). `gl_test`
  succeeded (`TESTS=30 PASS=30 FAIL=0 SKIP=0`, 01:20:00 to 02:47:13) and
  `viewer` deployed the Pages site from this test-branch build.
* **The `test` run** (`43956712900`) succeeded: `test` (`3719 passed,
  31 skipped`: five more passes than at `bc8882b`; the commit adds workflow
  tests), `mutants` (`367 of 367 mutants killed`) and `formal` (14 tasks
  `DONE (PASS, rc=0)`). The `docs` run (`43956712530`) succeeded.

### The runs of `00cd1c6` on `main`

`main` was fast-forwarded to `00cd1c6` on 2026-11-17. As `github/runs.txt`
gives them (the time it states):

| Job | Job id | Start and end (UTC) | Duration | What the extract shows |
|---|---|---|---|---|
| `gds_place` | `133875476896` | 06:56:52 to 07:42:01 | 45 min 09 s | LibreLane from `Verilator.Lint` to `OpenROAD.STAMidPNR-3`, the late-clock line, `gds_run_placed` uploaded |
| `gds_route` | `133894334152` | 07:42:03 to 12:23:17 | 4 h 41 min 14 s | `Project.DetailedRouting` alone, 4 h 38 min 37 s from its start to the flow's end, `gds_run_routed` uploaded |
| `gds` | `134036897492` | 12:23:20 to 13:00:01 | 36 min 41 s | LibreLane from `Odb.RemoveRoutingObstructions` on, the signoff summary, `tt_submission` uploaded, then the hash step |

* **The hash step** printed both pairs as `(match)` and `RELEASE IDENTITY
  MATCHES evidence/8x4-release-expected.json` (`github/main-00cd1c6-gds.txt`,
  log lines 16441-16445).
* **`gds_route`'s margin.** The job took 281 min against the workflow's own
  `timeout-minutes: 350` for it (`.github/workflows/gds.yaml`): 69 min to
  spare, measured once. On the test branch the same job took 172 min.
* **`viewer`** succeeded (13:00:04 to 13:00:34) and deployed the Pages site
  from `main`.
* **`gl_test`** (job `134056766024`) succeeded, 13:00:03 to 14:26:55 (1 h 26 min
  52 s): `TESTS=30 PASS=30 FAIL=0 SKIP=0` (`github/main-00cd1c6-gl_test.txt`).
* **`precheck`** (job `134056765985`), started 13:00:03, had not finished
  when `github/runs.txt` was read; its result is not recorded here.
* **The `test` run** (`44034950943`) succeeded: `test` (`3719 passed,
  31 skipped`), `mutants` (`367 of 367 mutants killed`) and `formal` (14
  tasks `DONE (PASS, rc=0)`). The `docs` run (`44034950997`) succeeded.

### The precheck's time: a measured risk

The `precheck` job is Tiny Tapeout's precheck action, unchanged by the split:
one job, one KLayout process for its main DRC run. On the four green runs
above, whose `gds` jobs each printed the release identity's two hashes as
`(match)`, its step "Run Tiny Tapeout Precheck" took:

| Run | Precheck job | The step's time | The main DRC run's connectivity setup |
|---|---|---|---|
| `a84401f` | `133022084379` | 4 h 39 min 20 s (279 min) | 00:48:34 to 05:10:41 (4 h 22 min) |
| `2c97c15` (`S`) | `133122030720` | 4 h 28 min 24 s (268 min) | 06:05:10 to 10:09:50 (4 h 05 min) |
| `bc8882b` (`main`) | `133654739439` | 5 h 03 min 47 s (304 min) | 22:20:06 to 03:06:21 (4 h 46 min) |
| `00cd1c6` (test branch) | `133743926009` | 5 h 30 min 10 s (330 min) | 01:22:44 to 06:30:25 (5 h 08 min) |

GitHub's limit for a job is 6 h (360 min). Most of the step (245 to 308
min of it) is one phase of the main KLayout DRC run, from its line "Starting IHP-SG13CMOS5L DRC
connectivity setup" to its line "Starting IHP-SG13CMOS5L DRC rules." (the
run's own timestamps; the extracts quote both lines). The slowest, the split
run's, ended 29 min 44 s before the limit (the job, 01:20:00 to 06:50:16).
The precheck has not timed out on GitHub in these runs, but the margin is
small: that is a measured risk, from four runs. If one timed out, the plan is
to re-run the failed job on the same GDS. That has not been exercised. The
basis is how run 43811275309 kept its artifacts: after its third attempt, the
`tt_submission` artifact of the cancelled second attempt (artifact
14254545353) is still listed in the run (`gh api
repos/josusanmartin/wirelingo/actions/runs/43811275309/artifacts`), so a
re-run attempt does not remove an earlier attempt's artifacts.

### What the artifacts hold beyond the hash step

The `gds` job's hash step hashes the run directory's final netlist and
dates-zeroed GDS. The `tt_submission` artifact also holds an `.oas` (written
from the GDS by tt-support-tools with KLayout) and a `.lef`, which no workflow
step checks. `github/tt-submission-hashes.txt` records, for six artifacts
(`a84401f`, `S`, `bc8882b` attempts 2 and 3, `00cd1c6` on the test branch
and on `main`), the sha256 of each zip (equal to the digest the API lists)
and of the `.oas`, `.lef`, netlist and GDS inside:

* the `.oas` (`b990a29e…`), the `.lef` (`498cd3ef…`) and the netlist
  (`453d0af7…`) are the same file in all six;
* the raw GDS differs in each, and its dates-zeroed digest is `357ea15b…` in
  all six, the expected identity.

This records the files; it does not make the hash step cover them, and which
file a Tiny Tapeout shuttle takes from the artifact is not recorded here.
The artifacts were deleted from the local scratch directory after hashing.

### The Pages site

Each green `gds` run's `viewer` job deployed the layout viewer to the
repository's GitHub Pages site, the test branch's included: `a84401f`
(00:46 UTC on 2026-11-16), `S` (06:02), `bc8882b` from `main` (22:18) and
`00cd1c6` from the test branch (01:20 on 2026-11-17), each a build with the
release identity. `main` at `00cd1c6` redeployed it from `main` at 13:00 on
2026-11-17 (`github/main-00cd1c6-viewer.txt`).

### Redactions in `github/`

The precheck extracts quote lines that name the GDS by its path under the
hosted runner's home directory; that directory is written as `~` (4 lines in
each of the four `*-precheck.txt`, as their headers say). Nothing else was
changed: no other extract quotes a path under that directory. The files were
searched for tailnet names and addresses, private LAN addresses, email
addresses, host names and credentials: none was found. The Pages site's
address is not quoted. Log lines are verbatim apart from trailing spaces,
which were removed, and the home directory above.
