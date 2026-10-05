"""Opt-in RP2 PIO/DMA transport. Requires MicroPython 1.26's rp2.DMA API.

Construct ProtoEmu first, then attach this transport. The caller reserves an
unused state machine and all ten SPI GPIOs; the TT SDK must not write ui_in
while attached. No board throughput or sampling margin has been established.
"""
from array import array

MAX_WORDS = 4096


def reverse_nibbles(word):
    """Reverse bit order inside each nibble for descending GPIO maps."""
    word = ((word & 0x55555555) << 1) | ((word >> 1) & 0x55555555)
    return ((word & 0x33333333) << 2) | ((word >> 2) & 0x33333333)


def serial_byte(value):
    word = 0
    for bit in range(7, -1, -1):
        word = (word << 4) | (((value >> bit) & 1) << 3)
    return word


def _address(addr, n, fixed):
    if not isinstance(addr, int) or not 0 <= addr <= 0xFFFF:
        raise ValueError("address must fit 16 bits")
    if not isinstance(n, int) or not 1 <= n <= MAX_WORDS:
        raise ValueError("burst must contain 1..4096 words")
    if not fixed and addr + n > 0x10000:
        raise ValueError("burst crosses the address limit")


def write_frame(addr, words, fixed=False):
    """PIO payload words and exact SCK count, excluding its control word."""
    words = [words] if isinstance(words, int) else list(words)
    _address(addr, len(words), fixed)
    for word in words:
        if not isinstance(word, int) or not 0 <= word <= 0xFFFFFFFF:
            raise ValueError("data must fit 32 bits")
    payload = array('I', [serial_byte(0x4A if fixed else 0x42)])
    previous = addr
    for word in words:
        payload.append((previous << 16) | (word >> 16))
        previous = word & 0xFFFF
    payload.append(previous << 16)
    return payload, 12 + 8 * len(words)


def read_frame(addr, n=1, fixed=False):
    _address(addr, n, fixed)
    # Eight serial command bits, four quad address nibbles, eight dummy SCK.
    payload = array('I', [serial_byte(0x4B if fixed else 0x43), addr << 16])
    payload.extend(0 for _ in range(n + 1))
    return payload, 20 + 8 * n


def read_words(samples, n, reverse=False):
    # First data nibble is sample 20, halfway through RX word 2. A partial
    # last word was padded in PIO, so the final high half is valid as well.
    if reverse:
        samples = [reverse_nibbles(w) for w in samples]
    return [((samples[i + 2] & 0xFFFF) << 16) | (samples[i + 3] >> 16)
            for i in range(n)]


def gpio_group(numbers):
    numbers = tuple(numbers)
    if len(numbers) != 4 or any(not isinstance(p, int) or not 0 <= p < 48 for p in numbers):
        raise ValueError("data GPIOs must be four pins in 0..47")
    step = numbers[1] - numbers[0]
    if step not in (-1, 1) or any(numbers[i] != numbers[0] + i * step for i in range(4)):
        raise ValueError("data GPIOs must be contiguous in logical IO0..IO3 order")
    return min(numbers), step == -1


def check_gpio_window(pins, base):
    if base not in (0, 16) or any(not base <= pin < base + 32 for pin in pins):
        raise ValueError("SPI pins do not fit the PIO GPIO window; configure an owned block before creating state machines")


def clock_config(system_hz, project_hz, sck_hz):
    if any(not isinstance(n, int) or n <= 0 for n in (system_hz, project_hz, sck_hz)):
        raise ValueError("clock frequencies must be positive integers")
    if sck_hz * 2 > project_hz:
        raise ValueError("this transport limits SCK to half the project clock")
    # Round the Q16.8 divider up: requested SCK is an upper bound. Check the
    # shortest fractional-divider half-cycle, not just the average period.
    divider = (system_hz * 256 + 4 * sck_hz - 1) // (4 * sck_hz)
    if not 256 <= divider <= 0xFFFFFF:
        raise ValueError("PIO clock divider is outside its supported range")
    if 4 * (divider // 256) * project_hz < system_hz:
        raise ValueError("fractional-divider half-cycle violates the SPI minimum")
    pio_hz = (system_hz * 256) // divider
    # MicroPython truncates its divider; flooring the frequency can only make
    # that divider larger. Report the actual configured average below.
    actual_divider = (system_hz * 256) // pio_hz
    return pio_hz, actual_divider, (system_hz * 256) // (4 * actual_divider)


class QuadSPI:
    def __init__(self, out_gpio, in_gpio, sck_gpio, cs_gpio, project_hz,
                 sm_id, sck_hz=None, timeout_us=100000):
        import machine
        import rp2
        import time
        if not hasattr(rp2, 'DMA'):
            raise RuntimeError("MicroPython with rp2.DMA is required")
        if not isinstance(sm_id, int) or not 0 <= sm_id < 8:
            raise ValueError("reserve an unused state machine in 0..7")
        if not isinstance(timeout_us, int) or not 1 <= timeout_us <= 1000000:
            raise ValueError("timeout_us must be in 1..1000000")
        out_gpio, in_gpio = tuple(out_gpio), tuple(in_gpio)
        self.out_base, self.out_reverse = gpio_group(out_gpio)
        self.in_base, self.in_reverse = gpio_group(in_gpio)
        pins = out_gpio + in_gpio + (sck_gpio, cs_gpio)
        if any(not isinstance(p, int) or not 0 <= p < 48 for p in pins) or len(set(pins)) != 10:
            raise ValueError("SPI requires ten distinct GPIOs in 0..47")
        pio = rp2.PIO(sm_id // 4)
        self.gpio_base = 0
        if hasattr(pio, 'gpio_base'):
            base_pin = pio.gpio_base()
            if base_pin == machine.Pin(16):
                self.gpio_base = 16
            elif base_pin != machine.Pin(0):
                raise ValueError("unsupported PIO GPIO base")
        elif any(pin >= 30 for pin in pins):
            raise ValueError("this MicroPython build cannot access GPIOs above 29")
        # GPIO base belongs to the whole PIO block. Query it; never change it
        # behind another state-machine owner's back. Software CS is separate.
        check_gpio_window(out_gpio + in_gpio + (sck_gpio,), self.gpio_base)
        self.system_hz = machine.freq()
        self.pio_hz, self.divider, self.sck_hz = clock_config(
            self.system_hz, project_hz, project_hz // 2 if sck_hz is None else sck_hz)
        self.project_hz, self.sm_id = project_hz, sm_id
        self.out_gpio, self.in_gpio = out_gpio, in_gpio
        self.sck_gpio, self.cs_gpio = sck_gpio, cs_gpio
        self._tt = None
        self.timeout_us = timeout_us
        self.gap_us = max(1, (5_000_000 + project_hz - 1) // project_hz)
        self._machine, self._rp2, self._time = machine, rp2, time
        self.sm = rp2.StateMachine(sm_id)
        if self.sm.active():
            raise RuntimeError("reserved state machine is already active")
        from pio_quad import spi_stream
        self._program = spi_stream
        self.tx_dma = self.rx_dma = None
        self.closed, self.busy, self._buffers = False, False, None
        # Allocate both DMA channels before changing GPIO muxes. Partial
        # allocation failure returns the first channel to its owner pool.
        try:
            self.tx_dma = rp2.DMA()
            self.rx_dma = rp2.DMA()
            self.cs = machine.Pin(cs_gpio, machine.Pin.OUT, value=1)
            self.sck = machine.Pin(sck_gpio, machine.Pin.OUT, value=0)
            self._inputs = [machine.Pin(p, machine.Pin.IN) for p in in_gpio]
            self._init_sm()
            dreq = (sm_id // 4 << 3) + sm_id % 4
            self.tx_ctrl = self.tx_dma.pack_ctrl(size=2, inc_read=True, inc_write=False,
                                                 treq_sel=dreq)
            self.rx_ctrl = self.rx_dma.pack_ctrl(size=2, inc_read=False, inc_write=True,
                                                 treq_sel=dreq + 4)
        except BaseException:
            self.close()
            raise

    @classmethod
    def from_tt(cls, tt, sm_id, sck_hz=None, timeout_us=100000):
        """Use the selected SDK pin map and actual SDK project-clock setting."""
        project_hz = tt.auto_clocking_freq
        if not isinstance(project_hz, int) or project_hz <= 0:
            raise ValueError("start and verify the project clock before attaching PIO")
        out_gpio = [getattr(tt.pins, 'ui_in%d' % p).gpio_num for p in range(2, 6)]
        in_gpio = [getattr(tt.pins, 'uo_out%d' % p).gpio_num for p in range(4, 8)]
        bus = cls(out_gpio, in_gpio, tt.pins.ui_in6.gpio_num, tt.pins.ui_in7.gpio_num,
                  project_hz, sm_id, sck_hz, timeout_us)
        bus._tt = tt
        return bus

    def _init_sm(self):
        self.sm.init(self._program, freq=self.pio_hz,
                     out_base=self._machine.Pin(self.out_base),
                     in_base=self._machine.Pin(self.in_base), sideset_base=self.sck)
        self.sm.active(1)

    def _abort(self):
        self.sm.active(0)
        # active(False) calls dma_channel_abort in MicroPython 1.26; close()
        # only unclaims the channel, so always abort before dropping buffers.
        for dma in (self.rx_dma, self.tx_dma):
            if dma is not None:
                dma.active(0)
        if hasattr(self, 'cs'):
            self.cs.value(1)
            self.sck.init(self._machine.Pin.OUT, value=0)

    def close(self):
        if self.closed:
            return
        self._abort()
        for dma in (self.rx_dma, self.tx_dma):
            if dma is not None:
                dma.close()
        self._buffers = None
        self.closed = True

    def _transfer(self, payload, count):
        if self.closed or self.busy:
            raise RuntimeError("transport is closed or already in use")
        if self._machine.freq() != self.system_hz:
            raise RuntimeError("system clock changed; recreate the transport")
        if self._tt is not None and self._tt.auto_clocking_freq != self.project_hz:
            raise RuntimeError("project clock changed; recreate the transport")
        if count % 8 not in (0, 4) or not 1 <= count <= 65536 or len(payload) != (count + 7) // 8:
            raise ValueError("invalid PIO sample count")
        tx = array('I', [((count - 1) << 16) | (1 if count % 8 else 0)])
        tx.extend(reverse_nibbles(w) if self.out_reverse else w for w in payload)
        rx = array('I', (0 for _ in range((count + 7) // 8 + 1)))
        self.busy, self._buffers = True, (tx, rx)
        try:
            self.rx_dma.config(read=self.sm, write=rx, count=len(rx), ctrl=self.rx_ctrl)
            self.tx_dma.config(read=tx, write=self.sm, count=len(tx), ctrl=self.tx_ctrl)
            self._time.sleep_us(self.gap_us)
            self.cs.value(0)
            self.rx_dma.active(1)
            self.tx_dma.active(1)
            start = self._time.ticks_us()
            while self.rx_dma.active() or self.tx_dma.active():
                if self._time.ticks_diff(self._time.ticks_us(), start) >= self.timeout_us:
                    raise RuntimeError("PIO/DMA SPI timed out; a partial write may have landed")
            for dma in (self.rx_dma, self.tx_dma):
                status = dma.unpack_ctrl(dma.ctrl)
                if dma.count or any(status[k] for k in ('ahb_err', 'read_err', 'write_err')):
                    raise RuntimeError("PIO/DMA SPI transfer failed")
            if rx[-1] != 0xFFFFFFFF:
                raise RuntimeError("PIO completion marker is missing")
            self.cs.value(1)
            return rx[:-1]
        except BaseException:
            self._abort()
            self._init_sm()  # Clears FIFOs/partial shifts before any retry.
            raise
        finally:
            self.busy, self._buffers = False, None

    def xfer(self, data, nread=0):
        """Legacy serial transfer, including bootstrap/recovery after chip reset."""
        data = bytes(data)
        if not isinstance(nread, int) or nread < 0 or not 1 <= len(data) + nread <= 8192:
            raise ValueError("serial transfer must contain 1..8192 bytes")
        samples = self._transfer(array('I', (serial_byte(b) for b in data + bytes(nread))),
                                 8 * (len(data) + nread))
        result = bytearray()
        for word in samples:
            if self.in_reverse:
                word = reverse_nibbles(word)
            byte = 0
            for shift in range(28, -1, -4):
                byte = (byte << 1) | ((word >> (shift + 3)) & 1)
            result.append(byte)
        return bytes(result)

    def enable_quad(self):
        """Call after reset; does not infer or reset core/ownership state."""
        self.xfer(b'\x02\x08\x0b\x00\x00\x00\x01')
        if self.read(0x800) != 0x50450002:
            raise RuntimeError("quad ID check failed; check pins, clock and chip reset")

    def write(self, addr, words, fixed=False):
        self._transfer(*write_frame(addr, words, fixed))

    def read(self, addr, n=1, fixed=False):
        samples = self._transfer(*read_frame(addr, n, fixed))
        words = read_words(samples, n, self.in_reverse)
        return words[0] if n == 1 else words
