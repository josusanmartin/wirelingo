"""Firmware tests on the cycle-accurate model (fast). Run: pytest test/model"""
import pathlib
import random

import pytest

from pe import asm, host, sim, usb, usbdev
from pe.modelhost import ModelHost

FW = pathlib.Path(__file__).resolve().parents[2] / "firmware"
DP, DM = 0, 1  # uio0, uio1
LINE = {usb.J: (0, 1), usb.K: (1, 0), usb.SE0: (0, 0)}
USB_DEFS = usbdev.defines(addr=0, endp=0)
USB_CRCCTRL = usbdev.CRCCTRL


def rx_setup():
    mh = ModelHost()
    prog = asm.assemble((FW / "usb_ls_rx.pasm").read_text(), USB_DEFS)
    mh.apply(host.load_program(0, prog, clkdiv=1 << 8))
    mh.write(sim.core_reg(0, sim.C_CRCCTRL), USB_CRCCTRL)
    mh.ext_oe = 0b11
    mh.uio = 0b10  # J
    mh.start(1)
    mh.step(20)
    return mh


def drive(mh, states, ticks_per_bit=32.0, phase=0.0):
    t = phase
    for s in states:
        dp, dm = LINE[s]
        mh.uio = dp | (dm << 1)
        t += ticks_per_bit
        n = int(t)
        t -= n
        mh.step(n)
    mh.uio = 0b10  # back to idle J
    mh.step(200)


def rx_record(mh):
    """The one record (or None) the RX core pushed for the last packet."""
    recs, rest = usbdev.parse_records(mh.pop_all(0))
    assert not rest and len(recs) <= 1, (recs, rest)
    return recs[0] if recs else None


@pytest.mark.parametrize("ppm", [0, 15000, -15000, 25000, -25000])
def test_usb_rx_packets(ppm):
    """Tokens/handshakes only raise flags; DATA after SETUP/OUT gives a record."""
    mh = rx_setup()
    rng = random.Random(ppm)
    tpb = 32 * (1 + ppm / 1e6)
    setup = bytes([0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x40, 0x00])
    drive(mh, usb.encode(usb.token(usb.PID_SETUP, 0, 0)), tpb, rng.random())
    assert rx_record(mh) is None and mh.chip.flags & 0b1100 == 0b1100  # F_EXP, F_SETUP
    drive(mh, usb.encode(usb.data_packet(usb.PID_DATA0, setup)), tpb, rng.random())
    assert rx_record(mh) == usbdev.Record(True, setup, 16 + 8 * 10, setup=True)
    assert mh.chip.flags & 0b0110 == 0b0010  # F_ACK set, F_EXP cleared
    drive(mh, usb.encode(usb.handshake(usb.PID_ACK)), tpb, rng.random())
    assert rx_record(mh) is None and mh.chip.flags & usbdev.F_GOTACK
    drive(mh, usb.encode(usb.token(usb.PID_OUT, 0, 0)), tpb, rng.random())
    ones = bytes([0xFF] * 8)  # lots of stuffing
    drive(mh, usb.encode(usb.data_packet(usb.PID_DATA1, ones)), tpb, rng.random())
    assert rx_record(mh).data == ones
    for i in range(1, 9):     # every payload length, including zero
        drive(mh, usb.encode(usb.token(usb.PID_OUT, 0, 0)), tpb, rng.random())
        p = bytes(range(i - 1))
        drive(mh, usb.encode(usb.data_packet(usb.PID_DATA0, p)), tpb, rng.random())
        assert rx_record(mh) == usbdev.Record(True, p, 16 + 8 * (len(p) + 2))
    drive(mh, usb.encode(usb.token(usb.PID_IN, 0, 0)), tpb, rng.random())
    assert rx_record(mh) is None and mh.chip.flags & 1


@pytest.mark.parametrize("tok", [
    usb.token(usb.PID_IN, 0x15, 0),             # another address
    usb.token(usb.PID_IN, 0, 1),                # another endpoint
    usb.token(usb.PID_SETUP, 0x15, 0xE),
    usb.token(usb.PID_IN, 0, 0)[:2] + bytes([usb.token(usb.PID_IN, 0, 0)[2] ^ 0x80]),  # bad CRC5
])
def test_usb_tokens_for_others_ignored(tok):
    mh = rx_setup()
    drive(mh, usb.encode(tok))
    assert mh.chip.flags == 0 and rx_record(mh) is None


def test_usb_bad_data_gives_a_bad_record_then_retry_parses():
    """A DATA packet with a bad CRC still gives one complete record (marked bad),
    so the next good packet parses cleanly."""
    mh = rx_setup()
    setup = bytes([0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x40, 0x00])
    good = usb.data_packet(usb.PID_DATA0, setup)
    bad = good[:-1] + bytes([good[-1] ^ 0x10])
    words = []
    for pkt in (bad, good):
        drive(mh, usb.encode(usb.token(usb.PID_SETUP, 0, 0)))
        drive(mh, usb.encode(pkt))
        words += mh.pop_all(0)
    recs, rest = usbdev.parse_records(words)
    assert [r.ok for r in recs] == [False, True] and not rest
    assert recs[1].data == setup


# ---------------------------------------------------------------- transactions

def device_setup():
    """RX on core 0, TX on core 1 (owns D+/D-), 48 MHz, CLKDIV 1, address 0."""
    mh = ModelHost()
    rx = asm.assemble((FW / "usb_ls_rx.pasm").read_text(), USB_DEFS)
    tx = asm.assemble((FW / "usb_ls_tx.pasm").read_text(), USB_DEFS)
    mh.apply(host.pin_owner_writes({DP: 1, DM: 1}))
    mh.apply(host.load_program(0, rx, clkdiv=1 << 8))
    mh.write(sim.core_reg(0, sim.C_CRCCTRL), USB_CRCCTRL)
    mh.apply(host.load_program(1, tx, out_pins=(DP, 2), clkdiv=1 << 8))
    mh.pull_up = 0b10  # 1.5k pull-up on D- (low-speed device), J when idle
    mh.start(0b11)
    mh.step(50)
    return mh


def host_send(mh, packet, tpb=32.0):
    mh.ext_oe = 0b11
    drive(mh, usb.encode(packet), tpb)  # ends with J driven, then idle
    mh.ext_oe = 0
    mh.pop_all(0)  # the host drains the RX FIFO promptly


def host_send_then_listen(mh, packet, tpb=32.0, listen=4000):
    """Send a packet, release the bus, and decode whatever the device answers."""
    mh.ext_oe = 0b11
    t = 0.0
    for s in usb.encode(packet):
        dp, dm = LINE[s]
        mh.uio = dp | (dm << 1)
        t += tpb
        n = int(t)
        t -= n
        mh.step(n)
    mh.ext_oe = 0  # release after our EOP's J
    mh.pop_all(0)
    trace = []
    first_k = None
    for i in range(listen):
        v = mh.bus_uio() & 3
        s = {0b10: usb.J, 0b01: usb.K, 0b00: usb.SE0}.get(v, "?")
        trace.append(s)
        if first_k is None and s == usb.K:
            first_k = i
        mh.step(1)
    if first_k is None:
        return None, None
    # sample at the middle of each bit cell from the first K
    states = [trace[j] for j in range(first_k + 16, len(trace), 32)]
    return usb.decode([usb.J] + states), first_k


def test_usb_setup_gets_ack():
    mh = device_setup()
    host_send(mh, usb.token(usb.PID_SETUP, 0, 0))
    resp, turnaround = host_send_then_listen(
        mh, usb.data_packet(usb.PID_DATA0, bytes([0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x40, 0x00])))
    assert resp == usb.handshake(usb.PID_ACK), resp
    assert turnaround < 7.5 * 32, turnaround  # device must start answering within 7.5 bit times


def test_usb_in_nak_then_data():
    mh = device_setup()
    # nothing staged: NAK
    resp, _ = host_send_then_listen(mh, usb.token(usb.PID_IN, 0, 0))
    assert resp == usb.handshake(usb.PID_NAK), resp
    # stage the first 8 bytes of a device descriptor
    desc = bytes([0x12, 0x01, 0x10, 0x01, 0x00, 0x00, 0x00, 0x08])
    words = usbdev.stage_words(usb.PID_DATA1, desc)
    for w in words[:2]:                # half-staged: still NAK
        mh.push(1, w)
    resp, _ = host_send_then_listen(mh, usb.token(usb.PID_IN, 0, 0))
    assert resp == usb.handshake(usb.PID_NAK), resp
    for w in words[2:]:
        mh.push(1, w)
    mh.write(sim.A_FSET, usbdev.F_STAGED)
    resp, turnaround = host_send_then_listen(mh, usb.token(usb.PID_IN, 0, 0))
    assert resp == usb.data_packet(usb.PID_DATA1, desc), resp
    assert turnaround < 7.5 * 32


def test_usb_bad_crc_not_acked():
    mh = device_setup()
    host_send(mh, usb.token(usb.PID_SETUP, 0, 0))
    good = usb.data_packet(usb.PID_DATA0, bytes([0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x40, 0x00]))
    bad = good[:-1] + bytes([good[-1] ^ 0x10])
    resp, _ = host_send_then_listen(mh, bad)
    assert resp is None, resp          # no handshake: the host will retry
    host_send(mh, usb.token(usb.PID_SETUP, 0, 0))
    resp, _ = host_send_then_listen(mh, good)
    assert resp == usb.handshake(usb.PID_ACK), resp
