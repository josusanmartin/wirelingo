"""Regressions for corruption across software/hardware configuration boundaries."""
import pytest
import sys
import time
from types import SimpleNamespace

from pe import asm, host, isa
from pe.profile import FULL, Profile
from pe.modelhost import ModelHost
from test_micropython_driver import FakeTT, protoemu, bringup


@pytest.mark.parametrize("core,source,base", [
    (0, "nop\n" * 257, 0), (1, "nop", 0), (0, "nop", -1),
    (0, "nop\nnop", 255), (2, "nop", 512), (0, ".word 256", 0),
    (0, ".wrap_target\nnop\n.wrap\nnop", 256),
    (0, f".word {isa.enc(isa.OP_CALL, 0, 256)}", 0),
])
def test_host_rejects_cross_bank_loads(core, source, base):
    p = asm.assemble(source)
    with pytest.raises(ValueError):
        host.load_program(core, p, base)


def test_profile_and_branch_relocation_boundaries():
    p = asm.assemble("entry: nop\njmp entry")
    assert host.load_program(3, p, profile=FULL)[0][0] == 768
    assert host.load_program(1, p, profile=Profile("small", 2, 2, 16))[0][0] == 256
    with pytest.raises(ValueError, match="address space"):
        asm.assemble("jmp 1023").relocate(1)
    with pytest.raises(ValueError):
        host.load_program(0, asm.assemble("nop\n" * 17), profile=Profile("small", 2, 2, 16))
    p.wrap_top = 2
    with pytest.raises(ValueError, match="wrap"):
        host.load_program(0, p)
    with pytest.raises(ValueError, match="empty"):
        host.load_program(0, asm.Program(wrap_top=0))


def test_board_load_validates_before_any_bus_write():
    pe = protoemu.ProtoEmu(FakeTT(ModelHost()))
    writes = []
    pe.write = lambda *a, **k: writes.append((a, k))
    nop = isa.enc(isa.OP_MOV, 0, 0x41000)
    for args in [(0, [nop] * 257, 0), (1, [nop], 0), (2, [nop], 512),
                 (0, [], 0), (0, [nop], 0, 1 << 16), (0, [256], 0),
                 (0, [isa.enc(isa.OP_CALL, 0, 256)], 0), (0, [-1], 0)]:
        core, words, base, *wrap = args
        with pytest.raises(ValueError):
            pe.load(core, iter(words), base, wrap=wrap[0] if wrap else None)
        assert not writes
    pe.load(1, iter([nop, nop]), 256)
    assert writes[0][0] == (256, [nop, nop])
    assert writes[1][0][1][-1] == 256 | (257 << 16)


def test_pin_ownership_survives_repeated_and_mixed_assignments():
    mh = ModelHost()
    pe = protoemu.ProtoEmu(FakeTT(mh))
    pe.own_pins(1, [0, 17])
    pe.own_pins(1, [1, 31])
    pe.own_pins(0, [1])
    expected = {0: 1, 17: 1, 31: 1}
    lo, hi = [w for _, w in host.pin_owner_writes(expected)]
    assert mh.chip.pin_owner == [expected.get(p, 0) for p in range(32)]
    before = list(mh.chip.pin_owner)
    for c, ps in [(2, [0]), (1, [2, -1]), (1, [32])]:
        with pytest.raises(ValueError):
            pe.own_pins(c, ps)
        assert mh.chip.pin_owner == before
    pe.set_pin_owners({1: 1})
    assert mh.chip.pin_owner == [0, 1] + [0] * 30


@pytest.mark.parametrize("actual", [48_000_000, 47_999_000])
def test_bringup_checks_generated_clock_before_releasing_reset(monkeypatch, actual):
    resets, clocks, constructed = [], [], []
    tt = SimpleNamespace(shuttle=SimpleNamespace(**{bringup.PROJECT: SimpleNamespace(enable=lambda: None)}),
                         uio_oe_pico=SimpleNamespace(value=255), auto_clocking_freq=actual,
                         reset_project=resets.append, clock_project_PWM=clocks.append)
    monkeypatch.setitem(sys.modules, "ttboard.demoboard", SimpleNamespace(DemoBoard=SimpleNamespace(get=lambda: tt)))
    monkeypatch.setitem(sys.modules, "ttboard.mode", SimpleNamespace(RPMode=SimpleNamespace(ASIC_RP_CONTROL=1)))
    monkeypatch.setattr(time, "sleep_ms", lambda _: None, raising=False)
    pe = SimpleNamespace(read=lambda _: bringup.ID)
    monkeypatch.setattr(bringup, "ProtoEmu", lambda board: constructed.append(board) or pe)
    if actual == 48_000_000:
        assert bringup.setup() is pe
        assert resets == [True, False] and constructed == [tt]
    else:
        with pytest.raises(RuntimeError, match="project clock"):
            bringup.setup()
        assert resets == [True] and not constructed
    assert clocks == [48_000_000] and tt.uio_oe_pico.value == 0
