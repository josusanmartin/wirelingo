"""Exercise the real runner scripts with deterministic external-tool substitutes."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from test_release_evidence import ROOT, candidate


@pytest.mark.parametrize("behavior", ["pass", "missing-xml", "failed", "skipped", "duplicate",
                                     "annotation-error", "annotation-warning", "no-annotation", "negative-assert",
                                     "negative-x", "negative-runtime", "changed-candidate", "hostile-environment"])
def test_sdf_runner_emits_receipt_only_for_completed_outcome(tmp_path, behavior):
    wt = candidate(tmp_path)
    (wt / "flow").mkdir()
    for name in ("gl_sdf_test.sh", "release_receipts.py"):
        shutil.copyfile(ROOT / "flow" / name, wt / "flow" / name)
    shutil.copyfile(ROOT / "test/check_results.py", wt / "test/check_results.py")
    (wt / "flow/sdf_prep.py").write_text(
        "import sys\nfrom pathlib import Path\n"
        "Path(sys.argv[3]).write_bytes(Path(sys.argv[1]).read_bytes())\n"
        "Path(sys.argv[4]).write_bytes(Path(sys.argv[2]).read_bytes())\n")
    subprocess.run(["git", "init", "-q", str(wt)], check=True)
    fake = wt / "fakebin"
    fake.mkdir()
    make = fake / "make"
    make.write_text('''#!/usr/bin/env python3
import os, sys, pathlib, xml.etree.ElementTree as ET
a = dict(x.split('=', 1) for x in sys.argv[1:] if '=' in x)
assert {k: a[k] for k in ('SIM', 'TB', 'IMEM', 'SDF_MTM', 'TB_EXT_DELAY')} == dict(SIM='icarus', TB='top', IMEM='sram', SDF_MTM='typ', TB_EXT_DELAY='4')
b = os.environ['FAKE_BEHAVIOR']
if b != 'no-annotation': print('sdf_annotate: ' + a['SDF'] + ' -> tb.user_project')
if b == 'annotation-error': print('SDF ERROR: unknown cell')
if b == 'annotation-warning': print('SDF WARNING: annotate.sdf:100: COND not supported.')
names = a['COCOTB_TESTCASE'].split(',')
if b == 'duplicate': names = [names[0]] * len(names)
r = ET.Element('testsuites'); suite = ET.SubElement(r, 'testsuite')
for n in names:
    c = ET.SubElement(suite, 'testcase', name=n)
    if b == 'skipped': ET.SubElement(c, 'skipped')
    elif b == 'failed' or b.startswith('negative-'):
        error = {'negative-runtime': 'RuntimeError', 'negative-x': 'ValueError'}.get(b, 'AssertionError')
        message = "Can't convert LogicArray to int: it contains non-0/1 values" if b == 'negative-x' else 'fixture failed'
        ET.SubElement(c, 'failure', error_type=error, error_msg=message)
        print(error + ': ' + message)
if b != 'missing-xml': ET.ElementTree(r).write(a['COCOTB_RESULTS_FILE'])
print('** TESTS=' + str(len(names)) + ' **')
if b == 'changed-candidate':
    pathlib.Path('../runs/wokwi/final/nl/design.nl.v').write_text('changed')
sys.exit(2 if b == 'failed' or b.startswith('negative-') else 0)
''')
    make.chmod(0o755)
    env = dict(os.environ, PATH=str(fake) + os.pathsep + os.environ["PATH"],
               TESTS="test_a test_b", FAKE_BEHAVIOR=behavior)
    if behavior == "hostile-environment":
        env.update(SIM="another-simulator", TB="chip", IMEM="flop", SDF_MTM="min", TB_EXT_DELAY="0")
    expectation = "fail" if behavior.startswith("negative-") else "pass"
    result = subprocess.run(["bash", str(wt / "flow/gl_sdf_test.sh"), str(wt), "slow", "10", expectation],
                            capture_output=True, text=True, env=env)
    good = behavior in ("pass", "negative-assert", "negative-x", "hostile-environment")
    assert (result.returncode == 0) == good, result.stdout + result.stderr
    receipts = list((wt / "test/sim_build").glob("gl_sdf.*/receipt.json"))
    assert len(receipts) == int(good)


@pytest.mark.parametrize("behavior", ["pass", "failed", "missing-xml", "wrong-gds", "changed-tool"])
def test_precheck_runner_keeps_shared_reports_and_binds_its_own_run(tmp_path, behavior):
    wt = candidate(tmp_path)
    tt = tmp_path / "tt"
    pre = tt / "precheck"
    pre.mkdir(parents=True)
    (tt / "tech").mkdir()
    old = pre / "reports/results.xml"
    old.parent.mkdir()
    old.write_text("old evidence must remain unchanged")
    (pre / "precheck.py").write_text('''import os, sys
from pathlib import Path
b = os.environ['FAKE_BEHAVIOR']
gds = sys.argv[sys.argv.index('--gds') + 1]
assert Path('reports').is_dir(), 'upstream precheck requires a reports directory'
assert not list(Path('reports').iterdir()), 'previous reports must not be copied'
if b != 'missing-xml':
    Path('reports/results.xml').write_text('<testsuites><testsuite><testcase name="drc"/></testsuite></testsuites>')
print('INFO: Precheck passed for ' + ('another.gds' if b == 'wrong-gds' else gds) + '!')
if b == 'changed-tool': Path(__file__).write_text('changed tool during run')
sys.exit(2 if b == 'failed' else 0)
''')
    out = tmp_path / "private-results"
    result = subprocess.run([sys.executable, str(ROOT / "flow/precheck_test.py"), str(wt),
                             "--tt-root", str(tt), "--out", str(out)], capture_output=True, text=True,
                            env=dict(os.environ, PDK_ROOT=str(tmp_path / "pdk"), FAKE_BEHAVIOR=behavior))
    assert (result.returncode == 0) == (behavior == "pass"), result.stdout + result.stderr
    assert (out / "receipt.json").exists() == (behavior == "pass")
    assert old.read_text() == "old evidence must remain unchanged"
    # No existing private results may be overwritten on a retry.
    retry = subprocess.run([sys.executable, str(ROOT / "flow/precheck_test.py"), str(wt),
                            "--tt-root", str(tt), "--out", str(out)], capture_output=True, text=True,
                           env=dict(os.environ, PDK_ROOT=str(tmp_path / "pdk")))
    assert retry.returncode != 0
