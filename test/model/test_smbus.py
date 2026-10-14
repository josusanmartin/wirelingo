"""SMBus host with PEC (firmware/smbus_host.pasm, post-freeze) on the cycle
model.

Judges: pe.postfreeze3.smbus_pec (the specification's CRC-8, checked
against the CRC catalogue's check value), an independent SMBus device model
on the open-drain bus that computes the PEC of what it saw, checks the PEC
of writes, appends one to reads, stretches the clock and resets after
35 ms of SCL low (pe.postfreeze3.SmbusDevice), timing measured on the pins,
and sigrok's i2c decoder (whose bytes the PEC is recomputed over).

The chip runs at 3.571 MHz with CLKDIV 4 (1 tick = 1.12 us, tick for tick
the waveform of 50 MHz / CLKDIV 56)."""
import pathlib

import pytest

from pe import asm, host, postfreeze as pf, postfreeze3 as p3, sigrok, sim
from pe.modelhost import ModelHost

FW = pathlib.Path(__file__).resolve().parents[2] / "firmware"
SDA, SCL, FOV = 0, 1, 7          # uio0, uio1 (open drain)
F_CLK = 4 * 1e9 / p3.SMBUS_TICK
BAT = 0x0B                       # a smart battery's address

REGS = {0x09: bytes([0x34, 0x12]),                        # Voltage (read word)
        0x0D: bytes([0x5A]),                              # a byte register
        0x20: bytes([7]) + b"Wireli\x6e"[:7],             # ManufacturerName (block read)
        0x21: bytes([0]),                                  # an empty block
        None: bytes([0xA5])}                              # receive byte


class Rig:
    """smbus_host on core 0; SDA and SCL open drain with pull-ups; the bus =
    the chip's drive AND every device's (and any extra driver's)."""

    def __init__(self, devices=(), f_clk=F_CLK, start=True, extra=None):
        self.mh = mh = ModelHost()
        prog = asm.assemble((FW / "smbus_host.pasm").read_text(), p3.smbus_defines(SDA, SCL, FOV))
        assert len(prog.words) <= 256
        mh.apply(host.load_program(0, prog, clkdiv=p3.smbus_clkdiv(f_clk)))
        mh.write(sim.core_reg(0, sim.C_CRCCTRL), p3.SMBUS_CRCCTRL)
        mh.write(sim.A_POUT, 3)
        mh.write(sim.A_OD, 3)
        mh.pull_up = 3
        self.period = 1e9 / f_clk
        self.devices = list(devices)
        self.extra = extra                        # fn(t, scl, sda) -> (scl drive, sda drive)
        self.drv = [(1, 1)] * (len(self.devices) + 1)
        self.log, self.words, self.drive = [(0.0, 3)], [], [(0.0, 3)]
        self.t_rec, self.n_popped = [], 0           # when each RX word arrived
        orig = mh.chip.step

        def step(*a, **kw):
            self._observe()
            return orig(*a, **kw)
        mh.chip.step = step
        if start:
            mh.start(1)

    def _observe(self):
        mh = self.mh
        t = mh.chip.cycle * self.period
        bus = mh.bus_uio() & 3
        scl, sda = bus >> 1 & 1, bus & 1
        drv = [d.step(t, scl, sda) for d in self.devices]
        if self.extra:
            drv.append(self.extra(t, scl, sda))
        low = 0
        for s, d in drv:
            low |= (0 if d else 1) | (0 if s else 2)
        mh.ext_oe, mh.uio = low, 0
        if bus != self.log[-1][1]:
            self.log.append((t, bus))
        d = mh.chip.pin_out & 3
        if d != self.drive[-1][1]:
            self.drive.append((t, d))
        if mh.chip.cores[0].rxf and len(self.t_rec) < len(mh.chip.cores[0].rxf) + self.n_popped:
            self.t_rec.append(t)

    def now(self):
        return self.mh.chip.cycle * self.period

    def run(self, ns, until=None):
        mh, chip = self.mh, self.mh.chip
        for _ in range(int(ns / self.period)):
            mh.step()
            if chip.cores[0].rxf:
                w = mh.pop_all(0)
                self.n_popped += len(w)
                self.words += w
                if until and until(self):
                    return

    def transact(self, words, wait=60e6):
        n0 = len(p3.smbus_records(self.words))
        for w in words:
            while self.mh.tx_level(0) >= 8:
                self.run(20e3)
            self.mh.push(0, w)
        self.run(wait, until=lambda r: len(p3.smbus_records(r.words)) > n0)
        self.run(20e3)
        return p3.smbus_records(self.words)[-1]


def test_pec_check_value():
    """The CRC catalogue's CRC-8/SMBUS: check value 0xF4 over "123456789"."""
    assert p3.smbus_pec(b"123456789") == 0xF4


def test_smbus_protocols_with_pec():
    """Every SMBus protocol with PEC against a device with PEC: write
    byte/word, read byte/word, block write/read, process call, send/receive
    byte, quick command; the device saw the right bytes and a correct PEC on
    every write; the firmware checked the PEC of every read."""
    dev = p3.SmbusDevice(BAT, REGS)
    rig = Rig([dev])
    rig.run(50e3)
    plan = [
        ("write byte", p3.smbus_words(BAT, [0x0D, 0x42], pec=True), "ok", b""),
        ("write word", p3.smbus_words(BAT, [0x09, 0x78, 0x56], pec=True), "ok", b""),
        ("read byte", p3.smbus_words(BAT, [0x0D], 1, pec=True), "pec_ok", REGS[0x0D]),
        ("read word", p3.smbus_words(BAT, [0x09], 2, pec=True), "pec_ok", REGS[0x09]),
        ("block write", p3.smbus_words(BAT, [0x30, 3, 1, 2, 3], pec=True), "ok", b""),
        ("block read", p3.smbus_words(BAT, [0x20], 1, pec=True, block=True), "pec_ok", REGS[0x20]),
        ("process call", p3.smbus_words(BAT, [0x09, 0x11, 0x22], 2, pec=True), "pec_ok", REGS[0x09]),
        ("send byte", p3.smbus_words(BAT, [0x5C], pec=True), "ok", b""),
        ("receive byte", p3.smbus_words(BAT, b"", 1, pec=True), "pec_ok", REGS[None]),
        ("quick write", p3.smbus_words(BAT, quick_rw=0), "ok", b""),
        ("quick read", p3.smbus_words(BAT, quick_rw=1), "ok", b""),
        ("empty block", p3.smbus_words(BAT, [0x21], 1, pec=True, block=True), "pec_ok", REGS[0x21]),
    ]
    out = []
    for name, words, want, data in plan:
        r = rig.transact(words)
        out.append((name, r))
        got = r["read"][:-1] if want == "pec_ok" else r["read"]
        assert r["result"] == want and got == data, (name, r)
    trs = dev.transactions
    print("\n" + "\n".join(f"  {n}: {r['read'].hex()} {r['result']}; the device: write {t['write'].hex()} "
                           f"read {t['read'].hex()} pec_ok {t['pec_ok']}" for (n, r), t in zip(out, trs)))
    assert len(trs) == len(plan)
    writes = [t for t in trs if not t["read"] and len(t["write"]) >= 2]
    assert writes and all(t["pec_ok"] for t in writes)
    assert [t["write"][:-1].hex() for t in writes] == ["0d42", "097856", "3003010203", "5c"]
    # reads: the master acknowledged every byte but the last (the PEC)
    for t in trs:
        if t["read"]:
            assert t["acks"][-1] == 1 and all(a == 0 for a in t["acks"][:-1]), t
    assert [t["sr"] for t in trs] == [False, False, True, True, False, True, True, False, False, False, False, True]


def test_smbus_without_pec():
    """The same transactions without PEC, against a device without PEC: the
    last byte read is not acknowledged, results "ok"."""
    dev = p3.SmbusDevice(BAT, REGS, pec=False)
    rig = Rig([dev])
    rig.run(50e3)
    assert rig.transact(p3.smbus_words(BAT, [0x09], 2))["read"] == REGS[0x09]
    r = rig.transact(p3.smbus_words(BAT, [0x20], 1, block=True))
    assert r["read"] == REGS[0x20] and r["result"] == "ok"
    assert rig.transact(p3.smbus_words(BAT, [0x0D, 0x99]))["result"] == "ok"
    assert dev.transactions[-1]["write"] == b"\x0d\x99"
    assert [t["acks"] for t in dev.transactions[:2]] == [[0, 1], [0] * 7 + [1]]


def test_smbus_pec_errors():
    """Negative controls: the device's PEC is wrong (its register says so
    after one bit flip in the data); a device without PEC answers a read
    that expects one (the last data byte taken as the PEC). Both
    'pec_bad'."""
    dev = p3.SmbusDevice(BAT, REGS)
    rig = Rig([dev])
    rig.run(50e3)
    real = dev._next_read_byte

    def flip():
        b = real()
        return b ^ 0x10 if dev.tr["out"] and len(dev.tr["read"]) == 0 else b
    dev._next_read_byte = flip
    r = rig.transact(p3.smbus_words(BAT, [0x09], 2, pec=True))
    assert r["result"] == "pec_bad" and r["read"][0] == REGS[0x09][0] ^ 0x10
    dev._next_read_byte = real
    dev.pec = False
    r = rig.transact(p3.smbus_words(BAT, [0x09], 1, pec=True))
    assert r["result"] == "pec_bad" and r["read"] == REGS[0x09]
    dev.pec = True
    assert rig.transact(p3.smbus_words(BAT, [0x09], 2, pec=True))["result"] == "pec_ok"


def test_smbus_nacks():
    """No device at the address: 'addr_nack', the transaction's 4 data
    words dropped; a device that refuses the second data byte:
    'data_nack', the rest dropped; the next transaction is correct."""
    dev = p3.SmbusDevice(BAT, REGS, nack_after=1)
    rig = Rig([dev])
    rig.run(50e3)
    r = rig.transact(p3.smbus_words(0x50, [1, 2, 3, 4], pec=True))
    assert r["result"] == "addr_nack" and r["unsent"] == 4
    r = rig.transact(p3.smbus_words(BAT, [0x30, 5, 1, 2, 3, 4, 5], pec=True))
    assert r["result"] == "data_nack" and r["unsent"] == 5
    dev.nack_after = None
    r = rig.transact(p3.smbus_words(BAT, [0x09], 2, pec=True))
    assert r["result"] == "pec_ok" and r["read"][:2] == REGS[0x09]
    assert rig.mh.tx_level(0) == 0


@pytest.mark.parametrize("stretch_us", [3, 50, 2000])
def test_smbus_clock_stretching(stretch_us):
    """A device that holds SCL low after every ACK (3 us, 50 us, 2 ms):
    every transaction completes, PEC checked."""
    dev = p3.SmbusDevice(BAT, REGS, stretch=stretch_us * 1e3)
    rig = Rig([dev])
    rig.run(50e3)
    r = rig.transact(p3.smbus_words(BAT, [0x20], 1, pec=True, block=True), wait=200e6)
    assert r["result"] == "pec_ok" and r["read"][:-1] == REGS[0x20]
    r = rig.transact(p3.smbus_words(BAT, [0x30, 2, 9, 8], pec=True), wait=200e6)
    assert r["result"] == "ok" and dev.transactions[-1]["pec_ok"]


def test_smbus_timeout():
    """A device that would hold SCL low for 40 ms after the command byte:
    the firmware gives up 25.0 ms after it released SCL (SMBus: 25..35 ms),
    releases the bus and says 'timeout'; the device, as SMBus asks, resets
    itself after 35 ms of SCL low and lets go; the next transaction works."""
    dev = p3.SmbusDevice(BAT, REGS, hang=(2, 40e6))
    rig = Rig([dev])
    rig.run(50e3)
    r = rig.transact(p3.smbus_words(BAT, [0x09], 2, pec=True), wait=100e6)
    assert r["result"] == "timeout"
    rig.run(20e6)                                   # the device lets go at 35 ms (its reset)
    lows = [(t, nxt - t) for (t, v), (nxt, _) in zip(rig.log, rig.log[1:]) if not v & 2 and nxt - t > 1e6]
    rel = max(t for t, v in rig.drive if v & 2 and t < rig.t_rec[-1])     # the chip let SCL go
    gave_up = rig.t_rec[-1] - rel
    print(f"\nSCL held low {lows[0][1] / 1e6:.3f} ms; the firmware gave up {gave_up / 1e6:.3f} ms after "
          f"releasing SCL (its record)")
    assert lows and 34.99e6 <= lows[0][1] <= 35.01e6
    assert 25.0e6 <= gave_up <= 25.05e6
    assert dev.transactions[-1]["aborted"]
    dev.hang = None
    rig.run(20e6)
    assert rig.transact(p3.smbus_words(BAT, [0x09], 2, pec=True))["result"] == "pec_ok"


def test_smbus_busy():
    """SDA held low by something else before the START: after 25 ms the
    firmware says 'busy' and drops the transaction's data words."""
    rig = Rig([], extra=lambda t, scl, sda: (1, 0 if t < 40e6 else 1))
    rig.run(10e3)
    r = rig.transact(p3.smbus_words(BAT, [0x09, 1, 2], pec=True), wait=60e6)
    assert r["result"] == "busy" and r["unsent"] == 3 and rig.mh.tx_level(0) == 0


class OtherMaster:
    """A second master that starts with us and sends its address byte in
    step with our SCL (wired-AND): it keeps driving SDA while it wins."""

    def __init__(self, byte):
        self.byte, self.k, self.on, self.lost = byte, None, False, False
        self.prev = (1, 1)
        self.sda = 1

    def __call__(self, t, scl, sda):
        pscl, psda = self.prev
        self.prev = (scl, sda)
        if self.k is None and scl and pscl and psda and not sda:      # our START: start too
            self.k, self.sda = 0, 1
        elif self.k is not None and not self.lost:
            if pscl and not scl and self.k < 8:                       # SCL fell: next bit
                self.sda = (self.byte >> (7 - self.k)) & 1
                self.k += 1
            elif scl and not pscl and self.sda == 1 and sda == 0:
                self.lost, self.sda = True, 1
            elif pscl and not scl and self.k >= 8:
                self.sda = 1
        return 1, self.sda


def test_smbus_arbitration_lost():
    """Another master starts with us and addresses 0x08 (0001000x) where we
    address 0x0B (0001011x): at the 6th address bit we send a 1 and see a 0:
    'lost', both lines released."""
    other = OtherMaster(0x08 << 1)
    rig = Rig([], extra=other)
    rig.run(10e3)
    r = rig.transact(p3.smbus_words(BAT, [0x09], 2, pec=True))
    assert r["result"] == "lost" and not other.lost and r["unsent"] == 1
    p = rig.mh.chip.pin_out
    assert p & 3 == 3                                           # SDA and SCL released


def test_smbus_timing():
    """On the pins (tick for tick 50 MHz / CLKDIV 56): SCL 89..99 kHz inside
    a byte, tLOW >= 4.7 us, tHIGH >= 4.0 us, START hold >= 4.0 us, STOP setup
    >= 4.0 us, bus free >= 4.7 us, SDA changes only while SCL is low (except
    START/STOP)."""
    dev = p3.SmbusDevice(BAT, REGS)
    rig = Rig([dev])
    rig.run(50e3)
    for _ in range(2):
        rig.transact(p3.smbus_words(BAT, [0x20], 1, pec=True, block=True))
        rig.transact(p3.smbus_words(BAT, [0x09, 1, 2], pec=True))
    scl = pf.bit_edges(rig.log, 1)
    sda = pf.bit_edges(rig.log, 0)
    hi = [d for v, t, d in pf.runs(scl, rig.now()) if v == 1 and d < 1e6]
    lo = [d for v, t, d in pf.runs(scl, rig.now()) if v == 0 and d < 1e6]
    rises = [t for t, v in scl if v]
    per = [b - a for a, b in zip(rises, rises[1:]) if b - a < 30e3]
    # SDA edges while SCL is high: START and STOP only
    def scl_at(t):
        lv = 1
        for tt, v in scl:
            if tt > t:
                break
            lv = v
        return lv
    sta = [t for t, v in sda if v == 0 and scl_at(t)]
    sto = [t for t, v in sda if v == 1 and t > 0 and scl_at(t)]
    print(f"\nSCL high {min(hi) / 1e3:.2f}..{max(hi) / 1e3:.2f} us, low {min(lo) / 1e3:.2f}..{max(lo) / 1e3:.2f} us, "
          f"bit period {min(per) / 1e3:.2f}..{max(per) / 1e3:.2f} us; {len(sta)} START/Sr, {len(sto)} STOP")
    assert min(hi) >= 4.0e3 and min(lo) >= 4.7e3 and 1e9 / min(per) <= 100e3
    assert len(sta) == 6 and len(sto) == 4
    for t in sta:                                    # hold: SCL falls >= 4.0 us after the START
        assert next(tt for tt, v in scl if tt > t and v == 0) - t >= 4.0e3
    for t in sto:                                    # setup: SCL rose >= 4.0 us before the STOP
        assert t - max(tt for tt, v in scl if tt < t and v == 1) >= 4.0e3
    free = [b - a for a in sto for b in sta if b > a][:1]
    assert all(f >= 4.7e3 for f in free)


@pytest.mark.skipif(sigrok.cli() is None, reason=sigrok.INSTALL_HINT)
def test_sigrok_smbus():
    """sigrok's i2c decoder on SDA/SCL: the addresses, data and ACK/NACK of
    a write word, a read word and a block read with PEC; the PEC recomputed
    over the bytes sigrok decoded (address bytes included) matches the PEC
    byte on the bus every time."""
    dev = p3.SmbusDevice(BAT, REGS)
    rig = Rig([dev])
    rig.run(50e3)
    rig.transact(p3.smbus_words(BAT, [0x09, 0x78, 0x56], pec=True))
    rig.transact(p3.smbus_words(BAT, [0x09], 2, pec=True))
    rig.transact(p3.smbus_words(BAT, [0x20], 1, pec=True, block=True))
    rig.run(50e3)
    tr = sigrok.Trace()
    pf.to_trace(tr, pf.bit_edges(rig.log, 1), "scl")
    pf.to_trace(tr, pf.bit_edges(rig.log, 0), "sda")
    tr.end(round(rig.now() * 1000))
    toks = sigrok.i2c(tr, "scl", "sda", res_ps=10_000)
    frames, cur = [], None
    for _, k in toks:
        if k in ("S",):
            cur = []
            frames.append(cur)
        elif isinstance(k, tuple) and k[0] in ("AW", "AR"):
            cur.append((k[1] << 1) | (k[0] == "AR"))
        elif isinstance(k, tuple) and k[0] in ("DW", "DR"):
            cur.append(k[1])
    print(f"\nsigrok i2c transactions: {[bytes(f).hex() for f in frames]}")
    assert len(frames) == 3
    for f in frames:
        assert p3.smbus_pec(bytes(f[:-1])) == f[-1]
    assert bytes(frames[2][3:-1]) == REGS[0x20]
