"""The demo-board SPI master and UART capture helpers (tools/micropython/bringup.py),
unmodified, against the cycle model through the SPI host-port model (FakeTT).

Devices sit on the uio pins the bundle uses (SCK 2, MOSI 3, MISO 4, CS 5; UART
RX 6) and see every clock, including those of the host's own bus transactions.
The SPI device is test_spi_packed.FramedPeer (exact CS frames, MOSI checked at
each sample edge, MISO launched two clocks after its launch edge); the UART
source is test_uart._PinTimeline. This is model evidence only: the helpers
have not run on a demo board.
"""
import pytest

from pe import host, spipacked, uartcapture
from pe.modelhost import ModelHost
from test_micropython_driver import FakeTT, _TapeoutModelHost, protoemu, protoemu_fw, bringup
from test_spi_packed import FramedPeer, pattern
from test_uart import _PinTimeline
from test_uart_packed import BURST

SCK, MOSI, MISO, CS, RX = 2, 3, 4, 5, 6
LONG = 120000                      # ms: success paths end early; generous for a loaded runner


class Wire:
    """Calls each device before every chip clock; a device maps uio_in."""

    def __init__(self, mh):
        self.devices = []
        chip, original = mh.chip, mh.chip.step

        def step(ui_in=0, uio_in=0, **kw):
            for device in self.devices:
                uio_in = device(chip, uio_in)
            return original(ui_in, uio_in, **kw)
        chip.step = step


def board(depth=8):
    mh = _TapeoutModelHost() if depth == 8 else ModelHost(fifo_depth=depth)
    wire = Wire(mh)
    return mh, wire, protoemu.ProtoEmu(FakeTT(mh), fifo_depth=depth)


def spi_device(mode, messages, delay=2):
    """FramedPeer on the uio pins; an undriven CS reads high (pull-up) and an
    undriven SCK idle, as before the first SPI call on the board."""
    peer = FramedPeer(mode, messages, delay)

    def device(chip, uio_in):
        out, oe = chip.pin_out, chip.pin_oe
        cs = (out >> CS) & 1 if (oe >> CS) & 1 else 1
        sck = (out >> SCK) & 1 if (oe >> SCK) & 1 else mode >> 1
        miso = peer.tick(chip.cycle, cs, sck, (out >> MOSI) & 1)
        return (uio_in & ~(1 << MISO)) | (miso << MISO)
    return peer, device


def messages(lengths, salt=0):
    return [(pattern(n, 0xA5 + 2 * i + salt), pattern(n, 0x6D + 2 * i + salt))
            for i, n in enumerate(lengths)]


# ---------------------------------------------------------------- bundle images

def test_bundle_spi_uart_images_match_the_assembler():
    """Each image is its firmware at the documented pins: one core-0 bank,
    SPI divider 12, UART 8 ticks per bit at 115200 baud and 48 MHz."""
    from pe import asm
    from pe.mpy_bundle import FW, spi_uart_images
    images = spi_uart_images()
    assert sorted(images) == sorted([f"SPI_MODE{m}" for m in range(4)]
                                    + [f"SPI_PACKED_MODE{m}" for m in range(4)]
                                    + ["SPI_PRELOADED", "UART_CAPTURE", "UART_PACKED"])
    for name, image in images.items():
        assert getattr(protoemu_fw, name) == image, name
        assert image["core"] == image["base"] == 0
        assert image["clkdiv"] == (12 << 8 if name.startswith("SPI") else host.clkdiv_for(48e6, 8 * 115200))
    modes = asm.assemble_all((FW / "spi_master_modes.pasm").read_text(), {"CPOL": 1})
    assert protoemu_fw.SPI_MODE3["words"] == modes[1].words
    assert [len(images[n]["words"]) for n in ("SPI_MODE0", "SPI_PACKED_MODE0", "SPI_PACKED_MODE1",
                                              "SPI_PRELOADED", "UART_CAPTURE", "UART_PACKED")] \
        == [6, 15, 16, 11, 19, 13]
    assert (bringup.SPI_SCK, bringup.SPI_MOSI, bringup.SPI_MISO, bringup.SPI_CS,
            bringup.UART_CAPTURE_RX) == (SCK, MOSI, MISO, CS, RX)


# ---------------------------------------------------------------- framing rules

def test_board_framing_matches_the_cpython_helpers():
    """The board copies of encode/decode/plans equal pe.spipacked and
    pe.uartcapture, including what they reject."""
    for n in [1, 2, 3, 4, 5, 7, 8, 31, 32, 33, 255, 8192]:
        tx = pattern(n, n)
        words = bringup.spi_packed_encode(tx)
        assert words == spipacked.encode(tx)
        assert bringup.spi_packed_encode(bytearray(tx), padding=0) == spipacked.encode(tx, padding=0)
        rx = [w if i < len(words) - 2 else w & ((1 << 8 * min(4, n - 4 * i)) - 1)
              for i, w in enumerate(words[1:])]
        assert bringup.spi_packed_decode(rx, n) == spipacked.decode(rx, n)
    for bad in (b"", bytes(8193), "text", [1, 2], None):
        for f in (bringup.spi_packed_encode, spipacked.encode):
            with pytest.raises(ValueError):
                f(bad)
    for words, n in (([0x123], 1), ([0x1FF], 1), ([1, 2], 1), ([], 1), ([1], 0), ([-1], 1),
                     ([0x01020304, 0x10000], 6)):
        ok = True
        try:
            want = spipacked.decode(words, n)
        except ValueError:
            ok = False
        if ok:
            assert bringup.spi_packed_decode(words, n) == want
        else:
            with pytest.raises(ValueError):
                bringup.spi_packed_decode(words, n)
    for n in (1, 3, 4, 31, 32):
        for en in range(4):
            tx = pattern(n, en)
            got, want = bringup.spi_preload_plan(tx, en), spipacked.preload_plan(tx, enabled_before=en)
            assert got == {k: want[k] for k in got}, (n, en)
    for bad, en in ((b"", 0), (bytes(33), 0), (b"x", 4)):
        with pytest.raises(ValueError):
            bringup.spi_preload_plan(bad, en)
        with pytest.raises(ValueError):
            spipacked.preload_plan(bad, enabled_before=en)
    for n in range(1, 33):
        for en in range(4):
            assert bringup.uart_capture_plan(n, en) == uartcapture.capture_plan(n, enabled_before=en)
        words = [int.from_bytes(BURST[i:i + 4], "little") for i in range(0, n - n % 4, 4)]
        if n % 4:
            words.append(int.from_bytes(BURST[n - n % 4:n], "little") << 8 * (4 - n % 4))
        assert bringup.uart_capture_decode(n, words) == uartcapture.decode_capture(n, words) == BURST[:n]
    for n, words in ((3, [0x81d33901]), (3, []), (4, [1, 2]), (0, []), (33, [0] * 9), (1, [1 << 32])):
        with pytest.raises(ValueError):
            uartcapture.decode_capture(n, words)
        with pytest.raises(ValueError):
            bringup.uart_capture_decode(n, words)
    for f in (48e6, 50e6):
        for baud in (9600, 57600, 115200, 1000000):
            assert bringup.uart_clkdiv(f, baud) == host.clkdiv_for(f, 8 * baud)


# ---------------------------------------------------------------- SPI

@pytest.mark.parametrize("mode", range(4))
def test_spi_transfer_all_modes(mode):
    """spi_master_modes: host CS around exact 8N-bit frames, full duplex; the
    longer frame needs FIFO refills and pauses on a full RX FIFO."""
    mh, wire, pe = board()
    msgs = messages([6, 20], salt=mode)
    peer, device = spi_device(mode, msgs)
    wire.devices.append(device)
    for tx, resp in msgs:
        assert bringup.spi_transfer(pe, tx, mode=mode, timeout_ms=LONG) == resp
    assert [bytes(f["wire"]) for f in peer.frames] == [tx for tx, _ in msgs]
    assert mh.chip.pin_out >> CS & 1 and (mh.chip.pin_out >> SCK) & 1 == mode >> 1
    assert not mh.chip.enable & 1                    # core 0 stopped after the transfer


def test_spi_transfer_divider_and_rejects():
    mh, wire, pe = board()
    msgs = messages([3])
    peer, device = spi_device(0, msgs, delay=1)
    wire.devices.append(device)
    assert bringup.spi_transfer(pe, msgs[0][0], div=3, timeout_ms=LONG) == msgs[0][1]
    rises = [c for c, leading in peer.edges if leading]
    assert min(b - a for a, b in zip(rises, rises[1:])) == 4 * 3
    cycle = mh.chip.cycle
    for kw in (dict(tx=b""), dict(tx="ab"), dict(tx=b"a", mode=4), dict(tx=b"a", div=2)):
        with pytest.raises(ValueError):
            bringup.spi_transfer(pe, **kw)
    assert mh.chip.cycle == cycle                    # rejected before any bus traffic


@pytest.mark.parametrize("mode", range(4))
def test_spi_packed_queue(mode):
    """spi_master_packed resident: a queued list (headers wait in the FIFO
    while a frame is on the wire), then a single message, no reload."""
    mh, wire, pe = board()
    lengths = [1, 5, 8] + ([3, 41] if mode in (0, 3) else [3])
    msgs = messages(lengths, salt=mode)
    peer, device = spi_device(mode, msgs)
    wire.devices.append(device)
    bringup.spi_packed_setup(pe, mode)
    got = bringup.spi_packed_transfer(pe, [tx for tx, _ in msgs[:3]], timeout_ms=LONG)
    assert got == [resp for _, resp in msgs[:3]]
    for tx, resp in msgs[3:]:
        assert bringup.spi_packed_transfer(pe, tx, timeout_ms=LONG) == resp
    assert [bytes(f["wire"]) for f in peer.frames] == [tx for tx, _ in msgs]
    assert mh.chip.enable & 1 and pe.status(0) & 0xFF == 0


def test_spi_packed_rejects():
    mh, wire, pe = board()
    with pytest.raises(RuntimeError, match="spi_packed_setup"):
        bringup.spi_packed_transfer(pe, b"abc")      # nothing loaded
    bringup.spi_packed_setup(pe, 0)
    cycle = mh.chip.cycle
    for bad in (b"", bytes(8193), [], [b"ok", b""], "str"):
        with pytest.raises(ValueError):
            bringup.spi_packed_transfer(pe, bad)
    assert mh.chip.cycle == cycle


def test_spi_preloaded_rearm():
    """spi_master_preloaded_once: 1..32-byte frames, each armed by the plan
    (disable, R0/R1, one FIFO burst, start) without reload."""
    mh, wire, pe = board()
    msgs = messages([1, 5, 32, 32, 4])
    peer, device = spi_device(0, msgs)
    wire.devices.append(device)
    bringup.spi_preloaded_setup(pe)
    for tx, resp in msgs:
        assert bringup.spi_preloaded_transfer(pe, tx, timeout_ms=LONG) == resp
    assert [bytes(f["wire"]) for f in peer.frames] == [tx for tx, _ in msgs]
    # a leftover TX word breaks the precondition: refused, and not cleared
    pe.write(pe.core_reg(0, protoemu.C_FIFO), 0x12345678, fixed=True)
    with pytest.raises(RuntimeError, match="precondition"):
        bringup.spi_preloaded_transfer(pe, b"x")
    assert pe.status(0) & 15 == 1 and len(peer.frames) == len(msgs)
    for bad in (b"", bytes(33)):
        with pytest.raises(ValueError):
            bringup.spi_preload_plan(bad)
    with pytest.raises(ValueError, match="depth8"):
        bringup.spi_preloaded_setup(board(depth=4)[2])


# ---------------------------------------------------------------- UART

def uart_source(wire, mh, frames, gap=3000, bad_stop=(), brk=False, ppm=0):
    """Returns after_arm(): the line starts `gap` clocks after the arm returns."""
    state = {}

    def device(chip, uio_in):
        src = state.get("src")
        level = 1 if src is None else src.level(chip.cycle)
        return (uio_in & ~(1 << RX)) | (level << RX)

    def after_arm():
        src = _PinTimeline(mh.chip.cycle + gap, ppm, 0)
        for i, byte in enumerate(frames):
            src.frame(byte, stop=0 if i in bad_stop else 1)
            if i in bad_stop and not brk:
                src.gap(2, 1)           # idle after a low STOP: the receiver rearms on high
        if brk:
            src.gap(12, 0)
            src.gap(2, 1)
        src.finish()
        state["src"] = src
    wire.devices.append(device)
    return after_arm


def test_uart_capture_declared():
    """uart_rx_capture: the documented 39 d3 81 (one high-aligned word), a
    later frame ignored; 32 bytes with a low final STOP keep every byte and
    report it; the next capture clears F0; a final STOP low then a break."""
    mh, wire, pe = board()
    arm = uart_source(wire, mh, b"\x39\xd3\x81\xaa")
    assert bringup.uart_capture(pe, 3, timeout_ms=LONG, after_arm=arm) == (b"\x39\xd3\x81", False)
    wire.devices.clear()
    arm = uart_source(wire, mh, BURST + b"\xf6", bad_stop={31}, ppm=30000)
    assert bringup.uart_capture(pe, 32, timeout_ms=LONG, after_arm=arm) == (BURST, True)
    wire.devices.clear()
    arm = uart_source(wire, mh, BURST[:5] + b"\xaa", ppm=-30000)
    assert bringup.uart_capture(pe, 5, timeout_ms=LONG, after_arm=arm) == (BURST[:5], False)
    wire.devices.clear()
    arm = uart_source(wire, mh, BURST[:4], bad_stop={3}, brk=True)
    assert bringup.uart_capture(pe, 4, timeout_ms=LONG, after_arm=arm) == (BURST[:4], True)
    assert pe.status(0) & (protoemu.ST_HALTED | 0xFF) == protoemu.ST_HALTED


def test_uart_capture_packed_beyond_32_bytes():
    """uart_rx_packed for 40 bytes, drained while they arrive; a low STOP in
    the middle keeps its byte and sets F0."""
    mh, wire, pe = board()
    data = bytes((i * 37 + 11) & 0xFF for i in range(40))
    arm = uart_source(wire, mh, data + b"\x55\x66")
    assert bringup.uart_capture(pe, 40, timeout_ms=LONG, after_arm=arm) == (data, False)
    wire.devices.clear()
    arm = uart_source(wire, mh, data[:36], bad_stop={9})
    assert bringup.uart_capture(pe, 36, timeout_ms=LONG, after_arm=arm) == (data[:36], True)
    assert not mh.chip.enable & 1 and pe.status(0) & 0xFF == 0


def test_uart_capture_undersupply_and_rejects():
    """Five declared, four sent: no HALT, so a timeout error, after which core
    0 is stopped and its FIFOs and F0 are clear. Bad lengths: no bus traffic."""
    mh, wire, pe = board()
    arm = uart_source(wire, mh, BURST[:4], gap=100)
    with pytest.raises(RuntimeError, match="no completion"):
        bringup.uart_capture(pe, 5, timeout_ms=1500, after_arm=arm)
    assert not mh.chip.enable & 1 and pe.status(0) & 0xFF == 0 and not pe.read(protoemu.A_FSET) & 1
    cycle = mh.chip.cycle
    for n in (0, 33, 34, 2.0, None):
        with pytest.raises(ValueError):
            bringup.uart_capture(pe, n)
    assert mh.chip.cycle == cycle
