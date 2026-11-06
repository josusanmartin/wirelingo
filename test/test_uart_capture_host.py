"""Two declared UART captures through the paid serial host, actual top RTL.

Requires the committed uart_rx_capture.pasm/pe.uartcapture dependency.
One fixed nominal 50MHz/115200 source; no general baud/stream qualification.
"""
import bisect
from fractions import Fraction
import json
import os
from pathlib import Path
import sys

import cocotb
from cocotb.triggers import Timer, ValueChange

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from pe import asm, host, sim, uartcapture
from pe.profile import TAPEOUT
from test_spi_packed_host import (ObservedSpi, artifact_paths, bounded_clock,
                                 load_bursts, now_ps)
from test_spi_preloaded_host import run_native

ROOT = Path(__file__).resolve().parents[1]
F_CLK, BAUD, RX = 50_000_000, 115200, 16
CYCLE_PS = 20000
BIT_CYCLES = Fraction(F_CLK, BAUD)
DIVIDER = host.clkdiv_for(F_CLK, 8 * BAUD)
CAPTURES = [dict(start=24000, service=164000, data=bytes.fromhex(
    '00ff55aa5ac319e701020408102040807e813cc19669123456789abcdef027d8'), bad_final=True),
    dict(start=190000, service=204100, data=bytes.fromhex('39d381'), bad_final=False)]


def source_plan():
    """Absolute rational 8N1 slots, rounded only to simulator picoseconds."""
    frames, events, level = [], [], 1
    for capture in CAPTURES:
        for j, byte in enumerate(capture['data']):
            begin = Fraction(capture['start']) + j * 10 * BIT_CYCLES
            stop = 0 if capture['bad_final'] and j == len(capture['data']) - 1 else 1
            bits = [0] + [(byte >> k) & 1 for k in range(8)] + [stop]
            slots = []
            for k, value in enumerate(bits):
                cycle = begin + k * BIT_CYCLES
                ps = round(cycle * CYCLE_PS)
                slots.append(dict(kind='start' if k == 0 else 'stop' if k == 9 else 'data',
                                  level=value, begin_ps=ps,
                                  end_ps=round((cycle + BIT_CYCLES) * CYCLE_PS),
                                  cycle_numerator=cycle.numerator, cycle_denominator=cycle.denominator))
                if value != level:
                    events.append(dict(time_ps=ps, value=value))
                    level = value
            frames.append(dict(capture_start=capture['start'], index=j, byte=byte, slots=slots))
        if level != 1:
            events.append(dict(time_ps=round((Fraction(capture['start']) +
                                             len(capture['data']) * 10 * BIT_CYCLES) * CYCLE_PS), value=1))
            level = 1
    return dict(clock_hz=F_CLK, baud=BAUD, initial_level=1, frames=frames, events=events,
                captures=[{**c, 'data':list(c['data'])} for c in CAPTURES])


async def until_cycle(cycle):
    """Host schedule uses only fixed absolute simulator time, never DUT state."""
    delay = cycle * CYCLE_PS - now_ps()
    assert delay >= 0, ('fixed service time already missed', cycle, now_ps())
    if delay:
        await Timer(delay, unit='ps')


async def drive_source(spi, plan, emitted):
    spi.set_low_inputs(1)
    for event in plan['events']:
        delay = event['time_ps'] - now_ps()
        assert delay >= 0, 'source missed absolute time'
        if delay:
            await Timer(delay, unit='ps')
        spi.set_low_inputs(event['value'])  # Share shadow, preserve ui[7:5].
        emitted.append(dict(time_ps=now_ps(), value=event['value']))


class HostRxPins:
    """Observe actual UI RX/host CS/SCK transitions only, including setup."""
    def __init__(self, dut):
        self.dut, self.trace = dut, []

    def observe(self):
        ui = int(self.dut.ui_in.value)
        point = [now_ps(), ui & 1, (ui >> 7) & 1, (ui >> 6) & 1]
        if not self.trace or point[1:] != self.trace[-1][1:]:
            self.trace.append(point)

    async def run(self):
        # Settle the queued initial UI write before strict pin conversion.
        await Timer(1, unit='ps')
        self.observe()
        while True:
            await ValueChange(self.dut.ui_in)
            await Timer(1, unit='ps')
            self.observe()


def verify_quiet(row, trace):
    """Finite physical-pin and complete API-span silence, not an RTL theorem."""
    begin, end = row['arm_return_ps'], row['service_begin_ps']
    assert begin < end
    times = [p[0] for p in trace]
    index = bisect.bisect_right(times, begin) - 1
    assert index >= 0
    assert trace[index][2:] == [1, 0], 'host not idle at arm return'
    for point in trace[index + 1:]:
        if point[0] >= end:
            break
        assert point[2:] == [1, 0], 'host edge/selection inside quiet interval'
    for t in row['transactions']:
        finish = t.get('end_ps', row['observation_end_ps'])
        assert not (t['start_ps'] < end and finish > begin), 'host API overlaps quiet interval'
    return dict(begin_ps=begin, end_ps=end, active_host_transactions=0)


def verify_capture(row, trace, plan):
    """Decode raw physical RX slots independently of received words/success flags."""
    times = [p[0] for p in trace]
    wire = []
    for frame in plan['frames']:
        if frame['capture_start'] != row['source_start']:
            continue
        observed = []
        for slot in frame['slots']:
            midpoint = (slot['begin_ps'] + slot['end_ps']) // 2
            index = bisect.bisect_right(times, midpoint) - 1
            assert index >= 0 and trace[index][1] == slot['level'], 'physical RX source framing'
            observed.append(trace[index][1])
        assert observed[0] == 0
        wire.append(sum(observed[k+1] << k for k in range(8)))
    expected = bytes(row['expected'])
    assert bytes(wire) == expected, 'independent physical RX payload'
    assert uartcapture.decode_capture(len(expected), row['received_words']) == expected, 'raw capture payload'
    nwords = (len(expected) + 3) // 4
    st = row['ready_status']
    assert st & (1 << 13) and not st & (1 << 14), 'capture must HALT without fault'
    assert st & 255 == nwords << 4, 'exact RX occupancy and empty TX required'
    assert row['flags'] & 1 == int(row['bad_final']), 'sticky F0 framing result'
    assert row['final_status'] & 255 == 0, 'FIFO leftovers after exact drain'
    assert row['final_status'] & (1 << 13) and not row['final_status'] & (1 << 14)
    if len(expected) == 3:
        assert row['received_words'] == [0x81D33900], 'exact high-aligned partial word'
    return dict(wire=list(wire), stop_levels=[f['slots'][-1]['level'] for f in plan['frames']
                                             if f['capture_start'] == row['source_start']])


async def cold_load(dut, spi, program, row):
    start = now_ps()
    dut.ena.value = 1
    dut.rst_n.value = 0
    dut.ext_oe.value = dut.ext_val.value = dut.pull_up.value = 0
    await Timer(10 * CYCLE_PS, unit='ps')
    dut.rst_n.value = 1
    await Timer(5 * CYCLE_PS, unit='ps')
    assert (await spi.read(sim.A_ID))[0] == sim.ID_VALUE
    await spi.write(sim.A_CTRL, [1 << 16])  # The sole cold FIFO clear.
    await load_bursts(spi, [(sim.A_POE, 0), (sim.A_SYNCBYP, 0)] + host.pin_owner_writes({RX:0}))
    writes = host.load_program(0, program, profile=TAPEOUT, in_pins=RX, clkdiv=DIVIDER)
    await load_bursts(spi, writes)
    configuration = writes[-6:]
    configuration_values = [v for _,v in configuration]
    # Core configuration is write-only/read-zero; retain the paid six-word read.
    configuration_readback = await spi.read(configuration[0][0], 6)
    row.update(configuration_write_values=configuration_values,
               configuration_readback=configuration_readback)
    assert configuration_readback == [0] * 6, 'write-only configuration must read zero'
    submitted = [t for t in spi.transactions
                 if t['command'] == host.CMD_WRITE and t['address'] == configuration[0][0]]
    assert len(submitted) == 1, 'expected one completed configuration write frame'
    row['configuration_submitted_frame'] = list(submitted[0]['frame'])
    assert row['configuration_submitted_frame'] == list(host.spi_frame(
        host.CMD_WRITE, configuration[0][0], configuration_values)), 'submitted configuration frame'
    assert (await spi.read(sim.A_SYNCBYP))[0] == 0
    row.update(system_clocks=(now_ps()-start)/CYCLE_PS, transactions=list(spi.transactions),
               program_bytes=4*len(program.words),
               reset_count=1, program_load_count=1, fifo_clear_count=1)


async def arm(spi, row):
    row['arm_begin_ps'] = now_ps()
    spi.phase_label = row['label'] + ':precondition_status'
    status = (await spi.read(sim.core_reg(0,sim.C_STATUS)))[0]
    spi.phase_label = row['label'] + ':precondition_ctrl'
    ctrl = (await spi.read(sim.A_CTRL))[0]
    row.update(precondition_status=status, precondition_ctrl=ctrl)
    assert status & 255 == 0, 'both FIFOs must be empty before arm'
    assert not ctrl & 1 or status & (1 << 13), 'target must be stopped/disabled'
    assert not status & (1 << 14) and not ctrl & 2, 'no fault, core1 disabled'
    writes = uartcapture.capture_plan(len(row['expected']), enabled_before=ctrl & 3)
    row['arm_writes'] = writes
    # Exactly disable, adjacent R1/R2, FCLR1, restart/enable, in order.
    for label, (address, values) in zip(('disable','counts','fclr1','start'),host.coalesce(writes)):
        spi.phase_label = row['label'] + ':' + label
        await spi.write(address, values)
    row['arm_return_ps'] = now_ps()
    row['arm_system_clocks'] = (now_ps()-row['arm_begin_ps'])/CYCLE_PS


@cocotb.test()
async def test_capture_rearm(dut):
    assert float(os.environ.get('PE_CLK_NS','20')) == 20
    assert [int(os.environ.get(k,v)) for k,v in zip(
        ('PE_NCORES','PE_NBANKS','PE_BANK_AW','PE_FIFO_LOG2'),('2','2','8','3'))] == [2,2,8,3]
    root, budget_path, output_path = artifact_paths(230000)
    ledger = json.loads(budget_path.read_text())
    assert ledger['limit'] <= 230000
    plan = source_plan()
    result = dict(profile='2cores/2banks256/depth8', clock_hz=F_CLK, baud=BAUD,
                  divider=DIVIDER, host_sck_system_clocks=8, source=plan,
                  source_emitted=[], pin_trace=[], cases=[], cold={})
    (root/'predeclared-source.json').write_text(json.dumps(plan,indent=2)+'\n')
    spi = ObservedSpi(dut)
    def save():
        result['actual_rtl_clocks'] = ledger['cycles']
        result['transactions'] = spi.transactions
        budget_path.write_text(json.dumps(ledger)+'\n')
        output_path.write_text(json.dumps(result,indent=2)+'\n')
    clock = cocotb.start_soon(bounded_clock(dut,ledger,budget_path))
    spi.set_low_inputs(1)
    monitor = HostRxPins(dut)
    result['pin_trace'] = monitor.trace
    pins = cocotb.start_soon(monitor.run())
    source = cocotb.start_soon(drive_source(spi,plan,result['source_emitted']))
    try:
        program = asm.assemble((ROOT/'firmware/uart_rx_capture.pasm').read_text(),{'RX':RX})
        assert len(program.words) == 19
        await cold_load(dut,spi,program,result['cold'])
        for index, capture in enumerate(CAPTURES):
            row = dict(label=f'capture-{index+1}', expected=list(capture['data']),
                       bad_final=capture['bad_final'], source_start=capture['start'],
                       scheduled_service=capture['service'], received_words=[], transactions=[])
            result['cases'].append(row)
            first = len(spi.transactions)
            try:
                await arm(spi,row)
                row['arm_start_margin_clocks'] = capture['start']-now_ps()/CYCLE_PS
                # Always save a deadline result before its acceptance assertion.
                if index:
                    row['drain_and_rearm_clocks'] = (now_ps()-result['cases'][0]['service_begin_ps'])/CYCLE_PS
                    row['rearm_deadline_slack'] = 180000-now_ps()/CYCLE_PS
                    save()
                    assert row['rearm_deadline_slack'] >= 0, 'first drain plus second arm misses180000'
                assert row['arm_start_margin_clocks'] > 0, 'arm returned after fixed source START'
                await until_cycle(capture['service'])
                row['service_begin_ps'] = now_ps()
                row['transactions'] = spi.transactions[first:]
                row['observation_end_ps'] = now_ps()
                row['quiet'] = verify_quiet(row,monitor.trace)
                spi.phase_label = row['label'] + ':ready_status'
                row['ready_status'] = (await spi.read(sim.core_reg(0,sim.C_STATUS)))[0]
                spi.phase_label = row['label'] + ':flags'
                row['flags'] = (await spi.read(sim.A_FCLR))[0]
                spi.phase_label = row['label'] + ':drain'
                row['received_words'] = await spi.read(sim.core_reg(0,sim.C_FIFO),
                                                     (len(row['expected'])+3)//4,fixed=True)
                row['drain_return_ps'] = now_ps()
                spi.phase_label = row['label'] + ':empty_status'
                row['final_status'] = (await spi.read(sim.core_reg(0,sim.C_STATUS)))[0]
                row['completion_return_ps'] = now_ps()
                row['completion_system_clocks'] = (now_ps()-row['service_begin_ps'])/CYCLE_PS
                row['arm_to_completion_system_clocks'] = (now_ps()-row['arm_begin_ps'])/CYCLE_PS
                row['observation_end_ps'] = now_ps()
                row['transactions'] = spi.transactions[first:]
                row['oracle'] = verify_capture(row,monitor.trace,plan)
                row['quiet'] = verify_quiet(row,monitor.trace)
                row['complete'] = True
                if index:
                    row['completion_deadline_slack'] = 210000-now_ps()/CYCLE_PS
                    save()
                    assert row['completion_deadline_slack'] >= 0, 'second completion misses210000'
            except BaseException as exc:
                row['failure'] = repr(exc)
                raise
            finally:
                row.update(transactions=spi.transactions[first:],observation_end_ps=now_ps())
                save()
        assert result['source_emitted'] == plan['events']
    except BaseException as exc:
        result['failure'] = repr(exc)
        raise
    finally:
        pins.cancel(); source.cancel(); clock.cancel()
        save()


if __name__ == '__main__':
    run_native(module='test_uart_capture_host',testcase='test_capture_rearm',limit=230000)
