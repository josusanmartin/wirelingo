"""Data through the full-speed device's vendor requests (pe.usbfsdata) on the
model, against the pins-only host in pe.usbfs. The KV260 runs the same class
against Linux (fpga/kv260/host/usb_fs_data_demo.py)."""
import pathlib
import queue
import random
import threading

import pytest

from pe import asm, host, sim, usb, usbdev
from pe.modelhost import ModelHost
from pe.usbfs import FsHost, SpiService
from pe.usbfsdata import REQ_READ, REQ_WRITE, LoopbackDevice

FW = pathlib.Path(__file__).resolve().parents[2] / "firmware"


def enumerated():
    mh = ModelHost()
    prog = asm.assemble((FW / "usb_fs_dev.pasm").read_text(), usbdev.defines())
    mh.apply(host.load_program(0, prog, 0, clkdiv=1 << 8))
    mh.write(sim.core_reg(0, sim.C_CRCCTRL), usbdev.CRCCTRL)
    mh.write(sim.A_LINEDIV, (4 << 16) | (4 << 8))
    mh.write(sim.A_LINEPINS, 0)
    mh.write(sim.A_LINECFG, 0x49F3)
    mh.pull_up = 0b10
    mh.start(1)
    mh.step(50)
    sw = LoopbackDevice(mh, prog, usbdev.DESCRIPTORS, 0, 0)

    def idle(m):
        m.step(300)
        sw.service(m)

    h = FsHost(mh, idle=idle, seed=3, jitter_ns=5)
    assert h.control_read(0, bytes([0x80, 6, 0, 1, 0, 0, 18, 0]), 18) == usbdev.DEVICE_DESC
    h.control_write_nodata(0, bytes([0, 5, 5, 0, 0, 0, 0, 0]))
    assert h.control_read(5, bytes([0x80, 6, 0, 2, 0, 0, 18, 0]), 18) == usbdev.CONFIG_DESC
    h.control_write_nodata(5, bytes([0, 9, 1, 0, 0, 0, 0, 0]))
    assert sw.address == 5
    return h, sw


def control_write(h, addr, req, data=b""):
    """SETUP, OUT data stage (DATA1, DATA0, ...), zero-length IN status stage."""
    h.setup(addr, req)
    for i in range(0, len(data), 8):
        pid = usb.PID_DATA0 if i // 8 % 2 else usb.PID_DATA1
        h.data_out(addr, 0, pid, data[i:i + 8])
    _, payload = h.data_in(addr, 0)
    assert payload == b""


def write_req(n):
    return bytes([0x40, REQ_WRITE, 0, 0, 0, 0, n & 255, n >> 8])


def read_req(n):
    return bytes([0xC0, REQ_READ, 0, 0, 0, 0, n & 255, n >> 8])


@pytest.mark.parametrize("size", [1, 7, 8, 9, 16, 64, 100])
def test_loopback(size):
    h, sw = enumerated()
    data = random.Random(size).randbytes(size)
    control_write(h, 5, write_req(size), data)
    assert sw.buffer == data
    assert h.control_read(5, read_req(size), size) == data


def test_short_read_ends_in_a_short_packet():
    h, _ = enumerated()
    control_write(h, 5, write_req(16), bytes(range(16)))
    assert h.control_read(5, read_req(32), 32) == bytes(range(16))


def test_read_clips_to_wlength():
    h, _ = enumerated()
    control_write(h, 5, write_req(16), bytes(range(16)))
    assert h.control_read(5, read_req(5), 5) == bytes(range(5))


def test_zero_length_write_empties_the_buffer():
    h, sw = enumerated()
    control_write(h, 5, write_req(8), bytes(8))
    control_write(h, 5, write_req(0))
    assert sw.buffer == b""


# ------------------------------------------------------------------ software concurrent with the bus
class SpiBridge:
    """The transport LoopbackDevice sees (read/write/push/pop_all), with every
    access run as a quad SPI transaction inside FsHost's clock loop
    (pe.usbfs.SpiService), so the device software polls while the host keeps
    the bus busy, as on the board: a SETUP can land in the middle of a
    service() call. The software runs in a thread that hands each access to
    the SpiService program and waits for its result; only one side runs at a
    time, so the run is deterministic."""

    def __init__(self, h, sw, poll=480, sck=2, overhead=48):
        self.chip = sw.mh.chip
        self.sw = sw
        self.hold = None            # hold(op, previous op, the one before): True keeps the software waiting
        self.held = False
        self.services = 0           # service() calls completed
        self.ops, self.results, self.go = queue.Queue(), queue.Queue(), queue.Queue()
        sw.mh = self
        h.idle = lambda mh: None
        self.thread = threading.Thread(target=self._software, daemon=True)
        self.thread.start()
        self.poll = poll
        self.service = h.service = SpiService(self._program, sck=sck, overhead=overhead)

    def close(self):
        self.go.put(False)
        self.results.put(self.go)           # frees a thread left waiting by a failed test
        self.thread.join(5)

    # -- the device software's transport (runs in the software thread)
    def _call(self, *op):
        self.ops.put(op)
        r = self.results.get()
        if r is self.go:
            raise SystemExit
        return r

    def read(self, addr, pop=False):
        return self._call("read", addr)

    def write(self, addr, data):
        self._call("write", addr, data)

    def push(self, core, word):
        self._call("write", sim.core_reg(core, sim.C_FIFO), word)

    def pop_all(self, core):
        return self._call("pop_all", core)

    def now_us(self):
        return self.chip.cycle / 48

    def _software(self):
        while self.go.get():
            try:
                self.sw.service()
                self.ops.put(("done",))
            except SystemExit:
                return
            except BaseException as e:      # handed to the bus side
                self.ops.put(("error", e))

    # -- the SPI side (runs inside FsHost's clock loop)
    def _program(self, bus):
        prev = prev2 = None
        while True:
            self.go.put(True)
            while True:
                op = self.ops.get()
                if op[0] == "done":
                    self.services += 1
                    break
                if op[0] == "error":
                    raise op[1]
                while self.hold is not None and self.hold(op, prev, prev2):    # descheduled here
                    self.held = True
                    yield 4
                self.held, prev, prev2 = False, op, prev
                if op[0] == "read":
                    v = (yield from bus.read(op[1]))[0]
                elif op[0] == "write":
                    v = yield from bus.write(op[1], [op[2]])
                else:                                   # RX FIFO level, then its words
                    n = ((yield from bus.read(sim.core_reg(op[1], sim.C_STATUS)))[0] >> 4) & 15
                    v = (yield from bus.read(sim.core_reg(op[1], sim.C_FIFO), n)) if n else []
                self.results.put(v)
            yield self.poll


@pytest.fixture
def concurrent():
    bridges = []

    def make(h, sw):
        bridges.append(SpiBridge(h, sw))
        return bridges[-1]
    yield make
    for b in bridges:
        b.close()


def status_in(h, addr, what, unanswered=0):
    """The status stage of a control write or a no-data request: one IN
    answered with a zero-length DATA1 (after NAKs while the software catches
    up). A DATA0 here is a stale packet from an earlier request (a real host
    ACKs and drops it, then keeps asking); no answer at all is a transaction
    error to the host, allowed only `unanswered` times before the DATA1."""
    n = len(h.log)
    pid, payload = h.data_in(addr, 0)
    seen = [e for e in h.log[n:] if e[:2] != ("IN", "nak")]
    silent = [e for e in seen[:-1] if e == ("IN", None, None)]
    assert seen[-1:] == [("IN", usb.pid_byte(usb.PID_DATA1), "")] and len(silent) == len(seen) - 1 \
        and len(silent) <= unanswered, f"{what}: status stage saw {seen}"
    assert payload == b""


def checked_write(h, addr, data, what):
    h.setup(addr, write_req(len(data)))
    for i in range(0, len(data), 8):
        h.data_out(addr, 0, usb.PID_DATA0 if i // 8 % 2 else usb.PID_DATA1, data[i:i + 8])
    status_in(h, addr, what)


def checked_read(h, addr, n, what):
    """Data stage DATA1, DATA0, ... (a short packet ends it), OUT status; with
    wLength 0 there is no data stage and the status stage is an IN."""
    h.setup(addr, read_req(n))
    if not n:
        status_in(h, addr, what)
        return b""
    out, toggle = b"", 1
    while len(out) < n:
        pid, payload = h.data_in(addr, 0)
        assert pid == usb.pid_byte(usb.PID_DATA1 if toggle else usb.PID_DATA0), f"{what}: DATA toggle"
        toggle ^= 1
        out += payload
        if len(payload) < 8:
            break
    h.data_out(addr, 0, usb.PID_DATA1, b"")
    return out


GAPS = range(0, 200, 8)     # host bit times between transfers: sweeps the software's poll period


def test_zero_length_writes_with_software_running_concurrently(concurrent):
    """Zero-length REQ_WRITEs back to back on the configured device, the next
    SETUP coming 0-192 bit times after each status stage, with the device
    software polling over SPI meanwhile; then an 8-byte round trip. Each
    status stage must be exactly one zero-length DATA1 (the KV260 bug of
    2026-11-03: a second, stale DATA0 one, which surfaced in a later
    request's status stage)."""
    h, sw = enumerated()
    concurrent(h, sw)
    for k, gap in enumerate(GAPS):
        checked_write(h, 5, b"", f"zero-length write {k}")
        assert sw.buffer == b""
        h.wait_bits(gap)
    data = bytes(range(0xA0, 0xA8))
    checked_write(h, 5, data, "8-byte write")
    assert checked_read(h, 5, 8, "8-byte read") == data


@pytest.mark.parametrize("size", [0, 8, 16, 64])
def test_round_trips_with_software_running_concurrently(concurrent, size):
    """The same check for every request shape that ends without a short data
    packet: zero-length reads (the status stage is an IN), and writes and
    reads that are a multiple of 8 bytes."""
    h, sw = enumerated()
    concurrent(h, sw)
    for k, gap in enumerate(GAPS[::3]):
        data = random.Random(k).randbytes(size)
        checked_write(h, 5, data, f"write {k}")
        assert sw.buffer == data
        h.wait_bits(gap)
        assert checked_read(h, 5, size, f"read {k}") == data
        h.wait_bits(gap)


RX_STATUS = ("read", sim.core_reg(0, sim.C_STATUS))
FSET_READ, FSET_WRITE = ("read", sim.A_FSET), ("write", sim.A_FSET, usbdev.F_STAGED)
PUSH = ("write", sim.core_reg(0, sim.C_FIFO))
HOLDS = {   # where the software is descheduled: (access, the access before it, the one before that)
    "at the decision": (FSET_READ, "pop_all", None),                   # before the flags read
    "at the first write": (("write", sim.A_FCLR, usbdev.F_GOTACK), RX_STATUS, None),  # checks passed
    "inside the check before FSET": (RX_STATUS, FSET_READ, PUSH),       # flags read, RX level not yet
    "at FSET": (FSET_WRITE, RX_STATUS, None),                           # words pushed, checked again
    "after FSET": (FSET_READ, FSET_WRITE, None),                        # F_STAGED set
    "inside the check after FSET": (RX_STATUS, FSET_READ, FSET_WRITE),  # (verifier 036)
}
# What the software cannot close by itself: a SETUP whose token lands after
# the check's flags read and whose DATA is not yet in the RX FIFO at its
# RX-level read (or that lands at the FSET write) leaves F_STAGED over a
# flushed FIFO until the check after the FSET clears it. The firmware's got_in
# guard NAKs an IN then (F_STAGED with an empty TX FIFO) instead of parking the
# core in `pull block` (usb_fs_dev.pasm, branch usb-guard), so the host sees
# NAKs, never silence: 0 unanswered INs at every point. Before the guard the
# counts were 2 (inside the check before FSET, token), 8 (at FSET, SETUP) and 1
# (after FSET, SETUP: the restart that freed the core), fpga/kv260/README.md.
UNANSWERED = {}


def staged_over_empty(h, b, what):
    """The device's own state, read from the model once the software has
    caught up. F_STAGED set over an empty TX FIFO: the next IN finds no packet
    and parks the core in `pull block`. F_UNDERRUN (flag 0, sticky): a core
    freed from `pull block` by a later push started that packet unasked and cut
    it short, leaving the rest of the words out of step in the FIFO. Caught
    up: two more service() calls end (the one running may be inside a stage's
    checks, which a status IN the firmware answers itself, SET_CONFIGURATION's,
    no longer waits for), then 400 bit times."""
    n = b.services
    for _ in range(4000):
        if b.services >= n + 2:
            break
        h.wait_bits(8)
    h.wait_bits(400)
    chip = b.chip
    assert not (chip.flags & usbdev.F_STAGED and not chip.cores[0].txf), f"{what}: F_STAGED over an empty TX FIFO"
    assert not chip.flags & 1 << usbdev.FLAGS["F_IN"], f"{what}: F_UNDERRUN, a packet sent unasked and cut short"


def restage_race(concurrent, resume, hold_at):
    """The scenario: the host takes the last DATA of a read but its ACK is
    lost, so the device must re-stage it 100 us on; the host has moved on
    (OUT status, then a zero-length REQ_WRITE's SETUP, which clears F_GOTACK
    and flushes the TX FIFO). hold_at(op, prev, prev2, ctx) deschedules the
    software at one access; the SETUP goes on the bus while it is held, whole
    or token first, and the software resumes. Returns (h, sw, b, unanswered
    INs in the status stage). The new request was SET_CONFIGURATION until
    branch usb-config-prestage, where usb_fs_dev answers that status stage
    itself and would hide the software's races; the zero-length write's is
    still the software's."""
    h, sw = enumerated()
    b = concurrent(h, sw)
    data = bytes(range(0xB0, 0xB8))
    checked_write(h, 5, data, "8-byte write")
    h.setup(5, read_req(8))
    assert h.data_in(5, 0, ack=False) == (usb.pid_byte(usb.PID_DATA1), data)     # ACK lost
    h.data_out(5, 0, usb.PID_DATA1, b"")
    ctx = {"go": False, "stale": (usb.PID_DATA1, data), "sw": sw, "b": b}
    b.hold = lambda op, prev, prev2: not ctx["go"] and hold_at(op, prev, prev2, ctx)
    for _ in range(5000):
        if b.held:
            break
        h.wait_bits(8)
    assert b.held, "the software never got to the hold"
    req = write_req(0)                                  # its status ZLP: the software's
    if resume == "after the SETUP":
        h.setup(5, req)
        ctx["go"] = True
    else:
        t = h.send(usb.token(usb.PID_SETUP, 5, 0))
        ctx["go"] = True
        t = h.send(usb.data_packet(usb.PID_DATA0, req), t, h.ipd)
        assert h.listen(t) == usb.handshake(usb.PID_ACK)
    n = len(h.log)
    status_in(h, 5, "zero-length write", 1000)
    unanswered = sum(1 for e in h.log[n:] if e == ("IN", None, None))
    staged_over_empty(h, b, "after the zero-length write")
    checked_write(h, 5, data[::-1], "8-byte write after")
    assert checked_read(h, 5, 8, "8-byte read after") == data[::-1]
    staged_over_empty(h, b, "after the round trip")
    return h, sw, b, unanswered


def at_decision(op, prev, prev2, ctx):
    """The flags read after pop_all with which the software decides to re-stage."""
    sw, b = ctx["sw"], ctx["b"]
    return (op == FSET_READ and bool(prev) and prev[0] == "pop_all" and sw.unacked == ctx["stale"]
            and sw.sent_at is not None and b.now_us() - sw.sent_at >= 100)


@pytest.mark.parametrize("resume", ["after the SETUP", "after its token"])
@pytest.mark.parametrize("where", list(HOLDS))
def test_no_restage_after_a_setup(concurrent, where, resume):
    """restage_race held at one named point of the re-stage (before it reads
    the flags to decide; at the first write of stage(), both checks passed;
    between the flags and RX-level reads of the check before the FSET; at the
    FSET, the words pushed; just after the FSET; between the two reads of the
    check after it). Whatever the point, the zero-length write's status IN must
    get one zero-length DATA1 after NAKs only, never the old DATA (left in the
    TX FIFO behind the SETUP's flush) and never silence (UNANSWERED)."""
    access, before, before2 = HOLDS[where]

    def hold_at(op, prev, prev2, ctx):
        if where == "at the decision":
            return at_decision(op, prev, prev2, ctx)
        if op != access or prev != before or ctx["sw"].unacked != ctx["stale"]:
            return False
        return before2 is None or (bool(prev2) and prev2[:len(before2)] == before2)
    h, sw, b, unanswered = restage_race(concurrent, resume, hold_at)
    assert unanswered == UNANSWERED.get((where, resume), 0), f"{where}, {resume}: {unanswered} unanswered INs"
    if where == "at the decision":
        assert sw.tx_clears == 0, "the first check should have kept the old DATA out of the TX FIFO"


@pytest.mark.parametrize("resume", ["after the SETUP", "after its token"])
@pytest.mark.parametrize("n", range(26))
def test_restage_held_at_every_access(concurrent, n, resume):
    """restage_race held at the n-th SPI access from the re-stage decision
    (n = 0 is the decision's flags read): every access of the decision, the
    pushes, both checks, the FSET and the service loops after, not only the
    named points (verifier 040, note 3). With the got_in guard every one gives
    0 unanswered INs; before it, n = 8, 9 and 10 gave 2, 8 and 1."""
    seen = {"n": None, "last": None}

    def hold_at(op, prev, prev2, ctx):
        if seen["n"] is None:
            if not at_decision(op, prev, prev2, ctx):
                return False
            seen["n"], seen["last"] = 0, op
        elif op is not seen["last"]:
            seen["n"] += 1
            seen["last"] = op
        return seen["n"] == n
    h, sw, b, unanswered = restage_race(concurrent, resume, hold_at)
    assert unanswered == 0, f"access {n}, {resume}: {unanswered} unanswered INs"


@pytest.mark.parametrize("wlength", [8, 16])
def test_descriptor_ending_at_wlength_has_no_zlp(concurrent, wlength):
    """GET_DESCRIPTOR whose wLength is a multiple of 8 and shorter than the
    descriptor: the data stage ends at wLength, so no zero-length packet
    follows (USB 2.0 §5.5.3). A ZLP staged anyway waits in the TX FIFO for a
    SETUP to flush it, and when the next SETUP races the software it is
    stale: here a zero-length write follows at every gap of the sweep."""
    h, sw = enumerated()
    concurrent(h, sw)
    req = bytes([0x80, 6, 0, 1, 0, 0, wlength, 0])
    for k, gap in enumerate(GAPS):
        h.setup(5, req)
        out, toggle = b"", 1
        while len(out) < wlength:
            pid, payload = h.data_in(5, 0)
            assert pid == usb.pid_byte(usb.PID_DATA1 if toggle else usb.PID_DATA0)
            toggle ^= 1
            out += payload
        h.data_out(5, 0, usb.PID_DATA1, b"")
        assert out == usbdev.DEVICE_DESC[:wlength]
        h.wait_bits(gap)
        checked_write(h, 5, b"", f"zero-length write {k}, after GET_DESCRIPTOR(device, {wlength})")


TRANSFERS = {   # name -> (request, the bytes the host writes or None, the bytes it reads)
    "zero-length write": (write_req(0), b"", None),
    "8-byte write": (write_req(8), bytes(range(8)), None),
    "GET_DESCRIPTOR(device, 8)": (bytes([0x80, 6, 0, 1, 0, 0, 8, 0]), None, 8),
    "GET_DESCRIPTOR(device, 16)": (bytes([0x80, 6, 0, 1, 0, 0, 16, 0]), None, 16),
    "GET_DESCRIPTOR(device, 18)": (bytes([0x80, 6, 0, 1, 0, 0, 18, 0]), None, 18),
}


@pytest.mark.parametrize("name", list(TRANSFERS))
def test_nothing_left_staged_after_a_transfer(concurrent, name):
    """After a transfer ends and the software has caught up, the device has
    nothing staged: F_STAGED clear, the TX FIFO empty, nothing pending. A
    packet staged for a stage the host will not ask for (a second status ZLP;
    a ZLP after a data stage that reached wLength) is a bug even when the
    checks around FSET keep it off the bus, which is all the sweeps above see."""
    h, sw = enumerated()
    b = concurrent(h, sw)
    req, out, n = TRANSFERS[name]
    if out is not None:
        checked_write(h, 5, out, name)
    else:
        h.setup(5, req)
        got, toggle = b"", 1
        while len(got) < n:
            pid, payload = h.data_in(5, 0)
            assert pid == usb.pid_byte(usb.PID_DATA1 if toggle else usb.PID_DATA0)
            toggle ^= 1
            got += payload
        h.data_out(5, 0, usb.PID_DATA1, b"")
        assert got == usbdev.DEVICE_DESC[:n]
    h.wait_bits(400)
    chip = b.chip
    assert not chip.flags & usbdev.F_STAGED and not chip.cores[0].txf, \
        f"{name}: left staged: F_STAGED {bool(chip.flags & usbdev.F_STAGED)}, TX FIFO {len(chip.cores[0].txf)} words"
    assert sw.unacked is None and not sw.pending, f"{name}: the software still holds {sw.unacked}, {sw.pending}"
