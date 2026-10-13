# Lean verification of WAIT execution


Run the finite SRAM-fetch/ownership regression from the checkout with installed
Python3.10+, Lean4.28.0 (including its standard library), Icarus Verilog/vvp,
and strace. Replace the executable placeholders with installed paths:

```sh
python3 formal/lean/run_fetch_ownership.py \
  --output /tmp/wirelingo-ownership-review \
  --lean /path/to/lean4.28/bin/lean \
  --iverilog /path/to/iverilog --vvp /path/to/vvp
```

The output must not exist; its parent must exist and lie outside the checkout
and Lean library. Icarus, vvp and strace can also resolve from PATH; use
`--strace` if needed. The Lean library defaults to `lib/lean` alongside its
installation's `bin` directory; `--lean-lib` overrides it. Supply the actual
installed Lean binary rather than an elan shim: this runner never downloads
or installs a toolchain. No Lake build or historical evidence/objects/commit
is required. Wait/Event/Gating/SideSet compile from checked-in source into
fresh scratch, followed by PinOwnership and the unchanged six-theorem audit.
Both real RTL variants elaborate before the fixed132-positive-row comparison
and43-clock actual owner0 mutant. Total175 RTL clocks include8 reset edges;
model steps/deposits are zero. The actual mux and offline output/port shift
controls must first disagree at29 and31 respectively. This remains finite
manual-model/native agreement in the documented domain, not universal SV
refinement or pad/physical validation.

`run-ledger.json` and the prepare/native ledgers retain commands, exits,
versions, source/tool hashes, syscall-read runtime/library hashes, raw and
predicted rows and negative counterexamples. The preparation receipt binds
every required checked-in source, tool, read Lean library input, compiled
object, corpus and prediction artifact; native rejects stale source or
artifact inputs before creating its output. Advanced separate phases use
`check_rtl_fetch_ownership.py --phase prepare|native` with explicit tool/library
paths and fresh outputs; native additionally takes `--prepared` and the exact
`--prepared-sha256`. Historical C9/C10 receipts retain their original meaning
and bytes; this maintained interface does not reinterpret old packets.

The October13 checkout-usability run rebuilt imports in an isolated source-only
copy and passed in27.63s. Its corpus,132 positive rows and both counterexamples
match the saved C10 result exactly; syscall-read records show no historical
evidence or original-worktree inputs. Missing tools/nonfresh output/stale
prepared sources reject before output writes. The proofs and bench are unchanged.

This provides executable Lean specifications and 46 kernel-checked theorems:
15 for the **resolved-condition WAIT instruction-attempt boundary**, and 15
for the **RISE/FALL/EDGE/DIFF tracker and condition-resolution projection**,
plus 16 for a **closed divider/delay/gating projection**.
They establish timeout progress, selected state invariants, event retention,
first-entry/clear rules and selected cycle priorities. Executable differential
checks connect the specifications to the Python instruction model.
There is no proved RTL refinement, full-ISA verification, or physical claim.

The source reference is Wirelingo v2 commit
`93c213722a7f530e5100bae46a19fc04731c75fa`. The implementation files below
were read in a clean worktree at that commit, and remain unchanged by this work:

| Source | Relevant semantics |
| --- | --- |
| `docs/isa.md` §4.2 and §5.2 | Attempt versus delay ticks, sequential/wrapped PC, WAIT condition and timeout priority |
| `tools/pe/isa.py` | WAIT opcode 1; timeout bit 10; selected register bits 9:8 |
| `tools/pe/sim.py`, `Core._op_wait`, `Core.step` | Actual condition/timeout branches, register and T effects, completion/PC/delay commit |
| `src/pe_core.sv`, `OP_WAIT`, `w_timeout`, `pc_seq`, execution commit | Corresponding hand-inspected RTL logic; not translated or proved equivalent |

## Executable transition

`formal/lean/Wirelingo/Wait.lean` defines `waitAttempt` with four inputs:
timeout enable, the already-resolved condition, the selected 16-bit register
budget, and the prior T flag. It returns completion, the new budget, and T.

1. If the condition holds, complete. Preserve the budget; clear T only if
   timeout is enabled. This branch wins even when the budget is zero.
2. If the condition does not hold and timeout is enabled with budget zero,
   complete with T set and budget still zero.
3. Otherwise stall. Preserve T, and decrement the budget only with timeout
   enabled. The timed decrement is reached only for positive budgets.

`waitStep` projects this into PC, the delay counter, and the in-progress WAIT
latch. Completion advances PC to `WRAP_BOTTOM` when PC equals `WRAP_TOP`, or
to `(PC + 1) mod 1024` otherwise; it loads the configured delay and clears
the WAIT latch. Stalling keeps PC and delay counter and sets the WAIT latch.

This interface is an **attempt**, not a clock cycle. It applies when the core
is enabled, has a tick, is neither halted nor faulted, has zero pending delay,
fetches in its own bank, and has no overriding host write or restart. Divider,
instruction fetch and these gates are outside the specification. The probe
always supplies a zero incoming delay counter. The range/frame theorems also
hold for the broader mathematical input domain.

Budgets use Lean natural numbers. `timed_stall_iff` proves that the timed
stall branch requires a positive budget, so natural subtraction agrees with
the RTL's guarded 16-bit subtraction. `budget_word_range` proves a valid
16-bit budget stays valid; `step_preserves_word_ranges` also preserves the
10-bit PC and 8-bit delay ranges. This does not establish bank ownership or
the configured number of physical engines/banks.

## Proved properties

| Theorems | Meaning |
| --- | --- |
| `success_priority`, `expires_at_zero`, `positive_miss`, `untimed_miss` | Exact outcomes of the four meaningful branches |
| `budget_nonincreasing`, `budget_word_range`, `timed_stall_iff` | Budget never increases, remains in range, and a timed stall has a positive input budget |
| `stall_preserves_t`, `stall_frame` | A stall preserves T, PC and delay counter and sets the WAIT latch |
| `misses_leave_budget`, `timeout_after_budget_plus_one` | n misses starting at n+k leave k and remain incomplete; budget N expires on attempt N+1 |
| `completion_bound` | For every sequence of resolved conditions, a timed WAIT completes within N+1 attempts starting at budget N |
| `complete_advances`, `completed_wrap`, `step_preserves_word_ranges` | Completion commits PC/delay/latch correctly and preserves word ranges |

The progress proofs execute the transition repeatedly and use induction on
budget/attempt count; completion is not a supplied hypothesis. They count
**instruction attempts**, not elapsed clocks or microseconds. Disabled cores,
host interference, restarts and lack of ticks can prevent wall-clock progress.
The bound ends at the first completion and does not mean a program that wraps
back to the same WAIT must terminate.

## Reproduction

Prerequisites: Python 3, Bash, a C compiler for Lean's executable, and elan
with `leanprover/lean4:v4.28.0` available. `lean-toolchain` pins that stable
version. Lake uses only Lean's bundled `Std`; `lake-manifest.json` contains
no external packages. Generated `.lake/` files stay local and ignored.

From the repository root:

```sh
formal/lean/verify.sh
```

The script builds the library and compiled `wait_probe` executable with
warnings treated as errors, audits every theorem's transitive axiom
dependencies, compares the executable against the Python model, then runs
negative controls in disposable temporary copies. Separate commands are:

```sh
cd formal/lean
lake build
python3 audit_axioms.py
python3 check_model.py
python3 check_negative_controls.py
```

The audit permits only Lean's standard foundations `propext`,
`Classical.choice`, and `Quot.sound`; it rejects `sorryAx` and custom axioms.
Every theorem declared in `Wait.lean` must appear in `Audit.lean`'s output.
The focused event check similarly audits every `Event.lean` theorem using
`EventAudit.lean`; all 15 event proofs use only `propext` or no axioms.
There are no admitted proofs or added axioms. The trusted base includes the
Lean kernel and standard foundations; running the compiled probe additionally
trusts Lean's compiler/runtime and the host C toolchain.

The numerical probe accepts one line per attempt:

```text
timeout condition budget T PC wrapTop wrapBottom delay
```

It emits `complete budget T PC delay inWait`, rejecting malformed or
out-of-width inputs. `check_model.py` encodes real `WAIT PIN1` instructions
with side-set disabled and executes `Core.step` with matching logical pin
input. It compares all six output fields and checks that the Python model
preserves unselected registers and produces no flag set/clear effects.
It uses the model's four-bank logical ISA for PC coverage; this is not a
claim about active engine capacity in the 6x4 physical configuration.

The October 11 check passed **541,184 transitions**:

- Every budget 0–65535, both timeout enables, both resolved conditions and
  both prior T values (524,288 cases), rotating timeout register and initial
  in-progress state across budgets.
- Every PC 0–1023, both wrap branches, zero-budget success/expiry priority,
  and delay values 0, 1, 127 and 255 (16,384 cases).
- All four timeout registers and both initial WAIT states at eight boundary
  budgets, with all timeout/condition/T combinations (512 cases).

Twelve invalid probe inputs were rejected. Negative controls rejected a Lean
timeout-priority error, a lost decrement, an admitted proof, and an in-memory
Python timeout-priority error. The checker emits SHA-256 fingerprints of the
actual specification and referenced source files; preserve that output with
the exact commit when recording a run. It does not certify a Git head or
provide a signed/hermetic receipt.

## C1-EUCLID-WAIT: all-cycle event composition

`Wirelingo/Event.lean` separates observation, condition resolution, execution
commit and PC cancellation. It reads the encoded operand and pre-cycle
register file. `eventNow` is qualified rise/fall/any-edge detection, or the
masked rotated-pin/reference-byte difference for DIFF. `sampleEvent` updates
`prev` and sticky `event` on **every enabled cycle while in a WAIT**, even
without a tick or execution permission. It does not consult the attempt gate.

`resolveEvent` ignores stale history on an edge WAIT's first attempt, but
checks DIFF as a level on its first attempt. Later attempts use the sticky
event OR the observation in the same cycle. Qualifiers apply when new edges
are observed; changing the qualifier later cannot retract a sticky event.
For DIFF, bits 7:0 are the mask, including bits that otherwise encode edge
qualifiers/clear. Its `rref=x[15:14]` and `treg=x[9:8]` can alias. Resolution
reads R before `waitAttempt` changes the budget; the next cycle sees the
decremented reference if they alias.

`eventCycle` composes this resolution with the existing `waitAttempt`.
It supplies `exec` as an observed execution permission, independently of
the observation gate. A first stalled entry samples `prev` and clears
`event`; this overrides any stale observation. Completion clears `inWait`
but does **not** necessarily erase `event`. A host PC write clears `inWait`
after the cycle's execution/tracker effects and preserves `prev/event`.
Restart clears the tracker and T without executing or changing R. Cold reset
sets the projected tracker, R and T to zero.

`sticky_completes_wait` derives successful completion and budget preservation
by composition with `success_priority`, including a zero timeout budget.
Other new theorems prove retention, disabled-cycle freeze, first-entry edge
rejection versus DIFF level resolution, qualifier exclusion of new edges,
first-stall clearing, completion latch clearing, PC cancellation, and
reset/restart priority. They establish properties of the executable Lean
projection, not an RTL state relation.

### Proposed RTL state relation and remaining obligations

| Lean state/input | RTL signal/state | Python observation | Remaining obligation |
| --- | --- | --- | --- |
| `tracker.inWait/prev/event` | `in_wait/wprev/wevent` | `Core.in_wait/wprev/wevent` | Establish relation at reset and preserve it over every clock |
| `r0..r3`, `t` | `r0..r3`, `t` | `Core.r`, `Core.t` | Host register priority, range invariants and all other writers |
| `operand` | `x=instr[18:0]` | `isa.fields(Chip.fetch(pc))` | Fetch latency, SRAM behavior, stable instruction and bank ownership |
| `pins`, `inBase`, `rotatePins` | `pins_in`, `in_base`, `pins_rot` | `Chip.pins_in()`, `Core.in_base`, `rotr32` | Bitvector rotation/decode equivalence, synchronization and physical pin timing |
| `eventNow` | `trans`, `qual`, `wdiff`, `edge_done` | `_track_wait`'s `ev/diff/edge_done` | Prove combinational decode, DIFF alias/read-before-write and qualifier relation |
| `enabled` | `enable` outside restart | `Chip.enable` before `Chip.step` | Registered CTRL enable timing and power-selection behavior |
| `exec` | `tick && !halted && !fault && dcnt==0 && bank_ok` | Start-of-cycle `cnt==0`, halt/fault/delay/bank predicates | Divider, tick/commit timing, PC/delay and fault transitions |
| `sampleEvent/commitEvent` | enabled updates at lines 776–794 | `_track_wait` then `_op_wait` | Compose actual sequential assignments, especially first-entry override |
| `restart` | `restart`, `active=enable&&!restart` | Registered `Chip.ctrl_pend` restart | CTRL capture timing, all restarted state, interaction with unrelated host writes |
| `pcWrite` | `host_pc_w`; final host latch/delay write | `Chip.step(we=True, C_PC, ...)` after core execution | PC value/fetch priority and cancellation outside this projection |
| `reset` | `!rst_n` asynchronous reset branch | Fresh `Core(0)` projection | Actual async reset/deassertion; Python has no `rst_n` cycle API |

### Finite check and observed limits

This ticket's focused invocation, from the repository root, is:

```sh
python3 formal/lean/validate_event.py --evidence /tmp/wirelingo-event-check
```

It enforces a 90-second combined cap for build, both theorem audits and
`check_event.py`. It deliberately omits the earlier 541,184-case timeout
corpus. The event probe accepts 16 fields: `inWait prev event R0 R1 R2 R3 T
operand pins inBase enabled exec reset restart pcWrite`; its output is the
eight projected state fields. It rejects kinds outside 2/3/4/7 and
simultaneous restart plus PC write. Operand/inBase stay stable within a trace.

Validation 2 passed in **14.26 seconds**: 13,824 deterministic 12-cycle
traces, 165,888 actual `Chip.step` cycles, 1,728 DIFF reference/timeout alias
traces, and 2,531 model coverage events for a pulse caught between ticks.
The model's synchronizers step with real input-pad pulses; the Lean inputs
use the actual start-of-cycle logical pin view. The trace corpus covers
dividers 1/2/4, mask 0/1/255, both qualifier enables/polarities, enable gaps,
registered restart, PC writes, and seeded delay/halt/fault gates. Those seeded
states test the local transition without proving reachable global state.
Sixteen separate cold-reset projections matched newly constructed model
cores; they are **not** model reset-cycle tests.

Two in-memory Python mutations were rejected: sampling only on tick loses
the event on a non-tick pulse (first mismatch at clock 3), and failing to
clear stale first-entry history differs on the first attempted clock.
Counterexamples and exact source fingerprints are retained under team-root
`evidence/sol-euclid/C1-EUCLID-WAIT/validation-2/4.log`. The hash set includes
every loaded repository-local Python dependency, the Lean source and the
compiled event probe. Validation 1 stopped on a record-layout syntax error
before any model trace; there were two implementation versions and two
focused invocations, with no unsupported RTL campaign.

Supported host interference is restricted to PC cancellation or a previously
registered restart, never both at once. All other host register/configuration,
instruction-memory and FIFO writes, flags/other cores, program changes,
WAIT kinds 0/1/5/6, and bank-fault transitions are outside the experiment.
No PC, delay, divider, synchronization or asynchronous-reset state is in
the Lean projection. Enable levels are already-effective model state;
their host-write pipeline is not covered. Restart while disabled and PC
cancellation while disabled are not dynamically checked here. These gates
are excluded or supplied as observations, not assumed equivalent to RTL.

## Refinement gap and next proof candidates

The C2 extension below closes C1's observed execution-gate input for its
fixed-configuration subset. The C1 interface/table above remains its precise
historical contract; neither interface establishes RTL refinement.

The initial differential test is exhaustive over the selected timeout input
domain and uses level WAIT PIN1. The event extension adds bounded clock traces
and abstract tracker properties. Neither proves Python equivalent to Lean or
RTL. Decode/rotation and DIFF aliases have executable agreement, with formal
bitvector correspondence still open. Full execution gates, unrelated registers,
side-set, flags, FIFOs, concurrency, host priority and the remaining ISA stay
outside the proved relation. No RTL simulation, SymbiYosys job, hardening,
board access or SWD was performed. Existing formal and release evidence keeps
its own scope and input provenance.

Useful next bounded steps are to prove the tracker decoder's bitvector
correspondence; add an RTL-to-Lean relation for execution gates and host
priority; and connect retained encoded pulse/cancellation traces to a small,
lead-coordinated RTL lockstep slice. Reset/reload
proofs would separately address the competition FAQ's nonzero initial-flop
and power-down guidance. These are follow-up candidates, not established
claims in this deliverable.

## C2-EUCLID-GATING: closed execution permission

Starting from accepted C1 commit `03bc2a6fae0fbce630fb2989681a6329e568ea81`,
`Wirelingo/Gating.lean` adds a reusable projection containing C1's event/R/T
state plus `cnt`, `facc`, `dcnt`, `halted`, and `fault`. Configuration is fixed:
one WAIT RISE/FALL/EDGE/DIFF operand, `IN_BASE`, raw `DIV_INT/DIV_FRAC`, and
instruction delay. The PC is in its own valid bank and wraps to the same
WAIT; a PC cancellation writes that same address. No bank/fetch oracle is
introduced: those properties are declared domain restrictions.

The only cycle inputs are logical sampled pins, already-effective enable,
already-registered restart, and PC cancellation. No later model counters,
tick, exec, completion or projected state enter the transition. The model
profile used for comparisons is explicitly **two cores, two banks, 256 words
per bank, FIFO depth 8**. Only the selected core is enabled, with targets in
both banks represented across traces.

`gateActive = enable && !restart`. The transition derives `tick = cnt==0`
and `gateExecute = active && tick && !halted && !fault && dcnt==0` internally.
Every active clock advances the divider and C1 tracker, including while
halted or faulted. Eligible ticks decrement a positive delay; only a zero-delay
attempt can invoke the existing WAIT result and load instruction delay on
completion. PC cancellation overrides the resulting delay to zero, including
while disabled. Restart clears counters, delay, tracker, T and halt/fault
even while disabled, preserving R through C1's restart projection. Simultaneous
restart and PC cancellation is rejected by the probe contract.

The divider uses natural arithmetic with explicit width reduction:

```text
if cnt == 0:
    carry = (facc + DIV_FRAC) / 256
    facc' = (facc + DIV_FRAC) mod 256
    cnt' = (DIV_INT + 65535 + carry) mod 65536
else:
    cnt' = (cnt + 65535) mod 65536
    facc' = facc
```

This supports **raw DIV_INT=0**. Without carry it reloads 65535; with carry
it reloads 0, exactly as the source's 16-bit modular subtraction/addition.
No clock-rate interpretation or wall-clock termination theorem is claimed.

### C2 proofs and relation

Sixteen new theorems establish counter/fraction bounds, carry at most one
for valid 8-bit inputs, quotient/remainder conservation, fractional reload,
positive countdown, raw-zero behavior with/without carry, inactive/non-tick
execution exclusion, disabled freeze, active divider evolution, restart
priority, delay width and preservation of all four registers on every
non-attempt clock. `closed_sticky_completes` composes C1's sticky-event theorem
with the **derived** execution gate, establishing completion whenever an
eligible attempted WAIT has a sticky event. It does not assume completion.

| Closed projection | Source correspondence to investigate | Remaining obligation |
| --- | --- | --- |
| `divider.cnt/facc`, `dividerStep` | `cnt/facc`, `tick`, `fsum`, `cnt_next`; `Core._divider` | Bitvector/source refinement and fractional-divider timing theorem |
| `gateActive/gateExecute` | `active/attempt/exec`; `Core.step` gates | Own-bank/fetch/config domain relation, not bank-fault proof |
| `dcnt`, WAIT-derived completion | Delay branch/complete commit; `Core.step` | Complete instruction-fetch/delay/PC relation |
| `eventCycle` with derived permission | C1 tracker/WAIT source signals | RTL sequential correspondence; preserved C1 decode/rotation obligations |
| Effective enable/restart inputs | `enable/restart`, registered CTRL boundary | Actual bus capture/latency and competing host writes |
| Same-address PC cancellation | `host_pc_w`, final delay/latch priority | General PC values, fetch redirection and simultaneous restart/PC writes |

### C2 finite validation

From the repository root:

```sh
python3 formal/lean/validate_gating.py --evidence /tmp/wirelingo-gating-check
```

The focused script builds and audits the original 30 and new 16 theorems,
then runs only the C2 corpus. It does not rerun the broader C1/legacy model
corpora. `gating_probe` is stateful: command `0` initializes the fixed config
and raw state once; each subsequent command `1` supplies only four external
inputs. Its evolved state is held inside the executable, never overwritten
from model snapshots. Output includes predicted state and execution/completion
bits, compared against actual model state and model coverage-counter deltas.

The comparison independently advances the two-clock pad history to generate
the logical pin boundary, without reading `Chip.pins_in()` for probe inputs.
Actual `Chip.step` advances the corresponding pads/synchronizers. Initial
counter/register/flag values are declared cases, not freshly measured model
outputs. Physical synchronization and arbitrary pin-source behavior remain
outside the proved relation.

The final invocation passed in **6.61 seconds**: 432 deterministic 24-cycle
traces, 10,368 baseline model cycles plus 72 mutant cycles (**10,440 total**).
Integer dividers 1/2/4; fractional configurations 1/1, 2/128, 4/255; raw-zero
configurations 0/0, 0/128, 0/255; initial `facc` 0/128/254/255 and `cnt`
0/1/65535; delays; enable gaps; seeded halt/fault and restart recovery;
restart and PC cancellation while disabled; and short pulses between ticks
are included. Seeded states are local domain cases, not reachability claims.

Three new in-memory mutations were rejected: dropped fractional carry,
advancing the divider while disabled, and suppressing restart while disabled.
Concrete earliest mismatch prefixes and full dependency/probe fingerprints
are under team-root `evidence/sol-euclid/C2-EUCLID-GATING/`. The accepted C1
source packet and prior evidence hashes are recorded separately and unchanged.

Two bounded implementation corrections were used: replace an over-recursive
proof tactic with direct modular arithmetic, then rename a reserved Lean
identifier in the probe. The first two invocations stopped at compilation
with zero model cycles. The third passed; all three were below 90 seconds.
No further C2 run or implementation revision is authorized by this packet.

The result closes observed tick/exec feedback for this subset. It excludes
configuration/imem/register/FIFO writes, simultaneous restart/PC cancellation,
bank faults, general PC control, arbitrary instructions, side-set, concurrency,
asynchronous reset and CTRL bus latency. No source correspondence or Python
agreement is an RTL refinement proof. Next: a separate bounded bitvector/
RTL relation experiment for the decoder and derived gates, with fetch and
host-priority obligations made explicit.

## C4-EUCLID-RTL-GATE: finite native RTL correspondence

The C4 packet starts at accepted C2 commit
`8e47d0c9f201cab275e0f0c53eb6aaa1e588c9dc`. It adds a standalone Icarus
testbench that instantiates **the unchanged `src/pe_core.sv`**, with its real
`pe_fifo`, `pe_fifos` and behavioral `pe_cg` dependencies. `FIFO_LOG2=3` and
IDX0/IDX1 are separate elaborations. This tests the core boundary; there is
no substitute SV gate model, chip synchronizer, SRAM or host-port model.

The stable instruction input encodes one WAIT RISE/FALL/EDGE/DIFF. PC, entry
and both wrap endpoints select that core's bank and the same WAIT address.
The only later drives are logical `pins_in`, effective enable/restart, and
a same-address host PC write via `bus_sel/bus_we`, offset `0x08`. All other
bus/flag/line/FIFO inputs stay quiescent; side-set remains disabled. Joint
restart/PC write is excluded. Input words, little-endian instruction bytes,
initial states and every drive are frozen before tool execution.

Each trace begins with asynchronous reset and two reset rising edges. With
clock low after reset, the testbench **deposits configuration and projected
state once** through hierarchical procedural assignments. It never uses
`force/release`, and never writes internal state after that deposit. These
are declared raw-state cases, not reachability proofs: in particular the
fault=1/halted=0 seed violates the reachable core invariant; cnt=65535 and
delay=255 under the fixed small divider/delay are outside reset-reachable
states of this restricted configuration. Halt seeds may require an earlier
instruction outside the fixed-WAIT domain. Deposits bypass host programming,
reset-to-configuration timing and instruction-memory loading.

Sampling is explicit and race-free: drive ports with clock low, settle four
ns, capture pre-edge `exec` and **`exec && complete`**, raise clock, then wait
one ns past NBA updates before reading tracker, R/T, cnt/facc/dcnt and flags.
Unqualified `complete` is combinational decode and is not a completion event.
The accepted C2 `gating_probe` evolves independently from the same initial
state and external drives. RTL state/outputs never enter Lean or subsequent
test inputs. All predictions are computed before running the DUT traces.

### Actual source map and arithmetic relation

Line references below bind the pinned source hash in the C4 evidence packet.
`source-expression-map.json` retains the exact text and line numbers.

| RTL expression | Lean meaning / boundary |
| --- | --- |
| `op=instr[31:27]`, `sd=instr[26:19]`, `x=instr[18:0]` (169–171) | WAIT opcode 1, fixed 8-bit delay, 19-bit operand; side_count/side_opt=0 |
| `tick=cnt==16'd0`, 9-bit `fsum={0,facc}+{0,div_frac}`, 16-bit `cnt_next` (188–190) | Explicit 16-bit modular subtraction/reload and 8-bit fraction; carry is `fsum[8]` |
| `active=enable&&!restart`; `attempt=active&&tick&&!halted&&!fault&&dcnt==0`; `exec=attempt&&bank_ok` (210–213) | C2 derived gate under `pc[9:8]==IDX`; bank restriction is explicit |
| `wkind=x[18:16]`, `widx=x[15:11]`, qualifier and transition (196–201) | RISE/FALL/EDGE, selected pin and edge qualification |
| `wref=R[x[15:14]]`, `wdiff=((rotated_pins[7:0]^wref[7:0])&x[7:0])!=0`, `edge_done` (206–208) | DIFF reference/budget aliases use pre-write R; sticky event OR current condition |
| Edge `wbase=in_wait&&edge_done`; DIFF `in_wait?edge_done:wdiff`; timeout (335–344) | Edge first attempt ignores history; DIFF first attempt is level; event precedes timeout |
| WAIT decode R decrement and T update (427–443) | C1 attempted-WAIT semantics, invoked only through derived `exec` |
| `commit=exec&&complete`; restart/host-PC/commit fetch priority (657–663) | Qualified completion; fixed self-wrap and same-address cancellation exclude general fetch reasoning |
| Restart branch before active branch (755–772) | Clears divider/tracker/delay/T/halt/fault even with enable=0; R preserved |
| Active divider and tracker before tick/delay/exec gates (773–797) | Every active clock advances divider/tracker, including halt/fault and non-tick clocks; first entry clears stale event |
| Final `host_w` PC-write case (827–830) | Overrides dcnt/in_wait after core updates, including while disabled; tracker prev/event otherwise retained |

`Wirelingo/RtlGating.lean` gives four small arithmetic/gate lemmas. A **manual
Nat transcription** models the 9-bit sum modulo 512, carry bit as division
by 256 modulo 2, and 16-bit counter truncation modulo 65536. Under 8-bit
fraction inputs it proves the sum has no 9-bit overflow, bit 8 equals C2's
carry, the divider one-step equals `dividerStep`, and the gate equals
`gateExecute` under own-bank validity. Raw DIV_INT=0 is included through
modular subtraction. This is not a machine-checked translation of SV or a
sequential refinement theorem. Axiom audit found only `propext` and
`Quot.sound`; no sorry/new axioms. The 46 accepted lemmas remain unchanged;
the combined catalogue now has 50 lemmas, with the four new ones audited
separately in C4.

### Predeclared traces, native result and negative evidence

The corpus is exactly 64 positive traces: both IDX values, four WAIT kinds,
and eight C2-derived divider/state rows, each with 32 comparison clocks plus
two reset clocks. It covers integer 1/2/4, fractional 1/128/255, raw integer
zero with and without carry, facc near wrap, cnt=65535, delay, enable gaps,
seeded halt/fault and recovery, disabled restart/PC cancellation, DIFF
reference/budget aliases, edge qualifiers and short non-tick pulses.

Final invocation **PASS in 14.38 seconds**: 2,048 positive comparison clocks,
46 non-tick event captures, 24 disabled restart clocks and 24 disabled PC
cancellation clocks. Every selected projected state and pre-edge permission/
completion agrees with the unchanged compiled accepted Lean probe. A separate
one-clock **wrong-bank assumption violation** yields attempt=1, bank_ok=0,
exec=0, halted=1, fault=1 as expected; it is not a positive trace.

An actual RTL mutant, labeled non-product and compiled only from an evidence
copy, removes `{15'd0,fsum[8]}` from `cnt_next`. It is rejected at clock 0:
DIV_INT=2, DIV_FRAC=128, facc=128, cnt=0 predicts cnt'=2 but the mutant gives
1. The full source delta, instruction word `083a0461`, raw initial state and
one-clock failing drive prefix are retained. Real FIFO/clock-gate dependencies
stay unchanged in this negative compilation.

Commands/logs are retained under team-root
`evidence/sol-euclid/C4-EUCLID-RTL-GATE/validation-N/`; the focused driver is
historical C4 commands used `check_rtl_gating.py --invocation N`. That frozen
interface cannot replay its exhausted private directory; C5 below supplies
the corrected public replay command. The historical runner used a
finite-ticket ledger and refused existing invocation directories. Four
invocations and three corrections were used: proof simplification, evidence
namespace import setup, and compiler scratch placement. Invocations 1/2
failed before any RTL clock (6.86/4.71 seconds). Invocation 3 passed in 13.91
seconds but Icarus used its default ephemeral `/tmp/ivrl*` scratch; the final
invocation repeated the identical corpus with TMPDIR under C4 evidence.
Retained syscall logs verify final compiler file writes use that directory
(normal `/dev/null` and `/dev/tty` opens excluded). All permanent build/output
artifacts are under C4 evidence; accepted `.lake` objects/probe are read-only
inputs. No further C4 source revision or native run follows the freeze.

Across all invocations: **4,426 total RTL clocks**, including 264 reset clocks,
132 one-time deposits (no additional edge), both wrong-bank negatives and both
32-clock RTL-mutant traces; **zero incidental Python model cycles**. Actual
tool wrappers, loader, Icarus executables/compiler target/VPI modules and
loaded libraries are pinned from `strace` exec/read-open logs. The tool reports
Icarus 14.0-devel; exact binary hashes, not its version label, bind the check.

The result is finite RTL/Lean agreement at the declared boundary plus four
separate manually transcribed arithmetic lemmas. It excludes full-chip
synchronization, SRAM/registered-fetch timing, host serialization/CTRL latency,
arbitrary host writes/configuration/instructions, general PC/bank behavior,
FIFO operations, side-set, cross-core concurrency, asynchronous reset during
execution, joint restart/PC writes, reachability, physical timing/electrical
behavior and general sequential refinement. A next obligation is an RTL-state
relation preserved across arbitrary-length traces of this subset, followed
separately by fetch/control-boundary integration.

## C5-EUCLID-REPLAY: explicit, fresh replay interface

C5 corrects C4's replay plumbing. The original C4 checker/docs snapshots,
four invocation records, corpus, source map, bench and arithmetic lemmas remain
frozen in C4 evidence. The checker now resolves repository sources from its
own file and requires explicit output, probe, provenance, Icarus, Lean library/
object and strace paths. It has no private team/evidence/tool defaults and
does not read the old C2 result log. It performs no implicit accepted-probe
build, object replacement, dependency fetch or tool installation.

Prepare the read-only inputs before running: the existing Icarus compiler
and vvp executables (their runtime/loader dependencies must be available),
Lean 4.28 executable and standard library, strace, and the accepted C2
`gating_probe` plus its exact compiled object bundle. The ordinary object
root contains `Wirelingo/*.olean*`; a prepared repository may use
`formal/lean/.lake/build/lib/lean` and `.lake/build/bin/gating_probe`. An
explicit byte-identical copy of these artifacts is also valid. This checker
does not prepare/build them. Missing or differently built accepted inputs
are errors, not a request to silently regenerate a reference.

Supply the pinned schema1 `reference-provenance.json` from the C5 handoff
packet as an explicit input. It contains baseline
`8e47d0c9f201cab275e0f0c53eb6aaa1e588c9dc`, 29 repository source/config hashes,
the accepted probe hash and all six accepted object hashes. Its canonical
identity, checked in the source, is
`5155ceb41cc12ea9f9c533baaa7275430c43b9aaaa11ef63d976be3660137277`:
SHA-256 of JSON with sorted keys and compact separators. Receipt location
and JSON whitespace may change; identity contents may not. Baseline, actual
probe, repository sources and object set/bytes must all match before output
creation. The accepted probe hash remains
`17ccae47742796f268346790ce4e1388ae0684cc34cdaa4f26b673a762ea9c79`.
This is a deliberately pinned replay contract; a new reference/build needs
separate review and a new accepted identity.

From the repository root, with the listed inputs already prepared:

```sh
task_reference=/path/to/reference-provenance.json
task_probe=/path/to/accepted/gating_probe
task_objects=/path/to/accepted/object-root
task_icarus=/path/to/oss-cad-suite
task_lean=/path/to/lean-4.28.0
task_strace=/path/to/strace
mkdir -p /path/to/replay-parent
python3 formal/lean/check_rtl_gating.py \
  --output /path/to/replay-parent/rtl-replay-001 \
  --reference "$task_reference" --probe "$task_probe" \
  --iverilog "$task_icarus/bin/iverilog" --vvp "$task_icarus/bin/vvp" \
  --lean "$task_lean/bin/lean" --lean-lib "$task_lean/lib/lean" \
  --lean-objects "$task_objects" --strace "$task_strace"
```

The destination itself must **not exist**, even if empty; its parent must
already exist and be writable. Every path/identity check is read-only and
precedes mkdir/declaration. Argument errors, missing inputs, wrong identities,
existing destinations and dangling output symlinks reject without writing
there. Each fresh destination contains its own `input-receipt.json`, copied
provenance input, unchanged `predeclared-traces.json` and a single
`validation-1/` ledger. No old C4 ledger is read/reset/reused or its cap raised.
All compiler scratch uses the new invocation's TMPDIR. `--declare-only`
requires the same explicit valid inputs and fresh output contract, runs no
compiler/RTL/model, and creates a declaration-only directory; use another
fresh directory for an actual replay.

C5 is only CLI/provenance/output plumbing and documentation. ROWS, corpus,
input encoding, first mismatch, positive assertions, 64x32 predictions,
actual RTL mutant and theorem audit semantics remain unchanged. The C5
evidence packet retains an AST preservation audit and the old frozen packets'
before/after hashes. The exact fresh native argv, exits, TMPDIR, loaded runtime
hashes and deterministic row/counterexample comparison are retained there.
The native result and all core-boundary exclusions described in C4 still
apply; reproducibility does not establish broader RTL refinement.

The bounded correction passed: 18 grouped CLI/safety cases in 1.07 seconds
with zero compiler/RTL/model execution, then one fresh native replay in 14.19
seconds (14.08 seconds inside the checker). It used explicit byte-identical
accepted probe/object copies and a relocated supplied receipt. The unchanged
64x32 corpus produced 2,048 positive clocks, 46 non-tick captures and 24 each
disabled restarts/PC cancellations; the real RTL carry mutant still fails at
clock 0 and the wrong-bank diagnostic is unchanged. Total C5 clocks, including
132 reset edges and both negatives, are 2,213; 66 deposits add no edge and no
Python model is stepped. All 66 RTL row streams, Lean inputs/predictions,
corpus, mutant source/counterexample and assumption diagnostic exactly match
frozen C4. Only documented path/time/tool bookkeeping differs. Both focused
checks are consumed; no additional native replay or old C4 ledger change.


## C7: core side-set output contract

C7 adds seven manual Lean pin-effect lemmas and a standalone actual-core
comparison. `Wirelingo/SideSet.lean` is a manually written semantics, not a
machine-checked translation of SV. It normalizes count/base/sd to 3/5/8 bits,
clamps count to four, and selects each destination pin independently:
local bit j maps to `(base+j) mod 32`. Optional encoding reserves sd[7];
remaining side bits occupy the upper bits of sd above the delay field.
Completion is an intentionally unused argument of `sideEffect`. Output and
output-enable direction are separate channels. `sideExecute` composes the
accepted `gateExecute` with explicit bank validity. The bridge example proves
mandatory count2/base9/sd128 yields out mask `0x600`, value `0x400` with
false completion. The other lemmas state completion independence, no side
write without execution, optional disable, and direction separation.

The new `flagSideCycle` is a separately explicit narrow executable projection
for stable WAIT kind5/FLAG1, flag0, with qualifier, timeout and auto-clear bits
all zero. It does not call `gatingWaitResult` or `decodeKind`: C1/C2's proved
RISE/FALL/EDGE/DIFF contract is not extended to flags by this experiment.
Flag0 is sampled only for completion at an executing tick. The raw divider,
delay, fault/halt, PC and stalled projection is closed from one initial state
and external enable/restart/F0 drives, never later DUT counters or results.
No general sequential flag-WAIT theorem is claimed.

The actual `pe_core.sv` source is pinned at SHA-256
`00082387fba0b09c143e897e5c037d9d1784fa7e5893c7822ca100c7e71955e0`.
The source-to-semantics map is:

| Source signals/expressions | Manual projection / observation |
| --- | --- |
| instr[31:27], sd[26:19], x[18:0]; wkind/x[18:16], widx/x[15:11] | fixed op1, kind5, index0; exact word/LE bytes in corpus |
| cnt==0; active=enable&&!restart; attempt=active&&tick&&!halted&&!fault&&dcnt==0; bank_ok=pc[9:8]==IDX; exec=attempt&&bank_ok (210–213) | accepted gateExecute plus explicit bank check; pre-edge exec |
| sc=min(side_count,4), dbits=8-sc-side_opt; side_en=optional?sd[7]:1; side_v shifted/masked; rotl32 side_mask/side_val (216–224) | normalized fields and independent per-pin mask/value oracle |
| side_act=exec&&sc!=0&&side_en (222) | sideEffect; completion has no input to activation |
| W_FLAG1:wflag&&qual; OP_WAIT wbase/timeout branches (329–344,427–448) | unqualified timeout-free F0 completion, qualified pre-edge `exec&&complete` |
| pw_out/pw_oe default0; pw_data/out_data; pw_val_out/pw_val_oe (394–401,631–634) | WAIT has no main write; reset OSR0, autopull0 imply main bus values0 in this domain |
| side_to_out/side_to_oe; m_out/m_oe/s_mask; wr_out_en/val and wr_oe_en/val (635–644) | all four pre-edge buses; channel separation; no registered pad accumulator |
| fsum[8], 16-bit cnt_next, fsum[7:0] (202–209,774–775) | accepted dividerStep; post-NBA cnt/facc |
| restart first; else active; eligible tick delay decrement/bank fault/exec, stalled and complete delay load (754–797) | separate flagSideCycle; post-NBA dcnt/halt/fault/stalled |
| fetch_addr restart then host PC then commit; pc_seq/self-wrap (659–668); pc<=fetch_addr (753) | fixed entry/wrap/PC own-bank slot0; post-NBA PC; host writes excluded |

`rtl_sideset_tb.sv` instantiates unchanged core plus real FIFO/clock-gate
dependencies at FIFO_LOG2=3, primary IDX1 and one IDX0 control. Every trace
has an asynchronous reset event and two reset edges, followed by one
low-clock configuration/raw-state deposit. Thereafter only DUT input ports
change. Inputs settle for four ns, then all write buses/exec/qualified
completion are captured before the rising edge. State is captured one ns
after it, after NBA. No force/release or state feedback is used.

The predeclared corpus contains 57 own-bank one-step cases, one separately
classified wrong-bank assumption diagnostic, and two 20-clock sequences.
It covers stalled/completed WAIT with identical side value, mandatory and
optional side-set, count0/4/clamped7, base31 wrap, both channels, all execution
suppression gates, delay and raw halt/fault seeds. The div4 stalled sequence
expects repeated bridge writes on clocks0/4/8/12/16 with no completion;
the other crosses enable gaps, disabled restart and flag changes between ticks.
Total expected actual clocks including resets and the isolated RTL mutant:
221. The mutant changes the unique actual source expression from `exec` to
`exec&&complete` and must suppress the bridge buses on clock0; offline channel
swap and lost-wrap-bit mutations of captured rows must also be rejected.
Exact results, audit output, raw rows, commands and syscall dependency hashes
are retained in the frozen C7 evidence ledger; a failure is retained rather
than replacing the DUT or weakening the oracle.

Replay uses explicit existing inputs. Obtain the supplied schema1
`accepted-inputs.json` from the C7 evidence packet (copy it anywhere); its
canonical hash, baseline c77dd128, exact source and import-object identities
are enforced independently of receipt location. Existing accepted
Wait/Event/Gating objects and Lean4.28 standard library are prerequisites;
the checker does not run Lake or rebuild them. Select the real Icarus wrappers
and existing strace executable. Prepare only the parent of a fresh output
path. For example, from the repository root:

```sh
python3 formal/lean/check_rtl_sideset.py \
  --output /absolute/fresh-parent/c7-replay \
  --reference /absolute/input-packet/accepted-inputs.json \
  --iverilog /absolute/oss-cad-suite/bin/iverilog \
  --vvp /absolute/oss-cad-suite/bin/vvp \
  --lean /absolute/lean4-v4.28.0/bin/lean \
  --lean-lib /absolute/lean4-v4.28.0/lib/lean \
  --lean-objects /absolute/repository/formal/lean/.lake/build/lib/lean \
  --strace /absolute/existing/strace
```

All paths and identities are validated before destination creation; even an
existing empty destination is rejected. `--declare-only` follows the same
contract and performs no compiler/model/RTL clocks. The new module is compiled
into the fresh evidence root with exact accepted import copies, then its
closed executable projection is run through Lean's `--run`; no LLVM/native
Lean executable or existing .lake object is overwritten. Icarus binaries,
TMPDIR and syscall logs also stay in the fresh output. The <=90-second ledger
counts reset and captured RTL clocks, including negative probes/failures.

These are manual semantics theorems plus finite actual-core output evidence,
not universal RTL refinement. Count/base decoding, rotation and bus expression
correspondence are still a manual translation/trust boundary; the finite
checker/thebench/simulator/Lean runtime are trusted. Seed reachability is not
proved, including fault-without-halt and arbitrary divider/delay seeds.
Nonzero OSR/main pin data, other instructions/WAIT kinds, flag timeout,
qualifiers/auto-clear, arbitrary host/configuration writes, simultaneous
restart/PC writes, registered CTRL latency, registered fetch/SRAM, full-chip
flag arbitration, pin ownership muxes, pads/synchronizers, and physical timing
are excluded. Effective enable/restart/logical F0 are external boundary inputs.


Frozen C7 outcome: all seven new lemmas passed the axiom audit (only `propext`
where required); unchanged RTL matched all 98 predictions over 60 streams,
including 57 own-bank one-step cases, two 20-clock sequences and the one
wrong-bank diagnostic. Actual aggregate was 218 clocks including 120 reset
edges. The mutant compiler rejected the forward reference to `complete` at
line222 (its declaration is line357), so the required actual-RTL mutation
rejection has NOT been established. The offline mutations occur after that
compile in the checker and were not reached. Two earlier proof builds failed
on unused-simp-argument warnings; both diagnostics remain frozen. Three source
revisions and four focused checks are exhausted; no rerun is implied. Full
C7 acceptance is unmet. The next narrow obligation is a separately authorized
mutant-harness correction that preserves the expected bridge failure, followed
by the actual and offline negative controls. No semantic RTL disagreement was
observed on the unmodified declared corpus, and no product semantics changed.


## C8: disposable side-set mutant declaration binding

C8 corrects only the evidence-local RTL mutation generator. The early
initialized `side_act` declaration becomes `wire side_act;`; immediately after
the unchanged `reg complete, jump, new_wait;` declaration it adds exactly
`assign side_act = exec && complete && sc != 3'd0 && side_en;`.
Both exact source needles must occur once; the receipt records both edits.
No product statement, default_nettype, compiler option, positive input,
comparison assertion, Lean proof or testbench changes. C7's original sources,
patch, positive rows and elaboration failure remain immutable.

The fresh C8 checker invocation uses the same explicit-input command above,
with a new `--output` and the identical accepted provenance receipt. Its
historical internal5000-clock cap is unchanged; this ticket's external
acceptance cap is300, with expected221 clocks including resets and the actual
mutant. Only one native replay is authorized. The result ledger must show
seven identical lemma audits,60 unchanged positive/assumption streams and98
unchanged Lean rows, executed actual mutant failure on bridge clock0, both
offline negative controls rejected, and final checker exit0. The source/AST
check uses zero compiler/Lean/RTL/model executions. Exact commands, input and
prior-packet inventories, source deltas, raw rows and loaded runtime hashes
are in the separate frozen C8 packet. This adds negative-control evidence for
the finite core-bus slice; it does not establish universal SV refinement,
normal fetch, chip pin ownership, pads/electrical or whole-program behavior.


Frozen C8 result: PASS, final checker exit0 in15.818s. All60 original
positive/assumption streams and98 Lean rows, their inputs and the seven-lemma
axiom audit are byte-identical to frozen C7. The real completion-gated RTL
mutant elaborated and mismatched bridge clock0: expected out mask/value
0x600/0x400, observed0/0, with exec1 and qualified completion0 in both.
Channel-swap and lost-wrap offline controls were rejected at clock0. Aggregate
221 actual RTL clocks (122 reset edges,99 captured rows including mutant),
61 one-time deposits, zero model steps. Both focused checks and the single
implementation revision are consumed. All1450 prior packet files remain
byte-identical. C8 earns the missing bounded negative controls while preserving
C7's historical partial/failure evidence; host review/commit remains separate.


## C9: normally fetched WAITs and registered per-pin ownership

`PinOwnership.lean` adds six manually transcribed line-disabled per-pin
register lemmas: selected-owner update, nonowner noninterference, no-write
retention, absent/zero-mask retention, direct host POUT priority, and old-owner
use on the owner-configuration edge. Owner/pin/word inputs normalize to2/5/32
bits. Four core mask/value slots are explicit; slots2/3 are absent and zero
in this bench. The proofs do not translate SV or prove global sequential RTL
refinement. The closed executable reference reuses accepted `sideEffect` only
for zero-main-data, output side-set WAITs, and `sideExecute` with explicit own
bank validity. Its separate condition function covers exactly unqualified,
no-timeout/no-auto-clear FLAG1/FLAG0 F0, without C1/C2 event resolution.

The executable reference starts from reset, models one-cycle SRAM read/hold
and CTRL pending state, and consumes only the fixed bus schedule. It never
accepts DUT PCs, write buses, counters or outputs as prediction inputs.
Unknown SRAM/DOUT is explicit before loading; only inactive value buses with
unknown instruction in the declared cold phase are excluded from comparison.
Masks/registers/ports and known fetch words are still compared. All fields
must be known in execution phase1. The live bench has no deposits, force,
instruction injection, memory replacement or testbench pad accumulator.

Three positive streams and one exact actual-chip mux-mutant stream are
predeclared:43 owner-walk clocks,39 fetch/release/wrap/host clocks,50 div4
clocks, and43 mutant clocks, totaling175 actual edges including8 reset edges
and all loading/CTRL settling. Both cores are active together, loaded through
the documented direct chip bus while disabled. Direct bus is not serial host
transport evidence. Geometry is NCORES2/NBANKS2/BANK_AW8/FIFO_LOG2=3 with
unchanged pe_chip/core/imem/line/FIFO/gating sources and `test/sram_model.v`
on the real SRAM macro interface; PE_FLOP_IMEM is not defined.

Exact normally loaded two-word programs:

| Core | Address | Word | Instruction |
| --- | --- | --- | --- |
|0|0|0x0a050000|WAIT FLAG1 F0, mandatory side1|
|0|1|0x0c060000|WAIT FLAG0 F0, mandatory side2|
|1|256|0x0c050000|WAIT FLAG1 F0, mandatory side2|
|1|257|0x0a060000|WAIT FLAG0 F0, mandatory side1|

DIV integer1 (or4 in the last stream), fraction0; SIDE_COUNT2/SIDE_BASE9,
optional0/pindir0; ENTRY own bank slot0, WRAP slot0..1; SHIFTCTRL0 (OSR reset0,
autopull disabled), PINCTRL0=0. LINECFG0, OD_MASK0, owner-hi0, POE0x600,
initial POUT0x600, flags0. CTRL0x303 is written then its one-cycle restart/
enable latency observed before assertions. Setup is29 edges including reset.
All per-edge bus drives, exact words/LE bytes and independent predictions are
in the declaration/preparation packet. Active owner/POUT changes and host flag
set/clear are the specifically exercised exceptions to cold configuration.
Program banks and core configuration are never written while enabled.

The owner walk covers pairs(0,0)->0x200,(1,1)->0x400,(0,1)->0x600,(1,0)->0
under both-stalled FLAG1 preconditions, and absent owners(2,3) retaining0x600.
It verifies old owner on the configuration edge and new owner thereafter.
The release/wrap stream observes fetched FLAG0 side effects on the edge after
FLAG1 completes, a POUT/core-write overlap and subsequent core reapplication,
then wraps back to FLAG1. The div4 stream changes flags/owners between ticks.

| Actual source relation | Manual semantics / actual observation |
| --- | --- |
|pe_chip sel_imem/sel_core/sel_glob, ctrl_pend/ctrl_data and restart/enable|fixed bus decode and one-cycle pending CTRL in closed reference|
|pe_imem bank-local raddr, we/waddr, SRAM A_REN=!we; behavioral SRAM NBA DOUT/hold|unknown initial memory, normal loaded words, registered read/hold; pre/post instr and pre fetch captured|
|pe_core exec/complete/fetch_addr, own-bank PC and wrap|accepted execution gate plus separate narrow flag condition; pre-edge masks/qualified completion and post-PC/stalled/fault/halt|
|pe_chip pin_owner[2*p+:2], w_out_en/val selection|per-pin ownerBit/ownerWord, slots2/3 zero|
|pe_chip pin_owner_lo/hi clock-gated NBA configuration|new owner stored; old owner used in same-edge register update|
|pe_chip pin_out<=POUT host write?bus_wdata:pin_out_n|manual register host priority; actual post-NBA pin_out|
|line_mask overlay and OD_MASK|line-off/OD0 checked; pad synchronization/timing excluded|
|uo_out=eff_out[14:8]|logical pins9/10 correspond to actual uo_out[1]/[2], slices pin_out[10:9] and uo_out[2:1]|

The bench drives inputs with clock low, waits4ns, snapshots old owner/config,
instr/fetch and write masks/completion before the rising edge, then reads
actual pin_out/port after1ns and NBA. All edges, reset and loading included,
are counted. The exact NONPRODUCT mux mutant replaces both mask and value
owner references on the one output-selection statement with core0 references.
It must compile before behavioral execution and disagree at owner1's first
eligible edge29. Offline negative delays only the saved output/port sequence
one clock while leaving success/completion markers intact; it must disagree
at edge31. Syntax/assertion/runtime failures are failures, not passing negatives.

Replay requires explicit --phase prepare/native, fresh --output, --reference,
--iverilog/--vvp/--lean/--lean-lib/--lean-objects/--strace. The canonical accepted
receipt anchors baseline2de7a6a and exact sources/imports, independent of its
location. Existing accepted Wait/Event/Gating/SideSet objects are copied
read-only into new evidence, never rebuilt with Lake. Preparation elaborates
both actual RTL variants, compiles/audits the new module and computes all
predictions before native use. Native additionally requires --prepared and
--prepared-sha256, checks source/tool/artifact identity before creating its
fresh destination, and runs only the predeclared streams. Explicit executable
replay commands, runtime syscall file hashes and complete budget ledgers are
retained in NEW C9 evidence. All compiler output/TMPDIR stays there.

This slice has line-off, integer-divider/delay0, fixed two-word own-bank WAIT,
no qualifier/timeout/auto-clear or main pin write, reset OSR0/autopull0, bus
schedule and normal behavioral SRAM assumptions. It does not establish
universal instruction/fetch/RTL refinement, arbitrary host interference,
serial bus transport, CDC/pad synchronization/timing, SRAM electrical behavior,
full-chip concurrency beyond these two cores, physical or silicon correctness.


Frozen C9 outcome: FAILURE before native execution. Both original actual-chip
and evidence-local owner0 mux mutant elaborated successfully in each of two
preparation checks, using the unchanged behavioral SRAM macro path. The first
Lean build failed on three local-let rewrite goals and a record layout error.
The second source revision resolved those diagnostics, but selected-owner and
no-write lemmas still leave `if true=true` / `if false=true` goals unclosed.
Thus the new module has NOT compiled, the six declarations are NOT accepted
or axiom-audited theorems, and closed Lean predictions were not emitted.
No positive native rows, actual behavioral mux mutation rejection or offline
shift rejection exists. Planned175 clocks were not run: actual RTL/model
clock count0, state deposits0. Two source revisions and two focused checks
consumed; the other two checks remain unused because source revision budget
is exhausted. Failed logs/compile exits and all predeclared stimuli remain
immutable. Next decision is a separately authorized proof-only closure/audit
correction before the original native experiment; no C9 retry is inferred.


C10 proof closure and completed native result (reconciled October 13): the
previous C9 failure remains historical. The follow-up changed only the two
selected-owner/no-write proof bodies, adding explicit `if_pos`/`if_neg`
closure after the hypothesis rewrite. Definitions, theorem statements, other
proofs, audit driver, checker, bench, programs and bus schedules are unchanged.
Lean compiled and all six manual theorem audits passed: five have no axioms;
`owner_absent_mask_retains` uses `propext`, permitted by the existing policy.
No sorry, new axiom or native_decide was introduced.

The single preparation check passed in9.30s, elaborated original and actual
owner0 mux-mutant RTL, and emitted132 closed predictions before simulation.
The single native check passed in0.61s: original43/39/50-clock streams matched
all originally compared fields/phases, including reset/loading. The43-clock
actual mutant first disagreed at clock29: expected pin_out0x400/uo_out0x04,
observed0x200/0x02, with both WAITs executing but incomplete. The existing
offline control shifts the saved registered-output and external-port columns
together; it first disagreed at clock31 and preserves completion markers.
These are two compared channels in one saved-row mutation, not two independent
mutant simulations. Aggregate175 RTL clocks include8 reset edges; Python
model steps and hierarchical state deposits are zero.

Successful raw/predicted rows, axiom output, exact commands, explicit input
receipts, runtime syscall/hash maps and clock ledgers remain in immutable
`evidence/sol-euclid/C10-EUCLID-ITE-CLOSURE/`. The run completed before STOP
and was recovered without replay after the October13 host rebase. A narrow
current-checkout checker interface follow-up needs its own assignment; no
old budget is renewed. This establishes finite normal behavioral-SRAM fetch,
core-bus, registered ownership and logical-port correspondence in the declared
line-off/OD0/two-core WAIT domain. Manual source translation, universal RTL
refinement, serial-host transport, pad/electrical/CDC and release approval
remain outside the claim.


## IN8/autopush32 full-RXF boundary — frozen C11 preparation failure

The new `Wirelingo/RxAutopush.lean` proposes a manual finite-width transition:
ISR32/PC10, reachable count0..32, explicit effective enable/restart/tick/bank,
delay/halt/fault gate, and start-of-cycle full flag. Five declarations cover
inactive no-push, accepted below-threshold accumulation, threshold-ready
shift/push/clear, threshold-full ISR/count/PC retention, and conditional
two-execution full-pop/resume with the current resume byte. Restart clears
storage separately from disabled retention. These declarations have NOT
completed a module build or axiom audit; no accepted new proof is claimed.

| Actual source | Proposed manual/native boundary |
| --- | --- |
|pe_core n/in_bits, shared fs/in_isr, in_cnt_sum/in_cnt, push_th/in_autopush|IN PINS8, left/right BitVec32 arithmetic; count min32 and zero threshold encoding32|
|pe_core active/attempt/bank_ok/exec; OP_IN rx_block branch; exec isr_we update|explicit gate; full leaves complete0/isr_we0/core_rxpush0; PC holds, ISR retains|
|pe_fifos rx_block=fjoin OR rxf_full; pe_fifo do_push/pop from old cnt|FJOIN0/depth8, old-full pop bubble, queue update from old occupancy|
|pe_core fifo_sel, res_n/res_v/res_real, rd_real/rd_word, host_pop|two reservations; snapshot captures head/read-ahead, commit consumes real oldest, cancel drops reservations|
|pe_chip ctrl_pend/ctrl_data/enable/restart, sync1/sync2/pad_in|actual registered CTRL boundary and two-flop raw-pin pipeline; no bypass|
|pe_imem and unchanged behavioral SRAM macro|normal bus loading/fetch; four identical IN words in bank0, wrap0..3; unknown cold memory explicit|

The unmodified pe_chip witness uses2 cores/2 banks/256 words per bank/depth8,
core1 disabled. It has no deposits, force/release or injected instructions.
Input schedules are frozen before vvp: left/right96 clocks each, plus57-clock
left mutant prefix (planned249 total,6 reset edges, all setup included).
Program slots0..3 contain0x10020008 (`IN PINS,8`). Fixed raw byte at edgej is
(37*j+11)%256; sync2 feeds the IN two edges later. Config is div1/frac0,
autopush32, delay0, IN_BASE0, side_count0, CRC tap0, LINE0, FJOIN0, POE0.
After8 words fill RXF, three bytes accumulate to count24 while full; subsequent
threshold attempts stall. Cancelled one/two reservations consume no data.
A real commit at67 must leave the IN partial/PC intact and reduce occupancy
to7; edge68 must push exactly once and clear the partial. Planned ninth word
is0x6a8fb495 left or0x95b48f6a right, using the current resumed input rather
than the first stalled byte. Disabled partial retention and restart-while-
disabled with RXF preserved follow, then all9 words drain in order.

PRE observations settle4ns with clock low; POST storage/occupancy follows the
rising edge after1ns/NBA and actual latch FIFO settling. Thirty-four fields
are compared to closed Lean predictions, including push/data/ISR/count/PC,
full/pop/reservations, sync pipeline, and actual snapped/drained word order.
The evidence-local NONPRODUCT core mutant uniquely replaces the blocked-IN
branch with incorrect ISR/count writes; both original and mutant elaborate.
A behavioral mismatch at56 is required and has NOT been exercised.

C11 outcome: two preparation checks failed, before any axiom audit, closed
predictions or RTL simulation. First build: mutable record-field assignment at141.
Second/source-final build: multi-field record-update syntax at154:59,
`unexpected identifier; expected '}'`. Both actual RTL variants elaborated
exit0 in both checks. Actual RTL clocks/model steps/deposits0; planned249
clocks must not be reported as observed. Two source iterations and2/3 focused
checks consumed; native check is unused, with no implicit retry authorization.
The failed packet remains in `evidence/sol-euclid/C11-EUCLID-RX-AUTOPUSH/`.

Proposed maintained command after a separately authorized syntax closure:

```sh
python3 formal/lean/check_rtl_rx_autopush.py \
  --output /tmp/wirelingo-rx-autopush-review \
  --lean /path/to/lean4.28/bin/lean \
  --iverilog /path/to/iverilog --vvp /path/to/vvp
```

Installed Python3.10+/Lean4.28.0/Icarus/vvp/strace are required; tools may be
explicit or resolve from PATH. Fresh output lies outside checkout/library;
Std is installed and the new module builds into fresh scratch, without Lake,
downloads, shared objects or historical receipts. Separate prepare/native
phases bind exact sources, corpus, tools, read library inputs and artifacts;
native takes the prepared receipt SHA. Until compilation/audit/native pass,
this is a source/preparation artifact, not a shippable validated regression.
LINE/CRC/side-set/FJOIN/concurrent config/PC/register writes/FIFO clear and
arbitrary sampled reset/protocol semantics are excluded. The finite witness
uses the parallel configuration bus, not paid serial-host timing or SPI/UART
wire capture. Manual source correspondence/universal SV refinement and
physical/pad/silicon behavior remain unproved.


C12 syntax-only closure: putting the four-field `rxChipCycle` update on one
line resolved the Lean layout error. Whitespace-normalized semantic tokens,
all five theorem statements/proofs, audit, checker, bench, mutant and frozen
C11 corpus are unchanged. The C11 failure packet remains historical. Fresh
preparation passed10.52s: the module compiled, five manual lemmas audited
(each uses only allowed `propext`), and192 closed prediction rows emitted.
The sole grouped native check passed0.49s: two96-clock left/right settings
matched all34 fields and drained9 ordered words each, including the retained
partial plus current resume byte. The full host-pop edge67 left ISR/count/PC
unchanged and occupancy7; resume68 pushed once, cleared ISR/count and restored
occupancy8. Disabled partial retention and disabled restart clearing with
queued data retained also matched. The actual57-clock blocked-IN mutant first
diverged56: expected ISR0x006a8fb4/count24, mutant0x6a8fb4d9/count32; both
kept PC3/no push. Aggregate249 RTL clocks include6 reset edges;0model/deposits.
Exact input/source/tool/library/runtime pins, commands/ledgers, raw/predicted
rows, drain order and first counterexample are in NEW
`evidence/sol-euclid/C12-EUCLID-RX-SYNTAX/`. The maintained command above now
builds/runs this fixed regression from the checkout. This is manual-model plus
finite normal-fetch/FIFO/parallel-bus agreement, not universal SV refinement
or lossless wire capture during stalls; all original exclusions remain.
