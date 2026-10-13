"""ACK length and final-bit dribble on production USB firmware.

USB 2.0 §8.4.5 excludes an extra handshake byte. §7.1.9.1 requires
accepting up to six bit times without a transition immediately before EOP.
The positive peer holds the final NRZI symbol longer; it does not encode
another byte. All transfer/toggle verdicts use the next external DATA packet.
"""
from contextlib import nullcontext
from pathlib import Path

import pytest

from pe import asm, host, sim, usb, usbdev, usbenum
from pe.modelhost import ModelHost
from pe.usbfs import FsControlHost
from pe.usbhost import ControlHost

FW = Path(__file__).resolve().parents[2] / "firmware"
DESCRIPTOR_REQUEST = bytes.fromhex("8006000100001200")
FIRST = usb.data_packet(usb.PID_DATA1, usbdev.DEVICE_DESC[:8])
SECOND = usb.data_packet(usb.PID_DATA0, usbdev.DEVICE_DESC[8:16])
VARIANTS = ("enum-low", "enum-full", "fifo-full", "fifo-low")
ACK_CASES = (
    pytest.param(b"", 0, True, id="nominal"),
    pytest.param(b"", 1, True, id="dribble-4-bit-run"),
    pytest.param(b"", 2, True, id="dribble-5-bit-run"),
    pytest.param(b"", 3, True, id="dribble-6-bit-run"),
    pytest.param(b"\x00", 0, False, id="extra-00"),
    pytest.param(b"\x55", 0, False, id="extra-55"),
    pytest.param(b"\xff", 0, False, id="extra-ff"),
    pytest.param(b"\xff\xff\xff", 0, False, id="extra-three-bytes"),
)


def ack_symbols(tail=b"", stretch=0):
    """Explicit peer waveform: stretch the final level, keep ordinary EOP."""
    assert not (tail and stretch)
    states = usb.encode(usb.handshake(usb.PID_ACK) + tail)
    assert states[-3:] == [usb.SE0, usb.SE0, usb.J]
    if stretch:
        last = states[-4]
        run = 0
        for symbol in reversed(states[:-3]):
            if symbol != last:
                break
            run += 1
        assert run == 3 and run + stretch <= 6
        states = states[:-3] + [last] * stretch + states[-3:]
    # Independently check the bytes/bits represented by the generated source.
    bits = usb.nrzi_destuff([usb.J] + states)
    prefix = list(usb.bits_lsb_first(b"\x80\xd2"))
    assert bits[:16] == prefix
    if stretch:
        assert bits[16:] == [1] * stretch
    else:
        assert bits == prefix + list(usb.bits_lsb_first(tail))
    return states


class LoadingHostIdle:
    """Autonomous enumeration must receive no active loading-host service."""
    OPS = ("write", "snap", "commit", "cancel", "read", "push", "pop_all", "apply", "start")

    def __init__(self, mh):
        self.mh = mh

    def __enter__(self):
        for name in self.OPS:
            setattr(self.mh, name, self.refuse)
        return self

    def refuse(self, *args, **kwargs):
        raise AssertionError("loading-host operation during autonomous USB transfer")

    def __exit__(self, *exc):
        for name in self.OPS:
            delattr(self.mh, name)


def device(variant, phase):
    mh = ModelHost()
    software = None
    if variant.startswith("enum"):
        speed = "low" if variant == "enum-low" else "full"
        mh.apply(usbenum.load_writes(usbenum.assemble(speed), speed))
    else:
        filename = "usb_ls_dev.pasm" if variant == "fifo-low" else "usb_fs_dev.pasm"
        program = asm.assemble((FW / filename).read_text(), usbdev.defines())
        mh.apply(host.load_program(0, program, 0, clkdiv=1 << 8))
        mh.write(sim.core_reg(0, sim.C_CRCCTRL), usbdev.CRCCTRL)
        bit = 32 if variant == "fifo-low" else 4
        mh.write(sim.A_LINEDIV, (bit << 16) | (bit << 8))
        mh.write(sim.A_LINEPINS, 0)
        mh.write(sim.A_LINECFG, usbenum.LINECFG)
        mh.start(1)
        software = usbdev.DeviceSoftware(mh, program, usbdev.DESCRIPTORS, 0, 0)
    mh.pull_up = 2
    mh.step(100)
    if variant in ("enum-low", "fifo-low"):
        peer = ControlHost(mh, bit=32, keepalive=False, retries=0)
    else:
        peer = FsControlHost(mh, sof=False, retries=0, seed=phase+1,
                             jitter_ns=0, ppm=0)
    assert mh.chip.sync_bypass == 0
    return mh, peer, software


def poll(peer):
    peer._send(usb.token(usb.PID_IN, 0, 0))
    packet = peer._response()
    assert packet is not None and len(packet) >= 3, "missing complete DATA reply"
    assert packet[0] in (usb.pid_byte(usb.PID_DATA0), usb.pid_byte(usb.PID_DATA1))
    assert packet[-2:] == usb.crc16(packet[1:-2]), "reply CRC16"
    return packet


def send_ack(peer, states):
    if isinstance(peer, FsControlHost):
        peer.fs.send(usb.handshake(usb.PID_ACK), states=states)
    else:
        peer._drive(states)
    peer.gap()


def test_ack_stretch_is_a_final_level_hold():
    for stretch in (1, 2, 3):
        states = ack_symbols(stretch=stretch)
        assert states[:-3] == ack_symbols()[:-3] + [states[-4]] * stretch
        assert len(usb.nrzi_destuff([usb.J]+states)) < 24
    for tail in (b"\x00", b"\x55", b"\xff"):
        assert len(usb.nrzi_destuff([usb.J]+ack_symbols(tail))) == 24


@pytest.mark.parametrize("variant", VARIANTS)
@pytest.mark.parametrize("phase", (0, 1))
@pytest.mark.parametrize("tail,stretch,accepted", ACK_CASES)
def test_ack_length_and_dribble(variant, phase, tail, stretch, accepted):
    """Bad ACK retries identical bytes; good/dribbled ACK advances cursor/toggle."""
    mh, peer, software = device(variant, phase)
    guard = LoadingHostIdle(mh) if software is None else nullcontext()
    with guard:
        peer.setup(0, DESCRIPTOR_REQUEST)
        if software:
            software.service()  # normal SETUP record/packet staging API
        assert poll(peer) == FIRST
        if software:
            software.service()  # record when the staged packet was sent
        peer._steps(4)
        mh.step(phase)
        send_ack(peer, ack_symbols(tail, stretch))
        if software:
            # Observe firmware completion before ordinary service clears the flag.
            got_ack = bool(mh.read(sim.A_FSET) & usbdev.F_GOTACK)
            # A fixed delay exceeds the existing software's retry timer; no
            # privileged device/peer completion schedules re-staging.
            mh.step(5000)
            software.service()
        assert poll(peer) == (SECOND if accepted else FIRST)
        if software:
            assert got_ack == accepted
            assert not software.words, "unwanted handshake tail entered RX records"
        if not accepted:
            # The retried DATA is still pending: one ordinary ACK must advance
            # it exactly once, rather than abandon or corrupt the transfer.
            if software:
                software.service()
            peer._steps(4)
            send_ack(peer, ack_symbols())
            if software:
                mh.step(100)
                software.service()
            assert poll(peer) == SECOND
