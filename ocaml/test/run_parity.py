#!/usr/bin/env python3
"""Stage 4 check: wl_run (OCaml: assembler, host, modelhost, cycle model) against
the same run in Python (pe.asm, pe.host, pe.modelhost, pe.sim).

    python3 ocaml/test/run_parity.py

Each case loads firmware on core 0 at address 0, pushes words to its TX FIFO, runs
and prints every pin change and the words left in RXF. The two outputs must match
byte for byte. The cases are firmware that runs with only a FIFO behind it; peers
on the pins (USB hosts, I2C devices) are what the model suite's lockstep covers.

SPDX-License-Identifier: Apache-2.0
"""
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))
from pe import asm, host, sim  # noqa: E402
from pe.modelhost import ModelHost  # noqa: E402

WL = str(REPO / "ocaml/_build/default/bin/wl_run.exe")

# firmware, defines, load options, pushes, cycles
CASES = [
    ("uart_tx", {"TX": 8}, {"out_pins": (8, 1), "clkdiv": 2}, [0x55, 0xA3, 0x00, 0xFF], 4000),
    ("ws2812", {}, {"side_pins": 8, "clkdiv": 2}, [0x00FF0080, 0x12345678], 4000),
    ("spi_master", {}, {"out_pins": (8, 1), "side_pins": 9, "in_pins": 0, "clkdiv": 2}, [0x5A000000, 0xC3000000], 3000),
    ("dmx512_tx", {"TX": 8, "MTBS": 2}, {"out_pins": (8, 1), "clkdiv": 2}, [1, 2, 3], 6000),
    ("eth10_tx", {"F_GO": 0}, {"clkdiv": 2}, [0x12345678, 0x9ABCDEF0], 4000),
    ("i2s_tx", {}, {"clkdiv": 2}, [0x7FFF0000, 0x80000001], 4000),
    ("dshot600", {"GAP": 40}, {"clkdiv": 2}, [0xA5A5], 4000),
    ("nec_tx", {"IR": 8}, {"clkdiv": 2}, [0x00FF12ED], 8000),
]


def run_python(name, defines, opts, pushes, cycles):
    prog = asm.assemble((REPO / "firmware" / f"{name}.pasm").read_text(), defines)
    o = dict(opts)
    clk = o.pop("clkdiv", None)
    mh = ModelHost()
    mh.apply(host.load_program(0, prog, 0, **o, **({"clkdiv": (clk << 8)} if clk else {})))
    mh.start(1)
    for w in pushes:
        mh.push(0, w)
    out, last = [], None

    def watch(_, m):
        nonlocal last
        now = m.chip.outputs()
        if now != last:
            out.append(f"{m.chip.cycle} {now[0]} {now[1]} {now[2]}\n")
            last = now

    mh.run(cycles, watch)
    out += [f"rxf {w}\n" for w in mh.chip.cores[0].rxf]
    return "".join(out)


def run_ocaml(name, defines, opts, pushes, cycles):
    a = [WL, str(REPO / "firmware" / f"{name}.pasm"), "--cycles", str(cycles)]
    for k, v in defines.items():
        a += ["--define", f"{k}={v}"]
    for k, v in opts.items():
        flag = "--" + k.replace("_", "-")
        a += [flag, ",".join(map(str, v)) if isinstance(v, tuple) else str(v)]
    for w in pushes:
        a += ["--push", str(w)]
    r = subprocess.run(a, capture_output=True, text=True)
    return r.stdout + r.stderr


def main():
    bad = changes = 0
    for c in CASES:
        py, ml = run_python(*c), run_ocaml(*c)
        same = py == ml
        changes += py.count("\n")
        print(f"{c[0]:12s} {py.count(chr(10)):6d} lines {'agree' if same else 'DISAGREE'}")
        if not same:
            bad += 1
            for i, (a, b) in enumerate(zip(py.split("\n"), ml.split("\n"))):
                if a != b:
                    print(f"  line {i}: py {a!r} ml {b!r}")
                    break
            else:
                print(f"  lengths {len(py)} {len(ml)}; ml tail {ml[-200:]!r}")
    print(f"{len(CASES)} firmware runs, {changes} lines compared, {bad} disagree")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
