"""Pin-level I2C models for tests and demos: a bit-banged master and an EEPROM.

Both are clocked by their environment and see only the resolved bus. The
master is a generator that yields the number of clocks to wait, so the same
code runs on the model (ModelHost loop) and in cocotb (`await ClockCycles`):

    m = Master(bus=lambda: resolved_uio, sda=0, scl=1, half=250)
    for n in m.run([("w", 0x50, [0x10]), ("r", 0x50, 8)]): wait n clocks,
        then apply m.low (the lines it pulls low)
    m.results  -> per op: ACK list (write) or bytes (read); None if NACKed

Ops: ("w", addr, bytes), ("r", addr, n), ("wr", addr, bytes, n) = write then
repeated START and read (a register read). Each op ends with STOP. SCL
stretching is honoured (with a timeout); data changes at the middle of SCL
low, SDA is sampled at the middle of SCL high.
"""


class Master:
    def __init__(self, bus, sda, scl, half, stretch_timeout=200000):
        self.bus, self.sda, self.scl, self.half = bus, sda, scl, half
        self.timeout = stretch_timeout
        self.low = 0
        self.results = []
        self.stretched = 0          # clocks spent waiting for SCL (stretching)

    def _line(self, pin, level):
        self.low = (self.low & ~(1 << pin)) | ((not level) << pin)

    def _sda_in(self):
        return (self.bus() >> self.sda) & 1

    def _scl_release(self):
        self._line(self.scl, 1)
        yield 1
        n = 0
        while not (self.bus() >> self.scl) & 1:
            n += 1
            if n > self.timeout:
                raise TimeoutError("SCL held low")
            yield 1
        self.stretched += n

    def _start(self):
        self._line(self.sda, 1)
        yield self.half // 2
        yield from self._scl_release()
        yield self.half // 2
        self._line(self.sda, 0)             # START: SDA falls while SCL is high
        yield self.half // 2
        self._line(self.scl, 0)

    def _stop(self):
        yield self.half // 2
        self._line(self.sda, 0)
        yield self.half // 2
        yield from self._scl_release()
        yield self.half // 2
        self._line(self.sda, 1)             # STOP: SDA rises while SCL is high
        yield self.half

    def _bit(self, v):
        """Send v (1 = release SDA) on one SCL clock; return the sampled SDA."""
        yield self.half // 2
        self._line(self.sda, v)
        yield self.half - self.half // 2
        yield from self._scl_release()
        yield self.half // 2
        got = self._sda_in()
        yield self.half - self.half // 2
        self._line(self.scl, 0)
        return got

    def _write_byte(self, b):
        for i in range(7, -1, -1):
            yield from self._bit((b >> i) & 1)
        ack = yield from self._bit(1)
        return ack == 0

    def _read_byte(self, ack):
        v = 0
        for _ in range(8):
            v = (v << 1) | (yield from self._bit(1))
        yield from self._bit(0 if ack else 1)
        return v

    def _read(self, addr, n):
        if not (yield from self._write_byte((addr << 1) | 1)):
            return None
        data = []
        for i in range(n):
            data.append((yield from self._read_byte(i + 1 < n)))
        return data

    def run(self, ops):
        yield self.half
        for op in ops:
            yield from self._start()
            if op[0] == "w":
                _, addr, data = op
                acks = [(yield from self._write_byte(addr << 1))]
                if acks[0]:
                    for b in data:
                        acks.append((yield from self._write_byte(b)))
                self.results.append(acks if acks[0] else None)
            elif op[0] == "r":
                _, addr, n = op
                self.results.append((yield from self._read(addr, n)))
            elif op[0] == "wr":
                _, addr, data, n = op
                ok = yield from self._write_byte(addr << 1)
                for b in data if ok else []:
                    ok = ok and (yield from self._write_byte(b))
                if ok:
                    yield from self._start()        # repeated START
                    self.results.append((yield from self._read(addr, n)))
                else:
                    self.results.append(None)
            else:
                raise ValueError(op)
            yield from self._stop()
            yield 2 * self.half


class Eeprom:
    """24Cxx-style target: 7-bit address, 256-byte memory, register pointer.
    Call tick(bus) once per clock; `low` is the lines it pulls low. No clock
    stretching. It drives SDA right after it sees SCL fall."""

    def __init__(self, sda, scl, addr=0x50, mem=None):
        self.sda, self.scl, self.addr = sda, scl, addr
        self.mem = bytearray(mem if mem is not None else range(256))
        self.ptr = 0
        self.low = 0
        self.psda = self.pscl = 1
        self.state, self.bits, self.byte, self.first, self.rw = "idle", 0, 0, False, 0
        self.send = 0
        self.log = []

    def _sda(self, level):
        self.low = (self.low & ~(1 << self.sda)) | ((not level) << self.sda)

    def tick(self, bus):
        sda, scl = (bus >> self.sda) & 1, (bus >> self.scl) & 1
        if scl and self.pscl and self.psda and not sda:            # START
            self.state, self.bits, self.byte = "addr", 0, 0
            self._sda(1)
        elif scl and self.pscl and not self.psda and sda:          # STOP
            self.state = "idle"
            self._sda(1)
        elif scl and not self.pscl:                                # rising: sample
            if self.state in ("addr", "wdata") and self.bits < 8:
                self.byte = (self.byte << 1) | sda
                self.bits += 1
            elif self.state == "rack":
                self.state = "rnext" if sda == 0 else "idle"   # ACK: another byte
        elif self.pscl and not scl:                                # falling: drive
            if self.state in ("addr", "wdata") and self.bits == 8:
                if self.state == "addr":
                    if self.byte >> 1 == self.addr:
                        self.rw, self.first = self.byte & 1, not (self.byte & 1)
                        self.state = "ack"
                        self._sda(0)
                        self.log.append(("R" if self.rw else "W", []))
                    else:
                        self.state = "idle"
                else:
                    if self.first:
                        self.ptr, self.first = self.byte, False
                    else:
                        self.mem[self.ptr] = self.byte
                        self.ptr = (self.ptr + 1) & 0xFF
                    self.log[-1][1].append(self.byte)
                    self.state = "ack"
                    self._sda(0)
            elif self.state == "ack":
                if self.rw:
                    self._load()
                else:
                    self._sda(1)
                    self.state, self.bits, self.byte = "wdata", 0, 0
            elif self.state == "rdata":
                if self.bits < 8:
                    self._sda((self.send >> (7 - self.bits)) & 1)
                    self.bits += 1
                else:
                    self._sda(1)                                   # the master's ACK bit
                    self.state = "rack"
            elif self.state == "rnext":
                self._load()
        self.psda, self.pscl = sda, scl

    def _load(self):
        self.send = self.mem[self.ptr]
        self.log[-1][1].append(self.send)
        self.ptr = (self.ptr + 1) & 0xFF
        self.state, self.bits = "rdata", 1
        self._sda((self.send >> 7) & 1)
