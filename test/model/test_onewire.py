"""1-Wire master firmware vs a slave model (READ ROM)."""
import pathlib

from pe import asm, host, sim
from pe.modelhost import ModelHost

FW = pathlib.Path(__file__).resolve().parents[2] / "firmware"
DQ = 0
US = 48          # clocks per microsecond at 48 MHz


class Slave:
    """Standard-speed 1-Wire slave: presence, READ ROM (0x33)."""

    def __init__(self, mh, rom):
        self.mh, self.rom = mh, rom
        self.low_since, self.prev = None, 1
        self.pull_until = -1
        self.state, self.bits, self.byte = "idle", 0, 0
        self.tx_bits = []
        self.t = 0

    def tick(self):
        m, t = self.mh, self.t
        # our own pull-down
        m.ext_oe = (m.ext_oe | 1) if t < self.pull_until else (m.ext_oe & ~1)
        m.uio &= ~1
        line = m.bus_uio() & 1
        if self.prev == 1 and line == 0 and t >= self.pull_until:   # master started a slot/reset
            self.low_since = t
            if self.state == "tx" and self.tx_bits:
                if self.tx_bits.pop(0) == 0:
                    self.pull_until = t + 30 * US                   # read slot: send a 0
        if self.prev == 0 and line == 1 and self.low_since is not None:
            dur = t - self.low_since
            if dur > 400 * US:                                      # reset: presence after 30 us
                self.state, self.bits, self.byte = "cmd", 0, 0
                self.presence_at = t + 30 * US
            elif self.state == "cmd":
                self.byte |= (1 if dur < 15 * US else 0) << self.bits
                self.bits += 1
                if self.bits == 8:
                    if self.byte == 0x33:
                        self.state = "tx"
                        self.tx_bits = [(b >> i) & 1 for b in self.rom for i in range(8)]
                    else:
                        self.state = "idle"
            self.low_since = None
        if getattr(self, "presence_at", None) is not None and t >= self.presence_at:
            self.pull_until = t + 120 * US
            self.presence_at = None
        self.prev = line
        self.t += 1


def test_read_rom():
    rom = bytes([0x28, 0xFF, 0x4C, 0x60, 0x91, 0x16, 0x04, 0x8C])
    mh = ModelHost()
    prog = asm.assemble((FW / "onewire.pasm").read_text(), {"DQ": DQ})
    mh.apply(host.load_program(0, prog, in_pins=DQ, clkdiv=US << 8))
    mh.write(sim.A_POUT, 1 << DQ)       # released
    mh.write(sim.A_OD, 1 << DQ)
    mh.pull_up = 1 << DQ
    mh.start(1)
    s = Slave(mh, rom)

    def cmd(word, n_replies=1, limit=2_000_000):
        mh.push(0, word)
        out = []
        for _ in range(limit):
            s.tick()
            mh.step()
            out += mh.pop_all(0)
            if len(out) >= n_replies:
                return out
        raise TimeoutError

    assert cmd(0) == [1], "no presence"
    assert cmd(1 | (0x33 << 8)) == [0]
    got = bytes(cmd(2)[0] & 0xFF for _ in range(8))
    assert got == rom, got.hex()
