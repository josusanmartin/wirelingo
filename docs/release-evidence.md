# Release evidence

`flow/signoff.py` remains the minimum physical check. `flow/release_check.py`
checks the stronger release contract: clean setup and hold at the actual
run constraint, electrical limits, complete functional GL, timing-annotated
tests at the requested operating clocks, and precheck of the exact GDS.
Passing tests do not override a failed physical check.

## Generate evidence

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

Old logs remain useful historical evidence. They cannot be converted into a
new receipt after the fact: rerun the corresponding wrapper. Fixtures and
tool substitutes in the Python tests verify failure handling only; they are
not chip validation. The runners' real artifact results must be recorded
separately.

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
