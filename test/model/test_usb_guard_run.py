"""fpga/kv260/host/usb_guard_run.py, the board run for the got_in guard, on
the cycle model (--model): one attach, two zero-length writes, one zero-length
read, through the same steps and log parsing as on the board. The board path
refuses before it touches anything unless run as root with protoemu48 active."""
import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
RUN = ROOT / "fpga/kv260/host/usb_guard_run.py"


def test_model_run(tmp_path):
    p = subprocess.run([sys.executable, str(RUN), "--model", "--attaches", "1", "--writes", "2", "--reads", "1",
                        "--out", str(tmp_path)], capture_output=True, text=True, timeout=900)
    assert p.returncode == 0, p.stdout + p.stderr
    summary = (tmp_path / "summary.txt").read_text()
    assert "usb_fs_dev 142 words, got_in guard present" in summary
    assert "[attaches] 1 runs, 0 not configured or failed" in summary
    assert "[zero-length writes] rt 0x40 rq 0x10: 2 of 2 ok, 0 timed out" in summary
    assert "[zero-length reads] rt 0xC0 rq 0x11: 1 of 1 ok, 0 timed out" in summary
    assert summary.rstrip().endswith("PASS")
    assert "NODATA rt 0xC0 rq 0x11 wLength 0" in (tmp_path / "nodata-1-reads-1.log").read_text()


@pytest.mark.skipif(os.geteuid() == 0, reason="the refusal is for a non-root run")
def test_board_path_refuses_without_root(tmp_path):
    p = subprocess.run([sys.executable, str(RUN), "--out", str(tmp_path)], capture_output=True, text=True, timeout=60)
    assert p.returncode != 0 and "run as root" in p.stderr
    assert not any(tmp_path.iterdir())
