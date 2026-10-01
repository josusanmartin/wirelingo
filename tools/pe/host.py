"""Host-side helpers: turn programs + pin assignments into register writes.

Everything here is transport-agnostic: functions return lists of (addr, data)
register writes that can be sent over SPI (cocotb, RP2350 MicroPython) or
applied directly to the model.
"""
from . import isa, sim

CMD_WRITE, CMD_READ, CMD_WRITE_FIXED, CMD_READ_FIXED = 0x02, 0x03, 0x0A, 0x0B
QUAD = 0x40        # cmd | QUAD: address and data 4 bits per SCK (HOSTCFG.QUAD_EN)
READ_DUMMY = 1     # dummy bytes (8 SCK) between a read's address and its data (1-bit)


def spi_frame(cmd, addr, words=(), nread=0):
    """Bytes for one 1-bit SPI transaction. Reads get the dummy byte and
    4*nread zero bytes; their data starts at byte 3 + READ_DUMMY."""
    b = bytearray([cmd, (addr >> 8) & 0xFF, addr & 0xFF])
    for w in words:
        b += w.to_bytes(4, "big")
    if cmd & 1:
        b += bytes(READ_DUMMY + 4 * nread)
    return bytes(b)


def clkdiv_for(f_clk, f_tick):
    """CLKDIV value giving f_tick ticks/s from f_clk."""
    i, f = isa.clkdiv_fields(f_clk / f_tick)
    return (i << 8) | f


def core_config(prog, *, out_pins=None, set_pins=None, side_pins=None, in_pins=None,
                shift_out=None, shift_in=None, clkdiv=None):
    """Compute CLKDIV/PINCTRL0/PINCTRL1/SHIFTCTRL values for a program.
    Keyword args override the program's own directives."""
    cfg = dict(prog.config)
    for k, val in (("out_pins", out_pins), ("set_pins", set_pins), ("side_pins", side_pins),
                   ("in_pins", in_pins), ("shift_out", shift_out), ("shift_in", shift_in)):
        if val is not None:
            cfg[k] = val
    ob, oc = cfg.get("out_pins", (0, 0))
    sb, scnt = cfg.get("set_pins", (0, 0))
    pinctrl0 = (ob & 31) | ((oc & 63) << 8) | ((sb & 31) << 16) | ((scnt & 7) << 24)
    sideb = cfg.get("side_pins", 0)
    inb = cfg.get("in_pins", 0)
    pinctrl1 = ((sideb & 31) | (prog.side_count << 8) | (prog.side_opt << 12)
                | (prog.side_pindir << 13) | ((inb & 31) << 16))
    osr_right, autopull, pull_th = cfg.get("shift_out", (False, False, 32))
    isr_right, autopush, push_th = cfg.get("shift_in", (False, False, 32))
    shiftctrl = (int(osr_right) | (int(isr_right) << 1) | (int(autopull) << 2) | (int(autopush) << 3)
                 | (int(cfg.get("fjoin", False)) << 4) | ((pull_th & 31) << 8) | ((push_th & 31) << 16))
    if clkdiv is None:
        i, f = cfg.get("clkdiv", (1, 0))
        clkdiv = (i << 8) | f
    return dict(clkdiv=clkdiv, pinctrl0=pinctrl0, pinctrl1=pinctrl1, shiftctrl=shiftctrl)


def load_program(core, prog, base=None, **cfg_kw):
    """Register writes to load `prog` into IMEM at `base` and configure `core`.
    The core must be disabled. Doesn't enable it."""
    if base is None:
        base = prog.origin if prog.origin is not None else core * 256
    words = prog.relocate(base)
    w = [(base + i, word) for i, word in enumerate(words)]
    c = core_config(prog, **cfg_kw)
    reg = lambda off: sim.core_reg(core, off)
    w += [
        (reg(sim.C_CLKDIV), c["clkdiv"]),
        (reg(sim.C_PINCTRL0), c["pinctrl0"]),
        (reg(sim.C_PINCTRL1), c["pinctrl1"]),
        (reg(sim.C_SHIFTCTRL), c["shiftctrl"]),
        (reg(sim.C_ENTRY), base),
        (reg(sim.C_WRAP), (base + prog.wrap_bottom) | ((base + prog.wrap_top) << 16)),
    ]
    return w


def pin_owner_writes(owners):
    """owners: dict pin -> core. Returns writes for PIN_OWNER_LO/HI (others -> core 0)."""
    lo = hi = 0
    for p, c in owners.items():
        if p < 16:
            lo |= c << (2 * p)
        else:
            hi |= c << (2 * (p - 16))
    return [(sim.A_POWN_LO, lo), (sim.A_POWN_HI, hi)]


def start_writes(mask, enabled_before=0):
    """Restart the cores in `mask` and enable them (plus already-enabled ones)."""
    return [(sim.A_CTRL, enabled_before | mask | (mask << 8))]


def coalesce(writes):
    """Group consecutive-address writes into (addr, [words]) bursts."""
    bursts = []
    for a, d in writes:
        if bursts and bursts[-1][0] + len(bursts[-1][1]) == a:
            bursts[-1][1].append(d)
        else:
            bursts.append((a, [d]))
    return bursts
