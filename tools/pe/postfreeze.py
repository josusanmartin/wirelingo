"""Host side of the post-freeze protocols (docs/post-freeze-protocols.md):
the words each firmware expects in its TX FIFO, the decoding of what it
pushes back, and pins-only models of the other end of each line.

Everything here was written after the RTL freeze (tag rc-01ce16d-t48ba). The
models see nothing but pin levels and times: they are the tests' own judges,
next to sigrok's decoders (pe.sigrok), which are third-party code.

Times are in ns throughout. An edge list is [(t_ns, level)] with the level
before the first edge given separately.
"""
from . import sim


# ---------------------------------------------------------------- capture

class PinLog:
    """Steps a ModelHost and records changes of probe(mh) (an int) after
    every clock, host writes included (a write is a clock of its own).
    log: [(t_ns, value)], starting with the value when the log was made."""

    def __init__(self, mh, probe, period_ns):
        self.mh, self.probe, self.period = mh, probe, period_ns
        self.log = [(mh.chip.cycle * period_ns, probe(mh))]

    def sample(self):
        v = self.probe(self.mh)
        if v != self.log[-1][1]:
            self.log.append((self.mh.chip.cycle * self.period, v))

    def step(self, n=1, until=None):
        mh, chip = self.mh, self.mh.chip
        for _ in range(n):
            chip.step(mh.ui, mh.bus_uio())
            self.sample()
            if until is not None and until(mh):
                return False
        return True

    def push(self, core, word):
        self.mh.push(core, word)
        self.sample()

    def write(self, addr, data):
        self.mh.write(addr, data)
        self.sample()

    def pop_all(self, core):
        out = []
        while self.mh.chip.cores[core].rxf:
            out.append(self.mh.read(sim.core_reg(core, sim.C_FIFO), pop=True))
            self.sample()
        return out

    def now(self):
        return self.mh.chip.cycle * self.period

    def edges(self, i=0):
        return bit_edges(self.log, i)


def pin_probe(*pins):
    """probe() for record(): the chip's drive of logical pins (uo, virtual and
    pushed-pull uio pins as pin_out; uio pins as the resolved bus), packed
    LSB = first pin."""
    def probe(mh):
        chip = mh.chip
        out, _ = chip.eff_pins()
        bus = None
        v = 0
        for i, p in enumerate(pins):
            if p < 8:
                if bus is None:
                    bus = mh.bus_uio()
                b = (bus >> p) & 1
            else:
                b = (out >> p) & 1
            v |= b << i
        return v
    return probe


def bit_edges(rec, i=0):
    """[(t, level)] of bit i of a record()."""
    out, last = [], None
    for t, v in rec:
        b = (v >> i) & 1
        if b != last:
            out.append((t, b))
            last = b
    return out


def runs(edges, t_end):
    """[(level, t_start, duration)] between the edges (the last up to t_end)."""
    out = []
    for (t, v), nxt in zip(edges, edges[1:] + [(t_end, None)]):
        out.append((v, t, nxt[0] - t))
    return out


def to_trace(trace, edges, name, t_end=None):
    """Add a channel's edges to a sigrok.Trace (times ns -> ps)."""
    for t, v in edges:
        trace.set(round(t * 1000), name, v)
    if t_end is not None:
        trace.end(round(t_end * 1000))
    return trace


# ---------------------------------------------------------------- DMX512

def dmx_words(slots):
    """TX FIFO words for one packet: header (n) and 4 slots per word."""
    slots = bytes(slots)
    if not 1 <= len(slots) <= 513:
        raise ValueError("a DMX512 packet has 1..513 slots (START code included)")
    w = [len(slots)]
    for i in range(0, len(slots), 4):
        w.append(int.from_bytes(slots[i:i + 4].ljust(4, b"\0"), "little"))
    return w


class DmxReceiver:
    """A DMX512 receiver that sees only the line (edges of one pin): BREAK is
    a low of at least 88 us (E1.11 receiver), MARK AFTER BREAK a high of at
    least 8 us, then slots of 11 bits at 4 us, each re-synchronised on its
    start bit's falling edge and sampled at mid-bit. Reports, per packet:
    break/MAB lengths, the slots, framing errors, and every bit edge's offset
    from the nominal grid (the transmitter's timing error)."""

    BIT = 4000.0

    def __init__(self, bit_ns=4000.0):
        self.BIT = bit_ns

    def decode(self, edges, t_end):
        rs = runs(edges, t_end)
        packets, i = [], 0
        while i < len(rs):
            lvl, t, d = rs[i]
            if lvl == 0 and d >= 88000:
                p = dict(t_break=t, brk=d, mab=None, slots=[], errors=[], grid_err=[], mark_gaps=[])
                packets.append(p)
                i += 1
                if i >= len(rs):
                    break
                p["mab"] = rs[i][2]
                if p["mab"] < 8000:
                    p["errors"].append(("short MAB", p["mab"]))
                i += 1
                prev_end = None
                while i < len(rs) and not (rs[i][0] == 0 and rs[i][2] >= 88000):
                    lvl, t, d = rs[i]
                    if lvl != 0:          # mark between slots
                        i += 1
                        continue
                    s0 = t                # start bit
                    if prev_end is not None:
                        p["mark_gaps"].append(s0 - prev_end)
                    # sample 11 bits at mid-bit from the run list
                    val, bits = 0, []
                    for k in range(11):
                        bits.append(self._level_at(rs, s0 + (k + 0.5) * self.BIT))
                    if bits[0] != 0:
                        p["errors"].append(("start", s0))
                    for k in range(8):
                        val |= bits[1 + k] << k
                    if bits[9] != 1 or bits[10] != 1:
                        p["errors"].append(("stop", s0))
                    p["slots"].append(val)
                    # every edge inside the slot sits on the 4 us grid
                    end = s0 + 9 * self.BIT
                    j = i
                    while j < len(rs) and rs[j][1] < end + self.BIT / 2:
                        if rs[j][1] > s0:
                            k = round((rs[j][1] - s0) / self.BIT)
                            p["grid_err"].append(rs[j][1] - s0 - k * self.BIT)
                        j += 1
                    prev_end = s0 + 11 * self.BIT
                    # skip the runs this slot covered
                    while i < len(rs) and rs[i][1] < s0 + 9 * self.BIT:
                        i += 1
                continue
            i += 1
        for a, b in zip(packets, packets[1:]):
            a["break_to_break"] = b["t_break"] - a["t_break"]
        return packets

    @staticmethod
    def _level_at(rs, t):
        lo, hi = 0, len(rs) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if rs[mid][1] <= t:
                lo = mid
            else:
                hi = mid - 1
        return rs[lo][0]


# ---------------------------------------------------------------- I2S

def i2s_word(left, right):
    """TX FIFO word for one stereo frame (16-bit samples, two's complement)."""
    return ((left & 0xFFFF) << 16) | (right & 0xFFFF)


class I2sReceiver:
    """A Philips-I2S receiver on (BCLK, WS, SD) levels: samples SD and WS on
    BCLK rising edges. The bit sampled on the rise where WS has changed is
    the previous word's LSB; that word belongs to the channel WS selected
    before the change (low = left). Words before the first WS change are
    dropped (their start was not seen)."""

    def decode(self, log, bclk=0, ws=1, sd=2):
        words, prev_clk, prev_ws, cur, nbits, started = [], None, None, 0, 0, False
        rises = []
        for t, v in log:
            c, w, d = (v >> bclk) & 1, (v >> ws) & 1, (v >> sd) & 1
            if prev_clk == 0 and c == 1:
                rises.append(t)
                cur, nbits = (cur << 1) | d, nbits + 1
                if prev_ws is not None and w != prev_ws:
                    if started:
                        words.append(("L" if prev_ws == 0 else "R", cur, nbits, t))
                    started, cur, nbits = True, 0, 0
                prev_ws = w
            prev_clk = c
        return words, rises


# ---------------------------------------------------------------- DShot

def dshot_frame(throttle, telemetry=0):
    """The 16-bit DShot frame (Betaflight's checksum: XOR of the three
    nibbles of value = throttle << 1 | telemetry)."""
    v = ((throttle & 0x7FF) << 1) | (telemetry & 1)
    csum = (v ^ (v >> 4) ^ (v >> 8)) & 0xF
    return (v << 4) | csum


def dshot_decode(edges, t_end, bit_ns, idle_ns=None):
    """Frames from the signal's edges: every bit starts with a rising edge, a
    high time above half a bit is a 1; a low longer than idle_ns (2 bits by
    default) ends a frame. Returns [(t_start, bits, highs, periods)] with
    highs (ns, per bit) and periods (ns, rising edge to rising edge inside
    the frame: one fewer than bits)."""
    idle_ns = 2 * bit_ns if idle_ns is None else idle_ns
    frames, cur = [], None
    for lvl, t, d in runs(edges, t_end):
        if lvl == 1:
            if cur is None:
                cur = (t, [], [], [])
                frames.append(cur)
            cur[1].append(1 if d > bit_ns / 2 else 0)
            cur[2].append(d)
            cur[3].append(t)
        elif cur is not None and d > idle_ns:
            cur = None
    return [(t0, bits, highs, [b - a for a, b in zip(rises, rises[1:])])
            for t0, bits, highs, rises in frames]


def bits_value(bits):
    v = 0
    for b in bits:
        v = (v << 1) | b
    return v


# ---------------------------------------------------------------- NEC infrared

NEC_U = 562.5e3        # ns, the NEC unit


def nec_word(address, command=0, mode="standard"):
    """nec_tx TX FIFO word. mode: standard (8-bit address), extended
    (16-bit address), repeat."""
    m = {"standard": 0, "extended": 1, "repeat": 2}[mode]
    return (address & 0xFFFF) | ((command & 0xFF) << 16) | (m << 24)


def nec_rx_record(w):
    """Decode an nec_rx RX FIFO word."""
    if w & 0x10000000:
        return {"kind": "error", "bits": (31 - (w & 0xFFFF)) & 0xFFFF}
    if w & 0x08000000:
        return {"kind": "repeat"}
    return {"kind": "frame", "address": w & 0xFFFF, "command": (w >> 16) & 0xFF,
            "command_ok": bool(w >> 24 & 1), "standard": bool(w >> 25 & 1)}


def nec_envelope(address, command=0, mode="standard", scale=1.0, stretch=0.0):
    """A remote's demodulated output as [(level, duration_ns)], active low
    (low = burst), for an independent NEC sender model: nominal NEC times
    times `scale`, every burst `stretch` ns longer and every space as much
    shorter (what a receiver module does)."""
    u = NEC_U * scale
    seq = []
    if mode == "repeat":
        seq = [(16, 4)]
    else:
        if mode == "standard":
            data = (address & 0xFF) | ((~address & 0xFF) << 8)
        else:
            data = address & 0xFFFF
        data |= ((command & 0xFF) | ((~command & 0xFF) << 8)) << 16
        seq = [(16, 8)] + [(1, 3 if (data >> i) & 1 else 1) for i in range(32)]
    seq.append((1, None))
    out = []
    for burst, space in seq:
        out.append((0, burst * u + stretch))
        if space is not None:
            out.append((1, space * u - stretch))
    return out


def nec_decode_envelope(rs, tol=0.25):
    """Decode NEC codes from runs [(level, t, duration)] of an active-low
    envelope with relative tolerance tol. Returns [(t, kind, data32)]."""
    near = lambda d, n: abs(d - n * NEC_U) <= tol * n * NEC_U
    out, i = [], 0
    while i + 1 < len(rs):
        lvl, t, d = rs[i]
        if lvl == 0 and near(d, 16):
            sp = rs[i + 1][2]
            if near(sp, 4) and i + 2 < len(rs) and near(rs[i + 2][2], 1):
                out.append((t, "repeat", None))
                i += 3
                continue
            if near(sp, 8):
                data, ok, j = 0, True, i + 2
                for k in range(32):
                    if j + 1 >= len(rs):
                        ok = False
                        break
                    b, s = rs[j][2], rs[j + 1][2]
                    if not near(b, 1):
                        ok = False
                        break
                    # burst + space is 2 u (0) or 4 u (1): judge the sum, as the
                    # receiver-module distortion moves time between them
                    tot = b + s
                    if near(tot, 2):
                        pass
                    elif near(tot, 4):
                        data |= 1 << k
                    else:
                        ok = False
                        break
                    j += 2
                if ok and j < len(rs) and near(rs[j][2], 1):
                    out.append((t, "frame", data))
                    i = j + 1
                    continue
        i += 1
    return out


class IrReceiverModule:
    """A TSOP-style IR receiver: output (active low) goes low once the
    38 kHz carrier has been present for on_periods periods, and high again
    once it has been absent for off_periods. step(t_ns, led) is called with
    the LED drive level in time order; returns the output level."""

    def __init__(self, f_carrier=38e3, on_periods=7, off_periods=9):
        self.per = 1e9 / f_carrier
        self.on_ns, self.off_ns = on_periods * self.per, off_periods * self.per
        self.last_rise = None
        self.burst_start = None
        self.prev = 0
        self.out = 1

    def step(self, t, led):
        if led and not self.prev:
            if self.last_rise is None or t - self.last_rise > 1.5 * self.per:
                self.burst_start = t
            self.last_rise = t
        self.prev = led
        present = self.last_rise is not None and t - self.last_rise <= 1.5 * self.per
        if present and self.burst_start is not None and t - self.burst_start >= self.on_ns:
            self.out = 0
        elif self.last_rise is not None and t - self.last_rise >= self.off_ns:
            self.out = 1
        return self.out


def carrier_bursts(edges, max_gap_ns):
    """Group a carrier's edges into bursts: [(t_first_rise, t_last_fall,
    n_periods)]; a gap over max_gap_ns between rises ends a burst."""
    out = []
    for t, v in edges:
        if v == 1:
            if not out or t - out[-1][1] > max_gap_ns:
                out.append([t, t, 0, t])
            out[-1][1] = t
            out[-1][2] += 1
        elif out:
            out[-1][3] = t
    return [(a, d, n) for a, _, n, d in out]


# ---------------------------------------------------------------- Wiegand

def wiegand_bits(n, data, raw=False):
    """The bits on the wire, as a '0'/'1' string, for n data bits (MSB
    first): with parity, an even-parity bit over the first n // 2 data bits
    first and an odd-parity bit over the rest last."""
    d = format(data & ((1 << n) - 1), f"0{n}b")
    if raw:
        return d
    h = n // 2
    pe = d[:h].count("1") & 1
    po = 1 - (d[h:].count("1") & 1)
    return f"{pe}{d}{po}"


def wiegand_words(n, data, raw=False):
    """wiegand_tx TX FIFO words for one frame."""
    if not 1 <= n <= 32 or (not raw and n < 2):
        raise ValueError("1..32 data bits (2..32 with parity)")
    return [n | (int(raw) << 8), (data & ((1 << n) - 1)) << (32 - n)]


def h10301(facility, card):
    """The 24 data bits of the 26-bit H10301 format."""
    return ((facility & 0xFF) << 16) | (card & 0xFFFF)


def wiegand_rx_frames(words):
    """Decode wiegand_rx's A, B, S triples."""
    out = []
    for a, b, s in zip(words[0::3], words[1::3], words[2::3]):
        n = s & 0xFF
        if n <= 32:
            bits = format(a, "032b")[32 - n:] if n else ""
        else:
            k = min(n - 32, 32)
            bits = format(a, "032b") + format(b, "032b")[32 - k:]
        out.append({"n": n, "bits": bits, "valid": bool(s >> 8 & 1), "parity_all": s >> 9 & 1,
                    "parity13": s >> 10 & 1, "parity17": s >> 11 & 1, "error": bool(s >> 12 & 1)})
    return out


def wiegand_decode(log, d0=0, d1=1, gap_ns=4e6):
    """Pins-only Wiegand decoder: a falling edge on D0 is a 0, on D1 a 1; a
    frame ends after gap_ns without one. Returns [(t_first, bits, widths,
    intervals)] with every pulse's low time and the bit-to-bit intervals."""
    frames, prev, falls = [], None, []
    for t, v in log:
        if prev is not None:
            for line, bit in ((d0, "0"), (d1, "1")):
                was, now = (prev >> line) & 1, (v >> line) & 1
                if was and not now:
                    falls.append([t, bit, None, line])
                elif not was and now:
                    for f in reversed(falls):
                        if f[3] == line and f[2] is None:
                            f[2] = t - f[0]
                            break
        prev = v
    cur = None
    for t, bit, w, _ in falls:
        if cur is None or t - cur[-1] > gap_ns:
            cur = [t, "", [], [], t]
            frames.append(cur)
        else:
            cur[3].append(t - cur[-1])
        cur[1] += bit
        cur[2].append(w)
        cur[-1] = t
    return [(f[0], f[1], f[2], f[3]) for f in frames]


# ---------------------------------------------------------------- LIN

def lin_pid(ident):
    """The protected identifier: ID with P0 = ID0^ID1^ID2^ID4 (bit 6) and
    P1 = !(ID1^ID3^ID4^ID5) (bit 7), LIN 2.x section 2.3.1.3."""
    b = [(ident >> i) & 1 for i in range(6)]
    p0 = b[0] ^ b[1] ^ b[2] ^ b[4]
    p1 = 1 - (b[1] ^ b[3] ^ b[4] ^ b[5])
    return (ident & 0x3F) | (p0 << 6) | (p1 << 7)


def lin_checksum(pid, data, enhanced=True):
    """Inverted 8-bit sum with end-around carry, over the data (classic) or
    the PID and the data (enhanced)."""
    s = pid if enhanced else 0
    for d in data:
        s += d
        if s > 255:
            s -= 255
    return (~s) & 0xFF


def _pack(data):
    return [int.from_bytes(bytes(data[i:i + 4]).ljust(4, b"\0"), "little") for i in range(0, len(data), 4)]


def lin_command(ident, data=None, n=None, enhanced=True):
    """lin_commander TX FIFO words: publish `data` (bytes) or, with data None,
    subscribe to n bytes (n = 0: the header alone)."""
    if data is not None:
        if not 1 <= len(data) <= 8:
            raise ValueError("1..8 data bytes")
        return [(ident & 0x3F) | (int(enhanced) << 8) | (1 << 9) | (len(data) << 12)] + _pack(data)
    if not 0 <= n <= 8:
        raise ValueError("0..8 bytes")
    return [(ident & 0x3F) | (int(enhanced) << 8) | (n << 12)]


def lin_stage(ident, data, enhanced=True):
    """lin_responder TX FIFO words: a response staged for `ident`."""
    if not 1 <= len(data) <= 8:
        raise ValueError("1..8 data bytes")
    return [(ident & 0x3F) | (int(enhanced) << 8) | (len(data) << 12)] + _pack(data)


def lin_records(words):
    """Group LIN RX FIFO records (either firmware) into frames: dicts with
    header (responder: T, pid, parity_error), bytes, and the end flags."""
    frames, cur = [], None
    for w in words:
        kind = (w >> 12) & 0xF
        if kind == 1:
            cur = {"T": w >> 16, "pid": w & 0xFF, "parity_error": bool(w >> 8 & 1), "bytes": b""}
            frames.append(cur)
        elif kind == 2:
            if cur is None:
                cur = {"bytes": b""}
                frames.append(cur)
            cur["bytes"] += bytes([w & 0xFF])
        elif kind == 3:
            if cur is None:
                cur = {"bytes": b""}
                frames.append(cur)
            cur.update(count=(w >> 4) & 0xF, flag0=bool(w & 1), flag1=bool(w & 2),
                       framing_error=bool(w & 4), published=bool(w & 8))
            cur = None
        else:
            raise ValueError(f"not a LIN record: {w:#x}")
    return frames


def lin_waveform(ident, data=None, bit_ns=1e9 / 19200, pid=None, checksum=None, enhanced=True,
                 brk_bits=13, delim_bits=1, space_bits=0.0, response_space_bits=1.0):
    """A LIN frame as [(level, duration_ns)]: break, delimiter, sync, PID and
    optionally a response (data + checksum). For an independent commander
    (or commander + responder) model; pid/checksum override the right ones."""
    pid = lin_pid(ident) if pid is None else pid
    seq = [(0, brk_bits * bit_ns), (1, delim_bits * bit_ns)]

    def byte(b, space):
        out = [(0, bit_ns)] + [((b >> i) & 1, bit_ns) for i in range(8)] + [(1, bit_ns)]
        if space:
            out.append((1, space * bit_ns))
        return out
    seq += byte(0x55, space_bits) + byte(pid, 0)
    if data is not None:
        seq.append((1, response_space_bits * bit_ns))
        cks = lin_checksum(pid, data, enhanced) if checksum is None else checksum
        for b in list(data) + [cks]:
            seq += byte(b, space_bits)
    merged = []
    for lvl, d in seq:
        if merged and merged[-1][0] == lvl:
            merged[-1] = (lvl, merged[-1][1] + d)
        elif d > 0:
            merged.append((lvl, d))
    return merged


def lin_decode(edges, t_end, bit_ns, brk_bits=11):
    """Pins-only LIN decoder of the bus: a low of at least brk_bits starts a
    frame; the bytes after it are 8N1 at bit_ns, each re-synchronised on its
    start bit and sampled at mid-bit. Returns [{t, brk_ns, bytes, stop_errors,
    starts}] (starts: each byte's start-bit time)."""
    rs = runs(edges, t_end)
    frames, cur = [], None
    level_at = DmxReceiver._level_at
    i = 0
    while i < len(rs):
        lvl, t, d = rs[i]
        if lvl == 0 and d >= brk_bits * bit_ns:
            cur = {"t": t, "brk_ns": d, "bytes": bytearray(), "stop_errors": 0, "starts": []}
            frames.append(cur)
            i += 1
            continue
        if lvl == 0 and cur is not None:
            s0 = t
            v = 0
            for k in range(8):
                v |= level_at(rs, s0 + (1.5 + k) * bit_ns) << k
            if level_at(rs, s0 + 9.5 * bit_ns) != 1:
                cur["stop_errors"] += 1
            cur["bytes"].append(v)
            cur["starts"].append(s0)
            while i < len(rs) and rs[i][1] < s0 + 9.5 * bit_ns:
                i += 1
            continue
        i += 1
    for f in frames:
        f["bytes"] = bytes(f["bytes"])
    return frames


class LinNode:
    """A pins-only LIN responder model at its own bit time: sees the bus
    level at each step(t_ns, bus), returns its TXD level. It finds a break
    (low for >= 11 of its bits), receives sync and PID (8N1, mid-bit), and if
    the ID is in `responses` sends that response after one bit of space."""

    def __init__(self, bit_ns, responses=None):
        self.bit = bit_ns
        self.responses = dict(responses or {})
        self.low_since = None
        self.state = "idle"
        self.tx = []            # [(t_until, level)] scheduled output
        self.rx_bytes = []
        self.byte_start = None
        self.prev = 1
        self.seen = []          # (pid, responded)

    def step(self, t, bus):
        if self.prev == 1 and bus == 0:
            self.low_since = t
            if self.state in ("sync", "pid") and self.byte_start is None:
                self.byte_start = t
        if self.prev == 0 and bus == 1 and self.low_since is not None:
            if t - self.low_since >= 11 * self.bit and not self.tx:
                self.state, self.rx_bytes, self.byte_start = "sync", [], None
        self.prev = bus
        if self.byte_start is not None and t >= self.byte_start + 9.5 * self.bit:
            self.byte_start = None
            self.rx_bytes.append(self._byte)
            if self.state == "sync":
                self.state = "pid"
            elif self.state == "pid":
                self.state = "idle"
                pid = self.rx_bytes[-1]
                resp = self.responses.get(pid & 0x3F)
                self.seen.append((pid, resp is not None))
                if resp is not None and pid == lin_pid(pid & 0x3F):
                    data, enh = resp
                    wave = []
                    for b in list(data) + [lin_checksum(pid, data, enh)]:
                        wave += [0] + [(b >> i) & 1 for i in range(8)] + [1]
                    t0 = t + 1.5 * self.bit           # mid-stop + 1 bit of space
                    self.tx = [(t0 + (k + 1) * self.bit, lvl) for k, lvl in enumerate(wave)]
        if self.byte_start is not None:
            k = (t - self.byte_start) / self.bit
            if not hasattr(self, "_samples") or self._sb != self.byte_start:
                self._sb, self._samples, self._byte = self.byte_start, set(), 0
            for j in range(8):
                if j not in self._samples and k >= 1.5 + j:
                    self._samples.add(j)
                    self._byte |= bus << j
        while self.tx and t >= self.tx[0][0]:
            self.tx.pop(0)
        if self.tx and t >= self.tx[0][0] - self.bit:
            return self.tx[0][1]
        return 1


# ---------------------------------------------------------------- bidirectional DShot

GCR = [0x19, 0x1B, 0x12, 0x13, 0x1D, 0x15, 0x16, 0x17, 0x1A, 0x09, 0x0A, 0x0B, 0x1E, 0x0D, 0x0E, 0x0F]
GCR_DECODE = {c: i for i, c in enumerate(GCR)}


def dshot_bidir_frame(throttle, telemetry=0):
    """The inverted-checksum frame of bidirectional DShot."""
    v = ((throttle & 0x7FF) << 1) | (telemetry & 1)
    return (v << 4) | ((~(v ^ (v >> 4) ^ (v >> 8))) & 0xF)


def dshot_bidir_defines(f_clk, esc_pin=0, rtmo_us=100, gap_us=50):
    """dshot_bidir.pasm defines for a clock of f_clk (CLKDIV 1)."""
    t = lambda ns: round(ns * f_clk / 1e9)
    return {"ESC": esc_pin, "BIT": t(1e9 / 600e3), "T0": t(625), "T1": t(1250), "RBIT": round(f_clk / 750e3),
            "RTMO": t(rtmo_us * 1e3), "GAP": t(gap_us * 1e3)}


def erpm_telemetry(period_us):
    """The 16-bit eRPM telemetry value for an eRPM period: e (3 bits),
    m (9 bits) with period = m << e, and the checksum nibble."""
    e = 0
    while period_us >> e > 511:
        e += 1
    if e > 7:
        raise ValueError("period too long")
    v12 = (e << 9) | (period_us >> e)
    v = v12 << 4
    return v | ((~((v12 >> 8) ^ (v12 >> 4) ^ v12)) & 0xF)


def telemetry_levels(value16):
    """The 21 line levels an ESC sends for a telemetry value: 4 nibbles ->
    GCR quintets (20 bits), each 1 a transition, starting from l[0] = 0."""
    gcr = 0
    for k in (12, 8, 4, 0):
        gcr = (gcr << 5) | GCR[(value16 >> k) & 0xF]
    levels = [0]
    for i in range(19, -1, -1):
        levels.append(levels[-1] ^ ((gcr >> i) & 1))
    return levels


def dshot_telemetry(record):
    """Decode a dshot_bidir RX record: None (no reply), or dict with value,
    checksum ok, e, m, period_us; 'gcr_error' if a quintet is not GCR."""
    count = record & 0xFF
    if count == 0:
        return None
    levels = (record >> 8) & 0x1FFFFF
    gcr = (levels ^ (levels >> 1)) & 0xFFFFF
    v = 0
    for k in (15, 10, 5, 0):
        q = (gcr >> k) & 0x1F
        if q not in GCR_DECODE:
            return {"gcr_error": True, "count": count, "levels": levels}
        v = (v << 4) | GCR_DECODE[q]
    c = v ^ (v >> 8)
    c ^= c >> 4
    e, m = v >> 13, (v >> 4) & 0x1FF
    return {"value": v, "checksum_ok": (c & 0xF) == 0xF, "e": e, "m": m, "period_us": m << e, "count": count}


class BidirEsc:
    """A pins-only bidirectional-DShot ESC: on the one wire (idle high,
    pulled up) it times each frame's low pulses (a 1 if low for over half a
    bit), checks the inverted checksum, and turn_ns after the frame's last bit
    drives a telemetry reply (21 levels at 750 kbit/s * rate) taken from
    `replies`, then releases the line. step(t, line) -> (drive, level);
    `contention` counts samples where the line was low while it drove high
    (someone else driving)."""

    def __init__(self, replies, turn_ns=30e3, rate=1.0, bit_ns=1e9 / 600e3):
        self.replies = list(replies)
        self.turn, self.rbit, self.bit = turn_ns, 1e9 / 750e3 / rate, bit_ns
        self.prev, self.fall, self.bits, self.last_rise = 1, None, [], None
        self.frames = []           # (t, frame16, checksum ok)
        self.tx = None             # (t0, levels)
        self.contention = 0
        self.sent = []
        self.drove = None          # the level driven on the previous step

    def step(self, t, line):
        if self.drove == 1 and line == 0:
            self.contention += 1               # it drove high, the wire was low
        self.drove = None
        if self.tx is not None:
            t0, lv = self.tx
            k = int((t - t0) // self.rbit)
            if t < t0:
                pass
            elif k < len(lv):
                self.prev = line
                self.drove = lv[k]
                return 1, lv[k]
            else:
                self.tx = None
        if self.prev == 1 and line == 0:
            self.fall = t
        elif self.prev == 0 and line == 1 and self.fall is not None:
            self.bits.append(1 if t - self.fall > self.bit / 2 else 0)
            self.last_rise = t
            self.fall = None
            if len(self.bits) == 16:
                f = int("".join(map(str, self.bits)), 2)
                v = f >> 4
                ok = (f & 0xF) == ((~(v ^ (v >> 4) ^ (v >> 8))) & 0xF)
                self.frames.append((t, f, ok))
                self.bits = []
                if ok and self.replies:
                    r = self.replies.pop(0)
                    if r is not None:
                        # the frame's last bit ends ~ (bit - low) after this rise
                        self.tx = (t + (self.bit - (1250 if f & 1 else 625)) + self.turn,
                                   telemetry_levels(r))
                        self.sent.append(r)
        if self.bits and self.last_rise is not None and t - self.last_rise > 3 * self.bit:
            self.bits = []                     # a partial frame
        self.prev = line
        return 0, 1
