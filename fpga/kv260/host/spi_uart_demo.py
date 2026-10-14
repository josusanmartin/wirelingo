#!/usr/bin/env python3
"""SPI and UART firmwares that had only run in simulation
(spi_master_modes, spi_master_packed, spi_master_preloaded_once,
uart_rx_capture, uart_rx_packed, uart_rx_capture_peer, uart_rx_mailbox) on
the KV260 build of the chip RTL. Run as root on the Kria with the protoemu app (pe_lab) loaded.
Nothing leaves the FPGA: every line is a uio pad that a core or pe_lab's
fabric drives; no PMOD pin is enabled.

    sudo python3 -u fpga/kv260/host/spi_uart_demo.py [groups] [--save su.json]
    python3 fpga/kv260/host/spi_uart_demo.py --model [groups] [--save m.json]   # cycle model
    python3 fpga/kv260/host/spi_uart_demo.py compare su.json m.json             # off the board

Pins (uio): SCK 2, MOSI 3, MISO 4 (pe_lab's SPI device's), CS 5, trigger 7;
UART RX on uio0 (core 0) and uio1 (core 1).

SPI against pe_lab's SPI device (MODE 2): a reactive peer in the fabric. It
samples MOSI on each SCK rise and puts the next MISO bit out (DELAY + 2) x
2.5 ns after the clock edge that dropped SCK; it counts SCK rises and logs
the last 16 MOSI bytes. That is a mode-0 peer, and also a mode-3 one (mode 3
samples on the rise and launches on the fall too) once its 128-bit response
register is rotated by one bit, because mode 3's first fall must present bit
7. Modes 1 and 2 have no peer in the fabric.

SPI with la_capture (all four modes): core 0 runs the master behind the
2-word trigger preamble of postfreeze_demo (its side-set holding SCK at
CPOL), core 1 runs la_capture on uio0-7
(SCK, MOSI, CS and the trigger; 1 tick = 1 clock, master CLKDIV 8 so every
edge is >= 7 clocks from the previous one and timestamped exactly), and the
pattern generator plays MISO: each bit D_LAUNCH clocks after the peer's
launch edge (CPHA0: CS fall, then each trailing edge; CPHA1: each leading
edge) as the cycle model predicts it for the same set-up. On the board the
master's tick phase against the trigger differs from the model's by delta
(|delta| < 8 clocks, measured from the capture), so the bits land D_LAUNCH -
delta clocks after the edge (it is pre-timed, not a reactive peer); the
master reads MISO as it was 14 or more clocks after the edge.
The captured wire is checked with test_spi_packed.FramedPeer's rules, and
compared edge for edge with the model's.

UART: pe_lab's pattern generator plays precomputed 8N1 lines (true baud
offsets, start phase, alternating jitter, bad STOP bits, breaks) at exact
clocks, built like test_uart._PinTimeline. The captures go through the host
helpers pe.uartcapture / pe.uartcapture_peer with their documented checks.
uart_rx_mailbox runs with the host standing in for usb_uart_byte's core 0
(it clears F7, as core 0 does after a valid DATA ACK); no USB host.

--model runs the same code on the cycle model (postfreeze_demo.ModelRig,
which models pe_lab's pattern generator and SPI device), some runs at a
slower clock (the firmware counts ticks of its divider). --save keeps a
summary of every run; `compare` prints which runs' deterministic results
(DELAY tables, waveforms in clocks, data) are equal between two files.
"""
import argparse
import json
import math
import pathlib
import random
import sys
import time
from fractions import Fraction

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import postfreeze_demo as pfd  # noqa: E402  (sets up sys.path for tools/)
from postfreeze_demo import (FIFO, STATUS, PG, SPI, TRIG, PRE, HALTED, FAULT, HwRig, ModelRig,  # noqa: E402
                             check, fw, load, setup, finish, model_clock, poll_records)
from pe import asm, host, la, sim, spipacked, uartcapture, uartcapture_peer  # noqa: E402

SCK, MOSI, MISO, CS = 2, 3, 4, 5
LA_MASK = (1 << SCK) | (1 << MOSI) | (1 << CS) | (1 << TRIG)
LA_DIV = 8                     # master CLKDIV for the la_capture runs
D_LAUNCH = 4                   # generator MISO: clocks after the predicted launch edge
TRIG_CLK = 7500                # generator: trigger this many clocks after its start (150 us at 50 MHz)
BURST = bytes.fromhex("00ff55aa5ac319e701020408102040807e813cc19669123456789abcdef027d8")


def rd(rig, addr, n):
    """n words from a FIFO address, always a list."""
    if n == 0:
        return []
    w = rig.read(addr, n, fixed=True)
    return [w] if n == 1 else list(w)


def ctrl(rig, v):
    """CTRL write. It acts a clock later (docs/isa.md §5.3); the model's host
    can issue the next bus cycle in that clock, a serial host can't, so the
    model waits a microsecond (the board's next transaction is later anyway)."""
    rig.write(sim.A_CTRL, v)
    if rig.model:
        rig.wait(1e-6)


def apply(rig, writes):
    for a, v in writes:
        if a == sim.A_CTRL:
            ctrl(rig, v)
        else:
            rig.write(a, v)


def rxn(st):
    return (st >> 4) & 15


def wait_until(rig, pred, timeout_s, step_s=1e-4):
    """Poll pred() (host-port reads) until it is true; model time advances by step_s per poll."""
    t, t0 = 0.0, time.monotonic()
    while True:
        v = pred()
        if v:
            return v
        check((t if rig.model else time.monotonic() - t0) < timeout_s, f"no completion within {timeout_s} s")
        rig.wait(step_s)
        t += step_s


def rng_bytes(rng, n):
    return bytes(rng.getrandbits(8) for _ in range(n))


def det(store, name, **kw):
    """The deterministic part of a run, for `compare`."""
    if store is not None:
        store.setdefault(name, {}).update(kw)


# ================================================================ SPI: helpers

def modes_prog(mode, unrolled):
    progs = {p.name: p for p in asm.assemble_all((pfd.FW / "spi_master_modes.pasm").read_text(),
                                                 {"CPOL": mode >> 1})}
    return progs[f"spi_master_{'unrolled_' if unrolled else ''}cpha{mode & 1}"]


def packed_prog(mode):
    return asm.assemble_all((pfd.FW / "spi_master_packed.pasm").read_text(),
                            {"CPOL": mode >> 1, "WORD_BITS": 32, "CS": CS})[mode & 1]


def preloaded_prog():
    return asm.assemble((pfd.FW / "spi_master_preloaded_once.pasm").read_text(),
                        {"CPOL": 0, "WORD_BITS": 32, "CS": CS})


def dev_resp(resp16, mode):
    """SPID_RESP for a master in `mode` to read resp16 (repeating every 16
    bytes). Mode 0: as is (bit 7 of byte 0 is on MISO from the arm). Mode 3:
    rotated right by one bit, since the device shifts at every fall and mode
    3's first fall is the leading edge that must present bit 7."""
    r = int.from_bytes(bytes(resp16), "big")
    if mode == 3:
        r = (r >> 1) | ((r & 1) << 127)
    return r.to_bytes(16, "big")


def bits_of(data):
    return [(b >> i) & 1 for b in data for i in range(7, -1, -1)]


def lateness(resp16, mode, got):
    """k if the master read the device's stream k bits late (0 = right), else None."""
    stream = bits_of(resp16)
    g = bits_of(got)
    lo = -1 if mode == 3 else 0          # mode 3: the arm's bit (the stream's last) is on MISO first
    for k in range(4):
        if g == [stream[max(lo, j - k) % 128] for j in range(len(g))]:
            return k
    return None


def expected_rx(resp16, n):
    return bytes(resp16[i % 16] for i in range(n))


def device_session(rig, mode, prog, div, cs=False):
    pins = {SCK: 0, MOSI: 0, MISO: 0}
    poe = (1 << SCK) | (1 << MOSI)
    pout = (mode >> 1) << SCK
    if cs:
        pins[CS] = 0
        poe |= 1 << CS
        pout |= 1 << CS
    setup(rig, mode=SPI, owners=pins, pout=pout, poe=poe)
    load(rig, 0, prog, out_pins=(MOSI, 1), side_pins=SCK, in_pins=MISO, clkdiv=div << 8)


def rx_ready(rig, n):
    def f():
        st = rig.read(STATUS(0))
        return st if rxn(st) >= n else 0
    return f


# ================================================================ SPI: pe_lab's SPI device

@model_clock(50e6)
def spi_modes_device(rig, store):
    """spi_master_modes, modes 0 and 3, compact and unrolled, CLKDIV 3 and 4, against pe_lab's SPI device, DELAY 0..31."""
    rng = random.Random(21)
    lines, table = [], {}
    rounds = 2
    for mode in (0, 3):
        for unrolled in (False, True):
            prog = modes_prog(mode, unrolled)
            for div in (3, 4):
                device_session(rig, mode, prog, div)
                row, pers = [], set()
                for delay in range(32):
                    ks = set()
                    for _ in range(rounds):
                        tx, resp = rng_bytes(rng, 8), rng_bytes(rng, 16)
                        ctrl(rig, 1 << 16)                  # core 0 stopped, FIFOs cleared
                        rig.spid_arm(dev_resp(resp, mode), delay)
                        rig.write(FIFO(0), [b << 24 for b in tx], fixed=True)
                        ctrl(rig, 1 | (1 << 8))
                        wait_until(rig, rx_ready(rig, 8), 0.05, 2e-5)
                        got = bytes(w & 0xFF for w in rd(rig, FIFO(0), 8))
                        rises, pmin, pmax = rig.spid_status()
                        check(rises == 64, f"{rises} SCK rises for 8 bytes")
                        check(rig.spid_rx()[8:] == tx, f"device got {rig.spid_rx()[8:].hex()} for {tx.hex()}")
                        check((rig.read(sim.A_POUT) >> SCK) & 1 == mode >> 1, "SCK not idle at the end")
                        ks.add(lateness(resp, mode, got))
                        pers.add((pmin, pmax))
                    row.append(sorted(ks, key=lambda x: 99 if x is None else x))
                name = f"mode {mode} {'unrolled' if unrolled else 'compact'} CLKDIV {div}"
                table[name] = row
                ok = [d for d, ks in enumerate(row) if ks == [0]]
                check(ok and ok == list(range(len(ok))), f"{name}: correct DELAYs {ok} not a range from 0")
                ck = 1e9 / rig.f_clk
                bits = 4 * div
                want = {(bits * 8, (bits if unrolled else bits + 2 * div) * 8)}
                check(pers == want, f"{name}: SCK rise-to-rise min/max {pers} dclk, want {want}")
                after = sorted({str(k) for ks in row[len(ok):] for k in ks})
                lines.append(f"{name}: right up to DELAY {max(ok)} (MISO {2.5 * (max(ok) + 2):.1f} ns after the "
                             f"SCK edge{'' if max(ok) == 31 else f'; from DELAY {max(ok) + 1}: bits late {after}'}); "
                             f"SCK rise to rise {bits} clocks{'' if unrolled else f', {bits + 2 * div} across bytes'} "
                             f"({bits * ck:.0f}{'' if unrolled else f'/{(bits + 2 * div) * ck:.0f}'} ns); "
                             f"{rounds} x 8 random bytes per DELAY, MOSI right in all (device log)")
    finish(rig)
    det(store, "spi_modes_device", table=table)
    return lines


@model_clock(50e6)
def spi_packed_device(rig, store):
    """spi_master_packed, modes 0 and 3, CLKDIV 3, against pe_lab's SPI device: lengths 1..28 queued, DELAY sweep, a 200-byte host-fed message."""
    rng = random.Random(22)
    lines, dets = [], {}
    lengths = [1, 2, 3, 4, 5, 7, 8, 15, 16, 28]
    for mode in (0, 3):
        device_session(rig, mode, packed_prog(mode), 3, cs=True)
        ctrl(rig, 1 | (1 << 8))       # started once: waits for a header with CS high
        res = []

        def message(n, delay, resp=None):
            tx = rng_bytes(rng, n)
            resp = resp or rng_bytes(rng, 16)
            check((rig.read(sim.A_POUT) >> CS) & 1, "CS low before the message")
            rig.spid_arm(dev_resp(resp, mode), delay)
            rig.write(FIFO(0), spipacked.encode(tx), fixed=True)
            nrx = (n + 3) // 4
            wait_until(rig, rx_ready(rig, nrx), 0.05, 2e-5)
            wait_until(rig, lambda: (rig.read(sim.A_POUT) >> CS) & 1, 0.05, 2e-5)
            st = rig.read(STATUS(0))
            check(rxn(st) == nrx and st & 15 == 0, f"STATUS {st:#x} for {n} bytes")
            words = rd(rig, FIFO(0), nrx)
            rises = rig.spid_status()[0]
            check(rises == 8 * n, f"{rises} SCK rises for {n} bytes")
            k = min(16, n)
            check(rig.spid_rx()[16 - k:] == tx[-k:], f"device got {rig.spid_rx().hex()} for {tx.hex()}")
            check((rig.read(sim.A_POUT) >> SCK) & 1 == mode >> 1, "SCK not idle after the frame")
            try:
                got = spipacked.decode(words, n)
            except ValueError as e:
                return f"decode: {e}"
            return lateness(resp, mode, got) if got != expected_rx(resp, n) else 0
        for n in lengths:
            r = message(n, 0)
            check(r == 0, f"mode {mode}: {n} bytes read {r}")
        sweep = [message(8, d) for d in range(32)]
        ok = [d for d, r in enumerate(sweep) if r == 0]
        check(ok and ok == list(range(len(ok))), f"mode {mode}: correct DELAYs {ok}")
        # 200 bytes: header and words fed, RX drained, while the master runs (it pauses
        # with SCK idle when TX runs dry, at the sample phase when RX is full)
        n = 200
        tx, resp = rng_bytes(rng, n), rng_bytes(rng, 16)
        rig.spid_arm(dev_resp(resp, mode), 0)
        words, rx = spipacked.encode(tx), []
        sent, polls, t0 = 0, 0, time.monotonic()
        while len(rx) < (n + 3) // 4:
            st = rig.read(STATUS(0))
            room = 8 - (st & 15)
            if sent < len(words) and room:
                rig.write(FIFO(0), words[sent:sent + room], fixed=True)
                sent += min(room, len(words) - sent)
            rx += rd(rig, FIFO(0), rxn(st))
            polls += 1
            check(polls < 100000 and time.monotonic() - t0 < 5, "200-byte message did not finish")
            if rig.model:
                rig.wait(2e-6)
        wait_until(rig, lambda: (rig.read(sim.A_POUT) >> CS) & 1, 0.05, 2e-5)
        rises = rig.spid_status()[0]
        got = spipacked.decode(rx, n)
        check(rises == 8 * n and got == expected_rx(resp, n) and rig.spid_rx() == tx[-16:],
              f"200 bytes: {rises} rises, RX right {got == expected_rx(resp, n)}")
        dets[mode] = dict(sweep=sweep)
        lines.append(f"mode {mode}: {len(lengths)} queued messages of {lengths} bytes (header + payload "
                     f"preloaded, core never restarted): exact SCK rises 8 N, MOSI and full-duplex RX right, "
                     f"CS high and SCK idle after each; 8-byte message right up to DELAY {max(ok)} "
                     f"({2.5 * (max(ok) + 2):.1f} ns){'' if max(ok) == 31 else f', from {max(ok) + 1}: {sorted(set(map(str, sweep[len(ok):])))}'}"
                     f"; 200 bytes fed/drained by the host while running ({polls} polls): 1600 rises, all 200 RX bytes right")
        ctrl(rig, 1 << 16)
    finish(rig)
    det(store, "spi_packed_device", **{str(k): v for k, v in dets.items()})
    return lines


@model_clock(50e6)
def spi_preloaded_device(rig, store):
    """spi_master_preloaded_once (mode 0, CLKDIV 3) against pe_lab's SPI device: 1..32 bytes, rearmed by pe.spipacked plans without reload."""
    rng = random.Random(23)
    device_session(rig, 0, preloaded_prog(), 3, cs=True)
    lengths = [1, 3, 4, 5, 31, 32, 32]
    for n in lengths:
        tx, resp = rng_bytes(rng, n), rng_bytes(rng, 16)
        st, ctl = rig.read(STATUS(0)), rig.read(sim.A_CTRL)
        check(st & 0xFF == 0 and not st & FAULT and (not ctl & 1 or st & HALTED), f"precondition STATUS {st:#x}")
        plan = spipacked.preload_plan(tx, enabled_before=ctl & 3)
        rig.spid_arm(dev_resp(resp, 0), 0)
        apply(rig, plan["disable_writes"])
        rig.write(plan["register_writes"][0][0], [v for _, v in plan["register_writes"]])
        rig.write(plan["fifo_address"], plan["tx_words"], fixed=True)
        apply(rig, plan["start_writes"])
        st = wait_until(rig, lambda: (lambda s: s if s & HALTED else 0)(rig.read(STATUS(0))), 0.05, 2e-5)
        check(st & 0xFF == plan["rx_words"] << 4 and not st & FAULT, f"{n} bytes: STATUS {st:#x}")
        got = spipacked.decode(rd(rig, plan["fifo_address"], plan["rx_words"]), n)
        st2, pout = rig.read(STATUS(0)), rig.read(sim.A_POUT)
        rises = rig.spid_status()[0]
        k = min(16, n)
        check(got == expected_rx(resp, n), f"{n} bytes: RX {got.hex()} != {expected_rx(resp, n).hex()}")
        check(st2 & 0xFF == 0 and st2 & HALTED and pout & ((1 << CS) | (1 << SCK)) == 1 << CS,
              f"after drain STATUS {st2:#x} POUT {pout:#x}")
        check(rises == 8 * n and rig.spid_rx()[16 - k:] == tx[-k:], f"{n} bytes: {rises} rises, device log")
    finish(rig)
    det(store, "spi_preloaded_device", lengths=lengths)
    return [f"{len(lengths)} one-shot frames of {lengths} bytes, each armed by preload_plan (disable, R0/R1, "
            f"FIFO burst, start; no reset or reload): HALT, no fault, exact RX count, decode right, STATUS "
            f"empty after the drain, CS high and SCK low; device: exactly 8 N SCK rises and MOSI right"]


# ================================================================ SPI: la_capture runs

def side_preamble(base, cpol):
    """postfreeze_demo's trigger preamble for a program with `.side_set 1`:
    its words execute with the program's side-set, so they must hold SCK at
    CPOL (the plain preamble would drive side value 0, an SCK edge in CPOL 1)."""
    p = asm.assemble(f".program trig\n.side_set 1\nx: wait 1 pin {TRIG} side {cpol}\njmp x side {cpol}")
    return [p.words[0], (p.words[1] & ~0x3FF) | base]


def la_spi(rig, prog, cfg, *, prefill=(), extra=(), owners, pout, poe, miso_init=0, miso_events=(),
           burst=(4096, 1, True)):
    """prog on core 0 behind the trigger preamble, la_capture on core 1
    (uio0-7, LA_MASK), the generator raising TRIG at TRIG_CLK and playing
    MISO. Returns (Capture in clocks, core 0 RX words, core 0 STATUS)."""
    init = (miso_init & 1) << MISO
    setup(rig, mode=PG, owners=owners, pout=pout, poe=poe, pg_init=init)
    load(rig, 0, prog, **cfg)
    rig.write(PRE, side_preamble(0, (pout >> SCK) & 1))
    rig.write(sim.core_reg(0, sim.C_ENTRY), PRE)
    for a, v in extra:
        rig.write(a, v)
    load(rig, 1, fw("la_capture", MASK=LA_MASK), in_pins=0, clkdiv=1 << 8)
    if prefill:
        check(len(prefill) <= 8, "at most 8 TX words before the start")
        rig.write(FIFO(0), list(prefill), fixed=True)
    v = init | (1 << TRIG)
    sched = [(TRIG_CLK, v)]
    for dt, bit in sorted(miso_events):
        v = (v & ~(1 << MISO)) | ((bit & 1) << MISO)
        t = TRIG_CLK + dt
        check(t >= sched[-1][0], "MISO events out of order")
        if t == sched[-1][0]:
            sched[-1] = (t, v)
        else:
            sched.append((t, v))
    check(len(sched) <= 1024, f"{len(sched)} generator entries")
    rig.pg_load(sched, init=init)
    ctrl(rig, 0b11 | (0b11 << 8))
    rig.pg_start()
    words = rig.burst(1, *burst)
    words += rd(rig, FIFO(1), rxn(rig.read(STATUS(1))))
    st0 = rig.read(STATUS(0))
    rx = rd(rig, FIFO(0), rxn(st0))
    finish(rig)
    c = pfd.Capture(words, LA_MASK, 1.0)
    check(c.cap.exact, f"capture has {len(c.cap.gaps)} gap(s)")
    check(not c.cap.pulses, f"pulses shorter than a record at {c.cap.pulses}")
    ts = [t for t, _ in c.cap.edges]
    check(all(b - a >= 7 for a, b in zip(ts[1:], ts[2:])), "records closer than 7 ticks: not every edge exact")
    return c, rx, st0


def spi_wire(c, mode, cs_used):
    """FramedPeer's rules (test_spi_packed) on the captured pads: CS changes
    only with SCK idle, no SCK edge outside CS, MOSI never changes at a sample
    edge (CPHA0: leading, CPHA1: trailing). Times in clocks."""
    cpol, cpha = mode >> 1, mode & 1
    E = c.cap.edges
    b = lambda s, p: (s >> p) & 1
    t_trig = next(t for t, s in E if b(s, TRIG))
    check(b(E[0][1], SCK) == cpol and (not cs_used or b(E[0][1], CS)), "SCK not idle / CS low at the start")
    frames, cur = [], (None if cs_used else dict(fall=None, edges=[], bits=[], samples=[]))
    mosi_ch, recs = [], []
    prev = E[0][1]
    for t, s in E[1:]:
        dsck, dmosi, dcs = (b(s, p) != b(prev, p) for p in (SCK, MOSI, CS))
        if t > t_trig:
            recs.append((t - t_trig, s))
        if dmosi:
            mosi_ch.append(t)
        if cs_used and dcs:
            check(b(prev, SCK) == cpol and b(s, SCK) == cpol, f"CS changed at {t} with SCK not idle")
            if not b(s, CS):
                check(cur is None, "CS fell twice")
                cur = dict(fall=t, edges=[], bits=[], samples=[])
            else:
                check(cur is not None, "CS rose outside a frame")
                cur["rise"] = t
                frames.append(cur)
                cur = None
        if dsck:
            check(cur is not None, f"SCK edge at {t} outside CS")
            leading = b(s, SCK) != cpol
            cur["edges"].append((t, leading))
            if leading != bool(cpha):
                check(not dmosi, f"MOSI changed at the sample edge at {t}")
                cur["bits"].append(b(s, MOSI))
                cur["samples"].append(t)
        prev = s
    check(b(prev, SCK) == cpol, "SCK not idle at the end")
    if cs_used:
        check(cur is None, "CS still low at the end")
    else:
        frames = [cur]
    for fr in frames:
        lead = [l for _, l in fr["edges"]]
        check(lead == [True, False] * (len(lead) // 2), "SCK edges do not alternate leading/trailing")
        fr["bytes"] = bytes(int("".join(map(str, fr["bits"][i:i + 8])), 2) for i in range(0, len(fr["bits"]), 8))
        ts = [t for t, _ in fr["edges"]]
        fr["active"] = sorted({ts[i + 1] - ts[i] for i in range(0, len(ts), 2)})
        fr["idle"] = sorted({ts[i + 1] - ts[i] for i in range(1, len(ts) - 1, 2)})
        fr["byte_period"] = sorted({ts[i + 16] - ts[i] for i in range(0, len(ts) - 16, 16)})
        su, ho = [], []
        for t in fr["samples"]:
            before = [m for m in mosi_ch if m < t]
            after = [m for m in mosi_ch if m > t]
            if before:
                su.append(t - before[-1])
            if after:
                ho.append(after[0] - t)
        fr["setup"], fr["hold"] = (min(su) if su else None), (min(ho) if ho else None)
        if fr["fall"] is not None:
            fr["cs_setup"], fr["cs_hold"] = ts[0] - fr["fall"], fr["rise"] - ts[-1]
    gaps = [b_["fall"] - a_["rise"] for a_, b_ in zip(frames, frames[1:])]
    return dict(t_trig=t_trig, frames=frames, recs=recs, cs_gaps=gaps)


def miso_plan(pw, mode, resp_frames, d=D_LAUNCH):
    """(init, [(clocks after the trigger, bit)]) for a peer launching every
    MISO bit d clocks after its launch edge on the predicted wire pw."""
    t0, init, ev = pw["t_trig"], 0, []
    for fr, resp in zip(pw["frames"], resp_frames):
        bits = bits_of(resp)
        lead = [t for t, l in fr["edges"] if l]
        trail = [t for t, l in fr["edges"] if not l]
        if mode & 1 == 0:
            if fr["fall"] is None:
                init = bits[0]
            else:
                ev.append((fr["fall"] - t0 + d, bits[0]))
            ev += [(t - t0 + d, bits[k + 1]) for k, t in enumerate(trail[:len(bits) - 1])]
        else:
            ev += [(t - t0 + d, bits[k]) for k, t in enumerate(lead[:len(bits)])]
        if fr.get("rise") is not None:
            ev.append((fr["rise"] - t0 + d, 0))
    return init, ev


def la_case(rig, name, prog, cfg, mode, cs_used, txs, resps, *, prefill, extra=(), rx_decode):
    """Predict on the cycle model (MISO constant), then run with the predicted MISO; check both wires."""
    owners = {SCK: 0, MOSI: 0, MISO: 0}
    poe = (1 << SCK) | (1 << MOSI)
    pout = (mode >> 1) << SCK
    if cs_used:
        owners[CS], poe, pout = 0, poe | (1 << CS), pout | (1 << CS)
    kw = dict(prefill=prefill, extra=extra, owners=owners, pout=pout, poe=poe)
    mrig = ModelRig(rig.f_clk)
    pc, _, _ = la_spi(mrig, prog, cfg, **kw)
    pw = spi_wire(pc, mode, cs_used)
    init, ev = miso_plan(pw, mode, resps)
    c, rx, st0 = la_spi(rig, prog, cfg, miso_init=init, miso_events=ev, **kw)
    w = spi_wire(c, mode, cs_used)
    check(len(w["frames"]) == len(txs), f"{len(w['frames'])} CS frames for {len(txs)} messages")
    for fr, tx in zip(w["frames"], txs):
        check(len(fr["bits"]) == 8 * len(tx), f"{len(fr['bits'])} sample edges for {len(tx)} bytes")
        check(fr["bytes"] == tx, f"wire {fr['bytes'].hex()} != {tx.hex()}")
    got = rx_decode(rx, st0)
    check(got == list(resps), f"master RX {[g.hex() for g in got]} != {[r.hex() for r in resps]}")
    delta = w["recs"][0][0] - pw["recs"][0][0]
    check([(t - delta, s) for t, s in w["recs"]] == pw["recs"],
          "the board's wire differs from the model's beyond a constant start offset")
    lead = lambda fr, launch: [t for t, l in fr["edges"] if l == launch]
    actual = set()
    for fr_h, fr_p in zip(w["frames"], pw["frames"]):
        le_h = lead(fr_h, bool(mode & 1))
        le_p = lead(fr_p, bool(mode & 1))
        actual |= {(tp - pw["t_trig"] + D_LAUNCH) - (th - w["t_trig"]) for th, tp in zip(le_h, le_p)}
    return w, delta, actual


def fmt_frame(fr, div):
    tk = lambda xs: "/".join(f"{x / div:g}" for x in xs)
    s = (f"SCK active {tk(fr['active'])} idle {tk(fr['idle'])} ticks, bytes every {tk(fr['byte_period'])} ticks, "
         f"MOSI setup {fr['setup']} hold {fr['hold']} clocks at the sample edge")
    if fr.get("cs_setup") is not None:
        s += f", CS low to first edge {fr['cs_setup'] / div:g} ticks, last edge to CS high {fr['cs_hold'] / div:g}"
    return s


@model_clock(50e6)
def spi_modes_la(rig, store):
    """spi_master_modes, all four modes, compact and unrolled, CLKDIV 8, la_capture of SCK/MOSI, MISO from the generator."""
    rng = random.Random(31)
    lines, dets = [], {}
    for mode in range(4):
        for unrolled in (False, True):
            prog = modes_prog(mode, unrolled)
            tx, resp = rng_bytes(rng, 8), rng_bytes(rng, 8)
            cfg = dict(out_pins=(MOSI, 1), side_pins=SCK, in_pins=MISO, clkdiv=LA_DIV << 8)
            w, delta, actual = la_case(
                rig, "modes", prog, cfg, mode, False, [tx], [resp], prefill=[b << 24 for b in tx],
                rx_decode=lambda rx, st: [bytes(x & 0xFF for x in rx)])
            fr = w["frames"][0]
            cpha, div = mode & 1, LA_DIV
            check(fr["active"] == [(1 if cpha else 2) * div], f"active {fr['active']}")
            want_idle = {(3 if cpha else 2) * div} | (set() if unrolled else {(5 if cpha else 4) * div})
            check(set(fr["idle"]) == want_idle, f"idle {fr['idle']}, want {sorted(want_idle)}")
            check(fr["byte_period"] == [(32 if unrolled else 34) * div], f"byte period {fr['byte_period']}")
            name = f"mode {mode} {'unrolled' if unrolled else 'compact'}"
            dets[name] = dict(recs=[(t - delta, s) for t, s in w["recs"]], frame={
                k: fr[k] for k in ("active", "idle", "byte_period", "setup", "hold")})
            lines.append(f"{name}: 8 bytes, MOSI at the sample edges = TX, master RX = the generator's MISO; "
                         f"{fmt_frame(fr, div)}; wire = model's {delta:+d} clocks; MISO changed "
                         f"{sorted(actual)} clocks after the launch edge (negative: before)")
    det(store, "spi_modes_la", **dets)
    return lines


@model_clock(50e6)
def spi_packed_la(rig, store):
    """spi_master_packed, all four modes, CLKDIV 8: two queued messages (5 and 3 bytes) captured with CS."""
    rng = random.Random(32)
    lines, dets = [], {}
    for mode in range(4):
        txs = [rng_bytes(rng, 5), rng_bytes(rng, 3)]
        resps = [rng_bytes(rng, 5), rng_bytes(rng, 3)]
        cfg = dict(out_pins=(MOSI, 1), side_pins=SCK, in_pins=MISO, clkdiv=LA_DIV << 8)

        def dec(rx, st, txs=txs):
            check(rxn(st) == 3, f"{rxn(st)} RX words, want 2 + 1")
            return [spipacked.decode(rx[:2], len(txs[0])), spipacked.decode(rx[2:], len(txs[1]))]
        w, delta, actual = la_case(rig, "packed", packed_prog(mode), cfg, mode, True, txs, resps,
                                      prefill=spipacked.encode(txs[0]) + spipacked.encode(txs[1]), rx_decode=dec)
        f0 = w["frames"][0]
        dets[f"mode {mode}"] = dict(recs=[(t - delta, s) for t, s in w["recs"]])
        lines.append(f"mode {mode}: 5 + 3 bytes in two CS frames, exactly 40 and 24 sample edges, wire = TX, "
                     f"RX decoded right; {fmt_frame(f0, LA_DIV)}; CS high between {w['cs_gaps'][0] / LA_DIV:g} ticks; "
                     f"wire = model's {delta:+d} clocks; MISO {sorted(actual)} clocks after the launch edge (negative: before)")
    det(store, "spi_packed_la", **dets)
    return lines


@model_clock(50e6)
def spi_preloaded_la(rig, store):
    """spi_master_preloaded_once (mode 0), CLKDIV 8: 32- and 5-byte one-shot frames captured with CS."""
    rng = random.Random(33)
    lines, dets = [], {}
    for n in (32, 5):
        tx, resp = rng_bytes(rng, n), rng_bytes(rng, n)
        plan = spipacked.preload_plan(tx)
        cfg = dict(out_pins=(MOSI, 1), side_pins=SCK, in_pins=MISO, clkdiv=LA_DIV << 8)

        def dec(rx, st, n=n, plan=plan):
            check(st & HALTED and not st & FAULT and rxn(st) == plan["rx_words"], f"STATUS {st:#x}")
            return [spipacked.decode(rx, n)]
        w, delta, actual = la_case(rig, "preloaded", preloaded_prog(), cfg, 0, True, [tx], [resp],
                                      prefill=plan["tx_words"], extra=plan["register_writes"], rx_decode=dec)
        fr = w["frames"][0]
        dets[f"{n} bytes"] = dict(recs=[(t - delta, s) for t, s in w["recs"]])
        lines.append(f"{n} bytes: one CS frame, {8 * n} sample edges, wire = TX, RX right, HALT; "
                     f"{fmt_frame(fr, LA_DIV)}; wire = model's {delta:+d} clocks; MISO {sorted(actual)} clocks after "
                     f"the launch edge (negative: before)")
    det(store, "spi_preloaded_la", **dets)
    return lines


# ================================================================ UART

class UartLine:
    """A precomputed 8N1 line in generator clocks, like test_uart._PinTimeline:
    true baud offset (ppm), start phase, alternating +-jitter clocks per bit;
    each level starts at int(cursor)."""

    def __init__(self, f_clk, baud, start, ppm=0, jitter=0):
        self.period = Fraction(round(f_clk)) * 1000000 / (baud * (1000000 + ppm))
        self.cursor = Fraction(start)
        self.jitter, self.nbit = jitter, 0
        self.edges = []

    def frame(self, byte, stop=1):
        for level in [0] + [(byte >> b) & 1 for b in range(8)] + [stop]:
            self.edges.append((int(self.cursor), level))
            self.cursor += self.period + (self.jitter if self.nbit % 2 == 0 else -self.jitter)
            self.nbit += 1
        return self

    def gap(self, bits, level):
        self.edges.append((int(self.cursor), level))
        self.cursor += self.period * bits
        return self

    def idle_at(self, t):
        """Idle high from the end of the last bit (a low STOP ends there) until t."""
        self.edges.append((int(self.cursor), 1))
        self.cursor = max(self.cursor, Fraction(t))
        return self

    def end(self):
        return int(self.cursor)


def pg_lines(lines, idle=0xFF, tail=2000):
    """Generator entries for {bit: UartLine}; other bits at idle; ends idle high."""
    ev = {}
    for bit, ln in lines.items():
        lv = 1
        for t, v in ln.edges + [(ln.end(), 1)]:
            if v != lv:
                ev.setdefault(max(1, t), []).append((bit, v))
                lv = v
    cur, out = idle, []
    for t in sorted(ev):
        for bit, v in ev[t]:
            cur = (cur & ~(1 << bit)) | (v << bit)
        out.append((t, cur))
    end = max(ln.end() for ln in lines.values()) + tail
    out.append((max(end, out[-1][0] + 1), cur))
    check(len(out) <= 1024, f"{len(out)} generator entries")
    return pfd.split_long(out, out[-1][0])


def message(f, data, *, baud=115200, start=50000, ppm=0, jitter=0, bad_final=False, extra=None, brk=False,
            offered=None):
    ln = UartLine(f, baud, start, ppm, jitter)
    offered = data if offered is None else offered
    for i, byte in enumerate(offered):
        ln.frame(byte, stop=0 if bad_final and i == len(data) - 1 else 1)
    if brk:
        ln.gap(12, 0).gap(2, 1)
    if extra is not None:
        ln.gap(3, 1).frame(extra)
    return ln


class CaptureCore:
    """Declared-length capture on core 0 (uart_rx_capture, F0, pe.uartcapture)
    or core 1 (uart_rx_capture_peer, F6, pe.uartcapture_peer)."""

    def __init__(self, rig, core, rx, baud):
        self.rig, self.core, self.rx = rig, core, rx
        self.mod, self.flag = (uartcapture, 1) if core == 0 else (uartcapture_peer, 1 << 6)
        name = "uart_rx_capture" if core == 0 else "uart_rx_capture_peer"
        load(rig, core, fw(name, RX=rx), in_pins=rx, clkdiv=host.clkdiv_for(rig.f_clk, 8 * baud))

    def arm(self, n):
        rig, c = self.rig, self.core
        st, ctl = rig.read(STATUS(c)), rig.read(sim.A_CTRL)
        check(st & 0xFF == 0, f"core {c}: FIFOs not empty before arming ({st:#x})")
        check(not (ctl >> c) & 1 or st & HALTED, f"core {c}: running before arming")
        apply(rig, self.mod.capture_plan(n, enabled_before=ctl & 3))

    def result(self, n):
        """STATUS, flag, then exactly ceil(n/4) words (only if HALT, no fault, right count)."""
        rig, c = self.rig, self.core
        st = rig.read(STATUS(c))
        flag = bool(rig.read(sim.A_FCLR) & self.flag)
        r = dict(halted=bool(st & HALTED), fault=bool(st & FAULT), rx=rxn(st), tx=st & 15, flag=flag, data=None)
        if r["halted"] and not r["fault"] and r["rx"] == (n + 3) // 4:
            words = rd(rig, FIFO(c), r["rx"])
            r["words"] = words
            try:
                r["data"] = self.mod.decode_capture(n, words)
            except ValueError as e:
                r["error"] = str(e)
            r["after"] = rig.read(STATUS(c)) & 0xFF
        return r

    def teardown(self):
        """After an out-of-contract source (not part of the normal rearm): stop the core, clear its FIFOs and flag."""
        rig, c = self.rig, self.core
        ctl = rig.read(sim.A_CTRL)
        ctrl(rig, (ctl & 3 & ~(1 << c)) | (1 << (16 + c)))
        rig.write(sim.A_FCLR, self.flag)


def play(rig, sched, init=0xFF, after_s=0.002):
    rig.pg_load(sched, init=init)
    rig.pg_start()
    wait_until(rig, lambda: not rig.pg_busy(), 5.0, 5e-4)
    rig.wait(after_s)


def ok_capture(r, data, flag):
    return r["halted"] and not r["fault"] and r["data"] == bytes(data) and r["flag"] == flag and r.get("after") == 0


@model_clock(50e6)
def uart_capture(rig, store):
    """uart_rx_capture (core 0, uio0, 115200): the native test's rearmed pair, lengths, baud/phase/jitter corners, bad STOP + break, under-supply."""
    f = rig.f_clk
    setup(rig, mode=PG, pg_init=0xFF)
    cap = CaptureCore(rig, 0, 0, 115200)
    lines, res = [], {}

    def episode(name, n, ln, want_data, want_flag):
        cap.arm(n)
        play(rig, pg_lines({0: ln}))
        r = cap.result(n)
        res[name] = dict(ok=ok_capture(r, want_data, want_flag), flag=r["flag"], rx=r["rx"], halted=r["halted"],
                         data=r["data"].hex() if r["data"] else None)
        return r
    # the native test's pair: 32 bytes with a low final STOP, then 39 d3 81, no reload in between
    r1 = episode("pair32", 32, message(f, BURST, bad_final=True, extra=0xF6), BURST, True)
    check(ok_capture(r1, BURST, True), f"32 bytes, bad final STOP: {r1}")
    r2 = episode("pair3", 3, message(f, b"\x39\xd3\x81", extra=0x69), b"\x39\xd3\x81", False)
    check(ok_capture(r2, b"\x39\xd3\x81", False) and r2["words"] == [0x81d33900], f"39 d3 81: {r2}")
    lines.append(f"rearmed pair (one load): 32 bytes with a low final STOP -> HALT, all 32 bytes kept, F0 = 1, "
                 f"the extra frame 0xF6 not taken; FCLR + rearm, 39 d3 81 -> word {r2['words'][0]:#010x}, F0 = 0")
    for n in (1, 4, 5, 31):
        data = BURST[:n]
        r = episode(f"len{n}", n, message(f, data, extra=0xAA), data, False)
        check(ok_capture(r, data, False), f"{n} bytes: {r}")
    lines.append("lengths 1, 4, 5, 31 (extra 0xAA frame after each): HALT, exact words, data right, F0 = 0")
    corners = [(-30000, 0, 0), (-30000, 51, 0), (0, 51, 0), (30000, 0, 0), (30000, 51, 0), (30000, 0, 2), (30000, 51, 2)]
    for ppm, ph, j in corners:
        r = episode(f"c{ppm}_{ph}_{j}", 32, message(f, BURST, start=50000 + ph, ppm=ppm, jitter=j, extra=0xAA),
                    BURST, False)
        check(ok_capture(r, BURST, False), f"32 bytes at {ppm} ppm, phase {ph}, jitter {j}: {r}")
    lines.append(f"32 bytes at the model test's corners (ppm, phase clocks, jitter clocks) {corners}: all right")
    for n in (3, 4):
        r = episode(f"brk{n}", n, message(f, BURST[:n], bad_final=True, brk=True), BURST[:n], True)
        check(ok_capture(r, BURST[:n], True), f"{n} bytes, bad final STOP + break: {r}")
    lines.append("3 and 4 bytes, final STOP low then a 12-bit break: HALT, data kept, F0 = 1")
    # under-supply: 5 declared, 4 offered
    cap.arm(5)
    play(rig, pg_lines({0: message(f, BURST[:5], offered=BURST[:4])}))
    st = rig.read(STATUS(0))
    r1v = rig.read(sim.core_reg(0, sim.C_R0 + 1))
    check(not st & (HALTED | FAULT) and rxn(st) == 1 and r1v == 1, f"under-supply: STATUS {st:#x}, R1 {r1v}")
    res["under"] = dict(status=st & 0x60FF, r1=r1v)
    cap.teardown()
    lines.append(f"under-supply (5 declared, 4 sent): no HALT, 1 early full word in RX, R1 = {r1v} (teardown: core "
                 "stopped and cleared)")
    # tolerance sweep beyond the qualified +-3 %
    sweep = {}
    for pct in (-7, -6, -5, -4, 4, 5, 6, 7):
        cap.arm(32)
        play(rig, pg_lines({0: message(f, BURST, ppm=pct * 10000, extra=0xAA)}))
        r = cap.result(32)
        good = ok_capture(r, BURST, False)
        sweep[pct] = "ok" if good else ("halt, wrong data" if r["halted"] else f"no HALT, {r['rx']} words") + \
            (", F0" if r["flag"] else "")
        if not good:
            cap.teardown()
    res["sweep"] = sweep
    lines.append(f"baud sweep (32 bytes, outside the qualified +-3 %): {sweep}")
    finish(rig)
    det(store, "uart_capture", **res)
    return lines


@model_clock(50e6)
def uart_packed(rig, store):
    """uart_rx_packed (core 0, uio0, 115200): 32 bytes unattended, 30 (partial group stays), a bad STOP."""
    f = rig.f_clk
    setup(rig, mode=PG, pg_init=0xFF)
    load(rig, 0, fw("uart_rx_packed", RX=0), in_pins=0, clkdiv=host.clkdiv_for(f, 8 * 115200))
    lines, res = [], {}

    def run(name, ln, nwords):
        ctrl(rig, 1 << 16)
        rig.write(sim.A_FCLR, 1)
        ctrl(rig, 1 | (1 << 8))
        play(rig, pg_lines({0: ln}))
        st = rig.read(STATUS(0))
        words = rd(rig, FIFO(0), rxn(st))
        flag = rig.read(sim.A_FCLR) & 1
        res[name] = dict(words=words, flag=flag)
        check(len(words) == nwords, f"{name}: {len(words)} words, want {nwords}")
        return b"".join(w.to_bytes(4, "little") for w in words), flag
    got, fl = run("b32", message(f, BURST), 8)
    check(got == BURST and not fl, f"32 bytes: {got.hex()} F0 {fl}")
    got, fl = run("b30", message(f, BURST[:30]), 7)
    check(got == BURST[:28] and not fl, f"30 bytes: {got.hex()}")
    data = BURST[:16]
    ln = UartLine(f, 115200, 50000)
    for i, byte in enumerate(data):
        ln.frame(byte, stop=0 if i == 5 else 1)
        if i == 5:
            ln.gap(2, 1)
    got, fl = run("bad", ln, 4)
    check(got == data and fl, f"bad STOP: {got.hex()} F0 {fl}")
    finish(rig)
    det(store, "uart_packed", **res)
    return ["32 bytes, no host service: 8 little-endian words = the bytes, F0 = 0",
            "30 bytes: 7 words (28 bytes); the last 2 stay in ISR (no implicit flush), as documented",
            "16 bytes with byte 5's STOP low: F0 = 1, all 4 words kept (the bad byte included)"]


@model_clock(5e6)
def uart_packed_stream(rig, store):
    """uart_rx_packed, 160 random bytes at 115200 back to back, drained by the host while they arrive."""
    f = rig.f_clk
    rng = random.Random(41)
    data = rng_bytes(rng, 160)
    setup(rig, mode=PG, pg_init=0xFF)
    load(rig, 0, fw("uart_rx_packed", RX=0), in_pins=0, clkdiv=host.clkdiv_for(f, 8 * 115200))
    rig.write(sim.A_FCLR, 1)
    ctrl(rig, 1 | (1 << 8))
    ln = message(f, data, start=round(1e-3 * f))
    sched = pg_lines({0: ln})
    rig.pg_load(sched, init=0xFF)
    rig.pg_start()
    recs = poll_records(rig, [0], lambda r: len(r[0]) >= 40, step_s=5e-4, timeout_s=2.0, after_s=0.002)[0]
    flag = rig.read(sim.A_FCLR) & 1
    finish(rig)
    got = b"".join(w.to_bytes(4, "little") for w in recs)
    check(got == data and not flag, f"stream: {len(recs)} words, right {got == data}, F0 {flag}")
    det(store, "uart_packed_stream", words=recs)
    return [f"160 bytes (40 words, {len(sched)} generator entries, {ln.end() / f * 1e3:.1f} ms): all right, F0 = 0; "
            "the host drained the 8-word RX FIFO while the bytes arrived"]


@model_clock(5e6)
def uart_dual(rig, store):
    """uart_rx_capture (core 0, uio0, F0) and uart_rx_capture_peer (core 1, uio1, F6) at once; core 1 rearmed while core 0 captures."""
    f = rig.f_clk
    rng = random.Random(42)
    setup(rig, mode=PG, pg_init=0xFF)
    c0 = CaptureCore(rig, 0, 0, 115200)
    c1 = CaptureCore(rig, 1, 1, 57600)
    lines, res = [], {}
    # round 1: overlapping, core 1's final STOP low
    d1 = rng_bytes(rng, 7)
    c0.arm(32)
    c1.arm(7)
    ln0 = message(f, BURST, start=round(1e-3 * f), extra=0x55)
    ln1 = message(f, d1, baud=57600, start=round(1.1e-3 * f), bad_final=True, extra=0x33)
    play(rig, pg_lines({0: ln0, 1: ln1}))
    r0, r1 = c0.result(32), c1.result(7)
    check(ok_capture(r0, BURST, False), f"core 0: {r0}")
    check(ok_capture(r1, d1, True), f"core 1: {r1}")
    res["round1"] = dict(c0=r0["data"].hex(), c1=r1["data"].hex(), f0=r0["flag"], f6=r1["flag"])
    lines.append(f"overlapping: core 0 32 bytes at 115200 (F0 = 0) and core 1 7 bytes at 57600 with a low final "
                 f"STOP (F6 = 1, data kept): both HALT with exact words; each flag only its own")
    # round 2: core 0 takes 32 bytes at 19200 (16.7 ms); core 1 gets two 4-byte messages at 115200,
    # rearmed by the host (pe.uartcapture_peer plan keeps core 0 enabled) between them
    ctrl(rig, 0)                      # both stopped to reload (19200 / 115200)
    c0b = CaptureCore(rig, 0, 0, 19200)
    c1b = CaptureCore(rig, 1, 1, 115200)
    rig.write(sim.A_FCLR, 1 | (1 << 6))
    ma, mb = rng_bytes(rng, 4), rng_bytes(rng, 4)
    c0b.arm(32)
    c1b.arm(4)
    ln0 = message(f, BURST, baud=19200, start=round(1e-3 * f))
    ln1 = message(f, ma, start=round(2e-3 * f))
    ln1.idle_at(round(12e-3 * f)).frame(mb[0]).frame(mb[1]).frame(mb[2]).frame(mb[3])
    sched = pg_lines({0: ln0, 1: ln1})
    rig.pg_load(sched, init=0xFF)
    rig.pg_start()
    wait_until(rig, lambda: rig.read(STATUS(1)) & HALTED, 1.0, 2e-4)
    ra = c1b.result(4)
    check(ok_capture(ra, ma, False), f"core 1, first message: {ra}")
    st0 = rig.read(STATUS(0))
    check(not st0 & HALTED, "core 0 finished too early")
    ctl = rig.read(sim.A_CTRL)
    c1b.arm(4)
    check(rig.read(sim.A_CTRL) & 1, "core 0 not enabled after core 1's rearm")
    wait_until(rig, lambda: not rig.pg_busy(), 5.0, 5e-4)
    rig.wait(0.002)
    rb, r0 = c1b.result(4), c0b.result(32)
    check(ok_capture(rb, mb, False), f"core 1, second message: {rb}")
    check(ok_capture(r0, BURST, False), f"core 0 at 19200: {r0}")
    finish(rig)
    res["round2"] = dict(c0=r0["data"].hex(), a=ra["data"].hex(), b=rb["data"].hex(), ctrl=ctl)
    det(store, "uart_dual", **res)
    lines.append(f"core 0 capturing 32 bytes at 19200 ({ln0.end() / f * 1e3:.1f} ms) while core 1 took a 4-byte "
                 f"message at 115200, was drained and rearmed by the host (CTRL {ctl:#x} -> core 1 restarted, core 0 "
                 "untouched), and took a second: all three right, F0 = F6 = 0")
    return lines


@model_clock(5e6)
def uart_mailbox(rig, store):
    """uart_rx_mailbox (core 1, RX uio1, READY uo0, byte on V24..V31) with the host standing in for core 0's USB ACK (FCLR F7)."""
    f = rig.f_clk
    rng = random.Random(43)
    data = rng_bytes(rng, 6)
    bad = 3                                            # this frame's STOP is low
    READY = 8
    setup(rig, mode=PG, owners={1: 1, READY: 1, **{p: 1 for p in range(24, 32)}}, pg_init=0xFF)
    load(rig, 1, fw("uart_rx_mailbox", RX=1, READY=READY), clkdiv=host.clkdiv_for(f, 8 * 115200))
    rig.write(sim.A_FCLR, 0xC0)
    ctrl(rig, 0b10 | (0b10 << 8))
    pins = lambda: rig.read(sim.A_PINS)
    wait_until(rig, lambda: (pins() >> READY) & 1, 0.1, 1e-4)
    ln = UartLine(f, 115200, round(1e-3 * f))
    for k, byte in enumerate(data):
        ln.idle_at(round((1e-3 + 4e-3 * k) * f)).frame(byte, stop=0 if k == bad else 1)
    rig.pg_load(pg_lines({1: ln}), init=0xFF)
    rig.pg_start()
    got, errors, ready_low, t0 = [], 0, [], time.monotonic()
    tm = 0.0
    while rig.pg_busy() or len(got) + errors < len(data):
        fl = rig.read(sim.A_FSET)
        if fl & 0x80:
            p = pins()
            got.append(p >> 24)
            ready_low.append(not (p >> READY) & 1)
            rig.write(sim.A_FCLR, 0x80)                    # what core 0 does after a valid DATA ACK
            wait_until(rig, lambda: (pins() >> READY) & 1, 0.01, 2e-5)
        if fl & 0x40:
            errors += 1
            rig.write(sim.A_FCLR, 0x40)
        rig.wait(2e-4)
        tm += 2e-4
        check((tm if rig.model else time.monotonic() - t0) < 2.0, f"mailbox: got {got}, {errors} errors")
    st = finish(rig)
    want = [b for k, b in enumerate(data) if k != bad]
    check(got == want and errors == 1, f"mailbox bytes {bytes(got).hex()} (want {bytes(want).hex()}), "
                                       f"{errors} framing errors (want 1)")
    check(all(ready_low), "READY high while F7 was set")
    det(store, "uart_mailbox", got=got, errors=errors)
    return [f"6 frames 4 ms apart at 115200, frame {bad} with a low STOP: {len(got)} bytes published on V24..V31 "
            f"with F7 ({bytes(got).hex()}), READY low each time F7 was seen and high again after FCLR F7; the bad "
            f"frame discarded with F6 = 1 (once)"]


# ================================================================ main

GROUPS = {
    "spidev": [spi_modes_device, spi_packed_device, spi_preloaded_device],
    "spila": [spi_modes_la, spi_packed_la, spi_preloaded_la],
    "uart": [uart_capture, uart_packed, uart_packed_stream, uart_dual],
    "mailbox": [uart_mailbox],
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


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("groups", nargs="*", default=list(GROUPS))
    ap.add_argument("--model", action="store_true", help="the cycle model instead of the board")
    ap.add_argument("--save", help="keep the run summaries (JSON)")
    a = ap.parse_args()
    if a.groups[:1] == ["compare"]:
        return compare_main(*a.groups[1:3])
    bad = [g for g in a.groups if g not in GROUPS and g not in FUNCS]
    if bad:
        ap.error(f"unknown group(s) {bad}; choose from {list(GROUPS)}")
    store = {} if a.save else None
    if a.model:
        rig = ModelRig()
        print("cycle model (pe.sim) with pe_lab's generator and SPI device modelled", flush=True)
    else:
        rig = HwRig()
        i = rig.ident
        print(f"bitstream RTL revision {i['rtl_rev'][:12]}{' (dirty)' if i['dirty'] else ''}, built {i['build_utc']} "
              f"UTC, digest {i['digest']}, clk_div {i['clk_div']}", flush=True)
        print(f"protoemu clk measured {rig.f_meas / 1e6:.5f} MHz over 2 s; CLKDIVs derived for "
              f"{rig.f_clk / 1e6:.5f} MHz", flush=True)
    try:
        results = run_all(rig, a.groups, store)
    finally:
        rig.close()
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
