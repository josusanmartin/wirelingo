"""SWD probe firmware vs an SWD target model (ARM DP: DPIDR read, SELECT write/read)."""
import pathlib

from pe import asm, host, sim
from pe.modelhost import ModelHost

FW = pathlib.Path(__file__).resolve().parents[2] / "firmware"
SWCLK, SWDIO = 2, 3


class SwdTarget:
    """Samples SWDIO on SWCLK rising edges, drives it right after them."""
    DPIDR = 0x0BC12477

    def __init__(self, mh):
        self.mh, self.prev_clk = mh, 0
        self.regs = {0x0: self.DPIDR, 0x8: 0, 0x4: 0}
        self.state, self.bits, self.out = "idle", [], []
        self.log = []

    def drive(self, bit):
        m = self.mh
        if bit is None:
            m.ext_oe &= ~(1 << SWDIO)
        else:
            m.ext_oe |= 1 << SWDIO
            m.uio = (m.uio & ~(1 << SWDIO)) | (bit << SWDIO)

    def tick(self, cyc, m):
        clk = (m.bus_uio() >> SWCLK) & 1
        if clk and not self.prev_clk:
            self.rising((m.bus_uio() >> SWDIO) & 1)
        self.prev_clk = clk

    def rising(self, d):
        if self.out:                      # we own the line: shift out the next bit
            self.drive(self.out.pop(0))
            if not self.out and self.state == "rdata":
                self.state = "trn_back"
            return
        if self.state == "idle":
            if d == 1:
                self.state, self.bits = "req", [1]
        elif self.state == "req":
            self.bits.append(d)
            if len(self.bits) == 8:
                b = self.bits
                apndp, rnw, a = b[1], b[2], (b[3] << 2) | (b[4] << 3)
                ok = (b[1] ^ b[2] ^ b[3] ^ b[4]) == b[5] and b[6] == 0 and b[7] == 1
                self.req = (apndp, rnw, a)
                self.log.append(("req", apndp, rnw, hex(a), ok))
                self.state = "trn" if ok else "idle"
        elif self.state == "trn":
            # turnaround done: ACK OK (0b001 LSB first), then read data + parity
            self.drive(1)
            ack = [0, 0]
            apndp, rnw, a = self.req
            if rnw:
                v = self.regs.get(a, 0)
                data = [(v >> i) & 1 for i in range(32)]
                self.out = ack + data + [sum(data) & 1]
                self.state = "rdata"
            else:
                self.out = ack + [None]   # then release for the host's turnaround
                self.state, self.bits = "wtrn", []
        elif self.state == "trn_back":
            self.drive(None)
            self.state = "idle"
        elif self.state == "wtrn":
            self.state = "wdata"          # this edge is the turnaround cycle
        elif self.state == "wdata":
            self.bits.append(d)
            if len(self.bits) == 33:
                v = sum(b << i for i, b in enumerate(self.bits[:32]))
                par_ok = (sum(self.bits[:32]) & 1) == self.bits[32]
                self.regs[self.req[2]] = v
                self.log.append(("write", hex(self.req[2]), hex(v), par_ok))
                self.state = "idle"


class Probe:
    def __init__(self, mh, target):
        self.mh, self.t = mh, target
        self.fifo = sim.core_reg(0, sim.C_FIFO)

    def run_until(self, cond, limit=200000):
        for _ in range(limit):
            if cond():
                return
            self.t.tick(0, self.mh)
            self.mh.step()
        raise TimeoutError

    def write_bits(self, n, value):
        self.mh.push(0, n - 1)
        for i in range(0, n, 32):
            self.mh.push(0, (value >> i) & 0xFFFFFFFF)
        self.run_until(lambda: self.mh.chip.cores[0].rxf)
        assert self.mh.pop_all(0) == [0]

    def read_bits(self, n):
        self.mh.push(0, (n - 1) | (1 << 9))
        want = (n + 31) // 32
        got = []
        self.run_until(lambda: len(self.mh.chip.cores[0].rxf) >= want or got.extend(self.mh.pop_all(0)) or len(got) >= want)
        got += self.mh.pop_all(0)
        return sum(w << (32 * i) for i, w in enumerate(got[:want]))

    @staticmethod
    def request(apndp, rnw, a):
        a2, a3 = (a >> 2) & 1, (a >> 3) & 1
        par = apndp ^ rnw ^ a2 ^ a3
        return 1 | apndp << 1 | rnw << 2 | a2 << 3 | a3 << 4 | par << 5 | 0 << 6 | 1 << 7

    def dp_read(self, a):
        self.write_bits(8, self.request(0, 1, a))
        v = self.read_bits(1 + 3 + 32 + 1)          # trn, ack, data, parity
        self.write_bits(8, 0)                       # trn back to the host + idle (SWDIO low)
        ack = (v >> 1) & 7
        data = (v >> 4) & 0xFFFFFFFF
        par = (v >> 36) & 1
        return ack, data, par == (bin(data).count("1") & 1)

    def dp_write(self, a, value):
        self.write_bits(8, self.request(0, 0, a))
        v = self.read_bits(1 + 3 + 1)               # trn, ack, trn
        ack = (v >> 1) & 7
        self.write_bits(33, value | ((bin(value).count("1") & 1) << 32))
        self.write_bits(8, 0)                       # idle
        return ack


def setup():
    mh = ModelHost()
    prog = asm.assemble((FW / "swd_probe.pasm").read_text())
    mh.apply(host.load_program(0, prog, out_pins=(SWDIO, 1), set_pins=(SWDIO, 1), side_pins=SWCLK,
                               in_pins=SWDIO, clkdiv=3 << 8))
    mh.write(sim.A_POE, 1 << SWCLK)          # SWCLK is an output
    mh.pull_up = 1 << SWDIO
    mh.start(1)
    mh.step(20)
    t = SwdTarget(mh)
    return mh, t, Probe(mh, t)


def test_swd_dp_read_write():
    mh, t, p = setup()
    p.write_bits(8, 0)                       # a few idle cycles
    ack, idr, par_ok = p.dp_read(0x0)
    assert ack == 0b001 and par_ok, (ack, t.log)
    assert idr == SwdTarget.DPIDR, hex(idr)
    assert p.dp_write(0x8, 0x0100_00F0) == 0b001   # SELECT
    assert ("write", "0x8", "0x10000f0", True) in t.log, t.log
    ack, v, par_ok = p.dp_read(0x8)
    assert ack == 1 and v == 0x0100_00F0 and par_ok
