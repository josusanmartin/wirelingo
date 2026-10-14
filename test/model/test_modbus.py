"""Modbus RTU client framing (firmware/modbus_rtu.pasm, post-freeze) on the
cycle model.

Judges: published request examples (their CRC bytes written out here),
pe.postfreeze2.modbus_crc (the specification's algorithm, independent of the
CRC tap), an independent server model on the RS-485 bus that decodes the
characters, frames them by silence and answers (pe.postfreeze2.ModbusServer),
and sigrok's uart + modbus decoders.

The chip runs at TB * baud (CLKDIV 1: 1.9968 MHz for 19200 baud), tick for
tick the waveform of 50 MHz / CLKDIV 25.04; test_modbus_50mhz runs a request
at 50 MHz."""
import pathlib
import random

import pytest

from pe import asm, host, postfreeze as pf, postfreeze2 as p2, sigrok, sim
from pe.modelhost import ModelHost

FW = pathlib.Path(__file__).resolve().parents[2] / "firmware"
TX, DE, RX = 8, 9, 16          # uo0, uo1, ui0
TB = 104


class Rig:
    """modbus_rtu on core 0 and a server model on one RS-485 bus: the line is
    TX while DE is high, else the server's driver, else idle (biased high)."""

    def __init__(self, server=None, baud=19200, f_clk=None, start=True, **dkw):
        self.mh = mh = ModelHost()
        self.defs = p2.modbus_defines(baud, TB, TX, DE, RX, **dkw)
        prog = asm.assemble((FW / "modbus_rtu.pasm").read_text(), self.defs)
        assert len(prog.words) <= 256
        f_clk = f_clk or baud * TB
        mh.apply(host.load_program(0, prog, clkdiv=host.clkdiv_for(f_clk, baud * TB)))
        mh.write(sim.core_reg(0, sim.C_CRCCTRL), p2.MODBUS_CRCCTRL)
        mh.write(sim.A_POUT, 1 << TX)
        mh.ui = 1
        if start:
            mh.start(1)
        self.period, self.bit = 1e9 / f_clk, 1e9 / baud
        self.server = server
        self.line, self.client, self.de = [(0.0, 1)], [(0.0, 1)], [(0.0, 0)]
        self.words, self.contention = [], 0
        self.drive = (0, 1)

    def run(self, ns, until=None):
        mh, chip = self.mh, self.mh.chip
        for _ in range(int(ns / self.period)):
            t = chip.cycle * self.period
            p = chip.pin_out
            tx, de = (p >> TX) & 1, (p >> DE) & 1
            sd, sl = self.drive
            line = tx if de else (sl if sd else 1)
            if de and sd:
                self.contention += 1
            if self.server is not None:
                self.drive = self.server.step(t, line)
            for log, v in ((self.line, line), (self.client, tx if de else 1), (self.de, de)):
                if v != log[-1][1]:
                    log.append((t, v))
            mh.ui = line
            mh.step()
            if chip.cores[0].rxf:
                self.words += mh.pop_all(0)
                if until and until(self):
                    return

    def now(self):
        return self.mh.chip.cycle * self.period

    def request(self, body, expect=True, wait=None):
        for w in p2.modbus_words(body, expect):
            while self.mh.tx_level(0) >= 8:
                self.run(4 * self.bit)
            self.mh.push(0, w)
        n0 = len(p2.modbus_response(self.words)) if expect else None
        self.run(wait or 400e6, until=(lambda r: len(p2.modbus_response(r.words)) > n0) if expect else None)
        return p2.modbus_response(self.words)[-1] if expect else None


REGS = {a: (a * 0x0101 + 0x1234) & 0xFFFF for a in range(0x10000)}


def server_fn(body):
    """A server for address 0x11: functions 3 (read holding registers), 6
    (write single register: echo) and 8 (diagnostics: echo); others get
    exception 01."""
    if body[0] != 0x11:
        return None
    fc = body[1]
    if fc == 3:
        a, n = int.from_bytes(body[2:4], "big"), int.from_bytes(body[4:6], "big")
        return bytes([0x11, 3, 2 * n]) + b"".join(REGS[(a + i) & 0xFFFF].to_bytes(2, "big") for i in range(n))
    if fc in (6, 8):
        return bytes(body)
    return bytes([0x11, fc | 0x80, 0x01])


def test_modbus_published_examples():
    """Requests with CRCs published in Modbus documentation (01 03 00 00 00 0A
    -> C5 CD; 11 03 00 6B 00 03 -> 76 87): the bytes on the bus, decoded by
    the pins-only decoder, end with exactly those CRC bytes."""
    srv = p2.ModbusServer(lambda b: None)
    rig = Rig(srv)
    for body in (bytes.fromhex("01030000000a"), bytes.fromhex("1103006b0003")):
        rig.request(body, expect=False, wait=12e6)
    got = [r[1].hex() for r in srv.requests]
    print(f"\non the bus: {got}")
    assert got == ["01030000000ac5cd", "1103006b00037687"]
    assert all(r[2] and r[3] and r[4] for r in srv.requests)
    assert rig.words == []                         # broadcasts get no record


def test_modbus_request_response():
    """Requests and responses of every length mod 3 (the firmware packs 3
    bytes per word): the response's bytes and CRC verdict, the request as
    the server saw it (CRC, parity, characters back to back), the driver
    enabled only around the request."""
    srv = p2.ModbusServer(server_fn)
    rig = Rig(srv)
    bodies = [bytes([0x11, 3, 0, 0x6B, 0, n]) for n in (1, 2, 3, 4, 10)] + \
             [bytes([0x11, 6, 0, 1, 0xBE, 0xEF]), bytes([0x11, 8, 0, 0, 0xA5, 0x37]), bytes([0x11, 0x2B, 0x0E])]
    for body in bodies:
        r = rig.request(body)
        want = p2.modbus_adu(server_fn(body))
        assert r["bytes"] == want and r["crc_ok"], (body.hex(), r)
        assert not (r["parity_error"] or r["framing_error"] or r["gap"] or r["overflow"])
    assert [q[1] for q in srv.requests] == [p2.modbus_adu(b) for b in bodies]
    assert all(q[2] and q[3] and q[4] for q in srv.requests)
    gaps = [g for q in srv.requests for g in q[5]]
    print(f"\nrequest character gaps {min(gaps) / 1e3:.2f}..{max(gaps) / 1e3:.2f} us (t1.5 = 859 us)")
    assert max(gaps) <= 0.2 * rig.bit and rig.contention == 0
    # DE: high from before the first start bit until after the last stop bit
    de = pf.runs(rig.de, rig.now())
    on = [(t, d) for lvl, t, d in de if lvl == 1]
    assert len(on) == len(bodies)
    chars = p2.uart_decode(rig.client, rig.now(), rig.bit)
    for (t, d), q in zip(on, srv.requests):
        cs = [c for c in chars if t <= c["t"] <= t + d]
        assert len(cs) == len(q[1]) and cs[0]["t"] >= t and cs[-1]["t_stop_end"] <= t + d + 1e3


def test_modbus_long_response():
    """A 255-byte response (125 registers), drained while it arrives."""
    srv = p2.ModbusServer(server_fn)
    rig = Rig(srv)
    body = bytes([0x11, 3, 0x10, 0x00, 0, 125])
    r = rig.request(body)
    assert len(r["bytes"]) == 255 and r["bytes"] == p2.modbus_adu(server_fn(body)) and r["crc_ok"]
    assert not r["overflow"]


@pytest.mark.parametrize("rate", [0.98, 1.02])
@pytest.mark.parametrize("gap_chars", [0, 1.3])
def test_modbus_tolerance(rate, gap_chars):
    """A server 2 % slow or fast, with gaps of 0 or 1.3 characters between
    its characters (under t1.5): every response received, CRC ok, no flag."""
    char = 11 * 1e9 / 19200
    srv = p2.ModbusServer(lambda b: {"data": server_fn(b), "gaps": {i: gap_chars * char for i in range(40)}},
                          rate=rate)
    rig = Rig(srv)
    for n in (1, 5, 9):
        r = rig.request(bytes([0x11, 3, 0, 0, 0, n]))
        assert r["crc_ok"] and r["bytes"] == p2.modbus_adu(server_fn(bytes([0x11, 3, 0, 0, 0, n])))
        assert not (r["gap"] or r["parity_error"] or r["framing_error"])


def test_modbus_errors():
    """Negative controls: a wrong CRC; a parity error; a low stop bit; a
    2.5-character gap inside a response (between t1.5 and t3.5: the frame is
    broken, its bytes and CRC otherwise fine); a 4-character gap (two
    frames: the first ends at t3.5 and fails its CRC); no response. Then a
    good one."""
    char = 11 * 1e9 / 19200
    body = bytes([0x11, 3, 0, 0, 0, 2])
    good = server_fn(body)
    plan = iter([{"data": good, "crc": False}, {"data": good, "bad_parity": (3,)},
                 {"data": good, "bad_stop": (4,)}, {"data": good, "gaps": {2: 2.5 * char}},
                 {"data": good, "gaps": {2: 4.0 * char}}, None, good])
    srv = p2.ModbusServer(lambda b: next(plan))
    rig = Rig(srv)
    out = []
    for _ in range(7):
        out.append(rig.request(body))
        rig.run(12e6)                           # let a split response's tail pass
    for r in out:
        print(f"  {len(r['bytes'])} bytes crc {r['crc_ok']} parity {r['parity_error']} "
              f"framing {r['framing_error']} gap {r['gap']}")
    bad_crc, par, stop, gap, split, none, ok = out
    assert not bad_crc["crc_ok"] and len(bad_crc["bytes"]) == len(good) + 2
    assert par["parity_error"] and par["crc_ok"]          # the data bits were right
    assert stop["framing_error"]
    assert gap["gap"] and gap["crc_ok"] and gap["bytes"] == p2.modbus_adu(good)
    assert not split["gap"] and not split["crc_ok"] and split["bytes"] == p2.modbus_adu(good)[:3]
    assert none["bytes"] == b"" and not none["crc_ok"]
    assert ok["crc_ok"] and ok["bytes"] == p2.modbus_adu(good) and not (ok["gap"] or ok["parity_error"])


def test_modbus_odd_parity_38400():
    """Odd parity (PAR = 1) at 38400 baud, where t1.5 / t3.5 are the fixed
    750 / 1750 us: responses received, and a 1.0 ms gap (> t1.5 = 750 us)
    flagged."""
    srv = p2.ModbusServer(server_fn, baud=38400, parity="odd")
    rig = Rig(srv, baud=38400, parity="odd")
    assert (rig.defs["T15"], rig.defs["T35"]) == (round(750e3 / (1e9 / 38400 / TB)), round(1750e3 / (1e9 / 38400 / TB)))
    body = bytes([0x11, 3, 0, 7, 0, 4])
    r = rig.request(body)
    assert r["crc_ok"] and r["bytes"] == p2.modbus_adu(server_fn(body)) and not r["gap"]
    srv.respond = lambda b: {"data": server_fn(b), "gaps": {3: 1.0e6}}
    r = rig.request(body)
    assert r["gap"] and r["crc_ok"]
    srv.respond = lambda b: {"data": server_fn(b), "gaps": {3: 0.6e6}}
    r = rig.request(body)
    assert not r["gap"] and r["crc_ok"]


def test_modbus_50mhz():
    """At 50 MHz (CLKDIV 25.04): every edge of a request within a clock or
    two of the 19200 baud grid of its character's start bit."""
    srv = p2.ModbusServer(lambda b: None)
    rig = Rig(srv, f_clk=50e6)
    body = bytes([0x11, 0x10, 0, 1, 0, 2, 4, 0xDE, 0xAD, 0xBE, 0xEF])
    for w in p2.modbus_words(body, expect=False):
        rig.mh.push(0, w)
    rig.run(2.1e6 + 13 * 11 * rig.bit + 2.2e6)
    chars = p2.uart_decode(rig.client, rig.now(), rig.bit)
    assert bytes(c["byte"] for c in chars) == p2.modbus_adu(body)
    err = []
    for c in chars:
        for t, v in rig.client:
            if c["t"] < t < c["t"] + 10.5 * rig.bit:
                k = round((t - c["t"]) / rig.bit)
                err.append(t - c["t"] - k * rig.bit)
    print(f"\nModbus at 50 MHz: {len(chars)} characters, edges {min(err):+.1f}..{max(err):+.1f} ns off the grid")
    assert max(abs(e) for e in err) <= 2 * 20 + 1


@pytest.mark.skipif(sigrok.cli() is None, reason=sigrok.INSTALL_HINT)
def test_sigrok_modbus():
    """sigrok's uart (even parity) + modbus decoders: the requests on the
    client's TX (while DE is high), the responses on the server's: function
    names and 'CRC correct' for both directions."""
    srv = p2.ModbusServer(server_fn)
    rig = Rig(srv)
    bodies = [bytes([0x11, 3, 0, 0x6B, 0, 3]), bytes([0x11, 6, 0, 1, 0x00, 0x03])]
    for b in bodies:
        rig.request(b)
    rig.run(5e6)
    srv_line = [(0.0, 1)]
    for t, v in rig.line:
        de_now = [d for tt, d in rig.de if tt <= t][-1]
        if not de_now and v != srv_line[-1][1]:
            srv_line.append((t, v))
    tr = sigrok.Trace()
    pf.to_trace(tr, rig.client, "client")
    pf.to_trace(tr, srv_line, "server")
    tr.end(round(rig.now() * 1000))
    data, warn = sigrok.uart_parity(tr, "client", 19200, res_ps=100_000)
    assert bytes(b for _, b in data) == b"".join(p2.modbus_adu(b) for b in bodies) and not warn
    got = sigrok.modbus(tr, "client", "server", 19200, res_ps=100_000)
    cs = [t for _, t in got["cs"] if t.startswith("Function") or "CRC" in t]
    sc = [t for _, t in got["sc"] if t.startswith("Function") or "CRC" in t]
    print(f"\nsigrok modbus: client->server {cs}\n               server->client {sc}")
    assert cs == ["Function 3: Read Holding Registers", "CRC correct", "Function 6: Write Single Register", "CRC correct"]
    assert sc == cs
