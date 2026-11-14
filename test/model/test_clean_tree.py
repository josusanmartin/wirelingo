"""The release identity (097h): flow/clean_tree.py's receipt and release_check's
gate on it. A run releases only as the harden of a clean checkout of exactly the
shipping tree, with the pinned tt-support-tools, image and PDK, and the
generated configs regenerated equal. The three routes 097g found past the
file-set manifest (a sibling a Tcl file sources, an extra root-level plugin, an
info.yaml source file under tt/) fail here, whatever reads the file: committed,
the tree differs; uncommitted, the receipt is DIRTY."""
import json
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
    ct.start(wt, "img", tmp_path / "pdk-checkout", ident="sha256:" + "d1" * 32)
    data = ct.generated(wt)
    assert data["status"] == "DIRTY" and any(" M src/config.json" in r for r in data["reasons"])


def test_the_harden_records_the_receipt_before_generating():
    from pathlib import Path
    text = (Path(__file__).resolve().parents[2] / "flow/harden.sh").read_text()
    a, b, c = (text.index("clean_tree.py start"), text.index("gen_config.py --preset"),
               text.index("clean_tree.py generated"))
    assert text.index('cp -r "$REPO/tt" .') < a < b < text.index("tt_tool.py --create-user-config") < c
    assert c < text.index("tt_tool.py --harden")


def test_the_repository_pins_tt_the_image_and_the_pdk():
    import re
    from pathlib import Path
    pins = json.loads((Path(__file__).resolve().parents[2] / "flow/release_pins.json").read_text())
    assert set(pins) == {"tt_support_tools", "librelane_image", "pdk"}
    assert re.fullmatch(r"[0-9a-f]{40}", pins["tt_support_tools"]) and re.fullmatch(r"[0-9a-f]{40}", pins["pdk"])
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", pins["librelane_image"])
