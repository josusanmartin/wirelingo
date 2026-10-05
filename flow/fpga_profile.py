#!/usr/bin/env python3
"""Stage a named FPGA profile without modifying the ASIC checkout.

The default preserves the ASIC core/bank/FIFO geometry. Inferred RAM,
flop FIFOs and configuration clock enables replace ASIC-only primitives.
Explicit smaller profiles are experiments, not validation of the tapeout.
FPGA results do not establish ASIC timing/CDC/analog behavior.
"""
import argparse
import hashlib
import json
import pathlib
import re
import shutil

ROOT = pathlib.Path(__file__).resolve().parents[1]


def inputs():
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((ROOT / "src").rglob("*")) if p.is_file()
            and p.name not in ("user_config.json", "config_merged.json")}


def stage(dst, ncores=None, fifo_log2=None):
    if dst.exists():
        raise ValueError("output exists; select a new private staging directory")
    before = inputs()
    original = (ROOT / "src/project.v").read_text()
    def default(name):
        match = re.search(rf"^`define {name} ([0-9]+)$", original, re.M)
        if match is None:
            raise ValueError("ASIC profile define changed: " + name)
        return int(match.group(1))
    ncores = default("PE_NCORES") if ncores is None else ncores
    fifo_log2 = default("PE_FIFO_LOG2") if fifo_log2 is None else fifo_log2
    if ncores not in range(1, 5) or fifo_log2 not in (2, 3):
        raise ValueError("FPGA profile requires 1..4 cores and FIFO log2 2 or 3")
    shutil.copytree(ROOT / "src", dst / "src", ignore=shutil.ignore_patterns("user_config.json", "config_merged.json"))
    copied = {str(p.relative_to(dst)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted((dst / "src").rglob("*")) if p.is_file()}
    if before != copied or before != inputs():
        raise ValueError("ASIC sources changed during staging; retain output and use a new directory")
    project = dst / "src/project.v"
    text = re.sub(r"^`define PE_NCORES [0-9]+$", f"`define PE_NCORES {ncores}", original, flags=re.M)
    text = re.sub(r"^`define PE_FIFO_LOG2 [0-9]+$", f"`define PE_FIFO_LOG2 {fifo_log2}", text, flags=re.M)
    project.write_text('`define PE_FLOP_IMEM\n`define PE_BANK_AW 8\n`define PE_FIFO_NO_CG\n' + text)
    # The ASIC configuration registers use gated clocks. FPGA fabrics require
    # enables: replace only the seven named configuration blocks, fail on drift.
    core = dst / "src/pe_core.sv"
    text = core.read_text()
    gate = re.compile(r"  wire \[6:0\] cfg_gclk;.*?  endgenerate\n", re.S)
    text, count = gate.subn("", text)
    if count != 1:
        raise ValueError("configuration gate site changed; review FPGA conversion")
    for index in range(7):
        pattern = re.compile(rf"always @\(posedge cfg_gclk\[{index}\] or negedge rst_n\)(.*?)\n    else ", re.S)
        text, count = pattern.subn(r"always @(posedge clk or negedge rst_n)\1\n    else if (bus_sel && bus_we && bus_off == " + str(index) + ") ", text)
        if count != 1:
            raise ValueError("configuration register site changed; review FPGA conversion")
    core.write_text(text)
    chip = dst / "src/pe_chip.sv"
    text = chip.read_text()
    pattern = re.compile(r"  wire \[3:1\] gcfg_gclk;.*?  endgenerate\n", re.S)
    text, count = pattern.subn("", text)
    if count != 1:
        raise ValueError("global configuration gate site changed")
    for index in range(1, 4):
        pattern = re.compile(rf"always @\(posedge gcfg_gclk\[{index}\] or negedge rst_n\)(.*?) else ")
        text, count = pattern.subn(r"always @(posedge clk or negedge rst_n)\1 else if (bus_we && sel_glob && goff == " + str(index + 4) + ") ", text)
        if count != 1:
            raise ValueError("global configuration register site changed")
    chip.write_text(text)
    # Yosys preprocesses each input file separately; local headers are needed
    # even when the TT action provides no synthesis define options.
    for name, define in (("pe_fifo.sv", "PE_FIFO_NO_CG"), ("pe_imem.sv", "PE_FLOP_IMEM")):
        path = dst / "src" / name
        path.write_text(f"`define {define}\n" + path.read_text())
    manifest = dict(profile=f"fpga-fixed-bank-{ncores}c{ncores}b256w{1 << fifo_log2}f",
                    ncores=ncores, nbanks=ncores, bank_words=256, fifo_depth=1 << fifo_log2,
                    changes="inferred RAM, enable-flop FIFOs and configuration registers")
    manifest["asic_inputs"] = before
    manifest["staged_sources"] = {str(p.relative_to(dst)): hashlib.sha256(p.read_bytes()).hexdigest()
                                  for p in sorted((dst / "src").rglob("*")) if p.is_file()}
    (dst / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", required=True, type=pathlib.Path)
    ap.add_argument("--cores", type=int, choices=range(1, 5), help="explicit experimental geometry override")
    ap.add_argument("--fifo-log2", type=int, choices=(2, 3), help="explicit experimental FIFO override")
    a = ap.parse_args()
    stage(a.output.resolve(), a.cores, a.fifo_log2)
