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

from test_release_evidence import candidate, checks_output, complete_receipts, module

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
    # LibreLane records its version in the run's resolved.json too (a candidate file)
    p = run / "resolved.json"
    p.write_text(json.dumps(dict(json.loads(p.read_text()), meta={"librelane_version": "3.1.0.dev3"})))
    return wt


FAKE_DOCKER = r'''#!PYTHON
"""Fake docker. `image inspect --format {{.Id}} IMAGE`: a made-up ID from the
name (fails with FAKE_IMAGE_MISSING). `run ... ID python3 -c PROBE`: prints
FAKE_LIBRELANE_VERSION. `run ... librelane.steps run -c CONFIG -i STATE -o OUT`:
writes the step's outputs, with slow-corner metrics from FAKE_SLOW_WS / FAKE_SLOW_VIO."""
import hashlib, json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_DOCKER_LOG"], "a") as f:
    f.write(json.dumps(args) + "\n")
if args[:3] == ["image", "inspect", "--format"]:
    if os.environ.get("FAKE_IMAGE_MISSING"):
        sys.exit("Error: No such image: " + args[4])
    print("sha256:" + hashlib.sha256(args[4].encode()).hexdigest())
    sys.exit(0)
assert args[:2] == ["run", "--rm"] and args[args.index("--network") + 1] == "none"
if "librelane.steps" not in args:
    assert args[-3:-1] == ["python3", "-c"] and args[-4].startswith("sha256:")
    print(os.environ.get("FAKE_LIBRELANE_VERSION", "3.1.0.dev3"))
    sys.exit(0)
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
    # flow/check_rerun.py's report Tcl: its report, from FAKE_CHECK_DIR/<tcl>.<corner>.rpt
    tcl = os.path.basename(config.get("STA_EXTRA_CORNER_TCL_FILE", ""))
    if tcl in ("check_types.tcl", "pulse_checks.tcl"):
        text = open(os.path.join(os.environ["FAKE_CHECK_DIR"], f"{tcl}.{corner}.rpt")).read()
        open(os.path.join(out, corner, tcl.replace(".tcl", ".rpt")), "w").write(text)
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


def rerun(wt, period, out_parent, *extra):
    rc = module("sta_rerun").main([str(wt), str(period), "--out-parent", str(out_parent), *extra])
    outs = sorted(Path(out_parent).glob("sta.*"))
    return rc, (outs[-1] if outs else None)


def fake_image_id(name):
    import hashlib
    return "sha256:" + hashlib.sha256(name.encode()).hexdigest()


DEFAULT_IMAGE = "ghcr.io/librelane/librelane:3.1.0.dev3"


def sta_calls(log):
    """The fake docker's STA step runs (not the image inspect or version probe)."""
    calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    return [c for c in calls if "librelane.steps" in c]


def manual_plan(period=20.832):
    return dict(period_ns=period, step=STEP, image=DEFAULT_IMAGE, image_id=fake_image_id(DEFAULT_IMAGE),
                librelane_version="3.1.0.dev3")


def manual_run_log(out, image_id=None):
    image_id = image_id or fake_image_id(DEFAULT_IMAGE)
    (out / "run.log").write_text(f"docker run --rm --network none -w {out} {image_id} python3 -m librelane.steps run "
                                 f"--id OpenROAD.STAPostPNR -c {out}/config.json -i state_in.json -o {out}/sta\n")


def reseal(out, **plan):
    """A forged receipt: its plan edited and its artifact digests recomputed
    (what verify's own checks, not the digests, must catch)."""
    rec = module("release_receipts")
    data = json.loads((out / "receipt.json").read_text())
    data["plan"].update(plan)
    data["artifacts"] = {name: rec.digest(out / name) for name in data["artifacts"]}
    (out / "receipt.json").write_text(json.dumps(data))


def test_48mhz_slow_release_needs_the_fresh_sta(tmp_path, fake_docker):
    wt = sta_step(candidate(tmp_path))
    rel = module("release_check")
    args = complete_receipts(wt)
    assert rel.main(args) == 1                           # 50 MHz: slow -0.643 ns at 20 ns
    assert rel.main(args + ["--slow-mhz", "48"]) == 1    # no fresh STA: never extrapolated
    rc, out = rerun(wt, 20.832, tmp_path / "sta-out")
    assert rc == 0 and (out / "receipt.json").is_file()
    receipt = str(out / "receipt.json")
    assert rel.main(args + ["--slow-mhz", "48", "--slow-sta-receipt", receipt]) == 0
    # the receipt's 20.832 ns does not establish 50 MHz, nor 49
    assert rel.main(args + ["--slow-sta-receipt", receipt]) == 1
    assert rel.main(args + ["--slow-mhz", "49", "--slow-sta-receipt", receipt]) == 1
    # the docker call: the run read-only, no network, only CLOCK_PERIOD changed
    call = json.loads(fake_docker.read_text().splitlines()[-1])
    assert f"{wt}:{wt}:ro" in call and call[call.index("--network") + 1] == "none"
    # the image: the run's version's tag, run by the ID docker gave for it, and recorded
    plan = json.loads((out / "receipt.json").read_text())["plan"]
    assert plan["image"] == DEFAULT_IMAGE and plan["image_id"] == fake_image_id(DEFAULT_IMAGE)
    assert plan["librelane_version"] == "3.1.0.dev3"
    assert sta_calls(fake_docker) == [call] and call[call.index("python3") - 1] == plan["image_id"]
    config = json.loads((out / "config.json").read_text())
    original = json.loads((wt / "runs/wokwi" / STEP / "config.json").read_text())
    assert config == dict(original, CLOCK_PERIOD=20.832)
    # 097b: the re-run STA times the run's signoff SDC, never the PnR (transition) SDC
    assert config["SIGNOFF_SDC_FILE"] == original["SIGNOFF_SDC_FILE"] and \
        "PNR_SDC_FILE" not in (Path(__file__).resolve().parents[2] / "flow/sta_rerun.py").read_text()


def test_fresh_sta_receipt_through_the_release_view(tmp_path, fake_docker):
    wt = sta_step(candidate(tmp_path))
    view = module("release_evidence").make_view(wt, tmp_path / "view")
    rc, out = rerun(view, 20.832, tmp_path / "sta-out")
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
    rc, out = rerun(wt, 20.832, tmp_path / "sta-out")
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
    rc, out = rerun(wt, 20.832, tmp_path / "sta-out")
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
    plan = manual_plan()
    rec.begin(wt, out, "sta", wt, plan)
    config = json.loads((wt / "runs/wokwi" / STEP / "config.json").read_text())
    config["CLOCK_PERIOD"] = 20.0 if fault == "other-period" else 20.832
    if fault == "config-changed":
        config["STA_EXTRA_CORNER_TCL_FILE"] = ""
    (out / "config.json").write_text(json.dumps(config))
    (out / "status.json").write_text('{"returncode":0}')
    manual_run_log(out)
    for corner in CORNERS:
        d = out / "sta" / corner
        d.mkdir(parents=True)
        (d / "sta.log").write_text("".join(f"%OL_METRIC_{'I' if 'count' in k else 'F'} {k}__corner:{corner} 0\n"
                                           for k in rec.STA_METRICS))
        (d / "clock.rpt").write_text("Clock: clk\nPeriod: 20.832000\n")
    (out / "sta/state_out.json").write_text("{}")
    if fault == "spef-changed":
        (wt / "runs/wokwi/61-rcx/nom/design.nom.spef").write_text("another extraction\n")
    with pytest.raises(ValueError):
        rec.finish(out)


@pytest.mark.parametrize("printed,ok", [("20.832001", True), ("20.831999", True), ("20.832000", True),
                                        ("20.832010", False), ("20.833000", False)])
def test_clock_report_in_single_precision(tmp_path, printed, ok):
    # OpenSTA prints 20.832 ns as 20.832001 (the run apbase's rerun)
    wt = sta_step(candidate(tmp_path))
    rec = module("release_receipts")
    out = tmp_path / "sta.manual"
    rec.begin(wt, out, "sta", wt, manual_plan())
    config = json.loads((wt / "runs/wokwi" / STEP / "config.json").read_text())
    (out / "config.json").write_text(json.dumps(dict(config, CLOCK_PERIOD=20.832)))
    (out / "status.json").write_text('{"returncode":0}')
    manual_run_log(out)
    for corner in CORNERS:
        d = out / "sta" / corner
        d.mkdir(parents=True)
        (d / "sta.log").write_text("".join(f"%OL_METRIC_{'I' if 'count' in k else 'F'} {k}__corner:{corner} 0\n"
                                           for k in rec.STA_METRICS))
        (d / "clock.rpt").write_text(f"Clock: clk\nSources: clk\nPeriod: {printed}\n")
    (out / "sta/state_out.json").write_text("{}")
    if ok:
        assert rec.finish(out).is_file()
    else:
        with pytest.raises(ValueError):
            rec.finish(out)


def test_sta_rerun_refuses_a_step_that_did_not_read_the_final_netlist(tmp_path, fake_docker):
    wt = sta_step(candidate(tmp_path), nl_text="module older; endmodule\n")
    rc, out = rerun(wt, 20.832, tmp_path / "sta-out")
    assert rc == 2 and sta_calls(fake_docker) == [] and not (out / "receipt.json").exists()


def sta_release(tmp_path):
    """A run, its complete receipts and a fresh 48 MHz STA receipt that releases it."""
    wt = sta_step(candidate(tmp_path))
    args = complete_receipts(wt)
    rc, out = rerun(wt, 20.832, tmp_path / "sta-out")
    assert rc == 0
    args += ["--slow-mhz", "48", "--slow-sta-receipt", str(out / "receipt.json")]
    assert module("release_check").main(args) == 0
    return wt, out, args


def release_output(args, capsys):
    capsys.readouterr()
    rc = module("release_check").main(args)
    return rc, capsys.readouterr().out


# 080 finding 2: the verifier's four cases (both SPEF copies, the step's config.json,
# its state_in.json, the odb) passed the release after the receipt was written.
TAMPER = {
    "spef-both": ("61-rcx/nom/design.nom.spef", "final/spef/nom/design.nom.spef"),
    "spef-final": ("final/spef/nom/design.nom.spef",),
    "spef-extracted": ("61-rcx/nom/design.nom.spef",),
    "step-config": (f"{STEP}/config.json",),
    "step-config-bytes": (f"{STEP}/config.json",),
    "step-state": (f"{STEP}/state_in.json",),
    "odb": ("60-odb/design.odb",),
}


def tamper(wt, case):
    run = wt / "runs/wokwi"
    for name in TAMPER[case]:
        p = run / name
        if case == "step-config":          # a setting changed
            p.write_text(json.dumps(dict(json.loads(p.read_text()), PL_TARGET_DENSITY_PCT=44)))
        elif case == "step-config-bytes":  # the same settings, other bytes
            p.write_text(p.read_text() + "\n")
        elif case == "step-state":
            p.write_text(json.dumps(dict(json.loads(p.read_text()), metrics={"injected": 1})))
        else:
            p.write_text("another " + p.read_text())


# The checks receipt (092) binds the same STA inputs, and its refusal reads "checks
# STA inputs differ from the receipt's": only the STA receipt's own wording counts here.
STA_REFUSED = "the run's STA inputs: STA inputs differ from the receipt's"


@pytest.mark.parametrize("case", sorted(TAMPER))
def test_run_changed_after_the_sta_receipt_cannot_release(tmp_path, fake_docker, capsys, case):
    wt, out, args = sta_release(tmp_path)
    rec = module("release_receipts")
    assert rec.verify(out / "receipt.json", wt, "sta")["skipped"] == []
    tamper(wt, case)
    rc, text = release_output(args, capsys)
    assert rc == 1 and STA_REFUSED in text and "RELEASE FAILED" in text
    # A checks receipt made after the change agrees with the changed run. Then only the
    # STA receipt refuses: without its input comparison six of these seven cases would
    # release (step-config would still be refused, by the config comparison).
    checks = Path(args[args.index("--checks-receipt") + 1])
    shutil.rmtree(checks.parent)
    assert checks_output(wt) == checks and rec.verify(checks, wt, "checks")["skipped"] == []
    rc, text = release_output(args, capsys)
    assert rc == 1 and STA_REFUSED in text and "RELEASE FAILED" in text
    assert "checks STA inputs differ" not in text and "checks receipt verified" in text


def test_forged_receipt_config_is_compared_with_the_runs(tmp_path, fake_docker, capsys):
    # the receipt's own config edited and its digests recomputed: the inputs still
    # match, so only the step-config comparison can refuse it
    wt, out, args = sta_release(tmp_path)
    config = json.loads((out / "config.json").read_text())
    (out / "config.json").write_text(json.dumps(dict(config, PL_TARGET_DENSITY_PCT=44)))
    reseal(out)
    rc, text = release_output(args, capsys)
    assert rc == 1 and "STA step config differs from the run's beyond CLOCK_PERIOD" in text


def archive_shaped(wt, keep=()):
    """What an evidence archive keeps of the run: the candidate files, not the
    STA step's state and config, the odb, the step's netlist copy or the SPEFs."""
    run = wt / "runs/wokwi"
    for name in (f"{STEP}/state_in.json", f"{STEP}/config.json", "59-fill/design.nl.v", "59-fill/design.sdc",
                 "60-odb/design.odb", "61-rcx/nom/design.nom.spef", "final/spef/nom/design.nom.spef"):
        if name not in keep:
            (run / name).unlink()


def test_archive_without_the_sta_inputs_says_what_it_skipped(tmp_path, fake_docker, capsys):
    wt, out, args = sta_release(tmp_path)
    archive_shaped(wt)
    rc, text = release_output(args, capsys)
    assert rc == 0
    skipped = [line for line in text.splitlines() if line.startswith("slow-corner STA receipt, SKIPPED: ")]
    assert len(skipped) == 2
    assert "STA inputs not recomputed" in skipped[0] and "3 of 9 recorded input files checked" in skipped[0]
    assert "(the step netlist against final/nl)" in skipped[0] and "59-fill/design.nl.v" not in skipped[0]
    assert "runs/wokwi/60-odb/design.odb" in skipped[0] and "STA step config not compared" in skipped[1]
    verdict = [line for line in text.splitlines() if line.startswith("RELEASE PASSED")]
    # 2 of the slow-corner STA receipt and 2 of the clock/reset checks receipt; the
    # two power gates and the shipping build have no inputs in an archive (092)
    assert verdict == ["RELEASE PASSED (SDF profile v2, slow target 48 MHz, 4 STA RECEIPT CHECKS SKIPPED "
                       "(the run's STA inputs are absent), 3 RELEASE GATES SKIPPED)"]
    assert len(module("release_receipts").verify(out / "receipt.json", wt, "sta")["skipped"]) == 2


@pytest.mark.parametrize("case", ["archive-odb-changed", "state-removed", "config-removed"])
def test_partial_sta_inputs_are_still_checked(tmp_path, fake_docker, capsys, case):
    wt, out, args = sta_release(tmp_path)
    run = wt / "runs/wokwi"
    if case == "archive-odb-changed":       # archive-shaped, but an input it does hold differs
        archive_shaped(wt, keep=("60-odb/design.odb",))
        (run / "60-odb/design.odb").write_text("another odb\n")
    else:                                    # one of the step's two files gone: not an archive
        (run / STEP / ("state_in.json" if case == "state-removed" else "config.json")).unlink()
    rc, text = release_output(args, capsys)
    # no STA receipt check is skipped (an archive-shaped tree skips the power gates, 092)
    assert rc == 1 and not receipts_skipped(text) and "checks receipt, SKIPPED" not in text


@pytest.mark.parametrize("forgery", ["other-version", "other-image-ran", "no-image-id"])
def test_sta_receipt_binds_the_image_that_ran(tmp_path, fake_docker, capsys, forgery):
    wt, out, args = sta_release(tmp_path)
    if forgery == "other-version":
        reseal(out, librelane_version="3.0.0")
        expected = f"STA image {fake_image_id(DEFAULT_IMAGE)} has LibreLane 3.0.0, not the run's 3.1.0.dev3"
    elif forgery == "other-image-ran":
        log = out / "run.log"
        log.write_text(log.read_text().replace(fake_image_id(DEFAULT_IMAGE), fake_image_id("example/other")))
        reseal(out)
        expected = "the STA command did not run image"
    else:                                   # a receipt made before the image was recorded
        data = json.loads((out / "receipt.json").read_text())
        del data["plan"]["image_id"]
        (out / "receipt.json").write_text(json.dumps(data))
        expected = "records no image ID"
    rc, text = release_output(args, capsys)
    assert rc == 1 and expected in text and f"{out / 'receipt.json'}: " in text   # 082 f3: the receipt is named


# 082 finding 1: a live run whose step files are gone, or replaced, must not pass as an archive
@pytest.mark.parametrize("case", ["deleted", "step-directories", "step-dangling-link", "archive-odb-directory"])
def test_live_run_without_its_sta_inputs_cannot_release(tmp_path, fake_docker, capsys, case):
    wt, out, args = sta_release(tmp_path)
    run = wt / "runs/wokwi"
    if case == "deleted":                   # what an archive holds: passes with the notice, unless required
        archive_shaped(wt)
        rc, text = release_output(args, capsys)
        assert rc == 0 and receipts_skipped(text)
    elif case == "step-directories":        # the step files and the odb replaced by directories
        for name in (f"{STEP}/state_in.json", f"{STEP}/config.json", "60-odb/design.odb"):
            (run / name).unlink()
            (run / name).mkdir()
    elif case == "step-dangling-link":
        archive_shaped(wt)
        (run / STEP / "config.json").symlink_to(run / STEP / "elsewhere.json")
    else:                                   # archive-shaped, but a directory where the odb was
        archive_shaped(wt)
        (run / "60-odb/design.odb").mkdir()
    if case != "deleted":                   # not an archive: refused, without the flag too
        rc, text = release_output(args, capsys)
        assert rc == 1 and not receipts_skipped(text) and "RELEASE FAILED" in text
        if case == "archive-odb-directory":
            assert "STA inputs are not regular files: runs/wokwi/60-odb/design.odb" in text
    rc, text = release_output(args + ["--require-sta-inputs"], capsys)
    assert rc == 1 and "RELEASE PASSED" not in text
    if case == "deleted":
        assert "--require-sta-inputs: the run lacks the STA inputs its receipt records (2 checks" in text


def receipts_skipped(text):
    return [line for line in text.splitlines() if line.startswith("slow-corner STA receipt, SKIPPED: ")]


def test_require_sta_inputs_passes_a_complete_live_run(tmp_path, fake_docker):
    wt, out, args = sta_release(tmp_path)
    assert module("release_check").main(args + ["--require-sta-inputs"]) == 0


def test_release_evidence_requires_the_sta_inputs_and_refuses_a_skipped_verdict(tmp_path):
    ev = module("release_evidence")
    assert "--require-sta-inputs" in ev.release_command(tmp_path / "run", tmp_path / "V", [("sta", "s")], slow_mhz=48)
    passed = "RELEASE PASSED (SDF profile v2, slow target 48 MHz)"
    skipped = ("RELEASE PASSED (SDF profile v2, slow target 48 MHz, 2 STA RECEIPT CHECKS SKIPPED "
               "(the run's STA inputs are absent))")
    assert ev.verdict_failures(0, passed) == []
    assert ev.verdict_failures(1, "RELEASE FAILED:") == ["RELEASE FAILED:"]
    assert "skipped STA receipt checks" in ev.verdict_failures(0, skipped)[0]
    assert ev.verdict_failures(0, "RELEASE CHECK PRODUCED NO VERDICT")


def test_archive_refuses_a_verdict_or_receipt_with_skipped_checks(tmp_path):
    from test_flow_tools import evidence_bundle, flow_module
    ar = flow_module("archive_evidence")
    wt, verdict, paths = evidence_bundle(tmp_path)
    ar.collect(verdict, ar.Manifest("/"))
    rec = flow_module("release_receipts")
    with pytest.raises(ValueError, match="checks skipped on the run being archived"):
        ar.collect(verdict, ar.Manifest("/"), verify=lambda *a: dict(rec.verify(*a), skipped=["STA step config"]))
    verdict = dict(verdict, verdict="RELEASE PASSED (SDF profile v2, slow target 48 MHz, 2 STA RECEIPT CHECKS "
                                    "SKIPPED (the run's STA inputs are absent))")
    with pytest.raises(ValueError, match="skipped STA receipt checks"):
        ar.collect(verdict, ar.Manifest("/"))


# 082 finding 2: on an archive the receipt's config is not compared with the run's,
# so the version is bound to the run's resolved.json (a candidate file) as well
@pytest.mark.parametrize("shape", ["live", "archive"])
def test_forged_librelane_version_is_refused_against_resolved_json(tmp_path, fake_docker, capsys, shape):
    wt, out, args = sta_release(tmp_path)
    if shape == "archive":
        archive_shaped(wt)
    config = json.loads((out / "config.json").read_text())
    config["meta"]["librelane_version"] = "3.0.0"
    (out / "config.json").write_text(json.dumps(config))
    reseal(out, librelane_version="3.0.0")
    rc, text = release_output(args, capsys)
    assert rc == 1 and "has LibreLane 3.0.0, not the run's 3.1.0.dev3 (resolved.json)" in text


def test_archive_checks_the_step_netlist_against_final_nl(tmp_path, fake_docker, capsys):
    # 082 f3: begin required the step's netlist copy to equal final/nl, which an archive holds
    wt, out, args = sta_release(tmp_path)
    archive_shaped(wt)
    data = json.loads((out / "receipt.json").read_text())
    data["plan"]["inputs"]["runs/wokwi/59-fill/design.nl.v"] = "0" * 64
    (out / "receipt.json").write_text(json.dumps(data))
    rc, text = release_output(args, capsys)
    assert rc == 1 and "STA inputs differ from the receipt's: runs/wokwi/59-fill/design.nl.v" in text


@pytest.mark.parametrize("case", ["other-version", "missing-image"])
def test_sta_rerun_refuses_an_image_without_the_runs_librelane(tmp_path, fake_docker, monkeypatch, capsys, case):
    wt = sta_step(candidate(tmp_path))
    monkeypatch.setenv(*{"other-version": ("FAKE_LIBRELANE_VERSION", "3.0.0"),
                         "missing-image": ("FAKE_IMAGE_MISSING", "1")}[case])
    rc, out = rerun(wt, 20.832, tmp_path / "sta-out", "--image", "example/librelane:local")
    assert rc == 2 and out is None and sta_calls(fake_docker) == []
    text = capsys.readouterr().out
    assert "refused:" in text and ("has LibreLane 3.0.0; the run used 3.1.0.dev3" in text
                                   if case == "other-version" else "docker pull it first" in text)


def test_sta_rerun_accepts_another_image_with_the_runs_librelane(tmp_path, fake_docker):
    wt = sta_step(candidate(tmp_path))
    rc, out = rerun(wt, 20.832, tmp_path / "sta-out", "--image", "example/librelane:local")
    assert rc == 0
    plan = json.loads((out / "receipt.json").read_text())["plan"]
    assert plan["image"] == "example/librelane:local" and plan["image_id"] == fake_image_id("example/librelane:local")
    [call] = sta_calls(fake_docker)
    assert call[call.index("python3") - 1] == plan["image_id"]


def test_sta_plan_needs_an_image_id(tmp_path):
    wt = sta_step(candidate(tmp_path))
    rec = module("release_receipts")
    for bad in ("ghcr.io/librelane/librelane:3.1.0.dev3", "sha256:abc", None):
        with pytest.raises(ValueError, match="image's ID"):
            rec.begin(wt, tmp_path / "sta.bad", "sta", wt, dict(manual_plan(), image_id=bad))
    with pytest.raises(ValueError, match="needs period_ns"):
        plan = manual_plan()
        del plan["librelane_version"]
        rec.begin(wt, tmp_path / "sta.bad", "sta", wt, plan)


def test_a_repeated_sta_metric_must_repeat_its_value(tmp_path):
    # 080 note 4: a repeated key used to keep its last value
    rec = module("release_receipts")
    d = tmp_path / "sta" / SLOW
    d.mkdir(parents=True)
    lines = [f"%OL_METRIC_{'I' if 'count' in k else 'F'} {k}__corner:{SLOW} 0" for k in rec.STA_METRICS]
    (d / "sta.log").write_text("\n".join(lines + lines[:1]) + "\n")
    assert rec.sta_metrics(tmp_path, SLOW)["timing__setup__ws"] == 0      # the same value twice is harmless
    (d / "sta.log").write_text("\n".join(lines + [f"%OL_METRIC_F timing__setup__ws__corner:{SLOW} 1.5"]) + "\n")
    with pytest.raises(ValueError, match="twice"):
        rec.sta_metrics(tmp_path, SLOW)
    (d / "sta.log").write_text("\n".join([f"%OL_METRIC_I timing__setup_vio__count__corner:{SLOW} 3"] + lines) + "\n")
    with pytest.raises(ValueError, match="twice"):
        rec.sta_metrics(tmp_path, SLOW)


def test_release_evidence_plans_the_48mhz_release(tmp_path):
    from test_flow_tools import evidence_args, flow_module
    ev = flow_module("release_evidence")
    assert ev.slow_period(48) == 20.832 and ev.slow_period(50) == 20.0 and ev.slow_period(46) == 21.738
    assert ev.slow_period(48) <= 1000 / 48
    for tenths in range(400, 501):   # 40.0 .. 50.0 MHz: never slower than claimed, an even ps count
        mhz = tenths / 10
        ps = round(ev.slow_period(mhz) * 1000)
        assert ps % 2 == 0 and ev.slow_period(mhz) <= 1000 / mhz + 1e-12 and 1000 / mhz - ev.slow_period(mhz) < 0.002
    with pytest.raises(SystemExit):
        ev.parse_args(evidence_args(tmp_path, "--validation-commit", "HEAD", "--slow-mhz", "51"))
    a = ev.parse_args(evidence_args(tmp_path, "--validation-commit", "HEAD", "--slow-mhz", "48"))
    inherited = dict(PATH="/usr/bin", SLOW_CLK_NS="23", HOME="/home/x")
    jobs = ev.plan_jobs(a, tmp_path / "view", tmp_path / "V", tmp_path / "work", base_env=inherited)
    by = {j.name: j for j in jobs}
    assert sorted(by) == ["checks", "gl", "precheck", "sdf_auto_fast", "sdf_auto_slow", "sdf_auto_typ",
                          "sdf_standard", "sdf_v2", "sta_slow"]
    assert by["sdf_standard"].env["SLOW_CLK_NS"] == "20.832" and by["sdf_v2"].env["SLOW_CLK_NS"] == "20.832"
    assert all("SLOW_CLK_NS" not in j.env for n, j in by.items() if n not in ("sdf_standard", "sdf_v2"))
    sta = by["sta_slow"]
    assert sta.kind == "sta" and sta.weight == 1 and sta.argv[2:4] == [str(tmp_path / "view"), "20.832"]
    cmd = ev.release_command(tmp_path / "run", tmp_path / "V", [("gl", "g"), ("sta", "s")], slow_mhz=48)
    assert cmd[cmd.index("--slow-mhz") + 1] == "48" and cmd[cmd.index("--slow-sta-receipt") + 1] == "s"
    assert "--slow-mhz" not in ev.release_command(tmp_path / "run", tmp_path / "V", [])
    # 50 MHz: the eight jobs (the seven, and the clock/reset checks) without sta_slow
    a50 = ev.parse_args(evidence_args(tmp_path, "--validation-commit", "HEAD"))
    assert "sta_slow" not in {j.name for j in ev.plan_jobs(a50, tmp_path / "view", tmp_path / "V", tmp_path / "work",
                                                          base_env=inherited)}


def test_sdf_slow_cases_at_the_48mhz_period_count_for_48_not_50(tmp_path):
    from test_release_evidence import sdf_output
    wt = candidate(tmp_path)
    plan = module("release_receipts").read_json(sdf_output(wt) / "inputs.json")["plan"]
    for case in plan:
        if case["corner"] == "slow" and case["expect"] == "pass" and case["period_ns"] == 20:
            case["period_ns"] = 20.832
    rel = module("release_check")
    assert rel.sdf_coverage([{"plan": plan}], 48, "v2") == []
    assert any("SDF slow" in e for e in rel.sdf_coverage([{"plan": plan}], 50, "v2"))
