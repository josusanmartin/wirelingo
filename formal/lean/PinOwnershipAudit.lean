import Wirelingo.PinOwnership

#print axioms Wirelingo.owner_selected_update
#print axioms Wirelingo.owner_nonowner_noninterference
#print axioms Wirelingo.owner_no_write_retains
#print axioms Wirelingo.owner_absent_mask_retains
#print axioms Wirelingo.owner_host_priority
#print axioms Wirelingo.owner_old_configuration_edge

open Wirelingo

def unknownWord : Nat := 4294967296

def main : IO Unit := do
  let stdin ← IO.getStdin
  let stdout ← IO.getStdout
  let mut s := coldFetchChip
  repeat
    let line ← stdin.getLine
    if line.isEmpty then break
    let ns := (line.trimAscii.toString.splitOn " ").filterMap String.toNat?
    let get := fun n => ns[n]?.getD 0
    if get 0 == 0 then s := coldFetchChip else
      let b : FetchBus := ⟨get 1 == 1, get 2 == 1, get 3, get 4⟩
      let ex0 := fetchExecute s s.c0 0
      let ex1 := fetchExecute s s.c1 1
      let w0 := fetchSideWrites s s.c0 0
      let w1 := fetchSideWrites s s.c1 1
      let pre := [s.ownerLo, s.ownerHi, s.enabled,
        (coreRestart s 0).toNat + 2 * (coreRestart s 1).toNat, s.f0.toNat,
        s.c0.pc, s.c1.pc, s.c0.instr.getD unknownWord, s.c1.instr.getD unknownWord,
        nextFetch s s.c0 0, nextFetch s s.c1 1, s.c0.cnt, s.c1.cnt,
        ex0.toNat, ex1.toNat, (ex0 && fetchCondition s.c0 s.f0).toNat,
        (ex1 && fetchCondition s.c1 s.f0).toNat,
        w0.outEn, w0.outVal, w1.outEn, w1.outVal, w0.oeEn, w1.oeEn, 0, 0]
      s := fetchChipCycle s b
      let post := [s.pinOut, s.pinOe, s.pinOut / 256 % 128,
        s.c0.pc, s.c1.pc, s.c0.instr.getD unknownWord, s.c1.instr.getD unknownWord,
        s.c0.stalled.toNat, s.c1.stalled.toNat, 0, 0]
      stdout.putStrLn ("PRED " ++ String.intercalate " " ((pre ++ post).map toString))
