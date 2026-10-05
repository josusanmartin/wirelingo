"""Release gates must fail on stale results, missing checks and config drift."""
import importlib.util
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]


def test_configuration_check_is_read_only_and_detects_drift(tmp_path):
    dst = tmp_path / "config.json"
    cmd = [sys.executable, str(ROOT / "flow/gen_config.py"), "--output", str(dst)]
    subprocess.run(cmd, check=True, capture_output=True)
    original = dst.read_bytes()
    assert subprocess.run(cmd + ["--check"], capture_output=True).returncode == 0
    assert dst.read_bytes() == original
    dst.write_text('{"stale": true}')
    assert subprocess.run(cmd + ["--check"], capture_output=True).returncode != 0
    assert dst.read_text() == '{"stale": true}'
    c = json.loads(original)
    # signoff times the real latch arcs; PnR is the same SDC plus only the FIFO
    # latch D->Q exception (docs/review-fixes.md, "PnR latch timing")
    assert c["SIGNOFF_SDC_FILE"] == "dir::protoemu.sdc"
    assert c["PNR_SDC_FILE"] == "dir::protoemu_pnr.sdc"
    pnr = [l for l in (ROOT / "src/protoemu_pnr.sdc").read_text().splitlines()
           if l.strip() and not l.lstrip().startswith("#")]
    assert pnr == ["source [file join [file dirname $::env(PNR_SDC_FILE)] protoemu.sdc]",
                   "set_disable_timing -from D -to Q [get_lib_cells */sg13cmos5l_dlhq_1]"]
    assert c["//generator"]["preset"] == "submission"


def test_clock_repair_is_opt_in_and_keeps_submission_geometry(tmp_path):
    dst = tmp_path / "clock-fix.json"
    subprocess.run([sys.executable, str(ROOT / "flow/gen_config.py"), "--clock-fanout-fix",
                    "--output", str(dst)], check=True, capture_output=True)
    c = json.loads(dst.read_text())
    baseline = json.loads((ROOT / "src/config.json").read_text())
    assert c["meta"]["substituting_steps"]["+OpenROAD.CTS"] == "Project.FixClockFanout"
    assert c["CTS_SINK_CLUSTERING_SIZE"] == 8
    for name in ("CLOCK_PERIOD", "MACROS", "PNR_SDC_FILE", "SIGNOFF_SDC_FILE", "PNR_CORNERS"):
        assert c[name] == baseline[name]
    assert "clock_fanout_fix" not in baseline["//generator"]


@pytest.mark.parametrize("racing", [False, True])
def test_snapshot_includes_all_root_plugins_and_rejects_racing_edit(tmp_path, monkeypatch, racing):
    monkeypatch.syspath_prepend(str(ROOT / "flow"))
    import manifest
    repo, work = tmp_path / "source", tmp_path / "work"
    for p in (repo, work):
        p.mkdir()
        subprocess.run(["git", "init", "-q", str(p)], check=True)
        (p / "librelane_plugin_fixture.py").write_text("# old plugin\n")
    (repo / "librelane_plugin_fixture.py").write_text("# uncommitted team repair\n")
    (repo / "librelane_plugin_new.py").write_text("# new autoloaded plugin\n")
    (work / "librelane_plugin_deleted.py").write_text("# removed in candidate\n")
    copy = shutil.copy2
    def racing_copy(src, dst):
        copy(src, dst)
        if racing and pathlib.Path(src).name == "librelane_plugin_fixture.py":
            pathlib.Path(src).write_text("# edited during snapshot\n")
    monkeypatch.setattr(shutil, "copy2", racing_copy)
    if racing:
        with pytest.raises(ValueError, match="source changed while snapshotting"):
            manifest.snapshot(repo, work)
    else:
        manifest.snapshot(repo, work)
        assert (work / "librelane_plugin_fixture.py").read_bytes() == (repo / "librelane_plugin_fixture.py").read_bytes()
        assert (work / "librelane_plugin_new.py").is_file()
        assert not (work / "librelane_plugin_deleted.py").exists()


@pytest.mark.parametrize("behavior", ["compile-failure", "startup-failure", "missing-xml", "test-failure",
                                    "pio-failure", "eth-service-failure", "pass"])
def test_gl_never_accepts_stale_xml(tmp_path, behavior):
    repo = tmp_path / "candidate"
    for d in ("src", "test", "runs/wokwi/final/nl", "fakebin"):
        (repo / d).mkdir(parents=True)
    (repo / "src/project.v").write_text('`define PE_NCORES 2\n`define PE_NBANKS `PE_NCORES\n`define PE_FIFO_LOG2 3\n')
    for module in ("test", "test_pio", "test_eth_service"):
        (repo / ("test/" + module + ".py")).write_text('@cocotb.test()\nasync def test_a(dut): pass\n')
    shutil.copy(ROOT / "test/check_results.py", repo / "test")
    (repo / "runs/wokwi/final/nl/design.nl.v").write_text('module design; endmodule\n')
    old = '<testsuites><testsuite><testcase name="old"/></testsuite></testsuites>'
    (repo / "test/gl.xml").write_text(old)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                    "commit", "-qm", "fixture"], check=True)
    stub = repo / "fakebin/make"
    stub.write_text('#!/usr/bin/env python3\nimport sys\n'
                    + ('sys.exit(42)\n' if behavior in ("compile-failure", "startup-failure") else '')
                    + ("\nargs=dict(arg.split('=',1) for arg in sys.argv[1:] if '=' in arg)\n"
                       f"bad={dict(**{'test-failure':'test','pio-failure':'test_pio','eth-service-failure':'test_eth_service'}).get(behavior)!r}\n"
                       "failure='<failure message=\"assertion\"/>' if args['COCOTB_TEST_MODULES']==bad else ''\n"
                       "open(args['COCOTB_RESULTS_FILE'], 'w').write('<testsuites><testsuite><testcase name=\"test_a\">'+failure+'</testcase></testsuite></testsuites>')\n"
                       if behavior in ("pass", "test-failure", "pio-failure", "eth-service-failure") else ''))
    stub.chmod(0o755)
    env = dict(os.environ, PATH=str(repo / "fakebin") + os.pathsep + os.environ["PATH"])
    r = subprocess.run(["bash", str(ROOT / "flow/gl_test.sh"), str(repo)], env=env, capture_output=True, text=True)
    assert (r.returncode == 0) == (behavior == "pass"), r.stdout + r.stderr
    assert (repo / "test/gl.xml").read_text() == old
    outputs = list((repo / "test/sim_build").glob("gl.*"))
    assert len(outputs) == 1
    assert (outputs[0] / "manifest.json").exists() == (behavior == "pass")
    if behavior == "pass":
        suites = json.loads((outputs[0] / "manifest.json").read_text())["suites"]
        assert {s["module"] for s in suites} == {"test", "test_pio", "test_eth_service"}
        for suite in suites:
            assert suite["results_sha256"] == hashlib.sha256((outputs[0] / suite["results_file"]).read_bytes()).hexdigest()


def test_mutation_infrastructure_is_not_a_kill(tmp_path):
    spec = importlib.util.spec_from_file_location("mutants", ROOT / "test/mutants.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.result(tmp_path, "false").startswith("INFRASTRUCTURE")
    (tmp_path / "m.xml").write_text('<testsuites><testsuite><testcase name="old"><failure/></testcase></testsuite></testsuites>')
    assert module.result(tmp_path, "false").startswith("INFRASTRUCTURE")
    assert not (tmp_path / "m.xml").exists()


@pytest.mark.parametrize("fault", [None, "negative-timing", "wrong-sram", "missing-receipt",
                                  "changed-evidence", "different-netlist", "unbound-gl",
                                  "missing-suite", "gl-suite-failure", "changed-gl-results",
                                  "changed-plugin", "new-plugin", "legacy-flow-provenance",
                                  "changed-tt", "changed-merged-config"])
def test_release_gate_requires_exact_complete_evidence(tmp_path, monkeypatch, fault):
    monkeypatch.syspath_prepend(str(ROOT / "flow"))
    spec = importlib.util.spec_from_file_location("review_signoff", ROOT / "flow/signoff.py")
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    import manifest
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for name in ("src/project.v", "src/config_merged.json", "src/user_config.json",
                 "flow/fixture.py", "librelane_plugin_fixture.py", "tt/fixture.py"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n")
    subprocess.run(["git", "init", "-q", str(tmp_path / "tt")], check=True)
    hardware = {"src/project.v": "fixture"}
    monkeypatch.setattr(gate, "sources", lambda: hardware)
    candidate = tmp_path / "candidate-inputs.json"
    files = manifest.sources(tmp_path)
    physical = manifest.physical_flow_files(files)
    runtime = manifest.hardening_files(tmp_path)
    inputs = dict(hardware_files=hardware, hardware_sha256=gate.identity(hardware), files=files,
                  physical_flow_files=physical, physical_flow_sha256=gate.identity(physical),
                  hardening_files=runtime, hardening_sha256=gate.identity(runtime))
    if fault == "legacy-flow-provenance":
        inputs.pop("physical_flow_files")
    candidate.write_text(json.dumps(inputs))
    if fault in ("changed-plugin", "new-plugin", "changed-tt", "changed-merged-config"):
        target = {"changed-plugin":"librelane_plugin_fixture.py", "new-plugin":"librelane_plugin_extra.py",
                  "changed-tt":"tt/fixture.py", "changed-merged-config":"src/config_merged.json"}[fault]
        (tmp_path / target).write_text("changed after capture\n")
    run = tmp_path / "run"
    for d in ("final/nl", "final/gds"):
        (run / d).mkdir(parents=True)
    nl = run / "final/nl/design.nl.v"
    nl.write_text("module design; endmodule\n")
    (run / "final/gds/design.gds").write_bytes(b"fixture")
    metrics = {k: 0 for _, k in gate.KEYS}
    for c, view in zip(gate.CORNERS, ("typ_1p20V_25C", "slow_1p08V_125C", "fast_1p32V_m55C")):
        for k in ("timing__setup__ws", "timing__hold__ws", "timing__setup_vio__count",
                  "timing__hold_vio__count", "design__max_slew_violation__count", "design__max_cap_violation__count"):
            metrics[f"{k}__corner:{c}"] = 0
        log = run / "99-openroad-stapostpnr" / c / "sta.log"
        log.parent.mkdir(parents=True)
        log.write_text(f"RM_IHPSG13_1P_256x32_c2_bm_bist_{'typ_1p20V_25C' if fault == 'wrong-sram' else view}.lib\n")
    if fault == "negative-timing":
        metrics[f"timing__setup__ws__corner:{gate.CORNERS[1]}"] = -0.01
    (run / "final/metrics.json").write_text(json.dumps(metrics))
    digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    evidence = run / "checks.txt"
    evidence.write_text("reviewed timing evidence fixture\n")
    gl = run / "gl.json"
    suites = []
    for module in ("test", "test_pio", "test_eth_service"):
        xml = run / (module + ".xml")
        failure = '<failure/>' if fault == "gl-suite-failure" and module == "test_eth_service" else ''
        xml.write_text('<testsuites><testsuite><testcase name="fixture">'+failure+'</testcase></testsuite></testsuites>')
        suites.append(dict(module=module, tests=1, results_file=xml.name, results_sha256=digest(xml)))
    if fault == "missing-suite":
        suites.pop()
    if fault == "changed-gl-results":
        (run / "test_pio.xml").write_text('<stale/>')
    gl.write_text(json.dumps(dict(status="pass", kind="functional GL without SDF",
                                 netlist_sha256=digest(nl), simulated_netlist_sha256=digest(nl),
                                 suites=suites,
                                 candidate_inputs_sha256=None if fault == "unbound-gl" else digest(candidate))))
    receipt = dict(netlist_sha256="different" if fault == "different-netlist" else digest(nl))
    for name in ("cdc_max_delay", "synchronizer_resolution", "latch_timing", "clock_gating",
                 "minimum_pulse_width", "electrical_limits", "functional_gl"):
        path = gl if name == "functional_gl" else evidence
        receipt[name] = dict(status="pass", evidence=[dict(path=path.name, sha256=digest(path))])
    if fault != "missing-receipt":
        (run / "release_checks.json").write_text(json.dumps(receipt))
    if fault == "changed-evidence":
        evidence.write_text("stale receipt\n")
    assert gate.main(str(run), str(candidate)) == (0 if fault is None else 1)


@pytest.mark.parametrize("fault", [None, "tool-failure", "missing-report", "negative-timing"])
def test_fpga_retains_failures_and_requires_routed_timing(tmp_path, monkeypatch, fault):
    monkeypatch.syspath_prepend(str(ROOT / "flow"))
    import fpga_validate as flow
    monkeypatch.setattr(flow, "tool_identity", lambda name: {"version": "fixture"})
    def run(cmd, out, name):
        (out / (name + ".log")).write_text(repr(cmd))
        if name == "synthesis":
            (out / "design.json").write_text('{}')
            (out / "mapped.v").write_text('module fixture; endmodule')
        elif fault == "tool-failure":
            raise RuntimeError("route failed (1)")
        elif fault != "missing-report":
            clocks = {name: dict(achieved=24 if fault == "negative-timing" else 60, constraint=48)
                      for name in ("$glbnet$clk$TRELLIS_IO_IN", "$glbnet$ui_in[6]$TRELLIS_IO_IN")}
            (out / "route.json").write_text(json.dumps(dict(fmax=clocks)))
    monkeypatch.setattr(flow, "run", run)
    out = tmp_path / "private"
    if fault:
        with pytest.raises(RuntimeError):
            flow.validate(out, 48)
    else:
        flow.validate(out, 48)
    receipt = json.loads((out / "validation.json").read_text())
    assert receipt["status"] == ("fail" if fault else "pass")
    assert receipt["evidence"]["manifest.json"] == hashlib.sha256((out / "manifest.json").read_bytes()).hexdigest()
    assert json.loads((out / "manifest.json").read_text())["ncores"] == 2
    assert (out / "route.log").exists()
    old = (out / "validation.json").read_bytes()
    with pytest.raises(ValueError, match="output exists"):
        flow.validate(out, 48)
    assert (out / "validation.json").read_bytes() == old


def test_fpga_staging_rejects_a_racing_team_edit(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "flow"))
    import fpga_profile as flow
    source = tmp_path / "source"
    shutil.copytree(ROOT / "src", source / "src")
    monkeypatch.setattr(flow, "ROOT", source)
    copy = shutil.copytree
    def racing_copy(src, dst, *args, **kwargs):
        copy(src, dst, *args, **kwargs)
        if pathlib.Path(src) == source / "src":
            with (source / "src/project.v").open("a") as f:
                f.write("\n// team edit during copy\n")
    monkeypatch.setattr(flow.shutil, "copytree", racing_copy)
    with pytest.raises(ValueError, match="sources changed during staging"):
        flow.stage(tmp_path / "private")
    assert not (tmp_path / "private/manifest.json").exists()
