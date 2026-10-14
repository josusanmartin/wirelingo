"""Prepared fixed-calendar overlap4 witness. No preparation result is DUT evidence.

One ordinary node, one cold load; UART edges advance inside every Chip.step,
including all USB peer calls. Expected bytes come only from the external list.
"""
import bisect
import copy
import hashlib
import json
import os
from pathlib import Path

from pe import isa, sim, usb, usbdev, usb_uart_overlap4
from pe.modelhost import ModelHost
from pe.usbhost import ControlHost, Stall

NODE = "test/model/test_usb_uart_overlap4.py::test_overlap4_retained_and_next"
RAW_NAME = "overlap4-raw.json"
MAX_STEPS, BIT = 400000, 32
# report, byte, fixed physical START, valid STOP; each whole frame is4167 clocks.
FRAMES = (("A",0,5000,True),("A",0,10000,True),("A",0,15000,True),("A",0,20000,True),
    ("B",0x39,150009,True),("B",0xd3,155000,True),("B",0x81,165000,True),("B",0x5e,170000,True),
    ("C",0x12,190000,True),("C",0x34,195000,True),("C",0xa6,200000,False),
    ("C",0x56,205000,True),("C",0x78,210000,True),
    ("D",0x9a,230000,True),("D",0xbc,235000,True),("D",0xde,240000,True),("D",0xf0,245000,True))
REPORTS = {name:bytes(byte for report,byte,_,good in FRAMES if report==name and good)
           for name in "ABCD"}
# Frozen predictions must be observed, not used to schedule input.
DETECT = (5006,10023,15015,20007,150011,155003,165020,170012,
          190016,195008,200025,205017,210009,230013,235005,240022,245014)
CALENDAR = (("enum",30000),("A-setup",140000),("A-first",148502),
    ("A-bad-ACK",152000),("A-retry",154335),("A-ACK",157700),("A-status",159000),
    ("B-setup",180000),("B-first",187000),("B-retry",200115),("B-ACK",203448),("B-status",204500),
    ("C-setup",220000),("C-first",232000),("C-retry",256667),("C-ACK",260000),("C-status",261000),
    ("D-setup",270000),("D-first",277667),("D-ACK",281000),("D-status",282000),("empty",290000))
FINAL_DEADLINE = 310000


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
    "enum-get-config":[b"\xd2",data(b"\x01"),b"\xd2"],"empty":[b"\xd2",b"\x1e"]}
for report in REPORTS:
    EXPECTED[report+"-setup"]=[b"\xd2"]
    EXPECTED[report+"-first"]=[data(REPORTS[report])]
    EXPECTED[report+"-status"]=[b"\xd2"]
for report in "ABC":EXPECTED[report+"-retry"]=[data(REPORTS[report])]


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
        self.rig.usb.append(dict(direction='host',stage=self.rig.stage,begin=begin,end=end,
                                 raw=self.rig.raw(begin,end),source_states=states))

    def _response(self):
        begin=self.mh.chip.cycle;reply=super()._response()
        self.mh.step(32)  # complete the EOP J cell; does not shift a fixed next action
        end=self.mh.chip.cycle
        self.rig.usb.append(dict(direction='device',stage=self.rig.stage,begin=begin,end=end,
                                 raw=self.rig.raw(begin,end),helper_reply=None if reply is None else reply.hex()))
        stage=self.rig.stage;assert stage in EXPECTED
        index=self.counts[stage];assert index<len(EXPECTED[stage]),'hidden response retry'
        assert reply==EXPECTED[stage][index],('first-response mismatch',stage,index,reply)
        self.counts[stage]+=1
        return reply

    def poll(self,report):
        self._send(usb.token(usb.PID_IN,5,0));return self._response()

    def fixed_ack(self,malformed=False):
        states=usb.encode(b'\xd2')
        if malformed:states=states[:-3]+[usb.J if states[-4]==usb.K else usb.K]+states[-3:]
        self._drive(states);self.gap()


def _completed(rows,pc):
    return [e for e in rows if e['pc']==pc and e['completed']]


def audit(result):
    """Saved-only byte oracle AND mandatory passive phase coverage; no DUT access."""
    assert result['node']==NODE and result['error'] is None
    assert result['geometry']==[2,2,256,8] and result['chip_step_calls']==result['advanced_cycles']<=MAX_STEPS
    assert result['cold_write_count']==374 and not result['active_strobes'] and not result['driver_overlap']
    assert result['final_FIFOs']==[[0,0],[0,0]]
    assert not any(o['cycle']>=374 for o in result['host_calls']), 'runtime loading-host service'
    assert [(f['report'],f['byte'],f['start'],f['good_stop']) for f in result['UART_frames']]==list(FRAMES)
    source=timetable()[0]
    transitions=[list(e) for i,e in enumerate(source) if (i==0 or e[1]!=source[i-1][1]) and e[0]<result['advanced_cycles']]
    assert result['rx_edges']==transitions, 'external RX calendar'
    for f in result['UART_frames']:
        assert f['grant']<f['start'] and f['end']==f['start']+4167
        assert at(result['ready_edges'],f['start'])==1
    expected_actions=list(CALENDAR)+[("final",FINAL_DEADLINE)]
    assert [(e['stage'],e['clock']) for e in result['actions']]==expected_actions
    decoded=[];actual={stage:[] for stage in EXPECTED}
    for row in result['usb']:
        packet=raw_decode(row['raw']);decoded.append((row,packet))
        if row['direction']=='device':
            assert packet['packet'] is not None
            actual[row['stage']].append(packet['packet'])
        elif row['stage']=='A-bad-ACK':
            assert packet['bits'][8:16]==[0,1,0,0,1,0,1,1] and packet['bits'][16:]==[0]
    assert actual==EXPECTED, 'independent raw report/descriptor/CRC oracle'
    ACK_gaps=[]
    for report in 'ABCD':
        preceding=report+('-retry' if report!='D' else '-first')
        reply=next(p for row,p in decoded if row['direction']=='device' and row['stage']==preceding)
        ack=next(p for row,p in decoded if row['direction']=='host' and row['stage']==report+'-ACK')
        assert ack['packet']==b'\xd2' and ack['first']==dict(CALENDAR)[report+'-ACK']
        first_J_gap=ack['first']-reply['eop_j']
        full_end_gap=ack['first']-reply['eop_end']
        assert first_J_gap==64 and full_end_gap==32, 'ACK EOP coordinate contract'
        ACK_gaps.append(dict(report=report,DATA_first_EOP_J=reply['eop_j'],DATA_full_EOP_end=reply['eop_end'],
                             ACK_start=ack['first'],first_J_gap=first_J_gap,full_end_gap=full_end_gap))
    assert all(2<=x<=6.5 for x in result['turnarounds'])
    p0,p1=result['consumer_events'],result['producer_events'];l0,l1=result['usb_labels'],result['producer_labels']
    starts=_completed(p1,l1['wait_start']+1)
    detected=[e for e in starts if e['logical_RX']==0]
    assert [e['cycle'] for e in detected]==list(DETECT), 'fixed START detection chronology'
    sample_rows=_completed(p1,l1['sample']);stop_rows=_completed(p1,l1['stop_delay']+3)
    # Source has NOP at stop_delay+2 then STOP branch at+3.
    assert len(sample_rows)==17*8 and len(stop_rows)==17
    for i,(frame,D) in enumerate(zip(FRAMES,DETECT)):
        start=frame[2];good=frame[3]
        samples=sample_rows[8*i:8*i+8]
        assert [e['cycle'] for e in samples]==[D+625+417*k for k in range(8)]
        assert stop_rows[i]['cycle']==D+3961 and stop_rows[i]['logical_RX']==int(good)
        for slot,e in enumerate(samples,1):
            begin=start+(slot*1250+2)//3;end=start+((slot+1)*1250+2)//3
            assert begin<=e['source_cycle']<end and e['logical_RX']==(frame[1]>>(slot-1))&1
    # All old reports remain unchanged through service until the valid ACK edge.
    for e in p1:
        if e['flags']&128:assert e['regs'][1:3]==e['after_regs'][1:3], 'retained report overwritten'
    clears=[e for e in p0 if e['pc'] in (l0['get_word'],l0['get_word']+2) and e['completed']]
    captures=[e for e in p0 if e['pc'] in (l0['get_word']+1,l0['get_word']+3) and e['completed']]
    pubs=[e for e in p1 if not e['flags']&64 and e['after_flags']&64]
    pin_writes=[e for e in p1 if isa.fields(e['word'])[0]==isa.OP_OUT and (isa.fields(e['word'])[2]>>15)&15==isa.PINS]
    publication=[]
    for clear in clears:
        next_pub=next(e for e in pubs if e['cycle']>clear['cycle'])
        delay=next_pub['cycle']-clear['cycle'];assert delay<=66, 'publication deadline'
        capture=next(e for e in captures if e['cycle']>clear['cycle'])
        assert capture['cycle']==clear['cycle']+1 and capture['pin']==clear['pin']
        assert not any(clear['cycle']<e['cycle']<=capture['cycle'] for e in pin_writes), 'rewrite before consumer IN'
        publication.append(dict(clear=clear['cycle'],publication=next_pub['cycle'],delay=delay))
    critical=next(e for e in clears if e['cycle']==150592)
    same=next(e for e in p1 if e['cycle']==150592 and e['pc']==l1['service']+1)
    assert critical['flags']&64 and same['flags']&64 and not same['after_flags']&64
    assert any(e['cycle']==150658 and e['pc']==l1['publish'] for e in pubs), 'actual66-clock maximum absent'
    next_out=next(e for e in p0 if e['completed'] and e['pc']==l0['s_bit'] and e['cycle']>150592)
    last_out=next(e for e in p0 if e['completed'] and e['pc']==l0['s_bit'] and e['cycle']==150587)
    assert last_out['regs'][0]==17 and next_out['cycle']<=150663 and next_out['cycle']<150682
    packet=next(p for r,p in decoded if r['direction']=='device' and r['stage']=='A-first')
    overlap=[e for e in sample_rows[32:40] if packet['first']<=e['cycle']<packet['se0']]
    assert overlap, 'device DATA did not overlap UART IN1 samples'
    release=_completed(p0,l0['ack_advance']-1)
    assert [e['cycle'] for e in release]==[158241,203989,260541,281541], 'valid ACK releases only'
    for e in release:
        assert e['flags']&128 and not e['after_flags']&128
        assert [t for t,v in result['flag_edges'] if t==e['cycle']+1 and not v&128]
    # B partial persists through A ACK between second frame samples6/7.
    assert sample_rows[5*8+6]['cycle']==158130 < release[0]['cycle'] < sample_rows[5*8+7]['cycle']==158547
    assert [e['cycle'] for e in result['ack_falls']]==[e['cycle'] for e in release]
    during=next(e for e in result['ack_falls'] if e['cycle']==158241)
    assert during['flags']&128 and not during['after_flags']&128
    assert during['before']['pc']==during['after']['pc']==l1['bit_delay']+1
    assert during['before']['dcnt']==19 and during['after']['dcnt']==18
    assert during['before']['isr']==during['after']['isr']==0xa6720000, 'ACK lost pending prefix'
    # Rollback and FLAG instruction coincide, rather than EOP being charged first.
    rollback=[e for e in p1 if e['cycle'] in (203988,203989,203990)]
    assert [e['pc'] for e in rollback]==list(range(l1['good_stop']-4,l1['good_stop']-1))
    assert rollback[1]['cycle']==release[1]['cycle'] and rollback[-1]['after_isr']==0x34120000
    error_rises=[t for t,v in result['error_edges'] if v]
    assert error_rises==[203988] and not any(t>=203988 and not v for t,v in result['error_edges'])
    # Complete D remains pending while C is retried; promotion is fully guarded.
    assert at(result['ready_edges'],245016)==0
    assert not any(245016<=t<260594 and v for t,v in result['ready_edges'])
    guard=next(e for e in p1 if e['cycle']==260554)
    assert guard['pc']==l1['pending_full']+1 and not guard['flags']&128 and guard['isr']==0xf0debc9a
    copy1=next(e for e in p1 if e['cycle']==260556)
    copy2=next(e for e in p1 if e['cycle']==260559)
    assert copy1['pc']==l1['promote']+1 and copy1['after_regs'][1]==0xbc9a
    assert copy2['pc']==l1['promote']+4 and copy2['after_regs'][2]==0xf0de
    promoted=next(e for e in p1 if e['cycle']==260566)
    assert promoted['pc']==l1['promote']+11 and promoted['after_flags']&128
    clean=next(e for e in p1 if e['cycle']==260555)
    assert clean['pc']==l1['promote'] and not clean['after_flags']&64
    assert promoted['regs'][1:3]==[0xbc9a,0xf0de] and promoted['isr']==0
    assert promoted['regs'][3]==0x401 and promoted['flags']&64 and promoted['pin']==0x3d
    assert at(result['ready_edges'],260593)==0 and at(result['ready_edges'],260594)==1
    f7=[(t,int(bool(v&128))) for t,v in result['flag_edges']]
    f7=[row for i,row in enumerate(f7) if i==0 or row[1]!=f7[i-1][1]]
    assert [v for _,v in f7]==[0,1,0,1,0,1,0,1,0] and not result['final_flags']&128
    assert result['final_pin_out']&(1<<9)
    return dict(reports=[REPORTS[k].hex() for k in 'ABCD'],UART_frames=17,actual66=True,
        overlap_IN1_samples=[e['cycle'] for e in overlap],publication=publication,
        partial_release=158241,rollback_release=203989,promotion=260566,READY=260594,
        USB_packets=len(decoded),ACK_gaps=ACK_gaps,overflow_observed=len(result['overflow']))


def mutate_and_reject(result):
    """Three saved-only disclosed negatives, never additional DUT results."""
    outcomes=[]
    for name in ('changed_report_valid_CRC','removed_overlap_rollback_coverage','late_publication'):
        bad=copy.deepcopy(result)
        if name=='changed_report_valid_CRC':
            row=next(r for r in bad['usb'] if r['direction']=='device' and r['stage']=='B-first')
            altered=bytes([REPORTS['B'][0]^1])+REPORTS['B'][1:]
            states=usb.encode(data(altered));start=raw_decode(row['raw'])['first']
            row['raw']=[[start+i*BIT,{usb.J:2,usb.K:1,usb.SE0:0}[s]] for i,s in enumerate(states)]
            row['raw'].append([start+len(states)*BIT,2])
            assert raw_decode(row['raw'])['packet']==data(altered)
        elif name=='removed_overlap_rollback_coverage':
            bad['producer_events']=[e for e in bad['producer_events'] if e['cycle'] not in (150592,203989)]
        else:
            e=next(e for e in bad['producer_events'] if e['cycle']==150658)
            e['cycle']=150659  #67clocks, beyond source bound
        try:audit(bad)
        except (AssertionError,StopIteration,ValueError) as exc:outcomes.append(dict(mutation=name,rejected=True,reason=str(exc)))
        else:raise AssertionError('saved mutation accepted '+name)
    return outcomes


def test_overlap4_retained_and_next(tmp_path):
    out=Path(os.environ.get('WIRELINGO_EVIDENCE') or tmp_path)
    out.mkdir(parents=True,exist_ok=True)
    raw_path=out/RAW_NAME
    assert not raw_path.exists(), 'preserve prior raw artifact'
    programs=usb_uart_overlap4.assemble();rig=_Rig(programs);peer=None;error=None
    try:
        writes=usb_uart_overlap4.load_writes(programs)
        assert len(writes)==374
        rig.mh.apply(writes);rig.freeze_apis();peer=_Peer(rig)
        for stage,clock in CALENDAR:
            rig.absolute(stage,clock)
            if stage=='enum':
                for name,fn in (
                    ('enum-device',lambda:peer.get_descriptor(0,1,18)),
                    ('enum-address',lambda:peer.control_write(0,bytes.fromhex('0005050000000000'))),
                    ('enum-config',lambda:peer.get_descriptor(5,2,18)),
                    ('enum-configure',lambda:peer.control_write(5,bytes.fromhex('0009010000000000'))),
                    ('enum-get-config',lambda:peer.control_read(5,bytes.fromhex('8008000000000100')))):
                    rig.stage=name;fn()
                assert rig.mh.chip.cycle<140000, 'enumeration deadline'
            elif stage=='empty':
                try:peer.control_read(5,usb_uart_overlap4.vendor_request())
                except Stall:pass
                else:raise AssertionError('empty report resurrected')
            elif stage.endswith('-setup'):peer.setup(5,usb_uart_overlap4.vendor_request())
            elif stage.endswith(('-first','-retry')):peer.poll(stage[0])
            elif stage=='A-bad-ACK':peer.fixed_ack(True)
            elif stage.endswith('-ACK'):peer.fixed_ack()
            elif stage.endswith('-status'):peer.data_out(5,usb.PID_DATA1,b'')
        rig.absolute('final',FINAL_DEADLINE)
        audit(rig.snapshot(None,peer))
    except BaseException as exc:
        error=type(exc).__name__+': '+str(exc)
        raise
    finally:
        result=rig.snapshot(error,peer)
        raw_path.write_text(json.dumps(result,indent=2,default=_json_bytes)+'\n')
