"""Two finite pin-level cases: autonomous USB core0 plus declared UART core1.

Run against the combined shipping source tree, not a checkout with stale USB.
No old UART/USB test functions run. Stimulus/service times are fixed before load.
"""
import bisect
import collections
import hashlib
import json
import os
import pathlib

import pytest

from pe import asm, host, isa, sim, uartcapture, uartcapture_peer, usb, usbdev, usbenum
from pe.modelhost import ModelHost
from pe.profile import TAPEOUT
from pe.usbhost import ControlHost, Stall
from line_echo import EchoProbe
from test_uart import _PinTimeline
from test_uart_packed import BURST, DIV, RX
from test_usb_autonomous import TransportIdle, enumerate_flow

FW = pathlib.Path(__file__).resolve().parents[2] / "firmware"
_STEPS = 0


def _encode_bytes(value):
    """Lossless nested evidence tags; unknown object types still fail."""
    if type(value) in (bytes, bytearray):
        return {"__bytes_type__": type(value).__name__, "hex": value.hex()}
    raise TypeError("unsupported evidence object: " + type(value).__name__)


def _save(name, value):
    directory = os.environ.get("USB_UART_TRACE_DIR")
    if directory:
        p = pathlib.Path(directory)
        p.mkdir(parents=True, exist_ok=True)
        (p / (name + ".json")).write_text(json.dumps(value, indent=2, default=_encode_bytes) + "\n")


def _wire(origin, data, bad_final=False):
    source = _PinTimeline(origin, 0, 0)
    for index, byte in enumerate(data):
        source.frame(byte, stop=0 if bad_final and index == len(data)-1 else 1)
    source.finish()
    return source


def _assert_quiet(cycle, strobes, gaps):
    assert not strobes or not any(a <= cycle < b for a, b in gaps), "active loading-host bus strobe"


def _assert_bytes(length, words, expected):
    assert uartcapture_peer.decode_capture(length, words) == bytes(expected), "UART payload mismatch"


def _assert_usb_log(log, expected_stalls):
    ack = usb.handshake(usb.PID_ACK)
    stalls = 0
    for row in log:
        assert "error" not in row, ("nominal USB error", row)
        if row[0] in ("SETUP", "OUT"):
            assert row[-1] == ack, ("first handshake failure", row)
        elif row[0] == "IN":
            if row[2] == "STALL":
                stalls += 1
            else:
                assert row[2] in ("DATA0", "DATA1") and row[-1] == "ack", row
    assert stalls == expected_stalls


@pytest.fixture(autouse=True)
def _count_steps(monkeypatch, request):
    global _STEPS
    begin = _STEPS
    original = sim.Chip.step
    limit = int(os.environ.get("USB_UART_STEP_LIMIT", "500000"))

    def counted(chip, *args, **kwargs):
        global _STEPS
        assert _STEPS < limit, "aggregate USB/UART model-clock cap exhausted"
        _STEPS += 1
        return original(chip, *args, **kwargs)

    monkeypatch.setattr(sim.Chip, "step", counted)
    yield
    used = _STEPS - begin
    request.node.user_properties.append(("model_clocks", used))
    _save("clocks-" + request.node.name,
          {"test": request.node.name, "model_clocks": used, "aggregate_steps": _STEPS})


class _Host(ModelHost):
    """Normal ModelHost fields and paid bus; explicit actual tapeout geometry."""
    def __init__(self, sources):
        self.chip = sim.Chip(ncores=2, nbanks=2, bank_words=256, fifo_depth=8)
        self.echo = EchoProbe(self.chip, lambda: self.ext_oe & 3)  # line-RX overflow is own echo only
        self.ui = 1
        self.uio = self.ext_oe = 0
        self.pull_up = 2
        self.sources = sources
        self.operations, self.gaps = [], []
        self.trace = dict(uart_events=[], publications=[], halts=[], flag_events=[],
                          enable_events=[], controls=[], usb_edges=[], usb_tx_epochs=[],
                          overflow_events=[], peak_rx=[0, 0], peak_tx=[0, 0],
                          uart_shift_stalls=0, active_strobes=0, strobes=0,
                          core_stall_clocks=[0, 0], max_core_stall_run=[0, 0])
        self.tail = collections.deque(maxlen=128)
        self.uart = asm.assemble((FW / "uart_rx_capture_peer.pasm").read_text(), {"RX": RX})
        self.usb = usbenum.assemble("low")
        self.steps = 0
        original = self.chip.step
        stall_runs = [0, 0]
        tx_begin = None

        def observed(ui_in=0, uio_in=0, **kwargs):
            nonlocal tx_begin
            chip, core = self.chip, self.chip.cores[1]
            cycle = chip.cycle
            source, index = self.source_at(cycle)
            sampled, sample_index = self.source_at(cycle-2)
            raw = source.level(cycle)
            logical = (chip.pins_in() >> RX) & 1
            assert chip.sync_bypass == 0
            if cycle >= 2:
                assert logical == sampled.level(cycle-2), "UART input synchronization mismatch"
            strobes = any(kwargs.get(k, False) for k in ("we", "snap", "commit", "cancel"))
            self.trace["strobes"] += int(strobes)
            try:
                _assert_quiet(cycle, strobes, self.gaps)
            except AssertionError:
                self.trace["active_strobes"] += 1
                raise
            if kwargs.get("we") and kwargs.get("addr") == sim.A_CTRL:
                self.trace["controls"].append(dict(cycle=cycle, data=kwargs["wdata"],
                                                    enable_before=chip.enable))
            bus = uio_in & 3
            direction = "peer" if self.ext_oe & 3 else "device" if chip.line.tx_on else "idle"
            edge = [cycle, bus, direction]
            if not self.trace["usb_edges"] or edge[1:] != self.trace["usb_edges"][-1][1:]:
                self.trace["usb_edges"].append(edge)
            pc = core.pc
            op, _, operand = isa.fields(chip.fetch(pc))
            active = chip.enable & 2 and core.cnt == 0 and core.dcnt == 0 and not core.halted
            kind = None
            if active:
                if pc == 256 + self.uart.labels["start"]:
                    kind = "start"
                elif op == isa.OP_IN and isa.bits(operand, 18, 15) == isa.PINS:
                    kind = "data"
                elif pc in (256+self.uart.labels["last"]+3,
                             256+self.uart.labels["final_stop"]+1):
                    kind = "stop"
                elif op == isa.OP_PUSH:
                    kind = "push"
            old_isr, old_bits = core.isr, core.isr_cnt
            old_flags, old_halt, old_enable = chip.flags, core.halted, chip.enable
            old_overflow = chip.stats.get("line:rx_overflow", 0)
            auto = kind == "data" and core.autopush and old_bits == 31
            old_tx = chip.line.tx_on
            before_pc = [c.pc for c in chip.cores[:2]]
            result = original((ui_in & ~1) | raw, uio_in, **kwargs)
            self.steps += 1
            assert chip.cycle == self.steps
            assert not chip.pin_oe & (1 << RX), "UART RX must remain input"
            assert not chip.pin_out & 0xFF000000, "no virtual-pin mailbox"
            if kind and (kind != "start" or core.pc != pc):
                event = dict(kind=kind, cycle=cycle, source_cycle=cycle-2, segment=sample_index,
                             pc=pc, logical=logical, window=sampled.window(cycle-2),
                             stalled=bool(core.stalled))
                self.trace["uart_events"].append(event)
                if (auto or kind == "push") and core.stalled:
                    self.trace["uart_shift_stalls"] += 1
                if (auto or kind == "push") and not core.stalled:
                    word = (old_isr >> 1) | (logical << 31) if auto else old_isr
                    self.trace["publications"].append(dict(cycle=cycle, segment=sample_index,
                                                         word=word, autopush=bool(auto)))
            if chip.flags != old_flags:
                self.trace["flag_events"].append(dict(cycle=cycle, before=old_flags,
                    after=chip.flags, usb_low6=chip.flags & 63, uart_f6=(chip.flags >> 6) & 1,
                    pc=before_pc, host_write=kwargs.get("addr") if kwargs.get("we") else None))
            if chip.enable != old_enable:
                self.trace["enable_events"].append(dict(cycle=cycle,before=old_enable,after=chip.enable))
            if not old_halt and core.halted:
                self.trace["halts"].append(dict(cycle=cycle, segment=index, fault=core.fault))
            if not old_tx and chip.line.tx_on:
                tx_begin = cycle
            if old_tx and not chip.line.tx_on:
                self.trace["usb_tx_epochs"].append([tx_begin, cycle])
                tx_begin = None
            if chip.stats.get("line:rx_overflow", 0) != old_overflow:
                self.trace["overflow_events"].append(dict(cycle=cycle, own_tx=bool(old_tx),
                    peer_tx=bool(self.ext_oe & 3), pc0=before_pc[0]))
            for i, c in enumerate(chip.cores[:2]):
                self.trace["peak_rx"][i] = max(self.trace["peak_rx"][i], len(c.rxf))
                self.trace["peak_tx"][i] = max(self.trace["peak_tx"][i], len(c.txf))
                self.trace["core_stall_clocks"][i] += int(bool(c.stalled))
                stall_runs[i] = stall_runs[i]+1 if c.stalled else 0
                self.trace["max_core_stall_run"][i] = max(self.trace["max_core_stall_run"][i], stall_runs[i])
                assert not c.fault, ("core fault", i, c.pc)
            self.tail.append(dict(cycle=cycle, pc=before_pc, flags=old_flags,
                                  enable=old_enable, rx=[len(c.rxf) for c in chip.cores[:2]],
                                  r=[list(c.r) for c in chip.cores[:2]],
                                  lr=[c.lr for c in chip.cores[:2]], bus=bus))
            return result

        self.chip.step = observed
        assert (self.chip.ncores, self.chip.nbanks, self.chip.bank_words, self.chip.fifo_depth) == (2, 2, 256, 8)
        self.apply(usbenum.load_writes(self.usb, "low", start=False))
        self.apply(host.load_program(1, self.uart, 256, in_pins=RX, clkdiv=DIV, profile=TAPEOUT))
        assert self.chip.enable == 0 and len(self.usb.words) == 253 and len(self.uart.words) == 19
        assert [self.chip.fetch(i) for i in range(253)] == self.usb.words
        assert [self.chip.fetch(256+i) for i in range(19)] == self.uart.relocate(256)
        self.cold_writes = len([o for o in self.operations if o["kind"] == "write"])

    def source_at(self, cycle):
        index = max(i for i, s in enumerate(self.sources) if i == 0 or cycle >= s.frames[0]["start"])
        return self.sources[index], index

    def _guard(self, kind, addr=None, **extra):
        cycle = self.chip.cycle
        self.operations.append(dict(cycle=cycle, kind=kind, addr=addr, **extra))
        assert not any(a <= cycle < b for a, b in self.gaps), "active loading-host API call"

    def write(self, addr, data):
        self._guard("write", addr, data=data)
        return super().write(addr, data)

    def snap(self, addr):
        self._guard("snap", addr)
        return super().snap(addr)

    def commit(self, addr):
        self._guard("commit", addr)
        return super().commit(addr)

    def cancel(self):
        self._guard("cancel")
        return super().cancel()

    def read(self, addr, pop=False):
        self._guard("read", addr, pop=pop)
        return super().read(addr, pop)

    def wait_until(self, deadline):
        assert self.chip.cycle <= deadline, ("absolute deadline missed", self.chip.cycle, deadline)
        self.step(deadline-self.chip.cycle)


def _line(value):
    return {2: usb.J, 1: usb.K, 0: usb.SE0}.get(value, "?")


class _Peer(ControlHost):
    """Immutable transaction implementation, with strict first-toggle oracle."""
    def __init__(self, mh):
        super().__init__(mh, retries=0)
        self.packets, self.calls, self.response_attempts = [], [], []
        self.want = None

    def _send(self, packet):
        begin = self.mh.chip.cycle
        result = super()._send(packet)
        self.packets.append(dict(direction="peer", begin=begin, end=self.mh.chip.cycle,
                                 packet=bytes(packet).hex()))
        return result

    def _decode(self, trace):
        packet = super()._decode(trace)
        end = self.mh.chip.cycle
        begin = end-len(trace)+1
        states = [trace[j] for j in range(self.bit//2, len(trace), self.bit)]
        assert usb.decode([usb.J]+states) == packet
        self.packets.append(dict(direction="device", begin=begin, end=end,
                                 packet=packet.hex(), raw_states=trace))
        return packet

    def _response(self):
        begin = self.mh.chip.cycle
        packet = super()._response()
        self.response_attempts.append(dict(begin=begin,end=self.mh.chip.cycle,
            packet=None if packet is None else packet.hex(), expected_first_pid=self.want))
        assert packet is not None and packet != b"?", ("first response missing/invalid", packet)
        if self.want is not None and packet != usb.handshake(usb.PID_STALL):
            assert len(packet) >= 3 and packet[0] == self.want, ("unexpected first toggle", packet, self.want)
            assert usb.crc16(packet[1:-2]) == packet[-2:], ("DATA CRC", packet)
        return packet

    def data_in(self, addr, toggle):
        self.want = usb.pid_byte(usb.PID_DATA1 if toggle else usb.PID_DATA0)
        try:
            return super().data_in(addr, toggle)
        finally:
            self.want = None

    def get_descriptor(self, addr, dtype, length, index=0):
        begin = self.mh.chip.cycle
        try:
            result = super().get_descriptor(addr, dtype, length, index)
            self.calls.append(dict(kind="descriptor", dtype=dtype, begin=begin,
                                   end=self.mh.chip.cycle, data=result.hex(), stalled=False))
            return result
        except Stall:
            self.calls.append(dict(kind="descriptor", dtype=dtype, begin=begin,
                                   end=self.mh.chip.cycle, data=None, stalled=True))
            raise


def _arm(mh, length, *, inspect_clear=False):
    begin = mh.chip.cycle
    status = mh.read(sim.core_reg(1, sim.C_STATUS))
    enable = mh.read(sim.A_CTRL)
    assert status & 0xFF == 0 and (not enable & 2 or status & (1 << 13))
    plan = uartcapture_peer.capture_plan(length, enabled_before=enable)
    if inspect_clear:
        before = mh.read(sim.A_FSET)
        mh.apply(plan[:4])
        after = mh.read(sim.A_FSET)
        assert before & 63 == after & 63 and not after & 64 and after & 1
        assert mh.read(sim.A_CTRL) & 1
        mh.apply(plan[4:])
    else:
        mh.apply(plan)
    return dict(begin=begin, end=mh.chip.cycle, writes=plan)


def _drain(mh, length, expected, bad):
    begin = mh.chip.cycle
    status = mh.read(sim.core_reg(1, sim.C_STATUS))
    flags = mh.read(sim.A_FSET)
    assert status & (1 << 13) and not status & (1 << 14)
    assert status & 15 == 0 and (status >> 4) & 15 == (length+3)//4
    assert bool(flags & 64) == bad
    words = [mh.read(sim.core_reg(1, sim.C_FIFO), pop=True) for _ in range((length+3)//4)]
    after = mh.read(sim.core_reg(1, sim.C_STATUS))
    assert after & 255 == 0
    _assert_bytes(length, words, expected)
    return dict(begin=begin, end=mh.chip.cycle, status=status, flags=flags,
                words=words, decoded=list(uartcapture_peer.decode_capture(length, words)), after=after)


def _assert_uart(mh):
    for index, source in enumerate(mh.sources):
        events = [e for e in mh.trace["uart_events"] if e["segment"] == index and e["kind"] in ("start", "data", "stop")]
        assert len(events) == len(source.frames)*10
        for frame in source.frames:
            samples = [e for e in events if e["window"] and e["window"]["frame"] == frame["index"]]
            assert [e["kind"] for e in samples] == ["start"]+["data"]*8+["stop"]
            assert [e["window"]["slot"] for e in samples] == list(range(10))
            for sample in samples:
                window = sample["window"]
                assert not sample["stalled"] and sample["logical"] == window["level"]
                assert window["begin"] <= sample["source_cycle"] < window["end"]
        stop = [e for e in events if e["kind"] == "stop"][-1]
        halted = [h for h in mh.trace["halts"] if h["segment"] == index]
        assert len(halted) == 1 and halted[0]["cycle"] > stop["cycle"] and not halted[0]["fault"]
    assert mh.trace["uart_shift_stalls"] == mh.trace["active_strobes"] == 0
    assert not mh.chip.stats.get("fifo:host_push_to_full_dropped", 0)
    assert not mh.chip.stats.get("fifo:clear_nonempty", 0)
    assert mh.trace["peak_rx"][0] == mh.trace["peak_tx"][0] == mh.trace["peak_tx"][1] == 0


def _assert_wire(mh, peer):
    edges = mh.trace["usb_edges"]
    cycles = [e[0] for e in edges]
    for packet in peer.packets:
        begin, end = packet["begin"], packet["end"]
        # Re-decode resolved raw pads, not the sent packet or DUT line queue.
        states = [_line(edges[bisect.bisect_right(cycles, c)-1][1])
                  for c in range(begin+16, end, 32)]
        decoded = usb.decode([usb.J]+states)
        assert decoded.hex() == packet["packet"], ("raw USB packet mismatch", packet, decoded)
        if decoded[0] in (usb.pid_byte(usb.PID_DATA0), usb.pid_byte(usb.PID_DATA1)):
            assert usb.crc16(decoded[1:-2]) == decoded[-2:]
    devices = [p for p in peer.packets if p["direction"] == "device"]
    assert len(devices) == len(mh.trace["usb_tx_epochs"]), "unexpected device transmission"
    assert [p["packet"] for p in peer.packets] == [p.hex() for p in peer.wire]
    assert all(2 <= t <= 6.5 for t in peer.turnarounds)


def _record(mh, peer, extra, error=None):
    return dict(geometry=dict(ncores=mh.chip.ncores, nbanks=mh.chip.nbanks,
                             bank_words=mh.chip.bank_words, fifo_depth=mh.chip.fifo_depth),
        program_words=[len(mh.usb.words), len(mh.uart.words)],
        source_hashes={name:hashlib.sha256((FW/name).read_bytes()).hexdigest()
                      for name in ("usb_enum.pasm", "uart_rx_capture_peer.pasm")},
        program_words_sha256=[hashlib.sha256(b"".join(w.to_bytes(4,"little") for w in words)).hexdigest()
                              for words in (mh.usb.words,mh.uart.relocate(256))],
        cold_writes=mh.cold_writes, model_clocks=mh.steps, gaps=mh.gaps, operations=mh.operations,
        trace=mh.trace, usb_packets=peer.packets, usb_log=peer.log, usb_calls=peer.calls,
        usb_wire=[p.hex() for p in peer.wire], turnarounds=peer.turnarounds,
        usb_response_attempts=peer.response_attempts,
        sources=[dict(frames=s.frames,windows=s.windows,edges=s.edges) for s in mh.sources],
        stats=mh.chip.stats, final_flags=mh.chip.flags, final_enable=mh.chip.enable,
        final_pc=[c.pc for c in mh.chip.cores[:2]], tail=list(mh.tail), error=error, **extra)


def test_concurrent_enumeration_and_32byte_capture():
    source = _wire(4096, BURST)
    mh = _Host([source])
    peer = _Peer(mh)
    extra = {}
    try:
        extra["arm"] = _arm(mh, 32)
        mh.apply(host.start_writes(1, enabled_before=2))
        both_start = mh.chip.cycle
        assert both_start < 4096
        mh.gaps.append((both_start,150000))
        with TransportIdle(mh):
            mh.step(100)
            enumerate_flow(peer, addr=5)
            extra["usb_finished"] = mh.chip.cycle
            mh.wait_until(150000)
        extra["uart"] = _drain(mh,32,BURST,False)
        extra["usb_state"] = usbenum.state(mh.read)
        assert extra["usb_state"] == dict(address=5,configured=True,pending_address=False,stalled=False)
        _assert_uart(mh)
        _assert_usb_log(peer.log,1)  # the standard flow deliberately requests missing strings
        _assert_wire(mh,peer)
        overlap = [p for p in peer.packets if p["begin"] < source.frames[-1]["end"] and p["end"] > 4096]
        assert any(p["direction"] == "device" for p in overlap)
        extra["overlapping_packets"] = overlap
        assert mh.trace["peak_rx"][1] == 8
        mh.echo.check()
        assert mh.trace["controls"][-1]["data"] & 0x101 == 0x101
        assert all(e["after"] & 1 for e in mh.trace["enable_events"] if e["cycle"] >= both_start)
    except BaseException as exc:
        _save("case1",_record(mh,peer,extra,repr(exc)))
        raise
    _save("case1",_record(mh,peer,extra))


def test_stall_bad_stop_clear_and_rearm_isolation():
    first, second = bytes.fromhex("39d381"), bytes.fromhex("a65c27")
    mh = _Host([_wire(24000,first,True),_wire(65000,second)])
    peer = _Peer(mh)
    extra = {}
    try:
        mh.start(1)
        usb_started = mh.chip.cycle
        mh.step(100)
        with TransportIdle(mh), pytest.raises(Stall):
            peer.get_descriptor(0,3,255)
        assert mh.chip.cycle < 24000
        assert mh.read(sim.A_FSET) & 1
        extra["first_arm"] = _arm(mh,3,inspect_clear=True)
        assert mh.chip.cycle < 24000 and mh.read(sim.A_FSET) & 1
        mh.gaps.append((24000,40000))
        with TransportIdle(mh):
            mh.wait_until(40000)
        extra["first_uart"] = _drain(mh,3,first,True)
        assert extra["first_uart"]["words"] == [0x81D33900]
        assert extra["first_uart"]["flags"] & 1
        extra["second_arm"] = _arm(mh,3,inspect_clear=True)
        rearmed = mh.chip.cycle
        assert rearmed < 65000
        mh.gaps.append((rearmed,100000))
        with TransportIdle(mh):
            mh.wait_until(66000)
            extra["usb_descriptor_begin"] = mh.chip.cycle
            assert peer.get_descriptor(0,1,18) == usbdev.DEVICE_DESC
            extra["usb_finished"] = mh.chip.cycle
            mh.wait_until(100000)
        extra["second_uart"] = _drain(mh,3,second,False)
        assert extra["second_uart"]["words"] == [0x275CA600]
        extra["usb_state"] = usbenum.state(mh.read)
        assert extra["usb_state"] == dict(address=0,configured=False,pending_address=False,stalled=False)
        _assert_uart(mh)
        _assert_usb_log(peer.log,1)
        _assert_wire(mh,peer)
        later_controls = [w for w in mh.trace["controls"] if w["cycle"] > usb_started]
        assert all(w["data"] & 1 and not w["data"] & 256 for w in later_controls)
        assert all(e["after"] & 1 for e in mh.trace["enable_events"] if e["cycle"] >= usb_started)
        assert mh.trace["peak_rx"][1] == 1
        mh.echo.check()
    except BaseException as exc:
        _save("case2",_record(mh,peer,extra,repr(exc)))
        raise
    _save("case2",_record(mh,peer,extra))


def test_pure_peer_plan_source_and_negative_oracles():
    old = asm.assemble((FW/"uart_rx_capture.pasm").read_text(),{"RX":RX})
    new = asm.assemble((FW/"uart_rx_capture_peer.pasm").read_text(),{"RX":RX})
    assert len(old.words) == len(new.words) == 19
    diffs = [i for i,(a,b) in enumerate(zip(old.words,new.words)) if a != b]
    assert len(diffs) == 2 and new.reloc == old.reloc and new.config == old.config
    assert (new.wrap_bottom,new.wrap_top) == (old.wrap_bottom,old.wrap_top)
    for i in diffs:
        assert isa.fields(old.words[i])[0] == isa.OP_FLAG
        assert new.words[i] == old.words[i] | 6
    writes = host.load_program(1,new,256,in_pins=RX,clkdiv=DIV,profile=TAPEOUT)
    assert len(writes) == 25 and writes[:19] == list(enumerate(new.relocate(256),256))
    for index in new.reloc:
        assert 256 <= new.relocate(256)[index] & 1023 < 275
    assert uartcapture_peer.decode_capture is uartcapture.decode_capture
    for length in range(1,33):
        for enabled in range(4):
            plan = uartcapture_peer.capture_plan(length,enabled_before=enabled)
            assert plan == [(sim.A_CTRL,enabled&1),(sim.core_reg(1,sim.C_R0+1),length),
                            (sim.core_reg(1,sim.C_R0+2),length%4),(sim.A_FCLR,64),
                            (sim.A_CTRL,(enabled&1)|2|512)]
    for kw in [dict(length=x) for x in (0,33,True,1.0,"3",None)]+[
            dict(length=3,core=x) for x in (0,2,True,1.0,None)]+[
            dict(length=3,enabled_before=x) for x in (-1,4,True,1.0,None)]:
        with pytest.raises(ValueError):
            uartcapture_peer.capture_plan(**kw)
    _assert_bytes(3,[0x81D33900],bytes.fromhex("39d381"))
    with pytest.raises(AssertionError):
        _assert_bytes(3,[0x81D33800],bytes.fromhex("39d381"))
    with pytest.raises(AssertionError):
        _assert_quiet(25000,True,[(24000,40000)])
    with pytest.raises(AssertionError):
        _assert_usb_log([("IN",0,"error",None)],0)
