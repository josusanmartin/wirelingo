import Wirelingo.RxAutopush

#print axioms Wirelingo.rx_manual_inactive_no_push
#print axioms Wirelingo.rx_manual_below_threshold
#print axioms Wirelingo.rx_manual_threshold_ready
#print axioms Wirelingo.rx_manual_threshold_full
#print axioms Wirelingo.rx_manual_full_pop_resume

#print axioms Wirelingo.rx1_manual_below
#print axioms Wirelingo.rx1_manual_ready
#print axioms Wirelingo.rx1_manual_full
#print axioms Wirelingo.rx1_manual_current_bit_resume
#print axioms Wirelingo.rx_push_manual_ready
#print axioms Wirelingo.rx_push_manual_full
#print axioms Wirelingo.rx1_manual_lsb_byte_order
#print axioms Wirelingo.rx1_manual_high_tails

open Wirelingo

def main : IO Unit := do
  let stdin ← IO.getStdin
  let stdout ← IO.getStdout
  let mut s := rxCold
  repeat
    let line ← stdin.getLine
    if line.isEmpty then break
    let ns := (line.trimAscii.toString.splitOn " ").filterMap String.toNat?
    let get := fun n => ns[n]?.getD 0
    if get 0 == 0 then s := rxCold
    else if get 0 == 2 then
      -- Manual per-action projection, not a whole-core control-flow predictor.
      let p : RxPartial := ⟨BitVec.ofNat 32 (get 2), get 3, BitVec.ofNat 10 (get 4)⟩
      let g : RxGate := ⟨get 9 == 1, get 10 == 1, get 11 == 1, get 12 == 1,
        get 13, get 14 == 1, get 15 == 1⟩
      let r := if get 1 == 0 then
          rxIn1 p g (BitVec.ofNat 1 (get 5)) (get 6 == 1) (BitVec.ofNat 10 (get 7)) (BitVec.ofNat 10 (get 8))
        else rxPushBlock p g (get 6 == 1) (BitVec.ofNat 10 (get 7)) (BitVec.ofNat 10 (get 8))
      let ns := [p.isr.toNat, p.count, p.pc.toNat, r.state.isr.toNat,
        r.state.count, r.state.pc.toNat, r.push.isSome.toNat,
        (r.push.getD p.isr).toNat, r.isrWrite.toNat, r.complete.toNat]
      stdout.putStrLn ("PROJ " ++ String.intercalate " " (ns.map toString))
    else
      let b : RxBus := ⟨get 1 == 1, get 2 == 1, get 3, get 4, get 5,
        get 6 == 1, get 7 == 1, get 8 == 1⟩
      stdout.putStrLn ("PRED " ++ String.intercalate " " ((rxObserve s b).map toString))
      s := rxChipCycle s b
