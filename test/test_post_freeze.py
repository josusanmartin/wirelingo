"""Post-freeze protocols on the RTL, through the real SPI host port
(docs/post-freeze-protocols.md).

The RTL is frozen at tag rc-93c2137-t48ba; these firmwares were written
afterwards. Each test loads one (or two) of them over the bit-banged SPI port
of test.py, feeds the FIFOs through it, records the pads and judges them with
the pins-only models of pe.postfreeze and with sigrok's decoders (skipped
without sigrok-cli). Short, directed runs; the sweeps are on the model
(test/model/test_{dmx512,lin,nec,i2s,wiegand,dshot,dali,modbus,midi,quad_enc}.py).

    make COCOTB_TEST_MODULES=test_post_freeze
    make COCOTB_TEST_MODULES=test_post_freeze COCOTB_TEST_FILTER=pf_lin

Protocols whose frames last milliseconds run with a slower chip clock (the
firmware counts ticks of its divider, so the waveform in ticks is the same as
at 50 MHz; the model tests compare the two): DMX512's full universe at 1 MHz,
LIN at 5 MHz, Wiegand at 1 MHz, NEC at 456 kHz, DALI at 307.2 kHz, Modbus RTU
at 1.9968 MHz, MIDI at 4 MHz. DMX512's timing run, I2S, DShot600 and the
quadrature counter run at 50 MHz.
"""
import importlib.util
import pathlib
import random

import cocotb
from cocotb.triggers import ClockCycles, RisingEdge, ValueChange
from cocotb.utils import get_sim_time

from pe import asm, host, postfreeze as pf, postfreeze2 as p2, postfreeze3 as p3, sigrok, sim

HERE = pathlib.Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("pe_top_tests", HERE / "test.py")
T = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(T)

FW = HERE.parent / "firmware"
HAVE_SIGROK = sigrok.cli() is not None
if not HAVE_SIGROK:
    cocotb.log.warning(sigrok.INSTALL_HINT + " -- the sigrok checks in test_post_freeze are skipped")


def load(name, **defines):
    return asm.assemble((FW / f"{name}.pasm").read_text(), defines)


class Pads:
    """Value changes of ui_in, uo_out and the resolved uio bus, as (t_ns, value)."""

    def __init__(self, dut):
        self.sigs = {"ui": dut.ui_in, "uo": dut.uo_out, "uio": dut.uio_in}
        self.log = {k: [] for k in self.sigs}
        self.tasks = []
        for k, s in self.sigs.items():
            self._sample(k)
            self.tasks.append(cocotb.start_soon(self._watch(k)))

    def _sample(self, k):
        s = str(self.sigs[k].value)
        v = sum(1 << i for i, c in enumerate(reversed(s)) if c == "1")
        t = get_sim_time(unit="ps") / 1000
        if not self.log[k] or self.log[k][-1][1] != v:
            self.log[k].append((t, v))

    async def _watch(self, k):
        while True:
            await ValueChange(self.sigs[k])
            self._sample(k)

    def stop(self):
        for t in self.tasks:
            t.cancel()
        self.t_end = get_sim_time(unit="ps") / 1000

    def edges(self, bus, bit):
        return pf.bit_edges(self.log[bus], bit)

    def trace(self, chans, t0=None):
        """A sigrok.Trace with channels {name: (bus, bit)}, from t0 (ns) on."""
        tr = sigrok.Trace()
        for name, (bus, bit) in chans.items():
            e = self.edges(bus, bit)
            if t0 is not None:
                before = [x for x in e if x[0] <= t0]
                e = [(t0, before[-1][1] if before else e[0][1])] + [x for x in e if x[0] > t0]
            pf.to_trace(tr, e, name)
        tr.end(round(self.t_end * 1000))
        return tr


async def push_all(spi, core, words):
    """Push words into a core's TX FIFO, waiting for room (8 deep)."""
    fifo = sim.core_reg(core, sim.C_FIFO)
    while words:
        st = (await spi.read(sim.core_reg(core, sim.C_STATUS)))[0]
        room = 8 - (st & 15)
        if room:
            await spi.write(fifo, words[:room], fixed=True)
            words = words[room:]


async def drain(spi, core):
    st = (await spi.read(sim.core_reg(core, sim.C_STATUS)))[0]
    n = (st >> 4) & 15
    return await spi.read(sim.core_reg(core, sim.C_FIFO), n, fixed=True) if n else []


async def wait_ns(dut, ns, period):
    await ClockCycles(dut.clk, int(ns / period))


# ------------------------------------------------------------------ DMX512

@cocotb.test()
async def pf_dmx512(dut):
    """dmx512_tx on uo0: at 50 MHz (CLKDIV 50) a 9-slot packet, padded to
    1204 us, with every edge on the 4 us grid; then at 1 MHz (CLKDIV 1) two
    full universes (513 slots), decoded by sigrok's dmx512 (MTBS = 1 us, which
    that decoder needs)."""
    TX = T.UO(0)
    spi = await T.setup(dut)
    await spi.write(sim.A_POUT, [1 << TX])
    await T.run_program(spi, 0, load("dmx512_tx", TX=TX, MTBS=0), out_pins=(TX, 1), clkdiv=50 << 8)
    pads = Pads(dut)
    slots = bytes([0, 1, 2, 4, 8, 16, 32, 64, 128])
    await push_all(spi, 0, pf.dmx_words(slots) + pf.dmx_words(b"\x00\x99"))
    await wait_ns(dut, 2700e3, T.CLK_NS)
    pads.stop()
    got = pf.DmxReceiver().decode(pads.edges("uo", 0), pads.t_end)
    dut._log.info(f"DMX512 at 50 MHz: break {got[0]['brk'] / 1e3} us, MAB {got[0]['mab'] / 1e3} us, "
                  f"slots {got[0]['slots']}, break to break {got[0]['break_to_break'] / 1e3} us, "
                  f"grid error max {max(abs(e) for e in got[0]['grid_err'])} ns")
    assert bytes(got[0]["slots"]) == slots and not got[0]["errors"]
    # exact in RTL; with SDF the pad's rise and fall delays differ by ~0.2 ns
    assert abs(got[0]["brk"] - 176e3) < 1 and abs(got[0]["mab"] - 12e3) < 1
    assert abs(got[0]["break_to_break"] - 1204e3) < 1
    assert max(abs(e) for e in got[0]["grid_err"]) < 1
    assert bytes(got[1]["slots"]) == b"\x00\x99"


@cocotb.test()
async def pf_dmx512_universe(dut):
    TX = T.UO(0)
    spi = await T.setup(dut, clock_ns=1000)
    await spi.write(sim.A_POUT, [1 << TX])
    rng = random.Random(1)
    u1 = bytes([0]) + bytes(rng.getrandbits(8) for _ in range(512))
    u2 = bytes([0]) + bytes(range(256)) * 2
    words = pf.dmx_words(u1) + pf.dmx_words(u2) + pf.dmx_words(b"\x00")
    # At 1 MHz one 32-bit SPI word takes 256 us, longer than the MAB: queue the
    # first 8 words before starting the core (a restart keeps the FIFOs)
    await spi.apply(host.load_program(0, load("dmx512_tx", TX=TX, MTBS=1), profile=T.PROFILE,
                                      out_pins=(TX, 1), clkdiv=1 << 8))
    await spi.write(sim.core_reg(0, sim.C_FIFO), words[:8], fixed=True)
    pads = Pads(dut)
    await spi.apply(host.start_writes(1))
    await push_all(spi, 0, words[8:])
    await wait_ns(dut, 3000e3, 1000)
    pads.stop()
    got = pf.DmxReceiver().decode(pads.edges("uo", 0), pads.t_end)
    assert [bytes(p["slots"]) for p in got[:2]] == [u1, u2] and not any(p["errors"] for p in got)
    if HAVE_SIGROK:
        # sigrok's decoder takes a mark longer than 88 us before the first
        # BREAK for a BREAK: start the capture 20 us before the first one
        first = next(t for t, v in pads.edges("uo", 0) if v == 0)
        pk, err = sigrok.dmx512(pads.trace({"dmx": ("uo", 0)}, t0=first - 20e3), "dmx", res_ps=100_000)
        dut._log.info(f"sigrok dmx512: {[len(p['slots']) for p in pk]} slots, break "
                      f"{[p['brk_ns'] / 1e3 for p in pk]} us, MAB {[p['mab_ns'] / 1e3 for p in pk]} us, errors {err}")
        assert [p["slots"] for p in pk[:2]] == [u1, u2] and not err


# ------------------------------------------------------------------ I2S

@cocotb.test()
async def pf_i2s(dut):
    """i2s_tx at 50 MHz, 48 kHz (CLKDIV 16.28): BCLK uo0, WS uo1, SD uo2;
    20 frames then an underrun (silence, clocks running)."""
    BCLK, WS, SD = T.UO(0), T.UO(1), T.UO(2)
    spi = await T.setup(dut)
    await T.run_program(spi, 0, load("i2s_tx"), out_pins=(SD, 1), side_pins=BCLK,
                        clkdiv=host.clkdiv_for(1e9 / T.CLK_NS, 64 * 48000))
    pads = Pads(dut)
    rng = random.Random(2)
    frames = [(rng.randrange(-32768, 32768), rng.randrange(-32768, 32768)) for _ in range(20)]
    await push_all(spi, 0, [pf.i2s_word(l, r) for l, r in frames])
    await wait_ns(dut, 30 * 1e9 / 48000, T.CLK_NS)
    pads.stop()
    uo = [(t, (v >> 0) & 7) for t, v in pads.log["uo"]]
    words, rises = pf.I2sReceiver().decode(uo)
    vals = [(c, v) for c, v, _, _ in words]
    want = sum([[("L", l & 0xFFFF), ("R", r & 0xFFFF)] for l, r in frames], [])
    i = vals.index(want[0])
    assert vals[i:i + len(want)] == want and all(v == 0 for _, v in vals[i + len(want):])
    per = {round((b - a) / T.CLK_NS) for a, b in zip(rises, rises[1:])}
    dut._log.info(f"I2S: {len(want)} samples; BCLK periods {sorted(per)} clk; after the data, "
                  f"{len(vals) - i - len(want)} silent words with the clocks running")
    assert per <= {32, 33}
    if HAVE_SIGROK:
        got, warn = sigrok.i2s(pads.trace({"bclk": ("uo", 0), "ws": ("uo", 1), "sd": ("uo", 2)}),
                               "bclk", "ws", "sd", res_ps=10_000)
        sv = [(c, v) for _, c, v in got]
        j = sv.index(want[0])
        dut._log.info(f"sigrok i2s: {len(got)} words, first {got[j:j + 2]}, warnings {warn}")
        assert sv[j:j + len(want)] == want and not warn


# ------------------------------------------------------------------ DShot600

@cocotb.test()
async def pf_dshot600(dut):
    """dshot600 at 50 MHz (CLKDIV 10.42) on uo0, frames every 246 ticks."""
    ESC = T.UO(0)
    spi = await T.setup(dut)
    await spi.write(sim.core_reg(0, sim.C_R0), [0])
    await T.run_program(spi, 0, load("dshot600", GAP=100), side_pins=ESC,
                        clkdiv=host.clkdiv_for(1e9 / T.CLK_NS, 8 * 600e3))
    pads = Pads(dut)
    vals = [(1000 << 1), (48 << 1) | 1, (2047 << 1)]
    for v in vals:
        await push_all(spi, 0, [v])
        await wait_ns(dut, 2 * 246 * 1e9 / 4.8e6, T.CLK_NS)
    pads.stop()
    bit = 1e9 / 600e3
    frames = [f for f in pf.dshot_decode(pads.edges("uo", 0), pads.t_end, bit)
              if len(f[1]) == 16 and f[0] < pads.t_end - 30e3]      # not the frame the capture cut
    got = [pf.bits_value(b) for _, b, _, _ in frames]
    want = [pf.dshot_frame(v >> 1, v & 1) for v in vals]
    dedup = [g for j, g in enumerate(got) if j == 0 or g != got[j - 1]]
    hi1 = [h for _, b, hs, _ in frames for x, h in zip(b, hs) if x]
    hi0 = [h for _, b, hs, _ in frames for x, h in zip(b, hs) if not x]
    dut._log.info(f"DShot600: frames {[hex(g) for g in dedup]}, T1H {min(hi1):.0f}..{max(hi1):.0f} ns, "
                  f"T0H {min(hi0):.0f}..{max(hi0):.0f} ns")
    assert dedup[dedup.index(want[0]):][:3] == want
    assert all(abs(h - 1250) <= T.CLK_NS for h in hi1) and all(abs(h - 625) <= T.CLK_NS for h in hi0)
    if HAVE_SIGROK:
        bits, fr = [], []
        for t0, t1, d in sigrok.pwm(pads.trace({"esc": ("uo", 0)}), "esc", res_ps=10_000):
            if t1 - t0 < 2 * bit:
                bits.append(1 if d > 50 else 0)
            else:
                bits.append(1 if d / 100 * (t1 - t0) > bit / 2 else 0)
                fr.append(pf.bits_value(bits))
                bits = []
        fr = [f for j, f in enumerate(fr) if j == 0 or f != fr[j - 1]]
        dut._log.info(f"sigrok pwm -> DShot frames {[hex(f) for f in fr]}")
        assert fr[fr.index(want[0]):][:3] == want


# ------------------------------------------------------------------ NEC infrared

@cocotb.test()
async def pf_nec(dut):
    """nec_tx (core 0, LED on uo0) -> a receiver-module model -> nec_rx
    (core 1, ui0), with the chip at 456 kHz: a standard frame, a repeat code
    and an extended frame. sigrok decodes the raw carrier and the module's
    output."""
    PERIOD = 1e9 / 456e3
    LED, IR = T.UO(0), T.UI(0)
    spi = await T.setup(dut, clock_ns=round(PERIOD, 3))
    spi.set_low_inputs(1)
    await T.run_program(spi, 0, load("nec_tx", IR=LED), clkdiv=host.clkdiv_for(456e3, 114e3))
    await T.run_program(spi, 1, load("nec_rx", IR=IR), in_pins=IR, clkdiv=host.clkdiv_for(456e3, 100e3))
    tsop = pf.IrReceiverModule()

    async def module():
        while True:
            await RisingEdge(dut.clk)
            o = tsop.step(get_sim_time(unit="ps") / 1000, int(dut.uo_out.value) & 1)
            if o != (spi.ui & 1):
                spi.set_low_inputs(o)
    task = cocotb.start_soon(module())
    pads = Pads(dut)
    codes = [(0x04, 0x08, "standard"), (0, 0, "repeat"), (0xBEEF, 0x11, "extended")]
    await push_all(spi, 0, [pf.nec_word(a, c, m) for a, c, m in codes])
    recs = []
    for _ in range(35):
        await wait_ns(dut, 10e6, PERIOD)
        recs += [pf.nec_rx_record(w) for w in await drain(spi, 1)]
    task.cancel()
    pads.stop()
    dut._log.info(f"nec_rx records: {recs}")
    assert recs == [{"kind": "frame", "address": 0xFB04, "command": 0x08, "command_ok": True, "standard": True},
                    {"kind": "repeat"},
                    {"kind": "frame", "address": 0xBEEF, "command": 0x11, "command_ok": True, "standard": False}]
    if HAVE_SIGROK:
        tr = pads.trace({"led": ("uo", 0), "rx": ("ui", 0)})
        raw, w1 = sigrok.ir_nec(tr, "led", "active-high", 38000, res_ps=100_000)
        dem, w2 = sigrok.ir_nec(tr, "rx", "active-low", res_ps=100_000)
        dut._log.info(f"sigrok ir_nec: carrier {[(k, a, c) for _, k, a, c in raw]} {w1}; "
                      f"module output {[(k, a, c) for _, k, a, c in dem]} {w2}")
        want = [("frame", 0x04, 0x08), ("repeat", None, None)]
        # sigrok's decoder knows only the standard NEC frame: the extended one
        # (0xBEEF is not an address and its inverse) is reported as an address error
        assert [(k, a, c) for _, k, a, c in raw][:2] == want and [(k, a, c) for _, k, a, c in dem][:2] == want


# ------------------------------------------------------------------ Wiegand

@cocotb.test()
async def pf_wiegand(dut):
    """wiegand_tx (core 0) -> uio0/uio1 open drain, pulled up -> wiegand_rx
    (core 1), at 1 MHz (CLKDIV 1, 1 tick = 1 us), 200 us bit interval: a
    26-bit and a 34-bit frame, decoded and parity-checked by the receiver."""
    D0, D1 = T.UIO(0), T.UIO(1)
    spi = await T.setup(dut, clock_ns=1000)
    dut.pull_up.value = 0b11
    await spi.write(sim.A_POUT, [0b11])
    await spi.write(sim.A_OD, [0b11])
    await T.run_program(spi, 0, load("wiegand_tx", D0=D0, D1=D1, PW=50, IV=200, FG=2000), clkdiv=1 << 8)
    await spi.write(sim.core_reg(0, sim.C_CRCCTRL), [1 | (1 << 21)])
    await T.run_program(spi, 1, load("wiegand_rx", D0=D0, FTO=1000, PMAX=500), in_pins=D0, clkdiv=1 << 8)
    await spi.write(sim.core_reg(1, sim.C_CRCCTRL), [1 | (1 << 20)])
    pads = Pads(dut)
    frames = [(24, pf.h10301(42, 31337)), (32, 0x12345678)]
    await push_all(spi, 0, sum([pf.wiegand_words(n, d) for n, d in frames], []))
    words = []
    for _ in range(20):
        await wait_ns(dut, 1e6, 1000)
        words += await drain(spi, 1)
    pads.stop()
    got = pf.wiegand_rx_frames(words)
    want = [pf.wiegand_bits(n, d) for n, d in frames]
    dut._log.info(f"wiegand_rx: {[(g['bits'], g['valid']) for g in got]}")
    assert [g["bits"] for g in got] == want and all(g["valid"] and not g["error"] for g in got)
    dec = pf.wiegand_decode(pads.log["uio"], gap_ns=1e6)
    assert [b for _, b, _, _ in dec] == want
    # to the ns: exact in RTL; with SDF the two lines' pad delays differ by < 0.1 ns
    assert {round(w) for _, _, ws, _ in dec for w in ws} == {50000}
    assert {round(i) for _, _, _, iv in dec for i in iv} == {200000}
    if HAVE_SIGROK:
        sg, bad = sigrok.wiegand(pads.trace({"d0": ("uio", 0), "d1": ("uio", 1)}), "d0", "d1",
                                 bitwidth_ms=1, res_ps=100_000)
        dut._log.info(f"sigrok wiegand: {[b for _, b in sg]} invalid={bad}")
        assert [b for _, b in sg] == want and not bad


# ------------------------------------------------------------------ LIN

@cocotb.test()
async def pf_lin(dut):
    """lin_commander (core 0, TXD uo0) and lin_responder (core 1, TXD uo1) on
    one bus (RXD = ui0 = uo0 AND uo1), at 5 MHz: the commander publishes 4
    bytes, then subscribes to 3 the responder has staged. sigrok's uart + lin
    decode the bus."""
    PERIOD = 200.0
    TXC, TXR, RX = T.UO(0), T.UO(1), T.UI(0)
    spi = await T.setup(dut, clock_ns=PERIOD)
    spi.set_low_inputs(1)
    await spi.apply(host.pin_owner_writes({TXC: 0, TXR: 1}, profile=T.PROFILE))
    await spi.write(sim.A_POUT, [(1 << TXC) | (1 << TXR)])

    async def bus():
        while True:
            await RisingEdge(dut.clk)
            uo = int(dut.uo_out.value)
            b = (uo >> 0) & (uo >> 1) & 1
            if b != (spi.ui & 1):
                spi.set_low_inputs(b)
    task = cocotb.start_soon(bus())
    await spi.write(sim.core_reg(1, sim.C_CRCCTRL), [15 << 16])
    await T.run_program(spi, 0, load("lin_commander", TX=TXC, RX=RX, TB=104, BTMO=416), out_pins=(TXC, 1),
                        in_pins=RX, clkdiv=host.clkdiv_for(5e6, 19200 * 104))
    await T.run_program(spi, 1, load("lin_responder", TX=TXR, RX=RX, BRK=1144, TMIN=80, TMAX=130, BTMO=416),
                        out_pins=(TXR, 1), in_pins=RX, clkdiv=host.clkdiv_for(5e6, 2e6))
    pads = Pads(dut)
    staged = bytes([0xC0, 0xFF, 0xEE])
    await push_all(spi, 1, pf.lin_stage(0x21, staged))
    await push_all(spi, 0, pf.lin_command(0x10, data=bytes([1, 2, 3, 4])) + pf.lin_command(0x21, n=3))
    recs = {0: [], 1: []}
    for _ in range(15):
        await wait_ns(dut, 1e6, PERIOD)
        for c in (0, 1):
            recs[c] += await drain(spi, c)
    task.cancel()
    pads.stop()
    cmd, rsp = pf.lin_records(recs[0]), pf.lin_records(recs[1])
    dut._log.info(f"commander: {cmd}\nresponder: {rsp}")
    bit = 1e9 / 19200
    frames = pf.lin_decode(pads.edges("ui", 0), pads.t_end, bit)
    want0 = bytes([0x55, pf.lin_pid(0x10), 1, 2, 3, 4, pf.lin_checksum(pf.lin_pid(0x10), [1, 2, 3, 4])])
    want1 = bytes([0x55, pf.lin_pid(0x21)]) + staged + bytes([pf.lin_checksum(pf.lin_pid(0x21), staged)])
    assert [f["bytes"] for f in frames] == [want0, want1]
    assert cmd[0]["published"] and cmd[1]["bytes"] == staged and cmd[1]["flag0"]
    assert rsp[0]["bytes"] == want0[2:] and rsp[0]["flag1"] and rsp[1]["published"]
    if HAVE_SIGROK:
        fr, warn = sigrok.lin(pads.trace({"lin": ("ui", 0)}), "lin", 19200, version=2, res_ps=100_000)
        s = [(f["id"], f["parity_ok"], f["data"].hex(), f["checksum_ok"]) for f in fr]
        dut._log.info(f"sigrok lin: {s} {warn}")
        assert s == [(0x10, True, "01020304", True), (0x21, True, staged.hex(), True)]


# ------------------------------------------------------------------ bidirectional DShot

@cocotb.test()
async def pf_dshot_bidir(dut):
    """dshot_bidir at 48 MHz (CLKDIV 1) on uio0 (pulled up) against the
    pins-only ESC model: inverted frames out, GCR telemetry back 30 us later
    at 750 kbit/s, recovered by the firmware and decoded by the host."""
    ESC = T.UIO(0)
    PERIOD = 20.834
    spi = await T.setup(dut, clock_ns=PERIOD)
    dut.pull_up.value = 1 << ESC
    await spi.write(sim.core_reg(0, sim.C_R0), [0])
    await T.run_program(spi, 0, load("dshot_bidir", **pf.dshot_bidir_defines(48e6, ESC)), side_pins=ESC,
                        in_pins=ESC, clkdiv=1 << 8)
    esc = pf.BidirEsc([pf.erpm_telemetry(p) for p in (333, 4096, 65000)], rate=1.03)

    async def model():
        while True:
            await RisingEdge(dut.clk)
            drive, lvl = esc.step(get_sim_time(unit="ps") / 1000, int(dut.uio_in.value) & 1)
            T.EXT.set(ESC, drive, lvl)
    task = cocotb.start_soon(model())
    pads = Pads(dut)
    vals = [(800 << 1), (12 << 1) | 1, (1999 << 1)]
    recs = []
    for v in vals:
        await spi.write(sim.core_reg(0, sim.C_FIFO), [v], fixed=True)
        await wait_ns(dut, 190e3, PERIOD)
        recs += await drain(spi, 0)
    task.cancel()
    pads.stop()
    tel = [pf.dshot_telemetry(r) for r in recs if r]
    dut._log.info(f"ESC saw {[(hex(f), ok) for _, f, ok in esc.frames]}; telemetry "
                  f"{[(hex(t['value']), t['checksum_ok'], t['period_us']) for t in tel]}; sent "
                  f"{[hex(x) for x in esc.sent]}; contention {esc.contention}")
    seen = [f for _, f, ok in esc.frames if ok]
    assert len(seen) == len(esc.frames)                         # every checksum good
    dedup = [f for j, f in enumerate(seen) if j == 0 or f != seen[j - 1]]
    assert dedup[-3:] == [pf.dshot_bidir_frame(v >> 1, v & 1) for v in vals]   # after R0's motor-stop frame
    assert [t["value"] for t in tel] == esc.sent and all(t["checksum_ok"] for t in tel)
    assert esc.contention == 0


# ------------------------------------------------------------------ DALI

@cocotb.test()
async def pf_dali(dut):
    """dali_ctrl (core 0, TX uo0, RX ui0) against the pins-only control-gear
    model on a wired-AND bus, the chip at 307.2 kHz (CLKDIV 4, TE = 32: tick
    for tick 50 MHz / 651.04): a broadcast forward frame, two queries the gear
    answers (8 ms settling time) and one it does not. sigrok's dali decodes
    the bus."""
    TE = 32
    PERIOD = 1e9 / (2400 * TE * 4)
    TX, RX = T.UO(0), T.UI(0)
    spi = await T.setup(dut, clock_ns=round(PERIOD, 3))
    spi.set_low_inputs(1)
    await spi.write(sim.A_POUT, [1 << TX])
    await T.run_program(spi, 0, load("dali_ctrl", **p2.dali_defines(TX, RX, TE)), clkdiv=4 << 8)
    gear = p2.DaliGear(lambda v, n: {0x0390: 0x5A, 0x0399: 0xC3}.get(v), settle=8e6)

    async def bus():
        while True:
            await RisingEdge(dut.clk)
            t = get_sim_time(unit="ps") / 1000
            b = int(dut.uo_out.value) & 1 & gear.player.level(t)
            gear.step(t, b)
            if b != (spi.ui & 1):
                spi.set_low_inputs(b)
    task = cocotb.start_soon(bus())
    pads = Pads(dut)
    await push_all(spi, 0, [p2.dali_word(0xFE40), p2.dali_word(0x0390, reply=True),
                            p2.dali_word(0x0399, reply=True), p2.dali_word(0x0590, reply=True)])
    words = []
    for _ in range(26):
        await wait_ns(dut, 10e6, PERIOD)
        words += await drain(spi, 0)
    task.cancel()
    pads.stop()
    recs = p2.dali_records(words)
    tick = p2.DALI_TE / TE
    dut._log.info(f"dali_ctrl: {[(r['answer'], r['n'], r['no_answer'], round(r['settle_ticks'] * tick / 1e3)) for r in recs]}"
                  f"; the gear decoded {[(hex(v), n) for _, v, n, _ in gear.frames]}")
    assert [r["answer"] for r in recs] == [0x5A, 0xC3, None] and recs[2]["no_answer"]
    assert not any(r["violation"] for r in recs)
    assert all(0 <= r["settle_ticks"] * tick - 8e6 <= 2 * tick for r in recs[:2])
    assert [(v, n, e) for _, v, n, e in gear.frames] == [(0xFE40, 16, None), (0x0390, 16, None),
                                                         (0x0399, 16, None), (0x0590, 16, None)]
    fw = p2.dali_decode(pads.edges("uo", 0), pads.t_end)
    assert [f["value"] for f in fw] == [0xFE40, 0x0390, 0x0399, 0x0590]
    assert {round(d / tick) for f in fw for d in f["runs"]} <= {32, 64}
    if HAVE_SIGROK:
        got = sigrok.dali(pads.trace({"dali": ("ui", 0)}), "dali", res_ps=100_000)
        s = [(k, b.hex()) for _, k, b in got]
        dut._log.info(f"sigrok dali: {s}")
        assert s == [("forward", "fe40"), ("forward", "0390"), ("backward", "5a"), ("forward", "0399"),
                     ("backward", "c3"), ("forward", "0590")]


# ------------------------------------------------------------------ Modbus RTU

@cocotb.test()
async def pf_modbus(dut):
    """modbus_rtu (core 0: TX uo0, DE uo1, RX ui0) and the pins-only server
    model on one RS-485 line, 19200 baud with the chip at 1.9968 MHz (CLKDIV 1,
    TB = 104: tick for tick 50 MHz / 25.04): a read of 3 holding registers
    (the published example request 11 03 00 6B 00 03, CRC 76 87) and a write
    the server echoes. sigrok's uart + modbus decode both directions."""
    BAUD, TB = 19200, 104
    PERIOD = 500.8                      # ns: 1 / (19200 * 104) = 500.80 ns
    TX, DE, RX = T.UO(0), T.UO(1), T.UI(0)
    spi = await T.setup(dut, clock_ns=round(PERIOD, 3))
    spi.set_low_inputs(1)
    await spi.write(sim.A_POUT, [1 << TX])
    await spi.write(sim.core_reg(0, sim.C_CRCCTRL), [p2.MODBUS_CRCCTRL])
    await T.run_program(spi, 0, load("modbus_rtu", **p2.modbus_defines(BAUD, TB, TX, DE, RX)), clkdiv=1 << 8)
    regs = {0x6B: 0xAE41, 0x6C: 0x5652, 0x6D: 0x4340}

    def respond(body):
        if body[1] == 3:
            return bytes([body[0], 3, 6]) + b"".join(regs[0x6B + i].to_bytes(2, "big") for i in range(3))
        return bytes(body)
    srv = p2.ModbusServer(respond, baud=BAUD)
    state = {"drive": (0, 1), "contention": 0}

    async def bus():
        while True:
            await RisingEdge(dut.clk)
            t = get_sim_time(unit="ps") / 1000
            uo = int(dut.uo_out.value)
            sd, sl = state["drive"]
            line = (uo & 1) if (uo >> 1) & 1 else (sl if sd else 1)
            state["contention"] += (uo >> 1) & 1 & sd
            state["drive"] = srv.step(t, line)
            if line != (spi.ui & 1):
                spi.set_low_inputs(line)
    task = cocotb.start_soon(bus())
    pads = Pads(dut)
    reqs = [bytes.fromhex("1103006b0003"), bytes.fromhex("110600010003")]
    words = []
    for r in reqs:
        await push_all(spi, 0, p2.modbus_words(r))
        for _ in range(12):
            await wait_ns(dut, 2e6, PERIOD)
            words += await drain(spi, 0)
    task.cancel()
    pads.stop()
    got = p2.modbus_response(words)
    dut._log.info(f"modbus_rtu: {[(g['bytes'].hex(), g['crc_ok']) for g in got]}; the server decoded "
                  f"{[(q[1].hex(), q[2], q[3]) for q in srv.requests]}")
    assert [q[1] for q in srv.requests] == [bytes.fromhex("1103006b00037687"), p2.modbus_adu(reqs[1])]
    assert all(q[2] and q[3] and q[4] for q in srv.requests)
    assert [g["bytes"] for g in got] == [p2.modbus_adu(respond(r)) for r in reqs]
    assert all(g["crc_ok"] and not (g["parity_error"] or g["framing_error"] or g["gap"]) for g in got)
    assert state["contention"] == 0
    if HAVE_SIGROK:
        # split the bus into the client's and the server's characters by DE
        de = pads.edges("uo", 1)
        client, server = [(0.0, 1)], [(0.0, 1)]
        for t, v in pads.edges("ui", 0):
            on = [d for tt, d in de if tt <= t]
            dst = client if on and on[-1] else server
            if v != dst[-1][1]:
                dst.append((t, v))
        tr = sigrok.Trace()
        pf.to_trace(tr, client, "client")
        pf.to_trace(tr, server, "server")
        tr.end(round(pads.t_end * 1000))
        m = sigrok.modbus(tr, "client", "server", BAUD, res_ps=100_000)
        cs = [t for _, t in m["cs"] if t.startswith("Function") or "CRC" in t]
        sc = [t for _, t in m["sc"] if t.startswith("Function") or "CRC" in t]
        dut._log.info(f"sigrok modbus: client->server {cs}; server->client {sc}")
        want = ["Function 3: Read Holding Registers", "CRC correct", "Function 6: Write Single Register", "CRC correct"]
        assert cs == want and sc == want


# ------------------------------------------------------------------ MIDI

@cocotb.test()
async def pf_midi(dut):
    """midi_tx (core 0, uo0, running status) wired to midi_rx (core 1, ui0)
    with the chip at 4 MHz (midi_rx: CLKDIV 1, TB = 128; midi_tx: CLKDIV 4,
    TB = 32): notes on one status (running status), program change, pitch
    bend, a song position, a SysEx, real-time bytes between messages. The
    receiver's messages equal those sent; sigrok's midi decodes the wire."""
    PERIOD = 250.0
    TX, RX = T.UO(0), T.UI(0)
    spi = await T.setup(dut, clock_ns=PERIOD)
    spi.set_low_inputs(1)
    await spi.write(sim.A_POUT, [1 << TX])

    async def wire():
        while True:
            await RisingEdge(dut.clk)
            b = int(dut.uo_out.value) & 1
            if b != (spi.ui & 1):
                spi.set_low_inputs(b)
    task = cocotb.start_soon(wire())
    await T.run_program(spi, 1, load("midi_rx", RX=RX, TB=128, FOV=0), clkdiv=1 << 8)
    await T.run_program(spi, 0, load("midi_tx", TX=TX, TB=32, RS=1), clkdiv=4 << 8)
    pads = Pads(dut)
    ev = [("msg", 0x90, (60, 100)), ("msg", 0x90, (64, 100)), ("msg", 0xF8, ()), ("msg", 0x90, (67, 100)),
          ("msg", 0xC1, (12,)), ("msg", 0xE0, (0x00, 0x48)), ("msg", 0xF2, (0x10, 0x02)),
          ("sysex", bytes([0x7E, 0x7F, 0x09, 0x01]), True), ("msg", 0xFA, ()), ("msg", 0x90, (60, 0)),
          ("msg", 0x90, (64, 0))]
    await push_all(spi, 0, p2.midi_tx_words(ev))
    words = []
    for _ in range(8):
        await wait_ns(dut, 2e6, PERIOD)
        words += await drain(spi, 1)
    task.cancel()
    pads.stop()
    got = p2.midi_rx_events(words)
    wire_bytes = p2.midi_encode(ev)
    chars = p2.uart_decode(pads.edges("uo", 0), pads.t_end, 1e9 / p2.MIDI_BAUD, parity=None)
    dut._log.info(f"midi_rx: {got}; on the wire {bytes(c['byte'] for c in chars).hex(' ')} "
                  f"({len(wire_bytes)} bytes, {len(p2.midi_encode(ev, running_status=False))} without running status)")
    assert bytes(c["byte"] for c in chars) == wire_bytes
    assert got == ev
    if HAVE_SIGROK:
        sg = sigrok.midi(pads.trace({"midi": ("uo", 0)}), "midi", res_ps=100_000)
        dut._log.info(f"sigrok midi: {sg}")
        assert len(sg) == len(ev) and sg[0] == "Channel 1: note on (note = 60 'C4', velocity = 100)"
        assert sg[2] == "System Realtime: timing clock" and sg[3].startswith("Channel 1: note on (note = 67")
        assert sg[6] == "System Common: song position pointer 0x10 0x02 (272)"
        assert sg[8] == "System Realtime: start"
        assert sg[7] == "system exclusive: for 'Universal Non-Realtime' with payload 0x7f 0x09 0x01 "


# ------------------------------------------------------------------ quadrature encoder

def pf2_quad(pads):
    return p2.quad_count([(t, v & 3) for t, v in pads.log["ui"]])


@cocotb.test()
async def pf_quad_enc(dut):
    """quad_enc (core 0, A = ui0, B = ui1) at 50 MHz, CLKDIV 1: a random walk
    of 1500 steps 15 to 60 clocks apart; the position read through the core's
    R3 register over SPI equals the encoder's; then a jump of two states sets
    flag FERR and leaves the position. sigrok's graycode counts the same."""
    A, FERR = T.UI(0), 6
    spi = await T.setup(dut)
    spi.set_low_inputs(0)
    await spi.write(sim.core_reg(0, sim.C_R0 + 3), [0])
    await T.run_program(spi, 0, load("quad_enc", A=A, FERR=FERR), clkdiv=1 << 8)
    pads = Pads(dut)
    await ClockCycles(dut.clk, 20)              # (the capture starts at the idle state)
    seq, phase, pos, d = [0, 1, 3, 2], 0, 0, 1
    rng = random.Random(6)
    for _ in range(1500):
        if rng.random() < 0.05:
            d = -d
        phase, pos = (phase + d) % 4, pos + d
        spi.set_low_inputs(seq[phase])
        await ClockCycles(dut.clk, rng.choice([15, 16, 20, 30, 60]))
    await ClockCycles(dut.clk, 50)
    got = (await spi.read(sim.core_reg(0, sim.C_R0 + 3)))[0]
    flags = (await spi.read(sim.A_FSET))[0]
    got = got - 0x10000 if got & 0x8000 else got
    dut._log.info(f"quad_enc: position {got}, the encoder's {pos}, FERR {(flags >> FERR) & 1}")
    assert got == pos and not (flags >> FERR) & 1
    phase = (phase + 2) % 4                     # both lines at once: a missed step
    spi.set_low_inputs(seq[phase])
    await ClockCycles(dut.clk, 50)
    after = (await spi.read(sim.core_reg(0, sim.C_R0 + 3)))[0]
    flags = (await spi.read(sim.A_FSET))[0]
    assert (after - 0x10000 if after & 0x8000 else after) == pos and (flags >> FERR) & 1
    phase = (phase + 1) % 4                     # one more step: counted again
    spi.set_low_inputs(seq[phase])
    await ClockCycles(dut.clk, 50)
    last = (await spi.read(sim.core_reg(0, sim.C_R0 + 3)))[0]
    assert (last - 0x10000 if last & 0x8000 else last) == pos + 1
    pads.stop()
    assert pf2_quad(pads) == (pos + 1, 1)          # the pins-only decoder on the pads
    if HAVE_SIGROK:
        # graycode annotates a count when it changes: the last one shown is
        # the position before the final step (the double step counts 0 there too)
        counts = sigrok.graycode(pads.trace({"a": ("ui", 0), "b": ("ui", 1)}), ["a", "b"], res_ps=10_000)
        dut._log.info(f"sigrok graycode: {len(counts)} count changes, the last shown {counts[-1]}")
        assert counts[-1] == pos


# ------------------------------------------------------------------ third round: HDMI-CEC

@cocotb.test()
async def pf_cec(dut):
    """cec (core 0, uio0 open drain, pulled up, logical address 4) and the
    pins-only CEC device model (the TV, address 0) on one line, the chip at
    100 kHz with CLKDIV 1 (1 tick = 10 us: tick for tick 50 MHz / 500): our
    frames to the TV (acknowledged), a broadcast, a poll nobody answers
    (NACK); then the TV's frame to us, which the firmware acknowledges.
    sigrok's cec decodes the line."""
    PERIOD = 10_000.0
    CEC = T.UIO(0)
    spi = await T.setup(dut, clock_ns=PERIOD)
    dut.pull_up.value = 1 << CEC
    await spi.write(sim.A_POUT, [1 << CEC])
    await spi.write(sim.A_OD, [1 << CEC])
    await spi.write(sim.core_reg(0, sim.C_CRCCTRL), [p3.CEC_CRCCTRL])
    await spi.write(sim.core_reg(0, sim.C_CRC), [4])
    await T.run_program(spi, 0, load("cec", **p3.cec_defines(CEC)), clkdiv=1 << 8)
    tv = p3.CecNode(addr=0)

    async def bus():
        while True:
            await RisingEdge(dut.clk)
            t = get_sim_time(unit="ps") / 1000
            d = tv.step(t, int(dut.uio_in.value) >> CEC & 1)
            T.EXT.set(CEC, not d, 0)
    task = cocotb.start_soon(bus())
    pads = Pads(dut)
    ours = [bytes([0x40, 0x04]), bytes([0x4F, 0x82, 0x10, 0x00]), bytes([0x4B])]
    await push_all(spi, 0, p3.cec_words(ours[0], 5) + p3.cec_words(ours[1], 7) + p3.cec_words(ours[2], 7))
    words = []
    for _ in range(25):
        await wait_ns(dut, 10e6, PERIOD)
        words += await drain(spi, 0)
    tv.send(bytes([0x04, 0x8F]))
    for _ in range(10):
        await wait_ns(dut, 10e6, PERIOD)
        words += await drain(spi, 0)
    task.cancel()
    pads.stop()
    frames, tx, err = p3.cec_records(words)
    dut._log.info(f"cec: ours {tx}; received {[(f['blocks'].hex(), f['ack'], f['to_us']) for f in frames]}; "
                  f"the TV received {[(f['blocks'].hex(), f['ack']) for f in tv.frames]}, sent {tv.results}")
    assert [(r["ok"], r["nack"]) for r in tx] == [(True, False), (True, False), (False, True)] and not err
    assert [f["blocks"] for f in tv.frames] == ours + [bytes([0x04, 0x8F])]
    assert [f["blocks"] for f in frames] == [bytes([0x04, 0x8F])] and frames[0]["to_us"] and frames[0]["ack"] == [0, 0]
    assert tv.results[0][2] == "ok"
    dec = p3.cec_decode(pads.edges("uio", CEC), pads.t_end)
    assert [f["blocks"] for f in dec] == ours + [bytes([0x04, 0x8F])] and not any(f["error"] for f in dec)
    if HAVE_SIGROK:
        got, warn = sigrok.cec(pads.trace({"cec": ("uio", CEC)}), "cec", res_ps=100_000)
        dut._log.info(f"sigrok cec: {got} {warn}")
        assert [g[1] for g in got] == ["40:04", "4f:82:10:00", "4b", "04:8f"]
        assert [g[2].endswith("R: ACK") for g in got] == [True, True, False, True]


# ------------------------------------------------------------------ ISO 7816-3 T=0

@cocotb.test()
async def pf_iso7816(dut):
    """iso7816 (core 0: I/O = uio0 open drain, RST = uo0) and its clock
    (core 1: CLK = uo1, CLKDIV 1: 2 MHz) with the chip at 4 MHz, against the
    pins-only T=0 card timed by that CLK (one etu = 372 periods): cold reset
    and the answer to reset (direct convention detected), then READ BINARY
    of 2 bytes, the first of which the card sends with a wrong parity bit
    once: the reader's error signal and the card's repetition. sigrok's
    uart (even parity) decodes I/O."""
    PERIOD = 250.0
    IO, RST, CLK = T.UIO(0), T.UO(0), T.UO(1)
    ATR = bytes.fromhex("3BB4110000574C474F")
    spi = await T.setup(dut, clock_ns=PERIOD)
    dut.pull_up.value = 1 << IO
    await spi.apply(host.pin_owner_writes({CLK: 1}, profile=T.PROFILE))
    await spi.write(sim.A_POUT, [1 << IO])
    await spi.write(sim.A_OD, [1 << IO])
    await spi.write(sim.core_reg(0, sim.C_CRCCTRL), [p3.ISO_CRCCTRL])
    d0, d1 = p3.iso_clkdivs(1)
    card = p3.IsoCard(ATR, corrupt={len(ATR) + 1: 1})

    async def contacts():
        while True:
            await RisingEdge(dut.clk)
            uo = int(dut.uo_out.value)
            d = card.step(get_sim_time(unit="ps") / 1000, uo >> 1 & 1, uo & 1, int(dut.uio_in.value) & 1)
            T.EXT.set(IO, not d, 0)
    task = cocotb.start_soon(contacts())
    prog = load("iso7816", **p3.iso_defines(IO, RST, wt=16, finv=5, fov=7))
    await spi.apply(host.load_program(0, prog, 0, profile=T.PROFILE, clkdiv=d0))
    await spi.apply(host.load_program(1, load("iso7816_clk", CLK=CLK), 256, profile=T.PROFILE, clkdiv=d1))
    pads = Pads(dut)
    await spi.write(sim.A_CTRL, [3 | (3 << 8)])               # both cores together
    etu = 744 * PERIOD
    await push_all(spi, 0, [p3.ISO_RESET] + p3.iso_recv(33, first_timeout_etu=112, ts=True))
    words = []
    for _ in range(20):
        await wait_ns(dut, 10 * etu, PERIOD)
        words += await drain(spi, 0)
        if any(r[0] == "recv" for r in p3.iso_records(words)):
            break
    await push_all(spi, 0, p3.iso_send(bytes([0x00, 0xB0, 0x00, 0x10, 0x02])) + p3.iso_recv(7))
    for _ in range(30):
        await wait_ns(dut, 10 * etu, PERIOD)
        words += await drain(spi, 0)
        if sum(r[0] == "recv" for r in p3.iso_records(words)) == 2:
            break
    task.cancel()
    pads.stop()
    recs = p3.iso_records(words)
    chars = [r[1] for r in recs if r[0] == "char"]
    dut._log.info(f"iso7816: {recs}; the card: received {[hex(b) for _, b, _, _ in card.received]}, "
                  f"commands {card.commands}, errors seen {card.err_seen}")
    assert bytes(chars[:len(ATR)]) == ATR
    assert bytes(chars[len(ATR):]) == bytes([0xB0, 0x10, 0x11, 0x90, 0x00])
    assert [r[2] for r in recs if r[0] == "char"][len(ATR) + 1] == 1          # one error signal for 0x10
    assert card.commands[0][0] == bytes([0x00, 0xB0, 0x00, 0x10, 0x02])
    rises = [t for t, v in pads.edges("uo", 1) if v]
    per = {round(b - a) for a, b in zip(rises, rises[1:])}
    assert per == {500}                                          # CLK 2 MHz, steady
    if HAVE_SIGROK:
        tr = pads.trace({"io": ("uio", IO)})
        data, warn = sigrok.uart_parity(tr, "io", round(1e9 / etu), res_ps=100_000)
        got = bytes(b for _, b in data)
        dut._log.info(f"sigrok uart: {got.hex()} {warn}")
        assert got == ATR + bytes.fromhex("00b0001002" "b0" "10" "10" "11" "9000")
        assert [w for _, w in warn] == ["Frame error"]


# ------------------------------------------------------------------ SMBus with PEC

@cocotb.test()
async def pf_smbus(dut):
    """smbus_host (core 0: SDA = uio0, SCL = uio1, open drain, pulled up)
    and the pins-only SMBus device model (a smart battery at 0x0B, with PEC,
    stretching SCL 20 us after every ACK), the chip at 3.571 MHz with CLKDIV
    4 (tick for tick 50 MHz / 56): write word, read word, block read, all
    with PEC; then a poll of an absent address (NACK). sigrok's i2c decodes
    the bus and the PECs are recomputed over its bytes."""
    PERIOD = 280.0
    SDA, SCL = T.UIO(0), T.UIO(1)
    BAT = 0x0B
    regs = {0x09: bytes([0x34, 0x12]), 0x20: bytes([5]) + b"Wirel"}
    spi = await T.setup(dut, clock_ns=PERIOD)
    dut.pull_up.value = 3
    await spi.write(sim.A_POUT, [3])
    await spi.write(sim.A_OD, [3])
    await spi.write(sim.core_reg(0, sim.C_CRCCTRL), [p3.SMBUS_CRCCTRL])
    await T.run_program(spi, 0, load("smbus_host", **p3.smbus_defines(SDA, SCL, 7)), clkdiv=4 << 8)
    dev = p3.SmbusDevice(BAT, regs, stretch=20e3)

    async def bus():
        while True:
            await RisingEdge(dut.clk)
            v = int(dut.uio_in.value)
            sc, sd = dev.step(get_sim_time(unit="ps") / 1000, v >> SCL & 1, v >> SDA & 1)
            T.EXT.set(SCL, not sc, 0)
            T.EXT.set(SDA, not sd, 0)
    task = cocotb.start_soon(bus())
    pads = Pads(dut)
    plan = [p3.smbus_words(BAT, [0x09, 0x78, 0x56], pec=True), p3.smbus_words(BAT, [0x09], 2, pec=True),
            p3.smbus_words(BAT, [0x20], 1, pec=True, block=True), p3.smbus_words(0x50, [0x01])]
    words = []
    for w in plan:
        await push_all(spi, 0, w)
        for _ in range(40):
            await wait_ns(dut, 200e3, PERIOD)
            words += await drain(spi, 0)
            if len(p3.smbus_records(words)) == plan.index(w) + 1:
                break
    task.cancel()
    pads.stop()
    recs = p3.smbus_records(words)
    dut._log.info(f"smbus_host: {recs}; the device: "
                  f"{[(t['write'].hex(), t['read'].hex(), t['pec_ok']) for t in dev.transactions]}")
    assert [r["result"] for r in recs] == ["ok", "pec_ok", "pec_ok", "addr_nack"]
    assert recs[1]["read"][:2] == regs[0x09] and recs[2]["read"][:-1] == regs[0x20]
    assert dev.transactions[0]["write"][:3] == bytes([0x09, 0x78, 0x56]) and dev.transactions[0]["pec_ok"]
    if HAVE_SIGROK:
        tr = pads.trace({"scl": ("uio", SCL), "sda": ("uio", SDA)})
        toks = sigrok.i2c(tr, "scl", "sda", res_ps=10_000)
        frames, cur = [], None
        for _, k in toks:
            if k == "S":
                cur = []
                frames.append(cur)
            elif isinstance(k, tuple) and k[0] in ("AW", "AR", "DW", "DR"):
                cur.append((k[1] << 1 | (k[0] == "AR")) if k[0][0] == "A" else k[1])
        dut._log.info(f"sigrok i2c: {[bytes(f).hex() for f in frames]}")
        assert len(frames) == 4
        for f in frames[:3]:
            assert p3.smbus_pec(bytes(f[:-1])) == f[-1]


# ------------------------------------------------------------------ RC-5

@cocotb.test()
async def pf_rc5(dut):
    """rc5_tx (core 0, LED on uo0, CLKDIV 1: 108 kHz ticks) -> the 36 kHz
    receiver-module model -> ui0 -> rc5_rx (core 1, CLKDIV 1.5: 128 ticks
    per bit), the chip at 108 kHz (tick for tick 50 MHz / 462.96 and 694.4):
    a key, its repeat, a new key (toggle), an RC-5X command. sigrok's ir_rc5
    decodes the receiver module's output."""
    PERIOD = 9260.0                     # ns: 108 kHz (107.99), an even number of ps for the clock
    LED, RX = T.UO(0), T.UI(0)
    spi = await T.setup(dut, clock_ns=PERIOD)
    spi.set_low_inputs(1)
    tsop = pf.IrReceiverModule(f_carrier=p3.RC5_F)

    async def ir():
        while True:
            await RisingEdge(dut.clk)
            o = tsop.step(get_sim_time(unit="ps") / 1000, int(dut.uo_out.value) & 1)
            if o != (spi.ui & 1):
                spi.set_low_inputs(o)
    task = cocotb.start_soon(ir())
    await T.run_program(spi, 1, load("rc5_rx", IR=RX, TB=128, FOV=7), in_pins=RX, clkdiv=(1 << 8) | 128)
    await T.run_program(spi, 0, load("rc5_tx", IR=LED), side_pins=LED, clkdiv=1 << 8)
    pads = Pads(dut)
    await wait_ns(dut, 10e6, PERIOD)
    keys = [(0, 12, False), (0, 12, True), (5, 53, False), (20, 70, False)]
    await push_all(spi, 0, [p3.rc5_word(a, c, r) for a, c, r in keys])
    words = []
    for _ in range(10):
        await wait_ns(dut, 50e6, PERIOD)
        words += await drain(spi, 1)
    task.cancel()
    pads.stop()
    recs = [p3.rc5_record(w) for w in words]
    dut._log.info(f"rc5_rx: {recs}")
    assert [(r["address"], r["command"]) for r in recs] == [(a, c) for a, c, _ in keys]
    tg = [r["toggle"] for r in recs]
    assert tg[0] == tg[1] != tg[2] != tg[3]
    rises = [t for t, v in pads.edges("uo", 0) if v]
    per = {round((b - a) / PERIOD) for a, b in zip(rises, rises[1:]) if b - a < 10 * PERIOD}
    assert per == {3}                                            # 36 kHz carrier, 3 ticks
    if HAVE_SIGROK:
        got = sigrok.ir_rc5(pads.trace({"ir": ("ui", 0)}), "ir", res_ps=100_000)
        dut._log.info(f"sigrok ir_rc5: {got}")
        assert [(g[1], g[2], g[3]) for g in got] == [(r["address"], r["command"] & 63, r["toggle"]) for r in recs]
