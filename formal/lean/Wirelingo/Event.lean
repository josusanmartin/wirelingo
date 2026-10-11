import Wirelingo.Wait

namespace Wirelingo

/-- Supported WAIT kinds, corresponding to wkind 2,3,4,7. -/
inductive EventKind where
  | rise | fall | edge | diff
  deriving DecidableEq, Repr

structure Tracker where
  inWait : Bool
  prev : Bool
  event : Bool
  deriving DecidableEq, Repr

def emptyTracker : Tracker := ⟨false, false, false⟩

/-- trans && qual for edges; wdiff for DIFF (qualifier bits alias mask). -/
def eventNow (kind : EventKind) (prev pin qual diff : Bool) : Bool :=
  match kind with
  | .rise => !prev && pin && qual
  | .fall => prev && !pin && qual
  | .edge => (prev != pin) && qual
  | .diff => diff

/-- wbase: first-entry edges never resolve; first-entry DIFF is a level. -/
def resolveEvent (kind : EventKind) (s : Tracker) (pin qual diff : Bool) : Bool :=
  let ev := eventNow kind s.prev pin qual diff
  match kind with
  | .diff => if s.inWait then s.event || ev else diff
  | _ => s.inWait && (s.event || ev)

/-- Every active clock samples the tracker, even without an execution attempt. -/
def sampleEvent (s : Tracker) (enabled observed pin : Bool) : Tracker :=
  if enabled && s.inWait then { s with prev := pin, event := s.event || observed }
  else s

/-- Execution latch update; first stalled entry overrides any stale event. -/
def commitEvent (before sampled : Tracker) (pin complete : Bool) : Tracker :=
  if !complete && !before.inWait then ⟨true, pin, false⟩
  else { sampled with inWait := !complete }

def cancelEvent (s : Tracker) (pcWrite : Bool) : Tracker :=
  if pcWrite then { s with inWait := false } else s

theorem disabled_sample (s : Tracker) (ev pin : Bool) :
    sampleEvent s false ev pin = s := by
  simp [sampleEvent]

theorem sticky_retained (s : Tracker) (ev pin : Bool)
    (hw : s.inWait = true) (he : s.event = true) :
    (sampleEvent s true ev pin).event = true := by
  simp [sampleEvent, hw, he]

theorem sample_without_wait (s : Tracker) (enabled ev pin : Bool)
    (h : s.inWait = false) : sampleEvent s enabled ev pin = s := by
  simp [sampleEvent, h]

theorem first_edge_ignores_history (s : Tracker) (pin qual diff : Bool)
    (h : s.inWait = false) :
    resolveEvent .rise s pin qual diff = false ∧
    resolveEvent .fall s pin qual diff = false ∧
    resolveEvent .edge s pin qual diff = false := by
  simp [resolveEvent, h]

theorem first_diff_is_level (s : Tracker) (pin qual diff : Bool)
    (h : s.inWait = false) : resolveEvent .diff s pin qual diff = diff := by
  simp [resolveEvent, h]

theorem qualifier_blocks_new_edges (prev pin diff : Bool) :
    eventNow .rise prev pin false diff = false ∧
    eventNow .fall prev pin false diff = false ∧
    eventNow .edge prev pin false diff = false := by
  simp [eventNow]

theorem diff_ignores_qualifier (prev pin qual diff : Bool) :
    eventNow .diff prev pin qual diff = diff := by
  rfl

theorem sticky_resolves (kind : EventKind) (s : Tracker) (pin qual diff : Bool)
    (hw : s.inWait = true) (he : s.event = true) :
    resolveEvent kind s pin qual diff = true := by
  cases kind <;> simp [resolveEvent, hw, he]

/-- Composition with the existing WAIT theorem, including zero budget. -/
theorem sticky_completes_wait (kind : EventKind) (s : Tracker)
    (pin qual diff timeout t : Bool) (budget : Nat)
    (hw : s.inWait = true) (he : s.event = true) :
    waitAttempt timeout (resolveEvent kind s pin qual diff) budget t =
      ⟨true, budget, if timeout then false else t⟩ := by
  rw [sticky_resolves kind s pin qual diff hw he]
  exact success_priority timeout budget t

theorem first_stall_clears (before sampled : Tracker) (pin : Bool)
    (h : before.inWait = false) :
    commitEvent before sampled pin false = ⟨true, pin, false⟩ := by
  simp [commitEvent, h]

theorem completion_clears_latch (before sampled : Tracker) (pin : Bool) :
    (commitEvent before sampled pin true).inWait = false ∧
    (commitEvent before sampled pin true).event = sampled.event := by
  simp [commitEvent]

theorem pc_write_cancels_without_erasing (s : Tracker) :
    (cancelEvent s true).inWait = false ∧
    (cancelEvent s true).prev = s.prev ∧
    (cancelEvent s true).event = s.event := by
  simp [cancelEvent]

structure EventState where
  tracker : Tracker
  r0 : Nat
  r1 : Nat
  r2 : Nat
  r3 : Nat
  t : Bool
  deriving DecidableEq, Repr

def readR (s : EventState) (idx : Nat) : Nat :=
  match idx % 4 with
  | 0 => s.r0
  | 1 => s.r1
  | 2 => s.r2
  | _ => s.r3

def writeR (s : EventState) (idx value : Nat) : EventState :=
  match idx % 4 with
  | 0 => { s with r0 := value }
  | 1 => { s with r1 := value }
  | 2 => { s with r2 := value }
  | _ => { s with r3 := value }

def field (x lo width : Nat) : Nat := (x / 2 ^ lo) % 2 ^ width
def pinAt (pins idx : Nat) : Bool := field pins idx 1 == 1
def rotatePins (pins base : Nat) : Nat :=
  (pins / 2 ^ base + pins * 2 ^ (32 - base)) % 2 ^ 32

def decodeKind (x : Nat) : EventKind :=
  match field x 16 3 with
  | 2 => .rise
  | 3 => .fall
  | 4 => .edge
  | _ => .diff -- probe accepts only 2,3,4,7

/-- x[15:14] reads the original register file, including alias with x[9:8]. -/
def diffOperand (s : EventState) (x pins base : Nat) : Bool :=
  Nat.land (Nat.xor (rotatePins pins base % 256) (readR s (field x 14 2) % 256))
    (x % 256) != 0

def qualOperand (x pins : Nat) : Bool :=
  field x 6 1 == 0 || (pinAt pins (field x 0 5) == (field x 5 1 == 1))

structure EventSignals where
  operand : Nat
  pins : Nat
  inBase : Nat
  enabled : Bool
  exec : Bool -- tick && !halted && !fault && dcnt==0 && bank_ok; WAIT fetch stable
  reset : Bool
  restart : Bool
  pcWrite : Bool
  deriving Repr

def restartEvent (s : EventState) : EventState :=
  { s with tracker := emptyTracker, t := false }

def resetEvent : EventState := ⟨emptyTracker, 0, 0, 0, 0, false⟩

/-- Projection of one core cycle. Unsupported host writes are excluded by the
probe contract, not modeled as harmless. Simultaneous restart/PC write is excluded.
Reset is a projected cold-reset definition, not a Python rst_n-cycle claim. -/
def eventCycle (s : EventState) (i : EventSignals) : EventState :=
  if i.reset then resetEvent
  else
    let out := if i.restart then restartEvent s else
      let kind := decodeKind i.operand
      let pin := pinAt i.pins (field i.operand 11 5)
      let qual := qualOperand i.operand i.pins
      let diff := diffOperand s i.operand i.pins i.inBase
      let observed := eventNow kind s.tracker.prev pin qual diff
      let sampled := sampleEvent s.tracker i.enabled observed pin
      if i.enabled && i.exec then
        let result := waitAttempt (field i.operand 10 1 == 1)
          (resolveEvent kind s.tracker pin qual diff)
          (readR s (field i.operand 8 2)) s.t
        let changed := writeR s (field i.operand 8 2) result.budget
        { changed with
          t := result.t
          tracker := commitEvent s.tracker sampled pin result.complete }
      else { s with tracker := sampled }
    { out with tracker := cancelEvent out.tracker i.pcWrite }

theorem reset_cycle_priority (s : EventState) (i : EventSignals)
    (h : i.reset = true) : eventCycle s i = resetEvent := by
  simp [eventCycle, h]

theorem restart_cycle_clears (s : EventState) (i : EventSignals)
    (hr : i.reset = false) (hs : i.restart = true) :
    (eventCycle s i).tracker = emptyTracker ∧ (eventCycle s i).t = false := by
  cases hp : i.pcWrite <;> simp [eventCycle, hr, hs, hp, restartEvent, cancelEvent, emptyTracker]

theorem disabled_cycle_freezes (s : EventState) (i : EventSignals)
    (hr : i.reset = false) (hs : i.restart = false)
    (he : i.enabled = false) (hp : i.pcWrite = false) : eventCycle s i = s := by
  simp [eventCycle, hr, hs, he, hp, sampleEvent, cancelEvent]

end Wirelingo
