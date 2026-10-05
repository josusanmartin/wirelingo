# Review fixes and team handoff

Implementation started 2026-10-04 from `691ff8a5248954774e4534bda6988e8fe32d2df7`
on `fixed-bank`. The review header's `main` branch label was incorrect.

This file is the coordination ledger. Before editing an area, claim its paths
here; do not reset another contributor's changes or reuse another contributor's
simulation or hardening output directory. A completed code fix and a completed
physical/hardware validation are separate statuses.

| Area | Paths | Owner | Status |
|---|---|---|---|
| Host validation and ownership (R6/R7) | `tools/pe/{host,asm,export,mpy_bundle,profile}.py`, `tools/micropython/`, driver/model regressions | Codex | Implemented; regressions pass |
| Evidence and CI (C1/C3/V1/V4) | `flow/`, `.github/workflows/`, tooling regressions | Codex | Implemented; config/bundle/tooling/mutation checks pass |
| SPI reset/CDC (R2/R3/V2) | `src/pe_hostport.sv`, SDCs, host-port tests/formal | Codex | Digital checks pass; routed timing pending |
| USB (R4/R5/V2) | USB firmware, `tools/pe/usbdev.py`, USB tests | Codex | Implemented; model, complete RTL suite and extended error/retry cases pass |
| Ethernet streaming (V3) | Ethernet tests and demos | Codex | Streaming/overflow/recovery RTL test passes |
| Claims and submission documentation (C2/V4) | README, docs, `info.yaml` | Codex | Updated; PIO comparison and generated-demo measurements included |
| Exact-candidate physical signoff (R1/R2/V4) | `../protoemu-exp-reviewfixes-20261004`, candidate manifest | Codex | Running; no release signoff claim |
| Detailed timing diagnostics (R2/V4) | `flow/{timing_review,pulse_width_review}.tcl`, `test/sta/` | Codex | Three-corner placement reports and native positive/negative controls complete; final extraction pending |
| Repaired-checkpoint timing and CTS probe | `test/sim_build/review_repair43_timing.cn5hyire/`, `test/sim_build/review_cts_fanout.5whqdnhd/` only | Codex | Fresh step-43 analysis fails ordinary/electrical checks; one CTS probe reduces clock-fanout violations from 79 to 4; budget closed, no production promotion |
| Clock branch splitting probe | `test/sim_build/review_clock_branch.gm75haj5/` only | Codex | Exact emitted-netlist/database connectivity and power checks pass; eight added buffers remove the four remaining clock-fanout violations in all corner placement reports; full physical validation pending |
| Reusable clock repair and physical-input provenance | `librelane_plugin_protoemu.py`, `flow/{clock_fanout.tcl,clock_fanout_repair.tcl,manifest.py,gen_config.py,harden.sh,signoff.py}`, tooling regressions | Codex | Implemented; 32 tooling cases, native topology/power/no-op/rejection checks and six corner analyses complete. Clock fanout passes; remaining timing/electrical checks fail. Original live worktree is frozen; no new hardening or routing sweep |
| FPGA profile (V4) | `flow/fpga_{profile,validate}.py`, FPGA workflows/tests | Codex | Full-geometry lockstep passes; ECP5 fits but fails 48-MHz timing |
| FPGA pin-path prototype | `test/sim_build/review_fpga_pin_paths.brd0tt23/` only | Codex | Closed without promotion: pin SAT and 4/4 lockstep pass; routed 27.278 MHz fails target and previous best |
| FPGA threshold-tail prototype | `test/sim_build/review_fpga_threshold_tail.znyeuxwu/` only | Codex | Closed without promotion: count SAT and 4/4 lockstep pass; 27.970 MHz still below protected best and target |
| FPGA instruction-memory primitive probe | `test/sim_build/review_fpga_lutram.jst94wo1/` only | Codex | Primitive result retained at 30.490 MHz; mapped-memory tests pass; two follow-up attempts completed; no integration |
| FPGA LUT-RAM threshold follow-up | `test/sim_build/review_fpga_lut_threshold.wun97l_g/` only | Codex | SAT and 4/4 full-profile lockstep pass; new experimental best 31.821 MHz still fails 48 MHz; no default/ASIC integration |
| FPGA parallel-read follow-up | `test/sim_build/review_fpga_parallel_read.uoat5y0g/` only | Codex | Address SAT, 4/4 full-profile lockstep and 2032 mapped-memory cycles pass; full route fits but reaches 28.749 MHz; closed without integration |
| Common-clock Ethernet receiver prototypes | `test/sim_build/review_eth40_dual_edge.af1a0ic5/` only | Codex | Closed without integration: selecting a half sample fixes the uniform-jitter probe, but both candidates fail jitter-limit waveforms |
| Optional PIO/DMA transport (V4) | `tools/micropython/{quad_spi,pio_quad,transport_bench}.py`, `test/{compile_pio,pio_support,test_pio}.py`, PIO fixture/tests | Codex | Implemented; host/API and RTL checks pass; board measurements pending |
| External Ethernet bench demo (V4) | `tools/micropython/eth10_rx.py`, bundle/bringup, receive tests, `docs/eth10-receiver*` | Codex | Capture implemented; 19 model, 2 RTL and 2 checkpoint GL cases pass. Proposed circuit documented; board qualification unassigned |
| RP2350/board/analog validation (V4) | Hardware evidence | Unassigned | Hardware availability not established |

## Fix record

Each completed area below will state the behavior changed, regression evidence,
and any remaining physical or board validation. Historical t5 results do not
validate this implementation or the pre-fix HEAD.

### Host ownership and fixed-bank loading (R6/R7)

`ProtoEmu` keeps a complete ownership shadow because PIN_OWNER registers are
write-only. Construct it after reset, or supply the existing complete owner
map. `set_pin_owners()` replaces/resynchronises it explicitly. A single
configuration writer must own a chip; direct PIN_OWNER writes by another
writer require resynchronisation. Repeated/mixed assignments preserve other
pins, and invalid pins/cores fail before writes.

PC loaders/exporters/bundles and the MicroPython loader reject empty images,
missing cores/banks, bank overrun, wrong bases, invalid wrap ranges and direct
JMP targets outside the bank. Relocation rejects address overflow rather than
masking it. Validation precedes any bus writes and accepts generators safely.
Bank selection stays address[9:8], even for reduced-size simulation banks.
The default named profile is `fixed-bank-v1-2c2b8f`; `FULL` explicitly selects
four cores. The legacy hardware ID does not encode geometry, so do not use ID
alone to choose a profile. Computed PC targets remain hardware runtime checks.

The UART board loopback previously queued the entire message before reading
RX. It now drains RX while feeding TX, checks core status and stops both cores
on timeout/failure. A 53-byte driver regression exceeds the FIFO capacity;
the generated 4/13-word UART images also pass a two-core RTL SPI loopback with
a 187.5-us service stall. These are digital/driver results, not board captures.

### SPI reset and CDC (R2/R3/V2)

Global reset resets parser/output state and aborts pending transactions.
`host_ready` requires an idle CS interval; selection held across reset cannot
resume a partial command. Writes additionally require an active write
transaction. Reset-phase tests cover stopped SCK and read-ahead aborts, with
fresh transfers checked across all available cores. Fast tests now cover
0.5-clk minimum high/low times through 25/75% duty at SCK=clk/2.

Synchronizer signals carry preservation attributes. SDC no longer cuts all
cross-clock paths: OpenSTA `set_max_delay -ignore_clock_latency` bounds both
directions to one minimum clk period; only unrelated cross-clock hold checks
are false paths. These supported commands were checked in the installed
OpenSTA 2.7.0. Cross-clock reports show actual constrained paths in the new
run's pre-route STA. Routed margins and synchronizer resolution remain gates.

Address/mode holds are consumed after event synchronization plus pending and
activation cycles; write data after synchronization and a pending cycle. Each
write hold lasts at least eight SCK. Initial read buffers are ready after the
address event, activation, snapshot and capture, before eight dummy SCK end;
buffer reuse alternates across eight-SCK quad words. The one-period routed
data bound is below these stability windows. Output setup depends on the
half-SCK pad/board/host window. Preserve the five-clk idle-CS reset contract.
Metastability reliability needs process characterization and routed stage
resolution time; digital proofs do not establish it.

### Detailed timing coverage (R2/V4)

`flow/timing_review.tcl` supplements the normal candidate timing constraints.
It resolves the 20 synchronizer groups through synthesis keeper inversions to
the actual register clock pins, reports 21 stage paths, and fails on missing or
ambiguous targets. It explicitly reports 53 IHP clock-gate enable pins: their
Liberty arcs are ordinary setup/hold checks, so an empty
`-clock_gating_setup/-clock_gating_hold` report alone misses this coverage.
It also reports both delay directions to all 1024 FIFO latch data pins.
Zero latch setup slack can be a valid borrowing result; inspect the retained
window/borrowing details rather than treating that number as unused margin.

The installed OpenSTA 2.7 image returns no pulse-width check rows with the
candidate's cross-clock max-delay and hold exceptions. Controlled runs on the
same database and libraries show numerical checks after omitting exactly
those four SDC lines. The native single-flop controls in `test/sta/` print
violations at a 0.01-ns period and passing margins at 20 ns. An empty candidate
report must therefore not be counted as verified pulse-width coverage.

`flow/pulse_width_review.tcl` is a **separate clock-only diagnostic**, never the
regular flow's extra-corner hook. Start a fresh STA process with a derived SDC
that omits only the two cross-clock maxima and two hold cuts. All other SDC
content, libraries, clock trees and parasitics stay identical. The helper
checks the original/derived contents and requires explicit opt-in. Native
negative controls reject missing opt-in and an incorrect derived SDC before
creating a report. Its setup/hold/CDC results are not release evidence; those
checks must use the complete candidate SDC.

Fresh post-CTS **placement estimates**, on the completed step-39 database:

| Report | Coverage per corner | Worst observed margin across three corners |
|---|---|---|
| CDC | 313 reported paths | +9.397983 ns |
| Synchronizer stages | 20 groups / 21 reported paths | +9.134532 ns setup slack; not an MTBF measurement |
| Clock-gate enables | 53 pins / 106 setup/hold paths | +0.219097 ns |
| FIFO latches | 1024 data pins / 2048 setup/hold paths | 0 ns with borrowing details retained |
| Separate clock-only pulse widths | 2693 target pins with numeric checks / 4360 checks plus one repeated worst-check row | +9.594543 ns |

The latest full reports, including reset-stage coverage, are in
`test/sim_build/review_timing_with_reset_checkpoint.097wv3fi/`; earlier
19-group reports remain in `test/sim_build/review_timing_checkpoint_final/`.
The separate pulse reports and pin-coverage lists are in
`test/sim_build/review_clock_pulse_checkpoint/`. These are not final extracted
signoff, and positive margins do not establish process metastability behavior
or board timing. Repeat both analyses with final extracted parasitics before
creating detailed release receipts. Respect which pulse edges the selected
Liberty view actually characterizes.

A separate native check also covers the top-level `rst_s[0]` to `rst_s[1]`
reset synchronizer, which the initial 19-group helper omitted. On the same
step-39 placement database it reports one path per corner, with setup slack
19.075979 ns typical, 18.669258 ns slow and 19.301081 ns fast. Retained inputs,
logs and hashes are in
`test/sim_build/review_reset_stage_checkpoint.qn5iwsid/manifest.json`.
These are placement diagnostics, not MTBF or final extraction evidence.
The complete helper addition also passes a fresh three-corner run with 20
groups and 21 paths. It was promoted only after checkpoint GL finished and
verified its source snapshot. The current helper byte-matches the executed
copy in `review_timing_with_reset_checkpoint.097wv3fi/flow/`.

Fresh ordinary timing on the completed **step-41 global-routing estimates**
fails before the live post-routing repair step. Each diagnostic loads only its
selected library corner and explicitly supplies that corner to path searches.
These values are independent of inherited aggregate flow metrics:

| Corner | Setup WS | Hold WS | Negative setup endpoints | Electrical violation rows |
|---|---|---|---|---|
| Typical | 0 ns (latch borrowing) | -0.180928 ns | 0 | 148 |
| Slow | -1.345594 ns | -0.107074 ns | 120 | 148 |
| Fast | 0 ns (latch borrowing) | -0.214631 ns | 0 | 147 |

The worst slow setup path is bank 0 SRAM `A_DOUT[3]` to `_35979_/D`, whose
mapped state is core 0 `isr[31]`. The data path includes 55 distinct inserted
`rebuffer` cells, with 7.017110 ns of cell arc delay in this estimate; this sum
excludes their wires and other cells. Inspect the final path and repair effects
before attributing the failure to the core's intrinsic logic depth.
Electrical rows comprise six slew violations at typical/slow (five at fast),
three capacitance violations and 139 fanout violations at each corner.
They are not counts of distinct pins. Inputs, full reports, the mapped-path
analysis and a failed
checkpoint receipt are retained under
`test/sim_build/review_ordinary_grt_checkpoint.qx20b460/`.
The copied database contains global routes, so the diagnostic uses
`estimate_parasitics -global_routing`. It is not a final extracted analysis.
Repair, detailed routing and final corner checks must resolve these failures
before release; the earlier positive CDC/stage/gate/pulse checks cannot replace
ordinary setup, hold or electrical checks.

### USB correctness and portable service (R4/R5/V2)

Both variants compare the full SYNC+DATA0/DATA1 PID, including complement bits;
SETUP data must use DATA0. Invalid packets are not ACKed. The pure-firmware RX
also detects a seventh consecutive one and emits a failed partial record so
retries can resynchronise. A countdown/borrow check preserves the timing
budget, with ±2.5% receive-rate regressions.

USB record ABI v2 adds reversed PID in trailer[15:8] and SETUP association in
bit 1, preserving marker bit 7 and status bit 0. Software suppresses duplicate
OUT toggles, uses record metadata instead of a racy live SETUP flag, rereads
flags after draining, and waits 100 us from observed send start before
restaging an unacknowledged <=8-byte packet. SETUP aborts a staged transfer.
Firmware and host service must be updated together.

Sustained SE0 raises F_RESET after 120 clocks at 48 MHz (normal EOP is 64).
Both lines must be low: a sustained K state does not reset endpoint zero.
Software stops the USB cores, clears FIFOs/queues/flags and control state,
restores filter address zero, releases USB pins, then waits for J to restart.
Models exercise ten-ms reset plus recovery at address five and with pending
transfers. Concurrent RTL SPI service tests both firmware variants, invalid
PID retry, descriptor chunks/toggles, lost ACK, new SETUP and reset.

DeviceSoftware now requires only a read/write/push/pop_all/now_us transport.
`pe.mpy_bundle` emits firmware and the same portable service as
`usb_device.py`/`usb_bits.py`, with a read-only `--check`. `bringup.usb_setup()`
provides a board entrypoint. Descriptors now advertise only the implemented
vendor endpoint-zero interface, without HID endpoint/string claims. Board
enumeration and sustained transport performance remain unmeasured.

### Verification integrity, configuration and CI (C1/C3/V1/V4)

The submission preset uses all PVT corners and swapped bottom macros.
Signoff (`SIGNOFF_SDC_FILE = protoemu.sdc`) times the real latch arcs; PnR
(`protoemu_pnr.sdc`) times the FIFO latches like flops (see "PnR latch timing"
below; the first version of this preset forbade it). Generator arguments are in the JSON; `--check`
never writes. GDS CI checks the same preset, and test CI checks generated
board bundles. The duplicate formal workflow is consolidated into test CI.

GL uses private netlist/build/log/XML paths, propagates make failure, rejects
missing XML and writes a passing netlist/test/XML hash receipt only afterward.
Tooling regressions include stale XML with compilation/startup failures and
strict release-gate rejection of stale or missing timing/GL evidence.
Waveform paths now follow the private simulation build directory.

Mutation testing records a passing baseline for each target command. Only
fresh XML containing an executed test failure counts as killed; compilation,
import/collection errors and missing results are infrastructure failures.
One immutable snapshot supplies every baseline and mutant, includes new
untracked source files, detects racing edits, and preserves baseline/failure logs.
There are 17 definitions; the actual run determines the killed count.

`manifest.py` records exact source/hardware/config/SDC hashes, generator
arguments, PDK file hashes, LibreLane image identity and TT tool revision.
Schema 2 includes every root `librelane_plugin_*.py` in source capture and
snapshot copying. It separately binds flow/plugin implementation bytes. For
hardening, capture occurs after TT configuration generation and also binds
`src/{user_config,config_merged}.json` and the actual support-tool files.
`harden.sh` refuses run-name reuse and silent dirty-tree omission; SNAPSHOT=1
captures actual team files and detects edits during copying. `signoff.py`
separates historical summaries from a strict release gate that rejects
missing/violated metrics, wrong SRAM views, changed hardware, unbound or changed
physical flow/plugin/TT configuration inputs, absent GDS and
missing detailed timing/GL receipts. Receipt references must be files within
the run with matching full SHA-256 hashes. Functional GL must also bind to the
same netlist and candidate input manifest. A flow exit alone is insufficient.

### Optional RP2350 transport (V4)

`ProtoEmu` can delegate read/write/legacy transfers to an explicitly attached
PIO/DMA backend. The 14-instruction program keeps command bits serial,
address/data quad and eight read-dummy clocks. RX completion includes a marker
after the final falling edge and hold interval, so TX DMA completion alone
cannot release CS early. Final TX padding is discarded before the next control
word. Bursts are bounded and validated before selection; timeout/error cleanup
aborts both DMA channels before buffers are released. Partial writes require
application recovery, not an automatic retry.

The SDK adapter resolves named GPIOs and verifies clock settings. It handles
ascending/descending data groups and checks the already configured PIO GPIO
window, including the RP2350B high pins in the SDK's TTDBv3 map. It never changes
another state machine's shared block configuration. Caller-owned state-machine,
GPIO and clock resources remain an explicit team requirement. A bounded
read-only ID benchmark retains individual latency samples and labels configured
clocks separately from unmeasured clocks/margin.

The pinned official MicroPython v1.26 assembler compiles the checked PIO source;
its source/program hashes are retained in `test/pio_program.json`. Host tests
cover independent wire packing, consecutive partial/full transfers, FIFO
starvation, SDK/GPIO-window guards and DMA allocation/error paths. DMA API
checks use fakes. Compiled instruction traces drive the real ASIC SPI pins in
two RTL/checkpoint-GL tests: loaded-image execution, 32-bit FIFO data, all four
input/output pin-order combinations, bursts through 256 words, read commits,
stalls and reset/bootstrap recovery. This excludes RP2 input synchronizers,
pad delay and DMA hardware. Board throughput, electrical sampling and concurrent
service bounds remain open. See [board-transport.md](board-transport.md).

### Ethernet and hardware boundaries (V3/V4)

The new RTL streaming test sends valid 64, 128 and 1518-byte frames including
FCS, with minimum 96-bit gaps, jitter, concurrent quad SPI draining and bounded
four-us stalls. It forces overflow, invalidates the frame, resets the FIFO,
firmware and line unit, and verifies a clean subsequent frame. Required RX
payload throughput is 13.33 Mbit/s plus SPI/status overhead; the slow board
bit-bang driver is not a sustained Ethernet transport.

Board setup now holds reset while checking the SDK's actual generated project
clock; a mismatch fails before release or any SPI access. This uses
[`DemoBoard.auto_clocking_freq`](https://github.com/TinyTapeout/tt-micropython-firmware/blob/main/src/ttboard/demoboard.py),
which reads the active PWM frequency. FPGA staging defaults to the complete
two-core/two-bank/256-word/eight-entry tapeout geometry, using inferred RAM and
enable-flop FIFO/configuration state without modifying ASIC files. It checks
input hashes before/after copying to reject racing team edits. Smaller profiles
require explicit options. The validation tool retains failures and tool/source
identities in `validation.json`; missing reports or negative timing cannot pass.

**FPGA timing remains open:** the full two-core ECP5-25k profile routes using
13516/24288 logic cells and two block RAMs, but achieves only 24.02 MHz against
48 MHz (SCK achieves 80.35 MHz). `review_fpga_full_ecp5_final/validation.json`
records failure. The converted RTL passes 4/4 lockstep checks, 30x1000 cycles;
this does not validate the mapped RAM or board. Installed Yosys simulation
models leave DP16KD memory as a black box, so no mapped-FPGA GL success is claimed.
The explicit one-core/four-entry UP5K experiment uses 7392/5280 logic cells
and fails capacity. Neither workflow establishes a working bitstream; the
ECP5 workflow checks conversion equivalence before reporting routed timing.

The ECP5 routed critical path is 41.633 ns: 15.262 ns in logic (including
RAM clock-to-output) and 26.371 ns in routing. It starts at bank 0's DP16KD
`DOA0` and ends at the packed register named from `uio_oe`. The exact cells,
locations and delays are in `review_fpga_full_ecp5_final/route.json`.
Synthesis already uses ABC9; simply enabling ABC9 is not a new optimization.
Preserve ISA behavior and complete geometry while reducing that path's logic
depth/routing, and keep the current failed build as the comparison baseline.

Four private FPGA experiments preserve two cores, two 256-word banks,
eight-entry FIFOs, cycle behavior and the same ECP5-25k/CABGA381 48-MHz target:

| Mapping | clk achieved | Logic cells / 24288 | Decision |
|---|---|---|---|
| Protected baseline | 24.019 MHz | 13516 | Retain failed baseline |
| Parallel low-mask comparisons | 24.968 MHz | 14141 | Fails 48 MHz |
| Parallel masks, constant comparisons in LUT logic | 26.172 MHz | 13673 | Fails 48 MHz |
| Separate IN/OUT shifters, with the preceding mapping | 26.999 MHz | 14117 | Fails 48 MHz |
| Compose OUT extraction and pin rotation | 28.137 MHz | 14979 | Fails 48 MHz |

SAT proves the changed mask equations for all 64 counts, the split shifter
equations for all encoded OUT counts and all IN counts/modes/data, and the
composed OUT equations for every count/base/direction/data word. Every distinct
RTL candidate passes 4/4 full-profile lockstep tests at 30x1000 cycles; the two
mask mappings use identical RTL. This supports the changed equations and
tested converted-RTL behavior, not mapped block-RAM or board correctness.

The best observed path is still 35.541 ns: 5.609 ns RAM clock-to-output,
7.334 ns logic and 22.598 ns routing, ending in the chip's pin register.
These single deterministic mappings do not establish a timing floor.
The first three-candidate mask/shifter round and one separate composed-rotation
probe are closed; another seed or small parameter change is not an established
solution. The next attempt should target the remaining pin-write dependency
or FPGA physical mapping and retain the 48-MHz/full-geometry requirements.

A subsequent bounded physical-placement experiment is closed without
promotion. It reused the exact composed-rotation mapped design, device,
speed grade, seed and 48-MHz target. Constraining the measured path and both
RAMs produced 25.690 MHz, with four selected cells outside the requested
region. A second attempt included both observed endpoint dependency cones
and their sequential/RAM frontiers (5799 cells). Its pre-route check found
43 cells outside the declared region and stopped before routing. That is a
placement-constraint failure, not a measured routed frequency or a functional
failure. Evidence and hashes are in
`test/sim_build/review_fpga_floorplan.7llp8ucq/results.json`. The protected
baseline/prototype evidence was rehashed unchanged. Region constraints have
not established a remedy; do not promote either attempt or reopen this family
with arbitrary seeds or slightly different rectangles.

Sources, SAT inputs/logs, simulation and route evidence remain under
`test/sim_build/review_fpga_{parallel_mask,parallel_mask_softcmp,split_shift,composed_out}/`.
Each directory has `plan.json` and `experiment_result.json`;
`test/sim_build/review_fpga_experiments_results.json` binds their full hashes
and tool identities. No prototype was copied into the production converter or
ASIC sources, and the validated default remains a reported timing failure.

Three further isolated probes retain the same device, 48-MHz target, default
speed/seed and full geometry. A pin-path representation computes separate
operation masks/rotations before opcode selection. Its effective pin-write SAT
and 4/4 full-profile lockstep tests pass, but it routes at only **27.278 MHz**
(13923 logic cells). An IN-autopush follow-up precomputes the remaining threshold
from register state, removing an instruction-time sum/saturation chain. Its
all-counts SAT and 4/4 lockstep pass, but **27.970 MHz** (14623 cells) still
fails the previous best and target. Both are closed without integration;
results are in `review_fpga_pin_paths.brd0tt23/results.json` and
`review_fpga_threshold_tail.znyeuxwu/results.json` under `test/sim_build/`.

A distinct memory-primitive probe uses `-nobram` on the byte-identical protected
composed-OUT RTL. Distributed RAM preserves both 256x32 banks and registered
read behavior. A native mapped-memory test checks all 512 words, bank isolation,
read/write collisions and ignored nonexistent-bank writes across 1516 checked
cycles using the pinned official Yosys LUT-RAM/FF models. It passes. The whole
mapped chip's DSP remains a black box in those models; this test does not prove
the whole mapped chip or a board.

The primitive probe reaches **30.490 MHz**, an 8.36% gain over the previous
28.137-MHz best, using 17269/24288 logic cells and 256 distributed RAM write
sites. Its worst 32.798-ns instruction-register path has 0.525 ns clock-to-output,
7.425 ns logic and 24.848 ns routing. Removing the former 5.609-ns block-RAM
contribution is a mechanism signal, but **48 MHz still fails**. The experimental
best and 33 hashed artifacts are retained in
`test/sim_build/review_fpga_lutram.jst94wo1/results.json`; the baseline and
production converter remain unchanged. `followup_plan.json` limits further
work to two candidates/routes: the proven threshold equation on this parent,
then memory-read selection analysis only if capacity and cycle equivalence
can be established. No clock/geometry weakening or seed sweep is authorized
by that experiment plan.

Initial miter cwd/lexical errors and a duplicate unused FF/IO wrapper include
in the mapped-memory harness are retained as infrastructure failures. The
final pin proof uses the actual encoded `x[4:0]` count. `NO_INCLUDES` removes
duplicate unused wrappers while preserving the instantiated functional RAM,
LUT and FF models. None of the setup failures count as functional evidence.

The two predeclared follow-ups are now complete. Applying the remaining-threshold
equation alone to the LUT-RAM parent passes SAT for every encoded threshold,
6-bit count and autopush state, plus 4/4 full-profile lockstep (30x1000 cycles,
85.10 s). It reaches **31.821 MHz**, 4.37% above the primitive parent, using
18115/24288 logic cells, 2574 FFs and 256 RAM write sites. Its 31.426-ns path
contains 0.525 ns clock-to-output, 6.346 ns logic and 24.555 ns routing.
`review_fpga_lut_threshold.wun97l_g/results.json` retains 30 hashed artifacts.
It is the current experimental best; **48 MHz remains unmet**.

The second follow-up reads held/candidate addresses in parallel, registering
the selection alongside both words. Address-priority SAT, 4/4 full-profile
lockstep (90.81 s) and a new mapped-memory test all pass. The native memory test
checks 2032 cycles across both read ports/select modes, all 512 logical words,
stability before the edge, write collisions, bank isolation and ignored
nonexistent-bank writes. Mapping replicates each logical bank's read port:
512 DPR16X4 wrappers, rather than the parent's 256. The full design fits with
19062 logic cells, 2640 FFs and 512 RAM write sites, but reaches only
**28.749 MHz**. Its registered-select/instruction path has 8.119 ns logic and
26.140 ns routing, plus 0.525 ns clock-to-output. The added instruction mux
and routing cost outweigh this representation's intended address-path benefit.
`review_fpga_parallel_read.uoat5y0g/results.json` retains 38 hashed artifacts;
the candidate is closed without integration.

`test/sim_build/review_handoff_fpga._o1b0n7v/followup_closed.json` binds the
two results and closes the two-candidate/two-route budget. Both routes used
the unchanged full geometry, device, speed, seed and 48-MHz target. No default
converter or ASIC source changed. Any further experiment needs a distinct
mechanism and bounded plan based on the remaining routing-dominated path;
these results do not establish a timing floor or board readiness.

There is no connected `/dev/ttyACM*` or `/dev/ttyUSB*` device detected. A fresh
local `/sys/bus/usb/devices` inventory exposes only the VM's host controller;
it establishes no attached board on this host, not the state of other team
machines. A measured
RP2350 PIO/DMA transport, board/FPGA programming and building/qualifying the
proposed analog RX comparator remain unassigned hardware work. A common-clock Ethernet TX/RX
digital loopback at 50 MHz uses TX divider 320 (1.25) and RX period 5.
Its 40/60-ns transmit half cells require separate electrical validation;
no compliant simultaneous board interface is established.
A private 40-MHz alternative uses integer TX divider 256, RX period 4 and
quad SPI at 40 MHz. Its complete 64/1518-byte TX/RX loopback passes (40.15 s),
but the unchanged external RX jitter bound of +/-13.5 ns produces a decoded
payload mismatch (20.79 s). Nominal loopback alone cannot establish a general
link. The initial copied-fixture import failures occurred at time zero and
are retained separately as infrastructure errors. Plans, sources, fresh XML
and executed failures are under
`test/sim_build/review_eth40_corrected.glw2_zgn/`; the aggregate
`review_eth40_results.json` hashes 22 artifacts and records no promotion.
The existing 50-MHz tests and production profiles remain unchanged.

Two isolated receiver prototypes then qualified each half-cycle edge
separately and used the selected sample's polarity. At 40 MHz, two transitions
can occur within one project-clock interval; the original first-edge/final-
sample combination can lose an eligible edge or use the following boundary's
polarity. Selecting the first eligible edge passes the previously failing
64/128/1518/64-byte uniform-jitter SPI probe, including overflow/recovery
(23.35 s), 20 line/Ethernet model tests and four explicit polarity cases.
Its wider-arithmetic/queue formal proof also passes after updating the
independent wide check to account for the selected edge. The original assertion
failure and sources are retained, not counted as passing proof evidence.

That partial success is insufficient: moving every transition to a jitter
limit (+/-13.5 ns) exercises both edge orders and corrupts frames (25.39 s).
A bounded follow-up selecting the eligible edge nearest the predicted mid-bit
also fails (24.74 s), despite passing phase/queue formal bounds. Independent
40-MHz model cases reproduce all four extreme-jitter failures; eight uniform-
waveform/polarity cases pass. A uniform-waveform coverage run also failed to
exercise the second edge order and is explicitly recorded as a coverage
failure. This two-selector family is closed without root promotion.
`test/sim_build/review_eth40_dual_edge.af1a0ic5/results.json` binds 374 retained
source/result artifacts, including failing XML/waves. The next receiver change
needs phase-recovery analysis and the complete jitter contract; any eventual
RTL integration requires full digital verification and a new physical run.
The validated production source/hardware hashes still match the live candidate.

`docs/info.md` states digital RX polarity/voltage/threshold assumptions and
separates 40-MHz TX from 50-MHz RX; it does not claim an analog design is tested.

### Finite Ethernet receive service and bench circuit (V4)

The portable bundle now exports `ETH10_RX`. `tools/micropython/eth10_rx.py`
arms core 0/the line unit at a verified 50-MHz project setting, drains bounded
records and stops reception before decoding/checking FCS. It preserves core 1.
Returned frames require sufficient alternating preamble, SFD, supported length
and CRC. Short link pulses are counted, malformed records discarded, pending
records bounded, and overflow/timeout/core failure/clock drift raise. Cleanup
preserves the original failure if transport recovery also fails. This supports
a finite externally coordinated bench burst, not continuous network service.

Nineteen independent model/API cases pass. The actual capture driver passes
both RTL SPI tests (30.66 s) and both checkpoint functional GL tests (225.86 s):
64/1518-byte frames at +/-13.5-ns random transition jitter and -200-ppm source
offset, post-stop bad FCS rejection and deliberate 40-us service-stall overflow.
The checkpoint netlist remains `faa4076e9f3c72fffbf902bfe6d0c2ee5a7a8e2fe661fa39ca615daebc481e84`;
these are simulations without SDF or board transport/analog models.

The retained initial runs exposed test infrastructure/setup errors: bridge
imports, an incorrect guessed netlist path, an uncaught expected background
exception and a 1-ms deadline shorter than the 1.29-ms two-frame burst. The
final test waits for the actual start write, catches expected errors within
the service task and uses a 2-ms deadline. Diagnostic raw counts were 576 and
12208 bits, with no pending/malformed record. The earlier failures remain
separate evidence and are not counted as passing runs.

[`eth10-receiver.md`](eth10-receiver.md) and its native SVG specify an unbuilt
magnetics/termination, sign comparator, two-polarity magnitude detector and
envelope/Schmitt idle gate. Package pin maps were checked against official
datasheets, including the Schmitt buffer's input on DBV pin 2. The document
includes nominal bias/threshold/RC calculations, power cost, exact wiring,
bounded capture usage and measurements required from the hardware owner.
It establishes a reviewable circuit proposal, not analog or link compliance.

The final GL runner now requires all three suites (`test`, `test_pio`,
`test_eth_service`) on one copied final netlist, with separate private
XML/log/build paths and hashes. Release gating independently reopens each
bound XML, checks its hash, exact declared count and absence of failure/error/
skip, and rejects a missing suite. Tooling tests exercise late PIO/Ethernet
failures, omitted suites, failed XML and changed XML; 24 pass.

### Repaired-checkpoint diagnosis and clock-tree candidate (R1/R2)

Fresh three-corner reports were run on a hash-checked copy of the completed
step-43 database and netlist, with the full candidate SDC and correct standard,
I/O and SRAM views. These use **global-routing RC estimates**. The saved
`state_out.json` contains inherited metrics and is not a fresh per-corner report.
`test/sim_build/review_repair43_timing.cn5hyire/{analysis,manifest}.json`
retains the diagnosis and 42 hashed artifacts.

| Corner | Setup worst slack | Hold worst slack | Negative setup endpoints | Electrical violation rows |
|---|---:|---:|---:|---:|
| Typical | 0 ns | -0.110 ns | 0 | 80 |
| Slow | -2.274 ns | -0.062 ns | 242 | 80 |
| Fast | 0 ns | -0.137 ns | 0 | 80 |

The slow worst path is SRAM-bank0 `A_DOUT[2]` to core0 `crc[9]`. Its 31 named
data-path buffers contribute 6.437 ns of cell-arc delay, excluding wire and
other logic. Slow setup worsened from the step-41 estimate of -1.346 ns.
The 80 electrical rows are 79 clock-leaf fanout violations and one SRAM
`A_DOUT[13]` capacitance violation; no slew rows remain. All three fresh
processes exited 0, but these results fail ordinary/electrical checks.

Each corner resolves 20 synchronizer groups and reports 21 stage paths,
313 CDC paths, 106 enable checks for 53 ICGs, and 2048 timing paths for 1024 FIFO
latch data pins. Those reports have nonnegative numerical margins; latch
borrowing still produces 0-ns margins. Full-SDC pulse reports have no numerical
margins and require the separate clock-only process. These are checkpoint
diagnostics, not extracted signoff, pulse compliance or MTBF.

The actual standard-cell libraries specify `default_max_fanout:8`, while the
flow's global fanout setting is 10. An isolated CTS probe adds only
`CTS_SINK_CLUSTERING_SIZE=8`, starting from the same closed pre-CTS placement.
Fresh baseline/candidate placement analysis shows 79 leaf-clock violations
reduced to four internal branches driving 16 sinks. Sixty preexisting data-net
fanout rows remain unchanged on both controls. The single-CTS budget is closed;
`review_cts_fanout.5whqdnhd/results.json` retains 79 artifacts. Reducing a leaf
cluster does not bound every level of the tree.

A distinct native insertion candidate splits those four internal branches.
Each original driver now feeds two new `sg13cmos5l_buf_8` cells, and each new
cell feeds exactly eight captured leaf inputs. All 64 original loads remain.
Native checks preserve CLOCK classification and the original non-default
routing rule. Complete emitted-netlist and database comparisons find only
eight added buffers and 64 changed leaf input connections; every other
original cell/master/pin remains unchanged. All eight physical VDD/VSS pins
connect to VPWR/VGND, and all three actual library views confirm output
function `A`, preserving clock polarity.

Fresh placement reports have **zero clock-fanout violations at all three
corners**. The 60 data-fanout rows and pre-repair ordinary timing violations
remain: slow setup -8.445 ns; typical/slow/fast hold -0.324/-0.466/-0.246 ns.
These compare with the CTS-stage controls, not the later step-43 checkpoint.
`review_clock_branch.gm75haj5/results.json` retains 64 artifacts;
`buffer_model_proof.json` separately binds the three library functions.
This was a concrete clock-tree correction candidate; the original live run
was unchanged. The reusable implementation below now finds overloaded clock
branches without depending on the prototype's four captured instance names.
It still needs complete
post-CTS/GRT repair, routing, extracted corner checks, pulse analysis,
DRC/LVS and exact final-netlist GL in a new named run.

Wrapper/verifier setup errors are retained: initial RC loading preceded the
database; native insertion names are base names; emitted-netlist and database
cell counts differ; physical VDD/VSS pins must be distinguished from VPWR/VGND
net names. Corrected checks follow actual connectivity and validate both
complete scopes. The first image guard also confused image ID with repository
digest before any STA execution; the checked receipt records both separately
and pins the exact live-candidate image ID. No failed setup is counted as a
passing timing or topology result.

### Reusable clock repair and complete physical-input capture (R1/R2/V4)

`Project.FixClockFanout` is an opt-in post-CTS composite: native branch repair
then detailed placement. `--clock-fanout-fix` selects sink clusters of eight
and inserts that step. The inactive flag is omitted from generator metadata,
so the checked-in submission configuration remains byte-identical and passes
`--check`. Cores, banks, FIFO depth, clock period, SRAM mapping and full SDC
remain unchanged. A native LibreLane configuration load verifies the actual
step order and both composite children.

The Tcl repair discovers overloaded CLOCK nets from OpenDB. It retains exact
load sets, polarity and NDR; inserts the IHP noninverting buffer; resolves its
actual generated name through the moved loads; verifies every new connection;
and checks that all resulting clock branches have at most eight loads. The
algorithm can add upper levels for larger branches; native validation here
covers the actual checkpoint branch sizes. An unsupported multi-driver or
top-level-port topology fails before candidate views are written. This is an
IHP flow hook, not a library-independent resizer or a timing waiver.

`test/sim_build/review_clock_hook.9hsa3f_s/` retains the implementation copies,
reproduction scripts, full database dumps, native logs and corner reports.
`results.json` binds 151 artifacts, including retained failures and the
post-start read-only observation of 175 TT/generated-configuration inputs:

| Closed parent | Added buffers | Original input connections moved | Clock fanout after repair |
|---|---:|---:|---:|
| CTS with eight-sink clusters | 8 | 64 | At most 8; zero violations at all three corners |
| Original CTS checkpoint | 158 | 902 | At most 8; zero violations at all three corners |
| Already repaired checkpoint | 0 | 0 | No changes to the complete database connection graph |

The comparisons cover over 137,000 physical pins per case. Every original
master/source type, other connection, top-level port, net classification and
routing rule is preserved. All added VDD/VSS pins connect to VPWR/VGND. The
three captured Liberty views confirm buffer function `A` and fanout limit
eight. A separate unsupported-topology control is rejected without emitting
candidate views. Both candidate budgets are closed; no CTS or routing sweep
was performed.

All six fresh placement STA processes exit 0. Ordinary/electrical release
checks still fail: clustered/original-tree slow setup is -8.445/-8.260 ns,
slow hold is -0.466/-0.465 ns, and each has 60 data-fanout rows plus one slow
SRAM-address slew row. Typical and fast hold also remain negative. These
results do not replace the later GRT checkpoint or extracted signoff. The
first diagnostic copies omitted the helper's relative `src/timing_checks.tcl`
dependency and exited 1; their inputs/logs remain retained. The corrected
copies and successful complete analyses are separate artifacts.

The provenance fix addresses a real omission: the previous manifest excluded
the root plugin, so a dirty snapshot could silently use HEAD's plugin. Before
editing, ROOT, live worktree and HEAD plugin bytes were checked equal at
SHA-256 `c4f00c9ede74036f13eaa0a8746f35e0b769c460b9d1bd7a0bda1c547e710558`.
That retrospective comparison cannot provide a missing pre-run binding.
The original run's manifest is preserved as legacy diagnostic evidence.
Current strict release checks reject missing physical-flow provenance and
compare future captured inputs with the hardened worktree, allowing newer
validation software while keeping the physical candidate fixed.

The 32 passing tooling regressions include modified/new root plugins,
candidate-deleted plugins, a racing snapshot edit, TT tool/config drift and
legacy-manifest rejection. Source input `current_inputs.json` identifies 126
executable files at SHA-256
`e1126cdb767be7726767727729062510dd0fcfa1a7a0f9a76718abb414b668e8`.
Only eight flow/plugin/tooling-test files differ from the earlier Ethernet
epoch. Its firmware, model, drivers, RTL and protocol tests are unchanged;
their retained digital results remain applicable to those inputs. The 17-file
hardware map is still `b2a090e416b235e94e9970f8511822614866d6e349dc14971aaf03ae8fe6ce5d`
and equals the live worktree. New complete physical validation remains open.

### PnR latch timing (decision, 2026-10-05)

The FIFO storage latches receive their data while open, so STA passes the
borrowed time on through Q and every incremental update during repair walks
it. Same RTL, placement and settings except the PnR SDC (runs t11s and t12b,
2026-10-04):

| Step | real latch arcs in PnR (t11s) | latch D->Q disabled in PnR (t12b) |
|---|---|---|
| post-CTS repair | 1 h 19 m, final WNS -0.068 ns, 194 buffers | 3 m 54 s, identical edits and result |
| post-GRT repair | 1 h 54 m, final WNS -1.993 ns | 8 m, final WNS -2.013 ns |

The run that started this review (reviewfixes-20261004, real arcs in PnR) was
past 10 h and still in post-GRT repair. Signoff always times the real arcs,
so an approximation in PnR cannot hide a violation; it can only leave one for
signoff to report. The submission preset therefore uses `protoemu_pnr.sdc`
(a multi-hour PnR would also exceed the 6-hour CI job limit).

## Validation record

| Check | Fresh evidence | Scope |
|---|---|---|
| Initial model suite | 150 passed, `test/sim_build/review_team_model.log`, 317.23 s | Initial review-fix input, including long board UART, common-clock Ethernet and FPGA evidence checks |
| Model suite after PIO/GPIO-window support | 176 passed, `test/sim_build/review_pio_transport.y406ob1d/all_model_gpio_window.log`, 304.43 s | All firmware/software/model checks; 26 PIO/API checks use the pinned instruction fixture and fake board/DMA APIs |
| Model suite after finite Ethernet capture and final GL gating | 200 passed, `test/sim_build/review_eth_board.wt1icfzz/all_model_final.log`, 307.95 s | Complete model/software/tooling suite at that input epoch, including 19 receive cases and 24 tooling cases; initial 195-test result retained separately |
| Flow/provenance regressions after reusable clock repair | 32 passed, `test/sim_build/review_clock_hook.9hsa3f_s/tooling.log`, 2.45 s | Current flow/plugin input; stale/new plugins, snapshot race, TT/config drift and complete release-gate controls |
| Combined RTL suite | 16/16 passed, `test/review_team_final_top.xml`, 611.77 s | Tapeout RTL via SPI, generated UART, complete concurrent USB service and common-clock Ethernet |
| Ethernet streaming | 1/1 passed | Current RTL through real quad SPI |
| Board Ethernet capture software | 2/2 RTL and 2/2 checkpoint GL passed, `review_eth_board.wt1icfzz/{rtl,gl}_deadline.xml` | Actual capture software over simulated quad SPI; complete frames/FCS/overflow, no analog or board rate claim |
| Extended concurrent USB | One selected test passed (13 intentionally unselected), `test/review_release_usb_errors.xml`, 458.93 s | Both variants; CRC/stuffing rejection, duplicate OUT, IN retry, SETUP interruption/reset |
| Common-clock Ethernet | Passes within the complete 16-test suite; focused evidence retained separately | 50 MHz, 64/1518-byte frames; no analog TX-compliance claim |
| Tooling | 24 gate/config/GL/FPGA regressions pass; bundle/config, GL shell syntax and diff whitespace checks pass | Fail-closed evidence, late-suite/stale-XML rejection, racing edits and generated file integrity |
| Mutation suite | 17/17 killed with all target baselines passing, `test/sim_build/review_release_mutants.log` | Immutable input: `/tmp/protoemu-mutants-r0_nj4vm/inputs.json`; later driver/test/FPGA-tooling changes are outside that snapshot |
| Tapeout lockstep | 4/4 passed, 20 seeds x 1000 cycles | 2 cores / 2 SRAM banks / 8-entry FIFOs |
| Formal | Seven tasks PASS | FIFO 4/8 proof + cover, host proof + cover, core proof, line proof |
| Four-core lockstep | 4/4 passed with all core coverage goals, 30 seeds x 1000 cycles | Expanded from original 10x600, which missed side-set-over-OUT |
| Full FPGA profile | 4/4 lockstep pass, 30x1000, `test/review_fpga_full_lockstep.xml`; ECP5 routing fails 48 MHz at 24.02 MHz | 2 cores / 2x256 RAM / 8-entry FIFOs; capacity fits, timing and board readiness fail |
| Initial FPGA timing experiments | Four private mappings, three distinct RTL candidates each 4/4 lockstep pass; changed equations have SAT proofs | Best in that round was 28.137 MHz; production profile unchanged |
| FPGA placement probes | One routed attempt at 25.690 MHz; second stopped by 43 out-of-region cells before routing | Two-attempt experiment closed; exact mapped inputs and protected baseline unchanged |
| FPGA pin/threshold probes | Each has passing SAT and 4/4 full-profile lockstep; routed 27.278/27.970 MHz | Closed without integration; unchanged device/clock/geometry |
| FPGA distributed instruction RAM | 1516 checked native mapped-memory cycles pass; full route reaches 30.490 MHz with full logical geometry | New measured experiment best, still fails 48 MHz; black-box DSP/full mapped-chip/board behavior unverified |
| LUT-RAM threshold follow-up | Count SAT and 4/4 full-profile lockstep pass; full route reaches 31.821 MHz | Current experimental best; fails 48 MHz, no production integration |
| Parallel-read follow-up | Address SAT, 4/4 full-profile lockstep and 2032 mapped-memory cycles pass; full route reaches 28.749 MHz | Closed without promotion; doubled physical read-port RAM, unchanged logical geometry; whole mapped-chip/board evidence absent |
| Ethernet 40-MHz receiver prototypes | Uniform-jitter SPI probe passes; both selectors fail extreme-jitter SPI waveforms, reproduced on the model | Closed without integration; phase/queue formal bounds alone do not prove protocol recovery |
| Detailed placement timing | Three PVT corners; latest 20-group helper coverage in `test/sim_build/review_reset_results.json`, with separate pulse-only coverage retained in `review_timing_results.json` | Placement RC estimates only; all final extracted checks remain required |
| Repaired step-43 checkpoint | Fresh three-corner native ordinary/electrical/detailed reports; slow setup -2.274 ns, all corners fail hold, each has 80 electrical rows | Global-routing estimates; source/ODB/netlist/PDK hashes verified; no final signoff |
| CTS clustering/branch candidate | One CTS probe reduces clock-fanout 79 → 4; eight explicit buffers reduce 4 → 0 in every corner; complete emitted-netlist/database topology and power checks pass | Pre-repair placement estimates; 60 data-fanout rows and ordinary timing remain; original flow/production sources unchanged |
| Global-routing checkpoint GL | 16/16 passed, 2976.49 s, `test/sim_build/review_gl_grt_checkpoint.z1_mz_u1/manifest.json` | Full suite on a netlist exported from completed step 41; checkpoint evidence only, without SDF |
| Compiled PIO transport | 2/2 RTL passed, 37.15 s; 2/2 checkpoint GL passed, 137.47 s; `test/sim_build/review_pio_transport.y406ob1d/results_final.json` | Separate PIO suites on tapeout geometry; image-load checks require clean HALT and the expected PC; no SDF or board claim |
| Hardened candidate | Run `reviewfixes-20261004` in progress | No final STA/DRC/LVS/GL result yet |

Hardening hardware SHA-256:
`b2a090e416b235e94e9970f8511822614866d6e349dc14971aaf03ae8fe6ce5d`.
The run's `candidate-inputs.json` is authoritative. Software-only refinements
do not alter this hardware hash; exact validation software hashes must also be
recorded with final results. Team members must not edit this private run.

Model/RTL validation input manifest:
`test/sim_build/review_team_inputs.json`, source SHA-256
`700234177e73615b04af9622b4cb61cdbebc62a06552b955a434d6717335ea81`.
Its hardware map equals the physical candidate's hardware map. Documentation is
outside the executable input hash. The changes remain uncommitted; include the
new source, test, flow and generated service files when preparing the team review.
The associated `test/sim_build/review_team_results.json` hashes the input and
result artifacts and distinguishes passing digital checks from the pending
physical release and failed FPGA timing target.
Earlier 14-test and focused USB results remain retained, as do the previous
`review_release_inputs.json` and `review_handoff_results.json` manifests.
The latest FPGA routed sources exactly match the earlier FPGA lockstep sources;
the ASIC hardware map still matches the isolated physical candidate. Do not
replace historical manifests when collecting newer results.

The additional timing helpers and native fixtures are captured separately in
`test/sim_build/review_timing_inputs.json`, source SHA-256
`fecb032366d8ef54915fd89ce268aab56a1badc09cb0fd6efae3cd6d1f7171a7`.
Every executable file in the earlier model/RTL manifest is unchanged; the
four additions are the two timing helpers and two native control fixtures.
`review_timing_results.json` binds 58 evidence files, the stable database,
SDCs, selected library files and previous digital results by full hashes.
The main timing hook's final change only clarifies comments; the executed
copy is retained. Its later reset-stage addition has its own executed copy
and validation manifest below.

The reset-stage input manifest is `test/sim_build/review_reset_inputs.json`, source
SHA-256 `352ed0373bf2e2657a7e8a7d8dafdc3be9537a000f98e2871c514fe4e4acf3e0`.
Only `flow/timing_review.tcl` changed from the preceding timing snapshot:
it adds the previously omitted global reset synchronizer. All executable files
from the 150-model/16-RTL-test input remain unchanged, and the physical hardware
hash remains the same. `review_reset_results.json` binds 21 artifacts, including
the passing checkpoint GL receipt, native helper promotion, failed ordinary
checkpoint timing and the retained previous validation records. Historical
input/result manifests are preserved.

The PIO transport's latest executable input is
`test/sim_build/review_pio_transport.y406ob1d/inputs_final.json`.
Its source SHA-256 is
`52a0921f4662a36d0f4c6b6c663eae5fb38ae8ccd6adcb836ac8c294b80fe473`.
`results_final.json` records its full source identity, matching hardware map,
checkpoint netlist hash, command/environment descriptions and 18 hashed evidence
files. The 176-test model run uses `inputs_gpio_window.json` (source
`2ac91babf129aa47a672e0688af880fd743f0eea81ae7c99e410f12b820699d0`).
Only `test/test_pio.py` changed afterward to reject fault-induced HALT and
require the loaded program's final PC; fresh RTL/GL runs cover that stronger
check. No model, firmware, board driver, PIO program or hardware file changed
after the full model run. The earlier 174-test/pre-window and GPIO-window
receipts remain retained. SDK reference files are pinned to commit
`d485c7a9986336024e35d790568fca33be1c5665`; assembler references are pinned to
MicroPython v1.26.0. They do not identify firmware installed on a real board.

Earlier PIO diagnostic runs had incorrect test expectations: IMEM is write-only,
R1 keeps 16 bits, and ISR is read-only. Their logs/XML/waves are retained and
are not passing evidence. The final tests use the normative register map and
check loaded-image execution, GPIO/register round trips and 32-bit FIFO data.

The retained Ethernet/service/release-gate input is
`test/sim_build/review_eth_board.wt1icfzz/inputs_final.json`, source SHA-256
`33dc2b7d86f3002c454ecfedf3cd16219e90b2650a48e5e3207df4660fbff369`.
All 123 executable files were rehashed unchanged after the 200-test run. The
subsequent eight-file flow/provenance change has its separate input above. Its
17-file hardware map still equals the live physical candidate. The associated
`results_final.json` hashes 64 artifacts, including retained failures, passing
RTL/GL XML/waves, model/tooling logs, official component references and the
unbuilt circuit proposal/calculations. The receive RTL/GL runs preceded only
GL-runner/signoff/tooling-test changes; their runtime inputs are unchanged.
The initial 195-model-test input and intermediate diagnostic input manifests
remain retained. All 17 planted mutation target files also remain byte-identical
to their previously passing mutation snapshot; this is a source comparison,
not a new mutation execution.

Latest team snapshot:
`test/sim_build/review_handoff_hook.hz8s36uv/progress.json`. It rechecks
the 126 current executable files, 17 live/current hardware files and 987
artifacts across 13 receipts. The eight changed flow/plugin/tooling files have
their own input identity; older receipt/ledger hashes remain historical.
The opt-in reusable clock repair is verified at placement scope, while timing
and board requirements remain open. The original step-46 process is still live.

The preceding snapshot,
`test/sim_build/review_handoff_clock.nrb2tj3n/progress.json`, rechecked
the 123 executable files, 17 live/current hardware files and 836 artifacts
across 12 receipts, plus the three-library buffer-function proof and this ledger.
The physical run is still the original step 46 process, confirmed live with
advancing CPU time; no final netlist or release result is available.

The preceding snapshot,
`test/sim_build/review_handoff_fpga._o1b0n7v/progress.json`, rechecked
the 123 executable files, 17 live/current hardware files, all 651 artifacts
across nine retained receipts, the bounded follow-up closure and this ledger.
It retains a fresh process observation of the original hardening run; there
is still no final netlist or release result.

The preceding snapshot,
`test/sim_build/review_handoff.mjrmfrut/progress_final.json`, rechecked
the current source/hardware maps and all 64 Ethernet, 18 PIO, 26 FPGA-placement
and 374 receiver-prototype receipt artifacts. Its earlier `progress.json`
checked the PIO receipt JSON but used the wrong artifact-key name; the corrected
snapshot verifies the PIO `evidence` map too. Intermediate observations are retained.

At 22:49 UTC the physical flow has completed global routing (step 41) and
antenna inspection (step 42), and is running post-routing repair (step 43).
Global routing reported congestion; subsequent repair and final DRC must
resolve it. The live OpenROAD process is advancing CPU time despite sparse
log output. A quiet observation interval is not a terminal flow result.

At 23:31 UTC, step 43 completed and the flow advanced through antenna repair
to step 46 (post-global-route timing repair). The step-46 process remains
active with advancing CPU time and buffered log output. Step 43 still reported
routing congestion; no final timing/DRC/GL signoff is available.

The completed step-41 database was copied into the private checkpoint GL
directory above, with its original/copy SHA-256 checked before exporting a
non-powered netlist using the same `write_verilog` command as LibreLane's
normal netlist view. `inputs.json` binds the copied database, exporter Tcl,
Docker image, candidate manifest and current validation sources. The exporter
log has the expected completion marker and no tool errors. The independent
GL run completed at 20:54:56 UTC: all 16 tests passed with no failures or skips,
including concurrent USB service/reset recovery for both variants. The fresh
XML, build exit and unchanged source/netlist hashes were verified. Its receipt
uses the distinct kind
`functional GL checkpoint without SDF`; it cannot satisfy the final release
gate. Retain this result and run `flow/gl_test.sh` again on the final netlist.
For an active GL run, keep its captured executable validation files unchanged.
Teammates can prepare changes in a separate worktree; promote them after the
receipt closes, then capture a new input manifest. Documentation is
outside the executable snapshot.

## Team reproduction and release handoff

From the repository root, use the existing environments and private outputs:

```sh
export PATH="$HOME/tools/oss-cad-suite/bin:$PWD/.venv/bin:$PATH"
export PDK_ROOT="$HOME/ttpdk"
PYTHONPATH=tools .venv/bin/python -m pytest -q test/model
make -C test SIM_BUILD=sim_build/my-team-run COCOTB_RESULTS_FILE=my-team-run.xml
python3 test/check_results.py test/my-team-run.xml 16
PYTHONPATH=tools python3 -m pe.mpy_bundle --check
python3 flow/gen_config.py --check
python3 test/mutants.py
make -C formal
```

After the original run reaches a terminal state, the physical owner can create
a new candidate with the reusable clock repair. For example, use a fresh name
(the script refuses reuse):

```sh
SNAPSHOT=1 flow/harden.sh team-clock8-20261005 2 -- --clock-fanout-fix
```

This captures schema-2 flow/plugin/TT/config inputs before hardening. Keep that
candidate's complete configuration with its validation checkout. The opt-in
configuration differs from ROOT's default JSON even though RTL geometry and
clock period are unchanged; exact-hardware/config gates deliberately require
matching candidate bytes. The legacy original run can provide diagnostics,
but its manifest cannot satisfy the new pre-run provenance requirement.

The existing hardening run is isolated at
`[home directory redacted]/dev/protoemu-exp-reviewfixes-20261004`. Watch `runs/wokwi/flow.log`
and the current step's OpenROAD log as well as `exp.log` and `result.txt`.
Some logs are buffered: verify the owning process and advancing CPU time before
concluding that an observation timeout is a failed run. Do not restart it or
reuse its name. A different hardware
candidate needs a new named run. The slow library must be the slow SRAM view,
and the fast view's -55 C SRAM characterization remains distinct from the
standard-cell -40 C corner.

For detailed timing, load `flow/timing_review.tcl` after the selected corner's
design, full candidate SDC and parasitics. The retained per-corner `run.tcl`
files show the exact database, library and RC loading sequence. For the pulse
process set `PROTOEMU_CLOCK_ONLY_PULSE_REVIEW=1`,
`PROTOEMU_ORIGINAL_SDC` to the candidate SDC, and `_SDC_IN` to its four-line
filtered copy **before loading the design/SDC**. Source
`flow/pulse_width_review.tcl` afterward. Keep those results separate. A final
analysis must load extracted parasitics, not reuse the placement estimate.

The native report controls can be reproduced with the manifest's LibreLane
image and the same PDK; retain fresh logs and check their contents:

```sh
REVIEW_ROOT=$PWD
REVIEW_OUT=$(mktemp -d "$PWD/test/sim_build/pulse-control.XXXXXX")
for pulse_ns in 0.01 20; do
  docker run --rm --network none -v "$HOME:$HOME:ro" -e PDK_ROOT \
    -e PROTOEMU_PULSE_PERIOD_NS="$pulse_ns" \
    ghcr.io/librelane/librelane:3.1.0.dev3 \
    /nix/store/4wvhzvwmv2rnwgcg0sadhs769hky28xk-zsh-5.9.1/bin/zsh \
    -c 'openroad -exit -no_splash "$1"' _ "$REVIEW_ROOT/test/sta/pulse_control.tcl" \
    > "$REVIEW_OUT/$pulse_ns.log" 2>&1
done
```

The 0.01-ns log must contain executed pulse-width violations; the 20-ns log
must contain passing numerical margins and no violations. Neither may contain
Tcl/tool errors. Process exit alone does not establish the expected result.

After the flow finishes, the hardware owner must review all extracted-corner
CDC maxima, synchronizer stage resolution, latch borrowing, clock gating,
pulse/recovery/removal and electrical limits. Process metastability constants
are needed to derive MTBF; currently no MTBF result is established. Keep every
report. Run current validation software against the exact physical netlist:

```sh
flow/gl_test.sh ../protoemu-exp-reviewfixes-20261004 "$PWD"
python3 flow/signoff.py ../protoemu-exp-reviewfixes-20261004/runs/wokwi \
  --manifest ../protoemu-exp-reviewfixes-20261004/candidate-inputs.json
```

The optional GL validation directory must match the captured hardware hashes;
it may contain newer firmware/tests. Copy its private GL evidence into a named
subdirectory of the physical run before building `release_checks.json`.
That receipt requires `netlist_sha256` and seven entries: `cdc_max_delay`,
`synchronizer_resolution`, `latch_timing`, `clock_gating`,
`minimum_pulse_width`, `electrical_limits`, `functional_gl`. Each entry has
`status: "pass"` only after checking the result, and a nonempty `evidence` list
of `{ "path": "relative/file", "sha256": "full digest" }` objects. The GL
entry must include its JSON manifest. Missing checks deliberately fail release.
Copy all three GL suite XML files alongside that manifest: the release gate
checks their hashes and contents. The runner stops without a passing receipt
if any main/PIO/Ethernet-service suite fails; its checkpoint results cannot be
substituted for the final netlist.
The Tiny Tapeout GDS action's existing GL job invokes the top-level suite.
That CI result alone does not replace this three-suite final release receipt.

The hardware owner should separately record board/SDK revision, measured
project/SCK clocks, wiring and analog circuit, raw UART/USB/Ethernet captures,
host throughput/stall distributions, and the exact firmware/driver hashes.
Do not infer board success from model, FPGA synthesis, GL or physical timing.

## Remaining work and team ownership

| Requirement | Current state | Next concrete action |
|---|---|---|
| Exact ASIC physical signoff (R1/R2) | Live isolated legacy run; repaired checkpoint still fails ordinary/electrical checks. Opt-in reusable clock repair passes topology/power checks and removes clock-fanout violations at placement scope | Physical owner: finish the original run and retain diagnostic evidence; use a fresh schema-2 candidate for clock repair and remaining setup/hold/capacitance fixes, then require extracted corner checks, seven receipts and exact final-netlist GL |
| FPGA at protocol clock | Full geometry fits ECP5; LUT-RAM plus the threshold equation improves the experimental best to 31.821 MHz, still below 48 MHz. The two-route follow-up, pin/threshold and placement probes are closed; UP5K fails capacity | FPGA owner: investigate the remaining routing path with a distinct bounded representation, preserve cycle behavior/geometry and require equivalence/capacity/timing at 48 MHz; then validate the full mapped chip and a real board adapter |
| Sustained RP2350 transport | Optional PIO/DMA implementation and digital checks exist; measured bounds absent | Board owner: validate the selected SDK/pin map, measure quad SPI sampling/throughput and concurrent service latency; see `board-transport.md` |
| USB board demonstration | Complete digital error/reset/retry tests pass | Board owner: enumerate with matching endpoint-zero descriptors and retain host/bus captures |
| Ethernet analog/common-clock demonstration | Finite board capture software passes RTL/checkpoint GL; proposed receiver circuit is unbuilt. Full digital RX streaming and 50-MHz internal TX/RX pass; 40-MHz selectors still fail jitter-limit waveforms | Hardware/RTL owners: build/qualify `eth10-receiver.md`, resolve receiver phase recovery and TX half-cell limits, preserve the full jitter bound, then validate simultaneous external operation |

These are open validation/implementation requirements, not completed fixes.
They must not be relabelled as passed because software tests or a flow process
exit successfully. No connected board or process metastability characterization
has been established in this workspace.
