#!/usr/bin/env python3
"""Mutation check: each mutant is a one-line bug in the model, the RTL, a
firmware program or the assembler, paired with the test that must catch it.
Each runs in a scratch copy of the tracked files; the working tree is never
touched. Exit status 1 if any mutant survives.

    python3 test/mutants.py [name ...]      # from the repo root (needs the test env)
"""
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parents[1]

LOCKSTEP = ("cd test && LOCKSTEP_SEEDS=20 LOCKSTEP_CYCLES=1000 PE_NCORES=2 PE_NBANKS=2 PE_FIFO_LOG2=3 "
            "make TB=chip COCOTB_RESULTS_FILE=m.xml > m.log 2>&1; python3 check_results.py m.xml 4")


def top(test):
    """Killed only if this test itself fails (TESTCASE reports the rest as skipped)."""
    return f"cd test && make TESTCASE={test} COCOTB_RESULTS_FILE=m.xml > m.log 2>&1"


def pytest(spec):
    return f"PYTHONPATH=tools python -m pytest -q -x -p no:cacheprovider --junitxml=m.xml {spec} > m.log 2>&1"


# name: (file, original text, mutated text, test command)
MUTANTS = {
    "model-alu-borrow": ("tools/pe/sim.py", "c = int(a < s)", "c = int(a <= s)", LOCKSTEP),
    "model-wait-not-sticky": ("tools/pe/sim.py", "self.wevent = int(e.edge_done)", "self.wevent = int(e.ev)", LOCKSTEP),
    "model-join-early-full": ("tools/pe/sim.py", "if core.txf and len(core.rxf) < self.fifo_depth:",
                              "if core.txf and len(core.rxf) < self.fifo_depth - 1:", LOCKSTEP),
    "rtl-wait-not-sticky": ("src/pe_core.sv", "wire        edge_done = wevent || (trans && qual);",
                            "wire        edge_done = (trans && qual);", LOCKSTEP),
    "rtl-join-ignores-full": ("src/pe_fifos.sv", "wire xfer      = fjoin && !txf_empty && !rxf_full;",
                              "wire xfer      = fjoin && !txf_empty;", LOCKSTEP),
    "rtl-pop-without-snapshot": ("src/pe_core.sv", "bus_commit && res_n != 2'd0 && res_v[0];", "bus_commit && res_n != 2'd0;",
                                 top("test_fifo_read_race")),
    "fw-usb-no-token-filter": ("firmware/usb_ls_rx.pasm",
                               "    jmp !z, eop_skip               ; another address or endpoint, or a bad CRC5",
                               "    nop", pytest("test/model/test_usb_model.py -k tokens_for_others")),
    "fw-i2c-data-wait-no-timeout": ("firmware/i2c_master.pasm",
                                    "    wait 1 pin SCL timeout r3     ; clock stretching",
                                    "    wait 1 pin SCL                ; clock stretching",
                                    pytest("test/model/test_i2c_timeout.py")),
    "fw-can-no-stuff-check": ("firmware/can_rx.pasm", "    jmp z, stuff_error                ; S+5   six identical bits",
                              "    nop                               ; S+5", pytest("test/model/test_can.py -k stuff")),
    "fw-eth10-short-tp-idl": ("firmware/eth10_tx.pasm", "    nop                       side 1 [9]",
                              "    nop                       side 1 [5]", pytest("test/model/test_eth10.py -k min_frame")),
    "asm-clkdiv-no-carry": ("tools/pe/isa.py", "    if f == 256:", "    if f == 257:", pytest("test/model/test_asm.py")),
    "rtl-config-in-base-field": ("src/pe_core.sv", "bus_wdata[12], bus_wdata[13], bus_wdata[20:16]}",
                                 "bus_wdata[12], bus_wdata[13], bus_wdata[21:17]}", LOCKSTEP),
    "rtl-line-no-falling-edge-sample": ("src/pe_chip.sv", "assign line_rx_mid = rx_is_pad ? rx_h2 : pins_in[line_rxp];",
                                        "assign line_rx_mid = pins_in[line_rxp];", top("test_eth10_rx_jitter")),
    "model-line-fast-loop": ("tools/pe/line.py", "g = min(5, 1 + self.n // 6)", "g = 1",
                             pytest("test/model/test_eth10.py -k external")),
    "rtl-line-no-tx-stuff": ("src/pe_line.sv", "        if (k != 3'd0 && n_trun == k) begin                      // stuff bit",
                             "        if (1'b0) begin                                          // stuff bit", LOCKSTEP),
    "model-line-manchester-window": ("tools/pe/line.py", "-(48 * p12 if wide else 32 * p12)",
                                     "-(48 * p12 if wide else 44 * p12)", pytest("test/model/test_eth10.py -k external")),
    "rtl-line-nrzi-full-count": ("src/pe_line.sv", "wire [15:0] r_load = {1'b0, p[15:1]} - (edge1 ? 16'd2 : 16'd1);",
                                 "wire [15:0] r_load = {1'b0, p[15:1]} - 16'd1;", LOCKSTEP),
    "model-line-nrzi-full-count": ("tools/pe/line.py", "        return int(h != self.rprev)", "        return 0",
                                   pytest("test/model/test_usb_fs.py -k duty_cycle")),
    "fw-usb-record-no-marker": ("firmware/usb_ls_rx.pasm", "    ldi r1, 0x81                   ; trailer status: good",
                                "    ldi r1, 0x01                   ; trailer status: good",
                                pytest("test/model/test_usb_model.py -k rx_packets")),
}


def result(work, cmd):
    """Only an executed test's failure kills a mutant. Missing XML, collection
    errors, compilation failures and empty selections are infrastructure errors.
    """
    xml = work / ("test/m.xml" if cmd.startswith("cd test") else "m.xml")
    xml.unlink(missing_ok=True)
    rc = subprocess.run(cmd, shell=True, cwd=work, env=os.environ).returncode
    if not xml.exists():
        return "INFRASTRUCTURE (no fresh test results)"
    try:
        cases = ET.parse(xml).findall(".//testcase")
    except ET.ParseError:
        return "INFRASTRUCTURE (invalid test results)"
    if not cases or any(c.find("error") is not None for c in cases):
        return "INFRASTRUCTURE (test collection/execution error)"
    failed = [c.get("name", "?") for c in cases if c.find("failure") is not None]
    if failed:
        return "killed: " + ", ".join(failed)
    if rc != 0 or not any(c.find("skipped") is None for c in cases):
        return "INFRASTRUCTURE (runner failed or no tests executed)"
    return "pass"


def source_files(root):
    files = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=root,
                           capture_output=True, text=True, check=True).stdout.splitlines()
    return [f for f in files if (root / f).is_file()]


def snapshot(scratch):
    """Copy the checkout (tracked and new files) once: every baseline and mutant
    starts from this copy, so edits made while the run goes on don't leak in."""
    work = pathlib.Path(scratch) / "candidate"
    work.mkdir()
    files = source_files(ROOT)
    for name in files:
        (work / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, work / name)
    return work, files


def run(name, scratch, baselines, candidate, files):
    path, old, new, cmd = MUTANTS[name]
    work = pathlib.Path(tempfile.mkdtemp(prefix=f"{name}-", dir=scratch))
    tar = subprocess.Popen(["tar", "cf", "-", "--", *files], cwd=candidate, stdout=subprocess.PIPE)
    subprocess.run(["tar", "xf", "-"], cwd=work, stdin=tar.stdout, check=True)
    if tar.wait() != 0:
        return "INFRASTRUCTURE (source copy failed)"
    f = work / path
    src = f.read_text()
    if src.count(old) != 1:
        return "STALE (mutation site not found)"
    if cmd not in baselines:
        baseline = result(work, cmd)
        baselines[cmd] = baseline
        log = work / ("test/m.log" if cmd.startswith("cd test") else "m.log")
        if log.exists():
            log.rename(log.with_name("baseline.log"))
        xml = work / ("test/m.xml" if cmd.startswith("cd test") else "m.xml")
        if xml.exists():
            xml.rename(xml.with_name("baseline.xml"))
    if baselines[cmd] != "pass":
        return "BASELINE FAILED: " + baselines[cmd]
    f.write_text(src.replace(old, new))
    t = time.time()
    status = result(work, cmd)
    return f"{'SURVIVED' if status == 'pass' else status} ({time.time() - t:.0f} s)"


def main(names):
    scratch = tempfile.mkdtemp(prefix="protoemu-mutants-")
    bad = 0
    baselines = {}
    candidate, files = snapshot(scratch)
    for name in names or MUTANTS:
        r = run(name, scratch, baselines, candidate, files)
        bad += not r.startswith("killed")
        print(f"{name:32s} {r}", flush=True)
    print(f"{len(names or MUTANTS) - bad} of {len(names or MUTANTS)} mutants killed (scratch: {scratch})")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main(sys.argv[1:])
