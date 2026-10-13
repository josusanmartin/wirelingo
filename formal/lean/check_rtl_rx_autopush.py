#!/usr/bin/env python3
"""Finite real-chip IN8/autopush32 regression; no universal SV refinement.

Installed Lean4.28/Icarus/vvp/strace, no network/Lake/historical objects. All
build/runtime scratch stays under a fresh explicit output outside the checkout.
"""
import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time

sys.dont_write_bytecode = True
# Read-only shared command/syscall-log helpers, not the ownership experiment.
from check_rtl_fetch_ownership import Invocation, digest, put

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SOURCES = tuple('src/' + n + '.sv' for n in
    ('pe_chip','pe_core','pe_imem','pe_line','pe_fifo','pe_fifos','pe_cg')) + (
    'test/sram_model.v','formal/lean/lean-toolchain','formal/lean/audit_axioms.py',
    'formal/lean/check_rtl_fetch_ownership.py','formal/lean/check_rtl_rx_autopush.py',
    'formal/lean/Wirelingo/RxAutopush.lean','formal/lean/RxAutopushAudit.lean',
    'formal/lean/rtl_rx_autopush_tb.sv')
FIELDS = ('enable','restart','pins8','instr','pc','isr','isr_count','level',
    'res_n','res_real','rd_real','fifo_read','exec','complete','push','push_data',
    'isr_write','host_pop','head','post_pc','post_isr','post_count','post_level',
    'post_res_n','post_res_real','post_stalled','post_halted','post_fault',
    'post_sync1','post_sync2','post_cnt','post_dcnt','post_facc','inactive_core_push')
IN8 = (2 << 27) | (4 << 15) | 8
NEEDLE = "          if (rx_block) complete = 1'b0;"
MUTANT = """          if (rx_block) begin
            complete = 1'b0;
            isr_we = 1'b1;
            isr_nv = in_isr;
            isr_cnt_nv = in_cnt;
          end"""


def drive(rn=1,we=0,addr=65535,data=0,snap=0,commit=0,cancel=0):
    return [rn,we,addr,data,0,snap,commit,cancel]


def write(addr,data):
    return drive(we=1,addr=addr,data=data)


def trace(right):
    rows = [drive(rn=0),drive(rn=0)]
    rows += [write(i,IN8) for i in range(4)]
    rows += [write(1024+off,value) for off,value in
        ((0,256),(1,0),(2,0),(3,8+2*right),(4,0),(5,3<<16),(6,0))]
    rows += [write(addr,0) for addr in (0x80C,0x807,0x80A,0x804)]
    rows += [drive(),drive(),write(0x801,0x101),drive()]
    assert len(rows)==21
    rows += [drive() for _ in range(35)]  # eight words + three partial bytes
    rows += [drive() for _ in range(5)]   # input changes during full stalls
    rows += [drive(addr=1040,snap=1),drive(cancel=1),
             drive(addr=1040,snap=1),drive(addr=1040,snap=1),drive(cancel=1),
             drive(addr=1040,snap=1),drive(addr=1040,commit=1)]
    rows += [write(0x801,0),drive(),drive(),drive(),write(0x801,0x100),drive()]
    rows += [drive(addr=1040,snap=1),drive(cancel=1)]
    for _ in range(8): rows += [drive(addr=1040,snap=1),drive(addr=1040,commit=1)]
    rows += [drive(),drive(addr=1040,snap=1),drive(cancel=1),drive()]
    assert len(rows)==96
    for j,row in enumerate(rows): row[4]=(37*j+11)%256
    return {'id':'right' if right else 'left','right':bool(right),'drives':rows}


def corpus():
    return [trace(0),trace(1)]


def pack_bytes(values,right):
    return int.from_bytes(bytes(values),'little' if right else 'big')


def expected_words(t):
    raw=lambda j:t['drives'][j][4]
    words=[pack_bytes([raw(j) for j in range(19+4*k,23+4*k)],t['right']) for k in range(8)]
    return words+[pack_bytes([raw(51),raw(52),raw(53),raw(66)],t['right'])]


def declaration():
    return {'traces':corpus(),'instruction_hex':f'{IN8:08x}',
        'instruction_bytes_le':IN8.to_bytes(4,'little').hex(),
        'program':'core0 bank0 slots0..3: IN PINS8, wrap0..3; core1 disabled',
        'domain':'div1/frac0/delay0/autopush32; sync bypass0/two flops; no LINE/CRC/side/FJOIN/host PC or register writes/FIFO clear',
        'positive_clocks':[96,96],'mutant_trace':'left','mutant_prefix_clocks':57,
        'aggregate_RTL_clocks':249,'reset_edges':6,'expected_first_mutant_clock':56,
        'full_pop_clock':67,'resume_push_clock':68,'disabled_restart_clock':73,
        'drained_words':{t['id']:expected_words(t) for t in corpus()},
        'sampling':'pre-edge after4ns settle; post-edge after1ns NBA/latch settle',
        'host_boundary':'parallel pe_chip bus only; snap data captured, commits consume reservations, cancel drops reservations; no paid serial/wire-capture claim'}


def source_pins():
    return {f:digest(ROOT/f) for f in SOURCES}


def installed(name,path):
    selected=path or shutil.which(name)
    if not selected: raise ValueError(f'{name} unavailable; supply installed --{name}')
    try: p=Path(selected).resolve(strict=True)
    except OSError as e: raise ValueError(f'{name} unavailable; supply installed --{name}') from e
    if not p.is_file() or not os.access(p,os.X_OK): raise ValueError(f'{name} is not executable')
    if name=='lean' and p.name=='elan': raise ValueError('supply actual installed Lean4.28 bin/lean, not an elan download shim')
    return p


def validate(a):
    a.output=a.output.absolute()
    if a.output.exists() or a.output.is_symlink() or not a.output.parent.is_dir():
        raise ValueError('fresh nonexistent output required, parent must exist')
    a.output=a.output.parent.resolve()/a.output.name
    for name in ('lean','iverilog','vvp','strace'): setattr(a,name,installed(name,getattr(a,name)))
    a.lean_lib=(a.lean_lib or a.lean.parent.parent/'lib/lean').resolve(strict=True)
    if not (a.lean_lib/'Std.olean').is_file(): raise ValueError('Lean standard library missing; supply --lean-lib')
    if any(a.output.is_relative_to(p) for p in (ROOT,a.lean_lib)): raise ValueError('output overlaps source/library')
    a.sources=source_pins()
    if a.corpus=='uart-in1':
        if a.phase=='all': raise ValueError('UART-IN1 requires separately reviewed prepare/native phases; --phase all disabled')
        a.sources.update({f:digest(ROOT/f) for f in UART_INPUTS})
    if a.phase=='native':
        if not a.prepared or not a.prepared_sha256: raise ValueError('native requires --prepared and its --prepared-sha256')
        a.prepared=a.prepared.resolve(strict=True)
        if digest(a.prepared/'prepared-receipt.json')!=a.prepared_sha256: raise ValueError('prepared receipt identity mismatch')
        receipt=json.loads((a.prepared/'prepared-receipt.json').read_text())
        if receipt['sources']!=a.sources: raise ValueError('stale prepared source')
        if receipt['declaration']!=selected_declaration(a): raise ValueError('stale prepared corpus')
        for f,h in receipt['artifacts'].items():
            if digest(a.prepared/f)!=h: raise ValueError(f'stale prepared artifact {f}')
        for n,h in receipt['tools'].items():
            if digest(getattr(a,n))!=h: raise ValueError(f'stale prepared tool {n}')
        if receipt['lean_lib']!=str(a.lean_lib): raise ValueError('stale Lean library path')
        for f,h in receipt['library_inputs'].items():
            if digest(Path(f))!=h: raise ValueError(f'stale Lean library {f}')
        if a.output.is_relative_to(a.prepared): raise ValueError('output overlaps preparation')
    return a


def prepare(inv):
    a=inv.a;original=ROOT/'src/pe_core.sv';text=original.read_text()
    assert text.count(NEEDLE)==1
    mutant=a.output/'NONPRODUCT-pe_core-wrong-full-shift.sv'
    mutant.write_text(text.replace(NEEDLE,MUTANT))
    put(a.output/'mutant-delta.json',{'nonproduct':True,'needle_count':1,'before':NEEDLE,'after':MUTANT,
        'original_sha256':digest(original),'mutant_sha256':digest(mutant)})
    common=[ROOT/f'src/{n}.sv' for n in ('pe_chip','pe_imem','pe_line','pe_fifo','pe_fifos','pe_cg')]
    for name,source in (('original',original),('mutant',mutant)):
        inv.run([a.iverilog,'-g2012','-s','rtl_rx_autopush_tb','-o',a.output/(name+'.vvp'),
            HERE/'rtl_rx_autopush_tb.sv',source,*common,ROOT/'test/sram_model.v'],'elaborate-'+name)
    version=inv.run([a.lean,'--version'],'lean-version')
    if not re.search(r'Lean \(version 4\.28\.0(?:,|\))',version): raise RuntimeError('installed Lean4.28.0 required')
    inv.run([a.iverilog,'-V'],'iverilog-version');inv.run([a.vvp,'-V'],'vvp-version')
    build=a.output/'lean-build';(build/'Wirelingo').mkdir(parents=True)
    env={'LEAN_PATH':str(build)+':'+str(a.lean_lib)}
    inv.run([a.lean,'-DwarningAsError=true','-o',build/'Wirelingo/RxAutopush.olean',
        HERE/'Wirelingo/RxAutopush.lean'],'rx-build',env)
    audit=inv.run([a.lean,'-DwarningAsError=true',HERE/'RxAutopushAudit.lean'],'rx-audit',env)
    spec=importlib.util.spec_from_file_location('policy',HERE/'audit_axioms.py');policy=importlib.util.module_from_spec(spec);spec.loader.exec_module(policy)
    names={'Wirelingo.'+n for n in re.findall(r'^theorem (\w+)',(HERE/'Wirelingo/RxAutopush.lean').read_text(),re.M)}
    found=set(re.findall(r"'([^']+)' (?:depends on axioms|does not depend on any axioms)",audit))
    assert names==found and len(names)==13
    for deps in re.findall(r'depends on axioms: \[([^\]]*)\]',audit):
        assert (set(deps.split(', ')) if deps else set())<=policy.ALLOWED
    lines=[]
    for t in corpus():
        lines.append('0');lines += ['1 '+' '.join(map(str,d)) for d in t['drives']]
    inp=a.output/'lean-input.txt';inp.write_text('\n'.join(lines)+'\n')
    raw=inv.run([a.lean,'-DwarningAsError=true','--run',HERE/'RxAutopushAudit.lean'],'closed-predictions',env,inp)
    flat=[list(map(int,line.split()[1:])) for line in raw.splitlines() if line.startswith('PRED ')]
    assert len(flat)==192 and all(len(r)==len(FIELDS) for r in flat)
    predictions={t['id']:flat[k*96:(k+1)*96] for k,t in enumerate(corpus())}
    for t in corpus():
        rs=predictions[t['id']]
        pushes=[r[15] for r in rs if r[14]]
        assert pushes==expected_words(t),'closed packing oracle mismatch'
        assert [j for j,r in enumerate(rs) if r[14]]==[24,28,32,36,40,44,48,52,68]
        assert all(r[14]==r[16]==r[13]==0 and r[6]==24 and r[7]==8 and r[4]==3 and r[19]==3 and r[20]==r[5] and r[21]==24 for r in rs[56:68])
        assert rs[67][17]==1 and rs[67][22]==7 and rs[68][14]==1 and rs[68][22]==8
        assert rs[70][0]==0 and rs[70][20]==rs[72][20] and rs[70][21]==rs[72][21]==8
        assert rs[73][0]==0 and rs[73][1]==1 and rs[73][20:23]==[0,0,8]
        assert [r[18] for r in rs if r[17]]==expected_words(t)
        assert rs[-1][22]==0
    put(a.output/'predictions.json',predictions)
    artifacts=['original.vvp','mutant.vvp','predictions.json','predeclared-stimuli.json',
        'lean-build/Wirelingo/RxAutopush.olean','rx-audit.stdout.log','mutant-delta.json']
    receipt={'sources':a.sources,'declaration':declaration(),'tools':{n:digest(getattr(a,n)) for n in ('lean','iverilog','vvp','strace')},
        'lean_lib':str(a.lean_lib),'library_inputs':{str(f):digest(f) for f in sorted(inv.dependencies) if f.is_relative_to(a.lean_lib)},
        'artifacts':{f:digest(a.output/f) for f in artifacts}}
    put(a.output/'prepared-receipt.json',receipt)
    return {'result':'pass','phase':'prepare','manual_lemmas':5,'closed_rows':192,
        'prepared_receipt_sha256':digest(a.output/'prepared-receipt.json')}


def text_input(t):
    return str(len(t['drives']))+'\n'+'\n'.join(' '.join(map(str,d)) for d in t['drives'])+'\n'


def parse(raw):
    rows=[]
    for line in raw.splitlines():
        if not line.startswith('ROW '): continue
        ts=line.split()[1:];row=[int(ts[0])]
        for k,token in enumerate(ts[1:]):
            row.append(4294967296 if re.search('[xz]',token,re.I) else int(token,16 if k==3 else 10))
        assert len(row)==len(FIELDS)+1,(len(row),line)
        rows.append(row)
    return rows


def mismatch(t,pred,rows):
    assert len(pred)==len(rows)
    for j,(p,r) in enumerate(zip(pred,rows,strict=True)):
        assert r[0]==j
        diff=[FIELDS[k] for k,(x,y) in enumerate(zip(p,r[1:],strict=True)) if x!=y]
        if diff:return {'trace':t['id'],'clock':j,'differences':diff,'fields':FIELDS,
            'predicted':p,'RTL':r[1:],'drive_prefix':t['drives'][:j+1]}
    return None


def drained_words(t,rows):
    reservations=[];drained=[]
    for d,r in zip(t['drives'],rows,strict=True):
        p=r[1:]
        if not d[0]: reservations=[]
        elif d[7]: reservations=[]
        elif d[2]==1040 and d[6] and reservations:
            valid,word=reservations.pop(0)
            if valid: drained.append(word)
        elif d[2]==1040 and d[5] and len(reservations)<2:
            reservations.append((bool(p[10]),p[11]))
    return drained


def native(inv):
    a=inv.a;pred=json.loads((a.prepared/'predictions.json').read_text());allrows={}
    def simulate(t,binary,label):
        inp=a.output/(label+'.input.txt');inp.write_text(text_input(t))
        raw=inv.run([a.vvp,a.prepared/binary,'+INPUT='+str(inp)],label)
        rows=parse(raw);assert len(rows)==len(t['drives']);return rows
    for t in corpus():
        rows=simulate(t,'original.vvp',t['id']);allrows[t['id']]=rows
        diff=mismatch(t,pred[t['id']],rows)
        if diff:
            put(a.output/'first-mismatch.json',diff)
            raise RuntimeError('real RTL/prediction disagreement; freeze, no oracle weakening')
        assert drained_words(t,rows)==expected_words(t)
    put(a.output/'positive-rows.json',allrows)
    t=copy.deepcopy(corpus()[0]);t['drives']=t['drives'][:57]
    rows=simulate(t,'mutant.vvp','wrong-full-shift-mutant')
    diff=mismatch(t,pred['left'][:57],rows)
    assert diff and diff['clock']==56
    assert {'post_isr','post_count'}<=set(diff['differences'])
    put(a.output/'actual-mutant-counterexample.json',diff)
    assert inv.clocks==249 and inv.resets==6
    put(a.output/'drained-words.json',{t['id']:drained_words(t,allrows[t['id']]) for t in corpus()})
    return {'result':'pass','phase':'native','positive_streams':2,'positive_rows':192,
        'actual_mutant_first_mismatch':56,'drained_words_per_setting':9,
        'scope':'finite IN8/autopush32 and parallel-bus/FIFO agreement; no SV refinement or stalled wire-capture claim'}


def phase(a):
    a.output.mkdir(exist_ok=False);put(a.output/'predeclared-stimuli.json',selected_declaration(a))
    put(a.output/'input-receipt.json',{'sources':a.sources,'paths':{n:str(getattr(a,n)) for n in ('output','lean','lean_lib','iverilog','vvp','strace')},
        'prepared':str(a.prepared) if a.prepared else None,'prepared_sha256':a.prepared_sha256})
    inv=UartInvocation(a) if a.corpus=='uart-in1' else Invocation(a)
    try:
        if a.corpus=='uart-in1': result=prepare_uart(inv) if a.phase=='prepare' else native_uart(inv)
        else: result=prepare(inv) if a.phase=='prepare' else native(inv)
    except Exception as e: result={'result':'fail','phase':a.phase,'error':str(e)}
    finally:
        inv.dependencies.update([Path(sys.executable).resolve(),a.strace,*[ROOT/f for f in a.sources]])
        put(a.output/'dependency-hashes.json',{str(f):digest(f) for f in sorted(inv.dependencies)})
        result.update(commands=inv.commands,elapsed_seconds=time.monotonic()-inv.start,
            RTL_clocks=inv.clocks,reset_edges=inv.resets,model_steps=0,state_deposits=0)
        put(a.output/'ledger.json',result)
    return result



# Explicit new domain; legacy corpus(), rxObserve and default CLI stay IN8.
UART_INPUTS = ('firmware/uart_rx_capture.pasm','tools/pe/asm.py','tools/pe/isa.py',
    'tools/pe/host.py','tools/pe/profile.py','tools/pe/uartcapture.py','tools/pe/sim.py',
    'tools/pe/__init__.py')
UART_FW_SHA256 = '62ec2bac87998eb435ed0d6bfc947472d933c39444d9fd47425c296995e6b62e'
UART_FIELDS = FIELDS + ('pre_dcnt','pre_halted','pre_fault','pre_cnt','pre_r0',
    'pre_r1','pre_r2','pre_flags','bus_rdata_all','post_r0','post_r1','post_r2',
    'post_flags','post_instr')
UART_MUTANT_NEEDLE = '          core_rxdata = isr;'
UART_MUTANT_REPLACE = "          core_rxdata = isr >> (6'd32 - isr_cnt);"


class UartInvocation(Invocation):
    """Reuse installed command/syscall helpers; separately count new UROWs.

    The shared legacy helper's ROW cap remains untouched. A new stream uses
    UROW and this explicit 32768 aggregate cap. External slot supervisor must
    enforce 120s/2GiB and process-group TERM/KILL grace5s on a native release.
    """
    def run(self,argv,name,env=None,stdin=None):
        previous=self.clocks;self.clocks=0
        try:
            raw=super().run(argv,name,env,stdin)
        finally:
            log=self.directory/(name+'.stdout.log')
            new_rows=len(re.findall(r'^UROW ',log.read_text(),re.M)) if log.exists() else 0
            self.clocks += previous+new_rows
        if self.clocks>32768:raise RuntimeError('declared UART aggregate clock cap')
        return raw


def uart_program():
    if digest(ROOT/'firmware/uart_rx_capture.pasm')!=UART_FW_SHA256:
        raise ValueError('pinned unchanged UART capture firmware mismatch')
    sys.path.insert(0,str(ROOT/'tools'))
    # Pure assembler/planners. sim is imported for register constants only:
    # no Chip/Core instance or model step is constructed/called.
    from pe import asm,host,uartcapture
    prog=asm.assemble((ROOT/'firmware/uart_rx_capture.pasm').read_text(),{'RX':0})
    assert len(prog.words)==19 and prog.labels['bitloop']==3 and prog.labels['finish']==16
    return prog,host,uartcapture


def represented(bits):
    """Independent positional representation of a high-aligned bit prefix."""
    return sum(bit << (32-len(bits)+j) for j,bit in enumerate(bits))


def uart_words(data,partial=True):
    full=len(data)//4
    words=[int.from_bytes(bytes(data[4*j:4*j+4]),'little') for j in range(full)]
    tail=data[4*full:]
    if tail and partial:words.append(int.from_bytes(bytes(tail),'little') << (8*(4-len(tail))))
    return words


def action(kind,clock,word,count,pc,bit,full,next_pc,after,after_count,push):
    # PROJ inputs are fixed manual boundary fixtures, not observed DUT gates.
    args=[kind,word,count,pc,bit,int(full),next_pc,0,1,0,1,1,0,0,0]
    expected=[word,count,pc,after,after_count,next_pc if not full else pc,
        int(push), (after if push and kind==0 else word),int(not full),int(not full)]
    return {'clock':clock,'args':args,'expected':expected}


def in_action(clock,prefix,bit,pc,next_pc,full=False):
    before=represented(prefix)
    shifted=represented(prefix+[bit]);ready=len(prefix)==31
    after=before if ready and full else (0 if ready else shifted)
    count=len(prefix) if ready and full else (0 if ready else len(prefix)+1)
    result=action(0,clock,before,len(prefix),pc,bit,full,next_pc,after,count,ready and not full)
    result['expected'][5]=pc if ready and full else next_pc
    result['expected'][8]=result['expected'][9]=int(not (ready and full))
    result['expected'][7]=shifted if ready and not full else before
    return result


def uart_setup(words,prog,length,host,planner):
    rows=[drive(rn=0),drive(rn=0)]
    if prog is not None:
        load=host.load_program(0,prog,in_pins=0,clkdiv=256)
    else:
        load=list(enumerate(words))+[(1024+off,value) for off,value in
            ((0,256),(1,0),(2,0),(3,10),(4,0),(5,3<<16))]
    rows += [write(addr,value) for addr,value in load]
    rows += [write(addr,0) for addr in (0x80C,0x807,0x80A,0x804)]
    precondition_clocks=[len(rows),len(rows)+1]
    rows += [drive(addr=1033),drive(addr=2049)]
    arm=planner.capture_plan(length,enabled_before=0)
    assert arm==[(2049,0),(1035,length),(1036,length%4),(2051,1),(2049,257)]
    arm_clocks=list(range(len(rows),len(rows)+len(arm)))
    rows += [write(addr,value) for addr,value in arm]
    return rows,[list(pair) for pair in arm],arm_clocks,precondition_clocks


def uart_trace(length,offered=None,bad=False):
    prog,host,planner=uart_program();offered=length if offered is None else offered
    data=([0x39,0xd3,0x81,0x5e]+[(17+53*j)%256 for j in range(28)])[:offered]
    rows,arm,arm_clocks,precond=uart_setup(prog.words,prog,length,host,planner)
    origin=96;deadline=origin+80*offered+64
    rows += [drive() for _ in range(deadline-len(rows))]
    complete=offered==length
    expected_words=uart_words(data,partial=complete)
    status_clock=len(rows);rows += [drive(addr=1033),drive(addr=2051)]
    rows += [drive(addr=1040,snap=1),drive(cancel=1)]
    for _ in expected_words:rows += [drive(addr=1040,snap=1),drive(addr=1040,commit=1)]
    rows += [drive(),drive()]
    frames=[]
    for i,value in enumerate(data):
        start=origin+80*i
        levels=[0]+[(value>>k)&1 for k in range(8)]+[0 if bad and i==offered-1 else 1]
        frames.append({'start':start,'byte':value,'levels':levels,'bit_clocks':8})
    for j,row in enumerate(rows):
        row[4]=1
        for frame in frames:
            if frame['start']<=j<frame['start']+80:
                row[4]=frame['levels'][(j-frame['start'])//8];break
    actions=[];prefix=[];publications=[];stops=[]
    for i,frame in enumerate(frames):
        for k in range(8):
            clock=frame['start']+14+8*k;bit=(frame['byte']>>k)&1
            assert rows[clock-2][4]==bit
            a=in_action(clock,prefix,bit,3,4);actions.append(a)
            if len(prefix)==31:
                publications.append({'clock':clock,'word':represented(prefix+[bit]),'kind':'autopush'})
                prefix=[]
            else:prefix=prefix+[bit]
        stops.append({'clock':frame['start']+78,'pc':14 if complete and i==offered-1 else 10,
            'level':frame['levels'][-1],'frame':i})
    halt_clock=None;flag_clock=None
    if complete:
        final=frames[-1]['start'];extra=int(bad)
        if bad:flag_clock=final+79
        if length%4:
            pc=17;clock=final+80+extra;word=represented(prefix)
            a=action(1,clock,word,len(prefix),pc,1,False,18,0,0,True)
            actions.append(a);publications.append({'clock':clock,'word':word,'kind':'explicit-push'})
            prefix=[]
        halt_clock=final+(81 if length%4 else 80)+extra
    ident='capture-'+str(length)+('-bad-stop' if bad else '-under4' if not complete else '')
    return {'id':ident,'kind':'capture','drives':rows,'program':prog.words,'program_labels':prog.labels,
        'length':length,'offered':offered,'data':data,'frames':frames,'arm_writes':arm,
        'arm_clocks':arm_clocks,'precondition_clocks':precond,'image_check_from':arm_clocks[0],
        'actions':actions,'publications':publications,'stop_checks':stops,'halt_clock':halt_clock,
        'flag_clock':flag_clock,'status_clock':status_clock,'expected_words':expected_words,
        'final_r1':length-offered,'final_r2':length%4,'complete':complete}


def uart_stall_trace():
    _,host,planner=uart_program();words=[(2<<27)|(4<<15)|1]*4
    rows,arm,arm_clocks,precond=uart_setup(words,None,1,host,planner)
    rows.append(drive())  # pending restart/enable application
    first=len(rows)
    rows += [drive() for _ in range(287)]
    blocked=len(rows);rows += [drive() for _ in range(5)]
    rows += [drive(addr=1040,snap=1),drive(cancel=1),drive(addr=1040,snap=1),
        drive(addr=1040,snap=1),drive(cancel=1),drive(addr=1040,snap=1),drive(addr=1040,commit=1)]
    pop=len(rows)-1;resume=len(rows)
    rows += [write(2049,0),drive(),drive(),drive(),write(2049,256),drive()]
    restart=len(rows)-1
    rows += [drive(addr=1040,snap=1),drive(cancel=1)]
    for _ in range(8):rows += [drive(addr=1040,snap=1),drive(addr=1040,commit=1)]
    rows += [drive(),drive(addr=1040,snap=1),drive(cancel=1),drive()]
    for j,row in enumerate(rows):row[4]=(j+j//5)%2
    rows[blocked-2][4]=0;rows[resume-2][4]=1
    actions=[];prefix=[];words_out=[]
    for j in range(first,blocked):
        bit=rows[j-2][4];pc=(j-first)%4;a=in_action(j,prefix,bit,pc,(pc+1)%4,full=(j-first>=256));actions.append(a)
        if len(prefix)==31:words_out.append(represented(prefix+[bit]));prefix=[]
        else:prefix.append(bit)
    assert len(prefix)==31 and len(words_out)==8
    for j in range(blocked,pop+1):actions.append(in_action(j,prefix,rows[j-2][4],3,0,True))
    a=in_action(resume,prefix,rows[resume-2][4],3,0);actions.append(a)
    words_out.append(represented(prefix+[rows[resume-2][4]]))
    actions.append(in_action(resume+1,[],rows[resume-1][4],0,1))
    assert rows[blocked-2][4]!=rows[resume-2][4]
    return {'id':'reduced-in1-full','kind':'reduced','drives':rows,'program':words,'actions':actions,
        'arm_writes':arm,'arm_clocks':arm_clocks,'precondition_clocks':precond,
        'image_check_from':arm_clocks[0],'expected_words':words_out,'first':first,
        'blocked':blocked,'full_pop':pop,'resume':resume,'disabled_restart':restart,
        'scope':'reduced IN1 current-bit/full-pop witness, NOT a UART lossless wire case'}


def uart_corpus():
    return [uart_trace(n) for n in (1,2,3,4,5,31,32)]+[
        uart_trace(3,bad=True),uart_trace(5,offered=4),uart_stall_trace()]


def uart_declaration():
    traces=uart_corpus();mutant=next(t for t in traces if t['id']=='capture-3')
    total=sum(len(t['drives']) for t in traces)+len(mutant['drives'])
    assert total<=32768
    return {'corpus':'uart-in1','evidence_level':'PREPARATION; future finite waveform plus projected IN/PUSH checks, not closed whole-core prediction',
        'traces':traces,'mutant_trace':mutant['id'],'expected_mutant_first_push_clock':mutant['publications'][-1]['clock'],
        'mutant_expected_word':mutant['expected_words'][-1],'mutant_wrong_word':mutant['expected_words'][-1]>>8,
        'aggregate_RTL_clocks':total,'reset_edges':2*(len(traces)+1),
        'stream_clocks':{t['id']:len(t['drives']) for t in traces},'mutant_clocks':len(mutant['drives']),
        'RX_pin':0,'baud':'exact 8 clocks/bit, no tolerance claim','fifo_depth':8,'ncores':2,'nbanks':2,'bank_words':256,
        'domain':'normally loaded immutable19-word capture, div1/right/autopush32, two-flop RX, core0 only; reduced4-IN image separately labeled',
        'excluded':'LINE/CRC/side/FJOIN; active IMEM/config/PC/register writes; serial-host timing, concurrency, pad/physical/SV refinement, lossless stalled wire capture',
        'native_authorization':'separate reviewed 120s/2GiB/5s process-group-kill slot required; this packet grants none'}


def selected_declaration(a):
    return uart_declaration() if a.corpus=='uart-in1' else declaration()


def audit_manual(inv,env):
    raw=inv.run([inv.a.lean,'--threads=1','-DwarningAsError=true',HERE/'RxAutopushAudit.lean'],'uart-audit',env)
    spec=importlib.util.spec_from_file_location('policy',HERE/'audit_axioms.py');policy=importlib.util.module_from_spec(spec);spec.loader.exec_module(policy)
    names={'Wirelingo.'+n for n in re.findall(r'^theorem (\w+)',(HERE/'Wirelingo/RxAutopush.lean').read_text(),re.M)}
    found=set(re.findall(r"'([^']+)' (?:depends on axioms|does not depend on any axioms)",raw))
    assert names==found and len(names)==13
    for deps in re.findall(r'depends on axioms: \[([^\]]*)\]',raw):
        assert (set(deps.split(', ')) if deps else set())<=policy.ALLOWED
    return raw


def prepare_uart(inv):
    a=inv.a;decl=uart_declaration();text=(ROOT/'src/pe_core.sv').read_text()
    assert text.count(UART_MUTANT_NEEDLE)==1
    mutant=a.output/'NONPRODUCT-pe_core-partial-right-aligned.sv'
    mutant.write_text(text.replace(UART_MUTANT_NEEDLE,UART_MUTANT_REPLACE))
    put(a.output/'mutant-delta.json',{'nonproduct':True,'before':UART_MUTANT_NEEDLE,'after':UART_MUTANT_REPLACE,
        'occurrences':1,'original_sha256':digest(ROOT/'src/pe_core.sv'),'mutant_sha256':digest(mutant)})
    common=[ROOT/f'src/{n}.sv' for n in ('pe_chip','pe_imem','pe_line','pe_fifo','pe_fifos','pe_cg')]
    for name,source in (('original',ROOT/'src/pe_core.sv'),('mutant',mutant)):
        inv.run([a.iverilog,'-g2012','-s','rtl_rx_autopush_tb','-o',a.output/(name+'.vvp'),
            HERE/'rtl_rx_autopush_tb.sv',source,*common,ROOT/'test/sram_model.v'],'uart-elaborate-'+name)
    version=inv.run([a.lean,'--version'],'lean-version')
    if not re.search(r'Lean \(version 4\.28\.0(?:,|\))',version):raise RuntimeError('Lean4.28.0 required')
    inv.run([a.iverilog,'-V'],'iverilog-version')
    # Deliberately NO vvp call, including -V, in this new prepare-only mode.
    build=a.output/'lean-build';(build/'Wirelingo').mkdir(parents=True)
    env={'LEAN_PATH':str(build)+':'+str(a.lean_lib)}
    inv.run([a.lean,'--threads=1','-DwarningAsError=true','-o',build/'Wirelingo/RxAutopush.olean',
        HERE/'Wirelingo/RxAutopush.lean'],'uart-build',env)
    audit_manual(inv,env)
    lines=[];actions=[]
    for t in decl['traces']:
        for step in t['actions']:lines.append('2 '+' '.join(map(str,step['args'])));actions.append((t['id'],step))
    inp=a.output/'uart-projection-input.txt';inp.write_text('\n'.join(lines)+'\n')
    raw=inv.run([a.lean,'--threads=1','-DwarningAsError=true','--run',HERE/'RxAutopushAudit.lean'],'uart-projections',env,inp)
    rows=[list(map(int,line.split()[1:])) for line in raw.splitlines() if line.startswith('PROJ ')]
    assert len(rows)==len(actions) and all(len(r)==10 for r in rows)
    projections={t['id']:[] for t in decl['traces']}
    for (ident,step),row in zip(actions,rows,strict=True):
        assert row==step['expected'],(ident,step['clock'],row,step['expected'])
        projections[ident].append({'clock':step['clock'],'row':row})
    put(a.output/'uart-projections.json',projections)
    put(a.output/'firmware-image.json',{'source_sha256':UART_FW_SHA256,'defines':{'RX':0},
        'words_hex':[f'{w:08x}' for w in decl['traces'][0]['program']],
        'bytes_le':' '.join(w.to_bytes(4,'little').hex() for w in decl['traces'][0]['program']),
        'labels':decl['traces'][0]['program_labels'],'arm_writes_by_case':{t['id']:t['arm_writes'] for t in decl['traces']}})
    for t in decl['traces']:(a.output/(t['id']+'.input.txt')).write_text(text_input(t))
    mutant_t=next(t for t in decl['traces'] if t['id']==decl['mutant_trace'])
    (a.output/'partial-right-aligned-mutant.input.txt').write_text(text_input(mutant_t))
    artifacts=['original.vvp','mutant.vvp','predeclared-stimuli.json','uart-projections.json','firmware-image.json',
        'lean-build/Wirelingo/RxAutopush.olean','uart-audit.stdout.log','mutant-delta.json']+[
        t['id']+'.input.txt' for t in decl['traces']]+['partial-right-aligned-mutant.input.txt']
    receipt={'sources':a.sources,'declaration':decl,'tools':{n:digest(getattr(a,n)) for n in ('lean','iverilog','vvp','strace')},
        'lean_lib':str(a.lean_lib),'library_inputs':{str(f):digest(f) for f in sorted(inv.dependencies) if f.is_relative_to(a.lean_lib)},
        'artifacts':{f:digest(a.output/f) for f in artifacts},'authorization':'prepared only; separate exact native release required'}
    put(a.output/'prepared-receipt.json',receipt)
    assert inv.clocks==0
    return {'result':'pass','phase':'prepare','corpus':'uart-in1','manual_lemmas':13,'new_manual_statements':8,
        'projected_action_rows':len(rows),'future_RTL_clocks':decl['aggregate_RTL_clocks'],
        'vvp_invocations':0,'native_authorized':False,'prepared_receipt_sha256':digest(a.output/'prepared-receipt.json')}


def parse_uart(raw):
    rows=[]
    for line in raw.splitlines():
        if not line.startswith('UROW '):continue
        ts=line.split()[1:];row=[int(ts[0])]
        for k,token in enumerate(ts[1:]):
            row.append(4294967296 if re.search('[xz]',token,re.I) else int(token,16 if k in (3,47) else 10))
        assert len(row)==len(UART_FIELDS)+1,(len(row),line)
        rows.append(row)
    return rows


def uart_projection_row(p):
    return [p[5],p[6],p[4],p[20],p[21],p[19],p[14],p[15],p[16],p[13]]


def check_uart_trace(t,rows,projections,mutant=False):
    assert len(rows)==len(t['drives'])
    projected={r['clock']:r['row'] for r in projections};observed_actions=[];mismatches=[]
    for j,record in enumerate(rows):
        assert record[0]==j;p=record[1:]
        assert p[27]==p[33]==0 and p[30]==p[32]==p[37]==0
        if j>=t['image_check_from']:
            assert p[4]<len(t['program']) and p[3]==t['program'][p[4]],('fetch',t['id'],j)
            assert p[19]<len(t['program']) and p[47]==t['program'][p[19]]
        if p[12] and p[3]>>27 in (2,4):
            observed_actions.append(j);assert j in projected,('unexpected IN/PUSH',t['id'],j)
            assert p[34]==p[35]==p[36]==0
            got=uart_projection_row(p);want=projected[j]
            if got!=want:mismatches.append({'trace':t['id'],'clock':j,'observed':got,'expected':want,
                'kind':'projected IN/PUSH (not whole-core Lean refinement)','input_prefix':t['drives'][:j+1]})
        elif j in projected:raise AssertionError(('missing IN/PUSH',t['id'],j))
        elif j>=t['image_check_from'] and not p[1]:
            assert p[5:7]==p[20:22],('unexpected ISR writer',t['id'],j)
        if j in t['precondition_clocks']:
            assert p[0]==0 and p[7]==0 and p[42]==0
        if t['kind']=='capture':
            if j>=t['image_check_from']:
                assert p[46]==(1 if t['flag_clock'] is not None and j>=t['flag_clock'] else 0)
                assert p[26]==(1 if t['halt_clock'] is not None and j>=t['halt_clock'] else 0)
            for stop in t['stop_checks']:
                if j==stop['clock']:assert p[12]==1 and p[4]==stop['pc'] and (p[2]&1)==stop['level']
    assert observed_actions==sorted(projected)
    actual_words=drained_words(t,rows)
    if not mutant:assert actual_words==t['expected_words'],('drain representation',t['id'])
    if t['kind']=='capture':
        p=rows[t['status_clock']][1:]
        assert p[7]==len(t['expected_words']) and p[39]==t['final_r1'] and p[40]==t['final_r2']
        assert bool(p[35])==t['complete']
        assert ((p[42]>>4)&15)==len(t['expected_words']) and bool(p[42]&(1<<13))==t['complete']
        assert not p[42]&(1<<14)
    else:
        full=t['full_pop'];resume=t['resume'];p=rows[full][1:];q=rows[resume][1:]
        assert p[7]==8 and p[6]==31 and p[14]==0 and p[22]==7
        assert q[7]==7 and q[14]==1 and q[21]==0 and q[22]==8
        assert rows[t['blocked']][3]!=rows[resume][3], 'changing sampled stall bit required'
        r=rows[t['disabled_restart']][1:];assert r[0]==0 and r[1]==1 and r[20:23]==[0,0,8]
    return mismatches,actual_words


def native_uart(inv):
    """Future-only execution path: explicit phase and reviewed native slot."""
    a=inv.a;decl=uart_declaration();projections=json.loads((a.prepared/'uart-projections.json').read_text())
    allrows={}
    def simulate(t,binary,name):
        inp=a.output/(name+'.input.txt');inp.write_text(text_input(t))
        raw=inv.run([a.vvp,a.prepared/binary,'+INPUT='+str(inp),'+UART_IN1=1','+LIMIT='+str(len(t['drives']))],name)
        return parse_uart(raw)
    for t in decl['traces']:
        rows=simulate(t,'original.vvp',t['id']);allrows[t['id']]=rows
        diffs,_=check_uart_trace(t,rows,projections[t['id']])
        if diffs:
            put(a.output/'first-mismatch.json',diffs[0]);raise RuntimeError('UART projected/native disagreement; frozen, no retry')
    put(a.output/'uart-positive-rows.json',allrows)
    t=next(t for t in decl['traces'] if t['id']==decl['mutant_trace'])
    rows=simulate(t,'mutant.vvp','partial-right-aligned-mutant')
    diffs,words=check_uart_trace(t,rows,projections[t['id']],mutant=True)
    assert diffs and diffs[0]['clock']==decl['expected_mutant_first_push_clock']
    assert words==[decl['mutant_wrong_word']] and words!=t['expected_words']
    put(a.output/'uart-actual-mutant-counterexample.json',diffs[0])
    assert inv.clocks==decl['aggregate_RTL_clocks'] and inv.resets==decl['reset_edges']
    return {'result':'pass','phase':'native','corpus':'uart-in1','scope':'finite waveform/output and projected IN/PUSH transitions; not closed whole-core/SV/UART theorem',
        'positive_streams':len(decl['traces']),'mutant_first_push':diffs[0]['clock']}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--phase',choices=('all','prepare','native'),default='all')
    for n in ('lean','lean-lib','iverilog','vvp','strace','prepared'):p.add_argument('--'+n,type=Path)
    p.add_argument('--prepared-sha256')
    p.add_argument('--corpus',choices=('in8','uart-in1'),default='in8')
    try:a=validate(p.parse_args())
    except (OSError,ValueError,KeyError,TypeError) as e:p.error(str(e))
    if a.phase!='all':result=phase(a)
    else:
        start=time.monotonic();a.output.mkdir(exist_ok=False)
        prep=argparse.Namespace(**{**vars(a),'phase':'prepare','output':a.output/'prepare'})
        prepared=phase(prep);result=prepared
        if prepared['result']=='pass':
            nxt=argparse.Namespace(**{**vars(a),'phase':'native','output':a.output/'native','prepared':prep.output,
                'prepared_sha256':digest(prep.output/'prepared-receipt.json')})
            try: result=phase(validate(nxt))
            except (OSError,ValueError,KeyError,TypeError) as e:result={'result':'fail','phase':'native-validation','error':str(e)}
        result={k:v for k,v in result.items() if k!='commands'}
        result['elapsed_seconds']=time.monotonic()-start
        put(a.output/'run-ledger.json',result)
    print(json.dumps({k:v for k,v in result.items() if k!='commands'},indent=2))
    return 0 if result['result']=='pass' else 1


if __name__=='__main__':sys.exit(main())
