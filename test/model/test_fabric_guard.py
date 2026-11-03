"""fabric_guard.py: nothing maps the fabric unless an app of ours is shown active.
Fakes only. The incident it answers is in that file's docstring."""
import pathlib
import re
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
HOST = ROOT / "fpga/kv260/host"
sys.path.insert(0, str(HOST))
import fabric_guard as fg                      # noqa: E402
import pe_kria                                 # noqa: E402
import pe_lab                                  # noqa: E402
import pe_spim                                 # noqa: E402

ACTIVE = """   Accelerator   Accel_type   Base   Base_type   #slots(PL+AIE)   Active_slot

   k26-starter-kits   XRT_FLAT   k26-starter-kits   XRT_FLAT   (0+0)   -1
   protoemu40   XRT_FLAT   protoemu40   XRT_FLAT   (0+0)   0,
"""
INACTIVE = ACTIVE.replace("  0,", "  -1")
OTHER = INACTIVE.replace("k26-starter-kits   XRT_FLAT   k26-starter-kits   XRT_FLAT   (0+0)   -1",
                         "k26-starter-kits   XRT_FLAT   k26-starter-kits   XRT_FLAT   (0+0)   0,")


def check(table, tmp_path, state=None, firmware=None, env=None):
    s, f = tmp_path / "state", tmp_path / "firmware"
    if state is not None:
        s.write_text(state + "\n")
    if firmware is not None:
        f.write_bytes(firmware.encode() + b"\0")
    return fg.check(lambda: table, state=str(s), firmware=str(f), env=env or {})


def test_an_active_app_of_ours_passes(tmp_path):
    assert check(ACTIVE, tmp_path, "operating", "protoemu40.bit.bin") == "protoemu40"
    assert check(ACTIVE, tmp_path) == "protoemu40"                   # paths absent: the table decides


@pytest.mark.parametrize("table,why", [
    (INACTIVE, "no active app"),
    (OTHER, "not a protoemu app"),
    ("", "can't read"),
    ("Load Error -1", "no active app"),
    ("garbled", "can't read"),
])
def test_the_incident_and_its_relatives_refuse(tmp_path, table, why):
    with pytest.raises(fg.NoActiveApp, match=why):
        check(table, tmp_path)


def test_every_refusal_says_no_tool_may_run(tmp_path):
    with pytest.raises(fg.NoActiveApp, match="No tool may run"):
        check(INACTIVE, tmp_path)


@pytest.mark.parametrize("state", ["write error", "write init error", "firmware request error", "unknown"])
def test_a_failed_load_in_the_fpga_manager_refuses_even_if_listed_active(tmp_path, state):
    with pytest.raises(fg.NoActiveApp, match="a load failed"):
        check(ACTIVE, tmp_path, state=state)


def test_another_bitstream_in_the_device_tree_refuses_even_if_listed_active(tmp_path):
    with pytest.raises(fg.NoActiveApp, match="firmware-name"):
        check(ACTIVE, tmp_path, firmware="k26-starter-kits.bit.bin")


def test_a_person_can_only_settle_an_unreadable_table(tmp_path):
    env = {fg.OVERRIDE: "protoemu40"}
    assert check("garbled table", tmp_path, env=env) == "protoemu40"
    with pytest.raises(fg.NoActiveApp, match="no active app"):
        check(INACTIVE, tmp_path, env=env)                           # a readable table isn't overruled
    with pytest.raises(fg.NoActiveApp, match="shows k26-starter-kits active"):
        check(OTHER, tmp_path, env=env)
    with pytest.raises(fg.NoActiveApp, match="shows protoemu40 active"):
        check(ACTIVE, tmp_path, env={fg.OVERRIDE: "protoemu"})
    assert check(ACTIVE, tmp_path, env=env) == "protoemu40"
    with pytest.raises(fg.NoActiveApp, match="not a protoemu app"):
        check("garbled table", tmp_path, env={fg.OVERRIDE: "k26-starter-kits"})
    with pytest.raises(fg.NoActiveApp, match="a load failed"):
        check("garbled table", tmp_path, state="write error", env=env)


@pytest.mark.parametrize("failure", [FileNotFoundError("xmutil"), subprocess.TimeoutExpired("xmutil", 30)])
def test_xmutil_not_running_refuses(monkeypatch, failure):
    def boom(*a, **k):
        raise failure
    monkeypatch.setattr(fg.subprocess, "run", boom)
    with pytest.raises(fg.NoActiveApp, match="didn't run"):
        fg.listapps()


def test_xmutil_failing_refuses(monkeypatch):
    monkeypatch.setattr(fg.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "no daemon"))
    with pytest.raises(fg.NoActiveApp, match="no daemon"):
        fg.listapps()


OPENERS = [pe_kria.DevMem, pe_spim.SpimMem, pe_lab.LabMem]


@pytest.mark.parametrize("opener", OPENERS, ids=lambda c: c.__name__)
def test_each_opener_refuses_before_opening_dev_mem(monkeypatch, opener):
    monkeypatch.setattr(fg, "_checked", None)
    monkeypatch.setattr(fg, "listapps", lambda: INACTIVE)
    monkeypatch.setattr("os.open", lambda *a, **k: pytest.fail("/dev/mem was opened"))
    monkeypatch.setattr(fg, "check", lambda f=fg.check, **kw: f(lambda: INACTIVE, env={}))
    with pytest.raises(fg.NoActiveApp):
        opener()


@pytest.mark.parametrize("opener", OPENERS, ids=lambda c: c.__name__)
def test_each_opener_opens_only_after_the_check_passes(monkeypatch, opener):
    calls = []

    class Opened(Exception):
        pass

    def fake_open(path, *a):
        calls.append(path)
        raise Opened
    monkeypatch.setattr(fg, "_checked", None)
    monkeypatch.setattr(fg, "check", lambda: calls.append("checked") or "protoemu40")
    monkeypatch.setattr("os.open", fake_open)
    with pytest.raises(Opened):
        opener()
    assert calls == ["checked", "/dev/mem"]


@pytest.mark.parametrize("build", [lambda: pe_spim.Spim(), lambda: pe_lab.Lab(), lambda: pe_kria.Board()],
                         ids=["Spim", "Lab", "Board"])
def test_nothing_reads_a_register_on_a_board_with_no_app(monkeypatch, build):
    """The incident's path: building the object read VERSION."""
    monkeypatch.setattr(fg, "_checked", None)
    monkeypatch.setattr(fg, "check", lambda f=fg.check, **kw: f(lambda: INACTIVE, env={}))
    monkeypatch.setattr("mmap.mmap", lambda *a, **k: pytest.fail("mapped"))
    monkeypatch.setattr("os.open", lambda *a, **k: pytest.fail("opened"))
    with pytest.raises(fg.NoActiveApp):
        build()


def test_injected_objects_skip_the_guard(monkeypatch):
    """The simulation and the cycle model pass their own memory."""
    monkeypatch.setattr(fg, "require_app", lambda: pytest.fail("guard on an injected object"))

    class Mem:
        def rd(self, off):
            return pe_spim.VERSION_VALUE
    assert pe_spim.Spim(Mem()).mem.rd(0) == pe_spim.VERSION_VALUE


def test_require_app_checks_once_a_process(monkeypatch):
    n = []
    monkeypatch.setattr(fg, "_checked", None)
    monkeypatch.setattr(fg, "check", lambda: n.append(1) or "protoemu")
    assert fg.require_app() == fg.require_app() == "protoemu" and len(n) == 1


def test_every_dev_mem_opener_in_the_tree_is_guarded():
    found = {}
    for top in ("fpga", "tools", "flow", "test"):
        for p in (ROOT / top).rglob("*.py"):
            if p.name.startswith("test_"):
                continue
            text = p.read_text(errors="replace")
            for m in re.finditer(r"""open\(\s*["']/dev/mem""", text):
                found[p.name] = text[:m.start()].rsplit("def ", 1)[1]
    assert sorted(found) == ["pe_kria.py", "pe_lab.py", "pe_spim.py"]
    assert all("fabric_guard.require_app()" in body for body in found.values())


def test_the_guard_itself_never_maps_anything():
    text = (HOST / "fabric_guard.py").read_text().split('"""', 2)[2]
    assert "mmap" not in text and "/dev/mem" not in text and "memoryview" not in text
