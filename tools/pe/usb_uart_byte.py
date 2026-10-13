"""Cold-load helper for the optional one-byte UART -> low-speed USB mailbox.

USB EP0 is autonomous after start; the UART sender must honor active-high
READY. No device-side service/rearm API exists. Existing enumeration modes
are separate programs. This helper does not generate embedded bundles.
"""
from pathlib import Path
from . import asm, host, sim, usbenum
from .profile import TAPEOUT

FW = Path(__file__).resolve().parents[2] / "firmware"
UART_HZ = 115200
CLOCK_HZ = 48000000
RX, READY, MAILBOX = 16, 8, 24
FRAMING_ERROR, OCCUPIED = 6, 7
UART_DIVIDER = host.clkdiv_for(CLOCK_HZ, 8 * UART_HZ)


def assemble():
    usb = asm.assemble((FW / "usb_uart_byte.pasm").read_text(),
                       {"DP": 0, "DM": 1, "TURN": 63})
    uart = asm.assemble((FW / "uart_rx_mailbox.pasm").read_text(),
                        {"RX": RX, "READY": READY})
    if len(usb.words) > 256 or len(uart.words) > 32:
        raise ValueError("optional mailbox image exceeds its declared bank budget")
    return usb, uart


def load_writes(programs=None):
    """Cold setup only: disable, clear flags/outputs, assign owners, load/start.

    Applying this list replaces both cores and consumes all virtual pins and
    flags. Call only for this optional workload, with cores disabled. After
    start, the peer uses USB packets and physical READY/RX, not loading APIs.
    """
    usb, uart = assemble() if programs is None else programs
    writes = [(sim.A_CTRL, 0), (sim.A_FCLR, 0xff), (sim.A_SYNCBYP, 0),
              (sim.A_POUT, 0), (sim.A_POE, 0)]
    owners = {RX: 1, READY: 1, **{p: 1 for p in range(24, 32)}}
    writes += host.pin_owner_writes(owners, profile=TAPEOUT)
    writes += host.load_program(0, usb, 0, profile=TAPEOUT,
                                in_pins=MAILBOX, clkdiv=1 << 8)
    writes += host.load_program(1, uart, 256, profile=TAPEOUT,
                                clkdiv=UART_DIVIDER)
    writes += [(sim.core_reg(0, sim.C_CRCCTRL), usbenum.CRCCTRL),
               (sim.A_LINEDIV, (32 << 16) | (32 << 8)),
               (sim.A_LINEPINS, 0), (sim.A_LINECFG, usbenum.LINECFG)]
    writes += host.start_writes(3)
    return writes


def vendor_request(wlength=1):
    """Application request: vendor/device IN1, wValue=0/wIndex=0/wLength."""
    if not isinstance(wlength, int) or not 0 <= wlength <= 65535:
        raise ValueError("wLength must fit16 bits")
    return bytes((0xc0, 1, 0, 0, 0, 0, wlength & 255, wlength >> 8))
