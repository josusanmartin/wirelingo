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

Added for item 092 (the Astra review's findings 2 and 3). `release_check.py`
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
Measured on runner1 (2026-11-13; the clock-by-clock cases by the item 092
verifier, on their own three-flop design): with no exception a two-clock
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

**Status: a release candidate, pending the final clean harden of the
shipping commit.** Nothing here is silicon or FPGA; the levels are STA, gate
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
on regenerating the configs, which needs that environment (verifier item
100, finding 1). Records:
[evidence/8x4-release-candidate-20261115/](../evidence/8x4-release-candidate-20261115/README.md).

The rehearsal cannot release the shipping commit: its tree is not the
shipping tree, and its clean-tree receipt predates the gate's current
format. The release takes its own run.

**The release run.** The release harden certifies one commit, the shipping
commit `S`. The procedure needs `flow/harden.sh` from `e8ac948` (item 100d)
or later, which runs LibreLane unbuffered so that `exp.log` keeps the
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
2. `S/flow/release_evidence.py RUN --validation-root S --slow-mhz 48`
   (`S` is then also the shipping root): the receipt jobs, the fresh slow
   STA, the clock and reset checks, `flow/release_check.py` and, on a pass,
   the evidence archive `wirelingo-release-evidence-<rev>-<run>.tar.gz`;
3. `flow/sta_nonlatch.py RUN --period 20.832`, then the `release check: …`
   command that `release_evidence.py` printed in step 2, rerun with
   `--nonlatch-receipt` and the new receipt added, so the verdict prints the
   non-latch slack;
4. `formal/equiv/equiv_check.sh --mutants RUN`;
5. the acceptance rule: the netlist sha256 and the dates-zeroed GDS digest
   that `release_check.py` prints must equal the candidate's (`453d0af7…`,
   `357ea15b…`), so that the numbers in physical.md are the released
   netlist's. If they differ, the release stops for investigation.

**Where its receipts go.** `flow/release_check.py` refuses a run whose tree
differs from the shipping tree, so the release run's receipts cannot be in
`S` itself. They go into `evidence/8x4-release-<date>/` in a follow-up
commit: a summary of the `release_evidence.py` archive (verdict, receipt
list, case counts), the non-latch receipt, the equivalence output, the
netlist and dates-zeroed GDS hashes, and `S`'s commit id. This section
describes the procedure and does not quote the run's results; that folder
does.

**What may follow `S`.** Only commits that touch `evidence/` and `docs/`. For
a later commit `T`, `git diff --stat S T -- . ':!evidence' ':!docs'` must
print nothing, and `flow/build_manifest.py compare <S checkout> <T checkout>`
must print `SAME BUILD`.

**What GitHub builds.** The repository's GDS action builds from the pushed
commit, not from the release run. The workflow references
`TinyTapeout/tt-gds-action` by a branch, so its GDS is expected to match the
release run's only while that action installs tt-support-tools `d66cf17`,
LibreLane 3.1.0.dev3 (image `sha256:d109140b…`) and IHP-Open-PDK `2bbec755`,
the versions the rehearsal's release check recorded and `flow/release_pins.json`
pins. The check is the
comparison of its dates-zeroed GDS digest with the release run's. No
GitHub-built GDS of the 3-core design has been compared yet.

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
(about 55 MB) is not in git: it is to be attached as a release asset when the
repository is published.

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
