"""MDIO (IEEE 802.3 Clause 22) bench firmware: host side and pins-only models
(docs/bench-firmware.md).

Host side of firmware/mdio_master.pasm (frame_word, parse_record) and of
firmware/mdio_phy.pasm (phy_defines, phy_setup, parse_write).

Pins-only models, which see levels and times and share no code with the
firmware's logic:

    PhyModel        a PHY: needs 32 preamble ones, answers after a chosen
                    clock-to-output delay, hands MDIO back after D0
    Sta             an STA with a chosen MDC period, setup, hold, preamble
                    length; outside its valid window it drives the inverse of
                    the bit, so it fails a sampler that looks at the wrong time
    decode_frames   frames from the MDC and MDIO edges alone
    bus_timing      the 802.3 numbers measured on those edges

Clause 22 numbers, as public secondary sources give them (the standard itself
was not available when this was written): MDC high and low >= 160 ns, period
>= 400 ns, no maximum (22.2.2.13); MDIO from the STA setup and hold >= 10 ns
around the rising edge of MDC (22.3.4); MDIO from the PHY 0..300 ns after the
rising edge. Frame: 32 ones, ST 01, OP 10 read / 01 write, PHYAD, REGAD, TA,
16 data bits.
"""
from . import host, sim

ST, OP_READ, OP_WRITE = 0b01, 0b10, 0b01
MDC_MIN_NS, MDC_PERIOD_NS, STA_SETUP_NS, STA_HOLD_NS, PHY_C2O_MAX_NS = 160.0, 400.0, 10.0, 10.0, 300.0
BMCR, BMSR, PHYID1, PHYID2, ANAR = 0, 1, 2, 3, 4
CONSTANT_REGS = tuple(r for r in range(32) if r not in (BMCR, BMSR, ANAR))
BMCR_READ_MASK = 0x7DFF          # reset (15) and restart auto-negotiation (9) read back 0
CRC16_NO_TAPS = 15 << 16         # CRCCTRL: width 16, neither tap


def _check(phy, reg, data=0):
    if not (0 <= phy < 32 and 0 <= reg < 32 and 0 <= data < 65536):
        raise ValueError("PHYAD and REGAD are 5 bits, data 16")


def frame_word(read, phy, reg, data=0):
    """mdio_master's TX word: the 32 bits after the preamble."""
    _check(phy, reg, data)
    if read and data:
        raise ValueError("a read has no data to send")
    op, ta = (OP_READ, 0) if read else (OP_WRITE, 0b10)
    return ST << 30 | op << 28 | phy << 23 | reg << 18 | ta << 16 | data


def parse_record(word):
    """mdio_master's RX word -> {"read", "ack", "data"}. ack: a PHY drove TA2
    low (writes have no such check on the wire: None)."""
    if word == 1 << 18:
        return {"read": False, "ack": None, "data": None}
    if word >> 17 == 1:
        return {"read": True, "ack": not word >> 16 & 1, "data": word & 0xFFFF}
    raise ValueError(f"not an mdio_master record: {word:#x}")


def master_defines(mdio=0, mdc=8, t=20, pre=32):
    if t % 2 or t < 6 or pre < 2:
        raise ValueError("T ticks per bit: even, at least 6; at least 2 preamble bits")
    return {"MDIO": mdio, "MDC": mdc, "T": t, "PRE": pre}


def master_load(core, prog, mdio=0, mdc=8, clkdiv=1 << 8):
    return host.load_program(core, prog, side_pins=mdc, clkdiv=clkdiv)


def phy_defines(mdio=1, mdc=16, phyad=1, base=256, regs=None):
    """Defines of mdio_phy: the constant registers from regs (default 0).
    regs may also give BMCR, BMSR and ANAR; phy_setup uses those."""
    regs = dict(regs or {})
    if not 0 <= phyad < 32:
        raise ValueError("PHYAD is 5 bits")
    d = {"MDIO": mdio, "MDC": mdc, "PHYAD": phyad, "BASE": base}
    for r in CONSTANT_REGS:
        d[f"V{r}"] = regs.get(r, 0)
    return d


def phy_timing_defines():
    return phy_defines()


def phy_setup(core, prog, regs=None, base=None):
    """Register writes: load mdio_phy, set R2 (BMCR), R3 (BMSR), the CRC tap
    to a plain 16-bit register and its value (ANAR)."""
    regs = regs or {}
    r = lambda off: sim.core_reg(core, off)
    return (host.load_program(core, prog, core * 256 if base is None else base, clkdiv=1 << 8)
            + [(r(sim.C_CRCCTRL), CRC16_NO_TAPS), (r(sim.C_CRC), regs.get(ANAR, 0)),
               (r(sim.C_R0 + 2), regs.get(BMCR, 0) & BMCR_READ_MASK), (r(sim.C_R0 + 3), regs.get(BMSR, 0))])


def parse_write(word):
    """mdio_phy's RX word -> {"reg", "ta", "data"}."""
    if word >> 23 != 1:
        raise ValueError(f"not an mdio_phy record: {word:#x}")
    return {"reg": word >> 18 & 31, "ta": word >> 16 & 3, "data": word & 0xFFFF}


class PhyModel:
    """A pins-only PHY. step(t_ns, mdc, mdio) -> what it drives on MDIO (0, 1 or
    None for released). regs: {reg: value}; writes land there (and in
    `writes`, with the TA bits seen). delay_ns: its clock-to-output delay.
    respond=False: never drives (no PHY at that address)."""

    def __init__(self, phyad=1, regs=None, delay_ns=100.0, respond=True, preamble=32):
        self.phyad, self.regs, self.delay, self.respond = phyad, dict(regs or {}), delay_ns, respond
        self.need = preamble
        self.ones, self.prev = 0, 1
        self.state, self.bits, self.n = "pre", 0, 0
        self.out, self.sched = None, []       # [(t, level or None)]
        self.frames, self.writes = [], []
        self.preamble_seen = 0

    def _later(self, t, level):
        self.sched.append((t + self.delay, level))

    def step(self, t, mdc, mdio):
        rising = mdc and not self.prev
        self.prev = mdc
        if rising:
            self._bit(t, mdio)
        while self.sched and self.sched[0][0] <= t:
            self.out = self.sched.pop(0)[1]
        return self.out

    def _bit(self, t, b):
        if self.state == "pre":
            if b:
                self.ones += 1
            elif self.ones >= self.need:
                self.preamble_seen, self.ones = self.ones, 0
                self.state, self.bits, self.n = "hdr", 0, 0
            else:
                self.ones = 0
            return
        if self.state == "hdr":
            self.bits = self.bits << 1 | b
            self.n += 1
            if self.n == 13:                 # ST1, OP, PHYAD, REGAD
                st1, op = self.bits >> 12 & 1, self.bits >> 10 & 3
                phy, reg = self.bits >> 5 & 31, self.bits & 31
                self.frames.append({"op": op, "phy": phy, "reg": reg, "preamble": self.preamble_seen})
                if not st1 or phy != self.phyad or op not in (OP_READ, OP_WRITE) or not self.respond:
                    self.state = "pre"
                elif op == OP_READ:
                    self.reg, self.state, self.n = reg, "ta1", 0
                else:
                    self.reg, self.state, self.bits, self.n = reg, "wr", 0, 0
            return
        if self.state == "ta1":               # the rising edge of TA1: drive TA2 next
            self.sent = self.regs.get(self.reg, 0)
            self._later(t, 0)
            self.state, self.n = "rd", 0
            return
        if self.state == "rd":
            if self.n < 16:
                self._later(t, self.sent >> (15 - self.n) & 1)
                self.n += 1
            else:
                self._later(t, None)
                self.state, self.ones = "pre", 0
            return
        if self.state == "wr":
            self.bits = self.bits << 1 | b
            self.n += 1
            if self.n == 18:
                ta, data = self.bits >> 16, self.bits & 0xFFFF
                self.writes.append((self.reg, ta, data))
                if ta == 0b10:
                    self.regs[self.reg] = data
                self.state, self.ones = "pre", 0


class Sta:
    """A pins-only STA, for firmware that plays the PHY. step(t_ns, mdio) ->
    (mdc, its MDIO drive: 0, 1 or None).

    period_ns: MDC period, high half of it. setup_ns, hold_ns: the bit it
    drives is valid only from setup before to hold after the rising edge; the
    rest of the bit time it drives the inverse (the worst a legal STA can do
    at those numbers). A read's bits are sampled at the rising edge.
    ops: ("r", phy, reg), ("w", phy, reg, data), each with optional keywords
    in a dict as last element: pre (preamble ones), prefix (bits before the
    preamble), st, op, ta (for a write), idle (idle clocks after the frame,
    default 1).
    results: per op, reads {"ta2", "data"}; writes None."""

    def __init__(self, ops, period_ns=400.0, setup_ns=10.0, hold_ns=10.0, t0=10000.0):
        self.period, self.setup, self.hold = period_ns, setup_ns, hold_ns
        self.events = []              # (t, kind, value)
        self.results = []
        t = t0
        for i, op in enumerate(ops):
            t = self._frame(i, op, t)
        self.t_end = t
        self.events.sort(key=lambda e: e[0])
        self.mdc, self.drive = 0, None
        self._k = 0

    def _frame(self, i, op, t):
        kw = op[-1] if isinstance(op[-1], dict) else {}
        read = op[0] == "r"
        phy, reg = op[1], op[2]
        data = 0 if read else op[3]
        pre = kw.get("pre", 32)
        st, opc = kw.get("st", ST), kw.get("op", OP_READ if read else OP_WRITE)
        bits = list(kw.get("prefix", ())) + [1] * pre + [st >> 1, st & 1, opc >> 1, opc & 1]
        bits += [phy >> b & 1 for b in range(4, -1, -1)] + [reg >> b & 1 for b in range(4, -1, -1)]
        n_hdr = len(bits)
        if read:
            bits += [None] * 18
        else:
            ta = kw.get("ta", 0b10)
            bits += [ta >> 1, ta & 1] + [data >> b & 1 for b in range(15, -1, -1)]
        P = self.period
        self.results.append(None if not read else {"ta2": None, "data": 0})
        for k, b in enumerate(bits):
            rise = t + P / 2 + k * P
            self.events.append((rise, "mdc", 1))
            self.events.append((rise + P / 2, "mdc", 0))
            if b is None:
                if k == n_hdr:           # released from the falling edge before TA1
                    self.events.append((rise - P / 2, "mdio", None))
                self.events.append((rise, "sample", (i, k - n_hdr)))
            else:
                self.events.append((rise - P / 2, "mdio", 1 - b))
                self.events.append((rise - self.setup, "mdio", b))
                self.events.append((rise + self.hold, "mdio", 1 - b))
        end = t + P / 2 + len(bits) * P - P / 2
        self.events.append((end, "mdio", None))
        idle = kw.get("idle", 1)
        for k in range(idle):
            rise = end + P / 2 + k * P
            self.events.append((rise, "mdc", 1))
            self.events.append((rise + P / 2, "mdc", 0))
        return end + idle * P + P

    def step(self, t, mdio):
        ev = self.events
        while self._k < len(ev) and ev[self._k][0] <= t:
            _, kind, v = ev[self._k]
            self._k += 1
            if kind == "mdc":
                self.mdc = v
            elif kind == "mdio":
                self.drive = v
            else:
                i, k = v
                r = self.results[i]
                if k == 1:
                    r["ta2"] = mdio
                elif k >= 2:
                    r["data"] = r["data"] << 1 | mdio
        return self.mdc, self.drive

    def done(self, t):
        return self._k >= len(self.events)


class Wire:
    """The MDIO wire: tri-state drivers and a pull-up. resolve(drivers) -> the
    level; `contention` counts instants two drivers fought, of any value:
    the turnaround must never let that happen."""

    def __init__(self):
        self.contention = 0

    def resolve(self, *drivers):
        d = [x for x in drivers if x is not None]
        if len(d) > 1:
            self.contention += 1
        return 1 if not d else min(d)


def decode_frames(mdc_edges, mdio_edges):
    """Frames from edges alone ([(t_ns, level)], the first entry each line's
    initial level): MDIO is read just before each rising MDC edge. A frame
    starts at the first 0 after at least `preamble` ones. Returns
    [{preamble, st, op, phy, reg, ta, data, rise (rising edge times)}]."""
    rises = [t for t, v in mdc_edges if v]
    i, m, level = 0, len(mdio_edges), 1
    bits = []
    for t in rises:
        while i < m and mdio_edges[i][0] < t:
            level = mdio_edges[i][1]
            i += 1
        bits.append((t, level))
    out, k, ones = [], 0, 0
    while k < len(bits):
        if bits[k][1]:
            ones += 1
            k += 1
            continue
        if k + 32 > len(bits):
            break
        f = [b for _, b in bits[k:k + 32]]
        num = lambda s, e: int("".join(map(str, f[s:e])), 2)
        out.append({"preamble": ones, "st": num(0, 2), "op": num(2, 4), "phy": num(4, 9), "reg": num(9, 14),
                    "ta": (f[14], f[15]), "data": num(16, 32), "rise": [t for t, _ in bits[k:k + 32]]})
        k += 32
        ones = 0
    return out
