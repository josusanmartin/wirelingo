"""Preparation of one fixed-calendar envelope3 sparse-message witness.

Default output is pytest tmp_path; a fresh nonempty WIRELINGO_EVIDENCE override
is optional. Every USB/UART input time is frozen before model construction.
No source inputs move in response to READY, registers, flags or USB replies.
No model result exists from the preparation ticket.
"""
import bisect
import copy
import hashlib
import json
import os
from pathlib import Path
from pe import isa, sim, usb, usbdev, usb_uart_envelope3
from pe.modelhost import ModelHost
from line_echo import EchoProbe
NODE="test/model/test_usb_uart_envelope3.py::test_envelope3_admission_and_retention"
RAW_NAME="envelope3-raw.json"
MAX_STEPS,BIT,FINAL_DEADLINE=1000000,32,620000
SOURCE_PAIR={"firmware/usb_uart_envelope3.pasm":"9c645b6b8cf9add98eaab15e732ea001066f45b365b704654c84b3d38f77d2a1",
             "firmware/uart_rx_envelope3.pasm":"1b64c2e4ae592d70b10356b33661c425985c52e521f6e4621007300c4ff534af"}

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
    if packet is not None and packet[0] in (0xe1,0x69,0x2d):
        assert len(packet)==3
        token=packet[1]|(packet[2]<<8);crc=31
        for k in range(11):
            feedback=(crc^(token>>k))&1;crc>>=1
            if feedback:crc^=0x14
        assert token>>11==(crc^31), 'rawtokenCRC5'
    return dict(packet=packet,bits=bits,first=first,se0=se0,eop_j=end_j,eop_end=end_j+32)


def _json_bytes(value):
    if type(value) in (bytes,bytearray):return {"__bytes_type__":type(value).__name__,"hex":value.hex()}
    raise TypeError(type(value).__name__)



# Pure closed-form source ledger. No opcode evaluator/instruction interpreter.
# First producer tick394 follows393 paid writes and registered start/restart.
def ceil_div(a,b): return -(-a//b)
def detected(start,first_idle):
    first_test=first_idle+24
    return first_test+26*max(0,ceil_div(start+2-first_test,26))
def after_good(D): return D+3994

def promotion(P,n):
    #5 setup instructions,4 clocks per padding byte, then17 throughF7.
    return dict(promote=P,publication=P+17+4*(3-n),
                READY=P+46+4*(3-n)-1,first_idle=P+46+4*(3-n))

# Source CRC/stuff arithmetic only; full EOP end and fixed idle are paid.
def report(payload):
    assert 1<=len(payload)<=3
    return payload+bytes(3-len(payload))+bytes([len(payload)])
REPORTS={"A":report(b"\x00"),"B":report(bytes.fromhex("00a5")),
         "C":report(bytes.fromhex("39d381")),"D":report(bytes.fromhex("1234"))}
def predicted_reply(token_start,packet):
    start=token_start+1157  #1120 full IN token +37 source turnaround
    end=start+len(usb.encode(packet))*BIT
    return dict(first=start,eop_j=end-32,eop_end=end)
def ack_start(IN_start,payload):return predicted_reply(IN_start,data(payload))["eop_j"]+64

# Initial empty timeout; thenone ordinaryzero frame and its unblocked1-byte seal.
INITIAL_EMPTY=dict(first_idle=427,C=427+47970,READY_low=427+47996+1)
EMPTY_REOPEN_FIRST=INITIAL_EMPTY["C"]+62
A_D=detected(50000,EMPTY_REOPEN_FIRST)
A_IDLE=after_good(A_D)
A_SEAL_C=A_IDLE+47970
A_PROMOTE=promotion(A_SEAL_C+61,1)
#Five separate finalidle service calls, all legal raw STARTs whileREADY remainshigh.
LATE=[]
first_idle=A_PROMOTE["first_idle"]
for offset,value,group in ((22,0,"B"),(23,0xa5,"B")):
    C=first_idle+47970;D=C+(24 if offset==22 else 30)
    LATE.append(dict(offset=offset,group=group,byte=value,C=C,start=C+offset,D=D,
                     ordinary_test=C+24,READY_low_instruction=C+26,
                     physical_timeout_close=None if offset==22 else C+27,final_recheck=C+30))
    first_idle=after_good(D)
B_SEAL_C=first_idle+47970
#A's fixed ACK is earlier thanB's seal, soB promotes withoutACK polling ambiguity.
A_ACK=ack_start(246000,REPORTS["A"])
assert A_ACK+541 < B_SEAL_C
B_PROMOTE=promotion(B_SEAL_C+61,2)
first_idle=B_PROMOTE["first_idle"]
for offset,value in ((24,0x39),(25,0xd3),(26,0x81)):
    C=first_idle+47970;D=C+30
    LATE.append(dict(offset=offset,group="C",byte=value,C=C,start=C+offset,D=D,
                     ordinary_test=C+24,READY_low_instruction=C+26,
                     physical_timeout_close=C+27,final_recheck=C+30))
    first_idle=after_good(D)
C_FULL_CALL=LATE[-1]["D"]+3966
B_ACK=ack_start(420000,REPORTS["B"])
#F7 poll usesoldflags. Coincident clearisobserved only onnext25-clockloop.
B_ACK_FLAG=B_ACK+541
C_PROMOTE=promotion(C_FULL_CALL+24+25*((B_ACK_FLAG-(C_FULL_CALL+24))//25+1)+1,3)
D0=detected(430000,C_PROMOTE["first_idle"])
DBAD=detected(435000,after_good(D0))
BAD_IDLE_TEST=DBAD+3991+25*max(0,ceil_div(435000+4167+2-(DBAD+3991),25))
D1=detected(440000,BAD_IDLE_TEST+3)
D_SEAL_C=after_good(D1)+47970
C_ACK=ack_start(500000,REPORTS["C"])
D_FULL_CALL=D_SEAL_C+36
D_PROMOTE=promotion(D_FULL_CALL+24+25*((C_ACK+541-(D_FULL_CALL+24))//25+1)+1,2)
FRAMES=tuple([("A",0,50000,True)]+[(x["group"],x["byte"],x["start"],True) for x in LATE]+
             [("D",0x12,430000,True),("D",0xa6,435000,False),("D",0x34,440000,True)])
DETECT=tuple([A_D]+[x["D"] for x in LATE]+[D0,DBAD,D1])
SOURCE_GRANTS=(INITIAL_EMPTY['C']+61,A_PROMOTE['READY'],LATE[0]['D']+3993,
    B_PROMOTE['READY'],LATE[2]['D']+3993,LATE[3]['D']+3993,
    C_PROMOTE['READY'],D0+3993,BAD_IDLE_TEST+2)
WORST=dict(IN_start=LATE[0]["D"]+581-2090,clear=LATE[0]["D"]+581,
           publication=LATE[0]["D"]+647,next_OUT_deadline=LATE[0]["D"]+652)


def timetable():
    edges=[(0,1)];windows=[]
    for index,(group,byte,start,good) in enumerate(FRAMES):
        for slot,level in enumerate([0]+[(byte>>bit)&1 for bit in range(8)]+[int(good)]):
            begin=start+(slot*1250+2)//3;end=start+((slot+1)*1250+2)//3
            edges.append((begin,level));windows.append(dict(index=index,report=group,slot=slot,begin=begin,end=end,level=level))
        edges.append((start+4167,1))
    return edges,windows


#Every hostpacketisdatednow, beforetheDUTexists. Responses are observations,
#not timing feedback.69clfromEOPJ/37clfromcompletehostEOPisconditional source arithmetic.
def usb_calendar():
    packets=[];resets=[];actions=[];drains=[]
    def send(stage,t,packet):
        states=usb.encode(packet);end=t+len(states)*BIT
        packets.append(dict(direction="host",stage=stage,clock=t,end=end,packet=packet.hex(),states=states))
        return end
    def reply(stage,host_end,packet):
        t=host_end+37;end=t+len(usb.encode(packet))*BIT
        packets.append(dict(direction="device",stage=stage,clock=host_end,end=end,
                            expected_first=t,packet=packet.hex()))
        return end+32  #fixed32idleafterthecompleteJ; firstJ->nextinput64
    def setup(stage,t,address,request):
        end=send(stage,t,usb.token(usb.PID_SETUP,address,0))
        end=send(stage,end+64,usb.data_packet(usb.PID_DATA0,request))
        return reply(stage,end,b"\xd2")
    def IN(stage,t,address,payload=None,ack=False,stall=False,malformed=False):
        end=send(stage,t,usb.token(usb.PID_IN,address,0))
        packet=b"\x1e" if stall else data(payload)
        next_t=reply(stage,end,packet)
        if ack or malformed:
            if malformed:
                states=usb.encode(b"\xd2");states=states[:-3]+[usb.J if states[-4]==usb.K else usb.K]+states[-3:]
                end=next_t+len(states)*BIT
                packets.append(dict(direction="host",stage=stage+"-bad-ACK",clock=next_t,end=end,packet=None,states=states))
            else:end=send(stage+"-ACK",next_t,b"\xd2")
            return end+64
        return next_t+32
    def status(stage,t,address):
        end=send(stage,t,usb.token(usb.PID_OUT,address,0))
        end=send(stage,end+64,data(b""))
        return reply(stage,end,b"\xd2")
    def read(stage,t,address,request,payload,stall=False,alias=False):
        t=setup(stage,t,address,request)
        if stall:return IN(stage,t,address,stall=True)
        for i in range(0,len(payload),8):
            end=send(stage,t,usb.token(usb.PID_IN,address,0))
            t=reply(stage,end,data(payload[i:i+8],1^((i//8)&1)))
            t=send(stage+"-ACK",t,b"\xd2")+64
        if alias:
            # Explicit adversarial extra IN at completed cursor37, before status.
            # Retain first-J64/full-EOP32 ACK gap and every other input date.
            t=IN(stage+'-alias',t,address,b'',ack=True)
        return status(stage,t,address)
    def write(stage,t,address,request):
        t=setup(stage,t,address,request)
        return IN(stage,t,address,b"",True)
    def enum(prefix,t,full=True,configuration=1):
        t=read(prefix+"device",t,0,bytes.fromhex("8006000100001200"),usbdev.DEVICE_DESC)
        t=write(prefix+"address",t,0,bytes.fromhex("0005050000000000"))
        if full:t=read(prefix+"descriptor",t,5,bytes.fromhex("8006000200001200"),usbdev.CONFIG_DESC)
        t=write(prefix+"configuration",t,5,bytes([0,9,configuration,0,0,0,0,0]))
        return read(prefix+"get-config",t,5,bytes.fromhex("8008000000000100"),bytes([configuration]))
    def action(stage,t,fn):
        actions.append(dict(stage=stage,clock=t));end=fn(t)
        drains.append(dict(stage=stage,start=t,helper_return=end,fixed_idle_gap=32,source_only=True))
        return end
    action("empty",55000,lambda t:read("empty",t,0,usb_uart_envelope3.vendor_request(),b"",True))
    action("enum",65000,lambda t:enum("enum-",t))
    action("A-setup",140000,lambda t:setup("A-setup",t,5,usb_uart_envelope3.vendor_request()))
    action("A-first",WORST["IN_start"],lambda t:IN("A-first",t,5,REPORTS["A"]))
    action("A-retry",154000,lambda t:IN("A-retry",t,5,REPORTS["A"],malformed=True))
    action("descriptor-retain",160000,lambda t:read("descriptor-retain",t,5,bytes.fromhex("8006000200001200"),usbdev.CONFIG_DESC,alias=True))
    resets.append(dict(stage="reset-A-partial-B",clock=185000,end=185144,recovery_end=189944))
    action("reenum",190000,lambda t:enum("reenum-",t,False,0))
    action("A-recover-setup",240000,lambda t:setup("A-recover-setup",t,5,usb_uart_envelope3.vendor_request()))
    end=action("A-recover",246000,lambda t:IN("A-recover",t,5,REPORTS["A"],ack=True))
    action("A-status",end,lambda t:status("A-status",t,5))
    action("B-setup",270000,lambda t:setup("B-setup",t,5,usb_uart_envelope3.vendor_request()))
    action("B-first",276000,lambda t:IN("B-first",t,5,REPORTS["B"]))
    action("abandon-B",282000,lambda t:read("abandon-B",t,5,bytes.fromhex("8006000200001200"),usbdev.CONFIG_DESC))
    action("B-recover-setup",414000,lambda t:setup("B-recover-setup",t,5,usb_uart_envelope3.vendor_request()))
    end=action("B-recover",420000,lambda t:IN("B-recover",t,5,REPORTS["B"],ack=True))
    action("B-status",end,lambda t:status("B-status",t,5))
    action("C-setup",490000,lambda t:setup("C-setup",t,5,usb_uart_envelope3.vendor_request()))
    end=action("C-read",500000,lambda t:IN("C-read",t,5,REPORTS["C"],ack=True))
    action("C-status",end,lambda t:status("C-status",t,5))
    resets.append(dict(stage="reset-D-error",clock=515000,end=515144,recovery_end=519944))
    action("D-setup",520000,lambda t:setup("D-setup",t,0,usb_uart_envelope3.vendor_request()))
    end=action("D-read",530000,lambda t:IN("D-read",t,0,REPORTS["D"],ack=True))
    action("D-status",end,lambda t:status("D-status",t,0))
    action("final-empty",590000,lambda t:read("final-empty",t,0,usb_uart_envelope3.vendor_request(),b"",True))
    packets.sort(key=lambda row:row["clock"])
    return packets,resets,actions,drains

PACKET_PLAN,RESETS,CALENDAR,DRAINS=usb_calendar()

def calendar_document():
    return dict(frames=list(FRAMES),detections=list(DETECT),grants=list(SOURCE_GRANTS),late=LATE,initial_empty=INITIAL_EMPTY,
                A_seal=A_SEAL_C,A_promote=A_PROMOTE,B_seal=B_SEAL_C,B_promote=B_PROMOTE,
                C_full_call=C_FULL_CALL,C_promote=C_PROMOTE,D_seal=D_SEAL_C,D_promote=D_PROMOTE,
                worst=WORST,packets=PACKET_PLAN,resets=RESETS,actions=CALENDAR,drains=DRAINS,final=FINAL_DEADLINE)

def calendar_sha256():return hashlib.sha256(json.dumps(calendar_document(),sort_keys=True).encode()).hexdigest()
def source_hashes():
    root=Path(__file__).resolve().parents[2]
    paths=list(SOURCE_PAIR)+["tools/pe/usb_uart_envelope3.py","test/model/test_usb_uart_envelope3.py","docs/usb-uart-envelope3.md"]
    return {p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in paths}

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
        self.host_calls=[];self.active_strobes=[];self.driver_overlap=[];self.actions=[];self.tail=[]
        self.grant=-1;self.used_grant=-1;self.previous_end=0;self.ready=0
        self.labels0=programs[0].labels;self.labels1={k:v+256 for k,v in programs[1].labels.items()}
        l=self.labels0
        self.watch0={l['got_in']+2,l['s_bit'],*range(l['get_word'],l['get_word']+4),
                     *range(l['got_ack'],l['ack_advance']+4)}
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
            before1=dict(pc=q1.pc,dcnt=q1.dcnt,isr=q1.isr,regs=list(q1.r))
            answer=original((ui_in&~1)|raw_rx,uio_in,**kw)
            if oldflags&128 and not c.flags&128:
                self.ack_falls.append(dict(cycle=cycle,flags=oldflags,after_flags=c.flags,
                    before=before1,after=dict(pc=q1.pc,dcnt=q1.dcnt,isr=q1.isr,regs=list(q1.r))))
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
            geometry=[c.ncores,c.nbanks,c.bank_words,c.fifo_depth],cold_write_count=usb_uart_envelope3.COLD_WRITES,
            usb_labels=self.labels0,producer_labels=self.labels1,source_calendar=[list(r) for r in FRAMES],actions=self.actions,
            calendar_sha256=calendar_sha256(),source_sha256=source_hashes(),sync_bypass=c.sync_bypass,
            UART_frames=self.frames,source_edges=self.source_edges,rx_edges=self.rx_edges,
            ready_edges=self.ready_edges,error_edges=self.error_edges,flag_edges=self.flag_edges,
            consumer_events=self.consumer,producer_events=self.producer,ack_falls=self.ack_falls,usb=self.usb,raw_bus_edges=self.raw_edges,
            host_calls=self.host_calls,active_strobes=self.active_strobes,driver_overlap=self.driver_overlap,
            overflow=self.overflow,stats=c.stats,tail=self.tail,final_flags=c.flags,final_pin_out=c.pin_out,
            final_FIFOs=[[len(q.txf),len(q.rxf)] for q in c.cores[:2]],
            peer_log=[] if peer is None else peer.log,turnarounds=[] if peer is None else peer.turnarounds,
            image_sha256=[hashlib.sha256(b''.join(w.to_bytes(4,'little') for w in p.relocate(i*256))).hexdigest()
                          for i,p in enumerate(self.programs)])



class _FixedPeer:
    """Clock-only pin driver. DUT responses are captured without moving input dates."""
    def __init__(self,rig):self.rig=rig;self.mh=rig.mh;self.log=[];self.turnarounds=[]
    def host(self,row):
        self.mh.ext_oe|=3
        for state in row['states']:
            self.mh.uio=(self.mh.uio&~3)|{usb.J:2,usb.K:1,usb.SE0:0}[state]
            self.mh.step(BIT)
        self.mh.ext_oe&=~3
        assert self.mh.chip.cycle==row['end']
        self.rig.usb.append(dict(row,raw=self.rig.raw(row['clock'],row['end'])))
    def device(self,row):
        self.mh.step(row['end']-row['clock'])
        actual=dict(row,raw=self.rig.raw(row['clock'],row['end']))
        self.rig.usb.append(actual)
        #The declared observationwindow is fixed. First deviation terminates;
        #the next input is never repaired, retried, shifted or state-triggered.
        decoded=raw_decode(actual['raw'])
        assert decoded['packet']==bytes.fromhex(row['packet']),('first reply',row['stage'])
        assert decoded['first']==row['expected_first'] and decoded['eop_end']==row['end'], 'fixed source response chronology'
    def reset(self,row):
        self.mh.ext_oe|=3;self.mh.uio&=~3
        self.mh.step(row['end']-row['clock']);self.mh.ext_oe&=~3
        self.mh.step(row['recovery_end']-row['end'])


def _completed(rows,pc):return [e for e in rows if e['pc']==pc and e['completed']]
def retained_register_report(regs):return (regs[1]&65535).to_bytes(2,'little')+(regs[2]&65535).to_bytes(2,'little')

def audit(result):
    """Pure chronological raw oracle, with mandatory admission and supplier evidence."""
    assert result['node']==NODE and result['error'] is None, 'failed nominal outcome cannot be repaired later'
    assert result['geometry']==[2,2,256,8] and result['sync_bypass']==0
    assert result['chip_step_calls']==result['advanced_cycles']==FINAL_DEADLINE<=MAX_STEPS
    assert result['cold_write_count']==393 and result['calendar_sha256']==calendar_sha256()
    assert all(result['source_sha256'].get(k)==v for k,v in SOURCE_PAIR.items())
    assert result['source_calendar']==[list(r) for r in FRAMES]
    assert result['actions']==[dict(stage=r['stage'],clock=r['clock']) for r in sorted(PACKET_PLAN+RESETS,key=lambda r:r['clock'])]+[dict(stage='final',clock=FINAL_DEADLINE)]
    assert not result['active_strobes'] and not result['driver_overlap']
    assert not any(e['cycle']>=393 for e in result['host_calls']), 'active loading-host API or bus traffic'
    assert result['final_FIFOs']==[[0,0],[0,0]]
    assert [(f['report'],f['byte'],f['start'],f['good_stop']) for f in result['UART_frames']]==list(FRAMES)
    transitions=[list(e) for i,e in enumerate(timetable()[0]) if (i==0 or e[1]!=timetable()[0][i-1][1]) and e[0]<FINAL_DEADLINE]
    assert result['rx_edges']==transitions
    assert [f['grant'] for f in result['UART_frames']]==list(SOURCE_GRANTS), 'sourcepredictedfreshREADYgrants'
    for i,f in enumerate(result['UART_frames']):
        assert f['grant']<f['start'] and f['end']==f['start']+4167 and at(result['ready_edges'],f['start'])==1
        if i:assert f['grant']>result['UART_frames'][i-1]['grant'] and f['start']>=result['UART_frames'][i-1]['end']
    assert len(result['usb'])==len(PACKET_PLAN)
    decoded=[]
    for row,plan in zip(result['usb'],PACKET_PLAN):
        assert (row['direction'],row['stage'],row['clock'],row['end'])==(plan['direction'],plan['stage'],plan['clock'],plan['end'])
        q=raw_decode(row['raw']);decoded.append(dict(stage=row['stage'],direction=row['direction'],
            packet=None if q['packet'] is None else q['packet'].hex(),first=q['first'],eop_j=q['eop_j'],eop_end=q['eop_end']))
        if plan['packet'] is None:
            assert q['bits']==[0,0,0,0,0,0,0,1,0,1,0,0,1,0,1,1,0], 'malformed ACK raw extra0'
        else:assert q['packet']==bytes.fromhex(plan['packet']), 'exact packet/PID/payload/count/CRC'
        assert q['first']==(plan['clock'] if plan['direction']=='host' else plan['expected_first'])
        assert q['eop_end']==plan['end'], 'complete EOP or split/underrun'
    for row in result['usb']:
        if row['direction']=='host':
            #Raw symbols, not packet labels, determine actual host bits.
            states=[{usb.J:2,usb.K:1,usb.SE0:0}[s] for s in row['states']]
            assert all(at(row['raw'],row['clock']+i*BIT+16)==v for i,v in enumerate(states))
    p0,p1=result['consumer_events'],result['producer_events'];l0,l1=result['usb_labels'],result['producer_labels']
    ordinary=[e for e in _completed(p1,l1['wait_start']+1) if not e['logical_RX']]
    rechecks=[e for e in _completed(p1,l1['wait_start']+5) if not e['logical_RX']]
    assert sorted(e['cycle'] for e in ordinary+rechecks)==list(DETECT)
    sample_rows=_completed(p1,l1['sample']);stop_rows=_completed(p1,l1['stop_delay']+3)
    assert len(sample_rows)==8*len(FRAMES) and len(stop_rows)==len(FRAMES)
    for i,(f,D) in enumerate(zip(FRAMES,DETECT)):
        assert [e['cycle'] for e in sample_rows[8*i:8*i+8]]==[D+625+417*k for k in range(8)]
        assert stop_rows[i]['cycle']==D+3961 and stop_rows[i]['logical_RX']==int(f[3])
        for k,e in enumerate(sample_rows[8*i:8*i+8],1):
            begin=f[2]+(k*1250+2)//3;end=f[2]+((k+1)*1250+2)//3
            assert begin<=e['source_cycle']<end and e['logical_RX']==(f[1]>>(k-1))&1
    late=[]
    for x in LATE:
        C=x['C'];offset=x['offset']
        call=next(e for e in p1 if e['cycle']==C and e['pc']==l1['wait_start'])
        assert call['regs'][0]==0, 'final idle counter not zero'
        test=next(e for e in p1 if e['cycle']==C+24 and e['pc']==l1['wait_start']+1)
        assert test['logical_RX']==(0 if offset==22 else 1)
        expected_close=C+(26 if offset==22 else 27)
        assert [t for t,v in result['ready_edges'] if t>=x['start'] and not v][0]==expected_close
        assert at(result['ready_edges'],x['start'])==1 and x['start']<expected_close
        if offset!=22:
            close=next(e for e in p1 if e['cycle']==C+26 and e['pc']==l1['wait_start']+3)
            last=next(e for e in p1 if e['cycle']==C+30 and e['pc']==l1['wait_start']+5)
            assert close['completed'] and last['completed'] and last['logical_RX']==0
        late.append(dict(offset=offset,C=C,start=x['start'],physical_READY_close=expected_close,D=x['D'],route='ordinary' if offset==22 else 'final_recheck'))
    #Empty finalidle doesnotpublish aphantom report; everyF7rise bindscount+data.
    publications=[e for e in p1 if not e['flags']&128 and e['after_flags']&128]
    assert [retained_register_report(e['after_regs']) for e in publications]==list(REPORTS.values())
    assert publications[0]['cycle']==A_PROMOTE['publication'] and publications[1]['cycle']==B_PROMOTE['publication']
    assert publications[2]['cycle']==C_PROMOTE['publication'] and publications[3]['cycle']==D_PROMOTE['publication']
    assert not any(e['cycle']<INITIAL_EMPTY['READY_low']+30 and e['after_flags']&128 for e in p1)
    for e in p1:
        if e['flags']&128:assert e['regs'][1:3]==e['after_regs'][1:3], 'retained payload/count overwritten'
    release=[e for e in p0 if e['completed'] and e['pc']==l0['ack_advance']-1]
    assert len(release)==4
    expected_ACK_stages=['A-recover-ACK','B-recover-ACK','C-read-ACK','D-read-ACK']
    for e,stage in zip(release,expected_ACK_stages):
        assert e['stage']==stage and e['regs'][2]==4133 and e['flags']&128 and not e['after_flags']&128
    assert [e['cycle'] for e in result['ack_falls']]==[e['cycle'] for e in release]
    for e in result['ack_falls']:
        assert e['before']['regs'][1:3]==e['after']['regs'][1:3], 'oldACK changed count before promotion'
    #A_ACK leavespartialB2 unchanged, B_ACK leavesfullC3 intact pendingpromotion.
    assert result['ack_falls'][0]['before']['isr']==result['ack_falls'][0]['after']['isr']==0xa5000000
    assert result['ack_falls'][1]['before']['isr']==result['ack_falls'][1]['after']['isr']==0x81d33900
    f7=[(t,int(bool(v&128))) for t,v in result['flag_edges']]
    f7=[r for i,r in enumerate(f7) if not i or r[1]!=f7[i-1][1]]
    assert [v for _,v in f7]==[0,1,0,1,0,1,0,1,0] and not result['final_flags']&128
    # Ordinary final DATA ACK compares2083, then advances37. The explicit
    # extra empty DATA1/ACK separately exercises comparison37 versus full4133.
    descriptor_ack=[r for r in PACKET_PLAN if r['stage']=='descriptor-retain-ACK' and r['packet']=='d2'][-1]
    alias_ack=next(r for r in PACKET_PLAN if r['stage']=='descriptor-retain-alias-ACK')
    alias_data=next(r for r in result['usb'] if r['stage']=='descriptor-retain-alias' and r['direction']=='device')
    assert raw_decode(alias_data['raw'])['packet']==data(b''), 'alias DATA1 is empty, not an envelope with count0'
    for ACK,cursor in ((descriptor_ack,2083),(alias_ack,37)):
        near=[e for e in p0 if ACK['clock']<=e['cycle']<ACK['end']+64]
        gate=next(e for e in near if e['completed'] and e['pc']==l0['ack_end'])
        sent_clear=next(e for e in near if e['completed'] and e['pc']==l0['ack_end']+1)
        compare=next(e for e in near if e['completed'] and e['pc']==l0['ack_end']+3)
        assert gate['cycle']==ACK['clock']+536 and gate['flags']&4 and gate['flags']&128
        assert sent_clear['cycle']==gate['cycle']+1 and sent_clear['flags']&4 and not sent_clear['after_flags']&4
        assert compare['cycle']==ACK['clock']+539 and compare['regs'][2]==cursor
        assert not compare['flags']&6 and compare['flags']&128 and compare['after_flags']&128, 'full cursor comparison after F_SENT clear'
        assert any(e['completed'] and e['pc']==l0['ack_advance']+3 and e['after_regs'][2]==37 and e['after_flags']&128 for e in near), 'descriptor completedcursor37 ACK witness'
        assert not any(ACK['clock']<=e['cycle']<ACK['end']+64 for e in result['ack_falls'])
        assert at(result['flag_edges'],ACK['clock'])&128
        assert all(v&128 for t,v in result['flag_edges'] if ACK['clock']<=t<ACK['end']+64), 'descriptor alias cleared retained F7'
        retained=[e for e in p1 if ACK['clock']<=e['cycle']<ACK['end']+64]
        assert retained and all(retained_register_report(e['regs'])==REPORTS['A'] and retained_register_report(e['after_regs'])==REPORTS['A'] for e in retained), 'descriptor changed retained payload/count'
    #Closedfull/partial pending states cannotclaim anothergrant before oldACK.
    C_sealed=LATE[-1]['C']+27
    D_sealed=D_SEAL_C+27
    for begin,relevant in ((C_sealed,C_PROMOTE),(D_sealed,D_PROMOTE)):
        #Use actualnextpromotionepoch,never drivefromit.
        assert not any(begin<=t<relevant['READY'] and v for t,v in result['ready_edges'])
    error_rises=[t for t,v in result['error_edges'] if v]
    assert error_rises==[DBAD+3962+1]
    assert not any(t>=error_rises[0] and not v for t,v in result['error_edges']), 'ERROR notsticky acrossUSBreset'
    #Rawresetedges andpassiveprefix/retention snapshots identify conditionalreset.
    for reset in RESETS:
        assert at(result['raw_bus_edges'],reset['clock'])==0 and at(result['raw_bus_edges'],reset['end'])==2
    reset_prefix=[e for e in p1 if RESETS[0]['clock']<=e['cycle']<RESETS[0]['recovery_end']]
    assert reset_prefix and all(e['isr']==0 and e['regs'][3]>>8==2 for e in reset_prefix), 'pendingzero byte prefix changed onreset'
    clears=[e for e in p0 if e['completed'] and e['pc'] in (l0['get_word'],l0['get_word']+2)]
    captures=[e for e in p0 if e['completed'] and e['pc'] in (l0['get_word']+1,l0['get_word']+3)]
    pubs=[e for e in p1 if not e['flags']&64 and e['after_flags']&64]
    pin_writes=[e for e in p1 if isa.fields(e['word'])[0]==isa.OP_OUT and (isa.fields(e['word'])[2]>>15)&15==isa.PINS]
    spans=[]
    for clear in clears:
        pub=next(e for e in pubs if e['cycle']>clear['cycle']);capture=next(e for e in captures if e['cycle']>clear['cycle'])
        assert pub['cycle']-clear['cycle']<=66 and capture['cycle']==clear['cycle']+1
        assert capture['pin']==clear['pin'] and not any(clear['cycle']<e['cycle']<=capture['cycle'] for e in pin_writes)
        spans.append(dict(clear=clear['cycle'],publication=pub['cycle'],span=pub['cycle']-clear['cycle']))
    S=WORST['clear']
    clear=next(e for e in clears if e['cycle']==S)
    poll=next(e for e in p1 if e['cycle']==S and e['pc']==l1['service']+1)
    assert clear['flags']&64 and poll['flags']&64 and not poll['after_flags']&64
    assert any(e['cycle']==WORST['publication'] and e['pc']==l1['publish'] for e in pubs), 'actual66-clock target absent'
    next_OUT=next(e for e in p0 if e['cycle']>S and e['completed'] and e['pc']==l0['s_bit'])
    last_OUT=next(e for e in p0 if e['cycle']==S-5 and e['completed'] and e['pc']==l0['s_bit'])
    assert last_OUT['regs'][0]==17 and next_OUT['regs'][0]==16
    assert next_OUT['cycle']<=WORST['next_OUT_deadline']==S-5+76 and next_OUT['cycle']<S-5+95
    assert any(r['span']==66 and r['clear']==S for r in spans)
    return dict(envelopes=[r.hex() for r in REPORTS.values()],payload_bytes=sum(r[3] for r in REPORTS.values()),padding_bytes=sum(3-r[3] for r in REPORTS.values()),count_bytes=4,
                UART_frames=len(FRAMES),late_START=late,actual66=True,F6_spans=spans,publications=[e['cycle'] for e in publications],
                release=[e['cycle'] for e in release],ERROR_rise=error_rises[0],raw_packets=len(decoded),decoded_packets=decoded,
                cold_writes=393,program_words=[256,112],program_bytes=1472,max_payload_storage=6,
                overflow_observed=len(result['overflow']),drains=DRAINS,cycles_per_payload_byte=FINAL_DEADLINE/sum(r[3] for r in REPORTS.values()))


def mutate_and_reject(result):
    """Three future saved-only controls, never DUT behavior or a substitute PASS."""
    outcomes=[]
    for name in ('wrong_count_valid_CRC','missing_legal_late_START','descriptor_ACK_releases_F7'):
        bad=copy.deepcopy(result)
        if name=='wrong_count_valid_CRC':
            row=next(r for r in bad['usb'] if r['direction']=='device' and r['stage']=='A-retry')
            changed=REPORTS['A'][:3]+b'\x02';states=usb.encode(data(changed))
            start=raw_decode(row['raw'])['first']
            row['raw']=[[start+i*BIT,{usb.J:2,usb.K:1,usb.SE0:0}[s]] for i,s in enumerate(states)]+[[start+len(states)*BIT,2]]
            assert raw_decode(row['raw'])['packet']==data(changed), 'mutantCRC precondition'
        elif name=='missing_legal_late_START':
            C=LATE[-1]['C']
            bad['producer_events']=[e for e in bad['producer_events'] if e['cycle']!=C+30]
        else:
            ACK=next(r for r in PACKET_PLAN if r['stage']=='descriptor-retain-alias-ACK')
            t=ACK['clock']+542  # visible state after hypothetical wrong clear atACK+541
            v=at(bad['flag_edges'],t)&~128
            bad['flag_edges'].append([t,v]);bad['flag_edges'].sort(key=lambda e:e[0])
        try:audit(bad)
        except (AssertionError,StopIteration,ValueError) as exc:outcomes.append(dict(mutation=name,rejected=True,reason=str(exc)))
        else:raise AssertionError('saved negative accepted '+name)
    return outcomes


def test_envelope3_admission_and_retention(tmp_path):
    out=Path(os.environ.get('WIRELINGO_EVIDENCE') or tmp_path);out.mkdir(parents=True,exist_ok=True)
    raw_path=out/RAW_NAME
    assert not raw_path.exists() and not raw_path.is_symlink(), 'preserve earlierraw'
    programs=usb_uart_envelope3.assemble();rig=_Rig(programs);peer=None;error=None
    try:
        writes=usb_uart_envelope3.load_writes(programs);assert len(writes)==393
        rig.mh.apply(writes);rig.freeze_apis();peer=_FixedPeer(rig)
        for row in sorted(PACKET_PLAN+RESETS,key=lambda r:r['clock']):
            rig.absolute(row['stage'],row['clock'])
            if row in RESETS:peer.reset(row)
            elif row['direction']=='host':peer.host(row)
            else:peer.device(row)
        rig.absolute('final',FINAL_DEADLINE)
        result=rig.snapshot(None,peer);verdict=audit(result);rig.mh.echo.check()
        mutate_and_reject(result)  # saved-trace oracle controls
        (out/'oracle.json').write_text(json.dumps(verdict,indent=2)+'\n')
    except BaseException as exc:
        error=type(exc).__name__+': '+str(exc);raise
    finally:
        raw_path.write_text(json.dumps(rig.snapshot(error,peer),indent=2,default=_json_bytes)+'\n')
