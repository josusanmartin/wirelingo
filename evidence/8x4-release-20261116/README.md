# The 3-core 8x4 release: the release run of the shipping commit, and GitHub's builds (2026-11-15/16)

Records for the release of the 3-core 8x4 design described in
[docs/release-evidence.md](../../docs/release-evidence.md#the-3-core-8x4-release-candidate).
The shipping commit `S` is `2c97c15b5ab47d226798ed51d3d88f09eb548b21`
(tree `b4b79c79c94a70f0981f16f18855023218bcc62d`). This folder has two parts:

* `release-run/`: the release run, a clean harden of `S` on the compute
  runner (runner1) followed by `flow/release_evidence.py` and the
  equivalence check, the procedure of
  [docs/release-evidence.md](../../docs/release-evidence.md#the-3-core-8x4-release-candidate).
  It is the record that releases `S`: `RELEASE PASSED (SDF profile v2, slow target 48 MHz)`;
* `github/`: what GitHub Actions did with the pushed commits `a84401f` and
  `S`, below the release run's section.

The release run's receipts are not in `S` itself: `flow/release_check.py`
refuses a run whose tree differs from the shipping tree, so they follow `S`
in commits that touch only `evidence/` and `docs/` (README.md too, from
2026-11-16; "What may follow `S`" in the same doc).

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
  These are the netlist and GDS of the candidate's four earlier hardens
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
  git; it stays on runner1, to be attached as a release asset.

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
in the public repository `josusanmartin/wirelingo`, branch `ci-8x4-test`, and
read from the public run logs and the API with read-only `gh api` calls. The
jobs are a harden with the flow's own DRC, LVS and antenna checks, RTL
simulation (cocotb on Icarus), functional gate-level simulation of the
netlist GitHub built (no SDF delays), Tiny Tapeout's precheck, the cycle
model (pytest), formal properties (SymbiYosys) and the mutation check.
Nothing here is silicon, and nothing here ran on an FPGA. Jobs that had not
finished when `github/runs.txt` was read are listed there as unfinished and
have no extract.

| File | What it holds |
|---|---|
| `github/runs.txt` | the six runs of the two pushes (workflows `gds`, `test`, `docs` at `a84401f` and at `2c97c15`), each job's result, start, end and duration, as the API gave them at the time the file states |
| `github/a84401f-gds.txt` | the `gds` job at `a84401f`: its steps and times, the preset check, the tool versions it used, the late-clock count, detailed routing's start and end, the flow's signoff summary, and the hash comparison's printed lines |
| `github/a84401f-test.txt` | the `test` job at `a84401f`: the model suite's total, the Tcl version, the 31 skips by reason, each cocotb suite's total |
| `github/a84401f-mutants.txt` | the `mutants` job at `a84401f`: the one mutant that survived, the total, the exit |
| `github/a84401f-formal.txt` | the `formal` job at `a84401f`: the 14 tasks' verdicts |
| `github/a84401f-gl_test.txt` | the `gl_test` job at `a84401f`: its steps and times, the netlist it tests, the suite's total |
| `github/a84401f-viewer.txt` | the `viewer` job at `a84401f`: the GitHub Pages deployment |
| `github/2c97c15-formal.txt` | the `formal` job at `2c97c15`: the 14 tasks' verdicts |
| `github/2c97c15-mutants.txt` | the `mutants` job at `2c97c15`: the command, the total (none survived), and the line of the mutant that survived at `a84401f` |
| `github/earlier-runs.txt` | the repository's six runs before this branch's (all on `main`), with their mutants totals |
### The two pushes

`a84401f` is the commit the independent verifier passed before `S`. `S` is
four commits later (`git log a84401f..2c97c15`): one redacts verifier
records, two edit docs and the evidence index, and one, `e610fcb`, fixes a
test (below). The two have the same build inputs:
`flow/build_manifest.py compare` on a checkout of each prints `SAME BUILD`
(run on 2026-11-16 for this record). So GitHub's build of `a84401f` is a
build of the release's inputs.

| Commit | Pushed (the runs' creation time, UTC) | Runs: `gds`, `test`, `docs` |
|---|---|---|
| `a84401f` | 2026-11-15 19:32 | `43753001925`, `43753002010`, `43753001926` |
| `2c97c15` (`S`) | 2026-11-16 01:40 | `43787610261`, `43787610305`, `43787610191` |

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
  measurement, on one runner; the time on another day is not known.
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
* **`precheck`:** still running when `github/runs.txt` was last read
  (started at 00:46 UTC on 2026-11-16; at the time that file states, in its
  step "Run Tiny Tapeout Precheck"). Its result is not recorded here. The
  release run's own precheck of the same GDS passed (`release-run/`).

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

As `github/runs.txt` gives them (the time it states):

* **`mutants` succeeded: `367 of 367 mutants killed`**, none survived, and
  `flow-sta-inputs-not-compared` is killed by
  `test_run_changed_after_the_sta_receipt_cannot_release[odb]`
  (`github/2c97c15-mutants.txt`). So GitHub's run of the fixed test agrees
  with the verifier's run on runner1.
* `docs` succeeded, and `formal` succeeded with its 14 tasks each
  `DONE (PASS, rc=0)` (`github/2c97c15-formal.txt`).
* **Not finished then:** the `test` job, and the `gds` job ("Build GDS",
  started at 01:40 UTC). `precheck`, `gl_test` and `viewer` start only after
  `gds`. GitHub's limit for a job is 6 h, and the `gds` job of `a84401f` took
  5 h 13 min, so this one may need a re-run if it reaches the limit; its
  build inputs are `a84401f`'s. Results that finish later are not recorded
  here.

### Redactions in `github/`

None was needed. The extracts were chosen to hold no path under the hosted
runner's home directory (the logs have many; none is quoted), and the files
were searched for tailnet names and addresses, private LAN addresses, email
addresses, host names and credentials: none was found. The Pages site's
address is not quoted. Log lines are verbatim apart from trailing spaces,
which were removed.
