"""The 3-core 8x4 release's identity gates (audit 2, R1, R5 and R9):

* flow/release_expected.py compares a hardened run's final netlist sha256, its
  GDS digest with the dates zeroed and its late-clock register count with a
  tracked expected file (evidence/8x4-release-expected.json); GitHub's gds job
  runs it after the GDS build;
* flow/release_check.py --expect FILE makes the same comparison a release
  condition, and --require-nonlatch-floor NS requires the non-latch STA receipt
  with NS of slack at the slow corner, at the release's slow period;
* flow/release_evidence.py runs the non-latch job and passes both options on.

A fake `docker` stands in for OpenSTA (test_sta_nonlatch.py)."""
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest

from test_release_evidence import candidate, complete_receipts, module
from test_sta_nonlatch import fake_sta, run  # noqa: F401 (fake_sta is a fixture)
from test_flow_slow48 import sta_step, STEP, SLOW
from test_flow_tools import evidence_args, flow_module, git_clone

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


def test_two_sram_clock_advance_step_logs_are_refused(tmp_path, capsys):
    # a second run of the step in the same run directory: neither count is taken
    ex = module("release_expected")
    wt = fixture_run(tmp_path)
    exp = expected_for(wt, tmp_path / "expected.json")
    assert ex.main([str(wt), str(exp), "--late-clock"]) == 0
    second = wt / LATE_LOG.replace("37-project-", "52-project-")
    second.parent.mkdir(parents=True)
    shutil.copy(wt / LATE_LOG, second)
    capsys.readouterr()
    assert ex.main([str(wt), str(exp), "--late-clock"]) == 1
    text = capsys.readouterr().out
    assert "late clock: expected one SRAM clock advance step log" in text and "found 2" in text
    assert "actual unreadable" in text and "RELEASE IDENTITY MISMATCH" in text


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


# ---- flow/release_check.py --expect and --require-nonlatch-floor

def passing_run(tmp_path, *extra):
    """test_sta_nonlatch's run(), with the run's own STA meeting 20 ns at every
    corner (so the release passes without a fresh STA), and its non-latch receipt."""
    wt = sta_step(candidate(tmp_path))
    p = wt / "runs/wokwi/final/metrics.json"
    m = json.loads(p.read_text())
    m.update({f"timing__setup__ws__corner:{SLOW}": 0.2, f"timing__setup_vio__count__corner:{SLOW}": 0})
    p.write_text(json.dumps(m))
    p = wt / "runs/wokwi" / STEP / "config.json"
    c = json.loads(p.read_text())
    pdk = Path(c["PDK_ROOT"])
    c.update(STA_CORNERS=["nom_typ_1p20V_25C", SLOW, "nom_fast_1p32V_m40C"],
             CELL_LIBS={"nom_slow_*": [str(pdk / "slow.lib")], "nom_typ_*": [str(pdk / "typ.lib")],
                        "nom_fast_*": [str(pdk / "fast.lib")]},
             MACROS={"SRAM": {"lib": {"*_slow_*": [str(pdk / "sram_slow.lib")]}}}, IO_DELAY_CONSTRAINT=20)
    p.write_text(json.dumps(c))
    (wt / "src/protoemu.sdc").write_text("set period $::env(CLOCK_PERIOD)\nset io $::env(IO_DELAY_CONSTRAINT)\n")
    args = complete_receipts(wt)
    assert module("sta_nonlatch").main([str(wt), "--out-parent", str(tmp_path / "out"), *extra]) == 0
    out = sorted((tmp_path / "out").glob("nonlatch.*"))[-1]
    return wt, args, out


def gated(tmp_path, *extra, **change):
    """A complete fixture release that passes at 50 MHz, a non-latch receipt at the
    run's 20 ns (+0.4304 ns slow, the fake report's), the late-clock log and an
    expected file matching the run."""
    wt, args, out = passing_run(tmp_path, *extra)
    late_log(wt)
    exp = expected_for(wt, tmp_path / "expected.json", **dict(dict(slow_mhz=50), **change))
    return wt, args + ["--nonlatch-receipt", str(out / "receipt.json")], exp


def check(args, capsys):
    capsys.readouterr()
    rc = module("release_check").main(args)
    return rc, capsys.readouterr().out


def test_the_8x4_release_passes_with_both_gates(tmp_path, fake_sta, capsys):
    wt, args, exp = gated(tmp_path)
    rc, text = check(args + ["--require-nonlatch-floor", "0.3", "--expect", str(exp)], capsys)
    assert rc == 0, text
    assert "nom_slow_1p08V_125C: worst non-latch setup slack +0.4304 ns meets the floor of 0.3 ns" in text
    assert "release identity: the run is the build" in text and "(match)" in text
    assert "late clock expected 64 registers on 23 branches; actual 64 on 23 (match)" in text
    # without the options a run is checked as before
    rc, text = check(args, capsys)
    assert rc == 0 and "release identity: not compared (no --expect)" in text and "meets the floor" not in text


def test_the_floor_needs_the_receipt(tmp_path, fake_sta, capsys):
    wt, args, exp = gated(tmp_path)
    i = args.index("--nonlatch-receipt")
    rc, text = check(args[:i] + args[i + 2:] + ["--require-nonlatch-floor", "0.3"], capsys)
    assert rc == 1 and "--require-nonlatch-floor 0.3: no non-latch STA receipt" in text


def test_a_slack_below_the_floor_fails(tmp_path, fake_sta, capsys):
    wt, args, exp = gated(tmp_path)
    rc, text = check(args + ["--require-nonlatch-floor", "0.5"], capsys)
    assert rc == 1 and "worst non-latch setup slack 0.4304 ns is below the floor of 0.5 ns" in text
    rc, text = check(args + ["--require-nonlatch-floor", "0.4304"], capsys)
    assert rc == 0, text


def test_the_floor_is_at_the_slow_period(tmp_path, fake_sta, capsys):
    # a receipt at 20.832 ns does not show margin at 50 MHz (20 ns)
    wt, args, exp = gated(tmp_path, "--period", "20.832")
    rc, text = check(args + ["--require-nonlatch-floor", "0.3"], capsys)
    assert rc == 1 and "the non-latch STA was timed at 20.832 ns, not at 20 ns or tighter" in text
    rc, text = check(args + ["--require-nonlatch-floor", "0.3", "--slow-mhz", "48"], capsys)
    assert "meets the floor of 0.3 ns" in text and "not at 20.8333 ns or tighter" not in text


def test_the_floor_needs_a_verified_receipt(tmp_path, fake_sta, capsys):
    wt, args, exp = gated(tmp_path)
    receipt = Path(args[args.index("--nonlatch-receipt") + 1])
    (receipt.parent / "nom_slow_1p08V_125C.rpt").chmod(0o644)
    (receipt.parent / "nom_slow_1p08V_125C.rpt").write_text("edited\n")
    rc, text = check(args + ["--require-nonlatch-floor", "0.3"], capsys)
    assert rc == 1 and "--require-nonlatch-floor 0.3: the non-latch STA receipt did not verify" in text


def test_the_floor_reads_the_one_slow_corner():
    rel = module("release_check")
    data = lambda results, period=20.832: {"plan": {"period_ns": period}, "results": results}
    slow = {"nom_slow_1p08V_125C": {"nonlatch": 0.4304}, "nom_typ_1p20V_25C": {"nonlatch": 3.3}}
    assert rel.nonlatch_floor_failures(data(slow), "r", 0.3, 48) == []
    assert "no single slow corner" in rel.nonlatch_floor_failures(data({"nom_typ_1p20V_25C": {"nonlatch": 3.3}}),
                                                                  "r", 0.3, 48)[0]
    assert "below the floor" in rel.nonlatch_floor_failures(data({"nom_slow_1p08V_125C": {"nonlatch": float("nan")}}),
                                                            "r", 0.3, 48)[0]
    assert "timed at" in rel.nonlatch_floor_failures(data(slow, float("inf")), "r", 0.3, 48)[0]
    with pytest.raises(SystemExit):
        rel.main(["x", "--require-nonlatch-floor", "nan"])


@pytest.mark.parametrize("change,message", [
    (dict(netlist_sha256="0" * 64), "release identity: netlist_sha256"),
    (dict(gds_normalized_sha256="0" * 64), "release identity: gds_normalized_sha256"),
    (dict(late_clock={"registers": 65, "branches": 23}), "release identity: late clock: 64 registers on 23"),
    (dict(nonlatch_floor_ns=0.4), "requires --require-nonlatch-floor 0.4 or more"),
    (dict(slow_mhz=50), "releases 50 MHz at the slow corner, not 48"),       # with --slow-mhz 48
    (dict(schema="x"), "invalid release identity evidence"),
])
def test_expect_refuses_another_build_or_weaker_terms(tmp_path, fake_sta, capsys, change, message):
    wt, args, exp = gated(tmp_path, **change)
    slow = ["--slow-mhz", "48"] if "slow_mhz" in change else []
    rc, text = check(args + ["--require-nonlatch-floor", "0.3", "--expect", str(exp)] + slow, capsys)
    assert rc == 1 and message in text, text


def test_expect_needs_the_floor_option(tmp_path, fake_sta, capsys):
    wt, args, exp = gated(tmp_path)
    rc, text = check(args + ["--expect", str(exp)], capsys)
    assert rc == 1 and "requires --require-nonlatch-floor 0.3 or more" in text
    rc, text = check(args + ["--expect", str(exp), "--require-nonlatch-floor", "0.2"], capsys)
    assert rc == 1 and "requires --require-nonlatch-floor 0.3 or more" in text


def test_expect_reads_the_late_clock_from_the_run(tmp_path, fake_sta, capsys):
    wt, args, exp = gated(tmp_path)
    (wt / LATE_LOG).unlink()
    rc, text = check(args + ["--require-nonlatch-floor", "0.3", "--expect", str(exp)], capsys)
    assert rc == 1 and "release identity: late clock: expected one SRAM clock advance step log" in text


def test_an_archive_compares_the_hashes_and_says_it_skips_the_late_clock(tmp_path, fake_sta, capsys):
    wt, args, exp = gated(tmp_path)
    for name in ("state_in.json", "config.json"):
        (wt / "runs/wokwi/99-openroad-stapostpnr" / name).unlink()
    rc, text = check(args + ["--require-nonlatch-floor", "0.3", "--expect", str(exp)], capsys)
    assert "netlist_sha256 actual" in text and "(match)" in text
    assert "release gate, SKIPPED: release identity: late clock: an evidence archive keeps no step logs" in text
    rc, text = check(args + ["--require-nonlatch-floor", "0.3", "--expect", str(exp), "--require-sta-inputs"],
                     capsys)
    assert rc == 1 and "release gates would be skipped" in text


# ---- flow/release_evidence.py and flow/archive_evidence.py

def test_release_evidence_runs_the_nonlatch_job_and_passes_both_options(tmp_path):
    ev = flow_module("release_evidence")
    exp = tmp_path / "expected.json"
    exp.write_text("{}")
    a = ev.parse_args(evidence_args(tmp_path, "--validation-commit", "HEAD", "--slow-mhz", "48",
                                    "--require-nonlatch-floor", "0.3", "--expect", str(exp)))
    jobs = {j.name: j for j in ev.plan_jobs(a, tmp_path / "view", tmp_path / "V", tmp_path / "work", base_env={})}
    job = jobs["nonlatch"]
    assert job.kind == "nonlatch" and job.argv[1].endswith("flow/sta_nonlatch.py")
    assert job.argv[2] == str(tmp_path / "view") and job.argv[job.argv.index("--period") + 1] == "20.832"
    cmd = ev.release_command(tmp_path / "run", tmp_path / "V", [("nonlatch", "/n/receipt.json")], slow_mhz=48,
                             nonlatch_floor=a.require_nonlatch_floor, expect=a.expect)
    assert cmd[cmd.index("--nonlatch-receipt") + 1] == "/n/receipt.json"
    assert cmd[cmd.index("--require-nonlatch-floor") + 1] == "0.3"
    assert cmd[cmd.index("--expect") + 1] == str(exp.resolve())
    # without the options: no job, no flags (other runs as before)
    b = ev.parse_args(evidence_args(tmp_path, "--validation-commit", "HEAD", "--slow-mhz", "48"))
    assert "nonlatch" not in {j.name for j in ev.plan_jobs(b, tmp_path / "view", tmp_path / "V", tmp_path / "w",
                                                             base_env={})}
    plain = ev.release_command(tmp_path / "run", tmp_path / "V", [], slow_mhz=48)
    assert "--require-nonlatch-floor" not in plain and "--expect" not in plain
    with pytest.raises(SystemExit):
        ev.parse_args(evidence_args(tmp_path, "--validation-commit", "HEAD", "--require-nonlatch-floor", "inf"))


def test_release_evidence_main_hands_both_options_to_the_release_check(tmp_path, monkeypatch):
    """main() itself, with no receipt jobs and the release check's process stubbed:
    the command it runs, and records in verdict.json, carries the floor and the
    expected file. Without them the release check prints "release identity: not
    compared", makes no floor check, and can still pass."""
    ev = flow_module("release_evidence")
    clone = git_clone(tmp_path)
    exp = tmp_path / "expected.json"
    exp.write_text("{}")
    ran, real = [], subprocess.run

    def fake_run(cmd, *args, **kw):
        if str(ev.FLOW / "release_check.py") not in map(str, cmd):
            return real(cmd, *args, **kw)                          # git
        ran.append([str(c) for c in cmd])
        return subprocess.CompletedProcess(cmd, 0, "RELEASE PASSED (SDF profile v2, slow target 48 MHz)\n", "")

    monkeypatch.setattr(ev.subprocess, "run", fake_run)
    monkeypatch.setattr(ev.signal, "signal", lambda *a: None)      # main() installs a SIGTERM handler
    monkeypatch.setattr(ev, "plan_jobs", lambda *a, **k: [])       # no simulators (the planned jobs: above)
    monkeypatch.setattr(ev, "make_view", lambda run, view: view)
    monkeypatch.setattr(ev.receipts, "candidate_files", lambda run: {})
    argv = evidence_args(tmp_path, "--validation-root", str(clone), "--no-archive", "--slow-mhz", "48")
    assert ev.main(argv + ["--work", str(tmp_path / "w1"), "--require-nonlatch-floor", "0.3",
                           "--expect", str(exp)]) == 0
    assert len(ran) == 1
    cmd = ran[0]
    assert cmd[1] == str(ev.FLOW / "release_check.py") and cmd[cmd.index("--slow-mhz") + 1] == "48"
    assert cmd[cmd.index("--require-nonlatch-floor") + 1] == "0.3"
    assert cmd[cmd.index("--expect") + 1] == str(exp.resolve())
    verdict = json.loads((tmp_path / "w1/verdict.json").read_text())
    assert verdict["release_check"] == cmd and verdict["exit"] == 0
    # a run without the options is checked as before
    assert ev.main(argv + ["--work", str(tmp_path / "w2")]) == 0
    assert len(ran) == 2 and "--require-nonlatch-floor" not in ran[1] and "--expect" not in ran[1]


def test_release_evidence_preflight_needs_the_expected_file(tmp_path):
    ev = flow_module("release_evidence")
    a = ev.parse_args(evidence_args(tmp_path, "--validation-commit", "HEAD", "--require-nonlatch-floor", "0.3",
                                    "--expect", str(tmp_path / "missing.json")))
    problems = ev.preflight(a, None, tmp_path / "work", tmp_path / "a.tar.gz")
    assert any("--expect file not found" in p for p in problems)
    (tmp_path / "missing.json").write_text("{}")
    assert not any("--expect" in p for p in ev.preflight(a, None, tmp_path / "work", tmp_path / "a.tar.gz"))


def test_release_evidence_collects_a_nonlatch_receipt(tmp_path, fake_sta):
    ev = flow_module("release_evidence")
    wt, rc, out, args = run(tmp_path, "--period", "20.832")
    job = ev.Job("nonlatch", "nonlatch", [], 1, out_parent=out.parent)
    log = f"non-latch STA outputs {out}\nreceipt: {out / 'receipt.json'}\n"
    assert ev.collect_receipt(job, log, set(), 0) == out / "receipt.json"
    with pytest.raises(ValueError, match="not a completed gl receipt"):
        ev.collect_receipt(ev.Job("gl", "gl", [], 1, out_parent=out.parent), log, set(), 0)


def test_archive_binds_the_nonlatch_receipt_and_its_reports(tmp_path, fake_sta):
    ar = flow_module("archive_evidence")
    rec = flow_module("release_receipts")
    wt, rc, out, args = run(tmp_path, "--period", "20.832")
    receipt = out / "receipt.json"
    others = []
    for kind in ("gl", "sdf", "precheck", "checks"):         # stand-ins: their own checks are tested elsewhere
        d = tmp_path / f"r-{kind}"
        d.mkdir()
        (d / "receipt.json").write_text("{}")
        (d / "inputs.json").write_text("{}")
        others.append(dict(job=kind, kind=kind, path=str(d / "receipt.json"), sha256=rec.digest(d / "receipt.json")))
    work = tmp_path / "work"
    (work / "logs").mkdir(parents=True)
    for name in ("release-evidence.log", "release-check.log", "verdict.json", "source.bundle"):
        (work / name).write_text(name)
    verdict = dict(run=str(wt), validation_root=str(tmp_path), work=str(work), runner_root=str(ROOT / "flow"),
                   exit=0, verdict="RELEASE PASSED (SDF profile v2, slow target 48 MHz)",
                   receipts=others + [dict(job="nonlatch", kind="nonlatch", path=str(receipt),
                                           sha256=rec.digest(receipt))])
    stub = lambda path, run, kind, validation: dict(worktree=str(run), validation_root=str(tmp_path),
                                                    runner_root=str(ROOT / "flow"), candidate={}, validation={},
                                                    runner={}, artifacts={})
    manifest = ar.Manifest("/")
    verified = ar.collect(verdict, manifest, verify=stub)
    rel = lambda p: str(Path(p).resolve().relative_to("/"))
    assert [v["kind"] for v in verified][-1] == "nonlatch"
    for name in ("receipt.json", "nom_slow_1p08V_125C.rpt", "nom_typ_1p20V_25C.rpt", "nom_fast_1p32V_m40C.rpt"):
        assert rel(out / name) in manifest.files
    assert rel(wt / "runs/wokwi/final/nl/design.nl.v") in manifest.files
    assert rel(ROOT / "flow/release_expected.py") in manifest.files
    (out / "nom_slow_1p08V_125C.rpt").chmod(0o644)
    (out / "nom_slow_1p08V_125C.rpt").write_text("edited\n")
    with pytest.raises(ValueError, match="changed after"):
        ar.collect(verdict, ar.Manifest("/"), verify=stub)


# ---- the GitHub workflows (read as text: CI's Python has no YAML package)

def steps(workflow, job):
    """Each step of one job of a workflow file, as its text."""
    text = (ROOT / ".github/workflows" / workflow).read_text()
    jobs = re.split(r"^  ([A-Za-z_]+):\n", text.split("\njobs:\n", 1)[1], flags=re.M)
    body = dict(zip(jobs[1::2], jobs[2::2]))[job]
    return re.split(r"^      - ", body.split("    steps:\n", 1)[1], flags=re.M)[1:]


def job(workflow, name):
    """One job of a workflow file, as its text before its steps."""
    text = (ROOT / ".github/workflows" / workflow).read_text()
    jobs = re.split(r"^  ([A-Za-z_]+):\n", text.split("\njobs:\n", 1)[1], flags=re.M)
    return dict(zip(jobs[1::2], jobs[2::2]))[name].split("    steps:\n", 1)[0]


def step(job_steps, name):
    """The step of a job (steps() above) with this name."""
    found = [s for s in job_steps if s.startswith(f"name: {name}\n")]
    assert len(found) == 1, name
    return found[0]


def step_names(job_steps):
    return [re.match(r"name: (.*)\n", s).group(1) for s in job_steps]


# gds.yaml builds in three jobs, each under GitHub's 6 h limit for a job: each
# job's LibreLane step range (LIBRELANE_PART) and whether it keeps runs/wokwi
BUILD_JOBS = ("gds_place", "gds_route", "gds")
PARTS = {
    "gds_place": ("--to OpenROAD.STAMidPNR-3", "0"),
    "gds_route": ("--from Project.DetailedRouting --to Project.DetailedRouting "
                  "--with-initial-state runs/wokwi/50-openroad-stamidpnr-3/state_out.json", "1"),
    "gds": ("--from Odb.RemoveRoutingObstructions "
            "--with-initial-state runs/wokwi/51-project-detailedrouting/state_out.json", "1"),
}
# the GDS action's steps after its build (action.yml at ACTION_SHA), then the hash check
ACTION_TAIL = ["Show build files (for debugging)", "Linter output", "Yosys warnings", "Routing summary",
               "Cell usage summary", "Publish build logs", "Prepare tt_submission artifact",
               "Publish tt_submission artifact", "Render PNG from GDS", "Upload gds_render (png) artifact",
               "Compare the build with the release's expected hashes"]


def test_the_gds_workflow_builds_in_three_chained_jobs_with_the_pinned_tools():
    text = (ROOT / ".github/workflows/gds.yaml").read_text()
    names = re.findall(r"^  ([A-Za-z_]+):\n", text.split("\njobs:\n", 1)[1], flags=re.M)
    assert names == [*BUILD_JOBS, "precheck", "gl_test", "viewer"]
    needs = {name: re.findall(r"^    needs: (\S+)\n", job("gds.yaml", name), re.M) for name in names}
    assert needs == {"gds_place": [], "gds_route": ["gds_place"], "gds": ["gds_route"],
                     "precheck": ["gds"], "gl_test": ["gds"], "viewer": ["gds"]}
    setups = []
    for name in BUILD_JOBS:
        head = job("gds.yaml", name)
        assert "    timeout-minutes: 350\n" in head
        s = steps("gds.yaml", name)
        n = step_names(s).index("Install LibreLane")
        setups.append(s[:n + 1])
        assert step_names(s)[:3] == ["checkout repo", "Check frozen submission configuration",
                                     "Set up environment variables"]
        assert ("cat << __EOF >> $GITHUB_ENV\n          PDK=ihp-sg13cmos5l\n          PDK_ROOT=$HOME/pdk\n"
                "          TT_ARGS=--ihp\n          __EOF\n") in s[2]
        assert "run: python3 flow/gen_config.py --preset submission --check\n" in s[1]
        assert (f"repository: TinyTapeout/tt-support-tools\n          ref: {TOOLS_SHA}\n          path: tt\n"
                in step(s, "Checkout tt-support-tools repo"))
        assert (f"repository: TinyTapeout/tt-gds-action\n          ref: {ACTION_SHA} # ihp-cmos5l\n"
                in step(s, "Checkout tt-gds-action"))
        assert "run: ./tt-gds-action/install_sg13cmos5l.sh\n" in step(s, "Install ihp-sg13cmos5l PDK")
        assert "run: pip install librelane==3.1.0.dev3\n" in step(s, "Install LibreLane")
        assert "python-version: '3.11'\n" in step(s, "Setup python")
    assert setups[0] == setups[1] == setups[2]                 # the same setup in each job
    assert f"tools-ref: {TOOLS_SHA}\n" in steps("gds.yaml", "precheck")[0]
    pins = json.loads((ROOT / "flow/release_pins.json").read_text())
    assert pins["tt_support_tools"] == TOOLS_SHA


def test_each_build_job_runs_tt_tools_harden_on_its_part_of_the_flow():
    make = []
    for name in BUILD_JOBS:
        s = step(steps("gds.yaml", name), "Make GDS with LibreLane")
        part, keep = PARTS[name]
        env = f"        env:\n          LIBRELANE_PART: {part}\n          KEEP_RUN_DIR: {keep}\n"
        assert env in s
        make.append(s.replace(env, ""))
    assert make[0] == make[1] == make[2]                       # one script, apart from its env
    assert 'sys.argv = ["tt/tt_tool.py", "--harden", *os.environ["TT_ARGS"].split()]' in make[0]


def harden_script():
    s = step(steps("gds.yaml", "gds"), "Make GDS with LibreLane")
    body = s.split("python3 - <<'EOF'\n", 1)[1].split("\n          EOF\n", 1)[0]
    return "\n".join(l[10:] for l in body.splitlines()) + "\n"


# A stand-in for tt-support-tools' tt_tool.py --harden (Project.harden at TOOLS_SHA): it
# clears runs/wokwi, runs LibreLane through the shell with the command it builds under CI,
# then another command
FAKE_TT_TOOL = """import os, shutil, subprocess, sys
if __name__ == "__main__":
    assert sys.argv[1:] == ["--harden", "--ihp"], sys.argv
    shutil.rmtree("runs/wokwi", ignore_errors=True)
    os.makedirs("runs/wokwi", exist_ok=True)
    cmd = ('python -m librelane --pdk-root "$PDK_ROOT" --docker-no-tty --dockerized --pdk-root "$PDK_ROOT" '
           '--pdk ihp-sg13cmos5l --manual-pdk --run-tag wokwi --force-run-dir runs/wokwi --hide-progress-bar '
           'src/config_merged.json')
    if subprocess.run(cmd, shell=True, env=os.environ.copy()).returncode != 0:
        sys.exit(1)
    subprocess.run(["sh", "-c", "echo other >> runs/wokwi/other"], check=True)
"""


@pytest.mark.parametrize("name", BUILD_JOBS)
def test_the_harden_script_adds_the_part_and_keeps_a_resumed_run(tmp_path, name):
    (tmp_path / "tt").mkdir()
    (tmp_path / "tt/tt_tool.py").write_text(FAKE_TT_TOOL)
    (tmp_path / "bin").mkdir()
    fake = tmp_path / "bin/python"                             # `python -m librelane` records its arguments
    fake.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > runs/wokwi/librelane.argv\n')
    fake.chmod(0o755)
    (tmp_path / "runs/wokwi").mkdir(parents=True)
    (tmp_path / "runs/wokwi/50-openroad-stamidpnr-3").mkdir()
    part, keep = PARTS[name]
    env = {"PATH": f"{tmp_path / 'bin'}:/usr/bin:/bin", "LIBRELANE_PART": part, "KEEP_RUN_DIR": keep,
           "TT_ARGS": "--ihp", "PDK_ROOT": "/pdk"}
    r = subprocess.run([sys.executable, "-c", harden_script()], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    argv = (tmp_path / "runs/wokwi/librelane.argv").read_text().split("\n")[:-1]
    assert argv == ["-m", "librelane", "--pdk-root", "/pdk", "--docker-no-tty", "--dockerized", "--pdk-root", "/pdk",
                    "--pdk", "ihp-sg13cmos5l", "--manual-pdk", "--run-tag", "wokwi", "--force-run-dir", "runs/wokwi",
                    "--hide-progress-bar", *part.split(), "src/config_merged.json"]
    assert "LibreLane command: python -m librelane " in r.stdout
    assert (tmp_path / "runs/wokwi/50-openroad-stamidpnr-3").exists() == (keep == "1")
    assert (tmp_path / "runs/wokwi/other").read_text() == "other\n"
    (tmp_path / "bin/python").write_text("#!/bin/sh\nexit 3\n")  # a failed LibreLane fails the step
    assert subprocess.run([sys.executable, "-c", harden_script()], cwd=tmp_path, env=env,
                          capture_output=True).returncode == 1


def uploads(job_steps):
    return [re.search(r"^          name: (\S+)$", s, re.M).group(1) for s in job_steps
            if "uses: actions/upload-artifact@v7\n" in s]


def downloads(job_steps):
    return [re.search(r"^          name: (\S+)$", s, re.M).group(1) for s in job_steps
            if "uses: actions/download-artifact@v7\n" in s]


def test_the_run_directory_goes_from_job_to_job_and_gds_ends_as_the_action_did():
    place, route, gds = (steps("gds.yaml", name) for name in BUILD_JOBS)
    assert (uploads(place), downloads(place)) == (["gds_run_placed", "GDS_logs_gds_place"], [])
    assert (uploads(route), downloads(route)) == (["gds_run_routed", "GDS_logs_gds_route"], ["gds_run_placed"])
    assert (uploads(gds), downloads(gds)) == (["GDS_logs", "tt_submission", "gds_render"], ["gds_run_routed"])
    pack = ("tar --zstd --format=posix -cf gds-run.tar.zst runs/wokwi\n"
            "          sha256sum src/user_config.json src/config_merged.json > gds-run-config.sha256\n")
    unpack = ("sha256sum -c gds-run-config.sha256\n          tar --zstd -xf gds-run.tar.zst\n"
              "          rm gds-run.tar.zst gds-run-config.sha256\n          rm -rf runs/wokwi/final\n")
    for s, stop in ((place, "50-openroad-stamidpnr-3"), (route, "51-project-detailedrouting")):
        names = step_names(s)
        assert pack in s[names.index("Make GDS with LibreLane") + 2]
        assert f"test -f runs/wokwi/{stop}/state_out.json\n" in s[names.index("Make GDS with LibreLane") + 1]
        assert "        if: failure()\n" in step(s, "Publish build logs of a failed part")
    for s in (route, gds):
        names = step_names(s)
        assert unpack in s[names.index("Make GDS with LibreLane") - 1]
        assert names.index("Install LibreLane") + 1 == names.index("Make GDS with LibreLane") - 2
    names = step_names(gds)
    assert names[names.index("Make GDS with LibreLane") + 2:] == ACTION_TAIL
    assert "test -f runs/wokwi/52-odb-removeroutingobstructions/state_out.json" in gds[names.index("Make GDS with LibreLane") + 1]
    assert re.search(r"^        run: python3 flow/release_expected.py \. evidence/8x4-release-expected.json$",
                     gds[-1], re.M)
    assert "          name: GDS_logs\n          path: |\n            src/*\n            runs/wokwi/*\n" in step(gds, "Publish build logs")
    assert "        if: success() || failure()\n" in step(gds, "Publish build logs")


def test_every_workflow_uses_the_action_by_the_pinned_commit():
    # gds.yaml's three uses and three checkouts (above), docs.yaml's and fpga.yaml's: none by branch or tag
    found = {}
    for path in sorted((ROOT / ".github/workflows").glob("*.y*ml")):
        text = "\n".join(l for l in path.read_text().splitlines() if not l.lstrip().startswith("#"))
        uses = re.findall(rf"uses: TinyTapeout/tt-gds-action(?:/[\w/]+)?@{ACTION_SHA} # ihp-cmos5l\n", text + "\n")
        checkouts = re.findall(rf"repository: TinyTapeout/tt-gds-action\n +ref: {ACTION_SHA} # ihp-cmos5l\n", text)
        assert len(re.findall("TinyTapeout/tt-gds-action", text)) == len(uses) + len(checkouts), path.name
        found[path.name] = (len(uses), len(checkouts))
    assert found == {"docs.yaml": (1, 0), "fpga.yaml": (1, 0), "gds.yaml": (3, 3), "test.yaml": (0, 0)}
    docs = steps("docs.yaml", "docs")
    build = next(s for s in docs if s.startswith("name: Build docs\n"))
    assert f"uses: TinyTapeout/tt-gds-action/docs@{ACTION_SHA} # ihp-cmos5l\n" in build
    assert f"        with:\n          tools-ref: {TOOLS_SHA}\n" in build


def test_harden_result_names_the_generator_options_of_an_overridden_run():
    """flow/harden.sh's result.txt label line, read as text and expanded by bash
    alone (nothing else of the script runs): a run with generator options says
    "with options ..."; a run without reads as before."""
    lines = [l for l in (ROOT / "flow/harden.sh").read_text().splitlines() if "preset $PRESET" in l]
    assert lines == ['  echo "tiles $TILE, cores $NC, preset $PRESET${*:+ with options $*}, '
                     'FIFO_LOG2 ${FIFO_LOG2:-3}, STOP ${STOP:-none}"']
    env = {"PATH": "/usr/bin:/bin", "TILE": "8x4", "NC": "3", "PRESET": "submission"}
    label = lambda *options: subprocess.run(["bash", "-uc", lines[0], "harden.sh", *options], env=env,
                                            capture_output=True, text=True, check=True).stdout
    assert label() == "tiles 8x4, cores 3, preset submission, FIFO_LOG2 3, STOP none\n"
    assert label("--density", "47") == ("tiles 8x4, cores 3, preset submission with options --density 47, "
                                        "FIFO_LOG2 3, STOP none\n")


def test_the_test_workflow_runs_the_tcl_tests_and_lists_skips():
    wf = steps("test.yaml", "test")
    assert "fetch-depth: 0\n" in wf[0]                 # test_clean_tree clones this repository
    assert any(re.search(r"^        run: .*apt-get install -y .*\blibtcl8\.6 libtk8\.6\b", s, re.M) for s in wf)
    model = next(s for s in wf if "python -m pytest" in s)
    assert "tkinter.Tcl()" in model and "pytest -q -rs test/model" in model
    assert not any("tapeout" in s.splitlines()[0] for s in wf)
