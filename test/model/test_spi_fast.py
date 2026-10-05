"""SPI master speed and the input synchroniser bypass (docs/isa.md §2,
SYNC_BYPASS). A mode-0 SPI device model answers on SCK falls after a delay of
`d` clocks (0: within the clock after the SCK pad fell, like a wire; the RTL
test's device model is d = 1). For each firmware and setting: clocks per SCK,
and the largest device delay with every byte correct in both directions."""
import pathlib

from pe import asm, host, sim
from pe.modelhost import ModelHost

FW = pathlib.Path(__file__).resolve().parents[2] / "firmware"
MOSI, SCK, MISO = 8, 9, 16                 # uo0, uo1, ui0
TX = [0xA5, 0x3C, 0xFF, 0x00, 0x81]
RESP = [0xC3, 0x5A, 0x01, 0x80, 0x7E]


class SpiDevice:
    """Mode 0: MOSI taken on an SCK rise; the next MISO bit `delay` clocks
    after the SCK fall is seen on the pads (the first before the first rise)."""

    def __init__(self, resp, delay):
        self.bits = [(b >> (7 - i)) & 1 for b in resp for i in range(8)]
        self.delay, self.idx, self.prev, self.cur, self.n = delay, 0, 0, 0, 0
        self.rx, self.pending, self.miso = [], [], self.bits[0]

    def tick(self, cyc, sck, mosi):
        if sck and not self.prev:
            self.cur, self.n = (self.cur << 1) | mosi, self.n + 1
            if self.n % 8 == 0:
                self.rx.append(self.cur & 0xFF)
        if self.prev and not sck:
            self.idx += 1
            self.pending.append((cyc + self.delay, self.bits[self.idx] if self.idx < len(self.bits) else 0))
        self.prev = sck
        while self.pending and self.pending[0][0] <= cyc:
            self.miso = self.pending.pop(0)[1]
        return self.miso


def transfer(fw, div, bypass, delay):
    """(clocks per SCK, bytes the device got, bytes the master got)."""
    mh = ModelHost()
    prog = asm.assemble((FW / f"{fw}.pasm").read_text())
    mh.apply(host.load_program(0, prog, out_pins=(MOSI, 1), side_pins=SCK, in_pins=MISO, clkdiv=div << 8))
    if bypass:
        mh.write(sim.A_SYNCBYP, 1 << MISO)
    for b in TX:
        mh.push(0, b << 24)
    dev = SpiDevice(RESP, delay)
    mh.ui = dev.miso
    mh.start(1)
    rises, prev = [], 0
    for cyc in range(len(TX) * 8 * 4 * div + 40):
        out = mh.chip.pin_out                  # what the pads show this cycle
        sck = (out >> SCK) & 1
        if sck and not prev:
            rises.append(cyc)
        prev = sck
        mh.ui = dev.tick(cyc, sck, (out >> MOSI) & 1)
        mh.step()
    got = [w & 0xFF for w in mh.chip.cores[0].rxf]
    return rises[1] - rises[0], dev.rx, got


def max_delay(fw, div, bypass):
    """Clocks per SCK and the largest device delay (clocks) that still reads
    every byte correctly; -1 if even d = 0 fails."""
    best = -1
    for d in range(4):
        cps, rx, got = transfer(fw, div, bypass, d)
        assert rx == TX, (fw, div, bypass, d, rx)       # MOSI is always fine
        if got != RESP:
            break
        best = d
    return cps, best


def test_spi_sync_bypass_table():
    r = {(fw, div, byp): max_delay(fw, div, byp)
         for fw, div, byp in [("spi_master", 2, 0), ("spi_master", 1, 0), ("spi_master", 1, 1),
                              ("spi_master_fast", 1, 0), ("spi_master_fast", 1, 1)]}
    for (fw, div, byp), (cps, d) in r.items():
        print(f"{fw:16s} CLKDIV {div} bypass {byp}: {cps} clocks per SCK, "
              + (f"correct if MISO's next bit reaches the pad within {d + 1} clock(s) of the SCK fall" if d >= 0 else
                 "MISO sampled a bit late even with a zero-delay device"))
    assert r[("spi_master", 2, 0)] == (8, 1)            # today's setting: 6.25 MHz at 50 MHz
    assert r[("spi_master", 1, 0)] == (4, -1)           # 0xA5 -> 0x52 (docs/pio-comparison.md)
    assert r[("spi_master", 1, 1)] == (4, 0)            # bypass: a wire-like loopback only
    assert r[("spi_master_fast", 1, 0)] == (4, 0)
    assert r[("spi_master_fast", 1, 1)] == (4, 1)       # 12.5 MHz with the DIV-2 delay budget


def test_spi_div1_without_bypass_reads_late():
    _, rx, got = transfer("spi_master", 1, 0, 0)
    # each bit a bit late: the first byte starts with the bit before it (MISO's
    # first bit, 1, seen twice), 0xC3 -> 0xE1, 0x5A -> 0xAD
    late = [((RESP[i - 1] & 1 if i else RESP[0] >> 7) << 7) | (RESP[i] >> 1) for i in range(len(RESP))]
    assert rx == TX and got == late == [0xE1, 0xAD, 0x00, 0xC0, 0x3F]
