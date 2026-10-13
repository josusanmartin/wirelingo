import Wirelingo.SideSet

namespace Wirelingo

/-- Manual line-disabled register semantics. Pin/owner/word inputs normalize
to 5/2/32 bits. Four core slots exist; absent cores supply zero masks. -/
def ownerSlot (owner : Nat) : Fin 4 := ⟨owner % 4, Nat.mod_lt _ (by decide)⟩
def ownerWordBit (word pin : Nat) : Bool :=
  ((word % (2 ^ 32)) / (2 ^ (pin % 32))) % 2 == 1

def ownerBit (before : Bool) (owner pin : Nat) (writes : Fin 4 → SideWrites) : Bool :=
  let w := writes (ownerSlot owner)
  if ownerWordBit w.outEn pin then ownerWordBit w.outVal pin else before

structure OwnedPin where
  value : Bool
  owner : Nat
  deriving Repr

/-- New configuration is stored at this edge, while the value uses OLD owner.
Direct host POUT replacement wins. Line overlay is excluded explicitly. -/
def registerOwnerBitLineOff (s : OwnedPin) (pin : Nat) (writes : Fin 4 → SideWrites)
    (newOwner : Option Nat) (host : Option Bool) : OwnedPin :=
  ⟨host.getD (ownerBit s.value s.owner pin writes), (newOwner.getD s.owner) % 4⟩

theorem owner_selected_update (before : Bool) (owner pin : Nat) (w : Fin 4 → SideWrites)
    (h : ownerWordBit (w (ownerSlot owner)).outEn pin = true) :
    ownerBit before owner pin w = ownerWordBit (w (ownerSlot owner)).outVal pin := by
  dsimp only [ownerBit]
  rw [h]
  rw [if_pos rfl]

theorem owner_nonowner_noninterference (before : Bool) (owner pin : Nat)
    (w : Fin 4 → SideWrites) (other : Fin 4) (replacement : SideWrites)
    (h : ownerSlot owner ≠ other) :
    ownerBit before owner pin (fun j => if j = other then replacement else w j) =
      ownerBit before owner pin w := by
  dsimp only [ownerBit]
  rw [if_neg h]

theorem owner_no_write_retains (before : Bool) (owner pin : Nat) (w : Fin 4 → SideWrites)
    (h : ownerWordBit (w (ownerSlot owner)).outEn pin = false) :
    ownerBit before owner pin w = before := by
  dsimp only [ownerBit]
  rw [h]
  rw [if_neg (by decide : ¬ false = true)]

theorem owner_absent_mask_retains (before : Bool) (owner pin : Nat)
    (w : Fin 4 → SideWrites) (h : (w (ownerSlot owner)).outEn = 0) :
    ownerBit before owner pin w = before := by
  apply owner_no_write_retains
  rw [h]
  simp [ownerWordBit]

theorem owner_host_priority (s : OwnedPin) (pin : Nat) (w : Fin 4 → SideWrites)
    (cfg : Option Nat) (hostValue : Bool) :
    (registerOwnerBitLineOff s pin w cfg (some hostValue)).value = hostValue := by rfl

theorem owner_old_configuration_edge (s : OwnedPin) (pin newOwner : Nat)
    (w : Fin 4 → SideWrites) :
    (registerOwnerBitLineOff s pin w (some newOwner) none).value =
      ownerBit s.value s.owner pin w ∧
    (registerOwnerBitLineOff s pin w (some newOwner) none).owner = newOwner % 4 := by
  exact ⟨rfl, rfl⟩

def ownerAt (lo hi pin : Nat) : Nat :=
  (if pin % 32 < 16 then lo else hi) / (2 ^ (2 * (pin % 16))) % 4

def ownerWord (before lo hi : Nat) (w : Fin 4 → SideWrites) : Nat :=
  ((List.range 32).foldl (fun word pin =>
    if ownerBit (ownerWordBit before pin) (ownerAt lo hi pin) pin w
    then word + 2 ^ pin else word) 0) % (2 ^ 32)

/-- Closed executable projection for the declared bus-loaded two-word programs.
Only WAIT FLAG1/FLAG0 F0, no timeout/qualifier/auto-clear, delay0, integer
divider, output side-set, reset OSR0/autopull0. No old event resolution is used.
This is NOT a theorem of fetch/core/chip refinement. Unknown SRAM is explicit. -/
structure FetchCore where
  pc : Nat
  cnt : Nat
  stalled : Bool
  divInt : Nat
  entry : Nat
  bottom : Nat
  top : Nat
  count : Nat
  base : Nat
  instr : Option Nat
  deriving Repr

def resetFetchCore (idx : Nat) : FetchCore :=
  ⟨idx * 256, 0, false, 1, idx * 256, 0, 1023, 0, 0, none⟩

structure FetchChip where
  c0 : FetchCore
  c1 : FetchCore
  mem0 : Array (Option Nat)
  mem1 : Array (Option Nat)
  enabled : Nat
  ctrlPend : Bool
  ctrlData : Nat
  f0 : Bool
  ownerLo : Nat
  ownerHi : Nat
  pinOut : Nat
  pinOe : Nat
  deriving Repr

def coldFetchChip : FetchChip :=
  ⟨resetFetchCore 0, resetFetchCore 1, Array.replicate 256 none,
    Array.replicate 256 none, 0, false, 0, false, 0, 0, 0, 0⟩

structure FetchBus where
  resetN : Bool
  we : Bool
  addr : Nat
  data : Nat
  deriving Repr

def busWrite (b : FetchBus) (addr : Nat) : Bool := b.we && b.addr == addr
def coreEnabled (s : FetchChip) (idx : Nat) : Bool := (s.enabled / 2 ^ idx) % 2 == 1
def coreRestart (s : FetchChip) (idx : Nat) : Bool :=
  s.ctrlPend && (s.ctrlData / 2 ^ (8 + idx)) % 2 == 1

def fetchExecute (s : FetchChip) (c : FetchCore) (idx : Nat) : Bool :=
  sideExecute (flagGateState ⟨⟨c.cnt, 0⟩, 0, false, false, c.stalled, c.pc⟩)
    ⟨0, coreEnabled s idx, coreRestart s idx, false⟩ (c.pc / 256 == idx)

def fetchCondition (c : FetchCore) (f0 : Bool) : Bool :=
  match c.instr with
  | none => false
  | some word => if word / 65536 % 8 == 5 then f0 else !f0

def nextFetch (s : FetchChip) (c : FetchCore) (idx : Nat) : Nat :=
  if coreRestart s idx then c.entry else
    if fetchExecute s c idx && fetchCondition c s.f0 then
      if c.pc == c.top then c.bottom else (c.pc + 1) % 1024
    else c.pc

def fetchSideWrites (s : FetchChip) (c : FetchCore) (idx : Nat) : SideWrites :=
  sideEffect ⟨c.count, c.base, false, false, c.instr.getD 0 / 524288 % 256⟩
    (fetchExecute s c idx) (fetchExecute s c idx && fetchCondition c s.f0)

def fetchedCoreCycle (s : FetchChip) (c : FetchCore) (idx : Nat)
    (memory : Array (Option Nat)) (b : FetchBus) : FetchCore :=
  let fetch := nextFetch s c idx
  let instr := if b.we && b.addr < 1024 && b.addr / 256 == idx then c.instr
    else memory[fetch % 256]?.getD none
  let baseAddr := 1024 + idx * 32
  let next := { c with
    pc := fetch
    cnt := if coreRestart s idx then 0 else
      if coreEnabled s idx then (dividerStep c.divInt 0 ⟨c.cnt, 0⟩).cnt else c.cnt
    stalled := if coreRestart s idx then false else
      if fetchExecute s c idx then !fetchCondition c s.f0 else c.stalled
    divInt := if busWrite b baseAddr then b.data / 256 % 65536 else c.divInt
    count := if busWrite b (baseAddr + 2) then b.data / 256 % 8 else c.count
    base := if busWrite b (baseAddr + 2) then b.data % 32 else c.base
    entry := if busWrite b (baseAddr + 4) then b.data % 1024 else c.entry
    bottom := if busWrite b (baseAddr + 5) then b.data % 1024 else c.bottom
    top := if busWrite b (baseAddr + 5) then b.data / 65536 % 1024 else c.top
    instr := instr }
  if b.resetN then next else { resetFetchCore idx with instr := instr }

def fetchChipCycle (s : FetchChip) (b : FetchBus) : FetchChip :=
  let w := fun j : Fin 4 => if j.val == 0 then fetchSideWrites s s.c0 0 else
    if j.val == 1 then fetchSideWrites s s.c1 1 else ⟨0, 0, 0, 0⟩
  let m0 := if b.we && b.addr < 256 then s.mem0.set! (b.addr % 256) (some b.data) else s.mem0
  let m1 := if b.we && 256 ≤ b.addr && b.addr < 512 then
      s.mem1.set! (b.addr % 256) (some b.data) else s.mem1
  let s' := { s with
    c0 := fetchedCoreCycle s s.c0 0 s.mem0 b
    c1 := fetchedCoreCycle s s.c1 1 s.mem1 b
    mem0 := m0, mem1 := m1
    enabled := if s.ctrlPend then s.ctrlData % 16 else s.enabled
    ctrlPend := busWrite b 2049, ctrlData := b.data
    f0 := if busWrite b 2050 && b.data % 2 == 1 then true else
      if busWrite b 2051 && b.data % 2 == 1 then false else s.f0
    ownerLo := if busWrite b 2053 then b.data % (2 ^ 32) else s.ownerLo
    ownerHi := if busWrite b 2054 then b.data % (2 ^ 32) else s.ownerHi
    pinOut := if busWrite b 2057 then b.data % (2 ^ 32) else ownerWord s.pinOut s.ownerLo s.ownerHi w
    pinOe := if busWrite b 2058 then b.data % (2 ^ 32) else s.pinOe }
  if b.resetN then s' else
    { coldFetchChip with c0 := s'.c0, c1 := s'.c1, mem0 := m0, mem1 := m1 }

end Wirelingo
