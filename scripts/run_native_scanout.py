#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check first-line scanout against the selected full GPU and Native PSRAM.

Authored NTSC 640x480i commands exercise idle, opaque writes and additive
read/modify/write drawing over a coded corner. Independent Native word,
line-RAM generation and visible RGB oracles run together. A lost first RAM
word must fail the visible RGB check. No BIOS or disc is read.

This is a digital Verilator composition. The card DDR arbiter, APF scaler,
actual BIOS trace, four-state pin apertures and hardware are separate gates.
"""
import argparse
import concurrent.futures
import json
from pathlib import Path
import re
import subprocess
import tempfile
from prepare_native_scanout_sim import prepare_snapshot, prepare_gpu
from prepare_resource_targets import digest
from tool_paths import ROOT, find_tool

def make_bench(directory, manifest):
    HERE = Path(directory).resolve()
    m=manifest
    gpu=HERE/'gpu/gpu.v';text=gpu.read_text()
    header=text[text.index('module gpu\n'):].split(');',1)[0]
    ports=re.findall(r'\b(input|output)\s+(\[[^\]]+\]\s+)?(\w+)',header)
    lower={name.lower():name for _,_,name in ports}
    t='`timescale 1ns/1ps\nmodule tb;\n'
    for direction,width,name in ports:
        if direction=='input':
            value=1 if name.lower() in ('ce','reset','ss_reset','gun1offscreen','gun2offscreen','fixedvblank') else 0
            if name.lower() not in ('vram_busy','vram_dout','vram_dout_ready'):
                t+=f'reg {width}{name.lower()}={value};\n'
            else:t+=f'wire {width}{name.lower()};\n'
        else:t+=f'wire {width}{name.lower()};\n'
    t+='gpu core ('+',\n'.join('.'+name+'('+name.lower()+')' for _,_,name in ports)+');\n'
    t+='''always #14.76255 clk1x=~clk1x;
    always #7.381275 clk2x=~clk2x;
    real video_phase=0;
    initial begin if($value$plusargs("VIDEO_PHASE=%f",video_phase))begin end
     #(video_phase);forever #9.31219 clkvid=~clkvid;end
    reg toggle=0,sampled=0;
    always @(posedge clk1x)toggle<=!toggle;
    always @(posedge clk2x)begin sampled<=toggle;clk2xindex<=sampled==toggle;end
    wire clk=clk2x;reg park=0;
    localparam real CLK_DLY=1,CTL_DLY=1,DQ_DLY=1,RET_DLY=1,T_ACLK=9,T_KOH=2;
    localparam integer LATENCY=3,ROW_LOG2=6;
    `include "psx_vram_psram_windows.svh"
    '''
    fixture=Path(m['extra']['tb_psx_vram_psram.sv']['captured']).read_text()
    prefix=fixture[fixture.index('    reg [7:0] ddr_burstcnt'):fixture.index('    // ---- golden memory')]
    prefix=prefix.replace(' dut (',' memory (')
    for n in ['ddr_burstcnt','ddr_addr','ddr_rd','ddr_we','ddr_din','ddr_be']:
        # Keep types as logic; remove only their initializer before wiring them.
        prefix=re.sub(r'\b'+n+r' = 0',n,prefix)
    t+=prefix
    t+='''assign ddr_burstcnt=vram_burstcnt;assign ddr_addr=vram_addr[27:3];
    assign ddr_rd=vram_rd;assign ddr_we=vram_we;assign ddr_din=vram_din;assign ddr_be=vram_be;
    assign vram_busy=ddr_busy;assign vram_dout=ddr_dout;assign vram_dout_ready=ddr_dout_ready;
    initial begin #2;
     for(integer w=0;w<131072;w++)begin
      chip0.mem[2*w]=16'h7fff;chip0.mem[2*w+1]=16'h7fff;
      chip1.mem[2*w]=16'h7fff;chip1.mem[2*w+1]=16'h7fff;
     end
    end
    integer cycles=0,frames=0,start_field=0,draws=0,mode=0,pattern=0,checked=0,pixels=0;
    integer reads=0,writes=0,returns=0,checks=0,checked_words=0,read_address=0,read_left=0;
    integer line_tag[0:2047],read_tag=-1;
    integer fetch_at[0:511],grant_at[0:511],first_at[0:511],last_at[0:511];
    integer first_pixels=0,min_margin=100000000,max_margin=0;
    reg checking=0;
    initial begin
     if($value$plusargs("DRAW=%d",mode))begin end
     if($value$plusargs("PATTERN=%d",pattern))begin end
     for(integer i=0;i<2048;i++)line_tag[i]=-1;
     for(integer i=0;i<512;i++)begin fetch_at[i]=-1;grant_at[i]=-1;first_at[i]=-1;last_at[i]=-1;end
    end
    function automatic [15:0] expected_pixel(input integer x,input integer y);
     if(pattern && x<16 && y<4)expected_pixel=16'(((x*619+y*1733)^32'h25db)&32767);
     else expected_pixel=16'h7fff;
    endfunction
    function automatic [23:0] rgb15(input [15:0] p);
     return {p[4:0],p[4:2],p[9:5],p[9:7],p[14:10],p[14:12]};
    endfunction
    wire scan_ready=core.igpu_videoout.vram_dout_ready;
    wire [8:0] scan_y=core.igpu_videoout.lineact;
    wire [8:0] beam_y=core.igpu_videoout.videoout_request_clkvid[38:30];
    always @(posedge clk2x)begin
     cycles++;
     if(!reset)begin
      if(vram_rd && !vram_busy)begin
       reads++;read_address=vram_addr>>3;read_left=vram_burstcnt;
      end
      if(vram_we && !vram_busy)begin
       writes++;
       if(checking)for(integer lane=0;lane<4;lane++)
        if(vram_be[2*lane+:2]==2'b11 && vram_din[16*lane+:16]!==expected_pixel(((vram_addr>>1)+lane)&1023,(vram_addr>>11)&511))
          $fatal(1,"Authored white-preserving draw changed VRAM data=%h addr=%h",vram_din,vram_addr);
      end
      if(vram_dout_ready)begin
       returns++;
       if(read_left<=0)$fatal(1,"Unexpected Native read beat");
       if(checking)begin
        for(integer lane=0;lane<4;lane++)
         if(vram_dout[lane*16+:16]!==expected_pixel((read_address*4+lane)&1023,(read_address>>8)&511))
           $fatal(1,"Native address-pattern mismatch addr=%h lane=%0d got=%h",read_address,lane,vram_dout);
        checked_words++;
       end
       read_left--;read_address++;
      end
      if(core.videoout_reqvramenable && core.igpu_videoout.requestvramidle)begin
       grant_at[core.videoout_reqvramypos]=cycles;
       first_at[core.videoout_reqvramypos]=-1;last_at[core.videoout_reqvramypos]=-1;
       if(core.videoout_reqvramypos<2)$display("GPU_GRANT f=%0d y=%0d c=%0d",frames,core.videoout_reqvramypos,cycles);
      end
      if(scan_ready && core.igpu_videoout.store)begin
       for(integer lane=0;lane<4;lane++)line_tag[core.igpu_videoout.filladdr*4+lane]=frames*512+scan_y;
       if(first_at[scan_y]<0)first_at[scan_y]=cycles;
       last_at[scan_y]=cycles;
      end
     end
    end
    reg old_vsync=0,old_fetch=0;integer old_fetch_y=-1,old_beam=-1;
    always @(posedge clkvid)begin
     if(!reset)begin
      if(core.igpu_videoout.igpu_videoout_async.state==4 &&
         core.igpu_videoout.igpu_videoout_async.readstate==1 &&
         !core.igpu_videoout.igpu_videoout_async.nodraw && !video_vblank &&
         beam_y<2 && checking && frames>start_field)begin
       checked++;
       if(read_tag!=frames*512+int'(beam_y))
         $fatal(1,"GPU first-line stale/unwritten RAM frame=%0d row=%0d addr=%0d tag=%0d expected=%0d",
           frames,beam_y,core.igpu_videoout.videoout_readaddr,read_tag,frames*512+int'(beam_y));
      end
      read_tag=line_tag[core.igpu_videoout.videoout_readaddr];
      if(core.igpu_videoout.videoout_request_clkvid[0] &&
        (!old_fetch || int'(core.igpu_videoout.videoout_request_clkvid[19:11])!=old_fetch_y))begin
       fetch_at[core.igpu_videoout.videoout_request_clkvid[19:11]]=cycles;
       if(core.igpu_videoout.videoout_request_clkvid[19:11]<2)
        $display("GPU_FETCH f=%0d y=%0d c=%0d",frames,core.igpu_videoout.videoout_request_clkvid[19:11],cycles);
      end
      old_fetch=core.igpu_videoout.videoout_request_clkvid[0];
      old_fetch_y=core.igpu_videoout.videoout_request_clkvid[19:11];
     end
    end
    always @(negedge clkvid)begin
     if(!reset)begin
      if(video_vsync && !old_vsync)begin frames++;old_beam=-1;$display("GPU_FIELD %0d c=%0d",frames,cycles);end
      old_vsync=video_vsync;
      if(video_ce && !video_hblank && !video_vblank && checking)begin
       pixels++;
       if(frames>start_field && beam_y<2)begin
        checks++;
        if({video_r,video_g,video_b}!==rgb15(expected_pixel((int'(core.igpu_videoout.videoout_request_clkvid[29:20])-1)&1023,beam_y)))
          $fatal(1,"GPU first-line RGB mismatch f=%0d y=%0d x=%0d RGB=%h",frames,beam_y,
            core.igpu_videoout.videoout_request_clkvid[29:20],{video_r,video_g,video_b});
        if(old_beam!=beam_y)begin
         integer margin;
         first_pixels++;margin=cycles-last_at[beam_y];
         if(margin<min_margin)min_margin=margin;if(margin>max_margin)max_margin=margin;
         $display("GPU_PIXEL f=%0d y=%0d c=%0d fetch=%0d grant=%0d first=%0d last=%0d margin=%0d",
           frames,beam_y,cycles,fetch_at[beam_y],grant_at[beam_y],first_at[beam_y],last_at[beam_y],margin);
        end
       end
       old_beam=beam_y;
      end
     end
    end
    task write_gpu(input [3:0] address,input [31:0] value);
     @(negedge clk1x);while(bus_stall)@(negedge clk1x);
     bus_addr=address;bus_datawrite=value;bus_write=1;
     @(negedge clk1x);bus_write=0;
    endtask
    task idle;
     integer n;
     repeat(20)@(negedge clk1x);n=0;
     while(!ss_idle)begin @(negedge clk1x);n++;if(n>2500000)$fatal(1,"GPU draw timeout");end
     repeat(20)@(negedge clk1x);
    endtask
    initial begin
     repeat(100)@(negedge clk1x);reset=0;ss_reset=0;
     repeat(10000)@(negedge clk1x);
     write_gpu(4,32'h00000000); // GP1 reset
     write_gpu(0,32'he1000420); // add blend, draw to display, no dither
     write_gpu(0,32'he3000000);write_gpu(0,32'he407ffff);write_gpu(0,32'he5000000);write_gpu(0,32'he6000000);
     write_gpu(0,32'h02ffffff);write_gpu(0,0);write_gpu(0,32'h01e00280);idle();
     if(pattern)begin
      write_gpu(0,32'ha0000000);write_gpu(0,0);write_gpu(0,32'h00040010);
      for(integer y=0;y<4;y++)for(integer x=0;x<16;x+=2)
        write_gpu(0,{expected_pixel(x+1,y),expected_pixel(x,y)});
      idle();
     end
     write_gpu(4,32'h05000000);write_gpu(4,32'h06c60260);write_gpu(4,32'h07040010);
     write_gpu(4,32'h08000027);write_gpu(4,32'h03000000);
     videoout_on=1;repeat(1000)@(negedge clk1x);
     start_field=frames;checking=1;
     $display("GPU_CONFIG c=%0d field=%0d mode=%0d pattern=%0d",cycles,frames,mode,pattern);
     if(mode==0)wait(frames>=start_field+4);
     else while(frames<start_field+4)begin
       // Same white result, real renderer writes and (mode2) read/modify/write.
       write_gpu(0,mode==2 ? 32'h62000000:32'h60ffffff);
       write_gpu(0,0);write_gpu(0,32'h01e00280);idle();draws++;
     end
     checking=0;
     $display("GPU_RESULT frames=%0d draws=%0d reads=%0d writes=%0d returns=%0d checked_words=%0d pixels=%0d checked_tags=%0d rgb_checks=%0d first_pixels=%0d margin=%0d..%0d chip_errors=%0d",
     frames,draws,reads,writes,returns,checked_words,pixels,checked,checks,first_pixels,min_margin,max_margin,chip0.errors+chip1.errors);
     if(writes<76800 || checked_words<100000 || checked<1800 || checks<1800 || first_pixels<3 || chip0.errors+chip1.errors>0 ||
        (mode>0 && draws==0))$fatal(1,"GPU coverage incomplete");
     $display("PASS FULL_GPU_FIRST_LINE");$finish;
    end
    initial begin #250000000;$fatal(1,"Full GPU first-line timeout");end
    endmodule
    '''
    test=HERE/'gpu/tb_gpu.sv';test.write_text(t)
    return test


def build(directory, manifest):
    HERE = Path(directory).resolve()
    m = manifest
    test = HERE/'gpu/tb_gpu.sv'
    gpu = HERE/'gpu/gpu.v'
    files=[test,gpu,Path(m['by_name']['psx_video_config_cdc.sv']['captured']),
           Path(m['by_name']['psx_vram_psram.sv']['captured']),
           *[Path(m['extra'][n]['captured']) for n in ['dual_clock_fifo.sv','mister_memory_models.sv','psx_cram_model.sv']]]
    with (HERE/'gpu/verilator.log').open('w') as log:
     subprocess.run([find_tool('verilator'),'--binary','--timing','-j','4','-Wno-fatal','--top-module','tb',
                     '--Mdir',str(HERE/'gpu/obj'),'-I'+str(HERE/'frozen/extra'),*map(str,files)],
                    cwd=HERE,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=240)
    binary=HERE/'gpu/obj/Vtb'
    return binary


def checks(directory, manifest):
    HERE = Path(directory).resolve()
    m=manifest
    g=json.loads((HERE/'gpu/sources.json').read_text())
    source=HERE/'gpu/gpu.v';binary=HERE/'gpu/obj/Vtb'
    compiled=[source,HERE/'gpu/tb_gpu.sv',
              *[Path(m['by_name'][n]['captured']) for n in ['psx_video_config_cdc.sv','psx_vram_psram.sv']],
              *[Path(m['extra'][n]['captured']) for n in ['dual_clock_fifo.sv','mister_memory_models.sv','psx_cram_model.sv','psx_vram_psram_windows.svh']]]
    protected={str(p):digest(p) for p in compiled+[ROOT/'scripts/run_native_scanout.py',ROOT/'scripts/prepare_native_scanout_sim.py']}
    def run(case):
     name,args=case
     log=HERE/'gpu'/(name+'.log')
     with log.open('w') as out:
      p=subprocess.run(['stdbuf','-oL',str(binary),*args],cwd=HERE,stdout=out,stderr=subprocess.STDOUT,timeout=240)
     text=log.read_text();ok=p.returncode==0 and 'PASS FULL_GPU_FIRST_LINE' in text
     stats=re.search(r'GPU_RESULT (.*)',text)
     result=dict(name=name,command=[str(binary),*args],exit_code=p.returncode,passed=ok,
                 stats=stats[1] if stats else None,log=dict(path=str(log),sha256=digest(log)),binary_sha256=digest(binary))
     print(name,ok,text[-1300:],flush=True)
     return result
    cases=[('pattern-idle',['+PATTERN=1']),
           ('pattern-blend',['+PATTERN=1','+DRAW=2']),
           ('pattern-blend-phase13p4',['+PATTERN=1','+DRAW=2','+VIDEO_PHASE=13.4']),
           ('opaque-phase6p7',['+DRAW=1','+VIDEO_PHASE=6.7'])]
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
     runs=list(pool.map(run,cases))

    text=source.read_text()
    parent=re.search(r'^module (gpu_videoout(?:_Barch)?)\n',text,re.M)
    assert parent
    end=text.index('endmodule',parent.start())
    part=text[parent.start():end]
    part,n=re.subn(r'(\bilineram\s*\(.*?\.wren_a\()([^()]+)(\))',
                  r"\1(\2 && filladdr[7:0]!=8'd0)\3",part,count=1,flags=re.S)
    assert n==1
    bad=HERE/'gpu/bad-first-word.v';bad.write_text(text[:parent.start()]+part+text[end:])
    with (HERE/'gpu/fault-build.log').open('w') as out:
     subprocess.run([find_tool('verilator'),'--binary','--timing','-j','4','-Wno-fatal','--top-module','tb',
                     '--Mdir',str(HERE/'gpu/fault-obj'),'-I'+str(HERE/'frozen/extra'),str(bad),
                     *[str(p) for p in compiled if p!=source and p.suffix!='.svh']],
                     cwd=HERE,stdout=out,stderr=subprocess.STDOUT,check=True,timeout=240)
    fault_binary=HERE/'gpu/fault-obj/Vtb';fault_log=HERE/'gpu/lost-first-word.log'
    with fault_log.open('w') as out:
     p=subprocess.run(['stdbuf','-oL',str(fault_binary),'+PATTERN=1'],cwd=HERE,stdout=out,stderr=subprocess.STDOUT,timeout=240)
    fault_ok=p.returncode!=0 and 'GPU first-line RGB mismatch' in fault_log.read_text()
    print('lost-first-word',fault_ok,fault_log.read_text()[-650:],flush=True)
    identity=all(digest(Path(p))==sha for p,sha in protected.items())
    identity &= all(digest(Path(x['captured']))==x['sha256'] for x in m['inputs'])
    identity &= all(digest(Path(x['adapted']))==x['sha256'] for x in g['selected'])
    report=dict(passed=identity and all(r['passed'] for r in runs) and fault_ok,source_identity_verified=identity,
                compiled_inputs=protected,compiled_binary=dict(path=str(binary),sha256=digest(binary)),runs=runs,
                fault=dict(rejected=fault_ok,exit_code=p.returncode,log=dict(path=str(fault_log),sha256=digest(fault_log)),
                           modified_GPU_sha256=digest(bad),binary_sha256=digest(fault_binary)),
                exact_GPU_renderer_and_scanout_bodies=True,DDR_memcard_arbiter_connected=False,
                authored_commands_not_actual_BIOS_trace=True,compiled_RBF_leaf_identity_proven=False,
                electrical_four_state_qualification=False,hardware_verified=False,
                fixedVBlank=1,ntsc_fixed_vblank_GPU_body=True,
                script=dict(path=str(Path(__file__)),sha256=digest(Path(__file__))),
                clk2x_period_ns=14.76255)
    (HERE/'gpu/report.json').write_text(json.dumps(report,indent=2)+'\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True, type=Path,
                        help='Generated Native project with the selected GPU/config CDC sources')
    parser.add_argument('--output', type=Path, help='Fresh ignored build directory')
    args = parser.parse_args()
    if args.output:
        directory = args.output.resolve()
    else:
        parent = ROOT/'build/sim/native-scanout'
        parent.mkdir(parents=True, exist_ok=True)
        directory = Path(tempfile.mkdtemp(prefix='checks-', dir=parent))/'run'
    if directory.exists() or not directory.is_relative_to(ROOT/'build'):
        parser.error('Output must be a fresh directory below build/')
    print(directory, flush=True)
    manifest = prepare_snapshot(directory, args.project)
    prepare_gpu(directory)
    make_bench(directory, manifest)
    build(directory, manifest)
    report = checks(directory, manifest)
    print('PASS NATIVE SCANOUT' if report['passed'] else 'FAIL NATIVE SCANOUT', flush=True)
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
