# Team integration checklist

## Current review delivery — October 14, 2026

The dated `shipping1913` dependency table below is historical. The later
review delivery is `2d08f141fa0c61ef2117fd7f07710eac636d1072` on
`team/20261014-overlap4-service`, based on main
`34de7a06a884e99976be1e99d589e4d63d267a8a`. Its complete apply order is in the
[October 14 handoff]([home directory redacted]/wirelingo-team-20261011/control/resume-20261013/integration-20261014-handoff.md).
Use that sequence for this delivery; do not reapply the older table's commits
to a main tree that already contains them. This note is a documentation-only
successor and changes no tested inputs.

The current evidence closes several gaps left open in the historical table:

- Exact `85991cdd` passed the full software/model suite: 1,194 tests across
  52 modules, no failures, errors, skips, xfails or deselection. The separate
  complete-service test added in `6052028` passed once on the same runtime
  inputs. There is no combined 1,195-test result for `2d08f141`.
- [UART serialized-host capture](uart-capture-host.md) passed the later
  C16 two-message RTL schedule. The earlier C14/C15 failures remain history;
  the pass does not establish continuous capture or concurrent core 1 use.
- [UART IN1/partial-PUSH verification](lean-verification.md) now includes
  13 audited manual lemmas and ten positive finite RTL streams plus an
  actual mutant, totaling 9,101 clocks. This does not enlarge the earlier
  IN8 result into a whole-core proof.
- [Concurrent UART-to-USB forwarding](usb-uart-overlap4.md) has separate
  finite model evidence for retention, retry and eight complete reads.
  Idle one-to-three-byte tails, arbitrary host blackouts and native/board
  operation remain outside that fixed-four-byte profile's qualification.

The [review record](review.md) connects the later ACK and tracer fixes to
their maintained tests. None of these updates promotes main, changes fo6's
receipt identity, or authorizes a release. The saved results below remain
bound to their original snapshots.

## Historical dependency snapshot — October 13, 2026

This is a review checklist for lead/Claude integration, not permission to merge,
run another lane's tests, start a harden or use runner1. Workers keep their own
branches; lead commits reviewed changes. The accepted dependency snapshot below
is dated **October 13, 2026, shipping1913**. Its combined Git tree is
`f8772208d86e21ca1cec573bd33b353b8dbb5b72`; it is not main integration.
Select the declared production prerequisites, not every worker ancestor.

## Release and source baseline

- Preserve current main's post-freeze DMX512, LIN, NEC, I2S, Wiegand and DShot
  programs and USB bus-reset fixes. A stale firmware copy is not the baseline.
- fo6 with validation root `d916d40` and tag `rc-d916d40-fo6` passed the strict
  release gate without a waiver, with seven receipts. ci7 reproduced the
  netlist and timestamp-normalized GDS. See [physical notes](physical.md) and
  [release evidence](release-evidence.md#recorded-release-fo6-with-software-d916d40-2026-10-13).
- t48ba's 11/8 fanout failure and October 11 prototype/provenance work are
  historical. The retired producer ticket stopped after 14 metadata/source
  GETs, with no signature/parser checks or archive download. It is not a
  current release dependency. Retain saved failures and proposal-only claims.
- Physical receipts validate their named software tree, not every later
  firmware change. Model tests and fixture-based runner tests are different
  evidence from RTL, GL/SDF, routed signoff or board measurements.

## Five lanes — accepted shipping1913 dependency snapshot

Pinned main base: `0ebe6caac4e76ca2a648356543d0d7460574546d`.
The [lead handoff]([home directory redacted]/wirelingo-team-20261011/control/resume-20261013/shipping-20261013-1913/HANDOFF.md)
records the full commit identities, reviewed patches and saved results.
**Numbers below are the global apply order of its 24 commits**; apply in
increasing numerical order, including the interleaved lanes. No unselected
historical worker ancestor is a prerequisite of this snapshot.

| Lane | Selected commits in global apply order | Accepted evidence and limits |
| --- | --- | --- |
| Atlas USB / Forge bundle and driver fixture | **1** `b6fdac6` → **18** `1d62eb7` → **23** `f1a313d` → **24** `5dde2a4` | USB source fixes and matching bundle reviewed/committed. Saved ACK corpus: 125 passes/1639116 model steps; two maintained driver/loader nodes: 2 passes/554241 steps, actual 2 cores/2 banks/256 words/depth8, core0/bank0 only, LS+FS loading-host idle. Earlier eight-test driver result uses default 4/4 and remains separate. No native/physical USB or concurrent core1 acceptance. |
| Kepler UART | **2** `4519e2f` → **3** `80a28e5` → **22** `82ba9b2` | Timing regression, packed UART, then declared-length capture/helper reviewed/committed. Saved capture result: 208 passes/1662635 model steps, 19 words/76 bytes, N1..32, final STOP-sample/HALT/framing gates. No serialized-host/native UART or board acceptance. |
| Pulse SPI | **13** `f328b02` → **14** `74583e2` → **15** `03923c9` → **16** `e4dcb87` → **17** `8f4df47` → **20** `416edf0` → **21** `7cff7d7` | All-mode export, byte scheduling/trace, queued packed API, serialized-host comparison, preload API, fixed-wait correction and measured outcome reviewed/committed. Saved native core0/mode0/div3/depth8, two 32B resident frames: 11712 paid clocks each, 288 margin to 12000; cold load 8734.5 clocks separate. Prior startup/deadline failures retained; no other-mode/length/pad/PPA claim. |
| Euclid Lean/finite comparison | **5** `38fa52b` → **6** `fb4d90a` → **7** `37926fe` → **8** `8644940` → **9** `086d81d` → **10** `80f39b6` → **11** `28fa7d9` → **12** `dfee155` → **19** `9620901` | Finite check chain and current guide reviewed/committed. Saved normally fetched ownership: 132 positive rows/175 aggregate RTL clocks; RX-autopush: 192 positive rows/249 aggregate clocks, actual mutants detected. Manual Lean proofs and finite RTL comparisons are distinct; IN8 does not prove UART IN1 or arbitrary programs. |
| Forge production docs/tools tests | **4** `ef192a3` | Physical-default correction and generator regression reviewed/committed. Saved three-group software baseline: 111 passes; changed generator parameter cases: 4 passes. Fixture/software evidence, zero model/RTL/EDA clocks. This checklist correction is a later docs-only delta, not part of the 24-commit snapshot. |

Use `b6fdac6` **instead of** separate `1415771` + `2f4087c`; apply successor
`1d62eb7` after it **instead of** separate `8278342` + regenerated bundle patch.
Never apply both component and packaged alternatives. The fixture commits
`f1a313d` and `5dde2a4` follow the selected final USB source/bundle, even though
their isolated branch parents differ. No historical Atlas bridge chain is needed.

Lead's saved isolated-index check applied all 24 commits and a fresh export
passed `pe.mpy_bundle --check`: 75 files, no src/, flow/ or FPGA changes.
That apply/bundle check adds zero model/RTL clocks and does **not** replace
still-unrun final combined-tree dynamic regression or Claude/main integration.
The [checklist source review]([home directory redacted]/wirelingo-team-20261011/control/resume-20261013/integration-checklist-current-review.json)
identified this document's stale pending table and absent default fanout test.

The later C14 UART serial attempt is **outside this selected snapshot** and
failed at time0 before any RTL clock, host transaction or capture, on unresolved
initial UI observation. Keep its failed record; it provides no measured UART
cost, native capture, service/deadline or board acceptance. A follow-up requires
its own accepted scope, not an inferred pass from SPI or UART model results.

## Maintained command references for the selected tree

These are source-qualified commands from the named selected tree, **not new
execution authorization or combined-tree pass receipts**. Run from a checkout
of the selected inputs only after lead assigns ownership, dependencies and caps.
Forge's default software command contains the three existing groups:

```sh
PYTHONPATH=tools python3 -m pytest -q test/model/test_flow_tools.py \
  test/model/test_release_evidence.py test/model/test_release_runners.py
python3 flow/gen_config.py --preset submission --check
```

The existing focused generator contract is
`test/model/test_flow_tools.py::test_submission_clock_defaults_and_overrides_keep_geometry`.
These software tests use temporary fixtures/mocked processes, not Docker/SSH/EDA.
Historical `test_release_fanout_audit.py` is **absent** from the selected tree;
its tool/test prerequisite is unselected `14375cabdabae4d59a93ec8fed8cef1e22ba9172`.
It is optional historical evidence, not part of the default command or a reason
to import retired prototype/provenance work. A missing suite is not a pass.

Maintained UART model commands, after the selected UART chain:

```sh
PYTHONPATH=tools python3 -m pytest -q test/model/test_uart.py \
  test/model/test_uart_packed.py test/model/test_uart_capture.py
```

See [UART timing/capture contracts](uart-rx-timing.md) for finite baud/phase,
framing, normal rearm, under-supply and host-service boundaries. This combined
command was not executed by this docs ticket; saved per-candidate results do
not establish a new whole-tree result.

Maintained USB model references, after the packaged USB and fixture changes:

```sh
PYTHONPATH=tools python3 -m pytest -q test/model/test_usb_ack.py
PYTHONPATH=tools python3 -m pytest -q \
  test/model/test_micropython_driver.py::test_bringup_usb_enum_enumerates_alone \
  test/model/test_micropython_driver.py::test_bundle_usb_enum_matches_the_loader
PYTHONPATH=tools python3 -m pytest -q test/model/test_usb_autonomous.py \
  test/model/test_usb_fs.py
```

Only the ACK corpus and two driver nodes above have the cited new 2/2 fixture
results. Default geometry in other suites is not silently overridden. Tests use
software transport/cycle models; retain loading-host idle guards and ordinary
loader equality. Read [USB enumeration](usb-enumeration.md) and
[full-speed limits](usb-full-speed.md); no native or board pass follows.

Maintained SPI model command and separately gated native reference:

```sh
PYTHONPATH=tools python3 -m pytest -q test/model/test_spi_preloaded.py \
  test/model/test_spi_packed.py
python3 test/test_spi_preloaded_host.py --output /tmp/spi-preloaded-fresh
```

The native command needs separately qualified installed cocotb/Icarus/Python
and a fresh/empty isolated output, with lead-owned execution/caps. See
[SPI host contract](spi-packed-host.md) for exact core0/mode0/div3, synchronized
MISO16, host SCK8 system clocks, fixed3120-clock wait and two-message limits.
A displayed command or preflight is not an executed native result.

Maintained Lean/fetched-RTL references, using **already installed** executable
paths substituted for placeholders (Lean4.28.0, Icarus/vvp, strace):

```sh
python3 formal/lean/run_fetch_ownership.py --output /tmp/wirelingo-ownership-check \
  --lean /path/to/lean4.28.0/bin/lean --iverilog /path/to/iverilog \
  --vvp /path/to/vvp --strace /path/to/strace
python3 formal/lean/check_rtl_rx_autopush.py --phase all \
  --output /tmp/wirelingo-rx-autopush-check \
  --lean /path/to/lean4.28.0/bin/lean --iverilog /path/to/iverilog \
  --vvp /path/to/vvp --strace /path/to/strace
```

Each output must not exist, even if empty; parent must exist, outside source
and Lean library. No elan shim, download, Lake or stale campaign objects.
[Current Lean guide](lean-verification.md) states installed library/tool identity,
receipt gates, manual theorem domains and finite/mutant limits; its
[history](lean-verification-history.md) retains earlier failures. Lead coordinates
native ownership/caps separately. This docs correction runs no Lean/RTL tools.

For changes to other current protocols, select the existing corresponding
regression: `test/model/test_dmx512.py`, `test/model/test_lin.py`,
`test/model/test_nec.py`, `test/model/test_i2s.py`, `test/model/test_wiegand.py`,
`test/model/test_dshot.py` or `test/model/test_dshot_bidir.py`.
See [test commands](../test/README.md). Presence does not claim a Forge pass or
authorize a broad replay. Combined dynamic regression remains explicitly pending.

## What needs fresh physical work

Docs, test fixtures, Python host tooling and existing-ISA firmware changes do
not themselves require re-hardening **if RTL, hardware profile, physical-flow
configuration, SDC, macro placement/PDN and tool/PDK inputs stay identical**.
They do require the owning software/protocol regressions; firmware changes
may also require fresh candidate-bound GL/SDF receipts for a shipping software
validation root. Lead coordinates any such heavier validation separately.

A change to those hardware/physical inputs needs a measured case, named owner,
lead-coordinated re-harden and fresh strict signoff/release receipts. Do not
reuse fo6 identity or assert physical equivalence after an input change.
Neither category grants permission for a worker physical run, merge, push,
tag, waiver or board access. Keep board/pad/USB electrical and eventual
silicon claims separate from software and archived physical evidence.
