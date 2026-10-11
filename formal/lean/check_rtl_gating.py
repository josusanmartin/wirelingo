#!/usr/bin/env python3
"""Finite pinned pe_core / accepted Lean correspondence; no Python core model.

Explicit inputs select tools and a pinned accepted reference; every replay
uses a fresh output directory and one sequential <=90s invocation. The checker
never builds/replaces accepted objects/probe. --declare-only has the same
input/output safety checks and invokes no compiler or simulator.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BASELINE = '8e47d0c9f201cab275e0f0c53eb6aaa1e588c9dc'
# Location-independent identity of the explicitly supplied accepted receipt.
# SHA256(JSON with sorted keys and compact separators), schema1, baseline,
# accepted probe, repository inputs and exact accepted compiler-object bytes.
REFERENCE_IDENTITY_SHA256 = '5155ceb41cc12ea9f9c533baaa7275430c43b9aaaa11ef63d976be3660137277'
# di, df, cnt, facc, dcnt, halted, fault, in_wait, event, scenario, delay, budget
ROWS = ((1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 6),
        (2, 0, 0, 0, 0, 0, 0, 1, 0, 1, 1, 65535),
        (4, 0, 1, 128, 3, 0, 0, 1, 0, 0, 7, 257),
        (1, 1, 0, 255, 0, 0, 0, 1, 0, 0, 1, 2),
        (2, 128, 0, 128, 0, 0, 0, 1, 0, 2, 7, 65535),
        (4, 255, 65535, 254, 255, 1, 0, 1, 1, 1, 1, 65535),
        (0, 0, 0, 0, 0, 0, 1, 1, 0, 2, 7, 65535),
        (0, 255, 0, 255, 0, 0, 0, 1, 1, 1, 7, 0))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def corpus():
    traces = []
    for idx in (0, 1):
        for kind in (2, 3, 4, 7):
            for row, values in enumerate(ROWS):
                di, df, cnt, facc, dcnt, halted, fault, iw, ev, scenario, delay, budget = values
                treg = row % 4
                operand = (kind << 16) | (1 << 10) | (treg << 8)
                operand |= (treg << 14) | 255 if kind == 7 else (1 << 6) | (1 << 5) | 1
                regs = [0, 0x55, 0xAA, 0xFF]
                regs[treg] = budget
                initial = [operand, idx * 16, di, df, delay, iw, 0, ev,
                           *regs, row % 2, cnt, facc, dcnt, halted, fault]
                drives = []
                for clock in range(32):
                    enable = not (scenario == 1 and 3 <= clock <= 8 or scenario == 2 and 5 <= clock <= 6)
                    restart = scenario == 1 and clock == 6 or scenario == 2 and clock == 12
                    pcw = scenario == 1 and clock == 7 or scenario == 2 and clock == 4
                    pad = int(clock in (1, 9, 17, 25)) | (int(not (scenario == 2 and clock < 7)) << 1)
                    pins = (pad & 255) | ((pad & 31) << 16)
                    drives.append([pins, int(enable), int(restart), int(pcw)])
                word = (1 << 27) | (delay << 19) | operand
                traces.append({'id': f'idx{idx}-kind{kind}-row{row}', 'idx': idx,
                    'kind': kind, 'row': row, 'initial': initial, 'drives': drives,
                    'instruction_word_hex': f'{word:08x}',
                    'instruction_bytes_le_hex': word.to_bytes(4, 'little').hex()})
    return traces


def input_text(trace, bad_bank=False):
    return (f'{len(trace["drives"])} {int(bad_bank)}\n' +
            ' '.join(map(str, trace['initial'])) + '\n' +
            '\n'.join(' '.join(map(str, d)) for d in trace['drives']) + '\n')


def declare(output):
    traces = corpus()
    path = output / 'predeclared-traces.json'
    text = json.dumps({'baseline': BASELINE, 'reset_clocks': 2,
        'deposits_once_per_trace': 1, 'traces': traces}, indent=2) + '\n'
    path.write_text(text)
    return traces


class Invocation:
    def __init__(self, inputs):
        self.number = 1
        self.inputs = inputs
        self.directory = inputs.output / 'validation-1'
        self.directory.mkdir(exist_ok=False)
        self.temporary_directory = self.directory / 'tmp'
        self.temporary_directory.mkdir()
        self.start = time.monotonic()
        self.commands = []
        self.dependencies = set()
        self.rtl_cycles = self.deposits = self.reset_cycles = self.comparison_cycles = 0

    def run(self, argv, name, env=None):
        remaining = 90 - (time.monotonic() - self.start)
        if remaining <= 0:
            raise RuntimeError('90-second invocation cap reached')
        trace_path = self.directory / f'{name}.system.log'
        wrapped = [str(self.inputs.strace), '-f', '-e', 'trace=execve,openat',
                   '-o', str(trace_path), *map(str, argv)]
        before = time.monotonic()
        environment = {**(env if env is not None else os.environ),
                       'TMPDIR': str(self.temporary_directory)}
        result = subprocess.run(wrapped, cwd=HERE, env=environment, text=True,
            capture_output=True, timeout=remaining)
        (self.directory / f'{name}.stdout.log').write_text(result.stdout)
        (self.directory / f'{name}.stderr.log').write_text(result.stderr)
        self.commands.append({'argv': list(map(str, argv)), 'instrumented_argv': wrapped,
            'cwd': str(HERE), 'exit': result.returncode,
            'elapsed_seconds': time.monotonic() - before,
            'LEAN_PATH': environment.get('LEAN_PATH'),
            'TMPDIR': environment['TMPDIR']})
        for line in trace_path.read_text().splitlines():
            if ' = -1 ' in line or 'O_WRONLY' in line or 'O_RDWR' in line:
                continue
            match = re.search(r'(?:execve\(|openat\([^,]+, )"([^"]+)"', line)
            if match:
                p = Path(match[1])
                p = p if p.is_absolute() else HERE / p
                if p.is_file():
                    self.dependencies.add(p.resolve())
        self.dependencies.add(self.inputs.strace)
        # Count all edges reported before any failure, including reset edges.
        resets = sum(int(n) for n in re.findall(r'^RESET (\d+)$', result.stdout, re.M))
        rows = len(re.findall(r'^ROW ', result.stdout, re.M))
        self.reset_cycles += resets
        self.comparison_cycles += rows
        self.rtl_cycles += resets + rows
        self.deposits += len(re.findall(r'^DEPOSIT ', result.stdout, re.M))
        if result.returncode:
            raise RuntimeError(f'{name}: exit {result.returncode}; see retained logs')
        return result.stdout

    def simulate(self, binary, trace, name, bad_bank=False):
        path = self.directory / f'{name}.input.txt'
        path.write_text(input_text(trace, bad_bank))
        output = self.run([self.inputs.vvp, binary, f'+INPUT={path}'], name)
        rows = [list(map(int, line.split()[1:])) for line in output.splitlines() if line.startswith('ROW ')]
        assert len(rows) == len(trace['drives']), 'missing RTL clocks'
        assert all(len(row) == 23 for row in rows), 'unexpected RTL field count'
        return rows, output


def first_mismatch(trace, expected, rows):
    for clock, (prediction, row) in enumerate(zip(expected, rows, strict=True)):
        if prediction != row[1:16]:
            return {'trace': trace['id'], 'initial': trace['initial'],
                'instruction_word_hex': trace['instruction_word_hex'], 'clock': clock,
                'drive_prefix': trace['drives'][:clock + 1], 'Lean': prediction,
                'RTL': row[1:16], 'RTL_extra': row[16:]}
    return None


def validate(inv, traces):
    # Audit new manually transcribed arithmetic without changing accepted modules
    # or putting build artifacts in .lake. Resolve the existing Lean environment.
    lean = inv.inputs.lean
    lean_path = str(inv.inputs.lean_objects) + ':' + str(inv.inputs.lean_lib)
    build = inv.directory / 'lean-build'
    (build / 'Wirelingo').mkdir(parents=True)
    # Lean resolves a namespace from one search root. Populate this evidence-only
    # root with exact accepted object copies; do not rebuild or alter .lake.
    accepted_objects = {}
    for source in (inv.inputs.lean_objects / 'Wirelingo').glob('*.olean*'):
        target = build / 'Wirelingo' / source.name
        shutil.copyfile(source, target)
        accepted_objects[str(source)] = digest(source)
        assert digest(target) == digest(source)
    (build / 'accepted-object-hashes.json').write_text(json.dumps(accepted_objects, indent=2) + '\n')
    inv.run([lean, '-DwarningAsError=true', '-o', build / 'Wirelingo/RtlGating.olean',
             HERE / 'Wirelingo/RtlGating.lean'], 'arithmetic-build',
            {**os.environ, 'LEAN_PATH': lean_path})
    audit = inv.run([lean, '-DwarningAsError=true', HERE / 'RtlGatingAudit.lean'], 'arithmetic-audit',
                   {**os.environ, 'LEAN_PATH': str(build) + ':' + lean_path})
    declared = {'Wirelingo.' + n for n in re.findall(r'^theorem (\w+)',
                 (HERE / 'Wirelingo/RtlGating.lean').read_text(), re.M)}
    audited = set(re.findall(r"'([^']+)' (?:depends on axioms|does not depend on any axioms)", audit))
    assert declared == audited and len(declared) == 4, 'incomplete arithmetic audit'
    for deps in re.findall(r'depends on axioms: \[([^\]]*)\]', audit):
        assert set(deps.split(', ')) <= {'propext', 'Classical.choice', 'Quot.sound'}
    assert digest(inv.inputs.probe) == inv.inputs.provenance['probe_sha256']
    commands = []
    for t in traces:
        commands.append('0 ' + ' '.join(map(str, t['initial'])))
        commands.extend('1 ' + ' '.join(map(str, d)) for d in t['drives'])
    probe_input = inv.directory / 'lean-input.txt'
    probe_input.write_text('\n'.join(commands) + '\n')
    # Input redirection via a shell uses only a generated constant evidence path.
    predicted = inv.run(['/bin/sh', '-c', 'exec "$1" < "$2"', 'probe', inv.inputs.probe, probe_input], 'accepted-lean-probe')
    flat = [list(map(int, row.split())) for row in predicted.splitlines()]
    assert len(flat) == 2048 and all(len(row) == 15 for row in flat)
    expected = {t['id']: flat[i * 32:(i + 1) * 32] for i, t in enumerate(traces)}
    sources = [ROOT / f'src/{n}.sv' for n in ('pe_core', 'pe_fifo', 'pe_fifos', 'pe_cg')]
    binaries = {}
    for idx in (0, 1):
        binary = inv.directory / f'idx{idx}.vvp'
        inv.run([inv.inputs.iverilog, '-v', '-g2012', '-s', 'rtl_gating_tb',
            '-P', f'rtl_gating_tb.IDX={idx}', '-o', binary, HERE / 'rtl_gating_tb.sv', *sources], f'compile-idx{idx}')
        binaries[idx] = binary
    captures = disabled_restarts = disabled_cancels = 0
    for t in traces:
        rows, _ = inv.simulate(binaries[t['idx']], t, t['id'])
        mismatch = first_mismatch(t, expected[t['id']], rows)
        if mismatch:
            (inv.directory / 'semantic-mismatch.json').write_text(json.dumps(mismatch, indent=2) + '\n')
            raise RuntimeError('positive RTL/Lean mismatch; freeze without semantic change')
        assert all(row[-1] == t['idx'] * 256 and row[19] == 1 for row in rows), 'own bank/self wrap violated'
        prior_ev = t['initial'][7]
        for drive, row in zip(t['drives'], rows, strict=True):
            captures += int(not row[16] and not prior_ev and row[3])
            disabled_restarts += int(not drive[1] and drive[2])
            disabled_cancels += int(not drive[1] and drive[3])
            prior_ev = row[3]
    assert captures > 0 and disabled_restarts and disabled_cancels, 'missing required discriminating coverage'
    violation = dict(traces[0], id='assumption-wrong-bank', drives=[[0, 1, 0, 0]])
    rows, diagnostic = inv.simulate(binaries[0], violation, violation['id'], bad_bank=True)
    assert rows[0][12:16] == [1, 1, 0, 0] and rows[0][18:20] == [1, 0]
    assert 'ASSUMPTION wrong-bank attempt=1 bank_ok=0 exec=0 halted=1 fault=1' in diagnostic
    (inv.directory / 'assumption-violation.json').write_text(json.dumps({'name': 'wrong-bank',
        'positive_trace': False, 'initial': violation['initial'], 'pc': 256,
        'drive': violation['drives'], 'expected_diagnostic': 'attempt=1 bank_ok=0 exec=0 halted=1 fault=1',
        'observed': rows}, indent=2) + '\n')
    mutant_dir = inv.directory / 'non-product-mutant'
    mutant_dir.mkdir()
    original = sources[0].read_text()
    needle = "div_int - 16'd1 + {15'd0, fsum[8]}"
    assert original.count(needle) == 1
    mutant = mutant_dir / 'pe_core.sv'
    mutant.write_text('// NON-PRODUCT C4 carry-removal negative artifact\n' +
                      original.replace(needle, "div_int - 16'd1"))
    delta = subprocess.run(['git', 'diff', '--no-index', '--', str(sources[0]), str(mutant)],
                            text=True, capture_output=True)
    assert delta.returncode == 1
    (mutant_dir / 'source.delta.patch').write_text(delta.stdout)
    binary = mutant_dir / 'idx0.vvp'
    inv.run([inv.inputs.iverilog, '-v', '-g2012', '-s', 'rtl_gating_tb',
        '-P', 'rtl_gating_tb.IDX=0', '-o', binary, HERE / 'rtl_gating_tb.sv',
        mutant, *sources[1:]], 'compile-carry-mutant')
    mutant_trace = next(t for t in traces if t['idx'] == 0 and t['kind'] == 2 and t['row'] == 4)
    rows, _ = inv.simulate(binary, mutant_trace, 'carry-mutant')
    mismatch = first_mismatch(mutant_trace, expected[mutant_trace['id']], rows)
    assert mismatch, 'actual RTL fractional-carry mutant survived'
    (inv.directory / 'rtl-mutant-counterexample.json').write_text(json.dumps(mismatch, indent=2) + '\n')
    return {'result': 'pass', 'positive_traces': len(traces), 'positive_comparison_clocks': 2048,
        'total_RTL_clocks_including_reset_and_negatives': inv.rtl_cycles,
        'reset_clocks': inv.reset_cycles, 'one_time_deposits': inv.deposits,
        'incidental_model_cycles': 0, 'new_arithmetic_theorems_audited': 4,
        'non_tick_event_captures': captures, 'disabled_restart_clocks': disabled_restarts,
        'disabled_PC_cancel_clocks': disabled_cancels,
        'RTL_mutants_rejected': ['dropped-fractional-carry'], 'earliest_mutant_clock': mismatch['clock'],
        'reference_probe_sha256': digest(inv.inputs.probe), 'instruction_fetch': 'stable external WAIT word; no SRAM model',
        'logical_input_boundary': 'pins_in; effective enable/restart; same-PC host cancellation'}


def validate_inputs(args):
    # This entire function is read-only. No mkdir, declaration or subprocess
    # precedes successful validation of destination, tools and accepted identity.
    proposed = args.output.expanduser().absolute()
    if os.path.lexists(proposed):
        raise ValueError('output already exists; use a fresh nonexistent destination')
    args.output = proposed.resolve()
    if not args.output.parent.is_dir() or not os.access(args.output.parent, os.W_OK):
        raise ValueError('output parent must already exist and be writable')
    for name in ('reference', 'probe', 'iverilog', 'vvp', 'lean', 'strace'):
        path = getattr(args, name).expanduser().resolve()
        if not path.is_file() or not os.access(path, os.R_OK):
            raise ValueError(f'{name}: missing readable input file')
        if name != 'reference' and not os.access(path, os.X_OK):
            raise ValueError(f'{name}: executable permission required')
        setattr(args, name, path)
    for name in ('lean_lib', 'lean_objects'):
        path = getattr(args, name).expanduser().resolve()
        if not path.is_dir():
            raise ValueError(f'{name}: missing input directory')
        setattr(args, name, path)
    if not (args.lean_lib / 'Init.olean').is_file():
        raise ValueError('lean-lib must contain the existing Lean standard library')
    provenance = json.loads(args.reference.read_text())
    canonical = json.dumps(provenance, sort_keys=True, separators=(',', ':')).encode()
    if hashlib.sha256(canonical).hexdigest() != REFERENCE_IDENTITY_SHA256:
        raise ValueError('accepted reference identity mismatch')
    if provenance['baseline'] != BASELINE:
        raise ValueError('accepted baseline mismatch')
    if digest(args.probe) != provenance['probe_sha256']:
        raise ValueError('accepted probe hash mismatch')
    for relative, expected in provenance['repository_sha256'].items():
        path = ROOT / relative
        if not path.is_file() or digest(path) != expected:
            raise ValueError(f'accepted repository hash mismatch: {relative}')
    objects = args.lean_objects / 'Wirelingo'
    actual = {p.name: digest(p) for p in objects.glob('*.olean*') if p.is_file()}
    if actual != provenance['objects_sha256']:
        raise ValueError('accepted Lean object hashes/set mismatch')
    # Never put output inside the supplied read-only compiler/library roots.
    if any(args.output.is_relative_to(p) for p in (HERE, args.lean_objects, args.lean_lib)):
        raise ValueError('output overlaps a read-only source/compiler input root')
    args.provenance = provenance
    return args


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='fresh nonexistent directory; parent exists')
    parser.add_argument('--reference', type=Path, required=True, help='pinned schema1 provenance JSON')
    parser.add_argument('--probe', type=Path, required=True, help='existing accepted gating_probe executable')
    parser.add_argument('--iverilog', type=Path, required=True, help='actual Icarus compiler executable/wrapper')
    parser.add_argument('--vvp', type=Path, required=True, help='actual Icarus runtime executable/wrapper')
    parser.add_argument('--lean', type=Path, required=True, help='existing Lean4.28 executable')
    parser.add_argument('--lean-lib', type=Path, required=True, help='existing Lean standard library root')
    parser.add_argument('--lean-objects', type=Path, required=True, help='accepted object root containing Wirelingo/')
    parser.add_argument('--strace', type=Path, required=True, help='existing syscall tracer executable')
    parser.add_argument('--declare-only', action='store_true')
    try:
        args = validate_inputs(parser.parse_args())
    except (ValueError, OSError, KeyError, TypeError) as error:
        parser.error(str(error))
    try:
        args.output.mkdir(exist_ok=False)
    except OSError as error:
        parser.error(f'cannot create fresh output: {error}')
    selected = {name: str(getattr(args, name)) for name in
                ('output', 'reference', 'probe', 'iverilog', 'vvp', 'lean', 'lean_lib', 'lean_objects', 'strace')}
    input_receipt = {'baseline': BASELINE, 'accepted_identity_sha256': REFERENCE_IDENTITY_SHA256,
        'selected_paths': selected, 'explicit_file_sha256': {name: digest(getattr(args, name)) for name in
                ('reference', 'probe', 'iverilog', 'vvp', 'lean', 'strace')}, 'accepted_reference': args.provenance}
    (args.output / 'input-receipt.json').write_text(json.dumps(input_receipt, indent=2) + '\n')
    (args.output / 'reference-provenance.json').write_bytes(args.reference.read_bytes())
    traces = declare(args.output)
    if args.declare_only:
        (args.output / 'declaration-ledger.json').write_text(json.dumps(
            {'result': 'declared', 'RTL_clocks': 0, 'incidental_model_cycles': 0, 'compiler_invocations': 0}, indent=2) + '\n')
        print('Declared 64 immutable positive traces; no tools/tests invoked')
        return
    inv = Invocation(args)
    result = {}
    try:
        result = validate(inv, traces)
    except Exception as error:
        result = {'result': 'fail', 'error': str(error)}
    finally:
        sources = [HERE / 'rtl_gating_tb.sv', Path(__file__).resolve(),
                   HERE / 'Wirelingo/RtlGating.lean', HERE / 'RtlGatingAudit.lean', args.probe, args.reference,
                   *[ROOT / f'src/{n}.sv' for n in ('pe_core', 'pe_fifo', 'pe_fifos', 'pe_cg')]]
        inv.dependencies.update(p.resolve() for p in sources)
        inv.dependencies.add(Path(sys.executable).resolve())
        pins = {str(p): digest(p) for p in sorted(inv.dependencies)}
        (inv.directory / 'dependency-hashes.json').write_text(json.dumps(pins, indent=2) + '\n')
        summary = {'invocation': 1, 'elapsed_seconds': time.monotonic() - inv.start,
            'commands': inv.commands, 'RTL_clocks': inv.rtl_cycles, 'reset_clocks': inv.reset_cycles,
            'comparison_clocks': inv.comparison_cycles, 'one_time_deposits': inv.deposits,
            'incidental_model_cycles': 0, **result}
        (inv.directory / 'validation.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps({k: v for k, v in summary.items() if k != 'commands'}, indent=2))
    if result.get('result') != 'pass':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
