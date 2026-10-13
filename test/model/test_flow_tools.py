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
    assert c == json.loads((ROOT / "src/config.json").read_text())
    # t48ba uses extra setup margin during PnR. Signoff still selects the real
    # latch arcs, with neither that margin nor the PnR-only D->Q exception.
    assert c["SIGNOFF_SDC_FILE"] == "dir::protoemu.sdc"
    assert c["PNR_SDC_FILE"] == "dir::protoemu_pnr_margin.sdc"
    margin = [l for l in (ROOT / "src/protoemu_pnr_margin.sdc").read_text().splitlines()
              if l.strip() and not l.lstrip().startswith("#")]
    assert margin == ["source [file join [file dirname $::env(PNR_SDC_FILE)] protoemu_pnr.sdc]",
                      "set_clock_uncertainty -setup [expr {$::env(CLOCK_UNCERTAINTY_CONSTRAINT) + 0.75}] [get_clocks clk]"]
    pnr = [l for l in (ROOT / "src/protoemu_pnr.sdc").read_text().splitlines()
           if l.strip() and not l.lstrip().startswith("#")]
    assert pnr == ["source [file join [file dirname $::env(PNR_SDC_FILE)] protoemu.sdc]",
                   "set_disable_timing -from D -to Q [get_lib_cells */sg13cmos5l_dlhq_1]"]
    assert c["//generator"]["preset"] == "submission"
    assert c["GRT_RESIZER_HOLD_SLACK_MARGIN"] == 0.13
    steps = c["meta"]["substituting_steps"]
    assert steps["+OpenROAD.ResizerTimingPostGRT"] == "Project.SramPinBuffers"
    assert steps["+Project.SramPinBuffers"] == "OpenROAD.RepairDesignPostGRT"
    # antenna diodes count as loads: nets they take over the limit are split
    # after detailed routing (flow/antenna_fanout.tcl)
    assert steps["OpenROAD.DetailedRouting"] == "Project.DetailedRouting"
    assert c["MAX_FANOUT_CONSTRAINT"] == 8


@pytest.mark.parametrize("options,step,fanout_fix", [
    ([], "Project.SramClockAdvance", True),
    (["--no-sram-clock-advance", "--late-clock", "0"], "Project.FixClockFanout", True),
    (["--no-clock-fanout-fix"], "Project.SramClockAdvance", False),
])
def test_submission_clock_defaults_and_overrides_keep_geometry(tmp_path, options, step, fanout_fix):
    dst = tmp_path / "clock-fix.json"
    subprocess.run([sys.executable, str(ROOT / "flow/gen_config.py"), *options,
                    "--output", str(dst)], check=True, capture_output=True)
    c = json.loads(dst.read_text())
    baseline = json.loads((ROOT / "src/config.json").read_text())
    assert baseline["//generator"]["clock_fanout_fix"] is True
    assert c["meta"]["substituting_steps"]["+OpenROAD.CTS"] == step
    if fanout_fix:
        assert c["CTS_SINK_CLUSTERING_SIZE"] == 8
    else:
        assert "CTS_SINK_CLUSTERING_SIZE" not in c
    if step == "Project.SramClockAdvance":
        assert c.get("PROTOEMU_CLOCK_FANOUT_FIX", False) is fanout_fix
        assert c["PROTOEMU_LATE_CLOCK_STAGES"] == 6
        assert c["PROTOEMU_LATE_CLOCK_REGS"] == ["u_chip.pin_oe*=6", "*.pin_out_cur*"]
    else:
        assert "PROTOEMU_CLOCK_FANOUT_FIX" not in c
        assert "PROTOEMU_LATE_CLOCK_STAGES" not in c
    for name in ("CLOCK_PERIOD", "MACROS", "PNR_SDC_FILE", "SIGNOFF_SDC_FILE", "PNR_CORNERS"):
        assert c[name] == baseline[name]


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
    (repo / "info.yaml").write_text("project: {}\n")
    (repo / "runs/wokwi/resolved.json").write_text('{"CLOCK_PERIOD":20}')
    (repo / "runs/wokwi/final/metrics.json").write_text('{}')
    for folder, filename in (("gds", "design.gds"), ("lef", "design.lef")):
        p = repo / "runs/wokwi/final" / folder / filename
        p.parent.mkdir(parents=True)
        p.write_text("fixture")
    for corner in ("nom_typ_1p20V_25C", "nom_slow_1p08V_125C", "nom_fast_1p32V_m40C"):
        p = repo / "runs/wokwi/final/sdf" / corner / "design.sdf"
        p.parent.mkdir(parents=True)
        p.write_text("fixture")
        sta = repo / "runs/wokwi/99-openroad-stapostpnr" / corner
        sta.mkdir(parents=True)
        (sta / "sta.log").write_text("fixture")
        (sta / "clock.rpt").write_text("Clock: clk\nPeriod: 20.000000\n")
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
    if behavior == "pass":
        assert {p.name for p in outputs[0].glob("*.xml")} == {"test.xml", "test_pio.xml", "test_eth_service.xml"}


def test_mutation_infrastructure_is_not_a_kill(tmp_path):
    spec = importlib.util.spec_from_file_location("mutants", ROOT / "test/mutants.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.result(tmp_path, "false").startswith("INFRASTRUCTURE")
    (tmp_path / "m.xml").write_text('<testsuites><testsuite><testcase name="old"><failure/></testcase></testsuite></testsuites>')
    assert module.result(tmp_path, "false").startswith("INFRASTRUCTURE")
    assert not (tmp_path / "m.xml").exists()


@pytest.mark.parametrize("fault", [None, "slow-setup", "typ-setup", "fast-hold", "drc", "lvs",
                                   "wrong-sram", "no-gds"])
def test_signoff_verdict(tmp_path, fault):
    """TT's criteria: no DRC/LVS/antenna errors, typ setup, hold everywhere,
    the right SRAM view per corner. The slow corner's setup is reported only."""
    spec = importlib.util.spec_from_file_location("signoff", ROOT / "flow/signoff.py")
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    run = tmp_path / "run"
    for d in ("final/nl", "final/gds"):
        (run / d).mkdir(parents=True)
    (run / "final/nl/design.nl.v").write_text("module design; endmodule\n")
    if fault != "no-gds":
        (run / "final/gds/design.gds").write_bytes(b"fixture")
    metrics = {k: 0 for _, k in gate.KEYS}
    for c, view in gate.CORNERS.items():
        for k in ("timing__setup__ws", "timing__hold__ws"):
            metrics[f"{k}__corner:{c}"] = 0.5
        log = run / "99-openroad-stapostpnr" / c / "sta.log"
        log.parent.mkdir(parents=True)
        log.write_text(f"RM_IHPSG13_1P_256x32_c2_bm_bist_{'typ_1p20V_25C' if fault == 'wrong-sram' else view}.lib\n")
    corner = {k: c for k, c in zip(("typ", "slow", "fast"), gate.CORNERS)}
    if fault == "slow-setup":
        metrics[f"timing__setup__ws__corner:{corner['slow']}"] = -4.0
    if fault == "typ-setup":
        metrics[f"timing__setup__ws__corner:{corner['typ']}"] = -0.01
    if fault == "fast-hold":
        metrics[f"timing__hold__ws__corner:{corner['fast']}"] = -0.01
    if fault == "drc":
        metrics["klayout__drc_error__count"] = 3
    if fault == "lvs":
        metrics["design__lvs_error__count"] = 1
    (run / "final/metrics.json").write_text(json.dumps(metrics))
    assert gate.main(str(run)) == (0 if fault in (None, "slow-setup") else 1)


def test_fpga_profile_replaces_asic_primitives(tmp_path):
    spec = importlib.util.spec_from_file_location("fpga_profile", ROOT / "flow/fpga_profile.py")
    fpga = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fpga)
    fpga.stage(tmp_path / "fpga", ncores=1, fifo_log2=2)
    src = tmp_path / "fpga/src"
    assert "cfg_gclk" not in (src / "pe_core.sv").read_text()
    assert "gcfg_gclk" not in (src / "pe_chip.sv").read_text()
    project = (src / "project.v").read_text()
    assert "`define PE_FLOP_IMEM" in project and "`define PE_NCORES 1" in project
    with pytest.raises(ValueError):
        fpga.stage(tmp_path / "fpga")                    # never overwrites
