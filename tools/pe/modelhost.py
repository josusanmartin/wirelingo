"""Run firmware on the cycle-accurate model with pin-level peripherals.

The model is lockstep-verified against the RTL (test/test_lockstep.py), so this
is a fast way to develop and test firmware; test/test.py then confirms on RTL.

    mh = ModelHost()
    mh.apply(host.load_program(0, prog, ...)); mh.start(0)
    mh.run(1000, lambda cyc, outs: ...)      # callback sees outputs, returns inputs
"""
from . import sim, host


class ModelHost:
    def __init__(self, bank_words=256):
        self.chip = sim.Chip(bank_words=bank_words)
        self.ui = 0      # ui_in[4:0]
        self.uio = 0     # value external devices drive on uio (where they drive)
        self.ext_oe = 0  # uio bits driven externally
        self.pull_up = 0

    # -- host bus
    def write(self, addr, data):
        self.chip.step(self.ui, self.bus_uio(), we=True, addr=addr, wdata=data)

    def read(self, addr, pop=False):
        v = self.chip.read(addr)
        self.chip.step(self.ui, self.bus_uio(), addr=addr, commit=pop)
        return v

    def apply(self, writes):
        for a, d in writes:
            self.write(a, d)

    def start(self, mask):
        self.write(sim.A_CTRL, (self.chip.enable | mask) | (mask << 8))

    def push(self, core, word):
        self.write(sim.core_reg(core, sim.C_FIFO), word)

    def pop_all(self, core):
        out = []
        while self.chip.cores[core].rxf:
            out.append(self.read(sim.core_reg(core, sim.C_FIFO), pop=True))
        return out

    def tx_level(self, core):
        return len(self.chip.cores[core].txf)

    # -- pins
    def bus_uio(self):
        """Resolved uio bus (wired-AND of chip and external drivers, pull-ups)."""
        _, out, oe = self.chip.outputs()
        v = 0
        for i in range(8):
            c, e = (oe >> i) & 1, (self.ext_oe >> i) & 1
            if c and e:
                b = (out >> i) & 1 & (self.uio >> i) & 1
            elif c:
                b = (out >> i) & 1
            elif e:
                b = (self.uio >> i) & 1
            else:
                b = (self.pull_up >> i) & 1
            v |= b << i
        return v

    def step(self, n=1):
        for _ in range(n):
            self.chip.step(self.ui, self.bus_uio())

    def run(self, n, fn=None):
        """Step n cycles; fn(cycle, chip) may update self.ui/uio/ext_oe before each step."""
        for i in range(n):
            if fn:
                if fn(i, self) is False:
                    return i
            self.chip.step(self.ui, self.bus_uio())
        return n
