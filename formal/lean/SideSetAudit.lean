import Wirelingo.SideSet

#print axioms Wirelingo.side_completion_independent
#print axioms Wirelingo.side_no_execution
#print axioms Wirelingo.side_bridge_stalled
#print axioms Wirelingo.side_optional_disabled
#print axioms Wirelingo.side_direction_only
#print axioms Wirelingo.side_output_only
#print axioms Wirelingo.side_own_bank_gate

-- Executable closed projection. Initial state is supplied once per trace;
-- subsequent inputs contain only enable/restart/F0, never DUT observations.
open Wirelingo

def main : IO Unit := do
  let stdin ← IO.getStdin
  let stdout ← IO.getStdout
  let mut cfg : FlagSideConfig := ⟨⟨0, 0, false, false, 0⟩, 1, 1, 0⟩
  let mut state : FlagSideState := ⟨⟨0, 0⟩, 0, false, false, false, 256⟩
  repeat
    let line ← stdin.getLine
    if line.isEmpty then break
    let ns := (line.trimAscii.toString.splitOn " ").filterMap String.toNat?
    let get := fun n => ns[n]?.getD 0
    let bit := fun n => get n == 1
    if get 0 == 0 then
      cfg := ⟨⟨get 2, get 3, bit 4, bit 5, get 6⟩, get 1, get 7, get 8⟩
      state := ⟨⟨get 9, get 10⟩, get 11, bit 12, bit 13, bit 14, get 15⟩
    else
      let i : GatingInput := ⟨0, bit 1, bit 2, false⟩
      let ex := flagExecute cfg state i
      let done := ex && bit 3
      let w := sideEffect cfg.side ex done
      state := flagSideCycle cfg state i (bit 3)
      let row := [w.outEn, w.outVal, w.oeEn, w.oeVal, ex.toNat, done.toNat,
        state.pc, state.stalled.toNat, state.divider.cnt, state.divider.facc,
        state.dcnt, state.halted.toNat, state.fault.toNat]
      stdout.putStrLn ("PRED " ++ String.intercalate " " (row.map toString))
