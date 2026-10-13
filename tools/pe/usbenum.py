"""Host side of the autonomous USB device (firmware/usb_enum.pasm), low or full speed.

The firmware answers every control request itself (docs/usb-enumeration.md),
so the host's whole job is the load: the program, CRCCTRL and the line unit,
then the start. Afterwards it may read the outcome (`state`), but nothing it
does during enumeration matters, and the tests check it does nothing.

    writes = usbenum.load_writes(usbenum.assemble("full"), "full")   # (addr, data) list

Wiring: the line unit's pin (DP) is uio0 and the next pin (DM) uio1. Low
speed: D+ on uio0, D- on uio1, the 1.5k pull-up on D-. Full speed: D- on uio0,
D+ on uio1, the pull-up on D+. Either way J (idle) is uio1 high, uio0 low.
"""
import pathlib

from . import asm, host, sim, usb

FW = pathlib.Path(__file__).resolve().parents[2] / "firmware" / "usb_enum.pasm"
CLOCKS_PER_BIT = {"low": 32, "full": 4}     # at 48 MHz: 1.5 and 12 Mbit/s
TURN = {"low": 63, "full": 4}               # response delay after the host's EOP (ticks)
LINECFG = 0x49F3                  # EN NRZI STUFF_K=6 ONES DIFF END=SE0 END_BITS=2 RX_EN
LINEPINS = 0                      # TXP = RXP = uio0, TXN = RXN = uio1
CRCCTRL = 0x8005 | (15 << 16) | (1 << 20) | (1 << 21)   # CRC16, IN and OUT taps
FLAGS = dict(F_STALL=0, F_ADDR=1, F_SENT=2, F_EXP=3, F_SETUP=4, F_CFG=5)


def assemble(speed="low", text=None):
    return asm.assemble(FW.read_text() if text is None else text,
                        {"DP": 0, "DM": 1, "TURN": TURN[speed]})


def load_writes(prog, speed="low", start=True):
    """Every register write the host makes: load core 0 (at 0: the firmware
    jumps to absolute table addresses), configure the CRC and line unit, start.
    The chip runs at 48 MHz."""
    if prog.origin != 0:
        raise ValueError("usb_enum must load at address 0")
    n = CLOCKS_PER_BIT[speed]
    w = host.load_program(0, prog, 0, clkdiv=1 << 8)
    w += [(sim.core_reg(0, sim.C_CRCCTRL), CRCCTRL),
          (sim.A_LINEDIV, (n << 16) | (n << 8)), (sim.A_LINEPINS, LINEPINS), (sim.A_LINECFG, LINECFG)]
    if start:
        w += host.start_writes(1)
    return w


def table(prog, label):
    """The descriptor bytes in the table entry pairs (`ldi r1, x` / `call send2`)
    from `label` up to the next label."""
    out, pc = bytearray(), prog.labels[label]
    send2, starts = prog.labels["send2"], set(prog.labels.values())
    while pc + 1 < len(prog.words) and (pc == prog.labels[label] or pc not in starts):
        ldi, call = prog.words[pc], prog.words[pc + 1]
        if ldi >> 27 != 8 or (ldi >> 16) & 3 != 1 or call >> 27 != 13 or call & 0x3FF != send2:
            break
        out += (ldi & 0xFFFF).to_bytes(2, "little")
        pc += 2
    return bytes(out)


def address_of(filt):
    """The device address a token filter (r3) accepts."""
    return usb.rev(filt >> 9, 7)


def state(read):
    """read(addr) -> word. The device's address and whether it is configured."""
    return decode_state(read(sim.A_FSET), read(sim.core_reg(0, sim.C_R0 + 3)))


def decode_state(flags, filt):
    """From the FLAGS register and core 0's r3."""
    return dict(address=address_of(filt),
                configured=bool(flags >> FLAGS["F_CFG"] & 1),
                pending_address=bool(flags >> FLAGS["F_ADDR"] & 1),
                stalled=bool(flags >> FLAGS["F_STALL"] & 1))
