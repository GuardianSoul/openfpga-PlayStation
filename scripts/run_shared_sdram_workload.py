#!/usr/bin/env python3
"""Run an authored guest through the connected GPU/SPU/card DDR arbiter.

This evaluates the existing native topology, not production selection or fit.
FASTSIM skips external startup clears; full clear proof is a separate target.
"""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from ghdl_verilog import normalize_generated_verilog
from prepare_main_reset_fence import fenced_top
from prepare_pocket_audio_stream import capture_spu_text
from prepare_resource_targets import digest
from prepare_shared_sdram import prepare as prepare_phy
from prepare_system_verilator import prepare
from reference_shared_workload_program import write_program
from run_shared_sdram_system import bench_text,chip_text,replace
from tool_paths import ROOT,find_tool


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--native',action='store_true')
    ap.add_argument('--fault',choices=('card-data','spu-data'))
    ap.add_argument('--max-cycles',type=int,default=30000000)
    args=ap.parse_args()
    kind='native' if args.native else 'functional'
    parent=ROOT/'build/sim/shared-sdram-workload'/kind;parent.mkdir(parents=True,exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix='checks-',dir=parent));print(work,flush=True)
    prepared=prepare(work/'sources',fast_reset=True)
    for s in prepared['sources']:
        p=Path(s['path']);text=p.read_text()
        if args.native and p.name in ('psx_top.vhd','memorymux.vhd','psx_mister_psx_top_resource.vhd'):
            text=fenced_top(text,p.name);s['transforms'].append('Existing native main reset fence')
        if p.name=='psx_mister_psx_top_resource.vhd':
            text=replace(text,"SPUSDRAM => '1',","SPUSDRAM => '0',")
            s['transforms'].append('Evaluate existing cached SPU with real DDR arbiter')
        if p.name=='spu.vhd':
            text=capture_spu_text(text);s['transforms'].append('Observe completed stereo pair; original ports unchanged')
        p.write_text(text);s['sha256']=digest(p)
    program=write_program(work/'program')
    for name,key in [('boot.hex','program'),('boot.hex8','bytes'),('expected.hex','expected')]:shutil.copyfile(program[key],work/name)
    body=ROOT/'sim/pocket/shared_sdram_workload_body.sv'
    text=bench_text(True) if args.native else (ROOT/'sim/reference/tb_psx_system_fast.sv').read_text()
    text=text[:text.index('integer cycles=0,checks=0,max_cycles=')]+body.read_text()
    text=replace(text,'logic [63:0] ddr3_DIN=0;','wire [63:0] ddr3_DIN;')
    text=replace(text,'.ddr3_DIN(ddr3_DIN)', '.ddr3_DIN(raw_ddr3_DIN)')
    if not args.native:
        text=replace(text,'.ddr3_DOUT(ddr3_DOUT),','.ddr3_DOUT(header_input),')
        text=replace(text,'initial begin repeat(100) @(negedge clk1x);reset=0;end\n','')
    bench=work/'tb.sv';bench.write_text(text)
    netlist=work/'system.v'
    sources=[netlist,ROOT/'sim/reference/mister_memory_models.sv',bench]
    phy=None
    if args.native:
        phy=prepare_phy(work/'phy');chip=work/'chip.sv';chip.write_text(chip_text(True))
        sources += [Path(phy['path']),ROOT/'references/MIRLO64/rtl/soc/sdr_ctrl.sv',
            *[ROOT/'rtl/pocket'/n for n in ('psx_sdram_shared.sv','psx_spu_sdram_adapter.sv',
                'psx_mister_main_adapter.sv','psx_main_reset_fence.sv','psx_vram_stream.sv')],
            *[ROOT/'rtl/memory'/n for n in ('psx_mem_bridge.sv','psx_mem_cdc.sv')],
            ROOT/'references/Laxer3a_PSX/hdlGPU/rtl/dual_clock_fifo.sv',
            ROOT/'sim/memory/altddio_clock_model.sv',chip]
    ghdl,verilator=find_tool('ghdl'),find_tool('verilator')
    with (work/'build.log').open('w') as log:
        for s in prepared['sources']:
            subprocess.run([ghdl,'-a','--std=08','-frelaxed','--work='+s['library'],s['path']],cwd=work,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=120)
        with netlist.open('w') as out:
            subprocess.run([ghdl,'--synth','--std=08','-frelaxed','--vendor-library=ramip','--out=verilog',
                'psx_mister_psx_top_resource'],cwd=work,stdout=out,stderr=log,check=True,timeout=180)
        prepared['verilog_normalization']=normalize_generated_verilog(netlist)
        command=[verilator,'--binary','--timing','-UVERILATOR','-O3','-j','4','-Wno-fatal',
            *(['-DNATIVE_MEMORY_WORKLOAD'] if args.native else []),'--output-split','20000',
            '--top-module','tb_psx_fast','--Mdir',str(work/'obj'),*map(str,sources)]
        subprocess.run(command,cwd=work,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=600)
    inputs=[*sources,body,Path(__file__),ROOT/'scripts/reference_shared_workload_program.py',
            ROOT/'scripts/run_shared_sdram_system.py',ROOT/'scripts/prepare_system_verilator.py',
            ROOT/'scripts/prepare_main_reset_fence.py',ROOT/'scripts/prepare_pocket_audio_stream.py',
            ROOT/'scripts/prepare_pocket_sdram_only.py',ROOT/'scripts/prepare_shared_sdram.py',
            ROOT/'sim/memory/sdr_pin_model.sv',ROOT/'sim/reference/tb_psx_system_fast.sv',
            work/'boot.hex',work/'boot.hex8',work/'expected.hex']
    for s in prepared['sources']:inputs.extend(Path(s[k]) for k in ('path','original','selected') if k in s)
    hashes={str(p):digest(p) for p in inputs};archives={}
    for i,(name,h) in enumerate(hashes.items()):
        p=work/'input-archive'/str(i)/Path(name).name;p.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(name,p)
        if digest(p)!=h:raise ValueError('Workload input changed during archive')
        archives[name]=dict(path=str(p),sha256=h)
    binary=work/'obj/Vtb_psx_fast'
    command=[str(binary),'+expected_count='+str(program['expected_checks']),'+max_cycles='+str(args.max_cycles)]
    if args.fault:command+=['+bad_'+args.fault.replace('-','_')]
    if shutil.which('stdbuf'):command=[shutil.which('stdbuf'),'-oL',*command]
    print('Running',kind,args.fault or 'ordinary workload',flush=True)
    with (work/'run.log').open('w') as log:
        result=subprocess.run(command,cwd=work,stdout=log,stderr=subprocess.STDOUT,timeout=1800)
    output=(work/'run.log').read_text();identity=all(digest(Path(p))==h for p,h in hashes.items())
    marker='Shared workload guest mismatch' if args.fault=='card-data' else 'Shared workload PCM mismatch'
    passed=identity and (result.returncode!=0 and marker in output if args.fault else
        result.returncode==0 and output.count('PASS SHARED MEMORY WORKLOAD')==1)
    summary=re.search(r'WORKLOAD_RESULT (\{[^\n]+\})',output)
    metrics=json.loads(summary[1]) if summary else None
    if metrics:metrics['audio_deadline_met']=metrics['audio_timeouts']==0 and metrics['audio_pairs']>=metrics['audio_ticks']-1
    report=dict(passed=passed,source_identity_verified=identity,source_sha256=hashes,input_archives=archives,
        preparation=prepared,phy=phy,binary_sha256=digest(binary),program={k:str(v) if isinstance(v,Path) else v for k,v in program.items()},
        native=args.native,fault=args.fault,exit_code=result.returncode,output=output,metrics=metrics,
        log_sha256=digest(work/'run.log'),FASTSIM=True,full_reset_clear_verified=False,
        real_CPU_GPU_SPU_DMA_SIO_and_DDR_arbiter=True,physical_pin_model_verified=args.native and passed and not args.fault,
        APF_connected=False,full_optimized_candidate=False,production_selected=False,
        fit_run=False,timing_verified=False,hardware_verified=False,game_verified=False)
    (work/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(output[-3000:],flush=True);print(work/'report.json',flush=True)
    if not passed:raise SystemExit(1)


if __name__=='__main__':main()
