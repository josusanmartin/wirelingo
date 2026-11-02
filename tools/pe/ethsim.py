"""The wire and the host port around the cycle model, at pin level (PC only).

TimedPort runs a host at a stated SCK: each transaction takes the clocks it takes
on the real port (docs/isa.md §6.1, src/pe_hostport.sv), and the chip keeps
running meanwhile, so a slow host really does lose data.

Wire is the far end of the cable: a Manchester sender on RXP (ui0), and a receiver
on the TD+/TD- pads that knows nothing of the chip. Levels follow eth10-receiver.md's
front end: differential positive = 1, negative = 0, idle low.

Rig puts them together: chip, port, wire, pe.ethhost, pe.eth.Station.

    rig = Rig(clock_hz=50e6)
    rig.wire.send(frame)
    rig.run_until(lambda: rig.wire.frames())

pe.ethhost and pe.eth don't know any of this exists.
"""
import random
import struct

from . import eth, ethhost, host, sim
from .modelhost import ModelHost

IFG_NS = 9600.0
BIT_NS = 100.0
QUIET_NS = 1000.0            # silence on TD that ends a burst
PULSE_MAX_NS = 300.0         # a burst this short is a link pulse
TP_IDL_NS = 300.0


class TimedPort:
    """ProtoEmu's read/write on a model at SCK = clk / sck_div. setup, hold and
    gap are pe_spim's defaults (TIMING register), in clocks. Bus operations
    land when pe_hostport issues them: a word's last SCK rise, then the
    synchronizer and its queue (`LATENCY` clocks)."""
    LATENCY = 5

    def __init__(self, mh, sck_div=2, quad=True, setup=2, hold=3, gap=8, clock_hz=50e6):
        self.mh, self.sck, self.quad = mh, sck_div, quad
        self.clock_mhz = clock_hz / 1e6
        self.setup, self.hold, self.gap = setup, hold, gap
        self.addr_sck = 4 if quad else 16
        self.word_sck = 8 if quad else 32
        self.transactions = self.busy_clocks = 0
        if quad:
            mh.write(sim.A_HOSTCFG, 1)

    def _run(self, ops, end):
        """ops: (time, fn), a bus operation each; they take a clock, in order."""
        c = 0
        for t, fn in ops:
            if t > c:
                self.mh.step(t - c)
                c = t
            fn()
            c += 1
        if end > c:
            self.mh.step(end - c)
        self.transactions += 1
        self.busy_clocks += max(end, c)

    def write(self, addr, words, fixed=False):
        if isinstance(words, int):
            words = [words]
        s = self.sck
        t = self.setup + (8 + self.addr_sck) * s
        ops = []
        for i, w in enumerate(words):
            t += self.word_sck * s
            a = addr if fixed else addr + i
            ops.append((t - s + self.LATENCY, lambda a=a, w=w: self.mh.write(a, w)))
        self._run(ops, t + self.hold + self.gap)

    def read(self, addr, n=1, fixed=False):
        s = self.sck
        mh = self.mh
        got = []
        next_addr = [addr]

        def snap():
            got.append(mh.snap(next_addr[0]))
            if not fixed:
                next_addr[0] += 1

        t = self.setup + (8 + self.addr_sck) * s
        ops = [(t - s + 6, snap), (t - s + 8, snap)]         # two words ahead, as the port does
        t += 8 * s
        for i in range(n):
            t += self.word_sck * s
            a = addr if fixed else addr + i
            ops.append((t - s + self.LATENCY, lambda a=a: mh.commit(a)))
            if i + 1 < n:
                ops.append((t - s + self.LATENCY + 2, snap))
        end = t + self.hold
        ops.append((end + 4, mh.cancel))
        self._run(sorted(ops, key=lambda o: o[0]), end + self.gap)
        return got[0] if n == 1 else got[:n]

    def idle(self, clocks):
        self.mh.step(clocks)

    def now_us(self):
        return self.mh.chip.cycle / self.clock_mhz


def wire_bits(raw):
    """Preamble, SFD and frame bits in line order, as 802.3 sends them."""
    return [1, 0] * 31 + [1, 1] + [(b >> i) & 1 for b in raw for i in range(8)]


def line_edges(bits, t, jitter_ns=0.0, ppm=0.0, rng=None, tp_idl_ns=TP_IDL_NS):
    """(time, level) edges of a Manchester frame: a 1 is low then high, and the
    last level is held high for TP_IDL, then the line goes idle low."""
    half = 50.0 * (1 + ppm * 1e-6)
    edges, prev = [], 0
    for b in bits:
        for lv in (1 - b, b):
            if lv != prev:
                edges.append((t + (rng.uniform(-jitter_ns, jitter_ns) if jitter_ns else 0.0), lv))
                prev = lv
            t += half
    if not prev:
        edges.append((t, 1))
    edges.append((t + tp_idl_ns, 0))
    return edges


class Burst:
    """One stretch of activity on TD: (time, level) edges, level +1 (TD+ high),
    -1 (TD- high) or 0, the last one the return to idle."""

    def __init__(self, t, level):
        self.edges = [(t, level)]

    @property
    def t0(self):
        return self.edges[0][0]

    @property
    def t1(self):
        return self.edges[-1][0]

    @property
    def pulse(self):
        return self.t1 - self.t0 <= PULSE_MAX_NS


def manchester_decode(edges):
    """Bits from a burst's edges, tracked from mid-bit edge to mid-bit edge. The
    first bit of a frame is a 1 (L then H), so its mid-bit edge is the second.
    Returns (bits, TP_IDL ns) or None. TP_IDL is the high that ends the frame:
    from the last mid-bit edge after a 1, from the cell boundary after a 0."""
    if len(edges) < 3 or edges[0][1] != -1 or edges[1][1] != 1:
        return None
    bits = [1]
    i, mid = 1, edges[1][0]
    n = len(edges) - 1                         # edges[n] is the return to idle
    while True:
        j = i + 1
        if j < n and edges[j][0] - mid < 75 and j + 1 < n:   # a boundary edge first
            j += 1
        if j >= n or abs(edges[j][0] - mid - BIT_NS) > 30:
            break
        lv = edges[j][1]
        if lv == 0:
            return None
        bits.append(1 if lv == 1 else 0)
        i, mid = j, edges[j][0]
    return bits, edges[n][0] - mid - (0 if bits[-1] else 50)


def frame_from_bits(bits):
    """The bytes after exactly the 802.3 preamble and SFD, else None."""
    if bits[:64] != wire_bits(b"") or (len(bits) - 64) % 8:
        return None
    body = bits[64:]
    return bytes(sum(b << k for k, b in enumerate(body[i:i + 8])) for i in range(0, len(body), 8))


class Wire:
    """The far end of the cable, stepped by the chip's own clock: hooks
    mh.chip.step so host bus cycles and idle cycles all drive the waveform.

    send() queues a frame for a sender that defers like a half-duplex MAC: it
    starts once TD has been quiet for 96 bit times. at_ns + force start it at a
    stated time whatever TD is doing (a collision)."""

    def __init__(self, mh, clock_hz, td_pin=8):
        self.mh, self.chip = mh, mh.chip
        self.clk = 1e9 / clock_hz
        self.td_shift = td_pin - 8
        self.queue = []
        self.edges, self.i = [], 0
        self.lv = 0
        self.busy_until = 0.0            # our own last edge, for the gap
        self.td, self.last_td = 0, -1e9
        self.open = None
        self.bursts = []
        self.sent = []                   # (t0, t1) of frames on the wire
        self.both_high = 0
        self.rng = random.Random(1)
        self.chip_step = self.chip.step
        self.chip.step = self._step

    def now_ns(self):
        return self.chip.cycle * self.clk

    def send(self, raw=None, bits=None, jitter_ns=0.0, ppm=0.0, at_ns=None, force=False):
        self.queue.append(dict(bits=wire_bits(raw) if bits is None else bits, jitter=jitter_ns,
                               ppm=ppm, at=at_ns, force=force))

    def pulses(self, times_ns):
        """Link pulses (100 ns high) at the given times, merged into the schedule."""
        new = []
        for t in times_ns:
            new += [(t, 1), (t + 100.0, 0)]
        self.edges = self.edges[self.i:] + new
        self.edges.sort()
        self.i = 0

    def frames(self):
        """Good-FCS frames decoded from TD, in order."""
        out = []
        for b in self.bursts:
            d = None if b.pulse else manchester_decode(b.edges)
            f = frame_from_bits(d[0]) if d else None
            if f is not None and eth.fcs_ok(f):
                out.append(f)
        return out

    def pulse_bursts(self):
        return [b for b in self.bursts if b.pulse]

    def _try_send(self, t):
        q = self.queue[0]
        if q["at"] is not None and t < q["at"]:
            return
        if not q["force"]:
            if self.td or t - self.last_td < IFG_NS or t - self.busy_until < IFG_NS:
                return
        self.queue.pop(0)
        start = max(t + 2 * self.clk, self.edges[-1][0] if self.edges[self.i:] else 0.0)
        e = line_edges(q["bits"], start, q["jitter"], q["ppm"], self.rng)
        self.edges = self.edges[self.i:] + e
        self.i = 0
        self.busy_until = e[-1][0]
        self.sent.append((e[0][0], e[-1][0]))

    def _step(self, ui_in=0, uio_in=0, **kw):
        chip = self.chip
        t = chip.cycle * self.clk
        if self.queue:
            self._try_send(t)
        e, i, n = self.edges, self.i, len(self.edges)
        tm = t - self.clk / 2
        while i < n and e[i][0] <= tm:
            self.lv = e[i][1]
            i += 1
        mid = self.lv
        while i < n and e[i][0] <= t:
            self.lv = e[i][1]
            i += 1
        self.i = i
        ui = ui_in & ~1
        self.chip_step(ui | self.lv, uio_in, ui_mid=ui | mid, **kw)
        v = (chip.outputs()[0] >> self.td_shift) & 3
        if v == 3:
            self.both_high += 1
        lv = (0, 1, -1, 0)[v]
        t += self.clk
        if lv != self.td:
            self.td, self.last_td = lv, t
            if self.open is None:
                self.open = Burst(t, lv)
            else:
                self.open.edges.append((t, lv))
        elif self.open is not None and not lv and t - self.last_td >= QUIET_NS:
            self.bursts.append(self.open)
            self.open = None


def write_pcap(path, frames, usec_step=1000):
    """Classic pcap, LINKTYPE_ETHERNET: frames as bytes."""
    with open(path, "wb") as f:
        f.write(struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1))
        for i, fr in enumerate(frames):
            t = i * usec_step
            f.write(struct.pack("<IIII", t // 1000000, t % 1000000, len(fr), len(fr)) + bytes(fr))


class Rig:
    """Chip + host port + wire + host glue + Station at one clock.

    reply=False keeps the Station out (frames go to `received` unanswered). gap
    is the host's own time between service passes, in clocks: software."""

    def __init__(self, clock_hz=50e6, quad=True, sck_div=2, carrier_sense=True, gap=0,
                 addr=eth.mac("02:57:4c:00:00:02"), ipaddr=eth.ip("10.77.0.2"), reply=True):
        self.clock_hz = clock_hz
        self.mh = ModelHost()
        self.wire = Wire(self.mh, clock_hz)
        self.port = TimedPort(self.mh, sck_div, quad, clock_hz=clock_hz)
        self.host = ethhost.Eth10Host(self.port, int(clock_hz), carrier_sense)
        self.host.configure(host.coalesce(ethhost.load_writes(clock_hz, carrier_sense)))
        self.station = eth.Station(addr, ipaddr)
        self.reply, self.gap = reply, gap
        self.received = []

    def now_us(self):
        return self.mh.chip.cycle / (self.clock_hz / 1e6)

    def poll(self):
        for raw in self.host.service():
            self.received.append(raw)
            if self.reply:
                for r in self.station.handle(raw):
                    self.host.send(r)
        if self.gap:
            self.port.idle(self.gap)

    def run_us(self, us):
        end = self.now_us() + us
        while self.now_us() < end:
            self.poll()

    def run_until(self, done, limit_us=5000):
        end = self.now_us() + limit_us
        while not done():
            if self.now_us() > end:
                return False
            self.poll()
        return True

    def quiet(self):
        """Nothing in flight: the far end idle, RX idle, TX done, TD closed."""
        w = self.wire
        return (not w.queue and w.now_ns() > w.busy_until + 500 and not self.host.busy()
                and w.open is None and not self.mh.chip.line.rx_on and not self.mh.chip.cores[0].rxf)
