import Wirelingo.RxAutopush

#print axioms Wirelingo.rx_manual_inactive_no_push
#print axioms Wirelingo.rx_manual_below_threshold
#print axioms Wirelingo.rx_manual_threshold_ready
#print axioms Wirelingo.rx_manual_threshold_full
#print axioms Wirelingo.rx_manual_full_pop_resume

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
    if get 0 == 0 then s := rxCold else
      let b : RxBus := ⟨get 1 == 1, get 2 == 1, get 3, get 4, get 5,
        get 6 == 1, get 7 == 1, get 8 == 1⟩
      stdout.putStrLn ("PRED " ++ String.intercalate " " ((rxObserve s b).map toString))
      s := rxChipCycle s b
