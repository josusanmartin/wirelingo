"""A release needs complete evidence for the actual candidate, not pass text."""
import importlib.util
import json
import os
import subprocess
import sys
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
    # LibreLane records the design directory (absolute, in the worktree it hardened)
    (run / "resolved.json").write_text(json.dumps({"CLOCK_PERIOD": 20, "DESIGN_DIR": os.path.realpath(wt / "src")}))
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
    gate_inputs(wt)
    return wt


# ---- the release gates' inputs (flow/release_gates.py): one SRAM instance, FS-placed

SRAM = "RM_IHPSG13_1P_TEST"
SRAM_INST = "u_mem[0].sram"
SRAM_DEF_NAME = r"u_mem\[0\].sram"
# macro-local power columns (microns): one per half for each pin, full height of a 20 x 10 macro
SRAM_PINS = {"VDD!": [(1, 2), (15, 16)], "VDDARRAY!": [(3, 4), (17, 18)], "VSS!": [(6, 7), (12, 13)]}
SRAM_AT = (100000, 50000)
OWN_GATING_REPORT = "%OL_CREATE_REPORT gating_pulse_reset.rpt\nNo paths found.\n%OL_END_REPORT\n"
STRIPES_LOG = ("[WARNING ORD-0039] .openroad ignored with -python\n"
               "[INFO] u_mem\\[0\\].sram: array L: 2 pairs, band: 2 pairs, array R: 2 pairs; VPWR 3 / VGND 3 stripes\n"
               "[INFO] VPWR: tidy: 0 redundant stripe segments dropped\n"
               "[INFO] VGND: tidy: 0 redundant stripe segments dropped\n")


def sram_lef():
    pins = ""
    for pin, columns in SRAM_PINS.items():
        use = "GROUND" if pin == "VSS!" else "POWER"
        ports = "".join(f"    PORT\n      LAYER Metal4 ;\n        RECT {x0} 0 {x1} 10 ;\n    END\n" for x0, x1 in columns)
        pins += f"  PIN {pin}\n    DIRECTION INOUT ;\n    USE {use} ;\n{ports}  END {pin}\n"
    pins += "  PIN A_CLK\n    DIRECTION INPUT ;\n    USE SIGNAL ;\n    PORT\n      LAYER Metal2 ;\n" \
            "        RECT 9 0 9.5 0.5 ;\n    END\n  END A_CLK\n"
    return f"VERSION 5.7 ;\nMACRO {SRAM}\n  CLASS BLOCK ;\n  ORIGIN 0 0 ;\n  SIZE 20 BY 10 ;\n{pins}END {SRAM}\nEND LIBRARY\n"


def stripe(x_um, width=1000):
    """A full-height Metal4 stripe over a macro-local column centre (microns)."""
    x = SRAM_AT[0] + round(x_um * 1000)
    return f"      NEW Metal4 {width} + SHAPE STRIPE ( {x} 0 ) ( * 200000 )\n"


def sram_def(drop=(), extra="", conns=None, orient="FS"):
    """The final DEF: the SRAM placed, and a stripe over each of its power columns
    (except the (pin, half) pairs in `drop`)."""
    nets = {"VPWR": ["VDD!", "VDDARRAY!"], "VGND": ["VSS!"]}
    conns = conns or {"VPWR": "( PIN VPWR ) ( * VDD ) ( * VDD! ) ( * VDDARRAY! )", "VGND": "( PIN VGND ) ( * VSS ) ( * VSS! )"}
    special = ""
    for net, pins in nets.items():
        wires = "      + ROUTED Metal1 480 + SHAPE FOLLOWPIN ( 0 3780 ) ( 300000 3780 )\n"
        for pin in pins:
            for half, (x0, x1) in zip(("left", "right"), SRAM_PINS[pin]):
                if (pin, half) not in drop:
                    wires += stripe((x0 + x1) / 2)
        wires += extra if net == "VGND" else ""
        use = "GROUND" if net == "VGND" else "POWER"
        special += f"    - {net} {conns[net]} + USE {use}\n{wires}      ;\n"
    return (f"VERSION 5.8 ;\nDESIGN design ;\nUNITS DISTANCE MICRONS 1000 ;\nDIEAREA ( 0 0 ) ( 300000 200000 ) ;\n"
            f"COMPONENTS 2 ;\n    - {SRAM_DEF_NAME} {SRAM} + FIXED ( {SRAM_AT[0]} {SRAM_AT[1]} ) {orient} ;\n"
            f"    - _001_ sg13cmos5l_buf_1 + PLACED ( 10 10 ) N ;\nEND COMPONENTS\n"
            f"SPECIALNETS 2 ;\n{special}END SPECIALNETS\nEND DESIGN\n")


def gate_inputs(wt):
    """The release gates' inputs for a clean run: the run's own violator report in
    each sta.log, the SRAM in resolved.json, the ExtendPowerStripes log, the final
    DEF and the macro LEF."""
    run = wt / "runs/wokwi"
    for log in run.glob("*-openroad-stapostpnr/*/sta.log"):
        log.write_text(log.read_text() + OWN_GATING_REPORT)
    lef = wt.parent / "pdk-lef" / f"{SRAM}.lef"
    lef.parent.mkdir(exist_ok=True)
    lef.write_text(sram_lef())
    resolved = json.loads((run / "resolved.json").read_text())
    resolved.update(PDK_ROOT=str(lef.parent),       # the macro LEF is a PDK file
                    MACROS={SRAM: {"lef": [str(lef)], "instances": {SRAM_INST: {"location": [100, 50],
                                                                                  "orientation": "FS"}}}},
                    PDN_MACRO_CONNECTIONS=[r"u_mem\\?\[0\\?\]\.sram VPWR VGND VDD! VSS!",
                                           r"u_mem\\?\[0\\?\]\.sram VPWR VGND VDDARRAY! VSS!"])
    (run / "resolved.json").write_text(json.dumps(resolved))
    (run / "22-project-extendpowerstripes").mkdir()
    (run / "22-project-extendpowerstripes/project-extendpowerstripes.log").write_text(STRIPES_LOG)
    (run / "final/def").mkdir()
    (run / "final/def/design.def").write_text(sram_def())
    return wt


STA_STEP = "99-openroad-stapostpnr"
XCLK_SDC = ("set period $::env(CLOCK_PERIOD)\n"
            "create_clock -name clk -period $period [get_ports clk]\n"
            "create_clock -name sck -period $period [get_ports {ui_in[6]}]\n"
            "set_max_delay -ignore_clock_latency $period -from [get_clocks sck] -to [get_clocks clk]\n"
            "set_max_delay -ignore_clock_latency $period -from [get_clocks clk] -to [get_clocks sck]\n"
            "set_false_path -hold -from [get_clocks sck] -to [get_clocks clk]\n"
            "set_false_path -hold -from [get_clocks clk] -to [get_clocks sck]\n"
            "set_false_path -from [get_clocks clk] -to [get_clocks clk]\n"
            "set_propagated_clock [all_clocks]\n")


def final_sta_state(wt):
    """The final STA step's config and input state (as LibreLane writes them),
    unless a test wrote its own (test_flow_slow48.sta_step). Also the generator
    that the run, as its own shipping checkout, runs for `gen_config.py --preset
    submission --check` (exit 0; not a candidate file)."""
    run = wt / "runs/wokwi"
    (wt / "flow").mkdir(exist_ok=True)
    if not (wt / "flow/gen_config.py").exists():
        (wt / "flow/gen_config.py").write_text("# generator\n")
    if (run / STA_STEP / "state_in.json").exists():
        return wt
    files = {"59-fill/design.nl.v": (run / "final/nl/design.nl.v").read_text(), "59-fill/design.sdc": "sdc\n",
             "60-odb/design.odb": "odb\n", "61-rcx/nom/design.nom.spef": "spef\n",
             "final/spef/nom/design.nom.spef": "spef\n"}
    for name, text in files.items():
        (run / name).parent.mkdir(parents=True, exist_ok=True)
        (run / name).write_text(text)
    (wt / "src/protoemu.sdc").write_text(XCLK_SDC)
    (wt / "src/timing_checks.tcl").write_text("# fixture\n")
    state = {"nl": str(run / "59-fill/design.nl.v"), "sdc": str(run / "59-fill/design.sdc"),
             "odb": str(run / "60-odb/design.odb"), "spef": {"nom_*": str(run / "61-rcx/nom/design.nom.spef")}}
    config = {"meta": {"librelane_version": "3.1.0.dev3", "step": "OpenROAD.STAPostPNR"}, "CLOCK_PERIOD": 20,
              "PDK_ROOT": str(wt.parent / "pdk"), "SIGNOFF_SDC_FILE": str(wt / "src/protoemu.sdc"),
              "STA_EXTRA_CORNER_TCL_FILE": str(wt / "src/timing_checks.tcl")}
    (run / STA_STEP / "state_in.json").write_text(json.dumps(state))
    (run / STA_STEP / "config.json").write_text(json.dumps(config))
    p = run / "resolved.json"
    p.write_text(json.dumps(dict(json.loads(p.read_text()), meta={"librelane_version": "3.1.0.dev3"})))
    return wt


CHECK_IMAGE_ID = "sha256:" + "c" * 64


def signoff_report(corner, *, worst=None, violators=None):
    """A clean check_types.rpt: met recovery and removal checks, no pulse width
    check (the signoff SDC's two-way clock exceptions), no minimum-period or
    gating check. `worst`/`violators` replace a kind's section body."""
    worst = dict({"recovery": "   2.980012   slack (MET)", "removal": "   1.206076   slack (MET)"}, **(worst or {}))
    text = f"== corner {corner}\n"
    for kind in ("min_pulse_width", "min_period", "recovery", "removal", "clock_gating_setup", "clock_gating_hold"):
        text += f"== worst {kind}\n{worst.get(kind, '')}\n== violators {kind}\n{(violators or {}).get(kind, '')}\n"
    return text + "== end of checks\n"


REGISTERS = {"clk": ["_001_/CLK", "_002_/CLK"], "sck": ["_003_/CLK"]}


def pulse_table(registers=REGISTERS, extra=""):
    """report_pulse_width_checks -no_line_splits: one row per check."""
    rows = [f"{SRAM_INST}/A_CLK (high)    0.400000    9.998153    9.598153 (MET)"]
    for pins in registers.values():
        for pin in pins:
            rows += [f"{pin} (high)    0.247479    9.940100    9.692620 (MET)",
                     f"{pin} (low)    0.190000    9.950000    9.760000 (MET)"]
    return "    Required      Actual\nPin    Width       Width       Slack\n" + "\n".join(rows) + "\n" + extra


UNION = object()


def pulse_report(corner, *, worst="   9.598153   slack (MET)", violators="", pins=None, registers=REGISTERS,
                 table=None, header=None, all_pins=UNION, clock_lists=True):
    """A clean pulse_checks.rpt. `registers` gives each clock's register clock
    pins, `all_pins` all_registers -clock_pins (default: their union; None
    leaves the section out), `table` the check table; registers=None leaves
    out all three, clock_lists=False the per-clock lists only."""
    pins = {f"{SRAM_INST}/A_CLK": "   9.598153   slack (MET)"} if pins is None else pins
    text = f"== corner {header or corner}\n== worst min_pulse_width\n{worst}\n== violators min_pulse_width\n{violators}\n"
    text += "== sram clock pins\n" + "".join(f"pin {pin}\n{body}\n" for pin, body in pins.items())
    if registers is not None:
        if clock_lists:
            text += "== clock register pins\n" + "".join(
                f"clock {clock} {len(ps)}\n" + "".join(f"  {pin}\n" for pin in ps) for clock, ps in registers.items())
        if all_pins is UNION:
            all_pins = [pin for ps in registers.values() for pin in ps]
        if all_pins is not None:
            text += f"== all register clock pins\nregisters {len(all_pins)}\n" + "".join(f"  {p}\n" for p in all_pins)
        text += "== all pulse width checks\n" + (pulse_table(registers) if table is None else table)
    return text + "== end of checks\n"


def checks_output(wt, *, signoff=signoff_report, pulse=pulse_report, finish=True):
    """A checks receipt (flow/check_rerun.py's) written without docker."""
    rec = module("release_receipts")
    final_sta_state(wt)
    out = wt / "test/sim_build/checks.fresh"
    flow = ROOT / "flow"
    config = rec.read_json(wt / "runs/wokwi" / STA_STEP / "config.json")
    text, removed = rec.pulse_sdc(Path(config["SIGNOFF_SDC_FILE"]).read_text())
    plan = dict(step=STA_STEP, image="ghcr.io/librelane/librelane:3.1.0.dev3", image_id=CHECK_IMAGE_ID,
                librelane_version="3.1.0.dev3",
                tcl={name: rec.digest(flow / tcl) for name, tcl in rec.CHECK_PASSES.items()}, sdc_removed=removed)
    rec.begin(wt, out, "checks", wt, plan)
    configs = rec.check_configs(wt, out)
    for name, tcl in rec.CHECK_PASSES.items():
        d = out / name
        d.mkdir()
        shutil.copyfile(flow / tcl, d / tcl)
        if name == "pulse":
            (d / "pulse.sdc").write_text(text)
        (d / "config.json").write_text(json.dumps(configs[name]))
        (d / "run.log").write_text(f"docker run --rm --network none -w {d} {CHECK_IMAGE_ID} python3 -m librelane.steps "
                                   f"run --id OpenROAD.STAPostPNR -c {d}/config.json -i state_in.json -o {d}/sta\n")
        (d / "status.json").write_text('{"returncode": 0}\n')
        for corner in rec.CORNERS:
            (d / "sta" / corner).mkdir(parents=True)
            report = (signoff if name == "signoff" else pulse)(corner)
            (d / "sta" / corner / rec.CHECK_REPORTS[name]).write_text(report)
    return rec.finish(out) if finish else out


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


# A stand-in for tt-support-tools' tt_tool.py --create-user-config: the two generated configs
FAKE_TT = """import json, os, sys
assert sys.argv[1:] == ["--create-user-config", "--ihp"]
if os.path.basename(os.getcwd()).startswith("clean-tree-regen."):    # the release's regeneration
    assert not os.path.exists("tt/__pycache__"), "tt/ has bytecode"   # 100b: sources only
    if os.environ.get("FAKE_TT_LOG"):
        open(os.environ["FAKE_TT_LOG"], "a").write(json.dumps([os.getcwd(), os.environ["PATH"]]) + "\\n")
user = {"DESIGN_NAME": "design", "VERILOG_FILES": ["dir::project.v"]}
json.dump(user, open("src/user_config.json", "w"), indent=2)
config = json.load(open("src/config.json"))
config.update(user)
json.dump(config, open("src/config_merged.json", "w"), indent=2)
"""
FAKE_IMAGE_ID = "sha256:" + "d1" * 32
GIT_ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@example.com")


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True,
                          env=GIT_ENV).stdout.strip()


def commit_all(repo, message="fixture"):
    if not (Path(repo) / ".git").exists():
        git(repo, "init", "-q")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--allow-empty", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def harden_env():
    """The outer environment as flow/harden.sh would see it here: pytest's own
    PYTHONPATH=tools (the test runner's, not a harden's) left out."""
    return {k: v for k, v in os.environ.items() if not (k == "PYTHONPATH" and v == "tools")}


def clean_tree(wt, *, pins=None, before_start=None, after_start=None, compiled=None):
    """wt as flow/harden.sh leaves it: a commit (the run's files ignored as the
    repository ignores them), tt/ a clean checkout of a stand-in tt-support-tools,
    a PDK checkout, flow/release_pins.json pinning them, and runs/clean-tree.json
    recorded at start and after generation. Returns release_check's extra args."""
    ct = module("clean_tree")
    tt = wt / "tt"
    tt.mkdir(exist_ok=True)
    (tt / "tt_tool.py").write_text(FAKE_TT)
    (tt / ".gitignore").write_text("__pycache__/\n")     # as tt-support-tools ignores it
    tt_head = commit_all(tt)
    pdk = wt.parent / "pdk-checkout"
    (pdk / "ihp-sg13cmos5l").mkdir(parents=True, exist_ok=True)
    (pdk / "ihp-sg13cmos5l/README").write_text("fixture PDK\n")
    pdk_head = commit_all(pdk)
    (pdk / "ihp-sg13cmos5l/SOURCES").write_text(f"IHP-Open-PDK {pdk_head}\n")
    (wt / ".gitignore").write_text("runs/\ntt/\ntest/sim_build/\nsrc/user_config.json\nsrc/config_merged.json\n"
                                   "__pycache__/\n")
    (wt / "flow").mkdir(exist_ok=True)
    (wt / "flow/release_pins.json").write_text(json.dumps(dict(
        {"tt_support_tools": tt_head, "librelane_image": FAKE_IMAGE_ID, "pdk": pdk_head}, **(pins or {}))))
    commit_all(wt)
    if before_start:                          # an uncommitted change in the worktree or tt/ (a dirty harden)
        before_start(wt)
    aside = wt.parent / "runs-aside"          # a harden starts before runs/ exists
    shutil.move(str(wt / "runs"), str(aside))
    ct.start(wt, "ghcr.io/librelane/librelane:3.1.0.dev3", pdk, ["fixture"], ident=FAKE_IMAGE_ID,
             environ=harden_env(), syspath=[])
    if after_start:                           # what happens between the receipt and generation
        after_start(wt)
    subprocess.run([sys.executable, "tt/tt_tool.py", "--create-user-config", "--ihp"], cwd=wt, check=True)
    if compiled:                              # bytecode the generation step compiles
        compiled(wt)
    ct.generated(wt)
    # LibreLane's log names the image its container ran (as on a real harden)
    (wt / "exp.log").write_text("Running containerized command:\ndocker run --rm --name x -i --user 1000:1000 "
                                f"-v /h:/h -w {wt} ghcr.io/librelane/librelane:3.1.0.dev3 python3 -m librelane "
                                "--run-tag wokwi src/config_merged.json\n")
    for p in aside.iterdir():                 # then the flow's outputs
        shutil.move(str(p), str(wt / "runs" / p.name))
    aside.rmdir()
    return ["--flow-python", sys.executable]


def complete_receipts(wt, **clean):
    rec = module("release_receipts")
    final_sta_state(wt)
    extra = clean_tree(wt, **clean)
    paths = [rec.finish(out) for out in (gl_output(wt), sdf_output(wt), precheck_output(wt))]
    # the run is its own shipping checkout here: the same build inputs
    return [str(wt), "--gl-receipt", str(paths[0]), "--sdf-receipt", str(paths[1]),
            "--precheck-receipt", str(paths[2]), "--checks-receipt", str(checks_output(wt)),
            "--shipping-root", str(wt), *extra]


def test_complete_release_and_relocation(tmp_path):
    wt = candidate(tmp_path)
    args = complete_receipts(wt)
    assert module("release_check").main(args) == 0
    # Relative artifact hashes remain useful when the whole evidence is archived.
    # An archive keeps no STA step state (its paths name the original run), so
    # the run-side STA and gate checks are skipped there, and say so.
    moved = tmp_path / "reports/archive"
    shutil.copytree(wt, moved)
    for name in ("state_in.json", "config.json"):
        (moved / "runs/wokwi" / STA_STEP / name).unlink()
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


@pytest.mark.parametrize("fault", ["annotation-error", "annotation-warning", "no-annotation", "simulator-crash", "negative-runtime-error", "negative-passes", "missing-test", "command-fails"])
def test_sdf_receipt_requires_real_executed_outcomes(tmp_path, fault):
    wt = candidate(tmp_path)
    out = sdf_output(wt)
    log = out / ("negative" if fault.startswith("negative") else "typ") / "sim.log"
    if fault == "annotation-error":
        log.write_text(log.read_text() + "SDF ERROR: bad cell\n")
    elif fault == "annotation-warning":
        log.write_text(log.read_text() + "SDF WARNING: annotate.sdf:100: COND not supported.\n")
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


@pytest.mark.parametrize("kind", ["gl", "sdf"])
@pytest.mark.parametrize("dependency", ["test/model/test_eth10.py", "test/pio_program.json"])
@pytest.mark.parametrize("phase", ["during-run", "after-receipt"])
def test_imported_helpers_and_pio_fixture_are_bound(tmp_path, kind, dependency, phase):
    wt = candidate(tmp_path)
    root = wt if kind == "gl" else wt.parent / "validator"
    helper = root / dependency
    helper.parent.mkdir(parents=True, exist_ok=True)
    helper.write_text("original test input\n")
    out = gl_output(wt) if kind == "gl" else sdf_output(wt)
    rec = module("release_receipts")
    receipt = rec.finish(out) if phase == "after-receipt" else None
    helper.write_text("changed test input\n")
    with pytest.raises(ValueError):
        if receipt is None:
            rec.finish(out)
        else:
            rec.verify(receipt, wt, kind, root)


def test_generated_simulator_files_do_not_invalidate_input_snapshot(tmp_path):
    wt = candidate(tmp_path)
    out = gl_output(wt)
    # Private precheck copies and simulator output may have source suffixes.
    for name in ("test/sim_build/precheck.other/tt/precheck/precheck.py",
                 "test/sim_build/gl.other/netlist.v", "test/sim_build/gl.other/status.json",
                 "test/model/__pycache__/generated.py"):
        p = wt / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("generated output\n")
    assert module("release_receipts").finish(out).exists()


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


def test_verification_runner_change_during_execution_is_rejected(tmp_path, monkeypatch):
    rec = module("release_receipts")
    runner = tmp_path / "runner"
    runner.mkdir()
    script = runner / "sdf_prep.py"
    script.write_text("original preparation code\n")
    monkeypatch.setattr(rec, "RUNNER_ROOT", runner)
    wt = candidate(tmp_path)
    out = wt / "test/sim_build/gl.runner-change"
    rec.begin(wt, out, "gl", wt)
    script.write_text("different preparation code\n")
    with pytest.raises(ValueError, match="runner changed"):
        rec.finish(out)
