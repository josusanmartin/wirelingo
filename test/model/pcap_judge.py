"""Judge a pcap with scapy, in its own interpreter (a venv with scapy; see
test_eth_stack.py). Frames carry their FCS. Prints one JSON object per frame:
what scapy says it is and whether scapy's own recomputed checksums agree.
Nothing from pe is imported here."""
import json
import sys
import zlib

from scapy.all import ARP, ICMP, IP, UDP, Ether, Raw, rdpcap


def judge(raw):
    body, trailer = raw[:-4], raw[-4:]
    v = {"len": len(raw), "fcs_ok": zlib.crc32(body).to_bytes(4, "little") == trailer}
    p = Ether(body)
    if ARP in p:
        a = p[ARP]
        v.update(kind="arp-who-has" if a.op == 1 else "arp-is-at", hwsrc=a.hwsrc, psrc=a.psrc, hwdst=a.hwdst, pdst=a.pdst)
        v["checksums_ok"] = True
        return v
    i = p[IP]
    ok = i.chksum == IP(bytes(i.__class__(src=i.src, dst=i.dst, ttl=i.ttl, id=i.id, flags=i.flags, proto=i.proto, len=i.len)))[IP].chksum
    if ICMP in p:
        c = p[ICMP]
        v.update(kind="echo-request" if c.type == 8 else "echo-reply", id=c.id, seq=c.seq)
        load = p[Raw].load if Raw in p else b""
        v["payload"] = load.hex()
        check = ICMP(type=c.type, code=c.code, id=c.id, seq=c.seq) / Raw(load)
        ok = ok and ICMP(bytes(check)).chksum == c.chksum
    else:
        u = p[UDP]
        load = p[Raw].load
        v.update(kind="udp", sport=u.sport, dport=u.dport, load=load.decode(errors="replace"))
        check = IP(src=i.src, dst=i.dst) / UDP(sport=u.sport, dport=u.dport) / Raw(load)
        ok = ok and IP(bytes(check))[UDP].chksum == u.chksum
    v["checksums_ok"] = bool(ok)
    return v


print(json.dumps([judge(bytes(f)) for f in rdpcap(sys.argv[1])]))
