"""protoemu bring-up on the Tiny Tapeout demo board (MicroPython, TT SDK v3).

Copy protoemu.py, protoemu_fw.py and this file to the board, then:

    import bringup
    pe = bringup.setup()          # select the project, 48 MHz, reset, check the ID
    bringup.uart_loopback(pe)     # no wiring: core 0 UART TX -> virtual pin 24 -> core 1 UART RX
    bringup.uart_tx(pe, b"hi\\r\\n")  # 115200 8N1 on uo0 (to a USB-UART's RX)

setup() needs the TT SDK; the other functions only need a ProtoEmu, so they
also run against the model (test/model/test_micropython_driver.py).
"""
import protoemu_fw as fw
from protoemu import ProtoEmu, A_ID

PROJECT = "tt_um_josusanmartin_protoemu"
ID = 0x50450002


def setup(clock_hz=48_000_000):
    """Select the project, clock it, reset it and check the chip ID."""
    import time
    from ttboard.demoboard import DemoBoard
    from ttboard.mode import RPMode
    tt = DemoBoard.get()
    getattr(tt.shuttle, PROJECT).enable()
    tt.mode = RPMode.ASIC_RP_CONTROL
    tt.uio_oe_pico.value = 0            # the board drives no uio pin: the chip and devices do
    tt.clock_project_PWM(clock_hz)
    tt.reset_project(True)
    time.sleep_ms(10)
    tt.reset_project(False)
    pe = ProtoEmu(tt)
    got = pe.read(A_ID)
    if got != ID:
        raise RuntimeError("ID %08x, expected %08x: wrong project, clock or SPI pins" % (got, ID))
    return pe


def load(pe, prog, start=True):
    pe.stop(1 << prog["core"])
    pe.load(**prog)
    if start:
        pe.start(1 << prog["core"])


def uart_loopback(pe, msg=b"Hello, protoemu!", timeout=2000):
    """Core 0 sends msg on virtual pin 24; core 1 receives it. Returns what came back."""
    pe.stop(0b11)
    pe.clear_fifos(0b11)
    pe.own_pins(0, [24])
    load(pe, fw.UART_RX_V24)
    load(pe, fw.UART_TX_V24)
    got = bytearray()
    for b in msg:
        pe.push(0, b)
    for _ in range(timeout):
        w = pe.pop(1)
        if w is not None:
            got.append(w & 0xFF)      # uart_rx right-aligns the byte
            if len(got) == len(msg):
                break
    pe.stop(0b11)
    if bytes(got) != msg:
        raise RuntimeError("loopback got %r" % bytes(got))
    return bytes(got)


def uart_tx(pe, data):
    """Send bytes at 115200 8N1 on uo0 (logical pin 8)."""
    pe.own_pins(0, [8])
    load(pe, fw.UART_TX)
    for b in data:
        pe.push(0, b)
