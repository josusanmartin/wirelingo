# Team integration checklist — October 13, 2026

This is a review checklist for lead/Claude integration, not permission to merge,
run another lane's tests, start a harden or use runner1. Workers keep their own
branches; lead commits reviewed changes. The host rebase onto `41fab1b` retained
prior branch commits without integrating them into main. Select necessary
production prerequisites; do not import every historical research commit.

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

## Five lanes and handoff status

The table records worker/channel and dispatch facts at this checkpoint.
**Pending** means no final accepted R13 result/commit is available here; an
intermediate passing run does not replace the final source regression.

| Lane / branch | Integration prerequisites | Relevant maintained checks / current status |
| --- | --- | --- |
| Atlas USB ACK / `team/20261011-sol-atlas` | Worker reports current main `41fab1b` helpers/firmware suffice; no bridge/architecture predecessor needed. Additive study page comes from `f9c7ebd995a69086d968275ac1a3ca0102944d3c`, retaining the restored C9 append. Other Atlas historical docs/bridge commits are optional. Final R13 host commit pending. | New `test/model/test_usb_ack.py`; existing `test_usb_autonomous.py` and `test_usb_fs.py`, including reset/enumeration. Corrected pre-fix fixture reported 29 pass/20 fail; nominal and valid 4/5/6 total-bit holds pass. Final firmware fix/regression pending. FIFO-LS companion `usb_ls_dev.pasm` is outside R13; lead reports the analogous source gap, with no new LS companion result. |
| Pulse SPI host / `team/20261011-sol-pulse` | Lead verified required branch chain `f328b02` → `74583e2` → `03923c9` → `e4dcb87` (all-mode/export, byte/tracer, packed firmware/helper, native host test). Final R13 host commit pending; these are not already in main. | Queued packed API plus new 1..32-byte preload API/firmware. `test/model/test_spi_packed.py`, new `test_spi_preloaded.py`, and focused serialized-host native test. Files/normal native command become available with Pulse prerequisites. Final API/model/native results pending. |
| Kepler UART / `team/20261011-sol-kepler` | Worker reports **main `41fab1b` only**; no historical Kepler USB/docs commit needed. Dispatch base `987e2d5`; final R13 host commit pending. | `PYTHONPATH=tools python3 -m pytest -q test/model/test_uart.py`. First focused run reported 19 pass/674266 model clocks; final full-file run pending. Standalone RX unchanged at that report; no bridge-tail rewrite implied. |
| Euclid checkout check / `team/20261011-sol-euclid` | Lead verified branch chain `38fa52b` → `fb4d90a` → `37926fe` → `8644940` → `086d81d` → `80f39b6`. Last commit is the five-file fetch/ownership predecessor `80f39b6628e09dba4584e2bca7ee38dfa3553d45`; ship it with the portability follow-up. Final R13 host commit pending; historical checker portability is not implied. | New checkout-local prepare/check entrypoint under `formal/lean/`, installed Lean 4.28/Icarus, fresh output. Final maintained command and isolated-copy regression pending. Saved C10 result: 132 positive rows, 175 RTL clocks, mutant detected at clock 29; this is historical finite evidence, not a new portability pass. |
| Forge docs/tools / `team/20261011-sol-forge` | Shipping patch (physical-default sentence, generator regression, this checklist) depends only on main `41fab1b`; final host commit pending. Separate historical FAQ/status patch starts at `aa0820a`; its fanout CLI/test/FAQ originate in `14375cabdabae4d59a93ec8fed8cef1e22ba9172`. Do not import retired prototype/provenance commits to ship the production patch. | Three-group baseline: 111 pass, reused per lead; only modified parameter cases rerun. Missing fanout group: 23 pass. Final changed parametrized generator contract: 4 pass. Zero failures/skips/deselections; fixture/software checks only, zero model/RTL/EDA clocks. |

Before integration, replace pending predecessor/commit entries with the owning
worker's final handoff and lead receipt. Confirm each reviewed patch applies
to the chosen main and contains only the declared paths. Do not treat a branch
HEAD, rebase receipt or historical experiment pass as a dependency declaration
or final acceptance.

## Commands for the selected production tree

Forge's current software regression command (all four groups) is:

```sh
PYTHONPATH=tools python3 -m pytest -q test/model/test_flow_tools.py \
  test/model/test_release_evidence.py test/model/test_release_runners.py \
  test/model/test_release_fanout_audit.py
python3 flow/gen_config.py --preset submission --check
```

The fanout diagnostic suite is available with Forge's separately reviewed
`14375ca` tool; skip its command until that prerequisite is present rather
than treating a missing file as a pass. The main-only shipping patch can use
the first three existing groups.

The R13 worker reused the already passing 111-test first three groups, ran
only the missing fanout suite, then reran the four cases in the modified test:

```sh
PYTHONPATH=tools python3 -m pytest -q \
  test/model/test_flow_tools.py::test_submission_clock_defaults_and_overrides_keep_geometry
```

Those tests create temporary synthetic candidates and fake make/precheck
processes. They exercise real runner/parser software and rejection paths but
launch no Docker, SSH, compiler, OpenROAD or physical tool. No nonlocal case
needed deselection. Historical private-fixture Tcl mocks are not part of this
four-group command and are not native integration evidence.

For Atlas, after its reviewed new test is available, relevant commands are:

```sh
PYTHONPATH=tools python3 -m pytest -q test/model/test_usb_ack.py
PYTHONPATH=tools python3 -m pytest -q test/model/test_usb_autonomous.py \
  test/model/test_usb_fs.py
```

Use Atlas's final documented selectors/budget when running these model cases;
Forge did not run them. Kepler's maintained command is in the table. Pulse's
final native invocation and Euclid's checkout command remain pending; copy
those from their accepted docs rather than inventing an environment or
historical absolute-path requirement.

When a patch touches a newer protocol, select its existing corresponding
regression: `test/model/test_dmx512.py`, `test_lin.py`, `test_nec.py`,
`test_i2s.py`, `test_wiegand.py`, `test_dshot.py` or `test_dshot_bidir.py`.
These files are present in current main; no R13 Forge pass is claimed for them.
The reusable top-level commands are documented in [test/README.md](../test/README.md).
This list is not a request for a broad all-protocol replay.

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
