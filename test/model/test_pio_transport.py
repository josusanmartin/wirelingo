"""Packing and instruction-level checks plus DMA ownership/error-path checks."""
import pathlib
import random
import sys
import types
from array import array

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools/micropython'))
sys.path.insert(0, str(ROOT / 'test'))
import quad_spi as quad
from pio_support import PioMachine, program_fixture


def nibbles(payload, count):
    return [(word >> shift) & 15 for word in payload for shift in range(28, -1, -4)][:count]


@pytest.mark.parametrize('n', [1, 2, 7, 8, 31, 256, 4096])
@pytest.mark.parametrize('fixed', [False, True])
def test_quad_frames_match_wire_spec(n, fixed):
    rng = random.Random(n)
    words = [rng.getrandbits(32) for _ in range(n)]
    payload, count = quad.write_frame(0x1234, iter(words), fixed)
    command = 0x4A if fixed else 0x42
    expected = [((command >> bit) & 1) << 3 for bit in range(7, -1, -1)]
    expected += [1, 2, 3, 4]
    expected += [((w >> shift) & 15) for w in words for shift in range(28, -1, -4)]
    assert nibbles(payload, count) == expected
    payload, count = quad.read_frame(0x1234, n, fixed)
    command = 0x4B if fixed else 0x43
    assert nibbles(payload, count) == [((command >> bit) & 1) << 3 for bit in range(7, -1, -1)] + [1, 2, 3, 4] + [0] * (8 + 8 * n)
    # Feed independent response nibbles, including nonzero header samples.
    incoming = [15] * 20 + [(w >> shift) & 15 for w in words for shift in range(28, -1, -4)]
    incoming += [0] * ((-len(incoming)) % 8)
    samples = [sum(incoming[i + j] << (28 - j * 4) for j in range(8))
               for i in range(0, len(incoming), 8)]
    assert quad.read_words(samples, n) == words
    assert quad.read_words([quad.reverse_nibbles(w) for w in samples], n, True) == words


def test_compiled_pio_consecutive_partial_and_full_transfers_with_stalls():
    machine = PioMachine()
    traces = []
    # Keep the same machine state between transfers: PULL must discard no
    # control word and must not mistake the preceding TX padding for one.
    for payload, count in [quad.write_frame(0, [0xDEADBEEF]),
                           (array('I', [quad.serial_byte(b) for b in b'\x02\x08\x0b\x00\x00\x00\x01']), 56),
                           quad.read_frame(0x800, 3), quad.write_frame(17, [0, 0xFFFFFFFF])]:
        pending = [((count - 1) << 16) | int(bool(count % 8)), *payload]
        received, sampled, trace = [], [], []
        previous_sck = machine.sck
        for cycle in range(10000):
            # Deliberately starve TX and fill RX beyond its four-word depth.
            if pending and len(machine.tx) < 4 and cycle % 13 == 0:
                machine.tx.append(pending.pop(0))
            if machine.rx and cycle % 101 == 0:
                received.append(machine.rx.popleft())
            machine.step(machine.output)
            trace.append(machine.sck)
            if machine.sck and not previous_sck:
                sampled.append(machine.output)
            previous_sck = machine.sck
            if len(received) == (count + 7) // 8 + 1:
                break
        else:
            pytest.fail('PIO program did not complete')
        assert received[-1] == 0xFFFFFFFF
        assert sampled == nibbles(payload, count)
        assert machine.sck == 0 and not pending and not machine.tx and not machine.rx
        expected = list(payload)
        if count % 8:
            expected[-1] &= 0xFFFF0000
        assert received[:-1] == expected
        traces.append(trace)
    assert any(traces) and len(program_fixture()['instructions']) <= 32


def test_transport_validation_and_quantized_clock():
    assert quad.gpio_group([2, 3, 4, 5]) == (2, False)
    assert quad.gpio_group([11, 10, 9, 8]) == (8, True)
    for pins in ([2, 4, 3, 5], [2, 3, 4], [45, 46, 47, 48]):
        with pytest.raises(ValueError):
            quad.gpio_group(pins)
    for system in (125000000, 150000000, 200000000):
        pio_hz, divider, actual = quad.clock_config(system, 48000000, 24000000)
        assert actual <= 24000000 and divider == system * 256 // pio_hz
        assert 4 * (divider // 256) * 48000000 >= system
    with pytest.raises(ValueError, match='half'):
        quad.clock_config(150000000, 48000000, 25000000)
    with pytest.raises(ValueError, match='divider'):
        quad.clock_config(80000000, 48000000, 24000000)
    for fn, args in [(quad.write_frame, (0, [])), (quad.write_frame, (0, [-1])),
                     (quad.write_frame, (0, [1 << 32])), (quad.read_frame, (0, 0)),
                     (quad.read_frame, (0, 4097)), (quad.read_frame, (65535, 2))]:
        with pytest.raises(ValueError):
            fn(*args)


@pytest.fixture
def board_api(monkeypatch):
    state = types.SimpleNamespace(events=[], dmas=[], fail_second=False, stall=False,
                                  bad_marker=False, dma_error=False, clock=150000000, ticks=0,
                                  gpio_base=0)

    class Pin:
        OUT, IN = 1, 0
        def __init__(self, gpio, mode=None, value=0):
            self.gpio, self.level = gpio, value
        def init(self, mode, value=0):
            self.level = value
        def __eq__(self, other):
            return isinstance(other, Pin) and self.gpio == other.gpio
        def value(self, value=None):
            if value is None:
                return self.level
            self.level = value
            state.events.append(('pin', self.gpio, value))

    class StateMachine:
        def __init__(self, number):
            self.number, self.enabled = number, False
        def active(self, value=None):
            if value is not None:
                self.enabled = bool(value)
                state.events.append(('sm', value))
            return self.enabled
        def init(self, program, **kwargs):
            state.events.append(('init', kwargs))

    class DMA:
        def __init__(self):
            if state.fail_second and state.dmas:
                raise OSError('no free channel')
            self.number, self.running, self.closed = len(state.dmas), False, False
            self.count, self.ctrl = 0, 0
            state.dmas.append(self)
        def pack_ctrl(self, **kwargs):
            state.events.append(('ctrl', self.number, kwargs))
            return self.number
        def config(self, **kwargs):
            self.configured, self.count, self.ctrl = kwargs, kwargs['count'], kwargs['ctrl']
            state.events.append(('config', self.number))
        def active(self, value=None):
            if value is not None:
                state.events.append(('dma', self.number, value))
                self.running = bool(value)
                if value and self.number == 0 and not state.stall:
                    rx = state.dmas[1]
                    rx.configured['write'][:] = array('I', [0x55555555] * (rx.count - 1) + [0 if state.bad_marker else 0xFFFFFFFF])
                    self.count = rx.count = 0
                    self.running = rx.running = False
            return self.running
        def unpack_ctrl(self, ctrl):
            return dict(ahb_err=state.dma_error, read_err=0, write_err=0)
        def close(self):
            assert not self.running
            self.closed = True
            state.events.append(('close', self.number))

    machine = types.SimpleNamespace(Pin=Pin, freq=lambda: state.clock)
    class PIO:
        def __init__(self, number):
            self.number = number
        def gpio_base(self):
            return Pin(state.gpio_base)
    rp2 = types.SimpleNamespace(DMA=DMA, StateMachine=StateMachine, PIO=PIO)
    def ticks_us():
        state.ticks = (state.ticks + 1000) % (1 << 30)
        return state.ticks
    def ticks_diff(a, b):
        return (a - b + (1 << 29)) % (1 << 30) - (1 << 29)
    time = types.SimpleNamespace(ticks_us=ticks_us, ticks_diff=ticks_diff,
                                 sleep_us=lambda n: state.events.append(('sleep', n)))
    monkeypatch.setitem(sys.modules, 'machine', machine)
    monkeypatch.setitem(sys.modules, 'rp2', rp2)
    monkeypatch.setitem(sys.modules, 'time', time)
    monkeypatch.setitem(sys.modules, 'pio_quad', types.SimpleNamespace(spi_stream='compiled'))
    return state


def transport():
    return quad.QuadSPI([2, 3, 4, 5], [8, 9, 10, 11], 6, 7, 48000000, 5)


def test_dma_order_lifetime_close_and_driver_return_shape(board_api):
    bus = transport()
    assert bus.read(0x800) == 0x55555555
    assert bus.read(0x800, 2, True) == [0x55555555] * 2
    events = board_api.events
    begin = events.index(('config', 1))
    assert events[begin:begin + 6] == [('config', 1), ('config', 0), ('sleep', 1),
                                      ('pin', 7, 0), ('dma', 1, 1), ('dma', 0, 1)]
    controls = [event[2] for event in events if event[0] == 'ctrl']
    assert controls[0]['treq_sel'] == 9 and controls[1]['treq_sel'] == 13
    assert bus._buffers is None and not bus.busy
    bus.close()
    bus.close()
    assert all(dma.closed for dma in board_api.dmas)
    with pytest.raises(RuntimeError, match='closed'):
        bus.read(0x800)


@pytest.mark.parametrize('failure', ['stall', 'bad_marker', 'dma_error'])
def test_dma_failure_aborts_both_channels_and_resets_sm(board_api, failure):
    bus = transport()
    setattr(board_api, failure, True)
    board_api.ticks = (1 << 30) - 5000  # Exercise timeout across ticks wrap.
    with pytest.raises(RuntimeError):
        bus.write(0, [1, 2])
    assert not bus.busy and bus._buffers is None and bus.cs.value() == 1
    assert ('dma', 1, 0) in board_api.events and ('dma', 0, 0) in board_api.events
    assert board_api.events[-2][0] == 'init'
    setattr(board_api, failure, False)
    assert bus.read(0x800) == 0x55555555
    bus.close()


def test_partial_dma_allocation_and_changed_clock_are_safe(board_api):
    board_api.fail_second = True
    with pytest.raises(OSError):
        transport()
    assert board_api.dmas[0].closed
    assert not any(event[0] == 'pin' for event in board_api.events)
    board_api.fail_second = False
    board_api.dmas.clear()
    bus = transport()
    events_before = len(board_api.events)
    board_api.clock = 125000000
    with pytest.raises(RuntimeError, match='clock changed'):
        bus.read(0x800)
    assert len(board_api.events) == events_before
    bus.close()


def test_sdk_gpio_mapping_and_project_clock_guard(board_api):
    pins = types.SimpleNamespace()
    for logical, physical in zip(range(2, 8), range(2, 8)):
        setattr(pins, 'ui_in%d' % logical, types.SimpleNamespace(gpio_num=physical))
    for logical, physical in zip(range(4, 8), (11, 10, 9, 8)):
        setattr(pins, 'uo_out%d' % logical, types.SimpleNamespace(gpio_num=physical))
    tt = types.SimpleNamespace(pins=pins, auto_clocking_freq=48000000)
    bus = quad.QuadSPI.from_tt(tt, 5)
    assert bus.in_reverse and not bus.out_reverse
    before = len(board_api.events)
    tt.auto_clocking_freq = 40000000
    with pytest.raises(RuntimeError, match='project clock changed'):
        bus.read(0x800)
    assert len(board_api.events) == before
    bus.close()


def test_protoemu_delegates_every_low_level_operation():
    from protoemu import ProtoEmu
    tt = types.SimpleNamespace(ui_in=types.SimpleNamespace(value=0))
    pe = ProtoEmu(tt)
    calls = []
    class Backend:
        def write(self, addr, words, fixed):
            calls.append(('write', addr, list(words), fixed))
        def read(self, addr, n, fixed):
            calls.append(('read', addr, n, fixed))
            return 7 if n == 1 else [7] * n
        def xfer(self, data, nread):
            calls.append(('xfer', data, nread))
            return b'\0' * (len(data) + nread)
    pe.transport = Backend()
    pe.write(12, (w for w in (1, 2)), True)
    assert pe.read(12) == 7 and pe.read(12, 2, True) == [7, 7]
    assert pe.xfer(b'abc', 2) == b'\0' * 5
    assert calls == [('write', 12, [1, 2], True), ('read', 12, 1, False),
                     ('read', 12, 2, True), ('xfer', b'abc', 2)]
    assert tt.ui_in.value == 0x80  # Attaching PIO avoids SDK port writes.


def test_bounded_board_benchmark_records_samples_and_checks_id(board_api):
    import transport_bench
    bus = transport()
    pe = types.SimpleNamespace(transport=bus,
                               read=lambda addr, n, fixed: 0x50450002 if n == 1 else [0x50450002] * n)
    result = transport_bench.run(pe, 'declared-test-board', 'declared-test-sdk', repeats=3, sizes=(1, 8))
    assert [row['words'] for row in result['rows']] == [1, 8]
    assert result['rows'][0]['samples_us'] == [1000] * 3
    assert result['measured_sck_hz'] is None and result['in_gpio'] == (8, 9, 10, 11)
    pe.read = lambda addr, n, fixed: 0
    with pytest.raises(RuntimeError, match='corrupted'):
        transport_bench.run(pe, 'board', 'sdk', repeats=1, sizes=(1,))
    with pytest.raises(ValueError, match='bounded'):
        transport_bench.run(pe, 'board', 'sdk', repeats=1001)
    bus.close()


def test_rp2350_high_gpio_map_requires_preconfigured_block(board_api):
    pins = types.SimpleNamespace()
    for logical, physical in zip(range(2, 8), range(19, 25)):
        setattr(pins, 'ui_in%d' % logical, types.SimpleNamespace(gpio_num=physical))
    for logical, physical in zip(range(4, 8), range(37, 41)):
        setattr(pins, 'uo_out%d' % logical, types.SimpleNamespace(gpio_num=physical))
    tt = types.SimpleNamespace(pins=pins, auto_clocking_freq=48000000)
    with pytest.raises(ValueError, match='GPIO window'):
        quad.QuadSPI.from_tt(tt, 5)
    assert not board_api.dmas and not board_api.events
    board_api.gpio_base = 16
    bus = quad.QuadSPI.from_tt(tt, 5)
    assert bus.gpio_base == 16 and bus.in_base == 37 and bus.out_base == 19
    bus.close()


def test_legacy_rp2_build_rejects_high_gpio_without_changing_resources(board_api, monkeypatch):
    monkeypatch.setattr(sys.modules['rp2'], 'PIO', lambda number: object())
    bus = transport()
    assert bus.gpio_base == 0
    bus.close()
    board_api.dmas.clear()
    board_api.events.clear()
    with pytest.raises(ValueError, match='above 29'):
        quad.QuadSPI([19, 20, 21, 22], [37, 38, 39, 40], 23, 24, 48000000, 5)
    assert not board_api.dmas and not board_api.events
