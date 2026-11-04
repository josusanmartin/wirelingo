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


def test_tracer_puts_back_what_it_found(tmp_path):
    """The board path's capture (verifier 044, note 4) on a fake tracefs: one
    passing attach and one failing one, then close(): buffer size, tracing
    on/off, the enabled events and the xHCI enable are as they were before."""
    sys.path.insert(0, str(RUN.parent))
    import usb_guard_run as g
    trace, out = tmp_path / "tracing", tmp_path / "out"
    (trace / "events/xhci-hcd").mkdir(parents=True)
    out.mkdir()
    before = {"buffer_size_kb": "1408\n", "tracing_on": "1\n", "events/xhci-hcd/enable": "0\n",
              "set_event": "sched:sched_switch\nirq:irq_handler_entry\n", "trace": "old\n"}
    for f, v in before.items():
        (trace / f).write_text(v)
    (tmp_path / "1u").write_text("")
    calls = []

    class Done:
        stdout = "kernel log\n"

    def run(cmd, **kw):
        calls.append(cmd[0])
        return Done()

    class Mon:
        def __init__(self, cmd, stdout):
            stdout.write("usbmon text\n")
            stdout.close()

        def terminate(self):
            pass

        def wait(self):
            pass

    t = g.Tracer(out, trace=trace, usbmon=tmp_path / "1u", run=run, popen=Mon)
    assert (trace / "buffer_size_kb").read_text() == "16384" and (trace / "events/xhci-hcd/enable").read_text() == "1"
    t.start(1)
    t.stop(1, failed=False)
    t.start(2)
    t.stop(2, failed=True)
    t.close()
    assert not (out / "mon-1.txt").exists() and (out / "mon-2.txt").read_text() == "usbmon text\n"
    assert (out / "dmesg-2.txt").read_text() == "kernel log\n" and (out / "trace-2.txt").exists()
    assert calls == ["modprobe", "journalctl"]
    for f in ("buffer_size_kb", "tracing_on", "events/xhci-hcd/enable"):
        assert (trace / f).read_text() == before[f], f
    assert (trace / "set_event").read_text().split() == before["set_event"].split()


@pytest.mark.skipif(os.geteuid() == 0, reason="the refusal is for a non-root run")
def test_board_path_refuses_without_root(tmp_path):
    p = subprocess.run([sys.executable, str(RUN), "--out", str(tmp_path)], capture_output=True, text=True, timeout=60)
    assert p.returncode != 0 and "run as root" in p.stderr
    assert not any(tmp_path.iterdir())
