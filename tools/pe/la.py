"""Logic-analyzer host side: la_capture records -> edges -> VCD and I2C decode.

    words = drained RX words of the core running firmware/la_capture.pasm
    cap = decode_records(words)              # Capture: edges in ticks, gaps
    open("bus.vcd", "w").write(to_vcd(cap, {0: "SDA", 1: "SCL"}, ns_per_tick=20))
    events = i2c_events(cap, sda=0, scl=1)   # START / byte+ACK / STOP
    print("\\n".join(i2c_summary(events)))   # "write to 0x50: 10" ...

Command line (one hex word per line, as a host would log them):

    PYTHONPATH=tools python -m pe.la words.txt --vcd bus.vcd --i2c SDA=0,SCL=1 [--ns-per-tick 20]

Timing rules (see the firmware header): samples every POLL ticks, the first
sample after a record is HANDLER ticks after the record's sample (IDLE_HANDLER
after the last sample of an idle round).
"""
from dataclasses import dataclass, field

POLL = 5                    # ticks per sample in the poll loop
HANDLER = 12                # change sample -> next sample
IDLE_HANDLER = 14           # last sample of an idle round -> next sample
ROUND = 0x10000             # samples per R2 round

M_FIRST, M_CHANGE, M_IDLE, M_GAP_CHANGE, M_GAP_IDLE = 0xF0, 0xFF, 0xFE, 0xFC, 0xF8
MARKERS = {M_FIRST, M_CHANGE, M_IDLE, M_GAP_CHANGE, M_GAP_IDLE}


@dataclass
class Capture:
    edges: list = field(default_factory=list)   # (tick, state): the first state, then each change
    gaps: list = field(default_factory=list)    # tick of each record that followed a gap
    end: int = 0                                # last instant covered (ticks)
    exact: bool = True                          # no gaps: every tick accounted for


def decode_records(words):
    """Records -> Capture. Zero words (empty-FIFO reads) are skipped. Times are
    ticks from the start sample. After a gap the time base continues as if the
    wait had taken no time, so times after it are lower bounds."""
    cap = Capture()
    base = None          # B: the first sample after the previous record
    state = None
    for w in words:
        if w == 0:
            continue
        m, s, c = w >> 24, (w >> 16) & 0xFF, w & 0xFFFF
        if m not in MARKERS:
            raise ValueError(f"not an la_capture record: {w:#010x}")
        if m == M_FIRST:
            if base is not None:
                raise ValueError("first record in the middle of a capture")
            cap.edges.append((0, s))
            base, state = HANDLER, s
            continue
        if base is None:
            raise ValueError("capture does not start with a first record")
        if m in (M_GAP_CHANGE, M_GAP_IDLE):
            cap.gaps.append(base)
            cap.exact = False
        if m in (M_IDLE, M_GAP_IDLE):
            if s != state:
                raise ValueError(f"idle record changed the state: {w:#010x}")
            last = base + POLL * (ROUND - 1)
            cap.end = last
            base = last + IDLE_HANDLER
        else:
            t = base + POLL * (0xFFFF - c)
            if s == state:
                raise ValueError(f"change record without a change: {w:#010x}")
            cap.edges.append((t, s))
            cap.end = t
            base, state = t + HANDLER, s
    return cap


def channel_edges(cap, ch):
    """[(tick, level)] for one channel: its first level, then each change."""
    out = []
    for t, s in cap.edges:
        v = (s >> ch) & 1
        if not out or out[-1][1] != v:
            out.append((t, v))
    return out


def to_vcd(cap, names, ns_per_tick=1.0, timescale_ns=1):
    """VCD text. names: {channel: name}."""
    ids = {ch: chr(33 + i) for i, ch in enumerate(sorted(names))}
    lines = [f"$timescale {timescale_ns} ns $end", "$scope module la $end"]
    lines += [f"$var wire 1 {ids[ch]} {names[ch]} $end" for ch in sorted(names)]
    lines += ["$upscope $end", "$enddefinitions $end"]
    prev = None
    for t, s in cap.edges:
        changed = [ch for ch in sorted(names) if prev is None or ((s ^ prev) >> ch) & 1]
        if changed:
            lines.append(f"#{round(t * ns_per_tick / timescale_ns)}")
            lines += [f"{(s >> ch) & 1}{ids[ch]}" for ch in changed]
        prev = s
    lines.append(f"#{round(max(cap.end, cap.edges[-1][0] if cap.edges else 0) * ns_per_tick / timescale_ns)}")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ I2C

def i2c_events(cap, sda, scl):
    """Bus events from a capture: ("S", t), ("P", t), ("B", t, byte, ack) with
    ack True for an ACK (SDA low on the 9th clock). A record in which SDA and
    SCL both changed is ordered as a well-formed bus would have done it: SCL
    falls before SDA changes, SDA changes before SCL rises."""
    ev = []
    bits = None                       # None: no START seen yet
    psda = pscl = None
    for t, s in cap.edges:
        nsda, nscl = (s >> sda) & 1, (s >> scl) & 1
        if psda is None:
            psda, pscl = nsda, nscl
            continue
        steps = []
        if pscl and not nscl:
            steps.append(("scl", 0))
        if nsda != psda:
            steps.append(("sda", nsda))
        if nscl and not pscl:
            steps.append(("scl", 1))
        for what, v in steps:
            if what == "sda":
                if pscl:              # SDA changed while SCL high: START or STOP
                    if v == 0:
                        ev.append(("S", t))
                        bits = []
                    else:
                        ev.append(("P", t))
                        bits = None
                psda = v
            else:
                if v and bits is not None:   # SCL rise: sample SDA
                    bits.append(psda)
                    if len(bits) == 9:
                        byte = int("".join(map(str, bits[:8])), 2)
                        ev.append(("B", t, byte, bits[8] == 0))
                        bits = []
                pscl = v
    return ev


def i2c_transactions(events):
    """Group events into transfers: dicts with addr (7-bit), rw ('W'/'R'),
    data (bytes after the address), acks (per data byte), addr_ack, and how it
    ended ('Sr' or 'P'; None if the capture ended first)."""
    out, cur = [], None
    for e in events:
        if e[0] == "S":
            if cur:
                cur["end"] = "Sr"
                out.append(cur)
            cur = {"addr": None}
        elif e[0] == "P":
            if cur:
                cur["end"] = "P"
                out.append(cur)
            cur = None
        elif cur is not None:
            _, t, b, ack = e
            if cur["addr"] is None:
                cur.update(addr=b >> 1, rw="R" if b & 1 else "W", addr_ack=ack, data=[], acks=[], end=None, t=t)
            else:
                cur["data"].append(b)
                cur["acks"].append(ack)
    if cur and cur["addr"] is not None:
        out.append(cur)
    return [x for x in out if x.get("addr") is not None]


def i2c_summary(events):
    """Human-readable transcript, one line per transfer."""
    lines = []
    for x in i2c_transactions(events):
        hexes = " ".join(f"{b:02X}" for b in x["data"])
        if not x["addr_ack"]:
            lines.append(f"{'write to' if x['rw'] == 'W' else 'read from'} 0x{x['addr']:02X}: address NACKed")
        elif x["rw"] == "W":
            lines.append(f"write to 0x{x['addr']:02X}: {hexes}".rstrip())
        else:
            lines.append(f"read {len(x['data'])} bytes from 0x{x['addr']:02X}: {hexes}".rstrip())
    return lines


def i2c_register_image(events):
    """What a register-pointer device (EEPROM-style) exposed: {addr7: {reg: byte}}
    from "write pointer [data...]" and following reads. Writes are recorded too
    (a device that stores them would read them back)."""
    image, ptr = {}, {}
    for x in i2c_transactions(events):
        if not x["addr_ack"]:
            continue
        mem = image.setdefault(x["addr"], {})
        if x["rw"] == "W":
            if x["data"]:
                p = x["data"][0]
                for b in x["data"][1:]:
                    mem[p] = b
                    p = (p + 1) & 0xFF
                ptr[x["addr"]] = p
        else:
            p = ptr.get(x["addr"])
            for b in x["data"]:
                if p is not None:
                    mem[p] = b
                    p = (p + 1) & 0xFF
            ptr[x["addr"]] = p
    return image


def main(argv=None):
    import argparse
    import sys
    ap = argparse.ArgumentParser(description="decode la_capture records")
    ap.add_argument("words", help="file with one hex record per line ('-' = stdin)")
    ap.add_argument("--vcd", help="write a VCD file")
    ap.add_argument("--ns-per-tick", type=float, default=20.0, help="tick length (20 = 50 MHz, CLKDIV 1)")
    ap.add_argument("--names", default="", help="channel names, e.g. SDA=0,SCL=1 (default ch0..ch7)")
    ap.add_argument("--i2c", help="decode I2C: SDA=<ch>,SCL=<ch>")
    a = ap.parse_args(argv)
    f = sys.stdin if a.words == "-" else open(a.words)
    words = [int(x, 16) for x in f.read().split()]
    cap = decode_records(words)
    print(f"{len(cap.edges)} states, {cap.end * a.ns_per_tick / 1000:.1f} us"
          + ("" if cap.exact else f", {len(cap.gaps)} gap(s): times after them are lower bounds"))
    names = {}
    for spec in filter(None, (a.names or a.i2c or "").split(",")):
        n, ch = spec.split("=")
        names[int(ch)] = n
    if not names:
        used = 0
        for _, s in cap.edges:
            used |= s
        names = {ch: f"ch{ch}" for ch in range(8) if used >> ch & 1} or {0: "ch0"}
    if a.vcd:
        with open(a.vcd, "w") as out:
            out.write(to_vcd(cap, names, a.ns_per_tick))
    if a.i2c:
        ch = dict(kv.split("=") for kv in a.i2c.split(","))
        for line in i2c_summary(i2c_events(cap, int(ch["SDA"]), int(ch["SCL"]))):
            print(line)


if __name__ == "__main__":
    main()
