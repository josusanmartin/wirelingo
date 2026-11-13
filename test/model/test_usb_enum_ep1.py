"""firmware/usb_enum_ep1.pasm: the autonomous USB device with an interrupt IN
endpoint 1 that reports pins, on the v0.3 ISA. It needs the table read, CMPW,
`jmp !t` and `flag tog` to fit core 0's 256 words (the v3 study, on its branch,
measured 280 words for the same firmware in v2 instructions). Ported from the
v3 study's tests, on today's usb_enum (bus-reset, dribble and turnaround
fixes since), with bus resets around endpoint 1 added. The host controller
models are test_usb_autonomous's; the host transport stays idle throughout."""
import pathlib
import random

import pytest

from pe import asm, sim, usb, usbdev, usbenum
from pe.modelhost import ModelHost
from pe.usbfs import FsControlHost
from pe.usbhost import ControlHost
from test_usb_autonomous import TransportIdle, SHORT_RESET

FW = pathlib.Path(__file__).resolve().parents[2] / "firmware"
DEV = usbdev.DEVICE_DESC
CFG = bytes([9, 2, 25, 0, 1, 1, 0, 0x80, 50,
             9, 4, 0, 0, 1, 0xFF, 0, 0, 0,
             7, 5, 0x81, 3, 8, 0, 10])          # + endpoint 0x81: interrupt IN, 8 bytes, 10 ms
SPEEDS = ["low", "full"]


def program(speed):
    return usbenum.assemble(speed, (FW / "usb_enum_ep1.pasm").read_text())


def device(speed, **host_kw):
    mh = ModelHost()
    mh.apply(usbenum.load_writes(program(speed), speed))
    mh.pull_up = 0b10
    mh.step(100)
    if speed == "low":
        return mh, ControlHost(mh, **host_kw)
    host_kw.setdefault("ipd_bits", 2.0)
    host_kw.setdefault("ack_bits", 2.0)
    return mh, FsControlHost(mh, **host_kw)


def configure(h, addr):
    assert h.get_descriptor(0, 1, 8) == DEV[:8]
    h.control_write(0, bytes([0x00, 5, addr, 0, 0, 0, 0, 0]))
    assert h.get_descriptor(addr, 1, 18) == DEV
    assert h.get_descriptor(addr, 2, 9) == CFG[:9]
    assert h.get_descriptor(addr, 2, CFG[2]) == CFG
    h.control_write(addr, bytes([0x00, 9, 1, 0, 0, 0, 0, 0]))


def set_inputs(mh, ui, vpins):
    """ui[4:0] are pads; V0-V7 are core outputs: core 0 owns them by default,
    and the host's PIN_OUT write stands in for a core 1 program writing them."""
    mh.ui = ui
    mh.chip.pin_out = (mh.chip.pin_out & 0x00FFFFFF) | (vpins << 24)


def test_fits_and_table():
    for speed in SPEEDS:
        prog = program(speed)
        assert len(prog.words) == 256                  # of the bank's 256
        assert usbenum.table(prog, "dev") == DEV and usbenum.table(prog, "cfg", len(CFG)) == CFG
    print(f"usb_enum_ep1: {len(prog.words)} of 256 words")


@pytest.mark.parametrize("speed,host_kw", [
    ("low", dict(ppm=0)), ("low", dict(ppm=-15000)),
    ("full", dict(ppm=0)), ("full", dict(ppm=2500, jitter_ns=3, seed=2))],
    ids=["low", "low-1.5%", "full", "full+0.25%,jitter"])
def test_reports_pins_on_endpoint_1(speed, host_kw):
    mh, h = device(speed, **host_kw)
    rng = random.Random(1)
    with TransportIdle(mh):
        h._send(usb.token(usb.PID_IN, 0, 1))         # not configured: no endpoint 1
        assert h._response() is None
        h.gap()
        configure(h, 7)
        toggle = 0
        for _ in range(6):
            ui, vp = rng.randrange(32), rng.randrange(256)
            set_inputs(mh, ui, vp)
            mh.step(20)                               # through the synchronizer
            assert h.data_in(7, toggle, endp=1) == bytes([ui, vp])
            toggle ^= 1
        assert h.get_descriptor(7, 1, 18) == DEV      # endpoint 0 still works
    assert usbenum.state(mh.read)["configured"]
    assert all(2.0 <= t <= 6.5 for t in h.turnarounds), (min(h.turnarounds), max(h.turnarounds))
    print(f"{speed} {host_kw}: turnaround {min(h.turnarounds):.2f}-{max(h.turnarounds):.2f} bit times")


@pytest.mark.parametrize("speed", SPEEDS)
def test_lost_host_ack_repeats_the_toggle(speed):
    mh, h = device(speed)
    with TransportIdle(mh):
        configure(h, 3)
        set_inputs(mh, 5, 0xA5)
        mh.step(20)
        assert h.data_in(3, 0, endp=1) == bytes([5, 0xA5])
        h.faults["lose_host_ack"] = 1                 # the device misses our ACK of DATA1
        assert h.data_in(3, 1, endp=1) == bytes([5, 0xA5])
        set_inputs(mh, 6, 0x5A)
        mh.step(20)
        # the device repeats DATA1 (ACKed, discarded by the host), then sends DATA0
        assert h.data_in(3, 0, endp=1) == bytes([6, 0x5A])
        assert h.injected == {"lose_host_ack": 1}


@pytest.mark.parametrize("speed", SPEEDS)
def test_set_configuration_resets_and_disables_the_endpoint(speed):
    mh, h = device(speed)
    with TransportIdle(mh):
        configure(h, 9)
        assert h.data_in(9, 0, endp=1) == bytes([0, 0])
        h.control_write(9, bytes([0x00, 9, 1, 0, 0, 0, 0, 0]))       # again: DATA0
        assert h.data_in(9, 0, endp=1) == bytes([0, 0])
        h.control_write(9, bytes([0x00, 9, 0, 0, 0, 0, 0, 0]))       # unconfigured
        h._send(usb.token(usb.PID_IN, 9, 1))
        assert h._response() is None
        h.gap()
        h._send(usb.token(usb.PID_IN, 9, 2))                          # no endpoint 2
        assert h._response() is None
        h.gap()
        h.control_write(9, bytes([0x00, 9, 1, 0, 0, 0, 0, 0]))
        assert h.data_in(9, 0, endp=1) == bytes([0, 0])


@pytest.mark.parametrize("speed", SPEEDS)
@pytest.mark.parametrize("seed", range(2))
def test_faults_on_both_endpoints(speed, seed):
    mh, h = device(speed)
    rng = random.Random(seed)
    with TransportIdle(mh):
        configure(h, 4)
        toggle = 0
        for i in range(10):
            kind = rng.choice(["lose_handshake", "lose_host_ack", "bad_data", None])
            if kind:
                h.faults[kind] = 1
            if i % 3 == 2:
                assert h.get_descriptor(4, 2, 25) == CFG
            else:
                set_inputs(mh, i, 255 - i)
                mh.step(20)
                assert h.data_in(4, toggle, endp=1) == bytes([i, 255 - i])
                toggle ^= 1
    assert usbenum.state(mh.read)["configured"]


@pytest.mark.parametrize("offset", range(6))
def test_bus_reset_after_an_endpoint_1_in(offset):
    """A bus reset right after endpoint 1's DATA, at any clock: the device is back
    at address 0, unconfigured, endpoint 1 gone (the shared timed WAIT and the
    echo check of today's usb_enum hold for endpoint 1 too)."""
    mh, h = device("full")
    with TransportIdle(mh):
        configure(h, 6)
        assert h.data_in(6, 0, endp=1) == bytes([0, 0])
        mh.step(offset)
        h.bus_reset(**SHORT_RESET)
        h._send(usb.token(usb.PID_IN, 6, 1))
        assert h._response() is None
        h.gap()
    assert usbenum.state(mh.read) == dict(address=0, configured=False, pending_address=False, stalled=False)


@pytest.mark.parametrize("speed", SPEEDS)
def test_reconfigure_after_reset_starts_at_data0(speed):
    mh, h = device(speed)
    with TransportIdle(mh):
        configure(h, 2)
        assert h.data_in(2, 0, endp=1) == bytes([0, 0])     # the toggle is now DATA1
        h.bus_reset() if speed == "low" else h.bus_reset(**SHORT_RESET)
        configure(h, 8)
        assert h.data_in(8, 0, endp=1) == bytes([0, 0])     # SET_CONFIGURATION: DATA0 again


def test_deadlines_static_and_sound():
    """firmware/usb_enum_ep1.timing at full speed: every turnaround passes the
    static checker (endpoint 1: 19-25 clocks of 26), and the intervals measured
    on the model during a session with jitter lie inside its bounds."""
    from pe import timing
    from test_timing_check import soundness
    chk, results = timing.check_source((FW / "usb_enum_ep1.pasm").read_text(), {"DP": 0, "DM": 1, "TURN": 4},
                                       sidecar=(FW / "usb_enum_ep1.timing").read_text())
    print()
    for r in results:
        print(r.report())
    st = {r.deadline.name: r for r in results}
    assert not chk.errors
    assert st["in_ta_any"].status == "PASS" and st["data_ta"].status == "PASS"
    assert st["ep1_ta"].status == "PASS (conditional)" and st["rx_bit"].status == "PASS (conditional)"
    # Endpoint 1 has one clock of static margin at full speed (max 26): pin the
    # bounds, so a change that costs it a clock fails here instead of eroding
    # it silently (verifier 072, finding 2). A deliberate change updates them.
    assert st["ep1_ta"].total() == (19, 25) and st["in_ta_any"].total() == (10, 25)
    assert st["ep1_ta"].deadline.max == 26
    mh = ModelHost()
    mh.apply(usbenum.load_writes(program("full"), "full"))
    tr = timing.Tracer(mh.chip, 0, 0)
    mh.pull_up = 0b10
    mh.step(100)
    h = FsControlHost(mh, seed=4, jitter_ns=3, ppm=2500, ipd_bits=2.0, ack_bits=2.0)
    configure(h, 11)
    for t in (0, 1, 0, 1):
        assert h.data_in(11, t, endp=1) == bytes([0, 0])
    s = soundness(chk, results, tr, label="usb_enum_ep1")
    assert s["ep1_ta"][0] == 4 and s["in_ta_any"][0] > 0
    print(s)
