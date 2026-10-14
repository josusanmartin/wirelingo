"""ISO 7816-3 T=0 reader (firmware/iso7816.pasm on core 0, the card clock
firmware/iso7816_clk.pasm on core 1, post-freeze) on the cycle model.

Judges: an independent card model (pe.postfreeze3.IsoCard) that times
everything by counting the CLK it is given (one etu = 372 of its periods),
answers to reset, runs a small T=0 application, checks the parity of what
it receives, signals errors and repeats what the reader rejects; the
pins-only reading of the I/O line against the CLK edges; sigrok's uart
decoder (even parity) on I/O.

The chip runs the clock core at CLKDIV 1 (CLK = f_clk / 2) and the reader at
CLKDIV 6: one etu = 744 clocks, tick for tick the 50 MHz configuration
(clock core 6, reader 36); test_iso7816_50mhz runs that. WT is 32 etu here
(a real reader: 9600), so that the end of an answer is found quickly."""
import pathlib

import pytest

from pe import asm, host, postfreeze as pf, postfreeze3 as p3, sigrok, sim
from pe.modelhost import ModelHost

FW = pathlib.Path(__file__).resolve().parents[2] / "firmware"
IO, RST, CLK = 0, 8, 9           # uio0 (open drain), uo0, uo1
FINV, FOV = 5, 7
WT = 32
ETU_CLK = 744                    # chip clocks per etu at clock-core CLKDIV 1
ATR = bytes.fromhex("3BB4110000574C474F")
ATR_INV = bytes.fromhex("3FB4110000574C474F")


class Rig:
    """iso7816 on core 0, iso7816_clk on core 1, the card on I/O (wired-AND
    with the reader's open-drain drive, pulled up), RST and CLK."""

    def __init__(self, card, d=1, wt=WT, rmax=4, start=True):
        self.mh = mh = ModelHost()
        defs = p3.iso_defines(IO, RST, wt=wt, rmax=rmax, finv=FINV, fov=FOV)
        prog = asm.assemble((FW / "iso7816.pasm").read_text(), defs)
        clk = asm.assemble((FW / "iso7816_clk.pasm").read_text(), {"CLK": CLK})
        assert len(prog.words) <= 256 and len(clk.words) <= 256
        d0, d1 = p3.iso_clkdivs(d)
        mh.apply(host.load_program(0, prog, clkdiv=d0))
        mh.apply(host.load_program(1, clk, clkdiv=d1))
        mh.write(sim.core_reg(0, sim.C_CRCCTRL), p3.ISO_CRCCTRL)
        mh.apply(host.pin_owner_writes({CLK: 1}))
        mh.write(sim.A_POUT, 1 << IO)          # I/O released, RST low, CLK low
        mh.write(sim.A_OD, 1 << IO)
        mh.pull_up = 1 << IO
        self.card = card
        self.drive = 1
        self.io, self.rst, self.clk, self.rd = [(0, 1)], [(0, 0)], [], [(0, 1)]
        self.words = []
        self.etu_clk = ETU_CLK * d
        # the card sees every clock cycle, the host's bus cycles (FIFO pushes
        # and pops) included: observe from inside the chip's step
        orig = mh.chip.step

        def step(*a, **kw):
            self._observe()
            return orig(*a, **kw)
        mh.chip.step = step
        if start:
            mh.start(3)

    def _observe(self):
        mh, chip = self.mh, self.mh.chip
        c = chip.cycle
        p = chip.pin_out
        rst, clk = (p >> RST) & 1, (p >> CLK) & 1
        io = mh.bus_uio() & 1
        self.drive = self.card.step(c, clk, rst, io)
        mh.ext_oe = 0 if self.drive else 1 << IO
        rd = (p >> IO) & 1
        for log, v in ((self.io, io), (self.rst, rst), (self.rd, rd)):
            if v != log[-1][1]:
                log.append((c, v))
        if not self.clk or self.clk[-1][1] != clk:
            self.clk.append((c, clk))

    def run(self, n, until=None):
        mh, chip = self.mh, self.mh.chip
        for _ in range(int(n)):
            mh.step()
            if chip.cores[0].rxf:
                self.words += mh.pop_all(0)
                if until and until(self):
                    return

    def command(self, words, n_records=1, limit=None):
        """Push words, run until n_records more records with tag 2 or 3 (the
        end of a RECV or SEND) arrived; returns the new records."""
        k0 = len(p3.iso_records(self.words))
        for w in words:
            self.mh.push(0, w)

        def done(r):
            recs = p3.iso_records(r.words)[k0:]
            return sum(x[0] in ("recv", "send") for x in recs) >= n_records
        self.run(limit or 3000 * self.etu_clk, until=done)
        self.run(2 * self.etu_clk)
        return p3.iso_records(self.words)[k0:]

    def activate(self, ts=True):
        recs = self.command([p3.ISO_RESET] + p3.iso_recv(33, first_timeout_etu=112, ts=ts))
        return bytes(r[1] for r in recs if r[0] == "char"), recs[-1]

    def t0(self, header, data=None, le=None):
        """One T=0 command: the header, then data (case 3) or Le bytes (case
        2). Returns (procedure bytes and data parsed, every record)."""
        header = bytes(header)
        if data is None:
            recs = self.command(p3.iso_send(header) + p3.iso_recv((le or 0) + 3 + 4), n_records=2)
            chars = [r[1] for r in recs if r[0] == "char"]
            return p3.t0_parse(chars, header[1]), recs
        recs = self.command(p3.iso_send(header) + p3.iso_recv(1), n_records=2)
        chars = [r[1] for r in recs if r[0] == "char"]
        while chars and chars[-1] == 0x60:
            recs += self.command(p3.iso_recv(1))
            chars = [r[1] for r in recs if r[0] == "char"]
        if chars[-1] != header[1]:
            recs += self.command(p3.iso_recv(1))
            return p3.t0_parse([r[1] for r in recs if r[0] == "char"], header[1]), recs
        recs += self.command(p3.iso_send(data) + p3.iso_recv(2), n_records=2)
        return p3.t0_parse([r[1] for r in recs if r[0] == "char"], header[1]), recs

    def char_starts(self, log):
        """Start edges of characters on a line log (falls separated by at
        least 11 etu), in clocks."""
        out = []
        for c, v in log:
            if v == 0 and (not out or c - out[-1] > 10.9 * self.etu_clk):
                out.append(c)
        return out


def card_clock_ok(rig):
    """The CLK the card got: periods of exactly 2 chip clocks (clock core at
    CLKDIV 1), 372 of them per etu."""
    rises = [c for c, v in rig.clk if v]
    per = {b - a for a, b in zip(rises, rises[1:])}
    return per == {rig.etu_clk // 372}


def test_iso7816_atr_direct():
    """Cold reset: RST low for 2 etu (744 CLK, >= 400), then high; the card
    answers after 5000 CLK; the reader detects the direct convention on TS
    and receives the whole answer to reset."""
    card = p3.IsoCard(ATR)
    rig = Rig(card)
    rig.run(1000)
    atr, end = rig.activate()
    print(f"\nATR {atr.hex()}, end {end}, the card's CLK ok {card_clock_ok(rig)}")
    assert atr == ATR and p3.atr_length(atr) == len(ATR)
    assert end[0] == "recv" and end[1] == len(ATR) and end[3] and not end[4] and not end[5]
    assert not (rig.mh.chip.flags >> FINV) & 1
    rst = [(c, v) for c, v in rig.rst]
    lo = rst[1][0] - rig.rst[0][0] if len(rst) > 1 else None
    rises = [c for c, v in rig.clk if v]
    clk_before = sum(1 for c in rises if c < rst[1][0])
    assert card_clock_ok(rig) and clk_before >= 400
    print(f"RST rose after {clk_before} CLK cycles ({lo} clocks from the start)")


def test_iso7816_atr_inverse():
    """The inverse convention (TS = 3F): detected, and the rest of the ATR
    and a T=0 exchange decoded in it."""
    card = p3.IsoCard(ATR_INV, inverse=True)
    rig = Rig(card)
    rig.run(1000)
    atr, end = rig.activate()
    assert atr == ATR_INV and end[5] and (rig.mh.chip.flags >> FINV) & 1
    (nulls, ack, data, sw), _ = rig.t0([0x00, 0xB0, 0x00, 0x10, 0x08], le=8)
    assert ack and data == bytes(range(16, 24)) and sw == b"\x90\x00"
    (nulls, ack, _, sw), _ = rig.t0([0x00, 0xD6, 0x00, 0x00, 0x03], data=b"\xa5\x0f\xf0")
    assert ack and sw == b"\x90\x00" and card.mem[:3] == b"\xa5\x0f\xf0"


def test_iso7816_bad_ts():
    """A first character that is neither 3B nor 3F: reported as a bad TS,
    and the reader stops receiving."""
    card = p3.IsoCard(bytes([0x3A]) + ATR[1:])
    rig = Rig(card)
    rig.run(1000)
    atr, end = rig.activate()
    assert end[4] and end[1] == 0 and atr == b""


def test_iso7816_t0_commands():
    """READ BINARY (case 2) and UPDATE BINARY (case 3) through the T=0
    procedure bytes, an unknown instruction (6D 00), NULL procedure bytes;
    the card's view of every command; the reader's characters 12 etu apart
    (with N = 0), the line idle >= 16 etu between characters in opposite
    directions."""
    card = p3.IsoCard(ATR)
    rig = Rig(card)
    rig.run(1000)
    rig.activate()
    (nulls, ack, data, sw), _ = rig.t0([0x00, 0xB0, 0x00, 0x20, 0x10], le=16)
    assert ack and data == bytes(range(0x20, 0x30)) and sw == b"\x90\x00"
    (nulls, ack, _, sw), _ = rig.t0([0x00, 0xD6, 0x00, 0x40, 0x05], data=b"HELLO")
    assert ack and sw == b"\x90\x00" and card.mem[0x40:0x45] == b"HELLO"
    (nulls, ack, _, sw), _ = rig.t0([0x00, 0x12, 0x00, 0x00, 0x00])
    assert not ack and sw == b"\x6d\x00"
    card.nulls = 2
    (nulls, ack, data, sw), _ = rig.t0([0x00, 0xB0, 0x00, 0x40, 0x05], le=5)
    assert nulls == 2 and ack and data == b"HELLO" and sw == b"\x90\x00"
    assert [c[0].hex() for c in card.commands] == ["00b0002010", "00d6004005", "0012000000", "00b0004005"]
    # timing, on the card's clock: the reader's characters inside one command
    rd = rig.char_starts(rig.rd)
    gaps = [(b - a) / rig.etu_clk for a, b in zip(rd, rd[1:])]
    print(f"\nreader character spacing (etu): min {min(gaps):.3f}")
    assert min(gaps) == 12.0
    # opposite directions: the card's last character -> our next one
    sent = [n0 for n0, _, _ in card.sent]
    got = [n0 for n0, _, _, _ in card.received]
    turn = [min(r - s for s in sent if s < r) for r in got if any(s < r for s in sent)]
    print(f"card -> reader turnaround >= {min(turn) / 372:.2f} etu")
    assert min(turn) >= 16 * 372


@pytest.mark.parametrize("n_extra", [1, 5])
def test_iso7816_extra_guard(n_extra):
    """Extra guard time N (from the ATR's TC1): the reader's characters
    12 + N etu apart."""
    card = p3.IsoCard(ATR)
    rig = Rig(card)
    rig.run(1000)
    rig.activate()
    rig.command(p3.iso_send(bytes([0x00, 0xB0, 0x00, 0x00, 0x02]), extra_guard=n_extra) + p3.iso_recv(5))
    rd = rig.char_starts(rig.rd)[-5:]
    gaps = {(b - a) / rig.etu_clk for a, b in zip(rd, rd[1:])}
    assert gaps == {12.0 + n_extra}, gaps


def test_iso7816_rx_error_signal():
    """The card sends its 3rd answer character with a wrong parity bit twice:
    the reader signals an error each time (I/O low 10.5 etu after the start
    for 1.5 etu, as the card measured on its clock), the card repeats, and
    the record says 2 error signals and the right byte."""
    card = p3.IsoCard(ATR, corrupt={len(ATR) + 2: 2})
    rig = Rig(card)
    rig.run(1000)
    rig.activate()
    (nulls, ack, data, sw), recs = rig.t0([0x00, 0xB0, 0x00, 0x00, 0x04], le=4)
    assert ack and data == bytes(range(4)) and sw == b"\x90\x00"
    errs = [r[2] for r in recs if r[0] == "char"]
    print(f"\nerror signals per character {errs}; the card saw errors at {[(e - s) / 372 for s, e in card.err_seen]} etu")
    assert errs == [0, 0, 2, 0, 0, 0, 0] and len(card.err_seen) == 2
    # our error signal on the line: 10.5 etu (+-0.2) after the start of the
    # card's character (on the card's CLK), 1.5 etu long
    rises = [c for c, v in rig.clk if v]
    for s, _ in card.err_seen:
        c0 = rises[s - 1]                              # the CLK edge the card started its character on
        a, b = next((a, b) for (a, va), (b, vb) in zip(rig.rd, rig.rd[1:]) if va == 0 and a > c0 + 10 * rig.etu_clk)
        print(f"  error signal from {(a - c0) / rig.etu_clk:.3f} etu, {(b - a) / rig.etu_clk:.3f} etu long")
        assert abs((a - c0) / rig.etu_clk - 10.5) <= 0.2 and (b - a) == 1.5 * rig.etu_clk


def test_iso7816_rx_give_up():
    """A character corrupted more often than the reader signals (RMAX = 4):
    after 4 error signals the card stops repeating (its own limit, 4) ...
    and the reader keeps the 5th copy, flagged."""
    card = p3.IsoCard(ATR, corrupt={len(ATR) + 1: 9})
    rig = Rig(card)
    rig.run(1000)
    rig.activate()
    (_, ack, data, sw), recs = rig.t0([0x00, 0xB0, 0x00, 0x00, 0x02], le=2)
    ch = [r for r in recs if r[0] == "char"]
    print(f"\n{ch}")
    assert ch[1][2] == 4 and ch[1][3] and ch[1][1] == 0x00
    assert data == b"\x00\x01" and sw == b"\x90\x00"


def test_iso7816_tx_repetition():
    """The card rejects our 3rd header byte once and our 2nd data byte
    twice: the reader repeats each 2 etu after the card's error signal ends
    (the card holds it 1.5 etu: 14.05 etu after the previous start; ISO
    7816-3 asks >= 2 etu after the error is detected, >= 13 etu), the card
    gets the right bytes, and the SEND records count the repetitions."""
    card = p3.IsoCard(ATR, reject={2: 1, 6: 2})
    rig = Rig(card)
    rig.run(1000)
    rig.activate()
    (_, ack, _, sw), recs = rig.t0([0x00, 0xD6, 0x00, 0x00, 0x03], data=b"\x11\x22\x33")
    sends = [r for r in recs if r[0] == "send"]
    print(f"\n{sends}; the card got {card.commands}")
    assert ack and sw == b"\x90\x00" and card.mem[:3] == b"\x11\x22\x33"
    assert [s[2] for s in sends] == [1, 2] and not any(s[3] for s in sends)
    rd = rig.char_starts(rig.rd)
    gaps = [(b - a) / rig.etu_clk for a, b in zip(rd, rd[1:])]
    reps = [g for g in gaps if 12.5 < g < 15]
    print(f"repetitions {[round(g, 3) for g in reps]} etu after the previous start")
    assert len(reps) == 3 and all(14.0 <= g <= 14.1 for g in reps) and min(gaps) == 12.0


def test_iso7816_tx_abort():
    """The card rejects a byte 6 times: the reader sends it 5 times (RMAX = 4
    repetitions), then aborts the command and says so."""
    card = p3.IsoCard(ATR, reject={1: 6})
    rig = Rig(card)
    rig.run(1000)
    rig.activate()
    recs = rig.command(p3.iso_send(bytes([0x00, 0xB0, 0x00, 0x00, 0x02])))
    s = recs[-1]
    print(f"\n{s}")
    assert s[0] == "send" and s[3] and s[1] == 4 and s[2] == 5
    assert [b for _, b, _, bad in card.received if bad] == [0xB0] * 5


def test_iso7816_mute_card():
    """No answer to reset: the reader's first-character timeout ends the
    RECV (timeout, 0 characters)."""
    card = p3.IsoCard(b"")
    rig = Rig(card)
    rig.run(1000)
    atr, end = rig.activate()
    assert atr == b"" and end[3] and end[1] == 0


def test_iso7816_deactivate():
    """DEACT: RST low and I/O low (the host then stops CLK and VCC)."""
    card = p3.IsoCard(ATR)
    rig = Rig(card)
    rig.run(1000)
    rig.activate()
    rig.mh.push(0, p3.ISO_DEACT)
    rig.run(200)
    assert rig.rst[-1][1] == 0 and rig.io[-1][1] == 0


def test_iso7816_50mhz():
    """At 50 MHz (clock core CLKDIV 6, reader 36): CLK 4.1667 MHz, every
    edge of a SEND on the 4464-clock etu grid."""
    card = p3.IsoCard(ATR, atr_delay=500)
    rig = Rig(card, d=6)
    rig.run(1000)
    rig.activate()
    n0 = len(rig.rd)
    rig.command(p3.iso_send(bytes([0x00, 0xB0, 0x00, 0x00, 0x01])) + p3.iso_recv(4))
    edges = rig.rd[n0:]
    starts = rig.char_starts(edges)
    err = set()
    for c, v in edges:
        s = max(x for x in starts if x <= c)
        err.add((c - s) % 4464)
    print(f"\nat 50 MHz: {len(starts)} characters, edges off the etu grid by {sorted(err)} clocks")
    assert err == {0} and card.commands[-1][2][-2:] == b"\x90\x00"


@pytest.mark.skipif(sigrok.cli() is None, reason=sigrok.INSTALL_HINT)
def test_sigrok_iso7816():
    """sigrok's uart decoder on I/O (direct convention: 8 data bits LSB
    first, even parity, at f_CLK / 372 baud): the ATR and a READ BINARY both
    ways; a character the reader rejected shows as a frame error (its error
    signal holds the guard time low)."""
    card = p3.IsoCard(ATR, corrupt={len(ATR) + 1: 1})
    rig = Rig(card)
    rig.run(1000)
    rig.activate()
    rig.t0([0x00, 0xB0, 0x00, 0x00, 0x03], le=3)
    period = 20.0                                     # ns per chip clock (any: the baud rate scales)
    baud = 1e9 / (rig.etu_clk * period)
    tr = sigrok.Trace()
    pf.to_trace(tr, [(c * period, v) for c, v in rig.io], "io", rig.mh.chip.cycle * period)
    data, warn = sigrok.uart_parity(tr, "io", round(baud), res_ps=100_000)
    got = bytes(b for _, b in data)
    print(f"\nsigrok uart: {got.hex()} {warn}")
    # the header, INS, the rejected 00, its repetition, 01 02, 90 00
    assert got == ATR + bytes.fromhex("00b0000003" "b0" "00" "00" "0102" "9000")
    assert [w for _, w in warn] == ["Frame error"]
