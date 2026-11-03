"""fabric_guard.py: nothing maps the fabric unless one of our apps is shown active,
every check passing, and any doubt refuses. Fakes only; the board data below is the
orchestrator's read-only reading of the Kria on 2026-11-03."""
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

HEADER = "Accelerator  Accel_type  Base  Pid  Base_type  #slots(RPU+PL+AIE)  slot->handle"
# Rows as the board printed them on 2026-11-03 (listapps-20261103.txt, the whole table:
# test_the_boards_whole_table_on_2026_11_03), spacing aside.


def row(name, active=False):
    return f"  {name}  XRT_FLAT  {name}  id_ok  XRT_FLAT  (0+0+0)  {'0->0,' if active else '-1'}"


def table(active=None, names=("k26-starter-kits", "protoemu", "protoemu40", "protoemu48")):
    return "\n".join([HEADER, ""] + [row(n, n == active) for n in names]) + "\n"


BOARD_2026_11_03 = table("k26-starter-kits")         # the incident: the default app held the slot
LOAD_ERROR = "protoemu: Load Error: -1"                # what the failed load printed


@pytest.fixture
def kernel(tmp_path):
    """The kernel's view of a good protoemu40 load; tests spoil one piece at a time."""
    k = dict(state=tmp_path / "state", firmware=tmp_path / "firmware-name", firmware_dir=tmp_path / "fw")
    k["state"].write_text("operating\n")
    k["firmware"].write_bytes(b"protoemu40.bit.bin\0")
    for app in fg.KNOWN:
        (k["firmware_dir"] / app).mkdir(parents=True)
        (k["firmware_dir"] / app / f"{app}.bit.bin").write_bytes(b"x")
    return k


def check(text, kernel, **spoil):
    return fg.check(lambda: text, **{**kernel, **spoil})


def test_a_good_load_passes(kernel):
    assert check(table("protoemu40"), kernel) == "protoemu40"


def test_the_incident_refuses(kernel):
    kernel["firmware"].write_bytes(b"k26-starter-kits.bit.bin\0")
    with pytest.raises(fg.NoActiveApp, match="'k26-starter-kits', not one of ours"):
        check(BOARD_2026_11_03, kernel)


@pytest.mark.parametrize("text,why", [
    (table(None), "no active app"),
    (LOAD_ERROR, "isn't a row"),
    (table("protoemu40") + LOAD_ERROR + "\n", "isn't a row"),
    ("protoemu: load failed, error code 1", "isn't a row"),
    ("Error: Failed to connect to dfx-mgr", "isn't a row"),
    ("", "didn't print its table"),
    (HEADER + "\n", "didn't print its table"),
    (table("protoemu40").replace(HEADER, ""), "didn't print its table"),
    (HEADER + "\n" + table("protoemu40"), "2 headers"),
    (table("protoemu40").replace("id_ok  XRT_FLAT  (0+0+0)  0->0,", "XRT_FLAT  (0+0+0)  0->0,"), "isn't a row"),
    (table("protoemu40").replace("protoemu40  id_ok", "protoemu40  extra  id_ok"), "isn't a row"),
    (table("protoemu40").replace("protoemu40  id_ok  XRT_FLAT  (0+0+0)  0->0,", "protoemu40  0  XRT_FLAT  (0+0+0)  0->0,"),
     "isn't a row"),
    (table("protoemu40").replace("(0+0+0)  0->0,", "(0+0+0)  0->0,  extra"), "isn't a row"),
    (table("protoemu40").replace("(0+0+0)  0->0,", "(0+0)  0->0,"), "isn't a row"),
    (table("protoemu40").replace("0->0,", "0,"), "isn't a row"),
    (table("protoemu40").replace("0->0,", "0->0"), "isn't a row"),
    (table("protoemu40").replace("(0+0+0)  -1", "(0+0+0)  0->0,", 1), "2 active rows"),
    (table("protoemu-broken", names=("protoemu-broken", "protoemu40")), "not one of ours"),
    (table("protoemu_old", names=("protoemu_old",)), "not one of ours"),
    (table("protoemu4", names=("protoemu4",)), "not one of ours"),
])
def test_anything_but_one_known_active_row_refuses(kernel, text, why):
    with pytest.raises(fg.NoActiveApp, match=why):
        check(text, kernel)


def test_old_header_format_refuses(kernel):
    old = "Accelerator  Accel_type  Base  Base_type  #slots(PL+AIE)  Active_slot\n  protoemu40  XRT_FLAT  protoemu40  XRT_FLAT  (0+0)  0,\n"
    with pytest.raises(fg.NoActiveApp):
        check(old, kernel)


@pytest.mark.parametrize("piece,content,why", [
    ("firmware", None, "firmware-name is missing"),
    ("firmware", b"k26-starter-kits.bit.bin\0", "'k26-starter-kits.bit.bin'"),
    ("firmware", b"protoemu.bit.bin\0", "'protoemu.bit.bin', not protoemu40.bit.bin"),
    ("state", None, "state is missing"),
    ("state", b"write error\n", "'write error'"),
    ("state", b"unknown\n", "'unknown'"),
])
def test_each_kernel_file_must_be_there_and_agree(kernel, piece, content, why):
    if content is None:
        kernel[piece].unlink()
    else:
        kernel[piece].write_bytes(content)
    with pytest.raises(fg.NoActiveApp, match=why):
        check(table("protoemu40"), kernel)


@pytest.mark.parametrize("firmware", [b"protoemu40.bit.bin\0", b"protoemu48.bit.bin\0"])
def test_an_app_whose_name_starts_the_file_name_still_refuses(kernel, firmware):
    kernel["firmware"].write_bytes(firmware)
    with pytest.raises(fg.NoActiveApp, match="not protoemu.bit.bin"):
        check(table("protoemu"), kernel)


# The header and the protoemu40 row exactly as inbox item 022 quotes the 2026-11-03 read
# (that capture was cut at 110 columns, so only these two lines are verbatim)
REAL_HEADER = ("                     Accelerator  Accel_type                    Base    Pid   Base_type  "
               "#slots(RPU+PL+AIE)    slot->handle")
REAL_ROW = ("                    protoemu40    XRT_FLAT              protoemu40  id_ok    XRT_FLAT             "
            "(0+0+0)              -1")


def test_the_boards_own_lines_refuse_inactive_and_pass_active(kernel):
    with pytest.raises(fg.NoActiveApp):
        check(f"{REAL_HEADER}\n{REAL_ROW}\n", kernel)
    active = REAL_ROW[:-len("-1")] + "0->0,"
    assert check(f"{REAL_HEADER}\n{active}\n", kernel) == "protoemu40"


def test_an_uninstalled_app_refuses(kernel):
    (kernel["firmware_dir"] / "protoemu40" / "protoemu40.bit.bin").unlink()
    with pytest.raises(fg.NoActiveApp, match="isn't installed"):
        check(table("protoemu40"), kernel)


def test_the_real_paths_are_the_boards():
    assert fg.FIRMWARE_NAME == "/proc/device-tree/fpga-region/firmware-name"
    assert fg.FIRMWARE_DIR == "/lib/firmware/xilinx" and fg.FPGA_STATE == "/sys/class/fpga_manager/fpga0/state"
    assert fg.KNOWN == ("protoemu", "protoemu40", "protoemu48")


def test_there_is_no_override(kernel, monkeypatch):
    monkeypatch.setenv("PE_ACTIVE_APP", "protoemu40")
    for text in ("", "garbled", table(None)):
        with pytest.raises(fg.NoActiveApp):
            check(text, kernel)
    assert "PE_ACTIVE_APP" not in (HOST / "fabric_guard.py").read_text()
    assert "environ" not in (HOST / "fabric_guard.py").read_text()


def test_every_refusal_says_no_tool_may_run(kernel):
    for text in ("", LOAD_ERROR, table(None), BOARD_2026_11_03):
        with pytest.raises(fg.NoActiveApp, match="No tool may run"):
            check(text, kernel)


@pytest.mark.parametrize("text", [BOARD_2026_11_03, table(None)], ids=["another app", "no app"])
def test_a_refusal_with_another_or_no_app_gives_the_load_sequence(kernel, text):
    with pytest.raises(fg.NoActiveApp) as e:
        check(text, kernel)
    m = str(e.value)
    m = m[m.index("xmutil unloadapp"):]
    assert m.index("xmutil loadapp protoemu") < m.index("xmutil listapps")


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
    monkeypatch.setattr(fg, "listapps", lambda: BOARD_2026_11_03)
    monkeypatch.setattr("os.open", lambda *a, **k: pytest.fail("/dev/mem was opened"))
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
def test_the_incidents_path_reads_no_register(monkeypatch, build):
    """Building Spim read VERSION; with the default app active nothing may be mapped."""
    monkeypatch.setattr(fg, "_checked", None)
    monkeypatch.setattr(fg, "listapps", lambda: BOARD_2026_11_03)
    monkeypatch.setattr("mmap.mmap", lambda *a, **k: pytest.fail("mapped"))
    monkeypatch.setattr("os.open", lambda *a, **k: pytest.fail("opened"))
    with pytest.raises(fg.NoActiveApp, match="k26-starter-kits"):
        build()


def test_a_refusal_is_not_cached_as_a_pass(monkeypatch):
    monkeypatch.setattr(fg, "_checked", None)
    monkeypatch.setattr(fg, "check", lambda: (_ for _ in ()).throw(fg.NoActiveApp("no")))
    for _ in range(2):
        with pytest.raises(fg.NoActiveApp):
            fg.require_app()


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


def test_require_app_with_expect_refuses_any_other_active_app(monkeypatch, kernel):
    """A run for one clock and pin map (eth_tap's fpga backend, --measure-spim) names
    its app: another of ours, active and well loaded, refuses too, then and later."""
    monkeypatch.setattr(fg, "_checked", None)
    text = table("protoemu40")
    with pytest.raises(fg.NoActiveApp, match="active app is 'protoemu40', and this run needs 'protoemu'"):
        fg.require_app(expect="protoemu", read=lambda: text, **kernel)
    with pytest.raises(fg.NoActiveApp, match="loadapp protoemu`"):
        fg.require_app(expect="protoemu")
    assert fg.require_app(expect="protoemu40") == fg.require_app() == "protoemu40"
    with pytest.raises(fg.NoActiveApp, match="not one of our apps"):
        fg.require_app(expect="k26-starter-kits")


def test_require_app_expect_still_needs_every_check(monkeypatch, kernel):
    monkeypatch.setattr(fg, "_checked", None)
    kernel["state"].write_text("unknown\n")
    with pytest.raises(fg.NoActiveApp, match="'unknown'"):
        fg.require_app(expect="protoemu40", read=lambda: table("protoemu40"), **kernel)
    assert fg._checked is None


def test_app_names_for_the_clocks():
    assert fg.APP_FOR_MHZ == {50: "protoemu", 40: "protoemu40", 48: "protoemu48"}
    assert set(fg.APP_FOR_MHZ.values()) == set(fg.KNOWN)


# ---------------------------------------------------------------- the board's whole table, verbatim
BOARD_TABLE = (pathlib.Path(__file__).parent / "listapps-20261103.txt").read_text()
MERGED = "kv260-q5-full-pq5-g32d128_gbpd-150"           # a name that fills Base's column


@pytest.fixture
def protoemu_kernel(kernel):
    kernel["firmware"].write_bytes(b"protoemu.bit.bin\0")
    return kernel


def test_the_boards_whole_table_on_2026_11_03(protoemu_kernel):
    """`sudo xmutil listapps` on the Kria after the separate unload/load/listapps
    sequence, 58 lines as printed, rows with Accel_type and Base run together
    included. Refused before the merged rows were read (the 2026-11-03 runbook stop)."""
    lines = BOARD_TABLE.splitlines()
    assert len(lines) == 58 and sum(line.endswith("-1") for line in lines) == 56
    assert f"XRT_FLAT{MERGED}" in BOARD_TABLE
    r = fg.rows(BOARD_TABLE)
    assert len(r) == 57 and (MERGED, "-1") in r and ("protoemu", "0->0,") in r
    assert sum(n.startswith("kv260-") for n, _ in r) == 52
    assert sum(len(line.split()) == 6 for line in lines) == 42     # the merged rows
    assert check(BOARD_TABLE, protoemu_kernel) == "protoemu"


@pytest.mark.parametrize("spoil,why", [
    (lambda t: t.replace("0->0,", "-1"), "no active app"),
    (lambda t: t.replace("protoemu40  id_ok    XRT_FLAT             (0+0+0)              -1",
                         "protoemu40  id_ok    XRT_FLAT             (0+0+0)           0->0,"), "2 active rows"),
    (lambda t: t + "protoemu: Load Error: -1\n", "isn't a row"),
    (lambda t: t.replace(f"XRT_FLAT{MERGED}", f"XRT_FLAT{MERGED[:-1]}"), "isn't a row"),       # Base isn't the name
    (lambda t: t.replace(f"XRT_FLAT{MERGED}", f"XRT_FLAT{MERGED[:-1]}9"), "isn't a row"),      # nor here, same length
    (lambda t: t.replace("protoemu40    XRT_FLAT              protoemu40  id_ok",
                         "protoemu40    XRT_FLAT              protoemu41  id_ok"), "isn't a row"),
    (lambda t: t.replace("protoemu40    XRT_FLAT              protoemu40  id_ok    XRT_FLAT ",
                         "protoemu40    xrt_flat              protoemu40  id_ok    xrt_flat "), "isn't a row"),
    (lambda t: t.replace(f"XRT_FLAT{MERGED}", MERGED), "isn't a row"),                       # no type before it
    (lambda t: t.replace(f"XRT_FLAT{MERGED}", f"xrt_flat{MERGED}"), "isn't a row"),          # not a type
    (lambda t: t.replace(f"XRT_FLAT{MERGED}  id_ok", f"XRT_FLAT{MERGED}  1234"), "isn't a row"),
    (lambda t: t.replace(f"XRT_FLAT{MERGED}  id_ok    XRT_FLAT", f"XRT_FLAT{MERGED}  id_ok    XRT_FULL"),
     "isn't a row"),
    (lambda t: t.replace(f"XRT_FLAT{MERGED}", f"XRT_FLAT {MERGED} extra"), "isn't a row"),
    (lambda t: t.replace("(0+0+0)           0->0,", "(0+0+0)           0->0"), "isn't a row"),
    (lambda t: t + t.splitlines()[1] + "\n", "lists an app twice"),
    (lambda t: "\n".join(t.splitlines()[1:]), "didn't print its table"),
])
def test_the_boards_table_spoiled_one_way_at_a_time_refuses(protoemu_kernel, spoil, why):
    text = spoil(BOARD_TABLE)
    assert text != BOARD_TABLE
    with pytest.raises(fg.NoActiveApp, match=why):
        check(text, protoemu_kernel)


def test_a_merged_row_is_read_only_as_type_then_its_own_name():
    assert fg.row(f"{MERGED}    XRT_FLAT{MERGED}  id_ok    XRT_FLAT   (0+0+0)   -1") == (MERGED, "-1")
    assert fg.row("ab    XRT_FLATab  id_ok    XRT_FLAT   (0+0+0)   0->0,") == ("ab", "0->0,")
    for bad in ("ab    XRT_FLATxab  id_ok    XRT_FLAT   (0+0+0)   -1",       # the type would be XRT_FLATx
                "ab    ab  id_ok    XRT_FLAT   (0+0+0)   -1",
                "ab    XRT_FLATab  id_ok    XRT_FLAT   (0+0+0)",
                "-ab    XRT_FLAT-ab  id_ok    XRT_FLAT   (0+0+0)   -1"):
        assert fg.row(bad) is None
