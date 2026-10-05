"""CALL / RET and the link register (docs/isa.md §5.2) on the model."""
from pe import asm, host, sim
from pe.modelhost import ModelHost


def run(src, steps=60, r=(), **cfg):
    mh = ModelHost()
    prog = asm.assemble(src)
    mh.apply(host.load_program(0, prog, **cfg))
    for i, v in enumerate(r):
        mh.write(sim.core_reg(0, sim.C_R0 + i), v)
    mh.start(1)
    mh.step(steps)
    return mh.chip.cores[0], prog


def test_call_links_and_ret_returns():
    c, p = run("""
        call sub            ; 0
        add r1, 1           ; 1: after the return
        halt                ; 2
    sub:
        add r0, 10          ; 3
        ret                 ; 4: mov pc, lr
    """)
    assert c.halted and c.r[0] == 10 and c.r[1] == 1 and c.lr == 1


def test_conditional_call_links_only_when_taken():
    c, _ = run("""
        call r0, sub        ; 0: r0 = 0: not taken, LR unchanged (0)
        mov r2, lr          ; 1
        call !r0, sub       ; 2: taken, LR = 3
        halt                ; 3
    sub:
        mov r1, lr
        ret
    """)
    assert c.halted and c.r[2] == 0 and c.r[1] == 3 and c.lr == 3


def test_nesting_through_a_register():
    """One link register: a subroutine that calls another saves LR first."""
    c, _ = run("""
        call outer
        halt
    outer:
        mov r3, lr          ; save
        call inner
        mov lr, r3          ; restore
        add r0, 1
        ret
    inner:
        add r0, 100
        ret
    """)
    assert c.halted and c.r[0] == 101


def test_call_at_wrap_top_returns_to_wrap_bottom():
    """The link is the next PC as if not jumping, so at .wrap it is .wrap_target."""
    c, _ = run("""
        jmp go
    .wrap_target
    loop:
        add r1, 1
        jmp r1, check       ; second time round: stop
        nop
    go:
        call sub            ; the last word of the wrap: links to `loop`
    .wrap
    sub:
        add r0, 1
        ret
    check:
        halt
    """)
    assert c.halted and c.r[0] == 1 and c.r[1] == 1


def test_lr_survives_restart():
    c, _ = run("""
        call sub [5]
    back:
        halt
    sub:
        mov r2, pc
        ret
    """, steps=30)
    assert c.halted and c.lr == 1 and c.r[2] == 2
    mh = ModelHost()
    mh.chip.cores[0].lr = 77
    mh.chip.cores[0].restart()
    assert mh.chip.cores[0].lr == 77
