"""Assembler self-test: every mnemonic form encodes to what the disassembler reads back."""
import pytest

from pe import asm, host, isa

CASES = {
    "nop": "mov null, null",
    "jmp 5": "jmp 5",
    "jmp !r1, 7": "jmp !r1, 7",
    "jmp r2, 7": "jmp r2, 7",
    "jmp r3--, 9": "jmp r3--, 9",
    "jmp pin 17, 3": "jmp pin 17, 3",
    "jmp !pin uio4, 3": "jmp !pin 4, 3",
    "jmp z, 1": "jmp z, 1", "jmp !z, 1": "jmp !z, 1", "jmp c, 1": "jmp c, 1", "jmp !c, 1": "jmp !c, 1",
    "jmp t, 1": "jmp t, 1", "jmp !osre, 1": "jmp !osre, 1", "jmp txne, 1": "jmp txne, 1",
    "jmp rxf, 1": "jmp rxf, 1", "jmp f 3, 1": "jmp f 3, 1", "jmp !f 7, 1": "jmp !f 7, 1",
    "wait 1 pin 9": "wait 1 pin 9", "wait 0 pin ui2": "wait 0 pin 18",
    "wait rise 3": "wait rise 3", "wait fall 3 if pin 4 == 1": "wait fall 3 if pin 4 == 1",
    "wait edge v2 timeout r1": "wait edge 26 timeout r1",
    "wait 1 flag 5 clear": "wait 1 flag 5 clear", "wait 0 flag 2 timeout": "wait 0 flag 2 timeout r3",
    "wait pins != r1 mask 0x0F timeout r2": "wait pins != r1 mask 0x0f timeout r2",
    "wait pins != x": "wait pins != r0 mask 0xff",
    "in pins, 8": "in pins, 8", "in r2, 32": "in r2, 32", "in null, 1": "in null, 1",
    "out pindirs, 3": "out pindirs, 3", "out pc, 5": "out pc, 5", "out isr, 16": "out isr, 16",
    "push": "push block", "push iffull noblock": "push iffull noblock",
    "pull ifempty": "pull ifempty block", "pull noblock": "pull noblock",
    "mov isr, ::osr": "mov isr, ::osr", "mov r1, ~pins": "mov r1, ~pins", "mov pc, r2": "mov pc, r2",
    "mov r0, status": "mov r0, status", "mov r3, flags": "mov r3, flags",
    "set pins, 21": "set pins, 21", "set pindirs, 3": "set pindirs, 3",
    "ldi r2, 65535": "ldi r2, 65535",
    "add r0, 8191": "add r0, 8191", "sub r1, r2": "sub r1, r2", "and r2, 7": "and r2, 7",
    "or r3, r0": "or r3, r0", "xor r0, 3": "xor r0, 3", "shl r1, 4": "shl r1, 4",
    "shr r1, r3": "shr r1, r3", "cmp r3, 0x50": "cmp r3, 80",
    "pin 5, lo": "pin 5, lo", "pin uo2, hi": "pin 10, hi", "pin 3, toggle": "pin 3, toggle", "pin 0, hiz": "pin 0, hiz",
    "flag set 6": "flag set 6", "flag clr 1": "flag clr 1",
    "halt": "halt",
    "call 12": "call 12", "call !r2, 7": "call !r2, 7", "call !pin uio3, 4": "call !pin 3, 4",
    "ret": "mov pc, lr", "mov r1, lr": "mov r1, lr", "mov lr, r3": "mov lr, r3",
}


def test_roundtrip():
    for src, want in CASES.items():
        p = asm.assemble(src + "\n")
        assert isa.disasm(p.words[0]) == want, (src, isa.disasm(p.words[0]))


def test_call_is_relocated():
    p = asm.assemble("call sub\njmp 0\nsub: ret\n")
    assert p.relocate(256)[:2] == [isa.enc(isa.OP_CALL, 0, 258), isa.enc(isa.OP_JMP, 0, 256)]


def test_side_delay():
    p = asm.assemble(".side_set 2 opt\n out pins, 1 side 2 [3]\n nop\n")
    op, sd, _ = isa.fields(p.words[0])
    assert sd == (1 << 7) | (2 << 5) | 3  # 5 delay bits, side at [6:5], enable at [7]
    assert isa.fields(p.words[1])[1] == 0


def test_clkdiv_rounding_carries():
    assert asm.assemble(".clkdiv 1.999\nnop").config["clkdiv"] == (2, 0)
    assert asm.assemble(".clkdiv 2.999\nnop").config["clkdiv"] == (3, 0)
    assert asm.assemble(".clkdiv 2.5\nnop").config["clkdiv"] == (2, 128)
    assert host.clkdiv_for(48e6, 48e6 / 1.999) == 2 << 8
    for bad in ("0.5", "65536"):
        with pytest.raises(asm.AsmError):
            asm.assemble(f".clkdiv {bad}\nnop")


@pytest.mark.parametrize("src", [
    "pull blcok",                       # typo used to mean `pull block`
    "push iffull ifempty",
    "pull block noblock",
    "out osr, 8",                       # OUT to OSR only discards (docs/isa.md)
    "out status, 4",
    ".shift_out rigth autopull 8\nnop",  # typo used to select left
    ".shift_in left autopull\nnop",     # autopull belongs to .shift_out
    ".shift_out right 8 16\nnop",
    ".shift_out right 33\nnop",
    ".fifo join_rx\nnop",
    "ldi r0",                           # missing operands used to raise IndexError
    "set pins",
    "pin 3",
    "flag set",
    "add r0",
    "wait pins != r0 mask 256",         # 8-bit mask
    "wait pins != r0 if pin 3 == 1",    # kind 7 has no qualifier (its bits are the mask)
    "wait pins != 5",
    "wait pins != r0 0x0F",
    "out lr, 4",                        # OUT to LR only discards (MOV writes it)
    "call",
    "call 1024",
])
def test_rejects_malformed(src):
    with pytest.raises(asm.AsmError):
        asm.assemble(src)
