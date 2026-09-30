import cocotb
from cocotb.triggers import RisingEdge, ReadOnly
from pe import isa

async def trace_core(dut, idx, n=400, log=None):
    core = dut.user_project.u_chip.g_core[idx].u_core
    chip = dut.user_project.u_chip
    out = log if log is not None else []
    while len(out) < n:
        await RisingEdge(dut.clk)
        await ReadOnly()
        if int(core.exec.value) and int(core.complete.value):
            instr = int(core.instr.value)
            out.append(f"pc={int(core.pc.value):4d} {isa.disasm(instr):32s} osr={int(core.osr.value):08x} "
                       f"cmpl={int(core.complete.value)} pin_out={int(chip.pin_out.value) & 3:02b} bus={int(dut.uio_in.value) & 3:02b}")
