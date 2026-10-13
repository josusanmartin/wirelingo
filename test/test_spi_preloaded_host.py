"""Headerless SPI preload through the real serial host; runnable from checkout.

python test/test_spi_preloaded_host.py --output /tmp/spi-preload-run
Requires cocotb and Icarus already installed; never installs or bootstraps them.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

import cocotb
from cocotb.triggers import ClockCycles, First, Timer, ValueChange
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))

from pe import asm, host, sim, spipacked
from pe.profile import TAPEOUT
from test_spi_packed_host import (CLK_NS, MOSI, SCK, CS, MISO, ObservedSpi,
                                 PinPeer, artifact_paths, bounded_clock,
                                 load_bursts, now_ps, pattern, verify_observation)

ROOT = Path(__file__).resolve().parents[1]
FW = ROOT / 'firmware/spi_master_preloaded_once.pasm'
SALTS = [(0xA5, 0x6D), (0x39, 0xD3)]
WAIT_CLOCKS = 4096
DEFAULT_ENTRIES = ('cocotb_tools._coverage:start_cocotb_library_coverage',
                   'cocotb.logging:_configure',
                   'cocotb._init:init_package_from_simulation',
                   'cocotb._init:run_regression')


def verify_host_silence(row):
    """Physical host pins plus complete API spans, including start hold/gap.

    This finite trace cannot promise silence for arbitrary caller traffic.
    Deliberately includes the trailing start transaction; a fixed drain timer
    is insufficient if protocol CS asserts before that transaction returns.
    """
    trace = row['host_pin_trace']
    assert trace and trace[0][1] == 1, 'initial protocol CS not idle'
    begins, ends = [], []
    previous = trace[0]
    for point in trace[1:]:
        ps, pcs, hcs, hsck = point
        assert ps >= previous[0]
        if not pcs:
            assert hcs == 1, 'host CS active inside protocol CS'
        if pcs == 0 or previous[1] == 0:
            assert point[2:] == previous[2:], 'host edge inside protocol CS'
        if pcs != previous[1]:
            (begins if not pcs else ends).append(ps)
        previous = point
    assert len(begins) == len(ends) == 1, 'incomplete/extra protocol frame'
    begin, end = begins[0], ends[0]
    assert begin < end
    for t in row['transactions']:
        finish = t.get('end_ps', row['observation_end_ps'])
        assert not (t['start_ps'] < end and finish > begin), (
            'host transaction overlaps protocol CS-low', t['phase'],
            t['start_ps'], finish, begin, end)
    return dict(begin_ps=begin, end_ps=end, active_host_transactions=0)


class HostPins:
    """Read only actual ui[7:6]/uo[2] changes, never internal DUT state."""
    def __init__(self, dut):
        self.dut, self.trace = dut, []

    def observe(self):
        ui = int(self.dut.ui_in.value)
        point = [now_ps(), (int(self.dut.uo_out.value) >> 2) & 1,
                 (ui >> 7) & 1, (ui >> 6) & 1]
        if not self.trace or point[1:] != self.trace[-1][1:]:
            self.trace.append(point)
        if not point[1]:
            assert point[2] == 1, 'physical host CS active inside protocol CS'
            if len(self.trace) > 1:
                assert point[2:] == self.trace[-2][2:], 'physical host edge inside protocol CS'

    async def run(self):
        self.observe()
        while True:
            await First(ValueChange(self.dut.ui_in), ValueChange(self.dut.uo_out))
            await Timer(1, unit='ps')
            self.observe()


async def cold_load(dut, spi, program):
    start = now_ps()
    dut.ena.value = 1
    dut.rst_n.value = 0
    dut.ui_in.value = spi.ui
    dut.ext_oe.value = dut.ext_val.value = dut.pull_up.value = 0
    await ClockCycles(dut.clk, 10)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 5)
    assert (await spi.read(sim.A_ID))[0] == sim.ID_VALUE
    await spi.write(sim.A_CTRL, [1 << 16])  # Only cold reset clears FIFO state.
    await load_bursts(spi, [(sim.A_POUT, 1 << CS),
                           (sim.A_POE, (1 << MOSI) | (1 << SCK) | (1 << CS))])
    await load_bursts(spi, [(sim.A_SYNCBYP, 0)] +
                      host.pin_owner_writes({MOSI: 0, SCK: 0, CS: 0, MISO: 0}))
    await load_bursts(spi, host.load_program(
        0, program, profile=TAPEOUT, out_pins=(MOSI, 1), side_pins=SCK,
        in_pins=MISO, clkdiv=3 << 8))
    assert (await spi.read(sim.A_SYNCBYP))[0] == 0
    assert (await spi.read(sim.core_reg(0, sim.C_STATUS)))[0] & 255 == 0
    return dict(system_clocks=(now_ps() - start) / 20000,
                transactions=list(spi.transactions), program_bytes=4 * len(program.words),
                cold_fifo_clear_charged_separately=True)


async def message(dut, spi, monitor, ledger, row, save, salts):
    tx, rx = pattern(32, salts[0]), pattern(32, salts[1])
    plan = spipacked.preload_plan(tx)
    first_tx = len(spi.transactions)
    first_pin = max(0, len(monitor.trace) - 1)
    row.update(length=32, word_bytes=4, payload=list(tx), response=list(rx),
               command_words=plan['tx_words'], header_words=0, received_words=[],
               transactions=[], host_pin_trace=[], wait_clocks=WAIT_CLOCKS)
    peer = PinPeer(dut, spi, rx, ledger)
    task = cocotb.start_soon(peer.run())
    row.update(pin_trace=peer.trace, sample_trace=peer.samples, edge_trace=peer.edges)
    try:
        await Timer(1, unit='ps')
        row['resident_begin_ps'], row['resident_begin_cycle'] = now_ps(), ledger['cycles']
        spi.phase_label = 'disable'
        await spi.apply(plan['disable_writes'])
        spi.phase_label = 'register_setup'
        await spi.apply(plan['register_writes'])  # Actual consecutive CSR writes.
        spi.phase_label = 'register_readback'
        assert await spi.read(sim.core_reg(0, sim.C_R0), 2) == [255, 0]
        spi.phase_label = 'prefill'
        await spi.write(plan['fifo_address'], plan['tx_words'], fixed=True)
        spi.phase_label = 'start'
        await spi.apply(plan['start_writes'])
        row['start_return_ps'], row['start_return_cycle'] = now_ps(), ledger['cycles']
        # Service schedule is ONLY this predeclared timer, not peer completion.
        await ClockCycles(dut.clk, WAIT_CLOCKS)
        row['timer_return_ps'], row['timer_return_cycle'] = now_ps(), ledger['cycles']
        row['transactions'] = spi.transactions[first_tx:]
        row['host_pin_trace'] = list(monitor.trace[first_pin:])
        row['observation_end_ps'] = now_ps()
        row['silence'] = verify_host_silence(row)
        spi.phase_label = 'ready_status'
        st = (await spi.read(sim.core_reg(0, sim.C_STATUS)))[0]
        row['ready_status'] = st
        assert st & 255 == 0x80, 'expected exactly8 RX and0 TX after fixed timer'
        assert st & (1 << 13) and not st & (1 << 14), 'expected HALT/no fault'
        spi.phase_label = 'drain'
        row['received_words'] = await spi.read(plan['fifo_address'], 8, fixed=True)
        row['last_rx_return_ps'] = now_ps()
        assert spipacked.decode(row['received_words'], 32) == rx
        spi.phase_label = 'final_status'
        st = (await spi.read(sim.core_reg(0, sim.C_STATUS)))[0]
        row['final_status'] = st
        assert st & 255 == 0 and st & (1 << 13) and not st & (1 << 14)
        spi.phase_label = 'final_pout'
        pout = (await spi.read(sim.A_POUT))[0]
        assert pout & ((1 << CS) | (1 << SCK)) == 1 << CS
        row.update(final_pout=pout, completion_return_ps=now_ps(),
                   resident_system_clocks=(now_ps() - row['resident_begin_ps']) / 20000,
                   wire_end_cycle=peer.wire_end)
        row['deadline_slack'] = 12000 - row['resident_system_clocks']
        row['deadline_pass'] = row['deadline_slack'] >= 0
        row['oracle'] = verify_observation(row)
        row['host_pin_trace'] = list(monitor.trace[first_pin:])
        row['transactions'] = spi.transactions[first_tx:]
        row['observation_end_ps'] = now_ps()
        row['silence'] = verify_host_silence(row)
        row['final_cs_hold_clocks'] = peer.wire_end - peer.edges[-1][0]
        row['complete'] = True
    finally:
        row.update(wire_end_cycle=peer.wire_end,
                   host_pin_trace=list(monitor.trace[first_pin:]),
                   transactions=spi.transactions[first_tx:], observation_end_ps=now_ps())
        task.cancel()
        save()


@cocotb.test()
async def test_preloaded_twice(dut):
    assert float(os.environ.get('PE_CLK_NS', '20')) == CLK_NS
    assert [int(os.environ.get(k, v)) for k, v in zip(
        ('PE_NCORES','PE_NBANKS','PE_BANK_AW','PE_FIFO_LOG2'),('2','2','8','3'))] == [2,2,8,3]
    _, budget_path, output_path = artifact_paths(100000)
    ledger = json.loads(budget_path.read_text())
    assert ledger['limit'] <= 100000
    result = dict(profile='2cores/2banks256/depth8', clock_ns=20,
                  host_sck_system_clocks=8, cases=[], cold=None, host_pin_trace=[])
    def save():
        result['actual_rtl_clocks'] = ledger['cycles']
        budget_path.write_text(json.dumps(ledger) + '\n')
        output_path.write_text(json.dumps(result, indent=2) + '\n')
    clock = cocotb.start_soon(bounded_clock(dut, ledger, budget_path))
    pins_task = None
    spi = ObservedSpi(dut)
    try:
        program = asm.assemble(FW.read_text(), dict(CPOL=0, WORD_BITS=32, CS=CS))
        result['cold'] = await cold_load(dut, spi, program)
        monitor = HostPins(dut)
        result['host_pin_trace'] = monitor.trace
        pins_task = cocotb.start_soon(monitor.run())
        await Timer(1, unit='ps')
        for index, salts in enumerate(SALTS):
            row = dict(label=f'preloaded-{index + 1}')
            result['cases'].append(row)
            await message(dut, spi, monitor, ledger, row, save, salts)
    finally:
        if pins_task is not None:
            pins_task.cancel()
        clock.cancel()
        save()


def record_identity(argv):
    """Observation-only pygpi hook, followed by the original four delegates."""
    import encodings, pygpi, cocotb.simulator
    root = Path(os.environ['PACKED_HOST_CHECK_DIR'])
    raw = Path('/proc/self/maps').read_text()
    (root / 'proc-self-maps.txt').write_text(raw)
    paths = sorted({s.split(None, 5)[5] for s in raw.splitlines()
                    if len(s.split(None, 5)) == 6 and s.split(None, 5)[5].startswith('/')})
    mapped = {p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in paths}
    expected = os.environ['LIBPYTHON_LOC']
    assert expected in mapped
    assert list(cocotb.simulator.get_sim_time()) == [0, 0]
    (root / 'identity.json').write_text(json.dumps(dict(
        version=sys.version, executable=sys.executable, prefix=sys.prefix,
        base_prefix=sys.base_prefix, modules={m.__name__:m.__file__ for m in (encodings, pygpi, cocotb)},
        argv=argv, libpython=expected, mapped_files=mapped,
        pygpi_users=os.environ['PYGPI_USERS']), indent=2) + '\n')


def exec_vvp(argv):
    root = Path(os.environ['PACKED_HOST_CHECK_DIR'])
    expected = [os.environ['PULSE_VVP'], '-M', os.environ['PULSE_VPI_DIR'],
                '-m', 'libcocotbvpi_icarus', str(root/'build/sim.vvp'),
                '-none', '+DUMP_FILE='+str(root/'dump-disabled.vcd')]
    assert argv == expected, argv
    assert os.environ['COCOTB_TEST_MODULES'] == os.environ['PULSE_TEST_MODULE']
    assert os.environ['COCOTB_TEST_FILTER'] == os.environ['PULSE_TEST_FILTER']
    child = dict(os.environ)
    for name in ('PYTHONHOME', 'PYTHONEXECUTABLE'):
        child.pop(name, None)
    vvp = Path(argv[0]).resolve()
    suite = vvp.parent.parent
    loader = suite/'lib/ld-linux-x86-64.so.2'
    # Existing OSS wrapper requires its loader/libs but has an incompatible
    # PYTHONHOME. This private child adapter mirrors it, without editing it.
    if loader.exists() and (suite/'libexec/vvp').exists():
        actual = [str(loader), '--inhibit-cache', '--inhibit-rpath', '',
                  '--library-path', str(suite/'lib'), str(suite/'libexec/vvp')]+argv[1:]
    else:
        actual = argv
    (root/'sim-exec.json').write_text(json.dumps(dict(argv=actual,
        environment={k:child.get(k) for k in ['PYGPI_USERS','PYGPI_PYTHON_BIN','LIBPYTHON_LOC',
            'COCOTB_TEST_MODULES','COCOTB_TEST_FILTER','PYTHONPATH','PE_CLK_NS','PE_NCORES','PE_NBANKS','PE_FIFO_LOG2']}),indent=2)+'\n')
    os.execve(actual[0], actual, child)


def run_native(module='test_spi_preloaded_host', testcase='test_preloaded_twice', limit=100000):
    ap = argparse.ArgumentParser(description='One focused actual-top SPI regression')
    ap.add_argument('--output', type=Path)
    ap.add_argument('--icarus-bin', type=Path)
    args = ap.parse_args()
    root = args.output.resolve() if args.output else Path(tempfile.mkdtemp(prefix='wirelingo-spi-native-'))
    root.mkdir(parents=True, exist_ok=True)
    assert not any(root.iterdir()), 'output directory must be fresh/empty'
    (root/'tmp').mkdir()
    (root/'clocks.json').write_text(json.dumps(dict(cycles=0, limit=limit))+'\n')
    import cocotb_tools.config
    vpi = subprocess.check_output([sys.executable,'-m','cocotb_tools.config','--lib-dir'],text=True).strip()
    libpython = subprocess.check_output([sys.executable,'-m','cocotb_tools.config','--libpython'],text=True).strip()
    binary = shutil.which('iverilog')
    bindir = args.icarus_bin or (Path(binary).parent if binary else None)
    if bindir is None:
        raise RuntimeError('Icarus not installed; API/model tests remain available')
    filter_ = '^'+module+'[.]'+testcase+'$'
    env = {**os.environ, 'PATH':str(Path(sys.executable).parent)+':'+str(bindir)+':'+os.environ['PATH'], 'PYTHONDONTWRITEBYTECODE':'1','PYTHONPATH':str(ROOT/'test')+':'+str(ROOT/'tools'),
           'PACKED_HOST_CHECK_DIR':str(root),'PACKED_HOST_BUDGET':str(root/'clocks.json'),
           'PACKED_HOST_RESULTS':str(root/'observations.json'),'TMPDIR':str(root/'tmp'),
           'PE_CLK_NS':'20','PE_NCORES':'2','PE_NBANKS':'2','PE_BANK_AW':'8','PE_FIFO_LOG2':'3',
           'PYGPI_PYTHON_BIN':sys.executable,'LIBPYTHON_LOC':libpython,
           'PYGPI_USERS':','.join(('test_spi_preloaded_host:record_identity',)+DEFAULT_ENTRIES),
           'PULSE_VVP':str(bindir/'vvp'),'PULSE_VPI_DIR':vpi,
           'PULSE_TEST_MODULE':module,'PULSE_TEST_FILTER':filter_}
    argv = ['timeout','--signal=TERM','--kill-after=2s','108s','make','-j1',
            f'PWD={ROOT}/test',f'PYTHON_BIN={sys.executable}',f'ICARUS_BIN_DIR={bindir}',
            f'PYTHONPATH={env["PYTHONPATH"]}',f'LIBPYTHON_LOC={libpython}',
            'SIM=icarus','TB=top','GATES=no','IMEM=sram','PE_NCORES=2','PE_NBANKS=2','PE_FIFO_LOG2=3',
            'PE_CLK_NS=20',f'SIM_BUILD={root}/build',f'COCOTB_RESULTS_FILE={root}/results.xml',
            f'COCOTB_TEST_MODULES={module}',f'COCOTB_TEST_FILTER={filter_}','COCOTB_TESTCASE=',
            'FST=',f'COCOTB_PLUSARGS=-none +DUMP_FILE={root}/dump-disabled.vcd',
            f'SIM_CMD_PREFIX={sys.executable} {Path(__file__)} --exec-vvp']
    (root/'command.json').write_text(json.dumps(dict(argv=argv,environment={k:env[k] for k in env if k.startswith(('PE_','PYGPI','LIBPYTHON','PACKED','PULSE'))}),indent=2)+'\n')
    start = time.monotonic()
    with (root/'run.log').open('w') as f:
        proc = subprocess.run(argv,cwd=ROOT/'test',env=env,stdout=f,stderr=subprocess.STDOUT)
    log = (root/'run.log').read_text()
    suppressed = 'VCD info: dumping is suppressed.' in log and 'dumpfile' not in log.lower()
    waves = [str(p) for p in root.rglob('*') if p.suffix in ('.vcd','.fst','.lxt','.lxt2')]
    tests = [dict(t.attrib) for t in ET.parse(root/'results.xml').iter('testcase')] if (root/'results.xml').exists() else []
    exact = len(tests) == 1 and tests[0].get('name') == testcase and tests[0].get('classname') == module
    (root/'receipt.json').write_text(json.dumps(dict(make_exit=proc.returncode,
         elapsed_seconds=time.monotonic()-start,clocks=json.loads((root/'clocks.json').read_text()),
         suppression=suppressed,waveforms=waves,selected_tests=tests,exact_test=exact),indent=2)+'\n')
    print('Artifacts:',root,'make exit:',proc.returncode)
    if proc.returncode or not suppressed or waves or not exact:
        print(log[-5000:]); raise SystemExit(proc.returncode or 1)


if __name__ == '__main__':
    if sys.argv[1:2] == ['--exec-vvp']:
        exec_vvp(sys.argv[2:])
    else:
        run_native()
