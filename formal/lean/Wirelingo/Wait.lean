import Std

/-! WAIT's resolved-condition execution boundary, from v2 01ce16d:
docs/isa.md §4.2/§5.2, tools/pe/sim.py Core._op_wait/Core.step,
src/pe_core.sv OP_WAIT and pc_seq. No edge tracker or RTL refinement is assumed.
Natural budgets are accompanied by a proved 16-bit range invariant.
-/
namespace Wirelingo

structure WaitResult where
  complete : Bool
  budget : Nat
  t : Bool
  deriving DecidableEq, Repr

/-- One attempted WAIT, after condition resolution. Success wins at budget zero.
Without timeout, T and the selected register are never changed. -/
def waitAttempt (timeout condition : Bool) (budget : Nat) (t : Bool) : WaitResult :=
  if condition then ⟨true, budget, if timeout then false else t⟩
  else if timeout && budget == 0 then ⟨true, budget, true⟩
  else ⟨false, if timeout then budget - 1 else budget, t⟩

theorem success_priority (timeout : Bool) (budget : Nat) (t : Bool) :
    waitAttempt timeout true budget t =
      ⟨true, budget, if timeout then false else t⟩ := by
  simp [waitAttempt]

theorem expires_at_zero (t : Bool) :
    waitAttempt true false 0 t = ⟨true, 0, true⟩ := by
  simp [waitAttempt]

theorem positive_miss (budget : Nat) (t : Bool) :
    waitAttempt true false (budget + 1) t = ⟨false, budget, t⟩ := by
  simp [waitAttempt]

theorem untimed_miss (budget : Nat) (t : Bool) :
    waitAttempt false false budget t = ⟨false, budget, t⟩ := by
  simp [waitAttempt]

theorem budget_nonincreasing (timeout condition : Bool) (budget : Nat) (t : Bool) :
    (waitAttempt timeout condition budget t).budget ≤ budget := by
  cases timeout <;> cases condition <;> simp [waitAttempt]
  split <;> simp_all <;> omega

theorem budget_word_range (timeout condition : Bool) (budget : Nat) (t : Bool)
    (h : budget < 65536) :
    (waitAttempt timeout condition budget t).budget < 65536 := by
  have := budget_nonincreasing timeout condition budget t
  omega

/-- A timed WAIT stalls exactly for a missed condition with a positive budget. -/
theorem timed_stall_iff (condition : Bool) (budget : Nat) (t : Bool) :
    (waitAttempt true condition budget t).complete = false ↔
      condition = false ∧ 0 < budget := by
  cases condition <;> simp [waitAttempt]
  split <;> simp_all <;> omega

theorem stall_preserves_t (timeout condition : Bool) (budget : Nat) (t : Bool)
    (h : (waitAttempt timeout condition budget t).complete = false) :
    (waitAttempt timeout condition budget t).t = t := by
  cases timeout <;> cases condition <;> simp_all [waitAttempt]
  split at h <;> simp_all

/-- Run up to n unsuccessful timed attempts; stop permanently on completion.
`some b` means still waiting with budget b; `none` means timed out. -/
def afterMisses : Nat → Nat → Option Nat
  | 0, budget => some budget
  | n + 1, budget =>
    let r := waitAttempt true false budget false
    if r.complete then none else afterMisses n r.budget

/-- The first N misses all stall, leaving zero after the Nth miss.
This is also a prefix proof: n misses from budget n+k leave k. -/
theorem misses_leave_budget (n k : Nat) :
    afterMisses n (n + k) = some k := by
  induction n with
  | zero => simp [afterMisses]
  | succ n ih =>
    simpa [afterMisses, waitAttempt, Nat.add_assoc, Nat.add_comm, Nat.add_left_comm] using ih

/-- Timeout takes N+1 unsuccessful attempts, including the zero-budget attempt. -/
theorem timeout_after_budget_plus_one (budget : Nat) :
    afterMisses (budget + 1) budget = none := by
  induction budget with
  | zero => simp [afterMisses, waitAttempt]
  | succ budget ih => simpa [afterMisses, waitAttempt] using ih

/-- The same bound holds for arbitrary resolved conditions: successful conditions
complete earlier, and the all-miss trace is the longest possible trace. -/
def completesWithin : Nat → (Nat → Bool) → Nat → Bool
  | 0, _, _ => false
  | n + 1, conditions, budget =>
    let r := waitAttempt true (conditions 0) budget false
    r.complete || completesWithin n (fun i => conditions (i + 1)) r.budget

theorem completion_bound (budget : Nat) (conditions : Nat → Bool) :
    completesWithin (budget + 1) conditions budget = true := by
  induction budget generalizing conditions with
  | zero => cases h : conditions 0 <;> simp [completesWithin, waitAttempt, h]
  | succ budget ih =>
    cases h : conditions 0 with
    | false =>
      rw [completesWithin, h, positive_miss]
      exact ih (fun i => conditions (i + 1))
    | true =>
      rw [completesWithin, h, success_priority]
      rfl

structure WaitState where
  budget : Nat
  pc : Nat
  t : Bool
  dcnt : Nat
  inWait : Bool
  deriving DecidableEq, Repr

structure WaitConfig where
  timeout : Bool
  wrapTop : Nat
  wrapBottom : Nat
  delay : Nat
  deriving Repr

def nextPc (pc wrapTop wrapBottom : Nat) : Nat :=
  if pc == wrapTop then wrapBottom else (pc + 1) % 1024

/-- Project the attempted WAIT's result into the selected register, PC, T,
delay and in-progress latch. Other registers, side-set and flag-clear effects
are outside this projection. Call only at the instruction-attempt boundary. -/
def waitStep (cfg : WaitConfig) (condition : Bool) (s : WaitState) : Bool × WaitState :=
  let r := waitAttempt cfg.timeout condition s.budget s.t
  (r.complete, {
    budget := r.budget
    pc := if r.complete then nextPc s.pc cfg.wrapTop cfg.wrapBottom else s.pc
    t := r.t
    dcnt := if r.complete then cfg.delay else s.dcnt
    inWait := !r.complete })

theorem stall_frame (cfg : WaitConfig) (condition : Bool) (s : WaitState)
    (h : (waitStep cfg condition s).1 = false) :
    (waitStep cfg condition s).2.pc = s.pc ∧
    (waitStep cfg condition s).2.dcnt = s.dcnt ∧
    (waitStep cfg condition s).2.t = s.t ∧
    (waitStep cfg condition s).2.inWait = true := by
  have ht := stall_preserves_t cfg.timeout condition s.budget s.t h
  simp_all [waitStep]

theorem complete_advances (cfg : WaitConfig) (condition : Bool) (s : WaitState)
    (h : (waitStep cfg condition s).1 = true) :
    (waitStep cfg condition s).2.pc = nextPc s.pc cfg.wrapTop cfg.wrapBottom ∧
    (waitStep cfg condition s).2.dcnt = cfg.delay ∧
    (waitStep cfg condition s).2.inWait = false := by
  simp_all [waitStep]

theorem completed_wrap (cfg : WaitConfig) (condition : Bool) (s : WaitState)
    (h : (waitStep cfg condition s).1 = true) (hw : s.pc = cfg.wrapTop) :
    (waitStep cfg condition s).2.pc = cfg.wrapBottom := by
  have hp := (complete_advances cfg condition s h).1
  simpa [nextPc, hw] using hp

def StateBounded (s : WaitState) : Prop :=
  s.budget < 65536 ∧ s.pc < 1024 ∧ s.dcnt < 256

def ConfigBounded (cfg : WaitConfig) : Prop :=
  cfg.wrapTop < 1024 ∧ cfg.wrapBottom < 1024 ∧ cfg.delay < 256

theorem step_preserves_word_ranges (cfg : WaitConfig) (condition : Bool) (s : WaitState)
    (hs : StateBounded s) (hc : ConfigBounded cfg) :
    StateBounded (waitStep cfg condition s).2 := by
  have hb := budget_word_range cfg.timeout condition s.budget s.t hs.1
  have hp : nextPc s.pc cfg.wrapTop cfg.wrapBottom < 1024 := by
    unfold nextPc
    split
    · exact hc.2.1
    · exact Nat.mod_lt _ (by decide)
  cases h : (waitAttempt cfg.timeout condition s.budget s.t).complete <;>
    simp_all [waitStep, StateBounded, ConfigBounded]

end Wirelingo
