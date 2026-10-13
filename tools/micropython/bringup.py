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
from protoemu import ProtoEmu, A_ID, A_FSET, A_FCLR, C_R0, ST_HALTED, ST_FAULT

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
    tt.reset_project(True)
    tt.clock_project_PWM(clock_hz)
    actual = tt.auto_clocking_freq
    if actual != clock_hz:
        raise RuntimeError("project clock %r Hz, requested %r Hz" % (actual, clock_hz))
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
    """Core 0 sends to core 1 over virtual pin 24; drain RX while feeding TX.
    The transport must service RX within its eight-byte buffering window.
    timeout bounds service iterations; both cores stop even on failure.
    """
    msg = bytes(msg)
    pe.stop(0b11)
    pe.clear_fifos(0b11)
    pe.own_pins(0, [24])
    load(pe, fw.UART_RX_V24)
    load(pe, fw.UART_TX_V24)
    got, sent = bytearray(), 0
    try:
        for _ in range(timeout):
            got.extend(w & 0xFF for w in pe.pop_all(1))
            if len(got) >= len(msg):
                break
            rx = pe.status(1)
            tx = pe.status(0)
            if (rx | tx) & (ST_HALTED | ST_FAULT):
                raise RuntimeError("UART loopback core halted or faulted")
            if sent < len(msg) and (tx & 15) < pe.depth:
                pe.push(0, msg[sent])
                sent += 1
    finally:
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


def usb_setup(pe, variant="line"):
    """48 MHz, D+ on uio0, D- on uio1 with the external low-speed pull-up.
    Returns the endpoint-zero service: call sw.service() repeatedly.
    USB owns flags 0..6. The slow bit-bang transport is a bring-up path;
    sustained host service must be measured on the selected board transport.
    """
    import usb_device
    if variant not in ("line", "two-core"):
        raise ValueError("USB variant must be line or two-core")
    pe.stop(3)
    pe.clear_fifos(3)
    pe.write(usb_device.sim.A_FCLR, 0x7F)
    rx = fw.USB_LINE if variant == "line" else fw.USB_RX
    for image in [rx] if variant == "line" else [rx, fw.USB_TX]:
        pe.load(**{k: v for k, v in image.items() if k != "filter_index"})
    tx = 0 if variant == "line" else 1
    pe.own_pins(tx, [0, 1])
    pe.write(pe.core_reg(0, 6), usb_device.CRCCTRL)
    if variant == "line":
        pe.write(usb_device.sim.A_LINEDIV, (32 << 16) | (32 << 8))
        pe.write(usb_device.sim.A_LINEPINS, 0)
        pe.write(usb_device.sim.A_LINECFG, 0x49F3)
    pe.start(1 if variant == "line" else 3)
    return usb_device.DeviceSoftware(pe, rx, usb_device.DESCRIPTORS, tx_core=tx)


def usb_enum(pe, speed="low"):
    """The autonomous USB device: after this load the chip enumerates on its own,
    with no further host-port traffic. 48 MHz project clock. Low speed: D+ on
    uio0, D- on uio1, 1.5k from D- to 3.3 V. Full speed: D- on uio0, D+ on
    uio1, 1.5k from D+ to 3.3 V. 27 ohm in series with each line. Connect the
    pull-up (or plug the cable in) after this call returns; read the outcome
    with usb_enum_state(pe).
    """
    image = {"low": fw.USB_ENUM_LOW, "full": fw.USB_ENUM_FULL}.get(speed)
    if image is None:
        raise ValueError("USB speed must be low or full")
    pe.stop(3)
    pe.clear_fifos(3)
    pe.write(A_FCLR, 0x7F)
    for addr, words in image["writes"]:
        pe.write(addr, words)
    pe.start(1)


def usb_enum_state(pe):
    """The device's address and whether it is configured (pe.usbenum.state)."""
    flags, filt = pe.read(A_FSET), pe.read(pe.core_reg(0, C_R0 + 3))
    a, addr = filt >> 9, 0
    for i in range(7):                  # the token filter holds the address bit-reversed
        addr |= ((a >> i) & 1) << (6 - i)
    return dict(address=addr, configured=bool(flags >> 5 & 1),
                pending_address=bool(flags >> 1 & 1), stalled=bool(flags & 1))


def eth10_receive(pe, timeout_us=100000, max_frames=1):
    """Finite RX capture at 50 MHz, comparator output on ui0; returns frames
    with a good FCS. Uses core 0 and the line unit; reception stops before
    the frames are decoded.
    """
    import eth10_rx
    return eth10_rx.Capture(pe).capture(timeout_us, max_frames)
