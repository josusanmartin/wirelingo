"""USB low-speed enumeration on the model: a host model runs control transfers
against protoemu (RX core 0, TX core 1) plus slow 'device software' on the SPI
host side that decodes SETUP requests, stages descriptor packets, re-stages a
packet the host didn't acknowledge, and moves the token filter to the new
address after SET_ADDRESS."""
import pathlib
import random

import pytest

from pe import asm, host, sim, usb, usbdev
from pe.modelhost import ModelHost
from pe.usbhost import UsbHost

FW = pathlib.Path(__file__).resolve().parents[2] / "firmware"
RX, TX = 0, 1

DEVICE_DESC = bytes([18, 1, 0x10, 0x01, 0, 0, 0, 8, 0x09, 0x12, 0x34, 0x56, 0x01, 0x00, 1, 2, 0, 1])
CONFIG_DESC = bytes([9, 2, 34, 0, 1, 1, 0, 0x80, 50,             # configuration
                     9, 4, 0, 0, 1, 3, 1, 1, 0,                  # interface: HID boot keyboard
                     9, 0x21, 0x11, 0x01, 0, 1, 0x22, 63, 0,     # HID
                     7, 5, 0x81, 3, 8, 0, 10])                   # endpoint 1 IN, interrupt


class DeviceSoftware:
    """What the RP2350 would run: read RX records over SPI, answer control requests.
    It only gets a turn between host transactions (via UsbHost.idle), and each
    turn is slow, so the chip's automatic NAKs cover the gap."""

    def __init__(self, mh, rx_prog):
        self.mh, self.rx_prog = mh, rx_prog
        self.words = []
        self.pending = []      # 8-byte chunks still to send for the current request
        self.toggle = 1
        self.unacked = None    # (pid, payload) staged or sent, not yet acknowledged
        self.new_address = None
        self.address = 0
        self.requests = []
        self.restaged = 0

    def records(self):
        self.words += self.mh.pop_all(RX)
        recs, self.words = usbdev.parse_records(self.words)
        return [r for r in recs if r.ok]       # bad packets were not ACKed: the host retries

    def stage(self, pid, payload):
        mh = self.mh
        mh.write(sim.A_FCLR, usbdev.F_GOTACK)
        for w in usbdev.stage_words(pid, payload):
            mh.push(TX, w)
        mh.write(sim.A_FSET, usbdev.F_STAGED)
        self.unacked = (pid, payload)

    def set_address(self, addr):
        """Move the RX token filter (its core briefly disabled: docs/isa.md §3)."""
        mh = self.mh
        en = mh.chip.read(sim.A_CTRL)
        mh.write(sim.A_CTRL, en & ~(1 << RX))
        mh.write(*usbdev.filter_write(self.rx_prog, 0, addr))
        mh.write(sim.A_CTRL, en)
        self.address = addr

    def service(self, mh):
        flags = mh.read(sim.A_FSET)
        for r in self.records():
            if flags & usbdev.F_SETUP and len(r.data) == 8:
                mh.write(sim.A_FCLR, usbdev.F_SETUP)
                self.requests.append(r.data.hex())
                self.handle(r.data)
                flags = mh.read(sim.A_FSET)
        if self.unacked and not flags & usbdev.F_STAGED:      # the TX core sent it
            if flags & usbdev.F_GOTACK:
                mh.write(sim.A_FCLR, usbdev.F_GOTACK)
                self.unacked = None
                if self.new_address is not None and not self.pending:
                    self.set_address(self.new_address)      # after the status stage
                    self.new_address = None
            else:                                           # lost ACK: same packet again
                self.stage(*self.unacked)
                self.restaged += 1
        if not self.unacked and self.pending:
            chunk = self.pending.pop(0)
            self.stage(usb.PID_DATA1 if self.toggle else usb.PID_DATA0, chunk)
            self.toggle ^= 1

    def handle(self, req):
        bm, breq, wval, wlen = req[0], req[1], req[2] | req[3] << 8, req[6] | req[7] << 8
        self.toggle = 1
        if self.unacked:                       # a new SETUP abandons the old transfer
            self.mh.write(sim.A_FCLR, usbdev.F_STAGED | usbdev.F_GOTACK)
            en = self.mh.chip.read(sim.A_CTRL)
            self.mh.write(sim.A_CTRL, en | (1 << (16 + TX)))   # clear the TX core's FIFOs
            self.unacked = None
        if bm == 0x80 and breq == 6:           # GET_DESCRIPTOR
            desc = {1: DEVICE_DESC, 2: CONFIG_DESC}.get(wval >> 8, b"")[:wlen]
            self.pending = [desc[i:i + 8] for i in range(0, len(desc), 8)]
            if len(desc) % 8 == 0:
                self.pending.append(b"")       # terminate with a zero-length packet
        elif bm == 0x00 and breq == 5:         # SET_ADDRESS (status stage is a zero-length IN)
            self.new_address = wval
            self.pending = [b""]
        elif bm == 0x00 and breq == 9:         # SET_CONFIGURATION
            self.pending = [b""]


def device():
    mh = ModelHost()
    rx = asm.assemble((FW / "usb_ls_rx.pasm").read_text(), usbdev.defines())
    tx = asm.assemble((FW / "usb_ls_tx.pasm").read_text(), usbdev.defines())
    mh.apply(host.pin_owner_writes({0: TX, 1: TX}))
    mh.apply(host.load_program(RX, rx, 0, clkdiv=1 << 8))
    mh.write(sim.core_reg(RX, sim.C_CRCCTRL), usbdev.CRCCTRL)
    mh.apply(host.load_program(TX, tx, out_pins=(0, 2), clkdiv=1 << 8))
    mh.pull_up = 0b10  # the device's D- pull-up: the bus idles in J
    mh.start(0b11)
    mh.step(50)
    return mh, rx


@pytest.mark.parametrize("seed", [None, 1, 2])
def test_enumeration(seed):
    """seed: None = service every 300 cycles; else random 100..3000-cycle stalls."""
    mh, rx = device()
    sw = DeviceSoftware(mh, rx)
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


def test_address_filter_follows_set_address():
    """After SET_ADDRESS 5 the device answers address 5 only."""
    mh, rx = device()
    sw = DeviceSoftware(mh, rx)
    h = UsbHost(mh, idle=lambda m: (m.step(300), sw.service(m)))
    h.control_write_nodata(0, bytes([0x00, 5, 5, 0, 0, 0, 0, 0]))
    assert sw.address == 5
    h._send(usb.token(usb.PID_IN, 0, 0))
    assert h._listen(120 * h.bit) is None          # the old address is ignored
    h._send(usb.token(usb.PID_IN, 5, 0))
    assert h._listen(120 * h.bit) == usb.handshake(usb.PID_NAK)


def test_lost_ack_restages_the_same_packet():
    """The host misses our DATA1 (sends no ACK): the device software re-stages it,
    so the next IN gets the same DATA1, and only then the next chunk as DATA0."""
    mh, rx = device()
    sw = DeviceSoftware(mh, rx)
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
