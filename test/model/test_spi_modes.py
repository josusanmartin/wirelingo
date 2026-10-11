"""Pin-level SPI mode coverage; independent peer samples actual SCK edges.

No synchronizer bypass is used. Delayed MISO is a digital model assumption,
not a measurement of pad, board or silicon timing.
"""
import pathlib

import pytest

from pe import asm, host, sim
from pe.modelhost import ModelHost

FW = pathlib.Path(__file__).resolve().parents[2] / "firmware"
MOSI, SCK, MISO = 8, 9, 16
TX = [0xA5, 0x3C, 0x00, 0xFF, 0x81]
RESP = [0x5A, 0xC3, 0xFF, 0x00, 0x7E]


class SpiPeer:
    """CPHA0 preloads MISO; CPHA1 launches it on the leading edge.

    Sample on the leading edge for CPHA0 and trailing edge for CPHA1.
    Launch the next MISO bit on the opposite edge after `delay` clocks.
    """
    def __init__(self, mode, response, delay=1):
        self.cpol, self.cpha = mode >> 1, mode & 1
        self.prev = self.cpol
        self.bits = [(b >> i) & 1 for b in response for i in range(7, -1, -1)]
        self.delay = delay
        self.miso = self.bits[0] if not self.cpha else 0
        self.pending, self.edges, self.rx = [], [], []
        self.index = self.byte = 0
        self.prev_mosi = None

    def tick(self, cycle, sck, mosi):
        if sck != self.prev:
            leading = sck != self.cpol
            self.edges.append((cycle, leading))
            if leading != bool(self.cpha):
                # MOSI must already be stable at the peer's sample edge.
                assert mosi == self.prev_mosi, "MOSI changed at the sample edge"
                self.byte = (self.byte << 1) | mosi
                self.index += 1
                if self.index % 8 == 0:
                    self.rx.append(self.byte & 0xFF)
            else:
                bit = self.bits[self.index] if self.index < len(self.bits) else 0
                self.pending.append((cycle + self.delay, bit))
        self.prev, self.prev_mosi = sck, mosi
        while self.pending and self.pending[0][0] <= cycle:
            _, self.miso = self.pending.pop(0)
        return self.miso


def program(mode):
    return asm.assemble_all((FW / "spi_master_modes.pasm").read_text(),
                            {"CPOL": mode >> 1})[mode & 1]


def setup(mode, divider=4 << 8, fifo_depth=8, delay=1):
    mh = ModelHost(fifo_depth=fifo_depth)
    mh.write(sim.A_POUT, (mode >> 1) << SCK)
    mh.apply(host.load_program(0, program(mode), out_pins=(MOSI, 1),
                               side_pins=SCK, in_pins=MISO, clkdiv=divider))
    peer = SpiPeer(mode, RESP, delay)
    mh.ui = peer.miso
    return mh, peer


def action(mh, peer, fn, *args, **kwargs):
    """Observe pins before every clock, including clocks caused by host I/O."""
    out = mh.chip.pin_out
    mh.ui = peer.tick(mh.chip.cycle, (out >> SCK) & 1, (out >> MOSI) & 1)
    return fn(*args, **kwargs)


def step(mh, peer, n):
    for _ in range(n):
        action(mh, peer, mh.step)


def pop(mh, peer):
    addr = sim.core_reg(0, sim.C_FIFO)
    value = action(mh, peer, mh.snap, addr)
    action(mh, peer, mh.commit, addr)
    action(mh, peer, mh.cancel)
    return value


@pytest.mark.parametrize("mode", range(4))
@pytest.mark.parametrize("divider", [3, 4])
@pytest.mark.parametrize("delay", [1, 2])
def test_full_duplex_modes(mode, divider, delay):
    mh, peer = setup(mode, divider << 8, delay=delay)
    for b in TX:
        mh.push(0, b << 24)
    mh.start(1)
    step(mh, peer, 1500)
    assert peer.rx == TX
    assert mh.pop_all(0) == RESP
    assert len(peer.edges) == 16 * len(TX)
    assert [leading for _, leading in peer.edges] == [True, False] * (8 * len(TX))
    # No host I/O during this transfer: check the documented digital waveform.
    active = [peer.edges[i + 1][0] - peer.edges[i][0]
              for i in range(0, len(peer.edges), 2)]
    idle = [peer.edges[i + 1][0] - peer.edges[i][0]
            for i in range(1, len(peer.edges) - 1, 2)]
    assert set(active) == {2 * divider}
    assert set(idle) == {3 * divider, 5 * divider}
    assert ((mh.chip.pin_out >> SCK) & 1) == mode >> 1
    assert mh.chip.cores[0].stalled and not mh.chip.cores[0].fault


@pytest.mark.parametrize("mode", range(4))
def test_tx_starvation_keeps_clock_idle(mode):
    mh, peer = setup(mode)
    mh.start(1)
    step(mh, peer, 100)
    assert not peer.edges
    for i, b in enumerate(TX):
        action(mh, peer, mh.push, 0, b << 24)
        step(mh, peer, 300)
        assert pop(mh, peer) == RESP[i]
        assert peer.rx == TX[:i + 1]
        assert len(peer.edges) == 16 * (i + 1)
        assert ((mh.chip.pin_out >> SCK) & 1) == mode >> 1
        step(mh, peer, 100)
        assert len(peer.edges) == 16 * (i + 1)
        assert mh.chip.cores[0].stalled


@pytest.mark.parametrize("mode", range(4))
def test_rx_backpressure_pauses_then_resumes_without_extra_bit(mode):
    mh, peer = setup(mode, fifo_depth=4)
    for b in TX[:4]:
        mh.push(0, b << 24)
    mh.start(1)
    step(mh, peer, 100)  # first TX word consumed; one slot is now available
    action(mh, peer, mh.push, 0, TX[4] << 24)
    step(mh, peer, 1500)
    assert mh.chip.cores[0].rxf == RESP[:4]
    assert peer.rx == TX
    assert mh.chip.cores[0].stalled and not mh.chip.cores[0].fault
    paused = list(peer.edges)
    step(mh, peer, 100)
    assert peer.edges == paused
    got = [pop(mh, peer)]
    step(mh, peer, 100)
    while mh.chip.cores[0].rxf:
        got.append(pop(mh, peer))
    assert got == RESP
    assert len(peer.edges) == 16 * len(TX)
    assert ((mh.chip.pin_out >> SCK) & 1) == mode >> 1


@pytest.mark.parametrize("mode", range(4))
def test_disable_clear_reload_different_mode(mode):
    mh, peer = setup(mode)
    mh.push(0, TX[0] << 24)
    mh.start(1)
    step(mh, peer, 300)
    assert peer.rx == TX[:1] and mh.chip.cores[0].rxf == RESP[:1]
    action(mh, peer, mh.write, sim.A_CTRL, 0)
    step(mh, peer, 2)  # CTRL is registered; ensure disable has taken effect
    mh.push(0, 0xDE << 24)  # stale queued traffic while disabled
    mh.write(sim.A_CTRL, 1 << 16)
    mh.step(2)
    assert not mh.chip.cores[0].rxf and not mh.chip.cores[0].txf
    mode ^= 3  # exercise both a phase and polarity change
    # CS must be deasserted while changing idle polarity and reloading.
    mh.write(sim.A_POUT, (mode >> 1) << SCK)
    mh.apply(host.load_program(0, program(mode), out_pins=(MOSI, 1),
                               side_pins=SCK, in_pins=MISO, clkdiv=4 << 8))
    peer = SpiPeer(mode, RESP)
    mh.ui = peer.miso
    mh.push(0, TX[0] << 24)
    mh.start(1)
    step(mh, peer, 300)
    assert peer.rx == TX[:1] and mh.pop_all(0) == RESP[:1]
    assert len(peer.edges) == 16
    assert ((mh.chip.pin_out >> SCK) & 1) == mode >> 1
