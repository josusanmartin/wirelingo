#!/usr/bin/env python3
"""The second round of post-freeze firmwares (docs/post-freeze-protocols.md:
dali_ctrl, modbus_rtu, midi_rx, midi_tx, quad_enc) on the KV260 build of the
chip RTL. Run as root on the Kria with the protoemu app (pe_lab) loaded.
Nothing leaves the FPGA: every line is a virtual pin or a uio pad that a core
or pe_lab's fabric drives; no PMOD pin is enabled.

    sudo python3 -u fpga/kv260/host/postfreeze2_demo.py [dali modbus midi quad] [--save pf2.json] [--plans plans.json]
    python3 fpga/kv260/host/postfreeze2_demo.py --model [groups] [--save m2.json] [--plans plans.json]
    python3 fpga/kv260/host/postfreeze2_demo.py compare pf2.json m2.json     # off the board
    python3 fpga/kv260/host/postfreeze2_demo.py sigrok pf2.json              # off the board

Lines (uio pads; uio7 is the pattern generator's start trigger):
* DALI: the bus is uio0. dali_ctrl (core 0) drives it open drain and reads it
  (TX = RX = uio0); the generator is the rest of the bus: released high, the
  control gear's backward frames, another controller's frame. An open-drain
  pin that is released reads what the fabric drives (kv260_top: uio_in =
  uio_oe ? uio_out : generator), so the pad is the wired-AND of both.
* Modbus RTU: the RS-485 line is uio0 (TX open drain, RX), DE is uio2. The
  server's response comes from the generator on the same wired-AND line, so
  the client hears its own request too, as on a half-duplex bus.
* MIDI: midi_tx on core 0, midi_rx on core 1 (both cores: a full port).
  TX to virtual pin V0 for la_capture and the loopback, to uio1 for the
  full port (read by the host from the GPIO); RX from uio0 (the generator).
* Quadrature: A = uio0, B = uio1 from the generator (on protoemu's clock, or
  on the 45.93 MHz alternate clock for steps asynchronous to the core).

Far ends that answer (DALI gear, Modbus server) are pre-timed: each scenario
first runs on the cycle model with the pins-only model of the far end in the
loop on the model's pad (pe.postfreeze2.DaliGear, ModbusServer, as in
test/model/test_dali.py and test_modbus.py); the far end's own waveform from
that run (its exact edges, relative to the trigger) is then what the
generator plays. The firmware's start on the board differs from the model's
by under a tick, so the answers land within a tick of the model's times; the
captures and the firmware's records say where they landed. Transmitters are
recorded by la_capture on core 1 (1 tick = 1 clock) and drained by one
READ_FIXED burst (postfreeze_demo.capture); receivers' records are polled.
Everything is judged by the models and decoders the model tests use
(pe.postfreeze2) and, off the board, sigrok.

--model runs the same code on the cycle model at a slower clock where the
firmware's ticks allow (it checks this script, not the FPGA). --plans keeps
the pre-timing runs in a file (keyed by everything they depend on), so the
Kria can reuse plans computed on a faster machine. --save keeps captures and
results; `compare` lists which runs' deterministic results (values, flags,
waveforms in ticks) are equal in two files; `sigrok` decodes saved captures.
"""
import argparse
import hashlib
import json
import math
import pathlib
import random
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import postfreeze_demo as pfd  # noqa: E402  (sets up sys.path for tools/)
from postfreeze_demo import (FIFO, STATUS, PG, TRIG, PRE, V0, HwRig, ModelRig, Capture,  # noqa: E402
                             check, fw, load, setup, finish, model_clock, split_long, trigger_preamble)
from pe import host, sim  # noqa: E402
from pe import postfreeze as pf  # noqa: E402
from pe import postfreeze2 as p2  # noqa: E402

TRIG_S = 1e-3                  # the generator raises uio7 this long after its start
R3 = lambda c: sim.core_reg(c, sim.C_R0 + 3)
PLANS = {"data": {}, "made": 0, "used": 0}


def rd(rig, addr, n):
    """n words from a FIFO address, always a list."""
    if n == 0:
        return []
    w = rig.read(addr, n, fixed=True)
    return [w] if n == 1 else list(w)


def rxn(st):
    return (st >> 4) & 15


def det(store, name, **kw):
    """The deterministic part of a run, for `compare`."""
    if store is not None:
        store.setdefault(name, {}).update(kw)


def keep(store, name, **kw):
    """Captures for `sigrok` (not compared)."""
    if store is not None:
        store.setdefault("_captures", {})[name] = kw


def span(xs, fmt="{:.2f}"):
    return f"{fmt.format(min(xs))}..{fmt.format(max(xs))}" if xs else "-"


def ctrl(rig, mask):
    """Enable (and restart) the cores in mask."""
    rig.write(sim.A_CTRL, mask | (mask << 8))
    if rig.model:
        rig.wait(1e-6)


# ================================================================ far ends and pre-timing

def and_edges(lists, idle=1):
    """The wired-AND of several drivers' edge lists [(t, level)] (each idle before its first edge)."""
    ev = sorted(((t, i, v) for i, lst in enumerate(lists) for t, v in lst), key=lambda x: (x[0], x[1]))
    cur, last, out = [idle] * len(lists), idle, []
    for t, i, v in ev:
        cur[i] = v
        lv = int(all(cur))
        if lv != last:
            out.append((t, lv))
            last = lv
    return out


class GearD:
    def __init__(self, g):
        self.g = g

    def level(self, t):
        return self.g.player.level(t)

    def step(self, t, bus):
        self.g.step(t, bus)

    def edges(self):
        return self.g.player.edges

    def info(self, t0):
        return {"heard": [(t - t0, v, n, e) for t, v, n, e in self.g.frames],
                "sent": [(t - t0, a if isinstance(a, int) else "waveform") for t, a in self.g.sent]}


class PlayerD:
    """A fixed waveform on the bus (another controller)."""

    def __init__(self, p):
        self.p = p

    def level(self, t):
        return self.p.level(t)

    def step(self, t, bus):
        pass

    def edges(self):
        return self.p.edges

    def info(self, t0):
        return {}


class ServerD:
    def __init__(self, s):
        self.s, self.drive = s, (0, 1)

    def level(self, t):
        sd, sl = self.drive
        return sl if sd else 1

    def step(self, t, line):
        self.drive = self.s.step(t, line)

    def edges(self):
        return self.s.player.edges

    def info(self, t0):
        return {"requests": [(t - t0, d.hex(), ok, po, so) for t, d, ok, po, so, _ in self.s.requests],
                "sent": [(t - t0, fr.hex()) for t, fr in self.s.sent]}


class Far:
    """The far end of one line: the wired-AND of its drivers."""

    def __init__(self, *drivers):
        self.ds = drivers

    def level(self, t):
        return int(all(d.level(t) for d in self.ds))

    def step(self, t, bus):
        for d in self.ds:
            d.step(t, bus)

    def edges(self):
        return and_edges([d.edges() for d in self.ds])

    def info(self, t0):
        return [d.info(t0) for d in self.ds]


def plan_key(*parts):
    return hashlib.sha1(json.dumps(parts, sort_keys=True, default=repr).encode()).hexdigest()[:16]


def prep(rig, prog, cfg, *, owners, od=0, pout=0, poe=0, extra=(), prefill=(), sched, pg_init,
         la_mask=None, la_base=0, core=0):
    """Both cores stopped; prog on `core` behind the trigger preamble, optionally
    la_capture on the other core; TX FIFO prefilled; the generator loaded."""
    setup(rig, mode=PG, owners=owners, od=od, pout=pout, poe=poe, pg_init=pg_init)
    load(rig, core, prog, **cfg)
    base = core * 256
    rig.write(base + PRE, trigger_preamble(base))
    rig.write(sim.core_reg(core, sim.C_ENTRY), base + PRE)
    for a, v in extra:
        rig.write(a, v)
    if la_mask is not None:
        load(rig, 1 - core, fw("la_capture", MASK=la_mask), in_pins=la_base, clkdiv=1 << 8)
    prefill = list(prefill)
    check(len(prefill) <= 8, "at most 8 TX FIFO words before the start")
    if prefill:
        rig.write(FIFO(core), prefill, fixed=True)
    check(len(sched) <= 1024, f"{len(sched)} generator entries (at most 1024)")
    rig.pg_load(sched, init=pg_init)


def predict(name, f_model, prog, cfg_fn, *, far_factory, far_key, bit, pg_idle, dur_s, done=None, tail_s=0.0,
            **kw):
    """The scenario on the cycle model at f_model, the far end in the loop on
    uio[bit] (its level ANDed with the chip's drive, as on the board). The
    core's RX FIFO is drained as it fills. Returns {far: the far end's edges
    [(ns after the trigger, level)], pins: [(ns after the trigger, uio | V0..V7 << 8)]
    (the model's pads), words, info, t_last}."""
    key = plan_key(name, f_model, prog.words, cfg_fn(f_model), far_key, bit, pg_idle, dur_s, tail_s, TRIG_S,
                   sorted((k, v if not isinstance(v, dict) else sorted(v.items())) for k, v in kw.items()))
    hit = PLANS["data"].get(name)
    if hit and hit.get("key") == key:
        PLANS["used"] += 1
        return hit
    m = ModelRig(f_model)
    t_trig = round(TRIG_S * f_model)
    prep(m, prog, cfg_fn(f_model), sched=[(t_trig, pg_idle | (1 << TRIG))], pg_init=pg_idle, **kw)
    mh, per = m.mh, 1e9 / f_model
    m.write(sim.A_CTRL, 0b01 | (0b01 << 8))
    c0 = mh.chip.cycle
    m.pg_start()
    t_tr = (c0 + t_trig) * per
    far = far_factory(t_tr)
    base_pg = mh.pg_value
    pins = []

    def bus_uio():
        t = mh.chip.cycle * per
        v = (base_pg() & ~(1 << bit)) | (far.level(t) << bit)
        _, out, oe = mh.chip.outputs()
        r = ((oe & out) | (~oe & v)) & 0xFF
        far.step(t, (r >> bit) & 1)
        s = r | (((mh.chip.pin_out >> 24) & 0xFF) << 8)
        if not pins or pins[-1][1] != s:
            pins.append((t, s))
        return r
    mh.bus_uio = bus_uio
    words, chunk = [], max(64, round(0.5e-3 * f_model))
    n_max = c0 + round((TRIG_S + dur_s) * f_model)
    stop = None
    while mh.chip.cycle < (n_max if stop is None else min(stop, n_max)):
        mh.step(chunk)
        words += mh.pop_all(0)
        if stop is None and done is not None and done(words):
            stop = mh.chip.cycle + round(tail_s * f_model)
    check(done is None or done(words), f"{name}: the model run did not finish within {dur_s} s: {words}")
    rel = [(t - t_tr, s) for t, s in pins]
    chg = [t for (t, s), (_, s0) in zip(rel[1:], rel) if (s ^ s0) & 0xFF7F]
    t_stop = mh.chip.cycle * per - t_tr
    check(not chg or t_stop - max(chg) >= 1.5e6, f"{name}: the model run ended {(t_stop - max(chg)) / 1e6:.2f} ms "
                                                  f"after the last edge (the lines still busy?)")
    plan = {"key": key, "f_model": f_model, "far": [(t - t_tr, v) for t, v in far.edges()],
            "pins": rel, "words": words, "info": far.info(t_tr), "t_last": max(chg) if chg else 0.0}
    PLANS["data"][name] = plan
    PLANS["made"] += 1
    return plan


def far_sched(rig, far, *, bit, pg_idle):
    """Generator entries: uio7 rises at TRIG_S, then uio[bit] follows the far end's edges."""
    f = rig.f_clk
    t_trig = round(TRIG_S * f)
    v = pg_idle | (1 << TRIG)
    out = [(t_trig, v)]
    for t, lvl in far:
        c = t_trig + max(1, round(t * f / 1e9))
        v = (v & ~(1 << bit)) | (int(lvl) << bit)
        if c <= out[-1][0]:
            check(len(out) > 1, "a far-end edge before the trigger")
            out[-1] = (out[-1][0], v)
        else:
            out.append((c, v))
    return split_long(out, out[-1][0] + 1)


def run_capture(rig, prog, cfg, *, sched, pg_init, la_mask, dur_s, **kw):
    """prep, both cores started, one burst draining la_capture (core 1) for dur_s.
    Returns (Capture, core 0's RX words)."""
    burst = pfd.burst_plan(rig, dur_s)
    prep(rig, prog, cfg, sched=sched, pg_init=pg_init, la_mask=la_mask, **kw)
    ctrl(rig, 0b11)
    rig.pg_start()
    words = rig.burst(1, *burst)
    words += rd(rig, FIFO(1), rxn(rig.read(STATUS(1))))
    st0 = rig.read(STATUS(0))
    recs = rd(rig, FIFO(0), rxn(st0))
    st = finish(rig)
    c = Capture(words, la_mask, 1e9 / rig.f_clk)
    check(c.cap.exact, f"the capture has {len(c.cap.gaps)} gap(s)")
    check(st0 & 15 == 0, f"{st0 & 15} TX FIFO words left: the burst ended too soon?")
    c.info = {"records": sum(1 for w in words if w), "burst": list(burst), "status": st}
    return c, recs


def run_poll(rig, prog, cfg, *, sched, pg_init, until, timeout_s, after_s=0.01, step_s=0.002, **kw):
    """prep, core 0 started, its RX FIFO polled until until(words) and the generator is done."""
    prep(rig, prog, cfg, sched=sched, pg_init=pg_init, **kw)
    ctrl(rig, 0b01)
    rig.pg_start()
    recs = pfd.poll_records(rig, [0], lambda r: until(r[0]) and not rig.pg_busy(), step_s=step_s,
                            timeout_s=timeout_s, after_s=after_s)[0]
    fset = rig.read(sim.A_FSET)
    st = finish(rig)
    return recs, fset, st


def trig_rel(c, ch_map):
    """Edges (ns after the captured trigger) of the channels {name: ch}, and the end.
    out["uncertain"]: times of edges la_capture could not stamp exactly (closer than
    LOOP = 7 ticks to the previous record: folded, or stamped at W + 7 up to 5 ticks late)."""
    trig = next(t for t, v in c.edges(TRIG) if v == 1)
    out = {}
    for name, ch in ch_map.items():
        e = c.edges(ch)
        lvl = [v for t, v in e if t <= trig][-1]
        out[name] = [(0.0, lvl)] + [(t - trig, v) for t, v in e if t > trig]
    ce = c.cap.edges
    out["uncertain"] = [t * c.tick_ns - trig for (tp, _), (t, _) in zip(ce, ce[1:]) if t - tp <= 7]
    return out, c.t_end - trig


def vs_model(edges, plan, bit, *, after=0.0):
    """Board edges of uio[bit] against the model's (both ns after the trigger):
    the differences, or None if the edge counts differ."""
    me = pf.bit_edges([(t, s) for t, s in plan["pins"] if t >= after], bit)
    be = [x for x in edges if x[0] >= after]
    me = [x for x in me if x[0] > after]
    be = [x for x in be if x[0] > after]
    if len(me) != len(be) or [v for _, v in me] != [v for _, v in be]:
        return None
    return [b[0] - m[0] for b, m in zip(be, me)]


# ================================================================ DALI

DALI_F = 2400 * 32 * 4          # model clock: 307.2 kHz with CLKDIV 4 (the bus sampled every 3.3 us)
BUS = 0
DALI_PINS = dict(owners={BUS: 0}, od=1 << BUS, pout=1 << BUS)
DALI_IDLE = 1 << BUS
DALI_MASK = (1 << BUS) | (1 << TRIG)
TE = p2.DALI_TE


def dali_prog():
    return fw("dali_ctrl", **p2.dali_defines(BUS, BUS, 32))


def dali_cfg(f):
    return dict(clkdiv=p2.dali_clkdiv(f, 32))


def dali_tick(f):
    cd = dali_cfg(f)["clkdiv"]
    return (cd / 256) * 1e9 / f


def dali_dur(cmds):
    """A generous model-run length: SETTLE, the frame, the answer window and the stop condition each."""
    return sum(14.3e-3 + (n + 1) * 0.84e-3 + (10.6e-3 + 9 * 0.84e-3 + 3e-3 if r else 0) for _, n, r in cmds) + 5e-3


def dali_frames(edges, t_end, far):
    """The bus's frames (pins-only decoder, IEC 62386-101 receiver ranges),
    each marked as the far end's (it starts where one of the far end's does) or ours."""
    fr = p2.dali_decode(edges, t_end)
    far_starts = [f["t"] for f in p2.dali_decode([(-1e3, 1)] + [tuple(x) for x in far], t_end)]
    for f in fr:
        f["far"] = any(abs(f["t"] - s) < 50e3 for s in far_starts)
        f["t_end_bit"] = f["t"] + sum(f["runs"]) + (TE if f["bits"] and f["bits"][-1] == 1 else 0)
    return fr


def dali_case(rig, store, name, cmds, far_factory, far_key, *, capture, extra_s=0.0):
    """cmds: [(value, n, reply)] prefilled. Returns (plan, records, capture or None,
    bus edges or None, t_end)."""
    prog, words = dali_prog(), [p2.dali_word(v, n, r) for v, n, r in cmds]
    n_reply = sum(1 for c in cmds if c[2])
    plan = predict(name, DALI_F, prog, dali_cfg, far_factory=far_factory, far_key=far_key, bit=BUS,
                   pg_idle=DALI_IDLE, dur_s=dali_dur(cmds) + extra_s, prefill=words, **DALI_PINS)
    check(len(plan["words"]) == 2 * n_reply, f"model run: {len(plan['words'])} record words")
    sched = far_sched(rig, plan["far"], bit=BUS, pg_idle=DALI_IDLE)
    if capture:
        check(n_reply <= 4, "at most 4 answers per captured run (8 RX FIFO words)")
        c, recs = run_capture(rig, prog, dali_cfg(rig.f_clk), sched=sched, pg_init=DALI_IDLE, la_mask=DALI_MASK,
                              dur_s=TRIG_S + plan["t_last"] / 1e9 + 4e-3, prefill=words, **DALI_PINS)
        e, t_end = trig_rel(c, {"bus": BUS})
        keep(store, name, bus=e["bus"], t_end=t_end)
        return plan, p2.dali_records(recs), c, e["bus"], t_end
    recs, fset, _ = run_poll(rig, prog, dali_cfg(rig.f_clk), sched=sched, pg_init=DALI_IDLE,
                             until=lambda r: len(r) >= 2 * n_reply, timeout_s=2 + 2 * dali_dur(cmds),
                             after_s=0.02, prefill=words, **DALI_PINS)
    check(len(recs) == 2 * n_reply, f"{len(recs)} record words for {n_reply} answers")
    return plan, p2.dali_records(recs), None, None, None


def dali_model_line(rig, e, plan, c):
    d = vs_model(e, plan, BUS)
    if d is None:
        return "board vs model: the bus's edges differ in number or order"
    tk = dali_tick(rig.f_clk)
    return (f"board vs model ({len(d)} bus edges, ns after the trigger): board - model {span(d, '{:+.0f}')} ns "
            f"(1 tick = {tk / 1e3:.2f} us; the model's clock {1e6 / plan['f_model']:.2f} us); "
            f"{c.info['records']} records, burst SCK = clk/{c.info['burst'][1]}")


@model_clock(DALI_F)
def dali_forward(rig, store):
    """dali_ctrl: 16-, 24- and 8-bit forward frames on the bus, captured: bi-phase timing and spacing."""
    cmds = [(0xFE80, 16, 0), (0x0105, 16, 0), (0xA300, 16, 0), (0xFFFF, 16, 0), (0x0000, 16, 0),
            (0xC10203, 24, 0), (0x5A, 8, 0)]
    plan, recs, c, e, t_end = dali_case(rig, store, "dali_forward", cmds, lambda t0: Far(), "none", capture=True)
    fr = dali_frames(e, t_end, [])
    check([(f["value"], f["n"], f["error"]) for f in fr] == [(v, n, None) for v, n, _ in cmds],
          f"frames {[(hex(f['value']), f['n'], f['error']) for f in fr]}")
    ck, tk = 1e9 / rig.f_clk, dali_tick(rig.f_clk)
    runs = [d for f in fr for d in f["runs"]]
    bad = [d for d in runs if min(abs(d - k * 32 * tk) for k in (1, 2)) > ck + 0.5]
    check(not bad, f"runs {sorted(set(round(d) for d in bad))[:6]} ns are not Te or 2 Te to a clock")
    clocks = sorted({round(d / ck) for d in runs})
    gaps = [b["t"] - (a["t"] + sum(a["runs"])) for a, b in zip(fr, fr[1:])]
    check(min(gaps) >= 14.0e6, f"idle between frames {min(gaps) / 1e6:.3f} ms < 14.0")
    det(store, "dali_forward", frames=[(f["value"], f["n"]) for f in fr],
        runs_ticks=sorted({round(d / tk) for d in runs}))
    return [f"bus: {[hex(f['value']) for f in fr]} ({[f['n'] for f in fr]} bits), all legal for the "
            f"pins-only decoder (IEC 62386-101 receiver ranges)",
            f"every run between edges {clocks} clocks = {sorted({round(d / 1e3, 2) for d in runs})} us "
            f"(Te = 32 ticks of CLKDIV {dali_cfg(rig.f_clk)['clkdiv'] / 256:.4f}); idle from a frame's last edge "
            f"to the next frame {span([g / 1e6 for g in gaps], '{:.3f}')} ms (>= 14.0)",
            dali_model_line(rig, e, plan, c)]


def gears(*specs):
    """A far-end factory: DaliGear(answer, settle, te, low_extra) for each spec, wired-AND."""
    return lambda t0: Far(*[GearD(p2.DaliGear(a, settle=s, te=te, low_extra=lx)) for a, s, te, lx in specs])


def dali_settles(fr, recs, rig):
    """(measured settle per answered query from the capture, firmware - measured) in us."""
    out = []
    fwd = [f for f in fr if not f["far"]]
    bwd = [f for f in fr if f["far"]]
    for b in bwd:
        a = max((f for f in fwd if f["t"] < b["t"]), key=lambda f: f["t"])
        out.append((a["value"], (b["t"] - a["t_end_bit"]) / 1e3))
    return out


@model_clock(DALI_F)
def dali_query(rig, store):
    """dali_ctrl queries answered by the gear model (played by the generator), captured: answers and settling times."""
    ans = {0x0190: 0xAB, 0xFF91: 0x00, 0x0399: 0xFF}
    cmds = [(0x0190, 16, 1), (0xFF91, 16, 1), (0x0399, 16, 1), (0x0590, 16, 1)]
    plan, recs, c, e, t_end = dali_case(rig, store, "dali_query", cmds,
                                        gears((lambda v, n: ans.get(v), 8e6, TE, 0.0)), "query 8ms", capture=True)
    tk = dali_tick(rig.f_clk)
    fr = dali_frames(e, t_end, plan["far"])
    check([(f["value"], f["n"], f["far"]) for f in fr] ==
          [(0x0190, 16, False), (0xAB, 8, True), (0xFF91, 16, False), (0x00, 8, True), (0x0399, 16, False),
           (0xFF, 8, True), (0x0590, 16, False)] and not any(f["error"] for f in fr),
          f"bus frames {[(hex(f['value']), f['n'], f['far'], f['error']) for f in fr]}")
    check([r["answer"] for r in recs] == [0xAB, 0x00, 0xFF, None], f"firmware answers {recs}")
    check([r["n"] for r in recs[:3]] == [9, 9, 9] and not any(r["violation"] for r in recs), f"records {recs}")
    check(recs[3]["no_answer"] and not any(r["no_answer"] for r in recs[:3]), f"no-answer flags {recs}")
    meas = dali_settles(fr, recs, rig)
    lag = [r["settle_ticks"] * tk / 1e3 - m for r, (_, m) in zip(recs, meas)]
    check(all(-0.5 * tk / 1e3 <= x <= 2 * tk / 1e3 + 0.1 for x in lag),
          f"firmware settle - measured {lag} us (want 0..2 ticks)")
    det(store, "dali_query", answers=[r["answer"] for r in recs], n=[r["n"] for r in recs],
        no_answer=[r["no_answer"] for r in recs])
    return [f"bus: forward 0x0190, 0xFF91, 0x0399, 0x0590; the gear's backward frames 0xAB, 0x00, 0xFF "
            f"(decoded on the bus, legal); firmware: answers {[hex(r['answer']) if r['answer'] is not None else None for r in recs]}, "
            f"9 bits each, 'no answer' for 0x0590",
            f"settling time on the bus {span([m for _, m in meas], '{:.1f}')} us (the gear's 8000 us, pre-timed); "
            f"the firmware reports {[round(r['settle_ticks'] * tk / 1e3, 1) for r in recs[:3]]} us, "
            f"{span(lag, '{:+.1f}')} us after the bus (detection: 0..2 ticks of {tk / 1e3:.2f} us)",
            dali_model_line(rig, e, plan, c)]


@model_clock(DALI_F)
def dali_tolerance(rig, store):
    """Backward frames from gear at the ends of the receiver ranges (half bits 336..498 us, +-75 us asymmetry), at 5.5 and 10.4 ms."""
    out = []
    combos = [(336, 0), (366.7, 0), (416.67, 0), (466.7, 0), (498, 0), (416.67, 75), (416.67, -75), (380, 40),
              (450, -40)]
    tk = dali_tick(rig.f_clk)
    lags = []
    for te_us, lx_us in combos:
        a1, a2 = [0x00, 0xFF, 0x55, 0xAA], [0x81, 0x7E, 0x55, 0xAA]
        cmds = [(0x0100 | a, 16, 1) for a in a1] + [(0x0300 | a, 16, 1) for a in a2]
        g = gears((lambda v, n: v & 0xFF if v >> 8 == 1 else None, 5.5e6, te_us * 1e3, lx_us * 1e3),
                  (lambda v, n: v & 0xFF if v >> 8 == 3 else None, 10.4e6, te_us * 1e3, lx_us * 1e3))
        name = f"dali_tol_{te_us}_{lx_us}"
        plan, recs, _, _, _ = dali_case(rig, store, name, cmds, g, f"tol {te_us} {lx_us}", capture=False)
        bw = [f for f in p2.dali_decode([(-1e3, 1)] + [tuple(x) for x in plan["far"]], plan["t_last"] + 5e6)]
        check([f["value"] for f in bw] == a1 + a2 and not any(f["error"] for f in bw),
              f"{name}: the gear's waveform is not legal for the reference decoder: {bw}")
        got = [r["answer"] for r in recs]
        check(got == a1 + a2, f"half bit {te_us} us, low +{lx_us} us: answers {got}")
        planned = [r["settle_ticks"] for r in p2.dali_records(plan["words"])]
        lags += [(r["settle_ticks"] - p) for r, p in zip(recs, planned)]
        det(store, name, answers=got)
        out.append(f"half bit {te_us} us, low phases {lx_us:+} us: answers {[hex(x) for x in got]} at 5.5 ms "
                   f"(4) and 10.4 ms (4) settling time: all right")
    check(all(abs(x) <= 2 for x in lags), f"board settle - model settle {sorted(set(lags))} ticks")
    out.append(f"settling times reported: board - model {sorted(set(lags))} ticks ({tk / 1e3:.2f} us each); "
               f"{len(combos) * 8} answers")
    return out


@model_clock(DALI_F)
def dali_violations(rig, store):
    """Negative controls: 250 us half bits, a 1300 us double, no mid-bit edge, a 5-bit frame, an 11.2 ms reply, 3 ms low; then a good answer."""
    waves = {
        0x0101: p2.dali_wave(0x5A, 8, te=250e3),
        0x0102: None,
        0x0103: [(0, TE), (1, 3 * TE), (0, TE), (1, TE)] + [(0, TE), (1, TE)] * 7,
        0x0104: p2.dali_wave(0b10110, 5),
        0x0105: None,
        0x0106: [(0, 3e6)],
        0x0107: p2.dali_wave(0x3C, 8),
    }
    long_double = p2.dali_wave(0x00, 8)
    long_double[1] = (1, 1300e3)
    waves[0x0102] = long_double
    out, got = [], {}
    for name, part in (("dali_viol_a", [0x0101, 0x0102, 0x0103, 0x0104]), ("dali_viol_b", [0x0105, 0x0106, 0x0107])):
        cmds = [(v, 16, 1) for v in part]
        far = lambda t0: Far(GearD(p2.DaliGear(lambda v, n: waves.get(v), settle=7e6)),
                             GearD(p2.DaliGear(lambda v, n: 0x99 if v == 0x0105 else None, settle=11.2e6)))
        plan, recs, c, e, t_end = dali_case(rig, store, name, cmds, far, "violations", capture=True)
        got.update(zip(part, recs))
        fr = dali_frames(e, t_end, plan["far"])
        if name == "dali_viol_b":
            late = next(f for f in fr if f["far"] and f["value"] == 0x99)
            nxt = next(f for f in fr if not f["far"] and f["t"] > late["t"])
            wait = nxt["t"] - (late["t"] + sum(late["runs"]))
            check(wait >= 14e6 - 2 * TE, f"next forward frame {wait / 1e6:.2f} ms after the late answer")
            out.append(f"the late answer (0x99, 11.2 ms) is on the bus; the next forward frame starts "
                       f"{wait / 1e6:.2f} ms after its last edge; {dali_model_line(rig, e, plan, c)}")
        else:
            out.append(dali_model_line(rig, e, plan, c))
    g = got
    check(all(g[v]["answer"] is None for v in list(waves)[:-1]), f"an answer from a broken reply: {g}")
    check(g[0x0101]["violation"] and g[0x0102]["violation"] and g[0x0103]["violation"], f"violations {g}")
    check(g[0x0104]["n"] == 6 and not g[0x0104]["violation"], f"5-bit frame {g[0x0104]}")
    check(g[0x0105]["no_answer"], f"late reply {g[0x0105]}")
    check(g[0x0106]["violation"], f"3 ms low {g[0x0106]}")
    check(g[0x0107]["answer"] == 0x3C, f"good answer {g[0x0107]}")
    det(store, "dali_violations", recs={hex(v): (r["n"], r["violation"], r["no_answer"], r["answer"])
                                        for v, r in g.items()})
    return [", ".join(f"{v:#06x}: n {r['n']}{' violation' if r['violation'] else ''}"
                      f"{' no-answer' if r['no_answer'] else ''}{' answer ' + hex(r['answer']) if r['answer'] is not None else ''}"
                      for v, r in g.items())] + out


@model_clock(DALI_F)
def dali_collision(rig, store):
    """Two gear answering at once (wired-AND): the firmware's verdict against the pins-only decoder's on the captured bus."""
    pairs = [(0x0F, 0xF0), (0x80, 0x01), (0xFF, 0x00), (0x01, 0x03), (0x12, 0x12)]
    out, res = [], []
    for name, part in (("dali_coll_a", pairs[:3]), ("dali_coll_b", pairs[3:])):
        cmds = [(0xFF90 + i, 16, 1) for i in range(len(part))]
        a = {0xFF90 + i: x for i, (x, _) in enumerate(part)}
        b = {0xFF90 + i: y for i, (_, y) in enumerate(part)}
        far = gears((lambda v, n: a.get(v), 7e6, TE, 0.0), (lambda v, n: b.get(v), 7e6, TE, 0.0))
        plan, recs, c, e, t_end = dali_case(rig, store, name, cmds, far, "collision " + repr(part), capture=True)
        bw = [f for f in dali_frames(e, t_end, plan["far"]) if f["far"]]
        check(len(bw) == len(part), f"{len(bw)} backward frames on the bus for {len(part)} queries")
        for (x, y), f, r in zip(part, bw, recs):
            ref = None if (f["error"] or f["n"] != 8) else f["value"]
            check(r["answer"] == ref, f"{x:#04x} & {y:#04x}: decoder {f['error'] or hex(f['value'])}, firmware {r}")
            res.append((x, y, f["error"] or hex(f["value"]), r["violation"], r["answer"]))
        out.append(dali_model_line(rig, e, plan, c))
    check(any(r[3] for r in res) and res[-1][4] == 0x12, f"collisions {res}")
    det(store, "dali_collision", res=res)
    return [", ".join(f"{x:#04x}&{y:#04x}: decoder {d}, firmware {'violation' if v else hex(a) if a is not None else '-'}"
                      for x, y, d, v, a in res)] + out


@model_clock(DALI_F)
def dali_busy(rig, store):
    """Another controller's frame (the generator) during our SETTLE wait: ours waits for 14 ms of idle bus after it."""
    def far(t0):
        p = p2.Player()
        p.schedule(t0 + 3e6, p2.dali_wave(0xFE05, 16))
        return Far(PlayerD(p))
    plan, recs, c, e, t_end = dali_case(rig, store, "dali_busy", [(0xFE80, 16, 0)], far, "busy 3ms", capture=True,
                                        extra_s=20e-3)
    fr = p2.dali_decode(e, t_end)
    check([f["value"] for f in fr] == [0xFE05, 0xFE80] and not any(f["error"] for f in fr),
          f"frames {[(hex(f['value']), f['error']) for f in fr]}")
    gap = fr[1]["t"] - (fr[0]["t"] + sum(fr[0]["runs"]))
    check(gap >= 14.0e6, f"ours started {gap / 1e6:.3f} ms after the other controller's last edge")
    det(store, "dali_busy", frames=[f["value"] for f in fr])
    return [f"the other controller's 0xFE05 at 3 ms, ours (0xFE80, queued before) started {gap / 1e6:.3f} ms "
            f"after its last edge (>= 14.0); {dali_model_line(rig, e, plan, c)}"]


# ================================================================ Modbus RTU

LINE, DE = 0, 2
MB_PINS = dict(owners={LINE: 0, DE: 0}, od=1 << LINE, pout=1 << LINE, poe=1 << DE)
MB_IDLE = 1 << LINE
MB_MASK = (1 << LINE) | (1 << DE) | (1 << TRIG)
MB_F = 19200 * 104              # model clock: CLKDIV 1, tick for tick 50 MHz / 25.04


def mb_prog(baud=19200, parity="even"):
    return fw("modbus_rtu", **p2.modbus_defines(baud, 104, LINE, DE, LINE, parity=parity))


def mb_cfg(baud):
    return lambda f: dict(clkdiv=host.clkdiv_for(f, baud * 104))


def mb_extra():
    return [(sim.core_reg(0, sim.C_CRCCTRL), p2.MODBUS_CRCCTRL)]


def reg(a):
    return (a * 0x0101 + 0x1234) & 0xFFFF


def server_fn(body):
    """test_modbus.server_fn: address 0x11, functions 3, 6 and 8; others exception 01."""
    if body[0] != 0x11:
        return None
    fc = body[1]
    if fc == 3:
        a, n = int.from_bytes(body[2:4], "big"), int.from_bytes(body[4:6], "big")
        return bytes([0x11, 3, 2 * n]) + b"".join(reg((a + i) & 0xFFFF).to_bytes(2, "big") for i in range(n))
    if fc in (6, 8):
        return bytes(body)
    return bytes([0x11, fc | 0x80, 0x01])


LOW_EDGE = [0x00, 0xFF, 0x0F, 0xF0, 0x01, 0x80, 0x07, 0xE0, 0x03, 0xC0, 0x1F, 0xF8, 0x3F, 0xFC, 0x7F, 0xFE]


def long_fn(body):
    """A server for the 255-byte response: register bytes from LOW_EDGE (few edges per character,
    so its waveform fits the generator's 1024 entries)."""
    n = int.from_bytes(body[4:6], "big")
    rng = random.Random(25)
    return bytes([0x11, 3, 2 * n]) + bytes(rng.choice(LOW_EDGE) for _ in range(2 * n))


def mb_case(rig, store, name, reqs, respond_factory, far_key, *, baud=19200, parity="even", rate=1.0,
            capture=False):
    """reqs: [(body, expect)] prefilled; the server model (pre-timed). Returns (plan, responses, capture or None,
    edges {line, de} or None, t_end)."""
    prog, cfg = mb_prog(baud, parity), mb_cfg(baud)
    words = sum((p2.modbus_words(b, x) for b, x in reqs), [])
    n_exp = sum(1 for _, x in reqs if x)
    done = (lambda w: len(p2.modbus_response(w)) >= n_exp) if n_exp else None
    char = 11e9 / baud
    dur = sum(4e-3 + 2 * 260 * char / 1e9 for _ in reqs) + 0.12 * n_exp + 5e-3
    far = lambda t0: Far(ServerD(p2.ModbusServer(respond_factory(), baud=baud, rate=rate, parity=parity)))
    kw = dict(prefill=words, extra=mb_extra(), **MB_PINS)
    plan = predict(name, baud * 104, prog, cfg, far_factory=far, far_key=far_key, bit=LINE, pg_idle=MB_IDLE,
                   dur_s=dur if done else (4e-3 + 3 * len(words) * 4 * char / 1e9), done=done, tail_s=6e-3, **kw)
    sched = far_sched(rig, plan["far"], bit=LINE, pg_idle=MB_IDLE)
    if capture:
        c, recs = run_capture(rig, prog, cfg(rig.f_clk), sched=sched, pg_init=MB_IDLE, la_mask=MB_MASK,
                              dur_s=TRIG_S + plan["t_last"] / 1e9 + 5e-3, **kw)
        e, t_end = trig_rel(c, {"line": LINE, "de": DE})
        keep(store, name, line=e["line"], de=e["de"], t_end=t_end, baud=baud)
        return plan, (p2.modbus_response(recs) if recs else []), c, e, t_end
    recs, fset, _ = run_poll(rig, prog, cfg(rig.f_clk), sched=sched, pg_init=MB_IDLE,
                             until=done or (lambda r: True), timeout_s=2 + 3 * dur, after_s=0.01, **kw)
    return plan, p2.modbus_response(recs), None, None, None


def mb_chars(e, t_end, baud, parity="even"):
    """Characters on the line (pins-only UART decoder), split by DE at their start bits."""
    chars = p2.uart_decode(e["line"], t_end, 1e9 / baud, parity)
    de = e["de"]
    lvl = lambda t: [v for tt, v in de if tt <= t][-1]
    return [c for c in chars if lvl(c["t"])], [c for c in chars if not lvl(c["t"])]


def frames_of(chars, t35):
    out = []
    for c in chars:
        if out and c["t"] - out[-1][-1]["t_stop_end"] < t35:
            out[-1].append(c)
        else:
            out.append([c])
    return out


def mb_wire(e, t_end, baud=19200):
    """Request frames on the bus with their timing: edges vs the bit grid, gaps, DE lead and lag."""
    bit = 1e9 / baud
    cl, sv = mb_chars(e, t_end, baud)
    req = frames_of(cl, 3.5 * 11 * bit)
    err, gaps, lead, lag = [], [], [], []
    unc = e.get("uncertain", [])
    for fr in req:
        for c in fr:
            o = [t - c["t"] - round((t - c["t"]) / bit) * bit for t, _ in e["line"] if c["t"] < t < c["t"] + 10.5 * bit]
            if c["t"] in unc and o:          # its start edge was stamped late: grid from the other edges
                o = [x - sorted(o)[len(o) // 2] for x in o]
            err += o
        gaps += [b["t"] - a["t_stop_end"] for a, b in zip(fr, fr[1:])]
        on = max(t for t, v in e["de"] if v == 1 and t <= fr[0]["t"])
        off = min(t for t, v in e["de"] if v == 0 and t >= fr[0]["t"])
        lead.append(fr[0]["t"] - on)
        lag.append(off - fr[-1]["t_stop_end"])
    return req, frames_of(sv, 3.5 * 11 * bit), err, gaps, lead, lag


def mb_model_line(e, plan, c):
    d = vs_model(e["line"], plan, LINE)
    dd = vs_model(e["de"], plan, DE)
    if d is None or dd is None:
        return "board vs model: the line's or DE's edges differ in number or order"
    return (f"board vs model ({len(d)} line and {len(dd)} DE edges, ns after the trigger): board - model "
            f"{span(d + dd, '{:+.0f}')} ns (1 tick = {1e9 / MB_F:.0f} ns); {c.info['records']} records, "
            f"burst SCK = clk/{c.info['burst'][1]}")


@model_clock(MB_F)
def modbus_published(rig, store):
    """modbus_rtu: two broadcast requests with published CRCs, captured: bytes, bit timing, DE."""
    bodies = [bytes.fromhex("01030000000a"), bytes.fromhex("1103006b0003")]
    plan, resp, c, e, t_end = mb_case(rig, store, "modbus_published", [(b, False) for b in bodies],
                                      lambda: (lambda b: None), "none", capture=True)
    req, sv, err, gaps, lead, lag = mb_wire(e, t_end)
    got = [bytes(ch["byte"] for ch in fr).hex() for fr in req]
    check(got == ["01030000000ac5cd", "1103006b00037687"], f"on the bus {got}")
    check(all(ch["parity_ok"] and ch["stop_ok"] for fr in req for ch in fr), "parity or stop bit wrong")
    check(not sv and not resp, f"the server answered a broadcast? {sv} {resp}")
    srv = p2.ModbusServer(lambda b: None)
    ev = e["line"][1:]
    for i, (t, v) in enumerate(ev):
        srv.step(t, v)
        nxt = ev[i + 1][0] if i + 1 < len(ev) else t_end + 3e6
        if nxt > t + srv.t35 + 1:
            srv.step(t + srv.t35 + 1, v)      # silence: the server frames by t3.5
    check([(r[1].hex(), r[2], r[3], r[4]) for r in srv.requests] == [(g, True, True, True) for g in got],
          f"the server model heard {srv.requests}")
    bit = 1e9 / 19200
    check(max(abs(x) for x in err) <= 2 * 1e9 / rig.f_clk + 1, f"edges {span(err)} ns off the grid")
    check(max(gaps) <= 0.2 * bit and min(lead) > 0 and min(lag) >= -1e3, f"gaps {gaps} lead {lead} lag {lag}")
    det(store, "modbus_published", bus=got)
    n_unc = sum(1 for fr in req for ch in fr if ch["t"] in e["uncertain"])
    return [f"bus: {got} (CRC C5 CD and 76 87 as published), even parity right; the server model "
            f"(pins only, the specification's CRC) heard both with CRC ok; no response (broadcasts)",
            f"edges {span(err, '{:+.0f}')} ns from each character's 19200 baud grid"
            f"{f' ({n_unc} start edges within 7 capture ticks of DE: grid from their other edges)' if n_unc else ''}; "
            f"characters "
            f"{span([g / 1e3 for g in gaps])} us apart (t1.5 = 859 us); DE high {span([x / 1e3 for x in lead])} us "
            f"before the first start bit, low {span([x / 1e3 for x in lag])} us after the last stop bit",
            mb_model_line(e, plan, c)]


@model_clock(MB_F)
def modbus_reqresp(rig, store):
    """modbus_rtu: two requests and the server's responses (played by the generator), captured."""
    bodies = [bytes([0x11, 3, 0, 0x6B, 0, 3]), bytes([0x11, 6, 0, 1, 0xBE, 0xEF])]
    plan, resp, c, e, t_end = mb_case(rig, store, "modbus_reqresp", [(b, True) for b in bodies],
                                      lambda: server_fn, "server_fn", capture=True)
    want = [p2.modbus_adu(server_fn(b)) for b in bodies]
    check([r["bytes"] for r in resp] == want and all(r["crc_ok"] for r in resp), f"responses {resp}")
    check(not any(r[k] for r in resp for k in ("parity_error", "framing_error", "gap", "overflow")), f"flags {resp}")
    req, sv, err, gaps, lead, lag = mb_wire(e, t_end)
    check([bytes(ch["byte"] for ch in fr) for fr in req] == [p2.modbus_adu(b) for b in bodies], "requests on the bus")
    check([bytes(ch["byte"] for ch in fr) for fr in sv] == want, "responses on the bus")
    turn = [s[0]["t"] - r[-1]["t_stop_end"] for r, s in zip(req, sv)]
    det(store, "modbus_reqresp", responses=[r["bytes"].hex() for r in resp])
    return [f"requests {[p2.modbus_adu(b).hex() for b in bodies]} on the bus; the server's responses "
            f"{[w.hex() for w in want]} received by the firmware with CRC ok, no flag",
            f"request edges {span(err, '{:+.0f}')} ns off the grid; response start {span([t / 1e3 for t in turn], '{:.1f}')} "
            f"us after the request's last stop bit (the server model's 3000 us, pre-timed)",
            mb_model_line(e, plan, c)]


def mb_batches(reqs):
    out, cur, n = [], [], 0
    for r in reqs:
        w = len(p2.modbus_words(*r))
        if cur and n + w > 8:
            out.append(cur)
            cur, n = [], 0
        cur.append(r)
        n += w
    return out + ([cur] if cur else [])


@model_clock(MB_F)
def modbus_lengths(rig, store):
    """Responses of every length mod 3 (the 3-byte packing), functions 3, 6, 8 and an exception; polled."""
    bodies = [bytes([0x11, 3, 0, 0x6B, 0, n]) for n in (1, 2, 3, 4, 10)] + \
             [bytes([0x11, 6, 0, 1, 0xBE, 0xEF]), bytes([0x11, 8, 0, 0, 0xA5, 0x37]), bytes([0x11, 0x2B, 0x0E])]
    out, got = [], []
    for i, batch in enumerate(mb_batches([(b, True) for b in bodies])):
        plan, resp, *_ = mb_case(rig, store, f"modbus_len_{i}", batch, lambda: server_fn, "server_fn")
        for (b, _), r in zip(batch, resp):
            want = p2.modbus_adu(server_fn(b))
            check(r["bytes"] == want and r["crc_ok"], f"{b.hex()}: {r}")
            check(not any(r[k] for k in ("parity_error", "framing_error", "gap", "overflow")), f"flags {r}")
            got.append(len(r["bytes"]))
        check(len(resp) == len(batch), f"{len(resp)} responses for {len(batch)} requests")
    det(store, "modbus_lengths", lengths=got)
    return [f"{len(bodies)} requests in {len(mb_batches([(b, True) for b in bodies]))} runs: responses of "
            f"{got} bytes (CRC included; {sorted({n % 3 for n in got})} mod 3) all right, CRC ok, no flag"]


@model_clock(MB_F)
def modbus_long(rig, store):
    """A 255-byte response (125 registers), drained while it arrives; polled."""
    body = bytes([0x11, 3, 0x10, 0x00, 0, 125])
    plan, resp, *_ = mb_case(rig, store, "modbus_long", [(body, True)], lambda: long_fn, "long_fn")
    want = p2.modbus_adu(long_fn(body))
    check(len(resp) == 1 and resp[0]["bytes"] == want and resp[0]["crc_ok"] and not resp[0]["overflow"],
          f"response {resp}")
    det(store, "modbus_long", n=len(resp[0]["bytes"]))
    return [f"255 bytes (85 records, about {255 * 11 / 19200 * 1e3:.0f} ms) drained by the host while arriving: "
            f"all right, CRC ok, no overflow; generator entries {len(plan['far']) + 1}"]


@model_clock(MB_F)
def modbus_tolerance(rig, store):
    """A server 2 % slow or fast, 0 or 1.3 characters between its characters; polled."""
    char = 11e9 / 19200
    out = []
    for rate in (0.98, 1.02):
        for gc in (0, 1.3):
            bodies = [bytes([0x11, 3, 0, 0, 0, n]) for n in (1, 9)]
            fn = lambda: (lambda b: {"data": server_fn(b), "gaps": {i: gc * char for i in range(40)}})
            plan, resp, *_ = mb_case(rig, store, f"modbus_tol_{rate}_{gc}", [(b, True) for b in bodies], fn,
                                     f"tol {rate} {gc}", rate=rate)
            for b, r in zip(bodies, resp):
                check(r["crc_ok"] and r["bytes"] == p2.modbus_adu(server_fn(b)), f"rate {rate} gaps {gc}: {r}")
                check(not (r["gap"] or r["parity_error"] or r["framing_error"]), f"flags {r}")
            check(len(resp) == 2, f"{len(resp)} responses")
            out.append(f"server x{rate}, {gc} characters between characters: {[len(r['bytes']) for r in resp]}-byte "
                       f"responses right, CRC ok, no flag")
    det(store, "modbus_tolerance", ok=True)
    return out


@model_clock(MB_F)
def modbus_errors(rig, store):
    """Negative controls: wrong CRC, parity error, low stop bit, 2.5- and 4-character gaps, no response; then a good one."""
    char = 11e9 / 19200
    body = bytes([0x11, 3, 0, 0, 0, 2])
    good = server_fn(body)
    runs = [("modbus_err_a", [{"data": good, "crc": False}, {"data": good, "bad_parity": (3,)}]),
            ("modbus_err_b", [{"data": good, "bad_stop": (4,)}, {"data": good, "gaps": {2: 2.5 * char}}]),
            ("modbus_err_c", [{"data": good, "gaps": {2: 4.0 * char}}]),
            ("modbus_err_d", [None, good])]
    res = []
    for name, plan_ in runs:
        def factory(plan_=plan_):
            it = iter(plan_)
            return lambda b: next(it)
        plan, resp, *_ = mb_case(rig, store, name, [(body, True)] * len(plan_), factory, repr(plan_))
        check(len(resp) == len(plan_), f"{name}: {len(resp)} responses")
        res += resp
    bad_crc, par, stop, gap, split, none, ok = res
    adu = p2.modbus_adu(good)
    check(not bad_crc["crc_ok"] and len(bad_crc["bytes"]) == len(good) + 2, f"wrong CRC {bad_crc}")
    check(par["parity_error"] and par["crc_ok"], f"parity {par}")
    check(stop["framing_error"], f"stop {stop}")
    check(gap["gap"] and gap["crc_ok"] and gap["bytes"] == adu, f"2.5-character gap {gap}")
    check(not split["gap"] and not split["crc_ok"] and split["bytes"] == adu[:3], f"4-character gap {split}")
    check(none["bytes"] == b"" and not none["crc_ok"], f"no response {none}")
    check(ok["crc_ok"] and ok["bytes"] == adu and not (ok["gap"] or ok["parity_error"]), f"good {ok}")
    det(store, "modbus_errors", res=[(r["bytes"].hex(), r["crc_ok"], r["parity_error"], r["framing_error"], r["gap"])
                                     for r in res])
    fl = lambda r: ",".join(k for k in ("crc_ok", "parity_error", "framing_error", "gap") if r[k]) or "-"
    return [f"wrong CRC: {len(bad_crc['bytes'])} bytes, {fl(bad_crc)}; parity error: {fl(par)}; low stop bit: "
            f"{fl(stop)}; 2.5-character gap: {fl(gap)}; 4-character gap: frame ends after {len(split['bytes'])} "
            f"bytes, {fl(split)}; no response: {len(none['bytes'])} bytes; good: {fl(ok)}"]


@model_clock(38400 * 104)
def modbus_odd(rig, store):
    """Odd parity at 38400 baud (t1.5 = 750 us): a response, a 1.0 ms gap flagged, a 0.6 ms gap not; polled."""
    body = bytes([0x11, 3, 0, 7, 0, 4])
    res = []
    for name, opts in (("modbus_odd_a", [{}, {"gaps": {3: 1.0e6}}]), ("modbus_odd_b", [{"gaps": {3: 0.6e6}}])):
        def factory(opts=opts):
            it = iter(opts)
            return lambda b: dict(data=server_fn(b), **next(it))
        plan, resp, *_ = mb_case(rig, store, name, [(body, True)] * len(opts), factory, repr(opts), baud=38400,
                                 parity="odd")
        res += resp
    check(len(res) == 3 and all(r["crc_ok"] and r["bytes"] == p2.modbus_adu(server_fn(body)) for r in res),
          f"responses {res}")
    check([r["gap"] for r in res] == [False, True, False], f"gap flags {[r['gap'] for r in res]}")
    det(store, "modbus_odd", gaps=[r["gap"] for r in res])
    return ["odd parity, 38400 baud: 3 responses right with CRC ok; the 1.0 ms gap flagged (t1.5 = 750 us), "
            "no gap and the 0.6 ms gap not"]


# ================================================================ MIDI

MIDI_BIT = 1e9 / p2.MIDI_BAUD
MRX = 0                         # midi_rx's RX: uio0 (the generator)
MTX_PIN = 1                     # midi_tx's pin in the full port: uio1


def mrx_prog(rx=MRX):
    return fw("midi_rx", RX=rx, TB=128, FOV=0)


def mtx_prog(tx, rs=1):
    return fw("midi_tx", TX=tx, TB=32, RS=rs)


def mrx_div(f):
    return host.clkdiv_for(f, p2.MIDI_BAUD * 128)


def mtx_div(f):
    return host.clkdiv_for(f, p2.MIDI_BAUD * 32)


def midi_line_check(e, t_end, want, f):
    """Bytes on a captured MIDI line and its timing: (bytes, bit runs in us, stop + space in us)."""
    chars = p2.uart_decode(e, t_end, MIDI_BIT, parity=None)
    got = bytes(c["byte"] for c in chars)
    check(got == want and all(c["stop_ok"] for c in chars), f"wire {got.hex()} != {want.hex()}")
    ck = 1e9 / f
    runs = []
    for c in chars:
        inner = [t for t, _ in e if c["t"] <= t < c["t"] + 9.5 * MIDI_BIT]
        runs += [b - a for a, b in zip(inner, inner[1:])]
    bad = [r for r in runs if abs(r - round(r / MIDI_BIT) * MIDI_BIT) > ck / 2 + 0.01]
    check(not bad, f"bit runs {sorted(set(round(x) for x in bad))[:5]} ns are not multiples of 32 us")
    space = [(b["t"] - a["t"]) / 1e3 - 9 * 32 for a, b in zip(chars, chars[1:])]
    return chars, sorted({round(r / 1e3, 3) for r in runs}), space


@model_clock(31250 * 32)
def midi_tx_capture(rig, store):
    """midi_tx -> V0 -> la_capture: running status on and off, every bit 32 us."""
    out = []
    evs = {1: [("msg", 0x90, (60, 100)), ("msg", 0x90, (64, 90)), ("msg", 0x90, (60, 0)), ("msg", 0xF8, ()),
               ("msg", 0x81, (64, 10)), ("msg", 0xC2, (5,)), ("msg", 0xC2, (6,)), ("msg", 0xE0, (0x00, 0x40))],
           0: [("msg", 0x90, (60, 100)), ("msg", 0x90, (64, 90)), ("sysex", bytes([0x7E, 0x09, 0x01]), True),
               ("msg", 0xF2, (0x10, 0x02)), ("msg", 0xFA, ()), ("msg", 0xB0, (7, 100))]}
    for rs, ev in evs.items():
        words = p2.midi_tx_words(ev)
        want = p2.midi_encode(ev, running_status=bool(rs))
        dur = TRIG_S + len(want) * 12 * MIDI_BIT / 1e9 + 2e-3
        c = pfd.capture(rig, mtx_prog(V0, rs), dict(clkdiv=mtx_div(rig.f_clk)), mask=1, prefill=words,
                        owners={V0: 0}, pout=1 << V0, trig_s=TRIG_S, burst=pfd.burst_plan(rig, dur))
        e = c.edges(0)
        chars, runs, space = midi_line_check(e, c.t_end, want, rig.f_clk)
        name = f"midi_tx_rs{rs}"
        keep(store, name, tx=e, t_end=c.t_end)
        t0 = chars[0]["t"]
        det(store, name, wire=want.hex(), edges_us=[round((t - t0) / 1e3, 3) for t, _ in e if t >= t0])
        out.append(f"RS={rs}: {len(ev)} events -> {len(want)} bytes {want.hex()} (the reference encoder's; "
                   f"{len(p2.midi_encode(ev, running_status=not rs))} with RS={1 - rs}); runs inside bytes "
                   f"{runs} us; stop bit + space {span(space, '{:.0f}')} us; {c.info['records']} records")
    return out


def midi_rx_run(rig, sched, until, *, timeout_s=5.0, step_s=0.5e-3, after_s=0.01, core=1, extra_start=None):
    setup(rig, mode=PG, pg_init=1 << MRX)
    load(rig, core, mrx_prog(), clkdiv=mrx_div(rig.f_clk))
    check(len(sched) <= 1024, f"{len(sched)} generator entries")
    rig.pg_load(sched, init=1 << MRX)
    rig.write(sim.A_FCLR, 1)
    ctrl(rig, 1 << core)
    rig.pg_start()
    recs = pfd.poll_records(rig, [core], lambda r: until(r[core]) and not rig.pg_busy(), step_s=step_s,
                            timeout_s=timeout_s, after_s=after_s)[core]
    fset = rig.read(sim.A_FSET)
    finish(rig)
    return recs, fset


def midi_wave(stream, rate=1.0, **kw):
    return p2.uart_wave(stream, MIDI_BIT / rate, parity=None, **kw) + [(1, 2e6)]


@model_clock(31250 * 128)
def midi_rx_stream(rig, store):
    """midi_rx fed by the generator: random streams of every kind with running status and real-time bytes anywhere, back to back, at x1.0 and +-2 %."""
    out = []
    for name, seed, n_ev, n_rt, rate in (("midi_rx_s1", 1, 40, 20, 1.0), ("midi_rx_s2", 2, 40, 20, 1.0),
                                         ("midi_rx_slow", 98, 30, 10, 0.98), ("midi_rx_fast", 102, 30, 10, 1.02)):
        rng = random.Random(seed)
        stream = p2.insert_realtime(p2.midi_encode(p2.midi_random_events(rng, n_ev)), rng, n_rt)
        sched = pfd.pg_from_levels(rig, [midi_wave(stream, rate)], other=0, bit=MRX, t0_s=TRIG_S)
        want = p2.midi_parse(stream)
        recs, fset = midi_rx_run(rig, sched, lambda r: len(p2.midi_rx_events(r)) >= len(want))
        got = p2.midi_rx_events(recs)
        check(got == want, f"{name}: {len(got)} events != the reference's {len(want)}; first difference at "
                           f"{next((i for i, (a, b) in enumerate(zip(got, want)) if a != b), min(len(got), len(want)))}")
        check(not fset & 1, f"{name}: FOV set (the host fell behind)")
        det(store, name, events=len(got), equal=True)
        out.append(f"{name}: {len(stream)} bytes at x{rate} ({len(sched)} generator entries) -> {len(got)} events "
                   f"({sum(e[0] == 'sysex' for e in got)} SysEx, {sum(e[0] == 'msg' and e[1] >= 0xF8 for e in got)} "
                   f"real-time), equal to the reference parser's; FOV 0")
    return out


@model_clock(31250 * 128)
def midi_rx_negative(rig, store):
    """Negative controls: data with no status, after system common, a cut message; a low stop bit (framing error)."""
    s1 = bytes.fromhex("12 34" "f3 05 06 07" "90 3c" "80 3c 00 3d" "c5 10 11" "b0 07") + bytes([0x64, 0x90, 0x40, 0x7F])
    bad = bytes.fromhex("90 3c 40 3e 40 40 41")
    seqs = [midi_wave(s1, gaps={1: 5 * MIDI_BIT}), [(1, 5e6)], midi_wave(bad, bad_stop=(2,), gaps={2: 3 * MIDI_BIT})]
    sched = pfd.pg_from_levels(rig, seqs, other=0, bit=MRX, t0_s=TRIG_S)
    want1 = p2.midi_parse(s1)
    want2 = [("ferr", 0x40)] + p2.midi_parse(bad[:2] + bad[3:])
    recs, fset = midi_rx_run(rig, sched, lambda r: len(p2.midi_rx_events(r)) >= len(want1) + len(want2))
    got = p2.midi_rx_events(recs)
    check(want1 == [("msg", 0xF3, (5,)), ("msg", 0x80, (0x3C, 0)), ("msg", 0xC5, (0x10,)), ("msg", 0xC5, (0x11,)),
                    ("msg", 0xB0, (7, 0x64)), ("msg", 0x90, (0x40, 0x7F))], "reference")
    # the first stream ends in running status 0x90; the second starts with its own status
    check(got == want1 + want2, f"events {got}\n want {want1 + want2}")
    det(store, "midi_rx_negative", events=[list(map(str, e)) for e in got])
    return [f"stray data bytes dropped as the reference drops them: {len(want1)} messages; a low stop bit on "
            f"byte 2 -> ('ferr', 0x40), the stream resumes: {want2[1:]}"]


@model_clock(31250 * 128)
def midi_rx_fifo_full(rig, store):
    """A host that stops reading: the first 8 messages kept, FOV set, the receiver keeps going."""
    notes = [("msg", 0x90, (60 + i, 100)) for i in range(20)]
    s1 = p2.midi_encode(notes)
    rng = random.Random(9)
    s2 = p2.midi_encode(p2.midi_random_events(rng, 20))
    sched = pfd.pg_from_levels(rig, [midi_wave(s1), [(1, 150e6)], midi_wave(s2)], other=0, bit=MRX, t0_s=TRIG_S)
    setup(rig, mode=PG, pg_init=1 << MRX)
    load(rig, 1, mrx_prog(), clkdiv=mrx_div(rig.f_clk))
    rig.pg_load(sched, init=1 << MRX)
    rig.write(sim.A_FCLR, 1)
    ctrl(rig, 0b10)
    rig.pg_start()
    t1 = TRIG_S + len(s1) * 10 * MIDI_BIT / 1e9
    rig.wait(t1 + 0.05)                          # the host does not read meanwhile
    w1 = rd(rig, FIFO(1), rxn(rig.read(STATUS(1))))
    fov = rig.read(sim.A_FSET) & 1
    rig.write(sim.A_FCLR, 1)
    rest = pfd.poll_records(rig, [1], lambda r: not rig.pg_busy(), step_s=0.5e-3, timeout_s=3, after_s=0.01)[1]
    fov2 = rig.read(sim.A_FSET) & 1
    finish(rig)
    g1, g2 = p2.midi_rx_events(w1), p2.midi_rx_events(rest)
    want2 = p2.midi_parse(s1 + s2)[len(p2.midi_parse(s1)):]
    check(g1 == notes[:8] and fov, f"kept {g1}, FOV {fov}")
    check(g2 == want2 and not fov2, f"after the drain: {len(g2)} events, FOV {fov2}")
    det(store, "midi_rx_fifo_full", kept=len(g1), fov=fov, after=len(g2))
    return [f"20 note-ons ({len(s1)} bytes) with the host not reading: the first 8 kept, FOV = {fov}; then "
            f"{len(s2)} bytes drained as they came: {len(g2)} events equal to the reference, FOV = {fov2}"]


@model_clock(31250 * 128)
def midi_loopback(rig, store):
    """midi_tx (core 0) -> V0 -> midi_rx (core 1), the host feeding and draining: 40 events of every kind."""
    rng = random.Random(11)
    ev = p2.midi_random_events(rng, 40)
    words = p2.midi_tx_words(ev)
    setup(rig, mode=PG, owners={V0: 0}, pout=1 << V0)
    load(rig, 0, mtx_prog(V0, 1), clkdiv=mtx_div(rig.f_clk))
    load(rig, 1, mrx_prog(V0), clkdiv=mrx_div(rig.f_clk))
    rig.write(sim.A_FCLR, 1)
    ctrl(rig, 0b11)
    got, i = [], 0
    t0, tm = time.monotonic(), 0.0
    n_bytes = len(p2.midi_encode(ev))
    limit = 3.0 + n_bytes * 10 * MIDI_BIT / 1e9
    while True:
        st0 = rig.read(STATUS(0))
        room = 8 - (st0 & 15)
        if i < len(words) and room:
            k = min(room, len(words) - i)
            rig.write(FIFO(0), words[i:i + k], fixed=True)
            i += k
        got += rd(rig, FIFO(1), rxn(rig.read(STATUS(1))))
        if i == len(words) and len(p2.midi_rx_events(got)) >= len(ev):
            break
        check((tm if rig.model else time.monotonic() - t0) < limit, f"loopback stalled: {i} words sent, "
                                                                     f"{len(p2.midi_rx_events(got))} events")
        rig.wait(0.5e-3)
        tm += 0.5e-3
    fset = rig.read(sim.A_FSET)
    finish(rig)
    out = p2.midi_rx_events(got)
    check(out == ev and not fset & 1, f"{len(out)} events back; equal: {out == ev}; FOV {fset & 1}")
    det(store, "midi_loopback", events=len(out), equal=True)
    return [f"{len(ev)} events ({n_bytes} bytes with running status) through V0: the same {len(out)} events out, "
            f"FOV 0; both cores running at once"]


@model_clock(31250 * 128)
def midi_port(rig, store):
    """A full port: midi_rx (core 1) takes a generator stream on uio0 while midi_tx (core 0) sends on uio1, read by the host."""
    ev_tx = [("msg", 0x90, (60, 100)), ("msg", 0x90, (64, 90)), ("msg", 0x80, (60, 0)), ("msg", 0xF8, ()),
             ("msg", 0xC1, (12,)), ("msg", 0xE0, (0x10, 0x40)), ("msg", 0xB0, (64, 127)), ("msg", 0xB0, (64, 0))]
    ev_rx = [("msg", 0x92, (36, 120)), ("msg", 0x92, (38, 120)), ("msg", 0xF8, ()), ("msg", 0x82, (36, 0)),
             ("msg", 0xD2, (50,)), ("msg", 0xF2, (0, 8)), ("msg", 0xFA, ()), ("msg", 0x92, (40, 1))]
    want_tx = p2.midi_encode(ev_tx)
    stream = p2.midi_encode(ev_rx)
    f = rig.f_clk
    # trigger first, then the stream into uio0 (uio7 stays high)
    sched = pfd.pg_from_levels(rig, [[(1, 0.2e6)], midi_wave(stream)], other=1 << TRIG, bit=MRX, t0_s=TRIG_S)
    setup(rig, mode=PG, owners={MTX_PIN: 0}, pout=(1 << MTX_PIN), poe=(1 << MTX_PIN), pg_init=1 << MRX)
    prog = mtx_prog(MTX_PIN, 1)
    load(rig, 0, prog, clkdiv=mtx_div(f))
    rig.write(PRE, trigger_preamble(0))
    rig.write(sim.core_reg(0, sim.C_ENTRY), PRE)
    load(rig, 1, mrx_prog(), clkdiv=mrx_div(f))
    rig.write(FIFO(0), p2.midi_tx_words(ev_tx), fixed=True)
    rig.pg_load(sched, init=1 << MRX)
    rig.write(sim.A_FCLR, 1)
    ctrl(rig, 0b11)
    span_ns = (TRIG_S + max(len(want_tx), len(stream)) * 10.5 * MIDI_BIT / 1e9 + 1e-3) * 1e9
    edges = []
    rig.pg_start()
    if rig.model:
        mh, per = rig.mh, 1e9 / f
        c0 = mh.chip.cycle
        while (mh.chip.cycle - c0) * per < span_ns:
            mh.step(4)
            v = (mh.chip.outputs()[1] >> MTX_PIN) & 1
            if not edges or edges[-1][1] != v:
                edges.append(((mh.chip.cycle - c0) * per, v))
    else:
        inp = rig.board.regs.inp
        t0 = time.perf_counter_ns()
        while True:
            v = (inp() >> (8 + MTX_PIN)) & 1
            t = time.perf_counter_ns() - t0
            if not edges or edges[-1][1] != v:
                edges.append((t, v))
            if t > span_ns:
                break
    recs = rd(rig, FIFO(1), rxn(rig.read(STATUS(1))))
    st0 = rig.read(STATUS(0))
    fset = rig.read(sim.A_FSET)
    finish(rig)
    got_rx = p2.midi_rx_events(recs)
    chars = p2.uart_decode(edges, span_ns, MIDI_BIT, parity=None)
    got_tx = bytes(c["byte"] for c in chars)
    dt = [b[0] - a[0] for a, b in zip(edges, edges[1:])]
    keep(store, "midi_port", tx_host=edges, t_end=span_ns)
    det(store, "midi_port", rx=len(got_rx), tx=got_tx.hex())
    check(got_rx == ev_rx and not fset & 1, f"RX events {got_rx}")
    check(st0 & 15 == 0, f"{st0 & 15} TX words left")
    check(got_tx == want_tx and all(c["stop_ok"] for c in chars),
          f"TX as the host read it {got_tx.hex()} != {want_tx.hex()} (host-timed GPIO reads)")
    return [f"RX: {len(stream)} bytes from the generator -> {len(got_rx)} events equal to the sent ones (FOV 0); "
            f"TX at the same time: {want_tx.hex()} ({len(want_tx)} bytes, running status), decoded from "
            f"{len(edges)} level changes the host read from the GPIO (uio_out[1])",
            f"(the TX side is timed by the host's reads: runs between changes {span([d / 1e3 for d in dt], '{:.1f}')} "
            f"us, so it shows the bytes, not the bit timing; midi_tx_capture measures that)"]


# ================================================================ quadrature

QA, QFERR = 0, 6
SEQ = p2.QUAD_SEQ
ALT = 26.125 / 24               # alternate generator clock: one cycle = this many protoemu clocks


def quad_prog():
    return fw("quad_enc", A=QA, FERR=QFERR)


class Enc:
    """The encoder: steps on the generator's schedule (times in generator cycles)."""

    def __init__(self, t0=1, phase=0):
        self.t, self.phase, self.pos, self.sched = t0, phase, 0, []

    def state(self):
        return SEQ[self.phase]

    def put(self, s, hold):
        self.sched.append((self.t, s))
        self.t += hold

    def step(self, d, hold):
        self.phase = (self.phase + d) % 4
        self.pos += d
        self.put(self.state(), hold)


def quad_begin(rig, la_on=False):
    setup(rig, mode=PG, pg_init=0)
    load(rig, 0, quad_prog(), clkdiv=1 << 8)
    rig.write(R3(0), 0)
    rig.write(sim.A_FCLR, 1 << QFERR)
    if la_on:
        load(rig, 1, fw("la_capture", MASK=0b11), in_pins=0, clkdiv=1 << 8)
    ctrl(rig, 0b11 if la_on else 0b01)


def quad_play(rig, sched, init, alt=False, wait=True):
    if alt and not rig.model:
        rig.lab.pg_load(sched, init=init, alt=True)
    elif alt:
        rig.pg_load([(math.ceil(t * ALT), v) for t, v in sched], init=init)
    else:
        rig.pg_load(sched, init=init)
    rig.pg_start()
    while wait and rig.pg_busy():
        rig.wait(1e-4 if rig.model else 1e-3)
    if wait:
        rig.wait(2e-6)


def quad_pos(rig):
    v = rig.read(R3(0)) & 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


def quad_ferr(rig):
    return (rig.read(sim.A_FSET) >> QFERR) & 1


def quad_end(rig):
    rig.write(sim.A_FCLR, 1 << QFERR)
    if not rig.model:
        rig.lab.pg_set(0, alt=False)
    finish(rig)


@model_clock(50e6)
def quad_walk(rig, store):
    """quad_enc, CLKDIV 1: a 1000-step random walk (15..60 clocks per step) from the generator, captured too."""
    rng = random.Random(1)
    enc, d = Enc(round(150e-6 * rig.f_clk)), 1
    for _ in range(1000):
        if rng.random() < 0.05:
            d = -d
        enc.step(d, rng.choice([15, 16, 20, 30, 60]))
    before = enc.pos
    enc.step(1, 40)                                 # a final known step for sigrok's count
    quad_begin(rig, la_on=True)
    rig.pg_load(enc.sched, init=0)
    rig.pg_start()
    dur = enc.t / rig.f_clk + 0.1e-3
    words = rig.burst(1, *pfd.burst_plan(rig, dur, quad=True))
    words += rd(rig, FIFO(1), rxn(rig.read(STATUS(1))))
    pos, ferr = quad_pos(rig), quad_ferr(rig)
    quad_end(rig)
    c = Capture(words, 0b11, 1e9 / rig.f_clk)
    check(c.cap.exact and not c.cap.pulses, f"capture gaps {len(c.cap.gaps)}, pulses {len(c.cap.pulses)}")
    cnt, dbl = p2.quad_count([(t, s) for t, s in c.cap.edges])
    states = [s for _, s in c.cap.edges[1:]]
    times = [t for t, _ in c.cap.edges[1:]]
    check(states == [s for _, s in enc.sched], "the captured states are not the generator's")
    off = {t - st for t, (st, _) in zip(times, enc.sched)}
    check(len(off) == 1, f"captured times - generator times {sorted(off)[:5]} (want one constant)")
    check(pos == enc.pos == cnt and not ferr and not dbl, f"R3 {pos}, true {enc.pos}, pins-only {cnt}, FERR {ferr}")
    keep(store, "quad_walk", a=c.edges(0), b=c.edges(1), t_end=c.t_end, pos=pos)
    det(store, "quad_walk", pos=pos, steps=len(enc.sched))
    return [f"{len(enc.sched)} steps: R3 = {pos}, the encoder's position {enc.pos}, the pins-only X4 decoder on "
            f"the capture {cnt} (no double step); FERR 0",
            f"capture: every step recorded, at the generator's time + {off.pop()} clocks (constant: the "
            f"synchroniser and the start), {len([w for w in words if w])} records, quad burst; position before "
            f"the final step {before}"]


@model_clock(50e6)
def quad_multi(rig, store):
    """A 5000-step random walk in 5 generator plays, R3 read after each."""
    rng = random.Random(2)
    quad_begin(rig)
    pos, d, state, out = 0, 1, 0, []
    for k in range(5):
        enc = Enc(10, SEQ.index(state))
        for _ in range(1000):
            if rng.random() < 0.05:
                d = -d
            enc.step(d, rng.choice([15, 16, 20, 30, 60]))
        quad_play(rig, enc.sched, init=state)
        pos += enc.pos
        state = enc.state()
        r = quad_pos(rig)
        check(r == pos, f"play {k}: R3 {r}, true {pos}")
        out.append(r)
    ferr = quad_ferr(rig)
    quad_end(rig)
    check(not ferr, "FERR set")
    det(store, "quad_multi", pos=out)
    return [f"5 x 1000 steps: R3 after each play {out} = the encoder's position; FERR 0"]


def quad_rate_table(rig, alt, ns):
    res = {}
    for d in (1, -1):
        for n in ns:
            quad_begin(rig)
            enc = Enc(10)
            for _ in range(400):
                enc.step(d, n)
            quad_play(rig, enc.sched, init=0, alt=alt)
            res[(d, n)] = (quad_pos(rig) == enc.pos, quad_ferr(rig))
            quad_end(rig)
    return res


@model_clock(50e6)
def quad_rate(rig, store):
    """Steady rotation, a step every n clocks (n = 10..17), both ways: exact from 14 (forward) / 15 (backward), else missed and flagged."""
    res = quad_rate_table(rig, False, range(10, 18))
    for (d, n), (ok, fe) in res.items():
        lim = 14 if d > 0 else 15
        check((ok, fe) == ((True, 0) if n >= lim else (False, 1)), f"direction {d:+d}, every {n} clocks: "
                                                                   f"exact {ok}, FERR {fe}")
    det(store, "quad_rate", res={f"{d:+d}/{n}": v for (d, n), v in res.items()})
    fmt = lambda d: " ".join(f"{n}:{'ok' if res[(d, n)][0] else 'miss'}{'+F' if res[(d, n)][1] else ''}"
                             for n in range(10, 18))
    return [f"forward  (clocks per step: result) {fmt(1)}",
            f"backward (clocks per step: result) {fmt(-1)}",
            f"exact from 14 clocks forward, 15 backward ({rig.f_clk / 14 / 1e6:.2f} / {rig.f_clk / 15 / 1e6:.2f} M "
            f"steps/s); faster, missed steps and FERR set every time (400 steps each)"]


@model_clock(50e6)
def quad_glitch(rig, store):
    """1- and 3-clock glitches move nothing; a double step sets FERR and is not counted; counting goes on."""
    quad_begin(rig)
    enc = Enc(10)
    for _ in range(10):
        enc.step(1, 30)
    s = enc.state()
    enc.put(s ^ 1, 1)
    enc.put(s, 40)
    enc.put(s ^ 2, 3)
    enc.put(s, 40)
    quad_play(rig, enc.sched, init=0)
    p1, f1 = quad_pos(rig), quad_ferr(rig)
    enc2 = Enc(10, enc.phase)
    enc2.phase = (enc2.phase + 2) % 4
    enc2.put(enc2.state(), 40)
    quad_play(rig, enc2.sched, init=s)
    p2_, f2 = quad_pos(rig), quad_ferr(rig)
    enc3 = Enc(10, enc2.phase)
    for _ in range(7):
        enc3.step(-1, 30)
    quad_play(rig, enc3.sched, init=enc2.state())
    p3 = quad_pos(rig)
    quad_end(rig)
    check((p1, f1) == (10, 0), f"after the glitches: R3 {p1}, FERR {f1}")
    check((p2_, f2) == (10, 1), f"after the double step: R3 {p2_}, FERR {f2}")
    check(p3 == 3, f"after 7 steps back: R3 {p3}")
    det(store, "quad_glitch", res=[p1, f1, p2_, f2, p3])
    return [f"10 steps, a 1-clock pulse on A and a 3-clock pulse on B: R3 {p1}, FERR {f1}; both lines at once: "
            f"R3 {p2_}, FERR {f2}; 7 steps back: R3 {p3}"]


@model_clock(50e6)
def quad_wrap(rig, store):
    """40000 steps forward at 14 clocks each (40 plays): R3 wraps to 40000 - 65536."""
    quad_begin(rig)
    state = 0
    for _ in range(40):
        enc = Enc(10, SEQ.index(state))
        for _ in range(1000):
            enc.step(1, 14)
        quad_play(rig, enc.sched, init=state)
        state = enc.state()
    pos, ferr = quad_pos(rig), quad_ferr(rig)
    quad_end(rig)
    check(pos == 40000 - 65536 and not ferr, f"R3 {pos}, FERR {ferr}")
    det(store, "quad_wrap", pos=pos)
    return [f"40000 steps at 14 clocks ({rig.f_clk / 14 / 1e6:.2f} M steps/s): R3 = {pos} = 40000 - 65536; FERR 0"]


@model_clock(50e6)
def quad_async(rig, store):
    """The generator on the 45.93 MHz alternate clock (phase walking against protoemu's): rate limits and a random walk."""
    res = quad_rate_table(rig, True, range(11, 17))
    lines = []
    for (d, n), (ok, fe) in res.items():
        lim = 14 if d > 0 else 15
        want = (True, 0) if n * ALT >= lim else (False, 1)
        check((ok, fe) == want, f"direction {d:+d}, every {n} alternate cycles ({n * ALT:.2f} clocks): "
                                f"exact {ok}, FERR {fe}")
    fmt = lambda d: " ".join(f"{n}({n * ALT:.1f}):{'ok' if res[(d, n)][0] else 'miss'}{'+F' if res[(d, n)][1] else ''}"
                             for n in range(11, 17))
    lines += [f"forward  (alternate cycles (clocks): result) {fmt(1)}",
              f"backward (alternate cycles (clocks): result) {fmt(-1)}",
              "exact while the mean step is at least the loop (14 forward, 15 backward clocks), missed and flagged "
              "below, as for synchronous steps"]
    rng = random.Random(3)
    quad_begin(rig)
    enc, d = Enc(10), 1
    for _ in range(1000):
        if rng.random() < 0.05:
            d = -d
        enc.step(d, rng.choice([15, 16, 20, 30, 60]))
    quad_play(rig, enc.sched, init=0, alt=True)
    pos, ferr = quad_pos(rig), quad_ferr(rig)
    quad_end(rig)
    check(pos == enc.pos and not ferr, f"asynchronous random walk: R3 {pos}, true {enc.pos}, FERR {ferr}")
    det(store, "quad_async", res={f"{d:+d}/{n}": v for (d, n), v in res.items()}, pos=pos)
    lines.append(f"random walk, 1000 steps 15..60 alternate cycles apart (16.3..65.3 clocks): R3 {pos} = the "
                 f"encoder's {enc.pos}, FERR 0")
    if rig.model:
        lines.append("(model: alternate-clock times rounded up to protoemu clocks; no phase walk)")
    return lines


# ================================================================ main

GROUPS = {
    "dali": [dali_forward, dali_query, dali_tolerance, dali_violations, dali_collision, dali_busy],
    "modbus": [modbus_published, modbus_reqresp, modbus_lengths, modbus_long, modbus_tolerance, modbus_errors,
               modbus_odd],
    "midi": [midi_tx_capture, midi_rx_stream, midi_rx_negative, midi_rx_fifo_full, midi_loopback, midi_port],
    "quad": [quad_walk, quad_multi, quad_rate, quad_glitch, quad_wrap, quad_async],
}
FUNCS = {fn.__name__: fn for fns in GROUPS.values() for fn in fns}


def run_all(rig, names, store):
    results = []
    for g in names:
        for fn in GROUPS.get(g, [FUNCS.get(g)]):
            rig.model_clock(fn.model_f)
            print(f"[{fn.__name__}] {fn.__doc__.split(chr(10))[0]}", flush=True)
            t = time.monotonic()
            try:
                for line in fn(rig, store):
                    print("  " + line, flush=True)
                print(f"  PASS ({time.monotonic() - t:.1f} s)", flush=True)
                results.append((g, fn.__name__, "PASS"))
            except Exception as e:  # report and continue
                print(f"  FAIL: {type(e).__name__}: {e}", flush=True)
                results.append((g, fn.__name__, "FAIL"))
                try:
                    finish(rig)
                except Exception as e2:
                    print(f"  (cleanup: {type(e2).__name__}: {e2})", flush=True)
    return results


def compare_main(a, b):
    da, db = json.loads(pathlib.Path(a).read_text()), json.loads(pathlib.Path(b).read_text())
    ma, mb = da.pop("_meta", {}), db.pop("_meta", {})
    da.pop("_captures", None)
    db.pop("_captures", None)
    print(f"{a}: {'model' if ma.get('model') else 'hardware'} {ma.get('utc')}; "
          f"{b}: {'model' if mb.get('model') else 'hardware'} {mb.get('utc')}")
    for k in sorted(set(da) | set(db)):
        if k not in da or k not in db:
            print(f"  {k}: only in {'the first' if k in da else 'the second'}")
            continue
        same = [s for s in da[k] if s in db[k] and da[k][s] == db[k][s]]
        diff = [s for s in da[k] if s in db[k] and da[k][s] != db[k][s]]
        print(f"  {k}: {'equal' if not diff else 'DIFFERENT in ' + ', '.join(diff)}"
              f"{'' if not same or not diff else ' (equal: ' + ', '.join(same) + ')'}")


def sigrok_main(path):
    from pe import sigrok
    if sigrok.cli() is None:
        sys.exit(sigrok.INSTALL_HINT)
    d = json.loads(pathlib.Path(path).read_text())
    meta = d.get("_meta", {})
    caps = d.get("_captures", {})
    print(f"captures from {path} ({'model' if meta.get('model') else 'hardware'}, {meta.get('utc')})")
    for name, r in caps.items():
        try:
            if name.startswith("dali"):
                tr = sigrok.Trace()
                pf.to_trace(tr, [(t + 1e6, v) for t, v in r["bus"]], "dali", r["t_end"] + 1e6)
                got = sigrok.dali(tr, "dali", res_ps=100_000)
                print(f"[{name}] sigrok dali: {[(k, b.hex()) for _, k, b in got]}")
            elif name.startswith("modbus"):
                baud = r["baud"]
                de = r["de"]
                lvl = lambda t: [v for tt, v in de if tt <= t][-1]
                client, server = [(0.0, 1)], [(0.0, 1)]
                for t, v in r["line"][1:]:
                    dst = client if lvl(t) else server
                    if v != dst[-1][1]:
                        dst.append((t, v))
                    other = server if dst is client else client
                    if other[-1][1] != 1:
                        other.append((t, 1))
                tr = sigrok.Trace()
                pf.to_trace(tr, [(t + 1e6, v) for t, v in client], "client")
                pf.to_trace(tr, [(t + 1e6, v) for t, v in server], "server")
                tr.end(round((r["t_end"] + 30e6) * 1000))      # idle after the capture: frames close at t3.5
                data, warn = sigrok.uart_parity(tr, "client", baud, res_ps=100_000)
                got = sigrok.modbus(tr, "client", "server", baud, res_ps=100_000)
                cs = [t for _, t in got["cs"] if t.startswith("Function") or "CRC" in t]
                sc = [t for _, t in got["sc"] if t.startswith("Function") or "CRC" in t]
                print(f"[{name}] sigrok uart (even parity) client bytes {bytes(b for _, b in data).hex()} {warn}")
                print(f"[{name}] sigrok modbus: client->server {cs}; server->client {sc}")
            elif name.startswith("midi_tx"):
                tr = sigrok.Trace()
                pf.to_trace(tr, r["tx"], "midi", r["t_end"])
                print(f"[{name}] sigrok midi: {sigrok.midi(tr, 'midi', res_ps=100_000)}")
            elif name == "quad_walk":
                tr = sigrok.Trace()
                pf.to_trace(tr, r["a"], "a")
                pf.to_trace(tr, r["b"], "b")
                tr.end(round(r["t_end"] * 1000))
                counts = sigrok.graycode(tr, ["a", "b"], res_ps=10_000)
                print(f"[{name}] sigrok graycode: {len(counts)} count changes, last {counts[-1]} "
                      f"(the firmware's R3 {r['pos']} after one more forward step: {r['pos'] - 1} expected)")
        except Exception as e:
            print(f"[{name}] sigrok: {type(e).__name__}: {e}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("groups", nargs="*", default=list(GROUPS))
    ap.add_argument("--model", action="store_true", help="the cycle model instead of the board")
    ap.add_argument("--save", help="keep captures and results (JSON)")
    ap.add_argument("--plans", help="pre-timing runs: reused from / added to this JSON file")
    a = ap.parse_args()
    if a.groups[:1] == ["compare"]:
        return compare_main(*a.groups[1:3])
    if a.groups[:1] == ["sigrok"]:
        return sigrok_main(a.groups[1])
    bad = [g for g in a.groups if g not in GROUPS and g not in FUNCS]
    if bad:
        ap.error(f"unknown group(s) {bad}; choose from {list(GROUPS)} or {list(FUNCS)}")
    if a.plans and pathlib.Path(a.plans).exists():
        PLANS["data"] = json.loads(pathlib.Path(a.plans).read_text())
    store = {} if a.save else None
    if a.model:
        rig = ModelRig()
        print("cycle model (pe.sim) with pe_lab's generator modelled; per-run model clocks as noted", flush=True)
    else:
        rig = HwRig()
        i = rig.ident
        print(f"bitstream RTL revision {i['rtl_rev'][:12]}{' (dirty)' if i['dirty'] else ''}, built {i['build_utc']} "
              f"UTC, digest {i['digest']}, clk_div {i['clk_div']}", flush=True)
        print(f"protoemu clk measured {rig.f_meas / 1e6:.5f} MHz over 2 s; defines and CLKDIVs derived for "
              f"{rig.f_clk / 1e6:.5f} MHz", flush=True)
    try:
        results = run_all(rig, a.groups, store)
    finally:
        rig.close()
    print(f"pre-timing runs: {PLANS['made']} on the model now, {PLANS['used']} reused from {a.plans}")
    if a.plans and PLANS["made"]:
        pathlib.Path(a.plans).write_text(json.dumps(PLANS["data"]))
    if store is not None:
        store["_meta"] = {"model": rig.model, "f_clk": rig.f_clk, "f_meas": rig.f_meas, "ident": rig.ident,
                          "results": results, "utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())}
        pathlib.Path(a.save).write_text(json.dumps(store))
        print(f"saved {a.save}")
    print("summary: " + ", ".join(f"{n} {s}" for _, n, s in results))
    nfail = sum(1 for r in results if r[2] != "PASS")
    print("ALL PASS" if not nfail else f"{nfail} run(s) FAILED")
    sys.exit(1 if nfail else 0)


if __name__ == "__main__":
    main()
