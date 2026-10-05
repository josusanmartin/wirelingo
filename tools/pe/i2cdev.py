"""Host side of firmware/i2c_device.pasm: the device's register model.

The core runs the bus (address match, ACKs, bits, clock stretching); the host
decides what the device *is*. The default model is a register-pointer device
(24Cxx EEPROM, most sensors): the first byte written after the address sets the
pointer, further written bytes are stored, reads return bytes from the pointer;
the pointer increments after each byte.

    dev = Device(addr=0x50, mem={0x10: 0xDE, ...})
    writes = dev.setup_writes(core=1, prog=..., sda=0, scl=1)  # load + R3 = address
    ...
    for word in drained RX words of that core:
        for tx in dev.handle(word):      # bytes to push to its TX FIFO
            push(tx)

`on_read(reg, value) -> value` changes what a read returns (man in the middle);
`log` records the transfers as the device saw them.
"""
from . import host, sim

EV_ADDR, EV_WRITE, EV_READ, EV_STOP, EV_NACK = 1, 2, 3, 4, 5


class Device:
    def __init__(self, addr, mem=None, fill=0xFF, on_read=None):
        self.addr = addr
        self.mem = bytearray([fill]) * 256
        for k, v in (mem or {}).items():
            self.mem[k & 0xFF] = v
        self.on_read = on_read
        self.ptr = 0
        self.first = False      # the next written byte is the register pointer
        self.log = []           # ("W", [bytes]) / ("R", [bytes]) per transfer

    def setup_writes(self, core, prog, sda, scl, base=None, **kw):
        """Register writes: load the program for `core` and set R3 = address.
        The caller sets OD_MASK, PIN_OUT (released) and pin ownership."""
        w = host.load_program(core, prog, base, out_pins=(sda, 1), in_pins=sda, clkdiv=1 << 8, **kw)
        return w + [(sim.core_reg(core, sim.C_R0 + 3), self.addr)]

    def handle(self, word):
        """One RX word from the core -> list of TX words to push."""
        kind, b = (word >> 8) & 0xF, word & 0xFF
        if word == 0:
            return []
        if kind == EV_ADDR:
            self.first = not (b & 1)
            self.log.append(("R" if b & 1 else "W", []))
        elif kind == EV_WRITE:
            if self.first:
                self.ptr, self.first = b, False
            else:
                self.mem[self.ptr] = b
                self.ptr = (self.ptr + 1) & 0xFF
            self.log[-1][1].append(b)
        elif kind == EV_READ:
            v = self.mem[self.ptr]
            if self.on_read:
                v = self.on_read(self.ptr, v) & 0xFF
            self.ptr = (self.ptr + 1) & 0xFF
            self.log[-1][1].append(v)
            return [v]
        elif kind in (EV_STOP, EV_NACK):
            pass
        else:
            raise ValueError(f"not an i2c_device event: {word:#x}")
        return []
