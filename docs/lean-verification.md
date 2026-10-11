# Lean verification of WAIT execution

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
