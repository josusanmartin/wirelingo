"""Top-level tests: everything goes through the real SPI host port and the pins.

Each protocol test loads firmware from firmware/*.pasm with the host library,
then talks to a Python device model that sees only the pins.
"""
import os
import pathlib
import importlib.util

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, RisingEdge, Timer
from cocotb.utils import get_sim_time
from cocotb.task import bridge, resume

from pe import asm, host, sim
from pe.profile import Profile

FW = pathlib.Path(__file__).resolve().parent.parent / "firmware"
BANK_AW = int(os.environ.get("PE_BANK_AW", "4"))
NCORES = int(os.environ.get("PE_NCORES", "4"))
core = lambda i: i % NCORES  # tests spread over the cores that exist
PROFILE = Profile("simulation", NCORES, int(os.environ.get("PE_NBANKS", str(NCORES))), 1 << BANK_AW)
CLK_NS = float(os.environ.get("PE_CLK_NS", "20"))  # 50 MHz (flow/gl_sdf_test.sh varies it)
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


async def setup(dut, clock_ns=CLK_NS):
    cocotb.start_soon(Clock(dut.clk, clock_ns, unit="ns").start())
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
    await spi.apply(host.load_program(core, prog, base, profile=PROFILE, **cfg))
    ctrl = (await spi.read(sim.A_CTRL))[0]
    await spi.apply(host.start_writes(1 << core, ctrl & 15))


# ------------------------------------------------------------------ tests

@cocotb.test()
async def test_generated_uart_virtual_loopback(dut):
    """Run the board's generated programs through SPI with two concurrent cores.
    Core 0 TX reaches core 1 RX on virtual pin 24, without external wiring.
    """
    assert NCORES >= 2, "the board loopback requires two cores"
    spi = await setup(dut, clock_ns=20.834)  # 48 MHz within simulator precision
    path = FW.parent / "tools/micropython/protoemu_fw.py"
    spec = importlib.util.spec_from_file_location("board_firmware", path)
    bundle = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bundle)
    await spi.write(sim.A_CTRL, [3 << 16])
    await spi.apply(host.pin_owner_writes({24: 0}))
    for image in (bundle.UART_RX_V24, bundle.UART_TX_V24):
        await spi.write(image["base"], image["words"])
        settings = [image[k] for k in ("clkdiv", "pinctrl0", "pinctrl1", "shiftctrl", "base", "wrap")]
        await spi.write(sim.core_reg(image["core"], sim.C_CLKDIV), settings)
    await spi.write(sim.A_CTRL, [3 | (3 << 8)])
    payload = bytes(range(32)) + b"protoemu: two cores\x00\xff"
    sent, got, stalled = 0, bytearray(), False
    begin = get_sim_time(unit="ns")
    for _ in range(1000):
        status = (await spi.read(sim.core_reg(0, sim.C_STATUS)))[0]
        assert not status & ((1 << 13) | (1 << 14)), "TX halted/faulted (ISA STATUS)"
        n = min(4, (1 << int(os.environ.get("PE_FIFO_LOG2", "3"))) - (status & 15), len(payload) - sent)
        if n:
            await spi.write(sim.core_reg(0, sim.C_FIFO), list(payload[sent:sent+n]), fixed=True)
            sent += n
        status = (await spi.read(sim.core_reg(1, sim.C_STATUS)))[0]
        assert not status & ((1 << 13) | (1 << 14)), "RX halted/faulted (ISA STATUS)"
        n = (status >> 4) & 15
        if n:
            got.extend(w & 255 for w in await spi.read(sim.core_reg(1, sim.C_FIFO), n, fixed=True))
        if sent >= 16 and not stalled:
            await ClockCycles(dut.clk, 9000)  # 187.5 us host stall, within RX FIFO capacity
            stalled = True
        if len(got) == len(payload):
            break
    assert bytes(got) == payload
    assert not int(dut.uio_oe.value), "virtual loopback unexpectedly drives external pins"
    elapsed = get_sim_time(unit="ns") - begin
    dut._log.info("generated UART loopback: %d bytes, %.3f ms simulated, TX/RX %d/%d words, 2 cores, 187.5 us service stall",
                  len(payload), elapsed / 1e6, len(bundle.UART_TX_V24["words"]), len(bundle.UART_RX_V24["words"]))

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
        self.task = cocotb.start_soon(self.run())

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


@cocotb.test()
async def test_spi_master_fast(dut):
    """SPI master at 4 clocks per SCK (12.5 MHz at 50 MHz) against the same
    device model as test_spi_master, which answers one clock after it sees SCK
    fall. spi_master_fast with MISO's pad in SYNC_BYPASS (docs/isa.md §2)
    reads every byte; without the bypass, or with spi_master at CLKDIV 1 even
    with it, every MISO bit is taken one bit late (MOSI is always right)."""
    spi = await setup(dut)
    SCK, MOSI, MISO = UIO(2), UIO(3), UIO(4)
    c = core(2)
    await spi.apply(host.pin_owner_writes({SCK: c, MOSI: c, MISO: c}))
    await run_program(spi, c, asm.assemble(f"pin {SCK}, lo\npin {MOSI}, lo\nhalt"))
    await ClockCycles(dut.clk, 20)
    tx, resp = [0xA5, 0x0F, 0xFF, 0x00, 0x3C], [0xC3, 0x5A, 0x01, 0x80, 0xE7]

    async def transfer(fw, bypass):
        await spi.write(sim.A_CTRL, [1 << (16 + c)])                 # stop the core, clear its FIFOs
        await spi.write(sim.A_SYNCBYP, [(1 << MISO) if bypass else 0])
        await spi.write(sim.core_reg(c, sim.C_FIFO), [b << 24 for b in tx], fixed=True)   # queued: back to back
        slave = SpiSlave(dut, SCK, MOSI, MISO, resp)
        rises = []

        async def sck_monitor():
            prev = 0
            while True:
                await RisingEdge(dut.clk)
                s = (int(dut.uio_out.value) >> SCK) & 1
                if s and not prev:
                    rises.append(get_sim_time(unit="ns"))
                prev = s
        mon = cocotb.start_soon(sck_monitor())
        await run_program(spi, c, load_fw(fw), out_pins=(MOSI, 1), side_pins=SCK, in_pins=MISO, clkdiv=1 << 8)
        for _ in range(100):
            if ((await spi.read(sim.core_reg(c, sim.C_STATUS)))[0] >> 4) & 15 == len(tx):
                break
        got = [g & 0xFF for g in await spi.read(sim.core_reg(c, sim.C_FIFO), len(tx), fixed=True)]
        slave.task.cancel()
        mon.cancel()
        clocks = {round((b - a) / CLK_NS) for a, b in zip(rises, rises[1:])}
        return list(slave.rx), got, clocks

    rx, got, clocks = await transfer("spi_master_fast", True)
    assert rx == tx and got == resp, (rx, got)
    assert clocks == {4}, clocks                                   # 12.5 MHz SCK, back to back
    dut._log.info(f"spi_master_fast, CLKDIV 1, MISO in SYNC_BYPASS: {len(tx)} bytes at "
                  f"{sorted(clocks)} clocks per SCK, read {[hex(b) for b in got]}")
    for fw, byp in (("spi_master_fast", False), ("spi_master", True)):
        rx, got, clocks = await transfer(fw, byp)
        assert rx == tx and clocks == {4}, (fw, byp, rx, clocks)
        late = [((resp[i - 1] & 1 if i else resp[0] >> 7) << 7) | (resp[i] >> 1) for i in range(len(resp))]
        assert got == late, (fw, byp, [hex(b) for b in got])         # every MISO bit one bit late
        dut._log.info(f"{fw}, CLKDIV 1, bypass {int(byp)}: read {[hex(b) for b in got]} (one bit late)")
    await spi.write(sim.A_SYNCBYP, [0])


class I2cEeprom:
    """I2C target at 7-bit address 0x50 with a 256-byte memory and a register pointer.
    Watches the resolved bus every clock; drives SDA/SCL low via ext_oe/ext_val.
    Optionally stretches SCL after each ACK."""

    def __init__(self, dut, sda, scl, addr=0x50, stretch=0):
        self.dut, self.sda, self.scl, self.addr, self.stretch = dut, sda, scl, addr, stretch
        self.mem = bytearray(range(256))
        self.ptr = 0
        self.log = []
        self.task = cocotb.start_soon(self.run())

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


# ------------------------------------------------------------------ reverse engineering

class OdBus:
    """External open-drain devices sharing uio lines: a line is low while any
    of them pulls it (the testbench's ext_oe/ext_val are one driver)."""

    def __init__(self, pins):
        self.pins, self.low = pins, {}

    def set(self, who, mask):
        self.low[who] = mask
        lows = 0
        for m in self.low.values():
            lows |= m
        for p in self.pins:
            EXT.set(p, (lows >> p) & 1, 0)


class BusEeprom(I2cEeprom):
    """The I2cEeprom model on a shared OdBus (removable: task.cancel + release)."""

    def __init__(self, dut, bus, sda, scl, **kw):
        self.bus, self.lowmask = bus, 0
        super().__init__(dut, sda, scl, **kw)

    def _pull(self, pin, low):
        self.lowmask = (self.lowmask & ~(1 << pin)) | (int(low) << pin)
        self.bus.set("eeprom", self.lowmask)

    def remove(self):
        self.task.cancel()
        self.bus.set("eeprom", 0)


async def i2c_session(dut, spi, bus, ops, device=None, restart=0, half=250, sda=0, scl=1):
    """A bit-banged I2C master (pe.i2cbus, 100 kHz at half = 250 clk) runs `ops`
    while the host restarts core 0's capture and keeps draining it with blind
    8-word READ_FIXED bursts; with a device model it also serves core 1's
    i2c_device events. Returns (master, capture words)."""
    from pe import i2cbus
    m = i2cbus.Master(lambda: int(dut.uio_in.value), sda, scl, half)

    async def master():
        for n in m.run(ops):
            bus.set("master", m.low)
            await ClockCycles(dut.clk, n)
        bus.set("master", 0)
    en = 0b11 if device else 0b01
    await spi.write(sim.A_CTRL, [en | ((1 | restart) << 8) | ((1 | restart) << 16)])
    task = cocotb.start_soon(master())
    fifo0, fifo1 = sim.core_reg(0, sim.C_FIFO), sim.core_reg(1, sim.C_FIFO)
    words = []
    while not task.done():
        words += [w for w in await spi.read(fifo0, 8, fixed=True) if w]
        if device:
            for ev in [w for w in await spi.read(fifo1, 2, fixed=True) if w]:
                for b in device.handle(ev):
                    await spi.write(fifo1, [b], fixed=True)
    await ClockCycles(dut.clk, 100)
    words += [w for w in await spi.read(fifo0, 8, fixed=True) if w]
    return m, words


@cocotb.test()
async def test_reverse_engineering(dut):
    """Capture -> understand -> impersonate (docs/reverse-engineering.md), all
    through the SPI port at SCK = clk/2. A 100 kHz master talks to the
    I2cEeprom model while core 0 captures SDA/SCL (la_capture); pe.la decodes
    the transcript and the register image; the EEPROM is removed and core 1
    (i2c_device) serves that image at the decoded address: the master gets the
    same answers and core 0's capture of the clone decodes the same. Then a man
    in the middle changes one register on its way to the master."""
    import random
    from pe import i2cdev, la
    if NCORES < 2:
        dut._log.warning("needs two cores")
        return
    await setup(dut)
    T = CLK_NS
    spi = TimedSpi(dut, period=2 * T, cs_setup=T, cs_hold=T, cs_gap=4 * T)
    SDA, SCL = UIO(0), UIO(1)
    dut.pull_up.value = 0b11
    await spi.write(sim.A_POUT, [0b11])                 # released
    await spi.write(sim.A_OD, [0b11])
    await spi.apply(host.pin_owner_writes({SDA: 1, SCL: 1}, profile=PROFILE))
    await spi.apply(host.load_program(0, load_fw("la_capture", MASK=0b11), 0, profile=PROFILE, in_pins=0,
                                      clkdiv=1 << 8))         # channels SDA, SCL
    bus = OdBus([SDA, SCL])
    rng = random.Random(5)
    rom = bytes(rng.getrandbits(8) for _ in range(256))
    ops = [("wr", 0x50, [0x10], 8), ("w", 0x50, [0x20, 0xCA, 0xFE]), ("wr", 0x50, [0x20], 2), ("w", 0x51, [0])]
    hexes = lambda bs: " ".join(f"{b:02X}" for b in bs)
    transcript = ["write to 0x50: 10", "read 8 bytes from 0x50: " + hexes(rom[0x10:0x18]),
                  "write to 0x50: 20 CA FE", "write to 0x50: 20", "read 2 bytes from 0x50: CA FE",
                  "write to 0x51: address NACKed"]

    def decode(words):
        cap = la.decode_records(words, 0b11)
        assert cap.exact, f"capture has gaps at {cap.gaps}"
        ev = la.i2c_events(cap, SDA, SCL)
        return cap, ev, la.i2c_summary(ev)

    # 1. capture: master <-> EEPROM model
    ee = BusEeprom(dut, bus, SDA, SCL)
    ee.mem[:] = rom
    m, words = await i2c_session(dut, spi, bus, ops)
    ee.remove()
    answers = m.results
    assert answers[0] == list(rom[0x10:0x18]) and answers[2] == [0xCA, 0xFE] and answers[3] is None, answers
    # 2. understand
    cap, ev, text = decode(words)
    dut._log.info(f"captured {len(words)} records over {cap.end * T / 1000:.0f} us:\n  " + "\n  ".join(text))
    assert text == transcript, text
    image = la.i2c_register_image(ev)
    assert list(image) == [0x50] and image[0x50] == {**{r: rom[r] for r in range(0x10, 0x18)}, 0x20: 0xCA, 0x21: 0xFE}
    # 3. impersonate: core 1 at the decoded address, serving the decoded image
    addr = list(image)[0]
    dev = i2cdev.Device(addr, mem=image[addr])
    await spi.apply(dev.setup_writes(1, load_fw("i2c_device", SDA=SDA, SCL=SCL), SDA, SCL, 256, profile=PROFILE))
    m2, words2 = await i2c_session(dut, spi, bus, ops, device=dev, restart=0b10)
    assert m2.results == answers, m2.results
    assert decode(words2)[2] == transcript
    dut._log.info(f"clone: same answers and transcript; SCL stretched {m2.stretched} clocks waiting for the host")
    assert m2.stretched > 0
    # man in the middle: register 0x12 changed on its way to the master
    mitm = i2cdev.Device(addr, mem=image[addr], on_read=lambda reg, v: 0x00 if reg == 0x12 else v)
    m3, words3 = await i2c_session(dut, spi, bus, ops[:1], device=mitm)
    want = list(rom[0x10:0x18])
    want[2] = 0
    assert m3.results == [want], m3.results
    assert decode(words3)[2][1] == "read 8 bytes from 0x50: " + hexes(want)


@cocotb.test()
async def test_la_capture_rate(dut):
    """la_capture's sustained edge rate through the host port: 300 edges of a
    square wave on one channel, drained with blind READ_FIXED bursts of 8 words,
    must arrive complete with no gap marker. 1-bit SPI at SCK = clk/2 and quad
    at SCK = clk (8- and 32-word bursts); logs the fastest gap-free wave of each
    sweep (each sweep stops at its first failure)."""
    from pe import la
    await setup(dut)
    T = CLK_NS
    one = TimedSpi(dut, period=2 * T, cs_setup=T, cs_hold=T, cs_gap=4 * T)
    quad = QuadSpi(dut, period=T, cs_setup=T, cs_hold=T, cs_gap=4 * T)
    await one.apply(host.load_program(0, load_fw("la_capture", MASK=0b100), 0, profile=PROFILE, in_pins=0,
                                      clkdiv=1 << 8))         # channel 2 only
    await one.write(sim.A_HOSTCFG, [1])
    fifo = sim.core_reg(0, sim.C_FIFO)
    EXT.set(2, True, 0)
    N = 300

    async def run(drain, half):
        async def wave():
            for i in range(N):
                await ClockCycles(dut.clk, half)
                EXT.set(2, True, (i + 1) & 1)
        await one.write(sim.A_CTRL, [1 | (1 << 8) | (1 << 16)])
        await ClockCycles(dut.clk, 20)
        task = cocotb.start_soon(wave())
        words = []
        while not task.done():
            words += [w for w in await drain() if w]
        await ClockCycles(dut.clk, 50)
        words += [w for w in await drain() if w]
        await one.write(sim.A_CTRL, [0])
        cap = la.decode_records(words, 0b100)
        return cap.exact and len(cap.edges) == N + 1

    modes = {"1-bit SPI, SCK = clk/2": (lambda: one.read(fifo, 8, fixed=True), (120, 90, 80, 70)),
             "quad SPI, SCK = clk": (lambda: quad.qxfer(0x4B, fifo, nread=8), (24, 14, 13, 12, 11)),
             "quad SPI, SCK = clk, 32-word bursts": (lambda: quad.qxfer(0x4B, fifo, nread=32), (12, 11, 10, 9, 8, 7))}
    best = {}
    for name, (drain, halves) in modes.items():
        for half in halves:
            if not await run(drain, half):
                break
            best[name] = half
        assert name in best, f"{name}: gaps even at an edge every {halves[0]} clocks"
        dut._log.info(f"{name}: gap-free down to an edge every {best[name]} clocks "
                      f"({1e3 / (best[name] * T):.2f} M edges/s at {1e3 / T:.0f} MHz)")
    # The firmware records an edge per 7 ticks (test_capture_close_edges); the
    # port is the limit here: 8-word quad bursts drain a word per ~11 clocks
    # (g4's polling firmware stopped at 12 itself), 32-word bursts one per ~8.8
    assert best["1-bit SPI, SCK = clk/2"] <= 90 and best["quad SPI, SCK = clk"] == 12, best
    assert best["quad SPI, SCK = clk, 32-word bursts"] == 9, best


@cocotb.test()
async def test_la_capture_ticks(dut):
    """WAIT kind 7 (`wait pins != r0 mask m`) at 1-tick resolution, short and
    directed (for the SDF gate-level set, flow/gl_sdf_test.sh; ~0.2 ms):
    la_capture at CLKDIV 1 on three channels while the testbench changes them
    between clock edges at known clocks: edges 7 to 30 ticks apart, the
    7-tick square wave, pairs on two channels 1 to 6 ticks apart, 1- and
    2-clock pulses; drained between batches of at most 7 records. The decoded
    edges and pulses, relative to the first edge, must equal the cycle
    model's for the same stimulus, and edges >= 7 ticks apart must be
    timestamped exactly (their drive-time differences)."""
    from pe import la
    from pe.modelhost import ModelHost
    await setup(dut)
    T = CLK_NS
    spi = TimedSpi(dut, period=2 * T, cs_setup=T, cs_hold=T, cs_gap=4 * T)
    MASK = 0b111
    prog = load_fw("la_capture", MASK=MASK)
    await spi.apply(host.load_program(0, prog, 0, profile=PROFILE, in_pins=0, clkdiv=1 << 8))
    for p in range(3):
        EXT.set(p, True, 0)
    fifo = sim.core_reg(0, sim.C_FIFO)
    cyc = [0]

    async def count():
        while True:
            await RisingEdge(dut.clk)
            cyc[0] += 1
    counter = cocotb.start_soon(count())
    state, log, words = 0, [], []

    async def drive(events):                       # (clocks after the previous change, channels to flip)
        nonlocal state
        for d, flip in events:
            await ClockCycles(dut.clk, d)
            await FallingEdge(dut.clk)             # mid-cycle: no race with the input synchronizers
            state ^= flip
            for p in range(3):
                EXT.set(p, True, (state >> p) & 1)
            log.append((cyc[0], state))

    async def drain():
        await ClockCycles(dut.clk, 60)
        for _ in range(2):
            words.extend(w for w in await spi.read(fifo, 8, fixed=True) if w)

    await spi.write(sim.A_CTRL, [1 | (1 << 8) | (1 << 16)])
    await drain()                                  # the first record
    spaced = [(5, 1), (7, 2), (8, 4), (9, 1), (12, 2), (7, 4), (30, 3)]
    batches = [spaced, [(5, 1)] + [(7, 1)] * 6]    # 7-tick square wave on channel 0
    for ds in ((1, 2, 3), (4, 5, 6)):              # channel 0, then channel 1 d ticks later
        batches.append([e for d in ds for e in ((40, 1), (d, 2))])
    batches.append([(40, 4), (1, 4), (40, 4), (2, 4), (40, 3), (1, 3)])   # 1-, 2- and 1-clock pulses
    for b in batches:
        await drive(b)
        await drain()
    counter.cancel()
    await spi.write(sim.A_CTRL, [0])

    # the cycle model, same stimulus at the same relative clocks
    mh = ModelHost()
    mh.apply(host.load_program(0, prog, 0, profile=PROFILE, in_pins=0, clkdiv=1 << 8))
    mh.ext_oe = 0b111
    mh.start(1)
    mh.step(30)
    mwords = []
    t = log[0][0]
    for c, s in log + [(log[-1][0] + 200, log[-1][1])]:
        while t < c:
            mh.step()
            mwords += mh.chip.cores[0].rxf
            mh.chip.cores[0].rxf.clear()
            t += 1
        mh.uio = s

    def rel(cap):
        e = cap.edges[1:]
        return [(t - e[0][0], s) for t, s in e], [t - e[0][0] for t in cap.pulses]
    cap, mcap = la.decode_records(words, MASK), la.decode_records(mwords, MASK)
    assert cap.exact and mcap.exact, (cap.gaps, mcap.gaps)
    got, want = rel(cap), rel(mcap)
    dut._log.info(f"{len(log)} pin changes -> {len(got[0])} edges, {len(got[1])} pulses; "
                  f"edges (tick, state) relative to the first: {got[0]}; pulses {got[1]}")
    assert got == want, (got, want)
    n = len(spaced)
    assert [t for t, _ in got[0][:n]] == [c - log[0][0] for c, _ in log[:n]], got[0][:n]
    assert [s for _, s in got[0][:n]] == [s for _, s in log[:n]]
    assert len(got[1]) == 2                        # the two 1-clock pulses


CALL_RET_SRC = """
.side_set 1 opt
.shift_in left autopush 32
    ldi r0, 6
.wrap_target
top:
    call !r1, leaf [1]          ; taken when r1 == 0 (every other pass)
    call outer side 1           ; nested: outer saves LR, calls leaf
    jmp r0--, more              ; r0 counts the passes
    halt
more:
    call leaf side 0            ; the wrap's last word: links to top
.wrap
outer:
    mov r3, lr
    call leaf [2]
    mov r2, lr                  ; leaf's link
    mov lr, r3
    in r1, 8
    in r0, 8
    in r2, 8
    in r3, 8                    ; autopush: r1, r0, leaf's link, outer's link
    ret side 0
leaf:
    add r1, 1
    and r1, 3
    ret side 1 [1]
"""


@cocotb.test()
async def test_call_ret(dut):
    """CALL / RET at the top level, short and directed (for the SDF gate-level
    set, flow/gl_sdf_test.sh; ~0.1 ms): conditional calls taken and not taken,
    nesting through a saved LR, a CALL on the wrap's last word, RET with
    side-set and delay, at CLKDIV 1. The side-set pin's edges (clock-exact
    intervals), the pushed words (r1, r0 and both links), and PC, status and
    R0-R3 at the halt must equal the cycle model's."""
    from pe.modelhost import ModelHost
    spi = await setup(dut)
    prog = asm.assemble(CALL_RET_SRC)
    PIN = UO(0)
    mh = ModelHost()
    mh.apply(host.load_program(0, prog, 0, profile=PROFILE, side_pins=PIN, clkdiv=1 << 8))
    mh.start(1)
    medges, prev = [], 0
    for cyc in range(400):
        mh.step()
        v = mh.chip.outputs()[0] & 1
        if v != prev:
            medges.append(cyc)
            prev = v
    assert mh.chip.cores[0].halted
    mwords = mh.pop_all(0)
    mregs = [mh.chip.cores[0].read_reg(sim.C_PC), mh.chip.cores[0].read_reg(sim.C_STATUS)] + mh.chip.cores[0].r[:4]

    edges = []

    async def monitor():
        prev, cyc = int(dut.uo_out.value) & 1, 0
        while True:
            await RisingEdge(dut.clk)
            cyc += 1
            v = int(dut.uo_out.value) & 1
            if v != prev:
                edges.append(cyc)
                prev = v
    mon = cocotb.start_soon(monitor())
    await run_program(spi, 0, prog, side_pins=PIN, clkdiv=1 << 8)
    await ClockCycles(dut.clk, 400)
    mon.cancel()
    words = await spi.read(sim.core_reg(0, sim.C_FIFO), len(mwords), fixed=True)
    regs = [(await spi.read(sim.core_reg(0, off)))[0] for off in (sim.C_PC, sim.C_STATUS, sim.C_R0, sim.C_R0 + 1,
                                                                   sim.C_R0 + 2, sim.C_R0 + 3)]
    diffs = lambda e: [b - a for a, b in zip(e, e[1:])]
    dut._log.info(f"side-set pin: {len(edges)} edges, intervals {diffs(edges)}; pushed {[hex(w) for w in words]}")
    assert len(edges) == len(medges) and diffs(edges) == diffs(medges), (diffs(edges), diffs(medges))
    assert words == mwords, ([hex(w) for w in words], [hex(w) for w in mwords])
    assert regs == mregs, ([hex(r) for r in regs], [hex(r) for r in mregs])


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
    Two cores (RX on core 0, TX on core 1); bit time = 32 clocks (48 MHz / 1.5 Mbit/s).
    The clock is 48 MHz: at the default 50 MHz the bus ran at 1.5625 Mbit/s
    (+4.2 %, outside low speed's +-1.5 %), which sigrok's decoder rejected
    (test_sigrok.py) while the test's own helpers, scaled the same way, passed."""
    from pe import usb, usbdev
    spi = await setup(dut, clock_ns=20.834)  # 48 MHz within simulator precision
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
    assert await records() == [usbdev.Record(True, req, 16 + 8 * 10, setup=True)]
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


# ------------------------------------------------------------------ USB autonomous enumeration

LAST_USB_HOST = None

def rtl_control_host(dut, **kw):
    """pe.usbhost.ControlHost (the model tests' host controller: toggles,
    retries, STALL, keep-alives, faults) on the RTL pads. Its transaction logic
    runs in a bridge() thread; each bus operation is one resume() into the
    simulator: drive a packet, wait, or record the device's answer."""
    import types
    from pe import usb
    from pe.usbhost import ControlHost

    class RtlControlHost(ControlHost):
        def __init__(self):
            super().__init__(types.SimpleNamespace(pull_up=0, chip=types.SimpleNamespace(cycle=0)), **kw)

        def _lengths(self, bits):
            out = []
            for _ in range(bits):
                self.frac += self.tpb
                out.append(int(self.frac))
                self.frac -= out[-1]
            return out

        @resume
        async def _clocks(self, n):
            await ClockCycles(dut.clk, n)
            self.mh.chip.cycle += n

        @resume
        async def _drive_rtl(self, states, lengths):
            for s, n in zip(states, lengths):
                dp, dm = USB_LINE[s]
                EXT.set(0, True, dp)
                EXT.set(1, True, dm)
                await ClockCycles(dut.clk, n)
            EXT.set(0, False)
            EXT.set(1, False)
            self.mh.chip.cycle += sum(lengths)

        def _steps(self, bits):
            n = sum(self._lengths(bits))
            if n:
                self._clocks(n)

        def _drive(self, states):
            self._drive_rtl(states, self._lengths(len(states)))

        @resume
        async def _response(self):
            trace, start, i = [], None, 0
            while True:
                await RisingEdge(dut.clk)
                s = {2: usb.J, 1: usb.K, 0: usb.SE0}.get(int(dut.uio_in.value) & 3, "?")
                i += 1
                if start is None:
                    if s == usb.K:
                        start = i
                        self.turnarounds.append(1 + (i - 1) / self.bit)
                    elif i >= self.TIMEOUT_BITS * self.bit:
                        self.mh.chip.cycle += i
                        return None
                if start is not None:
                    trace.append(s)
                    if len(trace) > 2 and trace[-1] == usb.J and trace[-2] == usb.SE0:
                        break
            self.mh.chip.cycle += i
            return self._decode(trace)

        @resume
        async def _reset_rtl(self, cycles, recovery):
            EXT.set(0, True, 0)
            EXT.set(1, True, 0)
            await ClockCycles(dut.clk, cycles)
            EXT.set(0, False)
            EXT.set(1, False)
            await ClockCycles(dut.clk, recovery)
            self.mh.chip.cycle += cycles + recovery

        def bus_reset(self, cycles=48 * 100, recovery=48 * 1000):
            """100 us of SE0 (the device must act on 2.5 us; hosts send 10 ms)."""
            self._reset_rtl(cycles, recovery)
            self.next_keepalive = self.mh.chip.cycle

    return RtlControlHost()


@cocotb.test()
async def test_usb_autonomous(dut):
    """Autonomous USB low-speed enumeration (docs/usb-enumeration.md): the host
    loads usb_enum and the line unit, then stays off the bus (CS_n high, SCK
    still: checked on the pads) while the host controller model enumerates the
    chip to Configured: a SETUP whose ACK is lost, SET_ADDRESS's status ACK
    lost with an IN as the first token to the new address, a host ACK lost in
    a data stage, damaged DATA, a STALLed string descriptor and SET_FEATURE, a bus
    reset in the middle of a configuration read, then the whole flow again at a
    new address with SET_ADDRESS's status ACK lost. Only then does the host
    read the outcome over SPI. D+ = uio0, D- = uio1 (pull-up)."""
    await usb_autonomous(dut, "low")


@cocotb.test()
async def test_usb_autonomous_fs(dut):
    """test_usb_autonomous at full speed (12 Mbit/s, 4 clocks per bit): the
    same firmware with a shorter turnaround delay. D- = uio0, D+ = uio1
    (pull-up), so the pads show the same J/K patterns. Bit-exact edges here;
    jitter and frequency offset are covered on the model (test_usb_autonomous.py)."""
    await usb_autonomous(dut, "full")


async def usb_autonomous(dut, speed):
    from cocotb.triggers import ValueChange
    from pe import usb, usbdev, usbenum
    from pe.usbhost import Stall
    DEV, CFG = usbdev.DEVICE_DESC, usbdev.CONFIG_DESC
    spi = await setup(dut, clock_ns=20.834)       # 48 MHz
    dut.pull_up.value = 0b10                      # the device's pull-up: J
    await spi.apply(usbenum.load_writes(usbenum.assemble(speed), speed))
    await ClockCycles(dut.clk, 100)

    host_port = []                                # any change of CS_n/SCK/MOSI

    async def watch():
        last = int(dut.ui_in.value) >> 5
        while True:
            await ValueChange(dut.ui_in)
            v = int(dut.ui_in.value) >> 5
            if v != last:
                host_port.append((get_sim_time(unit="ns"), v))
                last = v
    watcher = cocotb.start_soon(watch())
    global LAST_USB_HOST
    h = LAST_USB_HOST = rtl_control_host(dut, bit=usbenum.CLOCKS_PER_BIT[speed],
                                         keepalive=speed == "low")   # test_sigrok compares its record of the bus

    lost_in = []                                  # the turnaround of that IN (bit times)

    def flow():
        h.faults["lose_handshake"] = 1            # the first SETUP is sent twice
        assert h.get_descriptor(0, 1, 8) == DEV[:8]
        h.faults["lose_host_ack"] = 1             # SET_ADDRESS's status ACK lost,
        h.control_write(0, bytes([0x00, 5, 5, 0, 0, 0, 0, 0]))
        h._send(usb.token(usb.PID_IN, 5, 0))      # then an IN first: the address is
        assert h._response() == bytes([usb.pid_byte(usb.PID_DATA1), 0, 0])   # committed on it
        lost_in.append(h.turnarounds[-1])
        h._steps(2)
        h._send(usb.handshake(usb.PID_ACK))
        h.gap()
        h.faults["lose_host_ack"] = 1
        assert h.get_descriptor(5, 1, 18) == DEV
        h.faults["bad_data"] = 1
        assert h.get_descriptor(5, 2, 9) == CFG[:9]
        try:
            h.get_descriptor(5, 3, 255)
            raise AssertionError("string descriptor not STALLed")
        except Stall:
            pass
        h.setup(5, bytes([0x80, 6, 0, 2, 0, 0, 18, 0]))
        assert h.data_in(5, 1) == CFG[:8]         # mid-transfer:
        h.bus_reset()
        h._send(usb.token(usb.PID_IN, 5, 0))           # the old address is gone
        assert h._response() is None
        assert h.get_descriptor(0, 1, 8) == DEV[:8]
        h.faults["lose_host_ack"] = 1             # SET_ADDRESS's status ACK lost
        h.control_write(0, bytes([0x00, 5, 9, 0, 0, 0, 0, 0]))
        assert h.get_descriptor(9, 1, 18) == DEV
        assert h.get_descriptor(9, 2, 9) == CFG[:9]
        assert h.get_descriptor(9, 2, 18) == CFG
        try:
            h.control_write(9, bytes([0x00, 3, 1, 0, 0, 0, 0, 0]))
            raise AssertionError("SET_FEATURE not STALLed")
        except Stall:
            pass
        h.control_write(9, bytes([0x00, 9, 1, 0, 0, 0, 0, 0]))
        assert h.control_read(9, bytes([0x80, 0, 0, 0, 0, 0, 2, 0])) == b"\0\0"

    await bridge(flow)()
    watcher.cancel()
    assert not host_port, host_port[:4]
    assert h.injected == {"lose_handshake": 1, "lose_host_ack": 3, "bad_data": 1}, h.injected
    assert all(2.0 <= t <= 6.5 for t in h.turnarounds), (min(h.turnarounds), max(h.turnarounds))
    assert len(lost_in) == 1 and 2.0 <= lost_in[0] <= 6.5, lost_in
    st = usbenum.decode_state((await spi.read(sim.A_FSET))[0],
                              (await spi.read(sim.core_reg(0, sim.C_R0 + 3)))[0])
    assert st == dict(address=9, configured=True, pending_address=False, stalled=False), st
    dut._log.info(f"autonomous USB ({speed} speed): {len(h.log)} transactions, {h.mh.chip.cycle} cycles "
                  f"({h.mh.chip.cycle / 48e3:.1f} ms), turnaround {min(h.turnarounds):.2f}-"
                  f"{max(h.turnarounds):.2f} bit times (the IN after a lost status ACK: {lost_in[0]:.2f}), "
                  f"host port idle throughout; state {st}")


# ------------------------------------------------------------------ USB full speed (study)

@cocotb.test()
async def test_usb_fs_device(dut):
    """USB FULL-SPEED device (docs/usb-full-speed.md): usb_fs_dev on core 0 with
    the line unit at 4 clocks per bit, clk = 48 MHz. A pins-only host places
    its edges in continuous time (random phase against clk, duty-cycle
    distortion). SETUP + DATA0 -> ACK, IN -> NAK, IN -> pre-staged DATA1, the
    host's ACK -> F_GOTACK; turnaround measured on the bus from the host's
    SE0 -> J to the device's first K (USB 2.0: 2 to 6.5 bit times). Then OUT
    packets at the spec's consecutive-edge jitter (DCD 9.25 ns = 18.5 ns between
    edges) must all be ACKed, and so must those at DCD 14 ns (28 ns), which
    the g4 receiver (sample 2-3 clocks after an edge) missed half of: the
    half-cycle NRZI sample point (docs/isa.md §4.7) centres the sample.
    PE_USB_FS_CLK_NS (flow/gl_sdf_test.sh) runs it at another clock with the
    bus scaled to match (4 clocks per bit; bit time and DCD scale with the
    clock): not USB's 12 Mbit/s, the same logic at a slower clock."""
    import random
    from pe import usb, usbdev
    T = float(os.environ.get("PE_USB_FS_CLK_NS", "20.834"))   # 48 MHz (ps-quantized)
    TBIT = 1000 / 12 * T / 20.834
    dcd_scale = T / 20.834
    spi = await setup(dut, clock_ns=T)
    dut.pull_up.value = 0b10                       # 1.5k on D+ (uio1); D- = uio0
    prog = load_fw("usb_fs_dev", **usbdev.defines())
    await spi.write(sim.core_reg(0, sim.C_CRCCTRL), [usbdev.CRCCTRL])
    await spi.write(sim.A_LINEDIV, [(4 << 16) | (4 << 8)])
    await spi.write(sim.A_LINEPINS, [0])
    await spi.write(sim.A_LINECFG, [0x49F3])
    await run_program(spi, 0, prog, clkdiv=1 << 8)
    await ClockCycles(dut.clk, 50)
    rng = random.Random(7)
    PADS = {"J": (0, 1), "K": (1, 0), "0": (0, 0)}
    bus = {"edges": [], "last": None}

    async def monitor():                           # the device's pads change after a rising edge
        while True:
            await FallingEdge(dut.clk)
            v = {2: "J", 1: "K", 0: "0"}.get(int(dut.uio_in.value) & 3, "?")
            if v != bus["last"]:
                bus["edges"].append((get_sim_time(unit="ps") / 1000 - T / 2, v))
                bus["last"] = v
    mon = cocotb.start_soon(monitor())

    async def send(packet, gap_bits=4.0, dcd=0.0, after=None):
        """Drive a packet starting gap_bits after `after` (a bus time, ns) plus
        a random phase; returns the time of its SE0 -> J."""
        now = get_sim_time(unit="ps") / 1000
        t0 = max(now, (after or now) + gap_bits * TBIT) + rng.random() * T
        states = usb.encode(packet)
        prev, t = "J", t0
        for i, s in enumerate(states):
            te = t0 + i * TBIT + ((dcd if s == "K" else -dcd) if s != prev and s != "0" else 0)
            if te > now:
                await Timer(round((te - now) * 1000), unit="ps")
                now = te
            EXT.set(0, True, PADS[s][0])
            EXT.set(1, True, PADS[s][1])
            if prev == "0" and s == "J":
                t = te
            prev = s
        await Timer(round(TBIT * 1000), unit="ps")
        EXT.set(0, False)
        EXT.set(1, False)
        return t

    async def response(t_eop, bits=20):
        """The device's packet after the host's EOP at t_eop: (bytes, turnaround
        in bit times) or (None, None)."""
        start = len(bus["edges"])
        await Timer(round(bits * TBIT * 1000), unit="ps")
        while True:
            ev = [e for e in bus["edges"][start:] if e[0] > t_eop]
            if not ev or ev[0][1] != "K":
                return None, None
            if any(a[1] == "0" and b[1] == "J" for a, b in zip(ev, ev[1:])):
                break
            await ClockCycles(dut.clk, 20)
        states = ["J"]
        for (t, s), (t2, _) in zip(ev, ev[1:]):
            states += [s] * max(1, round((t2 - t) / TBIT))
            if s == "0" and _ == "J":
                break
        states.append("J")
        try:
            return usb.decode(states), (ev[0][0] - t_eop) / TBIT
        except ValueError:
            return b"?", (ev[0][0] - t_eop) / TBIT

    async def records():
        st = (await spi.read(sim.core_reg(0, sim.C_STATUS)))[0]
        n = (st >> 4) & 15
        words = await spi.read(sim.core_reg(0, sim.C_FIFO), n, fixed=True) if n else []
        recs, rest = usbdev.parse_records(words)
        assert not rest, words
        return recs

    tas = {}
    req = bytes([0x80, 6, 0, 1, 0, 0, 0x40, 0])
    t = await send(usb.token(usb.PID_SETUP, 0, 0))
    t = await send(usb.data_packet(usb.PID_DATA0, req), after=t)
    r, tas["ACK"] = await response(t)
    assert r == usb.handshake(usb.PID_ACK), r
    await ClockCycles(dut.clk, 100)
    assert await records() == [usbdev.Record(True, req, 16 + 8 * 10, setup=True)]
    t = await send(usb.token(usb.PID_IN, 0, 0))
    r, tas["NAK"] = await response(t)
    assert r == usb.handshake(usb.PID_NAK), r
    desc = usbdev.DEVICE_DESC[:8]
    await spi.write(sim.core_reg(0, sim.C_FIFO), usbdev.stage_words(usb.PID_DATA1, desc), fixed=True)
    await spi.write(sim.A_FSET, [usbdev.F_STAGED])
    t = await send(usb.token(usb.PID_IN, 0, 0))
    r, tas["DATA1"] = await response(t, 120)
    assert r == usb.data_packet(usb.PID_DATA1, desc), r
    eop = [b for a, b in zip(bus["edges"], bus["edges"][1:]) if a[1] == "0" and b[1] == "J"][-1]
    await send(usb.handshake(usb.PID_ACK), gap_bits=2.5, after=eop[0])
    await ClockCycles(dut.clk, 100)
    flags = (await spi.read(sim.A_FSET))[0]
    assert flags & usbdev.F_GOTACK and not flags & usbdev.F_STAGED, hex(flags)
    for k, v in tas.items():
        dut._log.info(f"turnaround {k}: {v:.2f} bit times = {v * 4:.1f} clocks (spec 2-6.5)")
        assert 2.0 <= v <= 6.5, (k, v)

    async def out_burst(dcd, n):
        ok = 0
        for i in range(n):
            p = bytes(rng.choice((0xFF, 0x00, 0x55, rng.randrange(256))) for _ in range(rng.randrange(1, 9)))
            t = await send(usb.token(usb.PID_OUT, 0, 0), dcd=dcd, gap_bits=8)
            t = await send(usb.data_packet(usb.PID_DATA0, p), dcd=dcd, after=t)
            r, ta = await response(t)
            recs = await records()
            ok += r == usb.handshake(usb.PID_ACK) and [(x.ok, x.data) for x in recs] == [(True, p)]
        return ok
    n = 24
    spec = await out_burst(9.25 * dcd_scale, n)
    wide = await out_burst(14.0 * dcd_scale, n)
    dut._log.info(f"half-cycle NRZI sample point, clk {T} ns: DCD {9.25 * dcd_scale:.2f} ns "
                  f"({18.5 * dcd_scale:.1f} ns edge-to-edge): {spec}/{n} ACKed; DCD {14 * dcd_scale:.2f} ns "
                  f"({28 * dcd_scale:.1f} ns): {wide}/{n} ACKed")
    mon.cancel()
    assert spec == n
    assert wide == n


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
async def test_fetch_to_pin(dut):
    """The last core writes uio5 every cycle, alternating the pin-writing
    instructions (SET, PIN, MOV pins, OUT pins after MOV osr) and toggles,
    which read the pin's own output register: a write that misses its clock
    edge changes the pattern rather than delaying it. In the signed-off
    netlist the slow-corner STA critical path runs from that core's SRAM bank
    through decode to the pin output register; flow/gl_sdf_test.sh runs this
    test with SDF timing."""
    spi = await setup(dut)
    P = UIO(5)
    # (instruction, the pin after it: 0, 1, "t" toggle or None unchanged)
    body = [("mov osr, ~r1", None), ("out pins, 1", 1), ("pin {p}, toggle", "t"), ("mov pins, r0", 1),
            ("mov osr, r1", None), ("out pins, 1", 0), ("pin {p}, toggle", "t"), ("mov pins, ~r0", 0),
            ("set pins, 1", 1), ("pin {p}, toggle", "t"), ("pin {p}, toggle", "t"), ("pin {p}, hi", 1),
            ("pin {p}, toggle", "t"), ("set pins, 1", 1), ("pin {p}, lo", 0), ("pin {p}, toggle", "t")]
    src = "    ldi r0, 1\n    ldi r1, 0\n.wrap_target\n"
    src += "".join(f"    {i.format(p=P)}\n" for i, _ in body) + ".wrap\n"
    prog = asm.assemble(src)
    want, v = [], 0                     # the pin after each body instruction, in steady state
    for _, e in body * 2:
        v = 1 - v if e == "t" else v if e is None else e
        want.append(v)
    want = want[len(body):]
    c = NCORES - 1
    await spi.apply(host.pin_owner_writes({P: c}, profile=PROFILE))
    await spi.write(sim.A_POE, [1 << P])
    await run_program(spi, c, prog, out_pins=(P, 1), set_pins=(P, 1), clkdiv=1 << 8)
    await ClockCycles(dut.clk, 20)
    got = []
    for _ in range(8 * len(body)):
        await FallingEdge(dut.clk)
        got.append((int(dut.uio_out.value) >> P) & 1)
    n = len(body)
    phases = [r for r in range(n) if all(g == want[(i + r) % n] for i, g in enumerate(got))]
    assert phases, f"uio5 per cycle {''.join(map(str, got))}, expected a rotation of {''.join(map(str, want))}"


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
    for period, duty in ((T, .5), (2 * T, .25), (2 * T, .75)):
        spi = QuadSpi(dut, period=period, duty=duty, phase=rng.uniform(0, T), cs_setup=T, cs_hold=T, cs_gap=4 * T)
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


@cocotb.test()
async def test_host_reset_aborts_selected_transaction(dut):
    """Reset at every SPI parser phase, hold CS low through release, and prove
    later clocks cannot write or pop. A fresh transaction recovers on all cores.
    This uses pin traffic and register/FIFO observations, also suitable for GL.
    """
    await setup(dut)
    T = CLK_NS
    spi = TimedSpi(dut, period=2*T, cs_setup=T, cs_hold=T, cs_gap=5*T)
    frame = host.spi_frame(host.CMD_WRITE, sim.A_POUT, [0x12345678, 0x87654321])
    bits = [(b >> i) & 1 for b in frame for i in range(7, -1, -1)]

    async def clock(bit):
        spi._set(spi.MOSI, bit)
        await Timer(T, unit="ns")
        spi._set(spi.SCK, 1)
        await Timer(T, unit="ns")
        spi._set(spi.SCK, 0)

    for phase_bits in (3, 12, 24, 39, 55, 64):
        spi._set(spi.CS, 0)
        await Timer(T, unit="ns")
        for bit in bits[:phase_bits]:
            await clock(bit)
        dut.rst_n.value = 0
        await ClockCycles(dut.clk, 5)  # SCK stopped: abort must still be immediate
        dut.rst_n.value = 1
        await ClockCycles(dut.clk, 6)
        for bit in bits[phase_bits:]:
            await clock(bit)
        for bit in bits:             # even command-shaped continuation must be ignored
            await clock(bit)
        spi._set(spi.CS, 1)
        await ClockCycles(dut.clk, 6)
        assert await spi.read(sim.A_POUT) == [0]
        assert await spi.read(sim.A_CTRL) == [0]
        for c in range(NCORES):
            values = [0x1234 + c, 0xA5A5, 0xFEED, c]
            await spi.write(sim.core_reg(c, sim.C_R0), values)
            assert await spi.read(sim.core_reg(c, sim.C_R0), 4) == values
    # Abort while a read has fetched FIFO words ahead, then verify recovery.
    echo = asm.assemble("loop: pull block\nmov isr, osr\npush block\njmp loop")
    await run_program(spi, 0, echo)
    await spi.write(sim.core_reg(0, sim.C_FIFO), [0xDEADBEEF], fixed=True)
    read_bits = [(b >> i) & 1 for b in host.spi_frame(host.CMD_READ_FIXED, sim.core_reg(0, sim.C_FIFO), nread=1)
                 for i in range(7, -1, -1)]
    spi._set(spi.CS, 0)
    await Timer(T, unit="ns")
    for bit in read_bits[:40]:
        await clock(bit)
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 6)
    for bit in read_bits[40:]:
        await clock(bit)
    spi._set(spi.CS, 1)
    await ClockCycles(dut.clk, 6)
    assert (await spi.read(sim.core_reg(0, sim.C_STATUS)))[0] & 255 == 0
    await run_program(spi, 0, echo)
    await spi.write(sim.core_reg(0, sim.C_FIFO), [0xC0FFEE], fixed=True)
    assert await spi.read(sim.core_reg(0, sim.C_FIFO), fixed=True) == [0xC0FFEE]


async def eth_send(port, frame, rng, jitter=True):
    """One 10BASE-T frame (preamble, SFD, frame) on ui0 at 10 Mbit/s - 200 ppm,
    each edge moved by up to 802.3's +-13.5 ns, then the minimum 96-bit gap.
    port._set drives ui0 without disturbing the quad IO pins on ui[5:2]."""
    bits = [1, 0]*31 + [1, 1] + [(b >> k) & 1 for b in frame for k in range(8)]
    levels = [v for b in bits for v in (1-b, b)] + [1]*4
    t0 = get_sim_time(unit="ns") + 120
    prev = 0
    for k, lv in enumerate(levels):
        if lv != prev:
            te = t0 + k*50*(1-200e-6) + (rng.uniform(-13.5, 13.5) if jitter else 0)
            dt = round((te-get_sim_time(unit="ns"))*1000)
            if dt > 0:
                await Timer(dt, unit="ps")
            port._set(0, lv)
        prev = lv
    dt = round((t0+len(levels)*50-get_sim_time(unit="ns"))*1000)
    if dt > 0:
        await Timer(dt, unit="ps")
    port._set(0, 0)
    await Timer(96*100, unit="ns")


@cocotb.test()
async def test_eth10_rx_streaming(dut):
    """Legal 64..1518-byte frames including FCS, 96-bit IFG, concurrent quad
    SPI draining, bounded stalls, deliberate overflow and explicit recovery.
    Digital comparator input at 50 MHz; no analog compliance claim.
    """
    import random
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "model"))
    import test_eth10 as eth
    await setup(dut)
    spi = QuadSpi(dut, period=2*CLK_NS, cs_setup=CLK_NS, cs_hold=CLK_NS, cs_gap=4*CLK_NS)
    await spi.write(sim.A_HOSTCFG, [1])
    prog = load_fw("eth10_rx")

    async def reset_rx():
        await spi.write(sim.A_CTRL, [1 << 16])
        await spi.write(sim.A_LINECFG, [0])
        await spi.write(sim.A_LINEDIV, [5 << 16])
        await spi.write(sim.A_LINEPINS, [UI(0) << 8])
        await spi.write(sim.A_LINECFG, [0x4005])
        await run_program(spi, 0, prog, clkdiv=1 << 8)

    await reset_rx()
    rng = random.Random(19)
    finished, collected = False, []

    async def drain():
        polls = 0
        while not finished:
            st = (await spi.qxfer(0x43, sim.core_reg(0, sim.C_STATUS), nread=1))[0]
            n = (st >> 4) & 15
            if n:
                collected.extend(await spi.qxfer(0x4B, sim.core_reg(0, sim.C_FIFO), nread=n))
            polls += 1
            if polls % 73 == 0:
                await ClockCycles(dut.clk, 200)  # bounded 4 us host stall

    async def send(fr, jitter=True):
        await eth_send(spi, fr, rng, jitter)

    task = cocotb.start_soon(drain())
    frames = [eth.frame(n) for n in (46, 110, 1500, 46)]
    for fr in frames:
        await send(fr)
    await ClockCycles(dut.clk, 1000)
    finished = True
    await task
    decoded = [eth.frame_from_bits(b) for b in eth.rx_bits(collected) if len(b) > 64]
    assert decoded == frames, [len(b) for b in decoded]
    assert not (await spi.read(sim.A_LINESTAT))[0] & (1 << 19)

    # With no servicing, a legal frame must report overflow, never be trusted.
    await reset_rx()
    await send(eth.frame(110), jitter=False)
    assert (await spi.read(sim.A_LINESTAT))[0] & (1 << 19)
    # Invalidate the entire frame and reset both firmware/FIFO and line queues.
    await reset_rx()
    assert not (await spi.read(sim.A_LINESTAT))[0] & (1 << 19)
    collected.clear()
    finished = False
    task = cocotb.start_soon(drain())
    await send(frames[0])
    await ClockCycles(dut.clk, 1000)
    finished = True
    await task
    assert [eth.frame_from_bits(b) for b in eth.rx_bits(collected) if len(b) > 64] == [frames[0]]


@cocotb.test()
async def test_eth10_common_clock_loopback(dut):
    """Two cores at 50 MHz, TX divider 1.25 and RX period 5, full-frame SPI
    service over virtual pins. Digital loopback does not prove analog TX limits.
    """
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "model"))
    import test_eth10 as eth
    await setup(dut)
    spi = QuadSpi(dut, period=2*CLK_NS, cs_setup=CLK_NS, cs_hold=CLK_NS, cs_gap=4*CLK_NS)
    await spi.write(sim.A_HOSTCFG, [1])
    await spi.apply(host.pin_owner_writes({24: 1, 25: 1}, profile=PROFILE))
    await spi.write(sim.A_LINEDIV, [5 << 16])
    await spi.write(sim.A_LINEPINS, [24 << 8])
    await spi.write(sim.A_LINECFG, [0x4005])
    await run_program(spi, 0, load_fw("eth10_rx"), clkdiv=256)
    await run_program(spi, 1, load_fw("eth10_tx", F_GO=0), clkdiv=320, side_pins=24)
    collected, frames = [], [eth.frame(46), eth.frame(1500, seed=5)]
    for fr in frames:
        words = eth.words(fr)
        await spi.write(sim.core_reg(1, sim.C_R0), [8*len(fr)-1])
        await spi.qxfer(0x4A, sim.core_reg(1, sim.C_FIFO), words=words[:16])
        sent = min(16, len(words))
        await spi.write(sim.A_FSET, [1])
        for _ in range(20000):
            rx = (await spi.read(sim.core_reg(0, sim.C_STATUS)))[0]
            assert not rx & (1 << 13 | 1 << 14), "RX halted/faulted"
            n = rx >> 4 & 15
            if n:
                collected.extend(await spi.qxfer(0x4B, sim.core_reg(0, sim.C_FIFO), nread=n))
            tx = (await spi.read(sim.core_reg(1, sim.C_STATUS)))[0]
            assert not tx & (1 << 13 | 1 << 14), "TX halted/faulted"
            n = min(4, 8-(tx & 15), len(words)-sent)
            if n:
                await spi.qxfer(0x4A, sim.core_reg(1, sim.C_FIFO), words=words[sent:sent+n])
                sent += n
            if sent == len(words) and not (await spi.read(sim.A_FSET))[0] & 1:
                break
        else:
            assert False, "common-clock frame did not finish"
        await ClockCycles(dut.clk, 1000)  # flush trailer and exceed 96-bit IFG
        n = (await spi.read(sim.core_reg(0, sim.C_STATUS)))[0] >> 4 & 15
        if n:
            collected.extend(await spi.qxfer(0x4B, sim.core_reg(0, sim.C_FIFO), nread=n))
        assert not (await spi.read(sim.A_LINESTAT))[0] & (1 << 19), "RX overflow"
    assert [eth.frame_from_bits(b) for b in eth.rx_bits(collected) if len(b) > 64] == frames


@cocotb.test()
async def test_usb_concurrent_service(dut):
    """Enumeration/error recovery with actual SPI service running concurrently
    with USB packets, independent of the reference model's bus or chip state.
    Both firmware variants use the same portable board control service.
    """
    from pe import usb, usbdev
    clock_ns = 20.834  # nearest even-ps period to 48 MHz (32 ppm quantization)
    spi = await setup(dut, clock_ns=clock_ns)
    port = QuadSpi(dut, period=2*clock_ns, cs_setup=clock_ns, cs_hold=clock_ns, cs_gap=4*clock_ns)

    class Transport:
        @resume
        async def read(self, addr):
            return (await port.qxfer(0x43, addr, nread=1))[0]

        @resume
        async def write(self, addr, word):
            await port.qxfer(0x42, addr, [word])

        @resume
        async def push(self, c, word):
            await port.qxfer(0x4A, sim.core_reg(c, sim.C_FIFO), [word])

        @resume
        async def pop_all(self, c):
            st = (await port.qxfer(0x43, sim.core_reg(c, sim.C_STATUS), nread=1))[0]
            n = (st >> 4) & 15
            return await port.qxfer(0x4B, sim.core_reg(c, sim.C_FIFO), nread=n) if n else []

        @resume
        async def now_us(self):
            return get_sim_time(unit="ns") / 1000

    async def send(pkt):
        await usb_drive(dut, usb.encode(pkt))

    async def response(cycles=4000):
        trace = await usb_listen(dut, cycles)
        return usb_decode_trace(trace)[0] if "K" in trace else None

    async def setup_req(addr, req):
        for _ in range(20):
            await send(usb.token(usb.PID_SETUP, addr, 0))
            await ClockCycles(dut.clk, 64)
            await send(usb.data_packet(usb.PID_DATA0, req))
            if await response(1200) == usb.handshake(usb.PID_ACK):
                return
        assert False, "SETUP was not ACKed"

    async def data_in(addr, ack=True):
        for _ in range(150):
            await send(usb.token(usb.PID_IN, addr, 0))
            r = await response()
            if r and r[0] in (usb.pid_byte(usb.PID_DATA0), usb.pid_byte(usb.PID_DATA1)):
                assert r[-2:] == usb.crc16(r[1:-2])
                if ack:
                    await ClockCycles(dut.clk, 64)
                    await send(usb.handshake(usb.PID_ACK))
                return r
        assert False, "IN never returned DATA"

    async def data_out(pid, payload):
        for _ in range(20):
            await send(usb.token(usb.PID_OUT, 0, 0))
            await ClockCycles(dut.clk, 64)
            await send(usb.data_packet(pid, payload))
            if await response(1200) == usb.handshake(usb.PID_ACK):
                return
        assert False, "OUT was not ACKed"

    def missing_stuffed_zero(packet):
        states, level, ones, skipped = [], usb.J, 0, False
        for bit in usb.bits_lsb_first(bytes([0x80]) + packet):
            if not bit:
                level = usb.K if level == usb.J else usb.J
                ones = 0
            else:
                ones += 1
            states.append(level)
            if ones == 6:
                if not skipped:
                    skipped = True
                else:
                    level = usb.K if level == usb.J else usb.J
                    states.append(level)
                ones = 0
        assert skipped
        return states + [usb.SE0, usb.SE0, usb.J]

    transport = Transport()
    for variant in ("two-core", "line"):
        # No service owns SPI during reconfiguration.
        await spi.write(sim.A_CTRL, [3 << 16])
        await spi.write(sim.A_LINECFG, [0])
        await spi.write(sim.A_FCLR, [0x7F])
        dut.pull_up.value = 2
        rx = load_fw("usb_ls_rx" if variant == "two-core" else "usb_ls_dev", **usbdev.defines())
        await spi.write(sim.core_reg(0, sim.C_CRCCTRL), [usbdev.CRCCTRL])
        if variant == "two-core":
            await spi.apply(host.pin_owner_writes({0: 1, 1: 1}))
            await run_program(spi, 1, load_fw("usb_ls_tx", **usbdev.defines()), out_pins=(0, 2))
            tx = 1
        else:
            await spi.apply(host.pin_owner_writes({0: 0, 1: 0}))
            await spi.write(sim.A_LINEDIV, [(32 << 16) | (32 << 8)])
            await spi.write(sim.A_LINEPINS, [0])
            await spi.write(sim.A_LINECFG, [0x49F3])
            tx = 0
        EXT.set(0, True, 1); EXT.set(1, True, 0)  # K must not look like reset
        await run_program(spi, 0, rx)
        await ClockCycles(dut.clk, 2000)
        assert not (await spi.read(sim.A_FSET))[0] & usbdev.F_RESET
        EXT.set(0, False); EXT.set(1, False)
        await ClockCycles(dut.clk, 100)
        await spi.write(sim.A_HOSTCFG, [1])
        sw = usbdev.DeviceSoftware(transport, rx, usbdev.DESCRIPTORS, tx_core=tx)
        done = False

        async def service():
            while not done:
                await bridge(sw.service)()
                await ClockCycles(dut.clk, 200)

        task = cocotb.start_soon(service())
        try:
            req = bytes.fromhex("8006000100001200")
            # Invalid complement with an unchanged valid CRC must be ignored.
            await send(usb.token(usb.PID_SETUP, 0, 0))
            await ClockCycles(dut.clk, 64)
            pkt = usb.data_packet(usb.PID_DATA0, req)
            await send(bytes([pkt[0]^0x10]) + pkt[1:])
            assert await response(1200) is None
            await send(usb.token(usb.PID_SETUP, 0, 0))
            await ClockCycles(dut.clk, 64)
            await send(pkt[:-1] + bytes([pkt[-1]^1]))  # valid PID, bad CRC16
            assert await response(1200) is None
            await send(usb.token(usb.PID_SETUP, 0, 0))
            await ClockCycles(dut.clk, 64)
            await usb_drive(dut, missing_stuffed_zero(usb.data_packet(usb.PID_DATA0, bytes([255]*8))))
            assert await response(1200) is None
            # OUT retries are ACKed repeatedly and delivered once per toggle.
            await data_out(usb.PID_DATA1, b"abc")
            await data_out(usb.PID_DATA1, b"abc")
            await data_out(usb.PID_DATA0, b"def")
            await ClockCycles(dut.clk, 10000)
            assert sw.out_records == [b"abc", b"def"]
            await setup_req(0, req)
            chunks = [await data_in(0) for _ in range(3)]
            assert b"".join(p[1:-2] for p in chunks) == usbdev.DEVICE_DESC
            assert [p[0] for p in chunks] == [0x4B, 0xC3, 0x4B]
            # Lost ACK must repeat the identical packet after the response window.
            await setup_req(0, req)
            first = await data_in(0, ack=False)
            assert await data_in(0) == first
            # A new SETUP aborts the old request and pending descriptor chunks.
            await setup_req(0, bytes.fromhex("0005050000000000"))
            assert (await data_in(0))[1:-2] == b""
            await ClockCycles(dut.clk, 3000)
            assert sw.address == 5
            # Firmware detects reset; SPI service clears filter, FIFOs and transfers.
            EXT.set(0, True, 0); EXT.set(1, True, 0)
            await ClockCycles(dut.clk, 480000)
            assert sw.address == 0 and sw.reset_wait
            EXT.set(0, False); EXT.set(1, False)
            await ClockCycles(dut.clk, 480000)
            assert not sw.reset_wait
            await setup_req(0, req)
            assert (await data_in(0))[1:-2] == usbdev.DEVICE_DESC[:8]
        finally:
            # bridge() runs synchronous service in a thread. Join it before an
            # assertion ends the test, so a failure cannot tear down its simulator.
            done = True
            await task
        await spi.write(sim.A_HOSTCFG, [0])
