#!/usr/bin/env python3
"""Mutation check: each mutant is a one-line bug in the model, the RTL, a
firmware program or the assembler, paired with the test that must catch it.
Each runs in a scratch copy of the tracked files; the working tree is never
touched. Exit status 1 if any mutant survives.

    python3 test/mutants.py [name ...]      # from the repo root (needs the test env)
"""
import os
import pathlib
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]

LOCKSTEP = ("cd test && LOCKSTEP_SEEDS=6 LOCKSTEP_CYCLES=800 PE_NCORES=2 PE_NBANKS=2 PE_FIFO_LOG2=3 "
            "make TB=chip COCOTB_RESULTS_FILE=m.xml > m.log 2>&1; python3 check_results.py m.xml 3")


def top(test):
    """Killed only if this test itself fails (TESTCASE reports the rest as skipped)."""
    return f"cd test && make TESTCASE={test} > m.log 2>&1; ! grep -Eq 'test\\.{test} +FAIL' m.log"


def pytest(spec):
    return f"PYTHONPATH=tools python -m pytest -q -x -p no:cacheprovider {spec} > m.log 2>&1"


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
    "rtl-line-no-tx-stuff": ("src/pe_line.sv", "        if (k != 3'd0 && n_trun == k) begin                      // stuff bit",
                             "        if (1'b0) begin                                          // stuff bit", LOCKSTEP),
    "model-line-manchester-window": ("tools/pe/line.py", "if trans and self.mt >= (3 * p) >> 2:",
                                     "if trans and self.mt >= p >> 1:", LOCKSTEP),
    "fw-usb-record-no-marker": ("firmware/usb_ls_rx.pasm", "    ldi r1, 0x81                   ; trailer status: good",
                                "    ldi r1, 0x01                   ; trailer status: good",
                                pytest("test/model/test_usb_model.py -k rx_packets")),
}


def run(name, scratch):
    path, old, new, cmd = MUTANTS[name]
    work = pathlib.Path(tempfile.mkdtemp(prefix=f"{name}-", dir=scratch))
    files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    tar = subprocess.Popen(["tar", "cf", "-", *files], cwd=ROOT, stdout=subprocess.PIPE)
    subprocess.run(["tar", "xf", "-"], cwd=work, stdin=tar.stdout, check=True)
    tar.wait()
    f = work / path
    src = f.read_text()
    if src.count(old) != 1:
        return "STALE (mutation site not found)"
    f.write_text(src.replace(old, new))
    t = time.time()
    caught = subprocess.run(cmd, shell=True, cwd=work, env=os.environ).returncode != 0
    return f"{'killed' if caught else 'SURVIVED'} ({time.time() - t:.0f} s)"


def main(names):
    scratch = tempfile.mkdtemp(prefix="protoemu-mutants-")
    bad = 0
    for name in names or MUTANTS:
        r = run(name, scratch)
        bad += not r.startswith("killed")
        print(f"{name:32s} {r}", flush=True)
    print(f"{len(names or MUTANTS) - bad} of {len(names or MUTANTS)} mutants killed (scratch: {scratch})")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main(sys.argv[1:])
