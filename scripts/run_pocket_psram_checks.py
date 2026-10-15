#!/usr/bin/env python3
"""Byte oracle for the delayed CPU/SPU behavioral memory model."""
import json
import subprocess
from prepare_pocket_memory_models import prepare
from prepare_resource_targets import digest
from tool_paths import ROOT,find_tool

def main():
    directory=ROOT/'build/sim/pocket-psram-model';directory.mkdir(parents=True,exist_ok=True)
    model=prepare(directory)
    bench=ROOT/'sim/reference/tb_pocket_psram_model.sv';verilator=find_tool('verilator')
    concurrent=ROOT/'sim/reference/tb_pocket_psram_bus.sv';runs=[]
    cases=[('independent',bench,'tb_pocket_psram_model',[]),
        ('paired',bench,'tb_pocket_psram_model',['-GLANES=2','-GSHARED_BUS=0']),
        ('contention',concurrent,'tb_pocket_psram_bus',[]),
        ('missing-arbitration',concurrent,'tb_pocket_psram_bus',['-GSHARED_BUS=-1'])]
    for name,source,top,parameters in cases:
        with (directory/(name+'-build.log')).open('w')as log:
            subprocess.run([verilator,'--binary','--timing','-Wno-fatal','-j','2','--top-module',top,
                *parameters,'--Mdir',str(directory/(name+'-obj')),model['path'],str(source)],
                stdout=log,stderr=subprocess.STDOUT,check=True,timeout=300)
        result=subprocess.run([str(directory/(name+'-obj')/('V'+top))],capture_output=True,text=True,timeout=120)
        output=result.stdout+result.stderr;(directory/(name+'.log')).write_text(output)
        if name=='missing-arbitration':
            passed=result.returncode!=0 and 'Shared PSRAM bus budget violated' in output
        elif top=='tb_pocket_psram_bus':passed=result.returncode==0 and 'fills=2 read_beats=8' in output
        else:passed=result.returncode==0 and 'scalar=544 cache_fills=32 dma_bursts=32 fifo_writes=32 comparisons=752' in output
        if name=='missing-arbitration':print(name,'rejected',passed)
        else:print(name,output,end='')
        runs.append({'case':name,'parameters':parameters,'passed':passed,'stdout':output})
    report={'passed':all(r['passed'] for r in runs),'model':model,
        'bench_sha256':digest(bench),'contention_bench_sha256':digest(concurrent),'runs':runs,'hardware_verified':False}
    (directory/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    if not report['passed']:raise SystemExit(1)

if __name__=='__main__':main()
