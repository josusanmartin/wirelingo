"""MIDI 1.0 receiver/parser and running-status transmitter
(firmware/midi_rx.pasm, midi_tx.pasm, post-freeze) on the cycle model.

Judges: pe.postfreeze2.midi_parse, a reference receiver written from the
MIDI 1.0 specification (running status, real-time anywhere, system common,
SysEx) and checked against the generator (parse(encode(events)) == events);
the pins-only UART decoder; sigrok's uart + midi decoders.

midi_rx runs at TB = 128 ticks per bit with the chip at 4 MHz (CLKDIV 1:
tick for tick 50 MHz / 12.5), midi_tx at TB = 32 (CLKDIV 4: 1 us ticks, as
50 MHz / 50)."""
import pathlib
import random
import re

import pytest

from pe import asm, host, postfreeze as pf, postfreeze2 as p2, sigrok, sim
from pe.modelhost import ModelHost

FW = pathlib.Path(__file__).resolve().parents[2] / "firmware"
F_CLK = p2.MIDI_BAUD * 128          # 4 MHz
BIT = 1e9 / p2.MIDI_BAUD            # 32 us
RX, TX, V0 = 16, 8, 24
FOV = 0


def rx_rig(rx=RX, core=0, mh=None):
    mh = mh or ModelHost()
    prog = asm.assemble((FW / "midi_rx.pasm").read_text(), {"RX": rx, "TB": 128, "FOV": FOV})
    assert len(prog.words) <= 256
    mh.apply(host.load_program(core, prog, clkdiv=1 << 8))
    return mh


def tx_rig(rs=1, tx=TX, core=0, mh=None):
    mh = mh or ModelHost()
    prog = asm.assemble((FW / "midi_tx.pasm").read_text(), {"TX": tx, "TB": 32, "RS": rs})
    assert len(prog.words) <= 256
    mh.apply(host.load_program(core, prog, clkdiv=4 << 8))
    mh.write(sim.A_POUT, 1 << tx)
    return mh


def receive(stream, rate=1.0, bad_stop=(), drain=True, gaps=None):
    """Play the stream (8N1 at 31250 * rate baud) into midi_rx; return its
    words."""
    mh = rx_rig()
    mh.ui = 1
    mh.start(1)
    player = p2.Player()
    t_end = player.schedule(50e3, p2.uart_wave(stream, BIT / rate, parity=None, bad_stop=bad_stop, gaps=gaps))
    words, period = [], 1e9 / F_CLK
    for _ in range(int((t_end + 2e6) / period)):
        mh.ui = player.level(mh.chip.cycle * period)
        mh.step()
        if drain and mh.chip.cores[0].rxf:
            words += mh.pop_all(0)
    return mh, words + mh.pop_all(0)


def test_reference_parser_roundtrip():
    """The reference parser undoes the encoder, with and without running
    status (and the published running-status example: 90 3C 40 3E 40 is
    two note-ons)."""
    rng = random.Random(5)
    ev = p2.midi_random_events(rng, 200)
    assert p2.midi_parse(p2.midi_encode(ev)) == ev
    assert p2.midi_parse(p2.midi_encode(ev, running_status=False)) == ev
    assert len(p2.midi_encode(ev)) < len(p2.midi_encode(ev, running_status=False))
    assert p2.midi_parse(bytes.fromhex("903c403e40")) == [("msg", 0x90, (0x3C, 0x40)), ("msg", 0x90, (0x3E, 0x40))]


@pytest.mark.parametrize("seed", [1, 2])
def test_midi_rx_stream(seed):
    """A random stream of every message kind with running status and 25
    real-time bytes inserted anywhere (inside messages and SysEx), bytes back
    to back: midi_rx's messages equal the reference parser's."""
    rng = random.Random(seed)
    ev = p2.midi_random_events(rng, 50)
    stream = p2.insert_realtime(p2.midi_encode(ev), rng, 25)
    _, words = receive(stream)
    got = p2.midi_rx_events(words)
    want = p2.midi_parse(stream)
    print(f"\n{len(stream)} bytes -> {len(got)} events ({sum(e[0] == 'sysex' for e in got)} SysEx, "
          f"{sum(e[0] == 'msg' and e[1] >= 0xF8 for e in got)} real-time)")
    assert got == want


@pytest.mark.parametrize("rate", [0.98, 1.02])
def test_midi_rx_rate(rate):
    """A sender 2 % slow or fast (MIDI 1.0 allows +-1 %), back to back."""
    rng = random.Random(int(rate * 100))
    stream = p2.insert_realtime(p2.midi_encode(p2.midi_random_events(rng, 30)), rng, 10)
    _, words = receive(stream, rate=rate)
    assert p2.midi_rx_events(words) == p2.midi_parse(stream)


def test_midi_rx_negative():
    """Negative controls: data bytes with no status before them, and after a
    system common message (which cancels running status), are dropped; a
    message cut short by a new status is dropped; a byte with a low stop bit
    is reported as a framing error and dropped, the stream resumes."""
    stream = bytes.fromhex("12 34" "f3 05 06 07" "90 3c" "80 3c 00 3d" "c5 10 11" "b0 07") + bytes([0x64, 0x90, 0x40, 0x7F])
    _, words = receive(stream, gaps={1: 5 * BIT})
    got = p2.midi_rx_events(words)
    assert got == p2.midi_parse(stream) == [("msg", 0xF3, (5,)), ("msg", 0x80, (0x3C, 0)), ("msg", 0xC5, (0x10,)),
                                            ("msg", 0xC5, (0x11,)), ("msg", 0xB0, (7, 0x64)),
                                            ("msg", 0x90, (0x40, 0x7F))]
    bad = bytes.fromhex("90 3c 40 3e 40 40 41")
    _, words = receive(bad, bad_stop=(2,), gaps={2: 3 * BIT})
    got = p2.midi_rx_events(words)
    print(f"\nframing error in byte 2: {got}")
    # the byte is gone; running status pairs the data bytes that remain
    assert got[0] == ("ferr", 0x40) and got[1:] == p2.midi_parse(bad[:2] + bad[3:]) == \
        [("msg", 0x90, (0x3C, 0x3E)), ("msg", 0x90, (0x40, 0x40))]


def test_midi_rx_fifo_full():
    """A host that stops reading: the receiver never stalls; items that do
    not fit are dropped and flag FOV is set; what the FIFO kept and what comes
    after the host drains are right."""
    notes = [("msg", 0x90, (60 + i, 100)) for i in range(20)]
    stream = p2.midi_encode(notes)
    mh, words = receive(stream, drain=False)
    got = p2.midi_rx_events(words)
    assert got == notes[:8] and (mh.chip.flags >> FOV) & 1
    rng = random.Random(9)
    stream2 = p2.midi_encode(p2.midi_random_events(rng, 20))
    _, words = receive(stream + stream2)
    assert p2.midi_rx_events(words) == notes + p2.midi_parse(stream2)


@pytest.mark.parametrize("rs", [1, 0])
def test_midi_tx(rs):
    """midi_tx: the bytes on the wire are the encoder's (running status on or
    off), 8N1 with every bit exactly 32 us."""
    rng = random.Random(7 + rs)
    ev = p2.midi_random_events(rng, 40)
    mh = tx_rig(rs)
    mh.start(1)
    log = pf.PinLog(mh, pf.pin_probe(TX), 250.0)
    for w in p2.midi_tx_words(ev):
        while mh.tx_level(0) >= 8:
            log.step(4000)
        log.push(0, w)
    want = p2.midi_encode(ev, running_status=bool(rs))
    log.step(int(len(want) * 12 * BIT / 250))
    chars = p2.uart_decode(log.edges(), log.now(), BIT, parity=None)
    assert bytes(c["byte"] for c in chars) == want and all(c["stop_ok"] for c in chars)
    widths = {round(d) for lvl, t, d in pf.runs(log.edges(), log.now())[1:-1] if lvl == 0}
    assert all(w % 32000 == 0 for w in widths), widths
    print(f"\nRS={rs}: {len(ev)} events, {len(want)} bytes (without running status "
          f"{len(p2.midi_encode(ev, running_status=False))})")


def test_midi_loopback():
    """midi_tx (core 0) -> virtual pin V0 -> midi_rx (core 1): events in,
    the same events out."""
    rng = random.Random(11)
    ev = p2.midi_random_events(rng, 40)
    mh = ModelHost()
    tx_rig(1, tx=V0, core=0, mh=mh)
    rx_rig(rx=V0, core=1, mh=mh)
    mh.start(3)
    words = []
    for w in p2.midi_tx_words(ev):
        while mh.tx_level(0) >= 8:
            mh.step(400)
            words += mh.pop_all(1)
        mh.push(0, w)
    for _ in range(int(len(p2.midi_encode(ev)) * 11 * BIT * F_CLK / 1e9 / 400) + 10):
        mh.step(400)
        words += mh.pop_all(1)
    assert p2.midi_rx_events(words) == ev


@pytest.mark.skipif(sigrok.cli() is None, reason=sigrok.INSTALL_HINT)
def test_sigrok_midi():
    """sigrok's uart + midi decoders on midi_tx's output (running status on):
    notes, program change, channel pressure, pitch bend, timing clock (sigrok
    names a note on with velocity 0 a note off, as MIDI 1.0 allows, and
    counts programs from 1)."""
    ev = [("msg", 0x90, (60, 100)), ("msg", 0x90, (64, 90)), ("msg", 0x90, (60, 0)), ("msg", 0xF8, ()),
          ("msg", 0x81, (64, 10)), ("msg", 0xC2, (5,)), ("msg", 0xC2, (6,)), ("msg", 0xE0, (0x00, 0x40)),
          ("msg", 0xD3, (77,)), ("msg", 0xF8, ()), ("msg", 0xE0, (0x7F, 0x7F))]
    mh = tx_rig(1)
    mh.start(1)
    log = pf.PinLog(mh, pf.pin_probe(TX), 250.0)
    for w in p2.midi_tx_words(ev):
        while mh.tx_level(0) >= 8:
            log.step(4000)
        log.push(0, w)
    log.step(int(30 * 10 * BIT / 250))
    tr = sigrok.Trace()
    pf.to_trace(tr, log.edges(), "midi", log.now())
    got = sigrok.midi(tr, "midi", res_ps=100_000)
    print(f"\nsigrok midi: {got}")
    assert got == ["Channel 1: note on (note = 60 'C4', velocity = 100)",
                   "Channel 1: note on (note = 64 'E4', velocity = 90)",
                   "Channel 1: note off (note = 60 'C4', velocity = 0)",
                   "System Realtime: timing clock",
                   "Channel 2: note off (note = 64 'E4', velocity = 10)",
                   "Channel 3: program change to instrument 6 (assuming Electric Piano 2)",
                   "Channel 3: program change to instrument 7 (assuming Harpsichord)",
                   "Channel 1: pitch bend change 0x00 0x40 (8192)",
                   "Channel 4: channel pressure / aftertouch 77",
                   "System Realtime: timing clock",
                   "Channel 1: pitch bend change 0x7f 0x7f (16383)"]


def _sigrok_key(text):
    if m := re.fullmatch(r"Channel (\d+): (note on|note off) \(note = (\d+) '[^']*', velocity = (\d+)\)", text):
        return (m[2], int(m[1]), int(m[3]), int(m[4]))
    if m := re.fullmatch(r"Channel (\d+): pitch bend change 0x(..) 0x(..) \(\d+\)", text):
        return ("bend", int(m[1]), int(m[2], 16), int(m[3], 16))
    if m := re.fullmatch(r"System Realtime: (.*)", text):
        return ("rt", m[1])
    return ("?", text)


def _our_key(e):
    _, s, d = e
    if s == 0xF8:
        return ("rt", "timing clock")
    if s & 0xF0 in (0x80, 0x90):
        on = s & 0xF0 == 0x90 and d[1] > 0
        return ("note on" if on else "note off", (s & 15) + 1, d[0], d[1])
    if s & 0xF0 == 0xE0:
        return ("bend", (s & 15) + 1, d[0], d[1])
    return ("?", e)


@pytest.mark.skipif(sigrok.cli() is None, reason=sigrok.INSTALL_HINT)
def test_sigrok_midi_rx():
    """The receive direction: sigrok's uart + midi decoders on the stream
    played into midi_rx (running status, timing clocks inside messages); the
    firmware's messages equal sigrok's, message for message, and both see
    the 12 clocks."""
    rng = random.Random(3)
    ev = []
    for _ in range(30):
        s = rng.choice([0x90, 0x80, 0xE0]) | rng.randrange(4)
        for _ in range(rng.randrange(1, 4)):
            ev.append(("msg", s, (rng.randrange(128), rng.randrange(128))))
    stream = p2.insert_realtime(p2.midi_encode(ev), rng, 12, choices=(0xF8,))
    _, words = receive(stream)
    got = p2.midi_rx_events(words)
    tr = sigrok.Trace()
    e, t_end = p2.waveform_edges(p2.uart_wave(stream, BIT, parity=None), 50e3)
    pf.to_trace(tr, [(0.0, 1)] + e, "midi", t_end + 1e6)
    sg = [_sigrok_key(t) for t in sigrok.midi(tr, "midi", res_ps=100_000)]
    print(f"\nsigrok midi: {len(sg)} messages, firmware {len(got)}")
    ours = [_our_key(x) for x in got]
    # sigrok places a message at its first byte, before a clock that came
    # inside it; the firmware reports it when complete, after the clock
    assert [k for k in sg if k[0] != "rt"] == [k for k in ours if k[0] != "rt"] == [_our_key(x) for x in ev]
    assert sg.count(("rt", "timing clock")) == ours.count(("rt", "timing clock")) == 12
