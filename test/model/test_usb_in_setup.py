"""An IN token between a SETUP token and its DATA packet (verifier 047 note 1).
A host does not send one, but a device must not answer it later, unasked:
usb_ls_rx sets F_IN for every IN, and usb_ls_tx, held in its SETUP flush while
F_SETUP is set, used to leave F_IN pending and answer it (a NAK, or a staged
packet) after the SETUP's ACK, onto a bus the host owns. usb_ls_tx's flush
now clears F_IN. The one-core firmware (usb_ls_dev, usb_fs_dev) has no
deferred IN: it answers each IN as its token ends. Pins-only hosts."""
import pytest

from pe import sim, usb, usbdev

REQ = bytes([0x80, 6, 0, 1, 0, 0, 18, 0])          # GET_DESCRIPTOR(device)
F_IN = 1 << usbdev.FLAGS["F_IN"]


def two_core():
    from test_usb_enum import device
    from pe.usbhost import UsbHost
    mh, sw = device("two-core")
    return mh, sw, UsbHost(mh)


@pytest.mark.parametrize("staged", [False, True], ids=["nothing-staged", "staged"])
def test_two_core_in_inside_a_setup_is_never_answered_later(staged):
    mh, sw, h = two_core()
    if staged:                                        # a packet of the transfer the SETUP aborts
        sw.stage(usb.PID_DATA1, b"stale!!!")
    h._send(usb.token(usb.PID_SETUP, 0, 0))
    h._gap(2)
    h._send(usb.token(usb.PID_IN, 0, 0))              # inside the SETUP: F_SETUP is set
    assert h._listen(20 * h.bit) is None, "an IN inside a SETUP answered"
    h._gap(2)
    h._send(usb.data_packet(usb.PID_DATA0, REQ))
    assert h._listen(20 * h.bit) == usb.handshake(usb.PID_ACK)
    later = h._listen(200 * h.bit)
    assert later is None, f"the IN from inside the SETUP answered after its ACK: {later!r}"
    assert not mh.read(sim.A_FSET) & F_IN, "F_IN still pending"
    # the next real IN: one NAK (nothing staged since the SETUP), and nothing after it
    h._gap(8)
    h._send(usb.token(usb.PID_IN, 0, 0))
    assert h._listen(120 * h.bit) == usb.handshake(usb.PID_NAK)
    assert h._listen(200 * h.bit) is None


@pytest.mark.parametrize("variant", ["fifo-low", "fifo-full"])
def test_one_core_in_inside_a_setup_is_never_answered_later(variant):
    """usb_ls_dev and usb_fs_dev answer the IN at once (a NAK: the SETUP's
    token cleared F_STAGED) and nothing after the SETUP's ACK."""
    from test_usb_ack import device
    mh, peer, software = device(variant, 0)
    peer._send(usb.token(usb.PID_SETUP, 0, 0))
    peer.gap(2)
    peer._send(usb.token(usb.PID_IN, 0, 0))
    assert peer._response() == usb.handshake(usb.PID_NAK)
    peer.gap(2)
    peer._send(usb.data_packet(usb.PID_DATA0, REQ))
    assert peer._response() == usb.handshake(usb.PID_ACK)
    assert peer._response() is None, "a packet after the SETUP's ACK"
