"""The signoff SDC's host-port contract (src/protoemu.sdc, docs/isa.md §6.1).

SPI mode 0: the host changes MOSI/IO on SCK falls and samples MISO/IO on SCK
rises; the chip samples on rises and changes its outputs on falls. The SDC is
evaluated in a real Tcl interpreter (Python's tkinter) with the timing
commands stubbed, so these tests pin the edges and the delay arithmetic, not
any STA result.
"""
import json
import os
import pathlib
import subprocess
import sys

import pytest

pytest.importorskip("tkinter")

ROOT = pathlib.Path(__file__).resolve().parents[2]
SDC = ROOT / "src/protoemu.sdc"
HOST_IO = ["ui_in[5]", "ui_in[4]", "ui_in[3]", "ui_in[2]"]
HOST_OUT = ["uo_out[7]", "uo_out[6]", "uo_out[5]", "uo_out[4]"]
INPUTS = ["clk", "rst_n", "ena"] + [f"ui_in[{i}]" for i in range(8)] + [f"uio_in[{i}]" for i in range(8)]
OUTPUTS = [f"uo_out[{i}]" for i in range(8)] + [f"uio_out[{i}]" for i in range(8)] + [f"uio_oe[{i}]" for i in range(8)]
FLOW_ENV = {"CLOCK_PERIOD": "20", "IO_DELAY_CONSTRAINT": "20", "MAX_FANOUT_CONSTRAINT": "8",
            "SYNTH_DRIVING_CELL": "sg13cmos5l_buf_4/X", "OUTPUT_CAP_LOAD": "6",
            "CLOCK_UNCERTAINTY_CONSTRAINT": "0.25", "CLOCK_TRANSITION_CONSTRAINT": "0.15",
            "TIME_DERATING_CONSTRAINT": "5"}

# Stubs: get_ports/all_inputs/all_outputs return port names; every timing
# command appends {cmd, args} to the global list `calls`.
STUBS = r"""
set calls {}
proc rec {args} { global calls; lappend calls $args }
proc get_ports {pats} { return $pats }
proc get_clocks {name} { return $name }
proc all_clocks {} { return {clk sck} }
proc current_design {} { return design }
proc get_lib_cells {p} { return $p }
foreach c {create_clock set_max_delay set_false_path set_input_delay set_output_delay
           set_max_fanout set_driving_cell set_load set_clock_uncertainty
           set_clock_transition set_timing_derate set_propagated_clock set_disable_timing} {
  proc $c {args} "rec $c {*}\$args"
}
"""

RUNNER = r"""
import json, sys, tkinter
t = tkinter.Tcl()
t.eval(sys.argv[1])
t.eval("proc all_inputs {} {return {%s}}" % " ".join("{%s}" % p for p in json.loads(sys.argv[3])))
t.eval("proc all_outputs {} {return {%s}}" % " ".join("{%s}" % p for p in json.loads(sys.argv[4])))
try:
    t.eval("source {%s}" % sys.argv[2])
except tkinter.TclError as e:
    print(json.dumps({"error": str(e)}))
    sys.exit(0)
calls = [list(t.splitlist(c)) for c in t.splitlist(t.eval("set calls"))]
print(json.dumps({"calls": [[c[0]] + [list(t.splitlist(a)) if " " in a else a for a in c[1:]] for c in calls]}))
"""


def evaluate(**env):
    e = {k: v for k, v in os.environ.items() if not k.startswith("PROTOEMU_")}
    e.update(FLOW_ENV)
    e.update({k: str(v) for k, v in env.items()})
    out = subprocess.run([sys.executable, "-c", RUNNER, STUBS, str(SDC), json.dumps(INPUTS),
                          json.dumps(OUTPUTS)], env=e, capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def parse(call):
    """set_input_delay/set_output_delay -> (clock, fall, minmax, add, value, ports)."""
    args, opts = call[1:], {"clock": None, "fall": False, "mm": "both", "add": False}
    rest = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "-clock":
            opts["clock"] = args[i + 1]
            i += 2
            continue
        if a == "-clock_fall":
            opts["fall"] = True
        elif a in ("-max", "-min"):
            opts["mm"] = a[1:]
        elif a == "-add_delay":
            opts["add"] = True
        else:
            rest.append(a)
        i += 1
    value, ports = rest
    ports = ports if isinstance(ports, list) else [ports]
    return dict(opts, value=float(value), ports=ports)


def delays(result, cmd):
    return [parse(c) for c in result["calls"] if c[0] == cmd]


def on(ds, port, clock):
    return {(d["fall"], d["mm"]): d["value"] for d in ds if port in d["ports"] and d["clock"] == clock}


def clocks(result):
    return {c[c.index("-name") + 1]: (float(c[c.index("-period") + 1]), c[-1])
            for c in result["calls"] if c[0] == "create_clock"}


def test_default_contract_edges_and_values():
    r = evaluate()
    assert clocks(r) == {"clk": (20.0, "clk"), "sck": (20.0, "ui_in[6]")}
    ins, outs = delays(r, "set_input_delay"), delays(r, "set_output_delay")
    # MOSI/IO: launched on the SCK fall, +-1 ns host skew; nothing on the rise
    for p in HOST_IO:
        assert on(ins, p, "sck") == {(True, "max"): 1.0, (True, "min"): -1.0}
    # CS_n: >= 1 clk before the next rise, >= 1 clk after the last fall,
    # both against the fall: max = T_sck/2 - T_clk + skew, min = T_clk - skew
    assert on(ins, "ui_in[7]", "sck") == {(True, "max"): -9.0, (True, "min"): 19.0}
    # MISO/IO out: captured by the host's SCK rise; 2 x 2 ns flight + 1 ns
    # setup, and for hold the same flights less 1 ns host hold
    for p in HOST_OUT:
        assert on(outs, p, "sck") == {(False, "max"): 5.0, (False, "min"): 3.0}
    # SCK itself and the non-host lines carry no SCK-referenced delay
    for p in set(INPUTS) - set(HOST_IO) - {"ui_in[7]"}:
        assert on(ins, p, "sck") == {}
    for p in set(OUTPUTS) - set(HOST_OUT):
        assert on(outs, p, "sck") == {}


def test_clk_io_delays_and_cdc_scheme_unchanged():
    r = evaluate()
    ins, outs = delays(r, "set_input_delay"), delays(r, "set_output_delay")
    for p in set(INPUTS) - {"clk", "ui_in[6]"}:
        assert on(ins, p, "clk") == {(False, "both"): 4.0}
        assert [d["add"] for d in ins if p in d["ports"] and d["clock"] == "clk"] == [True]
    for p in OUTPUTS:
        assert on(outs, p, "clk") == {(False, "both"): 4.0}
    assert on(ins, "clk", "clk") == {} and on(ins, "ui_in[6]", "clk") == {}
    cmds = [c for c in r["calls"] if c[0] in ("set_max_delay", "set_false_path", "set_clock_uncertainty")]
    assert ["set_max_delay", "-ignore_clock_latency", "20", "-from", "sck", "-to", "clk"] in cmds
    assert ["set_max_delay", "-ignore_clock_latency", "20", "-from", "clk", "-to", "sck"] in cmds
    assert ["set_false_path", "-hold", "-from", "sck", "-to", "clk"] in cmds
    assert ["set_false_path", "-hold", "-from", "clk", "-to", "sck"] in cmds
    assert ["set_clock_uncertainty", "-hold", "0.1", ["clk", "sck"]] in cmds
    assert ["set_clock_uncertainty", "-setup", "0.25", ["clk", "sck"]] in cmds


def test_overrides_scale_the_budgets():
    r = evaluate(PROTOEMU_SCK_PERIOD=40, PROTOEMU_HOST_BOARD_NS=5, PROTOEMU_HOST_SKEW_NS=0.5)
    assert clocks(r) == {"clk": (20.0, "clk"), "sck": (40.0, "ui_in[6]")}
    ins, outs = delays(r, "set_input_delay"), delays(r, "set_output_delay")
    assert on(ins, "ui_in[5]", "sck") == {(True, "max"): 0.5, (True, "min"): -0.5}
    assert on(ins, "ui_in[7]", "sck") == {(True, "max"): 0.5, (True, "min"): 19.5}
    assert on(outs, "uo_out[7]", "sck") == {(False, "max"): 10.5, (False, "min"): 9.5}
    # the cross-clock bound stays one clk period
    assert ["set_max_delay", "-ignore_clock_latency", "20", "-from", "sck", "-to", "clk"] in r["calls"]


@pytest.mark.parametrize("t_sck", [20, 20.832, 25, 40, 100])
def test_budgets_match_the_mode0_timeline(t_sck):
    """From the delays back to time between each event and its sampling edge."""
    t_clk, skew, board = 20.0, 1.0, 2.0
    r = evaluate(PROTOEMU_SCK_PERIOD=t_sck)
    ins, outs = delays(r, "set_input_delay"), delays(r, "set_output_delay")
    io, cs, mo = on(ins, "ui_in[5]", "sck"), on(ins, "ui_in[7]", "sck"), on(outs, "uo_out[7]", "sck")
    half = t_sck / 2
    # MOSI changes at fall + max, sampled at the next rise half a period later
    assert half - io[(True, "max")] == pytest.approx(half - skew)
    # and is held from the rise before the fall until fall + min
    assert half + io[(True, "min")] == pytest.approx(half - skew)
    # CS_n to the next SCK rise, and to the next SCK fall: >= 1 clk (+ half)
    assert half - cs[(True, "max")] == pytest.approx(t_clk - skew)
    assert t_sck - cs[(True, "max")] == pytest.approx(t_clk + half - skew)
    # last SCK fall to CS_n change
    assert cs[(True, "min")] == pytest.approx(t_clk - skew)
    # MISO: half a period from the chip's fall, less both flights and setup
    assert half - mo[(False, "max")] == pytest.approx(half - 2 * board - skew)
    # the host's hold after its rise: both flights less its hold
    assert mo[(False, "min")] == pytest.approx(2 * board - skew)


def test_sck_faster_than_clk_is_refused():
    assert "below CLOCK_PERIOD" in evaluate(PROTOEMU_SCK_PERIOD=19.9)["error"]
