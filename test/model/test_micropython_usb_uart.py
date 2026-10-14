"""The UART -> USB pairs from the demo board, end to end on the cycle model.

The board driver writes the whole ui_in byte, so ui0 (the helpers' default
UART RX, logical pin 16) is held low by the RP2350 and a board user cannot
wire a UART there. The pairs' RX is an assembler define, so the bundle's
images take RX on uio6 (logical pin 6, the board's UART capture pin) and
keep READY on uo0 and ERROR on uo1; USB stays on uio0/uio1.

Each scenario runs tools/micropython (protoemu.py, bringup.py and the
generated protoemu_fw.py) unmodified through FakeTT/SpiPortModel:
bringup.usb_uart() cold-loads the pair over the bit-banged host port; then,
with the host transport refused (TransportIdle) and the driver's pins
untouched, a pins-only 115200-baud 8N1 sender on uio6 starts one frame per
fresh READY rise (it sees nothing but uo0), pe.usbhost.ControlHost
enumerates the device on D+/D-, and the PC reader pe.usb_uart_reports reads
the reports through a pyusb-shaped ctrl_transfer(). Model evidence only:
none of this has run on a demo board or a PC.
"""
import array
import errno

import pytest

from pe import host, sim, usb_uart_byte, usb_uart_envelope3, usb_uart_overlap4, usb_uart_reports as rep
from pe.usbhost import ControlHost, Stall
from test_micropython_driver import FakeTT, _TapeoutModelHost, protoemu, protoemu_fw, bringup
from test_usb_autonomous import TransportIdle, enumerate_flow
from test_usb_uart_reports import FakeUsbError
from usb_uart_pairs import Sender

CLOCK = 48_000_000
RX = 6
HELPERS = {"overlap4": usb_uart_overlap4, "envelope3": usb_uart_envelope3, "byte": usb_uart_byte}
IMAGES = {"overlap4": protoemu_fw.USB_UART_OVERLAP4, "envelope3": protoemu_fw.USB_UART_ENVELOPE3,
          "byte": protoemu_fw.USB_UART_BYTE}


def flat(image):
    return [(a + i, w) for a, ws in image["writes"] for i, w in enumerate(ws)]


class Board:
    """The demo board on the model. The UART sender drives uio[rx] (idle
    high) and sees only READY = uo0; it runs before every chip clock,
    including those of the host's own SPI transactions. The chip must never
    drive the RX pad."""

    def __init__(self, rx=RX):
        self.mh = mh = _TapeoutModelHost()
        mh.pull_up = 0b10                       # low-speed D- pull-up
        self.sender = Sender([])                # no frames until the test queues them
        self.edges = {"ready": [], "error": []}
        chip, original, bit = mh.chip, mh.chip.step, 1 << rx

        def step(ui_in=0, uio_in=0, **kw):
            uo, _, oe = chip.outputs()
            assert not oe & bit, "the chip drives the UART RX pad"
            for name, v in (("ready", uo & 1), ("error", (uo >> 1) & 1)):
                e = self.edges[name]
                if not e or e[-1][1] != v:
                    e.append((chip.cycle, v))
            level = self.sender.tick(chip.cycle, uo & 1)
            mh.uio, mh.ext_oe = (mh.uio & ~bit) | (level << rx), mh.ext_oe | bit
            return original(ui_in, (uio_in & ~bit) | (level << rx), **kw)
        chip.step = step
        self.tt = FakeTT(mh)
        self.pe = protoemu.ProtoEmu(self.tt)

    def send(self, data, bad=()):
        """Queue frames: bytes of `data`, the indices in `bad` with a low STOP."""
        self.sender.todo += [(b, i not in bad, i, 0) for i, b in enumerate(data)]


class PcDevice:
    """pyusb's ctrl_transfer() on the model's ControlHost at `addr`; STALL is
    pyusb's EPIPE error; sleep() and clock() are model time."""

    def __init__(self, h, addr):
        self.h, self.addr, self.raw, self.stalls = h, addr, [], 0

    def ctrl_transfer(self, bmRequestType, bRequest, wValue=0, wIndex=0, data_or_wLength=None, timeout=None):
        n = data_or_wLength
        setup = bytes([bmRequestType, bRequest, wValue & 255, wValue >> 8, wIndex & 255, wIndex >> 8,
                       n & 255, n >> 8])
        try:
            data = self.h.control_read(self.addr, setup)
        except Stall:
            self.stalls += 1
            raise FakeUsbError(errno.EPIPE, rep.LIBUSB_ERROR_PIPE)
        self.raw.append(bytes(data))
        return array.array("B", data)

    def sleep(self, s):
        self.h.mh.step(round(s * CLOCK))

    def clock(self):
        return self.h.mh.chip.cycle / CLOCK


SCENARIOS = {
    # mode: (UART bytes, bad-STOP indices, raw reports the PC must receive)
    "overlap4": (bytes.fromhex("39d3815ea612345678"), {4}, ["39d3815e", "12345678"]),
    "envelope3": (bytes.fromhex("39d38100a5"), set(), ["39d38103", "00a50002"]),
    "byte": (bytes.fromhex("a5003cff"), {2}, ["a5", "00", "ff"]),
}


@pytest.mark.parametrize("mode", sorted(SCENARIOS))
def test_board_usb_uart_end_to_end(mode):
    data, bad, reports = SCENARIOS[mode]
    want = bytes(b for i, b in enumerate(data) if i not in bad)     # the good bytes, in order
    board = Board()
    mh, pe = board.mh, board.pe
    assert pe.ui & 0x1F == 0                    # the driver holds ui0..4 low
    pins = bringup.usb_uart(pe, mode, rx_pin=RX)
    assert pins == dict(mode=mode, rx=RX, ready=8, error=None if mode == "byte" else 9)
    loaded = mh.chip.cycle
    pin_writes = []
    port_pins = board.tt.port.pins
    board.tt.port.pins = lambda ui: pin_writes.append(ui)      # any driver pin activity
    with TransportIdle(mh):
        for _ in range(200):                    # the mailbox checks idle at 8 ticks per bit
            if mh.chip.outputs()[0] & 1:
                break
            mh.step(50)
        assert mh.chip.outputs()[0] & 1, "READY not high with RX idle on uio6"
        h = ControlHost(mh, bit=32, gap_bits=2, retries=0)
        enumerate_flow(h, addr=5)
        dev = PcDevice(h, 5)
        assert rep.read_report(dev, mode) is None and dev.stalls == 1    # nothing sent yet
        board.send(data, bad)
        got = rep.read_bytes(dev, mode, len(want), poll_s=200e-6, timeout_s=0.05,
                             sleep=dev.sleep, clock=dev.clock)
        assert got == want, got.hex()
        assert [r.hex() for r in dev.raw] == reports
        assert not board.sender.todo and board.sender.cur is None
        assert rep.read_report(dev, mode) is None                        # all delivered
    assert not pin_writes, "the board driver touched ui_in after the load"
    board.tt.port.pins = port_pins
    frames = board.sender.frames
    assert [f["byte"] for f in frames] == list(data)
    ready = board.edges["ready"]
    for f in frames:                            # one fresh READY rise per frame, READY high at START
        assert loaded <= f["grant"] < f["start"]
        assert [v for t, v in ready if t <= f["start"]][-1] == 1
    assert len({f["grant"] for f in frames}) == len(frames)
    error = [v for t, v in board.edges["error"]]
    if bad:                                     # ERROR rises inside the bad frame's STOP slot
        f = frames[min(bad)]
        assert len(board.edges["error"]) < 2 or f["start"] < board.edges["error"][1][0] <= f["end"] + 100
    flags = pe.read(protoemu.A_FSET)            # through the driver, after the scenario
    assert not flags & 0x80, "a report is still retained"
    if mode == "byte":
        assert error == [0] and flags & 0x40, "sticky bad-STOP flag F6"
    else:
        assert error == ([0, 1] if bad else [0]), board.edges["error"]
    print(f"{mode}: load {loaded} clocks over the bit-banged port, then "
          f"{mh.chip.cycle - loaded} clocks host-port idle; {len(frames)} frames, reports {reports}")


def test_bundle_images_replay_the_helpers_at_uio6():
    """Each bundle image is exactly helper.load_writes(rx_pin=6), the start
    included; its owner map is the one those writes set. Against the default
    image only RX-bearing words change: the PIN_OWNER pair and core 1's
    instructions and PINCTRL1. Core 0 (USB) is identical."""
    for mode, mod in HELPERS.items():
        image = IMAGES[mode]
        board = flat(image)
        assert board == mod.load_writes(rx_pin=RX), mode
        assert (image["rx"], image["ready"]) == (RX, mod.READY)
        assert image["request"] == mod.vendor_request() == rep.setup_packet(mode)
        assert host.pin_owner_writes(image["owners"]) == [w for w in board if w[0] in (sim.A_POWN_LO, sim.A_POWN_HI)]
        default = mod.load_writes()
        assert default == mod.load_writes(rx_pin=16) and len(default) == len(board)
        changed = {a for (a, x), (b, y) in zip(default, board) if (a, x) != (b, y)}
        assert all(a == b for (a, _), (b, _) in zip(default, board))
        core1 = set(range(256, 512)) | {sim.core_reg(1, sim.C_PINCTRL1)}
        assert {sim.A_POWN_LO, sim.A_POWN_HI} < changed <= {sim.A_POWN_LO, sim.A_POWN_HI} | core1, (mode, changed)
        assert all(x == y for (a, x), (_, y) in zip(default, board) if a < 256), mode


def test_default_rx_on_ui0_never_grants_on_the_board():
    """Why the board images move RX: the default image (RX = ui0), loaded
    through the same driver, sees ui0 held low (a break) and never raises READY."""
    board = Board()
    pe = board.pe
    pe.stop(3)
    pe.clear_fifos(3)
    bringup._apply(pe, usb_uart_overlap4.load_writes())
    board.mh.step(20000)
    assert pe.ui & 1 == 0 and [v for _, v in board.edges["ready"]] == [0]


def test_pc_reader_byte_mode():
    """The mailbox request is request 1 with wLength 1; one byte per report."""
    assert rep.setup_packet("byte") == usb_uart_byte.vendor_request() == bytes.fromhex("c001000000000100")
    assert rep.decode_byte(array.array("B", b"\x00")) == b"\x00"
    for bad in (b"", b"\x01\x02", bytes(4)):
        with pytest.raises(ValueError):
            rep.decode_byte(bad)


def test_rx_pin_choices_are_checked():
    for mod in HELPERS.values():
        for pin in (0, 1, 8, 15, 21, 24, -1, 32, True, "6"):
            with pytest.raises(ValueError):
                mod.load_writes(rx_pin=pin)
        for pin in (2, 7, 16, 20):
            assert len(mod.load_writes(rx_pin=pin)) == len(mod.load_writes())
    pe = Board().pe
    for kw in (dict(mode="batch4"), dict(mode="overlap4", rx_pin=16)):
        with pytest.raises(ValueError):
            bringup.usb_uart(pe, **kw)
    small = protoemu.ProtoEmu(FakeTT(_TapeoutModelHost()), fifo_depth=4)
    with pytest.raises(ValueError):
        bringup.usb_uart(small, "overlap4")
