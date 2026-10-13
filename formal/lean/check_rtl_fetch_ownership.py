#!/usr/bin/env python3
"""Finite bus-loaded SRAM/chip ownership evidence, not universal refinement.

Prepare elaborates BOTH real RTL variants before any simulation, compiles and
audits the new manual Lean semantics and computes closed bus-only predictions.
Native uses that explicit immutable preparation in one fresh output directory.
Installed tools are explicit inputs. Imports compile from checked-in source
into fresh output; no Lake/download/install or historical object dependency.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
# Historical corpus label, not a checkout/provenance prerequisite.
BASELINE = '2de7a6a728a32ad708040d564ac3f77fd3dcac5a'
IMPORTS = ('Wait', 'Event', 'Gating', 'SideSet')
SOURCE_FILES = tuple('src/' + n + '.sv' for n in
    ('pe_chip', 'pe_core', 'pe_imem', 'pe_line', 'pe_fifo', 'pe_fifos', 'pe_cg')) + (
    'test/sram_model.v', 'formal/lean/audit_axioms.py',
    'formal/lean/lean-toolchain') + tuple('formal/lean/Wirelingo/' + n + '.lean'
    for n in IMPORTS)
UNKNOWN = 4294967296
FIELDS = ('owner_lo', 'owner_hi', 'enable', 'restart', 'flags', 'pc0', 'pc1',
          'instr0', 'instr1', 'fetch0', 'fetch1', 'cnt0', 'cnt1', 'exec0', 'exec1',
          'complete0', 'complete1', 'out_en0', 'out_val0', 'out_en1', 'out_val1',
          'oe_en0', 'oe_en1', 'inactive_writes', 'line_mask', 'post_pin_out',
          'post_pin_oe', 'post_uo_out', 'post_pc0', 'post_pc1', 'post_instr0',
          'post_instr1', 'post_stalled0', 'post_stalled1', 'post_faults', 'post_halts')
NEW_SOURCES = ('Wirelingo/PinOwnership.lean', 'PinOwnershipAudit.lean',
               'rtl_fetch_ownership_tb.sv', 'check_rtl_fetch_ownership.py',
               'run_fetch_ownership.py')
MUX_NEEDLE = '      if (w_out_en[32*pin_owner[2*p +: 2] + p]) pin_out_n[p] = w_out_val[32*pin_owner[2*p +: 2] + p];'
MUX_MUTANT = '      if (w_out_en[p]) pin_out_n[p] = w_out_val[p];'


def digest(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def put(path, obj):
    path.write_text(json.dumps(obj, indent=2) + '\n')


def word(kind, side):
    return (1 << 27) | (side << 6 << 19) | (kind << 16)


def owner_pair(a, b):
    return (a << 18) | (b << 20)


def drive(addr=65535, data=0, phase=1, we=0, reset_n=1):
    return [reset_n, we, addr, data, phase]


def write(addr, data, phase=1):
    return drive(addr, data, phase, 1)


def setup(owner, divider):
    rows = [drive(reset_n=0, phase=0), drive(reset_n=0, phase=0)]
    for idx, sides in ((0, (1, 2)), (1, (2, 1))):
        for slot, (kind, side) in enumerate(zip((5, 6), sides)):
            rows.append(write(idx * 256 + slot, word(kind, side), 0))
    for idx in (0, 1):
        base = 1024 + idx * 32
        own = idx * 256
        rows.extend(write(base + off, value, 0) for off, value in
                    ((0, divider << 8), (1, 0), (2, 9 | (2 << 8)), (3, 0),
                     (4, own), (5, own | ((own + 1) << 16))))
    rows.extend(write(addr, value, 0) for addr, value in
                ((0x80C, 0), (0x807, 0), (0x806, 0), (0x805, owner),
                 (0x80A, 0x600), (0x809, 0x600), (0x803, 255)))
    rows.extend([drive(phase=0), drive(phase=0), write(0x801, 0x303, 0), drive(phase=0)])
    assert len(rows) == 29
    return rows


def corpus():
    walk = [drive(), write(0x805, owner_pair(0, 0)), drive(),
            write(0x805, owner_pair(0, 1)), drive(), write(0x805, owner_pair(1, 0)),
            drive(), write(0x809, 0x600), write(0x805, owner_pair(2, 3)),
            write(0x809, 0x600), drive(), write(0x805, owner_pair(1, 1)), drive(), drive()]
    release = [drive(), write(0x802, 1), drive(), drive(), write(0x809, 0x600),
               drive(), write(0x803, 1), drive(), drive(), drive()]
    div4 = [drive() for _ in range(21)]
    for j, addr, data in ((1, 0x805, owner_pair(1, 0)), (2, 0x802, 1),
                          (6, 0x805, owner_pair(0, 0)), (9, 0x809, 0x600),
                          (13, 0x803, 1)):
        div4[j] = write(addr, data)
    traces = [{'id': 'owner-walk', 'drives': setup(owner_pair(1, 1), 1) + walk},
              {'id': 'fetch-release-wrap-host', 'drives': setup(owner_pair(0, 0), 1) + release},
              {'id': 'div4-owner-fetch-host', 'drives': setup(owner_pair(0, 1), 4) + div4}]
    assert [len(t['drives']) for t in traces] == [43, 39, 50]
    return traces


def input_text(t):
    return str(len(t['drives'])) + '\n' + '\n'.join(' '.join(map(str, d)) for d in t['drives']) + '\n'


def predeclaration():
    traces = corpus()
    return {'baseline': BASELINE, 'traces': traces,
            'programs': {str(i): [{'address': i * 256 + j,
              'word_hex': f'{word(k, s):08x}', 'bytes_le': word(k, s).to_bytes(4, 'little').hex(),
              'WAIT_kind': k, 'side_value': s} for j, (k, s) in enumerate(zip((5, 6), sides))]
              for i, sides in ((0, (1, 2)), (1, (2, 1)))},
            'positive_clocks_including_reset_setup': 132, 'actual_mux_mutant_stream': 'owner-walk',
            'mutant_clocks_including_reset_setup': 43, 'aggregate_RTL_clocks': 175,
            'streams_total': 4, 'expected_first_mutant_clock': 29,
            'clock_sampling': 'pre-edge after4ns settle; post-edge after1ns NBA',
            'phase0': 'reset/loading/CTRL settling; uninitialized inactive value buses are not asserted',
            'phase1': 'all36 compared fields known; normal fetched execution',
            'domain': 'line-off, OD0, POE600, reset OSR0/autopull0; bus-only stable own-bank WAITs'}


def source_inputs():
    return {f: digest(ROOT / f) for f in SOURCE_FILES} | {
        'formal/lean/' + f: digest(HERE / f) for f in NEW_SOURCES}


def validate_inputs(a):
    a.output = a.output.absolute()
    if a.output.exists() or a.output.is_symlink() or not a.output.parent.is_dir():
        raise ValueError('fresh nonexistent output required with existing parent')
    a.output = a.output.parent.resolve() / a.output.name
    for name in ('iverilog', 'vvp', 'lean', 'strace'):
        try:
            p = getattr(a, name).resolve(strict=True)
        except OSError as e:
            raise ValueError(f'{name} unavailable: supply an installed executable via --{name}') from e
        if not p.is_file() or not os.access(p, os.X_OK):
            raise ValueError(f'{name} unavailable: supply an installed executable via --{name}')
        setattr(a, name, p)
    a.lean_lib = a.lean_lib.resolve(strict=True)
    if not (a.lean_lib / 'Init.olean').is_file():
        raise ValueError('Lean standard library missing: supply Lean4.28 lib/lean via --lean-lib')
    if any(a.output.is_relative_to(p) for p in (ROOT, a.lean_lib)):
        raise ValueError('output overlaps read-only source/library root')
    try:
        a.sources = source_inputs()
    except OSError as e:
        raise ValueError(f'checkout source missing: {e.filename}') from e
    if a.phase == 'native':
        if not a.prepared or not a.prepared_sha256:
            raise ValueError('native requires explicit prepared directory and receipt SHA256')
        a.prepared = a.prepared.resolve(strict=True)
        receipt_file = a.prepared / 'prepared-receipt.json'
        if digest(receipt_file) != a.prepared_sha256:
            raise ValueError('wrong prepared receipt identity')
        prep = json.loads(receipt_file.read_text())
        if prep['source_inputs'] != a.sources:
            raise ValueError('prepared source drift: rerun preparation in a fresh output')
        if prep['corpus_sha256'] != hashlib.sha256(json.dumps(
                predeclaration(), sort_keys=True).encode()).hexdigest():
            raise ValueError('prepared corpus drift')
        for f, h in prep['artifacts'].items():
            if digest(a.prepared / f) != h:
                raise ValueError(f'prepared artifact drift {f}')
        for name, h in prep['tools'].items():
            if digest(getattr(a, name)) != h:
                raise ValueError(f'prepared tool drift {name}')
        if str(a.lean_lib) != prep['lean_lib']:
            raise ValueError('prepared Lean library path drift')
        for f, h in prep['library_inputs'].items():
            if digest(Path(f)) != h:
                raise ValueError(f'prepared library drift {f}')
        if a.output.is_relative_to(a.prepared):
            raise ValueError('output overlaps frozen preparation')
    return a


class Invocation:
    def __init__(self, a):
        self.a = a
        self.directory = a.output
        self.tmp = a.output / 'tmp'; self.tmp.mkdir()
        self.start = time.monotonic()
        self.commands = []; self.dependencies = set()
        self.clocks = self.resets = 0

    def run(self, argv, name, env=None, stdin=None):
        remain = 90 - (time.monotonic() - self.start)
        if remain <= 0:
            raise RuntimeError('90-second check cap')
        log = self.directory / f'{name}.system.log'
        wrapped = [str(self.a.strace), '-f', '-e', 'trace=execve,openat', '-o', str(log), *map(str, argv)]
        environment = {**os.environ, **(env or {}), 'TMPDIR': str(self.tmp)}
        record = {'argv': list(map(str, argv)), 'wrapped_argv': wrapped, 'cwd': str(HERE),
                  'TMPDIR': str(self.tmp), 'LEAN_PATH': environment.get('LEAN_PATH'),
                  'stdin': str(stdin) if stdin else None, 'exit': None}
        self.commands.append(record); began = time.monotonic()
        with open(stdin if stdin else os.devnull) as fd:
            try:
                r = subprocess.run(wrapped, cwd=HERE, env=environment, stdin=fd,
                    text=True, capture_output=True, timeout=remain)
                out, err, code = r.stdout, r.stderr, r.returncode
            except subprocess.TimeoutExpired as e:
                out = e.stdout or b''; err = e.stderr or b''; code = 'timeout'
                if isinstance(out, bytes): out = out.decode()
                if isinstance(err, bytes): err = err.decode()
        (self.directory / f'{name}.stdout.log').write_text(out)
        (self.directory / f'{name}.stderr.log').write_text(err)
        record.update(exit=code, elapsed_seconds=time.monotonic() - began)
        for line in log.read_text().splitlines():
            if ' = -1 ' in line or 'O_WRONLY' in line or 'O_RDWR' in line: continue
            m = re.search(r'(?:execve\(|openat\([^,]+, )"([^"]+)"', line)
            if m:
                p = Path(m[1]); p = p if p.is_absolute() else HERE / p
                if p.is_file(): self.dependencies.add(p.resolve())
        self.clocks += len(re.findall(r'^ROW ', out, re.M))
        self.resets += len(re.findall(r'^DRIVE \d+ reset_n=0 ', out, re.M))
        if self.clocks > 1000: raise RuntimeError('aggregate clock cap exceeded')
        if code != 0: raise RuntimeError(f'{name} exit{code}; frozen diagnostic, no retry')
        return out


def prepare(inv):
    a = inv.a
    chip = ROOT / 'src/pe_chip.sv'; text = chip.read_text()
    assert text.count(MUX_NEEDLE) == 1
    mutant = a.output / 'NONPRODUCT-pe_chip-owner0-mutant.sv'
    mutant.write_text(text.replace(MUX_NEEDLE, MUX_MUTANT))
    put(a.output / 'mutant-delta.json', {'non_product': True, 'before': MUX_NEEDLE,
        'after': MUX_MUTANT, 'occurrences': 1, 'source_sha256': digest(chip), 'mutant_sha256': digest(mutant)})
    deps = [ROOT / f'src/{n}.sv' for n in ('pe_core', 'pe_imem', 'pe_line', 'pe_fifo', 'pe_fifos', 'pe_cg')]
    for label, source in (('original', chip), ('owner0-mutant', mutant)):
        inv.run([a.iverilog, '-g2012', '-s', 'rtl_fetch_ownership_tb', '-o', a.output / (label + '.vvp'),
                 HERE / 'rtl_fetch_ownership_tb.sv', source, *deps, ROOT / 'test/sram_model.v'], 'elaborate-' + label)
    build = a.output / 'lean-build'; (build / 'Wirelingo').mkdir(parents=True)
    env = {'LEAN_PATH': str(build) + ':' + str(a.lean_lib)}
    version = inv.run([a.lean, '--version'], 'lean-version')
    if not re.search(r'Lean \(version 4\.28\.0(?:,|\))', version):
        raise RuntimeError('Lean4.28.0 required; supply its installed bin/lean')
    inv.run([a.iverilog, '-V'], 'iverilog-version')
    inv.run([a.vvp, '-V'], 'vvp-version')
    for name in IMPORTS:
        inv.run([a.lean, '-DwarningAsError=true', '-o', build / ('Wirelingo/' + name + '.olean'),
                 HERE / ('Wirelingo/' + name + '.lean')], 'import-' + name, env)
    inv.run([a.lean, '-DwarningAsError=true', '-o', build / 'Wirelingo/PinOwnership.olean',
             HERE / 'Wirelingo/PinOwnership.lean'], 'owner-build', env)
    audit = inv.run([a.lean, '-DwarningAsError=true', HERE / 'PinOwnershipAudit.lean'], 'owner-audit', env)
    spec = importlib.util.spec_from_file_location('axiom_policy', HERE / 'audit_axioms.py')
    policy = importlib.util.module_from_spec(spec); spec.loader.exec_module(policy)
    declared = {'Wirelingo.' + n for n in re.findall(r'^theorem (\w+)', (HERE / NEW_SOURCES[0]).read_text(), re.M)}
    audited = set(re.findall(r"'([^']+)' (?:depends on axioms|does not depend on any axioms)", audit))
    assert declared == audited and len(declared) == 6
    for deps in re.findall(r'depends on axioms: \[([^\]]*)\]', audit):
        assert (set(deps.split(', ')) if deps else set()) <= policy.ALLOWED
    traces = corpus(); commands = []
    for t in traces:
        commands.append('0')
        commands.extend('1 ' + ' '.join(map(str, d[:4])) for d in t['drives'])
    inp = a.output / 'lean-input.txt'; inp.write_text('\n'.join(commands) + '\n')
    raw = inv.run([a.lean, '-DwarningAsError=true', '--run', HERE / 'PinOwnershipAudit.lean'],
                  'closed-owner-projection', env, inp)
    flat = [list(map(int, line.split()[1:])) for line in raw.splitlines() if line.startswith('PRED ')]
    assert len(flat) == 132 and all(len(row) == len(FIELDS) for row in flat)
    predictions = {}; offset = 0
    for t in traces:
        predictions[t['id']] = flat[offset:offset + len(t['drives'])]; offset += len(t['drives'])
    put(a.output / 'predictions.json', predictions)
    # Pre-execution expectations computed from reset + exact bus inputs only.
    assert [r[25] for r in predictions['owner-walk'][29:]] == [1024,1024,512,512,1536,1536,0,1536,0,1536,1536,1536,1024,1024]
    assert [r[25] for r in predictions['fetch-release-wrap-host'][29:]] == [512,512,512,1024,1536,1024,1024,1024,512,512]
    artifact_files = ['original.vvp', 'owner0-mutant.vvp', 'predictions.json', 'predeclared-stimuli.json',
                      'lean-build/Wirelingo/PinOwnership.olean', 'owner-audit.stdout.log', 'mutant-delta.json'] + [
                      'lean-build/Wirelingo/' + n + '.olean' for n in IMPORTS]
    receipt = {'baseline': BASELINE, 'source_inputs': a.sources,
        'corpus_sha256': hashlib.sha256(json.dumps(predeclaration(), sort_keys=True).encode()).hexdigest(),
        'lean_lib': str(a.lean_lib),
        'library_inputs': {str(f): digest(f) for f in sorted(inv.dependencies) if f.is_relative_to(a.lean_lib)},
        'new_sources': {f: digest(HERE / f) for f in NEW_SOURCES},
        'tools': {n: digest(getattr(a, n)) for n in ('iverilog', 'vvp', 'lean', 'strace')},
        'artifacts': {f: digest(a.output / f) for f in artifact_files}, 'axiom_lemmas': 6,
        'positive_RTL_clocks_predeclared': 132, 'aggregate_RTL_clocks_predeclared': 175}
    put(a.output / 'prepared-receipt.json', receipt)
    return {'result': 'pass', 'phase': 'prepare', 'manual_lemmas_audited': 6,
            'both_RTL_variants_elaborated': True, 'prepared_receipt_sha256': digest(a.output / 'prepared-receipt.json')}


def parse_rows(raw):
    rows = []
    for line in raw.splitlines():
        if not line.startswith('ROW '): continue
        ts = line.split()[1:]; row = [int(ts[0])]
        for i, token in enumerate(ts[1:]):
            row.append(UNKNOWN if re.search('[xz]', token, re.I) else int(token, 16 if i in (7,8,30,31) else 10))
        assert len(row) == 37
        rows.append(row)
    return rows


def first_mismatch(t, expected, rows):
    assert len(expected) == len(rows)
    for clock, (prediction, observed) in enumerate(zip(expected, rows, strict=True)):
        assert observed[0] == clock
        # Only uninitialized inactive VALUE buses during cold phase0 are excluded.
        # All register/port values, enable masks and known fetch words are compared.
        skip = {18 if prediction[7] == UNKNOWN else -1, 20 if prediction[8] == UNKNOWN else -1}
        if t['drives'][clock][4] == 1:
            assert skip == {-1}, 'assertion phase requires known instructions'
        differences = [FIELDS[i] for i, (a,b) in enumerate(zip(prediction, observed[1:], strict=True)) if i not in skip and a != b]
        if differences:
            return {'trace': t['id'], 'clock': clock, 'fields': FIELDS, 'differences': differences,
                    'drive_prefix': t['drives'][:clock+1], 'Lean': prediction, 'RTL': observed[1:]}
    return None


def native(inv):
    a = inv.a; traces = corpus()
    assert json.loads((a.prepared / 'predeclared-stimuli.json').read_text()) == predeclaration()
    predictions = json.loads((a.prepared / 'predictions.json').read_text()); rows_all = {}
    def simulate(binary, t, name):
        inp = a.output / (name + '.input.txt'); inp.write_text(input_text(t))
        raw = inv.run([a.vvp, a.prepared / binary, f'+INPUT={inp}'], name)
        rows = parse_rows(raw); assert len(rows) == len(t['drives'])
        assert all('uio_out=00 uio_oe=00' in l for l in raw.splitlines() if l.startswith('DRIVE '))
        return rows
    for t in traces:
        rows = simulate('original.vvp', t, t['id']); rows_all[t['id']] = rows
        mismatch = first_mismatch(t, predictions[t['id']], rows)
        if mismatch:
            put(a.output / 'first-mismatch.json', mismatch)
            raise RuntimeError('normal RTL/Lean disagreement; freeze without repairing stimulus')
    put(a.output / 'positive-rows.json', rows_all)
    t = traces[0]; rows = simulate('owner0-mutant.vvp', t, 'owner0-mutant')
    mismatch = first_mismatch(t, predictions[t['id']], rows)
    assert mismatch and mismatch['clock'] == 29
    assert mismatch['Lean'][25:28] == [1024,1536,4] and mismatch['RTL'][25:28] == [512,1536,2]
    put(a.output / 'actual-mux-mutant-counterexample.json', mismatch)
    # Offline saved pin sequence one edge late; every success marker unchanged.
    shifted = [r.copy() for r in rows_all[t['id']]]
    for j in range(30, len(shifted)):
        shifted[j][26] = rows_all[t['id']][j-1][26]
        shifted[j][28] = rows_all[t['id']][j-1][28]
    offline = first_mismatch(t, predictions[t['id']], shifted)
    assert offline and offline['clock'] == 31
    put(a.output / 'offline-one-clock-owner-shift-counterexample.json', offline)
    put(a.output / 'offline-shifted-rows.json', shifted)
    assert inv.clocks == 175 and inv.resets == 8
    return {'result': 'pass', 'phase': 'native', 'positive_streams': 3, 'positive_rows': 132,
            'actual_mutant_first_mismatch': 29, 'offline_shift_first_mismatch': 31,
            'scope': 'finite normal SRAM fetch/registered ownership/core-bus/port agreement; no physical/refinement claim'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--phase', choices=('prepare','native'), required=True)
    for n in ('output','iverilog','vvp','lean','lean-lib','strace'):
        p.add_argument('--'+n, type=Path, required=True)
    p.add_argument('--prepared', type=Path)
    p.add_argument('--prepared-sha256')
    try: a = validate_inputs(p.parse_args())
    except (OSError, ValueError, KeyError, TypeError) as e: p.error(str(e))
    a.output.mkdir(exist_ok=False)
    put(a.output / 'predeclared-stimuli.json', predeclaration())
    put(a.output / 'input-receipt.json', {'corpus_label':BASELINE,
        'source_inputs':a.sources,
        'selected_paths':{n:str(getattr(a,n)) for n in ('output','iverilog','vvp','lean','lean_lib','strace')},
        'prepared':str(a.prepared) if a.prepared else None, 'prepared_sha256':a.prepared_sha256})
    inv = Invocation(a)
    try: result = prepare(inv) if a.phase == 'prepare' else native(inv)
    except Exception as e: result = {'result':'fail','phase':a.phase,'error':str(e)}
    finally:
        inv.dependencies.update([Path(sys.executable).resolve(),a.strace,
            *[ROOT/f for f in a.sources]])
        put(a.output / 'dependency-hashes.json',{str(f):digest(f) for f in sorted(inv.dependencies)})
        summary={'elapsed_seconds':time.monotonic()-inv.start,'commands':inv.commands,
            'RTL_clocks_including_reset_setup':inv.clocks,'reset_edges':inv.resets,
            'python_model_steps':0,'state_deposits':0,**result}
        put(a.output / 'ledger.json',summary)
    print(json.dumps({k:v for k,v in summary.items() if k!='commands'},indent=2))
    if result.get('result')!='pass': raise SystemExit(1)


if __name__ == '__main__':
    sys.dont_write_bytecode = True
    main()
