"""Top-level tests: everything goes through the real SPI host port and the pins.

Each protocol test loads firmware from firmware/*.pasm with the host library,
then talks to a Python device model that sees only the pins.
"""
import os
import pathlib

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, RisingEdge, Timer
from cocotb.utils import get_sim_time

from pe import asm, host, sim

FW = pathlib.Path(__file__).resolve().parent.parent / "firmware"
BANK_AW = int(os.environ.get("PE_BANK_AW", "4"))
NCORES = int(os.environ.get("PE_NCORES", "4"))
core = lambda i: i % NCORES  # tests spread over the cores that exist
CLK_NS = 20  # 50 MHz
SCK_HALF = 4  # SPI SCK half period in clk cycles: clk/8 (the port allows up to clk; test_host_port_fast)

# logical pins (docs/isa.md §2)
UIO = lambda i: i
UO = lambda i: 8 + i
UI = lambda i: 16 + i


def load_fw(name, **defines):
    return asm.assemble((FW / f"{name}.pasm").read_text(), defines)


class Spi:
    """Bit-banged SPI master on ui[7:5]/uo[7] (mode 0, MSB first)."""
    CS, SCK, MOSI = 7, 6, 5

    def __init__(self, dut):
        self.dut = dut
        self.ui = 0x80  # CS_n high

    def _set(self, bit, val):
        self.ui = (self.ui & ~(1 << bit)) | (int(val) << bit)
        self.dut.ui_in.value = self.ui

    def set_low_inputs(self, val):
        """Drive ui[4:0] (protocol inputs) without disturbing the SPI pins."""
        self.ui = (self.ui & 0xE0) | (val & 0x1F)
        self.dut.ui_in.value = self.ui

    async def xfer(self, data):
        out = bytearray()
        self._set(self.CS, 0)
        await ClockCycles(self.dut.clk, SCK_HALF)
        for byte in data:
            r = 0
            for i in range(7, -1, -1):
                self._set(self.MOSI, (byte >> i) & 1)
                await ClockCycles(self.dut.clk, SCK_HALF)
                r = (r << 1) | ((int(self.dut.uo_out.value) >> 7) & 1)
                self._set(self.SCK, 1)
                await ClockCycles(self.dut.clk, SCK_HALF)
                self._set(self.SCK, 0)
            out.append(r)
        await ClockCycles(self.dut.clk, SCK_HALF)
        self._set(self.CS, 1)
        await ClockCycles(self.dut.clk, SCK_HALF)
        return bytes(out)

    async def write(self, addr, words, fixed=False):
        await self.xfer(host.spi_frame(host.CMD_WRITE_FIXED if fixed else host.CMD_WRITE, addr, words))

    async def read(self, addr, n=1, fixed=False):
        r = await self.xfer(host.spi_frame(host.CMD_READ_FIXED if fixed else host.CMD_READ, addr, nread=n))
        r = r[3 + host.READ_DUMMY:]
        return [int.from_bytes(r[4 * i:4 * i + 4], "big") for i in range(n)]

    async def apply(self, writes):
        for addr, words in host.coalesce(writes):
            await self.write(addr, words)


EXT = None


class Ext:
    """Shadow of the testbench's external-device drivers. cocotb applies writes at
    the end of the time step, so read-modify-write through .value loses updates."""

    def __init__(self, dut):
        self.dut, self.oe, self.val = dut, 0, 0
        dut.ext_oe.value = 0
        dut.ext_val.value = 0

    def set(self, pin, drive, val=0):
        self.oe = (self.oe | (1 << pin)) if drive else (self.oe & ~(1 << pin))
        self.val = (self.val & ~(1 << pin)) | (int(val) << pin)
        self.dut.ext_oe.value = self.oe
        self.dut.ext_val.value = self.val


async def setup(dut):
    cocotb.start_soon(Clock(dut.clk, CLK_NS, unit="ns").start())
    spi = Spi(dut)
    dut.ena.value = 1
    dut.ui_in.value = spi.ui
    global EXT
    EXT = Ext(dut)
    dut.pull_up.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 10)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 5)
    return spi


def need_words(n):
    """Programs must fit in one bank: sequential PCs wrap within a bank in the
    flop build (the SRAM build has 256 words per bank)."""
    return n <= (1 << BANK_AW)


async def run_program(spi, core, prog, base=None, **cfg):
    """Load and start one program at the start of the core's own bank."""
    assert need_words(len(prog.words)), f"{prog.name} needs {len(prog.words)} words"
    base = core * 256 if base is None else base
    await spi.apply(host.load_program(core, prog, base, **cfg))
    ctrl = (await spi.read(sim.A_CTRL))[0]
    await spi.apply(host.start_writes(1 << core, ctrl & 15))


# ------------------------------------------------------------------ tests

@cocotb.test()
async def test_id(dut):
    spi = await setup(dut)
    assert (await spi.read(sim.A_ID))[0] == sim.ID_VALUE
    await spi.write(sim.core_reg(core(2), sim.C_R0), [0x1234, 0xBEEF], fixed=False)
    assert await spi.read(sim.core_reg(core(2), sim.C_R0), 2) == [0x1234, 0xBEEF]
    # FIFO: push 3 words to core 1's TXF, check level
    await spi.write(sim.core_reg(core(1), sim.C_FIFO), [1, 2, 3], fixed=True)
    st = (await spi.read(sim.core_reg(core(1), sim.C_STATUS)))[0]
    assert st & 15 == 3, hex(st)


class UartMonitor:
    """Decodes 8N1 frames from a pin, sampling every clock."""

    def __init__(self, dut, get_bit, clks_per_bit):
        self.dut, self.get_bit, self.cpb = dut, get_bit, clks_per_bit
        self.rx = bytearray()
        self.errors = 0
        cocotb.start_soon(self.run())

    async def run(self):
        clk = self.dut.clk
        while True:
            await RisingEdge(clk)
            if self.get_bit() == 0:  # start bit
                await ClockCycles(clk, self.cpb // 2)
                if self.get_bit() != 0:
                    continue
                b = 0
                for i in range(8):
                    await ClockCycles(clk, self.cpb)
                    b |= self.get_bit() << i
                await ClockCycles(clk, self.cpb)
                if self.get_bit() != 1:
                    self.errors += 1
                self.rx.append(b)


@cocotb.test()
async def test_uart_tx(dut):
    spi = await setup(dut)
    prog = load_fw("uart_tx")
    div = 2  # tick = clk/2, 8 ticks/bit -> 16 clks/bit
    TX = UO(0)
    await run_program(spi, 0, prog, out_pins=(TX, 1), side_pins=TX, clkdiv=div << 8)
    mon = UartMonitor(dut, lambda: int(dut.uo_out.value) & 1, 8 * div)
    msg = b"Hello, protoemu!"
    for i in range(0, len(msg), 4):
        chunk = msg[i:i + 4]
        while True:
            st = (await spi.read(sim.core_reg(0, sim.C_STATUS)))[0]
            if (st & 15) == 0:
                break
        await spi.write(sim.core_reg(0, sim.C_FIFO), list(chunk), fixed=True)
    for _ in range(200):
        if len(mon.rx) >= len(msg):
            break
        await ClockCycles(dut.clk, 100)
    assert bytes(mon.rx) == msg, mon.rx
    assert mon.errors == 0


@cocotb.test()
async def test_uart_rx(dut):
    spi = await setup(dut)
    RX = UI(0)
    prog = load_fw("uart_rx", RX=RX)
    div = 3  # 24 clks per bit
    cpb = 8 * div
    await run_program(spi, core(1), prog, in_pins=RX, clkdiv=div << 8)
    spi.set_low_inputs(1)  # idle high
    await ClockCycles(dut.clk, 50)
    msg = b"\x55\xAA\x00\xFFok"
    got = []
    for byte in msg:
        bits = [0] + [(byte >> i) & 1 for i in range(8)] + [1]
        for bit in bits:
            spi.set_low_inputs(bit)
            await ClockCycles(dut.clk, cpb)
        # drain while sending
        st = (await spi.read(sim.core_reg(core(1), sim.C_STATUS)))[0]
        n = (st >> 4) & 15
        if n:
            got += await spi.read(sim.core_reg(core(1), sim.C_FIFO), n, fixed=True)
    await ClockCycles(dut.clk, 3 * cpb)
    st = (await spi.read(sim.core_reg(core(1), sim.C_STATUS)))[0]
    n = (st >> 4) & 15
    if n:
        got += await spi.read(sim.core_reg(core(1), sim.C_FIFO), n, fixed=True)
    assert bytes(got) == msg, got
    assert (await spi.read(sim.A_FSET))[0] & 1 == 0, "unexpected framing error flag"


class SpiSlave:
    """Mode-0 SPI slave on the uio pins: samples MOSI on SCK rise, shifts MISO on fall."""

    def __init__(self, dut, sck, mosi, miso, responses):
        self.dut, self.sck, self.mosi, self.miso = dut, sck, mosi, miso
        self.resp = list(responses)
        self.rx = bytearray()
        self._drive(self._bit())
        self.nbit = 0
        self.cur = 0
        cocotb.start_soon(self.run())

    def _bit(self):
        byte = self.resp[0] if self.resp else 0
        return (byte >> (7 - (getattr(self, "nbit", 0) % 8))) & 1

    def _drive(self, b):
        EXT.set(self.miso, True, b)

    async def run(self):
        prev = 0
        while True:
            await RisingEdge(self.dut.clk)
            pins = int(self.dut.uio_out.value)
            s = (pins >> self.sck) & 1
            if s and not prev:
                self.cur = (self.cur << 1) | ((pins >> self.mosi) & 1)
            if prev and not s:
                self.nbit += 1
                if self.nbit % 8 == 0:
                    self.rx.append(self.cur & 0xFF)
                    self.cur = 0
                    if self.resp:
                        self.resp.pop(0)
                self._drive(self._bit())
            prev = s


@cocotb.test()
async def test_spi_master(dut):
    spi = await setup(dut)
    SCK, MOSI, MISO = UIO(2), UIO(3), UIO(4)
    prog = load_fw("spi_master")
    # pins must be outputs: set OE via PIN ops before starting (host-exec style prologue)
    pro = asm.assemble(f"""
.program spi_prologue
    pin {SCK}, lo
    pin {MOSI}, lo
    halt
""")
    await spi.apply(host.pin_owner_writes({SCK: core(2), MOSI: core(2), MISO: core(2)}))
    await run_program(spi, core(2), pro)
    await ClockCycles(dut.clk, 20)
    await spi.write(sim.A_CTRL, [0])
    pins = int(dut.uio_oe.value)
    assert pins & (1 << SCK) and pins & (1 << MOSI), hex(pins)
    await run_program(spi, core(2), prog, out_pins=(MOSI, 1), side_pins=SCK, in_pins=MISO, clkdiv=2 << 8)
    resp = [0xC3, 0x5A, 0x01, 0x80]
    slave = SpiSlave(dut, SCK, MOSI, MISO, resp)
    tx = [0xA5, 0x0F, 0xFF, 0x00]
    await spi.write(sim.core_reg(core(2), sim.C_FIFO), [b << 24 for b in tx], fixed=True)
    for _ in range(100):
        st = (await spi.read(sim.core_reg(core(2), sim.C_STATUS)))[0]
        if (st >> 4) & 15 == len(tx):
            break
        await ClockCycles(dut.clk, 50)
    got = await spi.read(sim.core_reg(core(2), sim.C_FIFO), len(tx), fixed=True)
    assert list(slave.rx) == tx, list(slave.rx)
    assert [g & 0xFF for g in got] == resp, got


class I2cEeprom:
    """I2C target at 7-bit address 0x50 with a 256-byte memory and a register pointer.
    Watches the resolved bus every clock; drives SDA/SCL low via ext_oe/ext_val.
    Optionally stretches SCL after each ACK."""

    def __init__(self, dut, sda, scl, addr=0x50, stretch=0):
        self.dut, self.sda, self.scl, self.addr, self.stretch = dut, sda, scl, addr, stretch
        self.mem = bytearray(range(256))
        self.ptr = 0
        self.log = []
        cocotb.start_soon(self.run())

    def _pull(self, pin, low):
        EXT.set(pin, low, 0)

    def _bus(self):
        v = int(self.dut.uio_in.value)
        return (v >> self.sda) & 1, (v >> self.scl) & 1

    async def run(self):
        clk = self.dut.clk
        psda, pscl = 1, 1
        state = "idle"
        bits = byte = 0
        rw = 0
        first = True
        send = None  # byte being sent to master
        while True:
            await RisingEdge(clk)
            sda, scl = self._bus()
            if scl and pscl and psda and not sda:  # START
                state, bits, byte, first = "addr", 0, 0, True
                self._pull(self.sda, False)
                self.log.append("S")
            elif scl and pscl and not psda and sda:  # STOP
                state = "idle"
                self._pull(self.sda, False)
                self.log.append("P")
            elif scl and not pscl:  # SCL rising: sample
                if state in ("addr", "wdata"):
                    if bits < 8:
                        byte = (byte << 1) | sda
                        bits += 1
                elif state == "rdata" and bits == 9:
                    self.master_ack = sda == 0
            elif pscl and not scl:  # SCL falling: drive
                if state in ("addr", "wdata") and bits == 8:
                    if state == "addr":
                        if (byte >> 1) == self.addr:
                            rw = byte & 1
                            self._pull(self.sda, True)
                            state = "ack_addr"
                            self.log.append(f"A{byte:02x}")
                        else:
                            state = "idle"
                            self.log.append(f"N{byte:02x}")
                    else:
                        if first:
                            self.ptr = byte
                            first = False
                        else:
                            self.mem[self.ptr] = byte
                            self.ptr = (self.ptr + 1) & 0xFF
                        self.log.append(f"W{byte:02x}")
                        self._pull(self.sda, True)
                        state = "ack_w"
                elif state in ("ack_addr", "ack_w"):
                    self._pull(self.sda, False)
                    if self.stretch:
                        self._pull(self.scl, True)
                        await ClockCycles(clk, self.stretch)
                        self._pull(self.scl, False)
                        sda, scl = self._bus()[0], 0  # so the release is seen as a rising edge
                    if state == "ack_addr" and rw:
                        state, send = "rdata", self.mem[self.ptr]
                        self._pull(self.sda, not ((send >> 7) & 1))
                        bits = 1
                    else:
                        state, bits, byte = "wdata", 0, 0
                elif state == "rdata":
                    if bits < 8:
                        self._pull(self.sda, not ((send >> (7 - bits)) & 1))
                        bits += 1
                    elif bits == 8:  # data byte done: release SDA for the master's ACK
                        self._pull(self.sda, False)
                        self.log.append(f"R{send:02x}")
                        self.ptr = (self.ptr + 1) & 0xFF
                        bits = 9
                    else:  # ACK clock done
                        if self.master_ack:
                            send = self.mem[self.ptr]
                            self._pull(self.sda, not ((send >> 7) & 1))
                            bits = 1
                        else:
                            state = "idle"
            psda, pscl = sda, scl


@cocotb.test()
async def test_i2c_master(dut):
    spi = await setup(dut)
    SDA, SCL = UIO(0), UIO(1)
    prog = load_fw("i2c_master", SDA=SDA, SCL=SCL)
    if not need_words(len(prog.words)):
        dut._log.warning(f"i2c_master needs {len(prog.words)} words; skipped with BANK_AW={BANK_AW}")
        return
    dut.pull_up.value = 0b11
    await spi.write(sim.A_POUT, [0b11])  # released
    await spi.write(sim.A_OD, [0b11])
    await spi.apply(host.pin_owner_writes({SDA: core(3), SCL: core(3)}))
    eeprom = I2cEeprom(dut, SDA, SCL, stretch=int(os.environ.get("I2C_STRETCH", "40")))
    await run_program(spi, core(3), prog, out_pins=(SDA, 1), in_pins=SDA, clkdiv=4 << 8)
    if os.environ.get("TRACE"):
        import dbg_trace
        tl = []
        cocotb.start_soon(dbg_trace.trace_core(dut, 3, 120, tl))
    START, STOP = 2, 3
    WR = lambda b: (b << 24) | 0
    RD = lambda ack: 1 | (0 if ack else 4)
    fifo = sim.core_reg(core(3), sim.C_FIFO)

    async def cmds(words):
        replies = []
        for w in words:
            while True:
                st = (await spi.read(sim.core_reg(core(3), sim.C_STATUS)))[0]
                if st & 15 < 4:
                    break
            await spi.write(fifo, [w], fixed=True)
        return replies

    async def drain(n):
        out = []
        for _ in range(400):
            st = (await spi.read(sim.core_reg(core(3), sim.C_STATUS)))[0]
            k = (st >> 4) & 15
            if k:
                out += await spi.read(fifo, k, fixed=True)
            if len(out) >= n:
                return out
            await ClockCycles(dut.clk, 100)
        raise AssertionError(f"timeout waiting for {n} replies, got {out}")

    # write 3 bytes at register 0x10
    await cmds([START, WR(0xA0), WR(0x10), WR(0xDE), WR(0xAD), WR(0x42)])
    acks = await drain(5)
    await cmds([STOP])
    dut._log.info("i2c log: " + " ".join(eeprom.log))
    if os.environ.get("TRACE"):
        dut._log.info("trace:\n" + "\n".join(tl))
    assert [a & 1 for a in acks] == [0] * 5, acks
    assert eeprom.mem[0x10:0x13] == b"\xde\xad\x42", eeprom.log
    # read them back: set pointer, repeated start, read 3 (ACK, ACK, NACK)
    await cmds([START, WR(0xA0), WR(0x10), START, WR(0xA1), RD(True), RD(True), RD(False)])
    r = await drain(6)
    await cmds([STOP])
    assert [x & 1 for x in r[:3]] == [0, 0, 0], r
    assert [x & 0xFF for x in r[3:]] == [0xDE, 0xAD, 0x42], [hex(x) for x in r]
    # wrong address -> NACK
    await cmds([START, WR(0xB0)])
    r = await drain(1)
    await cmds([STOP])
    assert r[0] & 1 == 1
    assert (await spi.read(sim.A_FSET))[0] & 2 == 0, "clock-stretch timeout flag set"
    dut._log.info("i2c log: " + " ".join(eeprom.log))


# ------------------------------------------------------------------ USB low speed

USB_LINE = {"J": (0, 1), "K": (1, 0), "0": (0, 0)}


async def usb_drive(dut, states):
    for s in states:
        dp, dm = USB_LINE[s]
        EXT.set(0, True, dp)
        EXT.set(1, True, dm)
        await ClockCycles(dut.clk, 32)
    EXT.set(0, False)
    EXT.set(1, False)


async def usb_listen(dut, cycles):
    trace = []
    for _ in range(cycles):
        await RisingEdge(dut.clk)
        v = int(dut.uio_in.value) & 3
        trace.append({2: "J", 1: "K", 0: "0"}.get(v, "?"))
    return trace


def usb_decode_trace(trace):
    from pe import usb
    k = trace.index("K")
    states = [trace[j] for j in range(k + 16, len(trace), 32)]
    return usb.decode(["J"] + states), k


@cocotb.test()
async def test_usb_ls_device(dut):
    """USB low-speed device side in firmware: SETUP+DATA0 -> ACK (and a good record),
    IN to another address -> silence, IN -> NAK, IN -> staged DATA1.
    Two cores (RX on core 0, TX on core 1); bit time = 32 clocks (48 MHz / 1.5 Mbit/s)."""
    from pe import usb, usbdev
    spi = await setup(dut)
    dut.pull_up.value = 0b10  # D- pull-up: J when idle
    rx = load_fw("usb_ls_rx", **usbdev.defines())
    tx = load_fw("usb_ls_tx", **usbdev.defines())
    await spi.apply(host.pin_owner_writes({0: 1, 1: 1}))
    await spi.write(sim.core_reg(0, sim.C_CRCCTRL), [usbdev.CRCCTRL])
    await run_program(spi, 0, rx, clkdiv=1 << 8)
    await run_program(spi, 1, tx, out_pins=(0, 2), clkdiv=1 << 8)
    await ClockCycles(dut.clk, 100)

    async def records():
        st = (await spi.read(sim.core_reg(0, sim.C_STATUS)))[0]
        n = (st >> 4) & 15
        words = await spi.read(sim.core_reg(0, sim.C_FIFO), n, fixed=True) if n else []
        recs, rest = usbdev.parse_records(words)
        assert not rest, words
        return recs

    # SETUP + DATA0 -> ACK
    req = bytes([0x80, 6, 0, 1, 0, 0, 0x40, 0])
    await usb_drive(dut, usb.encode(usb.token(usb.PID_SETUP, 0, 0)))
    await usb_drive(dut, usb.encode(usb.data_packet(usb.PID_DATA0, req)))
    resp, ta = usb_decode_trace(await usb_listen(dut, 1500))
    assert resp == usb.handshake(usb.PID_ACK), resp
    assert ta < 7.5 * 32, ta
    await ClockCycles(dut.clk, 200)
    assert await records() == [usbdev.Record(True, req, 16 + 8 * 10)]
    # IN to another address -> no answer
    await usb_drive(dut, usb.encode(usb.token(usb.PID_IN, 0x15, 0)))
    assert "K" not in await usb_listen(dut, 1500)
    # IN with nothing staged -> NAK
    await usb_drive(dut, usb.encode(usb.token(usb.PID_IN, 0, 0)))
    resp, ta = usb_decode_trace(await usb_listen(dut, 1500))
    assert resp == usb.handshake(usb.PID_NAK), resp
    await ClockCycles(dut.clk, 200)
    # stage DATA1 (first 8 bytes of a device descriptor), IN -> DATA1
    desc = bytes([0x12, 0x01, 0x10, 0x01, 0x00, 0x00, 0x00, 0x08])
    await spi.write(sim.core_reg(1, sim.C_FIFO), usbdev.stage_words(usb.PID_DATA1, desc), fixed=True)
    await spi.write(sim.A_FSET, [usbdev.F_STAGED])
    await usb_drive(dut, usb.encode(usb.token(usb.PID_IN, 0, 0)))
    resp, ta = usb_decode_trace(await usb_listen(dut, 4000))
    assert resp == usb.data_packet(usb.PID_DATA1, desc), resp
    assert ta < 7.5 * 32, ta


@cocotb.test()
async def test_eth10_tx(dut):
    """10BASE-T TX with FIFO join: a 64-byte frame from one 16-word FIFO burst.
    CLKDIV = 1, so 4 clocks per bit (10 Mbit/s at 40 MHz). The decoder is the
    one in test/model/test_eth10.py."""
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "model"))
    import test_eth10 as eth
    spi = await setup(dut)
    c = 0  # pins reset to owner core 0
    await run_program(spi, c, load_fw("eth10_tx", F_GO=eth.F_GO), side_pins=UO(0), clkdiv=1 << 8)
    fr = eth.frame(46)
    await spi.write(sim.core_reg(c, sim.C_R0), [8 * len(fr) - 1])
    await spi.write(sim.core_reg(c, sim.C_FIFO), eth.words(fr), fixed=True)
    st = (await spi.read(sim.core_reg(c, sim.C_STATUS)))[0]
    assert (st & 15, (st >> 4) & 15) == (8, 8), hex(st)   # TXF and RXF (second TX stage) full
    trace = []

    async def sample():
        while True:
            await RisingEdge(dut.clk)
            v = int(dut.uo_out.value) & 3
            assert v != 3
            trace.append(v)
    task = cocotb.start_soon(sample())
    await spi.write(sim.A_FSET, [1 << eth.F_GO])
    for _ in range(100):
        await ClockCycles(dut.clk, 100)
        if not (await spi.read(sim.A_FSET))[0] >> eth.F_GO & 1:
            break
    task.cancel()
    eth.check(trace, 0, fr)


@cocotb.test()
async def test_eth10_rx_jitter(dut):
    """10BASE-T RX at 50 MHz with 802.3's +-13.5 ns of edge jitter (half the
    edges at the extreme) and -200 ppm: Manchester edges at arbitrary times on
    ui0, sampled by the chip at both clock edges, decoded by the line unit's
    tracked-phase receiver and eth10_rx, read back over SPI. Each frame (the
    802.3 preamble + 80 bits) fits the 8-word RX FIFO, so the 1-bit SPI
    driver reads it afterwards."""
    import random
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "model"))
    import test_eth10 as eth
    spi = await setup(dut)
    await run_program(spi, 0, load_fw("eth10_rx"), clkdiv=1 << 8)
    await spi.write(sim.A_LINEDIV, [5 << 16])                  # 5 clocks per bit
    await spi.write(sim.A_LINEPINS, [UI(0) << 8])
    await spi.write(sim.A_LINECFG, [1 | (2 << 1) | (1 << 14)]) # EN, Manchester, RX_EN
    rng = random.Random(3)
    half = 50.0 * (1 - 200e-6)
    for f in range(3):
        bits = [1, 0] * 31 + [1, 1] + [rng.getrandbits(1) for _ in range(80)]
        levels = [v for b in bits for v in (1 - b, b)] + [1, 1, 1, 1]   # then TP_IDL
        t0 = get_sim_time(unit="ns") + rng.uniform(100, 140)
        edges, prev = [], 0
        for k, lv in enumerate(levels):
            if lv != prev:
                j = rng.choice([-13.5, 13.5]) if rng.random() < 0.5 else rng.uniform(-13.5, 13.5)
                edges.append((t0 + k * half + j, lv))
            prev = lv
        edges.append((t0 + len(levels) * half, 0))                       # idle low
        for te, lv in edges:
            dt = round((te - get_sim_time(unit="ns")) * 1000)
            if dt > 0:
                await Timer(dt, unit="ps")
            spi.set_low_inputs(lv)
        await ClockCycles(dut.clk, 100)
        words = []
        for _ in range(3):                         # until the idle fragment is in too
            st = (await spi.read(sim.core_reg(0, sim.C_STATUS)))[0]
            if (st >> 4) & 15:
                words += await spi.read(sim.core_reg(0, sim.C_FIFO), (st >> 4) & 15, fixed=True)
        # the return to idle after TP_IDL arrives as a 1-bit fragment, like a
        # link pulse; software drops fragments (test_eth10's receive helpers do)
        frames = [b for b in eth.rx_bits(words) if len(b) > 64]
        assert frames == [bits], (f, [len(b) for b in eth.rx_bits(words)], words)


@cocotb.test()
async def test_fifo_read_race(dut):
    """A word the core pushes while the host is reading an empty RXF must not be
    lost: the read returns 0 and the word stays for the next read (the SPI port
    pops only if the word it sent was a real entry). The push time is swept
    across the read transfer."""
    spi = await setup(dut)
    prog = asm.assemble("""
    loop:
        jmp r0--, loop
        mov isr, r1
        push block
        halt
    """)
    await spi.apply(host.load_program(0, prog, 0, clkdiv=1 << 8))
    got = []
    for n in range(150, 520, 12):                  # push lands anywhere in the read word
        marker = 0xA000 + n
        await spi.write(sim.core_reg(0, sim.C_R0), [n, marker])
        await spi.write(sim.A_CTRL, [1 | (1 << 8)])    # restart + enable
        got += [w for w in await spi.read(sim.core_reg(0, sim.C_FIFO), 1, fixed=True) if w]
        await ClockCycles(dut.clk, 600)
        while (await spi.read(sim.core_reg(0, sim.C_STATUS)))[0] >> 4 & 15:
            got += await spi.read(sim.core_reg(0, sim.C_FIFO), 1, fixed=True)
        assert got and got[-1] == marker, (hex(marker), [hex(g) for g in got[-3:]])
    assert got == [0xA000 + n for n in range(150, 520, 12)]



class TimedSpi(Spi):
    """SPI master on its own time base: SCK period/duty in ns, a phase offset
    against clk, CS setup/hold/gap, and an optional abort after `abort_bits`."""

    def __init__(self, dut, period, duty=0.5, phase=0.0, cs_setup=None, cs_hold=None, cs_gap=None):
        super().__init__(dut)
        self.hi, self.lo = period * duty, period * (1 - duty)
        self.phase = phase
        self.cs_setup = cs_setup if cs_setup is not None else self.lo
        self.cs_hold = cs_hold if cs_hold is not None else self.lo
        self.cs_gap = cs_gap if cs_gap is not None else period
        self.abort_bits = None

    @staticmethod
    async def wait(ns):
        if round(ns * 1000) > 0:
            await Timer(round(ns * 1000), unit="ps")

    async def xfer(self, data):
        out = bytearray()
        await self.wait(self.phase)
        self._set(self.CS, 0)
        await self.wait(self.cs_setup)
        nbits = 0
        for byte in data:
            r = 0
            for i in range(7, -1, -1):
                if self.abort_bits is not None and nbits == self.abort_bits:
                    break
                self._set(self.MOSI, (byte >> i) & 1)
                await self.wait(self.lo)
                r = (r << 1) | ((int(self.dut.uo_out.value) >> 7) & 1)
                self._set(self.SCK, 1)
                await self.wait(self.hi)
                self._set(self.SCK, 0)
                nbits += 1
            out.append(r)
        await self.wait(self.cs_hold)
        self._set(self.CS, 1)
        await self.wait(self.cs_gap)
        return bytes(out)


@cocotb.test()
async def test_spi_port_timing(dut):
    """Host port contract (docs/isa.md §6.1): SCK period >= 8 clk with high and
    low >= 3 clk, CS_n setup/hold >= 3 clk, CS_n high >= 4 clk between
    transactions, at any phase against clk. Swept at the limits and at random
    points inside them: register writes read back, FIFO pushes count exactly,
    and a FIFO read aborted mid-word pops nothing (the next full read gets it)."""
    import random
    rng = random.Random(7)
    await setup(dut)
    T = CLK_NS
    prog = asm.assemble("""
        mov isr, r1
        push block
        halt
    """)
    await Spi(dut).apply(host.load_program(0, prog, 0, clkdiv=1 << 8))
    cases = [dict(period=8 * T, duty=3 / 8), dict(period=8 * T, duty=5 / 8),
             dict(period=8 * T, cs_setup=3 * T, cs_hold=3 * T, cs_gap=4 * T)]
    cases += [dict(period=rng.uniform(8, 20) * T, duty=rng.uniform(0.38, 0.62),
                   cs_setup=rng.uniform(3, 6) * T, cs_hold=rng.uniform(3, 6) * T, cs_gap=rng.uniform(4, 8) * T)
              for _ in range(6)]
    fifo, status = sim.core_reg(0, sim.C_FIFO), sim.core_reg(0, sim.C_STATUS)
    for n, kw in enumerate(cases):
        spi = TimedSpi(dut, phase=rng.uniform(0, T), **kw)
        regs = [rng.getrandbits(16) for _ in range(4)]
        await spi.write(sim.core_reg(0, sim.C_R0), regs)
        assert await spi.read(sim.core_reg(0, sim.C_R0), 4) == regs, (n, kw)
        await spi.write(sim.A_CTRL, [1 << 16])                     # clear core 0's FIFOs
        words = [rng.getrandbits(32) for _ in range(3)]
        await spi.write(fifo, words, fixed=True)
        assert (await spi.read(status))[0] & 15 == 3, (n, kw)
        # the core pushes R1 into its RXF; a read aborted mid-word must not pop it
        await spi.write(sim.A_CTRL, [1 | (1 << 8)])                # restart + enable core 0
        await ClockCycles(dut.clk, 20)
        assert (await spi.read(status))[0] >> 4 & 15 == 1, (n, kw)
        spi.abort_bits = 24 + 16                                  # cmd + addr + half a word
        await spi.read(fifo, fixed=True)
        spi.abort_bits = None
        assert (await spi.read(status))[0] >> 4 & 15 == 1, (n, kw)
        assert await spi.read(fifo, fixed=True) == [regs[1]], (n, kw)
        assert (await spi.read(status))[0] >> 4 & 15 == 0, (n, kw)
        await spi.write(sim.A_CTRL, [0])


class QuadSpi(TimedSpi):
    """TimedSpi with quad commands: the command byte on IO3 (ui[5]), then the
    address and data on IO[3:0] = ui[5:2] in, uo[7:4] out, 4 bits per SCK."""
    IO = 2      # ui[5:2]

    async def qxfer(self, cmd, addr, words=(), nread=0):
        out = []
        await self.wait(self.phase)
        self._set(self.CS, 0)
        await self.wait(self.cs_setup)

        async def clock(nib):
            self.ui = (self.ui & ~(0xF << self.IO)) | ((nib & 0xF) << self.IO)
            self.dut.ui_in.value = self.ui
            await self.wait(self.lo)
            r = (int(self.dut.uo_out.value) >> 4) & 0xF
            self._set(self.SCK, 1)
            await self.wait(self.hi)
            self._set(self.SCK, 0)
            return r
        for i in range(7, -1, -1):                      # command: 1-bit on IO3
            await clock(((cmd >> i) & 1) << 3)
        for i in range(3, -1, -1):
            await clock(addr >> (4 * i))
        for w in words:
            for i in range(7, -1, -1):
                await clock(w >> (4 * i))
        if nread:
            for _ in range(8):                          # dummy
                await clock(0)
            for _ in range(nread):
                w = 0
                for _ in range(8):
                    w = (w << 4) | await clock(0)
                out.append(w)
        await self.wait(self.cs_hold)
        self._set(self.CS, 1)
        await self.wait(self.cs_gap)
        return out


@cocotb.test()
async def test_host_port_fast(dut):
    """The host port at SCK = clk and clk/2, 1-bit and quad, at random phases:
    register round trips, FIFO bursts both ways, an aborted read losing
    nothing, quad commands ignored until HOSTCFG.QUAD_EN, uo[6:4] taken only
    during quad read data, and the throughput."""
    import random
    rng = random.Random(11)
    await setup(dut)
    T = CLK_NS
    prog = asm.assemble("""
    loop:
        pull block
        mov isr, osr
        push block
        jmp loop
    """)                                                  # echo: TX FIFO -> RX FIFO
    await Spi(dut).apply(host.load_program(0, prog, 0, clkdiv=1 << 8))
    await Spi(dut).write(sim.A_CTRL, [1 | (1 << 8)])
    fifo = sim.core_reg(0, sim.C_FIFO)
    for period in (T, 2 * T):
        spi = QuadSpi(dut, period=period, phase=rng.uniform(0, T), cs_setup=T, cs_hold=T, cs_gap=4 * T)
        regs = [rng.getrandbits(16) for _ in range(4)]
        await spi.write(sim.core_reg(1, sim.C_R0), regs)
        assert await spi.read(sim.core_reg(1, sim.C_R0), 4) == regs, period
        words = [rng.getrandbits(32) for _ in range(8)]
        await spi.write(fifo, words, fixed=True)        # echoed back by core 0
        await ClockCycles(dut.clk, 50)
        assert await spi.read(fifo, 8, fixed=True) == words, period
        # quad: ignored while QUAD_EN = 0, then works
        assert await spi.qxfer(0x43, sim.A_ID, nread=1) == [0]
        await spi.write(sim.A_HOSTCFG, [1])
        assert await spi.qxfer(0x43, sim.A_ID, nread=1) == [sim.ID_VALUE]
        await spi.qxfer(0x42, sim.core_reg(1, sim.C_R0), regs[::-1])
        assert await spi.qxfer(0x43, sim.core_reg(1, sim.C_R0), nread=4) == regs[::-1], period
        await spi.qxfer(0x4A, fifo, words)
        await ClockCycles(dut.clk, 50)
        # abort a quad FIFO read after two words: nothing beyond them is lost
        abort = QuadSpi(dut, period=period, cs_setup=T, cs_hold=T, cs_gap=4 * T)
        assert await abort.qxfer(0x4B, fifo, nread=2) == words[:2]
        assert await spi.qxfer(0x4B, fifo, nread=6) == words[2:], period
        await spi.write(sim.A_HOSTCFG, [0])
    # uo[6:4] belong to the cores except during quad read data
    await Spi(dut).write(sim.A_POUT, [0x7F00])          # uo[6:0] high
    assert (int(dut.uo_out.value) >> 4) & 7 == 7
    # throughput: 64 words pushed with one quad WRITE_FIXED at SCK = clk
    spi = QuadSpi(dut, period=T, cs_setup=T, cs_hold=T, cs_gap=4 * T)
    await spi.write(sim.A_HOSTCFG, [1])
    await spi.write(sim.A_CTRL, [0])                    # stop the echo
    t0 = get_sim_time(unit="ns")
    await spi.qxfer(0x4A, sim.core_reg(1, sim.C_R0), [rng.getrandbits(32) for _ in range(64)])
    ns = get_sim_time(unit="ns") - t0
    dut._log.info(f"quad write: {64 * 32 / ns * 1e3:.0f} Mbit/s at SCK = clk ({1e3 / T:.0f} MHz)")
    assert 64 * 32 / ns * 1e3 > 150
