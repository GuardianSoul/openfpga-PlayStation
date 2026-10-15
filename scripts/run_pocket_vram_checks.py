#!/usr/bin/env python3
"""Validate finite-bandwidth VRAM byte and handshake semantics locally."""
import json
import subprocess
from prepare_resource_targets import digest
from tool_paths import ROOT,find_tool

def main():
    compiler=find_tool('iverilog');runtime=find_tool('vvp')
    directory=ROOT/'build/sim/pocket-vram-model';directory.mkdir(parents=True,exist_ok=True)
    sources=[ROOT/'sim/reference/pocket_vram_model.sv',ROOT/'sim/reference/tb_pocket_vram_model.sv']
    results=[]
    for frequency,lanes in ((125000000,1),(61875000,1),(61875000,2)):
        binary=directory/f'{frequency}-{lanes}.vvp'
        subprocess.run([compiler,'-g2012','-s','tb_pocket_vram_model',
            f'-Ptb_pocket_vram_model.MEM_HZ={frequency}',f'-Ptb_pocket_vram_model.LANES={lanes}',
            '-o',str(binary),*map(str,sources)],check=True,timeout=60)
        result=subprocess.run([runtime,str(binary)],capture_output=True,text=True,timeout=60)
        output=result.stdout+result.stderr;(directory/f'{frequency}-{lanes}.log').write_text(output)
        print(output,end='');results.append({'memory_hz':frequency,'lanes':lanes,'exit_code':result.returncode,'stdout':output})
    mutations=[]
    original=sources[0].read_text()
    variants={
        'refresh-drop':original.replace('IDLE: if((rd || we) && !busy) begin',
            'IDLE: if((rd || we) && !busy && refresh_pending==0) begin'),
        'unlimited-payload':original.replace('delay_ticks=4/HALFWORDS_PER_TICK','delay_ticks=1')
            .replace('delay_ticks+=4/HALFWORDS_PER_TICK','delay_ticks+=1')}
    for name,text in variants.items():
        if text==original:raise ValueError('Mutation marker changed')
        mutated=directory/(name+'.sv');mutated.write_text(text)
        binary=directory/(name+'.vvp')
        subprocess.run([compiler,'-g2012','-s','tb_pocket_vram_model','-o',str(binary),
            str(mutated),str(sources[1])],check=True,timeout=60)
        result=subprocess.run([runtime,str(binary)],capture_output=True,text=True,timeout=60)
        output=result.stdout+result.stderr;(directory/(name+'.log')).write_text(output)
        reasons=('Lost read beat','VRAM byte mismatch','Memory bus bandwidth budget violated') if name=='refresh-drop' else ('Memory bus bandwidth budget violated',)
        rejected=result.returncode!=0 and any(reason in output for reason in reasons)
        mutations.append({'name':name,'rejected':rejected,'stdout':output});print(name,'rejected',rejected)
    report={'passed':all(r['exit_code']==0 and 'PASS VRAM model' in r['stdout'] for r in results),
        'source_hashes':{str(p):digest(p) for p in sources},'runs':results,'mutations':mutations,'hardware_verified':False}
    report['passed'] &= all(m['rejected'] for m in mutations)
    (directory/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    if not report['passed']:raise SystemExit(1)

if __name__=='__main__':main()
