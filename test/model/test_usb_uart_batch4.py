"""Proposed fixed4 pin-driven LS USB/UART witness, preparation only.

Codec/peer observer mechanics copied from the accepted one-byte maintained
test; expected reports, READY/ERROR/timing oracle and scenario are independent.
Pytest tmp_path is default; a nonempty WIRELINGO_EVIDENCE selects fresh output.
No model result exists for this schedule until an explicit future slot.
"""
import json
import os
from fractions import Fraction
from pathlib import Path

from pe import sim, usb, usbdev, usb_uart_batch4
from pe.modelhost import ModelHost
from pe.profile import TAPEOUT
from pe.usbhost import ControlHost, Stall
from line_echo import EchoProbe

BIT = 32
UART_BIT = Fraction(1250, 3)
REACTION = 8
RESET = (144, 4800)
REPORTS = (bytes(4), bytes.fromhex("39d3815e"))
COLD_WRITES = 339
# Source calendar T(n)=340+13*n+floor(5*n/256), normal restart only.
# H=IN_START+1155; word-boundary F6-clear S=H+935. First wait_a poll
# has tick index1286. Exact4 service flips its polling tick parity.
# Three fixed attempts discriminate source edge +/-1 ambiguity without
# pilot/model feedback or retuning. Fraction236/245/254 targets a53cl span.
PHASES = (("zero-first", 118185, 120275, 236),
          ("zero-lost-retry", 132206, 134297, 245),
          ("zero-malformed-retry", 146230, 148319, 254))
ZERO_SOURCE = ((406, 414, 4581), (4442, 4589, 8756),
               (8621, 8764, 12931), (12800, 12939, 17106))

def crc16(payload):
    """Independent reflected USB CRC16, not the peer/firmware helper."""
    value = 0xffff
    for byte in payload:
        value ^= byte
        for _ in range(8):
            value = (value >> 1) ^ (0xa001 if value & 1 else 0)
    return (value ^ 0xffff).to_bytes(2, "little")


def data(payload, toggle=1):
    return bytes((0x4b if toggle else 0xc3,)) + payload + crc16(payload)


def descriptor(payload):
    return [b"\xd2"] + [data(payload[i:i+8], 1 ^ ((i//8) & 1))
                         for i in range(0, len(payload), 8)] + [b"\xd2"]


EXPECTED = {}
for prefix in ("enum-", "reenum-"):
    EXPECTED.update({prefix+"device": descriptor(usbdev.DEVICE_DESC),
                     prefix+"address": [b"\xd2", data(b"")],
                     prefix+"config-descriptor": descriptor(usbdev.CONFIG_DESC),
                     prefix+"configure": [b"\xd2", data(b"")],
                     prefix+"get-configuration": [b"\xd2", data(b"\x01"), b"\xd2"]})
# Chronology is explicit; dictionary insertion order is not the stream oracle.
ORDER = ["enum-device", "enum-address", "enum-config-descriptor",
         "enum-configure", "enum-get-configuration", "zero-setup",
         "zero-first", "zero-lost-retry", "zero-malformed-retry",
         "descriptor-retain", "length3-refused", "deconfigure", "reconfigure",
         "reenum-device", "reenum-address", "reenum-config-descriptor",
         "reenum-configure", "reenum-get-configuration", "zero-recover",
         "status-zero", "empty-after-zero", "mixed-first",
         "error-descriptor-retain", "mixed-reset-recover", "status-mixed",
         "incomplete-refused"]
EXPECTED.update({"zero-setup": [b"\xd2"],
                 "zero-first": [data(REPORTS[0])],
                 "zero-lost-retry": [data(REPORTS[0])],
                 "zero-malformed-retry": [data(REPORTS[0])],
                 "descriptor-retain": descriptor(usbdev.DEVICE_DESC),
                 "length3-refused": [b"\xd2", b"\x1e"],
                 "deconfigure": [b"\xd2", data(b"")],
                 "reconfigure": [b"\xd2", data(b"")],
                 "zero-recover": [b"\xd2", data(REPORTS[0])],
                 "status-zero": [b"\xd2"],
                 "empty-after-zero": [b"\xd2", b"\x1e"],
                 "mixed-first": [b"\xd2", data(REPORTS[1])],
                 "error-descriptor-retain": descriptor(usbdev.CONFIG_DESC),
                 "mixed-reset-recover": [b"\xd2", data(REPORTS[1])],
                 "status-mixed": [b"\xd2"],
                 "incomplete-refused": [b"\xd2", b"\x1e"]})
assert set(ORDER) == set(EXPECTED)

def raw_decode(raw):
    """Decode rising-clock resolved pads, independent of ControlHost/usb.decode."""
    # Two observations at a clock can reflect a source change; last value wins.
    edges = sorted(dict(raw).items())
    start = next((t for t, value in edges if value == 1), None)
    assert start is not None, "packet has no first K"
    se0 = next((t for t, value in edges if t > start and value == 0), None)
    assert se0 is not None, "packet missing EOP SE0"
    end_j = next((t for t, value in edges if t > se0 and value == 2), None)
    assert end_j is not None and end_j-se0 == 2*BIT, "packet EOP duration"
    levels = []
    cursor = 0
    for t in range(start + BIT//2, se0, BIT):
        while cursor+1 < len(edges) and edges[cursor+1][0] <= t:
            cursor += 1
        value = edges[cursor][1]
        assert value in (1, 2), "illegal packet level"
        levels.append(value)
    bits, previous, ones = [], 2, 0
    for level in levels:
        bit = int(level == previous)
        previous = level
        if ones == 6:
            assert bit == 0, "packet stuffing error"
            ones = 0
            continue
        ones = ones+1 if bit else 0
        bits.append(bit)
    assert bits[:8] == [0, 0, 0, 0, 0, 0, 0, 1], "packet SYNC"
    payload = bits[8:]
    packet = None
    if len(payload) % 8 == 0:
        packet = bytes(sum(payload[i+j] << j for j in range(8))
                       for i in range(0, len(payload), 8))
        assert packet and packet[0] & 15 == (~(packet[0] >> 4) & 15), "PID complement"
        if packet[0] in (0x4b, 0xc3):
            assert len(packet) >= 3 and packet[-2:] == crc16(packet[1:-2]), "DATA CRC"
    return dict(packet=packet, bits=bits, start=start, eop_j=end_j)


def at(edges, t):
    value = edges[0][1]
    for when, sample in edges:
        if when > t:
            break
        value = sample
    return value


class Host(ModelHost):
    def __init__(self):
        self.chip = sim.Chip(ncores=TAPEOUT.ncores, nbanks=TAPEOUT.nbanks,
                             bank_words=TAPEOUT.bank_words, fifo_depth=TAPEOUT.fifo_depth)
        self.echo = EchoProbe(self.chip, lambda: self.ext_oe & 3)  # line-RX overflow is own echo only
        self.ui, self.uio, self.ext_oe, self.pull_up = 1, 0, 0, 2


class Rig:
    APIS = ("write", "read", "snap", "commit", "cancel", "push", "pop_all", "apply", "start", "tx_level")

    def __init__(self, programs):
        self.mh = Host()
        c = self.mh.chip
        assert (c.ncores, c.nbanks, c.bank_words, c.fifo_depth) == (2, 2, 256, 8)
        self.stage, self.active, self.calls = "cold-load", False, 0
        self.usb, self.uart, self.api_attempts, self.active_strobes = [], [], [], []
        self.bus_edges, self.rx_edges, self.ready_edges, self.error_edges = [], [], [], []
        self.states, self.overflow, self.overlap, self.flag_edges = [], [], [], []
        self.consumer_events, self.producer_events, self.consumed = [], [], []
        self.grant_used, self.previous_frame_end = -1, 0
        self.usb_labels = programs[0].labels
        self.uart_labels = {k: v+256 for k, v in programs[1].labels.items()}
        self.watch0 = {self.usb_labels['got_in']+2, self.usb_labels['s_bit'],
                       self.usb_labels['get_word'], self.usb_labels['get_word']+2,
                       self.usb_labels['get_word']+1, self.usb_labels['get_word']+3}
        # OUT/FLAG and both poll instructions at each supplier boundary.
        self.watch1 = set(range(256+30, 256+59))
        original = c.step

        def observed(*args, **kw):
            self.calls += 1
            assert self.calls <= 1000000, "model step cap"
            before = c.cycle
            strobes = {name: kw.get(name, args[index] if len(args)>index else False)
                       for name, index in (("we", 2), ("commit", 5), ("snap", 6), ("cancel", 7))}
            if self.active and any(strobes.values()):
                self.active_strobes.append(dict(cycle=before, signals=strobes))
                raise AssertionError("active loading-host strobe")
            self.edge(self.bus_edges, before, args[1] & 3)
            self.edge(self.rx_edges, before, args[0] & 1)
            self.edge(self.ready_edges, before, c.outputs()[0] & 1)
            self.edge(self.error_edges, before, (c.outputs()[0] >> 1) & 1)
            if self.mh.ext_oe & c.outputs()[2] & 3:
                self.overlap.append([before, self.stage, self.mh.ext_oe & 3, c.outputs()[2] & 3])
            q0, q1 = c.cores[:c.ncores]
            oldflags, pinbyte = c.flags, (c.pin_out >> 24) & 255
            own_tx, prev_ovf = bool(c.outputs()[2] & 3), c.stats.get('line:rx_overflow', 0)
            e0 = None
            if self.active and c.enable & 1 and not q0.cnt and not q0.dcnt and q0.pc in self.watch0:
                e0 = [before, self.stage, q0.pc, oldflags, q0.r[0], q0.osr_cnt,
                      len(c.line.entries), [len(x) for x in c.line.entries]]
            e1 = None
            if self.active and c.enable & 2 and not q1.cnt and not q1.dcnt and q1.pc in self.watch1:
                e1 = [before, self.stage, q1.pc, oldflags, q1.facc, pinbyte]
            result = original(*args, **kw)
            if e0 is not None and q0.pc != e0[2]:
                self.consumer_events.append(e0+[q0.pc, c.flags, (c.pin_out >> 24) & 255,
                                                len(c.line.entries), [len(x) for x in c.line.entries]])
            if e1 is not None:
                self.producer_events.append(e1+[q1.pc, c.flags, (c.pin_out >> 24) & 255])
            self.edge(self.bus_edges, c.cycle, self.mh.bus_uio() & 3)
            self.edge(self.ready_edges, c.cycle, c.outputs()[0] & 1)
            self.edge(self.error_edges, c.cycle, (c.outputs()[0] >> 1) & 1)
            self.edge(self.flag_edges, c.cycle, c.flags & 0xc0)
            state = [c.flags, c.enable, (c.pin_out >> 24) & 255, q0.pc, q1.pc]
            if not self.states or state != self.states[-1][1:]:
                self.states.append([c.cycle]+state)
            if c.stats.get('line:rx_overflow', 0) != prev_ovf:
                self.overflow.append([before, self.stage, own_tx, c.stats['line:rx_overflow']])
            return result

        c.step = observed

    @staticmethod
    def edge(rows, cycle, value):
        if not rows or rows[-1][1] != value:
            rows.append([cycle, value])

    def raw(self, begin, end):
        return [[begin, at(self.bus_edges, begin)]]+[r for r in self.bus_edges if begin<r[0]<=end]

    def ready(self):
        return self.mh.chip.outputs()[0] & 1

    def wait_ready(self):
        for _ in range(8192):
            if self.ready():
                return
            self.mh.step()
        raise AssertionError("physical READY timeout")

    def freeze_apis(self):
        self.active = True
        for name in self.APIS:
            def refused(*args, _name=name, **kw):
                self.api_attempts.append([self.mh.chip.cycle, _name])
                raise AssertionError("active loading-host API "+_name)
            setattr(self.mh, name, refused)

    def frame(self, value, good=True):
        self.wait_ready()  # sole source feedback: physical READY
        now = self.mh.chip.cycle
        grant = max(t for t, v in self.ready_edges if v and t <= now)
        assert grant > self.grant_used, "each frame requires a NEW physical READY grant"
        self.grant_used = grant
        # Complete any prior full frame; a grant may rise during its STOP.
        anchor = max(now, grant, self.previous_frame_end)
        self.mh.step(anchor+REACTION-now)
        start = self.mh.chip.cycle
        frame = dict(value=value, good_stop=good, grant=grant, observation=now,
                     reaction_anchor=anchor, start=start, end=start+4167)
        self.uart.append(frame)
        for i, level in enumerate([0]+[(value>>k)&1 for k in range(8)]+[int(good)]):
            self.mh.ui = (self.mh.ui & ~1) | level
            deadline = start+(i+1)*UART_BIT
            end = (deadline.numerator+deadline.denominator-1)//deadline.denominator
            self.mh.step(end-self.mh.chip.cycle)
        self.mh.ui |= 1
        self.previous_frame_end = frame['end']

    def until(self, clock):
        assert self.mh.chip.cycle <= clock, "missed frozen external phase deadline"
        self.mh.step(clock-self.mh.chip.cycle)

    def result(self, error):
        c = self.mh.chip
        return dict(error=error, chip_step_calls=self.calls, advanced_cycles=c.cycle,
                    geometry=[c.ncores,c.nbanks,c.bank_words,c.fifo_depth], sync_bypass=c.sync_bypass,
                    usb=self.usb, uart=self.uart, rx_edges=self.rx_edges, ready_edges=self.ready_edges,
                    error_edges=self.error_edges, flag_edges=self.flag_edges, usb_bus_edges=self.bus_edges,
                    api_attempts=self.api_attempts, active_bus_strobes=self.active_strobes,
                    corroborating_state=self.states, line_rx_overflow=self.overflow,
                    external_ownTX_overlap=self.overlap, stats=c.stats, logical_consumed=self.consumed,
                    consumer_events=self.consumer_events, producer_events=self.producer_events,
                    usb_labels=self.usb_labels, uart_labels=self.uart_labels,
                    final_flags=c.flags, final_enable=c.enable,
                    FIFOs=[[len(x.txf),len(x.rxf)] for x in c.cores[:c.ncores]])




class Peer(ControlHost):
    def __init__(self, rig):
        super().__init__(rig.mh, bit=BIT, gap_bits=2, keepalive=False, retries=0)
        self.rig = rig
        self.response_counts = {name: 0 for name in EXPECTED}

    def _drive(self, states):
        begin = self.mh.chip.cycle
        super()._drive(states)
        end = self.mh.chip.cycle
        bits = usb.nrzi_destuff([usb.J] + states)
        self.rig.usb.append(dict(direction="host", stage=self.rig.stage, begin=begin,
                                 end=end, raw=self.rig.raw(begin,end), source_states=states,
                                 source_bits=bits))

    def _response(self):
        begin = self.mh.chip.cycle
        reply = super()._response()
        end = self.mh.chip.cycle
        self.rig.usb.append(dict(direction="device",stage=self.rig.stage,begin=begin,
                                 end=end,raw=self.rig.raw(begin,end),
                                 helper_reply=None if reply is None else reply.hex()))
        stage = self.rig.stage
        assert stage in EXPECTED, "unplanned response stage"
        index = self.response_counts[stage]
        assert index < len(EXPECTED[stage]), "extra response cannot be hidden by retry"
        assert reply == EXPECTED[stage][index], ("first nominal response failure", stage, index, reply)
        self.response_counts[stage] += 1
        return reply

    def poll(self, address, value):
        self._send(usb.token(usb.PID_IN,address,0))
        reply = self._response()
        assert reply == data(value), (self.rig.stage, reply)
        return reply

    def ack(self, kind="ordinary"):
        self._steps(2)
        states = usb.encode(b"\xd2")
        if kind == "malformed":
            states = states[:-3] + [usb.J if states[-4] == usb.K else usb.K] + states[-3:]
            assert usb.nrzi_destuff([usb.J]+states)[16:] == [0]
        if kind == "dribble":
            states = states[:-3]+[states[-4]]*3+states[-3:]
            assert usb.nrzi_destuff([usb.J]+states)[16:] == [1,1,1]
        self._drive(states)
        self.gap()


def audit(result):
    """Raw pads decide bytes/retention/error/admission; passive trace decides coverage."""
    assert not result['error'], "a failed nominal attempt cannot be repaired by later success"
    assert result['geometry'] == [2, 2, 256, 8] and result['sync_bypass'] == 0
    assert result['api_attempts'] == result['active_bus_strobes'] == []
    assert result['external_ownTX_overlap'] == [], "USB driver overlap"
    received, decoded, stages = {k: [] for k in EXPECTED}, [], []
    for row in result['usb']:
        packet = raw_decode(row['raw'])
        decoded.append(dict(stage=row['stage'], direction=row['direction'],
                            packet=None if packet['packet'] is None else packet['packet'].hex(),
                            unstuffed_bits=len(packet['bits']), start=packet['start'], eop_j=packet['eop_j']))
        if row['direction'] == 'device':
            assert row['stage'] in received and packet['packet'] is not None
            received[row['stage']].append(packet['packet'])
            if not stages or stages[-1] != row['stage']:
                stages.append(row['stage'])
        else:
            assert packet['bits'] == row['source_bits'], "host symbols corrupted on resolved pads"
            if packet['packet'] is None:
                assert row['stage'] in ('malformed-zero-ACK', 'consume-zero')
                suffix = [0] if row['stage'] == 'malformed-zero-ACK' else [1, 1, 1]
                assert packet['bits'] == [0,0,0,0,0,0,0,1,0,1,0,0,1,0,1,1]+suffix
    assert stages == ORDER, "nominal stage lost, reordered or repeated"
    for stage, expected in EXPECTED.items():
        assert received[stage] == expected, "exact packet count/content "+stage
    frames = result['uart']
    assert [(f['value'], f['good_stop']) for f in frames] == [
        (0,True), (0,True), (0,True), (0,True),
        (0x39,True), (0xd3,True), (0xa6,False), (0x81,True), (0x5e,True),
        (0x12,True), (0x34,True)]
    for i, frame in enumerate(frames):
        assert frame['start'] == frame['reaction_anchor']+8
        assert frame['reaction_anchor'] == max(frame['observation'], frame['grant'],
                                              frames[i-1]['end'] if i else 0)
        assert frame['end']-frame['start'] == 4167
        assert at(result['ready_edges'], frame['start']) == 1
        if i:
            assert frame['grant'] > frames[i-1]['grant'] and frame['start'] >= frames[i-1]['end']
        for bit, value in enumerate([0]+[(frame['value']>>k)&1 for k in range(8)]+[int(frame['good_stop'])]):
            assert at(result['rx_edges'], int(frame['start']+(2*bit+1)*UART_BIT/2)) == value
    # These first four pin times were derived from normal load/restart and
    # the exact3333 divider. Any discrepancy is retained, never retuned.
    assert [(f['grant'], f['start'], f['end']) for f in frames[:4]] == list(ZERO_SOURCE)
    assert frames[0]['start'] == frames[0]['grant']+8
    assert sum(v for _,v in result['ready_edges']) == 12
    assert sum(v == 0 for _,v in result['ready_edges'][1:]) == 11
    bad = frames[6]
    error_rises = [t for t,v in result['error_edges'] if v]
    assert len(error_rises) == 1 and bad['start']+9*UART_BIT < error_rises[0] < bad['end']
    assert not any(t >= error_rises[0] and not v for t,v in result['error_edges']), "ERROR not sticky"
    consumption = []
    for last_frame, report, stage in ((frames[3], REPORTS[0], 'consume-zero'),
                                      (frames[8], REPORTS[1], 'consume-mixed')):
        ack = [r for r in decoded if r['stage'] == stage and r['direction'] == 'host']
        assert len(ack) == 1
        end = ack[0]['eop_j']
        assert not any(last_frame['end'] <= t < end and v for t,v in result['ready_edges']), "early report release"
        ready = next(t for t,v in result['ready_edges'] if t >= end and v)
        consumption.append(dict(report=report.hex(), ACK_EOP_J=end, next_READY=ready,
                                ACK_to_READY=ready-end, producer_stall=ready-last_frame['end']))
    assert result['logical_consumed'] == [r.hex() for r in REPORTS]
    # Physical ERROR is primary; F7 corroborates report isolation and no
    # resurrection after the producer's speculative F6 byte.
    f7 = [(t,int(bool(v&128))) for t,v in result['flag_edges']]
    f7 = [r for i,r in enumerate(f7) if not i or r[1] != f7[i-1][1]]
    assert [v for _,v in f7] == [0,1,0,1,0], "F7 resurrection or missing consumed report"
    assert not result['final_flags'] & 128 and at(result['error_edges'], result['advanced_cycles']) == 1
    timing = []
    l0, l1 = result['usb_labels'], result['uart_labels']
    for stage, IN_start, target_poll, fraction in PHASES:
        token = next(r for r in result['usb'] if r['stage'] == stage and r['direction'] == 'host')
        assert token['begin'] == IN_start, "frozen phase START shifted"
        rows = [r for r in result['consumer_events'] if r[1] == stage]
        header = [r for r in rows if r[2] == l0['got_in']+2]
        word1 = [r for r in rows if r[2] == l0['s_bit'] and r[4] == 17]
        word2 = [r for r in rows if r[2] == l0['s_bit'] and r[4] == 16]
        clears = [r for r in rows if r[2] in (l0['get_word'],l0['get_word']+2)]
        assert len(header) == len(word1) == len(word2) == 1 and len(clears) == 4
        C, S, next_OUT = word1[0][0], clears[2][0], word2[0][0]
        assert S == C+5 and next_OUT <= C+63 and next_OUT < C+95, "supplier deadline"
        producer = [r for r in result['producer_events'] if r[1] == stage]
        polls = [r for r in producer if r[2] == l1['wait_c']+1 and r[0] == S and r[3]&64]
        publication = next(r for r in producer if r[0] > S and not r[3]&64 and r[7]&64)
        pins = [r for r in producer if S < r[0] < publication[0] and r[2] == l1['wait_c']+2]
        assert len(pins) == 1, "missing supplier OUT before FLAG"
        timing.append(dict(stage=stage, header_OUT=header[0][0], word1_last_OUT=C, F6_clear=S,
                           producer_pin_OUT=pins[0][0], producer_FLAG=publication[0],
                           publication_span=publication[0]-S, next_OUT=next_OUT,
                           empty_decision_earliest=C+95, same_edge_old_high=bool(polls),
                           poll_fraction=None if not polls else polls[0][4],
                           declared_poll=target_poll, declared_fraction=fraction))
    assert any(t['same_edge_old_high'] and t['publication_span'] == 53 for t in timing), \
        "functional result lacks declared same-edge/53-clock timing coverage"
    return dict(decoded_packets=decoded, consumption=consumption, timing=timing,
                admitted_UART_frames=len(frames), ERROR_rise=error_rises[0],
                USB_transactions=sum(r['direction']=='host' for r in result['usb']))


def test_uart_batch4_control_mailbox(tmp_path):
    out = Path(os.environ.get('WIRELINGO_EVIDENCE') or tmp_path)
    out.mkdir(parents=True, exist_ok=True)
    programs = usb_uart_batch4.assemble()
    rig, error = Rig(programs), None
    try:
        writes = usb_uart_batch4.load_writes(programs)
        assert len(writes) == COLD_WRITES
        rig.mh.apply(writes)
        rig.freeze_apis()
        def stage(name): rig.stage = name
        for _ in range(4):
            stage('UART-zero');rig.frame(0)
        peer = Peer(rig)
        def enumerate_device(prefix):
            stage(prefix+'device');assert peer.get_descriptor(0,1,18) == usbdev.DEVICE_DESC
            stage(prefix+'address');peer.control_write(0,bytes.fromhex('0005050000000000'))
            stage(prefix+'config-descriptor');assert peer.get_descriptor(5,2,18) == usbdev.CONFIG_DESC
            stage(prefix+'configure');peer.control_write(5,bytes.fromhex('0009010000000000'))
            stage(prefix+'get-configuration');assert peer.control_read(5,bytes.fromhex('8008000000000100')) == b'\x01'
        def refused(address, length, name):
            stage(name)
            try: peer.control_read(address,usb_uart_batch4.vendor_request(length))
            except Stall: pass
            else: raise AssertionError('unavailable/refused report produced data')
        def consume(report, name, kind='ordinary'):
            stage(name);peer.ack(kind);rig.wait_ready();rig.consumed.append(report.hex())
        enumerate_device('enum-')
        stage('zero-setup');peer.setup(5,usb_uart_batch4.vendor_request())
        for i,(name,clock,_,_) in enumerate(PHASES):
            rig.until(clock)  # fixed absolute external clock, never flags/PC
            stage(name);peer.poll(5,REPORTS[0]);peer.gap()
            if i == 1:
                stage('malformed-zero-ACK');peer.ack('malformed')
        stage('descriptor-retain');assert peer.get_descriptor(5,1,18) == usbdev.DEVICE_DESC
        refused(5,3,'length3-refused')
        stage('deconfigure');peer.control_write(5,bytes.fromhex('0009000000000000'))
        stage('reconfigure');peer.control_write(5,bytes.fromhex('0009010000000000'))
        stage('zero-detector-reset');peer.bus_reset(cycles=RESET[0],recovery=RESET[1])
        enumerate_device('reenum-')
        stage('zero-recover');peer.setup(5,usb_uart_batch4.vendor_request());peer.poll(5,REPORTS[0])
        consume(REPORTS[0],'consume-zero','dribble')
        stage('status-zero');peer.data_out(5,usb.PID_DATA1,b'')
        refused(5,4,'empty-after-zero')
        for value,good in ((0x39,True),(0xd3,True),(0xa6,False),(0x81,True),(0x5e,True)):
            stage('UART-mixed');rig.frame(value,good)
        stage('mixed-first');peer.setup(5,usb_uart_batch4.vendor_request());peer.poll(5,REPORTS[1]);peer.gap()
        stage('error-descriptor-retain');assert peer.get_descriptor(5,2,18) == usbdev.CONFIG_DESC
        stage('mixed-detector-reset');peer.bus_reset(cycles=RESET[0],recovery=RESET[1])
        stage('mixed-reset-recover');peer.setup(0,usb_uart_batch4.vendor_request());peer.poll(0,REPORTS[1])
        consume(REPORTS[1],'consume-mixed')
        stage('status-mixed');peer.data_out(0,usb.PID_DATA1,b'')
        for value in (0x12,0x34):
            stage('UART-incomplete');rig.frame(value)
        stage('incomplete-idle');rig.mh.step(5000)
        refused(0,4,'incomplete-refused')
        verdict = audit(rig.result(None))
        rig.mh.echo.check()
        (out/'oracle.json').write_text(json.dumps(verdict,indent=2)+'\n')
    except BaseException as exc:
        error = type(exc).__name__+': '+str(exc)
        raise
    finally:
        (out/'dynamic.json').write_text(json.dumps(rig.result(error),indent=2)+'\n')
