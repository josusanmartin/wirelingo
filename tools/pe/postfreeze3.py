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
