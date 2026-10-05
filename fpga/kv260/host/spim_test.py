#!/usr/bin/env python3
"""Host-port tests through the fabric SPI master (pe_spim) on the KV260:

    sudo python3 fpga/kv260/host/spim_test.py [--divs 4 2 1] [--clocks shared alt] [--words 4096]

For each master clock (shared = protoemu's clock, alt = the 45.93 MHz MMCM
clock, not phase-aligned with it), SCK divider and mode (1-bit, quad):
register-file round trips cross-checked over the GPIO path, a long
WRITE_FIXED burst into core 0's TX FIFO (host/sink.pasm sums and counts the
words; the sums are read back over the GPIO path), and long READ_FIXED bursts
from core 1's RX FIFO (host/source.pasm pushes a known sequence; every word
is checked, across bursts). Throughput is payload bits over the CS_n-low time
counted in protoemu clocks (PCYC), converted with the measured clock.
"""
import argparse
import random
import sys
import time

from pe_kria import ROOT, Board, ID
from pe_spim import Spim
from pe import asm, host, sim  # noqa: E402

HOST = ROOT / "fpga" / "kv260" / "host"
R = lambda c, r: sim.core_reg(c, sim.C_R0 + r)
FIFO = lambda c: sim.core_reg(c, sim.C_FIFO)
STATUS = lambda c: sim.core_reg(c, sim.C_STATUS)
SRC_R1 = 0x8000


def source_word(k):
    return ((k & 0xFFFF) << 16) | ((SRC_R1 + 3 * k) & 0xFFFF)


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)


class Ctx:
    def __init__(self, board, spim, f_clk, log=print):
        self.board, self.spim, self.f_clk, self.log = board, spim, f_clk, log
        self.gpio = board.protoemu()           # GPIO path (no transport)
        self.fab = board.protoemu()
        self.fab.transport = spim
        self.rng = random.Random(7)
        self.results = []

    def via_gpio(self, fn):
        """Run fn(pe) over the GPIO path, then give the pins back to the master."""
        sel = self.spim.mem.rd(0x800C)
        self.spim.select(fabric=False)
        try:
            return fn(self.gpio)
        finally:
            self.spim.mem.wr(0x800C, sel)

    def mbps(self, nwords, pcyc):
        return 32 * nwords * self.f_clk / pcyc / 1e6


def load(pe, name, core, **cfg):
    prog = asm.assemble((HOST / f"{name}.pasm").read_text(), {})
    for addr, words in host.coalesce(host.load_program(core, prog, core * 256, **cfg)):
        pe.write(addr, words)


def t_regs(ctx, rounds):
    fab, rng = ctx.fab, ctx.rng
    for k in range(rounds):
        vals = [rng.getrandbits(16) for _ in range(8)]
        fab.write(R(0, 0), vals[:4])
        fab.write(R(1, 0), vals[4:])
        back = fab.read(R(0, 0), 4) + fab.read(R(1, 0), 4)
        check(back == vals, f"fabric readback {back} != {vals}")
        if k % 10 == 0:                      # cross-check over the GPIO path, both ways
            g = ctx.via_gpio(lambda pe: [pe.read(R(c, r)) for c in range(2) for r in range(4)])
            check(g == vals, f"GPIO readback {g} != fabric-written {vals}")
            vals = [rng.getrandbits(16) for _ in range(8)]
            ctx.via_gpio(lambda pe: [pe.write(R(c, r), vals[4 * c + r]) for c in range(2) for r in range(4)])
            back = fab.read(R(0, 0), 4) + fab.read(R(1, 0), 4)
            check(back == vals, f"fabric readback {back} != GPIO-written {vals}")
    ids = fab.read(sim.A_ID, 64, fixed=True)
    check(ids == [ID] * 64, "READ_FIXED of ID")
    return f"{rounds} rounds R0..R3 x2 cores, GPIO cross-checks; 64x READ_FIXED ID ok"


def t_tx_burst(ctx, nwords):
    fab, spim = ctx.fab, ctx.spim
    fab.stop(0b11)
    fab.clear_fifos(0b11)
    load(fab, "sink", 0)                       # IMEM written through the fabric master
    fab.write(R(0, 0), [0, 0, 0])
    fab.start(0b01)
    words = [ctx.rng.getrandbits(32) for _ in range(nwords)]
    fab.write(FIFO(0), words, fixed=True)
    pcyc = spim.last_pcyc
    t = time.monotonic()
    while fab.status(0) & 15 and time.monotonic() - t < 1:
        pass
    total, count = ctx.via_gpio(lambda pe: (pe.read(R(0, 0)), pe.read(R(0, 2))))
    fab.stop(0b01)
    exp = sum((w >> 16) + (w & 0xFFFF) for w in words) & 0xFFFF
    check(count == nwords & 0xFFFF, f"sink counted {count} words, sent {nwords}")
    check(total == exp, f"sink sum 0x{total:04X}, expected 0x{exp:04X}")
    return pcyc, f"WRITE_FIXED {nwords} words to TXF0: sink count {count}, sum 0x{total:04X} ok (GPIO readback)"


def t_rx_burst(ctx, sizes):
    fab, spim = ctx.fab, ctx.spim
    fab.stop(0b11)
    fab.clear_fifos(0b11)
    load(fab, "source", 1)
    fab.write(R(1, 0), [0, SRC_R1])
    fab.start(0b10)
    time.sleep(0.001)
    k, pcyc = 0, None
    for n in sizes:
        got = fab.read(FIFO(1), n, fixed=True)
        got = [got] if n == 1 else got
        exp = [source_word(k + i) for i in range(n)]
        if got != exp:
            bad = next(i for i in range(n) if got[i] != exp[i])
            raise AssertionError(f"RX word {k + bad}: 0x{got[bad]:08X}, expected 0x{exp[bad]:08X}")
        if n == max(sizes):
            pcyc = spim.last_pcyc
        k += n
    fab.stop(0b10)
    nxt = ctx.via_gpio(lambda pe: pe.read(FIFO(1), fixed=True))
    check(nxt == source_word(k), f"next word over GPIO 0x{nxt:08X}, expected 0x{source_word(k):08X}")
    return pcyc, f"READ_FIXED bursts {sizes} from RXF1: {k} words in sequence, next word over GPIO ok"


def t_short_write(ctx, nwords=64):
    """The spec's simulated figure: 64 words in one quad WRITE_FIXED at SCK = clk."""
    fab = ctx.fab
    fab.stop(0b11)
    fab.clear_fifos(0b11)
    load(fab, "sink", 0)
    fab.write(R(0, 0), [0, 0, 0])
    fab.start(0b01)
    words = [ctx.rng.getrandbits(32) for _ in range(nwords)]
    fab.write(FIFO(0), words, fixed=True)
    pcyc = ctx.spim.last_pcyc
    while fab.status(0) & 15:
        pass
    count = fab.read(R(0, 2))
    fab.stop(0b01)
    check(count == nwords, f"sink counted {count}")
    return pcyc


def run(ctx, clocks, divs, words, regs_rounds, rx_sizes):
    spim = ctx.spim
    for clock in clocks:
        for div in divs:
            for quad in (False, True):
                spim.select(fabric=True, alt=(clock == "alt"))
                spim.div = div
                spim.enable_quad(quad)
                name = f"{clock:6s} SCK=mclk/{div} {'quad ' if quad else '1-bit'}"
                try:
                    check(ctx.fab.read(sim.A_ID) == ID, "ID")
                    ctx.log(f"{name}: {t_regs(ctx, regs_rounds)}")
                    ptx, tx = t_tx_burst(ctx, words)
                    ctx.log(f"{name}: {tx}")
                    prx, rx = t_rx_burst(ctx, rx_sizes)
                    ctx.log(f"{name}: {rx}")
                    short = t_short_write(ctx) if quad and div == 1 else None
                    line = (f"{name}: PASS  write {ctx.mbps(words, ptx):6.1f} Mbit/s ({ptx} clk), "
                            f"read {ctx.mbps(max(rx_sizes), prx):6.1f} Mbit/s ({prx} clk)")
                    if short:
                        line += f", 64-word write {ctx.mbps(64, short):.1f} Mbit/s ({short} clk)"
                    ctx.log(line)
                    ctx.results.append((name, True))
                except Exception as e:
                    ctx.log(f"{name}: FAIL {type(e).__name__}: {e}")
                    ctx.results.append((name, False))
                finally:
                    spim.enable_quad(False)
    spim.select(fabric=False)
    return all(ok for _, ok in ctx.results)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clocks", nargs="*", default=["shared", "alt"])
    ap.add_argument("--divs", nargs="*", type=int, default=[4, 2, 1])
    ap.add_argument("--words", type=int, default=4096)
    ap.add_argument("--regs", type=int, default=50)
    a = ap.parse_args()
    board = Board()
    spim = Spim()
    spim.select(fabric=False)
    board.reset()
    f = board.clk_hz()
    print(f"protoemu clk {f / 1e6:.4f} MHz (cycle counter); ID over GPIO 0x{board.protoemu().read(0x800):08X}")
    ctx = Ctx(board, spim, f)
    ok = run(ctx, a.clocks, a.divs, a.words, a.regs, [a.words, 1, 7, 1000, 64])
    print("ALL PASS" if ok else "FAILURES")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
