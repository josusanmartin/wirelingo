#!/usr/bin/env python3
"""Bounded actual-core side-set / Lean comparison, zero Python model steps.

Explicit existing tools/import objects and a fresh destination are mandatory.
No Lake build, install, private path default, or mutable accepted artifact.
"""
import argparse
import hashlib
import importlib.util
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
BASELINE = 'c77dd128b2e3b4835731576da08380be6f063627'
REFERENCE_IDENTITY_SHA256 = '0f322b27855066889758752b4d1a59cb3920e033f9741fb626f4f1c1f57b6e80'
FIELDS = ('out_en', 'out_val', 'oe_en', 'oe_val', 'exec', 'qualified_complete',
          'pc', 'stalled', 'cnt', 'facc', 'dcnt', 'halted', 'fault')
PROFILES = (('bridge', 2, 9, 0, 0, 128),
            ('direction', 2, 9, 0, 1, 128),
            ('optional-clear', 2, 9, 1, 0, 64),
            ('optional-set', 2, 9, 1, 0, 192),
            ('zero', 0, 9, 0, 0, 5),
            ('wrap-four', 4, 31, 0, 0, 176),
            ('clamp-seven', 7, 31, 0, 0, 240))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')


def make_trace(name, profile, gate='execute', f0=0, idx=1, drives=None):
    _, sc, base, opt, direction, sd = profile
    # count/base/optional/direction/sd, div16.8, raw cnt/facc/dcnt/halt/fault/stalled/pc
    initial = [sc, base, opt, direction, sd, 4, 128,
               int(gate == 'non-tick'), 128, int(gate == 'delay'),
               int(gate == 'halt'), int(gate == 'fault'), 0,
               idx * 256 if gate != 'wrong-bank' else (idx ^ 1) * 256]
    if drives is None:
        drives = [[int(gate != 'disabled'), int(gate == 'restart'), f0]]
    word = (1 << 27) | (sd << 19) | (5 << 16)
    return {'id': name, 'idx': idx, 'initial': initial, 'drives': drives,
            'assumption_violation': gate == 'wrong-bank',
            'instruction_word_hex': f'{word:08x}',
            'instruction_bytes_le_hex': word.to_bytes(4, 'little').hex()}


def corpus():
    traces = []
    for p in PROFILES:
        for gate in ('execute', 'disabled', 'restart', 'non-tick', 'delay', 'halt', 'fault'):
            traces.append(make_trace(f'idx1-{p[0]}-{gate}-stalled', p, gate))
        traces.append(make_trace(f'idx1-{p[0]}-execute-completed', p, f0=1))
    traces.append(make_trace('idx0-bridge-control', PROFILES[0], idx=0))
    traces.append(make_trace('idx1-wrong-bank-assumption', PROFILES[0], 'wrong-bank'))
    a = make_trace('idx1-div4-repeat-stalled', PROFILES[0], drives=[[1, 0, 0]] * 20)
    a['initial'][6:9] = [0, 0, 0]  # integer div4, initial cnt/facc=0
    traces.append(a)
    drives = [[int(j not in (5, 6, 7)), int(j == 6),
               int(j in (1, 2, 3, 4, 9, 10, 11, 12, 17, 18))] for j in range(20)]
    b = make_trace('idx1-enable-disabled-restart-flag-gaps', PROFILES[0], drives=drives)
    b['initial'][6:9] = [0, 0, 0]
    traces.append(b)
    assert len(traces) == 60 and sum(len(t['drives']) for t in traces) == 98
    return traces


def input_text(t):
    return (str(len(t['drives'])) + '\n' + ' '.join(map(str, t['initial'])) + '\n' +
            '\n'.join(' '.join(map(str, d)) for d in t['drives']) + '\n')


def first_mismatch(t, expected, rows):
    for j, (a, row) in enumerate(zip(expected, rows, strict=True)):
        if a != row[1:]:
            return {'trace': t['id'], 'clock': j, 'fields': FIELDS,
                    'initial': t['initial'], 'drive_prefix': t['drives'][:j + 1],
                    'instruction_word_hex': t['instruction_word_hex'],
                    'Lean': a, 'RTL': row[1:]}
    return None


def validate_inputs(a):
    a.output = a.output.absolute()
    if a.output.exists() or a.output.is_symlink():
        raise ValueError('output must be fresh/nonexistent, even if existing directory is empty')
    if not a.output.parent.is_dir():
        raise ValueError('output parent must already exist')
    a.output = a.output.parent.resolve() / a.output.name
    for name in ('reference', 'iverilog', 'vvp', 'lean', 'strace'):
        p = getattr(a, name).resolve(strict=True)
        if not p.is_file() or (name != 'reference' and not os.access(p, os.X_OK)):
            raise ValueError(f'invalid existing file/executable: {name}')
        setattr(a, name, p)
    for name in ('lean_lib', 'lean_objects'):
        p = getattr(a, name).resolve(strict=True)
        if not p.is_dir():
            raise ValueError(f'invalid existing directory: {name}')
        setattr(a, name, p)
    if not (a.lean_lib / 'Init.olean').is_file():
        raise ValueError('Lean standard library Init.olean missing')
    if any(a.output.is_relative_to(p) for p in (ROOT, a.lean_lib, a.lean_objects)):
        raise ValueError('output overlaps read-only repository/compiler input root')
    receipt = json.loads(a.reference.read_text())
    identity = hashlib.sha256(json.dumps(receipt, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    if identity != REFERENCE_IDENTITY_SHA256 or receipt['baseline'] != BASELINE:
        raise ValueError('wrong accepted reference identity/baseline')
    for f, h in receipt['sources'].items():
        if digest(ROOT / f) != h:
            raise ValueError(f'wrong accepted source identity: {f}')
    for f, h in receipt['objects'].items():
        if digest(a.lean_objects / f) != h:
            raise ValueError(f'wrong accepted object identity: {f}')
    for f in ('Wirelingo/SideSet.lean', 'SideSetAudit.lean', 'rtl_sideset_tb.sv'):
        if not (HERE / f).is_file():
            raise ValueError(f'missing new source: {f}')
    a.receipt = receipt
    return a


class Invocation:
    def __init__(self, a):
        self.a = a
        self.directory = a.output
        self.tmp = self.directory / 'tmp'
        self.tmp.mkdir()
        self.start = time.monotonic()
        self.commands = []
        self.dependencies = set()
        self.rtl_clocks = self.resets = self.rows = self.deposits = 0

    def run(self, argv, name, env=None, stdin=None):
        remaining = 90 - (time.monotonic() - self.start)
        if remaining <= 0:
            raise RuntimeError('90 second cap exhausted')
        trace = self.directory / f'{name}.system.log'
        wrapped = [str(self.a.strace), '-f', '-e', 'trace=execve,openat', '-o', str(trace), *map(str, argv)]
        environment = {**os.environ, **(env or {}), 'TMPDIR': str(self.tmp)}
        self.commands.append({'argv': list(map(str, argv)), 'instrumented_argv': wrapped,
            'cwd': str(HERE), 'TMPDIR': str(self.tmp), 'LEAN_PATH': environment.get('LEAN_PATH'),
            'stdin_file': str(stdin) if stdin else None, 'exit': None})
        began = time.monotonic()
        with open(stdin) if stdin else open(os.devnull) as fd:
            try:
                r = subprocess.run(wrapped, cwd=HERE, env=environment, stdin=fd,
                    text=True, capture_output=True, timeout=remaining)
                out, err, code = r.stdout, r.stderr, r.returncode
            except subprocess.TimeoutExpired as e:
                out = (e.stdout or b'').decode() if isinstance(e.stdout, bytes) else (e.stdout or '')
                err = (e.stderr or b'').decode() if isinstance(e.stderr, bytes) else (e.stderr or '')
                code = 'timeout'
        (self.directory / f'{name}.stdout.log').write_text(out)
        (self.directory / f'{name}.stderr.log').write_text(err)
        self.commands[-1].update(exit=code, elapsed_seconds=time.monotonic() - began)
        for line in trace.read_text().splitlines():
            if ' = -1 ' in line or 'O_WRONLY' in line or 'O_RDWR' in line:
                continue
            m = re.search(r'(?:execve\(|openat\([^,]+, )"([^"]+)"', line)
            if m:
                p = Path(m[1]); p = p if p.is_absolute() else HERE / p
                if p.is_file():
                    self.dependencies.add(p.resolve())
        resets = sum(map(int, re.findall(r'^RESET (\d+)$', out, re.M)))
        rows = len(re.findall(r'^ROW ', out, re.M))
        self.resets += resets; self.rows += rows; self.rtl_clocks += resets + rows
        self.deposits += len(re.findall(r'^DEPOSIT ', out, re.M))
        if self.rtl_clocks > 5000:
            raise RuntimeError('RTL clock cap exceeded')
        if code != 0:
            raise RuntimeError(f'{name}: exit {code}; diagnostic frozen')
        return out

    def simulate(self, binary, t, name):
        inp = self.directory / f'{name}.input.txt'
        inp.write_text(input_text(t))
        out = self.run([self.a.vvp, binary, f'+INPUT={inp}'], name)
        rows = [list(map(int, l.split()[1:])) for l in out.splitlines() if l.startswith('ROW ')]
        assert len(rows) == len(t['drives']) and all(len(r) == 14 for r in rows)
        assert [r[0] for r in rows] == list(range(len(rows)))
        assert re.search(r'DEPOSIT 1 INSTR ' + t['instruction_word_hex'], out)
        return rows, out


def validate(inv, traces):
    a = inv.a
    build = inv.directory / 'lean-build'
    (build / 'Wirelingo').mkdir(parents=True)
    for f, h in a.receipt['objects'].items():
        shutil.copyfile(a.lean_objects / f, build / f)
        assert digest(build / f) == h
    lean_path = str(build) + ':' + str(a.lean_lib)
    inv.run([a.lean, '-DwarningAsError=true', '-o', build / 'Wirelingo/SideSet.olean',
             HERE / 'Wirelingo/SideSet.lean'], 'side-build', {'LEAN_PATH': lean_path})
    audit = inv.run([a.lean, '-DwarningAsError=true', HERE / 'SideSetAudit.lean'],
                    'side-audit', {'LEAN_PATH': lean_path})
    # Reuse the existing read-only audit tool's exact inventory and axiom policy.
    spec = importlib.util.spec_from_file_location('axiom_policy', HERE / 'audit_axioms.py')
    policy = importlib.util.module_from_spec(spec); spec.loader.exec_module(policy)
    declared = {'Wirelingo.' + n for n in re.findall(r'^theorem (\w+)',
                (HERE / 'Wirelingo/SideSet.lean').read_text(), re.M)}
    audited = set(re.findall(r"'([^']+)' (?:depends on axioms|does not depend on any axioms)", audit))
    assert len(declared) == 7 and declared == audited
    for deps in re.findall(r'depends on axioms: \[([^\]]*)\]', audit):
        assert (set(deps.split(', ')) if deps else set()) <= policy.ALLOWED
    commands = []
    for t in traces:
        commands.append('0 ' + str(t['idx']) + ' ' + ' '.join(map(str, t['initial'])))
        commands.extend('1 ' + ' '.join(map(str, d)) for d in t['drives'])
    inp = inv.directory / 'lean-input.txt'
    inp.write_text('\n'.join(commands) + '\n')
    out = inv.run([a.lean, '-DwarningAsError=true', '--run', HERE / 'SideSetAudit.lean'],
                  'lean-projection', {'LEAN_PATH': lean_path}, inp)
    flat = [list(map(int, l.split()[1:])) for l in out.splitlines() if l.startswith('PRED ')]
    assert len(flat) == 98 and all(len(r) == len(FIELDS) for r in flat)
    expected = {}; offset = 0
    for t in traces:
        expected[t['id']] = flat[offset:offset + len(t['drives'])]
        offset += len(t['drives'])
    write_json(inv.directory / 'lean-predictions.json', expected)
    sources = [ROOT / f'src/{n}.sv' for n in ('pe_core', 'pe_fifo', 'pe_fifos', 'pe_cg')]
    def compile_core(idx, core, name):
        binary = inv.directory / (name + '.vvp')
        inv.run([a.iverilog, '-g2012', '-s', 'rtl_sideset_tb',
                 '-P', f'rtl_sideset_tb.IDX={idx}', '-o', binary,
                 HERE / 'rtl_sideset_tb.sv', core, *sources[1:]], name)
        return binary
    binaries = {idx: compile_core(idx, sources[0], f'rtl-build-idx{idx}') for idx in (1, 0)}
    rows_by_trace = {}
    for t in traces:
        rows, raw = inv.simulate(binaries[t['idx']], t, t['id'])
        rows_by_trace[t['id']] = rows
        mismatch = first_mismatch(t, expected[t['id']], rows)
        if mismatch:
            write_json(inv.directory / 'first-mismatch.json', mismatch)
            raise RuntimeError('RTL/Lean semantic mismatch: frozen, no product change')
        if t['assumption_violation']:
            assert 'ASSUMPTION wrong-bank exec=0 fault=1 halted=1' in raw
    write_json(inv.directory / 'rtl-rows.json', rows_by_trace)
    repeat = rows_by_trace['idx1-div4-repeat-stalled']
    assert [r[0] for r in repeat if r[5]] == [0, 4, 8, 12, 16]
    assert all(r[6] == 0 for r in repeat)
    assert all(r[1:5] == [1536, 1024, 0, 0] for r in repeat if r[5])
    # Actual SV mutation, isolated and explicitly labeled non-product evidence.
    needle = 'wire        side_act = exec && sc != 3\'d0 && side_en;'
    replacement = 'wire        side_act;'
    declaration = '  reg         complete, jump, new_wait;'
    assignment = '  assign side_act = exec && complete && sc != 3\'d0 && side_en;'
    text = sources[0].read_text()
    assert text.count(needle) == 1
    assert text.count(declaration) == 1
    mutant = inv.directory / 'NONPRODUCT-pe_core-completion-mutant.sv'
    # Declare the net early, but bind complete only after its unchanged reg
    # declaration. These two edits apply solely to this disposable RTL copy.
    mutant.write_text(text.replace(needle, replacement).replace(
        declaration, declaration + '\n' + assignment))
    write_json(inv.directory / 'rtl-mutant-delta.json', {'non_product': True,
        'original_sha256': digest(sources[0]), 'mutant_sha256': digest(mutant),
        'deltas': [{'before': needle, 'after': replacement, 'exact_occurrences': 1},
                   {'before': declaration, 'after': declaration + '\n' + assignment,
                    'exact_occurrences': 1}]})
    binary = compile_core(1, mutant, 'rtl-build-completion-mutant')
    bridge = traces[0]
    rows, _ = inv.simulate(binary, bridge, 'rtl-completion-mutant-bridge')
    counterexample = first_mismatch(bridge, expected[bridge['id']], rows)
    assert counterexample and counterexample['clock'] == 0
    assert counterexample['Lean'][:4] == [1536, 1024, 0, 0] and counterexample['RTL'][:4] == [0, 0, 0, 0]
    write_json(inv.directory / 'rtl-mutant-counterexample.json', counterexample)
    # Offline mutation of actual captured output rows, no new DUT clocks.
    swap = [r.copy() for r in rows_by_trace[bridge['id']]]
    swap[0][1:5] = swap[0][3:5] + swap[0][1:3]
    sw = first_mismatch(bridge, expected[bridge['id']], swap)
    assert sw and sw['clock'] == 0
    write_json(inv.directory / 'offline-channel-swap-counterexample.json', sw)
    wrap = next(t for t in traces if t['id'] == 'idx1-wrap-four-execute-stalled')
    lost = [r.copy() for r in rows_by_trace[wrap['id']]]
    lost[0][1] &= ~1; lost[0][2] &= ~1
    lw = first_mismatch(wrap, expected[wrap['id']], lost)
    assert lw and lw['clock'] == 0
    write_json(inv.directory / 'offline-lost-wrap-counterexample.json', lw)
    assert inv.rtl_clocks == 221 and inv.deposits == 61
    for f, h in a.receipt['sources'].items():
        assert digest(ROOT / f) == h, 'accepted source changed during check'
    return {'result': 'pass', 'audited_manual_lemmas': len(declared),
            'own_bank_one_step_cases': 57, 'assumption_violation_cases': 1,
            'sequences': 2, 'predicted_and_compared_clocks': 98,
            'RTL_mutant_rejected_at': 0, 'offline_mutants_rejected': 2,
            'scope': 'finite pe_core buses plus manual semantics, not RTL refinement or chip pads'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('output', 'reference', 'iverilog', 'vvp', 'lean', 'lean-lib', 'lean-objects', 'strace'):
        p.add_argument('--' + name, type=Path, required=True)
    p.add_argument('--declare-only', action='store_true', help='same path/identity safety, no compiler or clocks')
    try:
        a = validate_inputs(p.parse_args())
    except (OSError, ValueError, KeyError, TypeError) as e:
        p.error(str(e))
    a.output.mkdir(exist_ok=False)
    selected = {n: str(getattr(a, n)) for n in
                ('output', 'reference', 'iverilog', 'vvp', 'lean', 'lean_lib', 'lean_objects', 'strace')}
    write_json(a.output / 'input-receipt.json', {'baseline': BASELINE,
        'accepted_identity_sha256': REFERENCE_IDENTITY_SHA256, 'selected_paths': selected,
        'file_sha256': {n: digest(getattr(a, n)) for n in ('reference', 'iverilog', 'vvp', 'lean', 'strace')},
        'accepted_inputs': a.receipt})
    traces = corpus()
    write_json(a.output / 'predeclared-corpus.json', {'traces': traces,
        'reset_clocks_per_case': 2, 'deposits_per_case': 1,
        'total_RTL_clocks_with_mutant': 221, 'model_steps': 0})
    if a.declare_only:
        write_json(a.output / 'ledger.json', {'result': 'declared', 'compiler_checks': 0, 'RTL_clocks': 0})
        print('Declared 58 one-step cases and 2 short sequences; zero compiler/model/RTL clocks')
        return
    inv = Invocation(a)
    try:
        result = validate(inv, traces)
    except Exception as e:
        result = {'result': 'fail', 'error': str(e)}
    finally:
        inv.dependencies.update([Path(sys.executable).resolve(), a.strace, a.reference,
            Path(__file__).resolve(), HERE / 'Wirelingo/SideSet.lean', HERE / 'SideSetAudit.lean',
            HERE / 'rtl_sideset_tb.sv', *[ROOT / f for f in a.receipt['sources']],
            *[a.lean_objects / f for f in a.receipt['objects']]])
        write_json(a.output / 'dependency-hashes.json', {str(f): digest(f) for f in sorted(inv.dependencies)})
        summary = {'elapsed_seconds': time.monotonic() - inv.start, 'commands': inv.commands,
            'RTL_clocks': inv.rtl_clocks, 'reset_clocks': inv.resets, 'captured_clocks': inv.rows,
            'one_time_deposits': inv.deposits, 'Python_model_steps': 0, **result}
        write_json(a.output / 'ledger.json', summary)
    print(json.dumps({k: v for k, v in summary.items() if k != 'commands'}, indent=2))
    if result.get('result') != 'pass':
        raise SystemExit(1)


if __name__ == '__main__':
    # Importing the existing audit policy must not write its __pycache__.
    sys.dont_write_bytecode = True
    main()
