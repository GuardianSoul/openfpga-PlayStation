#!/usr/bin/env python3
"""Exercise 640x1 DMA uploads with scanout and the actual posted SDRAM path.

Copies an existing verified generated GPU, with ideal/finite/pin-backed memory.
No CPU/BIOS/disc is read or running build edited. Authored pixels and public GPU
readback establish byte/order integrity; timings are scoped to this source.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import tempfile

from prepare_pocket_read_pipeline import prepare as retime_phy
from prepare_resource_targets import digest
from run_gpu_field_timing import copied_gpu
from tool_paths import ROOT,find_tool


def pin_model(directory):
    original=ROOT/'sim/memory/sdr_pin_model.sv';text=original.read_text()
    text=text.replace('module sdr_pin_model #(', 'module gpu_sdr_pin_model #(')
    marker='reg [15:0] memory [0:32767 + EDGE_REGIONS*24576];'
    if text.count(marker)!=1:raise ValueError('Pin memory declaration changed')
    text=text.replace(marker,'reg [15:0] memory [0:524287];')
    start=text.index('    function automatic integer slot(')
    end=text.index('    function automatic [24:0] fixture_address',start)
    text=text[:start]+'''    function automatic integer slot(input reg [24:0] a);
        if(a>=25'd524288) begin slot=0; $fatal(1,"GPU pin address outside primary VRAM %h",a); end
        else slot=integer'(a);
    endfunction
'''+text[end:]
    start=text.index('        for (integer i = 0; i < 32768 + EDGE_REGIONS*24576;')
    end=text.index('        for (integer b = 0; b < 4;',start)
    text=text[:start]+"        for(integer i=0;i<524288;i++) memory[i]=0;\n"+text[end:]
    path=directory/'gpu_sdr_pin_model.sv';path.write_text(text);return path


def bench(directory,gpu):
    template=(ROOT/'sim/reference/tb_gpu_primitives.sv').read_text()
    declarations=re.findall(r'^logic (\[\d+:\d+\]\s+)?(\w+)=([01]);',template,re.M)
    ports={n.lower():n for n in re.findall(r'\b(?:input|output)\s+(?:\[\d+:\d+\]\s+)?(\w+)',gpu['header'])}
    text='`timescale 1fs/1fs\nmodule tb_gpu_scanline;\nparameter MEMORY=0;\n'
    for width,name,value in declarations:text+=f'logic {width}{name}={value};\n'
    text+='logic [18:0] peek_addr=0; wire [15:0] peek_data;wire native_fault;\n'
    text+=gpu['module']+' gpu('+','.join('.'+ports[name]+'('+name+')' for _,name,_ in declarations)+');\n'
    text+='''always #14762550 clk1x=~clk1x;
always #7381275 clk2x=~clk2x;
always #9312170 clkvid=~clkvid;
reg toggle=0,sampled=0;
always @(posedge clk1x) toggle<=!toggle;
always @(posedge clk2x) begin sampled<=toggle;clk2xindex<=sampled==toggle;end
generate if(MEMORY==0)begin: ideal
 mister_ddr_model memory(.clk(clk2x),.busy(vram_busy),.burstcnt(vram_burstcnt),
 .addr({4'b0011,vram_addr[27:3]}),.dout(vram_dout),.ready(vram_dout_ready),.rd(vram_rd),
 .din(vram_din),.be(vram_be),.we(vram_we));
 assign peek_data=16'(memory.data[peek_addr>>1]>>((peek_addr[0])*16));
 assign native_fault=0;
end else if(MEMORY==1)begin: finite
 pocket_vram_model #(.MEMORY_WORDS(262656)) memory(.clk(clk2x),.busy(vram_busy),.burstcnt(vram_burstcnt),
 .addr({4'b0011,vram_addr[27:3]}),.dout(vram_dout),.ready(vram_dout_ready),.rd(vram_rd),
 .din(vram_din),.be(vram_be),.we(vram_we));
 assign peek_data=16'(memory.data[peek_addr>>1]>>((peek_addr[0])*16));
 assign native_fault=0;
end else begin: native_pins
 pocket_vram_native_fixture memory(.src_clk(clk2x),.reset_async(reset),
 .rd(vram_rd),.we(vram_we),.addr(vram_addr),.count(vram_burstcnt),.be(vram_be),.din(vram_din),
 .busy(vram_busy),.ready(vram_dout_ready),.dout(vram_dout),.fault(native_fault),
 .peek_addr(peek_addr),.peek_data(peek_data));
end endgenerate
'''
    text+=(ROOT/'sim/reference/tb_gpu_scanline_body.sv').read_text()
    path=directory/'tb_gpu_scanline.sv';path.write_text(text);return path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gpu-build',type=Path,required=True)
    parser.add_argument('--memory',type=int,choices=(0,1,2),action='append')
    args=parser.parse_args();tool=find_tool('verilator')
    if not tool:raise ValueError('Verilator required')
    parent=ROOT/'build/sim/gpu-scanline';parent.mkdir(parents=True,exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix='checks-',dir=parent))
    gpu=copied_gpu(args.gpu_build.resolve(),work,'scanline__')
    phy=retime_phy(work/'retimed-phy');pins=pin_model(work);top=bench(work,gpu)
    sources=[Path(gpu['path']),ROOT/'sim/reference/mister_memory_models.sv',
        ROOT/'sim/reference/pocket_vram_model.sv',ROOT/'sim/reference/pocket_vram_native_fixture.sv',
        ROOT/'rtl/pocket/psx_vram_stream.sv',ROOT/'references/Laxer3a_PSX/hdlGPU/rtl/dual_clock_fifo.sv',
        ROOT/'references/MIRLO64/rtl/soc/sdr_ctrl.sv',Path(phy['path']),pins,
        ROOT/'sim/memory/altddio_clock_model.sv',top]
    protected={str(p):digest(p) for p in [*sources,ROOT/'sim/memory/sdr_pin_model.sv',
        ROOT/'sim/reference/tb_gpu_scanline_body.sv',ROOT/'sim/reference/tb_gpu_primitives.sv',
        ROOT/'rtl/memory/psx_sdr_phy.sv',ROOT/'scripts/prepare_pocket_read_pipeline.py',
        ROOT/'scripts/run_gpu_field_timing.py',Path(gpu['original']),
        args.gpu_build.resolve()/'build-manifest.json',Path(__file__)]}
    results=[]
    for memory in args.memory or (0,1,2):
        case=work/f'memory-{memory}';case.mkdir()
        with (case/'build.log').open('w') as log:
            subprocess.run([tool,'--binary','--timing','-O3','-j','4','-Wno-fatal',
                '--output-split','20000','--top-module','tb_gpu_scanline','--Mdir',str(case/'obj'),
                '-GMEMORY='+str(memory),*map(str,sources)],cwd=case,stdout=log,
                stderr=subprocess.STDOUT,check=True,timeout=600)
        print('Run memory profile '+str(memory)+': '+str(work),flush=True)
        result=subprocess.run([str(case/'obj/Vtb_gpu_scanline')],cwd=case,
            capture_output=True,text=True,timeout=240)
        output=result.stdout+result.stderr;(case/'run.log').write_text(output);print(output,flush=True)
        if result.returncode or 'GPU_SCANLINE PASS' not in output:
            raise SystemExit('Scanline fixture failed; preserve '+str(work))
        results.append({'memory':memory,'exit_code':result.returncode,
            'log_sha256':digest(case/'run.log'),'events_sha256':digest(case/'scanline-events.csv')})
    for path,sha in protected.items():
        if digest(Path(path))!=sha:raise ValueError('Scanline input changed: '+path)
    (work/'report.json').write_text(json.dumps({'gpu':gpu,'protected_sha256':protected,'results':results,
        'physical_io_timing_profile':{'t_ac_first_ns':5.5,'t_ac_second_ns':5.5,'t_hz_ns':6.0,
            'memory_clock_mhz':125,'controller_clock_mhz':62.5,'refresh_interval_ns':7680},
        'limitations':['Authored burst source, no CPU/main/SPU/CD arbitration',
            'Fixed16CPUtick four-word bursts are not a full PSX DMA timing oracle',
            'Upload issue/idle deadline, final GPU readback fences pending posted writes',
            'Pin models do not establish board electrical or Quartus timing closure'],
        'game_correctness_verified':False,'hardware_verified':False},indent=2)+'\n')
    print(work)


if __name__=='__main__':main()
