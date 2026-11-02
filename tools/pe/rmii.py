"""RMII far end and host for the cycle model (docs/rmii-study.md).

Pins-only: the PHY side sees nothing of the chip but its pads, and the host sees
only what the SPI port returns. Nothing here imports firmware knowledge except the
FIFO word formats, which the firmware's header states.

QuadPort is the clk side of src/pe_hostport.sv, register for register, driven by
an SCK schedule, so a host-drain test pays the port's real latency, its two words
of read-ahead, and every header and CS gap.
"""
import pathlib
import zlib

from . import asm, host, sim

MIN_GAP = 48          # 96 bit times at 2 bits per clock
BUS_NS = 20           # one REF_CLK
MAXWORDS = 600        # firmware/rmii_rx.pasm
F_OVERFLOW, F_TOO_LONG, F_MISSED = 2, 4, 8
OVF_FLAG_BIT = 1      # F_OVF: global flag 0
SYNC_BYPASS = 0x0C    # uio2, uio3
TX_PINS = 8           # uo0..2: TXD0, TXD1, TX_EN
HIT_PIN = 11          # uo3


# ---------------------------------------------------------------- frames
def with_fcs(body):
    """body + its CRC-32, as it goes on the wire."""
    return body + zlib.crc32(body).to_bytes(4, "little")


def make_frame(size, seed=0, fill=None, ethertype=0x88B5):
    """A frame of `size` bytes including the FCS (64..1518)."""
    head = bytes.fromhex("ffffffffffff020000c0ffee") + ethertype.to_bytes(2, "big")
    n = size - 14 - 4
    pay = bytes([fill]) * n if fill is not None else bytes((seed * 37 + i * 11 + (i >> 3)) & 255 for i in range(n))
    return with_fcs(head + pay)


def to_dibits(data):
    """Bytes to dibits (values 0..3, RXD0 = bit 0), low bits of each byte first."""
    return [(b >> s) & 3 for b in data for s in (0, 2, 4, 6)]


def from_dibits(d):
    """Dibits to bytes; a part byte at the end is dropped."""
    return bytes(sum(d[i + j] << (2 * j) for j in range(4)) for i in range(0, len(d) - 3, 4))


PREAMBLE = bytes([0x55] * 7 + [0xD5])


def fcs_ok(frame):
    return len(frame) >= 4 and with_fcs(frame[:-4]) == frame


# ---------------------------------------------------------------- PHY to chip
class PhyRx:
    """The PHY's receive pins, one (RXD, CRS_DV) per clock.

    `frame(...)` appends one frame: `lead` clocks of CRS_DV high with RXD = 00
    (carrier before decode), preamble + SFD, the data. `toggle` nibbles before the
    data ends, carrier is lost: CRS_DV low on a nibble's first dibit and high on its
    second (LAN8720A datasheet rev 1.4 section 3.4.1.1). `corrupt`: dibit index
    counted from the first preamble dibit -> value XORed in."""

    def __init__(self):
        self.pins = []

    def idle(self, n):
        self.pins += [(0, 0)] * n

    def frame(self, data, lead=4, toggle=0, corrupt=None, preamble=PREAMBLE):
        d = to_dibits(preamble) + to_dibits(data)
        for i, x in (corrupt or {}).items():
            d[i] ^= x
        self.pins += [(0, 1)] * lead
        for i, x in enumerate(d):
            left = len(d) - i                  # dibits left, this one included
            crs = 1
            if left <= 2 * toggle and left % 2 == 0:
                crs = 0                        # a nibble's first dibit
            self.pins.append((x, crs))

    def at(self, cycle):
        return self.pins[cycle] if cycle < len(self.pins) else (0, 0)


def rx_pads(rxd, crs):
    """uio pads: each RXD line on two pads, CRS_DV on uio4 (the firmware's header)."""
    return rxd | (rxd << 2) | (crs << 4)


# ---------------------------------------------------------------- chip to PHY
class PhyTx:
    """Samples TX pins on every REF_CLK rise. `.bursts`: TX_EN runs, as dibits,
    with the clock each started on."""

    def __init__(self):
        self.bursts = []
        self.cur = None
        self.cycle = 0
        self.ends = []

    def sample(self, uo):
        v = (uo >> (TX_PINS - 8)) & 7
        en, d = v >> 2, v & 3
        if en:
            if self.cur is None:
                self.cur = []
                self.bursts.append((self.cycle, self.cur))
            self.cur.append(d)
        elif self.cur is not None:
            self.ends.append(self.cycle)
            self.cur = None
        self.cycle += 1

    def frames(self):
        """(start clock, bytes after the SFD, preamble intact) per burst."""
        out = []
        for t, d in self.bursts:
            pre = to_dibits(PREAMBLE)
            out.append((t, from_dibits(d[len(pre):]), d[:len(pre)] == pre, len(d)))
        return out


def tx_samples(frames, gap=MIN_GAP, lead=0, tail=20):
    """The per-clock samples (bit 2 = TX_EN) for preamble + SFD + frame, with gaps."""
    s = [0] * lead
    for i, fr in enumerate(frames):
        if i:
            s += [0] * gap
        s += [4 | x for x in to_dibits(PREAMBLE + fr)]
    return s + [0] * tail


def tx_words(samples):
    """Pack samples, 10 to a word; the last word is padded with idle."""
    s = list(samples) + [0] * (-len(samples) % 10)
    return [sum(s[i + j] << (3 * j) for j in range(10)) for i in range(0, len(s), 10)]


# ---------------------------------------------------------------- host side of RX
class RxRecord:
    def __init__(self, data, flags, count_ok):
        self.data, self.flags, self.count_ok = data, flags, count_ok

    @property
    def good(self):
        """Delivered whole: no flag against its own data, the word count agrees."""
        return self.count_ok and not self.flags & (F_OVERFLOW | F_TOO_LONG) and fcs_ok(self.data)


def parse_rx(words):
    """FIFO reads (zeros are reads of an empty FIFO) to records. Each record's data
    is its dibits as whole bytes; a frame's end leaves a part byte, which goes."""
    recs, d, full = [], [], 0
    for w in words:
        if w == 0:
            continue
        if w & 1:
            flags = w & 0xFFFF & ~1
            recs.append(RxRecord(from_dibits(d), flags, MAXWORDS - (w >> 16) == full))
            d, full = [], 0
            continue
        n = 0
        for i in range(6):
            s = (w >> (2 + 5 * i)) & 31
            if s & 1:
                d += [(s >> 1) & 3, (s >> 3) & 3]
                n += 1
        full += n == 6
    return recs, d


# ---------------------------------------------------------------- the port
class QuadPort:
    """pe_hostport's clk side, cycle for cycle, with an SCK schedule.

    A script is a generator yielding, once per clk, (cs_n, events). Events happen
    just after this clk edge, on the SCK side: 'a' an address word arrived, ('w', x)
    a write word, 's' a read word's last bit sent, 'l' a read word loaded into the
    output shifter (the SCK fall). With `late`, every toggle reaches the clk side a
    cycle later (the other phase of SCK against clk)."""

    def __init__(self, chip, late=False):
        self.chip, self.late = chip, late
        self.a_tog = self.w_tog = self.r_tog = 0
        self.a_s = self.w_s = self.r_s = 0
        self.a_d = self.w_d = self.r_d = 0
        self.cs_s = 7
        self.cs = 1
        self.a_hold, self.m_hold, self.w_hold = 0, (0, 0), 0
        self.wpar = 0
        self.active = self.c_rd = self.c_inc = 0
        self.snap_addr = self.com_addr = 0
        self.outstanding = self.fill = 0
        self.a_pend = self.w_pend = self.need_commit = self.need_snap = 0
        self.bus_addr = self.bus_we = self.bus_wdata = self.bus_snap = self.bus_commit = self.bus_cancel = 0
        self.buf = [0, 0]
        self.script = None
        self.rx = []                   # (clk, word) as the host receives them
        self.pushes = 0
        self.ui, self.uio = 0, 0

    def clock(self, ui=0, uio=0):
        """One clk edge of the whole chip."""
        c = self.chip
        addr = self.bus_addr
        rdata = c.read(addr) if self.bus_snap else None
        c.step(ui, uio, we=bool(self.bus_we), addr=addr, wdata=self.bus_wdata,
               commit=bool(self.bus_commit), snap=bool(self.bus_snap), cancel=bool(self.bus_cancel))
        self._edge(rdata)

    def _edge(self, rdata):
        # the hostport's clk-domain always blocks, reading start-of-cycle state
        a_ev = ((self.a_s >> 2) ^ (self.a_s >> 1)) & 1
        w_ev = ((self.w_s >> 2) ^ (self.w_s >> 1)) & 1
        r_ev = ((self.r_s >> 2) ^ (self.r_s >> 1)) & 1
        a_in, w_in, r_in = (self.a_d, self.w_d, self.r_d) if self.late else (self.a_tog, self.w_tog, self.r_tog)
        self.a_d, self.w_d, self.r_d = self.a_tog, self.w_tog, self.r_tog
        self.a_s = ((self.a_s << 1) | a_in) & 7
        self.w_s = ((self.w_s << 1) | w_in) & 7
        self.r_s = ((self.r_s << 1) | r_in) & 7
        cs2 = (self.cs_s >> 2) & 1
        self.cs_s = ((self.cs_s << 1) | self.cs) & 7

        bus_snap = self.bus_snap
        self.bus_we = self.bus_snap = self.bus_commit = self.bus_cancel = 0
        if bus_snap:
            self.buf[self.fill] = rdata
            self.fill ^= 1
        a_pend, w_pend, need_commit = self.a_pend, self.w_pend, self.need_commit
        if a_ev:
            self.a_pend = 1
        if w_ev:
            self.w_pend = 1
        if r_ev:
            self.need_commit = 1
        if need_commit and self.outstanding == 0:
            self.need_commit = 0
        elif need_commit:
            self.need_commit = 0
            self.bus_commit, self.bus_addr = 1, self.com_addr
            self.com_addr += self.c_inc
            self.outstanding -= 1
            self.need_snap = 1
        elif w_pend:
            self.w_pend = 0
            self.bus_we, self.bus_wdata, self.bus_addr = 1, self.w_hold, self.snap_addr
            self.snap_addr += self.c_inc
            self.pushes += 1
        elif (a_pend or cs2) and self.active:
            self.active = self.need_snap = self.outstanding = 0
            if self.c_rd:
                self.bus_cancel = 1
        elif a_pend:
            self.a_pend = 0
            self.active = 1
            self.c_rd, self.c_inc = self.m_hold
            self.snap_addr = self.com_addr = self.a_hold
            self.fill = 0
            self.need_snap = self.c_rd
        elif self.need_snap and not bus_snap and self.outstanding != 2 and self.c_rd and self.active:
            self.bus_snap, self.bus_addr = 1, self.snap_addr
            self.snap_addr += self.c_inc
            self.need_snap = int(self.outstanding == 0)
            self.outstanding += 1

        # the SCK side, for the interval up to the next clk edge
        cs, ev = 1, ()
        if self.script is not None:
            try:
                cs, ev = next(self.script)
            except StopIteration:
                self.script = None
        self.cs = cs
        for e in ev:
            if e == "a":
                self.a_tog ^= 1
                self.wpar = 0
            elif e == "s":
                self.r_tog ^= 1
                self.wpar ^= 1
            elif e == "l":
                self.rx.append((self.chip.cycle, self.buf[self.wpar]))
            else:
                self.w_hold = e[1]
                self.w_tog ^= 1

    # -- scripts: at SCK = clk/div, one rise every div clocks
    @staticmethod
    def _rises(n, div, last=()):
        for i in range(n):
            for _ in range(div - 1):
                yield (0, ())
            yield (0, last if i == n - 1 else ())

    def read_fixed(self, addr, n, div=1, gap=4, pause=None):
        """One READ_FIXED of n words. pause(): clocks to hold SCK before a word."""
        self.a_hold, self.m_hold = addr, (1, 0)
        yield (0, ())                        # CS_n low to first rise: 1 clk
        yield from self._rises(8, div)       # command
        yield from self._rises(4, div, ("a",))
        yield from self._rises(8, div, ("l",))  # dummy; the first word loads on the last fall
        for i in range(n):
            for _ in range(pause() if pause else 0):
                yield (0, ())
            yield from self._rises(8, div, ("s", "l") if i < n - 1 else ("s",))
        yield (0, ())                        # last fall to CS_n high: 1 clk
        for _ in range(gap):
            yield (1, ())

    def write_fixed(self, addr, words, div=1, gap=4, pause=None):
        self.a_hold, self.m_hold = addr, (0, 0)
        yield (0, ())
        yield from self._rises(8, div)
        yield from self._rises(4, div, ("a",))
        for w in words:
            for _ in range(pause() if pause else 0):
                yield (0, ())
            yield from self._rises(8, div, (("w", w),))
        yield (0, ())
        for _ in range(gap):
            yield (1, ())


def fifo_addr(core):
    return sim.core_reg(core, sim.C_FIFO)


def drain(port, core=0, burst=4096, div=1, gap=4, pause=None):
    """Read core's FIFO for ever in bursts of `burst` words."""
    while True:
        yield from port.read_fixed(fifo_addr(core), burst, div=div, gap=gap, pause=pause)


# ---------------------------------------------------------------- bench


ROOT = pathlib.Path(__file__).resolve().parents[2]


def firmware(name):
    return asm.assemble((ROOT / "firmware" / name).read_text())


class Bench:
    """The tapeout configuration with RMII pins on both sides and the port model."""

    def __init__(self, fifo_depth=8, late=False):
        self.chip = sim.Chip(bank_words=256, ncores=2, nbanks=2, fifo_depth=fifo_depth)
        self.port = QuadPort(self.chip, late=late)
        self.phy_rx = PhyRx()
        self.phy_tx = PhyTx()
        self.t0 = None
        self.pad_map = rx_pads
        self.trace = None              # a list: (t, uo pins after the edge at which pads[t] was taken)

    def write(self, addr, data):
        self.chip.step(we=True, addr=addr, wdata=data)

    def apply(self, writes):
        for a, d in writes:
            self.write(a, d)

    def load_rx(self, prog=None, core=0):
        self.apply(host.load_program(core, prog or firmware("rmii_rx.pasm")))
        self.write(sim.A_SYNCBYP, SYNC_BYPASS)

    def load_tx(self, prog=None, core=1):
        self.apply(host.load_program(core, prog or firmware("rmii_tx.pasm")))
        self.apply(host.pin_owner_writes({TX_PINS + i: core for i in range(3)}))

    def start(self, mask):
        self.write(sim.A_CTRL, mask | (mask << 8))
        self.chip.step()
        self.chip.step()

    def run(self, n):
        """n clocks. The PHY launches on the edge before the chip samples: the
        value given to chip.step() at edge t is the one it launched at edge t - 1."""
        if self.t0 is None:
            self.t0 = self.chip.cycle
        for _ in range(n):
            t = self.chip.cycle - self.t0
            self.phy_tx.sample(self.chip.outputs()[0])
            self.port.clock(0, self.pad_map(*self.phy_rx.at(t)))
            if self.trace is not None:
                self.trace.append((t, self.chip.outputs()[0]))

    def run_all(self, tail=200):
        self.run(len(self.phy_rx.pins) + tail)

    def words(self):
        return [w for _, w in self.port.rx]

    def flags(self):
        return self.chip.flags


# ---------------------------------------------------------------- pad timing
# RMII, LAN8720A datasheet rev 1.4 (SMSC, 08-23-12), tables 5.10 and 5.9, ns, for a
# system load of 10 to 25 pF. tov = output valid after REF_CLK rise (max), toh =
# output hold after REF_CLK rise (min), tsu/th = TXD, TX_EN at the PHY.
LAN8720A = {"ref_clk_in": dict(tov=14.0, toh=3.0, tsu=4.0, th=1.5),
            "ref_clk_out": dict(tov=5.0, toh=1.4, tsu=7.0, th=2.0)}

# The macro's own paths, ns, measured by OpenSTA on the signed-off netlist and SPEF of
# release candidate fo6 (clk and data at the project's boundary; output load 6 fF, not
# a pad; derates in the SDC included). Nothing outside the macro is in these numbers:
# no TT harness, no pad, no board.
#   clk_in = clk port to a uio synchroniser flop's clock pin, in_ = uio_in[0..4] port to
#   its D, setup/hold = that flop, out = clk port to uo_out[0..2] through pin_out[8..10]
#   (the TX pins' own path; the worst to uo_out over all registers is up to 1.1 ns later).
STA_FO6 = {"fast": dict(clk_in=(0.52, 0.60), in_=(0.07, 0.10), setup=0.09, hold=-0.03, out=(1.57, 2.49)),
           "typ": dict(clk_in=(0.75, 0.86), in_=(0.10, 0.15), setup=0.15, hold=-0.05, out=(2.31, 3.72)),
           "slow": dict(clk_in=(1.14, 1.31), in_=(0.16, 0.24), setup=0.26, hold=-0.08, out=(3.63, 5.85))}


def rx_slack(phy, chip, skew=0.0):
    """(setup, hold) slack in ns at the first synchroniser flop. skew: REF_CLK reaches
    the chip's clock port this much later than the PHY's clock pin; data flight 0."""
    setup = 20 - phy["tov"] + chip["clk_in"][0] + skew - chip["in_"][1] - chip["setup"]
    hold = phy["toh"] + chip["in_"][0] - chip["clk_in"][1] - skew - chip["hold"]
    return setup, hold


def tx_slack(phy, chip, skew=0.0):
    """(setup, hold) slack in ns at the PHY for one launch edge, same skew."""
    setup = 20 - phy["tsu"] - skew - chip["out"][1]
    hold = chip["out"][0] + skew - phy["th"]
    return setup, hold
