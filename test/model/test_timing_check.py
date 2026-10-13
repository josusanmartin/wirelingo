"""Static timing checker (tools/pe/timing.py, docs/timing-check.md).

Every annotated deadline must PASS, and every interval measured on the
cycle model between the same points (while the deadline's assumptions held)
must lie inside the checker's [min, max]: a measurement outside it is a
checker bug. Measurements that broke an assumption are counted apart, and
some tests break assumptions on purpose to show they are detected. Random
programs exercise the per-instruction cost model beyond the firmware.

Run with -s to see the reports and the measured ranges."""
import pathlib
import random
import re

import pytest

from pe import asm, host, sim, timing, usb, usbdev
from pe.modelhost import ModelHost
from pe.usbfs import FsHost

FW = pathlib.Path(__file__).resolve().parents[2] / "firmware"
DEFINES = {"uart_tx.pasm": {}, "i2c_master.pasm": {"SDA": 0, "SCL": 1},
           "usb_fs_dev.pasm": usbdev.defines()}


def check(name, text=None, **kw):
    text = text if text is not None else (FW / name).read_text()
    return timing.check_source(text, DEFINES[name], **kw)


def strip_annotations(text):
    """The source with every `@keyword ...` comment item removed."""
    out = []
    for line in text.splitlines():
        m = re.search(r"(;|//)(.*)$", line)
        if m:
            body = m.group(2)
            for kw, rest in timing._ann_items(body.strip()):
                body = re.sub(r"@%s\b\s*%s" % (kw, re.escape(rest)), "", body, count=1)
            line = line[:m.start(2)] + body
        out.append(line)
    return "\n".join(out)


def soundness(chk, results, tracer, *, expect=None, label=""):
    """Measure every deadline in a Tracer record; assert the clean ones lie in
    the bounds. Returns {name: (n_clean, n_broken, (min, max) ticks)}."""
    summary = {}
    for r in results:
        ms = timing.measure(chk, r.deadline, tracer)
        clean = [m for m in ms if not m.violations]
        bad = [(m.ticks, m.clocks, m.start_cycle) for m in clean if not r.contains(m.ticks, m.clocks)]
        assert not bad, (f"CHECKER BUG {label} {r.deadline.name}: bound [{r.lo}, {r.hi}] ticks "
                         f"[{r.lo_clk}, {r.hi_clk}] clk, measured (ticks, clk, cycle) {bad[:5]}")
        rng = (min(m.ticks for m in clean), max(m.ticks for m in clean)) if clean else None
        summary[r.deadline.name] = (len(clean), len(ms) - len(clean), rng)
    return summary


def show(summary, results):
    for r in results:
        n, nb, rng = summary[r.deadline.name]
        print(f"  {r.deadline.name:12s} bound [{r.lo:g}, {r.hi:g}] ticks; measured {n} clean"
              f"{f' in [{rng[0]}, {rng[1]}]' if rng else ''}, {nb} outside the assumptions")


# ---------------------------------------------------------------- annotations
@pytest.mark.parametrize("name", sorted(DEFINES))
def test_annotations_leave_the_words_unchanged(name):
    text = (FW / name).read_text()
    assert "@deadline" in text
    plain = strip_annotations(text)
    assert "@point" not in plain and "@deadline" not in plain
    a, b = asm.assemble(text, DEFINES[name]), asm.assemble(plain, DEFINES[name])
    assert a.words == b.words and (a.wrap_bottom, a.wrap_top, a.config) == (b.wrap_bottom, b.wrap_top, b.config)


@pytest.mark.parametrize("name", sorted(DEFINES))
def test_firmware_deadlines_pass(name):
    chk, results = check(name)
    print()
    for r in results:
        print(r.report())
    assert not chk.errors
    # A deadline may pass conditionally only where it says so (reach=conditional):
    # then the paths it excludes are listed in the report.
    assert results and all(r.status == ("PASS (conditional)" if r.deadline.reach == "conditional" else "PASS")
                           for r in results), [(r.deadline.name, r.status) for r in results]


# ---------------------------------------------------------------- UART TX
def uart_run(div, nbytes, seed):
    """uart_tx on core 0 at CLKDIV div, bytes pushed at random moments (the
    FIFO sometimes runs dry: idle stop bits of any length)."""
    mh = ModelHost()
    tx = asm.assemble((FW / "uart_tx.pasm").read_text())
    mh.apply(host.load_program(0, tx, out_pins=(8, 1), side_pins=8, clkdiv=div))
    tr = timing.Tracer(mh.chip, 0, 0)
    mh.start(1)
    rng = random.Random(seed)
    for _ in range(nbytes):
        if rng.random() < 0.7:
            mh.push(0, rng.getrandbits(8))
        mh.step(rng.randrange(1, 400) * max(1, div >> 8) // 4)
    mh.step(12 * 9 * (div >> 8) * 8 // 8 + 2000)
    return tr


@pytest.mark.parametrize("div", [host.clkdiv_for(48e6, 8 * 115200), 1 << 8, (3 << 8) | 128])
def test_uart_tx_sound(div):
    chk, results = check("uart_tx.pasm", clkdiv=(div >> 8, div & 0xFF))
    tr = uart_run(div, 40, div)
    s = soundness(chk, results, tr, label=f"div={div:#x}")
    print(f"\nuart_tx CLKDIV {div >> 8}+{div & 255}/256:")
    show(s, results)
    assert all(s[r.deadline.name][0] > 0 for r in results)
    assert all(s[r.deadline.name][1] == 0 for r in results)


def test_uart_tx_default_clkdiv_matches_the_test_bench():
    chk, _ = check("uart_tx.pasm")
    div = host.clkdiv_for(48e6, 8 * 115200)
    assert chk.clkdiv == (div >> 8, div & 0xFF)


# ---------------------------------------------------------------- I2C master
SDA, SCL = 0, 1


def i2c_run(div, seed, stretch_p=0.3, ncmd=6):
    """i2c_master with a peer that stretches SCL (holds it low a random 1-40
    clocks after the master releases it) with probability stretch_p."""
    mh = ModelHost()
    prog = asm.assemble((FW / "i2c_master.pasm").read_text(), {"SDA": SDA, "SCL": SCL})
    mh.apply(host.pin_owner_writes({SDA: 0, SCL: 0}))
    mh.write(sim.A_POUT, 0b11)
    mh.write(sim.A_OD, 0b11)
    mh.pull_up = 0b11
    mh.apply(host.load_program(0, prog, out_pins=(SDA, 1), in_pins=SDA, clkdiv=div << 8))
    tr = timing.Tracer(mh.chip, 0, 0)
    mh.start(1)
    rng = random.Random(seed)
    cmds = [2]
    for _ in range(ncmd):
        cmds.append(rng.choice([rng.getrandbits(8) << 24, 1, 1 | 4, 2]))
    cmds.append(3)
    for c in cmds:
        mh.push(0, c)
    prev, hold = 1, 0
    for _ in range(40 * 30 * div * len(cmds)):
        drive = (mh.chip.pin_out >> SCL) & 1
        if drive and not prev and rng.random() < stretch_p:
            hold = rng.randrange(1, 40)
        prev = drive
        if hold:
            mh.ext_oe |= 1 << SCL
            mh.uio &= ~(1 << SCL)
            hold -= 1
        else:
            mh.ext_oe &= ~(1 << SCL)
        mh.step()
        mh.pop_all(0)
    return tr


@pytest.mark.parametrize("seed", range(3))
def test_i2c_master_sound(seed):
    div = 4
    chk, results = check("i2c_master.pasm", clkdiv=div)
    tr = i2c_run(div, seed)
    s = soundness(chk, results, tr, label=f"seed={seed}")
    print(f"\ni2c_master CLKDIV {div}, random stretching:")
    show(s, results)
    for name in ("scl_high", "scl_low", "scl_high_ns"):
        assert s[name][0] > 0
    assert s["scl_high"][1] == 0                  # bounded by the ISA timeout: no assumption
    assert s["scl_high_ns"][1] > 0                # stretched phases break `stall=scl_wait:0`


def test_i2c_master_no_stretch_assumption_needs_clkdiv_3():
    """At CLKDIV 2 the synchroniser delays the SCL read-back by a tick
    (i2c_master.pasm header): the checker's `stall=scl_wait:0` is then false,
    and the measurement reports it as a broken assumption."""
    chk, results = check("i2c_master.pasm", clkdiv=2)
    r = next(r for r in results if r.deadline.name == "scl_high_ns")
    ms = timing.measure(chk, r.deadline, i2c_run(2, 0, stretch_p=0))
    broken = [m for m in ms if m.violations]
    assert broken and all("stall 1 > assumed 0" in m.violations[0] for m in broken)
    assert {m.ticks for m in broken} == {5}       # in a byte: 4 ticks + the late read-back
    assert all(m.violations or r.contains(m.ticks, m.clocks) for m in ms)


# ---------------------------------------------------------------- USB full speed
def usb_device():
    mh = ModelHost()
    prog = asm.assemble((FW / "usb_fs_dev.pasm").read_text(), usbdev.defines())
    mh.apply(host.load_program(0, prog, 0, clkdiv=1 << 8))
    mh.write(sim.core_reg(0, sim.C_CRCCTRL), usbdev.CRCCTRL)
    mh.write(sim.A_LINEDIV, (4 << 16) | (4 << 8))
    mh.write(sim.A_LINEPINS, 0)
    mh.write(sim.A_LINECFG, 0x49F3)
    mh.pull_up = 0b10
    tr = timing.Tracer(mh.chip, 0, 0)
    mh.start(1)
    mh.step(50)
    return mh, tr


def stage_words(pid, payload):
    pkt = bytes([0x80]) + usb.data_packet(pid, payload)
    return [8 * len(pkt)] + [int.from_bytes(pkt[i:i + 4].ljust(4, b"\0"), "little")
                             for i in range(0, len(pkt), 4)]


def usb_session(seed, jitter, ppm):
    """SETUP/DATA0 -> ACK, IN -> NAK, IN -> staged DATA1 (+ host ACK),
    OUT/DATA1 -> ACK, OUT/DATA0 without room in the RX FIFO -> NAK."""
    mh, tr = usb_device()
    h = FsHost(mh, seed=seed, jitter_ns=jitter, ppm=ppm)
    rng = random.Random(seed)
    h.setup(0, bytes(rng.getrandbits(8) for _ in range(8)))
    mh.step(100)
    mh.pop_all(0)
    h.send(usb.token(usb.PID_IN, 0, 0))
    h.listen(h.last_eop_j, "IN -> NAK")
    for w in stage_words(usb.PID_DATA1, bytes(rng.getrandbits(8) for _ in range(rng.randrange(9)))):
        mh.push(0, w)
    mh.write(sim.A_FSET, usbdev.F_STAGED)
    h.data_in(0, 0)
    h.data_out(0, 0, usb.PID_DATA1, bytes(rng.getrandbits(8) for _ in range(8)))
    mh.step(50)                             # its 5-word record stays in the RX FIFO, so
    t = h.send(usb.token(usb.PID_OUT, 0, 0))    # the next OUT's DATA finds no room: NAK
    t = h.send(usb.data_packet(usb.PID_DATA0, b"xy"), t, h.ipd)
    assert h.listen(t, "OUT -> NAK") == usb.handshake(usb.PID_NAK)
    mh.pop_all(0)
    return mh, tr, h


USB_RUNS = [(s, j, p) for s in range(2) for j, p in [(0, 0), (9.25, 500), (9.25, -2500), (5, 2500)]]


def test_usb_fs_sound_and_pad_turnaround():
    """Program-level bounds against the model, and the pad-level turnaround
    measured by the independent bus host against bound + adjust."""
    chk, results = check("usb_fs_dev.pasm")
    total = {r.deadline.name: [0, 0, None] for r in results}
    tas = []
    for seed, jit, ppm in USB_RUNS:
        mh, tr, h = usb_session(seed, jit, ppm)
        s = soundness(chk, results, tr, label=f"seed={seed} jitter={jit} ppm={ppm}")
        for k, (n, nb, rng) in s.items():
            t = total[k]
            t[0] += n
            t[1] += nb
            if rng:
                t[2] = rng if t[2] is None else (min(t[2][0], rng[0]), max(t[2][1], rng[1]))
        # pad level: pair each response's program interval with the bus turnaround
        ms = []
        for r in results:
            if r.deadline.name.endswith("_ta"):
                ms += [(m.start_cycle, m, r) for m in timing.measure(chk, r.deadline, tr)]
        ms.sort(key=lambda x: x[0])
        assert len(ms) == len(h.turnarounds), (len(ms), h.turnarounds)
        for (_, m, r), (what, ns) in zip(ms, h.turnarounds):
            clk = ns / h.tclk
            lo, hi = r.total()
            assert lo <= clk <= hi, (r.deadline.name, what, clk, (lo, hi))
            assert 3 <= clk - m.clocks <= 4, (r.deadline.name, clk - m.clocks)
            tas.append((r.deadline.name, clk))
    print(f"\nusb_fs_dev over {len(USB_RUNS)} sessions (jitter up to 9.25 ns, ppm -2500..+2500):")
    show({k: tuple(v) for k, v in total.items()}, results)
    for name in sorted({n for n, _ in tas}):
        v = [c for n, c in tas if n == name]
        r = next(r for r in results if r.deadline.name == name)
        print(f"  {name:12s} bus turnaround {min(v):.2f}..{max(v):.2f} clk; checker {r.total()[0]:g}..{r.total()[1]:g}")
    for r in results:
        assert total[r.deadline.name][0] > 0, r.deadline.name
        assert total[r.deadline.name][1] == 0, r.deadline.name


# ---------------------------------------------------------------- usb_enum (sidecar annotations)
ENUM_DEFS = {"DP": 0, "DM": 1, "TURN": 4}


def check_enum():
    return timing.check_source((FW / "usb_enum.pasm").read_text(), ENUM_DEFS,
                               sidecar=(FW / "usb_enum.timing").read_text())


def test_usb_enum_deadlines():
    """usb_enum at full speed, from firmware/usb_enum.timing: every path meets
    the turnaround, including an IN that is the first token to the new address
    after a lost SET_ADDRESS status ACK (in_ta_any: the address is committed
    inline on that token; before the fix it went through `call commit` and
    `prep` and could be answered at 30 clocks)."""
    chk, results = check_enum()
    print()
    for r in results:
        print(r.report())
    st = {r.deadline.name: r for r in results}
    assert not chk.errors
    assert st["data_ta"].status == "PASS"
    assert all(st[n].status == "PASS (conditional)" for n in ("in_ta", "rx_bit"))
    # in_ta_any is required outright: no path from a token's end is left out
    # except by name (not for us, or not an IN), so it may not pass conditionally.
    assert st["in_ta_any"].status == "PASS" and not st["in_ta_any"].nonreaching
    assert st["in_ta"].total() == (10, 15) and st["in_ta_any"].total() == (10, 21)


def enum_session(seed, jitter, ppm, lost_ack_in=False):
    from pe import usbenum
    from pe.usbfs import FsControlHost
    mh = ModelHost()
    mh.apply(usbenum.load_writes(usbenum.assemble("full"), "full"))
    tr = timing.Tracer(mh.chip, 0, 0)
    mh.pull_up = 0b10
    mh.step(100)
    h = FsControlHost(mh, seed=seed, jitter_ns=jitter, ppm=ppm, ipd_bits=2.0, ack_bits=2.0)
    if lost_ack_in:
        h.faults["lose_host_ack"] = 1
        h.control_write(0, bytes([0x00, 5, 77, 0, 0, 0, 0, 0]))
        h._send(usb.token(usb.PID_IN, 77, 0))         # an IN first, not the usual SETUP
        assert h._response() is not None
    else:
        h.faults["lose_handshake"] = 1
        assert h.get_descriptor(0, 1, 8) == usbdev.DEVICE_DESC[:8]
        h.control_write(0, bytes([0x00, 5, 9, 0, 0, 0, 0, 0]))
        assert h.get_descriptor(9, 2, 18) == usbdev.CONFIG_DESC
        h.control_write(9, bytes([0x00, 9, 1, 0, 0, 0, 0, 0]))
    return mh, tr, h


def test_usb_enum_sound_and_pad_turnaround():
    chk, results = check_enum()
    st = {r.deadline.name: r for r in results}
    late, lost_in = [], []
    for seed, jit, ppm in USB_RUNS[:4] + [(9, 9.25, 2500)]:
        for lost in (False, True):
            mh, tr, h = enum_session(seed, jit, ppm, lost)
            s = soundness(chk, results, tr, label=f"usb_enum seed={seed} lost={lost}")
            assert s["data_ta"][0] > 0 and s["in_ta_any"][0] > 0 and s["rx_bit"][0] > 0
            ms = sorted([(m.start_cycle, m, st[n]) for n in ("in_ta_any", "data_ta")
                         for m in timing.measure(chk, st[n].deadline, tr)], key=lambda x: x[0])
            assert len(ms) == len(h.fs.turnarounds)
            for (_, m, r), (_, ns) in zip(ms, h.fs.turnarounds):
                clk = ns / h.fs.tclk
                lo, hi = r.total()
                assert lo <= clk <= hi and 3 <= clk - m.clocks <= 4, (r.deadline.name, clk, m.clocks)
                if clk > 26:
                    late.append(round(clk, 2))
            if lost:                           # the forced IN: the last response
                lost_in.append(round(h.fs.turnarounds[-1][1] / h.fs.tclk, 2))
    print(f"\nusb_enum: the forced lost-ACK IN answered at {lost_in} clocks; later than 26: {late}")
    assert not late and len(lost_in) == 5 and max(lost_in) <= 26


# ---------------------------------------------------------------- broken variants fail
def test_broken_uart_bit_time_fails():
    text = (FW / "uart_tx.pasm").read_text().replace("jmp r0--, bitloop          [6]",
                                                    "jmp r0--, bitloop          [5]")
    chk, results = check("uart_tx.pasm", text)
    st = {r.deadline.name: r for r in results}
    print("\n" + st["data_bit"].report())
    assert st["data_bit"].status == "FAIL" and (st["data_bit"].lo, st["data_bit"].hi) == (7, 7)
    assert st["start_bit"].status == "PASS" and st["frame"].status == "FAIL"


def test_broken_usb_ack_delay_fails():
    """Seven more ticks of delay before the ACK: past 6.5 bit times."""
    text = (FW / "usb_fs_dev.pasm").read_text().replace("ldi r2, 0xD280            [2]",
                                                       "ldi r2, 0xD280            [7]")
    text = text.replace("mov osr, r2\n", "mov osr, r2               [7]\n", 1)
    chk, results = check("usb_fs_dev.pasm", text)
    r = next(r for r in results if r.deadline.name == "ack_ta")
    print("\n" + r.report())
    assert r.status == "FAIL" and r.total()[1] > 26


def test_broken_usb_missing_response_delay_breaks_the_minimum():
    """Without the [4] before the NAK, an OUT's NAK can come before 2 bit times."""
    text = (FW / "usb_fs_dev.pasm").read_text().replace("mov osr, r3               [4]", "mov osr, r3")
    chk, results = check("usb_fs_dev.pasm", text)
    r = next(r for r in results if r.deadline.name == "out_nak_ta")
    print("\n" + r.report())
    assert r.status == "FAIL" and r.total()[0] < 8


# ---------------------------------------------------------------- unbounded, errors
def run_src(src, **kw):
    prog = asm.assemble(src)
    chk = timing.Checker(prog, timing.parse_annotations(src), source=src, **kw)
    return chk, chk.check()


def test_unbounded_reasons():
    src = """
    ; @deadline w from=a to=b.enter max=100
    ; @deadline f from=b.enter to=c max=100
    ; @deadline l from=c to=d max=100
    ; @deadline ok from=a to=b.enter max=100 stall=a+1:0..5
    a:  nop
        wait 1 pin 3
    b:  pull block
    c:  ldi r0, 3
    lp: jmp pin 4, lp
    d:  jmp a
    """
    chk, rs = run_src(src)
    r = {x.deadline.name: x for x in rs}
    for x in rs:
        print(x.report())
    assert r["w"].status == "FAIL (UNBOUNDED)" and "untimed WAIT" in r["w"].why_unbounded
    assert "PULL may find TXF empty" in r["f"].why_unbounded
    assert "loop through" in r["l"].why_unbounded and "@loop" in r["l"].why_unbounded
    assert r["ok"].status == "PASS" and (r["ok"].lo, r["ok"].hi) == (2, 7)
    chk, rs = run_src(src, fifo_stall=0)
    assert next(x for x in rs if x.deadline.name == "f").status == "PASS"


def test_loop_annotation_and_counted_loops():
    src = """
    ; @deadline counted from=s to=e min=13 max=13 unit=tick
    ; @deadline unknown from=u to=e max=100
    ; @deadline bounded from=u to=e max=100 unit=tick
    s:  ldi r1, 3
    lp: nop                      [1]
        jmp r1--, lp
    e:  mov r1, status
    u:  jmp 1                    ; @point u
    """
    src2 = src.replace("lp: nop                      [1]", "lp: nop                      [1] ; @loop max=4")
    chk, rs = run_src(src)
    r = {x.deadline.name: x for x in rs}
    assert r["counted"].status == "PASS"           # r1 is a known constant: the loop is unrolled
    assert r["unknown"].status == "FAIL (UNBOUNDED)"
    chk, rs = run_src(src2)
    r = {x.deadline.name: x for x in rs}
    assert (r["bounded"].lo, r["bounded"].hi) == (4, 13)


def test_timed_wait_and_timeout_flag_paths():
    src = """
    ; @deadline met from=w.enter to=x avoid=y unit=tick
    ; @deadline gave_up from=w.enter to=y avoid=x unit=tick
    ; @deadline loops from=w.enter to=y unit=tick
        ldi r2, 9
    w:  wait rise 3 timeout r2
        jmp t, y
    x:  jmp 0
    y:  jmp 0
    """
    chk, rs = run_src(src)
    r = {x.deadline.name: x for x in rs}
    assert (r["met"].lo, r["met"].hi) == (3, 11)      # edge: 1..9 stall ticks, the jmp, x
    assert (r["gave_up"].lo, r["gave_up"].hi) == (11, 11)   # T = 1 only after exactly 9
    assert r["loops"].hi == timing.INF and "loop through" in r["loops"].why_unbounded   # via x, jmp 0


def test_nested_call_rejected():
    bad = """
        call outer
        jmp 0
    outer:
        call leaf
        ret
    leaf:
        ret
    """
    good = bad.replace("outer:\n        call leaf\n        ret",
                       "outer:\n        mov r3, lr\n        call leaf\n        mov lr, r3\n        ret")
    chk, _ = run_src(bad)
    assert len(chk.errors) == 1 and "nested CALL" in chk.errors[0]
    chk, _ = run_src(good)
    assert not chk.errors
    # i2c_device leaves a subroutine by jumping to the main code: fine
    text = (FW / "i2c_device.pasm").read_text()
    chk = timing.Checker(asm.assemble(text, {"SDA": 0, "SCL": 1}), source=text)
    assert not chk.errors


def test_call_ret_paths_are_context_sensitive():
    src = """
    ; @deadline both from=a to=b min=8 max=8 unit=tick
    a:  call sub
        call sub
    b:  jmp 0
    sub:
        nop                      [1]
        ret
    """
    chk, rs = run_src(src)
    assert not chk.errors and rs[0].status == "PASS"


def test_fractional_divider_conversion():
    """Any n consecutive ticks of the 16.8 divider span n*INT + floor/ceil(n*FRAC/256)
    clocks, from every divider phase."""
    chk, _ = run_src("nop")
    for i, f in [(1, 0), (2, 1), (52, 21), (3, 128), (1, 255), (7, 200)]:
        chk.clkdiv = (i, f)
        for facc in range(256):
            gaps, acc = [], facc
            for _ in range(300):
                s = acc + f
                acc = s & 0xFF
                gaps.append(i + (s >> 8))
            for n in (1, 2, 3, 7, 8, 13, 64, 255):
                for k in range(0, 300 - n, 17):
                    span = sum(gaps[k:k + n])
                    assert chk.ticks_to_clk(n, False) <= span <= chk.ticks_to_clk(n, True)


# ---------------------------------------------------------------- random programs
REGS = ["r0", "r1", "r2", "r3"]


def random_program(rng, n):
    side = rng.choice([0, 0, 0, 1, 2])
    opt = side and rng.random() < 0.5
    dmax = min(6, (1 << (8 - side - opt)) - 1)
    lines = []
    if side:
        lines.append(f".side_set {side}{' opt' if opt else ''}")
    lines.append(".shift_out right" + (" autopull 8" if rng.random() < 0.3 else ""))
    lines.append(".shift_in left" + (" autopush 8" if rng.random() < 0.3 else ""))
    L = lambda: f"L{rng.randrange(n)}"
    R = lambda: rng.choice(REGS)
    P = lambda: rng.randrange(4)
    menu = [
        lambda: f"ldi {R()}, {rng.randrange(5)}",
        lambda: f"add {R()}, 1", lambda: f"sub {R()}, 1", lambda: f"cmp {R()}, {rng.randrange(3)}",
        lambda: f"jmp {L()}", lambda: f"jmp {R()}--, {L()}", lambda: f"jmp !{R()}, {L()}",
        lambda: f"jmp z, {L()}", lambda: f"jmp !c, {L()}", lambda: f"jmp pin {P()}, {L()}",
        lambda: f"jmp t, {L()}", lambda: f"jmp txne, {L()}", lambda: f"jmp rxf, {L()}",
        lambda: f"jmp !osre, {L()}", lambda: f"jmp f {rng.randrange(2)}, {L()}",
        lambda: f"wait {rng.randrange(2)} pin {P()} timeout {R()}",
        lambda: f"wait {rng.choice(['rise', 'fall', 'edge'])} {P()} timeout {R()}",
        lambda: f"wait {rng.randrange(2)} pin {P()}", lambda: f"wait edge {P()}",
        lambda: f"wait pins != {R()} mask 3 timeout {R()}", lambda: f"wait 1 flag {rng.randrange(2)} timeout {R()}",
        lambda: f"in pins, {rng.choice([1, 4, 8])}", lambda: f"out pins, {rng.choice([1, 4, 8])}",
        lambda: f"out null, {rng.choice([1, 8, 32])}", lambda: f"out {R()}, 4", lambda: "out isr, 3",
        lambda: rng.choice(["push block", "push noblock", "push iffull", "pull block", "pull noblock",
                            "pull ifempty", "mov isr, null", "mov osr, null"]),
        lambda: f"mov {R()}, {R()}", lambda: f"mov {R()}, status",
        lambda: f"set pins, {rng.randrange(4)}", lambda: f"pin {8 + P()}, toggle", lambda: "nop",
        lambda: f"flag set {rng.randrange(2)}", lambda: f"flag clr {rng.randrange(2)}",
    ]
    weights = [6, 3, 3, 3, 3, 5, 3, 2, 2, 3, 3, 2, 2, 2, 2, 5, 4, 1, 1, 2, 2, 2, 2, 2, 1, 1, 4, 1, 1, 1, 1, 3, 1, 1]
    for i in range(n):
        ins = rng.choices(menu, weights)[0]()
        sfx = ""
        if side and (not opt or rng.random() < 0.5):
            sfx += f" side {rng.randrange(1 << side)}"
        if rng.random() < 0.3:
            sfx += f" [{rng.randrange(dmax + 1)}]"
        lines.append(f"L{i}: {ins}{sfx}")
    return "\n".join(lines)


def test_random_programs_sound():
    """Random programs on random dividers, random pins, flags and FIFO
    traffic: every interval measured between random points lies inside the
    checker's bounds (no assumptions, so no measurement is excused)."""
    rng = random.Random(2026)
    checked = bounded = nmeas = 0
    for prog_i in range(40):
        n = rng.randrange(5, 14)
        src = random_program(rng, n)
        prog = asm.assemble(src)
        div = (rng.choice([1, 1, 2, 3]), rng.choice([0, 0, 64, 200]))
        deadlines = []
        for k in range(4):
            a, b = rng.randrange(n), rng.randrange(n)
            fk, tk = rng.choice(["enter", "done"]), rng.choice(["enter", "done"])
            deadlines.append(f"; @deadline d{k} from=L{a}.{fk} to=L{b}.{tk}")
        text = "\n".join(deadlines) + "\n" + src
        chk = timing.Checker(prog, timing.parse_annotations(text), clkdiv=div)
        results = [r for r in chk.check() if r.status != "ERROR" and r.lo < timing.INF]
        if not results:
            continue
        mh = ModelHost()
        mh.apply(host.load_program(0, prog, 0, out_pins=(8, 4), set_pins=(8, 2), side_pins=12,
                                   in_pins=0, clkdiv=(div[0] << 8) | div[1]))
        tr = timing.Tracer(mh.chip, 0, 0)
        mh.ext_oe = 0xFF
        mh.start(1)
        for cyc in range(3000):
            if rng.random() < 0.08:
                mh.uio ^= 1 << rng.randrange(4)
            if rng.random() < 0.02:
                op = rng.randrange(4)
                if op == 0:
                    mh.push(0, rng.getrandbits(32))
                elif op == 1:
                    mh.pop_all(0)
                elif op == 2:
                    mh.write(sim.A_FSET, 1 << rng.randrange(2))
                else:
                    mh.write(sim.A_FCLR, 1 << rng.randrange(2))
            else:
                mh.step()
        s = soundness(chk, results, tr, label=f"program {prog_i}:\n{text}\n")
        checked += len(results)
        bounded += sum(r.hi < timing.INF for r in results)
        nmeas += sum(v[0] for v in s.values())
    print(f"\nrandom programs: {checked} deadlines with a path ({bounded} with a finite max), "
          f"{nmeas} measured intervals, all inside the bounds")
    assert checked > 50 and bounded > 20 and nmeas > 1000


# ---------------------------------------------------------------- contract regressions (Codex review)
def test_halt_done_is_not_a_completion():
    """HALT never completes, so `to=P.done` on a HALT is an error; P.enter is fine."""
    src = "; @deadline d from=start to=end max=2\nstart: nop\nend: halt\n"
    _, (r,) = timing.check_source(src)
    assert r.status == "ERROR" and "never completes" in r.errors[0]
    _, (r,) = timing.check_source(src.replace("to=end", "to=end.enter"))
    assert r.status == "PASS" and (r.lo, r.hi) == (1, 1)


def test_nonreaching_path_fails_unless_declared():
    """A reachable path that never gets to the endpoint (a forever loop here)
    fails a deadline by default; reach=conditional passes it but lists the path."""
    src = ("; @deadline d from=start to=end max=3\nstart: nop\njmp pin 0, lost\n"
           "end: nop\nhalt\nlost: jmp lost\n")
    _, (r,) = timing.check_source(src)
    assert r.status == "FAIL (MAY NEVER REACH)" and r.nonreaching
    assert "never reach" in r.report()                  # shown without -v
    _, (r,) = timing.check_source(src.replace("max=3", "max=3 reach=conditional"))
    assert r.status == "PASS (conditional)" and "CONDITIONAL" in r.report()
    src2 = src.replace("jmp pin 0, lost", "jmp pin 0, stop").replace("lost: jmp lost", "stop: halt")
    _, (r,) = timing.check_source(src2)
    assert r.status == "FAIL (MAY NEVER REACH)"         # a HALT on the way counts too


def test_cli_exit_status(tmp_path, capsys):
    """The command line exits 0 when every deadline passes, declared
    conditional ones included (usb_fs_dev has four), and 1 on a failure."""
    assert timing.main([str(FW / "usb_fs_dev.pasm"), "--defs", "pe.usbdev:defines"]) == 0
    assert "PASS (conditional)" in capsys.readouterr().out
    bad = tmp_path / "late.pasm"
    bad.write_text("; @deadline d from=start to=end max=1\nstart: nop\nnop\nend: nop\nhalt\n")
    assert timing.main([str(bad)]) == 1
    undeclared = tmp_path / "lost.pasm"
    undeclared.write_text("; @deadline d from=start to=end max=3\nstart: nop\njmp pin 0, lost\nend: nop\nhalt\nlost: jmp lost\n")
    assert timing.main([str(undeclared)]) == 1
