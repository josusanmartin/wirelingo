"""Match, then answer: firmware/eth10_reply.pasm on core 0, no host after loading
(docs/eth10-reaction.md).

The reply is a fixed 64-byte frame built into the program as 32 sixteen-bit
immediates. The chip has no CRC-32, so this module computes the FCS once, here,
and the frame can't depend on the one it answers.

    w = ethreply.load_writes(50e6, threshold=1000)      # register writes, reset to running
    raw = ethreply.reply_frame()                        # what goes out on TD

PC only (it assembles firmware).
"""
import pathlib

from . import asm, eth, host, sim

FW = pathlib.Path(__file__).resolve().parents[2] / "firmware" / "eth10_reply.pasm"

TD_PIN, RXP, HIT = 8, 16, 10          # TD+ = uo0 (TD- = uo1), RXP = ui0, HIT = uo2
CHIP_MAC = eth.mac("02:57:4c:00:00:02")
CHIP_IP = eth.ip("10.77.0.2")
BCAST_IP = eth.ip("10.77.0.255")
PORT = 7777
REPLY_BYTES = 64                      # the firmware holds exactly 32 chunks
END_BITS = 3                          # TP_IDL: 3 bit times high after the last bit (a 0)
LINECFG = 1 | (2 << 1) | (1 << 7) | (2 << 8) | (END_BITS << 10) | (1 << 14)  # EN Manchester DIFF END=2 RX_EN
IFG_BITS = 96
QUIET_BITS = 40                       # after the last chunk: the ~35 bit times of our own frame left, + 5
# The window's wait counts IFG_TICKS = 96 bit times - WINDOW_TRIM; the trim is the
# rest of the path from the far end's last edge to our first: 5 instruction ticks
# outside the count (the `ifg` deadline in the firmware), 1 clock from the cycle
# `out line` completes in to the clock edge that changes TD, and the pad
# synchronizer's 2-clock minimum. So the first edge comes 0 to 1 clock after the 96
# bit times (docs/eth10-reaction.md).
WINDOW_TRIM = 8
PATH_TICKS = 5                        # the ifg deadline minus IFG_TICKS
OUT_CLOCKS = 1                        # `out line` completing (its cycle's clock edge) to TD changing
SYNC_CLOCKS = (2, 3)                  # a pad edge to the cycle a core sees it: [2, 3) clocks


def clocks_per_bit(clock_hz):
    if clock_hz == 50e6:
        return 5
    if clock_hz == 40e6:
        return 4
    raise ValueError("10BASE-T reply needs a 40 or 50 MHz project clock")


def line_div(clock_hz):
    """LINEDIV: RX clocks per bit in [31:16], TX clocks per half bit as INT.FRAC."""
    p = clocks_per_bit(clock_hz)
    return (p << 16) | (2 << 8) | (128 if p == 5 else 0)


def reply_frame(dst=eth.BROADCAST, src=CHIP_MAC, data=b"wirelingo matched!", ip_src=CHIP_IP,
                ip_dst=BCAST_IP, port=PORT):
    """A 64-byte UDP/IPv4 frame whose last bit on the wire is 0: the IP
    identification is the first value from 0 up that makes the FCS end that way."""
    for ident in range(1 << 16):
        raw = eth.frame(dst, src, eth.ETH_IP, eth.ipv4(ip_src, ip_dst, eth.PROTO_UDP,
                                                     eth.udp(ip_src, ip_dst, port, port, data), ident=ident))
        if len(raw) != REPLY_BYTES:
            raise ValueError("the reply must be exactly 64 bytes: 18 bytes of UDP data")
        if not raw[-1] >> 7:
            return raw
    raise ValueError("no IP identification gives an FCS ending in 0")


def chunks(raw):
    """The 32 sixteen-bit immediates: byte 2k low, byte 2k+1 high (LSB first on the wire)."""
    raw = bytes(raw)
    if len(raw) != REPLY_BYTES:
        raise ValueError("the reply must be 64 bytes, destination MAC through FCS")
    if not eth.fcs_ok(raw):
        raise ValueError("the reply's FCS is wrong")
    if raw[-1] >> 7:
        raise ValueError("the reply's last bit must be 0 (TP_IDL is 3 bits after a 0)")
    return [raw[2 * k] | (raw[2 * k + 1] << 8) for k in range(REPLY_BYTES // 2)]


def defines(clock_hz, raw=None):
    p = clocks_per_bit(clock_hz)
    d = {f"C{k}": c for k, c in enumerate(chunks(reply_frame() if raw is None else raw))}
    d["IFG_TICKS"] = IFG_BITS * p - WINDOW_TRIM
    d["QUIET_TICKS"] = QUIET_BITS * p
    return d


def program(clock_hz, raw=None, text=None):
    return asm.assemble(FW.read_text() if text is None else text, defines(clock_hz, raw))


def load_writes(clock_hz, threshold, raw=None, text=None):
    """Every register write from reset to core 0 running. The threshold is the
    word the firmware pulls first (only its low 16 bits count)."""
    prog = program(clock_hz, raw, text)
    w = [(sim.A_CTRL, 3 << 16), (sim.A_LINECFG, 0)]
    w += host.load_program(0, prog, 0, clkdiv=1 << 8)
    w += [(sim.A_LINEDIV, line_div(clock_hz)), (sim.A_LINEPINS, TD_PIN | (RXP << 8)), (sim.A_LINECFG, LINECFG)]
    w += [(sim.core_reg(0, sim.C_FIFO), threshold & 0xFFFFFFFF)]
    w += [(sim.A_CTRL, 1 | (1 << 8))]
    return w
