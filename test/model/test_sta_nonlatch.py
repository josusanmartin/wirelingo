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

HEAD = """== CORNER nom_slow_1p08V_125C
LIB /pdk/slow.lib
LIB /pdk/sram_slow.lib
== CLOCKS
CLOCK clk 20.832000
CLOCK sck 20.832000
"""
REPORT = HEAD + """== ALL
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
"""Fake docker: an image ID (FAKE_IMAGE_ID), the LibreLane version, and for `sta`
a report whose head states the corner, libraries and clock period it was given."""
import json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_DOCKER_LOG"], "a") as f:
    f.write(json.dumps(args) + "\n")
if args[:3] == ["image", "inspect", "--format"]:
    print(os.environ["FAKE_IMAGE_ID"])
    sys.exit(0)
assert args[:2] == ["run", "--rm"] and args[args.index("--network") + 1] == "none"
if "python3" in args:
    print("3.1.0.dev3")
    sys.exit(0)
assert args[-4:-1] == ["sta", "-no_splash", "-exit"]
env = {args[i + 1].split("=", 1)[0]: args[i + 1].split("=", 1)[1] for i, a in enumerate(args) if a == "-e"}
p = float(env["CLOCK_PERIOD"])
head = f"== CORNER {env['STA_CORNER']}\n" + "".join(f"LIB {l}\n" for l in env["STA_LIBS"].split()) + \
    f"== CLOCKS\nCLOCK clk {p:f}\nCLOCK sck {p:f}\n"
body = open(os.environ["FAKE_REPORT"]).read()
sys.stdout.write(head + body[body.index("== ALL"):])
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
    from test_release_evidence import FAKE_IMAGE_ID
    monkeypatch.setenv("FAKE_IMAGE_ID", FAKE_IMAGE_ID)     # the run's own image (its clean-tree receipt)
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
    assert nl.parse(REPORT) == {"corner": "nom_slow_1p08V_125C", "libs": ["/pdk/slow.lib", "/pdk/sram_slow.lib"],
                                "clocks": {"clk": 20.832, "sck": 20.832},
                                "all": 0.0, "latch": 0.0, "latch_borrow": 0.937,
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


def reseal(out, edit):
    """A receipt edited and its report digests kept (what verify's own checks must catch)."""
    p = out / "receipt.json"
    data = json.loads(p.read_text())
    edit(data)
    p.chmod(0o644)
    p.write_text(json.dumps(data))


@pytest.mark.parametrize("tamper,message", [
    ("period", "timed clock clk at 20.832 ns, not 20 ns"),            # 099: 20.832 relabelled 20
    ("image", "made in image sha256:000"),                             # not the run's image
    ("corner", "the run's STA corners"),                               # the fast corner dropped
])
def test_the_receipt_binds_its_period_image_and_corners(tmp_path, fake_sta, capsys, tamper, message):
    wt, rc, out, args = run(tmp_path, "--period", "20.832")
    nl = module("sta_nonlatch")
    assert rc == 0 and nl.verify(out / "receipt.json", wt)["image_checked"]
    fast = "nom_fast_1p32V_m40C"

    def edit(d):
        if tamper == "period":
            d["plan"]["period_ns"] = 20
        elif tamper == "image":
            d["plan"]["image_id"] = "sha256:" + "0" * 64
        else:
            d["plan"]["corners"].remove(fast)
            del d["results"][fast], d["reports"][f"{fast}.rpt"]
    reseal(out, edit)
    with pytest.raises(ValueError, match=message):
        nl.verify(out / "receipt.json", wt)
    capsys.readouterr()
    module("release_check").main(args + ["--nonlatch-receipt", str(out / "receipt.json")])
    text = capsys.readouterr().out
    assert "invalid non-latch STA evidence" in text and "worst non-latch setup slack +0.4304" not in text


def test_a_report_for_another_corner_or_library_is_refused(tmp_path, fake_sta):
    wt, rc, out, args = run(tmp_path, "--period", "20.832")
    nl = module("sta_nonlatch")
    rpt = out / "nom_fast_1p32V_m40C.rpt"
    text = rpt.read_text().replace("== CORNER nom_fast_1p32V_m40C", "== CORNER nom_slow_1p08V_125C")
    rpt.chmod(0o644)
    rpt.write_text(text)

    def edit(d):
        d["reports"]["nom_fast_1p32V_m40C.rpt"] = module("release_receipts").digest(rpt)
        d["results"]["nom_fast_1p32V_m40C"] = nl.parse(text)
    reseal(out, edit)
    with pytest.raises(ValueError, match="was made for corner nom_slow_1p08V_125C"):
        nl.verify(out / "receipt.json", wt)


def test_clock_gate_enables_are_non_latch_endpoints():
    # the endpoint list (the concat the checks run to) holds the clock-gate inputs, which
    # come from every cell the ICG patterns name, except its clock pin, and those patterns
    # match the PDK's two integrated clock-gating cells
    import fnmatch
    import re
    tcl = (Path(__file__).resolve().parents[2] / "flow/sta_nonlatch.tcl").read_text()
    ends = re.search(r"^set ends \[concat (.*)\]$", tcl, re.M).group(1).split()
    assert "$icg_in" in ends and "$macro_in" in ends
    loop = tcl[tcl.index("set icg_in {}"):tcl.index("set ends")]
    assert "foreach pat $::env(STA_ICG)" in loop and "lappend icg_in $p" in loop and '"*/CLK"' in loop
    nl = module("sta_nonlatch")
    assert all(any(fnmatch.fnmatch(c, p) for p in nl.ICG.split()) for c in ("sg13cmos5l_lgcp_1", "sg13cmos5l_slgcp_1"))
    assert not fnmatch.fnmatch("sg13cmos5l_dfrbpq_1", nl.ICG)


def test_a_report_with_other_libraries_is_refused(tmp_path, fake_sta):
    # item 100 finding 6 (nl-libs-unchecked): the right corner, other Liberty files
    wt, rc, out, args = run(tmp_path, "--period", "20.832")
    nl = module("sta_nonlatch")
    rpt = out / "nom_slow_1p08V_125C.rpt"
    text = rpt.read_text().replace("slow.lib", "typ.lib", 1)
    rpt.write_text(text)

    def edit(d):
        d["reports"]["nom_slow_1p08V_125C.rpt"] = module("release_receipts").digest(rpt)
        d["results"]["nom_slow_1p08V_125C"] = nl.parse(text)
    reseal(out, edit)
    with pytest.raises(ValueError, match="was made for corner nom_slow_1p08V_125C with"):
        nl.verify(out / "receipt.json", wt)


# A stand-in for OpenSTA's commands over a tiny netlist, to run sta_nonlatch.tcl's own
# endpoint selection (Tcl via tkinter): two flops, a latch, a clock gate (GATE, CLK in;
# GCLK out), an SRAM (two inputs, one output) and an output port
STUB_STA = r"""
array set stub_dir {f1/D input f2/D input l1/D input cg/GATE input cg/CLK input cg/GCLK output
               m/A_ADDR[0] input m/A_CLK input m/A_DOUT[0] output}
array set stub_ref {f1 sg13cmos5l_dfrbpq_1 f2 sg13cmos5l_dfrbpq_1 l1 sg13cmos5l_dlhq_1 cg sg13cmos5l_lgcp_1 m SRAM}
array set stub_pins {cg {cg/GATE cg/CLK cg/GCLK} m {m/A_ADDR[0] m/A_CLK m/A_DOUT[0]}}
foreach c {read_liberty read_verilog link_design read_spef read_sdc report_worst_slack} { proc $c args {} }
proc puts args {}
proc all_clocks {} { return {clk} }
proc get_name {o} { return $o }
proc get_full_name {o} { return $o }
proc get_property {o p} {
  global stub_dir
  if {$p eq "period"} { return 20.0 }
  return $stub_dir($o)
}
proc all_registers {kind what} { if {$kind eq "-level_sensitive"} { return {l1/D} } { return {f1/D f2/D} } }
proc all_outputs {} { return {uo_out[0]} }
proc get_cells {f expr all} {
  global stub_ref
  regexp {^ref_name (==|=~) (.*)$} $expr -> op pat
  set out {}
  foreach c [lsort [array names stub_ref]] {
    if {($op eq "==" && $stub_ref($c) eq $pat) || ($op eq "=~" && [string match $pat $stub_ref($c)])} { lappend out $c }
  }
  return $out
}
proc get_pins {o c} { global stub_pins; return $stub_pins($c) }
proc report_checks args {
  global checked
  set i [lsearch $args -to]
  lappend checked [lindex $args [expr {$i + 1}]]
}
"""


def test_the_non_latch_endpoints_are_flops_clock_gate_enables_macro_inputs_and_outputs():
    # item 100c finding 2: run the selection itself, not its text
    import tkinter
    t = tkinter.Tcl()
    for k, v in dict(STA_CORNER="c", STA_LIBS="a.lib", STA_NETLIST="n", STA_TOP="t", STA_SPEF="s", STA_SDC="d",
                     STA_MACROS="SRAM", STA_ICG=module("sta_nonlatch").ICG).items():
        t.setvar(f"env({k})", v)
    t.eval(STUB_STA)
    t.eval("set checked {}")
    t.eval(f"source {{{Path(__file__).resolve().parents[2] / 'flow/sta_nonlatch.tcl'}}}")
    latch, ends = (t.splitlist(x) for x in t.splitlist(t.getvar("checked")))
    assert list(latch) == ["l1/D"]
    assert sorted(ends) == sorted(["f1/D", "f2/D", "cg/GATE", "m/A_ADDR[0]", "m/A_CLK", "uo_out[0]"])
