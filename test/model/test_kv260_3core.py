"""The KV260 tools for the v0.3 builds (backlog AR): the chip IDs and their
geometry, bringup_kria.py on the cycle model at 3 and 2 cores (the core ring,
the 32-bit CRC with POLY_HI), the install-time build check, build.sh's
arguments and staging (fake ssh/rsync, no Vivado), and board_smoke.py's
command sequence against a stand-in Kria. Nothing here touches a board."""
import json
import os
import pathlib
import subprocess
import sys
import types
import zlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
HOST = ROOT / "fpga/kv260/host"
sys.path.insert(0, str(HOST))
sys.path.insert(0, str(ROOT / "fpga/kv260"))
import app_build  # noqa: E402
import board_smoke  # noqa: E402
import bringup_kria  # noqa: E402
import fabric_guard  # noqa: E402
import pe_kria  # noqa: E402
import pmod_state  # noqa: E402
import spim_test  # noqa: E402
from pe import sim  # noqa: E402

ID3, ID2 = 0x50453303, 0x50452203


# ---------------------------------------------------------------- IDs

def test_the_three_kv260_ids_and_their_geometry():
    assert pe_kria.geometry(sim.ID_V2) == (sim.ID_V2, "v0.2", 2, 2)
    assert pe_kria.geometry(ID2) == (ID2, "v0.3", 2, 2) and ID2 == sim.id_value(2, 2)
    assert pe_kria.geometry(ID3) == (ID3, "v0.3", 3, 3) and ID3 == sim.id_value(3, 3)
    assert str(pe_kria.geometry(ID3)) == "0x50453303: v0.3, 3 cores, 3 banks"
    assert pe_kria.geometry(ID3, cores=3).ncores == 3 and pe_kria.geometry(sim.ID_V2, cores=2).ncores == 2


@pytest.mark.parametrize("v", [0, 0xFFFFFFFF, 0x50450003, 0x50454403, 0x50452303, 0x50451103, 0x50453203,
                               0x50450102, 0x50453304, 0x4C414231])
def test_any_other_id_is_refused(v):
    with pytest.raises(ValueError, match="not a KV260 build"):
        pe_kria.geometry(v)


def test_another_core_count_than_expected_is_refused():
    with pytest.raises(ValueError, match="expected a 2-core build"):
        pe_kria.geometry(ID3, cores=2)
    with pytest.raises(ValueError, match="expected a 3-core build"):
        pe_kria.geometry(sim.ID_V2, cores=3)


def test_no_board_tool_asserts_the_v02_id_alone():
    for p in list(HOST.glob("*.py")) + [ROOT / "fpga/kv260/sim/test_kv260.py"]:
        text = p.read_text()
        assert "== 0x50450002" not in text and "== ID\n" not in text and "ID = 0x50450002" not in text, p.name


def test_app_cores_follows_the_active_app(monkeypatch):
    monkeypatch.setattr(fabric_guard, "require_app", lambda: "protoemu3c")
    assert pe_kria.app_cores() == ("protoemu3c", 3) == pe_kria.app_cores(3)
    with pytest.raises(ValueError, match="holds a 3-core build"):
        pe_kria.app_cores(2)
    monkeypatch.setattr(fabric_guard, "require_app", lambda: "protoemu")
    assert pe_kria.app_cores() == ("protoemu", 2)


# ---------------------------------------------------------------- bringup_kria.py on the model

def model_ctx(regs, cores=None):
    ctx = bringup_kria.Ctx()
    ctx.board = pe_kria.Board(regs)
    ctx.pe = ctx.board.protoemu()
    ctx.f_nominal = ctx.f_clk = 50e6
    ctx.cores = cores
    return ctx


def args(**kw):
    d = dict(model=False, cores=None, settle=1, mhz=50.0, expect=None, allow_dirty=False, only=None)
    d.update(kw)
    return types.SimpleNamespace(**d)


@pytest.mark.parametrize("ncores", [3, 2])
def test_bringup_steps_on_the_model(ncores, capsys):
    ctx = model_ctx(bringup_kria.ModelRegs(ncores), ncores)
    bringup_kria.step_id(ctx)
    assert ctx.geom == pe_kria.geometry(sim.id_value(ncores, ncores))
    assert ctx.pe.ncores == ncores                    # the driver follows the ID
    bringup_kria.step_regs(ctx, n=2)
    bringup_kria.step_pins(ctx)
    bringup_kria.step_loopback(ctx, msg=b"ring")
    bringup_kria.step_crc32(ctx, nwords=10)
    out = capsys.readouterr().out
    assert f"v0.3, {ncores} cores, {ncores} banks" in out and f"all {ncores} cores" in out
    pairs = ["core 0 -> core 1", "core 1 -> core 2", "core 2 -> core 0"] if ncores == 3 else ["core 0 -> core 1"]
    assert [ln.strip().split(":")[0] for ln in out.splitlines() if "-> core" in ln] == pairs
    assert out.count("CRC 0x") == ncores


def test_run_on_the_model_reports_each_step(capsys):
    assert bringup_kria.run(args(model=True, cores=3, only=["id", "crc32"])) == 0
    out = capsys.readouterr().out
    assert "ID (0x800) = 0x50453303" in out and out.count("PASS") >= 2 and "ALL PASS" in out


def test_an_unknown_id_fails_every_step_that_needs_the_chip(capsys):
    regs = bringup_kria.ModelRegs(3)
    read = regs.mh.chip.read                               # the ID register of a 4-core, 4-bank chip
    regs.mh.chip.read = lambda a: sim.id_value(4, 4) if a == sim.A_ID else read(a)
    assert hex(regs.mh.chip.read(sim.A_ID)) == "0x50454403"
    assert bringup_kria.run(args(only=["id", "regs", "crc32"]), regs=regs) == 1
    out = capsys.readouterr().out
    assert out.count("not a KV260 build") == 3 and "3 step(s) FAILED" in out


def test_a_chip_with_another_core_count_than_the_app_fails(capsys):
    assert bringup_kria.run(args(cores=2, only=["id"]), regs=bringup_kria.ModelRegs(3)) == 1
    assert "expected a 2-core build" in capsys.readouterr().out


def v02_regs():
    regs = bringup_kria.ModelRegs(2)
    read = regs.mh.chip.read
    regs.mh.chip.read = lambda a: sim.ID_V2 if a == sim.A_ID else read(a)
    return regs


def test_a_v02_bitstream_is_accepted_and_skips_the_crc32_step(capsys):
    assert bringup_kria.run(args(cores=2, only=["id", "loopback", "crc32"]), regs=v02_regs()) == 0
    out = capsys.readouterr().out
    assert "v0.2, 2 cores, 2 banks" in out and "SKIP: a v0.2 bitstream" in out and "ALL PASS" in out


def test_the_crc32_step_needs_poly_hi(capsys):
    """POLY_HI not reaching the core (as a gated register the FPGA profile didn't
    convert would be): the CRC differs and the step fails."""
    regs = bringup_kria.ModelRegs(3)
    write = regs.mh.write
    poly_hi = {sim.core_reg(c, sim.C_POLY_HI) for c in range(3)}
    regs.mh.write = lambda addr, data: None if addr in poly_hi else write(addr, data)
    ctx = model_ctx(regs, 3)
    with pytest.raises(AssertionError, match="core 0 CRC"):
        bringup_kria.step_crc32(ctx, nwords=4)


def test_the_crc32_reference_is_zlibs():
    import random
    rng = random.Random(9)
    for n in (1, 2, 7):
        words = [rng.getrandbits(32) for _ in range(n)]
        want = bringup_kria.crc32_msb_first(words)
        assert zlib.crc32(bringup_kria.zlib_view(words)) == bringup_kria.rev32(want) ^ 0xFFFFFFFF
    # docs/isa.md §4.5: over a frame and its FCS (the complement, MSB first) the register is 0xC704DD7B
    words = [rng.getrandbits(32) for _ in range(5)]
    fcs = bringup_kria.crc32_msb_first(words) ^ 0xFFFFFFFF
    assert bringup_kria.crc32_msb_first(words + [fcs]) == 0xC704DD7B


class FakeLab:
    ident = dict(rtl_rev="5ff5e083d352126e279928010908b3b353bc2250", head="d" * 40, dirty=False, clk_div=24,
                 build_time=0, build_utc="2026-11-10 20:00:00", digest="a3b41f96")

    def identity(self):
        return dict(self.ident)


def test_the_expect_revision_check_still_works(monkeypatch):
    import pe_lab
    monkeypatch.setattr(pe_lab, "Lab", FakeLab)
    ctx = types.SimpleNamespace(expect="5ff5e08", allow_dirty=False)
    bringup_kria.step_build(ctx)
    for bad in ("d973462", "5ff5e0"):                      # another revision; too short a prefix
        ctx.expect = bad
        with pytest.raises(AssertionError):
            bringup_kria.step_build(ctx)
    ctx.expect = "5ff5e08"
    monkeypatch.setattr(FakeLab, "ident", dict(FakeLab.ident, dirty=True))
    with pytest.raises(AssertionError, match="uncommitted"):
        bringup_kria.step_build(ctx)
    ctx.allow_dirty = True
    bringup_kria.step_build(ctx)


# ---------------------------------------------------------------- spim_test.py's geometry

class LoopSpim:
    """pe_spim.Spim stand-in: the 'fabric' path is the same model over the GPIO port."""

    def __init__(self, board):
        self.pe, self.sel = board.protoemu(ncores=3, nbanks=3), 0
        self.mem = types.SimpleNamespace(rd=lambda off: self.sel, wr=lambda off, v: setattr(self, "sel", v))

    def select(self, fabric, alt=False):
        self.sel = int(fabric) | int(alt) << 1

    def write(self, addr, words, fixed=False):
        return self.pe.write(addr, words, fixed)

    def read(self, addr, n=1, fixed=False):
        return self.pe.read(addr, n, fixed)


def test_spim_test_uses_every_core_of_the_build():
    board = pe_kria.Board(bringup_kria.ModelRegs(3))
    ctx = spim_test.Ctx(board, LoopSpim(board), 50e6, log=lambda m: None, cores=3)
    assert ctx.geom.id == ID3 and ctx.gpio.ncores == ctx.fab.ncores == 3
    assert spim_test.t_regs(ctx, 2).startswith("2 rounds R0..R3 x3 cores")
    with pytest.raises(ValueError, match="expected a 2-core build"):
        spim_test.Ctx(board, LoopSpim(board), 50e6, cores=2)


# ---------------------------------------------------------------- install.sh's build check

INFO = {"rtl_rev": "a" * 40, "head": "b" * 40, "dirty": False}


@pytest.mark.parametrize("app,built", [("protoemu3c", dict(INFO, cores=3, clk_div=24)),
                                       ("protoemu", dict(INFO, cores=2, clk_div=24)),
                                       ("protoemu48", dict(INFO, cores=2, clk_div=25)),
                                       ("protoemu", dict(INFO))])          # a record from before 2026-11-10
def test_a_build_installs_as_its_app(app, built):
    lines = app_build.check(app, INFO, built)
    assert not any("WARNING" in ln for ln in lines)


@pytest.mark.parametrize("app,built,why", [
    ("protoemu3c", dict(INFO, cores=2, clk_div=24), "holds a 3-core"),
    ("protoemu", dict(INFO, cores=3, clk_div=24), "holds a 2-core"),
    ("protoemu48", dict(INFO, cores=2, clk_div=24), "clk_div 25"),
    ("protoemu3c", dict(INFO), "doesn't say how many cores"),
    ("k26-starter-kits", dict(INFO, cores=2, clk_div=24), "not one of our apps")])
def test_a_build_doesnt_install_as_another_app(app, built, why):
    with pytest.raises(SystemExit, match=why):
        app_build.check(app, INFO, built)


def test_another_checkouts_build_only_warns():
    assert any("WARNING" in ln for ln in app_build.check("protoemu3c", INFO, dict(INFO, rtl_rev="c" * 40,
                                                                                   cores=3, clk_div=24)))


# ---------------------------------------------------------------- build.sh (fake ssh and rsync)

FAKE = """#!/bin/bash
printf '%s\\n' "$(basename "$0") $*" >> "$FAKE_LOG"
if [ "$(basename "$0")" = rsync ] && [[ "${@: -1}" == *:*/build/ ]]; then cp -r "${@: -2:1}" "$FAKE_DIR/staged"; fi
exit 0
"""


def build_sh(tmp_path, *argv, **env):
    bin_ = tmp_path / "bin"
    bin_.mkdir(exist_ok=True)
    for name in ("ssh", "rsync"):
        (bin_ / name).write_text(FAKE)
        (bin_ / name).chmod(0o755)
    e = dict(os.environ, PATH=f"{bin_}:{os.environ['PATH']}", FAKE_LOG=str(tmp_path / "log"), FAKE_DIR=str(tmp_path),
             OUT_DIR=str(tmp_path / "out"), TMPDIR=str(tmp_path), ALLOW_DIRTY="1", **env)
    r = subprocess.run(["bash", str(ROOT / "fpga/kv260/build.sh"), *argv], env=e, capture_output=True, text=True)
    log = (tmp_path / "log").read_text() if (tmp_path / "log").exists() else ""
    return r, log


@pytest.mark.parametrize("argv,cores,clk_div", [((), 3, 24), (("--cores", "3"), 3, 24),
                                                (("--cores", "2", "25"), 2, 25), (("--cores=2",), 2, 24)])
def test_build_sh_stages_the_asked_core_count(tmp_path, argv, cores, clk_div):
    r, log = build_sh(tmp_path, *argv, FETCH="0")
    assert r.returncode == 0, r.stderr
    project = (tmp_path / "staged/rtl/src/project.v").read_text()
    assert f"`define PE_NCORES {cores}\n" in project
    vivado = [ln for ln in log.splitlines() if " vivado " in ln]
    assert len(vivado) == 1 and "nice -n 10 vivado" in vivado[0] and f"-tclargs rtl/src out {clk_div}" in vivado[0]
    assert "nice -n 10 bootgen" in vivado[0]
    rec = json.loads((tmp_path / "out/build_info.json").read_text())
    assert (rec["cores"], rec["clk_div"]) == (cores, clk_div)
    assert not any(ln.startswith("rsync") and "protoemu.bit.bin" in ln for ln in log.splitlines())   # FETCH=0


@pytest.mark.parametrize("argv,msg", [(("--cores", "4"), "--cores must be 2 or 3"), (("--cores", "1"), "2 or 3"),
                                      (("20",), "clk_div must be 24"), (("--cores", "3", "16"), "clk_div must be"),
                                      (("--fast",), "unknown option"), (("24", "25"), "usage")])
def test_build_sh_refuses_outside_the_plan_before_any_remote_command(tmp_path, argv, msg):
    r, log = build_sh(tmp_path, *argv)
    assert r.returncode != 0 and msg in r.stderr and log == ""


# ---------------------------------------------------------------- board_smoke.py against a stand-in Kria

HEADER = "Accelerator  Accel_type  Base  Pid  Base_type  #slots(RPU+PL+AIE)  slot->handle"


def table(active, names=("k26-starter-kits", "protoemu", "protoemu40", "protoemu48", "protoemu3c")):
    rows = [f"  {n}  XRT_FLAT  {n}  id_ok  XRT_FLAT  (0+0+0)  {'0->0,' if n == active else '-1'}" for n in names]
    return "\n".join([HEADER, ""] + rows) + "\n"


class Kria:
    """Answers board_smoke.py's ssh commands like a good board, unless a test changes one."""

    def __init__(self):
        self.cmds, self.active, self.answers = [], "protoemu", {}

    def run(self, cmd, timeout):
        self.cmds.append(cmd)
        for key, answer in self.answers.items():
            if key in cmd:
                return answer(self) if callable(answer) else answer
        if cmd == "uptime":
            return 0, " 21:00:00 up 3 days\n"
        if cmd.startswith("pgrep"):
            return 0, ""
        if cmd == "sudo xmutil listapps":
            return 0, table(self.active)
        if cmd == "sudo xmutil unloadapp":
            self.active = None
            return 0, "remove from slot 0 returns: 0 (Ok)\n"
        if cmd.startswith("sudo xmutil loadapp "):
            self.active = cmd.split()[-1]
            return 0, f"{self.active}: loaded to slot 0\n"
        if "pmod_state.py" in cmd:
            return 0, "PMOD0 00000000 PMOD1 00000000 PMODIN ff\n"
        if "bringup_kria.py" in cmd or "spim_test.py" in cmd or "v2_demo.py" in cmd:
            return 0, "...\nALL PASS\n"
        raise AssertionError(f"unexpected command {cmd!r}")


def smoke(tmp_path, kria, phases=("load", "smoke"), built=None, dirty=False):
    out = tmp_path / "out3c"
    out.mkdir(exist_ok=True)
    rec = dict(INFO, cores=3, clk_div=24) if built is None else built
    (out / "build_info.json").write_text(json.dumps(rec))
    a = types.SimpleNamespace(app="protoemu3c", out_dir=str(out), phases=list(phases), expect=None, timeout=5,
                              kria="ubuntu@kria.example")
    lines = []
    s = board_smoke.Smoke(a, kria, say=lines.append)
    s.checkout_info = lambda: dict(INFO, dirty=dirty)
    return s.run(), lines


LOAD = ["sudo xmutil unloadapp", "sudo xmutil loadapp protoemu3c", "sudo xmutil listapps"]


def test_the_whole_sequence_on_a_good_board(tmp_path):
    kria = Kria()
    rc, lines = smoke(tmp_path, kria)
    assert rc == 0 and lines[-1].startswith("ALL PASS")
    tests = [c.split("host/")[1] for c in kria.cmds if "fpga/kv260/host/" in c and not c.startswith("pgrep")]
    assert kria.cmds[:3] == ["uptime", "pgrep -af fpga/kv260/host/ || true", "sudo xmutil listapps"]
    assert kria.cmds[3:6] == LOAD
    assert tests == ["pmod_state.py", "bringup_kria.py", "spim_test.py", "v2_demo.py call la spi i2c",
                     "pmod_state.py"]
    assert all(c.startswith("cd protoemu3c && sudo python3 ") for c in kria.cmds[6:])
    # one xmutil command per ssh command, never chained, nothing after a loadapp
    assert all(c.count("xmutil") <= 1 and ";" not in c for c in kria.cmds)
    assert not any("pmod_test" in c or "eth0" in c or "reboot" in c for c in kria.cmds)


@pytest.mark.parametrize("cmd,answer,why", [
    ("xmutil loadapp", (0, "protoemu3c: Load Error: -1\n"), "Load Error"),
    ("xmutil loadapp", (1, ""), "status 1"),
    ("xmutil unloadapp", (0, "remove from slot 0 returns: -1 (Error)\n"), "Error"),
])
def test_a_failed_load_step_runs_nothing_after_it(tmp_path, cmd, answer, why):
    kria = Kria()
    kria.answers[cmd] = answer
    rc, lines = smoke(tmp_path, kria)
    assert rc == board_smoke.EXIT_REFUSED and why in lines[-1] and "No tool may run" in lines[-1]
    assert kria.cmds[-1] == ("sudo xmutil unloadapp" if "unload" in cmd else "sudo xmutil loadapp protoemu3c")


@pytest.mark.parametrize("listing", [table("k26-starter-kits"), table(None), table("protoemu"),
                                     "protoemu3c: Load Error: -1\n" + table("protoemu3c")])
def test_listapps_must_show_the_app_as_the_one_active_row(tmp_path, listing):
    kria = Kria()
    kria.answers["listapps"] = lambda k: (0, listing) if "sudo xmutil loadapp protoemu3c" in k.cmds else (0, table("protoemu"))
    rc, lines = smoke(tmp_path, kria)
    assert rc == board_smoke.EXIT_REFUSED and kria.cmds[-1] == "sudo xmutil listapps"
    assert not any("python3" in c for c in kria.cmds)


def test_a_running_board_tool_refuses_before_the_load(tmp_path):
    kria = Kria()
    kria.answers["pgrep"] = (0, "4242 python3 protoemu/fpga/kv260/host/usb_demo.py\n")
    rc, lines = smoke(tmp_path, kria)
    assert rc == board_smoke.EXIT_REFUSED and "one process at a time" in lines[-1]
    assert not any("xmutil" in c for c in kria.cmds)


def test_a_board_that_doesnt_answer_is_never_loaded(tmp_path):
    kria = Kria()
    kria.answers["uptime"] = (255, "ssh: connect to host kria.example port 22: Connection timed out\n")
    rc, lines = smoke(tmp_path, kria)
    assert rc == board_smoke.EXIT_REFUSED and kria.cmds == ["uptime"] and "never power-cycle" in lines[-1]


def test_a_failing_test_stops_the_run(tmp_path):
    kria = Kria()
    kria.answers["spim_test.py"] = (1, "shared SCK=mclk/4 1-bit: FAIL\nFAILURES\n")
    rc, lines = smoke(tmp_path, kria)
    assert rc == board_smoke.EXIT_FAILED and kria.cmds[-1].endswith("spim_test.py")
    kria = Kria()
    kria.answers["bringup_kria.py"] = (0, "[crc32]\n  FAIL: ...\n1 step(s) FAILED\n")   # status 0 but no ALL PASS
    assert smoke(tmp_path, kria)[0] == board_smoke.EXIT_FAILED and kria.cmds[-1].endswith("bringup_kria.py")


def test_an_enabled_pmod_pin_stops_the_run(tmp_path):
    kria = Kria()
    kria.answers["pmod_state.py"] = (1, "PMOD0 00000080 PMOD1 00000000 PMODIN ff: a PMOD pin is enabled\n")
    rc, _ = smoke(tmp_path, kria)
    assert rc == board_smoke.EXIT_FAILED and not any("bringup" in c for c in kria.cmds)


def test_a_test_that_doesnt_finish_refuses(tmp_path):
    kria = Kria()
    kria.answers["v2_demo.py"] = (None, "[la]\n")
    rc, lines = smoke(tmp_path, kria)
    assert rc == board_smoke.EXIT_REFUSED and "may still be running" in lines[-1]
    assert kria.cmds[-1].endswith("v2_demo.py call la spi i2c")


@pytest.mark.parametrize("built,dirty,why", [(dict(INFO, cores=2, clk_div=24), False, "holds a 3-core"),
                                             (dict(INFO, cores=3, clk_div=24, rtl_rev="c" * 40), False, "clean RTL"),
                                             (dict(INFO, cores=3, clk_div=24), True, "clean RTL")])
def test_the_wrong_bitstream_refuses_before_any_remote_command(tmp_path, built, dirty, why):
    kria = Kria()
    rc, lines = smoke(tmp_path, kria, built=built, dirty=dirty)
    assert rc == board_smoke.EXIT_REFUSED and why in lines[-1] and kria.cmds == []


def test_smoke_alone_checks_the_loaded_app_first(tmp_path):
    kria = Kria()
    kria.active = "protoemu3c"
    rc, _ = smoke(tmp_path, kria, phases=("smoke",))
    assert rc == 0 and not any("loadapp" in c for c in kria.cmds)
    kria = Kria()                                    # protoemu active: refused, no test runs
    rc, _ = smoke(tmp_path, kria, phases=("smoke",))
    assert rc == board_smoke.EXIT_REFUSED and not any("python3" in c for c in kria.cmds)


def test_install_never_loads(tmp_path):
    seen = []

    def local(cmd, env, **kw):
        seen.append((cmd, env))
        return types.SimpleNamespace(returncode=0, stdout="installed protoemu3c (not loaded). On the Kria, ...\n",
                                     stderr="")
    kria = Kria()
    out = tmp_path / "out3c"
    out.mkdir()
    (out / "protoemu.bit.bin").write_bytes(b"x")
    (out / "build_info.json").write_text(json.dumps(dict(INFO, cores=3, clk_div=24)))
    a = types.SimpleNamespace(app="protoemu3c", out_dir=str(out), phases=["install", "load", "smoke"], expect=None,
                              timeout=5, kria="ubuntu@kria.example")
    s = board_smoke.Smoke(a, kria, local=local, say=lambda m: None)
    s.checkout_info = lambda: dict(INFO)
    assert s.run() == 0
    (cmd, env), = seen
    assert cmd[0].endswith("fpga/kv260/install.sh") and env["LOAD"] == "0" and env["APP"] == "protoemu3c"
    assert env["OUT_DIR"] == str(out) and env["WL_KRIA"] == "ubuntu@kria.example"


def test_board_smoke_needs_wl_kria(monkeypatch):
    monkeypatch.delenv("WL_KRIA", raising=False)
    with pytest.raises(SystemExit):
        board_smoke.parse_args([])


# ---------------------------------------------------------------- pmod_state.py

def test_pmod_state_reads_only():
    import pe_spim
    regs = {pe_spim.VERSION: pe_spim.VERSION_VALUE, pe_spim.PMOD0: 0, pe_spim.PMOD1: 0, pe_spim.PMODIN: 0xFF}
    mem = types.SimpleNamespace(rd=regs.__getitem__, wr=None)
    assert pmod_state.main(mem) == 0
    regs[pe_spim.PMOD1] = 0x80
    assert pmod_state.state(mem) == (False, "PMOD0 00000000 PMOD1 00000080 PMODIN ff")
    regs[pe_spim.VERSION] = 0
    assert not pmod_state.state(mem)[0]
