"""HDMI-CEC device (firmware/cec.pasm, post-freeze) on the cycle model.

Judges: pe.postfreeze3.cec_decode (pins only, the CEC receiver ranges), an
independent CEC device model on the same open-drain line
(pe.postfreeze3.CecNode: a follower that acknowledges its address and an
initiator with its own timing, arbitration and ACK checks), and sigrok's
cec decoder.

The chip runs at 200 kHz with CLKDIV 2 (1 tick = 10 us, tick for tick the
waveform of 50 MHz / CLKDIV 500; the line is sampled every 5 us);
test_cec_50mhz runs a frame at 50 MHz."""
import pathlib

import pytest

from pe import asm, host, postfreeze as pf, postfreeze3 as p3, sigrok, sim
from pe.modelhost import ModelHost
from pe.postfreeze2 import Player

FW = pathlib.Path(__file__).resolve().parents[2] / "firmware"
CEC = 0                 # uio0, open drain
OURS = 4                # our logical address (playback device 1)
FOV, FLOST = 7, 6
F_CLK = 200e3
BIT = p3.CEC_BIT


class Rig:
    """cec on core 0, the line = the chip's open-drain drive AND every
    node's drive (pulled up)."""

    def __init__(self, nodes=(), f_clk=F_CLK, addr=OURS, start=True, players=()):
        self.mh = mh = ModelHost()
        prog = asm.assemble((FW / "cec.pasm").read_text(), p3.cec_defines(CEC, FOV, FLOST))
        assert len(prog.words) <= 256
        mh.apply(host.load_program(0, prog, clkdiv=p3.cec_clkdiv(f_clk)))
        mh.write(sim.core_reg(0, sim.C_CRCCTRL), p3.CEC_CRCCTRL)
        mh.write(sim.core_reg(0, sim.C_CRC), addr)
        mh.write(sim.A_POUT, 1 << CEC)
        mh.write(sim.A_OD, 1 << CEC)
        mh.pull_up = 1 << CEC
        if start:
            mh.start(1)
        self.period = 1e9 / f_clk
        self.nodes = list(nodes)
        self.bus, self.chip_drive, self.words = [(0.0, 1)], [(0.0, 1)], []
        self.drives = [1] * len(self.nodes)
        self.players = list(players)
        self.hooks = []

    def now(self):
        return self.mh.chip.cycle * self.period

    def run(self, ns, until=None):
        mh, chip = self.mh, self.mh.chip
        for _ in range(int(ns / self.period)):
            t = chip.cycle * self.period
            ext = all(self.drives) and all(p.level(t) for p in self.players)
            mh.ext_oe = 0 if ext else 1 << CEC
            mh.uio = 0
            bus = mh.bus_uio() & 1
            cd = (chip.pin_out >> CEC) & 1
            for i, n in enumerate(self.nodes):
                self.drives[i] = n.step(t, bus)
            for h in self.hooks:
                h(self, t, bus, cd)
            if bus != self.bus[-1][1]:
                self.bus.append((t, bus))
            if cd != self.chip_drive[-1][1]:
                self.chip_drive.append((t, cd))
            mh.step()
            if chip.cores[0].rxf:
                self.words += mh.pop_all(0)
                if until and until(self):
                    return

    def send(self, blocks, sft_bits=5, wait=None):
        for w in p3.cec_words(blocks, sft_bits):
            self.mh.push(0, w)
        n0 = len(p3.cec_records(self.words)[1])
        self.run(wait or 400e6,
                 until=lambda r: len(p3.cec_records(r.words)[1]) > n0)
        self.run(1e6)
        return p3.cec_records(self.words)[1][-1]

    def records(self):
        return p3.cec_records(self.words)


def test_cec_send_frames():
    """Our frames to a follower (the TV, address 0): a poll, a directed
    command, a broadcast and a 16-block frame: the follower decodes them
    and acknowledges, our results say ok; the line, decoded pins-only, has
    start bits of 3.7 / 4.5 ms and bits of exactly 0.6 / 1.5 / 2.4 ms on our
    drive (the follower's ACKs make some 1.5 ms lows on the line)."""
    tv = p3.CecNode(addr=0)
    rig = Rig([tv])
    rig.run(20e6)
    frames = [bytes([0x40]), bytes([0x40, 0x36]), bytes([0x4F, 0x82, 0x10, 0x00]),
              bytes([0x40, 0x47]) + b"Wirelingo dev"[:14].ljust(14, b".")]
    res = [rig.send(f) for f in frames]
    rig.run(20e6)
    print(f"\nresults {res}\nthe TV decoded {[(f['blocks'].hex(), f['ack'], f['error']) for f in tv.frames]}")
    assert all(r["ok"] and not (r["nack"] or r["lost"]) for r in res)
    assert [r["blocks"] for r in res] == [len(f) for f in frames]
    assert [f["blocks"] for f in tv.frames] == frames and not any(f["error"] for f in tv.frames)
    assert [f["ack"] for f in tv.frames][:2] == [[0], [0, 0]]           # directed: acknowledged
    assert tv.frames[2]["ack"] == [1, 1, 1, 1]                          # broadcast: nobody rejected
    dec = p3.cec_decode(rig.bus, rig.now())
    assert [f["blocks"] for f in dec] == frames and not any(f["error"] for f in dec)
    ours = p3.cec_decode(rig.chip_drive, rig.now())
    st = {(round(f["start_lo"] / 1e3), round(f["start"] / 1e3)) for f in ours}
    lows = {round(x / 1e3) for f in ours for x in f["lows"]}
    pers = {round(x / 1e3) for f in ours for x in f["periods"]}
    print(f"our drive: start bits {st} us, lows {sorted(lows)} us, periods {sorted(pers)} us")
    assert st == {(3700, 4500)} and lows == {600, 1500} and pers == {2400}
    # the next frame starts >= 7 bit periods after the end of our previous one
    # (the host asked for 5 here; the TV answered nothing; see test_cec_free_time)
    gaps = [b["t"] - (a["t"] + a["start"] + sum(a["periods"]) + BIT) for a, b in zip(dec, dec[1:])]
    assert min(gaps) >= 5 * BIT


def test_cec_nack_and_retry():
    """A poll to an address nobody has (11) is not acknowledged: the result
    says NACK. The host's retry (signal free time 3 bit periods) starts 3
    bit periods after the end of the first attempt."""
    rig = Rig()
    rig.run(20e6)
    r1 = rig.send(bytes([0x4B]))
    r2 = rig.send(bytes([0x4B]), sft_bits=3)
    assert r1["nack"] and not r1["ok"] and r2["nack"]
    dec = p3.cec_decode(rig.bus, rig.now())
    assert [f["blocks"] for f in dec] == [b"\x4b", b"\x4b"] and [f["ack"] for f in dec] == [[1], [1]]
    end1 = dec[0]["t"] + dec[0]["start"] + sum(dec[0]["periods"]) + BIT
    gap = dec[1]["t"] - end1
    print(f"\nretry {gap / 1e6:.3f} ms after the first attempt's end (3 bit periods: {3 * BIT / 1e6} ms)")
    assert 3 * BIT <= gap <= 3 * BIT + 0.4e6


def test_cec_free_time():
    """Our frame, queued while another initiator's frame is on the line,
    waits for it, then for the signal free time: 5 bit periods after a frame
    by someone else, and 7 when we send again after our own."""
    tv = p3.CecNode(addr=0)
    rig = Rig([tv])
    rig.run(10e6)
    tv.send(bytes([0x0F, 0x87, 0x00, 0x00, 0x01]))       # broadcast, 5 blocks
    rig.run(6e6)
    rig.send(bytes([0x40, 0x8F]), sft_bits=5)
    rig.send(bytes([0x40, 0x46]), sft_bits=7)
    rig.run(10e6)
    dec = p3.cec_decode(rig.bus, rig.now())
    assert [f["blocks"].hex() for f in dec] == ["0f87000001", "408f", "4046"]
    ends = [f["t"] + f["start"] + sum(f["periods"]) + BIT for f in dec]
    gaps = [b["t"] - e for e, b in zip(ends, dec[1:])]
    print(f"\ngaps {[round(g / 1e6, 3) for g in gaps]} ms (5 and 7 bit periods: 12.0, 16.8)")
    assert 5 * BIT <= gaps[0] <= 5 * BIT + 1.0e6
    assert 7 * BIT <= gaps[1] <= 7 * BIT + 0.4e6
    frames, tx, _ = rig.records()
    assert frames[0]["blocks"].hex() == "0f87000001" and all(t["ok"] for t in tx)


def test_cec_receive():
    """The TV sends: a frame to us (acknowledged by the firmware: the TV's
    result is ok), one to address 5 (nobody acknowledges: NACK, the TV
    stops after the header), a broadcast; all reported, with the ACK bits
    as they were on the line."""
    tv = p3.CecNode(addr=0)
    rig = Rig([tv])
    rig.run(10e6)
    for f in (bytes([0x04, 0x8F]), bytes([0x05, 0x8F]), bytes([0x0F, 0x36]), bytes([0x04, 0x44, 0x41])):
        tv.send(f)
        rig.run(20e6 + 25 * BIT * len(f))
    frames, tx, err = rig.records()
    print(f"\nreceived {[(f['blocks'].hex(), f['ack'], f['to_us']) for f in frames]}; the TV: {tv.results}")
    assert [f["blocks"].hex() for f in frames] == ["048f", "05", "0f36", "044441"]
    assert [f["ack"] for f in frames] == [[0, 0], [1], [1, 1], [0, 0, 0]]
    assert [f["to_us"] for f in frames] == [True, False, False, True]
    assert [r[2] for r in tv.results] == ["ok", "nack", "ok", "ok"] and not err and not tx


@pytest.mark.parametrize("timing", [
    dict(start_lo=3.5e6, start=4.3e6), dict(start_lo=3.9e6, start=4.7e6),
    dict(lo1=0.4e6, lo0=1.3e6), dict(lo1=0.8e6, lo0=1.7e6),
    dict(bit=2.05e6), dict(bit=2.75e6), dict(bit=2.75e6, lo1=0.8e6, lo0=1.7e6, start_lo=3.9e6, start=4.7e6),
    dict(bit=2.05e6, lo1=0.4e6, lo0=1.3e6, start_lo=3.5e6, start=4.3e6)])
def test_cec_receive_tolerance(timing):
    """Initiators at the ends of the receiver's ranges (start bit low
    3.5..3.9 ms, period 4.3..4.7; a 1 low 0.4..0.8, a 0 1.3..1.7; bits
    2.05..2.75 ms apart): every frame received and acknowledged."""
    tv = p3.CecNode(addr=0, **timing)
    rig = Rig([tv])
    rig.run(10e6)
    sent = [bytes([0x04, 0x00, 0xFF, 0x55, 0xAA]), bytes([0x0F, 0x84, 0x10, 0x00, 0x04])]
    for f in sent:
        tv.send(f)
        rig.run(20e6 + 30 * BIT * len(f))
    frames, _, err = rig.records()
    assert [f["blocks"] for f in frames] == sent and not err
    assert [r[2] for r in tv.results] == ["ok", "ok"] and frames[0]["ack"] == [0] * 5


def test_cec_errors():
    """Negative controls. A 3.0 ms "start bit" and a line held low for 5 ms
    are ignored; a bit 1.9 ms after the last one is an error ("short"); a
    frame that stops after 5 bits of a block is reported "partial"; a bit
    held low for 3.6 ms (a follower's error notification) is "low". Then a
    good frame."""
    pl = Player()
    rig = Rig(players=[pl])
    t = 10e6
    bad_start = [(0, 3.0e6), (1, 1.5e6)] + p3.cec_wave(b"\x04\x8f")[2:]
    short = p3.cec_wave(b"\x04\x8f", bit=lambda i: 1.9e6 if i == 5 else 2.4e6)
    part = p3.cec_wave(b"\x04", bits=p3.cec_wave_bits(b"\x04\x8f")[:15])
    lowbit = p3.cec_wave(b"\x04\x8f")
    k = 2 + 2 * 12
    lowbit[k] = (0, 3.6e6)
    lowbit[k + 1] = (1, 0.4e6)
    for w in [bad_start, [(0, 5e6)], short, part, lowbit, p3.cec_wave(b"\x04\x8f")]:
        t = pl.schedule(t, w) + 25e6
    rig.run(t)
    frames, tx, err = rig.records()
    print(f"\nerrors {err}; frames {[(f['blocks'].hex(), f['ack']) for f in frames]}")
    assert [e["reason"] for e in err] == ["short", "partial", "low"]
    assert [f["blocks"] for f in frames] == [b"\x04", b"\x04", b"\x04\x8f"]
    assert frames[-1]["ack"] == [0, 0] and not tx


def test_cec_arbitration_lost():
    """The TV (address 0) starts a frame to us at the moment we start ours
    (0x40 vs 0x04: we send a 1 where the TV sends a 0, bit 2 of the header):
    we stop driving, report the loss, receive the TV's frame and acknowledge
    it, and drop the 2 data words of our 10-block frame it had not pulled;
    the host's retry (3 bit periods) then goes through."""
    OSD = bytes([0x40, 0x47]) + b"Wireling"
    tv = p3.CecNode(addr=0)
    rig = Rig([tv])
    rig.run(10e6)
    state = {"armed": True}

    def start_tv(r, t, bus, cd):
        if state["armed"] and cd == 0:
            state["armed"] = False
            tv.send(bytes([0x04, 0x8F]), at=t)
    rig.hooks.append(start_tv)
    r1 = rig.send(OSD)
    rig.run(10e6)
    r2 = rig.send(OSD, sft_bits=3)
    rig.run(10e6)
    frames, tx, err = rig.records()
    print(f"\nours {tx}; received {[(f['blocks'].hex(), f['ack'], f['to_us']) for f in frames]}; TV {tv.results}"
          f"; TV received {[f['blocks'].hex() for f in tv.frames]}")
    assert tx[0]["lost"] and not tx[0]["ok"] and tx[1]["ok"]
    assert frames[0]["blocks"] == b"\x04\x8f" and frames[0]["to_us"] and frames[0]["ack"] == [0, 0]
    assert tv.results[0][2] == "ok"
    assert [f["blocks"] for f in tv.frames] == [b"\x04\x8f", OSD]
    dec = p3.cec_decode(rig.bus, rig.now())
    assert [f["blocks"] for f in dec] == [b"\x04\x8f", OSD] and not any(f["error"] for f in dec)
    assert r1 == tx[0] and r2 == tx[1]


def test_cec_arbitration_won():
    """The other way: a device at address 8 starts with us (0x80 vs 0x40:
    it sends a 1 where we send a 0, the first header bit) and stops; our
    frame goes through to the TV."""
    tv = p3.CecNode(addr=0)
    rec = p3.CecNode(addr=None)
    rig = Rig([tv, rec])
    rig.run(10e6)
    state = {"armed": True}

    def start_rec(r, t, bus, cd):
        if state["armed"] and cd == 0:
            state["armed"] = False
            rec.send(bytes([0x80, 0x36]), at=t)
    rig.hooks.append(start_rec)
    r = rig.send(bytes([0x40, 0x36]))
    rig.run(10e6)
    print(f"\nours {r}; the other initiator {rec.results}")
    assert r["ok"] and rec.results[0][2] == "lost"
    assert [f["blocks"] for f in tv.frames] == [b"\x40\x36"]


def test_cec_50mhz():
    """At 50 MHz (CLKDIV 500): our start bit and bits, clock for clock."""
    rig = Rig(f_clk=50e6)
    rig.mh.push(0, p3.cec_words(b"\x4f\x82", 0)[0])
    rig.mh.push(0, p3.cec_words(b"\x4f\x82", 0)[1])
    rig.run(4.5e6 + 20 * BIT + 1e6)
    [f] = p3.cec_decode(rig.chip_drive, rig.now())
    clk = sorted({round(x / 20) for x in f["lows"] + f["periods"] + [f["start_lo"], f["start"]]})
    print(f"\nCEC at 50 MHz: {f['blocks'].hex()}, intervals {clk} clocks")
    assert f["blocks"] == b"\x4f\x82" and clk == [30000, 75000, 120000, 185000, 225000]


@pytest.mark.skipif(sigrok.cli() is None, reason=sigrok.INSTALL_HINT)
def test_sigrok_cec():
    """sigrok's cec decoder on the line: our frames and the TV's, with the
    acknowledgements."""
    tv = p3.CecNode(addr=0)
    rig = Rig([tv])
    rig.run(10e6)
    rig.send(bytes([0x40, 0x04]))
    rig.send(bytes([0x4F, 0x82, 0x10, 0x00]), sft_bits=7)
    tv.send(bytes([0x04, 0x8F]))
    rig.run(20e6 + 25 * BIT * 2)
    rig.send(bytes([0x4B]), sft_bits=5)
    rig.run(10e6)
    tr = sigrok.Trace()
    pf.to_trace(tr, rig.bus, "cec", rig.now())
    got, warn = sigrok.cec(tr, "cec", res_ps=100_000)
    print(f"\nsigrok cec: {got} {warn}")
    assert [g[1] for g in got] == ["40:04", "4f:82:10:00", "04:8f", "4b"]
    assert [g[2].endswith("R: ACK") for g in got] == [True, True, True, False]
