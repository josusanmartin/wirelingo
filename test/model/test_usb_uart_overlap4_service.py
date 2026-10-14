"""Finite complete-service workload; source preparation is not measured forwarding.

One cold load, fixed external UART dates, no loading-host service afterward.
The USB peer performs all nine packets (eleven for one explicit unACKed retry).
Use ordinary pytest; WIRELINGO_EVIDENCE optionally selects a fresh nonempty path.
"""
import bisect
import copy
import hashlib
import json
import os
from pathlib import Path
from pe import isa, sim, usb, usbdev, usb_uart_overlap4
from pe.modelhost import ModelHost
from pe.usbhost import ControlHost
from line_echo import EchoProbe

NODE = "test/model/test_usb_uart_overlap4_service.py::test_overlap4_complete_service"
RAW_NAME = "overlap4-service-raw.json"
MAX_STEPS, BIT = 400000, 32
SOURCE_START, FRAME_CLOCKS, SLOT_CLOCKS = 120000, 4167, 16668
SOURCE_END, FINAL_DEADLINE = 253344, 270000
REPORTS = tuple(bytes.fromhex(s) for s in
    ("00000000","39d3815e","12345678","9abcdef0","ffffffff","fefefefe","7f7f7f7f","81817e7e"))
FRAMES = tuple((str(i),byte,SOURCE_START+FRAME_CLOCKS*(4*i+j),True)
               for i,report in enumerate(REPORTS) for j,byte in enumerate(report))
CALENDAR = (("enum",30000),)+tuple(("report-"+str(i),SOURCE_START+SLOT_CLOCKS*(i+1)) for i in range(8))
ENUM_DEADLINE = 120000
REQUEST = bytes.fromhex("c001000000000400")

def crc16(payload):
    value=0xffff
    for byte in payload:
        value ^= byte
        for _ in range(8):
            value=(value>>1) ^ (0xa001 if value&1 else 0)
    return (value^0xffff).to_bytes(2,"little")

def data(payload,toggle=1):
    return bytes([0x4b if toggle else 0xc3])+payload+crc16(payload)

def descriptor(payload):
    return [b"\xd2"]+[data(payload[i:i+8],1^((i//8)&1)) for i in range(0,len(payload),8)]+[b"\xd2"]

EXPECTED = {"enum-device":descriptor(usbdev.DEVICE_DESC),"enum-address":[b"\xd2",data(b"")],
    "enum-config":descriptor(usbdev.CONFIG_DESC),"enum-configure":[b"\xd2",data(b"")],
    "enum-get-config":[b"\xd2",data(b"\x01"),b"\xd2"]}
for i,payload in enumerate(REPORTS):
    EXPECTED["report-"+str(i)]=[b"\xd2",data(payload)]+([data(payload)] if i==3 else [])+[b"\xd2"]

def timetable():
    edges=[(0,1)];windows=[]
    for index,(report,byte,start,good) in enumerate(FRAMES):
        levels=[0]+[(byte>>bit)&1 for bit in range(8)]+[int(good)]
        for slot,level in enumerate(levels):
            begin=start+(slot*1250+2)//3;end=start+((slot+1)*1250+2)//3
            edges.append((begin,level))
            windows.append(dict(index=index,report=report,slot=slot,begin=begin,end=end,level=level))
        edges.append((start+4167,1))
    return edges,windows

def at(edges,cycle):
    index=bisect.bisect_right([row[0] for row in edges],cycle)-1
    return edges[max(index,0)][1]

def raw_decode(raw):
    """Independent NRZI/stuff/CRC decoder over resolved physical pad edges."""
    edges=sorted(dict(raw).items())
    first=next(t for t,v in edges if v==1)
    se0=next(t for t,v in edges if t>first and v==0)
    end_j=next(t for t,v in edges if t>se0 and v==2)
    assert end_j-se0==64,"raw EOP length"
    previous=2;ones=0;bits=[];cursor=0
    for clock in range(first+16,se0,32):
        while cursor+1<len(edges) and edges[cursor+1][0]<=clock:cursor+=1
        level=edges[cursor][1];assert level in (1,2)
        bit=int(level==previous);previous=level
        if ones==6:
            assert bit==0,"raw stuffed bit";ones=0;continue
        ones=ones+1 if bit else 0;bits.append(bit)
    assert bits[:8]==[0,0,0,0,0,0,0,1],"raw SYNC"
    payload=bits[8:];packet=None
    if len(payload)%8==0:
        packet=bytes(sum(payload[i+j]<<j for j in range(8)) for i in range(0,len(payload),8))
        assert packet and packet[0]&15==(~(packet[0]>>4)&15),"PID complement"
        if packet[0] in (0x4b,0xc3):assert packet[-2:]==crc16(packet[1:-2]),"raw DATA CRC"
    return dict(packet=packet,bits=bits,first=first,se0=se0,eop_j=end_j,eop_end=end_j+32)

def _json_bytes(value):
    if type(value) in (bytes,bytearray):return {"__bytes_type__":type(value).__name__,"hex":value.hex()}
    raise TypeError(type(value).__name__)

class _Host(ModelHost):
    def __init__(self):
        self.chip=sim.Chip(ncores=2,nbanks=2,bank_words=256,fifo_depth=8)
        self.echo=EchoProbe(self.chip,lambda:self.ext_oe&3)  # line-RX overflow is own echo only
        self.ui,self.uio,self.ext_oe,self.pull_up=1,0,0,2

class _Rig:
    APIS=("write","read","snap","commit","cancel","push","pop_all","apply","start","tx_level")
    def __init__(self,programs):
        self.mh=_Host();c=self.mh.chip
        assert (c.ncores,c.nbanks,c.bank_words,c.fifo_depth)==(2,2,256,8)
        self.programs=programs;self.stage="cold";self.active=False;self.calls=0
        self.source_edges,self.windows=timetable();self.source_times=[e[0] for e in self.source_edges]
        self.raw_edges=[];self.rx_edges=[];self.ready_edges=[];self.error_edges=[];self.flag_edges=[]
        self.consumer=[];self.producer=[];self.usb=[];self.frames=[];self.overflow=[];self.ack_falls=[]
        self.occupancy_edges=[];self.services=[];self.retry_observations=[];self.host_calls=[];self.active_strobes=[];self.driver_overlap=[];self.actions=[];self.tail=[]
        self.grant=-1;self.used_grant=-1;self.previous_end=0;self.ready=0
        self.labels0=programs[0].labels;self.labels1={k:v+256 for k,v in programs[1].labels.items()}
        l=self.labels0
        self.watch0={l['got_in']+2,l['s_bit'],*range(l['get_word'],l['get_word']+4),
                     *range(l['got_ack'],l['ack_advance'])}
        self.starts={f[2]:i for i,f in enumerate(FRAMES)}
        original=c.step
        def observed(ui_in=0,uio_in=0,**kw):
            cycle=c.cycle
            assert self.calls<MAX_STEPS,"aggregate step cap"
            self.calls+=1
            assert 'ui_mid' not in kw and 'uio_mid' not in kw,"no half-sample override"
            strobes={k:bool(kw.get(k,False)) for k in ('we','snap','commit','cancel')}
            if any(strobes.values()):self.host_calls.append(dict(cycle=cycle,signals=strobes,addr=kw.get('addr'),data=kw.get('wdata')))
            if self.active and any(strobes.values()):
                self.active_strobes.append(dict(cycle=cycle,signals=strobes));raise AssertionError('active host strobe')
            out,_,oe=c.outputs();physical_ready=out&1;physical_error=(out>>1)&1
            self.edge(self.ready_edges,cycle,physical_ready);self.edge(self.error_edges,cycle,physical_error)
            if physical_ready and not self.ready:self.grant=cycle
            self.ready=physical_ready
            if cycle in self.starts:
                index=self.starts[cycle];report,byte,start,good=FRAMES[index]
                assert physical_ready and c.pin_oe&(1<<8) and self.grant>self.used_grant,"missed new physical READY"
                assert cycle>=self.previous_end,"previous full frame truncated"
                self.frames.append(dict(index=index,report=report,byte=byte,start=start,end=start+4167,good_stop=good,grant=self.grant))
                self.used_grant=self.grant;self.previous_end=start+4167
            raw_rx=self.source_edges[bisect.bisect_right(self.source_times,cycle)-1][1]
            self.edge(self.rx_edges,cycle,raw_rx)
            self.edge(self.raw_edges,cycle,uio_in&3)
            if self.mh.ext_oe&oe&3:
                self.driver_overlap.append(dict(cycle=cycle,stage=self.stage,external=self.mh.ext_oe&3,device=oe&3))
                raise AssertionError('external/device USB driver overlap')
            assert c.sync_bypass==0
            if cycle>=2:assert (c.pins_in()>>16)&1==self.source_edges[bisect.bisect_right(self.source_times,cycle-2)-1][1]
            # Passive source-defined good-byte count; no input/service feedback.
            remaining=(c.cores[1].r[3]>>8)&255
            pending=4-min(4,remaining) if cycle>=SOURCE_START else 0
            occupancy=[int(bool(c.flags&128)),pending,physical_ready]
            self.edge(self.occupancy_edges,cycle,occupancy)
            oldflags=c.flags;old_pin=(c.pin_out>>24)&255;old_ovf=c.stats.get('line:rx_overflow',0)
            events=[]
            for i,q in enumerate(c.cores[:2]):
                active=(c.enable>>i)&1 and not q.halted and q.cnt==q.dcnt==0
                if active and (i==1 or q.pc in self.watch0):
                    events.append((i,dict(cycle=cycle,pc=q.pc,word=c.fetch(q.pc),stage=self.stage,
                        flags=oldflags,pin=old_pin,regs=list(q.r),isr=q.isr,osr=q.osr,crc=q.crc,
                        line=dict(tx=bool(c.line.tx_on),entries=len(c.line.entries),rx=len(c.line.rq),end=bool(c.line.end_pend)),
                        logical_RX=(c.pins_in()>>16)&1,source_cycle=cycle-2)))
            # Passive snapshot at every actual F7 fall, even during a delayed RET.
            q1=c.cores[1]
            before1=dict(pc=q1.pc,dcnt=q1.dcnt,isr=q1.isr)
            answer=original((ui_in&~1)|raw_rx,uio_in,**kw)
            if oldflags&128 and not c.flags&128:
                self.ack_falls.append(dict(cycle=cycle,flags=oldflags,after_flags=c.flags,
                    before=before1,after=dict(pc=q1.pc,dcnt=q1.dcnt,isr=q1.isr)))
            assert c.cycle==self.calls and not c.cores[0].fault and not c.cores[1].fault
            for i,event in events:
                q=c.cores[i]
                event.update(completed=not bool(q.stalled),after_flags=c.flags,after_pin=(c.pin_out>>24)&255,
                             after_regs=list(q.r),after_isr=q.isr)
                (self.consumer if i==0 else self.producer).append(event)
            self.edge(self.flag_edges,c.cycle,c.flags)
            if c.stats.get('line:rx_overflow',0)!=old_ovf:
                self.overflow.append(dict(cycle=cycle,stage=self.stage,own_TX_active=bool(oe&3),external_TX_active=bool(self.mh.ext_oe&3)))
            self.tail.append(dict(cycle=cycle,pc=[q.pc for q in c.cores[:2]],flags=oldflags,RX=raw_rx,READY=physical_ready,ERROR=physical_error))
            if len(self.tail)>96:self.tail.pop(0)
            return answer
        c.step=observed

    @staticmethod
    def edge(rows,cycle,value):
        if not rows or rows[-1][1]!=value:rows.append([cycle,value])

    def freeze_apis(self):
        self.active=True
        for name in self.APIS:
            def refuse(*args,_name=name,**kw):
                self.host_calls.append(dict(cycle=self.mh.chip.cycle,forbidden_API=_name))
                raise AssertionError('active loading-host API '+_name)
            setattr(self.mh,name,refuse)

    def absolute(self,stage,clock):
        assert self.mh.chip.cycle<=clock,('prior action missed next fixed clock',stage,self.mh.chip.cycle,clock)
        self.mh.step(clock-self.mh.chip.cycle);self.stage=stage
        self.actions.append(dict(stage=stage,clock=clock))

    def raw(self,begin,end):
        return [[begin,at(self.raw_edges,begin)]]+[e for e in self.raw_edges if begin<e[0]<=end]+[[end,self.mh.bus_uio()&3]]

    def snapshot(self,error,peer=None):
        c=self.mh.chip
        return dict(node=NODE,error=error,chip_step_calls=self.calls,advanced_cycles=c.cycle,
            geometry=[c.ncores,c.nbanks,c.bank_words,c.fifo_depth],cold_write_count=usb_uart_overlap4.COLD_WRITES,
            usb_labels=self.labels0,producer_labels=self.labels1,source_calendar=list(FRAMES),actions=self.actions,
            services=self.services,retry_observations=self.retry_observations,occupancy_edges=self.occupancy_edges,
            UART_frames=self.frames,source_edges=self.source_edges,rx_edges=self.rx_edges,
            ready_edges=self.ready_edges,error_edges=self.error_edges,flag_edges=self.flag_edges,
            consumer_events=self.consumer,producer_events=self.producer,ack_falls=self.ack_falls,usb=self.usb,raw_bus_edges=self.raw_edges,
            host_calls=self.host_calls,active_strobes=self.active_strobes,driver_overlap=self.driver_overlap,
            overflow=self.overflow,stats=c.stats,tail=self.tail,final_flags=c.flags,final_pin_out=c.pin_out,
            final_FIFOs=[[len(q.txf),len(q.rxf)] for q in c.cores[:2]],
            peer_log=[] if peer is None else peer.log,turnarounds=[] if peer is None else peer.turnarounds,
            image_sha256=[hashlib.sha256(b''.join(w.to_bytes(4,'little') for w in p.relocate(i*256))).hexdigest()
                          for i,p in enumerate(self.programs)])

class _Peer(ControlHost):
    def __init__(self,rig):
        super().__init__(rig.mh,bit=32,gap_bits=2,keepalive=False,retries=0)
        self.rig=rig;self.counts={stage:0 for stage in EXPECTED}

    def _drive(self,states):
        begin=self.mh.chip.cycle
        super()._drive(states)
        end=self.mh.chip.cycle
        self.rig.usb.append(dict(direction="host",stage=self.rig.stage,begin=begin,end=end,
                                 raw=self.rig.raw(begin,end),source_states=states))

    def _response(self):
        begin=self.mh.chip.cycle;reply=super()._response()
        self.mh.step(32)  # pay the complete EOP J cell before the64-clock gap
        end=self.mh.chip.cycle
        self.rig.usb.append(dict(direction="device",stage=self.rig.stage,begin=begin,end=end,
                                 raw=self.rig.raw(begin,end),helper_reply=None if reply is None else reply.hex()))
        stage=self.rig.stage;index=self.counts[stage]
        assert index<len(EXPECTED[stage]),"hidden response retry"
        assert reply==EXPECTED[stage][index],("first-response mismatch",stage,index,reply)
        self.counts[stage]+=1
        return reply

    def complete_report(self,index):
        # Source-driven index/payload only; no readiness-dependent poll/service.
        begin=self.mh.chip.cycle
        self.setup(5,REQUEST)
        self._send(usb.token(usb.PID_IN,5,0));first=self._response()
        if index==3:
            end=self.mh.chip.cycle
            self.gap()  # NO ACK: explicit second IN after full EOP+64
            retry_start=self.mh.chip.cycle
            self._send(usb.token(usb.PID_IN,5,0));second=self._response()
            assert second==first,"unACKed retry changed PID/payload/CRC"
            self.rig.retry_observations.append(dict(report=index,first_complete_EOP=end,
                retry_start=retry_start,retry_complete_EOP=self.mh.chip.cycle,
                first=first.hex(),retry=second.hex()))
        self.gap();self._send(usb.handshake(usb.PID_ACK));self.gap()
        self.data_out(5,usb.PID_DATA1,b"")
        end=self.mh.chip.cycle
        deadline=SOURCE_START+SLOT_CLOCKS*(index+2)
        if index==7:deadline=min(deadline,FINAL_DEADLINE)
        assert end<deadline,("complete control slot deadline",index,end,deadline)
        self.rig.services.append(dict(report=index,start=begin,end=end,deadline=deadline,
            clocks=end-begin,slack=deadline-end,expected_packets=11 if index==3 else 9))


def _completed(rows,pc):
    return [e for e in rows if e["pc"]==pc and e["completed"]]


def wire_for_read(payload,request=REQUEST,addr=5,retry=False):
    rows=[("host",usb.token(usb.PID_SETUP,addr,0)),("host",usb.data_packet(usb.PID_DATA0,request)),("device",b"\xd2")]
    for i in range(0,len(payload),8):
        packet=data(payload[i:i+8],1^((i//8)&1))
        rows += [("host",usb.token(usb.PID_IN,addr,0)),("device",packet)]
        if retry:rows += [("host",usb.token(usb.PID_IN,addr,0)),("device",packet)]
        rows += [("host",b"\xd2")]
    return rows+[("host",usb.token(usb.PID_OUT,addr,0)),("host",data(b"")),("device",b"\xd2")]


def enum_wire():
    reads={"enum-device":wire_for_read(usbdev.DEVICE_DESC,bytes.fromhex("8006000100001200"),0),
           "enum-config":wire_for_read(usbdev.CONFIG_DESC,bytes.fromhex("8006000200001200"),5),
           "enum-get-config":wire_for_read(b"\x01",bytes.fromhex("8008000000000100"),5)}
    for name,addr,request in (("enum-address",0,"0005050000000000"),("enum-configure",5,"0009010000000000")):
        reads[name]=[("host",usb.token(usb.PID_SETUP,addr,0)),("host",usb.data_packet(usb.PID_DATA0,bytes.fromhex(request))),
            ("device",b"\xd2"),("host",usb.token(usb.PID_IN,addr,0)),("device",data(b"")),("host",b"\xd2")]
    return reads


def intervals(edges,end,predicate,begin=0):
    result=[]
    for i,(t,v) in enumerate(edges):
        stop=edges[i+1][0] if i+1<len(edges) else end
        lo=max(t,begin);hi=min(stop,end)
        if hi>lo and predicate(v):result.append([lo,hi])
    return result


def audit(result):
    """Independent external-byte/physical-wire oracle; no DUT calls."""
    assert result["node"]==NODE and result["error"] is None
    assert result["geometry"]==[2,2,256,8]
    assert result["chip_step_calls"]==result["advanced_cycles"]==FINAL_DEADLINE<=MAX_STEPS
    assert result["cold_write_count"]==374 and result["final_FIFOs"]==[[0,0],[0,0]]
    assert not result["active_strobes"] and not result["driver_overlap"]
    assert len(result["host_calls"])==374 and all(e["cycle"]<374 for e in result["host_calls"])
    assert [(f["report"],f["byte"],f["start"],f["good_stop"]) for f in result["UART_frames"]]==list(FRAMES)
    source=timetable()[0]
    transitions=[list(e) for i,e in enumerate(source) if (i==0 or e[1]!=source[i-1][1]) and e[0]<FINAL_DEADLINE]
    assert result["rx_edges"]==transitions,"external absolute RX calendar"
    assert [(e["stage"],e["clock"]) for e in result["actions"]]==list(CALENDAR)+[("final",FINAL_DEADLINE)]
    grants=[]
    for f in result["UART_frames"]:
        assert f["grant"]<f["start"] and f["end"]==f["start"]+FRAME_CLOCKS
        assert at(result["ready_edges"],f["start"])==1
        grant=next((t for t,v in result["ready_edges"] if t==f["grant"] and v==1),None)
        assert grant is not None and (not grants or grant>grants[-1]),"missing fresh READY grant"
        grants.append(grant)
    assert not any(v for _,v in result["error_edges"]),"ERROR unexpectedly rose"
    decoded=[(row,raw_decode(row["raw"])) for row in result["usb"]]
    expected=enum_wire()
    expected.update({"report-"+str(i):wire_for_read(p,retry=i==3) for i,p in enumerate(REPORTS)})
    for stage,want in expected.items():
        actual=[(row["direction"],p["packet"]) for row,p in decoded if row["stage"]==stage]
        assert actual==want,("whole physical packet sequence/first-response",stage)
    assert all(row["stage"] in expected for row,_ in decoded)
    assert all(2<=t<=6.5 for t in result["turnarounds"]),"response-turnaround envelope"
    assert max(row["end"] for row,_ in decoded if row["stage"].startswith("enum-"))<ENUM_DEADLINE
    ACK_gaps=[]
    service_rows=[]
    for i in range(8):
        rows=[(r,p) for r,p in decoded if r["stage"]=="report-"+str(i)]
        assert len(rows)==(11 if i==3 else 9)
        # Every host action follows the entire prior packet by64 clocks.
        for (r,p),(n,q) in zip(rows,rows[1:]):
            if n["direction"]=="host":
                assert q["first"]-p["eop_end"]==64,"full-EOP host-stage gap"
                if r["direction"]=="device" and p["packet"][0]==0x4b and q["packet"]==b"\xd2":
                    assert q["first"]-p["eop_j"]==96
                    ACK_gaps.append(dict(report=i,ACK=q["first"],first_J_gap=96,full_end_gap=64))
        record=result["services"][i]
        start=SOURCE_START+SLOT_CLOCKS*(i+1)
        deadline=min(start+SLOT_CLOCKS,FINAL_DEADLINE)
        assert record["report"]==i and record["start"]==rows[0][1]["first"]==start
        assert record["end"]==rows[-1][1]["eop_end"]+64<deadline
        assert record["deadline"]==deadline and record["slack"]==deadline-record["end"]
        assert record["clocks"]==record["end"]-start and record["expected_packets"]==len(rows)
        service_rows.append(record)
    assert len(result["services"])==8 and len(ACK_gaps)==8
    assert len(result["retry_observations"])==1,"required report3 retry observation"
    retry=result["retry_observations"][0]
    report3=[p for r,p in decoded if r["stage"]=="report-3"]
    assert retry["report"]==3 and retry["first"]==retry["retry"]==data(REPORTS[3]).hex()
    assert report3[4]["packet"]==report3[6]["packet"]==data(REPORTS[3])
    assert report3[5]["packet"]==usb.token(usb.PID_IN,5,0)  # no intervening ACK/status
    assert retry["first_complete_EOP"]==report3[4]["eop_end"]
    assert retry["retry_start"]==report3[5]["first"]==retry["first_complete_EOP"]+64
    assert retry["retry_complete_EOP"]==report3[6]["eop_end"]
    p0,p1=result["consumer_events"],result["producer_events"]
    l0,l1=result["usb_labels"],result["producer_labels"]
    detected=[e for e in _completed(p1,l1["wait_start"]+1) if e["logical_RX"]==0]
    samples=_completed(p1,l1["sample"]);stops=_completed(p1,l1["stop_delay"]+3)
    assert len(detected)==32 and len(samples)==256 and len(stops)==32
    for i,(frame,D) in enumerate(zip(FRAMES,detected)):
        start=frame[2];D=D["cycle"]
        assert start<=D-2<start+417
        for k,e in enumerate(samples[i*8:i*8+8]):
            lo=start+((k+1)*1250+2)//3;hi=start+((k+2)*1250+2)//3
            assert e["cycle"]==D+625+417*k
            assert lo<=e["source_cycle"]<hi and e["logical_RX"]==(frame[1]>>k)&1
        e=stops[i]
        assert e["cycle"]==D+3961 and e["logical_RX"]==1
        assert start+3750<=e["source_cycle"]<start+4167
    for e in p1:
        if e["flags"]&128:assert e["regs"][1:3]==e["after_regs"][1:3],"retained report overwritten"
    publications=[e for e in p1 if not e["flags"]&128 and e["after_flags"]&128]
    releases=_completed(p0,l0["ack_advance"]-1)
    assert len(publications)==len(releases)==len(result["ack_falls"])==8
    assert [e["cycle"] for e in result["ack_falls"]]==[e["cycle"] for e in releases]
    retention=[]
    for i,(pub,release) in enumerate(zip(publications,releases)):
        expected_words=[int.from_bytes(REPORTS[i][:2],"little"),int.from_bytes(REPORTS[i][2:],"little")]
        assert pub["after_regs"][1:3]==expected_words and pub["cycle"]<release["cycle"]
        assert release["flags"]&128 and not release["after_flags"]&128
        assert ACK_gaps[i]["ACK"]<=release["cycle"]<ACK_gaps[i]["ACK"]+608
        retention.append(dict(report=i,publish=pub["cycle"],release=release["cycle"],clocks=release["cycle"]-pub["cycle"]))
    clears=[e for e in p0 if e["pc"] in (l0["get_word"],l0["get_word"]+2) and e["completed"]]
    captures=[e for e in p0 if e["pc"] in (l0["get_word"]+1,l0["get_word"]+3) and e["completed"]]
    pubs=[e for e in p1 if not e["flags"]&64 and e["after_flags"]&64]
    pins=[e for e in p1 if isa.fields(e["word"])[0]==isa.OP_OUT and (isa.fields(e["word"])[2]>>15)&15==isa.PINS]
    handoffs=[]
    for clear in clears:
        publication=next(e for e in pubs if e["cycle"]>clear["cycle"])
        capture=next(e for e in captures if e["cycle"]>clear["cycle"])
        span=publication["cycle"]-clear["cycle"]
        assert span<=66 and capture["cycle"]==clear["cycle"]+1 and capture["pin"]==clear["pin"]
        assert not any(clear["cycle"]<e["cycle"]<=capture["cycle"] for e in pins)
        handoffs.append(dict(clear=clear["cycle"],capture=capture["cycle"],publication=publication["cycle"],clocks=span))
    assert len(clears)==36  #9 responses,4 byte clears each, including retry
    high=intervals(result["ready_edges"],FINAL_DEADLINE,bool,SOURCE_START)
    low=intervals(result["ready_edges"],FINAL_DEADLINE,lambda v:not v,SOURCE_START)
    full=intervals(result["occupancy_edges"],FINAL_DEADLINE,lambda v:v[0] and v[1]==4 and not v[2],SOURCE_START)
    retry_increment=report3[6]["eop_end"]-report3[4]["eop_end"]
    return dict(reports=[p.hex() for p in REPORTS],UART_frames=32,USB_packets=len(decoded),
        services=service_rows,minimum_slot_slack=min(r["slack"] for r in service_rows),
        retry_increment_clocks=retry_increment,ACK_gaps=ACK_gaps,retention=retention,F6_handoffs=handoffs,
        READY_observation_window=[SOURCE_START,FINAL_DEADLINE],READY_high=high,READY_low=low,
        READY_high_clocks=sum(b-a for a,b in high),READY_low_clocks=sum(b-a for a,b in low),
        two_full_groups_READY_low=full,
        two_full_groups_clocks=sum(b-a for a,b in full),
        maximum_pending_good_bytes=max(v[1] for _,v in result["occupancy_edges"]),
        maximum_queued_good_bytes=max(4*v[0]+v[1] for _,v in result["occupancy_edges"]),
        admission_cadence=FRAME_CLOCKS,resident=dict(start=SOURCE_START,end=SOURCE_END,bytes=32,
        clocks=SOURCE_END-SOURCE_START,offered_Bps=32*48000000/(SOURCE_END-SOURCE_START)),
        first_START_to_last_ACK_first=ACK_gaps[-1]["ACK"]-SOURCE_START,
        first_START_to_last_valid_ACK_release=retention[-1]["release"]-SOURCE_START,
        first_START_to_last_ACK_complete=next(p["eop_end"] for r,p in decoded if r["stage"]=="report-7" and r["direction"]=="host" and p["packet"]==b"\xd2")-SOURCE_START,
        first_START_to_complete_status=service_rows[-1]["end"]-SOURCE_START,
        cold_to_status_clocks=service_rows[-1]["end"],cold_to_fixed_drain_clocks=FINAL_DEADLINE,
        overflow_observed=len(result["overflow"]))


def mutate_and_reject(result):
    """Three disclosed saved-only controls; never synthetic DUT results."""
    outcomes=[]
    for name in ("changed_report_valid_CRC","missing_fresh_READY","missing_report3_retry"):
        bad=copy.deepcopy(result)
        if name=="changed_report_valid_CRC":
            row=next(r for r in bad["usb"] if r["direction"]=="device" and r["stage"]=="report-1" and raw_decode(r["raw"])["packet"][0]==0x4b)
            payload=bytes([REPORTS[1][0]^1])+REPORTS[1][1:]
            states=usb.encode(data(payload));start=raw_decode(row["raw"])["first"]
            row["raw"]=[[start+i*BIT,{usb.J:2,usb.K:1,usb.SE0:0}[s]] for i,s in enumerate(states)]
            row["raw"].append([start+len(states)*BIT,2])
            assert raw_decode(row["raw"])["packet"]==data(payload)
        elif name=="missing_fresh_READY":
            clock=bad["UART_frames"][5]["grant"]
            bad["ready_edges"]=[r for r in bad["ready_edges"] if r[0]!=clock]
        else:bad["retry_observations"]=[]
        try:audit(bad)
        except (AssertionError,StopIteration,ValueError) as exc:outcomes.append(dict(mutation=name,rejected=True,reason=str(exc)))
        else:raise AssertionError("saved mutation accepted "+name)
    return outcomes


def test_overlap4_complete_service(tmp_path):
    out=Path(os.environ.get("WIRELINGO_EVIDENCE") or tmp_path)
    out.mkdir(parents=True,exist_ok=True)
    raw_path=out/RAW_NAME
    assert not raw_path.exists(),"preserve prior raw artifact"
    programs=usb_uart_overlap4.assemble();rig=_Rig(programs);peer=None;error=None
    try:
        writes=usb_uart_overlap4.load_writes(programs)
        assert len(writes)==374
        rig.mh.apply(writes);rig.freeze_apis();peer=_Peer(rig)
        rig.absolute("enum",30000)
        for stage,fn in (
            ("enum-device",lambda:peer.get_descriptor(0,1,18)),
            ("enum-address",lambda:peer.control_write(0,bytes.fromhex("0005050000000000"))),
            ("enum-config",lambda:peer.get_descriptor(5,2,18)),
            ("enum-configure",lambda:peer.control_write(5,bytes.fromhex("0009010000000000"))),
            ("enum-get-config",lambda:peer.control_read(5,bytes.fromhex("8008000000000100")))):
            rig.stage=stage;fn()
        assert rig.mh.chip.cycle<ENUM_DEADLINE,"enumeration fixed deadline"
        for i,(stage,clock) in enumerate(CALENDAR[1:]):
            rig.absolute(stage,clock);peer.complete_report(i)
        rig.absolute("final",FINAL_DEADLINE)
        audit(rig.snapshot(None,peer));rig.mh.echo.check()
    except BaseException as exc:
        error=type(exc).__name__+": "+str(exc)
        raise
    finally:
        raw_path.write_text(json.dumps(rig.snapshot(error,peer),indent=2,default=_json_bytes)+"\n")
