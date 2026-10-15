#!/usr/bin/env python3
"""Check fresh drain ownership, repeated resets, and stopped clocks."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from prepare_resource_targets import digest
from tool_paths import ROOT, find_tool


def main():
    rtl=ROOT/'rtl/pocket/psx_main_reset_fence.sv'
    bench=ROOT/'sim/pocket/tb_main_reset_fence.sv'
    transport=ROOT/'sim/pocket/tb_main_reset_transport.sv'
    dependencies=[transport,ROOT/'rtl/pocket/psx_mister_main_adapter.sv',ROOT/'rtl/memory/psx_mem_cdc.sv']
    parent=ROOT/'build/sim/main-reset-fence';parent.mkdir(parents=True,exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix='checks-',dir=parent))
    protected={str(p):digest(p) for p in (rtl,bench,Path(__file__),*dependencies)}
    runs=[]
    profiles=[('nominal',{}),('phase',{'PHASE3':'2.4'}),
        ('pocket',{'HALF1':'14.76255','HALF3':'4.92085'}),
        ('slow-service',{'HALF3':'11.3','PHASE3':'3.1'})]
    faults=[('early-token','WAIT_SOURCE: if(source_idle) begin','WAIT_SOURCE: if(1) begin',
        'Fence token bypassed source ownership acknowledgement'),
        ('busy-service-ack','request_sync[1]!=acknowledge_token && service_idle',
         'request_sync[1]!=acknowledge_token','Fence released before fresh drain acknowledgement'),
        ('stale-ack','acknowledge_sync[1]==request_token','1','Fence released before fresh drain acknowledgement')]
    cases=[(name,params,None) for name,params in profiles]+[(name,{},(a,b,expected)) for name,a,b,expected in faults]
    for name,params,fault in cases:
        folder=work/name;folder.mkdir();selected=rtl
        if fault:
            a,b,expected=fault;text=rtl.read_text()
            if text.count(a)!=1:raise ValueError('Fence fault anchor changed')
            selected=folder/'fault.sv';selected.write_text(text.replace(a,b))
        binary=folder/'sim.vvp'
        with (folder/'build.log').open('w') as log:
            subprocess.run([find_tool('iverilog'),'-g2012','-s','tb_main_reset_fence',
                *[f'-Ptb_main_reset_fence.{k}={v}' for k,v in params.items()],
                '-o',str(binary),str(selected),str(bench)],stdout=log,stderr=subprocess.STDOUT,check=True,timeout=60)
        result=subprocess.run([find_tool('vvp'),str(binary)],capture_output=True,text=True,timeout=30)
        output=result.stdout+result.stderr;(folder/'run.log').write_text(output)
        passed=(result.returncode!=0 and expected in output if fault else
            result.returncode==0 and 'PASS MAIN RESET FENCE' in output)
        runs.append({'name':name,'fault':bool(fault),'passed':passed,'exit_code':result.returncode,
            'rtl_sha256':digest(selected),'output':output})
    for name,params in profiles:
        folder=work/('transport-'+name);folder.mkdir();binary=folder/'sim.vvp'
        with (folder/'build.log').open('w') as log:
            subprocess.run([find_tool('iverilog'),'-g2012','-s','tb_main_reset_transport',
                *[f'-Ptb_main_reset_transport.{k}={v}' for k,v in params.items()],
                '-o',str(binary),str(rtl),*map(str,dependencies)],stdout=log,stderr=subprocess.STDOUT,check=True,timeout=60)
        result=subprocess.run([find_tool('vvp'),str(binary)],capture_output=True,text=True,timeout=30)
        output=result.stdout+result.stderr;(folder/'run.log').write_text(output)
        runs.append({'name':'transport-'+name,'fault':False,'passed':result.returncode==0 and
            'PASS MAIN RESET TRANSPORT' in output and output.count('RESET_TRANSPORT_CASE')==7,
            'exit_code':result.returncode,'output':output})
    identity=all(digest(Path(p))==h for p,h in protected.items())
    report={'passed':identity and all(r['passed'] for r in runs),'source_identity_verified':identity,
        'source_sha256':protected,'runs':runs,'hardware_verified':False,'full_reset_system_verified':False}
    for index,name in enumerate(protected):
        copy=work/'inputs'/str(index)/Path(name).name;copy.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(name,copy)
    (work/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(work/'report.json');print(json.dumps({'passed':report['passed'],'runs':runs},indent=2))
    if not report['passed']:raise SystemExit(1)


if __name__=='__main__':main()
