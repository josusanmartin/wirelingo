"""Cycle-accurate golden model of pe_chip (everything except the SPI slave).

Implements docs/isa.md. One call to Chip.step() == one rising clock edge.
Everything reads start-of-cycle state; effects combine into the next state,
mirroring the synchronous RTL.
"""
from . import isa
from .isa import (PINS, PINDIRS, ISR, OSR, STATUS, FLAGS, PC, CRC, LINE)
from .line import Line

M32 = 0xFFFFFFFF
M16 = 0xFFFF
NCORES = 4
FIFO_DEPTH = 4
ID_VALUE = 0x50450002
IMEM_WORDS = 1024

# Register map
A_ID, A_CTRL, A_FSET, A_FCLR, A_BANK, A_POWN_LO, A_POWN_HI, A_OD, A_PINS, A_POUT, A_POE, A_HOSTCFG = range(0x800, 0x80C)
A_LINECFG, A_LINEDIV, A_LINEPINS, A_LINESTAT = range(0x80C, 0x810)
CORE_BASE, CORE_STRIDE = 0x400, 0x20
C_CLKDIV, C_PINCTRL0, C_PINCTRL1, C_SHIFTCTRL, C_ENTRY, C_WRAP, C_CRCCTRL, C_CRC = 0, 1, 2, 3, 4, 5, 6, 7
C_PC, C_STATUS, C_R0, C_ISR, C_OSR, C_FIFO = 8, 9, 0xA, 0xE, 0xF, 0x10


def core_reg(c, off):
    return CORE_BASE + CORE_STRIDE * c + off


def mask(n):
    return (1 << n) - 1


def rotr32(v, s):
    s &= 31
    return ((v >> s) | (v << (32 - s))) & M32 if s else v


def rev32(v):
    return int(f"{v:032b}"[::-1], 2)


class Core:
    def __init__(self, idx):
        self.idx = idx
        self.div_int, self.div_frac = 1, 0
        self.out_base = self.out_count = self.set_base = self.set_count = 0
        self.side_base = self.side_count = self.side_opt = self.side_pindir = self.in_base = 0
        self.out_shr = self.in_shr = self.autopull = self.autopush = 0
        self.pull_thresh = self.push_thresh = 0
        self.fjoin = 0  # SHIFTCTRL.FJOIN_TX: RXF becomes a second TX stage (TXF -> RXF -> OSR)
        self.entry, self.wrap_bottom, self.wrap_top = 256 * idx, 0, 1023
        self.crc_poly = self.crc_w = self.in_tap = self.out_tap = 0
        self.crc = 0
        self.r = [0, 0, 0, 0]
        self.txf, self.rxf = [], []
        self.res = []  # host read reservations, oldest first: True if a real RXF entry (<= 2)
        self.restart()

    def restart(self):
        self.pc = self.entry
        self.isr = self.isr_cnt = self.osr = 0
        self.osr_cnt = 32
        self.z = self.c = self.t = 0
        self.dcnt = 0
        self.in_wait = self.wprev = self.wevent = 0
        self.cnt = self.facc = 0
        self.halted = self.fault = self.stalled = 0

    def status(self):
        return (len(self.txf) | (len(self.rxf) << 4) | (self.z << 8) | (self.c << 9)
                | (self.t << 10) | (self.stalled << 12) | (self.halted << 13) | (self.fault << 14))

    def read_reg(self, off):
        # configuration registers (CLKDIV .. CRCCTRL) are write-only: read 0
        if off == C_CRC:
            return self.crc
        if off == C_PC:
            return self.pc
        if off == C_STATUS:
            return self.status()
        if C_R0 <= off < C_R0 + 4:
            return self.r[off - C_R0]
        if off == C_ISR:
            return self.isr
        if off == C_OSR:
            return self.osr
        if off == C_FIFO:            # the next unread entry: behind the real reservations
            n = sum(self.res)
            return self.rxf[n] if not self.fjoin and n < 2 and len(self.rxf) > n else 0
        return 0

    def write_reg(self, off, v):
        """Config / debug register writes (FIFO pushes handled by the chip)."""
        if off == C_CLKDIV:
            self.div_int, self.div_frac = (v >> 8) & M16, v & 0xFF
        elif off == C_PINCTRL0:
            self.out_base, self.out_count = v & 31, (v >> 8) & 63
            self.set_base, self.set_count = (v >> 16) & 31, (v >> 24) & 7
        elif off == C_PINCTRL1:
            self.side_base, self.side_count = v & 31, (v >> 8) & 7
            self.side_opt, self.side_pindir = (v >> 12) & 1, (v >> 13) & 1
            self.in_base = (v >> 16) & 31
        elif off == C_SHIFTCTRL:
            self.out_shr, self.in_shr = v & 1, (v >> 1) & 1
            self.autopull, self.autopush = (v >> 2) & 1, (v >> 3) & 1
            self.fjoin = (v >> 4) & 1
            self.pull_thresh, self.push_thresh = (v >> 8) & 31, (v >> 16) & 31
        elif off == C_ENTRY:
            self.entry = v & 1023
        elif off == C_WRAP:
            self.wrap_bottom, self.wrap_top = v & 1023, (v >> 16) & 1023
        elif off == C_CRCCTRL:
            self.crc_poly, self.crc_w = v & M16, (v >> 16) & 15
            self.in_tap, self.out_tap = (v >> 20) & 1, (v >> 21) & 1
        elif off == C_CRC:
            self.crc = v & M16
        elif off == C_PC:
            self.pc = v & 1023
            self.dcnt = 0
            self.in_wait = 0
        elif C_R0 <= off < C_R0 + 4:
            self.r[off - C_R0] = v & M16

    # ------------------------------------------------------------ one cycle
    def step(self, chip, pins_in, flags):
        """Advance one enabled cycle. Mutates own non-FIFO state; returns effects."""
        op, sd, x = isa.fields(chip.fetch(self.pc))
        e = _Exec(self, chip, pins_in, flags, x)
        tick = self._divider()
        self._track_wait(e)
        if not tick or self.halted or self.fault:
            return e.fx
        if self.dcnt:
            self.dcnt -= 1
            return e.fx
        if chip.bank_owner[self.pc >> 8] != self.idx:
            self.fault = self.halted = 1
            self.stalled = 0
            chip.cover("core:bank_fault")
            return e.fx
        delay = self._side_set(sd, e.fx)
        name = isa.OPNAMES.get(op, "rsvd")
        chip.cover(f"attempt:{name}")
        handler = _OPS.get(op)
        if handler:
            handler(self, e)            # reserved opcodes complete as NOP
        chip.cover(("done:" if e.complete else "stall:") + name)
        if e.jump is not None:
            chip.cover("jump")
        self.stalled = int(not e.complete and op != isa.OP_HALT)
        self.in_wait = e.new_wait
        if e.complete:
            self.dcnt = delay
            if e.jump is not None:
                self.pc = e.jump
            elif self.pc == self.wrap_top:
                self.pc = self.wrap_bottom
            else:
                self.pc = (self.pc + 1) & 1023
        return e.fx

    def _divider(self):
        """Fractional clock divider: True on a tick."""
        if self.cnt == 0:
            s = self.facc + self.div_frac
            self.facc = s & 0xFF
            self.cnt = (self.div_int - 1 + (s >> 8)) & M16
            return True
        self.cnt = (self.cnt - 1) & M16
        return False

    def _track_wait(self, e):
        """WAIT edge tracker: runs every enabled cycle while a WAIT is in progress."""
        x = e.x
        e.kind = isa.bits(x, 18, 16)
        e.wpin = e.pin(isa.bits(x, 15, 11))
        qen, qpol, qpin = isa.bits(x, 6, 6), isa.bits(x, 5, 5), isa.bits(x, 4, 0)
        e.qual = (not qen) or e.pin(qpin) == qpol
        if e.kind == isa.W_RISE:
            trans = (not self.wprev) and e.wpin
        elif e.kind == isa.W_FALL:
            trans = self.wprev and not e.wpin
        else:
            trans = self.wprev != e.wpin
        e.ev = bool(trans and e.qual)
        e.edge_done = bool(self.wevent or e.ev)
        if self.in_wait and qen and trans and not e.qual and e.kind in _EDGE_WAITS:
            e.chip.cover("wait:qualifier_blocked_edge")
        if self.in_wait:
            self.wevent = int(e.edge_done)
            self.wprev = e.wpin

    def _side_set(self, sd, fx):
        """Drive the side-set pins; returns the delay."""
        sc = min(self.side_count, 4)
        dbits = 8 - sc - self.side_opt
        side_en = ((sd >> 7) & 1) if self.side_opt else 1
        if sc and side_en:
            sv = (sd >> dbits) & mask(sc)
            tgt = fx["side_oe"] if self.side_pindir else fx["side_out"]
            for i in range(sc):
                tgt[(self.side_base + i) & 31] = (sv >> i) & 1
        return sd & mask(dbits)

    # ------------------------------------------------------------ helpers for the ops
    def _write_pins(self, e, v, oe):
        tgt = e.fx["oe"] if oe else e.fx["out"]
        for i in range(min(self.out_count, 32)):
            tgt[(self.out_base + i) & 31] = (v >> i) & 1

    def _src(self, e, code):
        if code < 4:
            return self.r[code]
        return {PINS: rotr32(e.pins_in, self.in_base), ISR: self.isr, OSR: self.osr,
                STATUS: self.status() & 0x7FF, FLAGS: e.flags, PC: self.pc, CRC: self.crc}.get(code, 0)

    def _crc_mask(self):
        return mask(self.crc_w + 1)

    def _crc_feed(self, b):
        fb = ((self.crc >> self.crc_w) & 1) ^ b
        m = self._crc_mask()
        self.crc = ((self.crc << 1) & m) ^ ((self.crc_poly & m) if fb else 0)

    def _write_dst(self, e, dst, v, isr_count):
        """MOV/OUT destination write (OSR is MOV-only: OUT never passes it)."""
        if dst < 4:
            self.r[dst] = v & M16
        elif dst == PINS:
            self._write_pins(e, v, False)
        elif dst == PINDIRS:
            self._write_pins(e, v, True)
        elif dst == ISR:
            self.isr, self.isr_cnt = v, isr_count
        elif dst == OSR:
            self.osr, self.osr_cnt = v, 0
        elif dst == PC:
            e.jump = v & 1023
        elif dst == CRC:
            self.crc = v & self._crc_mask()

    # ------------------------------------------------------------ the ops (docs/isa.md §5)
    def _op_jmp(self, e):
        x = e.x
        cond, sel, addr = isa.bits(x, 18, 15), isa.bits(x, 14, 10), isa.bits(x, 9, 0)
        rs = sel & 3
        rv = self.r[rs]
        ok = [
            True, rv == 0, rv != 0, rv != 0,
            e.pin(sel) == 1, e.pin(sel) == 0,
            self.z == 1, self.z == 0, self.c == 1, self.c == 0, self.t == 1,
            self.osr_cnt < e.pull_th, len(e.pullq) > 0, e.rx_full,
            (e.flags >> (sel & 7)) & 1 == 1, (e.flags >> (sel & 7)) & 1 == 0,
        ][cond]
        if cond == isa.C_DEC:
            self.r[rs] = (rv - 1) & M16
        if ok:
            e.jump = addr

    def _op_wait(self, e):
        x, kind = e.x, e.kind
        idx = isa.bits(x, 15, 11)
        to, treg, clr = isa.bits(x, 10, 10), isa.bits(x, 9, 8), isa.bits(x, 7, 7)
        first = not self.in_wait
        f = (e.flags >> (idx & 7)) & 1
        if kind == isa.W_PIN1:
            done = e.wpin == 1 and e.qual
        elif kind == isa.W_PIN0:
            done = e.wpin == 0 and e.qual
        elif kind in _EDGE_WAITS:
            done = (not first) and e.edge_done
        elif kind == isa.W_FLAG1:
            done = f == 1 and e.qual
        elif kind == isa.W_FLAG0:
            done = f == 0 and e.qual
        else:
            done = True
        if done:
            if kind in _EDGE_WAITS and not e.ev:
                e.chip.cover("wait:edge_caught_between_ticks")
            if to:
                self.t = 0
            if kind == isa.W_FLAG1 and clr:
                e.fx["fclr"] |= 1 << (idx & 7)
        elif to and self.r[treg] == 0:
            self.t = 1
            e.chip.cover("wait:timeout")
        else:
            if to:
                self.r[treg] = (self.r[treg] - 1) & M16
            e.complete = False
            e.new_wait = 1
            if first:
                self.wprev, self.wevent = e.wpin, 0

    def _op_in(self, e):
        src, n = isa.bits(e.x, 18, 15), isa.bits(e.x, 4, 0) or 32
        if src == LINE and self.idx == 0:
            return self._in_line(e, n)
        b = self._src(e, src) & mask(n)
        if n == 32:
            nisr = b
        elif self.in_shr:
            nisr = ((self.isr >> n) | (b << (32 - n))) & M32
        else:
            nisr = ((self.isr << n) | b) & M32
        ncnt = min(32, self.isr_cnt + n)
        if self.autopush and ncnt >= e.push_th:
            if e.rx_full:
                e.complete = False
            else:
                e.fx["rxpush"] = nisr
                e.chip.cover("shift:autopush")
                self.isr, self.isr_cnt = 0, 0
        else:
            self.isr, self.isr_cnt = nisr, ncnt
        if e.complete and n == 1 and self.in_tap:
            self._crc_feed(b & 1)

    def _op_out(self, e):
        dst, n = isa.bits(e.x, 18, 15), isa.bits(e.x, 4, 0) or 32
        line = dst == LINE and self.idx == 0
        if line and not e.chip.line.room():
            e.complete = False                  # TX queue full: no side effects
            return
        osr0, cnt0 = self.osr, self.osr_cnt
        if self.autopull and self.osr_cnt >= e.pull_th:
            if not e.pullq:
                e.complete = False
                return
            osr0, cnt0 = e.pullq[0], 0
            e.fx["txpop"] = True
            e.chip.cover("shift:autopull")
        if self.out_shr:
            data = osr0 & mask(n)
            nosr = 0 if n == 32 else osr0 >> n
        else:
            data = osr0 >> (32 - n)
            nosr = 0 if n == 32 else (osr0 << n) & M32
        self.osr, self.osr_cnt = nosr, min(32, cnt0 + n)
        if n == 1 and self.out_tap:
            self._crc_feed(data & 1)
        if line:
            e.chip.line.push([(data >> i) & 1 for i in (range(n) if self.out_shr else range(n - 1, -1, -1))])
        elif dst != OSR:                # OUT to OSR only shifts (docs/isa.md §5)
            self._write_dst(e, dst, data, n)

    def _in_line(self, e, n):
        """IN LINE, n on core 0 (§4.7): n received bits, or fewer at a frame end (T = 1)."""
        got = e.chip.line.peek(n)
        if got is None:
            e.complete = False
            return
        bits, t = got
        k = len(bits)
        v = sum(b << i for i, b in enumerate(bits)) if self.in_shr else sum(b << (k - 1 - i) for i, b in enumerate(bits))
        if k == 32:
            nisr = v
        elif k == 0:
            nisr = self.isr
        elif self.in_shr:
            nisr = ((self.isr >> k) | (v << (32 - k))) & M32
        else:
            nisr = ((self.isr << k) | v) & M32
        ncnt = min(32, self.isr_cnt + k)
        if self.autopush and ncnt >= e.push_th:
            if e.rx_full:
                e.complete = False
                return
            e.fx["rxpush"] = nisr
            self.isr, self.isr_cnt = 0, 0
        else:
            self.isr, self.isr_cnt = nisr, ncnt
        e.chip.line.consume(k, t)
        self.t = t
        if n == 1 and k == 1 and self.in_tap:
            self._crc_feed(bits[0])

    def _op_push(self, e):
        iff, blk = isa.bits(e.x, 18, 18), isa.bits(e.x, 17, 17)
        if iff and self.isr_cnt < e.push_th:
            return
        if e.rx_full:
            if blk:
                e.complete = False
            else:
                self.isr, self.isr_cnt = 0, 0
        else:
            e.fx["rxpush"] = self.isr
            self.isr, self.isr_cnt = 0, 0

    def _op_pull(self, e):
        ife, blk = isa.bits(e.x, 18, 18), isa.bits(e.x, 17, 17)
        if ife and self.osr_cnt < e.pull_th:
            return
        if not e.pullq:
            if blk:
                e.complete = False
            else:
                self.osr, self.osr_cnt = self.r[0], 0
        else:
            self.osr, self.osr_cnt = e.pullq[0], 0
            e.fx["txpop"] = True

    def _op_mov(self, e):
        dst, mop, src = isa.bits(e.x, 18, 15), isa.bits(e.x, 14, 13), isa.bits(e.x, 12, 9)
        v = self._src(e, src)
        if mop == isa.M_INV:
            v = ~v & M32
        elif mop == isa.M_REV:
            v = rev32(v)
        self._write_dst(e, dst, v, 0)

    def _op_set(self, e):
        tgt = e.fx["oe"] if isa.bits(e.x, 18, 18) else e.fx["out"]
        v = isa.bits(e.x, 4, 0)
        for i in range(min(self.set_count, 5)):
            tgt[(self.set_base + i) & 31] = (v >> i) & 1

    def _op_ldi(self, e):
        self.r[isa.bits(e.x, 17, 16)] = isa.bits(e.x, 15, 0)

    def _op_alu(self, e):
        x = e.x
        aop, rd, imm = isa.bits(x, 18, 16), isa.bits(x, 15, 14), isa.bits(x, 13, 13)
        s = isa.bits(x, 12, 0) if imm else self.r[isa.bits(x, 1, 0)]
        a = self.r[rd]
        c = 0
        if aop == isa.A_ADD:
            res = a + s
            c = res >> 16
        elif aop in (isa.A_SUB, isa.A_CMP):
            res = a - s
            c = int(a < s)
        elif aop == isa.A_AND:
            res = a & s
        elif aop == isa.A_OR:
            res = a | s
        elif aop == isa.A_XOR:
            res = a ^ s
        elif aop == isa.A_SHL:
            res = a << (s & 15)
        else:
            res = a >> (s & 15)
        res &= M16
        self.z, self.c = int(res == 0), c
        if aop != isa.A_CMP:
            self.r[rd] = res

    def _op_pin(self, e):
        act, p = isa.bits(e.x, 18, 17), isa.bits(e.x, 4, 0)
        fx = e.fx
        if act == isa.P_LO:
            fx["out"][p], fx["oe"][p] = 0, 1
        elif act == isa.P_HI:
            fx["out"][p], fx["oe"][p] = 1, 1
        elif act == isa.P_TOGGLE:
            fx["out"][p] = ((e.chip.pin_out >> p) & 1) ^ 1
        else:
            fx["oe"][p] = 0

    def _op_flag(self, e):
        b = 1 << isa.bits(e.x, 2, 0)
        e.fx["fclr" if isa.bits(e.x, 18, 18) else "fset"] |= b

    def _op_halt(self, e):
        self.halted = 1
        e.complete = False


_EDGE_WAITS = (isa.W_RISE, isa.W_FALL, isa.W_EDGE)
_OPS = {isa.OP_JMP: Core._op_jmp, isa.OP_WAIT: Core._op_wait, isa.OP_IN: Core._op_in,
        isa.OP_OUT: Core._op_out, isa.OP_PUSH: Core._op_push, isa.OP_PULL: Core._op_pull,
        isa.OP_MOV: Core._op_mov, isa.OP_SET: Core._op_set, isa.OP_LDI: Core._op_ldi,
        isa.OP_ALU: Core._op_alu, isa.OP_PIN: Core._op_pin, isa.OP_FLAG: Core._op_flag,
        isa.OP_HALT: Core._op_halt}


class _Exec:
    """The inputs and effects of one core cycle."""

    def __init__(self, core, chip, pins_in, flags, x):
        self.chip, self.pins_in, self.flags, self.x = chip, pins_in, flags, x
        self.fx = dict(out={}, oe={}, side_out={}, side_oe={}, fset=0, fclr=0, txpop=False, rxpush=None)
        self.pull_th = core.pull_thresh or 32
        self.push_th = core.push_thresh or 32
        self.pullq = core.rxf if core.fjoin else core.txf
        self.rx_full = core.fjoin or len(core.rxf) >= chip.fifo_depth
        self.jump, self.complete, self.new_wait = None, True, 0

    def pin(self, p):
        return (self.pins_in >> (p & 31)) & 1


def _pad_bit(p):
    """Bit of logical pin p in the {ui[4:0], uio[7:0]} pad vector, or None if
    it isn't a pad input (docs/isa.md §2)."""
    return p if p < 8 else p - 8 if 16 <= p <= 20 else None


class Chip:
    def __init__(self, bank_words=256, ncores=NCORES, nbanks=4, fifo_depth=FIFO_DEPTH):
        """ncores/nbanks < 4 model a reduced build: missing cores never run and their
        registers read 0; missing banks read as 0 (a core fetching there executes 0,
        i.e. `jmp 0`, unless the bank-owner check faults it first)."""
        assert bank_words & (bank_words - 1) == 0 and bank_words <= 256
        self.bank_words = bank_words
        self.ncores, self.nbanks, self.fifo_depth = ncores, nbanks, fifo_depth
        self.imem = [0] * (4 * bank_words)
        self.cores = [Core(i) for i in range(NCORES)]
        self.enable = 0
        self.ctrl_pend = None  # CTRL write data from the previous cycle (acts one cycle late)
        self.flags = 0
        self.bank_owner = [0, 1, 2, 3]
        self.pin_owner = [0] * 32
        self.od_mask = 0
        self.pin_out = self.pin_oe = 0
        self.hostcfg = 0  # QUAD_EN: only the host port uses it
        self.line = Line(self.cover)
        self.sync1 = self.sync2 = 0  # {ui[4:0], uio[7:0]}
        # the line unit's RX pin, also sampled on the falling clock edge (pe_chip
        # rx_hq, rx_h2): half a clock older than its sync2 sample
        self.rx_hq = self.rx_h2 = 0
        self.pads_prev = 0           # last cycle's pads: the default mid-cycle value
        self.cycle = 0
        self.stats = {}

    def cover(self, event):
        """Count a coverage event (test_lockstep requires each goal to be hit)."""
        self.stats[event] = self.stats.get(event, 0) + 1

    def _imem_index(self, addr):
        return (addr >> 8) * self.bank_words + (addr & (self.bank_words - 1))

    def fetch(self, addr):
        if (addr >> 8) >= self.nbanks:
            return 0
        return self.imem[self._imem_index(addr)]

    # -- combinational views of current state
    def eff_pins(self):
        """(pin_out, pin_oe) as driven: the registers, with the line unit's
        transmit pins overriding them while it sends (§4.7)."""
        out, oe = self.pin_out, self.pin_oe
        for p, v in self.line.drive().items():
            out = (out & ~(1 << p)) | (v << p)
            oe |= 1 << p
        return out, oe

    def pins_in(self):
        s = self.sync2
        out, _ = self.eff_pins()
        return ((s & 0xFF) | (out & 0x7F00) | (((s >> 8) & 0x1F) << 16)
                | (out & 0xFF000000))

    def outputs(self):
        """(uo_out[6:0], uio_out, uio_oe)"""
        od = self.od_mask
        out, oe = self.eff_pins()
        uio_out = out & 0xFF & ~od
        uio_oe = ((oe & ~od) | (~out & od)) & 0xFF
        return (out >> 8) & 0x7F, uio_out, uio_oe

    def read(self, addr):
        """Bus read data (peek, no side effects)."""
        if CORE_BASE <= addr < CORE_BASE + NCORES * CORE_STRIDE:
            c, off = divmod(addr - CORE_BASE, CORE_STRIDE)
            return self.cores[c].read_reg(off) if c < self.ncores else 0
        return {
            A_ID: ID_VALUE,
            A_CTRL: self.enable,
            A_FSET: self.flags,
            A_FCLR: self.flags,
            A_PINS: self.pins_in(),
            A_POUT: self.pin_out,
            A_POE: self.pin_oe,
            A_LINESTAT: self.line.status(),
        }.get(addr, 0)      # configuration registers are write-only: read 0

    def step(self, ui_in=0, uio_in=0, we=False, addr=0, wdata=0, commit=False, snap=False, cancel=False,
             ui_mid=None, uio_mid=None):
        """One clock edge. ui_in/uio_in: the pads at this rising edge; ui_mid/
        uio_mid: at the falling edge before it (default: the previous cycle's
        values, i.e. the pads change just after a falling edge). `we`: bus write of wdata to addr this cycle. Reads
        (docs/isa.md §6.1): `snap` captures read(addr) for a word the host will
        send, reserving the next unread RXF entry at a FIFO address; `commit`
        says the oldest snapped word was sent (pops it if it was a real entry);
        `cancel` ends the transaction and drops the reservations."""
        pins_in = self.pins_in()
        line_write = None
        flags = self.flags
        fjoin = [core.fjoin for core in self.cores]  # start of cycle: a host write lands at the edge
        restart = fclear = 0
        ctrl = self.ctrl_pend
        if ctrl is not None:
            restart, fclear = (ctrl >> 8) & 15, (ctrl >> 16) & 15

        fset = fclr = 0
        new_out, new_oe = self.pin_out, self.pin_oe
        fifo_ops = []
        for c, core in enumerate(self.cores):
            if c >= self.ncores or (restart >> c) & 1 or not (self.enable >> c) & 1:
                fifo_ops.append((False, None))
                continue
            fx = core.step(self, pins_in, flags)
            fset |= fx["fset"]
            fclr |= fx["fclr"]
            fifo_ops.append((fx["txpop"], fx["rxpush"]))
            for kind in ("out", "oe"):
                w = dict(fx[kind])
                if w.keys() & fx["side_" + kind].keys():
                    self.cover("pins:side_set_over_out")
                w.update(fx["side_" + kind])  # side-set wins
                for p, v in w.items():
                    if self.pin_owner[p] != c:
                        self.cover("pins:write_to_unowned_pin")
                        continue
                    if kind == "out":
                        new_out = (new_out & ~(1 << p)) | (v << p)
                    else:
                        new_oe = (new_oe & ~(1 << p)) | (v << p)

        host_push = [None] * NCORES
        host_pop = [False] * NCORES
        in_cores = CORE_BASE <= addr < CORE_BASE + NCORES * CORE_STRIDE
        if in_cores and (addr - CORE_BASE) // CORE_STRIDE >= self.ncores:
            in_cores = False
            we = False if addr >= CORE_BASE else we
        if in_cores:
            c, off = divmod(addr - CORE_BASE, CORE_STRIDE)
        res_op = None                # this cycle's reservation change: (core, op, real)
        if in_cores and off == C_FIFO:
            core = self.cores[c]
            if commit and core.res:
                if core.res[0]:
                    host_pop[c] = True
                elif core.rxf:
                    self.cover("fifo:pop_suppressed_by_snapshot")
                res_op = (c, "commit", None)
            elif snap and len(core.res) < 2:
                n = sum(core.res)
                res_op = (c, "snap", not fjoin[c] and n < 2 and len(core.rxf) > n)
        if we:
            if addr < IMEM_WORDS:
                if (addr >> 8) < self.nbanks:
                    self.imem[self._imem_index(addr)] = wdata & M32
            elif in_cores:
                if off == C_FIFO:
                    host_push[c] = wdata & M32
                else:
                    self.cores[c].write_reg(off, wdata)
            elif addr == A_CTRL:
                pass  # registered: applied next cycle (ctrl_pend)
            elif addr == A_FSET:
                fset |= wdata & 0xFF
            elif addr == A_FCLR:
                fclr |= wdata & 0xFF
            elif addr == A_BANK:
                self.bank_owner = [(wdata >> (2 * b)) & 3 for b in range(4)]
            elif addr == A_POWN_LO:
                for i in range(16):
                    self.pin_owner[i] = (wdata >> (2 * i)) & 3
            elif addr == A_POWN_HI:
                for i in range(16):
                    self.pin_owner[16 + i] = (wdata >> (2 * i)) & 3
            elif addr == A_OD:
                self.od_mask = wdata & 0xFF
            elif addr == A_POUT:
                new_out = wdata & M32
            elif addr == A_POE:
                new_oe = wdata & M32
            elif addr == A_HOSTCFG:
                self.hostcfg = wdata & 1
            elif A_LINECFG <= addr <= A_LINEPINS:
                line_write = (addr - A_LINECFG, wdata)

        for c, core in enumerate(self.cores):
            if (fclear >> c) & 1:
                if core.txf or core.rxf:
                    self.cover("fifo:clear_nonempty")
                core.txf, core.rxf = [], []
                continue
            txpop, rxpush = fifo_ops[c]
            tx, rx = list(core.txf), list(core.rxf)
            if fjoin[c]:
                # TXF -> RXF -> OSR; the move uses start-of-cycle levels
                if txpop:
                    rx = rx[1:]
                if core.txf and len(core.rxf) < self.fifo_depth:
                    tx = tx[1:]
                    rx.append(core.txf[0])
                    self.cover("fifo:join_move")
            else:
                if txpop:
                    tx = tx[1:]
                if host_pop[c] and core.rxf:
                    rx = rx[1:]
                if rxpush is not None:
                    rx.append(rxpush)
            if host_push[c] is not None and len(core.txf) < self.fifo_depth:
                tx.append(host_push[c])
                if txpop and not fjoin[c]:
                    self.cover("fifo:host_push_and_core_pop")
            elif host_push[c] is not None:
                self.cover("fifo:host_push_to_full_dropped")
            core.txf, core.rxf = tx, rx

        for c in range(NCORES):
            if (restart >> c) & 1:
                if self.cores[c].in_wait or self.cores[c].dcnt:
                    self.cover("ctrl:restart_mid_instruction")
                self.cores[c].restart()

        if res_op:
            c, op, real = res_op
            if op == "commit":
                self.cores[c].res.pop(0)
            else:
                self.cores[c].res.append(real)
        for c, core in enumerate(self.cores):   # a cancel or FIFO clear drops them all
            if cancel or (fclear >> c) & 1:
                core.res = []
        if ctrl is not None:
            self.enable = ctrl & 15
        rxp = self.line.rxp
        rx_mid = self.rx_h2 if _pad_bit(rxp) is not None else (pins_in >> rxp) & 1
        self.line.step(pins_in, rx_mid)  # after the cores: they used its start-of-cycle state
        self.line.commit()
        if line_write:
            self.line.write(*line_write)
        self.ctrl_pend = wdata & M32 if (we and addr == A_CTRL) else None
        self.flags = ((flags & ~fclr) | fset) & 0xFF
        self.pin_out, self.pin_oe = new_out & M32, new_oe & M32
        self.sync2 = self.sync1
        pads = (uio_in & 0xFF) | ((ui_in & 0x1F) << 8)
        mid = self.pads_prev if ui_mid is None and uio_mid is None else \
            ((uio_in if uio_mid is None else uio_mid) & 0xFF) | (((ui_in if ui_mid is None else ui_mid) & 0x1F) << 8)
        bit = _pad_bit(rxp)              # the RX pin chosen at that falling edge
        self.rx_h2, self.rx_hq = self.rx_hq, (mid >> bit) & 1 if bit is not None else 0
        self.sync1 = self.pads_prev = pads
        self.cycle += 1
