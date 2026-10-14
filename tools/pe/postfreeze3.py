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


# ================================================================== SMBus with PEC
# System Management Bus Specification 3.2: PEC = CRC-8 (x^8 + x^2 + x + 1,
# preset 0, MSB first) over every byte of a transaction, address bytes
# included; f_SCL 10..100 kHz, tLOW >= 4.7 us, tHIGH >= 4.0 us (100 kHz
# class), T_TIMEOUT 25..35 ms: a device holding SCL low longer is reset.

SMBUS_CRCCTRL = 0x07 | (7 << 16) | (1 << 20) | (1 << 21)    # POLY 0x07, width 8, IN and OUT taps
SMBUS_TICK = 1120.0             # ns (CLKDIV 56 at 50 MHz)
SMBUS_CODES = {0x80: "ok", 0x81: "pec_ok", 0x82: "pec_bad", 0x83: "addr_nack", 0x84: "data_nack",
               0x85: "timeout", 0x86: "lost", 0x87: "busy"}


def smbus_pec(data, crc=0):
    """CRC-8 as SMBus specifies it (polynomial 0x07, preset 0, no
    reflection, no final XOR)."""
    for b in bytes(data):
        crc ^= b
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def smbus_defines(sda=0, scl=1, fov=7, tto_ms=25.0, tick_ns=SMBUS_TICK):
    """TTO: the clock-low timeout in ticks (the firmware gives up 3 ticks
    after it: 25.003 ms at 1.12 us)."""
    return {"SDA": sda, "SCL": scl, "FOV": fov, "TTO": int(tto_ms * 1e6 / tick_ns)}


def smbus_clkdiv(f_clk, tick_ns=SMBUS_TICK):
    return host.clkdiv_for(f_clk, 1e9 / tick_ns)


def smbus_words(addr, write=b"", nread=0, pec=False, block=False, quick_rw=0):
    """smbus_host TX FIFO words for one transaction: write the bytes (the
    command code first), then read nread bytes (or, with block, a count and
    that many), PEC appended or checked."""
    write = bytes(write)
    if len(write) > 255 or nread > 255 or not 0 <= addr < 128:
        raise ValueError("at most 255 bytes each way, a 7-bit address")
    flags = int(pec) | int(block) << 1 | (quick_rw & 1) << 3
    return [nread << 24 | len(write) << 16 | addr << 8 | flags] + [b << 24 for b in write]


def smbus_records(words):
    """smbus_host RX FIFO words -> [{read (bytes, the count and PEC
    included), result (SMBUS_CODES), unsent (data words dropped)}]."""
    out, cur = [], b""
    for w in words:
        if w >> 16 == 1:
            cur += bytes([w & 255])
        elif w & 0x8000 and w < 0x10000:
            out.append({"read": cur, "result": SMBUS_CODES.get(w >> 8, hex(w >> 8)), "unsent": w & 255})
            cur = b""
        else:
            raise ValueError(f"not a smbus_host record: {w:#x}")
    return out


class SmbusDevice:
    """A pins-only SMBus device (an I2C target that speaks PEC).
    step(t, scl, sda) -> (its SCL drive, its SDA drive), 1 = released.

    addr: its 7-bit address. regs: {command code: the bytes a read of that
    command returns (a block read's count included)}; a read without a
    command (receive byte) returns regs.get(None). With pec, a read is
    followed by the PEC the device computes over the transaction as it saw
    it, and a write that ends with STOP and has at least 2 bytes is taken to
    end with a PEC, which is checked. More bytes read than it has: 0xFF.
    stretch: hold SCL low that long (ns) after every ACK it sends; hang =
    (k, ns): after the k-th byte of a transaction hold SCL low for ns. As
    SMBus asks, it resets itself when SCL has been low for 35 ms.

    transactions: [{t, write (bytes after the address + W), read (bytes it
    sent), acks (the master's ACK bits after them), pec_ok (writes), sr
    (a repeated START was used), aborted}]."""

    def __init__(self, addr, regs=None, pec=True, stretch=0.0, hang=None, nack_after=None):
        self.addr, self.regs, self.pec = addr, dict(regs or {}), pec
        self.stretch, self.hang, self.nack_after = stretch, hang, nack_after
        self.prev_scl, self.prev_sda = 1, 1
        self.sda_out, self.scl_hold_until = 1, None
        self.mode, self.bit, self.shift = "idle", 0, 0
        self.tr, self.transactions = None, []
        self.t_scl_low = None
        self.bytes_in_tr = 0

    # -- helpers
    def _seen(self, b):
        self.tr["bus"].append(b)

    def _finish(self, aborted=False):
        tr = self.tr
        if tr is None:
            return
        tr["aborted"] = aborted
        if not tr["read"] and len(tr["write"]) >= 2 and self.pec and not aborted:
            body = bytes([self.addr << 1]) + tr["write"][:-1]
            tr["pec_ok"] = smbus_pec(body) == tr["write"][-1]
        self.transactions.append(tr)
        self.tr = None

    def _next_read_byte(self):
        tr = self.tr
        if tr["out"]:
            return tr["out"].pop(0)
        return 0xFF

    def step(self, t, scl, sda):
        # SMBus T_TIMEOUT: SCL low for 35 ms resets the device
        if scl == 0:
            if self.t_scl_low is None:
                self.t_scl_low = t
            elif t - self.t_scl_low > 35e6 and self.mode != "idle":
                self._finish(aborted=True)
                self.mode, self.sda_out, self.scl_hold_until = "idle", 1, None
        else:
            self.t_scl_low = None
        if scl and self.prev_scl and sda != self.prev_sda:      # START / STOP
            if sda == 0:
                if self.tr is not None:
                    self.tr["sr"] = True
                    self.mode = "addr"
                else:
                    self.mode = "addr"
                self.bit, self.shift, self.sda_out = 0, 0, 1
            else:
                if self.mode != "idle" or self.tr is not None:
                    self._finish()
                self.mode, self.sda_out = "idle", 1
        elif scl and not self.prev_scl:                            # SCL rises: sample
            self._rise(t, sda)
        elif not scl and self.prev_scl:                            # SCL falls: drive
            self._fall(t)
        self.prev_scl, self.prev_sda = scl, sda
        hold = self.scl_hold_until is not None and t < self.scl_hold_until
        return (0 if hold else 1), self.sda_out

    def _rise(self, t, sda):
        if self.mode in ("addr", "write"):
            if self.bit < 8:
                self.shift = (self.shift << 1) | sda
            self.bit += 1
        elif self.mode == "read":
            self.bit += 1
            if self.bit == 9:                       # the master's ACK bit for the byte we sent
                self.tr["read"] += bytes([self.cur])
                self.tr["acks"].append(sda)
                self.ack = sda

    def _fall(self, t):
        m = self.mode
        if m in ("addr", "write"):
            if self.bit == 8:                                      # a byte: ACK it?
                b = self.shift & 0xFF
                if m == "addr":
                    if b >> 1 != self.addr:
                        self.mode, self.sda_out = "ignore", 1
                        return
                    if self.tr is None:
                        self.tr = {"t": t, "write": b"", "read": b"", "acks": [], "pec_ok": None, "sr": False,
                                   "bus": [], "out": []}
                        self.bytes_in_tr = 0
                    self._seen(b)
                    self.rw = b & 1
                    if self.rw:
                        cmd = self.tr["write"][0] if self.tr["write"] else None
                        data = list(self.regs.get(cmd, b""))
                        if self.pec:
                            data = data + [smbus_pec(bytes(self.tr["bus"]) + bytes(data))]
                        self.tr["out"] = data
                    self.sda_out = 0
                else:
                    self._seen(b)
                    self.tr["write"] += bytes([b])
                    self.sda_out = 0 if (self.nack_after is None or len(self.tr["write"]) <= self.nack_after) else 1
                self.bytes_in_tr += 1
            elif self.bit == 9:                                    # after our ACK
                self.sda_out = 1
                self._after_ack(t)
                if m == "addr":
                    self.mode = "read" if self.rw else "write"
                    if self.rw:
                        self._load_byte()
                        return
                self.bit, self.shift = 0, 0
        elif m == "read":
            if self.bit < 8:
                self.sda_out = (self.cur >> (7 - self.bit)) & 1
            elif self.bit == 8:
                self.sda_out = 1                                   # the master's ACK bit
            elif self.bit == 9:
                if self.ack == 0:
                    self._load_byte()
                else:
                    self.mode, self.sda_out = "done", 1

    def _load_byte(self):
        self.cur = self._next_read_byte()
        self.bit = 0
        self.sda_out = (self.cur >> 7) & 1

    def _after_ack(self, t):
        hold = self.stretch
        if self.hang is not None and self.bytes_in_tr == self.hang[0]:
            hold = self.hang[1]
        if hold:
            self.scl_hold_until = t + hold


# ================================================================== RC-5 infrared
# Philips RC-5: 36 kHz carrier, a half bit of 32 carrier periods (888.9 us),
# a 1 sent as a space then a burst, a 0 as a burst then a space; 14 bits MSB
# first: S1 = 1, S2 (1, or the complement of command bit 6 in RC-5X), the
# toggle bit, 5 address bits, 6 command bits; repeated every 64 bits
# (113.78 ms) while a key is held; the toggle bit changes on a new press.

RC5_F = 36e3
RC5_HALF = 32e9 / RC5_F          # ns
RC5_BIT = 2 * RC5_HALF


def rc5_word(address, command, repeat=False):
    """rc5_tx TX FIFO word (command 0..127: bit 6 sent as the complement of
    S2, RC-5X)."""
    return (command & 63) | ((command >> 6) & 1) << 6 | (address & 31) << 7 | int(repeat) << 12


def rc5_bits(address, command, toggle):
    """The 14 bits of a frame, S1 first."""
    s2 = 1 - ((command >> 6) & 1)
    v = 1 << 13 | s2 << 12 | (toggle & 1) << 11 | (address & 31) << 6 | (command & 63)
    return [(v >> (13 - k)) & 1 for k in range(14)]


def rc5_fields(bits14):
    """(address, command (7 bits), toggle) from 14 bits (or a 14-bit int)."""
    v = bits14 if isinstance(bits14, int) else int("".join(map(str, bits14)), 2)
    return (v >> 6) & 31, (v & 63) | (1 - ((v >> 12) & 1)) << 6, (v >> 11) & 1


def rc5_record(w):
    """rc5_rx RX FIFO word -> {kind: frame, address, command, toggle, s1}
    or {kind: error, bits_left}."""
    if w >> 16 == 1:
        a, c, t = rc5_fields(w & 0x3FFF)
        return {"kind": "frame", "address": a, "command": c, "toggle": t, "s1": (w >> 13) & 1}
    if w >> 16 == 2:
        return {"kind": "error", "bits_left": w & 0xFFFF}
    raise ValueError(f"not an rc5_rx word: {w:#x}")


def rc5_envelope(address, command, toggle, scale=1.0, stretch=0.0, bits=None):
    """An independent remote's demodulated output (a receiver module's: low
    during bursts) as [(level, duration)]: half bits of RC5_HALF * scale,
    every burst `stretch` ns longer (and the space after it as much
    shorter), as receiver modules do. bits: send these instead."""
    bits = rc5_bits(address, command, toggle) if bits is None else bits
    halves = []
    for b in bits:
        halves += [1 - b, b]                     # 1 = burst
    h = RC5_HALF * scale
    out = []
    for i, x in enumerate(halves):
        lvl = 0 if x else 1
        d = h
        if x:
            d += stretch
        elif i and halves[i - 1]:
            d -= stretch
        if out and out[-1][0] == lvl:
            out[-1] = (lvl, out[-1][1] + d)
        else:
            out.append((lvl, d))
    if out and out[-1][0] == 1:
        out.pop()
    return out


def rc5_decode(edges, t_end, tol=0.35):
    """A pins-only RC-5 decoder of a demodulated (active-low) line, written
    from the definition: runs of one or two half bits (+-tol); a frame
    starts at a fall, S1's mid-bit edge; returns [(t, 14 bits)] and the
    errors [(t, why)]."""
    rs = runs(edges, t_end)
    frames, errs, i = [], [], 0
    h = RC5_HALF
    near = lambda d, n: abs(d - n * h) <= tol * h
    while i < len(rs):
        lvl, t, d = rs[i]
        if lvl != 0:
            i += 1
            continue
        halves = [1]                              # S1's first half: the idle line; this run starts its burst
        j = i
        while len(halves) < 28 and j < len(rs):
            lv, tt, dd = rs[j]
            n = 1 if near(dd, 1) else 2 if near(dd, 2) else None
            if n is None:
                if lv == 1 and len(halves) >= 27:   # the idle line after a final 0's space
                    halves.append(1)
                    break
                errs.append((tt, f"run of {dd / 1e3:.0f} us"))
                break
            halves += [lv] * n
            j += 1
        if len(halves) >= 28:
            hv = halves[:28]
            bits = []
            for a, b in zip(hv[0::2], hv[1::2]):
                if a == b:
                    bits = None
                    break
                bits.append(1 if (a, b) == (1, 0) else 0)
            if bits:
                frames.append((t, bits))
            else:
                errs.append((t, "no mid-bit transition"))
        i = j + 1
    return frames, errs
