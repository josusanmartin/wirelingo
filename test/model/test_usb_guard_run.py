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
    assert t.close() == []
    assert not (out / "mon-1.txt").exists() and (out / "mon-2.txt").read_text() == "usbmon text\n"
    assert (out / "dmesg-2.txt").read_text() == "kernel log\n" and (out / "trace-2.txt").exists()
    assert calls == ["modprobe", "journalctl"]
    for f in ("buffer_size_kb", "tracing_on", "events/xhci-hcd/enable"):
        assert (trace / f).read_text() == before[f], f
    assert (trace / "set_event").read_text().split() == before["set_event"].split()


@pytest.mark.parametrize("found", ["1408\n", "7 (expanded: 1408)\n", "X\n"])
def test_full_run_with_a_fake_tracefs(tmp_path, monkeypatch, found):
    """The whole run (one attach, a write, a read, on the model) with the board
    path's capture on a fake tracefs whose buffer_size_kb reads each of the
    kernel's forms (Linux 6.8 tracing_entries_read: a number; "N (expanded:
    M)" before the ring buffer's first expansion; "X" when the per-CPU sizes
    differ) and, like tracing_entries_write, takes only an integer: EINVAL
    otherwise (verifier 045). The run must finish, PASS and write summary.txt;
    a form it cannot take back is left and said so, never written."""
    sys.path.insert(0, str(RUN.parent))
    import usb_guard_run as g
    trace, out = tmp_path / "tracing", tmp_path / "out"
    (trace / "events/xhci-hcd").mkdir(parents=True)
    for f, v in {"buffer_size_kb": found, "tracing_on": "1\n", "events/xhci-hcd/enable": "0\n",
                 "set_event": "", "trace": "", "usbmon-1u": ""}.items():
        (trace / f).write_text(v)
    tried = []
    real_open = open

    def kernel_open(path, mode="r", *a, **kw):
        f = real_open(path, mode, *a, **kw)
        if pathlib.Path(path).name != "buffer_size_kb" or "w" not in mode:
            return f
        write = f.write

        def checked(text):
            tried.append(text)
            if not text.strip().isdigit():
                f.close()
                raise OSError(22, "Invalid argument")
            return write(text)
        f.write = checked
        return f
    monkeypatch.setattr(g, "open", kernel_open, raising=False)
    status = g.main(["--model", "--attaches", "1", "--writes", "1", "--reads", "1",
                     "--out", str(out), "--trace-root", str(trace)])
    summary = (out / "summary.txt").read_text()
    assert status == 0 and summary.rstrip().endswith("PASS"), summary
    assert "[zero-length writes] rt 0x40 rq 0x10: 1 of 1 ok" in summary
    assert "[zero-length reads] rt 0xC0 rq 0x11: 1 of 1 ok" in summary
    assert (trace / "tracing_on").read_text() == "1\n"
    assert all(t.strip().isdigit() for t in tried), f"wrote {tried} to buffer_size_kb"
    if found.strip().isdigit():
        assert (trace / "buffer_size_kb").read_text() == found and "[tracing]" not in summary
    else:
        assert (trace / "buffer_size_kb").read_text() == "16384"
        assert f"[tracing] buffer_size_kb: left at 16384, it read {found.strip()!r}" in summary


@pytest.mark.skipif(os.geteuid() == 0, reason="the refusal is for a non-root run")
def test_board_path_refuses_without_root(tmp_path):
    p = subprocess.run([sys.executable, str(RUN), "--out", str(tmp_path)], capture_output=True, text=True, timeout=60)
    assert p.returncode != 0 and "run as root" in p.stderr
    assert not any(tmp_path.iterdir())
