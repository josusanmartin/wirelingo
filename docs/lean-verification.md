# Lean verification: current commands, domains and evidence

Run from the repository root. The maintained native checks below build
required Lean modules from checked-in source into **fresh output**, without
Lake, downloads, historical receipts or preexisting campaign `.olean` files.
They establish finite agreement with a manual Lean reference, not universal
SystemVerilog refinement. The [dated history](lean-verification-history.md)
preserves the previous guide verbatim, including failed preparations and
subsequent passing corrections.

## Current checkout commands

Install separately: Python 3.10+, **Lean 4.28.0** with its standard library,
Icarus Verilog (`iverilog` and `vvp`), and `strace`. Supply the actual installed
Lean binary, not an elan shim; these commands refuse automatic toolchain
installation. Saved native checks used Icarus 14.0-devel; version labels alone
do not identify the tools. Each run records actual tool versions and hashes.

Replace these placeholders with installed executable paths:

```sh
task_lean=/path/to/lean4.28.0/bin/lean
task_iverilog=/path/to/iverilog
task_vvp=/path/to/vvp
task_strace=/path/to/strace

# Normally fetched two-core WAIT side writes and registered pin ownership.
python3 formal/lean/run_fetch_ownership.py \
  --output /tmp/wirelingo-ownership-check \
  --lean "$task_lean" --iverilog "$task_iverilog" \
  --vvp "$task_vvp" --strace "$task_strace"

# IN PINS8/autopush32 full-RXF preservation and resume; all phases by default.
python3 formal/lean/check_rtl_rx_autopush.py --phase all \
  --output /tmp/wirelingo-rx-autopush-check \
  --lean "$task_lean" --iverilog "$task_iverilog" \
  --vvp "$task_vvp" --strace "$task_strace"
```

Each destination must **not exist, even if empty**. Its parent must exist;
output must lie outside the source checkout and Lean library. Executables may
also resolve from `PATH`. The standard library defaults to `lib/lean` beside
the installation's `bin` directory; use `--lean-lib` for another layout.
No accepted/shared `.lake` objects are overwritten. Missing tools or invalid
paths fail with a dependency/output diagnostic.

The ownership wrapper runs prepare then native within one fresh root. The
default IN8 corpus's `all` mode does the same; UART-IN1 uses separate phases
below. Preparation elaborates unmodified RTL and a labeled scratch-only
mutant, compiles the manual specification, audits its lemmas,
and computes predictions before simulation. Native compares all declared
fields/phases and requires the expected actual-mutant divergence. Inspect
`run-ledger.json`, phase `ledger.json`, axiom output, raw/predicted rows,
negative counterexamples and `dependency-hashes.json`; a failed compilation
or assertion is a failed run, not a passing negative control.

### Separate prepare and native phases

Use separate fresh paths when a reviewer needs to inspect preparation first.
The ownership lower-level checker requires explicit tool **and library** paths;
RX accepts the same arguments. Keep the checkout and prepared files unchanged.
Native verifies the receipt SHA and source/tool/library/artifact bindings
before writing its destination. A receipt hash alone does not bless stale data.

```sh
task_lean_lib=/path/to/lean4.28.0/lib/lean
receipt_sha256() {
  python3 -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' "$1"
}

python3 formal/lean/check_rtl_fetch_ownership.py --phase prepare \
  --output /tmp/wirelingo-owner-prepare \
  --lean "$task_lean" --lean-lib "$task_lean_lib" \
  --iverilog "$task_iverilog" --vvp "$task_vvp" --strace "$task_strace"
python3 formal/lean/check_rtl_fetch_ownership.py --phase native \
  --output /tmp/wirelingo-owner-native \
  --lean "$task_lean" --lean-lib "$task_lean_lib" \
  --iverilog "$task_iverilog" --vvp "$task_vvp" --strace "$task_strace" \
  --prepared /tmp/wirelingo-owner-prepare \
  --prepared-sha256 "$(receipt_sha256 /tmp/wirelingo-owner-prepare/prepared-receipt.json)"

python3 formal/lean/check_rtl_rx_autopush.py --phase prepare \
  --output /tmp/wirelingo-rx-prepare \
  --lean "$task_lean" --lean-lib "$task_lean_lib" \
  --iverilog "$task_iverilog" --vvp "$task_vvp" --strace "$task_strace"
python3 formal/lean/check_rtl_rx_autopush.py --phase native \
  --output /tmp/wirelingo-rx-native \
  --lean "$task_lean" --lean-lib "$task_lean_lib" \
  --iverilog "$task_iverilog" --vvp "$task_vvp" --strace "$task_strace" \
  --prepared /tmp/wirelingo-rx-prepare \
  --prepared-sha256 "$(receipt_sha256 /tmp/wirelingo-rx-prepare/prepared-receipt.json)"
```

The current entrypoints are [ownership wrapper](../formal/lean/run_fetch_ownership.py),
[ownership checker](../formal/lean/check_rtl_fetch_ownership.py) and
[RX checker](../formal/lean/check_rtl_rx_autopush.py). Their historical corpus
labels are not a requirement to use an old Git checkout. Source bindings cover
the current required files; RX compiles against installed `Std`, while ownership
also builds Wait/Event/Gating/SideSet before PinOwnership.

## Independently proved manual domains

These are kernel-checked statements about **manually defined Lean functions**.
They do not prove that SV implements those functions. Read each theorem's
hypotheses; the rows below are separate domains, not a global theorem count.

| Specification | Proved domain and material boundary |
| --- | --- |
| [Wait](../formal/lean/Wirelingo/Wait.lean) | Resolved-condition WAIT attempt: success/timeout priority, budget/frame/ranges, PC wrap/delay and timed completion within N+1 **attempts**, not elapsed clocks. Gates, fetch and overriding writes are external. |
| [Event](../formal/lean/Wirelingo/Event.lean) | RISE/FALL/EDGE/DIFF (kinds2/3/4/7): sticky retention, edge versus DIFF first entry, qualifiers, clearing/priorities and WAIT composition. Execution permission is an observed input here. Pre-write DIFF reference/budget alias semantics are modeled and checked finitely below. |
| [Gating](../formal/lean/Wirelingo/Gating.lean) | Derives divider/tick/exec/delay for a fixed own-bank, self-wrapped edge/DIFF WAIT; counter bounds/carry/raw div_int0, disabled/restart/cancel priorities and non-attempt budget retention. Logical pins/effective CTRL inputs; no fetch/bank-fault proof. |
| [RtlGating](../formal/lean/Wirelingo/RtlGating.lean) | One-step arithmetic/gate equality between two manual Nat transcriptions under valid fraction/own-bank assumptions. Width truncation and carry included; no machine-checked SV extraction or sequential refinement. |
| [SideSet](../formal/lean/Wirelingo/SideSet.lean) | Per-pin finite-width side function: completion independence, execution/optional suppression, out/oe separation and mandatory stalled bridge mask/value0x600/0x400. The function clamps count/rotates pins; general SV rotation correspondence is unproved. WAIT zero-main-data domain; separate flag-cycle executable is not a general flag/event theorem. |
| [PinOwnership](../formal/lean/Wirelingo/PinOwnership.lean) | Selected-owner update, nonowner noninterference, absent/no-write retention, direct POUT priority and old owner on configuration edge; four supplied core slots with zero masks for absent cores, line disabled. Closed fetch projection has finite checks below, not a fetch refinement theorem. |
| [RxAutopush](../formal/lean/Wirelingo/RxAutopush.lean) | Five IN8/autopush32 lemmas cover inactive/below/ready/full and conditional full-pop/resume with the current byte. Eight additional statements cover right-shift IN1 accumulation/ready/full/current-bit resume, blocking PUSH ready/full, LSB-first byte order and high-aligned 1/2/3-byte tails. Supplied execution gates and state assumptions remain explicit; these are manual boundaries, not a UART interpreter or SV translation. |

The [axiom policy](../formal/lean/audit_axioms.py) allows only standard
`propext`, `Classical.choice`, `Quot.sound`; it rejects `sorryAx` and new axioms.
Ownership audits six statements; default IN8 audits its five original RX
lemmas, using only `propext`. UART-IN1 audits those five plus eight new
statements: 13 in total, using only `propext` and `Quot.sound`. General
specification statements depend on the Lean kernel/foundations. Executable
agreement additionally trusts the compiler/runtime, checker, bench, Icarus
and behavioral SRAM model; the source relation is hand reviewed.

## Saved comparisons that actually executed

These are recorded finite results, not tests run by reading this guide.
The [dated history](lean-verification-history.md) preserves earlier commands,
source maps, failures and audit details. The UART-IN1 result is described
below. Historical campaign evidence remains outside the checkout; the current
commands do not require it.

| Comparison | Recorded evidence level and result |
| --- | --- |
| Resolved WAIT attempt | Python `Core.step` comparison:541,184 transitions;12 invalid inputs and4 negative controls. **No RTL execution.** |
| C1 event tracker | Python `Chip.step`:165,888 cycles; qualified edges/DIFF aliases/non-tick pulse checks and2 model mutants. Seeded/local states; **no RTL execution.** |
| C2 closed gate | Python `Chip.step`:10,440 total cycles including mutants;3 model mutations rejected. Derived gate closes observed-exec feedback only in its fixed domain; **no RTL execution.** |
| C4/C5 core gate | Actual unchanged core/dependencies:64x32=2,048 positive comparison clocks; C5 replay2,213 aggregate clocks including resets/negatives,66 one-time deposits. Actual carry-removal mutant and wrong-bank diagnostic. Stable externally supplied instruction/raw seeds; **not normal chip fetch**. |
| C7/C8 side buses | Actual unchanged core:98 reference rows over60 positive/assumption streams; final C8 aggregate221 clocks,61 one-time deposits. Actual completion-gated mutant fails0; offline channel-swap/wrap-loss reject. C7's mutant elaboration failure was corrected in C8; **not chip ownership/pad evidence**. |
| C10/R13 fetched ownership | Actual normally loaded/fetched chip:132 positive rows across43/39/50 clocks;175 aggregate clocks/8 resets. Actual owner0 mux mutant fails29, offline output/port shift31. R13 source-only checkout rebuild passed; **no deposits or Python model steps**. |
| C11/C12 RX-autopush | Actual normally fetched chip/FIFO:192 positive rows,96 left+96 right+57 mutant clocks=249 total/6 resets;9 drained words each setting. Pop67 still blocks IN; resume68 shifts current synchronized byte once. Mutant writes stalled ISR/count and fails56. C11 compile failure was closed by C12; **no deposits or Python model steps**. |
| C23–C26 UART-IN1/PUSH | Actual normally loaded 19-word UART capture and a separate reduced full-IN1 fixture: 10 positive streams, 987 projected IN/PUSH boundary rows, 9,101 aggregate clocks including 22 resets and the mutant. The actual partial-PUSH alignment mutant diverges at clock 336. Both the metadata check and fresh prepare/native sequence pass; **no deposits or Python model steps**. |

## Current normal-fetch assumptions and exclusions

The normal-fetch witnesses instantiate unchanged `pe_chip` with two cores,
two banks,256 words per bank and FIFO depth8, plus real core/imem/line/FIFO/
clock-gate dependencies and unchanged behavioral SRAM. They start from reset
and load programs/configuration through **parallel chip bus ports**. There
are no hierarchical deposits, forced/injected instructions or DUT-feedback
inputs. This tests ordinary registered fetch and internal storage/bus logic,
not paid serial-host transport, bandwidth, pin ownership outside the declared
cases, electrical SRAM or physical pads/CDC/timing.

Ownership runs two complementary, mandatory side-set FLAG1/FLAG0 F0 WAIT
programs in their own banks, wrap0..1, div1/4, delay0, side base9/count2,
OSR0/autopull0. LINE and open-drain overlays are off; POE/other pins are fixed.
Old owner on a config edge, inactive-owner retention, POUT priority, flag
release/fetch/wrap and logical9/10 to `uo_out[1]/[2]` are checked. Qualifiers,
timeout, auto-clear, other instruction/main writes and arbitrary interference
are excluded. These flag conditions are a separate narrow executable, not
an extension of Gating's proved edge/DIFF WAIT contract.

The IN8 corpus runs core0 only (core1 disabled), four identical IN PINS8 words,
wrap0..3, div1/frac0/delay0, threshold32, both shift directions and a two-flop raw-pin
pipeline. LINE, CRC tap, side-set, FJOIN, FIFO clear and concurrent core
configuration/PC/register writes are excluded; config is stable after setup.
Its declared CTRL enable/restart and FIFO snap/commit/cancel schedule includes
disabled retention and restart clearing without clearing RXF. Full is sampled
before host pop; stalling preserves partial ISR/count/PC but **does not latch
all changing wire bytes**. General reset during execution, arbitrary programs,
UART IN1, SPI/UART lossless stalled reception and whole-protocol behavior are
outside this IN8 witness; the separate UART-IN1 scope is described below.

None of these results is universal ISA/SV refinement, reachability for every seed,
a wall-clock termination guarantee, full-chip concurrency beyond the cases,
or physical/silicon/release qualification. Consult the [ISA](isa.md), actual
[core](../src/pe_core.sv)/[FIFO pair](../src/pe_fifos.sv)/[chip](../src/pe_chip.sv),
and archived source maps for the remaining translation/trust gap.

## UART-IN1 and partial PUSH

The explicit `--corpus uart-in1` mode checks normally fetched UART capture
through actual core/chip/FIFO RTL and behavioral SRAM. Preparation compiles
and audits 13 manual lemmas and generates 987 expected IN/PUSH boundary rows.
The recorded native run passes ten positive streams and rejects an actual
partial-PUSH alignment mutation across 9,101 total clocks, including 22 reset
edges. The entire metadata-check/prepare/native sequence took 27.49 seconds
under a single 120-second wall limit and 2 GiB address-space cap.

Use fresh destinations and the installed-tool variables and `receipt_sha256`
function defined above. The firmware is the unchanged 19-word
[UART capture program](../firmware/uart_rx_capture.pasm).

```sh
python3 formal/lean/check_rtl_rx_autopush.py --corpus uart-in1 --phase prepare \
  --output /tmp/wirelingo-uart-in1-prepare \
  --lean "$task_lean" --iverilog "$task_iverilog" \
  --vvp "$task_vvp" --strace "$task_strace"
python3 formal/lean/check_rtl_rx_autopush.py --corpus uart-in1 --phase native \
  --output /tmp/wirelingo-uart-in1-native \
  --lean "$task_lean" --iverilog "$task_iverilog" \
  --vvp "$task_vvp" --strace "$task_strace" \
  --prepared /tmp/wirelingo-uart-in1-prepare \
  --prepared-sha256 "$(receipt_sha256 /tmp/wirelingo-uart-in1-prepare/prepared-receipt.json)"
```

Preparation does not execute `vvp`. Native requires the unchanged source,
tools, libraries and prepared artifacts bound by that receipt. `--phase all`
is disabled for this corpus. Its separate 32,768-clock bench/checker ceiling
does not enlarge the declared 9,101-clock schedule; default IN8 retains its
original limits and was not replayed for this result.

The capture cases request 1, 2, 3, 4, 5, 31 and 32 bytes, a final bad STOP,
and five bytes with only four offered. The last case remains incomplete:
there is no idle flush or false HALT. Independent raw RX0 waveforms run at
exactly eight clocks per bit through two synchronizer flops; core0 runs at
divider 1, with core1 disabled. Setup and drains use the parallel chip bus.
The reduced full-IN1 fixture separately checks snapshot/cancel/commit, the
pre-pop full bubble, restart, and resumption using the current sampled bit.

The three-byte alignment mutant drains `0x0081d339` instead of `0x81d33900`;
its first disagreement is clock 336. The saved positive traces agree with
all 987 projected boundary rows, fetched instructions, independent
UART samples, word/tail order, STOP/F0/HALT and drain assertions. The Lean
projection receives pre-state and execution/control-flow boundaries; it is
not a closed whole-core predictor or machine-extracted SV refinement.

Three verification defects were corrected without weakening those conditions:
UART-only Lean calls use one thread under the memory cap; the tail proof
explicitly reduces typed `BitVec 32` zero; and setup metadata emits list pairs
so JSON round trips preserve direct declaration equality. Altered numeric
setup data still fails that equality guard. The failed C23 preparation and
first C26 native-validation attempt remain preserved in campaign evidence;
the final passing packet is `C26-EUCLID-ARM-LISTS`.

Full words may publish before STOP validation, so callers must still inspect
final framing status and completion. A stalled IN cannot recover intervening
wire bits. This finite result does not establish baud tolerance, paid serial
host timing, concurrent protocols, arbitrary programs, electrical/physical
behavior or general UART correctness. LINE, CRC, side-set, FJOIN and active
IMEM/configuration/PC/register interference remain excluded.

## Historical section links

The archive preserves every original heading/body. Old guide anchors below
lead to their dated counterparts; superseded failure status is kept there.

- <a id="lean-verification-of-wait-execution"></a> [Lean verification of WAIT execution](lean-verification-history.md#lean-verification-of-wait-execution)
- <a id="executable-transition"></a> [Executable transition](lean-verification-history.md#executable-transition)
- <a id="proved-properties"></a> [Proved properties](lean-verification-history.md#proved-properties)
- <a id="reproduction"></a> [Reproduction](lean-verification-history.md#reproduction)
- <a id="c1-euclid-wait-all-cycle-event-composition"></a> [C1-EUCLID-WAIT: all-cycle event composition](lean-verification-history.md#c1-euclid-wait-all-cycle-event-composition)
- <a id="proposed-rtl-state-relation-and-remaining-obligations"></a> [Proposed RTL state relation and remaining obligations](lean-verification-history.md#proposed-rtl-state-relation-and-remaining-obligations)
- <a id="finite-check-and-observed-limits"></a> [Finite check and observed limits](lean-verification-history.md#finite-check-and-observed-limits)
- <a id="refinement-gap-and-next-proof-candidates"></a> [Refinement gap and next proof candidates](lean-verification-history.md#refinement-gap-and-next-proof-candidates)
- <a id="c2-euclid-gating-closed-execution-permission"></a> [C2-EUCLID-GATING: closed execution permission](lean-verification-history.md#c2-euclid-gating-closed-execution-permission)
- <a id="c2-proofs-and-relation"></a> [C2 proofs and relation](lean-verification-history.md#c2-proofs-and-relation)
- <a id="c2-finite-validation"></a> [C2 finite validation](lean-verification-history.md#c2-finite-validation)
- <a id="c4-euclid-rtl-gate-finite-native-rtl-correspondence"></a> [C4-EUCLID-RTL-GATE: finite native RTL correspondence](lean-verification-history.md#c4-euclid-rtl-gate-finite-native-rtl-correspondence)
- <a id="actual-source-map-and-arithmetic-relation"></a> [Actual source map and arithmetic relation](lean-verification-history.md#actual-source-map-and-arithmetic-relation)
- <a id="predeclared-traces-native-result-and-negative-evidence"></a> [Predeclared traces, native result and negative evidence](lean-verification-history.md#predeclared-traces-native-result-and-negative-evidence)
- <a id="c5-euclid-replay-explicit-fresh-replay-interface"></a> [C5-EUCLID-REPLAY: explicit, fresh replay interface](lean-verification-history.md#c5-euclid-replay-explicit-fresh-replay-interface)
- <a id="c7-core-side-set-output-contract"></a> [C7: core side-set output contract](lean-verification-history.md#c7-core-side-set-output-contract)
- <a id="c8-disposable-side-set-mutant-declaration-binding"></a> [C8: disposable side-set mutant declaration binding](lean-verification-history.md#c8-disposable-side-set-mutant-declaration-binding)
- <a id="c9-normally-fetched-waits-and-registered-per-pin-ownership"></a> [C9: normally fetched WAITs and registered per-pin ownership](lean-verification-history.md#c9-normally-fetched-waits-and-registered-per-pin-ownership)
- <a id="in8autopush32-full-rxf-boundary--frozen-c11-preparation-failure"></a> [IN8/autopush32 full-RXF boundary — frozen C11 preparation failure](lean-verification-history.md#in8autopush32-full-rxf-boundary--frozen-c11-preparation-failure)
