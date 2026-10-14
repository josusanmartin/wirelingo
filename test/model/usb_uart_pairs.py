"""Pins-only scenarios for the UART -> USB pair modes, shared by RTL and model.

One scenario function per mode drives a bench through the pins only:

* an ideal 8N1 UART sender on RX (logical pin 16 = ui0) at 115,200 baud and
  48 MHz (bit edges at round(k * 1250 / 3) clocks after START), which starts
  one frame per fresh READY rise (logical pin 8 = uo0), only while it sees
  READY high, and always finishes a frame it has started;
* `pe.usbhost.ControlHost` (the model tests' host controller: DATA toggles,
  STALL, keep-alives) on D+/D- (uio0/uio1, pull-up on D-), at the default
  two-bit packet gaps with no transaction retries.

A bench is `ModelBench` here (tools/pe/sim.py) or `RtlBench` in
test/test_usb_uart_rtl.py (the top level through cocotb). Both offer
ModelHost's interface to ControlHost (ui, uio, ext_oe, pull_up, chip.cycle,
bus_uio(), step(n)) and call `Bench.observe()` once per clock, before the
rising edge, with the outputs the chip drives at that point, so the sender and
the pin record run the same code on both. Time t counts clocks from the first
clock at which READY is high after the cold load (the anchor): the RTL's serial
load is far slower than the model's one write per clock, and the anchor makes
the two runs comparable clock for clock.

`check()` judges a run against expectations computed from the sender's byte
list alone (never from the device); `compare()` lists every difference
between two runs (RTL and model).
"""
from types import SimpleNamespace

from pe import sim, usb, usbdev, usb_uart_byte, usb_uart_envelope3, usb_uart_overlap4
from pe.modelhost import ModelHost
from pe.usbhost import ControlHost, Stall

EDGE = [(k * 1250 + 2) // 3 for k in range(11)]   # slot edges of one frame; EDGE[10] = 4167
FRAME = EDGE[10]
ACK = usb.handshake(usb.PID_ACK)
STALL = usb.handshake(usb.PID_STALL)
DATA_PIDS = (usb.pid_byte(usb.PID_DATA0), usb.pid_byte(usb.PID_DATA1))
SET_ADDRESS_5 = bytes.fromhex("0005050000000000")
SET_CONFIGURATION_1 = bytes.fromhex("0009010000000000")


# ------------------------------------------------------------------ pins

class Sender:
    """Ideal READY-gated 8N1 sender. frames: (byte, good_stop, group, not_before)."""

    def __init__(self, frames, lag=1):
        self.todo = list(frames)
        self.frames = []
        self.cur = None
        self.ready = 0
        self.grant = self.used = None
        self.prev_end = 0
        self.lag = lag          # clocks from seeing a fresh READY to START at the earliest

    def tick(self, t, ready):
        if ready and not self.ready:
            self.grant = t
        self.ready = ready
        if self.cur is not None and t >= self.cur["end"]:
            self.prev_end, self.cur = self.cur["end"], None
        if self.cur is None and self.todo and ready and self.grant is not None and self.grant != self.used:
            byte, good, group, after = self.todo[0]
            want = max(after, self.prev_end)
            if t >= max(want, self.grant + self.lag):
                self.todo.pop(0)
                self.cur = dict(byte=byte, good=good, group=group, start=t, end=t + FRAME,
                                grant=self.grant, want=want)
                self.frames.append(self.cur)
                self.used = self.grant
        if self.cur is None:
            return 1
        u = t - self.cur["start"]
        k = sum(1 for e in EDGE[1:10] if e <= u)          # 0 START, 1..8 data, 9 STOP
        if k == 0:
            return 0
        return (self.cur["byte"] >> (k - 1)) & 1 if k <= 8 else int(self.cur["good"])


class Bench:
    """Common part of both benches: anchor, sender and the pin record."""
    PINS = ("rx", "ready", "error", "dp", "dm")

    def init_bench(self, frames):
        self.t = None                       # clocks since the anchor
        self.sender = Sender(frames)
        self.edges = {name: [] for name in self.PINS}

    def observe(self, uo, bus):
        """One clock: uo = uo_out[6:0] as driven now, bus = resolved uio for
        this clock's edge. Returns the RX level for this edge."""
        t = self.t
        ready, error = uo & 1, (uo >> 1) & 1
        rx = self.sender.tick(t, ready)
        for name, v in (("rx", rx), ("ready", ready), ("error", error),
                        ("dp", bus & 1), ("dm", (bus >> 1) & 1)):
            e = self.edges[name]
            if not e or e[-1][1] != v:
                e.append((t, v))
        return rx

    def wait_until(self, t):
        if t > self.t:
            self.step(t - self.t)


class ModelBench(ModelHost, Bench):
    """The cycle model (two cores, two 256-word banks, depth-8 FIFOs)."""

    def __init__(self, frames):
        self.chip = sim.Chip(ncores=2, nbanks=2, bank_words=256, fifo_depth=8)
        self.ui, self.uio, self.ext_oe, self.pull_up = 1, 0, 0, 0b10   # RX idle high, D- pull-up
        self.init_bench(frames)
        try:
            from line_echo import EchoProbe        # test/model: line-RX overflow is own echo only
            self.echo = EchoProbe(self.chip, lambda: self.ext_oe & 3)
        except ImportError:
            self.echo = None

    def find_anchor(self, limit=5000):
        assert not self.chip.outputs()[0] & 1, "READY high before the anchor search"
        for _ in range(limit):
            if self.chip.outputs()[0] & 1:
                self.t = 0
                return self.chip.cycle
            ModelHost.step(self, 1)
        raise AssertionError("READY never rose after the cold load")

    def step(self, n=1):
        c = self.chip
        for _ in range(n):
            bus = self.bus_uio()
            rx = self.observe(c.outputs()[0], bus)
            self.ui = (self.ui & ~1) | rx
            c.step(self.ui, bus)
            self.t += 1


# ------------------------------------------------------------------ USB host

class PinHost(ControlHost):
    """ControlHost at two-bit gaps, keep-alives on, no transaction retries;
    every packet is recorded with its first and last clock."""

    def __init__(self, bench):
        super().__init__(bench, bit=32, gap_bits=2, keepalive=True, retries=0)
        self.timed = []

    def _send(self, packet):
        t0 = self.mh.t
        super()._send(packet)
        self.timed.append([t0, self.mh.t, "host", bytes(packet).hex()])

    def _response(self):
        t0 = self.mh.t
        r = super()._response()
        self.timed.append([t0, self.mh.t, "device", None if r is None else r.hex()])
        return r

    def data_in_raw(self, addr):
        self._send(usb.token(usb.PID_IN, addr, 0))
        r = self._response()
        assert r is not None and r != b"?", ("no or garbled answer to IN", r)
        if r[0] in DATA_PIDS:
            assert usb.crc16(r[1:-2]) == r[-2:], ("bad DATA CRC", r.hex())
        return r


def read(h, reads, addr, req, retry=False):
    """One vendor control read; None if STALLed. retry: the host drops the
    first DATA (no ACK) and repeats the IN within the same transfer."""
    t0 = h.mh.t
    try:
        if retry:
            h.setup(addr, req)
            first = h.data_in_raw(addr)
            if first == STALL:
                h.gap()
                raise Stall()
            assert first[0] == usb.pid_byte(usb.PID_DATA1), first.hex()
            h.gap()                                  # the DATA is "lost": no ACK
            second = h.data_in_raw(addr)
            assert second == first, ("retransmission differs", first.hex(), second.hex())
            h._steps(2)
            h._send(ACK)
            h.gap()
            h.data_out(addr, usb.PID_DATA1, b"")
            data = first[1:-2]
        else:
            data = h.control_read(addr, req)
    except Stall:
        data = None
    reads.append(dict(t=t0, end=h.mh.t, retry=retry, data=None if data is None else data.hex()))
    return data


def greedy(h, reads, addr, req, total, retry_at=None, tries=40):
    """Read until `total` reports have arrived, repeating a STALLed request at
    once. retry_at: the report index fetched with an in-transfer IN retry."""
    for _ in range(tries):
        got = sum(r["data"] is not None for r in reads)
        if got >= total:
            return
        read(h, reads, addr, req, retry=got == retry_at)
    raise AssertionError(f"only {got} of {total} reports after {tries} requests")


def finish(bench, h, reads, end, extra=None):
    bench.wait_until(end)
    return dict(t_end=bench.t, reads=reads, frames=bench.sender.frames, todo=len(bench.sender.todo),
                edges={k: [list(e) for e in v] for k, v in bench.edges.items()},
                wire=h.timed, turnarounds=h.turnarounds, **(extra or {}))


# ------------------------------------------------------------------ scenarios

def overlap4_frames():
    groups = [(0x00, 0xFF, 0x55, 0xAA), (0x7E, 0x81, 0x01, 0x80), (0x12, 0x34, -0xA6, 0x56, 0x78),
              (0xFE, 0xDC, 0xBA, 0x98), (0x39, 0xD3, 0x81, 0x5E), (0xFF, 0xFF, 0x7F, 0x00)]
    out = []
    for g, group in enumerate(groups):
        for b in group:
            out.append((abs(b), b >= 0, g, 1000 if not out else 0))   # negative = bad STOP
    return out


def overlap4_scenario(bench):
    """Empty request (STALL); enumeration (device descriptor, SET_ADDRESS,
    SET_CONFIGURATION) while the first groups arrive, long enough that the
    retained first report and the full second group throttle the sender
    through READY; two reports read greedily (the second with an in-transfer
    IN retry); the host pauses so that READY throttles the sender again; then
    greedy reads of the rest; a final empty request."""
    req = usb_uart_overlap4.vendor_request()
    h = PinHost(bench)
    reads = []
    bench.wait_until(200)
    read(h, reads, 0, req)                                   # nothing sent yet: STALL
    desc = h.get_descriptor(0, 1, 18)
    h.control_write(0, SET_ADDRESS_5)
    h.control_write(5, SET_CONFIGURATION_1)
    greedy(h, reads, 5, req, 2, retry_at=1)
    bench.wait_until(100000)                                 # host busy elsewhere
    greedy(h, reads, 5, req, 6)
    read(h, reads, 5, req)                                   # all delivered: STALL
    return finish(bench, h, reads, bench.t + 2000, dict(descriptor=desc.hex()))


def envelope3_frames():
    plan = [(0, 1000, (0xA5, 0x5A, 0xFF)), (1, 0, (0x7E, 0x81, 0x01)), (2, 0, (0x12, -0x99, 0x34)),
            (3, 102000, (0x00,))]
    out = []
    for g, after, group in plan:
        for i, b in enumerate(group):
            out.append((abs(b), b >= 0, g, after if i == 0 else 0))
    return out


def envelope3_scenario(bench):
    """Empty request (STALL); two full three-byte groups arrive while the host
    is busy, so the retained first group and the pending second one throttle
    the sender through READY; greedy reads (the second with an in-transfer IN
    retry); a group with a bad STOP in the middle, sealed as two bytes by the
    idle timer while the host's requests STALL; a one-byte group sealed the
    same way; a final empty request."""
    req = usb_uart_envelope3.vendor_request()
    h = PinHost(bench)
    reads = []
    bench.wait_until(200)
    read(h, reads, 0, req)                                   # nothing sent yet: STALL
    bench.wait_until(30000)                                  # host busy elsewhere
    greedy(h, reads, 0, req, 4, retry_at=1)                  # STALLs while the timer runs
    read(h, reads, 0, req)                                   # all delivered: STALL
    return finish(bench, h, reads, bench.t + 2000)


def byte_frames():
    return [(0x00, True, 0, 1000), (0xFF, True, 1, 0), (0x5A, False, 2, 0), (0xA5, True, 2, 0),
            (0x81, True, 3, 0)]


def byte_scenario(bench):
    """One-byte mailbox: empty request (STALL); greedy reads, one with an
    in-transfer IN retry; a bad STOP between two good bytes; a final empty
    request. READY throttles every frame after the first until its read."""
    req = usb_uart_byte.vendor_request()
    h = PinHost(bench)
    reads = []
    bench.wait_until(200)
    read(h, reads, 0, req)
    greedy(h, reads, 0, req, 4, retry_at=2)
    read(h, reads, 0, req)
    return finish(bench, h, reads, bench.t + 2000)


def _fixed_reports(n):
    def expect(frames):
        good = [f[0] for f in frames if f[1]]
        return [bytes(good[i:i + n]) for i in range(0, len(good), n)]
    return expect


def _envelope3_reports(frames):
    out = []
    for g in sorted({f[2] for f in frames}):
        good = [f[0] for f in frames if f[2] == g and f[1]]
        out.append(bytes(good + [0] * (3 - len(good)) + [len(good)]))
    return out


def _available_fixed(rec, k, size):
    """Clock by which report k (0-based) must be retained: its last good STOP has
    ended (plus 100 clocks of firmware publication)."""
    good = [f for f in rec["frames"] if f["good"]]
    return good[(k + 1) * size - 1]["end"] + 100 if len(good) >= (k + 1) * size else None


def _available_envelope3(rec, k):
    groups = sorted({f["group"] for f in rec["frames"]})
    if k >= len(groups):
        return None
    mine = [f for f in rec["frames"] if f["group"] == groups[k]]
    full = sum(f["good"] for f in mine) == 3
    # a full group publishes at its STOP; a partial one when 1,846 idle polls
    # of 26 clocks (47,996) have passed after READY returns
    return mine[-1]["end"] + (100 if full else 48500)


MODES = {
    "overlap4": SimpleNamespace(helper=usb_uart_overlap4, frames=overlap4_frames, scenario=overlap4_scenario,
                                expect=_fixed_reports(4), available=lambda r, k: _available_fixed(r, k, 4),
                                error_pin=True, min_holds=1, ready_fall_max=30),
    "envelope3": SimpleNamespace(helper=usb_uart_envelope3, frames=envelope3_frames, scenario=envelope3_scenario,
                                 expect=_envelope3_reports, available=_available_envelope3,
                                 error_pin=True, min_holds=1, ready_fall_max=31),
    "byte": SimpleNamespace(helper=usb_uart_byte, frames=byte_frames, scenario=byte_scenario,
                            expect=_fixed_reports(1), available=lambda r, k: _available_fixed(r, k, 1),
                            error_pin=False, min_holds=3, ready_fall_max=110),
}


def run_model(mode):
    """The scenario on the cycle model. Returns (record, bench)."""
    spec = MODES[mode]
    bench = ModelBench(spec.frames())
    bench.apply(spec.helper.load_writes())
    load = bench.chip.cycle
    anchor = bench.find_anchor()
    rec = spec.scenario(bench)
    rec.update(load_clocks=load, anchor_after_load=anchor - load, flags=bench.chip.flags)
    if bench.echo is not None:
        bench.echo.check()
        rec["echo_overflow_frames"] = sum(1 for f in bench.echo.frames if f.get("dropped"))
    return rec, bench


# ------------------------------------------------------------------ judging

def level(edges, t):
    v = None
    for t0, x in edges:
        if t0 > t:
            break
        v = x
    return v


def check(mode, rec):
    """Independent expectations; returns a summary of what was measured."""
    spec = MODES[mode]
    planned = spec.frames()
    frames = rec["frames"]
    ready, error = rec["edges"]["ready"], rec["edges"]["error"]
    assert rec["todo"] == 0 and [(f["byte"], f["good"], f["group"]) for f in frames] == \
        [p[:3] for p in planned], "sender did not send every planned frame"

    # READY: each frame starts on a fresh rise while READY is high; READY falls after its START
    fall = []
    for i, f in enumerate(frames):
        prev = frames[i - 1]["start"] if i else -1
        assert level(ready, f["start"]) == 1, ("frame started without READY", i)
        assert any(prev < t <= f["start"] and v == 1 for t, v in ready), ("no fresh READY grant", i)
        t_fall = next(t for t, v in ready if t > f["start"] and v == 0)
        fall.append(t_fall - f["start"])
        assert 1 <= fall[-1] <= spec.ready_fall_max, ("READY did not fall after START", i, fall[-1])
        if i:
            assert f["start"] >= frames[i - 1]["end"], "frames overlap"
    holds = [(i, f["start"] - f["want"]) for i, f in enumerate(frames) if f["start"] - f["want"] > 100]
    assert len(holds) >= spec.min_holds, ("READY did not throttle the sender", holds)

    # the READY rise that ended each hold, in clocks after the first clock of
    # the host's last ACK packet during the hold (the report's release)
    acks = [w[0] for w in rec["wire"] if w[2] == "host" and w[3] == ACK.hex()]
    release = []
    for i, _ in holds:
        f = frames[i]
        during = [t for t in acks if f["want"] <= t <= f["grant"]]
        release.append(f["grant"] - max(during) if during else None)

    # ERROR pin
    bad = [f for f in frames if not f["good"]]
    if spec.error_pin:
        assert len(bad) == 1 and [v for _, v in error] == [0, 1], ("ERROR edges", error)
        t_err = error[1][0]
        assert bad[0]["start"] + EDGE[9] <= t_err <= bad[0]["end"] + 100, ("ERROR not at the bad STOP", t_err)
    else:
        assert [v for _, v in error] == [0], ("ERROR pin moved", error)
        t_err = None

    # reports: exact contents and order, no loss, no duplicate
    reads = rec["reads"]
    data = [bytes.fromhex(r["data"]) for r in reads if r["data"] is not None]
    want = spec.expect(planned)
    assert data == want, ("reports", [d.hex() for d in data], [w.hex() for w in want])
    assert reads[0]["data"] is None and reads[0]["t"] < spec.available(rec, 0), "first (empty) request"
    assert reads[-1]["data"] is None and reads[-1]["t"] > frames[-1]["end"], "final (empty) request"
    delivered = 0
    for r in reads:
        if r["data"] is not None:
            delivered += 1
            continue
        due = spec.available(rec, delivered)
        assert due is None or r["t"] < due, ("STALL although a report was due", r, due)
    retried = [r for r in reads if r["retry"] and r["data"] is not None]
    assert len(retried) == 1, "in-transfer retry not exercised"
    window = [w for w in rec["wire"] if retried[0]["t"] <= w[0] < retried[0]["end"]]
    dev = [w[3] for w in window if w[2] == "device" and w[3] and int(w[3][:2], 16) in DATA_PIDS]
    assert len(dev) == 2 and dev[0] == dev[1], "retransmitted DATA differs"

    # USB: every device answer came, decoded, within the turnaround limits
    assert all(w[3] not in (None, "3f") for w in rec["wire"] if w[2] == "device")
    assert all(2.0 <= x <= 6.5 for x in rec["turnarounds"]), (min(rec["turnarounds"]), max(rec["turnarounds"]))
    if "descriptor" in rec:
        assert bytes.fromhex(rec["descriptor"]) == usbdev.DEVICE_DESC

    stalls = sum(r["data"] is None for r in reads)
    return dict(simulated_clocks=rec["t_end"], simulated_ms=round(rec["t_end"] / 48000, 3),
                frames=len(frames), good_bytes=sum(f["good"] for f in frames), reports=[d.hex() for d in data],
                requests=len(reads), stalls=stalls, usb_packets=len(rec["wire"]),
                ready_fall_after_start=[min(fall), max(fall)], holds=holds,
                ack_to_ready_rise=release, error_rise=t_err,
                turnaround_bits=[min(rec["turnarounds"]), max(rec["turnarounds"])])


def compare(a, b):
    """Every difference between two records (RTL and model), first one per key."""
    diffs = []
    for key in ("reads", "frames", "wire", "turnarounds", "descriptor", "t_end", "flags"):
        x, y = a.get(key), b.get(key)
        if x == y:
            continue
        if isinstance(x, list) and isinstance(y, list):
            i = next((i for i, (p, q) in enumerate(zip(x, y)) if p != q), min(len(x), len(y)))
            diffs.append(dict(key=key, index=i, rtl=x[i] if i < len(x) else None,
                              model=y[i] if i < len(y) else None, lengths=[len(x), len(y)]))
        else:
            diffs.append(dict(key=key, rtl=x, model=y))
    for pin in Bench.PINS:
        x, y = a["edges"][pin], b["edges"][pin]
        if x != y:
            i = next((i for i, (p, q) in enumerate(zip(x, y)) if p != q), min(len(x), len(y)))
            diffs.append(dict(key="edges." + pin, index=i, rtl=x[i] if i < len(x) else None,
                              model=y[i] if i < len(y) else None, lengths=[len(x), len(y)]))
    return diffs
