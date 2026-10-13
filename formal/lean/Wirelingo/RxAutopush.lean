import Std

namespace Wirelingo

/-- Manual IN PINS8/autopush32 boundary, NOT a translation of SV. ISR/PC have
32/10-bit widths; reachable count is 0..32. No LINE, CRC, side effects, FJOIN,
host register/PC/config writes, FIFO clear, or reset on the sampled edge. -/
structure RxPartial where
  isr : BitVec 32
  count : Nat
  pc : BitVec 10
  deriving DecidableEq, Repr

structure RxGate where
  enabled : Bool
  restart : Bool
  tick : Bool
  bankOk : Bool
  delay : Nat
  halted : Bool
  fault : Bool
  deriving Repr

def rxExecute (g : RxGate) : Bool :=
  g.enabled && !g.restart && g.tick && g.bankOk && (g.delay == 0) && !g.halted && !g.fault

def rxShift8 (right : Bool) (isr : BitVec 32) (byte : BitVec 8) : BitVec 32 :=
  if right then BitVec.ofNat 32 (isr.toNat / 256 + byte.toNat * 16777216)
  else BitVec.ofNat 32 (isr.toNat * 256 + byte.toNat)

def rxCount8 (s : RxPartial) : Nat := min 32 (s.count + 8)

structure RxResult where
  state : RxPartial
  push : Option (BitVec 32)
  complete : Bool
  isrWrite : Bool
  deriving DecidableEq, Repr

/-- Full is the start-of-cycle RXF full flag, including on a host-pop edge.
Restart clears storage even disabled, separately from an IN execution. -/
def rxIn8 (s : RxPartial) (g : RxGate) (right : Bool) (byte : BitVec 8)
    (full : Bool) (nextPC entry : BitVec 10) : RxResult :=
  if g.restart then ⟨⟨0, 0, entry⟩, none, false, false⟩
  else if rxExecute g then
    if 32 ≤ rxCount8 s then
      if full then ⟨s, none, false, false⟩
      else ⟨⟨0, 0, nextPC⟩, some (rxShift8 right s.isr byte), true, true⟩
    else ⟨⟨rxShift8 right s.isr byte, rxCount8 s, nextPC⟩, none, true, true⟩
  else ⟨s, none, false, false⟩

theorem rx_manual_inactive_no_push (s : RxPartial) (g : RxGate)
    (right : Bool) (byte : BitVec 8) (full : Bool) (nextPC entry : BitVec 10)
    (h : rxExecute g = false) :
    (rxIn8 s g right byte full nextPC entry).push = none := by
  cases hr : g.restart <;> simp [rxIn8, hr, h]

theorem rx_manual_below_threshold (s : RxPartial) (g : RxGate)
    (right : Bool) (byte : BitVec 8) (full : Bool) (nextPC entry : BitVec 10)
    (hr : g.restart = false) (he : rxExecute g = true) (hc : rxCount8 s < 32) :
    rxIn8 s g right byte full nextPC entry =
      ⟨⟨rxShift8 right s.isr byte, rxCount8 s, nextPC⟩, none, true, true⟩ := by
  simp [rxIn8, hr, he, Nat.not_le.mpr hc]

theorem rx_manual_threshold_ready (s : RxPartial) (g : RxGate)
    (right : Bool) (byte : BitVec 8) (nextPC entry : BitVec 10)
    (hr : g.restart = false) (he : rxExecute g = true) (hc : 32 ≤ rxCount8 s) :
    rxIn8 s g right byte false nextPC entry =
      ⟨⟨0, 0, nextPC⟩, some (rxShift8 right s.isr byte), true, true⟩ := by
  simp [rxIn8, hr, he, hc]

theorem rx_manual_threshold_full (s : RxPartial) (g : RxGate)
    (right : Bool) (byte : BitVec 8) (nextPC entry : BitVec 10)
    (hr : g.restart = false) (he : rxExecute g = true) (hc : 32 ≤ rxCount8 s) :
    rxIn8 s g right byte true nextPC entry = ⟨s, none, false, false⟩ := by
  simp [rxIn8, hr, he, hc]

/-- Conditional two-execution composition: a host pop makes full=false NEXT
cycle, with no intervening ISR write/restart. The retained partial shifts the
CURRENT resume byte. This does not claim wire capture during the stall. -/
theorem rx_manual_full_pop_resume (s : RxPartial) (g : RxGate)
    (right : Bool) (blockedByte resumeByte : BitVec 8) (nextPC entry : BitVec 10)
    (hr : g.restart = false) (he : rxExecute g = true) (hc : 32 ≤ rxCount8 s) :
    let blocked := rxIn8 s g right blockedByte true nextPC entry
    blocked.push = none ∧
    rxIn8 blocked.state g right resumeByte false nextPC entry =
      ⟨⟨0, 0, nextPC⟩, some (rxShift8 right s.isr resumeByte), true, true⟩ := by
  simp [rxIn8, hr, he, hc]

/-- Closed finite witness: core0, div1/delay0, four identical normally loaded
IN words/self-wrap0..3; core1 disabled. Actual two-flop pad pipeline included.
FIFO depth8 and up to two snap/commit/cancel reservations; no serial port. -/
structure RxChip where
  packed : RxPartial
  right : Bool
  enabled : Bool
  pending : Bool
  ctrl : Nat
  sync1 : Nat
  sync2 : Nat
  queue : List Nat
  reserved : List Bool
  stalled : Bool
  memory : Array (Option Nat)
  instr : Option Nat
  deriving Repr

def rxCold : RxChip :=
  ⟨⟨0, 0, 0⟩, false, false, false, 0, 0, 0, [], [], false,
    Array.replicate 256 none, none⟩

structure RxBus where
  resetN : Bool
  we : Bool
  addr : Nat
  data : Nat
  rawByte : Nat
  snap : Bool
  commit : Bool
  cancel : Bool
  deriving Repr

def rxRestart (s : RxChip) : Bool := s.pending && (s.ctrl / 256 % 2 == 1)
def rxReal (s : RxChip) : Nat := (s.reserved.filter id).length
def rxRdReal (s : RxChip) : Bool := rxReal s < 2 && s.queue.length > rxReal s
def rxRead (s : RxChip) : Nat := if rxRdReal s then s.queue[rxReal s]?.getD 0 else 0
def rxHostPop (s : RxChip) (b : RxBus) : Bool :=
  b.addr == 1040 && b.commit && s.reserved.head?.getD false

def rxGate (s : RxChip) : RxGate :=
  ⟨s.enabled, rxRestart s, true, s.packed.pc.toNat / 256 == 0, 0, false, false⟩

def rxStepResult (s : RxChip) : RxResult :=
  rxIn8 s.packed (rxGate s) s.right (BitVec.ofNat 8 s.sync2)
    (s.queue.length == 8)
    (if s.packed.pc.toNat == 3 then 0 else s.packed.pc + 1) 0

def rxChipCycle (s : RxChip) (b : RxBus) : RxChip := Id.run do
  let r := rxStepResult s
  let mut n := { s with packed := r.state }
  if rxRestart s then n := { n with stalled := false }
  else if rxExecute (rxGate s) then n := { n with stalled := !r.complete }
  let pop := rxHostPop s b
  let q := if pop then s.queue.drop 1 else s.queue
  let queue := match r.push with
    | none => q
    | some word => q ++ [word.toNat]
  n := { n with queue := queue }
  if b.cancel then n := { n with reserved := [] }
  else if b.addr == 1040 && b.commit && !s.reserved.isEmpty then
    n := { n with reserved := s.reserved.drop 1 }
  else if b.addr == 1040 && b.snap && s.reserved.length < 2 then
    n := { n with reserved := s.reserved ++ [rxRdReal s] }
  n := { n with sync1 := b.rawByte % 256, sync2 := s.sync1, pending := b.we && b.addr == 2049, ctrl := b.data }
  if s.pending then n := { n with enabled := s.ctrl % 2 == 1 }
  if b.we && b.addr == 1027 then n := { n with right := b.data / 2 % 2 == 1 }
  if b.we && b.addr < 256 then
    n := { n with memory := s.memory.set! b.addr (some b.data) }
  else n := { n with instr := s.memory[s.packed.pc.toNat]?.getD none }
  -- All program words are identical; fetch PC may advance but the word is the
  -- same. Before load, instr remains explicit unknown. No mid-run bank writes.
  if !b.resetN then
    n := { rxCold with memory := n.memory, instr := n.instr }
  return n

def rxObserve (s : RxChip) (b : RxBus) : List Nat :=
  let r := rxStepResult s
  let n := rxChipCycle s b
  let ex := rxExecute (rxGate s)
  let decodeData := if 32 ≤ rxCount8 s.packed && s.queue.length < 8 then
      (rxShift8 s.right s.packed.isr (BitVec.ofNat 8 s.sync2)).toNat
    else s.packed.isr.toNat
  [s.enabled.toNat, (rxRestart s).toNat, s.sync2,
   s.instr.getD 4294967296, s.packed.pc.toNat, s.packed.isr.toNat,
   s.packed.count, s.queue.length, s.reserved.length, rxReal s,
   (rxRdReal s).toNat, if b.addr == 1040 then rxRead s else 0,
   ex.toNat, r.complete.toNat, r.push.isSome.toNat, decodeData,
   (ex && r.isrWrite).toNat, (rxHostPop s b).toNat,
   if s.queue.isEmpty then 0 else s.queue.head?.getD 0,
   n.packed.pc.toNat, n.packed.isr.toNat, n.packed.count,
   n.queue.length, n.reserved.length, rxReal n, n.stalled.toNat,
   0, 0, n.sync1, n.sync2, 0, 0, 0, 0]

/-- Added UART boundary: IN PINS1, right shift, autopush32. All gate and
host-interference assumptions are the SAME manual boundary as IN8. -/
def rxShift1 (word : BitVec 32) (bit : BitVec 1) : BitVec 32 :=
  BitVec.ofNat 32 (word.toNat / 2 + bit.toNat * 2147483648)

def rxIn1 (s : RxPartial) (g : RxGate) (bit : BitVec 1) (full : Bool)
    (nextPC entry : BitVec 10) : RxResult :=
  if g.restart then ⟨⟨0, 0, entry⟩, none, false, false⟩
  else if rxExecute g then
    if 32 ≤ min 32 (s.count + 1) then
      if full then ⟨s, none, false, false⟩
      else ⟨⟨0, 0, nextPC⟩, some (rxShift1 s.isr bit), true, true⟩
    else ⟨⟨rxShift1 s.isr bit, min 32 (s.count + 1), nextPC⟩, none, true, true⟩
  else ⟨s, none, false, false⟩

/-- Blocking, unconditional PUSH of the current ISR: no realignment.
No if-full/CRC/LINE/host-register-write variant is part of this boundary. -/
def rxPushBlock (s : RxPartial) (g : RxGate) (full : Bool)
    (nextPC entry : BitVec 10) : RxResult :=
  if g.restart then ⟨⟨0, 0, entry⟩, none, false, false⟩
  else if rxExecute g then
    if full then ⟨s, none, false, false⟩
    else ⟨⟨0, 0, nextPC⟩, some s.isr, true, true⟩
  else ⟨s, none, false, false⟩

theorem rx1_manual_below (s : RxPartial) (g : RxGate) (bit : BitVec 1)
    (full : Bool) (nextPC entry : BitVec 10) (hr : g.restart = false)
    (he : rxExecute g = true) (hc : min 32 (s.count + 1) < 32) :
    rxIn1 s g bit full nextPC entry =
      ⟨⟨rxShift1 s.isr bit, min 32 (s.count + 1), nextPC⟩, none, true, true⟩ := by
  simp [rxIn1, hr, he, Nat.not_le.mpr hc]

theorem rx1_manual_ready (s : RxPartial) (g : RxGate) (bit : BitVec 1)
    (nextPC entry : BitVec 10) (hr : g.restart = false)
    (he : rxExecute g = true) (hc : 32 ≤ min 32 (s.count + 1)) :
    rxIn1 s g bit false nextPC entry =
      ⟨⟨0, 0, nextPC⟩, some (rxShift1 s.isr bit), true, true⟩ := by
  simp [rxIn1, hr, he, hc]

theorem rx1_manual_full (s : RxPartial) (g : RxGate) (bit : BitVec 1)
    (nextPC entry : BitVec 10) (hr : g.restart = false)
    (he : rxExecute g = true) (hc : 32 ≤ min 32 (s.count + 1)) :
    rxIn1 s g bit true nextPC entry = ⟨s, none, false, false⟩ := by
  simp [rxIn1, hr, he, hc]

theorem rx1_manual_current_bit_resume (s : RxPartial) (g : RxGate)
    (blocked current : BitVec 1) (nextPC entry : BitVec 10)
    (hr : g.restart = false) (he : rxExecute g = true)
    (hc : 32 ≤ min 32 (s.count + 1)) :
    let stalled := rxIn1 s g blocked true nextPC entry
    stalled.push = none ∧ rxIn1 stalled.state g current false nextPC entry =
      ⟨⟨0, 0, nextPC⟩, some (rxShift1 s.isr current), true, true⟩ := by
  simp [rxIn1, hr, he, hc]

theorem rx_push_manual_ready (s : RxPartial) (g : RxGate) (nextPC entry : BitVec 10)
    (hr : g.restart = false) (he : rxExecute g = true) :
    rxPushBlock s g false nextPC entry = ⟨⟨0, 0, nextPC⟩, some s.isr, true, true⟩ := by
  simp [rxPushBlock, hr, he]

theorem rx_push_manual_full (s : RxPartial) (g : RxGate) (nextPC entry : BitVec 10)
    (hr : g.restart = false) (he : rxExecute g = true) :
    rxPushBlock s g true nextPC entry = ⟨s, none, false, false⟩ := by
  simp [rxPushBlock, hr, he]

/-- Eight successive right IN1 operations in LSB-first byte order. -/
def rxFoldByte (s : BitVec 32) (b : BitVec 8) : BitVec 32 :=
  let r0 := rxShift1 s (BitVec.ofNat 1 (b.toNat % 2))
  let r1 := rxShift1 r0 (BitVec.ofNat 1 (b.toNat / 2 % 2))
  let r2 := rxShift1 r1 (BitVec.ofNat 1 (b.toNat / 4 % 2))
  let r3 := rxShift1 r2 (BitVec.ofNat 1 (b.toNat / 8 % 2))
  let r4 := rxShift1 r3 (BitVec.ofNat 1 (b.toNat / 16 % 2))
  let r5 := rxShift1 r4 (BitVec.ofNat 1 (b.toNat / 32 % 2))
  let r6 := rxShift1 r5 (BitVec.ofNat 1 (b.toNat / 64 % 2))
  rxShift1 r6 (BitVec.ofNat 1 (b.toNat / 128 % 2))

theorem rx1_manual_lsb_byte_order (s : BitVec 32) (b : BitVec 8) :
    rxFoldByte s b = BitVec.ofNat 32 (s.toNat / 256 + b.toNat * 16777216) := by
  apply BitVec.eq_of_toNat_eq
  have hs := s.isLt
  have hb := b.isLt
  simp only [rxFoldByte, rxShift1, BitVec.toNat_ofNat]
  omega

theorem rx1_manual_high_tails (a b c : BitVec 8) :
    rxFoldByte 0 a = BitVec.ofNat 32 (a.toNat * 16777216) ∧
    rxFoldByte (rxFoldByte 0 a) b =
      BitVec.ofNat 32 ((a.toNat + b.toNat * 256) * 65536) ∧
    rxFoldByte (rxFoldByte (rxFoldByte 0 a) b) c =
      BitVec.ofNat 32 ((a.toNat + b.toNat * 256 + c.toNat * 65536) * 256) := by
  simp only [rx1_manual_lsb_byte_order]
  have hz : (0 : BitVec 32).toNat = 0 := rfl
  simp only [hz]
  have ha := a.isLt
  have hb := b.isLt
  have hc := c.isLt
  constructor
  · apply BitVec.eq_of_toNat_eq
    simp
  · constructor <;> apply BitVec.eq_of_toNat_eq <;>
      simp only [BitVec.toNat_ofNat] <;> omega

end Wirelingo
