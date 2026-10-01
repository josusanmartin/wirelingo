"""I2C master: SCL stuck low in every phase must time out, not hang.

A peer pulls SCL low at the n-th time the master releases it, for every n in a
START / WRITE / READ / STOP transaction (19 releases; START has none, as SCL
idles high, so n = 0 means SCL is held low from the start), then holds it. Each
command from then on must time out: F1 set, WRITE/READ reply 0x100, the master
back waiting for commands. After the peer lets go, a new transaction works.
The SCL waveform timing (4 ticks low, 4 high) is checked on the clean run."""
import pathlib

import pytest

from pe import asm, host, sim
from pe.modelhost import ModelHost

FW = pathlib.Path(__file__).resolve().parents[2] / "firmware"
SDA, SCL = 0, 1
DIV = 4                                  # clocks per tick (>= 3: see i2c_master.pasm)
TMO = 2000                               # firmware timeout, ticks
START, STOP, WRITE, READ_NACK = 2, 3, 0xA0 << 24, 1 | 4
CMDS = [START, WRITE, READ_NACK, STOP]
CLEAN_REPLIES = [1, 0xFF]                # nobody on the bus: NACK, then 0xFF read
RELEASES = 9 + 9 + 1                     # 8 bits + ACK, 8 bits + ACK, STOP


def master():
    mh = ModelHost()
    prog = asm.assemble((FW / "i2c_master.pasm").read_text(), {"SDA": SDA, "SCL": SCL})
    mh.apply(host.pin_owner_writes({SDA: 0, SCL: 0}))
    mh.write(sim.A_POUT, 0b11)
    mh.write(sim.A_OD, 0b11)
    mh.pull_up = 0b11
    mh.apply(host.load_program(0, prog, out_pins=(SDA, 1), in_pins=SDA, clkdiv=DIV << 8))
    mh.start(1)
    return mh


def run(mh, cmds, stuck_at=None, budget=None):
    """Push cmds; pull SCL low from the stuck_at-th master release on.
    Returns (replies, SCL drive trace)."""
    for c in cmds:
        mh.push(0, c)
    releases, prev, trace, replies = 0, 1, [], []
    if stuck_at == 0:
        mh.ext_oe |= 1 << SCL
        mh.uio &= ~(1 << SCL)
    budget = budget or 4 * (TMO + 200) * DIV
    for _ in range(budget):
        drive = (mh.chip.pin_out >> SCL) & 1
        if drive and not prev:
            releases += 1
            if releases == stuck_at:
                mh.ext_oe |= 1 << SCL       # peer holds SCL low
                mh.uio &= ~(1 << SCL)
        prev = drive
        trace.append(drive)
        mh.step()
        replies += mh.pop_all(0)
    return replies, trace


def test_clean_transaction_and_scl_timing():
    mh = master()
    replies, trace = run(mh, CMDS)
    assert replies == CLEAN_REPLIES
    assert mh.chip.flags & 2 == 0
    # SCL high/low phases inside the WRITE and READ bytes: 4 ticks each
    runs, level, n = [], trace[0], 0
    for v in trace:
        if v == level:
            n += 1
        else:
            runs.append((level, n))
            level, n = v, 1
    highs = [n for lv, n in runs if lv == 1][1:-1]   # skip idle and the STOP release
    assert set(highs) == {4 * DIV}, highs
    lows = [n for lv, n in runs if lv == 0][1:]      # skip the START hold
    assert min(lows) == 4 * DIV, lows


@pytest.mark.parametrize("stuck_at", range(0, RELEASES + 1))
def test_scl_stuck_low_times_out(stuck_at):
    mh = master()
    replies, _ = run(mh, CMDS, stuck_at=stuck_at)
    phase = 0 if stuck_at == 0 else 1 if stuck_at <= 9 else 2 if stuck_at <= 18 else 3
    # replies before the stuck command are normal; from it on every WRITE/READ times out
    want = [r if i + 1 < phase else 0x100 for i, r in enumerate(CLEAN_REPLIES)]
    assert replies == want, (stuck_at, [hex(r) for r in replies])
    assert mh.chip.flags & 2
    core = mh.chip.cores[0]
    assert core.stalled and not core.txf     # back at `pull block`, all commands consumed
    # the peer lets go: the bus works again
    mh.ext_oe &= ~(1 << SCL)
    mh.write(sim.A_FCLR, 2)
    replies, _ = run(mh, CMDS)
    assert replies == CLEAN_REPLIES and mh.chip.flags & 2 == 0
