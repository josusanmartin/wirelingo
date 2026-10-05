"""A small instruction-level model of one RP2040 PIO state machine, for the PIO
comparison (docs/pio-comparison.md). Not a hardware model.

It executes the 16-bit words that the official pioasm produced for the
pico-examples programs (test/model/pio_reference.json). Semantics follow the
RP2040 datasheet (build 2025-02-20): 3.2.4 stalling, 3.4 instruction set,
3.5.1 side-set, 3.5.4 autopush/autopull (including the OUT-cycle refill rule),
3.5.7 OUT EXEC. One step() is one state machine clock (after the clock
divider). Not modelled: the 2-flop GPIO input synchroniser (two system clocks,
well under one SM clock at the dividers used here), MOV/PULL/PUSH variants and
JMP conditions the compared programs don't use (they raise), the second PIO
block and DMA.

Pins are modelled as open-drain-style "release" bits where the program drives
pindirs (I2C with the OE override inverted, as pio_i2c's init does), and as
levels otherwise; the caller resolves the bus and passes input levels in.
"""
import json
import pathlib

FIXTURE = pathlib.Path(__file__).with_name("pio_reference.json")


def program(name):
    return json.loads(FIXTURE.read_text())["programs"][name]


class Stuck(Exception):
    pass


class PioSm:
    """One state machine with a private 32-slot memory holding one program."""

    def __init__(self, prog, *, entry=None, in_base=0, jmp_pin=0, out_base=0, set_base=0,
                 side_base=0, out_right=False, in_right=False, autopull=False, pull_thresh=32,
                 autopush=False, push_thresh=32, fifo_depth=4, sideset_pindirs=None):
        self.words = prog["words"]
        self.wrap_top, self.wrap_bottom = prog["wrap"], prog["wrap_target"]
        ss = prog["sideset"] or {"size": 0, "optional": False, "pindirs": False}
        self.ss_size, self.ss_opt = ss["size"], ss["optional"]
        self.ss_pindirs = ss["pindirs"] if sideset_pindirs is None else sideset_pindirs
        self.pc = self.wrap_bottom if entry is None else entry
        self.in_base, self.jmp_pin, self.out_base = in_base, jmp_pin, out_base
        self.set_base, self.side_base = set_base, side_base
        self.out_right, self.in_right = out_right, in_right
        self.autopull, self.pull_thresh = autopull, pull_thresh
        self.autopush, self.push_thresh = autopush, push_thresh
        self.depth = fifo_depth
        self.x = self.y = self.isr = self.osr = 0
        self.isr_cnt, self.osr_cnt = 0, 32        # 3.5.4: after restart
        self.txf, self.rxf = [], []
        self.delay = 0
        self.exec_latch = None                    # OUT EXEC: run on the next cycle
        self.exec_latch_next = None
        self.pins = 0                             # output levels
        self.pindirs = 0                          # 1 = output enabled (before any inversion)
        self.irq = 0                              # the 8 PIO IRQ flags (shared, one SM here)
        self.irq_waiting = None
        self.txstall = False                      # FDEBUG.TXSTALL for this SM (3.7)
        self.stalled = False
        self.cycles = 0

    # -- system side
    def put(self, word):
        if len(self.txf) >= self.depth:
            raise Stuck("TX FIFO full")
        self.txf.append(word & 0xFFFFFFFF)

    def get(self):
        return self.rxf.pop(0)

    # -- helpers
    def _bits(self, word):
        """(side value or None, delay) from bits 12:8 (3.5.1)."""
        f = (word >> 8) & 31
        n = self.ss_size
        delay = f & ((1 << (5 - n)) - 1)
        if n == 0:
            return None, delay
        side = f >> (5 - n)
        if self.ss_opt:
            en = side >> (n - 1)
            side &= (1 << (n - 1)) - 1
            return (side if en else None), delay
        return side, delay

    def _write_pins(self, base, count, value, dirs):
        for i in range(count):
            b = 1 << ((base + i) % 32)
            v = (value >> i) & 1
            if dirs:
                self.pindirs = (self.pindirs & ~b) | (b if v else 0)
            else:
                self.pins = (self.pins & ~b) | (b if v else 0)

    def _autopull_background(self):
        if self.autopull and self.osr_cnt >= self.pull_thresh and self.txf:
            self.osr, self.osr_cnt = self.txf.pop(0), 0

    def _shift_out(self, n):
        n = n or 32
        if self.out_right:
            data = self.osr & ((1 << n) - 1) if n < 32 else self.osr
            self.osr = (self.osr >> n) if n < 32 else 0
        else:
            data = (self.osr >> (32 - n)) & ((1 << n) - 1)
            self.osr = (self.osr << n) & 0xFFFFFFFF if n < 32 else 0
        self.osr_cnt = min(32, self.osr_cnt + n)
        return data

    def _shift_in(self, value, n):
        n = n or 32
        value &= (1 << n) - 1 if n < 32 else 0xFFFFFFFF
        if self.in_right:
            self.isr = ((self.isr >> n) | (value << (32 - n))) & 0xFFFFFFFF if n < 32 else value
        else:
            self.isr = ((self.isr << n) | value) & 0xFFFFFFFF if n < 32 else value
        self.isr_cnt = min(32, self.isr_cnt + n)

    def step(self, pins_in):
        """One SM clock. pins_in: GPIO input levels (bit i = GPIO i)."""
        self.cycles += 1
        if self.delay:
            self.delay -= 1
            self._autopull_background()
            return
        if self.exec_latch is not None:
            word, from_exec = self.exec_latch, True
        else:
            word, from_exec = self.words[self.pc], False
        side, delay = self._bits(word)
        if side is not None:                      # side-set happens even on a stall (3.2.4)
            self._write_pins(self.side_base, self.ss_size - (1 if self.ss_opt else 0), side,
                             self.ss_pindirs)
        op = word >> 13
        a, b, cnt = (word >> 5) & 7, (word >> 5) & 7, word & 31
        nxt = self.wrap_bottom if self.pc == self.wrap_top else (self.pc + 1) % 32
        if from_exec:
            nxt = self.pc                         # 3.5.7: an EXEC'd instruction doesn't advance PC
        stall = False
        pin = lambda i: (pins_in >> (i % 32)) & 1
        if op == 0:                               # JMP
            take = {0: True, 1: self.x == 0, 2: self.x != 0, 3: self.y == 0, 4: self.y != 0,
                    5: self.x != self.y, 6: pin(self.jmp_pin) == 1, 7: self.osr_cnt < self.pull_thresh}[a]
            if a == 2:
                self.x = (self.x - 1) & 0xFFFFFFFF
            if a == 4:
                self.y = (self.y - 1) & 0xFFFFFFFF
            if take:
                nxt = cnt
        elif op == 1:                             # WAIT
            pol, src, idx = (word >> 7) & 1, (word >> 5) & 3, word & 31
            if src == 0:
                ok = pin(idx) == pol
            elif src == 1:
                ok = pin(self.in_base + idx) == pol
            elif src == 2:
                k = idx & 7
                ok = ((self.irq >> k) & 1) == pol
                if ok and pol:
                    self.irq &= ~(1 << k)
            else:
                raise NotImplementedError("WAIT source 3")
            stall = not ok
        elif op == 2:                             # IN
            if self.autopush and self.isr_cnt >= self.push_thresh and len(self.rxf) >= self.depth:
                stall = True
            else:
                src = {0: (pins_in >> self.in_base) | (pins_in << (32 - self.in_base)),
                       1: self.x, 2: self.y, 3: 0, 6: self.isr, 7: self.osr}[b]
                self._shift_in(src & 0xFFFFFFFF, cnt)
                if self.autopush and self.isr_cnt >= self.push_thresh:
                    if len(self.rxf) >= self.depth:
                        stall = True               # (the pseudocode stalls after shifting)
                    else:
                        self.rxf.append(self.isr)
                        self.isr, self.isr_cnt = 0, 0
        elif op == 3:                             # OUT
            if self.autopull and self.osr_cnt >= self.pull_thresh:
                if self.txf:
                    self.osr, self.osr_cnt = self.txf.pop(0), 0
                stall = True
                self.txstall |= not self.txf and self.osr_cnt >= self.pull_thresh
            else:
                data = self._shift_out(cnt)
                n = cnt or 32
                if b == 0:
                    self._write_pins(self.out_base, n, data, False)
                elif b == 1:
                    self.x = data
                elif b == 2:
                    self.y = data
                elif b == 3:
                    pass
                elif b == 4:
                    self._write_pins(self.out_base, n, data, True)
                elif b == 5:
                    nxt = data & 31
                elif b == 6:
                    self.isr, self.isr_cnt = data, n
                elif b == 7:
                    self.exec_latch_next = data & 0xFFFF
                self._autopull_background()
        elif op == 4:                             # PUSH / PULL
            if (word >> 7) & 1:                   # PULL
                ifempty, block = (word >> 6) & 1, (word >> 5) & 1
                if ifempty and self.osr_cnt < self.pull_thresh:
                    pass
                elif self.txf:
                    self.osr, self.osr_cnt = self.txf.pop(0), 0
                elif block:
                    stall = True
                    self.txstall = True
                else:
                    self.osr, self.osr_cnt = self.x, 0
            else:                                 # PUSH
                iffull, block = (word >> 6) & 1, (word >> 5) & 1
                if iffull and self.isr_cnt < self.push_thresh:
                    pass
                elif len(self.rxf) < self.depth:
                    self.rxf.append(self.isr)
                    self.isr, self.isr_cnt = 0, 0
                elif block:
                    stall = True
                else:
                    self.isr, self.isr_cnt = 0, 0
        elif op == 5:                             # MOV
            dst, mop, src = (word >> 5) & 7, (word >> 3) & 3, word & 7
            v = {0: pins_in >> self.in_base, 1: self.x, 2: self.y, 3: 0, 6: self.isr, 7: self.osr}[src]
            v &= 0xFFFFFFFF
            if mop == 1:
                v ^= 0xFFFFFFFF
            elif mop == 2:
                v = int(f"{v:032b}"[::-1], 2)
            if dst == 1:
                self.x = v
            elif dst == 2:
                self.y = v
            elif dst == 6:
                self.isr, self.isr_cnt = v, 0
            elif dst == 7:
                self.osr, self.osr_cnt = v, 0
            elif dst == 5:
                nxt = v & 31
            else:
                raise NotImplementedError(f"MOV dst {dst}")
        elif op == 6:                             # IRQ
            clr, wait, idx = (word >> 6) & 1, (word >> 5) & 1, word & 31
            k = idx & 7                           # rel with SM 0 adds 0
            if clr:
                self.irq &= ~(1 << k)
            elif self.irq_waiting is None:
                self.irq |= 1 << k
                if wait:
                    self.irq_waiting = k
                    stall = True
            else:                                 # already raised: wait for the clear
                stall = (self.irq >> k) & 1 == 1
                if not stall:
                    self.irq_waiting = None
        elif op == 7:                             # SET
            dst, v = (word >> 5) & 7, word & 31
            if dst == 0:
                self._write_pins(self.set_base, 1, v, False)
            elif dst == 1:
                self.x = v
            elif dst == 2:
                self.y = v
            elif dst == 4:
                self._write_pins(self.set_base, 1, v, True)
            else:
                raise NotImplementedError(f"SET dst {dst}")
        self.stalled = stall
        if stall:
            if not (op == 3):
                self._autopull_background()
            return
        if from_exec:
            self.exec_latch = None
        if self.exec_latch_next is not None:
            self.exec_latch, self.exec_latch_next = self.exec_latch_next, None
        self.pc = nxt
        self.delay = delay
        if op != 3:
            self._autopull_background()
