"""USB FULL-SPEED device on the model (docs/usb-full-speed.md): firmware
usb_fs_dev on core 0 with the line unit at 4 clocks per bit (48 MHz), against
the pins-only full-speed host in pe.usbfs (edges in continuous time, with
jitter and frequency offset; its own clock recovery; turnaround measured on
the bus). The firmware needs no RTL change (the g4 line unit and ISA); the
v2 line unit's half-cycle NRZI sample point (docs/isa.md §4.7) adds jitter
margin (test_fs_rx_duty_cycle_margin).

Run with -s to see the measured numbers."""
import pathlib
import random

import pytest

from pe import asm, host, sim, usb, usbdev
from pe.modelhost import ModelHost
from pe.usbfs import FsHost, SpiService

FW = pathlib.Path(__file__).resolve().parents[2] / "firmware"
LINECFG = 0x49F3                 # as low speed: EN NRZI STUFF_K=6 ONES DIFF END=SE0 END_BITS=2 RX_EN
FIFO0, STATUS0 = sim.core_reg(0, sim.C_FIFO), sim.core_reg(0, sim.C_STATUS)
F_UNDERRUN = 1 << usbdev.FLAGS["F_IN"]
TA_MIN, TA_MAX = 2.0, 6.5        # USB 2.0 §7.1.18.1, bit times (detachable cable)


def device(clk_hz=48e6):
    """(ModelHost, program): usb_fs_dev running, bus idle at J.
    Wiring: D- = uio0 (line TXP/RXP), D+ = uio1, 1.5k pull-up on D+."""
    mh = ModelHost()
    prog = asm.assemble((FW / "usb_fs_dev.pasm").read_text(), usbdev.defines())
    mh.apply(host.load_program(0, prog, 0, clkdiv=1 << 8))
    mh.write(sim.core_reg(0, sim.C_CRCCTRL), usbdev.CRCCTRL)
    if clk_hz == 48e6:
        mh.write(sim.A_LINEDIV, (4 << 16) | (4 << 8))
    else:                         # RX period is whole clocks; TX gets the fraction
        div = clk_hz / 12e6
        mh.write(sim.A_LINEDIV, (round(div) << 16) | (int(div) << 8) | round((div % 1) * 256))
    mh.write(sim.A_LINEPINS, 0)
    mh.write(sim.A_LINECFG, LINECFG)
    mh.pull_up = 0b10
    mh.start(1)
    mh.step(50)
    return mh, prog


def stage_words(pid, payload):
    """TX FIFO words for a packet of any length: bit count, then SYNC, PID,
    payload and CRC16 bytes, little-endian (pe.usbdev.stage_words without the
    low-speed 8-byte limit)."""
    pkt = bytes([0x80]) + usb.data_packet(pid, payload)
    return [8 * len(pkt)] + [int.from_bytes(pkt[i:i + 4].ljust(4, b"\0"), "little")
                             for i in range(0, len(pkt), 4)]


def records(mh):
    recs, rest = usbdev.parse_records(mh.pop_all(0))
    assert not rest
    return recs


def bits(h, what):
    return [ns / h.tbit for w, ns in h.turnarounds if w == what]


def test_fs_control_transaction():
    """SETUP + DATA0 (CRC16 checked) -> ACK in time; IN -> NAK; IN -> the
    pre-staged DATA1; the host's ACK -> F_GOTACK."""
    mh, _ = device()
    h = FsHost(mh, seed=3)
    req = bytes([0x80, 6, 0, 1, 0, 0, 0x40, 0])
    assert h.setup(0, req)
    mh.step(100)
    assert records(mh) == [usbdev.Record(True, req, 16 + 8 * 10, setup=True)]
    h.send(usb.token(usb.PID_IN, 0, 0))
    t = h.last_eop_j
    assert h.listen(t, "NAK") == usb.handshake(usb.PID_NAK)
    desc = usbdev.DEVICE_DESC[:8]
    for w in stage_words(usb.PID_DATA1, desc):
        mh.push(0, w)
    mh.write(sim.A_FSET, usbdev.F_STAGED)
    assert h.data_in(0, 0) == (usb.pid_byte(usb.PID_DATA1), desc)
    h.wait_bits(20)
    assert mh.chip.flags & usbdev.F_GOTACK and not mh.chip.flags & usbdev.F_STAGED
    assert h.collisions == 0
    for what in ("SETUP data -> ACK", "NAK", "IN -> DATA/NAK"):
        b = bits(h, what)
        print(f"turnaround {what}: {b[0] * 4:.1f} clocks = {b[0]:.2f} bit times "
              f"(spec {TA_MIN}-{TA_MAX}; margin {TA_MAX - b[0]:.2f} bits = {(TA_MAX - b[0]) * 4:.1f} clocks)")
        assert TA_MIN <= b[0] <= TA_MAX


@pytest.mark.parametrize("jitter,ppm", [(0, 0), (9.25, 500), (9.25, -500), (9.25, 2500), (9.25, -2500)])
def test_fs_rx_jitter_and_rate(jitter, ppm):
    """OUT + DATA packets of 0..24 bytes (lots of stuffing among them) with
    each host edge moved by up to +-jitter ns, i.e. +-2*jitter between
    consecutive edges (USB 2.0 TJR1: +-18.5 ns), at a host bit-rate offset of
    ppm (hosts: +-500, devices: +-2500). All must be ACKed with exact records."""
    mh, _ = device()
    h = FsHost(mh, ppm=ppm, jitter_ns=jitter, seed=int(jitter * 10) + ppm)
    rng = random.Random(ppm)
    pid = usb.PID_DATA1
    for i in range(40):
        n = rng.randrange(25)
        p = bytes(rng.choice((0xFF, 0x00, rng.randrange(256))) for _ in range(n))
        assert h.data_out(0, 0, pid, p, tries=1)
        mh.step(40)
        assert [(r.ok, r.data) for r in records(mh)] == [(True, p)]
        pid ^= usb.PID_DATA0 ^ usb.PID_DATA1
    b = bits(h, "OUT data -> ACK")
    print(f"jitter +-{jitter} ns/edge, {ppm:+} ppm: 40/40 ACKed, turnaround {min(b):.2f}-{max(b):.2f} bit times")
    assert TA_MIN <= min(b) and max(b) <= TA_MAX and h.collisions == 0


@pytest.mark.parametrize("ppm", [0, 2500, -2500])
def test_fs_rx_duty_cycle_margin(ppm):
    """Every J->K edge 14 ns late and every K->J edge 14 ns early: consecutive
    edges 28 ns closer than nominal (USB allows 18.5). The half-cycle NRZI
    sample point (docs/isa.md §4.7) samples 1.5-2.5 clocks after an edge, so
    all 40 packets must be ACKed; g4's receiver (2-3 clocks) lost about half."""
    mh, _ = device()
    h = FsHost(mh, ppm=ppm, dcd_ns=14.0, seed=17 + ppm)
    rng = random.Random(ppm + 1)
    pid = usb.PID_DATA1
    for i in range(40):
        p = bytes(rng.choice((0xFF, 0x00, 0x55, rng.randrange(256))) for _ in range(rng.randrange(1, 17)))
        assert h.data_out(0, 0, pid, p, tries=1), i
        mh.step(40)
        assert [(r.ok, r.data) for r in records(mh)] == [(True, p)]
        pid ^= usb.PID_DATA0 ^ usb.PID_DATA1
    print(f"DCD 14 ns (28 ns edge to edge), {ppm:+} ppm: 40/40 ACKed")


@pytest.mark.parametrize("gap", [2.0, 6.5])
def test_fs_back_to_back(gap):
    """Every gap the host leaves (its own packets, its ACK after our DATA, the
    next transaction after our handshake) at the spec minimum of 2 bit times
    and at 6.5, with SOF tokens in between: nothing is missed."""
    mh, _ = device()
    h = FsHost(mh, ipd_bits=gap, ack_bits=gap, seed=5, jitter_ns=5)
    req = bytes([0x80, 6, 0, 1, 0, 0, 0x40, 0])
    assert h.setup(0, req)
    h.sof(0x123)
    h.send(usb.token(usb.PID_IN, 0, 0))
    assert h.listen(h.last_eop_j) == usb.handshake(usb.PID_NAK)
    mh.pop_all(0)
    for w in stage_words(usb.PID_DATA1, b"\x01\x02\x03"):
        mh.push(0, w)
    mh.write(sim.A_FSET, usbdev.F_STAGED)
    h.sof(0x124)
    assert h.data_in(0, 0, tries=1) == (usb.pid_byte(usb.PID_DATA1), b"\x01\x02\x03")
    assert h.data_out(0, 0, usb.PID_DATA1, b"xyz", tries=1)
    assert mh.chip.flags & usbdev.F_GOTACK
    mh.step(100)
    assert [r.data for r in records(mh)] == [b"xyz"]
    assert h.collisions == 0


def test_fs_ignores_bad_and_foreign_packets():
    """No handshake for a bad CRC16, a bad PID check or a missing stuffed bit;
    silence for other addresses and endpoints; SOF never answered."""
    mh, _ = device()
    h = FsHost(mh, seed=9)
    req = bytes([0xFF] * 8)
    pkt = usb.data_packet(usb.PID_DATA0, req)
    for bad in (pkt[:-1] + bytes([pkt[-1] ^ 1]), bytes([pkt[0] ^ 0x10]) + pkt[1:]):
        h.send(usb.token(usb.PID_SETUP, 0, 0))
        h.send(bad)
        assert h.listen(h.last_eop_j) is None
    states, level, ones, skipped = [], usb.J, 0, False    # a seven-1 run
    for b in usb.bits_lsb_first(bytes([0x80]) + pkt):
        if not b:
            level, ones = (usb.K if level == usb.J else usb.J), 0
        else:
            ones += 1
        states.append(level)
        if ones == 6:
            if skipped:
                level = usb.K if level == usb.J else usb.J
                states.append(level)
            skipped, ones = True, 0
    h.send(usb.token(usb.PID_SETUP, 0, 0))
    h.send(None, states=states + [usb.SE0, usb.SE0, usb.J])
    assert h.listen(h.last_eop_j) is None
    mh.step(100)
    assert not any(r.ok for r in records(mh))
    for addr, ep in ((1, 0), (0, 1), (0x7F, 0xF)):
        h.send(usb.token(usb.PID_IN, addr, ep))
        assert h.listen(h.last_eop_j) is None
    for frame in range(0, 2048, 97):
        h.sof(frame)
        assert h.listen(h.last_eop_j) is None
    assert h.setup(0, bytes(8))                 # and still works


def test_fs_out_flow_control():
    """Back-to-back 8-byte OUTs while the SPI host reads nothing: the first
    record (5 entries) fits, the second OUT finds no room for another and is
    NAKed (no stall, no late ACK); once the host drains the FIFO the retry is
    ACKed, and each payload is delivered once."""
    mh, _ = device()
    h = FsHost(mh, seed=13)
    a, b = b"12345678", b"abcdefgh"
    assert h.data_out(0, 0, usb.PID_DATA0, a, tries=1)
    with pytest.raises(TimeoutError):
        h.data_out(0, 0, usb.PID_DATA1, b, tries=3)
    assert [e[2] for e in h.log[-3:]] == [usb.handshake(usb.PID_NAK)] * 3
    first = records(mh)
    assert h.data_out(0, 0, usb.PID_DATA1, b, tries=1)
    mh.step(100)
    assert [r.data for r in first + records(mh)] == [a, b]
    assert TA_MIN <= min(bits(h, "OUT data -> ACK")) and max(bits(h, "OUT data -> ACK")) <= TA_MAX


def test_fs_enumeration():
    """Enumeration (device descriptor, SET_ADDRESS, configuration descriptor,
    SET_CONFIGURATION) with the low-speed reference device software unchanged:
    bMaxPacketSize0 = 8, every packet pre-staged, NAKs while it is busy."""
    mh, prog = device()
    sw = usbdev.DeviceSoftware(mh, prog, usbdev.DESCRIPTORS, 0, 0)

    def idle(m):
        m.step(300)
        sw.service(m)

    h = FsHost(mh, idle=idle, seed=11, jitter_ns=5)
    d = h.control_read(0, bytes([0x80, 6, 0, 1, 0, 0, 64, 0]), 64)
    assert d == usbdev.DEVICE_DESC
    h.control_write_nodata(0, bytes([0x00, 5, 5, 0, 0, 0, 0, 0]))
    c = h.control_read(5, bytes([0x80, 6, 0, 2, 0, 0, 34, 0]), 34)
    assert c == usbdev.CONFIG_DESC
    h.control_write_nodata(5, bytes([0x00, 9, 1, 0, 0, 0, 0, 0]))
    assert sw.address == 5 and h.collisions == 0
    ta = [ns / h.tbit for _, ns in h.turnarounds]
    print(f"enumerated in {mh.chip.cycle / 48e3:.2f} ms of bus time; {len(ta)} device responses, "
          f"turnaround {min(ta):.2f}-{max(ta):.2f} bit times")
    assert TA_MIN <= min(ta) and max(ta) <= TA_MAX


# ------------------------------------------------------------------ streaming and throughput
def feed(bus, rest):
    """Push the rest of a packet as the TX FIFO drains; give up on F_UNDERRUN."""
    while rest:
        room = 8 - ((yield from bus.read(STATUS0))[0] & 15)
        if room:
            yield from bus.write(FIFO0, rest[:room])
            rest = rest[room:]
        elif (yield from bus.read(sim.A_FSET))[0] & F_UNDERRUN:
            return


def in_stream(mh, h, payloads, sck=1, overhead=0):
    """The SPI host software stages each packet (the first 8 words, then the
    rest as the TX FIFO drains), waits for the host's ACK, and re-stages after
    F_UNDERRUN. Returns (kB/s over the transfer, underruns)."""
    st = {"underrun": 0}

    def program(bus):
        pid = usb.PID_DATA0
        for p in payloads:
            while True:
                w = stage_words(pid, p)
                yield from bus.write(sim.A_FCLR, [usbdev.F_GOTACK | F_UNDERRUN])
                yield from bus.write(FIFO0, w[:8])
                yield from bus.write(sim.A_FSET, [usbdev.F_STAGED])
                yield from feed(bus, w[8:])
                while True:
                    f = (yield from bus.read(sim.A_FSET))[0]
                    if f & (usbdev.F_GOTACK | F_UNDERRUN) and not f & usbdev.F_STAGED:
                        break
                if f & usbdev.F_GOTACK and not f & F_UNDERRUN:
                    break
                st["underrun"] += 1
                yield 400                                     # the cut-short packet ends
                yield from bus.write(sim.A_CTRL, [1 | (1 << 16)])   # clear core 0's FIFOs
            pid ^= usb.PID_DATA0 ^ usb.PID_DATA1

    h.service = SpiService(program, sck=sck, overhead=overhead)
    t0 = h.now()
    got = [h.data_in(0, 0)[1] for _ in payloads]
    assert got == payloads
    return sum(map(len, payloads)) / ((h.now() - t0) * 1e-9) / 1e3, st["underrun"]


def out_stream(mh, h, payloads, sck=1, overhead=0):
    """The SPI host software polls STATUS and drains the RX FIFO. Returns
    (kB/s, OUT tries the device did not ACK)."""
    words = []

    def program(bus):
        while True:
            n = ((yield from bus.read(STATUS0))[0] >> 4) & 15
            if n:
                words.extend((yield from bus.read(FIFO0, n)))

    h.service = SpiService(program, sck=sck, overhead=overhead)
    t0 = h.now()
    pid = usb.PID_DATA0
    for p in payloads:
        assert h.data_out(0, 0, pid, p)
        pid ^= usb.PID_DATA0 ^ usb.PID_DATA1
    rate = sum(map(len, payloads)) / ((h.now() - t0) * 1e-9) / 1e3
    h.wait_bits(300)
    recs, _ = usbdev.parse_records(words)
    assert [r.data for r in recs if r.ok] == payloads
    return rate, sum(1 for e in h.log if e[0] == "OUT" and e[2] != usb.handshake(usb.PID_ACK))


@pytest.mark.parametrize("size", [8, 16, 24, 64])
def test_fs_throughput(size):
    """Back-to-back transfers with the SPI host software running concurrently
    (quad SPI at SCK = clk/2, 1 us of software per transaction). Packets up to
    24 bytes fit the FIFOs; 64-byte ones are streamed through them."""
    payloads = [bytes((i * 7 + j) & 255 for j in range(size)) for i in range(6)]
    mh, _ = device()
    rin, under = in_stream(mh, FsHost(mh, seed=size), payloads, sck=2, overhead=48)
    mh, _ = device()
    rout, retry = out_stream(mh, FsHost(mh, seed=size), payloads, sck=2, overhead=48)
    print(f"{size:2d}-byte packets: IN {rin:.0f} kB/s ({under} underruns), "
          f"OUT {rout:.0f} kB/s ({retry} unacknowledged tries)")
    assert under == 0


def test_fs_in_underrun_recovers():
    """A 64-byte IN streamed by SPI host software too slow to keep the TX FIFO
    fed (20 us per transaction): the packet is cut short (the host sees a bad
    CRC and sends no ACK) and the firmware sets F_UNDERRUN. The software clears
    the FIFO and re-stages at full speed; the retried IN gets the whole packet."""
    mh, _ = device()
    h = FsHost(mh, seed=21)
    payloads = [bytes(range(64))]
    slow = {"n": 960}

    st = {"underrun": 0}

    def program(bus):
        w = stage_words(usb.PID_DATA0, payloads[0])
        while True:
            bus.overhead = slow["n"]
            yield from bus.write(sim.A_FCLR, [usbdev.F_GOTACK | F_UNDERRUN])
            yield from bus.write(FIFO0, w[:8])
            yield from bus.write(sim.A_FSET, [usbdev.F_STAGED])
            yield from feed(bus, w[8:])
            while True:
                f = (yield from bus.read(sim.A_FSET))[0]
                if f & (usbdev.F_GOTACK | F_UNDERRUN) and not f & usbdev.F_STAGED:
                    break
            if f & usbdev.F_GOTACK and not f & F_UNDERRUN:
                return
            st["underrun"] += 1
            slow["n"] = 0
            yield 400
            yield from bus.write(sim.A_CTRL, [1 | (1 << 16)])

    h.service = SpiService(program, sck=2)
    assert h.data_in(0, 0) == (usb.pid_byte(usb.PID_DATA0), payloads[0])
    assert st["underrun"] >= 1
    assert any(e[1] == "crc" for e in h.log)
    print("log:", [e[1] if isinstance(e[1], str) else hex(e[1]) for e in h.log])
