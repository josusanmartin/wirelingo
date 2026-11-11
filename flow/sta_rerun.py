#!/usr/bin/env python3
"""Fresh signoff STA of a finished run at another clock period, with a receipt.

    flow/sta_rerun.py RUN_WORKTREE PERIOD_NS [--out-parent DIR] [--image IMAGE]

Re-runs the run's own LibreLane OpenROAD.STAPostPNR step (its step config and
input state: the final netlist, the extracted SPEF, the signoff SDC and
corner Tcl, all three corners) in the LibreLane image the run used, with only
CLOCK_PERIOD changed. src/protoemu.sdc derives the I/O delays and the host
port's SCK from CLOCK_PERIOD, so they scale with it. The run is mounted
read-only; nothing in it is written. The output goes to a fresh
test/sim_build/sta.XXXXXX of this checkout (or --out-parent), and on success
a receipt (flow/release_receipts.py, kind "sta") binds the candidate files, the
step's input files and the STA results.

flow/release_check.py --slow-mhz F --slow-sta-receipt R uses such a receipt
for the slow corner when the run was constrained at 50 MHz and the release
claims F < 50 MHz there (docs/release-evidence.md). A rerun at the run's own
period reproduces the run's final STA (the tool's self-check).
"""
import argparse
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile

FLOW = Path(__file__).resolve().parent
sys.path.insert(0, str(FLOW))
import release_receipts as receipts  # noqa: E402

STEP_ID = "OpenROAD.STAPostPNR"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("worktree", help="completed hardening worktree (read only)")
    ap.add_argument("period_ns", type=float)
    ap.add_argument("--out-parent", default=str(FLOW.parent / "test/sim_build"),
                    help="where the fresh sta.XXXXXX output directory is made")
    ap.add_argument("--image", help="LibreLane image (default: the one named by the step's LibreLane version)")
    a = ap.parse_args(argv)
    if not math.isfinite(a.period_ns) or a.period_ns <= 0:
        ap.error("PERIOD_NS must be finite and positive")
    wt = Path(a.worktree).resolve()
    step = receipts.sta_step(wt)
    config = receipts.read_json(step / "config.json")
    if config.get("meta", {}).get("step") != STEP_ID:
        ap.error(f"{step} is not a {STEP_ID} step")
    image = a.image or f"ghcr.io/librelane/librelane:{config['meta']['librelane_version']}"
    base = receipts.run_base(wt)
    pdk = Path(config["PDK_ROOT"])
    Path(a.out_parent).mkdir(parents=True, exist_ok=True)
    out = Path(tempfile.mkdtemp(prefix="sta.", dir=a.out_parent)).resolve()
    print(f"sta outputs {out}", flush=True)
    plan = dict(period_ns=a.period_ns, step=step.name, image=image)
    try:
        receipts.begin(wt, out, "sta", wt, plan)
    except (OSError, ValueError, KeyError, TypeError) as e:
        print(f"refused: {e}")
        return 2
    config["CLOCK_PERIOD"] = a.period_ns
    receipts.write_new(out / "config.json", config)
    cmd = ["docker", "run", "--rm", "--network", "none", "-u", f"{os.getuid()}:{os.getgid()}", "-e", "HOME=/tmp",
           "-v", f"{base}:{base}:ro", "-v", f"{pdk}:{pdk}:ro", "-v", f"{out}:{out}", "-w", str(out), image,
           "python3", "-m", "librelane.steps", "run", "--id", STEP_ID, "-c", str(out / "config.json"),
           "-i", str(base / "runs/wokwi" / step.name / "state_in.json"),
           "-o", str(out / "sta")]
    with (out / "run.log").open("x") as log:
        log.write(" ".join(cmd) + "\n")
        log.flush()
        rc = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT).returncode
    (out / "status.json").write_text(json.dumps({"returncode": rc}) + "\n")
    if rc != 0:
        print(f"STA step failed ({rc}); log {out / 'run.log'}")
        return 1
    try:
        for corner in receipts.CORNERS:
            m = receipts.sta_metrics(out, corner)
            print(f"{corner} at {a.period_ns:g} ns: setup WS {m['timing__setup__ws']}, "
                  f"{m['timing__setup_vio__count']} violators; hold WS {m['timing__hold__ws']}, "
                  f"{m['timing__hold_vio__count']} violators")
        print(f"receipt: {receipts.finish(out)}")
    except (OSError, ValueError, KeyError, TypeError) as e:
        print(f"receipt failed: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
