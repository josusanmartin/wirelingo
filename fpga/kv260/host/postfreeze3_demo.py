#!/usr/bin/env python3
"""The third round of post-freeze firmwares (docs/post-freeze-protocols.md:
cec, iso7816 + iso7816_clk, smbus_host, rc5_tx, rc5_rx) on the KV260 build
of the chip RTL. Run as root on the Kria with the protoemu app (pe_lab)
loaded. Nothing leaves the FPGA: every line is a virtual pin or a uio pad
that a core or pe_lab's fabric drives; no PMOD pin is enabled.

    python3 fpga/kv260/host/postfreeze3_demo.py --model [groups] [--save m3.json] [--plans plans3.json]
    sudo python3 -u fpga/kv260/host/postfreeze3_demo.py [cec iso smbus rc5] [--save pf3.json] [--plans plans3.json]
    python3 fpga/kv260/host/postfreeze3_demo.py compare pf3.json m3.json     # off the board
    python3 fpga/kv260/host/postfreeze3_demo.py sigrok pf3.json              # off the board

Lines (uio pads; uio7 is the pattern generator's start trigger):
* CEC: the bus is uio0. cec (core 0) drives it open drain and reads it; the
  generator is the rest of the bus (released high, other devices' frames,
  a follower's ACKs). A released open-drain pin reads what the fabric
  drives (kv260_top: uio_in = uio_oe ? uio_out : generator): the pad is the
  wired-AND of both.
* SMBus: SDA uio0, SCL uio1, both open drain, the same wired-AND with the
  generator, which plays the device (its ACKs, read data and SCL stretching).
* ISO 7816: I/O uio0 (open drain, wired-AND with the generator playing the
  card), RST uio1 (core 0), CLK uo1 (core 1, iso7816_clk). Both cores are
  the reader, so la_capture cannot run beside it: the reader's I/O is
  recorded by the host polling the GPIO (its drive), or, in separate runs,
  by la_capture on core 1 in place of the clock core (the reader never
  reads CLK; the card's answers were pre-timed with CLK running).
  iso_clk measures CLK with pe_lab's SPI device (CLK on uio2).
* RC-5: rc5_tx on V0, recorded by la_capture; rc5_rx on uio0 from the generator.

Far ends that answer (the CEC devices, the SMBus device, the card) are
pre-timed, as in postfreeze2_demo: each scenario first runs on the cycle
model with the pins-only model of the far end in the loop (pe.postfreeze3:
CecNode, SmbusDevice, IsoCard, as test/model/test_{cec,smbus,iso7816}.py
use them); the far end's own edges from that run, relative to the trigger,
are what the generator plays. The firmware's start on the board differs
from the model's by under a tick (and the model's synchroniser is 2 of its
slower clocks), so answers land within about a tick of the model's times;
the models get a small output delay (CEC 30 us, SMBus 2 us) so that a far
end never acts before the board's edge it answers. The firmware's records
on the board must equal the model run's, word for word.

--model runs the same code on the cycle model at a slower clock where the
firmware's ticks allow (it checks this script, not the FPGA). --plans keeps
the pre-timing runs (keyed by everything they depend on). --save keeps
captures and results; `compare` and `sigrok` as in postfreeze2_demo.
"""
import argparse
import gc
import json
import math
import pathlib
import random
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import postfreeze_demo as pfd  # noqa: E402  (sets up sys.path for tools/)
from postfreeze_demo import (FIFO, STATUS, PG, SPI, TRIG, PRE, V0, HwRig, ModelRig, Capture,  # noqa: E402
                             check, fw, load, setup, finish, model_clock, split_long, trigger_preamble)
import postfreeze2_demo as pf2d  # noqa: E402
from postfreeze2_demo import PLANS, TRIG_S, rd, rxn, det, keep, span, ctrl, plan_key  # noqa: E402
from pe import host, sim  # noqa: E402
from pe import postfreeze as pf  # noqa: E402
from pe import postfreeze2 as p2  # noqa: E402
from pe import postfreeze3 as p3  # noqa: E402

CORE = lambda c, off: sim.core_reg(c, off)


# ================================================================ scenarios, far ends, pre-timing

class Sc:
    """A run's chip set-up: progs {core: (prog, cfg(f) -> load kwargs)} (core 0
    behind the trigger preamble, all started by one CTRL write), pin
    registers, extra writes after the loads, TX FIFO words for core 0
    (prefill before the start; feed: groups pushed whole while running,
    whenever the FIFO has room), the generator's idle value."""

    def __init__(self, progs, *, owners=None, od=0, pout=0, poe=0, extra=(), prefill=(), feed=(), pg_idle=0):
        self.progs, self.owners, self.od, self.pout, self.poe = progs, owners or {}, od, pout, poe
        self.extra, self.prefill, self.feed, self.pg_idle = list(extra), list(prefill), [list(g) for g in feed], pg_idle

    def start(self):
        return sum(1 << c for c in self.progs)

    def key(self, f):
        return [{c: (p.words, cfg(f)) for c, (p, cfg) in self.progs.items()}, sorted(self.owners.items()), self.od,
                self.pout, self.poe, self.extra, self.prefill, self.feed, self.pg_idle]


def groups_split(groups):
    """TX words as whole groups: the first ones that fit in the FIFO (8) as the
    prefill, the rest fed while running."""
    pre, i = [], 0
    while i < len(groups) and len(pre) + len(groups[i]) <= 8:
        pre += groups[i]
        i += 1
    return pre, groups[i:]


def prep3(rig, sc, *, sched, la=None):
    """Both cores stopped; sc's programs loaded (core 1's replaced by la_capture
    if la = (mask, base, div)); the generator loaded with sched."""
    setup(rig, mode=PG, owners=sc.owners, od=sc.od, pout=sc.pout, poe=sc.poe, pg_init=sc.pg_idle)
    for core, (prog, cfg) in sc.progs.items():
        if la is None or core != 1:
            load(rig, core, prog, **cfg(rig.f_clk))
    rig.write(PRE, trigger_preamble(0))
    rig.write(CORE(0, sim.C_ENTRY), PRE)
    rig.write(sim.A_FCLR, 0xFF)
    for a, v in sc.extra:
        rig.write(a, v)
    if la is not None:
        load(rig, 1, fw("la_capture", MASK=la[0]), in_pins=la[1], clkdiv=la[2] << 8)
    check(len(sc.prefill) <= 8, "at most 8 TX FIFO words before the start")
    if sc.prefill:
        rig.write(FIFO(0), sc.prefill, fixed=True)
    check(len(sched) <= 1024, f"{len(sched)} generator entries (at most 1024)")
    rig.pg_load(sched, init=sc.pg_idle)


class Drv:
    """A far-end driver of the uio bits in mask (1 = released); its edges are
    logged as [(t, value over mask)]. Outputs go through a delay line of
    `delay` ns (the time the device takes to act on what it saw)."""

    def __init__(self, mask, delay=0.0):
        self.mask, self.v, self.e, self.delay, self.raw, self.q = mask, mask, [], delay, mask, []

    def level(self, t):
        return self.v

    def out(self, t, v):
        if v != self.raw:
            self.raw = v
            self.q.append((t + self.delay, v))
        while self.q and self.q[0][0] <= t:
            tq, vq = self.q.pop(0)
            if vq != self.v:
                self.v = vq
                self.e.append((tq, vq))

    def step(self, t, uio, chip):
        pass

    def edges(self):
        return self.e

    def info(self, t0):
        return {}


class Far3:
    """The far end: the per-bit wired-AND of its drivers."""

    def __init__(self, *ds):
        self.ds = ds
        self.mask = 0
        for d in ds:
            self.mask |= d.mask

    def level(self, t):
        v = self.mask
        for d in self.ds:
            v &= d.level(t) | ~d.mask
        return v & self.mask

    def step(self, t, uio, chip):
        for d in self.ds:
            d.step(t, uio, chip)

    def edges(self):
        ev = sorted(((t, i, v) for i, d in enumerate(self.ds) for t, v in d.edges()), key=lambda x: (x[0], x[1]))
        cur = [d.mask for d in self.ds]
        last, out = self.mask, []
        for t, i, v in ev:
            cur[i] = v
            lv = self.mask
            for c, d in zip(cur, self.ds):
                lv &= c | ~d.mask
            lv &= self.mask
            if lv != last:
                out.append((t, lv))
                last = lv
        return out

    def info(self, t0):
        return [d.info(t0) for d in self.ds]


class PlayerD(Drv):
    """A fixed waveform (pe.postfreeze2.Player) on uio[bit]."""

    def __init__(self, player, bit):
        super().__init__(1 << bit)
        self.p, self.bit = player, bit

    def level(self, t):
        return self.p.level(t) << self.bit

    def edges(self):
        return [(t, v << self.bit) for t, v in self.p.edges]


def predict3(name, f_model, sc, *, far_factory, far_key, dur_s, done=None, tail_s=0.0, drain=True):
    """The scenario on the cycle model at f_model, the far end in the loop (its
    level ANDed with the chip's drive per bit, as on the board); core 0's RX
    FIFO drained and its TX FIFO fed as the board's host does. Returns {far:
    the far end's edges [(ns after the trigger, value over mask)], mask, pins:
    [(ns, uio | V0..V7 << 8)] (the pads), drive: [(ns, the chip's own uio drive,
    1 = released or high)], words, info, t_last}."""
    key = plan_key(name, f_model, sc.key(f_model), far_key, round(dur_s, 9), round(tail_s, 9), TRIG_S, "pf3b", drain)
    hit = PLANS["data"].get(name)
    if hit and hit.get("key") == key:
        PLANS["used"] += 1
        return hit
    m = ModelRig(f_model)
    t_trig = round(TRIG_S * f_model)
    prep3(m, sc, sched=[(t_trig, sc.pg_idle | (1 << TRIG))])
    mh, per = m.mh, 1e9 / f_model
    m.write(sim.A_CTRL, sc.start() | (sc.start() << 8))
    c0 = mh.chip.cycle
    m.pg_start()
    t_tr = (c0 + t_trig) * per
    far = far_factory(t_tr)
    base_pg = mh.pg_value
    pins, drive, last = [], [], [0.0]
    last_chg = lambda: last[0]

    def bus_uio():
        t = mh.chip.cycle * per
        v = (base_pg() & ~far.mask) | far.level(t)
        _, out, oe = mh.chip.outputs()
        r = ((oe & out) | (~oe & v)) & 0xFF
        far.step(t, r, mh.chip)
        s = r | (((mh.chip.pin_out >> 24) & 0xFF) << 8)
        if not pins or pins[-1][1] != s:
            if pins and (pins[-1][1] ^ s) & 0xFF7F:
                last[0] = t
            pins.append((t, s))
        d = (~oe | out) & 0xFF
        if not drive or drive[-1][1] != d:
            drive.append((t, d))
        return r
    mh.bus_uio = bus_uio
    words, chunk = [], max(64, round(0.25e-3 * f_model))
    feed = [list(g) for g in sc.feed]
    n_max = c0 + round((TRIG_S + dur_s) * f_model)
    stop = None
    while mh.chip.cycle < (n_max if stop is None else min(stop, n_max)):
        mh.step(chunk)
        if drain:
            words += mh.pop_all(0)
        while feed and mh.tx_level(0) + len(feed[0]) <= 8:
            for w in feed.pop(0):
                mh.push(0, w)
        if stop is None and done is not None and done(words):
            stop = mh.chip.cycle + round(tail_s * f_model)
        if stop is not None and mh.chip.cycle >= stop and pins and (mh.chip.cycle * per - last_chg()) < tail_s * 1e9:
            stop = mh.chip.cycle + round(tail_s * f_model)       # the lines still busy: wait for tail_s of quiet
    if not drain:              # as in a captured run: the records left in the RX FIFO at the end
        words = mh.pop_all(0)
    check(done is None or done(words), f"{name}: the model run did not finish within {dur_s} s: {words}")
    check(not feed, f"{name}: {len(feed)} TX groups never fed")
    rel = [(t - t_tr, s) for t, s in pins]
    chg = [t for (t, s), (_, s0) in zip(rel[1:], rel) if (s ^ s0) & 0xFF7F]
    t_stop = mh.chip.cycle * per - t_tr
    check(not chg or t_stop - max(chg) >= 1.5e6, f"{name}: the model run ended {(t_stop - max(chg)) / 1e6:.2f} ms "
                                                  f"after the last edge (the lines still busy?)")
    plan = {"key": key, "f_model": f_model, "far": [(t - t_tr, v) for t, v in far.edges()], "mask": far.mask,
            "pins": rel, "drive": [(t - t_tr, d) for t, d in drive], "words": words, "info": far.info(t_tr),
            "t_last": max(chg) if chg else 0.0, "t_stop": t_stop, "fset": mh.chip.flags}
    PLANS["data"][name] = plan
    PLANS["made"] += 1
    return plan


def cap_plan(name, f_model, sc, plan, far_factory, far_key):
    """The pre-timing for a captured run, where the host cannot drain core 0
    during the burst: the drained plan if its records fit the RX FIFO (8), else
    the same scenario on the model with the RX FIFO left full (a firmware's
    'FIFO full' path may take another tick, so the times can differ)."""
    if len(plan["words"]) <= 8:
        return plan
    return predict3(name + "_full", f_model, sc, far_factory=far_factory, far_key=far_key,
                    dur_s=plan["t_last"] / 1e9 + 5e-3, drain=False)


def far_sched3(rig, plan, pg_idle):
    """Generator entries: uio7 rises at TRIG_S, then the far end's bits follow its edges."""
    f, mask = rig.f_clk, plan["mask"]
    t_trig = round(TRIG_S * f)
    v = pg_idle | (1 << TRIG)
    out = [(t_trig, v)]
    for t, lvl in plan["far"]:
        c = t_trig + max(1, round(t * f / 1e9))
        v = (v & ~mask) | lvl
        if c <= out[-1][0]:
            check(len(out) > 1, "a far-end edge before the trigger")
            out[-1] = (out[-1][0], v)
        else:
            out.append((c, v))
    return split_long(out, out[-1][0] + 1)


def run_capture3(rig, sc, plan, *, la_mask, dur_s, la_base=0, la_div=1):
    """sc on the board with la_capture on core 1, one READ_FIXED burst draining it
    for dur_s. Returns (Capture, core 0's RX words, FSET)."""
    burst = pfd.burst_plan(rig, dur_s)
    if rig.model and burst[1] == 1:    # the model's slow clock: a burst as long as the board's, not 4096 words
        burst = (min(4096, math.ceil(dur_s * rig.f_clk / 32) + 8), 1, False)
    prep3(rig, sc, sched=far_sched3(rig, plan, sc.pg_idle), la=(la_mask, la_base, la_div))
    ctrl(rig, 0b11)
    rig.pg_start()
    words = rig.burst(1, *burst)
    rig.wait(1.5 * 65536 * la_div / rig.f_clk)
    words += rd(rig, FIFO(1), rxn(rig.read(STATUS(1))))
    st0 = rig.read(STATUS(0))
    recs = rd(rig, FIFO(0), rxn(st0))
    fset = rig.read(sim.A_FSET)
    drive_end = uio_drive(rig)
    st = finish(rig)
    c = Capture(words, la_mask, la_div * 1e9 / rig.f_clk)
    check(c.cap.exact, f"the capture has {len(c.cap.gaps)} gap(s)")
    check(st0 & 15 == 0, f"{st0 & 15} TX FIFO words left: the burst ended too soon?")
    c.info = {"records": sum(1 for w in words if w), "burst": list(burst), "status": st, "uio_end": drive_end}
    return c, recs, fset


def run_poll3(rig, sc, plan, *, until, timeout_s, after_s=0.01, step_s=0.5e-3):
    """sc on the board, core 0's RX FIFO polled and its TX FIFO fed until
    until(words), the feed is done and the generator has finished.
    Returns (words, FSET)."""
    prep3(rig, sc, sched=far_sched3(rig, plan, sc.pg_idle))
    feed = [list(g) for g in sc.feed]
    ctrl(rig, sc.start())
    rig.pg_start()
    words, t_model = [], 0.0
    t0 = time.monotonic()
    done_at = None
    while True:
        st = rig.read(STATUS(0))
        words += rd(rig, FIFO(0), rxn(st))
        room = 8 - (st & 15)
        while feed and len(feed[0]) <= room:
            g = feed.pop(0)
            rig.write(FIFO(0), g, fixed=True)
            room -= len(g)
        now = t_model if rig.model else time.monotonic() - t0
        if done_at is None and not feed and until(words) and not rig.pg_busy():
            done_at = now
        if done_at is not None and now - done_at >= after_s:
            break
        check(now < timeout_s, f"no end within {timeout_s} s: {words}")
        rig.wait(step_s)
        t_model += step_s
    fset = rig.read(sim.A_FSET)
    finish(rig)
    return words, fset


def cap_log(c):
    """The capture's states [(ns after the captured trigger, uio)], and its end (ns after the trigger)."""
    trig = next(t for t, v in c.edges(TRIG) if v == 1)
    lg = c.log()
    first = [s for t, s in lg if t <= trig][-1]
    return [(0.0, first & 0x7F)] + [(t - trig, s & 0x7F) for t, s in lg if t > trig], c.t_end - trig


def edges_vs(board, model):
    """Board - model times (ns) of matching edges [(t, v)], or None if the
    edge lists differ in length or levels."""
    if len(board) != len(model) or [v for _, v in board] != [v for _, v in model]:
        return None
    return [b[0] - m[0] for b, m in zip(board, model)]


def bus_vs_plan(log, plan, bit, t_end):
    """One uio bit's edges on the board (log after the trigger) against the plan's pads."""
    be = [x for x in pf.bit_edges(log, bit) if 0 < x[0] < t_end]
    me = [x for x in pf.bit_edges(plan["pins"], bit) if 0 < x[0] < t_end]
    return edges_vs(be, me), len(be)


def same_words(name, got, plan, n=None):
    want = plan["words"] if n is None else plan["words"][:n]
    check(got == want, f"{name}: the firmware's records differ from the model run's:\n  board {[hex(w) for w in got]}"
                       f"\n  model {[hex(w) for w in want]}")


# ================================================================ HDMI-CEC

CEC = 0
OURS = 4
CEC_F = 200e3                    # model clock: CLKDIV 2, 1 tick = 10 us (test_cec's)
BIT = p3.CEC_BIT
CEC_REACT = 30e3                 # the device models act 30 us after what they see
CEC_MASK = (1 << CEC) | (1 << TRIG)
CLK_NS = lambda rig: 1e9 / rig.f_clk


def cec_sc(prefill=()):
    prog = fw("cec", **p3.cec_defines(CEC, 7, 6))
    return Sc({0: (prog, lambda f: dict(clkdiv=p3.cec_clkdiv(f)))}, od=1 << CEC, pout=1 << CEC,
              extra=[(CORE(0, sim.C_CRCCTRL), p3.CEC_CRCCTRL), (CORE(0, sim.C_CRC), OURS)], prefill=prefill,
              pg_idle=1 << CEC)


class CecD(Drv):
    """A CecNode on the bus; with `with_us` = (blocks, delay, sft) it starts
    that frame `delay` after the chip's first fall (an initiator starting
    with us, for arbitration)."""

    def __init__(self, node, t_idle=None, with_us=None):
        super().__init__(1 << CEC, CEC_REACT)
        self.node, self.with_us = node, with_us
        if t_idle is not None:
            node.t_idle = t_idle

    def step(self, t, uio, chip):
        if self.with_us and not (chip.pin_out >> CEC) & 1:
            blocks, delay, sft = self.with_us
            self.node.send(blocks, at=t + delay, sft_bits=sft)
            self.with_us = None
        self.out(t, self.node.step(t, (uio >> CEC) & 1) << CEC)

    def info(self, t0):
        n = self.node
        return {"heard": [(round(f["t"] - t0), f["blocks"].hex(), f["ack"], f["error"]) for f in n.frames],
                "results": [(round(t - t0), k, r) for t, k, r in n.results]}


def cec_words(frames):
    return sum((p3.cec_words(b, s) for b, s in frames), [])


def cec_case(rig, store, name, frames, far_factory, far_key, *, capture, n_words, dur_s, sc=None):
    """frames [(blocks, sft)] prefilled; the far end pre-timed. Returns (plan, records,
    bus edges or None, t_end or None, capture or None, FSET)."""
    sc = sc or cec_sc(cec_words(frames))
    plan = predict3(name, CEC_F, sc, far_factory=far_factory, far_key=far_key, dur_s=dur_s,
                    done=None if n_words is None else (lambda w: len(w) >= n_words), tail_s=12e-3)
    check(n_words is None or len(plan["words"]) == n_words, f"model run: {len(plan['words'])} record words, "
                                                             f"want {n_words}")
    n_words = len(plan["words"])
    if capture:
        c, recs, fset = run_capture3(rig, sc, plan, la_mask=CEC_MASK,
                                     dur_s=TRIG_S + plan["t_last"] / 1e9 + 8e-3)
        log, _ = cap_log(c)
        t_end = c.t_end - next(t for t, v in c.edges(TRIG) if v == 1)
        e = pf.bit_edges(log, CEC)
        keep(store, name, bus=e, t_end=t_end)
        same_words(name, recs, plan)
        return plan, recs, e, t_end, c, fset
    recs, fset = run_poll3(rig, sc, plan, until=lambda w: len(w) >= n_words, timeout_s=3 + 2 * dur_s, after_s=0.02)
    same_words(name, recs, plan)
    return plan, recs, None, None, None, fset


def cec_model_line(e, plan, t_end, c):
    d, n = bus_vs_plan([(0.0, 1)] + e, plan, CEC, t_end)
    if d is None:
        return "board vs model: the bus's edges differ in number or order"
    return (f"board vs model ({n} bus edges, ns after the trigger): board - model {span(d, '{:+.0f}')} ns "
            f"(1 tick = 10 us; the model's clock 5 us); {c.info['records']} records, burst SCK = clk/{c.info['burst'][1]}")


def cec_ours(dec, rig, acked):
    """Our frames' intervals on the bus, in clocks: start bit low and period,
    data-bit lows and periods (not the ACK slots a follower drives, nor the
    period ending at one, which a follower may start)."""
    ck = CLK_NS(rig)
    st, lows, pers = set(), set(), set()
    for f, ack in zip(dec, acked):
        st.add((round(f["start_lo"] / ck), round(f["start"] / ck)))
        for k, lo in enumerate(f["lows"]):
            if not (ack and k % 10 == 9):
                lows.add(round(lo / ck))
        for k, p in enumerate(f["periods"]):
            if not (ack and k % 10 == 8):
                pers.add(round(p / ck))
    return st, lows, pers


def ack_lows(dec, which):
    """The ACK slots' low times on the bus (us), for frames where which(frame)."""
    return [f["lows"][k] / 1e3 for f in dec if which(f) for k in range(9, len(f["lows"]), 10)]


def tv(addr=0, **timing):
    return p3.CecNode(addr=addr, **timing)


@model_clock(CEC_F)
def cec_send(rig, store):
    """cec: our frames to a follower (the TV model, pre-timed ACKs): a poll, a directed command, a broadcast; captured."""
    frames = [(bytes([0x40]), 5), (bytes([0x40, 0x36]), 5), (bytes([0x4F, 0x82, 0x10, 0x00]), 5)]
    plan, recs, e, t_end, c, fset = cec_case(rig, store, "cec_send", frames, lambda t0: Far3(CecD(tv())), "tv0",
                                             capture=True, n_words=3, dur_s=0.25)
    fr, tx, err = p3.cec_records(recs)
    check(all(r["ok"] and not (r["nack"] or r["lost"]) for r in tx) and [r["blocks"] for r in tx] == [1, 2, 4]
          and not fr and not err, f"results {tx}, frames {fr}, errors {err}")
    dec = p3.cec_decode(e, t_end)
    check([f["blocks"] for f in dec] == [b for b, _ in frames] and not any(f["error"] for f in dec),
          f"bus {[(f['blocks'].hex(), f['error']) for f in dec]}")
    check([f["ack"] for f in dec] == [[0], [0, 0], [1, 1, 1, 1]], f"ACK bits {[f['ack'] for f in dec]}")
    st, lows, pers = cec_ours(dec, rig, [True, True, False])
    tc = p3.cec_clkdiv(rig.f_clk) // 256           # clocks per 10 us tick (500 at 50 MHz)
    check(st == {(370 * tc, 450 * tc)} and lows == {60 * tc, 150 * tc} and pers == {240 * tc},
          f"our timing in clocks: start {st}, lows {sorted(lows)}, periods {sorted(pers)}")
    acks = ack_lows(dec[:2], lambda f: True)
    ends = [f["t"] + f["start"] + sum(f["periods"]) + BIT for f in dec]
    gaps = [b["t"] - a for a, b in zip(ends, dec[1:])]
    check(min(gaps) >= 5 * BIT, f"signal free time {gaps}")
    det(store, "cec_send", recs=recs, acks=[f["ack"] for f in dec])
    return [f"results ok, ok, ok (1, 2, 4 blocks); the bus decoded pins-only: {[f['blocks'].hex() for f in dec]}, "
            f"ACK bits {[f['ack'] for f in dec]} (directed: the follower's 0; broadcast: nobody rejected)",
            f"our drive, in clocks: start bit {sorted(st)}, data-bit lows {sorted(lows)}, bits {sorted(pers)} apart "
            f"(3.7 / 4.5 / 0.6 / 1.5 / 2.4 ms exactly); the follower's ACK slots low {span(acks)} us; "
            f"frame to frame {span([g / 1e6 for g in gaps], '{:.3f}')} ms after the previous frame's end "
            f"(5 bit periods: 12.0)",
            cec_model_line(e, plan, t_end, c)]


@model_clock(CEC_F)
def cec_send16(rig, store):
    """cec: a 16-block frame (CEC's longest) to the TV model, captured."""
    blocks = bytes([0x40, 0x47]) + b"Wirelingo dev.."[:14]
    plan, recs, e, t_end, c, fset = cec_case(rig, store, "cec_send16", [(blocks, 5)],
                                             lambda t0: Far3(CecD(tv())), "tv0", capture=True, n_words=1, dur_s=0.45)
    fr, tx, err = p3.cec_records(recs)
    check(len(tx) == 1 and tx[0]["ok"] and tx[0]["blocks"] == 16, f"result {tx}")
    dec = p3.cec_decode(e, t_end)
    check(len(dec) == 1 and dec[0]["blocks"] == blocks and dec[0]["ack"] == [0] * 16 and not dec[0]["error"],
          f"bus {[(f['blocks'], f['ack'], f['error']) for f in dec]}")
    check(dec[0]["eom"] == [0] * 15 + [1], f"EOM bits {dec[0]['eom']}")
    st, lows, pers = cec_ours(dec, rig, [True])
    tc = p3.cec_clkdiv(rig.f_clk) // 256           # clocks per 10 us tick (500 at 50 MHz)
    check(st == {(370 * tc, 450 * tc)} and lows == {60 * tc, 150 * tc} and pers == {240 * tc},
          f"our timing in clocks: start {st}, lows {sorted(lows)}, periods {sorted(pers)}")
    det(store, "cec_send16", recs=recs)
    return [f"{blocks.hex()} ({len(blocks)} blocks, {(dec[0]['start'] + sum(dec[0]['periods']) + BIT) / 1e6:.1f} ms): "
            f"result ok; on the bus every block acknowledged, EOM only on the last; our intervals {sorted(st)}, "
            f"{sorted(lows)}, {sorted(pers)} clocks", cec_model_line(e, plan, t_end, c)]


@model_clock(CEC_F)
def cec_receive(rig, store):
    """cec as a follower: the TV model (played by the generator) sends to us, to address 5 and a broadcast; captured."""
    sent = [bytes([0x04, 0x8F]), bytes([0x05, 0x8F]), bytes([0x0F, 0x36]), bytes([0x04, 0x44, 0x41])]

    def far(t0):
        n = tv()
        for b in sent:
            n.send(b)
        return Far3(CecD(n, t_idle=t0))
    plan, recs, e, t_end, c, fset = cec_case(rig, store, "cec_receive", [], far, "tv sends 4", capture=True,
                                             n_words=8, dur_s=0.35)
    fr, tx, err = p3.cec_records(recs)
    check([f["blocks"].hex() for f in fr] == ["048f", "05", "0f36", "044441"], f"frames {fr}")
    check([f["ack"] for f in fr] == [[0, 0], [1], [1, 1], [0, 0, 0]] and [f["to_us"] for f in fr] ==
          [True, False, False, True] and not tx and not err, f"records {fr} {tx} {err}")
    check(not (fset >> 7) & 1, "FOV set")
    dec = p3.cec_decode(e, t_end)
    check([(f["blocks"].hex(), f["ack"]) for f in dec] == [(f["blocks"].hex(), f["ack"]) for f in fr],
          f"bus {[(f['blocks'].hex(), f['ack']) for f in dec]}")
    acks = ack_lows(dec, lambda f: f["blocks"][0] & 15 == OURS)
    check(all(1500 <= a <= 1520 for a in acks), f"our ACK lows {acks} us")
    det(store, "cec_receive", recs=recs)
    res = [r for _, _, r in plan["info"][0]["results"]]
    return [f"records: {[(f['blocks'].hex(), f['ack'], 'to us' if f['to_us'] else '-') for f in fr]} (the TV model "
            f"stopped after the header to 5: nobody acknowledged); the same frames and ACK bits decoded on the bus",
            f"our ACKs on the bus: low {span(acks, '{:.2f}')} us from the TV's fall (1.5 ms; our detection 0..1 tick "
            f"+ 2 clocks); the TV model's verdicts in the pre-timing run {res}",
            cec_model_line(e, plan, t_end, c)]


@model_clock(CEC_F)
def cec_tolerance(rig, store):
    """Initiators at the ends of the receiver's ranges (start 3.5..3.9 / 4.3..4.7 ms, lows 0.4..0.8 / 1.3..1.7, bits 2.05..2.75 ms); polled."""
    timings = [dict(start_lo=3.5e6, start=4.3e6), dict(start_lo=3.9e6, start=4.7e6), dict(lo1=0.4e6, lo0=1.3e6),
               dict(lo1=0.8e6, lo0=1.7e6), dict(bit=2.05e6), dict(bit=2.75e6),
               dict(bit=2.75e6, lo1=0.8e6, lo0=1.7e6, start_lo=3.9e6, start=4.7e6),
               dict(bit=2.05e6, lo1=0.4e6, lo0=1.3e6, start_lo=3.5e6, start=4.3e6)]
    sent = [bytes([0x04, 0x00, 0xFF, 0x55, 0xAA]), bytes([0x0F, 0x84, 0x10, 0x00, 0x04])]
    out = []
    for i, tm in enumerate(timings):
        def far(t0, tm=tm):
            n = tv(**tm)
            for b in sent:
                n.send(b)
            return Far3(CecD(n, t_idle=t0))
        _, recs, *_ = cec_case(rig, store, f"cec_tol_{i}", [], far, repr(tm), capture=False, n_words=10, dur_s=0.32)
        fr, tx, err = p3.cec_records(recs)
        check([f["blocks"] for f in fr] == sent and fr[0]["ack"] == [0] * 5 and not err and not tx,
              f"{tm}: {fr} {err}")
        out.append(", ".join(f"{k} {v / 1e6:g}" for k, v in tm.items()) + " ms: both frames received, the "
                   "directed one acknowledged")
    det(store, "cec_tolerance", n=len(timings))
    return out


@model_clock(CEC_F)
def cec_errors(rig, store):
    """Negative controls (generator): a 3.0 ms start bit, 5 ms low, a bit 1.9 ms after the last, a partial block, a 3.6 ms low bit; then a good frame; polled."""
    bad_start = [(0, 3.0e6), (1, 1.5e6)] + p3.cec_wave(b"\x04\x8f")[2:]
    short = p3.cec_wave(b"\x04\x8f", bit=lambda i: 1.9e6 if i == 5 else 2.4e6)
    part = p3.cec_wave(b"\x04", bits=p3.cec_wave_bits(b"\x04\x8f")[:15])
    lowbit = p3.cec_wave(b"\x04\x8f")
    k = 2 + 2 * 12
    lowbit[k] = (0, 3.6e6)
    lowbit[k + 1] = (1, 0.4e6)

    def far(t0):
        pl = p2.Player()
        t = t0 + 5e6
        for w in [bad_start, [(0, 5e6)], short, part, lowbit, p3.cec_wave(b"\x04\x8f")]:
            t = pl.schedule(t, w) + 25e6
        return Far3(PlayerD(pl, CEC))
    _, recs, *_ = cec_case(rig, store, "cec_errors", [], far, "errors", capture=False, n_words=None, dur_s=0.45)
    fr, tx, err = p3.cec_records(recs)
    check([x["reason"] for x in err] == ["short", "partial", "low"], f"errors {err}")
    check([f["blocks"] for f in fr] == [b"\x04", b"\x04", b"\x04\x8f"] and fr[-1]["ack"] == [0, 0] and not tx,
          f"frames {fr}")
    det(store, "cec_errors", recs=recs)
    return [f"a 3.0 ms start bit and 5 ms low ignored; errors {[(x['reason'], x['bits_left']) for x in err]}; frames "
            f"{[f['blocks'].hex() for f in fr]} (the last acknowledged)"]


@model_clock(CEC_F)
def cec_arb_lost(rig, store):
    """Arbitration lost: the TV model starts 0x04 0x8F with our 10-block frame (0x40): we stop, receive and ACK it; the retry (3 bit periods) goes through; captured."""
    osd = bytes([0x40, 0x47]) + b"Wireling"
    far = lambda t0: Far3(CecD(tv(), with_us=(bytes([0x04, 0x8F]), 10e3, 5)))
    plan, recs, e, t_end, c, fset = cec_case(rig, store, "cec_arb_lost", [(osd, 5), (osd, 3)], far, "tv with us",
                                             capture=True, n_words=4, dur_s=0.45)
    fr, tx, err = p3.cec_records(recs)
    check(len(tx) == 2 and tx[0]["lost"] and not tx[0]["ok"] and tx[1]["ok"], f"results {tx}")
    check(len(fr) == 1 and fr[0]["blocks"] == b"\x04\x8f" and fr[0]["to_us"] and fr[0]["ack"] == [0, 0] and not err,
          f"frames {fr}")
    dec = p3.cec_decode(e, t_end)
    check([f["blocks"] for f in dec] == [b"\x04\x8f", osd] and not any(f["error"] for f in dec),
          f"bus {[(f['blocks'].hex(), f['error']) for f in dec]}")
    check(dec[1]["ack"] == [0] * 10, f"the TV's ACKs of our retry {dec[1]['ack']}")
    end0 = dec[0]["t"] + dec[0]["start"] + sum(dec[0]["periods"]) + BIT
    det(store, "cec_arb_lost", recs=recs)
    return [f"ours lost at the header (we sent 1, the TV 0); the TV's 048f received by us with ACK [0, 0], 'to us'; "
            f"our 2 unsent data words dropped; the retry (3 bit periods) went through, every block acknowledged, "
            f"{(dec[1]['t'] - end0) / 1e6:.3f} ms after the TV's frame (3 bit periods: 7.2)",
            cec_model_line(e, plan, t_end, c)]


@model_clock(CEC_F)
def cec_arb_won(rig, store):
    """Arbitration won: a device at address 8 starts 0x80 with our 0x40 and loses at the first bit; the TV acknowledges ours; captured."""
    far = lambda t0: Far3(CecD(tv()), CecD(tv(addr=None), with_us=(bytes([0x80, 0x36]), 10e3, 5)))
    plan, recs, e, t_end, c, fset = cec_case(rig, store, "cec_arb_won", [(bytes([0x40, 0x36]), 5)], far,
                                             "rec with us", capture=True, n_words=1, dur_s=0.12)
    fr, tx, err = p3.cec_records(recs)
    check(len(tx) == 1 and tx[0]["ok"] and not fr and not err, f"result {tx} {fr} {err}")
    dec = p3.cec_decode(e, t_end)
    check([f["blocks"] for f in dec] == [b"\x40\x36"] and dec[0]["ack"] == [0, 0], f"bus {dec}")
    other = plan["info"][1]["results"]
    check([r for _, _, r in other] == ["lost"], f"the other initiator in the model run: {other}")
    det(store, "cec_arb_won", recs=recs)
    return [f"ours ok; on the bus one frame, 4036, acknowledged; the other initiator's start bit overlapped ours "
            f"(bus start bit low {dec[0]['start_lo'] / 1e6:.3f} ms) and it lost at its first bit in the pre-timing run",
            cec_model_line(e, plan, t_end, c)]


@model_clock(CEC_F)
def cec_free_time(rig, store):
    """Signal free time: the TV's broadcast starts 2 ms after ours was queued; ours waits 5 bit periods after it, the next 7 after ours; captured."""
    def far(t0):
        n = tv()
        n.send(bytes([0x0F, 0x87, 0x00, 0x00, 0x01]), at=t0 + 2e6)
        return Far3(CecD(n))
    plan, recs, e, t_end, c, fset = cec_case(rig, store, "cec_free_time", [(bytes([0x40, 0x8F]), 5),
                                                                           (bytes([0x40, 0x46]), 7)], far,
                                             "tv broadcast 2ms", capture=True, n_words=7, dur_s=0.3)
    fr, tx, err = p3.cec_records(recs)
    check([f["blocks"].hex() for f in fr] == ["0f87000001"] and len(tx) == 2 and all(t["ok"] for t in tx),
          f"records {fr} {tx}")
    dec = p3.cec_decode(e, t_end)
    check([f["blocks"].hex() for f in dec] == ["0f87000001", "408f", "4046"], f"bus {[f['blocks'].hex() for f in dec]}")
    ends = [f["t"] + f["start"] + sum(f["periods"]) + BIT for f in dec]
    gaps = [b["t"] - x for x, b in zip(ends, dec[1:])]
    check(5 * BIT <= gaps[0] <= 5 * BIT + 1.0e6 and 7 * BIT <= gaps[1] <= 7 * BIT + 0.4e6, f"gaps {gaps}")
    det(store, "cec_free_time", recs=recs)
    return [f"the TV's broadcast received; ours {(gaps[0]) / 1e6:.3f} ms after its end (5 bit periods: 12.0), "
            f"the next {(gaps[1]) / 1e6:.3f} ms after ours (7: 16.8)", cec_model_line(e, plan, t_end, c)]


# ================================================================ SMBus

SDA, SCL = 0, 1
SMB_F = 4 * 1e9 / p3.SMBUS_TICK  # model clock: CLKDIV 4, tick for tick 50 MHz / 56
SMB_CAP_F = 4 * SMB_F            # model clock of the captured runs (la_capture's drain at 32 clocks a word keeps up)
SMB_DELAY = 2000.0              # the device's outputs follow what it saw 2 us later (SMBus: data valid <= 3.45 us)
SMB_MASK = (1 << SDA) | (1 << SCL) | (1 << TRIG)
BAT = 0x0B
REGS = {0x09: bytes([0x34, 0x12]), 0x0D: bytes([0x5A]), 0x20: bytes([7]) + b"Wirelin", 0x21: bytes([0]),
        None: bytes([0xA5])}


def smb_sc(words):
    prog = fw("smbus_host", **p3.smbus_defines(SDA, SCL, 7))
    return Sc({0: (prog, lambda f: dict(clkdiv=p3.smbus_clkdiv(f)))}, od=3, pout=3,
              extra=[(CORE(0, sim.C_CRCCTRL), p3.SMBUS_CRCCTRL)], prefill=words, pg_idle=3)


class SmbD(Drv):
    """fn(t, scl, sda) -> (SCL drive, SDA drive) (an SmbusDevice's step, or a
    bus user of the tests) on SDA/SCL, its outputs SMB_DELAY late."""

    def __init__(self, fn, dev=None):
        super().__init__((1 << SDA) | (1 << SCL), SMB_DELAY)
        self.fn, self.dev = fn, dev

    def step(self, t, uio, chip):
        sc, sd = self.fn(t, (uio >> SCL) & 1, (uio >> SDA) & 1)
        self.out(t, (sd << SDA) | (sc << SCL))

    def info(self, t0):
        if self.dev is None:
            return {}
        return {"transactions": [(round(x["t"] - t0), x["write"].hex(), x["read"].hex(), x["acks"], x["pec_ok"],
                                  x["sr"], x["aborted"]) for x in self.dev.transactions]}


def dev_far(factory):
    def far(t0):
        d = factory(t0)
        return Far3(SmbD(d.step, d))
    return far


def i2c_decode(log, sda=SDA, scl=SCL):
    """A pins-only I2C reader of a bus log [(t, uio)]: START/repeated START/STOP
    (SDA changing while SCL stays high), a bit at each SCL rise, 9 bits per byte.
    Returns [{t, bytes, acks, sr, stop}]."""
    out, cur, bits = [], None, []
    ps, pc = 1, 1
    for t, v in log:
        s, c = (v >> sda) & 1, (v >> scl) & 1
        if c and pc and s != ps:
            if s == 0:
                if cur is None:
                    cur = {"t": t, "bytes": [], "acks": [], "sr": 0, "stop": False}
                    out.append(cur)
                else:
                    cur["sr"] += 1
            elif cur is not None:
                cur["stop"] = True
                cur = None
            bits = []
        elif c and not pc and cur is not None:
            bits.append(s)
            if len(bits) == 9:
                cur["bytes"].append(int("".join(map(str, bits[:8])), 2))
                cur["acks"].append(bits[8])
                bits = []
        ps, pc = s, c
    return out


def i2c_bits(log, sda=SDA, scl=SCL):
    """SDA at every SCL rise after the first START."""
    out, ps, pc, on = [], 1, 1, False
    for t, v in log:
        s, c = (v >> sda) & 1, (v >> scl) & 1
        if c and pc and ps and not s:
            on = True
        elif on and c and not pc:
            out.append(s)
        ps, pc = s, c
    return out


def smb_timing(log):
    """SCL high and low times and bit periods (us, stretches and idle excluded),
    START hold, STOP setup and bus free times (us)."""
    scl = pf.bit_edges(log, SCL)
    sda = pf.bit_edges(log, SDA)
    t_end = log[-1][0] + 1
    hi = [d / 1e3 for v, t, d in pf.runs(scl, t_end) if v == 1 and d < 20e3]
    lo = [d / 1e3 for v, t, d in pf.runs(scl, t_end) if v == 0 and d < 20e3]
    rises = [t for t, v in scl if v]
    per = [(b - a) / 1e3 for a, b in zip(rises, rises[1:]) if b - a < 20e3]

    def scl_at(t):
        lv = 1
        for tt, v in scl:
            if tt >= t:
                break
            lv = v
        return lv
    sta = [t for t, v in sda if v == 0 and scl_at(t) and t > 0]
    sto = [t for t, v in sda if v == 1 and scl_at(t) and t > 0]
    hold = [(next(tt for tt, v in scl if tt > t and v == 0) - t) / 1e3 for t in sta]
    setup_ = [(t - max(tt for tt, v in scl if tt < t and v == 1)) / 1e3 for t in sto]
    free = [(min(b for b in sta if b > a) - a) / 1e3 for a in sto if any(b > a for b in sta)]
    return dict(hi=hi, lo=lo, per=per, hold=hold, setup=setup_, free=free, n_start=len(sta), n_stop=len(sto))


def smb_check_bus(name, trs, pecs):
    """Each transaction on the bus: the PEC (if any) = CRC-8 over its bytes, address bytes included."""
    for tr, pec in zip(trs, pecs):
        if pec:
            check(p3.smbus_pec(bytes(tr["bytes"][:-1])) == tr["bytes"][-1],
                  f"{name}: PEC on the bus {bytes(tr['bytes']).hex()}: want {p3.smbus_pec(bytes(tr['bytes'][:-1])):#04x}")


def smb_case(rig, store, name, txns, factory, far_key, *, capture, dur_s=0.03, poll_timeout=3.0):
    """txns: [smbus_words lists] prefilled; the device pre-timed. Returns (plan, records, bus log or None,
    capture or None)."""
    words = sum(txns, [])
    sc = smb_sc(words)
    n_end = len(txns)
    done = lambda w: sum(1 for x in w if x < 0x10000 and x & 0x8000) >= n_end
    plan = predict3(name, SMB_F, sc, far_factory=dev_far(factory), far_key=far_key, dur_s=dur_s, done=done,
                    tail_s=2e-3)
    if capture:
        pc = cap_plan(name, SMB_F, sc, plan, dev_far(factory), far_key)
        c, recs, fset = run_capture3(rig, sc, pc, la_mask=SMB_MASK, dur_s=TRIG_S + pc["t_last"] / 1e9 + 1e-3)
        log, t_end = cap_log(c)
        keep(store, name, log=log, t_end=t_end)
        c.info.update(t_end=t_end, plan=pc)
        same_words(name, recs, pc)
        check(((fset ^ pc["fset"]) >> 7) & 1 == 0, f"{name}: FOV {(fset >> 7) & 1}, model {(pc['fset'] >> 7) & 1}")
        if pc is not plan:     # the host can't drain core 0 during the burst: the same scenario again, polled
            recs, fset = run_poll3(rig, sc, plan, until=done, timeout_s=poll_timeout, after_s=0.005)
            same_words(name + " (polled twin)", recs, plan)
            c.info["twin"] = True
        return plan, recs, log, c
    recs, fset = run_poll3(rig, sc, plan, until=done, timeout_s=poll_timeout, after_s=0.005)
    same_words(name, recs, plan)
    return plan, recs, None, None


def smb_model_line(log, plan, c):
    """SCL edge for edge against the model run (SDA's edge count only: a release by one side and a drive by the
    other while SCL is low can make a sub-microsecond glitch whose presence depends on the start offset)."""
    t_end, plan = c.info["t_end"], c.info.get("plan", plan)
    d, n = bus_vs_plan(log, plan, SCL, t_end)
    if d is None:
        return "board vs model: SCL's edges differ in number or order"
    nb = len([x for x in pf.bit_edges(log, SDA) if 0 < x[0] < t_end])
    nm = len([x for x in pf.bit_edges(plan["pins"], SDA) if 0 < x[0] < t_end])
    tw = ("; core 0's RX FIFO not drained during the burst (as in its model run); all records from a polled run "
          "of the same scenario") if c.info.get("twin") else ""
    return (f"board vs model: {n} SCL edges, board - model {span(d, '{:+.0f}')} ns (1 tick = 1.12 us); SDA {nb} "
            f"edges (model {nm}); {c.info['records']} records, burst SCK = clk/{c.info['burst'][1]}{tw}")


SMB_TIMES = {"hi": [], "lo": [], "per": [], "hold": [], "setup": [], "free": []}


def smb_add_times(tm):
    for k in SMB_TIMES:
        SMB_TIMES[k] += tm[k]


PROTOCOLS = [
    ("write byte", p3.smbus_words(BAT, [0x0D, 0x42], pec=True), "ok", b""),
    ("write word", p3.smbus_words(BAT, [0x09, 0x78, 0x56], pec=True), "ok", b""),
    ("read byte", p3.smbus_words(BAT, [0x0D], 1, pec=True), "pec_ok", REGS[0x0D]),
    ("read word", p3.smbus_words(BAT, [0x09], 2, pec=True), "pec_ok", REGS[0x09]),
    ("block read", p3.smbus_words(BAT, [0x20], 1, pec=True, block=True), "pec_ok", REGS[0x20]),
    ("receive byte", p3.smbus_words(BAT, b"", 1, pec=True), "pec_ok", REGS[None]),
    ("block write", p3.smbus_words(BAT, [0x30, 3, 1, 2, 3], pec=True), "ok", b""),
    ("quick write", p3.smbus_words(BAT, quick_rw=0), "ok", b""),
    ("quick read", p3.smbus_words(BAT, quick_rw=1), "ok", b""),
    ("process call", p3.smbus_words(BAT, [0x09, 0x11, 0x22], 2, pec=True), "pec_ok", REGS[0x09]),
    ("send byte", p3.smbus_words(BAT, [0x5C], pec=True), "ok", b""),
    ("empty block", p3.smbus_words(BAT, [0x21], 1, pec=True, block=True), "pec_ok", REGS[0x21]),
]


@model_clock(SMB_CAP_F)
def smbus_protocols(rig, store):
    """smbus_host: every SMBus protocol with PEC against the device model (pre-timed), in 4 captured runs."""
    for k in SMB_TIMES:
        SMB_TIMES[k] = []
    out, allres = [], []
    batches, cur = [], []
    for p in PROTOCOLS:
        if sum(len(x[1]) for x in cur) + len(p[1]) > 8:
            batches.append(cur)
            cur = []
        cur.append(p)
    batches.append(cur)
    for i, batch in enumerate(batches):
        name = f"smbus_proto_{i}"
        plan, recs, log, c = smb_case(rig, store, name, [w for _, w, _, _ in batch],
                                      lambda t0: p3.SmbusDevice(BAT, REGS), "bat", capture=True)
        res = p3.smbus_records(recs)
        for (nm, _, want, data), r in zip(batch, res):
            got = r["read"][:-1] if want == "pec_ok" else r["read"]
            check(r["result"] == want and got == data, f"{nm}: {r}")
            allres.append((nm, r["result"], r["read"].hex()))
        check(len(res) == len(batch), f"{len(res)} results for {len(batch)} transactions")
        trs = i2c_decode(log)
        check(len(trs) == len(batch), f"{name}: {len(trs)} transactions on the bus")
        pecs = [w[0] & 1 for _, w, _, _ in batch]
        smb_check_bus(name, trs, pecs)
        for (nm, w, want, data), tr in zip(batch, trs):
            hdr = w[0]
            nw, nr, addr = hdr >> 16 & 255, hdr >> 24, hdr >> 8 & 127
            wbytes = [x >> 24 for x in w[1:]]
            check(tr["bytes"][0] == addr << 1 | (hdr >> 3 & 1 if nw == nr == 0 else int(nw == 0 and nr > 0)),
                  f"{nm}: address byte {tr['bytes'][0]:#04x}")
            check(tr["bytes"][1:1 + nw] == wbytes, f"{nm}: written {tr['bytes'][1:1 + nw]} != {wbytes}")
            if nr:
                check(not tr["acks"][-1] == 0 and all(a == 0 for a in tr["acks"][:-1]), f"{nm}: ACK bits {tr['acks']}")
        tm = smb_timing(log)
        smb_add_times(tm)
        out.append(f"run {i}: {[nm for nm, *_ in batch]}: results {[r['result'] for r in res]}; on the bus "
                   f"{[bytes(t['bytes']).hex() for t in trs]}, each PEC = CRC-8 of its bytes; {smb_model_line(log, plan, c)}")
    t = SMB_TIMES
    check(min(t["hi"]) >= 4.0 and min(t["lo"]) >= 4.7 and 1e3 / min(t["per"]) <= 100.0 and min(t["hold"]) >= 4.0
          and min(t["setup"]) >= 4.0 and (not t["free"] or min(t["free"]) >= 4.7), f"timing {t}")
    det(store, "smbus_protocols", res=allres)
    out.append(f"timing on the bus (us): SCL high {span(t['hi'])}, low {span(t['lo'])}, bit periods {span(t['per'])} "
               f"({1e3 / max(t['per']):.1f}..{1e3 / min(t['per']):.1f} kHz); START hold {span(t['hold'])}, STOP setup "
               f"{span(t['setup'])}, bus free {span(t['free'])} (SMBus: tHIGH >= 4.0, tLOW >= 4.7, <= 100 kHz, "
               f"tHD;STA >= 4.0, tSU;STO >= 4.0, tBUF >= 4.7)")
    return out


@model_clock(SMB_CAP_F)
def smbus_nopec(rig, store):
    """Without PEC against a device without PEC: read word, block read, write byte; captured."""
    txns = [p3.smbus_words(BAT, [0x09], 2), p3.smbus_words(BAT, [0x20], 1, block=True),
            p3.smbus_words(BAT, [0x0D, 0x99])]
    plan, recs, log, c = smb_case(rig, store, "smbus_nopec", txns, lambda t0: p3.SmbusDevice(BAT, REGS, pec=False),
                                  "bat nopec", capture=True)
    res = p3.smbus_records(recs)
    check([(r["result"], r["read"]) for r in res] == [("ok", REGS[0x09]), ("ok", REGS[0x20]), ("ok", b"")], f"{res}")
    trs = i2c_decode(log)
    check([t["acks"][-1] for t in trs[:2]] == [1, 1] and bytes(trs[2]["bytes"]) == bytes([BAT << 1, 0x0D, 0x99]),
          f"bus {trs}")
    det(store, "smbus_nopec", recs=recs)
    return [f"results {[r['result'] for r in res]}, read {[r['read'].hex() for r in res[:2]]}; the last byte read "
            f"not acknowledged on the bus; written {bytes(trs[2]['bytes']).hex()}", smb_model_line(log, plan, c)]


class ErrDev(p3.SmbusDevice):
    """Transaction 0: the first byte read has a bit flipped (its PEC is then
    wrong); transaction 1: the device has no PEC."""

    @property
    def pec(self):
        return len(self.transactions) != 1

    @pec.setter
    def pec(self, v):
        pass

    def _next_read_byte(self):
        b = super()._next_read_byte()
        if len(self.transactions) == 0 and self.tr["out"] and len(self.tr["read"]) == 0:
            return b ^ 0x10
        return b


@model_clock(SMB_CAP_F)
def smbus_pec_errors(rig, store):
    """Negative controls: a flipped bit in the data (the device's PEC is then wrong); a device without PEC; then a good read; captured."""
    txns = [p3.smbus_words(BAT, [0x09], 2, pec=True), p3.smbus_words(BAT, [0x09], 1, pec=True),
            p3.smbus_words(BAT, [0x09], 2, pec=True)]
    plan, recs, log, c = smb_case(rig, store, "smbus_pec_errors", txns, lambda t0: ErrDev(BAT, REGS), "errdev",
                                  capture=True)
    res = p3.smbus_records(recs)
    check(res[0]["result"] == "pec_bad" and res[0]["read"][0] == REGS[0x09][0] ^ 0x10, f"flipped: {res[0]}")
    check(res[1]["result"] == "pec_bad" and res[1]["read"] == REGS[0x09], f"no PEC: {res[1]}")
    check(res[2]["result"] == "pec_ok" and res[2]["read"][:2] == REGS[0x09], f"good: {res[2]}")
    trs = i2c_decode(log)
    wrong = [p3.smbus_pec(bytes(t["bytes"][:-1])) != t["bytes"][-1] for t in trs]
    check(wrong == [True, True, False], f"PEC on the bus wrong: {wrong}")
    det(store, "smbus_pec_errors", recs=recs)
    return [f"flipped bit: {res[0]['result']} (read {res[0]['read'].hex()}); device without PEC: {res[1]['result']}; "
            f"then {res[2]['result']}; the pins-only reader agrees (PEC wrong on the bus: {wrong})",
            smb_model_line(log, plan, c)]


@model_clock(SMB_CAP_F)
def smbus_nacks(rig, store):
    """No device at 0x50 (4 data words dropped); a device refusing the 2nd data byte (the rest dropped); then good reads; captured."""
    out, allr = [], []
    for name, txns, kw in (("smbus_nack_addr", [p3.smbus_words(0x50, [1, 2, 3, 4], pec=True),
                                                p3.smbus_words(BAT, [0x09], 2, pec=True)], {}),
                           ("smbus_nack_data", [p3.smbus_words(BAT, [0x30, 5, 1, 2, 3], pec=True),
                                                p3.smbus_words(BAT, [0x09], 2, pec=True)], {"nack_after": 1})):
        plan, recs, log, c = smb_case(rig, store, name, txns, lambda t0, kw=kw: p3.SmbusDevice(BAT, REGS, **kw),
                                      repr(kw), capture=True)
        res = p3.smbus_records(recs)
        allr += [(r["result"], r["unsent"]) for r in res]
        out.append(f"{name}: {[(r['result'], r['unsent']) for r in res]}; {smb_model_line(log, plan, c)}")
    check(allr == [("addr_nack", 4), ("pec_ok", 0), ("data_nack", 3), ("pec_ok", 0)], f"results {allr}")
    det(store, "smbus_nacks", res=allr)
    return out


@model_clock(SMB_CAP_F)
def smbus_stretch(rig, store):
    """A device holding SCL low 3 us, 50 us (captured) and 2 ms (polled) after every ACK it sends: block read and block write with PEC."""
    out = []
    for us in (3, 50, 2000):
        txns = [p3.smbus_words(BAT, [0x20], 1, pec=True, block=True), p3.smbus_words(BAT, [0x30, 2, 9, 8], pec=True)]
        cap = us < 1000
        plan, recs, log, c = smb_case(rig, store, f"smbus_stretch_{us}", txns,
                                      lambda t0, us=us: p3.SmbusDevice(BAT, REGS, stretch=us * 1e3), f"stretch {us}",
                                      capture=cap, dur_s=0.06)
        res = p3.smbus_records(recs)
        check(res[0]["result"] == "pec_ok" and res[0]["read"][:-1] == REGS[0x20] and res[1]["result"] == "ok",
              f"stretch {us} us: {res}")
        wr = plan["info"][0]["transactions"][1]
        check(wr[4] is True, f"the device model's PEC check of the write: {wr}")
        line = f"{us} us: block read {res[0]['result']}, block write {res[1]['result']}"
        if cap:
            lows = [d / 1e3 for v, t, d in pf.runs(pf.bit_edges(log, SCL), log[-1][0]) if v == 0 and d > 6e3]
            line += f"; stretched SCL lows on the bus {span(lows)} us; {smb_model_line(log, plan, c)}"
        else:
            line += " (polled: the run is longer than one capture burst can drain at SMBus's edge rate)"
        out.append(line)
    det(store, "smbus_stretch", ok=True)
    return out


class HangOnce(p3.SmbusDevice):
    """hang = (2, 40 ms) in its first transaction only."""

    @property
    def hang(self):
        return (2, 40e6) if not self.transactions else None

    @hang.setter
    def hang(self, v):
        pass


@model_clock(SMB_F)
def smbus_timeout(rig, store):
    """A device holding SCL low 40 ms after the command byte: 'timeout' after 25 ms; it resets at 35 ms; the next transaction works; polled, the record host-timed."""
    txns = [p3.smbus_words(BAT, [0x09], 2, pec=True), p3.smbus_words(BAT, [0x09], 2, pec=True)]
    words = sum(txns, [])
    sc = smb_sc(words)
    done = lambda w: sum(1 for x in w if x < 0x10000 and x & 0x8000) >= 2
    plan = predict3("smbus_timeout", SMB_F, sc, far_factory=dev_far(lambda t0: HangOnce(BAT, REGS)),
                    far_key="hang once", dur_s=0.1, done=done, tail_s=2e-3)
    # the chip released SCL (waiting for the device) last before the hold: from the plan's drive
    far_scl = pf.bit_edges([(t, v) for t, v in plan["far"]], SCL)
    hold = max((b[0] - a[0], a[0]) for a, b in zip(far_scl, far_scl[1:]) if a[1] == 0)
    rel = max(t for t, v in pf.bit_edges(plan["drive"], SCL) if v == 1 and t < hold[1] + hold[0])
    prep3(rig, sc, sched=far_sched3(rig, plan, sc.pg_idle))
    ctrl(rig, 1)
    if rig.model:
        c_go = rig.mh.chip.cycle
        now = lambda: (rig.mh.chip.cycle - c_go) / rig.f_clk
    else:
        t_go = time.monotonic()
        now = lambda: time.monotonic() - t_go
    rig.pg_start()
    words, t_rec = [], None
    while sum(1 for x in words if x < 0x10000 and x & 0x8000) < 2:
        st = rig.read(STATUS(0))
        n = rxn(st)
        if n:
            words += rd(rig, FIFO(0), n)
            if t_rec is None and any(x >> 8 == 0x85 and x < 0x10000 for x in words):
                t_rec = now()
        check(now() < 3, f"no end: {words}")
        rig.wait(0.1e-3)
    finish(rig)
    same_words("smbus_timeout", words, plan)
    res = p3.smbus_records(words)
    check([r["result"] for r in res] == ["timeout", "pec_ok"], f"results {res}")
    gave = (t_rec - TRIG_S) * 1e3 - rel / 1e6
    tr = plan["info"][0]["transactions"]
    check(tr[0][6] and 24.9 <= gave <= 25.8, f"gave up {gave:.3f} ms after releasing SCL (host-timed), {tr}")
    det(store, "smbus_timeout", recs=words)
    return [f"results {[r['result'] for r in res]}; the device (pre-timed) held SCL low {hold[0] / 1e6:.3f} ms (its "
            f"reset at 35 ms); the timeout record reached the host {gave:.2f} ms after the chip released SCL "
            f"(host-timed: polled every 0.1 ms, from pe_lab's start; the firmware's 25.003 ms + 9 ticks)"]


@model_clock(SMB_F)
def smbus_busy(rig, store):
    """SDA held low by something else for 40 ms: 'busy' after 25 ms, 3 data words dropped; then a read works once the bus is free; polled."""
    txns = [p3.smbus_words(BAT, [0x09, 1, 2], pec=True), p3.smbus_words(BAT, [0x09], 2, pec=True)]

    def far(t0):
        d = p3.SmbusDevice(BAT, REGS)
        return Far3(SmbD(d.step, d), SmbD(lambda t, scl, sda: (1, 0 if t < t0 + 40e6 else 1)))
    sc = smb_sc(sum(txns, []))
    done = lambda w: sum(1 for x in w if x < 0x10000 and x & 0x8000) >= 2
    plan = predict3("smbus_busy", SMB_F, sc, far_factory=far, far_key="busy 40ms", dur_s=0.1, done=done, tail_s=2e-3)
    recs, fset = run_poll3(rig, sc, plan, until=done, timeout_s=3.0, after_s=0.005)
    same_words("smbus_busy", recs, plan)
    res = p3.smbus_records(recs)
    check([(r["result"], r["unsent"]) for r in res] == [("busy", 3), ("pec_ok", 0)], f"results {res}")
    det(store, "smbus_busy", recs=recs)
    return [f"results {[(r['result'], r['unsent']) for r in res]}: busy after 25 ms with the 3 data words dropped; "
            f"the next transaction waited for the free bus and read {res[1]['read'][:2].hex()}"]


class OtherMaster:
    """test_smbus.OtherMaster: a second master that starts with us and sends its
    address byte in step with our SCL, driving SDA while it wins."""

    def __init__(self, byte):
        self.byte, self.k, self.lost, self.prev, self.sda = byte, None, False, (1, 1), 1

    def __call__(self, t, scl, sda):
        pscl, psda = self.prev
        self.prev = (scl, sda)
        if self.k is None and scl and pscl and psda and not sda:
            self.k, self.sda = 0, 1
        elif self.k is not None and not self.lost:
            if pscl and not scl and self.k < 8:
                self.sda = (self.byte >> (7 - self.k)) & 1
                self.k += 1
            elif scl and not pscl and self.sda == 1 and sda == 0:
                self.lost, self.sda = True, 1
            elif pscl and not scl and self.k >= 8:
                self.sda = 1
        return 1, self.sda


@model_clock(SMB_CAP_F)
def smbus_arbitration(rig, store):
    """Another master starts with us addressing 0x08 where we address 0x0B: we see a 0 at our 6th address bit: 'lost', both lines released; captured."""
    txns = [p3.smbus_words(BAT, [0x09], 2, pec=True)]
    words = sum(txns, [])
    sc = smb_sc(words)
    done = lambda w: sum(1 for x in w if x < 0x10000 and x & 0x8000) >= 1
    plan = predict3("smbus_arbitration", SMB_F, sc, far_factory=lambda t0: Far3(SmbD(OtherMaster(0x08 << 1))),
                    far_key="other master 0x08", dur_s=0.02, done=done, tail_s=2e-3)
    c, recs, fset = run_capture3(rig, sc, plan, la_mask=SMB_MASK, dur_s=TRIG_S + plan["t_last"] / 1e9 + 1e-3)
    log, t_end = cap_log(c)
    keep(store, "smbus_arbitration", log=log, t_end=t_end)
    c.info["t_end"] = t_end
    same_words("smbus_arbitration", recs, plan)
    res = p3.smbus_records(recs)
    check([(r["result"], r["unsent"]) for r in res] == [("lost", 1)], f"results {res}")
    bits = i2c_bits(log)
    _, oe = c.info["uio_end"]
    check(bits == [0, 0, 0, 1, 0, 0], f"bits clocked on the bus after START {bits}")
    check(not oe & 3, f"our lines not released at the end: uio_oe {oe:#04x}")
    det(store, "smbus_arbitration", recs=recs, bits=bits)
    return [f"result lost (1 data word dropped): the bus carried {bits} at our SCL rises (0x08's 000100, ours "
            f"000101), then we let go: SCL stopped (the other master here has no clock of its own) and our "
            f"drive of both lines is off (uio_oe {oe:#04x})", smb_model_line(log, plan, c)]


# ================================================================ ISO 7816-3

IO, RST, CLK = 0, 1, 9           # uio0 (open drain), uio1, uo1
ISO_FT = 50e6 / 6                # model clock: clock core CLKDIV 1, reader 6; tick for tick 50 MHz / 6 and 36
ISO_MASK = (1 << IO) | (1 << RST) | (1 << TRIG)
WT = 32
ATR = bytes.fromhex("3BB4110000574C474F")
ATR_INV = bytes.fromhex("3FB4110000574C474F")


def iso_d(f):
    d = round(f / ISO_FT)
    check(d >= 1 and abs(f / ISO_FT - d) < 1e-3, f"{f / 1e6:.4f} MHz is not a multiple of {ISO_FT / 1e6:.4f} MHz")
    return d


def iso_etu_ns(f=ISO_FT):
    return 372 * 2 * iso_d(f) * 1e9 / f


def iso_sc(groups, clk_pin=CLK, prefill_only=False):
    prog = fw("iso7816", **p3.iso_defines(IO, RST, wt=WT, finv=5, fov=7))
    clk = fw("iso7816_clk", CLK=clk_pin)
    pre, feed = groups_split(groups)
    check(not (prefill_only and feed), "more than 8 TX words in a prefilled run")
    return Sc({0: (prog, lambda f: dict(clkdiv=p3.iso_clkdivs(iso_d(f))[0])),
               1: (clk, lambda f: dict(clkdiv=p3.iso_clkdivs(iso_d(f))[1]))},
              owners={clk_pin: 1}, od=1 << IO, pout=1 << IO, poe=(1 << RST) | ((1 << clk_pin) if clk_pin < 8 else 0),
              extra=[(CORE(0, sim.C_CRCCTRL), p3.ISO_CRCCTRL)], prefill=pre, feed=feed, pg_idle=1 << IO)


class CardD(Drv):
    """An IsoCard on I/O; it sees CLK and RST from the chip's pins."""

    def __init__(self, card):
        super().__init__(1 << IO)
        self.card = card

    def step(self, t, uio, chip):
        p = chip.pin_out
        self.out(t, self.card.step(t, (p >> CLK) & 1, (p >> RST) & 1, (uio >> IO) & 1) << IO)

    def info(self, t0):
        c = self.card
        return {"received": [(b, ok, bad) for _, b, ok, bad in c.received], "sent": [(b, a) for _, b, a in c.sent],
                "commands": [(h.hex(), d.hex(), a.hex()) for h, d, a in c.commands], "errors_seen": len(c.err_seen)}


def activate():
    return [[p3.ISO_RESET] + p3.iso_recv(33, first_timeout_etu=112, ts=True)]


def t0_read(hdr, le, n_extra=0):
    return [p3.iso_send(bytes(hdr), n_extra) + p3.iso_recv(le + 3 + 4)]


def t0_update(hdr, data, nulls=0):
    return [p3.iso_send(bytes(hdr)) + p3.iso_recv(1 + nulls), p3.iso_send(bytes(data)) + p3.iso_recv(2)]


def iso_cmds(recs):
    """Records split per command: [(kind, chars [(byte, errors, gave up)], end record)]."""
    out, chars = [], []
    for r in p3.iso_records(recs):
        if r[0] == "char":
            chars.append(r[1:])
        else:
            out.append((r[0], chars, r))
            chars = []
    return out


def count_ends(groups):
    """The end records (SEND, RECV) that the host's command words will produce."""
    n, ws = 0, [w for g in groups for w in g]
    i = 0
    while i < len(ws):
        cmd = ws[i] & 3
        if cmd == p3.ISO_SEND:
            i += -(-((ws[i] >> 8) & 255) // 4)
        n += cmd in (p3.ISO_SEND, p3.ISO_RECV)
        i += 1
    return n


def iso_ends(recs):
    return sum(1 for r in p3.iso_records(recs) if r[0] in ("recv", "send"))


def iso_plan(name, groups, card_factory, far_key, n_ends_, dur_s=1.5, cap=False):
    sc, n_ends = iso_sc(groups), count_ends(groups)
    plan = predict3(name, ISO_FT, sc, far_factory=(far := lambda t0: Far3(CardD(card_factory()))), far_key=far_key,
                    dur_s=dur_s, done=lambda w: iso_ends(w) >= n_ends, tail_s=3e-3)
    return sc, (cap_plan(name, ISO_FT, sc, plan, far, far_key) if cap else plan)


def iso_poll(rig, store, name, groups, card_factory, far_key, n_ends):
    """Both cores (reader + clock), the card pre-timed, records polled and the TX FIFO fed."""
    sc, plan = iso_plan(name, groups, card_factory, far_key, None)
    n_ends = count_ends(groups)
    recs, fset = run_poll3(rig, sc, plan, until=lambda w: iso_ends(w) >= n_ends, timeout_s=5.0, after_s=0.01)
    same_words(name, recs, plan)
    det(store, name, recs=recs)
    return plan, recs, fset


def t0_result(cmds, ins):
    chars = [b for kind, cs, _ in cmds for b, _, _ in cs]
    return p3.t0_parse(chars, ins)


def iso_card_line(plan):
    i = plan["info"][0]
    return (f"the card model (pre-timing run) received {len(i['received'])} characters, ran "
            f"{[h for h, _, _ in i['commands']]}, saw {i['errors_seen']} error signals")


@model_clock(ISO_FT)
def iso_t0(rig, store):
    """iso7816 + iso7816_clk, card pre-timed: ATR (direct), READ BINARY 16, UPDATE BINARY 5, unknown INS, READ with N = 1; polled."""
    g = activate() + t0_read([0x00, 0xB0, 0x00, 0x20, 0x10], 16) + \
        t0_update([0x00, 0xD6, 0x00, 0x40, 0x05], b"HELLO") + t0_read([0x00, 0x12, 0x00, 0x00, 0x00], 0) + \
        t0_read([0x00, 0xB0, 0x00, 0x40, 0x05], 5, n_extra=1)
    plan, recs, fset = iso_poll(rig, store, "iso_t0", g, lambda: p3.IsoCard(ATR), "atr", 8)
    cmds = iso_cmds(recs)
    atr = bytes(b for b, _, _ in cmds[0][1])
    check(atr == ATR and cmds[0][2][3] and not cmds[0][2][4] and not cmds[0][2][5], f"ATR {atr.hex()} {cmds[0][2]}")
    check(not (fset >> 5) & 1, "FINV set for the direct convention")
    r1 = t0_result(cmds[1:3], 0xB0)
    check(r1[1] and r1[2] == bytes(range(0x20, 0x30)) and r1[3] == b"\x90\x00", f"READ BINARY {r1}")
    r2 = t0_result(cmds[3:7], 0xD6)
    check(r2[1] and r2[3] == b"\x90\x00", f"UPDATE BINARY {r2}")
    ins_seen = [b for b, _, _ in cmds[4][1]] == [0xD6]
    check(ins_seen, f"UPDATE's procedure byte {cmds[4]}")
    r3 = t0_result(cmds[7:9], 0x12)
    check(not r3[1] and r3[3] == b"\x6d\x00", f"unknown INS {r3}")
    r4 = t0_result(cmds[9:11], 0xB0)
    check(r4[2] == b"HELLO" and r4[3] == b"\x90\x00", f"READ after UPDATE {r4}")
    check(all(e == 0 for _, cs, _ in cmds for _, e, _ in cs) and not any(c[2][0] == "send" and (c[2][1] or c[2][2])
                                                                         for c in cmds), "error signals or repetitions")
    return [f"ATR {atr.hex()} (direct convention, TS detected, FINV 0); READ BINARY 00B0002010: INS, "
            f"{r1[2].hex()}, 9000; UPDATE BINARY 'HELLO': INS D6, 9000; INS 12: 6D00; READ 5 at 0x40 with N = 1: "
            f"{r4[2]!r} (the update landed), 9000",
            f"the firmware's {len(recs)} records = the model run's, word for word; {iso_card_line(plan)}"]


@model_clock(ISO_FT)
def iso_inverse(rig, store):
    """The inverse convention (TS = 3F): detected (FINV), then READ BINARY 8 and UPDATE BINARY 3 in it; polled."""
    g = activate() + t0_read([0x00, 0xB0, 0x00, 0x10, 0x08], 8) + t0_update([0x00, 0xD6, 0x00, 0x00, 0x03], b"\xa5\x0f\xf0")
    plan, recs, fset = iso_poll(rig, store, "iso_inverse", g, lambda: p3.IsoCard(ATR_INV, inverse=True), "inv", 6)
    cmds = iso_cmds(recs)
    atr = bytes(b for b, _, _ in cmds[0][1])
    check(atr == ATR_INV and cmds[0][2][5] and (fset >> 5) & 1, f"ATR {atr.hex()} {cmds[0][2]} FSET {fset:#x}")
    r1 = t0_result(cmds[1:3], 0xB0)
    check(r1[1] and r1[2] == bytes(range(16, 24)) and r1[3] == b"\x90\x00", f"READ {r1}")
    r2 = t0_result(cmds[3:7], 0xD6)
    check(r2[1] and r2[3] == b"\x90\x00", f"UPDATE {r2}")
    mem = plan["info"][0]["commands"][-1]
    check(mem[1] == "a50ff0", f"the card model got {mem}")
    return [f"ATR {atr.hex()}: inverse convention, FINV = 1; READ BINARY 8 at 0x10: {r1[2].hex()}, 9000; UPDATE "
            f"BINARY a5 0f f0: 9000 (the card model took {mem[1]})", f"records = the model run's; {iso_card_line(plan)}"]


@model_clock(ISO_FT)
def iso_nulls(rig, store):
    """NULL procedure bytes: a card answering 60 60 before INS; READ BINARY 5; polled."""
    g = activate() + t0_read([0x00, 0xB0, 0x00, 0x40, 0x05], 5 + 2)
    plan, recs, fset = iso_poll(rig, store, "iso_nulls", g, lambda: p3.IsoCard(ATR, nulls=2), "nulls 2", 4)
    cmds = iso_cmds(recs)
    r = t0_result(cmds[1:3], 0xB0)
    check(r[0] == 2 and r[1] and r[2] == bytes(range(0x40, 0x45)) and r[3] == b"\x90\x00", f"{r}")
    return [f"2 NULL bytes, INS, {r[2].hex()}, 9000; records = the model run's"]


@model_clock(ISO_FT)
def iso_rx_errors(rig, store):
    """The card sends a character with a wrong parity bit twice (2 error signals, then right), and one 9 times (4 signals, the 5th copy kept and flagged); polled."""
    g = activate() + t0_read([0x00, 0xB0, 0x00, 0x00, 0x04], 4) + t0_read([0x00, 0xB0, 0x00, 0x00, 0x02], 2)
    plan, recs, fset = iso_poll(rig, store, "iso_rx_errors", g,
                                lambda: p3.IsoCard(ATR, corrupt={len(ATR) + 2: 2, len(ATR) + 8: 9}), "corrupt", 6)
    cmds = iso_cmds(recs)
    e1 = [e for b, e, _ in cmds[2][1]]
    r1 = t0_result(cmds[1:3], 0xB0)
    check(e1 == [0, 0, 2, 0, 0, 0, 0] and r1[2] == bytes(range(4)) and r1[3] == b"\x90\x00", f"{e1} {r1}")
    ch = cmds[4][1]
    check(ch[1][1] == 4 and ch[1][2] and ch[1][0] == 0x00 and [b for b, _, _ in ch[2:]] == [0x01, 0x90, 0x00],
          f"give-up {ch}")
    check(cmds[2][2][2] == 2 and cmds[4][2][2] == 4, f"RECV error counts {cmds[2][2]} {cmds[4][2]}")
    return [f"READ 4: error signals per character {e1}, data {r1[2].hex()}, 9000 (the card repeated twice)",
            f"READ 2: the first data byte corrupted 9 times: {ch[1][1]} error signals, then kept and flagged "
            f"(gave up = {ch[1][2]}); the rest {bytes(b for b, _, _ in ch[2:]).hex()}; records = the model run's"]


@model_clock(ISO_FT)
def iso_tx_errors(rig, store):
    """The card rejects our 3rd header byte once and our 2nd data byte twice (repetitions), then, in a second run, a header byte 6 times (abort after 5 sends); polled."""
    g = activate() + t0_update([0x00, 0xD6, 0x00, 0x00, 0x03], b"\x11\x22\x33")
    plan, recs, fset = iso_poll(rig, store, "iso_tx_rep", g, lambda: p3.IsoCard(ATR, reject={2: 1, 6: 2}), "rej", 5)
    sends = [c[2] for c in iso_cmds(recs) if c[0] == "send"]
    check([(s[1], s[2], s[3]) for s in sends] == [(0, 1, False), (0, 2, False)], f"SEND records {sends}")
    r = t0_result(iso_cmds(recs)[1:5], 0xD6)
    check(r[1] and r[3] == b"\x90\x00", f"{r}")
    check(plan["info"][0]["commands"][-1][1] == "112233", f"card {plan['info'][0]['commands']}")
    g2 = activate() + [p3.iso_send(bytes([0x00, 0xB0, 0x00, 0x00, 0x02]))]
    plan2, recs2, _ = iso_poll(rig, store, "iso_tx_abort", g2, lambda: p3.IsoCard(ATR, reject={1: 6}), "rej6", 2)
    s = iso_cmds(recs2)[-1][2]
    check(s[0] == "send" and s[3] and s[1] == 4 and s[2] == 5, f"abort {s}")
    bad = [b for b, ok, bad in plan2["info"][0]["received"] if bad]
    return [f"repetitions: SEND records (not sent, repetitions, aborted) {[(x[1], x[2], x[3]) for x in sends]}, "
            f"9000; the card model took 11 22 33",
            f"6 rejections: SEND aborted with {s[1]} bytes not sent after {s[2]} repetitions (the card model saw "
            f"{len(bad)} copies of B0); records = the model run's"]


@model_clock(ISO_FT)
def iso_misc(rig, store):
    """A first character that is not TS (bad TS), a mute card (timeout), DEACT (RST and I/O low); polled."""
    _, recs, _ = iso_poll(rig, store, "iso_bad_ts", activate(), lambda: p3.IsoCard(bytes([0x3A]) + ATR[1:]), "3a", 1)
    e = iso_cmds(recs)[0][2]
    check(e[4] and e[1] == 0, f"bad TS {e}")
    _, recs2, _ = iso_poll(rig, store, "iso_mute", activate(), lambda: p3.IsoCard(b""), "mute", 1)
    e2 = iso_cmds(recs2)[0][2]
    check(e2[3] and e2[1] == 0, f"mute {e2}")
    # DEACT after an ATR: then read the reader's pins
    sc, plan = iso_plan("iso_deact", activate() + [[p3.ISO_DEACT]], lambda: p3.IsoCard(ATR), "deact", 1)
    prep3(rig, sc, sched=far_sched3(rig, plan, sc.pg_idle))
    ctrl(rig, 0b11)
    rig.pg_start()
    words = []
    for _ in range(400):
        words += rd(rig, FIFO(0), rxn(rig.read(STATUS(0))))
        rig.wait(0.5e-3)
        if iso_ends(words) >= 1 and rig.read(STATUS(0)) & 15 == 0 and not rig.pg_busy():
            break
    rig.wait(2e-3)
    out, oe = uio_drive(rig)
    finish(rig)
    same_words("iso_deact", words, plan)
    check((oe & 1) and not (out >> RST) & 1 and (oe >> RST) & 1, f"after DEACT: uio_out {out:#04x}, uio_oe {oe:#04x}")
    det(store, "iso_misc", res=[e[4], e2[3], oe & 3, out & 3])
    return [f"3A first: bad TS, 0 characters, receiving stopped; mute card: timeout, 0 characters; after DEACT the "
            f"pins: I/O driven low (uio_oe[0] = 1), RST low (uio_out[1] = 0, driven); records = the model runs'"]


def uio_drive(rig):
    """(uio_out, uio_oe) as the pins show them now."""
    if rig.model:
        _, out, oe = rig.mh.chip.outputs()
        return out, oe
    v = rig.board.regs.inp()
    return (v >> 8) & 0xFF, (v >> 16) & 0xFF


def strip_signals(edges, etu):
    """A drive's edges [(t, v)] without its error signals (1.5-etu lows)."""
    out, i = [], 0
    while i < len(edges):
        t, v = edges[i]
        if v == 0 and i + 1 < len(edges) and abs(edges[i + 1][0] - t - 1.5 * etu) < 0.1 * etu:
            i += 2
            continue
        out.append((t, v))
        i += 1
    return out


def char_starts(edges, etu):
    """Falls that start characters on a line [(t, level)] (each >= 10.9 etu after the last; test_iso7816)."""
    out = []
    for t, v in edges:
        if v == 0 and t > 0 and (not out or t - out[-1] > 10.9 * etu):
            out.append(t)
    return out


ISO_RX_SEQ = activate() + t0_read([0x00, 0xB0, 0x00, 0x00, 0x04], 4)


def iso_rx_plan(cap=False):
    return iso_plan("iso_seq_rx", ISO_RX_SEQ, lambda: p3.IsoCard(ATR, corrupt={len(ATR) + 2: 2}), "corrupt 2", 3, cap=cap)


def ours_from_plan(plan, etu):
    """From the model run: our drive's error signals (falls of 1.5-etu lows) and character starts."""
    d = [x for x in pf.bit_edges(plan["drive"], IO) if x[0] > 0]
    lows = [(t, dd) for v, t, dd in pf.runs(d, d[-1][0] + 20 * etu) if v == 0]
    sig = [t for t, dd in lows if abs(dd - 1.5 * etu) < 0.1 * etu]
    rest = [(t, 0) for t, dd in lows if abs(dd - 1.5 * etu) >= 0.1 * etu]
    return sig, char_starts(rest, etu)


def on_board(bus, ts, tol):
    """The board's bus falls nearest to the model's fall times ts (each within tol ns): [(t, low run)]."""
    lows = [(t, d) for v, t, d in pf.runs(bus, bus[-1][0] + 1e9) if v == 0 and t > 0]
    out = []
    for tm in ts:
        b = min(lows, key=lambda x: abs(x[0] - tm))
        check(abs(b[0] - tm) <= tol, f"no bus fall within {tol:.0f} ns of the model's at {tm:.0f}")
        out.append(b)
    return out


def iso_cap(rig, store, name, sc, plan):
    etu = iso_etu_ns(rig.f_clk)
    c, recs, fset = run_capture3(rig, sc, plan, la_mask=ISO_MASK, dur_s=TRIG_S + plan["t_last"] / 1e9 + 3e-3)
    log, t_end = cap_log(c)
    keep(store, name, log=log, t_end=t_end, etu=etu)
    io = pf.bit_edges(log, IO)
    d, n = bus_vs_plan(log, plan, IO, t_end)
    check(d is not None, f"{name}: the I/O line's edges differ from the model run's")
    dr, _ = bus_vs_plan(log, plan, RST, t_end)
    check(dr is not None and len(dr) == 1, f"{name}: RST should rise once")
    rst_up = next(t for t, v in pf.bit_edges(log, RST) if v == 1 and t > 0)
    sig_m, ch_m = ours_from_plan(plan, etu)
    sig = on_board(io, sig_m, 2 * 720)
    ch = [t for t, _ in on_board(io, ch_m, 2 * 720)]
    card = char_starts(pf.bit_edges(plan["far"], IO), etu)
    line = (f"{name}: board vs model ({n} I/O edges and RST's rise): board - model {span(d + dr, '{:+.0f}')} ns "
            f"(1 reader tick = 720 ns); RST rose {rst_up / etu:.3f} etu after the trigger; {c.info['records']} "
            f"records, burst SCK = clk/{c.info['burst'][1]}")
    return c, recs, fset, etu, sig, ch, card, line


@model_clock(ISO_FT)
def iso_cap_rx(rig, store):
    """Reader timing, la_capture on core 1 in place of the clock core: RST, the ATR, a READ BINARY whose 3rd character the card corrupts twice (our error signals)."""
    sc, plan = iso_rx_plan(cap=True)
    c, recs, fset, etu, sig, ch, card, line = iso_cap(rig, store, "iso_cap_rx", sc, plan)
    check(recs == plan["words"][:8] and (fset >> 7) & 1, f"the first 8 records (the host can't drain during the "
                                                          f"burst) {[hex(w) for w in recs]}, FOV {(fset >> 7) & 1}")
    ck = CLK_NS(rig)
    check(len(sig) == 2, f"{len(sig)} error signals")
    rel = [(t - max(s for s in card if s < t)) / etu for t, _ in sig]
    lens = sorted({round(dd / ck) for _, dd in sig})
    check(all(10.3 <= r <= 10.7 for r in rel) and lens == [round(1.5 * etu / ck)], f"error signals {rel} {lens}")
    gaps = sorted({round((b - a) / ck) for a, b in zip(ch, ch[1:]) if b - a < 13 * etu})
    check(gaps == [round(12 * etu / ck)], f"our characters {gaps} clocks apart")
    det(store, "iso_cap_rx", recs=recs, n_signals=len(sig))      # (the times are measurements, not compared)
    return [f"our error signals: {span(rel, '{:.3f}')} etu after the start of the card's character (10.5 +- 0.2), "
            f"{lens} clocks long (1.5 etu = {1.5 * etu / ck:.0f}); our {len(ch)} header characters {gaps} clocks apart "
            f"(12 etu); records: the first 8 = the model run's (then FOV: nobody drains during the burst)", line]


@model_clock(ISO_FT)
def iso_cap_tx(rig, store):
    """Reader timing, la_capture in place of the clock core: the card (no ATR) rejects header and data bytes (repetitions), then a header with N = 5."""
    out = []
    for name, groups, card_f, key, n_ends in (
            ("iso_seq_tx", [[p3.ISO_RESET]] + t0_update([0x00, 0xD6, 0x00, 0x00, 0x03], b"\x11\x22\x33"),
             lambda: p3.IsoCard(b"", reject={2: 1, 6: 2}), "rej mute", 4),
            ("iso_seq_guard", [[p3.ISO_RESET], p3.iso_send(bytes([0x00, 0xB0, 0x00, 0x00, 0x02]), 5) + p3.iso_recv(5)],
             lambda: p3.IsoCard(b""), "guard 5", 2)):
        sc, plan = iso_plan(name, groups, card_f, key, n_ends, cap=True)
        c, recs, fset, etu, sig, ch, card, line = iso_cap(rig, store, name, sc, plan)
        same_words(name, recs, plan)
        ck = CLK_NS(rig)
        gaps = [(b - a) / etu for a, b in zip(ch, ch[1:]) if b - a < 20 * etu]
        if name == "iso_seq_tx":
            sends = [x[2] for x in iso_cmds(recs) if x[0] == "send"]
            check([(s[1], s[2]) for s in sends] == [(0, 1), (0, 2)], f"SEND records {sends}")
            reps = [g for g in gaps if 12.5 < g < 15]
            norm = sorted({round(g * etu / ck) for g in gaps if g <= 12.5})
            check(len(reps) == 3 and all(14.0 <= g <= 14.1 for g in reps) and norm == [round(12 * etu / ck)],
                  f"gaps {gaps}")
            out.append(f"the card rejects header byte 3 once and data byte 2 twice: SEND records (not sent, "
                       f"repetitions) {[(s[1], s[2]) for s in sends]}; each repetition {span(reps, '{:.3f}')} etu "
                       f"after the previous start (2 etu after the card's 1.5-etu error signal ends); the other "
                       f"characters {norm} clocks apart (12 etu)")
        else:
            gc = sorted({round(g * etu / ck) for g in gaps})
            check(gc == [round(17 * etu / ck)], f"N = 5: gaps {gaps}")
            out.append(f"N = 5: our header characters {gc} clocks apart (17 etu)")
        out.append("  " + line + "; records = the model run's")
        det(store, name, recs=recs)
    return out


@model_clock(ISO_FT)
def iso_host(rig, store):
    """Both cores (reader + card clock): the ATR and READ BINARY with 2 error signals, the reader's I/O drive and RST read by the host polling the GPIO."""
    sc, plan = iso_rx_plan(cap=True)
    etu = iso_etu_ns(rig.f_clk)
    prep3(rig, sc, sched=far_sched3(rig, plan, sc.pg_idle))
    span_ns = (TRIG_S * 1e9 + plan["t_last"] + 3e6)
    ctrl(rig, 0b11)
    smp, n_reads, gmax = [], 0, 0.0      # smp: (time, uio_out, uio_oe, time of the read before) at each change
    rig.pg_start()
    if rig.model:
        mh, per = rig.mh, 1e9 / rig.f_clk
        c0 = mh.chip.cycle
        tp = 0.0
        while (mh.chip.cycle - c0) * per < span_ns:
            mh.step(8)
            _, out, oe = mh.chip.outputs()
            t = (mh.chip.cycle - c0) * per
            n_reads += 1
            gmax = max(gmax, t - tp)
            if not smp or (out & 3, oe & 3) != smp[-1][1:3]:
                smp.append((t, out & 3, oe & 3, tp))
            tp = t
    else:
        inp = rig.board.regs.inp
        gc.disable()                     # no collector pauses while polling
        try:
            t0 = time.perf_counter_ns()
            tp, last = 0, None
            while True:
                v = inp()
                t = time.perf_counter_ns() - t0
                n_reads += 1
                if t - tp > gmax:
                    gmax = t - tp
                k = (v >> 8) & 3, (v >> 16) & 3
                if k != last:
                    smp.append((t, k[0], k[1], tp))
                    last = k
                tp = t
                if t > span_ns:
                    break
        finally:
            gc.enable()
    recs = rd(rig, FIFO(0), rxn(rig.read(STATUS(0))))
    finish(rig)
    drv, rstl, win = [], [], []
    for t, out, oe, tp in smp:
        d = 0 if (oe & 1) and not out & 1 else 1
        r = (out >> RST) & 1
        if not drv or drv[-1][1] != d:
            drv.append((t, d))
            win.append((t - tp) / 1e3)
        if not rstl or rstl[-1][1] != r:
            rstl.append((t, r))
    keep_win = sorted(win)
    keep(store, "iso_host", drive=drv, rst=rstl, t_end=span_ns, etu=etu)
    rise = next(t for t, v in rstl if v == 1)
    pd = pf.bit_edges(plan["drive"], IO)
    pr = next(t for t, v in pf.bit_edges(plan["drive"], RST) if v == 1)
    b_rel = [(t - rise, v) for t, v in drv if t > rise]
    m_rel = [(t - pr, v) for t, v in pd if t > pr]
    dd = edges_vs(b_rel, m_rel)
    check(dd is not None, f"the reader's drive: {len(b_rel)} host-read edges, the model's {len(m_rel)}")
    bdec = p2.uart_decode(strip_signals(b_rel, etu), b_rel[-1][0] + 20 * etu, etu)
    mdec = p2.uart_decode(strip_signals(m_rel, etu), m_rel[-1][0] + 20 * etu, etu)
    check([c["byte"] for c in bdec] == [c["byte"] for c in mdec] == [0x00, 0xB0, 0x00, 0x00, 0x04] and
          all(c["parity_ok"] for c in bdec), f"the reader's characters {[(hex(c['byte']), c['parity_ok']) for c in bdec]}")
    nsig = (len(b_rel) - len(strip_signals(b_rel, etu))) // 2
    check(recs == plan["words"][:len(recs)] and len(recs) == 8, f"records {[hex(w) for w in recs]}")
    det(store, "iso_host", chars=[c["byte"] for c in bdec])
    return [f"host poll: {n_reads} GPIO reads, at most {gmax / 1e3:.1f} us apart; each drive edge seen within "
            f"{span(keep_win, '{:.1f}')} us of the read before it (1 etu = {etu / 1e3:.2f} us); RST rose "
            f"once; the reader's drive: {len(b_rel)} edges, the model's drive edge for edge, board - model "
            f"{span([x / 1e3 for x in dd], '{:+.1f}')} us after RST's rise (host timing)",
            f"the drive read as characters (even parity, 1 etu bits): {bytes(c['byte'] for c in bdec).hex()} "
            f"(the READ BINARY header, parity right) and {nsig} error signals of 1.5 etu; the first 8 records = the "
            f"model run's",
            "not captured: the card clock (4.17 MHz on uo1; iso_clk measures it) and exact edge times (host-timed)"]


@model_clock(50e6)
def iso_clk(rig, store):
    """iso7816_clk's CLK on uio2, measured by pe_lab's SPI device (SCK rise-to-rise in 2.5 ns steps, rises counted)."""
    clk = fw("iso7816_clk", CLK=2)
    d = iso_d(rig.f_clk)
    setup(rig, mode=SPI, owners={2: 1}, poe=1 << 2)
    load(rig, 1, clk, clkdiv=p3.iso_clkdivs(d)[1])
    rig.spid_arm(bytes(16), 0)
    if rig.model:
        c0 = rig.mh.chip.cycle
    else:
        c0 = rig.board.cycles()
    ctrl(rig, 0b10)
    rig.wait(0.5e-3 if rig.model else 8e-3)
    rises, pmin, pmax = rig.spid_status()
    c1 = rig.mh.chip.cycle if rig.model else rig.board.cycles()
    finish(rig)
    want = round(2 * d * 8)
    check(pmin == pmax == want, f"CLK rise to rise {pmin}..{pmax} dclk, want {want}")
    exp = (c1 - c0) / (2 * d)
    check(0.9 * exp <= rises <= exp + 1, f"{rises} rises in {c1 - c0} clocks")
    det(store, "iso_clk", per=[pmin, pmax])
    return [f"CLK period {pmin} x 2.5 ns = {pmin * 2.5:.1f} ns every time ({1e3 / (pmin * 2.5):.4f} MHz = f_clk / "
            f"{2 * d}); {rises} rises in {c1 - c0} protoemu clocks (expected <= {exp:.0f}: the arm and the reads "
            f"are host-timed)"]


# ================================================================ RC-5

RC5_TX_F = 5e6                   # model clock for clock-resolution captures (on = 46 clocks >= la_capture's 7)
RX_PIN = 0


def rc5_tx_cfg(f):
    return dict(clkdiv=host.clkdiv_for(f, 108e3), side_pins=V0)


def rc5_tick(f):
    cd = rc5_tx_cfg(f)["clkdiv"]
    return cd / 256 * 1e9 / f


def tsop_envelope(led, t_end, step=1000.0):
    """The LED's edges through the 36 kHz receiver-module model (pe.postfreeze.IrReceiverModule): its output edges."""
    m = pf.IrReceiverModule(f_carrier=p3.RC5_F)
    out, i, lvl = [(0.0, 1)], 0, 0
    ts = sorted({t for t, _ in led} | {k * step for k in range(int(t_end / step) + 1)})
    for t in ts:
        while i < len(led) and led[i][0] <= t:
            lvl = led[i][1]
            i += 1
        o = m.step(t, lvl)
        if o != out[-1][1]:
            out.append((t, o))
    return out


LAST_TX = {}


@model_clock(RC5_TX_F)
def rc5_tx_carrier(rig, store):
    """rc5_tx -> V0 -> la_capture at clock resolution: a frame (5, 53) and an RC-5X frame (20, 70); carrier, bursts, half bits."""
    out = []
    ck, tk = CLK_NS(rig), rc5_tick(rig.f_clk)
    for name, (a, cm) in (("rc5_tx_5_53", (5, 53)), ("rc5_tx_20_70", (20, 70))):
        dur = 0.5e-3 + 1.4e-3 + 14 * p3.RC5_BIT / 1e9 + 2e-3
        c = pfd.capture(rig, fw("rc5_tx", IR=V0), rc5_tx_cfg(rig.f_clk), mask=1, prefill=[p3.rc5_word(a, cm)],
                        owners={V0: 0}, trig_s=0.5e-3, burst=pfd.burst_plan(rig, dur))
        e = c.edges(0)
        LAST_TX[name] = (e, c.t_end)
        env = tsop_envelope(e, c.t_end)
        fr, errs = p3.rc5_decode(env, c.t_end)
        check(len(fr) == 1 and not errs and p3.rc5_fields(fr[0][1])[:2] == (a, cm), f"decoded {fr} {errs}")
        tog = p3.rc5_fields(fr[0][1])[2]
        rises = [t for t, v in e if v == 1]
        per = sorted({round((y - x) / ck) for x, y in zip(rises, rises[1:]) if y - x < 6 * tk})
        on = sorted({round((y[0] - x[0]) / ck) for x, y in zip(e, e[1:]) if x[1] == 1})
        bursts = pf.carrier_bursts(e, 2 * 3 * tk)
        nper = sorted({n for _, _, n in bursts})
        b0 = bursts[0][0]
        off = [(b - b0) / tk - round((b - b0) / tk / 96) * 96 for b, _, _ in bursts]
        pin_ = [y - x for x, y in zip(rises, rises[1:]) if y - x < 6 * tk]
        fc = 1e9 / (sum(pin_) / len(pin_))
        check(set(per) <= {1388, 1389} if not rig.model else True, f"carrier periods {per} clocks")
        check(set(on) <= {462, 463} if not rig.model else True, f"on {on} clocks")
        check(nper == [32, 64] and max(abs(x) for x in off) * tk <= ck + 0.5, f"bursts {nper}, off grid {off}")
        det(store, name, bits=fr[0][1], nper=nper, per_ticks=sorted({round(p * ck / tk) for p in per}))
        keep(store, name, led=e, env=env, t_end=c.t_end)
        out.append(f"({a}, {cm}) toggle {tog}: decoded from the receiver-module model's output; carrier periods {per} "
                   f"clocks (mean {fc:.1f} Hz), on {on}; bursts of {nper} periods, every burst start on the 96-tick "
                   f"half-bit grid ({max(abs(x) for x in off) * tk:.0f} ns off at most); {c.info['records']} records")
    return out


@model_clock(432e3)
def rc5_tx_frames(rig, store):
    """rc5_tx: 5 keys (a new key, 2 repeats, 2 new keys, one RC-5X) at 25 us capture ticks: toggle bits and the 113.78 ms frame period."""
    keys = [(0, 12, False), (0, 12, True), (0, 12, True), (5, 53, False), (20, 70, False)]
    la_div = max(1, round(25e-6 * rig.f_clk))
    dur = 0.5e-3 + 1.4e-3 + 4 * 12288 * 1e9 / 108e3 / 1e9 + 30e-3
    c = pfd.capture(rig, fw("rc5_tx", IR=V0), rc5_tx_cfg(rig.f_clk), mask=1,
                    prefill=[p3.rc5_word(a, cm, r) for a, cm, r in keys], owners={V0: 0}, trig_s=0.5e-3, la_div=la_div,
                    burst=pfd.burst_plan(rig, dur))
    check(c.info["burst"][1] * 32 < 7 * la_div, "the burst drains slower than la_capture's records")
    bursts = pfd.nec_envelope_from_records(c)
    env = [(0.0, 1)]
    for a, b in bursts:
        env += [(a, 0), (b, 1)]
    fr, errs = p3.rc5_decode(env, c.t_end)
    got = [p3.rc5_fields(b) for _, b in fr]
    check(not errs and [g[:2] for g in got] == [(a, cm) for a, cm, _ in keys], f"decoded {got} {errs}")
    tog = [g[2] for g in got]
    check(tog[0] == tog[1] == tog[2] != tog[3] != tog[4], f"toggle bits {tog}")
    tk = rc5_tick(rig.f_clk)
    starts = [t for t, _ in fr]
    per = [(b - a) / tk for a, b in zip(starts, starts[1:])]
    check(all(abs(p - 12288) * tk <= c.tick_ns + 1 for p in per), f"frame period {per} ticks")
    keep(store, "rc5_tx_frames", env=env, t_end=c.t_end)
    det(store, "rc5_tx_frames", got=got)
    return [f"{[(a, cm, 'repeat' if r else 'new') for a, cm, r in keys]} -> decoded {got} from the envelope (burst "
            f"starts to the {c.tick_ns / 1e3:.0f} us tick); toggle bits {tog}",
            f"frame starts {span([p * tk / 1e6 for p in per], '{:.3f}')} ms apart = {span(per, '{:.2f}')} ticks "
            f"(12288 = 113.78 ms; +-1 capture tick); {c.info['records']} records"]


def rx_run(rig, seqs, n_recs, t0_s=TRIG_S):
    sched = pfd.pg_from_levels(rig, seqs, other=0, bit=RX_PIN, t0_s=t0_s)
    check(len(sched) <= 1024, f"{len(sched)} generator entries")
    setup(rig, mode=PG, pg_init=1 << RX_PIN)
    load(rig, 0, fw("rc5_rx", IR=RX_PIN, TB=128, FOV=7), in_pins=RX_PIN, clkdiv=host.clkdiv_for(rig.f_clk, 72e3))
    rig.pg_load(sched, init=1 << RX_PIN)
    rig.write(sim.A_FCLR, 0xFF)
    ctrl(rig, 1)
    rig.pg_start()
    recs = pfd.poll_records(rig, [0], lambda r: len(r[0]) >= n_recs and not rig.pg_busy(), step_s=0.01,
                            timeout_s=10, after_s=0.05)[0]
    fset = rig.read(sim.A_FSET)
    finish(rig)
    return [p3.rc5_record(w) for w in recs], fset, len(sched)


@model_clock(288e3)
def rc5_rx_tolerance(rig, store):
    """rc5_rx (uio0) fed by the generator: an independent remote's envelope 10 % fast/slow, bursts +-150 us, 4 frames each."""
    out = []
    for scale, st in [(1.0, 0), (0.9, 0), (1.1, 0), (1.0, 150), (1.0, -150), (0.9, 150), (1.1, -150)]:
        rng = random.Random(int(scale * 10) + st)
        sent, seqs = [], [[(1, 20e6)]]
        for k in range(4):
            a, cm, tg = rng.randrange(32), rng.randrange(128), k & 1
            sent.append((a, cm, tg))
            w = p3.rc5_envelope(a, cm, tg, scale, st * 1e3)
            seqs.append(w + [(1, 90e6)])
        recs, fset, n = rx_run(rig, seqs, len(sent))
        got = [(r.get("address"), r.get("command"), r.get("toggle")) for r in recs]
        check(got == sent and not (fset >> 7) & 1, f"x{scale}, {st:+d} us: {recs}")
        out.append(f"x{scale}, bursts {st:+d} us: {got} = sent ({n} generator entries)")
    det(store, "rc5_rx_tolerance", ok=True)
    return out


@model_clock(288e3)
def rc5_rx_errors(rig, store):
    """Negative controls: a 200 us glitch ignored, a frame cut after 8 bits and a bit without its mid-bit transition reported; then a good frame."""
    cut = p3.rc5_envelope(1, 2, 0)
    tt = 0
    for i, (lv, d) in enumerate(cut):
        tt += d
        if tt > 8 * p3.RC5_BIT:
            cut = cut[:i]
            break
    halves = []
    for b in p3.rc5_bits(3, 4, 1):
        halves += [1 - b, b]
    halves[13] = halves[12]
    flat = []
    for x in halves:
        lvl = 0 if x else 1
        if flat and flat[-1][0] == lvl:
            flat[-1] = (lvl, flat[-1][1] + p3.RC5_HALF)
        else:
            flat.append((lvl, p3.RC5_HALF))
    seqs = [[(1, 20e6)], [(0, 200e3), (1, 20e6)], cut + [(1, 30e6)], (flat[1:] if flat[0][0] == 1 else flat) +
            [(1, 30e6)], p3.rc5_envelope(7, 33, 0) + [(1, 30e6)]]
    recs, fset, n = rx_run(rig, seqs, 3)
    check([r["kind"] for r in recs] == ["error", "error", "frame"] and recs[-1]["address"] == 7 and
          recs[-1]["command"] == 33, f"{recs}")
    det(store, "rc5_rx_errors", recs=[sorted(r.items()) for r in recs])
    return [f"records {[(r['kind'], r.get('bits_left', (r.get('address'), r.get('command')))) for r in recs]}: the "
            f"glitch ignored, the cut frame and the flat bit reported, then (7, 33)"]


@model_clock(288e3)
def rc5_loop(rig, store):
    """rc5_tx's own frames, captured at clock resolution, through the receiver-module model on the host, played into rc5_rx by the generator."""
    if not LAST_TX:
        return ["not run: rc5_tx_carrier did not run before it in this session"]
    seqs, want = [[(1, 20e6)]], []
    for name, (e, t_end) in LAST_TX.items():
        env = tsop_envelope(e, t_end)
        fr, _ = p3.rc5_decode(env, t_end)
        want.append(p3.rc5_fields(fr[0][1]))
        lv = [x for x in env if x[0] > 0]
        w = [(v, t2 - t1) for (t1, v), (t2, _) in zip(lv, lv[1:])]
        seqs.append(w + [(1, 60e6)])
    recs, fset, n = rx_run(rig, seqs, len(want))
    got = [(r.get("address"), r.get("command"), r.get("toggle")) for r in recs]
    check(got == [tuple(x) for x in want], f"rc5_rx {got}, sent {want}")
    det(store, "rc5_loop", got=got)
    return [f"rc5_tx's frames {[tuple(x) for x in want]} (rc5_tx_carrier's captures) -> receiver-module model (host) -> "
            f"generator -> rc5_rx: {got}"]


# ================================================================ main

GROUPS = {
    "cec": [cec_send, cec_send16, cec_receive, cec_tolerance, cec_errors, cec_arb_lost, cec_arb_won, cec_free_time],
    "smbus": [smbus_protocols, smbus_nopec, smbus_pec_errors, smbus_nacks, smbus_stretch, smbus_timeout, smbus_busy,
              smbus_arbitration],
    "iso": [iso_t0, iso_inverse, iso_nulls, iso_rx_errors, iso_tx_errors, iso_misc, iso_cap_rx, iso_cap_tx, iso_host,
            iso_clk],
    "rc5": [rc5_tx_carrier, rc5_tx_frames, rc5_rx_tolerance, rc5_rx_errors, rc5_loop],
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


def sigrok_main(path):
    from pe import sigrok
    if sigrok.cli() is None:
        sys.exit(sigrok.INSTALL_HINT)
    d = json.loads(pathlib.Path(path).read_text())
    meta = d.get("_meta", {})
    print(f"captures from {path} ({'model' if meta.get('model') else 'hardware'}, {meta.get('utc')})")
    for name, r in d.get("_captures", {}).items():
        try:
            tr = sigrok.Trace()
            if name.startswith("cec"):
                pf.to_trace(tr, [(t + 1e6, v) for t, v in r["bus"]], "cec", r["t_end"] + 1e6)
                got, warn = sigrok.cec(tr, "cec", res_ps=100_000)
                print(f"[{name}] sigrok cec: {[(f, s) for _, f, s in got]} {[w for _, w in warn]}")
            elif name.startswith("smbus"):
                log = [(t + 10e3, v) for t, v in r["log"]]
                pf.to_trace(tr, pf.bit_edges(log, SCL), "scl")
                pf.to_trace(tr, pf.bit_edges(log, SDA), "sda")
                tr.end(round((r["t_end"] + 10e3) * 1000))
                toks = sigrok.i2c(tr, "scl", "sda", res_ps=10_000)
                frames, cur = [], None
                for _, k in toks:
                    if k == "S":
                        cur = []
                        frames.append(cur)
                    elif isinstance(k, tuple) and k[0] in ("AW", "AR") and cur is not None:
                        cur.append((k[1] << 1) | (k[0] == "AR"))
                    elif isinstance(k, tuple) and k[0] in ("DW", "DR") and cur is not None:
                        cur.append(k[1])
                pec = [p3.smbus_pec(bytes(f[:-1])) == f[-1] if len(f) > 1 else None for f in frames]
                print(f"[{name}] sigrok i2c: {[bytes(f).hex() for f in frames]}; PEC recomputed over sigrok's bytes "
                      f"= the last byte: {pec}")
            elif name.startswith("iso"):
                key = "log" if "log" in r else "drive"
                e = pf.bit_edges(r[key], IO) if key == "log" else r["drive"]
                pf.to_trace(tr, [(t + 1e6, v) for t, v in e], "io", r["t_end"] + 1e6)
                data, warn = sigrok.uart_parity(tr, "io", round(1e9 / r["etu"]), res_ps=100_000)
                print(f"[{name}] sigrok uart (even parity, {1e9 / r['etu']:.1f} baud, "
                      f"{'the line' if key == 'log' else 'the reader drive, host-read'}): "
                      f"{bytes(b for _, b in data).hex()} {[w for _, w in warn]}")
            elif name.startswith("rc5"):
                pf.to_trace(tr, [(t + 1e6, v) for t, v in r["env"]], "ir", r["t_end"] + 30e6)
                got = sigrok.ir_rc5(tr, "ir", res_ps=100_000)
                gx = sigrok.ir_rc5(tr, "ir", protocol="extended", res_ps=100_000)
                print(f"[{name}] sigrok ir_rc5 (address, command, toggle): {[tuple(g[1:]) for g in got]}; "
                      f"extended: {[tuple(g[1:]) for g in gx]}")
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
        return pf2d.compare_main(*a.groups[1:3])
    if a.groups[:1] == ["sigrok"]:
        return sigrok_main(a.groups[1])
    bad = [g for g in a.groups if g not in GROUPS and g not in FUNCS]
    if bad:
        ap.error(f"unknown group(s) {bad}; choose from {list(GROUPS)} or {list(FUNCS)}")
    if a.plans and pathlib.Path(a.plans).exists():
        PLANS["data"].update(json.loads(pathlib.Path(a.plans).read_text()))
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
