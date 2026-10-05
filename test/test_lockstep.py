"""Lockstep verification: RTL pe_chip vs the Python golden model (tools/pe/sim.py).

Random programs, random pin stimulus and random host bus traffic are applied to
both; all architectural state is compared every clock cycle.

Env knobs: LOCKSTEP_SEEDS (number of random programs), LOCKSTEP_CYCLES, LOCKSTEP_SEED0.
"""
import bisect
import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge, Timer

from pe import isa, sim
from pe.randprog import random_profile, random_program, random_config_writes, random_bypass_writes

BANK_AW = int(os.environ.get("PE_BANK_AW", "4"))
NCORES = int(os.environ.get("PE_NCORES", "4"))
NBANKS = int(os.environ.get("PE_NBANKS", "4"))
FIFO_DEPTH = 1 << int(os.environ.get("PE_FIFO_LOG2", "2"))
BANK_WORDS = 1 << BANK_AW


def v(sig):
    return int(sig.value)


def reservations(n, real):
    """Host read reservations: their number and, for each, whether it was real."""
    return n, real & ((1 << n) - 1)


def line_state_rtl(u):
    return dict(
        tx_on=v(u.tx_on), level=v(u.level), txn=v(u.txn), half=v(u.half), ending=v(u.ending),
        eop=v(u.eop_idle), tcnt=v(u.tcnt), tfacc=v(u.tfacc), trun=v(u.trun), entries=v(u.e_cnt),
        rx_on=v(u.rx_on), rq=v(u.rq) & ((1 << v(u.rcount)) - 1), rcount=v(u.rcount), rcnt=v(u.rcnt),
        q=v(u.q) - ((v(u.q) >> 20) << 21), n=v(u.mn), quiet=v(u.quiet), rrun=v(u.rrun), end_pend=v(u.end_pend), ovf=v(u.ovf), rprev=v(u.rprev),
    )


def line_state_model(l):
    return dict(
        tx_on=l.tx_on, level=l.level, txn=l.txn, half=l.half, ending=l.ending, eop=l.eop_idle,
        tcnt=l.tcnt, tfacc=l.tfacc, trun=l.trun, entries=len(l.entries),
        rx_on=l.rx_on, rq=sum(b << i for i, b in enumerate(l.rq)), rcount=len(l.rq), rcnt=l.rcnt,
        q=l.q, n=l.n, quiet=l.quiet, rrun=l.rrun, end_pend=l.end_pend, ovf=l.ovf, rprev=l.rprev,
    )


CONFIG = ["div_int", "div_frac", "out_base", "out_count", "set_base", "set_count", "side_base", "side_count", "side_opt", "side_pindir", "in_base", "out_shr", "in_shr", "autopull", "autopush", "fjoin", "pull_thresh", "push_thresh", "entry", "wrap_bottom", "wrap_top", "crc_poly", "crc_w", "in_tap", "out_tap"]


def core_state_rtl(core):
    return dict(
        pc=v(core.pc), r0=v(core.r0), r1=v(core.r1), r2=v(core.r2), r3=v(core.r3),
        isr=v(core.isr), isr_cnt=v(core.isr_cnt), osr=v(core.osr), osr_cnt=v(core.osr_cnt),
        z=v(core.z), c=v(core.c), t=v(core.t), dcnt=v(core.dcnt), in_wait=v(core.in_wait),
        halted=v(core.halted), fault=v(core.fault), stalled=v(core.stalled),
        cnt=v(core.cnt), facc=v(core.facc),
        txl=v(core.u_fifos.txf.cnt), rxl=v(core.u_fifos.rxf.cnt), crc=v(core.crc),
        wprev=v(core.wprev), wevent=v(core.wevent), res=reservations(v(core.res_n), v(core.res_v)),
        # FIFO heads (when occupied): every entry is compared as it reaches the head
        txh=v(core.txf_head) if v(core.u_fifos.txf.cnt) else None,
        rxh=v(core.rxf_head) if v(core.u_fifos.rxf.cnt) else None,
        # configuration (write-only on the bus, so compared here)
        **{k: v(getattr(core, k)) for k in CONFIG},
    )


def core_state_model(m):
    return dict(
        pc=m.pc, r0=m.r[0], r1=m.r[1], r2=m.r[2], r3=m.r[3],
        isr=m.isr, isr_cnt=m.isr_cnt, osr=m.osr, osr_cnt=m.osr_cnt,
        z=m.z, c=m.c, t=m.t, dcnt=m.dcnt, in_wait=m.in_wait,
        halted=m.halted, fault=m.fault, stalled=m.stalled,
        cnt=m.cnt, facc=m.facc, txl=len(m.txf), rxl=len(m.rxf), crc=m.crc,
        wprev=m.wprev, wevent=m.wevent, res=reservations(len(m.res), sum(b << i for i, b in enumerate(m.res))),
        txh=m.txf[0] if m.txf else None, rxh=m.rxf[0] if m.rxf else None,
        **{k: getattr(m, k) for k in CONFIG},
    )


class Harness:
    def __init__(self, dut):
        self.dut = dut
        self.chip = dut.u_chip
        self.cores = [self.chip.g_core[i].g_on.u_core for i in range(NCORES)]
        self.model = sim.Chip(bank_words=BANK_WORDS, ncores=NCORES, nbanks=NBANKS, fifo_depth=FIFO_DEPTH)
        self.cycle = 0
        self.stats = {}
        self.trace = []  # recent (cycle, description) for failure reports

    async def reset(self):
        d = self.dut
        self.mid, self.next_mid = (0, 0), None
        d.rst_n.value = 0
        d.ui_in.value = 0
        d.uio_in.value = 0
        d.bus_addr.value = 0
        d.bus_we.value = 0
        d.bus_wdata.value = 0
        d.bus_commit.value = 0
        d.bus_snap.value = 0
        d.bus_cancel.value = 0
        for _ in range(3):
            await RisingEdge(d.clk)
        await FallingEdge(d.clk)
        d.rst_n.value = 1
        self.model = sim.Chip(bank_words=BANK_WORDS, ncores=NCORES, nbanks=NBANKS, fifo_depth=FIFO_DEPTH)

    def compare(self):
        errs = []
        m = self.model
        for i, (rc, mc) in enumerate(zip(self.cores, m.cores[:NCORES])):
            a, b = core_state_rtl(rc), core_state_model(mc)
            for k in a:
                if a[k] != b[k]:
                    errs.append(f"core{i}.{k}: rtl={a[k]:#x} model={b[k]:#x}")
        u = self.chip.u_line
        for name, rv, mv in (("pin_out", v(self.chip.pin_out), m.pin_out),
                             ("pin_oe", v(self.chip.pin_oe), m.pin_oe),
                             ("flags", v(self.chip.flags), m.flags),
                             # configuration (write-only on the bus, so compared here)
                             ("pin_owner", v(self.chip.pin_owner_lo) | v(self.chip.pin_owner_hi) << 32,
                              sum(o << 2 * i for i, o in enumerate(m.pin_owner))),
                             ("od_mask", v(self.chip.od_mask), m.od_mask),
                             ("sync_byp", v(self.chip.sync_byp), m.sync_bypass),
                             ("hostcfg", v(self.chip.hostcfg), m.hostcfg),
                             ("line.cfg", v(u.cfg), m.line.cfg), ("line.div", v(u.div), m.line.div),
                             ("line.txp", v(u.txp), m.line.txp), ("line.rxp", v(u.rxp), m.line.rxp)):
            if rv != mv:
                errs.append(f"{name}: rtl={rv:#x} model={mv:#x}")
        a, b = line_state_rtl(self.chip.u_line), line_state_model(m.line)
        for k in a:
            if a[k] != b[k]:
                errs.append(f"line.{k}: rtl={a[k]:#x} model={b[k]:#x}")
        uo, uo_io, uo_oe = m.outputs()
        for name, rv, mv in (("uo_out", v(self.dut.uo_out), uo), ("uio_out", v(self.dut.uio_out), uo_io),
                             ("uio_oe", v(self.dut.uio_oe), uo_oe)):
            if rv != mv:
                errs.append(f"{name}: rtl={rv:#x} model={mv:#x}")
        return errs

    def fail(self, errs, what):
        lines = [f"MISMATCH at cycle {self.cycle} ({what}):"] + errs
        lines.append("recent trace:")
        lines += [f"  {c}: {d}" for c, d in self.trace[-12:]]
        for i, mc in enumerate(self.model.cores):
            lines.append(f"  core{i} pc={mc.pc} instr={isa.disasm(self.model.fetch(mc.pc))}")
        raise AssertionError("\n".join(lines))

    async def cycle_step(self, ui=0, uio=0, we=False, addr=0, wdata=0, commit=False, snap=False, cancel=False):
        """Apply one cycle of inputs to both RTL and model, then compare."""
        d = self.dut
        d.ui_in.value = ui
        d.uio_in.value = uio
        d.bus_addr.value = addr
        d.bus_we.value = int(we)
        d.bus_wdata.value = wdata
        d.bus_commit.value = int(commit)
        d.bus_snap.value = int(snap)
        d.bus_cancel.value = int(cancel)
        await Timer(1, unit="ps")
        rd_rtl, rd_model = v(d.bus_rdata), self.model.read(addr)
        if rd_rtl != rd_model:
            self.fail([f"bus_rdata[{addr:#06x}]: rtl={rd_rtl:#x} model={rd_model:#x}"], "bus read")
        if we or commit:
            self.trace.append((self.cycle, f"{'W' if we else 'R'} {addr:#06x} {wdata:#010x}"))
        await RisingEdge(d.clk)
        self.model.step(ui_in=ui, uio_in=uio, we=we, addr=addr, wdata=wdata, commit=commit, snap=snap, cancel=cancel,
                        ui_mid=self.mid[0], uio_mid=self.mid[1])
        self.cycle += 1
        # the pads at the next falling edge (pe_chip samples the line unit's RX
        # pin there): unchanged, or some already at next cycle's value
        self.mid = self.next_mid or (ui, uio)
        self.next_mid = None
        await Timer(1, unit="ps")
        d.ui_in.value, d.uio_in.value = self.mid
        await FallingEdge(d.clk)
        errs = self.compare()
        if errs:
            self.fail(errs, "state")

    async def write(self, addr, data):
        await self.cycle_step(we=True, addr=addr, wdata=data)


async def start(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    h = Harness(dut)
    await h.reset()
    return h


@cocotb.test()
async def test_registers(dut):
    """Directed: ID, reset values, config readback."""
    h = await start(dut)
    await h.cycle_step(addr=sim.A_ID)
    assert v(dut.bus_rdata) == sim.ID_VALUE
    for c in range(4):
        for off in range(0x20):
            await h.cycle_step(addr=sim.core_reg(c, off))
    rng = random.Random(1)
    for c in range(4):
        for off in (0, 1, 2, 3, 4, 5, 6, 7, 0xA, 0xB, 0xC, 0xD):
            await h.write(sim.core_reg(c, off), rng.getrandbits(32))
            await h.cycle_step(addr=sim.core_reg(c, off))
    for a in (sim.A_SYNCBYP, sim.A_POWN_LO, sim.A_POWN_HI, sim.A_OD, sim.A_FSET, sim.A_FCLR):
        await h.write(a, rng.getrandbits(32))
        await h.cycle_step(addr=a)


@cocotb.test()
async def test_random_lockstep(dut):
    """Random programs + random stimulus, compared every cycle."""
    nseeds = int(os.environ.get("LOCKSTEP_SEEDS", "20"))
    ncycles = int(os.environ.get("LOCKSTEP_CYCLES", "600"))
    seed0 = int(os.environ.get("LOCKSTEP_SEED0", "1000"))
    h = await start(dut)
    for seed in range(seed0, seed0 + nseeds):
        rng = random.Random(seed)
        h.trace.append((h.cycle, f"--- seed {seed}"))
        dut._log.info(f"seed {seed}")
        for k, n in h.model.stats.items():
            h.stats[k] = h.stats.get(k, 0) + n
        await h.reset()
        # programs + config, all while cores are disabled (bank b is core b's)
        for b in range(4):
            profile = random_profile(rng)
            for w in range(BANK_WORDS):
                word = random_program(rng, b, BANK_WORDS, profile)
                await h.write((b << 8) | w, word)
        for addr, data in random_config_writes(rng, BANK_WORDS):
            await h.write(addr, data)
        for addr, data in random_line_writes(rng):
            await h.write(addr, data)
        for addr, data in random_bypass_writes(random.Random(seed + 0xB1A5)):
            await h.write(addr, data)
        await h.write(sim.A_CTRL, 15 if rng.random() < 0.7 else rng.randrange(1, 16))
        # run
        mrng = random.Random(seed + 0x5EED)            # own stream: the rest of the stimulus is unchanged
        nui = rng.getrandbits(5)
        nuio = rng.getrandbits(8)
        toggle_p = rng.choice([0.02, 0.1, 0.4])
        push_p = 0.06 if rng.random() < 0.7 else 0.3   # some seeds keep the TX FIFOs busy
        for _ in range(ncycles):
            ui, uio = nui, nuio
            for bit in range(5):
                if rng.random() < toggle_p:
                    nui ^= 1 << bit
            for bit in range(8):
                if rng.random() < toggle_p:
                    nuio ^= 1 << bit
            # at the falling edge before the next rising edge, each pin that is
            # about to change already shows its new value half the time
            early_ui, early_uio = mrng.getrandbits(5), mrng.getrandbits(8)
            h.next_mid = ((ui & ~early_ui) | (nui & early_ui), (uio & ~early_uio) | (nuio & early_uio))
            op = rng.random()
            c = rng.randrange(4)
            if op < push_p:
                op = 0.0
            else:
                op = 0.06 + (op - push_p) / (1 - push_p) * 0.94
            if op < 0.06:
                await h.cycle_step(ui, uio, we=True, addr=sim.core_reg(c, sim.C_FIFO), wdata=rng.getrandbits(32))
            elif op < 0.12:
                # FIFO reads: the host port snapshots words ahead, commits each once
                # it is sent, and cancels the rest at the end of a transaction;
                # random snaps/commits/cancels (rarely together) cover every interleaving
                k = rng.random()
                await h.cycle_step(ui, uio, snap=k < 0.45 or k >= 0.9, commit=0.45 <= k < 0.85 or k >= 0.9,
                                   cancel=0.85 <= k < 0.9, addr=sim.core_reg(c, sim.C_FIFO))
            elif op < 0.14:
                await h.cycle_step(ui, uio, we=True, addr=rng.choice([sim.A_FSET, sim.A_FCLR]),
                                   wdata=rng.getrandbits(8))
            elif op < 0.145:
                await h.cycle_step(ui, uio, we=True, addr=sim.core_reg(c, rng.choice([8, 0xA, 0xB, 0xC, 0xD])),
                                   wdata=rng.getrandbits(16) if rng.random() < 0.5 else (c << 8) | rng.randrange(BANK_WORDS))
            elif op < 0.148:
                ctrl = rng.getrandbits(4) | (rng.getrandbits(4) << 8 if rng.random() < 0.5 else 0) \
                    | (rng.getrandbits(4) << 16 if rng.random() < 0.3 else 0)
                await h.cycle_step(ui, uio, we=True, addr=sim.A_CTRL, wdata=ctrl)
            elif op < 0.152:
                await h.cycle_step(ui, uio, we=True, addr=sim.core_reg(c, rng.choice([0, 1, 2, 3, 4, 5, 6, 7])),
                                   wdata=rng.getrandbits(32))
            elif op < 0.155:
                await h.cycle_step(ui, uio, we=True, addr=rng.choice([sim.A_POWN_LO, sim.A_POWN_HI, sim.A_OD, sim.A_HOSTCFG,
                                                                   sim.A_SYNCBYP,
                                                                   sim.A_LINECFG,
                                                                   sim.A_POUT, sim.A_POE]),
                                   wdata=rng.getrandbits(32))
            else:
                # idle cycle, with a random (side-effect free) read address
                addr = rng.choice([sim.core_reg(c, rng.randrange(0x20)), 0x800 + rng.randrange(16)])
                await h.cycle_step(ui, uio, addr=addr)
    for k, n in h.model.stats.items():
        h.stats[k] = h.stats.get(k, 0) + n
    dut._log.info(f"{nseeds} seeds x {ncycles} cycles OK ({h.cycle} total cycles)")
    dut._log.info("coverage: " + ", ".join(f"{k}={n}" for k, n in sorted(h.stats.items())))
    # LOCKSTEP_GOALS=core skips the line-unit goals: a build with 16-word banks
    # rarely gives core 0 a line program; the tapeout-config run requires them
    goals = coverage_goals()
    if os.environ.get("LOCKSTEP_GOALS") == "core":
        goals = [g for g in goals if not g.startswith("line:")]
    missing = [g for g in goals if not h.stats.get(g)]
    assert not missing, f"coverage goals not reached (run longer or fix the generator): {missing}"


def random_line_writes(rng):
    """The line unit: sane timing most of the time, pins among the stimulated and
    internal ones (so its TX can loop back to its RX), enabled half the time."""
    live = [*range(8), *range(16, 21), 24, 25, 26]
    txp, rxp = rng.choice(live), rng.choice(live)
    if rng.random() < 0.5:
        rxp = txp                                   # hear its own transmission
    tx_int = rng.randrange(2, 7) if rng.random() < 0.9 else rng.randrange(256)
    rx_p = rng.randrange(4, 17) if rng.random() < 0.9 else rng.getrandbits(16)
    cfg = (rng.getrandbits(15) & ~0b110) | (rng.randrange(3) << 1)    # NRZ / NRZI / Manchester
    if rng.random() < 0.5:
        cfg |= 1 | (1 << 14)                        # EN and RX_EN
    return [(sim.A_LINEDIV, (rx_p << 16) | (tx_int << 8) | rng.getrandbits(8)),
            (sim.A_LINEPINS, txp | (rxp << 8)),
            (sim.A_LINECFG, cfg)]


def coverage_goals():
    """Events every lockstep run must exercise: each opcode completing, the
    blocking ones also stalling, and the spec's corner cases (tools/pe/sim.py
    counts them with Chip.cover)."""
    ops = ["jmp", "wait", "in", "out", "push", "pull", "mov", "set", "ldi", "alu", "pin", "flag"]
    return ([f"done:{op}" for op in ops] + ["stall:wait", "stall:push", "stall:pull", "stall:out", "jump"] + [
        "wait:timeout", "wait:edge_caught_between_ticks", "wait:qualifier_blocked_edge",
        "wait:diff_first_attempt", "wait:diff_done", "wait:diff_timeout",   # between ticks: test_wait_diff
        "shift:autopush", "shift:autopull",
        "pins:side_set_over_out", "pins:write_to_unowned_pin", "pins:sync_bypass_fresher",
        "fifo:host_push_to_full_dropped", "fifo:pop_suppressed_by_snapshot",   # push+pop: test_fifo_races
        "fifo:clear_nonempty", "fifo:join_move",
        "ctrl:restart_mid_instruction", "core:bank_fault",
        "line:out_entry", "line:tx_bit", "line:tx_stuff", "line:tx_manchester_half", "line:tx_frame_end",
        "line:rx_bit", "line:rx_stuff", "line:rx_frame_end", "line:in_bits", "line:in_frame_end",
        "line:rx_overflow", "line:rx_edge_first_half", "line:rx_edge_second_half",
        "line:rx_edge_outside_window", "line:rx_bypassed_pin",                                   # rx_locked: test_line_rx_jitter
    ])


@cocotb.test()
async def test_fifo_races(dut):
    """Directed, still compared every cycle: a core loops pull/push while the
    host pushes and reads at full rate, so host pushes meet core pops in the
    same cycle, and reads race the core's pushes (with and without FIFO join)."""
    from pe import asm
    h = await start(dut)
    prog = asm.assemble("""
    loop:
        pull block
        mov isr, osr
        push block
        jmp loop
    """)
    for join in (0, 1):
        h.model.stats.clear()
        for i, w in enumerate(prog.words):
            await h.write(i, w)
        await h.write(sim.core_reg(0, sim.C_SHIFTCTRL), join << 4)
        await h.write(sim.core_reg(0, sim.C_ENTRY), 0)
        await h.write(sim.A_CTRL, 1 | (1 << 8) | (1 << 16))   # restart + clear FIFOs core 0
        rng = random.Random(join)
        fifo = sim.core_reg(0, sim.C_FIFO)
        for cyc in range(3000):
            k = rng.random()
            draining = (cyc // 150) % 2          # alternate: push-heavy, then reads only
            if k < 0.5 and not draining:
                await h.cycle_step(we=True, addr=fifo, wdata=rng.getrandbits(32))
            elif k < 0.8:
                await h.cycle_step(snap=rng.random() < 0.5, commit=rng.random() < 0.5, addr=fifo)
            else:
                await h.cycle_step(addr=sim.core_reg(0, sim.C_STATUS))
        want = ["fifo:host_push_to_full_dropped"] + (["fifo:join_move"] if join else
                                                       ["fifo:host_push_and_core_pop", "fifo:pop_suppressed_by_snapshot"])
        missing = [e for e in want if not h.model.stats.get(e)]
        assert not missing, (join, missing)
        await h.write(sim.A_CTRL, 0)


@cocotb.test()
async def test_line_rx_jitter(dut):
    """Directed, compared every cycle: a 10BASE-T frame with +-13.5 ns of edge
    jitter on ui0, sampled at both clock edges (the falling-edge sample planned
    a cycle ahead), received by eth10_rx through the line unit while the host
    reads the FIFO. Reaches the receiver's locked state and both edge halves."""
    from pe import asm, host
    import pathlib
    h = await start(dut)
    prog = asm.assemble((pathlib.Path(__file__).resolve().parents[1] / "firmware/eth10_rx.pasm").read_text())
    for addr, data in host.load_program(0, prog, 0, clkdiv=1 << 8):
        await h.write(addr, data)
    await h.write(sim.A_LINEDIV, 5 << 16)                      # 5 clocks per bit: 50 MHz
    await h.write(sim.A_LINEPINS, 16 << 8)                     # RX on P16 = ui0
    await h.write(sim.A_LINECFG, 1 | (2 << 1) | (1 << 14))     # EN, Manchester, RX_EN
    await h.write(sim.A_CTRL, 1)
    rng = random.Random(5)
    bits = [1, 0] * 31 + [1, 1] + [rng.getrandbits(1) for _ in range(400)]
    clk, half = 20.0, 50.0 * (1 - 150e-6)
    t, edges, prev = 300.0, [], 0
    for lv in (v for b in bits for v in (1 - b, b)):
        if lv != prev:
            j = rng.choice([-13.5, 13.5]) if rng.random() < 0.5 else rng.uniform(-13.5, 13.5)
            edges.append((t + j, lv))
        prev = lv
        t += half
    edges.append((t + 200.0, 0))                                # TP_IDL, then idle low

    times = [te for te, _ in edges]

    def level(time):
        i = bisect.bisect_right(times, time)
        return edges[i - 1][1] if i else 0
    fifo = sim.core_reg(0, sim.C_FIFO)
    c0 = h.model.cycle
    for _ in range(int((t + 1000) / clk)):
        c = h.model.cycle - c0
        h.next_mid = (level((c + 0.5) * clk), 0)               # the pad at the next falling edge
        if rng.random() < 0.3:
            await h.cycle_step(level(c * clk), 0, addr=fifo, snap=rng.random() < 0.6, commit=rng.random() < 0.6)
        else:
            await h.cycle_step(level(c * clk), 0)
    missing = [g for g in ("line:rx_locked", "line:rx_edge_first_half", "line:rx_edge_second_half",
                           "line:rx_frame_end") if not h.model.stats.get(g)]
    assert not missing, missing


@cocotb.test()
async def test_wait_diff(dut):
    """Directed, compared every cycle: WAIT kind 7 (`wait pins != rN mask m`)
    on slow and fractional dividers while pins pulse for single clocks, with
    host PC writes and restarts landing mid-WAIT and timeouts expiring. Reaches
    a difference caught between ticks (a pulse that is gone by the tick)."""
    from pe import asm, host
    h = await start(dut)
    progs = [asm.assemble("""
    .shift_in left
    loop:
        mov r0, pins
        ldi r2, 30
        wait pins != r0 mask 0x0F timeout r2
        in r2, 16
        push noblock
        jmp loop
    """), asm.assemble("""
    loop:
        ldi r1, 0x5
        wait pins != r1 mask 0x05 timeout r3
        jmp t, loop
        wait pins != r1 mask 0x05
        mov r3, pins
        jmp loop
    """)]
    for c, (prog, div) in enumerate(zip(progs, (3 << 8 | 0x80, 2 << 8))):
        for addr, data in host.load_program(c, prog, c * 256, in_pins=0, clkdiv=div):
            await h.write(addr, data)
    await h.write(sim.core_reg(1, sim.C_R0 + 3), 25)
    await h.write(sim.A_CTRL, 3 | (3 << 8))
    rng = random.Random(7)
    uio = 0
    for cyc in range(6000):
        if rng.random() < 0.15:
            uio ^= 1 << rng.randrange(4)               # pulses: often for a single clock
        k = rng.random()
        if k < 0.003:
            await h.cycle_step(0, uio, we=True, addr=sim.core_reg(rng.randrange(2), sim.C_PC), wdata=0)
            uio = 0
        elif k < 0.006:
            await h.cycle_step(0, uio, we=True, addr=sim.A_CTRL, wdata=3 | (rng.randrange(1, 4) << 8))
        elif k < 0.01:
            c = rng.randrange(2)
            await h.cycle_step(0, uio, we=True, addr=sim.core_reg(c, sim.C_CLKDIV),
                               wdata=(rng.randrange(1, 4) << 8) | rng.getrandbits(8))
        else:
            await h.cycle_step(0, uio, addr=sim.core_reg(0, sim.C_FIFO), snap=k < 0.3, commit=k < 0.3)
            if rng.random() < 0.5:
                uio &= ~(1 << rng.randrange(4))
    want = ["wait:diff_first_attempt", "wait:diff_done", "wait:diff_caught_between_ticks", "wait:diff_timeout",
            "ctrl:restart_mid_instruction"]
    missing = [g for g in want if not h.model.stats.get(g)]
    assert not missing, (missing, {k: v for k, v in h.model.stats.items() if k.startswith("wait")})
