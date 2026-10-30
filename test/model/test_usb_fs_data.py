"""Data through the full-speed device's vendor requests (pe.usbfsdata) on the
model, against the pins-only host in pe.usbfs. The KV260 runs the same class
against Linux (fpga/kv260/host/usb_fs_data_demo.py)."""
import pathlib
import random

import pytest

from pe import asm, host, sim, usb, usbdev
from pe.modelhost import ModelHost
from pe.usbfs import FsHost
from pe.usbfsdata import REQ_READ, REQ_WRITE, LoopbackDevice

FW = pathlib.Path(__file__).resolve().parents[2] / "firmware"


def enumerated():
    mh = ModelHost()
    prog = asm.assemble((FW / "usb_fs_dev.pasm").read_text(), usbdev.defines())
    mh.apply(host.load_program(0, prog, 0, clkdiv=1 << 8))
    mh.write(sim.core_reg(0, sim.C_CRCCTRL), usbdev.CRCCTRL)
    mh.write(sim.A_LINEDIV, (4 << 16) | (4 << 8))
    mh.write(sim.A_LINEPINS, 0)
    mh.write(sim.A_LINECFG, 0x49F3)
    mh.pull_up = 0b10
    mh.start(1)
    mh.step(50)
    sw = LoopbackDevice(mh, prog, usbdev.DESCRIPTORS, 0, 0)

    def idle(m):
        m.step(300)
        sw.service(m)

    h = FsHost(mh, idle=idle, seed=3, jitter_ns=5)
    assert h.control_read(0, bytes([0x80, 6, 0, 1, 0, 0, 18, 0]), 18) == usbdev.DEVICE_DESC
    h.control_write_nodata(0, bytes([0, 5, 5, 0, 0, 0, 0, 0]))
    assert h.control_read(5, bytes([0x80, 6, 0, 2, 0, 0, 18, 0]), 18) == usbdev.CONFIG_DESC
    h.control_write_nodata(5, bytes([0, 9, 1, 0, 0, 0, 0, 0]))
    assert sw.address == 5
    return h, sw


def control_write(h, addr, req, data=b""):
    """SETUP, OUT data stage (DATA1, DATA0, ...), zero-length IN status stage."""
    h.setup(addr, req)
    for i in range(0, len(data), 8):
        pid = usb.PID_DATA0 if i // 8 % 2 else usb.PID_DATA1
        h.data_out(addr, 0, pid, data[i:i + 8])
    _, payload = h.data_in(addr, 0)
    assert payload == b""


def write_req(n):
    return bytes([0x40, REQ_WRITE, 0, 0, 0, 0, n & 255, n >> 8])


def read_req(n):
    return bytes([0xC0, REQ_READ, 0, 0, 0, 0, n & 255, n >> 8])


@pytest.mark.parametrize("size", [1, 7, 8, 9, 16, 64, 100])
def test_loopback(size):
    h, sw = enumerated()
    data = random.Random(size).randbytes(size)
    control_write(h, 5, write_req(size), data)
    assert sw.buffer == data
    assert h.control_read(5, read_req(size), size) == data


def test_short_read_ends_in_a_short_packet():
    h, _ = enumerated()
    control_write(h, 5, write_req(16), bytes(range(16)))
    assert h.control_read(5, read_req(32), 32) == bytes(range(16))


def test_read_clips_to_wlength():
    h, _ = enumerated()
    control_write(h, 5, write_req(16), bytes(range(16)))
    assert h.control_read(5, read_req(5), 5) == bytes(range(5))


def test_zero_length_write_empties_the_buffer():
    h, sw = enumerated()
    control_write(h, 5, write_req(8), bytes(8))
    control_write(h, 5, write_req(0))
    assert sw.buffer == b""
