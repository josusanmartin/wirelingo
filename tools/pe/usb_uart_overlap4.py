"""Optional cooperative fixed4 pair; finite model scope in the workload guide.

Cold-only ordinary loader: two cores/banks; no rearm/service/FIFO relay API.
The external UART sender honors new READY and complete nominal 8N1 frames.
"""
from pathlib import Path
from . import asm, host, sim, usbenum
from .profile import TAPEOUT

FW = Path(__file__).resolve().parents[2] / "firmware"
CLOCK_HZ, UART_HZ, UART_DIVIDER = 48000000, 115200, 256
RX, READY, ERROR, MAILBOX = 16, 8, 9, 24
PRODUCER_CRCCTRL = 15 << 16  # mandatory16-bit scratch, BOTH taps off
COLD_WRITES = 374


def assemble():
    consumer = asm.assemble((FW / "usb_uart_overlap4.pasm").read_text(),
                            {"DP": 0, "DM": 1, "TURN": 63})
    producer = asm.assemble((FW / "uart_rx_overlap4.pasm").read_text(),
                            {"RX": RX, "READY": READY, "ERROR": ERROR})
    if [len(p.words) for p in (consumer, producer)] != [256, 93]:
        raise ValueError("overlap4 source images must be exactly256/93 words")
    if host.core_config(producer)["shiftctrl"] != 3:
        raise ValueError("overlap4 requires right shifts without autopush/autopull")
    return consumer, producer


def load_writes(programs=None):
    """374 paid cold writes; both stopped/empty before replacing ownership.

    All flags and virtual pins belong to this pair. Core1 CRC is scratch,
    not UART CRC. No register deposits or loading-host service follow start.
    """
    if (TAPEOUT.ncores, TAPEOUT.nbanks, TAPEOUT.bank_words, TAPEOUT.fifo_depth) != (2, 2, 256, 8):
        raise ValueError("overlap4 requires ordinary tapeout2/2/256/depth8")
    consumer, producer = assemble() if programs is None else programs
    writes = [(sim.A_CTRL, 0), (sim.A_FCLR, 255), (sim.A_SYNCBYP, 0),
              (sim.A_POUT, 0), (sim.A_POE, 0)]
    owners = {0: 0, 1: 0, RX: 1, READY: 1, ERROR: 1,
              **{pin: 1 for pin in range(24, 32)}}
    writes += host.pin_owner_writes(owners, profile=TAPEOUT)
    writes += host.load_program(0, consumer, 0, profile=TAPEOUT, in_pins=24, clkdiv=256)
    writes += host.load_program(1, producer, 256, profile=TAPEOUT, clkdiv=256)
    writes += [(sim.core_reg(0, sim.C_CRCCTRL), usbenum.CRCCTRL),
               (sim.core_reg(1, sim.C_CRCCTRL), PRODUCER_CRCCTRL),
               (sim.A_LINEDIV, (32 << 16) | (32 << 8)),
               (sim.A_LINEPINS, 0), (sim.A_LINECFG, usbenum.LINECFG)]
    writes += host.start_writes(3)
    if len(writes) != COLD_WRITES:
        raise ValueError("overlap4 cold plan differs from374 writes")
    return writes


def vendor_request():
    """One vendor/device IN request for exactly four bytes; no host data source."""
    return bytes.fromhex("c001000000000400")
