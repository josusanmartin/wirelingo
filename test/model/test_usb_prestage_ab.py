"""fpga/kv260/host/usb_prestage_ab.py, the board A/B for SET_CONFIGURATION's
status stage, off the board: its classification, order and statistics, its
control flow with stand-in demos and a fake tracefs (as test_usb_guard_run),
and one real pair on the cycle model (the demo's --model path, each arm from
its own checkout)."""
import pathlib
import shutil
import signal
import subprocess
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
HOST = ROOT / "fpga/kv260/host"
RUN = HOST / "usb_prestage_ab.py"
sys.path.insert(0, str(HOST))
import usb_prestage_ab as ab                      # noqa: E402
from test_usb_config_prestage import without_prestage   # noqa: E402
from test_usb_guard_run import fake_tracefs, restored   # noqa: E402

STALL = ("  attach 1 of 1: not configured after 7 s; requests seen: ['8006000200000900', '8006000200001200', "
         "'0009010000000000']; F_SET 0x10; core 0 TX FIFO 2 words, RX FIFO 0, PC 4; software staged (11, b''), "
         "pending []\n[device software] attach 1: 4 requests, 0 packets re-staged; detached\nFAIL\n")
FAKE_DEMO = r'''
import pathlib, sys, time
here = pathlib.Path(__file__).parent
n = int((here / "count").read_text()) if (here / "count").exists() else 0
(here / "count").write_text(str(n + 1))
modes = (here / "modes").read_text().split()
mode = modes[n % len(modes)]
if mode == "ok":
    print("  configured after 1.20 s, 4 requests, address 7\nPASS")
    sys.exit(0)
if mode == "stall":
    print(STALL, end="")
    sys.exit(1)
if mode == "sleep":
    print("[service] answering over the SPI port until Linux has configured the device", flush=True)
    time.sleep(120)
print("Traceback (most recent call last):\nOSError: [Errno 5] Input/output error")
sys.exit(1)
'''.replace("STALL", repr(STALL))


def test_classify():
    assert ab.classify(0, "PASS\n") == "ok"
    assert ab.classify(1, STALL) == "stall"
    other = STALL.replace("'0009010000000000'", "'8006000100001200'")     # stopped earlier in enumeration
    assert ab.classify(1, other) == "other"
    assert ab.classify(1, "  attach 1 of 1: not configured after 7 s; requests seen: []; F_SET 0x00\nFAIL") == "other"
    assert ab.classify(124, "") == "other"
    assert ab.classify(2, "FAIL (wiring): every PMOD pin disabled") == "other"


def test_order_is_abba():
    assert "".join(ab.order(4)) == "ABBAABBA"
    assert ab.order(300).count("A") == ab.order(300).count("B") == 300


def test_statistics():
    """The figures --plan prints and docs/usb-full-speed.md quotes."""
    assert ab.fisher_less(6, 200, 0, 200) == pytest.approx(0.0150, abs=5e-4)
    assert ab.fisher_less(3, 100, 0, 100) == pytest.approx(0.123, abs=1e-3)
    assert ab.fisher_less(0, 100, 0, 100) == 1.0
    assert ab.power(100, 0.03, 0) == pytest.approx(0.18, abs=0.01)
    assert ab.power(200, 0.03, 0) == pytest.approx(0.72, abs=0.01)
    assert ab.power(300, 0.03, 0) == pytest.approx(0.95, abs=0.01)
    assert ab.power(300, 0.03, 0.01) == pytest.approx(0.43, abs=0.01)
    assert 0.97 ** 100 == pytest.approx(0.0476, abs=1e-4) and 0.97 ** 300 < 1e-3
    assert len(ab.plan()) == 6


def fake_arms(tmp_path, modes_a, modes_b):
    """Two checkouts whose usb_fs_data_demo.py prints a canned result per call
    (ok, stall, other or sleep, cycling); A's firmware without the pre-stage."""
    trees = {}
    for k, modes, fw in (("A", modes_a, without_prestage()), ("B", modes_b, None)):
        t = tmp_path / k
        (t / "fpga/kv260/host").mkdir(parents=True)
        (t / "firmware").mkdir()
        (t / "firmware/usb_fs_dev.pasm").write_text(fw or (ROOT / "firmware/usb_fs_dev.pasm").read_text())
        (t / "fpga/kv260/host/usb_fs_data_demo.py").write_text(FAKE_DEMO)
        (t / "fpga/kv260/host/modes").write_text(modes)
        trees[k] = t
    return trees


def calls(tree):
    p = tree / "fpga/kv260/host/count"
    return int(p.read_text()) if p.exists() else 0


def test_counts_with_stand_in_demos(tmp_path):
    """Three pairs, A stalling on its first and third attach, B never: the
    arms alternate ABBA AB, each attach runs its own arm's demo, the counts
    and runs are right, the capture is put back."""
    trees = fake_arms(tmp_path, "stall ok", "ok")
    trace, out = fake_tracefs(tmp_path / "tracing"), tmp_path / "out"
    status = ab.main(["--model", "--a", str(trees["A"]), "--b", str(trees["B"]), "--pairs", "3",
                      "--out", str(out), "--trace-root", str(trace)])
    summary = (out / "summary.txt").read_text()
    assert status == 0, summary
    assert calls(trees["A"]) == 3 and calls(trees["B"]) == 3
    assert sorted(p.name for p in out.glob("demo-*.log")) == [f"demo-{i}-{k}.log" for i, k in enumerate("ABBAAB", 1)]
    assert "pre-stage absent" in summary and "pre-stage present" in summary
    assert "[arm A] 3 attaches: 1 ok, 2 stalled at SET_CONFIGURATION (runs 1 5), 0 other failures" in summary
    assert "[arm B] 3 attaches: 3 ok, 0 stalled at SET_CONFIGURATION, 0 other failures" in summary
    assert "stall rate A 2/3 = 66.7%, B 0/3 = 0.0%" in summary and summary.rstrip().endswith("DONE")
    assert restored(trace)


def test_other_failures_set_the_exit_status(tmp_path):
    trees = fake_arms(tmp_path, "ok", "ok other")
    status = ab.main(["--model", "--a", str(trees["A"]), "--b", str(trees["B"]), "--pairs", "2",
                      "--out", str(tmp_path / "out")])
    summary = (tmp_path / "out/summary.txt").read_text()
    assert status == 1 and "[arm B] 2 attaches: 1 ok, 0 stalled at SET_CONFIGURATION, 1 other failures (runs 3)" \
        in summary, summary
    assert summary.rstrip().endswith("DONE, with other failures")


@pytest.mark.parametrize("a, b, refused", [("B", "B", "A"), ("A", "A", "B"), ("B", "A", "A")])
def test_refuses_arms_the_wrong_way_round(tmp_path, a, b, refused):
    """A must lack the pre-stage and B have it; nothing runs otherwise."""
    trees = fake_arms(tmp_path, "ok", "ok")
    with pytest.raises(SystemExit) as e:
        ab.main(["--model", "--a", str(trees[a]), "--b", str(trees[b]), "--pairs", "1", "--out", str(tmp_path / "out")])
    assert f"STOP: arm {refused}" in str(e.value)
    assert calls(trees["A"]) == calls(trees["B"]) == 0 and not (tmp_path / "out").exists()


def test_a_signal_stops_the_run(tmp_path):
    """A SIGTERM while an arm's demo runs: the demo gets it, the run stops
    there (no next attach), puts the capture back and says STOPPED."""
    trees = fake_arms(tmp_path, "sleep", "ok")
    trace, out = fake_tracefs(tmp_path / "tracing"), tmp_path / "out"
    p = subprocess.Popen([sys.executable, str(RUN), "--model", "--a", str(trees["A"]), "--b", str(trees["B"]),
                          "--pairs", "5", "--out", str(out), "--trace-root", str(trace)],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    log = out / "demo-1-A.log"
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline and not (log.exists() and "[service]" in log.read_text()):
        time.sleep(0.1)
    assert log.exists(), "the first demo never started"
    p.send_signal(signal.SIGTERM)
    stdout, _ = p.communicate(timeout=120)
    assert p.returncode == 128 + signal.SIGTERM, stdout
    summary = (out / "summary.txt").read_text()
    assert "[stopped] by SIGTERM during attach 1 (arm A; the demo stopped" in summary, summary
    assert summary.rstrip().endswith("STOPPED") and calls(trees["B"]) == 0
    assert restored(trace)


def test_plan_prints_without_arms():
    p = subprocess.run([sys.executable, str(RUN), "--plan"], capture_output=True, text=True, timeout=120)
    assert p.returncode == 0 and "300 pairs (600 attaches): power 0.95 if B never stalls" in p.stdout, p.stdout


def test_one_pair_on_the_model(tmp_path):
    """The real demos, each from its own checkout, on the cycle model: A a
    copy of this one with the pre-stage taken out, B this one. Both attach and
    pass (the model host always sends the status IN, so neither stalls)."""
    a = tmp_path / "A"
    for sub in ("tools", "firmware", "fpga/kv260/host"):
        shutil.copytree(ROOT / sub, a / sub, ignore=shutil.ignore_patterns("__pycache__"))
    (a / "firmware/usb_fs_dev.pasm").write_text(without_prestage())
    out = tmp_path / "out"
    p = subprocess.run([sys.executable, str(RUN), "--model", "--a", str(a), "--pairs", "1", "--out", str(out)],
                       capture_output=True, text=True, timeout=1200)
    summary = (out / "summary.txt").read_text()
    assert p.returncode == 0, p.stdout + p.stderr
    assert "usb_fs_dev 159 words, pre-stage absent" in summary and "usb_fs_dev 159 words, pre-stage present" in summary
    assert "[arm A] 1 attaches: 1 ok" in summary and "[arm B] 1 attaches: 1 ok" in summary
    assert "PASS (model)" in (out / "demo-1-A.log").read_text() and "PASS (model)" in (out / "demo-2-B.log").read_text()
