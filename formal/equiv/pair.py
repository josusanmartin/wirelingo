#!/usr/bin/env python3
"""Propose pairs for the flops that name matching left unmatched.

Reads the --pairing cut modules (techmapped to fine gates) and simulates
them on random patterns. Shared inputs get the same random values in both
designs; an unmatched gold flop's state (U:) and an unmatched gate flop's
state (V:) start independent. A gold candidate G and gate flop F pair with
polarity p when F's clock, reset and next state equal G's (next state XOR p)
on every pattern. Pairs are accepted when unique on both sides; the gate
flop's state input is then tied to G's state (XOR p) and the rest re-run,
so pairs whose next state depends on other unmatched flops resolve in later
rounds. When no unique pair is left, each remaining candidate (F, G, p) is
tried as a hypothesis (F's state tied to G's) and kept if F's next state
then agrees with G's and no other hypothesis for F or G does.

This only proposes; the equivalence run (equiv_simple, ABC) proves or
refutes every pair.

SPDX-License-Identifier: Apache-2.0
"""
import argparse
import json
import random

from sim import Module


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", required=True)
    ap.add_argument("--gate", required=True)
    ap.add_argument("--aliases", help="aliases.json from cut.py --pairing")
    ap.add_argument("--out", required=True)
    ap.add_argument("--patterns", type=int, default=256)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()

    gold, gate = Module(a.gold, "gold"), Module(a.gate, "gate")
    rng = random.Random(a.seed)
    W = a.patterns
    mask = (1 << W) - 1
    ins = {}
    for p, bits in list(gold.inputs.items()) + list(gate.inputs.items()):
        if p not in ins:
            ins[p] = [rng.getrandbits(W) for _ in bits]
    gout = gold.run(ins, mask)
    G = sorted(p[3:] for p in gout if p.startswith("UQ:"))
    F = sorted(p[2:] for p in gate.inputs if p.startswith("V:"))
    sig_g = {g: (gout["UC:" + g][0], gout["UR:" + g][0], gout["UD:" + g][0], gout["UQ:" + g][0]) for g in G}

    pairs = {}              # gate flop -> (gold, pol, round)

    def cands(nout, f, pool):
        c, r, d = nout["VC:" + f][0], nout["VR:" + f][0], nout["VD:" + f][0]
        out = []
        for g in pool:
            gc, gr, gd, _ = sig_g[g]
            if gc == c and gr == r:
                for p in (0, 1):
                    if d == (gd ^ (mask if p else 0)):
                        out.append((g, p))
        return out

    def tie(f, g, p, inputs):
        inputs["V:" + f] = [sig_g[g][3] ^ (mask if p else 0)]

    # alias pairs: the gate flop's output carries the name of a gold signal
    # that equals one unmatched gold flop's state (XOR p) on every pattern
    if a.aliases:
        nout = gate.run(ins, mask)
        taken = set()
        for f, names in sorted(json.load(open(a.aliases)).items()):
            hits = set()
            for n, inv in names:
                v = gout["UN:" + n][0] ^ (mask if inv else 0)
                for g in G:
                    if g in taken:
                        continue
                    for p in (0, 1):
                        if sig_g[g][3] ^ (mask if p else 0) == v and \
                                sig_g[g][:2] == (nout["VC:" + f][0], nout["VR:" + f][0]):
                            hits.add((g, p))
            if len(hits) == 1:
                g, p = hits.pop()
                pairs[f] = (g, p, "alias " + ",".join(n for n, _ in names))
                taken.add(g)
                tie(f, g, p, ins)

    rnd = 0
    while True:
        rnd += 1
        nout = gate.run(ins, mask)
        free_g = [g for g in G if g not in {v[0] for v in pairs.values()}]
        free_f = [f for f in F if f not in pairs]
        cf = {f: cands(nout, f, free_g) for f in free_f}
        cg = {}
        for f, cs in cf.items():
            for g, p in cs:
                cg.setdefault(g, []).append(f)
        new = 0
        for f, cs in cf.items():
            if len(cs) == 1 and len(cg[cs[0][0]]) == 1:
                g, p = cs[0]
                pairs[f] = (g, p, f"round {rnd}")
                tie(f, g, p, ins)
                new += 1
        if new:
            continue
        # hypotheses: tie one gate flop to one gold candidate at a time
        hyp = {}
        for f in free_f:
            c = nout["VC:" + f][0], nout["VR:" + f][0]
            for g in free_g:
                if sig_g[g][:2] != c:
                    continue
                for p in (0, 1):
                    trial = dict(ins)
                    tie(f, g, p, trial)
                    if gate.run(trial, mask)["VD:" + f][0] == sig_g[g][2] ^ (mask if p else 0):
                        hyp.setdefault(f, []).append((g, p))
        hg = {}
        for f, hs in hyp.items():
            for g, p in hs:
                hg.setdefault(g, []).append(f)
        for f, hs in hyp.items():
            if len(hs) == 1 and len(hg[hs[0][0]]) == 1:
                g, p = hs[0]
                pairs[f] = (g, p, f"hypothesis round {rnd}")
                tie(f, g, p, ins)
                new += 1
        if not new:
            break

    res = {"pairs": [{"gate": f, "gold": g, "pol": p, "found": how} for f, (g, p, how) in sorted(pairs.items())],
           "unpaired_gate": [f for f in F if f not in pairs],
           "unpaired_gold": [g for g in G if g not in {v[0] for v in pairs.values()}],
           "patterns": W, "seed": a.seed}
    json.dump(res, open(a.out, "w"), indent=1)
    print(f"pair.py: {len(pairs)} pairs; unpaired gate {len(res['unpaired_gate'])}, gold {len(res['unpaired_gold'])}")
    for p in res["pairs"]:
        print(f"    {p['gate']} <- {p['gold']} pol {p['pol']} ({p['found']})")
    for f in res["unpaired_gate"]:
        print(f"    unpaired gate {f}")
    for g in res["unpaired_gold"]:
        print(f"    unpaired gold {g}")


if __name__ == "__main__":
    main()
