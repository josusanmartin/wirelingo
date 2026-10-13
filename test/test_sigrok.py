"""sigrok as an independent oracle for the top-level protocol tests.

Each test here runs one test of test.py unchanged (its own device-model
assertions included) while recording the pads (ui_in, uo_out and the resolved
uio bus, as a logic analyzer on the board would see them), then hands the
waveform to sigrok-cli's protocol decoders (tools/pe/sigrok.py) and checks
what they decoded against the data the test meant to move. sigrok's decoders
are third-party code: they share no assumptions with test.py's UART monitor,
SPI slave, I2C EEPROM or USB helpers (tools/pe/usb.py).

Every test also decodes the SPI host port (CS_n = ui7, SCK = ui6, MOSI = ui5,
MISO = uo7, mode 0) and compares it byte for byte with what test.py's
bit-banged master sent and sampled.

    make COCOTB_TEST_MODULES=test_sigrok                 # RTL
    make GATES=yes GATE_LEVEL_NETLIST=<run>/runs/wokwi/final/nl/<top>.nl.v \
         COCOTB_TEST_MODULES=test_sigrok                 # gate level, functional
    MODULE=test_sigrok TESTS="sigrok_uart_tx sigrok_spi_master" \
         flow/gl_sdf_test.sh <run> typ 20                # gate level with SDF

Without sigrok-cli every test is skipped (see tools/pe/sigrok.py for how to
install it). SIGROK_KEEP=dir keeps each test's VCD and sigrok output.
"""
import importlib.util
import os
import pathlib

import cocotb
from cocotb.triggers import ValueChange
from cocotb.utils import get_sim_time

from pe import sigrok

HERE = pathlib.Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("pe_top_tests", HERE / "test.py")
T = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(T)

SKIP = sigrok.cli() is None
if SKIP:
    cocotb.log.warning(sigrok.INSTALL_HINT + " -- test_sigrok tests are skipped")


class PinRecorder:
    """Records every value change of the pads into a sigrok.Trace, plus the
    host-port transfers test.py's Spi master made (to compare with sigrok's
    decode of the port)."""

    def __init__(self, dut):
        self.trace = sigrok.Trace()
        self.xfers = []
        self.tasks = []
        self.sigs = (("ui", dut.ui_in), ("uo", dut.uo_out), ("uio", dut.uio_in))
        for name, sig in self.sigs:
            self._sample(name, sig)
            self.tasks.append(cocotb.start_soon(self._watch(name, sig)))
        self._xfer0 = T.Spi.xfer
        rec = self

        async def xfer(spi, data):
            out = await rec._xfer0(spi, data)
            rec.xfers.append((bytes(data), bytes(out)))
            return out
        T.Spi.xfer = xfer

    def _sample(self, name, sig):
        s = str(sig.value)                      # MSB first, may hold x/z at gate level
        val = sum(1 << i for i, c in enumerate(reversed(s)) if c == "1")
        self.trace.set_bus(get_sim_time(unit="ps"), name, val)

    async def _watch(self, name, sig):
        while True:
            await ValueChange(sig)
            self._sample(name, sig)

    def stop(self):
        for t in self.tasks:
            t.cancel()
        T.Spi.xfer = self._xfer0
        self.trace.end(get_sim_time(unit="ps"))
        return self.trace


def keep(name):
    d = os.environ.get("SIGROK_KEEP")
    return os.path.join(d, name) if d else None


def check_host_port(dut, rec, name):
    """sigrok's SPI decode of the host port == test.py's own view of it."""
    mosi, miso, warn = sigrok.spi(rec.trace, "ui6", "ui5", "uo7", cs="ui7", res_ps=10000,
                                  keep=keep(name + "_hostport"))
    want_mosi = b"".join(d for d, _ in rec.xfers)
    want_miso = b"".join(r for _, r in rec.xfers)
    got_mosi, got_miso = bytes(b for _, b in mosi), bytes(b for _, b in miso)
    assert not warn, warn
    assert got_mosi == want_mosi, (len(got_mosi), len(want_mosi), _first_diff(got_mosi, want_mosi))
    assert got_miso == want_miso, (len(got_miso), len(want_miso), _first_diff(got_miso, want_miso))
    dut._log.info(f"sigrok spi (host port): {len(rec.xfers)} transactions, {len(got_mosi)} bytes "
                  "each way, identical to the test's master")


def _first_diff(a, b):
    i = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
    return i, a[i:i + 8].hex(), b[i:i + 8].hex()


async def run_recorded(dut, test):
    rec = PinRecorder(dut)
    try:
        await test.func(dut)
    finally:
        rec.stop()
    return rec


# ------------------------------------------------------------------ UART

@cocotb.test(skip=SKIP)
async def sigrok_uart_tx(dut):
    """test_uart_tx: uo0 at 16 clocks per bit (3.125 Mbaud at 50 MHz)."""
    rec = await run_recorded(dut, T.test_uart_tx)
    data, warn = sigrok.uart(rec.trace, "uo0", round(1e9 / (16 * T.CLK_NS)), keep=keep("uart_tx"))
    got = bytes(b for _, b in data)
    dut._log.info(f"sigrok uart (uo0): {got!r} warnings={warn}")
    assert got == b"Hello, protoemu!" and not warn
    check_host_port(dut, rec, "uart_tx")


@cocotb.test(skip=SKIP)
async def sigrok_uart_rx(dut):
    """test_uart_rx: the test's sender on ui0 at 24 clocks per bit."""
    rec = await run_recorded(dut, T.test_uart_rx)
    data, warn = sigrok.uart(rec.trace, "ui0", round(1e9 / (24 * T.CLK_NS)), keep=keep("uart_rx"))
    got = bytes(b for _, b in data)
    dut._log.info(f"sigrok uart (ui0): {got!r} warnings={warn}")
    assert got == b"\x55\xAA\x00\xFFok" and not warn
    check_host_port(dut, rec, "uart_rx")


# ------------------------------------------------------------------ SPI master

@cocotb.test(skip=SKIP)
async def sigrok_spi_master(dut):
    """test_spi_master: SCK = uio2, MOSI = uio3, MISO = uio4 (the test's slave), mode 0."""
    rec = await run_recorded(dut, T.test_spi_master)
    mosi, miso, warn = sigrok.spi(rec.trace, "uio2", "uio3", "uio4", keep=keep("spi_master"))
    mosi, miso = [b for _, b in mosi], [b for _, b in miso]
    dut._log.info(f"sigrok spi: MOSI {[hex(b) for b in mosi]} MISO {[hex(b) for b in miso]}")
    assert mosi == [0xA5, 0x0F, 0xFF, 0x00] and miso == [0xC3, 0x5A, 0x01, 0x80] and not warn
    check_host_port(dut, rec, "spi_master")


# ------------------------------------------------------------------ I2C

I2C_WANT = (["S", ("AW", 0x50), "ACK"] + sum([[("DW", b), "ACK"] for b in (0x10, 0xDE, 0xAD, 0x42)], [])
            + ["P"]
            + ["S", ("AW", 0x50), "ACK", ("DW", 0x10), "ACK", "Sr", ("AR", 0x50), "ACK",
               ("DR", 0xDE), "ACK", ("DR", 0xAD), "ACK", ("DR", 0x42), "NACK", "P"]
            + ["S", ("AW", 0x58), "NACK", "P"])


@cocotb.test(skip=SKIP)
async def sigrok_i2c_master(dut):
    """test_i2c_master: SDA = uio0, SCL = uio1 (resolved open-drain bus), the
    EEPROM model stretching SCL after every ACK."""
    rec = await run_recorded(dut, T.test_i2c_master)
    toks = [t for _, t in sigrok.i2c(rec.trace, "uio1", "uio0", res_ps=10000, keep=keep("i2c"))]
    dut._log.info(f"sigrok i2c: {toks}")
    assert toks == I2C_WANT, toks
    check_host_port(dut, rec, "i2c")


# ------------------------------------------------------------------ USB

usb_summary = sigrok.usb_summary


@cocotb.test(skip=SKIP)
async def sigrok_usb_ls_device(dut):
    """test_usb_ls_device at 48 MHz (32 clocks per bit): D+ = uio0, D- = uio1."""
    rec = await run_recorded(dut, T.test_usb_ls_device)
    req = bytes([0x80, 6, 0, 1, 0, 0, 0x40, 0])
    desc = bytes([0x12, 0x01, 0x10, 0x01, 0x00, 0x00, 0x00, 0x08])
    want = [("SETUP", 0, 0, None), ("DATA0", None, None, req.hex()), ("ACK", None, None, None),
            ("IN", 0x15, 0, None), ("IN", 0, 0, None), ("NAK", None, None, None),
            ("IN", 0, 0, None), ("DATA1", None, None, desc.hex())]
    pk, err = sigrok.usb(rec.trace, "uio0", "uio1", "low-speed", keep=keep("usb_ls"))
    dut._log.info(f"sigrok usb ls: {usb_summary(pk)}")
    assert usb_summary(pk) == want and not err, (usb_summary(pk), err)
    assert all(p.crc_ok for p in pk)
    check_host_port(dut, rec, "usb_ls")


@cocotb.test(skip=SKIP)
async def sigrok_usb_fs_device(dut):
    """test_usb_fs_device at 48 MHz: D+ = uio1, D- = uio0. The control
    transaction, then 24 OUT + DATA0 pairs at the spec's DCD jitter (all must
    be ACKed) and 24 at DCD 14 ns (the half-cycle NRZI sample point takes
    them all, as test_usb_fs_device asserts)."""
    rec = await run_recorded(dut, T.test_usb_fs_device)
    pk, err = sigrok.usb(rec.trace, "uio1", "uio0", "full-speed", keep=keep("usb_fs"))
    s = usb_summary(pk)
    req = bytes([0x80, 6, 0, 1, 0, 0, 0x40, 0])
    from pe import usbdev
    head = [("SETUP", 0, 0, None), ("DATA0", None, None, req.hex()), ("ACK", None, None, None),
            ("IN", 0, 0, None), ("NAK", None, None, None),
            ("IN", 0, 0, None), ("DATA1", None, None, usbdev.DEVICE_DESC[:8].hex()),
            ("ACK", None, None, None)]
    dut._log.info(f"sigrok usb fs: {len(pk)} packets, {len(err)} errors; first {s[:8]}")
    assert s[:8] == head, s[:8]
    # the OUT bursts: OUT, DATA0, then ACK when the device took it
    rest, bursts, i = s[8:], [], 0
    while i < len(rest):
        assert rest[i][:3] == ("OUT", 0, 0) and rest[i + 1][0] == "DATA0", rest[i:i + 3]
        acked = i + 2 < len(rest) and rest[i + 2][0] == "ACK"
        bursts.append(acked)
        i += 3 if acked else 2
    spec, wide = bursts[:24], bursts[24:]
    dut._log.info(f"sigrok: DCD 9.25 ns {sum(spec)}/{len(spec)} ACKed, DCD 14 ns {sum(wide)}/{len(wide)}")
    assert len(bursts) == 48 and all(spec) and all(wide), (spec, wide)
    assert all(p.crc_ok for p in pk) and not err, err
    check_host_port(dut, rec, "usb_fs")


@cocotb.test(skip=SKIP)
async def sigrok_usb_autonomous(dut):
    """test_usb_autonomous (D+ = uio0, D- = uio1, 48 MHz): sigrok decodes
    exactly the packets the test's host controller model sent and received, in
    order (retransmissions and STALLs included), with no SYNC, stuffing, CRC5
    or CRC16 error. The host port carries only the load and the final reads."""
    await sigrok_usb_autonomous_at(dut, T.test_usb_autonomous, "uio0", "uio1", "low-speed")


@cocotb.test(skip=SKIP)
async def sigrok_usb_autonomous_fs(dut):
    """test_usb_autonomous_fs (D+ = uio1, D- = uio0): as sigrok_usb_autonomous."""
    await sigrok_usb_autonomous_at(dut, T.test_usb_autonomous_fs, "uio1", "uio0", "full-speed")


async def sigrok_usb_autonomous_at(dut, test, dp, dm, speed):
    rec = await run_recorded(dut, test)
    pk, err = sigrok.usb(rec.trace, dp, dm, speed, keep=keep("usb_autonomous_" + speed))
    got, want = usb_summary(pk), sigrok.usb_expect(T.LAST_USB_HOST.wire)
    diff = next((i for i, (g, w) in enumerate(zip(got, want)) if g != w), None)
    assert got == want, (len(got), len(want), diff, got[diff:diff + 3] if diff is not None else None)
    assert not err and all(p.crc_ok for p in pk), err[:5]
    dut._log.info(f"sigrok usb autonomous ({speed}): {len(pk)} packets, identical to the host model's, no errors")
    check_host_port(dut, rec, "usb_autonomous_" + speed)
