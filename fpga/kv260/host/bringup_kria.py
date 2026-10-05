#!/usr/bin/env python3
"""protoemu hardware bring-up on the KV260 (run as root on the Kria with the
protoemu app loaded):

    sudo python3 fpga/kv260/host/bringup_kria.py [--only NAME ...] [--model]

Programs are assembled from firmware/*.pasm with tools/pe and loaded through
the bit-banged SPI host port (tools/micropython/protoemu.py over pe_kria.Board).
--model runs the same steps against the cycle model (tools/pe/modelhost.py)
through the pin-level SPI port model of test/model/test_micropython_driver.py,
skipping the wall-clock UART and quad steps; it checks this script, not the FPGA.
"""
import argparse
import random
import sys
import time

from pe_kria import ROOT, Board, QuadTransport, ID

from pe import asm, host, sim   # noqa: E402  (pe_kria put tools/ on sys.path)

FW = ROOT / "firmware"
P_UO0, P_UI0, P_V24 = 8, 16, 24


def load(pe, name, core, defines=None, **cfg):
    """Assemble firmware/<name>.pasm and load it into core's bank (core stopped)."""
    prog = asm.assemble((FW / f"{name}.pasm").read_text(), defines or {})
    writes = host.load_program(core, prog, core * 256, **cfg)
    for addr, words in host.coalesce(writes):
        pe.write(addr, words)
    return len(prog.words)


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)


# ---- steps -------------------------------------------------------------------

def step_clock(ctx):
    hz = ctx.board.clk_hz()
    ctx.f_clk = hz
    print(f"  protoemu clk measured {hz / 1e6:.4f} MHz")
    check(abs(hz - ctx.f_nominal) < 0.01 * ctx.f_nominal, "clock off by more than 1 %")


def step_id(ctx):
    ctx.board.reset()
    got = ctx.pe.read(sim.A_ID)
    print(f"  ID (0x800) = 0x{got:08X}")
    check(got == ID, f"wrong ID 0x{got:08X}")


def step_regs(ctx, n=200):
    pe, rng = ctx.pe, random.Random(1)
    regs = [sim.core_reg(c, sim.C_R0 + r) for c in range(2) for r in range(4)]
    for _ in range(n):
        vals = [rng.getrandbits(16) for _ in regs]    # R0..R3 are 16-bit
        for a, v in zip(regs, vals):
            pe.write(a, v)
        back = [pe.read(a) for a in regs]
        check(back == vals, f"register readback {back} != {vals}")
        burst = pe.read(regs[0], 4)                   # incrementing read
        check(burst == vals[:4], f"burst readback {burst} != {vals[:4]}")
    print(f"  {n} rounds: R0..R3 of both cores written and read back, single and burst")


def step_pins(ctx):
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
    """core 0 UART TX -> virtual pin 24 -> core 1 UART RX, 115200 8N1."""
    pe = ctx.pe
    div = uart_cfg(ctx, 115200)
    pe.stop(0b11)
    pe.clear_fifos(0b11)
    pe.own_pins(0, [P_V24])
    n_rx = load(pe, "uart_rx", 1, {"RX": P_V24}, in_pins=P_V24, clkdiv=div)
    n_tx = load(pe, "uart_tx", 0, out_pins=(P_V24, 1), side_pins=P_V24, clkdiv=div)
    pe.start(0b10)
    pe.start(0b01)
    got, sent = bytearray(), 0
    try:
        for _ in range(20 * len(msg)):
            got.extend(w & 0xFF for w in pe.pop_all(1))
            if len(got) >= len(msg):
                break
            tx = pe.status(0)
            check(not (tx | pe.status(1)) & (protoemu_st()), "core halted or faulted")
            if sent < len(msg) and (tx & 15) < pe.depth:
                pe.write(sim.core_reg(0, sim.C_FIFO), msg[sent], fixed=True)
                sent += 1
    finally:
        pe.stop(0b11)
    print(f"  loaded uart_tx ({n_tx} words, core 0) and uart_rx ({n_rx} words, core 1)")
    print(f"  sent {msg!r}\n  got  {bytes(got)!r}")
    check(bytes(got) == msg, "loopback mismatch")
    check(not pe.read(sim.A_FSET) & 1, "framing error flag F0 set")


def protoemu_st():
    import protoemu
    return protoemu.ST_HALTED | protoemu.ST_FAULT


def step_uart_tx_pin(ctx, msg=b"protoemu", baud=9600):
    """core 0 UART TX on uo[0] (P8) at 9600 baud, decoded by polling the GPIO."""
    pe, b = ctx.pe, ctx.board
    pe.stop(0b11)
    pe.clear_fifos(0b11)
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
    pe, b = ctx.pe, ctx.board
    pe.stop(0b11)
    pe.clear_fifos(0b11)
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
    pe, rng = ctx.pe, random.Random(2)
    q = QuadTransport(ctx.board)
    q.enable(pe)
    try:
        got = pe.read(sim.A_ID)
        print(f"  quad: ID = 0x{got:08X}")
        check(got == ID, "quad ID")
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


STEPS = [("clock", step_clock), ("id", step_id), ("regs", step_regs), ("pins", step_pins),
         ("loopback", step_loopback), ("uart_tx_pin", step_uart_tx_pin),
         ("uart_rx_pin", step_uart_rx_pin), ("quad", step_quad)]
WALL_CLOCK = {"clock", "uart_tx_pin", "uart_rx_pin", "quad"}


class ModelRegs:
    """pe_kria.DevMem stand-in: the cycle model behind the pin-level SPI port model."""

    def __init__(self):
        sys.path.insert(0, str(ROOT / "test" / "model"))
        from pe.modelhost import ModelHost
        from test_micropython_driver import SpiPortModel
        self.mh = ModelHost()
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
    a = ap.parse_args()
    ctx = Ctx()
    ctx.board = Board(ModelRegs() if a.model else None, settle=a.settle)
    ctx.pe = ctx.board.protoemu()
    ctx.f_nominal = ctx.f_clk = a.mhz * 1e6
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
        except Exception as e:  # report and continue with the next step
            failed += 1
            print(f"  FAIL: {type(e).__name__}: {e}")
    print("ALL PASS" if not failed else f"{failed} step(s) FAILED")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
