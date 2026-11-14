"""Item 092 (Astra review findings 2 and 3): the release gate binds the run to the
shipping build, and gates clock/reset checks and SRAM power.

* flow/build_manifest.py and release_check --shipping-root: a run hardened from
  other build inputs (a tuned config, another plugin or flow script) than the
  shipping commit's cannot release; firmware, tools and tests may differ.
* flow/check_rerun.py (a "checks" receipt) and flow/release_gates.py: pulse width
  (every SRAM clock pin), recovery and removal must each have a met check at
  every corner, no kind may have a violator, and only listed kinds may have no
  check. OpenSTA 2.7.0 reports no pulse width check at all under an SDC with
  exceptions both ways between two clocks, so the evidence comes from a pass
  without them.
* The ExtendPowerStripes log and the final DEF: no unlisted warning, every SRAM
  region fed, every macro power pin on its net and touched by a stripe of it in
  each half of the macro.

A fake `docker` (test_flow_slow48) stands in for the LibreLane image."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from test_release_evidence import (REGISTERS, ROOT, SRAM, SRAM_INST, SRAM_PINS, STA_STEP, XCLK_SDC, candidate,
                                   checks_output, complete_receipts, final_sta_state, module, pulse_report,
                                   pulse_table, signoff_report, sram_def)
from test_flow_slow48 import fake_docker, fake_image_id, DEFAULT_IMAGE  # noqa: F401 (fixture)

CORNERS = ("nom_typ_1p20V_25C", "nom_slow_1p08V_125C", "nom_fast_1p32V_m40C")
SLOW = CORNERS[1]


def release(args, capsys):
    capsys.readouterr()
    rc = module("release_check").main(args)
    return rc, capsys.readouterr().out


def with_checks(args, receipt):
    """complete_receipts' arguments with another checks receipt."""
    args = list(args)
    args[args.index("--checks-receipt") + 1] = str(receipt)
    return args


def test_a_complete_run_releases_with_every_gate(tmp_path, capsys):
    wt = candidate(tmp_path)
    rc, text = release(complete_receipts(wt) + ["--require-sta-inputs"], capsys)
    assert rc == 0 and text.splitlines()[-1] == "RELEASE PASSED (SDF profile v2, slow target 50 MHz)"
    assert f"{SLOW}: min_pulse_width: worst slack 9.59815 ns (met)" in text
    assert f"{SLOW}: SRAM clock pin {SRAM_INST}/A_CLK: 1 pulse width checks, worst slack 9.59815 ns (met)" in text
    assert f"{SLOW}: recovery: worst slack 2.98001 ns (met)" in text
    assert "min_period: no check at nom_typ_1p20V_25C, nom_slow_1p08V_125C, nom_fast_1p32V_m40C (exception:" in text
    assert f"{SRAM_INST.replace('[', chr(92) + '[').replace(']', chr(92) + ']')} VDDARRAY! -> VPWR: " \
           "1 stripes (left half), 1 stripes (right half)" in text
    assert "shipping build: the run's build inputs equal" in text


# ---- clock and reset checks

def no_check(corner):
    return pulse_report(corner, worst="", pins={})


FAULTS = {
    # OpenSTA's report under the full signoff SDC: no pulse width check at all
    "pulse-none": (dict(pulse=no_check), "min_pulse_width: no check found (pulse pass)"),
    "pulse-violated": (dict(pulse=lambda c: pulse_report(c, worst="  -0.020000   slack (VIOLATED)")),
                       "min_pulse_width violated"),
    "pulse-violator-listed": (dict(pulse=lambda c: pulse_report(c, violators="  -0.020000   slack (VIOLATED)")),
                              "min_pulse_width violated"),
    "sram-pin-absent": (dict(pulse=lambda c: pulse_report(c, pins={})),
                        f"SRAM clock pin {SRAM_INST}/A_CLK: no pulse width check"),
    "sram-pin-violated": (dict(pulse=lambda c: pulse_report(c, pins={f"{SRAM_INST}/A_CLK": "  -0.1   slack (VIOLATED)"})),
                          f"SRAM clock pin {SRAM_INST}/A_CLK: pulse width violated"),
    "recovery-none": (dict(signoff=lambda c: signoff_report(c, worst={"recovery": "No paths found."})),
                      "recovery: no check found (signoff pass)"),
    "removal-violated": (dict(signoff=lambda c: signoff_report(c, worst={"removal": "  -0.05   slack (VIOLATED)"})),
                         "removal violated"),
    "removal-violator-listed": (dict(signoff=lambda c: signoff_report(c, violators={"removal": "  -0.05   slack (VIOLATED)"})),
                                "removal violated"),
    # an exception excuses the absence of a check, never a violation
    "min-period-violated": (dict(signoff=lambda c: signoff_report(c, violators={"min_period": "  -1.0   slack (VIOLATED)"})),
                            "min_period violated"),
    "gating-violated": (dict(signoff=lambda c: signoff_report(c, worst={"clock_gating_setup": "  -0.3   slack (VIOLATED)"})),
                        "clock_gating_setup violated"),
    "signoff-pulse-violated": (dict(signoff=lambda c: signoff_report(c, violators={"min_pulse_width": "  -0.2   slack (VIOLATED)"})),
                               "min_pulse_width violated under the signoff SDC"),
    "section-missing": (dict(signoff=lambda c: signoff_report(c).replace("== worst removal\n", "")),
                        "removal: missing from the signoff pass's report"),
    # 092b: a partial loss of pulse checks (an exception's -from clock drives
    # these flops) still leaves a met worst check; the per-clock coverage fails it
    "clock-unchecked": (dict(pulse=lambda c: pulse_report(c, table=pulse_table({"clk": REGISTERS["clk"]}))),
                        "clock sck: 1 of 1 register clock pins have no pulse width check (first: _003_/CLK)"),
    "pin-unchecked": (dict(pulse=lambda c: pulse_report(c, table=pulse_table({"clk": ["_001_/CLK"],
                                                                               "sck": ["_003_/CLK"]}))),
                      "clock clk: 1 of 2 register clock pins have no pulse width check (first: _002_/CLK)"),
    "table-violated": (dict(pulse=lambda c: pulse_report(c, table=pulse_table(
                           extra="_009_/CLK (low)    0.190000    0.100000   -0.090000 (VIOLATED)\n"))),
                       "pulse width violated"),
    "no-register-list": (dict(pulse=lambda c: pulse_report(c, registers=None)),
                         "lists no clock register pins or no pulse width checks"),
    "other-corner": (dict(pulse=lambda c: pulse_report(c, header="nom_typ_1p20V_25C")),
                     "nom_slow_1p08V_125C: the pulse pass's report is not this corner's"),
    "other-corner-signoff": (dict(signoff=lambda c: signoff_report(c).replace(f"== corner {c}", "== corner x")),
                             "the signoff pass's report is not this corner's"),
    # 092c (092b finding 1b): set_case_analysis on a clock port leaves sck's flop in
    # no clock's list, with no check: all_registers -clock_pins still lists it
    "unclocked-register": (dict(pulse=lambda c: pulse_report(c, registers={"clk": REGISTERS["clk"]},
                                                             all_pins=REGISTERS["clk"] + REGISTERS["sck"])),
                           "1 of 3 register clock pins are reached by no clock (first: _003_/CLK)"),
    "unclocked-register-unchecked": (dict(pulse=lambda c: pulse_report(c, registers={"clk": REGISTERS["clk"]},
                                                                       all_pins=REGISTERS["clk"] + REGISTERS["sck"])),
                                     "1 of 3 register clock pins have no pulse width check (first: _003_/CLK)"),
    "no-all-register-list": (dict(pulse=lambda c: pulse_report(c, all_pins=None)),
                             "the pulse pass's report lists no register clock pins at all"),
    "all-register-count-wrong": (dict(pulse=lambda c: pulse_report(c).replace("registers 3\n", "registers 4\n")),
                                 "the pulse pass's report lists no register clock pins at all"),
    # 092b finding 9: a check table without the per-clock lists
    "no-clock-lists": (dict(pulse=lambda c: pulse_report(c, clock_lists=False)),
                       "lists no clock register pins or no pulse width checks"),
}


def test_a_violated_register_pin_is_reported_as_violated_not_unchecked(tmp_path, capsys):
    # 092b's equivalent mutant cov-violated-row-dropped: a violated row still counts as a check
    wt = candidate(tmp_path)
    args = complete_receipts(wt)
    shutil.rmtree(wt / "test/sim_build/checks.fresh")
    table = pulse_table({"clk": REGISTERS["clk"]}) + "".join(
        f"_003_/CLK ({edge})    0.190000    0.100000   -0.090000 (VIOLATED)\n" for edge in ("high", "low"))
    receipt = checks_output(wt, pulse=lambda c: pulse_report(c, table=table))
    rc, text = release(with_checks(args, receipt), capsys)
    assert rc == 1 and "pulse width violated" in text and "have no pulse width check" not in text


def test_the_preamble_makes_the_four_commands_no_ops_in_tcl():
    # 092b finding 9: run it, not only read it (tkinter's Tcl; an `sta` namespace
    # stands in for OpenSTA's, whose commands record that they ran)
    tkinter = pytest.importorskip("tkinter")
    rec = module("release_receipts")
    tcl = tkinter.Tcl()
    tcl.eval("set ::calls {}; namespace eval ::sta {}")
    for cmd in rec.PATH_EXCEPTIONS:
        tcl.eval(f"proc ::sta::{cmd} {{args}} {{ lappend ::calls {cmd} }}; "
                 f"namespace eval ::sta {{ namespace export {cmd} }}; namespace import ::sta::{cmd}")
    tcl.eval("set_max_delay 1")                         # before: the real commands run
    assert tcl.eval("set ::calls") == "set_max_delay"
    tcl.eval("set ::calls {}")
    tcl.eval(rec.PULSE_SDC_PREAMBLE)
    for cmd in rec.PATH_EXCEPTIONS:
        for name in (cmd, f"::{cmd}", f"sta::{cmd}", f"::sta::{cmd}"):
            assert tcl.eval(f"{name} 5 -from [list clk]") == ""
        assert tcl.eval(f"info procs ::{cmd}") == f"::{cmd}" and tcl.eval(f"info body ::{cmd}") == ""
        assert tcl.eval(f"info body ::sta::{cmd}") == ""
    tcl.eval("eval set_false_path -hold -from clk; foreach c {clk} { set_min_delay 0 -from $c }")
    assert tcl.eval("set ::calls") == ""


def test_the_preamble_text_is_exactly_the_reviewed_one():
    # runs without Tcl too: the four names and the global and sta:: no-op procs
    rec = module("release_receipts")
    assert rec.PULSE_SDC_PREAMBLE.endswith(
        "foreach protoemu_pulse_cmd {set_max_delay set_min_delay set_false_path set_multicycle_path} {\n"
        "    proc ::$protoemu_pulse_cmd {args} {}\n"
        "    if {[namespace exists ::sta]} { proc ::sta::$protoemu_pulse_cmd {args} {} }\n"
        "}\n")


@pytest.mark.parametrize("fault", sorted(FAULTS))
def test_clock_and_reset_check_faults_cannot_release(tmp_path, capsys, fault):
    reports, expected = FAULTS[fault]
    wt = candidate(tmp_path)
    args = complete_receipts(wt)
    shutil.rmtree(wt / "test/sim_build/checks.fresh")
    receipt = checks_output(wt, **reports)
    rc, text = release(with_checks(args, receipt), capsys)
    assert rc == 1 and expected in text and "RELEASE FAILED" in text


def test_without_a_checks_receipt_the_run_cannot_release(tmp_path, capsys):
    wt = candidate(tmp_path)
    args = complete_receipts(wt)
    i = args.index("--checks-receipt")
    rc, text = release(args[:i] + args[i + 2:], capsys)
    assert rc == 1 and "no clock/reset check receipt" in text


@pytest.mark.parametrize("case", ["missing", "violated"])
def test_the_runs_own_gating_pulse_reset_report_is_gated(tmp_path, capsys, case):
    wt = candidate(tmp_path)
    log = wt / "runs/wokwi" / STA_STEP / SLOW / "sta.log"
    text = log.read_text()
    if case == "missing":
        log.write_text(text.replace("%OL_CREATE_REPORT gating_pulse_reset.rpt", "%OL_CREATE_REPORT other.rpt"))
        expected = "gating_pulse_reset.rpt (src/timing_checks.tcl) is not in its sta.log"
    else:
        log.write_text(text.replace("No paths found.", "Endpoint: _1_ (recovery check)\n  -0.4   slack (VIOLATED)"))
        expected = "gating_pulse_reset.rpt shows violations"
    rc, out = release(complete_receipts(wt), capsys)
    assert rc == 1 and expected in out


def reseal(out, **plan):
    rec = module("release_receipts")
    data = json.loads((out / "receipt.json").read_text())
    data["plan"].update(plan)
    data["artifacts"] = {name: rec.digest(out / name) for name in data["artifacts"]}
    (out / "receipt.json").write_text(json.dumps(data))


@pytest.mark.parametrize("case", ["report-edited", "pulse-sdc-forged", "older-tcl", "pass-config-forged",
                                  "other-tcl", "run-sdc-changed", "other-image-ran", "run-odb-changed"])
def test_the_checks_receipt_is_bound_to_the_run(tmp_path, capsys, case):
    wt = candidate(tmp_path)
    args = complete_receipts(wt)
    out = wt / "test/sim_build/checks.fresh"
    if case == "report-edited":
        p = out / "pulse/sta" / SLOW / "pulse_checks.rpt"
        p.write_text(p.read_text().replace("9.598153", "9.9"))
        expected = "evidence changed after receipt was written"
    elif case == "pulse-sdc-forged":             # the exceptions left active: digests resealed
        (out / "pulse/pulse.sdc").write_text(XCLK_SDC)
        reseal(out)
        expected = "the pulse pass's SDC is not the run's signoff SDC with its path exceptions disabled"
    elif case == "older-tcl":                    # an honest receipt from other report Tcl (092 finding 4)
        p = out / "pulse/pulse_checks.tcl"
        p.write_text(p.read_text().replace('puts "== clock register pins"', 'puts "== clocks"'))
        reseal(out, tcl=dict(json.loads((out / "receipt.json").read_text())["plan"]["tcl"],
                             pulse=module("release_receipts").digest(p)))
        expected = "checks receipt ran other report Tcl than this checkout's flow/: ['pulse']"
    elif case == "pass-config-forged":
        p = out / "signoff/config.json"
        p.write_text(json.dumps(dict(json.loads(p.read_text()), CLOCK_PERIOD=40)))
        reseal(out)
        expected = "checks STA signoff pass config differs from the run's"
    elif case == "other-tcl":
        p = out / "signoff/check_types.tcl"
        p.write_text("# reports nothing\n")
        reseal(out)
        expected = "checks STA signoff pass ran another report Tcl"
    elif case == "run-sdc-changed":
        (wt / "src/protoemu.sdc").write_text(XCLK_SDC + "set_clock_uncertainty 1 [all_clocks]\n")
        expected = "receipt belongs to different candidate inputs"
    elif case == "other-image-ran":
        p = out / "pulse/run.log"
        p.write_text(p.read_text().replace("c" * 64, "d" * 64))
        reseal(out)
        expected = "the checks STA pulse pass did not run image"
    else:
        p = wt / "runs/wokwi/60-odb/design.odb"
        p.write_text("another odb\n")
        expected = "checks STA inputs differ from the receipt's: runs/wokwi/60-odb/design.odb"
    rc, text = release(args, capsys)
    assert rc == 1 and expected in text


# 092b (092 finding 1): one exception whose -from is a clock already drops the
# pulse width checks of every pin that clock drives, so the pulse pass disables
# every path exception, in any form, rather than removing some lines
OTHER_FORMS = ("set_max_delay 5 -from [get_clocks clk] -to [get_clocks clk]\n"        # same clock
               "set_max_delay 5 -from [get_clocks clk]\n"                             # no -to
               "set_min_delay 0 -from [get_clocks sck] -to [get_clocks clk]\n"
               "set_multicycle_path 2 -setup \\\n    -from [get_clocks sck]\n"        # continued
               "foreach c {clk} { set_false_path -hold -from [get_clocks $c] }\n"      # in a Tcl construct
               "# set_max_delay in a comment is not a command\n")


def test_the_pulse_pass_disables_every_path_exception():
    rec = module("release_receipts")
    for sdc in (XCLK_SDC, XCLK_SDC + OTHER_FORMS):
        text, named = rec.pulse_sdc(sdc)
        assert text == rec.PULSE_SDC_PREAMBLE + sdc                 # the run's SDC itself, after the preamble
        assert named == [line for line in sdc.splitlines() if not line.startswith("#")
                         and any(cmd in line for cmd in rec.PATH_EXCEPTIONS)]
    assert len(rec.pulse_sdc(XCLK_SDC)[1]) == 5                     # the same-clock false path too
    assert len(rec.pulse_sdc(XCLK_SDC + OTHER_FORMS)[1]) == 10
    # the preamble redefines exactly the four path-exception commands as no-ops
    for cmd in ("set_max_delay", "set_min_delay", "set_false_path", "set_multicycle_path"):
        assert cmd in rec.PULSE_SDC_PREAMBLE.split("foreach protoemu_pulse_cmd {")[1].split("}")[0].split()
    assert "proc ::$protoemu_pulse_cmd {args} {}" in rec.PULSE_SDC_PREAMBLE
    # the real signoff SDC: its four lines named, nothing removed
    real = (ROOT / "src/protoemu.sdc").read_text()
    text, named = rec.pulse_sdc(real)
    assert len(named) == 4 and all("get_clocks sck" in line and "get_clocks clk" in line for line in named)
    assert text.endswith(real)


def test_check_rerun_runs_both_passes_read_only_in_the_runs_image(tmp_path, fake_docker, monkeypatch, capsys):
    wt = candidate(tmp_path)
    args = complete_receipts(wt)
    reports = tmp_path / "reports"
    reports.mkdir()
    for corner in CORNERS:
        (reports / f"check_types.tcl.{corner}.rpt").write_text(signoff_report(corner))
        (reports / f"pulse_checks.tcl.{corner}.rpt").write_text(pulse_report(corner))
    monkeypatch.setenv("FAKE_CHECK_DIR", str(reports))
    assert module("check_rerun").main([str(wt), "--out-parent", str(tmp_path / "chk"), "--cpus", "2"]) == 0
    out = next((tmp_path / "chk").glob("checks.*"))
    calls = [json.loads(line) for line in fake_docker.read_text().splitlines()]
    steps = [c for c in calls if "librelane.steps" in c]
    assert len(steps) == 2
    image = fake_image_id(DEFAULT_IMAGE)
    for call, name in zip(steps, ("signoff", "pulse")):
        assert f"{wt}:{wt}:ro" in call and call[call.index("--network") + 1] == "none"
        assert call[call.index("--cpus") + 1] == "2"                  # 092b finding 9
        assert call[call.index("python3") - 1] == image and call[call.index("-c") + 1] == str(out / name / "config.json")
    original = json.loads((wt / "runs/wokwi" / STA_STEP / "config.json").read_text())
    signoff = json.loads((out / "signoff/config.json").read_text())
    pulse = json.loads((out / "pulse/config.json").read_text())
    assert signoff == dict(original, STA_EXTRA_CORNER_TCL_FILE=str(out / "signoff/check_types.tcl"))
    assert pulse == dict(original, STA_EXTRA_CORNER_TCL_FILE=str(out / "pulse/pulse_checks.tcl"),
                         SIGNOFF_SDC_FILE=str(out / "pulse/pulse.sdc"))
    rec = module("release_receipts")
    assert (out / "pulse/pulse.sdc").read_text() == rec.PULSE_SDC_PREAMBLE + XCLK_SDC
    plan = json.loads((out / "receipt.json").read_text())["plan"]
    assert plan["image_id"] == image and len(plan["sdc_removed"]) == 5
    rc, text = release(with_checks(args, out / "receipt.json"), capsys)
    assert rc == 0 and "RELEASE PASSED" in text


def test_check_rerun_refuses_an_image_without_the_runs_librelane(tmp_path, fake_docker, monkeypatch):
    wt = final_sta_state(candidate(tmp_path))
    monkeypatch.setenv("FAKE_LIBRELANE_VERSION", "3.0.0")
    assert module("check_rerun").main([str(wt), "--out-parent", str(tmp_path / "chk")]) == 2
    assert not (tmp_path / "chk").exists()


# ---- the ExtendPowerStripes log

STRIPE_FAULTS = {
    "odb-warning": ("[WARNING] u_mem\\[0\\].sram: no free VPWR column for the stripe at x=9.03\n",
                    "ExtendPowerStripes: [WARNING] u_mem"),
    "other-warning": ("[WARNING ODB-0001] something about vias\n", "ExtendPowerStripes: [WARNING ODB-0001]"),
    "error": ("[ERROR] net VPWR not found\n", "ExtendPowerStripes: [ERROR] net VPWR"),
    "traceback": ("Traceback (most recent call last):\n", "ExtendPowerStripes: Traceback"),
}


@pytest.mark.parametrize("case", sorted(STRIPE_FAULTS) + ["no-tidy", "no-allocation", "empty-region", "no-log"])
def test_power_stripe_log_faults_cannot_release(tmp_path, capsys, case):
    wt = candidate(tmp_path)
    log = wt / "runs/wokwi/22-project-extendpowerstripes/project-extendpowerstripes.log"
    text = log.read_text()
    if case in STRIPE_FAULTS:
        log.write_text(text + STRIPE_FAULTS[case][0])
        expected = STRIPE_FAULTS[case][1]
    elif case == "no-tidy":
        log.write_text(text.replace("[INFO] VGND: tidy:", "[INFO] VGND:"))
        expected = "ExtendPowerStripes did not finish VGND"
    elif case == "no-allocation":
        log.write_text("\n".join(line for line in text.splitlines() if "array L:" not in line) + "\n")
        expected = f"ExtendPowerStripes: no stripe allocation for {SRAM_INST}"
    elif case == "empty-region":
        log.write_text(text.replace("array R: 2 pairs", "array R: 0 pairs"))
        expected = f"{SRAM_INST} has a region without a VPWR/VGND pair"
    else:
        log.unlink()
        expected = "no ExtendPowerStripes log"
    rc, out = release(complete_receipts(wt), capsys)
    assert rc == 1 and expected in out


# ---- macro power pins in the final DEF

def macro_power(wt):
    return module("release_gates").macro_power_failures(wt)


@pytest.mark.parametrize("pin", sorted(SRAM_PINS))
@pytest.mark.parametrize("half", ["left", "right"])
def test_a_macro_power_pin_without_its_stripe_cannot_release(tmp_path, capsys, pin, half):
    wt = candidate(tmp_path)
    (wt / "runs/wokwi/final/def/design.def").write_text(sram_def(drop=[(pin, half)]))
    failures, _ = macro_power(wt)
    net = "VGND" if pin == "VSS!" else "VPWR"
    assert failures == [rf"u_mem\[0\].sram {pin} ({net}): no {net} stripe on the pin in the macro's {half} half"]
    rc, out = release(complete_receipts(wt), capsys)
    assert rc == 1 and failures[0] in out


@pytest.mark.parametrize("case", ["not-in-special-net", "short", "no-pdn-rule", "conflicting-pdn-rules",
                                  "orientation", "instance-missing", "flipped-north"])
def test_macro_power_connectivity(tmp_path, case):
    wt = candidate(tmp_path)
    d = wt / "runs/wokwi/final/def/design.def"
    resolved = wt / "runs/wokwi/resolved.json"
    if case == "not-in-special-net":
        d.write_text(sram_def(conns={"VPWR": "( PIN VPWR ) ( * VDD ) ( * VDD! )",
                                     "VGND": "( PIN VGND ) ( * VSS ) ( * VSS! )"}))
        expected = r"u_mem\[0\].sram VDDARRAY!: not connected to VPWR in the DEF's special nets"
    elif case == "short":
        x = 100000 + 1500                                # a VGND stripe over a VDD! column
        d.write_text(sram_def(extra=f"      NEW Metal4 1000 + SHAPE STRIPE ( {x} 0 ) ( {x} 200000 )\n"))
        expected = r"u_mem\[0\].sram VDD!: touched by stripes of ['VGND']"
    elif case == "no-pdn-rule":
        r = json.loads(resolved.read_text())
        r["PDN_MACRO_CONNECTIONS"] = r["PDN_MACRO_CONNECTIONS"][:1]
        resolved.write_text(json.dumps(r))
        expected = r"u_mem\[0\].sram VDDARRAY!: PDN_MACRO_CONNECTIONS gives no net"
    elif case == "conflicting-pdn-rules":                # a later rule puts VDD! on VGND
        r = json.loads(resolved.read_text())
        r["PDN_MACRO_CONNECTIONS"].append(r"u_mem\\?\[0\\?\]\.sram VGND VPWR VDD! VSS!")
        resolved.write_text(json.dumps(r))
        expected = r"u_mem\[0\].sram VDD!: PDN_MACRO_CONNECTIONS gives ['VGND', 'VPWR']"
    elif case == "orientation":
        d.write_text(sram_def(orient="E"))
        expected = r"u_mem\[0\].sram: unsupported macro orientation E"
    elif case == "instance-missing":
        r = json.loads(resolved.read_text())
        r["MACROS"][SRAM]["instances"]["u_mem[1].sram"] = {}
        resolved.write_text(json.dumps(r))
        expected = f"{SRAM}: DEF instances ['{SRAM_INST}'] are not resolved.json's ['{SRAM_INST}', 'u_mem[1].sram']"
    else:                                                # N: the same full-height columns land the same way
        d.write_text(sram_def(orient="N"))
        expected = None
    failures, notes = macro_power(wt)
    if expected is None:
        assert failures == [] and len(notes) == 3
    else:
        assert expected in failures


def test_the_gates_have_no_inputs_in_an_archive_and_say_so(tmp_path, capsys):
    wt = candidate(tmp_path)
    args = complete_receipts(wt)
    for name in ("state_in.json", "config.json"):
        (wt / "runs/wokwi" / STA_STEP / name).unlink()
    shutil.rmtree(wt / "runs/wokwi/final/def")          # an archive keeps neither
    rc, text = release(args, capsys)
    assert rc == 0 and text.splitlines()[-1].endswith("3 RELEASE GATES SKIPPED)")
    assert len([line for line in text.splitlines() if line.startswith("release gate, SKIPPED: ")]) == 3
    rc, text = release(args + ["--require-sta-inputs"], capsys)
    assert rc == 1 and "--require-sta-inputs: 3 release gates would be skipped" in text


# ---- the shipping build (finding 2)

PLUGIN = '''HERE = "flow"
import os
def a(): return os.path.join(HERE, "odb_stripes.py")
def b(): return os.path.join(HERE, "step.tcl")
def c(): return [HERE + "/" + "extra_step.tcl"]   # named another way: "extra_step.tcl"
'''


def shipping(wt, tmp_path):
    """The run's plugin and flow scripts, and a shipping checkout with the same build
    inputs plus firmware and tests of its own."""
    (wt / "librelane_plugin_protoemu.py").write_text(PLUGIN)
    (wt / "flow").mkdir(exist_ok=True)
    (wt / "flow/odb_stripes.py").write_text("# stripes\n")
    (wt / "flow/step.tcl").write_text("source [file join [file dirname [info script]] helper.tcl]\n")
    (wt / "flow/helper.tcl").write_text("# helper\n")
    (wt / "flow/gen_config.py").write_text("# generator\n")
    (wt / "flow/extra_step.tcl").write_text("# extra step\n")
    ship = tmp_path / "ship"
    for name in ("src", "flow"):
        shutil.copytree(wt / name, ship / name)
    for name in ("info.yaml", "librelane_plugin_protoemu.py"):
        shutil.copyfile(wt / name, ship / name)
    (ship / "firmware").mkdir()
    (ship / "firmware/new.pasm").write_text("; firmware newer than the netlist\n")
    return ship


def with_shipping(args, ship):
    args = list(args)
    args[args.index("--shipping-root") + 1] = str(ship)
    return args


SHIP_FAULTS = {
    "tuned-config": ("src/config.json", '{"CLOCK_PERIOD":20,"PL_TARGET_DENSITY_PCT":34}',
                     "build input src/config.json differs (layout input)"),
    "core-count": ("src/project.v", "`define PE_NCORES 3\n", "build input src/project.v differs (layout input)"),
    "plugin": ("librelane_plugin_protoemu.py", PLUGIN + "# changed\n",
               "build input librelane_plugin_protoemu.py differs (layout input)"),
    "executed-script": ("flow/odb_stripes.py", "# other stripes\n", "build input flow/odb_stripes.py differs"),
    "sourced-tcl": ("flow/helper.tcl", "# other helper\n", "build input flow/helper.tcl differs"),
    # 092 finding 5: the generator is not read by the build; CI's check of it is
    "generator-check-fails": ("flow/gen_config.py", "import sys\nsys.exit('src/config.json differs from the preset')\n",
                              "the checkout's gen_config.py --preset submission --check fails"),
    # 092 finding 6: a script the plugin names otherwise than os.path.join(HERE, ...)
    "named-script": ("flow/extra_step.tcl", "# other extra step\n", "build input flow/extra_step.tcl differs"),
    "tiles": ("info.yaml", "project:\n  top_module: design\n  tiles: \"8x2\"\n", "build input info.yaml ("),
    "corner-tcl": ("src/timing_checks.tcl", "# other report\n",
                   "build input src/timing_checks.tcl differs (signoff STA only)"),
    "added-source": ("src/extra.sv", "module extra; endmodule\n", "build input src/extra.sv only in the shipping"),
}


@pytest.mark.parametrize("fault", sorted(SHIP_FAULTS))
def test_a_run_built_from_other_inputs_than_the_shipping_commit_cannot_release(tmp_path, capsys, fault):
    wt = candidate(tmp_path)
    args = complete_receipts(wt)
    ship = shipping(wt, tmp_path)
    rc, text = release(with_shipping(args, ship), capsys)
    assert rc == 0, text
    name, content, expected = SHIP_FAULTS[fault]
    (ship / name).write_text(content)
    rc, text = release(with_shipping(args, ship), capsys)
    assert rc == 1 and f"shipping build: {expected}" in text


def test_the_old_binding_compares_verilog_only(tmp_path):
    # before 092, receipts compared the validation/shipping checkout with the run on
    # src/*.v and *.sv only: a tuned config or another plugin went unnoticed
    rec = module("release_receipts")
    wt = candidate(tmp_path)
    ship = shipping(wt, tmp_path)
    (ship / "src/config.json").write_text('{"CLOCK_PERIOD":20,"PL_TARGET_DENSITY_PCT":34}')
    (ship / "librelane_plugin_protoemu.py").write_text(PLUGIN + "# changed\n")
    assert rec.hardware_files(wt) == rec.hardware_files(ship)
    bm = module("build_manifest")
    failures, _ = bm.compare(bm.run_manifest(wt), bm.checkout_manifest(ship))
    assert len(failures) == 2


@pytest.mark.parametrize("change", ["firmware", "tests", "datasheet-text", "unexecuted-flow-tool", "generated-config",
                                    "generator-text"])
def test_what_is_not_a_build_input_may_differ(tmp_path, capsys, change):
    # validating new firmware on a pinned netlist stays a separate, legitimate operation
    wt = candidate(tmp_path)
    if change == "generated-config":            # tt_tool's, made in the run at build time
        (wt / "src/config_merged.json").write_text("{}\n")
        (wt / "src/user_config.json").write_text("{}\n")
    args = complete_receipts(wt)
    ship = shipping(wt, tmp_path)
    for name in ("config_merged.json", "user_config.json"):
        (ship / "src" / name).unlink(missing_ok=True)
    if change == "firmware":
        (ship / "firmware/usb.pasm").write_text("; new\n")
    elif change == "tests":
        (ship / "test").mkdir()
        (ship / "test/test_new.py").write_text("# new\n")
    elif change == "datasheet-text":
        (ship / "info.yaml").write_text("project:\n  top_module: design\n  description: \"another\"\n"
                                        "pinout:\n  ui[0]: \"P16\"\n")
    elif change == "unexecuted-flow-tool":
        (ship / "flow/rmii_sta.tcl").write_text("# a standalone STA script\n")
    elif change == "generator-text":        # 092 finding 5: a preset changed to the run's tuned settings
        (ship / "flow/gen_config.py").write_text("# generator whose submission preset is the run's config\n")
    rc, text = release(with_shipping(args, ship), capsys)
    assert rc == 0 and "shipping build: the run's build inputs equal" in text
    if change == "generator-text":
        assert "flow/gen_config.py differs (the generator, not read by the build)" in text


def test_without_a_shipping_root_the_verdict_says_so(tmp_path, capsys):
    wt = candidate(tmp_path)
    args = complete_receipts(wt)
    i = args.index("--shipping-root")
    args = args[:i] + args[i + 2:]
    rc, text = release(args, capsys)
    assert rc == 0 and text.splitlines()[-1] == "RELEASE PASSED (SDF profile v2, slow target 50 MHz, " \
                                                "1 RELEASE GATES SKIPPED)"
    rc, text = release(args + ["--require-sta-inputs"], capsys)
    assert rc == 1


def test_a_missing_shipping_checkout_is_refused_not_listed(tmp_path, capsys):
    wt = candidate(tmp_path)
    rc, text = release(with_shipping(complete_receipts(wt), tmp_path / "deleted-clone"), capsys)
    assert rc == 1 and "not a checkout (no src/)" in text and "only in the run" not in text


def test_a_relative_shipping_root_works(tmp_path, capsys, monkeypatch):
    # found on runner1 (092b): the generator check ran the relative path from inside the checkout
    wt = candidate(tmp_path)
    args = complete_receipts(wt)
    shipping(wt, tmp_path)
    monkeypatch.chdir(tmp_path)
    rc, text = release(with_shipping(args, "ship"), capsys)
    assert rc == 0 and "gen_config.py --preset submission --check:  (rc 0)" in text


def gds(stamp, payload=b"\x00\x0c\x08\x00BOUNDARY"):
    """A minimal GDSII stream: BGNLIB and BGNSTR with dates, and one record."""
    dates = bytes([0, stamp] * 12)
    return (b"\x00\x1c\x01\x02" + dates + b"\x00\x1c\x05\x02" + dates + payload + b"\x00\x04\x04\x00")


def test_gds_normalization_zeroes_only_the_dates(tmp_path):
    bm = module("build_manifest")
    a, b, c = tmp_path / "a.gds", tmp_path / "b.gds", tmp_path / "c.gds"
    a.write_bytes(gds(1))
    b.write_bytes(gds(7))
    c.write_bytes(gds(1, payload=b"\x00\x0c\x08\x00BOUNDARZ"))
    assert bm.digest(a) != bm.digest(b)
    assert bm.normalized_gds_digest(a) == bm.normalized_gds_digest(b) != bm.normalized_gds_digest(c)


def test_two_builds_compare_tools_and_outputs(tmp_path):
    bm = module("build_manifest")
    wt = candidate(tmp_path)
    shipping(wt, tmp_path)
    tt = wt / "tt"
    (tt / ".git/refs/heads").mkdir(parents=True)
    (tt / ".git/HEAD").write_text("ref: refs/heads/ihp-sg13cmos5l\n")
    (tt / ".git/refs/heads/ihp-sg13cmos5l").write_text("ab" * 20 + "\n")
    (tt / "tt_tool.py").write_text("# tool\n")
    (wt / "runs/wokwi/final/gds/design.gds").write_bytes(gds(1))
    run = bm.run_manifest(wt)
    assert run["tools"]["tt_support_tools"]["commit"] == "ab" * 20 and run["tools"]["librelane_version"] is None
    other = tmp_path / "rebuild"
    shutil.copytree(wt, other)
    (other / "runs/wokwi/final/gds/design.gds").write_bytes(gds(9))   # a rebuild: other dates
    assert bm.compare(run, bm.run_manifest(other))[0] == []
    (other / "tt/tt_tool.py").write_text("# another tool\n")
    (other / "runs/wokwi/final/gds/design.gds").write_bytes(gds(9, payload=b"\x00\x0c\x08\x00BOUNDARZ"))
    failures, _ = bm.compare(run, bm.run_manifest(other))
    assert [f.split(" ")[0] for f in failures] == ["tt_support_tools", "gds_normalized"]
    # a checkout records no build: its tools are compared only when required
    assert bm.compare(run, bm.checkout_manifest(wt))[0] == []
    assert bm.compare(run, bm.checkout_manifest(wt), require_tools=True)[0]
    out = tmp_path / "m.json"
    assert bm.main(["write", str(wt), "--out", str(out)]) == 0
    assert bm.main(["write", str(wt), "--out", str(out)]) == 2      # never overwrites
    assert bm.main(["compare", str(out), str(wt)]) == 0
    assert bm.main(["compare", str(out), str(other)]) == 1


# ---- release_evidence and archive_evidence

def test_release_evidence_binds_the_shipping_build_and_the_checks():
    ev = module("release_evidence")
    cmd = ev.release_command(Path("/r"), Path("/V"), [("checks", "/c/receipt.json")])
    assert cmd[cmd.index("--shipping-root") + 1] == "/V" and cmd[cmd.index("--checks-receipt") + 1] == "/c/receipt.json"
    cmd = ev.release_command(Path("/r"), Path("/V"), [], shipping=Path("/S"))
    assert cmd[cmd.index("--shipping-root") + 1] == "/S" and "--require-sta-inputs" in cmd
    skipped = "RELEASE PASSED (SDF profile v2, slow target 50 MHz, 1 RELEASE GATES SKIPPED)"
    assert "skipped release gates" in ev.verdict_failures(0, skipped)[0]


def test_archive_refuses_skipped_gates_and_a_missing_checks_receipt(tmp_path):
    from test_flow_tools import evidence_bundle, flow_module
    ar = flow_module("archive_evidence")
    wt, verdict, paths = evidence_bundle(tmp_path)
    ar.collect(verdict, ar.Manifest("/"))
    with pytest.raises(ValueError, match="skipped release gates"):
        ar.collect(dict(verdict, verdict=verdict["verdict"][:-1] + ", 1 RELEASE GATES SKIPPED)"), ar.Manifest("/"))
    without = dict(verdict, receipts=[r for r in verdict["receipts"] if r["kind"] != "checks"])
    with pytest.raises(ValueError, match="lacks one clock/reset checks receipt"):
        ar.collect(without, ar.Manifest("/"))


def test_a_flow_file_the_config_names_is_a_build_input(tmp_path):
    # 096 finding 4: --pnr-max-transition names flow/protoemu_pnr_transition.sdc only in
    # src/config.json; an edit to it alone must make the builds differ
    bm = module("build_manifest")
    wt = candidate(tmp_path)
    (wt / "src/config.json").write_text(
        '{"CLOCK_PERIOD":20,"PNR_SDC_FILE":"dir::../flow/protoemu_pnr_transition.sdc","X":["dir::../flow/extra.tcl"]}')
    ship = shipping(wt, tmp_path)
    shutil.copyfile(wt / "src/config.json", ship / "src/config.json")
    for root in (wt, ship):
        (root / "flow/protoemu_pnr_transition.sdc").write_text("set_max_transition 1.0 [current_design]\n")
        (root / "flow/extra.tcl").write_text("# named by the config\n")
    assert [k for k in bm.config_paths(wt)[0] if k.startswith("flow/")] == ["flow/extra.tcl", "flow/protoemu_pnr_transition.sdc"]
    assert "flow/protoemu_pnr_transition.sdc" in bm.build_inputs(wt)
    assert bm.compare(bm.run_manifest(wt), bm.checkout_manifest(ship))[0] == []
    (ship / "flow/protoemu_pnr_transition.sdc").write_text("set_max_transition 2.0 [current_design]\n")
    failures, _ = bm.compare(bm.run_manifest(wt), bm.checkout_manifest(ship))
    assert any("flow/protoemu_pnr_transition.sdc" in f for f in failures), failures
    (ship / "flow/protoemu_pnr_transition.sdc").unlink()
    failures, _ = bm.compare(bm.run_manifest(wt), bm.checkout_manifest(ship))
    assert any("flow/protoemu_pnr_transition.sdc" in f for f in failures), failures


@pytest.mark.parametrize("spelling,target", [
    ("dir::../flow/./x.sdc", "flow/x.sdc"), ("dir::../flow//x.sdc", "flow/x.sdc"),
    ("dir::../flow/sub/../x.sdc", "flow/x.sdc"), ("dir::../flow/my+f.sdc", "flow/my+f.sdc"),
    ("dir::../flow/a b.sdc", "flow/a b.sdc"), ("dir::fl/x.sdc", "flow/x.sdc"),
    ("dir::../tools/x.sdc", "tools/x.sdc")])
def test_every_allowed_spelling_of_a_config_named_file_binds_it(tmp_path, spelling, target):
    # 097b allowlist: a dir:: path without glob characters, resolved against src/
    # (normalized, symlinks followed), bound wherever it lies in the worktree
    bm = module("build_manifest")
    wt = candidate(tmp_path)
    (wt / "src/config.json").write_text(json.dumps({"CLOCK_PERIOD": 20, "PNR_SDC_FILE": spelling}))
    ship = shipping(wt, tmp_path)
    shutil.copyfile(wt / "src/config.json", ship / "src/config.json")
    for root in (wt, ship):
        (root / target).parent.mkdir(parents=True, exist_ok=True)
        (root / target).write_text("set_max_transition 1.0 [current_design]\n")
        (root / "src/fl").symlink_to("../flow")
    assert bm.config_paths(wt) == ({target: bm.digest(wt / target)}, [])
    assert bm.compare(bm.run_manifest(wt), bm.checkout_manifest(ship))[0] == []
    (ship / target).write_text("set_max_transition 2.0 [current_design]\n")
    failures, _ = bm.compare(bm.run_manifest(wt), bm.checkout_manifest(ship))
    assert any(target in f for f in failures), failures


# 097b: each escape the verifier measured as SAME BUILD, and the earlier outside forms
REFUSED = ["tools/x.sdc",                    # unprefixed: LibreLane reads it from the working directory
           "../x.sdc", "tools/xt",           # unprefixed, outside; extensionless
           "pdk_dir::../../tools/x.sdc",     # pdk_dir:: climbing out of the PDK
           "dir::../flow/x[1].sdc",          # glob characters (LibreLane reads an existing name literally)
           "dir::../flow/*.sdc", "dir::../flow/x.s?c",
           "refg::$PDK_ROOT/../../x.sdc", "refg::$DESIGN_DIR/../flow/x.sdc",   # refg:: in any form
           "dir::../../flow/x.sdc", "/elsewhere/flow/x.sdc", "dir::/elsewhere/x.sdc", "refg::/elsewhere/x.sdc",
           "dir::../flow/absent.sdc",        # resolves to nothing
           # 097c: paths without a "/" or a listed suffix, which LibreLane accepts for
           # Path and List[Path] variables; a Path-typed key makes them paths
           "..", ".",                        # unprefixed: the worktree's parent, the root
           "dir::..", "dir::../tools",       # the root (dir:: adds the "/"); a directory outside src/
           "pdk_dir::..",                    # the PDK root
           "xt", "LICENSE", "README.md",     # root-level files: no extension, an unlisted suffix
           "refg::$DESIGN_DIR"]


@pytest.mark.parametrize("key", ["PNR_SDC_FILE", "VERILOG_INCLUDE_DIRS"])
@pytest.mark.parametrize("spelling", REFUSED)
def test_a_config_path_outside_the_allowlist_is_refused(tmp_path, spelling, key):
    # through a Path variable and a List[Path] variable
    bm = module("build_manifest")
    wt = candidate(tmp_path)
    value = spelling if key == "PNR_SDC_FILE" else [spelling]
    (wt / "src/config.json").write_text(json.dumps({"CLOCK_PERIOD": 20, key: value}))
    ship = shipping(wt, tmp_path)
    shutil.copyfile(wt / "src/config.json", ship / "src/config.json")
    for root in (wt, ship):            # the files these spellings could name exist in the worktree
        for f in ("tools/x.sdc", "tools/xt", "flow/x[1].sdc", "flow/x.sdc", "flow/x1.sdc",
                  "xt", "LICENSE", "README.md"):
            (root / f).parent.mkdir(parents=True, exist_ok=True)
            (root / f).write_text("# a file\n")
    assert bm.config_paths(wt) == ({}, [spelling])
    assert bm.run_manifest(wt)["refused"] == [spelling]
    failures, _ = bm.compare(bm.run_manifest(wt), bm.checkout_manifest(ship))
    assert any("outside the worktree" in f for f in failures)
    # the generator refuses to write such a config ("absent" is the manifest's
    # check alone: the generator does not require the file to exist)
    r = subprocess.run([sys.executable, str(ROOT / "flow/gen_config.py"), "--preset", "experiment",
                        "--set", f"{key}={json.dumps(value)}", "--output", str(tmp_path / "c.json")],
                       capture_output=True, text=True)
    if spelling == "dir::../flow/absent.sdc":
        assert r.returncode == 0
    else:
        assert r.returncode != 0 and "outside the allowlist" in r.stderr and not (tmp_path / "c.json").exists()


def test_a_config_named_directory_binds_every_file_under_it(tmp_path):
    # a directory inside src/ (097c: the root and directories outside src/ are refused)
    bm = module("build_manifest")
    wt = candidate(tmp_path)
    (wt / "src/config.json").write_text(json.dumps({"VERILOG_INCLUDE_DIRS": ["dir::inc"]}))
    ship = shipping(wt, tmp_path)
    shutil.copyfile(wt / "src/config.json", ship / "src/config.json")
    for root in (wt, ship):
        (root / "src/inc/sub").mkdir(parents=True)
        (root / "src/inc/h.vh").write_text("`define A 1\n")
        (root / "src/inc/sub/g.vh").write_text("`define B 1\n")
    assert bm.config_paths(wt) == ({"src/inc/h.vh": bm.digest(wt / "src/inc/h.vh"),
                                    "src/inc/sub/g.vh": bm.digest(wt / "src/inc/sub/g.vh")}, [])
    assert bm.compare(bm.run_manifest(wt), bm.checkout_manifest(ship))[0] == []
    (ship / "src/inc/sub/g.vh").write_text("`define B 2\n")
    failures, _ = bm.compare(bm.run_manifest(wt), bm.checkout_manifest(ship))
    assert any("src/inc/sub/g.vh" in f for f in failures), failures
    r = subprocess.run([sys.executable, str(ROOT / "flow/gen_config.py"), "--preset", "experiment",
                        "--set", 'VERILOG_INCLUDE_DIRS=["dir::inc"]', "--output", str(tmp_path / "c.json")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("case", ["outside-file", "outside-member", "dangling-member", "member-loop", "two-loops"])
def test_an_existing_target_outside_the_worktree_or_a_broken_member_is_refused(tmp_path, case):
    # 097c finding 2: the outside file exists, so only the inside checks refuse it;
    # a dangling member or a symlink loop is a named refusal, not a crash
    bm = module("build_manifest")
    wt = candidate(tmp_path)
    (tmp_path / "outside.vh").write_text("`define OUT 1\n")
    (wt / "src/inc").mkdir()
    (wt / "src/inc/h.vh").write_text("`define A 1\n")
    spelling = "dir::out.vh" if case == "outside-file" else "dir::inc"
    if case == "outside-file":
        (wt / "src/out.vh").symlink_to(tmp_path / "outside.vh")
    elif case == "outside-member":
        (wt / "src/inc/o.vh").symlink_to(tmp_path / "outside.vh")
    elif case == "dangling-member":
        (wt / "src/inc/gone.vh").symlink_to(wt / "src/inc/absent.vh")
    elif case == "member-loop":
        (wt / "src/inc/sub").mkdir()
        (wt / "src/inc/sub/loop").symlink_to("..")
    else:
        # 097d finding 2: two loop links make an unguarded walk grow as 2^40, so
        # only the guard refuses this in time (a subprocess under a timeout)
        (wt / "src/inc/sub").mkdir()
        (wt / "src/inc/sub/l0").symlink_to("..")
        (wt / "src/inc/sub/l1").symlink_to("..")
    (wt / "src/config.json").write_text(json.dumps({"VERILOG_INCLUDE_DIRS": [spelling]}))
    if case == "two-loops":
        code = ("import importlib.util, json, sys; s = importlib.util.spec_from_file_location('bm', sys.argv[1]); "
                "m = importlib.util.module_from_spec(s); s.loader.exec_module(m); print(json.dumps(m.config_paths(sys.argv[2])))")
        r = subprocess.run([sys.executable, "-c", code, str(ROOT / "flow/build_manifest.py"), str(wt)],
                           capture_output=True, text=True, timeout=30)
        assert r.returncode == 0 and json.loads(r.stdout) == [{}, [spelling]], r.stderr
        return
    assert bm.config_paths(wt) == ({}, [spelling])
    assert bm.run_manifest(wt)["refused"] == [spelling]


@pytest.mark.parametrize("config,refused", [
    ({"X": "y.sdc"}, "y.sdc"),                                  # an unknown key: the suffix backstop (absent)
    ({"X": ["tools/absent"]}, "tools/absent"),                  # an unknown key: the "/" backstop (absent)
    ({"MACROS": {"m": {"gds": ["tools/m.gds"]}}}, "tools/m.gds"),   # nested Path fields
    ({"FOO_CELL": "../x.sdc"}, "../x.sdc"),                     # the cell/pin carve-out is the shape only
    ({"X": ".."}, ".."), ({"X": "pdk_dir::.."}, "pdk_dir::..")])   # "." and "..", prefixes: whatever the key
def test_the_shape_rule_backstops_keys_that_are_not_path_typed(tmp_path, config, refused):
    bm = module("build_manifest")
    wt = candidate(tmp_path)
    for f in ("x.sdc", "tools/xt", "tools/m.gds"):
        (wt / f).parent.mkdir(parents=True, exist_ok=True)
        (wt / f).write_text("# a file\n")
    (wt / "src/config.json").write_text(json.dumps(config))
    assert bm.config_paths(wt) == ({}, [refused])
    (key, value), = config.items()
    r = subprocess.run([sys.executable, str(ROOT / "flow/gen_config.py"), "--preset", "experiment",
                        "--set", f"{key}={json.dumps(value)}", "--output", str(tmp_path / "c.json")],
                       capture_output=True, text=True)
    assert r.returncode != 0 and "outside the allowlist" in r.stderr and not (tmp_path / "c.json").exists()


def test_relative_worktree_arguments_compare_identical_trees_equal(tmp_path, monkeypatch):
    # 097b: keys are relative to the resolved worktree root, so the same tree
    # named by a relative path compares SAME BUILD from any directory
    bm = module("build_manifest")
    wt = candidate(tmp_path)
    (wt / "src/config.json").write_text(json.dumps({"PNR_SDC_FILE": "dir::../flow/x.sdc"}))
    ship = shipping(wt, tmp_path)
    shutil.copyfile(wt / "src/config.json", ship / "src/config.json")
    for root in (wt, ship):
        (root / "flow/x.sdc").write_text("# sdc\n")
    monkeypatch.chdir(tmp_path)
    assert bm.compare(bm.run_manifest(wt.name), bm.checkout_manifest(ship.name))[0] == []
    assert bm.run_manifest(wt.name)["files"] == bm.run_manifest(wt)["files"]


# 097d: each way LibreLane sets a Path variable other than its canonical top-level
# key, measured as SAME BUILD with a root-level xt, LICENSE or README.md; and the
# key-independent backstop: a name that exists relative to the root or src/
ESCAPES_097D = [
    ({"pdk::*": {"PNR_SDC_FILE": "xt"}}, ["pdk::*"]),          # flattened into the top level: refused
    ({"pdk::ihp*": {"VERILOG_INCLUDE_DIRS": ["LICENSE"]}}, ["pdk::ihp*"]),
    ({"scl::*": {"PNR_SDC_FILE": "README.md"}}, ["scl::*"]),
    ({"FP_PDN_CFG": "xt"}, ["xt"]),                             # deprecated names
    ({"BASE_SDC_FILE": "xt", "SDC_FILE": "LICENSE"}, ["LICENSE", "xt"]),
    ({"LINTER_VLT": "xt"}, ["xt"]),
    ({"FP_PIN_ORDER_CFG": "xt"}, ["xt"]),
    ({"VERILOG_FILES_BLACKBOX": ["xt"]}, ["xt"]),
    ({"EXTRA_GDS_FILES": ["xt"]}, ["xt"]),
    ({"MACROS": {"m": {"gds": ["xt"], "lef": ["LICENSE"]}}}, ["LICENSE", "xt"]),   # unprefixed Macro fields
    ({"MACROS": {"m": {"lib": {"*_slow_*": ["absent"]}}}}, ["absent"]),            # a field by name, any depth
    ({"PAD_LEFS": ["xt"]}, ["xt"]),                             # PAD_* (the 6 the 097c census missed)
    ({"FP_PDN_CFG": "absent"}, ["absent"]),                     # by name alone: no file needed
    ({"X": "xt"}, ["xt"]), ({"X": ["LICENSE"]}, ["LICENSE"]),   # an unknown key: the name exists
    ({"X": {"y": "README.md"}}, ["README.md"]), ({"X": "docs"}, ["docs"]),
    ({"X": "macros"}, ["macros"]),                              # relative to src/
    ({"DIODE_CELL": "cellx/A"}, ["cellx/A"]),                   # a cell/pin that exists as a path
]


@pytest.mark.parametrize("config,refused", ESCAPES_097D)
def test_every_other_way_to_set_a_path_is_refused(tmp_path, config, refused):
    bm = module("build_manifest")
    wt = candidate(tmp_path)
    for f in ("xt", "LICENSE", "README.md", "docs/a.md", "src/macros/m.v", "cellx/A"):
        (wt / f).parent.mkdir(parents=True, exist_ok=True)
        (wt / f).write_text("# a file\n")
    (wt / "src/config.json").write_text(json.dumps(dict(config, CLOCK_PERIOD=20)))
    ship = shipping(wt, tmp_path)
    shutil.copyfile(wt / "src/config.json", ship / "src/config.json")
    assert bm.config_paths(wt) == ({}, refused)
    failures, _ = bm.compare(bm.run_manifest(wt), bm.checkout_manifest(ship))
    assert any("outside the worktree" in f for f in failures)
    # the generator, on the same tree (it checks names against its own root)
    gen = module("gen_config")
    assert sorted(set(gen.paths_outside_repo(config, root=wt))) == refused
    # and through the CLI on this repository, where LICENSE, README.md, docs and src/macros exist
    if "MACROS" not in config and all(r in ("pdk::*", "pdk::ihp*", "scl::*", "LICENSE", "README.md", "docs", "macros",
                                            "absent") for r in refused):
        args = []
        for k, v in config.items():
            args += ["--set", f"{k}={json.dumps(v)}"]
        r = subprocess.run([sys.executable, str(ROOT / "flow/gen_config.py"), "--preset", "experiment", *args,
                            "--output", str(tmp_path / "c.json")], capture_output=True, text=True)
        assert r.returncode != 0 and "outside the allowlist" in r.stderr and not (tmp_path / "c.json").exists()


# LibreLane 3.1.0.dev3's Path-typed variables and their deprecated names, recorded
# from the flow image (097d's census: 59 names, plus 26 deprecated ones)
PATH_KEYS_CENSUS = set("""
CELL_BB_VERILOG_MODELS CELL_CDLS CELL_GDS CELL_LEFS CELL_LIBS CELL_MAGLEFS CELL_MAGS CELL_SPICE_MODELS
CELL_VERILOG_MODELS DESIGN_DIR EQY_SCRIPT EXTRA_CDLS EXTRA_GDS EXTRA_LEFS EXTRA_LIBS EXTRA_SPEFS
EXTRA_SPICE_MODELS EXTRA_VERILOG_MODELS FALLBACK_SDC FP_DEF_TEMPLATE FP_TRACKS_INFO IO_PIN_ORDER_CFG
KLAYOUT_DEF_LAYER_MAP KLAYOUT_DRC_RUNSET KLAYOUT_PROPERTIES KLAYOUT_TECH LINTER_VLTS MACRO_PLACEMENT_CFG
MAGICRC MAGIC_DRC_MAGLEFS MAGIC_PDK_SETUP MAGIC_TECH NETGEN_SETUP PAD_CDLS PAD_GDS PAD_LEFS PAD_LIBS
PAD_SPICE_MODELS PAD_VERILOG_MODELS PDK_ROOT PDN_CFG PNR_EXCLUDED_CELL_FILE PNR_SDC_FILE RCX_RULESETS
RCX_SDC_FILE SIGNOFF_SDC_FILE STA_EXTRA_CORNER_TCL_FILE SYNTH_ABC_STRATEGY_SCRIPT SYNTH_CSA_MAP
SYNTH_EXCLUDED_CELL_FILE SYNTH_EXTRA_MAPPING_FILE SYNTH_FA_MAP SYNTH_LATCH_MAP SYNTH_RCA_MAP
SYNTH_TRISTATE_MAP TECH_LEFS VERILOG_FILES VERILOG_INCLUDE_DIRS VSRC_LOC_FILES
""".split())
DEPRECATED_CENSUS = set("""
STD_CELL_LIBRARY_CDL GDS_FILES CELLS_GDS CELLS_LEF LIB EXTRA_GDS_FILES VERILOG_FILES_BLACKBOX
FALLBACK_SDC_FILE BASE_SDC_FILE SDC_FILE TRACKS_INFO_FILE FP_PIN_ORDER_CFG KLAYOUT_DRC_TECH_SCRIPT
LINTER_VLT MAGIC_MAGICRC MAGIC_TECH_FILE NETGEN_SETUP_FILE FP_PDN_CFG DRC_EXCLUDE_CELL_LIST
PNR_EXCLUSION_CELL_LIST CARRY_SELECT_ADDER_MAP NO_SYNTH_CELL_LIST SYNTH_EXCLUSION_CELL_LIST
FULL_ADDER_MAP RIPPLE_CARRY_ADDER_MAP TRISTATE_BUFFER_MAP
""".split())


def test_both_files_name_every_path_variable_and_refuse_a_bare_name_under_each(tmp_path):
    # pins the census (097d: two of 53 keys were pinned), the same in both files,
    # and that each name alone makes a nonexistent bare name a refused path
    bm, gen = module("build_manifest"), module("gen_config")
    assert len(PATH_KEYS_CENSUS) == 59 and len(DEPRECATED_CENSUS) == 26
    assert bm.PATH_KEYS == gen.PATH_KEYS == PATH_KEYS_CENSUS | DEPRECATED_CENSUS
    assert bm.MACRO_PATH_FIELDS == gen.MACRO_PATH_FIELDS == set("gds lef vh nl pnl spef lib spice sdf json_h".split())
    assert bm.PATH_PREFIXES == gen.PATH_PREFIXES and bm.BLOCK_PREFIXES == gen.BLOCK_PREFIXES == ("pdk::", "scl::")
    wt = candidate(tmp_path)
    for k in sorted(bm.PATH_KEYS):
        (wt / "src/config.json").write_text(json.dumps({k: "absent_name"}))
        assert bm.config_paths(wt) == ({}, ["absent_name"]), k
        assert gen.paths_outside_repo({k: "absent_name"}, root=wt) == ["absent_name"], k
    # MACROS: only the Path fields by name; instances and orientation are not paths
    m = {"MACROS": {"m": {"instances": {"u": {"location": [1, 2], "orientation": "N"}}, "gds": ["dir::m.gds"]}}}
    (wt / "src/m.gds").write_text("gds")
    (wt / "src/config.json").write_text(json.dumps(m))
    assert bm.config_paths(wt) == ({"src/m.gds": bm.digest(wt / "src/m.gds")}, [])
    assert gen.paths_outside_repo(m, root=wt) == []


def test_a_name_that_does_not_exist_under_an_unknown_key_is_not_a_path(tmp_path):
    # the existence backstop names nothing in a shipped config (097e scan: 19
    # committed versions, 27 recorded experiment configs): plain values stay plain
    bm, gen = module("build_manifest"), module("gen_config")
    wt = candidate(tmp_path)
    c = {"DESIGN_NAME": "tt_um_x", "X": ["N", "Classic"], "SYNTH_DRIVING_CELL": "sg13cmos5l_buf_4/X", "DIODE_CELL": "cell/A"}
    (wt / "src/config.json").write_text(json.dumps(c))
    assert bm.config_paths(wt) == ({}, [])
    assert gen.paths_outside_repo(c, root=wt) == []
    shipped = json.loads((ROOT / "src/config.json").read_text())
    assert gen.paths_outside_repo(shipped) == []


def test_manifests_without_config_paths_outside_are_unchanged(tmp_path):
    # PDK paths and cell/pin names are not repository paths, and no "refused" key
    # appears when nothing is refused, so older manifests stay byte-identical
    bm = module("build_manifest")
    wt = candidate(tmp_path)
    (wt / "src/config.json").write_text(json.dumps({
        "DIODE_CELL": "sg13cmos5l_antennanp/A", "G": ["pdk_dir::libs.ref/x/gds/y.gds"],
        "//": "Generated by flow/gen_config.py", "//generator": {"output": "/abs/src/config.json"}}))
    assert bm.config_paths(wt) == ({}, [])
    assert "refused" not in bm.run_manifest(wt) and "refused" not in bm.checkout_manifest(wt)
