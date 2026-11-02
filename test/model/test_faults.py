"""Seeded wire faults against existing firmware (docs/fault-testing.md).

Every case is: a clean frame (the control), a frame under one fault from
pe.faults.plan, then a clean frame again. The firmware is never changed. A case
ends in one verdict:

  ok           nothing corrupted was reported as good, the next clean frame works
  false-good   a frame the protocol can show is bad was reported as good
  needs-reset  the next clean frame failed; a host reset of the core cleared it
  hang         not even that cleared it

The sweep is fixed (SEEDS, deterministic on any machine), and the set of cases
that are not `ok` must equal KNOWN exactly: a new failure fails CI and so does a
known one that goes away."""
from dataclasses import dataclass
import bisect
import functools
import pathlib
import random
import struct
import zlib

import pytest

from pe import asm, faults, host, i2cbus, sim, usb, usbdev, usbenum
from pe.faults import Span, Wire
from pe.modelhost import ModelHost
from pe.usbfs import FsControlHost
from pe.usbhost import Stall

FW = pathlib.Path(__file__).resolve().parents[2] / "firmware"
SEEDS = range(16)


@functools.lru_cache(maxsize=None)
def assemble(text, defines=()):
    return asm.assemble(text, dict(defines))


def rx0(mh):
    return mh.pop_all(0)


class Harness:
    name = ""
    effect = None         # did the fault change what the frame's receiver reported?

    def bus_clear(self):
        """The protocol's own way out, if it has one. True if there was one."""
        return False

    def after_fault(self):
        """What the protocol's host does once the wire is back."""


# ---------------------------------------------------------------- uart_rx

def strict_8n1(levels, bit):
    """What the protocol can tell from a line: a receiver that checks the
    start bit at mid-bit and the stop bit. [("false start", i)] or
    [(byte, stop_ok, i)]; levels are per clock."""
    out, i, n = [], 0, len(levels)
    while i < n:
        if levels[i]:
            i += 1
            continue
        mid = i + bit // 2
        if mid + 9 * bit >= n:
            break
        if levels[mid]:
            out.append(("false start", i))
            i = mid
            continue
        byte = sum(levels[mid + (k + 1) * bit] << k for k in range(8))
        stop = levels[mid + 9 * bit]
        out.append((byte, bool(stop), i))
        i = mid + 9 * bit
        while i < n and not levels[i]:
            i += 1
    return out


class Uart(Harness):
    name = "uart_rx"
    RX, DIV, BIT = 16, 4, 32            # 8 ticks per bit, CLKDIV 4

    def __init__(self, text=None):
        self.prog = assemble(text or (FW / "uart_rx.pasm").read_text(), (("RX", self.RX),))
        self.mh = ModelHost()
        self.mh.apply(host.load_program(0, self.prog, in_pins=self.RX, clkdiv=self.DIV << 8))
        self.mh.ui = 1
        self.mh.start(1)
        self.wire = Wire(self.mh)
        self.mh.step(100)
        self.rng = random.Random(1)

    def send(self, data):
        for b in data:
            for lv in [0] + [(b >> i) & 1 for i in range(8)] + [1]:
                self.mh.ui = lv
                self.mh.step(self.BIT)

    def good(self):
        b = self.rng.getrandbits(8)
        self.mh.ui = 1
        self.mh.step(20 * self.BIT)
        self.mh.write(sim.A_FCLR, 1)
        rx0(self.mh)
        self.send([b])
        self.mh.step(3 * self.BIT)
        return rx0(self.mh) == [b] and not self.mh.chip.flags & 1

    def faulted(self, kind, seed):
        data = [self.rng.getrandbits(8) for _ in range(2)]
        self.mh.step(self.BIT)
        span = Span(self.mh.chip.cycle, self.BIT, 10 * len(data) + 2, {self.RX: 1})
        self.wire.trace = []
        self.wire.add(*faults.plan(kind, seed, span))
        self.send(data)
        self.mh.ui = 1
        self.mh.step(14 * self.BIT)
        self.wire.clear()
        levels = [t[2][0] & 1 for t in self.wire.trace]
        self.wire.trace = None
        got = rx0(self.mh)
        self.effect = got != data or bool(self.mh.chip.flags & 1)
        if got == data or self.mh.chip.flags & 1:
            return None
        for r in strict_8n1(levels, self.BIT):
            if r[0] == "false start" or not r[1]:
                return f"sent {data} got {got}, no error flag; on the wire: {r}"
        return None

    def reset(self):
        self.mh.write(sim.A_CTRL, 1 << 16)
        self.mh.ui = 1
        self.mh.step(4)
        self.mh.start(1)


# ---------------------------------------------------------------- i2c_master

class I2c(Harness):
    name = "i2c_master"
    SDA, SCL, DIV, TMO = 0, 1, 4, 2000
    START, STOP, READ_NACK = 2, 3, 1 | 4
    BIT = 8 * DIV

    def __init__(self, text=None):
        mh = self.mh = ModelHost()
        prog = assemble(text or (FW / "i2c_master.pasm").read_text(), (("SDA", self.SDA), ("SCL", self.SCL)))
        mh.apply(host.pin_owner_writes({self.SDA: 0, self.SCL: 0}))
        mh.write(sim.A_POUT, 0b11)
        mh.write(sim.A_OD, 0b11)
        mh.pull_up = 0b11
        mh.apply(host.load_program(0, prog, out_pins=(self.SDA, 1), in_pins=self.SDA, clkdiv=self.DIV << 8))
        mh.start(1)
        self.wire = Wire(mh)
        self.ee = i2cbus.Eeprom(self.SDA, self.SCL, 0x50, bytes(256))
        self.rng = random.Random(2)

    @staticmethod
    def wr(b):
        return b << 24

    def run(self, cmds, extra=200):
        """Push the commands, clock the bus. Returns (replies, finished): the
        core back at `pull block` with everything consumed, within the bound
        the firmware documents: one TMO per command at most."""
        mh, core = self.mh, self.mh.chip.cores[0]
        budget = (len(cmds) + 1) * (self.TMO + 80) * self.DIV + extra
        pending, replies = list(cmds), []
        done = False
        for _ in range(budget):
            if pending and len(core.txf) < 4:
                mh.push(0, pending.pop(0))
            b = self.wire.bus()
            self.ee.tick(b)
            mh.ext_oe, mh.uio = self.ee.low, 0
            mh.step()
            if core.rxf:
                replies.append(core.rxf.pop(0))
            done = not pending and not core.txf and core.stalled and core.pc == 0 and not core.rxf
            if done:
                break
        for _ in range(40):
            self.ee.tick(self.wire.bus())
            mh.ext_oe, mh.uio = self.ee.low, 0
            mh.step()
        self.done = done
        return replies, done

    def write_cmds(self, reg, val):
        return [self.START, self.wr(0xA0), self.wr(reg), self.wr(val), self.STOP]

    def good(self):
        reg, val = self.rng.randrange(256), self.rng.randrange(1, 256)
        self.mh.write(sim.A_FCLR, 2)
        r1, d1 = self.run(self.write_cmds(reg, val))
        r2, d2 = self.run([self.START, self.wr(0xA0), self.wr(reg), self.START, self.wr(0xA1),
                           self.READ_NACK, self.STOP])
        return (d1 and d2 and r1 == [0, 0, 0] and r2 == [0, 0, 0, val] and self.ee.mem[reg] == val
                and not self.mh.chip.flags & 2)

    def faulted(self, kind, seed):
        reg, val = self.rng.randrange(256), self.rng.randrange(1, 256)
        self.ee.mem[reg] = val ^ 0x55
        before = bytes(self.ee.mem)
        pin = (self.SDA, self.SCL)[seed & 1]
        reading = seed & 2
        span = Span(self.mh.chip.cycle + 8, self.BIT, 40 if reading else 29, {pin: 1})
        fs = faults.plan(kind, seed, span)
        if kind == "stretch":
            rng = random.Random(f"i2c:{seed}")
            extra = rng.randrange(1, 2 * self.TMO) * self.DIV
            fs = [faults.Hold(self.SCL, rng.randrange(1, 28), extra)]
        self.wire.add(*fs)
        cmds = ([self.START, self.wr(0xA0), self.wr(reg), self.START, self.wr(0xA1), self.READ_NACK, self.STOP]
                if reading else self.write_cmds(reg, val))
        r, done = self.run(cmds)
        # the fault lasts at most the span: the wire is clean once it is over
        while self.mh.chip.cycle < span.end + self.BIT:
            self.mh.ext_oe, self.mh.uio = self.ee.low, 0
            self.ee.tick(self.wire.bus())
            self.mh.step()
        self.wire.clear()
        if not done:
            return f"HANG: commands not consumed within the bound, replies {r}"
        timed_out = self.mh.chip.flags & 2
        after = bytes(self.ee.mem)
        want = bytearray(before)
        if reading:
            self.effect = bool(timed_out) or r != [0, 0, 0, val ^ 0x55]
            return None                 # a master can't check what it reads: no CRC, no read-back
        want[reg] = val
        acked = r == [0, 0, 0]
        self.effect = bool(timed_out) or not acked or after != bytes(want)
        if acked and not timed_out and after != bytes(want):
            diff = [(i, a) for i, (a, b) in enumerate(zip(after, want)) if a != b]
            return f"reg {reg:#x} = {val:#x}: every byte ACKed, memory differs at {diff[:3]}"
        return None

    def bus_clear(self):
        """What a host can do from the commands: 9 clocks with SDA released
        and a STOP (the I2C specification's bus clear)."""
        self.mh.write(sim.A_FCLR, 2)
        self.run([self.READ_NACK, self.STOP])
        self.mh.write(sim.A_FCLR, 2)
        return True

    def reset(self):
        self.mh.write(sim.A_CTRL, 1 << 16)
        self.mh.step(4)
        self.mh.start(1)
        self.bus_clear()
        rx0(self.mh)


# ---------------------------------------------------------------- can_rx

def crc15(bits):
    crc = 0
    for b in bits:
        nxt = b ^ ((crc >> 14) & 1)
        crc = (crc << 1) & 0x7FFF
        if nxt:
            crc ^= 0x4599
    return crc


def can_bits(ident, data):
    b = [0] + [(ident >> (10 - i)) & 1 for i in range(11)] + [0, 0, 0]
    b += [(len(data) >> (3 - i)) & 1 for i in range(4)]
    for byte in data:
        b += [(byte >> (7 - i)) & 1 for i in range(8)]
    c = crc15(b)
    return b + [(c >> (14 - i)) & 1 for i in range(15)]


def can_stuff(bits):
    out, run, last = [], 0, None
    for x in bits:
        out.append(x)
        run = run + 1 if x == last else 1
        last = x
        if run == 5:
            out.append(1 - x)
            last, run = 1 - x, 1
    return out


def can_records(words):
    out, start = [], 0
    for i, w in enumerate(words):
        if w >> 31:
            out.append((w & 0xFFFF, words[start:i]))
            start = i + 1
    return out


def can_unpack(words, nbits):
    bits = []
    full, rem = divmod(nbits, 24)
    if len(words) != full + (1 if rem else 0):
        return None
    for w in words[:full]:
        bits += [(w >> (23 - i)) & 1 for i in range(24)]
    if rem:
        bits += [(words[full] >> (rem - 1 - i)) & 1 for i in range(rem)]
    return bits


class Can(Harness):
    name = "can_rx"
    RX, TX, TPB = 16, 8, 48             # CLKDIV 1: a tick is a clock

    def __init__(self, text=None):
        mh = self.mh = ModelHost()
        prog = assemble(text or (FW / "can_rx.pasm").read_text(), (("RX", self.RX), ("TX", self.TX)))
        mh.apply(host.load_program(0, prog, in_pins=self.RX, clkdiv=1 << 8))
        mh.write(sim.core_reg(0, sim.C_CRCCTRL), 0x4599 | (14 << 16) | (1 << 20))
        mh.write(sim.A_POUT, 1 << self.TX)
        mh.ui = 1
        mh.start(1)
        self.wire = Wire(mh)
        mh.step(2000)
        self.rng = random.Random(3)
        self.rx = []

    def frame(self):
        n = self.rng.randrange(0, 2)
        return self.rng.randrange(0x800), bytes(self.rng.getrandbits(8) for _ in range(n))

    def tail(self):
        return [1, 1, 1] + [1] * 7 + [1] * 3

    def send(self, wire):
        mh = self.mh
        for b in wire:
            for _ in range(self.TPB):
                mh.ui = b & (mh.chip.pin_out >> self.TX) & 1
                mh.step()
            self.rx += rx0(mh)

    def idle(self, bits):
        self.send([1] * bits)

    def good(self):
        self.idle(14)
        self.rx = []
        bits = can_bits(*self.frame())
        self.send(can_stuff(bits) + self.tail())
        self.idle(4)
        recs = can_records(self.rx)
        return len(recs) == 1 and recs[0][0] == 1 and can_unpack(recs[0][1], len(bits)) == bits

    def faulted(self, kind, seed):
        self.rx = []
        bits = can_bits(*self.frame())
        wire = can_stuff(bits) + self.tail()
        span = Span(self.mh.chip.cycle, self.TPB, len(wire) + 4, {self.RX: 1})     # and the idle bus before it
        self.wire.add(*faults.plan(kind, seed, span))
        self.idle(4)
        self.send(wire)
        self.idle(4)
        self.wire.clear()
        recs = can_records(self.rx)
        self.effect = not (len(recs) == 1 and recs[0][0] == 1 and can_unpack(recs[0][1], len(bits)) == bits)
        for st, w in recs:
            if st == 1 and can_unpack(w, len(bits)) != bits:
                return f"sent {bits} reported good: {[hex(x) for x in w]}"
        return None

    def reset(self):
        self.mh.write(sim.A_CTRL, 1 << 16)
        self.mh.write(sim.A_POUT, 1 << self.TX)
        self.mh.ui = 1
        self.mh.step(4)
        self.mh.start(1)
        self.rx = []


# ---------------------------------------------------------------- usb_enum

@functools.lru_cache(maxsize=None)
def usb_prog(text):
    return usbenum.assemble("full", text)


class Usb(Harness):
    """usb_enum at full speed (4 clocks per bit). The faults are on the chip's
    input pads; the host model hears the device's drive as it is (the device
    to host direction has its own injected faults in test_usb_autonomous)."""
    name = "usb_enum"
    ADDR = 5
    BIT = 4
    CLEAN_LOG = 5                       # SETUP, 3 IN, OUT

    def __init__(self, text=None):
        self.text = text
        prog = usb_prog(text)
        mh = self.mh = ModelHost()
        mh.apply(usbenum.load_writes(prog, "full"))
        mh.pull_up = 0b10
        mh.step(100)
        self.wire = Wire(mh)
        self.h = FsControlHost(mh, ipd_bits=2.0, ack_bits=2.0)
        self.enumerate(self.ADDR)

    def enumerate(self, addr):
        self.h.control_write(0, bytes([0x00, 5, addr, 0, 0, 0, 0, 0]))
        self.addr = addr

    def good(self):
        try:
            return self.h.get_descriptor(self.addr, 1, 18) == usbdev.DEVICE_DESC
        except (TimeoutError, Stall):
            return False

    def faulted(self, kind, seed):
        h, mh = self.h, self.mh
        h.gap()
        # even seeds: SETUP + DATA0, about 140 bit times at the shortest gaps; odd
        # seeds: the whole 18-byte control read, about 800
        span = Span(mh.chip.cycle + self.BIT * 4, self.BIT, 800 if seed & 1 else 140, {0: 0, 1: 1})
        self.wire.add(*faults.plan(kind, seed, span))
        result = None
        n0 = len(h.log)
        self.effect = False
        try:
            h.retries = 1
            got = h.get_descriptor(self.addr, 1, 18)
            if got != usbdev.DEVICE_DESC:
                result = f"descriptor read returned {got.hex()}"
            self.effect = len(h.log) - n0 != self.CLEAN_LOG
        except Stall:
            result = "a good request was STALLed: the device acted on a damaged SETUP"
        except TimeoutError:
            self.effect = True
        finally:
            h.retries = 3
        while mh.chip.cycle < span.end + 4 * self.BIT:
            h.fs._edge()
        self.wire.clear()
        mh.step(120)
        if mh.chip.flags >> usbenum.FLAGS["F_CFG"] & 1:
            result = "configured itself"
        self.after_fault()
        return result

    def after_fault(self):
        if usbenum.state(self.mh.read)["address"] == 0:     # 2.5 us of SE0 is a bus reset, by the book
            self.effect = True
            self.enumerate(self.ADDR)

    def reset(self):
        self.mh.write(sim.A_CTRL, (1 << 16))
        self.mh.step(4)
        self.mh.start(1)
        self.mh.step(100)
        self.enumerate(self.ADDR)


# ---------------------------------------------------------------- 10BASE-T

def eth_frame(seed, field=None, ethertype=0x88B5):
    body = bytes(6 * [0xFF]) + bytes.fromhex("020000c0ffee") + struct.pack(">H", ethertype)
    rng = random.Random(seed)
    pay = bytes(rng.getrandbits(8) for _ in range(10))
    if field is not None:
        pay = struct.pack("<H", field) + pay[2:]
    body += pay
    return body + struct.pack("<I", zlib.crc32(body))


def fcs_ok(fr):
    return zlib.crc32(fr) == 0x2144DF1C          # the CRC-32 residue over a frame and its FCS


def eth_bits(fr):
    return [1, 0] * 31 + [1, 1] + [(b >> i) & 1 for b in fr for i in range(8)]


def eth_levels(bits, t):
    """Edges in clocks of a Manchester frame (802.3 polarity, 5 clocks per bit),
    TP_IDL high then low. Returns (edge times, levels, end)."""
    times, levels, prev = [], [], 0
    half = 2.5
    for x in bits:
        for lv in (1 - x, x):
            if lv != prev:
                times.append(t)
                levels.append(lv)
            prev = lv
            t += half
    if prev:
        times.append(t + 2 * 5.0)
        levels.append(0)
    return times, levels, t + 4 * half


class Eth(Harness):
    """The sender in continuous time, whole half-clocks here: 5 clocks per bit
    at 50 MHz. The pads are also sampled at the falling edge, as on the chip."""
    RXP, BIT = 16, 5

    def setup(self, prog):
        mh = self.mh = ModelHost()
        mh.apply(host.load_program(0, prog, 0, clkdiv=1 << 8))
        mh.write(sim.A_LINEDIV, 5 << 16)
        mh.write(sim.A_LINEPINS, self.RXP << 8)
        mh.write(sim.A_LINECFG, 1 | (2 << 1) | (1 << 14))
        self.wire = Wire(mh)
        self.times, self.levels = [], []
        wire_step = mh.chip.step

        def step(ui_in=0, *a, **kw):
            c = mh.chip.cycle
            wire_step(self.level(c), *a, ui_mid=self.level(c - 0.5), **kw)
        mh.chip.step = step
        self.rng = random.Random(4)

    def level(self, t):
        i = bisect.bisect_right(self.times, t) - 1
        return self.levels[i] if i >= 0 else 0

    def play(self, bits):
        mh = self.mh
        t0 = mh.chip.cycle + 10 + self.rng.random()
        times, levels, end = eth_levels(bits, t0)
        self.times += times
        self.levels += levels
        return t0, end

    def run_to(self, end):
        while self.mh.chip.cycle < end + 100:
            self.mh.step()
            self.words += rx0(self.mh)


class EthRx(Eth):
    name = "eth10_rx"

    def __init__(self, text=None):
        self.setup(assemble(text or (FW / "eth10_rx.pasm").read_text()))
        self.mh.start(1)
        self.mh.step(10)
        self.words = []

    def frames(self):
        out, data = [], []
        for w in self.words:
            if w & 0xFF:
                n = w >> 16
                bits = []
                for d in data:
                    bits += [(d >> (8 + i)) & 1 for i in range(24)]
                full, rem = divmod(n, 24)
                bits = bits[:24 * full]
                if rem and full < len(data):
                    bits += [(data[full] >> (32 - rem + i)) & 1 for i in range(rem)]
                out.append(bits)
                data = []
            else:
                data.append(w)
        return out

    @staticmethod
    def decode(bits):
        """The frame after the SFD, or None."""
        sfd = [1, 0, 1, 0, 1, 0, 1, 1]
        for i in range(len(bits) - 8):
            if bits[i:i + 8] == sfd:
                body = bits[i + 8:]
                return bytes(sum(b << j for j, b in enumerate(body[8 * k:8 * k + 8])) for k in range(len(body) // 8))
        return None

    def good(self):
        fr = eth_frame(self.rng.getrandbits(32))
        self.words = []
        _, end = self.play(eth_bits(fr))
        self.run_to(end + 100)
        got = [self.decode(b) for b in self.frames() if len(b) > 64]
        return got == [fr] and fcs_ok(got[0])

    def faulted(self, kind, seed):
        fr = eth_frame(self.rng.getrandbits(32))
        self.words = []
        bits = eth_bits(fr)
        t0, end = self.play(bits)
        self.wire.add(*faults.plan(kind, seed, Span(t0, self.BIT, len(bits), {self.RXP: 0})))
        self.run_to(end + 100)
        self.wire.clear()
        self.mh.step(100)
        self.words += rx0(self.mh)
        got = [self.decode(b) for b in self.frames() if len(b) > 64]
        self.effect = got != [fr]
        for d in got:
            if d and d != fr and fcs_ok(d):
                return f"a damaged frame passes the FCS: {d.hex()}"
        return None

    def reset(self):
        self.mh.write(sim.A_LINECFG, 0)
        self.mh.write(sim.A_CTRL, 1 << 16)
        self.mh.step(2)
        self.mh.write(sim.A_LINECFG, 1 | (2 << 1) | (1 << 14))
        self.mh.start(1)
        self.mh.step(10)
        self.words = []


class EthMatch(Eth):
    name = "eth10_match"
    HIT = 8
    THRESHOLD = 1000

    def __init__(self, text=None):
        self.setup(assemble(text or (FW / "eth10_match.pasm").read_text()))
        self.mh.push(0, self.THRESHOLD)
        self.mh.start(1)
        self.mh.step(10)
        self.words = []

    def good(self):
        field = self.rng.randrange(self.THRESHOLD, 60000)
        self.words = []
        _, end = self.play(eth_bits(eth_frame(1, field)))
        hit = False
        mh = self.mh
        while mh.chip.cycle < end + 100:
            mh.step()
            hit = hit or bool((mh.chip.pin_out >> self.HIT) & 1)
            self.words += rx0(mh)
        return hit and self.words == [field]

    def faulted(self, kind, seed):
        # HIT is decided before the FCS: the header says a damaged frame can raise
        # it, so the property here is only that the core comes back.
        self.words = []
        field = self.rng.randrange(self.THRESHOLD, 60000)
        bits = eth_bits(eth_frame(1, field))
        t0, end = self.play(bits)
        self.wire.add(*faults.plan(kind, seed, Span(t0, self.BIT, len(bits), {self.RXP: 0})))
        self.run_to(end + 100)
        self.wire.clear()
        self.mh.step(100)
        self.effect = self.words + rx0(self.mh) != [field]
        return None

    def reset(self):
        self.mh.write(sim.A_LINECFG, 0)
        self.mh.write(sim.A_CTRL, 1 << 16)
        self.mh.step(2)
        self.mh.write(sim.A_LINECFG, 1 | (2 << 1) | (1 << 14))
        self.mh.push(0, self.THRESHOLD)
        self.mh.start(1)
        self.mh.step(10)
        self.words = []


HARNESS = {h.name: h for h in (Uart, I2c, Can, Usb, EthRx, EthMatch)}


@dataclass
class Result:
    firmware: str
    kind: str
    seed: int
    verdict: str
    effect: bool = False
    detail: str = ""


def case(cls, kind, seed, text=None):
    h = cls(text)
    assert h.good(), f"{cls.name}: the clean frame failed before any fault"
    bad = h.faulted(kind, seed)
    res = lambda verdict, detail="": Result(cls.name, kind, seed, verdict, h.effect, detail)
    if bad and bad.startswith("HANG"):
        return res("hang", bad)
    if bad:
        return res("false-good", bad)
    if h.good():
        return res("ok")
    if h.good():
        return res("second-frame")
    if h.bus_clear() and h.good():
        return res("bus-clear")
    h.reset()
    if h.good():
        return res("needs-reset")
    return res("hang", "still failing after a host reset")


# (firmware, kind): (cases where the fault changed what was reported, {seed: verdict} of the cases not ok)
EXPECT = {
    ("uart_rx", "glitch"): (10, {3: "false-good"}),
    ("uart_rx", "stuck"): (15, {}),
    ("uart_rx", "truncate"): (10, {}),
    ("uart_rx", "flip"): (16, {}),
    ("uart_rx", "stretch"): (6, {}),
    ("uart_rx", "jitter"): (0, {}),
    ("i2c_master", "glitch"): (14, {}),
    ("i2c_master", "stuck"): (13, {0: "false-good", 4: "false-good"}),
    ("i2c_master", "truncate"): (16, {3: "second-frame", 15: "second-frame"}),
    ("i2c_master", "flip"): (16, {8: "false-good", 12: "false-good"}),
    ("i2c_master", "stretch"): (8, {}),
    ("i2c_master", "jitter"): (6, {}),
    ("can_rx", "glitch"): (6, {}),
    ("can_rx", "stuck"): (12, {}),
    ("can_rx", "truncate"): (14, {}),
    ("can_rx", "flip"): (11, {}),
    ("can_rx", "stretch"): (3, {}),
    ("can_rx", "jitter"): (0, {}),
    ("usb_enum", "glitch"): (10, {}),
    ("usb_enum", "stuck"): (14, {}),
    ("usb_enum", "truncate"): (13, {}),
    ("usb_enum", "flip"): (10, {}),
    ("usb_enum", "stretch"): (6, {}),
    ("usb_enum", "jitter"): (7, {}),
    ("eth10_rx", "glitch"): (4, {}),
    ("eth10_rx", "stuck"): (16, {}),
    ("eth10_rx", "truncate"): (16, {}),
    ("eth10_rx", "flip"): (13, {}),
    ("eth10_rx", "stretch"): (9, {}),
    ("eth10_rx", "jitter"): (3, {}),
    ("eth10_match", "glitch"): (0, {}),
    ("eth10_match", "stuck"): (13, {}),
    ("eth10_match", "truncate"): (10, {}),
    ("eth10_match", "flip"): (1, {}),
    ("eth10_match", "stretch"): (10, {}),
    ("eth10_match", "jitter"): (2, {}),
}


@pytest.mark.parametrize("firmware", list(HARNESS))
@pytest.mark.parametrize("kind", faults.KINDS)
def test_sweep(firmware, kind):
    res = [case(HARNESS[firmware], kind, s) for s in SEEDS]
    bad = {r.seed: r.verdict for r in res if r.verdict != "ok"}
    changed = sum(bool(r.effect) for r in res)
    assert (changed, bad) == EXPECT[firmware, kind], [(r.seed, r.verdict, r.detail) for r in res if r.verdict != "ok"]


def test_doc_table_is_the_sweep():
    """docs/fault-testing.md's table row for row: the numbers there are these."""
    text = (FW.parent / "docs" / "fault-testing.md").read_text()
    rows = {}
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip("|\n ").split("|")]
        if len(cells) == 5 and cells[0] in HARNESS and cells[1] in faults.KINDS:
            not_ok = {}
            if cells[4] != "-":
                for item in cells[4].split(", "):
                    seed, verdict = item.split(": ")
                    not_ok[int(seed)] = verdict
            rows[cells[0], cells[1]] = (int(cells[3]), not_ok)
            assert int(cells[2]) == len(SEEDS)
    assert rows == EXPECT
    not_ok = [v for _, bad in EXPECT.values() for v in bad.values()]
    assert (len(SEEDS) * len(EXPECT), len(not_ok)) == (576, 7)
    assert not_ok.count("false-good") == 5 and not_ok.count("second-frame") == 2
    assert f"{len(SEEDS)} seeds per firmware and fault kind: 576 cases, 7 not `ok`" in text


@pytest.mark.parametrize("firmware,pin,level", [
    ("uart_rx", 16, 0), ("uart_rx", 16, 1),
    ("i2c_master", 0, 0), ("i2c_master", 1, 0), ("i2c_master", 1, 1),
    ("can_rx", 16, 0), ("can_rx", 16, 1),
    ("eth10_rx", 16, 0), ("eth10_rx", 16, 1),
    ("eth10_match", 16, 0), ("eth10_match", 16, 1)])
def test_a_line_stuck_for_a_whole_frame_and_more(firmware, pin, level):
    """The line stuck through a whole clean frame, the sweep's `stuck` being
    bounded by a frame: the firmware must come back by itself when the wire
    does, and i2c_master must finish every command within its timeout bound
    while the wire is still stuck."""
    h = HARNESS[firmware]()
    assert h.good()
    h.wire.add(faults.Stuck(pin, level))
    was_good = h.good()
    if firmware == "i2c_master":
        assert h.done, "commands not finished within one TMO each"
    else:
        assert not was_good
    h.wire.clear()
    h.after_fault()
    assert h.good()
    assert h.good()


def test_usb_stuck_j_k_and_se0_for_a_whole_transfer():
    for dm, dp in ((1, 0), (0, 1), (0, 0), (1, 1)):
        h = Usb()
        assert h.good()
        h.wire.add(faults.Stuck(0, dm), faults.Stuck(1, dp))
        assert not h.good()
        h.wire.clear()
        h.mh.step(120)
        h.after_fault()
        assert h.good()


# Findings: real, minimal, pinned. Each test states the behaviour as it is today. --------

def test_finding_uart_rx_takes_a_glitch_for_a_start_bit():
    """FINDING 1 (sweep: uart_rx glitch seed 3). A low pulse on the idle line,
    from 1 tick up to 12 of a bit's 8 ticks: the firmware never looks at the
    start bit again, so it delivers 0xFF with no error flag. A 13-tick pulse
    reaches the first data sample: 0xFE. A receiver that checks the start bit
    at mid-bit (4 ticks) would call the pulses narrower than that no frame."""
    for ticks in range(1, 16):
        u = Uart()
        assert u.good()
        u.mh.step(40)
        u.wire.trace = []
        u.wire.add(faults.Glitch(u.RX, u.mh.chip.cycle + 5, ticks * u.DIV, level=0, bit=u.BIT * 2))
        u.mh.step(14 * u.BIT)
        u.wire.clear()
        levels = [t[2][0] & 1 for t in u.wire.trace]
        u.wire.trace = None
        assert rx0(u.mh) == [0xFF if ticks <= 12 else 0xFE], ticks
        assert not u.mh.chip.flags & 1
        strict = strict_8n1(levels, u.BIT)
        assert (bool(strict) and strict[0][0] == "false start") == (ticks <= 4), (ticks, strict)
        assert u.good()                              # the next frame is fine, if the line is idle first


def test_finding_uart_rx_a_frame_inside_the_phantom_byte_is_misframed():
    """The phantom byte lasts 9.5 bits. A real frame starting 3 bits after the
    glitch is read from its middle: the byte sent is not delivered. The
    phantom's stop sample falls on data bit 5 of the real frame, so whether F0
    says anything depends on that bit: 0 flags it, 1 passes silently."""
    for byte, flagged in ((0x5A, True), (0x7A, False)):
        u = Uart()
        assert u.good()
        u.mh.step(40)
        u.wire.add(faults.Glitch(u.RX, u.mh.chip.cycle + 5, u.DIV, level=0, bit=u.BIT))
        u.mh.step(3 * u.BIT)
        u.send([byte])
        u.mh.ui = 1
        u.mh.step(14 * u.BIT)
        u.wire.clear()
        got = rx0(u.mh)
        assert got and byte not in got, got
        assert bool(u.mh.chip.flags & 1) == flagged, (hex(byte), got)


def test_finding_uart_rx_glitch_shorter_than_a_tick_needs_luck():
    """Below one tick the level WAIT sees the pulse only if a tick falls in it:
    1 clock at CLKDIV 4 is caught at 1 phase in 4."""
    caught = 0
    for phase in range(4):
        u = Uart()
        assert u.good()
        u.mh.step(40 + phase)
        u.wire.add(faults.Glitch(u.RX, u.mh.chip.cycle + 5, 1, level=0, bit=u.BIT))
        u.mh.step(14 * u.BIT)
        u.wire.clear()
        caught += rx0(u.mh) == [0xFF]
    assert caught == 1


def test_finding_i2c_master_reports_ack_from_a_stuck_sda():
    """FINDING 2 (sweep: i2c_master stuck seeds 0, 4, flip seeds 8, 12). The
    firmware never reads back what it drives, so SDA held low looks like an
    ACK for every byte: with no device at the address, every WRITE is
    "acknowledged", a READ returns 0x00, F1 stays clear. On a clean bus the
    same transfer is NACKed."""
    clean = I2c()
    clean.ee.addr = 0x51
    assert clean.run(clean.write_cmds(1, 2)) == ([1, 1, 1], True)
    h = I2c()
    h.ee.addr = 0x51
    h.wire.add(faults.Stuck(h.SDA, 0))
    assert h.run(h.write_cmds(1, 2)) == ([0, 0, 0], True)
    assert h.run([h.START, h.wr(0xA1), h.READ_NACK, h.STOP]) == ([0, 0], True)
    assert not h.mh.chip.flags & 2
    assert h.ee.log == []


def test_finding_i2c_next_transfer_is_lost_after_a_truncated_read():
    """FINDING 3 (sweep: i2c_master truncate seeds 3, 15). SCL held high for
    the last clocks of a READ: the EEPROM is left driving a 0 on SDA in the
    middle of its byte. i2c_master has no bus clear, so its STOP can't raise
    SDA and the next START doesn't happen: the next transfer is NACKed (the
    host is told), it clocks the EEPROM out, and the one after works. The
    command set can do the bus clear itself (READ with NACK, STOP)."""
    for clear in (False, True):
        h = I2c()
        assert h.good()
        h.ee.mem[0x20] = 0x00
        h.run([h.START, h.wr(0xA0), h.wr(0x20), h.STOP])
        t = h.mh.chip.cycle + 16
        h.wire.add(faults.Truncate(h.SCL, 1, t + 12 * h.BIT, t + 16 * h.BIT))
        replies, done = h.run([h.START, h.wr(0xA1), h.READ_NACK, h.STOP])
        h.wire.clear()
        assert done and h.ee.state == "rdata" and h.ee.low
        if clear:
            assert h.bus_clear()
            assert h.good()
        else:
            assert h.run(h.write_cmds(0x21, 0x77)) == ([1, 1, 1], True)     # NACKed, F1 clear
            assert h.ee.mem[0x21] == 0
            assert h.good()


@pytest.mark.parametrize("name,index", [("CRC delimiter", 0), ("ACK delimiter", 2), ("EOF first", 3), ("EOF last", 9)])
def test_finding_can_rx_has_no_form_check(name, index):
    """FINDING 4. A dominant bit where CAN requires recessive is a form error
    a controller reports. can_rx stops looking after the last CRC bit: status
    1, and it ACKs. The payload is intact; the sweep's oracle (bits against
    the sent bits) can't see this, so it is pinned here."""
    c = Can()
    assert c.good()
    c.idle(14)
    c.rx = []
    bits = can_bits(0x123, b"\x55")
    tail = c.tail()
    tail[index] = 0
    c.send(can_stuff(bits) + tail)
    c.idle(14)
    [(status, words)] = can_records(c.rx)
    assert status == 1 and can_unpack(words, len(bits)) == bits
    assert c.good()


@pytest.mark.parametrize("clocks", [1, 4, 23, 47])
def test_finding_can_rx_answers_a_glitch_on_the_idle_bus_with_an_error_flag(clocks):
    """FINDING 5. The SOF is not validated at its sample point: one clock low
    on the idle bus starts a frame, the recessive bits after it are a stuff
    error, and can_rx drives a 6-bit error flag onto the bus (288 ticks of
    dominant) and reports status 2. It recovers, but a frame that starts
    during the flag is destroyed, and a transmitter that doesn't retry (the
    model here) loses it."""
    c = Can()
    assert c.good()
    c.idle(14)
    c.rx = []
    c.wire.add(faults.Glitch(c.RX, c.mh.chip.cycle + 10, clocks, level=0, bit=c.TPB))
    dominant = 0
    for _ in range(14 * c.TPB):
        tx = (c.mh.chip.pin_out >> c.TX) & 1
        dominant += 1 - tx
        c.mh.ui = tx
        c.mh.step()
    c.wire.clear()
    assert dominant == 288
    assert [st for st, _ in can_records(c.rx + rx0(c.mh))] == [2]
    assert c.good()


USB_ENUM_WORDS = 246


def test_finding_usb_enum_misses_a_reset_that_starts_in_an_eop():
    """FINDING 6. 4.2 us of SE0 (a bus reset is 2.5 us) starting anywhere in
    an IN token addressed to the device. From the end of the token, the
    firmware is in `got_in: wait 1 pin DM`, which has no timeout and no SE0
    check, so an SE0 that runs on from the token's EOP is not seen: the device
    keeps its address and answers when the line returns. 6 of the 22 start
    bits tried."""
    assert len(usbenum.assemble("full").words) == USB_ENUM_WORDS
    missed = []
    tried = [0, 12] + list(range(24, 44))
    assert len(tried) == 22
    for bit in tried:
        h = Usb()
        assert h.good()
        h.h.gap()
        start = h.mh.chip.cycle + 8 + bit * h.BIT
        h.wire.add(faults.Stuck(0, 0, start, start + 200), faults.Stuck(1, 0, start, start + 200))
        h.h.fs.ipd = 0
        h.h._send(usb.token(usb.PID_IN, h.ADDR, 0))
        answered = False
        while h.mh.chip.cycle < start + 320:
            answered = answered or bool(h.mh.chip.outputs()[2] & 3)
            h.h.fs._edge()
        h.wire.clear()
        h.mh.step(120)
        if usbenum.state(h.mh.read)["address"] == h.ADDR:
            missed.append(bit)
            assert answered, bit
        h.after_fault()
        assert h.good()
    assert missed == list(range(30, 36))


# The oracles and the fault layer themselves ---------------------------------------

def test_strict_receiver_oracle():
    bit = 32
    line = [1] * 40
    for lv in [0] + [1, 0, 1, 0, 0, 0, 0, 0] + [1]:
        line += [lv] * bit
    line += [1] * 40
    assert strict_8n1(line, bit) == [(5, True, 40)]
    assert strict_8n1([1] * 40 + [0] * 8 + [1] * 400, bit) == [("false start", 40)]
    bad_stop = [1] * 10 + [0] * (10 * bit) + [1] * 100
    assert strict_8n1(bad_stop, bit) == [(0, False, 10)]


def test_fcs_oracle_rejects_a_flip():
    fr = eth_frame(3)
    assert fcs_ok(fr)
    for i in (0, 12, len(fr) - 1):
        assert not fcs_ok(fr[:i] + bytes([fr[i] ^ 0x10]) + fr[i + 1:])


# Each mutant is a one-line firmware bug the sweep must turn into the verdict shown
# (None: the clean frame itself must fail, so no fault result could be believed).
MUTANTS = [
    ("uart_rx", "stuck", "    jmp pin RX, start               ; D+76  stop bit high: next byte",
     "    jmp start", "false-good"),
    ("can_rx", "flip", "    jmp r2, bad                       ; S+16", "    nop", "false-good"),
    ("can_rx", "flip", "    add r2, 79                        ; S+24  64 + 8n + 15 (CRC)", "    add r2, 78", None),
    ("eth10_match", "stuck", "    jmp t, frame\n    jmp drain", "    nop\n    jmp drain", "needs-reset"),
    ("eth10_rx", "stuck", "    jmp t, done                   ; the frame ended", "    nop", None),
]


@pytest.mark.parametrize("firmware,kind,old,new,verdict", MUTANTS)
def test_sweep_catches_firmware_mutant(firmware, kind, old, new, verdict):
    text = (FW / f"{firmware}.pasm").read_text()
    assert old in text
    text = text.replace(old, new)
    if verdict is None:
        with pytest.raises(AssertionError):
            case(HARNESS[firmware], kind, 0, text)
        return
    res = [case(HARNESS[firmware], kind, s, text) for s in SEEDS]
    assert any(r.verdict == verdict for r in res), [r.verdict for r in res]


def test_sweep_catches_usb_crc_mutant():
    """usb_enum with its CRC16 check gone: a flipped SETUP is ACKed and acted
    on, and the host gets a STALL or the wrong data for a good request."""
    text = usbenum.FW.read_text()
    old = "    jmp !c, idle                   ; bad: no handshake, the host retries\n    jmp !z, idle\n"
    assert old in text
    text = text.replace(old, "    nop\n    nop\n")
    res = [case(Usb, "flip", s, text) for s in SEEDS]
    assert any(r.verdict == "false-good" for r in res), [r.verdict for r in res]


def test_stuck_scl_finds_a_missing_timeout():
    """i2c_master with no timeout on the data bits: the bound in I2c.run fails."""
    text = (FW / "i2c_master.pasm").read_text()
    old = "    wait 1 pin SCL timeout r3     ; clock stretching  @point scl_wait"
    assert old in text
    h = I2c(text.replace(old, "    wait 1 pin SCL"))
    assert h.good()
    h.wire.add(faults.Stuck(h.SCL, 0))
    h.good()
    assert not h.done


def line(faults_):
    mh = ModelHost()
    return mh, Wire(mh, faults_, record=True)


def run_line(mh, wire, pattern):
    """Drive ui0 per clock from `pattern`; the line as the chip saw it."""
    out = []
    for lv in pattern:
        mh.ui = lv
        mh.step()
        out.append(wire.trace[-1][2][0] & 1)
    return out


def test_wire_without_faults_is_transparent():
    """The same frame with and without an empty Wire: the same clocks, the
    same byte, the same core state."""
    a, b = Uart(), Uart()
    a.mh.chip.step = a.wire.real_step
    a.send([0x5A])
    b.send([0x5A])
    ca, cb = a.mh.chip.cores[0], b.mh.chip.cores[0]
    assert (a.mh.chip.cycle, ca.pc, ca.r, list(ca.rxf)) == (b.mh.chip.cycle, cb.pc, cb.r, list(cb.rxf))
    assert rx0(a.mh) == rx0(b.mh) == [0x5A]


def test_wire_rejects_a_pin_that_is_not_an_input():
    for pin in (8, 15, 21, 24):
        with pytest.raises(ValueError):
            faults.Stuck(pin, 0)


def test_glitch_is_exact_and_shorter_than_a_bit():
    mh, w = line([faults.Glitch(16, 10, 3, bit=8)])
    assert run_line(mh, w, [1] * 20) == [1] * 10 + [0] * 3 + [1] * 7
    with pytest.raises(ValueError):
        faults.Glitch(16, 10, 8, bit=8)
    with pytest.raises(ValueError):
        faults.Glitch(16, 10, 0)
    mh, w = line([faults.Glitch(16, 2, 2, level=1)])
    assert run_line(mh, w, [1, 1, 1, 1, 0, 0]) == [1, 1, 1, 1, 0, 0]    # forced to what it was


def test_flip_stuck_truncate_are_exact():
    mh, w = line([faults.BitFlip(16, 4, 4)])
    assert run_line(mh, w, [1, 0] * 6) == [1, 0, 1, 0, 0, 1, 0, 1, 1, 0, 1, 0]
    mh, w = line([faults.Stuck(16, 0, 3, 5)])
    assert run_line(mh, w, [1] * 8) == [1, 1, 1, 0, 0, 1, 1, 1]
    mh, w = line([faults.Truncate(16, 1, 2)])
    assert run_line(mh, w, [0] * 6) == [0, 0, 1, 1, 1, 1]
    w.clear()
    assert run_line(mh, w, [0, 0]) == [0, 0]


def edges_of(v):
    return [i for i in range(1, len(v)) if v[i] != v[i - 1]]


def test_jitter_keeps_order_and_bound():
    """Every edge comes out, in order, late by 0..amp."""
    pattern = ([1] * 9 + [0] * 7) * 10
    mh, w = line([faults.EdgeJitter(16, 4, seed=1)])
    got = run_line(mh, w, pattern)
    a, b = edges_of(pattern), edges_of(got)
    assert len(a) == len(b)
    delays = [y - x for x, y in zip(a, b)]
    assert all(0 <= d <= 4 for d in delays) and len(set(delays)) > 2
    mh, w = line([faults.EdgeJitter(16, 4, seed=1)])
    assert run_line(mh, w, pattern) == got         # the seed decides it
    mh, w = line([faults.EdgeJitter(16, 4, seed=2)])
    assert run_line(mh, w, pattern) != got
    with pytest.raises(ValueError):
        faults.EdgeJitter(16, -1)


def test_jitter_never_reorders_edges():
    """Edges closer than the jitter: every edge still comes out, in order, a
    clock or more apart, so a pulse narrower than the jitter survives."""
    pattern = ([1] * 2 + [0] * 3) * 12 + [0] * 40
    mh, w = line([faults.EdgeJitter(16, 9, seed=3)])
    got = run_line(mh, w, pattern)
    a, b = edges_of(pattern), edges_of(got)
    assert len(b) == len(a) and got[-1] == pattern[-1]
    assert all(y >= x for x, y in zip(a, b)) and max(y - x for x, y in zip(a, b)) >= 5


def test_stretch_delays_from_the_nth_edge_on():
    pattern = ([1] * 5 + [0] * 5) * 4
    mh, w = line([faults.Stretch(16, 3, 2)])
    got = run_line(mh, w, pattern)
    assert [y - x for x, y in zip(edges_of(pattern), edges_of(got))] == [0, 0] + [2] * (len(edges_of(got)) - 2)
    with pytest.raises(ValueError):
        faults.Stretch(16, 0, 2)


def test_hold_stretches_the_nth_low():
    pattern = ([1] * 4 + [0] * 4) * 3
    mh, w = line([faults.Hold(16, 2, 7)])
    assert run_line(mh, w, pattern) == pattern[:12] + [0] * 7 + pattern[19:]


def test_half_clock_sample_goes_through_the_faults():
    """The falling-edge value a sender passes is faulted too, at t - 0.5."""
    seen = []

    class Chip:
        cycle = 7

        def step(self, ui, uio, *a, **kw):
            seen.append((ui, kw.get("ui_mid")))

    class Mh:
        chip = Chip()
        ui = 0
    Wire(Mh(), [faults.Stuck(16, 1, 6.5, 7)])
    Mh.chip.step(0, 0, ui_mid=0)
    assert seen == [(0, 1)]


def test_bus_shows_the_fault_to_a_wired_and_peer():
    h = I2c()
    assert h.wire.bus() & 0b11 == 0b11
    h.wire.add(faults.Stuck(h.SCL, 0))
    assert h.wire.bus() & 0b11 == 0b01
    assert h.mh.bus_uio() & 0b11 == 0b11           # the undisturbed resolver


def test_plan_is_the_seed_and_nothing_else():
    span = Span(1000, 32, 20, {16: 1})
    for kind in faults.KINDS:
        assert repr(faults.plan(kind, 5, span)) == repr(faults.plan(kind, 5, span))
        assert repr(faults.plan(kind, 5, span)) != repr(faults.plan(kind, 6, span)), kind
    with pytest.raises(ValueError):
        faults.plan("cosmic ray", 0, span)
    for s in range(50):
        [g] = faults.plan("glitch", s, span)
        assert 1000 <= g.start < span.end and 1 <= g.width < span.bit


def test_plan_covers_each_fault_class():
    span = Span(0, 16, 40, {16: 1, 0: 0})
    want = {"glitch": faults.Glitch, "stuck": faults.Stuck, "truncate": faults.Truncate, "flip": faults.BitFlip,
            "stretch": faults.Stretch, "jitter": faults.EdgeJitter}
    assert set(want) == set(faults.KINDS)
    for kind, cls in want.items():
        fs = faults.plan(kind, 1, span)
        assert [type(f) for f in fs] == [cls, cls] and [f.pin for f in fs] == [16, 0]
    assert all(f.level == span.idle[f.pin] for f in faults.plan("truncate", 1, span))
    for s in range(30):
        [f] = faults.plan("flip", s, Span(100, 16, 8, {16: 1}))
        assert (f.start - 100) % 16 == 0 and f.bit == 16
