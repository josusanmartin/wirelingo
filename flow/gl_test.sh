#!/bin/bash
# Functional GL without SDF. Each invocation has private output paths and
# tests the hardened worktree's sources. Optional second argument selects newer
# validation software, requiring the same manifest-bound hardware sources.
# Failed builds cannot reuse old XML.
set -euo pipefail
W=$(realpath "${1:-$PWD}")
shopt -s nullglob
NETLISTS=("$W"/runs/wokwi/final/nl/*.nl.v)
[[ ${#NETLISTS[@]} == 1 ]] || { echo "expected exactly one final netlist in $W"; exit 2; }
NL=${NETLISTS[0]}
REPO=$(realpath "${2:-$(git -C "$W" rev-parse --show-toplevel)}")
if [ "$REPO" != "$W" ] && [ ! -f "$W/candidate-inputs.json" ]; then
  echo "alternate validation sources require candidate-inputs.json"; exit 2
fi
def() { { grep -o "define $1 [0-9]" "$W/src/project.v" || true; } | awk '{print $3}' | head -1; }
NC=$(def PE_NCORES); FL=$(def PE_FIFO_LOG2); FL=${FL:-2}
NB=$(grep -q 'define PE_NBANKS `PE_NCORES' "$W/src/project.v" && echo "$NC" || def PE_NBANKS)
[[ $NC =~ ^[1-4]$ && $NB =~ ^[1-4]$ && $FL =~ ^[23]$ ]] || { echo "invalid build profile"; exit 2; }
mkdir -p "$REPO/test/sim_build"
OUT=$(mktemp -d "$REPO/test/sim_build/gl.XXXXXX")
python3 - "$REPO" "$W" "$OUT" <<'PY'
import hashlib, json, pathlib, subprocess, sys
repo, work, out = map(pathlib.Path, sys.argv[1:])
names = subprocess.check_output(['git', '-C', str(repo), 'ls-files', '--cached', '--others', '--exclude-standard'], text=True).splitlines()
files = {n: hashlib.sha256((repo / n).read_bytes()).hexdigest() for n in sorted(set(names))
         if (repo / n).is_file() and (n.startswith(('src/', 'firmware/', 'tools/', 'test/', 'formal/', 'flow/', '.github/'))
                                     or n in ('info.yaml', 'requirements.txt'))}
candidate = work / 'candidate-inputs.json'
if candidate.exists():
    hardware = {n: h for n, h in files.items() if n.startswith('src/') or n == 'info.yaml'}
    if hardware != json.loads(candidate.read_text())['hardware_files']:
        raise SystemExit('validation hardware differs from hardened candidate')
(out / 'inputs.json').write_text(json.dumps(dict(files=files, validation_repo=str(repo),
    candidate_inputs_sha256=hashlib.sha256(candidate.read_bytes()).hexdigest() if candidate.exists() else None), indent=2) + '\n')
PY
cp "$NL" "$OUT/netlist.v"
echo "GL outputs: $OUT; netlist $(sha256sum "$NL" | cut -d' ' -f1)"
cd "$REPO/test"
export PATH=$HOME/tools/oss-cad-suite/bin:$REPO/.venv/bin:$PATH PDK_ROOT=${PDK_ROOT:-$HOME/ttpdk}
for module in test test_pio test_eth_service; do
  case "$module" in
    test) prefix="";;
    test_pio) prefix="pio_";;
    test_eth_service) prefix="eth_service_";;
  esac
  [[ -f "$module.py" ]] || { echo "missing required GL suite $module"; exit 2; }
  count=$(grep -c '^@cocotb.test()' "$module.py")
  [[ $count -gt 0 ]] || { echo "empty GL suite $module"; exit 2; }
  if PE_NCORES=$NC PE_NBANKS=$NB PE_FIFO_LOG2=$FL make GATES=yes \
      GATE_LEVEL_NETLIST="$OUT/netlist.v" COCOTB_TEST_MODULES="$module" \
      SIM_BUILD="$OUT/${prefix}build" COCOTB_RESULTS_FILE="$OUT/${prefix}results.xml" \
      > "$OUT/${prefix}simulation.log" 2>&1; then
    python3 check_results.py "$OUT/${prefix}results.xml" "$count"
  else
    rc=$?
    tail -40 "$OUT/${prefix}simulation.log"
    echo "GL suite $module failed ($rc); results retained at $OUT"
    exit "$rc"
  fi
done
python3 - "$REPO" "$NL" "$OUT" <<'PY'
import datetime, hashlib, json, pathlib, subprocess, sys
w, nl, out = map(pathlib.Path, sys.argv[1:])
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
manifest = dict(completed_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                source_commit=subprocess.check_output(['git', '-C', str(w), 'rev-parse', 'HEAD'], text=True).strip(),
                netlist_sha256=sha(nl), simulated_netlist_sha256=sha(out / 'netlist.v'),
                test_sha256=sha(w / 'test/test.py'), results_sha256=sha(out / 'results.xml'),
                kind='functional GL without SDF', status='pass')
inputs = json.loads((out / 'inputs.json').read_text())
for name, expected in inputs['files'].items():
    if not (w / name).is_file() or sha(w / name) != expected:
        raise SystemExit('validation source changed while GL ran: ' + name)
manifest.update(inputs)
manifest['suites'] = []
for module, prefix in [('test', ''), ('test_pio', 'pio_'), ('test_eth_service', 'eth_service_')]:
    result = out / (prefix + 'results.xml')
    log = out / (prefix + 'simulation.log')
    count = sum(line.startswith('@cocotb.test()') for line in (w / 'test' / (module + '.py')).read_text().splitlines())
    manifest['suites'].append(dict(module=module, tests=count, results_file=result.name,
                                  results_sha256=sha(result), simulation_log_sha256=sha(log)))
if manifest['netlist_sha256'] != manifest['simulated_netlist_sha256']:
    raise SystemExit('source netlist changed while GL ran')
(out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
PY
