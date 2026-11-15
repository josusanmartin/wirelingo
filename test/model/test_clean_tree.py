"""The release identity (097h): flow/clean_tree.py's receipt and release_check's
gate on it. A run releases only as the harden of a clean checkout of exactly the
shipping tree, with the pinned tt-support-tools, image and PDK, and the
generated configs regenerated equal. The three routes 097g found past the
file-set manifest (a sibling a Tcl file sources, an extra root-level plugin, an
info.yaml source file under tt/) fail here, whatever reads the file: committed,
the tree differs; uncommitted, the receipt is DIRTY."""
import json
from pathlib import Path
import os
import subprocess
import sys

import pytest

from test_release_evidence import candidate, complete_receipts, commit_all, module
from test_release_gates import release, with_shipping, ship_clone, shipping


def releases(tmp_path, capsys, **clean):
    wt = candidate(tmp_path)
    shipping(wt, tmp_path, copy=False)
    args = complete_receipts(wt, **clean)
    return wt, args


def test_a_clean_harden_of_the_shipping_tree_releases(tmp_path, capsys):
    wt, args = releases(tmp_path, capsys)
    ship = ship_clone(wt, tmp_path)
    rc, text = release(with_shipping(args, ship), capsys)
    assert rc == 0, text
    assert "clean tree: the run hardened a clean checkout of" in text
    assert "clean tree: generated configs: regenerated from the shipping tree, equal" in text
    data = json.loads((wt / "runs/clean-tree.json").read_text())
    assert data["status"] == "CLEAN" and data["worktree"]["status"] == ["!! tt/"]
    # the run is its own shipping checkout too (the harden's outputs are owned)
    rc, text = release(args, capsys)
    assert rc == 0, text


def stray(path, text="x\n"):
    def make(wt):
        (wt / path).parent.mkdir(parents=True, exist_ok=True)
        (wt / path).write_text(text)
    return make


@pytest.mark.parametrize("case,path,reason", [
    ("tt-stray-v", "tt/zz.v", "tt/: ['?? zz.v']"),                                     # B3'
    ("tt-pycache-v", "tt/__pycache__/zz.v", "tt/: ['!! __pycache__/zz.v']"),           # B3
    ("tt-pyc-untracked", "tt/__pycache__/zz.cpython-312.pyc", "tt/: ['!! __pycache__/zz.cpython-312.pyc']"),
    ("tt-module-named", "tt/__pycache__/tt_tool", "tt/: ['!! __pycache__/tt_tool']"),  # not bytecode
    ("root-plugin", "librelane_plugin_zz.py", "?? librelane_plugin_zz.py"),            # B2
    ("plugin-package", "openlane_plugin_zz/__init__.py", "?? openlane_plugin_zz/__init__.py"),   # B2'
    ("sourced-sibling", "docs/exp/extra.sdc", "?? docs/exp/extra.sdc"),               # B1, uncommitted
])
def test_an_uncommitted_file_at_harden_start_makes_the_run_dirty(tmp_path, capsys, case, path, reason):
    wt, args = releases(tmp_path, capsys, before_start=stray(path))
    data = json.loads((wt / "runs/clean-tree.json").read_text())
    assert data["status"] == "DIRTY" and any(reason in r for r in data["reasons"]), data["reasons"]
    ship = ship_clone(wt, tmp_path)
    rc, text = release(with_shipping(args, ship), capsys)
    assert rc == 1 and "clean tree: the run's tree was DIRTY" in text and reason in text


@pytest.mark.parametrize("pyc,reason", [
    # 100b: a .pyc whose header copies its source's mtime and size is loaded instead
    # of the source, so even a tracked module's bytecode at start is a stray
    ("__pycache__/librelane_plugin_protoemu.cpython-312.pyc", "worktree at start"),
    ("__pycache__/librelane_plugin_zz.cpython-312.pyc", "worktree at start"),
    ("tt/__pycache__/tt_tool.cpython-312.pyc", "tt/: ['!! __pycache__/tt_tool.cpython-312.pyc']"),
])
def test_bytecode_at_start_is_a_stray(tmp_path, capsys, pyc, reason):
    wt, args = releases(tmp_path, capsys, before_start=stray(pyc, "crafted bytecode"),
                        after_start=lambda w: __import__("shutil").rmtree(w / "tt/__pycache__", ignore_errors=True))
    data = json.loads((wt / "runs/clean-tree.json").read_text())
    assert data["status"] == "DIRTY" and any(reason in r for r in data["reasons"]), data["reasons"]
    if not pyc.startswith("tt/"):      # bytecode there at start is not "compiled since start" either
        assert f"!! {pyc}" in data["generated_worktree"]["stray"]
        assert f"!! {pyc}" not in data["generated_worktree"]["compiled"]


@pytest.mark.parametrize("pyc,clean", [
    ("__pycache__/librelane_plugin_protoemu.cpython-312.pyc", True),     # the tracked plugin, imported later
    ("tt/__pycache__/tt_tool.cpython-312.pyc", True),                    # a tracked tt module
    ("__pycache__/librelane_plugin_zz.cpython-312.pyc", False),          # no such tracked module
    ("tt/__pycache__/zz.cpython-312.pyc", False),                        # nor in tt/
])
def test_bytecode_compiled_after_start_from_a_tracked_module_is_owned(tmp_path, capsys, pyc, clean):
    def compile_(w):
        (w / pyc).parent.mkdir(parents=True, exist_ok=True)
        (w / pyc).write_bytes(b"bytecode")
    wt, args = releases(tmp_path, capsys, compiled=compile_)
    data = json.loads((wt / "runs/clean-tree.json").read_text())
    assert (data["status"] == "CLEAN") is clean, data["reasons"]
    name = pyc.split("/", 1)[1] if pyc.startswith("tt/") else pyc
    record = data["generated_tt"] if pyc.startswith("tt/") else data["generated_worktree"]
    assert f"!! {name}" in record["compiled"]


def test_regeneration_reads_no_bytecode(tmp_path, capsys):
    # the run's tt/ holds bytecode compiled during the flow; the release
    # regenerates the configs from its sources alone (the stand-in tool refuses
    # to run beside a tt/__pycache__)
    wt, args = releases(tmp_path, capsys)
    (wt / "tt/__pycache__").mkdir()
    (wt / "tt/__pycache__/tt_tool.cpython-312.pyc").write_bytes(b"crafted")
    rc, text = release(with_shipping(args, ship_clone(wt, tmp_path)), capsys)
    assert rc == 0 and "generated configs: regenerated from the shipping tree, equal" in text


def test_the_start_verdict_does_not_expect_generated_configs(tmp_path):
    wt = candidate(tmp_path)
    shipping(wt, tmp_path, copy=False)
    complete_receipts(wt)
    ct = module("clean_tree")
    subprocess.run(["mv", str(wt / "runs"), str(tmp_path / "aside")], check=True)
    from test_release_evidence import harden_env
    data = ct.start(wt, "img", tmp_path / "pdk-checkout", ident="sha256:" + "d1" * 32, environ=harden_env(),
                    syspath=[])
    assert data["phase"] == "start" and "generated configs not recorded" not in data["reasons"]
    assert "generated configs not recorded" in ct.verdict(data)[1]


def test_a_file_present_only_at_start_still_makes_the_run_dirty(tmp_path, capsys):
    # the start status alone sees it (removed before generation)
    def gone(wt):
        (wt / "librelane_plugin_zz.py").unlink()
    wt, args = releases(tmp_path, capsys, before_start=stray("librelane_plugin_zz.py"), after_start=gone)
    data = json.loads((wt / "runs/clean-tree.json").read_text())
    assert data["status"] == "DIRTY" and data["generated_worktree"]["stray"] == []
    assert any("worktree at start" in r for r in data["reasons"])


def test_a_change_between_start_and_generation_makes_the_run_dirty(tmp_path, capsys):
    # the generation status alone sees it (a rewritten committed file)
    def rewrite(wt):
        (wt / "src/config.json").write_text('{"CLOCK_PERIOD":21}')
    wt, args = releases(tmp_path, capsys, after_start=rewrite)
    data = json.loads((wt / "runs/clean-tree.json").read_text())
    assert data["status"] == "DIRTY" and data["worktree"]["stray"] == []
    assert any("after generation" in r and " M src/config.json" in r for r in data["reasons"])


@pytest.mark.parametrize("case", ["sourced-sibling", "plugin", "info-source-under-tt"])
def test_a_committed_difference_is_a_different_tree(tmp_path, capsys, case):
    # B1 committed: the shipping commit changes a file a Tcl sources (no manifest
    # binds it); B2 a plugin; B3 info.yaml naming a source under tt/
    wt, args = releases(tmp_path, capsys)
    ship = ship_clone(wt, tmp_path)
    if case == "sourced-sibling":
        (ship / "docs/exp").mkdir(parents=True)
        (ship / "docs/exp/extra.sdc").write_text("set_max_transition 2.0 [current_design]\n")
    elif case == "plugin":
        (ship / "librelane_plugin_zz.py").write_text("# imported by LibreLane\n")
    else:
        (ship / "info.yaml").write_text("project:\n  top_module: design\n  source_files:\n    - ../tt/zz.v\n")
    commit_all(ship)
    rc, text = release(with_shipping(args, ship), capsys)
    assert rc == 1 and "clean tree: the run's tree" in text and "is not the shipping checkout's" in text


def test_a_dirty_shipping_checkout_is_refused(tmp_path, capsys):
    wt, args = releases(tmp_path, capsys)
    ship = ship_clone(wt, tmp_path)
    (ship / "docs").mkdir()
    (ship / "docs/extra.sdc").write_text("# untracked\n")
    rc, text = release(with_shipping(args, ship), capsys)
    assert rc == 1 and "clean tree: the shipping checkout is not clean: ['?? docs/extra.sdc']" in text


@pytest.mark.parametrize("key", ["librelane_image", "pdk", "tt_support_tools"])
def test_the_image_pdk_and_tt_must_be_the_pinned_ones(tmp_path, capsys, key):
    wt, args = releases(tmp_path, capsys, pins={key: "sha256:" + "0" * 64 if key == "librelane_image" else "0" * 40})
    ship = ship_clone(wt, tmp_path)
    rc, text = release(with_shipping(args, ship), capsys)
    assert rc == 1 and f"clean tree: {key}: the run used" in text


def test_regenerated_configs_must_equal_the_runs(tmp_path, capsys):
    wt, args = releases(tmp_path, capsys)
    ship = ship_clone(wt, tmp_path)
    p = wt / "runs/clean-tree.json"
    data = json.loads(p.read_text())
    data["generated"]["src/config_merged.json"] = "0" * 64
    p.write_text(json.dumps(data))
    rc, text = release(with_shipping(args, ship), capsys)
    assert rc == 1 and "clean tree: generated configs differ" in text


def test_without_a_receipt_the_run_cannot_release(tmp_path, capsys):
    wt, args = releases(tmp_path, capsys)
    (wt / "runs/clean-tree.json").unlink()
    rc, text = release(with_shipping(args, ship_clone(wt, tmp_path)), capsys)
    assert rc == 1 and "clean tree: no clean-tree receipt" in text


def test_a_config_that_differs_from_the_commit_makes_the_run_dirty(tmp_path):
    # an experiment preset, an option or a core count rewrites a committed file
    wt = candidate(tmp_path)
    shipping(wt, tmp_path, copy=False)
    complete_receipts(wt)
    ct = module("clean_tree")
    (wt / "src/config.json").write_text('{"CLOCK_PERIOD":21}')
    subprocess.run(["mv", str(wt / "runs"), str(tmp_path / "aside")], check=True)
    from test_release_evidence import harden_env
    ct.start(wt, "img", tmp_path / "pdk-checkout", ident="sha256:" + "d1" * 32, environ=harden_env(), syspath=[])
    data = ct.generated(wt)
    assert data["status"] == "DIRTY" and any(" M src/config.json" in r for r in data["reasons"])


def test_the_harden_records_the_receipt_before_generating():
    from pathlib import Path
    text = (Path(__file__).resolve().parents[2] / "flow/harden.sh").read_text()
    a, b, c = (text.index("clean_tree.py start"), text.index("gen_config.py --preset"),
               text.index("clean_tree.py generated"))
    copy = text.index("--exclude=__pycache__ --exclude='*.pyc' -cf - tt")      # tt/ in without bytecode (100b)
    assert copy < a < b < text.index("tt_tool.py --create-user-config") < c
    assert c < text.index("tt_tool.py --harden")
    # the version probe imports LibreLane outside the worktree, writing no bytecode there
    assert "LLV=$(cd / && PYTHONDONTWRITEBYTECODE=1 python -c" in text
    # 100c: the worktree is added with no hooks (a post-checkout hook could rewrite it)
    assert 'git -c core.hooksPath=/dev/null -C "$REPO" worktree add -q --detach "$W" HEAD' in text


def test_the_repository_pins_tt_the_image_and_the_pdk():
    import re
    from pathlib import Path
    pins = json.loads((Path(__file__).resolve().parents[2] / "flow/release_pins.json").read_text())
    assert set(pins) == {"tt_support_tools", "librelane_image", "pdk"}
    assert re.fullmatch(r"[0-9a-f]{40}", pins["tt_support_tools"]) and re.fullmatch(r"[0-9a-f]{40}", pins["pdk"])
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", pins["librelane_image"])


# ---- 100c: bytes, not only status (item 100, finding 2): routes in the harden's
# own git configuration that status does not show; each must be DIRTY

def git_in(repo, *args):
    from test_release_evidence import git
    return git(repo, *args)


def assume_unchanged(wt):
    git_in(wt, "update-index", "--assume-unchanged", "src/project.v")
    (wt / "src/project.v").write_text("`define PE_NCORES 1\n")


def skip_worktree(wt):
    git_in(wt, "update-index", "--skip-worktree", "info.yaml")
    (wt / "info.yaml").write_text("project:\n  top_module: other\n")


def smudge(wt):
    (wt / ".git/info").mkdir(parents=True, exist_ok=True)
    (wt / ".git/info/attributes").write_text("src/project.v filter=x\n")
    git_in(wt, "config", "filter.x.smudge", "sed s/PE_NCORES/PE_NCORES_X/")
    git_in(wt, "config", "filter.x.clean", "sed s/PE_NCORES_X/PE_NCORES/")
    (wt / "src/project.v").unlink()
    git_in(wt, "checkout", "--", "src/project.v")


def sparse(wt):
    git_in(wt, "sparse-checkout", "set", "--no-cone", "/*", "!/flow/helper.tcl")


def tt_replace(wt):
    tt = wt / "tt"
    pin = git_in(tt, "rev-parse", "HEAD")
    (tt / "tt_tool.py").write_text((tt / "tt_tool.py").read_text() + "# altered\n")
    git_in(tt, "commit", "-qam", "altered")
    alt = git_in(tt, "rev-parse", "HEAD")
    git_in(tt, "replace", pin, alt)
    git_in(tt, "checkout", "-q", pin)


@pytest.mark.parametrize("route", [assume_unchanged, skip_worktree, smudge, sparse, tt_replace],
                         ids=["assume-unchanged", "skip-worktree", "smudge-filter", "sparse-checkout", "tt-replace"])
def test_a_change_status_does_not_show_is_dirty_in_bytes(tmp_path, capsys, route):
    wt, args = releases(tmp_path, capsys, before_start=route)
    data = json.loads((wt / "runs/clean-tree.json").read_text())
    assert data["status"] == "DIRTY" and any("differ from the tree in bytes" in r for r in data["reasons"]), \
        data["reasons"]
    rc, text = release(with_shipping(args, ship_clone(wt, tmp_path)), capsys)
    assert rc == 1 and "from the tree in bytes" in text


# ---- 100c: the receipt bound to its run (finding 3f), the image LibreLane ran (3b),
# the outer process (3c), the config LibreLane read (3e)

def test_a_copied_receipt_or_a_changed_run_fails(tmp_path, capsys):
    wt, args = releases(tmp_path, capsys)
    ship = ship_clone(wt, tmp_path)
    (wt / "docs").mkdir()
    (wt / "docs/extra.sdc").write_text("# added to the run after its harden\n")
    rc, text = release(with_shipping(args, ship), capsys)
    assert rc == 1 and "the run worktree holds more than its tree" in text
    (wt / "docs/extra.sdc").unlink()
    p = wt / "runs/clean-tree.json"
    data = json.loads(p.read_text())
    data["worktree_path"] = "/elsewhere/run"
    p.write_text(json.dumps(data))
    rc, text = release(with_shipping(args, ship), capsys)
    assert rc == 1 and "the receipt was recorded in /elsewhere/run, not in this run" in text


def test_a_run_moved_to_another_commit_fails(tmp_path, capsys):
    # an empty commit keeps the tree and the bytes: only HEAD tells
    from test_release_evidence import git
    wt, args = releases(tmp_path, capsys)
    ship = ship_clone(wt, tmp_path)
    git(wt, "commit", "-q", "--allow-empty", "-m", "after the harden")
    rc, text = release(with_shipping(args, ship), capsys)
    assert rc == 1 and "the run worktree is at tree" in text


def test_the_image_librelane_ran_must_be_the_receipts(tmp_path, capsys):
    wt, args = releases(tmp_path, capsys)
    log = wt / "exp.log"
    log.write_text(log.read_text().replace("ghcr.io/librelane/librelane:3.1.0.dev3", "ghcr.io/someone/librelane:x"))
    rc, text = release(with_shipping(args, ship_clone(wt, tmp_path)), capsys)
    assert rc == 1 and "LibreLane ran image ghcr.io/someone/librelane:x" in text
    log.write_text("no container line\n")
    rc, text = release(with_shipping(args, ship_clone(wt, tmp_path, "ship2")), capsys)
    assert rc == 1 and "exp.log names no container image" in text


@pytest.mark.parametrize("var", ["PYTHONPATH", "LIBRELANE_IMAGE_OVERRIDE"])
def test_an_outer_python_path_or_image_override_is_dirty(tmp_path, capsys, monkeypatch, var):
    monkeypatch.setenv(var, "/somewhere")
    wt, args = releases(tmp_path, capsys)
    data = json.loads((wt / "runs/clean-tree.json").read_text())
    assert data["status"] == "DIRTY" and any(f"{var} was set" in r for r in data["reasons"])
    assert len(data["venv"]["sha256"]) == 64
    text = (Path(__file__).resolve().parents[2] / "flow/harden.sh").read_text()
    assert "for v in PYTHONPATH LIBRELANE_IMAGE_OVERRIDE; do" in text


def test_a_plugin_on_the_outer_path_is_dirty(tmp_path):
    ct = module("clean_tree")
    extra = tmp_path / "sitepkgs"
    (extra / "openlane_plugin_pkg").mkdir(parents=True)
    (extra / "librelane_plugin_other.py").write_text("# imported by LibreLane's plugin discovery\n")
    (extra / "openlane_plugin_pkg/__init__.py").write_text("# a plugin package\n")
    (extra / "unrelated.py").write_text("# not a plugin\n")
    assert ct.outer_plugins([str(extra)]) == ["librelane_plugin_other", "openlane_plugin_pkg"]
    data = {"worktree": {"stray": []}, "tt": {"stray": []}, "image": {"id": "x", "name": "x"},
            "pdk": {"stray": []}, "generated": {}, "outer_plugins": ["librelane_plugin_other"]}
    st, reasons = ct.verdict(data)
    assert st == "DIRTY" and any("librelane_plugin_other" in r for r in reasons)


def test_the_config_librelane_read_must_equal_the_regeneration(tmp_path, capsys):
    wt, args = releases(tmp_path, capsys)
    (wt / "src/config_merged.json").write_text('{"rewritten": "after the generated record"}\n')
    rc, text = release(with_shipping(args, ship_clone(wt, tmp_path)), capsys)
    assert rc == 1 and "the configs LibreLane read differ from the regenerated ones" in text


def test_the_regeneration_runs_beside_the_checkout_with_the_flow_python_first(tmp_path, capsys, monkeypatch):
    # item 100 finding 1: under /tmp and without the venv on PATH, the real tt tool failed
    log = tmp_path / "tt.log"
    monkeypatch.setenv("FAKE_TT_LOG", str(log))
    wt, args = releases(tmp_path, capsys)
    ship = ship_clone(wt, tmp_path)
    rc, text = release(with_shipping(args, ship), capsys)
    assert rc == 0, text
    cwd, path = json.loads(log.read_text().splitlines()[-1])
    assert Path(cwd).parent == ship.parent and path.split(os.pathsep)[0] == os.path.dirname(sys.executable)


# ---- item 100 findings 4 and 6

def test_a_venv_link_in_the_shipping_checkout_is_owned(tmp_path, capsys):
    wt, args = releases(tmp_path, capsys)
    ship = ship_clone(wt, tmp_path)
    (ship / ".venv-flow").symlink_to(tmp_path)        # a fresh clone without an exclude: "?? .venv-flow"
    rc, text = release(with_shipping(args, ship), capsys)
    assert rc == 0, text


def test_the_gate_reads_the_shipping_commits_pins_not_the_runs(tmp_path, capsys):
    wt, args = releases(tmp_path, capsys)
    ship = ship_clone(wt, tmp_path)
    p = wt / "flow/release_pins.json"
    pins = json.loads(p.read_text())
    p.write_text(json.dumps(dict(pins, tt_support_tools="0" * 40)))
    rc, text = release(with_shipping(args, ship), capsys)
    assert f"clean tree: tt_support_tools: {pins['tt_support_tools']} (pinned)" in text


def test_a_pdk_stray_is_dirty(tmp_path, capsys):
    def stray_pdk(wt):
        (wt.parent / "pdk-checkout/ihp-sg13cmos5l/extra.lib").write_text("library (x) { }\n")
    wt, args = releases(tmp_path, capsys, before_start=stray_pdk)
    data = json.loads((wt / "runs/clean-tree.json").read_text())
    assert data["status"] == "DIRTY" and any("PDK:" in r and "extra.lib" in r for r in data["reasons"])


def test_the_shipping_checkouts_tt_must_be_the_pinned_clean_commit(tmp_path, capsys):
    import shutil
    wt, args = releases(tmp_path, capsys)
    ship = ship_clone(wt, tmp_path)
    shutil.copytree(wt / "tt", ship / "tt", symlinks=True)
    (ship / "tt/tt_tool.py").write_text((ship / "tt/tt_tool.py").read_text() + "# changed\n")
    rc, text = release(with_shipping(args, ship), capsys)
    assert rc == 1 and "not a clean checkout of the pinned tt-support-tools" in text


# ---- 100d: the container command reaches exp.log (item 100c finding 1)

LAUNCHER = ("import os, shlex, sys\n"
            "print('[INFO] Running containerized command:', file=sys.stderr)\n"
            "print(shlex.join(['docker', 'run', '--rm', '-w', '/w', 'ghcr.io/librelane/librelane:3.1.0.dev3',\n"
            "                  'python3', '-m', 'librelane']))\n"
            "os.execlp('true', 'true')\n")


@pytest.mark.parametrize("unbuffered", [False, True])
def test_the_container_command_reaches_the_log_only_unbuffered(tmp_path, unbuffered):
    # LibreLane's run_in_container prints the command and execs docker without a
    # flush: into a file, the line is lost unless Python's output is unbuffered
    ct = module("clean_tree")
    env = {k: v for k, v in os.environ.items() if k != "PYTHONUNBUFFERED"}
    if unbuffered:
        env["PYTHONUNBUFFERED"] = "1"
    with (tmp_path / "exp.log").open("w") as log:
        subprocess.run([sys.executable, "-c", LAUNCHER], stdout=log, stderr=subprocess.STDOUT, env=env, check=True)
    assert ct.container_image(tmp_path) == ("ghcr.io/librelane/librelane:3.1.0.dev3" if unbuffered else None)


def test_the_harden_runs_librelane_unbuffered():
    text = (Path(__file__).resolve().parents[2] / "flow/harden.sh").read_text()
    exported = text.index("export PYTHONUNBUFFERED=1")
    assert exported < text.index("python -m librelane --pdk-root") and exported < text.index("tt_tool.py --harden")


# ---- 100d: bytes at release, links, modes and end-of-line attributes (item 100c findings 3-5)

def hide_edit(repo, path, text):
    """An edit status does not show (assume-unchanged)."""
    git_in(repo, "update-index", "--assume-unchanged", path)
    (repo / path).write_text(text)


def test_the_run_edited_after_its_harden_fails_in_bytes(tmp_path, capsys):
    wt, args = releases(tmp_path, capsys)
    hide_edit(wt, "src/project.v", "`define PE_NCORES 1\n")
    rc, text = release(with_shipping(args, ship_clone(wt, tmp_path)), capsys)
    assert rc == 1 and "the run worktree differs from its tree in bytes: ['src/project.v (bytes differ" in text


def test_a_shipping_checkout_edited_out_of_sight_fails_in_bytes(tmp_path, capsys):
    wt, args = releases(tmp_path, capsys)
    ship = ship_clone(wt, tmp_path)
    hide_edit(ship, "info.yaml", "project:\n  top_module: other\n")
    rc, text = release(with_shipping(args, ship), capsys)
    assert rc == 1 and "the shipping checkout differs from its tree in bytes: ['info.yaml (bytes differ" in text


def toy_repo(tmp_path, files, attributes=None):
    from test_release_evidence import commit_all
    repo = tmp_path / "toy"
    repo.mkdir(parents=True)
    for name, text in files.items():
        (repo / name).write_text(text)
    if attributes:
        (repo / ".gitattributes").write_text(attributes)
    commit_all(repo)
    return repo


def test_a_retargeted_link_and_a_lost_exec_bit_differ(tmp_path):
    ct = module("clean_tree")
    repo = toy_repo(tmp_path, {"a.txt": "a\n", "b.txt": "b\n", "run.sh": "#!/bin/sh\n"})
    (repo / "link").symlink_to("a.txt")
    (repo / "run.sh").chmod(0o755)
    commit_all(repo)
    assert ct.bytes_differ(repo, "HEAD^{tree}") == []
    (repo / "link").unlink()
    (repo / "link").symlink_to("b.txt")
    git_in(repo, "update-index", "--assume-unchanged", "link")
    git_in(repo, "config", "core.fileMode", "false")
    (repo / "run.sh").chmod(0o644)
    assert git_in(repo, "status", "--porcelain") == ""
    assert ct.bytes_differ(repo, "HEAD^{tree}") == ["link (bytes differ)", "run.sh (mode differs from 100755)"]


def local_filter_and_text(wt):
    (wt / ".git/info").mkdir(parents=True, exist_ok=True)
    (wt / ".git/info/attributes").write_text("src/project.v filter=v100 text\n")
    git_in(wt, "config", "filter.v100.smudge", "sed s/PE_NCORES/PE_NCORES_X/")
    git_in(wt, "config", "filter.v100.clean", "sed s/PE_NCORES_X/PE_NCORES/")
    (wt / "src/project.v").unlink()
    git_in(wt, "checkout", "--", "src/project.v")


def local_eol_crlf(wt):
    (wt / ".git/info").mkdir(parents=True, exist_ok=True)
    (wt / ".git/info/attributes").write_text("src/project.v eol=crlf\n")
    (wt / "src/project.v").unlink()
    git_in(wt, "checkout", "--", "src/project.v")


@pytest.mark.parametrize("route", [local_filter_and_text, local_eol_crlf], ids=["local-filter-text", "local-eol-crlf"])
def test_a_local_attribute_or_filter_is_dirty_in_bytes(tmp_path, capsys, route):
    # item 102 finding 1: local attributes and filters are never consulted (100e)
    wt, args = releases(tmp_path, capsys, before_start=route)
    assert b"\r\n" in (wt / "src/project.v").read_bytes() or "PE_NCORES_X" in (wt / "src/project.v").read_text()
    data = json.loads((wt / "runs/clean-tree.json").read_text())
    assert data["status"] == "DIRTY" and any("src/project.v (bytes differ" in r for r in data["reasons"])
    rc, text = release(with_shipping(args, ship_clone(wt, tmp_path)), capsys)
    assert rc == 1


def test_a_committed_eol_attribute_with_a_local_clean_filter_still_differs(tmp_path):
    ct = module("clean_tree")
    repo = toy_repo(tmp_path, {"lf.txt": "a\nb\n"}, attributes="*.txt text eol=crlf\n")
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(repo), str(clone)], check=True)
    (clone / ".git/info/attributes").write_text("lf.txt filter=x\n")
    git_in(clone, "config", "filter.x.clean", "sed s/altered/a/")
    (clone / "lf.txt").write_bytes(b"altered\r\nb\r\n")
    assert ct.bytes_differ(clone, "HEAD^{tree}") == [
        "lf.txt (bytes differ; the gate needs a default clone, without core.autocrlf, local attributes or filters)"]
    assert ct.converting_attributes(clone, "HEAD^{tree}") == [".gitattributes: *.txt text eol=crlf"]


def test_an_autocrlf_clone_differs_in_bytes(tmp_path):
    ct = module("clean_tree")
    plain = toy_repo(tmp_path, {"lf.txt": "a\nb\n"})
    crlf = tmp_path / "crlf"
    subprocess.run(["git", "-c", "core.autocrlf=true", "clone", "-q", str(plain), str(crlf)], check=True)
    assert ct.bytes_differ(crlf, "HEAD^{tree}") == [
        "lf.txt (bytes differ; the gate needs a default clone, without core.autocrlf, local attributes or filters)"]


@pytest.mark.parametrize("line", ["*.v text", "*.txt eol=lf", "src/** filter=lfs diff=lfs", "* text=auto",
                                  "docs/x ident", "*.v crlf", "*.txt working-tree-encoding=UTF-16"])
def test_a_tree_whose_attributes_convert_on_checkout_cannot_release(tmp_path, capsys, line):
    wt, args = releases(tmp_path, capsys, attributes=line + "\n")
    data = json.loads((wt / "runs/clean-tree.json").read_text())
    assert data["status"] == "DIRTY" and data["converting_attributes"] == [f".gitattributes: {line}"]
    rc, text = release(with_shipping(args, ship_clone(wt, tmp_path)), capsys)
    assert rc == 1 and "the shipping tree's .gitattributes set a checkout conversion" in text


@pytest.mark.parametrize("line", ["evidence/** -text", "*.bin binary", "*.v !text", "# a text comment", "*.v -crlf"])
def test_attributes_that_disable_conversion_release(tmp_path, capsys, line):
    wt, args = releases(tmp_path, capsys, attributes=line + "\n")
    rc, text = release(with_shipping(args, ship_clone(wt, tmp_path)), capsys)
    assert rc == 0, text


def test_a_clean_default_clone_of_this_repository_passes_in_bytes(tmp_path):
    ct = module("clean_tree")
    root = Path(__file__).resolve().parents[2]
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", "--no-local", str(root), str(clone)], check=True,
                   env=dict(os.environ, GIT_CONFIG_NOSYSTEM="1"))
    assert ct.converting_attributes(clone, "HEAD^{tree}") == []
    assert ct.bytes_differ(clone, "HEAD^{tree}") == []


def test_a_nested_attributes_file_counts(tmp_path):
    ct = module("clean_tree")
    repo = toy_repo(tmp_path, {"a.txt": "a\n"}, attributes="evidence/** -text\n")
    (repo / "docs").mkdir()
    (repo / "docs/.gitattributes").write_text("# nested\n*.md text eol=lf\n")
    commit_all(repo)
    assert ct.converting_attributes(repo, "HEAD^{tree}") == ["docs/.gitattributes: *.md text eol=lf"]
