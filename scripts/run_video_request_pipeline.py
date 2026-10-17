#!/usr/bin/env python3
"""Pinned scanout/fetcher A/B: coherent requests, frames and memory latency.

No BIOS/disc. Both engines use identical forward configuration and real
upstream line RAM/fetch logic. VRAM returns a deterministic address pattern
with a configurable initial delay and beat gaps. Request sequence and every
active RGB/control sample are compared; request delivery timing is measured.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import tempfile
from prepare_resource_targets import digest
from prepare_video_request_sim import prepare
from tool_paths import ROOT,find_tool


def bench(source, pan_updates=False):
    header=source[source.index('module original__gpu_videoout\n'):].split(');',1)[0]
    ports=re.findall(r'\b(input|output)\s+(\[[^\]]+\]\s+)?(\\[^\s,]+|[A-Za-z_]\w*)',header)
    clean=lambda n:re.sub(r'\W','_',n.lstrip('\\'))
    inp=lambda n:'i_'+clean('videoout_settings['+n+']')
    out=lambda label,n:label+'_'+clean('videoout_out['+n+']')
    special={'clk1x','clk2x','clkvid','vram_DOUT','vram_DOUT_READY','requestVRAMIdle','requestVRAMDone'}
    text='`timescale 1ns/1ps\nmodule tb;\n'
    for direction,width,name in ports:
        short=name.lstrip('\\')
        if direction=='input' and short not in special:
            init=1 if short in ('ce','reset','videoout_on','Gun1offscreen','Gun2offscreen') else 0
            text+=f'reg {width}i_{clean(name)}={init};\n'
        elif direction=='output':
            for label in ('old','new'):text+=f'wire {width}{label}_{clean(name)};\n'
    text+='''reg clk1x=0,clk2x=0,clkvid=0;
    always #14.76255 clk1x=~clk1x;
    always #7.381275 clk2x=~clk2x;
    always #9.31219 clkvid=~clkvid;
    integer cycles=0,latency=8;always @(posedge clk2x)cycles++;
    integer seen[0:1],compared=0,req_total=0,minlag=1000000,maxlag=-1000000;
    reg [30:0] requests[0:1][0:63];integer times[0:1][0:63];
    function automatic [15:0] pixel(input integer x,input integer y);
        pixel=16'((x*73)^(y*971)^16'h639c);
    endfunction
'''
    for index,(label,module) in enumerate([('old','original__gpu_videoout'),('new','candidate__gpu_videoout')]):
        text+=f'''reg [63:0] {label}_data=0;
        reg {label}_ready=0,{label}_idle=1,{label}_done=0,{label}_busy=0;
        integer {label}_left=0,{label}_x=0,{label}_y=0,{label}_beat=0,{label}_wait=0;
        always @(posedge clk2x) begin
            {label}_ready<=0;{label}_done<=0;
            if(i_reset) begin {label}_idle<=1;{label}_busy<=0;end
            else if(!{label}_busy && {label}_requestVRAMEnable && {label}_idle) begin
                requests[{index}][seen[{index}]&63]={{ {label}_requestVRAMMirror,{label}_requestVRAMXPos,{label}_requestVRAMYPos,{label}_requestVRAMSize }};
                times[{index}][seen[{index}]&63]=cycles;seen[{index}]++;
                {label}_busy<=1;{label}_idle<=0;{label}_wait<=latency;
                {label}_left<=({label}_requestVRAMSize+3)/4;
                {label}_x<=({label}_requestVRAMXPos&1020);{label}_y<={label}_requestVRAMYPos;{label}_beat<=0;
            end else if({label}_busy) begin
                if({label}_wait>0){label}_wait<={label}_wait-1;
                else begin
                    for(integer lane=0;lane<4;lane++) {label}_data[lane*16+:16]<=pixel(({label}_x+{label}_beat*4+lane)&1023,{label}_y);
                    {label}_ready<=1;{label}_left<={label}_left-1;{label}_beat<={label}_beat+1;
                    {label}_wait<=({label}_beat%4)==1 ? 1:0;
                    if({label}_left==1)begin {label}_busy<=0;{label}_idle<=1;{label}_done<=1;end
                end
            end
        end
'''
        associations=[]
        values={'clk1x':'clk1x','clk2x':'clk2x','clkvid':'clkvid','vram_DOUT':label+'_data',
                'vram_DOUT_READY':label+'_ready','requestVRAMIdle':label+'_idle','requestVRAMDone':label+'_done'}
        for direction,width,name in ports:
            short=name.lstrip('\\')
            value=label+'_'+clean(name) if direction=='output' else values.get(short,'i_'+clean(name))
            associations.append('.'+name+(' ' if name.startswith('\\') else '')+'('+value+')')
        text+=module+' '+label+'_dut ('+',\n'.join(associations)+');\n'
    text+='''always @(negedge clk2x) begin
        if(!i_reset) begin
            while(compared<seen[0] && compared<seen[1]) begin
                if(requests[0][compared&63]!==requests[1][compared&63])
                    $fatal(1,"VRAM request tuple mismatch n=%0d old=%h new=%h",compared,requests[0][compared&63],requests[1][compared&63]);
                if(times[1][compared&63]-times[0][compared&63]<minlag)minlag=times[1][compared&63]-times[0][compared&63];
                if(times[1][compared&63]-times[0][compared&63]>maxlag)maxlag=times[1][compared&63]-times[0][compared&63];
                compared++;req_total++;
            end
            if(seen[0]-compared>60||seen[1]-compared>60)$fatal(1,"Request stream failed to advance");
        end
    end
    reg checking=0,previous_vsync=0;integer frames=0,pixels=0,mode_pixels=0,checks=0,nonzero=0,changes=0;
    reg [23:0] previous_rgb=0;
    always @(negedge clkvid) begin
        if(checking) begin
'''
    for direction,width,name in ports:
        short=name.lstrip('\\')
        if direction=='output' and not short.startswith('requestVRAM') and short not in ('videoout_out[r]','videoout_out[g]','videoout_out[b]'):
            text+=f'if(old_{clean(name)}!==new_{clean(name)}) $fatal(1,"Video metadata changed: {clean(name)}");\n'
    active=out('old','ce')+' && !'+out('old','hblank')+' && !'+out('old','vblank')
    oldrgb='{'+','.join(out('old',n) for n in ('r','g','b'))+'}'
    newrgb='{'+','.join(out('new',n) for n in ('r','g','b'))+'}'
    text+=f'''if({active}) begin
        if({oldrgb}!=={newrgb})$fatal(1,"Active RGB changed old=%h new=%h",{oldrgb},{newrgb});
        if({oldrgb}!=0)nonzero++;
        if({oldrgb}!=previous_rgb)changes++;
        previous_rgb={oldrgb};
        pixels++;mode_pixels++;
    end
    checks++;
    if({out('old','vsync')}&&!previous_vsync)frames++;
    previous_vsync={out('old','vsync')};
    end end
    task run_mode(input integer res,input bit pal,input bit interlace,input bit color24,input bit full24=0,input bit dith=0);
    begin
        checking=0;@(negedge clk1x);i_reset=1;i_videoout_on=1;i_ce=1;i_softReset=0;i_savestate_pause=0;i_system_paused=0;
        seen[0]=0;seen[1]=0;compared=0;frames=0;mode_pixels=0;previous_vsync=0;nonzero=0;changes=0;previous_rgb=0;
'''
    for name,value in [('GPUSTAT_VerRes','interlace'),('GPUSTAT_VertInterlace','interlace'),('GPUSTAT_PalVideoMode','pal'),
        ('GPUSTAT_HorRes2','res==4'),('GPUSTAT_HorRes1',"2'(res)"),('GPUSTAT_ColorDepth24','color24'),
        ('GPUSTAT_DisplayDisable','0'),('vramRange',"19'h2043"),('hDisplayRange',"{12'd3168,12'd608}"),
        ('vDisplayRange',"pal ? {10'd288,10'd48}:{10'd256,10'd16}"),('dither24','dith'),('render24','full24')]:
        text+=inp(name)+'='+value+';\n'
    text+='''repeat(48)@(negedge clk1x);i_reset=0;
        repeat(64)@(negedge clk1x);checking=1;
        wait(frames==3);checking=0;
        @(negedge clk1x);i_videoout_on=0;
        repeat(2000)@(negedge clk2x);
        if(seen[0]!=seen[1]||compared!=seen[0]||mode_pixels<10000||nonzero<10000||changes<1000)$fatal(1,"Scanout coverage incomplete");
        $display("MODE res=%0d pal=%0d interlace=%0d color24=%0d req=%0d pixels=%0d",res,pal,interlace,color24,compared,mode_pixels);
    end endtask
    initial begin
        seen[0]=0;seen[1]=0;
        if($value$plusargs("LATENCY=%d",latency))begin end
        run_mode(0,0,0,0);run_mode(1,0,0,1);
        if(!$test$plusargs("QUICK"))begin
            run_mode(2,0,0,0);run_mode(3,0,0,0);run_mode(4,0,0,0);
            run_mode(1,0,1,0);run_mode(1,1,1,0);run_mode(1,0,0,1,1,1);
        end
        $display("PASS REQUEST_PIPELINE pixels=%0d metadata=%0d requests=%0d extra_2x_clocks=%0d..%0d",pixels,checks,req_total,minlag,maxlag);$finish;
    end
    initial begin #600000000;$fatal(1,"Pipeline timeout");end
    endmodule
'''
    if pan_updates:
        # Legal GP1 display-start writes independent of either engine.  The
        # fetcher uses this live CPU-side offset after the request crosses;
        # static modes alone cannot check that interaction with added latency.
        marker = '    reg checking=0,previous_vsync=0;'
        if text.count(marker) != 1:
            raise ValueError('Missing display-pan stimulus anchor')
        text = text.replace(marker, '''    integer pan_ticks=0,pan_writes=0;
    always @(negedge clk1x) if(checking) begin
        pan_ticks=pan_ticks+1;
        if((pan_ticks%8)==0)begin
            i_videoout_settings_vramRange_=i_videoout_settings_vramRange_^19'h10;
            pan_writes=pan_writes+1;
        end
    end
''' + marker)
        marker = '$display("PASS REQUEST_PIPELINE'
        if text.count(marker) != 1:
            raise ValueError('Missing display-pan coverage anchor')
        text = text.replace(marker, '''if(pan_writes<100)$fatal(1,"No dynamic GP1 coverage");
        $display("GP1_PAN writes=%0d",pan_writes);
        ''' + marker)
    return text


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--quick',action='store_true');parser.add_argument('--converted',type=Path)
    parser.add_argument('--pan-updates',action='store_true',help='diagnose live GP1 display-start writes; the current opt-in FIFO fails this comparison')
    args=parser.parse_args();parent=ROOT/'build/sim/video-request-pipeline';parent.mkdir(parents=True,exist_ok=True)
    work=Path(tempfile.mkdtemp(prefix='checks-',dir=parent));print(work,flush=True)
    prepared=json.loads((args.converted/'sources.json').read_text()) if args.converted else prepare(work/'converted')
    old=Path(prepared['converted']['original']);new=Path(prepared['converted']['candidate'])
    test=work/'tb.sv';test.write_text(bench(old.read_text(),pan_updates=args.pan_updates))
    sources=[old,new,test,*[Path(prepared['inputs'][str(ROOT/n)]['path']) for n in [
        'rtl/pocket/psx_video_request_cdc.sv','rtl/pocket/psx_video_config_cdc.sv',
        'references/Laxer3a_PSX/hdlGPU/rtl/dual_clock_fifo.sv','sim/reference/mister_memory_models.sv']]]
    def build(name,selected):
        binary_dir=work/name
        with (work/(name+'-build.log')).open('w') as log:
            subprocess.run([find_tool('verilator'),'--binary','--timing','--top-module','tb','-Wno-fatal','-j','4',
                '--Mdir',str(binary_dir),*[str(p) for p in selected]],stdout=log,stderr=subprocess.STDOUT,check=True,timeout=240)
        return binary_dir/'Vtb'
    binary=build('obj',sources)
    runs=[]
    for latency in (8,48):
        command=[str(binary),f'+LATENCY={latency}']+(['+QUICK'] if args.quick else [])
        with (work/f'latency{latency}.log').open('w') as log:
            p=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,timeout=240)
        output=(work/f'latency{latency}.log').read_text();passed=p.returncode==0 and 'PASS REQUEST_PIPELINE' in output
        runs.append(dict(latency=latency,passed=passed,exit_code=p.returncode,output=output));print(latency,passed,output[-700:],flush=True)
        if not passed:break
    fault_rejected=None
    fault_output='Not run: unmodified pipeline comparison failed.'
    if len(runs)==2 and all(r['passed'] for r in runs):
        # Corrupt the actual candidate's active-red output, rather than a checker
        # fixture: the pixel comparison must reject it even when requests agree.
        source=new.read_text();start=source.index('module candidate__gpu_videoout\n');end=source.index('endmodule',start)
        part=source[start:end]
        part,n=re.subn(r'(assign \\videoout_out\[r\]\s*=\s*)([^;]+);',r"\1(\2 ^ 8'h01);",part)
        if n!=1:raise ValueError('Missing actual scanout RGB corruption anchor')
        bad=work/'bad-rgb.v';bad.write_text(source[:start]+part+source[end:])
        selected=[bad if p==new else p for p in sources];fault_binary=build('fault',selected)
        with (work/'fault-rgb.log').open('w') as log:
            p=subprocess.run([str(fault_binary),'+QUICK'],stdout=log,stderr=subprocess.STDOUT,timeout=60)
        fault_output=(work/'fault-rgb.log').read_text();fault_rejected=p.returncode!=0 and 'Active RGB changed' in fault_output
    identity=all(digest(Path(p))==h for p,h in prepared['protected_sha256'].items())
    report=dict(passed=identity and len(runs)==2 and all(r['passed'] for r in runs) and fault_rejected,runs=runs,
                actual_RGB_fault_rejected=fault_rejected,RGB_fault_output=fault_output,
                source_identity_verified=identity,prepared=prepared,bench_sha256=digest(test),quick=args.quick,
                pan_updates=args.pan_updates,
                hardware_verified=False,fit_verified=False,timing_verified=False)
    (work/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    if not report['passed']:raise SystemExit(1)


if __name__=='__main__':main()
