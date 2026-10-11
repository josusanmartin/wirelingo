import Wirelingo
open Wirelingo

def eventProbe (line : String) : Except String String := do
  let xs ← (line.trimAscii.toString.splitOn " ").mapM fun token =>
    match token.toNat? with
    | some n => pure n
    | none => throw "expected natural numbers"
  match xs with
  | [iw, prev, ev, r0, r1, r2, r3, t, x, pins, base, en, exec, reset, restart, pcw] =>
    if [iw, prev, ev, t, en, exec, reset, restart, pcw].any (· > 1) ||
        [r0, r1, r2, r3].any (· >= 65536) || x >= 524288 || pins >= 4294967296 ||
        base >= 32 || !([2, 3, 4, 7].contains (field x 16 3)) ||
        (restart == 1 && pcw == 1) then throw "unsupported input"
    let s : EventState := ⟨⟨iw == 1, prev == 1, ev == 1⟩, r0, r1, r2, r3, t == 1⟩
    let i : EventSignals := ⟨x, pins, base, en == 1, exec == 1,
      reset == 1, restart == 1, pcw == 1⟩
    let out := eventCycle s i
    let bit (b : Bool) := if b then "1" else "0"
    pure (String.intercalate " " [bit out.tracker.inWait, bit out.tracker.prev,
      bit out.tracker.event, toString out.r0, toString out.r1, toString out.r2,
      toString out.r3, bit out.t])
  | _ => throw "expected sixteen fields"

def main : IO UInt32 := do
  let input ← IO.getStdin
  let output ← IO.getStdout
  repeat
    let line ← input.getLine
    if line.isEmpty then return 0
    match eventProbe line with
    | .ok result => output.putStrLn result
    | .error message =>
      (← IO.getStderr).putStrLn message
      return 1
  return 0
