"""A release needs complete evidence for the actual candidate, not pass text."""
import importlib.util
import json
from pathlib import Path
import shutil

import pytest

ROOT = Path(__file__).resolve().parents[2]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "flow" / f"{name}.py")
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def candidate(tmp_path):
    wt = tmp_path / "candidate"
    run = wt / "runs/wokwi"
    for d in ("final/nl", "final/gds", "final/lef"):
        (run / d).mkdir(parents=True)
    (wt / "src").mkdir()
    (wt / "test").mkdir()
    (wt / "src/project.v").write_text("`define PE_NCORES 2\n`define PE_NBANKS `PE_NCORES\n`define PE_FIFO_LOG2 3\n")
    (wt / "src/config.json").write_text('{"CLOCK_PERIOD":20}')
    (wt / "info.yaml").write_text("project:\n  top_module: design\n")
    (run / "resolved.json").write_text('{"CLOCK_PERIOD":20}')
    (run / "final/nl/design.nl.v").write_text("module design; endmodule\n")
    (run / "final/gds/design.gds").write_bytes(b"fixture GDS")
    (run / "final/lef/design.lef").write_text("fixture LEF")
    for name in ("test", "test_pio", "test_eth_service"):
        (wt / "test" / f"{name}.py").write_text(
            "@cocotb.test()\nasync def test_a(dut): pass\n"
            "@cocotb.test()\nasync def test_b(dut): pass\n")
    gate = module("signoff")
    metrics = {key: 0 for _, key in gate.KEYS}
    for c, view in gate.CORNERS.items():
        for key in ("timing__setup__ws", "timing__hold__ws"):
            metrics[f"{key}__corner:{c}"] = 0.2
        for key in ("timing__setup_vio__count", "timing__hold_vio__count",
                    "design__max_slew_violation__count", "design__max_cap_violation__count",
                    "design__max_fanout_violation__count"):
            metrics[f"{key}__corner:{c}"] = 0
        log = run / "99-openroad-stapostpnr" / c / "sta.log"
        log.parent.mkdir(parents=True)
        log.write_text(f"RM_IHPSG13_1P_256x32_c2_bm_bist_{view}.lib\n")
        (log.parent / "clock.rpt").write_text("Clock: clk\nSources: clk\nPeriod: 20.000000\n")
        sdf = run / "final/sdf" / c / "design.sdf"
        sdf.parent.mkdir(parents=True)
        sdf.write_text(f"fixture SDF {c}\n")
    (run / "final/metrics.json").write_text(json.dumps(metrics))
    return wt


def xml(names=("test_a", "test_b")):
    return '<testsuites><testsuite>' + ''.join(f'<testcase name="{n}"/>' for n in names) + '</testsuite></testsuites>'


def gl_output(wt, out=None):
    rec = module("release_receipts")
    out = out or wt / "test/sim_build/gl.fresh"
    rec.begin(wt, out, "gl", wt)
    shutil.copyfile(wt / "runs/wokwi/final/nl/design.nl.v", out / "netlist.v")
    for name in rec.GL_MODULES:
        (out / f"{name}.xml").write_text(xml())
        (out / f"{name}.log").write_text("simulator completed\n")
    return out


def sdf_output(wt, *, profile="v2"):
    rec = module("release_receipts")
    out = wt / "test/sim_build/gl_sdf.fresh"
    names = list(rec.BASIC_TESTS) + list(rec.V2_TESTS) + ["test_usb_fs_device"]
    source = "".join(f"@cocotb.test()\nasync def {n}(dut): pass\n" for n in names)
    # SDF uses a separate verification checkout, which may add directed tests.
    validation = wt.parent / "validator"
    shutil.copytree(wt / "src", validation / "src")
    (validation / "test").mkdir(parents=True, exist_ok=True)
    (validation / "test/test.py").write_text(source)
    plan = []
    for corner in ("typ", "slow", "fast"):
        plan.append(dict(corner=corner, period_ns=20., expect="pass", module="test",
                         tests=names[:-1] if profile == "v2" else list(rec.BASIC_TESTS), directory=corner))
        if profile == "v2":
            plan.append(dict(corner=corner, period_ns=20.834, expect="pass", module="test",
                             tests=["test_usb_fs_device"], directory=corner+"_usb"))
    plan.append(dict(corner="slow", period_ns=10., expect="fail", module="test",
                     tests=["test_fetch_to_pin"], directory="negative"))
    rec.begin(wt, out, "sdf", validation, plan)
    for case in plan:
        d = out / case["directory"]
        d.mkdir()
        result = xml(case["tests"])
        log = f"sdf_annotate: {d}/annotate.sdf -> tb.user_project\n** TESTS={len(case['tests'])}\n"
        rc = 0
        if case["expect"] == "fail":
            result = result.replace('/>', '><failure error_type="AssertionError" error_msg="wrong value"/></testcase>', 1)
            log += "AssertionError: wrong pin value\n"
            rc = 2
        (d / "results.xml").write_text(result)
        (d / "sim.log").write_text(log)
        (d / "status.json").write_text(json.dumps({"returncode": rc}))
        for f in ("sdf_prep.log", "netlist.v", "annotate.sdf"):
            (d / f).write_text("fixture\n")
    return out


def precheck_output(wt):
    rec = module("release_receipts")
    out = wt / "test/sim_build/precheck.fresh"
    rec.begin(wt, out, "precheck", wt)
    copy = out / "design/runs/wokwi/final/gds/design.gds"
    copy.parent.mkdir(parents=True)
    shutil.copyfile(wt / "runs/wokwi/final/gds/design.gds", copy)
    report = out / "tt/precheck/reports/results.xml"
    report.parent.mkdir(parents=True)
    report.write_text(xml(("drc", "pin_check")))
    (out / "tt/precheck/precheck.py").write_text("fixture support tool\n")
    (out / "status.json").write_text('{"returncode":0}')
    (out / "precheck.log").write_text(f"INFO: Precheck passed for {copy}!\n")
    return out


def complete_receipts(wt):
    rec = module("release_receipts")
    paths = [rec.finish(out) for out in (gl_output(wt), sdf_output(wt), precheck_output(wt))]
    return [str(wt), "--gl-receipt", str(paths[0]), "--sdf-receipt", str(paths[1]),
            "--precheck-receipt", str(paths[2])]


def test_complete_release_and_relocation(tmp_path):
    wt = candidate(tmp_path)
    args = complete_receipts(wt)
    assert module("release_check").main(args) == 0
    # Relative artifact hashes remain useful when the whole evidence is archived.
    moved = tmp_path / "reports/archive"
    shutil.copytree(wt, moved)
    relocated = [s.replace(str(wt), str(moved)) for s in args]
    assert module("release_check").main(relocated) == 0


@pytest.mark.parametrize("fault", ["missing-suite", "missing-case", "duplicate-case", "wrong-name", "unlisted-skip", "failed", "skipped", "malformed", "wrong-netlist", "changed-source", "changed-candidate"])
def test_gl_receipt_rejects_incomplete_or_changed_run(tmp_path, fault):
    wt = candidate(tmp_path)
    out = gl_output(wt)
    path = out / "test.xml"
    if fault == "missing-suite":
        (out / "test_eth_service.xml").unlink()
    elif fault == "missing-case":
        path.write_text(xml(("test_a",)))
    elif fault == "duplicate-case":
        path.write_text(xml(("test_a", "test_a")))
    elif fault == "wrong-name":
        path.write_text(xml(("test_a", "something_else")))
    elif fault == "unlisted-skip":
        path.write_text(xml().replace('</testsuite>', '<testcase name="unlisted"><skipped/></testcase></testsuite>'))
    elif fault in ("failed", "skipped"):
        tag = "failure" if fault == "failed" else "skipped"
        path.write_text(xml().replace('/>', f'><{tag}/></testcase>', 1))
    elif fault == "malformed":
        path.write_text("<testcase")
    elif fault == "wrong-netlist":
        (out / "netlist.v").write_text("wrong netlist")
    elif fault == "changed-source":
        (wt / "test/test.py").write_text("changed test\n")
    else:
        (wt / "runs/wokwi/final/nl/design.nl.v").write_text("changed netlist\n")
    with pytest.raises((OSError, ValueError, module("release_receipts").ET.ParseError)):
        module("release_receipts").finish(out)
    assert not (out / "receipt.json").exists()


@pytest.mark.parametrize("artifact", ["results.xml", "sim.log", "annotate.sdf", "status.json"])
def test_changed_sdf_output_invalidates_receipt(tmp_path, artifact):
    wt = candidate(tmp_path)
    rec = module("release_receipts")
    out = sdf_output(wt)
    receipt = rec.finish(out)
    path = out / "typ" / artifact
    path.write_text(path.read_text() + "\nchanged after completion\n")
    with pytest.raises((ValueError, rec.ET.ParseError)):
        rec.verify(receipt, wt, "sdf")


@pytest.mark.parametrize("fault", ["annotation-error", "no-annotation", "simulator-crash", "negative-runtime-error", "negative-passes", "missing-test", "command-fails"])
def test_sdf_receipt_requires_real_executed_outcomes(tmp_path, fault):
    wt = candidate(tmp_path)
    out = sdf_output(wt)
    log = out / ("negative" if fault.startswith("negative") else "typ") / "sim.log"
    if fault == "annotation-error":
        log.write_text(log.read_text() + "SDF ERROR: bad cell\n")
    elif fault == "no-annotation":
        log.write_text("simulation passed\n")
    elif fault == "simulator-crash":
        log.write_text(log.read_text() + "SimulatorFailure: premature exit\n")
    elif fault == "negative-runtime-error":
        log.write_text(log.read_text().replace("AssertionError", "RuntimeError"))
        result = out / "negative/results.xml"
        result.write_text(result.read_text().replace("AssertionError", "RuntimeError"))
    elif fault == "negative-passes":
        (out / "negative/results.xml").write_text(xml(("test_fetch_to_pin",)))
    elif fault == "missing-test":
        (out / "typ/results.xml").write_text(xml(("test_id",)))
    elif fault == "command-fails":
        (out / "typ/status.json").write_text('{"returncode":2}')
    with pytest.raises(ValueError):
        module("release_receipts").finish(out)


@pytest.mark.parametrize("field,value", [("timing__setup__ws", -0.01), ("timing__hold__ws", -0.001),
                                        ("timing__setup_vio__count", 1), ("timing__hold_vio__count", -1),
                                        ("design__max_slew_violation__count", 1),
                                        ("design__max_cap_violation__count", None),
                                        ("design__max_fanout_violation__count", True)])
def test_release_rejects_bad_final_metrics_even_with_fresh_receipts(tmp_path, field, value):
    wt = candidate(tmp_path)
    path = wt / "runs/wokwi/final/metrics.json"
    metrics = json.loads(path.read_text())
    metrics[f"{field}__corner:nom_slow_1p08V_125C"] = value
    path.write_text(json.dumps(metrics))
    args = complete_receipts(wt)
    assert module("release_check").main(args) == 1
    if field == "timing__setup__ws":
        assert module("release_check").main(args + ["--slow-mhz", "48"]) == 1


def test_electrical_waiver_is_explicit_and_requires_reason(tmp_path, capsys):
    wt = candidate(tmp_path)
    path = wt / "runs/wokwi/final/metrics.json"
    metrics = json.loads(path.read_text())
    metrics["design__max_fanout_violation__count__corner:nom_slow_1p08V_125C"] = 1
    path.write_text(json.dumps(metrics))
    args = complete_receipts(wt) + ["--waive", "fanout=1"]
    rel = module("release_check")
    with pytest.raises(SystemExit):
        rel.main(args)
    assert rel.main(args + ["--waiver-reason", "example reviewed exception"]) == 0
    assert "WITH ELECTRICAL WAIVERS" in capsys.readouterr().out


@pytest.mark.parametrize("fault", ["other-gds", "failure", "missing-xml", "command-fails", "wrong-log"])
def test_precheck_receipt_is_for_the_checked_gds(tmp_path, fault):
    wt = candidate(tmp_path)
    out = precheck_output(wt)
    report = out / "tt/precheck/reports/results.xml"
    if fault == "other-gds":
        (out / "design/runs/wokwi/final/gds/design.gds").write_bytes(b"different GDS")
    elif fault == "failure":
        report.write_text(xml(("drc",)).replace('/>', '><failure/></testcase>'))
    elif fault == "missing-xml":
        report.unlink()
    elif fault == "command-fails":
        (out / "status.json").write_text('{"returncode":1}')
    else:
        (out / "precheck.log").write_text("Precheck passed for some-other.gds!\n")
    with pytest.raises((OSError, ValueError)):
        module("release_receipts").finish(out)


def test_sdf_coverage_does_not_count_slow_or_report_only_cases(tmp_path):
    wt = candidate(tmp_path)
    out = sdf_output(wt)
    plan = module("release_receipts").read_json(out / "inputs.json")["plan"]
    rel = module("release_check")
    assert rel.sdf_coverage([{"plan": plan}], 50, "v2") == []
    for case in plan:
        if case["corner"] == "slow" and case["expect"] == "pass":
            case["period_ns"] = 23
    assert any("SDF slow" in error for error in rel.sdf_coverage([{"plan": plan}], 50, "v2"))
    for case in plan:
        case["expect"] = "report"
    assert len(rel.sdf_coverage([{"plan": plan}], 50, "v2")) >= 4


@pytest.mark.parametrize("fault", ["clock-report", "40mhz-run", "nan", "missing-clock"])
def test_fresh_receipts_cannot_hide_invalid_timing_constraint(tmp_path, fault):
    wt = candidate(tmp_path)
    report = wt / "runs/wokwi/99-openroad-stapostpnr/nom_slow_1p08V_125C/clock.rpt"
    if fault == "clock-report":
        report.write_text("Clock: clk\nPeriod: 25.000000\n")
    elif fault == "40mhz-run":
        (wt / "runs/wokwi/resolved.json").write_text('{"CLOCK_PERIOD":25}')
        for p in (wt / "runs/wokwi").glob("*-openroad-stapostpnr/*/clock.rpt"):
            p.write_text("Clock: clk\nPeriod: 25.000000\n")
    elif fault == "nan":
        p = wt / "runs/wokwi/final/metrics.json"
        metrics = json.loads(p.read_text())
        metrics["timing__setup__ws__corner:nom_slow_1p08V_125C"] = float("nan")
        p.write_text(json.dumps(metrics))
    else:
        report.write_text("Clock: sck\nPeriod: 20.000000\n")
    assert module("release_check").main(complete_receipts(wt)) == 1


def test_validator_hardware_change_during_sdf_is_rejected(tmp_path):
    wt = candidate(tmp_path)
    out = sdf_output(wt)
    (wt.parent / "validator/src/project.v").write_text("changed validation hardware profile")
    with pytest.raises(ValueError, match="validation inputs changed"):
        module("release_receipts").finish(out)


@pytest.mark.parametrize("kind,value", [("sdf", False), ("sdf", "0"), ("precheck", False), ("precheck", "0")])
def test_receipt_requires_numeric_process_status(tmp_path, kind, value):
    wt = candidate(tmp_path)
    out = sdf_output(wt) if kind == "sdf" else precheck_output(wt)
    status = out / ("typ/status.json" if kind == "sdf" else "status.json")
    status.write_text(json.dumps({"returncode": value}))
    with pytest.raises(ValueError):
        module("release_receipts").finish(out)


@pytest.mark.parametrize("attributes", ['errors="1"', 'tests="3"'])
def test_incomplete_xml_suite_is_not_a_pass(tmp_path, attributes):
    wt = candidate(tmp_path)
    out = gl_output(wt)
    p = out / "test.xml"
    p.write_text(p.read_text().replace('<testsuite>', f'<testsuite {attributes}>'))
    with pytest.raises(ValueError):
        module("release_receipts").finish(out)


def test_actual_testbench_annotation_banner(tmp_path):
    import re
    wt = candidate(tmp_path)
    out = sdf_output(wt)
    template = re.search(r'\$display\("(sdf_annotate: [^"]*%0s[^"]*)"',
                         (ROOT / "test/sdf_annotate.v").read_text())[1]
    for d in out.iterdir():
        if d.is_dir():
            p = d / "sim.log"
            lines = p.read_text().splitlines()
            lines[0] = template.replace("%0s", str(d / "annotate.sdf"))
            p.write_text("\n".join(lines) + "\n")
    assert module("release_receipts").finish(out).exists()


@pytest.mark.parametrize("fault", [None, "old-software", "changed-software", "different-rtl"])
def test_gl_can_validate_new_firmware_with_identical_hardware(tmp_path, fault):
    wt = candidate(tmp_path)
    newer = tmp_path / "new-software"
    shutil.copytree(wt, newer)
    p = newer / "test/test.py"
    p.write_text(p.read_text() + "@cocotb.test()\nasync def test_new_firmware(dut): pass\n")
    out = wt / "test/sim_build/gl.new-software"
    rec = module("release_receipts")
    rec.begin(wt, out, "gl", newer)
    shutil.copyfile(wt / "runs/wokwi/final/nl/design.nl.v", out / "netlist.v")
    for m in rec.GL_MODULES:
        (out / f"{m}.xml").write_text(xml(rec.test_names(newer / "test" / f"{m}.py")))
        (out / f"{m}.log").write_text("complete\n")
    receipt = rec.finish(out)
    selected = newer
    if fault == "old-software":
        selected = wt
    elif fault == "changed-software":
        p.write_text(p.read_text() + "# changed after verification\n")
    elif fault == "different-rtl":
        (newer / "src/project.v").write_text("different hardware profile\n")
    if fault is None:
        assert rec.verify(receipt, wt, "gl", selected)["status"] == "complete"
    else:
        with pytest.raises(ValueError):
            rec.verify(receipt, wt, "gl", selected)


@pytest.mark.parametrize("fault", ["partial-gl", "unbound-logs", "wrong-period", "nan-slack"])
def test_legacy_pass_text_cannot_release(tmp_path, fault):
    wt = candidate(tmp_path)
    gl = wt / "test/sim_build/gl.legacy"
    gl.mkdir(parents=True)
    (gl / "netlist.v").write_bytes((wt / "runs/wokwi/final/nl/design.nl.v").read_bytes())
    names = ("test",) if fault == "partial-gl" else ("test", "test_pio", "test_eth_service")
    for name in names:
        (gl / f"{name}.xml").write_text(xml(("test_a",) if fault == "partial-gl" else ("test_a", "test_b")))
    if fault == "wrong-period":
        (wt / "runs/wokwi/resolved.json").write_text('{"CLOCK_PERIOD":25}')
    if fault == "nan-slack":
        path = wt / "runs/wokwi/final/metrics.json"
        metrics = json.loads(path.read_text())
        for key in metrics:
            if "__ws__" in key:
                metrics[key] = float("nan")
        path.write_text(json.dumps(metrics))
    sdf, pre = tmp_path / "old-sdf.log", tmp_path / "old-precheck.log"
    sdf.write_text("A DIFFERENT CANDIDATE: gl_sdf_test: all cases as required\n")
    pre.write_text("INFO: Precheck passed for unrelated.gds!\n")
    assert module("release_check").main([str(wt), "--sdf-log", str(sdf), "--precheck-log", str(pre)]) == 1
