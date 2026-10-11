import Wirelingo

open Wirelingo

/-- Probe protocol: timeout condition budget T PC wrapTop wrapBottom delay.
Output: complete budget T PC delay inWait. Reject out-of-width inputs. -/
def probe (line : String) : Except String String := do
  let values ← (line.trimAscii.toString.splitOn " ").mapM fun token =>
    match token.toNat? with
    | some n => pure n
    | none => throw "expected space-separated natural numbers"
  match values with
  | [timeout, condition, budget, t, pc, wt, wb, delay] =>
    if timeout > 1 || condition > 1 || t > 1 || budget >= 65536 ||
        pc >= 1024 || wt >= 1024 || wb >= 1024 || delay >= 256 then
      throw "input outside ISA field width"
    let cfg : WaitConfig := ⟨timeout == 1, wt, wb, delay⟩
    let s : WaitState := ⟨budget, pc, t == 1, 0, false⟩
    let (complete, out) := waitStep cfg (condition == 1) s
    let bit (b : Bool) := if b then "1" else "0"
    pure (String.intercalate " " [bit complete, toString out.budget, bit out.t,
      toString out.pc, toString out.dcnt, bit out.inWait])
  | _ => throw "expected eight fields"

def main : IO UInt32 := do
  let input ← IO.getStdin
  let output ← IO.getStdout
  repeat
    let line ← input.getLine
    if line.isEmpty then return 0
    match probe line with
    | .ok result => output.putStrLn result
    | .error message =>
      (← IO.getStderr).putStrLn message
      return 1
  return 0
