"""Measurements behind docs/pio-comparison.md: protoemu firmware on the
cycle-accurate model against the pico-examples PIO programs on a small PIO
state-machine model (rp2040_pio.py), driven by the same I2C target and bus
monitor, the same UART waveforms and the same SPI loopback.

The PIO words come from the official pioasm (pio_reference.json records the
assembler, source URLs at pinned commits and their sha256). The PIO host side
re-implements pico-examples' pio_i2c.c line for line as Python against the
model, with the CPU treated as infinitely fast between SM clocks (favourable
to PIO). A CPU busy-wait that makes no progress within a budget is reported as
a hang.

    PYTHONPATH=tools python -m pytest -q test/model/test_pio_comparison.py
    PYTHONPATH=tools python test/model/test_pio_comparison.py   # prints the tables
"""
import pathlib

import pytest

from pe import asm, host, sim
from pe.modelhost import ModelHost
from rp2040_pio import PioSm, program

FW = pathlib.Path(__file__).resolve().parents[2] / "firmware"
F_PE = 50e6                       # protoemu tapeout clock
SDA, SCL = 0, 1
F_SCL = 100e3
PE_I2C_DIV = host.clkdiv_for(F_PE, 8 * F_SCL)      # 62.5 clocks per tick, 8 ticks per SCL
PIO_I2C_TICK = 32 * F_SCL                          # pio_i2c: clkdiv = clk_sys / (32 * 100 kHz)
TMO = 2000                                         # i2c_master.pasm's timeout, ticks


def pe_prog(name, **defs):
    return asm.assemble((FW / f"{name}.pasm").read_text(), defs)


# ---------------------------------------------------------------- program sizes

PE_SIZES = {  # firmware -> (instructions, defines)
    "uart_tx": {}, "uart_rx": {"RX": 16}, "spi_master": {}, "i2c_master": {"SDA": 0, "SCL": 1},
}
PIO_SIZES = {"uart_tx": 4, "uart_rx": 9, "uart_rx_mini": 4, "spi_cpha0": 2, "spi_cpha0_cs": 9,
             "i2c": 18, "set_scl_sda": 4, "usb_tx_dpdm": 5, "usb_edge_detector": 17,
             "usb_nrzi_decoder": 10}


def test_program_sizes():
    got = {n: len(pe_prog(n, **d).words) for n, d in PE_SIZES.items()}
    assert got == {"uart_tx": 4, "uart_rx": 14, "spi_master": 2, "i2c_master": 90}
    # what the I2C timeouts cost: `ldi r3, TMO` + `jmp t` around each of the six
    # SCL-release WAITs, plus the stretch_timeout handler at the end
    i2c = pe_prog("i2c_master", SDA=0, SCL=1)
    from pe import isa
    dis = [isa.disasm(w) for w in i2c.words]
    assert sum(d.startswith("wait 1 pin 1 timeout r3") for d in dis) == 6
    assert sum(d.startswith(("ldi r3,", "jmp t,")) for d in dis) == 12
    assert len(i2c.words) - i2c.labels["stretch_timeout"] == 8
    from pe import usbdev
    usb = {n: len(pe_prog(n, **usbdev.defines()).words) for n in ("usb_ls_dev", "usb_ls_rx", "usb_ls_tx")}
    assert usb == {"usb_ls_dev": 129, "usb_ls_rx": 143, "usb_ls_tx": 49}
    assert {n: len(program(n)["words"]) for n in PIO_SIZES} == PIO_SIZES
    # Pico-PIO-USB's README: "1 PIO, 3 state machines, 32 instructions"
    assert sum(PIO_SIZES[n] for n in ("usb_tx_dpdm", "usb_edge_detector", "usb_nrzi_decoder")) == 32


# ---------------------------------------------------------------- I2C bus models

class I2cTarget:
    """Bus-level I2C target at 0x50 with a register pointer (an EEPROM-like
    device). update() takes the resolved SDA/SCL levels and returns the lines
    it releases (1) or pulls low (0). Faults: hold SCL low from the falling
    edge before the n-th SCL rise on, as a stretching target does
    (`scl_stuck_at`, until release_scl()); hold SDA low (`sda_stuck`)."""

    def __init__(self, addr=0x50, scl_stuck_at=None, sda_stuck=False):
        self.addr, self.mem, self.ptr = addr, bytearray((0x80 + i) & 0xFF for i in range(256)), 0
        self.state, self.bits, self.byte, self.first = "idle", 0, 0, False
        self.psda = self.pscl = 1
        self.sda_rel, self.scl_rel = 1, 1
        self.rises, self.scl_stuck_at, self.sda_stuck = 0, scl_stuck_at, sda_stuck
        self.log = []

    def release_scl(self):
        self.scl_stuck_at, self.scl_rel = None, 1

    def update(self, sda, scl):
        if scl and self.pscl and self.psda and not sda:          # START / repeated START
            self.state, self.bits, self.byte, self.sda_rel = "addr", 0, 0, 1
        elif scl and self.pscl and not self.psda and sda:        # STOP
            self.state, self.sda_rel = "idle", 1
        elif scl and not self.pscl:                              # SCL rise
            self.rises += 1
            if self.state in ("addr", "write") and self.bits < 8:
                self.byte, self.bits = (self.byte << 1) | sda, self.bits + 1
            elif self.state == "rack":
                self.master_ack = sda == 0
        elif self.pscl and not scl:                              # SCL fall
            if self.scl_stuck_at is not None and self.rises + 1 >= self.scl_stuck_at:
                self.scl_rel = 0                 # stretch: the next release won't raise SCL
            if self.state in ("addr", "write") and self.bits == 8:
                if self.state == "addr":
                    ok = self.byte >> 1 == self.addr
                    self.read, self.first = self.byte & 1, True
                else:
                    ok = True
                    if self.first:
                        self.ptr, self.first = self.byte, False
                    else:
                        self.mem[self.ptr], self.ptr = self.byte, (self.ptr + 1) & 0xFF
                self.log.append(("A" if self.state == "addr" else "W", self.byte, ok))
                self.sda_rel = 0 if ok else 1
                self.state = ("ack_r" if self.read else "ack_w") if ok else "idle"
            elif self.state == "ack_w":
                self.state, self.bits, self.byte, self.sda_rel = "write", 0, 0, 1
            elif self.state == "ack_r" or (self.state == "rack" and self.master_ack):
                self.send = self.mem[self.ptr]
                self.ptr = (self.ptr + 1) & 0xFF
                self.state, self.bits, self.sda_rel = "read", 1, (self.send >> 7) & 1
            elif self.state == "read":
                if self.bits < 8:
                    self.sda_rel, self.bits = (self.send >> (7 - self.bits)) & 1, self.bits + 1
                else:
                    self.state, self.sda_rel = "rack", 1
            elif self.state == "rack":
                self.state, self.sda_rel = "idle", 1
        self.psda, self.pscl = sda, scl
        return (0 if self.sda_stuck else self.sda_rel), self.scl_rel


class I2cMonitor:
    """Independent bus decoder: ('S'|'P', t) and ('B', byte, ack_bit, t)."""

    def __init__(self):
        self.psda = self.pscl = 1
        self.ev, self.bits, self.val = [], 0, 0
        self.scl_runs, self._lvl, self._n = [], 1, 0

    def update(self, sda, scl, t):
        if scl == self._lvl:
            self._n += 1
        else:
            self.scl_runs.append((self._lvl, self._n))
            self._lvl, self._n = scl, 1
        if scl and self.pscl and self.psda and not sda:
            self.ev.append(("S", t))
            self.bits = self.val = 0
        elif scl and self.pscl and not self.psda and sda:
            self.ev.append(("P", t))
        elif scl and not self.pscl:
            if self.bits < 8:
                self.val, self.bits = (self.val << 1) | sda, self.bits + 1
            else:
                self.ev.append(("B", self.val, sda, t))
                self.bits = self.val = 0
        self.psda, self.pscl = sda, scl

    def transcript(self):
        return [e[:-1] for e in self.ev]


# ---------------------------------------------------------------- protoemu I2C master

class PeI2c:
    """i2c_master.pasm on the model at 100 kHz SCL; one step() = one 50 MHz clock."""
    START, STOP = 2, 3

    def __init__(self, target):
        self.mh = mh = ModelHost()
        mh.apply(host.pin_owner_writes({SDA: 0, SCL: 0}))
        mh.write(sim.A_POUT, 0b11)
        mh.write(sim.A_OD, 0b11)
        mh.pull_up = 0b11
        mh.apply(host.load_program(0, pe_prog("i2c_master", SDA=SDA, SCL=SCL),
                                   out_pins=(SDA, 1), in_pins=SDA, clkdiv=PE_I2C_DIV))
        mh.start(1)
        self.target, self.mon = target, I2cMonitor()
        self.replies, self.tx_words, self.rx_words = [], 0, 0

    @staticmethod
    def write(b):
        return b << 24

    @staticmethod
    def read(ack):
        return 1 | (0 if ack else 4)

    def t(self):
        return self.mh.chip.cycle / F_PE

    def step(self):
        v = self.mh.bus_uio()
        sda, scl = v & 1, (v >> 1) & 1
        self.mon.update(sda, scl, self.t())
        rs, rc = self.target.update(sda, scl)
        self.mh.ext_oe = (0 if rs else 1) | (0 if rc else 2)
        self.mh.step()
        got = self.mh.pop_all(0)
        self.rx_words += len(got)
        self.replies += got

    def run(self, cmds, until_idle=True, budget=2_000_000):
        cmds = list(cmds)
        for _ in range(budget):
            if cmds and self.mh.tx_level(0) < 8:
                self.mh.push(0, cmds.pop(0))
                self.tx_words += 1
            self.step()
            core = self.mh.chip.cores[0]
            if not cmds and until_idle and not core.txf and core.stalled and not core.rxf \
                    and core.pc == 0:
                return
        raise AssertionError("protoemu did not go idle")


def reg_write_pe(m, reg, data):
    W = m.write
    return [m.START, W(0xA0), W(reg)] + [W(d) for d in data] + [m.STOP]


def reg_read_pe(m, reg, n):
    W, R = m.write, m.read
    return [m.START, W(0xA0), W(reg), m.START, W(0xA1)] + [R(i < n - 1) for i in range(n)] + [m.STOP]


# ---------------------------------------------------------------- PIO I2C master + pico-examples glue

class Hang(Exception):
    """A pico-examples busy-wait loop made no progress within the budget."""


class PioI2c:
    """pico-examples pio/i2c: i2c.pio on the PIO model, pio_i2c.c as Python.
    One step() = one SM clock = 1 / 3.2 MHz at 100 kHz SCL."""
    ICOUNT_LSB, FINAL_LSB, DATA_LSB, NAK_LSB = 10, 9, 1, 0
    SC0_SD0, SC0_SD1, SC1_SD0, SC1_SD1 = range(4)
    MOV_ISR_NULL = 0xA0C3                       # pio_encode_mov(pio_isr, pio_null)

    def __init__(self, target, budget=400_000):
        p = program("i2c")
        self.table = program("set_scl_sda")["words"]
        # i2c_program_init: pins, autopull 16 (shift left), autopush 8, entry_point
        self.sm = PioSm(p, entry=p["public_labels"]["entry_point"], in_base=SDA, jmp_pin=SDA,
                        out_base=SDA, set_base=SDA, side_base=SCL, autopull=True, pull_thresh=16,
                        autopush=True, push_thresh=8)
        self.sm.pindirs = 0b11                  # both released (OE inverted in the pad)
        self.target, self.mon, self.tlines = target, I2cMonitor(), (1, 1)
        self.budget, self.spins, self.on_budget = budget, 0, None
        self.tx_words = self.rx_words = self.ctrl_writes = 0
        self.where = None

    def t(self):
        return self.sm.cycles / PIO_I2C_TICK

    def step(self):
        drive = self.sm.pindirs & 3             # pindir 1 = released
        ts, tc = self.tlines
        sda, scl = drive & 1 & ts, (drive >> 1) & 1 & tc
        self.mon.update(sda, scl, self.t())
        ts, tc = self.tlines = self.target.update(sda, scl)
        self.sm.step((drive & 1 & ts) | (((drive >> 1) & 1 & tc) << 1))

    def spin(self, where):
        """One iteration of a C busy-wait loop. Past the budget: call on_budget
        once (the caller can end a fault there) or raise Hang."""
        self.where = where
        self.spins += 1
        if self.spins > self.budget:
            if not self.on_budget:
                raise Hang(where)
            cb, self.on_budget = self.on_budget, None
            cb(self, where)
        self.step()

    # pio_i2c.c
    def check_error(self):
        return bool(self.sm.irq & 1)

    def resume_after_error(self):
        self.sm.txf.clear()                      # pio_sm_drain_tx_fifo
        self.sm.osr_cnt = 32
        self.sm.pc, self.sm.delay, self.sm.irq_waiting = self.sm.wrap_bottom, 0, None
        self.sm.exec_latch = None                # pio_sm_exec(jmp wrap_bottom)
        self.sm.irq &= ~1                        # pio_interrupt_clear
        self.ctrl_writes += 3

    def rx_enable(self, en):
        self.sm.autopush = en
        self.ctrl_writes += 1

    def _put(self, d):
        self.sm.put(((d & 0xFFFF) << 16) | (d & 0xFFFF))   # halfword write: replicated lanes
        self.tx_words += 1

    def put16(self, d):
        while len(self.sm.txf) >= self.sm.depth:
            self.spin("put16: TX FIFO full")
        self._put(d)

    def put_or_err(self, d):
        while len(self.sm.txf) >= self.sm.depth:
            if self.check_error():
                return
            self.spin("put_or_err: TX FIFO full")
        if self.check_error():
            return
        self._put(d)

    def get(self):
        self.rx_words += 1
        return self.sm.get() & 0xFF

    def start(self):
        for d in (2 << self.ICOUNT_LSB, self.table[self.SC1_SD0], self.table[self.SC0_SD0],
                  self.MOV_ISR_NULL):
            self.put_or_err(d)

    def stop(self):
        for d in (2 << self.ICOUNT_LSB, self.table[self.SC0_SD0], self.table[self.SC1_SD0],
                  self.table[self.SC1_SD1]):
            self.put_or_err(d)

    def repstart(self):
        for d in (4 << self.ICOUNT_LSB, self.table[self.SC0_SD1], self.table[self.SC1_SD1],
                  self.table[self.SC1_SD0], self.table[self.SC0_SD0], self.MOV_ISR_NULL):
            self.put_or_err(d)

    def wait_idle(self):
        self.sm.txstall = False                  # pio->fdebug = 1u << (TXSTALL_LSB + sm)
        self.ctrl_writes += 1
        while not (self.sm.txstall or self.check_error()):
            self.spin("pio_i2c_wait_idle")

    def write_blocking(self, addr, data):
        err = 0
        self.start()
        self.rx_enable(False)
        self.put16((addr << 2) | 1)
        data, n = list(data), len(data)
        while n and not self.check_error():
            if len(self.sm.txf) < self.sm.depth:
                n -= 1
                self.put_or_err((data.pop(0) << self.DATA_LSB) | ((n == 0) << self.FINAL_LSB) | 1)
            else:
                self.spin("write_blocking: data loop")
        self.stop()
        self.wait_idle()
        if self.check_error():
            err = -1
            self.resume_after_error()
            self.stop()
        return err

    def read_reg(self, addr, reg, n):
        """Register read with a repeated START, built from pio_i2c.c's
        low-level calls the way read_blocking/write_blocking use them."""
        out = []
        self.start()
        self.rx_enable(False)
        self.put16((addr << 2) | 1)
        self.put_or_err((reg << self.DATA_LSB) | 1)
        self.repstart()
        self.rx_enable(True)
        while self.sm.rxf:
            self.get()
        self.put16((addr << 2) | 3)
        tx_remain, first = n, True
        while (tx_remain or n) and not self.check_error():
            progressed = False
            if tx_remain and len(self.sm.txf) < self.sm.depth:
                tx_remain -= 1
                self.put16((0xFF << 1) | (0 if tx_remain else (1 << self.FINAL_LSB) | 1))
                progressed = True
            if self.sm.rxf:
                if first:
                    self.get()
                    first = False
                else:
                    n -= 1
                    out.append(self.get())
                progressed = True
            if not progressed:
                self.spin("read loop")
        self.stop()
        self.wait_idle()
        return out

    def settle(self, n=200):
        for _ in range(n):
            self.step()


# ---------------------------------------------------------------- I2C: clean transactions

def bus_time(mon):
    s = [e[-1] for e in mon.ev if e[0] == "S"]
    p = [e[-1] for e in mon.ev if e[0] == "P"]
    return p[-1] - s[0]


def i2c_clean():
    """Register write (3 data bytes) and register read (2 bytes, repeated START)
    on both engines against the same target model. Returns measurements."""
    r = {}
    tp, tq = I2cTarget(), I2cTarget()
    pe, pio = PeI2c(tp), PioI2c(tq)
    pe.run(reg_write_pe(pe, 0x10, [0xDE, 0xAD, 0x42]))
    assert pio.write_blocking(0x50, [0x10, 0xDE, 0xAD, 0x42]) == 0
    pio.settle()
    want_w = [("S",), ("B", 0xA0, 0), ("B", 0x10, 0), ("B", 0xDE, 0), ("B", 0xAD, 0),
              ("B", 0x42, 0), ("P",)]
    assert pe.mon.transcript() == want_w and pio.mon.transcript() == want_w
    assert tp.mem[0x10:0x13] == tq.mem[0x10:0x13] == b"\xde\xad\x42"
    r["write"] = dict(pe_bus_us=bus_time(pe.mon) * 1e6, pio_bus_us=bus_time(pio.mon) * 1e6,
                      pe_tx=pe.tx_words, pe_rx=pe.rx_words, pio_tx=pio.tx_words,
                      pio_rx=pio.rx_words, pio_ctrl=pio.ctrl_writes)
    assert [x & 1 for x in pe.replies] == [0] * 5

    tp, tq = I2cTarget(), I2cTarget()
    pe, pio = PeI2c(tp), PioI2c(tq)
    pe.run(reg_read_pe(pe, 0x20, 2))
    got = pio.read_reg(0x50, 0x20, 2)
    pio.settle()
    want_r = [("S",), ("B", 0xA0, 0), ("B", 0x20, 0), ("S",), ("B", 0xA1, 0), ("B", 0xA0, 0),
              ("B", 0xA1, 1), ("P",)]
    assert pe.mon.transcript() == want_r and pio.mon.transcript() == want_r
    assert [x & 0xFF for x in pe.replies[3:]] == got == [0xA0, 0xA1]
    r["read"] = dict(pe_bus_us=bus_time(pe.mon) * 1e6, pio_bus_us=bus_time(pio.mon) * 1e6,
                     pe_tx=pe.tx_words, pe_rx=pe.rx_words, pio_tx=pio.tx_words,
                     pio_rx=pio.rx_words, pio_ctrl=pio.ctrl_writes)
    # SCL high/low in data bytes (both engines meet 4.0 us high / 4.7 us low)
    for name, eng, unit in (("pe", pe, 1 / F_PE), ("pio", pio, 1 / PIO_I2C_TICK)):
        runs = eng.mon.scl_runs[2:-2]
        r[f"{name}_scl_high_min_us"] = min(n for lv, n in runs if lv) * unit * 1e6
        r[f"{name}_scl_low_min_us"] = min(n for lv, n in runs if not lv) * unit * 1e6
    # per-byte time inside the read (address byte to next byte boundary)
    for name, eng in (("pe", pe), ("pio", pio)):
        bt = [e[-1] for e in eng.mon.ev if e[0] == "B"]
        r[f"{name}_byte_us"] = (bt[-2] - bt[-3]) * 1e6
    return r


def test_i2c_clean_transactions_match():
    r = i2c_clean()
    # UM10204 standard mode: tHIGH >= 4.0 us, tLOW >= 4.7 us
    assert r["pe_scl_high_min_us"] == r["pe_scl_low_min_us"] == 5.0
    # pico-examples: 5 us high; between bytes SCL is low for 15 SM clocks = 4.6875 us
    # (jmp pin [2], out, out, jmp, set, out [7])
    assert r["pio_scl_high_min_us"] == 5.0 and r["pio_scl_low_min_us"] == 4.6875
    # bus time from START to STOP at nominal 100 kHz: protoemu is slower per byte
    # protoemu: 105.0 and 560.0 us before fault-fixes added the SDA read-back (+9 ticks per
    # WRITE command, +3 per READ)
    assert r["pe_byte_us"] == pytest.approx(111.24, abs=0.01) and r["pio_byte_us"] == pytest.approx(89.6875)
    assert r["read"]["pe_bus_us"] == pytest.approx(601.26, abs=0.01)
    assert r["write"]["pe_bus_us"] == pytest.approx(575.0, abs=0.01)
    assert r["read"]["pio_bus_us"] == pytest.approx(478.125)
    # host traffic for the register read: protoemu 8 commands / 5 replies (3 ACK + 2 data);
    # pico-examples 19 halfword FIFO writes (START 4, RSTART 6, STOP 4, 5 bytes) and 3 reads
    assert (r["read"]["pe_tx"], r["read"]["pe_rx"]) == (8, 5)
    assert (r["read"]["pio_tx"], r["read"]["pio_rx"], r["read"]["pio_ctrl"]) == (19, 3, 3)


# ---------------------------------------------------------------- I2C: faults

def i2c_scl_stuck():
    """The target holds SCL low from the 14th SCL rise on (bit 5 of the register
    byte) during a register write. protoemu: every queued command times out and
    the core returns to idle. PIO: the SM waits forever at `wait 1 pin, 1` and
    pio_i2c_write_blocking never returns."""
    r = {}
    tp = I2cTarget(scl_stuck_at=14)
    pe = PeI2c(tp)
    cmds = reg_write_pe(pe, 0x10, [0xDE])
    t0 = None
    for c in cmds:
        pe.mh.push(0, c)
    for i in range(3_000_000):
        pe.step()
        if t0 is None and tp.scl_rel == 0 and (pe.mh.chip.pin_out >> SCL) & 1:
            t0 = pe.t()                         # the master has released SCL; it stays low
        core = pe.mh.chip.cores[0]
        if 0x100 in pe.replies and "pe_report_ms" not in r:
            r["pe_report_ms"] = (pe.t() - t0) * 1e3
        if t0 and not core.txf and core.stalled and core.pc == 0:
            r["pe_idle_ms"] = (pe.t() - t0) * 1e3
            break
    r["pe_replies"] = [hex(x) for x in pe.replies]
    r["pe_f1"] = bool(pe.mh.chip.flags & 2)
    tp.release_scl()
    pe.mh.write(sim.A_FCLR, 2)
    pe.replies.clear()
    pe.run(reg_write_pe(pe, 0x10, [0xBE]))
    r["pe_after_release_ok"] = [x & 1 for x in pe.replies] == [0, 0, 0] and tp.mem[0x10] == 0xBE

    tq = I2cTarget(scl_stuck_at=14)
    pio = PioI2c(tq, budget=int(0.1 * PIO_I2C_TICK))     # 100 ms of busy-waiting

    def give_up(p, where):                     # what the SM and CPU look like after 100 ms
        r["pio_cpu_spinning_in"], r["pio_pc"], r["pio_stalled"] = where, p.sm.pc, p.sm.stalled
        r["pio_listing"] = program("i2c")["listing"][p.sm.pc]
        r["pio_irq"], r["pio_txstall"] = p.sm.irq & 1, p.sm.txstall
        r["pio_bus"] = p.mon.transcript()
        tq.release_scl()                       # the target lets go: PIO simply continues
        p.budget = 10 ** 6

    pio.on_budget = give_up
    r["pio_err"] = pio.write_blocking(0x50, [0x10, 0xDE])
    pio.settle()
    r["pio_after_release"] = pio.mon.transcript()
    r["pio_mem_ok"] = tq.mem[0x10] == 0xDE
    return r


def test_i2c_scl_stuck_low():
    r = i2c_scl_stuck()
    # protoemu: the register byte, the data byte and STOP each time out (TMO ticks)
    assert r["pe_replies"] == ["0x0", "0x100", "0x100"] and r["pe_f1"]
    assert 2.4 < r["pe_report_ms"] < 2.6                 # (TMO + 1) ticks of 1.25 us
    assert 7.4 < r["pe_idle_ms"] < 7.7
    assert r["pe_after_release_ok"]
    # PIO, 100 ms later: the SM is parked on the clock-stretch WAIT with no error
    # flag; the CPU is still inside pio_i2c_write_blocking (pio_i2c_stop's FIFO wait)
    assert r["pio_cpu_spinning_in"] == "put_or_err: TX FIFO full"
    assert r["pio_listing"].startswith("wait   1 pin, 1") and r["pio_stalled"]
    assert r["pio_irq"] == 0 and not r["pio_txstall"]
    assert r["pio_bus"] == [("S",), ("B", 0xA0, 0)]
    # when the target lets go, the transfer completes (I2C allows any stretch)
    assert r["pio_err"] == 0 and r["pio_mem_ok"] and r["pio_after_release"][-1] == ("P",)


def i2c_nack():
    """Address NACK (nobody at 0x51) followed by three queued data bytes."""
    r = {}
    tp = I2cTarget()
    pe = PeI2c(tp)
    W = pe.write
    pe.run([pe.START, W(0xA2), W(1), W(2), W(3), pe.STOP])
    r["pe_replies"] = [x & 1 for x in pe.replies]
    r["pe_bytes_on_bus"] = sum(1 for e in pe.mon.ev if e[0] == "B")
    tq = I2cTarget()
    pio = PioI2c(tq)
    r["pio_err"] = pio.write_blocking(0x51, [1, 2, 3])
    pio.settle()
    r["pio_bytes_on_bus"] = sum(1 for e in pio.mon.ev if e[0] == "B")
    r["pio_ctrl"] = pio.ctrl_writes
    r["pio_transcript"] = pio.mon.transcript()
    return r


def test_i2c_nack():
    r = i2c_nack()
    # protoemu reports the NACK per byte but keeps clocking what the host queued
    assert r["pe_replies"] == [1, 1, 1, 1] and r["pe_bytes_on_bus"] == 4
    # pico-examples stops at the NACK: the SM halts on `irq wait`, the CPU recovers it
    assert r["pio_err"] == -1 and r["pio_bytes_on_bus"] == 1 and r["pio_ctrl"] == 5
    assert r["pio_transcript"][-1] == ("P",)


def i2c_sda_stuck():
    """SDA held low by a broken target. pico-examples reads false ACKs;
    protoemu, since fault-fixes, reads SDA back and replies 0x200 (bus fault)."""
    r = {}
    tp = I2cTarget(sda_stuck=True)
    pe = PeI2c(tp)
    pe.run([pe.START, pe.write(0xA0), pe.read(False), pe.STOP])
    r["pe_replies"] = [hex(x) for x in pe.replies]
    tq = I2cTarget(sda_stuck=True)
    pio = PioI2c(tq)
    r["pio_err"] = pio.write_blocking(0x50, [0x10])
    return r


def test_i2c_sda_stuck_low():
    r = i2c_sda_stuck()
    assert r["pe_replies"] == ["0x200", "0x200"]         # bus fault on the address byte and the NACK
    assert r["pio_err"] == 0                             # "success"


# ---------------------------------------------------------------- UART RX: framing error and break

BAUD = 115200


def uart_levels(frames):
    """frames: list of (byte, stop_level) or ('break', bits). Returns per-bit levels."""
    out = [1] * 4
    for f in frames:
        if f[0] == "break":
            out += [0] * f[1] + [1] * 4
        else:
            b, stop = f
            out += [0] + [(b >> i) & 1 for i in range(8)] + [stop]
            if not stop:
                out += [1] * 2                  # the line recovers two bits later
    return out + [1] * 12


def pe_uart_rx(levels, ppm=0):
    """ppm > 0: the sender's bits are longer (a slow sender), as in test_uart.py."""
    mh = ModelHost()
    div = host.clkdiv_for(F_PE, 8 * BAUD)
    mh.apply(host.load_program(0, pe_prog("uart_rx", RX=16), in_pins=16, clkdiv=div))
    mh.ui = 1
    mh.start(1)
    bit, t, got = F_PE / BAUD * (1 + ppm / 1e6), 0.37, []
    for lv in levels:
        mh.ui = lv
        t += bit
        n = int(t)
        t -= n
        mh.step(n)
        got += mh.pop_all(0)
    return [x & 0xFF for x in got], bool(mh.chip.flags & 1)


def pio_uart_rx(levels, ppm=0):
    p = program("uart_rx")
    sm = PioSm(p, in_base=0, jmp_pin=0, in_right=True, fifo_depth=8)   # FIFO joined RX
    bit, t, got = 8 * (1 + ppm / 1e6), 0.37, []  # SM clock = 8 x baud
    for lv in levels:
        t += bit
        n = int(t)
        t -= n
        for _ in range(n):
            sm.step(lv)
        while sm.rxf:
            got.append(sm.get() >> 24)          # uart_rx_program_getc: byte 3 of the word
    return got, bool(sm.irq & (1 << 4))


def uart_tolerance():
    """32 random bytes back to back from a sender off by ppm: which receivers
    get them all with no framing error."""
    import random
    rng = random.Random(1)
    data = [rng.getrandbits(8) for _ in range(32)]
    frames = uart_levels([(b, 1) for b in data])
    r = {}
    for ppm in (-40000, -35000, -30000, -25000, 0, 40000):
        r[ppm] = dict(pe=pe_uart_rx(frames, ppm) == (data, False),
                      pio=pio_uart_rx(frames, ppm) == (data, False),
                      pe_pio_style=pe_uart_rx_transliterated(frames, ppm)[1:] == (data, False))
    return r


def uart_faults():
    r = {}
    good = uart_levels([(0x55, 1), (0xA5, 1), (0x3C, 1)])
    framing = uart_levels([(0x55, 1), (0xA5, 0), (0x3C, 1)])
    brk = uart_levels([(0x55, 1), ("break", 25), (0x3C, 1)])
    for name, lv in (("good", good), ("framing", framing), ("break", brk)):
        r[f"pe_{name}"] = pe_uart_rx(lv)
        r[f"pio_{name}"] = pio_uart_rx(lv)
    return r


def test_uart_rx_faults():
    r = uart_faults()
    assert r["pe_good"] == r["pio_good"] == ([0x55, 0xA5, 0x3C], False)
    # protoemu pushes the bad byte and sets F0; PIO drops it and sets IRQ 4
    assert r["pe_framing"] == ([0x55, 0xA5, 0x3C], True)
    assert r["pio_framing"] == ([0x55, 0x3C], True)
    assert r["pe_break"] == ([0x55, 0x00, 0x3C], True)
    assert r["pio_break"] == ([0x55, 0x3C], True)


# pico-examples uart_rx.pio, transliterated instruction for instruction
# (set x -> ldi r0, jmp x-- -> jmp r0--, irq 4 rel -> flag set 0). Not shipped
# firmware: it shows the 13 vs 9 instruction gap is a firmware choice
# (uart_rx.pasm pushes before checking the stop bit and right-aligns the byte),
# not an ISA limit. The byte lands in bits [31:24], as on PIO.
UART_RX_PIO_STYLE = """
.program uart_rx_pio_style
.shift_in right
start:
    wait 0 pin RX
    ldi r0, 7                  [10]
bitloop:
    in pins, 1
    jmp r0--, bitloop          [6]
    jmp pin RX, good_stop
    flag set 0
    wait 1 pin RX
    jmp start
good_stop:
    push block
"""


def pe_uart_rx_transliterated(levels, ppm=0):
    prog = asm.assemble(UART_RX_PIO_STYLE, {"RX": 16})
    mh = ModelHost()
    mh.apply(host.load_program(0, prog, in_pins=16, clkdiv=host.clkdiv_for(F_PE, 8 * BAUD)))
    mh.ui = 1
    mh.start(1)
    bit, t, got = F_PE / BAUD * (1 + ppm / 1e6), 0.37, []
    for lv in levels:
        mh.ui = lv
        t += bit
        n = int(t)
        t -= n
        mh.step(n)
        got += mh.pop_all(0)
    return len(prog.words), [x >> 24 for x in got], bool(mh.chip.flags & 1)


def test_uart_rx_back_to_back_tolerance():
    """uart_rx.pasm returns to its start-bit WAIT at D+77 (it pushes during the
    stop bit); pico-examples' uart_rx returns at D+78, after `jmp pin` and
    `push`. With a fast sender (frames shorter than 78 ticks) the late return
    adds up from frame to frame until a bit is mis-sampled. The transliteration
    of the PIO program on protoemu fails the same way."""
    r = uart_tolerance()
    # frames are 80 ticks nominal: uart_rx.pasm needs >= 77, pico-examples >= 78
    assert [p for p, v in r.items() if v["pe"]] == [-35000, -30000, -25000, 0, 40000]
    assert [p for p, v in r.items() if v["pio"]] == [-25000, 0, 40000]
    assert all(v["pio"] == v["pe_pio_style"] for v in r.values())


def test_uart_rx_pio_transliteration():
    framing = uart_levels([(0x55, 1), (0xA5, 0), (0x3C, 1)])
    assert pe_uart_rx_transliterated(framing) == (9, [0x55, 0x3C], True) == \
        (len(program("uart_rx")["words"]),) + pio_uart_rx(framing)


# ---------------------------------------------------------------- per-bit timing: UART TX and SPI

def tx_bit_ticks():
    """Back-to-back UART TX bytes at 1 tick per clock: ticks per bit and per byte."""
    r = {}
    mh = ModelHost()
    mh.apply(host.load_program(0, pe_prog("uart_tx"), out_pins=(8, 1), side_pins=8, clkdiv=1 << 8))
    for b in (0x00, 0xFF, 0x00):
        mh.push(0, b)
    mh.start(1)
    trace = []
    for _ in range(400):
        mh.step()
        trace.append((mh.chip.pin_out >> 8) & 1)
    falls = [i for i in range(1, len(trace)) if trace[i - 1] == 1 and trace[i] == 0]
    r["pe_byte"] = falls[1] - falls[0]
    sm = PioSm(program("uart_tx"), out_base=0, side_base=0, out_right=True)
    sm.pins = 1
    for b in (0x00, 0xFF, 0x00):
        sm.put(b)
    trace = []
    for _ in range(400):
        sm.step(0)
        trace.append(sm.pins & 1)
    falls = [i for i in range(1, len(trace)) if trace[i - 1] == 1 and trace[i] == 0]
    r["pio_byte"] = falls[1] - falls[0]
    return r


def spi_ticks():
    """Mode-0 SPI with MOSI looped to MISO and no external delay, two bytes
    back to back: system clocks per SCK and the data received. protoemu at
    DIV 1 and 2 (its pad inputs pass a 2-flop synchroniser, isa.md §2), and
    DIV 1 with MISO's pad in SYNC_BYPASS (one flop);
    pico-examples spi_cpha0 at clkdiv 1 with the input synchroniser bypassed,
    as pio_spi_init does (the PIO model has no synchroniser)."""
    r = {}

    def rises(sck):
        return [i for i in range(1, len(sck)) if not sck[i - 1] and sck[i]]

    for div, byp in ((1, 0), (2, 0), (1, 1)):
        mh = ModelHost()
        mh.apply(host.load_program(0, pe_prog("spi_master"), out_pins=(8, 1), side_pins=9,
                                   in_pins=16, clkdiv=div << 8))
        if byp:                                 # MISO (ui0 = P16) one synchroniser stage fresher
            mh.write(sim.A_SYNCBYP, 1 << 16)
        for b in (0xA5, 0x3C):
            mh.push(0, b << 24)
        mh.start(1)
        sck = []
        for _ in range(80 * div):
            mh.ui = (mh.chip.pin_out >> 8) & 1  # MOSI pad -> MISO (ui0 = P16)
            mh.step()
            sck.append((mh.chip.pin_out >> 9) & 1)
        e = rises(sck)
        r[f"pe_div{div}" + ("_bypass" if byp else "")] = dict(clocks_per_sck=e[1] - e[0], clocks_16bit=e[15] - e[0] + e[1] - e[0],
                                 data=[x & 0xFF for x in mh.pop_all(0)])
    sm = PioSm(program("spi_cpha0"), out_base=0, side_base=1, in_base=2, autopull=True,
               pull_thresh=8, autopush=True, push_thresh=8)
    for b in (0xA5, 0x3C):
        sm.put(b << 24)
    sck = []
    for _ in range(80):
        sm.step((sm.pins & 1) << 2)             # MOSI (pin 0) -> MISO (pin 2)
        sck.append((sm.pins >> 1) & 1)
    e = rises(sck)
    r["pio_div1"] = dict(clocks_per_sck=e[1] - e[0], clocks_16bit=e[15] - e[0] + e[1] - e[0],
                         data=[sm.get() & 0xFF for _ in range(len(sm.rxf))])
    return r


def test_uart_tx_and_spi_cycles():
    assert tx_bit_ticks() == {"pe_byte": 80, "pio_byte": 80}       # 8 per bit, 10 bits
    r = spi_ticks()
    assert r["pio_div1"] == dict(clocks_per_sck=4, clocks_16bit=64, data=[0xA5, 0x3C])
    # protoemu at f_tick = f_clk: 4 clocks per SCK, but the synchroniser makes the
    # MISO sample one bit late even with zero external delay
    assert r["pe_div1"] == dict(clocks_per_sck=4, clocks_16bit=64, data=[0x52, 0x9E])
    assert r["pe_div2"] == dict(clocks_per_sck=8, clocks_16bit=128, data=[0xA5, 0x3C])
    # with SYNC_BYPASS on MISO, the same 2-instruction program at 4 clocks per
    # SCK, like PIO (test_spi_fast.py: a device with real delay wants spi_master_fast)
    assert r["pe_div1_bypass"] == dict(clocks_per_sck=4, clocks_16bit=64, data=[0xA5, 0x3C])


if __name__ == "__main__":
    import json
    for f in (i2c_clean, i2c_scl_stuck, i2c_nack, i2c_sda_stuck, uart_faults, uart_tolerance,
              tx_bit_ticks, spi_ticks):
        print(f.__name__, json.dumps(f(), indent=1, default=str))
