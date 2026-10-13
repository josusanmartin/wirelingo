"""Maintained headerless mode0 framing/rearm regression, normal bus loading.

Model bus cycles are not actual serial host timing; the native test owns that
boundary. Peer decisions depend only on pins and predeclared response bytes.
"""
import json
import os
from pathlib import Path

import pytest
from pe import asm, host, sim, spipacked
from pe.modelhost import ModelHost
from test_spi_packed import FramedPeer, pattern

FW = Path(__file__).resolve().parents[2] / 'firmware/spi_master_preloaded_once.pasm'
MOSI, SCK, CS, MISO = 8, 9, 10, 16


@pytest.fixture(scope='module', autouse=True)
def model_budget():
    """Local to this module; share the optional ledger with the packed suite."""
    path = os.environ.get('PACKED_BUDGET_PATH')
    target = Path(path) if path else None
    state = json.loads(target.read_text()) if target else dict(cycles=0, limit=8000000)
    original = sim.Chip.step
    def counted(chip, *a, **kw):
        assert state['cycles'] < state['limit'], 'aggregate model step cap'
        state['cycles'] += 1
        return original(chip, *a, **kw)
    sim.Chip.step = counted
    try:
        yield
    finally:
        sim.Chip.step = original
        if target:
            target.write_text(json.dumps(state) + '\n')


class PreloadedRun:
    def __init__(self, messages):
        self.mh = ModelHost()
        self.mh.chip = sim.Chip(ncores=2, nbanks=2, bank_words=256, fifo_depth=8)
        self.peer = FramedPeer(0, messages, 2)
        self.bus = []
        self.mh.write(sim.A_POUT, 1 << CS)
        self.mh.write(sim.A_POE, (1 << MOSI) | (1 << SCK) | (1 << CS))
        self.mh.apply(host.pin_owner_writes({MOSI: 0, SCK: 0, CS: 0, MISO: 0}))
        self.program = asm.assemble(FW.read_text(), dict(CPOL=0, WORD_BITS=32, CS=CS))
        self.mh.apply(host.load_program(0, self.program, out_pins=(MOSI, 1),
                                       side_pins=SCK, in_pins=MISO, clkdiv=3 << 8))

    def call(self, method, *args):
        out = self.mh.chip.pin_out
        cs, sck, mosi = [(out >> p) & 1 for p in (CS, SCK, MOSI)]
        self.mh.ui = self.peer.tick(self.mh.chip.cycle, cs, sck, mosi)
        if method != self.mh.step:
            self.bus.append((self.mh.chip.cycle, bool(not cs)))
        return method(*args)

    def apply(self, writes):
        for addr, word in writes:
            self.call(self.mh.write, addr, word)

    def message(self, payload, response):
        plan = spipacked.preload_plan(payload)
        fifo = plan['fifo_address']
        status = sim.core_reg(0, sim.C_STATUS)
        assert self.call(self.mh.read, status) & 255 == 0
        self.apply(plan['disable_writes'])
        self.apply(plan['register_writes'])
        for word in plan['tx_words']:
            self.call(self.mh.write, fifo, word)
        self.apply(plan['start_writes'])
        for _ in range(4096):
            self.call(self.mh.step)
        st = self.call(self.mh.read, status)
        assert st & 255 == plan['rx_words'] << 4
        assert st & (1 << 13) and not st & (1 << 14)
        words = []
        for _ in range(plan['rx_words']):
            # Normal snap/commit/cancel; no internal FIFO pop shortcut.
            word = self.call(self.mh.snap, fifo)
            self.call(self.mh.commit, fifo)
            self.call(self.mh.cancel)
            words.append(word)
        assert spipacked.decode(words, len(payload)) == response
        assert self.call(self.mh.read, status) & 255 == 0
        assert self.call(self.mh.read, sim.A_POUT) & ((1 << CS) | (1 << SCK)) == 1 << CS
        assert not any(active for _, active in self.bus)
        return words


@pytest.mark.parametrize('length', [1, 3, 4, 5, 31, 32])
def test_preloaded_exact_tail(length):
    tx, rx = pattern(length, 0xA5), pattern(length, 0x6D)
    r = PreloadedRun([(tx, rx)])
    got = r.message(tx, rx)
    assert len(r.peer.frames) == 1
    assert len(r.peer.edges) == 16 * length
    assert len(got) == (length + 3) // 4
    if length % 4:
        assert got[-1] < 1 << (8 * (length % 4))


def test_two_messages_rearm_without_reset():
    messages = [(pattern(32, a), pattern(32, b)) for a, b in [(0xA5, 0x6D), (0x39, 0xD3)]]
    r = PreloadedRun(messages)
    chip = r.mh.chip
    for tx, rx in messages:
        assert len(r.message(tx, rx)) == 8
        assert r.mh.chip is chip
    assert len(r.peer.frames) == 2 and len(r.peer.edges) == 1024
    assert all(len(f['samples']) == 256 for f in r.peer.frames)


@pytest.mark.parametrize('payload', [b'', bytes(33), None, 'abc', 3, [1, 2]])
def test_invalid_payload(payload):
    with pytest.raises(ValueError):
        spipacked.preload_plan(payload)


@pytest.mark.parametrize('kw', [dict(mode=1), dict(mode=True), dict(core=1), dict(core=False),
                               dict(fifo_depth=4), dict(enabled_before=4),
                               dict(enabled_before=True), dict(padding=256)])
def test_invalid_configuration(kw):
    with pytest.raises(ValueError):
        spipacked.preload_plan(b'a', **kw)


def test_plan_preserves_other_enable_and_has_no_header_or_clear():
    plan = spipacked.preload_plan(bytes(range(32)), enabled_before=3)
    assert plan['disable_writes'] == [(sim.A_CTRL, 2)]
    assert plan['start_writes'] == [(sim.A_CTRL, 0x103)]
    assert plan['register_writes'] == [(sim.core_reg(0, sim.C_R0), 255),
                                      (sim.core_reg(0, sim.C_R0 + 1), 0)]
    assert len(plan['tx_words']) == plan['rx_words'] == 8
    assert plan['tx_words'] == spipacked.encode(bytes(range(32)))[1:]
    # No plan action clears FIFO state or fabricates completion.
    assert all(not v & (1 << 16) for a, v in plan['disable_writes'] + plan['start_writes'])


def test_bytes_like_plan_is_an_independent_copy():
    payload = bytearray(b'abcde')
    plan = spipacked.preload_plan(memoryview(payload))
    payload[:] = b'xxxxx'
    assert plan['tx_words'] == spipacked.encode(b'abcde')[1:]


def silence_oracle():
    # Load only the maintained pure verifier, without importing cocotb or
    # discovering native tests. Model-only installations remain supported.
    import ast
    path = FW.parents[1] / 'test/test_spi_preloaded_host.py'
    tree = ast.parse(path.read_text())
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                and n.name == 'verify_host_silence')
    namespace = {}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), namespace)
    return namespace['verify_host_silence']


@pytest.mark.parametrize('mutation', ['none', 'edge', 'selected', 'start_tail'])
def test_silence_oracle_includes_trailing_start(mutation):
    row = dict(host_pin_trace=[[0, 1, 1, 0], [100, 0, 1, 0], [200, 1, 1, 0]],
               transactions=[dict(phase='start', start_ps=1, end_ps=99),
                             dict(phase='drain', start_ps=201, end_ps=300)],
               observation_end_ps=301)
    if mutation == 'edge':
        row['host_pin_trace'].insert(2, [150, 0, 1, 1])
    elif mutation == 'selected':
        row['host_pin_trace'][1][2] = 0
    elif mutation == 'start_tail':
        row['transactions'][0]['end_ps'] = 101
    verifier = silence_oracle()
    if mutation == 'none':
        assert verifier(row)['active_host_transactions'] == 0
    else:
        with pytest.raises(AssertionError):
            verifier(row)
