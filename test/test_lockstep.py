"""Lockstep verification: RTL pe_chip vs the Python golden model (tools/pe/sim.py).

Random programs, random pin stimulus and random host bus traffic are applied to
both; all architectural state is compared every clock cycle.

Env knobs: LOCKSTEP_SEEDS (number of random programs), LOCKSTEP_CYCLES, LOCKSTEP_SEED0.
"""
import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge, Timer

from pe import isa, sim
from pe.randprog import random_program, random_config_writes

BANK_AW = int(os.environ.get("PE_BANK_AW", "4"))
BANK_WORDS = 1 << BANK_AW


def v(sig):
    return int(sig.value)


def core_state_rtl(core):
    return dict(
        pc=v(core.pc), r0=v(core.r0), r1=v(core.r1), r2=v(core.r2), r3=v(core.r3),
        isr=v(core.isr), isr_cnt=v(core.isr_cnt), osr=v(core.osr), osr_cnt=v(core.osr_cnt),
        z=v(core.z), c=v(core.c), t=v(core.t), dcnt=v(core.dcnt), in_wait=v(core.in_wait),
        halted=v(core.halted), fault=v(core.fault), stalled=v(core.stalled),
        cnt=v(core.cnt), facc=v(core.facc),
        txl=v(core.txf.cnt), rxl=v(core.rxf.cnt),
    )


def core_state_model(m):
    return dict(
        pc=m.pc, r0=m.r[0], r1=m.r[1], r2=m.r[2], r3=m.r[3],
        isr=m.isr, isr_cnt=m.isr_cnt, osr=m.osr, osr_cnt=m.osr_cnt,
        z=m.z, c=m.c, t=m.t, dcnt=m.dcnt, in_wait=m.in_wait,
        halted=m.halted, fault=m.fault, stalled=m.stalled,
        cnt=m.cnt, facc=m.facc, txl=len(m.txf), rxl=len(m.rxf),
    )


class Harness:
    def __init__(self, dut):
        self.dut = dut
        self.chip = dut.u_chip
        self.cores = [self.chip.g_core[i].u_core for i in range(4)]
        self.model = sim.Chip(bank_words=BANK_WORDS)
        self.cycle = 0
        self.stats = {}
        self.trace = []  # recent (cycle, description) for failure reports

    async def reset(self):
        d = self.dut
        d.rst_n.value = 0
        d.ui_in.value = 0
        d.uio_in.value = 0
        d.bus_addr.value = 0
        d.bus_we.value = 0
        d.bus_wdata.value = 0
        d.bus_commit.value = 0
        for _ in range(3):
            await RisingEdge(d.clk)
        await FallingEdge(d.clk)
        d.rst_n.value = 1
        self.model = sim.Chip(bank_words=BANK_WORDS)

    def compare(self):
        errs = []
        m = self.model
        for i, (rc, mc) in enumerate(zip(self.cores, m.cores)):
            a, b = core_state_rtl(rc), core_state_model(mc)
            for k in a:
                if a[k] != b[k]:
                    errs.append(f"core{i}.{k}: rtl={a[k]:#x} model={b[k]:#x}")
        for name, rv, mv in (("pin_out", v(self.chip.pin_out), m.pin_out),
                             ("pin_oe", v(self.chip.pin_oe), m.pin_oe),
                             ("flags", v(self.chip.flags), m.flags)):
            if rv != mv:
                errs.append(f"{name}: rtl={rv:#x} model={mv:#x}")
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

    async def cycle_step(self, ui=0, uio=0, we=False, addr=0, wdata=0, commit=False):
        """Apply one cycle of inputs to both RTL and model, then compare."""
        d = self.dut
        d.ui_in.value = ui
        d.uio_in.value = uio
        d.bus_addr.value = addr
        d.bus_we.value = int(we)
        d.bus_wdata.value = wdata
        d.bus_commit.value = int(commit)
        await Timer(1, unit="ps")
        rd_rtl, rd_model = v(d.bus_rdata), self.model.read(addr)
        if rd_rtl != rd_model:
            self.fail([f"bus_rdata[{addr:#06x}]: rtl={rd_rtl:#x} model={rd_model:#x}"], "bus read")
        if we or commit:
            self.trace.append((self.cycle, f"{'W' if we else 'R'} {addr:#06x} {wdata:#010x}"))
        await RisingEdge(d.clk)
        self.model.step(ui_in=ui, uio_in=uio, we=we, addr=addr, wdata=wdata, commit=commit)
        self.cycle += 1
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
        for off in (0, 1, 2, 3, 4, 5, 0xA, 0xB, 0xC, 0xD):
            await h.write(sim.core_reg(c, off), rng.getrandbits(32))
            await h.cycle_step(addr=sim.core_reg(c, off))
    for a in (sim.A_BANK, sim.A_POWN_LO, sim.A_POWN_HI, sim.A_OD, sim.A_FSET, sim.A_FCLR):
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
        # bank ownership + programs + config, all while cores are disabled
        owners = [rng.randrange(4) for _ in range(4)] if rng.random() < 0.3 else [0, 1, 2, 3]
        await h.write(sim.A_BANK, sum(o << (2 * b) for b, o in enumerate(owners)))
        for b in range(4):
            for w in range(BANK_WORDS):
                word = random_program(rng, b, BANK_WORDS, owners)
                await h.write((b << 8) | w, word)
        for addr, data in random_config_writes(rng, BANK_WORDS, owners):
            await h.write(addr, data)
        await h.write(sim.A_CTRL, 15 if rng.random() < 0.7 else rng.randrange(1, 16))
        # run
        ui = rng.getrandbits(5)
        uio = rng.getrandbits(8)
        toggle_p = rng.choice([0.02, 0.1, 0.4])
        for _ in range(ncycles):
            for bit in range(5):
                if rng.random() < toggle_p:
                    ui ^= 1 << bit
            for bit in range(8):
                if rng.random() < toggle_p:
                    uio ^= 1 << bit
            op = rng.random()
            c = rng.randrange(4)
            if op < 0.06:
                await h.cycle_step(ui, uio, we=True, addr=sim.core_reg(c, sim.C_FIFO), wdata=rng.getrandbits(32))
            elif op < 0.12:
                await h.cycle_step(ui, uio, commit=True, addr=sim.core_reg(c, sim.C_FIFO))
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
                await h.cycle_step(ui, uio, we=True, addr=sim.core_reg(c, rng.choice([0, 1, 2, 3, 4, 5])),
                                   wdata=rng.getrandbits(32))
            elif op < 0.155:
                await h.cycle_step(ui, uio, we=True, addr=rng.choice([sim.A_POWN_LO, sim.A_POWN_HI, sim.A_OD,
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
