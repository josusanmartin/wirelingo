"""WAIT kind 7, `wait pins != rN [mask m]` (docs/isa.md §5.2), on the model:
the condition, its mask and IN_BASE, the first attempt, a difference caught
between ticks, and the timeout."""
from pe import asm, host, sim
from pe.modelhost import ModelHost


def core0(src, in_base=0, clkdiv=1 << 8, r=()):
    mh = ModelHost()
    mh.ext_oe = 0xFF                 # the test drives every uio pin
    mh.apply(host.load_program(0, asm.assemble(src), in_pins=in_base, clkdiv=clkdiv))
    for i, v in enumerate(r):
        mh.write(sim.core_reg(0, sim.C_R0 + i), v)
    return mh


PROG = """
    wait pins != r0 mask 0x0C timeout r3
    mov r1, pc                       ; marks completion
    halt
"""


def done(mh):
    return mh.chip.cores[0].halted


def test_first_attempt_and_mask():
    mh = core0(PROG, r=(0b0100, 0, 0, 100))
    mh.uio = 0b0100                  # equal under the mask (bits 2, 3)
    mh.step(3)
    mh.start(1)
    mh.uio = 0b0111                  # bits 0-1 are masked out
    mh.step(20)
    assert not done(mh)
    mh.uio = 0b1100                  # bit 3 differs
    mh.step(10)
    assert done(mh) and mh.chip.cores[0].t == 0


def test_already_different_completes_on_the_first_attempt():
    mh = core0(PROG, r=(0, 0, 0, 100))
    mh.uio = 0b1000
    mh.step(3)
    mh.start(1)
    mh.step(2)                       # the restart (CTRL acts a cycle late), then the first attempt
    assert mh.chip.cores[0].pc == 1 and mh.chip.cores[0].r[3] == 100


def test_in_base_relative():
    mh = core0(PROG, in_base=16, r=(0, 0, 0, 100))       # channels from ui0
    mh.uio = 0xFF                    # not watched
    mh.step(3)
    mh.start(1)
    mh.step(10)
    assert not done(mh)
    mh.ui = 0b0100                   # ui2 = P18 = channel 2
    mh.step(10)
    assert done(mh)


def test_pulse_between_ticks_and_timeout():
    mh = core0(PROG, clkdiv=8 << 8, r=(0, 0, 0, 100))      # a tick every 8 clocks
    mh.start(1)
    mh.step(20)
    pc_before = mh.chip.cores[0].pc
    assert pc_before == 0 and mh.chip.cores[0].in_wait
    mh.uio = 0b0100                  # a one-clock pulse between two ticks
    mh.step(1)
    mh.uio = 0
    mh.step(40)                      # the WAIT completes on the next tick; mov, halt 8 clocks apart
    assert done(mh) and mh.chip.cores[0].t == 0           # caught by the sticky event bit
    mh = core0(PROG, r=(0, 0, 0, 5))
    mh.start(1)
    mh.step(20)
    c = mh.chip.cores[0]
    assert done(mh) and c.t == 1 and c.r[3] == 0          # N + 1 = 6 unsuccessful ticks


def test_timeout_counts_ticks():
    """The timeout register counts the waiting ticks: the WAIT's own timestamp."""
    mh = core0(PROG, r=(0, 0, 0, 1000))
    mh.start(1)
    mh.step(2 + 37)
    mh.uio = 0b0100
    mh.step(10)
    assert done(mh) and 1000 - mh.chip.cores[0].r[3] in (38, 39, 40)
