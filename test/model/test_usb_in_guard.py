"""The got_in guard of the one-core USB firmware (usb_fs_dev at full speed,
usb_ls_dev at low speed): an IN that finds F_STAGED set over an empty TX FIFO
is NAKed. Without the guard the core takes the IN as staged data, clears
F_STAGED and waits in `pull block` for a bit count that never comes: no
answer to that IN or any after it, until the host software pushes a word, and
then that word goes out unasked. The state arises when the device software's
stage races a SETUP, whose token flushes the TX FIFO (test_usb_fs_data's
re-stage tests). Pins-only hosts of pe.usbhost / pe.usbfs."""
import pytest

from pe import sim, usb, usbdev
from test_usb_ack import device, poll


@pytest.mark.parametrize("variant", ["fifo-full", "fifo-low"])
def test_in_with_f_staged_over_an_empty_fifo_is_naked(variant):
    mh, peer, software = device(variant, 0)
    assert not mh.chip.cores[0].txf
    mh.write(sim.A_FSET, usbdev.F_STAGED)               # set, nothing pushed
    for i in range(3):
        peer._send(usb.token(usb.PID_IN, 0, 0))
        assert peer._response() == usb.handshake(usb.PID_NAK), f"IN {i}: no NAK"
        peer.gap()
    assert mh.read(sim.A_FSET) & usbdev.F_STAGED, "the guard leaves F_STAGED to the software"
    assert not mh.read(sim.A_FSET) & 1 << usbdev.FLAGS["F_IN"], "F_UNDERRUN"
    # the software clears it and stages a real packet: the next IN gets it
    mh.write(sim.A_FCLR, usbdev.F_STAGED)
    software.stage(usb.PID_DATA1, b"\x12\x34")
    assert poll(peer) == usb.data_packet(usb.PID_DATA1, b"\x12\x34")
