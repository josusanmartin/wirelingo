#!/usr/bin/env python3
"""Clock, reset and pulse-width check evidence for a finished run, with a receipt.

    flow/check_rerun.py RUN_WORKTREE [--out-parent DIR] [--image IMAGE] [--cpus N]

Re-runs the run's own LibreLane OpenROAD.STAPostPNR step (its step config and
input state: final netlist, extracted SPEF, all three corners) twice, in the
LibreLane image the run used, by the image's ID, with the run mounted
read-only and no network (--cpus N limits each container on a shared runner).
Only the report Tcl changes, plus one SDC change in the second pass:

* signoff: the run's signoff SDC; flow/check_types.tcl reports the worst check
  and every violator of minimum pulse width, minimum period, recovery,
  removal and clock-gating setup/hold.
* pulse: the signoff SDC after a preamble that makes every path-exception
  command (set_max_delay, set_min_delay, set_false_path, set_multicycle_path)
  a no-op; flow/pulse_checks.tcl reports the worst minimum pulse width check,
  every violator, each SRAM clock pin's checks, each clock's register clock
  pins and every pulse width check. OpenSTA 2.7.0 (LibreLane 3.1.0.dev3)
  reports no pulse width check on the pins a min/max-type exception's -from
  clock drives (with one each way between two clocks, none at all), violated
  or not (docs/release-evidence.md). Pulse widths depend on the clock network
  alone, which no path exception touches; this pass's other results are not
  used.

The image must be local and hold the run's LibreLane version (as for
flow/sta_rerun.py). The output goes to a fresh test/sim_build/checks.XXXXXX
(or --out-parent); on success a receipt (flow/release_receipts.py, kind
"checks") binds the candidate files, the step's input files, both passes'
configs, the derived SDC, the report Tcl and the reports.
flow/release_check.py --checks-receipt R gates on them (flow/release_gates.py).
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

FLOW = Path(__file__).resolve().parent
sys.path.insert(0, str(FLOW))
import release_receipts as receipts  # noqa: E402
import release_gates as gates  # noqa: E402
from sta_rerun import image_identity  # noqa: E402

STEP_ID = receipts.STA_STEP_ID


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("worktree", help="completed hardening worktree (read only)")
    ap.add_argument("--out-parent", default=str(FLOW.parent / "test/sim_build"),
                    help="where the fresh checks.XXXXXX output directory is made")
    ap.add_argument("--image", help="LibreLane image (default: the one named by the step's LibreLane version)")
    ap.add_argument("--cpus", type=float, help="docker --cpus limit for the STA containers (a shared runner)")
    a = ap.parse_args(argv)
    wt = Path(a.worktree).resolve()
    step = receipts.sta_step(wt)
    config = receipts.read_json(step / "config.json")
    if config.get("meta", {}).get("step") != STEP_ID:
        ap.error(f"{step} is not a {STEP_ID} step")
    version = config["meta"]["librelane_version"]
    image = a.image or f"ghcr.io/librelane/librelane:{version}"
    try:
        image_id, image_version = image_identity(image)
    except (OSError, ValueError) as e:
        print(f"refused: {e}")
        return 2
    if image_version != version:
        print(f"refused: image {image} ({image_id}) has LibreLane {image_version}; the run used {version}")
        return 2
    print(f"image {image}: {image_id}, LibreLane {image_version}", flush=True)
    base = receipts.run_base(wt)
    pdk = Path(config["PDK_ROOT"])
    sdc = Path(config["SIGNOFF_SDC_FILE"])
    if not sdc.is_absolute() or not sdc.is_relative_to(base):
        print(f"refused: signoff SDC outside the run's worktree: {sdc}")
        return 2
    pulse_text, removed = receipts.pulse_sdc((wt / sdc.relative_to(base)).read_text())
    Path(a.out_parent).mkdir(parents=True, exist_ok=True)
    out = Path(tempfile.mkdtemp(prefix="checks.", dir=a.out_parent)).resolve()
    print(f"checks outputs {out}", flush=True)
    for line in removed:
        print(f"pulse pass disables: {line}")
    plan = dict(step=step.name, image=image, image_id=image_id, librelane_version=image_version,
                tcl={name: receipts.digest(FLOW / tcl) for name, tcl in receipts.CHECK_PASSES.items()},
                sdc_removed=removed)
    try:
        receipts.begin(wt, out, "checks", wt, plan)
    except (OSError, ValueError, KeyError, TypeError) as e:
        print(f"refused: {e}")
        return 2
    expected = receipts.check_configs(wt, out)
    for name, tcl in receipts.CHECK_PASSES.items():
        d = out / name
        d.mkdir()
        shutil.copyfile(FLOW / tcl, d / tcl)
        if name == "pulse":
            (d / "pulse.sdc").write_text(pulse_text)
        receipts.write_new(d / "config.json", expected[name])
        limit = ["--cpus", f"{a.cpus:g}"] if a.cpus else []
        cmd = ["docker", "run", "--rm", "--network", "none", *limit, "-u", f"{os.getuid()}:{os.getgid()}", "-e", "HOME=/tmp",
               "-v", f"{base}:{base}:ro", "-v", f"{pdk}:{pdk}:ro", "-v", f"{out}:{out}", "-w", str(d), image_id,
               "python3", "-m", "librelane.steps", "run", "--id", STEP_ID, "-c", str(d / "config.json"),
               "-i", str(base / "runs/wokwi" / step.name / "state_in.json"),
               "-o", str(d / "sta")]
        with (d / "run.log").open("x") as log:
            log.write(" ".join(cmd) + "\n")
            log.flush()
            rc = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT).returncode
        (d / "status.json").write_text(json.dumps({"returncode": rc}) + "\n")
        if rc != 0:
            print(f"checks STA {name} pass failed ({rc}); log {d / 'run.log'}")
            return 1
    try:
        receipt = receipts.finish(out)
    except (OSError, ValueError, KeyError, TypeError) as e:
        print(f"receipt failed: {e}")
        return 1
    for line in gates.check_summary(out):
        print(line)
    print(f"receipt: {receipt}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
