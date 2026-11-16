# The 3-core 8x4 release: GitHub's builds of the shipping commit and of the commit before it (2026-11-15/16)

Records for the release of the 3-core 8x4 design described in
[docs/release-evidence.md](../../docs/release-evidence.md#the-3-core-8x4-release-candidate).
The shipping commit `S` is `2c97c15`. This folder has two parts:

* `github/`: what GitHub Actions did with the pushed commits, below;
* the release run's own receipts (the clean harden of `S` on the compute
  runner and `flow/release_evidence.py`'s archive summary, the non-latch STA
  receipt, the equivalence output and the hashes). **Not here yet:** that run
  was in progress when this was written, and its records are added in a
  follow-up commit. Nothing below is a result of the release run.

Evidence level of `github/`: run on GitHub's hosted runners (`ubuntu-24.04`),
in the public repository `josusanmartin/wirelingo`, branch `ci-8x4-test`, and
read from the public run logs and the API with read-only `gh api` calls. The
jobs are a harden with the flow's own DRC, LVS and antenna checks, RTL
simulation (cocotb on Icarus), functional gate-level simulation of the
netlist GitHub built (no SDF delays), the cycle model (pytest), formal
properties (SymbiYosys) and the mutation check. Nothing here is silicon, and
nothing here ran on an FPGA. The precheck at `a84401f` had not finished when
this was written.

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
| `github/earlier-runs.txt` | the repository's six runs before this branch's (all on `main`), with their mutants totals |

Each extract gives the job id and, for every quoted line, its line number in
that job's log (`gh api repos/josusanmartin/wirelingo/actions/jobs/<job>/logs`),
so a reader can fetch the log and compare. GitHub keeps a job's log for a
limited time.

## The two pushes

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

## What the runs of `a84401f` show

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
* **`precheck`:** running when this was written (started at 00:46 UTC on
  2026-11-16).

## The mutant that survived on GitHub

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

## The runs of `2c97c15` (`S`)

Recorded when this was written (2026-11-16 02:26 UTC, `github/runs.txt`):
`docs` succeeded, and `formal` succeeded with its 14 tasks each
`DONE (PASS, rc=0)` (`github/2c97c15-formal.txt`). The `gds`, `test` and
`mutants` jobs were still running, so GitHub's result for the fixed test is
not known yet; `precheck`, `gl_test` and `viewer` start only after `gds`.
Their results are added in a follow-up commit.

## Redactions

None was needed. The extracts were chosen to hold no path under the hosted
runner's home directory (the logs have many; none is quoted), and the files
were searched for tailnet names and addresses, private LAN addresses, email
addresses, host names and credentials: none was found. The Pages site's
address is not quoted. Log lines are verbatim apart from trailing spaces,
which were removed.
