#!/usr/bin/env python3
"""Linux's full-speed enumeration, as usbmon recorded it on the KV260 in a
stalled run (2026-11-03), against pe.usbfsdata.LoopbackDevice running over a
modelled SPI port concurrently with the bus (test_usb_fs_data.SpiBridge),
with a strict host. Not a pytest test: a sweep for fpga/kv260/README.md's
"What the model says" (evidence: model).

    PYTHONPATH=tools python test/model/usb_fs_enum_sweep.py [--tree DIR] [--runs 40] [--procs 6]

--tree: a checkout (or `git archive` copy) whose tools/, firmware/ and
test/model/test_usb_fs_data.py are used, default this one; the software
under test is identified by the sha256 of its usbdev.py, usbfsdata.py and
usb_fs_dev.pasm in the output.

The host's sequence (usbmon, run 18): GET_DESCRIPTOR(device, 64) at address
0, ended by the first 8-byte packet (the xHCI assumes a 64-byte MPS);
OUT status; port reset (10 ms SE0); SET_ADDRESS(31); GET_DESCRIPTOR(device,
18); GET_DESCRIPTOR(config, 9); GET_DESCRIPTOR(config, 18); then
SET_CONFIGURATION(1), whose status stage must be one zero-length DATA1.
Gaps (us, uniform, per run from random.Random(run)): 20-200 before the reset,
100-3000 after it, 2000-10000 after SET_ADDRESS, then 20-200, 10-100 and
100-1000 (usbmon: 95, 27 and 569). SOF every 1 ms while the host waits.

The software (--combos, OVERHEAD:POLL:EVERY:PAUSE, clocks at 48 MHz): each
SPI transaction costs OVERHEAD clocks of host software on top of the quad
SPI timing (sck = clk/2); between service() calls it idles POLL (+0-199)
clocks, and every EVERY calls PAUSE clocks instead (the demo's sysfs check
every 256 polls). POLL must not equal an internal SPI wait (the defaults
don't): the bridge tells its poll from those by value.
"""
import argparse
import hashlib
import multiprocessing as mp
import pathlib
import random
import sys
import time

HERE = pathlib.Path(__file__).resolve()
COMBOS = "2400:0:256:96000,4800:0:16:48000,960:0:8:24000,2400:2400:4:9600,9600:0:64:192000"


def load(tree):
    sys.path[:0] = [str(tree / "tools"), str(tree / "test" / "model")]
    import test_usb_fs_data as t
    from pe import asm, host, sim, usb, usbdev
    from pe.modelhost import ModelHost
    from pe.usbfs import FsHost
    from pe.usbfsdata import LoopbackDevice

    class Bridge(t.SpiBridge):
        """SpiBridge with a jittered poll and a long pause every `every` calls."""

        def __init__(self, h, sw, every, pause, rng, **kw):
            self.every, self.pause, self.rng, self.n = every, pause, rng, 0
            super().__init__(h, sw, **kw)

        def _program(self, bus):
            inner = super()._program(bus)
            v = None
            while True:
                item = inner.send(v)
                v = None
                if isinstance(item, int) and item == self.poll:
                    self.n += 1
                    if self.every and self.n % self.every == 0:
                        item = self.pause
                    else:
                        item = max(1, self.poll + self.rng.randrange(0, 200))
                v = yield item

    class Host:
        """FsHost waits with a SOF every 1 ms, and a port reset."""

        def __init__(self, h):
            self.h, self.frame, self.next_sof = h, 0, h.now() + 1e6

        def wait_us(self, us):
            h = self.h
            end = h.now() + us * 1e3
            while h.now() < end:
                if h.now() >= self.next_sof:
                    h.sof(self.frame)
                    self.frame += 1
                    self.next_sof += 1e6
                h.wait_bits(min(64, max(1, (end - h.now()) / h.tbit)))

        def reset(self, ms=10):
            h = self.h
            for _ in range(int(ms * 48000)):
                h._edge(0b00)
            h.last_eop_j = None
            self.next_sof = h.now() + 1e6
            self.wait_us(100)

    def fresh(overhead, poll, every, pause, seed):
        mh = ModelHost()
        prog = asm.assemble((t.FW / "usb_fs_dev.pasm").read_text(), usbdev.defines())
        mh.apply(host.load_program(0, prog, 0, clkdiv=1 << 8))
        mh.write(sim.core_reg(0, sim.C_CRCCTRL), usbdev.CRCCTRL)
        mh.write(sim.A_LINEDIV, (4 << 16) | (4 << 8))
        mh.write(sim.A_LINEPINS, 0)
        mh.write(sim.A_LINECFG, 0x49F3)
        mh.pull_up = 0b10
        mh.start(1)
        mh.step(50)
        sw = LoopbackDevice(mh, prog, usbdev.DESCRIPTORS, 0, 0)
        h = FsHost(mh, seed=seed, jitter_ns=5)
        b = Bridge(h, sw, every, pause, random.Random(seed), poll=poll, sck=2, overhead=overhead)
        return h, sw, b

    def ctl_in(h, addr, req, what, mps=8):
        n = req[6] | req[7] << 8
        h.setup(addr, req)
        out, toggle = b"", 1
        while len(out) < n:
            k = len(h.log)
            pid, payload = h.data_in(addr, 0)
            seen = [e for e in h.log[k:] if e[:2] != ("IN", "nak")]
            want = usb.pid_byte(usb.PID_DATA1 if toggle else usb.PID_DATA0)
            assert len(seen) == 1 and pid == want, f"{what}: data stage saw {seen}"
            toggle ^= 1
            out += payload
            if len(payload) < mps:
                break
        h.data_out(addr, 0, usb.PID_DATA1, b"")
        return out

    def linux(H, rng):
        h = H.h
        H.wait_us(100)
        assert ctl_in(h, 0, bytes([0x80, 6, 0, 1, 0, 0, 64, 0]), "GET_DESCRIPTOR(device, 64)", mps=64) \
            == usbdev.DEVICE_DESC[:8]
        H.wait_us(rng.uniform(20, 200))
        H.reset()
        H.wait_us(rng.uniform(100, 3000))
        h.setup(0, bytes([0, 5, 31, 0, 0, 0, 0, 0]))
        t.status_in(h, 0, "SET_ADDRESS")
        H.wait_us(rng.uniform(2000, 10000))
        assert ctl_in(h, 31, bytes([0x80, 6, 0, 1, 0, 0, 18, 0]), "GET_DESCRIPTOR(device, 18)") == usbdev.DEVICE_DESC
        H.wait_us(rng.uniform(20, 200))
        assert ctl_in(h, 31, bytes([0x80, 6, 0, 2, 0, 0, 9, 0]), "GET_DESCRIPTOR(config, 9)") == usbdev.CONFIG_DESC[:9]
        H.wait_us(rng.uniform(10, 100))
        assert ctl_in(h, 31, bytes([0x80, 6, 0, 2, 0, 0, 18, 0]), "GET_DESCRIPTOR(config, 18)") == usbdev.CONFIG_DESC
        H.wait_us(rng.uniform(100, 1000))
        h.setup(31, bytes([0, 9, 1, 0, 0, 0, 0, 0]))
        t.status_in(h, 31, "SET_CONFIGURATION")

    return fresh, Host, linux


TREE = None


def one(job):
    fresh, Host, linux = load(TREE)
    (overhead, poll, every, pause), k = job
    h, sw, b = fresh(overhead, poll, every, pause, seed=k + 1)
    try:
        linux(Host(h), random.Random(k))
        out = None
    except Exception as ex:
        out = f"{type(ex).__name__}: {str(ex)[:200]}"
    finally:
        b.close()
    return job, out, sw.requests[-1:], sw.restaged


def init(tree):
    global TREE
    TREE = tree


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--tree", type=pathlib.Path, default=HERE.parents[2])
    ap.add_argument("--runs", type=int, default=40, help="enumerations per combination (run k: gaps from Random(k))")
    ap.add_argument("--procs", type=int, default=6)
    ap.add_argument("--combos", default=COMBOS, help="OVERHEAD:POLL:EVERY:PAUSE,... in 48 MHz clocks")
    a = ap.parse_args()
    tree = a.tree.resolve()
    combos = [tuple(int(x) for x in c.split(":")) for c in a.combos.split(",")]
    for f in ("tools/pe/usbdev.py", "tools/pe/usbfsdata.py", "firmware/usb_fs_dev.pasm"):
        print(f"{hashlib.sha256((tree / f).read_bytes()).hexdigest()}  {f}", flush=True)
    print(f"runs {a.runs} per combination, procs {a.procs}", flush=True)
    jobs = [(c, k) for c in combos for k in range(a.runs)]
    t0 = time.time()
    res = {}
    with mp.Pool(a.procs, initializer=init, initargs=(tree,)) as p:
        for job, out, last, restaged in p.imap_unordered(one, jobs):
            res.setdefault(job[0], []).append((job[1], out, last, restaged))
    bad_total = 0
    for c in combos:
        rs = sorted(res.get(c, []))
        bad = [r for r in rs if r[1]]
        bad_total += len(bad)
        print(f"overhead {c[0]} poll {c[1]} every {c[2]} pause {c[3]}: {len(bad)} of {len(rs)} bad, "
              f"re-staged in {sum(1 for r in rs if r[3])}", flush=True)
        for r in bad:
            print("   ", r, flush=True)
    print(f"{time.time() - t0:.0f} s")
    return 1 if bad_total else 0


if __name__ == "__main__":
    sys.exit(main())
