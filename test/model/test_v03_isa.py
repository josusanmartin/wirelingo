"""The v0.3 ISA additions on the model (docs/isa.md §4.1, §4.5, §5, §6.2, §8).

Each takes encoding space v0.2 reserved or ignored:
  cmp rd, #imm16     LDI with bit 18 set (CMPW)
  jmp/call !t, a     JMP/CALL cond 0 (always) with sel 1
  mov osr, [src]     MOV op 3: OSR = the word at {PC[9:8], src[7:0]} of the own bank,
                     at the end of the next clock, which the core spends on it
  flag tog k         FLAG with bit 17 set
  ticks              source 15: the core's tick counter
  MOV/OUT PC         bank-relative: PC = {PC[9:8], value[7:0]}
  CRCCTRL[23]        CRC_W[4]: any width 1..32, POLY_HI at core offset 0x11
Ported from the v3 study's model tests (its multi-bit tap and fixed-top CRC-32
left out) and extended for the 8x4 package's choices.
"""
import pathlib
import random
import zlib

import pytest

from pe import asm, host, isa, sim, timing, usb
from pe.modelhost import ModelHost
from pe.profile import FULL, PKG3, validate_image

ROOT = pathlib.Path(__file__).resolve().parents[2]


def run(src, core=0, steps=80, r=(), crcctrl=None, poly_hi=None, ncores=4, **cfg):
    mh = ModelHost(ncores=ncores, nbanks=ncores)
    prog = asm.assemble(src)
    mh.apply(host.load_program(core, prog, profile=PKG3 if core < 3 else FULL, **cfg))
    if crcctrl is not None:
        mh.write(sim.core_reg(core, sim.C_CRCCTRL), crcctrl)
    if poly_hi is not None:
        mh.write(sim.core_reg(core, sim.C_POLY_HI), poly_hi)
    for i, v in enumerate(r):
        mh.write(sim.core_reg(core, sim.C_R0 + i), v)
    mh.start(1 << core)
    mh.step(steps)
    return mh, mh.chip.cores[core], prog


# ---------------------------------------------------------------- CMPW


def test_cmpw_sets_z_and_c_and_writes_nothing():
    _, c, _ = run("""
        cmp r0, 0x800D      ; equal
        jmp !z, bad
        cmp r1, 0x800D      ; r1 < 0x800D: C
        jmp !c, bad
        cmp r2, 0x800D      ; r2 > 0x800D
        jmp c, bad
        jmp z, bad
        cmpw r3, 0          ; the cmpw form with a small immediate
        jmp !z, bad
        ldi r3, 1
        halt
    bad:
        halt
    """, r=(0x800D, 0x1234, 0xF000))
    assert c.halted and c.r[3] == 1 and c.r[:3] == [0x800D, 0x1234, 0xF000]


def test_cmpw_every_immediate_bit():
    """The split immediate {x[17:16], x[13:0]}: one bit at a time, and rd in [15:14]."""
    for bit in range(16):
        for rd in range(4):
            k = 1 << bit
            w = asm.assemble(f"cmpw r{rd}, {k}").words[0]
            assert w >> 27 == isa.OP_LDI and w & isa.LDI_CMPW and isa.bits(w, 15, 14) == rd
            assert isa.cmpw_imm(w & 0x7FFFF) == k
            assert asm.assemble(isa.disasm(w)).words[0] == w


def test_cmp_small_immediate_stays_an_alu_word():
    p = asm.assemble("cmp r1, 0x1FFF\ncmp r1, 0x2000\ncmpw r1, 5\ncmp r1, r2")
    assert [w >> 27 for w in p.words] == [isa.OP_ALU, isa.OP_LDI, isa.OP_LDI, isa.OP_ALU]
    with pytest.raises(asm.AsmError):
        asm.assemble("cmp r1, 0x10000")
    with pytest.raises(asm.AsmError):
        asm.assemble("add r1, 0x2000")          # only the compare has a 16-bit form


def test_with_imm16_patches_ldi_and_cmpw():
    for src in ("ldi r2, 0", "cmpw r2, 0"):
        w = asm.assemble(src).words[0]
        for k in (0, 1, 0x3FFF, 0x4000, 0xC000, 0xFFFF):
            p = isa.with_imm16(w, k)
            assert (isa.cmpw_imm(p) if p & isa.LDI_CMPW else p & 0xFFFF) == k
            assert p & ~(isa.CMPW_IMM_MASK if p & isa.LDI_CMPW else 0xFFFF) == w & ~(
                isa.CMPW_IMM_MASK if p & isa.LDI_CMPW else 0xFFFF)


# ---------------------------------------------------------------- jmp !t


def test_jmp_not_t():
    _, c, _ = run("""
        ldi r3, 0
        wait 1 flag 0 timeout r3   ; times out: T = 1
        jmp !t, bad
        ldi r3, 0
        wait 0 flag 0 timeout r3   ; met: T = 0
        jmp !t, good
    bad:
        halt
    good:
        ldi r0, 7
        halt
    """)
    assert c.halted and c.r[0] == 7


def test_call_not_t_links():
    _, c, p = run("""
        ldi r3, 0
        wait 0 flag 0 timeout r3   ; T = 0
        call !t, sub
        halt
    sub:
        ldi r1, 9
        ret
    """)
    assert c.halted and c.r[1] == 9 and c.lr == p.labels["sub"] - 1


def test_plain_jmp_is_unchanged():
    """cond 0 with sel 0 (what the assembler emits) and any sel but 1: always."""
    assert asm.assemble("jmp 5").words[0] == 5
    for sel in (0, 2, 3, 31):
        word = isa.enc(isa.OP_JMP, 0, (sel << 10) | 4)
        _, c, _ = run(f"""
            ldi r3, 0
            wait 1 flag 0 timeout r3   ; T = 1
            .word {word:#x}
            halt
            ldi r0, 1
            halt
        """)
        assert c.r[0] == 1, sel


# ---------------------------------------------------------------- table read


def test_table_read_bank_relative_on_core2():
    """`mov osr, [r1]` on core 2 reads its own bank, at index r1[7:0]."""
    _, c, _ = run("""
        ldi r1, table
        mov osr, [r1]
        mov r0, osr         ; low half
        add r1, 0x301       ; the high bits are ignored: table + 1
        mov osr, [r1]
        mov r2, osr
        mov isr, osr
        halt
    table:
        .data 0xCAFE1234
        .data 0x00005678
    """, core=2, ncores=3, steps=40)
    assert c.halted and not c.fault
    assert c.r[0] == 0x1234 and c.r[2] == 0x5678 and c.isr == 0x5678


def _pin_trace(src, n, **kw):
    mh, c, _ = run(src, steps=0, **kw)
    seen = []
    for _ in range(n):
        mh.step()
        seen.append(mh.chip.pin_out & 1)
    return seen


TWO_TICKS = """
    ldi r0, t
    mov osr, [r0]
    pin 0, hi
    halt
t:  .data 1
"""


def test_table_read_takes_two_ticks():
    # ldi (1), table read (2), pin (1): the pin register changes after 4 ticks
    assert _pin_trace(TWO_TICKS, 10).index(1) == 4
    # a plain MOV instead: one tick fewer
    assert _pin_trace(TWO_TICKS.replace("[r0]", "r0"), 10).index(1) == 3


def test_table_read_extra_clock_hides_in_a_slow_divider():
    """At CLKDIV 2 the word arrives on the idle clock: no extra tick."""
    assert _pin_trace(TWO_TICKS, 12, clkdiv=2 << 8).index(1) == 5   # ticks at clocks 0, 2, 4 (+1: the start)


def test_table_read_with_delay_and_back_to_back():
    """The clock after the read isn't a delay tick, and two reads in a row each get theirs."""
    plain = _pin_trace("""
        ldi r0, t
        mov osr, [r0] [2]
        mov osr, [r0]
        pin 0, hi
        halt
    t:  .data 1
    """, 14)
    assert plain.index(1) == 1 + (1 + 1 + 2) + (1 + 1) + 1


def test_table_read_fractional_divider_int1():
    """INT 1 with a fraction: the clock after the read is a tick unless a carry
    stretched the divider. Each loop is 5 instructions, plus that tick when it is one."""
    mh, c, _ = run("""
    loop:
        mov osr, [r1]
        mov r0, ticks
        in r0, 16
        push noblock
        jmp loop
    t:  .data 0
    """, steps=0, clkdiv=(1 << 8) | 0x55, r=(0, 5))
    got = []
    for _ in range(60):
        mh.step(20)
        got += [w & 0xFFFF for w in mh.pop_all(0)]
    d = [(b - a) & 0xFFFF for a, b in zip(got, got[1:])]
    assert set(d) == {5, 6}, d
    st = mh.chip.stats
    assert 0 < st["table:extra_tick"] < st["table:read"]


def test_table_read_word_reaches_osr_one_clock_later():
    """The host sees the old OSR in the clock after the MOV; restart, host PC writes."""
    prog = asm.assemble("""
        ldi r1, t
        mov osr, [r1]
        halt
    t:  .data 0xA5A5F00D
    """)
    mh = ModelHost()
    mh.apply(host.load_program(0, prog))
    mh.start(1)
    core = mh.chip.cores[0]
    mh.step()                          # the restart's clock
    mh.step()                          # ldi
    mh.step()                          # the MOV's tick
    assert core.osr == 0 and core.tbl == 0xA5A5F00D
    assert mh.chip.read(sim.core_reg(0, sim.C_OSR)) == 0
    mh.step()                          # its word
    assert core.osr == 0xA5A5F00D and core.osr_cnt == 0 and core.tbl is None


def _table_case(action):
    """Run to the MOV's tick, then do `action` there (0) or in the next clock (1)."""
    prog = asm.assemble("""
        ldi r1, t
        mov osr, [r1]
        jmp 0
    t:  .data 0x12345678
    """)
    mh = ModelHost()
    mh.apply(host.load_program(0, prog))
    mh.start(1)
    mh.step(2)                         # the restart's clock, ldi: the MOV's tick is next
    return mh, mh.chip.cores[0]


def test_table_read_restart_in_the_next_clock_wins():
    mh, core = _table_case(None)
    mh.write(sim.A_CTRL, 1 | (1 << 8))  # the MOV's tick; the restart acts in the next clock
    assert core.tbl is not None
    mh.step()
    assert core.osr == 0 and core.tbl is None and core.pc == 0
    assert mh.chip.stats.get("table:restart_wins")


def test_table_read_cancelled_by_a_host_pc_write():
    mh, core = _table_case(None)
    mh.write(sim.core_reg(0, sim.C_PC), 0)   # in the MOV's own clock
    assert core.tbl is None and core.osr == 0 and core.pc == 0
    mh.step(2)
    assert core.osr == 0
    assert mh.chip.stats.get("table:cancelled_by_host_pc")


def test_table_read_word_written_when_the_core_is_disabled_in_that_clock():
    """Disable so that the clock after the MOV is a disabled one: the word still lands."""
    prog = asm.assemble("""
        ldi r1, t
        mov osr, [r1]
        jmp 0
    t:  .data 0x0BADCAFE
    """)
    mh = ModelHost()
    mh.apply(host.load_program(0, prog))
    mh.start(1)
    mh.step()                             # the restart's clock
    mh.write(sim.A_CTRL, 0)               # ldi; a CTRL enable acts for the clock after next
    core = mh.chip.cores[0]
    mh.step()                             # the MOV's tick, still enabled
    assert core.tbl is not None and core.osr == 0
    mh.step()                             # disabled: the word lands anyway
    assert not mh.chip.enable and core.osr == 0x0BADCAFE and core.tbl is None
    assert mh.chip.stats.get("table:write_while_disabled")
    mh.step(5)
    assert core.pc == 2                   # frozen after the MOV


def test_table_read_only_to_osr():
    with pytest.raises(asm.AsmError):
        asm.assemble("mov r0, [r1]")
    w = asm.assemble("mov osr, [r1] [3]").words[0]
    assert isa.bits(w, 14, 13) == isa.M_TBL and (w >> 19) & 0xFF == 3 and isa.disasm(w).startswith("mov osr, [r1]")


def test_data_words_are_not_checked_as_jumps():
    p = asm.assemble("""
        halt
    t:  .data 0x3FF          ; looks like `jmp 1023`
    """)
    assert p.data == {1}
    host.load_program(1, p, profile=PKG3)       # would leave core 1's bank as a JMP
    with pytest.raises(ValueError):
        validate_image(1, p.words, 256, 256 | (257 << 16), PKG3)
    assert p.relocate(256)[1] == 0x3FF


# ---------------------------------------------------------------- flag tog


def test_flag_toggle():
    mh, c, _ = run("""
        flag tog 2
        flag tog 2
        flag tog 2
        halt
    """)
    assert mh.chip.flags == 0b100


def test_flag_toggle_against_another_cores_set():
    """Core 0 toggles F3 off while core 1 sets it in the same cycle: the set wins."""
    mh = ModelHost()
    mh.apply(host.load_program(0, asm.assemble("flag tog 3\nhalt")))
    mh.apply(host.load_program(1, asm.assemble("flag set 3\nhalt"), 256))
    mh.write(sim.A_FSET, 8)
    mh.start(3)
    mh.step(5)
    assert mh.chip.flags & 8


# ---------------------------------------------------------------- bank-relative PC


def test_bank_relative_computed_jump_on_core1():
    """v0.2: `mov pc, r` jumps to an absolute address, so a jump table assembled at
    0 faults on core 1. v0.3 keeps the bank."""
    _, c, _ = run("""
        ldi r0, target
        mov pc, r0
        halt
    target:
        ldi r1, 1
        halt
    """, core=1)
    assert c.halted and not c.fault and c.r[1] == 1 and c.pc >> 8 == 1


def test_bank_relative_out_pc_on_core2():
    _, c, _ = run("""
        ldi r0, 0x104        ; another bank's bits: only [7:0] count
        mov osr, r0
        out pc, 16
        halt
        ldi r1, 2
        halt
    """, core=2, ncores=3, shift_out=(True, False, 32))
    assert c.halted and not c.fault and c.r[1] == 2 and c.pc == 0x205


def test_jmp_still_faults_outside_the_bank():
    """JMP targets stay absolute: one into bank 3 (a word the loader doesn't check) faults."""
    _, c, _ = run(".data 0x300", core=1)
    assert c.fault and c.pc == 0x300


# ---------------------------------------------------------------- tick counter


def test_ticks_counts_ticks_from_restart():
    mh, c, _ = run("""
        mov r0, ticks         ; 0: the first tick after a restart
        nop [3]
        mov r1, ticks         ; 1 + 4 ticks later
        in ticks, 16
        halt
    """, clkdiv=3 << 8, steps=60)
    assert c.halted and c.r[0] == 0 and c.r[1] == 5 and c.isr == 6


def test_ticks_holds_while_disabled_and_runs_while_halted():
    mh, c, _ = run("halt", steps=0)
    mh.step(20)                                  # the restart's clock, then 19 ticks
    assert c.halted and c.ticks == 19
    mh.write(sim.A_CTRL, 0)
    t = c.ticks
    mh.step(30)
    assert c.ticks == t + 1                      # the enable falls two clocks after the write
    mh.start(1)
    assert c.ticks == t + 1
    mh.step()
    assert c.ticks == 0                          # restart clears it


def test_ticks_wraps_mod_2_16():
    _, c, _ = run("""
        mov r0, ticks        ; tick 0
        ldi r1, 30000
    l1: jmp r1--, l1         ; 30001 ticks
        ldi r1, 30000
    l2: jmp r1--, l2
        ldi r1, 10000
    l3: jmp r1--, l3
        mov r2, ticks
        sub r2, r0           ; one subtraction, mod 2**16
        halt
    """, steps=70020)
    n = 1 + 1 + 30001 + 1 + 30001 + 1 + 10001
    assert c.halted and c.r[0] == 0 and c.r[2] == n % 65536 and n > 65536


def test_ticks_not_an_out_destination():
    with pytest.raises(asm.AsmError):
        asm.assemble("out ticks, 1")
    assert isa.v03_feature(asm.assemble("mov r0, ticks").words[0]) == "ticks"


# ---------------------------------------------------------------- CRC of any width


def ref_crc(bits, width, poly, init):
    m = (1 << width) - 1
    crc = init & m
    for b in bits:
        fb = ((crc >> (width - 1)) & 1) ^ b
        crc = ((crc << 1) & m) ^ ((poly & m) if fb else 0)
    return crc


def _feed_program(n, shr):
    return f"""
    .shift_in {'right' if shr else 'left'}
    loop:
        wait 1 flag 0 clear
        in r0, {n}
        flag set 1
        jmp loop
    """


def _run_feed(bits, width, poly, init, ctrl_extra=0, poly_hi=None):
    mh = ModelHost()
    mh.apply(host.load_program(0, asm.assemble(_feed_program(1, True))))
    ctrl, hi = isa.crcctrl(poly, width, in_tap=1)
    mh.write(sim.core_reg(0, sim.C_CRCCTRL), ctrl | ctrl_extra)
    mh.write(sim.core_reg(0, sim.C_POLY_HI), hi if poly_hi is None else poly_hi)
    mh.write(sim.core_reg(0, sim.C_CRC), init)
    mh.start(1)
    for v in bits:
        mh.write(sim.core_reg(0, sim.C_R0), v)
        mh.write(sim.A_FSET, 1)
        while not mh.chip.flags & 2:
            mh.step()
        mh.write(sim.A_FCLR, 2)
    return mh.chip.cores[0].crc


def msb_bits(data):
    return [(b >> (7 - i)) & 1 for b in data for i in range(8)]


@pytest.mark.parametrize("width,poly,init,check", [
    (17, 0x1685B, 0, 0x04F03),               # CRC-17/CAN-FD
    (21, 0x102899, 0, 0x0ED841),             # CRC-21/CAN-FD
    (24, 0x5D6DCB, 0xFEDCBA, 0x7979BD),      # CRC-24/FLEXRAY-A
    (32, 0x04C11DB7, 0xFFFFFFFF, 0x0376E6E7),  # CRC-32/MPEG-2
    (16, 0x1021, 0xFFFF, 0x29B1),            # CRC-16/IBM-3740 (a v0.2 width)
    (15, 0x4599, 0, 0x059E),                 # CRC-15/CAN
])
def test_crc_catalogue_check_values(width, poly, init, check):
    """Standard non-reflected CRCs over "123456789", bits MSB first."""
    bits = msb_bits(b"123456789")
    assert ref_crc(bits, width, poly, init) == check
    assert _run_feed(bits, width, poly, init) == check


def test_crc32_ethernet_residue():
    """802.3 FCS through the tap, bits in wire order (LSB first): residue 0xC704DD7B."""
    rng = random.Random(9)
    frame = bytes(rng.randrange(256) for _ in range(60))
    fcs = zlib.crc32(frame).to_bytes(4, "little")
    bits = [(b >> i) & 1 for b in frame + fcs for i in range(8)]
    assert _run_feed(bits, 32, 0x04C11DB7, 0xFFFFFFFF) == 0xC704DD7B
    bad = bits[:]
    bad[17] ^= 1
    assert _run_feed(bad, 32, 0x04C11DB7, 0xFFFFFFFF) != 0xC704DD7B


def test_crc_random_widths_match_the_reference():
    rng = random.Random(21)
    for _ in range(12):
        w = rng.randrange(1, 33)
        poly, init = rng.getrandbits(w), rng.getrandbits(w)
        bits = [rng.getrandbits(1) for _ in range(rng.randrange(1, 40))]
        assert _run_feed(bits, w, poly, init) == ref_crc(bits, w, poly, init), (w, poly)


def test_crc_v02_widths_ignore_poly_hi():
    """W <= 16 is v0.2's tap: POLY_HI is masked off and CRC writes keep 16 bits."""
    rng = random.Random(4)
    bits = [rng.getrandbits(1) for _ in range(50)]
    a = _run_feed(bits, 16, 0x8005, 0xFFFF, poly_hi=0)
    assert _run_feed(bits, 16, 0x8005, 0xFFFF, poly_hi=0xFFFF) == a == ref_crc(bits, 16, 0x8005, 0xFFFF)
    core = sim.Core(0)
    core.write_reg(sim.C_CRCCTRL, 15 << 16)
    core.write_reg(sim.C_CRC, 0xDEADBEEF)
    assert core.read_reg(sim.C_CRC) == 0xBEEF           # v0.2: 16 bits
    core.write_reg(sim.C_CRCCTRL, isa.crcctrl(0, 32)[0])
    core.write_reg(sim.C_CRC, 0xDEADBEEF)
    assert core.read_reg(sim.C_CRC) == 0xDEADBEEF


def test_mov_crc_reads_32_bits():
    ctrl, hi = isa.crcctrl(0x04C11DB7, 32)
    _, c, _ = run("""
        mov crc, ~null
        mov isr, crc
        mov r0, crc
        halt
    """, crcctrl=ctrl, poly_hi=hi)
    assert c.isr == 0xFFFFFFFF and c.crc == 0xFFFFFFFF and c.r[0] == 0xFFFF


def test_crcctrl_fields():
    assert isa.crcctrl(0x8005, 16, 1, 0) == (0x8005 | (15 << 16) | (1 << 20), 0)
    assert isa.crcctrl(0x04C11DB7, 32, 0, 1) == (0x1DB7 | (15 << 16) | (1 << 21) | (1 << 23), 0x04C1)
    with pytest.raises(ValueError):
        isa.crcctrl(0, 33)
    with pytest.raises(ValueError):
        isa.crcctrl(0x20000, 17)


# ---------------------------------------------------------------- the chip


def test_id_encodes_the_geometry():
    assert sim.Chip(ncores=3, nbanks=3).read(sim.A_ID) == 0x50453303
    assert sim.Chip(ncores=2, nbanks=2).read(sim.A_ID) == 0x50452203
    assert sim.Chip().read(sim.A_ID) == 0x50454403
    assert sim.ID_V2 == 0x50450002


def test_three_cores_run_and_the_fourth_is_absent():
    mh = ModelHost(ncores=3, nbanks=3)
    for c in range(3):
        mh.apply(host.load_program(c, asm.assemble(f"ldi r0, {c + 10}\nhalt"), profile=PKG3))
    with pytest.raises(ValueError):
        host.load_program(3, asm.assemble("halt"), profile=PKG3)
    mh.write(sim.core_reg(3, sim.C_R0), 5)
    mh.start(15)
    mh.step(10)
    assert [mh.chip.read(sim.core_reg(c, sim.C_R0)) for c in range(4)] == [10, 11, 12, 0]
    assert [core.halted for core in mh.chip.cores[:3]] == [1, 1, 1]


def test_poly_hi_is_write_only_and_kept_on_restart():
    mh = ModelHost()
    mh.write(sim.core_reg(1, sim.C_POLY_HI), 0x1234_04C1)
    assert mh.chip.cores[1].crc_poly_hi == 0x04C1
    assert mh.chip.read(sim.core_reg(1, sim.C_POLY_HI)) == 0
    mh.start(2)
    assert mh.chip.cores[1].crc_poly_hi == 0x04C1


# ---------------------------------------------------------------- v0.2 programs


V03_FIRMWARE = {"eth10_rx_fcs.pasm", "eth10_tx_fcs.pasm", "usb_enum_ep1.pasm"}


def test_v02_firmware_uses_no_v03_encoding():
    """docs/isa.md §8: v0.2 programs assemble to v0.2 words, so they run unchanged."""
    import re
    for f in sorted((ROOT / "firmware").glob("*.pasm")):
        if f.name in V03_FIRMWARE:
            continue
        text = f.read_text()
        names = set(re.findall(r"\b([A-Z][A-Z0-9_]*)\b", text))
        defs = {n: 1 for n in names}
        try:
            progs = asm.assemble_all(text, defs)
        except asm.AsmError:
            continue
        for p in progs:
            used = {isa.v03_feature(w) for i, w in enumerate(p.words) if i not in p.data} - {None}
            assert not used, (f.name, used)
            assert not p.data, f.name


def test_v03_feature_classifier():
    cases = {"cmpw r1, 3": "cmpw", "jmp !t, 0": "not_t", "call !t, 0": "not_t",
             "mov osr, [r2]": "table", "flag tog 1": "tog", "in ticks, 3": "ticks",
             "mov r1, ticks": "ticks", "jmp 0": None, "ldi r1, 3": None, "flag clr 1": None,
             "mov osr, ::r2": None, "cmp r1, 3": None}
    for src, want in cases.items():
        assert isa.v03_feature(asm.assemble(src).words[0]) == want, src


# ---------------------------------------------------------------- static timing


def test_timing_checker_counts_the_table_read_clock():
    src = """
    top:
        wait 1 pin 0      ; @point a
        mov osr, [r1]
        pin 1, hi         ; @point b
        jmp top
    """
    for div, want in (((1, 0), (1, 1)), ((2, 0), (0, 0)), ((1, 0x80), (0, 1)), ((0, 0), (0, 0))):
        chk = timing.Checker(asm.assemble(src), clkdiv=div)
        assert chk.delay(1) == want, div          # the clock after the read, as ticks
        assert chk.delay(2) == (0, 0)
