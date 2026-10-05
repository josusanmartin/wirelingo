"""Compile the board program with a caller-supplied, pinned official assembler.

Download MicroPython v1.26.0 ports/rp2/modules/rp2.py into a private directory,
then pass --rp2-source PATH. Only native PIO constants are stubbed on CPython;
all instruction encoding runs through the verified upstream assembler.
"""
import argparse
import hashlib
import importlib.util
import json
import pathlib
import sys
import types

ROOT = pathlib.Path(__file__).resolve().parents[1]
ASSEMBLER_URL = 'https://raw.githubusercontent.com/micropython/micropython/v1.26.0/ports/rp2/modules/rp2.py'
ASSEMBLER_SHA256 = '095e1e4e44f02510bebd53e7e485a7368d726ced869e593985abfa08bc659f1b'


def compile_program(assembler):
    assembler = pathlib.Path(assembler)
    if hashlib.sha256(assembler.read_bytes()).hexdigest() != ASSEMBLER_SHA256:
        raise ValueError('assembler does not match pinned MicroPython v1.26.0')
    native = types.ModuleType('_rp2')
    native.PIO = type('PIO', (), dict(IN_LOW=0, IN_HIGH=1, OUT_LOW=2, OUT_HIGH=3,
                                    SHIFT_LEFT=0, SHIFT_RIGHT=1, JOIN_NONE=0))
    mpy = types.ModuleType('micropython')
    mpy.const = lambda value: value
    replacements = {'_rp2': native, 'micropython': mpy}
    previous = {name: sys.modules.get(name) for name in (*replacements, 'rp2')}
    try:
        sys.modules.update(replacements)
        spec = importlib.util.spec_from_file_location('rp2', assembler)
        rp2 = importlib.util.module_from_spec(spec)
        sys.modules['rp2'] = rp2
        spec.loader.exec_module(rp2)
        source = ROOT / 'tools/micropython/pio_quad.py'
        program_spec = importlib.util.spec_from_file_location('compiled_pio_quad', source)
        module = importlib.util.module_from_spec(program_spec)
        program_spec.loader.exec_module(module)
        program = module.spi_stream
        return dict(assembler_url=ASSEMBLER_URL, assembler_sha256=ASSEMBLER_SHA256,
                    source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                    instructions=list(program[0]), execctrl=program[4], shiftctrl=program[5],
                    out_init=list(program[6]), sideset_init=program[8])
    finally:
        for name, module in previous.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rp2-source', type=pathlib.Path, required=True)
    parser.add_argument('--output', type=pathlib.Path,
                        help='default: print JSON; name a fresh output explicitly to write')
    args = parser.parse_args()
    data = json.dumps(compile_program(args.rp2_source), indent=2) + '\n'
    if args.output:
        args.output.write_text(data)
    else:
        print(data, end='')
