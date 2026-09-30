"""Cycle-accurate golden model of pe_chip (everything except the SPI slave).

Implements docs/isa.md. One call to Chip.step() == one rising clock edge.
Everything reads start-of-cycle state; effects combine into the next state,
mirroring the synchronous RTL.
"""
from . import isa
from .isa import (PINS, PINDIRS, ISR, OSR, STATUS, FLAGS, PC)

M32 = 0xFFFFFFFF
M16 = 0xFFFF
NCORES = 4
FIFO_DEPTH = 4
ID_VALUE = 0x50450002
IMEM_WORDS = 1024

# Register map
A_ID, A_CTRL, A_FSET, A_FCLR, A_BANK, A_POWN_LO, A_POWN_HI, A_OD, A_PINS, A_POUT, A_POE = range(0x800, 0x80B)
CORE_BASE, CORE_STRIDE = 0x400, 0x20
C_CLKDIV, C_PINCTRL0, C_PINCTRL1, C_SHIFTCTRL, C_ENTRY, C_WRAP = 0, 1, 2, 3, 4, 5
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
        self.entry, self.wrap_bottom, self.wrap_top = 256 * idx, 0, 1023
        self.r = [0, 0, 0, 0]
        self.txf, self.rxf = [], []
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
        if off == C_CLKDIV:
            return (self.div_int << 8) | self.div_frac
        if off == C_PINCTRL0:
            return self.out_base | (self.out_count << 8) | (self.set_base << 16) | (self.set_count << 24)
        if off == C_PINCTRL1:
            return (self.side_base | (self.side_count << 8) | (self.side_opt << 12)
                    | (self.side_pindir << 13) | (self.in_base << 16))
        if off == C_SHIFTCTRL:
            return (self.out_shr | (self.in_shr << 1) | (self.autopull << 2) | (self.autopush << 3)
                    | (self.pull_thresh << 8) | (self.push_thresh << 16))
        if off == C_ENTRY:
            return self.entry
        if off == C_WRAP:
            return self.wrap_bottom | (self.wrap_top << 16)
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
        if off == C_FIFO:
            return self.rxf[0] if self.rxf else 0
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
            self.pull_thresh, self.push_thresh = (v >> 8) & 31, (v >> 16) & 31
        elif off == C_ENTRY:
            self.entry = v & 1023
        elif off == C_WRAP:
            self.wrap_bottom, self.wrap_top = v & 1023, (v >> 16) & 1023
        elif off == C_PC:
            self.pc = v & 1023
            self.dcnt = 0
            self.in_wait = 0
        elif C_R0 <= off < C_R0 + 4:
            self.r[off - C_R0] = v & M16

    def step(self, chip, pins_in, flags):
        """Advance one enabled cycle. Mutates own non-FIFO state; returns effects."""
        fx = dict(out={}, oe={}, side_out={}, side_oe={}, fset=0, fclr=0,
                  txpop=False, rxpush=None)
        instr = chip.fetch(self.pc)
        op, sd, x = isa.fields(instr)
        pin_of = lambda p: (pins_in >> (p & 31)) & 1
        pull_th = self.pull_thresh or 32
        push_th = self.push_thresh or 32

        # -- divider
        tick = self.cnt == 0
        if tick:
            s = self.facc + self.div_frac
            self.facc = s & 0xFF
            self.cnt = (self.div_int - 1 + (s >> 8)) & M16
        else:
            self.cnt = (self.cnt - 1) & M16

        # -- WAIT edge tracker (runs every enabled cycle while a WAIT is in progress)
        kind = isa.bits(x, 18, 16)
        wpin = pin_of(isa.bits(x, 15, 11))
        qen, qpol, qpin = isa.bits(x, 6, 6), isa.bits(x, 5, 5), isa.bits(x, 4, 0)
        qual = (not qen) or pin_of(qpin) == qpol
        if kind == isa.W_RISE:
            trans = (not self.wprev) and wpin
        elif kind == isa.W_FALL:
            trans = self.wprev and not wpin
        else:
            trans = self.wprev != wpin
        ev = bool(trans and qual)
        edge_done = bool(self.wevent or ev)
        if self.in_wait:
            self.wevent = int(edge_done)
            self.wprev = wpin

        if not tick or self.halted or self.fault:
            return fx
        if self.dcnt:
            self.dcnt -= 1
            return fx
        if chip.bank_owner[self.pc >> 8] != self.idx:
            self.fault = self.halted = 1
            self.stalled = 0
            return fx

        # -- side-set / delay split
        sc = min(self.side_count, 4)
        dbits = 8 - sc - self.side_opt
        delay = sd & mask(dbits)
        side_en = ((sd >> 7) & 1) if self.side_opt else 1
        if sc and side_en:
            sv = (sd >> dbits) & mask(sc)
            tgt = fx["side_oe"] if self.side_pindir else fx["side_out"]
            for i in range(sc):
                tgt[(self.side_base + i) & 31] = (sv >> i) & 1

        out_count = min(self.out_count, 32)

        def write_pins(v, oe):
            tgt = fx["oe"] if oe else fx["out"]
            for i in range(out_count):
                tgt[(self.out_base + i) & 31] = (v >> i) & 1

        def src_val(code):
            if code < 4:
                return self.r[code]
            return {PINS: rotr32(pins_in, self.in_base), ISR: self.isr, OSR: self.osr,
                    STATUS: self.status() & 0x7FF, FLAGS: flags, PC: self.pc}.get(code, 0)

        jump = None
        complete = True
        new_wait = 0
        chip.stats[f"attempt:{isa.OPNAMES.get(op, 'rsvd')}"] = chip.stats.get(f"attempt:{isa.OPNAMES.get(op, 'rsvd')}", 0) + 1

        if op == isa.OP_JMP:
            cond, sel, addr = isa.bits(x, 18, 15), isa.bits(x, 14, 10), isa.bits(x, 9, 0)
            rs = sel & 3
            rv = self.r[rs]
            ok = [
                True, rv == 0, rv != 0, rv != 0,
                pin_of(sel) == 1, pin_of(sel) == 0,
                self.z == 1, self.z == 0, self.c == 1, self.c == 0, self.t == 1,
                self.osr_cnt < pull_th, len(self.txf) > 0, len(self.rxf) >= FIFO_DEPTH,
                (flags >> (sel & 7)) & 1 == 1, (flags >> (sel & 7)) & 1 == 0,
            ][cond]
            if cond == isa.C_DEC:
                self.r[rs] = (rv - 1) & M16
            if ok:
                jump = addr

        elif op == isa.OP_WAIT:
            idx = isa.bits(x, 15, 11)
            to, treg, clr = isa.bits(x, 10, 10), isa.bits(x, 9, 8), isa.bits(x, 7, 7)
            first = not self.in_wait
            f = (flags >> (idx & 7)) & 1
            if kind == isa.W_PIN1:
                done = wpin == 1 and qual
            elif kind == isa.W_PIN0:
                done = wpin == 0 and qual
            elif kind in (isa.W_RISE, isa.W_FALL, isa.W_EDGE):
                done = (not first) and edge_done
            elif kind == isa.W_FLAG1:
                done = f == 1 and qual
            elif kind == isa.W_FLAG0:
                done = f == 0 and qual
            else:
                done = True
            if done:
                if to:
                    self.t = 0
                if kind == isa.W_FLAG1 and clr:
                    fx["fclr"] |= 1 << (idx & 7)
            elif to and self.r[treg] == 0:
                self.t = 1
            else:
                if to:
                    self.r[treg] = (self.r[treg] - 1) & M16
                complete = False
                new_wait = 1
                if first:
                    self.wprev, self.wevent = wpin, 0

        elif op == isa.OP_IN:
            src, n = isa.bits(x, 18, 15), isa.bits(x, 4, 0) or 32
            b = src_val(src) & mask(n)
            if n == 32:
                nisr = b
            elif self.in_shr:
                nisr = ((self.isr >> n) | (b << (32 - n))) & M32
            else:
                nisr = ((self.isr << n) | b) & M32
            ncnt = min(32, self.isr_cnt + n)
            if self.autopush and ncnt >= push_th:
                if len(self.rxf) >= FIFO_DEPTH:
                    complete = False
                else:
                    fx["rxpush"] = nisr
                    self.isr, self.isr_cnt = 0, 0
            else:
                self.isr, self.isr_cnt = nisr, ncnt

        elif op == isa.OP_OUT:
            dst, n = isa.bits(x, 18, 15), isa.bits(x, 4, 0) or 32
            osr0, cnt0 = self.osr, self.osr_cnt
            if self.autopull and self.osr_cnt >= pull_th:
                if not self.txf:
                    complete = False
                else:
                    osr0, cnt0 = self.txf[0], 0
                    fx["txpop"] = True
            if complete:
                if self.out_shr:
                    data = osr0 & mask(n)
                    nosr = 0 if n == 32 else osr0 >> n
                else:
                    data = osr0 >> (32 - n)
                    nosr = 0 if n == 32 else (osr0 << n) & M32
                self.osr, self.osr_cnt = nosr, min(32, cnt0 + n)
                if dst < 4:
                    self.r[dst] = data & M16
                elif dst == PINS:
                    write_pins(data, False)
                elif dst == PINDIRS:
                    write_pins(data, True)
                elif dst == ISR:
                    self.isr, self.isr_cnt = data, n
                elif dst == PC:
                    jump = data & 1023

        elif op == isa.OP_PUSH:
            iff, blk = isa.bits(x, 18, 18), isa.bits(x, 17, 17)
            if iff and self.isr_cnt < push_th:
                pass
            elif len(self.rxf) >= FIFO_DEPTH:
                if blk:
                    complete = False
                else:
                    self.isr, self.isr_cnt = 0, 0
            else:
                fx["rxpush"] = self.isr
                self.isr, self.isr_cnt = 0, 0

        elif op == isa.OP_PULL:
            ife, blk = isa.bits(x, 18, 18), isa.bits(x, 17, 17)
            if ife and self.osr_cnt < pull_th:
                pass
            elif not self.txf:
                if blk:
                    complete = False
                else:
                    self.osr, self.osr_cnt = self.r[0], 0
            else:
                self.osr, self.osr_cnt = self.txf[0], 0
                fx["txpop"] = True

        elif op == isa.OP_MOV:
            dst, mop, src = isa.bits(x, 18, 15), isa.bits(x, 14, 13), isa.bits(x, 12, 9)
            v = src_val(src)
            if mop == isa.M_INV:
                v = ~v & M32
            elif mop == isa.M_REV:
                v = rev32(v)
            if dst < 4:
                self.r[dst] = v & M16
            elif dst == PINS:
                write_pins(v, False)
            elif dst == PINDIRS:
                write_pins(v, True)
            elif dst == ISR:
                self.isr, self.isr_cnt = v, 0
            elif dst == OSR:
                self.osr, self.osr_cnt = v, 0
            elif dst == PC:
                jump = v & 1023

        elif op == isa.OP_SET:
            tgt = fx["oe"] if isa.bits(x, 18, 18) else fx["out"]
            v = isa.bits(x, 4, 0)
            for i in range(min(self.set_count, 5)):
                tgt[(self.set_base + i) & 31] = (v >> i) & 1

        elif op == isa.OP_LDI:
            self.r[isa.bits(x, 17, 16)] = isa.bits(x, 15, 0)

        elif op == isa.OP_ALU:
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

        elif op == isa.OP_PIN:
            act, p = isa.bits(x, 18, 17), isa.bits(x, 4, 0)
            if act == isa.P_LO:
                fx["out"][p], fx["oe"][p] = 0, 1
            elif act == isa.P_HI:
                fx["out"][p], fx["oe"][p] = 1, 1
            elif act == isa.P_TOGGLE:
                fx["out"][p] = ((chip.pin_out >> p) & 1) ^ 1
            else:
                fx["oe"][p] = 0

        elif op == isa.OP_FLAG:
            b = 1 << isa.bits(x, 2, 0)
            if isa.bits(x, 18, 18):
                fx["fclr"] |= b
            else:
                fx["fset"] |= b

        elif op == isa.OP_HALT:
            self.halted = 1
            complete = False

        # reserved opcodes complete as NOP

        key = ("done:" if complete else "stall:") + isa.OPNAMES.get(op, "rsvd")
        chip.stats[key] = chip.stats.get(key, 0) + 1
        if jump is not None:
            chip.stats["jump"] = chip.stats.get("jump", 0) + 1
        self.stalled = int(not complete and op != isa.OP_HALT)
        self.in_wait = new_wait
        if complete:
            self.dcnt = delay
            if jump is not None:
                self.pc = jump
            elif self.pc == self.wrap_top:
                self.pc = self.wrap_bottom
            else:
                self.pc = (self.pc + 1) & 1023
        return fx


class Chip:
    def __init__(self, bank_words=256):
        assert bank_words & (bank_words - 1) == 0 and bank_words <= 256
        self.bank_words = bank_words
        self.imem = [0] * (4 * bank_words)
        self.cores = [Core(i) for i in range(NCORES)]
        self.enable = 0
        self.flags = 0
        self.bank_owner = [0, 1, 2, 3]
        self.pin_owner = [0] * 32
        self.od_mask = 0
        self.pin_out = self.pin_oe = 0
        self.sync1 = self.sync2 = 0  # {ui[4:0], uio[7:0]}
        self.cycle = 0
        self.stats = {}

    def _imem_index(self, addr):
        return (addr >> 8) * self.bank_words + (addr & (self.bank_words - 1))

    def fetch(self, addr):
        return self.imem[self._imem_index(addr)]

    # -- combinational views of current state
    def pins_in(self):
        s = self.sync2
        return ((s & 0xFF) | (self.pin_out & 0x7F00) | (((s >> 8) & 0x1F) << 16)
                | (self.pin_out & 0xFF000000))

    def outputs(self):
        """(uo_out[6:0], uio_out, uio_oe)"""
        od = self.od_mask
        uio_out = self.pin_out & 0xFF & ~od
        uio_oe = ((self.pin_oe & ~od) | (~self.pin_out & od)) & 0xFF
        return (self.pin_out >> 8) & 0x7F, uio_out, uio_oe

    def read(self, addr):
        """Bus read data (peek, no side effects)."""
        if CORE_BASE <= addr < CORE_BASE + NCORES * CORE_STRIDE:
            c, off = divmod(addr - CORE_BASE, CORE_STRIDE)
            return self.cores[c].read_reg(off)
        return {
            A_ID: ID_VALUE,
            A_CTRL: self.enable,
            A_FSET: self.flags,
            A_FCLR: self.flags,
            A_BANK: sum(o << (2 * b) for b, o in enumerate(self.bank_owner)),
            A_POWN_LO: sum(self.pin_owner[i] << (2 * i) for i in range(16)),
            A_POWN_HI: sum(self.pin_owner[16 + i] << (2 * i) for i in range(16)),
            A_OD: self.od_mask,
            A_PINS: self.pins_in(),
            A_POUT: self.pin_out,
            A_POE: self.pin_oe,
        }.get(addr, 0)

    def step(self, ui_in=0, uio_in=0, we=False, addr=0, wdata=0, commit=False):
        """One clock edge. `we`: bus write of wdata to addr this cycle.
        `commit`: read side effect (FIFO pop) for addr this cycle."""
        pins_in = self.pins_in()
        flags = self.flags
        restart = fclear = 0
        if we and addr == A_CTRL:
            restart, fclear = (wdata >> 8) & 15, (wdata >> 16) & 15

        fset = fclr = 0
        new_out, new_oe = self.pin_out, self.pin_oe
        fifo_ops = []
        for c, core in enumerate(self.cores):
            if (restart >> c) & 1 or not (self.enable >> c) & 1:
                fifo_ops.append((False, None))
                continue
            fx = core.step(self, pins_in, flags)
            fset |= fx["fset"]
            fclr |= fx["fclr"]
            fifo_ops.append((fx["txpop"], fx["rxpush"]))
            for kind in ("out", "oe"):
                w = dict(fx[kind])
                w.update(fx["side_" + kind])  # side-set wins
                for p, v in w.items():
                    if self.pin_owner[p] != c:
                        continue
                    if kind == "out":
                        new_out = (new_out & ~(1 << p)) | (v << p)
                    else:
                        new_oe = (new_oe & ~(1 << p)) | (v << p)

        host_push = [None] * NCORES
        host_pop = [False] * NCORES
        in_cores = CORE_BASE <= addr < CORE_BASE + NCORES * CORE_STRIDE
        if in_cores:
            c, off = divmod(addr - CORE_BASE, CORE_STRIDE)
            if commit and off == C_FIFO:
                host_pop[c] = True
        if we:
            if addr < IMEM_WORDS:
                self.imem[self._imem_index(addr)] = wdata & M32
            elif in_cores:
                if off == C_FIFO:
                    host_push[c] = wdata & M32
                else:
                    self.cores[c].write_reg(off, wdata)
            elif addr == A_CTRL:
                self.enable = wdata & 15
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

        for c, core in enumerate(self.cores):
            if (fclear >> c) & 1:
                core.txf, core.rxf = [], []
                continue
            txpop, rxpush = fifo_ops[c]
            tx, rx = list(core.txf), list(core.rxf)
            if txpop:
                tx = tx[1:]
            if host_push[c] is not None and len(core.txf) < FIFO_DEPTH:
                tx.append(host_push[c])
            if host_pop[c] and core.rxf:
                rx = rx[1:]
            if rxpush is not None:
                rx.append(rxpush)
            core.txf, core.rxf = tx, rx

        for c in range(NCORES):
            if (restart >> c) & 1:
                self.cores[c].restart()

        self.flags = ((flags & ~fclr) | fset) & 0xFF
        self.pin_out, self.pin_oe = new_out & M32, new_oe & M32
        self.sync2 = self.sync1
        self.sync1 = (uio_in & 0xFF) | ((ui_in & 0x1F) << 8)
        self.cycle += 1
