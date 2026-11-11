VERDICT: PASS

070 pkg3-spec-model (branch `ao-pkg3`, commits `a53461b`, `39b05f3`, `effd086` on `next-8x4` `d2ed344`, backlog AO): verifier report.
Kria: verifier does not touch it. No SWD.

The v0.3 spec takes only space that v0.2 reserved or ignored. The model and assembler implement it, and the v0.2 firmware is byte-identical under the new assembler. My scan finds no v0.3 encoding in any assembled v0.2 program. The model suite and all 7 named mutants reproduce. I have one wording note on the spec (finding 1) and nothing that changes a claim.

## Findings

1. **Spec wording: "RET (`MOV PC, LR`) is unchanged for every LR a CALL wrote" (`docs/isa.md`, §5.1, the paragraph after the operand table) has one exception.**
   - **The case:** a CALL in the last word of a bank, with WRAP_TOP elsewhere, links LR = PC + 1, which is in the next bank. Core 3's word 1023 is the same case, wrapping to 0.
   - **The difference:** in v0.2 the RET jumps there and the core faults. In v0.3 the bank-relative write gives `{PC[9:8], LR[7:0]}`, word 0 of its own bank, so the core runs on instead of faulting.
   - **Is §8 complete?** Its row "only a computed jump that left its bank (which faulted)" covers the case in effect. So §8 is complete in substance, but the §5.1 sentence is not exact.
   - **Same for §8's lead sentence:** "Every word the v0.2 assembler can emit keeps its v0.2 meaning" is contradicted for out-of-bank `MOV`/`OUT PC` values by §8's own table.
   - **Fix:** say "unchanged whenever the CALL's link is in the bank (every CALL but one in the bank's last word without a wrap)", and "keeps its v0.2 meaning except a PC write that left its bank".
   - **No program affected:** none in `firmware/` has a CALL in a bank's last word, and no v0.2 program is affected (claim 2 below).

2. **Minor, no change required.** `sim.known_id` accepts any v0.3 geometry with NBANKS ≤ NCORES, for example `0x50452303`, as its docstring says. Nothing builds such a geometry today.

## Asked checks

- **The v0.3 slots against next-8x4's reserved list.** `d2ed344:docs/isa.md:400-401` says "Reserved operand bits should be 0. The assembler emits 0 and the hardware ignores them." Each new slot checks against it:
  - LDI `[18]` is outside LDI's listed `[17:16] rd [15:0] imm`.
  - FLAG `[17]` is outside `[18] clear [2:0] idx`.
  - MOV op 3 was "reserved (behaves as none)" (`:573`).
  - JMP/CALL `sel` with cond 0 was unused, since cond 0 is "always".
  - Operand 15 was "—, 0, discard".
  - CRCCTRL `[23]` and core offset 0x11 were unlisted ("Reserved addresses read as 0 and ignore writes", `:772`).

  Every slot was reserved, ignored or unused. ✓ Opcodes 14–31 stay reserved. ✓
- **§8 completeness.** I read the whole RTL diff of 071 (`src/pe_core.sv`, `src/pe_chip.sv`) for any change a v0.2 program could see:
  - `alu_imm` for a non-CMPW word is `{3'b0, x[12:0]}`, as before;
  - FLAG with `[17]` = 0 is as before;
  - cond 0 with `sel` = 0 is as before;
  - the CRC read is `{0, crc}` because the top half stays 0 with W ≤ 16 (every write to it is masked, and the host write keeps 16 bits unless `crc_w[4]`);
  - `exec` and the PC are unchanged unless a table read was issued.

  The ID, the bank-relative PC, the reinterpreted reserved encodings and the 32-bit CRC read are all in §8's table. Nothing is missing beyond finding 1's wording.
- **The table read's corner list is unambiguous**, and the model and RTL agree with it:
  - a restart in the read's clock wins, which also covers a CTRL restart written in the MOV's clock, since CTRL acts a clock later (§5.3);
  - a host PC write in the MOV's clock cancels the read;
  - the write happens whether or not the core is enabled;
  - a host OSR read in the read's clock sees the old value;
  - other `dst` values with op 3 behave as OSR.

  A host PC write in the read's clock is not listed. The model and RTL both write the word and take the host's PC, which follows from the stated rules.
- **Model against the spec** (`tools/pe/sim.py` diff):
  - **The table read.** `_op_mov` with `M_TBL` sets `fx["tbl"]` from `fetch({PC[9:8], v[7:0]})`. In `Chip.step`, after the cores and the FIFO update and before restarts:
    - last clock's word goes into the OSR with `osr_cnt` 0, enabled or not;
    - this clock's read is kept unless `host_pc == c`;
    - the restart loop then runs `restart()`, which clears the OSR and `tbl`, so a restart wins.

    In `Core.step`, a tick with `tbl` pending returns before the delay countdown, so the read's clock spends the tick and the delay doesn't count down. ✓ (§4.2, §5.2)
  - **The tick counter.** It increments on every tick of a stepped (enabled, not restarting) core, before the halted/fault return. The reading instruction gets `e.ticks`, the count captured at the start of the clock, so it reads the count before its own tick. `restart()` clears it. ✓ (§4.1)
  - **CRC.** `crc_w` is 5 bits from `[19:16]` and `[23]`; `poly = POLY_HI << 16 | POLY`, masked to W bits. A host write is `v & (M32 if crc_w >= 16 else M16)`, that is, 32 bits exactly when W > 16. ✓ (§4.5, §6.2)
  - **CMPW, `!t` and `flag tog`.** CMPW takes Z/C from `cmpw_imm`, with rd at `[15:14]` and no write. `!t` is `sel != XC_NT or t == 0`. `flag tog` uses `e.flags`, the start-of-cycle view, through `fset`/`fclr`. ✓ (§5.2, §5.3)
  - **ID.** `id_value(ncores, nbanks)` is `0x50450003 | nc << 8 | nb << 12`: 3/3 gives `0x50453303`, and 2/2 gives `0x50452203`. ✓
- **Byte-identical firmware.** I assembled every `firmware/*.pasm` of `d2ed344` with `d2ed344`'s `tools/pe` and with `3a7feab`'s, under 5 define sets. Unknown symbols were defined iteratively at 1, 2, 3, 5 and 7, giving 68 files × 5 = 340 combinations.
  - **286 assemble, and all 286 are identical:** the same programs, defines and 22,379 words, comparing `relocate(0)`.
  - **The other 54 fail identically in both** with the same error text.
  - The builder's 275/65 split differs from mine only because of the define sets.
  - The 3 new files are new.
- **No v0.3 encoding in v0.2 programs.** This is my own classifier, using the v0.2 `isa.fields`, not the branch's `v03_feature`. Over the 22,379 words it finds:
  - 0 LDI with `[18]`;
  - 0 FLAG with `[17]`;
  - 0 MOV op 3;
  - 0 cond-0 JMP/CALL with `sel` ≠ 0;
  - 0 MOV/IN from operand 15;
  - 0 opcodes ≥ 14.
- **Computed PC writes in the v0.2 library.** These are the only programs that could see the bank-relative change:
  - a RET (`MOV PC, LR`, assembled) in 17 programs;
  - `mov pc, r2` in `usb_enum` and its 4 `usb_uart_*` relatives, which are `.origin 0`, core 0, with table addresses below 256;
  - `mov pc, r1` in `mdio_phy`, which adds `table + BASE`, its own load base, so the target stays in its bank.

  In-bank, bank-relative gives the same address. ✓
- **Assembler.** The diff matches claim 4:
  - `cmp rN, K` ≥ 8192 now encodes CMPW; v0.2 rejected it as "alu immediate";
  - `mov osr, [src]` is accepted only to the OSR;
  - `flag tog` and the `ticks` operand are added;
  - `.data` words are not relocated.
- **Host tools.**
  - `pe.mpy_bundle --check` locally: "firmware bundle matches validated sources", rc 0.
  - `isa.crcctrl(0x04C11DB7, 32, in_tap=1)` gives `(0x9F1DB7, 0x4C1)`.
  - `profile.PKG3` is 3 cores, 3 banks, 256 words, FIFO depth 8.
  - `known_id` accepts `0x50450002`, `0x50453303` and `0x50452203`.
- **Model suite at `effd086`** (my runner1 clone, `remote_test.sh`'s pytest flags but `-n 8`, with `--ignore=test/model/test_swd.py`): rc 0, "2960 passed, 29 skipped in 1898.61s (0:31:38)", which matches claim 8. At the tip: see 072's report.
- **Mutants.** `test/mutants.py` at `3a7feab` on runner1, with OSS CAD Suite's cocotb and the test venv's pytest. Every baseline passed; `mutants.py` refuses to report a kill otherwise. All 7 of 7 are killed:

  | mutant | killed by |
  |---|---|
  | `model-crc-poly-hi-ignored` | `test_crc_catalogue_check_values[17-…]` |
  | `model-table-no-extra-tick` | `test_table_read_bank_relative_on_core2` |
  | `model-table-word-at-once` | `test_table_read_takes_two_ticks` |
  | `model-ticks-read-after` | `test_ticks_counts_ticks_from_restart` |
  | `isa-cmpw-imm-low-only` | `test_cmpw_sets_z_and_c_and_writes_nothing` |
  | `asm-not-t-as-always` | `test_jmp_not_t` |
  | `timing-table-read-one-tick` | `test_timing_checker_counts_the_table_read_clock` |

- **Tests.** `test/model/test_v03_isa.py` has 39 test functions, which fits claim 7's "44 tests (39 functions)" with the parametrized CRC catalogue. `test_v02_firmware_uses_no_v03_encoding` exists (`:590`).
- **Privacy.**
  - `record_check.py --repo . --tree HEAD --commits next-8x4..HEAD` at `3a7feab`: "tree HEAD: 727 files checked", "commit messages checked: 14", "TOTAL flagged: 0".
  - My private-string check over the branch diff and messages: "TOTAL 0".
  - A pattern grep finds no home path, address or email other than the noreply trailers.
  - All 14 commits are by the noreply identity and carry the trailer.
  - `git merge-base --is-ancestor b2fa7f3 HEAD` exits 1.
- **ISA timing check.** The checker's table-read rule (1 tick at CLKDIV 1, 0 at INT ≥ 2, 0..1 with a fraction) matches §4.2 and §5.2. `timing-table-read-one-tick` is killed.

## What I ran

- **Locally** (light, Python only): `git archive` of `d2ed344` and `3a7feab` `tools/`, `firmware/` and `test/model/` into scratch. My assembly comparison and encoding scan. `pe.mpy_bundle --check`. Reading `docs/isa.md`, `sim.py`, `asm.py` and `isa.py` diffs, and the RTL.
- **runner1** (`~/protoemu-flow/verifier-070/`, my own clone of `ao-pkg3`): the model suite at `effd086`, and `test/mutants.py` with the 7 names above. No harden.

Summary: the spec additions sit in reserved space, the model matches the spec at every corner I read, and v0.2 firmware is byte-identical with no v0.3 encodings. Tighten the two §5.1/§8 sentences (finding 1).
