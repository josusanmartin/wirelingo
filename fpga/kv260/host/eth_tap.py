#!/usr/bin/env python3
"""Connect a Linux TAP interface to the chip, so the real Linux network stack
ARPs and pings it.

    sudo unshare --net -- python3 fpga/kv260/host/eth_tap.py --model --selftest

--model runs the chip on the cycle model. Linux's frames go out on a Manchester
waveform into the RXP pad, the replies come off the TD+/TD- pads, and in between
are eth10_rx, eth10_tx_cs, pe.ethhost over a timed SPI port, and pe.eth. The
model is thousands of times slower than 10 Mbit/s, so a round trip here measures
Python. Simulated time stops while nothing is in flight, so the chip's own link
pulses don't run here; the report gives the simulated round trip too.

Network safety, enforced before anything is created: this program refuses to run
outside a dedicated network namespace, and refuses a namespace with any interface
but lo. `unshare --net` makes one that disappears with the process, so there is
nothing to clean up, no route outside it, and eth0 can't be reached from here.

A backend is anything with Backend's methods. FpgaBackend is the chip RTL in the
KV260's fabric with no cable (fabric_eth.py): pe_lab's pattern generator plays
each of Linux's frames into eth10_rx, and eth10_tx_cs's reply is sampled off TD
by the chip's other core. One frame at a time, at most 64 bytes each way: ping
with -s 18 at most. --backend fpga-model runs that same code on the cycle model.

    sudo python3 -B fpga/kv260/host/eth_tap.py --backend fpga --probe --save b.json   # no network at all
    sudo unshare --net -- python3 -B fpga/kv260/host/eth_tap.py --backend fpga --selftest
    python3 fpga/kv260/host/eth_tap.py --compare b.json m.json    # m.json: the same probe, fpga-model

On the board nothing maps the fabric until fabric_guard shows the app for --clock
(protoemu at 50 MHz) as the one active row; nothing here loads an app. The board
is given back (cores stopped, pe_lab idle, host port on the GPIO path, quad off,
PMOD disabled) on any end: normal, an exception, Ctrl-C, SIGTERM, SIGHUP.
"""
import argparse
import fcntl
import json
import os
import pathlib
import re
import select
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import fabric_guard                            # noqa: E402
import pe_spim                                 # noqa: E402
from pe import eth, ethsim                     # noqa: E402

TUNSETIFF = 0x400454CA
IFF_TAP, IFF_NO_PI = 0x0002, 0x1000
SETTLE_US = 150                                # simulated time after the last activity
CHIP_MAC_TEXT, OTHER_MAC_TEXT = "02:57:4c:00:00:02", "02:57:4c:00:00:03"
# The TAP's own MAC for the fpga backends: Linux's ARP request from it is 974
# generator edges (a random MAC needs up to about 1014 of the 1024)
HOST_MAC_TEXT = "02:57:4c:55:55:01"
FABRIC = ("fpga", "fpga-model")
FABRIC_PING = 18                               # ping data bytes: a 64-byte frame


class NetnsError(RuntimeError):
    pass


def require_dedicated_netns(ns_self="/proc/self/ns/net", ns_init="/proc/1/ns/net",
                            interfaces=socket.if_nameindex):
    """Raise unless this process is in a network namespace of its own, holding
    nothing but lo. Runs before anything else, with no way to turn it off.
    (Not /sys/class/net: sysfs shows the namespace it was mounted in.)"""
    try:
        here, init = os.stat(ns_self).st_ino, os.stat(ns_init).st_ino
    except OSError as e:
        raise NetnsError(f"can't compare network namespaces ({e}): run as root inside `unshare --net`")
    if here == init:
        raise NetnsError("in the host's network namespace: run inside `unshare --net`")
    others = sorted({name for _, name in interfaces()} - {"lo"})
    if others:
        raise NetnsError(f"this namespace holds {others}: it must be dedicated to the test")


class Tap:
    """An Ethernet TAP in this namespace, frames without FCS."""

    def __init__(self, name):
        self.name = name
        self.fd = os.open("/dev/net/tun", os.O_RDWR | os.O_NONBLOCK)
        fcntl.ioctl(self.fd, TUNSETIFF, struct.pack("16sH", name.encode(), IFF_TAP | IFF_NO_PI))

    def up(self, cidr, mtu=1500, mac=None, ipv6=True):
        """mac: the TAP's own address; ipv6=False keeps Linux's IPv6 chatter off it
        (this namespace only)."""
        cmds = [["ip", "link", "set", "lo", "up"], ["ip", "link", "set", self.name, "mtu", str(mtu)]]
        if mac:
            cmds.append(["ip", "link", "set", self.name, "address", mac])
        for cmd in cmds:
            subprocess.run(cmd, check=True)
        v6 = pathlib.Path(f"/proc/sys/net/ipv6/conf/{self.name}/disable_ipv6")
        if not ipv6 and v6.exists():
            v6.write_text("1")
        for cmd in (["ip", "addr", "add", cidr, "dev", self.name], ["ip", "link", "set", self.name, "up"]):
            subprocess.run(cmd, check=True)

    def read(self):
        try:
            return os.read(self.fd, 2048)
        except BlockingIOError:
            return None

    def write(self, frame):
        os.write(self.fd, frame)


class Backend:
    """The chip as a NIC's far side. Frames are destination..payload, no FCS,
    which is how a TAP passes them. Single-threaded: run() calls these in turn."""

    def send(self, frame):
        """Linux sent this frame to the chip."""
        raise NotImplementedError

    def poll(self):
        """Move the chip on for a moment; returns the frames it sent, FCS checked."""
        raise NotImplementedError

    def busy(self):
        """True while poll() has work: otherwise the bridge sleeps in select()."""
        raise NotImplementedError

    def report(self):
        return {}

    def close(self):
        """Give the hardware back, if there is any."""


class ModelBackend(Backend):
    """pe.ethsim.Rig, one simulated cable and one Station. corrupt = n spoils the
    FCS of the next n IPv4 frames from Linux on their way to the chip."""
    SLICE_S = 0.02

    def __init__(self, ipaddr, clock_hz=50e6, quad=True, sck_div=2, gap=0):
        self.rig = ethsim.Rig(clock_hz=clock_hz, quad=quad, sck_div=sck_div, gap=gap, ipaddr=ipaddr)
        self.seen = 0
        self.last = 0.0
        self.requests = {}                     # (ident, seq) -> simulated start of the request
        self.rtt_us = []
        self.oversize = 0
        self.corrupt = 0
        self.rig.run_us(20)

    def send(self, frame):
        frame = frame + bytes(max(0, eth.MIN_FRAME - eth.FCS - len(frame)))      # a NIC pads
        if len(frame) > eth.MAX_FRAME - eth.FCS:
            self.oversize += 1
            return
        self._note(frame, True)
        fcs = eth.crc32(frame)
        if self.corrupt and frame[12:14] == b"\x08\x00":
            self.corrupt -= 1
            fcs ^= 1
        self.rig.wire.send(frame + fcs.to_bytes(4, "little"))
        self.last = self.rig.now_us()

    def _note(self, frame, outward, t=None):
        """Simulated round trip: the request's first edge to the reply's last."""
        if frame[12:14] != b"\x08\x00":
            return
        ip = eth.parse_ipv4(frame[14:])
        if ip is None or ip[2] != eth.PROTO_ICMP:
            return
        e = eth.parse_icmp_echo(ip[3])
        if e is None:
            return
        if outward:
            self.requests[e[1:3]] = self.rig.now_us()
        elif e[1:3] in self.requests:
            self.rtt_us.append(t / 1000 - self.requests.pop(e[1:3]))

    def poll(self):
        rig, out = self.rig, []
        end = time.monotonic() + self.SLICE_S
        while time.monotonic() < end and self.busy():
            rig.poll()
            bursts = rig.wire.bursts
            while self.seen < len(bursts):
                b = bursts[self.seen]
                self.seen += 1
                self.last = rig.now_us()
                d = None if b.pulse else ethsim.manchester_decode(b.edges)
                raw = ethsim.frame_from_bits(d[0]) if d else None
                if raw is not None and eth.fcs_ok(raw):
                    frame = raw[:-eth.FCS]
                    self._note(frame, False, b.t1)
                    out.append(frame)
            if out:
                break
        return out

    def busy(self):
        rig = self.rig
        return not rig.quiet() or rig.now_us() < self.last + SETTLE_US

    def report(self):
        rig = self.rig
        return dict(simulated_us=round(rig.now_us()), clocks=rig.mh.chip.cycle, host=rig.host.stats,
                    station=rig.station.stats, simulated_rtt_us=[round(x, 1) for x in self.rtt_us],
                    far_end_frames=len(rig.wire.sent), oversize=self.oversize,
                    td_both_high=rig.wire.both_high)


class FpgaBackend(Backend):
    """The chip RTL in the KV260's fabric, one frame at a time and no cable
    (fabric_eth.py). send() plays Linux's frame into eth10_rx and hands what the
    chip recorded to pe.eth.Station, the stack's host side; poll() has eth10_tx_cs
    send each reply and returns the frames read off TD with a good FCS.

    rig: a postfreeze_demo rig. None is the board, and fabric_guard runs before it
    is touched; `model()` is the cycle model with pe_lab modelled, through the same
    code. corrupt = n spoils the FCS of the next n IPv4 frames from Linux."""
    KEEP = 20                                  # exchanges kept for report()

    def __init__(self, ipaddr, clock_hz=50e6, rig=None, **_):
        import fabric_eth
        self.fe = fabric_eth
        rig = fabric_eth.board_rig(clock_hz) if rig is None else rig
        try:
            self.fab = fabric_eth.FabricEth(rig, clock_hz)
        except Exception:
            rig.close()
            raise
        self.station = eth.Station(eth.mac(CHIP_MAC_TEXT), ipaddr)
        self.replies, self.log, self.stats = [], [], {}
        self.corrupt = 0

    @classmethod
    def model(cls, ipaddr, clock_hz=50e6, **kw):
        import postfreeze_demo
        return cls(ipaddr, clock_hz, rig=postfreeze_demo.ModelRig(clock_hz))

    def count(self, name):
        self.stats[name] = self.stats.get(name, 0) + 1

    def _keep(self, entry):
        self.log = (self.log + [entry])[-self.KEEP:]

    def send(self, frame):
        frame = frame + bytes(max(0, eth.MIN_FRAME - eth.FCS - len(frame)))      # a NIC pads
        if len(frame) > eth.MAX_FRAME - eth.FCS:
            self.count("oversize")
            return
        fcs = eth.crc32(frame)
        if self.corrupt and frame[12:14] == b"\x08\x00":
            self.corrupt -= 1
            fcs ^= 1
        raw = frame + fcs.to_bytes(4, "little")
        try:
            got, info = self.fab.receive(raw)
        except self.fe.TooBig as e:
            self.count("rx_too_big")
            self._keep(dict(rx=len(raw), why=str(e)))
            return
        except self.fe.RxGap as e:
            self.count("rx_gap")
            self._keep(dict(rx=len(raw), why=str(e)))
            return
        info.pop("fifo_words", None)
        self._keep(dict(rx=len(raw), same=got == raw, **info))
        if got is None:
            self.count("rx_lost")
            return
        self.count("rx_frames")
        if got != raw:
            self.count("rx_differs")
        self.replies += self.station.handle(got)

    def poll(self):
        out = []
        while self.replies:
            r = self.replies.pop(0)
            try:
                f, info = self.fab.transmit(r)
            except self.fe.TooBig as e:
                self.count("tx_too_big")
                self._keep(dict(tx=len(r), why=str(e)))
                continue
            for x in info["frames"]:
                x.pop("edges")
            self._keep(dict(tx=len(r), same=f == r, **info))
            if f is None or not eth.fcs_ok(f):
                self.count("tx_lost")
                continue
            self.count("tx_frames")
            if f != r:
                self.count("tx_differs")
            out.append(f[:-eth.FCS])
        return out

    def busy(self):
        return bool(self.replies)

    def report(self):
        rig = self.fab.rig
        return dict(rig="cycle model" if rig.model else "KV260", ident=rig.ident, clock_mhz=rig.f_clk / 1e6,
                    f_meas_mhz=None if rig.model else round(rig.f_meas / 1e6, 5), stats=self.stats,
                    station=self.station.stats, last=self.log)

    def close(self):
        self.fab.close()


BACKENDS = {"model": ModelBackend, "fpga": FpgaBackend, "fpga-model": FpgaBackend.model}


class Bridge:
    def __init__(self, tap, backend):
        self.tap, self.backend = tap, backend
        self.to_chip = self.from_chip = 0

    def step(self, block_s=0.05):
        """One round: frames from Linux, a slice of chip, frames to Linux."""
        wait = 0 if self.backend.busy() else block_s
        r, _, _ = select.select([self.tap.fd], [], [], wait)
        if r:
            while True:
                f = self.tap.read()
                if f is None:
                    break
                self.to_chip += 1
                self.backend.send(f)
        for f in self.backend.poll():
            self.from_chip += 1
            self.tap.write(f)

    def run_while(self, keep_going, timeout_s):
        end = time.monotonic() + timeout_s
        while keep_going():
            if time.monotonic() > end:
                return False
            self.step()
        return True


def measure_spim(spim, passes=2000):
    """us per RX service pass from this Python on pe_spim: a STATUS read and a
    4-word FIFO read, as Eth10Host does. Core 0 should be idle: a FIFO read pops."""
    status, fifo = 0x400 + 9, 0x400 + 0x10
    t = time.monotonic()
    for _ in range(passes):
        spim.read(status)
        spim.read(fifo, 4, fixed=True)
    return (time.monotonic() - t) / passes * 1e6


class SessionSignal(Exception):
    """A signal ended the session, which then gave the port back."""

    def __init__(self, signum):
        super().__init__(f"signal {signum}")
        self.signum = signum


def measure_spim_session(spim, passes=2000):
    """measure_spim with the host port borrowed and given back, on a board nobody may
    be able to reach. It moves the host pins to the fabric master and sets QUAD_EN, as
    the measurement needs. The select goes back to what it was, and QUAD_EN to 0, its
    reset value, because the register is write-only and can't be read first.

    That happens on a normal end, on any exception, on SIGINT, SIGTERM (`timeout`) and
    SIGHUP (a dropped ssh session), which raise SessionSignal once the port is back. The
    first signal raises; any after it are ignored, so one can't cut the restore short.
    With the master stuck busy QUAD_EN can't be cleared: the select is still written,
    and the error says so. SIGKILL can't be caught."""
    live = [True]

    def stop(signum, frame):
        if live[0]:
            live[0] = False
            raise SessionSignal(signum)

    signals = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
    old = [signal.signal(n, stop) for n in signals]
    sel = None
    try:
        sel = spim.mem.rd(pe_spim.SEL)
        spim.select(fabric=True)
        spim.enable_quad()
        return measure_spim(spim, passes)
    finally:
        live[0] = False            # from here on a signal raises nothing
        try:
            if sel is not None:
                spim.enable_quad(False)
        finally:
            if sel is not None:
                spim.mem.wr(pe_spim.SEL, sel)
            for n, handler in zip(signals, old):
                signal.signal(n, handler)


def sysctl(path, value):
    pathlib.Path("/proc/sys", path).write_text(str(value))


def ping(bridge, target, count, size, wait_s):
    """Run ping inside this namespace while the bridge runs. Returns (received,
    wall-clock rtt avg in ms or None, raw summary)."""
    cmd = ["ping", "-n", "-c", str(count), "-i", "0.2", "-W", str(wait_s), "-s", str(size), "-M", "do", target]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    bridge.run_while(lambda: p.poll() is None, wait_s * (count + 2))
    if p.poll() is None:
        p.kill()
    out = p.communicate()[0]
    m = re.search(r"(\d+) received", out)
    rtt = re.search(r"= [\d.]+/([\d.]+)/", out)
    return int(m.group(1)) if m else 0, float(rtt.group(1)) if rtt else None, out


def udp_echo(bridge, target, port, data, wait_s):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setblocking(False)
    s.sendto(data, (target, port))
    got = []

    def waiting():
        try:
            got.append(s.recv(2048))
        except BlockingIOError:
            pass
        return not got
    bridge.run_while(waiting, wait_s)
    s.close()
    return got[0] if got else None


def said(f, keep=600):
    """The end of what a subprocess wrote to the temporary file f."""
    f.seek(0)
    return f.read().decode(errors="replace").strip()[-keep:]


def selftest(a, bridge):
    """Real ping and a real UDP socket. Exit status 0 only if every answer came
    and every frame that should go unanswered did."""
    res, pcap, pcap_err = {}, None, None
    if a.pcap:                                 # reported, never part of the pass: a capture is only a record
        if shutil.which("tcpdump") is None:
            res["pcap"] = "not taken: tcpdump is not on PATH"
        else:
            pcap_err = tempfile.TemporaryFile()
            pcap = subprocess.Popen(["tcpdump", "-Z", "root", "-U", "-i", a.tap, "-w", a.pcap],
                                    stdout=subprocess.DEVNULL, stderr=pcap_err)
            time.sleep(0.5)
            if pcap.poll() is not None:
                res["pcap"] = dict(path=a.pcap, tcpdump_status=pcap.returncode, tcpdump_said=said(pcap_err))
                pcap = None
    fabric = a.backend in FABRIC
    size = FABRIC_PING if fabric else 56
    n, rtt, text = ping(bridge, a.chip_ip, a.count, size, a.wait)
    print(text)
    res["ping"] = dict(sent=a.count, received=n, wall_rtt_ms=rtt, data_bytes=size)
    if fabric:
        res["ping_1500_byte_packet"] = "not run: the generator holds a 64-byte frame, the TX FIFOs a 64-byte reply"
    else:
        n, rtt, text = ping(bridge, a.chip_ip, 2, 1472, a.wait)
        print(text)
        res["ping_1500_byte_packet"] = dict(sent=2, received=n, wall_rtt_ms=rtt)
    got = udp_echo(bridge, a.chip_ip, eth.UDP_ECHO_PORT, b"wirelingo", a.wait)
    res["udp_echo"] = got.decode() if got else None
    # Frames the chip must not answer, sent by Linux itself: neighbour entries
    # aim them at the chip's MAC with another IP, and at the chip's IP with another MAC.
    short = min(a.wait, 6)
    n, _, _ = ping(bridge, a.other_ip, 1, size, short)
    res["no_reply_unknown_ip_no_arp"] = n == 0
    subprocess.run(["ip", "neigh", "replace", a.other_ip, "lladdr", CHIP_MAC_TEXT, "dev", a.tap], check=True)
    n, _, _ = ping(bridge, a.other_ip, 1, size, short)
    res["no_reply_other_ip"] = n == 0
    subprocess.run(["ip", "neigh", "replace", a.chip_ip, "lladdr", OTHER_MAC_TEXT, "dev", a.tap], check=True)
    n, _, _ = ping(bridge, a.chip_ip, 1, size, short)
    res["no_reply_other_mac"] = n == 0
    subprocess.run(["ip", "neigh", "replace", a.chip_ip, "lladdr", CHIP_MAC_TEXT, "dev", a.tap], check=True)
    if hasattr(bridge.backend, "corrupt"):
        bridge.backend.corrupt = 1
        n, _, _ = ping(bridge, a.chip_ip, 1, size, short)
        res["no_reply_bad_fcs"] = n == 0
    n, _, _ = ping(bridge, a.chip_ip, 1, size, a.wait)
    res["answers_again"] = n == 1
    if pcap:
        time.sleep(0.5)
        pcap.terminate()
        status = pcap.wait()
        size = os.path.getsize(a.pcap) if os.path.exists(a.pcap) else None
        res["pcap"] = dict(path=a.pcap, bytes=size, tcpdump_status=status, tcpdump_said=said(pcap_err))
        if not size:
            print(f"--pcap: {a.pcap} is {'missing' if size is None else 'empty'}; tcpdump said: "
                  f"{res['pcap']['tcpdump_said']!r}", file=sys.stderr)
    res["frames_to_chip"], res["frames_from_chip"] = bridge.to_chip, bridge.from_chip
    res["backend"] = bridge.backend.report()
    ok = (res["ping"]["received"] == a.count and (fabric or res["ping_1500_byte_packet"]["received"] == 2)
          and res["udp_echo"] == "wirelingo" and res["answers_again"]
          and all(v for k, v in res.items() if k.startswith("no_reply")))
    if fabric:                                 # and the chip's path itself was whole every time
        st = res["backend"]["stats"]
        ok = ok and not any(st.get(k) for k in ("rx_gap", "rx_lost", "rx_differs", "tx_lost", "tx_differs"))
    res["pass"] = ok
    print(json.dumps(res, indent=1))
    return 0 if ok else 1


# ---------------------------------------------------------------- the fabric probe, no network
def probe_frames(chip_ip, host_ip):
    """(name, frame destination..FCS, a reply expected) for --probe: what Linux's
    first exchanges look like, built here, from HOST_MAC_TEXT."""
    hm, cm, om = eth.mac(HOST_MAC_TEXT), eth.mac(CHIP_MAC_TEXT), eth.mac(OTHER_MAC_TEXT)
    hi, ci = eth.ip(host_ip), eth.ip(chip_ip)
    data = struct.pack("<qq", 1793750400, 123456) + bytes((0x10, 0x11))     # ping -s 18: a timeval, then 0x10..
    echo = eth.icmp_echo(eth.ICMP_ECHO_REQUEST, 0x1234, 1, data)
    ping_ = eth.frame(cm, hm, eth.ETH_IP, eth.ipv4(hi, ci, eth.PROTO_ICMP, echo, ident=0x4242))
    bad = ping_[:-1] + bytes([ping_[-1] ^ 1])
    return [("arp", eth.arp_request(hm, hi, ci), True),
            ("ping", ping_, True),
            ("udp", eth.frame(cm, hm, eth.ETH_IP, eth.ipv4(hi, ci, eth.PROTO_UDP,
                                                           eth.udp(hi, ci, 40000, eth.UDP_ECHO_PORT, b"wirelingo"))), True),
            ("bad_fcs", bad, False),
            ("other_mac", eth.frame(om, hm, eth.ETH_IP, eth.ipv4(hi, ci, eth.PROTO_ICMP, echo, ident=0x4242)), False)]


def probe(backend, chip_ip, host_ip):
    """Each probe frame into RXP; the Station's answer out of TD. Returns (pass, results)."""
    fab, out, ok = backend.fab, [], True
    station = eth.Station(eth.mac(CHIP_MAC_TEXT), eth.ip(chip_ip))
    for name, raw, want in probe_frames(chip_ip, host_ip):
        got, rx = fab.receive(raw)
        r = dict(name=name, sent=raw.hex(), received_same=got == raw, rx_records=rx["records"],
                 rx_fifo_words=rx["fifo_words"], rx_zero_words_restored=rx["zero_words_restored"],
                 rx_entries=rx["entries"], rx_wall_ms=rx["wall_ms"])
        replies = station.handle(got) if got is not None else []
        r["replies"] = len(replies)
        good = got == raw and len(replies) == int(want)
        if replies:
            f, tx = fab.transmit(replies[0])
            fr = tx["frames"][0] if len(tx["frames"]) == 1 else {}
            r.update(reply=replies[0].hex(), td_same=f == replies[0], td_fcs_ok=bool(f) and eth.fcs_ok(f),
                     tx_pulses=tx["pulses"], tx_frame_start=fr.get("start"), tx_driven_runs=fr.get("driven_runs"),
                     tx_tp_idl_ns=fr.get("tp_idl_ns"), tx_edges=fr.get("edges"), tx_both_high=tx["both_high"],
                     tx_f_go_cleared=tx["f_go_cleared"], tx_wall_ms=tx["wall_ms"], tx_why=tx.get("why"))
            good = good and f == replies[0] and eth.fcs_ok(f) and link_pulse_ok(tx, fab.clk_ns)
            if fab.rig.model:                  # every phase core 1's divider can have at the trigger
                r["tx_phases"] = [td_record(r)]
                for k in range(1, backend.fe.TX_PHASES):
                    f2, tx2 = fab.transmit(replies[0], phase=k)
                    good = good and f2 == replies[0] and link_pulse_ok(tx2, fab.clk_ns)
                    r["tx_phases"].append(dict(pulses=[list(p) for p in tx2["pulses"]],
                                               edges=tx2["frames"][0]["edges"] if len(tx2["frames"]) == 1 else None))
        r["pass"] = good
        ok = ok and good
        out.append(r)
        print(f"{name}: {len(raw)} bytes in, {'as sent' if got == raw else 'NOT as sent'}, records {rx['records']}, "
              f"{rx['zero_words_restored']} zero words restored; "
              + (f"reply {len(replies[0])} bytes off TD {'as sent' if r['td_same'] else 'NOT as sent'}, "
                 f"FCS {'ok' if r['td_fcs_ok'] else 'BAD'}, link pulse {r['tx_pulses']}, frame from sample "
                 f"{r['tx_frame_start']}, driven runs {r['tx_driven_runs']} clocks, TP_IDL {r['tx_tp_idl_ns']} ns"
                 if replies else "no reply") + f": {'PASS' if good else 'FAIL'}", flush=True)
    return ok, out


DETERMINISTIC = ("sent", "received_same", "rx_records", "rx_fifo_words", "replies", "reply", "td_same")


def link_pulse_ok(tx, clk_ns):
    """eth10_tx_cs starts with one 100 ns link pulse, before the frame's first edge."""
    p, fr = tx["pulses"], tx["frames"]
    return (len(p) == 1 and round(p[0][1] * clk_ns) == 100 and len(fr) == 1
            and p[0][0] + p[0][1] < fr[0]["start"])


def td_record(r):
    """Everything seen on TD after the trigger, in clocks: the link pulses as (start,
    width), then the frame burst's edges, from its first transition to the end of TP_IDL."""
    return dict(pulses=[list(p) for p in r.get("tx_pulses") or []], edges=r.get("tx_edges"))


def tx_timing(x, y):
    """None if TD agrees to the clock, link pulses and the frame's edges both, else
    why not. A model run carries both for each phase of core 1's divider at the
    trigger (FabricEth.transmit), and a board's run must be one of them."""
    if any("tx_edges_phases" in r and "tx_phases" not in r for r in (x, y)):
        return "a model file without link pulses per phase: run --backend fpga-model --probe again with this eth_tap"
    px, py = x.get("tx_phases"), y.get("tx_phases")
    if px and py:
        return None if px == py else "the models' phases differ"
    if px or py:
        phases, seen = (px, td_record(y)) if px else (py, td_record(x))
        if seen in phases:
            print(f"  TD: link pulses {seen['pulses']} and {len(seen['edges'] or [])} frame edges, equal to the "
                  f"model's at divider phase {phases.index(seen)}")
            return None
        best = min(range(len(phases)), key=lambda k: sum(p != q for p, q in zip(phases[k]["edges"] or [],
                                                                                  seen["edges"] or [])))
        pulses = "same" if phases[best]["pulses"] == seen["pulses"] else f"{phases[best]['pulses']} / {seen['pulses']}"
        return (f"TD equal to none of the model's {len(phases)} phases (closest by frame edges: phase {best}; "
                f"link pulses there {pulses})")
    return None if td_record(x) == td_record(y) else "TD differs"


def compare(path_a, path_b):
    """Two --probe --save files, field by field: the board's and the model's must
    agree to the clock. Exit status 0 only if every deterministic field is equal and
    the TD edges are one of the model's divider phases (tx_timing)."""
    a, b = (json.loads(pathlib.Path(p).read_text()) for p in (path_a, path_b))
    ra, rb = ({r["name"]: r for r in x["results"]} for x in (a, b))
    print(f"{path_a}: {a['rig']}, {a['ident'].get('rtl_rev', '?')[:12]}; {path_b}: {b['rig']}, "
          f"{b['ident'].get('rtl_rev', '?')[:12]}")
    same = sorted(ra) == sorted(rb)
    for name in sorted(set(ra) | set(rb)):
        x, y = ra.get(name, {}), rb.get(name, {})
        diff = [k for k in DETERMINISTIC if x.get(k) != y.get(k)]
        why = tx_timing(x, y) if "reply" in x or "reply" in y else None
        same = same and not diff and why is None
        print(f"{name}: {'same' if not diff and why is None else 'differs in ' + ', '.join(diff + ([why] if why else []))}")
        for k in diff:
            u, v = x.get(k), y.get(k)
            if isinstance(u, list) and isinstance(v, list):
                i = next((i for i, (p, q) in enumerate(zip(u, v)) if p != q), min(len(u), len(v)))
                print(f"  {k}: lengths {len(u)} / {len(v)}, first difference at {i}: "
                      f"{u[i] if i < len(u) else '-'} / {v[i] if i < len(v) else '-'}")
            else:
                print(f"  {k}: {u} / {v}")
    print("SAME" if same else "DIFFERENT")
    return 0 if same else 1


def run_closing(make, body):
    """backend = make(), body(backend), then backend.close() whatever happens. The
    handlers are in place before make() starts: SIGTERM and SIGHUP (a dropped ssh
    session) raise SessionSignal there too, and a backend that fails or is stopped
    while it is being built gives back what it had touched itself
    (fabric_eth.board_rig). Once the close has begun no signal, SIGINT included, can
    cut it short. Returns body's status, or 128 + the signal; a refusal by the guard
    returns 2."""
    live = [True]

    def stop(signum, frame):
        if live[0]:
            live[0] = False
            raise SessionSignal(signum)

    signals = (signal.SIGTERM, signal.SIGHUP)
    old = [signal.signal(n, stop) for n in signals]
    old_int = signal.getsignal(signal.SIGINT)
    backend = None
    try:
        backend = make()
        return body(backend)
    except SessionSignal as e:
        print(f"stopped by {e}; {'giving the board back' if backend else 'nothing was left on the board'}",
              file=sys.stderr)
        return 128 + e.signum
    except fabric_guard.NoActiveApp as e:
        print(f"refusing to run: {e}", file=sys.stderr)
        return 2
    finally:
        live[0] = False
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            if backend is not None:
                backend.close()
        finally:
            signal.signal(signal.SIGINT, old_int)
            for n, handler in zip(signals, old):
                signal.signal(n, handler)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", action="store_const", const="model", dest="backend", help="the cycle model")
    ap.add_argument("--backend", choices=sorted(BACKENDS), help="same, by name")
    ap.add_argument("--selftest", action="store_true", help="ping and UDP from this namespace, then exit")
    ap.add_argument("--clock", type=float, default=50, choices=(40, 50), help="project clock in MHz")
    ap.add_argument("--sck-div", type=int, default=2, help="model: SCK = clock / this")
    ap.add_argument("--single", action="store_true", help="model: 1-bit SPI instead of quad")
    ap.add_argument("--tap", default="wl0")
    ap.add_argument("--host-ip", default="10.77.0.1")
    ap.add_argument("--chip-ip", default="10.77.0.2")
    ap.add_argument("--other-ip", default="10.77.0.3", help="an address nothing answers")
    ap.add_argument("--count", type=int, default=5)
    ap.add_argument("--wait", type=int, default=60, help="seconds ping waits for each reply")
    ap.add_argument("--pcap", help="tcpdump the TAP to this file")
    ap.add_argument("--measure-spim", action="store_true",
                    help="on the Kria, as root, no network involved: how fast can this Python serve RX? "
                         "Borrows the host port and gives it back; core 0 should be idle")
    ap.add_argument("--probe", action="store_true",
                    help="fpga backends, no network: fixed ARP, ping, UDP and two unanswerable frames "
                         "through the chip, each reply read off TD, then exit")
    ap.add_argument("--save", help="--probe: write the results here, for --compare")
    ap.add_argument("--compare", nargs=2, metavar=("A", "B"), help="two --probe --save files, field by field")
    a = ap.parse_args(argv)
    if a.compare:
        return compare(*a.compare)
    if a.measure_spim:
        try:                                   # the app for this clock, or nothing is mapped
            fabric_guard.require_app(expect=fabric_guard.APP_FOR_MHZ[int(a.clock)])
        except fabric_guard.NoActiveApp as e:
            print(f"refusing to run: {e}", file=sys.stderr)
            return 2
        try:
            print(f"{measure_spim_session(pe_spim.Spim(div=1)):.1f} us per pass; docs/eth10-network.md has the budget")
        except SessionSignal as e:
            print(f"stopped by {e}; host port given back", file=sys.stderr)
            return 128 + e.signum
        except RuntimeError as e:
            print(f"{e}; pin select restored, but QUAD_EN may still be set", file=sys.stderr)
            return 1
        return 0
    if a.backend is None:
        ap.error("say which chip: --model")
    fabric = a.backend in FABRIC
    if a.probe and not fabric:
        ap.error("--probe is for --backend fpga or fpga-model")

    def make():
        return BACKENDS[a.backend](eth.ip(a.chip_ip), clock_hz=a.clock * 1e6, quad=not a.single, sck_div=a.sck_div)
    if a.probe:                                # no interface is created: no namespace needed
        def run_probe(backend):
            rep = backend.report()
            meas = f", {rep['f_meas_mhz']} MHz measured" if rep["f_meas_mhz"] else ""
            print(f"{rep['rig']} at {rep['clock_mhz']:g} MHz{meas}, RTL revision {rep['ident'].get('rtl_rev', '?')[:12]}, "
                  f"built {rep['ident'].get('build_utc', '?')}", flush=True)
            ok, results = probe(backend, a.chip_ip, a.host_ip)
            if a.save:
                pathlib.Path(a.save).write_text(json.dumps(dict(rig=rep["rig"], ident=rep["ident"],
                                                                f_meas_mhz=rep["f_meas_mhz"], clock_mhz=a.clock,
                                                                results=results), indent=1))
            print("probe PASS" if ok else "probe FAIL")
            return 0 if ok else 1
        return run_closing(make, run_probe)
    try:
        require_dedicated_netns()
    except NetnsError as e:
        print(f"refusing to run: {e}", file=sys.stderr)
        return 2

    def run_bridge(backend):                   # built before the TAP: a refusal leaves nothing behind
        tap = Tap(a.tap)
        tap.up(f"{a.host_ip}/24", mac=HOST_MAC_TEXT if fabric else None, ipv6=not fabric)
        sysctl(f"net/ipv4/neigh/{a.tap}/retrans_time_ms", 15000)     # this namespace only
        sysctl(f"net/ipv4/neigh/{a.tap}/base_reachable_time_ms", 600000)
        bridge = Bridge(tap, backend)
        if a.selftest:
            return selftest(a, bridge)
        print(f"{a.tap} is {a.host_ip}; the chip is {a.chip_ip}. Ctrl-C ends it.", flush=True)
        try:
            while True:
                bridge.step()
        except KeyboardInterrupt:
            print(json.dumps(backend.report(), indent=1))
        return 0
    return run_closing(make, run_bridge)


if __name__ == "__main__":
    sys.exit(main())
