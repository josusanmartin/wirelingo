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
from pathlib import Path
import re
import xml.etree.ElementTree as ET

SCHEMA = 1
GL_MODULES = ("test", "test_pio", "test_eth_service")
CORNERS = ("nom_typ_1p20V_25C", "nom_slow_1p08V_125C", "nom_fast_1p32V_m40C")
BASIC_TESTS = ("test_id", "test_uart_tx", "test_spi_master", "test_host_port_fast", "test_fetch_to_pin")
V2_TESTS = ("test_spi_master_fast", "test_la_capture_ticks", "test_call_ret")
RUNNER_ROOT = Path(__file__).resolve().parent


def runner_files(root):
    return {name: digest(Path(root) / name) for name in
            ("release_receipts.py", "gl_test.sh", "gl_sdf_test.sh", "precheck_test.py", "sdf_prep.py")
            if (Path(root) / name).is_file()}


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
    for directory, suffixes in (("test", (".py", ".v", ".sv")),
                                ("tools", (".py",)), ("firmware", (".pasm",))):
        paths = (root / directory).glob("*") if directory == "test" else (root / directory).rglob("*")
        files.extend(p for p in paths if p.is_file() and p.suffix in suffixes)
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
    if kind not in ("gl", "sdf", "precheck"):
        raise ValueError(f"unknown receipt kind {kind}")
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
                candidate=candidate_files(wt), validation=validation_files(root, include_hardware=kind in ("gl", "sdf")), plan=plan)
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
                if re.search(r"SDF ERROR|VPI error|Could not insert|Cannot find", line) and not re.search(r"annotate.sdf:(9|11): Chosen value not defined", line):
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
    if validation_files(data["validation_root"], include_hardware=data["kind"] in ("gl", "sdf")) != data["validation"]:
        raise ValueError("validation inputs changed during verification")
    data["artifacts"] = outputs(out, data)
    data["status"] = "complete"
    # Origins aid reproduction; artifact references themselves are relative.
    write_new(out / "receipt.json", data)
    return out / "receipt.json"


def verify(path, worktree, kind, validation_root=None):
    path = Path(path)
    data = read_json(path)
    if data.get("schema") != SCHEMA or data.get("kind") != kind or data.get("status") != "complete":
        raise ValueError(f"{path}: not a completed {kind} receipt")
    if data["candidate"] != candidate_files(worktree):
        raise ValueError(f"{path}: receipt belongs to different candidate inputs")
    if not data.get("artifacts") or outputs(path.parent, data) != data["artifacts"]:
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
    return data


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
