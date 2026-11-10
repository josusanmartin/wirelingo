#!/usr/bin/env python3
"""protoemu hardware bring-up on the KV260 (run as root on the Kria with the
protoemu app loaded):

    sudo python3 fpga/kv260/host/bringup_kria.py [--only NAME ...] [--model] [--expect REV] [--cores N]

The first step reads the bitstream's identity from pe_lab (RTL revision, build
time, digest; fpga/kv260/build_id.py) and fails unless the RTL revision is the
expected one: --expect (a commit, or a unique prefix), else expected_build.json
next to this script, which install.sh writes from the checkout it installs
(the RTL revision that checkout's tools, firmware and host software belong
to). A bitstream built from uncommitted RTL fails too (--allow-dirty).

The `id` step reads the chip ID and accepts only a KV260 build's
(pe_kria.BUILDS: v0.2 0x50450002, v0.3 0x50452203 and 0x50453303), with the
core count the active app holds (fabric_guard.APP_BUILD; --cores overrides).
The 2- and 3-core builds of one checkout have the same RTL revision, so this
is the check that tells them apart. The steps then use every core of the
build: `regs` all of them, `loopback` a ring (0 -> 1 -> ... -> 0), and `crc32`
(v0.3 only; SKIP on a v0.2 bitstream) the 32-bit CRC tap with POLY_HI on each
core at once, against the LFSR of docs/isa.md §4.5 and zlib.

Programs are assembled from firmware/*.pasm (crc32_sink from host/) with tools/pe and loaded through
the bit-banged SPI host port (tools/micropython/protoemu.py over pe_kria.Board).
--model runs the same steps against the cycle model (tools/pe/modelhost.py,
--cores geometry, default 3) through the pin-level SPI port model of
test/model/test_micropython_driver.py, skipping the wall-clock UART and quad
steps; it checks this script, not the FPGA.
"""
import argparse
import random
import sys
import time
import zlib

import json
import pathlib

import pe_kria
from pe_kria import ROOT, Board, QuadTransport

from pe import asm, host, isa, sim   # noqa: E402  (pe_kria put tools/ on sys.path)
from pe import profile as profile_mod   # noqa: E402

FW = ROOT / "firmware"
HOST = ROOT / "fpga" / "kv260" / "host"
P_UO0, P_UI0, P_V24 = 8, 16, 24
CRC32_POLY = 0x04C11DB7
ST_STALLED = 1 << 12                     # STATUS bit (pe_core.sv status16)


class Skip(Exception):
    """The step doesn't apply to this build (printed as SKIP, not counted as a failure)."""


def load(pe, name, core, defines=None, fw=FW, **cfg):
    """Assemble <fw>/<name>.pasm and load it into core's bank (core stopped)."""
    prog = asm.assemble((fw / f"{name}.pasm").read_text(), defines or {})
    profile = profile_mod.Profile(f"kv260-{pe.ncores}c", pe.ncores, pe.nbanks)   # the driver has the chip's geometry
    writes = host.load_program(core, prog, core * 256, profile=profile, **cfg)
    for addr, words in host.coalesce(writes):
        pe.write(addr, words)
    return len(prog.words)


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)


# ---- steps -------------------------------------------------------------------

def expected_rev(arg):
    if arg:
        return arg.lower(), "--expect"
    f = pathlib.Path(__file__).resolve().parent / "expected_build.json"
    if f.exists():
        return json.loads(f.read_text())["rtl_rev"], f.name
    return None, None


def step_build(ctx):
    from pe_lab import Lab
    ident = Lab().identity()
    print(f"  bitstream RTL revision {ident['rtl_rev']}" + (" (DIRTY: uncommitted RTL)" if ident["dirty"] else ""))
    print(f"  built {ident['build_utc']} UTC from HEAD {ident['head'][:12]}, "
          f"RTL digest {ident['digest']}, clk_div {ident['clk_div']}")
    want, src = expected_rev(ctx.expect)
    check(want is not None, "no expected revision: pass --expect REV or install with install.sh")
    print(f"  expected RTL revision {want} ({src})")
    check(len(want) >= 7 and ident["rtl_rev"].startswith(want),
          f"loaded bitstream is RTL revision {ident['rtl_rev'][:12]}, expected {want[:12]}")
    check(not ident["dirty"] or ctx.allow_dirty, "bitstream built from uncommitted RTL (--allow-dirty)")


def step_clock(ctx):
    hz = ctx.board.clk_hz()
    ctx.f_clk = hz
    print(f"  protoemu clk measured {hz / 1e6:.4f} MHz")
    check(abs(hz - ctx.f_nominal) < 0.01 * ctx.f_nominal, "clock off by more than 1 %")


def chip(ctx):
    """The build's Geometry, read from the ID register once (without a reset when
    the `id` step didn't run); ctx.pe then drives that many cores. Raises for an
    ID no KV260 build has, or another core count than ctx.cores."""
    if getattr(ctx, "geom", None) is None:
        got = ctx.pe.read(sim.A_ID)
        try:
            g = pe_kria.geometry(got, getattr(ctx, "cores", None))
        except ValueError as e:
            raise AssertionError(str(e)) from None
        ctx.pe = ctx.board.protoemu(ncores=g.ncores, nbanks=g.nbanks)
        ctx.geom = g
    return ctx.geom


def all_cores(ctx):
    return (1 << chip(ctx).ncores) - 1


def step_id(ctx):
    ctx.board.reset()
    ctx.geom = None
    got = ctx.pe.read(sim.A_ID)
    print(f"  ID (0x800) = 0x{got:08X}")
    g = chip(ctx)
    print(f"  {g.version}, {g.ncores} cores, {g.nbanks} banks"
          + (f" (expected {ctx.cores} cores)" if getattr(ctx, "cores", None) else ""))


def step_regs(ctx, n=200):
    ncores = chip(ctx).ncores
    pe, rng = ctx.pe, random.Random(1)
    regs = [sim.core_reg(c, sim.C_R0 + r) for c in range(ncores) for r in range(4)]
    for _ in range(n):
        vals = [rng.getrandbits(16) for _ in regs]    # R0..R3 are 16-bit
        for a, v in zip(regs, vals):
            pe.write(a, v)
        back = [pe.read(a) for a in regs]
        check(back == vals, f"register readback {back} != {vals}")
        for c in range(ncores):                       # incrementing read
            burst = pe.read(regs[4 * c], 4)
            check(burst == vals[4 * c:4 * c + 4], f"core {c} burst readback {burst} != {vals[4 * c:4 * c + 4]}")
    print(f"  {n} rounds: R0..R3 of all {ncores} cores written and read back, single and burst")


def step_pins(ctx):
    chip(ctx)
    pe, b = ctx.pe, ctx.board
    pe.write(sim.A_OD, 0)
    pe.write(sim.A_POE, 0)
    for ui_low, uio in ((0x15, 0xA5), (0x0A, 0x5A), (0x1F, 0x00), (0x00, 0xFF)):
        b.set_inputs(ui_low=ui_low, uio=uio)
        pins = pe.read(sim.A_PINS)
        check(pins & 0xFF == uio and (pins >> 16) & 0x1F == ui_low,
              f"PINS 0x{pins:08X} for ui[4:0]=0x{ui_low:02X} uio pads=0x{uio:02X}")
    print("  inputs: ui[4:0] and undriven uio pads reach PINS through the synchronisers")
    for out, oe in ((0x00007F33, 0x0F), (0x00002A0C, 0xF0), (0xFF00550F, 0xFF)):
        pe.write(sim.A_POUT, out)
        pe.write(sim.A_POE, oe)
        check(pe.read(sim.A_POUT) == out and pe.read(sim.A_POE) == oe, "PIN_OUT/PIN_OE readback")
        uo, uio_out, uio_oe, _ = b.pins()
        check(uo & 0x7F == (out >> 8) & 0x7F, f"uo_out 0x{uo:02X} for PIN_OUT 0x{out:08X}")
        check(uio_out == out & 0xFF and uio_oe == oe, f"uio_out/oe 0x{uio_out:02X}/0x{uio_oe:02X}")
        pins = pe.read(sim.A_PINS)
        pad = (out & oe | b.uio_drive & ~oe) & 0xFF
        check(pins & 0xFF == pad, f"PINS[7:0] 0x{pins & 0xFF:02X}, pads 0x{pad:02X}")
    pe.write(sim.A_OD, 0x01)                         # open drain on P0: 0 pulls low
    pe.write(sim.A_POUT, 0)
    _, uio_out, uio_oe, _ = b.pins()
    check(uio_oe & 1 and not uio_out & 1, "open-drain low")
    pe.write(sim.A_POUT, 1)
    _, uio_out, uio_oe, _ = b.pins()
    check(not uio_oe & 1, "open-drain release")
    pe.write(sim.A_OD, 0)
    pe.write(sim.A_POE, 0)
    pe.write(sim.A_POUT, 0)
    b.set_inputs(ui_low=0, uio=0xFF)
    print("  outputs: PIN_OUT/PIN_OE drive uo_out/uio_out/uio_oe; open drain on P0 works")


def uart_cfg(ctx, baud):
    return host.clkdiv_for(ctx.f_clk, 8 * baud)


def step_loopback(ctx, msg=b"Hello from protoemu on a KV260!"):
    """UART TX -> virtual pin 24 -> UART RX, 115200 8N1, around the ring of cores:
    0 -> 1 on a 2-core build (as before), 0 -> 1, 1 -> 2, 2 -> 0 on a 3-core one."""
    ncores = chip(ctx).ncores
    for tx in range(ncores if ncores > 2 else 1):
        loopback_pair(ctx, tx, (tx + 1) % ncores, msg)


def loopback_pair(ctx, tx_core, rx_core, msg):
    pe = ctx.pe
    allc, tx_bit, rx_bit = all_cores(ctx), 1 << tx_core, 1 << rx_core
    div = uart_cfg(ctx, 115200)
    pe.stop(allc)
    pe.clear_fifos(allc)
    pe.write(sim.A_FCLR, 1)
    pe.own_pins(tx_core, [P_V24])
    n_rx = load(pe, "uart_rx", rx_core, {"RX": P_V24}, in_pins=P_V24, clkdiv=div)
    n_tx = load(pe, "uart_tx", tx_core, out_pins=(P_V24, 1), side_pins=P_V24, clkdiv=div)
    pe.start(rx_bit)
    pe.start(tx_bit)
    got, sent = bytearray(), 0
    try:
        for _ in range(20 * len(msg)):
            got.extend(w & 0xFF for w in pe.pop_all(rx_core))
            if len(got) >= len(msg):
                break
            tx = pe.status(tx_core)
            check(not (tx | pe.status(rx_core)) & (protoemu_st()), "core halted or faulted")
            if sent < len(msg) and (tx & 15) < pe.depth:
                pe.write(sim.core_reg(tx_core, sim.C_FIFO), msg[sent], fixed=True)
                sent += 1
    finally:
        pe.stop(allc)
    print(f"  core {tx_core} -> core {rx_core}: uart_tx ({n_tx} words) and uart_rx ({n_rx} words)")
    print(f"  sent {msg!r}\n  got  {bytes(got)!r}")
    check(bytes(got) == msg, f"loopback core {tx_core} -> core {rx_core} mismatch")
    check(not pe.read(sim.A_FSET) & 1, "framing error flag F0 set")


def protoemu_st():
    import protoemu
    return protoemu.ST_HALTED | protoemu.ST_FAULT


def step_uart_tx_pin(ctx, msg=b"protoemu", baud=9600):
    """core 0 UART TX on uo[0] (P8) at 9600 baud, decoded by polling the GPIO."""
    chip(ctx)
    pe, b = ctx.pe, ctx.board
    pe.stop(all_cores(ctx))
    pe.clear_fifos(all_cores(ctx))
    pe.own_pins(0, [P_UO0])
    load(pe, "uart_tx", 0, out_pins=(P_UO0, 1), side_pins=P_UO0, clkdiv=uart_cfg(ctx, baud))
    for ch in msg:                       # queue the whole message while stopped (8-deep FIFO)
        pe.write(sim.core_reg(0, sim.C_FIFO), ch, fixed=True)
    check(pe.status(0) & 15 == len(msg), "TX FIFO level")
    bit_ns = 1e9 / baud
    span = int((len(msg) * 10 + 4) * bit_ns)
    pe.start(0b01)
    edges, last = [], None
    t0 = time.perf_counter_ns()
    inp, settle = b.regs.inp, b.settle
    while True:
        for _ in range(settle):
            inp()
        v = inp() & 1
        t = time.perf_counter_ns()
        if v != last:
            edges.append((t, v))
            last = v
        if t - t0 > span:
            break
    pe.stop(0b01)
    pe.write(sim.A_POUT, 0)

    def level(t):
        lv = edges[0][1]
        for te, v in edges:
            if te > t:
                break
            lv = v
        return lv
    out, i = bytearray(), 0
    while True:      # next falling edge after the line has been high
        i = next((k for k in range(max(i, 1), len(edges)) if edges[k][1] == 0 and edges[k - 1][1] == 1), None)
        if i is None:
            break
        ts = edges[i][0]
        byte = sum(level(ts + (1.5 + k) * bit_ns) << k for k in range(8))
        check(level(ts + 9.5 * bit_ns) == 1, "stop bit low")
        out.append(byte)
        i = next((k for k in range(i, len(edges)) if edges[k][0] > ts + 9.2 * bit_ns), len(edges))
    widths = [(edges[k + 1][0] - edges[k][0]) / bit_ns for k in range(1, len(edges) - 1)]
    err = max(abs(w - round(w)) for w in widths) if widths else 0
    print(f"  {len(edges)} edges on uo[0], decoded {bytes(out)!r} at {baud} baud; "
          f"worst edge {err * 100:.1f} % of a bit off the grid (Python polling jitter)")
    check(bytes(out) == msg, "uo[0] UART decode mismatch")


def step_uart_rx_pin(ctx, msg=b"KV260 ok", baud=9600):
    """Python bit-bangs 9600 8N1 on ui[0] (P16); core 1 UART RX pushes the bytes."""
    chip(ctx)
    pe, b = ctx.pe, ctx.board
    pe.stop(all_cores(ctx))
    pe.clear_fifos(all_cores(ctx))
    pe.write(sim.A_FCLR, 1)
    b.set_inputs(ui_low=1)              # idle high before the RX program starts
    load(pe, "uart_rx", 1, {"RX": P_UI0}, in_pins=P_UI0, clkdiv=uart_cfg(ctx, baud))
    pe.start(0b10)
    bit_ns = int(1e9 / baud)
    t = time.perf_counter_ns() + 2 * bit_ns
    for ch in msg:
        for lv in [0] + [(ch >> k) & 1 for k in range(8)] + [1, 1]:
            while time.perf_counter_ns() < t:
                pass
            b.set_inputs(ui_low=lv)
            t += bit_ns
    time.sleep(0.01)
    got = bytes(w & 0xFF for w in pe.pop_all(1))
    framing = pe.read(sim.A_FSET) & 1
    pe.stop(0b10)
    print(f"  drove {msg!r} on ui[0]; core 1 received {got!r}; framing error flag {framing}")
    check(got == msg and not framing, "ui[0] UART receive mismatch")


def step_quad(ctx, n=50):
    chip(ctx)
    pe, rng = ctx.pe, random.Random(2)
    q = QuadTransport(ctx.board)
    q.enable(pe)
    try:
        got = pe.read(sim.A_ID)
        print(f"  quad: ID = 0x{got:08X}")
        check(got == chip(ctx).id, f"quad ID 0x{got:08X}, 1-bit 0x{chip(ctx).id:08X}")
        regs = sim.core_reg(1, sim.C_R0)
        for _ in range(n):
            vals = [rng.getrandbits(16) for _ in range(4)]
            pe.write(regs, vals)                         # quad burst write
            check(pe.read(regs, 4) == vals, "quad burst readback")
            pe.transport = None
            check([pe.read(regs + i) for i in range(4)] == vals, "1-bit readback of quad write")
            pe.transport = q
    finally:
        pe.transport = None
        pe.write(0x80B, 0)
    print(f"  quad: {n} burst write/read rounds on core 1 R0..R3, cross-checked over 1-bit")


def crc32_msb_first(words, crc=0xFFFFFFFF):
    """docs/isa.md §4.5's register after each word's bits, MSB first (W = 32, CRC-32's POLY)."""
    for w in words:
        for i in range(31, -1, -1):
            fb = (crc >> 31) ^ ((w >> i) & 1)
            crc = ((crc << 1) & 0xFFFFFFFF) ^ (CRC32_POLY if fb else 0)
    return crc


def zlib_view(words):
    """The bytes whose zlib.crc32 is the complement of crc32_msb_first(words) bit-reversed:
    zlib's CRC-32 takes each byte LSB first, so each byte here is a word's byte bit-reversed."""
    rev8 = lambda b: int(f"{b:08b}"[::-1], 2)
    return bytes(rev8((w >> s) & 0xFF) for w in words for s in (24, 16, 8, 0))


def rev32(v):
    return int(f"{v:032b}"[::-1], 2)


def step_crc32(ctx, nwords=24):
    """v0.3: every core at once shifts host words out one bit at a time (crc32_sink)
    into its 32-bit CRC tap (CRCCTRL W = 32 with OUT_TAP, POLY_HI = 0x04C1, preset all
    ones through the CRC register); the host reads each core's CRC and compares it with
    the LFSR and with zlib. The sink stalls on an empty FIFO, so the host's pace doesn't
    matter. On the board this is the one step that writes POLY_HI (core offset 0x11)."""
    g = chip(ctx)
    if g.version == "v0.2":
        raise Skip("a v0.2 bitstream: its CRC tap is 16 bits, no POLY_HI")
    pe, allc, cores = ctx.pe, all_cores(ctx), range(g.ncores)
    ctrl, poly_hi = isa.crcctrl(CRC32_POLY, 32, out_tap=1)
    rng = random.Random(5)
    data = {c: [rng.getrandbits(32) for _ in range(nwords)] for c in cores}
    pe.stop(allc)
    pe.clear_fifos(allc)
    for c in cores:
        load(pe, "crc32_sink", c, fw=HOST)
        pe.write(sim.core_reg(c, sim.C_CRCCTRL), ctrl)
        pe.write(sim.core_reg(c, sim.C_POLY_HI), poly_hi)
        pe.write(sim.core_reg(c, sim.C_CRC), 0xFFFFFFFF)      # all 32 bits, as W > 16
    check(all(pe.read(sim.core_reg(c, sim.C_CRC)) == 0xFFFFFFFF for c in cores), "CRC preset readback")
    pe.start(allc)
    sent = dict.fromkeys(cores, 0)
    try:
        for _ in range(100 * nwords):
            busy = False
            for c in cores:
                st = pe.status(c)
                check(not st & protoemu_st(), f"core {c} halted or faulted (STATUS 0x{st:04X})")
                n = min(pe.depth - (st & 15), nwords - sent[c])
                if n > 0:
                    pe.write(sim.core_reg(c, sim.C_FIFO), data[c][sent[c]:sent[c] + n], fixed=True)
                    sent[c] += n
                busy |= sent[c] < nwords or st & 15 != 0 or not st & ST_STALLED
            if not busy:
                break
        else:
            raise AssertionError(f"the sinks didn't drain: sent {sent}")
    finally:
        pe.stop(allc)
    for c in cores:
        got, want = pe.read(sim.core_reg(c, sim.C_CRC)), crc32_msb_first(data[c])
        check(zlib.crc32(zlib_view(data[c])) == rev32(want) ^ 0xFFFFFFFF, "the LFSR disagrees with zlib")
        print(f"  core {c}: {nwords} words, CRC 0x{got:08X}, expected 0x{want:08X}")
        check(got == want, f"core {c} CRC 0x{got:08X}, expected 0x{want:08X}")


STEPS = [("build", step_build), ("clock", step_clock), ("id", step_id), ("regs", step_regs), ("pins", step_pins),
         ("loopback", step_loopback), ("uart_tx_pin", step_uart_tx_pin),
         ("uart_rx_pin", step_uart_rx_pin), ("quad", step_quad), ("crc32", step_crc32)]
WALL_CLOCK = {"build", "clock", "uart_tx_pin", "uart_rx_pin", "quad"}


class ModelRegs:
    """pe_kria.DevMem stand-in: the cycle model behind the pin-level SPI port model."""

    def __init__(self, ncores=3):
        sys.path.insert(0, str(ROOT / "test" / "model"))
        from pe.modelhost import ModelHost
        from test_micropython_driver import SpiPortModel
        self.mh = ModelHost(ncores=ncores, nbanks=ncores)
        self.port = SpiPortModel(self.mh)

    def out(self, v):
        self.mh.ui = v & 0x1F
        self.mh.pull_up = (v >> 16) & 0xFF   # undriven uio pads take the PS's value
        self.port.pins(v & 0xFF)

    def inp(self):
        uo, uio_out, uio_oe = self.mh.chip.outputs()
        return uo | (self.port.miso << 7) | (uio_out << 8) | (uio_oe << 16) | (1 << 24)

    def cycles_gray(self):
        c = self.mh.chip.cycle & 0xFFFFFFFF
        return c ^ (c >> 1)


class Ctx:
    pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", help="steps to run: " + " ".join(n for n, _ in STEPS))
    ap.add_argument("--model", action="store_true", help="run against the cycle model")
    ap.add_argument("--mhz", type=float, default=50.0, help="nominal protoemu clock")
    ap.add_argument("--settle", type=int, default=1, help="extra GPIO input reads per sample")
    ap.add_argument("--expect", help="expected RTL revision (default: expected_build.json)")
    ap.add_argument("--allow-dirty", action="store_true", help="accept a bitstream built from uncommitted RTL")
    ap.add_argument("--cores", type=int, choices=(2, 3),
                    help="the chip's core count: on the board, the expected one (default: the active app's, "
                         "fabric_guard.APP_BUILD); with --model, the model's (default 3)")
    a = ap.parse_args()
    sys.exit(run(a))


def run(a, regs=None):
    """main() without the exit: the status. regs: a DevMem stand-in (tests)."""
    ctx = Ctx()
    if a.model:
        ctx.cores = a.cores or 3
        regs = ModelRegs(ctx.cores)
    ctx.board = Board(regs, settle=a.settle)
    if not a.model:
        ctx.cores = a.cores
        if regs is None:                      # on the board: the app the guard saw active (DevMem checked it)
            try:
                app, ctx.cores = pe_kria.app_cores(a.cores)
            except ValueError as e:
                print(e)
                return 2
            print(f"active app {app} ({ctx.cores}-core build)")
    ctx.pe = ctx.board.protoemu()
    ctx.f_nominal = ctx.f_clk = a.mhz * 1e6
    ctx.expect, ctx.allow_dirty = a.expect, a.allow_dirty
    names = a.only or [n for n, _ in STEPS]
    failed = 0
    for name, fn in STEPS:
        if name not in names or (a.model and name in WALL_CLOCK):
            continue
        print(f"[{name}]")
        t = time.monotonic()
        try:
            fn(ctx)
            print(f"  PASS ({time.monotonic() - t:.2f} s)")
        except Skip as e:
            print(f"  SKIP: {e}")
        except Exception as e:  # report and continue with the next step
            failed += 1
            print(f"  FAIL: {type(e).__name__}: {e}")
    print("ALL PASS" if not failed else f"{failed} step(s) FAILED")
    return 1 if failed else 0


if __name__ == "__main__":
    main()
