"""The host tools pick the chip's geometry by one rule (pe.profile.resolve): an
explicit profile wins (but may not claim more cores or banks than the chip's
ID); otherwise, where a chip is present, its ID gives the core and bank counts;
otherwise (offline, no chip) TAPEOUT, with PKG3 selectable. Each changed
default runs at 2 and at 3 cores; a 3-core ID reaches core 2 through the
default bring-up path; unknown IDs are refused; the core-0 UART capture and
preloaded SPI plans keep every other core's enable, core 2's included.

Evidence level: model."""
import sys
import types

import pytest

from pe import asm, export, host, mdio, pps, rmii, sim, spipacked, uartcapture, uartcapture_peer
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
    assert profile.resolve(PKG3, chip_id=ID3) is PKG3
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
    assert (pe.ncores, pe.nbanks) == (3, 1)           # the unspecified core count is the ID's
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


# ---------------------------------------------------------------- explicit against the chip ID

BIGGER = [(PKG3, sim.ID_V2), (PKG3, ID2), (FULL, ID3), (Profile("3c3b", 3, 3), sim.id_value(3, 2)),
          (Profile("2c2b", 2, 2), sim.id_value(2, 1))]


@pytest.mark.parametrize("prof,chip_id", BIGGER)
def test_explicit_profile_bigger_than_the_chip_refused(prof, chip_id):
    p = asm.assemble(CORE2)
    for call in (lambda: profile.resolve(prof, chip_id=chip_id),
                 lambda: profile.validate_image(0, [0x08000000], 0, 0, prof, chip_id=chip_id),
                 lambda: host.load_program(0, p, profile=prof, chip_id=chip_id),
                 lambda: host.pin_owner_writes({}, profile=prof, chip_id=chip_id),
                 lambda: pps.load_writes(pps.assemble(), profile=prof, chip_id=chip_id),
                 lambda: uartcapture.capture_plan(3, profile=prof, chip_id=chip_id)):
        with pytest.raises(ValueError, match="more than chip ID"):
            call()


@pytest.mark.parametrize("prof,chip_id", [(TAPEOUT, ID3), (Profile("3c2b", 3, 2), ID3), (PKG3, ID3),
                                          (PKG3, sim.id_value(4, 4)), (TAPEOUT, sim.ID_V2)])
def test_explicit_profile_not_bigger_than_the_chip_allowed(prof, chip_id):
    assert profile.resolve(prof, chip_id=chip_id) is prof
    p = asm.assemble(CORE2)
    assert host.load_program(1, p, profile=prof, chip_id=chip_id) == host.load_program(1, p, profile=prof)


def test_driver_explicit_geometry_bigger_than_the_chip_refused():
    pe = protoemu.ProtoEmu(FakeTT(model(2)), ncores=3)
    for v in (sim.ID_V2, ID2):
        with pytest.raises(ValueError, match="exceeds chip ID"):
            pe.use_id(v)
        assert (pe.ncores, pe.nbanks) == (3, 3)
    pe = protoemu.ProtoEmu(FakeTT(model(3)), ncores=3, nbanks=3)
    with pytest.raises(ValueError, match="exceeds chip ID"):
        pe.use_id(sim.id_value(3, 2))                    # banks, not cores
    for nc, nb in ((2, 2), (3, 2), (3, 3), (1, 1)):     # smaller or equal: allowed, and kept
        pe = protoemu.ProtoEmu(FakeTT(model(3)), ncores=nc, nbanks=nb)
        pe.use_id(ID3)
        assert (pe.ncores, pe.nbanks) == (nc, nb)


def test_bringup_explicit_geometry_bigger_than_the_chip_refused(board):
    with pytest.raises(ValueError, match="exceeds chip ID"):
        board(model(2), ncores=3)
    assert (board(model(3), ncores=2).ncores) == 2


# ---------------------------------------------------------------- CTRL write-back keeps every core

@pytest.mark.parametrize("chip_id", TWO_CORE_IDS)
def test_core0_plans_two_core_id_unchanged(chip_id):
    for en in range(4):
        assert uartcapture.capture_plan(5, enabled_before=en, chip_id=chip_id) \
            == uartcapture.capture_plan(5, enabled_before=en)
        assert uartcapture_peer.capture_plan(5, enabled_before=en, chip_id=chip_id) \
            == uartcapture_peer.capture_plan(5, enabled_before=en)
        assert spipacked.preload_plan(b"abcde", enabled_before=en, chip_id=chip_id) \
            == spipacked.preload_plan(b"abcde", enabled_before=en)
    for en in (4, 5, 7):                                # a core-2 bit on a 2-core chip: refused, not dropped
        for call in (lambda: uartcapture.capture_plan(5, enabled_before=en, chip_id=chip_id),
                     lambda: uartcapture.capture_plan(5, enabled_before=en),
                     lambda: uartcapture_peer.capture_plan(5, enabled_before=en, chip_id=chip_id),
                     lambda: spipacked.preload_plan(b"x", enabled_before=en, chip_id=chip_id),
                     lambda: bringup.uart_capture_plan(5, en),
                     lambda: bringup.spi_preload_plan(b"x", en)):
            with pytest.raises(ValueError, match="2-core geometry"):
                call()


@pytest.mark.parametrize("en", range(8))
def test_core0_plans_three_core_keep_core_2(en):
    other = en & ~1
    w = uartcapture.capture_plan(5, enabled_before=en, chip_id=ID3)
    assert w[0] == (sim.A_CTRL, other) and w[-1] == (sim.A_CTRL, other | 1 | (1 << 8))
    assert w == uartcapture.capture_plan(5, enabled_before=en, profile=PKG3) == bringup.uart_capture_plan(5, en, 3)
    plan = spipacked.preload_plan(b"abcde", enabled_before=en, chip_id=ID3)
    assert plan["disable_writes"] == [(sim.A_CTRL, other)]
    assert plan["start_writes"] == [(sim.A_CTRL, other | 1 | (1 << 8))]
    got = bringup.spi_preload_plan(b"abcde", en, 3)
    assert got == {k: plan[k] for k in got}
    peer = en & ~2
    w = uartcapture_peer.capture_plan(5, enabled_before=en, chip_id=ID3)
    assert w[0] == (sim.A_CTRL, peer) and w[-1] == (sim.A_CTRL, peer | 2 | (2 << 8))
    with pytest.raises(ValueError, match="3-core geometry"):
        uartcapture.capture_plan(5, enabled_before=en | 8, chip_id=ID3)


def test_bringup_core0_capture_and_preloaded_spi_leave_core_2_running():
    """3-core chip, core 2 running a loop; then a UART capture and a preloaded
    SPI transfer on core 0 through bringup.py: core 2 stays enabled and keeps
    running in its bank."""
    from test_micropython_spi_uart import LONG, Wire, messages, spi_device, uart_source
    mh = model(3)
    wire = Wire(mh)
    pe = protoemu.ProtoEmu(FakeTT(mh), ncores=3)        # explicit, so the same driver call works before the change
    bringup.load(pe, dict(core=2, base=512, words=asm.assemble("l: jmp l").relocate(512)))
    assert mh.chip.enable & 4

    def core2_alive():
        c0 = mh.chip.cycle
        mh.step(20)
        return bool(mh.chip.enable & 4) and 512 <= mh.chip.cores[2].pc < 768 and mh.chip.cycle > c0
    arm = uart_source(wire, mh, b"\x39\xd3\x81")
    assert bringup.uart_capture(pe, 3, timeout_ms=LONG, after_arm=arm) == (b"\x39\xd3\x81", False)
    assert core2_alive(), "UART capture on core 0 stopped core 2"
    wire.devices.clear()
    msgs = messages([5])
    _, device = spi_device(0, msgs)
    wire.devices.append(device)
    bringup.spi_preloaded_setup(pe)
    assert bringup.spi_preloaded_transfer(pe, msgs[0][0], timeout_ms=LONG) == msgs[0][1]
    assert core2_alive(), "preloaded SPI on core 0 stopped core 2"


# ---------------------------------------------------------------- 089 findings: the chip bounds CTRL write-back

def start_core2_loop(mh):
    """Core 2 runs `l: jmp l` in its bank, loaded and started straight on the model."""
    mh.apply(host.load_program(2, asm.assemble("l: jmp l"), chip_id=mh.chip.read(sim.A_ID)))
    mh.start(1 << 2)
    mh.step(10)
    assert mh.chip.enable & 4


@pytest.mark.parametrize("how", ["use_id", "setup"])
def test_smaller_driver_geometry_keeps_core_2_through_capture_and_spi(board, how):
    """The verifier's reproduction (089 finding 1): a 2-core driver on a 3-core
    chip (allowed: it leaves core 2 alone) runs a UART capture and a preloaded
    SPI transfer on core 0 while core 2 runs; core 2 stays enabled."""
    from test_micropython_spi_uart import LONG, Wire, messages, spi_device, uart_source
    mh = model(3)
    wire = Wire(mh)
    if how == "use_id":
        pe = protoemu.ProtoEmu(FakeTT(mh), ncores=2)
        pe.use_id(pe.read(protoemu.A_ID))
    else:
        pe = board(mh, ncores=2)
    assert (pe.ncores, pe.nbanks) == (2, 2)            # allowed: the driver leaves core 2 alone
    start_core2_loop(mh)
    arm = uart_source(wire, mh, b"\x39\xd3\x81")
    assert bringup.uart_capture(pe, 3, timeout_ms=LONG, after_arm=arm) == (b"\x39\xd3\x81", False)
    mh.step(20)
    assert mh.chip.enable & 4, "UART capture through a 2-core driver stopped core 2"
    wire.devices.clear()
    msgs = messages([5])
    _, device = spi_device(0, msgs)
    wire.devices.append(device)
    bringup.spi_preloaded_setup(pe)
    assert bringup.spi_preloaded_transfer(pe, msgs[0][0], timeout_ms=LONG) == msgs[0][1]
    mh.step(20)
    assert mh.chip.enable & 4 and 512 <= mh.chip.cores[2].pc < 768, "preloaded SPI stopped core 2"


def test_driver_ctrl_mask_follows_the_chip():
    pe = protoemu.ProtoEmu(FakeTT(model(3)), ncores=2)
    assert (pe.chip_cores(), pe.ctrl_mask()) == (2, 3)          # no ID yet: the driver's count
    pe.use_id(ID3)
    assert (pe.chip_cores(), pe.ctrl_mask()) == (3, 7)
    pe = protoemu.ProtoEmu(FakeTT(model(2)))
    pe.use_id(sim.ID_V2)
    assert (pe.chip_cores(), pe.ctrl_mask()) == (2, 3)


def test_unspecified_count_comes_from_the_id(board):
    """089 minor: nbanks alone no longer pins ncores to 2."""
    for kw, chip_id, want in (({"nbanks": 2}, ID3, (3, 2)), ({"nbanks": 3}, ID3, (3, 3)),
                              ({"nbanks": 1}, sim.id_value(4, 4), (4, 1)),
                              ({"ncores": 2}, sim.id_value(3, 1), (2, 1)), ({"ncores": 2}, ID3, (2, 2)),
                              ({}, sim.id_value(3, 2), (3, 2))):
        pe = protoemu.ProtoEmu(FakeTT(model(2)), **kw)
        pe.use_id(chip_id)
        assert (pe.ncores, pe.nbanks) == want, (kw, hex(chip_id))
    pe = board(model(3), nbanks=2)
    assert (pe.ncores, pe.nbanks) == (3, 2)


def test_smaller_profile_keeps_core_2_enable_in_the_plans():
    """089 finding 1, pe side: a smaller profile (TAPEOUT) with the 3-core
    chip's ID accepts a CTRL mask with core 2 running and keeps its bit."""
    prof = TAPEOUT
    w = uartcapture.capture_plan(5, enabled_before=0b101, profile=prof, chip_id=ID3)
    assert w[0] == (sim.A_CTRL, 0b100) and w[-1] == (sim.A_CTRL, 0b100 | 1 | (1 << 8))
    plan = spipacked.preload_plan(b"abcde", enabled_before=0b101, profile=prof, chip_id=ID3)
    assert plan["disable_writes"] == [(sim.A_CTRL, 0b100)]
    assert plan["start_writes"] == [(sim.A_CTRL, 0b100 | 1 | (1 << 8))]
    w = uartcapture_peer.capture_plan(5, enabled_before=0b101, profile=prof, chip_id=ID3)
    assert w[0] == (sim.A_CTRL, 0b101) and w[-1] == (sim.A_CTRL, 0b101 | 2 | (2 << 8))
    with pytest.raises(ValueError, match="3-core geometry"):
        uartcapture.capture_plan(5, enabled_before=0b1001, profile=prof, chip_id=ID3)
    with pytest.raises(ValueError, match="2-core geometry"):            # no ID: the profile's bound
        uartcapture.capture_plan(5, enabled_before=0b101, profile=TAPEOUT)


@pytest.mark.parametrize("prof", [Profile("tiny", 2, 2, bank_words=128, fifo_depth=4),
                                  Profile("d4", 2, 2, fifo_depth=4), Profile("b128", 2, 2, bank_words=128)])
def test_explicit_profile_of_another_bank_size_or_depth_refused_with_an_id(prof):
    """089 minor: an ID build has 256-word banks and depth 8."""
    for chip_id in (ID3, sim.ID_V2, sim.id_value(4, 4)):
        with pytest.raises(ValueError, match="-word banks and depth"):
            profile.resolve(prof, chip_id=chip_id)
        with pytest.raises(ValueError, match="-word banks and depth"):
            host.load_program(0, asm.assemble(CORE2), profile=prof, chip_id=chip_id)
    assert profile.resolve(prof) is prof                                # offline: still allowed


def test_kv260_spi_uart_demo_masks_ctrl_by_the_chip(monkeypatch):
    """fpga/kv260/host/spi_uart_demo.chip(): the ID without a bus transaction
    (the board's from v2_demo.Hw's geometry, the model's from its chip) and
    the enable bits of every core it has. Host-side Python only."""
    monkeypatch.syspath_prepend(str(rmii.ROOT / "fpga/kv260/host"))
    import spi_uart_demo
    from postfreeze_demo import ModelRig
    for v, mask in ((sim.ID_V2, 3), (ID2, 3), (ID3, 7)):
        rig = types.SimpleNamespace(model=False, hw=types.SimpleNamespace(geom=types.SimpleNamespace(id=v)))
        assert spi_uart_demo.chip(rig) == (v, mask)
    rig = ModelRig()
    assert spi_uart_demo.chip(rig) == (rig.mh.chip.read(sim.A_ID), (1 << rig.mh.chip.ncores) - 1)
