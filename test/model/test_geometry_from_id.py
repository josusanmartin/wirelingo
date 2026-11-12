"""The host tools pick the chip's geometry by one rule (pe.profile.resolve): an
explicit profile wins; otherwise, where a chip is present, its ID gives the core
and bank counts; otherwise (offline, no chip) TAPEOUT, with PKG3 selectable.
Each changed default runs at 2 and at 3 cores; a 3-core ID reaches core 2
through the default bring-up path; unknown IDs are refused.

Evidence level: model."""
import sys
import types

import pytest

from pe import asm, export, host, mdio, pps, rmii, sim
from pe import usb_uart_batch4, usb_uart_envelope3, usb_uart_overlap4
from pe import profile
from pe.modelhost import ModelHost
from pe.profile import FULL, PKG3, TAPEOUT, Profile
from test_micropython_driver import FakeTT, bringup, protoemu

ID2 = sim.id_value(2, 2)               # 0x50452203
ID3 = sim.id_value(3, 3)               # 0x50453303
TWO_CORE_IDS = (sim.ID_V2, ID2)
UNKNOWN_IDS = (0x50450003, 0x50451003, 0x50454303, 0x50455503, 0x50453304, 0x50450102,
               0x50450001, 0x51453303, 0, 0xFFFFFFFF, -1)
CORE2 = "ldi r0, 42\nmov isr, r0\npush block\nhalt"     # core 2 pushes 42 and halts


def core2_runs(mh, writes):
    """Apply load writes for core 2 to a model and run core 2: it pushes 42."""
    mh.apply(writes)
    mh.start(1 << 2)
    mh.step(50)
    return mh.pop_all(2) == [42] and bool(mh.chip.read(sim.core_reg(2, sim.C_STATUS)) & protoemu.ST_HALTED)


def model(ncores):
    return ModelHost(ncores=ncores, nbanks=ncores)


# ---------------------------------------------------------------- pe.profile

@pytest.mark.parametrize("chip_id", TWO_CORE_IDS)
def test_for_id_two_core_ids_give_tapeout(chip_id):
    assert profile.for_id(chip_id) is TAPEOUT


def test_for_id_three_and_four_core_ids():
    assert profile.for_id(ID3) is PKG3
    assert profile.for_id(sim.id_value(4, 4)) is FULL
    p = profile.for_id(sim.id_value(3, 2))
    assert (p.ncores, p.nbanks, p.bank_words, p.fifo_depth) == (3, 2, 256, 8)


@pytest.mark.parametrize("chip_id", UNKNOWN_IDS + (True, None, "0x50453303", 3.0))
def test_for_id_refuses_unknown_ids(chip_id):
    with pytest.raises(ValueError, match="not a protoemu ID"):
        profile.for_id(chip_id)


def test_for_id_agrees_with_known_id_and_the_board_driver():
    """Every ID with the 'PE' prefix: for_id accepts exactly sim.known_id's, and
    the MicroPython driver's id_geometry decodes the same counts."""
    for low in range(1 << 16):
        v = 0x50450000 | low
        ok = sim.known_id(v)
        try:
            p = profile.for_id(v)
        except ValueError:
            assert not ok, hex(v)
            with pytest.raises(ValueError):
                protoemu.id_geometry(v)
            continue
        assert ok, hex(v)
        assert protoemu.id_geometry(v) == (p.ncores, p.nbanks), hex(v)


def test_resolve_explicit_wins_then_chip_id_then_tapeout():
    assert profile.resolve() is TAPEOUT is profile.OFFLINE_DEFAULT
    assert profile.resolve(chip_id=ID3) is PKG3
    assert profile.resolve(chip_id=sim.ID_V2) is TAPEOUT
    assert profile.resolve(TAPEOUT, chip_id=ID3) is TAPEOUT          # explicit wins
    assert profile.resolve(PKG3, chip_id=ID2) is PKG3
    with pytest.raises(ValueError):
        profile.resolve(chip_id=0x50450003)
    assert profile.PROFILES == {"tapeout": TAPEOUT, "pkg3": PKG3, "full": FULL}


# ---------------------------------------------------------------- validate_image

@pytest.mark.parametrize("chip_id", TWO_CORE_IDS)
def test_validate_image_two_core_id(chip_id):
    nop = 0x08000000
    profile.validate_image(1, [nop], 256, 256 | (256 << 16), chip_id=chip_id)
    for kw in ({}, {"chip_id": chip_id}):
        with pytest.raises(ValueError, match="no instruction bank"):
            profile.validate_image(2, [nop], 512, 512 | (512 << 16), **kw)


def test_validate_image_three_core_id():
    nop = 0x08000000
    profile.validate_image(2, [nop], 512, 512 | (512 << 16), chip_id=ID3)
    with pytest.raises(ValueError, match="no instruction bank"):
        profile.validate_image(3, [nop], 768, 768 | (768 << 16), chip_id=ID3)
    with pytest.raises(ValueError, match="no instruction bank"):     # explicit wins
        profile.validate_image(2, [nop], 512, 512 | (512 << 16), TAPEOUT, chip_id=ID3)
    with pytest.raises(ValueError, match="not a protoemu ID"):
        profile.validate_image(0, [nop], 0, 0, chip_id=0x50450003)


# ---------------------------------------------------------------- host.load_program / pin_owner_writes

@pytest.mark.parametrize("chip_id", TWO_CORE_IDS)
def test_load_program_two_core_id(chip_id):
    p = asm.assemble(CORE2)
    for core in (0, 1):
        assert host.load_program(core, p, chip_id=chip_id) == host.load_program(core, p) \
            == host.load_program(core, p, profile=TAPEOUT)
    with pytest.raises(ValueError, match="no instruction bank"):
        host.load_program(2, p, chip_id=chip_id)
    with pytest.raises(ValueError, match="no instruction bank"):
        host.load_program(2, p)                                          # offline default: TAPEOUT


def test_load_program_three_core_id_runs_core_2():
    p = asm.assemble(CORE2)
    mh = model(3)
    w = host.load_program(2, p, chip_id=mh.chip.read(sim.A_ID))
    assert w[0][0] == 512 and w == host.load_program(2, p, profile=PKG3)
    assert core2_runs(mh, w)
    with pytest.raises(ValueError, match="no instruction bank"):
        host.load_program(2, p, profile=TAPEOUT, chip_id=ID3)           # explicit wins
    with pytest.raises(ValueError, match="no instruction bank"):
        host.load_program(3, p, chip_id=ID3)


@pytest.mark.parametrize("chip_id", UNKNOWN_IDS)
def test_load_program_and_owners_refuse_unknown_ids(chip_id):
    p = asm.assemble(CORE2)
    with pytest.raises(ValueError, match="not a protoemu ID"):
        host.load_program(0, p, chip_id=chip_id)
    with pytest.raises(ValueError, match="not a protoemu ID"):
        host.pin_owner_writes({3: 1}, chip_id=chip_id)


@pytest.mark.parametrize("chip_id", TWO_CORE_IDS)
def test_pin_owner_writes_two_core_id(chip_id):
    owners = {0: 1, 17: 1}
    assert host.pin_owner_writes(owners, chip_id=chip_id) == host.pin_owner_writes(owners)
    for kw in ({}, {"chip_id": chip_id}):
        with pytest.raises(ValueError, match="does not exist"):
            host.pin_owner_writes({3: 2}, **kw)


def test_pin_owner_writes_three_core_id():
    w = host.pin_owner_writes({3: 2, 20: 2}, chip_id=ID3)
    assert w == [(sim.A_POWN_LO, 2 << 6), (sim.A_POWN_HI, 2 << 8)]
    with pytest.raises(ValueError, match="does not exist"):
        host.pin_owner_writes({3: 3}, chip_id=ID3)


# ---------------------------------------------------------------- pps.load_writes

@pytest.mark.parametrize("chip_id", TWO_CORE_IDS)
def test_pps_two_core_id(chip_id):
    prog = pps.assemble()
    assert pps.load_writes(prog, chip_id=chip_id) == pps.load_writes(prog)
    with pytest.raises(ValueError, match="no instruction bank"):
        pps.load_writes(prog, core=2, chip_id=chip_id)


def test_pps_three_core_id_loads_core_2():
    prog = pps.assemble()
    mh = model(3)
    w = pps.load_writes(prog, core=2, chip_id=mh.chip.read(sim.A_ID))
    assert w == pps.load_writes(prog, core=2, profile=PKG3)
    mh.apply(w)                                                          # ends with core 2's start
    mh.step(20)
    assert mh.chip.enable == 1 << 2 and 512 <= mh.chip.cores[2].pc < 768
    with pytest.raises(ValueError):
        pps.load_writes(prog, core=2, profile=TAPEOUT, chip_id=ID3)     # explicit wins


# ---------------------------------------------------------------- pe.export (offline: --profile)

def run_export(monkeypatch, capsys, *args):
    monkeypatch.setattr(sys, "argv", ["pe.export", str(rmii.ROOT / "firmware" / "uart_tx.pasm"),
                                      "--define", "TX=8", "--out-pins", "8,1", "--side-pins", "8", *args])
    export.main()
    return capsys.readouterr().out


def test_export_two_core_default_unchanged(monkeypatch, capsys):
    for core in ("0", "1"):
        out = run_export(monkeypatch, capsys, "--core", core)
        assert out == run_export(monkeypatch, capsys, "--core", core, "--profile", "tapeout")
        assert "profile fixed-bank-v1-2c2b8f" in out
    with pytest.raises(ValueError, match="no instruction bank"):
        run_export(monkeypatch, capsys, "--core", "2")


def test_export_pkg3_reaches_core_2(monkeypatch, capsys):
    out = run_export(monkeypatch, capsys, "--core", "2", "--profile", "pkg3")
    assert out.startswith("# uart_tx: 4 words at 512, profile fixed-bank-v1-3c3b8f\n")
    mh = model(3)
    pe = protoemu.ProtoEmu(FakeTT(mh))
    pe.use_id(pe.read(protoemu.A_ID))
    exec(out, {"pe": pe})                                                # the snippet loads core 2
    assert mh.chip.imem[2 * 256: 2 * 256 + 4] != [0] * 4
    two = run_export(monkeypatch, capsys, "--core", "0")
    three = run_export(monkeypatch, capsys, "--core", "0", "--profile", "pkg3")
    assert two.replace("2c2b8f", "3c3b8f") == three                      # only the profile line differs
    with pytest.raises(ValueError, match="no instruction bank"):
        run_export(monkeypatch, capsys, "--core", "3", "--profile", "pkg3")


# ---------------------------------------------------------------- rmii.Bench (a chip: its ID)

def test_rmii_bench_two_core_default():
    b = rmii.Bench()
    assert (b.chip.ncores, b.chip.nbanks) == (2, 2) and b.profile is TAPEOUT
    with pytest.raises(ValueError, match="no instruction bank"):
        b.load_rx(core=2)


def test_rmii_bench_three_core_receives_on_core_2():
    b = rmii.Bench(profile=PKG3)
    assert (b.chip.ncores, b.chip.nbanks) == (3, 3) and b.profile is PKG3
    b.load_rx(core=2)                                    # load_rx sets SYNC_BYPASS too
    b.apply(host.pin_owner_writes({31: 2}, chip_id=b.chip.read(sim.A_ID)))   # rmii_rx drives TAG (V7)
    b.start(1 << 2)
    b.phy_rx.idle(40)
    f = rmii.make_frame(64, 3)
    b.phy_rx.frame(f, lead=4)
    b.port.script = rmii.drain(b.port, core=2)
    b.run_all(400)
    recs, _ = rmii.parse_rx(b.words())
    assert [r.data for r in recs] == [f] and all(r.good for r in recs)


# ---------------------------------------------------------------- mdio (core argument passes the rule on)

def test_mdio_two_and_three_core():
    m = asm.assemble((rmii.ROOT / "firmware" / "mdio_master.pasm").read_text(), mdio.master_defines())
    ph = asm.assemble((rmii.ROOT / "firmware" / "mdio_phy.pasm").read_text(), mdio.phy_defines(base=512))
    assert mdio.master_load(1, m, chip_id=ID2) == mdio.master_load(1, m)
    with pytest.raises(ValueError):
        mdio.master_load(2, m)
    with pytest.raises(ValueError):
        mdio.phy_setup(2, ph, chip_id=sim.ID_V2)
    assert mdio.master_load(2, m, chip_id=ID3)[0][0] == 512
    assert mdio.phy_setup(2, ph, chip_id=ID3) == mdio.phy_setup(2, ph, profile=PKG3)


# ---------------------------------------------------------------- the USB UART pairs: at least 2 cores

@pytest.mark.parametrize("mod", [usb_uart_batch4, usb_uart_envelope3, usb_uart_overlap4])
def test_usb_uart_pairs_accept_three_cores_refuse_one(monkeypatch, mod):
    two = mod.load_writes()
    monkeypatch.setattr(mod, "TAPEOUT", PKG3)
    assert mod.load_writes() == two                  # cores 0 and 1 only: the same writes on 3 cores
    monkeypatch.setattr(mod, "TAPEOUT", Profile("one", 1, 1))
    with pytest.raises(ValueError, match="at least 2 cores"):
        mod.load_writes()


# ---------------------------------------------------------------- the MicroPython driver and bring-up

def test_driver_use_id_two_and_three_cores():
    pe = protoemu.ProtoEmu(FakeTT(model(2)))
    assert (pe.ncores, pe.nbanks) == (2, 2)           # before any ID: the tapeout's
    assert pe.read_id() == ID2 and (pe.ncores, pe.nbanks) == (2, 2)
    pe.use_id(sim.ID_V2)
    assert (pe.ncores, pe.nbanks) == (2, 2)
    mh = model(3)
    pe = protoemu.ProtoEmu(FakeTT(mh))
    assert pe.read_id() == ID3 and (pe.ncores, pe.nbanks) == (3, 3)
    pe.own_pins(2, [12])
    pe.load(core=2, base=512, words=asm.assemble(CORE2).relocate(512))
    pe.start(1 << 2)
    mh.step(50)
    assert pe.pop_all(2) == [42]


def test_driver_explicit_geometry_wins_and_unknown_ids_refused():
    pe = protoemu.ProtoEmu(FakeTT(model(3)), ncores=2)
    pe.use_id(ID3)
    assert (pe.ncores, pe.nbanks) == (2, 2)           # explicit wins
    pe = protoemu.ProtoEmu(FakeTT(model(3)), nbanks=1)
    pe.use_id(ID3)
    assert (pe.ncores, pe.nbanks) == (2, 1)
    pe = protoemu.ProtoEmu(FakeTT(model(2)))
    for v in UNKNOWN_IDS:
        with pytest.raises(ValueError, match="not a protoemu ID"):
            pe.use_id(v)
        assert (pe.ncores, pe.nbanks) == (2, 2)
    pe = protoemu.ProtoEmu(FakeTT(model(2)), owners={5: 1})
    with pytest.raises(ValueError, match="pin owner"):
        pe.use_id(sim.id_value(1, 1))                 # the owner map names core 1: refused, unchanged
    assert (pe.ncores, pe.nbanks) == (2, 2)


class _Board(FakeTT):
    """FakeTT plus the TT SDK calls bringup.setup() makes."""

    def __init__(self, mh):
        super().__init__(mh)
        self.shuttle = types.SimpleNamespace(**{bringup.PROJECT: types.SimpleNamespace(enable=lambda: None)})
        self.uio_oe_pico = types.SimpleNamespace(value=None)
        self.mode = None
        self.auto_clocking_freq = None

    def reset_project(self, on):
        pass

    def clock_project_PWM(self, hz):
        self.auto_clocking_freq = hz


@pytest.fixture
def board(monkeypatch):
    """Install a fake ttboard SDK; returns a function mh -> the setup() result."""
    import time
    holder = {}
    demo = types.ModuleType("ttboard.demoboard")
    demo.DemoBoard = types.SimpleNamespace(get=lambda: holder["tt"])
    mode = types.ModuleType("ttboard.mode")
    mode.RPMode = types.SimpleNamespace(ASIC_RP_CONTROL="asic")
    monkeypatch.setitem(sys.modules, "ttboard", types.ModuleType("ttboard"))
    monkeypatch.setitem(sys.modules, "ttboard.demoboard", demo)
    monkeypatch.setitem(sys.modules, "ttboard.mode", mode)
    monkeypatch.setattr(time, "sleep_ms", lambda ms: None, raising=False)

    def setup(mh, **kw):
        holder["tt"] = _Board(mh)
        return bringup.setup(**kw)
    return setup


def test_bringup_two_core_chip(board):
    pe = board(model(2))
    assert (pe.ncores, pe.nbanks, pe.bank_words, pe.depth) == (2, 2, 256, 8)
    with pytest.raises(ValueError, match="no instruction bank"):
        pe.load(core=2, base=512, words=[0x08000000])


def test_bringup_three_core_id_reaches_core_2(board):
    """The default path: setup() reads 0x50453303 and core 2 loads and runs."""
    mh = model(3)
    pe = board(mh)
    assert (pe.ncores, pe.nbanks) == (3, 3)
    p = asm.assemble(CORE2)
    words = p.relocate(512)
    bringup.load(pe, dict(core=2, base=512, words=words))
    mh.step(50)
    assert pe.pop_all(2) == [42]
    assert pe.status(2) & protoemu.ST_HALTED
    pe.own_pins(2, [9])                                # core 2 can own pins
    assert mh.chip.pin_owner[9] == 2


def test_bringup_explicit_geometry_wins(board):
    pe = board(model(3), ncores=2, nbanks=2)
    assert (pe.ncores, pe.nbanks) == (2, 2)


def test_bringup_usb_uart_on_three_cores():
    """bringup.usb_uart's pairs use cores 0 and 1: a 3-core driver is accepted
    (at least 2 cores and banks), a 1-core one refused."""
    mh = model(3)
    pe = protoemu.ProtoEmu(FakeTT(mh), ncores=3)
    got = bringup.usb_uart(pe, "byte")
    assert got["mode"] == "byte" and mh.chip.enable & 3 == 3
    with pytest.raises(ValueError, match="at least 2 cores"):
        bringup.usb_uart(protoemu.ProtoEmu(FakeTT(model(1)), ncores=1), "byte")


@pytest.mark.parametrize("bad", [0x50450003, 0x50454303, 0x50453304, 0, 0xFFFFFFFF])
def test_bringup_refuses_unknown_id(board, bad):
    """A regression guard: setup() refused unknown IDs before this change too."""
    mh = model(2)
    real = mh.chip.read
    mh.chip.read = lambda addr: bad if addr == sim.A_ID else real(addr)
    with pytest.raises(RuntimeError, match="wrong project"):
        board(mh)
