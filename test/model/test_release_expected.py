"""The 3-core 8x4 release's identity gates (audit 2, R1, R5 and R9):

* flow/release_expected.py compares a hardened run's final netlist sha256, its
  GDS digest with the dates zeroed and its late-clock register count with a
  tracked expected file (evidence/8x4-release-expected.json); GitHub's gds job
  runs it after the GDS build;
* GitHub's workflows pin the action and the tools and run that comparison."""
import json
from pathlib import Path
import re
import shutil

import pytest

from test_release_evidence import candidate, module

ROOT = Path(__file__).resolve().parents[2]
EXPECTED = ROOT / "evidence/8x4-release-expected.json"
RECORDS = ROOT / "evidence/8x4-release-candidate-20261115"
LATE_LOG = "runs/wokwi/37-project-sramclockadvance/1-project-sramclockadvancetcl/project-sramclockadvancetcl.log"
ACTION_SHA = "3412659307918422f3f0727917cf9b499aaca588"
TOOLS_SHA = "d66cf179e7bc4d296362ab7e2e3b344dc3c4f665"


def gds(stamp):
    """A small GDSII stream: HEADER, BGNLIB with a date `stamp`, LIBNAME, ENDLIB."""
    rec = lambda kind, body: (len(body) + 4).to_bytes(2, "big") + kind.to_bytes(2, "big") + body
    date = b"".join(int(x).to_bytes(2, "big") for x in [stamp] * 12)
    return rec(0x0002, (600).to_bytes(2, "big")) + rec(0x0102, date) + rec(0x0206, b"LIB\0") + rec(0x0400, b"")


def late_log(wt, text="LATE_CLOCK_COMPLETE 64 registers 23 branches\n"):
    p = wt / LATE_LOG
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("REG_CLOCK_DELAY_COMPLETE 6 stages (flops), 8 (clock gates) on 3 branch loads\n"
                 "LATE_CLOCK clknet_leaf_106_clk_regs: 2 registers behind 6 buffers\n" + text)
    return p


def expected_for(wt, path, **change):
    """An expected file that matches the run `wt`, with `change` applied."""
    ex = module("release_expected")
    data = dict(schema=ex.SCHEMA, **ex.actual_hashes(wt), late_clock={"registers": 64, "branches": 23},
                nonlatch_floor_ns=0.3, slow_mhz=48)
    data.update(change)
    path.write_text(json.dumps(data))
    return path


def fixture_run(tmp_path):
    wt = candidate(tmp_path)
    (wt / "runs/wokwi/final/gds/design.gds").write_bytes(gds(2026))
    late_log(wt)
    return wt


# ---- the tracked expected file holds the records' full values

def test_the_expected_file_is_the_records_full_hashes_and_count():
    ex = module("release_expected")
    data = ex.load(EXPECTED)
    log = (RECORDS / "rt10s5-pre/release-check.log").read_text()
    assert f"netlist: not compared (run: {data['netlist_sha256']}," in log
    assert re.search(rf"^sha256 nl {data['netlist_sha256'][:16]} ", log, re.M)
    lines = (RECORDS / "gds-normalized.txt").read_text().splitlines()
    assert len(lines) == 3 and all(l.split()[0] == data["gds_normalized_sha256"] for l in lines)
    late = (RECORDS / "late-clock.txt").read_text().splitlines()
    assert len(late) == 3 and all(f"LATE_CLOCK_COMPLETE {data['late_clock']['registers']} registers "
                                  f"{data['late_clock']['branches']} branches" in l for l in late)
    assert data["nonlatch_floor_ns"] == 0.3 and data["slow_mhz"] == 48
    assert "+0.4304 ns at _53246_/D" in (RECORDS / "rt10s5-pre/sta-nonlatch.txt").read_text()
    for record in data["records"].values():          # every record it names is in the tree
        paths = re.findall(r"evidence/[^\s)]+", record)
        assert paths and all((ROOT / p).is_file() for p in paths), record


# ---- flow/release_expected.py on its own (GitHub's gds job)

def test_a_matching_run_passes_and_prints_both_pairs(tmp_path, capsys):
    ex = module("release_expected")
    wt = fixture_run(tmp_path)
    exp = expected_for(wt, tmp_path / "expected.json")
    assert ex.main([str(wt), str(exp)]) == 0
    text = capsys.readouterr().out
    for key in ("netlist_sha256", "gds_normalized_sha256"):
        assert re.search(rf"^{key} expected [0-9a-f]{{64}}$", text, re.M)
        assert re.search(rf"^{key} actual   [0-9a-f]{{64}} \(match\)$", text, re.M)
    assert "RELEASE IDENTITY MATCHES" in text and "late clock" not in text
    # a rebuild differs from it only in the GDS dates: still the same build
    (wt / "runs/wokwi/final/gds/design.gds").write_bytes(gds(2027))
    assert ex.main([str(wt), str(exp), "--late-clock"]) == 0
    assert "actual 64 on 23 (match)" in capsys.readouterr().out


@pytest.mark.parametrize("what", ["netlist", "gds", "both"])
def test_a_different_build_fails_and_prints_both_pairs(tmp_path, capsys, what):
    ex = module("release_expected")
    wt = fixture_run(tmp_path)
    exp = expected_for(wt, tmp_path / "expected.json")
    if what in ("netlist", "both"):
        (wt / "runs/wokwi/final/nl/design.nl.v").write_text("module design; wire w; endmodule\n")
    if what in ("gds", "both"):
        (wt / "runs/wokwi/final/gds/design.gds").write_bytes(gds(2026) + gds(2026))
    assert ex.main([str(wt), str(exp)]) == 1
    text = capsys.readouterr().out
    assert "RELEASE IDENTITY MISMATCH" in text and text.count(" expected ") >= 2
    assert ("netlist_sha256 actual" in text and "gds_normalized_sha256 actual" in text)
    assert ("netlist_sha256 " in text.split("MISMATCH")[-1]) == (what != "gds")
    assert ("gds_normalized_sha256 " in text.split("MISMATCH")[-1]) == (what != "netlist")
    assert re.search(r"^- netlist_sha256 [0-9a-f]{64} is not the expected [0-9a-f]{64}$", text, re.M) or what == "gds"


@pytest.mark.parametrize("log,message", [
    ("LATE_CLOCK_COMPLETE 63 registers 23 branches\n", "63 registers on 23 branches, expected 64 on 23"),
    ("LATE_CLOCK_COMPLETE 64 registers 22 branches\n", "64 registers on 22 branches, expected 64 on 23"),
    ("LATE_CLOCK_COMPLETE 0 registers 0 branches\n", "0 registers on 0 branches"),
    ("", "expected one LATE_CLOCK_COMPLETE line, found 0"),
    ("LATE_CLOCK_COMPLETE 64 registers 23 branches\n" * 2, "expected one LATE_CLOCK_COMPLETE line, found 2"),
    (None, "expected one SRAM clock advance step log"),
])
def test_the_late_clock_count_must_be_the_expected_one(tmp_path, capsys, log, message):
    ex = module("release_expected")
    wt = fixture_run(tmp_path)
    exp = expected_for(wt, tmp_path / "expected.json")
    if log is None:
        shutil.rmtree((wt / LATE_LOG).parents[1])
    else:
        late_log(wt, log)
    assert ex.main([str(wt), str(exp)]) == 0                  # hashes only
    capsys.readouterr()
    assert ex.main([str(wt), str(exp), "--late-clock"]) == 1
    assert message in capsys.readouterr().out


@pytest.mark.parametrize("change", [
    dict(schema="other"), dict(netlist_sha256="453d0af7bea77dd5"), dict(gds_normalized_sha256="Z" * 64),
    dict(late_clock={"registers": 64}), dict(late_clock={"registers": True, "branches": 23}),
    dict(late_clock={"registers": -1, "branches": 23}), dict(nonlatch_floor_ns="0.3"),
    dict(nonlatch_floor_ns=float("nan")), dict(slow_mhz=0), dict(slow_mhz=60),
])
def test_a_malformed_expected_file_is_refused(tmp_path, capsys, change):
    ex = module("release_expected")
    wt = fixture_run(tmp_path)
    exp = expected_for(wt, tmp_path / "expected.json", **change)
    assert ex.main([str(wt), str(exp)]) == 1
    assert "RELEASE IDENTITY UNREADABLE" in capsys.readouterr().out


# ---- the GitHub workflows (read as text: CI's Python has no YAML package)

def steps(workflow, job):
    """Each step of one job of a workflow file, as its text."""
    text = (ROOT / ".github/workflows" / workflow).read_text()
    jobs = re.split(r"^  ([A-Za-z_]+):\n", text.split("\njobs:\n", 1)[1], flags=re.M)
    body = dict(zip(jobs[1::2], jobs[2::2]))[job]
    return re.split(r"^      - ", body.split("    steps:\n", 1)[1], flags=re.M)[1:]


def test_the_gds_workflow_pins_the_action_and_the_tools_and_checks_the_hashes():
    text = (ROOT / ".github/workflows/gds.yaml").read_text()
    uses = re.findall(r"uses: (TinyTapeout/tt-gds-action\S*)", text)
    assert len(uses) == 4 and all(u.endswith("@" + ACTION_SHA) for u in uses)
    gds_steps = steps("gds.yaml", "gds")
    i = next(n for n, s in enumerate(gds_steps) if s.startswith("name: Build GDS\n"))
    assert f"tools-ref: {TOOLS_SHA}\n" in gds_steps[i] and "librelane-version: 3.1.0.dev3\n" in gds_steps[i]
    assert f"tools-ref: {TOOLS_SHA}\n" in steps("gds.yaml", "precheck")[0]
    assert any(re.search(r"^        run: python3 flow/release_expected.py \. evidence/8x4-release-expected.json$",
                         s, re.M) for s in gds_steps[i + 1:])
    pins = json.loads((ROOT / "flow/release_pins.json").read_text())
    assert pins["tt_support_tools"] == TOOLS_SHA


def test_the_test_workflow_runs_the_tcl_tests_and_lists_skips():
    wf = steps("test.yaml", "test")
    assert "fetch-depth: 0\n" in wf[0]                 # test_clean_tree clones this repository
    assert any("libtk8.6" in s for s in wf)
    model = next(s for s in wf if "python -m pytest" in s)
    assert "tkinter.Tcl()" in model and "pytest -q -rs test/model" in model
    assert not any("tapeout" in s.splitlines()[0] for s in wf)
