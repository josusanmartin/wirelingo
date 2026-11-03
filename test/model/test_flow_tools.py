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
    (["--no-antenna-fanout-repair"], "Project.SramClockAdvance", True),
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
    steps = c["meta"]["substituting_steps"]
    if "--no-antenna-fanout-repair" in options:
        assert "OpenROAD.DetailedRouting" not in steps
        # An intentional opt-out is reproducible with its own flags, but must
        # fail the shipping-default drift check without rewriting the file.
        original = dst.read_bytes()
        command = [sys.executable, str(ROOT / "flow/gen_config.py"),
                   "--output", str(dst), "--check"]
        assert subprocess.run(command + options, capture_output=True).returncode == 0
        assert subprocess.run(command, capture_output=True).returncode != 0
        assert dst.read_bytes() == original
    else:
        assert steps["OpenROAD.DetailedRouting"] == "Project.DetailedRouting"
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
    assert "eth-no-fcs-check" in module.select(["eth-*"]) and module.select(["model-alu-borrow"]) == ["model-alu-borrow"]
    with pytest.raises(SystemExit, match="no mutant matches"):      # not "all of them"
        module.select(["eth-*", "eth-mistyped"])


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


# --- flow/release_evidence.py and flow/archive_evidence.py: the pure parts,
# with fixture receipts and fake processes (never real EDA).

def flow_module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "flow" / f"{name}.py")
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def evidence_args(tmp_path, *extra):
    run = tmp_path / "protoemu-exp-fx"
    (run / "runs/wokwi/final").mkdir(parents=True, exist_ok=True)
    (run / "tt/precheck").mkdir(parents=True, exist_ok=True)
    (run / "tt/precheck/precheck.py").write_text("")
    for name in ("python", "kbin/klayout", "pdk/.keep"):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("")
    return [str(run), "--flow-python", str(tmp_path / "python"), "--klayout-dir", str(tmp_path / "kbin"),
            "--pdk-root", str(tmp_path / "pdk"), *extra]


def git_clone(tmp_path):
    origin = tmp_path / "origin"
    (origin / "test").mkdir(parents=True)
    (origin / "test/test.py").write_text("x = 1\n")
    subprocess.run(["git", "init", "-q", str(origin)], check=True)
    subprocess.run(["git", "-C", str(origin), "add", "."], check=True)
    subprocess.run(["git", "-C", str(origin), "-c", "user.name=T", "-c", "user.email=t@example.invalid",
                    "commit", "-qm", "v"], check=True)
    clone = tmp_path / "V"
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True)
    return clone


def test_release_evidence_arguments_and_preflight_refusals(tmp_path):
    ev = flow_module("release_evidence")
    with pytest.raises(SystemExit):
        ev.parse_args(evidence_args(tmp_path))                     # needs a validation source
    with pytest.raises(SystemExit):
        ev.parse_args(evidence_args(tmp_path, "--validation-root", "a", "--validation-commit", "b"))
    with pytest.raises(SystemExit):
        ev.parse_args(evidence_args(tmp_path, "--validation-commit", "HEAD", "--jobs", "0"))
    clone = git_clone(tmp_path)
    a = ev.parse_args(evidence_args(tmp_path, "--validation-root", str(clone)))
    assert a.tt_root == a.run / "tt" and a.jobs == 6
    work, archive = ev.default_paths(a, "abc1234", ev.datetime(2026, 10, 14, 6, 0, tzinfo=ev.timezone.utc))
    assert work == tmp_path / "rcpt-fx-abc1234-20261014T060000Z"
    assert archive == tmp_path / "wirelingo-release-evidence-abc1234-fx.tar.gz"
    assert ev.preflight(a, clone, work, archive) == []
    # an rsync copy (no .git), a dirty clone, existing outputs: refused up front
    copy = tmp_path / "copy"
    shutil.copytree(clone, copy, ignore=shutil.ignore_patterns(".git"))
    assert any("not a real git clone" in p for p in ev.preflight(a, copy, work, archive))
    (clone / "test/new.py").write_text("")
    assert any("uncommitted" in p for p in ev.preflight(a, clone, work, archive))
    (clone / "test/new.py").unlink()
    work.mkdir()
    archive.write_text("old")
    problems = ev.preflight(a, clone, work, archive)
    assert any("work directory exists" in p for p in problems)
    assert any("refusing to overwrite" in p for p in problems)
    shutil.rmtree(a.run / "runs")
    assert any("not a completed hardening run" in p for p in ev.preflight(a, clone, work, archive))


def test_release_evidence_plan_sets_every_runner_variable(tmp_path):
    ev = flow_module("release_evidence")
    a = ev.parse_args(evidence_args(tmp_path, "--validation-commit", "HEAD"))
    inherited = dict(PATH="/usr/bin", SLOW_CLK_NS="23", TESTS="test_id", MODULE="test_sigrok",
                     VALIDATION_ROOT="/elsewhere", NEGATIVE_CLK_NS="30", HOME="/home/x")
    jobs = ev.plan_jobs(a, tmp_path / "view", tmp_path / "V", tmp_path / "work", base_env=inherited)
    by = {j.name: j for j in jobs}
    assert sorted(by) == ["gl", "precheck", "sdf_auto_fast", "sdf_auto_slow", "sdf_auto_typ",
                          "sdf_standard", "sdf_v2"]
    assert jobs[0].name == "gl"                                    # the longest job starts first
    assert [by[n].kind for n in ("gl", "precheck", "sdf_v2")] == ["gl", "precheck", "sdf"]
    assert by["sdf_v2"].weight == 6 and by["sdf_standard"].weight == 4
    assert by["gl"].argv[-2:] == [str(tmp_path / "view"), str(tmp_path / "V")]
    assert by["sdf_standard"].argv[-1] == "standard" and by["sdf_v2"].argv[-1] == "v2"
    for corner in ("typ", "slow", "fast"):
        job = by[f"sdf_auto_{corner}"]
        assert job.argv[-2:] == [corner, "20.834"]
        assert job.env["TESTS"] == "test_usb_autonomous test_usb_autonomous_fs"
    for job in jobs:
        assert job.env["HOME"] == "/home/x"
        assert "SLOW_CLK_NS" not in job.env and "NEGATIVE_CLK_NS" not in job.env and "MODULE" not in job.env
        if not job.name.startswith("sdf_auto"):
            assert "TESTS" not in job.env
        if job.kind == "sdf":
            assert job.env["VALIDATION_ROOT"] == str(tmp_path / "V")
        else:
            assert "VALIDATION_ROOT" not in job.env
    assert by["gl"].env["PATH"].startswith(a.sim_path)
    assert by["precheck"].env["PATH"].startswith(str(tmp_path / "kbin"))
    assert by["precheck"].env["PDK_ROOT"] == str(tmp_path / "pdk")
    assert by["precheck"].argv[-1] == str(tmp_path / "work/precheck")


def test_release_evidence_view_leaves_the_run_untouched(tmp_path):
    from test_release_evidence import candidate
    ev = flow_module("release_evidence")
    wt = candidate(tmp_path)
    before = sorted(str(p.relative_to(wt)) for p in wt.rglob("*"))
    view = ev.make_view(wt, tmp_path / "view")
    assert (view / "test/sim_build").is_dir() and not (view / "test").is_symlink()
    assert (view / "runs").is_symlink() and (view / "src").is_symlink()
    assert sorted(str(p.relative_to(wt)) for p in wt.rglob("*")) == before
    with pytest.raises(FileExistsError):
        ev.make_view(wt, view)


def test_release_evidence_collects_only_the_fresh_receipt(tmp_path):
    from test_release_evidence import candidate, gl_output
    ev = flow_module("release_evidence")
    rec = flow_module("release_receipts")
    wt = candidate(tmp_path)
    parent = wt / "test/sim_build"
    parent.mkdir(parents=True)
    stale = gl_output(wt, parent / "gl.old")
    rec.finish(stale)
    job = ev.Job("gl", "gl", [], 1, out_parent=parent)
    existing = ev.snapshot([job])
    started = ev.time.time() - 1
    out = gl_output(wt, parent / "gl.new")
    receipt = rec.finish(out)
    log = lambda o, extra="": f"netlist 0123  cores=2  outputs {o}\nreceipt: {o}/receipt.json\n{extra}"
    assert ev.collect_receipt(job, log(out), existing, started) == receipt
    with pytest.raises(ValueError, match="existed before"):
        ev.collect_receipt(job, log(stale), existing, started)
    with pytest.raises(ValueError, match="one receipt line"):
        ev.collect_receipt(job, log(out, f"receipt: {stale}/receipt.json\n"), existing, started)
    with pytest.raises(ValueError, match="one receipt line"):
        ev.collect_receipt(job, f"netlist 0123 outputs {out}\n", existing, started)
    with pytest.raises(ValueError, match="not in its output directory"):
        ev.collect_receipt(job, f"x outputs {out}\nreceipt: {stale}/receipt.json\n", existing, started)
    with pytest.raises(ValueError, match="not a completed sdf receipt"):
        ev.collect_receipt(ev.Job("s", "sdf", [], 1, out_parent=parent), log(out), existing, started)
    with pytest.raises(ValueError, match="not under"):
        ev.collect_receipt(ev.Job("gl", "gl", [], 1, out_parent=tmp_path), log(out), existing, started)
    with pytest.raises(ValueError, match="predates"):
        ev.collect_receipt(job, log(out), existing, ev.time.time() + 60)
    pre = ev.Job("precheck", "gl", [], 1, out=out)
    assert ev.collect_receipt(pre, f"precheck outputs: {out}\nreceipt: {receipt}\n", set(), started) == receipt
    with pytest.raises(ValueError, match="not the requested"):
        ev.collect_receipt(ev.Job("precheck", "gl", [], 1, out=stale),
                           f"precheck outputs: {out}\nreceipt: {receipt}\n", set(), started)


def test_release_evidence_scheduler_respects_the_simulator_budget():
    ev = flow_module("release_evidence")
    clock = [0.0]

    class Fake:
        def __init__(self, job):
            self.end, self.rc = clock[0] + job.duration, job.rc

        def poll(self):
            return self.rc if clock[0] >= self.end else None

    def job(name, weight, duration, rc=0):
        j = ev.Job(name, "sdf", [], weight)
        j.duration, j.rc = duration, rc
        return j

    jobs = [job("gl", 1, 65), job("auto", 1, 32), job("pre", 1, 5, rc=1), job("std", 4, 15), job("v2", 6, 23),
            job("huge", 9, 1)]
    live = []

    def launch(j):
        live.append(j)
        return Fake(j)

    def sleep(_):
        assert sum(min(j.weight, 6) for j in live if j.process.poll() is None) <= 6
        clock[0] += 1

    results, peak = ev.run_jobs(jobs, 6, launch, clock=lambda: clock[0], sleep=sleep, report=lambda m: None)
    assert peak <= 6 and set(results) == {j.name for j in jobs}
    assert results["pre"]["returncode"] == 1 and results["gl"]["returncode"] == 0
    assert results["v2"]["seconds"] == 23


def evidence_bundle(tmp_path):
    """Fixture receipts sharing one validation root, and the verdict for them."""
    from test_release_evidence import candidate, sdf_output, precheck_output, xml
    rec = flow_module("release_receipts")
    wt = candidate(tmp_path)
    validator = wt.parent / "validator"
    (validator / "test").mkdir(parents=True)
    for name in ("test_pio", "test_eth_service"):
        shutil.copy(wt / "test" / f"{name}.py", validator / "test")
    sdf = rec.finish(sdf_output(wt))
    gl = wt / "test/sim_build/gl.fresh"
    rec.begin(wt, gl, "gl", validator)
    shutil.copyfile(wt / "runs/wokwi/final/nl/design.nl.v", gl / "netlist.v")
    for name in rec.GL_MODULES:
        (gl / f"{name}.xml").write_text(xml(rec.test_names(validator / "test" / f"{name}.py")))
        (gl / f"{name}.log").write_text("simulator completed\n")
    paths = dict(gl=rec.finish(gl), sdf=sdf, precheck=rec.finish(precheck_output(wt)))
    work = tmp_path / "work"
    (work / "logs").mkdir(parents=True)
    for name in ("logs/gl.log", "release-evidence.log", "release-check.log", "source.bundle"):
        (work / name).write_text(name)
    verdict = dict(run=str(wt), validation_root=str(validator), validation_head="f" * 40, view=str(wt),
                   work=str(work), runner_root=str(ROOT / "flow"), exit=0,
                   verdict="RELEASE PASSED (SDF profile v2, slow target 50 MHz)",
                   receipts=[dict(job=k, kind=k, path=str(p), sha256=rec.digest(p)) for k, p in paths.items()])
    (work / "verdict.json").write_text(json.dumps(verdict))
    assert flow_module("release_check").main([str(wt), "--validation-root", str(validator),
                                              "--gl-receipt", str(paths["gl"]), "--sdf-receipt", str(paths["sdf"]),
                                              "--precheck-receipt", str(paths["precheck"])]) == 0
    return wt, verdict, paths


def test_archive_manifest_binds_every_hashed_file_and_never_overwrites(tmp_path):
    import tarfile
    ar = flow_module("archive_evidence")
    rec = flow_module("release_receipts")
    wt, verdict, paths = evidence_bundle(tmp_path)
    manifest = ar.Manifest("/")
    verified = ar.collect(verdict, manifest)
    assert [v["kind"] for v in verified] == ["gl", "sdf", "precheck"]
    names = set(manifest.files)
    rel = lambda p: str(pathlib.Path(p).resolve().relative_to("/"))
    for kind, path in paths.items():
        data = rec.read_json(path)
        for key, root in (("candidate", data["worktree"]), ("validation", data["validation_root"]),
                          ("runner", data["runner_root"]), ("artifacts", path.parent)):
            for name, digest in data[key].items():
                assert manifest.files[rel(pathlib.Path(root) / name)]["sha256"] == digest
        assert rel(path) in names and rel(path.parent / "inputs.json") in names
    for name in ("verdict.json", "release-check.log", "source.bundle", "logs/gl.log"):
        assert rel(tmp_path / "work" / name) in names
    assert rel(ROOT / "flow/archive_evidence.py") in names
    output = tmp_path / "evidence.tar.gz"
    status = ar.write_archive(output, manifest, {"MANIFEST.json": b"{}\n"})
    assert status["payload_files"] == len(manifest.files) and status["sha256"] == rec.digest(output)
    with tarfile.open(output) as tf:
        assert set(tf.getnames()) == names | {"MANIFEST.json"}
    original = output.read_bytes()
    with pytest.raises(ValueError, match="refusing to overwrite"):
        ar.write_archive(output, manifest, {})
    assert output.read_bytes() == original
    with pytest.raises(ValueError, match="outside the archive base"):
        ar.Manifest(tmp_path / "work").add(paths["gl"])


@pytest.mark.parametrize("fault", ["artifact", "receipt", "verdict", "missing-precheck"])
def test_archive_refuses_changed_or_incomplete_evidence(tmp_path, fault):
    ar = flow_module("archive_evidence")
    wt, verdict, paths = evidence_bundle(tmp_path)
    if fault == "artifact":
        (paths["gl"].parent / "test.log").write_text("edited after the receipt\n")
    if fault == "receipt":
        paths["sdf"].write_text(paths["sdf"].read_text() + "\n")
    if fault == "verdict":
        verdict.update(exit=1, verdict="RELEASE FAILED:\n- something")
    if fault == "missing-precheck":
        verdict["receipts"] = [r for r in verdict["receipts"] if r["kind"] != "precheck"]
    with pytest.raises(ValueError):
        ar.collect(verdict, ar.Manifest("/"))
    # the command-line form refuses the same evidence and writes nothing
    (tmp_path / "work/verdict.json").write_text(json.dumps(verdict))
    out = tmp_path / "evidence.tar.gz"
    assert ar.main([str(tmp_path / "work/verdict.json"), "--output", str(out), "--base", "/"]) == 1
    assert not out.exists() and not (tmp_path / "work/archive-status.json").exists()
