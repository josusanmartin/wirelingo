"""Host side of the second round of post-freeze protocols
(docs/post-freeze-protocols.md): DALI, Modbus RTU and MIDI. As in
pe.postfreeze: the words each firmware expects in its TX FIFO, the decoding
of what it pushes back, and pins-only models of the other end of each line.

Everything here was written after the RTL freeze (tag rc-93c2137-t48ba). The
models see nothing but pin levels and times. Times are in ns; waveforms are
[(level, duration_ns)], edge lists [(t_ns, level)] (pe.postfreeze.bit_edges).
"""
from .postfreeze import runs


def waveform_edges(wave, t0=0.0, idle=1):
    """[(level, duration)] -> edges [(t, level)] starting at t0 (the level
    before t0 is `idle`); also returns the end time."""
    out, t, last = [], t0, idle
    for lvl, d in wave:
        if lvl != last:
            out.append((t, lvl))
            last = lvl
        t += d
    return out, t


class Player:
    """Plays waveforms onto one line: schedule(t, wave) queues [(level,
    duration)] from time t; level(t) is the line level at t (idle between
    waveforms). Waveforms must not overlap."""

    def __init__(self, idle=1):
        self.idle, self.edges, self.k = idle, [], 0

    def schedule(self, t, wave):
        e, t_end = waveform_edges(wave, t, self.idle)
        if e and e[-1][1] != self.idle:
            e.append((t_end, self.idle))
        self.edges += e
        return t_end

    def level(self, t):
        while self.k < len(self.edges) and self.edges[self.k][0] <= t:
            self.k += 1
        return self.edges[self.k - 1][1] if self.k else self.idle

    def busy(self, t):
        return self.k < len(self.edges)


# ================================================================== DALI

DALI_TE = 1e9 / 2400        # ns, the half bit (416.67 us)


def dali_defines(tx=8, rx=16, te=32):
    return {"TX": tx, "RX": rx, "TE": te}


def dali_clkdiv(f_clk, te=32):
    from . import host
    return host.clkdiv_for(f_clk, 2400 * te)


def dali_word(data, n=16, reply=False):
    """dali_ctrl TX FIFO word: n data bits (MSB first) left-aligned in
    [31:8], n in [4:0], [5] receive a backward frame."""
    if not 1 <= n <= 24:
        raise ValueError("1..24 data bits")
    return ((data & ((1 << n) - 1)) << (32 - n)) | n | (int(reply) << 5)


def dali_records(words):
    """Decode dali_ctrl's (bits, status) pairs."""
    out = []
    for bits, st in zip(words[0::2], words[1::2]):
        n = st & 0xFF
        r = {"bits": bits, "n": n, "violation": bool(st >> 8 & 1), "no_answer": bool(st >> 9 & 1),
             "settle_ticks": st >> 16}
        r["answer"] = bits & 0xFF if n == 9 and bits >> 8 == 1 and not r["violation"] else None
        out.append(r)
    return out


def dali_wave(value, n, te=DALI_TE, low_extra=0.0, halves=None):
    """A DALI frame as [(level, duration)]: start bit, n bits MSB first,
    bi-phase (a 1 is low then high), idle high after. te: the sender's half
    bit; low_extra: every low phase that much longer (and the following high
    shorter; real buses' asymmetric edges); halves: per-half-bit durations to
    override (a list of 2 * (n + 1) floats or None), for broken senders."""
    bits = [1] + [(value >> (n - 1 - i)) & 1 for i in range(n)]
    lv = []
    for b in bits:
        lv += [1 - b, b]
    d = list(halves) if halves is not None else [te] * len(lv)
    wave = []
    for i, (lvl, dur) in enumerate(zip(lv, d)):
        dur += low_extra if lvl == 0 else (-low_extra if i and lv[i - 1] == 0 else 0)
        if wave and wave[-1][0] == lvl:
            wave[-1] = (lvl, wave[-1][1] + dur)
        else:
            wave.append((lvl, dur))
    return wave


DALI_RX = {"half": (333.3e3, 500e3), "double": (666.7e3, 1000e3), "stop": 2400e3}   # IEC 62386-101 ed.2 tables 18, 19


def dali_decode(edges, t_end, limits=DALI_RX):
    """Pins-only DALI decoder (idle high): every run between edges must be a
    half or a double half bit (the standard's receiver ranges); a high run of
    at least the stop condition ends a frame. Returns [{t, bits (values,
    start bit first), value (data bits), n (data bits), runs (durations),
    error, t_end_bit (the end of the last bit, by the sender's own timing)}]."""
    rs = runs(edges, t_end)
    frames, cur = [], None
    for lvl, t, d in rs:
        if cur is None:
            if lvl == 0:
                cur = {"t": t, "levels": [], "runs": [], "error": None, "t_last": t}
                frames.append(cur)
            else:
                continue
        if lvl == 1 and d >= limits["stop"]:
            cur["t_last"] = t
            cur = None
            continue
        cur["runs"].append(d)
        h0, h1 = limits["half"]
        d0, d1 = limits["double"]
        if h0 <= d <= h1:
            cur["levels"].append(lvl)
        elif d0 <= d <= d1:
            cur["levels"] += [lvl, lvl]
        elif cur["error"] is None:
            cur["error"] = f"run of {d / 1e3:.1f} us"
    for f in frames:
        lv = f.pop("levels")
        if len(lv) % 2:
            lv.append(1)              # the last 1's high half, merged into the idle line
        bits = []
        for a, b in zip(lv[0::2], lv[1::2]):
            if a == b and f["error"] is None:
                f["error"] = "no mid-bit transition"
            bits.append(b)
        if not bits or bits[0] != 1:
            f["error"] = f["error"] or "no start bit"
        f["bits"] = bits
        f["n"] = max(len(bits) - 1, 0)
        f["value"] = int("".join(map(str, bits[1:])) or "0", 2)
        f["t_end_bit"] = f["t"] + 2 * DALI_TE * len(bits)
    return frames


class DaliGear:
    """A pins-only DALI control gear (or several: anything answering
    backward frames). step(t_ns, bus) sees the bus level and returns the
    level it drives (1 = released). It decodes forward frames with
    dali_decode's rules once the bus has been idle for the stop condition,
    and answers those for which answer(value, n) returns a byte: a backward
    frame `settle` ns after the end of the forward frame's last bit (taken as
    the last edge, plus Te if the last bit was a 1), at its own half bit te,
    with low_extra (see dali_wave); answer may also return a ready waveform
    [(level, duration)] for broken replies."""

    def __init__(self, answer, settle=8e6, te=DALI_TE, low_extra=0.0):
        self.answer, self.settle, self.te, self.low_extra = answer, settle, te, low_extra
        self.prev, self.edges, self.t_last = 1, [], None
        self.player = Player()
        self.frames, self.sent = [], []       # (t, value, n, error), (t, byte or wave)
        self.tx_until = None

    def step(self, t, bus):
        if self.tx_until is not None and t > self.tx_until:
            self.tx_until = None
        if bus != self.prev:
            if self.tx_until is None:         # not our own reply
                self.edges.append((t, bus))
                self.t_last = t
            self.prev = bus
        if self.edges and bus == 1 and t - self.t_last >= DALI_RX["stop"]:
            fr = dali_decode([(self.edges[0][0] - 1, 1)] + self.edges, t)
            self.edges = []
            for f in fr:
                self.frames.append((f["t"], f["value"], f["n"], f["error"]))
                if f["error"] or f["n"] not in (16, 24):
                    continue
                a = self.answer(f["value"], f["n"])
                if a is None:
                    continue
                last_edge = f["t"] + sum(f["runs"])
                t_end = last_edge + (self.te if f["bits"][-1] == 1 else 0)
                wave = a if isinstance(a, list) else dali_wave(a, 8, self.te, self.low_extra)
                t0 = t_end + self.settle
                self.tx_until = self.player.schedule(t0, wave) + 1
                self.sent.append((t0, a))
        return self.player.level(t)


# ================================================================== UART (8 data bits, parity)

def parity_bit(byte, parity):
    ones = bin(byte & 0xFF).count("1")
    return {"even": ones & 1, "odd": 1 - (ones & 1)}[parity]


def uart_wave(data, bit_ns, parity="even", stops=1, gaps=None, bad_parity=(), bad_stop=()):
    """Characters as [(level, duration)], idle high: start, 8 data bits LSB
    first, parity (None: no parity bit), stops stop bits. gaps: {i: idle ns
    after character i}; bad_parity / bad_stop: character indices sent with
    the wrong parity bit / a low stop bit."""
    wave = []
    for i, b in enumerate(data):
        bits = [0] + [(b >> k) & 1 for k in range(8)]
        if parity:
            bits.append(parity_bit(b, parity) ^ (i in bad_parity))
        bits += [0 if i in bad_stop else 1] * stops
        wave += [(lv, bit_ns) for lv in bits]
        if gaps and gaps.get(i):
            wave.append((1, gaps[i]))
    merged = []
    for lv, d in wave:
        if merged and merged[-1][0] == lv:
            merged[-1] = (lv, merged[-1][1] + d)
        else:
            merged.append((lv, d))
    return merged


def uart_decode(edges, t_end, bit_ns, parity="even"):
    """Pins-only UART decoder (idle high): each character is
    re-synchronised on its start bit's falling edge and sampled at mid-bit.
    Returns [{t, byte, parity_ok, stop_ok, t_stop_end}]."""
    from .postfreeze import DmxReceiver
    rs = runs(edges, t_end)
    la = DmxReceiver._level_at
    nbits = 10 + (1 if parity else 0)
    out, i = [], 0
    while i < len(rs):
        lvl, t, d = rs[i]
        if lvl == 0:
            s = [la(rs, t + (k + 0.5) * bit_ns) for k in range(nbits)]
            b = sum(s[1 + k] << k for k in range(8))
            out.append({"t": t, "byte": b, "parity_ok": (s[9] == parity_bit(b, parity)) if parity else True,
                        "stop_ok": s[-1] == 1, "t_stop_end": t + nbits * bit_ns})
            while i < len(rs) and rs[i][1] < t + (nbits - 0.5) * bit_ns:
                i += 1
            continue
        i += 1
    return out


# ================================================================== Modbus RTU

def modbus_crc(data):
    """CRC-16/MODBUS as the specification computes it (reflected 0xA001,
    preset 0xFFFF); the frame carries it low byte first."""
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def modbus_adu(body):
    """The bytes + their CRC (low byte first)."""
    c = modbus_crc(body)
    return bytes(body) + bytes([c & 0xFF, c >> 8])


MODBUS_CRCCTRL = 0x8005 | (15 << 16) | (1 << 20) | (1 << 21)    # POLY 0x8005, width 16, IN and OUT taps


def modbus_defines(baud=19200, tb=104, tx=8, de=9, rx=16, rtmo_ms=100, parity="even"):
    """modbus_rtu.pasm defines: t1.5 / t3.5 in ticks (16.5 / 38.5 bits up
    to 19200 baud, 750 / 1750 us above), the response timeout in loops of
    4099 ticks."""
    tick = 1e9 / (baud * tb)
    if baud <= 19200:
        t15, t35 = round(16.5 * tb), round(38.5 * tb)
    else:
        t15, t35 = round(750e3 / tick), round(1750e3 / tick)
    return {"TX": tx, "DE": de, "RX": rx, "TB": tb, "T15": t15, "T35": t35,
            "RTMO": max(1, round(rtmo_ms * 1e6 / tick / 4099)), "PAR": {"even": 0, "odd": 1}[parity]}


def modbus_words(body, expect=True):
    """modbus_rtu TX FIFO words: the header and the bytes (the firmware
    appends the CRC)."""
    data = bytes(body)
    if not 1 <= len(data) <= 255:
        raise ValueError("1..255 bytes")
    w = [len(data) | (int(expect) << 8)]
    for i in range(0, len(data), 4):
        w.append(int.from_bytes(data[i:i + 4].ljust(4, b"\0"), "little"))
    return w


def modbus_response(words):
    """Group modbus_rtu's RX FIFO words into responses: [{bytes (all
    received, the CRC included), crc_ok, parity_error, framing_error, gap,
    overflow}]."""
    out, cur = [], b""
    for w in words:
        tag = w >> 24
        if tag == 3:
            cur += w.to_bytes(4, "little")[:3]
            continue
        if not tag & 0x80:
            raise ValueError(f"not a modbus_rtu word: {w:#x}")
        cur += w.to_bytes(4, "little")[:tag & 3]
        out.append({"bytes": cur, "parity_error": bool(tag & 4), "framing_error": bool(tag & 8),
                    "gap": bool(tag & 16), "crc_ok": bool(tag & 32), "overflow": bool(tag & 64)})
        cur = b""
    return out


class ModbusServer:
    """A pins-only Modbus RTU server on the RS-485 bus: step(t, line)
    -> (drive, level). It receives characters (11 bits, the given parity),
    frames them by t3.5 of silence, checks the CRC (modbus_crc), and if
    respond(request without its CRC) returns bytes, answers `delay` ns after
    the request's last stop bit with them and their CRC, at its own bit rate
    (rate: 1.02 = 2 % fast). respond may return a dict {data, crc=False (a
    wrong CRC), gaps, bad_parity, bad_stop} (see uart_wave). Records
    (t_first, bytes, crc_ok, parity_ok, stop_ok, gaps between characters)
    per request."""

    def __init__(self, respond, baud=19200, delay=3e6, rate=1.0, parity="even"):
        self.respond, self.delay, self.parity = respond, delay, parity
        self.bit = 1e9 / baud
        self.my_bit = self.bit / rate
        self.t35 = 38.5 * self.bit if baud <= 19200 else 1.75e6
        self.edges, self.prev, self.t_last = [], 1, None
        self.requests, self.sent, self.player = [], [], Player()
        self.tx_until = None

    def step(self, t, line):
        if self.tx_until is not None and t > self.tx_until:
            self.tx_until = None
        if line != self.prev:
            if self.tx_until is None:
                self.edges.append((t, line))
                self.t_last = t
            self.prev = line
        if self.edges and line == 1 and t - self.t_last >= self.t35:
            chars = uart_decode([(self.edges[0][0] - 1, 1)] + self.edges, t, self.bit, self.parity)
            self.edges = []
            if chars:
                data = bytes(c["byte"] for c in chars)
                ok = len(data) >= 4 and modbus_crc(data[:-2]) == int.from_bytes(data[-2:], "little")
                gaps = [b["t"] - a["t_stop_end"] for a, b in zip(chars, chars[1:])]
                self.requests.append((chars[0]["t"], data, ok, all(c["parity_ok"] for c in chars),
                                      all(c["stop_ok"] for c in chars), gaps))
                r = self.respond(data[:-2]) if ok else None
                if r is not None:
                    opts = dict(r) if isinstance(r, dict) else {"data": r}
                    body = opts.pop("data")
                    frame = modbus_adu(body) if opts.pop("crc", True) else bytes(body) + b"\0\0"
                    wave = uart_wave(frame, self.my_bit, self.parity, **opts)
                    t0 = chars[-1]["t_stop_end"] + self.delay
                    self.tx_until = self.player.schedule(t0, wave) + 1
                    self.sent.append((t0, frame))
        if self.tx_until is not None or self.player.busy(t):
            return 1, self.player.level(t)
        return 0, 1


# ================================================================== MIDI

MIDI_BAUD = 31250


def midi_ndata(status):
    """Data bytes of a message with this status (MIDI 1.0): channel
    messages 2, except program change and channel pressure (1); system
    common 0xF1 and 0xF3 1, 0xF2 2, the rest 0; real-time 0."""
    if status < 0xF0:
        return 1 if status & 0xE0 == 0xC0 else 2
    return {0xF1: 1, 0xF2: 2, 0xF3: 1}.get(status, 0)


def midi_parse(stream):
    """Reference MIDI 1.0 receiver over a byte stream, written from the
    specification (independently of the firmware): events ("msg", status,
    data tuple) for every complete message (real-time included, where it
    came), ("sysex", bytes, ended_by_eox) when a SysEx ends."""
    out, cur, buf, sx = [], None, [], None
    for b in stream:
        if b >= 0xF8:                              # real-time: anywhere, changes nothing
            out.append(("msg", b, ()))
            continue
        if b >= 0x80:
            if sx is not None:                     # any other status ends a SysEx
                out.append(("sysex", bytes(sx), b == 0xF7))
                sx = None
                if b == 0xF7:
                    cur = None
                    continue
            buf = []
            if b == 0xF0:
                sx, cur = bytearray(), None
            elif midi_ndata(b) == 0:               # 0xF4..0xF7: no data; cancels running status
                out.append(("msg", b, ()))
                cur = None
            else:
                cur = b
            continue
        if sx is not None:
            sx.append(b)
        elif cur is not None:                      # running status: the status stays
            buf.append(b)
            if len(buf) == midi_ndata(cur):
                out.append(("msg", cur, tuple(buf)))
                buf = []
                if cur >= 0xF0:                    # system common: no running status
                    cur = None
    return out


def midi_encode(events, running_status=True):
    """A sender: events (as midi_parse's) -> bytes, leaving out repeated
    channel status bytes when running_status (system common and SysEx
    cancel it, real-time leaves it)."""
    out, rs = bytearray(), None
    for e in events:
        if e[0] == "sysex":
            out += bytes([0xF0]) + e[1] + (b"\xF7" if e[2] else b"")
            rs = None
            continue
        _, s, d = e
        if s >= 0xF8:
            out.append(s)
            continue
        if s >= 0xF0:
            rs = None
            out.append(s)
        elif not (running_status and s == rs):
            out.append(s)
            rs = s
        out += bytes(d)
    return bytes(out)


def midi_message_word(status, data=()):
    """A midi_rx / midi_tx message word."""
    d = list(data) + [0, 0]
    return status | (d[0] << 8) | (d[1] << 16) | (len(data) << 24)


def midi_tx_words(events):
    """midi_tx TX FIFO words for events (a SysEx: its 0xF0, data words of up
    to 3 bytes, and 0xF7 if it ends with one)."""
    w = []
    for e in events:
        if e[0] == "sysex":
            w.append(midi_message_word(0xF0))
            for i in range(0, len(e[1]), 3):
                c = e[1][i:i + 3]
                w.append(int.from_bytes(c.ljust(3, b"\0"), "little") | ((0x80 | len(c)) << 24))
            if e[2]:
                w.append(midi_message_word(0xF7))
        else:
            w.append(midi_message_word(e[1], e[2]))
    return w


def midi_rx_events(words):
    """midi_rx RX FIFO words -> events as midi_parse's, plus ("ferr", byte)
    for a framing error."""
    out, sx = [], None
    for w in words:
        tag, status = w >> 24, w & 0xFF
        if tag == 0xC0:
            out.append(("ferr", status))
        elif tag & 0x80:
            sx += w.to_bytes(4, "little")[:tag & 3]
        elif status >= 0xF8:
            out.append(("msg", status, ()))
        else:
            if sx is not None:
                out.append(("sysex", bytes(sx), status == 0xF7))
                sx = None
                if status == 0xF7:
                    continue
            if status == 0xF0:
                sx = bytearray()
            else:
                out.append(("msg", status, tuple(w.to_bytes(4, "little")[1:1 + tag])))
    return out


def midi_random_events(rng, n=60):
    """A mix of every kind of message (channel, system common, SysEx ended
    by 0xF7 or by a status, real-time), several in a row on one status so
    running status applies."""
    ev = []
    while len(ev) < n:
        k = rng.random()
        if k < 0.55:
            s = rng.choice([0x80, 0x90, 0xA0, 0xB0, 0xC0, 0xD0, 0xE0]) | rng.randrange(16)
            for _ in range(rng.randrange(1, 4)):
                ev.append(("msg", s, tuple(rng.randrange(128) for _ in range(midi_ndata(s)))))
        elif k < 0.7:
            s = rng.choice([0xF1, 0xF2, 0xF3, 0xF6])
            ev.append(("msg", s, tuple(rng.randrange(128) for _ in range(midi_ndata(s)))))
        elif k < 0.8:
            ev.append(("sysex", bytes(rng.randrange(128) for _ in range(rng.randrange(0, 9))), True))
        elif k < 0.85:
            # ended by the next status, not 0xF7
            ev.append(("sysex", bytes(rng.randrange(128) for _ in range(rng.randrange(1, 6))), False))
            s = 0x90 | rng.randrange(16)
            ev.append(("msg", s, (rng.randrange(128), rng.randrange(128))))
        else:
            ev.append(("msg", rng.choice([0xF8, 0xFA, 0xFB, 0xFC, 0xFE, 0xFF]), ()))
    return ev


def insert_realtime(stream, rng, count, choices=(0xF8, 0xFE)):
    """The stream with `count` real-time bytes inserted at random places
    (inside messages and SysEx too)."""
    s = list(stream)
    for _ in range(count):
        s.insert(rng.randrange(len(s) + 1), rng.choice(choices))
    return bytes(s)
