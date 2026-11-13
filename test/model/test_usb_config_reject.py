"""Rejected SET_CONFIGURATION requests must preserve the configured function.

Model-level USB packets at low/full speed on the 2/3-core profiles. No host
register writes occur after loading. The EP1 check inspects the wire toggle;
a host that silently discards a duplicate toggle would hide a toggle reset.
"""
from pathlib import Path

import pytest

from pe import usb, usbenum
from pe.modelhost import ModelHost
from pe.usbfs import FsControlHost
from pe.usbhost import ControlHost, Stall
from test_usb_autonomous import TransportIdle

FW = Path(__file__).resolve().parents[2] / "firmware"


@pytest.mark.parametrize("ncores", [2, 3])
@pytest.mark.parametrize("speed", ["low", "full"])
@pytest.mark.parametrize("ep1", [False, True])
@pytest.mark.parametrize("configured", [0, 1])
def test_rejected_configuration_keeps_state_and_toggle(ncores, speed, ep1, configured):
    name = "usb_enum_ep1.pasm" if ep1 else "usb_enum.pasm"
    mh = ModelHost(ncores=ncores, nbanks=ncores)
    prog = usbenum.assemble(speed, (FW / name).read_text())
    assert len(prog.words) <= 256
    mh.apply(usbenum.load_writes(prog, speed))
    mh.pull_up = 0b10
    mh.step(100)
    h = ControlHost(mh) if speed == "low" else FsControlHost(mh, ipd_bits=2.0, ack_bits=2.0)
    addr, toggle = 5, 0
    with TransportIdle(mh):
        h.control_write(0, bytes([0, 5, addr, 0, 0, 0, 0, 0]))
        h.control_write(addr, bytes([0, 9, configured, 0, 0, 0, 0, 0]))
        if ep1 and configured:
            assert h.data_in(addr, toggle, endp=1) == bytes([0, 0])
            toggle ^= 1
        for invalid in (2, 255, 0x100, 0xFFFF):
            with pytest.raises(Stall):
                h.control_write(addr, bytes([0, 9, invalid & 255, invalid >> 8, 0, 0, 0, 0]))
            assert h.control_read(addr, bytes([0x80, 8, 0, 0, 0, 0, 1, 0])) == bytes([configured])
            if ep1:
                h._send(usb.token(usb.PID_IN, addr, 1))
                packet = h._response()
                if configured:
                    pid = usb.PID_DATA1 if toggle else usb.PID_DATA0
                    assert packet == usb.data_packet(pid, bytes([0, 0]))
                    h._steps(2)
                    h._send(usb.handshake(usb.PID_ACK))
                    toggle ^= 1
                else:
                    assert packet is None
                h.gap()
        # Both legal values still work after the refused requests.
        for value in (1, 0, 1):
            h.control_write(addr, bytes([0, 9, value, 0, 0, 0, 0, 0]))
            assert h.control_read(addr, bytes([0x80, 8, 0, 0, 0, 0, 1, 0])) == bytes([value])
            if ep1 and value:
                assert h.data_in(addr, 0, endp=1) == bytes([0, 0])
