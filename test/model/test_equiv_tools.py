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


def _module(ports, cells):
    return {"ports": {p: {"direction": d, "bits": b} for p, (d, b) in ports.items()}, "cells": cells, "netnames": {}}


def test_pair_breaks_an_fsm_bit_tie_by_the_compared_outputs(tmp_path):
    # Two FSM-encoded bits whose next states the patterns never tell apart (both
    # 0, and the inverted gate flop's 1) are both hypotheses for it; only fsm#0 at polarity 1
    # makes the compared output D:x agree, so that pair is proposed and logged,
    # never the other (the equivalence run then proves or refutes it)
    import subprocess
    gold = _module({"S": ("input", [2]), "clk": ("input", [3]), "rst": ("input", [4]), "V:f": ("input", [5]),
                    "UQ:fsm:r#0": ("output", [2]), "UD:fsm:r#0": ("output", ["0"]),
                    "UC:fsm:r#0": ("output", [3]), "UR:fsm:r#0": ("output", [4]),
                    "UQ:fsm:r#1": ("output", ["0"]), "UD:fsm:r#1": ("output", ["0"]),
                    "UC:fsm:r#1": ("output", [3]), "UR:fsm:r#1": ("output", [4]),
                    "D:x": ("output", [2])}, {})
    gate = _module({"S": ("input", [2]), "clk": ("input", [3]), "rst": ("input", [4]), "V:f": ("input", [5]),
                    "VD:f": ("output", ["1"]), "VC:f": ("output", [3]), "VR:f": ("output", [4]),
                    "D:x": ("output", [6])}, {"n": {"type": "$_NOT_", "connections": {"A": [5], "Y": [6]}}})
    (tmp_path / "gold.json").write_text(json.dumps({"modules": {"gold": gold}}))
    (tmp_path / "gate.json").write_text(json.dumps({"modules": {"gate": gate}}))
    r = subprocess.run([sys.executable, str(ROOT / "formal/equiv/pair.py"), "--gold", str(tmp_path / "gold.json"),
                        "--gate", str(tmp_path / "gate.json"), "--out", str(tmp_path / "pairs.json")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    res = json.loads((tmp_path / "pairs.json").read_text())
    assert [(p["gate"], p["gold"], p["pol"]) for p in res["pairs"]] == [("f", "fsm:r#0", 1)]
    assert "by compared outputs: 0 mismatching bits" in res["pairs"][0]["found"]
    assert res["unpaired_gold"] == ["fsm:r#1"]



def test_a_proof_only_pairing_mutant_counts_only_the_proof(tmp_path, monkeypatch):
    # 099 finding 3 / item 100 finding 6: m8 (the pair moved to another bit at the
    # polarity its reset needs) has no polarity conflict by construction, so only
    # ABC can catch it. Here the cut stub reports a polarity conflict for every
    # mutant and ABC never finds a difference: m5 and m6 count as caught, m8 must not.
    import equiv
    out = tmp_path / "out"
    out.mkdir()
    (out / "pairs.json").write_text(json.dumps({"pairs": [{"gate": "_g_", "gold": "fsm:r#0", "pol": 1}]}))
    m = {"flops": {"fsm:r#0": {"gold": "fsm:r#0", "gate": "_g_", "pol": 1, "how": "simulation-pair"}},
         "fsms": [{"reg": "r", "width": 3, "reset": 0, "table": {"0": "---1", "1": "--1-", "2": "-1--", "3": "1---"}}]}
    monkeypatch.setattr(equiv, "cut", lambda *a, **k: {"polarity_conflicts": [{"gold": "x"}]})
    monkeypatch.setattr(equiv, "abc_check", lambda *a, **k: "equivalent")
    monkeypatch.setattr(equiv, "_gate", lambda *a, **k: None)
    monkeypatch.setattr(equiv, "log", lambda *a, **k: None)
    res = {r["mutant"]: r for r in equiv.pairing_mutants({}, str(out), m, 5, "")}
    assert res[5]["caught"] and res[6]["caught"]          # the polarity rule counts for these
    assert res[8]["caught"] is False and "at polarity 0" in res[8]["description"]
