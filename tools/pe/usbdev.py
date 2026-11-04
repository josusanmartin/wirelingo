"""Host-side helpers for the USB low-speed device firmware (usb_ls_rx + usb_ls_tx).

Two firmware variants share this host protocol: usb_ls_rx + usb_ls_tx (two
cores, all in firmware) and usb_ls_dev (one core, with the line unit). The RX
side pushes one record per DATA packet received after a SETUP/OUT to us:
payload+CRC16 bits 24 per word in [31:8], a partial last word, then a trailer
with [7] = 1, reversed PID in [15:8], SETUP association in [1], and good in [0].
This is USB record ABI v2; firmware and generated board service must match.
A staged packet is sent when the host has pushed it and set
F_STAGED. DeviceSoftware is the reference device software (control requests,
re-staging after a lost ACK, the address filter after SET_ADDRESS).
"""
from . import sim, usb

FLAGS = dict(F_IN=0, F_ACK=1, F_EXP=2, F_SETUP=3, F_STAGED=4, F_GOTACK=5, F_RESET=6, F_SENT=7)
F_SETUP, F_STAGED, F_GOTACK, F_SENT = (1 << FLAGS[k] for k in ("F_SETUP", "F_STAGED", "F_GOTACK", "F_SENT"))
CRCCTRL = 0x8005 | (15 << 16) | (1 << 20)   # CRC16, 16 bits, IN tap
TRAILER = 0x80
F_RESET = 1 << FLAGS["F_RESET"]


def defines(addr=0, endp=0, dp=0, dm=1):
    """Assembler defines for both programs (token filter for addr/endp)."""
    return {"DP": dp, "DM": dm, "TOKEN_FILTER": usb.token_filter(addr, endp), **FLAGS}


class Record:
    def __init__(self, ok, data, nbits, pid=0xC3, setup=False):
        self.ok, self.data, self.nbits, self.pid, self.setup = ok, data, nbits, pid, setup

    def __eq__(self, other):
        return isinstance(other, Record) and vars(self) == vars(other)

    def __repr__(self):
        return "Record(%r, %r, %r, pid=%r, setup=%r)" % (self.ok, self.data, self.nbits, self.pid, self.setup)


DEVICE_DESC = bytes([18, 1, 0x10, 0x01, 0, 0, 0, 8, 0x09, 0x12, 0x34, 0x56, 1, 0, 0, 0, 0, 1])
CONFIG_DESC = bytes([9, 2, 18, 0, 1, 1, 0, 0x80, 50, 9, 4, 0, 0, 0, 0xFF, 0, 0, 0])
DESCRIPTORS = {1: DEVICE_DESC, 2: CONFIG_DESC}


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
        pid = usb.rev((w >> 8) & 255, 8)
        good = good and pid in (usb.pid_byte(usb.PID_DATA0), usb.pid_byte(usb.PID_DATA1))
        out.append(Record(good, data[:-2] if good else data, nbits, pid, bool(w & 2)))
        start = i + 1
    return out, words[start:]


def stage_words(pid, payload):
    """TX FIFO words for one packet: bit count, then SYNC+PID+payload+CRC16 bytes."""
    if pid not in (usb.PID_DATA0, usb.PID_DATA1) or len(payload) > 8:
        raise ValueError("low-speed endpoint-zero packet must be DATA0/1 and <=8 bytes")
    pkt = bytes([0x80]) + usb.data_packet(pid, payload)
    return [8 * len(pkt)] + [int.from_bytes(pkt[i:i + 4] + bytes(4 - len(pkt[i:i + 4])), "little")
                             for i in range(0, len(pkt), 4)]


def filter_write(rx_prog, rx_base, addr, endp=0):
    """(IMEM address, word) that makes the RX core answer tokens to addr/endp:
    the `ldi r2, TOKEN_FILTER` at label `filter` with a new immediate."""
    if isinstance(rx_prog, dict):   # validated, relocated MicroPython bundle
        idx = rx_prog["filter_index"]
        a, word = rx_base + idx, rx_prog["words"][idx]
    else:
        a = rx_base + rx_prog.labels["filter"]
        word = rx_prog.relocate(rx_base)[rx_prog.labels["filter"]]
    return a, (word & ~0xFFFF) | usb.token_filter(addr, endp)


class DeviceSoftware:
    """Endpoint-zero example on a read/write/push/pop_all transport.

    Service must be called regularly; automatic NAKs cover software latency.
    Simulation can schedule service concurrently with packet traffic. The board
    bundle emits this same control code as usb_device.py, without pe imports.
    """

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
        self.reset_wait = False
        self.reset_enable = 0
        self.out_toggle = 1
        self.out_records = []
        self.sent_at = None
        self.dirty = False     # the TX FIFO may hold words a SETUP's flush missed
        self.tx_clears = 0

    def records(self):
        self.words += self.mh.pop_all(self.rx)
        recs, self.words = parse_records(self.words)
        return [r for r in recs if r.ok]       # bad packets were not ACKed: the host retries

    def setup_since(self, flags):
        """A SETUP since the records were read: F_SETUP (`flags`, read first)
        marks its token up to the end of its DATA; from then on its record is
        in the RX FIFO. A SETUP whose token came after `flags` was read flushes
        every word pushed before."""
        rx_level = self.mh.read(sim.CORE_BASE + sim.CORE_STRIDE * self.rx + sim.C_STATUS) >> 4 & 15
        return bool(flags & F_SETUP or rx_level)

    def stage(self, pid, payload, racing=False):
        """racing: the host may have moved on to a new SETUP (a re-stage), so
        F_STAGED is set only if no SETUP came while the words went in; one that
        came leaves the TX FIFO dirty, emptied before the next stage."""
        mh = self.mh
        if self.dirty:                             # the host waits for this packet: no record is due
            self.tx_clears += 1
            if self.rx == self.tx:
                self.words += mh.pop_all(self.rx)  # the clear empties both FIFOs: keep the records
            mh.write(sim.A_CTRL, mh.read(sim.A_CTRL) & 15 | 1 << (16 + self.tx))
            self.dirty = False
        mh.write(sim.A_FCLR, F_GOTACK)
        for w in stage_words(pid, payload):
            mh.push(self.tx, w)
        self.unacked = (pid, payload)
        self.sent_at = None
        if racing and self.setup_since(mh.read(sim.A_FSET)):
            self.dirty = True                      # not staged: the words may follow its flush
            return
        mh.write(sim.A_FSET, F_STAGED)
        if racing and self.setup_since(mh.read(sim.A_FSET)):
            mh.write(sim.A_FCLR, F_STAGED)         # its flush may have emptied the FIFO first (an IN
            self.dirty = True                      # meanwhile is NAKed: the firmware's got_in guard)

    def set_address(self, addr):
        """Move the RX token filter (its core briefly disabled: docs/isa.md §3)."""
        mh = self.mh
        en = mh.read(sim.A_CTRL)
        mh.write(sim.A_CTRL, en & ~(1 << self.rx))
        mh.write(*filter_write(self.rx_prog, self.rx_base, addr))
        mh.write(sim.A_CTRL, en)
        self.address = addr

    def service(self, mh=None):
        mh = self.mh if mh is None else mh
        flags = mh.read(sim.A_FSET)
        if flags & F_RESET or self.reset_wait:
            self.bus_reset()
            return
        for r in self.records():
            if r.setup and len(r.data) == 8 and r.pid == usb.pid_byte(usb.PID_DATA0):
                self.requests.append(r.data.hex())
                self.handle(r.data)
            elif not r.setup:
                expected = usb.pid_byte(usb.PID_DATA1 if self.out_toggle else usb.PID_DATA0)
                if r.pid == expected:
                    self.out_records.append(r.data)
                    self.out_toggle ^= 1
        flags = mh.read(sim.A_FSET)  # SPI service may have spanned a SETUP/ACK transition
        if self.unacked and not flags & F_STAGED:      # the TX core sent it
            if flags & F_GOTACK:
                mh.write(sim.A_FCLR, F_GOTACK)
                self.unacked = None
                self.sent_at = None
                if self.new_address is not None and not self.pending:
                    self.set_address(self.new_address)      # after the status stage
                    self.new_address = None
            else:
                # STAGED clears at the START of transmission. Wait for the
                # maximum eight-byte packet plus host ACK before retrying.
                now = mh.now_us()
                if self.sent_at is None:
                    self.sent_at = now
                elapsed = mh.elapsed_us(now, self.sent_at) if hasattr(mh, "elapsed_us") else now - self.sent_at
                # Not if a SETUP came after the records were read: it cleared
                # F_GOTACK and flushed the TX FIFO, so a packet staged now would
                # be a stale one waiting there. stage() checks again around FSET.
                if elapsed >= 100 and not self.setup_since(flags):
                    self.stage(*self.unacked, racing=True)
                    self.restaged += 1
        if not self.unacked and self.pending:
            chunk = self.pending.pop(0)
            self.stage(usb.PID_DATA1 if self.toggle else usb.PID_DATA0, chunk)
            self.toggle ^= 1

    def bus_reset(self):
        """Reset endpoint zero and wait for J before restarting firmware.

        F_RESET is raised by firmware after sustained SE0. Disabling LINECFG
        also clears stale line-unit RX/TX queues in the one-core variant.
        """
        mh = self.mh
        mask = (1 << self.rx) | (1 << self.tx)
        if not self.reset_wait:
            self.reset_enable = mh.read(sim.A_CTRL)
            mh.write(sim.A_CTRL, (self.reset_enable & ~mask) | (mask << 16))
            if self.rx == self.tx:
                mh.write(sim.A_LINECFG, 0)
            mh.write(sim.A_POE, mh.read(sim.A_POE) & ~3)
            mh.write(*filter_write(self.rx_prog, self.rx_base, 0))
            mh.write(sim.A_FCLR, 0x3F)
            self.words, self.pending = [], []
            self.dirty = False                 # the FIFOs were just cleared
            self.unacked = self.new_address = None
            self.sent_at = None
            self.address = 0
            self.toggle = self.out_toggle = 1
            self.reset_wait = True
        if mh.read(sim.A_PINS) & 3 == 2:
            if self.rx == self.tx:
                mh.write(sim.A_LINECFG, 0x49F3)
            mh.write(sim.A_FCLR, 0x7F)
            mh.write(sim.A_CTRL, self.reset_enable | (mask << 8))
            self.reset_wait = False

    def handle(self, req):
        bm, breq, wval, wlen = req[0], req[1], req[2] | req[3] << 8, req[6] | req[7] << 8
        self.toggle = 1
        self.out_toggle = 1
        self.new_address = None
        self.pending = []
        self.sent_at = None
        if self.unacked:                       # a new SETUP abandons the old transfer
            self.mh.write(sim.A_FCLR, F_STAGED | F_GOTACK)
            self.unacked = None
        if self.tx != self.rx:                 # clear the TX core's FIFOs (the one-core
            en = self.mh.read(sim.A_CTRL)      # firmware flushes its TX FIFO itself)
            self.mh.write(sim.A_CTRL, en | (1 << (16 + self.tx)))
        if bm == 0x80 and breq == 6:           # GET_DESCRIPTOR
            desc = self.descriptors.get(wval >> 8, b"")[:wlen]
            self.pending = [desc[i:i + 8] for i in range(0, len(desc), 8)]
            if len(desc) % 8 == 0 and (len(desc) < wlen or not wlen):
                self.pending.append(b"")       # short of wLength: end with a zero-length packet
                                               # (wLength 0: the status stage is an IN)
        elif bm == 0x00 and breq == 5:         # SET_ADDRESS (status stage is a zero-length IN)
            self.new_address = wval
            self.pending = [b""]
        elif bm == 0x00 and breq == 9:         # SET_CONFIGURATION
            self.pending = [b""]
