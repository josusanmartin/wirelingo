#!/usr/bin/env python3
"""Generate, check and archive the complete release evidence in one command.

Run on the compute runner, from the checkout whose flow/ runners should be
used (normally the validation clone itself):

    V/flow/release_evidence.py RUN --validation-root V
    V/flow/release_evidence.py RUN --validation-commit REV    # fresh clone of REV

RUN is a completed hardening worktree (runs/wokwi/final). The command runs
functional GL, the SDF standard and v2 sets, autonomous USB enumeration with
SDF at typ/slow/fast (20.834 ns) and Tiny Tapeout precheck, each in its own
fresh output directory, with at most --jobs simulator processes at once. It
collects each receipt from that job's own output (never an older receipt),
runs flow/release_check.py, writes verdict.json and release-check.log, and
on a pass packs the evidence archive (flow/archive_evidence.py). Any failed
job or check exits 1 and builds no archive.

The physical run is never written to: the runners see it through a view
directory of symlinks (gl_test.sh writes its output under the run worktree it
is given). Receipts bind file contents, and release_check.py runs against the
real RUN path.
"""
import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

FLOW = Path(__file__).resolve().parent
sys.path.insert(0, str(FLOW))
import release_receipts as receipts  # noqa: E402

HOME = Path.home()
AUTO_TESTS = "test_usb_autonomous test_usb_autonomous_fs"
# Variables the runners read from the environment: an inherited value could
# silently narrow a set (e.g. SLOW_CLK_NS=23), so the orchestrator sets them.
RUNNER_VARIABLES = ("TESTS", "MODULE", "SLOW_CLK_NS", "NEGATIVE_CLK_NS", "VALIDATION_ROOT",
                    "COCOTB_TESTCASE", "COCOTB_TEST_FILTER", "COCOTB_TEST_MODULES",
                    "COCOTB_RESULTS_FILE", "SIM_BUILD", "SDF", "GATE_LEVEL_NETLIST")


@dataclass
class Job:
    name: str
    kind: str                    # receipt kind: gl, sdf or precheck
    argv: list
    weight: int                  # simulator processes it runs at once
    env: dict = field(default_factory=dict)
    out_parent: Path = None      # where the runner creates its fresh directory
    out: Path = None             # precheck: the directory we choose


def run_name(run):
    return Path(run).name.removeprefix("protoemu-exp-")


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run", help="completed hardening worktree (read only)")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--validation-root", help="existing, clean git clone of the shipping commit")
    src.add_argument("--validation-commit", help="clone this repository at REV into the work directory")
    ap.add_argument("--work", help="fresh directory for logs, view, precheck, verdict "
                                   "(default: next to the validation root, rcpt-RUN-REV-UTC)")
    ap.add_argument("--archive", help="archive path (default: next to the work directory, "
                                      "wirelingo-release-evidence-REV-RUN.tar.gz); never overwritten")
    ap.add_argument("--no-archive", action="store_true", help="stop after the verdict")
    ap.add_argument("--jobs", type=int, default=6, help="max concurrent simulator processes (default 6)")
    ap.add_argument("--tt-root", help="tt-support-tools for precheck (default RUN/tt)")
    ap.add_argument("--flow-python", default=str(HOME / "protoemu-flow/protoemu/.venv-flow/bin/python"),
                    help="Python with gdstk/klayout/yaml for precheck")
    ap.add_argument("--klayout-dir", default=str(HOME / "protoemu-flow/kbin"),
                    help="directory with the klayout binary or wrapper, prepended to PATH")
    ap.add_argument("--pdk-root", default=os.environ.get("PDK_ROOT") or str(HOME / "ttpdk"))
    ap.add_argument("--sim-path", default=str(HOME / "tools/oss-cad-suite/bin"),
                    help="Icarus/cocotb tools, prepended to PATH")
    ap.add_argument("--repro", action="append", default=[], metavar="RUN",
                    help="reproduction run whose candidate files go into the archive (repeatable)")
    ap.add_argument("--extra", action="append", default=[], metavar="FILE",
                    help="additional file for the archive, e.g. a reproduction comparison (repeatable)")
    ap.add_argument("--note", default="", help="paragraph for the archive README")
    a = ap.parse_args(argv)
    if a.jobs < 1:
        ap.error("--jobs must be at least 1")
    a.run = Path(a.run).resolve()
    if a.validation_root:
        a.validation_root = Path(a.validation_root).resolve()
    a.tt_root = Path(a.tt_root).resolve() if a.tt_root else a.run / "tt"
    return a


def default_paths(a, rev, now):
    """Work directory and archive for a run and a validation revision."""
    base = (a.validation_root.parent if a.validation_root else FLOW.parents[1])
    work = Path(a.work).resolve() if a.work else base / f"rcpt-{run_name(a.run)}-{rev}-{now:%Y%m%dT%H%M%SZ}"
    archive = (Path(a.archive).resolve() if a.archive
               else work.parent / f"wirelingo-release-evidence-{rev}-{run_name(a.run)}.tar.gz")
    return work, archive


def preflight(a, validation, work, archive):
    """Everything that can be refused before an hour of simulation."""
    problems = []
    if work.exists():
        problems.append(f"work directory exists: {work}")
    if not a.no_archive and archive.exists():
        problems.append(f"archive exists, refusing to overwrite: {archive}")
    if not (a.run / "runs/wokwi/final").is_dir():
        problems.append(f"not a completed hardening run: {a.run}")
    if validation is not None:
        if not (validation / ".git").is_dir():
            problems.append(f"validation root is not a real git clone (no .git directory): {validation}")
        elif git(validation, "status", "--porcelain", "--untracked-files=all"):
            problems.append(f"validation root has uncommitted or untracked files: {validation}")
    if not (a.tt_root / "precheck/precheck.py").is_file():
        problems.append(f"no precheck/precheck.py in --tt-root {a.tt_root}")
    for path, what in ((a.flow_python, "--flow-python"), (Path(a.klayout_dir) / "klayout", "klayout"),
                       (a.pdk_root, "--pdk-root")):
        if not Path(path).exists():
            problems.append(f"{what} not found: {path}")
    for name in ("gl_test.sh", "gl_sdf_test.sh", "precheck_test.py", "release_check.py", "archive_evidence.py"):
        if not (FLOW / name).is_file():
            problems.append(f"runner missing: {FLOW / name}")
    return problems


def make_view(run, view):
    """A directory whose entries link to the run, except a private test/."""
    view.mkdir()
    for entry in sorted(run.iterdir()):
        if entry.name not in ("test", ".git"):
            (view / entry.name).symlink_to(entry)
    (view / "test/sim_build").mkdir(parents=True)
    if receipts.candidate_files(view) != receipts.candidate_files(run):
        raise ValueError("the candidate view does not reproduce the run's candidate files")
    return view


def runner_env(base, sim_path, **settings):
    env = {k: v for k, v in base.items() if k not in RUNNER_VARIABLES}
    env["PATH"] = f"{sim_path}{os.pathsep}{base.get('PATH', '')}"
    env.update({k: str(v) for k, v in settings.items()})
    return env


def plan_jobs(a, view, validation, work, base_env=None):
    """The seven receipt jobs, the long ones first (GL took ~65 min for fo6)."""
    base_env = dict(os.environ if base_env is None else base_env)
    sim = runner_env(base_env, a.sim_path)
    sdf = lambda **extra: runner_env(base_env, a.sim_path, VALIDATION_ROOT=validation, **extra)
    repo_sim = FLOW.parent / "test/sim_build"
    jobs = [Job("gl", "gl", ["bash", str(FLOW / "gl_test.sh"), str(view), str(validation)], 1, sim,
                out_parent=view / "test/sim_build")]
    for corner in ("slow", "typ", "fast"):
        jobs.append(Job(f"sdf_auto_{corner}", "sdf", ["bash", str(FLOW / "gl_sdf_test.sh"), str(view), corner, "20.834"],
                        1, sdf(TESTS=AUTO_TESTS), out_parent=repo_sim))
    pre_env = runner_env(base_env, a.klayout_dir, PDK_ROOT=a.pdk_root)
    jobs.append(Job("precheck", "precheck", [str(a.flow_python), str(FLOW / "precheck_test.py"), str(view),
                                             "--tt-root", str(a.tt_root), "--out", str(work / "precheck")],
                    1, pre_env, out=work / "precheck"))
    jobs.append(Job("sdf_standard", "sdf", ["bash", str(FLOW / "gl_sdf_test.sh"), str(view), "standard"], 4, sdf(),
                    out_parent=repo_sim))
    jobs.append(Job("sdf_v2", "sdf", ["bash", str(FLOW / "gl_sdf_test.sh"), str(view), "v2"], 6, sdf(),
                    out_parent=repo_sim))
    return jobs


def snapshot(jobs):
    """Output directories that existed before any job started."""
    seen = set()
    for job in jobs:
        if job.out_parent is not None and job.out_parent.is_dir():
            seen.update(p.resolve() for p in job.out_parent.iterdir())
    return seen


def collect_receipt(job, log_text, existing, started):
    """The receipt a job just wrote, from its own log, or a ValueError."""
    outs = re.findall(r"^(?:.*\boutputs|precheck outputs:) (/\S+)$", log_text, re.M)
    found = re.findall(r"^receipt: (/\S+)$", log_text, re.M)
    if len(outs) != 1:
        raise ValueError(f"{job.name}: expected one output directory in its log, found {len(outs)}")
    if len(found) != 1:
        raise ValueError(f"{job.name}: expected one receipt line in its log, found {len(found)}")
    out, receipt = Path(outs[0]), Path(found[0])
    if receipt != out / "receipt.json":
        raise ValueError(f"{job.name}: receipt {receipt} is not in its output directory {out}")
    if job.out is not None and out.resolve() != job.out.resolve():
        raise ValueError(f"{job.name}: output {out} is not the requested {job.out}")
    if job.out_parent is not None and out.resolve().parent != job.out_parent.resolve():
        raise ValueError(f"{job.name}: output {out} is not under {job.out_parent}")
    if out.resolve() in existing:
        raise ValueError(f"{job.name}: output directory {out} existed before this run")
    data = receipts.read_json(receipt)
    if data.get("kind") != job.kind or data.get("status") != "complete":
        raise ValueError(f"{job.name}: {receipt} is not a completed {job.kind} receipt")
    if Path(data["output_directory"]) != out:
        raise ValueError(f"{job.name}: {receipt} records another output directory")
    if receipt.stat().st_mtime < started:
        raise ValueError(f"{job.name}: {receipt} predates this run")
    return receipt


def run_jobs(jobs, slots, launch, poll_seconds=5.0, clock=time.monotonic, sleep=time.sleep, report=print):
    """Start jobs in order while their weights fit in `slots`; skip ahead to
    smaller jobs that fit. `launch(job)` returns an object with poll()."""
    pending, running, results = list(jobs), {}, {}
    peak = 0
    while pending or running:
        used = sum(min(j.weight, slots) for j in running.values())
        for job in list(pending):
            weight = min(job.weight, slots)
            if used + weight <= slots:
                running[id(job)] = job
                job.process, job.t0 = launch(job), clock()
                used += weight
                pending.remove(job)
                report(f"started {job.name} (weight {weight}, {used}/{slots} in use)")
        peak = max(peak, used)
        for key, job in list(running.items()):
            rc = job.process.poll()
            if rc is not None:
                del running[key]
                results[job.name] = dict(returncode=rc, seconds=round(clock() - job.t0))
                report(f"{'finished' if rc == 0 else 'FAILED'} {job.name}: exit {rc} "
                       f"after {results[job.name]['seconds']} s")
        if pending or running:
            sleep(poll_seconds)
    return results, peak


def terminate(jobs):
    for job in jobs:
        process = getattr(job, "process", None)
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    time.sleep(10)
    for job in jobs:
        process = getattr(job, "process", None)
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)   # vvp ignores SIGTERM while annotating
            except ProcessLookupError:
                pass


def release_command(run, validation, receipt_paths, sdf_profile="v2"):
    cmd = [sys.executable, str(FLOW / "release_check.py"), str(run), "--validation-root", str(validation),
           "--sdf-profile", sdf_profile]
    for kind, path in receipt_paths:
        cmd += [{"gl": "--gl-receipt", "sdf": "--sdf-receipt", "precheck": "--precheck-receipt"}[kind], str(path)]
    return cmd


def verdict_line(text):
    lines = [l for l in text.splitlines() if l.startswith(("RELEASE PASSED", "RELEASE FAILED"))]
    return lines[-1] if lines else "RELEASE CHECK PRODUCED NO VERDICT"


def main(argv=None):
    a = parse_args(argv)
    now = datetime.now(timezone.utc)
    validation = a.validation_root
    rev = git(validation, "rev-parse", "--short=7", "HEAD") if validation else \
        git(FLOW.parent, "rev-parse", "--short=7", a.validation_commit)
    work, archive = default_paths(a, rev, now)
    problems = preflight(a, validation, work, archive)
    if problems:
        print("release evidence refused:\n" + "\n".join(f"- {p}" for p in problems), file=sys.stderr)
        return 2
    work.mkdir(parents=True)
    (work / "logs").mkdir()
    log = (work / "release-evidence.log").open("x")

    def say(message):
        line = f"{datetime.now(timezone.utc):%Y-%m-%dT%H:%M:%SZ} {message}"
        print(line, flush=True)
        log.write(line + "\n")
        log.flush()

    if validation is None:
        validation = work / "validation"
        subprocess.run(["git", "clone", "-q", "--no-checkout", str(FLOW.parent), str(validation)], check=True)
        git(validation, "-c", "advice.detachedHead=false", "checkout", "-q", "--detach", a.validation_commit)
    head = git(validation, "rev-parse", "HEAD")
    say(f"candidate {a.run}; validation root {validation} at {head}; runners {FLOW}; work {work}")
    view = make_view(a.run, work / "candidate")
    jobs = plan_jobs(a, view, validation, work)
    existing = snapshot(jobs)
    started = time.time()

    def launch(job):
        say(f"{job.name}: {' '.join(job.argv)}")
        handle = (work / "logs" / f"{job.name}.log").open("x")
        return subprocess.Popen(job.argv, cwd=FLOW.parent, env=job.env, stdout=handle,
                                stderr=subprocess.STDOUT, start_new_session=True)

    def stop(signum, frame):
        raise KeyboardInterrupt(f"signal {signum}")

    signal.signal(signal.SIGTERM, stop)              # stop the simulators too, not only this process
    try:
        results, peak = run_jobs(jobs, a.jobs, launch, report=say)
    except BaseException:
        say("interrupted: stopping all jobs")
        terminate(jobs)
        raise
    failures, collected = [], []
    for job in jobs:
        text = (work / "logs" / f"{job.name}.log").read_text(errors="replace")
        if results[job.name]["returncode"] != 0:
            failures.append(f"{job.name} exited {results[job.name]['returncode']} (log {work / 'logs' / (job.name + '.log')})")
            say(f"--- {job.name} log tail\n" + "\n".join(text.splitlines()[-25:]))
            continue
        try:
            path = collect_receipt(job, text, existing, started)
            collected.append(dict(job=job.name, kind=job.kind, path=str(path), sha256=receipts.digest(path)))
            say(f"{job.name}: receipt {path}")
        except (OSError, ValueError, KeyError) as e:
            failures.append(str(e))
    verdict = dict(schema=1, created_utc=now.isoformat(), finished_utc=None,
                   run=str(a.run), view=str(view), validation_root=str(validation), validation_head=head,
                   runner_root=str(FLOW), runner_head=git(FLOW.parent, "rev-parse", "HEAD"),
                   candidate=receipts.candidate_files(a.run), jobs=results, peak_simulators=peak,
                   receipts=collected, release_check=None, verdict=None, exit=None,
                   work=str(work), archive=None)
    if not failures:
        cmd = release_command(a.run, validation, [(r["kind"], r["path"]) for r in collected])
        say("release check: " + " ".join(cmd))
        check = subprocess.run(cmd, cwd=FLOW.parent, capture_output=True, text=True)
        (work / "release-check.log").write_text(check.stdout + check.stderr)
        verdict.update(release_check=cmd, exit=check.returncode, verdict=verdict_line(check.stdout))
        if check.returncode:
            failures.append(verdict["verdict"])
        say(check.stdout.rstrip())
    else:
        verdict.update(exit=1, verdict="RELEASE EVIDENCE INCOMPLETE: " + "; ".join(failures))
    verdict["finished_utc"] = datetime.now(timezone.utc).isoformat()
    if not failures and not a.no_archive:
        subprocess.run(["git", "-C", str(validation), "bundle", "create", str(work / "source.bundle"), "HEAD"],
                       check=True, capture_output=True)
        verdict["archive"] = str(archive)
    receipts.write_new(work / "verdict.json", verdict)
    if failures:
        say("RELEASE EVIDENCE FAILED:\n" + "\n".join(f"- {f}" for f in failures))
        return 1
    say(f"verdict: {verdict['verdict']}")
    if a.no_archive:
        return 0
    cmd = [sys.executable, str(FLOW / "archive_evidence.py"), str(work / "verdict.json"), "--output", str(archive)]
    for path in a.repro:
        cmd += ["--repro", path]
    for path in a.extra:
        cmd += ["--extra", path]
    if a.note:
        cmd += ["--note", a.note]
    result = subprocess.run(cmd, capture_output=True, text=True)
    say(result.stdout.rstrip() + result.stderr.rstrip())
    if result.returncode:
        say("ARCHIVE FAILED")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
