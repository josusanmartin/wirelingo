"""Capture -> understand -> impersonate, on the model (docs/reverse-engineering.md).

1. Capture: an I2C master talks to an EEPROM; core 0 runs la_capture on the
   two bus lines and the host drains its records.
2. Understand: pe.la decodes the records into a transcript and the register
   image the EEPROM exposed.
3. Impersonate: the EEPROM is removed; core 1 runs i2c_device at the decoded
   address, serving the decoded image. The same master gets the same answers,
   and core 0's capture of the clone decodes to the same transcript. Then a
   man in the middle: one register is changed on its way to the master.

Plus the capture timing contract (edges timestamped within one 5-tick sample)
and the gap marker when the host stops draining.
"""
import os
import pathlib
import random

from pe import asm, host, i2cbus, i2cdev, la, sim
from pe.modelhost import ModelHost

FW = pathlib.Path(__file__).resolve().parents[2] / "firmware"
SDA, SCL = 0, 1
HALF = 62                                   # SCL half period, clocks: 400 kHz at 50 MHz
_rng = random.Random(5)
ROM = bytes(_rng.getrandbits(8) for _ in range(256))
OPS = [
    ("wr", 0x50, [0x10], 8),                # register read: pointer 0x10, 8 bytes
    ("w", 0x50, [0x20, 0xCA, 0xFE]),        # write two bytes at 0x20
    ("wr", 0x50, [0x20], 2),                # read them back
    ("w", 0x51, [0x00]),                    # nobody at 0x51
]
TRANSCRIPT = [
    "write to 0x50: 10",
    "read 8 bytes from 0x50: " + " ".join(f"{b:02X}" for b in ROM[0x10:0x18]),
    "write to 0x50: 20 CA FE",
    "write to 0x50: 20",
    "read 2 bytes from 0x50: CA FE",
    "write to 0x51: address NACKed",
]


def chip():
    mh = ModelHost()
    mh.apply(host.pin_owner_writes({SDA: 1, SCL: 1}))
    mh.write(sim.A_POUT, 0b11)               # released
    mh.write(sim.A_OD, 0b11)
    mh.pull_up = 0b11
    cap = asm.assemble((FW / "la_capture.pasm").read_text())
    mh.apply(host.load_program(0, cap, in_pins=0, clkdiv=1 << 8))
    mh.write(sim.core_reg(0, sim.C_R0 + 3), 0b11)     # channel mask: SDA, SCL
    return mh


def run_master(mh, ops, eeprom=None, device=None, budget=600000):
    """Master (+ EEPROM) on the bus; the host drains core 0's records and
    serves core 1's events. Returns (master results, capture words)."""
    m = i2cbus.Master(mh.bus_uio, SDA, SCL, HALF)
    gen, wait = m.run(ops), 0
    words = []
    en = 0b11 if device else 0b01
    mh.write(sim.A_CTRL, en | (1 << 8) | (1 << 16))   # (re)start core 0 with its FIFOs cleared
    for _ in range(budget):
        bus = mh.bus_uio()
        if eeprom:
            eeprom.tick(bus)
        if wait == 0:
            try:
                wait = next(gen)
            except StopIteration:
                break
        wait -= 1
        mh.ext_oe = m.low | (eeprom.low if eeprom else 0)
        mh.uio = 0
        mh.step()
        words += mh.pop_all(0)
        if device:
            for ev in mh.pop_all(1):
                for b in device.handle(ev):
                    mh.push(1, b)
    else:
        raise AssertionError("master did not finish")
    mh.step(200)
    words += mh.pop_all(0)
    return m, words


def decode(words):
    cap = la.decode_records(words)
    assert cap.exact, cap.gaps
    ev = la.i2c_events(cap, SDA, SCL)
    return cap, ev, la.i2c_summary(ev)


def test_capture_understand_impersonate():
    mh = chip()
    # 1. capture the real device
    ee = i2cbus.Eeprom(SDA, SCL, 0x50, ROM)
    m, words = run_master(mh, OPS, eeprom=ee)
    answers = m.results
    assert answers[0] == list(ROM[0x10:0x18]) and answers[2] == [0xCA, 0xFE] and answers[3] is None
    # 2. understand
    cap, ev, text = decode(words)
    print("\ncaptured:", len(words), "records,", f"{cap.end * 20 / 1000:.0f} us\n  " + "\n  ".join(text))
    assert text == TRANSCRIPT
    if os.environ.get("RE_DUMP"):                  # keep the records for pe.la's command line
        d = pathlib.Path(os.environ["RE_DUMP"])
        d.mkdir(parents=True, exist_ok=True)
        (d / "capture.txt").write_text("".join(f"{w:08x}\n" for w in words))
    image = la.i2c_register_image(ev)
    assert list(image) == [0x50]
    assert image[0x50] == {**{r: ROM[r] for r in range(0x10, 0x18)}, 0x20: 0xCA, 0x21: 0xFE}
    # 3. impersonate: the EEPROM is gone; core 1 answers at the decoded address
    addr = list(image)[0]
    dev = i2cdev.Device(addr, mem=image[addr])
    mh.apply(dev.setup_writes(1, asm.assemble((FW / "i2c_device.pasm").read_text(), {"SDA": SDA, "SCL": SCL}), SDA, SCL))
    m2, words2 = run_master(mh, OPS, device=dev)
    assert m2.results == answers
    _, _, text2 = decode(words2)
    assert text2 == TRANSCRIPT                     # the clone looks the same on the wire
    print("clone: same answers, same transcript")
    # man in the middle: register 0x12 is changed on its way to the master
    mitm = i2cdev.Device(addr, mem=image[addr], on_read=lambda reg, v: 0x00 if reg == 0x12 else v)
    m3, words3 = run_master(mh, OPS[:1], device=mitm)
    want = list(ROM[0x10:0x18])
    want[2] = 0x00
    assert m3.results == [want]
    assert decode(words3)[2][1] == "read 8 bytes from 0x50: " + " ".join(f"{b:02X}" for b in want)


def test_la_command_line(tmp_path, capsys):
    """python -m pe.la on a logged capture: transcript and a VCD."""
    mh = chip()
    _, words = run_master(mh, OPS[:1], eeprom=i2cbus.Eeprom(SDA, SCL, 0x50, ROM))
    f = tmp_path / "words.txt"
    f.write_text("".join(f"{w:08x}\n" for w in words))
    la.main([str(f), "--vcd", str(tmp_path / "bus.vcd"), "--i2c", "SDA=0,SCL=1"])
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith(f"{len(words)} states") and out[1:] == TRANSCRIPT[:2]
    vcd = (tmp_path / "bus.vcd").read_text()
    assert "$var wire 1 ! SDA $end" in vcd and "$var wire 1 \" SCL $end" in vcd
    assert vcd.count("\n#") >= len(words)


def test_device_ignores_other_addresses_and_back_pressure():
    """The clone alone on the bus: other addresses NACK; a host that answers
    late only stretches SCL; a write-only transfer stores the bytes."""
    mh = chip()
    dev = i2cdev.Device(0x2A, mem={0: 0x11, 1: 0x22})
    mh.apply(dev.setup_writes(1, asm.assemble((FW / "i2c_device.pasm").read_text(), {"SDA": SDA, "SCL": SCL}), SDA, SCL))
    mh.start(2)
    slow = []                                      # events held back for 3000 clocks
    ops = [("r", 0x2A, 2), ("w", 0x2B, [1]), ("w", 0x2A, [5, 0x55, 0x66]), ("wr", 0x2A, [5], 2)]
    m = i2cbus.Master(mh.bus_uio, SDA, SCL, HALF)
    gen, wait, held = m.run(ops), 0, 0
    for _ in range(400000):
        if wait == 0:
            try:
                wait = next(gen)
            except StopIteration:
                break
        wait -= 1
        mh.ext_oe, mh.uio = m.low, 0
        mh.step()
        evs = mh.pop_all(1)
        slow += evs
        held = held + 1 if slow else 0
        if held >= 3000:                           # the host gets round to it
            for ev in slow:
                for b in dev.handle(ev):
                    mh.push(1, b)
            slow.clear()
    else:
        raise AssertionError("master did not finish")
    assert m.results == [[0x11, 0x22], None, [True] * 4, [0x55, 0x66]]
    assert m.stretched >= 3 * 3000                 # each read byte waited for the host
    assert dev.log == [("R", [0x11, 0x22]), ("W", [5, 0x55, 0x66]), ("W", [5]), ("R", [0x55, 0x66])]


def test_device_response_times():
    """How fast the clone answers, from the SCL fall on the bus (no host bus
    traffic during the transfer: the read bytes are queued beforehand)."""
    mh = chip()
    dev = i2cdev.Device(0x2A)
    mh.apply(dev.setup_writes(1, asm.assemble((FW / "i2c_device.pasm").read_text(), {"SDA": SDA, "SCL": SCL}), SDA, SCL))
    mh.push(1, 0x5A)
    mh.push(1, 0xA5)
    mh.start(2)
    m = i2cbus.Master(mh.bus_uio, SDA, SCL, HALF)
    gen, wait = m.run([("w", 0x2A, [0x00]), ("r", 0x2A, 2)]), 0
    fall, prev_bus, prev_oe, sda_lat, scl_lat, stretch = 0, 3, 0, [], [], []
    while True:
        if wait == 0:
            try:
                wait = next(gen)
            except StopIteration:
                break
        wait -= 1
        mh.ext_oe, mh.uio = m.low, 0
        mh.step()
        bus, oe = mh.bus_uio(), mh.chip.outputs()[2]     # OD pins: oe = pulling low
        if prev_bus & 2 and not bus & 2 and not oe & 2:
            fall = mh.chip.cycle
        if (oe ^ prev_oe) & 1:
            sda_lat.append(mh.chip.cycle - fall)
        if oe & 2 and not prev_oe & 2:
            scl_lat.append(mh.chip.cycle - fall)
            t0 = mh.chip.cycle
        if prev_oe & 2 and not oe & 2:
            stretch.append(mh.chip.cycle - t0)
        prev_bus, prev_oe = bus, oe
    assert m.results == [[True, True], [0x5A, 0xA5]]
    assert [ev for ev in mh.pop_all(1)] == [0x154, 0x200, 0x400, 0x155, 0x300, 0x300, 0x500, 0x400]
    print(f"\nSDA changes {min(sda_lat)}..{max(sda_lat)} clocks after an SCL fall; "
          f"stretch starts {max(scl_lat)} clocks after it and lasts {min(stretch)} clocks")
    assert max(sda_lat) <= 10 and max(scl_lat) <= 4 and min(stretch) == 22


def test_capture_timestamps():
    """Random edges on 4 channels, spaced >= 17 ticks: each record's time is
    the edge's time plus a constant within one sample (0..4 ticks); an idle
    stretch longer than an R2 round is bridged by an idle record."""
    rng = random.Random(3)
    mh = ModelHost()
    mh.apply(host.load_program(0, asm.assemble((FW / "la_capture.pasm").read_text()), in_pins=0, clkdiv=1 << 8))
    mh.write(sim.core_reg(0, sim.C_R0 + 3), 0xF)
    mh.ext_oe, mh.uio = 0xFF, 0
    t, sched = 100, []
    for i in range(120):
        t += rng.randint(17, 400) if i != 60 else 400000     # one gap > 327,690 ticks
        sched.append((t, rng.randint(1, 15)))                # XOR mask: 1-4 channels change
    mh.start(1)
    t0, words, state, got = mh.chip.cycle, [], 0, []
    for when, flip in sched:
        while mh.chip.cycle - t0 < when:
            mh.step()
            words += mh.chip.cores[0].rxf          # take records without bus cycles (exact time)
            mh.chip.cores[0].rxf.clear()
        state ^= flip
        mh.uio = state
        got.append(state)
    mh.step(100)
    words += mh.chip.cores[0].rxf
    cap = la.decode_records(words)
    assert cap.exact
    assert [s for _, s in cap.edges[1:]] == got
    assert any(w >> 24 == la.M_IDLE for w in words)
    lag = [ct - et for (ct, _), (et, _) in zip(cap.edges[1:], sched)]   # capture time - edge time
    assert max(lag) - min(lag) <= la.POLL - 1, (min(lag), max(lag))


def test_capture_gap_when_host_stops_draining():
    """RXF full: the core waits, and the next record carries the gap marker."""
    mh = ModelHost()
    mh.apply(host.load_program(0, asm.assemble((FW / "la_capture.pasm").read_text()), in_pins=0, clkdiv=1 << 8))
    mh.write(sim.core_reg(0, sim.C_R0 + 3), 1)
    mh.ext_oe, mh.uio = 1, 0
    mh.start(1)
    for i in range(20):                            # 20 edges, nobody draining
        mh.step(50)
        mh.uio ^= 1
    mh.step(50)
    words = mh.pop_all(0)
    mh.step(100)
    words += mh.pop_all(0)
    assert len(words) == 9 and words[0] >> 24 == la.M_FIRST    # 8 + the one that waited
    mh.uio ^= 1
    mh.step(100)
    words += mh.pop_all(0)
    assert words[-1] >> 24 == la.M_GAP_CHANGE
    cap = la.decode_records(words)
    assert not cap.exact and len(cap.gaps) == 1
