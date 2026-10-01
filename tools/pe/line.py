"""The line unit (docs/isa.md §4.7) in the golden model: line coding, bit
stuffing, bit timing and clock recovery, fed and drained by core 0.

Every decision uses start-of-cycle state; Chip.step calls core 0 first (which
may push or pop through `push`/`take`), then `step`, then `commit` applies the
cycle's queue changes."""

M16 = 0xFFFF
NRZ, NRZI, MANCH = 0, 1, 2


class Line:
    def __init__(self):
        self.cfg = self.div = self.pins = 0
        self.reset()

    def reset(self):
        # TX
        self.entries = []            # queued OUT LINE entries: lists of bits, send order
        self.tx_on = 0
        self.ending = 0              # end symbols left (0: not ending)
        self.eop_idle = 0            # END=1: in the trailing IDLE bit
        self.half = self.bit = 0
        self.level = self.txn = 0
        self.tcnt = self.tfacc = 0
        self.trun, self.trunbit = 0, None
        # RX
        self.rq = []                 # received bits, oldest first (<= 32)
        self.rx_on = 0
        self.rcnt = 0
        self.rlast = self.rprev = 0
        self.quiet = 0
        self.mt = 0                  # Manchester: cycles since the last mid-bit transition
        self.rrun, self.rrunbit = 0, None
        self.end_pend = self.ovf = 0
        # this cycle's core-0 traffic
        self._push = None
        self._take = 0
        self._clear_end = False

    # -- configuration fields
    def f(self, lo, width, reg=None):
        return ((self.cfg if reg is None else reg) >> lo) & ((1 << width) - 1)

    @property
    def en(self): return self.f(0, 1)
    @property
    def code(self): return self.f(1, 2)
    @property
    def stuff_k(self): return self.f(3, 3)
    @property
    def stuff_ones(self): return self.f(6, 1)
    @property
    def diff(self): return self.f(7, 1)
    @property
    def end(self): return self.f(8, 2)
    @property
    def end_bits(self): return self.f(10, 3)
    @property
    def idle(self): return self.f(13, 1)
    @property
    def rx_en(self): return self.f(14, 1)
    @property
    def txp(self): return self.pins & 31
    @property
    def rxp(self): return (self.pins >> 8) & 31

    def write(self, off, v):
        if off == 0:
            self.cfg = v & 0x7FFF
            if not self.en:
                cfg, div, pins = self.cfg, self.div, self.pins
                self.reset()
                self.cfg, self.div, self.pins = cfg, div, pins
        elif off == 1:
            self.div = v & 0xFFFFFFFF
        elif off == 2:
            self.pins = v & 0x1F1F

    def read(self, off):
        if off == 0:
            return self.cfg
        if off == 1:
            return self.div
        if off == 2:
            return self.pins
        if off == 3:
            return (len(self.entries) | (len(self.rq) << 8) | (self.tx_on << 16)
                    | (self.rx_on << 17) | (self.end_pend << 18) | (self.ovf << 19))
        return 0

    # -- pins: what the transmitter drives this cycle (from its registered state)
    def drive(self):
        """{pin: (value, oe)} overrides, or {} when not transmitting."""
        if not (self.en and self.tx_on):
            return {}
        d = {self.txp: self.level}
        if self.diff:
            d[(self.txp + 1) & 31] = self.txn
        return d

    # -- core 0 (start-of-cycle state)
    def room(self):
        return self.en and len(self.entries) < 2

    def push(self, bits):
        self._push = list(bits)

    def peek(self, n):
        """IN LINE n from the start-of-cycle queue: (bits, T), or None to stall."""
        if not self.en:
            return None
        if len(self.rq) >= n:
            return self.rq[:n], 0
        if self.end_pend:
            return list(self.rq), 1
        return None

    def consume(self, k, t):
        """IN LINE completed: k bits taken; T = 1 also clears the pending end."""
        self._take = k
        self._clear_end = bool(t)

    # -- one cycle of the unit
    def step(self, pins_in):
        if not self.en:
            self._push, self._take, self._clear_end = None, 0, False
            return
        self._new_rx = None
        self._new_end = False
        self._tx_step()
        if self.rx_en:
            self._rx_step(pins_in)
        else:
            self.rprev = (pins_in >> self.rxp) & 1

    def commit(self):
        """Apply this cycle's queue changes (core-0 traffic and the unit's)."""
        if not self.en:
            return
        start_len = len(self.rq) + 0
        if self._take:
            self.rq = self.rq[self._take:]
        if self._clear_end:
            self.end_pend = 0
        if self._new_rx is not None:
            if start_len < 32:
                self.rq.append(self._new_rx)
            else:
                self.ovf = 1
        if self._new_end:
            self.end_pend = 1
        if self._push is not None:
            self.entries.append(self._push)
        self._push, self._take, self._clear_end = None, 0, False

    # -- transmitter
    def _reload(self):
        s = self.tfacc + (self.div & 0xFF)
        self.tfacc = s & 0xFF
        self.tcnt = (((self.div >> 8) & 0xFF) - 1 + (s >> 8)) & M16

    def _spb(self):
        return 2 if self.code == MANCH else 1

    def _next_bit(self):
        k = self.stuff_k
        if k and self.trun == k:
            b = 0 if self.stuff_ones else 1 - self.trunbit
            if self.stuff_ones:
                self.trun = 0
            else:
                self.trun, self.trunbit = 1, b
        elif self._entries_left():
            b = self._pop_bit()
            if self.stuff_ones:
                self.trun = self.trun + 1 if b else 0
            else:
                self.trun = self.trun + 1 if b == self.trunbit else 1
                self.trunbit = b
        else:                                    # nothing left: end the frame
            if self.end_bits == 0 or self.end == 0:
                self.tx_on = 0
                return
            self.ending = self.end_bits * self._spb()
            if self.end == 1:
                self.level, self.txn = 0, 0
            elif self.end == 2:
                self.level, self.txn = 1, 0
            return
        self.bit = b
        if self.code == NRZI:
            if b == 0:
                self.level ^= 1
        elif self.code == MANCH:
            self.half = 0
            self.level = 1 - b
        else:
            self.level = b
        self.txn = 1 - self.level

    def _entries_left(self):
        # bits not yet taken by this cycle (entries hold only unsent bits)
        return bool(self._tx_entries)

    def _pop_bit(self):
        head = self._tx_entries[0]
        b = head.pop(0)
        if not head:
            self._tx_entries.pop(0)
        return b

    def _tx_step(self):
        # the unit consumes from the start-of-cycle entries (copied: core 0's
        # push this cycle is appended in commit)
        self._tx_entries = self.entries
        if not self.tx_on:
            if self.entries:
                self.tx_on = 1
                self.level = self.idle
                self.txn = 1 - self.idle
                self.trun, self.trunbit = 0, None
                self.ending = self.eop_idle = 0
                self._reload()
                self._next_bit()
            return
        if self.tcnt != 0:
            self.tcnt = (self.tcnt - 1) & M16
            return
        self._reload()
        if self.ending:
            self.ending -= 1
            if self.ending == 0:
                if self.end == 1 and not self.eop_idle:
                    self.eop_idle = 1                # USB EOP: one bit of IDLE after SE0
                    self.ending = self._spb()
                    self.level, self.txn = self.idle, 1 - self.idle
                else:
                    self.tx_on = 0
        elif self.code == MANCH and self.half == 0:
            self.half = 1
            self.level = self.bit
            self.txn = 1 - self.level
        else:
            self._next_bit()

    # -- receiver
    def _rx_step(self, pins_in):
        rx = (pins_in >> self.rxp) & 1
        rxn = (pins_in >> ((self.rxp + 1) & 31)) & 1
        trans = rx != self.rprev
        p = (self.div >> 16) & M16
        if not self.rx_on:
            if trans:
                self.rx_on = 1
                self.rrun, self.rrunbit = 0, None
                self.quiet = 0
                if self.code == MANCH:
                    self.mt = 0
                    self._deliver(rx)
                else:
                    self.rlast = self.rprev
                    self.rcnt = ((p >> 1) - 1) & M16
        elif self.code == MANCH:
            self.mt = (self.mt + 1) & M16
            if trans and self.mt >= (3 * p) >> 2:
                self.mt = 0
                self._deliver(rx)
            elif self.mt >= (3 * p) >> 1:
                self._end()
        else:
            if trans:
                self.rcnt = ((p >> 1) - 1) & M16
                self.quiet = 0
            elif self.rcnt == 0:
                self.rcnt = (p - 1) & M16
                self._sample(rx, rxn)
            else:
                self.rcnt = (self.rcnt - 1) & M16
        self.rprev = rx

    def _sample(self, rx, rxn):
        if self.diff and rx == 0 and rxn == 0:
            self._end()
            return
        self.quiet += 1
        if self.quiet >= (self.stuff_k + 2 if self.stuff_k else 8):
            self._end()
            return
        b = rx if self.code == NRZ else int(rx == self.rlast)
        self.rlast = rx
        self._deliver(b)

    def _deliver(self, b):
        k = self.stuff_k
        if k and self.rrun == k:                       # a stuff bit
            expect = 0 if self.stuff_ones else 1 - self.rrunbit
            if b != expect:
                self._end()
                return
            if self.stuff_ones:
                self.rrun = 0
            else:
                self.rrun, self.rrunbit = 1, b
            return
        if self.stuff_ones:
            self.rrun = self.rrun + 1 if b else 0
        else:
            self.rrun = self.rrun + 1 if b == self.rrunbit else 1
            self.rrunbit = b
        self._new_rx = b

    def _end(self):
        self.rx_on = 0
        self._new_end = True
