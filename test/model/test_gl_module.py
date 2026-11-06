"""flow/gl_module_test.py with a substitute make: a receipt only when every
selected test passed exactly once; verify catches later edits."""
import os
import subprocess
import sys

import pytest

from test_release_evidence import ROOT, candidate

FAKE_MAKE = '''#!/usr/bin/env python3
import os, sys, pathlib, xml.etree.ElementTree as ET
a = dict(x.split('=', 1) for x in sys.argv[1:] if '=' in x)
assert {k: a[k] for k in ('GATES', 'SIM', 'TB', 'IMEM', 'SDF')} == dict(GATES='yes', SIM='icarus', TB='top', IMEM='sram', SDF='')
assert os.environ['PE_CLK_NS'] == '20' and os.environ['PE_NCORES'] == '2' and os.environ['PE_NBANKS'] == '2'
assert 'COCOTB_TEST_FILTER' not in os.environ
assert pathlib.Path(a['GATE_LEVEL_NETLIST']).read_text() == 'module design; endmodule\\n'
b, name = os.environ['FAKE_BEHAVIOR'], a['COCOTB_TESTCASE']
r = ET.Element('testsuites'); suite = ET.SubElement(r, 'testsuite')
c = ET.SubElement(suite, 'testcase', name=name)
if b == 'failed' and name == 'test_b': ET.SubElement(c, 'failure', error_type='AssertionError', error_msg='x')
if b == 'skipped' and name == 'test_b': ET.SubElement(c, 'skipped')
if not (b == 'missing-xml' and name == 'test_b'): ET.ElementTree(r).write(a['COCOTB_RESULTS_FILE'])
print('** TESTS=1 **')
if b == 'changed-candidate': pathlib.Path(os.environ['CANDIDATE'], 'runs/wokwi/final/nl/design.nl.v').write_text('changed')
sys.exit(1 if b == 'failed' and name == 'test_b' else 0)
'''


def run_module(tmp_path, behavior):
    wt = candidate(tmp_path)
    fake = tmp_path / "fakebin"
    fake.mkdir()
    (fake / "make").write_text(FAKE_MAKE)
    (fake / "make").chmod(0o755)
    validation = tmp_path / "validation"
    (validation / "test").mkdir(parents=True)
    (validation / "src").mkdir()
    (validation / "src/project.v").write_bytes((wt / "src/project.v").read_bytes())
    (validation / "test/test_extra.py").write_text(
        "@cocotb.test()\nasync def test_a(dut): pass\n@cocotb.test()\nasync def test_b(dut): pass\n")
    env = dict(os.environ, FAKE_BEHAVIOR=behavior, CANDIDATE=str(wt), COCOTB_TEST_FILTER="hostile")
    result = subprocess.run([sys.executable, str(ROOT / "flow/gl_module_test.py"), "run", str(wt),
                             "--validation-root", str(validation), "--module", "test_extra", "--jobs", "2",
                             "--sim-path", str(fake), "--out", str(tmp_path / "out")],
                            capture_output=True, text=True, env=env)
    return wt, validation, result


@pytest.mark.parametrize("behavior", ["pass", "failed", "skipped", "missing-xml", "changed-candidate"])
def test_receipt_only_when_every_test_passes(tmp_path, behavior):
    wt, validation, result = run_module(tmp_path, behavior)
    good = behavior == "pass"
    assert (result.returncode == 0) == good, result.stdout + result.stderr
    assert (tmp_path / "out/receipt.json").exists() == good
    assert (tmp_path / "out/summary.json").exists()
    assert not list(wt.rglob("sim_build"))          # the candidate is only read


def test_verify_detects_changed_evidence(tmp_path):
    wt, validation, result = run_module(tmp_path, "pass")
    assert result.returncode == 0, result.stdout + result.stderr
    verify = [sys.executable, str(ROOT / "flow/gl_module_test.py"), "verify", str(tmp_path / "out/receipt.json"),
              str(wt), "--validation-root", str(validation)]
    assert subprocess.run(verify, capture_output=True).returncode == 0
    (tmp_path / "out/test_b/sim.log").write_text("** TESTS=1 ** edited")
    assert subprocess.run(verify, capture_output=True).returncode == 1


def test_refuses_other_hardware(tmp_path):
    wt = candidate(tmp_path)
    validation = tmp_path / "validation"
    (validation / "test").mkdir(parents=True)
    (validation / "src").mkdir()
    (validation / "src/project.v").write_text("different hardware\n")
    (validation / "test/test_extra.py").write_text("@cocotb.test()\nasync def test_a(dut): pass\n")
    result = subprocess.run([sys.executable, str(ROOT / "flow/gl_module_test.py"), "run", str(wt),
                             "--validation-root", str(validation), "--module", "test_extra",
                             "--out", str(tmp_path / "out")], capture_output=True, text=True)
    assert result.returncode == 1 and "differs from the candidate" in result.stderr
    assert not (tmp_path / "out").exists()
