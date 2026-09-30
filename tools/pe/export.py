"""Export an assembled program as a MicroPython snippet for tools/micropython/protoemu.py.

    python -m pe.export firmware/uart_tx.pasm --core 0 --define TX=8 --out-pins 8,1 --side-pins 8 --clkdiv 54.25
"""
import argparse

from . import asm, host


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--core", type=int, default=0)
    ap.add_argument("--base", type=int)
    ap.add_argument("--define", action="append", default=[], help="NAME=value")
    ap.add_argument("--out-pins")
    ap.add_argument("--set-pins")
    ap.add_argument("--side-pins", type=int)
    ap.add_argument("--in-pins", type=int)
    ap.add_argument("--clkdiv", type=float)
    a = ap.parse_args()
    defs = {k: int(v, 0) for k, v in (d.split("=") for d in a.define)}
    prog = asm.assemble(open(a.file).read(), defs)
    base = a.base if a.base is not None else a.core * 256
    kw = {}
    if a.out_pins:
        kw["out_pins"] = tuple(int(x) for x in a.out_pins.split(","))
    if a.set_pins:
        kw["set_pins"] = tuple(int(x) for x in a.set_pins.split(","))
    if a.side_pins is not None:
        kw["side_pins"] = a.side_pins
    if a.in_pins is not None:
        kw["in_pins"] = a.in_pins
    if a.clkdiv:
        i = int(a.clkdiv)
        kw["clkdiv"] = (i << 8) | int(round((a.clkdiv - i) * 256))
    c = host.core_config(prog, **kw)
    words = prog.relocate(base)
    wrap = (base + prog.wrap_bottom) | ((base + prog.wrap_top) << 16)
    print(f"# {prog.name}: {len(words)} words at {base}")
    print(f"pe.load(core={a.core}, base={base}, words=[{', '.join(hex(w) for w in words)}],")
    print(f"        clkdiv={c['clkdiv']:#x}, pinctrl0={c['pinctrl0']:#x}, pinctrl1={c['pinctrl1']:#x},")
    print(f"        shiftctrl={c['shiftctrl']:#x}, wrap={wrap:#x})")


if __name__ == "__main__":
    main()
