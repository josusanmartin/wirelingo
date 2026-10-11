import Wirelingo.Gating
open Wirelingo

def naturals (line : String) : Except String (List Nat) :=
  (line.trimAscii.toString.splitOn " ").mapM fun token =>
    match token.toNat? with
    | some n => pure n
    | none => throw "expected natural numbers"

def bitText (b : Bool) : String := if b then "1" else "0"

def stateText (s : GatingState) : String :=
  String.intercalate " " [bitText s.event.tracker.inWait, bitText s.event.tracker.prev,
    bitText s.event.tracker.event, toString s.event.r0, toString s.event.r1,
    toString s.event.r2, toString s.event.r3, bitText s.event.t,
    toString s.divider.cnt, toString s.divider.facc, toString s.dcnt,
    bitText s.halted, bitText s.fault]

def initializeGating (xs : List Nat) : Except String (GatingConfig × GatingState) := do
  match xs with
  | [x, base, di, df, delay, iw, prev, ev, r0, r1, r2, r3, t, cnt, facc, dcnt, halted, fault] =>
    if x >= 524288 || !([2,3,4,7].contains (field x 16 3)) || base >= 32 ||
        di >= 65536 || df >= 256 || delay >= 256 || cnt >= 65536 || facc >= 256 ||
        dcnt >= 256 || [r0,r1,r2,r3].any (· >= 65536) ||
        [iw,prev,ev,t,halted,fault].any (· > 1) then throw "invalid initial domain"
    pure (⟨x, base, di, df, delay⟩,
      ⟨⟨⟨iw==1, prev==1, ev==1⟩,r0,r1,r2,r3,t==1⟩,⟨cnt,facc⟩,dcnt,halted==1,fault==1⟩)
  | _ => throw "expected eighteen initialization fields"

def advance (cfg : GatingConfig) (s : GatingState) (xs : List Nat) :
    Except String (GatingState × String) := do
  match xs with
  | [pins,en,restart,pcw] =>
    if pins >= 4294967296 || [en,restart,pcw].any (· > 1) ||
        (restart == 1 && pcw == 1) then throw "unsupported cycle input"
    let i : GatingInput := ⟨pins,en==1,restart==1,pcw==1⟩
    let out := gatingCycle cfg s i
    pure (out, stateText out ++ " " ++ bitText (gateExecute s i) ++
      " " ++ bitText (gatingComplete cfg s i))
  | _ => throw "expected four external cycle inputs"

def processCommand (current : Option (GatingConfig × GatingState)) (tokens : List Nat) :
    Except String (Option (GatingConfig × GatingState) × Option String) := do
  match tokens with
  | 0 :: xs => pure (some (← initializeGating xs), none)
  | 1 :: xs =>
    match current with
    | none => throw "initialize before cycling"
    | some (cfg, s) =>
      let (out, row) ← advance cfg s xs
      pure (some (cfg, out), some row)
  | _ => throw "unknown command"

def main : IO UInt32 := do
  let input ← IO.getStdin
  let output ← IO.getStdout
  let mut current : Option (GatingConfig × GatingState) := none
  repeat
    let line ← input.getLine
    if line.isEmpty then return 0
    let result := do
      let tokens ← naturals line
      processCommand current tokens
    match result with
    | .ok (next, row) =>
      current := next
      match row with
      | some text => output.putStrLn text
      | none => pure ()
    | .error message =>
      (← IO.getStderr).putStrLn message
      return 1
  return 0
