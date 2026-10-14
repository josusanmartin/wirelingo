"""Host side of the third round of post-freeze protocols
(docs/post-freeze-protocols.md): HDMI-CEC, ISO 7816-3 T=0, SMBus with PEC
and RC-5 infrared. As in pe.postfreeze and pe.postfreeze2: the words each
firmware expects in its TX FIFO, the decoding of what it pushes back, and
pins-only models of the other end of each line.

Everything here was written after the RTL freeze (tag rc-93c2137-t48ba). The
models see nothing but pin levels and times (ns) and take their numbers
from the specifications, not from the firmware. Edge lists are [(t_ns,
level)] (pe.postfreeze.bit_edges); waveforms [(level, duration_ns)].
"""
from . import host
from .postfreeze import runs
from .postfreeze2 import Player


# ================================================================== HDMI-CEC
# HDMI 1.3a Supplement 1 (CEC), bit timing (ms): start bit low 3.7 +- 0.2,
# period 4.5 +- 0.2; data bit low 0.6 +- 0.2 (1) or 1.5 +- 0.2 (0), period
# 2.4 nominal, 2.05..2.75 for a receiver; sample at 1.05 +- 0.2 (0.85..1.25);
# a follower's ACK 0 is low until 1.5 ms. Signal free time before a frame:
# 3 bit periods (retry), 5 (new initiator), 7 (the same initiator again).

CEC_TICK = 10e3                 # ns: the firmware's tick
CEC_BIT = 2.4e6
CEC_RX = {"start_lo": (3.5e6, 3.9e6), "start": (4.3e6, 4.7e6), "lo1": (0.4e6, 0.8e6),
          "lo0": (1.3e6, 1.7e6), "bit": (2.05e6, 2.75e6), "sample": 1.05e6}
CEC_CRCCTRL = 15 << 16          # width 16, no taps: the CRC register holds our address
CEC_NONE = 16                   # "no logical address"


def cec_defines(cec=0, fov=7, flost=6):
    return {"CEC": cec, "FOV": fov, "FLOST": flost}


def cec_clkdiv(f_clk):
    return host.clkdiv_for(f_clk, 1e9 / CEC_TICK)


def cec_words(blocks, sft_bits=5):
    """cec TX FIFO words for one frame: blocks = bytes (the header first:
    initiator << 4 | destination). sft_bits: the signal free time in bit
    periods (3 retry, 5 new initiator, 7 the previous initiator again)."""
    blocks = bytes(blocks)
    if not 1 <= len(blocks) <= 16:
        raise ValueError("1..16 blocks")
    bc = int(blocks[0] & 15 == 15)
    w = [round(sft_bits * CEC_BIT / CEC_TICK) | (len(blocks) - 1) << 16 | bc << 20]
    for i in range(0, len(blocks), 4):
        w.append(int.from_bytes(blocks[i:i + 4].ljust(4, b"\0"), "big"))
    return w


def cec_records(words):
    """Decode cec's RX FIFO words: (frames received, results of our frames,
    errors). A frame: {blocks (bytes), eom [..], ack [..] (the ACK bit as on
    the line), to_us (we acknowledged it)}; a result: {ok, nack, lost,
    blocks}; an error: {reason ("short", "low", "partial"), bits_left}."""
    frames, tx, err = [], [], []
    for w in words:
        if w & (1 << 25):
            err.append({"reason": {1: "short", 2: "low", 3: "partial"}.get(w >> 8 & 255, w >> 8 & 255),
                        "bits_left": w & 255})
        elif w & (1 << 24):
            if w & 1 or not frames:
                frames.append({"blocks": b"", "eom": [], "ack": [], "to_us": False})
            f = frames[-1]
            f["blocks"] += bytes([w >> 4 & 255])
            f["eom"].append(w >> 3 & 1)
            f["ack"].append(w >> 2 & 1)
            f["to_us"] |= bool(w & 2)
        elif w & 0x8000:
            tx.append({"ok": bool(w & 0x1000), "nack": bool(w & 0x2000), "lost": bool(w & 0x4000),
                       "blocks": (w >> 8 & 15) + 1})
        else:
            raise ValueError(f"not a cec record: {w:#x}")
    return frames, tx, err


def cec_wave(blocks, start_lo=3.7e6, start=4.5e6, bit=2.4e6, lo1=0.6e6, lo0=1.5e6, bits=None):
    """An initiator's frame as [(level, duration)] (ACK bits sent as 1).
    bits: replace the bit list (0/1 values after the start bit) for broken
    frames; bit may be a function of the bit index for uneven periods."""
    if bits is None:
        bits = []
        for i, b in enumerate(bytes(blocks)):
            bits += [(b >> (7 - k)) & 1 for k in range(8)] + [int(i == len(blocks) - 1), 1]
    w = [(0, start_lo), (1, start - start_lo)]
    for i, b in enumerate(bits):
        lo = lo1 if b else lo0
        per = bit(i) if callable(bit) else bit
        w += [(0, lo), (1, per - lo)]
    return w


def cec_decode(edges, t_end, limits=CEC_RX):
    """Pins-only CEC decoder of a bus (idle high): a start bit by the
    receiver's ranges, then every bit sampled at 1.05 ms after its fall;
    a frame ends after 2.75 ms without a fall. Returns [{t, blocks (bytes),
    eom [..], ack [..], error, start_lo, start, lows [..], periods [..]}]."""
    falls = [t for t, v in edges if v == 0]
    rises = [t for t, v in edges if v == 1]

    def level_at(t):
        lv = 1
        for tt, v in edges:
            if tt > t:
                break
            lv = v
        return lv

    def rise_after(t):
        return next((r for r in rises if r > t), t_end)

    frames, i = [], 0
    while i < len(falls):
        t0 = falls[i]
        lo = rise_after(t0) - t0
        if not (limits["start_lo"][0] <= lo <= limits["start_lo"][1]) or i + 1 >= len(falls):
            i += 1
            continue
        per = falls[i + 1] - t0
        if not (limits["start"][0] <= per <= limits["start"][1]):
            i += 1
            continue
        f = {"t": t0, "start_lo": lo, "start": per, "bits": [], "lows": [], "periods": [], "error": None}
        frames.append(f)
        i += 1
        while i < len(falls):
            tf = falls[i]
            f["bits"].append(level_at(tf + limits["sample"]))
            f["lows"].append(rise_after(tf) - tf)
            nxt = falls[i + 1] if i + 1 < len(falls) else None
            i += 1
            if nxt is None or nxt - tf > limits["bit"][1]:
                break
            f["periods"].append(nxt - tf)
            if nxt - tf < limits["bit"][0] and f["error"] is None:
                f["error"] = f"bit period {(nxt - tf) / 1e6:.3f} ms"
        bits = f.pop("bits")
        f["blocks"], f["eom"], f["ack"] = b"", [], []
        for k in range(0, len(bits) - 9, 10):
            blk = bits[k:k + 10]
            f["blocks"] += bytes([int("".join(map(str, blk[:8])), 2)])
            f["eom"].append(blk[8])
            f["ack"].append(blk[9])
        if len(bits) % 10 and f["error"] is None:
            f["error"] = f"{len(bits) % 10} bits after the last block"
    return frames


class CecNode:
    """A pins-only CEC device: follower always, initiator on request.
    step(t, bus) -> its drive (1 released, 0 pulling the line low).

    Follower: finds start bits by the receiver's ranges, samples every bit
    at `sample` after its fall, acknowledges frames to `addr` by holding the
    ACK slot low until `ack_lo` after the fall (directed frames only; it
    never rejects a broadcast). frames: [{t, blocks, eom, ack, error}].

    Initiator: send(blocks, at=None, sft_bits=5, **timing) queues a frame,
    sent when the line has been idle for the signal free time (or at time
    `at`, whatever the line does: for arbitration tests), with cec_wave's
    timing. It samples its 1s at `sample`: a 0 outside the ACK slot is a lost
    arbitration (it stops driving), a NACK (directed) or a rejection
    (broadcast) ends the frame. results: [(t, blocks sent, "ok" | "nack" |
    "lost")]."""

    def __init__(self, addr=None, sample=1.05e6, ack_lo=1.5e6, start_lo=3.7e6, start=4.5e6, bit=2.4e6,
                 lo1=0.6e6, lo0=1.5e6):
        self.addr, self.sample, self.ack_lo = addr, sample, ack_lo
        self.timing = dict(start_lo=start_lo, start=start, bit=bit, lo1=lo1, lo0=lo0)
        self.prev, self.t_idle = 1, 0.0
        self.lows = []                  # [t0, t1): our drive
        self.frames, self.results, self.queue = [], [], []
        self.cur = None                 # the frame being received
        self.tx = None                  # the frame being sent
        self.t_fall = None
        self.pend_sample = None

    def send(self, blocks, at=None, sft_bits=5, **timing):
        self.queue.append((bytes(blocks), at, sft_bits, {**self.timing, **timing}))

    def _drive(self, t):
        return 0 if any(a <= t < b for a, b in self.lows) else 1

    def _start_tx(self, t, blocks, timing):
        w = cec_wave(blocks, **timing)
        bits_t = []                     # (fall time, bit value, index) per data bit
        tt = t
        for k, (lvl, d) in enumerate(w):
            if lvl == 0:
                self.lows.append((tt, tt + d))
                if k:
                    bits_t.append(tt)
            tt += d
        self.tx = {"t": t, "blocks": blocks, "falls": bits_t, "k": 0, "end": tt}

    def _stop_tx(self, t, outcome, nblocks):
        self.lows = [(a, b) for a, b in self.lows if a <= t]
        self.results.append((self.tx["t"], nblocks, outcome))
        self.tx = None

    def step(self, t, bus):
        # follower: edges
        if bus != self.prev:
            if bus == 0:
                self._fall(t)
            else:
                self._rise(t)
            self.prev = bus
        if bus == 0 or self._drive(t) == 0:
            self.t_idle = t
        if self.pend_sample is not None and t >= self.pend_sample:
            self.pend_sample = None
            self._sample(t, bus)
        if self.cur is not None and bus == 1 and \
                t - self.t_fall > CEC_RX["start" if self.cur["phase"] == "start" else "bit"][1]:
            self._end_frame()
        # initiator
        if self.tx is None and self.queue:
            blocks, at, sft, timing = self.queue[0]
            go = (t >= at) if at is not None else (bus == 1 and self.cur is None and t - self.t_idle >= sft * CEC_BIT)
            if go:
                self.queue.pop(0)
                self._start_tx(t, blocks, timing)
        if self.tx is not None and t >= self.tx["end"]:
            self._stop_tx(t, "ok", len(self.tx["blocks"]))
        self.lows = [(a, b) for a, b in self.lows if b > t]
        return self._drive(t)

    def _fall(self, t):
        if self.cur is None:
            self.cur = {"t": t, "phase": "start", "bits": [], "error": None, "t_start": t}
        else:
            c = self.cur
            if c["phase"] == "start":
                per = t - c["t_start"]
                if not (CEC_RX["start"][0] <= per <= CEC_RX["start"][1]) or c.get("lo") is None:
                    self.cur = {"t": t, "phase": "start", "bits": [], "error": None, "t_start": t}
                    self.t_fall = t
                    return
                c["phase"] = "bits"
            elif t - self.t_fall < CEC_RX["bit"][0] and c["error"] is None:
                c["error"] = f"bit period {(t - self.t_fall) / 1e6:.3f} ms"
            if c["phase"] == "bits":
                k = len(c["bits"])
                if k % 10 == 9 and self._to_me(c) and self.tx is None:
                    self.lows.append((t, t + self.ack_lo))
                self.pend_sample = t + self.sample
        self.t_fall = t

    def _rise(self, t):
        c = self.cur
        if c is not None and c["phase"] == "start" and c.get("lo") is None:
            lo = t - c["t_start"]
            if CEC_RX["start_lo"][0] <= lo <= CEC_RX["start_lo"][1]:
                c["lo"] = lo
            else:
                self.cur = None

    def _to_me(self, c):
        if self.addr is None or len(c["bits"]) < 8:
            return False
        dst = int("".join(map(str, c["bits"][4:8])), 2)
        return dst == self.addr

    def _sample(self, t, bus):
        c = self.cur
        if c is None or c["phase"] != "bits":
            return
        c["bits"].append(bus)
        k = len(c["bits"])
        tx = self.tx
        if tx is not None:
            blocks = tx["blocks"]
            sent = cec_wave_bits(blocks)
            if k <= len(sent):
                if k % 10 == 0:             # the ACK slot
                    bc = blocks[0] & 15 == 15
                    if bus == int(not bc):  # directed: 1 = NACK; broadcast: 0 = rejected
                        self._stop_tx(t, "nack", k // 10)
                elif sent[k - 1] == 1 and bus == 0:
                    self._stop_tx(t, "lost", (k - 1) // 10 + 1)

    def _end_frame(self):
        c = self.cur
        self.cur = None
        if c["phase"] != "bits":
            return
        bits = c["bits"]
        blocks, eom, ack = b"", [], []
        for k in range(0, len(bits) - 9, 10):
            blk = bits[k:k + 10]
            blocks += bytes([int("".join(map(str, blk[:8])), 2)])
            eom.append(blk[8])
            ack.append(blk[9])
        err = c["error"] or (f"{len(bits) % 10} bits after the last block" if len(bits) % 10 else None)
        self.frames.append({"t": c["t"], "blocks": blocks, "eom": eom, "ack": ack, "error": err})


def cec_wave_bits(blocks):
    """The bits an initiator sends after the start bit (ACK slots as 1)."""
    bits = []
    for i, b in enumerate(bytes(blocks)):
        bits += [(b >> (7 - k)) & 1 for k in range(8)] + [int(i == len(blocks) - 1), 1]
    return bits


# ================================================================== ISO 7816-3, T=0
# ISO/IEC 7816-3: etu = F / D card clock periods (372 until a PPS); a
# character is a start bit, 8 data bits and an even parity bit; 12 etu from
# start to start (+ N extra guard etu from the interface device); a receiver
# that sees a parity error holds I/O low from 10.5 +- 0.2 etu for 1..2 etu
# and the sender repeats the character >= 2 etu after detecting it; >= 16
# etu between the leading edges of characters in opposite directions; the
# answer to reset starts 400..40000 clock cycles after RST rises.

ISO_F = 372
ISO_TB = 124
ISO_SEND, ISO_RECV, ISO_RESET, ISO_DEACT = 0, 1, 2, 3
ISO_CRCCTRL = 15 << 16          # width 16, no taps: the CRC register is storage


def iso_defines(io=0, rst=8, tb=ISO_TB, wt=9600, rmax=4, rstlo=2, turn=6, finv=5, fov=7):
    return {"IO": io, "RST": rst, "TB": tb, "WT": wt, "RMAX": rmax, "RSTLO": rstlo, "TURN": turn,
            "FINV": finv, "FOV": fov}


def iso_clkdivs(d=1):
    """(the reader core's CLKDIV, the clock core's): f_CLK = f_clk / 2d, one
    etu = 372 CLK periods = 124 ticks of the reader core."""
    return (6 * d) << 8, d << 8


def iso_send(data, extra_guard=0):
    """iso7816 TX FIFO words: SEND the bytes (N extra guard etu)."""
    data = bytes(data)
    if not 1 <= len(data) <= 255:
        raise ValueError("1..255 bytes")
    w = [ISO_SEND | len(data) << 8 | extra_guard << 16]
    for i in range(0, len(data), 4):
        w.append(int.from_bytes(data[i:i + 4].ljust(4, b"\0"), "little"))
    return w


def iso_recv(m, first_timeout_etu=0, ts=False):
    """iso7816 RECV command word: up to m characters; the first within
    first_timeout_etu (rounded up to 8 etu; 0: WT); ts: the first is TS."""
    return [ISO_RECV | m << 8 | -(-first_timeout_etu // 8) << 16 | int(ts) << 24]


def iso_records(words):
    """Decode iso7816's RX FIFO words: [("char", byte, error signals sent,
    gave up), ("recv", received, errors, timeout, bad TS, inverse),
    ("send", not sent, repetitions, aborted)]."""
    out = []
    for w in words:
        tag = w >> 24
        if tag == 1:
            out.append(("char", w & 255, w >> 8 & 15, bool(w >> 12 & 1)))
        elif tag == 2:
            out.append(("recv", w & 255, w >> 8 & 255, bool(w >> 16 & 1), bool(w >> 17 & 1), bool(w >> 18 & 1)))
        elif tag == 3:
            out.append(("send", w & 255, w >> 8 & 255, bool(w >> 16 & 1)))
        else:
            raise ValueError(f"not an iso7816 record: {w:#x}")
    return out


def iso_parity(b):
    return bin(b & 255).count("1") & 1


def iso_levels(b, inverse=False, bad_parity=False):
    """The 10 levels of a character (start, 8 data, parity) on I/O (1 = H)."""
    if inverse:
        data = [1 - ((b >> (7 - k)) & 1) for k in range(8)]
        par = 1 - iso_parity(b)
    else:
        data = [(b >> k) & 1 for k in range(8)]
        par = iso_parity(b)
    return [0] + data + [par ^ int(bad_parity)]


def iso_byte(levels9, inverse=False):
    """(byte, parity ok) from the 9 levels after the start bit."""
    d, par = levels9[:8], levels9[8]
    if inverse:
        b = sum((1 - d[k]) << (7 - k) for k in range(8))
        return b, (1 - par) == iso_parity(b)
    b = sum(d[k] << k for k in range(8))
    return b, par == iso_parity(b)


def atr_length(atr):
    """The length of an answer to reset from its first bytes (None if more
    are needed): TS, T0, the interface bytes TAi..TDi, K historical bytes,
    and TCK if any protocol other than T=0 is offered."""
    if len(atr) < 2:
        return None
    n, y, k, t_other, i = 2, atr[1] >> 4, atr[1] & 15, False, 1
    while True:
        cnt = bin(y).count("1")
        if len(atr) < n + cnt:
            return None
        td = atr[n + cnt - 1] if y & 8 else None
        n += cnt
        if td is None:
            break
        if td & 15:
            t_other = True
        y, i = td >> 4, i + 1
    return n + k + int(t_other)


def t0_parse(chars, ins):
    """A T=0 card's answer after a command header: (NULL bytes, procedure
    byte INS seen, data, SW1 SW2) from the characters received."""
    nulls, i = 0, 0
    while i < len(chars) and chars[i] == 0x60:
        nulls, i = nulls + 1, i + 1
    if i < len(chars) and chars[i] == ins:
        return nulls, True, bytes(chars[i + 1:-2]), bytes(chars[-2:])
    return nulls, False, b"", bytes(chars[i:i + 2])


class IsoCard:
    """A pins-only ISO 7816-3 T=0 card. step(t, clk, rst, io) -> its drive
    of I/O (1 released, 0 low). It counts CLK rising edges: one etu is `f`
    of them (372); it knows nothing else of the reader's timing.

    On RST rising it sends `atr` (convention `inverse`) `atr_delay` CLK
    cycles later; characters `gt` etu apart, an answer to the reader `turn`
    etu after the leading edge of its last character. It samples the
    reader's characters at mid-etu from their start edge, checks the parity
    and signals errors (I/O low from 10.5 etu for `err_len` etu); reject =
    {k: r}: signal an error on the reader's k-th character (counting those
    accepted before it) r times regardless. Sending, it samples I/O at 11 etu and repeats
    a character on an error signal 13 etu after its start, at most `max_rep`
    times; corrupt = {k: r}: send its k-th distinct character with a wrong
    parity bit the first r times.

    The application: READ BINARY (INS B0: answers INS, P3 bytes from `mem`
    at P1 P2, 90 00), UPDATE BINARY (D6: INS, then takes P3 bytes, 90 00),
    others 6D 00; `nulls` NULL procedure bytes (60) before INS.

    Records: received [(n0, byte, parity ok, error signalled)], sent [(n0,
    byte, attempt)], commands [(header, data in, answer)], err_seen [(n0 of
    our character, CLK count of the error)]."""

    def __init__(self, atr=bytes.fromhex("3BB4110000574C474F"), inverse=False, atr_delay=5000, f=ISO_F,
                 gt=12, turn=20, err_len=1.5, max_rep=4, corrupt=None, reject=None, nulls=0, mem=None):
        self.atr, self.inverse, self.atr_delay, self.etu = bytes(atr), inverse, atr_delay, f
        self.gt, self.turn, self.err_len, self.max_rep = gt, turn, err_len, max_rep
        self.corrupt, self.reject, self.nulls = dict(corrupt or {}), dict(reject or {}), nulls
        self.mem = bytearray(mem if mem is not None else range(256))
        self.n, self.prev_clk, self.prev_rst, self.prev_io = 0, 0, 0, 1
        self.received, self.sent, self.commands, self.err_seen = [], [], [], []
        self.n_rx = 0          # the reader's characters seen (every attempt)
        self.n_tx = 0          # our distinct characters
        self._reset()

    def _reset(self):
        self.queue, self.tx, self.rx, self.next_tx = [], None, None, None
        self.lows = []                  # [n0, n1) CLK counts we hold I/O low (error signals)
        self.app, self.buf, self.need = "header", bytearray(), 0
        self.mute_until = -1

    def _drive(self):
        if any(a <= self.n < b for a, b in self.lows):
            return 0
        if self.tx is not None:
            e = (self.n - self.tx["n0"]) // self.etu
            if 0 <= e < 10:
                return self.tx["levels"][e]
        return 1

    def step(self, t, clk, rst, io):
        edge = clk and not self.prev_clk
        self.prev_clk = clk
        if edge:
            self.n += 1                         # (n counts every CLK cycle the card is given)
        if not rst:
            if self.prev_rst:
                self._reset()
            self.prev_rst = 0
            return 1
        if not self.prev_rst:
            self._reset()
            self.queue = list(self.atr)
            self.next_tx = self.n + self.atr_delay
            self.prev_rst = 1
        if edge:
            self._edge(io)
        return self._drive()

    def _send_later(self, data, after_n0):
        self.queue += list(data)
        start = after_n0 + self.turn * self.etu
        self.next_tx = start if self.next_tx is None else max(self.next_tx, start)

    def _edge(self, io):
        n, etu = self.n, self.etu
        self.lows = [(a, b) for a, b in self.lows if b > n]
        tx = self.tx
        if tx is not None:
            k = n - tx["n0"]
            if k == 11 * etu:
                if io == 0:                         # the reader signalled an error
                    self.err_seen.append((tx["n0"], n))
                    tx["rep"] += 1
                    if tx["rep"] <= self.max_rep:
                        tx["n0"] = tx["n0"] + 13 * etu
                        tx["levels"] = iso_levels(tx["byte"], self.inverse,
                                                  self.corrupt.get(tx["k"], 0) > tx["rep"])
                        self.sent.append((tx["n0"], tx["byte"], tx["rep"]))
                    else:
                        self.tx = None
                        self.next_tx = n + 2 * etu
                else:
                    self.next_tx = max(self.next_tx or 0, tx["n0"] + self.gt * etu)
                    self.tx = None
            self.prev_io = io
            return
        if self.queue and self.next_tx is not None and n >= self.next_tx and self.rx is None:
            b = self.queue.pop(0)
            k = self.n_tx
            self.n_tx += 1
            self.tx = {"n0": n, "byte": b, "rep": 0, "k": k,
                       "levels": iso_levels(b, self.inverse, self.corrupt.get(k, 0) > 0)}
            self.sent.append((n, b, 0))
            self.prev_io = io
            return
        rx = self.rx
        if rx is None:
            if io == 0 and self.prev_io == 1 and n > self.mute_until and self._drive():
                self.rx = {"n0": n, "lv": []}
        else:
            k = n - rx["n0"]
            for e in range(10):
                if k == round((e + 0.5) * etu):
                    if e == 0 and io:
                        self.rx = None              # a glitch
                    elif e:
                        rx["lv"].append(io)
                    break
            if self.rx is not None and len(rx["lv"]) == 9:
                b, ok = iso_byte(rx["lv"], self.inverse)
                self.rx = None
                rej = self.reject.get(self.n_rx, 0)
                bad = not ok or rej > 0
                if rej:
                    self.reject[self.n_rx] = rej - 1
                if not bad:
                    self.n_rx += 1
                self.received.append((rx["n0"], b, ok, bad))
                if bad:
                    a = rx["n0"] + round(10.5 * etu)
                    self.lows.append((a, a + round(self.err_len * etu)))
                    self.mute_until = a + round(self.err_len * etu)
                else:
                    self._app(b, rx["n0"])
        self.prev_io = io

    def _app(self, b, n0):
        if self.app == "header":
            self.buf.append(b)
            if len(self.buf) < 5:
                return
            hdr, self.buf = bytes(self.buf), bytearray()
            cla, ins, p1, p2, p3 = hdr
            off = p1 << 8 | p2
            if ins == 0xB0:
                le = p3 or 256
                data = bytes(self.mem[(off + i) % len(self.mem)] for i in range(le))
                ans = bytes([0x60] * self.nulls + [ins]) + data + b"\x90\x00"
                self.commands.append((hdr, b"", ans))
                self._send_later(ans, n0)
            elif ins == 0xD6:
                self.app, self.need, self.cur = "data", p3, (hdr, off)
                self._send_later(bytes([0x60] * self.nulls + [ins]), n0)
            else:
                self.commands.append((hdr, b"", b"\x6d\x00"))
                self._send_later(b"\x6d\x00", n0)
        else:
            self.buf.append(b)
            if len(self.buf) < self.need:
                return
            hdr, off = self.cur
            for i, x in enumerate(self.buf):
                self.mem[(off + i) % len(self.mem)] = x
            self.commands.append((hdr, bytes(self.buf), b"\x90\x00"))
            self.app, self.buf = "header", bytearray()
            self._send_later(b"\x90\x00", n0)
