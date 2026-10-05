"""USB low-speed enumeration on the model: a host model runs control transfers
against protoemu (RX core 0, TX core 1) plus slow 'device software' on the SPI
host side that decodes SETUP requests, stages descriptor packets, re-stages a
packet the host didn't acknowledge, and moves the token filter to the new
address after SET_ADDRESS."""
import pathlib
import random

import pytest

from pe import asm, host, sim, usb, usbdev
from pe.usbdev import CONFIG_DESC, DESCRIPTORS, DEVICE_DESC
from pe.modelhost import ModelHost
from pe.usbhost import UsbHost

FW = pathlib.Path(__file__).resolve().parents[2] / "firmware"
RX, TX = 0, 1

LINECFG = 0x49F3                  # EN NRZI STUFF_K=6 ONES DIFF END=SE0 END_BITS=2 RX_EN
VARIANTS = ["two-core", "line"]   # usb_ls_rx + usb_ls_tx, or usb_ls_dev on one core


def device(variant="two-core"):
    """(ModelHost, DeviceSoftware) for one firmware variant, bus idle at J."""
    mh = ModelHost()
    defs = usbdev.defines()
    if variant == "two-core":
        rx = asm.assemble((FW / "usb_ls_rx.pasm").read_text(), defs)
        tx = asm.assemble((FW / "usb_ls_tx.pasm").read_text(), defs)
        mh.apply(host.pin_owner_writes({0: TX, 1: TX}))
        mh.apply(host.load_program(RX, rx, 0, clkdiv=1 << 8))
        mh.write(sim.core_reg(RX, sim.C_CRCCTRL), usbdev.CRCCTRL)
        mh.apply(host.load_program(TX, tx, out_pins=(0, 2), clkdiv=1 << 8))
        mask, rx_core, tx_core = 0b11, RX, TX
    else:
        rx = asm.assemble((FW / "usb_ls_dev.pasm").read_text(), defs)
        mh.apply(host.load_program(0, rx, 0, clkdiv=1 << 8))
        mh.write(sim.core_reg(0, sim.C_CRCCTRL), usbdev.CRCCTRL)
        mh.write(sim.A_LINEDIV, (32 << 16) | (32 << 8))
        mh.write(sim.A_LINEPINS, 0)                   # D+ = uio0 (D- = uio1)
        mh.write(sim.A_LINECFG, LINECFG)
        mask, rx_core, tx_core = 0b01, 0, 0
    mh.pull_up = 0b10  # the device's D- pull-up: the bus idles in J
    mh.start(mask)
    mh.step(50)
    return mh, usbdev.DeviceSoftware(mh, rx, DESCRIPTORS, rx_core, tx_core)


@pytest.mark.parametrize("variant", VARIANTS)
@pytest.mark.parametrize("seed", [None, 1, 2])
def test_enumeration(seed, variant):
    """seed: None = service every 300 cycles; else random 100..3000-cycle stalls."""
    mh, sw = device(variant)
    rng = random.Random(seed)

    def idle(m):
        m.step(300 if seed is None else rng.randrange(100, 3000))   # the SPI host is slow
        sw.service(m)

    h = UsbHost(mh, idle=idle)
    # 1) first 8 bytes of the device descriptor (bMaxPacketSize0)
    d = h.control_read(0, bytes([0x80, 6, 0, 1, 0, 0, 64, 0]), 64)
    assert d[:8] == DEVICE_DESC[:8], d.hex()
    # 2) SET_ADDRESS 5
    h.control_write_nodata(0, bytes([0x00, 5, 5, 0, 0, 0, 0, 0]))
    # 3) full device descriptor and configuration descriptor (multi-packet reads)
    d = h.control_read(5, bytes([0x80, 6, 0, 1, 0, 0, 18, 0]), 18)
    assert d == DEVICE_DESC, d.hex()
    c = h.control_read(5, bytes([0x80, 6, 0, 2, 0, 0, 34, 0]), 34)
    assert c == CONFIG_DESC, c.hex()
    # 4) SET_CONFIGURATION 1
    h.control_write_nodata(5, bytes([0x00, 9, 1, 0, 0, 0, 0, 0]))
    assert sw.address == 5
    naks = sum(1 for e in h.log if e[0] == "IN" and e[1] == "nak")
    print(f"enumerated in {mh.chip.cycle} cycles ({mh.chip.cycle / 48e3:.1f} ms at 48 MHz), "
          f"{len(sw.requests)} requests, {naks} NAKs while the software was busy")


@pytest.mark.parametrize("variant", VARIANTS)
def test_address_filter_follows_set_address(variant):
    """After SET_ADDRESS 5 the device answers address 5 only."""
    mh, sw = device(variant)
    h = UsbHost(mh, idle=lambda m: (m.step(300), sw.service(m)))
    h.control_write_nodata(0, bytes([0x00, 5, 5, 0, 0, 0, 0, 0]))
    assert sw.address == 5
    h._send(usb.token(usb.PID_IN, 0, 0))
    assert h._listen(120 * h.bit) is None          # the old address is ignored
    h._send(usb.token(usb.PID_IN, 5, 0))
    assert h._listen(120 * h.bit) == usb.handshake(usb.PID_NAK)


@pytest.mark.parametrize("variant", VARIANTS)
def test_lost_ack_restages_the_same_packet(variant):
    """The host misses our DATA1 (sends no ACK): the device software re-stages it,
    so the next IN gets the same DATA1, and only then the next chunk as DATA0."""
    mh, sw = device(variant)
    h = UsbHost(mh, idle=lambda m: (m.step(300), sw.service(m)))
    h.setup(0, bytes([0x80, 6, 0, 1, 0, 0, 18, 0]))
    got = None
    for _ in range(200):                           # IN until data, but don't ACK it
        h._send(usb.token(usb.PID_IN, 0, 0))
        got = h._listen(120 * h.bit)
        if got and got[0] == usb.pid_byte(usb.PID_DATA1):
            break
        h.idle(mh)
    assert got == usb.data_packet(usb.PID_DATA1, DEVICE_DESC[:8])
    h._gap(16)
    h.idle(mh)
    pid, payload = h.data_in(0, 0)                 # retried: same toggle, same data
    assert (pid, payload) == (usb.pid_byte(usb.PID_DATA1), DEVICE_DESC[:8]) and sw.restaged == 1
    pid, payload = h.data_in(0, 0)
    assert (pid, payload) == (usb.pid_byte(usb.PID_DATA0), DEVICE_DESC[8:16])


@pytest.mark.parametrize("variant", VARIANTS)
def test_invalid_pid_no_ack_and_valid_retry(variant):
    mh, sw = device(variant)
    h = UsbHost(mh)
    req = bytes.fromhex("8006000100001200")
    h._send(usb.token(usb.PID_SETUP, 0, 0))
    h._gap(2)
    pkt = usb.data_packet(usb.PID_DATA0, req)
    h._send(bytes([pkt[0] ^ 0x10]) + pkt[1:])
    assert h._listen(120 * h.bit) is None
    assert not sw.records()
    h.idle = lambda m: (m.step(300), sw.service(m))
    assert h.control_read(0, req, 18) == DEVICE_DESC


@pytest.mark.parametrize("variant", VARIANTS)
@pytest.mark.parametrize("pending", [False, True])
def test_bus_reset_restores_default_address_and_discards_transfer(variant, pending):
    mh, sw = device(variant)
    h = UsbHost(mh, idle=lambda m: (m.step(300), sw.service(m)))
    h.control_write_nodata(0, bytes.fromhex("0005050000000000"))
    assert sw.address == 5
    if pending:
        h.setup(5, bytes.fromhex("8006000100001200"))
        assert sw.unacked or sw.pending
        sw.new_address = 7
    mh.ext_oe, mh.uio = 3, 0
    # Ten milliseconds SE0, with a bounded, realistically intermittent service loop.
    for _ in range(480):
        mh.step(1000)
        sw.service(mh)
    assert sw.address == 0 and sw.reset_wait
    assert not sw.pending and sw.unacked is None and sw.new_address is None
    mh.ext_oe = 0
    mh.step(1000)
    sw.service(mh)
    mh.step(480000)  # host's reset recovery interval
    assert not sw.reset_wait
    h._send(usb.token(usb.PID_IN, 5, 0))
    assert h._listen(120 * h.bit) is None
    assert h.control_read(0, bytes.fromhex("8006000100001200"), 18) == DEVICE_DESC


@pytest.mark.parametrize("variant", VARIANTS)
def test_bus_reset_requires_both_lines_low(variant):
    mh, _ = device(variant)
    mask = 3 if variant == "two-core" else 1
    mh.write(sim.A_CTRL, 0)
    mh.ext_oe, mh.uio = 3, 1        # K: D+ high, D- low
    mh.start(mask)
    mh.step(2000)
    assert not mh.read(sim.A_FSET) & usbdev.F_RESET
    mh.uio = 0                    # SE0: D+ and D- both low
    mh.step(2000)
    assert mh.read(sim.A_FSET) & usbdev.F_RESET


@pytest.mark.parametrize("variant", VARIANTS)
def test_duplicate_out_is_acked_but_delivered_once(variant):
    mh, sw = device(variant)
    h = UsbHost(mh, idle=lambda m: (m.step(300), sw.service(m)))
    h.data_out(0, 0, usb.PID_DATA1, b"abc")
    h.data_out(0, 0, usb.PID_DATA1, b"abc")  # lost ACK: repeated toggle
    h.data_out(0, 0, usb.PID_DATA0, b"def")
    assert sw.out_records == [b"abc", b"def"]


@pytest.mark.parametrize("variant", VARIANTS)
def test_missing_stuffed_bit_not_acked_and_retry_recovers(variant):
    mh, sw = device(variant)
    h = UsbHost(mh)
    req = bytes([0xFF]*8)
    h._send(usb.token(usb.PID_SETUP, 0, 0))
    h._gap(2)
    states, level, ones, skipped = [], usb.J, 0, False
    for b in usb.bits_lsb_first(bytes([0x80]) + usb.data_packet(usb.PID_DATA0, req)):
        if not b:
            level = usb.K if level == usb.J else usb.J
            ones = 0
        else:
            ones += 1
        states.append(level)
        if ones == 6:
            if not skipped:
                skipped = True  # malformed seven-one run, CRC bytes otherwise unchanged
            else:
                level = usb.K if level == usb.J else usb.J
                states.append(level)
            ones = 0
    mh.ext_oe = 3
    for state in states + [usb.SE0, usb.SE0, usb.J]:
        mh.uio = {usb.J: 2, usb.K: 1, usb.SE0: 0}[state]
        mh.step(32)
    mh.ext_oe = 0
    assert h._listen(120*32) is None
    sw.records()
    h.idle = lambda m: (m.step(300), sw.service(m))
    assert h.control_read(0, bytes.fromhex("8006000100001200"), 18) == DEVICE_DESC
