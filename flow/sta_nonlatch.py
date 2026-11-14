#!/usr/bin/env python3
"""Worst setup slack of a finished run with the latches excluded, with a receipt.

    flow/sta_nonlatch.py RUN_WORKTREE [--period NS] [--out-parent DIR] [--image IMAGE]
    flow/sta_nonlatch.py --verify RECEIPT RUN_WORKTREE

The run's own STA (and flow/sta_rerun.py's) reports the worst setup slack over
every endpoint. The FIFO's latch D pins borrow time and report slack exactly 0,
so that number reads 0 whenever every other endpoint has positive slack: it
says nothing about the margin. This re-times the run's final STA step inputs
(its netlist, extracted SPEF, signoff SDC with the step's variables and
Liberty files, per corner) in the LibreLane image the run used, read-only and
without network, at --period (default: the step's CLOCK_PERIOD), and reports
per corner: the worst slack over all endpoints, the worst latch path (its
slack and borrowed time) and the worst endpoint among flop data pins, macro
input pins and outputs (flow/sta_nonlatch.tcl).

The output goes to a fresh test/sim_build/nonlatch.XXXXXX (or --out-parent),
with receipt.json binding the candidate files and the step's inputs
(flow/release_receipts.py's candidate_files and sta_inputs), this tool and its
Tcl, the image and the reports. flow/release_check.py --nonlatch-receipt R
verifies it and prints the non-latch slack next to the latch-inclusive one.
"""
import argparse
import fnmatch
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

FLOW = Path(__file__).resolve().parent
sys.path.insert(0, str(FLOW))
import release_receipts as receipts  # noqa: E402
from sta_rerun import image_identity  # noqa: E402

SCHEMA = "protoemu-sta-nonlatch-1"
TOOL = ("sta_nonlatch.py", "sta_nonlatch.tcl")
END_RE = re.compile(r"^(\S+) \(([^)]*)\)\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+) \((?:MET|VIOLATED)\)$", re.M)


def parse(text):
    """{"all", "latch", "latch_borrow", "nonlatch", "nonlatch_endpoint"} from one corner's report."""
    sec = {m.group(1): m.start() for m in re.finditer(r"^== (ALL|LATCH|NONLATCH|END)\b", text, re.M)}
    if set(sec) != {"ALL", "LATCH", "NONLATCH", "END"}:
        raise ValueError("incomplete report")
    part = lambda a, b: text[sec[a]:sec[b]]
    m = re.search(r"worst slack max (\S+)", part("ALL", "LATCH"))
    if not m:
        raise ValueError("no worst slack")
    res = {"all": float(m.group(1)), "latch": None, "latch_borrow": None}
    latch = part("LATCH", "NONLATCH")
    s = re.search(r"^\s*(-?[\d.]+)\s+slack \((?:MET|VIOLATED)\)", latch, re.M)
    if s:
        b = re.search(r"^\s*(-?[\d.]+)\s+\S+\s+time borrowed from endpoint", latch, re.M)
        res["latch"], res["latch_borrow"] = float(s.group(1)), float(b.group(1)) if b else 0.0
    ends = END_RE.findall(part("NONLATCH", "END"))
    if not ends:
        raise ValueError("no non-latch endpoint reported")
    worst = min(ends, key=lambda e: float(e[4]))
    res["nonlatch"], res["nonlatch_endpoint"] = float(worst[4]), worst[0]
    return res


def plan_of(wt, period):
    """What the re-timing reads, from the run's final STA step (paths in the run's worktree)."""
    step = receipts.sta_step(wt)
    config = receipts.read_json(step / "config.json")
    state = receipts.read_json(step / "state_in.json")
    base = receipts.run_base(wt)
    corners = config.get("STA_CORNERS") or list(receipts.CORNERS)
    macros = config.get("MACROS") or {}
    libs = {}
    for c in corners:
        ls = [p for pat, v in (config.get("CELL_LIBS") or {}).items() if fnmatch.fnmatch(c, pat) for p in v]
        for m in macros.values():
            ls += [p for pat, v in (m.get("lib") or {}).items() if fnmatch.fnmatch(c, pat) for p in v]
        libs[c] = ls
    spef = {c: next((p for pat, p in state["spef"].items() if fnmatch.fnmatch(c, pat)), None) for c in corners}
    sdc = config["SIGNOFF_SDC_FILE"]
    for p in [state["nl"], sdc, *spef.values()]:
        if p is None or not Path(p).is_absolute() or not Path(p).is_relative_to(base):
            raise ValueError(f"STA input outside the run's worktree: {p}")
    sdc_text = (wt / Path(sdc).relative_to(base)).read_text()
    names = sorted(set(re.findall(r"::env\((\w+)\)", sdc_text)) - {"CLOCK_PERIOD"})
    env = {k: config[k] for k in names if isinstance(config.get(k), (str, int, float)) and not isinstance(config[k], bool)}
    env["CLOCK_PERIOD"] = period if period is not None else config["CLOCK_PERIOD"]
    return dict(step=step.name, base=str(base), pdk=config["PDK_ROOT"], corners=corners, libs=libs, spef=spef,
                netlist=state["nl"], sdc=sdc, env=env, top=config.get("DESIGN_NAME", "tt_um_josusanmartin_protoemu"),
                macros=sorted(macros), version=config["meta"]["librelane_version"])


def tool_digests():
    return {name: receipts.digest(FLOW / name) for name in TOOL}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("worktree", nargs="?", help="completed hardening worktree (read only)")
    ap.add_argument("--period", type=float, help="clock period in ns (default: the run's)")
    ap.add_argument("--out-parent", default=str(FLOW.parent / "test/sim_build"))
    ap.add_argument("--image", help="LibreLane image (default: the one named by the step's LibreLane version)")
    ap.add_argument("--verify", metavar="RECEIPT", help="verify RECEIPT against the worktree and print it")
    a = ap.parse_args(argv)
    if not a.worktree:
        ap.error("RUN_WORKTREE is required")
    wt = Path(a.worktree).resolve()
    if a.verify:
        try:
            data = verify(a.verify, wt)
        except (OSError, ValueError, KeyError, TypeError) as e:
            print(f"invalid: {e}")
            return 1
        for line in summary(data):
            print(line)
        return 0
    try:
        plan = plan_of(wt, a.period)
        image = a.image or f"ghcr.io/librelane/librelane:{plan['version']}"
        image_id, image_version = image_identity(image)
    except (OSError, ValueError, KeyError, TypeError) as e:
        print(f"refused: {e}")
        return 2
    if image_version != plan["version"]:
        print(f"refused: image {image} ({image_id}) has LibreLane {image_version}; the run used {plan['version']}")
        return 2
    Path(a.out_parent).mkdir(parents=True, exist_ok=True)
    out = Path(tempfile.mkdtemp(prefix="nonlatch.", dir=a.out_parent)).resolve()
    tcl = out / "sta_nonlatch.tcl"
    tcl.write_bytes((FLOW / "sta_nonlatch.tcl").read_bytes())
    print(f"non-latch STA outputs {out}", flush=True)
    results, reports = {}, {}
    for c in plan["corners"]:
        env = dict(plan["env"], STA_LIBS=" ".join(plan["libs"][c]), STA_NETLIST=plan["netlist"],
                   STA_SPEF=plan["spef"][c], STA_SDC=plan["sdc"], STA_TOP=plan["top"], STA_MACROS=" ".join(plan["macros"]))
        cmd = ["docker", "run", "--rm", "--network", "none", "-u", f"{os.getuid()}:{os.getgid()}", "-e", "HOME=/tmp",
               "-v", f"{plan['base']}:{plan['base']}:ro", "-v", f"{plan['pdk']}:{plan['pdk']}:ro",
               "-v", f"{out}:{out}:ro"]
        for k, v in sorted(env.items()):
            cmd += ["-e", f"{k}={v}"]
        cmd += [image_id, "sta", "-no_splash", "-exit", str(tcl)]
        rpt = out / f"{c}.rpt"
        with rpt.open("x") as f:
            rc = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT).returncode
        if rc != 0:
            print(f"STA for {c} failed ({rc}); report {rpt}")
            return 1
        try:
            results[c] = parse(rpt.read_text())
        except ValueError as e:
            print(f"STA for {c}: {e}; report {rpt}")
            return 1
        reports[f"{c}.rpt"] = receipts.digest(rpt)
    data = {"schema": SCHEMA, "kind": "nonlatch", "status": "complete",
            "plan": {"period_ns": plan["env"]["CLOCK_PERIOD"], "step": plan["step"], "image": image,
                     "image_id": image_id, "librelane_version": image_version, "corners": plan["corners"]},
            "tool": tool_digests(), "candidate": receipts.candidate_files(wt), "sta_inputs": receipts.sta_inputs(wt),
            "reports": reports, "results": results}
    receipts.write_new(out / "receipt.json", data)
    for line in summary(data):
        print(line)
    print(f"receipt: {out / 'receipt.json'}")
    return 0


def verify(path, worktree):
    """The receipt's data, after checking that it belongs to this run's inputs,
    this tool, and reports that still say what it records."""
    path = Path(path)
    data = receipts.read_json(path)
    if data.get("schema") != SCHEMA or data.get("kind") != "nonlatch" or data.get("status") != "complete":
        raise ValueError(f"{path}: not a completed non-latch STA receipt")
    if data["candidate"] != receipts.candidate_files(worktree):
        raise ValueError(f"{path}: receipt belongs to different candidate inputs")
    if data["sta_inputs"] != receipts.sta_inputs(worktree):
        raise ValueError(f"{path}: the run's final STA inputs changed")
    if data["tool"] != tool_digests():
        raise ValueError(f"{path}: made by another flow/sta_nonlatch.py or .tcl")
    for name, d in data["reports"].items():
        if receipts.digest(path.parent / name) != d:
            raise ValueError(f"{path}: report {name} changed after the receipt was written")
        if parse((path.parent / name).read_text()) != data["results"][name[:-len(".rpt")]]:
            raise ValueError(f"{path}: report {name} does not say what the receipt records")
    if sorted(data["results"]) != sorted(data["plan"]["corners"]):
        raise ValueError(f"{path}: corners missing")
    return data


def summary(data):
    p = data["plan"]["period_ns"]
    out = []
    for c in data["plan"]["corners"]:
        r = data["results"][c]
        latch = "no latch path" if r["latch"] is None else f"worst latch {r['latch']:g} ns (borrows {r['latch_borrow']:g})"
        out.append(f"{c} at {p:g} ns: worst non-latch setup slack {r['nonlatch']:+.4f} ns at {r['nonlatch_endpoint']}; "
                   f"over all endpoints {r['all']:g} ns; {latch}")
    return out


if __name__ == "__main__":
    sys.exit(main())
