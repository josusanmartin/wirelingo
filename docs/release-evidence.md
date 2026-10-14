# Release evidence

`flow/signoff.py` remains the minimum physical check. `flow/release_check.py`
checks the stronger release contract: clean setup and hold at the actual
run constraint, electrical limits, complete functional GL, timing-annotated
tests at the requested operating clocks, and precheck of the exact GDS.
Passing tests do not override a failed physical check.

## One command

On the compute runner, make a real git clone of the commit to certify (not an
rsync copy) and run the orchestrator from it:

```sh
git clone -q --no-checkout "$REPO" "$V" && git -C "$V" checkout -q --detach "$REV"
"$V"/flow/release_evidence.py "$RUN" --validation-root "$V"
# or let it clone REV itself:  flow/release_evidence.py "$RUN" --validation-commit "$REV"
```

It runs the seven receipt jobs below (functional GL; SDF `standard` and `v2`;
`test_usb_autonomous` and `test_usb_autonomous_fs` with SDF at typ, slow and
fast at 20.834 ns; precheck), each in its own fresh output directory, with
at most `--jobs` (default 6) simulator processes at once: the standard set
counts 4, v2 counts 6, every other job 1. GL starts first (about 65 minutes for fo6).
The runners' environment variables (`TESTS`, `MODULE`, `SLOW_CLK_NS`,
`NEGATIVE_CLK_NS`, `VALIDATION_ROOT`, ...) are set explicitly, never inherited.
Each receipt is taken from its own job's log and must sit in an output
directory that did not exist before the run; an older receipt is never used.
Then it runs `release_check.py` against the real `RUN` path and writes
`verdict.json`, `release-check.log`, `release-evidence.log` and `logs/<job>.log`
into the work directory (default `rcpt-<run>-<rev>-<UTC>` next to the clone).
On a pass, `flow/archive_evidence.py` re-verifies every receipt and packs
`wirelingo-release-evidence-<rev>-<run>.tar.gz` next to the work directory.
Add `--repro RUN2` (repeatable) for a reproduction run's candidate files and
`--extra FILE` for files such as a reproduction comparison. A failed job,
a missing or stale receipt, or a failed gate exits 1 and builds no archive.
The work directory and archive must not exist; nothing is overwritten.

The physical run is only read. `gl_test.sh` writes its output under the run
worktree it is given, so the orchestrator hands the runners a view: a
directory of symlinks to the run's entries with a private `test/`. It checks
that the view yields exactly the run's candidate file hashes before starting.
Precheck defaults: `--tt-root RUN/tt`, `--flow-python
~/protoemu-flow/protoemu/.venv-flow/bin/python`, `--klayout-dir
~/protoemu-flow/kbin` (the docker wrapper), `--pdk-root $PDK_ROOT` or `~/ttpdk`;
`--sim-path ~/tools/oss-cad-suite/bin` for Icarus and cocotb.

## Generate evidence by hand

Run these commands on the compute runner. `RUN` is a completed hardening
worktree with `runs/wokwi/final`, not an intermediate checkpoint. The SDF
runner uses the verification checkout's tests; its RTL and hardware profile
must match the candidate. Functional GL accepts that checkout as a second
argument too. Use it when firmware/tests advanced after the hardening run.

```sh
flow/gl_test.sh "$RUN" "$PWD"
flow/gl_sdf_test.sh "$RUN" standard
flow/gl_sdf_test.sh "$RUN" v2
PDK_ROOT="$HOME/ttpdk" .venv-flow/bin/python flow/precheck_test.py "$RUN" \
  --tt-root /path/to/tt-support-tools
```

If the runner tools are newer than the frozen shipping checkout, keep the
shipping files unchanged and select them explicitly for both simulations:

```sh
flow/gl_test.sh "$RUN" "$SHIPPING"
VALIDATION_ROOT="$SHIPPING" flow/gl_sdf_test.sh "$RUN" standard
VALIDATION_ROOT="$SHIPPING" flow/gl_sdf_test.sh "$RUN" v2
```

The receipt records the runner scripts and validation sources separately.
Use the same `--validation-root "$SHIPPING"` when checking the release.

Each command prints its fresh output directory and, only after success, its
`receipt.json`. Keep the complete output directories. The GL/SDF runners need
Icarus and cocotb. Precheck needs the flow Python packages and a `klayout`
binary on PATH, including a container wrapper when that is how the runner is
configured. The precheck wrapper copies the support tools and candidate
GDS/LEF/info.yaml into its private output directory. Existing
`tt/precheck/reports` and candidate files are not overwritten.

Then check the release:

```sh
python3 flow/release_check.py "$RUN" \
  --validation-root "$PWD" \
  --gl-receipt /path/to/gl.XXXXXX/receipt.json \
  --sdf-receipt /path/to/gl_sdf.STANDARD/receipt.json \
  --sdf-receipt /path/to/gl_sdf.V2/receipt.json \
  --precheck-receipt /path/to/precheck.XXXXXX/receipt.json
```

The default SDF profile is `v2`. `--sdf-profile standard` is an explicit,
narrower contract for a pre-v2 release; the final verdict names the profile.
`--validation-root` binds GL and SDF to the selected shipping tests/firmware.
Without it, GL must match the candidate worktree's own suite, and SDF records
the separate verification checkout used by its runner. The older candidate's
tests do not establish coverage of firmware added later.
Without `--gl-receipt`, the checker searches completed GL receipts in the
candidate worktree. An old failed attempt can remain alongside a newer,
complete passing attempt; partial attempts are never combined into a pass.

## What is required

- Minimum physical checks, plus finite setup/hold slack and zero timing
  violators at all three corners. The resolved clock period must agree with
  each final STA clock report and establish the requested operating rate.
- Zero slew, capacitance and fanout violations at every corner by default.
  A reviewed exception must be explicit, for example
  `--waive fanout=1 --waiver-reason 'reference to the reviewed exception'`.
  Such a verdict says `WITH ELECTRICAL WAIVERS`; it is not zero-violation closure.
- Functional GL: every declared test in `test`, `test_pio` and
  `test_eth_service`, exactly once, with no failures, errors or skips, on a
  byte-identical copy of the final netlist.
- Standard SDF: ID, UART TX, SPI master, host-port throughput and fetch-to-pin
  at typ/slow/fast with a 20 ns clock, plus an overclock control that completes
  with an assertion or the explicit cocotb unresolved-logic read error. An
  arbitrary Python exception, simulator crash, missing XML or bad annotation
  is a failed verification run, not a successful negative control.
  Unsupported SDF annotation warnings also fail; only the two known empty
  voltage/temperature header diagnostics are accepted. Compiler warnings that
  Icarus lacks setup/hold checks remain an explicit simulator limitation.
- v2 SDF additionally: fast SPI, timestamp capture and CALL/RET at 20 ns at
  every corner, and the full-speed USB device test at 20.834 ns at every corner.
- Precheck: successful process exit, a complete passing results file and the
  pass message for the private copy of this candidate's GDS.

`SLOW_CLK_NS=23 flow/gl_sdf_test.sh "$RUN" standard` remains useful for
diagnosing an older slow candidate. It does not establish 50 MHz operation.
Likewise, single `report` cases never satisfy release coverage. The separate
`v2long` set exercises sustained capture and reverse engineering; it is useful
additional evidence, not part of the directed release subset.

`--slow-mhz` states a required slow-corner rate, but never recomputes STA slack
by adding a period difference. A negative setup result needs fresh STA at the
claimed operating condition. The checker does not infer that a slower clock
fixes every path, including half-cycle and asynchronous paths.

## Why receipts replace pass logs

The first release checker could accept one passing XML file even when two
entire GL suites were absent. SDF and precheck needed only a pass string,
which could come from another design. It also assumed a 20 ns run and allowed
non-finite timing values. Regression tests reproduced all four false passes.

The runners now snapshot candidate and validation inputs before execution,
reject changes during execution, validate the named results, and write a
receipt after successful completion. They also record the runner scripts' hashes
and reject edits to those scripts during a run. Simulation uses the top-level
Icarus/SRAM configuration explicitly; SDF uses the typical value from each
corner's delay triples and a 4 ns external-driver delay. Inherited shell
settings cannot select another simulator, testbench, SRAM mode or SDF triple.
The candidate identity includes the
netlist, GDS, LEF, three SDFs, source/profile/configuration files, final metrics,
resolved configuration and each final STA clock report and log. Result files
are hashed and rechecked by the release gate. This catches stale, partial,
mixed, missing or subsequently altered evidence. Full output directories can
be archived or moved while preserving their relative artifact paths. Preserve
the selected validation checkout as well (or reproduce its exact file hashes)
and point `--validation-root` to it after relocation.

Validation inputs include the nested test sources and JSON fixtures. The
Ethernet tests import `test/model/test_eth10.py`, and the PIO test machine
loads `test/pio_program.json`; hashing only top-level Python/Verilog files
missed both. Regression cases demonstrate rejection when either changes
during a run or after its receipt. Generated `sim_build` and cache directories
are excluded, including private precheck copies with source-file suffixes.
GL/SDF receipts made with the earlier, incomplete input list do not match
the repaired verifier's selected validation checkout: rerun the simulations;
do not add missing hashes to an old receipt after execution.

Old logs remain useful historical evidence. They cannot be converted into a
new receipt after the fact: rerun the corresponding wrapper. Fixtures and
tool substitutes in the Python tests verify failure handling only; they are
not chip validation. The runners' real artifact results must be recorded
separately.

## Recorded release: fo6 with software d916d40 (2026-10-13)

Candidate run fo6 (netlist `a94a948b…`, GDS `e6769703…`), validation root
`d916d40`. Seven receipts: functional GL (29/29), SDF standard and v2 sets,
autonomous USB enumeration (low and full speed) with SDF at typ, slow and
fast at 20.834 ns, and precheck. `release_check.py` with all of them:
`RELEASE PASSED (SDF profile v2, slow target 50 MHz)`, no waiver. Run ci7, a
capped clean checkout of `d916d40`, reproduces the netlist and the
timestamp-normalized GDS. The archive
`wirelingo-release-evidence-d916d40-fo6.tar.gz` (sha256 `51da7962…`, 400
payload files) keeps every hashed input and result, the logs, the verdict
and the source bundle; the 93c2137 + t48ba archive (strict gate failing on
one fanout item) is kept for history.

## Limits

Receipts are local records, not cryptographic signatures or protection against
deliberately fabricated evidence. They preserve input identities and test
scope; they do not prove RTL/netlist equivalence, completeness of the SDC,
or the correctness of the PDK and tools. Record tool/PDK revisions with the
release archive. SDF with Icarus still lacks setup/hold timing checks and
covers only exercised transitions. STA remains necessary.

The directed USB test does not prove real-host enumeration, electrical USB
compliance or pad behavior. FPGA, external wiring and eventual silicon tests
retain their separate evidence. The standard/v2 profiles also do not claim
to exercise every firmware program in the repository.

Focused regression command:

```sh
PYTHONPATH=tools python -m pytest -q test/model/test_flow_tools.py \
  test/model/test_release_evidence.py test/model/test_release_runners.py
```
