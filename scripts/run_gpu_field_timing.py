#!/usr/bin/env python3
"""Isolate 480i field timing using exact, protected existing GPU builds.

Copies generated Verilog; never edits/evaluates the running complete models.
No BIOS/disc is read. Compare native/optimized GPU with ideal/finite VRAM and
an every-field/every-other-field producer. This is not an FFVII replay.
"""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from prepare_resource_targets import digest
from tool_paths import ROOT,find_tool


def copied_gpu(build,directory,prefix):
    manifest=json.loads((build/'build-manifest.json').read_text())
    for path,sha in manifest['protected_sha256'].items():
        if digest(Path(path))!=sha:raise ValueError('Existing GPU build input changed: '+path)
    original=build/'system.v';text=original.read_text()
    names=re.findall(r'^module (\w+)',text,re.M)
    gpu=re.findall(r'^module (gpu(?:_Barch[^\s]*)?)\s*\n',text,re.M)
    if len(gpu)!=1:raise ValueError('Expected one exact GPU entity')
    expression=re.compile(r'\b('+ '|'.join(sorted(names,key=len,reverse=True))+r')\b')
    copy=directory/(prefix+'system.v');copy.write_text(expression.sub(lambda m:prefix+m.group(),text))
    header=text[text.index('module '+gpu[0]):].split(');',1)[0]
    return dict(path=str(copy),sha256=digest(copy),original=str(original),original_sha256=digest(original),
        manifest_sha256=digest(build/'build-manifest.json'),module=prefix+gpu[0],header=header)


def bench(directory,old,new):
    template=(ROOT/'sim/reference/tb_gpu_primitives.sv').read_text()
    declarations=re.findall(r'^logic (\[\d+:\d+\]\s+)?(\w+)=([01]);',template,re.M)
    ports={n.lower():n for n in re.findall(r'\b(?:input|output)\s+(?:\[\d+:\d+\]\s+)?(\w+)',old['header'])}
    outputs={n.lower() for n in re.findall(r'\boutput\s+(?:\[\d+:\d+\]\s+)?(\w+)',old['header'])}
    memory_inputs={'vram_busy','vram_dout','vram_dout_ready'}
    shared={n for _,n,_ in declarations}-outputs-memory_inputs
    text='`timescale 1fs/1fs\nmodule tb_gpu_field_timing;\nparameter USE_CANDIDATE=0,FINITE_MEMORY=0,DRAW_CADENCE=1;\n'
    for width,n,v in declarations:
        if n in shared:text+=f'logic {width}{n}={v};\n'
        else:text+=f'logic {width}old_{n}={v},new_{n}={v};\n'
    def instance(module,instance,prefix):
        return module+' '+instance+'('+','.join('.'+ports[n]+'('+('' if n in shared else prefix)+n+')'
            for _,n,_ in declarations)+');\n'
    text+=instance(old['module'],'old_gpu','old_')
    text+='generate if(USE_CANDIDATE)begin: selected_candidate\n'+instance(new['module'],'new_gpu','new_')
    text+='end else begin: selected_reference\n'+instance(old['module'],'new_gpu','new_')+'end endgenerate\n'
    text+='''always #14762550 clk1x=~clk1x;
always #7381275 clk2x=~clk2x;
always #9312170 clkvid=~clkvid;
reg toggle=0,sampled=0;
always @(posedge clk1x) toggle<=!toggle;
always @(posedge clk2x) begin sampled<=toggle;clk2xindex<=sampled==toggle;end
'''
    connections={p:f'.clk(clk2x),.busy({p}vram_busy),.burstcnt({p}vram_burstcnt),\n .addr({{4\'b0011,{p}vram_addr[27:3]}}),.dout({p}vram_dout),.ready({p}vram_dout_ready),.rd({p}vram_rd),\n .din({p}vram_din),.be({p}vram_be),.we({p}vram_we)' for p in ('old_','new_')}
    text+='mister_ddr_model old_vram('+connections['old_']+');\n'
    text+='generate if(FINITE_MEMORY)begin: new_memory\npocket_vram_model #(.MEMORY_WORDS(262656)) mem('+connections['new_']+');\n'
    text+='end else begin: new_memory\nmister_ddr_model mem('+connections['new_']+');\nend endgenerate\n'
    text+=(ROOT/'sim/reference/tb_gpu_field_timing_body.sv').read_text()
    path=directory/'tb_gpu_field_timing.sv';path.write_text(text);return path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference-build',type=Path,required=True)
    parser.add_argument('--candidate-build',type=Path,required=True)
    args=parser.parse_args();tool=find_tool('verilator')
    if not tool:raise SystemExit('Verilator required')
    parent=ROOT/'build/sim/gpu-field-timing';parent.mkdir(parents=True,exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix='checks-',dir=parent))
    old=copied_gpu(args.reference_build.resolve(),work,'field_reference__')
    new=copied_gpu(args.candidate_build.resolve(),work,'field_candidate__')
    top=bench(work,old,new)
    sources=[Path(old['path']),Path(new['path']),ROOT/'sim/reference/mister_memory_models.sv',
             ROOT/'sim/reference/pocket_vram_model.sv',top]
    protected={str(p):digest(p) for p in [*sources,Path(__file__),ROOT/'sim/reference/tb_gpu_field_timing_body.sv',
                                        ROOT/'sim/reference/tb_gpu_primitives.sv']}
    results=[]
    # Same GPU + changed memory, changed GPU + ideal memory, both changes, then
    # intentional cadence control. Keep each build independent and archived.
    for candidate,finite,cadence in ((0,0,1),(0,1,1),(1,0,1),(1,1,1),(1,1,2)):
        case=work/f'candidate-{candidate}-finite-{finite}-cadence-{cadence}';case.mkdir()
        with (case/'build.log').open('w')as log:
            subprocess.run([tool,'--binary','--timing','-O3','-j','4','-Wno-fatal',
                '--output-split','20000','--top-module','tb_gpu_field_timing','--Mdir',str(case/'obj'),
                '-GUSE_CANDIDATE='+str(candidate),'-GFINITE_MEMORY='+str(finite),'-GDRAW_CADENCE='+str(cadence),
                *map(str,sources)],cwd=case,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=600)
        result=subprocess.run([str(case/'obj/Vtb_gpu_field_timing')],cwd=case,capture_output=True,text=True,timeout=180)
        output=result.stdout+result.stderr;(case/'run.log').write_text(output)
        print(output,flush=True)
        results.append(dict(candidate=candidate,finite_memory=finite,draw_cadence=cadence,
            exit_code=result.returncode,log_sha256=digest(case/'run.log'),
            event_trace_sha256=digest(case/'field-events.csv')))
        if result.returncode or 'GPU_FIELD PASS' not in output:raise SystemExit('Field fixture failed; preserve '+str(work))
    for p,sha in protected.items():
        if digest(Path(p))!=sha:raise ValueError('Field fixture input changed: '+p)
    (work/'report.json').write_text(json.dumps(dict(reference=old,candidate=new,
        protected_sha256=protected,results=results,hardware_verified=False,FFVII_cause_verified=False),indent=2)+'\n')
    print(work)


if __name__=='__main__':main()
