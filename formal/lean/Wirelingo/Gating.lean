import Wirelingo.Event

namespace Wirelingo

structure Divider where
  cnt : Nat
  facc : Nat
  deriving DecidableEq, Repr

/-- Raw 16.8 divider: adding 65535 represents subtraction modulo 65536,
including div_int=0 with either fractional carry value. -/
def dividerStep (divInt divFrac : Nat) (s : Divider) : Divider :=
  if s.cnt == 0 then
    ⟨(divInt + 65535 + (s.facc + divFrac) / 256) % 65536,
      (s.facc + divFrac) % 256⟩
  else ⟨(s.cnt + 65535) % 65536, s.facc⟩

theorem divider_bounds (divInt divFrac : Nat) (s : Divider) (hf : s.facc < 256) :
    (dividerStep divInt divFrac s).cnt < 65536 ∧
    (dividerStep divInt divFrac s).facc < 256 := by
  unfold dividerStep
  split
  · exact ⟨Nat.mod_lt _ (by decide), Nat.mod_lt _ (by decide)⟩
  · exact ⟨Nat.mod_lt _ (by decide), hf⟩

theorem fractional_carry_bound (facc frac : Nat) (ha : facc < 256) (hf : frac < 256) :
    (facc + frac) / 256 ≤ 1 := by omega

theorem fractional_partition (facc frac : Nat) :
    (facc + frac) % 256 + 256 * ((facc + frac) / 256) = facc + frac := by omega

theorem fractional_reload (divInt divFrac : Nat) (s : Divider) (h : s.cnt = 0) :
    dividerStep divInt divFrac s =
      ⟨(divInt + 65535 + (s.facc + divFrac) / 256) % 65536,
        (s.facc + divFrac) % 256⟩ := by
  simp [dividerStep, h]

theorem positive_countdown (divInt divFrac : Nat) (s : Divider)
    (hz : 0 < s.cnt) (hb : s.cnt < 65536) :
    dividerStep divInt divFrac s = ⟨s.cnt - 1, s.facc⟩ := by
  have hn : (s.cnt == 0) = false := by simp; omega
  simp only [dividerStep, hn, Bool.false_eq_true, ↓reduceIte]
  have hm : (s.cnt + 65535) % 65536 = s.cnt - 1 := by omega
  rw [hm]

theorem raw_zero_without_carry (s : Divider) (frac : Nat)
    (hc : s.cnt = 0) (hs : s.facc + frac < 256) :
    (dividerStep 0 frac s).cnt = 65535 := by
  simp [dividerStep, hc, Nat.div_eq_of_lt hs]

theorem raw_zero_with_carry (s : Divider) (frac : Nat)
    (hc : s.cnt = 0) (hs : 256 ≤ s.facc + frac) (hb : s.facc + frac < 512) :
    (dividerStep 0 frac s).cnt = 0 := by
  have hd : (s.facc + frac) / 256 = 1 := by omega
  simp [dividerStep, hc, hd]

structure GatingConfig where
  operand : Nat
  inBase : Nat
  divInt : Nat
  divFrac : Nat
  delay : Nat
  deriving Repr

structure GatingState where
  event : EventState
  divider : Divider
  dcnt : Nat
  halted : Bool
  fault : Bool
  deriving DecidableEq, Repr

structure GatingInput where
  pins : Nat -- Logical pins at the sampled input boundary, not model output.
  enabled : Bool -- Already-effective CTRL enable; bus capture latency excluded.
  restart : Bool
  pcCancel : Bool -- Host PC write to the same fixed WAIT address.
  deriving Repr

def gateActive (i : GatingInput) : Bool := i.enabled && !i.restart

def gateExecute (s : GatingState) (i : GatingInput) : Bool :=
  gateActive i && s.divider.cnt == 0 && !s.halted && !s.fault && s.dcnt == 0

/-- Same condition and selected pre-write R as the immutable C1 event transition. -/
def gatingWaitResult (cfg : GatingConfig) (s : GatingState) (pins : Nat) : WaitResult :=
  waitAttempt (field cfg.operand 10 1 == 1)
    (resolveEvent (decodeKind cfg.operand) s.event.tracker
      (pinAt pins (field cfg.operand 11 5)) (qualOperand cfg.operand pins)
      (diffOperand s.event cfg.operand pins cfg.inBase))
    (readR s.event (field cfg.operand 8 2)) s.event.t

def gatingComplete (cfg : GatingConfig) (s : GatingState) (i : GatingInput) : Bool :=
  gateExecute s i && (gatingWaitResult cfg s i.pins).complete

/-- Closed cycle for a stable WAIT, valid own bank, self-wrapped PC and fixed
configuration. No later tick/exec/complete/counter oracle is accepted. -/
def gatingCycle (cfg : GatingConfig) (s : GatingState) (i : GatingInput) : GatingState :=
  let ev := eventCycle s.event {
    operand := cfg.operand
    pins := i.pins
    inBase := cfg.inBase
    enabled := i.enabled
    exec := gateExecute s i
    reset := false
    restart := i.restart
    pcWrite := i.pcCancel }
  let divider := if i.restart then ⟨0, 0⟩ else
    if gateActive i then dividerStep cfg.divInt cfg.divFrac s.divider else s.divider
  let delay := if i.restart then 0 else
    if gateActive i && s.divider.cnt == 0 && !s.halted && !s.fault then
      if s.dcnt != 0 then s.dcnt - 1 else
        if (gatingWaitResult cfg s i.pins).complete then cfg.delay else s.dcnt
    else s.dcnt
  { event := ev
    divider := divider
    dcnt := if i.pcCancel then 0 else delay
    halted := if i.restart then false else s.halted
    fault := if i.restart then false else s.fault }

theorem inactive_no_execution (s : GatingState) (i : GatingInput)
    (h : gateActive i = false) : gateExecute s i = false := by
  simp [gateExecute, h]

theorem non_tick_no_execution (s : GatingState) (i : GatingInput)
    (h : s.divider.cnt ≠ 0) : gateExecute s i = false := by
  simp [gateExecute, h]

theorem closed_restart (cfg : GatingConfig) (s : GatingState) (i : GatingInput)
    (h : i.restart = true) :
    (gatingCycle cfg s i).divider = ⟨0, 0⟩ ∧
    (gatingCycle cfg s i).dcnt = 0 ∧
    (gatingCycle cfg s i).event.tracker = emptyTracker ∧
    (gatingCycle cfg s i).halted = false ∧ (gatingCycle cfg s i).fault = false := by
  have he := restart_cycle_clears s.event
    ⟨cfg.operand, i.pins, cfg.inBase, i.enabled, gateExecute s i, false, i.restart, i.pcCancel⟩
    rfl h
  cases hp : i.pcCancel <;> simp_all [gatingCycle]

theorem closed_disabled (cfg : GatingConfig) (s : GatingState) (i : GatingInput)
    (he : i.enabled = false) (hr : i.restart = false) (hp : i.pcCancel = false) :
    gatingCycle cfg s i = s := by
  have ev := disabled_cycle_freezes s.event
    ⟨cfg.operand, i.pins, cfg.inBase, i.enabled, gateExecute s i, false, i.restart, i.pcCancel⟩
    rfl hr he hp
  simp_all [gatingCycle, gateActive]

theorem active_divider_evolves (cfg : GatingConfig) (s : GatingState) (i : GatingInput)
    (h : gateActive i = true) :
    (gatingCycle cfg s i).divider = dividerStep cfg.divInt cfg.divFrac s.divider := by
  have hr : i.restart = false := by
    unfold gateActive at h
    cases he : i.enabled <;> cases hh : i.restart <;> simp_all
  simp [gatingCycle, hr, h]

theorem closed_counter_bounds (cfg : GatingConfig) (s : GatingState) (i : GatingInput)
    (hc : s.divider.cnt < 65536) (hf : s.divider.facc < 256) :
    (gatingCycle cfg s i).divider.cnt < 65536 ∧
    (gatingCycle cfg s i).divider.facc < 256 := by
  have hd := divider_bounds cfg.divInt cfg.divFrac s.divider hf
  cases hr : i.restart <;> cases ha : gateActive i <;> simp_all [gatingCycle]

theorem no_attempt_preserves_registers (cfg : GatingConfig) (s : GatingState) (i : GatingInput)
    (h : gateExecute s i = false) :
    (gatingCycle cfg s i).event.r0 = s.event.r0 ∧
    (gatingCycle cfg s i).event.r1 = s.event.r1 ∧
    (gatingCycle cfg s i).event.r2 = s.event.r2 ∧
    (gatingCycle cfg s i).event.r3 = s.event.r3 := by
  cases hr : i.restart <;> cases hp : i.pcCancel <;>
    simp [gatingCycle, eventCycle, h, hr, hp, restartEvent, cancelEvent]

theorem delay_word_bound (cfg : GatingConfig) (s : GatingState) (i : GatingInput)
    (hs : s.dcnt < 256) (hc : cfg.delay < 256) :
    (gatingCycle cfg s i).dcnt < 256 := by
  simp only [gatingCycle]
  split
  · decide
  · split
    · decide
    · split
      · split
        · omega
        · split <;> assumption
      · exact hs

/-- Reuses C1's composition theorem with the derived execution gate. -/
theorem closed_sticky_completes (cfg : GatingConfig) (s : GatingState) (i : GatingInput)
    (hx : gateExecute s i = true)
    (hw : s.event.tracker.inWait = true) (he : s.event.tracker.event = true) :
    gatingComplete cfg s i = true := by
  have hr := sticky_completes_wait (decodeKind cfg.operand) s.event.tracker
    (pinAt i.pins (field cfg.operand 11 5)) (qualOperand cfg.operand i.pins)
    (diffOperand s.event cfg.operand i.pins cfg.inBase)
    (field cfg.operand 10 1 == 1) s.event.t (readR s.event (field cfg.operand 8 2)) hw he
  simp [gatingComplete, gatingWaitResult, hx, hr]

end Wirelingo
