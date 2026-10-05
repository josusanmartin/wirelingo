#!/usr/bin/env python3
"""Validate the full FPGA profile on ECP5-25k without a board pin assignment.

This measures capacity and internal routed timing; it neither creates a
programmable board image nor establishes external timing or ASIC signoff.
Every invocation uses a new private output directory and retains failed logs.
"""
import argparse
import hashlib
import json
import pathlib
import subprocess
import datetime
import shutil

from fpga_profile import stage

SOURCES = ("project.v", "pe_chip.sv", "pe_core.sv", "pe_line.sv", "pe_fifos.sv",
           "pe_fifo.sv", "pe_cg.sv", "pe_imem.sv", "pe_hostport.sv")
TOP = "tt_um_josusanmartin_protoemu"


def run(cmd, out, name):
    with (out / f"{name}.log").open("w") as log:
        log.write("command: " + repr(cmd) + "\n")
        log.flush()
        proc = subprocess.run(cmd, cwd=out, stdout=log, stderr=subprocess.STDOUT)
    if proc.returncode:
        raise RuntimeError(f"{name} failed ({proc.returncode}); retained {(out / (name + '.log'))}")


def tool_identity(name):
    binary = shutil.which(name)
    if binary is None:
        return {"available": False}
    version = subprocess.run([name, "--version"], capture_output=True, text=True)
    return dict(path=binary, sha256=hashlib.sha256(pathlib.Path(binary).read_bytes()).hexdigest(),
                version=(version.stdout + version.stderr).strip(), version_exit=version.returncode)


def validate(out, freq):
    stage(out)
    error = None
    tools = {}
    try:
        tools = {name: tool_identity(name) for name in ("yosys", "nextpnr-ecp5")}
        source = " ".join("src/" + name for name in SOURCES)
        run(["yosys", "-p", f"read_verilog -sv {source}; synth_ecp5 -top {TOP} -json design.json; write_verilog -noattr mapped.v"],
            out, "synthesis")
        run(["nextpnr-ecp5", "--25k", "--package", "CABGA381", "--freq", str(freq),
             "--json", "design.json", "--lpf-allow-unconstrained", "--report", "route.json"],
            out, "route")
        for name in ("design.json", "mapped.v", "route.json"):
            if not (out / name).is_file() or not (out / name).stat().st_size:
                raise ValueError("missing FPGA evidence: " + name)
        clocks = json.loads((out / "route.json").read_text())["fmax"]
        for clock in ("$glbnet$clk$TRELLIS_IO_IN", "$glbnet$ui_in[6]$TRELLIS_IO_IN"):
            timing = clocks[clock]
            if not timing["achieved"] >= timing["constraint"] >= freq:
                raise ValueError("FPGA timing target failed: " + clock)
    except (OSError, RuntimeError, ValueError, KeyError, TypeError) as exc:
        error = str(exc)
    profile = json.loads((out / "manifest.json").read_text())
    evidence = {name: hashlib.sha256((out / name).read_bytes()).hexdigest()
                for name in ("manifest.json", "design.json", "mapped.v", "synthesis.log", "route.log", "route.json")
                if (out / name).is_file()}
    result = dict(kind="generic FPGA capacity and internal routed timing; external pins unconstrained",
                  completed_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  target="ECP5-25k CABGA381", target_mhz=freq, profile=profile["profile"],
                  status="fail" if error else "pass", error=error, tools=tools, evidence=evidence,
                  flow_sources={p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in (pathlib.Path(__file__), pathlib.Path(__file__).with_name("fpga_profile.py"))})
    (out / "validation.json").write_text(json.dumps(result, indent=2) + "\n")
    if error:
        raise RuntimeError(f"{error}; failure evidence: {out / 'validation.json'}")
    print(f"FPGA route passed; evidence: {out / 'validation.json'}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", type=pathlib.Path, required=True)
    ap.add_argument("--freq", type=float, default=48, help="internal clock target in MHz")
    args = ap.parse_args()
    if not 0 < args.freq <= 100:
        ap.error("frequency must be in (0,100] MHz")
    validate(args.output.resolve(), args.freq)
