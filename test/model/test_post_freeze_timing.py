"""The static timing checker (tools/pe/timing.py) on the post-freeze firmware
(docs/post-freeze-protocols.md): every annotated deadline passes, the
annotations leave the assembled words unchanged, and every interval measured
on the cycle model between the same points lies inside the checker's bound
(test_timing_check.py's soundness rule: a measurement outside it would be a
checker bug; measurements that broke a declared assumption are counted apart).

Run with -s for the reports and the measured ranges."""
import pathlib
import random

import pytest

from pe import asm, host, postfreeze as pf, sim, timing
from pe.modelhost import ModelHost

import test_timing_check as TC

FW = pathlib.Path(__file__).resolve().parents[2] / "firmware"

DEFS = {
    "dmx512_tx": {"TX": 8, "MTBS": 0},
    "i2s_tx": {},
    "dshot600": {"GAP": 454},
    "dshot_bidir": {"ESC": 0, "BIT": 80, "T0": 30, "T1": 60, "RBIT": 64, "RTMO": 4800, "GAP": 1000},
    "nec_tx": {"IR": 8},
    "nec_rx": {"IR": 16},
    "wiegand_tx": {"D0": 0, "D1": 1, "PW": 50, "IV": 2000, "FG": 20000},
    "wiegand_rx": {"D0": 0, "FTO": 10000, "PMAX": 500},
    "lin_commander": {"TX": 8, "RX": 16, "TB": 104, "BTMO": 416},
    "lin_responder": {"TX": 9, "RX": 16, "BRK": 1144, "TMIN": 80, "TMAX": 130, "BTMO": 416},
}


def check(name, **kw):
    return timing.check_source((FW / f"{name}.pasm").read_text(), DEFS[name], **kw)


@pytest.mark.parametrize("name", sorted(DEFS))
def test_post_freeze_annotations_leave_the_words_unchanged(name):
    text = (FW / f"{name}.pasm").read_text()
    assert "@deadline" in text
    a, b = asm.assemble(text, DEFS[name]), asm.assemble(TC.strip_annotations(text), DEFS[name])
    assert a.words == b.words and (a.wrap_bottom, a.wrap_top, a.config) == (b.wrap_bottom, b.wrap_top, b.config)


@pytest.mark.parametrize("name", sorted(DEFS))
def test_post_freeze_deadlines_pass(name):
    chk, results = check(name)
    print()
    for r in results:
        print(r.report())
    assert not chk.errors
    assert results and all(r.status == ("PASS (conditional)" if r.deadline.reach == "conditional" else "PASS")
                           for r in results), [(r.deadline.name, r.status) for r in results]


def sound(name, tracer, div, expect_clean=True):
    chk, results = check(name, clkdiv=(div >> 8, div & 0xFF))
    s = TC.soundness(chk, results, tracer, label=name)
    print(f"\n{name} at CLKDIV {div >> 8}+{div & 255}/256:")
    TC.show(s, results)
    if expect_clean:
        assert all(s[r.deadline.name][0] > 0 for r in results), s
    return s


def test_dmx512_sound():
    div = 1 << 8
    mh = ModelHost()
    prog = asm.assemble((FW / "dmx512_tx.pasm").read_text(), DEFS["dmx512_tx"])
    mh.apply(host.load_program(0, prog, out_pins=(8, 1), clkdiv=div))
    tr = timing.Tracer(mh.chip, 0, 0)
    mh.start(1)
    rng = random.Random(1)
    words = []
    for n in (1, 3, 9, 25, 40):
        words += pf.dmx_words(bytes(rng.getrandbits(8) for _ in range(n)))
    while words:                       # a host that is sometimes late
        if mh.tx_level(0) < 8 and rng.random() < 0.3:
            mh.push(0, words.pop(0))
        mh.step(rng.randrange(1, 60))
    mh.step(5000)
    sound("dmx512_tx", tr, div)


def test_i2s_sound():
    div = host.clkdiv_for(50e6, 64 * 48000)
    mh = ModelHost()
    prog = asm.assemble((FW / "i2s_tx.pasm").read_text())
    mh.apply(host.load_program(0, prog, out_pins=(10, 1), side_pins=8, clkdiv=div))
    tr = timing.Tracer(mh.chip, 0, 0)
    mh.start(1)
    rng = random.Random(2)
    for _ in range(40):                # with underruns
        if mh.tx_level(0) < 8 and rng.random() < 0.6:
            mh.push(0, rng.getrandbits(32))
        mh.step(rng.randrange(200, 2000))
    sound("i2s_tx", tr, div)


def test_dshot_sound():
    div = 10 << 8
    mh = ModelHost()
    prog = asm.assemble((FW / "dshot600.pasm").read_text(), DEFS["dshot600"])
    mh.apply(host.load_program(0, prog, side_pins=8, clkdiv=div))
    tr = timing.Tracer(mh.chip, 0, 0)
    mh.start(1)
    rng = random.Random(3)
    for _ in range(12):
        mh.push(0, rng.getrandbits(12))
        mh.step(rng.randrange(3000, 12000))
    sound("dshot600", tr, div)


def test_dshot_bidir_sound():
    mh = ModelHost()
    prog = asm.assemble((FW / "dshot_bidir.pasm").read_text(), DEFS["dshot_bidir"])
    mh.apply(host.load_program(0, prog, side_pins=0, in_pins=0, clkdiv=1 << 8))
    mh.pull_up = 1
    tr = timing.Tracer(mh.chip, 0, 0)
    mh.start(1)
    esc = pf.BidirEsc([pf.erpm_telemetry(p) for p in (100, 900, 30000)] + [None, pf.erpm_telemetry(5)])
    for v in (1000 << 1, 7 << 1, 2047 << 1, 3, 600 << 1):
        mh.push(0, v)
        for _ in range(12000):
            drive, lvl = esc.step(mh.chip.cycle * 1e9 / 48e6, mh.bus_uio() & 1)
            mh.ext_oe, mh.uio = drive, lvl
            mh.step()
            mh.pop_all(0)
    sound("dshot_bidir", tr, 1 << 8)


def test_nec_sound():
    """nec_tx and nec_rx on one model (the receiver-module model between
    them), both traced: standard, extended and repeat codes back to back."""
    f_clk = 456e3
    dtx, drx = host.clkdiv_for(f_clk, 114e3), host.clkdiv_for(f_clk, 100e3)
    mh = ModelHost()
    mh.apply(host.load_program(0, asm.assemble((FW / "nec_tx.pasm").read_text(), {"IR": 8}), clkdiv=dtx))
    mh.apply(host.load_program(1, asm.assemble((FW / "nec_rx.pasm").read_text(), {"IR": 16}),
                               in_pins=16, clkdiv=drx))
    ttx, trx = timing.Tracer(mh.chip, 0, 0), timing.Tracer(mh.chip, 1, 256)
    mh.ui = 1
    mh.start(3)
    for a, c, m in [(1, 2, "standard"), (0, 0, "repeat"), (0, 0, "repeat"), (0xABCD, 0x7E, "extended")]:
        mh.push(0, pf.nec_word(a, c, m))
    tsop = pf.IrReceiverModule()
    for _ in range(int(0.45 * f_clk)):
        mh.ui = tsop.step(mh.chip.cycle * 1e9 / f_clk, (mh.chip.pin_out >> 8) & 1)
        mh.step()
        mh.pop_all(1)
    sound("nec_tx", ttx, dtx)
    sound("nec_rx", trx, drx)


def test_wiegand_sound():
    div = 1 << 8
    mh = ModelHost()
    mh.apply(host.load_program(0, asm.assemble((FW / "wiegand_tx.pasm").read_text(), DEFS["wiegand_tx"]), clkdiv=div))
    mh.apply(host.load_program(1, asm.assemble((FW / "wiegand_rx.pasm").read_text(), DEFS["wiegand_rx"]),
                               in_pins=0, clkdiv=div))
    mh.write(sim.core_reg(0, sim.C_CRCCTRL), 1 | (1 << 21))
    mh.write(sim.core_reg(1, sim.C_CRCCTRL), 1 | (1 << 20))
    mh.write(sim.A_POUT, 3)
    mh.write(sim.A_OD, 3)
    mh.pull_up = 3
    ttx, trx = timing.Tracer(mh.chip, 0, 0), timing.Tracer(mh.chip, 1, 256)
    mh.start(3)
    for n, d, raw in [(24, 0x123456, False), (32, 0xCAFEF00D, False), (6, 0b101101, True)]:
        for w in pf.wiegand_words(n, d, raw):
            mh.push(0, w)
    for _ in range(170):
        mh.step(1000)
        mh.pop_all(1)
    sound("wiegand_tx", ttx, div)
    sound("wiegand_rx", trx, div)


def test_lin_sound():
    """lin_commander and lin_responder on one bus: publish, subscribe to a
    staged response, an unanswered header."""
    f_clk = 5e6
    dc, dr = host.clkdiv_for(f_clk, 19200 * 104), host.clkdiv_for(f_clk, 2e6)
    mh = ModelHost()
    mh.apply(host.load_program(0, asm.assemble((FW / "lin_commander.pasm").read_text(), DEFS["lin_commander"]),
                               out_pins=(8, 1), in_pins=16, clkdiv=dc))
    mh.apply(host.load_program(1, asm.assemble((FW / "lin_responder.pasm").read_text(), DEFS["lin_responder"]),
                               out_pins=(9, 1), in_pins=16, clkdiv=dr))
    mh.write(sim.core_reg(1, sim.C_CRCCTRL), 15 << 16)
    mh.apply(host.pin_owner_writes({8: 0, 9: 1}))
    mh.write(sim.A_POUT, (1 << 8) | (1 << 9))
    mh.ui = 1
    tc, trs = timing.Tracer(mh.chip, 0, 0), timing.Tracer(mh.chip, 1, 256)
    mh.start(3)
    for w in pf.lin_stage(0x21, bytes([1, 2, 3])):
        mh.push(1, w)
    for words in (pf.lin_command(0x10, data=bytes([9, 8, 7, 6, 5])), pf.lin_command(0x21, n=3),
                  pf.lin_command(0x05, n=2)):
        for w in words:
            mh.push(0, w)
        for _ in range(int(14e-3 * f_clk)):
            p = mh.chip.pin_out
            mh.ui = (p >> 8) & (p >> 9) & 1
            mh.step()
            mh.pop_all(0)
            mh.pop_all(1)
    sound("lin_commander", tc, dc)
    sound("lin_responder", trs, dr)
