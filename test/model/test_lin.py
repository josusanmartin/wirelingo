"""LIN commander and responder (firmware/lin_commander.pasm,
lin_responder.pasm, post-freeze) on the cycle model.

The bus: each node's TXD (1 = recessive) wired-AND, fed back as RXD (ui0) to
both cores, as a LIN transceiver does. Judges: pe.postfreeze.lin_decode (pins
only: break, 8N1 bytes), an independent commander waveform model and an
independent responder model (pe.postfreeze.LinNode) at their own bit rates,
the checksum and PID rules computed independently in pe.postfreeze, and
sigrok's uart + lin decoders.

Most runs clock the model at 5 MHz (commander CLKDIV 2.504, responder 2.5):
the programs count ticks, and the bit timing (104 ticks per bit) is the same
as at 50 MHz; test_lin_commander_50mhz checks the commander there."""
import pathlib

import pytest

from pe import asm, host, postfreeze as pf, sigrok, sim
from pe.modelhost import ModelHost

FW = pathlib.Path(__file__).resolve().parents[2] / "firmware"
RX = 16                    # ui0: the bus (RXD) for both cores
TXC, TXR = 8, 9            # uo0: commander TXD, uo1: responder TXD
BAUD = 19200
BIT = 1e9 / BAUD
TB = 104
CMD_DEFS = {"TX": TXC, "RX": RX, "TB": TB, "BTMO": 4 * TB}
RSP_DEFS = {"TX": TXR, "RX": RX, "BRK": 11 * 104, "TMIN": 80, "TMAX": 130, "BTMO": 4 * 104}
F_SLOW = 5e6


class Bus:
    def __init__(self, f_clk=F_SLOW, commander=True, responder=True, rsp_clock_err=0.0, nodes=()):
        self.mh = mh = ModelHost()
        self.period = 1e9 / f_clk
        if commander:
            prog = asm.assemble((FW / "lin_commander.pasm").read_text(), CMD_DEFS)
            mh.apply(host.load_program(0, prog, out_pins=(TXC, 1), in_pins=RX,
                                       clkdiv=host.clkdiv_for(f_clk, BAUD * TB)))
        if responder:
            prog = asm.assemble((FW / "lin_responder.pasm").read_text(), RSP_DEFS)
            # its own clock is off by rsp_clock_err: it ticks at 2 MHz * (1 + err)
            mh.apply(host.load_program(1, prog, out_pins=(TXR, 1), in_pins=RX,
                                       clkdiv=host.clkdiv_for(f_clk, 2e6 * (1 + rsp_clock_err))))
            mh.write(sim.core_reg(1, sim.C_CRCCTRL), 15 << 16)      # CRC = a 16-bit scratch register
        mh.apply(host.pin_owner_writes({TXC: 0, TXR: 1}))
        mh.write(sim.A_POUT, (1 << TXC) | (1 << TXR))
        mh.ui = 1
        mh.start((1 if commander else 0) | (2 if responder else 0))
        self.nodes = list(nodes)
        self.ext = []                       # [(t_until, level)] an external waveform
        self.log = pf.PinLog(mh, lambda m: m.ui & 1, self.period)
        self.recs = {0: [], 1: []}

    def drive(self, wave):
        """Queue a waveform after the one already queued (or now)."""
        t = max([self.log.now()] + [e[0] for e in self.ext[-1:]])
        for lvl, d in wave:
            t += d
            self.ext.append((t, lvl))

    def run(self, ns):
        mh, end = self.mh, self.log.now() + ns
        po = lambda: mh.chip.pin_out
        while self.log.now() < end:
            t = self.log.now()
            while self.ext and t >= self.ext[0][0]:
                self.ext.pop(0)
            ext = self.ext[0][1] if self.ext else 1
            p = po()
            bus = (p >> TXC) & (p >> TXR) & ext & 1
            for n in self.nodes:
                bus &= n.step(t, mh.ui & 1)
            mh.ui = bus
            self.log.step()
            for c in (0, 1):
                if mh.chip.cores[c].rxf:
                    self.recs[c] += self.log.pop_all(c)

    def frames(self, bit_ns=BIT):
        return pf.lin_decode(self.log.edges(), self.log.now(), bit_ns)


def frame_bytes(ident, data, enhanced=True):
    pid = pf.lin_pid(ident)
    return bytes([0x55, pid] + list(data) + [pf.lin_checksum(pid, data, enhanced)])


def test_lin_pid_and_checksum_rules():
    """The helpers' rules against LIN 2.x examples: PIDs of IDs 0, 0x3C, 0x3D,
    0x3F and a checksum with an end-around carry."""
    assert [pf.lin_pid(i) for i in (0, 0x3C, 0x3D, 0x3F, 0x10)] == [0x80, 0x3C, 0x7D, 0xBF, 0x50]
    assert pf.lin_checksum(0x4A, [0x55, 0x93, 0xE5], enhanced=True) == 0xE6
    assert pf.lin_checksum(0, [0xFF, 0xFF], enhanced=False) == 0x00


def test_lin_commander_and_responder():
    """Both firmwares on one bus: the commander publishes (enhanced and
    classic checksums), the responder publishes a staged response the
    commander subscribed to, a staged response waits through other headers,
    and an unanswered header times out. Every frame on the bus is decoded
    pins-only and checked byte for byte."""
    bus = Bus()
    mh = bus.mh
    staged_a = bytes([0xDE, 0xAD, 0xBE, 0xEF, 1, 2, 3, 4])
    for w in pf.lin_stage(0x22, staged_a):
        mh.push(1, w)
    plan = [("pub", 0x10, bytes([0x11, 0x22, 0x33, 0x44]), True),
            ("sub", 0x05, 3, True),                      # nobody answers 0x05
            ("pub", 0x3C, bytes([0x7F, 6, 0xB6, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF]), False),
            ("sub", 0x22, 8, True),                      # the responder's staged response
            ("pub", 0x01, bytes([0x80]), True)]
    want_bus = []
    for kind, ident, x, enh in plan:
        words = pf.lin_command(ident, data=x, enhanced=enh) if kind == "pub" else pf.lin_command(ident, n=x, enhanced=enh)
        for w in words:
            mh.push(0, w)
        bus.run(15e6)
        if kind == "pub":
            want_bus.append(frame_bytes(ident, x, enh))
        elif ident == 0x22:
            want_bus.append(frame_bytes(ident, staged_a, enh))
        else:
            want_bus.append(bytes([0x55, pf.lin_pid(ident)]))
    got = bus.frames()
    assert [f["bytes"] for f in got] == want_bus, [f["bytes"].hex() for f in got]
    assert all(f["stop_errors"] == 0 and abs(f["brk_ns"] - 13 * BIT) < 3 * bus.period for f in got)
    cmd = pf.lin_records(bus.recs[0])
    assert [c.get("count") for c in cmd] == [4, 0, 8, 8, 1]
    assert [c["published"] for c in cmd] == [True, False, True, False, True]
    assert cmd[1]["flag1"] and not cmd[1]["flag0"]                 # 0x05: timeout
    assert cmd[3]["bytes"] == staged_a and cmd[3]["flag0"]          # checksum ok
    rsp = pf.lin_records(bus.recs[1])
    assert [r["pid"] for r in rsp] == [pf.lin_pid(i) for _, i, _, _ in plan]
    assert not any(r["parity_error"] for r in rsp)
    # a listening responder does not know the length: every byte is reported,
    # the last one being the checksum, which it checks both ways
    assert rsp[0]["bytes"] == want_bus[0][2:] and rsp[0]["flag1"] and not rsp[0]["flag0"]   # enhanced ok
    assert rsp[2]["bytes"] == want_bus[2][2:] and rsp[2]["flag0"]                         # classic ok
    assert rsp[3]["published"] and rsp[1]["count"] == 0
    assert all(abs(r["T"] - 104) <= 1 for r in rsp)


@pytest.mark.parametrize("rate_err,clock_err", [(0, 0), (0.10, 0), (-0.10, 0), (0, 0.14), (0, -0.14), (0.05, -0.10)])
def test_lin_responder_sync(rate_err, clock_err):
    """The responder against an independent commander model at 19200 * (1 +
    rate_err) bit/s, with its own clock off by clock_err: it measures T from
    the sync field, receives the published frame, and answers its own ID at
    the commander's rate (the response's bit time measured on the bus)."""
    bus = Bus(commander=False, rsp_clock_err=clock_err)
    bit = BIT / (1 + rate_err)
    data = bytes([0xA5, 0x00, 0xFF, 0x3C])
    resp = bytes([9, 8, 7, 6, 5])
    for w in pf.lin_stage(0x31, resp, enhanced=True):
        bus.mh.push(1, w)
    bus.drive(pf.lin_waveform(0x12, data, bit) + [(1, 30 * bit)])
    bus.drive(pf.lin_waveform(0x31, None, bit) + [(1, 80 * bit)])
    bus.run(260 * bit)
    rsp = pf.lin_records(bus.recs[1])
    T = 2e6 * (1 + clock_err) * bit * 1e-9         # its ticks per bit
    assert [r["pid"] for r in rsp] == [pf.lin_pid(0x12), pf.lin_pid(0x31)]
    assert all(abs(r["T"] - T) <= 1.5 for r in rsp), ([r["T"] for r in rsp], T)
    assert rsp[0]["bytes"] == frame_bytes(0x12, data)[2:] and rsp[0]["flag1"] and rsp[0]["count"] == 5
    assert rsp[1]["published"]
    [f1, f2] = bus.frames(bit)
    assert f2["bytes"] == frame_bytes(0x31, resp) and f2["stop_errors"] == 0
    # the response's own bit time, from the edges inside each byte (start bit
    # to the farthest edge), and the spaces between its bytes
    edges = bus.log.edges()
    est = []
    for s0 in f2["starts"][2:]:
        inside = [t for t, _ in edges if s0 < t < s0 + 9.6 * bit]
        if inside:
            t = max(inside)
            est.append((t - s0) / round((t - s0) / bit))
    measured_bit = sum(est) / len(est)
    gaps = [b - a - 10 * measured_bit for a, b in zip(f2["starts"][2:], f2["starts"][3:])]
    print(f"\nLIN responder, commander {rate_err:+.0%}, own clock {clock_err:+.0%}: T = {[r['T'] for r in rsp]} "
          f"ticks (ideal {T:.1f}); response bit {measured_bit:.0f} ns vs commander {bit:.0f} ns "
          f"({(measured_bit / bit - 1) * 100:+.2f} %), space between its bytes {min(gaps) / bit:.2f}.."
          f"{max(gaps) / bit:.2f} bits")
    assert abs(measured_bit / bit - 1) < 0.02            # LIN 2.x: +-2 % after synchronisation


def test_lin_commander_subscribes_to_model_node():
    """The commander against an independent responder model 1.5 % slow: the
    commander receives its response at mid-bit and checks the checksum."""
    node = pf.LinNode(BIT * 1.015, {0x2A: (bytes([1, 2, 3, 4, 5, 6, 7, 8]), True), 0x2B: (bytes([0x42]), False)})
    bus = Bus(responder=False, nodes=[node])
    for ident, n, enh in [(0x2A, 8, True), (0x2B, 1, False), (0x2A, 8, False)]:
        for w in pf.lin_command(ident, n=n, enhanced=enh):
            bus.mh.push(0, w)
        bus.run(15e6)
    cmd = pf.lin_records(bus.recs[0])
    assert [c["bytes"] for c in cmd] == [bytes(range(1, 9)), b"\x42", bytes(range(1, 9))]
    assert [c["flag0"] for c in cmd] == [True, True, False]     # the third: classic asked, enhanced sent


def test_lin_responder_errors():
    """A PID with wrong parity is reported and never answered (even with a
    response staged for its ID); a wrong checksum is reported as such; a
    break in the middle of a response ends that frame and starts the next."""
    bus = Bus(commander=False)
    for w in pf.lin_stage(0x07, bytes([1, 2])):
        bus.mh.push(1, w)
    bad_pid = pf.lin_pid(0x07) ^ 0x80
    bus.drive(pf.lin_waveform(0x07, None, BIT, pid=bad_pid) + [(1, 40 * BIT)])
    bus.drive(pf.lin_waveform(0x15, bytes([1, 2, 3]), BIT, checksum=0x00) + [(1, 30 * BIT)])
    w = pf.lin_waveform(0x16, bytes([9, 9, 9, 9]), BIT)
    cut = w[:12]                                   # through the PID and a bit of the first byte
    bus.drive(cut + pf.lin_waveform(0x17, bytes([5]), BIT) + [(1, 40 * BIT)])
    bus.run(300 * BIT)
    rsp = pf.lin_records(bus.recs[1])
    assert rsp[0]["parity_error"] and not rsp[0]["published"] and rsp[0]["count"] == 0
    assert rsp[1]["bytes"] == bytes([1, 2, 3, 0]) and not rsp[1]["flag0"] and not rsp[1]["flag1"]
    assert rsp[-1]["pid"] == pf.lin_pid(0x17) and rsp[-1]["bytes"] == frame_bytes(0x17, [5])[2:] and rsp[-1]["flag1"]


def test_lin_commander_50mhz():
    """The commander at 50 MHz (CLKDIV 25.04): break, delimiter and bits
    against 19200 bit/s, edge to edge on the bus."""
    bus = Bus(f_clk=50e6, responder=False)
    for w in pf.lin_command(0x10, data=bytes([0x55, 0xAA])):
        bus.mh.push(0, w)
    bus.run(4.5e6)
    [f] = bus.frames()
    edges = bus.log.edges()[1:]                     # from the break's falling edge
    brk, delim = edges[1][0] - edges[0][0], edges[2][0] - edges[1][0]
    # inside each byte, every edge on the bit grid from its start bit
    errs = []
    for s0 in f["starts"]:
        for t, _ in edges:
            if s0 < t < s0 + 9.6 * BIT:
                k = round((t - s0) / BIT)
                errs.append(t - s0 - k * BIT)
    gaps = [b - a - 10 * BIT for a, b in zip(f["starts"], f["starts"][1:])]
    print(f"\nLIN commander at 50 MHz: break {brk / BIT:.5f} bits, delimiter {delim / BIT:.5f} bits, "
          f"edges within a byte {min(errs):+.1f}..{max(errs):+.1f} ns of the 19200 bit/s grid, "
          f"space between bytes {min(gaps) / 1e3:.2f}..{max(gaps) / 1e3:.2f} us; {f['bytes'].hex()}")
    assert f["bytes"] == frame_bytes(0x10, bytes([0x55, 0xAA]))
    assert abs(brk / (13 * BIT) - 1) < 1e-4 and abs(delim / BIT - 1) < 1e-3
    assert max(abs(e) for e in errs) < 2e-4 * 9 * BIT + 40      # the divider's rate error + a clock


@pytest.mark.skipif(sigrok.cli() is None, reason=sigrok.INSTALL_HINT)
def test_sigrok_lin():
    """sigrok's uart + lin decoders (LIN 2, enhanced checksums) on the bus of
    the two firmwares: PIDs with parity, data and checksums as sigrok judged
    them; a classic-checksum frame (ID 0x3C) decoded with version 1."""
    bus = Bus()
    mh = bus.mh
    for w in pf.lin_stage(0x22, bytes([0xCA, 0xFE])):
        mh.push(1, w)
    for words in (pf.lin_command(0x10, data=bytes([1, 2, 3, 4, 5])), pf.lin_command(0x22, n=2),
                  pf.lin_command(0x3C, data=bytes(range(8)), enhanced=False)):
        for w in words:
            mh.push(0, w)
        bus.run(14e6)
    tr = pf.to_trace(sigrok.Trace(), bus.log.edges(), "lin", bus.log.now())
    frames, warn = sigrok.lin(tr, "lin", BAUD, version=2, res_ps=100_000)
    summary = [(f["id"], f["parity_ok"], f["data"].hex(), f["checksum_ok"]) for f in frames]
    print(f"\nsigrok lin (v2): {summary} {warn}")
    assert summary[:2] == [(0x10, True, "0102030405", True), (0x22, True, "cafe", True)]
    assert summary[2][:3] == (0x3C, True, bytes(range(8)).hex())
    f1, _ = sigrok.lin(tr, "lin", BAUD, version=1, res_ps=100_000)
    assert f1[2]["checksum_ok"] and not f1[0]["checksum_ok"]      # classic vs enhanced
