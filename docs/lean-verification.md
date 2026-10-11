# Lean verification of WAIT execution

This adds an executable Lean specification and 15 kernel-checked theorems
for the **resolved-condition WAIT instruction-attempt boundary**. It establishes
timeout progress and selected state invariants for that specification. An
executable differential check connects it to the Python instruction model.
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

## Refinement gap and next proof candidates

The differential test is exhaustive over the selected timeout input domain,
but uses one level-WAIT condition realization. It does not prove the Python
model equivalent to Lean or RTL. Resolved conditions are inputs: pin rotation,
qualifiers, edge tracking, sticky events between ticks, kind-7 register aliasing,
and flag-clear effects are unproved. Other registers, side-set, shared flags,
FIFOs, concurrent cores, reset/host priority and the rest of the ISA are outside
the Lean projection. No RTL simulation, SymbiYosys job, hardening, board access
or SWD was performed for this deliverable. Existing formal and release evidence
keeps its own scope and input provenance.

Useful next bounded steps are to formalize the all-cycle WAIT edge/event
tracker and decode, then prove its composition with the timeout kernel; add
an RTL-to-Lean state relation including execution gates and host priority;
and connect selected encoded traces to RTL lockstep tests. Reset/reload
proofs would separately address the competition FAQ's nonzero initial-flop
and power-down guidance. These are follow-up candidates, not established
claims in this deliverable.
