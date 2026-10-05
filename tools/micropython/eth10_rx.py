"""Bounded 50-MHz Ethernet receive capture for the demo board.

Uses core 0 and the line unit. Raw records are drained while frames arrive;
decoding and the FCS check happen after reception stops. A finite capture,
not a network stack.
"""
from protoemu import (A_LINECFG, A_LINEDIV, A_LINEPINS, A_LINESTAT,
                      ST_HALTED, ST_FAULT, _crc32)
from protoemu_fw import ETH10_RX

PROJECT_HZ = 50_000_000
MAX_FRAME_BYTES = 1518  # Destination through FCS; no VLAN extension.
MAX_BITS = 64 + 8 * MAX_FRAME_BYTES
MAX_WORDS = MAX_BITS // 24 + 1  # Firmware always pushes a final partial/empty word.


class Records:
    """Collect bounded raw firmware records; ignore short link-pulse records."""
    def __init__(self):
        self.words = []
        self.discarding = False
        self.short = self.malformed = 0

    def feed(self, word):
        if not isinstance(word, int) or not 0 <= word <= 0xFFFFFFFF:
            raise ValueError("RX record must be a uint32")
        if word & 255 == 0:
            if not self.discarding:
                if len(self.words) == MAX_WORDS:
                    self.words = []
                    self.discarding = True
                else:
                    self.words.append(word)
            return None
        count = word >> 16
        words, self.words = self.words, []
        invalid = self.discarding
        self.discarding = False
        if invalid or word & 65535 != 0x80 or count > MAX_BITS or len(words) != count // 24 + 1:
            self.malformed += 1
            return None
        rem = count % 24
        if words[-1] & ((1 << (32 - rem)) - 1):
            self.malformed += 1
            return None
        if count < 40 + 8 + 8 * 64:
            self.short += 1
            return None
        return count, tuple(words)


def decode(record):
    """Return destination..FCS bytes only after preamble, length and CRC checks."""
    count, words = record
    if not 0 <= count <= MAX_BITS or len(words) != count // 24 + 1:
        raise ValueError("malformed Ethernet record length")
    full, rem = divmod(count, 24)
    if (any(not isinstance(w, int) or not 0 <= w <= 0xFFFFFFFF or w & 255 for w in words)
            or words[-1] & ((1 << (32 - rem)) - 1)):
        raise ValueError("malformed Ethernet record padding")

    def bit(index):
        if index < full * 24:
            return words[index // 24] >> (8 + index % 24) & 1
        return words[full] >> (32 - rem + index - full * 24) & 1

    # The first transition may lose a few preamble bits. Require an alternating
    # prefix of at least 40 bits and the SFD, rather than scanning arbitrary
    # payload/noise for an eight-bit pattern. Stop at the first repeated level.
    previous, shift, start = None, 0, None
    for i in range(min(count, 64)):
        b = bit(i)
        shift = (shift >> 1) | (b << 7)
        if previous == b:
            if b == 1 and shift == 0xD5 and i >= 47:
                start = i + 1
            break
        previous = b
    if start is None:
        raise ValueError("missing Ethernet preamble/SFD")
    length, tail = divmod(count - start, 8)
    if tail or not 64 <= length <= MAX_FRAME_BYTES:
        raise ValueError("runt, oversized or non-byte Ethernet frame")
    data = bytearray(length)
    for i in range(length):
        value = 0
        for k in range(8):
            value |= bit(start + 8 * i + k) << k
        data[i] = value
    if _crc32(data[:-4]) != int.from_bytes(data[-4:], "little"):
        raise ValueError("bad Ethernet FCS")
    return bytes(data)


class Capture:
    """Single-writer capture; call capture() for each finite packet burst.

    The selected SDK must report the actual 50-MHz project clock. Overflow,
    core failure, clock drift or timeout raises and stops RX. raw_frames and
    record counters remain available for diagnostics, including a bad FCS.
    """
    def __init__(self, pe):
        self.pe = pe
        self.raw_frames = []
        self.records = Records()
        self._clock()

    def _clock(self):
        if getattr(self.pe.tt, "auto_clocking_freq", None) != PROJECT_HZ:
            raise RuntimeError("Ethernet RX requires a verified 50-MHz project clock")

    def stop(self):
        self.pe.write(A_LINECFG, 0)
        self.pe.stop(1)

    def capture(self, timeout_us=100000, max_frames=1):
        if not isinstance(timeout_us, int) or not 1 <= timeout_us <= 1_000_000:
            raise ValueError("capture timeout must be 1..1000000 us")
        if not isinstance(max_frames, int) or not 1 <= max_frames <= 16:
            raise ValueError("capture frame count must be 1..16")
        self._clock()  # Reject before any configuration writes.
        self.raw_frames, self.records = [], Records()
        try:
            self.pe.stop(1)
            self.pe.write(A_LINECFG, 0)
            self.pe.clear_fifos(1)
            self.pe.load(**ETH10_RX)
            self.pe.write(A_LINEDIV, 5 << 16)
            self.pe.write(A_LINEPINS, 16 << 8)
            self.pe.write(A_LINECFG, 0x4005)
            self.pe.start(1)
            before = self.pe.now_us()
            while len(self.raw_frames) < max_frames:
                self._clock()
                if self.pe.read(A_LINESTAT) & (1 << 19):
                    raise RuntimeError("Ethernet RX overflow; captured frames invalid")
                if self.pe.status(0) & (ST_HALTED | ST_FAULT):
                    raise RuntimeError("Ethernet RX core halted or faulted")
                words = self.pe.pop_all(0)
                # Overflow may occur during the SPI burst; check before accepting
                # its trailers. Never trust a complete-looking truncated frame.
                if self.pe.read(A_LINESTAT) & (1 << 19):
                    raise RuntimeError("Ethernet RX overflow; captured frames invalid")
                for word in words:
                    record = self.records.feed(word)
                    if record is not None:
                        self.raw_frames.append(record)
                        if len(self.raw_frames) == max_frames:
                            break
                if self.pe.elapsed_us(self.pe.now_us(), before) >= timeout_us:
                    raise RuntimeError("Ethernet RX capture timed out")
            self._clock()
            if self.pe.read(A_LINESTAT) & (1 << 19):
                raise RuntimeError("Ethernet RX overflow; captured frames invalid")
        except BaseException:
            # Preserve the capture error if a changed clock or failed transport
            # also prevents cleanup. Reset/recreate the transport before retrying.
            try:
                self.stop()
            except Exception:
                pass
            raise
        else:
            self.stop()
        self._clock()
        return [decode(record) for record in self.raw_frames]
