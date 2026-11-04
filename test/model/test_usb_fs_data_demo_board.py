"""usb_fs_data_demo's board path (main, attach, run, the forked Linux client,
usb_demo.detach) on a fake board that logs every operation with the pid that
made it, and real signals (verifier 048). Only the demo's own process may
ever touch the board: the client child, which inherits the board's mapping,
must not, even when a signal reaches the whole process group (a terminal
Ctrl-C, an ssh hangup)."""
import os
import pathlib
import signal
import subprocess
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
HOST = ROOT / "fpga/kv260/host"
DRIVER = r'''
import os, pathlib, signal, sys, time, types
host, tools, log = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path[:0] = [host, tools]
from pe import sim

def note(op):
    with open(log, "a") as f:
        f.write(f"{os.getpid()} {op}\n")

class FakePE:
    transport = None
    def read(self, addr, *a, **k):
        return sim.ID_VALUE if addr == sim.A_ID else 0
    def write(self, addr, data):
        note(f"pe.write {addr:#x} {data}")
    def push(self, core, word):
        note(f"pe.push {core}")
    def pop_all(self, core):
        return []
    def stop(self, mask):
        note(f"pe.stop {mask}")

class FakeBoard:
    def reset(self):
        note("board.reset")
    def protoemu(self):
        return FakePE()
    def clk_hz(self):
        return 48e6

class FakeLab:
    GPIO = 1
    def identity(self):
        note("lab.identity")
        time.sleep(float(os.environ.get("LAB_DELAY", "0")))
        return {"clk_div": 25, "rtl_rev": "fake00000000"}
    def mode(self, m):
        pass

class FakeSpim:
    div = quad = None
    def select(self, fabric):
        pass
    def pmod(self, d):
        note(f"spim.pmod {d}")

sys.modules["pe_kria"] = types.SimpleNamespace(Board=FakeBoard)
sys.modules["pe_lab"] = types.SimpleNamespace(Lab=FakeLab)
sys.modules["pe_spim"] = types.SimpleNamespace(Spim=FakeSpim)
import usb_fs_data_demo as d
os.sched_setaffinity = lambda *a: None
dev = pathlib.Path(log).parent / "dev"
dev.mkdir(exist_ok=True)
(dev / "bConfigurationValue").write_text("1\n")
d.usb_demo.sysfs_device = lambda *a, **k: str(dev)      # Linux has configured it
d.usb_demo.detect = lambda spim, pe: ("normal", "D+")

def fake_client(*a):
    note(f"client SIGINT={signal.getsignal(signal.SIGINT)!r}")
    time.sleep(60)
    return 0
d.client = fake_client
if os.environ.get("FORK_SIGNAL"):          # a SIGTERM that lands between the fork and the unblock
    real_fork = os.fork
    def fork():
        pid = real_fork()
        if pid:
            os.kill(os.getpid(), signal.SIGTERM)     # blocked here: pending until fork_client unblocks
        return pid
    os.fork = fork
sys.argv = ["usb_fs_data_demo.py", "--sizes", "8", "--rounds", "1", "--attaches", "1", "--timeout", "30"]
d.main()
'''


def start(tmp_path, env=None):
    driver, log = tmp_path / "driver.py", tmp_path / "board.log"
    driver.write_text(DRIVER)
    log.write_text("")
    p = subprocess.Popen([sys.executable, str(driver), str(HOST), str(ROOT / "tools"), str(log)],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True,
                         env={**os.environ, **(env or {})})
    return p, log


def wait_for(log, text, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if text in log.read_text():
            return
        time.sleep(0.1)
    raise AssertionError(f"never logged {text!r}: {log.read_text()}")


def ops(log):
    return [line.split(" ", 1) for line in log.read_text().splitlines()]


DETACH = ["pe.write 0x809 0", "spim.pmod {0: 8}", "pe.stop 1"]   # usb_demo.detach, as a normal end


def test_a_group_signal_reaches_only_the_demo_on_the_board(tmp_path):
    """A SIGINT to the whole process group while the Linux client runs: the
    client (default handlers, so it just dies) never touches the board; the
    demo kills it, detaches as at a normal end, and says STOPPED (130)."""
    p, log = start(tmp_path)
    wait_for(log, "client SIGINT=")
    os.killpg(p.pid, signal.SIGINT)
    out, _ = p.communicate(timeout=120)
    assert p.returncode == 130 and "STOPPED" in out and "the Linux client killed" in out, out
    client = [pid for pid, op in ops(log) if op.startswith("client")]
    assert client and client[0] != str(p.pid)
    assert "SIG_DFL" in [op for pid, op in ops(log) if op.startswith("client")][0], "the client kept the demo's handler"
    board = [(pid, op) for pid, op in ops(log) if not op.startswith("client")]
    assert {pid for pid, _ in board} == {str(p.pid)}, f"another process touched the board: {board}"
    assert [op for _, op in board][-3:] == DETACH, board[-5:]
    assert "[device software] attach 1: 0 requests, 0 packets re-staged; detached" in out, out


def test_a_stop_before_the_wiring_check_is_clean(tmp_path):
    """A SIGTERM before [wiring] (here, during Lab.identity): STOPPED and 143,
    no traceback, and the board not touched (verifier 048 note 1)."""
    p, log = start(tmp_path, {"LAB_DELAY": "5"})
    wait_for(log, "lab.identity")
    p.send_signal(signal.SIGTERM)
    out, _ = p.communicate(timeout=120)
    assert p.returncode == 143 and "STOPPED" in out and "Traceback" not in out, out
    assert "[device software] no attach; the board not touched" in out, out
    assert [op for _, op in ops(log)] == ["lab.identity"]


def test_a_stop_raised_by_the_fork_unblock_still_kills_the_client(tmp_path):
    """A signal pending across the fork is handled when fork_client unblocks,
    before the pid is back in run(): the client is still killed (the demo
    does not sit on an orphan holding its output) and attach 1 is reported."""
    p, log = start(tmp_path, {"FORK_SIGNAL": "1"})
    out, _ = p.communicate(timeout=30)
    assert p.returncode == 143 and "STOPPED" in out and "the Linux client killed" in out, out
    assert "[device software] attach 1: 0 requests, 0 packets re-staged; detached" in out, out
    board = [(pid, op) for pid, op in ops(log) if not op.startswith("client")]
    assert {pid for pid, _ in board} == {str(p.pid)} and [op for _, op in board][-3:] == DETACH


@pytest.mark.parametrize("sig", ["SIGTERM", "SIGHUP"])
def test_a_stop_during_an_attach_reports_that_attach(tmp_path, sig):
    """A SIGTERM or SIGHUP to the demo alone while attach 1's client runs: the
    report names attach 1 (verifier 048 note 3), the client killed, detached."""
    p, log = start(tmp_path)
    wait_for(log, "client SIGINT=")
    p.send_signal(getattr(signal, sig))
    out, _ = p.communicate(timeout=120)
    assert p.returncode == 128 + getattr(signal, sig) and "STOPPED" in out, out
    assert "[device software] attach 1: 0 requests, 0 packets re-staged; detached" in out, out
    board = [(pid, op) for pid, op in ops(log) if not op.startswith("client")]
    assert {pid for pid, _ in board} == {str(p.pid)} and [op for _, op in board][-3:] == DETACH
