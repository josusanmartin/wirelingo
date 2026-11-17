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
fast at 20.834 ns; precheck), and an eighth, the clock/reset check STA
(`flow/check_rerun.py`, [below](#clock-reset-and-sram-power-gates-and-the-shipping-build)),
each in its own fresh output directory, with
at most `--jobs` (default 6) simulator processes at once: the standard set
counts 4, v2 counts 6, every other job 1. GL starts first (48 minutes for fo6
on a quiet runner). With the default budget, v2 starts only after GL ends;
`--jobs 7` lets them overlap.
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

## 48 MHz at the slow corner (8x4)

The 8x4 build aims for 50 MHz and accepts 48 MHz at the slow corner (the
author's decision, 2026-11-10); typical and fast stay at 50 MHz. A run is
hardened and timed at one `CLOCK_PERIOD` (20 ns), so its own STA cannot show
48 MHz at the slow corner. `flow/sta_rerun.py RUN 20.832` gives the fresh STA:
it re-runs the run's own LibreLane `OpenROAD.STAPostPNR` step (its step
config and input state: final netlist, extracted SPEF, signoff SDC and corner
Tcl) in the run's LibreLane image, with the run mounted read-only, no
network, and only `CLOCK_PERIOD` changed (`src/protoemu.sdc` scales the
clk-referenced I/O delays and the host port's SCK with it; the host lines'
external delays are fixed nanoseconds, and their CS_n terms follow the
periods: docs/isa.md §6.1, "Pad timing"). The image must be local and report
the run's own LibreLane version (`meta.librelane_version`; an `--image` with
another version is refused), and the STA runs it by the ID `docker image
inspect` gives. Its receipt (kind `sta`) binds the candidate files, that image
ID and version, the digests of every design file the step read plus the final
SPEF copy, the step config, and each corner's `sta.log` and `clock.rpt`; the
timing is read from the fresh `sta.log`, never from inherited state, and a
metric reported twice with two values is refused.

Verifying the receipt (in `release_check.py`) rechecks the run as well: it
recomputes the step's input digests and compares the step config, so a run
whose SPEF, odb, step `state_in.json` or `config.json` changed after the
receipt cannot release. It checks that the receipt's LibreLane version is the
run's (the receipt's config and the run's `resolved.json`) and that the
recorded command ran the recorded image ID; it does not ask docker. An
evidence archive keeps the candidate files but not the step's state, config,
odb or SPEF. There the inputs it does hold are checked, and so is the step
netlist against `final/nl` (3 of 9 files). The check prints each skipped check
(`slow-corner STA receipt, SKIPPED: ...`) and says so in its verdict (`..., 2
STA RECEIPT CHECKS SKIPPED (the run's STA inputs are absent))`). Anything at
all where a step file should be (one file, a directory, a dangling link)
means "not an archive", and a recorded input that is present must be a
regular file. On a live run, `--require-sta-inputs` refuses a receipt whose
checks would be skipped. `flow/release_evidence.py` always passes it and
refuses a verdict that says checks were skipped, and `flow/archive_evidence.py`
refuses such a verdict or receipt too. Then

```sh
python3 flow/release_check.py "$RUN" --slow-mhz 48 \
  --slow-sta-receipt /path/to/sta.XXXXXX/receipt.json ...
```

times the slow corner from that receipt (its period must be at most
1000/48 ns) and typical and fast from the run's own STA at 20 ns. The SDF
sets' slow cases must also run at 1000/48 ns or faster
(`SLOW_CLK_NS=20.832 flow/gl_sdf_test.sh "$RUN" standard`, and `v2`). The one
command does all of it with `flow/release_evidence.py RUN --slow-mhz 48 ...`:
an eighth job (`sta_slow`), `SLOW_CLK_NS=20.832` for the two SDF sets, and
the two options for `release_check.py`. 20.832 ns is 1000/48 rounded down to
an even number of picoseconds, 1.3 ps faster than 48 MHz: the testbench's
cocotb `Clock` must halve its period at the 1 ps precision, and refuses
20.833 ns ("Bad `period`", every test fails at time 0; the same reason the
USB full-speed cases run at 20.834 ns). A rerun at the run's own 20 ns
reproduces the run's final STA metrics, which is how the tool was checked
(docs/physical.md, 8x4).

## Clock, reset and SRAM power gates, and the shipping build

Added for the Astra review's findings 2 and 3. `release_check.py`
also requires the following; `flow/release_gates.py RUN [--checks-receipt R]`
prints the three physical gates on their own, read-only.

**Clock and reset checks.** The run's own `gating_pulse_reset.rpt`
(`src/timing_checks.tcl`; read from each corner's `sta.log`, a candidate file)
must be present and show no violation. It lists violators only, so it cannot
tell "no violation" from "no check", and under the signoff SDC it has no pulse
width check at all. OpenSTA 2.7.0 in the LibreLane 3.1.0.dev3 image reports no
minimum pulse width check, met or violated, on any pin driven by the `-from`
clock of a min/max-type exception (`set_max_delay`, `set_min_delay`,
`set_false_path -hold`): one such exception from `clk`, with or without a
`-to`, to the same clock or another, removes `clk`'s checks, and one each way
between `sck` and `clk`, as `src/protoemu.sdc` has, removes them all.
Measured on runner1 (2026-11-13; the clock-by-clock cases by the
independent verifier, on its own three-flop design, in a report that is not
in this repository): with no exception a two-clock
three-flop design keeps every check; with one `set_max_delay -from A -to B`
only clock B's flop keeps its check; with one each way, none; at a 0.2 ns
clock, violations disappear the same way. On fo6's final netlist, applying
`src/protoemu.sdc` line by line, the last checks disappear at the second
cross-clock `set_max_delay`, and, with those two left out, at the second
cross-clock `set_false_path -hold`. `flow/check_rerun.py RUN` therefore
re-runs the run's own `OpenROAD.STAPostPNR` step twice, read-only, in the
run's image by its ID (as `flow/sta_rerun.py` does):

* `signoff`: the run's SDC; `flow/check_types.tcl` reports the worst check and
  every violator of minimum pulse width, minimum period, recovery, removal and
  clock-gating setup/hold.
* `pulse`: the run's SDC after a preamble that makes the four path-exception
  commands (`set_max_delay`, `set_min_delay`, `set_false_path`,
  `set_multicycle_path`) no-ops by their global and `sta::` names, whatever
  line or Tcl construct calls them so. An exception made another way is not
  prevented; it is failed by the coverage rule below. `flow/pulse_checks.tcl`
  reports the worst pulse-width check, every violator, each SRAM clock pin
  (`A_CLK`), each clock's register clock pins, all register clock pins and
  every pulse-width check. Pulse widths depend on the clock network alone,
  which no path exception touches; this pass's other results are not used.

Its receipt (kind `checks`) binds the candidate files, the step's input
digests, both passes' configs (the run's own except the report Tcl and, for
`pulse`, the SDC), the derived SDC (recomputed from the run's on verify), the
report Tcl and the reports, and the image ID; `release_check` also requires
the receipt's report Tcl to be its own checkout's, and each report's corner
header to be its directory's. `--checks-receipt R` then requires, at every
corner, a met worst check of pulse width, and the coverage rule: every
register clock pin (`all_registers -clock_pins`) must be reached by a clock
(in some `all_registers -clock C -clock_pins`) and have a pulse-width check,
so that a check lost to an exception, or a register no clock reaches (case
analysis on a clock port, a divided clock without a generated clock), fails.
A pin counts as checked with one row: one lost polarity of a flop's two
(high and low; a latch has one) is not caught, which only an exception left
active by its `-rise_from`/`-fall_from` form would cause. Then a check on
every SRAM instance's clock pin, met recovery and removal checks, and no
violator of any kind. Two kinds may have no check, each for a stated reason (never a
violation): minimum period (no library the chip uses defines a minimum-period
arc) and clock-gating setup/hold (the clock gates are the library's integrated
cells, timed by their setup/hold arcs). `flow/release_evidence.py` runs the
job and passes the receipt.

**SRAM power.** The `Project.ExtendPowerStripes` log (`flow/odb_stripes.py`)
must have finished both nets, give each SRAM a VPWR/VGND pair in each of its
three regions (two arrays and the band), and print no warning or error
other than a listed one (OpenROAD's `ORD-0039` start-up notice). In the final
DEF, every power pin of every macro (`VDD!`, `VDDARRAY!`, `VSS!` on the SRAM)
must be on the net `PDN_MACRO_CONNECTIONS` names, be touched on its own layer
by a stripe of that net in each half of the macro, and by no stripe of another
net. The flow's own `ERROR_ON_PDN_VIOLATIONS: 0` (the expected PSM-0069 before
the step) is unchanged: these gates read the results, they do not change the
flow.

**The shipping build** (`flow/build_manifest.py`). `--shipping-root CHECKOUT`
requires the run's build inputs to equal the checkout's: everything under
`src/` (the generated `config.json` and the `project.v` defines as the run
used them, the SDCs, PDN and corner Tcl, the RTL and macro models),
`info.yaml`'s build keys (tiles, top module, source files, language, clock),
the LibreLane plugin and every `flow/` script it runs or names (and the Tcl
those source). The generator `flow/gen_config.py` is recorded but not compared,
since no build runs it; instead the checkout's own `gen_config.py --preset
submission --check` must pass, as it must in CI. A run hardened with a tuned
configuration or another core count than the shipping commit holds fails,
however good its receipts.
Firmware, tools and tests are not build inputs, so validating new firmware on
a pinned netlist (`--validation-root`) remains a separate operation.
`flow/release_evidence.py` passes its validation root as the shipping commit
unless `--shipping-root` names another. The run's tools (the `tt/` tree and
its commit, the LibreLane version, the PDK's `SOURCES` line), its netlist and
its GDS with the BGNLIB/BGNSTR dates zeroed are recorded too; a checkout has
none of them, so `release_check` prints them uncompared. `build_manifest.py
compare RUN OTHER --require-tools` compares two builds in full, for example
the receipted run with a CI build's `build-manifest.json`
(`build_manifest.py write`).

Without `--shipping-root`, or in an evidence archive (which keeps neither the
DEF, the step logs, `flow/` nor `tt/`), those gates are skipped and the
verdict says `N RELEASE GATES SKIPPED`; `--require-sta-inputs` makes a skip a
failure, and `release_evidence.py` and `archive_evidence.py` refuse such a
verdict. An archive also needs one `checks` receipt.

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

## The 3-core 8x4 release candidate

**Status: the release build (2026-11-16).** The release run, a clean harden
of the shipping commit `2c97c15` by the procedure below, passed:
`RELEASE PASSED (SDF profile v2, slow target 48 MHz)`, its identity matching
and equivalence `EQUIVALENT`
([the record](#recorded-release-3-core-8x4-shipping-commit-2c97c15-2026-11-16)).
Nothing here is silicon or FPGA; the levels are STA, gate
level (functional and with SDF), precheck and formal equivalence under the
checker's model. The design, its timing and its reproductions are in
[physical.md](physical.md#the-3-core-release-candidate-2026-11-15).

**The rehearsal (run aqp-rt10s5-pre, 2026-11-15).** A clean harden of
`77c3936` through `flow/harden.sh`'s clean-tree path, then
`flow/release_evidence.py --slow-mhz 48`. It reproduced the candidate's
netlist (`453d0af7…`) and GDS with its dates zeroed (`357ea15b…`). Receipts:
functional GL 30/30, `test_pio` 2/2 and `test_eth_service` 2/2; SDF
`standard` 5/5 at typ 20 ns, slow 20.832 ns and fast 20 ns, the slow 10 ns
control failing as required; SDF `v2` 3/3 at the same three and USB full
speed 1/1 at 20.834 ns at each corner; autonomous USB 2/2 at 20.834 ns at
each corner; the fresh slow STA at 20.832 ns; the clock and reset checks;
precheck. Beside them, `flow/sta_nonlatch.py` at 20.832 ns (+0.4304 ns slow,
+3.3180 typ, +3.9191 fast) and `formal/equiv/equiv_check.sh --mutants`
(`EQUIVALENT`, 10754/10754, 9 of 9 mutants caught). The release check
printed `RELEASE PASSED (SDF profile v2, slow target 48 MHz)` once it ran in
the prepared tool environment; its first run, from a plain shell, failed only
on regenerating the configs, which needs that environment (a verifier
finding, fixed by `d6dbd38`, which regenerates from a plain shell). Records:
[evidence/8x4-release-candidate-20261115/](../evidence/8x4-release-candidate-20261115/README.md).

The rehearsal cannot release the shipping commit: its tree is not the
shipping tree, and its clean-tree receipt predates the gate's current
format. The release takes its own run.

**The release run.** The release harden certifies one commit, the shipping
commit `S`. The procedure needs `flow/harden.sh` from `e8ac948` or later, which runs LibreLane unbuffered so that `exp.log` keeps the
container command naming the image, as `release_check.py`'s clean-tree gate
requires. On the compute runner:

1. in a clean clone of `S`, `flow/harden.sh NAME` with no options (the
   committed core count, so the submission preset), which records the
   clean-tree receipt at the start and after config generation. The clone
   needs `tt/` at the pinned `d66cf17`, `.venv-flow` as a directory (or as a
   link listed in `.git/info/exclude`, since an untracked link makes the tree
   dirty), the PDK at its pinned commit `2bbec755` (`PDK_ROOT`, default
   `~/ttpdk`) and the pinned LibreLane image (`flow/release_pins.json`),
   with `PYTHONPATH` and `LIBRELANE_IMAGE_OVERRIDE` unset, and `STOP` and
   `FIFO_LOG2` unset too (`harden.sh` reads both: `STOP` switches to the
   experiment preset, and `FIFO_LOG2` rewrites `src/project.v`);
2. `S/flow/release_evidence.py RUN --validation-root S --slow-mhz 48
   --require-nonlatch-floor 0.3 --expect S/evidence/8x4-release-expected.json`
   (`S` is then also the shipping root): the receipt jobs, the fresh slow
   STA, the clock and reset checks, the non-latch STA at 20.832 ns
   (`flow/sta_nonlatch.py`), `flow/release_check.py` and, on a pass, the
   evidence archive `wirelingo-release-evidence-<rev>-<run>.tar.gz`. The two
   last options make the release check refuse the run unless:
   * the worst non-latch setup slack at the slow corner, timed at 20.832 ns
     or tighter, is at least +0.3 ns (`--require-nonlatch-floor`; the
     candidate has +0.4304 ns). The all-endpoints figure cannot show this:
     the FIFO latches' time borrowing makes it read 0;
   * the run's final netlist sha256, its GDS digest with the BGNLIB/BGNSTR
     dates zeroed and the late-clock step's count (the
     `LATE_CLOCK_COMPLETE 64 registers 23 branches` line of the SRAM clock
     advance step log) equal those in
     [evidence/8x4-release-expected.json](../evidence/8x4-release-expected.json)
     (`--expect`, `flow/release_expected.py`): `453d0af7…`, `357ea15b…`, 64
     registers on 23 branches. So the numbers in physical.md are the
     released netlist's, and a late clock that matched other registers
     fails here even though the flow completes. The file also requires the
     floor option and a 48 MHz slow target at least;
3. `formal/equiv/equiv_check.sh --mutants RUN`.

Without the two options the release check verifies other runs and older
archives as before, and prints `release identity: not compared`. If the
identity differs, the release stops for investigation: a moved placement can
lose 48 MHz (aqp-rt10d48, physical.md).

**Where its receipts go.** `flow/release_check.py` refuses a run whose tree
differs from the shipping tree, so the release run's receipts cannot be in
`S` itself. They go into `evidence/8x4-release-<date>/` in a follow-up
commit: a summary of the `release_evidence.py` archive (verdict, receipt
list, case counts), the non-latch receipt, the equivalence output, the
netlist and dates-zeroed GDS hashes, and `S`'s commit id. This section
describes the procedure and does not quote the run's results; that folder
does. For `S` = `2c97c15` it is
[evidence/8x4-release-20261116/](../evidence/8x4-release-20261116/README.md),
summarized in
[the recorded release below](#recorded-release-3-core-8x4-shipping-commit-2c97c15-2026-11-16).

**What may follow `S`.** Only commits that touch `evidence/` and `docs/`,
`README.md`, `.github/workflows/gds.yaml` and the test file that reads it,
`test/model/test_release_expected.py`. `README.md` is documentation,
not a build input, and the author allowed it on 2026-11-16 so that its
status lines could give the released state. `gds.yaml` is not a build input
either: it says how GitHub runs the build, not what is built, and its last
step compares GitHub's build with the expected identity below, so a green
`gds` job shows that the shipped GDS is the receipted build whatever the
workflow looks like. The author allowed it on 2026-11-16 to split the GitHub
build into three jobs (**Why GitHub builds in three jobs**, below); the test
file came with it, since its workflow tests check the jobs, the pins and the
hash step. For a later commit `T`,
`git diff --stat S T -- . ':!evidence' ':!docs' ':!README.md' ':!.github/workflows/gds.yaml' ':!test/model/test_release_expected.py'`
must print nothing, and `flow/build_manifest.py compare <S checkout> <T checkout>`
must print `SAME BUILD` (it does with the `README.md`, `gds.yaml` and test
changes: none of them is among the build inputs the manifest binds). The expected-identity file is under `evidence/` but
must not change either: `git diff --stat S T -- evidence/8x4-release-expected.json`
must print nothing, since GitHub's build of `T` is compared with it.

**What GitHub builds.** The repository's GDS workflow builds from the pushed
commit, not from the release run. The workflows pin
`TinyTapeout/tt-gds-action` to commit `3412659` (the head of its
`ihp-cmos5l` branch on 2026-11-15) at every use: in
`.github/workflows/gds.yaml` the three actions `precheck`, `gl_test` and
`viewer` use, and the three checkouts of the action's repository in the build
jobs, which run its `install_sg13cmos5l.sh`; and the one each in `docs.yaml`
and `fpga.yaml`. tt-support-tools is pinned to `d66cf17` (`tools-ref`, or
`ref` in the build jobs' checkout) for the
build, the precheck and the docs job. `gl_test` and the viewer check out no
tt-support-tools. The FPGA workflow (started by hand; its push trigger is
disabled) passes no `tools-ref`, so it takes its action's default, the
tools' `main` branch. At that commit the
action's script installs IHP-Open-PDK `2bbec755`, and the build jobs install
LibreLane 3.1.0.dev3 with pip as the action does, the versions
`flow/release_pins.json` pins.
LibreLane then runs in its docker image by the version tag; neither the action
nor the workflow pins the image digest (`sha256:d109140b…` in the release
runs). The last step of the `gds` job runs
`flow/release_expected.py . evidence/8x4-release-expected.json`:
it prints the expected and the actual netlist sha256 and dates-zeroed GDS
digest and fails the job on a difference, so a GitHub build of another
placement cannot finish green.

**Measured on GitHub (2026-11-15 to 17).** The workflows ran on GitHub's
hosted runners for four commits with the same build inputs
(`flow/build_manifest.py compare` prints `SAME BUILD` for each of the others
against `S`):
`a84401f`, the verified commit before `S`, and `S` itself, on a test branch
of the public repository; `bc8882b` (`S` plus the release run's evidence) on
`main`; and `00cd1c6` (the three-job split below), on the test branch and
then on `main`. Every `gds` job that succeeded printed both pairs of the
hash step as `(match)`, netlist `453d0af7…` and dates-zeroed GDS
`357ea15b…`, then `RELEASE IDENTITY MATCHES evidence/8x4-release-expected.json`,
with the image tag resolved to the digest above:

| Commit (branch) | `gds` run | Build | Precheck step | Other jobs |
| --- | --- | --- | --- | --- |
| `a84401f` (test branch) | 43753001925 | one job, 5 h 13 min 33 s | 4 h 39 min | `gl_test`, `viewer`, `test`, `formal` green; `mutants` 366 of 367 (the survivor's test fix `e610fcb` is in `S`) |
| `2c97c15` = `S` (test branch) | 43787610261 | one job, 4 h 21 min 58 s | 4 h 28 min | all green; `mutants` 367 of 367 |
| `bc8882b` (`main`) | 43811275309 | attempts 1 and 2 cancelled at the 6 h limit; attempt 3 one job, 4 h 29 min 53 s | 5 h 04 min | all green; `mutants` 367 of 367 |
| `00cd1c6` (test branch) | 43956712707 | three jobs: 44 min 38 s, 2 h 51 min 36 s, 36 min 46 s | 5 h 30 min | all green; `mutants` 367 of 367 |
| `00cd1c6` (`main`) | 44034950929 | three jobs: 45 min 09 s, 4 h 41 min 14 s, 36 min 41 s | still running when this was written (started 13:00:03 UTC) | `viewer` green; `gl_test` green; `test`, `formal` green; `mutants` 367 of 367 |

On `main`, attempt 1 of the one-job build was still in LibreLane (its
`KLayout.DRC` step) when GitHub cancelled it; attempt 2's flow completed
after 5 h 49 min but the job was cancelled in the action's PNG render after
it, so its hash step did not run. Detailed routing alone took from 2 h 49 min
(the test-branch split run's) to 4 h 41 min (`main`'s attempt 1). `main` was
fast-forwarded to `00cd1c6` on 2026-11-17. Its `gds_route` job took 281 min
(07:42:03 to 12:23:17 UTC) against the workflow's own `timeout-minutes: 350`
for the job, 69 min of margin, measured once; its `gds` job's hash step printed
both pairs as `(match)`. Its `gl_test` succeeded (`TESTS=30 PASS=30 FAIL=0 SKIP=0`); its `precheck`,
started at 13:00:03 UTC, had not finished when this was written, and no result
of it is claimed here.
Records, with every job's result, times and the log lines quoted:
[evidence/8x4-release-20261116/](../evidence/8x4-release-20261116/README.md).

**The precheck's time on GitHub (a measured risk).** The `precheck` job is
Tiny Tapeout's own action, one job running one KLayout process for its main
DRC run; the split does not change it. Its step took 268 min (`S`), 279 min
(`a84401f`), 304 min (`bc8882b`) and 330 min (`00cd1c6`, the split run's),
against GitHub's 6 h (360 min) limit for a job. Most of it is one phase of
that DRC run, from "Starting IHP-SG13CMOS5L DRC connectivity setup" to
"Starting IHP-SG13CMOS5L DRC rules.": 01:22:44 to 06:30:25 UTC (5 h 08 min)
in the split run's precheck job 133743926009. None timed out, but the
slowest ended about 30 min before the limit. If one timed out, the plan is to
re-run the failed job on the same GDS. That has not been exercised. The basis
is how run 43811275309 kept its artifacts: after its third attempt, the
`tt_submission` artifact of the cancelled second attempt is still listed in
the run (`gh api repos/josusanmartin/wirelingo/actions/runs/43811275309/artifacts`),
so a re-run attempt does not remove an earlier attempt's artifacts.

**Why GitHub builds in three jobs.** On a slower hosted runner the one-job
build needs about 6 h 10 min, and GitHub cancels a hosted job at 6 h:
`main`'s GDS run 43811275309 was cancelled at the limit twice. In GitHub's
step times for such runners, setup takes about 3 min, LibreLane's steps 1 to
50 about 1 h 05 min, detailed routing (step 51) 4 h 27 min to about 4 h 40 min,
steps 52 to 79 about 30 min and the action's PNG render 8 to 16 min. So
`gds.yaml` runs the build as three jobs, each with its own 6 h limit and
`timeout-minutes: 350`:

| Job | LibreLane steps | Hands on | Estimated time on that runner |
| --- | --- | --- | --- |
| `gds_place` | 1 to 50, `--to OpenROAD.STAMidPNR-3` | `gds_run_placed` | about 1 h 10 min |
| `gds_route` | 51, `--from Project.DetailedRouting --to Project.DetailedRouting` | `gds_run_routed` | about 4 h 35 min to 4 h 50 min |
| `gds` | 52 to 79, `--from Odb.RemoveRoutingObstructions`, then the action's remaining steps and the hash check | `GDS_logs`, `tt_submission`, `gds_render` | about 1 h |

The times are estimates: the cancelled run's step times plus each job's setup
(about 3 min there), not a measured split run. The split run on GitHub
since (`00cd1c6`, above) took 44 min 38 s, 2 h 51 min 36 s and 36 min 46 s.
Each job first does what the
action does before it hardens, at the same pins (its environment, packages,
tt-support-tools, Python 3.11, its PDK script, LibreLane 3.1.0.dev3), then
runs tt-support-tools' own `tt_tool.py --harden` with two changes made from
the workflow: the job's step range is added to the LibreLane command the
tools build, and the resuming jobs keep `runs/wokwi` instead of clearing it.
So `commit_id.json` and `pdk.json` are still the tools' own. The run
directory goes to the next job as a zstd tar in an artifact (kept three
days), with the sha256 of `src/user_config.json` and `src/config_merged.json`,
which the next job checks against its own. GitHub checks the repository out
at the same path on every runner, so the absolute paths in the run directory's
state files stay valid. A resumed job names its starting state
(`--with-initial-state`, the state file of the step before it): without it
LibreLane takes the newest `state_out.json` by modification time, and a
composite step's last sub-step writes its state file in the same instant as
the step itself. LibreLane numbers a resumed run's steps on from the highest
step directory, so the directories are named as in one run, and each job
checks the boundary directory. The `gds` job keeps the job id, the artifact
names and the steps after the build, so `precheck`, `gl_test` and `viewer`
read the same artifacts as before (`gl_test` and `viewer` ran green on both
split runs, 43956712707 and 44034950929; `precheck` ran green on the
test-branch split run 43956712707, and on `main`'s 44034950929 it had not
finished when this was written). That
Tiny Tapeout's app reads a split run as it reads a one-job run is an
expectation only: the app's source is not public, and nothing in these
records tests it. The hash step, unchanged
at the end of `gds`, is what shows the split changed nothing in the chip.
Checked on runner1 (2026-11-16), on copies of the release run aqp-rel-2c97c15
with the later steps removed, the run directory packed and unpacked as the
jobs do, and LibreLane resumed with the jobs' arguments in the release run's
image (the copy mounted at the original's path). Once with the `gds` job's
part alone (steps 52 to 79), and once with step 50 under `gds_place`'s stop,
then `gds_route`'s part (2 h 02 min of routing there) and the `gds` job's:
both gave netlist `453d0af7…` and dates-zeroed GDS `357ea15b…` (`RELEASE
IDENTITY MATCHES`); step 51's routed database, DEF and netlist were
byte-identical to the release run's; the step directories were the same, and
the final views differed from the
release run's only in their dates (the GDS headers, the SDF and SPEF `DATE`
lines, the magic `timestamp`) and in how `metrics.csv` writes one number:
`route__wirelength__estimated` is `2519140` where the one-job run wrote
`2.51914E+6`, the same value read back from a state file. Records:
[evidence/gds-split-20261116/](../evidence/gds-split-20261116/README.md).

**What the pin fixes, and what it does not.** This list is read from the
action's source at `3412659` and from tt-support-tools at `d66cf17`. None of
it was run. The only commands run for it were the two branch-head lookups on
2026-11-15 (`ihp-cmos5l` is at `3412659`; tt-support-tools' `ihp-sg13cmos5l`
is at `d66cf17`) and `python --version` in runner1's flow venv.

* **Fixed by commit or version:** the action; tt-support-tools at `d66cf17`,
  with its Python packages pinned by its `requirements.txt` files;
  IHP-Open-PDK `2bbec755`, which `install_sg13cmos5l.sh` fetches by commit for
  the build, the precheck and `gl_test`; `librelane==3.1.0.dev3`; the
  precheck's nixpkgs, by a tarball commit; and the two actions
  `awalsh128/cache-apt-pkgs-action` and `rikhuijzer/cache-install`, by commit.
* **Not fixed:**
  - the LibreLane docker image, taken by its version tag (above) and pulled
    again by each of the three build jobs. This is the one that matters for
    the build;
  - LibreLane's own pip dependencies: only LibreLane's version is given, and
    pip resolves the rest at install time;
  - the `actions/*` actions (`checkout`, `setup-python`, `upload-artifact`,
    `download-artifact`, `upload-pages-artifact`, `deploy-pages`) and
    `test-summary/action`, all referenced by tag. `TinyTapeout/ciel-action`
    is also by tag, but its step is skipped for this PDK;
  - three more references by tag, none of them in `gds.yaml`'s jobs:
    `typst-community/setup-typst@v5` (the action's `docs/action.yml`, used
    by `docs.yaml`), `YosysHQ/setup-oss-cad-suite@v4` (its
    `fpga/ice40up5k/action.yml`, used by `fpga.yaml`) and
    `YosysHQ/setup-oss-cad-suite@v3` (the formal job of this repository's
    `.github/workflows/test.yaml`). Read from those three files, the
    action's two at `3412659`; not run;
  - Python: 3.11 as the runner's toolcache resolves it. runner1's flow venv
    (the default `--flow-python` above) reports 3.12.3, read on 2026-11-15;
    no tracked log holds it;
  - `gl_test`'s iverilog, downloaded from a release URL (`v13.0`) with no
    hash;
  - apt packages, at whatever version the runner has.

**What the hash step covers.** It is the backstop for the build only: it does
not cover the tools of the precheck or of `gl_test`. It runs after the `gds`
job has uploaded the `tt_submission` artifact, so on a mismatch the job is red but
the artifact exists. tt-support-tools' `create_tt_submission` copies the
hashed GDS into `tt_submission/` with `shutil.copyfile`, so the artifact's
`.gds` is a byte copy of the hashed file. Next to it the same function writes
an `.oas` conversion (KLayout), which no workflow step hashes. This too is read
from the source, not run. The `.oas` and `.lef` of six `tt_submission`
artifacts (`a84401f`, `S`, `bc8882b` attempts 2 and 3, and `00cd1c6` on the
test branch and on `main`) were downloaded and hashed on 2026-11-17: each is
the same file in all six (`.oas` `b990a29e…`, `.lef` `498cd3ef…`), and each
artifact's netlist and dates-zeroed GDS equal the expected identity
([evidence/8x4-release-20261116/github/tt-submission-hashes.txt](../evidence/8x4-release-20261116/github/tt-submission-hashes.txt)).
That is a record, not a check: the hash step still covers neither file, and
which file a Tiny Tapeout shuttle takes from the artifact is not recorded
here.

## Recorded release: 3-core 8x4, shipping commit 2c97c15 (2026-11-16)

The release run of the procedure above, on runner1, for the shipping commit
`S` = `2c97c15b5ab47d226798ed51d3d88f09eb548b21` (tree `b4b79c79…`).
Evidence levels: harden and STA, gate-level simulation (functional and with
SDF delays), Tiny Tapeout's precheck, formal equivalence under the
checker's model. None of it is silicon; the chip has not been fabricated.
Records:
[evidence/8x4-release-20261116/](../evidence/8x4-release-20261116/README.md)
(`release-run/`).

* **The harden.** Run aqp-rel-2c97c15: `flow/harden.sh aqp-rel-2c97c15` with
  no options in a clean clone of `S`, at nice 19, with `PYTHONPATH`,
  `LIBRELANE_IMAGE_OVERRIDE`, `STOP` and `FIFO_LOG2` unset, from 2026-11-15
  23:20 to 2026-11-16 01:42 UTC (142 minutes). The clean-tree receipt says
  `CLEAN (commit 2c97c15b5ab4, tree b4b79c79c94a)` at the start and after
  config generation. `flow/signoff.py`: 0 routing DRC, 0 KLayout DRC, 0 LVS
  errors, 0 antenna violations, no slew or capacitance violation,
  `SIGNOFF PASSED`; at the run's 20 ns constraint, setup WS −0.4016 ns with
  13 violators at the slow corner (so 50 MHz is not claimed), hold
  +0.212 / +0.395 / +0.105 ns typ / slow / fast.
* **The receipts.** `flow/release_evidence.py` from a second clean clone of
  `S` as the validation root, a third as the shipping root, `--slow-mhz 48
  --require-nonlatch-floor 0.3 --expect`, 01:49 to 03:19 UTC, ten jobs, all
  exit 0:

  | Job | Result |
  | --- | --- |
  | functional GL | `test` 30/30, `test_pio` 2/2, `test_eth_service` 2/2 |
  | SDF standard | 5/5 at typ 20 ns, slow 20.832 ns and fast 20 ns; the slow 10 ns control fails as required |
  | SDF v2 | 3/3 at typ 20 ns, slow 20.832 ns and fast 20 ns; USB full speed 1/1 at 20.834 ns at each corner |
  | autonomous USB, SDF | low and full speed 2/2 at 20.834 ns at typ, slow and fast |
  | fresh slow STA | 20.832 ns: setup WS 0 (latches included), 0 violators, at each corner |
  | non-latch STA | 20.832 ns: +0.4304 ns slow (at `_53246_/D`, above the 0.3 ns floor), +3.3180 typ, +3.9191 fast |
  | clock and reset checks | 3885 register clock pins clocked and pulse-width checked at each corner; pulse width, recovery and removal met |
  | precheck | passes; its receipt verifies |

  The release check: `RELEASE PASSED (SDF profile v2, slow target 48 MHz)`,
  no waiver. Clean tree: the run's tree equals the shipping commit's; tt,
  image and PDK as pinned; the configs regenerated from the shipping tree
  equal the files LibreLane read; the build inputs equal the shipping
  clone's. Release identity: netlist `453d0af7…`, dates-zeroed GDS
  `357ea15b…` and the late clock, 64 registers on 23 branches, each a match
  with [evidence/8x4-release-expected.json](../evidence/8x4-release-expected.json).
* **Equivalence.** `formal/equiv/equiv_check.sh --mutants` on the run:
  `EQUIVALENT`, ABC equivalent, `equiv_simple` 10754/10754 proven, 9 of 9
  planted netlist mutants caught.
* **The archive.** `wirelingo-release-evidence-2c97c15-aqp-rel-2c97c15.tar.gz`,
  sha256 `1ff19b7d7d673aeb37c88d941d13ae01750274f2cc838fbdb9283ef2e8bedb8b`,
  72,314,042 bytes, 568 payload files, 10 verified receipts. It is not in
  git and not yet published: the repository has no GitHub release yet (`gh api repos/josusanmartin/wirelingo/releases`, 2026-11-17).

The netlist and the dates-zeroed GDS are the release candidate's
([evidence/8x4-release-candidate-20261115/](../evidence/8x4-release-candidate-20261115/README.md)),
so the figures in [physical.md](physical.md#the-3-core-release-candidate-2026-11-15)
are the released build's. GitHub's builds of `a84401f`, `S`, `bc8882b` and
`00cd1c6`, which have the same build inputs, printed the same two hashes
(above).

## Recorded release: fo6 with software 5457d8e (2026-10-14)

Candidate run fo6 (netlist `a94a948b…`, GDS `e6769703…`), validation root
`5457d8e` = `main` `ee3dd25` plus `flow/gl_module_test.py` and its test
`test/model/test_gl_module.py`, nothing else. Its `firmware/`, `tools/`,
`test/` (apart from that one model test) and `src/` are `main`'s: the
third post-freeze round, the `ws2812` 0-bit and `can_rx` stuff-bit fixes
and the 246-word `usb_enum` are covered. Generated with the one command
above on runner1, `--jobs 7` (clone `~/protoemu-flow/rel-7685aba`, work
directory `~/protoemu-flow/rcpt-fo6-7685aba-20261014T103430Z`),
10:34:30–11:27:35 UTC (53 minutes, at most 7 simulator processes), all from
fresh output directories:

| Job | Result | Receipt |
| --- | --- | --- |
| functional GL | test 25/25, test_pio 2/2, test_eth_service 2/2 (3135 s) | `rcpt-…/candidate/test/sim_build/gl.MGs14Q` |
| SDF standard | typ/slow/fast 20 ns 5/5 each; slow 10 ns control fails as required | `rel-7685aba/test/sim_build/gl_sdf.y3yb5I` |
| SDF v2 | typ/slow/fast 20 ns 3/3, USB FS 20.834 ns 1/1 each | `rel-7685aba/test/sim_build/gl_sdf.akCoF8` |
| autonomous USB, SDF | LS + FS enumeration 2/2 at 20.834 ns: typ, slow, fast | `gl_sdf.qXbbzP`, `gl_sdf.1JdG7G`, `gl_sdf.x4NsnJ` |
| precheck | pass for the private GDS copy | `rcpt-…/precheck` |

`release_check.py` against the real fo6 path with all seven receipts:
`RELEASE PASSED (SDF profile v2, slow target 50 MHz)`, no waiver. fo6 was
only read. `src/`, the existing `flow/` scripts, `info.yaml` and the
LibreLane plugin are byte-identical in `d890596`, `00dfa75`, `4f5179d` and
`5457d8e`, so the ci7 reproduction still applies. The archive
`wirelingo-release-evidence-7685aba-fo6.tar.gz` (sha256
`868c5d3ab8010cac27040617a869377076df6067e9fa1f28203ba5cf357409bf`, 473
payload files, runner1 `~/protoemu-flow/` and the build machine's home directory) has the same
contents as the 4f5179d record's: every hashed input and result, the job
logs, the verdict, the source bundle, ci7's candidate files and the
reproduction comparison. Extracted into an empty directory, its own
`release_check.py` re-verifies all seven receipts and passes. The archive
(about 55 MB) is not in git and not yet published, although the repository
is public: the repository has no GitHub release yet (`gh api repos/josusanmartin/wirelingo/releases`, 2026-11-17).

Supplementary, outside the release gate: `test_post_freeze` (16 tests) on
the same netlist with `flow/gl_module_test.py`, validation `9254b9e`
(the same `test/`, `tools/`, `firmware/` and `src/` as `5457d8e`; its
receipt verifies against `rel-7685aba`). Functional: 16/16 pass,
hash-bound receipt `rel-beddbe2/test/sim_build/glm.hra0lnoh/receipt.json`
(sha256 `e4be0ed4…67f1`). SDF at the typical corner: 14/16 pass, each with
its own `gl_sdf_test.sh` receipt. `pf_dmx512` and `pf_wiegand` fail on
exact-equality timing assertions: the pads' rise and fall delays differ by
50–170 ps, so a 176 us BREAK measures 175999.83 ns. The protocols are
decoded correctly. After a 1 ns tolerance in those two assertions (commit
dfdaa27), both pass at SDF typ on fo6 (separate single-case receipt); see
[post-freeze-protocols.md](post-freeze-protocols.md#results). Archive
`wirelingo-postfreeze-gl-beddbe2-fo6.tar.gz` (sha256 `7466b8f8…0485`).

SDF at the slow and fast corners, 20 ns (2026-11-02), also supplementary:
`test_post_freeze` 16/16 at each corner on fo6's netlist, validation `af2c097`,
each with its own `gl_sdf_test.sh` receipt (`pfsdf-61af9b2/test/sim_build/`:
slow `gl_sdf.I0i5IJ/receipt.json`, sha256 `2d16097a…90b1`; fast
`gl_sdf.QWI1Yc/receipt.json`, sha256 `fdae9400…3f2a`). These are single-case
runs, with no negative control of their own; the release set's 10 ns control
shows the annotation in effect on the same netlist.

Host-workflow modules at gate level (2026-11-02), also supplementary and functional
(no SDF): 6/6 tests on fo6's netlist with `flow/gl_module_test.py`, validation
`af2c097`: `test_usb_uart_rtl` 3/3 (`glm.o49y18cw`, sha256 `a13b2398…6a65`),
`test_spi_packed_host` 1/1 (`glm.ldn0okrb`, `b4c09537…d8dd`),
`test_spi_preloaded_host` 1/1 (`glm.1uhplvjn`, `7b4e62aa…ba44`),
`test_uart_capture_host` 1/1 (`glm.cuwsqxi6`, `f27a1435…7e19`). Receipts are under
`pfsdf-61af9b2/test/sim_build/` on runner1.

## Recorded release: fo6 with software 4f5179d (2026-10-14)

Candidate run fo6 (netlist `a94a948b…`, GDS `e6769703…`; `5e0e3c6e…` with
its timestamps zeroed, the hash in tag `rc-00dfa75-fo6`), validation root
`4f5179d` = main `928c197` plus `flow/release_evidence.py`,
`flow/archive_evidence.py`, their tests and this document. Generated with the
one command above on runner1 (clone `~/protoemu-flow/rel-f1cb7c9`, work
directory `~/protoemu-flow/rcpt-fo6-f1cb7c9-20261014T045116Z`), 63 minutes
with at most 6 simulator processes, all from fresh output directories:

| Job | Result | Receipt |
| --- | --- | --- |
| functional GL | test 25/25, test_pio 2/2, test_eth_service 2/2 | `rcpt-…/candidate/test/sim_build/gl.vd8Mgr` |
| SDF standard | typ/slow/fast 20 ns 5/5 each; slow 10 ns control fails as required | `rel-f1cb7c9/test/sim_build/gl_sdf.KVmaYm` |
| SDF v2 | typ/slow/fast 20 ns 3/3, USB FS 20.834 ns 1/1 each | `rel-f1cb7c9/test/sim_build/gl_sdf.IiPCW7` |
| autonomous USB, SDF | LS + FS enumeration 2/2 at 20.834 ns: typ, slow, fast | `gl_sdf.OQongm`, `gl_sdf.kwggKU`, `gl_sdf.0T2DPo` |
| precheck | pass for the private GDS copy | `rcpt-…/precheck` |

`release_check.py` against the real fo6 path with all seven receipts:
`RELEASE PASSED (SDF profile v2, slow target 50 MHz)`, no waiver. The firmware,
tool and test changes since `00dfa75` introduced no GL or SDF regression. fo6
itself was only read. Physical-build inputs (`src/`, the existing `flow/`
scripts, `info.yaml`, the LibreLane plugin) are byte-identical in `d890596`,
`00dfa75` and `4f5179d`, so the ci7 reproduction (identical netlist and
normalized GDS) still applies. The archive
`wirelingo-release-evidence-f1cb7c9-fo6.tar.gz` (sha256 `091a1070…4209a`,
461 payload files; runner1 `~/protoemu-flow/` and the build machine's home directory) keeps every
hashed input and result, the job logs, verdict and source bundle, the ci7
candidate files and the reproduction comparison. Extracted elsewhere, its
own `release_check.py` re-verifies all seven receipts and passes. A later
commit that touches only docs keeps the same validation inputs; any change
to `test/`, `tools/`, `firmware/` or `src/` needs a new run. `main` has since
changed `firmware/`, `test/` and `tools/` (the third post-freeze round, the
`ws2812` and `can_rx` timing fixes, the bundled MicroPython images): those
are not covered by this record (the `5457d8e` record above covers them).
`src/` is unchanged.

## Recorded release: fo6 with software 00dfa75 (2026-10-13)

Candidate run fo6 (netlist `a94a948b…`, GDS `e6769703…`), validation root
`00dfa75`. Seven receipts: functional GL (29/29), SDF standard and v2 sets,
autonomous USB enumeration (low and full speed) with SDF at typ, slow and
fast at 20.834 ns, and precheck. `release_check.py` with all of them:
`RELEASE PASSED (SDF profile v2, slow target 50 MHz)`, no waiver. Run ci7, a
capped clean checkout of `00dfa75`, reproduces the netlist and the
timestamp-normalized GDS. The archive
`wirelingo-release-evidence-d916d40-fo6.tar.gz` (sha256 `51da7962…`, 400
payload files) keeps every hashed input and result, the logs, the verdict
and the source bundle; the 01ce16d + t48ba archive (strict gate failing on
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
