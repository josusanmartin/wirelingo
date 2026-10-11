import Wirelingo.Gating

namespace Wirelingo

/-- Manual semantics, not a generated translation of SV. Inputs are normalized
to the RTL widths: count=3, base=5, sd=8 bits. Count is then clamped to four.
The value-bus contract below assumes WAIT's main pin data (OSR) is zero. -/
structure SideConfig where
  count : Nat
  base : Nat
  optional : Bool
  pindir : Bool
  sd : Nat
  deriving Repr

def sideCount (c : SideConfig) : Nat := min (c.count % 8) 4
def sideDelayBits (c : SideConfig) : Nat := 8 - sideCount c - c.optional.toNat
def sideDelay (c : SideConfig) : Nat := (c.sd % 256) % (2 ^ sideDelayBits c)
def sideEnabled (c : SideConfig) : Bool := !c.optional || decide (128 ≤ c.sd % 256)

/-- Independent per-pin selection: local bit j goes to (base+j) mod 32.
No rotation/mask expression from the RTL is evaluated here. -/
def sideOffset (c : SideConfig) (pin : Nat) : Nat := (pin + 32 - c.base % 32) % 32
def sideWord (c : SideConfig) (value : Bool) : Nat :=
  ((List.range 32).foldl (fun word pin =>
    let j := sideOffset c pin
    if j < sideCount c &&
        (!value || (c.sd % 256 / 2 ^ (sideDelayBits c + j)) % 2 == 1)
    then word + 2 ^ pin else word) 0) % (2 ^ 32)

structure SideWrites where
  outEn : Nat
  outVal : Nat
  oeEn : Nat
  oeVal : Nat
  deriving DecidableEq, Repr

/-- Completion deliberately has no effect on side-set. This returns the full
four buses only in the declared WAIT/zero-main-data domain. -/
def sideEffect (c : SideConfig) (execute : Bool) (_complete : Bool) : SideWrites :=
  if execute && sideCount c != 0 && sideEnabled c then
    if c.pindir then ⟨0, 0, sideWord c false, sideWord c true⟩
    else ⟨sideWord c false, sideWord c true, 0, 0⟩
  else ⟨0, 0, 0, 0⟩

/-- gateExecute's own-bank boundary is explicit rather than an implicit axiom. -/
def sideExecute (s : GatingState) (i : GatingInput) (bankOk : Bool) : Bool :=
  gateExecute s i && bankOk

theorem side_completion_independent (c : SideConfig) (ex a b : Bool) :
    sideEffect c ex a = sideEffect c ex b := by rfl

theorem side_no_execution (c : SideConfig) (done : Bool) :
    sideEffect c false done = ⟨0, 0, 0, 0⟩ := by simp [sideEffect]

theorem side_bridge_stalled :
    sideEffect ⟨2, 9, false, false, 128⟩ true false = ⟨1536, 1024, 0, 0⟩ := by
  decide

theorem side_optional_disabled (c : SideConfig) (ex done : Bool)
    (ho : c.optional = true) (hs : c.sd % 256 < 128) :
    sideEffect c ex done = ⟨0, 0, 0, 0⟩ := by
  simp [sideEffect, sideEnabled, ho, Nat.not_le.mpr hs]

theorem side_direction_only (c : SideConfig) (ex done : Bool)
    (hd : c.pindir = true) :
    (sideEffect c ex done).outEn = 0 ∧ (sideEffect c ex done).outVal = 0 := by
  unfold sideEffect
  rw [if_pos hd]
  split <;> exact ⟨rfl, rfl⟩

theorem side_output_only (c : SideConfig) (ex done : Bool)
    (hd : c.pindir = false) :
    (sideEffect c ex done).oeEn = 0 ∧ (sideEffect c ex done).oeVal = 0 := by
  unfold sideEffect
  have hn : ¬ c.pindir = true := by rw [hd]; decide
  rw [if_neg hn]
  split <;> exact ⟨rfl, rfl⟩

theorem side_own_bank_gate (s : GatingState) (i : GatingInput) :
    sideExecute s i true = gateExecute s i := by simp [sideExecute]

/-- Separately explicit narrow FLAG1/F0 cycle. This does NOT call
gatingWaitResult or decodeKind, whose accepted domain excludes flags.
Fixed WAIT kind5, flag index0, no qualifier/timeout/auto-clear, stable instruction,
own-bank self-wrap (except the separately diagnosed bank violation), no host
writes. Unreachable raw seeds are permitted as test-domain inputs. -/
structure FlagSideConfig where
  side : SideConfig
  idx : Nat
  divInt : Nat
  divFrac : Nat
  deriving Repr

structure FlagSideState where
  divider : Divider
  dcnt : Nat
  halted : Bool
  fault : Bool
  stalled : Bool
  pc : Nat
  deriving Repr

def flagGateState (s : FlagSideState) : GatingState :=
  ⟨⟨emptyTracker, 0, 0, 0, 0, false⟩, s.divider, s.dcnt, s.halted, s.fault⟩

def flagExecute (c : FlagSideConfig) (s : FlagSideState) (i : GatingInput) : Bool :=
  sideExecute (flagGateState s) i (s.pc / 256 == c.idx)

def flagSideCycle (c : FlagSideConfig) (s : FlagSideState)
    (i : GatingInput) (f0 : Bool) : FlagSideState :=
  if i.restart then ⟨⟨0, 0⟩, 0, false, false, false, c.idx * 256⟩ else
  if gateActive i then
    let eligible := s.divider.cnt == 0 && !s.halted && !s.fault
    let bad := eligible && s.dcnt == 0 && s.pc / 256 != c.idx
    let ex := flagExecute c s i
    { divider := dividerStep c.divInt c.divFrac s.divider
      dcnt := if eligible then
        if s.dcnt != 0 then s.dcnt - 1 else
          if ex && f0 then sideDelay c.side else s.dcnt
        else s.dcnt
      halted := s.halted || bad
      fault := s.fault || bad
      stalled := if bad then false else if ex then !f0 else s.stalled
      pc := if ex && f0 then c.idx * 256 else s.pc }
  else s

end Wirelingo
