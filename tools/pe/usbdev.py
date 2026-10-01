"""Host-side helpers for the USB low-speed device firmware (usb_ls_rx + usb_ls_tx).

Two firmware variants share this host protocol: usb_ls_rx + usb_ls_tx (two
cores, all in firmware) and usb_ls_dev (one core, with the line unit). The RX
side pushes one record per DATA packet received after a SETUP/OUT to us:
payload+CRC16 bits 24 per word in [31:8], a partial last word, then a trailer
with [7] = 1. A staged packet is sent when the host has pushed it and set
F_STAGED. DeviceSoftware is the reference device software (control requests,
re-staging after a lost ACK, the address filter after SET_ADDRESS).
"""
from dataclasses import dataclass

from . import sim, usb

FLAGS = dict(F_IN=0, F_ACK=1, F_EXP=2, F_SETUP=3, F_STAGED=4, F_GOTACK=5)
F_SETUP, F_STAGED, F_GOTACK = (1 << FLAGS[k] for k in ("F_SETUP", "F_STAGED", "F_GOTACK"))
CRCCTRL = 0x8005 | (15 << 16) | (1 << 20)   # CRC16, 16 bits, IN tap
TRAILER = 0x80


def defines(addr=0, endp=0, dp=0, dm=1):
    """Assembler defines for both programs (token filter for addr/endp)."""
    return {"DP": dp, "DM": dm, "TOKEN_FILTER": usb.token_filter(addr, endp), **FLAGS}


@dataclass
class Record:
    ok: bool          # CRC16 good (and ACKed)
    data: bytes       # payload, CRC16 removed
    nbits: int        # bits received, SYNC and PID included


def parse_records(words):
    """Split RX FIFO words into records. Returns (records, leftover words of an
    incomplete record). Unambiguous: only trailers have a non-zero low byte."""
    out, start = [], 0
    for i, w in enumerate(words):
        if not w & TRAILER:
            continue
        body, nbits = words[start:i], w >> 16
        n = max(nbits - 16, 0)                     # payload + CRC16 bits
        bits = []
        full, rem = divmod(n, 24)
        for k in range(full):
            bits += [(body[k] >> (8 + j)) & 1 for j in range(24)] if k < len(body) else []
        if rem and full < len(body):
            bits += [(body[full] >> (32 - rem + j)) & 1 for j in range(rem)]
        data = bytes(sum(b << j for j, b in enumerate(bits[8 * k:8 * k + 8])) for k in range(len(bits) // 8))
        good = bool(w & 1) and len(body) == full + 1 and n % 8 == 0
        out.append(Record(good, data[:-2] if good else data, nbits))
        start = i + 1
    return out, words[start:]


def stage_words(pid, payload):
    """TX FIFO words for one packet: bit count, then SYNC+PID+payload+CRC16 bytes."""
    pkt = bytes([0x80]) + usb.data_packet(pid, payload)
    return [8 * len(pkt)] + [int.from_bytes(pkt[i:i + 4].ljust(4, b"\0"), "little")
                             for i in range(0, len(pkt), 4)]


def filter_write(rx_prog, rx_base, addr, endp=0):
    """(IMEM address, word) that makes the RX core answer tokens to addr/endp:
    the `ldi r2, TOKEN_FILTER` at label `filter` with a new immediate."""
    a = rx_base + rx_prog.labels["filter"]
    word = rx_prog.relocate(rx_base)[rx_prog.labels["filter"]]
    return a, (word & ~0xFFFF) | usb.token_filter(addr, endp)


class DeviceSoftware:
    """What the RP2350 would run: read RX records over SPI, answer control requests.
    It only gets a turn between host transactions (via UsbHost.idle), and each
    turn is slow, so the chip's automatic NAKs cover the gap."""

    def __init__(self, mh, rx_prog, descriptors, rx_core=0, tx_core=1, rx_base=0):
        """rx_core == tx_core: the one-core firmware (usb_ls_dev), which drops a
        stale staged packet itself when a SETUP comes."""
        self.mh, self.rx_prog, self.descriptors = mh, rx_prog, descriptors
        self.rx, self.tx, self.rx_base = rx_core, tx_core, rx_base
        self.words = []
        self.pending = []      # 8-byte chunks still to send for the current request
        self.toggle = 1
        self.unacked = None    # (pid, payload) staged or sent, not yet acknowledged
        self.new_address = None
        self.address = 0
        self.requests = []
        self.restaged = 0

    def records(self):
        self.words += self.mh.pop_all(self.rx)
        recs, self.words = parse_records(self.words)
        return [r for r in recs if r.ok]       # bad packets were not ACKed: the host retries

    def stage(self, pid, payload):
        mh = self.mh
        mh.write(sim.A_FCLR, F_GOTACK)
        for w in stage_words(pid, payload):
            mh.push(self.tx, w)
        mh.write(sim.A_FSET, F_STAGED)
        self.unacked = (pid, payload)

    def set_address(self, addr):
        """Move the RX token filter (its core briefly disabled: docs/isa.md §3)."""
        mh = self.mh
        en = mh.chip.read(sim.A_CTRL)
        mh.write(sim.A_CTRL, en & ~(1 << self.rx))
        mh.write(*filter_write(self.rx_prog, self.rx_base, addr))
        mh.write(sim.A_CTRL, en)
        self.address = addr

    def service(self, mh):
        flags = mh.read(sim.A_FSET)
        for r in self.records():
            if flags & F_SETUP and len(r.data) == 8:
                mh.write(sim.A_FCLR, F_SETUP)
                self.requests.append(r.data.hex())
                self.handle(r.data)
                flags = mh.read(sim.A_FSET)
        if self.unacked and not flags & F_STAGED:      # the TX core sent it
            if flags & F_GOTACK:
                mh.write(sim.A_FCLR, F_GOTACK)
                self.unacked = None
                if self.new_address is not None and not self.pending:
                    self.set_address(self.new_address)      # after the status stage
                    self.new_address = None
            else:                                           # lost ACK: same packet again
                self.stage(*self.unacked)
                self.restaged += 1
        if not self.unacked and self.pending:
            chunk = self.pending.pop(0)
            self.stage(usb.PID_DATA1 if self.toggle else usb.PID_DATA0, chunk)
            self.toggle ^= 1

    def handle(self, req):
        bm, breq, wval, wlen = req[0], req[1], req[2] | req[3] << 8, req[6] | req[7] << 8
        self.toggle = 1
        if self.unacked:                       # a new SETUP abandons the old transfer
            self.mh.write(sim.A_FCLR, F_STAGED | F_GOTACK)
            if self.tx != self.rx:             # (the one-core firmware flushes its TX FIFO itself)
                en = self.mh.chip.read(sim.A_CTRL)
                self.mh.write(sim.A_CTRL, en | (1 << (16 + self.tx)))   # clear the TX core's FIFOs
            self.unacked = None
        if bm == 0x80 and breq == 6:           # GET_DESCRIPTOR
            desc = self.descriptors.get(wval >> 8, b"")[:wlen]
            self.pending = [desc[i:i + 8] for i in range(0, len(desc), 8)]
            if len(desc) % 8 == 0:
                self.pending.append(b"")       # terminate with a zero-length packet
        elif bm == 0x00 and breq == 5:         # SET_ADDRESS (status stage is a zero-length IN)
            self.new_address = wval
            self.pending = [b""]
        elif bm == 0x00 and breq == 9:         # SET_CONFIGURATION
            self.pending = [b""]
