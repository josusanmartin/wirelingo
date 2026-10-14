"""protoemu bring-up on the Tiny Tapeout demo board (MicroPython, TT SDK v3).

Copy protoemu.py, protoemu_fw.py and this file to the board, then:

    import bringup
    pe = bringup.setup()          # select the project, 48 MHz, reset, check the ID
    bringup.uart_loopback(pe)     # no wiring: core 0 UART TX -> virtual pin 24 -> core 1 UART RX
    bringup.uart_tx(pe, b"hi\\r\\n")  # 115200 8N1 on uo0 (to a USB-UART's RX)
    bringup.spi_transfer(pe, b"\\x9f\\0\\0\\0", mode=0)   # SPI master, CS on uio5
    data, framing_error = bringup.uart_capture(pe, 16)  # 16 bytes 8N1 on uio6

setup() needs the TT SDK; the other functions only need a ProtoEmu, so they
also run against the model (test/model/test_micropython_driver.py). The SPI
master and UART capture helpers are tested on that model only: none of them
has run on a demo board yet.
"""
import protoemu_fw as fw
from protoemu import (ProtoEmu, A_ID, A_CTRL, A_FSET, A_FCLR, A_POUT, A_POE, C_R0, C_FIFO,
                      ST_HALTED, ST_FAULT)

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


# -- SPI master on core 0: firmware/spi_master_modes.pasm (spi_transfer),
#    spi_master_packed.pasm (spi_packed_*) and spi_master_preloaded_once.pasm
#    (spi_preloaded_*). The driver writes the whole ui_in byte, so the device
#    side uses uio pins: SCK uio2, MOSI uio3, MISO uio4, CS uio5 (logical pins
#    2..5). Put a pull-up on CS: it floats until the first SPI call drives it.
#    Tested on the cycle model only; not yet run on a demo board.

SPI_SCK, SPI_MOSI, SPI_MISO, SPI_CS = 2, 3, 4, 5
SPI_DIV = 12                    # 4 ticks per bit: 1 MHz SCK at 48 MHz
SPI_PACKED_MAX = 8192           # the 16-bit bit counter: 8 * N - 1 <= 65535
SPI_PRELOADED_MAX = 32          # eight preloaded TX words
SPI_PADDING = 0xD3              # never clocked; nonzero exposes accidental tails


def _payload(data, most, what):
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise ValueError("payload must be bytes-like")
    data = bytes(data)
    if not 1 <= len(data) <= most:
        raise ValueError("%s length must be 1..%d bytes" % (what, most))
    return data


def _mode(mode):
    if not isinstance(mode, int) or mode not in (0, 1, 2, 3):
        raise ValueError("SPI mode must be 0..3")
    return mode


def _pop(pe, core, n):
    """Exactly n RX words in one fixed-address burst, always a list."""
    if not n:
        return []
    w = pe.read(pe.core_reg(core, C_FIFO), n, fixed=True)
    return [w] if n == 1 else list(w)


def _apply(pe, writes):
    """(addr, value) writes; adjacent addresses go out as one burst."""
    i = 0
    while i < len(writes):
        addr, words = writes[i][0], [writes[i][1]]
        i += 1
        while i < len(writes) and writes[i][0] == addr + len(words):
            words.append(writes[i][1])
            i += 1
        pe.write(addr, words)


def _wait(pe, done, timeout_ms, what):
    """Call done() (host-port reads) until it returns a true value."""
    t0 = pe.now_us()
    while True:
        v = done()
        if v:
            return v
        if pe.elapsed_us(pe.now_us(), t0) > 1000 * timeout_ms:
            raise RuntimeError("%s: no completion within %d ms" % (what, timeout_ms))


def _halted(pe):
    st = pe.status(0)
    return st if st & ST_HALTED else 0


def _spi_pins(pe, mode, cs):
    """CS (uio5) and SCK = CPOL through PIN_OUT. Called only while the core
    holds SCK idle, so the write never makes an SCK edge."""
    m = (1 << SPI_SCK) | (1 << SPI_CS)
    pe.write(A_POUT, (pe.read(A_POUT) & ~m) | (cs << SPI_CS) | ((mode >> 1) << SPI_SCK))


def _spi_load(pe, image, mode, div):
    """Stop core 0, set CS high and SCK idle, then drive them; load the image."""
    if not isinstance(div, int) or not 3 <= div <= 0xFFFF:
        raise ValueError("SPI divider must be an integer in 3..65535")
    pe.stop(1)
    pe.clear_fifos(1)
    pe.own_pins(0, [SPI_SCK, SPI_MOSI, SPI_CS])
    _spi_pins(pe, mode, 1)
    out = (1 << SPI_SCK) | (1 << SPI_MOSI) | (1 << SPI_CS)
    pe.write(A_POE, (pe.read(A_POE) | out) & ~(1 << SPI_MISO))
    image = dict(image)
    image["clkdiv"] = div << 8
    pe.load(**image)


def spi_transfer(pe, tx, mode=0, div=SPI_DIV, timeout_ms=2000):
    """Full-duplex SPI master transfer in mode 0..3, MSB first: clocks out tx
    and returns as many bytes read on MISO. CS is low for the whole transfer;
    the host drives it through PIN_OUT while SCK is idle. The core pauses
    with SCK idle when TX runs dry and in the sample phase when RX is full,
    so the device must tolerate pauses. Loads spi_master_modes on core 0
    each call. SCK: 4 ticks of `div` clocks per bit, 2 more per byte.
    """
    tx = _payload(tx, 1 << 30, "SPI transfer")
    mode = _mode(mode)
    _spi_load(pe, (fw.SPI_MODE0, fw.SPI_MODE1, fw.SPI_MODE2, fw.SPI_MODE3)[mode], mode, div)
    pe.start(1)                         # waits at PULL with SCK idle
    _spi_pins(pe, mode, 0)
    rx, sent, ok = bytearray(), 0, False

    def step():
        nonlocal sent
        st = pe.status(0)
        if st & (ST_HALTED | ST_FAULT):
            raise RuntimeError("SPI core halted or faulted (STATUS %08x)" % st)
        rx.extend([w & 0xFF for w in _pop(pe, 0, (st >> 4) & 15)])
        room = min(pe.depth - (st & 15), len(tx) - sent)
        if room > 0:
            pe.write(pe.core_reg(0, C_FIFO), [b << 24 for b in tx[sent:sent + room]], fixed=True)
            sent += room
        return len(rx) >= len(tx)
    try:
        _wait(pe, step, timeout_ms, "SPI transfer")
        ok = True
    finally:
        if ok:
            _spi_pins(pe, mode, 1)      # every byte is complete: SCK is idle
            pe.stop(1)
        else:
            pe.stop(1)
            pe.clear_fifos(1)
            _spi_pins(pe, mode, 1)
    return bytes(rx)


def spi_packed_encode(payload, padding=SPI_PADDING):
    """FIFO words for spi_master_packed (pe.spipacked.encode, word_bytes=4):
    the header (8 N - 1) << 16 | N % 4, then ceil(N / 4) payload words, MSB
    first; the unused bytes of a final partial word are padding, not sent."""
    payload = _payload(payload, SPI_PACKED_MAX, "packed SPI")
    if not isinstance(padding, int) or not 0 <= padding <= 255:
        raise ValueError("padding must be a byte")
    n = len(payload)
    words = [((8 * n - 1) << 16) | (n % 4)]
    for i in range(0, n, 4):
        chunk = payload[i:i + 4]
        chunk += bytes([padding]) * (4 - len(chunk))
        words.append(int.from_bytes(chunk, "big"))
    return words


def spi_packed_decode(words, n):
    """The bytes of an N-byte message's RX words: full words MSB first, a
    partial final word low aligned. Rejects a wrong word count and phantom
    bits above a partial word (pe.spipacked.decode)."""
    if not isinstance(n, int) or not 1 <= n <= SPI_PACKED_MAX:
        raise ValueError("length must be an integer in 1..8192 bytes")
    words = list(words)
    if len(words) != (n + 3) // 4:
        raise ValueError("RX word count does not match exact message length")
    out = bytearray()
    for i, w in enumerate(words):
        size = min(4, n - 4 * i)
        if not isinstance(w, int) or not 0 <= w < (1 << (8 * size)):
            raise ValueError("RX word is invalid or contains phantom tail bits")
        out.extend(w.to_bytes(size, "big"))
    return bytes(out)


def spi_packed_setup(pe, mode=0, div=SPI_DIV):
    """Load spi_master_packed (mode 0..3) on core 0 and start it; it waits
    with CS high for a header. The firmware drives CS (uio5) itself. SCK:
    4 ticks of `div` clocks per bit in modes 0 and 2, 5 in modes 1 and 3."""
    mode = _mode(mode)
    images = (fw.SPI_PACKED_MODE0, fw.SPI_PACKED_MODE1, fw.SPI_PACKED_MODE2, fw.SPI_PACKED_MODE3)
    _spi_load(pe, images[mode], mode, div)
    pe.start(1)


def spi_packed_transfer(pe, messages, timeout_ms=5000):
    """Send one message (bytes-like, 1..8192 bytes) or a list of them through
    the resident packed firmware; returns the RX bytes (a list for a list).
    Each message is one CS-low frame of exactly 8 N SCK cycles. Headers and
    payload words are queued back to back and fed while RX is drained, so
    the next header can wait in the FIFO while a frame is on the wire. All
    messages are validated before any bus traffic. The core stays resident.
    """
    single = isinstance(messages, (bytes, bytearray, memoryview))
    msgs = [messages] if single else list(messages)
    if not msgs:
        raise ValueError("no messages")
    cmds = [spi_packed_encode(m) for m in msgs]
    st, ctl = pe.status(0), pe.read(A_CTRL)
    if not ctl & 1 or st & (ST_HALTED | ST_FAULT | 0xFF):
        raise RuntimeError("packed SPI core not idle (CTRL %x, STATUS %08x): call spi_packed_setup()"
                           % (ctl, st))
    words = [w for c in cmds for w in c]
    need = sum([len(c) - 1 for c in cmds])
    rx, sent = [], 0

    def step():
        nonlocal sent
        st = pe.status(0)
        if st & (ST_HALTED | ST_FAULT):
            raise RuntimeError("SPI core halted or faulted (STATUS %08x)" % st)
        rx.extend(_pop(pe, 0, (st >> 4) & 15))
        room = min(pe.depth - (st & 15), len(words) - sent)
        if room > 0:
            pe.write(pe.core_reg(0, C_FIFO), words[sent:sent + room], fixed=True)
            sent += room
        return len(rx) >= need
    _wait(pe, step, timeout_ms, "packed SPI")
    _wait(pe, lambda: (pe.read(A_POUT) >> SPI_CS) & 1, timeout_ms, "packed SPI CS release")
    out, i = [], 0
    for m in msgs:
        k = (len(m) + 3) // 4
        out.append(spi_packed_decode(rx[i:i + k], len(m)))
        i += k
    return out[0] if single else out


def spi_preload_plan(payload, enabled_before=0):
    """pe.spipacked.preload_plan for core 0, mode 0, depth 8; no I/O."""
    payload = _payload(payload, SPI_PRELOADED_MAX, "preloaded")
    if not isinstance(enabled_before, int) or not 0 <= enabled_before <= 3:
        raise ValueError("enabled_before must be the two-core enable mask")
    n = len(payload)
    disabled = enabled_before & ~1
    return dict(length=n, tx_words=spi_packed_encode(payload)[1:], rx_words=(n + 3) // 4,
                fifo_address=ProtoEmu.core_reg(0, C_FIFO),
                disable_writes=[(A_CTRL, disabled)],
                register_writes=[(ProtoEmu.core_reg(0, C_R0), 8 * n - 1),
                                 (ProtoEmu.core_reg(0, C_R0 + 1), n % 4)],
                start_writes=[(A_CTRL, disabled | 1 | (1 << 8))])


def spi_preloaded_setup(pe, div=SPI_DIV):
    """Load spi_master_preloaded_once (mode 0) on core 0; it stays disabled
    until spi_preloaded_transfer() arms it. Needs depth-8 FIFOs."""
    if pe.depth != 8:
        raise ValueError("preloaded firmware requires depth8 FIFOs")
    _spi_load(pe, fw.SPI_PRELOADED, 0, div)


def spi_preloaded_transfer(pe, tx, timeout_ms=2000):
    """One mode-0 frame of 1..32 bytes, all preloaded before the start (no
    header word): the firmware lowers CS, sends exactly 8 N bits, raises CS
    and halts. Rearms without reloading. Checks the documented preconditions
    (CS high, SCK idle, no fault, both FIFOs empty, core disabled or halted)
    and never clears leftovers to meet them; then HALT, no fault and the exact
    RX count; after the drain, empty FIFOs and CS high with SCK idle.
    """
    st, ctl = pe.status(0), pe.read(A_CTRL)
    plan = spi_preload_plan(tx, ctl & 3)
    pout = pe.read(A_POUT)
    if st & (ST_FAULT | 0xFF) or (ctl & 1 and not st & ST_HALTED) \
            or pout & ((1 << SPI_CS) | (1 << SPI_SCK)) != 1 << SPI_CS:
        raise RuntimeError("preloaded SPI precondition (STATUS %08x, CTRL %x, PIN_OUT %08x): "
                           "call spi_preloaded_setup()" % (st, ctl, pout))
    _apply(pe, plan["disable_writes"])
    _apply(pe, plan["register_writes"])
    pe.write(plan["fifo_address"], plan["tx_words"], fixed=True)
    _apply(pe, plan["start_writes"])
    st = _wait(pe, lambda: _halted(pe), timeout_ms, "preloaded SPI")
    if st & ST_FAULT or st & 0xFF != plan["rx_words"] << 4:
        raise RuntimeError("preloaded SPI: STATUS %08x at HALT" % st)
    rx = spi_packed_decode(_pop(pe, 0, plan["rx_words"]), plan["length"])
    st, pout = pe.status(0), pe.read(A_POUT)
    if st & (0xFF | ST_FAULT) or not st & ST_HALTED \
            or pout & ((1 << SPI_CS) | (1 << SPI_SCK)) != 1 << SPI_CS:
        raise RuntimeError("preloaded SPI: STATUS %08x, PIN_OUT %08x after the drain" % (st, pout))
    return rx


# -- UART receive on core 0, 8N1, RX on uio6 (logical pin 6): declared-length
#    capture of 1..32 bytes (firmware/uart_rx_capture.pasm) and, for longer
#    messages, packed receive (firmware/uart_rx_packed.pasm). F0 is the sticky
#    framing-error flag of both. Tested on the cycle model only; not yet run
#    on a demo board.

UART_CAPTURE_RX = 6
UART_CAPTURE_MAX = 32


def uart_clkdiv(clock_hz, baud):
    """CLKDIV for 8 ticks per bit (pe.host.clkdiv_for(clock_hz, 8 * baud))."""
    d = clock_hz / (8 * baud)
    i = int(d)
    f = int(round((d - i) * 256))
    if f == 256:
        i, f = i + 1, 0
    if not 1 <= i < 65536:
        raise ValueError("clock divider out of range")
    return (i << 8) | f


def uart_capture_plan(n, enabled_before=0):
    """pe.uartcapture.capture_plan's five writes: disable core 0 (core 1's
    enable kept), R1 = N, R2 = N % 4, clear F0, restart core 0. No I/O."""
    if not isinstance(n, int) or not 1 <= n <= UART_CAPTURE_MAX:
        raise ValueError("capture length must be an integer in 1..32")
    if not isinstance(enabled_before, int) or not 0 <= enabled_before <= 3:
        raise ValueError("enabled_before must be a two-core enable mask in 0..3")
    peer = enabled_before & 2
    return [(A_CTRL, peer), (ProtoEmu.core_reg(0, C_R0 + 1), n),
            (ProtoEmu.core_reg(0, C_R0 + 2), n % 4), (A_FCLR, 1), (A_CTRL, peer | 1 | (1 << 8))]


def uart_capture_decode(n, words):
    """pe.uartcapture.decode_capture: exactly ceil(N / 4) words, full words
    little endian, a partial final word high aligned over zero padding."""
    if not isinstance(n, int) or not 1 <= n <= UART_CAPTURE_MAX:
        raise ValueError("capture length must be an integer in 1..32")
    words = list(words)
    if len(words) != (n + 3) // 4:
        raise ValueError("capture word count does not match declared length")
    for w in words:
        if not isinstance(w, int) or not 0 <= w <= 0xFFFFFFFF:
            raise ValueError("capture words must be uint32 integers")
    full, tail = divmod(n, 4)
    out = b"".join([w.to_bytes(4, "little") for w in words[:full]])
    if tail:
        pad = 8 * (4 - tail)
        if words[-1] & ((1 << pad) - 1):
            raise ValueError("nonzero padding in high-aligned partial word")
        out += (words[-1] >> pad).to_bytes(tail, "little")
    return out


def _sleep_us(us):
    import time
    if hasattr(time, "sleep_us"):
        time.sleep_us(us)
    else:
        time.sleep(us / 1e6)


def _uart_teardown(pe):
    """After a failed capture: stop core 0, clear its FIFOs and F0."""
    pe.stop(1)
    pe.clear_fifos(1)
    pe.write(A_FCLR, 1)


def uart_capture(pe, n, baud=115200, clock_hz=48_000_000, timeout_ms=2000, after_arm=None):
    """Receive exactly n bytes of 8N1 on uio6; returns (data, framing_error).

    framing_error is the sticky F0: some frame had a low STOP bit. That byte
    is kept in data (suspect), as docs/uart-capture-host.md specifies. The
    receiver is armed before after_arm() is called (if given: make the device
    send there); the sender must not start earlier.

    n = 1..32: uart_rx_capture, declared length. The core halts after the
    final STOP sample; nothing is read before that. Later frames are ignored.
    n > 32, a multiple of 4: uart_rx_packed, four bytes per RX word, drained
    while the bytes arrive; RuntimeError if the RX FIFO was ever seen full,
    since bytes may have been lost then (no lossless promise when full).

    RuntimeError on a timeout (fewer bytes sent than declared, for example),
    after stopping core 0 and clearing its FIFOs and F0. Loads the firmware
    each call (core 0; core 1's enable is kept).
    """
    if not isinstance(n, int) or n < 1 or (n > UART_CAPTURE_MAX and n % 4):
        raise ValueError("capture length must be 1..32, or a multiple of 4 above 32")
    image = dict(fw.UART_CAPTURE if n <= UART_CAPTURE_MAX else fw.UART_PACKED)
    image["clkdiv"] = uart_clkdiv(clock_hz, baud)
    pe.stop(1)
    pe.clear_fifos(1)
    pe.write(A_POE, pe.read(A_POE) & ~(1 << UART_CAPTURE_RX))
    pe.load(**image)
    nwords = (n + 3) // 4
    if n <= UART_CAPTURE_MAX:
        _apply(pe, uart_capture_plan(n, pe.read(A_CTRL) & 3))
        if after_arm:
            after_arm()
        try:
            st = _wait(pe, lambda: _halted(pe), timeout_ms, "UART capture of %d bytes" % n)
        except RuntimeError:
            _uart_teardown(pe)
            raise
        flag = pe.read(A_FSET) & 1              # F0, before any FCLR
        if st & ST_FAULT or st & 0xFF != nwords << 4:
            _uart_teardown(pe)
            raise RuntimeError("UART capture: STATUS %08x at HALT" % st)
        data = uart_capture_decode(n, _pop(pe, 0, nwords))
        if pe.status(0) & 0xFF:
            raise RuntimeError("UART capture: FIFOs not empty after the drain")
        return data, bool(flag)
    pe.write(A_FCLR, 1)
    pe.start(1)
    if after_arm:
        after_arm()
    words, full = [], [False]

    def step():
        st = pe.status(0)
        k = (st >> 4) & 15
        if k >= pe.depth:
            full[0] = True
        words.extend(_pop(pe, 0, min(k, nwords - len(words))))
        return len(words) >= nwords
    try:
        _wait(pe, step, timeout_ms, "UART receive of %d bytes" % n)
    except RuntimeError:
        _uart_teardown(pe)
        raise
    _sleep_us(20_000_000 // baud)               # the last word comes a bit before its STOP
    flag = pe.read(A_FSET) & 1
    pe.stop(1)
    pe.clear_fifos(1)
    if full[0]:
        raise RuntimeError("UART receive: the RX FIFO filled up, bytes may have been lost")
    return b"".join([w.to_bytes(4, "little") for w in words]), bool(flag)
