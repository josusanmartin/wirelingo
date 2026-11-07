"""SET_CONFIGURATION's status stage answered by usb_fs_dev itself (branch
usb-config-prestage, backlog item F: the KV260's SET_CONFIGURATION stall).

Before: the status IN of SET_CONFIGURATION (wLength 0) was NAKed until the
device software on the SPI port had read the SETUP's record and staged a
zero-length DATA1, one software round trip. Now the firmware reads the
SETUP's first 16 bits as they arrive and, for bmRequestType 0x00 bRequest
0x09, answers the first status IN itself (F_CFGZLP, `cfg_zlp`); the software
stages nothing for that request. Every other request is answered as before.
Evidence level: model."""
import pathlib

import pytest

from pe import asm, host, sim, usb, usbdev
from pe.modelhost import ModelHost
from pe.usbfs import FsHost
from pe.usbfsdata import LoopbackDevice
import test_usb_fs_data as fs

FW_TEXT = (pathlib.Path(__file__).resolve().parents[2] / "firmware" / "usb_fs_dev.pasm").read_text()
DATA1 = usb.pid_byte(usb.PID_DATA1)
ZLP = ("IN", DATA1, "")
F_CFGZLP = 1 << usbdev.FLAGS["F_SENT"]     # usb_fs_dev's name for flag 7


def set_config(value):
    return bytes([0, 9, value, 0, 0, 0, 0, 0])


def without_prestage(text=FW_TEXT):
    """The same firmware with the pre-stage taken out: F_CFGZLP never set and
    no `cfg_zlp` label, so the software stages the status ZLP as before."""
    assert text.count("    flag set F_CFGZLP") == 1
    return text.replace("    flag set F_CFGZLP", "    nop").replace("cfg_zlp", "cfg_zlp_off")


def device(text=FW_TEXT, seed=3):
    """(FsHost, LoopbackDevice, serve): usb_fs_dev on the model, the software
    served at the host's idle points while serve["on"]."""
    mh = ModelHost()
    prog = asm.assemble(text, usbdev.defines())
    mh.apply(host.load_program(0, prog, 0, clkdiv=1 << 8))
    mh.write(sim.core_reg(0, sim.C_CRCCTRL), usbdev.CRCCTRL)
    mh.write(sim.A_LINEDIV, (4 << 16) | (4 << 8))
    mh.write(sim.A_LINEPINS, 0)
    mh.write(sim.A_LINECFG, 0x49F3)
    mh.pull_up = 0b10
    mh.start(1)
    mh.step(50)
    sw = LoopbackDevice(mh, prog, usbdev.DESCRIPTORS, 0, 0)
    serve = {"on": True}

    def idle(m):
        m.step(300)
        if serve["on"]:
            sw.service(m)
    return FsHost(mh, idle=idle, seed=seed, jitter_ns=5), sw, serve


def addressed(text=FW_TEXT):
    """Enumerated up to the configuration descriptor (address 5, not configured)."""
    h, sw, serve = device(text)
    assert h.control_read(0, bytes([0x80, 6, 0, 1, 0, 0, 18, 0]), 18) == usbdev.DEVICE_DESC
    h.control_write_nodata(0, bytes([0, 5, 5, 0, 0, 0, 0, 0]))
    assert h.control_read(5, bytes([0x80, 6, 0, 2, 0, 0, 18, 0]), 18) == usbdev.CONFIG_DESC
    assert sw.address == 5
    return h, sw, serve


def first_in(h, addr):
    """One IN, answered or not: the host's log entry for it."""
    n = len(h.log)
    try:
        h.data_in(addr, 0, tries=1)
    except TimeoutError:
        pass
    return h.log[n:]


def nothing_staged(h, sw, what):
    chip = h.mh.chip
    assert not chip.flags & usbdev.F_STAGED and not chip.cores[0].txf, \
        f"{what}: left staged: F_STAGED {bool(chip.flags & usbdev.F_STAGED)}, TX FIFO {len(chip.cores[0].txf)} words"
    assert sw.unacked is None and not sw.pending, f"{what}: the software still holds {sw.unacked}, {sw.pending}"


def test_the_software_knows_the_firmware_answers():
    prog = asm.assemble(FW_TEXT, usbdev.defines())
    assert "cfg_zlp" in prog.labels
    assert usbdev.DeviceSoftware(ModelHost(), prog, usbdev.DESCRIPTORS, 0, 0).fw_cfg_status
    old = asm.assemble(without_prestage(), usbdev.defines())
    assert not usbdev.DeviceSoftware(ModelHost(), old, usbdev.DESCRIPTORS, 0, 0).fw_cfg_status


@pytest.mark.parametrize("value", [1, 0])
def test_status_in_answered_with_the_software_held(value):
    """The software is not served from the SETUP on: the first status IN still
    gets the zero-length DATA1, with no NAK (0 NAKs: the firmware answers it
    as its token ends), and so does a repeated IN (a host that lost the
    packet). Once served, the software has seen the request and staged
    nothing; the next transfers are the software's again."""
    h, sw, serve = addressed()
    serve["on"] = False
    n = len(h.log)
    h.setup(5, set_config(value))
    assert h.data_in(5, 0, tries=1) == (DATA1, b"")
    assert h.data_in(5, 0, tries=1) == (DATA1, b"")
    assert h.log[n:] == [("SETUP", set_config(value).hex(), usb.handshake(usb.PID_ACK)), ZLP, ZLP]
    serve["on"] = True
    for _ in range(4):
        h.idle(h.mh)
    assert sw.requests[-1] == set_config(value).hex()
    nothing_staged(h, sw, f"SET_CONFIGURATION({value})")
    fs.control_write(h, 5, fs.write_req(8), bytes(range(8)))
    assert h.control_read(5, fs.read_req(8), 8) == bytes(range(8))
    assert h.control_read(5, bytes([0x80, 6, 0, 1, 0, 0, 18, 0]), 18) == usbdev.DEVICE_DESC
    nothing_staged(h, sw, "after the round trip")


OTHERS = {   # no-data requests the firmware must leave to the software
    "vendor write, wLength 0 (0x40 0x10)": fs.write_req(0),
    "vendor, bRequest 9 (0x40 0x09)": bytes([0x40, 9, 1, 0, 0, 0, 0, 0]),
    "class, bRequest 9 (0x21 0x09: HID SET_REPORT)": bytes([0x21, 9, 1, 0, 0, 0, 0, 0]),
    "SET_INTERFACE (0x01 0x0B)": bytes([0x01, 11, 0, 0, 0, 0, 0, 0]),
    "SET_FEATURE (0x00 0x03)": bytes([0x00, 3, 1, 0, 0, 0, 0, 0]),
    "SET_ADDRESS (0x00 0x05)": bytes([0x00, 5, 6, 0, 0, 0, 0, 0]),
    "endpoint, bRequest 9 (0x02 0x09)": bytes([0x02, 9, 1, 0, 0, 0, 0, 0]),
}


@pytest.mark.parametrize("after_config", [False, True])
@pytest.mark.parametrize("name", list(OTHERS))
def test_other_requests_wait_for_the_software(name, after_config):
    """With the software held, the status IN of any other no-data request is
    NAKed: the firmware answers SET_CONFIGURATION only (bmRequestType and
    bRequest both checked), and the next SETUP token clears F_CFGZLP, also
    right after a SET_CONFIGURATION whose status stage it answered."""
    h, sw, serve = addressed()
    if after_config:
        h.control_write_nodata(5, set_config(1))
    serve["on"] = False
    h.setup(5, OTHERS[name])
    for i in range(3):
        seen = first_in(h, 5)
        assert seen == [("IN", "nak", None)], f"{name}: IN {i} saw {seen}"
    assert not h.mh.chip.flags & F_CFGZLP


def test_a_bad_setup_leaves_no_answer_behind():
    """A SETUP whose DATA has a bad CRC is not ACKed; F_CFGZLP may be set by
    its first 16 bits, and the host's retry of the whole SETUP (token first)
    clears it before an IN can come. Here the retry is another request: its
    status IN is NAKed while the software is held."""
    h, sw, serve = addressed()
    pkt = bytearray(usb.data_packet(usb.PID_DATA0, set_config(1)))
    pkt[-1] ^= 0x55                                     # bad CRC16
    t = h.send(usb.token(usb.PID_SETUP, 5, 0))
    t = h.send(bytes(pkt), t, h.ipd)
    assert h.listen(t) is None, "a bad SETUP DATA was answered"
    assert h.mh.chip.flags & F_CFGZLP                  # its first 16 bits were SET_CONFIGURATION's
    for _ in range(4):                                  # the software drops the bad record
        h.idle(h.mh)
    serve["on"] = False
    h.setup(5, fs.write_req(0))
    assert first_in(h, 5) == [("IN", "nak", None)]


# ------------------------------------------------------------------ software concurrent with the bus
@pytest.mark.parametrize("value", [1, 0])
def test_first_status_in_with_software_running_concurrently(fs_concurrent, value):
    """The board's case: the software polls over SPI concurrently with the bus
    (test_usb_fs_data.SpiBridge) and the host sends the status IN right after
    the SETUP's ACK. SET_CONFIGURATION's first status IN gets the zero-length
    DATA1 with 0 NAKs at every gap of the sweep; a zero-length write's and an
    8-byte round trip's stay the software's (NAKs allowed), and nothing is
    left staged."""
    h, sw = fs.enumerated()
    b = fs_concurrent(h, sw)
    before = sw.requests.count(set_config(value).hex())    # enumerated() sent SET_CONFIGURATION(1)
    for k, gap in enumerate(fs.GAPS[::3]):
        h.setup(5, set_config(value))
        seen = first_in(h, 5)
        assert seen == [ZLP], f"SET_CONFIGURATION {k}, gap {gap}: the first status IN saw {seen}"
        h.wait_bits(gap)
        fs.checked_write(h, 5, b"", f"zero-length write {k}")
        h.wait_bits(gap)
        data = bytes([k] * 8)
        fs.checked_write(h, 5, data, f"8-byte write {k}")
        assert fs.checked_read(h, 5, 8, f"8-byte read {k}") == data
    h.setup(5, set_config(value))
    assert first_in(h, 5) == [ZLP]
    fs.staged_over_empty(h, b, "after the last SET_CONFIGURATION")
    nothing_staged(h, sw, "after the last SET_CONFIGURATION")
    assert sw.requests.count(set_config(value).hex()) - before == len(fs.GAPS[::3]) + 1


@pytest.fixture
def fs_concurrent():
    made = []

    def make(h, sw):
        made.append(fs.SpiBridge(h, sw))
        return made[-1]
    yield make
    for b in made:
        b.close()


def linux_sequence(text, fs_concurrent):
    """Linux's enumeration as usbmon recorded it on the KV260 (the sequence of
    test/model/usb_fs_enum_sweep.py, fixed gaps: 95, 27 and 569 us before the
    configuration reads and SET_CONFIGURATION), then a vendor round trip, with
    the software polling over SPI concurrently. Returns (log, requests, NAKs
    in SET_CONFIGURATION's status stage)."""
    h, sw, _ = device(text, seed=7)
    fs_concurrent(h, sw)

    def us(t):
        h.wait_bits(int(t * 12))

    def ctl_in(addr, req, mps=8):
        n = req[6] | req[7] << 8
        h.setup(addr, req)
        out = b""
        while len(out) < n:
            _, payload = h.data_in(addr, 0)
            out += payload
            if len(payload) < mps:
                break
        h.data_out(addr, 0, usb.PID_DATA1, b"")
        return out

    us(100)
    assert ctl_in(0, bytes([0x80, 6, 0, 1, 0, 0, 64, 0]), mps=64) == usbdev.DEVICE_DESC[:8]
    us(100)
    for _ in range(int(10 * 48000)):             # port reset: 10 ms of SE0
        h._edge(0b00)
    h.last_eop_j = None
    us(1000)
    h.setup(0, bytes([0, 5, 31, 0, 0, 0, 0, 0]))
    fs.status_in(h, 0, "SET_ADDRESS")
    us(2000)
    assert ctl_in(31, bytes([0x80, 6, 0, 1, 0, 0, 18, 0])) == usbdev.DEVICE_DESC
    us(95)
    assert ctl_in(31, bytes([0x80, 6, 0, 2, 0, 0, 9, 0])) == usbdev.CONFIG_DESC[:9]
    us(27)
    assert ctl_in(31, bytes([0x80, 6, 0, 2, 0, 0, 18, 0])) == usbdev.CONFIG_DESC
    us(569)
    h.setup(31, set_config(1))
    n = len(h.log)
    fs.status_in(h, 31, "SET_CONFIGURATION")
    naks = sum(1 for e in h.log[n:] if e == ("IN", "nak", None))
    us(200)
    fs.checked_write(h, 31, bytes(range(16)), "16-byte write")
    assert fs.checked_read(h, 31, 16, "16-byte read") == bytes(range(16))
    fs.checked_write(h, 31, b"", "zero-length write")
    assert sw.address == 31
    return h.log, sw.requests, naks


def test_linux_enumeration_changes_only_the_set_configuration_status(fs_concurrent):
    """The same host sequence against the firmware with and without the
    pre-stage: the same requests reach the software, and every transaction
    the host sees is the same apart from NAKs (the IN -> NAK turnaround is one
    clock longer, which moves the software's polls against the bus). The
    status stage of SET_CONFIGURATION: one zero-length DATA1 either way, after
    0 NAKs with the pre-stage and at least one without it."""
    new_log, new_req, new_naks = linux_sequence(FW_TEXT, fs_concurrent)
    old_log, old_req, old_naks = linux_sequence(without_prestage(), fs_concurrent)
    assert new_req == old_req
    assert [e for e in new_log if e[:2] != ("IN", "nak")] == [e for e in old_log if e[:2] != ("IN", "nak")]
    assert new_naks == 0
    assert old_naks >= 1


# ------------------------------------------------------------------ the double stage of 2026-11-03
def test_zero_length_write_stages_its_status_once():
    """pe.usbfsdata on the 2026-11-03 board run: a zero-length REQ_WRITE's
    status ZLP was staged twice (handle() set it pending, then service() saw an
    empty, complete incoming buffer and set it again), so a stale DATA0 ZLP
    answered a later request. Fixed in 06b0dd4 (on fe3c7ab already); counted
    here at the stage() calls: one zero-length DATA1 per request."""
    h, sw, _ = addressed()
    h.control_write_nodata(5, set_config(1))
    staged = []
    stage = sw.stage
    sw.stage = lambda pid, payload: (staged.append((pid, payload)), stage(pid, payload))
    for k in range(3):
        fs.control_write(h, 5, fs.write_req(0))
        for _ in range(6):
            h.idle(h.mh)
        assert staged == [(usb.PID_DATA1, b"")] * (k + 1), f"request {k}: staged {staged}"
    nothing_staged(h, sw, "after three zero-length writes")


# ------------------------------------------------------------------ the cost on the receive side
@pytest.mark.parametrize("seed, ppm", [(0, -2500), (1, 0), (2, 2500)])
def test_setup_capture_keeps_up(seed, ppm):
    """The SETUP's first 16 bits take 4 clocks each (one more than the DATA
    loop), so the line unit holds a bit more while they come in: at most 4
    bits during a SETUP's DATA (3 before; OUT DATA still 3), and none when the
    firmware reaches `dend`, so the ACK path starts as it did."""
    h0, _, _ = device(seed=seed)
    mh = h0.mh
    h = FsHost(mh, seed=seed, jitter_ns=5, ppm=ppm)
    prog = asm.assemble(FW_TEXT, usbdev.defines())
    line, core, step = mh.chip.line, mh.chip.cores[0], mh.chip.step
    lo, hi = prog.labels["cfg_bit"] - 2, prog.labels["dend"]    # `jmp !f F_SETUP, dbit` .. the loop's end
    seen = {"peak": 0, "at_dend": []}

    def watched(*a, **k):
        r = step(*a, **k)
        if lo <= core.pc < hi:
            seen["peak"] = max(seen["peak"], len(line.rq))
        elif core.pc == hi:
            seen["at_dend"].append(len(line.rq))
        return r
    mh.chip.step = watched
    peaks = {}
    for name, fn in (("SET_CONFIGURATION", lambda: h.setup(0, set_config(1))),
                     ("GET_DESCRIPTOR", lambda: h.setup(0, bytes([0x80, 6, 0, 1, 0, 0, 18, 0]))),
                     ("OUT, 8 bytes of 0xFF", lambda: h.data_out(0, 0, usb.PID_DATA1, bytes([0xFF] * 8))),
                     ("OUT, zero-length", lambda: h.data_out(0, 0, usb.PID_DATA1, b""))):
        seen["peak"], seen["at_dend"] = 0, []
        fn()
        peaks[name] = seen["peak"]
        assert seen["at_dend"] and not any(seen["at_dend"]), f"{name}: bits still queued at dend {seen['at_dend']}"
        mh.step(200)
        mh.pop_all(0)
    assert peaks == {"SET_CONFIGURATION": 4, "GET_DESCRIPTOR": 4, "OUT, 8 bytes of 0xFF": 3,
                     "OUT, zero-length": 3}, peaks
