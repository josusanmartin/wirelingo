"""One finite, pin-driven LS USB/UART mailbox scenario; no device-side service.

Pytest uses its isolated tmp_path by default; a nonempty WIRELINGO_EVIDENCE
selects an explicit output directory. Unset or empty values use tmp_path.
Independent saved-pad oracle is also usable without constructing a model.
"""
import copy
import json
import os
from fractions import Fraction
from pathlib import Path

from pe import sim, usb, usbdev, usb_uart_byte
from pe.modelhost import ModelHost
from pe.profile import TAPEOUT
from pe.usbhost import ControlHost, Stall
from line_echo import EchoProbe

BIT = 32
UART_BIT = Fraction(1250, 3)       # 48 MHz / true nominal115200, no float drift
REACTION = 8                    # READY observation -> START, system clocks
RESET = (144, 4800)              # detector-only3us SE0 +100us recovery


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


EXPECTED = {
    "enum-device": descriptor(usbdev.DEVICE_DESC),
    "enum-address": [b"\xd2", data(b"")],
    "enum-config-descriptor": descriptor(usbdev.CONFIG_DESC),
    "enum-configure": [b"\xd2", data(b"")],
    "enum-get-configuration": [b"\xd2", data(b"\x01"), b"\xd2"],
    "empty-initial": [b"\xd2", b"\x1e"],
    "vendor-00-setup": [b"\xd2"],
    "first-00": [data(b"\x00")],
    "lost-ack-retry": [data(b"\x00")],
    "malformed-ack-retry": [data(b"\x00")],
    "descriptor-interleave": descriptor(usbdev.DEVICE_DESC),
    "zero-length": [b"\xd2", data(b"")],
    "recover-00": [b"\xd2", data(b"\x00")],
    "status-00": [b"\xd2"],
    "empty-after-00": [b"\xd2", b"\x1e"],
    "vendor-a5-first": [b"\xd2", data(b"\xa5")],
    "deconfigure": [b"\xd2", data(b"")],
    "reconfigure": [b"\xd2", data(b"")],
    "recover-a5": [b"\xd2", data(b"\xa5")],
    "status-a5": [b"\xd2"],
    "vendor-ff-first": [b"\xd2", data(b"\xff")],
    "reset-get-configuration": [b"\xd2", data(b"\x00"), b"\xd2"],
    "recover-ff-default": [b"\xd2", data(b"\xff")],
    "status-ff": [b"\xd2"],
    "empty-final": [b"\xd2", b"\x1e"],
}
CONSUME = ((0x00, "consume-00"), (0xa5, "consume-a5"), (0xff, "consume-ff"))


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


def audit(result):
    """Wire/data/order verdict. Internal observations never supply expected bytes."""
    assert not result["error"], "dynamic error cannot be repaired by later success"
    assert result["external_ownTX_overlap"] == [], "USB driver overlap"
    assert result["api_attempts"] == [] and result["active_bus_strobes"] == [], "active host service"
    assert result["geometry"] == [2, 2, 256, 8] and result["sync_bypass"] == 0
    received = {name: [] for name in EXPECTED}
    decoded = []
    for row in result["usb"]:
        packet = raw_decode(row["raw"])
        decoded.append(dict(stage=row["stage"], direction=row["direction"],
                            packet=packet["packet"].hex() if packet["packet"] is not None else None,
                            unstuffed_bits=len(packet["bits"]), eop_j=packet["eop_j"]))
        if row["direction"] == "device":
            assert row["stage"] in received, "unplanned device response"
            assert packet["packet"] is not None, "partial device response"
            received[row["stage"]].append(packet["packet"])
        else:
            # Expected source bits retained independently of physical resolution.
            assert packet["bits"] == row["source_bits"], "host symbols changed on resolved pads"
            if packet["packet"] is None:
                suffix = [0] if row["stage"] == "malformed-ack" else [1, 1, 1]
                assert row["stage"] in ("malformed-ack", "consume-00")
                prefix = [0, 0, 0, 0, 0, 0, 0, 1, 0, 1, 0, 0, 1, 0, 1, 1]
                assert packet["bits"] == prefix + suffix, "declared ACK tail bits"
    seen_stages = []
    for row in result["usb"]:
        if row["direction"] == "device" and (not seen_stages or seen_stages[-1] != row["stage"]):
            seen_stages.append(row["stage"])
    assert seen_stages == ["empty-initial"] + [s for s in EXPECTED if s != "empty-initial"], "chronological USB stages"
    for stage, expected in EXPECTED.items():
        assert len(received[stage]) == len(expected), f"packet count {stage}"
        assert received[stage] == expected, f"packet mismatch {stage}"
    assert sum(v == 1 for _, v in result["ready_edges"]) == 5, "phantom READY grant"
    assert sum(v == 0 for _, v in result["ready_edges"][1:]) == 4, "unaccounted READY withdrawal"
    frames = result["uart"]
    assert [(f["value"], f["good_stop"], f["compliant"]) for f in frames] == [
        (0x3c, False, True), (0, True, True), (0x5a, True, False),
        (0xa5, True, True), (0xff, True, True)]
    for frame in frames:
        for index, value in enumerate([0] + [(frame["value"] >> k) & 1 for k in range(8)] + [int(frame["good_stop"])]):
            t = frame["start"] + (2*index+1)*UART_BIT/2
            assert at(result["rx_edges"], int(t)) == value, "raw UART sample mismatch"
        assert at(result["ready_edges"], frame["start"]) == int(frame["compliant"]), "UART grant"
        assert frame["end"]-frame["start"] == 4167, "nominal rational UART frame length"
    good_frames = [f for f in frames if f["compliant"] and f["good_stop"]]
    prompt = good_frames[0]
    actual_grant = max(t for t,v in result["ready_edges"] if v and t <= prompt["start"])
    assert prompt["value"] == 0 and prompt["start"] == actual_grant+8, "READY-edge+8 zero witness"
    assert prompt["grant_observation"] == actual_grant, "prompt witness used an old READY high"
    latencies = []
    for frame, (value, stage) in zip(good_frames, CONSUME):
        assert frame["value"] == value
        ack = [r for r, d in zip(result["usb"], decoded) if r["stage"] == stage and r["direction"] == "host"]
        assert len(ack) == 1
        ack_decode = raw_decode(ack[0]["raw"])
        suffix = [1, 1, 1] if value == 0 else []
        assert ack_decode["bits"] == [0,0,0,0,0,0,0,1,0,1,0,0,1,0,1,1] + suffix, "matching valid ACK"
        end = ack_decode["eop_j"]
        assert end > frame["end"]
        assert at(result["ready_edges"], frame["end"]) == 0
        assert not any(frame["end"] <= t < end and v for t, v in result["ready_edges"]), "early READY/release"
        rises = [t for t, v in result["ready_edges"] if t >= end and v]
        assert rises, "missing next READY grant"
        latencies.append(dict(value=value, uart_start=frame["start"], ack_eop_j=end,
                              next_ready=rises[0], ack_to_ready=rises[0]-end,
                              producer_stall=rises[0]-frame["end"],
                              acquisition_to_ack=end-frame["start"]))
    assert result["logical_consumed"] == [0, 0xa5, 0xff], "logical delivery order"
    assert result["final_flags"] & 0x40, "sticky F6 corroboration missing"
    return dict(decoded_packets=decoded, consumption=latencies,
                prompt_zero=dict(ready_rise=actual_grant, start=prompt["start"], reaction_clocks=8),
                USB_transactions=sum(r["direction"] == "host" for r in result["usb"]),
                deliberate_loss="0x5a violates READY: outside supported lossless contract")


class Host(ModelHost):
    def __init__(self):
        self.chip = sim.Chip(ncores=TAPEOUT.ncores, nbanks=TAPEOUT.nbanks,
                             bank_words=TAPEOUT.bank_words, fifo_depth=TAPEOUT.fifo_depth)
        self.echo = EchoProbe(self.chip, lambda: self.ext_oe & 3)  # line-RX overflow is own echo only
        self.ui, self.uio, self.ext_oe, self.pull_up = 1, 0, 0, 2


class Rig:
    APIS = ("write", "read", "snap", "commit", "cancel", "push", "pop_all", "apply", "start", "tx_level")

    def __init__(self):
        self.mh = Host()
        c = self.mh.chip
        assert (c.ncores, c.nbanks, c.bank_words, c.fifo_depth) == (2, 2, 256, 8)
        self.stage, self.active, self.calls = "cold-load", False, 0
        self.usb, self.uart, self.api_attempts, self.active_strobes = [], [], [], []
        self.bus_edges, self.rx_edges, self.ready_edges = [], [], []
        self.states, self.overflow, self.overlap = [], [], []
        self.consumed = []
        original = c.step

        def observed(*args, **kw):
            self.calls += 1
            assert self.calls <= 1000000, "model step cap"
            before = c.cycle
            strobes = {name: kw.get(name, args[index] if len(args)>index else False)
                       for name, index in (("we", 2), ("commit", 5), ("snap", 6), ("cancel", 7))}
            if self.active and any(strobes.values()):
                self.active_strobes.append(dict(cycle=before, signals=strobes))
                raise AssertionError("active direct loading-host strobe")
            self.edge(self.bus_edges, before, args[1] & 3)
            self.edge(self.rx_edges, before, args[0] & 1)
            self.edge(self.ready_edges, before, c.outputs()[0] & 1)
            own_tx = bool(c.outputs()[2] & 3)
            if self.mh.ext_oe & c.outputs()[2] & 3:
                self.overlap.append([before, self.stage, self.mh.ext_oe & 3, c.outputs()[2] & 3])
            prev_ovf = c.stats.get("line:rx_overflow", 0)
            result = original(*args, **kw)
            self.edge(self.bus_edges, c.cycle, self.mh.bus_uio() & 3)
            self.edge(self.ready_edges, c.cycle, c.outputs()[0] & 1)
            state = [c.flags, c.enable, (c.pin_out >> 24) & 255, c.cores[0].pc, c.cores[1].pc]
            if not self.states or state != self.states[-1][1:]:
                self.states.append([c.cycle] + state)
            if c.stats.get("line:rx_overflow", 0) != prev_ovf:
                self.overflow.append([before, self.stage, own_tx, c.stats["line:rx_overflow"]])
            return result

        c.step = observed

    @staticmethod
    def edge(rows, cycle, value):
        if not rows or rows[-1][1] != value:
            rows.append([cycle, value])

    def raw(self, begin, end):
        return [[begin, at(self.bus_edges, begin)]] + [row for row in self.bus_edges if begin < row[0] <= end]

    def ready(self):
        return self.mh.chip.outputs()[0] & 1

    def wait_ready(self):
        for _ in range(8192):
            if self.ready():
                return
            self.mh.step()
        raise AssertionError("physical READY grant timeout")

    def freeze_apis(self):
        self.active = True
        for name in self.APIS:
            def refused(*args, _name=name, **kwargs):
                self.api_attempts.append([self.mh.chip.cycle, _name])
                raise AssertionError("active loading-host API " + _name)
            setattr(self.mh, name, refused)

    def frame(self, value, good=True, compliant=True):
        if compliant:
            self.wait_ready()    # only physical READY controls admission
        else:
            assert not self.ready(), "violation must occur while READY low"
        grant = self.mh.chip.cycle
        self.mh.step(REACTION)
        start = self.mh.chip.cycle
        frame = dict(value=value, good_stop=good, compliant=compliant,
                     grant_observation=grant, start=start, end=start+4167)
        self.uart.append(frame)
        for i, level in enumerate([0]+[(value>>k)&1 for k in range(8)]+[int(good)]):
            self.mh.ui = (self.mh.ui & ~1) | level
            deadline = start + (i+1)*UART_BIT
            end = (deadline.numerator+deadline.denominator-1)//deadline.denominator
            self.mh.step(end-self.mh.chip.cycle)
        self.mh.ui |= 1         # predetermined return to idle after the full frame

    def result(self, error):
        c = self.mh.chip
        return dict(error=error, chip_step_calls=self.calls, advanced_cycles=c.cycle,
                    geometry=[c.ncores,c.nbanks,c.bank_words,c.fifo_depth], sync_bypass=c.sync_bypass,
                    usb=self.usb, uart=self.uart, rx_edges=self.rx_edges,
                    ready_edges=self.ready_edges, usb_bus_edges=self.bus_edges,
                    api_attempts=self.api_attempts, active_bus_strobes=self.active_strobes,
                    corroborating_state=self.states, line_rx_overflow=self.overflow,
                    stats=c.stats, external_ownTX_overlap=self.overlap,
                    logical_consumed=self.consumed, final_flags=c.flags,
                    final_enable=c.enable, FIFOs=[[len(x.txf),len(x.rxf)] for x in c.cores[:c.ncores]])


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


def mutate_and_reject(result):
    receipts = []
    mutant = copy.deepcopy(result)
    row = next(r for r in mutant["usb"] if r["stage"] == "lost-ack-retry" and r["direction"] == "device")
    changed = data(b"\x01")       # correct recomputed CRC, wrong retained payload
    states = usb.encode(changed)
    row["raw"] = [[row["begin"]+i*BIT,{usb.J:2,usb.K:1,usb.SE0:0}[s]] for i,s in enumerate(states)]
    row["end"] = row["begin"]+len(states)*BIT
    assert raw_decode(row["raw"])["packet"] == changed
    mutants = [("retry-payload-with-recomputed-CRC", mutant)]
    early = copy.deepcopy(result)
    first = next(f for f in early["uart"] if f["value"] == 0 and f["compliant"])
    release = next(row for row in early["ready_edges"] if row[0] > first["end"] and row[1])
    release[0] = first["end"]+10;early["ready_edges"].sort()
    mutants.append(("early-READY-release",early))
    repaired = copy.deepcopy(result)
    row = next(r for r in repaired["usb"] if r["stage"] == "enum-address" and r["direction"] == "device")
    fault = copy.deepcopy(row);fault["raw"]=[[fault["begin"],2]];fault["helper_reply"]=None
    repaired["usb"].insert(repaired["usb"].index(row),fault)
    mutants.append(("nominal-error-followed-by-success",repaired))
    for name, mutant in mutants:
        try:
            audit(mutant)
        except AssertionError as exc:
            receipts.append(dict(name=name,rejected=True,reason=str(exc),model_steps=0))
        else:
            raise AssertionError("saved-artifact mutant accepted: "+name)
    return receipts


def test_uart_byte_control_mailbox(tmp_path):
    out = Path(os.environ.get("WIRELINGO_EVIDENCE") or tmp_path)
    out.mkdir(parents=True, exist_ok=True)
    rig, error = Rig(), None
    try:
        programs = usb_uart_byte.assemble()
        rig.mh.apply(usb_uart_byte.load_writes(programs))
        rig.freeze_apis()
        rig.mh.step(200)
        peer = Peer(rig)
        def stage(name): rig.stage = name
        def vendor(address,length=1): peer.setup(address,usb_uart_byte.vendor_request(length))
        def empty(address,name):
            stage(name)
            try: peer.control_read(address,usb_uart_byte.vendor_request())
            except Stall: pass
            else: raise AssertionError("empty mailbox did not STALL")
        def config(value,name):
            stage(name);peer.control_write(5,bytes((0,9,value,0,0,0,0,0)))
            assert not rig.ready(), "configuration consumed pending byte"
        def consume(value,name,kind="ordinary"):
            stage(name);peer.ack(kind);rig.wait_ready();rig.consumed.append(value)
        empty(0,"empty-initial")
        stage("bad-stop-3c");rig.frame(0x3c,good=False)
        assert rig.mh.chip.flags & 0x40, "sticky bad-STOP flag absent"
        # No USB work between error recovery and prompt good START: this
        # wait observes the NEW physical READY rise, not an old high level.
        stage("uart-00");rig.frame(0)
        stage("enum-device");assert peer.get_descriptor(0,1,18)==usbdev.DEVICE_DESC
        stage("enum-address");peer.control_write(0,bytes.fromhex("0005050000000000"))
        stage("enum-config-descriptor");assert peer.get_descriptor(5,2,18)==usbdev.CONFIG_DESC
        stage("enum-configure");peer.control_write(5,bytes.fromhex("0009010000000000"))
        stage("enum-get-configuration");assert peer.control_read(5,bytes.fromhex("8008000000000100"))==b"\x01"
        stage("vendor-00-setup");vendor(5)
        stage("first-00");peer.poll(5,b"\x00");peer.gap() # intentional absent ACK
        stage("violating-5a");rig.frame(0x5a,compliant=False)
        stage("lost-ack-retry");peer.poll(5,b"\x00")
        stage("malformed-ack");peer.ack("malformed")
        stage("malformed-ack-retry");peer.poll(5,b"\x00")
        peer.gap()  # fixed two-bit no-ACK handoff; complete device EOP first
        stage("descriptor-interleave");assert peer.get_descriptor(5,1,18)==usbdev.DEVICE_DESC
        assert not rig.ready()
        stage("zero-length");vendor(5,0);peer.poll(5,b"");peer.ack()
        assert not rig.ready(), "zero-length ACK consumed mailbox"
        stage("recover-00");vendor(5);peer.poll(5,b"\x00")
        consume(0,"consume-00","dribble")
        stage("status-00");peer.data_out(5,usb.PID_DATA1,b"")
        empty(5,"empty-after-00")
        stage("uart-a5");rig.frame(0xa5)
        stage("vendor-a5-first");vendor(5);peer.poll(5,b"\xa5")
        peer.gap()  # fixed two-bit no-ACK handoff; complete device EOP first
        config(0,"deconfigure");config(1,"reconfigure")
        stage("recover-a5");vendor(5);peer.poll(5,b"\xa5")
        consume(0xa5,"consume-a5")
        stage("status-a5");peer.data_out(5,usb.PID_DATA1,b"")
        stage("uart-ff");rig.frame(0xff)
        stage("vendor-ff-first");vendor(5);peer.poll(5,b"\xff")
        peer.gap()  # fixed two-bit no-ACK handoff; complete device EOP first
        stage("USB-detector-reset");peer.bus_reset(cycles=RESET[0],recovery=RESET[1])
        assert not rig.ready(), "USB reset consumed producer byte"
        stage("reset-get-configuration");assert peer.control_read(0,bytes.fromhex("8008000000000100"))==b"\x00"
        stage("recover-ff-default");vendor(0);peer.poll(0,b"\xff")
        consume(0xff,"consume-ff")
        stage("status-ff");peer.data_out(0,usb.PID_DATA1,b"")
        empty(0,"empty-final")
        result = rig.result(None)
        verdict = audit(result)
        rig.mh.echo.check()
        mutate_and_reject(result)  # saved-trace oracle controls
        (out/"oracle.json").write_text(json.dumps(verdict,indent=2)+"\n")
    except BaseException as exc:
        error = type(exc).__name__+": "+str(exc)
        raise
    finally:
        (out/"dynamic.json").write_text(json.dumps(rig.result(error),indent=2)+"\n")
