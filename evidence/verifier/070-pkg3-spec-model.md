# 070 pkg3-spec-model

Branch `ao-pkg3`, made from `next-8x4` (public/main `d2ed344`);
`git merge-base --is-ancestor b2fa7f3 ao-pkg3` fails, as it must. Nothing was
cherry-picked: the v3 study's model and assembler work was ported by content
(a diff of `f0cdb80..v3-study -- tools/` applied as a patch, then reworked).
This item is the first three commits on `d2ed344`:

- `a53461b` docs/isa.md: the v0.3 ISA (and docs/asm.md);
- `39b05f3` tools/pe: v0.3 in the model, assembler and timing checker, and
  `test/model/test_v03_isa.py`;
- `effd086` tools: host software accepts the v0.2 and v0.3 IDs.

The branch goes on with the RTL (071) and the firmware (072); their
commits sit on top of these and do not change these files except
`tools/pe/randprog.py` (071) and `test/mutants.py`.

Backlog item AO. Evidence level: **model** (the Python cycle model) and the
static timing checker. No RTL claim here.

The builder's claims:

1. **Spec (docs/isa.md, now v0.3).** Every addition takes space v0.2 marked
   reserved or ignored; opcodes 14–31 stay reserved:
   - CMPW = LDI with `[18]` = 1, rd at `[15:14]`, imm16 = `{[17:16], [13:0]}`;
     Z/C as `cmp`, no write (§5, §5.2);
   - `jmp !t` / `call !t` = cond 0 with sel 1 (§5.2);
   - table read = MOV op 3 (v0.2: "reserved, behaves as none"):
     `OSR = IMEM[{PC[9:8], src[7:0]}]` at the end of the next clock; that
     clock is the read's (a tick there is spent); written even if the core
     is disabled then; a restart in that clock wins; a host PC write in the
     MOV's clock cancels it; a host OSR read in that clock sees the old value;
   - `flag tog k` = FLAG `[17]`, a set or a clear of F[k] as the core sees
     it (§5.2, §5.3);
   - bank-relative MOV/OUT PC: `{PC[9:8], value[7:0]}` (§5.1);
   - source 15 = TICKS, a 16-bit count of the core's ticks while enabled,
     cleared by reset and restart, read as the count before the reading
     instruction's tick (§4.1, §5.1);
   - CRC of any width 1..32: CRC_W gets bit 4 at CRCCTRL[23], POLY_HI
     (core offset 0x11) is POLY[31:16], the register is 32 bits, a host
     write keeps v0.2's 16 bits unless W > 16 (§4.5, §6.2);
   - ID = `0x5045BC03` (B = NBANKS, C = NCORES): 8x4 `0x50453303`, fallback
     `0x50452203`, v0.2 `0x50450002` (§6.2); §7 names 3/3/8 as the 8x4
     tapeout's build and 2/2/8 its fallback.
   §8 lists what a v0.2 program can see differently: the ID, an
   out-of-bank computed jump (faulted in v0.2), and hand-made words with
   reserved bits set.
2. **Backward compatible on the existing firmware.** Every `firmware/*.pasm`
   assembles to the same words with the new assembler as with next-8x4's:
   all 68 files under 5 define sets (340 combinations; 275 assemble, the
   other 65 fail the same way in both for missing defines). Scripted
   comparison, not committed; `test_v02_firmware_uses_no_v03_encoding`
   (model) asserts that no v0.2 program uses a v0.3 encoding.
3. **Model (`tools/pe/sim.py`)** implements §1 exactly, including the
   table read's late write as `Core.tbl` (consumed on the next clock by the
   chip step, after the cores and before restarts), and covers each corner
   (`table:*`, `cmpw:*`, `jmp:not_t_*`, `flag:tog_*`, `ticks:read`) for the
   lockstep goals. `sim.ID_VALUE` stays v0.2's (the board tools still talk
   to a v0.2 bitstream); `sim.id_value()` and `sim.known_id()` are new.
4. **Assembler/disassembler**: `cmp rN, K` with K ≥ 8192 → CMPW, `cmpw`,
   `jmp/call !t`, `mov osr, [src]` (only to the OSR), `flag tog k`,
   `ticks` as an IN/MOV source (OUT rejects it), `.data`. Round trips in the
   tests.
5. **Timing checker**: `Checker.delay()` is now (min, max): a table read
   adds 1 tick at CLKDIV 1, 0 at INT ≥ 2, 0..1 at INT 1 with a fraction;
   CMPW and `!t` as in the v3 study.
6. **Host tools**: `profile.PKG3` (3 cores, 3 banks), `ModelHost(ncores=,
   nbanks=)`, `isa.crcctrl(poly, width, ...)` gives CRCCTRL and POLY_HI,
   `.data` words are not checked as jumps by `validate_image`, the USB token
   filter patch handles a CMPW filter (and stays MicroPython-safe:
   `pe.mpy_bundle --check` passes), the board tools and the MicroPython
   driver accept both IDs (`known_id`).
7. **Tests**: `test/model/test_v03_isa.py`, 44 tests (39 functions): 12
   from the v3 study's 14 (its 2 multi-bit-tap tests dropped, its CRC-32
   tests moved to POLY_HI), 32 new (every CMPW immediate bit, `call !t`, the
   table read's two ticks / slow divider / fractional divider / delay /
   back-to-back / restart / host-PC cancel / disabled write / one-clock
   lateness, bank-relative OUT PC on core 2, JMP still faults out of bank,
   ticks (start, divider, disable, halt, wrap), CRC catalogue values for
   widths 15, 16, 17, 21, 24, 32, random widths against a reference, v0.2
   widths ignore POLY_HI, the ID, three cores, the encoding classifier, the
   checker's delay).
8. **Model suite** on runner1 at the tree of the branch's later commit
   `d5d8af0` (run from its pre-reword twin `aebb2d6`, same tree `0b0ea017`;
   this item's files are the same there): **2960 passed, 29 skipped**
   (next-8x4: 2916 passed, 29 skipped; +44 = test_v03_isa). A first run at
   `39b05f3` had 99 failures, all one cause: the board tools (test_fabric_ping,
   test_micropython_driver) checked the model's ID against v0.2's; fixed by
   `effd086` (`known_id`).

9. **Mutants** (test/mutants.py at `8c4c635`, runner1): `model-table-no-extra-tick`,
   `model-table-word-at-once`, `model-crc-poly-hi-ignored`, `model-ticks-read-after`,
   `isa-cmpw-imm-low-only`, `asm-not-t-as-always`, `timing-table-read-one-tick`:
   7 of 7 killed by test_v03_isa.
10. **Privacy**: `record_check.py --repo . --tree HEAD --commits next-8x4..HEAD`
    at the branch tip: 0 flagged.

The builder's caveats:
- The bank-relative PC is the one change visible to a v0.2 program that
  executes: a computed jump whose value leaves the bank used to fault and
  now stays in the bank. No program in the library does that; the v0.2
  assembler can't express the other reinterpreted encodings.
- `sim.ID_VALUE` keeps the v0.2 value on purpose (board tools); tests on
  the RTL use `sim.id_value(ncores, nbanks)`. `fpga/kv260/host/pe_kria.py`
  and `bringup_kria.py` still expect `0x50450002` exactly: board-only, left
  for the KV260 3-core item (AR).
- The OCaml port is v0.2 only (071 adds a note to docs/hardcaml-port.md).

Asked checks:
- **Read docs/isa.md v0.3 against the reserved list** of next-8x4's
  isa.md: is every new slot reserved/ignored there; is §8 complete (any
  v0.2-visible change missing?); is the table read's corner list (restart,
  host PC write, disable, host read) unambiguous.
- **Model vs spec**: read `sim.py`'s new code against §4.1, §4.5, §5.2,
  §5.3, §6.2. In particular the table read's order in `Chip.step` and the
  tick counter's read-before-increment.
- **Byte-identical firmware**: assemble `firmware/*.pasm` with next-8x4's
  and this branch's `tools/pe` (any define sets you like) and compare.
- **Model suite**: `remote_test.sh model` at `effd086` (and at the tip).
- **Mutants** `model-*`, `isa-cmpw-imm-low-only`, `asm-not-t-as-always`,
  `timing-table-read-one-tick` (in test/mutants.py on the tip): all killed.
- **Privacy**: `record_check.py --repo . --tree HEAD --commits next-8x4..HEAD`.
