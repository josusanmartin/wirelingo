#!/usr/bin/env python3
"""v2 feature demos on the KV260 (run as root on the Kria, protoemu app with
pe_lab loaded), all through protoemu's pins as the fabric sees them:

    sudo python3 fpga/kv260/host/v2_demo.py [la] [spi] [i2c] [call] [--save la.json]
    python3 fpga/kv260/host/v2_demo.py la-model la.json      # off the board: the cycle model

la    WAIT kind 7 / la_capture: pe_lab's pattern generator drives the uio pads
      (P0-P7) with edges at exact clock times; core 0 runs la_capture on them
      and the host drains the records over the fabric SPI master. Synchronous
      run (generator on protoemu's clock): every edge's timestamp minus its
      drive time must be one constant (1-tick resolution); close edges show
      the dead time; square waves the edge-rate bound. Asynchronous run
      (generator on the 45.93 MHz alternate clock): timestamps against the
      ideal times. --save keeps schedules and records; `la-model` replays the
      synchronous schedule on the cycle model and compares record by record.
spi   SYNC_BYPASS: spi_master_fast (4 clocks per SCK) on core 1, SCK = uio2,
      MOSI = uio3, MISO = uio4, against pe_lab's SPI device, sweeping its
      SCK-fall-to-MISO delay in 2.5 ns steps, with and without the bypass.
i2c   CALL/RET in a real program: core 1 runs i2c_device (3 subroutines) at
      0x50, core 0 runs i2c_master; pe_lab joins their pins into one bus
      (uio0/uio2 = SDA, uio1/uio3 = SCL); the host is the master's
      application and the device's register model (pe.i2cdev).
call  The directed CALL/RET program (link register, conditional call,
      nesting through a register) on core 0: registers against the model.
"""
import argparse
import json
import pathlib
import random
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
ROOT = HERE.parents[2]
for sub in ("tools", "tools/micropython"):
    sys.path.insert(0, str(ROOT / sub))

from pe import asm, host, i2cdev, la, sim  # noqa: E402

FW = ROOT / "firmware"
ALT_RATIO = 26.125 / 24          # protoemu ticks per alternate-clock cycle (same 1200 MHz VCO)
FIFO = lambda c: sim.core_reg(c, sim.C_FIFO)
STATUS = lambda c: sim.core_reg(c, sim.C_STATUS)


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)


def fw(name, **defines):
    return asm.assemble((FW / f"{name}.pasm").read_text(), defines)


class Hw:
    """The board: GPIO path for reset, pe_spim for the host port, pe_lab.
    The simulation (fpga/kv260/sim) passes its own objects, f_clk and a
    wall-clock timeout it can't hit."""

    def __init__(self, board=None, spim=None, lab=None, f_clk=None, timeout=2.0):
        from pe_kria import Board
        from pe_spim import Spim
        from pe_lab import Lab
        self.timeout = timeout
        self.board = board or Board()
        self.lab = lab or Lab()
        self.lab.mode(Lab.GPIO)
        if board is None:
            self.board.reset()
        self.f_clk = f_clk or self.board.clk_hz(0.2)
        self.spim = spim or Spim()
        self.spim.div, self.spim.quad = 1, False
        self.spim.select(fabric=True)                 # host port at SCK = clk, 1-bit
        self.pe = self.board.protoemu()
        self.pe.transport = self.spim
        check(self.pe.read(sim.A_ID) == sim.ID_VALUE, "ID over the fabric master")

    def load(self, core, prog, **cfg):
        for addr, words in host.coalesce(host.load_program(core, prog, core * 256, **cfg)):
            self.pe.write(addr, words)

    def ctrl(self, v):
        self.pe.write(sim.A_CTRL, v)


# ---------------------------------------------------------------- la_capture
MASK = 0x0F                      # channels 0-3 watched; 4-7 toggle as noise


def la_sync_schedule(seed=3, n_ts=160, close=range(1, 11), reps=10, bursts=5, pause=30000):
    """(t, value, section) in protoemu clocks. Pauses of 30000 ticks between
    bursts let the host drain (RXF holds 8 records) and stay under the
    65536-tick idle record."""
    rng, t, v, out = random.Random(seed), 2000, 0, []

    def put(dt, flip, sec):
        nonlocal t, v
        t += dt
        v ^= flip
        out.append((t, v, sec))
    for i in range(n_ts):                                  # random edges >= 7 ticks apart
        put((pause if i % 6 == 0 else 0) + rng.randint(7, 400),
            rng.randint(1, 15) | (rng.getrandbits(4) << 4), "ts")
    for d in close:                                        # ch0, then ch1 d ticks later
        for rep in range(reps):
            put(pause if rep % 3 == 0 else 40, 0x01, f"close{d}")
            put(d, 0x02, f"close{d}")
    for spacing, n in ((7, 7), (6, 8)):                    # square waves on ch0 (<= 8 records a burst)
        for burst in range(bursts):
            for k in range(n):
                put(pause if k == 0 else spacing, 0x01, f"rate{spacing}")
    return out


def la_async_schedule(seed=4):
    """Alternate-clock cycles: channel 0 only (no channel-to-channel routing
    skew), edges >= 9 cycles (9.8 ticks) apart."""
    rng, t, v, out = random.Random(seed), 2000, 0, []
    for i in range(240):
        t += (28000 if i % 6 == 0 else 0) + rng.randint(9, 300)
        v ^= 1
        out.append((t, v, "async"))
    return out


def la_run_hw(hw, sched, alt):
    pe, lab = hw.pe, hw.lab
    from pe_lab import Lab
    hw.ctrl(1 << 16)                                       # stop, clear core 0's FIFOs
    lab.mode(Lab.PG)
    lab.pg_set(0, alt=alt)
    hw.load(0, fw("la_capture", MASK=MASK), in_pins=0, clkdiv=1 << 8)
    lab.pg_load([(t, v) for t, v, _ in sched], init=0, alt=alt)
    hw.ctrl(1 | (1 << 8))                                  # start core 0
    time.sleep(0.001)
    words = [w for w in pe.read(FIFO(0), 8, fixed=True) if w]
    lab.pg_start()
    t_end = time.monotonic() + hw.timeout + sched[-1][0] / hw.f_clk * (ALT_RATIO if alt else 1)
    reads, empty = 0, 0
    while True:
        check(time.monotonic() < t_end, "pattern generator still busy")
        got = [w for w in pe.read(FIFO(0), 8, fixed=True) if w]
        reads += 1
        words += got
        if not lab.pg_busy():
            empty = empty + 1 if not got else 0
            if empty >= 3:
                break
    hw.ctrl(0)
    lab.mode(Lab.GPIO)
    return words, {"reads": reads, "pg_cycles": lab.pg_cycles()}


def la_model_records(sched, start=1000):
    """The synchronous schedule on the cycle model: records taken straight
    from RXF every cycle (a host that never falls behind)."""
    from pe.modelhost import ModelHost
    mh = ModelHost()
    mh.apply(host.load_program(0, fw("la_capture", MASK=MASK), in_pins=0, clkdiv=1 << 8))
    mh.ext_oe, mh.uio = 0xFF, 0
    mh.start(1)
    mh.step(start)
    t0, words = mh.chip.cycle, []
    core = mh.chip.cores[0]
    for t, v, _ in sched + [(sched[-1][0] + 200, sched[-1][1], "end")]:
        while mh.chip.cycle - t0 < t:
            mh.step()
            if core.rxf:
                words += core.rxf
                core.rxf.clear()
        mh.uio = v
    return words


def la_analyse(sched, words, alt=False, log=print):
    """Timestamps vs the driven edges. Returns a summary dict."""
    cap = la.decode_records(words, MASK)
    res = {"records": len(words), "exact": cap.exact, "gaps": len(cap.gaps), "idles": len(cap.idles),
           "pulses": len(cap.pulses)}
    driven, state = [], 0
    for t, v, sec in sched:
        if (v ^ state) & MASK:
            driven.append((t * (ALT_RATIO if alt else 1), v & MASK, sec))
        state = v
    got = cap.edges[1:]
    if alt:
        check(len(got) == len(driven) and [s for _, s in got] == [s for _, s, _ in driven],
              f"async: {len(got)} edges captured for {len(driven)} driven")
        err = [ct - et for (ct, _), (et, _, _) in zip(got, driven)]
        base = min(err)
        hist = {}
        for e in err:
            k = round(e - base, 1)
            hist[k] = hist.get(k, 0) + 1
        res.update(edges=len(got), spread=max(err) - min(err), hist=dict(sorted(hist.items())))
        log(f"  async: {len(got)} edges on channel 0, >= 9 alt cycles apart, all captured; "
            f"capture tick - ideal time spans {res['spread']:.3f} ticks")
        return res
    # synchronous: section by section
    ts = [(e, g) for e, g in zip(driven, got) if e[2] == "ts"]
    check(len(ts) == sum(1 for e in driven if e[2] == "ts"), "edges lost in the timestamp section")
    check(all(e[1] == g[1] for e, g in ts), "timestamp section: states differ")
    lags = sorted({g[0] - e[0] for e, g in ts})
    lag = lags[0]
    res.update(ts_edges=len(ts), ts_lags=lags)
    log(f"  timestamps: {len(ts)} edges on 4 channels, 7-430 ticks apart (unwatched channels toggling): "
        f"capture tick - drive tick = {lags} for every edge")
    # the rest: walk the captured edges after the ts section
    rest = got[len(ts):]
    lateness = {}
    gi = 0
    for d in sorted({int(e[2][5:]) for e in driven if e[2].startswith("close")}):
        sec = [e for e in driven if e[2] == f"close{d}"]
        lat = []
        for k in range(0, len(sec), 2):
            first, second = sec[k], sec[k + 1]
            # the record that first shows the second edge's state (both channels flipped)
            while gi < len(rest) and rest[gi][1] != second[1]:
                gi += 1
            check(gi < len(rest), f"close{d}: second edge lost")
            lat.append(rest[gi][0] - second[0] - lag)
            gi += 1
        lateness[d] = sorted(set(lat))
    res["close_lateness"] = lateness
    log("  close edges (ch1 d ticks after ch0; lateness of ch1's timestamp, ticks): " +
        ", ".join(f"d={d}: {v}" for d, v in lateness.items()))
    rate = {}
    for spacing in sorted({int(e[2][4:]) for e in driven if e[2].startswith("rate")}, reverse=True):
        n_drv = sum(1 for e in driven if e[2] == f"rate{spacing}")
        lo = min(e[0] for e in driven if e[2] == f"rate{spacing}") + lag - 1
        hi = max(e[0] for e in driven if e[2] == f"rate{spacing}") + lag + 8
        n_got = sum(1 for t, _ in got if lo <= t <= hi)
        rate[spacing] = (n_drv, n_got)
    res["rate"] = rate
    log("  square waves on ch0: " + ", ".join(f"edge every {s} ticks: {g} of {n} edges captured"
                                             for s, (n, g) in rate.items()))
    return res


def demo_la(hw, save=None, sched=None, run_async=True):
    out = {"f_clk": hw.f_clk}
    sched = sched or la_sync_schedule()
    words, info = la_run_hw(hw, sched, alt=False)
    print(f"  synchronous run: {len(sched)} entries, generator ran {info['pg_cycles']} clk; "
          f"{len(words)} records drained in {info['reads']} READ_FIXED bursts")
    res = la_analyse(sched, words)
    out["sync"] = {"sched": sched, "words": words, "result": res}
    check(res["exact"] and not res["gaps"], "synchronous capture has gaps (host fell behind)")
    check(len(res["ts_lags"]) == 1, "timestamps vary: not 1-tick resolution")
    check(all(v == ([-1] if d == 1 else [7 - d] if d < 7 else [0]) for d, v in res["close_lateness"].items()),
          "close-edge lateness differs from la_capture's rule (d=1: -1, 2-6: 7-d, >= 7: 0)")
    check(all(n == g for n, g in [res["rate"].get(7, (0, 0))]) and
          all(g < n for n, g in [res["rate"].get(6, (1, 0))]), "edge-rate bound differs from 7 ticks")
    if not run_async:
        return out
    sched_a = la_async_schedule()
    words_a, info_a = la_run_hw(hw, sched_a, alt=True)
    res_a = la_analyse(sched_a, words_a, alt=True)
    out["async"] = {"sched": sched_a, "words": words_a, "result": res_a}
    print(f"  async histogram (capture - ideal, ticks above the minimum: count): {res_a['hist']}")
    check(res_a["exact"] and res_a["spread"] < 1.05, "async timestamps spread over more than a tick")
    if save:
        pathlib.Path(save).write_text(json.dumps(out))
        print(f"  saved {save}")


def demo_la_model(path):
    d = json.loads(pathlib.Path(path).read_text())
    sched = [tuple(e) for e in d["sync"]["sched"]]
    hw_words = d["sync"]["words"]
    t = time.monotonic()
    mw = la_model_records(sched)
    print(f"  model: {len(mw)} records ({time.monotonic() - t:.0f} s); hardware: {len(hw_words)}")
    res = la_analyse(sched, mw)

    def after_first_change(words):
        i = next(k for k, w in enumerate(words) if w >> 24 == 0xFF and (w >> 16) & MASK)
        return words[i + 1:]
    a, b = after_first_change(hw_words), after_first_change(mw)
    same = a == b
    print(f"  records after the first change: hardware {len(a)}, model {len(b)}, identical: {same}")
    if not same:
        k = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
        print(f"  first difference at record {k}: hw {a[k:k + 3]} model {b[k:k + 3]}")
    # (the lag's value includes the arbitrary start offset; each must be one constant)
    check(same and len(res["ts_lags"]) == len(d["sync"]["result"]["ts_lags"]) == 1, "hardware differs from the model")
    print("  the hardware's la_capture records equal the cycle model's, word for word")


# ---------------------------------------------------------------- SPI / SYNC_BYPASS
SCK, MOSI, MISO, SPI_CORE = 2, 3, 4, 1


def bits_late(resp, got):
    """How many bit times late the master read the device's stream (0 = right), or None."""
    stream = [(b >> (7 - i)) & 1 for b in resp for i in range(8)]
    gbits = [(b >> (7 - i)) & 1 for b in got for i in range(8)]
    for k in range(4):
        if gbits == [stream[0]] * k + stream[:len(stream) - k]:
            return k
    return None


def demo_spi(hw, delays=range(0, 20), rounds=8):
    pe, lab, c = hw.pe, hw.lab, SPI_CORE
    from pe_lab import Lab
    hw.ctrl(0)
    pe.set_pin_owners({SCK: c, MOSI: c, MISO: c})
    hw.load(c, asm.assemble(f"pin {SCK}, lo\npin {MOSI}, lo\nhalt"))
    hw.ctrl((1 << c) | (1 << (8 + c)))
    time.sleep(0.001)
    lab.mode(Lab.SPI)
    prog = fw("spi_master_fast")
    hw.ctrl(0)
    hw.load(c, prog, out_pins=(MOSI, 1), side_pins=SCK, in_pins=MISO, clkdiv=1 << 8)
    rng = random.Random(11)
    table = {}
    for d in delays:
        row = []
        for byp in (0, 1):
            lates, per = set(), set()
            for _ in range(rounds):
                tx = bytes(rng.getrandbits(8) for _ in range(8))
                resp = bytes(rng.getrandbits(8) for _ in range(8))
                hw.ctrl(1 << (16 + c))                      # stop; clear the FIFOs
                pe.write(sim.A_SYNCBYP, (1 << MISO) if byp else 0)
                lab.spid_arm(resp + bytes(8), d)
                pe.write(FIFO(c), [b << 24 for b in tx], fixed=True)
                hw.ctrl((1 << c) | (1 << (8 + c)))          # start: 8 bytes back to back
                for _ in range(100):
                    if (pe.read(STATUS(c)) >> 4) & 15 == 8:
                        break
                else:
                    raise AssertionError("SPI master did not finish")
                got = bytes(w & 0xFF for w in pe.read(FIFO(c), 8, fixed=True))
                rises, pmin, pmax = lab.spid_status()
                check(rises == 64 and lab.spid_rx()[8:] == tx, f"device got {lab.spid_rx()[8:].hex()} for {tx.hex()}")
                per |= {pmin, pmax}
                lates.add(bits_late(resp, got))
            row.append((sorted(lates, key=lambda x: 99 if x is None else x), sorted(per)))
        table[d] = row
    hw.ctrl(0)
    pe.write(sim.A_SYNCBYP, 0)
    lab.mode(Lab.GPIO)
    name = lambda ls: "/".join("ok" if k == 0 else "?" if k is None else f"{k} bit late" for k in ls)
    print(f"  spi_master_fast, CLKDIV 1, {rounds} x 8 random bytes per cell; MOSI always right "
          f"(device log); SCK rise-to-rise {sorted(set(p for r in table.values() for _, ps in r for p in ps))} dclk = "
          "4 clocks")
    print("  DELAY  MISO after the chip's SCK edge  no bypass      SYNC_BYPASS")
    for d, ((l0, _), (l1, _)) in table.items():
        print(f"  {d:5d}  {2.5 * (d + 2):6.1f} ns                       {name(l0):14s} {name(l1)}")
    ok0 = [d for d, r in table.items() if r[0][0] == [0]]
    ok1 = [d for d, r in table.items() if r[1][0] == [0]]
    print(f"  correct reads: no bypass up to DELAY {max(ok0) if ok0 else None} "
          f"({2.5 * (max(ok0) + 2) if ok0 else 0:.1f} ns), SYNC_BYPASS up to DELAY {max(ok1) if ok1 else None} "
          f"({2.5 * (max(ok1) + 2) if ok1 else 0:.1f} ns)")
    ds = list(table)
    check(ok0 and ok1 and ok0 == [d for d in ds if d <= max(ok0)] and ok1 == [d for d in ds if d <= max(ok1)]
          and max(ok1) > max(ok0),
          "SYNC_BYPASS does not extend the device's window")
    return table


# ---------------------------------------------------------------- I2C / CALL-RET
START, STOP = 2, 3
WR = lambda b: (b << 24) | 0
RD = lambda ack: 1 | (0 if ack else 4)


def demo_i2c(hw, khz=100):
    pe, lab = hw.pe, hw.lab
    from pe_lab import Lab
    rng = random.Random(5)
    rom = bytes(rng.getrandbits(8) for _ in range(256))
    hw.ctrl(0b11 << 16)
    pe.set_pin_owners({0: 0, 1: 0, 2: 1, 3: 1})
    pe.write(sim.A_POUT, 0x0F)                              # released
    pe.write(sim.A_OD, 0x0F)
    pe.write(sim.A_FCLR, 0xFF)
    lab.mode(Lab.I2C)
    master = fw("i2c_master", SDA=0, SCL=1)
    hw.load(0, master, out_pins=(0, 1), in_pins=0, clkdiv=host.clkdiv_for(hw.f_clk, 8 * khz * 1000))
    dprog = fw("i2c_device", SDA=2, SCL=3)
    ncall = sum(1 for w in dprog.words if w >> 27 == 13)
    dev = i2cdev.Device(0x50, mem={r: rom[r] for r in range(256)})
    for addr, words in host.coalesce(dev.setup_writes(1, dprog, 2, 3, base=256)):
        pe.write(addr, words)
    hw.ctrl(0b11 | (0b11 << 8))
    txns = [
        ("write 0x10, read 8", [START, WR(0xA0), WR(0x10), START, WR(0xA1)] + [RD(True)] * 7 + [RD(False), STOP]),
        ("write 0x20: CA FE", [START, WR(0xA0), WR(0x20), WR(0xCA), WR(0xFE), STOP]),
        ("write 0x20, read 2", [START, WR(0xA0), WR(0x20), START, WR(0xA1), RD(True), RD(False), STOP]),
        ("address 0x51", [START, WR(0xA2), STOP]),
    ]
    results = []
    t0 = time.monotonic()
    served = 0
    for label, cmds in txns:
        nrep = sum(1 for w in cmds if w & 3 in (0, 1))
        replies, i, t = [], 0, time.monotonic()
        while len(replies) < nrep or i < len(cmds):
            if i < len(cmds):
                room = 8 - (pe.read(STATUS(0)) & 15)
                if room > 0:
                    pe.write(FIFO(0), cmds[i:i + room], fixed=True)
                    i += min(room, len(cmds) - i)
            for ev in pe.pop_all(1):
                for b in dev.handle(ev):
                    pe.write(FIFO(1), b, fixed=True)
                    served += 1
            replies += pe.pop_all(0)
            check(time.monotonic() - t < hw.timeout, f"{label}: no progress ({replies})")
        results.append((label, replies))
    time.sleep(0.01)
    for ev in pe.pop_all(1):
        dev.handle(ev)
    dt = time.monotonic() - t0
    fset = pe.read(sim.A_FSET)
    st = [pe.read(STATUS(c)) for c in (0, 1)]
    hw.ctrl(0)
    pe.write(sim.A_OD, 0)
    pe.write(sim.A_POUT, 0)
    lab.mode(Lab.GPIO)
    print(f"  i2c_device: {len(dprog.words)} words, {ncall} CALLs (opcode 13; the bit loop, the event push "
          f"with its stretch, the START/STOP check are subroutines); i2c_master at {khz} kHz")
    for label, rep in results:
        print(f"  {label:20s} master replies {[hex(r) for r in rep]}")
    print(f"  device log (pe.i2cdev): {dev.log}; {served} read bytes served by the host; {dt * 1000:.0f} ms")
    acks = lambda rep: [r & 1 for r in rep]
    r1, r2, r3, r4 = (r for _, r in results)
    check(acks(r1[:3]) == [0, 0, 0] and [r & 0xFF for r in r1[3:]] == list(rom[0x10:0x18]), "read 8 bytes")
    check(acks(r2) == [0] * 4 and dev.mem[0x20:0x22] == b"\xca\xfe", "write CA FE")
    check(acks(r3[:3]) == [0, 0, 0] and [r & 0xFF for r in r3[3:]] == [0xCA, 0xFE], "read back CA FE")
    check([r & 0x101 for r in r4] == [1], "0x51 must be NACKed")
    check(not any(r & 0x100 for _, rep in results for r in rep) and not fset & 2, "clock-stretch timeout (F1)")
    check(not any(s & ((1 << 13) | (1 << 14)) for s in st), "a core halted or faulted")
    print("  same answers as an EEPROM: CALL/RET firmware works on the bitstream")


CALL_SRC = """
    call r0, sub        ; r0 = 0: not taken, no link
    call outer          ; LR = 2
    mov r2, lr          ; LR as restored by outer: 2
    halt
outer:
    mov r3, lr          ; save
    call inner          ; LR = 6
    mov lr, r3          ; restore
    add r0, 1
    ret
inner:
    add r0, 100
    mov r1, lr          ; 6
    ret
sub:
    ldi r0, 0x7777      ; must not run
    ret
"""


def demo_call(hw):
    from pe.modelhost import ModelHost
    prog = asm.assemble(CALL_SRC)
    mh = ModelHost()
    mh.apply(host.load_program(0, prog))
    mh.start(1)
    mh.step(100)
    want = [mh.chip.cores[0].r[i] for i in range(4)]
    pe = hw.pe
    hw.ctrl(1 << 16)
    for i in range(4):
        pe.write(sim.core_reg(0, sim.C_R0 + i), 0)
    hw.load(0, prog)
    hw.ctrl(1 | (1 << 8))
    time.sleep(0.001)
    st = pe.read(STATUS(0))
    got = [pe.read(sim.core_reg(0, sim.C_R0 + i)) for i in range(4)]
    hw.ctrl(0)
    print(f"  directed CALL/RET program: hardware R0..R3 = {[hex(v) for v in got]}, model {[hex(v) for v in want]}, "
          f"halted {bool(st & (1 << 13))}")
    check(got == want and want == [101, 6, 2, 2] and st & (1 << 13), "CALL/RET registers")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("demos", nargs="*", default=["call", "la", "spi", "i2c"])
    ap.add_argument("--save", help="la: keep schedules and records (JSON)")
    a = ap.parse_args()
    if a.demos[:1] == ["la-model"]:
        print("[la-model]")
        demo_la_model(a.demos[1])
        return
    hw = Hw()
    print(f"protoemu clk {hw.f_clk / 1e6:.4f} MHz; host port via pe_spim at SCK = clk")
    failed = 0
    for name in a.demos:
        print(f"[{name}]")
        t = time.monotonic()
        try:
            {"la": lambda: demo_la(hw, a.save), "spi": lambda: demo_spi(hw), "i2c": lambda: demo_i2c(hw),
             "call": lambda: demo_call(hw)}[name]()
            print(f"  PASS ({time.monotonic() - t:.1f} s)")
        except Exception as e:  # report and continue
            failed += 1
            print(f"  FAIL: {type(e).__name__}: {e}")
    print("ALL PASS" if not failed else f"{failed} demo(s) FAILED")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
