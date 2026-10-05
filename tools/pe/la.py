"""Logic-analyzer host side: la_capture records -> edges -> VCD and I2C decode.

    words = drained RX words of the core running firmware/la_capture.pasm
    cap = decode_records(words, mask)        # Capture: edges in ticks, gaps
    open("bus.vcd", "w").write(to_vcd(cap, {0: "SDA", 1: "SCL"}, ns_per_tick=20))
    events = i2c_events(cap, sda=0, scl=1)   # START / byte+ACK / STOP
    print("\\n".join(i2c_summary(events)))   # "write to 0x50: 10" ...

Command line (one hex word per line, as a host would log them):

    PYTHONPATH=tools python -m pe.la words.txt --mask 0x03 --vcd bus.vcd --i2c SDA=0,SCL=1 [--ns-per-tick 20]

Timing rules (see the firmware header): `wait pins != r0` timestamps a change
to the tick with its timeout counter; the first WAIT starts START ticks after
the start sample, and each WAIT starts LOOP ticks after the previous one
completed.
"""
from dataclasses import dataclass, field

START = 7                   # start sample -> first WAIT attempt
LOOP = 7                    # a WAIT completes at W -> the next one starts at W + LOOP

M_FIRST, M_CHANGE, M_GAP = 0xF0, 0xFF, 0xFC
MARKERS = {M_FIRST, M_CHANGE, M_GAP}


@dataclass
class Capture:
    edges: list = field(default_factory=list)   # (tick, state): the first state, then each change
    gaps: list = field(default_factory=list)    # tick after which each gap happened
    idles: list = field(default_factory=list)   # ticks of idle records (65536 ticks without a change)
    pulses: list = field(default_factory=list)  # ticks of pulses over before they were sampled
    end: int = 0                                # last instant covered (ticks)
    exact: bool = True                          # no gaps: every tick accounted for


def decode_records(words, mask=0xFF):
    """Records -> Capture. `mask` is the MASK la_capture was assembled with.
    Zero words (empty-FIFO reads) are skipped. Times are ticks from the start
    sample. After a gap the time base continues as if the wait had taken no
    time, so times after it are lower bounds."""
    cap = Capture()
    a = None             # A: the current WAIT's first attempt
    state = None
    for w in words:
        if w == 0:
            continue
        m, s, c = w >> 24, ((w >> 16) & 0xFF) & mask, w & 0xFFFF
        if m not in MARKERS:
            raise ValueError(f"not an la_capture record: {w:#010x}")
        if m == M_FIRST:
            if a is not None:
                raise ValueError("first record in the middle of a capture")
            cap.edges.append((0, s))
            a, state = START, s
            continue
        if a is None:
            raise ValueError("capture does not start with a first record")
        if m == M_GAP:
            cap.gaps.append(a)
            cap.exact = False
            continue
        t = a + (0xFFFF - c)
        if s != state:
            cap.edges.append((t, s))
            state = s
        elif c == 0:
            cap.idles.append(t)
        else:
            cap.pulses.append(t)
        cap.end = t
        a = t + LOOP
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
    ap.add_argument("--mask", type=lambda v: int(v, 0), default=0xFF, help="la_capture's MASK (default 0xFF)")
    ap.add_argument("--vcd", help="write a VCD file")
    ap.add_argument("--ns-per-tick", type=float, default=20.0, help="tick length (20 = 50 MHz, CLKDIV 1)")
    ap.add_argument("--names", default="", help="channel names, e.g. SDA=0,SCL=1 (default ch0..ch7)")
    ap.add_argument("--i2c", help="decode I2C: SDA=<ch>,SCL=<ch>")
    a = ap.parse_args(argv)
    f = sys.stdin if a.words == "-" else open(a.words)
    words = [int(x, 16) for x in f.read().split()]
    cap = decode_records(words, a.mask)
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
