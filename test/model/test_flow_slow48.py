"""A release at 48 MHz at the slow corner (50 MHz typical and fast) needs a fresh
STA of the run at that period, bound by a receipt; slack is never extrapolated.

flow/sta_rerun.py re-runs the run's own STAPostPNR step with only CLOCK_PERIOD
changed. Here a fake `docker` stands in for the LibreLane image."""
import json
import os
from pathlib import Path
import shutil
import stat
import sys

import pytest

from test_release_evidence import candidate, complete_receipts, module

SLOW = "nom_slow_1p08V_125C"
CORNERS = ("nom_typ_1p20V_25C", SLOW, "nom_fast_1p32V_m40C")
STEP = "99-openroad-stapostpnr"


def sta_step(wt, *, nl_text=None):
    """The run's final STA step: config and input state, as LibreLane writes them."""
    run = wt / "runs/wokwi"
    files = {"59-fill/design.nl.v": nl_text or (run / "final/nl/design.nl.v").read_text(),
             "59-fill/design.sdc": "fixture sdc\n", "60-odb/design.odb": "fixture odb\n",
             "61-rcx/nom/design.nom.spef": "fixture spef\n", "final/spef/nom/design.nom.spef": "fixture spef\n"}
    for name, text in files.items():
        (run / name).parent.mkdir(parents=True, exist_ok=True)
        (run / name).write_text(text)
    (wt / "src/protoemu.sdc").write_text("set period $::env(CLOCK_PERIOD)\n")
    (wt / "src/timing_checks.tcl").write_text("# fixture\n")
    pdk = wt.parent / "pdk"
    pdk.mkdir(exist_ok=True)
    state = {"nl": str(run / "59-fill/design.nl.v"), "sdc": str(run / "59-fill/design.sdc"),
             "odb": str(run / "60-odb/design.odb"), "spef": {"nom_*": str(run / "61-rcx/nom/design.nom.spef")},
             "metrics": {}}
    config = {"meta": {"librelane_version": "3.1.0.dev3", "step": "OpenROAD.STAPostPNR"}, "CLOCK_PERIOD": 20,
              "PDK_ROOT": str(pdk), "SIGNOFF_SDC_FILE": str(wt / "src/protoemu.sdc"),
              "STA_EXTRA_CORNER_TCL_FILE": str(wt / "src/timing_checks.tcl")}
    (run / STEP / "state_in.json").write_text(json.dumps(state))
    (run / STEP / "config.json").write_text(json.dumps(config))
    # the run's own STA at 20 ns: the slow corner misses (base on 8x4: -0.643 ns, 90 endpoints)
    p = run / "final/metrics.json"
    m = json.loads(p.read_text())
    m[f"timing__setup__ws__corner:{SLOW}"] = -0.643
    m[f"timing__setup_vio__count__corner:{SLOW}"] = 90
    p.write_text(json.dumps(m))
    return wt


FAKE_DOCKER = r'''#!PYTHON
"""Fake `docker run ... librelane.steps run -c CONFIG -i STATE -o OUT`: writes the
step's outputs, with slow-corner metrics from FAKE_SLOW_WS / FAKE_SLOW_VIO."""
import json, os, sys
args = sys.argv[1:]
assert args[:2] == ["run", "--rm"] and "--network" in args and "librelane.steps" in args
with open(os.environ["FAKE_DOCKER_LOG"], "a") as f:
    f.write(json.dumps(args) + "\n")
config = json.load(open(args[args.index("-c") + 1]))
json.load(open(args[args.index("-i") + 1]))
out = args[args.index("-o") + 1]
period = config["CLOCK_PERIOD"]
for corner in ("nom_typ_1p20V_25C", "nom_slow_1p08V_125C", "nom_fast_1p32V_m40C"):
    os.makedirs(os.path.join(out, corner))
    slow = corner.startswith("nom_slow")
    ws = float(os.environ["FAKE_SLOW_WS"]) if slow else 0.0
    vio = int(os.environ["FAKE_SLOW_VIO"]) if slow else 0
    lines = [f"%OL_METRIC_F timing__hold__ws__corner:{corner} 0.36",
             f"%OL_METRIC_F timing__setup__ws__corner:{corner} {ws}",
             f"%OL_METRIC_I timing__hold_vio__count__corner:{corner} 0",
             f"%OL_METRIC_I timing__setup_vio__count__corner:{corner} {vio}"]
    if not (slow and os.environ.get("FAKE_DROP_METRIC")):
        open(os.path.join(out, corner, "sta.log"), "w").write("\n".join(lines) + "\n")
    else:
        open(os.path.join(out, corner, "sta.log"), "w").write("\n".join(lines[:1]) + "\n")
    open(os.path.join(out, corner, "clock.rpt"), "w").write(f"Clock: clk\nSources: clk\nPeriod: {period:f}\n")
open(os.path.join(out, "state_out.json"), "w").write("{}\n")
sys.exit(int(os.environ.get("FAKE_DOCKER_RC", "0")))
'''


@pytest.fixture
def fake_docker(tmp_path, monkeypatch):
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(FAKE_DOCKER.replace("PYTHON", sys.executable))
    docker.chmod(docker.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_DOCKER_LOG", str(tmp_path / "docker.log"))
    monkeypatch.setenv("FAKE_SLOW_WS", "0.0")
    monkeypatch.setenv("FAKE_SLOW_VIO", "0")
    return tmp_path / "docker.log"


def rerun(wt, period, out_parent, capsys=None):
    rc = module("sta_rerun").main([str(wt), str(period), "--out-parent", str(out_parent)])
    outs = sorted(Path(out_parent).glob("sta.*"))
    return rc, outs[-1]


def test_48mhz_slow_release_needs_the_fresh_sta(tmp_path, fake_docker):
    wt = sta_step(candidate(tmp_path))
    rel = module("release_check")
    args = complete_receipts(wt)
    assert rel.main(args) == 1                           # 50 MHz: slow -0.643 ns at 20 ns
    assert rel.main(args + ["--slow-mhz", "48"]) == 1    # no fresh STA: never extrapolated
    rc, out = rerun(wt, 20.833, tmp_path / "sta-out")
    assert rc == 0 and (out / "receipt.json").is_file()
    receipt = str(out / "receipt.json")
    assert rel.main(args + ["--slow-mhz", "48", "--slow-sta-receipt", receipt]) == 0
    # the receipt's 20.833 ns does not establish 50 MHz, nor 49
    assert rel.main(args + ["--slow-sta-receipt", receipt]) == 1
    assert rel.main(args + ["--slow-mhz", "49", "--slow-sta-receipt", receipt]) == 1
    # the docker call: the run read-only, no network, only CLOCK_PERIOD changed
    call = json.loads(fake_docker.read_text().splitlines()[-1])
    assert f"{wt}:{wt}:ro" in call and call[call.index("--network") + 1] == "none"
    config = json.loads((out / "config.json").read_text())
    original = json.loads((wt / "runs/wokwi" / STEP / "config.json").read_text())
    assert config == dict(original, CLOCK_PERIOD=20.833)


def test_fresh_sta_receipt_through_the_release_view(tmp_path, fake_docker):
    wt = sta_step(candidate(tmp_path))
    view = module("release_evidence").make_view(wt, tmp_path / "view")
    rc, out = rerun(view, 20.833, tmp_path / "sta-out")
    assert rc == 0
    data = json.loads((out / "receipt.json").read_text())
    assert data["candidate"] == module("release_receipts").candidate_files(wt)
    assert "runs/wokwi/61-rcx/nom/design.nom.spef" in data["plan"]["inputs"]
    call = json.loads(fake_docker.read_text().splitlines()[-1])
    assert f"{wt}:{wt}:ro" in call                        # the real run, not the view's symlinks


@pytest.mark.parametrize("fault", ["slow-violators", "slow-negative", "missing-metric", "step-fails"])
def test_failing_or_incomplete_fresh_sta_cannot_release(tmp_path, fake_docker, monkeypatch, fault):
    wt = sta_step(candidate(tmp_path))
    env = {"slow-violators": ("FAKE_SLOW_VIO", "3"), "slow-negative": ("FAKE_SLOW_WS", "-0.01"),
           "missing-metric": ("FAKE_DROP_METRIC", "1"), "step-fails": ("FAKE_DOCKER_RC", "1")}[fault]
    monkeypatch.setenv(*env)
    rc, out = rerun(wt, 20.833, tmp_path / "sta-out")
    args = complete_receipts(wt) + ["--slow-mhz", "48"]
    if fault.startswith("slow-"):
        assert rc == 0                                   # a complete STA; the gate judges it
        assert module("release_check").main(args + ["--slow-sta-receipt", str(out / "receipt.json")]) == 1
    else:
        assert rc != 0 and not (out / "receipt.json").exists()


def test_slower_rerun_does_not_establish_48mhz(tmp_path, fake_docker):
    wt = sta_step(candidate(tmp_path))
    rc, out = rerun(wt, 21.0, tmp_path / "sta-out")
    assert rc == 0
    args = complete_receipts(wt) + ["--slow-mhz", "48", "--slow-sta-receipt", str(out / "receipt.json")]
    assert module("release_check").main(args) == 1


@pytest.mark.parametrize("fault", ["sta-log", "clock-report", "candidate-netlist", "kind"])
def test_changed_evidence_invalidates_the_sta_receipt(tmp_path, fake_docker, fault):
    wt = sta_step(candidate(tmp_path))
    rc, out = rerun(wt, 20.833, tmp_path / "sta-out")
    assert rc == 0
    rec = module("release_receipts")
    rec.verify(out / "receipt.json", wt, "sta")
    if fault == "sta-log":
        with (out / "sta" / SLOW / "sta.log").open("a") as f:
            f.write("%OL_METRIC_F timing__setup__ws__corner:nom_slow_1p08V_125C 1.0\n")
    elif fault == "clock-report":
        (out / "sta" / SLOW / "clock.rpt").write_text("Clock: clk\nPeriod: 20.000000\n")
    elif fault == "candidate-netlist":
        (wt / "runs/wokwi/final/nl/design.nl.v").write_text("module other; endmodule\n")
    with pytest.raises(ValueError):
        rec.verify(out / "receipt.json", wt, "gl" if fault == "kind" else "sta")


@pytest.mark.parametrize("fault", ["spef-changed", "config-changed", "other-period"])
def test_sta_receipt_rejects_changes_during_the_run(tmp_path, fault):
    wt = sta_step(candidate(tmp_path))
    rec = module("release_receipts")
    out = tmp_path / "sta.manual"
    plan = dict(period_ns=20.833, step=STEP, image="fixture")
    rec.begin(wt, out, "sta", wt, plan)
    config = json.loads((wt / "runs/wokwi" / STEP / "config.json").read_text())
    config["CLOCK_PERIOD"] = 20.0 if fault == "other-period" else 20.833
    if fault == "config-changed":
        config["STA_EXTRA_CORNER_TCL_FILE"] = ""
    (out / "config.json").write_text(json.dumps(config))
    (out / "status.json").write_text('{"returncode":0}')
    (out / "run.log").write_text("fixture\n")
    for corner in CORNERS:
        d = out / "sta" / corner
        d.mkdir(parents=True)
        (d / "sta.log").write_text("".join(f"%OL_METRIC_{'I' if 'count' in k else 'F'} {k}__corner:{corner} 0\n"
                                           for k in rec.STA_METRICS))
        (d / "clock.rpt").write_text("Clock: clk\nPeriod: 20.833000\n")
    (out / "sta/state_out.json").write_text("{}")
    if fault == "spef-changed":
        (wt / "runs/wokwi/61-rcx/nom/design.nom.spef").write_text("another extraction\n")
    with pytest.raises(ValueError):
        rec.finish(out)


def test_sta_rerun_refuses_a_step_that_did_not_read_the_final_netlist(tmp_path, fake_docker):
    wt = sta_step(candidate(tmp_path), nl_text="module older; endmodule\n")
    rc, out = rerun(wt, 20.833, tmp_path / "sta-out")
    assert rc == 2 and not fake_docker.exists() and not (out / "receipt.json").exists()


def test_release_evidence_plans_the_48mhz_release(tmp_path):
    from test_flow_tools import evidence_args, flow_module
    ev = flow_module("release_evidence")
    assert ev.slow_period(48) == 20.833 and ev.slow_period(50) == 20.0 and ev.slow_period(46) == 21.739
    assert ev.slow_period(48) <= 1000 / 48
    with pytest.raises(SystemExit):
        ev.parse_args(evidence_args(tmp_path, "--validation-commit", "HEAD", "--slow-mhz", "51"))
    a = ev.parse_args(evidence_args(tmp_path, "--validation-commit", "HEAD", "--slow-mhz", "48"))
    inherited = dict(PATH="/usr/bin", SLOW_CLK_NS="23", HOME="/home/x")
    jobs = ev.plan_jobs(a, tmp_path / "view", tmp_path / "V", tmp_path / "work", base_env=inherited)
    by = {j.name: j for j in jobs}
    assert sorted(by) == ["gl", "precheck", "sdf_auto_fast", "sdf_auto_slow", "sdf_auto_typ",
                          "sdf_standard", "sdf_v2", "sta_slow"]
    assert by["sdf_standard"].env["SLOW_CLK_NS"] == "20.833" and by["sdf_v2"].env["SLOW_CLK_NS"] == "20.833"
    assert all("SLOW_CLK_NS" not in j.env for n, j in by.items() if n not in ("sdf_standard", "sdf_v2"))
    sta = by["sta_slow"]
    assert sta.kind == "sta" and sta.weight == 1 and sta.argv[2:4] == [str(tmp_path / "view"), "20.833"]
    cmd = ev.release_command(tmp_path / "run", tmp_path / "V", [("gl", "g"), ("sta", "s")], slow_mhz=48)
    assert cmd[cmd.index("--slow-mhz") + 1] == "48" and cmd[cmd.index("--slow-sta-receipt") + 1] == "s"
    assert "--slow-mhz" not in ev.release_command(tmp_path / "run", tmp_path / "V", [])
    # 50 MHz: the seven jobs as before
    a50 = ev.parse_args(evidence_args(tmp_path, "--validation-commit", "HEAD"))
    assert "sta_slow" not in {j.name for j in ev.plan_jobs(a50, tmp_path / "view", tmp_path / "V", tmp_path / "work",
                                                          base_env=inherited)}


def test_sdf_slow_cases_at_the_48mhz_period_count_for_48_not_50(tmp_path):
    from test_release_evidence import sdf_output
    wt = candidate(tmp_path)
    plan = module("release_receipts").read_json(sdf_output(wt) / "inputs.json")["plan"]
    for case in plan:
        if case["corner"] == "slow" and case["expect"] == "pass" and case["period_ns"] == 20:
            case["period_ns"] = 20.833
    rel = module("release_check")
    assert rel.sdf_coverage([{"plan": plan}], 48, "v2") == []
    assert any("SDF slow" in e for e in rel.sdf_coverage([{"plan": plan}], 50, "v2"))
