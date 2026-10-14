#!/usr/bin/env python3
"""Bit-parallel random simulation of a combinational yosys JSON module.

Used only to *propose* correspondences (pair.py) and to replay
counterexamples; never as evidence of equivalence. Every signal is a Python
int holding one bit per pattern. Supports the fine-grained gate cells that
`techmap` produces ($_AND_, $_MUX_, ...); x and z constants evaluate to 0.

SPDX-License-Identifier: Apache-2.0
"""
import json
import sys

ONE_IN = {"$_NOT_": lambda a, m: ~a & m, "$_BUF_": lambda a, m: a}
TWO_IN = {
    "$_AND_": lambda a, b, m: a & b,
    "$_OR_": lambda a, b, m: a | b,
    "$_XOR_": lambda a, b, m: a ^ b,
    "$_NAND_": lambda a, b, m: ~(a & b) & m,
    "$_NOR_": lambda a, b, m: ~(a | b) & m,
    "$_XNOR_": lambda a, b, m: ~(a ^ b) & m,
    "$_ANDNOT_": lambda a, b, m: a & ~b & m,
    "$_ORNOT_": lambda a, b, m: (a | ~b) & m,
}


class Module:
    def __init__(self, path, name):
        mod = json.load(open(path))["modules"][name]
        self.inputs = {p: v["bits"] for p, v in mod["ports"].items() if v["direction"] == "input"}
        self.outputs = {p: v["bits"] for p, v in mod["ports"].items() if v["direction"] == "output"}
        self.driver = {}
        self.cells = []
        for cname, c in mod["cells"].items():
            t = c["type"]
            if t not in ONE_IN and t not in TWO_IN and t != "$_MUX_":
                sys.exit(f"sim.py: {name}: unsupported cell {cname} ({t})")
            y = c["connections"]["Y"][0]
            self.driver[y] = len(self.cells)
            self.cells.append((t, c["connections"]))
        self.order = self._order()

    def _order(self):
        order, state = [], {}
        for root in range(len(self.cells)):
            if root in state:
                continue
            stack = [(root, False)]
            while stack:
                i, done = stack.pop()
                if done:
                    state[i] = 2
                    order.append(i)
                    continue
                if state.get(i) == 2:
                    continue
                if state.get(i) == 1:
                    continue
                state[i] = 1
                stack.append((i, True))
                t, conn = self.cells[i]
                for p, bits in conn.items():
                    if p == "Y":
                        continue
                    for b in bits:
                        j = self.driver.get(b)
                        if j is not None and state.get(j) != 2:
                            if state.get(j) == 1:
                                sys.exit("sim.py: combinational loop")
                            stack.append((j, False))
        return order

    def run(self, inputs, mask):
        """inputs: {port: [int per bit]} -> {port: [int per bit]}"""
        val = {}
        for p, bits in self.inputs.items():
            vs = inputs[p]
            for b, v in zip(bits, vs):
                if not isinstance(b, str):
                    val[b] = v

        def get(b):
            if isinstance(b, str):
                return mask if b == "1" else 0
            return val.get(b, 0)

        for i in self.order:
            t, conn = self.cells[i]
            if t in TWO_IN:
                v = TWO_IN[t](get(conn["A"][0]), get(conn["B"][0]), mask)
            elif t in ONE_IN:
                v = ONE_IN[t](get(conn["A"][0]), mask)
            else:
                s = get(conn["S"][0])
                v = (get(conn["A"][0]) & ~s | get(conn["B"][0]) & s) & mask
            val[conn["Y"][0]] = v
        return {p: [get(b) for b in bits] for p, bits in self.outputs.items()}
