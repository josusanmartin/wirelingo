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

A backend is anything with Backend's four methods. FpgaBackend is the place for
the board, and is a stub: docs/eth10-network.md lists what it needs.
"""
import argparse
import fcntl
import json
import os
import pathlib
import re
import select
import socket
import struct
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import pe_spim                                 # noqa: E402
from pe import eth, ethsim                     # noqa: E402

TUNSETIFF = 0x400454CA
IFF_TAP, IFF_NO_PI = 0x0002, 0x1000
SETTLE_US = 150                                # simulated time after the last activity
CHIP_MAC_TEXT, OTHER_MAC_TEXT = "02:57:4c:00:00:02", "02:57:4c:00:00:03"


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

    def up(self, cidr, mtu=1500):
        for cmd in (["ip", "link", "set", "lo", "up"],
                    ["ip", "link", "set", self.name, "mtu", str(mtu)],
                    ["ip", "addr", "add", cidr, "dev", self.name],
                    ["ip", "link", "set", self.name, "up"]):
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
    """The KV260: not written yet, and it needs the orchestrator's hardware."""

    def __init__(self, *a, **kw):
        raise SystemExit("the FPGA backend is a stub: see docs/eth10-network.md, 'The FPGA backend'")


BACKENDS = {"model": ModelBackend, "fpga": FpgaBackend}


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


def measure_spim_session(spim, passes=2000):
    """measure_spim with the host port borrowed and given back. It moves the host
    pins to the fabric master and sets QUAD_EN, as the measurement needs. The
    select goes back to what it was; QUAD_EN goes to 0, its reset value, because
    the register is write-only and can't be read first."""
    sel = spim.mem.rd(pe_spim.SEL)
    spim.select(fabric=True)
    try:
        spim.enable_quad()
        return measure_spim(spim, passes)
    finally:
        spim.enable_quad(False)
        spim.wait_idle()
        spim.mem.wr(pe_spim.SEL, sel)


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


def selftest(a, bridge):
    """Real ping and a real UDP socket. Exit status 0 only if every answer came
    and every frame that should go unanswered did."""
    pcap = None
    if a.pcap:
        pcap = subprocess.Popen(["tcpdump", "-Z", "root", "-U", "-i", a.tap, "-w", a.pcap],
                                stderr=subprocess.DEVNULL)
        time.sleep(0.5)
    res = {}
    n, rtt, text = ping(bridge, a.chip_ip, a.count, 56, a.wait)
    print(text)
    res["ping"] = dict(sent=a.count, received=n, wall_rtt_ms=rtt)
    n, rtt, text = ping(bridge, a.chip_ip, 2, 1472, a.wait)
    print(text)
    res["ping_1500_byte_packet"] = dict(sent=2, received=n, wall_rtt_ms=rtt)
    got = udp_echo(bridge, a.chip_ip, eth.UDP_ECHO_PORT, b"wirelingo", a.wait)
    res["udp_echo"] = got.decode() if got else None
    # Frames the chip must not answer, sent by Linux itself: neighbour entries
    # aim them at the chip's MAC with another IP, and at the chip's IP with another MAC.
    short = min(a.wait, 6)
    n, _, _ = ping(bridge, a.other_ip, 1, 56, short)
    res["no_reply_unknown_ip_no_arp"] = n == 0
    subprocess.run(["ip", "neigh", "replace", a.other_ip, "lladdr", CHIP_MAC_TEXT, "dev", a.tap], check=True)
    n, _, _ = ping(bridge, a.other_ip, 1, 56, short)
    res["no_reply_other_ip"] = n == 0
    subprocess.run(["ip", "neigh", "replace", a.chip_ip, "lladdr", OTHER_MAC_TEXT, "dev", a.tap], check=True)
    n, _, _ = ping(bridge, a.chip_ip, 1, 56, short)
    res["no_reply_other_mac"] = n == 0
    subprocess.run(["ip", "neigh", "replace", a.chip_ip, "lladdr", CHIP_MAC_TEXT, "dev", a.tap], check=True)
    if hasattr(bridge.backend, "corrupt"):
        bridge.backend.corrupt = 1
        n, _, _ = ping(bridge, a.chip_ip, 1, 56, short)
        res["no_reply_bad_fcs"] = n == 0
    n, _, _ = ping(bridge, a.chip_ip, 1, 56, a.wait)
    res["answers_again"] = n == 1
    if pcap:
        time.sleep(0.5)
        pcap.terminate()
        pcap.wait()
    res["frames_to_chip"], res["frames_from_chip"] = bridge.to_chip, bridge.from_chip
    res["backend"] = bridge.backend.report()
    ok = (res["ping"]["received"] == a.count and res["ping_1500_byte_packet"]["received"] == 2
          and res["udp_echo"] == "wirelingo" and res["answers_again"]
          and all(v for k, v in res.items() if k.startswith("no_reply")))
    res["pass"] = ok
    print(json.dumps(res, indent=1))
    return 0 if ok else 1


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
    a = ap.parse_args(argv)
    if a.measure_spim:
        print(f"{measure_spim_session(pe_spim.Spim(div=1)):.1f} us per pass; docs/eth10-network.md has the budget")
        return 0
    if a.backend is None:
        ap.error("say which chip: --model")
    try:
        require_dedicated_netns()
    except NetnsError as e:
        print(f"refusing to run: {e}", file=sys.stderr)
        return 2
    tap = Tap(a.tap)
    tap.up(f"{a.host_ip}/24")
    sysctl(f"net/ipv4/neigh/{a.tap}/retrans_time_ms", 15000)     # this namespace only
    sysctl(f"net/ipv4/neigh/{a.tap}/base_reachable_time_ms", 600000)
    backend = BACKENDS[a.backend](eth.ip(a.chip_ip), clock_hz=a.clock * 1e6, quad=not a.single, sck_div=a.sck_div)
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


if __name__ == "__main__":
    sys.exit(main())
