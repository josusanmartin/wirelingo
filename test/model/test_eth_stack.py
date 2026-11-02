"""The 10BASE-T stack end to end on the cycle model, at pin level.

A far end frames ARP and ping the way Linux does, built here with struct and zlib
and sharing no code with pe.eth. It drives the RXP pad at 10 Mbit/s; the replies
are read off the TD+/TD- pads. The host is pe.ethhost over pe.ethsim.TimedPort,
so every SPI transaction takes the clocks it takes on the real port while the
chip runs. Run with -s to see the numbers docs/eth10-network.md quotes.

Oracles other than our own decoder: a second Manchester decoder that samples
cells, zlib for the FCS, and tcpdump and scapy over a pcap (pcap_judge.py),
each with a negative control.
"""
import ast
import importlib.util
import json
import os
import pathlib
import random
import shutil
import struct
import subprocess
import sys
import zlib

import pytest

from pe import asm, eth, ethhost, ethsim, host, sim
from pe.ethsim import Rig, manchester_decode, wire_bits
from pe.modelhost import ModelHost

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/micropython"))
import eth10_rx  # noqa: E402

CHIP_MAC, CHIP_IP = bytes.fromhex("02574c000002"), bytes([10, 77, 0, 2])
PEER_MAC, PEER_IP = bytes.fromhex("0200deadbe01"), bytes([10, 77, 0, 1])
BCAST = b"\xff" * 6
CLOCKS = [50e6, 40e6]
SHORT = 300        # a frame long enough for a rate to show, cheap enough to run many of


# ---------------------------------------------------------------- the far end
def cksum(b):
    b = bytes(b) + bytes(len(b) & 1)
    s = sum(struct.unpack(f">{len(b) // 2}H", b))
    while s >> 16:
        s = (s & 0xFFFF) + (s >> 16)
    return ~s & 0xFFFF


def fcs(body):
    return struct.pack("<I", zlib.crc32(body))


def l2(dst, src, kind, payload):
    """What a NIC puts on the wire: padded to 60, FCS appended."""
    body = dst + src + struct.pack(">H", kind) + payload
    body += bytes(max(0, 60 - len(body)))
    return body + fcs(body)


def peer_arp_request(target=CHIP_IP):
    return l2(BCAST, PEER_MAC, 0x0806, struct.pack(">HHBBH6s4s6s4s", 1, 0x0800, 6, 4, 1, PEER_MAC, PEER_IP,
                                                   bytes(6), target))


def ip_packet(proto, payload, src=PEER_IP, dst=CHIP_IP, ident=0x4242, flags=0x4000, ttl=64):
    h = struct.pack(">BBHHHBBH4s4s", 0x45, 0, 20 + len(payload), ident, flags, ttl, proto, 0, src, dst)
    return h[:10] + struct.pack(">H", cksum(h)) + h[12:] + payload


def ping_payload(n=56, seq=1):
    """As `ping`: 8 bytes of timestamp, then 0x10, 0x11, ..."""
    return struct.pack("<II", 0x6543210, seq)[:8][:n] + bytes((0x10 + i) & 255 for i in range(max(0, n - 8)))


def icmp_echo(ident, seq, data):
    h = struct.pack(">BBHHH", 8, 0, 0, ident, seq)
    return struct.pack(">BBHHH", 8, 0, cksum(h + data), ident, seq) + data


def peer_ping(seq=1, data=None, ident=0x1234, dst_mac=CHIP_MAC, dst_ip=CHIP_IP):
    data = ping_payload(seq=seq) if data is None else data
    return l2(dst_mac, PEER_MAC, 0x0800, ip_packet(1, icmp_echo(ident, seq, data), dst=dst_ip))


def udp_datagram(sport, dport, data, src=PEER_IP, dst=CHIP_IP):
    h = struct.pack(">HHHH", sport, dport, 8 + len(data), 0)
    c = cksum(src + dst + struct.pack(">BBH", 0, 17, 8 + len(data)) + h + data) or 0xFFFF
    return h[:6] + struct.pack(">H", c) + data


def peer_udp(data, dport=7):
    return l2(CHIP_MAC, PEER_MAC, 0x0800, ip_packet(17, udp_datagram(40000, dport, data)))


def test_frame(n, seed=1):
    """n bytes, FCS included, to nobody in particular."""
    body = BCAST + PEER_MAC + b"\x88\xb5" + bytes((seed * 37 + i * 11) & 255 for i in range(n - 18))
    return body + fcs(body)


test_frame.__test__ = False


def check_arp_reply(raw):
    assert zlib.crc32(raw[:-4]).to_bytes(4, "little") == raw[-4:]
    assert len(raw) == 64
    dst, src, kind = raw[:6], raw[6:12], raw[12:14]
    assert (dst, src, kind) == (PEER_MAC, CHIP_MAC, b"\x08\x06")
    htype, ptype, hl, pl, op, sha, spa, tha, tpa = struct.unpack(">HHBBH6s4s6s4s", raw[14:42])
    assert (htype, ptype, hl, pl, op) == (1, 0x0800, 6, 4, 2)
    assert (sha, spa, tha, tpa) == (CHIP_MAC, CHIP_IP, PEER_MAC, PEER_IP)


def check_echo_reply(raw, proto, payload_check):
    assert zlib.crc32(raw[:-4]).to_bytes(4, "little") == raw[-4:]
    assert len(raw) >= 64
    assert (raw[:6], raw[6:12], raw[12:14]) == (PEER_MAC, CHIP_MAC, b"\x08\x00")
    ihl = (raw[14] & 15) * 4
    assert raw[14] >> 4 == 4 and ihl == 20
    total = struct.unpack(">H", raw[16:18])[0]
    assert cksum(raw[14:14 + ihl]) == 0
    assert raw[23] == proto and raw[26:30] == CHIP_IP and raw[30:34] == PEER_IP
    payload_check(raw[14 + ihl:14 + total])


def check_ping_reply(raw, seq, data, ident=0x1234):
    def payload(p):
        assert cksum(p) == 0
        assert struct.unpack(">BBHHH", p[:8])[0:2] == (0, 0)
        assert struct.unpack(">HH", p[4:8]) == (ident, seq)
        assert p[8:] == data
    check_echo_reply(raw, 1, payload)


def sampled_decode(burst):
    """A second Manchester decoder, nothing like the edge tracker in pe.ethsim:
    level in the middle of each half cell, on a grid laid from the first edge,
    each cell must have two different levels."""
    edges = burst.edges

    def level(t):
        lv = 0
        for te, v in edges:
            if te > t:
                break
            lv = v
        return lv
    t0, bits = edges[0][0], []
    k = 0
    while True:
        a, b = level(t0 + k * 100 + 25), level(t0 + k * 100 + 75)
        if a == b or not a or not b:
            break
        bits.append(1 if b == 1 else 0)
        k += 1
    return bits


def wire_frames(wire):
    """Every frame on TD, decoded twice; the two decoders must agree."""
    out = []
    for b in wire.bursts:
        if b.pulse:
            continue
        bits, tp_idl = manchester_decode(b.edges)
        assert sampled_decode(b) == bits
        assert bits[:64] == [1, 0] * 31 + [1, 1], "preamble and SFD"
        assert len(bits) % 8 == 0
        assert 250 <= tp_idl <= 350, f"TP_IDL {tp_idl} ns"
        out.append(bytes(sum(x << k for k, x in enumerate(bits[i:i + 8])) for i in range(64, len(bits), 8)))
    assert wire.both_high == 0
    return out


def exchange(rig, frame, n=1, limit_us=2500):
    """Send one frame, wait for n more frames on TD, return the new ones."""
    start = len(wire_frames(rig.wire))
    rig.wire.send(frame)
    rig.run_until(lambda: len(rig.wire.frames()) >= start + n, limit_us)
    return wire_frames(rig.wire)[start:]


def assert_no_reply(rig, frame):
    before = sum(not b.pulse for b in rig.wire.bursts)
    rig.wire.send(frame)
    assert rig.run_until(lambda: not rig.wire.queue, 200)
    rig.run_us(ethsim.IFG_NS / 1000 + 2 * (len(frame) + 8) * 0.8 + 300)
    assert sum(not b.pulse for b in rig.wire.bursts) == before, "the chip answered"
    assert rig.wire.open is None or rig.wire.open.pulse


# ---------------------------------------------------------------- pe.eth alone
def test_mac_is_locally_administered_unicast():
    assert eth.locally_administered(CHIP_MAC) and not CHIP_MAC[0] & 1


def test_crc_matches_zlib_with_and_without_binascii(monkeypatch):
    rng = random.Random(1)
    data = [bytes(rng.getrandbits(8) for _ in range(n)) for n in (0, 1, 3, 60, 1514)]
    want = [zlib.crc32(d) for d in data]
    assert [eth.crc32(d) for d in data] == want
    monkeypatch.setattr(eth, "_crc32", None)
    monkeypatch.setattr(eth, "_table", None)
    assert [eth.crc32(d) for d in data] == want


def test_builders_match_the_independent_far_end():
    assert eth.arp_request(PEER_MAC, PEER_IP, CHIP_IP) == peer_arp_request()
    data = ping_payload()
    assert eth.frame(CHIP_MAC, PEER_MAC, eth.ETH_IP, eth.ipv4(PEER_IP, CHIP_IP, 1, eth.icmp_echo(8, 0x1234, 1, data),
                                                              ident=0x4242)) == peer_ping(1, data)
    assert eth.frame(CHIP_MAC, PEER_MAC, eth.ETH_IP, eth.ipv4(PEER_IP, CHIP_IP, 17, eth.udp(PEER_IP, CHIP_IP, 40000, 7, b"hello"),
                                                              ident=0x4242)) == peer_udp(b"hello")


def test_rfc1071_checksum_vector():
    # the example in RFC 1071 section 3: sum 0xDDF2 before the complement
    assert eth.checksum(bytes.fromhex("0001f203f4f5f6f7")) == (~0xddf2 & 0xFFFF)
    # odd length pads with a zero byte
    assert eth.checksum(b"\x01") == cksum(b"\x01\x00")


def test_udp_checksum_zero_is_sent_as_ffff():
    """RFC 768: a computed 0 goes out as 0xFFFF; search for a payload that computes to 0."""
    for i in range(65536):
        data = struct.pack(">H", i)
        p = eth.udp(PEER_IP, CHIP_IP, 7, 7, data)
        h = p[:6] + b"\0\0" + data
        if cksum(PEER_IP + CHIP_IP + struct.pack(">BBH", 0, 17, len(p)) + h) == 0:
            assert p[6:8] == b"\xff\xff"
            return
    raise AssertionError("no zero checksum found")


def station():
    return eth.Station(CHIP_MAC, CHIP_IP)


def test_station_answers():
    st = station()
    check_arp_reply(st.handle(peer_arp_request())[0])
    check_ping_reply(st.handle(peer_ping(7))[0], 7, ping_payload(seq=7))
    assert st.stats["arp"] == 1 and st.stats["icmp"] == 1
    r = st.handle(peer_udp(b"hello"))
    assert len(r) == 1

    def payload(p):
        assert struct.unpack(">HHH", p[:6]) == (7, 40000, 13) and p[8:] == b"hello"
        assert cksum(CHIP_IP + PEER_IP + struct.pack(">BBH", 0, 17, 13) + p) == 0
    check_echo_reply(r[0], 17, payload)
    # a minimum ping's ICMP payload is padded by the NIC: the reply must not echo the padding
    r = st.handle(peer_ping(9, b"x"))
    check_ping_reply(r[0], 9, b"x")
    # an ARP probe has sender address 0: answered to 0.0.0.0, at the sender's MAC
    probe = l2(BCAST, PEER_MAC, 0x0806, struct.pack(">HHBBH6s4s6s4s", 1, 0x0800, 6, 4, 1, PEER_MAC, bytes(4), bytes(6), CHIP_IP))
    r = st.handle(probe)[0]
    assert r[:6] == PEER_MAC and r[38:42] == bytes(4)
    # options in the IP header are skipped and not echoed
    opt = b"\x01\x01\x01\x00"
    ic = icmp_echo(1, 2, b"abc")
    h = struct.pack(">BBHHHBBH4s4s", 0x46, 0, 24 + len(ic), 1, 0, 64, 1, 0, PEER_IP, CHIP_IP) + opt
    h = h[:10] + struct.pack(">H", cksum(h)) + h[12:]
    r = st.handle(l2(CHIP_MAC, PEER_MAC, 0x0800, h + ic))
    check_ping_reply(r[0], 2, b"abc", ident=1)


BAD = {
    "bad_fcs": (lambda: peer_ping()[:-1] + bytes([peer_ping()[-1] ^ 1]), "bad_fcs"),
    "bit_in_payload": (lambda: (lambda f: f[:40] + bytes([f[40] ^ 4]) + f[41:])(peer_ping()), "bad_fcs"),
    "other_mac": (lambda: peer_ping(dst_mac=bytes.fromhex("02574c000003")), "other_dst"),
    "multicast_mac": (lambda: peer_ping(dst_mac=bytes.fromhex("01005e000001")), "other_dst"),
    "other_ip": (lambda: peer_ping(dst_ip=bytes([10, 77, 0, 3])), "other_ip"),
    "broadcast_mac_other_ip": (lambda: peer_ping(dst_mac=BCAST, dst_ip=bytes([10, 77, 0, 255])), "other_ip"),
    "arp_other_ip": (lambda: peer_arp_request(bytes([10, 77, 0, 3])), "arp_ignored"),
    "arp_reply": (lambda: l2(CHIP_MAC, PEER_MAC, 0x0806, struct.pack(">HHBBH6s4s6s4s", 1, 0x0800, 6, 4, 2, PEER_MAC, PEER_IP, CHIP_MAC, CHIP_IP)), "arp_ignored"),
    "arp_truncated": (lambda: l2(BCAST, PEER_MAC, 0x0806, b"\x00\x01\x08\x00"), "arp_ignored"),
    "bad_ip_checksum": (lambda: (lambda p: l2(CHIP_MAC, PEER_MAC, 0x0800, p[:10] + bytes([p[10] ^ 1]) + p[11:]))(ip_packet(1, icmp_echo(1, 1, b"a"))), "bad_ip"),
    "bad_icmp_checksum": (lambda: (lambda c: l2(CHIP_MAC, PEER_MAC, 0x0800, ip_packet(1, c[:2] + bytes([c[2] ^ 1]) + c[3:])))(icmp_echo(1, 1, b"a")), "icmp_ignored"),
    "icmp_reply": (lambda: l2(CHIP_MAC, PEER_MAC, 0x0800, ip_packet(1, b"\x00\x00\xff\xff\x00\x00\x00\x00")), "icmp_ignored"),
    "fragment": (lambda: l2(CHIP_MAC, PEER_MAC, 0x0800, ip_packet(1, icmp_echo(1, 1, b"a"), flags=0x2000)), "bad_ip"),
    "udp_wrong_port": (lambda: peer_udp(b"a", dport=9), "udp_ignored"),
    "udp_bad_checksum": (lambda: (lambda u: l2(CHIP_MAC, PEER_MAC, 0x0800, ip_packet(17, u[:6] + bytes([u[6] ^ 1]) + u[7:])))(udp_datagram(1, 7, b"abcd")), "udp_ignored"),
    "other_ethertype": (lambda: l2(CHIP_MAC, PEER_MAC, 0x86DD, bytes(46)), "other_type"),
    "group_source": (lambda: l2(CHIP_MAC, bytes.fromhex("010000000001"), 0x0800, ip_packet(1, icmp_echo(1, 1, b"a"))), "bad_src"),
    "other_protocol": (lambda: l2(CHIP_MAC, PEER_MAC, 0x0800, ip_packet(6, bytes(20))), "other_proto"),
    "runt": (lambda: test_frame(63), "bad_size"),
    "giant": (lambda: test_frame(1519), "bad_size"),
}


@pytest.mark.parametrize("name", sorted(BAD))
def test_station_rejects(name):
    make, why = BAD[name]
    st = station()
    assert st.handle(make()) == []
    assert st.stats[why] == 1


def test_station_never_raises_and_never_answers_garbage():
    """Random frames with a good FCS, and valid requests with random damage."""
    rng = random.Random(3)
    st = station()
    goods = [peer_arp_request(), peer_ping(), peer_udp(b"hello")]
    for i in range(3000):
        n = rng.randrange(0, 1600)
        body = bytearray(rng.getrandbits(8) for _ in range(n))
        if i % 3 == 0 and n > 14:                # aim at our MAC, with a real EtherType
            body[:6] = CHIP_MAC
            body[12:14] = rng.choice((b"\x08\x00", b"\x08\x06"))
        st.handle(bytes(body) + fcs(bytes(body)))
        f = bytearray(rng.choice(goods))
        f[rng.randrange(len(f))] ^= 1 << rng.randrange(8)      # one bit: the FCS must stop it
        assert st.handle(bytes(f)) == []


def records(raw, lost=0):
    bits = wire_bits(raw)[lost:]
    full, rem = divmod(len(bits), 24)
    words = [sum(b << (8 + k) for k, b in enumerate(bits[i * 24:i * 24 + 24])) for i in range(full)]
    words.append(sum(b << (32 - rem + k) for k, b in enumerate(bits[24 * full:])))
    return len(bits), words


@pytest.mark.parametrize("n,lost", [(64, 0), (64, 3), (65, 7), (127, 11), (1518, 16)])
def test_decode_record_agrees_with_the_board_decoder(n, lost):
    raw = test_frame(n)
    count, words = records(raw, lost)
    assert eth.decode_record(count, words) == raw
    assert eth10_rx.decode((count, tuple(words))) == raw


def bits_record(bits):
    n = len(bits)
    full, rem = divmod(n, 24)
    w = [sum(b << (8 + k) for k, b in enumerate(bits[i * 24:i * 24 + 24])) for i in range(full)]
    w.append(sum(b << (32 - rem + k) for k, b in enumerate(bits[24 * full:])))
    return n, w


def test_decode_record_rejects():
    raw = test_frame(64)
    assert eth.decode_record(*records(raw, 17)) is None         # 39 preamble bits survive: 40 are required
    assert eth.decode_record(*records(raw, 16)) == raw
    assert eth.decode_record(*bits_record(wire_bits(raw) + [1])) is None   # not a whole number of bytes
    assert eth.decode_record(2, [1 << 31]) is None              # a link pulse
    count, words = records(raw)
    assert eth.decode_record(count, words[:-1]) is None         # a word short of its count
    assert eth.decode_record(*bits_record([1, 0] * 32 + wire_bits(raw)[64:])) is None   # no SFD


# ---------------------------------------------------------------- ARP and ping at the pads
@pytest.mark.parametrize("clock", CLOCKS)
def test_arp_then_ping_at_the_pads(clock):
    rig = Rig(clock_hz=clock)
    rig.run_us(20)
    r = exchange(rig, peer_arp_request())
    assert len(r) == 1
    check_arp_reply(r[0])
    for seq in (1, 2):
        data = ping_payload(seq=seq)
        r = exchange(rig, peer_ping(seq, data))
        assert len(r) == 1
        check_ping_reply(r[0], seq, data)
    assert rig.station.stats == {"rx": 3, "arp": 1, "icmp": 2}
    print(f"\n{clock / 1e6:.0f} MHz: ARP and 2 pings, {rig.now_us():.0f} us simulated")


def test_udp_echo_at_the_pads():
    rig = Rig()
    r = exchange(rig, peer_udp(b"wirelingo"))

    def payload(p):
        assert p[8:] == b"wirelingo" and struct.unpack(">HH", p[:4]) == (7, 40000)
    check_echo_reply(r[0], 17, payload)


@pytest.mark.parametrize("clock,jitter", [(50e6, 13.5), (40e6, 12.0)])
def test_ping_with_edge_jitter(clock, jitter):
    """Every far-end edge moved by up to +-jitter ns, the far end's clock 100 ppm off."""
    rig = Rig(clock_hz=clock)
    rig.wire.rng = random.Random(int(jitter))
    start = len(rig.wire.frames())
    rig.wire.send(peer_ping(3), jitter_ns=jitter, ppm=100)
    assert rig.run_until(lambda: len(rig.wire.frames()) > start, 1000)
    check_ping_reply(rig.wire.frames()[-1], 3, ping_payload(seq=3))


def test_50mhz_half_cells_are_40_and_60_ns_and_40mhz_is_exact():
    """The price of CLKDIV 1.25: four ticks are five clocks, so half cells come in 2s and 3s."""
    for clock, widths in ((50e6, {40.0, 60.0}), (40e6, {50.0})):
        rig = Rig(clock_hz=clock)
        fr = test_frame(64)
        rig.host.send(fr)
        rig.run_until(lambda: rig.wire.frames(), 500)
        e = [b for b in rig.wire.bursts if not b.pulse][0].edges
        got = set()
        mids = [t for (t, lv), (_, prev) in zip(e[1:], e) if lv * prev == -1]
        for a, b in zip(e, e[1:]):
            if a[1] and b[1] and b[0] - a[0] < 75:
                got.add(round(b[0] - a[0], 3))
        assert got <= widths and len(got) == len(widths)
        print(f"\n{clock / 1e6:.0f} MHz: half cells {sorted(got)} ns, {len(mids)} transitions")


@pytest.mark.parametrize("name,frame", [
    ("corrupt_fcs", peer_ping()[:-1] + bytes([peer_ping()[-1] ^ 0x80])),
    ("corrupt_payload", (lambda f: f[:50] + bytes([f[50] ^ 1]) + f[51:])(peer_ping())),
    ("other_mac", peer_ping(dst_mac=bytes.fromhex("02574c000003"))),
    ("other_ip", peer_ping(dst_ip=bytes([10, 77, 0, 3]))),
    ("arp_other_ip", peer_arp_request(bytes([10, 77, 0, 3]))),
])
def test_no_reply_at_the_pads(name, frame):
    """Silence on TD, then a good ping is answered, so the silence meant something."""
    rig = Rig()
    assert_no_reply(rig, frame)
    assert rig.host.stats.get("rx_frames") == 1
    data = ping_payload(seq=5)
    check_ping_reply(exchange(rig, peer_ping(5, data))[0], 5, data)


def test_bit_flipped_on_the_line_gets_no_reply():
    """Damage between the sender's FCS and the pad: a Manchester bit of the frame inverted."""
    rig = Rig()
    bits = wire_bits(peer_ping())
    bits[64 + 8 * 40 + 3] ^= 1
    before = len(rig.wire.bursts)
    rig.wire.send(bits=bits)
    rig.run_until(lambda: not rig.wire.queue, 100)
    rig.run_us(500)
    assert not [b for b in rig.wire.bursts[before:] if not b.pulse]
    assert rig.station.stats.get("bad_fcs") == 1


# ---------------------------------------------------------------- frame size limits
@pytest.mark.parametrize("clock", CLOCKS)
def test_largest_ping(clock):
    """1500-byte IP packet, a 1518-byte frame both ways through pads and SPI."""
    rig = Rig(clock_hz=clock)
    data = bytes((i * 7 + 3) & 255 for i in range(1472))
    fr = peer_ping(1, data)
    assert len(fr) == 1518
    r = exchange(rig, fr, limit_us=4000)
    assert len(r) == 1 and len(r[0]) == 1518
    check_ping_reply(r[0], 1, data)


def test_smallest_frames_and_zero_runs():
    """ARP padding makes FIFO words that are 0; an all-zero ping is nothing but
    those. A host that reads the FIFO blind loses them, so count them."""
    rig = Rig()
    data = bytes(1000)
    r = exchange(rig, peer_ping(1, data), limit_us=3000)
    check_ping_reply(r[0], 1, data)
    assert len(r[0]) == 14 + 20 + 8 + 1000 + 4
    _, words = records(peer_ping(1, data))
    zero = sum(1 for w in words if w == 0)
    assert zero > 300
    print(f"\nall-zero ping: {zero} of {len(words)} record words read as an empty FIFO would")


@pytest.mark.parametrize("n,why", [(1519, "bad_size"), (1522, "rx_malformed"), (63, "bad_size"), (32, "rx_runts")])
def test_illegal_sizes_at_the_pads(n, why):
    """Longer than 1518 (a VLAN-tagged maximum too) or shorter than 64: dropped,
    counted, no reply. Good FCS on all of them, so size is the only fault."""
    rig = Rig()
    fr = test_frame(n)
    rig.wire.send(fr)
    rig.run_until(lambda: not rig.wire.queue, 100)
    rig.run_us(n * 0.8 + 150)
    stats = {**rig.host.stats, **rig.station.stats}
    assert stats.get(why, 0) >= 1, stats
    assert not [b for b in rig.wire.bursts if not b.pulse]


def test_send_rejects_illegal_sizes():
    rig = Rig()
    for n in (63, 1519):
        with pytest.raises(ValueError):
            rig.host.send(bytes(n))
    with pytest.raises(ValueError):
        eth.frame(BCAST, PEER_MAC, 0x88B5, bytes(1501))


# ---------------------------------------------------------------- the host port's real rate
def rate_run(clock, quad, div, what, n=1518, gap=0):
    """Frames at a stated SCK; `what` is one of test_rate_table's cases. Returns
    (rx ok, tx ok, stats, Rig)."""
    rig = Rig(clock_hz=clock, quad=quad, sck_div=div, reply=False, gap=gap)
    rig.run_us(5)
    rx, tx = test_frame(n), test_frame(n, seed=2)
    if what in ("tx", "both"):
        rig.host.send(tx)
    if what == "rx":
        rig.wire.send(rx, force=True)
    elif what == "both":
        rig.wire.send(rx, at_ns=rig.wire.now_ns() + 40000, force=True)
    elif what == "defer":
        rig.wire.send(rx, force=True)
        rig.run_us(30)
        rig.host.send(tx)
    rig.run_until(rig.quiet, 2 * (n * 0.8) + 200)
    rig.run_us(20)
    return rx in rig.received, tx in rig.wire.frames(), rig.host.stats, rig


Y, N = True, False
RATES = [    # clock, quad, SCK = clock / div, rx, tx, both (rx ok, tx ok), defer (rx ok, tx ok)
    (50e6, True, 6, Y, Y, (Y, Y), (Y, Y)),
    (50e6, True, 7, Y, Y, (N, Y), (Y, Y)),
    (50e6, True, 8, N, Y, (N, Y), (N, Y)),
    (50e6, True, 9, N, Y, (N, N), (N, Y)),
    (50e6, True, 10, N, N, (N, N), (N, N)),
    (50e6, False, 2, Y, Y, (Y, Y), (Y, Y)),
    (50e6, False, 3, N, Y, (N, N), (N, Y)),
    (50e6, False, 4, N, N, (N, N), (N, N)),
    (40e6, True, 5, Y, Y, (Y, Y), (Y, Y)),
    (40e6, True, 6, Y, Y, (N, Y), (Y, Y)),
    (40e6, True, 7, N, Y, (N, N), (N, Y)),
    (40e6, True, 8, N, N, (N, N), (N, N)),
    (40e6, False, 1, Y, Y, (Y, Y), (Y, Y)),
    (40e6, False, 2, Y, Y, (N, N), (Y, Y)),
    (40e6, False, 3, N, N, (N, N), (N, N)),
]


@pytest.mark.parametrize("clock,quad,div,rx,tx,both,defer", RATES)
def test_rate_table(clock, quad, div, rx, tx, both, defer):
    """A 1518-byte frame at a stated SCK, pe_spim's CS timing and no software
    delay at all: the fastest any host on this port can be. rx and tx: one
    direction alone. defer: a reply queued while the frame arrives, the
    half-duplex case. both: a collision, the far end starting 20 us into
    our frame; the worst a host can see. Whatever a frame's fate, the glue must
    not claim one that didn't arrive."""
    mhz = clock / (1e6 * div)
    want = dict(rx=(rx, False), tx=(False, tx), both=both, defer=defer)
    for what in want:
        r, t, stats, rig = rate_run(clock, quad, div, what)
        print(f"\n{clock / 1e6:.0f} MHz {'quad' if quad else '1-bit'} SCK {mhz:.2f} MHz {what}: rx {r} tx {t} {stats}", end="")
        assert bool(stats.get("rx_frames")) == r, stats
        assert bool(stats.get("tx_frames")) == t, stats
        assert rig.wire.both_high == 0
        assert (r, t) == want[what], (what, r, t, stats)


@pytest.mark.parametrize("clock,gap,ok", [(50e6, 800, True), (50e6, 900, False), (40e6, 640, True), (40e6, 720, False)])
def test_rx_service_latency(clock, gap, ok):
    """Software time allowed between passes, RX, quad at SCK = clk: the FIFOs and
    the line queue are all there is."""
    r, _, stats, _ = rate_run(clock, True, 1, "rx", SHORT, gap=gap)
    print(f"\n{clock / 1e6:.0f} MHz, {gap / clock * 1e6:.1f} us of software per pass: {r} {stats}")
    assert r is ok
    assert bool(stats.get("rx_overflows")) is not ok


@pytest.mark.parametrize("clock,gap,ok", [(50e6, 1200, True), (50e6, 1400, False), (40e6, 960, True), (40e6, 1120, False)])
def test_tx_service_latency(clock, gap, ok):
    _, t, stats, _ = rate_run(clock, True, 1, "tx", SHORT, gap=gap)
    print(f"\n{clock / 1e6:.0f} MHz, {gap / clock * 1e6:.1f} us of software per pass: {t} {stats}")
    assert t is ok
    assert bool(stats.get("tx_underruns")) is not ok


def test_min_frame_still_needs_service():
    """Even the shortest frame outruns the FIFOs: 72 bytes on the wire are 24
    record words, the FIFO holds 8. A host that only looks when a frame is over
    has lost it, which is why the glue has to stream."""
    rig = Rig(reply=False)
    fr = test_frame(64)
    rig.wire.send(fr, force=True)
    rig.port.idle(int(58.0 * 50) + 100)
    rig.run_us(30)
    assert fr not in rig.received
    assert rig.host.stats.get("rx_overflows") == 1


def test_link_pulses_pile_up_if_nobody_reads():
    """Records of link pulses wait in the FIFO, and a sleeping host overflows it
    with pulses alone, 16 ms apart on a real wire. The glue finds out from the
    damaged records and recovers, and the next frames arrive."""
    rig = Rig(reply=False)
    rig.run_us(30)
    t = rig.wire.now_ns()
    n, step = 40, 3000.0
    rig.wire.pulses([t + 1000 + i * step for i in range(n)])
    first = None
    for i in range(n):
        rig.port.idle(int(step / rig.wire.clk))
        if first is None and rig.mh.chip.line.ovf:
            first = i + 1
    print(f"\noverflowed after {first} link pulses with no service")
    assert first and first >= 4
    a, b = test_frame(100), test_frame(100, seed=2)
    rig.run_us(60)
    rig.wire.send(a)
    rig.run_until(rig.quiet, 300)
    rig.wire.send(b)
    rig.run_until(rig.quiet, 300)
    assert b in rig.received
    assert rig.host.stats.get("rx_overflows") == 1


def test_overflow_loses_one_frame_and_recovers():
    """A stalled host loses the frame in flight: counted, core 0 restarted, the
    next frame is whole, and core 1 keeps its frame."""
    rig = Rig(reply=False)
    a, b = test_frame(200), test_frame(200, seed=3)
    tx = test_frame(64, seed=4)
    rig.wire.send(a, force=True)
    rig.port.idle(100 * 50)
    rig.run_until(lambda: rig.host.stats.get("rx_overflows"), 300)
    rig.run_until(rig.quiet, 300)
    assert a not in rig.received
    rig.host.send(tx)
    rig.run_until(rig.quiet, 300)
    rig.wire.send(b)
    rig.run_until(rig.quiet, 300)
    assert b in rig.received and tx in rig.wire.frames()


def test_tx_underrun_abandons_the_frame():
    """A host that stops feeding: the frame goes wrong on the wire, the glue says
    so, TD doesn't stay driven, and the next frame is whole."""
    rig = Rig(reply=False)
    long, nxt = test_frame(400), test_frame(100, seed=9)
    rig.host.send(long)
    rig.run_until(lambda: rig.host.go, 100)
    rig.port.idle(200 * 50)                      # 200 us: far past the 16 words of FIFO
    assert rig.wire.td != 0, "stalled mid-cell, driving"
    rig.run_us(10)
    assert rig.host.stats.get("tx_underruns") == 1 and not rig.host.stats.get("tx_frames")
    rig.port.idle(5 * 50)
    assert rig.wire.td == 0
    assert long not in rig.wire.frames()
    rig.host.send(nxt)
    rig.run_until(lambda: nxt in rig.wire.frames(), 200)
    assert rig.host.stats.get("tx_frames") == 1


# ---------------------------------------------------------------- half duplex
def on_air_overlap(rig):
    return [b for b in rig.wire.bursts if not b.pulse
            and any(b.t0 < e and s < b.t1 for s, e in rig.wire.sent)]


def arrival_run(carrier_sense, offset_ns, clock=50e6, tx_size=64, rx_size=64):
    """Queue a frame to send, while a far end that isn't listening (a frame
    already on its way, in effect) starts one offset_ns from the moment the
    glue set F_GO. Returns (overlap, TX start, RX start, Rig)."""
    rig = Rig(clock_hz=clock, carrier_sense=carrier_sense, reply=False)
    rig.run_us(15)
    rig.host.send(test_frame(tx_size, seed=2))
    rig.run_until(lambda: rig.host.go, 200)
    t_go = rig.wire.now_ns()
    rig.wire.send(test_frame(rx_size), at_ns=t_go + offset_ns, force=True)
    rig.run_until(lambda: rig.wire.sent and rig.quiet(), 400)
    rig.run_us(20)
    b = [x for x in rig.wire.bursts if not x.pulse][0]
    return bool(on_air_overlap(rig)), b.t0 - t_go, rig.wire.sent[0][0] - t_go, rig


@pytest.mark.parametrize("clock", CLOCKS)
def test_carrier_sense_defers_to_an_arriving_frame(clock):
    """F_GO is set, and the far end starts anywhere from at once to the end of
    the window. The chip never talks over it, and starts 96 bit times after
    the far end's last edge."""
    for off in range(1000, 9001, 1000):
        over, t_tx, t_rx, rig = arrival_run(True, off, clock)
        assert not over, off
        end = rig.wire.sent[0][1]
        b = [x for x in rig.wire.bursts if not x.pulse][0]
        assert b.t0 - end >= ethsim.IFG_NS - 100, (off, b.t0, end)
        assert b.t0 - end < ethsim.IFG_NS + 300


def blind_run(carrier_sense, lead_ns, tx_start=None):
    """The model is deterministic, so a first run finds when TX starts with the
    line idle; a second has the far end start lead_ns before that. Returns (time
    of the first TD edge, time of the far end's first edge), absolute."""
    rig = Rig(carrier_sense=carrier_sense, reply=False)
    rig.run_us(15)
    if tx_start is not None:
        rig.wire.send(test_frame(64), at_ns=tx_start - lead_ns, force=True)
    rig.host.send(test_frame(64, seed=2))
    rig.run_until(lambda: [b for b in rig.wire.bursts + [rig.wire.open] if b and not b.pulse], 400)
    first = [b for b in rig.wire.bursts + [rig.wire.open] if b and not b.pulse][0].t0
    return first, rig.wire.sent[0][0] if rig.wire.sent else None


def blind_ns(carrier_sense, leads):
    """The most a far-end frame can lead our first edge with the chip still
    going ahead at its usual time, over a sweep of leads."""
    t0 = blind_run(carrier_sense, 0)[0]
    hit, heard = [], []
    for lead in leads:
        tx, rx = blind_run(carrier_sense, lead, t0)
        (hit if tx == t0 else heard).append(t0 - rx)
    return max(hit), min(heard) if heard else None


def test_blind_window_with_carrier_sense():
    """How late can the far end's first edge be and still hold the chip back? 10 ns
    steps. What's left is the synchronizer and the jump to the preamble: a frame
    starting inside it collides, as on any CSMA station, since the two can't yet
    know of each other. docs/eth10-network.md quotes this."""
    blind, heard = blind_ns(True, range(50, 400, 10))
    print(f"\neth10_tx_cs: a far-end edge up to {blind:.0f} ns before our first edge goes unheard; {heard:.0f} ns is heard")
    assert 0 < blind <= 150 and blind < heard <= blind + 40


def test_blind_window_without_carrier_sense():
    """The control. Plain eth10_tx with the host's own look at LINESTAT: the look is
    stale by an SPI read, an SPI write and the idle loop, microseconds, in a
    frame that arrives over a preamble's time. This is why the stack does not
    load it. Quad SPI at 25 MHz; a slower host is worse."""
    blind, heard = blind_ns(False, range(0, 14000, 500))
    print(f"\nplain eth10_tx: a far-end edge up to {blind:.0f} ns before our first edge goes unheard; {heard} is heard")
    assert blind >= 2000


def test_chip_waits_while_a_long_frame_arrives():
    """A reply queued while 1518 bytes come in: no edge on TD until they end,
    with the RX side streaming through the wait."""
    rig = Rig(reply=False)
    rx = test_frame(1518)
    rig.wire.send(rx, force=True)
    rig.run_us(30)
    tx = test_frame(1518, seed=5)
    rig.host.send(tx)
    rig.run_until(rig.quiet, 3500)
    rig.run_us(20)
    assert rx in rig.received and tx in rig.wire.frames()
    assert not on_air_overlap(rig)
    assert [b for b in rig.wire.bursts if not b.pulse][0].t0 - rig.wire.sent[0][1] >= ethsim.IFG_NS - 100


def test_two_chip_frames_keep_the_gap():
    rig = Rig(reply=False, sck_div=1)
    a, b = test_frame(64, seed=1), test_frame(64, seed=2)
    rig.host.send(a)
    rig.host.send(b)
    rig.run_until(lambda: len(rig.wire.frames()) == 2, 400)
    ba, bb = [x for x in rig.wire.bursts if not x.pulse]
    print(f"\nchip's own gap: {bb.t0 - ba.t1:.0f} ns from TP_IDL's end to the next preamble")
    assert bb.t0 - ba.t1 >= ethsim.IFG_NS - 300                    # TP_IDL is inside the 96 bit times
    assert wire_frames(rig.wire) == [a, b]


def test_far_end_also_defers_to_the_chip():
    """The wire model's half duplex: its next frame waits for the chip's reply."""
    rig = Rig()
    rig.wire.send(peer_ping(1))
    rig.wire.send(peer_ping(2))
    rig.run_until(lambda: len(rig.wire.frames()) == 2, 1500)
    assert not on_air_overlap(rig)
    assert rig.host.stats.get("rx_frames") == 2 and rig.host.stats.get("tx_frames") == 2


def test_line_never_quiet_gives_up_and_link_pulses_resume():
    """Traffic with gaps under the window starves the deferral. F_GO stays set,
    so no link pulse goes out; the host's timeout ends it and restarts core 1,
    which starts at a link pulse."""
    rig = Rig(reply=False)
    rig.host.tx_timeout_us = 400
    tx = test_frame(64, seed=3)
    rig.run_us(15)
    rig.host.send(tx)
    rig.run_until(lambda: rig.host.go, 100)
    pulses = [rig.wire.now_ns() + 2000 + i * 6000.0 for i in range(100)]    # one every 6 us
    rig.wire.pulses(pulses)
    before = len(rig.wire.pulse_bursts())
    rig.run_until(lambda: rig.host.stats.get("tx_timeouts"), 1000)
    assert rig.host.stats == {**rig.host.stats, "tx_timeouts": 1, "tx_aborts": 1}
    assert not [b for b in rig.wire.bursts if not b.pulse]
    rig.run_us(10)
    assert len(rig.wire.pulse_bursts()) == before + 1
    assert tx not in rig.wire.frames()


def test_collision_is_not_handled_but_does_no_lasting_harm():
    """The far end starts inside the blind window or simply ignores carrier. Both
    frames run to their end: no jam, no back-off, no retry here. The chip's RX still
    gets the far end's frame; everything works afterwards."""
    rig = Rig(reply=True, sck_div=1)
    rig.run_us(15)
    data = ping_payload(seq=8)
    rig.host.send(test_frame(500, seed=7))
    rig.run_until(lambda: rig.wire.td != 0, 100)
    rig.wire.send(peer_ping(8, data), at_ns=rig.wire.now_ns() + 1000, force=True)
    rig.run_until(rig.quiet, 1500)
    assert on_air_overlap(rig)
    assert rig.host.stats.get("rx_frames") == 1
    r = wire_frames(rig.wire)
    assert test_frame(500, seed=7) in r
    check_ping_reply(r[-1], 8, data)
    data = ping_payload(seq=9)
    check_ping_reply(exchange(rig, peer_ping(9, data))[0], 9, data)


def waveform(defines, name):
    """A 69-byte frame's edges relative to the first one, in clocks, firmware
    alone: FIFOs preloaded, no host port. 69 leaves a part-used last word."""
    mh = ModelHost()
    prog = asm.assemble((ROOT / "firmware" / name).read_text(), defines)
    mh.apply(host.load_program(0, prog, 0, side_pins=8, clkdiv=320))
    mh.start(1)
    mh.step(200)
    fr = test_frame(69, seed=2)
    b = fr + bytes(-len(fr) & 3)
    mh.write(sim.core_reg(0, sim.C_R0), 8 * len(fr) - 1)
    words = [int.from_bytes(b[i:i + 4], "little") for i in range(0, len(b), 4)]
    for w in words[:16]:
        while len(mh.chip.cores[0].txf) >= 8:
            mh.step()
        mh.push(0, w)
    mh.step(20)
    mh.write(sim.A_FSET, 1)
    edges, prev = [], 0
    for _ in range(20000):
        if words[16:] and len(mh.chip.cores[0].txf) < 8:
            mh.push(0, words.pop(16))
        mh.step()
        v = mh.chip.outputs()[0] & 3
        if v != prev:
            edges.append((mh.chip.cycle, v))
            prev = v
        if edges and not (mh.chip.flags & 1 or prev):
            break
    return [(c - edges[0][0], v) for c, v in edges]


def test_cs_firmware_sends_the_same_waveform_as_eth10_tx():
    """Same levels in the same order. Times to within a clock: where the 1.25
    divider's pattern stands at the first edge depends on the path into the
    frame, which moves the 40 and 60 ns half cells by one tick."""
    plain = waveform({"F_GO": 0}, "eth10_tx.pasm")
    cs = waveform({"F_GO": 0, "RXP": 16}, "eth10_tx_cs.pasm")
    assert len(plain) > 500
    assert [v for _, v in plain] == [v for _, v in cs]
    assert max(abs(a[0] - b[0]) for a, b in zip(plain, cs)) <= 1


# ---------------------------------------------------------------- link pulses
def tx_rig(cs, clock=50e6):
    rig = Rig(clock_hz=clock, carrier_sense=cs, reply=False)
    return rig


R1 = sim.core_reg(1, sim.C_R0 + 1)


def next_pulse_after(rig, t, limit_us):
    if not rig.run_until(lambda: rig.wire.pulse_bursts() and rig.wire.pulse_bursts()[-1].t0 > t, limit_us):
        raise AssertionError("no link pulse")
    return rig.wire.pulse_bursts()[-1]


@pytest.mark.parametrize("cs", [False, True])
def test_link_pulses_while_idle_a_whole_period(cs):
    """Two pulses 16 ms apart at 50 MHz with both cores running, each 100 ns. The
    long run: it steps 800,000 clocks."""
    rig = tx_rig(cs)
    rig.gap = 40000                               # a host polling at 800 us; the chip doesn't care
    rig.run_us(10)
    first = rig.wire.pulse_bursts()[0]
    second = next_pulse_after(rig, first.t0, 17000)
    gap = (second.t0 - first.t0) / 1e6
    print(f"\n{'eth10_tx_cs' if cs else 'eth10_tx'}: {gap:.3f} ms between pulses at 50 MHz, {second.t1 - second.t0:.0f} ns wide")
    assert 15.9 < gap < 16.1
    assert second.t1 - second.t0 == 100
    assert second.edges[0][1] == 1 and len(second.edges) == 2       # positive, then idle


@pytest.mark.parametrize("cs,clock", [(False, 50e6), (True, 50e6), (False, 40e6), (True, 40e6)])
def test_link_pulses_survive_frames(cs, clock):
    """The count is the core's R1: write it down so a period takes 2 ms of model
    time. Frames then land in the idle stretch; pulses go on, none of them
    falls inside a frame, and the timer's stopped, not lost."""
    rig = tx_rig(cs, clock)
    rig.run_us(10)
    rig.mh.write(R1, 60)
    fr = test_frame(100, seed=3)
    for i in range(3):
        rig.host.send(fr)
        rig.run_until(lambda: len(rig.wire.frames()) > i, 400)
        rig.mh.write(R1, 60)
        t = rig.wire.now_ns()
        b = next_pulse_after(rig, t, 200)
        assert b.t0 - t > 100
    frames = [b for b in rig.wire.bursts if not b.pulse]
    assert len(frames) == 3
    for p in rig.wire.pulse_bursts():
        assert all(p.t1 < f.t0 or f.t1 < p.t0 for f in frames)


def after_frame_pulse(cs, r1, clock=50e6):
    """A frame with the pulse count set to r1 while it is on the air. Returns the
    Rig and the frame's end."""
    rig = tx_rig(cs, clock)
    rig.run_us(10)
    rig.host.send(test_frame(64, seed=3))
    rig.run_until(lambda: rig.wire.td != 0 and not rig.wire.pulse_bursts()[1:], 200)
    rig.mh.write(R1, r1)
    rig.run_until(lambda: rig.wire.frames(), 200)
    end = [b for b in rig.wire.bursts if not b.pulse][0].t1
    return rig, end


def test_eth10_tx_pulse_can_follow_a_frame_at_once():
    """Plain eth10_tx leaves the count where it was: with R1 almost out, the pulse
    comes right behind TP_IDL. The stack doesn't load it for this reason, and
    a test should say so."""
    rig, end = after_frame_pulse(False, 1)
    b = next_pulse_after(rig, end, 50)
    print(f"\neth10_tx: link pulse {(b.t0 - end) / 1000:.1f} us after a frame")
    assert b.t0 - end < 10000


def test_eth10_tx_cs_restarts_the_pulse_timer_after_a_frame():
    """eth10_tx_cs reloads R1 at the end of a frame: the next pulse is 16 ms off.
    R1 is readable, and the period is measured by the long test above."""
    rig, end = after_frame_pulse(True, 1)
    r1 = rig.mh.read(R1)
    assert r1 > 9990
    print(f"\neth10_tx_cs: R1 = {r1} after a frame, so {(r1 + 1) * 64 / 40:.0f} us to the next pulse")
    t = rig.now_us()
    rig.run_us(100)
    assert not [b for b in rig.wire.pulse_bursts() if b.t0 > end]
    assert rig.now_us() - t >= 100


def test_autonegotiation_bursts_from_the_far_end_do_no_harm():
    """A modern NIC starts with fast link pulses, 33 of them 62.5 us apart. To
    eth10_rx they are short records. None of it is a frame, deferral sits each
    one out, and a frame right behind them is received and answered. (We send
    NLPs only, so a NIC falls back to 10 Mbit/s half duplex by parallel detection;
    that part is 802.3's and has not been tried against one.)"""
    rig = Rig()
    t = rig.wire.now_ns() + 20000
    rig.wire.pulses([t + i * 62500.0 for i in range(33)])
    rig.run_us(33 * 62.5 + 40)
    assert rig.host.stats.get("rx_pulses", 0) >= 33
    assert "rx_malformed" not in rig.host.stats and "rx_overflows" not in rig.host.stats
    assert not [b for b in rig.wire.bursts if not b.pulse]
    data = ping_payload(seq=4)
    check_ping_reply(exchange(rig, peer_ping(4, data))[0], 4, data)


# ---------------------------------------------------------------- the bridge, without root
def load_bridge():
    spec = importlib.util.spec_from_file_location("eth_tap", ROOT / "fpga/kv260/host/eth_tap.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_bridge_refuses_the_hosts_namespace(tmp_path):
    """The guard is the first thing that runs, and creates nothing."""
    tap = load_bridge()
    same = tmp_path / "ns"
    same.write_text("")
    with pytest.raises(tap.NetnsError, match="host's network namespace"):
        tap.require_dedicated_netns(same, same, lambda: [(1, "lo")])
    other = tmp_path / "ns2"
    other.write_text("")
    with pytest.raises(tap.NetnsError, match="eth0"):
        tap.require_dedicated_netns(same, other, lambda: [(1, "lo"), (2, "eth0")])
    tap.require_dedicated_netns(same, other, lambda: [(1, "lo")])
    r = subprocess.run([sys.executable, str(ROOT / "fpga/kv260/host/eth_tap.py"), "--model", "--selftest"],
                       capture_output=True, text=True, env={**os.environ, "PYTHONPATH": str(ROOT / "tools")})
    assert r.returncode == 2 and "refusing to run" in r.stderr


def test_bridge_backend_is_a_nic_to_a_tap():
    """A fake TAP: frames in without FCS and short, as Linux hands them over; frames
    out without FCS, as Linux takes them. ModelBackend behind the Bridge, whole."""
    tap = load_bridge()

    class FakeTap:
        fd = None
        inbox, outbox = [], []

        def __init__(self):
            self.r, self.w = os.pipe()
            self.fd = self.r

        def put(self, f):
            self.inbox.append(f)
            os.write(self.w, b"x")

        def read(self):
            if not self.inbox:
                return None
            os.read(self.r, 1)
            return self.inbox.pop(0)

        def write(self, f):
            self.outbox.append(f)

    t = FakeTap()
    b = tap.Bridge(t, tap.ModelBackend(CHIP_IP))
    t.put(peer_arp_request()[:42])                       # Linux's 42 bytes
    assert b.run_while(lambda: not t.outbox, 30)
    assert len(t.outbox) == 1
    check_arp_reply(t.outbox[0] + fcs(t.outbox[0]))
    assert len(t.outbox[0]) == 60
    data = ping_payload()
    t.put(peer_ping(1, data)[:-4])
    assert b.run_while(lambda: len(t.outbox) < 2, 30)
    check_ping_reply(t.outbox[1] + fcs(t.outbox[1]), 1, data)
    assert len(b.backend.rtt_us) == 1 and b.backend.report()["td_both_high"] == 0
    b.backend.send(bytes(1515))
    assert b.backend.oversize == 1
    with pytest.raises(SystemExit):
        tap.FpgaBackend()


def test_measure_spim_counts_a_pass_as_the_glue_does():
    tap = load_bridge()

    class Fake:
        calls = []

        def read(self, addr, n=1, fixed=False):
            self.calls.append((addr, n, fixed))
    f = Fake()
    assert tap.measure_spim(f, 3) >= 0
    assert f.calls == [(ethhost.core_reg(0, ethhost.C_STATUS), 1, False),
                       (ethhost.core_reg(0, ethhost.C_FIFO), 4, True)] * 3


# ---------------------------------------------------------------- independent judges
def scapy_python():
    p = os.environ.get("PE_SCAPY_PY") or os.path.expanduser("~/protoemu-flow/eth-venv/bin/python")
    return p if os.path.exists(p) else None


def run_session(tmp_path):
    """ARP, ping, UDP at the pads. Returns the pcap path; frames carry their FCS."""
    rig = Rig()
    rig.run_us(10)
    frames = [peer_arp_request()]
    frames += exchange(rig, frames[0])
    p = peer_ping(1, ping_payload(seq=1))
    frames += [p] + exchange(rig, p)
    u = peer_udp(b"wirelingo")
    frames += [u] + exchange(rig, u)
    big = peer_ping(2, bytes(i & 255 for i in range(1000)))
    frames += [big] + exchange(rig, big, limit_us=3000)
    path = tmp_path / "session.pcap"
    ethsim.write_pcap(path, frames)
    return path, frames


def tcpdump(path):
    return subprocess.run(["tcpdump", "-nn", "-e", "-vvv", "-r", str(path)], capture_output=True, text=True, check=True).stdout


@pytest.mark.skipif(shutil.which("tcpdump") is None, reason="tcpdump not installed")
def test_tcpdump_reads_the_session(tmp_path):
    path, frames = run_session(tmp_path)
    out = tcpdump(path)
    print("\n" + out)
    assert len(frames) == 8
    assert "Reply 10.77.0.2 is-at 02:57:4c:00:00:02" in out
    assert out.count("ICMP echo reply") == 2 and out.count("ICMP echo request") == 2
    assert out.count("[udp sum ok]") == 2
    assert "bad" not in out and "wrong" not in out and "truncated" not in out.lower()
    # the control: a checksum broken on purpose has to show
    br = bytearray(frames[3])
    br[14 + 10] ^= 0x10                     # IP header checksum
    br2 = bytearray(frames[3])
    br2[14 + 20 + 2] ^= 0x10                # ICMP checksum
    ethsim.write_pcap(tmp_path / "bad.pcap", [bytes(br), bytes(br2)])
    bad = tcpdump(tmp_path / "bad.pcap")
    assert "bad cksum" in bad and "wrong icmp cksum" in bad


@pytest.mark.skipif(scapy_python() is None, reason="no scapy venv: python3 -m venv ~/protoemu-flow/eth-venv && pip install scapy")
def test_scapy_validates_the_session(tmp_path):
    path, frames = run_session(tmp_path)
    judge = pathlib.Path(__file__).with_name("pcap_judge.py")
    r = subprocess.run([scapy_python(), str(judge), str(path)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    v = json.loads(r.stdout)
    print("\n", v)
    assert [x["kind"] for x in v] == ["arp-who-has", "arp-is-at", "echo-request", "echo-reply", "udp", "udp",
                                      "echo-request", "echo-reply"]
    assert all(x["fcs_ok"] and x["checksums_ok"] for x in v)
    assert v[1]["hwsrc"] == "02:57:4c:00:00:02" and v[1]["psrc"] == "10.77.0.2"
    assert v[3]["payload"] == v[2]["payload"] and v[7]["payload"] == v[6]["payload"]
    assert v[5]["load"] == v[4]["load"] == "wirelingo"
    # the control
    br = bytearray(frames[3])
    br[14 + 21] ^= 0x10
    ethsim.write_pcap(tmp_path / "bad.pcap", [bytes(br)])
    r = subprocess.run([scapy_python(), str(judge), str(tmp_path / "bad.pcap")], capture_output=True, text=True)
    v = json.loads(r.stdout)
    assert not v[0]["fcs_ok"] and not v[0]["checksums_ok"]


# ---------------------------------------------------------------- the board bundle
FORBIDDEN = {"struct", "dataclasses", "typing", "re", "enum"}


def imports(path):
    tree = ast.parse(path.read_text())
    return {(n.names[0].name if isinstance(n, ast.Import) else n.module or "") for n in ast.walk(tree)
            if isinstance(n, (ast.Import, ast.ImportFrom))}


def test_stack_sources_stay_micropython_sized():
    """No imports a board build lacks, in the sources or their bundled copies,
    and nothing PC-only at module level."""
    assert imports(ROOT / "tools/pe/eth.py") == {"binascii"}
    bundled = ROOT / "tools/micropython"
    assert imports(bundled / "eth_stack.py") == {"binascii"}
    assert "eth_stack" in imports(bundled / "eth10_host.py")
    assert not imports(bundled / "eth10_host.py") & FORBIDDEN


MPY_SCRIPT = r"""
import eth_stack as eth, eth10_host
st = eth.Station(eth.mac("02:57:4c:00:00:02"), eth.ip("10.77.0.2"))
peer, pip = eth.mac("02:00:de:ad:be:01"), eth.ip("10.77.0.1")
r = st.handle(eth.arp_request(peer, pip, st.ip))
assert len(r) == 1 and eth.parse_frame(r[0])[0] == peer
req = eth.frame(st.mac, peer, eth.ETH_IP, eth.ipv4(pip, st.ip, 1, eth.icmp_echo(8, 1, 2, b"micropython")))
r = st.handle(req)
assert eth.parse_icmp_echo(eth.parse_ipv4(eth.parse_frame(r[0])[3])[3]) == (0, 1, 2, b"micropython")
u = eth.frame(st.mac, peer, eth.ETH_IP, eth.ipv4(pip, st.ip, 17, eth.udp(pip, st.ip, 5, 7, b"x")))
assert len(st.handle(u)) == 1
assert st.handle(req[:-1] + bytes([req[-1] ^ 1])) == []
eth._crc32 = None
assert eth.crc32(b"123456789") == 0xCBF43926
class Port:
    log = []
    fifo = [0]
    def read(self, addr, n=1, fixed=False):
        return 0
    def write(self, addr, words, fixed=False):
        self.log.append(addr)
    def now_us(self):
        return 0
h = eth10_host.Eth10Host(Port(), 50000000)
h.send(req)
h.service()
print("ok", len(Port.log))
"""


def test_runs_on_real_micropython(tmp_path):
    exe = os.environ.get("PE_MICROPYTHON") or os.path.expanduser("~/protoemu-flow/eth-tools/micropython")
    if not os.path.exists(exe) and shutil.which("micropython") is None:
        pytest.skip("no micropython: see docs/eth10-network.md")
    exe = exe if os.path.exists(exe) else shutil.which("micropython")
    (tmp_path / "t.py").write_text(MPY_SCRIPT)
    r = subprocess.run([exe, str(tmp_path / "t.py")], capture_output=True, text=True, timeout=60,
                       env={**os.environ, "MICROPYPATH": str(ROOT / "tools/micropython")})
    assert r.returncode == 0 and r.stdout.startswith("ok"), r.stdout + r.stderr


def test_bundle_image_matches_load_writes():
    import protoemu_fw
    from pe import mpy_bundle
    assert protoemu_fw.ETH_STACK_50["writes"] == mpy_bundle.bursts(ethhost.load_writes(50e6))
    assert protoemu_fw.ETH_STACK_40["writes"] == mpy_bundle.bursts(ethhost.load_writes(40e6))


def test_load_writes_configure_both_cores():
    mh = ModelHost()
    mh.apply(ethhost.load_writes(50e6))
    mh.step(5)
    assert mh.chip.enable == 3
    assert (mh.chip.cores[1].div_int, mh.chip.cores[1].div_frac) == (1, 0x40)       # 1.25
    assert mh.chip.line.div >> 16 == 5 and mh.chip.line.rxp == 16
    assert mh.chip.pin_owner[8] == mh.chip.pin_owner[9] == 1
    mh = ModelHost()
    mh.apply(ethhost.load_writes(40e6))
    mh.step(5)
    assert (mh.chip.cores[1].div_int, mh.chip.cores[1].div_frac) == (1, 0)
    assert mh.chip.line.div >> 16 == 4
    with pytest.raises(ValueError):
        ethhost.load_writes(48e6)
    with pytest.raises(ValueError):
        ethhost.load_writes(50e6, rxp=8)                     # not an input pad
