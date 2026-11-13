# Astra review validation

Review base: `fb4228d367a6528817ce6f370cf7ff77648e5ca0` (`next-8x4`).
Functional/RTL validation tip: `40ab698`; final full model suite: `0a5a7c4`
(the intervening commit changes documentation). The later evidence commit
adds and indexes these records. These are author-side results awaiting the
independent verifier. No physical signoff, gate-level simulation, FPGA or
silicon measurement was performed.

Completed validation runs used runner1, under `nice -n 10`, sequentially, in
`~/protoemu-flow/astra-review/`. Existing tools and the test virtualenv were
read/executed; no tool installation was performed. The model run ignored
`test/model/test_swd.py` and deselected the general firmware-inventory test
that would otherwise open the parked SWD firmware. Generic privacy scans
still scanned tracked bytes.

| Record | Tip and scope | Result |
|---|---|---|
| [usb-before.txt](usb-before.txt) | New USB rejection regression with original `fb4228d` firmware substituted in the `20186e4` scratch checkout | 8 failed, 8 passed |
| [usb-after.txt](usb-after.txt) | `20186e4`, USB rejection/autonomy/EP1/timing/MicroPython tests | 145 passed, 2 sigrok skips |
| [tools-before.txt](tools-before.txt) | New tests at `2501b7a` with original Python assembler and minimum-signoff checker substituted | 72 failed, 43 passed; 56 signoff and 16 directive cases fail |
| [tools-after.txt](tools-after.txt) | `2501b7a`, assembler/flow/signoff tests | 147 passed |
| [formal-matrix.txt](formal-matrix.txt) | `215b69e`, indices 0/1/2 at FIFO depths 4/8 | Six proofs PASS |
| [mutants.txt](mutants.txt) | `2501b7a`, core mutation harness | All 16 planted faults caught |
| [ocaml-asm.txt](ocaml-asm.txt) | `2501b7a`, Python/OCaml assembler parity, SWD excluded | Zero disagreements, 2,000 fuzz cases, 247,080 binary bytes |
| [intermediate-model.txt](intermediate-model.txt) | `fdf67c8`, full model run before the image-size test update | 3 failed, 3,252 passed, 29 skipped; three stale 253-word expectations |
| [usb-integration.txt](usb-integration.txt) | `40ab698`, updated reset/coexistence tests | 4 passed |
| [formal-diagnostics.txt](formal-diagnostics.txt) | `40ab698`, default formal gate, handshake sensitivity and model diagnostics | 14 proof/cover PASS; old harness accepts two faults that new harness rejects; EP1 retry defect reproduced on the two-core model at both speeds; divider tick sequence confirmed |
| [rtl.txt](rtl.txt) | `40ab698`, SRAM geometries and added CI modules | 108/108 PASS: 8 lockstep + 30 top-level per geometry, then 17 blind + 6 RMII + 7 Ethernet stack + 2 Ethernet reply; 11 documentation/privacy tests PASS; firmware bundle matches |
| [final-model.txt](final-model.txt) | `0a5a7c4`, full model suite | 3,255 passed, 29 skipped, 854.69 s |
| [record-before-final.txt](record-before-final.txt) | `0a5a7c4`, prescribed tree/history scan before the evidence commit | 979 files, 10 commit messages, 0 flagged |

The before runs substituted the original implementation files into an isolated
scratch clone containing the new tests, then restored the committed files.
For the assembler, 15 rejection cases expose invalid acceptance/error handling;
`.side_set 5` was already rejected but now has the expected diagnostic. Four
valid boundary cases pass before and after. Minimum-signoff fixtures do not
measure a routed design. The parity log's matching exception cases are
malformed inputs, not disagreements.

Reproduction commands (from a scratch clone; set `TMPDIR` inside it and
`PYTHONDONTWRITEBYTECODE=1`):

```sh
PYTHONPATH=tools nice -n 10 python -m pytest -q -p no:cacheprovider -n 4 --ignore=test/model/test_swd.py --deselect=test/model/test_v03_isa.py::test_v02_firmware_uses_no_v03_encoding test/model
PYTHONPATH=tools nice -n 10 python -m pytest -q -p no:cacheprovider --ignore=test/model/test_swd.py test/model/test_usb_config_reject.py test/model/test_usb_enum_ep1.py test/model/test_usb_autonomous.py test/model/test_timing_check.py test/model/test_micropython_driver.py
PYTHONPATH=tools nice -n 10 python -m pytest -q -p no:cacheprovider --ignore=test/model/test_swd.py test/model/test_asm.py test/model/test_flow_tools.py test/model/test_signoff_metrics.py
nice -n 10 make -C formal
nice -n 10 bash formal/core_mutants.sh
```

[checks.sh](checks.sh) records the final formal, before/after handshake-mutant,
raw EP1 retry/divider diagnostic, two-geometry RTL and documentation commands.
Run it with `bash` from a clone inside the assigned scratch directory. Its EP1
assertion deliberately confirms the unresolved retry defect; it is not a USB
conformance pass. It uses seed 20261112 for 40 random lockstep programs of
1,000 clocks at each geometry, in addition to the directed cases.

OCaml parity used the existing compiler switch, a local scratch link for GMP,
`DUNE_CACHE=disabled`, `dune build bin/wl_asm.exe` in `ocaml/`, then
`nice -n 10 python3 ocaml/test/asm_parity.py --fuzz 2000` from the repository
root (default seed 1). `TMPDIR` and the link directory were inside the scratch
clone. No Hardcaml implementation changed, so its RTL equivalence was not
rerun; the existing v0.3 port evidence is separately indexed.

The initial RTL launch stopped before tests: a test-virtualenv-first PATH
mixed Python 3.12 with the OSS simulator wrapper's Python home. The rerun
uses the installed helper's OSS-tool-first PATH, with the model virtualenv
selected explicitly for the diagnostic. This was a runner setup error.

Raw outputs were shortened to summaries for repository size; line-end
whitespace was stripped. User home paths,
addresses and private machine names are redacted where present; runner1 and
relative scratch paths remain. No assertion, result count or failure was
redacted. The intermediate failure is retained rather than presented as a pass.
The two interrupted full-suite attempts are described in the external review;
they are not full-suite results. The initial attempt may have used pytest's
default temporary directory outside the assigned scratch before interruption;
no paths outside the assigned scratch were inspected or cleaned.
