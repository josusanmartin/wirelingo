import Wirelingo.Gating

namespace Wirelingo

/-- Manual Nat transcription of the RTL's 9-bit fsum and bit-8 carry.
This is not a machine-checked extraction from SystemVerilog. -/
def rtlDividerStep (di df : Nat) (s : Divider) : Divider :=
  let fsum := (s.facc + df) % 512
  if s.cnt == 0 then
    ⟨(di + 65535 + (fsum / 256) % 2) % 65536, fsum % 256⟩
  else ⟨(s.cnt + 65535) % 65536, s.facc⟩

theorem rtl_fsum_no_overflow (facc frac : Nat) (ha : facc < 256) (hf : frac < 256) :
    (facc + frac) % 512 = facc + frac := by
  apply Nat.mod_eq_of_lt
  omega

theorem rtl_bit8_is_carry (facc frac : Nat) (ha : facc < 256) (hf : frac < 256) :
    (((facc + frac) % 512) / 256) % 2 = (facc + frac) / 256 := by
  rw [rtl_fsum_no_overflow facc frac ha hf]
  apply Nat.mod_eq_of_lt
  omega

theorem rtl_divider_one_step (di df : Nat) (s : Divider)
    (ha : s.facc < 256) (hf : df < 256) :
    rtlDividerStep di df s = dividerStep di df s := by
  have hc : ((s.facc + df) / 256) % 2 = (s.facc + df) / 256 := by
    apply Nat.mod_eq_of_lt
    omega
  simp only [rtlDividerStep, dividerStep,
    rtl_fsum_no_overflow s.facc df ha hf, hc]

/-- Manual transcription of active/attempt/bank_ok/exec at the core boundary. -/
def rtlExecute (s : GatingState) (i : GatingInput) (bankOk : Bool) : Bool :=
  (i.enabled && !i.restart && s.divider.cnt == 0 &&
    !s.halted && !s.fault && s.dcnt == 0) && bankOk

theorem rtl_gate_own_bank (s : GatingState) (i : GatingInput) :
    rtlExecute s i true = gateExecute s i := by
  simp [rtlExecute, gateExecute, gateActive]

end Wirelingo
