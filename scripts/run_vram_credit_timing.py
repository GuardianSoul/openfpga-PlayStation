#!/usr/bin/env python3
"""Whole VRAM frontend byte/cycle proof for conservative BUSY credits.

Uses the existing independent physical-word/byte/mask/reset oracle. No PHY,
clock, fitted project or production RTL is edited; no STA/fitter is invoked.
"""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from prepare_resource_targets import digest
from prepare_vram_credit_timing import conservative_text,prepare
from tool_paths import ROOT,find_tool


def address_fanin(work,source,fifo,name):
    """Conservative cell-level combinational cone, stopping at state cells."""
    yosys=shutil.which('yosys') or str(ROOT/'build/tools/oss-cad-suite/bin/yosys')
    netlist=work/(name+'-cone.json')
    command=[yosys,'-Q','-T','-p',
        f'read_verilog -sv -D SYNTHESIS "{source}" "{fifo}"; hierarchy -top psx_vram_stream; '
        f'proc; flatten; opt; write_json "{netlist}"']
    with (work/(name+'-cone.log')).open('w') as log:
        subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=120)
    module=json.loads(netlist.read_text())['modules']['psx_vram_stream']
    drivers={}
    for cell in module['cells'].values():
        for port,direction in cell['port_directions'].items():
            if direction=='output':
                for bit in cell['connections'][port]:
                    if isinstance(bit,int):drivers[bit]=cell
    seen=set();frontier=set();pending=list(module['ports']['busy']['bits'])
    while pending:
        bit=pending.pop()
        if not isinstance(bit,int) or bit in seen:continue
        seen.add(bit);cell=drivers.get(bit)
        if cell is None:continue
        kind=cell['type']
        if any(tag in kind for tag in ('dff','latch')):
            frontier.add(kind);continue
        for port,direction in cell['port_directions'].items():
            if direction=='input':pending.extend(cell['connections'][port])
    address_bits=[i for i,b in enumerate(module['ports']['addr']['bits']) if b in seen]
    return dict(current_address_bits_in_busy_cone=address_bits,state_cell_frontier=sorted(frontier),
                netlist_sha256=digest(netlist),log_sha256=digest(work/(name+'-cone.log')),command=command,
                method='Overapproximate combinational cell inputs; stop at flip-flops/latches; no technology mapping or STA')


def credit_lemma(work,source):
    """Prove the bounded modulo identity; deliberately break it as a control."""
    yosys=shutil.which('yosys') or str(ROOT/'build/tools/oss-cad-suite/bin/yosys')
    fault=work/'credit-lemma-fault.sv'
    text=source.read_text();anchor='projected=posted+addend;'
    if text.count(anchor)!=1:raise ValueError('Credit lemma fault anchor changed')
    fault.write_text(text.replace(anchor,'projected=posted+addend+1;'))
    runs=[]
    for size,path,negative in [(m,source,False) for m in (512,1024,2048,4096)]+[(1024,fault,True)]:
        name='lemma-fault' if negative else 'lemma-'+str(size)
        command=[yosys,'-Q','-T','-p',f'read_verilog "{path}"; chparam -set M {size} vram_credit_lemma; '
                 'prep -top vram_credit_lemma; flatten; sat -prove bad 0 -verify']
        log=work/(name+'.log')
        with log.open('w') as output:
            completed=subprocess.run(command,stdout=output,stderr=subprocess.STDOUT,timeout=60)
        text=log.read_text()
        passed=(completed.returncode!=0 and 'FAIL!' in text) if negative else (completed.returncode==0 and 'SUCCESS!' in text)
        runs.append(dict(max_write_words=size,negative=negative,passed=passed,exit_code=completed.returncode,
                         source_sha256=digest(path),log_sha256=digest(log),command=command))
        if not passed:raise ValueError('Credit arithmetic lemma/control failed: '+name)
    return runs


def main():
    compiler,runtime=find_tool('iverilog'),find_tool('vvp')
    if not compiler or not runtime:raise ValueError('Icarus Verilog required')
    parent=ROOT/'build/sim/vram-credit-timing';parent.mkdir(parents=True,exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix='checks-',dir=parent));print(work,flush=True)
    variant=prepare(work/'candidate');original=Path(variant['original']);candidate=Path(variant['path'])
    previous=work/'previous-conservative.sv';previous.write_text(conservative_text(original.read_text()))
    fifo=ROOT/'references/Laxer3a_PSX/hdlGPU/rtl/dual_clock_fifo.sv'
    oracle=ROOT/'sim/memory/tb_psx_vram_stream.sv';focused=ROOT/'sim/pocket/tb_vram_credit_timing.sv'
    lemma=ROOT/'sim/pocket/vram_credit_lemma.sv'
    inputs=[original,previous,candidate,fifo,oracle,focused,lemma,Path(__file__),ROOT/'scripts/prepare_vram_credit_timing.py']
    hashes={str(p):digest(p) for p in inputs};archives={}
    for i,(name,h) in enumerate(hashes.items()):
        p=work/'input-archive'/str(i)/Path(name).name;p.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(name,p)
        if digest(p)!=h:raise ValueError('VRAM proof input changed during archive')
        archives[name]=dict(path=str(p),sha256=h)
    formal=credit_lemma(work,lemma)
    def run(name,source,bench,top,parameters):
        binary=work/(name+'.vvp');log=work/(name+'-compile.log')
        with log.open('w') as out:
            subprocess.run([compiler,'-g2012','-s',top,*['-P'+top+'.'+k+'='+str(v) for k,v in parameters.items()],
                '-o',str(binary),str(source),str(fifo),str(bench)],stdout=out,stderr=subprocess.STDOUT,check=True,timeout=60)
        trace=work/(name+'-trace.txt')
        result=subprocess.run([runtime,str(binary),'+trace='+str(trace)],capture_output=True,text=True,timeout=180)
        output=result.stdout+result.stderr;path=work/(name+'.log');path.write_text(output)
        return dict(name=name,exit_code=result.returncode,output=output,log_sha256=digest(path),binary_sha256=digest(binary),
                    trace_sha256=digest(trace) if trace.exists() else None,
                    trace_lines=sum(1 for _ in trace.open()) if trace.exists() else 0)
    profiles=[]
    for i,(src,mem,aw) in enumerate(((3,8,4),(31,4,2),(7.381275,8,4))):
        parameters=dict(BACKEND=0,SRC_HALF_NS=src,MEM_HALF_NS=mem,FIFO_AW=aw,TRACE=1)
        base=run('base-'+str(i),original,oracle,'tb_psx_vram_stream',parameters)
        predecessor=run('previous-'+str(i),previous,oracle,'tb_psx_vram_stream',parameters)
        changed=run('candidate-'+str(i),candidate,oracle,'tb_psx_vram_stream',parameters)
        passed=all(x['exit_code']==0 and 'VRAM_STREAM PASS' in x['output'] for x in (base,predecessor,changed))
        passed &= base['trace_sha256']==predecessor['trace_sha256']==changed['trace_sha256'] and base['trace_lines']>1000
        profiles.append(dict(profile=parameters,base=base,previous=predecessor,candidate=changed,passed=passed))
        print('Whole frontend profile',i,'passed=',passed,'trace_lines=',base['trace_lines'],flush=True)
        if not passed:break
    focus=run('conservative-capacity',candidate,focused,'tb_vram_credit_timing',{})
    previous_focus=run('previous-capacity',previous,focused,'tb_vram_credit_timing',{})
    legacy=run('old-address-dependency',original,focused,'tb_vram_credit_timing',{})
    focus_ok=all(x['exit_code']==0 and 'PASS VRAM conservative credit:' in x['output'] for x in (focus,previous_focus))
    old_rejected=legacy['exit_code']!=0 and 'VRAM BUSY depends on invalid address' in legacy['output']
    print(focus['output'],legacy['output'],flush=True)
    base_cone=address_fanin(work,original,fifo,'base')
    candidate_cone=address_fanin(work,candidate,fifo,'candidate')
    cone_ok=bool(base_cone['current_address_bits_in_busy_cone']) and not candidate_cone['current_address_bits_in_busy_cone']
    fault=work/'credit-fault.sv';text=candidate.read_text()
    anchor='projected_delta=projected_posted-write_binary(finished_word_sync);'
    if text.count(anchor)!=1:raise ValueError('VRAM credit fault anchor changed')
    fault.write_text(text.replace(anchor,"projected_delta=projected_posted-write_binary(finished_word_sync)+1'b1;"))
    fault_run=run('credit-fault',fault,oracle,'tb_psx_vram_stream',profiles[0]['profile'])
    # A shifted credit count can keep all bytes correct while changing BUSY.
    # Compare the full source+memory-edge trace, not only the byte scoreboard.
    fault_detected=bool(fault_run['trace_sha256'] and fault_run['trace_sha256']!=profiles[0]['candidate']['trace_sha256'])
    identity=all(digest(Path(n))==h for n,h in hashes.items())
    report=dict(passed=identity and len(profiles)==3 and all(p['passed'] for p in profiles) and focus_ok and old_rejected and cone_ok and fault_detected,
        source_identity_verified=identity,source_sha256=hashes,input_archives=archives,variant=variant,
        profiles=profiles,focused=focus,previous_focused=previous_focus,old_dependency_rejected=old_rejected,negative_control=legacy,
        modulo_lemma=formal,credit_fault=fault_run,credit_fault_trace_difference_detected=fault_detected,
        base_busy_fanin=base_cone,candidate_busy_fanin=candidate_cone,current_address_dependency_removed=cone_ok,
        physical_pin_model_verified=False,STA_run=False,fit_run=False,production_RTL_changed=False,
        hardware_verified=False,game_verified=False)
    (work/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(work/'report.json',flush=True)
    if not report['passed']:raise SystemExit(1)


if __name__=='__main__':main()
