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
from pe.profile import TAPEOUT
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
SHORT_SUFFIXES = (
    pytest.param("0", id="short-zero"),
    *[pytest.param(bits, id="short-" + bits)
      for bits in ("01", "10", "001", "101", "110", "1111")],
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


def partial_ack_symbols(suffix):
    """Chronological decoded bits, raw NRZI before EOP; no byte padding/stuffing."""
    assert suffix and set(suffix) <= {"0", "1"}
    states = ack_symbols()[:-3]
    level = states[-1]
    for bit in suffix:
        if bit == "0":
            level = usb.K if level == usb.J else usb.J
        states.append(level)
    states += [usb.SE0, usb.SE0, usb.J]
    prefix = list(usb.bits_lsb_first(b"\x80\xd2"))
    assert usb.nrzi_destuff([usb.J] + states) == prefix + [int(b) for b in suffix]
    assert len(states) == 16 + len(suffix) + 3
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


class TapeoutModelHost(ModelHost):
    """Explicit shipping geometry; these cases use only core0 and bank0."""
    def __init__(self):
        self.chip = sim.Chip(ncores=TAPEOUT.ncores, nbanks=TAPEOUT.nbanks,
                             bank_words=TAPEOUT.bank_words,
                             fifo_depth=TAPEOUT.fifo_depth)
        self.ui = 0
        self.uio = 0
        self.ext_oe = 0
        self.pull_up = 0


def device(variant, phase, gap_bits=4):
    mh = TapeoutModelHost()
    assert (mh.chip.ncores, mh.chip.nbanks, mh.chip.bank_words,
            mh.chip.fifo_depth) == (2, 2, 256, 8)
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
        peer = ControlHost(mh, bit=32, keepalive=False, retries=0, gap_bits=gap_bits)
    else:
        peer = FsControlHost(mh, sof=False, retries=0, seed=phase+1,
                             jitter_ns=0, ppm=0, gap_bits=gap_bits,
                             ipd_bits=gap_bits)
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
    exercise_ack(variant, phase, ack_symbols(tail, stretch), accepted)


@pytest.mark.parametrize("variant", VARIANTS)
@pytest.mark.parametrize("phase", (0, 1))
@pytest.mark.parametrize("suffix", SHORT_SUFFIXES)
def test_partial_ack(variant, phase, suffix, record_property):
    exercise_ack(variant, phase, partial_ack_symbols(suffix), False, record_property)


def exercise_ack(variant, phase, states, accepted, record_property=None):
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
        send_ack(peer, states)
        if software:
            # Observe firmware completion before ordinary service clears the flag.
            got_ack = bool(mh.read(sim.A_FSET) & usbdev.F_GOTACK)
            # A fixed delay exceeds the existing software's retry timer; no
            # privileged device/peer completion schedules re-staging.
            mh.step(5000)
            software.service()
        reply = poll(peer)
        if record_property:
            record_property("next_reply", reply.hex())
            record_property("expected_reply", (SECOND if accepted else FIRST).hex())
            if software:
                record_property("F_GOTACK", got_ack)
        assert reply == (SECOND if accepted else FIRST)
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


class PadEdges:
    """Observe supplied resolved pads at actual model clocks; no DUT state writes."""
    def __init__(self, mh, record_property):
        self.chip = mh.chip
        self.edges = []
        self.record_property = record_property

    def __enter__(self):
        self.original = self.chip.step

        def observed(*args, **kwargs):
            pads = args[1] & 3
            if not self.edges or pads != self.edges[-1][1]:
                self.edges.append((self.chip.cycle, pads))
            return self.original(*args, **kwargs)

        self.chip.step = observed
        return self

    def __exit__(self, *exc):
        del self.chip.step
        self.record_property("resolved_pad_edges", self.edges)


def minimum_ack(peer, states):
    if isinstance(peer, FsControlHost):
        # The next send schedules its K from EOP J using ipd_bits=2.
        peer.fs.send(usb.handshake(usb.PID_ACK), states=states)
    else:
        peer._drive(states)
        # _drive already paid the trailing J bit: one more gives J->K=2 bits.
        peer._steps(peer.gap_bits - 1)


@pytest.mark.parametrize("variant", ("enum-low", "enum-full"))
@pytest.mark.parametrize("phase", (0, 1))
def test_partial_ack_minimum_first_retry(variant, phase, record_property):
    mh, peer, software = device(variant, phase, gap_bits=2)
    assert software is None
    with LoadingHostIdle(mh):
        peer.setup(0, DESCRIPTOR_REQUEST)
        assert poll(peer) == FIRST
        peer._steps(4)
        mh.step(phase)
        with PadEdges(mh, record_property) as pads:
            minimum_ack(peer, partial_ack_symbols("0"))
            # One first IN and one response: no eventual-success/retry path.
            retry = poll(peer)
            assert retry == FIRST
            peer._steps(4)
            minimum_ack(peer, ack_symbols())
            assert poll(peer) == SECOND
        edges = pads.edges
        eop_j = next(t for (prev_t, prev), (t, value) in zip(edges, edges[1:])
                     if prev == 0 and value == 2)
        token_k = next(t for t, value in edges if t > eop_j and value == 1)
        clocks = token_k - eop_j
        assert peer.retries == 0 and peer.gap_bits == 2
        assert 2 * peer.bit <= clocks <= 2 * peer.bit + 2
        record_property("EOP_J_clock", eop_j)
        record_property("first_retry_K_clock", token_k)
        record_property("EOP_to_first_retry_clocks", clocks)
        record_property("EOP_to_first_retry_bits", clocks / peer.bit)
        record_property("first_retry", retry.hex())
