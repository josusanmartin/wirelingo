"""eth_tap.FpgaBackend and fpga/kv260/host/fabric_eth.py: real-Linux-shaped frames
through the chip with no cable, on the cycle model (ModelRig, pe_lab modelled) and
on a fake KV260 whose pe_spim and pe_lab register maps drive that model, so the
board's code path, register writes included, runs here. No hardware: evidence
level "model". Judges: zlib for the FCS and this file's own frame builders and
checkers (test_eth_stack's far end), not pe.eth."""
import importlib.util
import io
import json
import os
import pathlib
import random
import signal
import struct
import sys
import zlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
HOST = ROOT / "fpga/kv260/host"
sys.path.insert(0, str(HOST))
sys.path.insert(0, str(ROOT / "tools"))
import fabric_eth as fe                         # noqa: E402
import fabric_guard as fg                       # noqa: E402
import pe_kria                                  # noqa: E402
import pe_lab                                   # noqa: E402
import pe_spim                                  # noqa: E402
import postfreeze_demo as pfd                   # noqa: E402
import v2_demo                                  # noqa: E402
from pe import ethsim, sim                      # noqa: E402

from test_eth_stack import (BCAST, CHIP_IP, CHIP_MAC, check_arp_reply, check_ping_reply, fcs,  # noqa: E402
                            icmp_echo, ip_packet, l2, udp_datagram)

HOST_MAC = bytes.fromhex("02574c555501")        # eth_tap.HOST_MAC_TEXT
HOST_IP = bytes([10, 77, 0, 1])
CLOCKS = [50e6, 40e6]


def load_bridge():
    spec = importlib.util.spec_from_file_location("eth_tap", HOST / "eth_tap.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- frames, as Linux sends them
def linux_arp(target=CHIP_IP):
    return l2(BCAST, HOST_MAC, 0x0806, struct.pack(">HHBBH6s4s6s4s", 1, 0x0800, 6, 4, 1, HOST_MAC, HOST_IP,
                                                   bytes(6), target))


def ping_data(n=18, seq=1):
    """As Linux's ping: a 16-byte timeval, then 0x10, 0x11, ..."""
    return struct.pack("<qq", 1793750400 + seq, 654321) + bytes((0x10 + i) & 255 for i in range(n - 16))


def linux_ping(seq=1, n=18, dst_mac=CHIP_MAC):
    return l2(dst_mac, HOST_MAC, 0x0800, ip_packet(1, icmp_echo(0x1234, seq, ping_data(n, seq)), src=HOST_IP))


def linux_udp(data=b"wirelingo"):
    return l2(CHIP_MAC, HOST_MAC, 0x0800, ip_packet(17, udp_datagram(40000, 7, data, src=HOST_IP), src=HOST_IP))


def check_reply_to_host(raw, kind):
    assert zlib.crc32(raw[:-4]).to_bytes(4, "little") == raw[-4:] and len(raw) == 64
    assert raw[:6] == HOST_MAC and raw[6:12] == CHIP_MAC and raw[12:14] == kind


# ---------------------------------------------------------------- the generator schedule
def test_linux_frames_fit_the_generator_and_bigger_ones_are_refused():
    for raw in (linux_arp(), linux_ping(), linux_udp()):
        s = fe.pg_schedule(raw, 50e6)
        assert len(s) <= fe.PG_DEPTH
    assert len(fe.pg_schedule(linux_arp(), 50e6)) == 974          # the figure eth_tap's HOST_MAC_TEXT comment gives
    with pytest.raises(fe.TooBig, match=r"102-byte frame: 1\d\d\d edges"):
        fe.pg_schedule(linux_ping(n=56), 50e6)                    # ping's default size


@pytest.mark.parametrize("clock", CLOCKS)
def test_schedule_is_the_line_waveform_at_clock_times(clock):
    raw = linux_arp()
    s = fe.pg_schedule(raw, clock, lead_s=1e-5)
    e = ethsim.line_edges(ethsim.wire_bits(raw), 0.0)
    assert [v for _, v in s] == [lv << fe.RXP for _, lv in e]
    lead = round(1e-5 * clock)
    clk = 1e9 / clock
    assert all(abs((t - lead) * clk - te) <= clk / 2 for (t, _), (te, _) in zip(s, e))
    gaps = {b[0] - a[0] for a, b in zip(s, s[1:-2])}               # TP_IDL and the return to idle aside
    assert gaps == ({2, 3, 5} if clock == 50e6 else {2, 4})


# ---------------------------------------------------------------- putting zero words back
def fifo_words(bits):
    """eth10_rx's FIFO words for a record of these line bits (its header comment)."""
    full, k = divmod(len(bits), 24)
    out = [sum(b << i for i, b in enumerate(bits[24 * j:24 * j + 24])) << 8 for j in range(full)]
    tail = sum(b << i for i, b in enumerate(bits[24 * full:]))
    return out + [tail << (32 - k) if k else 0, (len(bits) << 16) | 0x80]


def blind_burst(words, spacing=15.0, i0=40, gap=2, after=30):
    """A burst as a blind READ_FIXED sees those words: data word j at i0 + j*spacing,
    the last word gap reads after the last full one and the trailer right after
    it, 0 everywhere else."""
    full = len(words) - 2
    pos = [i0 + round(j * spacing) for j in range(full)]
    last = (pos[-1] if pos else i0) + gap
    burst = [0] * (last + 2 + after)
    for p, w in zip(pos, words):
        burst[p] = w
    burst[last], burst[last + 1] = words[-2], words[-1]
    return burst


def zero_heavy_bits():
    raw = linux_arp()                                   # 24 zero bytes: target MAC and padding
    return ethsim.wire_bits(raw), raw


@pytest.mark.parametrize("spacing,gap", [(15.0, 1), (15.0, 2), (15.0, 3), (12.0, 2), (15.4, 2), (14.6, 2)])
def test_restore_puts_zero_words_back(spacing, gap):
    bits, _ = zero_heavy_bits()
    words = fifo_words(bits)
    assert words.count(0) >= 3
    got, restored = fe.restore_rx(blind_burst(words, spacing, gap=gap), 15.0 if spacing > 13 else 12.0)
    assert got == words and restored == words[:-2].count(0)


def test_restore_keeps_a_last_word_with_bits_in_it():
    raw = linux_ping()
    raw = raw[:-4] + bytes(2) + fcs(raw[:-4] + bytes(2))       # 66 bytes: 592 line bits, 16 in the last word
    words = fifo_words(ethsim.wire_bits(raw))
    assert words[-2] and (words[-1] >> 16) % 24 == 16
    got, _ = fe.restore_rx(blind_burst(words, gap=1), 15)
    assert got == words


def test_restore_keeps_a_zero_last_word_and_short_records():
    bits = ethsim.wire_bits(bytes(range(1, 65)))[:24 * 20]          # a multiple of 24: the last word is empty
    w = fifo_words(bits)
    assert w[-2] == 0
    pulse = fifo_words([1])
    burst = blind_burst(w, after=5) + [0, 0, pulse[0], pulse[1], 0]
    got, _ = fe.restore_rx(burst, 15)
    assert got == w + pulse


def test_restore_refuses_what_it_cannot_place():
    bits, _ = zero_heavy_bits()
    words = fifo_words(bits)
    late = blind_burst(words)
    nz = [i for i, x in enumerate(late) if x and not x & 0xFF]
    late[nz[0]], late[nz[0] + 1] = 0, late[nz[0]]                   # a word a read late
    late[nz[0] + 1], late[nz[0] + 3] = 0, late[nz[0] + 1]           # three reads late
    with pytest.raises(fe.RxGap, match="cadence"):
        fe.restore_rx(late, 15)
    backlog = [0] * 10 + [w for w in words if w]                    # the burst began after the FIFO had filled
    with pytest.raises(fe.RxGap, match="cadence"):
        fe.restore_rx(backlog, 15)
    with pytest.raises(fe.RxGap, match="inside a record"):
        fe.restore_rx(blind_burst(words)[:300], 15)


# ---------------------------------------------------------------- the two phases on the cycle model
@pytest.fixture(scope="module")
def fab50():
    return fe.FabricEth(pfd.ModelRig(50e6), 50e6)


@pytest.mark.parametrize("clock", CLOCKS)
@pytest.mark.parametrize("phase", range(0, 8, 3))
def test_receive_puts_arp_padding_back(clock, phase):
    """Linux's ARP request (24 zero bytes) into RXP at three phases of the burst's reads."""
    f = fe.FabricEth(pfd.ModelRig(clock), clock)
    f.lead_s = fe.LEAD_S + phase / clock
    raw = linux_arp()
    got, info = f.receive(raw)
    assert got == raw and info["zero_words_restored"] >= 5
    assert info["records"][0] == 8 * (8 + len(raw))                 # every line bit, preamble included


def test_receive_random_frames(fab50):
    """Frames with zero runs where they fall, up to the generator's limit."""
    rng = random.Random(5)
    done = 0
    while done < 6:
        body = bytearray(rng.randrange(256) for _ in range(rng.choice((60, 60, 80, 100))))
        for _ in range(rng.randrange(4)):
            i = rng.randrange(len(body))
            body[i:i + rng.randrange(3, 12)] = bytes(rng.randrange(3, 12))
        raw = bytes(body[:len(body)]) + fcs(bytes(body))
        try:
            got, _ = fab50.receive(raw)
        except fe.TooBig:
            continue
        assert got == raw
        done += 1


def test_transmit_is_read_off_td(fab50):
    """eth10_tx_cs on uio3/uio4 at CLKDIV 1.25, sampled every clock: the stack's
    waveform (test_eth_stack and test_eth10 have its timing at the pads)."""
    reply = l2(HOST_MAC, CHIP_MAC, 0x0806, struct.pack(">HHBBH6s4s6s4s", 1, 0x0800, 6, 4, 2, CHIP_MAC, CHIP_IP,
                                                        HOST_MAC, HOST_IP))
    got, info = fab50.transmit(reply)
    assert got == reply and zlib.crc32(got[:-4]).to_bytes(4, "little") == got[-4:]
    assert info["both_high"] == 0 and info["f_go_cleared"] and info["loaded_words"] == 16
    assert info["pulses"] == [(5, 5)]                               # the 100 ns link pulse first
    fr = info["frames"][0]
    assert fr["driven_runs"] == [2, 3, 5]                           # 40 and 60 ns half cells
    assert fr["start"] - 10 >= 96 * 5                               # the 96-bit window after the pulse
    assert 255 <= fr["tp_idl_ns"] <= 320                           # 275 or 300 ns (eth10_tx_cs), +-1 sample
    with pytest.raises(fe.TooBig, match="TX FIFOs hold 64"):
        fab50.transmit(reply[:-4] + bytes(4) + fcs(reply[:-4] + bytes(4)))


def test_both_high_on_td_is_counted():
    """Samples: bit 0 the marker, bit 1 TD+, bit 2 TD-."""
    smp = [1] * 10 + [3] * 5 + [7] + [5] * 5 + [1] * 60
    _, info = fe.decode_td(smp, 20.0)
    assert info["both_high"] == 1
    _, info = fe.decode_td([1] * 10 + [3] * 5 + [5] * 5 + [1] * 60, 20.0)
    assert info["both_high"] == 0 and info["pulses"] == [(10, 10)]


# ---------------------------------------------------------------- the backend behind a TAP
class FakeTap:
    def __init__(self):
        self.r, self.w = os.pipe()
        self.fd = self.r
        self.inbox, self.outbox = [], []

    def put(self, f):
        self.inbox.append(f)
        os.write(self.w, b"x")

    def read(self):
        if not self.inbox:
            return None
        os.read(self.r, 1)
        return self.inbox.pop(0)

    def write(self, f):
        self.outbox.append(f)


def exchange(bridge, tap, frame):
    n = len(tap.outbox)
    tap.put(frame)
    bridge.run_while(lambda: bridge.backend.busy() or tap.inbox, 30)
    bridge.step(0)
    return tap.outbox[n:]


@pytest.fixture(scope="module")
def bridged():
    tap_mod = load_bridge()
    t = FakeTap()
    b = tap_mod.Bridge(t, tap_mod.FpgaBackend.model(CHIP_IP))
    return tap_mod, b, t


def test_backend_answers_arp_ping_and_udp(bridged):
    _, b, t = bridged
    out = exchange(b, t, linux_arp()[:42])                          # what a TAP hands over: no padding, no FCS
    assert len(out) == 1 and len(out[0]) == 60
    check_reply_to_host(out[0] + fcs(out[0]), b"\x08\x06")
    check_arp_reply(as_peer(out[0]))
    out = exchange(b, t, linux_ping(3)[:-4])
    assert len(out) == 1
    check_reply_to_host(out[0] + fcs(out[0]), b"\x08\x00")
    check_ping_reply(as_peer(out[0]), 3, ping_data(18, 3))
    out = exchange(b, t, linux_udp()[:-4])
    assert len(out) == 1 and out[0][42:51] == b"wirelingo"


def as_peer(frame):
    """A frame to our host (no FCS) as one to test_eth_stack's peer, which has the
    same IP, so that file's checkers apply; FCS added."""
    from test_eth_stack import PEER_IP, PEER_MAC
    assert PEER_IP == HOST_IP
    body = frame.replace(HOST_MAC, PEER_MAC)
    return body + fcs(body)


def test_backend_answers_nothing_it_must_not(bridged):
    _, b, t = bridged
    before = dict(b.backend.stats)
    b.backend.corrupt = 1
    assert exchange(b, t, linux_ping(4)[:-4]) == []                         # bad FCS
    assert exchange(b, t, linux_ping(5, dst_mac=bytes.fromhex("02574c000003"))[:-4]) == []
    assert exchange(b, t, linux_arp(bytes([10, 77, 0, 3]))[:42]) == []      # an ARP for another IP
    assert exchange(b, t, linux_ping(6, n=56)[:-4]) == []                   # too big for the generator
    st = b.backend.stats
    assert st.get("rx_too_big", 0) == before.get("rx_too_big", 0) + 1
    assert st["rx_frames"] == before.get("rx_frames", 0) + 3 and "rx_gap" not in st and "rx_lost" not in st
    assert b.backend.station.stats.get("bad_fcs") == 1 and b.backend.station.stats.get("other_dst") == 1
    out = exchange(b, t, linux_ping(7)[:-4])                                # and answers again
    assert len(out) == 1


def test_backend_never_hands_linux_a_bad_fcs(bridged):
    _, b, t = bridged
    good = linux_ping(8)
    b.backend.replies.append(good[:-1] + bytes([good[-1] ^ 0x80]))
    assert b.backend.poll() == []
    assert b.backend.stats["tx_lost"] == 1
    b.backend.stats.pop("tx_lost")


# ---------------------------------------------------------------- the probe, and the board's code path on fakes
class FakeDevMem:
    """pe_kria's AXI GPIO: uio drive to the model's pe_lab GPIO value; MMCM locked."""

    def __init__(self, fm):
        self.fm = fm

    def out(self, v):
        self.fm.gpio = (v >> 16) & 0xFF

    def inp(self):
        return 1 << 24

    def cycles_gray(self):
        c = self.fm.chip.cycle
        return c ^ (c >> 1)


class FakeBoard(pe_kria.Board):
    fault, calls = None, 0

    def clk_hz(self, interval=0.5):
        self.calls += 1
        if self.fault and self.calls == 2:
            self.quad_at_fault = self.regs.fm.chip.hostcfg & 1      # HOSTCFG is write-only: the model's own state
            self.fault()
        return 50e6 if self.f is None else self.f


class FakeSpimMem:
    """pe_spim's register map (pe_spim.v's header) executing each transaction on the
    model: a word every 8 (quad) or 32 (1-bit) SCK, SCK = clk / div. Logs SEL and PMOD writes."""

    def __init__(self, fm):
        self.fm, self.regs, self.buf = fm, {pe_spim.VERSION: pe_spim.VERSION_VALUE}, [0] * pe_spim.BUF_WORDS
        self.log = []

    def rd(self, off):
        return self.regs.get(off, 0) if off != pe_spim.GO else 0

    def wr(self, off, v):
        self.regs[off] = v
        if off in (pe_spim.SEL, pe_spim.PMOD0, pe_spim.PMOD1):
            self.log.append((off, v))
        if off == pe_spim.GO:
            self.run()

    def buf_write(self, words):
        self.buf[:len(words)] = list(words)

    def buf_read(self, n):
        return self.buf[:n]

    def run(self):
        fm, r = self.fm, self.regs
        cmd, addr, n = r[pe_spim.CMD] & 0xFF, (r[pe_spim.CMD] >> 8) & 0xFFFF, r[pe_spim.LEN]
        cfg = r[pe_spim.CFG]
        per = (8 if cfg >> 8 & 1 else 32) * (cfg & 0xFF)
        fixed = cmd & 0x08
        for i in range(n):
            a = addr if fixed else addr + i
            if cmd & 1:
                fm.step(per - 3)
                self.buf[i] = fm.read(a, pop=True)
                if a == sim.A_ID and getattr(self, "bad_id", False):
                    self.buf[i] = 0
                if a == sim.A_ID and getattr(self, "on_id", None):
                    self.on_id()
            else:
                fm.step(per - 1)
                fm.write(a, self.buf[i])


class FakeLabMem:
    """pe_lab's register map (pe_lab.v's header) on the model's pe_lab: MODE and the generator."""

    def __init__(self, fm):
        self.fm, self.regs, self.mem = fm, {}, {}
        self.modes = []

    def rd(self, off):
        fm = self.fm
        if off == 0:
            return pe_lab.MAGIC
        if off == 4:
            return 24 << 8
        if off == pe_lab.PG_GO:
            return int(fm.pg is not None and fm.chip.cycle - fm.pg[0] <= fm.pg[1][-1])
        return self.regs.get(off, 0)

    def wr(self, off, v):
        fm = self.fm
        if off >= pe_lab.PG_MEM:
            self.mem[(off - pe_lab.PG_MEM) >> 2] = v
            return
        self.regs[off] = v
        if off == pe_lab.MODE:
            fm.mode = v & 3
            self.modes.append(v & 3)
        elif off == pe_lab.PG_GO and not self.rd(pe_lab.PG_GO):
            n, t = self.regs.get(pe_lab.PG_LEN, 0), self.regs.get(pe_lab.PG_D0, 1)
            fm.pg_init = self.regs.get(pe_lab.PG_INIT, 0xFF) & 0xFF
            ts, vs = [], []
            for i in range(n):
                ts.append(t)
                vs.append(self.mem[i] & 0xFF)
                t += self.mem[i] >> 8
            fm.pg = (fm.chip.cycle, ts, vs) if n else None


def fake_board(f_meas=None):
    m = pfd.ModelRig(50e6)
    fm = m.mh
    board = FakeBoard(regs=FakeDevMem(fm))
    board.f = f_meas
    spim, lab = pe_spim.Spim(mem=FakeSpimMem(fm)), pe_lab.Lab(mem=FakeLabMem(fm))
    return pfd.HwRig(hw=v2_demo.Hw(board=board, spim=spim, lab=lab, f_clk=50e6)), fm


@pytest.fixture(scope="module")
def model_probe(tmp_path_factory):
    tap = load_bridge()
    path = tmp_path_factory.mktemp("probe") / "m.json"
    assert tap.main(["--backend", "fpga-model", "--probe", "--save", str(path)]) == 0
    return path


def test_probe_on_the_model_passes_and_its_replies_are_right(model_probe):
    res = {r["name"]: r for r in json.loads(model_probe.read_text())["results"]}
    assert all(r["pass"] and r["received_same"] for r in res.values())
    assert res["bad_fcs"]["replies"] == 0 and res["other_mac"]["replies"] == 0
    check_reply_to_host(bytes.fromhex(res["arp"]["reply"]), b"\x08\x06")
    for name in ("ping", "udp"):
        assert res[name]["td_same"] and res[name]["tx_both_high"] == 0
        check_reply_to_host(bytes.fromhex(res[name]["reply"]), b"\x08\x00")
    assert res["arp"]["rx_zero_words_restored"] == 6


def test_the_boards_code_path_on_a_fake_board_agrees_with_the_model_to_the_clock(model_probe, tmp_path, capsys):
    """HwRig, pe_spim.Spim, pe_lab.Lab and go_burst's register sequence, on register
    maps that drive the model. Its probe equals the ModelRig's in every deterministic
    field, edges in clocks included; no PMOD pin is ever enabled; the board is given back."""
    tap = load_bridge()
    rig, fm = fake_board()
    backend = tap.FpgaBackend(CHIP_IP, 50e6, rig=rig)
    ok, results = tap.probe(backend, "10.77.0.2", "10.77.0.1")
    backend.close()
    assert ok
    path = tmp_path / "b.json"
    rep = backend.report()
    path.write_text(json.dumps(dict(rig=rep["rig"], ident=rep["ident"], results=results)))
    assert tap.compare(str(path), str(model_probe)) == 0
    log = rig.spim.mem.log
    assert all(v == 0 for off, v in log if off != pe_spim.SEL)
    assert log[-1] == (pe_spim.SEL, 0) and (pe_spim.SEL, 1) in log
    assert rig.lab.mem.modes[-1] == pfd.GPIO and fm.chip.enable == 0
    assert fm.chip.hostcfg == 0                       # QUAD_EN off (write-only on the bus: the model's state)


def as_board(d, phase=0):
    """A model probe file as a board's: one run, at one divider phase."""
    d = json.loads(json.dumps(d))
    d["rig"] = "KV260"
    for r in d["results"]:
        if "tx_phases" in r:
            ph = r.pop("tx_phases")[phase]
            r["tx_edges"], r["tx_pulses"] = ph["edges"], ph["pulses"]
    return d


def test_the_model_gives_four_distinct_divider_phases(model_probe):
    """Five trigger arrivals over the 5-clock pattern, four ticks in it: two arrivals
    meet the same tick, so four different waveforms after the trigger, link pulse
    included: one 100 ns pulse (5 clocks) before each frame."""
    for r in json.loads(model_probe.read_text())["results"]:
        if r["replies"]:
            ph = r["tx_phases"]
            assert len(ph) == 5 and all(p["edges"] for p in ph) and len({json.dumps(p) for p in ph}) == 4
            assert ph[0] == dict(pulses=r["tx_pulses"], edges=r["tx_edges"])
            assert all(len(p["pulses"]) == 1 and p["pulses"][0][1] == 5 for p in ph)


def test_compare_sees_the_link_pulse(model_probe, tmp_path):
    """Verifier report 029, finding 2: two extra link pulses, or one a clock late,
    are DIFFERENT; so is a model file from before the pulses were kept per phase."""
    tap = load_bridge()
    d = json.loads(model_probe.read_text())
    p = tmp_path / "x.json"
    for spoil in (lambda r: r["tx_pulses"].extend([[2000, 5], [4000, 5]]),
                  lambda r: r["tx_pulses"][0].__setitem__(0, r["tx_pulses"][0][0] + 1),
                  lambda r: r["tx_pulses"].clear()):
        b = as_board(d, 2)
        spoil(next(r for r in b["results"] if r["name"] == "arp"))
        p.write_text(json.dumps(b))
        assert tap.compare(str(p), str(model_probe)) == 1
    old = json.loads(json.dumps(d))
    for r in old["results"]:
        if "tx_phases" in r:
            r["tx_edges_phases"] = [x["edges"] for x in r.pop("tx_phases")]
    q = tmp_path / "old.json"
    q.write_text(json.dumps(old))
    p.write_text(json.dumps(as_board(d, 2)))
    assert tap.compare(str(p), str(q)) == 1


@pytest.mark.parametrize("rig", ["fake board", "model"])
def test_the_probe_fails_on_a_wrong_link_pulse(monkeypatch, rig):
    """On a board the probe sends each reply once; on the model once per divider phase."""
    tap = load_bridge()
    backend = tap.FpgaBackend.model(CHIP_IP) if rig == "model" else tap.FpgaBackend(CHIP_IP, rig=fake_board()[0])
    real = backend.fab.transmit

    def two_pulses(raw, phase=0):
        f, info = real(raw, phase)
        info["pulses"] = info["pulses"] + [(info["frames"][0]["end"] + 100, 5)]
        return f, info
    monkeypatch.setattr(backend.fab, "transmit", two_pulses)
    ok, results = tap.probe(backend, "10.77.0.2", "10.77.0.1")
    assert not ok and [r["pass"] for r in results] == [False, False, False, True, True]
    assert tap.link_pulse_ok(dict(pulses=[(5, 5)], frames=[dict(start=535)]), 20.0)
    assert not tap.link_pulse_ok(dict(pulses=[(5, 4)], frames=[dict(start=535)]), 20.0)
    assert not tap.link_pulse_ok(dict(pulses=[(600, 5)], frames=[dict(start=535)]), 20.0)


def test_compare_sees_one_clock(model_probe, tmp_path):
    tap = load_bridge()
    d = json.loads(model_probe.read_text())
    p = tmp_path / "x.json"
    for phase in (0, 3):
        p.write_text(json.dumps(as_board(d, phase)))
        assert tap.compare(str(p), str(model_probe)) == 0
    b = as_board(d, 2)
    r = next(r for r in b["results"] if r["name"] == "ping")
    r["tx_edges"][100][0] += 1
    p.write_text(json.dumps(b))
    assert tap.compare(str(p), str(model_probe)) == 1
    b = as_board(d)
    b["results"][0]["rx_fifo_words"][3] ^= 1 << 8
    p.write_text(json.dumps(b))
    assert tap.compare(str(model_probe), str(p)) == 1
    assert tap.compare(str(model_probe), str(model_probe)) == 0


def test_a_board_at_another_clock_is_refused():
    with pytest.raises(RuntimeError, match="not 40"):
        fe.FabricEth(fake_board()[0], 40e6)


# ---------------------------------------------------------------- the board: guard first, give back always
@pytest.fixture
def no_devmem(monkeypatch):
    real = os.open

    def guarded(path, *a, **kw):
        assert path != "/dev/mem", "/dev/mem opened"
        return real(path, *a, **kw)
    monkeypatch.setattr(os, "open", guarded)
    monkeypatch.setattr(fg, "_checked", None)

    def no_rig(*a, **kw):
        raise AssertionError("HwRig built")
    monkeypatch.setattr(pfd, "HwRig", no_rig)


@pytest.mark.parametrize("active,why", [(None, "didn't run"), ("protoemu40", "needs 'protoemu'"),
                                        ("protoemu48", "needs 'protoemu'")])
def test_board_backend_refuses_before_touching_the_fabric(monkeypatch, no_devmem, active, why):
    def check(**kw):
        if active is None:
            raise fg.NoActiveApp("`xmutil listapps` didn't run")
        return active
    monkeypatch.setattr(fg, "check", check)
    tap = load_bridge()
    with pytest.raises(fg.NoActiveApp, match=why):
        tap.FpgaBackend(CHIP_IP, 50e6)
    assert tap.main(["--backend", "fpga", "--probe"]) == 2


def test_board_backend_at_40mhz_needs_protoemu40(monkeypatch, no_devmem):
    monkeypatch.setattr(fg, "check", lambda **kw: "protoemu")
    with pytest.raises(fg.NoActiveApp, match="needs 'protoemu40'"):
        fe.board_rig(40e6, None)


def test_measure_spim_needs_the_app_for_its_clock(monkeypatch, no_devmem, capsys):
    monkeypatch.setattr(fg, "check", lambda **kw: "protoemu40")
    tap = load_bridge()
    assert tap.main(["--measure-spim"]) == 2
    assert "needs 'protoemu'" in capsys.readouterr().err


class Closing:
    def __init__(self, on_close=None):
        self.closed, self.on_close = 0, on_close

    def close(self):
        if self.on_close:
            self.on_close()
        self.closed += 1


@pytest.fixture
def sentinels():
    """Python handlers on the signals, so one run_closing fails to catch shows up as a
    test failure instead of killing pytest."""
    nums = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)

    def sentinel(signum, frame):
        pass
    old = [signal.signal(n, sentinel) for n in nums]
    yield lambda: all(signal.getsignal(n) is sentinel for n in nums)
    for n, h in zip(nums, old):
        signal.signal(n, h)


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGHUP, signal.SIGINT])
def test_run_closing_gives_the_board_back_on_a_signal(signum, sentinels):
    """SIGTERM is `timeout`, SIGHUP a dropped ssh session; a second signal of any kind
    during the close doesn't cut it short."""
    tap = load_bridge()
    b = Closing(on_close=lambda: [os.kill(os.getpid(), n) for n in (signum, signal.SIGINT)])

    def body(backend):
        assert backend is b
        os.kill(os.getpid(), signum)
        return 0
    if signum == signal.SIGINT:
        signal.signal(signal.SIGINT, signal.default_int_handler)
        with pytest.raises(KeyboardInterrupt):
            tap.run_closing(lambda held: b, body)
        assert signal.getsignal(signal.SIGINT) is signal.default_int_handler
    else:
        assert tap.run_closing(lambda held: b, body) == 128 + signum
        assert sentinels()
    assert b.closed == 1


def test_run_closing_gives_the_board_back_on_an_exception_and_a_normal_end():
    tap = load_bridge()
    b = Closing()

    def boom(backend):
        raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        tap.run_closing(lambda held: b, boom)
    assert tap.run_closing(lambda held: b, lambda backend: 7) == 7
    assert b.closed == 2


def test_run_closing_handles_signals_while_the_backend_is_built(sentinels):
    """A signal during make() is the session's, not the default kill; nothing was built,
    so nothing is closed."""
    tap = load_bridge()

    def make(held):
        os.kill(os.getpid(), signal.SIGHUP)
        raise AssertionError("the signal should have stopped make()")
    assert tap.run_closing(make, lambda backend: 0) == 128 + signal.SIGHUP
    assert sentinels()


# ---------------------------------------------------------------- a rig stopped while it is being built
def fake_parts(fault=None, bad_id=False):
    """Board, Spim and Lab of a fake KV260; fault() runs inside HwRig's 2 s clock
    measurement (the second clk_hz call: after the select and quad), bad_id makes
    the chip's ID read wrong (as a wrong bitstream would)."""
    fm = pfd.ModelRig(50e6).mh
    board = FakeBoard(regs=FakeDevMem(fm))
    board.f, board.fault, board.calls, board.quad_at_fault = None, fault, 0, None
    mem = FakeSpimMem(fm)
    mem.bad_id = bad_id
    return fm, (board, pe_spim.Spim(mem=mem), pe_lab.Lab(mem=FakeLabMem(fm)))


def raise_(e):
    raise e


FAULTS = {
    "exception": (lambda: raise_(OSError("a read failed")), OSError),
    "ctrl-c": (lambda: raise_(KeyboardInterrupt()), KeyboardInterrupt),
    "sigterm": (lambda: os.kill(os.getpid(), signal.SIGTERM), 128 + signal.SIGTERM),
    "sighup": (lambda: os.kill(os.getpid(), signal.SIGHUP), 128 + signal.SIGHUP),
    "wrong id": (None, AssertionError),
}


@pytest.mark.parametrize("fault", sorted(FAULTS))
def test_a_rig_stopped_while_it_is_built_gives_the_board_back(monkeypatch, sentinels, fault):
    """Verifier report 029, finding 1: through eth_tap.main as on the board. v2_demo.Hw
    has moved the host port to pe_spim and HwRig has turned quad on when the fault
    comes; after it, SEL is back on the GPIO path, quad is off, pe_lab is in GPIO
    mode, and no PMOD register was written anything but 0."""
    action, outcome = FAULTS[fault]
    monkeypatch.setattr(fg, "_checked", None)
    monkeypatch.setattr(fg, "check", lambda **kw: "protoemu")
    fm, parts = fake_parts(fault=action, bad_id=fault == "wrong id")
    board, spim, lab = parts
    monkeypatch.setattr(fe, "board_parts", lambda: parts)
    tap = load_bridge()
    if isinstance(outcome, int):
        assert tap.main(["--backend", "fpga", "--probe"]) == outcome
    else:
        with pytest.raises(outcome):
            tap.main(["--backend", "fpga", "--probe"])
    log = spim.mem.log
    assert (pe_spim.SEL, 1) in log and log[-1] == (pe_spim.SEL, 0)
    assert all(v == 0 for off, v in log if off != pe_spim.SEL)
    if action:
        assert board.quad_at_fault == 1                     # the window was open
    assert fm.chip.hostcfg == 0
    assert lab.mem.modes[-1] == pfd.GPIO
    assert sentinels()


def test_a_ctrl_c_at_the_id_read_gives_the_board_back(monkeypatch, sentinels):
    """Verifier report 031, note 1: inside v2_demo.Hw, after the host port has moved to
    the fabric master and before HwRig's clock measurement."""
    monkeypatch.setattr(fg, "_checked", None)
    monkeypatch.setattr(fg, "check", lambda **kw: "protoemu")
    fm, parts = fake_parts()
    board, spim, lab = parts
    spim.mem.on_id = lambda: raise_(KeyboardInterrupt())
    monkeypatch.setattr(fe, "board_parts", lambda: parts)
    with pytest.raises(KeyboardInterrupt):
        load_bridge().main(["--backend", "fpga", "--probe"])
    assert (pe_spim.SEL, 1) in spim.mem.log and spim.mem.log[-1] == (pe_spim.SEL, 0)
    assert fm.chip.hostcfg == 0 and lab.mem.modes[-1] == pfd.GPIO and sentinels()


@pytest.mark.parametrize("fault", ["exception", "ctrl-c", "sigterm", "sighup"])
def test_a_backend_stopped_while_fabric_eth_is_built_gives_the_board_back(monkeypatch, sentinels, fault):
    """Verifier report 031, finding 1: the rig is whole (host port on the fabric
    master, quad on) and FabricEth's constructor is where the end comes."""
    action, outcome = FAULTS[fault]
    monkeypatch.setattr(fg, "_checked", None)
    monkeypatch.setattr(fg, "check", lambda **kw: "protoemu")
    fm, parts = fake_parts()
    board, spim, lab = parts
    monkeypatch.setattr(fe, "board_parts", lambda: parts)
    quad = []

    class Asm:
        @staticmethod
        def assemble(*a, **kw):
            quad.append(fm.chip.hostcfg & 1)
            action()
            raise AssertionError("the fault should have ended the build")
    monkeypatch.setattr(fe, "asm", Asm)
    tap = load_bridge()
    if isinstance(outcome, int):
        assert tap.main(["--backend", "fpga", "--probe"]) == outcome
    else:
        with pytest.raises(outcome):
            tap.main(["--backend", "fpga", "--probe"])
    assert quad == [1]                                       # the rig was whole, quad on
    log = spim.mem.log
    assert log[-1] == (pe_spim.SEL, 0) and all(v == 0 for off, v in log if off != pe_spim.SEL)
    assert fm.chip.hostcfg == 0 and lab.mem.modes[-1] == pfd.GPIO and sentinels()


def test_give_back_runs_every_step_and_reports_the_first_failure():
    fm, (board, spim, lab) = fake_parts()
    spim.select(fabric=True)
    board.set_inputs = lambda **kw: raise_(OSError("gpio"))
    with pytest.raises(OSError, match="gpio"):
        fe.give_back(board, spim, lab)
    assert spim.mem.log[-1] == (pe_spim.SEL, 0) and lab.mem.modes[-1] == pfd.GPIO


def test_give_back_goes_on_when_the_select_cannot_be_read():
    """Verifier report 031, note 2."""
    fm, (board, spim, lab) = fake_parts()
    spim.select(fabric=True)
    real = spim.mem.rd
    spim.mem.rd = lambda off: raise_(OSError("SEL")) if off == pe_spim.SEL else real(off)
    with pytest.raises(OSError, match="SEL"):
        fe.give_back(board, spim, lab)
    assert spim.mem.log[-1] == (pe_spim.SEL, 0) and lab.mem.modes[-1] == pfd.GPIO


# ---------------------------------------------------------------- the holder (verifier report 034)
import board_hold                               # noqa: E402
import pe.eth                                   # noqa: E402


def test_the_closer_stops_the_cores_once_the_rig_exists():
    """With a rig, the holder's closer is HwRig.close (cores stopped too); give_back
    alone wouldn't stop them."""
    fm, (board, spim, lab) = fake_parts()
    closer = fe.BoardCloser(board, spim, lab)
    closer.rig = pfd.HwRig(hw=v2_demo.Hw(board=board, spim=spim, lab=lab, f_clk=50e6))
    closer.rig.write(sim.A_CTRL, 0b11)
    fm.step(4)                                          # a bus write lands the next cycle
    assert fm.chip.enable == 0b11 and fm.chip.hostcfg == 1
    closer()
    fm.step(4)
    assert fm.chip.enable == 0 and fm.chip.hostcfg == 0 and spim.mem.log[-1] == (pe_spim.SEL, 0)


def test_held_closes_once_and_says_whether_it_held_anything():
    h, calls = board_hold.Held(), []
    assert h.close() is False
    h.hold(lambda: calls.append(1))
    assert h.close() is True and h.close() is False and calls == [1]


def test_board_rig_needs_a_holder(monkeypatch):
    monkeypatch.setattr(fg, "_checked", None)
    monkeypatch.setattr(fg, "check", lambda **kw: "protoemu")
    monkeypatch.setattr(fe, "board_parts", lambda: pytest.fail("parts built without a holder"))
    with pytest.raises(ValueError, match="holder"):
        fe.board_rig(50e6, None)


def board_end_state(fm, board, spim, lab):
    """The board as run_closing must leave it: host port on the GPIO path (if it ever
    left it), quad off, pe_lab in GPIO mode, cores stopped, PMOD never enabled."""
    log = spim.mem.log
    sel = [v for off, v in log if off == pe_spim.SEL]
    assert not sel or sel[-1] == 0, f"SEL writes {sel}"
    assert all(v == 0 for off, v in log if off != pe_spim.SEL)
    assert fm.chip.hostcfg == 0, "quad on"
    assert not lab.mem.modes or lab.mem.modes[-1] == pfd.GPIO
    fm.step(4)                                          # a CTRL write lands the next cycle
    assert fm.chip.enable == 0


@pytest.mark.parametrize("call", ["mac", "Station"])
@pytest.mark.parametrize("kind", ["ctrl-c", "sigterm"])
def test_verifier_034_window_after_fabric_eth(monkeypatch, sentinels, capsys, call, kind):
    """Report 034's test_v034_window.py cases: FabricEth.__init__ finishes, then the
    first eth.mac or eth.Station call faults."""
    monkeypatch.setattr(fg, "_checked", None)
    monkeypatch.setattr(fg, "check", lambda **kw: "protoemu")
    fm, parts = fake_parts()
    monkeypatch.setattr(fe, "board_parts", lambda: parts)
    real = getattr(pe.eth, call)
    seen = []

    def fault(*a, **kw):
        if not seen:
            seen.append(1)
            assert fm.chip.hostcfg == 1                  # the rig is whole: quad on
            if kind == "ctrl-c":
                raise KeyboardInterrupt
            os.kill(os.getpid(), signal.SIGTERM)
        return real(*a, **kw)
    monkeypatch.setattr(pe.eth, call, fault)
    tap = load_bridge()
    if kind == "ctrl-c":
        with pytest.raises(KeyboardInterrupt):
            tap.main(["--backend", "fpga", "--probe"])
    else:
        assert tap.main(["--backend", "fpga", "--probe"]) == 128 + signal.SIGTERM
        assert "giving the board back" in capsys.readouterr().err
    assert seen
    board_end_state(fm, *parts)
    assert sentinels()


# ---------------------------------------------------------------- every line from the first write to run_closing's backend
TRACED = (str(HOST), str(ROOT / "tools/micropython"))   # the board code; not the model, the assembler or these fakes


def injected_run(monkeypatch, kind, at, grain="line"):
    """eth_tap.main on the fake KV260 with a fault injected at event number `at` (None:
    count them), events being the board code's (TRACED) `line` events or, grain
    "opcode", its opcode events. Counting starts at the call of Board.reset, the first
    write after board_rig hands the holder its closer, so before the host port first
    moves (SEL), and stops at the first event of run_closing that has the backend.
    Returns (events, outcome, stderr)."""
    fm, parts = fake_parts()
    board, spim, lab = parts
    monkeypatch.setattr(fe, "board_parts", lambda: parts)
    reset_code = pe_kria.Board.reset.__code__
    st = dict(armed=False, n=0, fired=False, done=False)
    tap = load_bridge()

    def tracer(frame, event, arg):
        if st["done"]:
            return None
        code = frame.f_code
        if event == "call":
            if code is reset_code:
                st["armed"] = True
            if not code.co_filename.startswith(TRACED):
                return None
            if grain == "opcode":
                frame.f_trace_opcodes = True
                sys.settrace(tracer)                    # 3.12: the flag takes effect on a re-arm
            return tracer
        if event != grain or not st["armed"]:
            return tracer
        if code.co_name == "run_closing" and frame.f_locals.get("backend") is not None:
            st["done"] = True
            sys.settrace(None)                          # the probe itself runs untraced
            return None
        if at is not None and st["n"] == at:
            st["fired"], st["done"] = True, True
            sys.settrace(None)
            if kind == "ctrl-c":
                raise KeyboardInterrupt
            os.kill(os.getpid(), signal.SIGTERM)    # run_closing's handler raises at the next check
            return None
        st["n"] += 1
        return tracer
    err = io.StringIO()
    monkeypatch.setattr(sys, "stderr", err)
    sys.settrace(tracer)
    try:
        outcome = tap.main(["--backend", "fpga", "--probe"])
    except KeyboardInterrupt:
        outcome = "KeyboardInterrupt"
    finally:
        sys.settrace(None)
        monkeypatch.undo()                              # stderr and board_parts back
    assert st["armed"]
    if at is not None:
        assert st["fired"], f"point {at} not reached"
    board_end_state(fm, board, spim, lab)
    return st["n"], outcome, err.getvalue()


SHARDS = {"line": 8, "opcode": 32}
_points = {}


def window_points(grain):
    """How many events of this grain the window has (one clean run, once a process)."""
    if grain not in _points:
        mp = pytest.MonkeyPatch()
        try:
            mp.setattr(fg, "_checked", None)
            mp.setattr(fg, "check", lambda **kw: "protoemu")
            n, outcome, _ = injected_run(mp, None, None, grain)
        finally:
            mp.undo()
        assert outcome == 0
        _points[grain] = n
    return _points[grain]


@pytest.mark.parametrize("grain,shard", [(g, s) for g in SHARDS for s in range(SHARDS[g])])
@pytest.mark.parametrize("kind", ["ctrl-c", "sigterm"])
def test_every_point_in_the_window_gives_the_board_back(monkeypatch, sentinels, kind, grain, shard):
    """Verifier report 034, asked for structurally: a Ctrl-C, and separately a real
    SIGTERM, at every line event, and then at every opcode event (report 037, note 2),
    of the board code from the call of Board.reset (just after the holder has the
    board, before the first SEL write) until run_closing holds the backend. Each run
    ends as run_closing promises, and the board is given back."""
    n = window_points(grain)
    assert n > (2000 if grain == "opcode" else 200)
    for at in range(shard, n, SHARDS[grain]):
        monkeypatch.setattr(fg, "_checked", None)
        monkeypatch.setattr(fg, "check", lambda **kw: "protoemu")
        _, outcome, err = injected_run(monkeypatch, kind, at, grain)
        if kind == "ctrl-c":
            assert outcome == "KeyboardInterrupt", f"{grain} {at}: {outcome}"
        else:
            assert outcome == 128 + signal.SIGTERM, f"{grain} {at}: {outcome}"
            assert "giving the board back" in err, f"{grain} {at}: {err}"
    assert sentinels()


def test_the_window_point_count(capsys):
    lines, opcodes = window_points("line"), window_points("opcode")
    with capsys.disabled():
        print(f"\nfabric-ping window: {lines} line events, {opcodes} opcode events of the board code")


# ---------------------------------------------------------------- run_closing's own way out (verifier report 037, note 1)
def finally_run(tap, end, at):
    """run_closing with a holder whose closer counts, the body ending as `end` says,
    and a real SIGTERM sent at opcode event number `at` (None: count them) of
    run_closing's frame, from the body's last statement until Held.close
    is called: the rest of the try, the except taken, and the finally up to the close.
    Returns (events, outcome, closes)."""
    rc_code, close_code = tap.run_closing.__code__, board_hold.Held.close.__code__
    closes, st = [], dict(armed=False, n=0, done=False, fired=False)

    def tracer(frame, event, arg):
        if st["done"]:
            return None
        code = frame.f_code
        if event == "call":
            if code is close_code and st["armed"]:
                st["done"] = True
                sys.settrace(None)
                return None
            if code is rc_code or (code.co_name == "block" and code.co_filename == rc_code.co_filename):
                frame.f_trace_opcodes = True
                sys.settrace(tracer)
                return tracer
            return None
        if event == "opcode" and st["armed"]:
            if at is not None and st["n"] == at:
                st["fired"], st["done"] = True, True
                sys.settrace(None)
                os.kill(os.getpid(), signal.SIGTERM)
                return None
            st["n"] += 1
        return tracer

    def make(held):
        held.hold(lambda: closes.append(1))
        return "backend"

    def body(backend):
        st["armed"] = True
        if end == "exception":
            raise OSError("the body failed")
        if end == "ctrl-c":
            raise KeyboardInterrupt
        return 7
    sys.settrace(tracer)
    try:
        outcome = tap.run_closing(make, body)
    except BaseException as e:                          # noqa: BLE001 - the outcome is what is checked
        outcome = type(e).__name__
    finally:
        sys.settrace(None)
    if at is not None:
        assert st["fired"], f"opcode {at} not reached"
    return st["n"], outcome, len(closes)


FINAL_ENDS = {"return": (7,), "exception": ("OSError",), "ctrl-c": ("KeyboardInterrupt",)}


@pytest.mark.parametrize("end", sorted(FINAL_ENDS))
def test_no_opcode_on_the_way_out_lets_a_signal_skip_the_close(sentinels, end, capsys):
    """A SIGTERM at every opcode from the body's last statement to the call of
    Held.close, after a normal end, an exception and a Ctrl-C in the body. The close
    happens once every time, and the run ends as the body did or as the signal says
    (143), never with the signal escaping past the close."""
    tap = load_bridge()
    n, outcome, closes = finally_run(tap, end, None)
    assert outcome in FINAL_ENDS[end] and closes == 1
    assert n >= 10
    for at in range(n):
        _, outcome, closes = finally_run(tap, end, at)
        assert closes == 1, f"opcode {at}: closed {closes} times, outcome {outcome}"
        # 143: the signal ended the run in the try; RunStopped: it arrived while an
        # exception from the body was on its way out, and replaced it. Either way after the close.
        assert outcome in FINAL_ENDS[end] + (128 + signal.SIGTERM, "RunStopped"), f"opcode {at}: {outcome}"
    assert sentinels()
    with capsys.disabled():
        print(f"\nrun_closing way out after {end}: {n} opcode events")
