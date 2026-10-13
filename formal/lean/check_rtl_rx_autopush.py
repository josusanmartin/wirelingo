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
    if a.phase=='native':
        if not a.prepared or not a.prepared_sha256: raise ValueError('native requires --prepared and its --prepared-sha256')
        a.prepared=a.prepared.resolve(strict=True)
        if digest(a.prepared/'prepared-receipt.json')!=a.prepared_sha256: raise ValueError('prepared receipt identity mismatch')
        receipt=json.loads((a.prepared/'prepared-receipt.json').read_text())
        if receipt['sources']!=a.sources: raise ValueError('stale prepared source')
        if receipt['declaration']!=declaration(): raise ValueError('stale prepared corpus')
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
    assert names==found and len(names)==5
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
    a.output.mkdir(exist_ok=False);put(a.output/'predeclared-stimuli.json',declaration())
    put(a.output/'input-receipt.json',{'sources':a.sources,'paths':{n:str(getattr(a,n)) for n in ('output','lean','lean_lib','iverilog','vvp','strace')},
        'prepared':str(a.prepared) if a.prepared else None,'prepared_sha256':a.prepared_sha256})
    inv=Invocation(a)
    try: result=prepare(inv) if a.phase=='prepare' else native(inv)
    except Exception as e: result={'result':'fail','phase':a.phase,'error':str(e)}
    finally:
        inv.dependencies.update([Path(sys.executable).resolve(),a.strace,*[ROOT/f for f in SOURCES]])
        put(a.output/'dependency-hashes.json',{str(f):digest(f) for f in sorted(inv.dependencies)})
        result.update(commands=inv.commands,elapsed_seconds=time.monotonic()-inv.start,
            RTL_clocks=inv.clocks,reset_edges=inv.resets,model_steps=0,state_deposits=0)
        put(a.output/'ledger.json',result)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--phase',choices=('all','prepare','native'),default='all')
    for n in ('lean','lean-lib','iverilog','vvp','strace','prepared'):p.add_argument('--'+n,type=Path)
    p.add_argument('--prepared-sha256')
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
