"""The line-RX overflow seen in USB device tests is the device's own echo.

`line_echo.EchoProbe` attributes every dropped RX bit to a receiver frame. Here
the shipped autonomous `usb_enum` answers one descriptor read: each eight-byte
DATA reply (96 line bits) drops 46 of its own echoed bits and nothing else.
Two small negative controls show the check is not vacuous: a device that never
drains loses peer bits, and a device that leaves its echo queued meets the next
peer packet with a dirty receiver. Both must fail the check.
"""
import pytest

from line_echo import EchoProbe
from pe import asm, host, sim, usb, usbdev, usbenum
from pe.modelhost import ModelHost
from pe.usbhost import ControlHost


def _probed(writes):
    mh = ModelHost()
    mh.pull_up = 0b10                       # D- pull-up: the bus idles in J
    probe = EchoProbe(mh.chip, lambda: mh.ext_oe & 3)
    mh.apply(writes)
    mh.step(64)
    return mh, probe


def _line_only(source):
    prog = asm.assemble(".program t\n.shift_out right 16\n" + source)
    return _probed(host.load_program(0, prog, 0, clkdiv=1 << 8)
                   + [(sim.A_LINEDIV, (32 << 16) | (32 << 8)), (sim.A_LINEPINS, 0),
                      (sim.A_LINECFG, usbenum.LINECFG)] + host.start_writes(1))


def test_usb_enum_overflow_is_only_its_own_echo():
    mh, probe = _probed(usbenum.load_writes(usbenum.assemble("low"), "low"))
    peer = ControlHost(mh, keepalive=False)
    assert peer.get_descriptor(0, 1, 18) == usbdev.DEVICE_DESC
    summary = probe.check()
    own = [(f["queued"] + f["dropped"], f["dropped"]) for f in probe.frames if f["own"]]
    # SETUP's ACK, DATA1/DATA0 of 8 bytes, DATA1 of 2 bytes, the status ACK. (Since
    # fault-fixes `echo` first checks that its packet has begun, 4 ticks: one more
    # bit of the second DATA's echo is dropped, 46 and 92 before.)
    assert own == [(16, 0), (96, 46), (96, 47), (48, 0), (16, 0)]
    assert summary["dropped_bits"] == 93 and summary["peer_peak_rx_queued"] <= 16


def test_check_rejects_a_dropped_peer_bit():
    mh, probe = _line_only("    halt\n")            # never drains the receiver
    peer = ControlHost(mh, keepalive=False)
    peer._send(usb.data_packet(usb.PID_DATA0, bytes(8)))   # 96 bits > 32
    assert mh.chip.stats.get("line:rx_overflow", 0) > 0
    with pytest.raises(AssertionError, match="outside the device's own echo"):
        probe.check()


def test_check_rejects_a_peer_packet_over_undrained_echo():
    mh, probe = _line_only("    ldi r0, 0xD280\n    mov osr, r0\n    out line, 16\n    halt\n")
    mh.step(1000)                                    # our ACK goes out and echoes back
    assert not mh.chip.stats.get("line:rx_overflow", 0)
    peer = ControlHost(mh, keepalive=False)
    peer._send(usb.token(usb.PID_IN, 0, 0))
    with pytest.raises(AssertionError, match="before the echo was drained"):
        probe.check()
    # and the 16 stale echo bits cost the token its last 16 bits
    assert [(f["own"], f["queued"], f["dropped"]) for f in probe.frames] == [(True, 16, 0), (False, 16, 16)]
