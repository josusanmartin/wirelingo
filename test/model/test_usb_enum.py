"""USB low-speed enumeration on the model: a host model runs control transfers
against protoemu (RX core 0, TX core 1) plus slow 'device software' on the SPI
host side that decodes SETUP requests and stages descriptor packets."""
import pathlib

from pe import asm, host, sim, usb
from pe.modelhost import ModelHost
from pe.usbhost import UsbHost

FW = pathlib.Path(__file__).resolve().parents[2] / "firmware"
DEFS = {"DP": 0, "DM": 1, "F_IN": 0, "F_ACK": 1, "F_EXP": 2, "F_SETUP": 3}

DEVICE_DESC = bytes([18, 1, 0x10, 0x01, 0, 0, 0, 8, 0x09, 0x12, 0x34, 0x56, 0x01, 0x00, 1, 2, 0, 1])
CONFIG_DESC = bytes([9, 2, 34, 0, 1, 1, 0, 0x80, 50,             # configuration
                     9, 4, 0, 0, 1, 3, 1, 1, 0,                  # interface: HID boot keyboard
                     9, 0x21, 0x11, 0x01, 0, 1, 0x22, 63, 0,     # HID
                     7, 5, 0x81, 3, 8, 0, 10])                   # endpoint 1 IN, interrupt


class DeviceSoftware:
    """What the RP2350 would run: poll the RX FIFO over SPI, answer control requests.
    It only gets a turn between host transactions (via UsbHost.idle), and each
    turn is slow, so the chip's automatic NAKs cover the gap."""

    def __init__(self, mh):
        self.mh = mh
        self.words = []
        self.pending = []      # 8-byte chunks still to send for the current request
        self.toggle = 1
        self.address = 0
        self.new_address = None
        self.requests = []

    def packets(self):
        """Pushed DATA packets: full words..., partial word, bit count (incl. SYNC+PID).
        Returns the payloads (CRC16 dropped)."""
        self.words += self.mh.pop_all(0)
        out = []
        while self.words:
            for j in range(len(self.words)):
                nbits = self.words[j] - 16
                if not 0 <= nbits < 4096:
                    continue
                nfull, rem = divmod(nbits, 32)
                if j == nfull + 1:
                    body = self.words[:j]
                    data = b"".join(w.to_bytes(4, "little") for w in body[:nfull])
                    if rem:
                        data += (body[nfull] >> (32 - rem)).to_bytes(4, "little")[: rem // 8]
                    out.append(data[:-2])
                    self.words = self.words[j + 1:]
                    break
            else:
                break
        return out

    def stage(self, pid, payload):
        pkt = bytes([0x80]) + usb.data_packet(pid, payload)
        words = [int.from_bytes(pkt[i:i + 4].ljust(4, b"\0"), "little") for i in range(0, len(pkt), 4)]
        self.mh.push(1, 8 * len(pkt))
        for w in words:
            self.mh.push(1, w)

    def service(self, mh):
        pkts = self.packets()
        setup = mh.read(sim.A_FSET) & 8      # F_SETUP: the last pushed DATA was a SETUP
        for p in pkts:
            if setup and len(p) == 8:
                mh.write(sim.A_FCLR, 8)
                self.requests.append(p.hex())
                self.handle(p)
        if self.pending and mh.tx_level(1) == 0:
            chunk = self.pending.pop(0)
            self.stage(usb.PID_DATA1 if self.toggle else usb.PID_DATA0, chunk)
            self.toggle ^= 1

    def handle(self, req):
        bm, breq, wval, windex, wlen = req[0], req[1], req[2] | req[3] << 8, req[4] | req[5] << 8, req[6] | req[7] << 8
        self.toggle = 1
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
    rx = asm.assemble((FW / "usb_ls_rx.pasm").read_text(), DEFS)
    tx = asm.assemble((FW / "usb_ls_tx.pasm").read_text(), DEFS)
    mh.apply(host.pin_owner_writes({0: 1, 1: 1}))
    mh.apply(host.load_program(0, rx, clkdiv=1 << 8))
    mh.write(sim.core_reg(0, sim.C_CRCCTRL), 0x8005 | (15 << 16) | (1 << 20))
    mh.apply(host.load_program(1, tx, out_pins=(0, 2), clkdiv=1 << 8))
    mh.pull_up = 0b10  # the device's D- pull-up: the bus idles in J
    mh.start(0b11)
    mh.step(50)
    return mh


def test_enumeration():
    mh = device()
    sw = DeviceSoftware(mh)

    def idle(m):
        m.step(300)          # the SPI host is slow: it gets a turn every ~300 cycles
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
    naks = sum(1 for e in h.log if e[0] == "IN" and e[1] == "nak")
    print(f"enumerated in {mh.chip.cycle} cycles ({mh.chip.cycle / 48e3:.1f} ms at 48 MHz), "
          f"{len(sw.requests)} requests, {naks} NAKs while the software was busy")
