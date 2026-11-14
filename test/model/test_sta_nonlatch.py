"""flow/sta_nonlatch.py: the worst setup slack without the latches, with a receipt
that flow/release_check.py verifies and prints next to the latch-inclusive one.

The run's own STA reports the worst slack over every endpoint; a FIFO latch that
borrows time reports exactly 0, so that number reads 0 whenever everything else
is positive. Here a fake `docker` stands in for the LibreLane image's OpenSTA."""
import json
import os
from pathlib import Path
import stat
import sys

import pytest

from test_release_evidence import candidate, complete_receipts, module
from test_flow_slow48 import sta_step, STEP

REPORT = """== ALL
worst slack max 0.0000
== LATCH 1536
Startpoint: _55328_ (rising edge-triggered flip-flop clocked by clk)
Endpoint: u_lat.l (positive level-sensitive latch clocked by clk)
    Delay      Time   Description
   0.9370    3.5165   time borrowed from endpoint
             3.5165   data required time
-------------------------------------------------------------
             0.0000   slack (MET)
== NONLATCH 4200
max_delay/setup group clk

                                      Required    Actual
Endpoint                                 Delay     Delay     Slack
------------------------------------------------------------------
_53246_/D (sg13cmos5l_dfrbpq_1)        22.5730   22.1426    0.4304 (MET)

max_delay/setup group sck

                                      Required    Actual
Endpoint                                 Delay     Delay     Slack
------------------------------------------------------------------
uo_out[4] (output)                     15.5820   13.2945    2.2875 (MET)
== END
"""

FAKE = r'''#!PYTHON
import hashlib, json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_DOCKER_LOG"], "a") as f:
    f.write(json.dumps(args) + "\n")
if args[:3] == ["image", "inspect", "--format"]:
    print("sha256:" + hashlib.sha256(args[4].encode()).hexdigest())
    sys.exit(0)
assert args[:2] == ["run", "--rm"] and args[args.index("--network") + 1] == "none"
if "python3" in args:
    print("3.1.0.dev3")
    sys.exit(0)
assert args[-4:-1] == ["sta", "-no_splash", "-exit"]
sys.stdout.write(open(os.environ["FAKE_REPORT"]).read())
'''


@pytest.fixture
def fake_sta(tmp_path, monkeypatch):
    b = tmp_path / "fakebin"
    b.mkdir()
    d = b / "docker"
    d.write_text(FAKE.replace("PYTHON", sys.executable))
    d.chmod(d.stat().st_mode | stat.S_IEXEC)
    (tmp_path / "report.txt").write_text(REPORT)
    monkeypatch.setenv("PATH", f"{b}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_DOCKER_LOG", str(tmp_path / "docker.log"))
    monkeypatch.setenv("FAKE_REPORT", str(tmp_path / "report.txt"))
    return tmp_path / "docker.log"


def run(tmp_path, *extra):
    wt = sta_step(candidate(tmp_path))
    p = wt / "runs/wokwi" / STEP / "config.json"
    c = json.loads(p.read_text())
    pdk = Path(c["PDK_ROOT"])
    c.update(STA_CORNERS=["nom_typ_1p20V_25C", "nom_slow_1p08V_125C", "nom_fast_1p32V_m40C"],
             CELL_LIBS={"nom_slow_*": [str(pdk / "slow.lib")], "nom_typ_*": [str(pdk / "typ.lib")],
                        "nom_fast_*": [str(pdk / "fast.lib")]},
             MACROS={"SRAM": {"lib": {"*_slow_*": [str(pdk / "sram_slow.lib")]}}}, IO_DELAY_CONSTRAINT=20)
    p.write_text(json.dumps(c))
    (wt / "src/protoemu.sdc").write_text("set period $::env(CLOCK_PERIOD)\nset io $::env(IO_DELAY_CONSTRAINT)\n")
    args = complete_receipts(wt)      # the run as hardened (clean tree, receipts) before its re-timing
    nl = module("sta_nonlatch")
    rc = nl.main([str(wt), "--out-parent", str(tmp_path / "out"), *extra])
    outs = sorted((tmp_path / "out").glob("nonlatch.*"))
    return wt, rc, (outs[-1] if outs else None), args


def test_the_report_is_parsed_per_family():
    nl = module("sta_nonlatch")
    assert nl.parse(REPORT) == {"all": 0.0, "latch": 0.0, "latch_borrow": 0.937,
                                "nonlatch": 0.4304, "nonlatch_endpoint": "_53246_/D"}
    with pytest.raises(ValueError):
        nl.parse(REPORT.replace("== END\n", ""))
    with pytest.raises(ValueError):
        nl.parse(REPORT.split("== NONLATCH")[0] + "== NONLATCH 0\n== END\n")     # no non-latch endpoint


def test_a_receipt_binds_the_run_and_release_check_prints_it(tmp_path, fake_sta, capsys):
    wt, rc, out, args = run(tmp_path, "--period", "20.832")
    assert rc == 0 and (out / "receipt.json").is_file()
    calls = [json.loads(line) for line in fake_sta.read_text().splitlines()]
    sta = [c for c in calls if "sta" in c]
    assert len(sta) == 3
    for c in sta:          # read-only, no network, only the period set
        assert "--network" in c and c[c.index("--network") + 1] == "none"
        assert any(x.endswith(":ro") and x.startswith(str(wt)) for x in c)
        assert "CLOCK_PERIOD=20.832" in c and "IO_DELAY_CONSTRAINT=20" in c
    slow = next(c for c in sta if any("slow.lib" in x for x in c))
    assert any(x.startswith("STA_LIBS=") and "slow.lib" in x and "sram_slow.lib" in x for x in slow)
    nl = module("sta_nonlatch")
    data = nl.verify(out / "receipt.json", wt)
    assert data["plan"]["period_ns"] == 20.832 and data["results"]["nom_slow_1p08V_125C"]["nonlatch"] == 0.4304
    capsys.readouterr()
    rel = module("release_check")
    rel.main(args + ["--nonlatch-receipt", str(out / "receipt.json")])
    text = capsys.readouterr().out
    assert "(all endpoints, latches included)" in text
    assert "nom_slow_1p08V_125C: worst non-latch setup slack +0.4304 ns at _53246_/D (20.832 ns" in text
    assert "invalid non-latch STA evidence" not in text
    rel.main(args)
    assert "worst non-latch setup slack: not measured" in capsys.readouterr().out


def test_a_changed_report_or_run_invalidates_the_receipt(tmp_path, fake_sta, capsys):
    wt, rc, out, args = run(tmp_path)
    nl = module("sta_nonlatch")
    assert rc == 0 and nl.verify(out / "receipt.json", wt)["plan"]["period_ns"] == 20
    rpt = out / "nom_slow_1p08V_125C.rpt"
    rpt.chmod(0o644)
    good = rpt.read_text()
    rpt.write_text(good.replace("0.4304 (MET)", "0.9304 (MET)"))
    with pytest.raises(ValueError, match="changed after"):
        nl.verify(out / "receipt.json", wt)
    rpt.write_text(good)
    nlf = wt / "runs/wokwi/final/nl/design.nl.v"
    nlf.write_text(nlf.read_text() + "// edited\n")
    with pytest.raises(ValueError, match="different candidate"):
        nl.verify(out / "receipt.json", wt)
    capsys.readouterr()
    rel = module("release_check")
    assert rel.main(args + ["--nonlatch-receipt", str(out / "receipt.json")]) == 1
    assert "invalid non-latch STA evidence" in capsys.readouterr().out


def test_signoff_and_the_sta_rerun_say_the_slack_includes_latches():
    root = Path(__file__).resolve().parents[2]
    assert "(all endpoints, latches included)" in (root / "flow/signoff.py").read_text()
    assert "setup WS {m['timing__setup__ws']} (all endpoints, latches included)" in \
        (root / "flow/sta_rerun.py").read_text()
