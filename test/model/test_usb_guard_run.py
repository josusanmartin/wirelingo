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
        def __init__(self, cmd, stdout, **kw):
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


def fake_tracefs(root, journal=""):
    (root / "events/xhci-hcd").mkdir(parents=True)
    for f, v in {"buffer_size_kb": "1408\n", "tracing_on": "1\n", "events/xhci-hcd/enable": "0\n",
                 "set_event": "sched:sched_switch\n", "trace": "", "usbmon-1u": "", "journal.txt": journal}.items():
        (root / f).write_text(v)
    return root


def restored(root):
    return ((root / "buffer_size_kb").read_text(), (root / "tracing_on").read_text(),
            (root / "events/xhci-hcd/enable").read_text(), (root / "set_event").read_text().split()) == \
        ("1408\n", "1\n", "0\n", ["sched:sched_switch"])


@pytest.mark.parametrize("sig, ignored", [("SIGTERM", False), ("SIGINT", True)])
def test_a_signal_stops_the_run_cleanly(tmp_path, sig, ignored):
    """A real signal to the run while a demo child is running (verifier 046's
    board run: SIGINT went on to the next attach; SIGTERM killed it with the
    tracing state left set). SIGINT is sent with SIGINT ignored at the run's
    start, as for a run put in the background by a non-interactive shell. The
    run must end, the demo must stop itself (rc 128 + 15: its own SIGTERM
    handler, after detaching), the tracing state must be put back, and
    summary.txt must say STOPPED."""
    import signal
    import time
    trace, out = fake_tracefs(tmp_path / "tracing"), tmp_path / "out"
    pre = (lambda: signal.signal(signal.SIGINT, signal.SIG_IGN)) if ignored else None
    p = subprocess.Popen([sys.executable, str(RUN), "--model", "--attaches", "50", "--out", str(out),
                          "--trace-root", str(trace)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, preexec_fn=pre)
    log = out / "demo-1.log"
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline and not (log.exists() and "[service]" in log.read_text()):
        time.sleep(0.2)
    assert log.exists() and "[service]" in log.read_text(), "the first demo never started"
    p.send_signal(getattr(signal, sig))
    stdout, _ = p.communicate(timeout=300)
    assert p.returncode == 128 + getattr(signal, sig), stdout
    summary = (out / "summary.txt").read_text()
    assert f"[stopped] by {sig} during attach 1 (the demo stopped: rc 143)" in summary, summary
    assert summary.rstrip().endswith("STOPPED") and "the zero-length steps not run" in summary
    assert "STOPPED" in log.read_text(), "the demo did not stop itself"
    assert not (out / "demo-2.log").exists(), "it went on to the next attach"
    assert restored(trace), "the tracing state was not put back"


REAP_DRIVER = r'''
import os, pathlib, signal, sys
sys.path.insert(0, sys.argv[1])
import usb_guard_run as g
g.DEMO = pathlib.Path(sys.argv[2])
stopper = g.Stopper()
real_waitpid, real_kill = os.waitpid, os.kill
def waitpid(pid, flags):                 # Popen.wait's reap; a SIGINT just after it
    got, st = real_waitpid(pid, flags)
    if got:
        print(f"reaped {got}", flush=True)
        real_kill(os.getpid(), signal.SIGINT)
    return got, st
def kill(pid, sig):
    print(f"kill {pid} {sig}", flush=True)
    return real_kill(pid, sig)
os.waitpid, os.kill = waitpid, kill
rc, _ = g.demo([], sys.argv[3], False, 60, stopper)
print(f"rc {rc}, stopped by {stopper.signal.name}", flush=True)
'''


def test_a_signal_as_the_demo_is_reaped_signals_no_reaped_pid(tmp_path):
    """A SIGINT just after Popen.wait has reaped the demo, before it notes the
    exit status: the stopper's handler held off until the wait is over
    (verifier 054), it finds the demo ended and sends nothing; unheld, its
    poll() said running and the SIGTERM went to the reaped pid (a reused one:
    some other process)."""
    driver, demo = tmp_path / "driver.py", tmp_path / "demo.py"
    driver.write_text(REAP_DRIVER)
    demo.write_text("import time\ntime.sleep(0.5)\n")
    p = subprocess.run([sys.executable, str(driver), str(RUN.parent), str(demo), str(tmp_path / "demo.log")],
                       capture_output=True, text=True, timeout=60)
    lines = p.stdout.splitlines()
    assert p.returncode == 0 and lines[-1] == "rc 0, stopped by SIGINT", p.stdout + p.stderr
    reaped = [line.split()[1] for line in lines if line.startswith("reaped")]
    assert len(reaped) == 1, p.stdout
    assert not [line for line in lines if line.startswith(f"kill {reaped[0]} ")], p.stdout


def test_no_device_stops_early(tmp_path):
    """A bus with nothing on it (the demo's --model-no-device): after --give-up
    attaches that see no request and no kernel line for the port, it stops
    with the wiring message and writes summary.txt (exit 3)."""
    trace, out = fake_tracefs(tmp_path / "tracing"), tmp_path / "out"
    p = subprocess.run([sys.executable, str(RUN), "--model", "--model-no-device", "--give-up", "3",
                        "--attaches", "50", "--out", str(out), "--trace-root", str(trace)],
                       capture_output=True, text=True, timeout=900)
    assert p.returncode == 3, p.stdout + p.stderr
    summary = (out / "summary.txt").read_text()
    assert "no device seen on the hub port in 3 attaches: check the USB wiring" in summary
    assert "[attaches] 3 of 50 run" in summary and summary.rstrip().endswith("NO DEVICE")
    assert (out / "demo-3.log").exists() and not (out / "demo-4.log").exists()
    assert restored(trace)


def test_a_kernel_line_for_the_port_is_not_nothing(tmp_path):
    """The same empty attaches, but the kernel logged the port (a device the
    hub saw and failed to enumerate): that is not "no device", so the run does
    not stop for it."""
    trace = fake_tracefs(tmp_path / "tracing", journal="usb 1-1.4: new full-speed USB device number 9 using xhci-hcd\n")
    out = tmp_path / "out"
    p = subprocess.run([sys.executable, str(RUN), "--model", "--model-no-device", "--give-up", "2",
                        "--attaches", "3", "--writes", "0", "--reads", "", "--out", str(out), "--trace-root", str(trace)],
                       capture_output=True, text=True, timeout=900)
    summary = (out / "summary.txt").read_text()
    assert p.returncode == 1 and "no device seen" not in summary, summary
    assert "[attaches] 3 runs, 3 not configured or failed" in summary


def test_port_report_reads_the_log_and_sysfs(tmp_path):
    """The read-only report at the start: the port this boot's kernel log last
    saw our device on, and the hub port's sysfs state."""
    sys.path.insert(0, str(RUN.parent))
    import usb_guard_run as g
    state = tmp_path / "1-1:1.0" / "1-1-port4" / "state"
    state.parent.mkdir(parents=True)
    state.write_text("not attached\n")
    log = ("usb 1-1.4: New USB device found, idVendor=1209, idProduct=5634, bcdDevice= 0.01\n"
           "usb 1-1.1: New USB device found, idVendor=a69c, idProduct=8d80\n")
    run = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout=log)
    assert g.port_report("1-1.4", run=run, sysfs=tmp_path) == [
        "[port] device last seen on usb 1-1.4 (kernel log, this boot)", "[port] 1-1-port4 state: not attached"]
