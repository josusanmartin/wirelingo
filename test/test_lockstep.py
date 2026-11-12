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
from pe.randprog import random_profile, random_program, random_config_writes, random_bypass_writes, random_v03_writes

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
        runbits=(v(u.trunbit), v(u.trunv), v(u.rrunbit), v(u.rrunv)),
    )


def line_state_model(l):
    return dict(
        tx_on=l.tx_on, level=l.level, txn=l.txn, half=l.half, ending=l.ending, eop=l.eop_idle,
        tcnt=l.tcnt, tfacc=l.tfacc, trun=l.trun, entries=len(l.entries),
        rx_on=l.rx_on, rq=sum(b << i for i, b in enumerate(l.rq)), rcount=len(l.rq), rcnt=l.rcnt,
        q=l.q, n=l.n, quiet=l.quiet, rrun=l.rrun, end_pend=l.end_pend, ovf=l.ovf, rprev=l.rprev,
        runbits=(l.trunbit, l.trunv, l.rrunbit, l.rrunv),
    )


CONFIG = ["div_int", "div_frac", "out_base", "out_count", "set_base", "set_count", "side_base", "side_count", "side_opt", "side_pindir", "in_base", "out_shr", "in_shr", "autopull", "autopush", "fjoin", "pull_thresh", "push_thresh", "entry", "wrap_bottom", "wrap_top", "crc_poly", "crc_poly_hi", "crc_w", "in_tap", "out_tap"]


def core_state_rtl(core):
    return dict(
        pc=v(core.pc), r0=v(core.r0), r1=v(core.r1), r2=v(core.r2), r3=v(core.r3),
        isr=v(core.isr), isr_cnt=v(core.isr_cnt), osr=v(core.osr), osr_cnt=v(core.osr_cnt),
        z=v(core.z), c=v(core.c), t=v(core.t), dcnt=v(core.dcnt), in_wait=v(core.in_wait),
        halted=v(core.halted), fault=v(core.fault), stalled=v(core.stalled),
        cnt=v(core.cnt), facc=v(core.facc), lr=v(core.lr),
        txl=v(core.u_fifos.txf.cnt), rxl=v(core.u_fifos.rxf.cnt), crc=v(core.crc_eff),   # the architectural CRC (pe_core.sv)
        ticks=v(core.tcnt), tbl=v(core.tbl_pend),
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
        cnt=m.cnt, facc=m.facc, lr=m.lr, txl=len(m.txf), rxl=len(m.rxf), crc=m.crc,
        ticks=m.ticks, tbl=int(m.tbl is not None),
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
    assert v(dut.bus_rdata) == sim.id_value(NCORES, NBANKS)
    for c in range(4):
        for off in range(0x20):
            await h.cycle_step(addr=sim.core_reg(c, off))
    rng = random.Random(1)
    for c in range(4):
        for off in (0, 1, 2, 3, 4, 5, 6, 7, 0xA, 0xB, 0xC, 0xD, 0x11, 6, 7):
            await h.write(sim.core_reg(c, off), rng.getrandbits(32))
            await h.cycle_step(addr=sim.core_reg(c, off))
    for a in (sim.A_SYNCBYP, sim.A_POWN_LO, sim.A_POWN_HI, sim.A_OD, sim.A_FSET, sim.A_FCLR):
        await h.write(a, rng.getrandbits(32))
        await h.cycle_step(addr=a)


@cocotb.test()
async def test_random_lockstep(dut):
    """Random programs + random stimulus, compared every cycle."""
    nseeds = int(os.environ.get("LOCKSTEP_SEEDS", "40"))
    ncycles = int(os.environ.get("LOCKSTEP_CYCLES", "1000"))
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
        v03 = random.Random(seed + 0x3C30)                # the v0.3 forms: their own stream
        for b in range(4):
            profile = random_profile(rng)
            for w in range(BANK_WORDS):
                word = random_program(rng, b, BANK_WORDS, profile, v03)
                await h.write((b << 8) | w, word)
        for addr, data in random_config_writes(rng, BANK_WORDS):
            await h.write(addr, data)
        for addr, data in random_line_writes(rng):
            await h.write(addr, data)
        for addr, data in random_bypass_writes(random.Random(seed + 0xB1A5)):
            await h.write(addr, data)
        for addr, data in random_v03_writes(random.Random(seed + 0x3C3)):
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
    ops = ["jmp", "wait", "in", "out", "push", "pull", "mov", "set", "ldi", "alu", "pin", "flag", "call"]
    return ([f"done:{op}" for op in ops] + ["stall:wait", "stall:push", "stall:pull", "stall:out", "jump"] + [
        "wait:timeout", "wait:edge_caught_between_ticks", "wait:qualifier_blocked_edge",
        "wait:diff_first_attempt", "wait:diff_done", "wait:diff_timeout",   # between ticks: test_wait_diff
        "shift:autopush", "shift:autopull", "call:taken", "call:ret", "call:mov_to_lr",
        "pins:side_set_over_out", "pins:write_to_unowned_pin", "pins:sync_bypass_fresher",
        "fifo:host_push_to_full_dropped", "fifo:pop_suppressed_by_snapshot",   # push+pop: test_fifo_races
        "fifo:clear_nonempty", "fifo:join_move",
        "ctrl:restart_mid_instruction", "core:bank_fault",
        # v0.3 (docs/isa.md §5.2); the table read's rare corners: test_v03_corners
        "table:read", "table:extra_tick", "cmpw:lt", "cmpw:gt",           # cmpw:eq: test_v03_corners
        "jmp:not_t_taken", "jmp:not_t_fallthrough", "flag:tog_set", "flag:tog_clear", "ticks:read",
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
            c = rng.randrange(2)                       # back to its loop (its own bank)
            await h.cycle_step(0, uio, we=True, addr=sim.core_reg(c, sim.C_PC), wdata=c * 256)
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


@cocotb.test()
async def test_call_ret(dut):
    """Directed, compared every cycle: CALL / RET with conditional calls,
    nesting through a saved LR, a CALL on the wrap's last word, side-set and
    delays, on a fractional divider, while the host restarts the cores and
    writes their PCs at random."""
    from pe import asm, host
    h = await start(dut)
    progs = [asm.assemble("""
    .side_set 1 opt
        ldi r0, 3
    .wrap_target
    top:
        call !r1, sub [1]
        call r0--, outer side 1
        jmp pin 0, skip
        call sub
    skip:
        call sub side 0
    .wrap
    outer:
        mov r3, lr
        call sub [2]
        mov lr, r3
        ret
    sub:
        add r1, 1
        and r1, 3
        ret side 1 [1]
    """), asm.assemble("""
    loop:
        call f
        mov r2, lr
        jmp loop
    f:
        add r3, 1
        ret
    """)]
    for c, (prog, div) in enumerate(zip(progs, (2 << 8 | 0x40, 1 << 8))):
        for addr, data in host.load_program(c, prog, c * 256, side_pins=8, clkdiv=div):
            await h.write(addr, data)
    await h.write(sim.A_CTRL, 3 | (3 << 8))
    rng = random.Random(11)
    for cyc in range(5000):
        k = rng.random()
        uio = rng.getrandbits(1)
        if k < 0.004:
            c = rng.randrange(2)                       # back to its program start (its own bank)
            await h.cycle_step(0, uio, we=True, addr=sim.core_reg(c, sim.C_PC), wdata=c * 256)
        elif k < 0.008:
            await h.cycle_step(0, uio, we=True, addr=sim.A_CTRL, wdata=3 | (rng.randrange(1, 4) << 8))
        else:
            await h.cycle_step(0, uio)
    want = ["done:call", "call:taken", "call:ret", "call:mov_to_lr", "call:at_wrap_top",
            "ctrl:restart_mid_instruction"]
    missing = [g for g in want if not h.model.stats.get(g)]
    assert not missing, (missing, {k: v for k, v in h.model.stats.items() if "call" in k})


@cocotb.test()
async def test_v03_corners(dut):
    """Directed, compared every cycle: the v0.3 additions (docs/isa.md §5.2) on every
    core with a bank, table reads at CLKDIV 1, 1.25 and 2, while the host restarts
    cores, writes their PCs and disables them at random, so each lands in a table
    read's clock: restart wins, a PC write in the MOV's clock cancels it, the word
    lands in a disabled clock. Also CMPW, `jmp/call !t`, `flag tog`, the ticks, a
    bank-relative computed jump, and CRCs of 32 and 21 bits with POLY_HI."""
    from pe import asm, host
    from pe.profile import Profile
    h = await start(dut)
    prof = Profile("lockstep", NCORES, NBANKS, BANK_WORDS)
    progs = [asm.assemble("""
    top:
        ldi r1, t
        mov osr, [r1]
        mov r0, osr
        cmpw r0, 0xBEEF
        jmp !z, top
        mov osr, [r1] [2]
        mov r2, ticks
        in ticks, 16
        push noblock
        flag tog 2
        ldi r3, 2
        wait 1 flag 3 timeout r3
        jmp !t, top
        call !t, top
        jmp top
    t:  .data 0xBEEF
    """), asm.assemble("""
    loop:
        mov osr, [r0]
        mov r1, osr
        cmp r1, 0x4000
        in r1, 1
        mov r0, ticks
        and r0, 7
        flag tog 3
        ldi r2, loop
        mov pc, r2
    """)]
    divs = [1 << 8, (1 << 8) | 0x40, 2 << 8, 1 << 8]
    cores = list(range(min(NCORES, NBANKS)))
    for b in range(NBANKS):                       # the SRAM keeps the last test's words:
        for w in range(BANK_WORDS):               # a table read beyond the program must
            await h.write((b << 8) | w, 0)        # see the model's (r0 is kept on restart)
    for c in cores:
        for addr, data in host.load_program(c, progs[min(c, 1)], c * 256, profile=prof, clkdiv=divs[c]):
            await h.write(addr, data)
    for c in cores:
        ctrl, hi = isa.crcctrl(*((0x04C11DB7, 32) if c % 2 else (0x102899, 21)), in_tap=1)
        for off, val in ((sim.C_CRCCTRL, ctrl), (sim.C_POLY_HI, hi), (sim.C_CRC, 0xFFFFFFFF)):
            await h.write(sim.core_reg(c, off), val)
    await h.write(sim.core_reg(0, sim.C_CRCCTRL), 0)          # back to a v0.2 width: 16 bits kept
    await h.write(sim.core_reg(0, sim.C_CRC), 0xFFFFFFFF)
    mask = sum(1 << c for c in cores)
    await h.write(sim.A_CTRL, mask | (mask << 8))
    rng = random.Random(33)
    enabled = mask
    for _ in range(10000):
        k = rng.random()
        c = rng.choice(cores)
        if k < 0.015:
            await h.cycle_step(we=True, addr=sim.core_reg(c, sim.C_PC), wdata=c * 256)
        elif k < 0.03:
            await h.cycle_step(we=True, addr=sim.A_CTRL, wdata=enabled | (1 << (8 + c)))
        elif k < 0.05:
            enabled ^= 1 << c
            if not enabled & (1 << c) and rng.random() < 0.3:
                enabled |= 1 << c
            await h.cycle_step(we=True, addr=sim.A_CTRL, wdata=enabled)
        elif k < 0.053:
            await h.cycle_step(we=True, addr=rng.choice([sim.A_FSET, sim.A_FCLR]), wdata=rng.getrandbits(8))
        else:
            await h.cycle_step(addr=sim.core_reg(c, rng.choice([sim.C_FIFO, sim.C_OSR, sim.C_PC])),
                               snap=k < 0.2, commit=k < 0.2)
    want = ["table:read", "table:extra_tick", "table:restart_wins", "table:cancelled_by_host_pc",
            "table:write_while_disabled", "ticks:read", "cmpw:eq", "cmpw:lt", "cmpw:gt",
            "jmp:not_t_taken", "jmp:not_t_fallthrough", "flag:tog_set", "flag:tog_clear"]
    missing = [g for g in want if not h.model.stats.get(g)]
    assert not missing, (missing, {k: v for k, v in h.model.stats.items() if k.split(":")[0] in
                                   ("table", "ticks", "cmpw", "jmp", "flag")})


@cocotb.test()
async def test_crc_retime(dut):
    """Directed, compared every cycle: the retimed CRC step (src/pe_core.sv
    crc_eff) is invisible. Feeds at CLKDIV 1 meet, in the clock after a feed:
    another feed, an IN/MOV read of CRC, a set, a host read and a host write of
    CRC, CRCCTRL and POLY_HI writes, disable, restart, FIFO clear and reset;
    and in the feed's own clock: a set (OUT crc, 1), a host CRC write and a
    CRCCTRL or POLY_HI write. Widths 1..32 with POLY_HI, and the 16-bit host
    write of the v0.2 widths. Coverage is read from the model, so the
    Hardcaml port's run (ocaml/scripts/run_cocotb.sh) needs no extra wires."""
    from pe import asm, host
    from pe.isa import CRC
    from pe.profile import Profile
    h = await start(dut)
    prof = Profile("lockstep", NCORES, NBANKS, BANK_WORDS)
    progs = [asm.assemble("""
    .shift_in left
    top:
        in pins, 1                ; feed
        in pins, 1                ; a feed after a feed
        mov r0, crc               ; a read in the clock after a feed
        in crc, 1                 ; a read and a feed (CRC bit 0)
        mov crc, r0               ; a set after a feed
        in pins, 1                ; a feed after a set
        in pins, 1
        mov isr, crc
        push noblock
        in pins, 1
        mov crc, ~null
        in osr, 1
        jmp top
    """), asm.assemble("""
    top:
        pull noblock
        out r2, 1                 ; feed (OUT_TAP)
        out r2, 1
        out crc, 1                ; a set and a feed in one clock: the set wins
        out r3, 1
        mov r1, crc
        out r3, 1
        out crc, 16
        out r2, 1
        mov osr, crc
        out r3, 1
        jmp top
    """)]
    cores = list(range(min(NCORES, NBANKS)))
    taps = [(1, 0), (0, 1), (1, 1), (0, 1)]       # (IN_TAP, OUT_TAP) per core
    divs = [1 << 8, 1 << 8, (1 << 8) | 0x40, 2 << 8]
    rng = random.Random(0xC3C)

    # what the model does in each clock, per core (the class is patched, so the
    # Chip that h.reset() builds is covered too)
    ev = []
    orig = (sim.Core._crc_feed, sim.Core._write_dst, sim.Core._src)

    def feed(core, b):
        ev.append((core.idx, "feed"))
        return orig[0](core, b)

    def write_dst(core, e, dst, v, isr_count):
        if dst == CRC:
            ev.append((core.idx, "set"))
        return orig[1](core, e, dst, v, isr_count)

    def src(core, e, code):
        if code == CRC:
            ev.append((core.idx, "read"))
        return orig[2](core, e, code)
    sim.Core._crc_feed, sim.Core._write_dst, sim.Core._src = feed, write_dst, src
    seen = {}
    widths = set()

    def cov(k):
        seen[k] = seen.get(k, 0) + 1

    def crcctrl(c):
        width = rng.choice([1, 5, 8, 15, 16, 17, 21, 31, 32]) if rng.random() < 0.5 else rng.randrange(1, 33)
        ti, to = taps[c] if rng.random() < 0.9 else (rng.getrandbits(1), rng.getrandbits(1))
        return isa.crcctrl(rng.getrandbits(width), width, in_tap=ti, out_tap=to)

    try:
        prev = {c: set() for c in cores}               # last clock's events
        pend = {c: False for c in cores}               # a feed last clock that nothing overrode
        for rnd in range(4):
            await h.reset()
            prev = {c: set() for c in cores}
            pend = {c: False for c in cores}
            for c in cores:
                for addr, data in host.load_program(c, progs[c % 2], c * 256, profile=prof, clkdiv=divs[c]):
                    await h.write(addr, data)
                ctrl, hi = crcctrl(c)
                for off, val in ((sim.C_CRCCTRL, ctrl), (sim.C_POLY_HI, hi), (sim.C_CRC, rng.getrandbits(32))):
                    await h.write(sim.core_reg(c, off), val)
            mask = sum(1 << c for c in cores)
            enabled = mask
            await h.write(sim.A_CTRL, mask | (mask << 8))
            for cyc in range(6000 + (1 << 20 if rnd == 3 else 0)):
                c = rng.choice(cores)
                m = h.model
                # this clock's core inputs: last clock's CTRL write (§6.2), and
                # the settings a feed in this clock uses
                ctrl_now = m.ctrl_pend
                restart = (ctrl_now >> 8) & 15 if ctrl_now is not None else 0
                fclear = (ctrl_now >> 16) & 15 if ctrl_now is not None else 0
                en_now = m.enable
                cfg = {cc: (m.cores[cc].crc_w, m.cores[cc].crc_poly_hi) for cc in cores}
                k = rng.random()
                kw = dict(addr=sim.core_reg(c, sim.C_CRC))          # default: the host reads CRC
                act = "read"
                if k < 0.04:
                    act = "w_crc"
                    kw = dict(we=True, addr=sim.core_reg(c, sim.C_CRC), wdata=rng.getrandbits(32))
                    if cfg[c][0] < 16 and kw["wdata"] >> 16:
                        cov("v02_host_write_16_bits")
                elif k < 0.07:
                    act = "w_ctrl"
                    kw = dict(we=True, addr=sim.core_reg(c, sim.C_CRCCTRL), wdata=crcctrl(c)[0])
                elif k < 0.09:
                    act = "w_ctrl"
                    kw = dict(we=True, addr=sim.core_reg(c, sim.C_POLY_HI), wdata=rng.getrandbits(16))
                elif k < 0.10:
                    act = "other"
                    enabled ^= 1 << c
                    if not enabled & (1 << c) and rng.random() < 0.5:
                        enabled |= 1 << c
                    kw = dict(we=True, addr=sim.A_CTRL, wdata=enabled)
                elif k < 0.11:
                    act = "other"
                    kw = dict(we=True, addr=sim.A_CTRL, wdata=enabled | (1 << (8 + c)))
                elif k < 0.12:
                    act = "other"
                    kw = dict(we=True, addr=sim.A_CTRL, wdata=enabled | (1 << (16 + c)))
                elif k < 0.15:
                    act = "other"
                    kw = dict(we=True, addr=sim.core_reg(c, sim.C_FIFO), wdata=rng.getrandbits(32))
                elif k < 0.20:
                    act = "other"
                    kw = dict(addr=sim.core_reg(c, sim.C_FIFO), snap=True, commit=True)
                ev.clear()
                await h.cycle_step(rng.getrandbits(5), rng.getrandbits(8), **kw)
                now = {cc: {kind for i, kind in ev if i == cc} for cc in cores}
                for cc in cores:
                    host_here = cc == c and act != "other"
                    if pend[cc]:
                        for kind in now[cc]:
                            cov(f"{kind}_after_feed")
                        if host_here:
                            cov(f"host_{act}_after_feed")
                        if not (en_now >> cc) & 1:
                            cov("disabled_after_feed")
                        if (restart >> cc) & 1:
                            cov("restart_after_feed")
                        if (fclear >> cc) & 1:
                            cov("fifo_clear_after_feed")
                    if "set" in prev[cc] and "feed" in now[cc]:
                        cov("feed_after_set")
                    if "feed" in now[cc]:
                        if "set" in now[cc]:
                            cov("set_with_feed")
                        if host_here and act != "read":
                            cov(f"host_{act}_with_feed")
                    pend[cc] = "feed" in now[cc] and "set" not in now[cc] and not (host_here and act == "w_crc")
                    if pend[cc]:
                        widths.add(cfg[cc][0])                  # the width the pending step uses
                        if cfg[cc][0] >= 16 and cfg[cc][1]:
                            cov("poly_hi_feed")
                prev = now
                if rnd == 3 and cyc >= 6000 and any(pend.values()):
                    cov("reset_after_feed")
                    break                               # the reset below lands on a pending step
        await h.reset()
        await h.cycle_step(addr=sim.core_reg(0, sim.C_CRC))   # compared: CRC 0 after the reset
        await h.write(sim.A_CTRL, 0)
    finally:
        sim.Core._crc_feed, sim.Core._write_dst, sim.Core._src = orig
    dut._log.info("crc coverage: " + ", ".join(f"{k}={n}" for k, n in sorted(seen.items())))
    want = ["feed_after_feed", "read_after_feed", "set_after_feed", "feed_after_set", "set_with_feed",
            "host_read_after_feed", "host_w_crc_after_feed", "host_w_ctrl_after_feed",
            "host_w_crc_with_feed", "host_w_ctrl_with_feed",
            "disabled_after_feed", "restart_after_feed", "fifo_clear_after_feed", "reset_after_feed",
            "poly_hi_feed", "v02_host_write_16_bits"]
    missing = [g for g in want if not seen.get(g)]
    assert not missing, (missing, seen)
    assert widths == set(range(32)), sorted(set(range(32)) - widths)   # every width with a step pending
