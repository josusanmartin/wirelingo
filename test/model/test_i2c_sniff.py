"""I2C master (core 0) talks to an EEPROM model while the sniffer (core 1) watches."""
import pathlib

from pe import asm, host, sim
from pe.modelhost import ModelHost

FW = pathlib.Path(__file__).resolve().parents[2] / "firmware"
SDA, SCL = 0, 1


class Eeprom:
    """Minimal I2C target at 0x50 (ACKs its address and every written byte)."""

    def __init__(self, mh):
        self.mh, self.pscl, self.psda = mh, 1, 1
        self.state, self.bits, self.byte, self.ack_next = "idle", 0, 0, False

    def tick(self):
        m = self.mh
        v = m.bus_uio()
        sda, scl = v & 1, (v >> 1) & 1
        if scl and self.pscl and self.psda and not sda:
            self.state, self.bits, self.byte = "rx", 0, 0
        elif scl and self.pscl and not self.psda and sda:
            self.state = "idle"
        elif scl and not self.pscl and self.state == "rx" and self.bits < 8:
            self.byte = (self.byte << 1) | sda
            self.bits += 1
        elif self.pscl and not scl:
            if self.state == "rx" and self.bits == 8:
                ok = (self.byte >> 1) == 0x50 if self.first else True
                m.ext_oe = (m.ext_oe | 1) if ok else (m.ext_oe & ~1)
                self.state = "ack"
            elif self.state == "ack":
                m.ext_oe &= ~1
                self.state, self.bits, self.byte = "rx", 0, 0
                self.first = False
        if self.state == "rx" and self.bits == 0 and not hasattr(self, "first_set"):
            pass
        self.pscl, self.psda = scl, sda

    first = True


def test_sniffer_sees_the_transaction():
    mh = ModelHost()
    master = asm.assemble((FW / "i2c_master.pasm").read_text(), {"SDA": SDA, "SCL": SCL})
    sniff = asm.assemble((FW / "i2c_sniff.pasm").read_text(), {"SDA": SDA, "SCL": SCL})
    mh.apply(host.pin_owner_writes({SDA: 0, SCL: 0}))
    mh.write(sim.A_POUT, 0b11)
    mh.write(sim.A_OD, 0b11)
    mh.pull_up = 0b11
    mh.uio = 0
    mh.apply(host.load_program(0, master, out_pins=(SDA, 1), in_pins=SDA, clkdiv=12 << 8))
    mh.apply(host.load_program(1, sniff, in_pins=SDA, clkdiv=1 << 8))
    mh.start(0b11)
    ee = Eeprom(mh)
    START, STOP = 2, 3
    cmds = [START, 0xA0 << 24, 0x10 << 24, 0x42 << 24, STOP]
    events = []
    for c in cmds:
        while mh.tx_level(0) >= 4:
            ee.tick(); mh.step()
        mh.push(0, c)
    for _ in range(200000):
        ee.tick()
        mh.step()
        mh.pop_all(0)                       # master's ACK replies
        events += mh.pop_all(1)
        if 0x200 in events:
            break
    assert events == [0x100, 0xA0, 0x10, 0x42, 0x200], [hex(e) for e in events]
