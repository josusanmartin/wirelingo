"""Top-level tests: everything goes through the real SPI host port and the pins.

Each protocol test loads firmware from firmware/*.pasm with the host library,
then talks to a Python device model that sees only the pins.
"""
import os
import pathlib

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, RisingEdge

from pe import asm, host, sim

FW = pathlib.Path(__file__).resolve().parent.parent / "firmware"
BANK_AW = int(os.environ.get("PE_BANK_AW", "4"))
NCORES = int(os.environ.get("PE_NCORES", "4"))
core = lambda i: i % NCORES  # tests spread over the cores that exist
CLK_NS = 20  # 50 MHz
SCK_HALF = 4  # SPI SCK half period in clk cycles (SCK = clk/8)

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
        r = r[3:]
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
    assert st & 7 == 3, hex(st)


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
            if (st & 7) == 0:
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
        n = (st >> 4) & 7
        if n:
            got += await spi.read(sim.core_reg(core(1), sim.C_FIFO), n, fixed=True)
    await ClockCycles(dut.clk, 3 * cpb)
    st = (await spi.read(sim.core_reg(core(1), sim.C_STATUS)))[0]
    n = (st >> 4) & 7
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
        if (st >> 4) & 7 == len(tx):
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
                if st & 7 < 4:
                    break
            await spi.write(fifo, [w], fixed=True)
        return replies

    async def drain(n):
        out = []
        for _ in range(400):
            st = (await spi.read(sim.core_reg(core(3), sim.C_STATUS)))[0]
            k = (st >> 4) & 7
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

USB_DEFS = {"DP": 0, "DM": 1, "F_IN": 0, "F_ACK": 1, "F_EXP": 2}
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
    """USB low-speed device side in firmware: SETUP+DATA0 -> ACK, IN -> NAK, IN -> staged DATA1.
    Two cores (RX on core 0, TX on core 1); bit time = 32 clocks (48 MHz / 1.5 Mbit/s)."""
    from pe import usb
    spi = await setup(dut)
    dut.pull_up.value = 0b10  # D- pull-up: J when idle
    rx = load_fw("usb_ls_rx", **USB_DEFS)
    tx = load_fw("usb_ls_tx", **USB_DEFS)
    await spi.apply(host.pin_owner_writes({0: 1, 1: 1}))
    await run_program(spi, 0, rx, clkdiv=1 << 8)
    await run_program(spi, 1, tx, out_pins=(0, 2), clkdiv=1 << 8)
    await ClockCycles(dut.clk, 100)

    async def drain():
        st = (await spi.read(sim.core_reg(0, sim.C_STATUS)))[0]
        n = (st >> 4) & 7
        if n:
            await spi.read(sim.core_reg(0, sim.C_FIFO), n, fixed=True)

    # SETUP + DATA0 -> ACK
    await usb_drive(dut, usb.encode(usb.token(usb.PID_SETUP, 0, 0)))
    await drain()
    await usb_drive(dut, usb.encode(usb.data_packet(usb.PID_DATA0, bytes([0x80, 6, 0, 1, 0, 0, 0x40, 0]))))
    resp, ta = usb_decode_trace(await usb_listen(dut, 1500))
    assert resp == usb.handshake(usb.PID_ACK), resp
    assert ta < 7.5 * 32, ta
    await ClockCycles(dut.clk, 200)
    await drain()
    # IN with nothing staged -> NAK
    await usb_drive(dut, usb.encode(usb.token(usb.PID_IN, 0, 0)))
    resp, ta = usb_decode_trace(await usb_listen(dut, 1500))
    assert resp == usb.handshake(usb.PID_NAK), resp
    await ClockCycles(dut.clk, 200)
    await drain()
    # stage DATA1 (first 8 bytes of a device descriptor), IN -> DATA1
    desc = bytes([0x12, 0x01, 0x10, 0x01, 0x00, 0x00, 0x00, 0x08])
    pkt = bytes([0x80]) + usb.data_packet(usb.PID_DATA1, desc)
    words = [int.from_bytes(pkt[i:i + 4].ljust(4, b"\0"), "little") for i in range(0, len(pkt), 4)]
    await spi.write(sim.core_reg(1, sim.C_FIFO), [8 * len(pkt)] + words, fixed=True)
    await usb_drive(dut, usb.encode(usb.token(usb.PID_IN, 0, 0)))
    resp, ta = usb_decode_trace(await usb_listen(dut, 4000))
    assert resp == usb.data_packet(usb.PID_DATA1, desc), resp
    assert ta < 7.5 * 32, ta
