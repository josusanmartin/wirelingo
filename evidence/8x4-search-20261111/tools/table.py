#!/usr/bin/env python3
"""AQ search table from ~/protoemu-flow/aq-search/results/*/ (run on runner1).

    table.py [--md] [NAME ...]

score(): the slow corner's worst flop/SRAM endpoint at 20 ns when the run is a
candidate (signoff passed, 0 slew/cap/fanout at every corner, fast hold >=
HOLD_MIN); 1 ns less per failed criterion
(electrical, fast hold), 100 less for a failed signoff/DRC/LVS. prune.py ranks by it.
"""
import glob
import json
import os
import re
import sys

B = os.environ.get("AQ_RESULTS") or os.path.expanduser("~/protoemu-flow/aq-search/results")
C = ("nom_typ_1p20V_25C", "nom_slow_1p08V_125C", "nom_fast_1p32V_m40C")
HOLD_MIN = 0.045


def load(n):
    d = f"{B}/{n}"
    r = dict(name=n, spec=open(f"{d}/spec.txt").read().strip() if os.path.exists(f"{d}/spec.txt") else "")
    try:
        m = json.load(open(f"{d}/metrics.json"))
    except OSError:
        r["ok"] = False
        return r
    g = lambda k, c: m.get(f"{k}__corner:{c}")
    res = open(f"{d}/result.txt").read() if os.path.exists(f"{d}/result.txt") else ""
    nl = re.search(r"sha256 nl (\w+)", res)
    r.update(ok=True, signoff="SIGNOFF PASSED" in res, nl=nl[1] if nl else "-",
             util=m.get("design__instance__utilization__stdcell"), wire=m.get("route__wirelength"))
    m3 = re.findall(r"Metal3\s+\d+\s+\d+\s+([\d.]+)%\s+\d+ /\s+\d+ /\s+(\d+)", res)
    r["metal3"], r["ovf"] = (m3[-1] if m3 else ("-", "-"))
    r["drc"] = [m.get(k) for k in ("route__drc_errors", "klayout__drc_error__count", "design__lvs_error__count",
                                     "route__antenna_violation__count")]
    r["corners"] = {}
    elec = 0
    for c in C:
        cc = dict(setup=g("timing__setup__ws", c), svio=g("timing__setup_vio__count", c),
                  tns=g("timing__setup__tns", c), hold=g("timing__hold__ws", c), hvio=g("timing__hold_vio__count", c),
                  slew=g("design__max_slew_violation__count", c), cap=g("design__max_cap_violation__count", c),
                  fanout=g("design__max_fanout_violation__count", c))
        elec += sum(int(cc[k] or 0) for k in ("slew", "cap", "fanout"))
        r["corners"][c] = cc
    r["elec"] = elec
    flop = {}
    if os.path.exists(f"{d}/sta20-flop.txt"):
        t = open(f"{d}/sta20-flop.txt").read()
        for sec in t.split("=== ")[1:]:
            corner = sec.split()[0]
            vals = re.findall(r"group clk\n-+\n\S+ \(\S+\)\s+[\d.]+\s+[\d.]+\s+(-?[\d.]+)", sec)
            flop[corner] = float(vals[0]) if vals else None
    r["flop20"] = flop
    s48 = {}
    for f in glob.glob(f"{d}/sta48/sta.*/sta/*/sta.metrics"):
        corner = f.split("/")[-2]
        t = open(f).read()
        ws = re.search(rf"timing__setup__ws__corner:{corner} (\S+)", t)
        vio = re.search(rf"timing__setup_vio__count__corner:{corner} (\S+)", t)
        s48[corner] = (ws and float(ws[1]), vio and int(float(vio[1])))
    r["sta48"] = s48
    r["time"] = open(f"{d}/time.txt").read().strip() if os.path.exists(f"{d}/time.txt") else ""
    return r


def score(r):
    if not r.get("ok"):
        return -1000.0
    f = r["flop20"].get("slow_1p08V_125C")
    if f is None:
        return -1000.0
    fast_hold = r["corners"]["nom_fast_1p32V_m40C"]["hold"] or -1
    if not r["signoff"] or any(r["drc"]):
        return f - 100
    # 1 ns per failed candidate criterion: electrical exceptions, thin fast hold
    return f - (r["elec"] > 0) - (fast_hold < HOLD_MIN)


def names():
    return sorted(n for n in os.listdir(B) if os.path.exists(f"{B}/{n}/done"))


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    for n in args or names():
        r = load(n)
        if not r.get("ok"):
            print(f"== {n}: no metrics; {r['spec']}")
            continue
        print(f"== {n} [{r['spec']}]: signoff {'PASSED' if r['signoff'] else 'FAILED'}, nl {r['nl']}, "
              f"util {r['util']:.4f}, Metal3 {r['metal3']}% ovf {r['ovf']}, wire {r['wire']}, "
              f"drc/klayout/lvs/antenna {r['drc']}, score {score(r):+.3f}")
        for c in C:
            cc = r["corners"][c]
            print(f"   {c}: setup WS {cc['setup']:.3f} vio {cc['svio']} TNS {cc['tns']:.2f}; hold WS {cc['hold']:.3f} "
                  f"vio {cc['hvio']}; slew {cc['slew']} cap {cc['cap']} fanout {cc['fanout']}; "
                  f"worst flop@20 {r['flop20'].get(c.split('_', 1)[1])}; @20.832 {r['sta48'].get(c)}")
        print("   ", r["time"])


if __name__ == "__main__":
    main()
