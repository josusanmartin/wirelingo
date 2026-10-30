#!/usr/bin/env python3
"""Firmwares that had only run in simulation, on the KV260 build of the chip
RTL at 50 MHz: ws2812, spi_master, i2c_sniff, ps2_device, onewire, jtag,
can_tx + can_rx, eth10_tx + eth10_rx. Run as root on the Kria with the
protoemu app (pe_lab) loaded. Nothing leaves the FPGA: every line is a
virtual pin or a uio pad that a core or pe_lab drives; no PMOD pin is enabled.
(The USB device firmwares are in usb_replay_demo.py: they need the 48 MHz app.)

    python3 fpga/kv260/host/legacy_demo.py --model --save ml.json --plans pl.json   # cycle model first
    scp pl.json ubuntu@kria.[tailnet domain redacted]:legacy_logs/
    ssh ubuntu@kria.[tailnet domain redacted] sudo python3 -u protoemu/fpga/kv260/host/legacy_demo.py \\
        --save legacy_logs/hl.json --plans legacy_logs/pl.json
    python3 fpga/kv260/host/legacy_demo.py compare hl.json ml.json
    python3 fpga/kv260/host/legacy_demo.py sigrok hl.json

Method (the post-freeze rounds' toolkit, postfreeze_demo / postfreeze2_demo):
* Transmitters run on core 0 behind the 2-word trigger preamble; core 1 runs
  la_capture (1 tick = 1 clock) on their pins and one READ_FIXED burst drains
  it, or, for edges closer than la_capture's 7-tick dead time (10BASE-T's
  50 ns half bits), usb_sampler.pasm (every clock, 3 pins).
* Far ends that answer (1-Wire slave, JTAG TAP, I2C EEPROM, CAN nodes) are
  pre-timed: the scenario first runs on the cycle model with a pins-only model
  of the far end in the loop (seeing every uio pad), and pe_lab's pattern
  generator replays the far end's own edges, relative to the trigger, on the
  board. Far ends react a margin after what they see (more than the firmware's
  start offset, under a tick), so a pre-timed answer never precedes the edge
  it answers.
* Receivers get waveforms from the generator at clock-exact times (or on the
  45.93 MHz alternate clock, phase walking).
* Two cores on one wired-AND line: pe_lab's I2C-bus mode joins uio0/uio2 with
  a pull-up, both nodes open drain (CAN).
* Judges: the decoders and models of the firmwares' model tests (copied here
  where the tests are not on the Kria), `compare` with the --model run, sigrok
  off the board.
"""
import argparse
import binascii
import json
import math
import pathlib
import random
import struct
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import postfreeze_demo as pfd  # noqa: E402  (sets up sys.path for tools/)
import postfreeze2_demo as p2d  # noqa: E402
import spi_uart_demo as sud  # noqa: E402
import usb_replay_demo as urd  # noqa: E402  (imported here: a first import inside a capture takes ms on the Kria)
from postfreeze_demo import (FIFO, STATUS, PG, SPI, I2C, GPIO, TRIG, PRE, V0, HALTED, FAULT, HwRig,  # noqa: E402
                             ModelRig, check, fw, load, setup, finish, model_clock, split_long, trigger_preamble)
from postfreeze2_demo import rd, rxn, det, keep, span, ctrl, PLANS  # noqa: E402
from pe import asm, host, la, sim  # noqa: E402

TRIG_S = p2d.TRIG_S                      # 1 ms: the generator raises uio7 this long after its start
VERSION = "legacy-1"
QUAD_RD_FIXED = 0x03 | 0x08 | 0x40


def clkdiv(f_clk, f_tick):
    """CLKDIV register value (8.8 fixed point) for ticks at f_tick."""
    v = round(f_clk / f_tick * 256)
    check(256 <= v < 1 << 24, f"CLKDIV {v / 256} out of range")
    return v


def ctrl_raw(rig, v):
    """A CTRL write as is (enable bits, restart [11:8], FIFO clear [19:16])."""
    rig.write(sim.A_CTRL, v)
    if rig.model:
        rig.wait(1e-6)


def go_burst(rig, core, n, div, quad=True):
    """Start the generator and, right after it, one READ_FIXED burst of core's RXF
    (as usb_replay_demo: the two AXI writes back to back)."""
    return urd.go_burst(rig, core, n, div, quad)


TRS = 0.2e-3                    # captures: generator start -> trigger (the burst starts a few us after the generator)


def capture_q(rig, prog, cfg, *, la_mask, la_base=0, dur_s, trig_s=TRS, div=None, sched=None, pg_init=0, **kw):
    """postfreeze2_demo.run_capture with a quad burst (8 x div clocks per word)
    started right after the generator (go_burst), so the trigger can come
    trig_s after it; dur_s = the run after the trigger. Then an idle record
    (the lines' end state, timed). Returns the Capture (c.recs: core 0's RX
    words). A gap record (the drain too slow) fails the run."""
    total = trig_s + dur_s + 50e-6
    if div is None:
        div = max(1, math.ceil(total * rig.f_clk / (4096 * 8)))
    check(div <= 255 and 4096 * 8 * div >= total * rig.f_clk, f"no quad burst lasts {total * 1e3:.2f} ms")
    if sched is None:
        sched = [(round(trig_s * rig.f_clk), pg_init | (1 << TRIG))]
    p2d.prep(rig, prog, cfg, sched=sched, pg_init=pg_init, la_mask=la_mask, la_base=la_base, **kw)
    ctrl(rig, 0b11)
    words = go_burst(rig, 1, 4096, div, True)
    rig.wait(1.5 * 65536 / rig.f_clk)
    words += rd(rig, FIFO(1), rxn(rig.read(STATUS(1))))
    st0 = rig.read(STATUS(0))
    recs = rd(rig, FIFO(0), rxn(st0))
    st = finish(rig)
    c = pfd.Capture(words, la_mask, 1e9 / rig.f_clk)
    check(c.cap.exact, f"the capture has {len(c.cap.gaps)} gap(s)")
    check(st0 & 15 == 0, f"{st0 & 15} TX FIFO words left: the burst ended too soon?")
    c.info = {"records": sum(1 for w in words if w), "burst": [4096, div, True], "status": st}
    c.recs = recs
    return c


# ================================================================ pre-timing with pins-only far ends

class FarM:
    """A pins-only far end on the uio lines in `mask`: step(t, pads) sees every
    resolved uio pad each clock (t in ns) and sets self.drive (a byte; 1 =
    released on its lines); a change becomes visible the next clock and is
    logged then, so the generator replays it at the same clock."""

    def __init__(self, mask, per, idle=0xFF):
        self.mask, self.per = mask, per
        self.drive = idle & mask
        self.log = [(0.0, self.drive)]

    def level(self, t):
        return self.drive

    def step(self, t, pads):
        new = self.react(t, pads) & self.mask
        if new != self.drive:
            self.drive = new
            self.log.append((t + self.per, new))

    def react(self, t, pads):
        return self.drive

    def edges(self):
        return self.log

    def info(self, t0):
        return {}


def far_src():
    """Plans depend on the far-end models' code."""
    import hashlib
    import inspect
    objs = [FarM, predict_m, EepromFar, OneWireSlave, TapFar, CanNode]
    return hashlib.sha1("".join(inspect.getsource(o) for o in objs).encode()).hexdigest()[:12]


def predict_m(name, f_model, prog, cfg, *, far_factory, far_key, mask, pg_idle, dur_s, done=None, tail_s=0.0,
              **kw):
    """postfreeze2_demo.predict with a far end that sees all uio pads and drives
    the lines in `mask`. Returns {far: [(ns after the trigger, byte)], pins:
    [(ns, uio | V0..V7 << 8)], words, info, t_last}."""
    key = p2d.plan_key(VERSION, far_src(), name, f_model, prog.words, cfg, far_key, mask, pg_idle, round(dur_s, 9),
                       round(tail_s, 9), TRIG_S,
                       sorted((k, v if not isinstance(v, dict) else sorted(v.items())) for k, v in kw.items()))
    hit = PLANS["data"].get(name)
    if hit and hit.get("key") == key:
        PLANS["used"] += 1
        return hit
    m = ModelRig(f_model)
    t_trig = round(TRIG_S * f_model)
    p2d.prep(m, prog, cfg, sched=[(t_trig, pg_idle | (1 << TRIG))], pg_init=pg_idle, **kw)
    mh, per = m.mh, 1e9 / f_model
    m.write(sim.A_CTRL, 0b01 | (0b01 << 8))
    c0 = mh.chip.cycle
    m.pg_start()
    t_tr = (c0 + t_trig) * per
    far = far_factory(t_tr, per)
    base_pg = mh.pg_value
    pins = []

    def bus_uio():
        t = mh.chip.cycle * per
        v = (base_pg() & ~mask) | (far.level(t) & mask)
        _, out, oe = mh.chip.outputs()
        r = ((oe & out) | (~oe & v)) & 0xFF
        far.step(t, r)
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
    plan = {"key": key, "f_model": f_model, "far": [(t - t_tr, v) for t, v in far.edges() if t - t_tr > -1e3],
            "far0": far.edges()[0][1], "pins": rel, "words": words, "info": far.info(t_tr),
            "t_last": max(chg) if chg else 0.0}
    PLANS["data"][name] = plan
    PLANS["made"] += 1
    return plan


def far_sched_m(rig, far, *, mask, pg_idle, trig_s=TRS):
    """Generator entries: uio7 rises at trig_s, then the lines in mask follow the far end."""
    f = rig.f_clk
    t_trig = round(trig_s * f)
    v = pg_idle | (1 << TRIG)
    out = [(t_trig, v)]
    for t, lvl in far:
        if t <= 0:
            continue
        c = t_trig + max(1, round(t * f / 1e9))
        v = (v & ~mask) | (lvl & mask)
        if c <= out[-1][0]:
            out[-1] = (out[-1][0], v)
        else:
            out.append((c, v))
    return split_long(out, out[-1][0] + 1)


def bits_from(pins, bit):
    """[(t, level)] of one uio bit (or V0.. as 8 + i) from a pins log."""
    return pfd.pf.bit_edges(pins, bit)


def edges_vs(board, model, *, after=0.0):
    """Board edges of one line against the model's (ns after the trigger): the
    differences, or None if the levels differ."""
    b = [x for x in board if x[0] > after]
    m = [x for x in model if x[0] > after]
    if len(b) != len(m) or [v for _, v in b] != [v for _, v in m]:
        return None
    return [x[0] - y[0] for x, y in zip(b, m)]


# ================================================================ WS2812

WS_PIN = V0


def ws_decode(edges, f_clk):
    """[(t_ns, level)] of DOUT -> (colours, highs_ns, periods_ns)."""
    highs, lows, periods = [], [], []
    rises = [t for t, v in edges if v == 1]
    for (t, v), (t2, _) in zip(edges, edges[1:]):
        (highs if v == 1 else lows).append(t2 - t)
    periods = [b - a for a, b in zip(rises, rises[1:]) if b - a < 5000]
    bits = [1 if h > 600 else 0 for h in highs]
    cols = [int("".join(map(str, bits[24 * i:24 * i + 24])), 2) for i in range(len(bits) // 24)]
    return cols, highs, periods


@model_clock(50e6)
def ws2812(rig, store):
    """ws2812 on V0 at 800 kbit/s (CLKDIV f/8 MHz, fractional at 50 MHz), la_capture: colours, T0H/T1H, bit period, latch."""
    prog = fw("ws2812")
    cd = clkdiv(rig.f_clk, 8e6)
    rng = random.Random(2812)
    lines = []
    for run, cols in enumerate(([0xFF0000, 0x00A55A, 0x123456, 0x000000, 0xFFFFFF, 0x800001, 0x7FFFFE, 0x55AA55],
                                [rng.getrandbits(24) for _ in range(8)])):
        c = capture_q(rig, prog, dict(side_pins=WS_PIN, clkdiv=cd), la_mask=0x01, la_base=WS_PIN,
                      owners={WS_PIN: 0}, prefill=[x << 8 for x in cols], dur_s=0.3e-3)
        e = c.edges(0)
        got, highs, periods = ws_decode(e, rig.f_clk)
        check(got == cols, f"colours {[hex(x) for x in got]} != {[hex(x) for x in cols]}")
        t0h = [h for h in highs if h < 600]
        t1h = [h for h in highs if h > 600 and h < 2000]
        check(all(250 <= h <= 550 for h in t0h) and all(700 <= h <= 1000 for h in t1h), "T0H/T1H windows")
        check(all(1100 <= p <= 1400 for p in periods), f"bit periods {span(periods)}")
        tail = c.t_end - e[-1][0]
        check(e[-1][1] == 0 and tail > 50e3, f"line not low for the 50 us latch ({tail / 1e3:.1f} us)")
        lines.append(f"run {run}: {len(cols)} colours right ({' '.join(f'{x:06x}' for x in cols[:4])} ...); "
                     f"T0H {span(t0h, '{:.0f}')} ns, T1H {span(t1h, '{:.0f}')} ns, bit {span(periods, '{:.0f}')} ns, "
                     f"then low {tail / 1e3:.0f} us")
        # (the high times depend on the fractional divider's phase at the trigger: not compared)
        det(store, f"ws2812_{run}", colours=got)
        keep(store, f"ws2812_{run}", dout=e, t_end=c.t_end)
    return lines


# ================================================================ SPI master (pe_lab's SPI device)

@model_clock(50e6)
def spi_master(rig, store):
    """spi_master (mode 0, MSB first, f_tick/4) against pe_lab's SPI device: CLKDIV 2 and 3, and CLKDIV 1 with and without SYNC_BYPASS on MISO, DELAY 0..31."""
    rng = random.Random(23)
    prog = fw("spi_master")
    lines, table = [], {}
    for div, byp in ((3, 0), (2, 0), (1, 0), (1, 1)):
        sud.device_session(rig, 0, prog, div)
        rig.write(sim.A_SYNCBYP, byp << sud.MISO)
        row, pers = [], set()
        for delay in range(32):
            ks = set()
            for _ in range(2):
                tx, resp = sud.rng_bytes(rng, 8), sud.rng_bytes(rng, 16)
                ctrl_raw(rig, 1 << 16)                      # core 0 stopped, FIFOs cleared
                rig.spid_arm(sud.dev_resp(resp, 0), delay)
                rig.write(FIFO(0), [b << 24 for b in tx], fixed=True)
                ctrl_raw(rig, 1 | (1 << 8))
                sud.wait_until(rig, sud.rx_ready(rig, 8), 0.05, 2e-5)
                got = bytes(w & 0xFF for w in rd(rig, FIFO(0), 8))
                rises, pmin, pmax = rig.spid_status()
                check(rises == 64, f"{rises} SCK rises for 8 bytes")
                check(rig.spid_rx()[8:] == tx, f"device got {rig.spid_rx()[8:].hex()} for {tx.hex()}")
                ks.add(sud.lateness(resp, 0, got))
                pers.add((pmin, pmax))
            row.append(sorted(ks, key=lambda x: 99 if x is None else x))
        rig.write(sim.A_SYNCBYP, 0)
        name = f"CLKDIV {div}{' SYNC_BYPASS' if byp else ''}"
        table[name] = row
        ok = [d for d, ks in enumerate(row) if ks == [0]]
        late = sorted({str(k) for ks in row[len(ok):] for k in ks})
        per = sorted(pers)
        head = f"{name}: SCK {rig.f_clk / 4 / div / 1e6:.2f} MHz ({4 * div} clocks), SCK rise to rise {per} x 2.5 ns"
        if div == 1 and not byp:
            # test_spi_fast: without the bypass the 2-flop synchroniser makes CLKDIV 1 read every bit a bit late
            one = [d for d, ks in enumerate(row) if ks == [1]]
            check(not ok and one == list(range(len(one))) and one, f"{name}: expected every DELAY late, got {row}")
            lines.append(f"{head}; no DELAY right: MISO read one bit late up to DELAY {max(one)}, two from "
                         f"{max(one) + 1}, as test_spi_fast predicts (the 2-flop synchroniser; spi_master_fast or "
                         f"the bypass are the fixes); MOSI right in all 64 rounds")
        else:
            check(ok and ok == list(range(len(ok))), f"{name}: correct DELAYs {ok} not a range from 0")
            lines.append(f"{head}; right up to DELAY {max(ok)} (MISO {2.5 * (max(ok) + 2):.1f} ns after the clock "
                         f"edge that dropped SCK{'' if max(ok) == 31 else f'; from DELAY {max(ok) + 1}: bits late {late}'})"
                         f"; MOSI right in all 64 rounds")
    finish(rig)
    det(store, "spi_master", table=table)
    return lines


# ================================================================ I2C sniffer

SDA, SCL = 0, 1
EV_START, EV_STOP = 0x100, 0x200


class I2cWave:
    """A pins-only I2C bus waveform (both lines, as the generator plays them):
    SDA changes in the middle of SCL low, START/STOP with SCL high."""

    def __init__(self, f_scl, t0=20e3):
        self.h = 1e9 / f_scl / 2
        self.t = t0
        self.ev = [(0.0, 0b11)]
        self.sda, self.scl = 1, 1
        self.want = []

    def _set(self, sda=None, scl=None, dt=0.0):
        self.t += dt
        if sda is not None:
            self.sda = sda
        if scl is not None:
            self.scl = scl
        self.ev.append((self.t, self.sda | (self.scl << 1)))

    def start(self):
        if not self.sda or not self.scl:            # repeated START: SDA up while SCL low, then SCL up
            self._set(sda=1, dt=self.h / 2)
            self._set(scl=1, dt=self.h / 2)
        self._set(sda=0, dt=self.h)
        self._set(scl=0, dt=self.h)
        self.want.append(EV_START)

    def byte(self, b, ack):
        for i in range(7, -1, -1):
            self._set(sda=(b >> i) & 1, dt=self.h / 2)
            self._set(scl=1, dt=self.h / 2)
            self._set(scl=0, dt=self.h)
        self._set(sda=0 if ack else 1, dt=self.h / 2)
        self._set(scl=1, dt=self.h / 2)
        self._set(scl=0, dt=self.h)
        self.want.append(b | (0 if ack else 1) << 9)

    def stop(self):
        self._set(sda=0, dt=self.h / 2)
        self._set(scl=1, dt=self.h / 2)
        self._set(sda=1, dt=self.h)
        self.want.append(EV_STOP)

    def glitch(self, ns):
        """SCL low, an SDA pulse shorter than a bit: no event (SCL low)."""
        self._set(sda=1 - self.sda, dt=self.h / 2)
        self._set(sda=1 - self.sda, dt=ns)

    def idle(self, ns):
        self._set(dt=ns)


def sniff_traffic(f_scl, rng):
    w = I2cWave(f_scl)
    w.start(); w.byte(0xA0, True); w.byte(0x10, True); w.byte(0x42, True); w.stop()
    w.idle(5 * w.h)
    w.start(); w.byte(0xA0, True); w.byte(0x10, True)
    w.start(); w.byte(0xA1, True)                           # repeated START, read
    for k in range(3):
        w.byte(rng.randrange(1, 256), k < 2)                # the master NACKs the last
    w.stop()
    w.idle(5 * w.h)
    w.start(); w.byte(0x9E, False); w.stop()                 # nobody at 0x4F
    w.idle(5 * w.h)
    w.start(); w.byte(0x80, True); w.byte(0x7F, False); w.stop()    # (a 0x00 byte with ACK is event 0,
                                                                 # which a blind burst can't tell from empty)
    return w


@model_clock(50e6)
def i2c_sniff_gen(rig, store):
    """i2c_sniff on uio0/uio1 (CLKDIV 1), the generator playing an I2C bus at 100 kHz, 400 kHz and 1 MHz: START, repeated START, STOP, ACK/NACK, reads."""
    prog = fw("i2c_sniff", SDA=SDA, SCL=SCL)
    lines = []
    for f_scl in (100e3, 400e3, 1e6):
        w = sniff_traffic(f_scl, random.Random(int(f_scl)))
        sched = []
        f = rig.f_clk
        t_trig = round(TRIG_S * f)
        sched = [(t_trig, 0b11 | (1 << TRIG))]
        for t, v in w.ev[1:]:
            c = t_trig + round(t * f / 1e9)
            vv = v | (1 << TRIG)
            if c <= sched[-1][0]:
                sched[-1] = (sched[-1][0], vv)
            else:
                sched.append((c, vv))
        check(len(sched) <= 1024, f"{len(sched)} generator entries")
        setup(rig, mode=PG, pg_init=0b11)
        load(rig, 0, prog, in_pins=SDA, clkdiv=1 << 8)
        rig.pg_load(sched, init=0b11)
        ctrl(rig, 0b01)
        dur = TRIG_S + w.t / 1e9 + 1e-3
        n, div = 4096, max(1, math.ceil(dur * f / (4096 * 32)))
        words = [x for x in go_burst(rig, 0, n, div, False) if x]
        words += rd(rig, FIFO(0), rxn(rig.read(STATUS(0))))
        finish(rig)
        # every RX word is an event; a 0 is an empty read only if no event can be 0 (a 0x00 byte
        # with ACK is 0): the bursts drain much faster than events come, so zeros are dropped and
        # the 0x00-ACK byte is not in the traffic
        check(words == w.want, f"{f_scl / 1e3:.0f} kHz: sniffer {[hex(x) for x in words]} != {[hex(x) for x in w.want]}")
        lines.append(f"{f_scl / 1e3:.0f} kHz: {len(words)} events = the waveform's "
                     f"({sum(1 for x in words if x == EV_START)} START incl. repeated, "
                     f"{sum(1 for x in words if x == EV_STOP)} STOP, "
                     f"{sum(1 for x in words if x < 0x100 or x & 0x200)} bytes with ACK bits), burst SCK/{div}")
        det(store, f"i2c_sniff_{f_scl / 1e3:.0f}k", events=words)
    return lines


class EepromFar(FarM):
    """test_i2c_sniff.Eeprom as a pins-only far end: ACKs address 0x50 and every
    written byte; drives SDA a margin (DLY) after the SCL fall it reacts to."""
    DLY = 2000.0

    def __init__(self, t_tr, per):
        super().__init__(1 << SDA, per)
        self.pscl, self.psda = 1, 1
        self.state, self.bits, self.byte, self.first = "idle", 0, 0, True
        self.pending = None          # (time, drive)

    def react(self, t, pads):
        sda, scl = (pads >> SDA) & 1, (pads >> SCL) & 1
        if scl and self.pscl and self.psda and not sda:
            self.state, self.bits, self.byte, self.first = "rx", 0, 0, True
        elif scl and self.pscl and not self.psda and sda:
            self.state = "idle"
        elif scl and not self.pscl and self.state == "rx" and self.bits < 8:
            self.byte = (self.byte << 1) | sda
            self.bits += 1
        elif self.pscl and not scl:
            if self.state == "rx" and self.bits == 8:
                ok = (self.byte >> 1) == 0x50 if self.first else True
                self.pending = (t + self.DLY, 0 if ok else 1 << SDA)
                self.state = "ack"
            elif self.state == "ack":
                self.pending = (t + self.DLY, 1 << SDA)
                self.state, self.bits, self.byte, self.first = "rx", 0, 0, False
        self.pscl, self.psda = scl, sda
        if self.pending and t >= self.pending[0]:
            d = self.pending[1]
            self.pending = None
            return d
        return self.drive


@model_clock(50e6)
def i2c_sniff_live(rig, store):
    """i2c_master (core 0, open drain) writes to a pre-timed EEPROM (generator on SDA) while i2c_sniff (core 1) watches the same pads."""
    master = fw("i2c_master", SDA=SDA, SCL=SCL)
    sniff = fw("i2c_sniff", SDA=SDA, SCL=SCL)
    cd = clkdiv(rig.f_clk, 8 * 100e3)
    START, STOP = 2, 3
    cmds = [START, 0xA0 << 24, 0x10 << 24, 0x42 << 24, STOP, START, 0xA2 << 24, STOP]
    pins = dict(owners={SDA: 0, SCL: 0}, od=0b11, pout=0b11)
    cfg = dict(out_pins=(SDA, 1), in_pins=SDA, clkdiv=cd)
    plan = predict_m("i2c_sniff_live", 50e6, master, cfg,
                     far_factory=EepromFar, far_key="eeprom-2us", mask=1 << SDA, pg_idle=0b11, dur_s=1.5e-3,
                     done=lambda w: len(w) >= 4, tail_s=0.2e-3, prefill=cmds, **pins)
    check([w & 1 for w in plan["words"]] == [0, 0, 0, 1], f"model: master replies {plan['words']}")
    sched = far_sched_m(rig, plan["far"], mask=1 << SDA, pg_idle=0b11)
    p2d.prep(rig, master, cfg, sched=sched, pg_init=0b11, prefill=cmds, **pins)
    load(rig, 1, sniff, in_pins=SDA, clkdiv=1 << 8)
    ctrl(rig, 0b11)
    rig.pg_start()
    dur = TRIG_S + plan["t_last"] / 1e9 + 0.5e-3
    recs = pfd.poll_records(rig, [0, 1], lambda r: len(r[0]) >= 4 and r[1].count(EV_STOP) >= 2,
                            step_s=1e-4, timeout_s=2 + dur, after_s=0.2e-3)
    finish(rig)
    want = [EV_START, 0xA0, 0x10, 0x42, EV_STOP, EV_START, 0xA2 | 0x200, EV_STOP]
    check([w & 1 for w in recs[0]] == [0, 0, 0, 1], f"master replies {recs[0]}")
    check(recs[1] == want, f"sniffer {[hex(x) for x in recs[1]]} != {[hex(x) for x in want]}")
    det(store, "i2c_sniff_live", master=recs[0], sniffer=recs[1])
    return [f"master: write 0x50 [10 42] ACKed by the pre-timed EEPROM, address 0x51 NACKed (replies {recs[0]}); "
            f"sniffer: {' '.join(hex(x) for x in recs[1])} (START, A0, 10, 42 with ACK, STOP, START, A2 NACK, STOP)"]


# ================================================================ PS/2 device

PS_CLK, PS_DAT = 0, 1


def ps2_frames(edges_clk, edges_dat):
    """Host's view: DAT at each CLK falling edge, 11 bits per frame."""
    def lvl(edges, t):
        v = edges[0][1]
        for te, x in edges:
            if te > t:
                break
            v = x
        return v
    falls = [t for (t, v), (_, v0) in zip(edges_clk[1:], edges_clk) if v == 0 and v0 == 1]
    bits = [lvl(edges_dat, t) for t in falls]
    frames = [bits[i:i + 11] for i in range(0, len(bits) - len(bits) % 11, 11)]
    return frames, falls


@model_clock(50e6)
def ps2_device(rig, store):
    """ps2_device (CLK uio0, DAT uio1, open drain, generator = pull-ups / host): frames with odd parity, 12.5 kHz CLK; host inhibit (CLK held low) delays the next frame."""
    prog = fw("ps2_device", CLK=PS_CLK, DAT=PS_DAT)
    cd = clkdiv(rig.f_clk, 100e3)
    codes = [0x1C, 0xF0, 0x1C, 0x5A, 0x00, 0xFF, 0x81, 0x7E]
    pins = dict(owners={PS_CLK: 0, PS_DAT: 0}, od=0b11, pout=0b11)
    cfg = dict(out_pins=(PS_DAT, 1), in_pins=PS_CLK, clkdiv=cd)
    extra = [(sim.core_reg(0, sim.C_CRCCTRL), 1 | (0 << 16) | (1 << 21))]
    lines = []
    for inhibit in (False, True):
        f = rig.f_clk
        t_trig = round(TRS * f)
        idle = 0b11
        # inhibit: the host holds CLK low before the device starts and 3 ms after
        init = idle & ~(1 << PS_CLK) if inhibit else idle
        sched = [(t_trig, init | (1 << TRIG))]
        if inhibit:
            sched += [(t_trig + round(3e-3 * f), idle | (1 << TRIG))]
        c = capture_q(rig, prog, cfg, sched=sched, pg_init=init, la_mask=0b11 | (1 << TRIG),
                      dur_s=(3e-3 if inhibit else 0) + 8.5e-3, prefill=codes, extra=extra, **pins)
        e, t_end = p2d.trig_rel(c, {"clk": PS_CLK, "dat": PS_DAT})
        frames, falls = ps2_frames(e["clk"], e["dat"])
        got = []
        for fr in frames:
            check(fr[0] == 0 and fr[10] == 1, f"start/stop bits {fr}")
            check(sum(fr[1:10]) & 1 == 1, f"parity {fr}")
            got.append(sum(b << i for i, b in enumerate(fr[1:9])))
        check(got == codes, f"frames {[hex(x) for x in got]} != {[hex(x) for x in codes]}")
        starts = [falls[i] for i in range(0, len(falls), 11)]
        per = [(b - a) / 1e3 for k, (a, b) in enumerate(zip(falls, falls[1:])) if (k + 1) % 11]
        gaps = [(b - a) / 1e3 for a, b in zip(starts, starts[1:])]
        lows = [(b[0] - a[0]) / 1e3 for a, b in zip(e["clk"], e["clk"][1:]) if a[1] == 0 and a[0] > 0]
        msg = (f"{'host inhibit (CLK low) for 3 ms first' if inhibit else 'no inhibit'}: {len(got)} frames = the "
               f"codes, parity odd; CLK fall to fall inside a frame {span(per, '{:.1f}')} us "
               f"({1e3 / (sum(per) / len(per)):.2f} kHz), CLK low {span(lows, '{:.1f}')} us; frame starts "
               f"{span(gaps, '{:.0f}')} us apart")
        if inhibit:
            msg += (f"; CLK held low by the host until 3000 us after the trigger: the first frame's first "
                    f"CLK fall {falls[0] / 1e3:.1f} us after the trigger")
            check(falls[0] > 3e6, "the device clocked while the host held CLK low")
        lines.append(msg)
        det(store, f"ps2_{'inhibit' if inhibit else 'plain'}", codes=got,
            # the device's CLK runs from its first fall (integer CLKDIV: exact in clocks)
            clk_runs=[round((b[0] - a[0]) * rig.f_clk / 1e9) for a, b in zip(e["clk"], e["clk"][1:])
                      if a[0] >= falls[0]])
        keep(store, f"ps2_{'inhibit' if inhibit else 'plain'}", clk=e["clk"], dat=e["dat"], t_end=t_end)
    return lines


# ================================================================ 1-Wire

DQ = 0
US = 1000.0


class OneWireSlave(FarM):
    """test_onewire.Slave as a pins-only far end (ns): presence after a reset,
    READ ROM (0x33) answered; react() sees the DQ pad."""

    def __init__(self, t_tr, per, rom=None, present=True):
        super().__init__(1 << DQ, per)
        self.rom, self.present = rom, present
        self.low_since, self.prev = None, 1
        self.pull_until = -1.0
        self.state, self.bits, self.byte = "idle", 0, 0
        self.tx_bits = []
        self.presence_at = None
        self.cmds = []

    def react(self, t, pads):
        if not self.present:                          # no device on the line at all
            return 1 << DQ
        line = (pads >> DQ) & 1
        if self.prev == 1 and line == 0 and t >= self.pull_until:
            self.low_since = t
            if self.state == "tx" and self.tx_bits:
                if self.tx_bits.pop(0) == 0:
                    self.pull_until = t + 30 * US
        if self.prev == 0 and line == 1 and self.low_since is not None:
            dur = t - self.low_since
            if dur > 400 * US:
                self.state, self.bits, self.byte = "cmd", 0, 0
                if self.present:
                    self.presence_at = t + 30 * US
            elif self.state == "cmd":
                self.byte |= (1 if dur < 15 * US else 0) << self.bits
                self.bits += 1
                if self.bits == 8:
                    self.cmds.append(self.byte)
                    if self.byte == 0x33 and self.rom:
                        self.state = "tx"
                        self.tx_bits = [(b >> i) & 1 for b in self.rom for i in range(8)]
                    else:
                        self.state = "idle"
            self.low_since = None
        if self.presence_at is not None and t >= self.presence_at:
            self.pull_until = t + 120 * US
            self.presence_at = None
        self.prev = line
        return 0 if t < self.pull_until else 1 << DQ

    def info(self, t0):
        return {"cmds": self.cmds}


@model_clock(50e6)
def onewire(rig, store):
    """onewire master (uio0, open drain, 1 us ticks): reset/presence, READ ROM from a pre-timed slave, no slave; la_capture of DQ."""
    prog = fw("onewire", DQ=DQ)
    cd = clkdiv(rig.f_clk, 1e6)
    pins = dict(owners={DQ: 0}, od=1 << DQ, pout=1 << DQ)
    cfg = dict(in_pins=DQ, clkdiv=cd)
    rom = bytes([0x28, 0xFF, 0x4C, 0x60, 0x91, 0x16, 0x04, 0x8C])
    lines = []
    for name, present, cmds, want in (
            ("onewire_rom", True, [0, 1 | (0x33 << 8)] + [2] * 6, [1, 0] + list(rom[:6])),
            ("onewire_absent", False, [0, 1 | (0x33 << 8), 2], [0, 0, 0xFF])):
        plan = predict_m(name, 50e6, prog, cfg, far_factory=lambda t, per, p=present: OneWireSlave(t, per, rom, p),
                         far_key=("ow", present, rom.hex()), mask=1 << DQ, pg_idle=1 << DQ, dur_s=8e-3,
                         done=lambda w, n=len(want): len(w) >= n, tail_s=0.3e-3, prefill=cmds, **pins)
        check([w & 0xFF for w in plan["words"]] == want, f"model: {plan['words']}")
        sched = far_sched_m(rig, plan["far"], mask=1 << DQ, pg_idle=1 << DQ)
        c = capture_q(rig, prog, cfg, sched=sched, pg_init=1 << DQ, la_mask=(1 << DQ) | (1 << TRIG),
                      dur_s=plan["t_last"] / 1e9 + 0.3e-3, prefill=cmds, **pins)
        recs = c.recs
        got = [w & 0xFF for w in recs]
        check(got == want, f"records {got} != {want}")
        e, t_end = p2d.trig_rel(c, {"dq": DQ})
        d = edges_vs(e["dq"], bits_from(plan["pins"], DQ))
        check(d is not None, "DQ edges differ from the model's")
        lows = [b[0] - a[0] for a, b in zip(e["dq"], e["dq"][1:]) if a[1] == 0]
        rst = [x for x in lows if x > 400e3]
        lines.append(f"{'slave present' if present else 'no slave'}: records {got} (presence, write done, "
                     f"{'ROM bytes ' + rom[:6].hex() if present else 'a read of nothing = 0xff'}); DQ lows "
                     f"{span([x / 1e3 for x in lows], '{:.1f}')} us, reset {span([x / 1e3 for x in rst], '{:.1f}')} us; "
                     f"{len(d)} DQ edges board - model {span(d, '{:.0f}')} ns")
        det(store, name, records=got)
        keep(store, name, dq=e["dq"], t_end=t_end)
    return lines


# ================================================================ JTAG

TDO, TDI, TMS, TCK = 0, 4, 5, 6
NEXT = {
    "TLR": ("RTI", "TLR"), "RTI": ("RTI", "SELDR"), "SELDR": ("CAPDR", "SELIR"),
    "CAPDR": ("SHDR", "EX1DR"), "SHDR": ("SHDR", "EX1DR"), "EX1DR": ("PDR", "UPDR"),
    "PDR": ("PDR", "EX2DR"), "EX2DR": ("SHDR", "UPDR"), "UPDR": ("RTI", "SELDR"),
    "SELIR": ("CAPIR", "TLR"), "CAPIR": ("SHIR", "EX1IR"), "SHIR": ("SHIR", "EX1IR"),
    "EX1IR": ("PIR", "UPIR"), "PIR": ("PIR", "EX2IR"), "EX2IR": ("SHIR", "UPIR"),
    "UPIR": ("RTI", "SELDR"),
}


class TapFar(FarM):
    """test_jtag.Tap as a pins-only far end: IDCODE (IR 0001) and BYPASS (IR 1111);
    TDO (uio0) updated DLY after the TCK fall it sees."""
    IDCODE = 0x4BA00477
    IR_LEN = 4

    def __init__(self, t_tr, per, dly=20.0):
        super().__init__(1 << TDO, per)
        self.state, self.prev_tck = "TLR", 0
        self.ir, self.ir_sh, self.dr_sh, self.tdo = 0b0001, 0, 0, 1
        self.dly = dly
        self.pending = None
        self.states = []

    def dr_len(self):
        return 32 if self.ir == 0b0001 else 1

    def react(self, t, pads):
        tck = (pads >> TCK) & 1
        if tck and not self.prev_tck:
            tms, tdi = (pads >> TMS) & 1, (pads >> TDI) & 1
            s = self.state
            if s == "SHDR":
                self.dr_sh = (self.dr_sh >> 1) | (tdi << (self.dr_len() - 1))
            elif s == "SHIR":
                self.ir_sh = (self.ir_sh >> 1) | (tdi << (self.IR_LEN - 1))
            self.state = NEXT[s][tms]
            self.states.append(self.state)
            if self.state == "TLR":
                self.ir = 0b0001
            elif self.state == "CAPDR":
                self.dr_sh = self.IDCODE if self.ir == 0b0001 else 0
            elif self.state == "CAPIR":
                self.ir_sh = 0b0001
            elif self.state == "UPIR":
                self.ir = self.ir_sh
        if not tck and self.prev_tck:
            if self.state == "SHDR":
                self.pending = (t + self.dly, self.dr_sh & 1)
            elif self.state == "SHIR":
                self.pending = (t + self.dly, self.ir_sh & 1)
        self.prev_tck = tck
        if self.pending and t >= self.pending[0]:
            self.tdo = self.pending[1]
            self.pending = None
        return self.tdo << TDO

    def info(self, t0):
        return {"final": self.state, "ir": self.ir}


def jtag_words(pairs):
    n = len(pairs)
    out = [n - 1]
    for i in range(0, n, 16):
        w = 0
        for j, (tdi, tms) in enumerate(pairs[i:i + 16]):
            w |= (tdi | tms << 1) << (2 * j)
        out.append(w)
    return out


def jtag_bits(words, n):
    v = sum(w << (32 * i) for i, w in enumerate(words))
    return [(v >> i) & 1 for i in range(n)]


def jtag_seq():
    """IDCODE (reset, Shift-DR, 32 bits out), then IR = BYPASS (1111) and 8 bits
    through the 1-bit bypass register, back to Run-Test/Idle."""
    seq = [(0, 1)] * 5 + [(0, 0), (0, 1), (0, 0), (0, 0)]
    seq += [(0, 0)] * 31 + [(0, 1)] + [(0, 1), (0, 0)]                       # 41: ends in RTI
    seq += [(0, 1), (0, 1), (0, 0), (0, 0)]                                  # to Shift-IR
    seq += [(1, 0)] * 3 + [(1, 1)] + [(0, 1), (0, 0)]                        # IR = 1111, Update-IR, RTI
    seq += [(0, 1), (0, 0), (0, 0)]                                          # to Shift-DR
    pat = [1, 0, 1, 1, 0, 0, 1, 0]
    seq += [(b, 0) for b in pat[:-1]] + [(pat[-1], 1)] + [(0, 1), (0, 0)]   # 8 bits through BYPASS
    return seq, pat


@model_clock(50e6)
def jtag(rig, store):
    """jtag engine (TDI uio4, TMS uio5, TCK uio6; TDO uio0 from a pre-timed TAP): IDCODE and BYPASS at CLKDIV 4 and 8, la_capture of all four lines."""
    prog = fw("jtag")
    seq, pat = jtag_seq()
    words = jtag_words(seq)
    check(len(words) <= 8, "the sequence fits the TX FIFO")
    n = len(seq)
    pins = dict(owners={TDI: 0, TMS: 0, TCK: 0}, poe=(1 << TDI) | (1 << TMS) | (1 << TCK))
    lines = []
    for div in (4, 8):
        cfg = dict(out_pins=(TDI, 2), side_pins=TCK, in_pins=TDO, clkdiv=div << 8)
        name = f"jtag_div{div}"
        nw = (n + 31) // 32
        plan = predict_m(name, 50e6, prog, cfg, far_factory=TapFar, far_key="tap-20ns", mask=1 << TDO,
                         pg_idle=1 << TDO, dur_s=0.2e-3 + n * 4 * div / 50e6, done=lambda w: len(w) >= nw,
                         tail_s=20e-6, prefill=words, **pins)
        tdo = jtag_bits(plan["words"], n)
        idcode = sum(b << i for i, b in enumerate(tdo[9:41]))
        check(idcode == TapFar.IDCODE, f"model IDCODE {idcode:#x}")
        sched = far_sched_m(rig, plan["far"], mask=1 << TDO, pg_idle=1 << TDO)
        c = capture_q(rig, prog, cfg, sched=sched, pg_init=1 << TDO,
                      la_mask=(1 << TDO) | (1 << TDI) | (1 << TMS) | (1 << TCK) | (1 << TRIG),
                      dur_s=plan["t_last"] / 1e9 + 0.2e-3, prefill=words, **pins)
        recs = c.recs
        e, t_end = p2d.trig_rel(c, {"tck": TCK, "tms": TMS, "tdi": TDI, "tdo": TDO})
        ds = {}
        unc = set(round(t) for t in e["uncertain"])
        for k, b in (("tck", TCK), ("tms", TMS), ("tdi", TDI)):
            ds[k] = edges_vs(e[k], bits_from(plan["pins"], b))
            check(ds[k] is not None, f"{k} edges differ from the model's")
            # la_capture stamps an edge under 7 ticks after the previous record (TCK just
            # after a TDO edge) up to 5 ticks late: those are not compared
            ds[k] = [d for d, (t, _) in zip(ds[k], [x for x in e[k] if x[0] > 0]) if round(t) not in unc]
        allv = [x for v in ds.values() for x in v]
        per = [b[0] - a[0] for a, b in zip(e["tck"][1:], e["tck"][3:]) if a[1] == 1][:200]
        d_txt = (f"TCK/TMS/TDI {len(allv)} edges board - plan {span(allv, '{:.0f}')} ns (one start offset; "
                 f"{len(unc)} records under 7 ticks after the previous not compared), "
                 f"TCK period {span(per, '{:.0f}')} ns, {len([1 for t, v in e['tck'] if v])} rising edges")
        keep(store, name, tck=e["tck"], tms=e["tms"], tdi=e["tdi"], tdo=e["tdo"], t_end=t_end)
        got = jtag_bits(recs, n)
        idc = sum(b << i for i, b in enumerate(got[9:41]))
        byp = got[-10:-2]
        check(idc == TapFar.IDCODE, f"IDCODE {idc:#x}")
        check(byp[1:] == pat[:-1], f"bypass bits {byp} for {pat}: want the pattern one TCK late")
        check(got == tdo, "TDO bits differ from the model's")
        lines.append(f"CLKDIV {div} (TCK {rig.f_clk / 4 / div / 1e6:.4f} MHz): {n} TCK cycles; IDCODE {idc:#010x}; "
                     f"BYPASS: TDO = TDI one TCK later ({''.join(map(str, byp))} for {''.join(map(str, pat))}); "
                     f"TAP (model) ends in {plan['info']['final']}; {d_txt}")
        det(store, name, tdo=got)
    return lines


# ================================================================ CAN

TPB = 48                       # ticks per bit


def crc15(bits):
    crc = 0
    for b in bits:
        nxt = b ^ ((crc >> 14) & 1)
        crc = (crc << 1) & 0x7FFF
        if nxt:
            crc ^= 0x4599
    return crc


def frame_bits(ident, data, rtr=False):
    """Destuffed SOF..CRC bits of a standard frame (test_can.frame_bits)."""
    b = [0] + [(ident >> (10 - i)) & 1 for i in range(11)] + [int(rtr), 0, 0]
    dlc = len(data)
    b += [(dlc >> (3 - i)) & 1 for i in range(4)]
    if not rtr:
        for byte in data:
            b += [(byte >> (7 - i)) & 1 for i in range(8)]
    c = crc15(b)
    return b + [(c >> (14 - i)) & 1 for i in range(15)]


def stuff(bits):
    out, run, last = [], 0, None
    for x in bits:
        out.append(x)
        run = run + 1 if x == last else 1
        last = x
        if run == 5:
            out.append(1 - x)
            last, run = 1 - x, 1
    return out


def tx_words(bits):
    words = [len(bits)]
    for i in range(0, len(bits), 32):
        chunk = bits[i:i + 32] + [0] * (32 - len(bits[i:i + 32]))
        words.append(int("".join(map(str, chunk)), 2))
    return words


def can_records(words):
    out, start = [], 0
    for i, w in enumerate(words):
        if w >> 31:
            out.append((w & 0xFFFF, words[start:i]))
            start = i + 1
    return out, words[start:]


def unpack(words, nbits):
    bits = []
    full, rem = divmod(nbits, 24)
    for w in words[:full]:
        bits += [(w >> (23 - i)) & 1 for i in range(24)]
    if rem and full < len(words):
        w = words[full]
        bits += [(w >> (rem - 1 - i)) & 1 for i in range(rem)]
    return bits


def can_decode(edges, tb, t_end):
    """Bus edges (ns) -> [(sof_ns, destuffed bits SOF..CRC, ack_slot_level)] by
    sampling at 60 % of each bit, resynchronising on recessive->dominant edges."""
    def lvl(t):
        v = edges[0][1]
        for te, x in edges:
            if te > t:
                break
            v = x
        return v
    out, i = [], 0
    while i < len(edges):
        t, v = edges[i]
        if v != 0 or (i and edges[i - 1][1] == 0):
            i += 1
            continue
        sof, bits, wire, run, last = t, [], [], 0, None
        tb0 = t
        k = 0
        while True:
            ts = tb0 + 0.6 * tb
            b = lvl(ts)
            wire.append(b)
            if run == 5:                     # a stuff bit
                run, last = 1, b
            else:
                bits.append(b)
                run = run + 1 if b == last else 1
                last = b
            nxt = [te for te, x in edges if tb0 + 0.5 * tb < te < tb0 + 1.5 * tb and x == 0]
            tb0 = nxt[0] if nxt and lvl(nxt[0] - 1) == 1 else tb0 + tb
            k += 1
            if len(bits) >= 19:
                dlc = sum(bits[15 + j] << (3 - j) for j in range(4))
                total = 19 + (0 if bits[12] else 8 * min(dlc, 8)) + 15
                if len(bits) == total:
                    break
            if k > 200 or tb0 > t_end:
                break
        if run == 5:                                # a stuff bit after the CRC
            tb0 += tb
        ack = lvl(tb0 + tb + 0.6 * tb)              # tb0: the CRC delimiter's start; then the ACK slot
        out.append((sof, bits, ack))
        i = next((j for j, (te, _) in enumerate(edges) if te > tb0 + 10 * tb), len(edges))
    return out


class CanNode(FarM):
    """A pins-only CAN node on uio0: from the first falling edge of the bus
    (SOF) it drives `wire` (stuffed bits, 1 = recessive) bit by bit, and/or
    drives the ACK slot dominant `ack_bit` bits after the SOF; DLY after the
    bit boundary it computes."""

    def __init__(self, t_tr, per, tb, wire=(), ack_bit=None, dly=200.0):
        super().__init__(1, per)
        self.tb, self.wire, self.ack_bit, self.dly = tb, list(wire), ack_bit, dly
        self.sof = None
        self.prev = 1

    def react(self, t, pads):
        line = pads & 1
        if self.sof is None:
            if self.prev == 1 and line == 0:
                self.sof = t
            self.prev = line
            if self.sof is None:
                return 1
        k = int((t - self.sof - self.dly) // self.tb)
        d = 1
        if 0 <= k < len(self.wire):
            d = self.wire[k]
        if self.ack_bit is not None and k == self.ack_bit:
            d = 0
        return d


def can_cfg(rig_f, base):
    return dict(clkdiv=clkdiv(rig_f, 500e3 * TPB), **base)


@model_clock(50e6)
def can_pair(rig, store):
    """can_tx (core 0, uio0) and can_rx (core 1, uio2), open drain on pe_lab's wired-AND bus (I2C mode), 500 kbit/s (CLKDIV 2.08): frames sent, ACKed, received with CRC ok."""
    tx = fw("can_tx", RX=0, TX=0)
    rx = fw("can_rx", RX=2, TX=2)
    cd = clkdiv(rig.f_clk, 500e3 * TPB)
    frames = [(0x123, bytes([1, 2, 3, 4])), (0x7F0, bytes([0xFF] * 8)), (0x001, b""), (0x555, bytes([0x00] * 8)),
              (0x2AA, bytes([0xDE, 0xAD, 0xBE, 0xEF, 0x55, 0xAA]))]
    lines, got_all = [], []
    setup(rig, mode=I2C, owners={0: 0, 2: 1}, od=0b101, pout=0b101)
    load(rig, 0, tx, out_pins=(0, 1), in_pins=0, clkdiv=cd)
    load(rig, 1, rx, in_pins=2, clkdiv=cd)
    rig.write(sim.core_reg(0, sim.C_CRCCTRL), 0x4599 | (14 << 16) | (1 << 21))
    rig.write(sim.core_reg(1, sim.C_CRCCTRL), 0x4599 | (14 << 16) | (1 << 20))
    ctrl(rig, 0b11)
    for ident, data in frames:
        bits = frame_bits(ident, data)
        rig.write(FIFO(0), tx_words(bits[:-15]), fixed=True)
        st = pfd.poll_records(rig, [0], lambda r: len(r[0]) >= 1, step_s=2e-4, timeout_s=1.0)[0]
        recs = []
        for _ in range(20):
            recs += rd(rig, FIFO(1), rxn(rig.read(STATUS(1))))
            fr, left = can_records(recs)
            if fr:
                break
            rig.wait(2e-4)
        check(st == [0], f"{ident:#x}: TX status {st} (0 = sent and ACKed)")
        check(len(fr) == 1 and fr[0][0] == 1 and unpack(fr[0][1], len(bits)) == bits,
              f"{ident:#x}: RX {fr}")
        got_all.append((ident, data.hex(), st[0], fr[0][0]))
    finish(rig)
    lines.append(f"{len(frames)} frames (IDs {', '.join(hex(i) for i, _ in frames)}, DLC 0-8): can_tx status 0 "
                 f"(ACKed by can_rx) and can_rx CRC ok with every destuffed bit SOF..CRC equal to the frame's")
    det(store, "can_pair", frames=got_all)
    return lines


@model_clock(50e6)
def can_rx_gen(rig, store):
    """can_rx (uio0, open drain) receiving generator frames at 0, +-0.5 % and +-1 %; a corrupted CRC and a stuff error; la_capture of the bus (its ACK and error flag)."""
    prog = fw("can_rx", RX=0, TX=0)
    cd = clkdiv(rig.f_clk, 500e3 * TPB)
    tbit = 1e9 / 500e3
    pins = dict(owners={0: 0}, od=1, pout=1)
    cfg = dict(in_pins=0, clkdiv=cd)
    extra = [(sim.core_reg(0, sim.C_CRCCTRL), 0x4599 | (14 << 16) | (1 << 20))]
    lines = []
    cases = []
    rng = random.Random(5)
    for ppm in (0, 5000, -5000, 10000, -10000):
        cases.append((f"ppm {ppm:+d}", ppm, frame_bits(rng.choice([0x123, 0x7FF, 0x0, 0x2AA]),
                                                       bytes(rng.getrandbits(8) for _ in range(rng.randrange(9)))),
                      None, 1))
    b = frame_bits(0x321, bytes([1, 2, 3]))
    cases.append(("CRC corrupted", 0, b, ("flip", 30), 0))
    b2 = frame_bits(0x0F0, bytes([0x00, 0xFF, 0x0F]))
    w2 = stuff(b2)
    k = next(i for i in range(5, len(w2)) if len(set(w2[i - 5:i])) == 1 and w2[i] != w2[i - 1])
    cases.append(("stuff error", 0, b2, ("flip", k), 2))
    for name, ppm, bits, corrupt, want in cases:
        wire = stuff(bits)
        if corrupt:
            wire[corrupt[1]] ^= 1
        wire += [1] * 13
        f = rig.f_clk
        t_trig = round(TRS * f)
        sched = [(t_trig, 1 | (1 << TRIG))]
        tb = tbit * (1 + ppm / 1e6)
        t = 50e3
        for x in wire:
            c = t_trig + round(t * f / 1e9)
            v = x | (1 << TRIG)
            if sched[-1][1] != v:
                sched.append((c, v))
            t += tb
        sched.append((t_trig + round((t + 100e3) * f / 1e9), 1 | (1 << TRIG)))
        c = capture_q(rig, prog, cfg, sched=sched, pg_init=1, la_mask=1 | (1 << TRIG),
                      dur_s=t / 1e9 + 0.2e-3, extra=extra, **pins)
        recs = c.recs
        e, t_end = p2d.trig_rel(c, {"bus": 0})
        fr, left = can_records(recs)
        check(len(fr) == 1 and fr[0][0] == want and not left, f"{name}: records {fr} {left}")
        if want == 1:
            check(unpack(fr[0][1], len(bits)) == bits, f"{name}: bits differ")
        # the bus: the generator's line ANDed with can_rx's drive. ACK slot / error flag from can_rx:
        ack_t = 50e3 + (len(stuff(bits)) + 1) * tb
        lows = [(a[0], b_[0] - a[0]) for a, b_ in zip(e["bus"], e["bus"][1:]) if a[1] == 0]
        ack = [d for t0, d in lows if abs(t0 - ack_t) < tb / 2]
        flag = max(d for _, d in lows) if lows else 0
        msg = f"{name}: status {fr[0][0]}"
        if want == 1:
            check(ack and 0.8 * tb < ack[0] < 1.2 * tb, f"{name}: no ACK slot drive ({ack})")
            msg += f", {len(bits)} bits right, ACK slot driven {ack[0]:.0f} ns"
        elif want == 0:
            check(not ack, f"{name}: ACKed a bad CRC")
            msg += ", CRC error, not ACKed"
        else:
            check(flag >= 6 * tb - 100, f"{name}: error flag {flag:.0f} ns")
            msg += f", stuff error, error flag {flag / tb:.2f} bits dominant"
        lines.append(msg)
        det(store, f"can_rx_{name}", status=fr[0][0], bits=unpack(fr[0][1], len(bits)) if want == 1 else None)
        keep(store, f"can_rx_{name}", bus=e["bus"], t_end=t_end)
    return lines


@model_clock(50e6)
def can_tx_gen(rig, store):
    """can_tx (uio0, open drain) against pre-timed nodes: an ACKing receiver, nobody (no ACK), and a node with a lower ID (arbitration lost); la_capture of the bus."""
    prog = fw("can_tx", RX=0, TX=0)
    cd = clkdiv(50e6, 500e3 * TPB)
    tb = TPB * (cd / 256) * 1e9 / 50e6
    pins = dict(owners={0: 0}, od=1, pout=1)
    cfg = dict(out_pins=(0, 1), in_pins=0, clkdiv=cd)
    extra = [(sim.core_reg(0, sim.C_CRCCTRL), 0x4599 | (14 << 16) | (1 << 21))]
    lines = []
    ours = frame_bits(0x123, bytes([0xAA, 0x55, 0x01]))
    theirs = frame_bits(0x100, bytes([0x55]))
    for name, factory, want in (
            ("can_tx_ack", lambda t, per: CanNode(t, per, tb, ack_bit=len(stuff(ours)) + 1), 0),
            ("can_tx_noack", lambda t, per: CanNode(t, per, tb), 1),
            ("can_tx_lost", lambda t, per: CanNode(t, per, tb, wire=stuff(theirs) + [1] * 20), 2)):
        words = tx_words(ours[:-15])
        plan = predict_m(name, 50e6, prog, cfg, far_factory=factory, far_key=(name, "200ns"), mask=1, pg_idle=1,
                         dur_s=1e-3, done=lambda w: len(w) >= 1, tail_s=0.2e-3, prefill=words, extra=extra, **pins)
        check(plan["words"] == [want], f"model: status {plan['words']}")
        sched = far_sched_m(rig, plan["far"], mask=1, pg_idle=1)
        c = capture_q(rig, prog, cfg, sched=sched, pg_init=1, la_mask=1 | (1 << TRIG),
                      dur_s=plan["t_last"] / 1e9 + 0.2e-3, prefill=words, extra=extra, **pins)
        recs = c.recs
        check(recs == [want], f"{name}: status {recs}, want {want}")
        e, t_end = p2d.trig_rel(c, {"bus": 0})
        dec = can_decode(e["bus"], tb, t_end)
        check(dec, f"{name}: no frame on the bus")
        sof, bits, ack = dec[0]
        if want == 2:
            check(bits[:len(theirs)] == theirs, f"{name}: the bus does not carry the winner's frame")
            txt = f"lost arbitration (status 2); the bus carries the other node's 0x100 frame bit for bit"
        else:
            check(bits == ours, f"{name}: bus bits differ from the frame")
            check(ack == (0 if want == 0 else 1), f"{name}: ACK slot level {ack}")
            txt = (f"status {want} ({'ACKed' if want == 0 else 'no ACK'}); the bus carries 0x123's {len(ours)} "
                   f"destuffed bits with the right CRC15, ACK slot {'dominant' if ack == 0 else 'recessive'}")
        d = edges_vs(e["bus"], bits_from(plan["pins"], 0))
        check(d is not None, f"{name}: bus edges differ from the model's")
        lines.append(f"{name.replace('can_tx_', '')}: {txt}; {len(d)} bus edges board - model {span(d, '{:.0f}')} ns")
        det(store, name, status=recs, bits=bits)
        keep(store, name, bus=e["bus"], t_end=t_end)
    return lines


# ================================================================ 10BASE-T

F_GO = 0
SFD = [1, 0, 1, 0, 1, 0, 1, 1]
ETH_LEAD = 0.1e-3              # generator start -> the frame's trigger (the burst lasts 0.65 ms)


def eth_frame(payload_len, seed=1):
    """A frame with no 24-bit run of zeros in its bits, so no eth10_rx record word
    is 0, which a blind READ_FIXED burst can't tell from an empty read (the
    test frames' 02:00:00 source address can make one)."""
    dst, src = bytes.fromhex("ffffffffffff"), bytes.fromhex("025aa5c33c7e")
    body = dst + src + struct.pack(">H", 0x88B5) + bytes((seed * 37 + i * 11) % 255 + 1 for i in range(payload_len))
    return body + struct.pack("<I", binascii.crc32(body))


def eth_words(b):
    b = b + bytes(-len(b) % 4)
    return [int.from_bytes(b[i:i + 4], "little") for i in range(0, len(b), 4)]


def wire_bits(fr):
    return [1, 0] * 31 + [1, 1] + [(byte >> i) & 1 for byte in fr for i in range(8)]


def rx_bits(words):
    """eth10_rx records (zero words dropped: a blind burst reads 0 from an empty
    RXF) -> frames' bits (test_eth10.rx_bits). The empty last word of a frame
    whose bit count is a multiple of 24 is 0, so it is missing here; a 1-2 bit
    record (a link pulse or the end of TP_IDL) may miss its only word too."""
    frames, data = [], []
    for w in words:
        if w & 0xFF:
            n = w >> 16
            full, rem = divmod(n, 24)
            need = full + (1 if rem else 0)
            if len(data) < need:
                check(n <= 2, f"a {n}-bit record with {len(data)} of its {need} words: a zero data word?")
                data = []
                continue
            bits = []
            for d in data[:full]:
                bits += [(d >> (8 + i)) & 1 for i in range(24)]
            if rem:
                bits += [(data[full] >> (32 - rem + i)) & 1 for i in range(rem)]
            frames.append(bits)
            data = []
        else:
            data.append(w)
    return frames


def frame_from_bits(bits):
    for i in range(len(bits) - 8):
        if bits[i:i + 8] == SFD:
            body = bits[i + 8:]
            return bytes(sum(b << j for j, b in enumerate(body[8 * k:8 * k + 8])) for k in range(len(body) // 8))
    return None


def fcs_ok(fr):
    return fr is not None and len(fr) >= 64 and struct.pack("<I", binascii.crc32(fr[:-4])) == fr[-4:]


def eth_rx_setup(rig, rxp):
    prog = fw("eth10_rx")
    load(rig, 0, prog, clkdiv=1 << 8)
    rig.write(sim.A_LINEDIV, 5 << 16)
    rig.write(sim.A_LINEPINS, rxp << 8)
    rig.write(sim.A_LINECFG, 1 | (2 << 1) | (1 << 14))


def manchester_sched(rig, fr, *, t0_ns, alt=False, bit=0, gap_ns=2e3):
    """Generator entries for one frame (802.3 polarity, TP_IDL then silence)
    on uio[bit]; times rounded to the generator clock (protoemu's or the
    alternate 45.93 MHz one)."""
    f = 1200e6 / 26.125 if alt else rig.f_clk
    line = [b for x in wire_bits(fr) for b in (1 - x, x)]
    t = t0_ns
    t_trig = round(ETH_LEAD * f)
    out, prev = [(t_trig, 1 << TRIG)], 0
    for lv in line:
        if lv != prev:
            out.append((t_trig + round(t * f / 1e9), (lv << bit) | (1 << TRIG)))
        prev = lv
        t += 50.0
    if prev:
        t += 200.0
        out.append((t_trig + round(t * f / 1e9), 1 << TRIG))
    out.append((t_trig + round((t + gap_ns) * f / 1e9), 1 << TRIG))
    return out, t


@model_clock(50e6)
def eth10_rx_gen(rig, store):
    """eth10_rx (core 0, line unit, 5 clocks per bit) receiving generator frames on uio0: on protoemu's clock (half bits 2/3 clocks) and on the 45.93 MHz alternate clock; FCS checked."""
    lines = []
    for alt in (False, True):
        for size, seed in ((60, 1), (60, 7)):
            fr = eth_frame(46, seed)                     # 64-byte frames
            sched, t_end = manchester_sched(rig, fr, t0_ns=20e3, alt=alt)
            check(len(sched) <= 1024, f"{len(sched)} generator entries")
            setup(rig, mode=PG, pg_init=0)
            eth_rx_setup(rig, 0)
            if alt and not rig.model:
                rig.lab.pg_load(sched, init=0, alt=True)
            elif alt:
                # the model has no alternate clock: alternate cycles as rounded-up protoemu clocks
                r = rig.f_clk / (1200e6 / 26.125)
                rig.pg_load([(math.ceil(t * r), v) for t, v in sched], init=0)
            else:
                rig.pg_load(sched, init=0)
            ctrl(rig, 0b01)
            words = [w for w in go_burst(rig, 0, 4096, 1, True)]
            words += rd(rig, FIFO(0), rxn(rig.read(STATUS(0))))
            if alt and not rig.model:
                rig.lab.pg_wait()
                rig.lab.pg_set(0, alt=False)
            finish(rig)
            rig.write(sim.A_LINECFG, 0)
            got = [frame_from_bits(b) for b in rx_bits([w for w in words if w]) if len(b) > 64]
            check(len(got) == 1 and got[0] == fr and fcs_ok(got[0]), f"received {[g.hex() if g else g for g in got]}")
            name = f"eth10_rx_{'alt' if alt else 'sync'}_{seed}"
            lines.append(f"{'alternate clock' if alt else 'protoemu clock'}, {len(fr)}-byte frame (seed {seed}): "
                         f"received, FCS ok, {len(sched)} generator entries")
            det(store, name, frame=got[0].hex())
    return lines


@model_clock(50e6)
def eth10_loop(rig, store):
    """eth10_tx (core 1, CLKDIV 1.25: 40/60 ns half cells) on V0/V1 -> eth10_rx (core 0, RXP = V0): two 64-byte frames, preloaded, drained by a burst."""
    tx = fw("eth10_tx", F_GO=F_GO)
    lines = []
    for seed in (1, 5):
        fr = eth_frame(46, seed)
        setup(rig, mode=PG, owners={V0: 1, V0 + 1: 1}, pg_init=0)
        load(rig, 1, tx, side_pins=V0, clkdiv=320)
        base = 256
        rig.write(base + PRE, trigger_preamble(base))
        rig.write(sim.core_reg(1, sim.C_ENTRY), base + PRE)
        eth_rx_setup(rig, V0)
        rig.write(sim.core_reg(1, sim.C_R0), 8 * len(fr) - 1)
        ctrl(rig, 0b11)
        w = eth_words(fr)
        rig.write(FIFO(1), w[:8], fixed=True)
        rig.wait(1e-5)
        rig.write(FIFO(1), w[8:16], fixed=True)
        rig.write(sim.A_FSET, 1 << F_GO)
        rig.pg_load([(round(0.2e-3 * rig.f_clk), 1 << TRIG)], init=0)
        words = go_burst(rig, 0, 4096, 1, True)
        words += rd(rig, FIFO(0), rxn(rig.read(STATUS(0))))
        fl = rig.read(sim.A_FSET)
        finish(rig)
        rig.write(sim.A_LINECFG, 0)
        got = [frame_from_bits(b) for b in rx_bits([x for x in words if x]) if len(b) > 64]
        check(not fl & (1 << F_GO), "eth10_tx did not finish (F_GO still set)")
        check(len(got) == 1 and got[0] == fr and fcs_ok(got[0]), f"received {[g.hex() if g else g for g in got]}")
        lines.append(f"seed {seed}: the {len(fr)}-byte frame sent by eth10_tx came out of eth10_rx, FCS ok")
        det(store, f"eth10_loop_{seed}", frame=got[0].hex())
    return lines


def match_frame(field):
    """A 64-byte frame with the 16-bit field eth10_match compares."""
    body = bytes(6 * [0xFF]) + bytes.fromhex("020000c0ffee") + struct.pack(">H", 0x88B5)
    body += struct.pack("<H", field) + bytes((i * 11) & 255 for i in range(44))
    return body + struct.pack("<I", binascii.crc32(body))


@model_clock(50e6)
def eth10_cut(rig, store):
    """eth10_match (core 0, line unit) raising a pin on a field while the frame arrives: uio0 (generator) and uio1 (HIT) sampled every clock by usb_sampler on core 1."""
    text = (HERE.parents[2] / "firmware" / "eth10_match.pasm").read_text().replace(".define HIT 8", ".define HIT 1")
    prog = asm.assemble(text)
    samp = asm.assemble((HERE / "usb_sampler.pasm").read_text(), {"TRIG": 2})
    last_bit = 64 + 8 * 16 - 1
    lines = []
    for field, threshold, alt in ((2000, 1000, False), (1000, 1000, False), (999, 1000, False),
                                  (2000, 1000, True), (2100, 1000, True), (2200, 1000, True)):
        fr = match_frame(field)
        sched, _ = manchester_sched(rig, fr, t0_ns=5e3, alt=alt)
        sched = [(t, v | 1 << 2) for t, v in sched]
        setup(rig, mode=PG, owners={1: 0}, poe=0b10, pg_init=0)
        load(rig, 0, prog, clkdiv=1 << 8)
        load(rig, 1, samp, in_pins=0, clkdiv=1 << 8)
        rig.write(sim.A_LINEDIV, 5 << 16)
        rig.write(sim.A_LINEPINS, 0)
        rig.write(sim.A_LINECFG, 1 | (2 << 1) | (1 << 14))
        rig.write(FIFO(0), threshold, fixed=True)
        if alt and not rig.model:
            rig.lab.pg_load(sched, init=0, alt=True)
        elif alt:
            r = rig.f_clk / (1200e6 / 26.125)
            rig.pg_load([(math.ceil(t * r), v) for t, v in sched], init=0)
        else:
            rig.pg_load(sched, init=0)
        ctrl(rig, 0b11)
        words = go_burst(rig, 1, 4096, 1, True)
        if alt and not rig.model:
            rig.lab.pg_wait()
            rig.lab.pg_set(0, alt=False)
        rec = rd(rig, FIFO(0), rxn(rig.read(STATUS(0))))
        finish(rig)
        rig.write(sim.A_LINECFG, 0)
        samples = [(x >> (3 * (9 - i))) & 7 for x in words if x for i in range(10)]
        rxp = [v & 1 for v in samples]
        hit = [v >> 1 & 1 for v in samples]
        edges = [i for i in range(1, len(rxp)) if rxp[i] != rxp[i - 1]]
        check(edges, "no edges on uio0")
        rise = next((i for i in range(1, len(hit)) if hit[i] and not hit[i - 1]), None)
        check((rise is not None) == (field >= threshold), f"field {field}, threshold {threshold}: HIT rise {rise}")
        name = f"field {field}, threshold {threshold}, {'alternate' if alt else 'protoemu'} clock"
        if rise is None:
            check(rec == [], f"records {rec}")
            lines.append(f"{name}: no HIT")
            continue
        check(rec == [field], f"records {rec}")
        want = edges[0] + 5 * last_bit
        mid = min(edges, key=lambda e: abs(e - want))
        check(abs(mid - want) <= 3, f"no edge near the field's last bit ({mid} against {want})")
        check(mid < rise < edges[-1], f"HIT at {rise}, last bit {mid}, frame end {edges[-1]}")
        lines.append(f"{name}: HIT {rise - mid} clocks ({(rise - mid) * 20} ns) after the field's last mid-bit edge, "
                     f"{(edges[-1] - rise) * 20 / 1000:.1f} us before the frame's last edge")
        det(store, f"eth10_cut_{field}_{int(alt)}", reaction=rise - mid)
    return lines


@model_clock(50e6)
def eth10_tx_wave(rig, store):
    """eth10_tx (core 0) on uio3/uio4 (TD+/TD-), sampled every clock by usb_sampler on core 1: Manchester cells, preamble/SFD, frame and FCS, TP_IDL."""
    tx = fw("eth10_tx", F_GO=F_GO)
    samp = asm.assemble((HERE / "usb_sampler.pasm").read_text(), {"TRIG": 2})
    lines = []
    fr = eth_frame(46, 3)
    setup(rig, mode=PG, owners={3: 0, 4: 0}, poe=0b11000, pg_init=0)
    load(rig, 0, tx, side_pins=3, clkdiv=320)
    rig.write(PRE, trigger_preamble(0))
    rig.write(sim.core_reg(0, sim.C_ENTRY), PRE)
    load(rig, 1, samp, in_pins=2, clkdiv=1 << 8)
    rig.write(sim.core_reg(0, sim.C_R0), 8 * len(fr) - 1)
    ctrl(rig, 0b01)
    w = eth_words(fr)
    rig.write(FIFO(0), w[:8], fixed=True)
    rig.wait(1e-5)
    rig.write(FIFO(0), w[8:16], fixed=True)
    rig.write(sim.A_FSET, 1 << F_GO)
    ctrl_raw(rig, 0b11 | (0b10 << 8))              # the sampler started; core 0 waits at its preamble
    trig = (1 << 2) | (1 << TRIG)
    rig.pg_load([(round(0.1e-3 * rig.f_clk), trig)], init=0)
    words = go_burst(rig, 1, 4096, 1, True)
    finish(rig)
    lead = next((i for i, x in enumerate(words) if x), None)
    check(lead is not None and lead > 0, f"the burst started after the sampling (first word {lead})")
    samples = []
    for x in words:
        if x:
            for i in range(10):
                samples.append((x >> (3 * (9 - i) + 1)) & 3)       # TD+ (uio3), TD- (uio4)
    check(3 not in samples, "TD+ and TD- both high")
    # Manchester cells: H = TD+ (1), L = TD- (2), idle 0; cells start at the first non-idle sample
    runs, cur, n, t0 = [], samples[0], 0, 0
    for i, x in enumerate(samples):
        if x == cur:
            n += 1
        else:
            runs.append((cur, t0, n))
            cur, n, t0 = x, 1, i
    runs.append((cur, t0, n))
    # the link pulse (100 ns H) first, then the frame: the first stretch of > 20 driven samples
    act, start = [], None
    for lvl, t, n in runs:
        if lvl:
            start = t if start is None else start
        else:
            if start is not None and t - start > 20:
                break
            start = None
    check(start is not None, "no frame in the samples")
    pulses = [(t, n) for lvl, t, n in runs if lvl == 1 and t < start]
    # 4 ticks of 1.25 clocks per bit: exactly 5 clocks; the half cells are 2 or 3 clocks
    want = wire_bits(fr)
    bits, bad = [], 0
    for k in range(len(want)):
        a, b = samples[start + 5 * k + 1], samples[start + 5 * k + 4]
        bad += a == b or 0 in (a, b)
        bits.append(1 if b == 1 else 0)
    check(not bad, f"{bad} bit cells without a mid-bit transition")
    check(bits == want, f"{sum(x != y for x, y in zip(bits, want))} of {len(want)} bits differ")
    dec = frame_from_bits(bits)
    check(dec == fr and fcs_ok(dec), "frame or FCS")
    end = start + 5 * len(want)
    last_h = [(t, n) for lvl, t, n in runs if lvl == 1 and t < end + 40][-1]     # TP_IDL's high
    halves = sorted({n for lvl, t, n in runs if lvl and start <= t < end - 3})
    lines.append(f"{len(fr)}-byte frame: {len(want)} wire bits decoded from TD+/TD- sampled every clock "
                 f"(preamble, SFD, frame, FCS ok), every bit 5 clocks with its mid-bit transition; driven runs "
                 f"{halves} clocks (half cells 40/60 ns); link pulse before it {[n * 20 for _, n in pulses]} ns H; "
                 f"TP_IDL: the last H lasts {last_h[1] * 20} ns, then the line is idle")
    det(store, "eth10_tx_wave", bits=bits)
    keep(store, "eth10_tx_wave", samples=samples[:20000])
    return lines


# ================================================================ main

GROUPS = {
    "ws2812": [ws2812],
    "spi": [spi_master],
    "sniff": [i2c_sniff_gen, i2c_sniff_live],
    "ps2": [ps2_device],
    "onewire": [onewire],
    "jtag": [jtag],
    "can": [can_pair, can_rx_gen, can_tx_gen],
    "eth10": [eth10_rx_gen, eth10_loop, eth10_tx_wave, eth10_cut],
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
                    rig.write(sim.A_LINECFG, 0)
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
            if name.startswith("can"):
                pfd.pf.to_trace(tr, [(t + 50e3, v) for t, v in r["bus"]], "can", r["t_end"] + 100e3)
                got = sigrok.can(tr, "can", 500000, res_ps=10_000)
                print(f"[{name}] sigrok can: {got}")
            elif name.startswith("ps2"):
                pfd.pf.to_trace(tr, [(t + 50e3, v) for t, v in r["clk"]], "clk")
                pfd.pf.to_trace(tr, [(t + 50e3, v) for t, v in r["dat"]], "data", r["t_end"] + 100e3)
                anns = sigrok.decode(tr, "ps2:clk=clk:data=data", "ps2=word:parity-ok:parity-err", res_ps=100_000)
                print(f"[{name}] sigrok ps2: {[a.text for a in anns][:24]}")
            elif name.startswith("onewire"):
                pfd.pf.to_trace(tr, [(t + 50e3, v) for t, v in r["dq"]], "owr", r["t_end"] + 100e3)
                anns = sigrok.decode(tr, "onewire_link:owr=owr,onewire_network", "onewire_network=text",
                                     res_ps=100_000)
                print(f"[{name}] sigrok onewire_network: {[a.text for a in anns][:12]}")
            elif name.startswith("jtag"):
                for k in ("tck", "tms", "tdi", "tdo"):
                    pfd.pf.to_trace(tr, [(t + 1e3, v) for t, v in r[k]], k)
                tr.end(round((r["t_end"] + 2e3) * 1000))
                anns = sigrok.decode(tr, "jtag:tck=tck:tms=tms:tdi=tdi:tdo=tdo", "jtag=bitstring-tdo:bitstring-tdi",
                                     res_ps=10_000)
                print(f"[{name}] sigrok jtag bit strings (TDI/TDO): {[a.text for a in anns][:12]}")
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
        return p2d.compare_main(*a.groups[1:3])
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
        print("cycle model (pe.sim) with pe_lab's modes modelled, 50 MHz", flush=True)
    else:
        rig = HwRig()
        i = rig.ident
        print(f"bitstream RTL revision {i['rtl_rev'][:12]}{' (dirty)' if i['dirty'] else ''}, built {i['build_utc']} "
              f"UTC, digest {i['digest']}, clk_div {i['clk_div']}", flush=True)
        print(f"protoemu clk measured {rig.f_meas / 1e6:.5f} MHz over 2 s; CLKDIVs derived for "
              f"{rig.f_clk / 1e6:.5f} MHz", flush=True)
        if i["clk_div"] != 24:
            rig.close()
            sys.exit("FAIL: not the 50 MHz build (protoemu)")
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
