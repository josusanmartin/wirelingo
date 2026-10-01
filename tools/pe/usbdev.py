"""Host-side helpers for the USB low-speed device firmware (usb_ls_rx + usb_ls_tx).

The RX core pushes one record per DATA packet received after a SETUP/OUT to
us (firmware/usb_ls_rx.pasm): payload+CRC16 bits 24 per word in [31:8], a
partial last word, then a trailer with [7] = 1. The TX core sends a staged
packet when the host has pushed it and set F_STAGED.
"""
from dataclasses import dataclass

from . import usb

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
