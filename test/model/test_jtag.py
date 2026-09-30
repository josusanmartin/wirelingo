"""JTAG engine firmware vs an IEEE 1149.1 TAP model: IDCODE via Shift-DR, bypass."""
import pathlib

from pe import asm, host, sim
from pe.modelhost import ModelHost

FW = pathlib.Path(__file__).resolve().parents[2] / "firmware"
TDI, TMS, TCK, TDO = 4, 5, 6, 16  # uio4, uio5, uio6 outputs; TDO on ui0

NEXT = {  # state -> (TMS=0, TMS=1)
    "TLR": ("RTI", "TLR"), "RTI": ("RTI", "SELDR"), "SELDR": ("CAPDR", "SELIR"),
    "CAPDR": ("SHDR", "EX1DR"), "SHDR": ("SHDR", "EX1DR"), "EX1DR": ("PDR", "UPDR"),
    "PDR": ("PDR", "EX2DR"), "EX2DR": ("SHDR", "UPDR"), "UPDR": ("RTI", "SELDR"),
    "SELIR": ("CAPIR", "TLR"), "CAPIR": ("SHIR", "EX1IR"), "SHIR": ("SHIR", "EX1IR"),
    "EX1IR": ("PIR", "UPIR"), "PIR": ("PIR", "EX2IR"), "EX2IR": ("SHIR", "UPIR"),
    "UPIR": ("RTI", "SELDR"),
}


class Tap:
    IDCODE = 0x4BA00477
    IR_LEN = 4

    def __init__(self, mh):
        self.mh, self.state, self.prev_tck = mh, "TLR", 0
        self.ir, self.ir_sh, self.dr_sh, self.tdo = 0b0001, 0, 0, 1

    def tick(self):
        v = self.mh.bus_uio()
        tck = (v >> TCK) & 1
        if tck and not self.prev_tck:               # rising: sample TDI/TMS, advance
            tms, tdi = (v >> TMS) & 1, (v >> TDI) & 1
            s = self.state
            if s == "SHDR":
                self.dr_sh = (self.dr_sh >> 1) | (tdi << (self.dr_len() - 1))
            elif s == "SHIR":
                self.ir_sh = (self.ir_sh >> 1) | (tdi << (self.IR_LEN - 1))
            self.state = NEXT[s][tms]
            if self.state == "TLR":
                self.ir = 0b0001
            elif self.state == "CAPDR":
                self.dr_sh = self.IDCODE if self.ir == 0b0001 else 0
            elif self.state == "CAPIR":
                self.ir_sh = 0b0001
            elif self.state == "UPIR":
                self.ir = self.ir_sh
        if not tck and self.prev_tck:               # falling: update TDO
            if self.state == "SHDR":
                self.tdo = self.dr_sh & 1
            elif self.state == "SHIR":
                self.tdo = self.ir_sh & 1
        self.prev_tck = tck
        self.mh.ui = (self.mh.ui & ~1) | self.tdo

    def dr_len(self):
        return 32 if self.ir == 0b0001 else 1


def shift(mh, tap, pairs):
    """pairs: list of (tdi, tms). Returns the TDO bits."""
    n = len(pairs)
    mh.push(0, n - 1)
    for i in range(0, n, 16):
        w = 0
        for j, (tdi, tms) in enumerate(pairs[i:i + 16]):
            w |= (tdi | tms << 1) << (2 * j)
        mh.push(0, w)
    want = (n + 31) // 32
    words = []
    for _ in range(200000):
        tap.tick()
        mh.step()
        words += mh.pop_all(0)
        if len(words) >= want:
            break
    v = sum(w << (32 * i) for i, w in enumerate(words))
    return [(v >> i) & 1 for i in range(n)]


def test_jtag_idcode():
    mh = ModelHost()
    prog = asm.assemble((FW / "jtag.pasm").read_text())
    mh.apply(host.load_program(0, prog, out_pins=(TDI, 2), side_pins=TCK, in_pins=TDO, clkdiv=2 << 8))
    mh.write(sim.A_POE, (1 << TDI) | (1 << TMS) | (1 << TCK))
    mh.start(1)
    tap = Tap(mh)
    # reset (5x TMS=1), go to Shift-DR (0,1,0,0), shift 32 bits out (TMS=1 on the last)
    seq = [(0, 1)] * 5 + [(0, 0), (0, 1), (0, 0), (0, 0)]
    seq += [(0, 0)] * 31 + [(0, 1)] + [(0, 1), (0, 0)]   # exit to Update-DR, then RTI
    tdo = shift(mh, tap, seq)
    idcode = sum(b << i for i, b in enumerate(tdo[9:9 + 32]))
    assert idcode == Tap.IDCODE, hex(idcode)
    assert tap.state == "RTI"
