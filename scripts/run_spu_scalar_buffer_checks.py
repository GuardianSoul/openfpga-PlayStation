#!/usr/bin/env python3
"""Byte/coherence/reset/CE checks for the private scalar SPU buffer."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from prepare_resource_targets import digest
from prepare_spu_scalar_buffer import prepare
from tool_paths import ROOT,find_tool


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--storage',choices=('MLAB','M10K','M10K-dual','M10K-stream'),default='MLAB')
    args=parser.parse_args()
    parent=ROOT/'build/sim/spu-scalar-buffer-checks';parent.mkdir(parents=True,exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix='checks-',dir=parent));print(work,flush=True)
    if args.storage=='M10K-stream':
        from prepare_spu_scalar_stream_buffer import prepare as prepare_stream,connect
        selected=prepare_stream(work/'candidate')
    elif args.storage=='M10K-dual':
        from prepare_spu_scalar_dual_buffer import prepare as prepare_dual
        selected=prepare_dual(work/'candidate')
    elif args.storage=='M10K':
        from prepare_spu_scalar_bram_buffer import prepare as prepare_bram
        selected=prepare_bram(work/'candidate')
    else:selected=prepare(work/'candidate',2)
    source=Path(selected['path'])
    rtl_inputs=[ROOT/'rtl/memory/psx_mem_cdc.sv',ROOT/'rtl/memory/psx_mem_bridge.sv',ROOT/'sim/pocket/tb_spu_scalar_buffer.sv']
    original_bench=rtl_inputs[-1]
    if args.storage=='M10K-stream':
        bench=work/'bench.sv';bench.write_text(connect(original_bench.read_text(),'dut','bridge'))
        rtl_inputs[-1]=bench;rtl_inputs += [Path(selected['bridge_path'])]
    inputs=[source,ROOT/'scripts/prepare_spu_scalar_buffer.py',Path(__file__),*rtl_inputs]
    if args.storage!='MLAB':inputs += [ROOT/'scripts/prepare_spu_scalar_bram_buffer.py']
    if args.storage in ('M10K-dual','M10K-stream'):inputs += [ROOT/'scripts/prepare_spu_scalar_dual_buffer.py']
    endpoint_bench=ROOT/'sim/pocket/tb_spu_scalar_stream_bridge.sv'
    if args.storage=='M10K-stream':inputs += [ROOT/'scripts/prepare_spu_scalar_stream_buffer.py',original_bench,endpoint_bench]
    protected={str(p):digest(p) for p in inputs};archives={};runs=[]
    for i,(name,h) in enumerate(protected.items()):
        p=work/'input-archive'/str(i)/Path(name).name;p.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(name,p)
        if digest(p)!=h:raise ValueError('Buffer check input changed during archive')
        archives[name]=dict(path=str(p),sha256=h)
    def run(name,mutation=None,parameters=()):
        target=source
        if mutation:
            before,after=mutation;text=source.read_text()
            if text.count(before)!=1:raise ValueError('Scalar buffer mutation anchor changed')
            target=work/(name+'.sv');target.write_text(text.replace(before,after))
        binary=work/(name+'.vvp')
        with (work/(name+'-compile.log')).open('w') as log:
            subprocess.run([find_tool('iverilog'),'-g2012','-s','tb_spu_scalar_buffer','-o',str(binary),
                *(['-Ptb_spu_scalar_buffer.TEST_CANCEL='+str({'M10K-dual':2,'M10K-stream':3}.get(args.storage,1))] if args.storage!='MLAB' else []),
                *['-Ptb_spu_scalar_buffer.'+p for p in parameters],
                str(target),*map(str,rtl_inputs)],stdout=log,stderr=subprocess.STDOUT,check=True,timeout=60)
        r=subprocess.run([find_tool('vvp'),str(binary)],capture_output=True,text=True,timeout=120)
        output=r.stdout+r.stderr;log=work/(name+'.log');log.write_text(output)
        passed=(r.returncode!=0 and 'Scalar buffer read mismatch' in output if mutation else
                r.returncode==0 and output.count('PASS scalar buffer checks')==1)
        runs.append(dict(name=name,passed=passed,exit_code=r.returncode,output=output,
            clock_parameters=list(parameters),
            selected_source_sha256=digest(target),log_sha256=digest(log),binary_sha256=digest(binary)))
        print(name,passed,output,flush=True);return passed
    if run('ordinary'):
        run('stale-write',('if(!rnw) begin valid<=0;valid1<=0;end',"if(1'b0) begin valid<=0;valid1<=0;end"))
        run('stale-reload',('if(discard_returns) begin valid<=0;valid1<=0;end',"if(1'b0) begin valid<=0;valid1<=0;end"))
        if args.storage=='M10K':run('publish-cancelled-fill',('if(!fill_cancel && !discard_returns) begin',"if(1'b1) begin"))
        if args.storage in ('M10K-dual','M10K-stream'):run('publish-cancelled-fill',('if(!write && !rerror && !discard_returns && !fill_cancel) begin',"if(!write && !rerror) begin"))
        if args.storage in ('M10K-dual','M10K-stream'):
            run('shifted-clock-phase',parameters=('MEM_PHASE=7.35',))
            run('slower-memory',parameters=('MEM_HALF=19.137','MEM_PHASE=13.7'))
        if args.storage=='M10K-stream':
            for name in ('endpoint','endpoint-admit-invalid'):
                target=Path(selected['bridge_path'])
                if name.endswith('invalid'):
                    old='state<=invalid ? RESPONSE:req_write ? WRITE:READ;'
                    text=target.read_text()
                    if text.count(old)!=1:raise ValueError('Endpoint mutation anchor changed')
                    target=work/(name+'.sv');target.write_text(text.replace(old,'state<=req_write ? WRITE:READ;'))
                binary=work/(name+'.vvp')
                c=subprocess.run([find_tool('iverilog'),'-g2012','-s','tb_spu_scalar_stream_bridge','-o',str(binary),
                    str(target),str(endpoint_bench)],capture_output=True,text=True,timeout=60)
                (work/(name+'-compile.log')).write_text(c.stdout+c.stderr)
                c.check_returncode()
                r=subprocess.run([find_tool('vvp'),str(binary)],capture_output=True,text=True,timeout=120)
                output=r.stdout+r.stderr;log=work/(name+'.log');log.write_text(output)
                passed=(r.returncode!=0 and 'Scalar endpoint command coverage' in output if name.endswith('invalid') else
                    r.returncode==0 and 'PASS scalar stream endpoint commands=16' in output)
                runs.append(dict(name=name,passed=passed,exit_code=r.returncode,output=output,
                    selected_source_sha256=digest(target),log_sha256=digest(log),binary_sha256=digest(binary)))
                print(name,passed,output,flush=True)
    identity=all(digest(Path(p))==h for p,h in protected.items())
    expected_runs={'MLAB':3,'M10K':4,'M10K-dual':6,'M10K-stream':8}[args.storage]
    report=dict(passed=identity and len(runs)==expected_runs and all(r['passed'] for r in runs),
        source_identity_verified=identity,source_sha256=protected,input_archives=archives,runs=runs,
        selected=selected,storage=args.storage,native_pin_model=False,deadline_verified=False,area_verified=False,
        hardware_verified=False,game_verified=False)
    (work/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(work/'report.json',flush=True)
    if not report['passed']:raise SystemExit(1)


if __name__=='__main__':main()
