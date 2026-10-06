"""A USB full-speed host (bus master) for the cycle-accurate model, pins only.

Independent of the device firmware: it drives D+/D- with edges placed in
continuous time (12 Mbit/s with a frequency offset, random edge jitter and
duty-cycle distortion), samples the chip's pads at its clock edges, and
decodes the device's answers with its own clock recovery (resynchronised on
every transition, as a real receiver does). It measures the bus turnaround
(USB 2.0 §7.1.18.1: from the SE0 -> J transition ending the host's EOP to the
J -> K transition starting the device's packet).

Wiring (docs/usb-full-speed.md): D- on uio0, D+ on uio1, the device's 1.5k
pull-up on D+. Full-speed J is D+ high, so the pad patterns are
J = 0b10, K = 0b01, SE0 = 0b00.

The model has no analog: pad values are taken at the chip's rising edges (and
at the falling edge before, for the line unit's half-cycle sample), and the
device's pads change at the clock edge that updates its output register.
"""
import bisect
import random

from . import usb
from .usbhost import ControlHost

J, K, SE0 = usb.J, usb.K, usb.SE0
PADS = {J: 0b10, K: 0b01, SE0: 0b00}
STATE = {0b10: J, 0b01: K, 0b00: SE0, 0b11: "1"}


class FsHost:
    def __init__(self, mh, clk_hz=48e6, ppm=0.0, jitter_ns=0.0, dcd_ns=0.0, seed=1,
                 ipd_bits=4.0, ack_bits=4.0, idle=None, timeout_bits=18):
        """mh: ModelHost (chip clocked at clk_hz). ppm: host bit-rate offset.
        jitter_ns: each host edge moved by a uniform random amount in
        [-jitter_ns, +jitter_ns]; dcd_ns: J->K edges late and K->J edges early
        by that much (duty-cycle distortion). ipd_bits: gap between the host's
        own packets (token -> DATA). ack_bits: gap before the host's ACK of
        device DATA. timeout_bits: the host's response timeout (spec 16-18)."""
        self.mh = mh
        self.tclk = 1e9 / clk_hz
        self.tbit = 1e9 / (12e6 * (1 + ppm * 1e-6))     # the host's bit time
        self.tnom = 1e9 / 12e6
        self.jitter, self.dcd = jitter_ns, dcd_ns
        self.rng = random.Random(seed)
        self.ipd, self.ack_bits, self.timeout = ipd_bits, ack_bits, timeout_bits
        self.idle = idle or (lambda mh: None)
        self.log = []
        self.turnarounds = []          # (what, ns from host EOP J to device K)
        self.collisions = 0
        self.last_eop_j = None         # time of the last SE0 -> J on the bus (host or device)
        self.service = None            # SpiService running concurrently, if any
        mh.pull_up |= 0b10             # 1.5k on D+

    # ------------------------------------------------------------- time
    def now(self):
        """Time (ns) of the next rising clock edge."""
        return self.mh.chip.cycle * self.tclk

    def _edge(self, pads=None, mid=None):
        """One rising clock edge. pads/mid: what the host drives on D-/D+ at
        this edge and at the falling edge before it (None: not driving). A
        concurrent SPI service (self.service) may add a host-bus operation."""
        mh = self.mh
        bus = mh.bus_uio()
        if pads is not None:
            if mh.chip.outputs()[2] & 3:
                self.collisions += 1
            mid = bus & 3 if mid is None else mid
            kw = dict(uio_mid=(bus & ~3) | mid)
            bus = (bus & ~3) | pads
        else:
            kw = {} if mid is None else dict(uio_mid=(bus & ~3) | mid)
        if self.service is not None:
            kw.update(self.service.tick(mh.chip))
        mh.chip.step(mh.ui, bus, **kw)

    def wait_until(self, t):
        while self.now() < t:
            self._edge()

    def wait_bits(self, n):
        self.wait_until(self.now() + n * self.tbit)

    # ------------------------------------------------------------- transmit
    def send(self, packet, gap_from=None, gap_bits=None, states=None):
        """Drive one packet (bytes after SYNC; or raw line `states`). Starts
        gap_bits host bit times after gap_from (a bus time; default: ipd_bits
        after the last EOP on the bus), or now plus a random phase if the bus
        has been idle. Returns the time of its EOP's SE0 -> J."""
        states = states if states is not None else usb.encode(packet)
        if gap_from is None and self.last_eop_j is not None:
            gap_from, gap_bits = self.last_eop_j, self.ipd    # never closer than ipd to the last EOP
        # the host's clock is asynchronous to the chip's: a random phase
        t0 = self.now() if gap_from is None else max(gap_from + gap_bits * self.tbit, self.now())
        t0 += self.rng.random() * self.tclk
        # boundary i starts state i; boundary len(states) ends the trailing J
        edges = []
        prev = J
        for i, s in enumerate(states + [None]):
            t = t0 + i * self.tbit
            if s is not None and s != prev:
                t += self.rng.uniform(-self.jitter, self.jitter)
                if s == K:
                    t += self.dcd
                elif s == J:
                    t -= self.dcd
            edges.append(t)
            prev = s if s is not None else prev
        for i in range(1, len(edges)):            # jitter never reorders edges
            edges[i] = max(edges[i], edges[i - 1] + 1.0)

        def at(t):
            """The host's pad value at time t, or None while it doesn't drive."""
            i = bisect.bisect_right(edges, t) - 1
            return PADS[states[i]] if 0 <= i < len(states) else None

        mh = self.mh
        while self.now() - self.tclk / 2 < edges[-1]:
            t = self.now()
            v, m = at(t), at(t - self.tclk / 2)
            if v is None and m is None:
                self._edge()                          # not driving (yet)
                continue
            self._edge(v if v is not None else mh.bus_uio() & 3, m)
        self.last_eop_j = edges[len(states) - 1]
        return self.last_eop_j

    # ------------------------------------------------------------- receive
    def listen(self, from_t, what=""):
        """Record the bus until the device's packet ends (EOP) or the host's
        timeout passes without a K. Returns the packet bytes, None (no answer)
        or b"?" (undecodable). Logs the turnaround."""
        mh = self.mh
        trace = []              # (edge time, state) changes
        prev = None
        start = None
        deadline = from_t + self.timeout * self.tbit
        while True:
            t = self.now()
            self._edge()
            s = STATE[mh.bus_uio() & 3]      # the pads after edge t
            if s != prev:
                trace.append((t, s))
                prev = s
                if s == K and start is None:
                    start = t
            if start is None and t > deadline:
                return None
            if start is not None and len(trace) >= 2 and trace[-1][1] == J and trace[-2][1] == SE0:
                break
            if start is not None and t - start > 2e5:
                return b"?"
        self.turnarounds.append((what, start - from_t))
        self.last_eop_j = trace[-1][0]
        return self._decode([(t, s) for t, s in trace if t >= start])

    def _decode(self, trace):
        """Clock recovery at the nominal 12 Mbit/s: sample mid-bit, re-centred
        on every transition."""
        states = [J]
        for (t, s), (t2, _) in zip(trace, trace[1:]):
            n = max(1, round((t2 - t) / self.tnom))
            states += [s] * n
        states.append(trace[-1][1])
        try:
            return usb.decode(states)
        except ValueError:
            return b"?"

    # ------------------------------------------------------------- transactions
    def setup(self, addr, req, tries=20):
        for _ in range(tries):
            t = self.send(usb.token(usb.PID_SETUP, addr, 0))
            t = self.send(usb.data_packet(usb.PID_DATA0, req), t, self.ipd)
            r = self.listen(t, "SETUP data -> ACK")
            self.log.append(("SETUP", req.hex(), r))
            self.idle(self.mh)
            if r == usb.handshake(usb.PID_ACK):
                return True
        raise TimeoutError("SETUP not acknowledged")

    def data_in(self, addr, endp, tries=2000, ack=True):
        for _ in range(tries):
            t = self.send(usb.token(usb.PID_IN, addr, endp))
            r = self.listen(t, "IN -> DATA/NAK")
            if r and len(r) >= 3 and r[0] in (usb.pid_byte(usb.PID_DATA0), usb.pid_byte(usb.PID_DATA1)):
                payload, crc = r[1:-2], r[-2:]
                if usb.crc16(payload) != crc:        # corrupt: no handshake, retry
                    self.log.append(("IN", "crc", None))
                    self.idle(self.mh)
                    self.wait_bits(8)
                    continue
                if ack:
                    self.send(usb.handshake(usb.PID_ACK), self.last_eop_j, self.ack_bits)
                self.log.append(("IN", r[0], payload.hex()))
                self.idle(self.mh)
                return r[0], payload
            self.log.append(("IN", "nak" if r == usb.handshake(usb.PID_NAK) else r, None))
            self.idle(self.mh)
            self.wait_bits(8)
        raise TimeoutError("IN never answered with data")

    def data_out(self, addr, endp, pid, payload, tries=20):
        for _ in range(tries):
            t = self.send(usb.token(usb.PID_OUT, addr, endp))
            t = self.send(usb.data_packet(pid, payload), t, self.ipd)
            r = self.listen(t, "OUT data -> ACK")
            self.log.append(("OUT", payload.hex(), r))
            self.idle(self.mh)
            if r == usb.handshake(usb.PID_ACK):
                return True
        raise TimeoutError("OUT not acknowledged")

    def sof(self, frame):
        """Start-of-frame token (the 11-bit frame number where a token has
        address and endpoint)."""
        self.send(usb.token(0x5, frame & 0x7F, (frame >> 7) & 0xF))

    def control_read(self, addr, req, length, mps=8):
        self.setup(addr, req)
        out = b""
        while len(out) < length:
            pid, payload = self.data_in(addr, 0)
            out += payload
            if len(payload) < mps:
                break
        self.data_out(addr, 0, usb.PID_DATA1, b"")
        return out[:length]

    def control_write_nodata(self, addr, req):
        self.setup(addr, req)
        pid, payload = self.data_in(addr, 0)
        assert payload == b""


class FsControlHost(ControlHost):
    """pe.usbhost.ControlHost's control transfers (DATA toggles, retries,
    STALL, injected faults) over FsHost's full-speed bus: edges in continuous
    time with frequency offset and jitter, its own clock recovery, the
    turnaround measured on the bus, and a SOF token every millisecond.
    fs_kw go to FsHost (ppm, jitter_ns, dcd_ns, seed, ...)."""

    def __init__(self, mh, gap_bits=4, retries=3, sof=True, **fs_kw):
        self.fs = FsHost(mh, **fs_kw)
        super().__init__(mh, bit=4, gap_bits=gap_bits, keepalive=sof, retries=retries)
        self.frame = 0

    def _steps(self, bits):
        self.fs.wait_bits(bits)

    def _send(self, packet):
        self.wire.append(bytes(packet))
        self.fs.send(packet)

    def _response(self):
        r = self.fs.listen(self.fs.last_eop_j)
        if r is not None:
            self.turnarounds.append(self.fs.turnarounds[-1][1] / self.fs.tnom)
            self.wire.append(r)
        return r

    def gap(self, bits=None):
        self._steps(self.gap_bits if bits is None else bits)
        if self.keepalive and self.mh.chip.cycle >= self.next_keepalive:
            self._send(usb.token(0x5, self.frame & 0x7F, (self.frame >> 7) & 0xF))   # SOF
            self.frame = (self.frame + 1) & 0x7FF
            self.next_keepalive = self.mh.chip.cycle + 48000
            self._steps(self.gap_bits)

    def bus_reset(self, cycles=480000, recovery=48000):
        """SE0 for `cycles` (10 ms by default), then J and recovery."""
        for _ in range(cycles):
            self.fs._edge(0b00)
        for _ in range(recovery):
            self.fs._edge()
        self.fs.last_eop_j = None
        self.next_keepalive = self.mh.chip.cycle


# ------------------------------------------------------------------ SPI host timing
class SpiService:
    """Device-side host software running concurrently with the USB traffic,
    with each register access costing what a quad SPI transaction costs on
    the real port (docs/isa.md §6.1): CS setup 1 clk, command 8 SCK, address
    4 SCK, 8 dummy SCK before read data, 8 SCK per 32-bit word, CS hold 1 clk,
    CS high 4 clk, plus `overhead` clk of host software per transaction, and
    2 clk of synchronizer latency on writes. `sck` = clk cycles per SCK
    (1 = SCK at clk, the port's maximum).

    `program(self)` is a generator that uses `yield from self.read(...)`,
    `self.write(...)`, and yields an int to idle that many clocks. FsHost
    calls tick() once per clock edge and merges the returned host-bus
    operation into that edge."""

    def __init__(self, program, sck=1, overhead=0):
        self.sck, self.overhead = sck, overhead
        self.gen = program(self)
        self.wait = 0
        self.val = None
        self.done = False
        self.busy = 0          # clocks inside SPI transactions

    def tick(self, chip):
        if self.done:
            return {}
        if self.wait:
            self.wait -= 1
            return {}
        try:
            item = self.gen.send(self.val)
        except StopIteration:
            self.done = True
            return {}
        self.val = None
        if isinstance(item, int):
            self.wait = max(item, 1) - 1
            return {}
        if item.get("snap"):
            self.val = chip.read(item["addr"])
        return item

    def _end(self):
        yield 1 + 4 + self.overhead

    def write(self, addr, words, fixed=True):
        """WRITE(_FIXED) of `words` (FIFO bursts use fixed)."""
        s = self.sck
        self.busy += 1 + 12 * s + 8 * s * len(words) + 1
        yield 1 + 12 * s
        for i, w in enumerate(words):
            yield 8 * s + 2 - 1
            yield dict(we=True, addr=addr if fixed else addr + i, wdata=w)
        yield from self._end()

    def read(self, addr, n=1, fixed=True):
        """READ(_FIXED): returns the list of n words (FIFO reads pop on commit)."""
        s = self.sck
        self.busy += 1 + 20 * s + 8 * s * n + 1
        a = lambda i: addr if fixed else addr + i
        yield 1 + 12 * s + 4 * s
        out = [(yield dict(addr=a(0), snap=True))]
        if n > 1:
            out.append((yield dict(addr=a(1), snap=True)))
        yield 4 * s
        for i in range(n):
            yield 8 * s - (2 if i < 2 and i + 1 < n else 1)
            yield dict(addr=a(i), commit=True)
            if i + 2 < n:
                out.append((yield dict(addr=a(i + 2), snap=True)))
        yield dict(cancel=True)
        yield from self._end()
        return out
