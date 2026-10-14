"""Measured UART envelope of the overlap4 pair: baud tolerance and line rate.

Model only, two-core tapeout profile, the shipped helper's cold load. The UART
sender is ideal and READY-gated: one 8N1 frame per fresh READY rise, edges at
round(k * T) clocks after its START edge (T = 48 MHz / baud), never starting
before its previous STOP ends. The USB host is `pe.usbhost.ControlHost` at the
default address 0 with two-bit packet gaps; it reads reports with the vendor
request and treats STALL as "no report yet".

Receiver timing (firmware/uart_rx_overlap4.pasm): START is polled every 25
clocks, so the pad sample that detects it lies d = 0..24 clocks after the edge.
Data bit j and the STOP bit (j = 8) are then sampled d + 625 + 417 j clocks
after the edge. `supported()` turns that schedule into a baud range; the
simulations check it at the edges, at the phase that is worst there.

Line rate: back to back at 115200 baud the sender sends a frame every 4167
clocks. It is held only when the second four-byte group is complete while the
first report is still unreleased; READY then rises 41..65 clocks after the
release (one 25-clock poll of phase). Each report's DATA ACK must therefore
arrive before the eighth following STOP ends, less that latency.
"""
import bisect

import pytest

from line_echo import EchoProbe
from pe import sim, usb_uart_overlap4
from pe.modelhost import ModelHost
from pe.usbhost import ControlHost, Stall

CLOCK = 48_000_000
REQUEST = usb_uart_overlap4.vendor_request()
FIRST, SPACING, POLL = 625, 417, 25           # receiver schedule (see the docstring)
LINE_FRAME = round(10 * CLOCK / 115200)       # 4167 clocks
EDGE_BYTES = bytes([0x55, 0x2A, 0x00, 0x15])  # alternating bits, every bit 7 clear


def stream(n):
    return bytes((i * 37 + 11) & 0xFF for i in range(n))


def supported(baud, delta):
    """True if an ideal sender's frame decodes when START is seen `delta`
    clocks late, for every byte (bit 7 clear is the worst case at both ends)."""
    t = CLOCK / baud

    def slot(u):                    # 0 START, 1..8 data, 9 STOP, 10 idle after it
        return next((k for k in range(10) if u < round((k + 1) * t)), 10)
    return all(slot(delta + FIRST + SPACING * j) == j + 1 for j in range(8)) \
        and slot(delta + FIRST + SPACING * 8) >= 9


class _Host(ModelHost):
    def __init__(self):
        self.chip = sim.Chip(ncores=2, nbanks=2, bank_words=256, fifo_depth=8)
        self.ui, self.uio, self.ext_oe, self.pull_up = 1, 0, 0, 2


class _Rig:
    def __init__(self, data, baud=115200, start=None, keepalive=False):
        self.mh = mh = _Host()
        c = mh.chip
        programs = usb_uart_overlap4.assemble()
        self.T = CLOCK / baud
        self.queue = list(data)
        self.start_rule = start or (lambda grant, prev_end: max(grant + 1, prev_end))
        self.frames, self.detect, self.rises, self.releases, self.error = [], [], [], [], []
        self.grant = self.used = self.planned = self.frame = None
        self.ready, self.prev_end = 0, 0
        self.probe = EchoProbe(c, lambda: mh.ext_oe & 3)
        detect_pc = 256 + programs[1].labels["wait_start"] + 1
        original = c.step

        def step(ui_in=0, uio_in=0, **kw):
            cycle, out = c.cycle, c.outputs()[0]
            if out & 1 and not self.ready:
                self.grant = cycle
                self.rises.append(cycle)
            self.ready = out & 1
            if out & 2 and not self.error:
                self.error.append(cycle)
            if self.frame is not None and cycle >= self.frame["end"]:
                self.prev_end, self.frame = self.frame["end"], None
            if self.frame is None and self.queue and self.grant not in (None, self.used):
                if self.planned is None:
                    self.planned = self.start_rule(self.grant, self.prev_end)
                if cycle >= self.planned:
                    assert self.ready, "sender starts only on READY"
                    self.frame = dict(byte=self.queue.pop(0), start=cycle, want=self.prev_end,
                                      end=cycle + round(10 * self.T))
                    self.frames.append(self.frame)
                    self.used, self.planned = self.grant, None
            level = 1
            if self.frame is not None:
                t, k = cycle - self.frame["start"], 0
                while k < 9 and round((k + 1) * self.T) <= t:
                    k += 1
                level = 0 if k == 0 else 1 if k == 9 else (self.frame["byte"] >> (k - 1)) & 1
            q = c.cores[1]
            if q.pc == detect_pc and q.cnt == q.dcnt == 0 and not (c.pins_in() >> 16) & 1:
                self.detect.append(cycle)
            flags = c.flags
            answer = original((ui_in & ~1) | level, uio_in, **kw)
            if flags & 128 and not c.flags & 128:
                self.releases.append(cycle)
            return answer

        c.step = step
        mh.apply(usb_uart_overlap4.load_writes(programs))
        self.host = ControlHost(mh, bit=32, gap_bits=2, keepalive=keepalive, retries=0)

    def run_to(self, cycle):
        assert cycle >= self.mh.chip.cycle, "host schedule missed"
        self.mh.step(cycle - self.mh.chip.cycle)

    def read(self):
        try:
            return self.host.control_read(0, REQUEST)
        except Stall:
            return None

    def deltas(self):
        return [next(d for d in self.detect if d >= f["start"]) - 2 - f["start"] for f in self.frames]

    def holds(self):
        """(frame, clocks) the sender waited for READY after its previous STOP."""
        return [(i, f["start"] - f["want"]) for i, f in enumerate(self.frames)
                if i and f["start"] > f["want"]]

    def promote_latency(self, reports):
        """For each report: READY rise after its release, and its 8th following STOP end."""
        rows = []
        for n in range(reports):
            release = self.releases[n]
            rise = self.rises[bisect.bisect_left(self.rises, release)]
            rows.append((release, rise - release, self.frames[4 * n + 7]["end"]))
        return rows


def _phased(delta):
    """Start each frame so that its START is detected `delta` clocks late (the
    poll that detects it runs 22 clocks after READY rises, then every 25)."""
    def rule(grant, prev_end):
        t = grant + 22 - delta
        while t < max(grant + 1, prev_end):
            t += POLL
        return t
    return rule


def test_supported_baud_range_over_all_detection_phases():
    rate = lambda d, bauds: [b for b in bauds if supported(b, d)]
    low = max(min(rate(d, range(108000, 110500))) for d in range(POLL))
    high = min(max(rate(d, range(120000, 122500))) for d in range(POLL))
    assert (low, high) == (109050, 121059)                 # -5.34% .. +5.09%
    assert all(supported(b, d) for b in (low, 115200, high) for d in range(POLL))
    assert not supported(low - 1, 0) and not supported(high + 1, POLL - 1)


@pytest.mark.parametrize("baud,delta,ok", [(109050, 0, True), (109049, 0, False),
                                           (121059, 24, True), (121060, 24, False)])
def test_baud_edge(baud, delta, ok):
    rig = _Rig(EDGE_BYTES, baud, _phased(delta))
    while len(rig.frames) < 4 and rig.mh.chip.cycle < 40000:
        rig.mh.step(100)
    rig.run_to(rig.mh.chip.cycle + 5000)
    report = rig.read()
    assert rig.deltas() == [delta] * 4
    if ok:
        assert report == EDGE_BYTES and not rig.error
    elif baud < 115200:                    # STOP sampled inside bit 7: ERROR, byte dropped
        assert rig.error and report is None
    else:                                  # bit 7 sampled inside STOP: silent corruption
        assert not rig.error and report == bytes(b | 0x80 for b in EDGE_BYTES)
    rig.probe.check()


def test_line_rate_with_greedy_host():
    """The host repeats the request at once after each STALL (keep-alives on)."""
    reports = 12
    data = stream(4 * reports + 8)
    rig = _Rig(data, keepalive=True)
    got, stalls = [], 0
    while len(got) < reports:
        report = rig.read()
        stalls += report is None
        if report is not None:
            got.append(report)
    assert b"".join(got) == data[:4 * reports] and not rig.error
    assert not rig.holds() and stalls >= reports - 2
    span = rig.frames[4 * reports - 1]["end"] - rig.frames[0]["start"]
    assert span == 4 * reports * LINE_FRAME            # 11,519 B/s: the line rate
    rig.probe.check()


@pytest.mark.parametrize("lag,reports,held", [(7653, 26, []), (7654, 11, [(48, 19)])])
def test_read_lag_edge(lag, reports, held):
    """Report n's read starts `lag` clocks after its fourth STOP ends."""
    data = stream(4 * reports + 8)
    rig = _Rig(data)
    got = []
    for n in range(reports):
        while len(rig.frames) < 4 * n + 4:
            rig.mh.step(1)
        rig.run_to(rig.frames[4 * n + 3]["end"] + lag)
        got.append(rig.read())
    assert b"".join(got) == data[:4 * reports] and not rig.error   # held, never lost
    assert rig.holds() == held
    rows = rig.promote_latency(reports - 1)
    assert all(41 <= x <= 65 for _, x, _ in rows)
    assert all((release + x >= end) == (4 * n + 8 in dict(held))
               for n, (release, x, end) in enumerate(rows))
    rig.probe.check()
