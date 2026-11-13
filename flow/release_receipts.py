#!/usr/bin/env python3
"""Automatic, hash-bound receipts for GL, SDF GL and Tiny Tapeout precheck.

These detect stale/mixed/incomplete local evidence; they are not signatures or
proof against somebody deliberately fabricating a receipt. Runners snapshot
inputs before execution and refuse to certify a run whose inputs changed.
"""
import argparse
import ast
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import re
import xml.etree.ElementTree as ET

SCHEMA = 1
GL_MODULES = ("test", "test_pio", "test_eth_service")
CORNERS = ("nom_typ_1p20V_25C", "nom_slow_1p08V_125C", "nom_fast_1p32V_m40C")
BASIC_TESTS = ("test_id", "test_uart_tx", "test_spi_master", "test_host_port_fast", "test_fetch_to_pin")
V2_TESTS = ("test_spi_master_fast", "test_la_capture_ticks", "test_call_ret")
RUNNER_ROOT = Path(__file__).resolve().parent


STA_METRICS = ("timing__setup__ws", "timing__hold__ws", "timing__setup_vio__count", "timing__hold_vio__count")
CLOCK_PERIOD_RE = r"^Clock: clk\n(?:(?!^Clock:).)*?^Period: ([0-9.]+)\s*$"
# OpenSTA holds a period in single precision: 20.832 ns prints as 20.832001
# (1.0e-6 off). 5e-6 ns (0.005 ps) still pins the period.
CLOCK_TOLERANCE_NS = 5e-6
STA_STEP_ID = "OpenROAD.STAPostPNR"
IMAGE_ID_RE = r"sha256:[0-9a-f]{64}"
# release_check's verdict when an STA receipt was checked without the run's STA
# inputs (an archive); release_evidence and archive_evidence refuse it
SKIPPED_VERDICT = "STA RECEIPT CHECKS SKIPPED"
# release_check's verdict when a release gate had no inputs (an evidence archive
# keeps neither the final DEF nor the step logs); refused the same way
GATES_SKIPPED_VERDICT = "RELEASE GATES SKIPPED"
# The clock/reset check STA (flow/check_rerun.py): two passes of the run's own
# STA step, each with a report Tcl. In "pulse" no path exception exists:
# OpenSTA 2.7.0 (LibreLane 3.1.0.dev3) reports no minimum pulse width check on
# any pin a min/max-type exception's -from clock drives (one each way between
# two clocks removes them all), while pulse widths depend on the clock network
# alone, which no path exception touches.
CHECK_PASSES = {"signoff": "check_types.tcl", "pulse": "pulse_checks.tcl"}
CHECK_REPORTS = {"signoff": "check_types.rpt", "pulse": "pulse_checks.rpt"}
CHECK_REPORT_END = "== end of checks"
PATH_EXCEPTIONS = ("set_max_delay", "set_min_delay", "set_false_path", "set_multicycle_path")
PATH_EXCEPTION_RE = r"\b(?:" + "|".join(PATH_EXCEPTIONS) + r")\b"
PULSE_SDC_PREAMBLE = (
    "# flow/check_rerun.py, pulse pass: every path-exception command is a no-op here\n"
    "# (pulse widths depend on none; OpenSTA 2.7.0 drops the pulse width checks of\n"
    "# the pins an exception's -from clock drives). The run's signoff SDC follows.\n"
    f"foreach protoemu_pulse_cmd {{{' '.join(PATH_EXCEPTIONS)}}} {{\n"
    "    proc $protoemu_pulse_cmd {args} {}\n"
    "}\n")


def runner_files(root):
    return {name: digest(Path(root) / name) for name in
            ("release_receipts.py", "gl_test.sh", "gl_sdf_test.sh", "precheck_test.py", "sdf_prep.py", "sta_rerun.py",
             "check_rerun.py", *CHECK_PASSES.values())
            if (Path(root) / name).is_file()}


def sta_step(worktree):
    return one(Path(worktree).glob("runs/wokwi/*-openroad-stapostpnr"), "final STA directory")


def run_base(worktree):
    """The worktree the run was hardened in (a release view's entries are symlinks to it)."""
    return Path(os.path.realpath(Path(worktree) / "runs/wokwi")).parents[1]


def sta_inputs(worktree):
    """Digests of every design file the run's final STA step read, by path in the
    worktree, and of the final SPEF (begin checks the step read that extraction)."""
    wt, base, step = Path(worktree), run_base(worktree), sta_step(worktree)
    state, config = read_json(step / "state_in.json"), read_json(step / "config.json")
    files = {f"runs/wokwi/{step.name}/{name}": digest(step / name) for name in ("state_in.json", "config.json")}
    final_spef = one((wt / "runs/wokwi/final/spef/nom").glob("*.spef"), "final SPEF")
    files[str(final_spef.relative_to(wt))] = digest(final_spef)
    paths = [state["nl"], state["sdc"], state["odb"], *state["spef"].values()]
    paths += [config[k] for k in ("SIGNOFF_SDC_FILE", "STA_EXTRA_CORNER_TCL_FILE") if config.get(k)]
    for p in map(Path, paths):   # LibreLane records absolute paths in the hardened worktree
        if not p.is_absolute() or not p.is_relative_to(base):
            raise ValueError(f"STA input outside the run's worktree: {p}")
        files[str(p.relative_to(base))] = digest(wt / p.relative_to(base))
    return files


def sta_metrics(out, corner):
    """The fresh STA's own timing metrics for one corner (from its sta.log, never inherited state)."""
    text = (Path(out) / "sta" / corner / "sta.log").read_text(errors="replace")
    found = {}
    for kind, key, value in re.findall(rf"^%OL_METRIC_([FI]) (\S+)__corner:{re.escape(corner)} (\S+)$", text, re.M):
        value = int(value) if kind == "I" else float(value)
        if key in found and found[key] != value:
            raise ValueError(f"{out}: {corner} reports {key} twice, as {found[key]} and {value}")
        found[key] = value
    metrics = {key: found.get(key) for key in STA_METRICS}
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in metrics.values()):
        raise ValueError(f"{out}: incomplete or non-finite STA metrics for {corner}: {metrics}")
    return metrics


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for part in iter(lambda: f.read(1024 * 1024), b""):
            h.update(part)
    return h.hexdigest()


def read_json(path):
    def invalid(value):
        raise ValueError(f"non-finite JSON number: {value}")
    return json.loads(Path(path).read_text(), parse_constant=invalid)


def write_new(path, value):
    with Path(path).open("x") as f:
        json.dump(value, f, sort_keys=True, indent=2, allow_nan=False)
        f.write("\n")


def one(paths, description):
    paths = list(paths)
    if len(paths) != 1:
        raise ValueError(f"expected exactly one {description}; found {len(paths)}")
    return paths[0]


def sta_recheck(worktree, data, out, *, allow_absent=False):
    """Recompute an STA receipt's run-side inputs: the step's input digests and
    its config (equal to the receipt's except CLOCK_PERIOD). With allow_absent,
    a tree where nothing at all stands at the step's state_in.json and
    config.json (an evidence archive keeps neither) has every recorded input
    that is present checked, the step netlist against final/nl (begin required
    them equal), and the checks it skipped are returned. In both modes the
    image's LibreLane version must be the run's (resolved.json, a candidate file)."""
    wt, plan = Path(worktree), data["plan"]
    recorded, step = plan["inputs"], sta_step(worktree)
    if step.name != plan["step"]:
        raise ValueError("the receipt names another STA step")
    run_version = read_json(wt / "runs/wokwi/resolved.json").get("meta", {}).get("librelane_version")
    if plan.get("librelane_version") != run_version:
        raise ValueError(f"STA image has LibreLane {plan.get('librelane_version')}, not the run's {run_version} "
                         "(resolved.json)")
    if allow_absent and not any(os.path.lexists(step / name) for name in ("state_in.json", "config.json")):
        absent = sorted(name for name in recorded if not os.path.lexists(wt / name))
        irregular = sorted(name for name in recorded if name not in absent and not (wt / name).is_file())
        if irregular:
            raise ValueError(f"STA inputs are not regular files: {', '.join(irregular)}")
        changed = sorted(name for name in recorded if name not in absent and digest(wt / name) != recorded[name])
        final_nl = one((wt / "runs/wokwi/final/nl").glob("*.nl.v"), "final netlist")
        netlists = [name for name in absent if name.endswith(".nl.v")]
        changed += [name for name in netlists if recorded[name] != digest(final_nl)]
        if changed:
            raise ValueError(f"STA inputs differ from the receipt's: {', '.join(sorted(changed))}")
        unchecked = [name for name in absent if name not in netlists]
        return [f"STA inputs not recomputed: the run's {step.name}/state_in.json and config.json are absent; "
                f"{len(recorded) - len(unchecked)} of {len(recorded)} recorded input files checked "
                f"(the step netlist against final/nl), absent and not checked: {', '.join(unchecked)}",
                "STA step config not compared with the run's: its config.json is absent"]
    inputs = sta_inputs(wt)
    if inputs != recorded:
        changed = sorted(name for name in set(inputs) | set(recorded) if inputs.get(name) != recorded.get(name))
        raise ValueError(f"STA inputs differ from the receipt's: {', '.join(changed)}")
    # the step's own configuration with only the clock period changed
    config = read_json(step / "config.json")
    config["CLOCK_PERIOD"] = plan["period_ns"]
    if read_json(Path(out) / "config.json") != config:
        raise ValueError("STA step config differs from the run's beyond CLOCK_PERIOD")
    return []


def pulse_sdc(text):
    """The pulse-width pass's SDC (flow/check_rerun.py): the run's signoff SDC
    unchanged, after a preamble that makes every path-exception command a
    no-op, so no exception exists in whatever form or Tcl construct it is
    written; and, for the record, the SDC's lines that name one."""
    named = [line.rstrip("\n") for line in text.splitlines()
             if re.search(PATH_EXCEPTION_RE, line) and not line.lstrip().startswith("#")]
    return PULSE_SDC_PREAMBLE + text, named


def check_configs(worktree, out):
    """The two passes' expected step configs: the run's own, with only the
    report Tcl (and, for "pulse", the signoff SDC) pointing into the receipt."""
    config = read_json(sta_step(worktree) / "config.json")
    expected = {}
    for name, tcl in CHECK_PASSES.items():
        c = dict(config, STA_EXTRA_CORNER_TCL_FILE=str(Path(out) / name / tcl))
        if name == "pulse":
            c["SIGNOFF_SDC_FILE"] = str(Path(out) / name / "pulse.sdc")
        expected[name] = c
    return expected


def checks_recheck(worktree, data, out, *, allow_absent=False):
    """A checks receipt against the run: the step and its LibreLane version, the
    pulse pass's SDC derived from the run's signoff SDC, and (live) the step's
    input digests and both passes' configs. With allow_absent, a tree where
    nothing stands at the step's state_in.json and config.json (an evidence
    archive) has its present inputs checked and the skipped checks returned."""
    # (worded apart from sta_recheck: test/mutants.py finds each mutation site by its text)
    wt, plan, step = Path(worktree), data["plan"], sta_step(worktree)
    recorded = plan["inputs"]
    if plan["step"] != step.name:
        raise ValueError("the checks receipt names another STA step")
    version = read_json(wt / "runs/wokwi/resolved.json").get("meta", {}).get("librelane_version")
    if version != plan.get("librelane_version"):
        raise ValueError(f"checks STA image has LibreLane {plan.get('librelane_version')}, not the run's {version}")
    sdc = [name for name in recorded if name.endswith(".sdc") and not name.startswith("runs/")]
    if len(sdc) != 1:
        raise ValueError(f"expected one signoff SDC among the recorded inputs, found {sdc}")
    if digest(wt / sdc[0]) != recorded[sdc[0]]:
        raise ValueError(f"the run's signoff SDC differs from the receipt's: {sdc[0]}")
    text, removed = pulse_sdc((wt / sdc[0]).read_text())
    if removed != plan["sdc_removed"] or (Path(out) / "pulse/pulse.sdc").read_text() != text:
        raise ValueError("the pulse pass's SDC is not the run's signoff SDC with its path exceptions disabled")
    archive = not any(os.path.lexists(step / n) for n in ("state_in.json", "config.json"))
    if allow_absent and archive:
        present = [n for n in recorded if os.path.lexists(wt / n)]
        differ = sorted(n for n in present if not (wt / n).is_file() or digest(wt / n) != recorded[n])
        if differ:
            raise ValueError(f"checks STA inputs differ from the receipt's: {', '.join(differ)}")
        return [f"checks STA inputs not recomputed: the run's {step.name}/state_in.json and config.json are absent; "
                f"{len(present)} of {len(recorded)} recorded input files checked",
                "checks STA pass configs not compared with the run's: its config.json is absent"]
    now = sta_inputs(wt)
    differ = sorted(n for n in set(now) | set(recorded) if now.get(n) != recorded.get(n))
    if differ:
        raise ValueError(f"checks STA inputs differ from the receipt's: {', '.join(differ)}")
    for name, config in check_configs(wt, data["output_directory"]).items():
        if read_json(Path(out) / name / "config.json") != config:
            raise ValueError(f"checks STA {name} pass config differs from the run's beyond its report Tcl and SDC")
    return []


def candidate_files(worktree):
    wt = Path(worktree)
    run = wt / "runs/wokwi"
    files = [wt / "info.yaml", run / "resolved.json", run / "final/metrics.json"]
    for folder, pattern in (("nl", "*.nl.v"), ("gds", "*.gds"), ("lef", "*.lef")):
        files.append(one((run / "final" / folder).glob(pattern), folder))
    for corner in CORNERS:
        files.append(one((run / "final/sdf" / corner).glob("*.sdf"), f"{corner} SDF"))
    sta = one(run.glob("*-openroad-stapostpnr"), "final STA directory")
    for corner in CORNERS:
        files += [sta / corner / name for name in ("sta.log", "clock.rpt")]
    sources = sorted((wt / "src").rglob("*"))
    files += [p for p in sources if p.is_file() and p.suffix in (".v", ".sv", ".sdc", ".tcl", ".json")]
    if not (wt / "src/project.v").is_file():
        raise ValueError("missing hardware profile src/project.v")
    return {str(p.relative_to(wt)): digest(p) for p in files}


def hardware_files(root):
    root = Path(root)
    return {str(p.relative_to(root)): digest(p) for p in sorted((root / "src").rglob("*"))
            if p.is_file() and p.suffix in (".v", ".sv")}


def validation_files(root, *, include_hardware=False):
    root = Path(root)
    files = []
    for directory, suffixes in (("test", (".py", ".v", ".sv", ".vh", ".svh", ".json")),
                                ("tools", (".py",)), ("firmware", (".pasm",))):
        # GL imports test/model helpers and loads the compiled PIO JSON fixture.
        # Prune generated output before traversal: private precheck copies and
        # simulator netlists also have source suffixes, but are not inputs.
        for parent, directories, names in os.walk(root / directory):
            directories[:] = [name for name in directories
                              if name not in {"sim_build", "__pycache__", ".pytest_cache", ".git"}]
            files.extend(Path(parent) / name for name in names
                         if Path(name).suffix in suffixes and (Path(parent) / name).is_file())
    if (root / "test/Makefile").exists():
        files.append(root / "test/Makefile")
    if include_hardware:
        files += [p for p in (root / "src").rglob("*") if p.is_file() and p.suffix in (".v", ".sv")]
    return {str(p.relative_to(root)): digest(p) for p in sorted(files)}


def test_names(path):
    """Static cocotb tests only; unsupported/dynamic declarations fail closed."""
    names = []
    for node in ast.parse(Path(path).read_text()).body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            fn = decorator.func if isinstance(decorator, ast.Call) else decorator
            if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name) and fn.value.id == "cocotb" and fn.attr == "test":
                if isinstance(decorator, ast.Call) and (decorator.args or decorator.keywords):
                    raise ValueError(f"{path}: parameterized cocotb decorator needs an explicit test plan")
                names.append(node.name)
    if not names or len(set(names)) != len(names):
        raise ValueError(f"{path}: missing or duplicate static cocotb tests")
    return names


def check_xml(path, names=None, expect="pass", *, allow_filtered=False):
    root = ET.parse(path).getroot()
    for suite in root.iter("testsuite"):
        if suite.get("errors") is not None and suite.get("errors") != "0":
            raise ValueError(f"{path}: suite reports infrastructure errors")
        if suite.get("tests") is not None and int(suite.get("tests")) != len(list(suite.iter("testcase"))):
            raise ValueError(f"{path}: suite count disagrees with its test cases")
    cases = list(root.iter("testcase"))
    if names is not None:
        selected = [c for c in cases if c.get("name") in names]
        if Counter(c.get("name") for c in selected) != Counter(names):
            raise ValueError(f"{path}: expected each test exactly once: {names}")
        # Cocotb may include filtered tests as skips, never as extra executions.
        if any(c.get("name") not in names and (not allow_filtered or c.find("skipped") is None) for c in cases):
            raise ValueError(f"{path}: unexpected executed test")
        cases = selected
    if not cases or any(c.find("error") is not None or c.find("skipped") is not None for c in cases):
        raise ValueError(f"{path}: empty, errored or skipped required tests")
    failures = [c for c in cases if c.find("failure") is not None]
    if expect == "pass" and failures:
        raise ValueError(f"{path}: required test failed")
    if expect == "fail" and not failures:
        raise ValueError(f"{path}: negative control did not fail")
    return len(cases), len(failures)


def begin(worktree, out, kind, validation_root, plan=None):
    wt, out, root = Path(worktree).resolve(), Path(out).resolve(), Path(validation_root).resolve()
    if kind not in ("gl", "sdf", "precheck", "sta", "checks"):
        raise ValueError(f"unknown receipt kind {kind}")
    if kind == "sta":
        # a fresh STA of the run's own final step at plan["period_ns"]
        if not isinstance(plan, dict) or set(plan) != {"period_ns", "step", "image", "image_id", "librelane_version"}:
            raise ValueError("STA plan needs period_ns, step, image, image_id and librelane_version")
        if type(plan["period_ns"]) not in (int, float) or not math.isfinite(plan["period_ns"]) or plan["period_ns"] <= 0:
            raise ValueError("invalid STA period")
        if plan["step"] != sta_step(wt).name:
            raise ValueError("STA plan names another step")
        if not isinstance(plan["image_id"], str) or not re.fullmatch(IMAGE_ID_RE, plan["image_id"]):
            raise ValueError("STA plan needs the image's ID (sha256:...)")
        plan = dict(plan, inputs=sta_inputs(wt))
        state = read_json(sta_step(wt) / "state_in.json")
        rel = lambda p: str(wt / Path(p).relative_to(run_base(wt)))
        final_nl = one((wt / "runs/wokwi/final/nl").glob("*.nl.v"), "final netlist")
        final_spef = one((wt / "runs/wokwi/final/spef/nom").glob("*.spef"), "final SPEF")
        if digest(rel(state["nl"])) != digest(final_nl) or [digest(rel(p)) for p in state["spef"].values()] != [digest(final_spef)]:
            raise ValueError("the final STA step did not read the final netlist and SPEF")
    if kind == "checks":
        # the run's own final STA step, twice, with report Tcl (flow/check_rerun.py)
        if not isinstance(plan, dict) or set(plan) != {"step", "image", "image_id", "librelane_version", "tcl",
                                                       "sdc_removed"}:
            raise ValueError("checks plan needs step, image, image_id, librelane_version, tcl and sdc_removed")
        if plan["step"] != sta_step(wt).name:
            raise ValueError("checks plan names another step")
        if not isinstance(plan["image_id"], str) or not re.fullmatch(IMAGE_ID_RE, plan["image_id"]):
            raise ValueError("checks plan needs the image's ID (sha256:...)")
        if not isinstance(plan["tcl"], dict) or set(plan["tcl"]) != set(CHECK_PASSES):
            raise ValueError(f"checks plan needs a report Tcl digest for each pass {sorted(CHECK_PASSES)}")
        plan = dict(plan, inputs=sta_inputs(wt))
    if kind == "gl":
        plan = {m: test_names(root / "test" / f"{m}.py") for m in GL_MODULES}
        if hardware_files(wt) != hardware_files(root):
            raise ValueError("GL validation RTL/profile differs from the candidate")
    if kind == "sdf":
        if hardware_files(wt) != hardware_files(root):
            raise ValueError("SDF validation RTL/profile differs from the candidate")
        if not isinstance(plan, list) or not plan:
            raise ValueError("empty SDF plan")
        for case in plan:
            if case["corner"] not in ("typ", "slow", "fast") or case["expect"] not in ("pass", "fail", "report"):
                raise ValueError("invalid SDF corner or expectation")
            if not math.isfinite(case["period_ns"]) or case["period_ns"] <= 0:
                raise ValueError("invalid SDF period")
            available = test_names(root / "test" / (case["module"] + ".py"))
            if not case["tests"] or len(set(case["tests"])) != len(case["tests"]) or not set(case["tests"]) <= set(available):
                raise ValueError("SDF plan has unknown or duplicate tests")
            safe_artifact(out, case["directory"])
        if len({c["directory"] for c in plan}) != len(plan):
            raise ValueError("duplicate SDF output directory")
    out.mkdir(parents=True, exist_ok=True)
    data = dict(schema=SCHEMA, kind=kind, worktree=str(wt), validation_root=str(root), output_directory=str(out),
                runner_root=str(RUNNER_ROOT), runner=runner_files(RUNNER_ROOT),
                candidate=candidate_files(wt),
                validation={} if kind in ("sta", "checks") else
                validation_files(root, include_hardware=kind in ("gl", "sdf")),
                plan=plan)
    if kind in ("gl", "sdf"):
        hardware = lambda files: {p: h for p, h in files.items() if p.startswith("src/") and Path(p).suffix in (".v", ".sv")}
        if hardware(data["candidate"]) != hardware(data["validation"]):
            raise ValueError("hardware inputs changed while being captured")
    write_new(out / "inputs.json", data)
    return data


def safe_artifact(root, name):
    name = Path(name)
    if name.is_absolute() or ".." in name.parts or not name.parts:
        raise ValueError(f"unsafe artifact path {name}")
    path = Path(root) / name
    if not path.resolve().is_relative_to(Path(root).resolve()):
        raise ValueError(f"artifact outside receipt directory: {name}")
    return path


def outputs(out, data):
    """Recheck results as well as hashes; return the evidence files to bind."""
    out = Path(out)
    kind, plan = data["kind"], data["plan"]
    files = []
    if kind == "gl":
        if set(plan) != set(GL_MODULES):
            raise ValueError("incomplete GL suite plan")
        netlist = one((p for p in data["candidate"] if p.endswith(".nl.v")), "receipt netlist")
        if digest(out / "netlist.v") != data["candidate"][netlist]:
            raise ValueError("GL copy is not the candidate netlist")
        files.append(out / "netlist.v")
        for name in GL_MODULES:
            check_xml(out / f"{name}.xml", plan[name])
            files += [out / f"{name}.xml", out / f"{name}.log"]
    elif kind == "sdf":
        for case in plan:
            d = safe_artifact(out, case["directory"])
            rc = read_json(d / "status.json")["returncode"]
            if type(rc) is not int:
                raise ValueError(f"{d}: invalid simulator exit status")
            text = (d / "sim.log").read_text(errors="replace")
            # Match the exact annotation path emitted by the testbench.
            annotation = Path(data["output_directory"]) / case["directory"] / "annotate.sdf"
            if f"sdf_annotate: {annotation} -> tb.user_project" not in text.splitlines():
                raise ValueError(f"{d}: SDF annotation did not run")
            for line in text.splitlines():
                if re.search(r"SDF (ERROR|WARNING)|VPI error|Could not insert|Cannot find", line) and not re.search(r"annotate.sdf:(9|11): Chosen value not defined", line):
                    raise ValueError(f"{d}: annotation error: {line}")
            expectation = case["expect"]
            count, failed = check_xml(d / "results.xml", case["tests"], expectation, allow_filtered=True)
            if expectation == "pass" and rc != 0:
                raise ValueError(f"{d}: simulator command failed ({rc})")
            if expectation == "fail":
                if rc not in (1, 2):
                    raise ValueError(f"{d}: negative control has unexpected process status {rc}")
                failed_cases = [c for c in ET.parse(d / "results.xml").getroot().iter("testcase")
                                if c.get("name") in case["tests"] and c.find("failure") is not None]
                for c in failed_cases:
                    failure = c.find("failure")
                    error, message = failure.get("error_type"), failure.get("error_msg", "")
                    known_x_value = error == "ValueError" and message == "Can't convert LogicArray to int: it contains non-0/1 values"
                    if error != "AssertionError" and not known_x_value:
                        raise ValueError(f"{d}: negative control failed for an unexpected reason ({error}: {message})")
                if "** TESTS=" not in text:
                    raise ValueError(f"{d}: negative control did not finish its test summary")
            if "SimulatorFailure" in text:
                raise ValueError(f"{d}: simulator infrastructure failure")
            files += [d / p for p in ("status.json", "results.xml", "sim.log", "sdf_prep.log", "netlist.v", "annotate.sdf")]
    elif kind == "precheck":
        rc = read_json(out / "status.json")["returncode"]
        if type(rc) is not int or rc != 0:
            raise ValueError("precheck process failed")
        check_xml(out / "tt/precheck/reports/results.xml")
        gds = one((p for p in data["candidate"] if p.endswith(".gds")), "receipt GDS")
        copy = out / "design" / gds
        if digest(copy) != data["candidate"][gds]:
            raise ValueError("precheck ran on another GDS")
        text = (out / "precheck.log").read_text(errors="replace")
        original_copy = Path(data["output_directory"]) / "design" / gds
        if f"Precheck passed for {original_copy}!" not in text or "Precheck failed" in text:
            raise ValueError("precheck did not report this GDS passed")
        files = [out / "status.json", out / "precheck.log", out / "tt/precheck/reports/results.xml", copy]
        files += [p for p in (out / "design").rglob("*") if p.is_file() and p != copy]
        files += [p for p in (out / "tt").rglob("*") if p.is_file()
                  and not {"reports", "__pycache__"}.intersection(p.relative_to(out / "tt").parts)]
    elif kind == "sta":
        rc = read_json(out / "status.json")["returncode"]
        if type(rc) is not int or rc != 0:
            raise ValueError("STA step failed")
        period, config = plan["period_ns"], read_json(out / "config.json")
        if config.get("CLOCK_PERIOD") != period:
            raise ValueError("STA step config is not at the planned period")
        # the image that ran: recorded by ID, the run's LibreLane, and the one the command ran
        image_id, version = plan.get("image_id"), plan.get("librelane_version")
        if not isinstance(image_id, str) or not re.fullmatch(IMAGE_ID_RE, image_id):
            raise ValueError("the STA receipt records no image ID (sha256:...)")
        if version != config["meta"]["librelane_version"]:
            raise ValueError(f"STA image {image_id} has LibreLane {version}, "
                             f"not the run's {config['meta']['librelane_version']}")
        command = (out / "run.log").read_text(errors="replace").split("\n", 1)[0]
        if f" {image_id} python3 -m librelane.steps run --id {STA_STEP_ID} " not in command:
            raise ValueError(f"the STA command did not run image {image_id}")
        for corner in CORNERS:
            d = out / "sta" / corner
            clock = re.search(CLOCK_PERIOD_RE, (d / "clock.rpt").read_text(), re.M | re.S)
            if not clock or abs(float(clock[1]) - period) > CLOCK_TOLERANCE_NS:
                raise ValueError(f"{corner}: STA clock report missing or not at {period} ns")
            sta_metrics(out, corner)
            files += [d / "sta.log", d / "clock.rpt"]
        files += [out / "config.json", out / "status.json", out / "run.log", out / "sta/state_out.json"]
    elif kind == "checks":
        ran = plan.get("image_id")
        if not (isinstance(ran, str) and re.fullmatch(IMAGE_ID_RE, ran)):
            raise ValueError("the checks receipt records no image ID (sha256:...)")
        for name, tcl in CHECK_PASSES.items():
            d = out / name
            status = read_json(d / "status.json")["returncode"]
            if type(status) is not int or status != 0:
                raise ValueError(f"checks STA {name} pass failed")
            if digest(d / tcl) != plan["tcl"][name]:
                raise ValueError(f"checks STA {name} pass ran another report Tcl")
            first = (d / "run.log").read_text(errors="replace").split("\n", 1)[0]
            if " ".join(["", ran, "python3 -m librelane.steps run --id", STA_STEP_ID, ""]) not in first:
                raise ValueError(f"the checks STA {name} pass did not run image {ran}")
            for corner in CORNERS:
                report = d / "sta" / corner / CHECK_REPORTS[name]
                if CHECK_REPORT_END not in report.read_text(errors="replace").splitlines():
                    raise ValueError(f"{report.relative_to(out)}: incomplete check report")
                files.append(report)
            files += [d / "config.json", d / "status.json", d / "run.log", d / tcl]
        files.append(out / "pulse/pulse.sdc")
    else:
        raise ValueError(f"unknown receipt kind {kind}")
    return {str(p.relative_to(out)): digest(p) for p in files}


def finish(out):
    out = Path(out)
    data = read_json(out / "inputs.json")
    if runner_files(data["runner_root"]) != data["runner"]:
        raise ValueError("verification runner changed during execution")
    if candidate_files(data["worktree"]) != data["candidate"]:
        raise ValueError("candidate changed during verification")
    if data["kind"] == "sta":
        sta_recheck(data["worktree"], data, out)
    elif data["kind"] == "checks":
        checks_recheck(data["worktree"], data, out)
    elif validation_files(data["validation_root"], include_hardware=data["kind"] in ("gl", "sdf")) != data["validation"]:
        raise ValueError("validation inputs changed during verification")
    data["artifacts"] = outputs(out, data)
    data["status"] = "complete"
    # Origins aid reproduction; artifact references themselves are relative.
    write_new(out / "receipt.json", data)
    return out / "receipt.json"


def verify(path, worktree, kind, validation_root=None):
    """The receipt's data, plus "skipped": the checks this tree could not make
    (only an STA receipt checked against an evidence archive skips any)."""
    path = Path(path)
    data = read_json(path)
    if data.get("schema") != SCHEMA or data.get("kind") != kind or data.get("status") != "complete":
        raise ValueError(f"{path}: not a completed {kind} receipt")
    if data["candidate"] != candidate_files(worktree):
        raise ValueError(f"{path}: receipt belongs to different candidate inputs")
    try:
        artifacts = outputs(path.parent, data) if data.get("artifacts") else None
    except (OSError, ValueError, KeyError, TypeError, ET.ParseError) as e:
        raise ValueError(f"{path}: {e}") from e
    if artifacts is None or artifacts != data["artifacts"]:
        raise ValueError(f"{path}: evidence changed after receipt was written")
    if kind == "gl":
        root = Path(validation_root or worktree)
        expected = {m: test_names(root / "test" / f"{m}.py") for m in GL_MODULES}
        if data["plan"] != expected or data["validation"] != validation_files(root, include_hardware=True):
            raise ValueError(f"{path}: GL tests no longer match the selected validation checkout")
        if hardware_files(root) != hardware_files(worktree):
            raise ValueError(f"{path}: selected validation RTL/profile differs from the candidate")
    if kind == "sdf" and validation_root is not None:
        if data["validation"] != validation_files(validation_root, include_hardware=True):
            raise ValueError(f"{path}: SDF tests no longer match the selected validation checkout")
    skipped = []
    if kind == "sta":
        try:
            skipped = sta_recheck(worktree, data, path.parent, allow_absent=True)
        except (OSError, ValueError, KeyError, TypeError) as e:
            raise ValueError(f"{path}: the run's STA inputs: {e}") from e
    if kind == "checks":
        try:
            skipped = checks_recheck(worktree, data, path.parent, allow_absent=True)
        except (OSError, ValueError, KeyError, TypeError) as e:
            raise ValueError(f"{path}: the run's checks STA inputs: {e}") from e
    return dict(data, skipped=skipped)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)
    start = sub.add_parser("begin")
    start.add_argument("kind", choices=("gl", "sdf", "precheck"))
    start.add_argument("worktree")
    start.add_argument("out")
    start.add_argument("--validation-root", required=True)
    start.add_argument("--plan")
    done = sub.add_parser("finish")
    done.add_argument("out")
    a = ap.parse_args()
    try:
        if a.command == "begin":
            begin(a.worktree, a.out, a.kind, a.validation_root, read_json(a.plan) if a.plan else None)
        else:
            print(f"receipt: {finish(a.out)}")
    except (OSError, ValueError, KeyError, TypeError, ET.ParseError) as e:
        ap.exit(1, f"receipt failed: {e}\n")


if __name__ == "__main__":
    main()
