"""eth_tap.FpgaBackend and fpga/kv260/host/fabric_eth.py: real-Linux-shaped frames
through the chip with no cable, on the cycle model (ModelRig, pe_lab modelled) and
on a fake KV260 whose pe_spim and pe_lab register maps drive that model, so the
board's code path, register writes included, runs here. No hardware: evidence
level "model". Judges: zlib for the FCS and this file's own frame builders and
checkers (test_eth_stack's far end), not pe.eth."""
import importlib.util
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
    def clk_hz(self, interval=0.5):
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
    assert fm.chip.read(sim.A_HOSTCFG) & 1 == 0


def as_board(d, phase=0):
    """A model probe file as a board's: one run, at one divider phase."""
    d = json.loads(json.dumps(d))
    d["rig"] = "KV260"
    for r in d["results"]:
        if "tx_edges_phases" in r:
            r["tx_edges"] = r.pop("tx_edges_phases")[phase]
    return d


def test_the_model_gives_four_distinct_divider_phases(model_probe):
    """Five trigger arrivals over the 5-clock pattern, four ticks in it: two arrivals
    meet the same tick, so four different waveforms after the trigger."""
    for r in json.loads(model_probe.read_text())["results"]:
        if r["replies"]:
            ph = r["tx_edges_phases"]
            assert len(ph) == 5 and all(ph) and len({json.dumps(p) for p in ph}) == 4
            assert ph[0] == r["tx_edges"]


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
        fe.board_rig(40e6)


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

    def body():
        os.kill(os.getpid(), signum)
        return 0
    if signum == signal.SIGINT:
        signal.signal(signal.SIGINT, signal.default_int_handler)
        with pytest.raises(KeyboardInterrupt):
            tap.run_closing(b, body)
        assert signal.getsignal(signal.SIGINT) is signal.default_int_handler
    else:
        assert tap.run_closing(b, body) == 128 + signum
        assert sentinels()
    assert b.closed == 1


def test_run_closing_gives_the_board_back_on_an_exception_and_a_normal_end():
    tap = load_bridge()
    b = Closing()

    def boom():
        raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        tap.run_closing(b, boom)
    assert tap.run_closing(b, lambda: 7) == 7
    assert b.closed == 2
