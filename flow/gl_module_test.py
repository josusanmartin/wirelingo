#!/usr/bin/env python3
"""Gate-level runs of one extra cocotb module, one test per simulator, with a
hash-bound receipt. Supplementary evidence: release_check.py does not read it.

    flow/gl_module_test.py run RUN --validation-root V [--module test_post_freeze]
        [--tests pf_lin pf_cec ...] [--jobs 6] [--sdf-corner typ] [--out DIR]
    flow/gl_module_test.py verify OUT/receipt.json RUN --validation-root V

Functional (the default) is flow/gl_test.sh's mechanism for one module: the
unpowered netlist final/nl with the PDK cell and SRAM models, no SDF, tapeout
profile (PE_NCORES/PE_NBANKS/PE_FIFO_LOG2 read from RUN/src/project.v),
PE_CLK_NS=20, each test in its own build directory (COCOTB_TESTCASE), at most
--jobs simulators at once. Outputs go to a fresh directory (default
V/test/sim_build/glm.XXXXXX); RUN is only read. The receipt binds the
candidate files and validation inputs (release_receipts.py), the runner
scripts, the validation commit, the tool versions, whether sigrok-cli was
available, and every test's results.xml and log; it is written only when
every test passed exactly once.

--sdf-corner CORNER runs each test through flow/gl_sdf_test.sh's single-case
mode (MODULE, TESTS, VALIDATION_ROOT set explicitly; CLK_NS 20 = the default
clock; tests that start their own slower clock keep it), so each test gets
that runner's own SDF receipt; this script's receipt then binds those.
"""
import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

FLOW = Path(__file__).resolve().parent
sys.path.insert(0, str(FLOW))
import release_receipts as receipts  # noqa: E402

HOME = Path.home()
SCHEMA = 1
# Variables cocotb's makefile or the runners read: never inherited.
SCRUB = ("TESTS", "MODULE", "SLOW_CLK_NS", "NEGATIVE_CLK_NS", "VALIDATION_ROOT", "COCOTB_TESTCASE",
         "COCOTB_TEST_FILTER", "COCOTB_TEST_MODULES", "COCOTB_RESULTS_FILE", "SIM_BUILD", "SDF",
         "SDF_MTM", "TB_EXT_DELAY", "GATE_LEVEL_NETLIST", "SIM", "TB", "IMEM", "GATES", "PE_CLK_NS",
         "PE_USB_FS_CLK_NS", "PE_NCORES", "PE_NBANKS", "PE_FIFO_LOG2", "PE_BANK_AW", "I2C_STRETCH",
         "COCOTB_RANDOM_SEED", "RANDOM_SEED")


def profile(run):
    """PE_NCORES, PE_NBANKS, PE_FIFO_LOG2 as flow/gl_test.sh derives them."""
    text = (Path(run) / "src/project.v").read_text()
    define = lambda name: (re.findall(rf"define {name} ([0-9])", text) or [None])[0]
    cores, fifo = define("PE_NCORES"), define("PE_FIFO_LOG2") or "2"
    banks = cores if "define PE_NBANKS `PE_NCORES" in text else define("PE_NBANKS")
    if not cores or not banks:
        raise ValueError(f"cannot read the hardware profile from {run}/src/project.v")
    return dict(PE_NCORES=cores, PE_NBANKS=banks, PE_FIFO_LOG2=fifo)


def tool_versions(path):
    def first_line(cmd):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, env=dict(os.environ, PATH=path), timeout=60)
            return (r.stdout + r.stderr).strip().splitlines()[0]
        except (OSError, IndexError, subprocess.SubprocessError):
            return None
    return dict(iverilog=first_line(["iverilog", "-V"]), cocotb=first_line(["cocotb-config", "--version"]),
                python=first_line(["python3", "--version"]))


def git_head(root):
    try:
        head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True,
                              text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all"],
                               capture_output=True, text=True, check=True).stdout.strip()
        return head, bool(dirty)
    except (OSError, subprocess.CalledProcessError):
        return None, None


def runner_hashes():
    files = receipts.runner_files(FLOW)
    files["gl_module_test.py"] = receipts.digest(Path(__file__).resolve())
    return files


def environment(a, **settings):
    env = {k: v for k, v in os.environ.items() if k not in SCRUB}
    env["PATH"] = f"{a.sim_path}{os.pathsep}{os.environ.get('PATH', '')}"
    env["PDK_ROOT"] = str(a.pdk_root)
    if a.sigrok_cli:
        env["SIGROK_CLI"] = str(a.sigrok_cli)
    env.update({k: str(v) for k, v in settings.items()})
    return env


def command(a, out, test, prof):
    """argv, cwd and environment of one test."""
    if a.sdf_corner:
        env = environment(a, VALIDATION_ROOT=a.validation_root, MODULE=a.module, TESTS=test)
        return (["bash", str(FLOW / "gl_sdf_test.sh"), str(a.run), a.sdf_corner, "20", "pass"],
                FLOW.parent, env)
    d = out / test
    env = environment(a, PE_CLK_NS=20, PE_USB_FS_CLK_NS=20.834, I2C_STRETCH=40, **prof)
    argv = ["make", "GATES=yes", "SIM=icarus", "TB=top", "IMEM=sram", "SDF=",
            f"GATE_LEVEL_NETLIST={out / 'netlist.v'}", f"COCOTB_TEST_MODULES={a.module}",
            f"COCOTB_TESTCASE={test}", f"SIM_BUILD={d / 'build'}", f"COCOTB_RESULTS_FILE={d / 'results.xml'}"]
    return argv, a.validation_root / "test", env


def sdf_receipt(log_text):
    found = re.findall(r"^receipt: (/\S+)$", log_text, re.M)
    if len(found) != 1:
        raise ValueError(f"expected one SDF receipt line, found {len(found)}")
    return Path(found[0])


def run(a):
    tests = a.tests or receipts.test_names(a.validation_root / "test" / f"{a.module}.py")
    available = receipts.test_names(a.validation_root / "test" / f"{a.module}.py")
    if len(set(tests)) != len(tests) or not set(tests) <= set(available):
        raise ValueError(f"unknown or duplicate tests for {a.module}: {tests}")
    if receipts.hardware_files(a.run) != receipts.hardware_files(a.validation_root):
        raise ValueError("validation RTL/profile differs from the candidate")
    prof = profile(a.run)
    netlist = receipts.one((a.run / "runs/wokwi/final/nl").glob("*.nl.v"), "final netlist")
    if a.out:
        out = Path(a.out).resolve()
        out.mkdir(parents=True)
    else:
        parent = a.validation_root / "test/sim_build"
        parent.mkdir(parents=True, exist_ok=True)
        out = Path(tempfile.mkdtemp(prefix="glm_sdf." if a.sdf_corner else "glm.", dir=parent))
    if out.resolve().is_relative_to(a.run.resolve()):
        raise ValueError("the output directory must not be inside the candidate run")
    head, dirty = git_head(a.validation_root)
    data = dict(schema=SCHEMA, kind="gl_module_sdf" if a.sdf_corner else "gl_module", module=a.module,
                sdf_corner=a.sdf_corner, tests=tests, worktree=str(a.run),
                validation_root=str(a.validation_root), validation_head=head, validation_dirty=dirty,
                output_directory=str(out), runner_root=str(FLOW), runner=runner_hashes(),
                candidate=receipts.candidate_files(a.run),
                validation=receipts.validation_files(a.validation_root, include_hardware=True),
                profile=prof, clock_ns_default=20, jobs=a.jobs, tools=tool_versions(f"{a.sim_path}{os.pathsep}{os.environ.get('PATH', '')}"),
                sigrok_cli=str(a.sigrok_cli) if a.sigrok_cli else None,
                sigrok_version=tool_versions_sigrok(a.sigrok_cli),
                started_utc=datetime.now(timezone.utc).isoformat())
    receipts.write_new(out / "inputs.json", data)
    if not a.sdf_corner:
        shutil.copyfile(netlist, out / "netlist.v")
        if receipts.digest(out / "netlist.v") != data["candidate"][str(netlist.relative_to(a.run))]:
            raise ValueError("netlist copy differs from the candidate")
    print(f"netlist {receipts.digest(netlist)[:16]}  {prof}  module {a.module}  "
          f"{'SDF ' + a.sdf_corner if a.sdf_corner else 'functional'}  outputs {out}", flush=True)

    pending, running, results = list(tests), {}, {}

    def stop(signum, frame):
        raise KeyboardInterrupt(f"signal {signum}")
    signal.signal(signal.SIGTERM, stop)
    try:
        while pending or running:
            while pending and len(running) < a.jobs:
                test = pending.pop(0)
                (out / test).mkdir()
                argv, cwd, env = command(a, out, test, prof)
                log = (out / test / "sim.log").open("x")
                running[test] = (subprocess.Popen(argv, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT,
                                                  start_new_session=True), time.monotonic())
                print(f"{datetime.now(timezone.utc):%H:%M:%SZ} started {test}", flush=True)
            for test, (proc, t0) in list(running.items()):
                rc = proc.poll()
                if rc is None:
                    continue
                del running[test]
                results[test] = dict(returncode=rc, seconds=round(time.monotonic() - t0))
                receipts.write_new(out / test / "status.json", results[test])
                print(f"{datetime.now(timezone.utc):%H:%M:%SZ} {'finished' if rc == 0 else 'FAILED'} "
                      f"{test}: exit {rc} after {results[test]['seconds']} s", flush=True)
            if pending or running:
                time.sleep(5)
    except BaseException:
        for proc, _ in running.values():
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(proc.pid, sig)
                except ProcessLookupError:
                    pass
                time.sleep(2)
        raise
    return finish(out, data, results)


def tool_versions_sigrok(cli):
    if not cli:
        return None
    try:
        r = subprocess.run([str(cli), "--version"], capture_output=True, text=True, timeout=60)
        return r.stdout.strip().splitlines()[0]
    except (OSError, IndexError, subprocess.SubprocessError):
        return None


def check_test(out, data, test, rc):
    """Artifacts of one passing test, or a ValueError."""
    d = out / test
    if data["sdf_corner"]:
        path = sdf_receipt((d / "sim.log").read_text(errors="replace"))
        sub = receipts.verify(path, data["worktree"], "sdf", data["validation_root"])
        plan = sub["plan"]
        if [(c["corner"], c["expect"], c["module"], c["tests"]) for c in plan] != \
                [(data["sdf_corner"], "pass", data["module"], [test])]:
            raise ValueError(f"{test}: SDF receipt {path} has another plan")
        if rc != 0:
            raise ValueError(f"{test}: gl_sdf_test.sh exited {rc}")
        return {f"{test}/sim.log": receipts.digest(d / "sim.log"), f"{test}/status.json": receipts.digest(d / "status.json"),
                f"{test}/sdf_receipt": str(path), f"{test}/sdf_receipt.sha256": receipts.digest(path)}
    if rc != 0:
        raise ValueError(f"{test}: simulator exited {rc}")
    receipts.check_xml(d / "results.xml", [test], "pass", allow_filtered=True)
    text = (d / "sim.log").read_text(errors="replace")
    if "SimulatorFailure" in text or "** TESTS=" not in text:
        raise ValueError(f"{test}: incomplete simulation log")
    return {f"{test}/{name}": receipts.digest(d / name) for name in ("results.xml", "sim.log", "status.json")}


def finish(out, data, results):
    failures, artifacts = [], {}
    if runner_hashes() != data["runner"]:
        failures.append("runner scripts changed during execution")
    if receipts.candidate_files(data["worktree"]) != data["candidate"]:
        failures.append("candidate changed during verification")
    if receipts.validation_files(data["validation_root"], include_hardware=True) != data["validation"]:
        failures.append("validation inputs changed during verification")
    if not data["sdf_corner"]:
        artifacts["netlist.v"] = receipts.digest(out / "netlist.v")
    for test in data["tests"]:
        try:
            artifacts.update(check_test(out, data, test, results[test]["returncode"]))
        except (OSError, ValueError, KeyError) as e:
            failures.append(str(e))
    summary = dict(data, results=results, failures=failures, finished_utc=datetime.now(timezone.utc).isoformat())
    receipts.write_new(out / "summary.json", summary)
    for test in data["tests"]:
        print(f"[{test}] {'PASS' if not any(f.startswith(test + ':') for f in failures) else 'FAIL'} "
              f"({results[test]['seconds']} s)")
    if failures:
        print("gl_module_test: FAILED\n" + "\n".join(f"- {f}" for f in failures))
        return 1
    receipt = dict(summary, artifacts=artifacts, status="complete")
    receipts.write_new(out / "receipt.json", receipt)
    print(f"gl_module_test: {len(data['tests'])}/{len(data['tests'])} passed\nreceipt: {out / 'receipt.json'}")
    return 0


def verify(path, run, validation_root):
    path = Path(path).resolve()
    data = receipts.read_json(path)
    if data.get("schema") != SCHEMA or data.get("status") != "complete" or data.get("kind") not in ("gl_module", "gl_module_sdf"):
        raise ValueError(f"{path}: not a completed gl_module receipt")
    if data["candidate"] != receipts.candidate_files(run):
        raise ValueError(f"{path}: receipt belongs to different candidate inputs")
    if data["validation"] != receipts.validation_files(validation_root, include_hardware=True):
        raise ValueError(f"{path}: validation inputs differ from {validation_root}")
    data = dict(data, worktree=str(Path(run).resolve()), validation_root=str(Path(validation_root).resolve()))
    out, artifacts = path.parent, {}
    if not data["sdf_corner"]:
        artifacts["netlist.v"] = receipts.digest(out / "netlist.v")
        netlist = receipts.one((p for p in data["candidate"] if p.endswith(".nl.v")), "receipt netlist")
        if artifacts["netlist.v"] != data["candidate"][netlist]:
            raise ValueError(f"{path}: netlist copy is not the candidate netlist")
    for test in data["tests"]:
        artifacts.update(check_test(out, data, test, data["results"][test]["returncode"]))
    if artifacts != data["artifacts"]:
        raise ValueError(f"{path}: evidence changed after the receipt was written")
    print(f"{path}: {data['kind']} {data['module']} {len(data['tests'])}/{len(data['tests'])} verified "
          f"(validation {data['validation_head']})")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run")
    r.add_argument("run")
    r.add_argument("--validation-root", required=True)
    r.add_argument("--module", default="test_post_freeze")
    r.add_argument("--tests", nargs="+", help="default: every static cocotb test of the module, in file order")
    r.add_argument("--jobs", type=int, default=6)
    r.add_argument("--sdf-corner", choices=("typ", "slow", "fast"))
    r.add_argument("--out", help="fresh output directory (default VALIDATION/test/sim_build/glm.XXXXXX)")
    r.add_argument("--sim-path", default=str(HOME / "tools/oss-cad-suite/bin"))
    r.add_argument("--pdk-root", default=os.environ.get("PDK_ROOT") or str(HOME / "ttpdk"))
    r.add_argument("--sigrok-cli", help="sigrok-cli for the tests' decoder checks (default: none, they skip)")
    v = sub.add_parser("verify")
    v.add_argument("receipt")
    v.add_argument("run")
    v.add_argument("--validation-root", required=True)
    a = ap.parse_args(argv)
    try:
        if a.command == "verify":
            return verify(a.receipt, a.run, a.validation_root)
        if a.jobs < 1:
            ap.error("--jobs must be at least 1")
        a.run, a.validation_root = Path(a.run).resolve(), Path(a.validation_root).resolve()
        if not (a.run / "runs/wokwi/final").is_dir():
            raise ValueError(f"not a completed hardening run: {a.run}")
        return run(a)
    except (OSError, ValueError, KeyError) as e:
        print(f"gl_module_test failed: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
