"""Ethernet II, ARP, IPv4, ICMP echo and UDP echo for a host beside the chip.

The chip moves bits (firmware/eth10_rx.pasm, eth10_tx_cs.pasm); CRC-32 and every
protocol live here, in host software. Plain bytes and ints, no imports beyond
binascii (optional), so the same file runs in CPython and in MicroPython:
tools/pe/mpy_bundle.py copies it to tools/micropython/eth_stack.py.

Frames are bytes from the destination MAC through the FCS, as on the wire.
Addresses are bytes (6 or 4); mac() and ip() convert from text.

    st = Station(mac("02:57:4c:00:00:02"), ip("10.77.0.2"))
    for reply in st.handle(received_frame):
        send(reply)

Every parser returns None for anything malformed, and never raises on data from
the wire. docs/eth10-network.md says what has been shown at which level.
"""
try:
    from binascii import crc32 as _crc32
except ImportError:
    _crc32 = None

BROADCAST = b"\xff" * 6
ETH_IP, ETH_ARP = 0x0800, 0x0806
PROTO_ICMP, PROTO_UDP = 1, 17
ARP_REQUEST, ARP_REPLY = 1, 2
ICMP_ECHO_REPLY, ICMP_ECHO_REQUEST = 0, 8
MIN_FRAME, MAX_FRAME = 64, 1518      # destination through FCS; no VLAN tag
HEADER, FCS = 14, 4
UDP_ECHO_PORT = 7
SFD = 0xD5
MIN_PREAMBLE_BITS = 40               # eth10_rx may lose the first few bits of the preamble

_table = None


def crc32(data):
    """IEEE 802.3 CRC-32 of bytes, as an int."""
    global _table
    if _crc32 is not None:
        return _crc32(data) & 0xFFFFFFFF
    if _table is None:
        _table = [0] * 256
        for i in range(256):
            c = i
            for _ in range(8):
                c = (c >> 1) ^ (0xEDB88320 if c & 1 else 0)
            _table[i] = c
    c = 0xFFFFFFFF
    for b in data:
        c = _table[(c ^ b) & 255] ^ (c >> 8)
    return c ^ 0xFFFFFFFF


def mac(text):
    return bytes(int(x, 16) for x in text.split(":"))


def ip(text):
    return bytes(int(x) for x in text.split("."))


def u16(b, i):
    return (b[i] << 8) | b[i + 1]


def be16(v):
    return bytes(((v >> 8) & 255, v & 255))


def checksum(data, seed=0):
    """Internet checksum (RFC 1071): 0 when computed over data holding its own."""
    s = seed
    n = len(data) & ~1
    for i in range(0, n, 2):
        s += (data[i] << 8) | data[i + 1]
    if len(data) & 1:
        s += data[-1] << 8
    while s >> 16:
        s = (s & 0xFFFF) + (s >> 16)
    return ~s & 0xFFFF


def locally_administered(addr):
    """A unicast address from the locally administered range (bits 1:0 of byte 0 = 10)."""
    return addr[0] & 3 == 2


# ---------------------------------------------------------------- Ethernet II
def frame(dst, src, ethertype, payload):
    """A complete frame: padded to the 64-byte minimum, FCS appended."""
    body = bytes(dst) + bytes(src) + be16(ethertype) + bytes(payload)
    if len(body) < MIN_FRAME - FCS:
        body += bytes(MIN_FRAME - FCS - len(body))
    if len(body) > MAX_FRAME - FCS:
        raise ValueError("frame too long")
    return body + crc32(body).to_bytes(4, "little")


def fcs_ok(raw):
    return len(raw) > FCS and crc32(raw[:-FCS]).to_bytes(4, "little") == bytes(raw[-FCS:])


def parse_frame(raw):
    """(dst, src, ethertype, payload) of a frame of legal size with a good FCS,
    payload including any padding; else None."""
    if not MIN_FRAME <= len(raw) <= MAX_FRAME or not fcs_ok(raw):
        return None
    return bytes(raw[:6]), bytes(raw[6:12]), u16(raw, 12), bytes(raw[HEADER:-FCS])


# ---------------------------------------------------------------- ARP
def _arp(op, sha, spa, tha, tpa):
    return b"\x00\x01\x08\x00\x06\x04" + be16(op) + sha + spa + tha + tpa


def arp_request(sha, spa, tpa):
    return frame(BROADCAST, sha, ETH_ARP, _arp(ARP_REQUEST, sha, spa, bytes(6), tpa))


def arp_reply(sha, spa, tha, tpa):
    return frame(tha, sha, ETH_ARP, _arp(ARP_REPLY, sha, spa, tha, tpa))


def parse_arp(p):
    """(op, sha, spa, tha, tpa) of an Ethernet/IPv4 ARP packet, else None."""
    if len(p) < 28 or p[:6] != b"\x00\x01\x08\x00\x06\x04":
        return None
    return u16(p, 6), p[8:14], p[14:18], p[18:24], p[24:28]


# ---------------------------------------------------------------- IPv4
def ipv4(src, dst, proto, payload, ttl=64, ident=0):
    """An IPv4 packet with no options, DF set, header checksum filled in."""
    h = bytearray(b"\x45\x00" + be16(20 + len(payload)) + be16(ident) + b"\x40\x00"
                  + bytes((ttl, proto)) + b"\x00\x00" + src + dst)
    h[10:12] = be16(checksum(h))
    return bytes(h) + bytes(payload)


def parse_ipv4(p):
    """(src, dst, proto, data, ttl) of a whole, unfragmented IPv4 packet with a
    good header checksum, ignoring options and Ethernet padding; else None."""
    if len(p) < 20 or p[0] >> 4 != 4:
        return None
    ihl = (p[0] & 15) * 4
    total = u16(p, 2)
    if ihl < 20 or total < ihl or total > len(p) or checksum(p[:ihl]):
        return None
    if u16(p, 6) & 0x3FFF:                 # MF or a fragment offset
        return None
    return p[12:16], p[16:20], p[9], p[ihl:total], p[8]


# ---------------------------------------------------------------- ICMP echo
def icmp_echo(kind, ident, seq, data):
    h = bytes((kind, 0, 0, 0)) + be16(ident) + be16(seq)
    c = checksum(h + data)
    return h[:2] + be16(c) + h[4:] + bytes(data)


def parse_icmp_echo(p):
    """(type, ident, seq, data) of an ICMP echo with a good checksum, else None."""
    if len(p) < 8 or p[0] not in (ICMP_ECHO_REQUEST, ICMP_ECHO_REPLY) or p[1] or checksum(p):
        return None
    return p[0], u16(p, 4), u16(p, 6), p[8:]


# ---------------------------------------------------------------- UDP
def _pseudo(src, dst, length):
    return checksum(src + dst + bytes((0, PROTO_UDP)) + be16(length)) ^ 0xFFFF


def udp(src, dst, sport, dport, data):
    n = 8 + len(data)
    h = be16(sport) + be16(dport) + be16(n)
    c = checksum(h + b"\x00\x00" + data, _pseudo(src, dst, n)) or 0xFFFF
    return h + be16(c) + bytes(data)


def parse_udp(src, dst, p):
    """(sport, dport, data) of a UDP datagram with a good or absent checksum, else None."""
    if len(p) < 8:
        return None
    n = u16(p, 4)
    if n < 8 or n > len(p):
        return None
    if u16(p, 6) and checksum(p[:n], _pseudo(src, dst, n)):
        return None
    return u16(p, 0), u16(p, 2), p[8:n]


# ---------------------------------------------------------------- eth10_rx records
def decode_record(count, words):
    """Destination..FCS bytes from one eth10_rx record: `count` line bits in
    `words`, 24 per word in [31:8], first bit lowest, then the last k bits at
    the top of the last word. None if there is no preamble and SFD. Bits past
    the last whole byte are dropped, as a MAC drops dribble bits, and the FCS
    decides: the line unit's queue has no frame boundaries, so a core that is
    behind when a frame ends takes the bit from TP_IDL's falling edge into that
    frame's record. The FCS is not checked here."""
    full, rem = count // 24, count % 24
    if len(words) != full + 1:
        return None
    b = bytearray()
    for w in words[:full]:
        b += (w >> 8).to_bytes(3, "little")
    if rem:
        b += (words[full] >> (32 - rem)).to_bytes(3, "little")
    # the alternating preamble; the first repeated level is the SFD's last bit
    prev, start = None, None
    for i in range(min(count, 64)):
        x = (b[i >> 3] >> (i & 7)) & 1
        if x == prev:
            if x and i >= MIN_PREAMBLE_BITS + 7:
                j = i - 7                  # the byte ending here, first bit lowest
                sfd = ((b[j >> 3] | (b[(j >> 3) + 1] << 8)) >> (j & 7)) & 255
                if sfd == SFD:
                    start = i + 1
            break
        prev = x
    if start is None:
        return None
    n, s = (count - start) >> 3, start & 7
    o = start >> 3
    if not s:
        return bytes(b[o:o + n])
    return bytes(((b[o + i] >> s) | (b[o + i + 1] << (8 - s))) & 255 for i in range(n))


class Records:
    """Assemble eth10_rx FIFO words into records. feed(word) returns a
    (count, words) tuple at each trailer, or None. Storage is bounded."""
    MAX_WORDS = (64 + 8 * MAX_FRAME) // 24 + 2

    def __init__(self):
        self.words = []
        self.over = False
        self.malformed = 0

    def reset(self):
        self.words, self.over = [], False

    def feed(self, w):
        if w & 255 == 0:
            if len(self.words) >= self.MAX_WORDS:
                self.over = True
                self.words = []
            elif not self.over:
                self.words.append(w)
            return None
        words, over = self.words, self.over
        self.reset()
        count = w >> 16
        if over or w & 0xFFFF != 0x80 or len(words) != count // 24 + 1:
            self.malformed += 1
            return None
        return count, words


# ---------------------------------------------------------------- a station
class Station:
    """Answers ARP for its address, ICMP echo requests and UDP echo.

    handle(raw) takes a received frame and returns the reply frames (a list,
    usually empty or one). A frame with a bad FCS or of illegal size is dropped
    before anything looks inside it, and so is a frame for another station.
    `stats` counts why each frame went the way it did."""

    def __init__(self, addr, ipaddr, udp_port=UDP_ECHO_PORT):
        self.mac, self.ip, self.udp_port = bytes(addr), bytes(ipaddr), udp_port
        self.stats = {}

    def _count(self, name):
        self.stats[name] = self.stats.get(name, 0) + 1

    def handle(self, raw):
        self._count("rx")
        f = parse_frame(raw)
        if f is None:
            self._count("bad_fcs" if MIN_FRAME <= len(raw) <= MAX_FRAME else "bad_size")
            return []
        dst, src, kind, p = f
        if dst != self.mac and dst != BROADCAST:
            self._count("other_dst")
            return []
        if src[0] & 1:                     # a group address never sends
            self._count("bad_src")
            return []
        if kind == ETH_ARP:
            return self._arp(p)
        if kind == ETH_IP:
            return self._ip(src, p)
        self._count("other_type")
        return []

    def _arp(self, p):
        a = parse_arp(p)
        if a is None or a[0] != ARP_REQUEST or a[1][0] & 1 or a[4] != self.ip:
            self._count("arp_ignored")
            return []
        self._count("arp")
        return [arp_reply(self.mac, self.ip, a[1], a[2])]

    def _ip(self, src_mac, p):
        pkt = parse_ipv4(p)
        if pkt is None:
            self._count("bad_ip")
            return []
        src, dst, proto, data, _ = pkt
        if dst != self.ip or src == b"\xff\xff\xff\xff" or src == bytes(4):
            self._count("other_ip")
            return []
        if proto == PROTO_ICMP:
            e = parse_icmp_echo(data)
            if e is None or e[0] != ICMP_ECHO_REQUEST:
                self._count("icmp_ignored")
                return []
            self._count("icmp")
            reply = icmp_echo(ICMP_ECHO_REPLY, e[1], e[2], e[3])
            return [frame(src_mac, self.mac, ETH_IP, ipv4(self.ip, src, PROTO_ICMP, reply))]
        if proto == PROTO_UDP:
            u = parse_udp(src, dst, data)
            if u is None or u[1] != self.udp_port:
                self._count("udp_ignored")
                return []
            self._count("udp")
            reply = udp(self.ip, src, u[1], u[0], u[2])
            return [frame(src_mac, self.mac, ETH_IP, ipv4(self.ip, src, PROTO_UDP, reply))]
        self._count("other_proto")
        return []
