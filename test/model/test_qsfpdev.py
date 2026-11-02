"""The QSFP-style stand-in (pe.qsfpdev on firmware/i2c_device.pasm,
docs/bench-firmware.md) on the cycle model.

The firmware is the existing i2c_device, unchanged. The reader is the
pins-only I2C master pe.i2cbus at 396.8 kHz (half = 63 clocks at 50 MHz), and
the offsets below are literals from SFF-8636 Rev 2.9's tables, not the
library's constants: a table typed twice, on purpose."""
import pathlib

import pytest

from pe import asm, host, i2cbus, postfreeze as pf, qsfpdev, sigrok, sim, timing
from pe.modelhost import ModelHost

import test_timing_check as TC

FW = pathlib.Path(__file__).resolve().parents[2] / "firmware"
SDA, SCL = 0, 1
HALF = 63
A = 0x50
CLK_NS = 20.0
PROG = asm.assemble((FW / "i2c_device.pasm").read_text(), {"SDA": SDA, "SCL": SCL})


class Rig:
    """i2c_device on core 1, the device model on the host side with a latency
    of `latency` clocks between an event appearing and its answer; the master
    on the wire. `holds` are the SCL low periods the chip itself drove."""

    def __init__(self, dev=None, latency=0, on_read=None, trace=False):
        self.dev = dev or qsfpdev.QsfpDevice()
        self.latency, self.on_read = latency, on_read
        self.mh = mh = ModelHost()
        mh.apply(host.pin_owner_writes({SDA: 1, SCL: 1}))
        mh.write(sim.A_POUT, 0b11)
        mh.write(sim.A_OD, 0b11)
        mh.pull_up = 0b11
        mh.apply(self.dev.setup_writes(1, PROG, SDA, SCL))
        self.tracer = timing.Tracer(mh.chip, 1, 256) if trace else None
        mh.start(2)
        self.queue, self.holds, self.t_hold, self.n_read = [], [], None, 0
        self.log = [(0.0, 3)]

    def run(self, ops):
        mh = self.mh
        m = i2cbus.Master(mh.bus_uio, SDA, SCL, HALF)
        gen, wait = m.run(ops), 0
        for _ in range(4_000_000):
            if wait == 0:
                try:
                    wait = next(gen)
                except StopIteration:
                    break
            wait -= 1
            mh.ext_oe, mh.uio = m.low, 0
            self._tick()
        else:
            raise AssertionError("master did not finish")
        mh.ext_oe = 0
        for _ in range(self.latency + 300):
            self._tick()
        return m.results

    def _tick(self):
        mh = self.mh
        mh.step()
        c = mh.chip.cycle
        for ev in mh.pop_all(1):
            self.queue.append((c + self.latency, ev))
        while self.queue and self.queue[0][0] <= c:
            ev = self.queue.pop(0)[1]
            if ev >> 8 == 3 and self.on_read:
                self.on_read(self)
            for b in self.dev.handle(ev):
                mh.push(1, b)
        pulling = mh.chip.outputs()[2] >> SCL & 1
        if pulling and self.t_hold is None:
            self.t_hold = c
        elif not pulling and self.t_hold is not None:
            self.holds.append(c - self.t_hold)
            self.t_hold = None
        bus = mh.bus_uio() & 3
        if bus != self.log[-1][1]:
            self.log.append((c * CLK_NS, bus))


def read(rig, offset, n):
    return rig.run([("wr", A, [offset], n)])[0]


def test_identifier_status_temperature_supply():
    """The lower-page fields at their literal offsets."""
    r = Rig(qsfpdev.QsfpDevice(temperature=47.25, vcc=3.3))
    assert read(r, 0, 3) == [0x11, 0x08, 0x02]        # QSFP28, rev 2.8+, IntL bit 1, paged, data ready
    assert read(r, 22, 2) == [47, 0x40]                # 47.25 C = 0x2F40
    assert read(r, 26, 2) == [0x80, 0xE8]              # 3.3 V = 33000 x 100 uV
    assert qsfpdev.decode_temperature(47, 0x40) == 47.25 and qsfpdev.decode_vcc(0x80, 0xE8) == 3.3


@pytest.mark.parametrize("c,raw", [(0.0, b"\x00\x00"), (25.5, b"\x19\x80"), (-40.0, b"\xd8\x00"), (125.0, b"\x7d\x00"),
                                   (-1 / 256, b"\xff\xff"), (-128.0, b"\x80\x00"), (32767 / 256, b"\x7f\xff")])
def test_temperature_format(c, raw):
    """Signed 1/256 C, MSB first, at the corners, over the bus."""
    r = Rig(qsfpdev.QsfpDevice(temperature=c))
    assert bytes(read(r, 22, 2)) == raw
    assert qsfpdev.decode_temperature(*raw) == c


def test_field_ranges():
    for bad in (128.0, -128.1, 1000):
        with pytest.raises(ValueError):
            qsfpdev.encode_temperature(bad)
    for bad in (-0.001, 6.5536):
        with pytest.raises(ValueError):
            qsfpdev.encode_vcc(bad)
    assert qsfpdev.encode_vcc(6.5535) == b"\xff\xff"
    with pytest.raises(ValueError):
        qsfpdev.QsfpDevice(vendor="x" * 17)


def test_vendor_fields_at_their_offsets():
    """Vendor name 148-163, OUI 165-167, part 168-183, rev 184-185, serial
    196-211, date 212-219: exact bytes, space padded."""
    r = Rig(qsfpdev.QsfpDevice(vendor="ACME OPTICS", part="QSFP-100G-X", rev="B1", serial="SN42", date="26110200",
                               oui=b"\x12\x34\x56"))
    assert bytes(read(r, 148, 16)) == b"ACME OPTICS     "
    assert bytes(read(r, 165, 3)) == b"\x12\x34\x56"
    assert bytes(read(r, 168, 16)) == b"QSFP-100G-X     "
    assert bytes(read(r, 184, 2)) == b"B1"
    assert bytes(read(r, 196, 16)) == b"SN42            "
    assert bytes(read(r, 212, 8)) == b"26110200"
    assert read(r, 128, 1) == [0x11]                   # byte 128 = byte 0 (6.2.1)


def test_check_codes_and_parser():
    """CC_BASE at 191 = sum of 128..190 low 8 bits, CC_EXT at 223 = sum of
    192..222, computed here over the bytes as read; one wrong byte fails the
    parser."""
    r = Rig()
    page = read(r, 128, 128)
    assert page[191 - 128] == sum(page[128 - 128:191 - 128]) & 0xFF
    assert page[223 - 128] == sum(page[192 - 128:223 - 128]) & 0xFF
    p = qsfpdev.parse_upper00(page)
    assert p == {"identifier": 0x11, "vendor": "WIRELINGO", "oui": b"\0\0\0", "part": "BENCH-STANDIN", "rev": "A0",
                 "serial": "000001", "date": "26110200"}
    for a in (148, 190, 191):
        bad = list(page)
        bad[a - 128] ^= 1
        with pytest.raises(ValueError):
            qsfpdev.parse_upper00(bad)
    bad = list(page)
    bad[200 - 128] ^= 0x40
    with pytest.raises(ValueError):
        qsfpdev.parse_upper00(bad)
    with pytest.raises(ValueError):
        qsfpdev.parse_upper00(page[:127])


def test_everything_else_reads_zero():
    """The list of modelled bytes, typed here independently: every other byte
    of the lower page and of upper page 00h reads 0."""
    r = Rig(qsfpdev.QsfpDevice(oui=b"\x01\x02\x03"))
    lower = read(r, 0, 128)
    upper = read(r, 128, 128)
    assert len(lower) == 128 and len(upper) == 128
    keep = {0, 1, 2, 22, 23, 26, 27}
    assert [a for a in range(128) if lower[a] and a not in keep] == []
    keep = {128, 191, 223} | set(range(148, 164)) | set(range(165, 186)) | set(range(196, 220))
    assert [a for a in range(128, 256) if upper[a - 128] and a not in keep] == []
    assert set(qsfpdev.LOWER_MODELLED) == {0, 1, 2, 22, 23, 26, 27, 86, 127}
    assert set(qsfpdev.UPPER00_MODELLED) == keep


def test_page_select_and_page_03():
    """Byte 127: 3 maps page 03h, read back; thresholds at their offsets, MSB
    first; 0 maps page 00h again; the lower page does not change."""
    r = Rig()
    low0 = read(r, 0, 3)
    r.run([("w", A, [127, 3])])
    assert read(r, 127, 1) == [3]
    t = read(r, 128, 8)
    assert t == [75, 0, 0xFB, 0, 70, 0, 0, 0]          # 75 C, -5 C, 70 C, 0 C
    v = read(r, 144, 8)
    assert [a << 8 | b for a, b in zip(v[::2], v[1::2])] == [36300, 29700, 34650, 31350]   # 3.63, 2.97, 3.465, 3.135 V
    assert read(r, 148, 2) != list(b"WI")
    assert read(r, 0, 3) == low0
    r.run([("w", A, [127, 0])])
    assert bytes(read(r, 148, 9)) == b"WIRELINGO"


@pytest.mark.parametrize("page", [0x01, 0x02, 0x04, 0x20, 0x21, 0x22, 0x7F, 0x80, 0xFF])
def test_unsupported_page_reverts_to_zero(page):
    """SFF-8636 6.1, 6.2.11: a value not supported is not accepted, the byte
    reverts to 00h, operations go to upper page 00h. From page 03h, so that
    the revert is seen happening."""
    r = Rig()
    r.run([("w", A, [127, 3])])
    assert read(r, 127, 1) == [3]
    r.run([("w", A, [127, page])])
    assert read(r, 127, 1) == [0]
    assert bytes(read(r, 148, 9)) == b"WIRELINGO"


def test_address_counter_rolls_inside_the_page():
    """5.3.1: from the last byte of the 128-byte page to the first of the same
    page: 127 -> 0 and 255 -> 128, not 127 -> 128 or 255 -> 0."""
    r = Rig()
    assert read(r, 126, 4) == [0, 0, 0x11, 0x08]       # 126, 127, 0, 1
    upper = read(r, 128, 128)
    assert read(r, 254, 4) == [upper[126], upper[127], upper[0], upper[1]]
    assert upper[0] == 0x11


def test_current_address_read_continues():
    """The counter persists between operations."""
    r = Rig()
    assert read(r, 0, 1) == [0x11]
    assert r.run([("r", A, 2)])[0] == [0x08, 0x02]
    assert r.dev.ptr == 3


def test_write_takes_effect_at_stop():
    """Tx disable (byte 86): bits 3-0 stored at STOP; the reserved bits 7-4
    read 0."""
    r = Rig()
    assert read(r, 86, 1) == [0]
    r.run([("w", A, [86, 0xFA])])
    assert read(r, 86, 1) == [0x0A]


def test_write_longer_than_four_bytes_is_accepted():
    """A limit, not the specification: it requires 4, and does not say what a
    longer one does. Here every byte lands."""
    r = Rig()
    r.run([("w", A, [82, 1, 2, 3, 4, 0x05])])
    assert read(r, 86, 1) == [0x05] and r.dev.ignored == [(82, 1), (83, 2), (84, 3), (85, 4)]


def test_repeated_start_aborts_a_write():
    """5.3.2: a repeated START in place of the STOP: the data is discarded."""
    r = Rig()
    r.run([("w", A, [86, 0x05])])
    assert r.run([("wr", A, [86, 0x0F], 1)])[0] is not None
    assert read(r, 86, 1) == [0x05]


def test_read_only_bytes_ignore_writes():
    """Writes to monitors, ID bytes and unmodelled control bytes change
    nothing and are noted; they are ACKed (i2c_device ACKs every byte)."""
    r = Rig()
    before = read(r, 22, 2), read(r, 148, 4), read(r, 93, 1)
    res = r.run([("w", A, [22, 1, 2]), ("w", A, [148, 0x41]), ("w", A, [93, 3])])
    assert all(x == [True] * len(x) for x in res)
    assert (read(r, 22, 2), read(r, 148, 4), read(r, 93, 1)) == before
    assert r.dev.ignored == [(22, 1), (23, 2), (148, 0x41), (93, 3)]


def test_other_address_nacked():
    r = Rig()
    assert r.run([("w", 0x51, [0])]) == [None]
    assert r.run([("wr", 0x51, [0], 1)]) == [None]


def test_data_not_ready_flag():
    r = Rig()
    r.dev.set_data_not_ready(True)
    assert read(r, 2, 1) == [0x03]
    r.dev.set_data_not_ready(False)
    assert read(r, 2, 1) == [0x02]


def test_two_byte_read_is_coherent():
    """6.2.4: no partly updated field. The temperature changes between the
    MSB and the LSB of a read, across a carry: the read gets the old value
    whole (0x1FFF), the next the new one whole (0x2000). Without the view
    the master would get 0x1F00."""
    r = Rig(qsfpdev.QsfpDevice(temperature=0x1FFF / 256))

    def bump(rig):
        rig.n_read += 1
        if rig.n_read == 2:
            rig.dev.set_temperature(0x2000 / 256)
    r.on_read = bump
    assert read(r, 22, 2) == [0x1F, 0xFF]
    assert read(r, 22, 2) == [0x20, 0x00]


def test_clock_hold_exact_with_a_prompt_host():
    """A host that answers in the same clock as the event: every read byte's
    SCL hold is exactly 26 clocks (520 ns at 50 MHz), against SFF-8636's
    500 us: the firmware's 22 (test_device_response_times, with the byte
    already queued) and the 4 this model host takes to pop and push."""
    r = Rig()
    read(r, 148, 8)
    assert set(r.holds) == {26}, r.holds


@pytest.mark.parametrize("latency", [100, 1000, 10000, 24900])
def test_clock_hold_follows_the_host(latency):
    """The hold is the host's latency + 23 clocks, exactly; a latency of 24900
    clocks at 50 MHz gives 498.46 us, inside the 500 us."""
    r = Rig(latency=latency)
    assert read(r, 148, 4) == list(b"WIRE")
    assert set(r.holds) == {latency + 23}
    assert max(r.holds) * 0.02 <= qsfpdev.T_CLOCK_HOLD_US


def test_slow_host_breaks_the_clock_hold_limit():
    """Negative: firmware cannot enforce the 500 us; a host 600 us late holds
    SCL longer and the measurement shows it."""
    r = Rig(latency=30000)
    read(r, 148, 2)
    assert set(r.holds) == {30023} and 30023 * 0.02 > qsfpdev.T_CLOCK_HOLD_US


def i2c_device_check():
    return timing.check_source((FW / "i2c_device.pasm").read_text(), {"SDA": SDA, "SCL": SCL},
                               sidecar=(FW / "i2c_device.timing").read_text())


def test_i2c_device_deadlines():
    """firmware/i2c_device.timing (a sidecar: the program is not edited): a
    read byte's MSB is on SDA exactly 16 ticks before SCL is released, and
    the stretch that starts each read byte lasts at least 22 ticks."""
    chk, results = i2c_device_check()
    assert not chk.errors
    for r in results:
        print(r.report())
    assert {r.deadline.name: (r.status, r.lo) for r in results} == {"rd_setup": ("PASS", 16), "rd_hold": ("PASS", 22)}
    assert {r.deadline.name: r.hi for r in results}["rd_setup"] == 16


def test_i2c_device_sound():
    r = Rig(trace=True, latency=50)
    read(r, 148, 8)
    chk, results = i2c_device_check()
    s = TC.soundness(chk, results, r.tracer, label="i2c_device")
    TC.show(s, results)
    assert all(s[x.deadline.name][0] == 8 for x in results), s


@pytest.mark.skipif(sigrok.cli() is None, reason=sigrok.INSTALL_HINT)
def test_sigrok_i2c():
    """sigrok's i2c decoder on the bus: the master's reads of identifier,
    vendor name and temperature, tokens and bytes, no warnings."""
    r = Rig(qsfpdev.QsfpDevice(vendor="WIRELINGO", temperature=25.5))
    read(r, 0, 1)
    read(r, 148, 16)
    read(r, 22, 2)
    tr = sigrok.Trace()
    pf.to_trace(tr, pf.bit_edges(r.log, 1), "scl")
    pf.to_trace(tr, pf.bit_edges(r.log, 0), "sda")
    tr.end(round((r.mh.chip.cycle + 1000) * CLK_NS * 1000))
    toks = [k for _, k in sigrok.i2c(tr, "scl", "sda", res_ps=10_000)]
    assert not [k for k in toks if isinstance(k, tuple) and k[0] == "WARN"], toks
    reads, cur = [], None
    for k in toks:
        if k == "Sr":
            cur = []
            reads.append(cur)
        elif isinstance(k, tuple) and k[0] == "DR":
            cur.append(k[1])
    writes = [k[1] for k in toks if isinstance(k, tuple) and k[0] == "DW"]
    print(writes, reads)
    assert writes == [0, 148, 22]
    assert [bytes(x) for x in reads] == [b"\x11", b"WIRELINGO       ", b"\x19\x80"]
    assert toks.count(("AW", A)) == 3 and toks.count(("AR", A)) == 3 and toks.count("NACK") == 3
