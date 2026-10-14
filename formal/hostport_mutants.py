#!/usr/bin/env python3
"""Mutation check for the host-port reference proof (hostport_ref.sv): each
mutant is a one-line bug in a scratch copy of src/pe_hostport.sv (the source
tree is never touched), and the `mutant` task must find a counterexample
trace for it: a legal trace (the guided subset of the host contract) of up to
240 steps on which the property group named for the mutant (specification
properties only, no lemmas) is violated. The unmutated RTL must pass the full proof.
Exit status 1 if any mutant survives or the baseline fails.

    python3 formal/hostport_mutants.py [-j N] [name ...]   # needs sby and yices on PATH
"""
import concurrent.futures
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
TASK = "mutant"

# name: (original text, mutated text, property group that must catch it, what it breaks)
MUTANTS = {
    "fixed-write-increments": (
        "bus_wdata <= w_hold;\n        bus_addr <= snap_addr;\n        snap_addr <= snap_addr + {15'd0, c_inc};",
        "bus_wdata <= w_hold;\n        bus_addr <= snap_addr;\n        snap_addr <= snap_addr + 16'd1;",
        "P1A", "WRITE_FIXED words all go to the same address"),
    "fixed-read-increments": (
        "snap_addr <= snap_addr + {15'd0, c_inc};\n        outstanding <= outstanding + 2'd1;",
        "snap_addr <= snap_addr + 16'd1;\n        outstanding <= outstanding + 2'd1;",
        "P2A", "READ_FIXED snapshots all of the same address"),
    "quad-nibble-reversed": (
        "wire [31:0] sh_n4 = {sh[27:0], io_in};",
        "wire [31:0] sh_n4 = {sh[27:0], io_in[0], io_in[1], io_in[2], io_in[3]};",
        "P1A", "quad address and data bits in order (IO3 = MSB)"),
    "read-commit-one-sck-early": (
        "if (phase == PH_DATA && cnt == word_len) begin\n        if (rd) r_tog <= !r_tog;",
        "if (phase == PH_DATA && cnt == word_len - {5'd0, rd}) begin\n        if (rd) r_tog <= !r_tog;",
        "P2A", "a word is committed only once its last bit is clocked out"),
    "cs-rise-no-cancel": (
        "end else if ((a_pend || cs_s[2]) && active) begin",
        "end else if (a_pend && active) begin",
        "P3", "CS_n rising cancels the read within LX"),
    "stale-commit-address": (
        "        snap_addr <= a_hold;\n        com_addr <= a_hold;",
        "        snap_addr <= a_hold;",
        "P2A", "a read commits its own words, not at an address left by an earlier read"),
    "quad-write-data-reversed": (
        "          w_hold <= sh_n;",
        "          w_hold <= quad ? {sh_n[3:0], sh_n[31:4]} : sh_n;",
        "P1D", "the written data is the word the host sent"),
    "reset-skips-rearm": (
        "else if (&cs_s) host_ready <= 1'b1;",
        "else host_ready <= 1'b1;",
        "P1A", "a transaction selected across reset is ignored until CS_n is seen high"),
}


def first_violation(vcd):
    """Step at which the harness's per-step violation flags (pv, pv6) first rise."""
    ids, step, sid = {}, None, None
    for line in vcd.read_text().splitlines():
        p = line.split()
        if line.startswith("$var") and len(p) >= 5:
            if p[4] in ("pv", "pv6") and p[2] == "1":
                ids[p[3]] = p[4]
            elif p[4] == "smt_step":
                sid = p[3]
        elif line.startswith("b") and len(p) == 2 and p[1] == sid:
            step = int(p[0][1:], 2)
        elif (len(p) == 2 and p[0] == "b1" and p[1] in ids) or (line[:1] == "1" and line[1:] in ids):
            return step
    return None


def status(work, task):
    """FAIL with an assertion counterexample kills; anything else is reported."""
    d = work / "formal" / f"hostport_ref_{task}"
    log = (d / "logfile.txt").read_text() if (d / "logfile.txt").exists() else ""
    st = (d / "status").read_text().split()[0] if (d / "status").exists() else "NOSTATUS"
    if st == "FAIL" and task == TASK:
        vcd = d / "engine_0" / "trace.vcd"
        at = first_violation(vcd) if vcd.exists() else None
        return f"killed: violated at step {at} (trace {vcd})"
    if st == "FAIL":
        src = (work / "formal" / "hostport_ref.sv").read_text().splitlines()
        lines = sorted({int(n) for n in re.findall(r"failed assertion .* at hostport_ref\.sv:(\d+)", log)})
        steps = re.findall(r"failed assertion .* step (\d+)", log)
        what = "; ".join(src[n - 1].strip() for n in lines) or "?"
        return f"killed at step {steps[0] if steps else '?'}: {what}"
    return st


def run(name, scratch):
    work = pathlib.Path(tempfile.mkdtemp(prefix=f"{name}-", dir=scratch))
    (work / "formal").mkdir()
    (work / "src").mkdir()
    for f in ("hostport_ref.sv", "hostport_ref.sby"):
        shutil.copy2(ROOT / "formal" / f, work / "formal" / f)
    rtl = (ROOT / "src" / "pe_hostport.sv").read_text()
    if name != "baseline":
        old, new, group, _ = MUTANTS[name]
        sby = work / "formal" / "hostport_ref.sby"
        line = "mutant: read_verilog -sv -formal -DGUIDE -DSTICKY "
        text = sby.read_text()
        if text.count(line) != 1:
            return "INFRASTRUCTURE (bmc task line not found)"
        sby.write_text(text.replace(line, f"{line}-DPSEL -D{group} "))
        if rtl.count(old) != 1:
            return "STALE (mutation site not found)"
        rtl = rtl.replace(old, new)
    (work / "src" / "pe_hostport.sv").write_text(rtl)
    task = "prove" if name == "baseline" else TASK
    t = time.time()
    subprocess.run(["sby", "-f", "hostport_ref.sby", task], cwd=work / "formal",
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return f"{status(work, task)} ({time.time() - t:.0f} s)"


def main(args):
    jobs = 1
    if args[:1] == ["-j"]:
        jobs, args = int(args[1]), args[2:]
    names = args or list(MUTANTS)
    scratch = tempfile.mkdtemp(prefix="hostport-mutants-")
    with concurrent.futures.ThreadPoolExecutor(jobs) as pool:
        base = pool.submit(run, "baseline", scratch)
        results = {name: pool.submit(run, name, scratch) for name in names}
        print(f"{'baseline (prove)':28s}    {base.result()}", flush=True)
        bad = not base.result().startswith("PASS")
        for name in names:
            r = results[name].result()
            bad += not r.startswith("killed")
            print(f"{name:28s} {MUTANTS[name][2]} {r}\n{'':31s} ({MUTANTS[name][3]})", flush=True)
    print(f"{len(names)} mutants, {'all killed' if not bad else 'FAILED'} (scratch: {scratch})")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main(sys.argv[1:])
