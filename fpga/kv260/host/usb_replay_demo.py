#!/usr/bin/env python3
"""USB enumeration of the autonomous device (firmware/usb_enum.pasm) on the
KV260 build of the chip RTL, with no PC: pe_lab's pattern generator replays a
model-computed USB host on the D+/D- pads (uio0/uio1) and core 1 records what
is on the pads. Run as root on the Kria with the 48 MHz app (protoemu48)
loaded. Nothing leaves the FPGA; no PMOD pin is enabled.

    python3 fpga/kv260/host/usb_replay_demo.py --plans up.json [scenarios]                  # 1. host model runs (workstation)
    python3 fpga/kv260/host/usb_replay_demo.py --model --plans up.json --save m.json        # 2. the replay on the cycle model
    scp up.json ubuntu@kria.[tailnet domain redacted]:usb_logs/
    ssh ubuntu@kria.[tailnet domain redacted] sudo python3 -u protoemu48/fpga/kv260/host/usb_replay_demo.py \\
        --expect 60b0692 --plans usb_logs/up.json --save usb_logs/hw.json                   # 3. the board
    python3 fpga/kv260/host/usb_replay_demo.py compare hw.json m.json                       # 4. off the board
    python3 fpga/kv260/host/usb_replay_demo.py sigrok hw.json                               #    sigrok's USB decoders

Scenarios (each at low speed `ls-*` and full speed `fs-*`): enum (the
standard sequence: device descriptor's first 8 bytes, SET_ADDRESS, device and
configuration descriptors, a string descriptor STALLed, SET_CONFIGURATION,
GET_STATUS), linux (usb_demo.linux_flow: 64-byte read, 10 ms bus reset, ...,
GET_CONFIGURATION), faults (lost and damaged handshakes and DATA, the host
retrying), reset (bus resets mid-enumeration, at six phases, and a 3 us one),
ack (malformed ACKs: dribble, extra bytes, partial), stall (unsupported
requests, other addresses, bad CRC5/CRC16, wLength 0..255), addr
(SET_ADDRESS's status ACK lost, then a SETUP or an IN first).

Method. (1) *Plan*: the scenario runs on the cycle model with an independent
host model in the loop (pe.usbhost.ControlHost: low speed at 32 clocks per
bit; full speed the same transaction logic at 4 clocks per bit on the clock
grid, SOF every ms). The host's own line levels (what it drives, or the idle
J the pull-up gives when nobody drives) are recorded per clock, cut into
segments at idle points the scenario marks, each small enough for the
generator (<= 1000 entries) and the capture. (2) *Replay*: usb_enum is
loaded and started on core 0 once; for each segment, core 1 is restarted
with the observer, the generator plays the host's levels on uio0/uio1 at the
exact clocks (relative to a trigger on uio2), and one READ_FIXED burst drains
the observer. The device is never touched after its start (the host port
only restarts core 1 and drains its FIFO between segments). (3) *Judge*:
every edge on the pads relative to the trigger, and the packets decoded from
them (pe.usb; sigrok off the board), against the plan: the device's packets
must equal the model's bit for bit, with their timing, and the device must
end in the model's state (address, Configured).

Observers: low speed, firmware/la_capture (1 tick = 1 clock; edges >= 7
clocks apart are timestamped exactly; LS edges are >= 32 apart). Full speed
(4 clocks per bit) is under la_capture's 7-tick dead time, so core 1 runs
host/usb_sampler.pasm: `in pins, 3` every clock (uio0-2), 10 samples per
word, drained by a quad burst at SCK = clk (8 clocks per word), started right
after the generator so that sampling (from the trigger) never stalls: a
0.6 ms window per segment, with full clock resolution.

Pads in kv260_top: uio_in = uio_oe ? uio_out : generator, so the device's
drive wins while it drives and the generator plays the rest of the bus. The
electrical picture is simplified: digital levels on FPGA fabric, no USB
transceiver or PHY, no single-ended receivers or differential threshold, no
pull-up/pull-down resistors (the idle J is the generator's value), no
contention model. Every pad change on the FPGA is launched by a clock edge,
so the line unit's falling-edge sample sees the next rising edge's value: the
model runs with uio_mid = uio_in (`sync_mid`) to match.

--plans keeps the host-model runs (keyed by firmware, scenario and these
constants) so the Kria reuses them; --save keeps every capture; `compare`
lists equal/different per scenario; `sigrok` decodes the saved captures with
sigrok's usb_signalling + usb_packet against the plan's packets.
"""
import argparse
import gc
import hashlib
import json
import math
import pathlib
import random
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import postfreeze_demo as pfd  # noqa: E402  (sets up sys.path for tools/)
from postfreeze_demo import FIFO, STATUS, PG, GPIO, HwRig, ModelRig, check, fw  # noqa: E402
from pe import asm, host, la, sim, usb, usbdev, usbenum  # noqa: E402
from pe.modelhost import ModelHost  # noqa: E402
from pe.usbhost import ControlHost, Stall  # noqa: E402

F_USB = 48e6                      # the USB firmware's clock (protoemu48)
BIT = {"low": 32, "full": 4}      # clocks per bit
TRIG = 2                          # uio2: the generator's trigger
J = 0b10                          # idle J at both speeds (uio1 high): low speed D- (pull-up) on uio1,
                                  # full speed D+ (pull-up) on uio1
STATE = {0b10: usb.J, 0b01: usb.K, 0b00: usb.SE0}
T_TRIG = 4800                     # generator clocks from its start to the trigger (100 us)
LEAD = 64                         # trigger -> the segment's start (s0): la_capture stamps edges >= 7 ticks apart
PG_MAX = 1000                     # generator entries per segment (1024 in pe_lab)
LA_MASK = 0b111
SAMPLES = 10                      # usb_sampler: samples (3 bits) per word
FS_WINDOW = 4096 * 8 - T_TRIG - 1600    # clocks of samples a quad burst at SCK = clk keeps after the trigger
VERSION = "usb-replay-1"
DEV, CFG = usbdev.DEVICE_DESC, usbdev.CONFIG_DESC
PLANS = {"data": {}, "made": 0, "used": 0}


def sources_key():
    """Plans depend on the scenario and plan code here (not the replay or the
    judge), the host models and the cycle model."""
    import inspect
    objs = [sync_mid, FsClockHost, Recorder, segment_cost, make_segments, make_plan, owner_at, ack_states,
            poll_in, enumerate_flow, ctl_write, dev_prog, dev_load_writes, dev_state, sc_dev,
            make_dev_plan] + list(SCENARIOS.values())
    text = "".join(inspect.getsource(o) for o in objs).encode()
    text += b"".join((HERE.parents[2] / f).read_bytes() for f in (
        "tools/pe/usbhost.py", "tools/pe/usb.py", "tools/pe/sim.py", "tools/pe/line.py", "tools/pe/usbenum.py",
        "tools/pe/usbdev.py", "firmware/usb_enum.pasm", "firmware/usb_ls_dev.pasm", "firmware/usb_fs_dev.pasm"))
    return hashlib.sha1(text).hexdigest()[:12]
QUAD_RD_FIXED = 0x03 | 0x08 | 0x40


def sync_mid(chip):
    """FPGA-synchronous pads: pe_lab's generator and the chip's own outputs
    change on rising clock edges, so the line unit's falling-edge flop sees
    the value the next rising edge sees (uio_mid = uio_in). The model's
    default (the previous cycle's pads) is a change just after a falling
    edge, as from an asynchronous host."""
    step = chip.step

    def s(ui_in=0, uio_in=0, *a, **kw):
        if kw.get("uio_mid") is None and kw.get("ui_mid") is None:
            kw["uio_mid"] = uio_in
        return step(ui_in, uio_in, *a, **kw)
    chip.step = s
    return chip


# ================================================================ hosts

class FsClockHost(ControlHost):
    """pe.usbhost.ControlHost's control transfers (toggles, retries, STALL,
    faults) at full speed on the clock grid: 4 clocks per bit (12 Mbit/s at
    48 MHz), every edge at a clock edge as the generator plays it, a SOF token
    every ms. pe.usbfs.FsControlHost places edges in continuous time instead
    (jitter, frequency offset); the generator can't."""

    def __init__(self, mh, **kw):
        kw.setdefault("gap_bits", 4)
        super().__init__(mh, bit=4, **kw)
        self.frame = 0

    def gap(self, bits=None):
        self._steps(self.gap_bits if bits is None else bits)
        if self.keepalive and self.mh.chip.cycle >= self.next_keepalive:
            self._send(usb.token(0x5, self.frame & 0x7F, (self.frame >> 7) & 0xF))    # SOF
            self.frame = (self.frame + 1) & 0x7FF
            self.next_keepalive = self.mh.chip.cycle + 48000
            self._steps(self.gap_bits)


class Recorder:
    """Per clock: the host side's level on uio0/1 (its drive, else the pull-up's
    idle level), the chip's drive (oe, out) and the resolved pads."""

    def __init__(self, mh):
        self.mh = mh
        self.gen, self.dev, self.pads = [], [], []
        step = mh.chip.step

        def s(ui_in=0, uio_in=0, *a, **kw):
            c = mh.chip.cycle
            e = mh.ext_oe & 3
            g = ((mh.uio & e) | (mh.pull_up & ~e)) & 3
            _, out, oe = mh.chip.outputs()
            d = (oe & 3, out & 3)
            p = uio_in & 3
            if not self.gen or self.gen[-1][1] != g:
                self.gen.append((c, g))
            if not self.dev or tuple(self.dev[-1][1:]) != d:
                self.dev.append((c,) + d)
            if not self.pads or self.pads[-1][1] != p:
                self.pads.append((c, p))
            return step(ui_in, uio_in, *a, **kw)
        mh.chip.step = s


# ================================================================ scenarios
# Each runs the host model and calls mark() at idle points where a segment may end.

def ctl_write(addr, req, value=0):
    return bytes([0x00, req, value & 0xFF, value >> 8, 0, 0, 0, 0])


def enumerate_flow(h, mark, addr=5, strings=True):
    """test_usb_autonomous.enumerate_flow, marked between transfers."""
    check(h.get_descriptor(0, 1, 8) == DEV[:8], "device descriptor (8)")
    mark()
    h.control_write(0, ctl_write(0, 5, addr))
    mark()
    check(h.get_descriptor(addr, 1, 18) == DEV, "device descriptor")
    mark()
    check(h.get_descriptor(addr, 2, 9) == CFG[:9], "configuration header")
    mark()
    check(h.get_descriptor(addr, 2, CFG[2]) == CFG, "configuration")
    mark()
    if strings:
        try:
            h.get_descriptor(addr, 3, 255)
            raise AssertionError("string descriptor not STALLed")
        except Stall:
            pass
        mark()
    h.control_write(addr, ctl_write(0, 9, 1))
    mark()
    check(h.control_read(addr, bytes([0x80, 0, 0, 0, 0, 0, 2, 0])) == b"\0\0", "GET_STATUS")
    mark()


def sc_enum(h, mark, speed):
    enumerate_flow(h, mark)
    return dict(address=5, configured=True, pending_address=False, stalled=False)


def sc_linux(h, mark, speed):
    """usb_demo.linux_flow: what Linux's hub driver does (new scheme), 10 ms bus reset."""
    check(h.get_descriptor(0, 1, 64) == DEV, "64-byte read at address 0")
    mark()
    h.bus_reset(cycles=480000, recovery=480000 if speed == "low" else 48000)
    mark()
    h.control_write(0, ctl_write(0, 5, 7))
    mark()
    check(h.get_descriptor(7, 1, 18) == DEV, "device descriptor")
    mark()
    cfg = h.get_descriptor(7, 2, 9)
    check(cfg == CFG[:9], "configuration header")
    mark()
    check(h.get_descriptor(7, 2, cfg[2]) == CFG, "configuration")
    mark()
    h.control_write(7, ctl_write(0, 9, 1))
    mark()
    check(h.control_read(7, bytes([0x80, 8, 0, 0, 0, 0, 1, 0])) == b"\x01", "GET_CONFIGURATION")
    mark()
    return dict(address=7, configured=True, pending_address=False, stalled=False)


def sc_faults(h, mark, speed):
    """test_lost_acks_are_retried's steps, with 0-2 of each fault per step (seeded)."""
    rng = random.Random(7)
    steps = [lambda: h.get_descriptor(0, 1, 8) == DEV[:8],
             lambda: h.control_write(0, ctl_write(0, 5, 9)) is None,
             lambda: h.get_descriptor(9, 1, 18) == DEV,
             lambda: h.get_descriptor(9, 2, 9) == CFG[:9],
             lambda: h.get_descriptor(9, 2, 255) == CFG,
             lambda: h.control_write(9, ctl_write(0, 9, 1)) is None]
    for i, step in enumerate(steps):
        for kind in ("lose_handshake", "lose_host_ack", "bad_data"):
            h.faults[kind] = rng.randrange(0, 3)
        check(step(), f"step {i}")
        mark()
    check(h.injected, "no fault injected")
    return dict(address=9, configured=True, pending_address=False, stalled=False)


SHORT_RESET = dict(cycles=48 * 50, recovery=48 * 200)    # 50 us SE0 (the device acts on 2.5 us)


def sc_reset(h, mark, speed):
    """test_bus_reset_mid_enumeration (data stage, after the address, configured),
    test_bus_reset_at_any_phase (offsets 0-5 clocks), test_bus_reset_unconfigures (3 us)."""
    mh = h.mh
    for when in ("data_stage", "after_address", "configured"):
        h.control_write(0, ctl_write(0, 5, 5))
        mark()
        if when == "data_stage":
            h.setup(5, bytes([0x80, 6, 0, 2, 0, 0, 18, 0]))
            check(h.data_in(5, 1) == CFG[:8], "first configuration packet")
        elif when == "configured":
            h.control_write(5, ctl_write(0, 9, 1))
        mark()
        h.bus_reset(**SHORT_RESET)
        h._send(usb.token(usb.PID_IN, 5, 0))           # back at the default address
        check(h._response() is None, f"{when}: address 5 answered after the reset")
        h.gap()
        mark()
        check(h.get_descriptor(0, 1, 18) == DEV, f"{when}: address 0 after the reset")
        mark()
    for offset in range(6):
        h.control_write(0, ctl_write(0, 5, 5))
        mark()
        h.control_write(5, ctl_write(0, 9, 1))
        mh.step(offset)
        h.bus_reset(**SHORT_RESET)
        h._send(usb.token(usb.PID_IN, 5, 0))
        check(h._response() is None, f"offset {offset}: address 5 answered after the reset")
        h.gap()
        mark()
    enumerate_flow(h, mark, addr=3, strings=False)
    h.bus_reset(cycles=48 * 3, recovery=48 * 100)      # 3 us: just over the 2.5 us threshold
    mark()
    return dict(address=0, configured=False, pending_address=False, stalled=False)


DESCRIPTOR_REQUEST = bytes.fromhex("8006000100001200")
FIRST = usb.data_packet(usb.PID_DATA1, DEV[:8])
SECOND = usb.data_packet(usb.PID_DATA0, DEV[8:16])


def ack_states(tail=b"", stretch=0, suffix=""):
    """test_usb_ack's ACK waveforms: extra bytes, a stretched final level
    (dribble), or a partial byte (raw NRZI bits before the EOP)."""
    states = usb.encode(usb.handshake(usb.PID_ACK) + tail)
    if stretch:
        last = states[-4]
        states = states[:-3] + [last] * stretch + states[-3:]
    if suffix:
        states = states[:-3]
        level = states[-1]
        for b in suffix:
            if b == "0":
                level = usb.K if level == usb.J else usb.J
            states.append(level)
        states += [usb.SE0, usb.SE0, usb.J]
    return states


ACK_CASES = [("nominal", {}, True), ("dribble-1", dict(stretch=1), True), ("dribble-2", dict(stretch=2), True),
             ("dribble-3", dict(stretch=3), True), ("extra-00", dict(tail=b"\x00"), False),
             ("extra-55", dict(tail=b"\x55"), False), ("extra-ff", dict(tail=b"\xff"), False),
             ("extra-3-bytes", dict(tail=b"\xff\xff\xff"), False)] + \
            [(f"short-{s}", dict(suffix=s), False) for s in ("0", "01", "10", "001", "101", "110", "1111")]


def poll_in(h):
    h._send(usb.token(usb.PID_IN, 0, 0))
    r = h._response()
    check(r is not None and len(r) >= 3 and r[-2:] == usb.crc16(r[1:-2]), f"no complete DATA reply: {r!r}")
    return r


def sc_ack(h, mark, speed):
    """test_usb_ack: after the first DATA of a GET_DESCRIPTOR, the host's ACK
    nominal, dribbled (accepted: the next IN gets the second packet) or
    malformed (rejected: the same packet again, then a nominal ACK advances it)."""
    h.keepalive = False
    verdicts = []
    def ack(name="nominal", **kw):
        # the wire record (for sigrok): the ACK's bytes, or a placeholder for a malformed one
        h.wire.append(usb.handshake(usb.PID_ACK) if not kw else ["raw", name])
        h._drive(ack_states(**kw))
        h.gap()
    for name, kw, accepted in ACK_CASES:
        h.setup(0, DESCRIPTOR_REQUEST)
        check(poll_in(h) == FIRST, f"{name}: first packet")
        h._steps(4)
        ack(name, **kw)
        r = poll_in(h)
        verdicts.append((name, "accepted" if r == SECOND else "rejected" if r == FIRST else r.hex()))
        check(r == (SECOND if accepted else FIRST), f"{name}: next reply {r.hex()}")
        if not accepted:
            h._steps(4)
            ack()
            check(poll_in(h) == SECOND, f"{name}: a nominal ACK after the retry")
        h._steps(4)
        ack()
        mark()
    h.verdicts = verdicts
    return dict(address=0, configured=False, pending_address=False, stalled=False)


UNSUPPORTED = {
    "string descriptor": (bytes([0x80, 6, 0, 3, 0, 0, 255, 0]), b""),
    "device qualifier": (bytes([0x80, 6, 0, 6, 0, 0, 10, 0]), b""),
    "HID report descriptor": (bytes([0x81, 6, 0, 0x22, 0, 0, 64, 0]), b""),
    "vendor IN": (bytes([0xC0, 1, 0, 0, 0, 0, 4, 0]), b""),
    "GET_INTERFACE": (bytes([0x81, 10, 0, 0, 0, 0, 1, 0]), b""),
    "SET_FEATURE": (bytes([0x00, 3, 1, 0, 0, 0, 0, 0]), None),
    "SET_CONFIGURATION 2": (bytes([0x00, 9, 2, 0, 0, 0, 0, 0]), None),
    "class SET_IDLE": (bytes([0x21, 10, 0, 0, 0, 0, 0, 0]), None),
    "SET_DESCRIPTOR with data": (bytes([0x00, 7, 0, 1, 0, 0, 8, 0]), bytes(8)),
}


def sc_stall(h, mark, speed):
    """test_unsupported_requests_are_stalled, test_other_addresses_and_bad_tokens_are_ignored,
    test_wlength_truncates_the_transfer."""
    for name, (req, data) in UNSUPPORTED.items():
        try:
            if req[0] & 0x80:
                h.control_read(0, req)
            else:
                h.control_write(0, req, data or b"")
            raise AssertionError(f"{name}: not STALLed")
        except Stall:
            pass
        try:
            h.data_in(0, 1)
            raise AssertionError(f"{name}: STALL not kept until the next SETUP")
        except Stall:
            pass
        check(h.get_descriptor(0, 1, 18) == DEV, f"{name}: the next SETUP clears the STALL")
        mark()
    for tok in (usb.token(usb.PID_IN, 3, 0), usb.token(usb.PID_IN, 0, 1), usb.token(usb.PID_SETUP, 1, 0)):
        h._send(tok)
        check(h._response() is None, f"{tok.hex()} answered")
        h.gap()
    bad = bytearray(usb.token(usb.PID_IN, 0, 0))
    bad[2] ^= 0x40                                   # a CRC5 bit
    h._send(bytes(bad))
    check(h._response() is None, "bad CRC5 answered")
    h.gap()
    h._send(usb.token(usb.PID_SETUP, 0, 0))          # SETUP DATA with a bad CRC16: no handshake
    h._steps(2)
    pkt = bytearray(usb.data_packet(usb.PID_DATA0, bytes([0x80, 6, 0, 1, 0, 0, 18, 0])))
    pkt[-1] ^= 1
    h._send(bytes(pkt))
    check(h._response() is None, "bad CRC16 acknowledged")
    h.gap()
    check(h.get_descriptor(0, 1, 18) == DEV, "after the bad packets")
    mark()
    for wl in (0, 1, 2, 7, 8, 9, 15, 16, 17, 18, 19, 64, 255):
        check(h.get_descriptor(0, 1, wl) == DEV[:wl], f"device descriptor wLength {wl}")
        check(h.get_descriptor(0, 2, wl) == CFG[:wl], f"configuration wLength {wl}")
        mark()
    return dict(address=0, configured=False, pending_address=False, stalled=False)


def sc_addr(h, mark, speed):
    """test_set_address_status_ack_lost (a SETUP first) and _then_in_first (an IN
    first, answered within the turnaround), then the rest of the enumeration."""
    h.faults["lose_host_ack"] = 1
    h.control_write(0, ctl_write(0, 5, 77))
    check(h.injected == {"lose_host_ack": 1}, "status ACK not lost")
    mark()
    check(h.get_descriptor(77, 1, 18) == DEV, "SETUP first to the new address")
    mark()
    h._send(usb.token(usb.PID_IN, 0, 0))             # the old address is gone
    check(h._response() is None, "the old address answered")
    h.gap()
    mark()
    h.bus_reset(**SHORT_RESET)
    mark()
    h.faults["lose_host_ack"] = 1
    h.control_write(0, ctl_write(0, 5, 78))
    mark()
    h._send(usb.token(usb.PID_IN, 78, 0))            # an IN first
    r = h._response()
    check(r == bytes([usb.pid_byte(usb.PID_DATA1), 0, 0]), f"IN first: {r!r}")
    h.ta_in_first = h.turnarounds[-1]
    h._steps(2)
    h._send(usb.handshake(usb.PID_ACK))
    h.gap()
    mark()
    check(h.get_descriptor(78, 1, 18) == DEV, "device descriptor at 78")
    mark()
    h.control_write(78, ctl_write(0, 9, 1))
    mark()
    return dict(address=78, configured=True, pending_address=False, stalled=False)


SCENARIOS = {"enum": sc_enum, "linux": sc_linux, "faults": sc_faults, "reset": sc_reset,
             "ack": sc_ack, "stall": sc_stall, "addr": sc_addr}
NAMES = [f"{s}-{n}" for s in ("ls", "fs") for n in list(SCENARIOS) + ["dev"]]
SPEED = {"ls": "low", "fs": "full"}


# ================================================================ plans

def plan_key(*parts):
    return hashlib.sha1(json.dumps(parts, sort_keys=True, default=repr).encode()).hexdigest()[:16]


def prog_for(speed):
    return usbenum.assemble(speed)


def segment_cost(plan, s0, s1):
    """(feasible, info) for the segment [s0, s1) of a plan: generator entries,
    the observer's needs."""
    speed = plan["speed"]
    gen = [(c, v) for c, v in plan["gen"] if s0 < c < s1]
    pads = [c for c, _ in plan["pads"] if s0 < c < s1]
    last = max([c for c, _ in gen] + pads + [s0]) - s0
    info = {"entries": len(gen) + 1, "edges": len(pads), "last": last}
    if info["entries"] > PG_MAX:
        return False, info
    if speed == "full":
        # the sampler sees FS_WINDOW clocks after the trigger: every device edge must be
        # inside; host edges after it (the end of a bus reset) go unobserved
        # the sampler sees FS_WINDOW clocks after the trigger: every edge must be inside but
        # the end of a long SE0 (a bus reset), the segment's last
        late = [c for c in pads if c - s0 > FS_WINDOW]
        info["unobserved"] = len(late)
        return len(late) == 0 or (len(late) == 1 and late[0] == pads[-1]), info
    # low speed: la_capture records (one per pad change, the trigger too) drained by a quad
    # burst at SCK = clk / div: long enough, and draining at least as fast as they come
    need = T_TRIG + LEAD + last + 20000
    div = max(1, math.ceil(need / (4096 * 8)))
    info["div"] = div
    if div > 255:
        return False, info
    P = 8 * div
    rec = [T_TRIG] + [T_TRIG + LEAD + c - s0 for c in pads]
    g = [k - (t // P) for k, t in enumerate(rec)]
    worst, lo = 0, g[0]
    for x in g:
        lo = min(lo, x)
        worst = max(worst, x - lo)
    info["fifo"] = worst + 2
    return worst + 2 <= 6 and len(rec) < 3800, info


def make_segments(plan, speed):
    """Greedy: extend each segment over the marked idle points while feasible."""
    marks = sorted(set([plan["start"]] + plan["marks"] + [plan["end"]]))
    segs, i = [], 0
    while i < len(marks) - 1:
        j = i + 1
        ok, info = segment_cost(plan, marks[i], marks[j])
        check(ok, f"{plan['name']}: the segment {marks[i]}..{marks[j]} does not fit: {info}")
        while j + 1 < len(marks) and plan.get("merge", True):
            ok2, info2 = segment_cost(plan, marks[i], marks[j + 1])
            if not ok2:
                break
            j, info = j + 1, info2
        segs.append(dict(s0=marks[i], s1=marks[j], **info))
        i = j
    return segs


def decode_packets(edges, bit, t_end):
    """Pad edges [(t, pads)] -> bus events: ("pkt", t_first_K, t_eop_j, bytes or '?'),
    ("se0", t, duration) for an SE0 from idle (keep-alive EOP or bus reset)."""
    ev, i, n = [], 0, len(edges)
    while i < n:
        t, p = edges[i]
        s = STATE.get(p, "?")
        if s == usb.K:                          # a packet: until SE0 -> J
            k, trace = i, []
            while k < n:
                trace.append(edges[k])
                if k > i and STATE.get(edges[k][1]) == usb.J and STATE.get(edges[k - 1][1]) == usb.SE0:
                    break
                k += 1
            states = [usb.J]
            for (ta, pa), (tb, _) in zip(trace, trace[1:]):
                states += [STATE.get(pa, "?")] * max(1, round((tb - ta) / bit))
            states.append(usb.J)
            try:
                data = usb.decode(states).hex()
            except ValueError:
                data = "?"
            ev.append(("pkt", t, trace[-1][0], data))
            i = k + 1
        elif s == usb.SE0:
            nxt = edges[i + 1][0] if i + 1 < n else t_end
            ev.append(("se0", t, nxt - t))
            i += 1
        else:
            i += 1
    return ev


DEV_FW = {"low": "usb_ls_dev", "full": "usb_fs_dev"}


def dev_prog(speed):
    """usb_ls_dev / usb_fs_dev: one core and the line unit, the device software on the host side."""
    return asm.assemble((pfd.FW / f"{DEV_FW[speed]}.pasm").read_text(), usbdev.defines())


def dev_load_writes(prog, speed):
    """test_usb_enum / test_usb_fs's device(): core 0 at 0, CRC16 IN tap, the line unit; started."""
    n = BIT[speed]
    w = host.load_program(0, prog, 0, clkdiv=1 << 8)
    w += [(sim.core_reg(0, sim.C_CRCCTRL), usbdev.CRCCTRL), (sim.A_LINEDIV, (n << 16) | (n << 8)),
          (sim.A_LINEPINS, 0), (sim.A_LINECFG, usbenum.LINECFG)]
    return w + host.start_writes(1)


def dev_state(sw):
    return dict(address=sw.address, requests=list(sw.requests), restaged=sw.restaged)


class RigPort:
    """pe.usbdev.DeviceSoftware's transport on a rig (the board's host port or
    the model's); now_us() is the plan's clock at the service being replayed,
    so the software's retry timer decides as it did in the plan."""

    def __init__(self, rig):
        self.rig, self.now = rig, 0.0

    def read(self, addr, pop=False):
        return self.rig.read(addr)

    def write(self, addr, v):
        self.rig.write(addr, v)

    def push(self, core, w):
        self.rig.write(FIFO(core), w)

    def pop_all(self, core):
        out = []
        while True:
            w = rd_fifo(self.rig, core)
            if not w:
                return out
            out += w

    def now_us(self):
        return self.now


def sc_dev(h, sw, idle, speed):
    """test_usb_enum's enumeration (64-byte device descriptor read at address 0,
    SET_ADDRESS 5, device and configuration descriptors, SET_CONFIGURATION),
    then test_lost_ack_restages_the_same_packet: the host takes DATA1 without
    an ACK, the software re-stages it after 100 us, the next IN gets it again."""
    mh, bit = h.mh, h.bit
    d = h.control_read(0, bytes([0x80, 6, 0, 1, 0, 0, 64, 0]), 64)
    check(d == DEV, f"device descriptor {d.hex()}")
    h.control_write_nodata(0, ctl_write(0, 5, 5))
    check(h.control_read(5, bytes([0x80, 6, 0, 1, 0, 0, 18, 0]), 18) == DEV, "device descriptor at 5")
    check(h.control_read(5, bytes([0x80, 6, 0, 2, 0, 0, CFG[2], 0]), CFG[2]) == CFG, "configuration")
    h.control_write_nodata(5, ctl_write(0, 9, 1))
    check(sw.address == 5, f"software address {sw.address}")
    h.setup(5, bytes([0x80, 6, 0, 1, 0, 0, 18, 0]))
    got = None
    for _ in range(20):                                # IN until data, not acknowledged
        h._send(usb.token(usb.PID_IN, 5, 0))
        got = h._listen(120 * bit)
        if got and got[0] == usb.pid_byte(usb.PID_DATA1):
            break
        h.idle(mh)
    check(got == usb.data_packet(usb.PID_DATA1, DEV[:8]), f"first packet {got!r}")
    h._gap(16)
    idle(mh)                                           # the software sees it sent, no ACK yet
    mh.step(round(120 * 48))
    idle(mh)                                           # 120 us later: re-staged
    pid, payload = h.data_in(5, 0)
    check((pid, payload) == (usb.pid_byte(usb.PID_DATA1), DEV[:8]) and sw.restaged == 1, "re-staged packet")
    pid, payload = h.data_in(5, 0)
    check((pid, payload) == (usb.pid_byte(usb.PID_DATA0), DEV[8:16]), "next packet")
    pid, payload = h.data_in(5, 0)
    check(payload == DEV[16:18], "last packet")
    h.data_out(5, 0, usb.PID_DATA1, b"")


def make_dev_plan(name, speed, prog, key):
    """usb_ls_dev / usb_fs_dev with pe.usbdev.DeviceSoftware serviced at every
    host idle point (a segment boundary on the board: the software runs there,
    between two generator plays)."""
    from pe.usbhost import UsbHost
    t = time.monotonic()
    bit = BIT[speed]
    mh = ModelHost()
    sync_mid(mh.chip)
    mh.apply(dev_load_writes(prog, speed))
    mh.pull_up = 0b10
    mh.step(100)
    rec = Recorder(mh)
    mh.step(200)
    sw = usbdev.DeviceSoftware(mh, prog, usbdev.DESCRIPTORS, 0, 0)
    marks, svc = [], []

    def idle(m):
        m.step(8 * bit)
        marks.append(m.chip.cycle - 1)
        svc.append((m.chip.cycle - 1, m.now_us()))
        sw.service(m)
    h = UsbHost(mh, bit=bit, idle=idle)
    h.wire = []
    send0 = h._send

    def send(packet):
        h.wire.append(bytes(packet))
        send0(packet)
    h._send = send
    listen0 = h._listen

    def listen(cycles):
        r = listen0(cycles)
        if r is not None:
            h.wire.append(r)
        return r
    h._listen = listen
    start = mh.chip.cycle - 1
    sc_dev(h, sw, idle, speed)
    mh.step(2000)
    end = mh.chip.cycle
    plan = {"key": key, "name": name, "kind": "dev", "merge": False, "speed": speed, "start": start, "end": end,
            "marks": [m for m in marks if start < m < end], "svc": svc,
            "gen": rec.gen, "dev": rec.dev, "pads": rec.pads,
            "wire": [("?" if p == b"?" else p.hex()) for p in h.wire],
            "log": [repr(e) for e in h.log], "state": dev_state(sw),
            "turnarounds": [], "injected": {}, "verdicts": None, "ta_in_first": None,
            "secs": round(time.monotonic() - t, 1)}
    plan["segments"] = make_segments(plan, speed)
    PLANS["data"][name] = plan
    PLANS["made"] += 1
    return plan


def make_plan(name):
    """The scenario on the cycle model with the host model in the loop."""
    sp, sc = name.split("-", 1)
    speed = SPEED[sp]
    prog = dev_prog(speed) if sc == "dev" else prog_for(speed)
    key = plan_key(VERSION, name, prog.words, T_TRIG, LEAD, PG_MAX, FS_WINDOW, SAMPLES, sources_key())
    hit = PLANS["data"].get(name)
    if hit and hit.get("key") == key:
        PLANS["used"] += 1
        return hit
    if sc == "dev":
        return make_dev_plan(name, speed, prog, key)
    t = time.monotonic()
    mh = ModelHost()
    sync_mid(mh.chip)
    mh.apply(usbenum.load_writes(prog, speed))
    mh.pull_up = 0b10
    mh.step(100)
    rec = Recorder(mh)
    mh.step(200)
    h = (ControlHost if speed == "low" else FsClockHost)(mh)
    start = mh.chip.cycle - 1             # segments start a clock before the bus's next change
    marks = []

    def mark():
        h._steps(8)                       # idle: the device is back in its idle loop
        marks.append(mh.chip.cycle - 1)
    want = SCENARIOS[sc](h, mark, speed)
    mh.step(2000)
    end = mh.chip.cycle
    state = usbenum.decode_state(mh.chip.read(sim.A_FSET), mh.chip.read(sim.core_reg(0, sim.C_R0 + 3)))
    check(state == want, f"{name}: model end state {state}, want {want}")
    plan = {"key": key, "name": name, "speed": speed, "start": start, "end": end,
            "marks": [m for m in marks if start < m < end],
            "gen": rec.gen, "dev": rec.dev, "pads": rec.pads,
            "wire": [("?" if p == b"?" else p.hex()) if isinstance(p, (bytes, bytearray)) else p for p in h.wire],
            "log": [repr(e) for e in h.log], "state": state,
            "turnarounds": h.turnarounds, "injected": h.injected,
            "verdicts": getattr(h, "verdicts", None), "ta_in_first": getattr(h, "ta_in_first", None),
            "secs": round(time.monotonic() - t, 1)}
    plan["segments"] = make_segments(plan, speed)
    PLANS["data"][name] = plan
    PLANS["made"] += 1
    return plan


def owner_at(plan, t):
    """'dev' if the chip drives uio0/1 at plan cycle t, else 'host'."""
    d = plan["dev"]
    lo, hi = 0, len(d) - 1
    while lo < hi:                         # last entry with cycle <= t
        mid = (lo + hi + 1) // 2
        if d[mid][0] <= t:
            lo = mid
        else:
            hi = mid - 1
    return "dev" if d[lo][1] & 3 else "host"


def seg_expect(plan, seg):
    """The plan's pads edges in a segment, relative to its start, and its events."""
    s0, s1 = seg["s0"], seg["s1"]
    pads = [(c - s0, p) for c, p in plan["pads"] if s0 < c < s1]
    lvl = [p for c, p in plan["pads"] if c <= s0][-1]
    pads = [(0, lvl)] + pads
    ev = decode_packets(pads, BIT[plan["speed"]], s1 - s0)
    ev = [e + (owner_at(plan, s0 + e[1]),) for e in ev]
    return pads, ev


# ================================================================ replay

class SyncModelRig(ModelRig):
    """postfreeze_demo's ModelRig (pe_lab's modes modelled) with FPGA-synchronous pads."""

    def __init__(self):
        super().__init__(F_USB)

    def model_clock(self, f):
        super().model_clock(f)
        sync_mid(self.mh.chip)


def go_burst(rig, core, n, div, quad=True):
    """Start the generator and, right after it, one READ_FIXED burst of core's RXF."""
    if rig.model:
        rig.pg_start()
        return rig.burst(core, n, div, quad)
    s, mem = rig.spim, rig.spim.mem
    from pe_spim import CMD, LEN, CFG, GO
    s.wait_idle()
    mem.wr(CMD, (QUAD_RD_FIXED if quad else 0x0B) | ((FIFO(core) & 0xFFFF) << 8))
    mem.wr(LEN, n)
    mem.wr(CFG, (div & 0xFF) | (int(quad) << 8) | (1 << 9))
    gc.disable()
    try:
        rig.lab.mem.wr(0x118, 1)          # PG_GO
        mem.wr(GO, 1)                     # the burst, a few us later
    finally:
        gc.enable()
    s.wait_idle(timeout=2.0)
    return mem.buf_read(n)


def rd_fifo(rig, core):
    n = (rig.read(STATUS(core)) >> 4) & 15
    if not n:
        return []
    w = rig.read(FIFO(core), n, fixed=True)
    return [w] if n == 1 else list(w)


def sampler_prog():
    return asm.assemble((HERE / "usb_sampler.pasm").read_text(), {"TRIG": TRIG})


def samples_to_edges(words, k0):
    """usb_sampler words (zeros = empty reads) -> pad edges [(t, uio[1:0])] with
    t = sample index + k0, and the number of samples."""
    out, n = [], 0
    for w in words:
        if not w:
            continue
        for i in range(SAMPLES):
            s = (w >> (3 * (SAMPLES - 1 - i))) & 7
            p = s & 3
            if not out or out[-1][1] != p:
                out.append((n + k0, p))
            n += 1
    return out, n


SAMPLER_K0 = 1      # sample 0 shows the pads 1 clock after the trigger's pad edge (the model; the board must agree)


def replay(rig, plan, store):
    """usb_enum on core 0 once; each segment played by the generator, observed by core 1."""
    speed, name = plan["speed"], plan["name"]
    bit = BIT[speed]
    prog = prog_for(speed)
    # both cores stopped, FIFOs cleared, the generator holding J
    rig.write(sim.A_CTRL, 0b11 << 16)
    rig.gpio_uio(0xFF)
    rig.lab_mode(PG)
    rig.pg_set(J)
    for a in (sim.A_OD, sim.A_POUT, sim.A_POE, sim.A_SYNCBYP):
        rig.write(a, 0)
    for addr, words in host.coalesce(host.pin_owner_writes({})):
        rig.write(addr, words)
    dev = plan.get("kind") == "dev"
    if dev:
        prog = dev_prog(speed)
        port = RigPort(rig)
        sw = usbdev.DeviceSoftware(port, prog, usbdev.DESCRIPTORS, 0, 0)
    for addr, words in host.coalesce(dev_load_writes(prog, speed) if dev else
                                     usbenum.load_writes(prog, speed, start=False)):
        rig.write(addr, words)
    if speed == "low":
        obs = fw("la_capture", MASK=LA_MASK)
    else:
        obs = sampler_prog()
    pfd.load(rig, 1, obs, in_pins=0, clkdiv=1 << 8)
    rig.write(sim.A_CTRL, 0b01 | (0b01 << 8))          # core 0: usb_enum, from here on never touched
    if rig.model:
        rig.wait(200 / F_USB)
    out_segs = []
    services = {int(c): us for c, us in plan.get("svc", [])}
    for k, seg in enumerate(plan["segments"]):
        s0, s1 = seg["s0"], seg["s1"]
        if dev and s0 in services:
            # the device software, as the plan ran it here (its clock: the plan's)
            port.now = services[s0]
            sw.service()
        sched = [(T_TRIG, J | (1 << TRIG))]
        for c, v in plan["gen"]:
            if s0 < c < s1:
                t = T_TRIG + LEAD + c - s0
                vv = v | (1 << TRIG)
                if t == sched[-1][0]:
                    sched[-1] = (t, vv)
                else:
                    sched.append((t, vv))
        check(len(sched) <= 1024, f"segment {k}: {len(sched)} generator entries")
        rig.write(sim.A_CTRL, 0b01 | (0b10 << 16))     # core 1 stopped, its FIFOs cleared; core 0 runs on
        rig.pg_set(J)                                   # trigger low (the lines stay J)
        rig.pg_load(sched, init=J)
        rig.write(sim.A_CTRL, 0b11 | (0b10 << 8))       # core 1 restarted
        if rig.model:
            rig.wait(100 / F_USB)
        if speed == "low":
            words = go_burst(rig, 1, 4096, seg["div"], True)
            rig.wait(1.5 * 65536 / F_USB)               # an idle record: the end state, timed
            words += rd_fifo(rig, 1)
            cap = la.decode_records(words, LA_MASK)
            check(cap.exact, f"segment {k}: la_capture gap(s) {cap.gaps}")
            tr = next(t for t, s in cap.edges if s >> TRIG & 1)
            lvl = [s for t, s in cap.edges if t <= tr][-1] & 3
            edges = [(0, lvl)] + [(t - tr - LEAD, s & 3) for t, s in cap.edges if t > tr]
            # la_capture stamps edges >= 7 ticks after the previous record exactly
            close = [b[0] for a, b in zip(cap.edges, cap.edges[1:]) if b[0] - a[0] < 7]
            t_obs = cap.end - tr - LEAD
            info = {"records": sum(1 for w in words if w), "div": seg["div"], "close": close}
        else:
            words = go_burst(rig, 1, 4096, 1, True)
            lead = next((i for i, w in enumerate(words) if w), None)
            check(lead is not None and lead > 0, f"segment {k}: the burst started after the sampling "
                                                 f"(first word read {lead})")
            edges, n = samples_to_edges(words, SAMPLER_K0 - LEAD)
            edges = [(max(0, t), p) for t, p in edges]
            t_obs = n + SAMPLER_K0 - LEAD
            info = {"samples": n, "lead_words": lead}
        out_segs.append(dict(edges=edges, t_obs=t_obs, **info))
        if not rig.model:
            pass
    st = finish_state(rig)
    if dev:
        st = dev_state(sw)
    store_run(store, plan, out_segs, st)
    return out_segs, st


def finish_state(rig):
    """After the last segment: the device's address and flags (host port reads), then stop."""
    flags = rig.read(sim.A_FSET)
    r3 = rig.read(sim.core_reg(0, sim.C_R0 + 3))
    st = rig.read(STATUS(0))
    rig.write(sim.A_CTRL, 0b11 << 16)
    rig.lab_mode(GPIO)
    rig.gpio_uio(0xFF)
    for a in (sim.A_LINECFG,):
        rig.write(a, 0)
    check(not st & pfd.FAULT, f"core 0 faulted: STATUS {st:#x}")
    return usbenum.decode_state(flags, r3)


def store_run(store, plan, segs, state):
    if store is None:
        return
    store.setdefault("_captures", {})[plan["name"]] = {
        "speed": plan["speed"], "segments": [dict(edges=s["edges"], t_obs=s["t_obs"]) for s in segs]}


# ================================================================ judge

def judge(plan, segs, state, store):
    """Board (or model replay) against the plan. Yields report lines; raises on failure."""
    speed, bit = plan["speed"], BIT[plan["speed"]]
    tol = 0
    n_dev = n_host = 0
    d_dev, d_host = [], []
    mism = []
    det_pk = []
    tas = []
    for k, (seg, got) in enumerate(zip(plan["segments"], segs)):
        want_e, want_ev = seg_expect(plan, seg)
        t_obs = got["t_obs"]
        if speed == "full":
            # the sampler's window depends on when the burst started: judge the same FS_WINDOW clocks
            check(t_obs >= FS_WINDOW, f"segment {k}: {t_obs} clocks sampled after the trigger, under {FS_WINDOW}")
            t_obs = FS_WINDOW
        want_e = [x for x in want_e if x[0] <= t_obs]
        got_e = [tuple(x) for x in got["edges"] if x[0] <= t_obs]
        # every edge: same level sequence, times compared
        if [p for _, p in got_e] != [p for _, p in want_e]:
            i = next((i for i, (a, b) in enumerate(zip(got_e, want_e)) if a[1] != b[1]), min(len(got_e), len(want_e)))
            mism.append(f"segment {k}: edge sequence differs at edge {i} of {len(want_e)}/{len(got_e)}: "
                        f"board {got_e[max(0, i - 2):i + 3]} model {want_e[max(0, i - 2):i + 3]}")
            continue
        for (tg, _), (tw, _) in zip(got_e, want_e):
            own = owner_at(plan, seg["s0"] + tw)
            (d_dev if own == "dev" else d_host).append(tg - tw)
        got_ev = decode_packets(got_e, bit, t_obs)
        want_ev = [e for e in want_ev if e[1] <= t_obs]
        if len(got_ev) != len(want_ev):
            mism.append(f"segment {k}: {len(got_ev)} bus events, plan {len(want_ev)}")
            continue
        prev_eop = None
        for g, w in zip(got_ev, want_ev):
            if g[0] != w[0] or (g[0] == "pkt" and g[3] != w[3]):
                mism.append(f"segment {k}: event {g[:4]} != plan {w[:4]}")
            if w[0] == "pkt":
                if w[-1] == "dev":
                    n_dev += 1
                    if prev_eop is not None:
                        tas.append((g[1] - prev_eop) / bit)
                else:
                    n_host += 1
                det_pk.append((k, w[-1], g[1], g[3]))
                prev_eop = g[2]
            else:
                det_pk.append((k, "host", g[1], f"se0 {g[2]}"))
                prev_eop = None
    if mism:
        raise AssertionError("; ".join(mism[:4]) + (f" (+{len(mism) - 4} more)" if len(mism) > 4 else ""))
    span = lambda xs: f"{min(xs)}..{max(xs)}" if xs else "-"
    check(all(d == 0 for d in d_host), f"host edges off the generator's times: {span(d_host)}")
    check(all(abs(d) <= tol for d in d_dev), f"device edges board - model {span(d_dev)} clocks")
    check(state == plan["state"], f"end state {state}, model {plan['state']}")
    if store is not None:
        store.setdefault(plan["name"], {}).update(packets=det_pk, state=state)
    yield (f"{len(plan['segments'])} segments, {n_host} host packets replayed, {n_dev} device packets: "
           f"bytes equal to the model's; {len(d_dev)} device edges board - model {span(d_dev)} clocks, "
           f"{len(d_host)} host edges {span(d_host)}")
    if tas:
        ta = plan["turnarounds"]
        yield (f"device turnaround on the pads {min(tas):.2f}..{max(tas):.2f} bit times"
               + (f" (model host saw {min(ta):.2f}..{max(ta):.2f})" if ta else ""))
    yield f"end state {state} = the model's"


def summary_lines(plan):
    segs = plan["segments"]
    yield (f"plan: {plan['end'] - plan['start']} clocks ({(plan['end'] - plan['start']) / F_USB * 1e3:.2f} ms at "
           f"48 MHz), {len(plan['wire'])} packets on the wire, {len(segs)} segments "
           f"(generator entries {min(s['entries'] for s in segs)}..{max(s['entries'] for s in segs)})"
           + (f", faults {plan['injected']}" if plan["injected"] else ""))
    if plan.get("verdicts"):
        yield "ACK verdicts: " + ", ".join(f"{n} {v}" for n, v in plan["verdicts"])
    if plan.get("ta_in_first") is not None:
        yield f"IN first to the new address answered {plan['ta_in_first']:.2f} bit times after the EOP (model host)"


# ================================================================ main

def run_all(rig, names, store):
    results = []
    for name in names:
        print(f"[{name}]", flush=True)
        t = time.monotonic()
        try:
            plan = make_plan(name)
            for line in summary_lines(plan):
                print("  " + line, flush=True)
            if rig is not None:
                segs, st = replay(rig, plan, store)
                for line in judge(plan, segs, st, store):
                    print("  " + line, flush=True)
            print(f"  PASS ({time.monotonic() - t:.1f} s)", flush=True)
            results.append((name, "PASS"))
        except Exception as e:  # report and continue
            print(f"  FAIL: {type(e).__name__}: {e}", flush=True)
            results.append((name, "FAIL"))
            if rig is not None:
                try:
                    rig.write(sim.A_CTRL, 0b11 << 16)
                    rig.lab_mode(GPIO)
                except Exception as e2:
                    print(f"  (cleanup: {type(e2).__name__}: {e2})", flush=True)
    return results


def compare_main(a, b):
    da, db = json.loads(pathlib.Path(a).read_text()), json.loads(pathlib.Path(b).read_text())
    ma, mb = da.pop("_meta", {}), db.pop("_meta", {})
    da.pop("_captures", None)
    db.pop("_captures", None)
    print(f"{a}: {'model' if ma.get('model') else 'hardware'} {ma.get('utc')}; "
          f"{b}: {'model' if mb.get('model') else 'hardware'} {mb.get('utc')}")
    nd = 0
    for k in sorted(set(da) | set(db)):
        if k not in da or k not in db:
            print(f"  {k}: only in {'the first' if k in da else 'the second'}")
            nd += 1
            continue
        pa, pb = da[k].get("packets", []), db[k].get("packets", [])
        same_pk = pa == pb
        same_st = da[k].get("state") == db[k].get("state")
        if not same_pk:
            i = next((i for i, (x, y) in enumerate(zip(pa, pb)) if x != y), min(len(pa), len(pb)))
            print(f"  {k}: DIFFERENT packets at {i} of {len(pa)}/{len(pb)}: {pa[i:i + 1]} / {pb[i:i + 1]}")
            nd += 1
        else:
            print(f"  {k}: equal ({len(pa)} bus events with their times in clocks, "
                  f"{sum(1 for p in pa if p[1] == 'dev')} device packets)"
                  + ("" if same_st else f", but end state {da[k].get('state')} / {db[k].get('state')}"))
            nd += 0 if same_st else 1
    print("ALL EQUAL" if not nd else f"{nd} DIFFERENT")


def sigrok_vs_wire(sigrok, got, wire):
    """sigrok's packets against the plan's wire record, whose ["raw", name]
    entries (malformed host packets) match whatever sigrok makes of them up to
    the next well-formed packet. Returns (equal, n, [(raw name, sigrok's
    packets)], where it differs)."""
    want = []
    for p in wire:
        if isinstance(p, list):
            want.append(("raw", p[1]))
        elif p not in (None, "?"):
            want += sigrok.usb_expect([bytes.fromhex(p)])
    raws, i = [], 0
    for j, w in enumerate(want):
        if w[0] == "raw":
            nxt = next((x for x in want[j + 1:] if x[0] != "raw"), None)
            k = i
            while k < len(got) and got[k] != nxt:
                k += 1
            raws.append((w[1], got[i:k]))
            i = k
            continue
        if i >= len(got) or got[i] != w:
            return False, len(want), raws, f"{i}: {got[i:i + 2]} / {want[j:j + 2]}"
        i += 1
    return i == len(got), len(want), raws, f"{i} (extra: {got[i:i + 2]})"


def sigrok_main(path, plans_path):
    from pe import sigrok
    if sigrok.cli() is None:
        sys.exit(sigrok.INSTALL_HINT)
    d = json.loads(pathlib.Path(path).read_text())
    plans = json.loads(pathlib.Path(plans_path).read_text()) if plans_path else {}
    meta = d.get("_meta", {})
    print(f"captures from {path} ({'model' if meta.get('model') else 'hardware'}, {meta.get('utc')})")
    for name, r in d.get("_captures", {}).items():
        speed = r["speed"]
        dp, dm = ("uio0", "uio1") if speed == "low" else ("uio1", "uio0")
        tns = 1e9 / F_USB
        allp, errs = [], []
        for k, seg in enumerate(r["segments"]):
            tr = sigrok.Trace()
            off = 100 * BIT[speed]
            for t, p in seg["edges"]:
                tr.set(round((t + off) * tns * 1000), "uio0", p & 1)
                tr.set(round((t + off) * tns * 1000), "uio1", (p >> 1) & 1)
            tr.end(round((seg["t_obs"] + 2 * off) * tns * 1000))
            pk, err = sigrok.usb(tr, dp, dm, f"{speed}-speed", res_ps=10_000 if speed == "low" else 1000)
            allp += sigrok.usb_summary(pk)
            errs += [(k, t, e) for t, e in err]
        line = f"[{name}] sigrok usb_signalling + usb_packet: {len(allp)} packets, {len(errs)} errors"
        if name in plans:
            ok, n_want, raws, where = sigrok_vs_wire(sigrok, allp, plans[name]["wire"])
            line += f"; the plan's wire ({n_want} packets): {'identical' if ok else 'DIFFERENT'}"
            if not ok:
                line += f" at {where}"
            if raws:
                kinds = {}
                for rn, got in raws:
                    kinds.setdefault(rn, " ".join(g[0] + (f"[{g[3]}]" if g[3] else "") for g in got) or "nothing")
                line += "; malformed ACKs as sigrok reads them: " + ", ".join(f"{k}: {v}" for k, v in kinds.items())
        print(line + (f" {errs[:3]}" if errs else ""))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("names", nargs="*", default=NAMES,
                    help=f"scenarios ({' '.join(NAMES)}; `ls`/`fs` = all at that speed)")
    ap.add_argument("--model", action="store_true", help="replay on the cycle model instead of the board")
    ap.add_argument("--plan-only", action="store_true", help="only the host-model runs (plans)")
    ap.add_argument("--save", help="keep captures and results (JSON)")
    ap.add_argument("--plans", help="host-model runs: reused from / added to this JSON file")
    ap.add_argument("--expect", help="RTL revision the bitstream must be (the 48 MHz app has no expected_build.json)")
    a = ap.parse_args()
    if a.names[:1] == ["compare"]:
        return compare_main(*a.names[1:3])
    if a.names[:1] == ["sigrok"]:
        return sigrok_main(a.names[1], a.plans)
    names = []
    for n in a.names:
        names += [x for x in NAMES if x.startswith(n + "-")] if n in SPEED else [n]
    bad = [n for n in names if n not in NAMES]
    if bad:
        ap.error(f"unknown scenario(s) {bad}; choose from {NAMES}")
    if a.plans and pathlib.Path(a.plans).exists():
        PLANS["data"].update(json.loads(pathlib.Path(a.plans).read_text()))
    store = {} if a.save else None
    rig = None
    if a.model:
        rig = SyncModelRig()
        print("cycle model (pe.sim, FPGA-synchronous pads) with pe_lab's generator modelled, 48 MHz", flush=True)
    elif not a.plan_only:
        rig = HwRig()
        i = rig.ident
        print(f"bitstream RTL revision {i['rtl_rev'][:12]}{' (dirty)' if i['dirty'] else ''}, built {i['build_utc']} "
              f"UTC, digest {i['digest']}, clk_div {i['clk_div']}", flush=True)
        print(f"protoemu clk measured {rig.f_meas / 1e6:.5f} MHz over 2 s", flush=True)
        if a.expect and not i["rtl_rev"].startswith(a.expect):
            rig.close()
            sys.exit(f"FAIL: loaded bitstream is RTL revision {i['rtl_rev'][:12]}, expected {a.expect}")
        if abs(rig.f_meas / F_USB - 1) > 1e-3 or i["clk_div"] != 25:
            rig.close()
            sys.exit(f"FAIL: not the 48 MHz build (clk_div {i['clk_div']}, {rig.f_meas / 1e6:.4f} MHz)")
    try:
        results = run_all(rig, names, store)
    finally:
        if rig is not None:
            rig.close()
    print(f"host-model runs: {PLANS['made']} made now, {PLANS['used']} reused from {a.plans}")
    if a.plans and PLANS["made"]:
        pathlib.Path(a.plans).write_text(json.dumps(PLANS["data"]))
    if store is not None:
        store["_meta"] = {"model": bool(a.model), "f_clk": getattr(rig, "f_clk", F_USB),
                          "f_meas": getattr(rig, "f_meas", None), "ident": getattr(rig, "ident", None),
                          "results": results, "utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())}
        pathlib.Path(a.save).write_text(json.dumps(store))
        print(f"saved {a.save}")
    print("summary: " + ", ".join(f"{n} {s}" for n, s in results))
    nfail = sum(1 for r in results if r[1] != "PASS")
    print("ALL PASS" if not nfail else f"{nfail} run(s) FAILED")
    sys.exit(1 if nfail else 0)


if __name__ == "__main__":
    main()
