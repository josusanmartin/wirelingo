#!/usr/bin/env python3
"""Build and run the finite fetch/ownership check without Lake or downloads."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

sys.dont_write_bytecode = True
import check_rtl_fetch_ownership as check


def installed(name, supplied):
    path = supplied or shutil.which(name)
    if not path:
        raise ValueError(f'{name} unavailable: install separately or supply --{name}')
    try:
        path = Path(path).resolve(strict=True)
    except OSError as e:
        raise ValueError(f'{name} unavailable: supply an installed executable via --{name}') from e
    if name == 'lean' and path.name == 'elan':
        raise ValueError('supply installed Lean4.28.0 bin/lean via --lean; '
                         'elan shims are excluded to avoid automatic downloads')
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    for name in ('lean', 'iverilog', 'vvp', 'strace'):
        parser.add_argument('--' + name, type=Path)
    parser.add_argument('--lean-lib', type=Path,
                        help='defaults to lib/lean next to the installed Lean bin directory')
    args = parser.parse_args()
    try:
        for name in ('lean', 'iverilog', 'vvp', 'strace'):
            setattr(args, name, installed(name, getattr(args, name)))
        args.lean_lib = args.lean_lib or args.lean.parent.parent / 'lib/lean'
        args.phase = 'prepare'
        args.prepared = args.prepared_sha256 = None
        check.validate_inputs(args)
    except (OSError, ValueError, KeyError, TypeError) as e:
        parser.error(str(e))

    # All paths, installed executables and checkout sources validated before writes.
    args.output.mkdir(exist_ok=False)
    start = time.monotonic()
    commands = []
    result = {'result': 'fail', 'RTL_clocks_including_reset_setup': 0,
              'python_model_steps': 0, 'state_deposits': 0}
    checker = check.HERE / 'check_rtl_fetch_ownership.py'
    try:
        for phase in ('prepare', 'native'):
            destination = args.output / phase
            command = [sys.executable, '-B', str(checker), '--phase', phase,
                       '--output', str(destination)]
            for name in ('lean', 'iverilog', 'vvp', 'strace', 'lean_lib'):
                command += ['--' + name.replace('_', '-'), str(getattr(args, name))]
            if phase == 'native':
                prepared = args.output / 'prepare'
                identity = hashlib.sha256((prepared / 'prepared-receipt.json').read_bytes()).hexdigest()
                command += ['--prepared', str(prepared), '--prepared-sha256', identity]
            record = {'argv': command, 'cwd': str(check.HERE), 'exit': None}
            commands.append(record)
            remaining = 120 - (time.monotonic() - start)
            if remaining <= 0:
                raise RuntimeError('120-second total check cap')
            try:
                completed = subprocess.run(command, cwd=check.HERE, text=True,
                                           capture_output=True, timeout=remaining,
                                           env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
            except subprocess.TimeoutExpired as e:
                record['exit'] = 'timeout'
                for suffix, data in (('stdout', e.stdout), ('stderr', e.stderr)):
                    if isinstance(data, bytes):
                        data = data.decode(errors='replace')
                    (args.output / (phase + '.' + suffix + '.log')).write_text(data or '')
                raise
            (args.output / (phase + '.stdout.log')).write_text(completed.stdout)
            (args.output / (phase + '.stderr.log')).write_text(completed.stderr)
            record['exit'] = completed.returncode
            ledger_path = destination / 'ledger.json'
            if ledger_path.exists():
                ledger = json.loads(ledger_path.read_text())
                result['RTL_clocks_including_reset_setup'] += ledger['RTL_clocks_including_reset_setup']
            if completed.returncode:
                raise RuntimeError(f'{phase} failed; inspect {destination} and {phase}.stderr.log; no retry')
        result.update({k: v for k, v in ledger.items() if k != 'commands'})
        assert result['RTL_clocks_including_reset_setup'] == 175
        result['result'] = 'pass'
    except (OSError, ValueError, AssertionError, RuntimeError, subprocess.TimeoutExpired) as e:
        result['result'] = 'fail'
        result['error'] = str(e)
    finally:
        result.update(commands=commands, elapsed_seconds=time.monotonic() - start)
        check.put(args.output / 'run-ledger.json', result)
    print(json.dumps({k: v for k, v in result.items() if k != 'commands'}, indent=2))
    return 0 if result['result'] == 'pass' else 1


if __name__ == '__main__':
    sys.exit(main())
