"""PS/2 device transmitter vs a host model sampling DAT on CLK falling edges."""
import pathlib

from pe import asm, host, sim
from pe.modelhost import ModelHost

FW = pathlib.Path(__file__).resolve().parents[2] / "firmware"
CLK, DAT = 0, 1


def test_ps2_frames():
    mh = ModelHost()
    prog = asm.assemble((FW / "ps2_device.pasm").read_text(), {"CLK": CLK, "DAT": DAT})
    mh.apply(host.load_program(0, prog, out_pins=(DAT, 1), in_pins=CLK, clkdiv=480 << 8))
    mh.write(sim.core_reg(0, sim.C_CRCCTRL), 1 | (0 << 16) | (1 << 21))  # 1-bit LFSR, OUT tap
    mh.write(sim.A_POUT, (1 << CLK) | (1 << DAT))
    mh.write(sim.A_OD, (1 << CLK) | (1 << DAT))
    mh.pull_up = (1 << CLK) | (1 << DAT)
    mh.start(1)
    codes = [0x1C, 0xF0, 0x1C, 0x5A, 0x00, 0xFF]   # 'A' make/break, Enter, edge cases
    queue = list(codes)
    frames, bits, prev = [], [], 1
    for _ in range(len(codes) * 12 * 80 * 48 * 10):
        if queue and mh.tx_level(0) < 4:
            mh.push(0, queue.pop(0))   # the FIFO is 4 deep; pushes to a full FIFO are dropped
        mh.step()
        v = mh.bus_uio()
        clk, dat = (v >> CLK) & 1, (v >> DAT) & 1
        if prev and not clk:
            bits.append(dat)
            if len(bits) == 11:
                frames.append(bits)
                bits = []
        prev = clk
        if len(frames) == len(codes):
            break
    for f, c in zip(frames, codes):
        assert f[0] == 0 and f[10] == 1, f
        data = sum(b << i for i, b in enumerate(f[1:9]))
        assert data == c, (hex(data), hex(c))
        assert (sum(f[1:10]) & 1) == 1, "odd parity"
    assert len(frames) == len(codes)
