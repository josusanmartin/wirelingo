#!/usr/bin/env python3
"""Mutation check for the blind-test firmware (docs/blind-test.md). Each mutant
is one bug in firmware/hd44780_4bit.pasm or firmware/ata_pio.pasm, run in a
scratch copy against the model tests named beside it. A mutant is killed only
if those tests fail, after a baseline of the same tests has passed. Exit 1 if
any mutant survives. test/mutants.py is left as it is.

    python3 test/blind_mutants.py [name ...]      # from the repo root, with pytest

Run it on runner1: PYTHONPATH=tools, test-venv's python.
"""
import concurrent.futures as cf
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
HD, ATA = "firmware/hd44780_4bit.pasm", "firmware/ata_pio.pasm"
HT, AT = "test/model/test_hd44780.py", "test/model/test_ata_pio.py"

# name: (file, original, mutated, test file, -k expression)
MUTANTS = {
    "hd-bf-read-one-nibble": (HD, "    nop side 1                [6]     ; @point re3",
                              "    nop                       [6]     ; @point re3", HT, "bf_ac_read"),
    "hd-no-busy-poll": (HD, "    call busy_wait\n    jmp r1, bf_stuck", "    nop\n    nop", HT, "polled_through_clear"),
    "hd-short-e-write": (HD, "    nop side 1                [5]     ; @point we1",
                         "    nop side 1                [3]     ; @point we1", HT, "smallest_figures"),
    "hd-sample-before-tddr": (HD, "    nop side 1                [6]     ; @point re1\n    in pins, 4                        ; @point rs1\n    nop side 0                [7]     ; @point re2",
                              "    nop side 1                [2]     ; @point re1\n    in pins, 4                        ; @point rs1\n    nop side 0                [11]    ; @point re2",
                              HT, "bf_ac_read"),
    "hd-low-nibble-first": (HD, "    shr r1, 4\n    mov pins, r1                      ; low 4 bits of r1 reach DB",
                            "    nop\n    mov pins, r1                      ; low 4 bits of r1 reach DB", HT, "init_and_write"),
    "hd-no-tadd-wait": (HD, "    nop                       [63]    ; tADD: 17 us with the next two lines\n    nop                       [63]\n    nop                       [41]\n",
                        "", HT, "any_poll_phase"),
    "hd-init-first-wait-short": (HD, "ldi r2, 2343 ", "ldi r2, 100  ", HT, "init_and_write"),
    "hd-init-second-wait-short": (HD, "ldi r2, 63 ", "ldi r2, 4  ", HT, "init_and_write"),
    "hd-init-power-wait-short": (HD, "ldi r2, 6500 ", "ldi r2, 3000 ", HT, "init_and_write"),
    "hd-init-only-two-0011": (HD, "    ldi r2, 63                        ; 410 us (> 100 us)\n    call delay\n    ldi r1, 3\n    call nibble\n",
                              "    ldi r2, 63                        ; 410 us (> 100 us)\n    call delay\n", HT, "recovers"),
    "hd-db-left-driven": (HD, "    mov pindirs, null\n    mov isr, null\n    push block\n    jmp top\nread_dr:",
                          "    nop\n    mov isr, null\n    push block\n    jmp top\nread_dr:", HT, "init_and_write or contended or read_ddram"),
    "hd-no-stuck-report": (HD, "    jmp r1, bf_stuck", "    nop", HT, "stuck_busy"),
    "hd-rs-ignored": (HD, "    pin RS, hi\n", "    pin RS, lo\n", HT, "init_and_write"),

    "ata-short-strobe": (ATA, "    nop                       side 2 [12]     ; @point rs0",
                         "    nop                       side 2 [6]      ; @point rs0", AT, "register_round_trip"),
    "ata-short-write-strobe": (ATA, "    nop                       side 1 [12]     ; @point ws0",
                               "    nop                       side 1 [6]      ; @point ws0", AT, "register_round_trip"),
    "ata-no-recovery": (ATA, "    jmp r0--, bbyte           [8]", "    jmp r0--, bbyte", AT, "read_sector"),
    "ata-no-write-recovery": (ATA, "    jmp r0--, bwbyte          [5]", "    jmp r0--, bwbyte", AT, "write_sector"),
    "ata-sample-after-strobe": (ATA, "    in pins, 8                side 3          ; @point rs1\n    ret                       [3]",
                                "    nop                       side 3 [3]      ; @point rs1\n    in pins, 8\n    ret", AT, "read_sector"),
    "ata-iordy-ignored-on-read": (ATA, "    ldi r3, ITO\n    wait 1 pin IORDY timeout r3\n    jmp t, rlate",
                                  "    nop\n    nop\n    nop", AT, "iordy_extends"),
    "ata-iordy-ignored-on-write": (ATA, "    ldi r3, ITO\n    wait 1 pin IORDY timeout r3\n    jmp t, wlate",
                                   "    nop\n    nop\n    nop", AT, "iordy_write"),
    "ata-iordy-no-flag": (ATA, "rlate:\n    flag set FIO\n", "rlate:\n    nop\n", AT, "iordy_stuck"),
    "ata-no-drq-check": (ATA, "    jmp !z, refuse", "    nop", AT, "refuse"),
    "ata-refused-write-no-drain": (ATA, "    cmp r0, 4\n    jmp !z, done\ndrain:", "    jmp done\n    nop\ndrain:", AT, "refused_write"),
    "ata-write-data-not-driven": (ATA, "    mov pindirs, r3\n    call wr_strobe", "    nop\n    call wr_strobe", AT, "register_round_trip"),
    "ata-poll-wrong-register": (ATA, "op_wait:\n    ldi r1, 0x0E00", "op_wait:\n    ldi r1, 0x0F00", AT, "software_reset"),
    "ata-wait-no-timeout-bit": (ATA, "    or r1, 0x100\n", "    nop\n", AT, "bsy_stuck"),
    "ata-address-late": (ATA, "    mov pins, r1                      ; @point addr\n    jmp !r0, op_write",
                         "    jmp !r0, op_write\n    nop", AT, "register_round_trip"),
    "ata-autopush": (ATA, ".shift_in left\n", ".shift_in left autopush 32\n", AT, "rx_full"),
    "ata-block-count-off": (ATA, "    jmp r2--, bread\n", "    jmp !r2, done\n    sub r2, 1\n    jmp !r2, done\n    jmp bread\n", AT, "read_sector"),
}


def pytest_cmd(test, k, out):
    return ([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", "-k", k, test], out)


def run(name):
    if name is None:
        return None, True, ""
    file, a, b, test, k = MUTANTS[name]
    with tempfile.TemporaryDirectory(prefix="bm-") as tmp:
        tmp = pathlib.Path(tmp)
        for d in ("firmware", "tools"):
            shutil.copytree(ROOT / d, tmp / d, ignore=shutil.ignore_patterns("__pycache__"))
        (tmp / "test").mkdir()
        shutil.copytree(ROOT / "test/model", tmp / "test/model", ignore=shutil.ignore_patterns("__pycache__"))
        p = tmp / file
        s = p.read_text()
        if a not in s:
            return name, None, "pattern not found"
        p.write_text(s.replace(a, b))
        env = dict(os.environ, PYTHONPATH="tools", PYTHONDONTWRITEBYTECODE="1")
        cmd, _ = pytest_cmd(test, k, None)
        try:
            r = subprocess.run(cmd, cwd=tmp, env=env, capture_output=True, text=True, timeout=400)
        except subprocess.TimeoutExpired:
            return name, True, "timeout (hang)"
        tail = [l for l in r.stdout.splitlines() if l.startswith(("FAILED", "ERROR")) or " passed" in l or " failed" in l]
        return name, r.returncode == 1, " | ".join(tail)[:150]


def baseline():
    env = dict(os.environ, PYTHONPATH="tools", PYTHONDONTWRITEBYTECODE="1")
    for test in (HT, AT):
        ks = " or ".join(sorted({m[4] for m in MUTANTS.values() if m[3] == test}))
        cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-n", "8", "-k", ks, test]
        r = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True)
        print(f"baseline {test}: {r.stdout.splitlines()[-1] if r.stdout else r.stderr[-200:]}", flush=True)
        if r.returncode:
            sys.exit("baseline failed: mutants mean nothing")


def main():
    names = sys.argv[1:] or list(MUTANTS)
    baseline()
    bad = 0
    with cf.ThreadPoolExecutor(8) as ex:
        for name, killed, why in ex.map(run, names):
            if killed is None:
                print(f"BROKEN   {name}: {why}", flush=True)
                bad += 1
            elif killed:
                print(f"killed   {name}: {why}", flush=True)
            else:
                print(f"SURVIVED {name}: {why}", flush=True)
                bad += 1
    print(f"{len(names) - bad}/{len(names)} killed")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
