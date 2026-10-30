"""Pure-Python parts of the RTL-vs-netlist equivalence check (formal/equiv).

The check itself needs a hardening run and runs on runner1
(docs/equivalence.md); these tests cover the pieces whose mistakes would
silently change what it proves: the UDP translation of cell models, the FSM
recoding parser and the random simulator that proposes flop pairs.
"""
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "formal/equiv"))

import cells  # noqa: E402
import cut  # noqa: E402
import sim  # noqa: E402

MUX4 = """
primitive ihp_mux4 (z, a, b , c, d, s0, s1);
	output z;
	input d, c, b, a, s1, s0;
	table
//                a  b  c  d s0 s1 : z
                  0  ?  ?  ?  0  0 : 0;
                  1  ?  ?  ?  0  0 : 1;
                  ?  0  ?  ?  1  0 : 0;
                  ?  1  ?  ?  1  0 : 1;
                  ?  ?  0  ?  0  1 : 0;
                  ?  ?  1  ?  0  1 : 1;
                  ?  ?  ?  0  1  1 : 0;
                  ?  ?  ?  1  1  1 : 1;
                  0  0  ?  ?  x  0 : 0;
                  1  1  1  1  x  x : 1;
	endtable
endprimitive
"""


def test_udp_translation_follows_the_header_order_not_the_declarations():
    ports, out, rows, seq = cells.parse_udps(cells.strip_comments(MUX4))["ihp_mux4"]
    assert not seq and ports[0] == out == "z"
    ins, truth = cells.udp_truth("ihp_mux4", ports, out, rows)
    assert ins == ["a", "b", "c", "d", "s0", "s1"]
    for vals, z in truth.items():
        a, b, c, d, s0, s1 = (int(v) for v in vals)
        assert int(z) == [a, b, c, d][s0 + 2 * s1]


def test_udp_translation_rejects_incomplete_or_conflicting_tables():
    hole = MUX4.replace("                  ?  ?  ?  1  1  1 : 1;\n", "")
    with pytest.raises(SystemExit):
        cells.udp_truth("ihp_mux4", *cells.parse_udps(cells.strip_comments(hole))["ihp_mux4"][:3])
    clash = MUX4.replace("1  1  1  1  x  x : 1;", "1  1  1  1  0  0 : 0;")
    with pytest.raises(SystemExit):
        cells.udp_truth("ihp_mux4", *cells.parse_udps(cells.strip_comments(clash))["ihp_mux4"][:3])


def test_banner_comments_do_not_swallow_modules():
    text = ("//******\n//   module name : x\n//******\nmodule a (Y, A);\n output Y; input A;\n not (Y, A);\n"
            "endmodule\n//******\nmodule b (X, A);\n output X; input A;\n buf (X, A);\nendmodule\n")
    assert [n for n, _ in cells.split_modules(cells.strip_comments(text))] == ["a", "b"]


FSM_LOG = r"""
Extracting FSM `\u_host.phase' from module `\tt_um_josusanmartin_protoemu'.
  found $adff cell for state register: $flatten\u_host.$procdff$14494
  found reset state: 3'000 (from async reset)
  found state code: 3'011
Recoding FSM `$fsm$\u_host.phase$14671' from module `\tt_um_josusanmartin_protoemu' using `auto' encoding:
  mapping auto encoding to `one-hot` for this FSM.
  000 -> ----1
  100 -> ---1-
  010 -> --1--
  001 -> -1---
  011 -> 1----

56.7. Executing FSM_INFO pass (dumping all available information on FSM cells).
"""


def test_fsm_recoding_table_is_parsed_msb_first():
    (f,) = cut.parse_fsms(FSM_LOG)
    assert f["reg"] == "u_host.phase" and f["width"] == 3 and f["reset"] == 0
    assert f["table"] == {0: "----1", 4: "---1-", 2: "--1--", 1: "-1---", 3: "1----"}


def test_fsm_extracted_but_not_recoded_is_an_error():
    with pytest.raises(SystemExit):
        cut.parse_fsms(FSM_LOG.split("Recoding")[0])


def test_simulator_evaluates_fine_gates_bit_parallel(tmp_path):
    mod = {"ports": {"a": {"direction": "input", "bits": [2]}, "b": {"direction": "input", "bits": [3]},
                     "s": {"direction": "input", "bits": [4]}, "y": {"direction": "output", "bits": [7]}},
           "cells": {"n": {"type": "$_NOT_", "connections": {"A": [2], "Y": [5]}},
                     "x": {"type": "$_XOR_", "connections": {"A": [5], "B": [3], "Y": [6]}},
                     "m": {"type": "$_MUX_", "connections": {"A": [6], "B": ["1"], "S": [4], "Y": [7]}}},
           "netnames": {}}
    p = tmp_path / "m.json"
    p.write_text(json.dumps({"modules": {"m": mod}}))
    m = sim.Module(str(p), "m")
    a, b, s = 0b1100, 0b1010, 0b0110
    y = m.run({"a": [a], "b": [b], "s": [s]}, 0b1111)["y"][0]
    assert y == ((((~a & 0xF) ^ b) & ~s) | s) & 0xF
