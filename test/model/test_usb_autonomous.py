"""Autonomous USB enumeration (docs/usb-enumeration.md): the host loads
firmware/usb_enum.pasm and the line unit configuration, and from then on the
chip alone answers a host controller model through enumeration to the
Configured state, at low speed (pe.usbhost.ControlHost: pins only, toggles,
retries, STALL, keep-alives, injected faults) and at full speed (the same
transaction logic over pe.usbfs.FsHost: edges in continuous time with jitter
and frequency offset, its own clock recovery, SOF every millisecond). The SPI
host transport is checked idle from the start until enumeration is over; only
then is the outcome read."""
import random

import pytest

from pe import sigrok, usb, usbdev, usbenum
from pe.modelhost import ModelHost
from pe.usbfs import FsControlHost
from pe.usbhost import ControlHost, Stall

DEV, CFG = usbdev.DEVICE_DESC, usbdev.CONFIG_DESC
PROG = {s: usbenum.assemble(s) for s in ("low", "full")}
SPEEDS = ["low", "full"]
SHORT_RESET = dict(cycles=48 * 50, recovery=48 * 200)    # 50 us SE0 (the device acts on 2.5 us)


class TransportIdle:
    """Fails the test if anything uses the host bus while active."""

    OPS = ("write", "snap", "commit", "cancel", "read", "push", "pop_all", "apply", "start")

    def __init__(self, mh):
        self.mh = mh

    def __enter__(self):
        for op in self.OPS:
            setattr(self.mh, op, self._refuse(op))
        return self

    def _refuse(self, op):
        def f(*a, **kw):
            raise AssertionError(f"host transport used during enumeration: {op}{a}")
        return f

    def __exit__(self, *exc):
        for op in self.OPS:
            delattr(self.mh, op)


def device(speed="low", **host_kw):
    """Low speed: D+ on uio0, D- on uio1, pull-up on D-. Full speed: D- on uio0,
    D+ on uio1, pull-up on D+. Either way the bus idles at uio1 high."""
    mh = ModelHost()
    mh.apply(usbenum.load_writes(PROG[speed], speed))
    mh.pull_up = 0b10
    mh.step(100)
    if speed == "low":
        return mh, ControlHost(mh, **host_kw)
    host_kw.setdefault("ipd_bits", 2.0)       # the host's shortest gaps
    host_kw.setdefault("ack_bits", 2.0)
    return mh, FsControlHost(mh, **host_kw)


def enumerate_flow(h, addr=5, strings=True):
    """The standard sequence (a Windows/Linux mix): device descriptor's first 8
    bytes at address 0, SET_ADDRESS, the full device descriptor, the
    configuration's 9-byte header then all of it, a string descriptor (STALLed:
    the device has none), SET_CONFIGURATION 1, GET_STATUS."""
    assert h.get_descriptor(0, 1, 8) == DEV[:8]
    h.control_write(0, bytes([0x00, 5, addr, 0, 0, 0, 0, 0]))
    assert h.get_descriptor(addr, 1, 18) == DEV
    assert h.get_descriptor(addr, 2, 9) == CFG[:9]
    assert h.get_descriptor(addr, 2, CFG[2]) == CFG
    if strings:
        with pytest.raises(Stall):
            h.get_descriptor(addr, 3, 255)
    h.control_write(addr, bytes([0x00, 9, 1, 0, 0, 0, 0, 0]))
    assert h.control_read(addr, bytes([0x80, 0, 0, 0, 0, 0, 2, 0])) == b"\0\0"


CONFIGURED = lambda addr: dict(address=addr, configured=True, pending_address=False, stalled=False)


def test_table_is_the_reference_descriptors():
    for prog in PROG.values():
        assert usbenum.table(prog, "dev") == DEV and usbenum.table(prog, "cfg") == CFG
        assert len(prog.words) <= 256
    print(f"usb_enum: {len(prog.words)} of 256 words, {len(DEV) + len(CFG)} of them descriptors")


@pytest.mark.parametrize("speed,host_kw", [
    ("low", dict(ppm=0)), ("low", dict(ppm=15000)), ("low", dict(ppm=-15000)),
    ("full", dict(ppm=0)), ("full", dict(ppm=2500, jitter_ns=3, seed=2)),
    ("full", dict(ppm=-2500, jitter_ns=3, dcd_ns=2, seed=3))],
    ids=["low", "low+1.5%", "low-1.5%", "full", "full+0.25%,jitter", "full-0.25%,jitter,dcd"])
def test_enumerates_with_no_host_involvement(speed, host_kw):
    """Clock offsets: low speed devices may be off by 1.5 %, full speed by
    0.25 % (the host model's bit rate moves instead). Full speed with the
    host's shortest inter-packet gaps (2 bit times) and edge jitter."""
    mh, h = device(speed, **host_kw)
    with TransportIdle(mh):
        enumerate_flow(h)
    assert usbenum.state(mh.read) == CONFIGURED(5)
    assert all(2.0 <= t <= 6.5 for t in h.turnarounds), (min(h.turnarounds), max(h.turnarounds))
    ack = usb.handshake(usb.PID_ACK)
    retried = [e for e in h.log if "error" in e or (e[0] in ("SETUP", "OUT") and e[-1] != ack)]
    assert not retried, retried                   # every transaction went through first time
    print(f"{speed} {host_kw}: configured in {mh.chip.cycle} cycles ({mh.chip.cycle / 48e3:.2f} ms at "
          f"48 MHz), {len(h.log)} transactions, turnaround {min(h.turnarounds):.2f}-"
          f"{max(h.turnarounds):.2f} bit times")


@pytest.mark.parametrize("speed", SPEEDS)
def test_linux_style_flow(speed):
    """Linux's "new scheme": a 64-byte device descriptor read at address 0, a
    10 ms bus reset, then SET_ADDRESS and the rest."""
    mh, h = device(speed)
    with TransportIdle(mh):
        assert h.get_descriptor(0, 1, 64) == DEV
        h.bus_reset() if speed == "low" else h.bus_reset(**SHORT_RESET)
        enumerate_flow(h, addr=42, strings=False)
        assert h.control_read(42, bytes([0x80, 8, 0, 0, 0, 0, 1, 0])) == b"\x01"   # GET_CONFIGURATION
    assert usbenum.state(mh.read) == CONFIGURED(42)


@pytest.mark.parametrize("speed", SPEEDS)
def test_wlength_truncates_the_transfer(speed):
    mh, h = device(speed)
    with TransportIdle(mh):
        for wlength in (0, 1, 2, 7, 8, 9, 15, 16, 17, 18, 19, 64, 255):
            assert h.get_descriptor(0, 1, wlength) == DEV[:wlength], wlength
            assert h.get_descriptor(0, 2, wlength) == CFG[:wlength], wlength


@pytest.mark.parametrize("speed", SPEEDS)
@pytest.mark.parametrize("kind", ["lose_handshake", "lose_host_ack", "bad_data"])
@pytest.mark.parametrize("seed", range(3))
def test_lost_acks_are_retried(speed, kind, seed):
    """Faults sprinkled over the whole flow: the device's ACKs lost (the host
    retries the SETUP or OUT), the host's ACKs lost (the device re-sends with
    the old toggle and the host discards it), damaged DATA (no ACK, retry)."""
    mh, h = device(speed)
    rng = random.Random(seed)
    steps = [lambda: h.get_descriptor(0, 1, 8) == DEV[:8],
             lambda: h.control_write(0, bytes([0x00, 5, 9, 0, 0, 0, 0, 0])) is None,
             lambda: h.get_descriptor(9, 1, 18) == DEV,
             lambda: h.get_descriptor(9, 2, 9) == CFG[:9],
             lambda: h.get_descriptor(9, 2, 255) == CFG,
             lambda: h.control_write(9, bytes([0x00, 9, 1, 0, 0, 0, 0, 0])) is None]
    with TransportIdle(mh):
        for step in steps:
            h.faults[kind] = rng.randrange(0, 3)
            assert step()
    assert usbenum.state(mh.read) == CONFIGURED(9)
    print(f"{speed} {kind} seed {seed}: injected {h.injected}")
    assert h.injected


@pytest.mark.parametrize("speed", SPEEDS)
def test_set_address_status_ack_lost(speed):
    """The host takes SET_ADDRESS's zero-length status packet but its ACK is
    lost: the host moves to the new address while the device has not seen the
    stage complete. The first token to the new address completes it."""
    mh, h = device(speed)
    with TransportIdle(mh):
        h.faults["lose_host_ack"] = 1
        h.control_write(0, bytes([0x00, 5, 77, 0, 0, 0, 0, 0]))
        assert h.injected == {"lose_host_ack": 1}
        assert h.get_descriptor(77, 1, 18) == DEV
        h._send(usb.token(usb.PID_IN, 0, 0))     # the old address is gone
        assert h._response() is None
    assert usbenum.state(mh.read)["address"] == 77


@pytest.mark.parametrize("speed", SPEEDS)
def test_set_address_status_ack_lost_then_in_first(speed):
    """As above, but the first token to the new address is an IN, not a SETUP:
    the device commits the address on that token and must still answer within
    the turnaround (6.5 bit times = 26 clocks at full speed, USB 2.0
    §7.1.18.1). Before the fix the commit sat on the IN's path and full speed
    answered at 27.4-28.2 clocks (docs/timing-check.md, deadline in_ta_any).
    The answer is the no-transfer one, a zero-length DATA1; enumeration then
    completes at the new address."""
    mh, h = device(speed)
    with TransportIdle(mh):
        h.faults["lose_host_ack"] = 1
        h.control_write(0, bytes([0x00, 5, 77, 0, 0, 0, 0, 0]))
        assert h.injected == {"lose_host_ack": 1}
        h._send(usb.token(usb.PID_IN, 77, 0))         # an IN first
        r = h._response()
        assert r == bytes([usb.pid_byte(usb.PID_DATA1), 0, 0]), r
        ta_bits = h.turnarounds[-1]
        ta_clk = h.fs.turnarounds[-1][1] / h.fs.tclk if speed == "full" else ta_bits * 32
        h._steps(2)
        h._send(usb.handshake(usb.PID_ACK))
        h.gap()
        h._send(usb.token(usb.PID_IN, 0, 0))          # the old address is gone
        assert h._response() is None
        h.gap()
        assert h.get_descriptor(77, 1, 18) == DEV
        assert h.get_descriptor(77, 2, CFG[2]) == CFG
        h.control_write(77, bytes([0x00, 9, 1, 0, 0, 0, 0, 0]))
        assert h.control_read(77, bytes([0x80, 0, 0, 0, 0, 0, 2, 0])) == b"\0\0"
    assert usbenum.state(mh.read) == CONFIGURED(77)
    assert 2.0 <= ta_bits <= 6.5, ta_bits
    if speed == "full":
        assert 8 <= ta_clk <= 26, ta_clk                   # 2 to 6.5 bit times at 48 MHz
    assert all(2.0 <= t <= 6.5 for t in h.turnarounds), (min(h.turnarounds), max(h.turnarounds))
    print(f"{speed}: lost status ACK + IN first: answered {ta_bits:.2f} bit times ({ta_clk:.1f} clocks) "
          f"after the EOP; configured at 77, turnaround {min(h.turnarounds):.2f}-{max(h.turnarounds):.2f}")


@pytest.mark.parametrize("speed", SPEEDS)
def test_set_address_waits_for_the_status_stage(speed):
    """Until the status stage, the device still answers address 0 (a SETUP
    retried there is answered), and a new SETUP cancels the pending address."""
    mh, h = device(speed)
    with TransportIdle(mh):
        h.setup(0, bytes([0x00, 5, 6, 0, 0, 0, 0, 0]))
        h.setup(0, bytes([0x00, 5, 6, 0, 0, 0, 0, 0]))     # lost ACK: repeated
        assert h.get_descriptor(0, 1, 8) == DEV[:8]         # abandons SET_ADDRESS
        h._send(usb.token(usb.PID_IN, 6, 0))
        assert h._response() is None
        h.faults["bad_data"] = 1                            # the status ZLP twice
        h.control_write(0, bytes([0x00, 5, 6, 0, 0, 0, 0, 0]))
        assert h.get_descriptor(6, 1, 8) == DEV[:8]


@pytest.mark.parametrize("speed", SPEEDS)
@pytest.mark.parametrize("when", ["data_stage", "after_address", "configured"])
def test_bus_reset_mid_enumeration(speed, when):
    mh, h = device(speed)
    with TransportIdle(mh):
        h.control_write(0, bytes([0x00, 5, 5, 0, 0, 0, 0, 0]))
        if when == "data_stage":                  # SETUP + one IN, no status
            h.setup(5, bytes([0x80, 6, 0, 2, 0, 0, 18, 0]))
            assert h.data_in(5, 1) == CFG[:8]
        elif when == "configured":
            h.control_write(5, bytes([0x00, 9, 1, 0, 0, 0, 0, 0]))
        h.bus_reset(**SHORT_RESET)
        h._send(usb.token(usb.PID_IN, 5, 0))      # back at the default address
        assert h._response() is None
        enumerate_flow(h, addr=9)
    assert usbenum.state(mh.read) == CONFIGURED(9)


@pytest.mark.parametrize("speed", SPEEDS)
def test_bus_reset_unconfigures(speed):
    mh, h = device(speed)
    with TransportIdle(mh):
        enumerate_flow(h, addr=3, strings=False)
        h.bus_reset(cycles=48 * 3, recovery=48 * 100)    # 3 us: just over the 2.5 us threshold
    assert usbenum.state(mh.read) == dict(address=0, configured=False, pending_address=False, stalled=False)


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


@pytest.mark.parametrize("speed", SPEEDS)
@pytest.mark.parametrize("name", UNSUPPORTED)
def test_unsupported_requests_are_stalled(speed, name):
    req, data = UNSUPPORTED[name]
    mh, h = device(speed)
    with TransportIdle(mh):
        with pytest.raises(Stall):
            if req[0] & 0x80:
                h.control_read(0, req)
            else:
                h.control_write(0, req, data or b"")
        with pytest.raises(Stall):                # until the next SETUP
            h.data_in(0, 1)
        assert h.get_descriptor(0, 1, 18) == DEV  # which clears it
    assert not usbenum.state(mh.read)["stalled"]


@pytest.mark.parametrize("speed", SPEEDS)
def test_other_addresses_and_bad_tokens_are_ignored(speed):
    mh, h = device(speed)
    with TransportIdle(mh):
        for tok in (usb.token(usb.PID_IN, 3, 0), usb.token(usb.PID_IN, 0, 1),
                    usb.token(usb.PID_SETUP, 1, 0)):
            h._send(tok)
            assert h._response() is None, tok.hex()
        bad = bytearray(usb.token(usb.PID_IN, 0, 0))
        bad[2] ^= 0x40                            # a CRC5 bit
        h._send(bytes(bad))
        assert h._response() is None
        h._send(usb.token(usb.PID_SETUP, 0, 0))   # SETUP DATA with a bad CRC16:
        h._steps(2)                               # no handshake
        pkt = bytearray(usb.data_packet(usb.PID_DATA0, bytes([0x80, 6, 0, 1, 0, 0, 18, 0])))
        pkt[-1] ^= 1
        h._send(bytes(pkt))
        assert h._response() is None
        h.gap()
        assert h.get_descriptor(0, 1, 18) == DEV


@pytest.mark.skipif(sigrok.cli() is None, reason=sigrok.INSTALL_HINT)
@pytest.mark.parametrize("speed", SPEEDS)
def test_sigrok_decodes_the_enumeration(speed):
    """sigrok's usb_signalling + usb_packet decoders (third-party code) on the
    recorded pins see exactly the packets the host model sent and received, in
    order, with no SYNC, stuffing, CRC5 or CRC16 error (lost-ACK faults
    included, so the retransmissions are on the wire too)."""
    with sigrok.ModelRecorder(period_ns=1000 / 48) as rec:
        mh, h = device(speed)
        rec.restart()
        mh.step(200)                 # idle J before the first packet
        h.faults.update(lose_host_ack=1, bad_data=1, lose_handshake=1)
        with TransportIdle(mh):
            enumerate_flow(h)
    dp, dm = ("uio0", "uio1") if speed == "low" else ("uio1", "uio0")
    pk, err = sigrok.usb(rec.trace, dp, dm, f"{speed}-speed")
    got, want = sigrok.usb_summary(pk), sigrok.usb_expect(h.wire)
    assert not err, err[:5]
    assert all(p.crc_ok for p in pk)
    diff = next((i for i, (g, w) in enumerate(zip(got, want)) if g != w), None)
    assert got == want, (len(got), len(want), diff, got[diff:diff + 3] if diff is not None else None)
    print(f"sigrok ({speed} speed): {len(pk)} packets, identical to the host model's, no errors")
